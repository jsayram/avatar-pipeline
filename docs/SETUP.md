# SETUP — Install, Prerequisites & Gap Checklist

> Machine snapshot taken 2026-06-28 — **MacBook Pro M1 Max, 64 GB**. This is the "what's
> missing to actually build it" list. Nothing here is installed automatically; it's the record.

---

## 0. Current machine state (verified)
**Already present ✅:** `python3`, `pip3`, `git`, `docker`, `node`, `brew`, **Ollama** (+ models in
`~/llms/ollama`), ~**219 GB** free disk.

**Not yet installed ❌:** `ffmpeg`, `yt-dlp`, `exiftool`, `n8n` (CLI), **ComfyUI**, **Kohya** (LoRA training).

> `torch`/`transformers`/`diffusers` show missing in the *global* python — that's fine: they live in
> the `local_imageCreate_test` venv, and ComfyUI/training use their own venvs. Not a real gap.

---

## 1. Quick installs (minutes)
```bash
brew install ffmpeg yt-dlp exiftool
```
- **n8n** — run via Docker (already installed); see `automated-avatar-pipeline-n8n.md` §4d.
- **DINOv2 gate venv** — `python3 -m venv gate && source gate/bin/activate && pip install fastapi uvicorn torch transformers pillow`.

---

## 2. ComfyUI + nodes + models (the big install)
- **ComfyUI** (API mode) — clone, venv, `pip install -r requirements.txt`, run `--listen` (pipeline doc §4a).
- **Custom nodes** (via ComfyUI Manager): PuLID-Flux, IPAdapter_plus, **comfyui_controlnet_aux (DWPose)**,
  KJNodes, **Wan 2.2 video nodes**, an SAM/segment node.
- **Models** — point ComfyUI at `~/llms` via `extra_model_paths.yaml` (pipeline doc §4c). Need:
  - FLUX.1 **schnell** / SDXL / **RealVisXL** (RealVisXL already in `~/llms`)
  - **Wan 2.2 Animate**
  - **ControlNet** (DWPose/OpenPose + Depth)
  - **SAM** (person segmentation)
  - **DINOv2** (gate, pulled by `transformers`)
  - **Real-ESRGAN** upscaler
- **Disk budget:** ~**50–100 GB** of models (you have ~219 GB free ✅).

---

## 3. Prerequisites — the real blockers
### 3a. The avatar doesn't exist yet (step zero)
Design + generate the synthetic character. Everything keys off this.

### 3b. Character LoRA = the keystone ⚠️
- Generate ~20–40 varied shots of the avatar (angles, lighting, the states you need).
- Train a LoRA (**Kohya**, normal image-based training — **NOT** InsightFace, to stay commercial-clean).
- **M1 Max caveat:** LoRA training on Apple Silicon (MPS) is **slow and finicky.** Recommended:
  **rent a GPU for ~30–60 min (RunPod, ~$1–3)**, train there, bring the small `.safetensors`
  back to `~/llms/.../loras/` to use locally forever.
- *The pipeline runs free locally; LoRA training is the one step where a few cloud dollars saves pain.*

---

## 4. Code to write (scaffolding — not yet created)
- `pick_next.py` — read the `.numbers` sheet + `processed.json` de-dupe.
- `face_gate.py` — DINOv2 FastAPI similarity service (pipeline doc §7).
- **worker chain** — yt-dlp → ffmpeg first-frame → ComfyUI(avatar) → Wan → gate → strip → publish.
- **ComfyUI workflow JSONs** — avatar-into-frame + Wan-Animate (build in the UI, then API-export).
- **n8n workflow** — Schedule (daily) → the steps above.

---

## 5. Recommended build order
1. `brew install ffmpeg yt-dlp exiftool`.
2. **Create the avatar + train its LoRA** (cloud GPU) — the keystone.
3. Install **ComfyUI** + custom nodes + models; point at `~/llms`.
4. **Build & tune the two ComfyUI workflows** in the UI (most hands-on time).
5. Write `pick_next.py` + `face_gate.py` + the worker chain.
6. Wire the **n8n Schedule** trigger; run unattended; test end-to-end on one link.

---

## 6. Open decisions (not yet settled)
- **Video render budget:** are M1 Max Wan-Animate times tolerable, or offload Step 5 to a cloud GPU?
- **Clip length cap** (long TikToks may exceed a day locally).
- **Notification** when the daily video is ready (local notification / email)?
- **.numbers state:** sidecar `processed.json` + `done.csv` (recommended) vs risky write-back.

---

## 7. Status checklist (updated 2026-07-02 — code built, quick installs done)
```
[x] brew install ffmpeg yt-dlp exiftool            (2026-07-02)
[ ] n8n running — installed via npm (host, not Docker); import n8n/workflow.json + activate
[ ] ComfyUI installed + API mode (--listen)
[ ] custom nodes installed
[ ] models downloaded → ~/llms (extra_model_paths.yaml)
[x] avatar designed (AvatarGirl; gate reference installed at assets/avatar_reference.png)
[ ] character LoRA trained (cloud GPU) → ~/llms/.../loras
      candidates: iCloud SocialAvatar/AvatarGirl/outputs (b1=410, b2=100, b3=100 imgs — curate 20–40)
[x] DINOv2 face_gate.py service                    (built + verified; weights cached)
[x] pick_next.py (.numbers reader + dedupe)        (built + verified on real sheet)
[x] worker chain script                            (built; dry-run verified)
[x] ComfyUI workflow templates + n8n workflow.json (export real API workflows — comfyui/README.md)
[ ] ComfyUI workflows exported (avatar-into-frame, Wan-Animate)
[ ] end-to-end test on one link
```
