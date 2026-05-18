"""
In-memory registry of known peers discovered via mDNS.
Fetches and caches each peer's game list on discovery.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from deckdrop.api.websocket import broadcast

log = logging.getLogger(__name__)

GAME_FETCH_TIMEOUT = 3.0  # seconds


@dataclass
class PeerEntry:
    peer_id: str
    name: str
    address: str
    port: int
    last_seen: float = field(default_factory=time.monotonic)
    games: list[dict] = field(default_factory=list)
    online: bool = True


class PeerRegistry:
    def __init__(self) -> None:
        self._peers: dict[str, PeerEntry] = {}  # peer_id → PeerEntry

    # -- Called from discovery callbacks (sync context) --

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

        # Fire-and-forget: fetch game list in the running event loop if available
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._fetch_games(peer_id, address, port))
        except RuntimeError:
            pass  # No event loop yet (e.g., during tests without async context)

    def remove(self, peer_id: str) -> None:
        entry = self._peers.get(peer_id)
        if entry:
            entry.online = False
            log.info("Peer offline: %s (%s)", entry.name, peer_id)
            asyncio.get_event_loop().create_task(broadcast("peer_offline", {"peer_id": peer_id}))

    # -- Async version for direct await usage --

    async def upsert(self, peer_id: str, name: str, address: str, port: int) -> None:
        self.upsert_sync(peer_id, name, address, port)
        await self._fetch_games(peer_id, address, port)

    async def _fetch_games(self, peer_id: str, address: str, port: int) -> None:
        url = f"http://{address}:{port}/api/games"
        try:
            async with httpx.AsyncClient(timeout=GAME_FETCH_TIMEOUT) as client:
                r = await client.get(url)
                r.raise_for_status()
                games = r.json()
        except Exception as exc:
            log.debug("Could not fetch games from %s:%s – %s", address, port, exc)
            games = []

        entry = self._peers.get(peer_id)
        if entry:
            entry.games = games
            log.debug("Fetched %d games from %s", len(games), entry.name)
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

    # -- Queries --

    def all(self) -> list[PeerEntry]:
        return [p for p in self._peers.values() if p.online]

    def get(self, peer_id: str) -> PeerEntry | None:
        return self._peers.get(peer_id)

    def get_games(self, peer_id: str) -> list[dict]:
        entry = self._peers.get(peer_id)
        return entry.games if entry else []

    def all_network_games(self) -> list[dict]:
        """All games from all online peers, with peer info injected."""
        result = []
        for entry in self._peers.values():
            if not entry.online:
                continue
            for game in entry.games:
                result.append({**game, "peer_id": entry.peer_id, "peer_name": entry.name})
        return result
