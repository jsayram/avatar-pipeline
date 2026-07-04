"""Pending-approval state for the two-gate human-review flow.

There are now TWO approval gates before a video ever gets published, each
tracked via the record's `stage` field:

  - `stage="frame"`: waiting on approval of the raw extracted TikTok frame,
    BEFORE any paid Seedream call happens. `worker.py --phase prepare`
    creates this. Yes -> `--phase generate_avatar` (runs Seedream, moves to
    stage="avatar"). No -> `--phase reject_frame` (flag+skip — a raw
    extracted frame has no seed to retry, so rejecting it just means this
    source clip isn't usable).
  - `stage="avatar"`: waiting on approval of the Seedream-generated avatar
    still, BEFORE any paid animation call happens. `--phase generate_avatar`
    (first attempt) or `--phase regenerate` (later attempts) create this.
    Yes -> `--phase animate`. No -> `--phase regenerate` (redo Seedream with
    a new seed).

Every phase EXITS immediately after sending its Telegram message — it does
not block waiting for a reply (n8n execution nodes shouldn't sit open for
hours, and now also for links texted in ad hoc via Telegram, not just the
one daily-scheduled link). The reply arrives later as a completely separate
process invocation (a webhook-triggered n8n workflow calling
`worker.py --phase <next-phase> --id <id>`), so the state needed to resume —
which video, which frame, which avatar still, which attempt number — has to
be persisted to disk in between. This module is that persistence layer.

Only one pending approval is allowed to exist at a time, regardless of
whether it came from the daily schedule or a link texted in via Telegram —
`find_pending_id()` relies on that to let the Telegram reply handler resolve
a bare "yes"/"no" message to the right run without n8n having to thread an
id through the webhook payload. `handle_telegram_reply.py` enforces this by
rejecting a newly texted-in link while one is already outstanding, asking the
operator to resolve the current one first.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PENDING_FILENAME = "pending_approval.json"


class PendingApprovalError(Exception):
    """No pending approval exists (or more than one does) when one was expected."""


def _pending_path(work_dir: Path, tiktok_id: str) -> Path:
    return work_dir / tiktok_id / PENDING_FILENAME


def save_pending(work_dir: Path, tiktok_id: str, *, stage: str, url: str,
                 ref_video_path: str, frame1_path: str,
                 avatar_frame_path: str | None = None,
                 attempt: int = 1,
                 processing_note: str = "") -> None:
    """Write/overwrite the pending-approval record for this id.

    stage must be "frame" or "avatar" — see module docstring.
    avatar_frame_path is None at stage="frame" (nothing generated yet)."""
    data = {
        "id": tiktok_id,
        "stage": stage,
        "url": url,
        "ref_video_path": ref_video_path,
        "frame1_path": frame1_path,
        "avatar_frame_path": avatar_frame_path,
        "attempt": attempt,
        "processing_note": processing_note,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _pending_path(work_dir, tiktok_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_pending(work_dir: Path, tiktok_id: str) -> dict:
    path = _pending_path(work_dir, tiktok_id)
    if not path.exists():
        raise PendingApprovalError(
            f"no pending approval found for id {tiktok_id} at {path} — "
            "it may have already been resolved, or 'prepare' was never run for it"
        )
    return json.loads(path.read_text())


def clear_pending(work_dir: Path, tiktok_id: str) -> None:
    path = _pending_path(work_dir, tiktok_id)
    path.unlink(missing_ok=True)


def find_pending_id(work_dir: Path) -> str:
    """Find the single outstanding pending-approval id.

    Raises if there are zero or more than one — both are situations the
    Telegram reply handler should surface rather than guess at, since acting
    on the wrong run would animate/flag the wrong TikTok link.
    """
    if not work_dir.exists():
        raise PendingApprovalError(f"work dir does not exist: {work_dir}")
    matches = sorted(work_dir.glob(f"*/{PENDING_FILENAME}"))
    if not matches:
        raise PendingApprovalError("no pending approval is currently outstanding")
    if len(matches) > 1:
        ids = ", ".join(m.parent.name for m in matches)
        raise PendingApprovalError(
            f"multiple pending approvals found ({ids}) — expected at most one; "
            "resolve manually with --id to avoid acting on the wrong run"
        )
    return matches[0].parent.name
