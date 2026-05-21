"""GameInfo model + deckdrop.toml read/write."""

from __future__ import annotations

import secrets
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tomli_w

TOML_FILENAME = "deckdrop.toml"


@dataclass
class SteamInfo:
    app_id: int | None = None
    install_dir: str = ""
    launch_args: str = ""
    runner: str = ""


@dataclass
class TorrentInfo:
    info_hash: str = ""
    magnet: str = ""


@dataclass
class OriginInfo:
    """LAN peer this copy was downloaded from (empty if added locally)."""

    peer_id: str = ""
    peer_name: str = ""


@dataclass
class GameInfo:
    # Stable 8-char hex ID that never changes
    id: str
    name: str
    version: int
    added_at: str
    added_by: str
    updated_at: str
    updated_by: str
    size_bytes: int
    platform: str  # linux | windows | any
    path: Path  # local folder path (not persisted in toml)
    available: bool = True  # False when path doesn't exist on disk
    description: str = ""
    launch_exe: str = ""

    steam: SteamInfo = field(default_factory=SteamInfo)
    torrent: TorrentInfo = field(default_factory=TorrentInfo)
    origin: OriginInfo = field(default_factory=OriginInfo)
    # filename → blake2b hex hash
    files: dict[str, str] = field(default_factory=dict)

    @property
    def toml_path(self) -> Path:
        return self.path / TOML_FILENAME

    def to_dict(self) -> dict[str, Any]:
        game_block: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "added_at": self.added_at,
            "added_by": self.added_by,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "size_bytes": self.size_bytes,
            "platform": self.platform,
        }
        if self.description:
            game_block["description"] = self.description
        if self.launch_exe:
            game_block["launch_exe"] = self.launch_exe
        d: dict[str, Any] = {
            "game": game_block,
            "files": self.files,
        }
        steam = self.steam
        if steam.app_id or steam.install_dir or steam.launch_args or steam.runner:
            d["steam"] = {
                k: v
                for k, v in {
                    "app_id": steam.app_id,
                    "install_dir": steam.install_dir,
                    "launch_args": steam.launch_args,
                    "runner": steam.runner,
                }.items()
                if v
            }
        torrent = self.torrent
        if torrent.info_hash or torrent.magnet:
            d["torrent"] = {
                k: v
                for k, v in {
                    "info_hash": torrent.info_hash,
                    "magnet": torrent.magnet,
                }.items()
                if v
            }
        origin = self.origin
        if origin.peer_id or origin.peer_name:
            d["origin"] = {
                k: v
                for k, v in {
                    "peer_id": origin.peer_id,
                    "peer_name": origin.peer_name,
                }.items()
                if v
            }
        return d


def load_from_path(game_path: Path) -> GameInfo | None:
    """Load GameInfo from a folder that contains deckdrop.toml. Returns None if missing."""
    toml_path = game_path / TOML_FILENAME
    if not toml_path.exists():
        return None

    with toml_path.open("rb") as f:
        data = tomllib.load(f)

    g = data.get("game", {})
    steam_data = data.get("steam", {})
    torrent_data = data.get("torrent", {})
    origin_data = data.get("origin", {})

    return GameInfo(
        id=g.get("id", _new_id()),
        name=g.get("name", game_path.name),
        version=g.get("version", 1),
        added_at=g.get("added_at", _now()),
        added_by=g.get("added_by", ""),
        updated_at=g.get("updated_at", _now()),
        updated_by=g.get("updated_by", ""),
        size_bytes=g.get("size_bytes", 0),
        platform=g.get("platform", "any"),
        path=game_path,
        available=game_path.exists(),
        description=g.get("description", ""),
        launch_exe=g.get("launch_exe", ""),
        steam=SteamInfo(
            app_id=steam_data.get("app_id"),
            install_dir=steam_data.get("install_dir", ""),
            launch_args=steam_data.get("launch_args", ""),
            runner=steam_data.get("runner", ""),
        ),
        torrent=TorrentInfo(
            info_hash=torrent_data.get("info_hash", ""),
            magnet=torrent_data.get("magnet", ""),
        ),
        origin=OriginInfo(
            peer_id=origin_data.get("peer_id", ""),
            peer_name=origin_data.get("peer_name", ""),
        ),
        files=data.get("files", {}),
    )


def create_new(
    game_path: Path,
    name: str,
    added_by: str,
    platform: str = "any",
    steam_app_id: int | None = None,
) -> GameInfo:
    """Create a fresh GameInfo (no deckdrop.toml yet). Call save() afterwards."""
    now = _now()
    return GameInfo(
        id=_new_id(),
        name=name,
        version=1,
        added_at=now,
        added_by=added_by,
        updated_at=now,
        updated_by=added_by,
        size_bytes=0,
        platform=platform,
        path=game_path,
        available=True,
        steam=SteamInfo(app_id=steam_app_id),
    )


def save(game: GameInfo) -> None:
    game.path.mkdir(parents=True, exist_ok=True)
    with game.toml_path.open("wb") as f:
        tomli_w.dump(game.to_dict(), f)


def bump_version(game: GameInfo, updated_by: str) -> None:
    game.version += 1
    game.updated_at = _now()
    game.updated_by = updated_by
    save(game)


def _new_id() -> str:
    return secrets.token_hex(4)  # 8-char hex


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
