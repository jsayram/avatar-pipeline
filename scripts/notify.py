#!/usr/bin/env python3
"""Success notification (FR-9), gated by config.notifications.

macOS provider posts a local notification via osascript — fully offline.
The email provider is intentionally not implemented here: wire an n8n Send
Email node after the success IF instead (see SETUP.md). Disabled/none is a
clean no-op, so n8n can always call this unconditionally.

Usage: python scripts/notify.py --config config.yaml --title "..." --message "..."
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import load_config
from lib.logging_utils import get_logger


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--title", default="avatar-pipeline")
    parser.add_argument("--message", required=True)
    args = parser.parse_args(argv)

    logger = get_logger("notify")
    cfg = load_config(args.config)

    if not cfg.notifications.enabled or cfg.notifications.provider == "none":
        logger.info("notifications disabled — skipping")
        return 0

    if cfg.notifications.provider == "macos":
        # osascript string literals: escape backslashes and double quotes.
        def esc(s: str) -> str:
            return s.replace("\\", "\\\\").replace('"', '\\"')

        script = (
            f'display notification "{esc(args.message)}" '
            f'with title "{esc(args.title)}"'
        )
        result = subprocess.run(["osascript", "-e", script],
                                capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("osascript failed: %s", result.stderr.strip())
            return 1
        logger.info("macOS notification posted")
        return 0

    if cfg.notifications.provider == "email":
        logger.info(
            "notifications.provider is 'email' — email is sent by the n8n "
            "Send Email node, not this script (see SETUP.md); skipping here."
        )
        return 0

    logger.error("unknown notifications.provider %r", cfg.notifications.provider)
    return 1


if __name__ == "__main__":
    sys.exit(main())
