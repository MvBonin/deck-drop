"""
/api/downloads – start, list, pause, resume, retry, remove downloads.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.api.websocket import broadcast
from deckdrop.core import game as game_mod

log = logging.getLogger(__name__)

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


def _fetch_peer_manifest(address: str, port: int, game_id: str) -> dict | None:
    """Best-effort manifest fetch; None for an old peer without /manifest or any error."""
    url = _peer_http_url(address, port, f"/api/games/{game_id}/manifest")
    try:
        r = httpx.get(url, timeout=15.0)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as exc:
        log.debug("Manifest fetch failed for %s: %s", game_id, exc)
        return None


def _fetch_peer_torrent(address: str, port: int, game_id: str) -> bytes | None:
    """Best-effort .torrent fetch; None for an old peer without /torrent or any error."""
    url = _peer_http_url(address, port, f"/api/games/{game_id}/torrent")
    try:
        r = httpx.get(url, timeout=30.0)
        if r.status_code != 200:
            return None
        return r.content
    except Exception as exc:
        log.debug("Torrent fetch failed for %s: %s", game_id, exc)
        return None


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
    # Which network version to fetch (peer_registry version_key). None keeps
    # the pre-Phase-4 behaviour: use the recommended version from `peer_id`.
    version_key: str | None = None


class DownloadOut(BaseModel):
    id: str
    game_id: str
    game_name: str
    peer_id: str
    peer_name: str
    status: str  # queued | downloading | verifying | seeding | done | error | paused
    progress: float  # 0.0–1.0
    speed_bytes_sec: int
    downloaded_bytes: int
    total_bytes: int
    num_peers: int
    pieces_total: int = 0
    pieces_missing: int = 0
    bytes_remaining: int = 0
    error: str | None = None
    error_hint: str | None = None
    dest_path: str | None = None
    phase: str = "queued"  # metadata | checking | verifying | downloading | queued | done
    phase_progress: float = 0.0  # file check progress while phase == "checking"
    stall_seconds: int = 0


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
async def start_download(req: StartDownloadRequest) -> DownloadOut:
    s = _get_transfer_or_503()

    if s.library.get(req.game_id) is not None:
        raise HTTPException(409, "Spiel ist schon installiert – Update verwenden.")

    peer = s.peer_registry.get(req.peer_id)
    if not peer:
        raise HTTPException(404, f"Peer {req.peer_id} nicht gefunden")

    game = next((g for g in peer.games if g["id"] == req.game_id), None)
    if not game:
        raise HTTPException(404, f"Spiel {req.game_id} beim Peer {req.peer_id} nicht gefunden")

    # Which peer(s) actually serve the requested version. Without a
    # version_key (old clients / recommended version) it's just `peer`.
    chosen_peer = peer
    extra_peer_ids: list[str] = []
    expected_content_hash = ""
    manifest: dict | None = None
    torrent_bytes: bytes | None = None

    if req.version_key is not None:
        version_peers = s.peer_registry.peers_for_version(req.game_id, req.version_key)
        if not version_peers:
            raise HTTPException(404, f"Version {req.version_key} nicht gefunden")
        chosen_peer = version_peers[0]
        extra_peer_ids = [p.peer_id for p in version_peers[1:]]
        game = next((g for g in chosen_peer.games if g["id"] == req.game_id), game)
        expected_content_hash = game.get("content_hash") or ""

        manifest = await asyncio.to_thread(
            _fetch_peer_manifest, chosen_peer.address, chosen_peer.port, req.game_id
        )
        torrent_bytes = await asyncio.to_thread(
            _fetch_peer_torrent, chosen_peer.address, chosen_peer.port, req.game_id
        )

    dest_path = (s.cfg.download_dir / game.get("name", req.game_id)).resolve()
    s.transfer.reserve_download_dest(dest_path)

    stale_toml = dest_path / game_mod.TOML_FILENAME
    if stale_toml.is_file():
        try:
            stale_toml.unlink()
        except OSError as exc:
            log.warning("Could not remove stale deckdrop.toml at %s: %s", stale_toml, exc)

    try:
        if torrent_bytes is not None:
            try:
                from deckdrop.core.torrent import make_magnet

                magnet, _info_hash = make_magnet(torrent_bytes)
            except Exception as exc:
                log.warning("Torrent bytes from %s unusable, falling back: %s", chosen_peer, exc)
                torrent_bytes = None

        if torrent_bytes is None:
            # Either no version_key was given, or the peer is too old to
            # serve /torrent (404) – fall back to the magnet flow as before.
            try:
                magnet = await asyncio.to_thread(
                    _fetch_magnet_from_peer, chosen_peer.address, chosen_peer.port, req.game_id
                )
                for g in chosen_peer.games:
                    if g["id"] == req.game_id:
                        g["has_torrent"] = True
                        break
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(502, f"Magnet-Link vom Host nicht abrufbar: {exc}") from exc

        try:
            download_id = s.transfer.start_download(
                game_id=req.game_id,
                game_name=game.get("name", "Unknown"),
                magnet=magnet,
                peer_id=chosen_peer.peer_id,
                peer_name=chosen_peer.name,
                peer_address=chosen_peer.address,
                dest_path=dest_path,
                torrent_bytes=torrent_bytes,
                manifest=manifest,
                extra_peer_ids=extra_peer_ids,
                expected_content_hash=expected_content_hash,
            )
        except Exception as exc:
            raise HTTPException(500, f"Download konnte nicht gestartet werden: {exc}") from exc

        out = _status_or_404(s.transfer, download_id)
        if out.status in ("queued", "downloading", "verifying", "paused", "error"):
            await broadcast("download_progress", out.model_dump())
        return out
    except Exception:
        s.transfer.release_download_dest(dest_path)
        raise


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
