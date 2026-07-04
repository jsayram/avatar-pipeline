#!/usr/bin/env python3
"""FR-2..FR-8 — process one TikTok URL, split into a TWO-GATE operator-
approval flow.

    download -> first frame
    -> [GATE 1: operator approves the RAW frame via Telegram — free, no
        Seedream spend yet]
    -> avatar-into-frame (FR-4, Seedream)
    -> [GATE 2: operator approves the generated avatar still via Telegram —
        no animation spend yet]
    -> animate (FR-5) -> identity gate (FR-6, retry/flag)
    -> metadata strip -> publish -> update processed.json + done.csv
    -> the final video is sent back via Telegram too (published or flagged)

STANDING RULE (see HANDOFF.md): the full chain must never run unattended past
either gate without the operator explicitly approving first. That's what
--phase exists for — n8n calls this across multiple separate invocations,
with the operator's Telegram reply in between each:

    --phase prepare          FR-2, FR-3 only (download + extract frame).
                             Sends the RAW frame to Telegram for GATE 1,
                             saves pending-approval state (stage="frame"),
                             EXITS. Requires --url. This is also the entry
                             point for a TikTok link texted in via Telegram
                             (see handle_telegram_reply.py) as well as the
                             daily-scheduled pick_next.py link — same gate
                             either way.
    --phase generate_avatar  Resume after a "yes" reply to GATE 1: FR-4
                             (Seedream). Sends the avatar still (+ the
                             original frame, for comparison) to Telegram for
                             GATE 2, saves pending-approval state
                             (stage="avatar"). Requires --id.
    --phase reject_frame     Resume after a "no" reply to GATE 1: flags the
                             URL and gives up — a raw extracted frame has no
                             seed to retry, so rejecting it means this
                             source clip isn't usable. Requires --id.
    --phase animate          Resume after a "yes" reply to GATE 2: FR-5..FR-8.
                             Requires --id (state is loaded from the
                             pending-approval record, not re-passed on the
                             command line).
    --phase regenerate       Resume after a "no" reply to GATE 2: redo FR-4
                             with a new seed and re-send for approval, or
                             flag the URL if telegram.max_approval_attempts
                             is exhausted. Requires --id.
    --phase full             Legacy all-in-one behavior (FR-2..FR-8 with no
                             pause, no gates at all) — for manual/local
                             testing only. Never wire this into the
                             scheduled n8n workflow.

Usage:
    python scripts/worker.py --phase prepare --url <url> --config config.yaml
    python scripts/worker.py --phase generate_avatar --id <id> --config config.yaml
    python scripts/worker.py --phase reject_frame --id <id> --config config.yaml
    python scripts/worker.py --phase animate --id <id> --config config.yaml
    python scripts/worker.py --phase regenerate --id <id> --config config.yaml
    python scripts/worker.py --url <url> --config config.yaml --dry-run

Prints exactly one JSON line on stdout (all logs go to stderr and
work/<id>/run.log):

    {"status": "pending_approval", "id": ..., "stage": "frame"|"avatar", ...}
                                          exit 0 — something was sent to
                                          Telegram; waiting for a yes/no
                                          reply (indefinitely)
    {"status": "published", "id": ..., "url": ..., "output": ..., "cosine": ...}
    {"status": "flagged", ...}            exit 2 — this URL failed; it is
                                          recorded in processed.json "flagged"
                                          so it never blocks future runs
    {"status": "error", "stage": ...}     exit 3 — infrastructure/setup problem
                                          (service down, template missing);
                                          the URL is NOT consumed and will be
                                          retried on the next scheduled run
    {"status": "already_processed", ...}  exit 0 — idempotent no-op
    {"status": "dry_run", ...}            exit 0 — plan + setup warnings only

Failure isolation (FR-9): a flagged URL is skipped by pick_next forever, so one
bad link never blocks the queue; an infrastructure error leaves state untouched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import statistics
import sys
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load .env from the repo root (never committed — see .gitignore) regardless
# of the caller's cwd, so WAVESPEED_API_KEY etc. work from a terminal, an
# IDE run configuration, or n8n's Execute Command node alike. Real
# environment variables (e.g. already `export`ed) always take precedence.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from lib.animation_providers import (
    AnimationError,
    AnimationNotConfigured,
    LocalComfyUIAnimationProvider,
    get_animation_provider,
)
from lib.avatar_frame_providers import (
    AvatarFrameError,
    AvatarFrameNotConfigured,
    get_avatar_frame_provider,
)
from lib.comfyui import (
    WorkflowTemplateError,
    load_workflow,
)
from lib.config import Config, ConfigError, load_config
from lib.logging_utils import get_logger
from lib.media import (
    MediaError,
    ToolMissingError,
    atomic_move,
    build_download_cmd,
    build_first_frame_cmd,
    build_sample_frames_cmd,
    build_strip_cmds,
    build_trim_cmd,
    probe_duration,
    run_cmd,
    safe_clip_seconds,
)
from lib.output_naming import timestamped_filename
from lib.pending import (
    PendingApprovalError,
    clear_pending,
    load_pending,
    save_pending,
)
from lib.state import (
    append_done_csv,
    extract_tiktok_id,
    load_state,
    mark_flagged,
    mark_processed,
    seen_ids,
)
from lib.status_sheet import write_status_sheet
from lib.telegram_notify import TelegramError, send_message, send_photo, send_video
from lib.wavespeed_balance import DASHBOARD_URL, WaveSpeedBalanceError, get_balance

EXIT_OK = 0
EXIT_FLAGGED = 2
EXIT_INFRA = 3

REQUIRED_TOOLS = ("yt-dlp", "ffmpeg", "ffprobe", "exiftool")
REFERENCE_INFO_FILENAME = "reference_info.json"


class StageFailure(Exception):
    """This URL failed (bad download, failed render, identity drift after all
    retries). The URL is flagged so it never blocks future runs."""

    def __init__(self, stage: str, message: str, cosine: float | None = None):
        super().__init__(message)
        self.stage = stage
        self.cosine = cosine


class InfraFailure(Exception):
    """Setup/environment problem (service down, workflow not exported, tool
    missing). State is left untouched so the URL is retried next run."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


# --------------------------------------------------------------------------
# Pure decision logic (unit-tested)

def mean_cosine(cosines: list[float]) -> float:
    return statistics.fmean(cosines) if cosines else 0.0


def identity_passes(cosines: list[float], cosine_min: float) -> bool:
    """FR-6: PASS iff the mean cosine over sampled frames meets the threshold."""
    return bool(cosines) and mean_cosine(cosines) >= cosine_min


def attempt_seed(base_seed: int | None, attempt: int) -> int:
    """Fixed config seed varies deterministically per retry; else random."""
    if base_seed is not None:
        return base_seed + attempt
    return int.from_bytes(os.urandom(4), "big")


def _reference_info_path(work: Path) -> Path:
    return work / REFERENCE_INFO_FILENAME


def _write_reference_info(work: Path, data: dict) -> None:
    work.mkdir(parents=True, exist_ok=True)
    path = _reference_info_path(work)
    fd, tmp = tempfile.mkstemp(dir=work, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_reference_info(work: Path) -> dict:
    path = _reference_info_path(work)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _reference_processing_note(work: Path) -> str:
    return str(_read_reference_info(work).get("processing_note") or "")


def _trim_processing_note(original_duration: float, final_duration: float,
                          max_seconds: int) -> str:
    return (
        f"Source video was {original_duration:.2f}s, so the reference clip "
        f"was trimmed to {final_duration:.2f}s for the {max_seconds}s "
        "Kling/WaveSpeed limit."
    )


def _reference_trim_needed(duration: float, cfg: Config) -> bool:
    """Use the safe target as the threshold, not the exact provider cap."""
    return duration > safe_clip_seconds(cfg.video.max_clip_seconds)


def _cap_reference_video(cfg: Config, ref: Path, work: Path, logger, url: str = "") -> Path:
    """Ensure a reference clip is safely under the provider duration cap.

    This runs both during prepare and again immediately before animation so
    older pending jobs created before the trim fix cannot reach WaveSpeed with
    an over-limit reference video.
    """
    try:
        duration = probe_duration(ref)
    except ToolMissingError as exc:
        raise InfraFailure("trim", str(exc)) from exc
    except MediaError as exc:
        raise StageFailure("trim", f"could not read reference duration: {exc}") from exc

    info = {
        "url": url,
        "original_duration_seconds": round(duration, 3),
        "max_clip_seconds": cfg.video.max_clip_seconds,
        "trimmed": False,
        "processing_note": "",
    }
    if _reference_trim_needed(duration, cfg):
        trimmed = work / "ref_capped.mp4"
        trimmed.unlink(missing_ok=True)
        logger.info(
            "reference is %.2fs — trimming to %.2fs target for %ds cap",
            duration, safe_clip_seconds(cfg.video.max_clip_seconds),
            cfg.video.max_clip_seconds,
        )
        try:
            run_cmd(build_trim_cmd(ref, trimmed, cfg.video.max_clip_seconds), logger)
            final_duration = probe_duration(trimmed)
        except ToolMissingError as exc:
            raise InfraFailure("trim", str(exc)) from exc
        except MediaError as exc:
            raise StageFailure("trim", str(exc)) from exc

        if final_duration > cfg.video.max_clip_seconds:
            raise StageFailure(
                "trim",
                f"trimmed reference is {final_duration:.2f}s, still above "
                f"the {cfg.video.max_clip_seconds}s provider limit",
            )

        os.replace(trimmed, ref)
        info.update({
            "final_duration_seconds": round(final_duration, 3),
            "trim_target_seconds": round(safe_clip_seconds(cfg.video.max_clip_seconds), 3),
            "trimmed": True,
            "processing_note": _trim_processing_note(
                duration, final_duration, cfg.video.max_clip_seconds
            ),
        })
    else:
        info["final_duration_seconds"] = round(duration, 3)
    _write_reference_info(work, info)
    return ref


def _is_duration_cap_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "duration must not exceed" in message
        or ("video duration" in message and "10 seconds" in message)
    )


# --------------------------------------------------------------------------
# Steps

def download_reference(cfg: Config, url: str, work: Path, logger) -> Path:
    """FR-2 + clip cap: download, then trim to max_clip_seconds up front."""
    ref_raw = work / "ref_raw.mp4"
    ref = work / "ref.mp4"
    try:
        run_cmd(build_download_cmd(url, ref_raw, cookies_file=cfg.video.cookies_file), logger)
    except ToolMissingError as exc:
        raise InfraFailure("download", str(exc)) from exc
    except MediaError as exc:
        raise StageFailure("download", f"yt-dlp failed: {exc}") from exc
    if not ref_raw.exists():
        raise StageFailure("download", f"yt-dlp reported success but {ref_raw} is missing")

    os.replace(ref_raw, ref)
    return _cap_reference_video(cfg, ref, work, logger, url=url)


def extract_frame(ref: Path, work: Path, logger) -> Path:
    """FR-3."""
    frame1 = work / "frame1.png"
    try:
        run_cmd(build_first_frame_cmd(ref, frame1), logger)
    except ToolMissingError as exc:
        raise InfraFailure("first_frame", str(exc)) from exc
    except MediaError as exc:
        raise StageFailure("first_frame", str(exc)) from exc
    return frame1


def make_avatar_frame(cfg: Config, frame1: Path, work: Path, seed: int, logger) -> Path:
    """FR-4."""
    dest = work / "avatar_frame1.png"
    try:
        provider = get_avatar_frame_provider(cfg, logger)
        logger.info("avatar-frame generation via %s (seed=%d)", provider.name, seed)
        return provider.generate(frame1, dest, cfg, seed=seed)
    except AvatarFrameNotConfigured as exc:
        raise InfraFailure("avatar_frame", str(exc)) from exc
    except AvatarFrameError as exc:
        raise StageFailure("avatar_frame", str(exc)) from exc


def gate_scores(cfg: Config, video: Path, frames_dir: Path, logger) -> list[float]:
    """FR-6: sample frames and score each against the avatar reference."""
    if not cfg.paths.avatar_reference.exists():
        raise InfraFailure(
            "identity_gate",
            f"avatar reference image not found: {cfg.paths.avatar_reference} — "
            "supply the canonical identity image (see assets/ and SETUP.md).",
        )
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(frames_dir / "f_%03d.png")
    try:
        run_cmd(build_sample_frames_cmd(video, pattern, cfg.identity.sample_fps), logger)
    except ToolMissingError as exc:
        raise InfraFailure("identity_gate", str(exc)) from exc
    except MediaError as exc:
        raise StageFailure("identity_gate", str(exc)) from exc

    frames = sorted(frames_dir.glob("f_*.png"))
    if not frames:
        raise StageFailure("identity_gate", f"no frames sampled from {video}")

    cosines = []
    for frame in frames:
        try:
            resp = requests.post(
                f"{cfg.endpoints.gate_url}/compare",
                json={"ref": str(cfg.paths.avatar_reference), "gen": str(frame)},
                timeout=120,
            )
        except requests.ConnectionError as exc:
            raise InfraFailure(
                "identity_gate",
                f"cannot reach the identity gate at {cfg.endpoints.gate_url} — "
                "start it: ./venv/bin/python scripts/face_gate.py",
            ) from exc
        except requests.RequestException as exc:
            raise StageFailure("identity_gate", f"gate request failed: {exc}") from exc
        if resp.status_code != 200:
            raise StageFailure(
                "identity_gate", f"gate returned {resp.status_code}: {resp.text[:300]}"
            )
        cosines.append(float(resp.json()["cosine"]))
    logger.info("gate cosines: %s (mean %.4f)",
                [round(c, 4) for c in cosines], mean_cosine(cosines))
    return cosines


def animate_and_gate(cfg: Config, tiktok_id: str, avatar_frame: Path, ref: Path,
                     work: Path, logger) -> tuple[Path, float, int]:
    """FR-5 + FR-6 retry loop. Returns (passed_video, mean_cosine, attempts)."""
    try:
        provider = get_animation_provider(cfg, logger)
    except AnimationNotConfigured as exc:
        raise InfraFailure("animate", str(exc)) from exc

    attempts = cfg.identity.max_retries + 1
    best_mean = 0.0
    for attempt in range(attempts):
        seed = attempt_seed(cfg.video.seed, attempt)
        raw = work / f"avatar_raw_attempt{attempt + 1}.mp4"
        logger.info("animation attempt %d/%d via %s (seed=%d)",
                    attempt + 1, attempts, provider.name, seed)
        try:
            provider.animate(avatar_frame, ref, raw, cfg, seed=seed)
        except AnimationNotConfigured as exc:
            raise InfraFailure("animate", str(exc)) from exc
        except (WorkflowTemplateError, ComfyUIUnreachable) as exc:
            raise InfraFailure("animate", str(exc)) from exc
        except AnimationError as exc:
            if provider.name == "wavespeed" and _is_duration_cap_error(exc):
                raise StageFailure("animate", str(exc)) from exc
            if (provider.name == "wavespeed"
                    and cfg.animation.fallback_to_local_on_cloud_error):
                logger.warning("cloud animation failed (%s) — falling back to "
                               "local ComfyUI for this and later attempts", exc)
                provider = LocalComfyUIAnimationProvider(logger=logger)
                try:
                    provider.animate(avatar_frame, ref, raw, cfg, seed=seed)
                except (WorkflowTemplateError, ComfyUIUnreachable) as exc2:
                    raise InfraFailure("animate", str(exc2)) from exc2
                except AnimationError as exc2:
                    raise StageFailure("animate", str(exc2)) from exc2
            else:
                raise StageFailure("animate", str(exc)) from exc

        cosines = gate_scores(cfg, raw, work / f"gate_frames_attempt{attempt + 1}", logger)
        mean = mean_cosine(cosines)
        best_mean = max(best_mean, mean)
        if identity_passes(cosines, cfg.identity.cosine_min):
            logger.info("identity gate PASS (%.4f >= %.2f)", mean, cfg.identity.cosine_min)
            return raw, mean, attempt + 1
        logger.warning("identity gate FAIL (%.4f < %.2f)", mean, cfg.identity.cosine_min)
        try:
            cfg.paths.video_out_dir.mkdir(parents=True, exist_ok=True)
            dest = cfg.paths.video_out_dir / timestamped_filename(tiktok_id, "video", raw.suffix)
            shutil.copy2(raw, dest)
            logger.info("saved failed attempt video -> %s", dest)
        except OSError as exc:
            logger.warning("could not save failed attempt video to video-out (non-fatal): %s", exc)

    raise StageFailure(
        "identity_gate",
        f"identity below threshold after {attempts} attempt(s): best mean "
        f"{best_mean:.4f} < {cfg.identity.cosine_min}",
        cosine=best_mean,
    )


def strip_and_publish(cfg: Config, tiktok_id: str, url: str, raw_video: Path,
                      work: Path, mean: float, logger) -> Path:
    """FR-7 + FR-8."""
    clean = work / "avatar_video.mp4"
    for cmd in build_strip_cmds(raw_video, clean):
        try:
            run_cmd(cmd, logger)
        except ToolMissingError as exc:
            raise InfraFailure("strip_metadata", str(exc)) from exc
        except MediaError as exc:
            raise StageFailure("strip_metadata", str(exc)) from exc

    today = dt.date.today().isoformat()
    dest = cfg.paths.video_out_dir / timestamped_filename(tiktok_id, "video", ".mp4")
    atomic_move(clean, dest)
    append_done_csv(cfg.paths.done_csv, {
        "date": today,
        "id": tiktok_id,
        "url": url,
        "output_path": str(dest),
        "identity_cosine": f"{mean:.4f}",
        "status": "published",
    })
    mark_processed(cfg.paths.processed_json, tiktok_id)
    logger.info("published %s", dest)
    _refresh_status_sheet(cfg, logger)
    _cleanup_work_dir(cfg, tiktok_id, work, logger)
    return dest


# --------------------------------------------------------------------------
# Dry run

def dry_run(cfg: Config, tiktok_id: str, url: str, logger) -> dict:
    """Report the plan and every setup gap without touching network or state."""
    work = cfg.paths.work_dir / tiktok_id
    warnings: list[str] = []

    for tool in REQUIRED_TOOLS:
        if shutil.which(tool) is None:
            warnings.append(f"'{tool}' not installed (brew install {tool.replace('ffprobe', 'ffmpeg')})")
    workflow_names = ["wan_animate.api.json"]
    if cfg.avatar_frame.provider == "local_comfyui":
        workflow_names.append("avatar_into_frame.api.json")
    for wf in workflow_names:
        try:
            load_workflow(cfg.paths.workflows_dir / wf)
        except WorkflowTemplateError as exc:
            warnings.append(str(exc).splitlines()[0])
    if cfg.avatar_frame.provider == "local_comfyui" and not cfg.paths.lora_path.exists():
        warnings.append(f"character LoRA missing: {cfg.paths.lora_path}")
    if not cfg.paths.avatar_reference.exists():
        warnings.append(f"avatar reference image missing: {cfg.paths.avatar_reference}")
    for ref in cfg.avatar_frame.identity_references:
        if not ref.exists():
            warnings.append(f"avatar identity reference missing: {ref}")
    if not cfg.paths.numbers_sheet.exists():
        warnings.append(f"numbers sheet missing: {cfg.paths.numbers_sheet}")
    if cfg.video.cookies_file is not None and not cfg.video.cookies_file.exists():
        warnings.append(
            f"video.cookies_file is set but missing: {cfg.video.cookies_file} — "
            "re-export it (see docs/TIKTOK-COOKIES.md) or downloads of "
            "login-gated TikTok posts will keep failing"
        )
    try:
        avatar_provider = get_avatar_frame_provider(cfg, logger)
        avatar_provider_name = avatar_provider.name
    except AvatarFrameNotConfigured as exc:
        avatar_provider_name = cfg.avatar_frame.provider
        warnings.append(str(exc))
    try:
        provider = get_animation_provider(cfg, logger)
        provider_name = provider.name
    except AnimationNotConfigured as exc:
        provider_name = cfg.animation.provider
        warnings.append(str(exc))
    if cfg.avatar_frame.provider == "wavespeed_seedream":
        if not cfg.wavespeed.enabled:
            warnings.append("avatar_frame.provider is wavespeed_seedream but wavespeed.enabled is false")
        if not cfg.avatar_frame.wavespeed_model:
            warnings.append("avatar_frame.wavespeed_model is empty — set bytedance/seedream-v4.5/edit")
        if not os.environ.get(cfg.wavespeed.api_key_env):
            warnings.append(f"env var {cfg.wavespeed.api_key_env} not set")
    if cfg.animation.provider == "wavespeed" and cfg.wavespeed.enabled:
        if not cfg.wavespeed.model:
            warnings.append("wavespeed.model is empty — set the model id before enabling")
        if not os.environ.get(cfg.wavespeed.api_key_env):
            warnings.append(f"env var {cfg.wavespeed.api_key_env} not set")
    if cfg.telegram.enabled:
        if not cfg.telegram.chat_id:
            warnings.append("telegram.chat_id is empty — message your bot once, then "
                            "look up the chat id via https://api.telegram.org/bot<token>/getUpdates")
        if not os.environ.get(cfg.telegram.bot_token_env):
            warnings.append(f"env var {cfg.telegram.bot_token_env} not set")
    else:
        warnings.append(
            "telegram.enabled is false — --phase prepare/generate_avatar/"
            "reject_frame/animate/regenerate (the two-gate operator-approval "
            "loop) will fail; only --phase full (no approval gates, "
            "manual/testing only) will work"
        )

    steps = [
        f"[prepare] (entry point for both a texted-in Telegram link and the "
        f"daily-scheduled pick_next.py link) download {url} -> {work / 'ref.mp4'} "
        f"(cap {cfg.video.max_clip_seconds}s)",
        f"[prepare] first frame -> {work / 'frame1.png'}",
        f"[prepare] GATE 1: send RAW frame to Telegram (no cost spent yet), "
        f"save pending-approval state (stage=frame), EXIT",
        f"[operator replies yes/no via Telegram — separate n8n webhook workflow]",
        f"[generate_avatar, on GATE 1 'yes'] avatar-into-frame via "
        f"{avatar_provider_name} ({len(cfg.avatar_frame.identity_references) or 1} "
        f"identity ref(s)) -> {work / 'avatar_frame1.png'}",
        f"[generate_avatar] GATE 2: send original frame + avatar still to "
        f"Telegram (for comparison), save pending-approval state "
        f"(stage=avatar), EXIT (no animation yet)",
        f"[reject_frame, on GATE 1 'no'] flag + skip — a raw extracted frame "
        f"has no seed to retry",
        f"[operator replies yes/no via Telegram — separate n8n webhook workflow]",
        f"[animate, on GATE 2 'yes'] save approved still -> "
        f"{cfg.paths.image_out_dir / (tiktok_id + '-image-<timestamp>.png')}",
        f"[animate] via {provider_name} -> {work / 'avatar_raw_attempt1.mp4'}",
        f"[animate] identity gate via {cfg.endpoints.gate_url} (mean cosine >= "
        f"{cfg.identity.cosine_min}, {cfg.identity.max_retries} retries); each "
        f"FAILED attempt saved + sent via Telegram -> "
        f"{cfg.paths.video_out_dir / (tiktok_id + '-video-<timestamp>.mp4')}",
        f"[animate] on PASS: strip metadata (ffmpeg -map_metadata -1; exiftool -all=)",
        f"[animate] publish + send via Telegram -> "
        f"{cfg.paths.video_out_dir / (tiktok_id + '-video-<timestamp>.mp4')}",
        f"[animate] update {cfg.paths.processed_json.name} + {cfg.paths.done_csv.name}",
        f"[regenerate, on GATE 2 'no'] redo avatar-into-frame with a new seed, "
        f"resend original frame + new still to Telegram, up to "
        f"{cfg.telegram.max_approval_attempts} attempt(s) before flagging",
    ]
    for step in steps:
        logger.info("[dry-run] %s", step)
    for warning in warnings:
        logger.warning("[dry-run] %s", warning)

    return {
        "status": "dry_run",
        "id": tiktok_id,
        "url": url,
        "avatar_frame_provider": avatar_provider_name,
        "animation_provider": provider_name,
        "steps": steps,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# Shared helpers across phases

def _flag_and_record(cfg: Config, tiktok_id: str, url: str, work: Path,
                     stage: str, reason: str, cosine: float | None,
                     logger) -> tuple[dict, int]:
    """Record a flagged (permanently skipped, non-blocking) failure and clear
    any pending-approval state for this id."""
    logger.error("FLAGGED at %s: %s", stage, reason)
    (work / "FLAGGED.txt").write_text(f"stage: {stage}\nreason: {reason}\n")
    append_done_csv(cfg.paths.done_csv, {
        "date": dt.date.today().isoformat(),
        "id": tiktok_id,
        "url": url,
        "output_path": "",
        "identity_cosine": f"{cosine:.4f}" if cosine is not None else "",
        "status": f"flagged:{stage}",
    })
    mark_flagged(cfg.paths.processed_json, tiktok_id)
    clear_pending(cfg.paths.work_dir, tiktok_id)
    _refresh_status_sheet(cfg, logger)
    _cleanup_work_dir(cfg, tiktok_id, work, logger)
    return {"status": "flagged", "id": tiktok_id, "url": url,
            "stage": stage, "reason": reason}, EXIT_FLAGGED


def _clear_without_consuming(cfg: Config, tiktok_id: str, url: str, work: Path,
                             stage: str, reason: str, logger,
                             notify: bool = False) -> tuple[dict, int]:
    """Stop a pre-final-video run without writing processed.json/done.csv.

    Use this for download/frame/Seedream/operator-review failures before a
    Kling motion video exists. The URL remains retryable, including if the
    same TikTok link is pasted again later.
    """
    logger.warning("NOT CONSUMED at %s: %s", stage, reason)
    work.mkdir(parents=True, exist_ok=True)
    (work / "NOT_CONSUMED.txt").write_text(f"stage: {stage}\nreason: {reason}\n")
    clear_pending(cfg.paths.work_dir, tiktok_id)
    _refresh_status_sheet(cfg, logger)
    _cleanup_work_dir(cfg, tiktok_id, work, logger)
    if notify:
        _notify(
            cfg,
            f"{tiktok_id}: stopped at {stage}: {reason}\n"
            "No final Kling video was produced, so this link was not marked "
            "processed and can be tried again.",
            logger,
        )
    return {"status": "not_consumed", "id": tiktok_id, "url": url,
            "stage": stage, "reason": reason}, EXIT_OK


def _record_video_failure(cfg: Config, tiktok_id: str, url: str, work: Path,
                          stage: str, reason: str, cosine: float | None,
                          output_path: Path, logger) -> tuple[dict, int]:
    """Record a terminal failed outcome only after a Kling video exists."""
    logger.error("VIDEO FAILED at %s: %s", stage, reason)
    (work / "FLAGGED.txt").write_text(
        f"stage: {stage}\nreason: {reason}\noutput_path: {output_path}\n"
    )
    append_done_csv(cfg.paths.done_csv, {
        "date": dt.date.today().isoformat(),
        "id": tiktok_id,
        "url": url,
        "output_path": str(output_path),
        "identity_cosine": f"{cosine:.4f}" if cosine is not None else "",
        "status": f"flagged:{stage}",
    })
    mark_processed(cfg.paths.processed_json, tiktok_id)
    clear_pending(cfg.paths.work_dir, tiktok_id)
    _refresh_status_sheet(cfg, logger)
    _cleanup_work_dir(cfg, tiktok_id, work, logger)
    return {"status": "flagged", "id": tiktok_id, "url": url,
            "stage": stage, "reason": reason,
            "output": str(output_path)}, EXIT_FLAGGED


def _notify(cfg: Config, text: str, logger) -> None:
    """Best-effort Telegram text notice — never let a notify failure mask
    the real result of a phase."""
    try:
        send_message(cfg, text, logger=logger)
    except TelegramError as exc:
        logger.warning("could not send Telegram notification: %s", exc)


def _refresh_status_sheet(cfg: Config, logger) -> None:
    """Best-effort regen of the read-glance companion CSV next to
    links.numbers — never let this mask the real result of a phase."""
    try:
        out = write_status_sheet(cfg)
        logger.info("refreshed status sheet -> %s", out)
    except Exception as exc:  # noqa: BLE001 — never let this break a phase
        logger.warning("could not refresh status sheet: %s", exc)


_CLEANUP_GLOBS = (
    "avatar_raw_attempt*.mp4",  # pre-strip animation output — the published
                                # copy already lives in video-out/, and every
                                # failed attempt was already copied there too
    "gate_frames_attempt*",     # sampled PNGs used only for the identity-gate
                                # math — never read again after the decision
    "ref_raw.mp4",              # pre-trim download; normally already renamed
                                # to ref.mp4 by download_reference(), this is
                                # just a defensive sweep for an interrupted run
)


def _cleanup_work_dir(cfg: Config, tiktok_id: str, work: Path, logger) -> None:
    """Best-effort: delete disposable per-attempt scratch files once a run
    reaches ANY terminal outcome (published, video-failed, flagged,
    not-consumed). This is where the vast majority of a run's disk footprint
    lives (often 90%+ — raw pre-strip videos and sampled gate-check frames),
    and it accumulates forever otherwise since nothing else ever touches it.
    Never let a cleanup failure mask the real phase result. Opt out via
    config.yaml's cleanup.enabled: false."""
    if not cfg.cleanup.enabled:
        return
    try:
        removed_bytes = 0
        for pattern in _CLEANUP_GLOBS:
            for path in work.glob(pattern):
                try:
                    if path.is_dir():
                        removed_bytes += sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
                        shutil.rmtree(path)
                    else:
                        removed_bytes += path.stat().st_size
                        path.unlink()
                except OSError as exc:
                    logger.warning("could not remove %s during cleanup (non-fatal): %s", path, exc)
        if removed_bytes:
            logger.info("cleanup freed %.1f MB in %s", removed_bytes / (1024 * 1024), work)
    except Exception as exc:  # noqa: BLE001 — never let this break a phase
        logger.warning("work dir cleanup failed (non-fatal): %s", exc)


def _send_comparison_frame(cfg: Config, tiktok_id: str, frame1: Path, logger) -> None:
    """Best-effort: send the original extracted frame first, so the operator
    can compare it against the generated avatar still that follows. Never let
    this failure block the actual approval-request send."""
    try:
        send_photo(cfg, frame1,
                  f"{tiktok_id}: original frame extracted from TikTok (for comparison)",
                  logger=logger)
    except TelegramError as exc:
        logger.warning("could not send comparison frame to Telegram (non-fatal): %s", exc)


def _send_video(cfg: Config, video_path: Path, caption: str, logger) -> None:
    """Best-effort: deliver the actual video file (published or a failed
    gate attempt) so the operator doesn't have to dig through video-out/.
    Never let this failure mask the real phase result."""
    try:
        send_video(cfg, video_path, caption, logger=logger)
    except TelegramError as exc:
        logger.warning("could not send video to Telegram (non-fatal): %s", exc)


def _notify_with_balance(cfg: Config, text: str, logger) -> None:
    """Best-effort: after each animation outcome (published or flagged),
    append the current WaveSpeed balance so the operator always has cost
    visibility. A balance-fetch failure never blocks or masks the real
    notification — it just falls back to the plain text."""
    if cfg.wavespeed.enabled:
        try:
            balance = get_balance(cfg)
            text = f"{text}\n\n💰 WaveSpeed balance: ${balance:.2f}\n🔗 {DASHBOARD_URL}"
        except WaveSpeedBalanceError as exc:
            logger.warning("could not fetch WaveSpeed balance (non-fatal): %s", exc)
    _notify(cfg, text, logger)


# --------------------------------------------------------------------------
# Phase: prepare (FR-2, FR-3 only — download + extract frame, then pause for
# the FIRST operator approval, before any paid Seedream call happens)

def run_prepare(cfg: Config, tiktok_id: str, url: str, work: Path, logger) -> tuple[dict, int]:
    state = load_state(cfg.paths.processed_json)
    if tiktok_id in state["processed"]:
        logger.info("id %s already processed — nothing to do", tiktok_id)
        return {"status": "already_processed", "id": tiktok_id,
                "url": url, "previous_status": "processed"}, EXIT_OK

    try:
        ref = download_reference(cfg, url, work, logger)
        frame1 = extract_frame(ref, work, logger)
    except StageFailure as exc:
        return _clear_without_consuming(
            cfg, tiktok_id, url, work, exc.stage, str(exc), logger, notify=True
        )
    except InfraFailure as exc:
        logger.error("infrastructure error at %s (URL not consumed): %s", exc.stage, exc)
        return {"status": "error", "id": tiktok_id, "url": url,
                "stage": exc.stage, "error": str(exc)}, EXIT_INFRA
    except Exception as exc:
        logger.exception("unexpected error (URL not consumed)")
        return {"status": "error", "id": tiktok_id, "url": url, "stage": "unexpected",
                "error": f"{type(exc).__name__}: {exc}"}, EXIT_INFRA

    processing_note = _reference_processing_note(work)
    save_pending(cfg.paths.work_dir, tiktok_id, stage="frame", url=url,
                ref_video_path=str(ref), frame1_path=str(frame1),
                processing_note=processing_note)
    _refresh_status_sheet(cfg, logger)
    try:
        caption = (
            f"{tiktok_id}: extracted frame ready (no cost spent yet). Reply "
            "yes to generate the avatar still (Seedream), no to clear this "
            "attempt so the link can be tried again later."
        )
        if processing_note:
            caption = f"{caption}\n\n{processing_note}"
        send_photo(
            cfg, frame1,
            caption,
            logger=logger,
        )
    except TelegramError as exc:
        logger.error("failed to send Telegram approval request: %s", exc)
        return {"status": "error", "id": tiktok_id, "url": url,
                "stage": "telegram_notify", "error": str(exc)}, EXIT_INFRA

    logger.info("=== prepare complete: waiting for frame approval (id=%s) ===", tiktok_id)
    result = {"status": "pending_approval", "id": tiktok_id, "url": url,
              "stage": "frame"}
    if processing_note:
        result["processing_note"] = processing_note
    return result, EXIT_OK


# --------------------------------------------------------------------------
# Phase: generate_avatar (resume after a "yes" reply to the frame gate ->
# FR-4, then pause for the SECOND operator approval, before any paid
# animation call happens)

def run_generate_avatar(cfg: Config, tiktok_id: str, work: Path, logger) -> tuple[dict, int]:
    try:
        pending = load_pending(cfg.paths.work_dir, tiktok_id)
    except PendingApprovalError as exc:
        logger.error("%s", exc)
        return {"status": "error", "id": tiktok_id, "stage": "pending_lookup",
                "error": str(exc)}, EXIT_INFRA

    url = pending["url"]
    frame1 = Path(pending["frame1_path"])

    try:
        avatar_frame = make_avatar_frame(cfg, frame1, work,
                                         seed=attempt_seed(cfg.video.seed, 0),
                                         logger=logger)
    except StageFailure as exc:
        return _clear_without_consuming(
            cfg, tiktok_id, url, work, exc.stage, str(exc), logger, notify=True
        )
    except InfraFailure as exc:
        logger.error("infrastructure error at %s (URL not consumed): %s", exc.stage, exc)
        return {"status": "error", "id": tiktok_id, "url": url,
                "stage": exc.stage, "error": str(exc)}, EXIT_INFRA
    except Exception as exc:
        logger.exception("unexpected error (URL not consumed)")
        return {"status": "error", "id": tiktok_id, "url": url, "stage": "unexpected",
                "error": f"{type(exc).__name__}: {exc}"}, EXIT_INFRA

    save_pending(cfg.paths.work_dir, tiktok_id, stage="avatar", url=url,
                ref_video_path=pending["ref_video_path"], frame1_path=str(frame1),
                avatar_frame_path=str(avatar_frame), attempt=1,
                processing_note=str(pending.get("processing_note") or ""))
    _refresh_status_sheet(cfg, logger)
    _send_comparison_frame(cfg, tiktok_id, frame1, logger)
    try:
        send_photo(
            cfg, avatar_frame,
            f"{tiktok_id}: avatar still ready (attempt 1/"
            f"{cfg.telegram.max_approval_attempts}). Reply yes to animate, "
            "no to regenerate.",
            logger=logger,
        )
    except TelegramError as exc:
        logger.error("failed to send Telegram approval request: %s", exc)
        return {"status": "error", "id": tiktok_id, "url": url,
                "stage": "telegram_notify", "error": str(exc)}, EXIT_INFRA

    logger.info("=== generate_avatar complete: waiting for avatar approval (id=%s) ===", tiktok_id)
    return {"status": "pending_approval", "id": tiktok_id, "url": url,
            "stage": "avatar", "attempt": 1}, EXIT_OK


# --------------------------------------------------------------------------
# Phase: reject_frame (resume after a "no" reply to the frame gate — clears
# this attempt without consuming the URL, so the same link can be retried)

def run_reject_frame(cfg: Config, tiktok_id: str, work: Path, logger) -> tuple[dict, int]:
    try:
        pending = load_pending(cfg.paths.work_dir, tiktok_id)
    except PendingApprovalError as exc:
        logger.error("%s", exc)
        return {"status": "error", "id": tiktok_id, "stage": "pending_lookup",
                "error": str(exc)}, EXIT_INFRA

    url = pending["url"]
    result, code = _clear_without_consuming(
        cfg, tiktok_id, url, work, "frame_rejected",
        "operator rejected the raw extracted frame — source clip not usable",
        logger,
    )
    _notify(
        cfg,
        f"{tiktok_id}: frame rejected. No final Kling video was produced, so "
        "the link was not marked processed and can be tried again.",
        logger,
    )
    return result, code


# --------------------------------------------------------------------------
# Phase: animate (resume after a "yes" reply to the avatar gate -> FR-5..FR-8)

def run_animate(cfg: Config, tiktok_id: str, work: Path, logger) -> tuple[dict, int]:
    try:
        pending = load_pending(cfg.paths.work_dir, tiktok_id)
    except PendingApprovalError as exc:
        logger.error("%s", exc)
        return {"status": "error", "id": tiktok_id, "stage": "pending_lookup",
                "error": str(exc)}, EXIT_INFRA

    url = pending["url"]
    ref = Path(pending["ref_video_path"])
    avatar_frame = Path(pending["avatar_frame_path"])

    try:
        image_out_dir = cfg.paths.image_out_dir
        image_out_dir.mkdir(parents=True, exist_ok=True)
        image_dest = image_out_dir / timestamped_filename(tiktok_id, "image", avatar_frame.suffix)
        shutil.copy2(avatar_frame, image_dest)
        logger.info("saved approved avatar image -> %s", image_dest)
    except OSError as exc:
        logger.warning("could not save avatar image to image-out (non-fatal): %s", exc)

    try:
        ref = _cap_reference_video(cfg, ref, work, logger, url=url)
        processing_note = _reference_processing_note(work)
        if processing_note and processing_note != str(pending.get("processing_note") or ""):
            save_pending(
                cfg.paths.work_dir, tiktok_id, stage=pending["stage"], url=url,
                ref_video_path=str(ref), frame1_path=pending["frame1_path"],
                avatar_frame_path=str(avatar_frame),
                attempt=int(pending.get("attempt", 1)),
                processing_note=processing_note,
            )
            _refresh_status_sheet(cfg, logger)
        raw_video, mean, attempts = animate_and_gate(cfg, tiktok_id, avatar_frame, ref, work, logger)
        dest = strip_and_publish(cfg, tiktok_id, url, raw_video, work, mean, logger)
    except StageFailure as exc:
        if exc.stage == "identity_gate":
            failed_videos = sorted(cfg.paths.video_out_dir.glob(f"{tiktok_id}-video-*.mp4"))
            if failed_videos:
                result, code = _record_video_failure(
                    cfg, tiktok_id, url, work, exc.stage, str(exc),
                    exc.cosine, failed_videos[-1], logger,
                )
                _notify_with_balance(
                    cfg,
                    f"{tiktok_id}: {exc.stage} failed: {exc}\n"
                    "A Kling video was produced and saved, so this link was "
                    "recorded as a terminal failed video outcome.",
                    logger,
                )
                _send_video(
                    cfg, failed_videos[-1],
                    f"{tiktok_id}: failed identity gate (best cosine "
                    f"{exc.cosine:.4f} < {cfg.identity.cosine_min}) — flagged, "
                    "not published.",
                    logger,
                )
                return result, code

        result, code = _clear_without_consuming(
            cfg, tiktok_id, url, work, exc.stage, str(exc), logger
        )
        _notify_with_balance(
            cfg,
            f"{tiktok_id}: {exc.stage} failed before a final Kling video was "
            f"produced: {exc}\nThis link was not marked processed and can be "
            "tried again.",
            logger,
        )
        return result, code
    except InfraFailure as exc:
        logger.error("infrastructure error at %s (URL not consumed): %s", exc.stage, exc)
        _notify(
            cfg,
            f"{tiktok_id}: animation stopped at {exc.stage}: {exc}\n"
            "URL was not consumed; fix the issue and retry.",
            logger,
        )
        return {"status": "error", "id": tiktok_id, "url": url,
                "stage": exc.stage, "error": str(exc)}, EXIT_INFRA
    except Exception as exc:
        logger.exception("unexpected error (URL not consumed)")
        _notify(
            cfg,
            f"{tiktok_id}: animation crashed unexpectedly: "
            f"{type(exc).__name__}: {exc}\nURL was not consumed.",
            logger,
        )
        return {"status": "error", "id": tiktok_id, "url": url, "stage": "unexpected",
                "error": f"{type(exc).__name__}: {exc}"}, EXIT_INFRA

    clear_pending(cfg.paths.work_dir, tiktok_id)
    _notify_with_balance(cfg, f"{tiktok_id}: published to {dest} (identity cosine {mean:.4f}).", logger)
    _send_video(cfg, dest, f"{tiktok_id}: published (identity cosine {mean:.4f}).", logger)
    logger.info("=== run complete: %s (mean cosine %.4f, %d attempt(s)) ===",
                dest, mean, attempts)
    return {"status": "published", "id": tiktok_id, "url": url, "output": str(dest),
            "cosine": round(mean, 4), "attempts": attempts}, EXIT_OK


# --------------------------------------------------------------------------
# Phase: regenerate (resume after a "no" reply -> redo FR-4 or give up)

def run_regenerate(cfg: Config, tiktok_id: str, work: Path, logger) -> tuple[dict, int]:
    try:
        pending = load_pending(cfg.paths.work_dir, tiktok_id)
    except PendingApprovalError as exc:
        logger.error("%s", exc)
        return {"status": "error", "id": tiktok_id, "stage": "pending_lookup",
                "error": str(exc)}, EXIT_INFRA

    url = pending["url"]
    frame1 = Path(pending["frame1_path"])
    next_attempt = pending["attempt"] + 1

    if next_attempt > cfg.telegram.max_approval_attempts:
        result, code = _clear_without_consuming(
            cfg, tiktok_id, url, work, "avatar_frame_rejected",
            f"operator rejected the avatar still "
            f"{cfg.telegram.max_approval_attempts} time(s); giving up",
            logger,
        )
        _notify(cfg, f"{tiktok_id}: gave up after "
                    f"{cfg.telegram.max_approval_attempts} rejected attempts. "
                    "No final Kling video was produced, so the link can be tried again.",
               logger)
        return result, code

    try:
        avatar_frame = make_avatar_frame(
            cfg, frame1, work,
            seed=attempt_seed(cfg.video.seed, next_attempt - 1),
            logger=logger,
        )
    except StageFailure as exc:
        return _clear_without_consuming(
            cfg, tiktok_id, url, work, exc.stage, str(exc), logger, notify=True
        )
    except InfraFailure as exc:
        logger.error("infrastructure error at %s (URL not consumed): %s", exc.stage, exc)
        return {"status": "error", "id": tiktok_id, "url": url,
                "stage": exc.stage, "error": str(exc)}, EXIT_INFRA

    save_pending(cfg.paths.work_dir, tiktok_id, stage="avatar", url=url,
                ref_video_path=pending["ref_video_path"], frame1_path=str(frame1),
                avatar_frame_path=str(avatar_frame), attempt=next_attempt,
                processing_note=str(pending.get("processing_note") or ""))
    _refresh_status_sheet(cfg, logger)
    _send_comparison_frame(cfg, tiktok_id, frame1, logger)
    try:
        send_photo(
            cfg, avatar_frame,
            f"{tiktok_id}: regenerated avatar still (attempt {next_attempt}/"
            f"{cfg.telegram.max_approval_attempts}). Reply yes to animate, "
            "no to regenerate.",
            logger=logger,
        )
    except TelegramError as exc:
        logger.error("failed to send Telegram approval request: %s", exc)
        return {"status": "error", "id": tiktok_id, "url": url,
                "stage": "telegram_notify", "error": str(exc)}, EXIT_INFRA

    return {"status": "pending_approval", "id": tiktok_id, "url": url,
            "attempt": next_attempt}, EXIT_OK


# --------------------------------------------------------------------------
# Phase: full (legacy all-in-one — manual/local testing only, never n8n)

def run_full(cfg: Config, tiktok_id: str, url: str, work: Path, logger) -> tuple[dict, int]:
    logger.warning(
        "--phase full bypasses the Telegram approval gate — manual/testing "
        "use only, never wire this into the scheduled n8n workflow"
    )
    state = load_state(cfg.paths.processed_json)
    if tiktok_id in seen_ids(state):
        status = "processed" if tiktok_id in state["processed"] else "flagged"
        logger.info("id %s already %s — nothing to do", tiktok_id, status)
        return {"status": "already_processed", "id": tiktok_id,
                "url": url, "previous_status": status}, EXIT_OK

    try:
        ref = download_reference(cfg, url, work, logger)
        frame1 = extract_frame(ref, work, logger)
        avatar_frame = make_avatar_frame(cfg, frame1, work,
                                         seed=attempt_seed(cfg.video.seed, 0),
                                         logger=logger)
        try:
            image_out_dir = cfg.paths.image_out_dir
            image_out_dir.mkdir(parents=True, exist_ok=True)
            image_dest = image_out_dir / timestamped_filename(tiktok_id, "image", avatar_frame.suffix)
            shutil.copy2(avatar_frame, image_dest)
            logger.info("saved approved avatar image -> %s", image_dest)
        except OSError as exc:
            logger.warning("could not save avatar image to image-out (non-fatal): %s", exc)
        raw_video, mean, attempts = animate_and_gate(cfg, tiktok_id, avatar_frame, ref, work, logger)
        dest = strip_and_publish(cfg, tiktok_id, url, raw_video, work, mean, logger)
    except StageFailure as exc:
        return _flag_and_record(cfg, tiktok_id, url, work, exc.stage, str(exc),
                                exc.cosine, logger)
    except InfraFailure as exc:
        logger.error("infrastructure error at %s (URL not consumed): %s", exc.stage, exc)
        return {"status": "error", "id": tiktok_id, "url": url,
                "stage": exc.stage, "error": str(exc)}, EXIT_INFRA
    except Exception as exc:
        logger.exception("unexpected error (URL not consumed)")
        return {"status": "error", "id": tiktok_id, "url": url, "stage": "unexpected",
                "error": f"{type(exc).__name__}: {exc}"}, EXIT_INFRA

    logger.info("=== run complete: %s (mean cosine %.4f, %d attempt(s)) ===",
                dest, mean, attempts)
    return {"status": "published", "id": tiktok_id, "url": url,
            "output": str(dest), "cosine": round(mean, 4),
            "attempts": attempts}, EXIT_OK


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=None,
                        help="required for --phase prepare/full")
    parser.add_argument("--id", default=None,
                        help="TikTok id; required for --phase generate_avatar/"
                             "reject_frame/animate/regenerate, derived from --url otherwise")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--phase",
                        choices=["full", "prepare", "generate_avatar", "reject_frame",
                                 "animate", "regenerate"],
                        default="full")
    args = parser.parse_args(argv)

    def emit(result: dict, code: int) -> int:
        print(json.dumps(result))
        return code

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        get_logger("worker").error("%s", exc)
        return emit({"status": "error", "stage": "config", "error": str(exc)}, EXIT_INFRA)

    if args.dry_run:
        tiktok_id = args.id or extract_tiktok_id(args.url)
        logger = get_logger("worker")  # stderr only — dry run writes nothing
        return emit(dry_run(cfg, tiktok_id, args.url, logger), EXIT_OK)

    if args.phase in ("generate_avatar", "reject_frame", "animate", "regenerate"):
        if not args.id:
            return emit({"status": "error", "stage": "args",
                         "error": f"--phase {args.phase} requires --id"}, EXIT_INFRA)
        tiktok_id = args.id
    else:
        if not args.url:
            return emit({"status": "error", "stage": "args",
                         "error": f"--phase {args.phase} requires --url"}, EXIT_INFRA)
        tiktok_id = args.id or extract_tiktok_id(args.url)

    work = cfg.paths.work_dir / tiktok_id
    work.mkdir(parents=True, exist_ok=True)
    logger = get_logger("worker", work / "run.log")
    logger.info("=== phase=%s start id=%s ===", args.phase, tiktok_id)

    if args.phase == "prepare":
        result, code = run_prepare(cfg, tiktok_id, args.url, work, logger)
    elif args.phase == "generate_avatar":
        result, code = run_generate_avatar(cfg, tiktok_id, work, logger)
    elif args.phase == "reject_frame":
        result, code = run_reject_frame(cfg, tiktok_id, work, logger)
    elif args.phase == "animate":
        result, code = run_animate(cfg, tiktok_id, work, logger)
    elif args.phase == "regenerate":
        result, code = run_regenerate(cfg, tiktok_id, work, logger)
    else:
        result, code = run_full(cfg, tiktok_id, args.url, work, logger)

    return emit(result, code)


if __name__ == "__main__":
    sys.exit(main())
