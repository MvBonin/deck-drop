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
class ContentInfo:
    """Content version, separate from `game.version` (metadata sync counter)."""

    revision: int = 1
    version_label: str = ""
    note: str = ""
    created_by: str = ""
    created_at: str = ""
    updated_by: str = ""
    updated_at: str = ""
    content_hash: str = ""
    ignore: list[str] = field(default_factory=list)


@dataclass
class HistoryEntry:
    revision: int
    version_label: str
    note: str
    by: str
    at: str
    content_hash: str


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
    # filename → size in bytes (new; empty means "not scanned yet")
    sizes: dict[str, int] = field(default_factory=dict)
    content: ContentInfo = field(default_factory=ContentInfo)
    history: list[HistoryEntry] = field(default_factory=list)

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
        content = self.content
        d["content"] = {
            "revision": content.revision,
            "version_label": content.version_label,
            "note": content.note,
            "created_by": content.created_by,
            "created_at": content.created_at,
            "updated_by": content.updated_by,
            "updated_at": content.updated_at,
            "content_hash": content.content_hash,
            "ignore": content.ignore,
        }
        if self.history:
            d["history"] = [
                {
                    "revision": h.revision,
                    "version_label": h.version_label,
                    "note": h.note,
                    "by": h.by,
                    "at": h.at,
                    "content_hash": h.content_hash,
                }
                for h in self.history
            ]
        if self.sizes:
            d["sizes"] = self.sizes
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
    content_data = data.get("content")
    history_data = data.get("history", [])

    added_by = g.get("added_by", "")
    added_at = g.get("added_at", _now())
    if content_data is None:
        # Legacy toml without [content] – defaults per docs/plans/game-updates.md.
        content = ContentInfo(
            revision=1,
            version_label="",
            note="",
            created_by=added_by,
            created_at=added_at,
            updated_by=added_by,
            updated_at=added_at,
            content_hash="",
            ignore=[],
        )
        history: list[HistoryEntry] = []
    else:
        content = ContentInfo(
            revision=content_data.get("revision", 1),
            version_label=content_data.get("version_label", ""),
            note=content_data.get("note", ""),
            created_by=content_data.get("created_by", added_by),
            created_at=content_data.get("created_at", added_at),
            updated_by=content_data.get("updated_by", added_by),
            updated_at=content_data.get("updated_at", added_at),
            content_hash=content_data.get("content_hash", ""),
            ignore=list(content_data.get("ignore", [])),
        )
        history = [
            HistoryEntry(
                revision=h.get("revision", 1),
                version_label=h.get("version_label", ""),
                note=h.get("note", ""),
                by=h.get("by", ""),
                at=h.get("at", ""),
                content_hash=h.get("content_hash", ""),
            )
            for h in history_data
        ]

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
        sizes=data.get("sizes", {}),
        content=content,
        history=history,
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
        content=ContentInfo(
            revision=1,
            created_by=added_by,
            created_at=now,
            updated_by=added_by,
            updated_at=now,
        ),
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


def manifest_dict(g: GameInfo) -> dict[str, Any]:
    """The JSON served by GET /api/games/{id}/manifest (Phase 4)."""
    return {
        "id": g.id,
        "name": g.name,
        "platform": g.platform,
        "steam_app_id": g.steam.app_id,
        "description": g.description,
        "launch_exe": g.launch_exe,
        "launch_args": g.steam.launch_args,
        "runner": g.steam.runner,
        "added_by": g.added_by,
        "added_at": g.added_at,
        "content": {
            "revision": g.content.revision,
            "version_label": g.content.version_label,
            "note": g.content.note,
            "created_by": g.content.created_by,
            "created_at": g.content.created_at,
            "updated_by": g.content.updated_by,
            "updated_at": g.content.updated_at,
            "content_hash": g.content.content_hash,
            "ignore": g.content.ignore,
        },
        "history": [
            {
                "revision": h.revision,
                "version_label": h.version_label,
                "note": h.note,
                "by": h.by,
                "at": h.at,
                "content_hash": h.content_hash,
            }
            for h in g.history
        ],
        "files": g.files,
        "sizes": g.sizes,
        "info_hash": g.torrent.info_hash,
    }


def apply_manifest(g: GameInfo, m: dict[str, Any], *, keep_local_meta: bool) -> None:
    """Apply a manifest fetched from a peer onto a (possibly fresh) GameInfo.

    Does not call save() – the caller decides when to persist.
    """
    content_data = m.get("content") or {}
    g.content = ContentInfo(
        revision=content_data.get("revision", 1),
        version_label=content_data.get("version_label", ""),
        note=content_data.get("note", ""),
        created_by=content_data.get("created_by", ""),
        created_at=content_data.get("created_at", ""),
        updated_by=content_data.get("updated_by", ""),
        updated_at=content_data.get("updated_at", ""),
        content_hash=content_data.get("content_hash", ""),
        ignore=list(content_data.get("ignore", [])),
    )
    g.history = [
        HistoryEntry(
            revision=h.get("revision", 1),
            version_label=h.get("version_label", ""),
            note=h.get("note", ""),
            by=h.get("by", ""),
            at=h.get("at", ""),
            content_hash=h.get("content_hash", ""),
        )
        for h in m.get("history", [])
    ]
    g.files = dict(m.get("files", {}))
    g.sizes = dict(m.get("sizes", {}))

    if not keep_local_meta:
        g.id = m.get("id", g.id)
        g.name = m.get("name", g.name)
        g.platform = m.get("platform", g.platform)
        if m.get("steam_app_id"):
            g.steam.app_id = m["steam_app_id"]
        g.description = m.get("description", g.description)
        g.launch_exe = m.get("launch_exe", g.launch_exe)
        g.added_by = m.get("added_by", g.added_by)
        g.added_at = m.get("added_at", g.added_at)


def _new_id() -> str:
    return secrets.token_hex(4)  # 8-char hex


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
