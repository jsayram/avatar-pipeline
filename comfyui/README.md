# ComfyUI workflows — how to export the real API JSONs

The two `.api.json` files here ship as **placeholders** (they contain
`"__template__": true` and the pipeline refuses to run until they're replaced).
Real API workflows can't be generated outside your install: node ids, model
filenames, and custom-node versions are specific to your ComfyUI. You author
them once in the UI and export them.

## One-time export procedure (both workflows)

1. Start ComfyUI (`cd ~/ComfyUI && ./venv/bin/python main.py --listen 0.0.0.0
   --port 8188`) and open http://localhost:8188.
2. Build/tune the workflow in the UI until a manual run produces a good result
   (this is the hands-on part — see the per-workflow notes below).
3. Replace the dynamic input values with the **literal placeholder tokens**
   listed below (type the token text, e.g. `__SEED__`, straight into the
   widget/input — for numeric widgets, convert the input to a text/primitive
   input or edit the exported JSON afterwards).
4. Enable dev mode: **Settings (gear) → Enable Dev mode Options**.
5. **Menu → Export (API)** (not the plain "Export") and save over the file in
   this directory.
6. Sanity-check: `python -c "import json;print(json.load(open('comfyui/wan_animate.api.json')).get('__template__'))"`
   must print `None`, and the file must contain your tokens
   (`grep __SEED__ comfyui/*.api.json`).
7. Verify end-to-end with `./venv/bin/python scripts/worker.py --url <test-url>
   --config config.yaml --dry-run` — template warnings disappear when the
   exports are in place.

The pipeline substitutes tokens anywhere they appear in the JSON. Required
tokens must be present; optional ones are only filled if you used them.
Any `__TOKEN__`-style string left in the workflow that the pipeline doesn't
know is an error, so typos surface immediately.

## `avatar_into_frame.api.json` (FR-4) — BUILT, validated 2026-07-03

Puts your avatar into the reference video's first frame, same pose/scene.
Identity comes from **your character LoRA** — no InsightFace/PuLID/InstantID
in this workflow (commercial-clean requirement, PRODUCT-SPEC §5.2). Built by
authoring the API JSON directly against `/object_info` introspection, not the
ComfyUI UI — see the technique note at the bottom of this file.

Actual graph:

- `CheckpointLoaderSimple` — ckpt_name: `__BASE_MODEL__` (RealVisXL V5.0)
- `LoadImage` — image: `__FRAME_IMAGE__`
- `LoraLoader` — lora_name: `__LORA_NAME__` (must be visible to ComfyUI via
  `extra_model_paths.yaml` → `loras:`)
- `GroundingDinoModelLoader` + `SAMModelLoader` + `GroundingDinoSAMSegment`
  (prompt: `"person"`) → person mask
- `DWPreprocessor` (`scale_stick_for_xinsr_cn: enable` — required specifically
  because we use xinsir's controlnet-union model) → pose image
- `ControlNetLoader` + `SetUnionControlNetType("openpose")` +
  `ControlNetApplyAdvanced` → pose-conditioned positive/negative
- `InpaintModelConditioning` → `KSampler` (dpmpp_2m/karras, denoise=1.0 since
  `noise_mask` fully replaces the masked region) — seed: `__SEED__`
- `VAEDecode`
- **Face-detail + upscale pass** (added 2026-07-03 for fidelity — see
  below): `UltralyticsDetectorProvider` + `SAMLoader` + `FaceDetailer`
  (Impact Pack; denoise=0.4, crops to the face and re-samples at higher
  effective resolution before pasting back) → `UpscaleModelLoader` +
  `ImageUpscaleWithModel` (Real-ESRGAN 4x) → `GetImageSize`(of the *original*
  `__FRAME_IMAGE__`) + `ImageScale` back down to that same size (lanczos) —
  net effect is a genuine hi-res-fix-style detail injection without changing
  output dimensions.
- `SaveImage`

Required tokens: `__FRAME_IMAGE__`, `__LORA_NAME__`, `__SEED__`.
Optional (now wired): `__BASE_MODEL__`.

**Needs (beyond the base FR-4 model set):** `bbox/face_yolov8m.pt` (Impact
Subpack face detector, `ultralytics_bbox` folder in `extra_model_paths.yaml`)
and the `ComfyUI-Impact-Pack`/`ComfyUI-Impact-Subpack` custom nodes. Both are
in `ops/download_models.py` / the ComfyUI install steps in `SETUP.md`.

If you ever need to rebuild this from scratch, the fastest path is NOT the
UI — dump `/object_info` on your running instance and author the JSON
directly (see technique note below); UI screenshots/tutorials go stale
quickly and canvas automation is unreliable.

## `wan_animate.api.json` (FR-5) — BUILT, structurally validated 2026-07-03

Animates the avatar frame with the reference video's motion via Wan 2.2
Animate. `WanAnimateToVideo` is a **native ComfyUI core node**
(`comfy_extras/nodes_wan.py`), not a custom node.

Actual graph:

- `UNETLoader`(wan2.2_animate_14B_bf16, weight_dtype=default) →
  `ModelSamplingSD3`(shift=5.0 — confirmed from the official bundled
  Wan2.2-14B-i2v template, don't reuse SDXL-family shift/sampler defaults)
- `CLIPLoader`(umt5_xxl_fp8_e4m3fn_scaled, type=`"wan"`) + `CLIPTextEncode` ×2
- `VAELoader`(wan_2.1_vae)
- `LoadImage`(`__AVATAR_IMAGE__`) → `CLIPVisionLoader`+`CLIPVisionEncode`
  (identity embedding) — the same avatar image also feeds
  `WanAnimateToVideo.reference_image` directly (raw pixels)
- `VHS_LoadVideo`(`__REF_VIDEO__`, force_rate=16) → `DWPreprocessor`
  (`scale_stick_for_xinsr_cn: disable` here — that flag is specific to the
  xinsir ControlNet used in FR-4, not relevant to Wan's own pose conditioning)
  → `WanAnimateToVideo.pose_video`
- **`WanAnimateToVideo`** (reference_image + clip_vision_output + pose_video;
  length: `__LENGTH__`, width/height: `__WIDTH__`/`__HEIGHT__`) → conditioning
  + latent + a `trim_latent` int output
- `KSampler`(euler/simple, cfg=5.0, 20 steps — NOT the 4-step/cfg=1 settings
  some official Wan i2v templates use with a distillation LoRA we didn't
  download) — seed: `__SEED__`
- **`TrimVideoLatent`** (trim_amount wired from `WanAnimateToVideo`'s
  `trim_latent` output) — **required**: the node front-loads a
  reference-image latent internally that must be sliced off before decode or
  the output video's first frame(s) are corrupted.
- `VAEDecode` → `VHS_VideoCombine` (`video/h264-mp4`)

Required tokens: `__AVATAR_IMAGE__`, `__REF_VIDEO__`, `__SEED__`, `__WIDTH__`,
`__HEIGHT__`, `__LENGTH__` (frame count; see `WAN_FPS`/`WAN_MAX_BLOCK_SECONDS`
in `scripts/lib/comfyui.py`).

**Known v1 limitation:** Wan Animate's official per-block length is ~77-81
frames (~5s @ 16fps). Longer clips need chaining multiple
`WanAnimateToVideo` calls via its `continue_motion`/`video_frame_offset`
inputs — **not implemented**. Every render today is capped to
`min(config.video.max_clip_seconds, 5)` seconds of *output*, regardless of
how long the reference clip is (the reference is just truncated to fit, so
feeding a longer clip is harmless, just wasteful). Multi-block chaining is a
scoped-out enhancement, not a bug. Also not wired: `face_video` (dedicated
face-crop channel for finer expression transfer), `character_mask`,
`background_video` — all present in the node's schema, easy to add later.

**Performance note (measured, not estimated):** on M1 Max/MPS, one 20-step
sampling attempt on this 14B model was still on step 1 after 8+ minutes
(interrupted before completion) — a full run would very plausibly take
2.5+ hours. This is dramatically slower than a rented CUDA GPU would be for
the same model. If daily-cadence local rendering proves impractical, this is
the concrete argument for enabling the WaveSpeed cloud provider
(`animation.provider: wavespeed`) for just this step — see root `SETUP.md`
§8. FR-4 (SDXL-based) is NOT similarly bottlenecked — a comparable single
image + face-detail pass + upscale took ~2.3 minutes.

## Technique: how these were actually built (no UI clicking)

1. `curl localhost:8188/object_info` — exact node class names, required vs
   optional inputs, types, and enum choices (model filenames, sampler lists,
   etc.) for *this specific install*. Never guess a node's schema.
2. Author the JSON directly, using those exact names/types.
3. `POST /prompt` against the running server — invalid graphs return an
   immediate 400 with `node_errors` before any slow execution starts, so
   wiring mistakes are caught in seconds, not after a 20-minute render.
4. For anything a node's name doesn't make obvious, read the source: core
   nodes live in `~/ComfyUI/comfy_extras/nodes_*.py`. Official example
   workflows (when they exist) are bundled at
   `~/ComfyUI/venv/lib/python3.14/site-packages/comfyui_workflow_templates_json/templates/`
   — note ComfyUI's "subgraph" feature nests the real graph under the JSON's
   `definitions.subgraphs[].nodes`, not the top-level `nodes` list.

Two real ComfyUI bugs were found this way (both `transformers`-API version
mismatches in `comfyui_segment_anything`'s vendored GroundingDINO code, not
anything specific to this setup) and fixed via a monkeypatch shim at
`~/ComfyUI/custom_nodes/zz_transformers_compat_patch/__init__.py` — see that
file's docstring for the full detail if something similar recurs.

Required tokens: `__AVATAR_IMAGE__`, `__REF_VIDEO__`, `__SEED__`.
Optional: `__WIDTH__`, `__HEIGHT__`, `__MAX_SECONDS__`, `__WAN_MODEL__`.

> Tip from the docs: try Wan 2.2 Animate's **replacement mode** (avatar image +
> reference video directly) before the two-stage swap — if it holds identity
> well enough, FR-4 tuning matters less.

## Custom nodes needed (install via ComfyUI Manager)

- `comfyui_controlnet_aux` (DWPose)
- a SAM/segmentation node pack (e.g. ComfyUI-SAM / Impact Pack)
- Wan 2.2 video nodes (native Wan support or WanVideoWrapper)
- `ComfyUI-KJNodes` (Points Editor, used by some Wan Animate graphs)
- `ComfyUI-VideoHelperSuite` (VHS_LoadVideo / VHS_VideoCombine)

Models go under `~/llms/image-models/comfyui/` via `extra_model_paths.yaml`
(see docs/automated-avatar-pipeline-n8n.md §4c).
