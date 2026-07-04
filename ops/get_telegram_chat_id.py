#!/usr/bin/env python3
"""One-off helper: fetch your Telegram chat_id via the bot's getUpdates API.

Prints ONLY chat id, name, and message text from the RESPONSE — never the
bot token or the request URL (which embeds the token in its path). Safe to
run and share output from, unlike the token itself.

Prerequisite: send your bot at least one message first (e.g. "hi") — Telegram
requires this before a bot can message a chat, and getUpdates has nothing to
return until you have.

Usage: ./venv/bin/python ops/get_telegram_chat_id.py
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

token = os.environ.get("TELEGRAM_BOT_TOKEN")
if not token:
    print("TELEGRAM_BOT_TOKEN not set (check .env)")
    sys.exit(1)

resp = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=15)
if resp.status_code >= 400:
    print(f"getUpdates failed: HTTP {resp.status_code}")
    sys.exit(1)

body = resp.json()
if not body.get("ok"):
    print(f"getUpdates error: {body.get('description', 'unknown')}")
    sys.exit(1)

results = body.get("result", [])
if not results:
    print("No messages found — make sure you sent your bot a message first.")
    sys.exit(1)

seen = set()
for update in results:
    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None or chat_id in seen:
        continue
    seen.add(chat_id)
    print(f"chat_id: {chat_id}  |  name: {chat.get('first_name', '')} "
          f"{chat.get('last_name', '')}  |  last message: {msg.get('text', '')!r}")
