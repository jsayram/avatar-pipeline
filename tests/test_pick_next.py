""".numbers reader behavior (FR-1) — via fixture rows, no real .numbers needed."""
import pytest

from pick_next import ensure_local_copy, select_next, urls_from_rows


class FakeLogger:
    def info(self, *a, **k):
        pass


ROWS = [
    ("TikTok URL", "note"),                                     # header
    ("https://www.tiktok.com/@me/video/111", "dance"),
    (None, None),                                               # empty row
    ("  https://www.tiktok.com/@me/video/222  ", None),         # whitespace
    ("not a url", "junk"),
    ("https://vm.tiktok.com/ZMshort1/", ""),
]


def test_urls_from_rows_skips_header_junk_and_empties():
    assert urls_from_rows(ROWS) == [
        "https://www.tiktok.com/@me/video/111",
        "https://www.tiktok.com/@me/video/222",
        "https://vm.tiktok.com/ZMshort1/",
    ]


def test_select_next_returns_first_unseen():
    urls = urls_from_rows(ROWS)
    nxt = select_next(urls, skip=set())
    assert nxt == {"id": "111", "url": "https://www.tiktok.com/@me/video/111"}


def test_select_next_skips_processed_and_flagged():
    urls = urls_from_rows(ROWS)
    nxt = select_next(urls, skip={"111", "222"})
    assert nxt == {"id": "ZMshort1", "url": "https://vm.tiktok.com/ZMshort1/"}


def test_select_next_exhausted_is_none():
    urls = urls_from_rows(ROWS)
    assert select_next(urls, skip={"111", "222", "ZMshort1"}) is None


def test_missing_sheet_without_icloud_placeholder_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="numbers sheet not found"):
        ensure_local_copy(tmp_path / "links.numbers", FakeLogger())


def test_existing_sheet_passes_through(tmp_path):
    sheet = tmp_path / "links.numbers"
    sheet.write_bytes(b"stub")
    ensure_local_copy(sheet, FakeLogger())  # no exception
