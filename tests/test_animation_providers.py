"""Provider selection + WaveSpeed opt-in guarantees (FR-5 isolation)."""
import pytest

from conftest import REPO_DIR
from lib.animation_providers import (
    AnimationNotConfigured,
    LocalComfyUIAnimationProvider,
    MockAnimationProvider,
    WaveSpeedAnimationProvider,
    get_animation_provider,
)
from lib.config import load_config


def test_default_provider_is_local_comfyui(make_config):
    cfg = load_config(make_config())
    provider = get_animation_provider(cfg)
    assert isinstance(provider, LocalComfyUIAnimationProvider)
    assert provider.name == "local_comfyui"


def test_wavespeed_disabled_by_default_in_example_config():
    cfg = load_config(REPO_DIR / "config.example.yaml")
    assert cfg.animation.provider == "local_comfyui"
    assert cfg.wavespeed.enabled is False


def test_wavespeed_provider_requires_explicit_enable(make_config):
    """provider: wavespeed with enabled: false must hard-error, never call out."""
    cfg = load_config(make_config(animation={"provider": "wavespeed"}))
    with pytest.raises(AnimationNotConfigured, match="explicitly enabled"):
        get_animation_provider(cfg)


def test_wavespeed_selected_when_enabled(make_config):
    cfg = load_config(make_config(
        animation={"provider": "wavespeed"},
        wavespeed={"enabled": True, "model": "some/wan-model"},
    ))
    provider = get_animation_provider(cfg)
    assert isinstance(provider, WaveSpeedAnimationProvider)


def test_wavespeed_animate_refuses_without_model(make_config, tmp_path):
    cfg = load_config(make_config(
        animation={"provider": "wavespeed"},
        wavespeed={"enabled": True, "model": ""},
    ))
    provider = WaveSpeedAnimationProvider()
    with pytest.raises(AnimationNotConfigured, match="wavespeed.model is empty"):
        provider.animate(tmp_path / "a.png", tmp_path / "r.mp4",
                         tmp_path / "o.mp4", cfg)


def test_wavespeed_animate_refuses_without_api_key(make_config, tmp_path, monkeypatch):
    monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
    cfg = load_config(make_config(
        animation={"provider": "wavespeed"},
        wavespeed={"enabled": True, "model": "some/wan-model"},
    ))
    provider = WaveSpeedAnimationProvider()
    with pytest.raises(AnimationNotConfigured, match="WAVESPEED_API_KEY"):
        provider.animate(tmp_path / "a.png", tmp_path / "r.mp4",
                         tmp_path / "o.mp4", cfg)


def test_wavespeed_animate_refuses_when_disabled_even_if_called(make_config, tmp_path):
    """Defense in depth: the provider itself checks the enable flag."""
    cfg = load_config(make_config(wavespeed={"enabled": False, "model": "m"}))
    provider = WaveSpeedAnimationProvider()
    with pytest.raises(AnimationNotConfigured, match="enabled"):
        provider.animate(tmp_path / "a.png", tmp_path / "r.mp4",
                         tmp_path / "o.mp4", cfg)


def test_build_payload_shape(make_config):
    cfg = load_config(make_config(wavespeed={
        "character_orientation": "image", "keep_original_sound": True,
    }))
    payload = WaveSpeedAnimationProvider.build_payload(
        "https://example.com/a.png", "https://example.com/r.mp4", cfg)
    assert payload["image"] == "https://example.com/a.png"
    assert payload["video"] == "https://example.com/r.mp4"
    assert payload["character_orientation"] == "image"
    assert payload["keep_original_sound"] is True
    assert "prompt" not in payload  # empty by default, omitted not sent blank


def test_build_payload_includes_prompts_when_set(make_config):
    cfg = load_config(make_config(wavespeed={
        "character_orientation": "video", "prompt": "seductive selfie",
        "negative_prompt": "blurry",
    }))
    payload = WaveSpeedAnimationProvider.build_payload(
        "https://example.com/a.png", "https://example.com/r.mp4", cfg)
    assert payload["character_orientation"] == "video"
    assert payload["prompt"] == "seductive selfie"
    assert payload["negative_prompt"] == "blurry"


def test_mock_provider_selected_when_configured(make_config):
    cfg = load_config(make_config(animation={"provider": "mock"}))
    provider = get_animation_provider(cfg)
    assert isinstance(provider, MockAnimationProvider)
    assert provider.name == "mock"


def test_mock_provider_copies_reference_video(make_config, tmp_path):
    cfg = load_config(make_config(animation={"provider": "mock"}))
    provider = get_animation_provider(cfg)
    ref = tmp_path / "ref.mp4"
    ref.write_bytes(b"fake reference video bytes")
    output = tmp_path / "out.mp4"
    result = provider.animate(tmp_path / "avatar.png", ref, output, cfg, seed=1)
    assert result == output
    assert output.read_bytes() == b"fake reference video bytes"
