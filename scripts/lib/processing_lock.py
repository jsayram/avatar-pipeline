"""Serializes texted-in link intake so back-to-back Telegram links never
race each other.

`lib/pending.py`'s "decline a new link if one is already pending" check only
guards the window AFTER `pending_approval.json` exists (i.e. after GATE 1 has
already been sent to Telegram) — everything from that point through the rest
of the two-gate flow is already safe. The gap is the window BEFORE that:
between a link arriving and `run_prepare()` finishing its download+extract
and writing the first pending record. Two links texted within that window
(e.g. back-to-back within a few seconds) would otherwise both start
processing concurrently and race on shared state files (processed.json,
done.csv, links_status.csv).

This is a simple exclusive lock file, acquired right before that window and
released right after, regardless of outcome. If a process crashes mid-hold
(leaving a stale lock), STALE_SECONDS bounds how long a new submission stays
blocked — generous relative to how long a download+extract should ever
realistically take.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

LOCK_FILENAME = ".link_processing.lock"
STALE_SECONDS = 15 * 60  # generous vs. real download+extract times (~1-2 min)


class LockHeldError(Exception):
    """Someone else already holds the lock (not stale)."""


def _lock_path(work_dir: Path) -> Path:
    return work_dir / LOCK_FILENAME


def try_acquire(work_dir: Path, tiktok_id: str) -> None:
    """Raise LockHeldError if another submission is actively being prepared;
    otherwise create the lock. Auto-steals a stale lock (see STALE_SECONDS)
    rather than wedging the system forever if a process died mid-hold."""
    work_dir.mkdir(parents=True, exist_ok=True)
    path = _lock_path(work_dir)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as fh:
            fh.write(tiktok_id)
        return
    except FileExistsError:
        pass

    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = STALE_SECONDS + 1  # lock vanished mid-check; treat as gone

    if age <= STALE_SECONDS:
        holder = path.read_text().strip() if path.exists() else "unknown"
        raise LockHeldError(
            f"an earlier link ({holder}) is still being downloaded/prepared "
            f"(lock age {age:.0f}s)"
        )

    # stale — steal it
    path.write_text(tiktok_id)


def release(work_dir: Path) -> None:
    _lock_path(work_dir).unlink(missing_ok=True)
