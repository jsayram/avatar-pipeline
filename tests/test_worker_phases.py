"""--phase prepare/generate_avatar/reject_frame/animate/regenerate — the
two-gate Telegram operator-approval loop.

All heavy steps (download, ffmpeg, avatar-frame generation, animation,
Telegram HTTP calls) are monkeypatched directly on the worker module; these
tests only exercise the phase orchestration logic (state transitions,
pending-approval persistence, attempt counting, flagging).
"""
import json
from pathlib import Path

import pytest

import worker
from lib.pending import load_pending
from lib.state import load_state

URL = "https://www.tiktok.com/@me/video/999888"
TIKTOK_ID = "999888"


def run_worker(capsys, *argv) -> tuple[int, dict]:
    code = worker.main(list(argv))
    out = capsys.readouterr().out.strip()
    return code, json.loads(out)


@pytest.fixture
def stub_pipeline(monkeypatch, tmp_path):
    """Replace every heavy step with a fast, deterministic fake."""
    sent = {"photos": [], "messages": [], "videos": []}

    def fake_download_reference(cfg, url, work, logger):
        ref = work / "ref.mp4"
        ref.write_bytes(b"fake video")
        return ref

    def fake_extract_frame(ref, work, logger):
        frame1 = work / "frame1.png"
        frame1.write_bytes(b"fake frame")
        return frame1

    def fake_make_avatar_frame(cfg, frame1, work, seed, logger):
        avatar = work / f"avatar_frame1_seed{seed}.png"
        avatar.write_bytes(b"fake avatar")
        return avatar

    def fake_animate_and_gate(cfg, tiktok_id, avatar_frame, ref, work, logger):
        raw = work / "avatar_raw_attempt1.mp4"
        raw.write_bytes(b"fake raw video")
        return raw, 0.95, 1

    def fake_strip_and_publish(cfg, tiktok_id, url, raw_video, work, mean, logger):
        # Mirror the real function's state side effect (mark_processed) so
        # tests asserting on processed.json reflect what --phase animate
        # actually delegates to, without re-running real ffmpeg/exiftool.
        dest = tmp_path / "out" / "video-out" / f"{tiktok_id}-video-fake.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake published video")
        worker.mark_processed(cfg.paths.processed_json, tiktok_id)
        return dest

    def fake_send_photo(cfg, image_path, caption, logger=None):
        sent["photos"].append({"image_path": str(image_path), "caption": caption})

    def fake_send_message(cfg, text, logger=None):
        sent["messages"].append(text)

    def fake_send_video(cfg, video_path, caption, logger=None):
        sent["videos"].append({"video_path": str(video_path), "caption": caption})

    monkeypatch.setattr(worker, "download_reference", fake_download_reference)
    monkeypatch.setattr(worker, "extract_frame", fake_extract_frame)
    monkeypatch.setattr(worker, "make_avatar_frame", fake_make_avatar_frame)
    monkeypatch.setattr(worker, "animate_and_gate", fake_animate_and_gate)
    monkeypatch.setattr(worker, "strip_and_publish", fake_strip_and_publish)
    monkeypatch.setattr(worker, "send_photo", fake_send_photo)
    monkeypatch.setattr(worker, "send_message", fake_send_message)
    monkeypatch.setattr(worker, "send_video", fake_send_video)
    # _refresh_status_sheet's real numbers_parser call is a genuine ~1.5s
    # one-time-per-process import cost (verified: 5 calls in one process ->
    # 1.45s, 0.00005s, 0.00003s, 0.00003s, 0.00003s — not a leak, just an
    # inherent library cost, unavoidable in real subprocess-per-phase
    # production use). These tests exercise phase-orchestration logic, not
    # the status sheet (see test_status_sheet.py for that), so there's no
    # reason to pay for it here.
    monkeypatch.setattr(worker, "_refresh_status_sheet", lambda cfg, logger: None)
    return sent


# --------------------------------------------------------------------------
# GATE 1: prepare (download + frame) / reject_frame

def test_prepare_sends_raw_frame_and_saves_pending(make_config, tmp_path, capsys, stub_pipeline):
    config_path = make_config(telegram={"enabled": True, "chat_id": "123"})
    code, result = run_worker(
        capsys, "--phase", "prepare", "--url", URL, "--config", str(config_path))

    assert code == 0
    assert result == {"status": "pending_approval", "id": TIKTOK_ID, "url": URL, "stage": "frame"}
    # only the raw extracted frame is sent — no Seedream call at this gate
    assert len(stub_pipeline["photos"]) == 1
    assert "no cost spent yet" in stub_pipeline["photos"][0]["caption"]

    pending = load_pending(tmp_path / "work", TIKTOK_ID)
    assert pending["stage"] == "frame"
    assert pending["url"] == URL
    assert pending["avatar_frame_path"] is None

    # FR-4/5 must NOT have run yet — no output published, no Seedream call
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "processed.json").exists()


def test_prepare_surfaces_reference_trim_note(
        make_config, tmp_path, capsys, stub_pipeline, monkeypatch):
    note = "Source video was 13.40s, so the reference clip was trimmed to 9.95s."

    def fake_download_reference_with_note(cfg, url, work, logger):
        ref = work / "ref.mp4"
        ref.write_bytes(b"fake video")
        (work / "reference_info.json").write_text(json.dumps({
            "trimmed": True,
            "processing_note": note,
        }))
        return ref

    monkeypatch.setattr(worker, "download_reference", fake_download_reference_with_note)
    config_path = make_config(telegram={"enabled": True, "chat_id": "123"})

    code, result = run_worker(
        capsys, "--phase", "prepare", "--url", URL, "--config", str(config_path))

    assert code == 0
    assert result["processing_note"] == note
    assert note in stub_pipeline["photos"][0]["caption"]
    assert load_pending(tmp_path / "work", TIKTOK_ID)["processing_note"] == note


def test_prepare_frame_failure_does_not_consume_link(
        make_config, tmp_path, capsys, stub_pipeline, monkeypatch):
    def fake_extract_frame(ref, work, logger):
        raise worker.StageFailure("first_frame", "could not extract a usable frame")

    monkeypatch.setattr(worker, "extract_frame", fake_extract_frame)
    config_path = make_config(telegram={"enabled": True, "chat_id": "123"})

    code, result = run_worker(
        capsys, "--phase", "prepare", "--url", URL, "--config", str(config_path))

    assert code == 0
    assert result["status"] == "not_consumed"
    assert result["stage"] == "first_frame"
    state = load_state(tmp_path / "processed.json")
    assert TIKTOK_ID not in state["processed"]
    assert TIKTOK_ID not in state["flagged"]
    assert not (tmp_path / "done.csv").exists()
    with pytest.raises(Exception):
        load_pending(tmp_path / "work", TIKTOK_ID)


def test_prepare_is_noop_if_already_processed(make_config, tmp_path, capsys, stub_pipeline):
    from lib.state import mark_processed
    config_path = make_config(telegram={"enabled": True, "chat_id": "123"})
    mark_processed(tmp_path / "processed.json", TIKTOK_ID)

    code, result = run_worker(
        capsys, "--phase", "prepare", "--url", URL, "--config", str(config_path))
    assert code == 0
    assert result["status"] == "already_processed"
    assert len(stub_pipeline["photos"]) == 0  # never sent — no work to review


def test_reject_frame_clears_pending_without_consuming(make_config, tmp_path, capsys, stub_pipeline):
    config_path = make_config(telegram={"enabled": True, "chat_id": "123"})
    run_worker(capsys, "--phase", "prepare", "--url", URL, "--config", str(config_path))

    code, result = run_worker(
        capsys, "--phase", "reject_frame", "--id", TIKTOK_ID, "--config", str(config_path))
    assert code == 0
    assert result["status"] == "not_consumed"
    assert result["stage"] == "frame_rejected"

    state = load_state(tmp_path / "processed.json")
    assert TIKTOK_ID not in state["processed"]
    assert TIKTOK_ID not in state["flagged"]
    assert not (tmp_path / "done.csv").exists()
    assert any("frame rejected" in m for m in stub_pipeline["messages"])
    with pytest.raises(Exception):
        load_pending(tmp_path / "work", TIKTOK_ID)


def test_reject_frame_with_no_pending_approval_errors(make_config, capsys):
    config_path = make_config()
    code, result = run_worker(
        capsys, "--phase", "reject_frame", "--id", "ghost", "--config", str(config_path))
    assert code == worker.EXIT_INFRA
    assert result["stage"] == "pending_lookup"


# --------------------------------------------------------------------------
# GATE 2: generate_avatar (Seedream) / animate / regenerate

def test_generate_avatar_sends_comparison_and_saves_pending(
        make_config, tmp_path, capsys, stub_pipeline):
    config_path = make_config(telegram={"enabled": True, "chat_id": "123"})
    run_worker(capsys, "--phase", "prepare", "--url", URL, "--config", str(config_path))

    code, result = run_worker(
        capsys, "--phase", "generate_avatar", "--id", TIKTOK_ID, "--config", str(config_path))
    assert code == 0
    assert result == {"status": "pending_approval", "id": TIKTOK_ID, "url": URL,
                       "stage": "avatar", "attempt": 1}
    # prepare sent 1 (raw frame), generate_avatar sends 2 more (comparison + avatar still)
    assert len(stub_pipeline["photos"]) == 3
    assert "for comparison" in stub_pipeline["photos"][1]["caption"]
    assert "attempt 1/3" in stub_pipeline["photos"][2]["caption"]

    pending = load_pending(tmp_path / "work", TIKTOK_ID)
    assert pending["stage"] == "avatar"
    assert pending["avatar_frame_path"] is not None

    # FR-5 must NOT have run yet — no output published
    assert not (tmp_path / "out").exists()
    assert not (tmp_path / "processed.json").exists()


def test_generate_avatar_with_no_pending_approval_errors(make_config, capsys):
    config_path = make_config()
    code, result = run_worker(
        capsys, "--phase", "generate_avatar", "--id", "ghost", "--config", str(config_path))
    assert code == worker.EXIT_INFRA
    assert result["stage"] == "pending_lookup"


def test_animate_requires_id_not_url(make_config, capsys):
    config_path = make_config()
    code, result = run_worker(capsys, "--phase", "animate", "--config", str(config_path))
    assert code == worker.EXIT_INFRA
    assert result["status"] == "error"
    assert "--id" in result["error"]


def test_animate_with_no_pending_approval_errors(make_config, capsys):
    config_path = make_config()
    code, result = run_worker(
        capsys, "--phase", "animate", "--id", "ghost", "--config", str(config_path))
    assert code == worker.EXIT_INFRA
    assert result["stage"] == "pending_lookup"


def test_full_approval_loop(make_config, tmp_path, capsys, stub_pipeline, monkeypatch):
    """prepare -> generate_avatar -> animate, the whole two-gate chain."""
    config_path = make_config(telegram={"enabled": True, "chat_id": "123"})
    monkeypatch.setattr(worker, "_cap_reference_video",
                        lambda cfg, ref, work, logger, url="": ref)

    code, prepare_result = run_worker(
        capsys, "--phase", "prepare", "--url", URL, "--config", str(config_path))
    assert prepare_result["status"] == "pending_approval"
    assert prepare_result["stage"] == "frame"

    code, avatar_result = run_worker(
        capsys, "--phase", "generate_avatar", "--id", TIKTOK_ID, "--config", str(config_path))
    assert avatar_result["status"] == "pending_approval"
    assert avatar_result["stage"] == "avatar"

    code, animate_result = run_worker(
        capsys, "--phase", "animate", "--id", TIKTOK_ID, "--config", str(config_path))
    assert code == 0
    assert animate_result["status"] == "published"
    assert animate_result["cosine"] == 0.95

    # published + state updated + pending cleared + operator notified
    assert load_state(tmp_path / "processed.json")["processed"] == [TIKTOK_ID]
    assert any("published" in m for m in stub_pipeline["messages"])
    # the actual video file, not just a text notice, gets delivered too
    assert len(stub_pipeline["videos"]) == 1
    assert "published" in stub_pipeline["videos"][0]["caption"]
    with pytest.raises(Exception):
        load_pending(tmp_path / "work", TIKTOK_ID)


def test_animate_caps_stale_pending_reference_before_provider_call(
        make_config, tmp_path, capsys, stub_pipeline, monkeypatch):
    config_path = make_config(
        telegram={"enabled": True, "chat_id": "123"},
        video={"max_clip_seconds": 10},
    )
    cfg = worker.load_config(str(config_path))
    work = tmp_path / "work" / TIKTOK_ID
    work.mkdir(parents=True)
    ref = work / "ref.mp4"
    frame1 = work / "frame1.png"
    avatar = work / "avatar.png"
    ref.write_bytes(b"old over-limit video")
    frame1.write_bytes(b"frame")
    avatar.write_bytes(b"avatar")
    from lib.pending import save_pending
    save_pending(tmp_path / "work", TIKTOK_ID, stage="avatar", url=URL,
                 ref_video_path=str(ref), frame1_path=str(frame1),
                 avatar_frame_path=str(avatar), attempt=1)

    def fake_probe(path):
        return 9.94 if Path(path).name == "ref_capped.mp4" else 12.40

    def fake_run_cmd(cmd, logger, dry_run=False, timeout=3600):
        Path(cmd[-1]).write_bytes(b"trimmed reference")

    monkeypatch.setattr(worker, "probe_duration", fake_probe)
    monkeypatch.setattr(worker, "run_cmd", fake_run_cmd)

    code, result = run_worker(capsys, "--phase", "animate", "--id", TIKTOK_ID,
                              "--config", str(config_path))

    assert code == 0
    assert result["status"] == "published"
    assert ref.read_bytes() == b"trimmed reference"
    info = json.loads((work / "reference_info.json").read_text())
    assert info["trimmed"] is True
    assert "10s Kling/WaveSpeed limit" in info["processing_note"]


def test_animate_notifies_exact_duration_cap_error(
        make_config, tmp_path, capsys, stub_pipeline, monkeypatch):
    config_path = make_config(telegram={"enabled": True, "chat_id": "123"})
    work = tmp_path / "work" / TIKTOK_ID
    work.mkdir(parents=True)
    ref = work / "ref.mp4"
    frame1 = work / "frame1.png"
    avatar = work / "avatar.png"
    ref.write_bytes(b"video")
    frame1.write_bytes(b"frame")
    avatar.write_bytes(b"avatar")
    from lib.pending import save_pending
    save_pending(tmp_path / "work", TIKTOK_ID, stage="avatar", url=URL,
                 ref_video_path=str(ref), frame1_path=str(frame1),
                 avatar_frame_path=str(avatar), attempt=1)

    message = (
        "WaveSpeed submit returned 400: For character_orientation 'image', "
        "the video duration must not exceed 10 seconds"
    )

    def fake_animate_and_gate(cfg, tiktok_id, avatar_frame, ref, work, logger):
        raise worker.StageFailure("animate", message)

    monkeypatch.setattr(worker, "_cap_reference_video",
                        lambda cfg, ref, work, logger, url="": ref)
    monkeypatch.setattr(worker, "animate_and_gate", fake_animate_and_gate)

    code, result = run_worker(capsys, "--phase", "animate", "--id", TIKTOK_ID,
                              "--config", str(config_path))

    assert code == 0
    assert result["status"] == "not_consumed"
    assert result["stage"] == "animate"
    assert any("duration must not exceed 10 seconds" in m
               for m in stub_pipeline["messages"])


def test_regenerate_bumps_attempt_and_resends(make_config, tmp_path, capsys, stub_pipeline):
    config_path = make_config(telegram={"enabled": True, "chat_id": "123", "max_approval_attempts": 3})
    run_worker(capsys, "--phase", "prepare", "--url", URL, "--config", str(config_path))
    run_worker(capsys, "--phase", "generate_avatar", "--id", TIKTOK_ID, "--config", str(config_path))

    code, result = run_worker(
        capsys, "--phase", "regenerate", "--id", TIKTOK_ID, "--config", str(config_path))
    assert code == 0
    assert result == {"status": "pending_approval", "id": TIKTOK_ID, "url": URL, "attempt": 2}
    # prepare (1) + generate_avatar (2: comparison + still) + regenerate (2 more) = 5
    assert len(stub_pipeline["photos"]) == 5
    assert "attempt 2/3" in stub_pipeline["photos"][4]["caption"]
    assert load_pending(tmp_path / "work", TIKTOK_ID)["attempt"] == 2
    assert load_pending(tmp_path / "work", TIKTOK_ID)["stage"] == "avatar"


def test_regenerate_clears_without_consuming_after_max_attempts(
        make_config, tmp_path, capsys, stub_pipeline):
    config_path = make_config(telegram={"enabled": True, "chat_id": "123", "max_approval_attempts": 2})
    run_worker(capsys, "--phase", "prepare", "--url", URL, "--config", str(config_path))
    run_worker(capsys, "--phase", "generate_avatar", "--id", TIKTOK_ID, "--config", str(config_path))
    run_worker(capsys, "--phase", "regenerate", "--id", TIKTOK_ID, "--config", str(config_path))

    # attempt is now 2 == max; one more rejection should give up without
    # marking the link processed because no final Kling video exists.
    code, result = run_worker(
        capsys, "--phase", "regenerate", "--id", TIKTOK_ID, "--config", str(config_path))
    assert code == 0
    assert result["status"] == "not_consumed"
    assert result["stage"] == "avatar_frame_rejected"

    state = load_state(tmp_path / "processed.json")
    assert TIKTOK_ID not in state["processed"]
    assert TIKTOK_ID not in state["flagged"]
    assert not (tmp_path / "done.csv").exists()
    assert any("gave up" in m for m in stub_pipeline["messages"])
    with pytest.raises(Exception):
        load_pending(tmp_path / "work", TIKTOK_ID)
