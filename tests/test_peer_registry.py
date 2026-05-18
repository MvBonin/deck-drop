"""PeerRegistry: upsert, remove, game caching, network games."""

from __future__ import annotations

import httpx
import pytest
import respx

from deckdrop.network.peer_registry import PeerRegistry


@pytest.fixture
def registry():
    return PeerRegistry()


def test_upsert_sync_adds_peer(registry):
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    peers = registry.all()
    assert len(peers) == 1
    assert peers[0].peer_id == "p1"
    assert peers[0].name == "Alice"


def test_upsert_sync_updates_existing(registry):
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.upsert_sync("p1", "Alice", "192.168.1.11", 7373)
    assert len(registry.all()) == 1
    assert registry.get("p1").address == "192.168.1.11"


def test_remove_marks_offline(registry):
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.remove("p1")
    assert registry.all() == []
    assert registry.get("p1").online is False


def test_get_unknown_peer(registry):
    assert registry.get("nobody") is None


def test_get_games_unknown_peer(registry):
    assert registry.get_games("nobody") == []


def test_all_network_games_empty(registry):
    assert registry.all_network_games() == []


def test_all_network_games_injects_peer_info(registry):
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.get("p1").games = [{"id": "abc", "name": "Celeste"}]
    games = registry.all_network_games()
    assert len(games) == 1
    assert games[0]["peer_id"] == "p1"
    assert games[0]["peer_name"] == "Alice"


def test_all_network_games_excludes_offline(registry):
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.get("p1").games = [{"id": "abc", "name": "Celeste"}]
    registry.remove("p1")
    assert registry.all_network_games() == []


@pytest.mark.asyncio
async def test_fetch_games_on_upsert(registry):
    with respx.mock:
        respx.get("http://192.168.1.10:7373/api/games").mock(
            return_value=httpx.Response(200, json=[{"id": "g1", "name": "Portal 2"}])
        )
        await registry.upsert("p1", "Bob", "192.168.1.10", 7373)

    games = registry.get_games("p1")
    assert len(games) == 1
    assert games[0]["name"] == "Portal 2"


@pytest.mark.asyncio
async def test_fetch_games_handles_timeout(registry):
    with respx.mock:
        respx.get("http://192.168.1.10:7373/api/games").mock(
            side_effect=httpx.ConnectTimeout("timeout")
        )
        await registry.upsert("p1", "Bob", "192.168.1.10", 7373)

    # Should not raise; games remain empty
    assert registry.get_games("p1") == []
