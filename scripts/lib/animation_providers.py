"""Animation providers for FR-5 (and ONLY FR-5).

The animation step — avatar frame + reference video -> animated avatar video —
is the single slow stage, so it is the only stage with a pluggable provider:

    AnimationProvider.animate(avatar_frame_path, reference_video_path,
                              output_path, config)

  * LocalComfyUIAnimationProvider — the DEFAULT. Wan 2.2 Animate via the local
    ComfyUI API. Fully offline.
  * WaveSpeedAnimationProvider — OPT-IN cloud offload. It is never constructed
    unless animation.provider == "wavespeed" AND wavespeed.enabled: true, and
    it refuses to run without an explicit model id and an API key in the
    environment. It handles nothing but this one step: input reading,
    downloading, avatar generation, identity gating, metadata stripping,
    publishing and state updates always stay local.

The worker owns retry-on-gate-failure and the fallback_to_local_on_cloud_error
behavior; providers just animate once or raise AnimationError.
"""
from __future__ import annotations

import logging
import mimetypes
import os
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path

import requests

from .comfyui import (
    WAN_FPS,
    WAN_MAX_BLOCK_SECONDS,
    ComfyUIClient,
    first_output_file,
    inject,
    load_workflow,
)
from .media import probe_dims, round_down_to_16


class AnimationError(Exception):
    """The animation step failed (render error, timeout, bad response)."""


class AnimationNotConfigured(AnimationError):
    """The selected provider is missing required configuration — fails with
    setup instructions rather than attempting anything."""


class AnimationProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def animate(
        self,
        avatar_frame_path: Path,
        reference_video_path: Path,
        output_path: Path,
        config,
        seed: int | None = None,
    ) -> Path:
        """Render the avatar frame performing the reference motion to output_path."""


class LocalComfyUIAnimationProvider(AnimationProvider):
    """Wan 2.2 Animate on local ComfyUI — the offline default (FR-5)."""

    name = "local_comfyui"

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("animation.local")

    def animate(self, avatar_frame_path, reference_video_path, output_path,
                config, seed=None):
        workflow = load_workflow(config.paths.workflows_dir / "wan_animate.api.json")
        client = ComfyUIClient(
            config.endpoints.comfyui_url,
            poll_interval=config.comfyui.poll_interval_seconds,
            logger=self.logger,
        )
        width, height = probe_dims(reference_video_path)
        avatar_name = client.upload(avatar_frame_path)
        video_name = client.upload(reference_video_path)
        # v1 renders a single Wan block regardless of config.video.max_clip_seconds
        # (see WAN_MAX_BLOCK_SECONDS docstring in lib/comfyui.py) — longer clips
        # are truncated to this block length, not chained across multiple blocks.
        block_seconds = min(config.video.max_clip_seconds, WAN_MAX_BLOCK_SECONDS)
        length = block_seconds * WAN_FPS + 1
        workflow = inject(workflow, {
            "__AVATAR_IMAGE__": avatar_name,
            "__REF_VIDEO__": video_name,
            "__WAN_MODEL__": config.video.wan_model,
            "__SEED__": seed if seed is not None else int.from_bytes(os.urandom(4), "big"),
            "__WIDTH__": round_down_to_16(width),
            "__HEIGHT__": round_down_to_16(height),
            "__MAX_SECONDS__": config.video.max_clip_seconds,
            "__LENGTH__": length,
        })
        prompt_id = client.queue(workflow)
        self.logger.info(
            "Wan 2.2 Animate queued on local ComfyUI (prompt_id=%s, %d frames "
            "@ %dfps) — this is the slow step: expect minutes per ~5s of clip "
            "on M1 Max", prompt_id, length, WAN_FPS,
        )
        outputs = client.wait(prompt_id, config.comfyui.video_timeout_seconds)
        file_info = first_output_file(outputs, kinds=("gifs", "videos", "images"))
        return client.download_output(file_info, output_path)


class MockAnimationProvider(AnimationProvider):
    """Free, instant stand-in for FR-5 — for verifying the two-gate
    Telegram mechanism without spending real WaveSpeed money. Just copies
    the reference video as-is; it is NOT an actual animation, so the real
    identity gate downstream will almost certainly fail against it (the
    avatar's face was never composited in). NEVER leave this configured for
    real scheduled/live runs — set animation.provider back to wavespeed (or
    local_comfyui) once done testing."""

    name = "mock"

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("animation.mock")

    def animate(self, avatar_frame_path, reference_video_path, output_path,
                config, seed=None):
        self.logger.warning(
            "MOCK animation provider active — no real WaveSpeed call made; "
            "this just copies the reference video, not an actual animation"
        )
        shutil.copy2(reference_video_path, output_path)
        return output_path


class WaveSpeedAnimationProvider(AnimationProvider):
    """Opt-in cloud offload of FR-5 to WaveSpeed AI — Kling Motion Control.

    Setup (all required before this provider will make any call):
      1. config.yaml: animation.provider: "wavespeed" AND wavespeed.enabled: true
      2. config.yaml: wavespeed.model, e.g. "kwaivgi/kling-v3.0-pro/motion-control"
      3. environment: export WAVESPEED_API_KEY=...   (name is configurable via
         wavespeed.api_key_env; the key is never read from config or logged).

    API (verified against WaveSpeed docs 2026-07 for kwaivgi/kling-v3.0-pro/
    motion-control — https://wavespeed.ai/models/kwaivgi/kling-v3.0-pro/motion-control):
      POST {api_base}/api/v3/media/upload/binary  (multipart "file")
                                          -> {"data": {"download_url": ...}}
      POST {api_base}/api/v3/{model}
           {image, video, character_orientation, prompt, negative_prompt,
            keep_original_sound}          -> {"data": {"id": ...}}
      GET  {api_base}/api/v3/predictions/{id}/result
                                          -> {"status": "...", "data": {"outputs": [url]}}
                                             (or status nested under "data" —
                                             handled defensively, see _extract_status)

    `character_orientation: "image"` caps the reference video at 10s;
    `"video"` allows up to 30s (see wavespeed.character_orientation in config).
    This model doesn't take a seed param — Kling doesn't expose one.
    """

    name = "wavespeed"

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger("animation.wavespeed")

    def animate(self, avatar_frame_path, reference_video_path, output_path,
                config, seed=None):
        ws = config.wavespeed
        # Defense in depth: the selector already enforces this, but this
        # provider must never fire unless explicitly enabled.
        if not ws.enabled:
            raise AnimationNotConfigured(
                "WaveSpeed provider invoked while wavespeed.enabled is false — "
                "set wavespeed.enabled: true in config.yaml to opt in."
            )
        if not ws.model:
            raise AnimationNotConfigured(
                "wavespeed.model is empty. Set it to the exact model id, e.g. "
                "'kwaivgi/kling-v3.0-pro/motion-control' — see "
                "https://wavespeed.ai/models/kwaivgi/kling-v3.0-pro/motion-control"
            )
        api_key = os.environ.get(ws.api_key_env)
        if not api_key:
            raise AnimationNotConfigured(
                f"environment variable {ws.api_key_env} is not set. Create an API "
                f"key at https://wavespeed.ai and `export {ws.api_key_env}=<key>` "
                "in the environment that runs worker.py (for n8n, set it in the "
                "environment that launches n8n). Never put the key in config.yaml."
            )
        headers = {"Authorization": f"Bearer {api_key}"}

        self.logger.info("uploading avatar frame + reference video to WaveSpeed")
        image_url = self._upload(avatar_frame_path, ws.api_base, headers)
        video_url = self._upload(reference_video_path, ws.api_base, headers)

        payload = self.build_payload(image_url, video_url, config)
        submit_url = f"{ws.api_base}/api/v3/{ws.model}"
        self.logger.info("submitting animation job to WaveSpeed model %s", ws.model)
        try:
            resp = requests.post(submit_url, json=payload, headers=headers, timeout=300)
        except requests.RequestException as exc:
            raise AnimationError(f"WaveSpeed submit failed: {exc}") from exc
        if resp.status_code >= 400:
            raise AnimationError(
                f"WaveSpeed submit returned {resp.status_code}: {resp.text[:500]}"
            )
        request_id = (resp.json().get("data") or {}).get("id")
        if not request_id:
            raise AnimationError(f"WaveSpeed submit gave no request id: {resp.text[:500]}")

        result_url = f"{ws.api_base}/api/v3/predictions/{request_id}/result"
        deadline = time.monotonic() + ws.timeout_seconds
        while time.monotonic() < deadline:
            try:
                poll = requests.get(result_url, headers=headers, timeout=60)
            except requests.RequestException as exc:
                raise AnimationError(f"WaveSpeed poll failed: {exc}") from exc
            body = poll.json()
            status, data = self._extract_status(body)
            if status == "completed":
                outputs = data.get("outputs") or []
                if not outputs:
                    raise AnimationError("WaveSpeed job completed with no outputs")
                return self._download(outputs[0], output_path, headers)
            if status == "failed":
                raise AnimationError(f"WaveSpeed job failed: {data.get('error', poll.text[:500])}")
            time.sleep(ws.poll_interval_seconds)
        raise AnimationError(
            f"WaveSpeed job {request_id} did not complete within {ws.timeout_seconds}s"
        )

    @staticmethod
    def _extract_status(body: dict) -> tuple[str | None, dict]:
        """WaveSpeed docs show status at top level; also handle it nested
        under 'data', since summarized docs disagreed and this wasn't
        confirmed against a raw real response — whichever is present wins."""
        data = body.get("data") or {}
        status = body.get("status") or data.get("status")
        return status, data

    @staticmethod
    def build_payload(image_url: str, video_url: str, config) -> dict:
        """Request body for kwaivgi/kling-v3.0-pro/motion-control."""
        ws = config.wavespeed
        payload = {
            "image": image_url,
            "video": video_url,
            "character_orientation": ws.character_orientation,
            "keep_original_sound": ws.keep_original_sound,
        }
        if ws.prompt:
            payload["prompt"] = ws.prompt
        if ws.negative_prompt:
            payload["negative_prompt"] = ws.negative_prompt
        return payload

    def _upload(self, path: Path, api_base: str, headers: dict) -> str:
        path = Path(path)
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
            raise AnimationError(f"WaveSpeed upload of {path.name} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise AnimationError(
                f"WaveSpeed upload of {path.name} returned {resp.status_code}: {resp.text[:500]}"
            )
        url = (resp.json().get("data") or {}).get("download_url")
        if not url:
            raise AnimationError(f"WaveSpeed upload of {path.name} gave no download_url: {resp.text[:500]}")
        return url

    def _download(self, url: str, output_path: Path, headers: dict) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with requests.get(url, headers=headers, stream=True, timeout=600) as resp:
                if resp.status_code >= 400:
                    raise AnimationError(
                        f"WaveSpeed output download returned {resp.status_code}"
                    )
                with output_path.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
        except requests.RequestException as exc:
            raise AnimationError(f"WaveSpeed output download failed: {exc}") from exc
        return output_path


def get_animation_provider(config, logger: logging.Logger | None = None) -> AnimationProvider:
    """Select the FR-5 provider from config. WaveSpeed requires explicit opt-in."""
    provider = config.animation.provider
    if provider == "local_comfyui":
        return LocalComfyUIAnimationProvider(logger=logger)
    if provider == "wavespeed":
        if not config.wavespeed.enabled:
            raise AnimationNotConfigured(
                "animation.provider is 'wavespeed' but wavespeed.enabled is false. "
                "WaveSpeed is never called unless explicitly enabled — set "
                "wavespeed.enabled: true to opt in, or set animation.provider "
                "back to 'local_comfyui'."
            )
        return WaveSpeedAnimationProvider(logger=logger)
    if provider == "mock":
        return MockAnimationProvider(logger=logger)
    raise AnimationNotConfigured(
        f"unknown animation.provider {provider!r}; valid values: "
        "local_comfyui, wavespeed, mock"
    )
