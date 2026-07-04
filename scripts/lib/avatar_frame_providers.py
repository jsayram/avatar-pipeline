"""Avatar-frame providers for FR-4.

FR-4 is the still image review step: first video frame -> avatar-styled first
frame. It is intentionally separate from FR-5 animation so the operator can
approve the still image before any video generation spend.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import time
from abc import ABC, abstractmethod
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

from .comfyui import (
    ComfyUIError,
    ComfyUIUnreachable,
    WorkflowTemplateError,
    generate_avatar_frame,
)


DEFAULT_SEEDREAM_PROMPT = (
    "Replace the entire person from image 1 with the person from image 2, "
    "keep the same facial expression from image 1 and pose from image 1, "
    "keep the same outfit from image 1"
)

DONE_STATUSES = {"completed", "succeeded", "success"}
FAILED_STATUSES = {"failed", "error", "canceled", "cancelled"}


class AvatarFrameError(Exception):
    """The still-image avatar frame generation failed."""


class AvatarFrameNotConfigured(AvatarFrameError):
    """The selected FR-4 provider is missing required setup."""


class AvatarFrameProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def generate(self, frame_path: Path, output_path: Path, config, seed: int | None = None) -> Path:
        """Generate one still avatar frame for visual approval."""


class LocalComfyUIAvatarFrameProvider(AvatarFrameProvider):
    """Existing offline FR-4 path: ComfyUI + LoRA + inpaint workflow."""

    name = "local_comfyui"

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("avatar_frame.local")

    def generate(self, frame_path: Path, output_path: Path, config, seed: int | None = None) -> Path:
        if not config.paths.lora_path.exists():
            raise AvatarFrameNotConfigured(
                f"character LoRA not found: {config.paths.lora_path} — train it and "
                "drop the .safetensors there (see SETUP.md), or fix paths.lora_path."
            )
        try:
            return generate_avatar_frame(
                config,
                frame_path,
                output_path,
                seed=seed if seed is not None else int.from_bytes(os.urandom(4), "big"),
                logger=self.logger,
            )
        except (WorkflowTemplateError, ComfyUIUnreachable) as exc:
            raise AvatarFrameNotConfigured(str(exc)) from exc
        except ComfyUIError as exc:
            raise AvatarFrameError(str(exc)) from exc


class MockAvatarFrameProvider(AvatarFrameProvider):
    """Free, instant stand-in for FR-4 — for verifying the two-gate
    Telegram mechanism (archiving, dispatch, sends) without spending real
    Seedream money. Just copies the source frame with a watermark; it is
    NOT an actual identity swap, so the real identity gate downstream will
    almost certainly fail against it. NEVER leave this configured for real
    scheduled/live runs — set avatar_frame.provider back to
    wavespeed_seedream (or local_comfyui) once done testing."""

    name = "mock"

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("avatar_frame.mock")

    def generate(self, frame_path: Path, output_path: Path, config, seed: int | None = None) -> Path:
        from PIL import ImageDraw

        self.logger.warning(
            "MOCK avatar-frame provider active — no real Seedream call made; "
            "this is a placeholder, not an actual identity swap"
        )
        image = Image.open(frame_path).convert("RGB")
        draw = ImageDraw.Draw(image)
        label = "MOCK — not real Seedream output"
        draw.rectangle([0, 0, image.width, 40], fill=(220, 30, 30))
        draw.text((10, 10), label, fill=(255, 255, 255))
        image.save(output_path)
        return output_path


class WaveSpeedSeedreamAvatarFrameProvider(AvatarFrameProvider):
    """Opt-in FR-4 still image edit through WaveSpeed Seedream 4.5.

    This provider never runs animation. It uploads the source first frame and a
    configured identity references if present (otherwise a face-only avatar
    reference if assets/avatar_reference_face.png exists), submits
    bytedance/seedream-v4.5/edit, polls once, and downloads the still.
    """

    name = "wavespeed_seedream"

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("avatar_frame.wavespeed_seedream")

    def generate(self, frame_path: Path, output_path: Path, config, seed: int | None = None) -> Path:
        ws = config.wavespeed
        af = config.avatar_frame
        if not ws.enabled:
            raise AvatarFrameNotConfigured(
                "avatar_frame.provider is 'wavespeed_seedream' but wavespeed.enabled is false. "
                "Set wavespeed.enabled: true to allow the paid still-image call, or set "
                "avatar_frame.provider back to 'local_comfyui'."
            )
        if not af.wavespeed_model:
            raise AvatarFrameNotConfigured(
                "avatar_frame.wavespeed_model is empty. Set it to "
                "'bytedance/seedream-v4.5/edit'."
            )
        api_key = os.environ.get(ws.api_key_env)
        if not api_key:
            raise AvatarFrameNotConfigured(
                f"environment variable {ws.api_key_env} is not set. Create a WaveSpeed "
                f"API key and export {ws.api_key_env}=<key> in the environment that "
                "runs worker.py. Never put the key in config.yaml."
            )

        reference_paths = self.identity_reference_paths(config)
        if not Path(frame_path).exists():
            raise AvatarFrameNotConfigured(f"source frame not found: {frame_path}")
        missing_refs = [path for path in reference_paths if not path.exists()]
        if missing_refs:
            missing = ", ".join(str(path) for path in missing_refs)
            raise AvatarFrameNotConfigured(
                f"avatar identity reference image(s) not found: {missing}"
            )

        headers = {"Authorization": f"Bearer {api_key}"}
        self.logger.info(
            "uploading source frame + %d avatar identity reference(s) to WaveSpeed Seedream",
            len(reference_paths),
        )
        source_url = self._upload(Path(frame_path), ws.api_base, headers)
        reference_urls = [self._upload(path, ws.api_base, headers) for path in reference_paths]

        payload = self.build_payload(source_url, reference_urls, config)
        submit_url = f"{ws.api_base}/api/v3/{af.wavespeed_model}"
        self.logger.info("submitting still image job to WaveSpeed model %s", af.wavespeed_model)
        request_id = self._submit(submit_url, payload, headers)
        result_url = f"{ws.api_base}/api/v3/predictions/{request_id}/result"

        deadline = time.monotonic() + ws.timeout_seconds
        while time.monotonic() < deadline:
            try:
                poll = requests.get(result_url, headers=headers, timeout=60)
            except requests.RequestException as exc:
                raise AvatarFrameError(f"WaveSpeed Seedream poll failed: {exc}") from exc
            if poll.status_code >= 400:
                raise AvatarFrameError(
                    f"WaveSpeed Seedream poll returned {poll.status_code}: {poll.text[:500]}"
                )
            body = poll.json()
            status, data = self._extract_status(body)
            if status in DONE_STATUSES:
                outputs = data.get("outputs") or []
                if not outputs:
                    raise AvatarFrameError("WaveSpeed Seedream completed with no outputs")
                return self._download(outputs[0], Path(output_path), headers)
            if status in FAILED_STATUSES:
                raise AvatarFrameError(
                    f"WaveSpeed Seedream failed: {data.get('error', poll.text[:500])}"
                )
            time.sleep(ws.poll_interval_seconds)
        raise AvatarFrameError(
            f"WaveSpeed Seedream job {request_id} did not complete within {ws.timeout_seconds}s"
        )

    @staticmethod
    def identity_reference_paths(config) -> list[Path]:
        if config.avatar_frame.identity_references:
            return list(config.avatar_frame.identity_references)
        avatar_reference = config.paths.avatar_reference
        face_reference = avatar_reference.with_name("avatar_reference_face.png")
        return [face_reference if face_reference.exists() else avatar_reference]

    @staticmethod
    def build_payload(source_url: str, reference_urls: list[str] | str, config) -> dict:
        af = config.avatar_frame
        if isinstance(reference_urls, str):
            reference_urls = [reference_urls]
        prompt = af.prompt.strip() or DEFAULT_SEEDREAM_PROMPT
        images = [source_url, *reference_urls]
        if prompt == DEFAULT_SEEDREAM_PROMPT and reference_urls:
            images = [source_url, reference_urls[0]]
        payload = {
            "prompt": prompt,
            "images": images,
            "enable_sync_mode": False,
            "enable_base64_output": False,
        }
        if af.size.strip():
            payload["size"] = af.size.strip()
        return payload

    def _submit(self, submit_url: str, payload: dict, headers: dict) -> str:
        for candidate in self._payload_candidates(payload):
            try:
                resp = requests.post(submit_url, json=candidate, headers=headers, timeout=300)
            except requests.RequestException as exc:
                raise AvatarFrameError(f"WaveSpeed Seedream submit failed: {exc}") from exc
            if resp.status_code < 400:
                request_id = (resp.json().get("data") or {}).get("id")
                if not request_id:
                    raise AvatarFrameError(
                        f"WaveSpeed Seedream submit gave no request id: {resp.text[:500]}"
                    )
                return request_id
            if "size" not in candidate:
                raise AvatarFrameError(
                    f"WaveSpeed Seedream submit returned {resp.status_code}: {resp.text[:500]}"
                )
            self.logger.warning(
                "WaveSpeed Seedream rejected size %r (%s); retrying with alternate size format",
                candidate.get("size"),
                resp.status_code,
            )
        raise AvatarFrameError("WaveSpeed Seedream submit failed before receiving a request id")

    @staticmethod
    def _payload_candidates(payload: dict) -> list[dict]:
        size = payload.get("size")
        if not size:
            return [payload]
        candidates = [payload]
        if "*" in size:
            candidates.append({**payload, "size": size.replace("*", "x")})
        elif "x" in size.lower():
            candidates.append({**payload, "size": size.lower().replace("x", "*")})
        candidates.append({k: v for k, v in payload.items() if k != "size"})
        return candidates

    @staticmethod
    def _extract_status(body: dict) -> tuple[str | None, dict]:
        data = body.get("data") or {}
        status = body.get("status") or data.get("status")
        return status, data

    def _upload(self, path: Path, api_base: str, headers: dict) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            with path.open("rb") as fh:
                resp = requests.post(
                    f"{api_base}/api/v3/media/upload/binary",
                    headers=headers,
                    files={"file": (path.name, fh, mime)},
                    timeout=300,
                )
        except requests.RequestException as exc:
            raise AvatarFrameError(f"WaveSpeed upload of {path.name} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise AvatarFrameError(
                f"WaveSpeed upload of {path.name} returned {resp.status_code}: {resp.text[:500]}"
            )
        url = (resp.json().get("data") or {}).get("download_url")
        if not url:
            raise AvatarFrameError(
                f"WaveSpeed upload of {path.name} gave no download_url: {resp.text[:500]}"
            )
        return url

    def _download(self, url: str, output_path: Path, headers: dict) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = output_path.with_name(f"{output_path.name}.download")
        try:
            with requests.get(url, headers=headers, stream=True, timeout=600) as resp:
                if resp.status_code >= 400:
                    raise AvatarFrameError(
                        f"WaveSpeed Seedream output download returned {resp.status_code}"
                    )
                with tmp_path.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
        except requests.RequestException as exc:
            raise AvatarFrameError(f"WaveSpeed Seedream output download failed: {exc}") from exc
        self._normalize_download(tmp_path, output_path)
        return output_path

    @staticmethod
    def _normalize_download(tmp_path: Path, output_path: Path) -> None:
        """WaveSpeed may return JPEG bytes even when our pipeline path is .png.

        Normalize to a true PNG for FR-5 upload/mime consistency. If Pillow
        cannot read the file, keep the raw bytes rather than discarding output.
        """
        if output_path.suffix.lower() != ".png":
            tmp_path.replace(output_path)
            return
        try:
            with Image.open(tmp_path) as image:
                image.save(output_path, format="PNG")
        except (OSError, UnidentifiedImageError):
            tmp_path.replace(output_path)
        else:
            tmp_path.unlink(missing_ok=True)


def get_avatar_frame_provider(config, logger: logging.Logger | None = None) -> AvatarFrameProvider:
    provider = config.avatar_frame.provider
    if provider == "local_comfyui":
        return LocalComfyUIAvatarFrameProvider(logger=logger)
    if provider == "wavespeed_seedream":
        return WaveSpeedSeedreamAvatarFrameProvider(logger=logger)
    if provider == "mock":
        return MockAvatarFrameProvider(logger=logger)
    raise AvatarFrameNotConfigured(
        f"unknown avatar_frame.provider {provider!r}; valid values: "
        "local_comfyui, wavespeed_seedream, mock"
    )
