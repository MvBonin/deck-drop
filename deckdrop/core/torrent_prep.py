"""
Background preparation of .torrent / magnet for shared games.

Hashing entire game folders synchronously in HTTP handlers can OOM or kill
the process on memory-constrained hosts (e.g. Steam Deck). Preparation runs
in a daemon thread instead; progress is pushed via WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

_lock = threading.Lock()
_preparing: set[str] = set()
_errors: dict[str, str] = {}
_progress: dict[str, float] = {}
_loop: asyncio.AbstractEventLoop | None = None


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def is_preparing(game_id: str) -> bool:
    with _lock:
        return game_id in _preparing


def get_prep_error(game_id: str) -> str | None:
    with _lock:
        return _errors.get(game_id)


def get_progress(game_id: str) -> float | None:
    with _lock:
        if game_id not in _preparing:
            return None
        return _progress.get(game_id, 0.0)


def _cache_path(cfg: object, game_id: str) -> object:
    from pathlib import Path

    return Path(cfg.torrent_cache) / f"{game_id}.torrent"


def has_cached_torrent(cfg: object, game_id: str) -> bool:
    return _cache_path(cfg, game_id).is_file()


def restore_from_cache(game_id: str) -> bool:
    """
    Load magnet/info_hash from an existing .torrent cache into deckdrop.toml.
    Returns True if the game is ready to share without re-hashing.
    """
    from deckdrop.api import state as app_state
    from deckdrop.core import game as game_mod
    from deckdrop.core.torrent import make_magnet

    s = app_state.get()
    g = s.library.get(game_id)
    if not g or g.torrent.magnet:
        return bool(g and g.torrent.magnet)

    cache = _cache_path(s.cfg, game_id)
    if not cache.is_file():
        return False

    try:
        torrent_bytes = cache.read_bytes()
        magnet, info_hash = make_magnet(torrent_bytes)
        g.torrent.magnet = magnet
        g.torrent.info_hash = info_hash
        game_mod.save(g)
        if s.transfer is not None:
            s.transfer.seed_from_cache(g.id, g.path, cache)
        log.info("Restored torrent for %s from cache", g.name)
        return True
    except Exception as exc:
        log.warning("Could not restore torrent cache for %s: %s", game_id, exc)
        return False


def restore_all_cached(library: object, cfg: object, transfer: object | None) -> int:
    """Restore magnets from disk cache for all library games. Returns count restored."""
    restored = 0
    for g in library.all():  # type: ignore[union-attr]
        if g.torrent.magnet:
            continue
        if restore_from_cache(g.id):
            restored += 1
    return restored


def schedule_prepare(game_id: str) -> None:
    """Start torrent/magnet preparation in a background thread if needed."""
    from deckdrop.api import state as app_state

    s = app_state.get()
    g = s.library.get(game_id)
    if not g:
        return

    if restore_from_cache(game_id):
        return
    if g.torrent.magnet:
        return
    if has_cached_torrent(s.cfg, game_id):
        # Cache present but restore failed – do not re-hash blindly
        return

    with _lock:
        if game_id in _preparing:
            return
        _errors.pop(game_id, None)
        _progress[game_id] = 0.0
        _preparing.add(game_id)

    _emit("torrent_prep_started", {"game_id": game_id, "progress": 0.0})
    threading.Thread(
        target=_prepare,
        args=(game_id,),
        daemon=True,
        name=f"torrent-prep-{game_id}",
    ).start()
    log.info("Scheduled torrent preparation for %s (%s)", g.name, game_id)


def _set_progress(game_id: str, progress: float) -> None:
    progress = max(0.0, min(0.99, progress))
    with _lock:
        if game_id not in _preparing:
            return
        prev = _progress.get(game_id, 0.0)
        if progress <= prev + 0.005:
            return
        _progress[game_id] = progress
    _emit("torrent_prep_progress", {"game_id": game_id, "progress": progress})


def _emit(event: str, data: dict[str, Any]) -> None:
    from deckdrop.api.websocket import broadcast

    loop = _loop
    if loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(broadcast(event, data), loop)
    except Exception as exc:
        log.debug("Could not emit %s: %s", event, exc)


def _prepare(game_id: str) -> None:
    from deckdrop.api import state as app_state
    from deckdrop.core import game as game_mod
    from deckdrop.core.torrent import create_torrent_data, make_magnet

    try:
        s = app_state.get()
        g = s.library.get(game_id)
        if not g:
            return
        if restore_from_cache(game_id) or g.torrent.magnet:
            with _lock:
                _progress[game_id] = 1.0
            _emit(
                "torrent_prep_complete",
                {"game_id": game_id, "progress": 1.0, "has_torrent": True},
            )
            return

        log.info("Preparing torrent for %s (%s)", g.name, game_id)
        _set_progress(game_id, 0.05)

        torrent_bytes = create_torrent_data(g.path, on_progress=lambda p: _set_progress(game_id, p))
        magnet, info_hash = make_magnet(torrent_bytes)
        g.torrent.magnet = magnet
        g.torrent.info_hash = info_hash
        game_mod.save(g)

        cache_path = s.cfg.torrent_cache / f"{g.id}.torrent"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(torrent_bytes)

        if s.transfer is not None:
            s.transfer.seed_from_cache(g.id, g.path, cache_path)

        with _lock:
            _progress[game_id] = 1.0
        _emit(
            "torrent_prep_complete",
            {"game_id": game_id, "progress": 1.0, "has_torrent": True},
        )
        log.info("Torrent ready for %s (%s)", g.name, game_id)
    except Exception as exc:
        msg = str(exc)
        with _lock:
            _errors[game_id] = msg
        _emit("torrent_prep_error", {"game_id": game_id, "error": msg})
        log.error("Torrent preparation failed for %s: %s", game_id, exc)
    finally:
        with _lock:
            _preparing.discard(game_id)
            _progress.pop(game_id, None)
