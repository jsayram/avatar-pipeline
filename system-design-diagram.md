# avatar-pipeline — System Design (as of 2026-07-03)

> Snapshot of the current architecture. This file is disposable — delete and
> regenerate it whenever the system changes meaningfully; it is not meant to
> stay in sync automatically. Canonical detail/history lives in
> `HANDOFF.md`; this file is purely a visual reference.

## Two entry points, one pipeline

```
  [A] DAILY SCHEDULE (n8n cron)              [B] TEXT A LINK TO TELEGRAM
  ─────────────────────────────              ───────────────────────────
  n8n workflow "AvatarPipeDaily1"            Operator sends a TikTok URL
  cron: 0 12 * * *  (noon, daily)            to the bot at any time
        │                                            │
        ▼                                            ▼
  scripts/pick_next.py                       n8n workflow
  reads links.numbers                        "AvatarPipeTelegramReply1"
  (iCloud, READ-ONLY —                       (Telegram Trigger, webhook)
   PRODUCT-SPEC §5 #6,                              │
   never written to)                                ▼
  → next un-seen row                         scripts/handle_telegram_reply.py
        │                                    classifies the message text:
        │                                    is it a TikTok URL? → yes:
        │                                      appends URL + timestamp to
        │                                      linksThroughTelegram.numbers
        │                                      (iCloud, append-only "keep"
        │                                       log — NOT read by pick_next,
        │                                       purely a record)
        │                                            │
        └──────────────────────┬─────────────────────┘
                                ▼
                  scripts/worker.py --phase prepare
                  FR-2 download (yt-dlp) + FR-3 extract
                  frame 1 (ffmpeg)  — both free, no API cost
                                │
                                ▼
                  saves work/<id>/pending_approval.json
                  {stage: "frame", ...}
                                │
                                ▼
                  sends the RAW frame to Telegram
                  "no cost spent yet — reply yes/no"
```

## The two-gate approval flow (after `prepare`)

```
                    ┌─────────────────────────────┐
                    │  GATE 1 — Telegram: raw      │
                    │  extracted frame, no cost    │
                    │  spent yet                   │
                    └───────────────┬──────────────┘
                          operator replies via Telegram
                     ┌────────────── │ ──────────────┐
                    "no"                            "yes"
                     │                                │
                     ▼                                ▼
        --phase reject_frame              --phase generate_avatar
        flag + skip (no seed to           FR-4: WaveSpeed Seedream
        retry — the raw frame is          4.5 Edit generates the
        a fixed extraction)               avatar-styled still
        → processed.json["flagged"]                 │
        → done.csv row                              ▼
        → Telegram text notice           saves work/<id>/pending_approval.json
                                          {stage: "avatar", ...}
                                                     │
                                                     ▼
                                         sends BOTH the original frame
                                         AND the generated avatar still
                                         to Telegram, side by side
                                         (for identity-fidelity comparison)
                                                     │
                                       ┌─────────────┴──────────────┐
                                       │  GATE 2 — Telegram: avatar  │
                                       │  still ready, reply yes/no  │
                                       └─────────────┬───────────────┘
                                       ┌────────────  │  ─────────────┐
                                     "no"                           "yes"
                                       │                               │
                                       ▼                               ▼
                          --phase regenerate                --phase animate
                          redo FR-4 with a new               FR-5: WaveSpeed Kling
                          seed, re-send GATE 2                3.0 Pro Motion Control
                          (up to telegram.                    animates the approved
                          max_approval_attempts,               still using the
                          currently 3) then                    reference video's motion
                          gives up + flags                              │
                                                                          ▼
                                                          save approved still →
                                                          out-pipe/image-out/
                                                          <id>-image-<ts>.png
                                                                          │
                                                                          ▼
                                                          FR-6: identity gate check
                                                          (local DINOv2 service,
                                                          :8189) — sample frames,
                                                          cosine similarity vs.
                                                          assets/avatar_reference.png
                                                          identity.max_retries = 0
                                                          → exactly ONE attempt
                                             ┌────────────────────┴───────────────────┐
                                          FAIL (< 0.88)                         PASS (>= 0.88)
                                             │                                          │
                                             ▼                                          ▼
                              flag + save failed video to               FR-7: strip metadata
                              out-pipe/video-out/                       (ffmpeg -map_metadata -1,
                              <id>-video-<ts>.mp4                       exiftool -all=)
                              (kept — review whether the                          │
                              gate is too strict)                                  ▼
                                             │                          FR-8: publish to
                                             │                          out-pipe/video-out/
                                             │                          <id>-video-<ts>.mp4
                                             │                                    │
                                             └─────────────┬──────────────────────┘
                                                           ▼
                                          update processed.json + done.csv
                                          send the ACTUAL VIDEO FILE back via
                                          Telegram (published OR flagged —
                                          either way, you get the video)
                                                           │
                                                           ▼
                                                       END OF RUN
```

## Directory map

```
~/Library/Mobile Documents/com~apple~CloudDocs/SocialAvatar/Pipeline/   (iCloud — syncs to phone/other devices)
├── into-pipe/
│   ├── links.numbers                 INPUT, read-only, hand-edited by operator
│   ├── links_status.csv              read-glance status companion (auto-regenerated
│   │                                 every state change — published/flagged/pending/
│   │                                 not-yet-processed per link; never edits links.numbers)
│   └── linksThroughTelegram.numbers  append-only "keep" log of every link texted
│                                     into the bot (NOT read by pick_next.py)
│
└── out-pipe/
    ├── image-out/
    │   └── <id>-image-<timestamp>.png     the ONE avatar still approved per run
    └── video-out/
        └── <id>-video-<timestamp>.mp4     every animation attempt, PASS or FAIL
                                            (flat dir, nothing ever deleted)

/Users/jramirez/Git/avatar-pipeline/   (repo — local, git-ignored working state)
├── config.yaml                   live config (secrets NEVER live here — see .env)
├── config.example.yaml           safe/local template for new setups
├── .env                          WAVESPEED_API_KEY, TELEGRAM_BOT_TOKEN (gitignored)
├── processed.json                {"processed": [...ids], "flagged": [...ids]}
│                                  — permanent per-id outcome; flagged ids never
│                                  block the queue again (retry = manually remove)
├── done.csv                       audit trail: date,id,url,output_path,
│                                  identity_cosine,status — one row per attempt
│
├── work/<id>/                     per-run scratch space (local only, not iCloud)
│   ├── ref.mp4                    downloaded + trimmed source clip
│   ├── frame1.png                 extracted first frame
│   ├── avatar_frame1.png          Seedream-generated still (current attempt)
│   ├── avatar_raw_attempt1.mp4    raw animation output before strip/publish
│   ├── gate_frames_attempt1/      frames sampled from the animation for the gate
│   ├── pending_approval.json      {"stage": "frame"|"avatar", ...} — cleared once
│   │                              the gate resolves; this is the ONLY place
│   │                              "what are we waiting on" state lives
│   ├── FLAGGED.txt                written on any flag (stage + reason)
│   └── run.log                    full log for this one id
│
├── scripts/
│   ├── pick_next.py               FR-1: next un-seen links.numbers row
│   ├── worker.py                  FR-2..FR-8, phase-dispatched (see below)
│   ├── handle_telegram_reply.py   classifies incoming Telegram text (yes/no/
│   │                              link/other), dispatches to worker.py phases
│   ├── face_gate.py               DINOv2 identity-gate HTTP service (:8189)
│   ├── notify.py                  macOS/email notifications (independent of Telegram)
│   └── lib/
│       ├── config.py               typed config loader
│       ├── state.py                processed.json / done.csv / tiktok-id extraction
│       ├── media.py                yt-dlp / ffmpeg / exiftool command builders
│       ├── pending.py              pending_approval.json read/write (stage-aware)
│       ├── avatar_frame_providers.py   FR-4: local_comfyui | wavespeed_seedream | mock
│       ├── animation_providers.py      FR-5: local_comfyui | wavespeed | mock
│       ├── comfyui.py              local ComfyUI API client (fallback path)
│       ├── telegram_notify.py      send_photo / send_message / send_video
│       ├── telegram_links_archive.py   append_link() → linksThroughTelegram.numbers
│       ├── status_sheet.py         build/write links_status.csv
│       ├── output_naming.py        <id>-{image,video}-<timestamp> naming
│       └── logging_utils.py        per-id run.log + stderr logging
│
├── n8n/
│   ├── workflow.json                    "AvatarPipeDaily1" — cron → pick_next →
│   │                                    worker.py --phase prepare
│   └── workflow_telegram_reply.json     "AvatarPipeTelegramReply1" — Telegram
│                                        Trigger (webhook) → handle_telegram_reply.py
│
├── ops/
│   ├── launchd/*.plist             n8n / ComfyUI / gate service definitions
│   ├── get_telegram_chat_id.py     one-off chat_id lookup helper
│   └── download_models.py          ComfyUI model-weight installer
│
├── assets/avatar_reference.png     canonical identity image the gate compares against
├── comfyui/*.api.json              exported ComfyUI workflow templates (local fallback)
└── tests/                          149 tests, pytest, all network/ffmpeg calls mocked
```

## Services & tools

```
┌─────────────────────┬──────────┬────────────────────────────────────────────┐
│ Service/Tool         │ Where    │ Role                                       │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ n8n                  │ :5678    │ Orchestrator — cron trigger + Telegram      │
│                      │ (local,  │ webhook trigger. launchd-managed            │
│                      │ tunneled)│ (com.jramirez.avatar.n8n)                   │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ Tailscale Funnel     │ public   │ Exposes n8n's webhook to the public         │
│                      │ HTTPS    │ internet so Telegram can reach it —         │
│                      │          │ https://<device>.<tailnet>.ts.net           │
│                      │          │ (n8n's WEBHOOK_URL env var points here)     │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ Telegram Bot API     │ cloud    │ Two-way: sendPhoto/sendVideo/sendMessage    │
│                      │          │ (approval requests, comparisons, final      │
│                      │          │ video) + Trigger webhook (yes/no/link       │
│                      │          │ replies come back in)                       │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ yt-dlp               │ CLI      │ FR-2: download the TikTok clip              │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ ffmpeg               │ CLI      │ FR-3 frame extraction, trimming, gate-frame  │
│                      │          │ sampling, FR-7 metadata stripping            │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ exiftool              │ CLI      │ FR-7: metadata stripping (belt + suspenders │
│                      │          │ alongside ffmpeg -map_metadata -1)          │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ WaveSpeed Seedream    │ cloud,   │ FR-4: still-image identity swap (PAID —     │
│ 4.5 Edit              │ paid API│ bytedance/seedream-v4.5/edit)                │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ WaveSpeed Kling 3.0   │ cloud,   │ FR-5: animation from the approved still     │
│ Pro Motion Control    │ paid API│ (PAID — kwaivgi/kling-v3.0-pro/motion-       │
│                      │          │ control), gated to exactly 1 attempt        │
│                      │          │ (identity.max_retries: 0)                    │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ DINOv2 (face_gate.py)│ :8189,   │ FR-6: local, FREE identity-similarity gate  │
│                      │ local    │ (cosine vs. assets/avatar_reference.png,     │
│                      │          │ threshold 0.88). launchd-managed             │
│                      │          │ (com.jramirez.avatar.gate)                   │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ ComfyUI               │ :8188,   │ Local FALLBACK for FR-4/FR-5 (provider:      │
│                      │ local    │ "local_comfyui") — not in the active path    │
│                      │          │ today (live config uses WaveSpeed for both), │
│                      │          │ kept warm via launchd                        │
│                      │          │ (com.jramirez.avatar.comfyui)                │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ "mock" provider mode  │ in-      │ FREE stand-ins for FR-4/FR-5 (watermarked   │
│                      │ process  │ frame copy / reference-video copy) — for     │
│                      │          │ testing the pipeline mechanism without       │
│                      │          │ spending real money. NEVER leave configured  │
│                      │          │ for real/scheduled runs.                     │
├─────────────────────┼──────────┼────────────────────────────────────────────┤
│ numbers_parser        │ Python   │ Reads links.numbers (read-only); writes      │
│                      │ library  │ linksThroughTelegram.numbers (the only       │
│                      │          │ .numbers file this pipeline ever writes to)  │
└─────────────────────┴──────────┴────────────────────────────────────────────┘
```

## State / "memory" — what's tracked where

```
┌───────────────────────────────────┬───────────┬──────────────────────────────┐
│ File                              │ Lifetime  │ Purpose                       │
├───────────────────────────────────┼───────────┼──────────────────────────────┤
│ processed.json                    │ permanent │ {"processed": [ids],          │
│                                    │           │  "flagged": [ids]} — the      │
│                                    │           │ single source of truth for    │
│                                    │           │ "has this id been handled."   │
│                                    │           │ Flagged ids are skipped       │
│                                    │           │ forever by pick_next.py       │
│                                    │           │ unless manually un-flagged.   │
├───────────────────────────────────┼───────────┼──────────────────────────────┤
│ done.csv                          │ permanent,│ Human-readable audit trail —  │
│                                    │ append-   │ one row per terminal outcome, │
│                                    │ only      │ includes the identity cosine  │
│                                    │           │ score for every attempt.      │
├───────────────────────────────────┼───────────┼──────────────────────────────┤
│ work/<id>/pending_approval.json   │ transient │ "What is this specific id     │
│                                    │           │ currently waiting on."        │
│                                    │           │ {"stage": "frame"|"avatar",   │
│                                    │           │  url, ref_video_path,         │
│                                    │           │  frame1_path,                 │
│                                    │           │  avatar_frame_path, attempt}  │
│                                    │           │ Created by prepare/           │
│                                    │           │ generate_avatar/regenerate,   │
│                                    │           │ deleted by animate/           │
│                                    │           │ reject_frame/flagging.        │
│                                    │           │ At most ONE exists at a time  │
│                                    │           │ — a new texted-in link is     │
│                                    │           │ declined if one's already     │
│                                    │           │ outstanding.                  │
├───────────────────────────────────┼───────────┼──────────────────────────────┤
│ links_status.csv                  │ regenerated│ Read-glance view joining      │
│                                    │ from      │ links.numbers + processed.json│
│                                    │ scratch   │ + done.csv + pending state —  │
│                                    │ every     │ NOT authoritative, purely for │
│                                    │ state     │ the operator to eyeball.      │
│                                    │ change    │                               │
├───────────────────────────────────┼───────────┼──────────────────────────────┤
│ linksThroughTelegram.numbers      │ permanent,│ Every link ever texted in —   │
│                                    │ append-   │ pure log, never consulted by  │
│                                    │ only      │ any code path, never read     │
│                                    │           │ back by the pipeline itself.  │
├───────────────────────────────────┼───────────┼──────────────────────────────┤
│ work/<id>/run.log                 │ permanent │ Full per-id execution log —   │
│                                    │           │ every command run, every      │
│                                    │           │ Telegram send, every gate     │
│                                    │           │ score.                        │
└───────────────────────────────────┴───────────┴──────────────────────────────┘
```

## Config surface (`config.yaml`)

```
paths:        numbers_sheet, work_dir, out_dir, processed_json, done_csv,
              lora_path, avatar_reference, workflows_dir, status_sheet*,
              image_out_dir*, video_out_dir*, telegram_links_archive*
              (* = optional, sensible defaults shown in comments)
endpoints:    comfyui_url (:8188), gate_url (:8189)
identity:     cosine_min (0.88), sample_fps (1), max_retries (0 — ONE attempt)
video:        base_model, wan_model, max_clip_seconds (10), seed
avatar_frame: provider (wavespeed_seedream | local_comfyui | mock),
              wavespeed_model, size, identity_references[], prompt
animation:    provider (wavespeed | local_comfyui | mock),
              fallback_to_local_on_cloud_error
wavespeed:    enabled, api_base, api_key_env, model, character_orientation,
              keep_original_sound, prompt, negative_prompt, timeouts
schedule:     cron ("0 12 * * *" — noon daily)
notifications:enabled, provider (macos)
telegram:     enabled, bot_token_env, chat_id, max_approval_attempts (3)
```

## Cost-consciousness, at a glance

```
FREE steps:  download (yt-dlp) → extract frame (ffmpeg) → GATE 1 (Telegram)
                                                                │
                                                          [operator "yes"]
                                                                │
PAID step:                                          Seedream generates still ($)
                                                                │
                                              GATE 2 (Telegram, free comparison)
                                                                │
                                                          [operator "yes"]
                                                                │
PAID step:                                    Kling animates, ONE attempt only ($)
                                                                │
FREE step:                                    DINOv2 identity gate (local, free)
                                                                │
                                               published or flagged — either way,
                                               the video reaches you via Telegram
```

Two paid API calls total per successful run, each gated behind an explicit
Telegram "yes" from the operator — this is the whole point of the two-gate
design.
