"""Install the running AppImage into the user's system (SteamOS / Linux).

"Installed" means:
- the AppImage lives at a stable path (~/Applications/DeckDrop.AppImage), so
  the background service, desktop entries and Steam shortcuts keep working
  when the downloaded file is moved or deleted, and self-updates replace it
  in place;
- two desktop entries: "DeckDrop" and "DeckDrop (Gaming Mode)" (--kiosk).
  Steam's "Add a Non-Steam Game" dialog lists both;
- optionally the systemd user service (deckdrop.core.service).

Nothing here needs root. Everything is undone by `uninstall()` except the
AppImage file itself (it may be the very file that is running).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "DeckDrop.AppImage"
DESKTOP_ID = "deckdrop.desktop"
KIOSK_DESKTOP_ID = "deckdrop-gaming-mode.desktop"
ICON_NAME = "deckdrop"


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def install_dir() -> Path:
    return home() / "Applications"


def install_path() -> Path:
    return install_dir() / APP_NAME


def applications_dir() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(home() / ".local" / "share")
    return Path(data) / "applications"


def icon_path() -> Path:
    data = os.environ.get("XDG_DATA_HOME") or str(home() / ".local" / "share")
    return Path(data) / "icons" / "hicolor" / "scalable" / "apps" / f"{ICON_NAME}.svg"


# What this process was really started from. $APPIMAGE is repointed at the
# installed copy after `install()` (so a restart execs that one), which must
# not make the status claim we already run from there.
_STARTED_FROM = os.environ.get("APPIMAGE", "")


def running_appimage() -> Path | None:
    p = _STARTED_FROM or os.environ.get("APPIMAGE")
    return Path(p) if p else None


@dataclass
class InstallStatus:
    # Path of the AppImage this process runs from ("" if not an AppImage)
    running_from: str
    # Stable install location and whether a file is there
    install_path: str
    installed: bool
    # The running AppImage *is* the installed one
    running_installed: bool
    desktop_entries: bool
    # An AppImage is running, so install() can do something
    can_install: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve() or os.path.samefile(a, b)
    except OSError:
        return False


def status() -> InstallStatus:
    running = running_appimage()
    target = install_path()
    installed = target.is_file()
    return InstallStatus(
        running_from=str(running or ""),
        install_path=str(target),
        installed=installed,
        running_installed=bool(running and installed and _same_file(running, target)),
        desktop_entries=(applications_dir() / DESKTOP_ID).is_file(),
        can_install=bool(running and running.is_file()),
    )


def _quote_exec(path: Path) -> str:
    """Desktop-entry Exec quoting (spec: double quotes, escape \\ " ` $)."""
    s = str(path)
    if not any(ch in s for ch in " \t\n\"'\\><~|&;$*?#()`"):
        return s
    for ch in ("\\", '"', "`", "$"):
        s = s.replace(ch, "\\" + ch)
    return f'"{s}"'


def desktop_entry(exec_path: Path, *, kiosk: bool) -> str:
    exe = _quote_exec(exec_path)
    if kiosk:
        name = "DeckDrop (Gaming Mode)"
        comment = "DeckDrop as a fullscreen app – add this one to Steam"
        exec_line = f"{exe} --kiosk"
    else:
        name = "DeckDrop"
        comment = "Share games over your LAN"
        exec_line = exe
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        f"Comment={comment}\n"
        f"Exec={exec_line}\n"
        f"Icon={ICON_NAME}\n"
        "Terminal=false\n"
        "Categories=Game;Network;FileTransfer;\n"
        "StartupWMClass=deckdrop\n"
    )


def _bundled_icon() -> Path | None:
    candidates = []
    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(Path(appdir) / "deckdrop.svg")
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(Path(meipass) / "deckdrop.svg")
    candidates.append(Path(__file__).resolve().parents[2] / "packaging" / "deckdrop.svg")
    return next((c for c in candidates if c.is_file()), None)


def _copy_atomic(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.installing")
    try:
        shutil.copyfile(src, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def write_desktop_entries(exec_path: Path) -> None:
    apps = applications_dir()
    apps.mkdir(parents=True, exist_ok=True)
    (apps / DESKTOP_ID).write_text(desktop_entry(exec_path, kiosk=False), encoding="utf-8")
    (apps / KIOSK_DESKTOP_ID).write_text(desktop_entry(exec_path, kiosk=True), encoding="utf-8")
    icon = _bundled_icon()
    if icon is not None:
        dest = icon_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(icon, dest)
        except OSError as exc:
            log.warning("Could not install icon: %s", exc)


def install(source: Path | None = None) -> Path:
    """Copy the AppImage to the stable path and write desktop entries.

    Returns the installed path. Raises ValueError if there's no AppImage to
    install (e.g. a pipx install).
    """
    src = source or running_appimage()
    if src is None or not src.is_file():
        raise ValueError("not running from an AppImage")
    target = install_path()
    if not (target.is_file() and _same_file(src, target)):
        _copy_atomic(src, target)
        log.info("AppImage installed: %s → %s", src, target)
    write_desktop_entries(target)
    return target


def uninstall() -> None:
    """Remove desktop entries and icon. The AppImage file stays (it may be running)."""
    for p in (applications_dir() / DESKTOP_ID, applications_dir() / KIOSK_DESKTOP_ID, icon_path()):
        try:
            p.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("Could not remove %s: %s", p, exc)
