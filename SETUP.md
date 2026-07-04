# SETUP — install, configure, run

Operational guide for the built pipeline. Background/spec: [docs/SETUP.md](docs/SETUP.md)
(gap checklist) and [docs/PRODUCT-SPEC.md](docs/PRODUCT-SPEC.md) (authoritative spec).

Everything below runs **locally**; nothing calls out unless you explicitly
enable the WaveSpeed animation provider (§8).

---

## 1. Install dependencies

```bash
# CLI tools
brew install ffmpeg yt-dlp exiftool

# Python environment (one venv serves the worker, pick_next, and the gate)
cd /Users/jramirez/Git/avatar-pipeline
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

> torch/transformers in requirements.txt exist only for the DINOv2 gate
> service; `worker.py`/`pick_next.py` never import them.

## 2. Configure

```bash
cp config.example.yaml config.yaml
# then edit config.yaml — every path/threshold/endpoint lives there
```

Defaults already match this machine: `.numbers` sheet in iCloud Drive, LoRA at
`~/llms/image-models/comfyui/loras/avatar_v1.safetensors`, 12 s clip cap,
02:00 daily cron, macOS notifications.

## 3. ComfyUI (the big install — manual)

1. Install ComfyUI + venv, run in API mode:
   ```bash
   git clone https://github.com/comfyanonymous/ComfyUI ~/ComfyUI && cd ~/ComfyUI
   python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
   ./venv/bin/python main.py --listen 0.0.0.0 --port 8188
   ```
2. Install custom nodes via ComfyUI Manager: `comfyui_controlnet_aux` (DWPose),
   a SAM/segmentation pack, Wan 2.2 video nodes, KJNodes, VideoHelperSuite.
3. Download models (~50–100 GB) into `~/llms/image-models/comfyui/` and point
   ComfyUI at them via `extra_model_paths.yaml`
   (see [docs/automated-avatar-pipeline-n8n.md](docs/automated-avatar-pipeline-n8n.md) §4c):
   FLUX.1-schnell / SDXL / RealVisXL, **Wan 2.2 Animate**, ControlNet
   (DWPose + Depth), SAM. No InsightFace/PuLID/InstantID — identity comes from
   **your LoRA** (commercial-clean rule, PRODUCT-SPEC §5.2).
4. **Export the two API workflows** — build them in the UI, insert the
   `__TOKEN__` placeholders, then *Export (API)* over
   `comfyui/avatar_into_frame.api.json` and `comfyui/wan_animate.api.json`.
   Full instructions: [comfyui/README.md](comfyui/README.md).
   *The pipeline refuses to run until these exports replace the shipped
   placeholders.*

## 4. Operator assets (manual)

- **Character LoRA** → drop the trained `.safetensors` at
  `paths.lora_path` (train once on a rented GPU; see docs/SETUP.md §3b).
- **Avatar reference image** → save as `assets/avatar_reference.png`
  (instructions in `assets/avatar_reference.png.placeholder`).

## 5. Run the local services

```bash
# ComfyUI (terminal 1)
cd ~/ComfyUI && ./venv/bin/python main.py --listen 0.0.0.0 --port 8188

# DINOv2 identity gate (terminal 2) — first start downloads the model, then offline
cd /Users/jramirez/Git/avatar-pipeline && ./venv/bin/python scripts/face_gate.py
# health check: curl http://localhost:8189/health

# Web dashboard (terminal 3, optional) — mission-control UI on 127.0.0.1:8190
./venv/bin/python scripts/dashboard.py --config config.yaml
# health check: curl http://localhost:8190/api/health
```

### 5b. Web dashboard (optional but recommended)

A single-page dark UI with full Telegram parity — submit links, approve/
reject both gates with images inline, toggle providers (local ComfyUI ↔
WaveSpeed), plus queue status, service health, log tails, WaveSpeed balance,
Tailscale status, RunPod pods, and an identity-gate cosine history chart.
Design doc: `docs/WEB-UI-PLAN.md`.

```bash
# Run as a launchd service instead of a terminal (mirrors the gate service):
cp ops/launchd/com.jramirez.avatar.dashboard.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.jramirez.avatar.dashboard.plist
# logs: ~/Library/Logs/avatar-dashboard.log

# Reach it from your phone/laptop anywhere via your PRIVATE tailnet:
tailscale serve --bg 8190
# NEVER use `tailscale funnel` for this port — the dashboard must stay
# tailnet-only. The UI shows a red warning if 8190 ever appears funneled.
```

Environment knobs (all optional, via `.env` or exported):
- `DASHBOARD_PORT` — override the port (default 8190, also settable in
  `config.yaml`'s `dashboard.port`).
- `RUNPOD_API_KEY` — enables the RunPod pods panel (shows pod status +
  $/hr so a forgotten GPU rental is visible); panel degrades to a
  "not configured" note without it.

Security posture: binds 127.0.0.1 only; mutating requests (POST/PUT/DELETE)
are rejected unless the Host header is localhost or a `*.ts.net` name; media
routes only serve files under `work_dir`/`out_dir` with an extension
allowlist; there is deliberately no endpoint that bypasses the two human
approval gates.

## 6. n8n + workflow import

Run n8n **on the host** (not Docker) so its Execute Command nodes can reach
this repo's venv, ffmpeg, and the iCloud folder directly:

```bash
# n8n's isolated-vm dependency won't compile on the newest Node (v26) —
# install under Node 22 LTS via nvm:
nvm install 22
nvm exec 22 npm install -g n8n
nvm exec 22 n8n        # opens http://localhost:5678
```

Import: **n8n UI → Workflows → ⋯ → Import from File →
`n8n/workflow.json`** → open it → toggle **Active**. The Schedule node fires
daily at 02:00 (`schedule.cron` in config.yaml is the same value — change
both if you reschedule). The workflow has a 12 h execution timeout.

- The Execute Command nodes use the absolute repo path
  `/Users/jramirez/Git/avatar-pipeline` — edit them if the repo moves.
- Email notifications: set `notifications.provider: email`, then add an n8n
  **Send Email** node (with your SMTP credentials) after the `Published?`
  true-branch. The `macos` provider needs no setup.
- Docker n8n is possible but not recommended here: you'd have to mount the
  repo, `/work`, `/out`, and the iCloud folder, and shell steps would run
  inside the container without ffmpeg/ComfyUI — stick with the host install.

## 7. Verify, dry-run, first real run

```bash
./scripts/setup_check.sh                                  # doctor: binaries, venv, services

# dry-run: prints the full plan + every remaining setup gap, touches nothing
./venv/bin/python scripts/worker.py \
  --url "https://www.tiktok.com/@you/video/<id>" --config config.yaml --dry-run

# manual end-to-end on one link (what n8n runs nightly)
./venv/bin/python scripts/pick_next.py --config config.yaml
./venv/bin/python scripts/worker.py --id <id> --url "<url>" --config config.yaml

./venv/bin/python -m pytest tests/ -q                     # unit/smoke tests
```

Output lands in `out/<YYYY-MM-DD>/<id>.mp4`; audit trail in `done.csv`;
per-run log in `work/<id>/run.log`.

## 8. Enabling the WaveSpeed animation provider (optional, opt-in)

WaveSpeed offloads **only FR-5 (animation)** — everything else always stays
local. `config.yaml` is currently configured to use it
(`animation.provider: "wavespeed"`, `wavespeed.enabled: true`, model
`kwaivgi/kling-v3.0-pro/motion-control` — Kling 3.0 Pro Motion Control,
verified 2026-07-03 against
https://wavespeed.ai/models/kwaivgi/kling-v3.0-pro/motion-control). The only
remaining switch:

- **Environment (never in config): `export WAVESPEED_API_KEY=<key>`** — get a
  key at https://wavespeed.ai. For scheduled n8n runs, add it to
  `EnvironmentVariables` in `ops/launchd/com.jramirez.avatar.n8n.plist` and
  reload the service (`launchctl kickstart -k
  gui/$(id -u)/com.jramirez.avatar.n8n`), since a plain shell `export` doesn't
  propagate to an already-running launchd service.

How it works: the provider uploads the avatar frame + reference video to
WaveSpeed's own media host (`POST /api/v3/media/upload/binary` — files auto-
delete after 7 days), submits the Kling Motion Control job with those URLs,
polls for completion, and downloads the result. `wavespeed.character_orientation:
"image"` (the current setting) caps the reference video at **10 seconds**
— matches `video.max_clip_seconds: 10` in config.yaml; don't raise one
without the other. Switching to `character_orientation: "video"` allows up to
30s but changes how the model frames the output (orients to the video
instead of the avatar image) — verify visually before relying on it.

If the cloud call fails and `fallback_to_local_on_cloud_error: true` (the
default), the worker automatically falls back to local ComfyUI for that run
— which still requires the local FR-5 workflow/models to be in place. With
`enabled: false` WaveSpeed is **never** contacted — selecting `provider:
wavespeed` without enabling it is a hard error, not a silent call.

To switch back to fully local/offline: set `animation.provider:
"local_comfyui"` in config.yaml (`wavespeed.enabled` can stay `true`, it's
simply not used unless selected).

## 9. Day-to-day operation & failure handling

- **You do two things:** paste TikTok links (your own public posts) into
  column A of the `.numbers` sheet; review `out/<date>/` each morning.
- A URL is processed **at most once** (`processed.json`). The `.numbers` file
  is never written to.
- **Flagged URLs** (bad download, render failure, identity gate below
  `identity.cosine_min` after `max_retries` re-renders) are recorded under
  `"flagged"` in `processed.json` + a `flagged:*` row in `done.csv` +
  `work/<id>/FLAGGED.txt`, and are skipped forever — one bad link never blocks
  the queue. To retry one deliberately, remove its id from the `"flagged"`
  list.
- **Infrastructure errors** (ComfyUI/gate down, workflow not exported, tool
  missing) do **not** consume the URL — the same link retries on the next
  scheduled run.
