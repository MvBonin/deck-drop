"""GameInfo model + deckdrop.toml read/write."""

from __future__ import annotations

import logging
import re
import secrets
import shutil
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tomli_w

log = logging.getLogger(__name__)

TOML_FILENAME = "deckdrop.toml"
PLATFORMS = ("linux", "windows", "any")
# IDs end up in file names (torrent cache, content state) and URLs.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class InvalidGameToml(ValueError):
    """deckdrop.toml exists but can't be used; the game has to be added again."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


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


# Parsed deckdrop.toml per path, keyed by the file's stat. GET /api/games
# reloads every game; re-parsing a manifest with tens of thousands of file
# hashes each time made the answer take seconds while the disk was busy
# hashing, and peers gave up on it.
_toml_cache: dict[Path, tuple[tuple[int, int, int], dict[str, Any]]] = {}


def _read_toml(toml_path: Path) -> dict[str, Any] | None:
    try:
        st = toml_path.stat()
    except OSError:
        return None
    key = (st.st_mtime_ns, st.st_size, st.st_ino)
    cached = _toml_cache.get(toml_path)
    if cached is not None and cached[0] == key:
        return cached[1]
    try:
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidGameToml(toml_path, str(exc)) from None
    except UnicodeDecodeError:
        raise InvalidGameToml(toml_path, "not UTF-8 text") from None
    except OSError as exc:
        raise InvalidGameToml(toml_path, exc.strerror or str(exc)) from None
    if not isinstance(data.get("game"), dict):
        raise InvalidGameToml(toml_path, "missing [game] section")
    _toml_cache[toml_path] = (key, data)
    return data


def _str(v: Any) -> bool:
    return isinstance(v, str)


def _int(v: Any, minimum: int) -> int | None:
    """v as an int >= minimum ("3" counts), else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v.strip())
    return v if isinstance(v, int) and v >= minimum else None


def _sanitize(data: dict[str, Any], game_path: Path) -> list[str]:
    """Fix wrong types / missing values in a parsed deckdrop.toml in place.

    Returns what was repaired (empty if the file was fine). Keeps the game ID
    when it is usable, so peers still recognise the game.
    """
    fixes: list[str] = []

    def table(parent: dict[str, Any], key: str, label: str) -> dict[str, Any]:
        v = parent.get(key)
        if v is None:
            return {}
        if not isinstance(v, dict):
            fixes.append(f"[{label}] is not a table")
            parent.pop(key)
            return {}
        return v

    def text(t: dict[str, Any], key: str, label: str, default: str = "") -> None:
        if key in t and not _str(t[key]):
            fixes.append(f"{label} is not text")
            t[key] = default

    g = data["game"]
    if not (_str(g.get("id")) and _ID_RE.match(g["id"])):
        fixes.append("game.id missing or invalid")
        g["id"] = _new_id()
    if not (_str(g.get("name")) and g["name"].strip()):
        fixes.append("game.name missing")
        g["name"] = game_path.name
    for key, minimum, default in (("version", 1, 1), ("size_bytes", 0, 0)):
        if key in g:
            n = _int(g[key], minimum)
            if n is None:
                fixes.append(f"game.{key} is not a valid number")
                n = default
            g[key] = n
    if "platform" in g and g["platform"] not in PLATFORMS:
        fixes.append("game.platform is unknown")
        g["platform"] = "any"
    for key in ("added_at", "added_by", "updated_at", "updated_by", "description", "launch_exe"):
        text(g, key, f"game.{key}")

    steam = table(data, "steam", "steam")
    if "app_id" in steam:
        app_id = _int(steam["app_id"], 1)
        if app_id is None:
            fixes.append("steam.app_id is not a valid number")
            steam.pop("app_id")
        else:
            steam["app_id"] = app_id
    for key in ("install_dir", "launch_args", "runner"):
        text(steam, key, f"steam.{key}")
    torrent = table(data, "torrent", "torrent")
    for key in ("info_hash", "magnet"):
        text(torrent, key, f"torrent.{key}")
    origin = table(data, "origin", "origin")
    for key in ("peer_id", "peer_name"):
        text(origin, key, f"origin.{key}")

    files = table(data, "files", "files")
    bad = [k for k, v in files.items() if not _str(v)]
    if bad:
        fixes.append(f"{len(bad)} invalid file hash(es)")
        for k in bad:
            files.pop(k)
    sizes = table(data, "sizes", "sizes")
    bad = [k for k, v in sizes.items() if _int(v, 0) is None]
    if bad:
        fixes.append(f"{len(bad)} invalid file size(s)")
        for k in bad:
            sizes.pop(k)

    if "content" in data:
        content = table(data, "content", "content")
        if "content" in data:
            if "revision" in content and _int(content["revision"], 1) is None:
                fixes.append("content.revision is not a valid number")
                content["revision"] = 1
            for key in (
                "version_label",
                "note",
                "created_by",
                "created_at",
                "updated_by",
                "updated_at",
                "content_hash",
            ):
                text(content, key, f"content.{key}")
            ignore = content.get("ignore", [])
            if not (isinstance(ignore, list) and all(_str(x) for x in ignore)):
                fixes.append("content.ignore is not a list of text")
                content["ignore"] = (
                    [x for x in ignore if _str(x)] if isinstance(ignore, list) else []
                )

    history = data.get("history", [])
    if not isinstance(history, list):
        fixes.append("[[history]] is not a list")
        data.pop("history")
    else:
        kept = [h for h in history if isinstance(h, dict)]
        for h in kept:
            if "revision" in h and _int(h["revision"], 1) is None:
                h["revision"] = 1
                fixes.append("history.revision is not a valid number")
            for key in ("version_label", "note", "by", "at", "content_hash"):
                text(h, key, f"history.{key}")
        if len(kept) != len(history):
            fixes.append("invalid [[history]] entries")
            data["history"] = kept
    return fixes


def backup_dir() -> Path:
    """Where replaced/repaired deckdrop.toml files are kept (outside the game
    folder, so they don't end up in the torrent or the content hash)."""
    from deckdrop.core import config as cfg_mod

    return cfg_mod.CONFIG_PATH.parent / "toml-backups"


def backup_toml(toml_path: Path) -> Path | None:
    """Copy deckdrop.toml aside before overwriting it. None if that failed."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    dest = backup_dir() / f"{toml_path.parent.name}-{stamp}.toml"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(toml_path, dest)
    except OSError as exc:
        log.warning("Could not back up %s: %s", toml_path, exc)
        return None
    return dest


def load_from_path(game_path: Path) -> GameInfo | None:
    """Load GameInfo from a folder that contains deckdrop.toml. Returns None if missing.

    Fixable mistakes (wrong types, missing ID/name, …) are repaired and written
    back once; the original is kept in backup_dir(). Raises InvalidGameToml if
    the file can't be read at all.
    """
    toml_path = game_path / TOML_FILENAME
    data = _read_toml(toml_path)
    if data is None:
        return None
    fixes = _sanitize(data, game_path)

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

    info = GameInfo(
        id=g["id"],
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
        files=dict(data.get("files", {})),
        sizes=dict(data.get("sizes", {})),
        content=content,
        history=history,
    )
    if fixes:
        log.warning("Repaired %s: %s", toml_path, "; ".join(fixes))
        backup = backup_toml(toml_path)
        if backup is not None:
            try:
                save(info)
            except OSError as exc:
                log.warning("Could not write repaired %s: %s", toml_path, exc)
    return info


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
    # Two saves within one mtime tick (coarse on exFAT) must not look unchanged.
    _toml_cache.pop(game.toml_path, None)
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
    # Manifests come from peers and are not trusted: a relpath must resolve
    # inside the game folder, or it's dropped here – the one funnel both a
    # fresh download and an update go through – before it can ever reach a
    # stat()/hash_file()/open() call elsewhere (docs/plans/game-updates.md
    # "Stolperfallen": paths from peer manifests are untrusted).
    from deckdrop.core.content import safe_join

    raw_files = m.get("files", {}) or {}
    raw_sizes = m.get("sizes", {}) or {}
    g.files = {rel: h for rel, h in raw_files.items() if safe_join(g.path, rel) is not None}
    g.sizes = {rel: s for rel, s in raw_sizes.items() if safe_join(g.path, rel) is not None}

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
