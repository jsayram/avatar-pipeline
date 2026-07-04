#!/usr/bin/env python3
"""Curate a LoRA training set from generated avatar candidates.

Scores every candidate image against the canonical avatar reference with
DINOv2 (same model as the identity gate), drops off-identity outliers, then
greedily picks a maximally diverse subset (max-min distance in embedding
space) so the training set varies in pose/lighting/framing while staying on
identity.

Usage:
    python scripts/curate_lora_set.py \
        --ref assets/avatar_reference.png \
        --src "<dir with candidate images>" [--src "<another dir>" ...] \
        --dest training/dataset/10_avatargirl \
        --count 40 [--keep-fraction 0.6]

Writes a report (curation_report.csv: filename, cosine, selected) next to
--dest. Images are copied, never moved; sources are read-only.
"""
from __future__ import annotations

import argparse
import csv
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.logging_utils import get_logger

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def find_images(sources: list[Path]) -> list[Path]:
    images: list[Path] = []
    for src in sources:
        if not src.is_dir():
            raise SystemExit(f"source dir not found: {src}")
        images.extend(
            p for p in sorted(src.iterdir())
            if p.suffix.lower() in IMAGE_EXTS and p.is_file()
        )
    return images


def embed_all(paths: list[Path], logger):
    """DINOv2 embeddings, normalized, on MPS when available."""
    import torch
    import torch.nn.functional as F
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModel

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    processor = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
    model = AutoModel.from_pretrained("facebook/dinov2-base").to(device).eval()
    logger.info("embedding %d images with DINOv2 on %s", len(paths), device)

    vecs = []
    for i, path in enumerate(paths, 1):
        image = Image.open(path).convert("RGB")
        inputs = processor(images=image, return_tensors="pt").to(device)
        with torch.no_grad():
            pooled = model(**inputs).last_hidden_state.mean(dim=1)
        vecs.append(F.normalize(pooled, dim=-1).cpu())
        if i % 100 == 0:
            logger.info("  %d/%d", i, len(paths))
    return torch.cat(vecs, dim=0)  # (N, D), unit-norm rows


def select_diverse(embeddings, candidate_idx: list[int], seed_idx: int,
                   count: int) -> list[int]:
    """Greedy max-min (farthest-point) selection for pose/scene diversity."""
    selected = [seed_idx]
    remaining = [i for i in candidate_idx if i != seed_idx]
    while remaining and len(selected) < count:
        best_i, best_score = None, -1.0
        for i in remaining:
            # distance to the CLOSEST already-selected image; maximize it
            sims = embeddings[i] @ embeddings[selected].T
            min_dist = float(1.0 - sims.max())
            if min_dist > best_score:
                best_score, best_i = min_dist, i
        selected.append(best_i)
        remaining.remove(best_i)
    return selected


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="canonical avatar image")
    parser.add_argument("--src", action="append", required=True,
                        help="candidate dir (repeatable)")
    parser.add_argument("--dest", required=True,
                        help="output dir (kohya-style, e.g. training/dataset/10_avatargirl)")
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--keep-fraction", type=float, default=0.6,
                        help="fraction of candidates kept after identity ranking "
                             "before the diversity pass (drops off-identity outliers)")
    args = parser.parse_args(argv)

    logger = get_logger("curate")
    ref = Path(args.ref)
    if not ref.is_file():
        raise SystemExit(f"reference image not found: {ref}")
    images = find_images([Path(s) for s in args.src])
    if len(images) < args.count:
        raise SystemExit(f"only {len(images)} candidates < requested {args.count}")

    embeddings = embed_all([ref] + images, logger)
    ref_vec, img_vecs = embeddings[0], embeddings[1:]
    cosines = (img_vecs @ ref_vec).tolist()

    order = sorted(range(len(images)), key=lambda i: cosines[i], reverse=True)
    keep = order[: max(args.count, int(len(images) * args.keep_fraction))]
    logger.info("identity ranking: best %.4f / median %.4f / worst kept %.4f",
                cosines[order[0]], cosines[order[len(order) // 2]],
                cosines[keep[-1]])

    chosen = select_diverse(img_vecs, keep, seed_idx=order[0], count=args.count)

    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    for rank, i in enumerate(chosen, 1):
        shutil.copy2(images[i], dest / f"{rank:03d}_{images[i].name}")

    report = dest.parent / "curation_report.csv"
    with report.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "cosine_to_ref", "selected"])
        for i in order:
            writer.writerow([str(images[i]), f"{cosines[i]:.4f}",
                             "yes" if i in chosen else ""])

    sel_cos = sorted(cosines[i] for i in chosen)
    logger.info("selected %d/%d images -> %s (cosine range %.4f–%.4f)",
                len(chosen), len(images), dest, sel_cos[0], sel_cos[-1])
    logger.info("report: %s — review the copies and delete any you dislike "
                "before training", report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
