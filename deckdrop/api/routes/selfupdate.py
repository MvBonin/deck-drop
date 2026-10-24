"""/api/selfupdate – check for, download and install new DeckDrop versions.

GitHub is only contacted on POST /check (user request). LAN peers offer their
own signed AppImage via /offer + /package; every source is verified against
the signed release manifest before anything is replaced.
"""

from __future__ import annotations

import asyncio
import os
import signal
import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.core import selfupdate
from deckdrop.core.i18n import t

router = APIRouter(tags=["selfupdate"])


def _registry() -> object | None:
    try:
        return app_state.get().peer_registry
    except RuntimeError:
        return None


@router.get("/selfupdate", dependencies=[Depends(local_only)])
def get_selfupdate() -> dict[str, Any]:
    return selfupdate.status(_registry())


@router.post("/selfupdate/check", dependencies=[Depends(local_only)])
async def check_selfupdate() -> dict[str, Any]:
    await asyncio.to_thread(selfupdate.check_github)
    return selfupdate.status(_registry())


class InstallRequest(BaseModel):
    source: str = "github"  # "github" or a peer_id


@router.post("/selfupdate/install", status_code=202, dependencies=[Depends(local_only)])
def install_selfupdate(req: InstallRequest) -> dict[str, Any]:
    if req.source == "github":
        gh = selfupdate._state.github or {}
        src = gh.get("_source")
        if not src:
            raise HTTPException(409, gh.get("error") or t("err.no_checked_update"))
        src = {**src, "label": "GitHub"}
    else:
        registry = _registry()
        peer = registry.get(req.source) if registry is not None else None
        offer = getattr(peer, "app_offer", None) if peer else None
        if not peer or not peer.online or not isinstance(offer, dict):
            raise HTTPException(404, "Peer bietet kein DeckDrop-Update an.")
        from deckdrop.network.peer_registry import _peer_http_url

        src = {
            "kind": "peer",
            "url": _peer_http_url(peer.address, peer.port, "/api/selfupdate/package"),
            "manifest": offer.get("manifest", ""),
            "signature": offer.get("signature", ""),
            "label": peer.name,
        }
    try:
        selfupdate.start_install(src)
    except selfupdate.UpdateError as exc:
        raise HTTPException(409, str(exc)) from exc
    return selfupdate.status(_registry())


def _stop_soon() -> None:
    def _stop() -> None:
        import time

        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGINT)

    threading.Thread(target=_stop, daemon=True).start()


@router.post("/selfupdate/restart", dependencies=[Depends(local_only)])
def restart_selfupdate() -> dict[str, bool]:
    """Graceful shutdown; main() then execs the (new) AppImage."""
    selfupdate.request_restart()
    _stop_soon()
    return {"ok": True}


@router.post("/selfupdate/rollback", dependencies=[Depends(local_only)])
def rollback_selfupdate() -> dict[str, bool]:
    try:
        selfupdate.rollback()
    except (selfupdate.UpdateError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    selfupdate.request_restart()
    _stop_soon()
    return {"ok": True}


# -- LAN: offered to peers (not local_only) --


@router.get("/selfupdate/offer")
def get_offer() -> dict[str, Any]:
    offer = selfupdate.local_offer()
    if offer is None:
        raise HTTPException(404, t("err.no_signed_package"))
    return offer


@router.get("/selfupdate/package")
def get_package() -> FileResponse:
    if selfupdate.local_offer() is None:
        raise HTTPException(404, t("err.no_signed_package"))
    path = selfupdate.appimage_path()
    return FileResponse(
        str(path),
        media_type="application/octet-stream",
        filename=f"DeckDrop-{selfupdate.__version__}-x86_64.AppImage",
    )
