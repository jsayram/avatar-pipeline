"""Telegram bot notifications for the FR-4 human-approval loop.

This is the operator's review channel: send the generated avatar still,
receive a yes/no reply (handled by a separate n8n webhook workflow, not this
module — this module only *sends*). Kept deliberately tiny and separate from
lib/animation_providers.py / lib/avatar_frame_providers.py since it's not a
provider, just a notification channel.

Setup: create a bot via @BotFather in Telegram (no business verification,
no template approval — unlike WhatsApp Business API), get a bot token,
`export TELEGRAM_BOT_TOKEN=...` (or put it in .env — see worker.py's
load_dotenv() call). Message the bot once from the account that should
receive approvals so Telegram allows it to message back; then set
telegram.chat_id in config.yaml to that chat's id (get it from
`https://api.telegram.org/bot<token>/getUpdates` after sending the bot a
message).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import requests


class TelegramError(Exception):
    """Sending a Telegram message/photo failed."""


class TelegramNotConfigured(TelegramError):
    """Telegram isn't set up — fails clearly rather than silently no-op,
    since a missed approval message means a run silently stalls forever."""


def _base_url(config) -> str:
    token = os.environ.get(config.telegram.bot_token_env)
    if not token:
        raise TelegramNotConfigured(
            f"environment variable {config.telegram.bot_token_env} is not set. "
            "Create a bot via @BotFather in Telegram and "
            f"`export {config.telegram.bot_token_env}=<token>` (or add it to "
            ".env). Never put the token in config.yaml."
        )
    return f"https://api.telegram.org/bot{token}"


def _require_configured(config) -> None:
    if not config.telegram.enabled:
        raise TelegramNotConfigured(
            "telegram.enabled is false in config.yaml — set it to true to "
            "use the Telegram approval loop."
        )
    if not config.telegram.chat_id:
        raise TelegramNotConfigured(
            "telegram.chat_id is empty in config.yaml. Message your bot once "
            "from the account that should receive approvals, then look up "
            "the chat id at https://api.telegram.org/bot<token>/getUpdates "
            "and set telegram.chat_id."
        )


def send_photo(config, image_path: Path, caption: str,
               logger: logging.Logger | None = None) -> None:
    """Send a photo (the extracted frame, the generated avatar still, etc.)
    — caption identifies which one for the operator."""
    logger = logger or logging.getLogger("telegram")
    _require_configured(config)
    base = _base_url(config)
    image_path = Path(image_path)
    if not image_path.exists():
        raise TelegramError(f"image not found: {image_path}")
    try:
        with image_path.open("rb") as fh:
            resp = requests.post(
                f"{base}/sendPhoto",
                data={"chat_id": config.telegram.chat_id, "caption": caption},
                files={"photo": (image_path.name, fh, "image/png")},
                timeout=60,
            )
    except requests.RequestException as exc:
        raise TelegramError(f"sendPhoto failed: {exc}") from exc
    if resp.status_code >= 400:
        raise TelegramError(f"sendPhoto returned {resp.status_code}: {resp.text[:500]}")
    logger.info("sent photo to Telegram: %s", caption[:80])


def send_video(config, video_path: Path, caption: str,
               logger: logging.Logger | None = None) -> None:
    """Send the final animation output (published or a failed gate attempt)
    so the operator can review it directly in Telegram, no need to dig
    through video-out/. Telegram's standard Bot API caps uploads at 50MB —
    raises TelegramError (not silently truncated) if the file's too big or
    the upload otherwise fails; callers should treat this as best-effort."""
    logger = logger or logging.getLogger("telegram")
    _require_configured(config)
    base = _base_url(config)
    video_path = Path(video_path)
    if not video_path.exists():
        raise TelegramError(f"video not found: {video_path}")
    try:
        with video_path.open("rb") as fh:
            resp = requests.post(
                f"{base}/sendVideo",
                data={"chat_id": config.telegram.chat_id, "caption": caption,
                      "supports_streaming": True},
                files={"video": (video_path.name, fh, "video/mp4")},
                timeout=300,
            )
    except requests.RequestException as exc:
        raise TelegramError(f"sendVideo failed: {exc}") from exc
    if resp.status_code >= 400:
        raise TelegramError(f"sendVideo returned {resp.status_code}: {resp.text[:500]}")
    logger.info("sent video to Telegram: %s", caption[:80])


def send_message(config, text: str, logger: logging.Logger | None = None) -> None:
    """Plain text notification (e.g. published, or gave up after N attempts)."""
    logger = logger or logging.getLogger("telegram")
    _require_configured(config)
    base = _base_url(config)
    try:
        resp = requests.post(
            f"{base}/sendMessage",
            data={"chat_id": config.telegram.chat_id, "text": text},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise TelegramError(f"sendMessage failed: {exc}") from exc
    if resp.status_code >= 400:
        raise TelegramError(f"sendMessage returned {resp.status_code}: {resp.text[:500]}")
    logger.info("sent Telegram message: %s", text[:80])
