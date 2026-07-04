"""Worker dry-run + idempotency — no network, no tools, no state writes."""
import json

import worker
from lib.state import load_state, mark_processed

URL = "https://www.tiktok.com/@me/video/123456"


def run_worker(capsys, *argv) -> tuple[int, dict]:
    code = worker.main(list(argv))
    out = capsys.readouterr().out.strip()
    return code, json.loads(out)


def test_dry_run_reports_plan_and_gaps_without_side_effects(
        make_config, tmp_path, capsys):
    config_path = make_config()
    code, result = run_worker(
        capsys, "--url", URL, "--config", str(config_path), "--dry-run")

    assert code == 0
    assert result["status"] == "dry_run"
    assert result["id"] == "123456"
    assert result["avatar_frame_provider"] == "local_comfyui"
    assert result["animation_provider"] == "local_comfyui"
    assert len(result["steps"]) == 15  # prepare (FR-2/3) + GATE 1 + generate_avatar (FR-4) + GATE 2 + reject_frame + animate (FR-5..8) + regenerate

    # setup gaps are surfaced as warnings, not failures
    warning_text = " ".join(result["warnings"])
    assert "LoRA" in warning_text
    assert "avatar reference" in warning_text

    # nothing was created or recorded
    assert not (tmp_path / "work").exists()
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "processed.json").exists()
    assert not (tmp_path / "done.csv").exists()


def test_dry_run_warns_on_wavespeed_misconfiguration(
        make_config, capsys, monkeypatch):
    monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
    config_path = make_config(
        animation={"provider": "wavespeed"},
        wavespeed={"enabled": True, "model": ""},
    )
    code, result = run_worker(
        capsys, "--url", URL, "--config", str(config_path), "--dry-run")
    assert code == 0
    warning_text = " ".join(result["warnings"])
    assert "wavespeed.model" in warning_text
    assert "WAVESPEED_API_KEY" in warning_text


def test_dry_run_warns_on_missing_cookies_file(make_config, capsys, tmp_path):
    config_path = make_config(video={"cookies_file": "./tiktok_cookies.txt"})
    code, result = run_worker(
        capsys, "--url", URL, "--config", str(config_path), "--dry-run")
    assert code == 0
    warning_text = " ".join(result["warnings"])
    assert "video.cookies_file is set but missing" in warning_text
    assert "tiktok_cookies.txt" in warning_text


def test_already_processed_id_is_a_clean_noop(make_config, tmp_path, capsys):
    """Idempotency (§5.5): a processed id exits 0 without re-running anything."""
    config_path = make_config()
    mark_processed(tmp_path / "processed.json", "123456")

    code, result = run_worker(capsys, "--url", URL, "--config", str(config_path))

    assert code == 0
    assert result["status"] == "already_processed"
    assert result["previous_status"] == "processed"
    # state unchanged
    assert load_state(tmp_path / "processed.json")["processed"] == ["123456"]


def test_explicit_id_overrides_url_extraction(make_config, capsys):
    config_path = make_config()
    code, result = run_worker(
        capsys, "--id", "custom42", "--url", URL,
        "--config", str(config_path), "--dry-run")
    assert code == 0
    assert result["id"] == "custom42"
