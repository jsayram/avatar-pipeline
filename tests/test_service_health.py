import subprocess

import pytest
import requests

from lib.config import load_config
from lib.service_health import gather_service_health, probe_http


class FakeResponse:
    ok = True
    status_code = 200


def test_probe_http_reports_down_on_request_error(monkeypatch):
    def fake_get(url, timeout):
        raise requests.RequestException("connection refused")

    monkeypatch.setattr("lib.service_health.requests.get", fake_get)

    result = probe_http("gate", "http://localhost:8189/health")

    assert result["status"] == "down"
    assert "connection refused" in result["detail"]


def test_gather_service_health_probes_http_and_launchd(monkeypatch, make_config):
    cfg = load_config(make_config(dashboard={"launchd_labels": {"n8n": "test.n8n"}}))
    requested_urls = []
    requested_labels = []

    def fake_get(url, timeout):
        requested_urls.append(url)
        return FakeResponse()

    def fake_run(cmd, capture_output, text, timeout, check):
        requested_labels.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("lib.service_health.requests.get", fake_get)
    monkeypatch.setattr("lib.service_health.subprocess.run", fake_run)

    result = gather_service_health(cfg)

    names = {service["name"] for service in result["services"]}
    assert {"n8n", "comfyui", "gate", "dashboard", "launchd:n8n"} <= names
    assert "http://localhost:8188/system_stats" in requested_urls
    assert "test.n8n" in requested_labels
