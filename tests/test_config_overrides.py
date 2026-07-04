import pytest

from lib.config import ConfigError, load_config
from lib.config_overrides import (
    ConfigOverrideError,
    clear_overrides,
    load_overrides,
    overrides_path,
    write_overrides,
    write_provider_overrides,
)


def test_provider_overrides_merge_into_load_config(make_config):
    config_path = make_config()

    write_provider_overrides(
        config_path,
        avatar_frame_provider="wavespeed_seedream",
        animation_provider="wavespeed",
        wavespeed_enabled=True,
    )

    cfg = load_config(config_path)
    base = load_config(config_path, apply_overrides=False)

    assert cfg.avatar_frame.provider == "wavespeed_seedream"
    assert cfg.animation.provider == "wavespeed"
    assert cfg.wavespeed.enabled is True
    assert base.avatar_frame.provider == "local_comfyui"
    assert base.animation.provider == "local_comfyui"
    assert base.wavespeed.enabled is False


def test_unknown_override_section_is_rejected(make_config):
    config_path = make_config()
    overrides_path(config_path).write_text("paths:\n  out_dir: /tmp/nope\n")

    with pytest.raises(ConfigError, match="not allowed"):
        load_config(config_path)


def test_unknown_override_key_is_rejected(make_config):
    config_path = make_config()

    with pytest.raises(ConfigOverrideError, match="not allowed"):
        write_overrides(config_path, {"wavespeed": {"api_key_env": "BAD"}})


def test_wavespeed_enabled_override_must_be_bool(make_config):
    config_path = make_config()

    with pytest.raises(ConfigOverrideError, match="true or false"):
        write_overrides(config_path, {"wavespeed": {"enabled": "true"}})


def test_clear_overrides_removes_overlay_file(make_config):
    config_path = make_config()
    write_provider_overrides(
        config_path,
        avatar_frame_provider="mock",
        animation_provider="mock",
        wavespeed_enabled=False,
    )

    clear_overrides(config_path)

    assert not overrides_path(config_path).exists()
    assert load_overrides(config_path) == {}
