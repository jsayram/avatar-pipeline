# Character LoRA training — RunPod runbook (avatar_v1)

Dataset: `dataset/10_avatargirl woman/` — 40 images auto-curated from
`AvatarGirl/outputs` by `scripts/curate_lora_set.py` (DINOv2 identity ranking
+ diversity selection; see `dataset/curation_report.csv`). Review the 40
copies and delete any you dislike before uploading — 25–40 images is fine.
`avatargirl_dataset.zip` is the same folder zipped for upload.

**Base model: RealVisXL V5.0** — must match the pipeline's FR-4 base
(`video.base_model` in config.yaml). Train the LoRA on the same base you
generate with. Plain image-based Kohya training only — **never InsightFace-
based identity adapters** (they poison the commercial license,
docs/mature-optimization.md §3). Check RealVis's model-card terms before
monetizing output.

Folder-name convention (already applied): `10_avatargirl woman` =
10 repeats/epoch, instance token `avatargirl`, class `woman`.
**Your prompt trigger after training: `avatargirl woman`.**

## 1. Rent the GPU (~$0.40–0.70/hr, done in ~45 min)

RunPod → Deploy → **RTX 4090** (24 GB) → template **RunPod PyTorch 2.x** →
Start. Open the pod's web terminal (or SSH).

## 2. On the pod

```bash
# kohya sd-scripts
git clone https://github.com/kohya-ss/sd-scripts && cd sd-scripts
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pip install bitsandbytes xformers

# base model (RealVisXL V5, single-file fp16, ~6.9 GB)
pip install -U "huggingface_hub[cli]"
hf download SG161222/RealVisXL_V5.0 RealVisXL_V5.0_fp16.safetensors --local-dir .

# dataset: upload training/avatargirl_dataset.zip from your Mac
# (runpodctl send/receive, or the Jupyter file upload in the pod UI), then:
mkdir -p dataset && unzip ~/avatargirl_dataset.zip -d dataset/
ls dataset/   # must show: 10_avatargirl woman/
```

## 3. Train (~3200 steps, ~30 min on a 4090 at ~1.7 it/s)

**Run this inside `tmux` so a dropped SSH connection doesn't kill it:**
```bash
tmux new -s lora
```

**Do NOT use `accelerate launch`** — on a fresh pod with no
`~/.cache/huggingface/accelerate/default_config.yaml`, it silently defaults to
**CPU** regardless of `--gpu_ids`/`--num_processes` flags (confirmed bug,
2026-07-03: `accelerator device: cpu` in the log despite
`torch.cuda.is_available()` being `True`). A bare `python` invocation lets
`Accelerator()` auto-detect the GPU correctly. Run the training script
directly instead:

```bash
export HF_HUB_ENABLE_HF_TRANSFER=0 CUDA_VISIBLE_DEVICES=0
python sdxl_train_network.py \
  --pretrained_model_name_or_path=RealVisXL_V5.0_fp16.safetensors \
  --train_data_dir=dataset \
  --output_dir=output --output_name=avatar_v1 \
  --resolution=1024,1024 --enable_bucket \
  --min_bucket_reso=768 --max_bucket_reso=1344 \
  --network_module=networks.lora --network_dim=32 --network_alpha=16 \
  --learning_rate=1e-4 --text_encoder_lr=5e-5 --lr_scheduler=cosine \
  --optimizer_type=AdamW8bit --train_batch_size=1 --max_train_epochs=8 \
  --mixed_precision=bf16 --save_precision=fp16 \
  --cache_latents --gradient_checkpointing --sdpa \
  --seed=42 --save_every_n_epochs=2
```

**Verify it's actually on GPU within the first ~30s**: the log must show
`accelerator device: cuda` (not `cpu`). If it says `cpu`, stop (Ctrl+C) and
confirm you're running plain `python`, not `accelerate launch`. Also sanity
check with `nvidia-smi` — VRAM usage should climb into the multi-GB range and
utilization should be nonzero once latent caching / training starts; ~7s per
image during latent caching is a CPU-mode tell (GPU mode is ~10-100x faster).

Notes:
- `--save_every_n_epochs=2` gives checkpoints at epochs 2/4/6/8 (steps
  800/1600/2400/3200) — if the final LoRA looks overbaked (plastic skin,
  ignores prompts), try epoch 4 or 6.
- `--train_batch_size=1` fits comfortably in 24 GB with room to spare
  (~9-10 GB used); bump to 2 only if you want to trade VRAM headroom for speed.

## 4. Bring it home

```bash
# on the pod:
ls output/avatar_v1.safetensors        # ~200 MB
# transfer back (runpodctl, scp, or download via the pod's Jupyter UI), then on the Mac:
mv ~/Downloads/avatar_v1.safetensors \
   /Users/jramirez/llms/image-models/comfyui/loras/avatar_v1.safetensors
```

Then **stop/terminate the pod** (billing stops).

## 5. Verify on the Mac

```bash
cd /Users/jramirez/Git/avatar-pipeline
./scripts/setup_check.sh          # LoRA line goes green via worker --dry-run
```

Quick visual check in ComfyUI: load RealVisXL_V5.0_fp16 + LoraLoader
(avatar_v1) and prompt `avatargirl woman, portrait photo` — it should
unmistakably be her. Compare against `assets/avatar_reference.png`.
