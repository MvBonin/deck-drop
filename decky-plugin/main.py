import logging
import os
import socket
import subprocess

log = logging.getLogger("deckdrop-plugin")
DECKDROP_PORT = 7373


def _is_running() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", DECKDROP_PORT)) == 0


class Plugin:
    async def _main(self):
        pass

    async def is_running(self):
        return {"running": _is_running()}

    async def start(self):
        if _is_running():
            return {"success": True}
        home = os.path.expanduser("~")
        deckdrop_bin = os.path.join(home, ".local", "bin", "deckdrop")
        if not os.path.isfile(deckdrop_bin):
            log.error("deckdrop not found at %s", deckdrop_bin)
            return {"success": False, "error": "deckdrop not installed"}
        subprocess.Popen(
            [deckdrop_bin, "--headless"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return {"success": True}

    async def stop(self):
        subprocess.run(["pkill", "-f", "deckdrop --headless"], check=False)
        return {"success": True}

    async def _unload(self):
        pass
