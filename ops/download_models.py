#!/usr/bin/env python3
"""Download all pipeline models into ~/llms/image-models/comfyui/.

Uses list_repo_files + pattern matching so a renamed file fails loudly with
the available candidates instead of a silent 404. Skips files that already
exist with nonzero size (safe to rerun).
"""
import fnmatch
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("HF_HOME", str(Path.home() / "llms/huggingface"))
from huggingface_hub import hf_hub_download, list_repo_files

BASE = Path.home() / "llms/image-models/comfyui"
for sub in ("checkpoints", "loras", "vae", "diffusion_models", "text_encoders",
            "clip_vision", "controlnet", "upscale_models", "sams",
            "grounding-dino", "embeddings", "ultralytics/bbox", "ultralytics/segm"):
    (BASE / sub).mkdir(parents=True, exist_ok=True)


def grab_hf(repo: str, pattern: str, dest_dir: str, rename: str | None = None):
    files = list_repo_files(repo)
    matches = [f for f in files if fnmatch.fnmatch(f, pattern)]
    if not matches:
        print(f"!! NO MATCH in {repo} for {pattern}; candidates:")
        for f in files:
            if f.endswith((".safetensors", ".pth", ".onnx")):
                print(f"     {f}")
        raise SystemExit(1)
    remote = sorted(matches)[0]
    dest = BASE / dest_dir / (rename or Path(remote).name)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"SKIP (exists): {dest.name}")
        return
    print(f"DOWNLOADING {repo} :: {remote} -> {dest_dir}/{dest.name}")
    got = hf_hub_download(repo_id=repo, filename=remote)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    subprocess.run(["cp", got, str(tmp)], check=True)
    os.replace(tmp, dest)
    print(f"DONE: {dest.name} ({dest.stat().st_size / 1e9:.2f} GB)")


def grab_url(url: str, dest_dir: str, name: str):
    dest = BASE / dest_dir / name
    if dest.exists() and dest.stat().st_size > 0:
        print(f"SKIP (exists): {name}")
        return
    print(f"DOWNLOADING {url}")
    tmp = str(dest) + ".tmp"
    subprocess.run(["curl", "-L", "--fail", "--retry", "3", "-o", tmp, url],
                   check=True, capture_output=True)
    os.replace(tmp, dest)
    print(f"DONE: {name} ({dest.stat().st_size / 1e9:.2f} GB)")


WAN_REPO = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"

# FR-4 base (RealVisXL single-file) + VAE safety net
grab_hf("SG161222/RealVisXL_V5.0", "RealVisXL_V5.0_fp16.safetensors", "checkpoints")
grab_hf("madebyollin/sdxl-vae-fp16-fix", "*.safetensors", "vae",
        rename="sdxl_vae_fp16_fix.safetensors")

# FR-4 ControlNet (union covers openpose+depth+more in one model)
grab_hf("xinsir/controlnet-union-sdxl-1.0", "*promax*.safetensors", "controlnet",
        rename="controlnet-union-sdxl-promax.safetensors")

# FR-5 Wan 2.2 Animate stack (per docs.comfy.org wan2-2-animate tutorial)
grab_hf(WAN_REPO, "*diffusion_models/wan2.2_animate_14B_bf16.safetensors",
        "diffusion_models")
grab_hf(WAN_REPO, "*text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        "text_encoders")
grab_hf(WAN_REPO, "*vae/wan_2.1_vae.safetensors", "vae")
# clip_vision_h ships in the Wan 2.1 repackage repo, not the 2.2 one
grab_hf("Comfy-Org/Wan_2.1_ComfyUI_repackaged",
        "*clip_vision/clip_vision_h.safetensors", "clip_vision")

# FR-4 person segmentation (storyicon/comfyui_segment_anything)
grab_url("https://dl.fbaipublicfiles.com/segment_anything/sam_vit_h_4b8939.pth",
         "sams", "sam_vit_h_4b8939.pth")
grab_url("https://huggingface.co/ShilongLiu/GroundingDINO/resolve/main/groundingdino_swint_ogc.pth",
         "grounding-dino", "groundingdino_swint_ogc.pth")
grab_url("https://huggingface.co/ShilongLiu/GroundingDINO/resolve/main/GroundingDINO_SwinT_OGC.cfg.py",
         "grounding-dino", "GroundingDINO_SwinT_OGC.cfg.py")

# Upscaler (small)
grab_url("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
         "upscale_models", "RealESRGAN_x4plus.pth")

# Face detector for Impact Pack's FaceDetailer (post-FR-4 detail pass)
grab_url("https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8m.pt",
         "ultralytics/bbox", "face_yolov8m.pt")

print("ALL DOWNLOADS COMPLETE")
