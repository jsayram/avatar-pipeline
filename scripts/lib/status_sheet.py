"""Read-glance companion status file, generated next to links.numbers.

NEVER touches links.numbers itself — PRODUCT-SPEC.md §5 #6 is a hard
requirement that the sheet stays read-only input, since it's an
unofficial/reverse-engineered file format synced via iCloud and a write
conflict there would risk the operator's real link data. This module instead
fully regenerates a brand-new CSV (paths.status_sheet, default
"links_status.csv" next to the sheet) from processed.json + done.csv +
work/<id>/pending_approval.json every time it's called — a derived,
disposable file, never an incremental edit, so there's nothing to corrupt.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .pending import PENDING_FILENAME
from .state import extract_tiktok_id, load_state

REFERENCE_INFO_FILENAME = "reference_info.json"
STATUS_FIELDS = (
    "url", "note", "processing_note", "status", "id",
    "identity_cosine", "output_path", "date",
)


def _rows_with_notes(numbers_path: Path) -> list[tuple[str, str]]:
    """Column A (URL) + column B (note) pairs; same row filter as pick_next.py."""
    from numbers_parser import Document  # imported lazily; heavy and gate-only tests skip it

    doc = Document(str(numbers_path))  # read-only: we never call doc.save()
    table = doc.sheets[0].tables[0]
    out = []
    for row in table.rows(values_only=True):
        if not row:
            continue
        cell = row[0]
        if isinstance(cell, str) and cell.strip().lower().startswith(("http://", "https://")):
            note = row[1] if len(row) > 1 and row[1] else ""
            out.append((cell.strip(), note))
    return out


def _done_csv_rows(done_csv: Path) -> dict[str, dict]:
    if not done_csv.exists():
        return {}
    with done_csv.open(newline="", encoding="utf-8") as fh:
        return {row["id"]: row for row in csv.DictReader(fh)}


def _processing_note(work_dir: Path, tiktok_id: str, pending_path: Path) -> str:
    info_path = work_dir / tiktok_id / REFERENCE_INFO_FILENAME
    for path in (info_path, pending_path):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        note = data.get("processing_note")
        if note:
            return str(note)
    return ""


def build_status_rows(cfg) -> list[dict]:
    link_rows = _rows_with_notes(cfg.paths.numbers_sheet)
    state = load_state(cfg.paths.processed_json)
    done_rows = _done_csv_rows(cfg.paths.done_csv)

    rows = []
    for url, note in link_rows:
        tiktok_id = extract_tiktok_id(url)
        pending_path = cfg.paths.work_dir / tiktok_id / PENDING_FILENAME

        # done.csv rows are only authoritative for the id's CURRENT terminal
        # state (published/flagged) — a stale row from an earlier rejected
        # attempt (e.g. un-flagged and retried) must not leak into a
        # pending/not-yet-processed row's cosine/output/date columns.
        is_terminal = tiktok_id in state["processed"] or tiktok_id in state["flagged"]
        done = done_rows.get(tiktok_id) if is_terminal else None

        if tiktok_id in state["processed"]:
            status = done["status"] if done else "published"
        elif tiktok_id in state["flagged"]:
            status = done["status"] if done else "flagged"
        elif pending_path.exists():
            status = "awaiting your Telegram approval"
        else:
            status = "not yet processed"

        rows.append({
            "url": url,
            "note": note,
            "processing_note": _processing_note(cfg.paths.work_dir, tiktok_id, pending_path),
            "status": status,
            "id": tiktok_id,
            "identity_cosine": done.get("identity_cosine", "") if done else "",
            "output_path": done.get("output_path", "") if done else "",
            "date": done.get("date", "") if done else "",
        })
    return rows


def write_status_sheet(cfg, rows: list[dict] | None = None) -> Path:
    """Regenerate the companion CSV from scratch. Atomic write; never touches
    links.numbers."""
    if rows is None:
        rows = build_status_rows(cfg)
    out_path = cfg.paths.status_sheet
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=STATUS_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(out_path)
    return out_path
