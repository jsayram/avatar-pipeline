#!/usr/bin/env python3
"""FR-1 — pick the next unprocessed TikTok URL from the iCloud .numbers sheet.

Reads Sheet 1 / Table 1 / column A (read-only — never writes back), skips ids
already in processed.json (published OR flagged), and prints exactly one JSON
line on stdout:

    {"status": "ok", "id": "<tiktok_id>", "url": "<url>"}
    {"status": "empty", "id": null, "url": null}      # nothing to do — clean no-op

Diagnostics go to stderr. Exit code is 0 in both cases so the n8n IF node can
branch on `status`.

Usage: python scripts/pick_next.py --config config.yaml
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import load_config
from lib.logging_utils import get_logger
from lib.state import extract_tiktok_id, load_state, seen_ids

ICLOUD_WAIT_SECONDS = 120


def ensure_local_copy(numbers_path: Path, logger) -> None:
    """Materialize an iCloud placeholder (`.name.icloud`) via brctl download."""
    if numbers_path.exists():
        return
    placeholder = numbers_path.parent / f".{numbers_path.name}.icloud"
    if not placeholder.exists():
        raise FileNotFoundError(
            f"numbers sheet not found: {numbers_path}\n"
            "Create it in Numbers (column A = TikTok URLs) or fix "
            "paths.numbers_sheet in config.yaml."
        )
    logger.info("iCloud placeholder detected — running `brctl download`")
    subprocess.run(["brctl", "download", str(numbers_path)], check=False,
                   capture_output=True)
    deadline = time.monotonic() + ICLOUD_WAIT_SECONDS
    while time.monotonic() < deadline:
        if numbers_path.exists():
            logger.info("iCloud download complete")
            return
        time.sleep(1)
    raise TimeoutError(
        f"iCloud did not materialize {numbers_path} within {ICLOUD_WAIT_SECONDS}s "
        "— check network/iCloud status and retry."
    )


def urls_from_rows(rows) -> list[str]:
    """Column A cells that look like URLs; headers/notes/empty rows are skipped."""
    urls = []
    for row in rows:
        if not row:
            continue
        cell = row[0]
        if isinstance(cell, str) and cell.strip().lower().startswith(("http://", "https://")):
            urls.append(cell.strip())
    return urls


def read_urls(numbers_path: Path) -> list[str]:
    from numbers_parser import Document  # imported lazily; heavy and gate-only tests skip it

    doc = Document(str(numbers_path))  # numbers-parser opens read-only; we never save
    table = doc.sheets[0].tables[0]
    return urls_from_rows(table.rows(values_only=True))


def select_next(urls: list[str], skip: set[str]) -> dict | None:
    for url in urls:
        tiktok_id = extract_tiktok_id(url)
        if tiktok_id not in skip:
            return {"id": tiktok_id, "url": url}
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)

    logger = get_logger("pick_next")
    cfg = load_config(args.config)

    ensure_local_copy(cfg.paths.numbers_sheet, logger)
    urls = read_urls(cfg.paths.numbers_sheet)
    state = load_state(cfg.paths.processed_json)
    logger.info("sheet has %d URL(s); %d already seen", len(urls), len(seen_ids(state)))

    nxt = select_next(urls, seen_ids(state))
    if nxt is None:
        logger.info("no unprocessed URLs — clean no-op")
        print(json.dumps({"status": "empty", "id": None, "url": None}))
    else:
        logger.info("next: id=%s url=%s", nxt["id"], nxt["url"])
        print(json.dumps({"status": "ok", **nxt}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
