"""Pending-approval state persistence (the two-gate human-review handoff)."""
import pytest

from lib.pending import (
    PendingApprovalError,
    clear_pending,
    find_pending_id,
    load_pending,
    save_pending,
)


def test_save_and_load_roundtrip(tmp_path):
    save_pending(tmp_path, "abc123", stage="frame", url="https://t/1",
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png")
    data = load_pending(tmp_path, "abc123")
    assert data["id"] == "abc123"
    assert data["stage"] == "frame"
    assert data["url"] == "https://t/1"
    assert data["ref_video_path"] == "/w/ref.mp4"
    assert data["frame1_path"] == "/w/frame1.png"
    assert data["avatar_frame_path"] is None
    assert data["attempt"] == 1
    assert "updated_at" in data


def test_save_at_avatar_stage_includes_avatar_frame_path(tmp_path):
    save_pending(tmp_path, "abc123", stage="avatar", url="https://t/1",
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png",
                avatar_frame_path="/w/avatar.png", attempt=1,
                processing_note="trimmed for 10s limit")
    data = load_pending(tmp_path, "abc123")
    assert data["stage"] == "avatar"
    assert data["avatar_frame_path"] == "/w/avatar.png"
    assert data["processing_note"] == "trimmed for 10s limit"


def test_save_overwrites_previous_attempt(tmp_path):
    save_pending(tmp_path, "abc123", stage="avatar", url="https://t/1",
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png",
                avatar_frame_path="/w/v1.png", attempt=1)
    save_pending(tmp_path, "abc123", stage="avatar", url="https://t/1",
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png",
                avatar_frame_path="/w/v2.png", attempt=2)
    data = load_pending(tmp_path, "abc123")
    assert data["attempt"] == 2
    assert data["avatar_frame_path"] == "/w/v2.png"


def test_load_missing_raises_clear_error(tmp_path):
    with pytest.raises(PendingApprovalError, match="no pending approval"):
        load_pending(tmp_path, "nonexistent")


def test_clear_pending_removes_file(tmp_path):
    save_pending(tmp_path, "abc123", stage="frame", url="https://t/1",
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png")
    clear_pending(tmp_path, "abc123")
    with pytest.raises(PendingApprovalError):
        load_pending(tmp_path, "abc123")


def test_clear_pending_on_nonexistent_is_a_noop(tmp_path):
    clear_pending(tmp_path, "never-existed")  # must not raise


def test_find_pending_id_with_exactly_one(tmp_path):
    save_pending(tmp_path, "onlyone", stage="frame", url="https://t/1",
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png")
    assert find_pending_id(tmp_path) == "onlyone"


def test_find_pending_id_with_none_raises(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(PendingApprovalError, match="no pending approval is currently outstanding"):
        find_pending_id(tmp_path)


def test_find_pending_id_with_multiple_raises(tmp_path):
    save_pending(tmp_path, "first", stage="frame", url="https://t/1",
                ref_video_path="/w/ref.mp4", frame1_path="/w/frame1.png")
    save_pending(tmp_path, "second", stage="frame", url="https://t/2",
                ref_video_path="/w/ref2.mp4", frame1_path="/w/frame1b.png")
    with pytest.raises(PendingApprovalError, match="multiple pending approvals"):
        find_pending_id(tmp_path)


def test_find_pending_id_missing_work_dir_raises(tmp_path):
    with pytest.raises(PendingApprovalError, match="does not exist"):
        find_pending_id(tmp_path / "nope")
