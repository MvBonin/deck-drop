"""POST /api/shutdown – stop the DeckDrop server (localhost only)."""

from __future__ import annotations

import os
import signal
import threading

from fastapi import APIRouter, Depends

from deckdrop.api.deps import local_only

router = APIRouter(tags=["shutdown"])


@router.post("/shutdown", dependencies=[Depends(local_only)])
def shutdown_server() -> dict[str, bool]:
    """Gracefully stop uvicorn after the HTTP response is sent."""

    def _stop() -> None:
        import time

        time.sleep(0.15)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=_stop, daemon=True).start()
    return {"ok": True}
