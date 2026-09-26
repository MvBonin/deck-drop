"""
In-memory registry of known peers discovered via mDNS.
Fetches and caches each peer's game list on discovery.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

import httpx

from deckdrop.api.websocket import broadcast
from deckdrop.core import game as game_mod
from deckdrop.core.comments import Comment, load_comments, merge_comments, save_comments

log = logging.getLogger(__name__)

GAME_FETCH_TIMEOUT = 3.0  # seconds
GAMES_REFRESH_INTERVAL = 30.0  # seconds – poll peer game lists
GAMES_REFRESH_FAST = 3.0  # while any peer game is not ready to share


@dataclass
class PeerEntry:
    peer_id: str
    name: str
    address: str
    port: int
    last_seen: float = field(default_factory=time.monotonic)
    games: list[dict] = field(default_factory=list)
    online: bool = True


def _peer_http_url(address: str, port: int, path: str) -> str:
    host = address
    if ":" in address and not address.startswith("["):
        host = f"[{address}]"
    return f"http://{host}:{port}{path}"


class PeerRegistry:
    def __init__(self) -> None:
        self._peers: dict[str, PeerEntry] = {}  # peer_id → PeerEntry
        self._loop: asyncio.AbstractEventLoop | None = None
        self._refresh_task: asyncio.Task | None = None
        self._library = None  # set via set_library()

    def set_library(self, library: object) -> None:
        self._library = library

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the uvicorn event loop (required for mDNS thread callbacks)."""
        self._loop = loop

    def start_refresh_loop(self, interval: float = GAMES_REFRESH_INTERVAL) -> None:
        """Periodically re-fetch game lists from all online peers."""
        if self._refresh_task is not None and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(self._refresh_loop(interval))
        log.info("Peer game-list refresh every %.0fs", interval)

    def stop_refresh_loop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None

    _OFFLINE_TTL = 86400.0  # 24h

    def _evict_stale_peers(self) -> None:
        cutoff = time.monotonic() - self._OFFLINE_TTL
        stale = [pid for pid, e in self._peers.items() if not e.online and e.last_seen < cutoff]
        for pid in stale:
            log.debug("Evicting stale offline peer %s", pid)
            del self._peers[pid]

    def _needs_fast_refresh(self) -> bool:
        for entry in self._peers.values():
            if not entry.online:
                continue
            for game in entry.games:
                if game.get("content_state") == "modified":
                    continue
                if not game.get("has_torrent") and not game.get("torrent_prep_error"):
                    return True
        return False

    async def _refresh_loop(self, interval: float) -> None:
        while True:
            wait = GAMES_REFRESH_FAST if self._needs_fast_refresh() else interval
            await asyncio.sleep(wait)
            try:
                await self.refresh_all_online()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Periodic peer refresh failed: %s", exc)
            self._evict_stale_peers()

    async def refresh_all_online(self) -> None:
        """Re-fetch /api/games from every online peer."""
        online = [p for p in self._peers.values() if p.online]
        if not online:
            return
        await asyncio.gather(
            *(self._fetch_games(p.peer_id, p.address, p.port, is_new_peer=False) for p in online),
            return_exceptions=True,
        )

    def _schedule(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Schedule a coroutine from sync code (possibly a non-async thread)."""
        loop = self._loop
        if loop is not None:
            asyncio.run_coroutine_threadsafe(coro, loop)
            return
        try:
            running = asyncio.get_running_loop()
            running.create_task(coro)
        except RuntimeError:
            log.warning("No event loop available; skipped background task")

    # -- Called from discovery callbacks (sync context, zeroconf thread) --

    def upsert_sync(self, peer_id: str, name: str, address: str, port: int) -> None:
        """Add or refresh a peer. Schedules async game-list fetch."""
        old_address: str | None = None
        is_new_peer = peer_id not in self._peers
        if peer_id in self._peers:
            entry = self._peers[peer_id]
            old_address = entry.address
            entry.last_seen = time.monotonic()
            entry.online = True
            entry.name = name
            entry.address = address
            entry.port = port
        else:
            self._peers[peer_id] = PeerEntry(peer_id=peer_id, name=name, address=address, port=port)

        if old_address and old_address != address:
            self._schedule(self._sync_peer_address(peer_id, address))

        self._schedule(self._fetch_games(peer_id, address, port, is_new_peer=is_new_peer))

    def remove(self, peer_id: str) -> None:
        entry = self._peers.get(peer_id)
        if entry:
            entry.online = False
            log.info("Peer offline: %s (%s)", entry.name, peer_id)
            self._schedule(broadcast("peer_offline", {"peer_id": peer_id}))

    # -- Async version for direct await usage --

    async def upsert(self, peer_id: str, name: str, address: str, port: int) -> None:
        self.upsert_sync(peer_id, name, address, port)
        await self._fetch_games(peer_id, address, port, is_new_peer=True)

    @staticmethod
    def _games_changed(old: list[dict], new: list[dict]) -> bool:
        if len(old) != len(new):
            return True
        old_by_id = {g.get("id"): g for g in old if g.get("id")}
        new_by_id = {g.get("id"): g for g in new if g.get("id")}
        if set(old_by_id) != set(new_by_id):
            return True
        for gid, ng in new_by_id.items():
            og = old_by_id.get(gid)
            if not og:
                return True
            for key in (
                "has_torrent",
                "torrent_preparing",
                "torrent_prep_error",
                "info_hash",
                "content_hash",
                "content_state",
                "revision",
                "version_label",
            ):
                if og.get(key) != ng.get(key):
                    return True
        return False

    def trigger_refresh(self) -> None:
        """Ask all online peers for fresh game lists (e.g. after local torrent rebuild)."""
        self._schedule(self.refresh_all_online())

    async def _sync_peer_address(self, peer_id: str, address: str) -> None:
        from deckdrop.api import state as app_state

        transfer = app_state.get().transfer
        if transfer is not None and hasattr(transfer, "update_peer_address"):
            transfer.update_peer_address(peer_id, address)
            log.info("Peer %s address updated to %s for active downloads", peer_id[:8], address)

    async def _fetch_games(
        self,
        peer_id: str,
        address: str,
        port: int,
        *,
        is_new_peer: bool = False,
    ) -> None:
        url = _peer_http_url(address, port, "/api/games")
        try:
            async with httpx.AsyncClient(timeout=GAME_FETCH_TIMEOUT) as client:
                r = await client.get(url)
                r.raise_for_status()
                games = r.json()
        except Exception as exc:
            log.warning("Could not fetch games from %s:%s – %s", address, port, exc)
            games = []
            # First contact often races mDNS/API – retry once quickly
            if is_new_peer:
                await asyncio.sleep(0.5)
                try:
                    async with httpx.AsyncClient(timeout=GAME_FETCH_TIMEOUT) as client:
                        r = await client.get(url)
                        r.raise_for_status()
                        games = r.json()
                except Exception as retry_exc:
                    log.warning("Game fetch retry failed for %s:%s – %s", address, port, retry_exc)
                    games = []

        entry = self._peers.get(peer_id)
        if not entry:
            return

        changed = self._games_changed(entry.games, games)
        entry.games = games
        entry.last_seen = time.monotonic()
        log.debug("Fetched %d games from %s", len(games), entry.name)

        if self._library is not None and games:
            await self._sync_from_peer(peer_id, games, address, port)

        if is_new_peer:
            await broadcast(
                "peer_online",
                {
                    "peer_id": peer_id,
                    "name": entry.name,
                    "address": address,
                    "port": port,
                    "game_count": len(games),
                },
            )
        elif changed:
            await broadcast(
                "peer_games_updated",
                {
                    "peer_id": peer_id,
                    "name": entry.name,
                    "game_count": len(games),
                },
            )

    def _find_legacy_match(self, peer_id: str, rg: dict, remote_ids: set[str]) -> Any | None:
        """Find a previously-downloaded game whose ID doesn't match this peer's ID for it.

        Fixes the legacy bug where a download used to get a freshly minted ID
        instead of the host's – see docs/plans/game-updates.md "1.4".
        """
        name = rg.get("name")
        size = int(rg.get("size_bytes") or 0)
        for local in self._library.all():
            if local.origin.peer_id != peer_id:
                continue
            if local.name != name:
                continue
            if abs(local.size_bytes - size) >= 1024 * 1024:
                continue
            if local.id in remote_ids:
                continue
            return local
        return None

    async def _sync_from_peer(
        self, peer_id: str, remote_games: list[dict], address: str, port: int
    ) -> None:
        """Sync metadata and comments for games we share with this peer."""
        remote_ids = {g.get("id") for g in remote_games if g.get("id")}
        for rg in remote_games:
            game_id = rg.get("id")
            if not game_id:
                continue
            local = self._library.get(game_id)
            if not local:
                legacy = self._find_legacy_match(peer_id, rg, remote_ids)
                if legacy is not None:
                    from deckdrop.api import state as app_state
                    from deckdrop.core.library import relink_game_id

                    try:
                        cfg = app_state.get().cfg
                    except RuntimeError:
                        cfg = None
                    if cfg is not None:
                        relink_game_id(cfg, self._library, legacy, game_id)
                        local = self._library.get(game_id)
            if not local:
                continue

            # Metadata sync: apply remote edits when their version is newer
            remote_version = rg.get("version", 0)
            if remote_version > local.version:
                for attr, key in [
                    ("name", "name"),
                    ("platform", "platform"),
                    ("description", "description"),
                    ("launch_exe", "launch_exe"),
                ]:
                    if key in rg:
                        setattr(local, attr, rg[key])
                if "steam_app_id" in rg:
                    local.steam.app_id = rg["steam_app_id"]
                local.steam.launch_args = rg.get("launch_args", local.steam.launch_args)
                local.steam.runner = rg.get("runner", local.steam.runner)
                local.version = remote_version
                local.updated_at = rg.get("updated_at", local.updated_at)
                local.updated_by = rg.get("updated_by", local.updated_by)
                game_mod.save(local)
                log.info("Metadata synced for game %s (v%d from peer)", game_id, remote_version)

            # Same files, but the host named the revision (e.g. "1.0.5") after
            # this copy was taken. Fill an empty local label so My Games matches.
            remote_label = (rg.get("version_label") or "").strip()
            remote_hash = rg.get("content_hash") or ""
            if (
                remote_label
                and remote_hash
                and remote_hash == local.content.content_hash
                and not (local.content.version_label or "").strip()
            ):
                local.content.version_label = remote_label
                remote_note = (rg.get("version_note") or "").strip()
                if remote_note and not local.content.note:
                    local.content.note = remote_note
                game_mod.save(local)
                log.info("Version label synced for game %s (%s)", game_id, remote_label)

            # Comment sync: fetch remote comments and merge
            await self._sync_comments(game_id, local, address, port)

    async def _sync_comments(
        self, game_id: str, local_game: object, address: str, port: int
    ) -> None:
        url = _peer_http_url(address, port, f"/api/games/{game_id}/comments")
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(url)
                if r.status_code != 200:
                    return
            remote = [Comment(**c) for c in r.json()]
            local_comments = load_comments(local_game.path)
            merged = merge_comments(local_comments, remote)
            if len(merged) > len(local_comments):
                save_comments(local_game.path, merged)
                added = len(merged) - len(local_comments)
                log.debug("Merged %d new comment(s) for game %s", added, game_id)
        except Exception as exc:
            log.debug("Comment sync failed for %s: %s", game_id, exc)

    # -- Queries --

    def all(self) -> list[PeerEntry]:
        return [p for p in self._peers.values() if p.online]

    def get(self, peer_id: str) -> PeerEntry | None:
        return self._peers.get(peer_id)

    def get_games(self, peer_id: str) -> list[dict]:
        entry = self._peers.get(peer_id)
        return entry.games if entry else []

    @staticmethod
    def _version_key(game: dict, peer_id: str) -> str:
        return game.get("content_hash") or game.get("info_hash") or f"legacy:{peer_id}"

    def all_network_games(self) -> list[dict]:
        """Games from all online peers, grouped by game id, then by version.

        See docs/plans/game-updates.md "4.2". Groups by the game's stable id
        (not name+size, which broke as soon as an update changed the size).
        Within a group, entries are further grouped by version_key
        (content_hash, falling back to info_hash, then a per-peer legacy key)
        so that several peers on the same content show up as one selectable
        version, while peers with a newer/older/unreleased revision show up
        as their own entries in `versions`.
        """
        groups: dict[str, list[dict]] = {}

        for entry in self._peers.values():
            if not entry.online:
                continue
            for game in entry.games:
                gid = game.get("id")
                if not gid:
                    continue
                groups.setdefault(gid, []).append(
                    {**game, "peer_id": entry.peer_id, "peer_name": entry.name}
                )

        local_games: dict[str, Any] = {}
        if self._library is not None:
            for g in self._library.all():
                local_games[g.id] = g

        result: list[dict] = []
        for game_id, entries in groups.items():
            by_version: dict[str, list[dict]] = {}
            for e in entries:
                vkey = self._version_key(e, e["peer_id"])
                by_version.setdefault(vkey, []).append(e)

            versions: list[dict] = []
            for vkey, es in by_version.items():
                first = es[0]
                shareable = bool(first.get("shareable", True))
                has_torrent = bool(first.get("has_torrent", False))
                seen_peers: set[str] = set()
                peers: list[dict] = []
                for e in es:
                    if e["peer_id"] in seen_peers:
                        continue
                    seen_peers.add(e["peer_id"])
                    peers.append({"peer_id": e["peer_id"], "peer_name": e["peer_name"]})
                versions.append(
                    {
                        "version_key": vkey,
                        "revision": first.get("revision", 1),
                        "version_label": first.get("version_label", ""),
                        "version_note": first.get("version_note", ""),
                        "created_by": first.get("created_by", ""),
                        "content_updated_by": first.get("content_updated_by", ""),
                        "content_updated_at": first.get("content_updated_at", ""),
                        "size_bytes": first.get("size_bytes", 0),
                        "info_hash": first.get("info_hash"),
                        "shareable": shareable,
                        "has_torrent": has_torrent,
                        "peers": peers,
                    }
                )

            versions.sort(
                key=lambda v: (v["revision"], v["content_updated_at"] or ""), reverse=True
            )

            shareable_versions = [v for v in versions if v["shareable"] and v["has_torrent"]]
            primary_version = shareable_versions[0] if shareable_versions else versions[0]
            primary_key = primary_version["version_key"]
            primary_entry = next(
                e for e in entries if self._version_key(e, e["peer_id"]) == primary_key
            )

            local = local_games.get(game_id)
            installed = local is not None
            local_revision = local.content.revision if local else None
            local_version_key: str | None = None
            if local is not None:
                local_version_key = local.content.content_hash or local.torrent.info_hash or None
            update_available = installed and any(
                v["shareable"] and v["has_torrent"] and v["revision"] > (local_revision or 0)
                for v in versions
            )

            peer_names: list[str] = []
            for e in entries:
                if e["peer_name"] not in peer_names:
                    peer_names.append(e["peer_name"])

            result.append(
                {
                    **primary_entry,
                    "versions": versions,
                    "version_count": len([v for v in versions if v["shareable"]]),
                    "peer_count": len(entries),
                    "peer_names": peer_names,
                    "installed": installed,
                    "local_revision": local_revision,
                    "local_version_key": local_version_key,
                    "update_available": update_available,
                }
            )
        return result

    def peers_for_version(self, game_id: str, version_key: str) -> list[PeerEntry]:
        """Online peers sharing `version_key` of `game_id` (shareable + has_torrent only)."""
        result: list[PeerEntry] = []
        seen: set[str] = set()
        for entry in self._peers.values():
            if not entry.online or entry.peer_id in seen:
                continue
            for game in entry.games:
                if game.get("id") != game_id:
                    continue
                if self._version_key(game, entry.peer_id) != version_key:
                    continue
                if not game.get("shareable", True) or not game.get("has_torrent"):
                    continue
                result.append(entry)
                seen.add(entry.peer_id)
                break
        return result

    def best_update_for(self, game_id: str, local_revision: int) -> dict | None:
        """The highest-revision shareable version of `game_id` newer than `local_revision`."""
        best: dict | None = None
        for entry in self._peers.values():
            if not entry.online:
                continue
            for game in entry.games:
                if game.get("id") != game_id:
                    continue
                if not game.get("shareable", True) or not game.get("has_torrent"):
                    continue
                rev = int(game.get("revision") or 1)
                if rev <= local_revision:
                    continue
                if best is None or rev > int(best.get("revision") or 1):
                    best = game
        return best
