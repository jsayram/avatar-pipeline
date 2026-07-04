"""image-out / video-out — the operator-facing deliverables.

video-out is a flat directory: whether an attempt passed the identity gate
(published) or failed it, the video lands in the same place, distinguished
only by filename/timestamp — see done.csv for pass/fail status per id.

animate_and_gate and strip_and_publish are stubbed out entirely in
test_worker_phases.py (phase-orchestration tests), so the actual
image/video-out copy logic living inside them needs its own direct coverage
here.
"""
import logging

import pytest

import worker
from lib.config import load_config

LOGGER = logging.getLogger("test")


class FakeProvider:
    name = "fake"

    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls = 0

    def animate(self, avatar_frame_path, reference_video_path, output_path, config, seed=None):
        self.calls += 1
        output_path.write_bytes(b"fake raw video")
        return output_path


def test_animate_and_gate_saves_every_failed_attempt_to_video_out(
        make_config, tmp_path, monkeypatch):
    cfg = load_config(make_config(identity={"cosine_min": 0.88, "sample_fps": 1, "max_retries": 2}))
    work = tmp_path / "work" / "id1"
    work.mkdir(parents=True)
    avatar_frame = work / "avatar_frame1.png"
    avatar_frame.write_bytes(b"fake avatar")
    ref = work / "ref.mp4"
    ref.write_bytes(b"fake ref")

    monkeypatch.setattr(worker, "get_animation_provider", lambda cfg, logger: FakeProvider())
    monkeypatch.setattr(worker, "gate_scores", lambda cfg, video, frames_dir, logger: [0.1])

    with pytest.raises(worker.StageFailure):
        worker.animate_and_gate(cfg, "id1", avatar_frame, ref, work, LOGGER)

    saved = sorted(cfg.paths.video_out_dir.glob("id1-video-*.mp4"))
    assert len(saved) == cfg.identity.max_retries + 1  # every attempt failed and got kept


def test_animate_and_gate_does_not_save_anything_on_pass(
        make_config, tmp_path, monkeypatch):
    """A passing attempt is strip_and_publish's job to save, not
    animate_and_gate's — the inline save-to-video-out only fires on FAIL."""
    cfg = load_config(make_config(identity={"cosine_min": 0.5, "sample_fps": 1, "max_retries": 2}))
    work = tmp_path / "work" / "id2"
    work.mkdir(parents=True)
    avatar_frame = work / "avatar_frame1.png"
    avatar_frame.write_bytes(b"fake avatar")
    ref = work / "ref.mp4"
    ref.write_bytes(b"fake ref")

    monkeypatch.setattr(worker, "get_animation_provider", lambda cfg, logger: FakeProvider())
    monkeypatch.setattr(worker, "gate_scores", lambda cfg, video, frames_dir, logger: [0.9])

    raw, mean, attempts = worker.animate_and_gate(cfg, "id2", avatar_frame, ref, work, LOGGER)
    assert attempts == 1
    assert mean == 0.9

    assert not cfg.paths.video_out_dir.exists() or not list(cfg.paths.video_out_dir.glob("*.mp4"))


def test_strip_and_publish_writes_to_video_out(make_config, tmp_path, monkeypatch):
    cfg = load_config(make_config())
    work = tmp_path / "work" / "id3"
    work.mkdir(parents=True)
    raw = work / "avatar_raw_attempt1.mp4"
    raw.write_bytes(b"fake raw video")
    # build_strip_cmds' real ffmpeg/exiftool commands would produce this;
    # stub run_cmd to a no-op and pre-create the file they'd have written.
    (work / "avatar_video.mp4").write_bytes(b"stripped video")
    monkeypatch.setattr(worker, "run_cmd", lambda cmd, logger: None)

    dest = worker.strip_and_publish(cfg, "id3", "https://example.com", raw, work, 0.95, LOGGER)

    assert dest.parent == cfg.paths.video_out_dir
    assert dest.name.startswith("id3-video-")
    assert dest.suffix == ".mp4"
    assert dest.exists()


def test_run_animate_saves_approved_still_to_image_out(make_config, tmp_path, monkeypatch):
    from lib.pending import save_pending

    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "123"}))
    work = tmp_path / "work" / "id4"
    work.mkdir(parents=True)
    avatar_frame = work / "avatar_frame1.png"
    avatar_frame.write_bytes(b"fake avatar")
    ref = work / "ref.mp4"
    ref.write_bytes(b"fake ref")
    frame1 = work / "frame1.png"
    frame1.write_bytes(b"fake frame1")
    save_pending(tmp_path / "work", "id4", stage="avatar", url="https://example.com",
                ref_video_path=str(ref), frame1_path=str(frame1),
                avatar_frame_path=str(avatar_frame), attempt=1)

    def fake_animate_and_gate(cfg, tiktok_id, avatar_frame, ref, work, logger):
        raw = work / "avatar_raw_attempt1.mp4"
        raw.write_bytes(b"fake raw video")
        return raw, 0.95, 1

    def fake_strip_and_publish(cfg, tiktok_id, url, raw_video, work, mean, logger):
        dest = cfg.paths.video_out_dir / f"{tiktok_id}-video-fake.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake published video")
        worker.mark_processed(cfg.paths.processed_json, tiktok_id)
        return dest

    monkeypatch.setattr(worker, "animate_and_gate", fake_animate_and_gate)
    monkeypatch.setattr(worker, "strip_and_publish", fake_strip_and_publish)
    monkeypatch.setattr(worker, "_cap_reference_video",
                        lambda cfg, ref, work, logger, url="": ref)
    monkeypatch.setattr(worker, "send_message", lambda cfg, text, logger=None: None)
    monkeypatch.setattr(worker, "send_video", lambda cfg, video_path, caption, logger=None: None)

    worker.run_animate(cfg, "id4", work, LOGGER)

    saved = list(cfg.paths.image_out_dir.glob("id4-image-*.png"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == b"fake avatar"


def test_run_animate_sends_published_video_via_telegram(make_config, tmp_path, monkeypatch):
    from lib.pending import save_pending

    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "123"}))
    work = tmp_path / "work" / "id5"
    work.mkdir(parents=True)
    avatar_frame = work / "avatar_frame1.png"
    avatar_frame.write_bytes(b"fake avatar")
    ref = work / "ref.mp4"
    ref.write_bytes(b"fake ref")
    frame1 = work / "frame1.png"
    frame1.write_bytes(b"fake frame1")
    save_pending(tmp_path / "work", "id5", stage="avatar", url="https://example.com",
                ref_video_path=str(ref), frame1_path=str(frame1),
                avatar_frame_path=str(avatar_frame), attempt=1)

    def fake_animate_and_gate(cfg, tiktok_id, avatar_frame, ref, work, logger):
        raw = work / "avatar_raw_attempt1.mp4"
        raw.write_bytes(b"fake raw video")
        return raw, 0.95, 1

    def fake_strip_and_publish(cfg, tiktok_id, url, raw_video, work, mean, logger):
        dest = cfg.paths.video_out_dir / f"{tiktok_id}-video-fake.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake published video")
        worker.mark_processed(cfg.paths.processed_json, tiktok_id)
        return dest

    sent_videos = []

    monkeypatch.setattr(worker, "animate_and_gate", fake_animate_and_gate)
    monkeypatch.setattr(worker, "strip_and_publish", fake_strip_and_publish)
    monkeypatch.setattr(worker, "_cap_reference_video",
                        lambda cfg, ref, work, logger, url="": ref)
    monkeypatch.setattr(worker, "send_message", lambda cfg, text, logger=None: None)
    monkeypatch.setattr(
        worker, "send_video",
        lambda cfg, video_path, caption, logger=None: sent_videos.append((video_path, caption)),
    )

    worker.run_animate(cfg, "id5", work, LOGGER)

    assert len(sent_videos) == 1
    video_path, caption = sent_videos[0]
    assert video_path.name == "id5-video-fake.mp4"
    assert "published" in caption


def test_run_animate_sends_failed_video_via_telegram_on_flag(make_config, tmp_path, monkeypatch):
    from lib.pending import save_pending

    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "123"}))
    work = tmp_path / "work" / "id6"
    work.mkdir(parents=True)
    avatar_frame = work / "avatar_frame1.png"
    avatar_frame.write_bytes(b"fake avatar")
    ref = work / "ref.mp4"
    ref.write_bytes(b"fake ref")
    frame1 = work / "frame1.png"
    frame1.write_bytes(b"fake frame1")
    save_pending(tmp_path / "work", "id6", stage="avatar", url="https://example.com",
                ref_video_path=str(ref), frame1_path=str(frame1),
                avatar_frame_path=str(avatar_frame), attempt=1)

    # simulate what the REAL animate_and_gate already does internally before
    # raising: save the one failed attempt's video into video_out_dir.
    cfg.paths.video_out_dir.mkdir(parents=True, exist_ok=True)
    failed_video = cfg.paths.video_out_dir / "id6-video-20260101T000000000000Z.mp4"
    failed_video.write_bytes(b"fake failed video")

    def fake_animate_and_gate(cfg, tiktok_id, avatar_frame, ref, work, logger):
        raise worker.StageFailure("identity_gate", "identity below threshold", cosine=0.51)

    sent_videos = []

    monkeypatch.setattr(worker, "animate_and_gate", fake_animate_and_gate)
    monkeypatch.setattr(worker, "_cap_reference_video",
                        lambda cfg, ref, work, logger, url="": ref)
    monkeypatch.setattr(worker, "send_message", lambda cfg, text, logger=None: None)
    monkeypatch.setattr(
        worker, "send_video",
        lambda cfg, video_path, caption, logger=None: sent_videos.append((video_path, caption)),
    )

    result, code = worker.run_animate(cfg, "id6", work, LOGGER)

    assert result["status"] == "flagged"
    assert len(sent_videos) == 1
    video_path, caption = sent_videos[0]
    assert video_path == failed_video
    assert "0.51" in caption
    assert "flagged" in caption
