"""Ensure only one DeckDrop server runs per config directory."""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

_GRACE_SEC = 3.0
_KILL_WAIT_SEC = 1.0


def pid_file_path(config_path: Path) -> Path:
    return config_path.parent / "deckdrop.pid"


def _read_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except (FileNotFoundError, PermissionError, ProcessLookupError):
        return ""
    return raw.replace(b"\0", b" ").decode("utf-8", errors="replace")


def is_deckdrop_process(pid: int, *, skip_pid: int | None = None) -> bool:
    """True if pid looks like a DeckDrop server (not this process)."""
    if pid <= 1 or pid == skip_pid:
        return False
    cmd = _read_cmdline(pid).lower()
    if not cmd:
        return False
    return "deckdrop" in cmd or "deck-drop" in cmd


def pids_listening_on_port(port: int) -> list[int]:
    """Return PIDs bound to TCP *:port (Linux, via ss)."""
    try:
        result = subprocess.run(
            ["ss", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    pids = [int(m.group(1)) for m in re.finditer(r"pid=(\d+)", result.stdout)]
    return list(dict.fromkeys(pids))


def _terminate(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        log.warning("No permission to stop process %s", pid)


def _wait_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True
        time.sleep(0.1)
    return False


def kill_process(pid: int) -> None:
    """SIGTERM, then SIGKILL if the process is still alive."""
    _terminate(pid)
    if _wait_exit(pid, _GRACE_SEC):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        log.warning("No permission to force-kill process %s", pid)
        return
    _wait_exit(pid, _KILL_WAIT_SEC)


def stop_other_instances(
    port: int,
    *,
    config_path: Path,
    my_pid: int | None = None,
) -> list[int]:
    """
    Stop other DeckDrop processes before binding the API port.

    Skipped when DECKDROP_SKIP_SINGLE_INSTANCE=1 (tests).
    """
    if os.environ.get("DECKDROP_SKIP_SINGLE_INSTANCE"):
        return []

    my_pid = my_pid or os.getpid()
    candidates: set[int] = set()

    pid_path = pid_file_path(config_path)
    if pid_path.exists():
        try:
            old_pid = int(pid_path.read_text().strip())
            if is_deckdrop_process(old_pid, skip_pid=my_pid):
                candidates.add(old_pid)
        except ValueError:
            pass

    for pid in pids_listening_on_port(port):
        if is_deckdrop_process(pid, skip_pid=my_pid):
            candidates.add(pid)

    stopped: list[int] = []
    for pid in sorted(candidates):
        log.info("Stopping previous DeckDrop instance (PID %s)", pid)
        kill_process(pid)
        stopped.append(pid)

    if stopped:
        time.sleep(0.25)
    return stopped


def write_pid_file(config_path: Path, pid: int | None = None) -> None:
    path = pid_file_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid or os.getpid()}\n")


def remove_pid_file(config_path: Path) -> None:
    try:
        pid_file_path(config_path).unlink(missing_ok=True)
    except OSError:
        pass
