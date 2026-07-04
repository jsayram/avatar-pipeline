#!/usr/bin/env python3
"""Telegram reply handler for the two-gate approval loop.

Triggered by n8n's Telegram Trigger node whenever a message arrives at the
bot (see n8n/workflow_telegram_reply.json). Classifies the incoming text
into one of three things:

  1. yes/no — resolves the single outstanding pending-approval id (see
     lib/pending.py — at most one is ever allowed outstanding), sends an
     immediate Telegram acknowledgement of what's about to happen (e.g.
     "sent to Seedream — generating the avatar still, processing... please
     wait"), then dispatches to the right worker.py phase based on the
     pending record's `stage` field:
       stage="frame":  yes -> generate_avatar   no -> reject_frame
       stage="avatar": yes -> animate           no -> regenerate
  2. a TikTok link — sends an immediate Telegram acknowledgement (archived,
     with the archive file path), logs it to the telegram_links_archive
     "keep" file (see lib/telegram_links_archive.py — NOT read by
     pick_next.py, purely a record), and kicks off `worker.run_prepare()`
     for it. Declined (with a Telegram notice) in either of two cases —
     only one link is ever in flight, end to end:
       - a pending approval already exists (GATE 1 or GATE 2 is currently
         awaiting a yes/no reply) — see lib/pending.py's find_pending_id().
       - an earlier link is still being downloaded/prepared, i.e. BEFORE its
         first pending-approval record even exists yet — see
         lib/processing_lock.py, which closes that narrower race window.
     If the link turns out to already be processed or flagged,
     `run_prepare()`'s "already_processed" result is turned into an explicit
     Telegram message too (previously silent).
  3. anything else — a clean no-op, not an error, so a stray "hi" while
     testing the bot doesn't break anything.

Usage:
    python scripts/handle_telegram_reply.py --text "<message text>" --config config.yaml

Prints one JSON line, same shape as worker.py's phase results, plus:
    {"status": "ignored", "text": ...}    exit 0 — not a recognized yes/no/
                                          link, no pending approval to act
                                          on, or a new link declined because
                                          one's already pending/in-progress
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import worker  # noqa: E402  (after sys.path/dotenv setup, reuses the run_* phase functions)
from lib.approval_lock import ApprovalLockHeldError  # noqa: E402
from lib.approval_lock import release as release_approval_lock  # noqa: E402
from lib.approval_lock import try_claim as try_claim_approval  # noqa: E402
from lib.config import ConfigError, load_config  # noqa: E402
from lib.logging_utils import get_logger  # noqa: E402
from lib.pending import PendingApprovalError, find_pending_id, load_pending  # noqa: E402
from lib.processing_lock import LockHeldError  # noqa: E402
from lib.processing_lock import release as release_lock  # noqa: E402
from lib.processing_lock import try_acquire as try_acquire_lock  # noqa: E402
from lib.state import extract_tiktok_id  # noqa: E402
from lib.telegram_links_archive import append_link, update_processing_note  # noqa: E402

YES_WORDS = {"yes", "y", "approve", "approved", "ok", "okay", "\U0001F44D", "✅"}
NO_WORDS = {"no", "n", "reject", "rejected", "regenerate", "\U0001F44E", "❌"}


def _looks_like_tiktok_url(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith(("http://", "https://")) and "tiktok.com" in t


def _handle_yes_no(cfg, text: str, is_yes: bool, logger) -> int:
    try:
        tiktok_id = find_pending_id(cfg.paths.work_dir)
    except PendingApprovalError as exc:
        logger.warning("no action taken: %s", exc)
        print(json.dumps({"status": "ignored", "text": text, "reason": str(exc)}))
        return 0

    try:
        try_claim_approval(cfg.paths.work_dir, tiktok_id, "telegram")
    except ApprovalLockHeldError as exc:
        logger.info("declining approval reply — %s", exc)
        worker._notify(
            cfg,
            f"{tiktok_id}: that approval is already being processed. "
            "Wait for the current phase to finish before replying again.",
            logger,
        )
        print(json.dumps({"status": "ignored", "text": text, "reason": str(exc)}))
        return 0

    try:
        work = cfg.paths.work_dir / tiktok_id
        run_logger = get_logger("worker", work / "run.log")
        pending = load_pending(cfg.paths.work_dir, tiktok_id)
        stage = pending["stage"]

        if is_yes:
            if stage == "frame":
                logger.info("reply %r for id=%s (stage=frame) -> generate_avatar", text, tiktok_id)
                worker._notify(cfg, f"{tiktok_id}: sent to Seedream — generating the "
                                    "avatar still, processing... please wait.", logger)
                result, code = worker.run_generate_avatar(cfg, tiktok_id, work, run_logger)
            else:
                logger.info("reply %r for id=%s (stage=avatar) -> animate", text, tiktok_id)
                worker._notify(cfg, f"{tiktok_id}: sent to WaveSpeed Kling — animating, "
                                    "processing... please wait.", logger)
                result, code = worker.run_animate(cfg, tiktok_id, work, run_logger)
        else:
            if stage == "frame":
                logger.info("reply %r for id=%s (stage=frame) -> reject_frame", text, tiktok_id)
                worker._notify(cfg, f"{tiktok_id}: got it — clearing this frame attempt "
                                    "without marking the link processed. Processing...", logger)
                result, code = worker.run_reject_frame(cfg, tiktok_id, work, run_logger)
            else:
                logger.info("reply %r for id=%s (stage=avatar) -> regenerate", text, tiktok_id)
                worker._notify(cfg, f"{tiktok_id}: got it — regenerating the avatar "
                                    "still via Seedream with a new seed, processing... "
                                    "please wait.", logger)
                result, code = worker.run_regenerate(cfg, tiktok_id, work, run_logger)
    finally:
        release_approval_lock(cfg.paths.work_dir, tiktok_id)

    print(json.dumps(result))
    return code


def _resend_pending_approval(cfg, tiktok_id: str, pending: dict, logger) -> None:
    stage = pending["stage"]
    if stage == "frame":
        worker.send_photo(
            cfg, Path(pending["frame1_path"]),
            f"{tiktok_id}: this link is already waiting on the extracted "
            "frame approval. Re-sending the same frame; reply yes to generate "
            "the avatar still, no to clear this attempt.",
            logger=logger,
        )
        return

    worker._send_comparison_frame(cfg, tiktok_id, Path(pending["frame1_path"]), logger)
    worker.send_photo(
        cfg, Path(pending["avatar_frame_path"]),
        f"{tiktok_id}: this link is already waiting on avatar-still approval. "
        "Re-sending the last generated still; reply yes to animate, no to regenerate.",
        logger=logger,
    )


def _handle_new_link(cfg, url: str, logger) -> int:
    tiktok_id = extract_tiktok_id(url)

    try:
        existing_id = find_pending_id(cfg.paths.work_dir)
    except PendingApprovalError:
        existing_id = None

    if existing_id is not None:
        if existing_id == tiktok_id:
            try:
                pending = load_pending(cfg.paths.work_dir, existing_id)
                _resend_pending_approval(cfg, existing_id, pending, logger)
                result = {
                    "status": "pending_approval",
                    "id": existing_id,
                    "url": pending["url"],
                    "stage": pending["stage"],
                    "reason": "same link already pending; resent existing approval frame",
                }
                print(json.dumps(result))
                return 0
            except Exception as exc:  # noqa: BLE001 — fall back to a text notice
                logger.warning("could not resend pending approval media: %s", exc)
                worker._notify(
                    cfg,
                    f"{existing_id}: this same link is already pending, but I "
                    f"could not resend the saved media: {exc}. Reply yes/no "
                    "to the current pending approval.",
                    logger,
                )
                print(json.dumps({
                    "status": "pending_approval",
                    "id": existing_id,
                    "text": url,
                    "reason": "same link already pending",
                }))
                return 0

        reason = f"pending approval already outstanding for {existing_id}"
        logger.info("declining new link — %s", reason)
        worker._notify(
            cfg,
            f"Still waiting on your approval for {existing_id} — reply "
            "yes/no to that before texting a new link.",
            logger,
        )
        print(json.dumps({"status": "ignored", "text": url, "reason": reason}))
        return 0

    try:
        try_acquire_lock(cfg.paths.work_dir, tiktok_id)
    except LockHeldError as exc:
        logger.info("declining new link — %s", exc)
        worker._notify(
            cfg,
            f"Still downloading/preparing an earlier link — {exc} "
            "Try again in a moment.",
            logger,
        )
        print(json.dumps({"status": "ignored", "text": url, "reason": str(exc)}))
        return 0

    try:
        archive_path = cfg.paths.telegram_links_archive
        try:
            append_link(archive_path, url)
            archived_ok = True
        except Exception as exc:  # noqa: BLE001 — archive failure must not block processing
            logger.warning("could not append to telegram links archive (non-fatal): %s", exc)
            archived_ok = False

        worker._notify(
            cfg,
            f"Link received: {url}\n"
            + (f"Logged to {archive_path}\n" if archived_ok
               else "(could not log to the archive — continuing anyway)\n")
            + "Downloading + extracting frame now...",
            logger,
        )

        work = cfg.paths.work_dir / tiktok_id
        run_logger = get_logger("worker", work / "run.log")
        logger.info("new link texted in via Telegram: id=%s url=%s", tiktok_id, url)
        result, code = worker.run_prepare(cfg, tiktok_id, url, work, run_logger)

        processing_note = result.get("processing_note")
        if archived_ok and processing_note:
            try:
                update_processing_note(archive_path, url, str(processing_note))
            except Exception as exc:  # noqa: BLE001 — archive failure must not block processing
                logger.warning("could not update telegram link processing note (non-fatal): %s", exc)

        if result.get("status") == "already_processed":
            worker._notify(
                cfg,
                f"{tiktok_id} was already {result.get('previous_status')} — "
                "see done.csv / out-pipe for the existing result. Nothing "
                "new was started.",
                logger,
            )
    finally:
        release_lock(cfg.paths.work_dir)

    print(json.dumps(result))
    return code


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True, help="the Telegram message text")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)

    logger = get_logger("telegram_reply")

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        logger.error("%s", exc)
        print(json.dumps({"status": "error", "stage": "config", "error": str(exc)}))
        return 3

    text = args.text.strip()
    normalized = text.lower()

    if normalized in YES_WORDS:
        return _handle_yes_no(cfg, text, True, logger)
    if normalized in NO_WORDS:
        return _handle_yes_no(cfg, text, False, logger)
    if _looks_like_tiktok_url(text):
        return _handle_new_link(cfg, text, logger)

    logger.info("ignoring non-approval, non-link message: %r", text)
    print(json.dumps({"status": "ignored", "text": text}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
