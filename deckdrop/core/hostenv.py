"""Environment for launching host programs from a bundled (AppImage) build.

The PyInstaller bootloader points LD_LIBRARY_PATH at the bundled libraries
(libstdc++, libssl, libz, … from the build machine). Host programs started
with that environment – xdg-open, the browser, systemctl – load those
instead of the system's own and may fail to start, silently. Every
subprocess that runs a host binary should get `host_env()` instead.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _bundle_dirs() -> list[str]:
    dirs = [getattr(sys, "_MEIPASS", ""), os.environ.get("APPDIR", "")]
    return [os.path.realpath(d) for d in dirs if d]


def _strip_bundle(value: str, bundle_dirs: list[str]) -> str:
    keep = []
    for entry in value.split(os.pathsep):
        if not entry:
            continue
        real = os.path.realpath(entry)
        if any(real == d or real.startswith(d + os.sep) for d in bundle_dirs):
            continue
        keep.append(entry)
    return os.pathsep.join(keep)


def host_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """os.environ without the bundle's library and binary paths."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        # The bootloader saves the value it found under *_ORIG.
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        bundle_dirs = _bundle_dirs()
        ld = _strip_bundle(orig if orig is not None else "", bundle_dirs)
        if ld:
            env["LD_LIBRARY_PATH"] = ld
        else:
            env.pop("LD_LIBRARY_PATH", None)
        if "PATH" in env:
            env["PATH"] = _strip_bundle(env["PATH"], bundle_dirs)
    if extra:
        env.update(extra)
    return env


def open_url(url: str) -> None:
    """Open url in the user's default browser."""
    if getattr(sys, "frozen", False) and sys.platform.startswith("linux"):
        # webbrowser.open() would hand our LD_LIBRARY_PATH to the browser.
        try:
            subprocess.Popen(
                ["xdg-open", url],
                env=host_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except OSError:
            pass
    import webbrowser

    webbrowser.open(url)
