"""FR-4 avatar-frame provider selection + Seedream safety checks."""
import pytest

from lib.avatar_frame_providers import (
    DEFAULT_SEEDREAM_PROMPT,
    AvatarFrameNotConfigured,
    LocalComfyUIAvatarFrameProvider,
    MockAvatarFrameProvider,
    WaveSpeedSeedreamAvatarFrameProvider,
    get_avatar_frame_provider,
)
from lib.config import load_config


def test_default_avatar_frame_provider_is_local(make_config):
    cfg = load_config(make_config())
    provider = get_avatar_frame_provider(cfg)
    assert isinstance(provider, LocalComfyUIAvatarFrameProvider)
    assert provider.name == "local_comfyui"


def test_seedream_provider_selected_when_configured(make_config):
    cfg = load_config(make_config(
        avatar_frame={"provider": "wavespeed_seedream"},
        wavespeed={"enabled": True},
    ))
    provider = get_avatar_frame_provider(cfg)
    assert isinstance(provider, WaveSpeedSeedreamAvatarFrameProvider)


def test_seedream_refuses_when_wavespeed_disabled(make_config, tmp_path):
    cfg = load_config(make_config(
        avatar_frame={"provider": "wavespeed_seedream"},
        wavespeed={"enabled": False},
    ))
    provider = WaveSpeedSeedreamAvatarFrameProvider()
    with pytest.raises(AvatarFrameNotConfigured, match="wavespeed.enabled"):
        provider.generate(tmp_path / "frame.png", tmp_path / "out.png", cfg)


def test_seedream_refuses_without_model(make_config, tmp_path):
    cfg = load_config(make_config(
        avatar_frame={"provider": "wavespeed_seedream", "wavespeed_model": ""},
        wavespeed={"enabled": True},
    ))
    provider = WaveSpeedSeedreamAvatarFrameProvider()
    with pytest.raises(AvatarFrameNotConfigured, match="avatar_frame.wavespeed_model"):
        provider.generate(tmp_path / "frame.png", tmp_path / "out.png", cfg)


def test_seedream_refuses_without_api_key(make_config, tmp_path, monkeypatch):
    monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
    cfg = load_config(make_config(
        avatar_frame={"provider": "wavespeed_seedream"},
        wavespeed={"enabled": True},
    ))
    provider = WaveSpeedSeedreamAvatarFrameProvider()
    with pytest.raises(AvatarFrameNotConfigured, match="WAVESPEED_API_KEY"):
        provider.generate(tmp_path / "frame.png", tmp_path / "out.png", cfg)


def test_seedream_payload_shape(make_config):
    cfg = load_config(make_config(
        avatar_frame={"provider": "wavespeed_seedream", "size": "1440x2560"},
    ))
    payload = WaveSpeedSeedreamAvatarFrameProvider.build_payload(
        "https://example.com/source.png",
        ["https://example.com/ref.png", "https://example.com/side.png"],
        cfg,
    )
    assert payload["images"] == [
        "https://example.com/source.png",
        "https://example.com/ref.png",
    ]
    assert payload["prompt"] == DEFAULT_SEEDREAM_PROMPT
    assert payload["size"] == "1440x2560"
    assert payload["enable_sync_mode"] is False
    assert payload["enable_base64_output"] is False


def test_default_seedream_prompt_is_two_image_replacement():
    assert DEFAULT_SEEDREAM_PROMPT == (
        "Replace the entire person from image 1 with the person from image 2, "
        "keep the same facial expression from image 1 and pose from image 1, "
        "keep the same outfit from image 1"
    )


def test_seedream_payload_uses_custom_prompt(make_config):
    cfg = load_config(make_config(
        avatar_frame={"provider": "wavespeed_seedream", "prompt": "swap identity only"},
    ))
    payload = WaveSpeedSeedreamAvatarFrameProvider.build_payload(
        "https://example.com/source.png",
        ["https://example.com/ref.png"],
        cfg,
    )
    assert payload["prompt"] == "swap identity only"
    assert payload["images"] == [
        "https://example.com/source.png",
        "https://example.com/ref.png",
    ]
    assert "size" not in payload


def test_seedream_custom_prompt_can_use_multiple_references(make_config):
    cfg = load_config(make_config(
        avatar_frame={"provider": "wavespeed_seedream", "prompt": "use every reference"},
    ))
    payload = WaveSpeedSeedreamAvatarFrameProvider.build_payload(
        "https://example.com/source.png",
        ["https://example.com/ref.png", "https://example.com/side.png"],
        cfg,
    )
    assert payload["images"] == [
        "https://example.com/source.png",
        "https://example.com/ref.png",
        "https://example.com/side.png",
    ]


def test_seedream_uses_configured_identity_references(make_config, tmp_path):
    refs = [str(tmp_path / "front.png"), str(tmp_path / "side.png")]
    cfg = load_config(make_config(avatar_frame={
        "provider": "wavespeed_seedream",
        "identity_references": refs,
    }))
    assert WaveSpeedSeedreamAvatarFrameProvider.identity_reference_paths(cfg) == [
        tmp_path / "front.png",
        tmp_path / "side.png",
    ]


def test_mock_avatar_frame_provider_selected_when_configured(make_config):
    cfg = load_config(make_config(avatar_frame={"provider": "mock"}))
    provider = get_avatar_frame_provider(cfg)
    assert isinstance(provider, MockAvatarFrameProvider)
    assert provider.name == "mock"


def test_mock_avatar_frame_provider_produces_watermarked_copy(make_config, tmp_path):
    from PIL import Image

    cfg = load_config(make_config(avatar_frame={"provider": "mock"}))
    provider = get_avatar_frame_provider(cfg)
    frame = tmp_path / "frame1.png"
    Image.new("RGB", (100, 100), color=(10, 20, 30)).save(frame)
    output = tmp_path / "avatar.png"

    result = provider.generate(frame, output, cfg, seed=1)
    assert result == output
    assert output.exists()
    # same dimensions as the source frame, just watermarked
    assert Image.open(output).size == (100, 100)
