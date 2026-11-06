"""Fullscreen app window for Gaming Mode (`deckdrop --kiosk`).

SteamOS ships no Chromium; users usually install it from Discover, i.e. as a
Flatpak. Native binaries are tried first, then the common Flatpak browsers.

The window runs with its own browser profile so the started process *is* the
browser – with the user's normal profile Chromium would hand the URL to an
already running instance and exit at once, and Steam would think the "game"
was closed.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from deckdrop.core.hostenv import host_env

log = logging.getLogger(__name__)

NATIVE_BROWSERS = (
    "chromium",
    "chromium-browser",
    "google-chrome-stable",
    "google-chrome",
    "brave-browser",
    "microsoft-edge-stable",
)
FLATPAK_BROWSERS = (
    "org.chromium.Chromium",
    "com.google.Chrome",
    "com.brave.Browser",
    "com.microsoft.Edge",
    "io.github.ungoogled_software.ungoogled_chromium",
)


def _home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def _flatpak_installed(app_id: str, env: dict[str, str]) -> bool:
    if not shutil.which("flatpak", path=env.get("PATH")):
        return False
    try:
        r = subprocess.run(
            ["flatpak", "info", app_id],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def browser_command(url: str, env: dict[str, str] | None = None) -> list[str] | None:
    """Command line for a Chromium-style app window on `url`, or None."""
    env = env if env is not None else host_env()
    flags = [
        f"--app={url}",
        "--start-fullscreen",
        "--window-size=1280,800",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=TranslateUI",
        "--class=deckdrop",
    ]
    for name in NATIVE_BROWSERS:
        exe = shutil.which(name, path=env.get("PATH"))
        if exe:
            profile = _home() / ".local" / "share" / "deckdrop" / "kiosk-browser"
            return [exe, f"--user-data-dir={profile}", *flags]
    for app_id in FLATPAK_BROWSERS:
        if _flatpak_installed(app_id, env):
            # A Flatpak can always write below its own ~/.var/app directory.
            profile = _home() / ".var" / "app" / app_id / "data" / "deckdrop-kiosk"
            return ["flatpak", "run", app_id, f"--user-data-dir={profile}", *flags]
    return None


def open_window(url: str) -> subprocess.Popen | None:
    """Start the kiosk window; None if no suitable browser is installed."""
    env = host_env()
    cmd = browser_command(url, env)
    if cmd is None:
        return None
    log.info("Kiosk window: %s", " ".join(cmd[:4]))
    try:
        return subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        log.warning("Could not start kiosk browser %s: %s", cmd[0], exc)
        return None
