# ASCII Diagram — Identity-Locked Avatar Video Pipeline (for review)

Full flow for `avatar-video-from-tiktok-pipeline.md`. Everything between the two "YOU" points
runs unattended on a daily cron.

```
╔═══════════════════════════════════════════════════════════════════════════╗
║      IDENTITY-LOCKED AVATAR VIDEO PIPELINE  —  100% local · daily cron      ║
╚═══════════════════════════════════════════════════════════════════════════╝

   YOU ▸ paste TikTok links                              YOU ▸ review & keep
          │                                                       ▲
          ▼                                                       │
   ┌──────────────────────┐                          ┌────────────────────────┐
   │  links.numbers       │                          │  /out/<YYYY-MM-DD>/     │
   │  (iCloud Drive)      │                          │   avatar_video.mp4      │
   └──────────┬───────────┘                          └────────────▲───────────┘
              │ read · numbers-parser                             │
              ▼                                                   │
   ┌──────────────────────────────────────────────────────────────┴────────┐
   │                 n8n  ·  Schedule (cron, 1×/day)  ·  orchestrator         │
   └──────────────────────────────────────────────────────────────┬────────┘
                                                                   │
  STEP 1  ┌────────────────────────────────────┐                  │
  pick    │ pick_next.py                       │  ◀── processed.json (de-dupe)
  link    │ → next unprocessed TikTok URL      │                  │
          └───────────────┬────────────────────┘                  │
                          ▼                                        │
  STEP 2  ┌────────────────────────────────────┐                  │
 download │ yt-dlp <url>            → ref.mp4   │                  │
          └───────────────┬────────────────────┘                  │
                          ▼                                        │
  STEP 3  ┌────────────────────────────────────┐                  │
 frame 1  │ ffmpeg -vframes 1      → frame1.png │                  │
          └───────────────┬────────────────────┘                  │
                          ▼                                        │
  STEP 4  ┌─────────────────────────────────────────────┐   ┌──────────────┐
 avatar   │ ComfyUI · avatar INTO the scene (ID lock)    │◀──│ character    │
  into    │  SAM mask ─▶ DWPose/Depth ControlNet ─▶      │   │ LoRA (yours) │
 frame    │  inpaint w/ LoRA      → avatar_frame1.png    │   └──────────────┘
          └───────────────┬─────────────────────────────┘
                          ▼
  STEP 5  ┌─────────────────────────────────────────────┐
 animate  │ ⏳ Wan 2.2 Animate   (slowest step)          │◀── ref.mp4 (motion source)
   to     │  avatar_frame1.png + ref video              │
 motion   │                      → avatar_raw.mp4        │
          └───────────────┬─────────────────────────────┘
                          ▼
  STEP 6  ┌─────────────────────────────────────────────┐
 identity │ DINOv2 face-gate · sample frames            │
  gate    │  avg cosine vs avatar reference?            │
          └───────┬─────────────────────────┬───────────┘
                  │ PASS (≥ threshold)      │ FAIL (< threshold)
                  │                         └────────▶ retry STEP 5  /  flag row
                  ▼
  STEP 7  ┌─────────────────────────────────────────────┐
 strip    │ ffmpeg -map_metadata -1  ·  exiftool -all=   │
 metadata │                      → avatar_video.mp4      │
          └───────────────┬─────────────────────────────┘
                          ▼
  STEP 8  ┌─────────────────────────────────────────────┐
 publish  │ mv → /out/<date>/                           │
  + log   │ append done.csv  ·  add id → processed.json  │ ──▶ (output ready)
          └─────────────────────────────────────────────┘
```

---

### Legend
```
 ▸ manual (you)        ⏳ slow on M1 Max (minutes per ~5s of video)
 ▼ automated flow      ◀── side input        ──▶ side output / log
```

### Tools per step (all open / free-commercial)
| Step | Tool | License |
|------|------|---------|
| orchestrate | n8n (self-host) | fair-code ✅ |
| 1 read sheet | numbers-parser | open ✅ |
| 2 download | yt-dlp | open ✅ |
| 3 / 7 frames+strip | ffmpeg · exiftool | open ✅ |
| 4 avatar swap | ComfyUI · SAM · DWPose · **your LoRA** | open / yours ✅ |
| 5 animate | Wan 2.2 Animate | Apache ✅ |
| 6 gate | DINOv2 | Apache ✅ |

### Notes for review
- **Manual = 2 touch points only:** paste links in `.numbers`, review the daily output.
- **STEP 4 needs your trained character LoRA** — that's the prerequisite asset for identity lock.
- **STEP 5 is the bottleneck** on M1 Max; long videos may exceed a day → cap length or offload to cloud GPU.
- **STEP 6 retry loop** is what makes "100% identity lock" real: nothing below your similarity bar ships.
- `.numbers` is **input-only**; state lives in `processed.json` + `done.csv` (safe, no write-back risk).
