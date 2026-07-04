# Optimizing the Local Pipeline for NSFW / Adult Content

> Written 2026-06-27. Production guide for **legal, fully-synthetic adult content** on
> **M1 Max 64 GB**, feeding into the avatar pipeline (`automated-avatar-pipeline-n8n.md`)
> and the free-commercial stack (`consider-upgrade-models.md`).
> This is quality + licensing engineering, not a "jailbreak" — local image tooling has no
> meaningful filter to defeat (see §2).

---

## 0. Non-negotiable boundaries (scope of this doc)
This doc assumes **all depicted characters are fully synthetic adults**. Hard lines that **no
setting changes**:
- **Adults only.** Sexual content depicting minors is illegal in many jurisdictions **even when
  fully synthetic/AI-generated**. "Not a real person" is not a defense here.
- **No real-person likeness** in sexual content without consent (you're already synthetic-only — good).
- **Disclosure/platform/payment rules are on you** — AI-content labeling laws (EU AI Act, US state
  laws) and platform/processor policies apply when you monetize.
Everything below is about **output quality and commercial-license cleanliness** within those lines.

---

## 1. Why this is mostly model-selection, not "unlocking"
Image models don't *refuse* — they depict what they were trained on. So "NSFW capability" =
**which checkpoint/LoRA you load**, not a guardrail you remove. Two levers only:
1. **The optional output filter** (§2) — off by default in ComfyUI.
2. **Model capability** (§3) — pick/fine-tune an adult-capable base.

---

## 2. The output filter (the only "safety" toggle)
- **ComfyUI: there is none.** It never blurs/blocks output. Your pipeline already has nothing to disable.
- **diffusers scripts:** turn off the optional NSFW classifier at load:
  ```python
  pipe = AutoPipelineForText2Image.from_pretrained(
      model_id, safety_checker=None, requires_safety_checker=False, torch_dtype=torch.float16)
  ```
That's the entire "filter." Nothing deeper exists for SD/FLUX-class models.

---

## 3. Model selection — capability **and** commercial license
The **SDXL ecosystem** has by far the most mature adult-content support; FLUX base is trained tame
and is harder for this (and FLUX-**dev** is non-commercial anyway). For a **free-commercial** adult
pipeline, SDXL-based is the practical route.

| Option | Notes | License caution |
|--------|-------|-----------------|
| **RealVisXL** (already in `~/llms`) | photoreal, largely unrestricted base — good for realistic adult | check model card (RealVis terms) |
| **SDXL 1.0 base** + your LoRA | clean base you control | CreativeML OpenRAIL-M (use-restrictions, commercial-OK) |
| **Pony Diffusion / community SDXL NSFW checkpoints** | strongest adult anatomy/coherence | ⚠️ **licenses vary widely — verify each** before monetizing |
| FLUX schnell + NSFW LoRA | Apache base, but weaker NSFW; fewer good LoRAs | schnell Apache ✅ / LoRA = verify |

**Commercial-clean strategy:** start from a **permissively-licensed base** (SDXL/RealVisXL/schnell)
and **train your own avatar+style LoRA** — the LoRA is *your* asset, so you don't inherit a random
community checkpoint's license. Always confirm a downloaded checkpoint/LoRA/embedding's license
before using it in paid work. (Same InsightFace rule as the main doc: don't build identity on
antelopey/buffalo for commercial.)

---

## 4. Quality optimization (where the real work is)
Adult content lives or dies on **anatomy + face coherence**. The standard ComfyUI stack:

1. **Native resolution first.** Generate at SDXL ~1024×1024 (or 832×1216 portrait). Off-ratio = artifacts.
2. **ADetailer / FaceDetailer (auto-inpaint).** Detect face (and other regions) and re-render them
   at high detail. This is the single biggest quality win for faces/hands in NSFW gen.
3. **Hi-res fix / upscale.** 1.5–2× latent upscale, then **Real-ESRGAN / 4x-UltraSharp** (BSD/open).
4. **Hands & anatomy.** Quality **negative prompts** + negative embeddings; **ControlNet (DWPose,
   Apache)** to force correct pose/proportions; **inpaint** to fix any remaining anatomy.
5. **Sampler/CFG.** `DPM++ 2M Karras` (or `Euler a`), ~25–35 steps, CFG **4–7** (Pony-class models
   often want their own recommended CFG/clip-skip — follow the model card).
6. **VAE.** Use the correct SDXL VAE (e.g. `sdxl-vae-fp16-fix`) for skin tones / no washed-out output.
7. **Detail LoRAs.** An "add-detail/skin" LoRA at low weight sharpens texture realism.

---

## 5. Consistent adult avatar (the identity part)
Tie this to the main pipeline's **character LoRA** approach:
1. Invent the avatar: generate candidate faces/bodies with your base model (synthetic from scratch).
2. Curate ~20–40 varied shots (angles, lighting, **and** the content states you'll need).
3. Train **one character LoRA** (Kohya, normal image-based training — **no InsightFace**).
4. Load that LoRA in every generation → same avatar, any scene/state, fully consistent and
   commercially yours. Add **ControlNet pose** + **ADetailer face** for per-image polish.

> This is what delivers your "same avatar, 100% consistent" across SFW *and* NSFW outputs.

---

## 6. Pose & composition control
- **ControlNet OpenPose / DWPose** (Apache) — drive exact poses from a reference pose image.
- **Regional prompting / inpainting** — control specific areas independently.
- For **video** (Wan 2.2 Animate): same capability rules; the reference video drives motion. Note
  the M1 Max video speed caveat (minutes/clip) from the pipeline doc — batch overnight.

---

## 7. M1 Max 64 GB performance notes
- SDXL/Pony at 1024 + ADetailer + upscale: roughly **30 s – 2 min/image** on M1 Max (ADetailer and
  hi-res add time). 64 GB is plenty of headroom.
- Run big sets as overnight batches via the n8n loop; keep the **DINOv2 face-gate** (Apache) in the
  loop so only on-model, in-spec images are kept.

---

## 8. Commercial-license checklist (before you sell anything)
- [ ] Base model license allows commercial use? (SDXL OpenRAIL / RealVis terms / schnell Apache)
- [ ] Every community **checkpoint / LoRA / embedding / upscaler** verified commercial-OK?
- [ ] Identity = **your own LoRA** (not an InsightFace-based adapter)?
- [ ] Face-gate = **DINOv2 / SFace** (not antelopev2)?
- [ ] Content: synthetic adults only; AI-disclosure + platform/payment rules handled?

---

### Related docs
- `automated-avatar-pipeline-n8n.md` — the orchestration + face-lock pipeline
- `consider-upgrade-models.md` — model landscape, FLUX tiers, licensing
