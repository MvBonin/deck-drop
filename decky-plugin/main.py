"""Decky plugin backend for DeckDrop.

Communicates with the DeckDrop API server on localhost:7373.
DeckDrop must be installed separately (pipx, AppImage, or Flatpak).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.error
import urllib.request

try:
    import decky

    logger = decky.logger
except ImportError:
    import logging

    logger = logging.getLogger("deckdrop-plugin")

_API_BASE = "http://localhost:7373/api"
_SERVICE = "deckdrop"
_DECKDROP_URL = "http://localhost:7373"


def _http(path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(
        f"{_API_BASE}{path}",
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("DeckDrop API %s %s: %s", method, path, exc)
        return {}


def _systemctl(*args: str) -> bool:
    return (
        subprocess.run(["systemctl", "--user", *args], capture_output=True).returncode == 0
    )


def _is_installed() -> bool:
    """Check whether deckdrop is installed (pipx/native or Flatpak)."""
    if shutil.which("deckdrop"):
        return True
    result = subprocess.run(
        ["flatpak", "list", "--app", "--columns=application"],
        capture_output=True,
        text=True,
    )
    return "com.deckdrop.DeckDrop" in result.stdout


class Plugin:
    async def get_status(self) -> dict:
        """Return install state, service state, and API reachability."""
        api_info = _http("/status")
        service_info = _http("/service")
        return {
            "installed": _is_installed(),
            "service_enabled": service_info.get("enabled", _systemctl("is-enabled", _SERVICE)),
            "service_active": _systemctl("is-active", _SERVICE),
            "api_reachable": bool(api_info),
            "version": api_info.get("version", ""),
            "url": _DECKDROP_URL,
        }

    async def enable_autostart(self) -> dict:
        """Enable the DeckDrop systemd user service via its API."""
        result = _http("/service/enable", method="POST")
        if not result:
            return {"error": "DeckDrop API nicht erreichbar – starte den Service zuerst manuell"}
        return result

    async def disable_autostart(self) -> dict:
        """Disable the DeckDrop systemd user service."""
        result = _http("/service/disable", method="POST")
        if not result:
            return {"error": "DeckDrop API nicht erreichbar"}
        return result

    async def start_service(self) -> bool:
        ok = _systemctl("start", _SERVICE)
        logger.info("start_service → %s", ok)
        return ok

    async def stop_service(self) -> bool:
        ok = _systemctl("stop", _SERVICE)
        logger.info("stop_service → %s", ok)
        return ok

    async def get_url(self) -> str:
        return _DECKDROP_URL

    async def _main(self) -> None:
        logger.info("DeckDrop plugin loaded")

    async def _unload(self) -> None:
        logger.info("DeckDrop plugin unloaded")
