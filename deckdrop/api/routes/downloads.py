"""
/api/downloads – start, list, cancel downloads.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from deckdrop.api import state as app_state

router = APIRouter(tags=["downloads"])


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
    error: str | None


@router.post("/download", response_model=DownloadOut, status_code=202)
def start_download(req: StartDownloadRequest) -> DownloadOut:
    s = app_state.get()

    if s.transfer is None:
        raise HTTPException(503, "Transfer not available (libtorrent not installed)")

    # Look up peer
    peer = s.peer_registry.get(req.peer_id)
    if not peer:
        raise HTTPException(404, f"Peer {req.peer_id} not found")

    # Find the game in the peer's cached game list
    game = next((g for g in peer.games if g["id"] == req.game_id), None)
    if not game:
        raise HTTPException(404, f"Game {req.game_id} not found on peer {req.peer_id}")

    if not game.get("has_torrent"):
        raise HTTPException(409, "Peer has not generated a magnet link for this game yet")

    # Fetch magnet link from the peer
    import httpx

    try:
        r = httpx.get(
            f"http://{peer.address}:{peer.port}/api/games/{req.game_id}/magnet",
            timeout=5.0,
        )
        r.raise_for_status()
        magnet = r.json()["magnet"]
    except Exception as exc:
        raise HTTPException(502, f"Could not fetch magnet from peer: {exc}") from exc

    dest_path = s.cfg.download_dir / game.get("name", req.game_id)

    download_id = s.transfer.start_download(
        game_id=req.game_id,
        game_name=game.get("name", "Unknown"),
        magnet=magnet,
        peer_id=req.peer_id,
        peer_name=peer.name,
        peer_address=peer.address,
        dest_path=dest_path,
    )

    status = s.transfer.get_status(download_id)
    if status is None:
        raise HTTPException(500, "Failed to start download")

    return DownloadOut(**status.__dict__)


@router.get("/downloads", response_model=list[DownloadOut])
def list_downloads() -> list[DownloadOut]:
    s = app_state.get()
    if s.transfer is None:
        return []
    return [DownloadOut(**ds.__dict__) for ds in s.transfer.all_statuses()]


@router.delete("/downloads/{download_id}", status_code=204)
def cancel_download(download_id: str) -> None:
    s = app_state.get()
    if s.transfer is None:
        raise HTTPException(503, "Transfer not available")
    if not s.transfer.cancel(download_id):
        raise HTTPException(404, "Download not found")
