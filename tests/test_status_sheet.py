"""Companion status CSV — regenerated from state, never touches links.numbers."""
import csv
import json

from lib.config import load_config
from lib.pending import save_pending
from lib.state import append_done_csv, mark_flagged, mark_processed
from lib.status_sheet import build_status_rows, write_status_sheet

URL_A = "https://www.tiktok.com/t/aaa111/"
URL_B = "https://www.tiktok.com/t/bbb222/"
URL_C = "https://www.tiktok.com/t/ccc333/"


def _write_sheet(path, rows):
    from numbers_parser import Document

    doc = Document()
    table = doc.sheets[0].tables[0]
    table.write(0, 0, "TikTok URL")
    table.write(0, 1, "note")
    for i, (url, note) in enumerate(rows, start=1):
        table.write(i, 0, url)
        table.write(i, 1, note)
    doc.save(str(path))


def test_not_yet_processed_by_default(make_config, tmp_path):
    _write_sheet(tmp_path / "links.numbers", [(URL_A, "n/a")])
    cfg = load_config(make_config())
    rows = build_status_rows(cfg)
    assert rows == [{
        "url": URL_A, "note": "n/a", "processing_note": "", "status": "not yet processed",
        "id": "aaa111", "identity_cosine": "", "output_path": "", "date": "",
    }]


def test_pending_approval_shows_awaiting(make_config, tmp_path):
    _write_sheet(tmp_path / "links.numbers", [(URL_A, "")])
    cfg = load_config(make_config())
    save_pending(cfg.paths.work_dir, "aaa111", stage="frame", url=URL_A,
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png")
    rows = build_status_rows(cfg)
    assert rows[0]["status"] == "awaiting your Telegram approval"


def test_published_pulls_done_csv_metadata(make_config, tmp_path):
    _write_sheet(tmp_path / "links.numbers", [(URL_B, "")])
    cfg = load_config(make_config())
    append_done_csv(cfg.paths.done_csv, {
        "date": "2026-07-03", "id": "bbb222", "url": URL_B,
        "output_path": "/out/bbb222.mp4", "identity_cosine": "0.9123",
        "status": "published",
    })
    mark_processed(cfg.paths.processed_json, "bbb222")
    rows = build_status_rows(cfg)
    assert rows[0]["status"] == "published"
    assert rows[0]["identity_cosine"] == "0.9123"
    assert rows[0]["output_path"] == "/out/bbb222.mp4"


def test_flagged_shows_stage_from_done_csv(make_config, tmp_path):
    _write_sheet(tmp_path / "links.numbers", [(URL_C, "")])
    cfg = load_config(make_config())
    append_done_csv(cfg.paths.done_csv, {
        "date": "2026-07-02", "id": "ccc333", "url": URL_C,
        "output_path": "", "identity_cosine": "0.5615",
        "status": "flagged:identity_gate",
    })
    mark_flagged(cfg.paths.processed_json, "ccc333")
    rows = build_status_rows(cfg)
    assert rows[0]["status"] == "flagged:identity_gate"


def test_stale_done_csv_row_does_not_leak_into_pending_status(make_config, tmp_path):
    """A previously-flagged id that gets un-flagged and retried must not show
    the OLD run's cosine/output/date once it's back to a non-terminal state."""
    _write_sheet(tmp_path / "links.numbers", [(URL_A, "")])
    cfg = load_config(make_config())
    append_done_csv(cfg.paths.done_csv, {
        "date": "2026-07-02", "id": "aaa111", "url": URL_A,
        "output_path": "", "identity_cosine": "0.5615",
        "status": "flagged:identity_gate",
    })
    # un-flag and put back into "pending approval" — mirrors a real retry
    save_pending(cfg.paths.work_dir, "aaa111", stage="frame", url=URL_A,
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png")
    rows = build_status_rows(cfg)
    assert rows[0]["status"] == "awaiting your Telegram approval"
    assert rows[0]["identity_cosine"] == ""
    assert rows[0]["date"] == ""


def test_processing_note_comes_from_reference_info(make_config, tmp_path):
    _write_sheet(tmp_path / "links.numbers", [(URL_A, "")])
    cfg = load_config(make_config())
    work = cfg.paths.work_dir / "aaa111"
    work.mkdir(parents=True)
    (work / "reference_info.json").write_text(json.dumps({
        "trimmed": True,
        "processing_note": "Source video was 13.40s, so the reference clip was trimmed to 9.95s.",
    }))

    rows = build_status_rows(cfg)
    assert rows[0]["processing_note"].startswith("Source video was 13.40s")


def test_write_status_sheet_produces_valid_csv(make_config, tmp_path):
    _write_sheet(tmp_path / "links.numbers", [(URL_A, "one"), (URL_B, "two")])
    cfg = load_config(make_config())
    out_path = write_status_sheet(cfg)
    assert out_path == cfg.paths.status_sheet
    with out_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["url"] for r in rows] == [URL_A, URL_B]
    assert "processing_note" in rows[0]
    assert all(r["status"] == "not yet processed" for r in rows)


def test_default_status_sheet_path_is_next_to_numbers_sheet(make_config, tmp_path):
    cfg = load_config(make_config())
    assert cfg.paths.status_sheet == cfg.paths.numbers_sheet.parent / "links_status.csv"
