"""
/api/downloads – start, list, pause, resume, retry, remove downloads.
"""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only

router = APIRouter(tags=["downloads"], dependencies=[Depends(local_only)])

_MAGNET_REQUEST_TIMEOUT = 120.0
_MAGNET_PREP_MAX_WAIT = 600.0
_MAGNET_PREP_POLL = 2.0


def _peer_http_url(address: str, port: int, path: str) -> str:
    """Build peer URL; bracket IPv6 literals."""
    host = address
    if ":" in address and not address.startswith("["):
        host = f"[{address}]"
    return f"http://{host}:{port}{path}"


def _fetch_magnet_from_peer(address: str, port: int, game_id: str) -> str:
    """Fetch magnet from host; retry while host prepares torrent (HTTP 409)."""
    url = _peer_http_url(address, port, f"/api/games/{game_id}/magnet")
    deadline = time.monotonic() + _MAGNET_PREP_MAX_WAIT
    last_detail = ""

    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=_MAGNET_REQUEST_TIMEOUT)
            if r.status_code == 409:
                last_detail = r.text or "Torrent wird vorbereitet"
                time.sleep(_MAGNET_PREP_POLL)
                continue
            r.raise_for_status()
            return r.json()["magnet"]
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 409:
                last_detail = exc.response.text or last_detail
                time.sleep(_MAGNET_PREP_POLL)
                continue
            detail = exc.response.text if exc.response is not None else str(exc)
            raise HTTPException(
                502,
                f"Magnet-Link vom Host nicht abrufbar (HTTP {exc.response.status_code}): {detail}",
            ) from exc
        except Exception as exc:
            raise HTTPException(502, f"Magnet-Link vom Host nicht abrufbar: {exc}") from exc

    raise HTTPException(
        502,
        "Magnet-Link: Host braucht zu lange für Torrent-Vorbereitung. "
        f"Am Host warten und erneut versuchen. ({last_detail})",
    )


class StartDownloadRequest(BaseModel):
    peer_id: str
    game_id: str


class DownloadOut(BaseModel):
    id: str
    game_id: str
    game_name: str
    peer_id: str
    peer_name: str
    status: str  # queued | downloading | seeding | done | error | paused
    progress: float  # 0.0–1.0
    speed_bytes_sec: int
    downloaded_bytes: int
    total_bytes: int
    num_peers: int
    error: str | None = None
    dest_path: str | None = None


def _to_out(status: object) -> DownloadOut:
    return DownloadOut(**status.__dict__)


def _get_transfer_or_503():
    s = app_state.get()
    if s.transfer is None:
        raise HTTPException(503, "Transfer nicht verfügbar (libtorrent nicht installiert)")
    return s


def _status_or_404(transfer: object, download_id: str) -> DownloadOut:
    status = transfer.get_status(download_id)
    if status is None:
        raise HTTPException(404, "Download nicht gefunden")
    return _to_out(status)


@router.post("/download", response_model=DownloadOut, status_code=202)
def start_download(req: StartDownloadRequest) -> DownloadOut:
    s = _get_transfer_or_503()

    peer = s.peer_registry.get(req.peer_id)
    if not peer:
        raise HTTPException(404, f"Peer {req.peer_id} nicht gefunden")

    game = next((g for g in peer.games if g["id"] == req.game_id), None)
    if not game:
        raise HTTPException(404, f"Spiel {req.game_id} beim Peer {req.peer_id} nicht gefunden")

    try:
        magnet = _fetch_magnet_from_peer(peer.address, peer.port, req.game_id)
        for g in peer.games:
            if g["id"] == req.game_id:
                g["has_torrent"] = True
                break
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Magnet-Link vom Host nicht abrufbar: {exc}") from exc

    dest_path = s.cfg.download_dir / game.get("name", req.game_id)

    try:
        download_id = s.transfer.start_download(
            game_id=req.game_id,
            game_name=game.get("name", "Unknown"),
            magnet=magnet,
            peer_id=req.peer_id,
            peer_name=peer.name,
            peer_address=peer.address,
            dest_path=dest_path,
        )
    except Exception as exc:
        raise HTTPException(500, f"Download konnte nicht gestartet werden: {exc}") from exc

    return _status_or_404(s.transfer, download_id)


@router.get("/downloads", response_model=list[DownloadOut])
def list_downloads() -> list[DownloadOut]:
    s = app_state.get()
    if s.transfer is None:
        return []
    return [_to_out(ds) for ds in s.transfer.all_statuses()]


@router.post("/downloads/{download_id}/pause", response_model=DownloadOut)
def pause_download(download_id: str) -> DownloadOut:
    s = _get_transfer_or_503()
    if not s.transfer.pause_download(download_id):
        raise HTTPException(404, "Download nicht gefunden")
    return _status_or_404(s.transfer, download_id)


@router.post("/downloads/{download_id}/resume", response_model=DownloadOut)
def resume_download(download_id: str) -> DownloadOut:
    s = _get_transfer_or_503()
    if not s.transfer.resume_download(download_id):
        raise HTTPException(404, "Download nicht gefunden oder konnte nicht fortgesetzt werden")
    return _status_or_404(s.transfer, download_id)


@router.post("/downloads/{download_id}/retry", response_model=DownloadOut)
def retry_download(download_id: str) -> DownloadOut:
    s = _get_transfer_or_503()
    if not s.transfer.retry_download(download_id):
        raise HTTPException(404, "Download nicht gefunden oder Wiederholung fehlgeschlagen")
    return _status_or_404(s.transfer, download_id)


@router.delete("/downloads/{download_id}", status_code=204)
def remove_download(
    download_id: str,
    delete_files: bool = Query(False, description="Spielordner auf der Festplatte löschen"),
) -> None:
    s = _get_transfer_or_503()
    if not s.transfer.remove_download(download_id, delete_files=delete_files):
        raise HTTPException(404, "Download nicht gefunden")
