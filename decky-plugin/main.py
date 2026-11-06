"""Decky plugin backend for DeckDrop.

Talks to the DeckDrop API on localhost:7373 when it runs. Setup that has to
work while DeckDrop is *not* running (installing the AppImage, turning the
background service on, starting it) runs the AppImage's own CLI
(`--install`, `--service on|off|status`), so the logic lives in one place.

Decky starts this backend outside the user's login session: the session
variables `systemctl --user` needs are derived here (see `_user_env`).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

try:
    import decky

    logger = decky.logger
except ImportError:  # running outside Decky (tests, manual runs)
    import logging

    decky = None
    logger = logging.getLogger("deckdrop-plugin")

_PORT = 7373
_API_BASE = f"http://127.0.0.1:{_PORT}/api"
_DECKDROP_URL = f"http://127.0.0.1:{_PORT}"
_SERVICE = "deckdrop"
_FLATPAK_ID = "com.deckdrop.DeckDrop"
_BROWSER_FLATPAKS = (
    "org.chromium.Chromium",
    "com.google.Chrome",
    "com.brave.Browser",
    "com.microsoft.Edge",
    "io.github.ungoogled_software.ungoogled_chromium",
)
_NATIVE_BROWSERS = ("chromium", "chromium-browser", "google-chrome-stable", "brave-browser")


# -- environment --------------------------------------------------------------


def _home() -> Path:
    if decky is not None and getattr(decky, "DECKY_USER_HOME", ""):
        return Path(decky.DECKY_USER_HOME)
    return Path(os.environ.get("HOME") or Path.home())


def _user_env() -> dict[str, str]:
    """Environment for programs that must act as the desktop user."""
    env = dict(os.environ)
    # Decky Loader is a PyInstaller binary: its bundled libraries must not
    # leak into the AppImage / systemctl / flatpak.
    for key in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    env["HOME"] = str(_home())
    if decky is not None and getattr(decky, "DECKY_USER", ""):
        env.setdefault("USER", decky.DECKY_USER)
    uid = os.getuid()
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = runtime
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


def _run(cmd: list[str], timeout: float = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=_user_env(), timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("%s failed: %s", cmd[0], exc)
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def _systemctl(*args: str) -> bool:
    return _run(["systemctl", "--user", *args]).returncode == 0


# -- settings -----------------------------------------------------------------


def _settings_file() -> Path:
    base = getattr(decky, "DECKY_PLUGIN_SETTINGS_DIR", "") if decky is not None else ""
    return Path(base or _home() / ".config" / "deckdrop-decky") / "settings.json"


def _load_settings() -> dict:
    try:
        return json.loads(_settings_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_settings(data: dict) -> None:
    path = _settings_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not save plugin settings: %s", exc)


# -- DeckDrop API -------------------------------------------------------------


def _http(path: str, method: str = "GET", timeout: float = 3) -> dict | list | None:
    req = urllib.request.Request(
        f"{_API_BASE}{path}",
        method=method,
        headers={"Content-Type": "application/json"},
        data=b"" if method == "POST" else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        logger.warning("DeckDrop API %s %s: HTTP %s", method, path, exc.code)
        return None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("DeckDrop API %s %s: %s", method, path, exc)
        return None


# -- finding the AppImage -----------------------------------------------------


def _unit_file() -> Path:
    return _home() / ".config" / "systemd" / "user" / "deckdrop.service"


def _appimage_from_unit() -> Path | None:
    try:
        text = _unit_file().read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^Environment="?APPIMAGE=([^"\n]+)"?$', text, re.M)
    if m and Path(m.group(1)).is_file():
        return Path(m.group(1))
    return None


def _installed_appimage() -> Path:
    return _home() / "Applications" / "DeckDrop.AppImage"


def _find_appimage() -> Path | None:
    """The AppImage to use: installed copy > service target > newest download."""
    installed = _installed_appimage()
    if installed.is_file():
        return installed
    unit = _appimage_from_unit()
    if unit is not None:
        return unit
    home = _home()
    found: list[Path] = []
    for pattern in ("DeckDrop*.AppImage", "*/DeckDrop*.AppImage", "*/*/DeckDrop*.AppImage"):
        try:
            found.extend(p for p in home.glob(pattern) if p.is_file())
        except OSError:
            pass
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


def _flatpak_has(app_id: str) -> bool:
    if not shutil.which("flatpak"):
        return False
    return _run(["flatpak", "info", app_id], timeout=10).returncode == 0


def _browser_available() -> bool:
    if any(shutil.which(b) for b in _NATIVE_BROWSERS):
        return True
    return any(_flatpak_has(app_id) for app_id in _BROWSER_FLATPAKS)


def _cli(*args: str, timeout: float = 120) -> dict:
    """Run the AppImage's setup CLI; returns its JSON status line (or an error)."""
    appimage = _find_appimage()
    if appimage is None:
        return {"error": "no_appimage"}
    r = _run([str(appimage), *args], timeout=timeout)
    for line in reversed((r.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                if r.returncode != 0:
                    data["error"] = (r.stdout or r.stderr or "").strip().splitlines()[0]
                return data
            except ValueError:
                break
    detail = (r.stderr or r.stdout or "").strip().splitlines()
    return {"error": detail[-1] if detail else f"exit {r.returncode}"}


# -- plugin -------------------------------------------------------------------


class Plugin:
    async def get_status(self) -> dict:
        """Everything the panel shows, in one call."""
        api_info = _http("/status") or {}
        api_reachable = bool(api_info)
        service = (_http("/service") if api_reachable else None) or {}
        appimage = _find_appimage()
        unit_exists = _unit_file().is_file()

        downloads: list[dict] = []
        peers_online = 0
        update_count = 0
        if api_reachable:
            for d in _http("/downloads") or []:
                if not isinstance(d, dict):
                    continue
                downloads.append(
                    {
                        "id": d.get("id"),
                        "name": d.get("game_name") or "?",
                        "status": d.get("status") or "",
                        "progress": float(d.get("progress") or 0.0),
                        "speed": int(d.get("speed_bytes_sec") or 0),
                        "kind": d.get("kind") or "new",
                        "source_state": d.get("source_state") or "",
                    }
                )
            peers = _http("/peers") or []
            peers_online = sum(1 for p in peers if isinstance(p, dict) and p.get("online", True))
            games = _http("/games") or []
            update_count = sum(
                1 for g in games if isinstance(g, dict) and g.get("update_available")
            )

        settings = _load_settings()
        return {
            "installed": bool(appimage)
            or unit_exists
            or api_reachable
            or shutil.which("deckdrop") is not None,
            "appimage": str(appimage or ""),
            "appimage_installed": _installed_appimage().is_file(),
            "service_installed": unit_exists,
            "service_enabled": bool(service.get("enabled"))
            if service
            else _systemctl("is-enabled", _SERVICE),
            "service_active": _systemctl("is-active", _SERVICE),
            "api_reachable": api_reachable,
            "version": api_info.get("version", ""),
            "language": api_info.get("language", ""),
            "url": _DECKDROP_URL,
            "downloads": downloads,
            "peers_online": peers_online,
            "update_count": update_count,
            "shortcut_app_id": int(settings.get("shortcut_app_id") or 0),
        }

    async def browser_available(self) -> bool:
        """Slow-ish (flatpak info), so not part of the 10 s status poll."""
        return _browser_available()

    async def install_app(self) -> dict:
        """Copy the AppImage to ~/Applications and add menu entries."""
        result = _cli("--install")
        logger.info("install_app → %s", result)
        return result

    async def set_autostart(self, enabled: bool) -> dict:
        """Background service on/off. Through the API if DeckDrop runs (it
        knows its own AppImage best), else through the AppImage's CLI."""
        path = "/service/enable" if enabled else "/service/disable"
        result = _http(path, method="POST", timeout=30)
        if result is None:
            result = _cli("--service", "on" if enabled else "off")
        logger.info("set_autostart(%s) → %s", enabled, result)
        return result

    async def start_service(self) -> dict:
        if not _unit_file().is_file():
            # No unit yet: create it (also enables autostart – that's what
            # "run in the background" means on the Deck).
            result = _cli("--service", "on")
            return {"ok": not result.get("error"), **result}
        ok = _systemctl("start", _SERVICE)
        logger.info("start_service → %s", ok)
        return {"ok": ok}

    async def stop_service(self) -> dict:
        ok = _systemctl("stop", _SERVICE)
        if not ok and _http("/status"):
            # DeckDrop runs, but not as the service (started by hand / from
            # Steam): ask it to quit.
            ok = _http("/shutdown", method="POST") is not None
        logger.info("stop_service → %s", ok)
        return {"ok": ok}

    async def get_launch_target(self) -> dict:
        """What a Steam shortcut ("DeckDrop" in the library) should start."""
        appimage = _find_appimage()
        if appimage is None:
            return {"error": "no_appimage"}
        return {
            "exe": f'"{appimage}"',
            "dir": f'"{appimage.parent}"',
            "options": "--kiosk",
        }

    async def set_shortcut_app_id(self, app_id: int) -> None:
        settings = _load_settings()
        settings["shortcut_app_id"] = int(app_id or 0)
        _save_settings(settings)

    async def get_url(self) -> str:
        return _DECKDROP_URL

    async def get_update_count(self) -> int:
        """Kept for older frontends; get_status() carries it now."""
        games = _http("/games")
        if not isinstance(games, list):
            return 0
        return sum(1 for g in games if isinstance(g, dict) and g.get("update_available"))

    async def _main(self) -> None:
        logger.info("DeckDrop plugin loaded")

    async def _unload(self) -> None:
        logger.info("DeckDrop plugin unloaded")
