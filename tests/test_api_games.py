"""Integration tests for the /api/games endpoints."""

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core.library import Library


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.user_name = "testuser"
    cfg_mod.save(cfg)

    library = Library()
    library.reload(cfg)
    app_state.init(cfg, library)

    app = create_app()
    return TestClient(app)


def test_list_games_empty(client):
    r = client.get("/api/games")
    assert r.status_code == 200
    assert r.json() == []


def test_add_game_wizard(client, tmp_path):
    game_dir = tmp_path / "MyGame"
    game_dir.mkdir()

    r = client.post("/api/games", json={"path": str(game_dir), "name": "My Game"})
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "My Game"
    assert data["available"] is True
    assert (game_dir / "deckdrop.toml").exists()


def test_add_game_existing_toml(client, tmp_path):
    from deckdrop.core import game as game_mod

    game_dir = tmp_path / "ExistingGame"
    game_dir.mkdir()
    info = game_mod.create_new(game_dir, name="Existing", added_by="someone")
    game_mod.save(info)

    r = client.post("/api/games", json={"path": str(game_dir)})
    assert r.status_code == 201
    assert r.json()["name"] == "Existing"


def test_add_game_no_toml_no_name(client, tmp_path):
    game_dir = tmp_path / "NoToml"
    game_dir.mkdir()
    r = client.post("/api/games", json={"path": str(game_dir)})
    assert r.status_code == 422


def test_get_game(client, tmp_path):
    game_dir = tmp_path / "G1"
    game_dir.mkdir()
    r = client.post("/api/games", json={"path": str(game_dir), "name": "G1"})
    game_id = r.json()["id"]

    r2 = client.get(f"/api/games/{game_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == game_id


def test_delete_game(client, tmp_path):
    game_dir = tmp_path / "G2"
    game_dir.mkdir()
    r = client.post("/api/games", json={"path": str(game_dir), "name": "G2"})
    game_id = r.json()["id"]

    r2 = client.delete(f"/api/games/{game_id}")
    assert r2.status_code == 204

    r3 = client.get(f"/api/games/{game_id}")
    assert r3.status_code == 404


def test_patch_game(client, tmp_path):
    game_dir = tmp_path / "G3"
    game_dir.mkdir()
    r = client.post("/api/games", json={"path": str(game_dir), "name": "Old Name"})
    game_id = r.json()["id"]

    r2 = client.patch(f"/api/games/{game_id}", json={"name": "New Name"})
    assert r2.status_code == 200
    assert r2.json()["name"] == "New Name"
