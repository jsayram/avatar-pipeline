"""Guarded runtime overrides for dashboard provider toggles.

Only three keys are allowed:
  - avatar_frame.provider
  - animation.provider
  - wavespeed.enabled

The overlay is machine-local, gitignored, and atomically rewritten.
"""
from __future__ import annotations

import os
import tempfile
from copy import deepcopy
from pathlib import Path

import yaml

OVERRIDES_FILENAME = "config.overrides.yaml"
ALLOWED_KEYS = {
    "avatar_frame": {"provider"},
    "animation": {"provider"},
    "wavespeed": {"enabled"},
}


class ConfigOverrideError(Exception):
    """Raised when config.overrides.yaml contains anything outside the whitelist."""


def overrides_path(config_path: str | Path) -> Path:
    return Path(config_path).expanduser().resolve().with_name(OVERRIDES_FILENAME)


def _validate_overrides(data: dict) -> None:
    if not isinstance(data, dict):
        raise ConfigOverrideError("config.overrides.yaml must contain a YAML mapping")
    for section, values in data.items():
        if section not in ALLOWED_KEYS:
            raise ConfigOverrideError(f"config override section '{section}' is not allowed")
        if not isinstance(values, dict):
            raise ConfigOverrideError(f"config override section '{section}' must be a mapping")
        for key, value in values.items():
            if key not in ALLOWED_KEYS[section]:
                raise ConfigOverrideError(f"config override key '{section}.{key}' is not allowed")
            if section == "wavespeed" and key == "enabled" and not isinstance(value, bool):
                raise ConfigOverrideError("config override 'wavespeed.enabled' must be true or false")
            if section != "wavespeed" and not isinstance(value, str):
                raise ConfigOverrideError(f"config override '{section}.{key}' must be a string")


def load_overrides(config_path: str | Path) -> dict:
    path = overrides_path(config_path)
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigOverrideError(f"invalid YAML in {path}: {exc}") from exc
    _validate_overrides(data)
    return data


def merge_overrides(base: dict, overrides: dict) -> dict:
    if not overrides:
        return base
    _validate_overrides(overrides)
    merged = deepcopy(base)
    for section, values in overrides.items():
        target = merged.setdefault(section, {})
        if not isinstance(target, dict):
            raise ConfigOverrideError(
                f"cannot merge override into non-mapping config section '{section}'"
            )
        target.update(values)
    return merged


def write_overrides(config_path: str | Path, overrides: dict) -> Path:
    _validate_overrides(overrides)
    path = overrides_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(overrides, fh, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def write_provider_overrides(
    config_path: str | Path,
    *,
    avatar_frame_provider: str,
    animation_provider: str,
    wavespeed_enabled: bool,
) -> Path:
    return write_overrides(config_path, {
        "avatar_frame": {"provider": avatar_frame_provider},
        "animation": {"provider": animation_provider},
        "wavespeed": {"enabled": wavespeed_enabled},
    })


def clear_overrides(config_path: str | Path) -> None:
    overrides_path(config_path).unlink(missing_ok=True)
