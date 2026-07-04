"""Append-only 'keep' archive of TikTok links texted in via Telegram."""
from lib.telegram_links_archive import append_link, update_processing_note


def _read_rows(path):
    from numbers_parser import Document

    doc = Document(str(path))
    table = doc.sheets[0].tables[0]
    return [r[:3] for r in table.rows(values_only=True) if r and r[0]]


def test_append_creates_file_with_header(tmp_path):
    path = tmp_path / "linksThroughTelegram.numbers"
    append_link(path, "https://www.tiktok.com/t/AAA111/")

    rows = _read_rows(path)
    assert rows[0] == ["TikTok URL", "submitted_at (UTC)", "processing_note"]
    assert rows[1][0] == "https://www.tiktok.com/t/AAA111/"
    assert rows[1][2] in ("", None)


def test_append_multiple_links_keeps_all(tmp_path):
    path = tmp_path / "linksThroughTelegram.numbers"
    append_link(path, "https://www.tiktok.com/t/AAA111/")
    append_link(path, "https://www.tiktok.com/t/BBB222/")
    append_link(path, "https://www.tiktok.com/t/CCC333/")

    rows = _read_rows(path)
    urls = [r[0] for r in rows[1:]]
    assert urls == [
        "https://www.tiktok.com/t/AAA111/",
        "https://www.tiktok.com/t/BBB222/",
        "https://www.tiktok.com/t/CCC333/",
    ]


def test_append_creates_parent_dir(tmp_path):
    path = tmp_path / "nested" / "dir" / "linksThroughTelegram.numbers"
    append_link(path, "https://www.tiktok.com/t/AAA111/")
    assert path.exists()


def test_update_processing_note_updates_latest_matching_link(tmp_path):
    path = tmp_path / "linksThroughTelegram.numbers"
    url = "https://www.tiktok.com/t/AAA111/"
    append_link(path, url)
    append_link(path, "https://www.tiktok.com/t/BBB222/")
    append_link(path, url)

    assert update_processing_note(path, url, "trimmed to 9.95s for 10s limit")

    rows = _read_rows(path)
    assert rows[1][2] in ("", None)
    assert rows[3][2] == "trimmed to 9.95s for 10s limit"


def test_update_processing_note_missing_row_returns_false(tmp_path):
    path = tmp_path / "linksThroughTelegram.numbers"
    append_link(path, "https://www.tiktok.com/t/AAA111/")
    assert not update_processing_note(path, "https://www.tiktok.com/t/MISSING/", "note")
