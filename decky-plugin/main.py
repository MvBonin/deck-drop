"""Decky plugin backend for DeckDrop.

Communicates with the DeckDrop API server running on localhost:7373.
The DeckDrop server must be installed separately (via pipx, AppImage, or Flatpak).
"""

from __future__ import annotations

import subprocess
import urllib.error
import urllib.request

import decky_plugin  # type: ignore[import-not-found]

_DECKDROP_URL = "http://localhost:7373"
_SERVICE = "deckdrop"


def _api(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(
        f"{_DECKDROP_URL}/api{path}",
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            import json
            return json.loads(resp.read())
    except Exception as exc:
        decky_plugin.logger.warning("DeckDrop API error (%s %s): %s", method, path, exc)
        return {}


def _systemctl(*args: str) -> bool:
    result = subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


class Plugin:
    async def get_status(self) -> dict:
        """Return service and API status."""
        api_status = _api("/status")
        return {
            "service_enabled": _systemctl("is-enabled", _SERVICE),
            "service_active": _systemctl("is-active", _SERVICE),
            "api_reachable": bool(api_status),
            "version": api_status.get("version", ""),
        }

    async def enable_autostart(self) -> dict:
        """Enable DeckDrop autostart via its API (sets up the systemd service)."""
        result = _api("/service/enable", method="POST")
        return result

    async def disable_autostart(self) -> dict:
        """Disable DeckDrop autostart."""
        result = _api("/service/disable", method="POST")
        return result

    async def start_service(self) -> bool:
        return _systemctl("start", _SERVICE)

    async def stop_service(self) -> bool:
        return _systemctl("stop", _SERVICE)

    async def get_url(self) -> str:
        return _DECKDROP_URL

    async def _main(self) -> None:
        decky_plugin.logger.info("DeckDrop plugin loaded")

    async def _unload(self) -> None:
        decky_plugin.logger.info("DeckDrop plugin unloaded")
