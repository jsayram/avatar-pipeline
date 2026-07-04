import sys
from pathlib import Path

import pytest
import yaml

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR / "scripts"))


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@pytest.fixture
def make_config(tmp_path):
    """Write a valid config.yaml into tmp_path and return its path.

    All paths live inside tmp_path so tests never touch the repo or real state.
    Pass nested dicts to override sections, e.g.
    make_config(animation={"provider": "wavespeed"}).
    """

    def _make(**overrides) -> Path:
        base = {
            "paths": {
                "numbers_sheet": str(tmp_path / "links.numbers"),
                "work_dir": str(tmp_path / "work"),
                "out_dir": str(tmp_path / "out"),
                "processed_json": str(tmp_path / "processed.json"),
                "done_csv": str(tmp_path / "done.csv"),
                "lora_path": str(tmp_path / "loras" / "avatar_v1.safetensors"),
                "avatar_reference": str(tmp_path / "avatar_reference.png"),
                "workflows_dir": str(tmp_path / "comfyui"),
            },
            "endpoints": {
                "comfyui_url": "http://localhost:8188",
                "gate_url": "http://localhost:8189",
                "n8n_url": "http://localhost:5678",
            },
            "identity": {"cosine_min": 0.88, "sample_fps": 1, "max_retries": 2},
            "video": {
                "base_model": "flux1-schnell",
                "wan_model": "wan2.2-animate",
                "max_clip_seconds": 12,
                "seed": None,
            },
            "animation": {
                "provider": "local_comfyui",
                "fallback_to_local_on_cloud_error": True,
            },
            "avatar_frame": {
                "provider": "local_comfyui",
                "wavespeed_model": "bytedance/seedream-v4.5/edit",
                "size": "",
                "identity_references": [],
                "prompt": "",
            },
            "wavespeed": {
                "enabled": False,
                "api_base": "https://api.wavespeed.ai",
                "api_key_env": "WAVESPEED_API_KEY",
                "model": "",
            },
            "schedule": {"cron": "0 2 * * *"},
            "notifications": {"enabled": True, "provider": "macos"},
            "dashboard": {
                "port": 8190,
                "log_tail_lines": 200,
                "launchd_labels": {},
            },
        }
        merged = _deep_merge(base, overrides)
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.safe_dump(merged))
        return config_path

    return _make
