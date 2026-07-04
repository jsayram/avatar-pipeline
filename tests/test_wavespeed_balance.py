"""WaveSpeed balance check (mocked network, no real API call)."""
import pytest

from lib.config import load_config
from lib.wavespeed_balance import WaveSpeedBalanceError, get_balance


def test_get_balance_requires_api_key_env(make_config, monkeypatch):
    monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
    cfg = load_config(make_config())
    with pytest.raises(WaveSpeedBalanceError, match="WAVESPEED_API_KEY"):
        get_balance(cfg)


def test_get_balance_success(make_config, monkeypatch):
    monkeypatch.setenv("WAVESPEED_API_KEY", "faketoken")
    cfg = load_config(make_config())

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"code": 200, "message": "success", "data": {"balance": 42.5}}

    calls = []

    def fake_get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers})
        return FakeResponse()

    monkeypatch.setattr("lib.wavespeed_balance.requests.get", fake_get)
    balance = get_balance(cfg)
    assert balance == 42.5
    assert calls[0]["url"] == "https://api.wavespeed.ai/api/v3/balance"
    assert calls[0]["headers"]["Authorization"] == "Bearer faketoken"


def test_get_balance_http_error_raises(make_config, monkeypatch):
    monkeypatch.setenv("WAVESPEED_API_KEY", "faketoken")
    cfg = load_config(make_config())

    class FakeResponse:
        status_code = 401
        text = "Unauthorized"

    monkeypatch.setattr("lib.wavespeed_balance.requests.get",
                        lambda *a, **k: FakeResponse())
    with pytest.raises(WaveSpeedBalanceError, match="401"):
        get_balance(cfg)


def test_get_balance_unexpected_shape_raises(make_config, monkeypatch):
    monkeypatch.setenv("WAVESPEED_API_KEY", "faketoken")
    cfg = load_config(make_config())

    class FakeResponse:
        status_code = 200
        text = "{}"
        def json(self):
            return {"code": 200, "message": "success"}  # missing data.balance

    monkeypatch.setattr("lib.wavespeed_balance.requests.get",
                        lambda *a, **k: FakeResponse())
    with pytest.raises(WaveSpeedBalanceError, match="unexpected"):
        get_balance(cfg)
