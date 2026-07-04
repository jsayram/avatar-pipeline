"""Config loading (deliverable: tests for config loading)."""
import pytest

from conftest import REPO_DIR
from lib.config import ConfigError, load_config


def test_example_config_loads_with_expected_defaults():
    cfg = load_config(REPO_DIR / "config.example.yaml")
    assert cfg.avatar_frame.provider == "local_comfyui"
    assert cfg.avatar_frame.wavespeed_model == "bytedance/seedream-v4.5/edit"
    assert cfg.avatar_frame.identity_references == ()
    assert cfg.animation.provider == "local_comfyui"
    assert cfg.wavespeed.enabled is False          # WaveSpeed off by default
    assert cfg.wavespeed.api_key_env == "WAVESPEED_API_KEY"
    assert cfg.wavespeed.model == "kwaivgi/kling-v3.0-pro/motion-control"
    assert cfg.video.max_clip_seconds == 10
    assert cfg.schedule.cron == "0 2 * * *"
    assert cfg.identity.cosine_min == 0.88
    assert cfg.notifications.provider == "macos"
    assert cfg.video.cookies_file is None
    assert cfg.endpoints.n8n_url == "http://localhost:5678"
    assert cfg.dashboard.port == 8190
    assert cfg.dashboard.log_tail_lines == 200
    assert cfg.dashboard.launchd_labels["dashboard"] == "com.jramirez.avatar.dashboard"


def test_cookies_file_resolves_relative_to_config_dir(make_config, tmp_path):
    config_path = make_config(video={"cookies_file": "./tiktok_cookies.txt"})
    cfg = load_config(config_path)
    assert cfg.video.cookies_file == tmp_path / "tiktok_cookies.txt"


def test_relative_paths_resolve_against_config_dir(make_config, tmp_path):
    config_path = make_config(paths={"work_dir": "./relative_work"})
    cfg = load_config(config_path)
    assert cfg.paths.work_dir == (tmp_path / "relative_work").resolve()


def test_missing_config_file_raises():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/config.yaml")


def test_missing_required_path_raises(make_config, tmp_path):
    config_path = make_config(paths={"lora_path": None})
    with pytest.raises(ConfigError, match="lora_path"):
        load_config(config_path)


def test_invalid_animation_provider_raises(make_config):
    config_path = make_config(animation={"provider": "runpod"})
    with pytest.raises(ConfigError, match="animation.provider"):
        load_config(config_path)


def test_invalid_avatar_frame_provider_raises(make_config):
    config_path = make_config(avatar_frame={"provider": "seedance"})
    with pytest.raises(ConfigError, match="avatar_frame.provider"):
        load_config(config_path)


def test_invalid_avatar_frame_identity_references_raises(make_config):
    config_path = make_config(avatar_frame={"identity_references": "not-a-list"})
    with pytest.raises(ConfigError, match="identity_references"):
        load_config(config_path)


def test_avatar_frame_identity_references_resolve(make_config, tmp_path):
    config_path = make_config(avatar_frame={"identity_references": ["./refs/front.png"]})
    cfg = load_config(config_path)
    assert cfg.avatar_frame.identity_references == ((tmp_path / "refs" / "front.png").resolve(),)


def test_invalid_cosine_min_raises(make_config):
    config_path = make_config(identity={"cosine_min": 1.5})
    with pytest.raises(ConfigError, match="cosine_min"):
        load_config(config_path)
