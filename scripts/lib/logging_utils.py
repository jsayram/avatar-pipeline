"""Logging: stderr always, plus an optional per-run file (work/<id>/run.log).

stdout is reserved for the machine-readable JSON result that n8n parses,
so no log handler may ever write there.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str, log_file: Path | None = None) -> logging.Logger:
    """Return a logger writing to stderr and, if given, to *log_file*.

    Calling it twice with the same name does not duplicate handlers.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    have_stderr = any(
        isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stderr
        for h in logger.handlers
    )
    if not have_stderr:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        logger.addHandler(sh)

    if log_file is not None:
        log_file = Path(log_file)
        have_file = any(
            isinstance(h, logging.FileHandler)
            and Path(getattr(h, "baseFilename", "")) == log_file.resolve()
            for h in logger.handlers
        )
        if not have_file:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
            logger.addHandler(fh)

    return logger
