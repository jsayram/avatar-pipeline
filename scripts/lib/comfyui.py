"""ComfyUI HTTP API client + workflow template handling (FR-4, FR-5).

API contract (PRODUCT-SPEC §9): POST /prompt {prompt, client_id} -> {prompt_id};
poll GET /history/{prompt_id}; fetch GET /view?filename=&subfolder=&type=output.
Input files are pushed with POST /upload/image (works for videos too — ComfyUI
just drops them into its input directory).

Workflow JSONs are authored in the ComfyUI UI and exported via "Export (API)".
The operator marks the dynamic fields with __TOKEN__ placeholders (see
comfyui/README.md); inject() substitutes them at run time. The placeholder
files shipped in comfyui/ carry "__template__": true and are rejected with
setup instructions until replaced by real exports.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path

import requests

# Tokens the pipeline knows how to fill. Only tokens actually present in the
# exported workflow are substituted; the rest of this vocabulary is optional.
KNOWN_TOKENS = (
    "__FRAME_IMAGE__",    # uploaded first-frame filename (FR-4)
    "__AVATAR_REFERENCE__", # uploaded canonical avatar reference image (FR-4 IPAdapter)
    "__AVATAR_IMAGE__",   # uploaded avatar_frame1.png (FR-5)
    "__REF_VIDEO__",      # uploaded capped reference video (FR-5)
    "__LORA_NAME__",      # character LoRA filename as ComfyUI sees it
    "__BASE_MODEL__",     # config.video.base_model
    "__WAN_MODEL__",      # config.video.wan_model
    "__SEED__",           # int — varied on identity-gate retries
    "__WIDTH__",          # ref dims rounded down to a multiple of 16
    "__HEIGHT__",
    "__MAX_SECONDS__",    # config.video.max_clip_seconds
    "__LENGTH__",         # Wan frame count for this render (see WAN_FPS note below)
)

_TOKEN_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")
_INT_TOKENS = {"__SEED__", "__WIDTH__", "__HEIGHT__", "__MAX_SECONDS__", "__LENGTH__"}

# Wan 2.2 Animate's native frame rate and its recommended single-block length
# (docs: "each extend block ~77 frames ~4.8s"). v1 of this pipeline renders
# ONE block per run regardless of config.video.max_clip_seconds — chaining
# multiple blocks via WanAnimateToVideo's continue_motion/video_frame_offset
# inputs for longer output is a documented future enhancement, not yet
# implemented (see comfyui/README.md).
WAN_FPS = 16
WAN_MAX_BLOCK_SECONDS = 5
LOCAL_IPADAPTER_MAX_REFS = 3
LOCAL_IPADAPTER_MULTI_REF_WEIGHTS = (0.42, 0.28, 0.32)


class ComfyUIError(Exception):
    """ComfyUI rejected the prompt or the render failed."""


class ComfyUIUnreachable(ComfyUIError):
    """ComfyUI is not running/reachable — a setup problem, not a per-URL
    failure, so the worker must not flag the URL."""


class WorkflowTemplateError(ComfyUIError):
    """The workflow JSON is still the shipped placeholder, not a real export."""


def load_workflow(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise WorkflowTemplateError(
            f"ComfyUI workflow not found: {path}\n"
            "Author the workflow in the ComfyUI UI and save it via "
            "'Export (API)' to that path. See comfyui/README.md."
        )
    data = json.loads(path.read_text())
    if data.get("__template__"):
        raise WorkflowTemplateError(
            f"{path} is still the shipped placeholder template.\n"
            "Build the real workflow in the ComfyUI UI, set the __TOKEN__ "
            "placeholders listed in comfyui/README.md, then export it with "
            "'Export (API)' over this file."
        )
    return data


def inject(workflow: dict, replacements: dict[str, object]) -> dict:
    """Substitute __TOKEN__ placeholders throughout the workflow JSON.

    A string that IS a token becomes the replacement value (preserving int
    types for seeds/dims); a string CONTAINING a token gets str-substituted.
    Any known-style token left unreplaced afterwards is an error — it means
    the workflow expects a value the pipeline didn't provide.
    """
    def visit(node):
        if isinstance(node, dict):
            return {k: visit(v) for k, v in node.items()}
        if isinstance(node, list):
            return [visit(v) for v in node]
        if isinstance(node, str):
            if node in replacements:
                value = replacements[node]
                if node in _INT_TOKENS and not isinstance(value, bool):
                    try:
                        return int(value)
                    except (TypeError, ValueError):
                        pass
                return value
            out = node
            for token, value in replacements.items():
                if token in out:
                    out = out.replace(token, str(value))
            return out
        return node

    result = visit(workflow)
    leftover = sorted(set(_TOKEN_RE.findall(json.dumps(result))))
    if leftover:
        raise ComfyUIError(
            f"workflow still contains unfilled placeholders: {', '.join(leftover)}. "
            f"Pipeline provides: {', '.join(sorted(replacements))}. "
            "Fix the exported workflow or remove the extra tokens."
        )
    return result


class ComfyUIClient:
    def __init__(self, base_url: str, poll_interval: float = 2.0,
                 logger: logging.Logger | None = None):
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.client_id = f"avatar-pipeline-{uuid.uuid4().hex[:8]}"
        self.logger = logger or logging.getLogger("comfyui")

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        try:
            resp = requests.request(method, url, timeout=kwargs.pop("timeout", 60), **kwargs)
        except requests.ConnectionError as exc:
            raise ComfyUIUnreachable(
                f"cannot reach ComfyUI at {self.base_url} — is it running? "
                "(cd ~/ComfyUI && ./venv/bin/python main.py --listen 0.0.0.0 --port 8188)"
            ) from exc
        if resp.status_code >= 400:
            raise ComfyUIError(
                f"ComfyUI {method} {path} returned {resp.status_code}: {resp.text[:500]}"
            )
        return resp

    def upload(self, file_path: Path) -> str:
        """Push an input file (image or video) into ComfyUI's input dir."""
        file_path = Path(file_path)
        with file_path.open("rb") as fh:
            resp = self._request(
                "POST", "/upload/image",
                files={"image": (file_path.name, fh)},
                data={"overwrite": "true"},
                timeout=300,
            )
        info = resp.json()
        name = info.get("name", file_path.name)
        sub = info.get("subfolder") or ""
        return f"{sub}/{name}" if sub else name

    def queue(self, workflow: dict) -> str:
        resp = self._request(
            "POST", "/prompt",
            json={"prompt": workflow, "client_id": self.client_id},
        )
        body = resp.json()
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"ComfyUI /prompt gave no prompt_id: {body}")
        return prompt_id

    def wait(self, prompt_id: str, timeout_seconds: int) -> dict:
        """Poll /history until the prompt completes; return its outputs dict."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            resp = self._request("GET", f"/history/{prompt_id}")
            entry = resp.json().get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    messages = json.dumps(status.get("messages", []))[:800]
                    raise ComfyUIError(f"ComfyUI execution failed: {messages}")
                if entry.get("outputs"):
                    return entry["outputs"]
                if status.get("completed"):
                    raise ComfyUIError("ComfyUI prompt completed but produced no outputs")
            time.sleep(self.poll_interval)
        raise ComfyUIError(
            f"ComfyUI prompt {prompt_id} did not finish within {timeout_seconds}s "
            "(raise comfyui.*_timeout_seconds in config.yaml if renders are just slow)"
        )

    def download_output(self, file_info: dict, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = self._request(
            "GET", "/view",
            params={
                "filename": file_info["filename"],
                "subfolder": file_info.get("subfolder", ""),
                "type": file_info.get("type", "output"),
            },
            timeout=600,
        )
        dest.write_bytes(resp.content)
        return dest


def first_output_file(outputs: dict, kinds: tuple[str, ...]) -> dict:
    """Find the first saved file of the given kinds in a /history outputs dict.

    Image workflows report under 'images'; video nodes (VHS VideoCombine, Wan
    save nodes) report under 'gifs' or 'videos'.
    """
    for node_output in outputs.values():
        for kind in kinds:
            for file_info in node_output.get(kind, []):
                if file_info.get("filename"):
                    return file_info
    raise ComfyUIError(
        f"no output file of kind {kinds} in ComfyUI history "
        f"(nodes reported: {sorted(outputs)})"
    )


def avatar_reference_paths(cfg) -> list[Path]:
    """Identity references for FR-4 image guidance.

    Explicit avatar_frame.identity_references are used first. If none are
    configured, keep the previous behavior: prefer assets/avatar_reference_face.png
    and fall back to paths.avatar_reference.
    """
    if getattr(cfg.avatar_frame, "identity_references", ()):
        return list(cfg.avatar_frame.identity_references)
    avatar_reference = cfg.paths.avatar_reference
    face_reference = avatar_reference.with_name("avatar_reference_face.png")
    return [face_reference if face_reference.exists() else avatar_reference]


def _add_ipadapter_reference_chain(workflow: dict, uploaded_refs: list[str]) -> dict:
    """Apply up to three identity refs in one sampling pass.

    Repeated img2img passes tend to smooth the face and drift away from the
    source frame. Chaining IPAdapter guidance before the main sampler reinforces
    identity while preserving the original single render pass.
    """
    if len(uploaded_refs) <= 1:
        return workflow
    if "28" not in workflow or workflow["28"].get("class_type") != "IPAdapterAdvanced":
        raise ComfyUIError(
            "multi-reference local ComfyUI mode expects IPAdapterAdvanced node '28' "
            "in avatar_into_frame.api.json"
        )

    refs = uploaded_refs[:LOCAL_IPADAPTER_MAX_REFS]
    workflow["28"]["inputs"]["weight"] = LOCAL_IPADAPTER_MULTI_REF_WEIGHTS[0]
    workflow["28"]["inputs"]["end_at"] = 0.75

    next_id = max(int(node_id) for node_id in workflow if str(node_id).isdigit()) + 1
    previous_model_node = "28"
    for ref_index, uploaded_ref in enumerate(refs[1:], start=1):
        load_id = str(next_id)
        apply_id = str(next_id + 1)
        next_id += 2
        workflow[load_id] = {
            "class_type": "LoadImage",
            "inputs": {"image": uploaded_ref},
        }
        workflow[apply_id] = {
            "class_type": "IPAdapterAdvanced",
            "inputs": {
                "model": [previous_model_node, 0],
                "ipadapter": ["27", 0],
                "image": [load_id, 0],
                "weight": LOCAL_IPADAPTER_MULTI_REF_WEIGHTS[ref_index],
                "weight_type": "linear",
                "combine_embeds": "concat",
                "start_at": 0.0,
                "end_at": 0.75,
                "embeds_scaling": "V only",
                "clip_vision": ["29", 0],
            },
        }
        previous_model_node = apply_id

    for node in workflow.values():
        inputs = node.get("inputs")
        if (
            node.get("class_type") != "IPAdapterAdvanced"
            and isinstance(inputs, dict)
            and inputs.get("model") == ["28", 0]
        ):
            inputs["model"] = [previous_model_node, 0]
    return workflow


def generate_avatar_frame(
    cfg,
    frame_path: Path,
    dest: Path,
    seed: int,
    logger: logging.Logger,
) -> Path:
    """FR-4: SAM mask + DWPose ControlNet + LoRA inpaint via the exported workflow."""
    workflow = load_workflow(cfg.paths.workflows_dir / "avatar_into_frame.api.json")
    client = ComfyUIClient(
        cfg.endpoints.comfyui_url,
        poll_interval=cfg.comfyui.poll_interval_seconds,
        logger=logger,
    )
    uploaded = client.upload(frame_path)
    reference_paths = avatar_reference_paths(cfg)
    uploaded_avatar_refs = [
        client.upload(path)
        for path in reference_paths[:LOCAL_IPADAPTER_MAX_REFS]
    ]
    workflow = _add_ipadapter_reference_chain(workflow, uploaded_avatar_refs)
    workflow = inject(workflow, {
        "__FRAME_IMAGE__": uploaded,
        "__AVATAR_REFERENCE__": uploaded_avatar_refs[0],
        "__LORA_NAME__": cfg.paths.lora_path.name,
        "__BASE_MODEL__": cfg.video.base_model,
        "__SEED__": seed,
    })
    prompt_id = client.queue(workflow)
    logger.info(
        "ComfyUI avatar-into-frame queued (prompt_id=%s, %d identity ref(s))",
        prompt_id,
        len(uploaded_avatar_refs),
    )
    outputs = client.wait(prompt_id, cfg.comfyui.image_timeout_seconds)
    file_info = first_output_file(outputs, kinds=("images",))
    return client.download_output(file_info, dest)
