"""Check WaveSpeed AI account balance — so the operator always has cost
visibility right after a paid run, without having to log in separately.

Verified 2026-07-03 against https://wavespeed.ai/docs/docs-common-api/balance:
    GET {api_base}/api/v3/balance
    Authorization: Bearer <WAVESPEED_API_KEY>
    -> {"code": 200, "message": "success", "data": {"balance": 50.25}}
"""
from __future__ import annotations

import os

import requests

DASHBOARD_URL = "https://wavespeed.ai"


class WaveSpeedBalanceError(Exception):
    """Could not fetch the WaveSpeed balance."""


def get_balance(config) -> float:
    api_key = os.environ.get(config.wavespeed.api_key_env)
    if not api_key:
        raise WaveSpeedBalanceError(
            f"environment variable {config.wavespeed.api_key_env} is not set"
        )
    try:
        resp = requests.get(
            f"{config.wavespeed.api_base}/api/v3/balance",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise WaveSpeedBalanceError(f"balance request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise WaveSpeedBalanceError(
            f"balance endpoint returned {resp.status_code}: {resp.text[:300]}"
        )
    try:
        return float(resp.json()["data"]["balance"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WaveSpeedBalanceError(
            f"unexpected balance response shape: {resp.text[:300]}"
        ) from exc
