from pathlib import Path

import pytest

from lib.config import load_config
from lib.log_tail import LogTailError, tail_file, tail_log


def test_tail_file_returns_last_lines(tmp_path):
    log = tmp_path / "test.log"
    log.write_text("\n".join(f"line {i}" for i in range(10)) + "\n")

    assert tail_file(log, lines=3) == ["line 7", "line 8", "line 9"]


def test_tail_file_missing_returns_empty(tmp_path):
    assert tail_file(tmp_path / "missing.log", lines=5) == []


def test_tail_log_allows_existing_run_id(make_config):
    cfg = load_config(make_config())
    run_dir = cfg.paths.work_dir / "abc123"
    run_dir.mkdir(parents=True)
    (run_dir / "run.log").write_text("started\nfinished\n")

    payload = tail_log(cfg, "run:abc123", lines=1)

    assert payload["name"] == "run:abc123"
    assert payload["lines"] == ["finished"]


def test_tail_log_rejects_unknown_or_invalid_run(make_config):
    cfg = load_config(make_config())

    with pytest.raises(LogTailError):
        tail_log(cfg, "run:../secret")

    with pytest.raises(LogTailError):
        tail_log(cfg, "unknown")
