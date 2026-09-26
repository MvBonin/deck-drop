"""PeerRegistry: upsert, remove, game caching, network games."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

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
    registry.get("p1").games = [{"id": "abc", "name": "Celeste", "size_bytes": 100}]
    games = registry.all_network_games()
    assert len(games) == 1
    assert games[0]["peer_id"] == "p1"
    assert games[0]["peer_name"] == "Alice"
    assert games[0]["peer_count"] == 1


def test_all_network_games_groups_same_id(registry):
    """Games are grouped by stable id (Phase 4), not name+size."""
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.upsert_sync("p2", "Bob", "192.168.1.11", 7373)
    registry.get("p1").games = [
        {
            "id": "a1",
            "name": "Portal 2",
            "size_bytes": 500,
            "has_torrent": True,
            "content_hash": "hash1",
        }
    ]
    registry.get("p2").games = [
        {
            "id": "a1",
            "name": "Portal 2",
            "size_bytes": 500,
            "has_torrent": True,
            "content_hash": "hash1",
        }
    ]
    games = registry.all_network_games()
    assert len(games) == 1
    assert games[0]["peer_count"] == 2
    assert set(games[0]["peer_names"]) == {"Alice", "Bob"}
    assert games[0]["version_count"] == 1


def test_all_network_games_different_ids_not_grouped(registry):
    """Same name+size but a different id (e.g. two unrelated games) stay separate."""
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.upsert_sync("p2", "Bob", "192.168.1.11", 7373)
    registry.get("p1").games = [
        {"id": "a1", "name": "Portal 2", "size_bytes": 500, "has_torrent": True}
    ]
    registry.get("p2").games = [
        {"id": "b1", "name": "Portal 2", "size_bytes": 500, "has_torrent": False}
    ]
    games = registry.all_network_games()
    assert len(games) == 2


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
async def test_upsert_sync_from_other_thread_fetches_games(registry):
    """mDNS callbacks run outside the asyncio loop; upsert_sync must still fetch games."""
    loop = asyncio.get_running_loop()
    registry.bind_loop(loop)

    with respx.mock:
        respx.get("http://192.168.1.10:7373/api/games").mock(
            return_value=httpx.Response(200, json=[{"id": "g1", "name": "Portal 2"}])
        )

        def _discover_from_zeroconf_thread() -> None:
            registry.upsert_sync("p1", "Bob", "192.168.1.10", 7373)

        with ThreadPoolExecutor(max_workers=1) as pool:
            await loop.run_in_executor(pool, _discover_from_zeroconf_thread)
        await asyncio.sleep(0.05)

    assert registry.get_games("p1")[0]["name"] == "Portal 2"


@pytest.mark.asyncio
async def test_fetch_games_handles_timeout(registry):
    with respx.mock:
        respx.get("http://192.168.1.10:7373/api/games").mock(
            side_effect=httpx.ConnectTimeout("timeout")
        )
        await registry.upsert("p1", "Bob", "192.168.1.10", 7373)

    # Should not raise; games remain empty
    assert registry.get_games("p1") == []


@pytest.mark.asyncio
async def test_refresh_all_online_updates_games(registry):
    with respx.mock:
        route = respx.get("http://192.168.1.10:7373/api/games")
        route.mock(return_value=httpx.Response(200, json=[{"id": "g1", "name": "Portal 2"}]))
        await registry.upsert("p1", "Bob", "192.168.1.10", 7373)
        assert len(registry.get_games("p1")) == 1

        route.mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": "g1", "name": "Portal 2"},
                    {"id": "g2", "name": "Half-Life"},
                ],
            )
        )
        await registry.refresh_all_online()

    games = registry.get_games("p1")
    assert len(games) == 2


def test_games_changed_detects_new_game(registry):
    assert PeerRegistry._games_changed([], [{"id": "a"}]) is True
    assert PeerRegistry._games_changed([{"id": "a"}], [{"id": "a"}]) is False
    assert PeerRegistry._games_changed([{"id": "a"}], [{"id": "b"}]) is True


def test_games_changed_detects_has_torrent(registry):
    old = [{"id": "a", "has_torrent": False}]
    new = [{"id": "a", "has_torrent": True}]
    assert PeerRegistry._games_changed(old, new) is True


@pytest.mark.asyncio
async def test_sync_from_peer_relinks_legacy_id(tmp_path, monkeypatch, registry):
    """A previously-downloaded game with the old (wrong) ID gets relinked."""
    from deckdrop.api import state as app_state
    from deckdrop.core import config as cfg_mod
    from deckdrop.core import game as game_mod
    from deckdrop.core.library import Library

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.download_dir = tmp_path / "games"
    cfg_mod.save(cfg)

    game_dir = cfg.download_dir / "Stardew_Valley"
    game_dir.mkdir(parents=True)
    info = game_mod.create_new(game_dir, "Stardew Valley", added_by="deckuser")
    info.origin.peer_id = "peer1"
    info.origin.peer_name = "PC1"
    info.size_bytes = 1000
    game_mod.save(info)
    old_id = info.id

    lib = Library()
    lib.reload(cfg)
    app_state.init(cfg, lib)

    registry.set_library(lib)

    remote_game = {
        "id": "hostid123",
        "name": "Stardew Valley",
        "size_bytes": 1000,
        "version": 1,
    }

    with respx.mock:
        respx.get("http://192.168.1.10:7373/api/games/hostid123/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        await registry._sync_from_peer("peer1", [remote_game], "192.168.1.10", 7373)

    assert lib.get("hostid123") is not None
    assert lib.get(old_id) is None
