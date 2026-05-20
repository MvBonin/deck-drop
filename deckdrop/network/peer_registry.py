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

    def _needs_fast_refresh(self) -> bool:
        for entry in self._peers.values():
            if not entry.online:
                continue
            for game in entry.games:
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
        if peer_id in self._peers:
            entry = self._peers[peer_id]
            entry.last_seen = time.monotonic()
            entry.online = True
            entry.address = address
            entry.port = port
        else:
            self._peers[peer_id] = PeerEntry(peer_id=peer_id, name=name, address=address, port=port)

        self._schedule(self._fetch_games(peer_id, address, port, is_new_peer=True))

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
        old_ids = {g.get("id") for g in old}
        new_ids = {g.get("id") for g in new}
        return old_ids != new_ids

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

        entry = self._peers.get(peer_id)
        if not entry:
            return

        changed = self._games_changed(entry.games, games)
        entry.games = games
        entry.last_seen = time.monotonic()
        log.debug("Fetched %d games from %s", len(games), entry.name)

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

    # -- Queries --

    def all(self) -> list[PeerEntry]:
        return [p for p in self._peers.values() if p.online]

    def get(self, peer_id: str) -> PeerEntry | None:
        return self._peers.get(peer_id)

    def get_games(self, peer_id: str) -> list[dict]:
        entry = self._peers.get(peer_id)
        return entry.games if entry else []

    def all_network_games(self) -> list[dict]:
        """Games from all online peers, grouped by name+size with peer_count."""
        groups: dict[tuple[str, int], list[dict]] = {}

        for entry in self._peers.values():
            if not entry.online:
                continue
            for game in entry.games:
                key = (game.get("name", ""), int(game.get("size_bytes") or 0))
                groups.setdefault(key, []).append(
                    {**game, "peer_id": entry.peer_id, "peer_name": entry.name}
                )

        result: list[dict] = []
        for variants in groups.values():
            ready = [v for v in variants if v.get("has_torrent")]
            primary = ready[0] if ready else variants[0]
            peer_names = [v["peer_name"] for v in variants]
            result.append(
                {
                    **primary,
                    "peer_count": len(variants),
                    "peer_names": peer_names,
                }
            )
        return result
