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
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from deckdrop.api import state as app_state
from deckdrop.core import game as game_mod
from deckdrop.core import integrity
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
    path: str

    @classmethod
    def from_info(cls, g: GameInfo) -> "GameOut":
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
            has_torrent=bool(g.torrent.magnet),
            path=str(g.path),
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


# -- Routes --

@router.get("/games", response_model=list[GameOut])
def list_games() -> list[GameOut]:
    s = app_state.get()
    s.library.reload(s.cfg)
    return [GameOut.from_info(g) for g in s.library.all()]


@router.get("/games/{game_id}", response_model=GameOut)
def get_game(game_id: str) -> GameOut:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Game not found")
    return GameOut.from_info(g)


@router.post("/games", response_model=GameOut, status_code=201)
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

    s.library.add(info)

    # Hash files in the background
    background_tasks.add_task(_hash_game_files, info)

    return GameOut.from_info(info)


@router.patch("/games/{game_id}", response_model=GameOut)
def patch_game(game_id: str, req: PatchGameRequest) -> GameOut:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Game not found")

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

    if changed:
        game_mod.bump_version(g, s.cfg.user_name)

    return GameOut.from_info(g)


@router.delete("/games/{game_id}", status_code=204)
def remove_game(game_id: str) -> None:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Game not found")

    # Remove from individual paths list if it was there
    s.cfg.remove_game_path(g.path)
    save_cfg(s.cfg)
    s.library.remove(game_id)


@router.get("/games/{game_id}/magnet")
def get_magnet(game_id: str) -> dict[str, str]:
    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        raise HTTPException(404, "Game not found")
    if not g.torrent.magnet:
        raise HTTPException(404, "No magnet link available yet")
    return {"magnet": g.torrent.magnet, "info_hash": g.torrent.info_hash}


# -- Background task --

def _hash_game_files(info: GameInfo) -> None:
    """Hash all game files and update deckdrop.toml."""
    try:
        hashes, total = integrity.hash_directory(info.path)
        info.files = hashes
        info.size_bytes = total
        game_mod.save(info)
    except Exception as exc:
        # Non-fatal: hashing can be slow, errors are logged but don't crash
        import logging
        logging.getLogger(__name__).error("Hashing failed for %s: %s", info.path, exc)
