"""Filename conventions for the operator-facing out-pipe deliverables.

image-out/ and video-out/{published,failed}/ live under paths.out_dir by
default (see config.py) — see worker.py's run_animate/animate_and_gate/
strip_and_publish for where each file actually gets written.
"""
from __future__ import annotations

import datetime as dt


def timestamp_slug() -> str:
    """Compact, sortable, UTC timestamp for filenames — microsecond precision
    so back-to-back calls (e.g. consecutive failed animation attempts) never
    collide and silently overwrite each other."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def timestamped_filename(tiktok_id: str, kind: str, suffix: str) -> str:
    """`<tiktok_id>-<kind>-<timestamp><suffix>`, e.g. `abc123-image-20260703T134502Z.png`."""
    return f"{tiktok_id}-{kind}-{timestamp_slug()}{suffix}"
