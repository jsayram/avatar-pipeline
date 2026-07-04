"""Sidecar state: dedupe, TikTok id extraction, done.csv (FR-1, FR-8)."""
import csv
import json

from lib.state import (
    append_done_csv,
    extract_tiktok_id,
    load_state,
    mark_flagged,
    mark_processed,
    seen_ids,
    unflag,
)


class TestExtractTiktokId:
    def test_full_video_url(self):
        assert extract_tiktok_id(
            "https://www.tiktok.com/@user/video/7234567890123456789"
        ) == "7234567890123456789"

    def test_url_with_query_string(self):
        assert extract_tiktok_id(
            "https://www.tiktok.com/@user/video/123456?is_from_webapp=1"
        ) == "123456"

    def test_photo_url(self):
        assert extract_tiktok_id("https://www.tiktok.com/@u/photo/99887766") == "99887766"

    def test_vm_short_link(self):
        assert extract_tiktok_id("https://vm.tiktok.com/ZMabcDEF/") == "ZMabcDEF"

    def test_t_short_link(self):
        assert extract_tiktok_id("https://www.tiktok.com/t/ZTabc123/") == "ZTabc123"

    def test_fallback_is_stable_hash(self):
        url = "https://example.com/some/other/link"
        first, second = extract_tiktok_id(url), extract_tiktok_id(url)
        assert first == second
        assert first.startswith("u") and len(first) == 17


class TestProcessedState:
    def test_missing_file_is_empty_state(self, tmp_path):
        state = load_state(tmp_path / "processed.json")
        assert state == {"processed": [], "flagged": []}
        assert seen_ids(state) == set()

    def test_legacy_schema_tolerated(self, tmp_path):
        path = tmp_path / "processed.json"
        path.write_text(json.dumps({"processed": ["a"]}))
        state = load_state(path)
        assert state["processed"] == ["a"]
        assert state["flagged"] == []

    def test_processed_url_dedupe(self, tmp_path):
        path = tmp_path / "processed.json"
        mark_processed(path, "111")
        mark_processed(path, "111")  # idempotent
        mark_flagged(path, "222")
        state = load_state(path)
        assert state["processed"] == ["111"]
        assert state["flagged"] == ["222"]
        # flagged ids are legacy/status-only; only final video outcomes in
        # processed block retries.
        assert seen_ids(state) == {"111"}

    def test_written_file_is_valid_json(self, tmp_path):
        path = tmp_path / "processed.json"
        mark_processed(path, "abc")
        assert json.loads(path.read_text())["processed"] == ["abc"]

    def test_unflag_removes_flagged_id_only(self, tmp_path):
        path = tmp_path / "processed.json"
        mark_processed(path, "111")
        mark_flagged(path, "222")
        unflag(path, "222")

        state = load_state(path)

        assert state["processed"] == ["111"]
        assert state["flagged"] == []


class TestDoneCsv:
    def test_header_written_once_and_rows_append(self, tmp_path):
        path = tmp_path / "done.csv"
        row = {
            "date": "2026-07-02", "id": "1", "url": "https://t/1",
            "output_path": "/out/1.mp4", "identity_cosine": "0.9100",
            "status": "published",
        }
        append_done_csv(path, row)
        append_done_csv(path, {**row, "id": "2", "status": "flagged:identity_gate"})
        with path.open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 2
        assert rows[0]["identity_cosine"] == "0.9100"
        assert rows[1]["status"] == "flagged:identity_gate"
