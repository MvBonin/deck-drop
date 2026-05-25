"""Systemd user service management – install/enable/disable deckdrop.service."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Literal

InstallType = Literal["flatpak", "appimage", "pipx"]

_SERVICE_NAME = "deckdrop"
_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
_UNIT_FILE = _UNIT_DIR / "deckdrop.service"


def detect_install_type() -> InstallType:
    """Detect how DeckDrop is installed based on environment variables."""
    if os.getenv("FLATPAK_ID"):
        return "flatpak"
    if os.getenv("APPIMAGE"):
        return "appimage"
    return "pipx"


def _exec_start(install_type: InstallType, appimage_path: str = "") -> str:
    if install_type == "flatpak":
        return "/usr/bin/flatpak run com.deckdrop.DeckDrop --headless"
    if install_type == "appimage":
        path = appimage_path or os.getenv("APPIMAGE", "")
        if not path:
            raise ValueError("AppImage-Pfad unbekannt – Service kann nicht eingerichtet werden")
        return f"{path} --headless"
    return "%h/.local/bin/deckdrop --headless"


def _unit_content(exec_start: str) -> str:
    return (
        "[Unit]\n"
        "Description=DeckDrop LAN Game Sharing\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "Environment=PYTHONUNBUFFERED=1\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _systemctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True,
        text=True,
    )


def is_enabled() -> bool:
    return _systemctl("is-enabled", _SERVICE_NAME).returncode == 0


def is_active() -> bool:
    return _systemctl("is-active", _SERVICE_NAME).returncode == 0


def enable(install_type: InstallType, appimage_path: str = "") -> None:
    """Write the unit file and enable the service."""
    exec_start = _exec_start(install_type, appimage_path)
    _UNIT_DIR.mkdir(parents=True, exist_ok=True)
    _UNIT_FILE.write_text(_unit_content(exec_start))
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", _SERVICE_NAME)


def disable() -> None:
    """Disable and stop the service, remove the unit file."""
    _systemctl("disable", "--now", _SERVICE_NAME)
    if _UNIT_FILE.exists():
        _UNIT_FILE.unlink()
    _systemctl("daemon-reload")
