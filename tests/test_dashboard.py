from pathlib import Path
import time

from fastapi.testclient import TestClient

import dashboard
from lib.config import load_config
from lib.config_overrides import overrides_path, write_provider_overrides
from lib.approval_lock import try_claim
from lib.pending import save_pending
from lib.state import load_state, mark_flagged, mark_processed


def wait_job(client, job_id):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        payload = client.get(f"/api/jobs/{job_id}").json()
        if payload["state"] in {"done", "error"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_status_endpoint_adds_safe_output_media_url(monkeypatch, make_config):
    cfg = load_config(make_config())
    output = cfg.paths.out_dir / "video-out" / "abc123.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"mp4")

    def fake_rows(current_cfg):
        return [{
            "url": "https://example.com",
            "note": "",
            "processing_note": "",
            "status": "published",
            "id": "abc123",
            "identity_cosine": "0.91",
            "output_path": str(output),
            "date": "2026-07-03",
        }]

    monkeypatch.setattr(dashboard, "build_status_rows", fake_rows)
    client = TestClient(dashboard.create_app(make_config()))

    payload = client.get("/api/status").json()

    assert payload["rows"][0]["output_media_url"] == "/api/media/out/video-out/abc123.mp4"


def test_pending_endpoint_exposes_only_work_media(make_config):
    config_path = make_config()
    cfg = load_config(config_path)
    run = cfg.paths.work_dir / "abc123"
    run.mkdir(parents=True)
    frame = run / "frame1.png"
    avatar = run / "avatar.png"
    video = run / "ref.mp4"
    frame.write_bytes(b"png")
    avatar.write_bytes(b"png")
    video.write_bytes(b"mp4")
    save_pending(
        cfg.paths.work_dir,
        "abc123",
        stage="avatar",
        url="https://example.com",
        ref_video_path=str(video),
        frame1_path=str(frame),
        avatar_frame_path=str(avatar),
    )
    client = TestClient(dashboard.create_app(config_path))

    payload = client.get("/api/pending").json()["pending"]

    assert payload["frame1_url"] == "/api/media/work/abc123/frame1.png"
    assert payload["avatar_frame_url"] == "/api/media/work/abc123/avatar.png"
    assert payload["ref_video_url"] == "/api/media/work/abc123/ref.mp4"
    assert client.get(payload["frame1_url"]).status_code == 200


def test_media_routes_reject_traversal_and_disallowed_extensions(make_config, tmp_path):
    config_path = make_config()
    cfg = load_config(config_path)
    run = cfg.paths.work_dir / "abc123"
    run.mkdir(parents=True)
    (run / "notes.txt").write_text("private")
    (tmp_path / "secret.png").write_bytes(b"secret")
    client = TestClient(dashboard.create_app(config_path))

    assert client.get("/api/media/work/abc123/notes.txt").status_code == 404
    assert client.get("/api/media/out/%2E%2E/secret.png").status_code == 404


def test_logs_endpoint_uses_allowlist(make_config):
    config_path = make_config()
    cfg = load_config(config_path)
    run = cfg.paths.work_dir / "abc123"
    run.mkdir(parents=True)
    (run / "run.log").write_text("one\ntwo\n")
    client = TestClient(dashboard.create_app(config_path))

    payload = client.get("/api/logs/run:abc123?lines=1").json()

    assert payload["lines"] == ["two"]
    assert client.get("/api/logs/unknown").status_code == 404


def test_submit_link_queues_prepare_and_archives(monkeypatch, make_config):
    config_path = make_config()
    calls = []

    def fake_run_prepare(cfg, tiktok_id, url, work, logger):
        calls.append(("prepare", tiktok_id, url))
        return {
            "status": "pending_approval",
            "id": tiktok_id,
            "url": url,
            "stage": "frame",
            "processing_note": "trimmed to 9.95s",
        }, 0

    monkeypatch.setattr(dashboard.worker, "run_prepare", fake_run_prepare)
    monkeypatch.setattr(dashboard, "append_link",
                        lambda path, url: calls.append(("archive", str(path), url)))
    monkeypatch.setattr(dashboard, "update_processing_note",
                        lambda path, url, note: calls.append(("note", str(path), url, note)))
    client = TestClient(dashboard.create_app(config_path))

    response = client.post("/api/links", json={"url": "https://www.tiktok.com/t/ABC123/"})

    assert response.status_code == 202
    job = wait_job(client, response.json()["id"])
    assert job["state"] == "done"
    assert ("prepare", "ABC123", "https://www.tiktok.com/t/ABC123/") in calls
    assert any(call[0] == "archive" for call in calls)
    assert any(call[0] == "note" and call[3] == "trimmed to 9.95s" for call in calls)


def test_submit_link_rejects_when_pending_exists(make_config):
    config_path = make_config()
    cfg = load_config(config_path)
    save_pending(cfg.paths.work_dir, "abc123", stage="frame", url="https://example.com",
                 ref_video_path="/w/ref.mp4", frame1_path="/w/frame.png")
    client = TestClient(dashboard.create_app(config_path))

    response = client.post("/api/links", json={"url": "https://www.tiktok.com/t/NEW123/"})

    assert response.status_code == 409


def test_run_next_queues_next_sheet_url(monkeypatch, make_config):
    config_path = make_config()
    calls = []

    monkeypatch.setattr(dashboard.pick_next, "ensure_local_copy",
                        lambda numbers_path, logger: calls.append(("ensure", str(numbers_path))))
    monkeypatch.setattr(dashboard.pick_next, "read_urls",
                        lambda numbers_path: ["https://www.tiktok.com/t/NEXT123/"])

    def fake_run_prepare(cfg, tiktok_id, url, work, logger):
        calls.append(("prepare", tiktok_id, url))
        return {"status": "pending_approval", "id": tiktok_id, "stage": "frame"}, 0

    monkeypatch.setattr(dashboard.worker, "run_prepare", fake_run_prepare)
    client = TestClient(dashboard.create_app(config_path))

    response = client.post("/api/queue/run-next")

    assert response.status_code == 202
    job = wait_job(client, response.json()["id"])
    assert job["state"] == "done"
    assert ("prepare", "NEXT123", "https://www.tiktok.com/t/NEXT123/") in calls


def test_run_next_returns_204_when_queue_empty(monkeypatch, make_config):
    config_path = make_config()
    monkeypatch.setattr(dashboard.pick_next, "ensure_local_copy", lambda numbers_path, logger: None)
    monkeypatch.setattr(dashboard.pick_next, "read_urls", lambda numbers_path: [])
    client = TestClient(dashboard.create_app(config_path))

    response = client.post("/api/queue/run-next")

    assert response.status_code == 204


def test_decision_endpoint_maps_frame_yes_to_generate_avatar(monkeypatch, make_config):
    config_path = make_config()
    cfg = load_config(config_path)
    save_pending(cfg.paths.work_dir, "abc123", stage="frame", url="https://example.com",
                 ref_video_path="/w/ref.mp4", frame1_path="/w/frame.png")
    calls = []

    monkeypatch.setattr(dashboard.worker, "_notify",
                        lambda cfg, text, logger: calls.append(("notify", text)))
    monkeypatch.setattr(
        dashboard.worker,
        "run_generate_avatar",
        lambda cfg, tiktok_id, work, logger: (
            calls.append(("generate_avatar", tiktok_id))
            or {"status": "pending_approval", "id": tiktok_id, "stage": "avatar"},
            0,
        ),
    )
    client = TestClient(dashboard.create_app(config_path))

    response = client.post("/api/pending/abc123/decision",
                           json={"stage": "frame", "decision": "yes"})

    assert response.status_code == 202
    job = wait_job(client, response.json()["id"])
    assert job["state"] == "done"
    assert ("generate_avatar", "abc123") in calls
    assert not (cfg.paths.work_dir / "abc123" / ".approval_action.lock").exists()


def test_decision_endpoint_rejects_stage_mismatch(make_config):
    config_path = make_config()
    cfg = load_config(config_path)
    save_pending(cfg.paths.work_dir, "abc123", stage="avatar", url="https://example.com",
                 ref_video_path="/w/ref.mp4", frame1_path="/w/frame.png",
                 avatar_frame_path="/w/avatar.png")
    client = TestClient(dashboard.create_app(config_path))

    response = client.post("/api/pending/abc123/decision",
                           json={"stage": "frame", "decision": "yes"})

    assert response.status_code == 409


def test_decision_endpoint_rejects_claim_held(make_config):
    config_path = make_config()
    cfg = load_config(config_path)
    save_pending(cfg.paths.work_dir, "abc123", stage="frame", url="https://example.com",
                 ref_video_path="/w/ref.mp4", frame1_path="/w/frame.png")
    try_claim(cfg.paths.work_dir, "abc123", "telegram")
    client = TestClient(dashboard.create_app(config_path))

    response = client.post("/api/pending/abc123/decision",
                           json={"stage": "frame", "decision": "yes"})

    assert response.status_code == 409
    assert "telegram" in response.json()["detail"]


def test_unflag_endpoint_removes_flagged_state(make_config):
    config_path = make_config()
    cfg = load_config(config_path)
    mark_processed(cfg.paths.processed_json, "processed-id")
    mark_flagged(cfg.paths.processed_json, "flagged-id")
    client = TestClient(dashboard.create_app(config_path))

    response = client.post("/api/flagged/flagged-id/unflag")

    assert response.status_code == 200
    state = load_state(cfg.paths.processed_json)
    assert state["processed"] == ["processed-id"]
    assert state["flagged"] == []


def test_provider_endpoint_reports_base_and_effective_values(make_config):
    config_path = make_config()
    client = TestClient(dashboard.create_app(config_path))

    payload = client.get("/api/config/providers").json()

    assert payload["effective"]["avatar_frame_provider"] == "local_comfyui"
    assert payload["effective"] == payload["base"]
    assert payload["overlay_exists"] is False
    assert payload["overridden"]["avatar_frame_provider"] is False
    assert "wavespeed_seedream" in payload["options"]["avatar_frame_provider"]


def test_wavespeed_balance_endpoint_returns_cached_balance(monkeypatch, make_config):
    config_path = make_config(wavespeed={"enabled": True})
    calls = []

    monkeypatch.setenv("WAVESPEED_API_KEY", "test-key")

    def fake_get_balance(cfg):
        calls.append(cfg.wavespeed.api_base)
        return 42.5

    monkeypatch.setattr(dashboard, "get_balance", fake_get_balance)
    client = TestClient(dashboard.create_app(config_path))

    first = client.get("/api/wavespeed/balance").json()
    second = client.get("/api/wavespeed/balance").json()

    assert first["ok"] is True
    assert first["enabled"] is True
    assert first["configured"] is True
    assert first["balance"] == 42.5
    assert first["dashboard_url"] == dashboard.WAVESPEED_DASHBOARD_URL
    assert second["balance"] == 42.5
    assert calls == ["https://api.wavespeed.ai"]


def test_wavespeed_balance_endpoint_reports_missing_key(monkeypatch, make_config):
    config_path = make_config()
    monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
    monkeypatch.setattr(
        dashboard,
        "get_balance",
        lambda cfg: (_ for _ in ()).throw(AssertionError("should not call WaveSpeed")),
    )
    client = TestClient(dashboard.create_app(config_path))

    response = client.get("/api/wavespeed/balance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["configured"] is False
    assert payload["balance"] is None
    assert "WAVESPEED_API_KEY" in payload["detail"]


def test_wavespeed_balance_endpoint_reports_provider_error(monkeypatch, make_config):
    config_path = make_config()
    monkeypatch.setenv("WAVESPEED_API_KEY", "test-key")

    def fake_get_balance(cfg):
        raise dashboard.WaveSpeedBalanceError("balance endpoint returned 401")

    monkeypatch.setattr(dashboard, "get_balance", fake_get_balance)
    client = TestClient(dashboard.create_app(config_path))

    response = client.get("/api/wavespeed/balance")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["configured"] is True
    assert payload["balance"] is None
    assert payload["detail"] == "balance endpoint returned 401"


def test_provider_put_writes_overlay_and_changes_effective_config(make_config):
    config_path = make_config()
    client = TestClient(dashboard.create_app(config_path))

    response = client.put("/api/config/providers", json={
        "avatar_frame_provider": "wavespeed_seedream",
        "animation_provider": "wavespeed",
        "wavespeed_enabled": True,
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["overlay_exists"] is True
    assert payload["effective"]["avatar_frame_provider"] == "wavespeed_seedream"
    assert payload["effective"]["animation_provider"] == "wavespeed"
    assert payload["effective"]["wavespeed_enabled"] is True
    assert payload["base"]["avatar_frame_provider"] == "local_comfyui"
    cfg = load_config(config_path)
    assert cfg.avatar_frame.provider == "wavespeed_seedream"
    assert cfg.animation.provider == "wavespeed"
    assert cfg.wavespeed.enabled is True


def test_provider_put_invalid_value_rolls_back_previous_overlay(make_config):
    config_path = make_config()
    write_provider_overrides(
        config_path,
        avatar_frame_provider="mock",
        animation_provider="mock",
        wavespeed_enabled=False,
    )
    client = TestClient(dashboard.create_app(config_path))

    response = client.put("/api/config/providers", json={
        "avatar_frame_provider": "bad-provider",
        "animation_provider": "mock",
        "wavespeed_enabled": False,
    })

    assert response.status_code == 422
    cfg = load_config(config_path)
    assert cfg.avatar_frame.provider == "mock"
    assert cfg.animation.provider == "mock"


def test_provider_delete_reverts_to_config_yaml(make_config):
    config_path = make_config()
    write_provider_overrides(
        config_path,
        avatar_frame_provider="mock",
        animation_provider="mock",
        wavespeed_enabled=False,
    )
    client = TestClient(dashboard.create_app(config_path))

    response = client.delete("/api/config/providers")

    assert response.status_code == 200
    assert response.json()["overlay_exists"] is False
    assert not overrides_path(config_path).exists()
    cfg = load_config(config_path)
    assert cfg.avatar_frame.provider == "local_comfyui"
    assert cfg.animation.provider == "local_comfyui"
