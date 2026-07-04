"""Sidecar state (FR-8, §9 data contracts).

The .numbers sheet is read-only input; all state lives here:
  processed.json — {"processed": ["<id>", ...], "flagged": ["<id>", ...]}
  done.csv       — date,id,url,output_path,identity_cosine,status

A URL is consumed only after a final Kling motion-video outcome exists:
pick_next skips ids in the processed list. The flagged list is retained for
legacy compatibility/status display, but it no longer blocks retries by
itself. All JSON writes are atomic (temp file + os.replace).
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlparse

DONE_CSV_FIELDS = ("date", "id", "url", "output_path", "identity_cosine", "status")

_SHORTLINK_HOSTS = {"vm.tiktok.com", "vt.tiktok.com"}


def extract_tiktok_id(url: str) -> str:
    """Deterministic, offline id for a TikTok URL.

    Full links -> the numeric video id; short links (vm.tiktok.com/<code>,
    tiktok.com/t/<code>) -> the code; anything else -> a stable hash prefix.
    """
    url = url.strip()
    m = re.search(r"/(?:video|photo)/(\d+)", url)
    if m:
        return m.group(1)
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    host = parsed.netloc.lower().removeprefix("www.")
    if host in _SHORTLINK_HOSTS and parts:
        return parts[0]
    if len(parts) >= 2 and parts[0] == "t":
        return parts[1]
    return "u" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def load_state(processed_json: Path) -> dict:
    """Load processed.json; tolerate a missing file or legacy schema."""
    processed_json = Path(processed_json)
    if not processed_json.exists():
        return {"processed": [], "flagged": []}
    data = json.loads(processed_json.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{processed_json} must contain a JSON object")
    return {
        "processed": list(data.get("processed", [])),
        "flagged": list(data.get("flagged", [])),
    }


def seen_ids(state: dict) -> set[str]:
    """Ids pick_next must skip: final video outcomes only."""
    return set(state.get("processed", []))


def _atomic_write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _add_to_list(processed_json: Path, key: str, item_id: str) -> None:
    state = load_state(processed_json)
    if item_id not in state[key]:
        state[key].append(item_id)
    _atomic_write_text(processed_json, json.dumps(state, indent=2) + "\n")


def mark_processed(processed_json: Path, item_id: str) -> None:
    _add_to_list(processed_json, "processed", item_id)


def mark_flagged(processed_json: Path, item_id: str) -> None:
    _add_to_list(processed_json, "flagged", item_id)


def unflag(processed_json: Path, item_id: str) -> None:
    state = load_state(processed_json)
    if item_id not in state["flagged"]:
        raise KeyError(item_id)
    state["flagged"] = [value for value in state["flagged"] if value != item_id]
    _atomic_write_text(processed_json, json.dumps(state, indent=2) + "\n")


def append_done_csv(done_csv: Path, row: dict) -> None:
    """Append one audit row; write the header when creating the file."""
    done_csv = Path(done_csv)
    done_csv.parent.mkdir(parents=True, exist_ok=True)
    new_file = not done_csv.exists()
    with done_csv.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=DONE_CSV_FIELDS, extrasaction="ignore")
        if new_file:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in DONE_CSV_FIELDS})
