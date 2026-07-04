"""worker._notify_with_balance — appends WaveSpeed balance to the published/
flagged Telegram notice, best-effort."""
import logging

import worker
from lib.config import load_config

LOGGER = logging.getLogger("test")


def test_appends_balance_when_wavespeed_enabled(make_config, monkeypatch):
    cfg = load_config(make_config(
        telegram={"enabled": True, "chat_id": "123"},
        wavespeed={"enabled": True, "model": "some/model"},
    ))
    sent = []
    monkeypatch.setattr(worker, "send_message", lambda cfg, text, logger=None: sent.append(text))
    monkeypatch.setattr(worker, "get_balance", lambda cfg: 12.34)

    worker._notify_with_balance(cfg, "abc123: published to /out/abc123.mp4", LOGGER)

    assert len(sent) == 1
    assert "abc123: published to /out/abc123.mp4" in sent[0]
    assert "$12.34" in sent[0]
    assert "wavespeed.ai" in sent[0]


def test_falls_back_to_plain_text_when_balance_fetch_fails(make_config, monkeypatch):
    from lib.wavespeed_balance import WaveSpeedBalanceError

    cfg = load_config(make_config(
        telegram={"enabled": True, "chat_id": "123"},
        wavespeed={"enabled": True, "model": "some/model"},
    ))
    sent = []

    def fake_get_balance(cfg):
        raise WaveSpeedBalanceError("boom")

    monkeypatch.setattr(worker, "send_message", lambda cfg, text, logger=None: sent.append(text))
    monkeypatch.setattr(worker, "get_balance", fake_get_balance)

    worker._notify_with_balance(cfg, "abc123: published to /out/abc123.mp4", LOGGER)

    assert sent == ["abc123: published to /out/abc123.mp4"]  # unchanged, no crash


def test_skips_balance_when_wavespeed_disabled(make_config, monkeypatch):
    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "123"}))
    sent = []
    calls = []

    monkeypatch.setattr(worker, "send_message", lambda cfg, text, logger=None: sent.append(text))
    monkeypatch.setattr(worker, "get_balance", lambda cfg: calls.append(1) or 99.0)

    worker._notify_with_balance(cfg, "abc123: flagged", LOGGER)

    assert sent == ["abc123: flagged"]
    assert calls == []  # get_balance never called — wavespeed.enabled is False
