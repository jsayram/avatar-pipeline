# Automated Local Avatar Pipeline — n8n + ComfyUI + Ollama

> Written 2026-06-27 for: **MacBook Pro M1 Max, 64 GB, macOS**. Fully local, self-hosted.
> Goal: drop in a **reference image** (+ optional **reference video**), and have a local AI
> orchestrate generation of the **SAME avatar** across many images with **face lock**, plus
> a motion-matched **avatar video** — input one-off, via a **spreadsheet**, or a **watched folder**.
>
> ⚠️ Two honest caveats up front (details in §1 and §8):
> 1. **"100% face lock" isn't something a generative model literally guarantees.** You get
>    ~90–98% with the right stack, and you make it *effectively* 100% by adding an automated
>    **face-similarity gate** that rejects + retries anything below a threshold. Nothing ships
>    below your bar.
> 2. **Video is slow on an M1 Max** (2021 GPU): minutes per short clip. Great for batch/overnight,
>    not real-time. Memory (64 GB) is fine; compute is the limit.

---

## 0.5 ✅ Fully-free-for-commercial build (use this if you monetize)
The "obvious" stack leans on **InsightFace** (antelopev2/buffalo) via PuLID/InstantID/
IP-Adapter-FaceID — and **InsightFace's pretrained models are non-commercial**. To be 100%
commercial-free **without losing quality**, make these swaps:

| Job | ❌ Non-commercial default | ✅ Free-commercial swap | Quality impact |
|-----|--------------------------|------------------------|----------------|
| Identity / face lock | PuLID / InstantID / IP-Adapter FaceID (InsightFace) | **Character LoRA** trained per avatar (your asset) | **none — better.** LoRA is the gold standard for a *recurring* avatar; you only lose zero-shot "one-photo" convenience |
| Image base | FLUX.1 **dev** | **FLUX.1 schnell** or **Qwen-Image** (both Apache 2.0) | minimal — closed by LoRA + upscale |
| Face-gate metric | InsightFace ArcFace (antelopev2) | **DINOv2** (Apache 2.0) or **OpenCV SFace** (commercial-safe) | none — LoRA does identity, gate is a safety net |
| Video / motion | **Wan 2.2 Animate** | same — **Apache 2.0** ✅ | — |
| Pose control | original OpenPose (academic) | **DWPose / RTMPose** (Apache) | none |
| Upscaler | — | **Real-ESRGAN** (BSD-3) | — |
| LLM / VLM | — | **Qwen3 / Qwen3-VL** (Apache) | — |
| Orchestrator | — | **n8n** self-host (free for your own business) — or **Node-RED / Airflow** (Apache) for zero asterisks | — |
| Runtime | — | **ComfyUI** (GPL-3.0 — software is free, **your images are yours**) | — |

**Net:** the only thing you give up is *zero-shot* face injection. You replace it with a
**one-time LoRA train per avatar** — the better path for "the SAME avatar forever" anyway.
Everything else stays top-quality and **$0, commercially clean**. The rest of this doc uses
this free-commercial stack.

---

## 1. What "face lock" really means here
A diffusion model is stochastic, so you don't *force* an identical face — you **stack
constraints** and then **verify**:

| Layer | Tool | Locks |
|-------|------|-------|
| 1. **Identity (primary)** | **Character LoRA** — trained once per avatar (your asset, fully commercial) | the whole identity: face, body, hair, clothing, "feel" |
| 2. Determinism | fixed **seed** + sampler/settings + **ControlNet (DWPose, Apache)** pose | composition / reproducibility |
| 3. **Enforcement gate** | **DINOv2 (Apache)** or **OpenCV SFace** cosine-similarity | rejects any output below threshold, auto-retries |
| (optional) zero-shot face | PuLID / InstantID — ⚠️ **personal use only** (InsightFace = non-commercial) | quick one-photo face, no training |

> The **gate** is the trick that delivers your "100%": compute face embedding of each output,
> compare to the reference, and only accept ≥ your threshold (e.g. **0.65–0.75 cosine**).
> Below it → retry with a new seed (up to N times) → else flag the row. So "100% face lock"
> becomes **"0% of shipped images are below the bar."**

For the **video** ("video reference lock"): **Wan 2.2 Animate** takes your character image +
a driving video and **replicates that video's motion + expressions** onto the avatar — that's
the lock to the reference video.

---

## 2. Architecture
```
                         ┌──────────────────────────────────────────────┐
 INPUT                   │                  n8n (orchestrator)            │
 ┌───────────────┐       │                                              │
 │ Spreadsheet   │──────▶│  1. Trigger (sheet row / watched folder /     │
 │ (CSV/GSheet)  │       │     webhook / schedule)                       │
 │  or           │       │  2. Loop rows (Split in Batches)              │
 │ Watched dir   │──────▶│  3. Ollama: qwen3-vl reads ref image +        │
 │ (drop files)  │       │     LLM expands scene list → JSON prompts     │
 └───────────────┘       │  4. Build ComfyUI API JSON (inject ref image, │
                         │     prompt, LoRA, seed)                       │
                         │            │                                  │
                         │            ▼  HTTP POST /prompt                │
   ┌─────────────────────┼───▶ ComfyUI (image: FLUX + PuLID + LoRA) ◀──┐ │
   │  retry (new seed)    │            │  poll /history, GET /view      │ │
   │                      │            ▼                                │ │
   │              ┌───────┴── 5. Face-gate: DINOv2 cosine sim ──────────┘ │
   │   sim < thr  │                     │ sim ≥ threshold                  │
   └──────────────┘                     ▼                                  │
                         │  6. ComfyUI (video: Wan 2.2 Animate            │
                         │     char image + ref video) ──▶ poll/save      │
                         │  7. Save to output dir + update sheet status   │
                         └──────────────────────────────────────────────┘
 LOCAL MODELS: Ollama (qwen3-vl + Qwen3) · FLUX schnell / Qwen-Image · char LoRA · DINOv2 gate · DWPose · Wan 2.2 Animate · Real-ESRGAN
```

---

## 3. Components (all local, all free to run)
| Component | Role | Install |
|-----------|------|---------|
| **n8n** (self-hosted) | orchestrator | Docker (you already have n8n state in `~/.cache/n8n`) |
| **ComfyUI** (API mode) | image + video engine | `git clone` + run with `--listen` |
| **Ollama** | local AI: qwen3-vl reads the ref image, an LLM writes scene prompts | already installed → `~/llms/ollama` |
| **DINOv2** (Apache 2.0) | face-similarity gate (commercial-safe) | `facebook/dinov2-base` via `transformers` |
| Models | FLUX schnell/dev, PuLID-Flux, char LoRA, Wan 2.2 Animate, 4x upscaler | see §6 |

---

## 4. Setup — step by step

### 4a. ComfyUI in API mode
```bash
git clone https://github.com/comfyanonymous/ComfyUI && cd ComfyUI
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
# Mac (Metal/MPS) + expose the API for n8n:
python main.py --listen 0.0.0.0 --port 8188
```
API you'll call from n8n: `POST /prompt` (queue), `GET /history/{id}` (poll), `GET /view` (fetch image).
In the ComfyUI web UI, build/import a workflow, then **Menu → Export (API)** to get the JSON template n8n will fill in.

### 4b. Custom nodes (via ComfyUI Manager)
- **ComfyUI Manager** (install first): `git clone` into `ComfyUI/custom_nodes/`
- **PuLID-Flux** (face lock) — provides `PulidFluxInsightFaceLoader`, `ApplyPulidFlux`
- **ComfyUI_IPAdapter_plus** (IP-Adapter FaceID alt path for SDXL)
- **comfyui_controlnet_aux** (DWPose estimator — needed by Wan Animate)
- **ComfyUI-KJNodes** (Points Editor — Wan Animate)
- **Wan 2.2 video nodes** (native Wan support / WanVideoWrapper) — `WanAnimateToVideo`

### 4c. Keep ComfyUI models in `~/llms` (don't fork your storage!)
ComfyUI defaults to `ComfyUI/models/`. Point it at your central store instead with
`ComfyUI/extra_model_paths.yaml`:
```yaml
llms:
  base_path: /Users/jramirez/llms/image-models/comfyui
  checkpoints: checkpoints
  loras: loras
  vae: vae
  unet: unet
  insightface: insightface
```
(Create those folders under `~/llms/image-models/comfyui/` and drop models there.)

### 4d. n8n (self-hosted via Docker)
```bash
docker run -d --name n8n -p 5678:5678 \
  -v ~/.n8n:/home/node/.n8n \
  -e N8N_SECURE_COOKIE=false \
  docker.n8n.io/n8nio/n8n
# open http://localhost:5678
```
> Note: a Dockerized n8n sees the host via `host.docker.internal` (use that instead of
> `localhost` when calling ComfyUI at `:8188` and Ollama at `:11434`). To read/write local
> files/folders, mount them: add `-v ~/avatars:/data/avatars`.

### 4e. Ollama models for orchestration
```bash
ollama pull qwen3-vl        # vision: describe / tag the reference image
ollama pull qwen3:14b       # text: expand a scene list into rich prompts
```

---

## 5. The n8n workflow (node by node)
1. **Trigger** — pick one:
   - **Spreadsheet:** *Google Sheets* node, or *Read Binary File* + *Spreadsheet File* (CSV).
   - **Watched folder:** *Local File Trigger* (or a *Schedule* node that lists a dir and diffs).
   - **One-off / API:** *Webhook* node.
2. **Loop** — *Split in Batches* over rows (one avatar/scene per iteration).
3. **(Optional) AI orchestration** — *Ollama* / *AI Agent* node:
   - feed the ref image to **qwen3-vl** → get a description/tags,
   - feed description + the row's scene list to **qwen3** → return **JSON** of detailed prompts.
4. **Build ComfyUI JSON** — *Set*/*Code* node: take your exported **image workflow** template
   and inject `ref_image`, `prompt`, `lora`, `seed`.
5. **Queue image** — *HTTP Request*: `POST http://host.docker.internal:8188/prompt`
   body `{"prompt": <workflow_json>, "client_id":"n8n"}` → returns `prompt_id`.
6. **Poll** — *HTTP Request* in a *Wait/loop*: `GET /history/{prompt_id}` until it has outputs;
   then `GET /view?filename=...&type=output` to fetch the PNG.
7. **Face-gate** — *HTTP Request* to a tiny local face-compare service (§7):
   `POST /compare {ref, generated}` → `{cosine: 0.78}`.
   - *IF* `cosine < threshold` → back to step 5 with a **new seed** (max N retries).
   - *ELSE* continue.
8. **(If row has ref_video) Animate** — *HTTP Request*: `POST /prompt` with the
   **Wan 2.2 Animate** workflow (character image + `ref_video`) → poll → save the MP4.
9. **Output** — *Write Binary File* to `~/avatars/out/<output_name>/…`, then update the
   sheet row `status = done` (+ paths, + final cosine score).

---

## 6. Models to download (and where)
| Model | Folder (under `~/llms/image-models/comfyui/`) | License |
|-------|-----------------------------------------------|---------|
| **FLUX.1 schnell** (or **Qwen-Image**) | `unet/` or `checkpoints/` + clip + `vae/` | **Apache 2.0 ✅** |
| **Character LoRA** (you train, once per avatar) | `loras/` | **yours ✅** |
| **Wan 2.2 Animate** | `diffusion_models/` | **Apache 2.0 ✅** |
| **DWPose / RTMPose** (pose for Wan + ControlNet) | `controlnet_aux` | Apache ✅ |
| **DINOv2** (face-gate embedding) | gate service (§7) | **Apache 2.0 ✅** |
| **Real-ESRGAN / 4x upscaler** | `upscale_models/` | BSD-3 / open ✅ |
| *(optional, personal only)* FLUX **dev** · PuLID · antelopev2 | — | ⚠️ non-commercial |

---

## 7. The face-lock gate (tiny local microservice — commercial-safe)
Use **DINOv2 (Apache 2.0)** embeddings for the gate — no InsightFace, fully commercial.
~25 lines; gives n8n a clean pass/fail to branch on:
```python
# face_gate.py  →  run: uvicorn face_gate:app --port 8189
from fastapi import FastAPI
from transformers import AutoModel, AutoImageProcessor
from PIL import Image
import torch, torch.nn.functional as F
app = FastAPI()
proc  = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
model = AutoModel.from_pretrained("facebook/dinov2-base").eval()   # Apache 2.0

def emb(path):
    x = proc(images=Image.open(path).convert("RGB"), return_tensors="pt")
    with torch.no_grad():
        v = model(**x).last_hidden_state.mean(dim=1)   # pooled embedding
    return F.normalize(v, dim=-1)

@app.post("/compare")
def compare(ref: str, gen: str):
    cos = float((emb(ref) * emb(gen)).sum())
    return {"cosine": cos, "pass": cos >= 0.88}
```
n8n calls `POST http://host.docker.internal:8189/compare` and branches on `pass`.
DINOv2 scores higher than ArcFace — tune ~**0.82** loose, **0.88** strict, **0.92** very strict.
> Tighter identity signal: crop to the face first with a free **YOLO-face** detector (Apache),
> then embed. For ArcFace-grade scoring with a commercial license, use **OpenCV SFace**
> (`insightface-opencv` backend) — commercial-safe, unlike antelopev2.

---

## 8. Performance on your M1 Max 64 GB (set expectations)
| Step | Rough time |
|------|-----------|
| One FLUX+PuLID image (schnell) | ~20–60 s |
| One FLUX+PuLID image (dev) | ~1–3 min |
| Face-gate compare | < 1 s |
| **Wan 2.2 Animate ~5 s clip** | **many minutes** (14B; +77 frames ≈ 4.8 s per extend block) |

➡️ **Run image batches locally** (overnight for big sheets). For **video volume**, consider a
**hybrid**: do identity + stills locally, push the heavy Wan-Animate render to a rented GPU
(RunPod) or your existing **WaveSpeed/cloud** — the M1 Max GPU is the bottleneck, not RAM.

---

## 9. ✅ Licensing — the fully-free-commercial stack (verified mid-2026)
Every component below is free for commercial use. The key move: identity via **LoRA, not
PuLID/InstantID**, which removes the InsightFace dependency entirely.

| Component | Choice | License |
|-----------|--------|---------|
| Image base | FLUX.1 **schnell** / **Qwen-Image** | Apache 2.0 ✅ |
| Identity | **Character LoRA** (yours) | your asset ✅ |
| Face-gate | **DINOv2** (or OpenCV SFace) | Apache 2.0 / commercial-safe ✅ |
| Pose | **DWPose / RTMPose** | Apache ✅ |
| Video | **Wan 2.2 Animate** | Apache 2.0 ✅ |
| Upscaler | **Real-ESRGAN** | BSD-3 ✅ |
| LLM/VLM | **Qwen3 / Qwen3-VL** | Apache 2.0 ✅ |
| Runtime | **ComfyUI** (your outputs are yours) | GPL-3.0 (software) ✅ |
| Orchestrator | **n8n** self-host (own business) or **Node-RED / Airflow** | fair-code / Apache ✅ |

**Personal-use-only (keep OUT of the commercial pipeline):** FLUX.1 **dev**, **PuLID /
InstantID / IP-Adapter FaceID**, **InsightFace** pretrained packs (antelopev2 / buffalo).
⚠️ Gotcha: a LoRA trained *using InsightFace embeddings* inherits the non-commercial limit —
train LoRAs the normal way (images + base model via **Kohya**/diffusers), which doesn't touch
InsightFace. n8n's license allows running your own business pipeline; it only forbids reselling
n8n-as-a-service. Always re-check a model card before shipping — licenses change.

---

## 10. Build order (suggested)
1. ComfyUI API up; build + **Export (API)** a FLUX+PuLID image workflow that nails one avatar.
2. Add the **face-gate** service; wire the retry loop in n8n on a single row.
3. Add the **spreadsheet** input + loop; batch 10 images of the same avatar across scenes.
4. Add the **Wan 2.2 Animate** branch for rows that include a `ref_video`.
5. Add **upscale** + output/sheet-update; then scale the sheet.

### Example spreadsheet schema
```
avatar_id, ref_image,            ref_video,             scenes,                         lora,              seed,  status
maria01,   /data/refs/maria.jpg, /data/refs/dance.mp4,  "coffee shop|beach|office desk", maria_v1.safetensors, 12345, pending
```

---

## Sources (mid-2026)
- n8n + ComfyUI: https://dev.to/worldlinetech/automating-image-generation-with-n8n-and-comfyui-521p · https://use-apify.com/blog/comfyui-n8n-image-generation
- Self-hosted stack (Ollama+n8n+ComfyUI): https://jameskilby.co.uk/2026/03/my-self-hosted-ai-stack-a-technical-deep-dive/ · https://github.com/freddy-schuetz/ai-launchkit
- Character consistency (PuLID + LoRA + FaceID): https://www.apatero.com/blog/comfyui-character-consistency-advanced-workflows-2026 · https://www.viewcomfy.com/blog/consistent-ai-characters-with-flux-and-comfyui
- PuLID InsightFace loader: https://www.runcomfy.com/comfyui-nodes/ComfyUI_PuLID_Flux_ll/pulid-flux-insight-face-loader
- Wan 2.2 Animate workflow: https://docs.comfy.org/tutorials/video/wan/wan2-2-animate · https://comfyui-wiki.com/en/tutorial/advanced/video/wan2.2/wan2-2-animate · https://www.nextdiffusion.ai/tutorials/how-to-use-wan-2-2-animate-in-comfyui-for-character-animations
