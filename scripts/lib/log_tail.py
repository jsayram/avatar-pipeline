"""Allowlisted log tailing for the local dashboard."""
from __future__ import annotations

import re
from pathlib import Path


class LogTailError(Exception):
    """Raised when a requested dashboard log is not allowed or not found."""


LOG_REGISTRY = {
    "n8n": Path("~/Library/Logs/avatar-n8n.log").expanduser(),
    "comfyui": Path("~/Library/Logs/avatar-comfyui.log").expanduser(),
    "gate": Path("~/Library/Logs/avatar-gate.log").expanduser(),
    "dashboard": Path("~/Library/Logs/avatar-dashboard.log").expanduser(),
}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def tail_file(path: Path, *, lines: int = 200, block_size: int = 8192) -> list[str]:
    path = Path(path)
    lines = max(1, min(int(lines), 1000))
    if not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, 2)
        end = fh.tell()
        chunks: list[bytes] = []
        remaining = end
        newline_count = 0
        while remaining > 0 and newline_count <= lines:
            read_size = min(block_size, remaining)
            remaining -= read_size
            fh.seek(remaining)
            chunk = fh.read(read_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")
    text = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    return text.splitlines()[-lines:]


def resolve_log_path(cfg, name: str) -> Path:
    if name.startswith("run:"):
        run_id = name.removeprefix("run:")
        if not RUN_ID_RE.fullmatch(run_id):
            raise LogTailError("invalid run id")
        run_dir = (cfg.paths.work_dir / run_id).resolve()
        work_dir = cfg.paths.work_dir.resolve()
        if run_dir.parent != work_dir or not run_dir.is_dir():
            raise LogTailError("run id not found")
        return run_dir / "run.log"

    try:
        return LOG_REGISTRY[name]
    except KeyError as exc:
        raise LogTailError("unknown log name") from exc


def tail_log(cfg, name: str, *, lines: int | None = None) -> dict:
    count = cfg.dashboard.log_tail_lines if lines is None else lines
    path = resolve_log_path(cfg, name)
    return {
        "name": name,
        "exists": path.exists(),
        "lines": tail_file(path, lines=count),
    }
