"""
Game library: scans download_dir + individual game_paths from config.
Central in-memory registry used by the API layer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from deckdrop.core import game as game_mod
from deckdrop.core.config import Config
from deckdrop.core.game import GameInfo

log = logging.getLogger(__name__)


class Library:
    def __init__(self) -> None:
        self._games: dict[str, GameInfo] = {}  # id → GameInfo

    # -- loading --

    def reload(
        self,
        cfg: Config,
        *,
        exclude_paths: frozenset[Path] | None = None,
    ) -> None:
        """Scan all configured paths and refresh the in-memory library."""
        found: dict[str, GameInfo] = {}
        excluded = exclude_paths or frozenset()

        # 1. Scan every subdirectory of download_dir
        download_dir = cfg.download_dir
        if download_dir.exists():
            for subdir in sorted(download_dir.iterdir()):
                if subdir.is_dir() and subdir.resolve() not in excluded:
                    info = game_mod.load_from_path(subdir)
                    if info:
                        info.available = True
                        found[info.id] = info

        # 2. Individual game paths
        for gpath in cfg.game_paths:
            info = game_mod.load_from_path(gpath)
            if info:
                info.available = gpath.exists()
                found[info.id] = info
            elif gpath.is_dir():
                # Directory exists but no toml → caller must run wizard, skip for now
                pass

        self._games = found

    def all(self) -> list[GameInfo]:
        return list(self._games.values())

    def get(self, game_id: str) -> GameInfo | None:
        return self._games.get(game_id)

    def add(self, info: GameInfo) -> None:
        self._games[info.id] = info

    def remove(self, game_id: str) -> bool:
        if game_id in self._games:
            del self._games[game_id]
            return True
        return False

    def needs_wizard(self, path: Path) -> bool:
        """True if path is a directory without a deckdrop.toml."""
        return path.is_dir() and not (path / game_mod.TOML_FILENAME).exists()


def relink_game_id(cfg: Config, library: Library, game: GameInfo, new_id: str) -> bool:
    """Fix a locally-downloaded game whose ID doesn't match the host's (legacy bug).

    Rewrites the game's own ID in its deckdrop.toml, renames the cached
    .torrent and the local content-tracker state to the new ID (best effort,
    only if the target doesn't already exist), and re-indexes it in the
    library. Returns True if the ID was actually changed.
    """
    old_id = game.id
    if old_id == new_id:
        return False

    game.id = new_id
    try:
        game_mod.save(game)
    except OSError as exc:
        log.warning("Could not save relinked game %s: %s", game.name, exc)
        game.id = old_id
        return False

    old_torrent = cfg.torrent_cache / f"{old_id}.torrent"
    new_torrent = cfg.torrent_cache / f"{new_id}.torrent"
    if old_torrent.is_file() and not new_torrent.is_file():
        try:
            old_torrent.rename(new_torrent)
        except OSError as exc:
            log.warning("Could not rename cached torrent for %s: %s", game.name, exc)

    old_state = cfg.content_state_dir / f"{old_id}.json"
    new_state = cfg.content_state_dir / f"{new_id}.json"
    if old_state.is_file() and not new_state.is_file():
        try:
            old_state.rename(new_state)
        except OSError as exc:
            log.warning("Could not rename content state for %s: %s", game.name, exc)

    library.remove(old_id)
    library.add(game)
    log.info("Relinked game %s: %s -> %s (legacy ID fix)", game.name, old_id, new_id)
    return True
