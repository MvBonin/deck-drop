"""Multi-instance scenario tests: two peers discovering, sharing, and syncing games.

Each test exercises real production classes end-to-end. The only thing mocked is the
HTTP transport layer — respx intercepts _fetch_games' httpx call and the side_effect
routes it through Peer B's real FastAPI ASGI app (via httpx.ASGITransport), so Peer B's
actual /api/games route runs, including its library.reload() call.
"""

from __future__ import annotations

import asyncio
import copy
from unittest.mock import MagicMock

import respx
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core import game as game_mod
from deckdrop.core.config import Config
from deckdrop.core.library import Library
from deckdrop.network.discovery import SERVICE_TYPE, _Listener
from deckdrop.network.peer_registry import PeerRegistry


def _make_service_info(peer_id: str, name: str, ip: str, port: int):
    """Mock ServiceInfo matching what _Listener._extract() reads."""
    info = MagicMock()
    info.properties = {
        b"peer_id": peer_id.encode(),
        b"name": name.encode(),
        b"version": b"2",
        b"port": str(port).encode(),
    }
    info.parsed_scoped_addresses.return_value = [ip]
    info.port = port
    return info


async def test_peer_a_discovers_peer_b_and_sees_games(peer_b_setup, asgi_forwarder):
    """_Listener.add_service → upsert_sync → _fetch_games hits real /api/games → games visible."""
    peer_b_id = peer_b_setup["peer_id"]
    peer_b_addr = peer_b_setup["address"]
    peer_b_port = peer_b_setup["port"]

    registry_a = PeerRegistry()
    listener = _Listener(
        own_peer_id="peer-a-id",
        on_found=registry_a.upsert_sync,
        on_lost=registry_a.remove,
    )

    mock_zc = MagicMock()
    mock_zc.get_service_info.return_value = _make_service_info(
        peer_b_id, "Bob", peer_b_addr, peer_b_port
    )

    with respx.mock:
        respx.get(f"http://{peer_b_addr}:{peer_b_port}/api/games").mock(side_effect=asgi_forwarder)
        # Discovery fires: listener parses the service and calls registry_a.upsert_sync()
        listener.add_service(mock_zc, SERVICE_TYPE, f"deckdrop-bob.{SERVICE_TYPE}")
        assert registry_a.get(peer_b_id) is not None  # peer registered synchronously

        # _fetch_games is called (the background task upsert_sync scheduled also runs here)
        await registry_a._fetch_games(peer_b_id, peer_b_addr, peer_b_port)

    games = registry_a.all_network_games()
    assert len(games) == 1
    assert games[0]["name"] == "Portal 2"
    assert games[0]["peer_id"] == peer_b_id
    assert games[0]["peer_name"] == "Bob"


async def test_peer_going_offline_excluded_from_network_games(peer_b_setup, asgi_forwarder):
    """remove() marks peer offline; all_network_games() excludes them; cached games preserved."""
    peer_b_id = peer_b_setup["peer_id"]
    peer_b_addr = peer_b_setup["address"]
    peer_b_port = peer_b_setup["port"]

    registry_a = PeerRegistry()
    with respx.mock:
        respx.get(f"http://{peer_b_addr}:{peer_b_port}/api/games").mock(side_effect=asgi_forwarder)
        await registry_a.upsert(peer_b_id, "Bob", peer_b_addr, peer_b_port)

    assert len(registry_a.all_network_games()) == 1

    # Simulate the on_lost discovery callback
    registry_a.remove(peer_b_id)
    await asyncio.sleep(0)  # drain the broadcast task remove() schedules

    assert registry_a.all_network_games() == []
    entry = registry_a.get(peer_b_id)
    assert entry is not None
    assert entry.online is False
    assert len(entry.games) == 1  # games cached on the entry, just hidden from network view


async def test_peer_b_library_change_visible_on_next_fetch(peer_b_setup, asgi_forwarder, make_game):
    """/api/games reloads the library on every call, so games added to disk appear on next fetch."""
    peer_b_id = peer_b_setup["peer_id"]
    peer_b_addr = peer_b_setup["address"]
    peer_b_port = peer_b_setup["port"]
    b_games_url = f"http://{peer_b_addr}:{peer_b_port}/api/games"

    registry_a = PeerRegistry()

    with respx.mock:
        respx.get(b_games_url).mock(side_effect=asgi_forwarder)
        await registry_a.upsert(peer_b_id, "Bob", peer_b_addr, peer_b_port)

    assert len(registry_a.get_games(peer_b_id)) == 1
    assert registry_a.get_games(peer_b_id)[0]["name"] == "Portal 2"

    # Peer B adds a second game to their download_dir on disk
    make_game(peer_b_setup["cfg"].download_dir, "Celeste", "bob")

    # Peer A re-fetches — /api/games calls library.reload() and returns both games
    with respx.mock:
        respx.get(b_games_url).mock(side_effect=asgi_forwarder)
        await registry_a._fetch_games(peer_b_id, peer_b_addr, peer_b_port)

    names = {g["name"] for g in registry_a.get_games(peer_b_id)}
    assert names == {"Portal 2", "Celeste"}


def test_two_library_instances_same_shared_directory(tmp_path, make_game):
    """Two Library objects on the same path see the same stable game ID and live version bumps."""
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()

    # Construct Config objects directly — no CONFIG_PATH file I/O needed
    data_a = copy.deepcopy(cfg_mod._DEFAULTS)
    data_a["user"]["peer_id"] = "peer-a-uuid"
    data_a["paths"]["download_dir"] = str(shared_dir)
    cfg_a = Config(data_a)

    data_b = copy.deepcopy(cfg_mod._DEFAULTS)
    data_b["user"]["peer_id"] = "peer-b-uuid"
    data_b["paths"]["download_dir"] = str(shared_dir)
    cfg_b = Config(data_b)

    make_game(shared_dir, "Celeste", "alice")

    lib_a = Library()
    lib_a.reload(cfg_a)
    lib_b = Library()
    lib_b.reload(cfg_b)

    assert len(lib_a.all()) == 1
    assert len(lib_b.all()) == 1

    # Both instances read the same deckdrop.toml, so they see the same stable ID
    game_id = lib_a.all()[0].id
    assert lib_b.all()[0].id == game_id

    assert lib_a.get(game_id).version == 1

    # Peer A bumps the version — writes to disk
    game_mod.bump_version(lib_a.get(game_id), "alice")

    # Peer B reloads and sees the new version
    lib_b.reload(cfg_b)
    assert lib_b.get(game_id).version == 2


async def test_full_discovery_to_network_games_endpoint(
    tmp_path, monkeypatch, peer_b_setup, asgi_forwarder
):
    """Discovery fires → registry populated → GET /api/network/games returns Peer B's games."""
    peer_b_id = peer_b_setup["peer_id"]
    peer_b_addr = peer_b_setup["address"]
    peer_b_port = peer_b_setup["port"]

    registry_a = PeerRegistry()
    listener = _Listener(
        own_peer_id="peer-a-id",
        on_found=registry_a.upsert_sync,
        on_lost=registry_a.remove,
    )

    mock_zc = MagicMock()
    mock_zc.get_service_info.return_value = _make_service_info(
        peer_b_id, "Bob", peer_b_addr, peer_b_port
    )

    # Phase 1: discovery + game fetch (state = Peer B from peer_b_setup fixture)
    with respx.mock:
        respx.get(f"http://{peer_b_addr}:{peer_b_port}/api/games").mock(side_effect=asgi_forwarder)
        listener.add_service(mock_zc, SERVICE_TYPE, f"deckdrop-bob.{SERVICE_TYPE}")
        await registry_a._fetch_games(peer_b_id, peer_b_addr, peer_b_port)

    # Phase 2: wire registry_a into Peer A's API and query the network games endpoint
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config_a.toml")
    cfg_a = cfg_mod.load()
    cfg_a.user_name = "Alice"
    cfg_mod.save(cfg_a)
    app_state.init(cfg_a, Library(), peer_registry=registry_a)
    client_a = TestClient(create_app())

    r = client_a.get("/api/network/games")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "Portal 2"
    assert data[0]["peer_id"] == peer_b_id
    assert data[0]["peer_name"] == "Bob"
