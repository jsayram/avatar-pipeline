"""Media command construction (FR-2/3/6/7) — pure, no tools executed."""
from pathlib import Path

from lib.media import (
    build_download_cmd,
    build_first_frame_cmd,
    build_sample_frames_cmd,
    build_strip_cmds,
    build_trim_cmd,
    round_down_to_16,
    safe_clip_seconds,
)


def test_download_cmd_targets_fixed_mp4():
    cmd = build_download_cmd("https://tiktok.com/@u/video/1", Path("/w/1/ref_raw.mp4"))
    assert cmd[0] == "yt-dlp"
    assert "--no-playlist" in cmd
    assert "/w/1/ref_raw.mp4" in cmd
    assert cmd[-1] == "https://tiktok.com/@u/video/1"
    assert "--cookies" not in cmd


def test_download_cmd_omits_cookies_by_default():
    cmd = build_download_cmd("https://tiktok.com/@u/video/1", Path("/w/1/ref_raw.mp4"))
    assert "--cookies" not in cmd


def test_download_cmd_includes_cookies_file_when_set():
    cmd = build_download_cmd(
        "https://tiktok.com/@u/video/1", Path("/w/1/ref_raw.mp4"),
        cookies_file=Path("/fake/tiktok_cookies.txt"),
    )
    assert "--cookies" in cmd
    idx = cmd.index("--cookies")
    assert cmd[idx + 1] == "/fake/tiktok_cookies.txt"
    assert cmd[-1] == "https://tiktok.com/@u/video/1"


def test_first_frame_cmd():
    cmd = build_first_frame_cmd(Path("ref.mp4"), Path("frame1.png"))
    assert cmd[:2] == ["ffmpeg", "-y"]
    assert cmd[cmd.index("-frames:v") + 1] == "1"
    # -update 1: tells image2 explicitly this is a single still frame, not an
    # image-sequence pattern — avoids relying on ffmpeg's deprecated fallback
    assert cmd[cmd.index("-update") + 1] == "1"


def test_trim_cmd_reencodes_under_provider_cap():
    cmd = build_trim_cmd(Path("in.mp4"), Path("out.mp4"), 12)
    assert cmd[cmd.index("-t") + 1] == "11.95"
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-c" not in cmd


def test_safe_clip_seconds_leaves_small_margin():
    assert safe_clip_seconds(10) == 9.95


def test_sample_frames_cmd_uses_configured_fps():
    cmd = build_sample_frames_cmd(Path("v.mp4"), "/tmp/f_%03d.png", 2)
    assert "fps=2" in cmd


def test_strip_cmds_match_spec_fr7():
    """FR-7: ffmpeg container scrub then exiftool belt-and-suspenders."""
    ffmpeg_cmd, exiftool_cmd = build_strip_cmds(Path("raw.mp4"), Path("clean.mp4"))
    assert ffmpeg_cmd == [
        "ffmpeg", "-y", "-i", "raw.mp4",
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-c:v", "copy", "-c:a", "copy",
        "clean.mp4",
    ]
    assert exiftool_cmd == ["exiftool", "-all=", "-overwrite_original", "clean.mp4"]


def test_round_down_to_16_for_wan_dims():
    assert round_down_to_16(1080) == 1072
    assert round_down_to_16(1920) == 1920
    assert round_down_to_16(17) == 16
    assert round_down_to_16(3) == 16  # floor at the minimum valid dim
