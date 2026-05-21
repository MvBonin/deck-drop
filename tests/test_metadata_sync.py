"""Tests for new metadata fields, PATCH, and peer metadata sync."""

from __future__ import annotations

import pytest
import httpx
import respx
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core import game as game_mod
from deckdrop.core.library import Library
from deckdrop.network.peer_registry import PeerRegistry


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client_with_game(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.user_name = "testuser"
    cfg.download_dir = tmp_path / "games"
    cfg.download_dir.mkdir()
    cfg_mod.save(cfg)

    library = Library()
    library.reload(cfg)
    app_state.init(cfg, library)

    app = create_app()
    client = TestClient(app)

    game_dir = cfg.download_dir / "TestGame"
    game_dir.mkdir()
    r = client.post("/api/games", json={"path": str(game_dir), "name": "Test Game"})
    assert r.status_code == 201
    game_id = r.json()["id"]

    return client, game_id, library


# ── GameOut fields ────────────────────────────────────────────────────────────


def test_game_out_includes_new_fields(client_with_game):
    client, game_id, _ = client_with_game
    r = client.get(f"/api/games/{game_id}")
    assert r.status_code == 200
    data = r.json()
    assert "description" in data
    assert "launch_exe" in data
    assert "launch_args" in data
    assert "runner" in data


# ── PATCH with new fields ─────────────────────────────────────────────────────


def test_patch_description(client_with_game):
    client, game_id, _ = client_with_game
    r = client.patch(f"/api/games/{game_id}", json={"description": "A great game"})
    assert r.status_code == 200
    assert r.json()["description"] == "A great game"


def test_patch_launch_exe(client_with_game):
    client, game_id, _ = client_with_game
    r = client.patch(f"/api/games/{game_id}", json={"launch_exe": "/games/test/game.exe"})
    assert r.status_code == 200
    assert r.json()["launch_exe"] == "/games/test/game.exe"


def test_patch_launch_args(client_with_game):
    client, game_id, _ = client_with_game
    r = client.patch(f"/api/games/{game_id}", json={"launch_args": "--fullscreen"})
    assert r.status_code == 200
    assert r.json()["launch_args"] == "--fullscreen"


def test_patch_runner(client_with_game):
    client, game_id, _ = client_with_game
    r = client.patch(f"/api/games/{game_id}", json={"runner": "Proton 9.0"})
    assert r.status_code == 200
    assert r.json()["runner"] == "Proton 9.0"


def test_patch_all_new_fields_bumps_version(client_with_game):
    client, game_id, _ = client_with_game
    r1 = client.get(f"/api/games/{game_id}")
    v1 = r1.json()["version"]

    r2 = client.patch(f"/api/games/{game_id}", json={
        "description": "Nice game",
        "launch_exe": "/path/to/game",
        "launch_args": "--no-intro",
        "runner": "Proton GE",
    })
    assert r2.json()["version"] == v1 + 1


def test_metadata_persisted_to_toml(client_with_game):
    client, game_id, library = client_with_game
    client.patch(f"/api/games/{game_id}", json={
        "description": "My cool game",
        "launch_exe": "/games/cool.exe",
        "launch_args": "--dx12",
        "runner": "Proton 8",
    })
    game = library.get(game_id)
    assert game is not None
    loaded = game_mod.load_from_path(game.path)
    assert loaded.description == "My cool game"
    assert loaded.launch_exe == "/games/cool.exe"
    assert loaded.steam.launch_args == "--dx12"
    assert loaded.steam.runner == "Proton 8"


# ── Metadata sync via PeerRegistry ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_peer_metadata_sync_applies_higher_version(tmp_path):
    """When remote game has a higher version, local metadata should be updated."""
    games_dir = tmp_path / "games"
    games_dir.mkdir()
    game_dir = games_dir / "SharedGame"
    game_dir.mkdir()

    info = game_mod.create_new(game_dir, "Shared Game", "alice")
    info.description = "Old description"
    info.version = 2
    game_mod.save(info)

    class FakeConfig:
        download_dir = games_dir
        game_paths: list = []

    library = Library()
    library.reload(FakeConfig())

    registry = PeerRegistry()
    registry.set_library(library)

    remote_game = {
        "id": info.id,
        "name": "Shared Game",
        "version": 5,
        "description": "Updated by creator",
        "launch_exe": "/games/shared.exe",
        "launch_args": "--fullscreen",
        "runner": "Proton 9.0",
        "platform": "linux",
        "steam_app_id": None,
        "updated_at": "2026-05-21T12:00:00+00:00",
        "updated_by": "alice",
    }

    with respx.mock:
        respx.get(f"http://192.168.1.10:7373/api/games/{info.id}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        await registry._sync_from_peer([remote_game], "192.168.1.10", 7373)

    local = library.get(info.id)
    assert local.description == "Updated by creator"
    assert local.launch_exe == "/games/shared.exe"
    assert local.steam.launch_args == "--fullscreen"
    assert local.steam.runner == "Proton 9.0"
    assert local.version == 5


@pytest.mark.asyncio
async def test_peer_metadata_sync_ignores_older_version(tmp_path):
    """When remote game has a lower version, local metadata stays unchanged."""
    games_dir = tmp_path / "games"
    games_dir.mkdir()
    game_dir = games_dir / "LocalGame"
    game_dir.mkdir()

    info = game_mod.create_new(game_dir, "Local Game", "bob")
    info.description = "My local description"
    info.version = 7
    game_mod.save(info)

    class FakeConfig:
        download_dir = games_dir
        game_paths: list = []

    library = Library()
    library.reload(FakeConfig())

    registry = PeerRegistry()
    registry.set_library(library)

    remote_game = {
        "id": info.id,
        "name": "Local Game",
        "version": 3,  # older than local v7
        "description": "Stale description",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "updated_by": "alice",
    }

    with respx.mock:
        respx.get(f"http://192.168.1.10:7373/api/games/{info.id}/comments").mock(
            return_value=httpx.Response(200, json=[])
        )
        await registry._sync_from_peer([remote_game], "192.168.1.10", 7373)

    local = library.get(info.id)
    assert local.description == "My local description"
    assert local.version == 7
