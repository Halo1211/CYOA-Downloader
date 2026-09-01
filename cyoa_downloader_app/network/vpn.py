"""System VPN route awareness and an optional fail-closed download guard.

The application does not create, disconnect, or reconfigure VPN tunnels. HTTP
libraries automatically follow the operating system route table; this module
only verifies that a named or recognisable VPN interface is active when the
user selects the ``require`` policy.
"""

from __future__ import annotations

import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
from typing import Dict, List, Tuple

from ..logging_setup import logger
from ..runtime import state
from ..runtime.compat import mirror_to_legacy


_VPN_INTERFACE_TOKENS = (
    "vpn", "wireguard", "wintun", "openvpn", "nordlynx", "mullvad",
    "proton", "tailscale", "zerotier", "surfshark", "expressvpn",
    "globalprotect", "forticlient", "anyconnect",
)
_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: Tuple[float, List[Dict[str, object]]] = (0.0, [])


def _looks_like_vpn_interface(name: str) -> bool:
    lowered = str(name or "").strip().lower()
    if any(token in lowered for token in _VPN_INTERFACE_TOKENS):
        return True
    # Short tunnel names must be token/prefix matches. Substring matching
    # ``tun``/``tap``/``wg`` produced false positives in ordinary adapter
    # descriptions.
    return bool(re.search(r"(?:^|[\s_-])(tun\d*|tap\d*|wg\d*)(?:$|[\s_-])", lowered))


def _windows_active_interfaces() -> List[Dict[str, object]]:
    # The .NET API is substantially faster than importing the NetAdapter
    # PowerShell module in a fresh process (which can exceed the timeout on a
    # normal Windows laptop). It also works without administrator privileges.
    command = (
        "[System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | "
        "Where-Object {$_.OperationalStatus -eq 'Up'} | "
        "Select-Object @{Name='Name';Expression={$_.Name}},"
        "@{Name='InterfaceDescription';Expression={$_.Description}},"
        "@{Name='Status';Expression={$_.OperationalStatus.ToString()}} | "
        "ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=4,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    payload = json.loads(completed.stdout)
    rows = payload if isinstance(payload, list) else [payload]
    return [
        {
            "name": str(row.get("Name") or ""),
            "description": str(row.get("InterfaceDescription") or ""),
            "up": str(row.get("Status") or "").lower() == "up",
        }
        for row in rows if isinstance(row, dict)
    ]


def _posix_active_interfaces() -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    for _index, name in socket.if_nameindex():
        up = True
        operstate = f"/sys/class/net/{name}/operstate"
        if os.path.isfile(operstate):
            try:
                with open(operstate, encoding="ascii", errors="ignore") as handle:
                    up = handle.read().strip().lower() in {"up", "unknown"}
            except OSError:
                pass
        rows.append({"name": name, "description": name, "up": up})
    return rows


def list_active_network_interfaces(*, refresh: bool = False) -> List[Dict[str, object]]:
    """Return active interfaces, cached briefly to keep the request path cheap."""
    global _STATUS_CACHE
    now = time.monotonic()
    with _STATUS_LOCK:
        if not refresh and _STATUS_CACHE[0] and now - _STATUS_CACHE[0] < 15:
            return [dict(item) for item in _STATUS_CACHE[1]]
        try:
            if platform.system().lower() == "windows":
                rows = _windows_active_interfaces()
            else:
                rows = _posix_active_interfaces()
        except Exception as exc:
            logger.debug("VPN interface discovery failed: %s", exc)
            rows = []
        if not rows:
            # Last-resort names do not prove link state. Keep them visible for
            # diagnosis but mark them down so the fail-closed policy cannot be
            # bypassed when OS interface discovery itself failed.
            try:
                rows = [
                    {"name": name, "description": name, "up": False}
                    for _index, name in socket.if_nameindex()
                ]
            except OSError:
                rows = []
        _STATUS_CACHE = (now, rows)
        return [dict(item) for item in rows]


def get_vpn_status(*, refresh: bool = False) -> Dict[str, object]:
    interfaces = list_active_network_interfaces(refresh=refresh)
    requested = str(state._vpn_interface or "").strip().lower()
    matches = []
    for item in interfaces:
        if not item.get("up"):
            continue
        searchable = f"{item.get('name', '')} {item.get('description', '')}".lower()
        if (requested and requested in searchable) or (
            not requested and _looks_like_vpn_interface(searchable)
        ):
            matches.append(item)
    return {
        "policy": state._vpn_policy,
        "requested_interface": state._vpn_interface,
        "available": bool(matches),
        "matches": matches,
        "interfaces": interfaces,
    }


def vpn_requirement_satisfied() -> bool:
    return state._vpn_policy != "require" or bool(get_vpn_status().get("available"))


def _set_vpn_config(policy: str = "system", interface: str = "") -> None:
    normalized = str(policy or "system").strip().lower().replace("-", "_")
    if normalized not in {"system", "require"}:
        raise ValueError("VPN policy must be 'system' or 'require'")
    normalized_interface = str(interface or "").strip()
    changed = (
        normalized != state._vpn_policy
        or normalized_interface != state._vpn_interface
    )
    state._vpn_policy = normalized
    state._vpn_interface = normalized_interface
    mirror_to_legacy("_vpn_policy", state._vpn_policy)
    mirror_to_legacy("_vpn_interface", state._vpn_interface)
    if not changed:
        return
    if normalized == "require":
        logger.info(
            "VPN guard enabled%s",
            f" for interface matching '{state._vpn_interface}'" if state._vpn_interface else "",
        )
    else:
        logger.debug("VPN guard disabled; downloader uses the operating-system route table")


__all__ = [
    "_set_vpn_config", "get_vpn_status", "vpn_requirement_satisfied",
    "list_active_network_interfaces",
]
