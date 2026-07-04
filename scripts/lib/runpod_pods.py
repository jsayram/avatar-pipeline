"""List RunPod GPU pods — gives the dashboard an at-a-glance view of
what's running, stopped, or exited.

Verified 2026-07-03 against https://rest.runpod.io/v1 (OpenAPI spec):
    GET https://rest.runpod.io/v1/pods
    Authorization: Bearer <RUNPOD_API_KEY>
    -> [ { "id": "...", "name": "...", "desiredStatus": "RUNNING", ... } ]
"""
from __future__ import annotations

import os
from typing import Any

import requests

BASE_URL = "https://rest.runpod.io/v1"


class RunPodError(Exception):
    """Could not fetch RunPod pod information."""


def get_pods(api_key: str | None = None) -> dict[str, Any]:
    """Return a summary of all RunPod pods.

    If *api_key* is ``None`` the function checks ``RUNPOD_API_KEY`` in
    the environment.  When no key is available it returns a
    ``{"configured": False, ...}`` dict rather than raising so that the
    dashboard can display a helpful "not configured" message.
    """
    if api_key is None:
        api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        return {"configured": False, "detail": "RUNPOD_API_KEY not set"}

    try:
        resp = requests.get(
            f"{BASE_URL}/pods",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise RunPodError(f"pods request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise RunPodError(
            f"pods endpoint returned {resp.status_code}: {resp.text[:300]}"
        )

    try:
        raw_pods: list[dict] = resp.json()
    except (ValueError, TypeError) as exc:
        raise RunPodError(
            f"unexpected pods response shape: {resp.text[:300]}"
        ) from exc

    pods = [
        {
            "id": p.get("id"),
            "name": p.get("name"),
            "status": p.get("desiredStatus"),
            "gpu_type": p.get("machine", {}).get("gpuDisplayName"),
            "cost_per_hr": p.get("costPerHr"),
        }
        for p in raw_pods
    ]
    return {"configured": True, "pods": pods}
