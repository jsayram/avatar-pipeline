#!/usr/bin/env python3
"""FR-6 — DINOv2 identity-gate service (Apache-licensed; no InsightFace).

Contract (PRODUCT-SPEC §8):
    POST /compare  {"ref": "/path/ref.png", "gen": "/path/gen.png"}
                -> {"cosine": 0.9123, "pass": true}

`pass` uses this service's own threshold (GATE_THRESHOLD env, default 0.88);
the worker applies config.identity.cosine_min to the MEAN cosine across
sampled frames, so the authoritative decision lives in the worker.

Run:  python scripts/face_gate.py            # 127.0.0.1:8189
Env:  GATE_MODEL      (default facebook/dinov2-base)
      GATE_THRESHOLD  (default 0.88)
      GATE_PORT       (default 8189)

First start downloads the DINOv2 weights into the Hugging Face cache; after
that it is fully offline.

Memory note: this is the ONE long-running process in the pipeline (launchd-
managed, stays up indefinitely) — every other script is a short-lived
subprocess that exits and returns its memory to the OS after each phase. On
MPS, PyTorch uses a caching allocator (like CUDA's): freed tensors return to
an internal pool for reuse rather than back to the OS immediately, so this
process's RSS can climb over the service's lifetime even though nothing is
actually "lost" — it's cached, not leaked. `_release_device_memory()` below
explicitly empties that cache after every request so the OS sees the memory
back, instead of it only ever growing until the service restarts.
"""
from __future__ import annotations

import gc
import os
from pathlib import Path

import torch
import torch.nn.functional as F
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel
from transformers import AutoImageProcessor, AutoModel

MODEL_NAME = os.environ.get("GATE_MODEL", "facebook/dinov2-base")
THRESHOLD = float(os.environ.get("GATE_THRESHOLD", "0.88"))

app = FastAPI(title="avatar-pipeline identity gate", version="1.0")

_device = "mps" if torch.backends.mps.is_available() else "cpu"
_processor = AutoImageProcessor.from_pretrained(MODEL_NAME)
_model = AutoModel.from_pretrained(MODEL_NAME).to(_device).eval()

# The reference image is the same file on every call — cache its embedding.
_ref_cache: dict[tuple[str, float], torch.Tensor] = {}


class CompareRequest(BaseModel):
    ref: str
    gen: str


def _embed(path: Path) -> torch.Tensor:
    with Image.open(path) as raw:
        image = raw.convert("RGB")
    inputs = _processor(images=image, return_tensors="pt").to(_device)
    with torch.no_grad():
        pooled = _model(**inputs).last_hidden_state.mean(dim=1)
    return F.normalize(pooled, dim=-1).cpu()


def _embed_ref(path: Path) -> torch.Tensor:
    key = (str(path), path.stat().st_mtime)
    if key not in _ref_cache:
        _ref_cache.clear()
        _ref_cache[key] = _embed(path)
    return _ref_cache[key]


def _release_device_memory() -> None:
    """Return the MPS caching allocator's freed-but-retained pool back to
    the OS. Cheap relative to the embedding call itself; called once per
    request rather than left to accumulate over this long-running
    service's lifetime."""
    gc.collect()
    if _device == "mps":
        torch.mps.empty_cache()


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "device": _device,
            "threshold": THRESHOLD}


@app.post("/compare")
def compare(req: CompareRequest):
    ref_path, gen_path = Path(req.ref), Path(req.gen)
    for label, p in (("ref", ref_path), ("gen", gen_path)):
        if not p.is_file():
            raise HTTPException(status_code=400, detail=f"{label} image not found: {p}")
    try:
        cosine = float((_embed_ref(ref_path) * _embed(gen_path)).sum())
        return {"cosine": round(cosine, 4), "pass": cosine >= THRESHOLD}
    finally:
        _release_device_memory()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("GATE_PORT", "8189")))
