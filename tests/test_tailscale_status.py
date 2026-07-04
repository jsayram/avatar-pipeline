"""Tailscale status checks (mocked subprocess, no real CLI calls)."""
import json
import subprocess

import pytest

from lib.tailscale_status import (
    get_full_status,
    get_serve_status,
    get_tailscale_status,
)

# ---------------------------------------------------------------------------
# Realistic mock data matching `tailscale status --json` shape
# ---------------------------------------------------------------------------

MOCK_STATUS_JSON = json.dumps({
    "Version": "1.78.1",
    "Self": {
        "ID": "n1234567890abcdef",
        "HostName": "avatar-worker",
        "DNSName": "avatar-worker.tail1234.ts.net.",
        "OS": "linux",
        "Online": True,
        "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
    },
    "MagicDNSSuffix": "tail1234.ts.net",
    "Peer": {},
})

# ---------------------------------------------------------------------------
# Realistic mock data matching `tailscale serve status --json` shape
# ---------------------------------------------------------------------------

MOCK_SERVE_JSON_WITH_8190 = json.dumps({
    "TCP": {},
    "Web": {
        "https://443": {
            "Handlers": {"/": {"Proxy": "http://127.0.0.1:8190"}},
        },
    },
    "AllowFunnel": {
        "443": True,
    },
})

MOCK_SERVE_JSON_ONLY_5678 = json.dumps({
    "TCP": {"5678": {"HTTPS": False}},
    "Web": {
        "https://5678": {
            "Handlers": {"/": {"Proxy": "http://127.0.0.1:5678"}},
        },
    },
    "AllowFunnel": {
        "5678": True,
    },
})


def _fake_run(stdout, returncode=0):
    """Return a factory that produces a CompletedProcess with given stdout."""
    def inner(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["tailscale"], returncode=returncode, stdout=stdout, stderr=""
        )
    return inner


# ---------------------------------------------------------------------------
# get_tailscale_status
# ---------------------------------------------------------------------------

class TestGetTailscaleStatus:
    def test_success_extracts_hostname(self, monkeypatch):
        monkeypatch.setattr("lib.tailscale_status.subprocess.run",
                            _fake_run(MOCK_STATUS_JSON))
        result = get_tailscale_status()
        assert result["available"] is True
        assert result["hostname"] == "avatar-worker"
        assert result["online"] is True
        assert result["tailnet"] == "tail1234.ts.net"

    def test_cli_not_found_degrades(self, monkeypatch):
        def raise_fnf(*_a, **_k):
            raise FileNotFoundError("tailscale")
        monkeypatch.setattr("lib.tailscale_status.subprocess.run", raise_fnf)
        result = get_tailscale_status()
        assert result["available"] is False
        assert "not found" in result["detail"]

    def test_timeout_degrades(self, monkeypatch):
        def raise_timeout(*_a, **_k):
            raise subprocess.TimeoutExpired(cmd="tailscale", timeout=10)
        monkeypatch.setattr("lib.tailscale_status.subprocess.run",
                            raise_timeout)
        result = get_tailscale_status()
        assert result["available"] is False
        assert "timed out" in result["detail"]


# ---------------------------------------------------------------------------
# get_serve_status
# ---------------------------------------------------------------------------

class TestGetServeStatus:
    def test_success_parses_served_and_funneled(self, monkeypatch):
        monkeypatch.setattr("lib.tailscale_status.subprocess.run",
                            _fake_run(MOCK_SERVE_JSON_WITH_8190))
        result = get_serve_status()
        assert result["available"] is True
        assert 443 in result["served_ports"]
        assert 443 in result["funneled_ports"]

    def test_dashboard_funneled_true_when_8190_funneled(self, monkeypatch):
        serve_json = json.dumps({
            "TCP": {},
            "Web": {
                "https://8190": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8190"}},
                },
            },
            "AllowFunnel": {
                "8190": True,
            },
        })
        monkeypatch.setattr("lib.tailscale_status.subprocess.run",
                            _fake_run(serve_json))
        result = get_serve_status()
        assert result["dashboard_funneled"] is True

    def test_dashboard_funneled_false_when_only_5678(self, monkeypatch):
        monkeypatch.setattr("lib.tailscale_status.subprocess.run",
                            _fake_run(MOCK_SERVE_JSON_ONLY_5678))
        result = get_serve_status()
        assert result["dashboard_funneled"] is False
        assert 5678 in result["funneled_ports"]

    def test_cli_not_found_degrades(self, monkeypatch):
        def raise_fnf(*_a, **_k):
            raise FileNotFoundError("tailscale")
        monkeypatch.setattr("lib.tailscale_status.subprocess.run", raise_fnf)
        result = get_serve_status()
        assert result["available"] is False


# ---------------------------------------------------------------------------
# get_full_status
# ---------------------------------------------------------------------------

class TestGetFullStatus:
    def test_combines_both(self, monkeypatch):
        call_count = {"n": 0}

        def fake_run(args, **_kwargs):
            call_count["n"] += 1
            if args == ["tailscale", "status", "--json"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=0,
                    stdout=MOCK_STATUS_JSON, stderr="",
                )
            if args == ["tailscale", "serve", "status", "--json"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=0,
                    stdout=MOCK_SERVE_JSON_WITH_8190, stderr="",
                )
            raise AssertionError(f"unexpected command: {args}")

        monkeypatch.setattr("lib.tailscale_status.subprocess.run", fake_run)
        result = get_full_status()
        assert "node" in result
        assert "serve" in result
        assert result["node"]["available"] is True
        assert result["serve"]["available"] is True
        assert call_count["n"] == 2


class TestRealCliShapes:
    """Shapes captured from the real `tailscale serve status --json` output
    on the operator's machine (2026-07-03) — AllowFunnel and Web keys are
    host:port strings, not bare ports."""

    REAL_SERVE_JSON = json.dumps({
        "TCP": {"443": {"HTTPS": True}},
        "Web": {
            "jr-m1max.tail1234.ts.net:443": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:5678"}},
            },
        },
        "AllowFunnel": {"jr-m1max.tail1234.ts.net:443": True},
    })

    def test_hostport_allowfunnel_keys_parse(self, monkeypatch):
        monkeypatch.setattr("lib.tailscale_status.subprocess.run",
                            _fake_run(self.REAL_SERVE_JSON))
        result = get_serve_status()
        assert result["funneled_ports"] == [443]
        assert 443 in result["served_ports"]
        assert result["dashboard_funneled"] is False

    def test_dashboard_funneled_detected_with_hostport_key(self, monkeypatch):
        serve_json = json.dumps({
            "TCP": {"8190": {"HTTPS": True}},
            "Web": {
                "jr-m1max.tail1234.ts.net:8190": {
                    "Handlers": {"/": {"Proxy": "http://127.0.0.1:8190"}},
                },
            },
            "AllowFunnel": {"jr-m1max.tail1234.ts.net:8190": True},
        })
        monkeypatch.setattr("lib.tailscale_status.subprocess.run",
                            _fake_run(serve_json))
        result = get_serve_status()
        assert result["dashboard_funneled"] is True
