"""Per-pending-approval lock shared by Telegram and the dashboard."""
from __future__ import annotations

import os
import time
from pathlib import Path

LOCK_FILENAME = ".approval_action.lock"
STALE_SECONDS = 2 * 60 * 60  # longer than WaveSpeed's 1800s animate poll


class ApprovalLockHeldError(Exception):
    """Someone else is already processing this approval decision."""


def _lock_path(work_dir: Path, tiktok_id: str) -> Path:
    return work_dir / tiktok_id / LOCK_FILENAME


def try_claim(work_dir: Path, tiktok_id: str, owner: str) -> None:
    path = _lock_path(work_dir, tiktok_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(owner)
        return
    except FileExistsError:
        pass

    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = STALE_SECONDS + 1

    if age <= STALE_SECONDS:
        holder = path.read_text(encoding="utf-8").strip() if path.exists() else "unknown"
        raise ApprovalLockHeldError(
            f"approval for {tiktok_id} is already being processed by {holder} "
            f"(lock age {age:.0f}s)"
        )

    path.write_text(owner, encoding="utf-8")


def release(work_dir: Path, tiktok_id: str) -> None:
    _lock_path(work_dir, tiktok_id).unlink(missing_ok=True)
