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

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.api.deps import local_only
from deckdrop.core import game as game_mod
from deckdrop.core import integrity, torrent_prep
from deckdrop.core.comments import (
    Comment,
    load_comments,
    new_comment,
    save_comments,
)
from deckdrop.core.config import save as save_cfg
from deckdrop.core.game import GameInfo

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

    @classmethod
    def from_info(cls, g: GameInfo) -> GameOut:
        cfg = app_state.get().cfg
        prep_error = torrent_prep.get_prep_error(g.id)
        has_torrent = bool(g.torrent.magnet)
        # Peer downloads do not need local torrent prep (only locally shared games do).
        local_share = not (g.origin.peer_id or g.origin.peer_name)
        preparing = local_share and (
            torrent_prep.is_preparing(g.id)
            or (
                not has_torrent
                and prep_error is None
                and not torrent_prep.has_cached_torrent(cfg, g.id)
            )
        )
        prep_progress: float | None = None
        if preparing:
            prep_progress = torrent_prep.get_progress(g.id)
            if prep_progress is None:
                prep_progress = 0.0
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
    exclude = (
        s.transfer.incomplete_download_dest_paths()
        if s.transfer is not None
        else frozenset()
    )
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
    if req.steam_app_id is not None:
        g.steam.app_id = req.steam_app_id
        changed = True
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


@router.get("/games/{game_id}/magnet")
def get_magnet(game_id: str) -> dict[str, str]:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Spiel nicht gefunden")

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


# -- Background task --


def _hash_game_files(game_id: str) -> None:
    """Hash all game files and update deckdrop.toml (runs after torrent prep is scheduled)."""
    import logging

    log = logging.getLogger(__name__)
    try:
        s = app_state.get()
        g = s.library.get(game_id)
        if not g:
            return
        old_files = dict(g.files)
        hashes, total = integrity.hash_directory(g.path)
        if hashes != old_files:
            torrent_prep.invalidate_torrent(game_id)
        g.files = hashes
        g.size_bytes = total
        game_mod.save(g)
    except Exception as exc:
        log.error("Hashing failed for game %s: %s", game_id, exc)
