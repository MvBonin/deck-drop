"""
Game library: scans download_dir + individual game_paths from config.
Central in-memory registry used by the API layer.
"""

from __future__ import annotations

from pathlib import Path

from deckdrop.core import game as game_mod
from deckdrop.core.config import Config
from deckdrop.core.game import GameInfo


class Library:
    def __init__(self) -> None:
        self._games: dict[str, GameInfo] = {}  # id → GameInfo

    # -- loading --

    def reload(self, cfg: Config) -> None:
        """Scan all configured paths and refresh the in-memory library."""
        found: dict[str, GameInfo] = {}

        # 1. Scan every subdirectory of download_dir
        download_dir = cfg.download_dir
        if download_dir.exists():
            for subdir in sorted(download_dir.iterdir()):
                if subdir.is_dir():
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
