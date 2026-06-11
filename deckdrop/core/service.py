"""Systemd user service management – install/enable/disable deckdrop.service."""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal

InstallType = Literal["flatpak", "appimage", "pipx"]

_SERVICE_NAME = "deckdrop"
_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
_UNIT_FILE = _UNIT_DIR / "deckdrop.service"


def _find_appimage() -> str:
    """Locate a DeckDrop AppImage under $HOME (same heuristic as service-setup.sh)."""
    home = Path.home()
    try:
        found = sorted(home.glob("DeckDrop-*.AppImage"), key=lambda p: p.name)
        if found:
            return str(found[-1])
        for path in home.rglob("DeckDrop-*.AppImage"):
            if path.is_file() and len(path.relative_to(home).parts) <= 4:
                return str(path)
    except OSError:
        pass
    return ""


def detect_install_type(appimage_path: str = "") -> InstallType:
    """Detect how DeckDrop is installed based on environment and config."""
    if os.getenv("FLATPAK_ID"):
        return "flatpak"
    if os.getenv("APPIMAGE"):
        return "appimage"
    if appimage_path and Path(appimage_path).is_file():
        return "appimage"
    if getattr(sys, "frozen", False):
        return "appimage"
    if _find_appimage():
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


def _unit_content(exec_start: str, *, appimage_path: str = "") -> str:
    env_lines = ["Environment=PYTHONUNBUFFERED=1"]
    if appimage_path:
        env_lines.append(f"Environment=APPIMAGE={appimage_path}")
    env_block = "\n".join(env_lines)
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
        f"{env_block}\n"
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


def _enable_linger() -> None:
    """Allow the user service to run after reboot without an active session."""
    try:
        user = os.getenv("USER") or getpass.getuser()
    except Exception:
        return
    if not user:
        return
    try:
        subprocess.run(
            ["loginctl", "enable-linger", user],
            capture_output=True,
            text=True,
        )
    except OSError:
        pass


def is_enabled() -> bool:
    return _systemctl("is-enabled", _SERVICE_NAME).returncode == 0


def is_active() -> bool:
    return _systemctl("is-active", _SERVICE_NAME).returncode == 0


def enable(install_type: InstallType, appimage_path: str = "") -> None:
    """Write the unit file and enable the service."""
    resolved_appimage = ""
    if install_type == "appimage":
        resolved_appimage = appimage_path or os.getenv("APPIMAGE", "") or _find_appimage()
    exec_start = _exec_start(install_type, resolved_appimage)
    _UNIT_DIR.mkdir(parents=True, exist_ok=True)
    _UNIT_FILE.write_text(
        _unit_content(exec_start, appimage_path=resolved_appimage),
    )
    _enable_linger()
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", _SERVICE_NAME)


def refresh_appimage_path(appimage_path: str) -> bool:
    """Point an already-installed AppImage service at the given AppImage.

    Used so launching a newer AppImage replaces the old path in the service.
    Only rewrites the unit file + daemon-reload (no restart); the new version
    is picked up on the next service start/reboot. No-op when the service is
    not installed, is not an AppImage install, or already points at this path.
    Returns True if the unit file was changed.
    """
    if not appimage_path or not _UNIT_FILE.exists():
        return False
    try:
        current = _UNIT_FILE.read_text()
    except OSError:
        return False
    # Don't clobber a flatpak/pipx install.
    if ".AppImage" not in current:
        return False
    new_content = _unit_content(
        _exec_start("appimage", appimage_path),
        appimage_path=appimage_path,
    )
    if new_content == current:
        return False
    _UNIT_FILE.write_text(new_content)
    _systemctl("daemon-reload")
    return True


def disable() -> None:
    """Disable and stop the service, remove the unit file."""
    _systemctl("disable", "--now", _SERVICE_NAME)
    if _UNIT_FILE.exists():
        _UNIT_FILE.unlink()
    _systemctl("daemon-reload")
