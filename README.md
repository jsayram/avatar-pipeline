# avatar-pipeline

Fully-local, identity-locked **synthetic avatar** image & video pipeline, orchestrated by
**n8n** and driven by **ComfyUI + Wan 2.2 Animate**. Built for **MacBook Pro M1 Max, 64 GB**.

You add TikTok links to an iCloud `.numbers` sheet; a daily cron produces a video of your
synthetic avatar performing the reference motion (full face+body identity), strips metadata,
and drops it in an output folder for review.

## Docs
| File | What |
|------|------|
| [SETUP.md](SETUP.md) | **Start here to run it** — install, configure, services, n8n import, dry-run, WaveSpeed opt-in |
| [docs/WEB-UI-PLAN.md](docs/WEB-UI-PLAN.md) | Web dashboard (mission-control UI on :8190) — design doc; run/install steps in SETUP.md §5b |
| [comfyui/README.md](comfyui/README.md) | How to export the two ComfyUI API workflows (required before first run) |
| [docs/PRODUCT-SPEC.md](docs/PRODUCT-SPEC.md) | Authoritative product spec, requirements, contracts, milestones |
| [docs/accii-diagram-avatar-pipe.md](docs/accii-diagram-avatar-pipe.md) | **Start here** — ASCII diagram of the whole flow |
| [docs/SETUP.md](docs/SETUP.md) | **Install + gap checklist** — what's installed, what's missing, prerequisites, status checklist |
| [docs/avatar-video-from-tiktok-pipeline.md](docs/avatar-video-from-tiktok-pipeline.md) | The TikTok → avatar-video pipeline (daily cron, .numbers input) |
| [docs/automated-avatar-pipeline-n8n.md](docs/automated-avatar-pipeline-n8n.md) | Core build guide — n8n + ComfyUI + face-lock gate |
| [docs/mature-optimization.md](docs/mature-optimization.md) | Adult-content quality + commercial-license notes |

> General model/licensing reference (not avatar-specific) lives outside this repo at
> `~/llms/consider-upgrade-models.md`.

## Stack (all free / commercial-clean)
n8n (self-host) · ComfyUI · Wan 2.2 Animate (Apache) · FLUX-schnell / SDXL / RealVisXL ·
**character LoRA (your asset)** · DINOv2 face-gate (Apache) · yt-dlp · ffmpeg · exiftool ·
numbers-parser. Identity via your own LoRA — **no InsightFace** — keeps it commercial-clean.

## Quickstart
The code is built; see [SETUP.md](SETUP.md) for the full walkthrough. Short version:
```bash
brew install ffmpeg yt-dlp exiftool
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml            # edit paths/thresholds
./scripts/setup_check.sh                      # doctor
./venv/bin/python scripts/worker.py --url "<tiktok-url>" --config config.yaml --dry-run
```
Still manual before the first real run: train + place the **character LoRA**,
supply `assets/avatar_reference.png`, install ComfyUI + models, and export the
two API workflows ([comfyui/README.md](comfyui/README.md)).

## Layout
```
avatar-pipeline/
├── docs/                 # the guides above
├── scripts/
│   ├── pick_next.py      # FR-1: .numbers reader + processed.json de-dupe
│   ├── worker.py         # FR-2..8: download→frame→avatar→animate→gate→strip→publish
│   ├── face_gate.py      # FR-6: DINOv2 identity gate (FastAPI :8189)
│   ├── notify.py         # success notification (macOS, config-gated)
│   ├── setup_check.sh    # doctor script — checks deps/services, installs nothing
│   └── lib/              # config, state, media, comfyui, animation_providers, logging
├── comfyui/              # API workflow templates + export instructions (README.md)
├── n8n/workflow.json     # FR-9: importable daily schedule → pick → worker → notify
├── assets/               # avatar_reference.png (operator-supplied; gate reference)
├── tests/                # 56 unit/smoke tests (pytest)
├── config.example.yaml   # every path/threshold/endpoint/provider knob
├── work/                 # scratch: downloads, frames, run logs       (gitignored)
└── out/                  # finished avatar videos, dated folders      (gitignored)
```

The animation step (FR-5, the slow one) is pluggable: `local_comfyui`
(default, offline) or `wavespeed` (cloud, **opt-in only** — never called
unless `wavespeed.enabled: true`; see SETUP.md §8). Everything else always
runs locally.

## Boundaries
Synthetic adults only; no minors (illegal even when synthetic); no non-consensual real-person
likeness; AI-disclosure / platform / payment compliance is the operator's responsibility.
