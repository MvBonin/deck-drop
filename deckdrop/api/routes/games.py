"""
/api/games  – list, add, update, delete games.

POST /api/games          Add a game by path (runs wizard if no deckdrop.toml)
GET  /api/games          List all local games
GET  /api/games/{id}     Game details
PATCH /api/games/{id}    Update metadata
DELETE /api/games/{id}   Remove from DeckDrop (files stay)
GET  /api/games/{id}/magnet  Magnet link for transfer
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.api.routes.downloads import (
    DownloadOut,
    _fetch_peer_manifest,
    _fetch_peer_torrent,
    _to_out,
)
from deckdrop.core import content, integrity, torrent_prep
from deckdrop.core import game as game_mod
from deckdrop.core.comments import (
    Comment,
    load_comments,
    new_comment,
    save_comments,
)
from deckdrop.core.config import save as save_cfg
from deckdrop.core.cover import COVER_FILENAMES, clear_covers, download_steam_cover, has_local_cover
from deckdrop.core.game import GameInfo

log = logging.getLogger(__name__)

router = APIRouter(tags=["games"])

# -- Pydantic schemas --


class GameOut(BaseModel):
    id: str
    name: str
    version: int
    size_bytes: int
    platform: str
    available: bool
    added_by: str
    updated_at: str
    steam_app_id: int | None
    has_torrent: bool
    has_local_cover: bool = False
    info_hash: str | None = None
    source_peer_name: str | None = None
    torrent_preparing: bool = False
    torrent_prep_progress: float | None = None
    torrent_prep_error: str | None = None
    path: str
    description: str = ""
    launch_exe: str = ""
    launch_args: str = ""
    runner: str = ""
    content_state: str = "clean"
    shareable: bool = True
    change_summary: dict[str, int] = {"changed": 0, "removed": 0, "added": 0}
    revision: int = 1
    version_label: str = ""
    version_note: str = ""
    created_by: str = ""
    content_updated_by: str = ""
    content_updated_at: str = ""
    content_hash: str = ""
    update_available: bool = False
    update_version_label: str = ""
    restore_available: bool = False

    @classmethod
    def from_info(cls, g: GameInfo) -> GameOut:
        s = app_state.get()
        cfg = s.cfg
        tracker = s.get_content_tracker()
        prep_error = torrent_prep.get_prep_error(g.id)
        has_torrent = bool(g.torrent.magnet)
        # Peer downloads do not need local torrent prep (only locally shared games do).
        local_share = not (g.origin.peer_id or g.origin.peer_name)
        preparing = torrent_prep.is_preparing(g.id) or (
            local_share
            and not has_torrent
            and prep_error is None
            and not torrent_prep.has_cached_torrent(cfg, g.id)
        )
        prep_progress: float | None = None
        if preparing:
            prep_progress = torrent_prep.get_progress(g.id)
            if prep_progress is None:
                prep_progress = 0.0
        local_cover = has_local_cover(g.path)
        content_state = tracker.state(g.id)
        best_update = s.peer_registry.best_update_for(g.id, g.content.revision)
        update_version_label = ""
        if best_update is not None:
            update_version_label = best_update.get("version_label") or (
                f"Rev. {best_update.get('revision', g.content.revision + 1)}"
            )
        # Phase 7: a game changed locally, but a peer still offers exactly the
        # same content we published before the change – "restore" is just an
        # update to our own version_key, reusing the same download path.
        restore_available = bool(
            content_state == "modified"
            and g.content.content_hash
            and s.peer_registry.peers_for_version(g.id, g.content.content_hash)
        )
        return cls(
            id=g.id,
            name=g.name,
            version=g.version,
            size_bytes=g.size_bytes,
            platform=g.platform,
            available=g.available,
            added_by=g.added_by,
            updated_at=g.updated_at,
            steam_app_id=g.steam.app_id,
            has_torrent=has_torrent,
            has_local_cover=local_cover,
            info_hash=g.torrent.info_hash or None,
            source_peer_name=g.origin.peer_name or None,
            torrent_preparing=preparing,
            torrent_prep_progress=prep_progress,
            torrent_prep_error=prep_error,
            path=str(g.path),
            description=g.description,
            launch_exe=g.launch_exe,
            launch_args=g.steam.launch_args,
            runner=g.steam.runner,
            content_state=content_state,
            shareable=has_torrent and tracker.is_shareable(g.id),
            change_summary=tracker.summary(g.id),
            revision=g.content.revision,
            version_label=g.content.version_label,
            version_note=g.content.note,
            created_by=g.content.created_by,
            content_updated_by=g.content.updated_by,
            content_updated_at=g.content.updated_at,
            content_hash=g.content.content_hash,
            update_available=best_update is not None,
            update_version_label=update_version_label,
            restore_available=restore_available,
        )


class AddGameRequest(BaseModel):
    path: str
    # Wizard fields – required only when deckdrop.toml doesn't exist
    name: str | None = None
    platform: str = "any"
    steam_app_id: int | None = None


class PatchGameRequest(BaseModel):
    name: str | None = None
    platform: str | None = None
    steam_app_id: int | None = None
    description: str | None = None
    launch_exe: str | None = None
    launch_args: str | None = None
    runner: str | None = None


class AddCommentRequest(BaseModel):
    text: str


class CommentOut(BaseModel):
    id: str
    author: str
    text: str
    created_at: str

    @classmethod
    def from_comment(cls, c: Comment) -> CommentOut:
        return cls(id=c.id, author=c.author, text=c.text, created_at=c.created_at)


# -- Routes --


@router.get("/games", response_model=list[GameOut])
def list_games() -> list[GameOut]:
    s = app_state.get()
    exclude = s.transfer.incomplete_download_dest_paths() if s.transfer is not None else frozenset()
    s.library.reload(s.cfg, exclude_paths=exclude)
    torrent_prep.restore_all_cached(s.library, s.cfg, s.transfer)
    return [GameOut.from_info(g) for g in s.library.all()]


@router.get("/games/{game_id}", response_model=GameOut)
def get_game(game_id: str) -> GameOut:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")
    return GameOut.from_info(g)


@router.post("/games", response_model=GameOut, status_code=201, dependencies=[Depends(local_only)])
def add_game(req: AddGameRequest, background_tasks: BackgroundTasks) -> GameOut:
    s = app_state.get()
    path = Path(req.path).expanduser().resolve()

    if not path.is_dir():
        raise HTTPException(400, f"Path is not a directory: {path}")

    if s.library.needs_wizard(path):
        if not req.name:
            raise HTTPException(
                422,
                "No deckdrop.toml found. Provide 'name' to create one (wizard mode).",
            )
        info = game_mod.create_new(
            game_path=path,
            name=req.name,
            added_by=s.cfg.user_name,
            platform=req.platform,
            steam_app_id=req.steam_app_id,
        )
        game_mod.save(info)
    else:
        loaded = game_mod.load_from_path(path)
        if not loaded:
            raise HTTPException(500, "Failed to load deckdrop.toml")
        info = loaded

    # Register path in config if it's outside the download_dir
    if not str(path).startswith(str(s.cfg.download_dir)):
        s.cfg.add_game_path(path)
        save_cfg(s.cfg)

    if info.size_bytes <= 0:
        info.size_bytes = integrity.dir_size(info.path)
        game_mod.save(info)

    s.library.add(info)

    if info.steam.app_id:
        download_steam_cover(info.path, info.steam.app_id)

    # Start torrent/magnet prep immediately (must not wait behind integrity hashing).
    torrent_prep.schedule_prepare(info.id)
    # Blake2b file hashes for deckdrop.toml — slow on large games, runs in parallel.
    background_tasks.add_task(_hash_game_files, info.id)

    return GameOut.from_info(info)


@router.patch("/games/{game_id}", response_model=GameOut, dependencies=[Depends(local_only)])
def patch_game(game_id: str, req: PatchGameRequest) -> GameOut:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")

    changed = False
    if req.name is not None:
        g.name = req.name
        changed = True
    if req.platform is not None:
        g.platform = req.platform
        changed = True
    if "steam_app_id" in req.model_fields_set:
        new_id = req.steam_app_id
        id_changed = new_id != g.steam.app_id
        if id_changed:
            g.steam.app_id = new_id
            changed = True
        if new_id:
            if id_changed or not has_local_cover(g.path):
                download_steam_cover(g.path, new_id)
        elif id_changed:
            clear_covers(g.path)
    if req.description is not None:
        g.description = req.description
        changed = True
    if req.launch_exe is not None:
        g.launch_exe = req.launch_exe
        changed = True
    if req.launch_args is not None:
        g.steam.launch_args = req.launch_args
        changed = True
    if req.runner is not None:
        g.steam.runner = req.runner
        changed = True

    if changed:
        game_mod.bump_version(g, s.cfg.user_name)
        game_mod.save(g)

    return GameOut.from_info(g)


@router.delete("/games/{game_id}", status_code=204, dependencies=[Depends(local_only)])
def remove_game(game_id: str) -> None:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")

    # Remove from individual paths list if it was there
    s.cfg.remove_game_path(g.path)
    save_cfg(s.cfg)
    s.library.remove(game_id)


@router.get("/games/{game_id}/comments", response_model=list[CommentOut])
def list_comments(game_id: str) -> list[CommentOut]:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")
    comments = load_comments(g.path)
    return [CommentOut.from_comment(c) for c in comments]


@router.post(
    "/games/{game_id}/comments",
    response_model=CommentOut,
    status_code=201,
    dependencies=[Depends(local_only)],
)
def add_comment(game_id: str, req: AddCommentRequest) -> CommentOut:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")
    if not req.text.strip():
        raise HTTPException(400, "Kommentar darf nicht leer sein")
    comment = new_comment(author=s.cfg.user_name, text=req.text.strip())
    existing = load_comments(g.path)
    save_comments(g.path, existing + [comment])
    return CommentOut.from_comment(comment)


@router.get("/games/{game_id}/cover")
def get_cover(game_id: str) -> FileResponse:
    g = app_state.get().library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")
    for name in COVER_FILENAMES:
        p = g.path / name
        if p.is_file():
            return FileResponse(p)
    raise HTTPException(404, "Kein lokales Cover gefunden")


@router.post("/games/{game_id}/search_cover", dependencies=[Depends(local_only)])
async def search_cover(game_id: str) -> dict[str, int]:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")

    search_url = (
        f"https://store.steampowered.com/api/storesearch/?term={quote(g.name)}&l=english&cc=US"
    )
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(search_url)
            r.raise_for_status()
            items = r.json().get("items", [])
    except Exception as exc:
        log.warning("Steam cover search failed for %r: %s", g.name, exc)
        raise HTTPException(502, f"Steam-Suche fehlgeschlagen: {exc}") from exc

    if not items:
        raise HTTPException(404, "Kein Steam-Treffer für diesen Spielnamen")

    app_id: int = items[0]["id"]
    g.steam.app_id = app_id
    cover_downloaded = download_steam_cover(g.path, app_id)
    game_mod.bump_version(g, s.cfg.user_name)
    game_mod.save(g)
    log.info("Auto-assigned steam_app_id=%d to game %s via search", app_id, game_id)
    return {"steam_app_id": app_id, "cover_downloaded": cover_downloaded}


@router.get("/games/{game_id}/magnet")
def get_magnet(game_id: str) -> dict[str, str]:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")

    tracker = s.get_content_tracker()
    tracker.scan(game_id)
    if not tracker.is_shareable(game_id):
        raise HTTPException(
            409,
            "Spiel wurde verändert – erst als Update veröffentlichen.",
        )

    if not g.torrent.magnet:
        torrent_prep.restore_from_cache(game_id)
    if not g.torrent.magnet:
        prep_err = torrent_prep.get_prep_error(game_id)
        if prep_err:
            raise HTTPException(503, f"Torrent konnte nicht erzeugt werden: {prep_err}") from None
        torrent_prep.schedule_prepare(game_id)
        raise HTTPException(
            409,
            "Torrent wird vorbereitet – bitte kurz warten und erneut versuchen.",
        )

    cache_path = s.cfg.torrent_cache / f"{g.id}.torrent"
    if s.transfer is not None and cache_path.is_file():
        s.transfer.seed_from_cache(g.id, g.path, cache_path)

    return {"magnet": g.torrent.magnet, "info_hash": g.torrent.info_hash}


@router.get("/games/{game_id}/manifest")
def get_manifest(game_id: str) -> dict:
    """Public (no local_only): the manifest peers use to compare/download versions."""
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")

    tracker = s.get_content_tracker()
    tracker.scan(game_id)
    if not tracker.is_shareable(game_id):
        raise HTTPException(
            409,
            "Spiel wurde verändert – erst als Update veröffentlichen.",
        )
    return game_mod.manifest_dict(g)


@router.get("/games/{game_id}/torrent")
def get_torrent(game_id: str) -> Response:
    """Public (no local_only): raw .torrent bytes for a peer starting a download."""
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")

    tracker = s.get_content_tracker()
    tracker.scan(game_id)
    if not tracker.is_shareable(game_id):
        raise HTTPException(
            409,
            "Spiel wurde verändert – erst als Update veröffentlichen.",
        )

    cache_path = s.cfg.torrent_cache / f"{g.id}.torrent"
    if not cache_path.is_file():
        prep_err = torrent_prep.get_prep_error(game_id)
        if prep_err:
            raise HTTPException(503, f"Torrent konnte nicht erzeugt werden: {prep_err}") from None
        torrent_prep.schedule_prepare(game_id)
        raise HTTPException(
            409,
            "Torrent wird vorbereitet – bitte kurz warten und erneut versuchen.",
        )

    return Response(content=cache_path.read_bytes(), media_type="application/x-bittorrent")


class PublishRequest(BaseModel):
    version_label: str = ""
    note: str = ""
    exclude: list[str] = []


_CHANGE_LIST_LIMIT = 500


@router.post("/games/scan", dependencies=[Depends(local_only)])
def scan_all_games() -> dict[str, str]:
    """Scan every local game synchronously and return {game_id: state}."""
    s = app_state.get()
    tracker = s.get_content_tracker()
    result: dict[str, str] = {}
    for g in s.library.all():
        result[g.id] = tracker.scan(g.id)
    return result


@router.post("/games/{game_id}/scan", response_model=GameOut, dependencies=[Depends(local_only)])
def scan_game(game_id: str) -> GameOut:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")
    s.get_content_tracker().scan(game_id)
    return GameOut.from_info(g)


@router.get("/games/{game_id}/changes", dependencies=[Depends(local_only)])
def get_changes(game_id: str) -> dict[str, object]:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")
    tracker = s.get_content_tracker()
    tracker.scan(game_id)
    lists = tracker.change_lists(game_id)

    def _stat_size(rel: str) -> int | None:
        try:
            return (g.path / rel).stat().st_size
        except OSError:
            return None

    changed = [
        {"path": rel, "old_size": g.sizes.get(rel), "new_size": _stat_size(rel)}
        for rel in lists["changed"]
    ]
    removed = [{"path": rel, "size": g.sizes.get(rel)} for rel in lists["removed"]]
    added = [{"path": rel, "size": _stat_size(rel)} for rel in lists["added"]]

    truncated = False
    if len(changed) > _CHANGE_LIST_LIMIT:
        changed = changed[:_CHANGE_LIST_LIMIT]
        truncated = True
    if len(removed) > _CHANGE_LIST_LIMIT:
        removed = removed[:_CHANGE_LIST_LIMIT]
        truncated = True
    if len(added) > _CHANGE_LIST_LIMIT:
        added = added[:_CHANGE_LIST_LIMIT]
        truncated = True

    return {
        "changed": changed,
        "removed": removed,
        "added": added,
        "revision": g.content.revision,
        "version_label": g.content.version_label,
        "truncated": truncated,
    }


@router.post("/games/{game_id}/publish", status_code=202, dependencies=[Depends(local_only)])
def publish_game(game_id: str, req: PublishRequest) -> dict[str, str]:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")

    for rel in req.exclude:
        if content.safe_join(g.path, rel) is None:
            raise HTTPException(400, f"Ungültiger Pfad: {rel}")

    tracker = s.get_content_tracker()
    state = tracker.state(game_id)
    if state in ("hashing", "publishing", "updating"):
        raise HTTPException(400, f"Spiel ist gerade beschäftigt ({state})")

    tracker.publish(game_id, req.version_label.strip(), req.note.strip(), req.exclude)
    return {"status": "publishing"}


_UPDATES_MANIFEST_TIMEOUT = 5.0
_UPDATES_HISTORY_LIMIT = 5


async def _manifest_for_update_check(
    client: httpx.AsyncClient, peer: object, game_id: str
) -> dict | None:
    from deckdrop.network.transfer import _peer_http_url

    url = _peer_http_url(peer.address, peer.port, f"/api/games/{game_id}/manifest")
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as exc:
        log.debug("Manifest fetch for update check failed (%s): %s", game_id, exc)
        return None


@router.get("/games/{game_id}/updates", dependencies=[Depends(local_only)])
async def get_updates(game_id: str) -> dict:
    """Versions of `game_id` available in the network, with a per-version
    download estimate (Phase 5 "5.1"). Fetches at most one manifest per
    version, from the first online peer that has it.
    """
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")

    tracker = s.get_content_tracker()
    network_games = s.peer_registry.all_network_games()
    entry = next((ng for ng in network_games if ng.get("id") == game_id), None)
    versions_meta = entry.get("versions", []) if entry else []

    local_key = g.content.content_hash or g.torrent.info_hash or ""
    out_versions: list[dict] = []
    async with httpx.AsyncClient(timeout=_UPDATES_MANIFEST_TIMEOUT) as client:
        for v in versions_meta:
            is_current = bool(local_key) and v["version_key"] == local_key
            is_newer = v["revision"] > g.content.revision
            download_estimate: int | None = None
            history: list[dict] = []
            if (is_newer or is_current) and v["shareable"] and v["has_torrent"]:
                peers = s.peer_registry.peers_for_version(game_id, v["version_key"])
                manifest = None
                for peer in peers:
                    manifest = await _manifest_for_update_check(client, peer, game_id)
                    if manifest is not None:
                        break
                if manifest is not None:
                    diff = content.diff_manifests(
                        g.files, g.sizes, manifest.get("files") or {}, manifest.get("sizes") or {}
                    )
                    download_estimate = diff.download_estimate
                    history = list(manifest.get("history") or [])[-_UPDATES_HISTORY_LIMIT:]
            out_versions.append(
                {
                    **v,
                    "is_newer": is_newer,
                    "is_current": is_current,
                    "download_estimate": download_estimate,
                    "history": history,
                }
            )

    return {
        "current": {
            "revision": g.content.revision,
            "version_label": g.content.version_label,
            "content_hash": g.content.content_hash,
            "state": tracker.state(game_id),
        },
        "versions": out_versions,
    }


class UpdateRequest(BaseModel):
    version_key: str


@router.post(
    "/games/{game_id}/update",
    response_model=DownloadOut,
    status_code=202,
    dependencies=[Depends(local_only)],
)
async def update_game(game_id: str, req: UpdateRequest) -> DownloadOut:
    """Apply an update (or, if `version_key` is the game's own content_hash and
    the game is `modified`, restore it – Phase 7 "Aus Netzwerk wiederherstellen").
    """
    s = app_state.get()
    if s.transfer is None:
        raise HTTPException(503, "Transfer nicht verfügbar (libtorrent nicht installiert)")
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")

    tracker = s.get_content_tracker()
    state = tracker.state(game_id)
    if state in ("hashing", "publishing", "updating"):
        raise HTTPException(409, f"Spiel ist gerade beschäftigt ({state})")

    peers = s.peer_registry.peers_for_version(game_id, req.version_key)
    if not peers:
        raise HTTPException(404, f"Version {req.version_key} nicht gefunden oder kein Peer online")

    primary = peers[0]
    manifest = await asyncio.to_thread(_fetch_peer_manifest, primary.address, primary.port, game_id)
    torrent_bytes = await asyncio.to_thread(
        _fetch_peer_torrent, primary.address, primary.port, game_id
    )
    if manifest is None or torrent_bytes is None:
        raise HTTPException(502, "Manifest oder Torrent-Datei vom Peer nicht abrufbar")

    try:
        # start_update does blocking file I/O (local prep, Phase 6 piece
        # hashing) – never run it directly on the event loop thread.
        download_id = await asyncio.to_thread(
            s.transfer.start_update, g, peers, torrent_bytes, manifest
        )
    except Exception as exc:
        raise HTTPException(500, f"Update konnte nicht gestartet werden: {exc}") from exc

    status = s.transfer.get_status(download_id)
    if status is None:
        raise HTTPException(500, "Update gestartet, aber Status nicht verfügbar")
    return _to_out(status)


# -- Background task --


def _hash_game_files(game_id: str) -> None:
    """Build the content baseline (files/sizes/content_hash) for a freshly added game.

    Delegates to ContentTracker.ensure_baseline, which (unlike the old
    implementation) does NOT invalidate the torrent that schedule_prepare()
    just started building.
    """
    try:
        app_state.get().get_content_tracker().ensure_baseline(game_id)
    except Exception as exc:
        log.error("Baseline hashing failed for game %s: %s", game_id, exc)
