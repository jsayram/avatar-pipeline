"""Disk hygiene: disposable per-attempt scratch files get removed from
work/<id>/ once a run reaches any terminal outcome, so they don't
accumulate forever (raw pre-strip videos and gate-check frames are
typically 90%+ of a run's footprint and are pure duplicates of what's
already durable in out-pipe/video-out/)."""
import logging

from lib.config import load_config
from worker import _cleanup_work_dir

LOGGER = logging.getLogger("test")


def _make_disposable_files(work):
    work.mkdir(parents=True, exist_ok=True)
    (work / "avatar_raw_attempt1.mp4").write_bytes(b"x" * 1000)
    (work / "avatar_raw_attempt2.mp4").write_bytes(b"x" * 1000)
    (work / "ref_raw.mp4").write_bytes(b"x" * 1000)
    frames = work / "gate_frames_attempt1"
    frames.mkdir()
    (frames / "f_001.png").write_bytes(b"x" * 1000)
    (frames / "f_002.png").write_bytes(b"x" * 1000)


def _make_keepers(work):
    work.mkdir(parents=True, exist_ok=True)
    (work / "run.log").write_text("log line\n")
    (work / "ref.mp4").write_bytes(b"x" * 500)
    (work / "frame1.png").write_bytes(b"x" * 500)
    (work / "avatar_frame1.png").write_bytes(b"x" * 500)


def test_cleanup_removes_disposable_files_keeps_essentials(make_config, tmp_path):
    cfg = load_config(make_config())
    work = tmp_path / "work" / "id1"
    _make_disposable_files(work)
    _make_keepers(work)

    _cleanup_work_dir(cfg, "id1", work, LOGGER)

    assert not (work / "avatar_raw_attempt1.mp4").exists()
    assert not (work / "avatar_raw_attempt2.mp4").exists()
    assert not (work / "ref_raw.mp4").exists()
    assert not (work / "gate_frames_attempt1").exists()

    assert (work / "run.log").exists()
    assert (work / "ref.mp4").exists()
    assert (work / "frame1.png").exists()
    assert (work / "avatar_frame1.png").exists()


def test_cleanup_disabled_via_config_leaves_everything(make_config, tmp_path):
    cfg = load_config(make_config(cleanup={"enabled": False}))
    work = tmp_path / "work" / "id2"
    _make_disposable_files(work)

    _cleanup_work_dir(cfg, "id2", work, LOGGER)

    assert (work / "avatar_raw_attempt1.mp4").exists()
    assert (work / "gate_frames_attempt1").exists()


def test_cleanup_enabled_by_default(make_config, tmp_path):
    cfg = load_config(make_config())
    assert cfg.cleanup.enabled is True


def test_cleanup_is_a_noop_on_already_clean_dir(make_config, tmp_path):
    """Nothing to remove must not raise."""
    cfg = load_config(make_config())
    work = tmp_path / "work" / "id3"
    _make_keepers(work)
    _cleanup_work_dir(cfg, "id3", work, LOGGER)  # must not raise
    assert (work / "run.log").exists()


def test_cleanup_missing_work_dir_does_not_raise(make_config, tmp_path):
    cfg = load_config(make_config())
    _cleanup_work_dir(cfg, "ghost", tmp_path / "work" / "ghost", LOGGER)
