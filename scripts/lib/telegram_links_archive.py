"""Append-only archive of TikTok links submitted via Telegram text.

Purely a "keep" log for the operator's own reference — NOT a queue that
pick_next.py reads from (links.numbers stays the sole daily-scheduled-run
input; texted-in links are processed immediately instead, by
handle_telegram_reply.py). numbers_parser has no incremental-append API, so
each submission re-reads the whole file, appends one row, and re-saves the
whole document — acceptable for infrequent, operator-paced submissions.
Written to a temp file + os.replace so a crash mid-save can't corrupt the
archive.
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile
from pathlib import Path

HEADER = ["TikTok URL", "submitted_at (UTC)", "processing_note"]


def _save_atomic(doc, archive_path: Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(archive_path.parent), suffix=".numbers")
    os.close(fd)
    try:
        doc.save(tmp)
        os.replace(tmp, archive_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _ensure_header(table) -> None:
    for col, value in enumerate(HEADER):
        table.write(0, col, value)


def append_link(archive_path: Path, url: str) -> None:
    from numbers_parser import Document  # imported lazily; heavy and gate-only tests skip it

    archive_path = Path(archive_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    if archive_path.exists():
        doc = Document(str(archive_path))
        table = doc.sheets[0].tables[0]
        _ensure_header(table)
        next_row = len([r for r in table.rows(values_only=True) if r and r[0]])
    else:
        doc = Document()
        table = doc.sheets[0].tables[0]
        _ensure_header(table)
        next_row = 1

    if next_row >= table.num_rows:
        table.add_row()

    timestamp = dt.datetime.now(dt.timezone.utc).isoformat()
    table.write(next_row, 0, url)
    table.write(next_row, 1, timestamp)
    table.write(next_row, 2, "")

    _save_atomic(doc, archive_path)


def update_processing_note(archive_path: Path, url: str, note: str) -> bool:
    """Write a post-prepare note to the latest archived row for this URL.

    Returns False when there is no archive row to update. The caller treats
    any archive failure as non-fatal because link processing must continue.
    """
    from numbers_parser import Document  # imported lazily; heavy and gate-only tests skip it

    if not note:
        return False

    archive_path = Path(archive_path)
    if not archive_path.exists():
        return False

    doc = Document(str(archive_path))
    table = doc.sheets[0].tables[0]
    _ensure_header(table)

    match_row = None
    for row_idx, row in enumerate(table.rows(values_only=True)):
        if row_idx == 0 or not row:
            continue
        if row[0] == url:
            match_row = row_idx

    if match_row is None:
        return False

    table.write(match_row, 2, note)
    _save_atomic(doc, archive_path)
    return True
