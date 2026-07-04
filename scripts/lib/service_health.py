"""Read-only health checks for the local dashboard."""
from __future__ import annotations

import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests


def probe_http(name: str, url: str, *, timeout: float = 2.0) -> dict[str, Any]:
    started = time.monotonic()
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        return {
            "name": name,
            "kind": "http",
            "status": "down",
            "url": url,
            "detail": str(exc),
            "latency_ms": None,
        }
    latency_ms = round((time.monotonic() - started) * 1000)
    return {
        "name": name,
        "kind": "http",
        "status": "ok" if response.ok else "error",
        "url": url,
        "detail": f"HTTP {response.status_code}",
        "latency_ms": latency_ms,
    }


def probe_launchctl(name: str, label: str, *, timeout: float = 2.0) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "name": f"launchd:{name}",
            "kind": "launchd",
            "status": "unknown",
            "label": label,
            "detail": str(exc),
        }
    detail = "loaded" if result.returncode == 0 else (result.stderr or result.stdout).strip()
    return {
        "name": f"launchd:{name}",
        "kind": "launchd",
        "status": "ok" if result.returncode == 0 else "down",
        "label": label,
        "detail": detail,
    }


def gather_service_health(cfg) -> dict[str, Any]:
    checks = [
        ("http", "n8n", cfg.endpoints.n8n_url),
        ("http", "comfyui", f"{cfg.endpoints.comfyui_url}/system_stats"),
        ("http", "gate", f"{cfg.endpoints.gate_url}/health"),
    ]
    for name, label in cfg.dashboard.launchd_labels.items():
        checks.append(("launchd", name, label))

    services: list[dict[str, Any]] = [{
        "name": "dashboard",
        "kind": "process",
        "status": "ok",
        "url": f"http://127.0.0.1:{cfg.dashboard.port}",
        "detail": "serving this page",
    }]

    with ThreadPoolExecutor(max_workers=min(8, len(checks) or 1)) as pool:
        futures = []
        for kind, name, target in checks:
            if kind == "http":
                futures.append(pool.submit(probe_http, name, target))
            else:
                futures.append(pool.submit(probe_launchctl, name, target))
        for future in as_completed(futures):
            services.append(future.result())

    return {
        "services": sorted(services, key=lambda item: item["name"]),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
