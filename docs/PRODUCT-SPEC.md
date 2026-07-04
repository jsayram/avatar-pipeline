# Product Specification & Build Requirements — Avatar Pipeline

> **Audience:** an AI build agent (e.g. Claude Code) that will implement this system end-to-end.
> **Authoritative source.** Where this conflicts with other docs, this wins. Supporting detail:
> `automated-avatar-pipeline-n8n.md`, `avatar-video-from-tiktok-pipeline.md`,
> `mature-optimization.md`, `accii-diagram-avatar-pipe.md`, `SETUP.md`.
> Keywords MUST / SHOULD / MUST NOT are normative (RFC-2119 sense).

---

## 1. Product summary
A **fully-local, commercial-clean, automated** pipeline that converts a reference TikTok video
into a video of a **synthetic, identity-locked avatar** performing the same motion. Runs on a
**daily cron** with **no operator intervention** beyond (a) adding links to a sheet and (b)
reviewing the day's output. Target host: **MacBook Pro M1 Max, 64 GB, macOS (Apple Silicon/MPS).**

## 2. Goal & definition of success
- Operator adds TikTok URLs to an **iCloud `.numbers`** sheet.
- Once per day, the system picks the next unprocessed URL and produces **one** video of the
  operator's avatar reproducing that reference's full-body + facial motion.
- **Identity lock:** a reviewer perceives the *same* avatar across all outputs; any frame-set
  scoring below the configured identity threshold MUST NOT be published (auto-retry or flag).
- **Local & free:** all processing on-device; every dependency is commercially licensable
  (Apache/BSD/MIT) or an operator-owned asset.
- Output is **metadata-stripped** and dropped in a dated folder for review.

## 3. Actors
| Actor | Responsibility |
|-------|----------------|
| **Operator** (human) | Designs the avatar; trains/supplies the LoRA; adds links; reviews output. 2 touch points at runtime. |
| **Build agent** (AI) | Implements every Deliverable in §11 to satisfy §8 functional requirements. |

## 4. Scope
**In scope:** input reader, downloader, frame extraction, avatar-into-frame generation, motion
animation, identity gate, metadata strip, publish, orchestration, config, logging, tests.
**Out of scope (operator-owned):** creative design of the avatar; **LoRA training** (operator
supplies the trained `.safetensors`); legal/disclosure/platform compliance; any non-synthetic or
real-person content.

## 5. Hard constraints (MUST)
1. **Hardware/OS:** Apple Silicon M1 Max, 64 GB, macOS; compute via **MPS** (no CUDA).
2. **Commercial-clean licensing — MUST NOT use:** InsightFace pretrained packs (antelopev2/buffalo),
   **PuLID / InstantID / IP-Adapter-FaceID**, **FLUX.1-dev** (for commercial output). Identity MUST
   come from the **operator's character LoRA**. Face-gate MUST use **DINOv2** (Apache) or OpenCV SFace.
3. **Local-only:** no user data leaves the machine. Cloud GPU offload of a single heavy step is
   permitted ONLY if explicitly enabled in config and the operator opts in.
4. **Legal/content (non-negotiable):** synthetic **adults only**; MUST refuse/skip anything that
   would depict minors (illegal even when synthetic) or non-consensual real-person likeness;
   reference videos are the **operator's own public** posts. The build MUST NOT add capability that
   circumvents these.
5. **Idempotency:** a URL MUST be processed at most once (dedupe via `processed.json`).
6. **`.numbers` is read-only input** — the system MUST NOT write back to it (state lives in sidecar files).

## 6. Tech stack (pinned roles + licenses)
| Layer | Tool | License |
|-------|------|---------|
| Orchestrator | **n8n** (self-host, Docker) | fair-code (self-host OK) |
| Runtime | **ComfyUI** (API mode) | GPL-3.0 (outputs are operator's) |
| Image base | **FLUX.1-schnell** / **Qwen-Image** / SDXL / RealVisXL | Apache 2.0 / OpenRAIL |
| Identity | **Character LoRA** (operator asset) | operator-owned |
| Segmentation/pose | **SAM**, **DWPose/RTMPose** (controlnet_aux) | Apache |
| Animation | **Wan 2.2 Animate** | Apache 2.0 |
| Identity gate | **DINOv2** (`facebook/dinov2-base`) | Apache 2.0 |
| Upscale | **Real-ESRGAN** | BSD-3 |
| Download | **yt-dlp** | open |
| Media/metadata | **ffmpeg**, **exiftool** | open |
| Sheet reader | **numbers-parser** (Python) | open |
| Prompt assist (opt) | **Ollama** (qwen3-vl / qwen3) | Apache |

## 7. Architecture (8-stage flow)
See `accii-diagram-avatar-pipe.md`. Stages: **1** pick link → **2** download → **3** first frame →
**4** avatar-into-frame (ComfyUI: SAM + DWPose/Depth + LoRA inpaint) → **5** animate (Wan 2.2
Animate) → **6** identity gate (DINOv2) → **7** strip metadata → **8** publish + update state.

---

## 8. Functional requirements (with I/O contracts)

**FR-1 — Input reader (`pick_next`)**
- MUST read the iCloud-synced `.numbers` via `numbers-parser`; if the file is an iCloud
  placeholder, MUST trigger `brctl download` and wait.
- MUST return the **first URL not present in `processed.json`**; exit cleanly (no-op) if none.
- Output: `{ "id": "<tiktok_id>", "url": "<url>" }`.

**FR-2 — Downloader** — `yt-dlp` → `work/<id>/ref.mp4`. MUST fail the run gracefully (log + flag) on download error; MUST NOT mark the URL processed on failure.

**FR-3 — First frame** — `ffmpeg -vframes 1` → `work/<id>/frame1.png`.

**FR-4 — Avatar-into-frame (ComfyUI)** — Input: `frame1.png` + `config.lora_path`. Workflow MUST:
SAM-mask the person → DWPose (+ optional Depth) ControlNet from `frame1.png` → inpaint the masked
region using the **character LoRA** + base model. Output: `work/<id>/avatar_frame1.png` showing the
avatar in the original scene/pose. Identity derives from the LoRA.

**FR-5 — Animator (Wan 2.2 Animate)** — Input: `avatar_frame1.png` + `ref.mp4`. Output:
`work/<id>/avatar_raw.mp4`. MUST respect dims = multiple of 16 and `config.max_clip_seconds`.

**FR-6 — Identity gate (DINOv2 service)** — Sample N frames (`ffmpeg fps=1`); for each, POST to the
gate vs `config.avatar_reference`; compute **mean cosine**. PASS if `mean ≥ config.identity_cosine_min`.
On FAIL: retry FR-5 up to `config.max_retries` (vary seed/settings); if still failing, **flag** the
row (no publish). Gate contract: `POST /compare {ref, gen} → {cosine: float, pass: bool}`.

**FR-7 — Metadata stripper** — `ffmpeg -map_metadata -1 -map_chapters -1 -c copy` then
`exiftool -all=` → `work/<id>/avatar_video.mp4`. Output MUST contain no source/tool metadata.

**FR-8 — Publisher** — Move final mp4 → `out/<YYYY-MM-DD>/<id>.mp4`; append a row to `done.csv`;
add `<id>` to `processed.json`. Writes MUST be atomic (temp + rename).

**FR-9 — Orchestrator (n8n)** — Daily `Schedule` trigger chains FR-1…FR-8; MUST stop early if FR-1
returns none; MUST isolate failures to the single URL (one bad link never blocks future days);
SHOULD emit a local notification "today's avatar video is ready" on success.

---

## 9. Data contracts

**`.numbers` (input, read-only)** — Sheet 1, Table 1:
| col A | col B (optional) |
|-------|------------------|
| TikTok URL | note/label |

**`processed.json`** — `{"processed": ["<id>", ...]}`
**`done.csv`** — `date,id,url,output_path,identity_cosine,status`
**`config.yaml`** (example):
```yaml
paths:
  numbers_sheet: "/Users/jramirez/Library/Mobile Documents/com~apple~CloudDocs/links.numbers"
  work_dir: "./work"
  out_dir: "./out"
  lora_path: "/Users/jramirez/llms/image-models/comfyui/loras/avatar_v1.safetensors"
  avatar_reference: "./assets/avatar_reference.png"   # canonical identity image for the gate
endpoints:
  comfyui_url: "http://localhost:8188"
  gate_url: "http://localhost:8189"
  ollama_url: "http://localhost:11434"
identity:
  cosine_min: 0.88          # DINOv2 threshold; tune 0.82 loose … 0.92 strict
  sample_fps: 1
  max_retries: 2
video:
  base_model: "flux1-schnell"   # commercial-clean default
  wan_model: "wan2.2-animate"
  max_clip_seconds: 8           # cap to fit M1 Max render budget
  cloud_offload: false          # if true, run FR-5 on configured remote GPU
schedule:
  cron: "0 2 * * *"             # daily 02:00
```

**ComfyUI API** — `POST /prompt {prompt:<workflow_json>, client_id}` → `{prompt_id}`;
poll `GET /history/{prompt_id}`; fetch `GET /view?filename=&subfolder=&type=output`.
Workflow JSONs are authored in the ComfyUI UI and exported via **Export (API)**.

---

## 10. Non-functional requirements
- **Performance (M1 Max realism):** FR-1/2/3/6/7/8 = seconds; FR-4 ≈ 0.5–2 min; **FR-5 dominates
  (minutes per ~5 s)** — the cron's 1/day cadence absorbs this; long clips MUST be capped
  (`max_clip_seconds`) or offloaded.
- **Reliability:** idempotent, resumable (re-running a failed `<id>` is safe), atomic writes, never
  corrupt the `.numbers` (read-only).
- **Observability:** per-run log file under `work/<id>/run.log` + n8n run history; `done.csv` is the
  audit trail (includes the identity score).
- **Privacy:** local-only by default; cloud offload gated behind explicit config.
- **Config-driven:** all paths/thresholds/models/endpoints in `config.yaml`; no hardcoding.

## 11. Deliverables (the build agent MUST produce)
```
avatar-pipeline/
├── scripts/
│   ├── pick_next.py          # FR-1
│   ├── face_gate.py          # FR-6 DINOv2 FastAPI service
│   ├── worker.py             # FR-2..FR-8 chain (CLI: --id / --url)
│   └── lib/ (helpers)
├── comfyui/
│   ├── avatar_into_frame.api.json   # FR-4 (exported workflow)
│   └── wan_animate.api.json         # FR-5 (exported workflow)
├── n8n/
│   └── workflow.json         # FR-9 (exported n8n flow)
├── config.example.yaml
├── requirements.txt
├── assets/avatar_reference.png  (operator-supplied placeholder)
└── tests/                    # smoke tests per FR
```

## 12. Milestones & acceptance criteria
| # | Milestone | Acceptance test |
|---|-----------|-----------------|
| M0 | Env install | `ffmpeg`, `yt-dlp`, `exiftool`, ComfyUI API, n8n all respond |
| M1 | `pick_next.py` | Given a sheet + `processed.json`, returns the correct next URL; no-op when exhausted |
| M2 | Skeleton chain | `worker.py --url X` does download→frame→strip→`out/` (no AI yet) on a real link |
| M3 | Avatar-into-frame | With operator LoRA, produces `avatar_frame1.png` of the avatar in frame1's pose |
| M4 | Animate | Wan step yields a playable `avatar_raw.mp4` ≤ `max_clip_seconds` |
| M5 | Identity gate | Gate returns cosine; below-threshold output triggers retry then flag (no publish) |
| M6 | Orchestrate | n8n daily schedule runs M1–M5 unattended; failure isolated to one URL; notify on success |
| M7 | E2E | One link in the sheet → next morning a metadata-clean avatar video in `out/<date>/`, link in `done.csv`, id in `processed.json` |

## 13. Assumptions & open decisions (use these defaults unless operator overrides)
- **Avatar + LoRA exist** before M3 (operator-supplied; training is out of scope — likely cloud GPU).
- Default base = **FLUX.1-schnell** (commercial-clean). `max_clip_seconds = 8`. `cloud_offload = false`.
- Notification = local macOS notification. Gate threshold default **0.88** (tune on real output).
- If a reference video is long, cap to `max_clip_seconds` from the start rather than full length.

## 14. Guardrails for the executing AI (restate)
The build agent MUST keep the §5.4 content boundaries intact, MUST NOT introduce InsightFace/PuLID/
InstantID or FLUX-dev into the commercial path, and MUST NOT add features whose purpose is to evade
the legal boundaries. Identity = operator's LoRA; gate = DINOv2/SFace; everything Apache/BSD/MIT/owned.
