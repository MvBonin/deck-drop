"""
/api/downloads – start, list, cancel downloads.
Transfer logic (libtorrent) wired in Phase 2.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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
    status: str          # queued | downloading | seeding | done | error | paused
    progress: float      # 0.0–1.0
    speed_bytes_sec: int
    downloaded_bytes: int
    total_bytes: int
    num_peers: int
    error: str | None


# In-memory store; replaced by transfer.py in Phase 2
_downloads: dict[str, DownloadOut] = {}


@router.post("/download", response_model=DownloadOut, status_code=202)
def start_download(req: StartDownloadRequest) -> DownloadOut:
    raise HTTPException(501, "Transfer not implemented yet (Phase 2)")


@router.get("/downloads", response_model=list[DownloadOut])
def list_downloads() -> list[DownloadOut]:
    return list(_downloads.values())


@router.delete("/downloads/{download_id}", status_code=204)
def cancel_download(download_id: str) -> None:
    if download_id not in _downloads:
        raise HTTPException(404, "Download not found")
    del _downloads[download_id]
