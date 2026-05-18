"""Settings + status API endpoints."""

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
    cfg.user_name = "alice"
    cfg_mod.save(cfg)
    app_state.init(cfg, Library())
    return TestClient(create_app())


def test_get_settings(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert data["user_name"] == "alice"
    assert data["port"] == 7373
    assert data["seed_after_download"] is True


def test_update_username(client):
    r = client.put("/api/settings", json={"user_name": "bob"})
    assert r.status_code == 200
    assert r.json()["user_name"] == "bob"


def test_update_download_dir(client, tmp_path):
    new_dir = str(tmp_path / "NewGames")
    r = client.put("/api/settings", json={"download_dir": new_dir})
    assert r.status_code == 200
    assert r.json()["download_dir"] == new_dir


def test_status_endpoint(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "alice"
    assert data["peer_id"]
    assert data["version"] == "2.0.0"
    assert data["onboarding_complete"] is False


def test_peers_empty(client):
    r = client.get("/api/peers")
    assert r.status_code == 200
    assert r.json() == []


def test_downloads_empty(client):
    r = client.get("/api/downloads")
    assert r.status_code == 200
    assert r.json() == []


def test_download_not_implemented(client):
    r = client.post("/api/download", json={"peer_id": "abc", "game_id": "xyz"})
    assert r.status_code == 501
