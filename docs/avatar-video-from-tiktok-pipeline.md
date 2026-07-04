# Scaffolding — Identity-Locked Avatar Video from a TikTok Reference

> Written 2026-06-27. Fully-automated daily pipeline on **M1 Max 64 GB**.
> You add TikTok links to a **.numbers sheet on iCloud**; the system makes a video of **your
> synthetic avatar performing the reference video's motion** (full face+body identity), strips
> metadata, and drops it in a folder for you to review. Builds on
> `automated-avatar-pipeline-n8n.md`, `nsfw-optimization.md`, `consider-upgrade-models.md`.

---

## 0. Your idea → concrete steps
```
[.numbers on iCloud: your TikTok links]
        │  (daily, pick next unprocessed link)
        ▼
1. Download reference video            → yt-dlp
2. Extract first frame                 → ffmpeg
3. Put YOUR avatar into that first     → ComfyUI: SAM mask person + DWPose/Depth ControlNet
   frame's scene/pose (identity lock)    + your character LoRA (inpaint)  → "avatar-in-scene" still
4. Animate it to the reference motion  → Wan 2.2 Animate (avatar still + reference video)
5. Identity gate (don't ship drift)    → DINOv2 face-sim on sampled frames → retry/flag
6. Strip ALL metadata                  → ffmpeg -map_metadata -1  (+ exiftool)
7. Save to output folder + mark link   → /out/<date>/, sheet/state → "done", de-dupe
        ▼
[You review the latest video, move keepers to your folder]
```
Manual effort = **add links + review output.** Everything between is automated by a daily cron.

---

## 1. Honest reality checks (read before building)
1. **Video is the slow part on an M1 Max.** Wan 2.2 Animate is 14B; each ~4.8 s segment is
   **minutes** to render. A short clip overnight is fine; a **full 30–60 s TikTok could take
   hours**. Your "one per day" cadence actually fits this well — but for long videos consider
   (a) capping clip length, or (b) a **hybrid**: render the Wan step on a rented GPU (RunPod) and
   keep everything else local. Memory (64 GB) is not the limit; GPU speed is.
2. **".numbers + iCloud write-back" is the finicky bit.** Reading a .numbers file headless works
   (`numbers-parser`); writing back to it cleanly is fragile. Recommended pattern (see §4): treat
   the sheet as **input-only**, track processed links in a **sidecar state file**, and log results
   to a `done.csv` + output folder. (Optional: write status back to .numbers with a backup.)
3. **"100% identity lock" = enforced, not guaranteed.** Identity comes from your **character LoRA**
   (baked into step 3) + Wan-Animate's face replication; the **DINOv2 gate** (step 5) samples frames
   and rejects/retries anything that drifts below threshold. So "100%" = "nothing below your bar ships."
4. **Legal:** using **your own public TikToks** as motion reference is fine; the avatar is
   synthetic; output is fully regenerated (no TikTok watermark survives). Adult-content lines from
   `nsfw-optimization.md` §0 still apply if relevant.

---

## 2. Components (all local / free-commercial)
| Step | Tool | Install |
|------|------|---------|
| Orchestrate (daily) | **n8n** (Schedule/cron node) | Docker |
| Read .numbers | **numbers-parser** (Python) | `pip install numbers-parser` |
| Download video | **yt-dlp** | `pip install yt-dlp` (or `brew install yt-dlp`) |
| Frames / metadata | **ffmpeg** (+ **exiftool**) | `brew install ffmpeg exiftool` |
| Avatar-into-frame | **ComfyUI**: SAM (segment) + ControlNet DWPose/Depth + your **character LoRA** | ComfyUI + nodes |
| Animate to motion | **Wan 2.2 Animate** | ComfyUI Wan nodes |
| Identity gate | **DINOv2** service (`face_gate.py`, Apache) | from pipeline doc §7 |

---

## 3. Step-by-step (with commands)

### Step 1 — Pick the next link (daily)
Read the iCloud-synced .numbers, take the first unprocessed link (see §4 for the reader).

### Step 2 — Download the reference
```bash
yt-dlp -o "/work/ref/%(id)s.%(ext)s" "<tiktok_url>"     # downloads your public video
```

### Step 3 — Extract the first frame
```bash
ffmpeg -y -i /work/ref/<id>.mp4 -vframes 1 /work/ref/<id>_frame1.png
```

### Step 4 — Put your avatar into that first frame (identity replica)
A ComfyUI workflow (call it via the API like the other doc):
- **SAM / segmentation** → mask the person in `frame1.png`.
- **ControlNet DWPose** (+ optional **Depth**) from `frame1.png` → preserves the pose & scene.
- **Inpaint** the masked region using your **character LoRA** (+ base model) → your avatar, in
  the same pose/lighting/scene. The LoRA is what locks identity.
- Output: `avatar_frame1.png`.
> **Simpler one-step alt:** **Wan 2.2 Animate "replacement" mode** can take your avatar image +
> the reference video and replace the performer directly — try this first; fall back to the
> 2-stage swap above when you need tighter control of the first frame.

### Step 5 — Animate to the reference motion
Wan 2.2 Animate workflow (ComfyUI API): inputs `avatar_frame1.png` + `/work/ref/<id>.mp4`
→ DWPose extracts the motion → avatar performs it → `avatar_video_raw.mp4`.
(Video dims must be multiples of 16; each "extend" block ≈ 77 frames ≈ 4.8 s.)

### Step 6 — Identity gate (sample frames)
```bash
ffmpeg -y -i avatar_video_raw.mp4 -vf "fps=1" /work/check/f_%03d.png   # 1 frame/sec
```
For each sampled frame, POST to the **DINOv2 gate** vs your avatar reference; if average cosine
< threshold → flag the row (and optionally re-run step 5 with different settings).

### Step 7 — Strip ALL metadata
```bash
ffmpeg -y -i avatar_video_raw.mp4 -map_metadata -1 -map_chapters -1 \
       -c:v copy -c:a copy /work/clean/avatar_video.mp4
exiftool -all= -overwrite_original /work/clean/avatar_video.mp4      # belt-and-suspenders
```
(The avatar video is fully generated, so there's no TikTok watermark to begin with — this just
clears tool/creation metadata.)

### Step 8 — Publish + mark done
- Move `avatar_video.mp4` → `/Users/jramirez/avatars/out/<YYYY-MM-DD>/`.
- Append the link to `done.csv` (date, link, output path, gate score), add its id to
  `processed.json` so it never reruns. (Optional: write "done" back into the .numbers — §4.)

---

## 4. The .numbers on iCloud (input handling)
**Where it lives (synced locally):**
`~/Library/Mobile Documents/com~apple~CloudDocs/<your_sheet>.numbers`

**iCloud gotcha:** if the file shows as a placeholder (`.<name>.numbers.icloud`), it isn't
downloaded yet. Force it:
```bash
brctl download "~/Library/Mobile Documents/com~apple~CloudDocs/<your_sheet>.numbers"
```

**Read it (Python):**
```python
from numbers_parser import Document
doc = Document("/Users/jramirez/Library/Mobile Documents/com~apple~CloudDocs/links.numbers")
rows = doc.sheets[0].tables[0].rows(values_only=True)
links = [r[0] for r in rows[1:] if r and r[0]]      # column A = TikTok URLs
```

**Recommended state model (robust):**
- `links.numbers` = **input only** (you just paste links).
- `processed.json` = ids already done (de-dupe; pick the first link not in here).
- `done.csv` + the dated output folder = the record of what's finished.
- *Optional* write-back: `numbers-parser` can edit+save cells (v4+), but **back up the file first**
  and expect occasional quirks — the sidecar approach above avoids the risk entirely.

---

## 5. n8n workflow (node map)
```
Schedule (daily, e.g. 02:00)
  → Execute Command: python pick_next.py          # reads .numbers, returns next link + id
  → IF no link → stop
  → Execute Command: yt-dlp …                      # download
  → Execute Command: ffmpeg first frame
  → HTTP Request → ComfyUI /prompt (avatar-into-frame)  → poll /history → /view
  → HTTP Request → ComfyUI /prompt (Wan 2.2 Animate)    → poll /history → save mp4
  → Execute Command: ffmpeg sample + HTTP DINOv2 gate   → IF drift: flag/retry
  → Execute Command: ffmpeg/exiftool metadata strip
  → Execute Command: mv to /out/<date>/ ; append done.csv ; update processed.json
  → (optional) Notify (e.g. a local notification / email that today's video is ready)
```
> Dockerized n8n: call host tools via mounted volumes + `host.docker.internal` for ComfyUI/Ollama,
> or run the shell steps with a small local "worker" script that n8n triggers over HTTP. Mount
> your iCloud folder and `/work` + `/out` into the container.

---

## 6. Performance & the daily cadence
- Steps 1–3, 6–8 are seconds. **Step 4** (avatar-into-frame) ≈ 0.5–2 min.
- **Step 5 (Wan Animate)** dominates: minutes per ~5 s, so a short clip is overnight-friendly;
  long clips may not finish in a day on M1 Max → cap length or offload step 5 to cloud.
- "One link/day" is a good match: kick it off at night, review in the morning.

---

## 7. Free-commercial + legal recap
- Stack is open/Apache/your-own-asset: yt-dlp, ffmpeg, exiftool, ComfyUI, **Wan 2.2 Animate
  (Apache)**, **DINOv2 (Apache)**, your **character LoRA**, SDXL/RealVisXL/schnell base. ✅
- Identity via **your LoRA** (no InsightFace) keeps it commercial-clean — see pipeline doc §9.
- Reference = **your own public TikToks**; output fully regenerated; metadata stripped.
- Synthetic adults only; AI-disclosure/platform rules are yours to handle.

---

## 8. Suggested build order
1. Get `pick_next.py` reading your `.numbers` + `processed.json` de-dupe working (no AI yet).
2. Wire **yt-dlp → ffmpeg first frame → metadata strip → /out** end-to-end on one link.
3. Add the **ComfyUI avatar-into-frame** step (needs your trained character LoRA first).
4. Add **Wan 2.2 Animate**; tune clip length to your render budget.
5. Add the **DINOv2 gate** + the daily **Schedule** trigger; let it run unattended.
