"""Media operations: yt-dlp download, ffmpeg frame/trim/sample, metadata strip.

Command construction is kept in pure build_* functions so tests can verify the
exact commands (FR-2, FR-3, FR-6 sampling, FR-7 strip) without running tools.
All subprocess output is captured and routed to the logger — never to stdout,
which is reserved for the worker's JSON result.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

TRIM_SAFETY_SECONDS = 0.05


class MediaError(Exception):
    """A media tool failed."""


class ToolMissingError(MediaError):
    """A required binary (yt-dlp/ffmpeg/exiftool) is not installed — a setup
    problem, not a per-URL failure, so the worker must not flag the URL."""


def build_download_cmd(url: str, dest: Path, cookies_file: Path | None = None) -> list[str]:
    """FR-2: yt-dlp the reference video to a fixed mp4 path.

    cookies_file (video.cookies_file in config.yaml): a Netscape-format
    cookies.txt exported from a logged-in browser session, for TikTok posts
    gated behind "this post may not be comfortable for some audiences, log
    in for access" — see docs/TIKTOK-COOKIES.md. Not live browser cookie
    access (--cookies-from-browser), which fails under macOS Full Disk
    Access restrictions for a background daemon's subprocess chain.
    """
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "mp4/best",
        "--no-part",
    ]
    if cookies_file is not None:
        cmd += ["--cookies", str(cookies_file)]
    cmd += ["-o", str(dest), url]
    return cmd


def build_first_frame_cmd(video: Path, dest: Path) -> list[str]:
    """FR-3: extract frame 1 as PNG.

    -update 1 tells the image2 muxer explicitly that this is a single still
    frame, not an image-sequence pattern — newer ffmpeg warns and falls back
    to the same behavior without it, but that fallback is deprecated.
    """
    return ["ffmpeg", "-y", "-i", str(video), "-frames:v", "1", "-update", "1", str(dest)]


def safe_clip_seconds(max_seconds: int | float) -> float:
    """Return a trim target that stays under provider hard duration caps."""
    return max(0.1, float(max_seconds) - TRIM_SAFETY_SECONDS)


def _seconds_arg(seconds: int | float) -> str:
    return f"{float(seconds):.2f}".rstrip("0").rstrip(".")


def build_trim_cmd(src: Path, dest: Path, max_seconds: int) -> list[str]:
    """Cap the reference clip below max_clip_seconds with frame-accurate output.

    Stream-copy trims can land on keyframes and leave an MP4 slightly over the
    requested limit, which provider APIs can reject. Re-encode the short
    reference clip so the final container duration stays below the hard cap.
    """
    return [
        "ffmpeg", "-y", "-i", str(src),
        "-t", _seconds_arg(safe_clip_seconds(max_seconds)),
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        str(dest),
    ]


def build_sample_frames_cmd(video: Path, pattern: str, fps: int) -> list[str]:
    """FR-6: sample frames for the identity gate (pattern like .../f_%03d.png)."""
    return ["ffmpeg", "-y", "-i", str(video), "-vf", f"fps={fps}", pattern]


def build_strip_cmds(src: Path, dest: Path) -> list[list[str]]:
    """FR-7: two-pass metadata strip — ffmpeg container scrub, then exiftool."""
    return [
        [
            "ffmpeg", "-y", "-i", str(src),
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            "-c:v", "copy", "-c:a", "copy",
            str(dest),
        ],
        ["exiftool", "-all=", "-overwrite_original", str(dest)],
    ]


def run_cmd(
    cmd: list[str],
    logger: logging.Logger,
    dry_run: bool = False,
    timeout: int = 3600,
) -> None:
    """Run a command, logging it; raise MediaError with the stderr tail on failure."""
    logger.info("$ %s", " ".join(cmd))
    if dry_run:
        return
    if shutil.which(cmd[0]) is None:
        raise ToolMissingError(
            f"'{cmd[0]}' is not installed or not on PATH. "
            f"Install it first (see SETUP.md — e.g. `brew install {cmd[0]}`)."
        )
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"'{cmd[0]}' timed out after {timeout}s") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-12:]
        raise MediaError(
            f"'{cmd[0]}' exited {result.returncode}:\n" + "\n".join(tail)
        )


def probe_duration(video: Path) -> float:
    """Video duration in seconds via ffprobe."""
    out = _ffprobe(video, ["-show_entries", "format=duration", "-of", "csv=p=0"])
    try:
        return float(out)
    except ValueError as exc:
        raise MediaError(f"could not parse duration of {video}: {out!r}") from exc


def probe_dims(video: Path) -> tuple[int, int]:
    """(width, height) of the first video stream."""
    out = _ffprobe(
        video,
        ["-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=s=x:p=0"],
    )
    try:
        w, h = out.split("x")
        return int(w), int(h)
    except ValueError as exc:
        raise MediaError(f"could not parse dimensions of {video}: {out!r}") from exc


def _ffprobe(video: Path, args: list[str]) -> str:
    cmd = ["ffprobe", "-v", "error", *args, str(video)]
    if shutil.which("ffprobe") is None:
        raise ToolMissingError("'ffprobe' not found — install ffmpeg (`brew install ffmpeg`).")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise MediaError(f"ffprobe failed on {video}: {result.stderr.strip()}")
    return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""


def round_down_to_16(n: int) -> int:
    """Wan 2.2 requires dimensions that are multiples of 16 (FR-5)."""
    return max(16, (int(n) // 16) * 16)


def atomic_move(src: Path, dest: Path) -> None:
    """FR-8: publish via copy-to-temp + rename so readers never see a partial file."""
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    os.close(fd)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    src.unlink(missing_ok=True)
