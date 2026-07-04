"""RunPod pods listing (mocked network, no real API call)."""
import pytest
import requests

from lib.runpod_pods import RunPodError, get_pods


# ---------------------------------------------------------------------------
# 1. Successful pod list parsing
# ---------------------------------------------------------------------------
def test_get_pods_success(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "rp_faketoken")

    class FakeResponse:
        status_code = 200

        def json(self):
            return [
                {
                    "id": "pod-abc123",
                    "name": "avatar-worker-1",
                    "desiredStatus": "RUNNING",
                    "machine": {"gpuDisplayName": "RTX 4090"},
                    "costPerHr": 0.44,
                },
                {
                    "id": "pod-def456",
                    "name": "comfy-server",
                    "desiredStatus": "EXITED",
                    "machine": {"gpuDisplayName": "RTX A5000"},
                    "costPerHr": 0.29,
                },
            ]

    calls: list[dict] = []

    def fake_get(url, headers=None, timeout=None):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        return FakeResponse()

    monkeypatch.setattr("lib.runpod_pods.requests.get", fake_get)
    result = get_pods()

    assert result["configured"] is True
    assert len(result["pods"]) == 2

    pod0 = result["pods"][0]
    assert pod0["id"] == "pod-abc123"
    assert pod0["name"] == "avatar-worker-1"
    assert pod0["status"] == "RUNNING"
    assert pod0["gpu_type"] == "RTX 4090"
    assert pod0["cost_per_hr"] == 0.44

    # Verify correct URL and auth header
    assert calls[0]["url"] == "https://rest.runpod.io/v1/pods"
    assert calls[0]["headers"]["Authorization"] == "Bearer rp_faketoken"
    assert calls[0]["timeout"] == 10


# ---------------------------------------------------------------------------
# 2. Missing API key returns configured=False
# ---------------------------------------------------------------------------
def test_get_pods_missing_key_returns_unconfigured(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    result = get_pods()
    assert result["configured"] is False
    assert "RUNPOD_API_KEY" in result["detail"]


def test_get_pods_explicit_none_key(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    result = get_pods(api_key=None)
    assert result["configured"] is False


def test_get_pods_empty_string_key(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    result = get_pods(api_key="")
    assert result["configured"] is False


# ---------------------------------------------------------------------------
# 3. HTTP error raises RunPodError
# ---------------------------------------------------------------------------
def test_get_pods_http_401_raises(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "rp_bad")

    class FakeResponse:
        status_code = 401
        text = "Unauthorized"

    monkeypatch.setattr("lib.runpod_pods.requests.get",
                        lambda *a, **k: FakeResponse())
    with pytest.raises(RunPodError, match="401"):
        get_pods()


def test_get_pods_http_500_raises(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "rp_key")

    class FakeResponse:
        status_code = 500
        text = "Internal Server Error"

    monkeypatch.setattr("lib.runpod_pods.requests.get",
                        lambda *a, **k: FakeResponse())
    with pytest.raises(RunPodError, match="500"):
        get_pods()


# ---------------------------------------------------------------------------
# 4. Empty pod list returns valid structure
# ---------------------------------------------------------------------------
def test_get_pods_empty_list(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "rp_key")

    class FakeResponse:
        status_code = 200

        def json(self):
            return []

    monkeypatch.setattr("lib.runpod_pods.requests.get",
                        lambda *a, **k: FakeResponse())
    result = get_pods()
    assert result["configured"] is True
    assert result["pods"] == []


# ---------------------------------------------------------------------------
# 5. Timeout / network error handling
# ---------------------------------------------------------------------------
def test_get_pods_timeout_raises(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "rp_key")

    def fake_get(*args, **kwargs):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr("lib.runpod_pods.requests.get", fake_get)
    with pytest.raises(RunPodError, match="timed out"):
        get_pods()


def test_get_pods_connection_error_raises(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "rp_key")

    def fake_get(*args, **kwargs):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr("lib.runpod_pods.requests.get", fake_get)
    with pytest.raises(RunPodError, match="connection refused"):
        get_pods()


# ---------------------------------------------------------------------------
# 6. Malformed JSON response
# ---------------------------------------------------------------------------
def test_get_pods_malformed_json_raises(monkeypatch):
    monkeypatch.setenv("RUNPOD_API_KEY", "rp_key")

    class FakeResponse:
        status_code = 200
        text = "not json"

        def json(self):
            raise ValueError("No JSON object could be decoded")

    monkeypatch.setattr("lib.runpod_pods.requests.get",
                        lambda *a, **k: FakeResponse())
    with pytest.raises(RunPodError, match="unexpected"):
        get_pods()


# ---------------------------------------------------------------------------
# 7. Explicit api_key parameter bypasses env
# ---------------------------------------------------------------------------
def test_get_pods_explicit_key(monkeypatch):
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)

    class FakeResponse:
        status_code = 200

        def json(self):
            return []

    calls: list[dict] = []

    def fake_get(url, headers=None, timeout=None):
        calls.append({"headers": headers})
        return FakeResponse()

    monkeypatch.setattr("lib.runpod_pods.requests.get", fake_get)
    result = get_pods(api_key="rp_direct")
    assert result["configured"] is True
    assert calls[0]["headers"]["Authorization"] == "Bearer rp_direct"
