"""Telegram approval-notification sending (mocked network, no real bot)."""
import pytest

from conftest import REPO_DIR
from lib.config import load_config
from lib.telegram_notify import TelegramNotConfigured, send_message, send_photo, send_video


def test_send_message_requires_enabled(make_config, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    cfg = load_config(make_config(telegram={"enabled": False, "chat_id": "123"}))
    with pytest.raises(TelegramNotConfigured, match="enabled"):
        send_message(cfg, "hello")


def test_send_message_requires_chat_id(make_config, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": ""}))
    with pytest.raises(TelegramNotConfigured, match="chat_id"):
        send_message(cfg, "hello")


def test_send_message_requires_bot_token_env(make_config, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "123"}))
    with pytest.raises(TelegramNotConfigured, match="TELEGRAM_BOT_TOKEN"):
        send_message(cfg, "hello")


def test_send_message_success(make_config, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "999"}))

    calls = []

    class FakeResponse:
        status_code = 200
        text = "{}"

    def fake_post(url, data=None, files=None, timeout=None):
        calls.append({"url": url, "data": data})
        return FakeResponse()

    monkeypatch.setattr("lib.telegram_notify.requests.post", fake_post)
    send_message(cfg, "hello world")
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/botfaketoken/sendMessage"
    assert calls[0]["data"]["chat_id"] == "999"
    assert calls[0]["data"]["text"] == "hello world"


def test_send_message_http_error_raises(make_config, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "999"}))

    class FakeResponse:
        status_code = 403
        text = "Forbidden: bot was blocked by the user"

    monkeypatch.setattr("lib.telegram_notify.requests.post",
                        lambda *a, **k: FakeResponse())
    from lib.telegram_notify import TelegramError
    with pytest.raises(TelegramError, match="403"):
        send_message(cfg, "hello")


def test_send_photo_missing_file_raises(make_config, monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "999"}))
    from lib.telegram_notify import TelegramError
    with pytest.raises(TelegramError, match="not found"):
        send_photo(cfg, tmp_path / "nope.png", "caption")


def test_send_photo_success(make_config, monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "999"}))
    image = tmp_path / "still.png"
    image.write_bytes(b"\x89PNG fake")

    calls = []

    class FakeResponse:
        status_code = 200
        text = "{}"

    def fake_post(url, data=None, files=None, timeout=None):
        calls.append({"url": url, "data": data, "files": files})
        return FakeResponse()

    monkeypatch.setattr("lib.telegram_notify.requests.post", fake_post)
    send_photo(cfg, image, "approve me")
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/botfaketoken/sendPhoto"
    assert calls[0]["data"]["caption"] == "approve me"
    assert "photo" in calls[0]["files"]


def test_send_video_missing_file_raises(make_config, monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "999"}))
    from lib.telegram_notify import TelegramError
    with pytest.raises(TelegramError, match="not found"):
        send_video(cfg, tmp_path / "nope.mp4", "caption")


def test_send_video_success(make_config, monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "999"}))
    video = tmp_path / "final.mp4"
    video.write_bytes(b"fake mp4 bytes")

    calls = []

    class FakeResponse:
        status_code = 200
        text = "{}"

    def fake_post(url, data=None, files=None, timeout=None):
        calls.append({"url": url, "data": data, "files": files})
        return FakeResponse()

    monkeypatch.setattr("lib.telegram_notify.requests.post", fake_post)
    send_video(cfg, video, "published video")
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/botfaketoken/sendVideo"
    assert calls[0]["data"]["caption"] == "published video"
    assert "video" in calls[0]["files"]


def test_send_video_http_error_raises(make_config, monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    cfg = load_config(make_config(telegram={"enabled": True, "chat_id": "999"}))
    video = tmp_path / "final.mp4"
    video.write_bytes(b"fake mp4 bytes")

    class FakeResponse:
        status_code = 413
        text = "Request Entity Too Large"

    monkeypatch.setattr("lib.telegram_notify.requests.post",
                        lambda *a, **k: FakeResponse())
    from lib.telegram_notify import TelegramError
    with pytest.raises(TelegramError, match="413"):
        send_video(cfg, video, "too big")
