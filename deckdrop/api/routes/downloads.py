"""
/api/downloads – start, list, pause, resume, retry, remove downloads.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.api.websocket import broadcast
from deckdrop.core import game as game_mod
from deckdrop.core.i18n import t

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
                last_detail = r.text or t("err.torrent_preparing")
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
                t("err.magnet_http", status=exc.response.status_code, detail=detail),
            ) from exc
        except Exception as exc:
            raise HTTPException(502, t("err.magnet_fetch", error=exc)) from exc

    raise HTTPException(
        502,
        t("err.magnet_timeout", detail=last_detail),
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
    kind: str = "new"  # "new" | "update" (Phase 5)
    target_version_label: str = ""  # only set for kind == "update"
    repair: bool = False  # update run as "Reparieren"
    # "" | "waiting" | "replaced" – see transfer.DownloadStatus.source_state
    source_state: str = ""
    switch_version_key: str = ""
    switch_version_label: str = ""


class SwitchVersionRequest(BaseModel):
    version_key: str


def _to_out(status: object) -> DownloadOut:
    return DownloadOut(**status.__dict__)


def _get_transfer_or_503():
    s = app_state.get()
    if s.transfer is None:
        raise HTTPException(503, t("err.transfer_unavailable"))
    return s


def _status_or_404(transfer: object, download_id: str) -> DownloadOut:
    status = transfer.get_status(download_id)
    if status is None:
        raise HTTPException(404, t("err.download_not_found"))
    return _to_out(status)


# Downloads whose start is still in flight: the route answers right away
# and fetches manifest / .torrent / magnet from the host in the background
# (that can take from seconds up to _MAGNET_PREP_MAX_WAIT). Until the torrent
# is added these placeholders show up in GET /api/downloads, with the id the
# real download will get – the UI shows the row at once and a second click
# on "Laden" finds it instead of starting the game twice.
_pending_starts: dict[str, DownloadOut] = {}
_pending_tasks: dict[str, asyncio.Task] = {}
# Starts whose route already returned the placeholder: a later failure can
# only reach the UI via WebSocket (otherwise the HTTP error carries it).
_detached_starts: set[str] = set()
# How long the route waits for a fast start (host answers at once) before it
# returns the placeholder instead of the real status.
_START_GRACE_SECONDS = 0.5


def _pending_for_game(game_id: str) -> DownloadOut | None:
    return next((p for p in _pending_starts.values() if p.game_id == game_id), None)


def _active_download_for_game(transfer: object, game_id: str) -> DownloadOut | None:
    """A first download of `game_id` that is still in the downloads list."""
    try:
        statuses = list(transfer.all_statuses())
    except Exception:
        return None
    for st in statuses:
        if getattr(st, "game_id", None) == game_id and getattr(st, "kind", "new") != "update":
            try:
                return _to_out(st)
            except Exception:
                return None
    return None


async def _finish_start(
    s: object,
    req: StartDownloadRequest,
    download_id: str,
    chosen_peer: object,
    game: dict,
    dest_path: object,
    extra_peer_ids: list[str],
    expected_content_hash: str,
) -> DownloadOut:
    """Fetch what the host offers and hand the torrent to libtorrent."""
    manifest: dict | None = None
    torrent_bytes: bytes | None = None
    try:
        if req.version_key is not None:
            manifest = await asyncio.to_thread(
                _fetch_peer_manifest, chosen_peer.address, chosen_peer.port, req.game_id
            )
            torrent_bytes = await asyncio.to_thread(
                _fetch_peer_torrent, chosen_peer.address, chosen_peer.port, req.game_id
            )

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
                raise HTTPException(502, t("err.magnet_fetch", error=exc)) from exc

        if download_id not in _pending_starts:
            # Removed by the user while we were waiting for the host.
            raise asyncio.CancelledError

        try:
            s.transfer.start_download(
                game_id=req.game_id,
                game_name=game.get("name", "Unknown"),
                magnet=magnet,
                peer_id=chosen_peer.peer_id,
                peer_name=chosen_peer.name,
                peer_address=chosen_peer.address,
                dest_path=dest_path,
                download_id=download_id,
                torrent_bytes=torrent_bytes,
                manifest=manifest,
                extra_peer_ids=extra_peer_ids,
                expected_content_hash=expected_content_hash,
            )
        except Exception as exc:
            raise HTTPException(500, t("err.download_start_failed", error=exc)) from exc

        out = _status_or_404(s.transfer, download_id)
        _pending_starts.pop(download_id, None)
        if out.status in ("queued", "downloading", "verifying", "paused", "error"):
            await broadcast("download_progress", out.model_dump())
        return out
    except BaseException as exc:
        placeholder = _pending_starts.pop(download_id, None)
        s.transfer.release_download_dest(dest_path)
        if not isinstance(exc, asyncio.CancelledError) and download_id in _detached_starts:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            log.warning("Download start for %s failed: %s", req.game_id, detail)
            await broadcast(
                "download_start_failed",
                {
                    "id": download_id,
                    "game_id": req.game_id,
                    "game_name": placeholder.game_name if placeholder else game.get("name", ""),
                    "error": detail,
                },
            )
        raise
    finally:
        _pending_tasks.pop(download_id, None)
        _detached_starts.discard(download_id)


@router.post("/download", response_model=DownloadOut, status_code=202)
async def start_download(req: StartDownloadRequest) -> DownloadOut:
    s = _get_transfer_or_503()

    if s.library.get(req.game_id) is not None:
        raise HTTPException(409, t("err.already_installed"))

    peer = s.peer_registry.get(req.peer_id)
    if not peer:
        raise HTTPException(404, t("err.peer_id_not_found", peer_id=req.peer_id))

    game = next((g for g in peer.games if g["id"] == req.game_id), None)
    if not game:
        raise HTTPException(
            404, t("err.peer_game_id_not_found", game_id=req.game_id, peer_id=req.peer_id)
        )

    # Already starting or downloading this game (double click, or "Laden"
    # pressed again while the host was still answering): hand back that
    # download instead of starting a second one into the same folder.
    existing = _pending_for_game(req.game_id) or _active_download_for_game(s.transfer, req.game_id)
    if existing is not None:
        return existing

    # Which peer(s) actually serve the requested version. Without a
    # version_key (old clients / recommended version) it's just `peer`.
    chosen_peer = peer
    extra_peer_ids: list[str] = []
    expected_content_hash = ""

    if req.version_key is not None:
        version_peers = s.peer_registry.peers_for_version(req.game_id, req.version_key)
        if not version_peers:
            raise HTTPException(404, t("err.version_not_found", version=req.version_key))
        chosen_peer = version_peers[0]
        extra_peer_ids = [p.peer_id for p in version_peers[1:]]
        game = next((g for g in chosen_peer.games if g["id"] == req.game_id), game)
        expected_content_hash = game.get("content_hash") or ""

    dest_path = (s.cfg.download_dir / game.get("name", req.game_id)).resolve()
    s.transfer.reserve_download_dest(dest_path)

    stale_toml = dest_path / game_mod.TOML_FILENAME
    if stale_toml.is_file():
        try:
            stale_toml.unlink()
        except OSError as exc:
            log.warning("Could not remove stale deckdrop.toml at %s: %s", stale_toml, exc)

    download_id = secrets.token_hex(4)
    placeholder = DownloadOut(
        id=download_id,
        game_id=req.game_id,
        game_name=game.get("name", "Unknown"),
        peer_id=chosen_peer.peer_id,
        peer_name=chosen_peer.name,
        status="queued",
        progress=0.0,
        speed_bytes_sec=0,
        downloaded_bytes=0,
        total_bytes=0,
        num_peers=0,
        dest_path=str(dest_path),
        phase="starting",
    )
    # Registered before the first await, so a concurrent second request
    # for the same game already finds it.
    _pending_starts[download_id] = placeholder

    task = asyncio.create_task(
        _finish_start(
            s,
            req,
            download_id,
            chosen_peer,
            game,
            dest_path,
            extra_peer_ids,
            expected_content_hash,
        )
    )
    _pending_tasks[download_id] = task
    # Retrieve the exception of a background failure so asyncio doesn't log
    # "Task exception was never retrieved" – it was broadcast already.
    task.add_done_callback(lambda tk: tk.cancelled() or tk.exception())

    done, _ = await asyncio.wait({task}, timeout=_START_GRACE_SECONDS)
    if done:
        # Fast host: return the real status, or the real error.
        return task.result()

    _detached_starts.add(download_id)
    await broadcast("download_progress", placeholder.model_dump())
    return placeholder


@router.post("/downloads/{download_id}/switch-version", response_model=DownloadOut)
async def switch_download_version(download_id: str, req: SwitchVersionRequest) -> DownloadOut:
    """Continue an unfinished first download with another version of the game
    (the one it was started with is gone, e.g. the host published an update).
    Files already downloaded are kept and reused where they still match."""
    s = _get_transfer_or_503()
    current = s.transfer.get_status(download_id)
    if current is None:
        raise HTTPException(404, t("err.download_not_found"))
    if current.kind != "new":
        raise HTTPException(400, t("err.switch_version_not_new"))

    version_peers = s.peer_registry.peers_for_version(current.game_id, req.version_key)
    if not version_peers:
        raise HTTPException(404, t("err.version_not_found", version=req.version_key))
    chosen = version_peers[0]
    game = next((g for g in chosen.games if g.get("id") == current.game_id), {})

    manifest = await asyncio.to_thread(
        _fetch_peer_manifest, chosen.address, chosen.port, current.game_id
    )
    torrent_bytes = await asyncio.to_thread(
        _fetch_peer_torrent, chosen.address, chosen.port, current.game_id
    )
    magnet = ""
    if torrent_bytes is not None:
        try:
            from deckdrop.core.torrent import make_magnet

            magnet, _ = make_magnet(torrent_bytes)
        except Exception as exc:
            log.warning("Torrent bytes from %s unusable, falling back: %s", chosen, exc)
            torrent_bytes = None
    if not magnet:
        magnet = await asyncio.to_thread(
            _fetch_magnet_from_peer, chosen.address, chosen.port, current.game_id
        )

    ok = s.transfer.switch_download_version(
        download_id,
        magnet=magnet,
        torrent_bytes=torrent_bytes,
        manifest=manifest,
        expected_content_hash=game.get("content_hash") or "",
        peer_id=chosen.peer_id,
        peer_name=chosen.name,
        peer_address=chosen.address,
        extra_peer_ids=[p.peer_id for p in version_peers[1:]],
    )
    if not ok:
        raise HTTPException(500, t("err.switch_version_failed"))
    out = _status_or_404(s.transfer, download_id)
    await broadcast("download_progress", out.model_dump())
    return out


@router.get("/downloads", response_model=list[DownloadOut])
def list_downloads() -> list[DownloadOut]:
    s = app_state.get()
    if s.transfer is None:
        return []
    out = [_to_out(ds) for ds in s.transfer.all_statuses()]
    known = {d.id for d in out}
    out.extend(p for did, p in _pending_starts.items() if did not in known)
    return out


@router.post("/downloads/{download_id}/pause", response_model=DownloadOut)
def pause_download(download_id: str) -> DownloadOut:
    s = _get_transfer_or_503()
    if not s.transfer.pause_download(download_id):
        raise HTTPException(404, t("err.download_not_found"))
    return _status_or_404(s.transfer, download_id)


@router.post("/downloads/{download_id}/resume", response_model=DownloadOut)
def resume_download(download_id: str) -> DownloadOut:
    s = _get_transfer_or_503()
    if not s.transfer.resume_download(download_id):
        raise HTTPException(404, t("err.download_resume_failed"))
    return _status_or_404(s.transfer, download_id)


@router.post("/downloads/{download_id}/retry", response_model=DownloadOut)
def retry_download(download_id: str) -> DownloadOut:
    s = _get_transfer_or_503()
    if not s.transfer.retry_download(download_id):
        raise HTTPException(404, t("err.download_retry_failed"))
    return _status_or_404(s.transfer, download_id)


@router.delete("/downloads/{download_id}", status_code=204)
def remove_download(
    download_id: str,
    delete_files: bool = Query(False, description="Delete the game folder on disk"),
) -> None:
    s = _get_transfer_or_503()
    if download_id in _pending_starts:
        # Still waiting for the host – nothing on disk yet, just stop it.
        _pending_starts.pop(download_id, None)
        task = _pending_tasks.pop(download_id, None)
        if task is not None:
            # This route runs in the threadpool – cancel on the task's loop.
            task.get_loop().call_soon_threadsafe(task.cancel)
        return None
    if not s.transfer.remove_download(download_id, delete_files=delete_files):
        raise HTTPException(404, t("err.download_not_found"))
