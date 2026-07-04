"""Query local Tailscale daemon for node and serve/funnel status.

Used by the dashboard to show the operator whether Tailscale is connected,
which ports are being served, and whether the dashboard itself (port 8190)
is publicly funneled.

Tailscale CLI references:
    tailscale status --json   → node info (hostname, online, tailnet)
    tailscale serve status --json → served/funneled ports
"""
from __future__ import annotations

import json
import subprocess


DASHBOARD_PORT = 8190


class TailscaleError(Exception):
    """Could not query the Tailscale daemon."""


# ---------------------------------------------------------------------------
# tailscale status --json
# ---------------------------------------------------------------------------

def get_tailscale_status() -> dict:
    """Return node-level Tailscale info.

    On success:
        {"available": True, "hostname": "...", "online": True,
         "tailnet": "..."}

    On any error (CLI missing, timeout, bad JSON):
        {"available": False, "detail": "..."}
    """
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {"available": False, "detail": "tailscale CLI not found"}
    except subprocess.TimeoutExpired:
        return {"available": False, "detail": "tailscale status timed out"}

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"available": False, "detail": f"invalid JSON: {exc}"}

    self_node = data.get("Self", {})
    return {
        "available": True,
        "hostname": self_node.get("HostName", ""),
        "online": self_node.get("Online", False),
        "tailnet": data.get("MagicDNSSuffix", ""),
    }


# ---------------------------------------------------------------------------
# tailscale serve status --json
# ---------------------------------------------------------------------------

def _extract_ports(serve_data: dict) -> tuple[list[int], list[int]]:
    """Parse the serve config to find served and funneled TCP ports."""
    served_ports: list[int] = []
    funneled_ports: list[int] = []

    # The top-level keys under the serve config are protocol+port strings
    # like "https://443" or "https+insecure://8190".
    tcp_map = serve_data.get("TCP", {})
    web_map = serve_data.get("Web", {})

    # Collect ports from the Web map. Keys can look like "https://443" or
    # "host.tailnet.ts.net:443" — take everything after the last colon and
    # strip any leftover URL slashes so both shapes parse.
    for endpoint in web_map:
        try:
            port = int(endpoint.rstrip("/").rsplit(":", 1)[-1].lstrip("/"))
            if port not in served_ports:
                served_ports.append(port)
        except (ValueError, IndexError):
            continue

    # Collect ports from the TCP map (keys like "443").
    for port_str in tcp_map:
        try:
            port = int(port_str)
            if port not in served_ports:
                served_ports.append(port)
        except (ValueError, IndexError):
            continue

    # AllowFunnel maps endpoint strings to booleans. Real CLI output keys
    # these as "host.tailnet.ts.net:443" (verified live 2026-07-03), not
    # bare port numbers — parse the same way as the Web map.
    allow_funnel = serve_data.get("AllowFunnel", {})
    for endpoint, enabled in allow_funnel.items():
        if enabled:
            try:
                port = int(str(endpoint).rstrip("/").rsplit(":", 1)[-1].lstrip("/"))
                if port not in funneled_ports:
                    funneled_ports.append(port)
            except (ValueError, IndexError):
                continue

    return sorted(served_ports), sorted(funneled_ports)


def get_serve_status() -> dict:
    """Return Tailscale Serve / Funnel info.

    On success:
        {"available": True, "served_ports": [...], "funneled_ports": [...],
         "dashboard_funneled": True/False}

    On any error:
        {"available": False, "detail": "..."}
    """
    try:
        result = subprocess.run(
            ["tailscale", "serve", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        return {"available": False, "detail": "tailscale CLI not found"}
    except subprocess.TimeoutExpired:
        return {"available": False, "detail": "tailscale serve status timed out"}

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        return {"available": False, "detail": f"invalid JSON: {exc}"}

    served_ports, funneled_ports = _extract_ports(data)
    return {
        "available": True,
        "served_ports": served_ports,
        "funneled_ports": funneled_ports,
        "dashboard_funneled": DASHBOARD_PORT in funneled_ports,
    }


# ---------------------------------------------------------------------------
# Combined status for the dashboard endpoint
# ---------------------------------------------------------------------------

def get_full_status() -> dict:
    """Return a combined dict suitable for a dashboard JSON endpoint."""
    return {
        "node": get_tailscale_status(),
        "serve": get_serve_status(),
    }
