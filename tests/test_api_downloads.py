"""Downloads API: list, cancel; no-libtorrent path."""

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core.library import Library
from deckdrop.network.peer_registry import PeerRegistry


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.user_name = "testuser"
    cfg_mod.save(cfg)
    app_state.init(cfg, Library(), PeerRegistry(), transfer=None)
    return TestClient(create_app())


def test_list_downloads_empty(client):
    r = client.get("/api/downloads")
    assert r.status_code == 200
    assert r.json() == []


def test_start_download_no_libtorrent(client):
    r = client.post("/api/download", json={"peer_id": "p1", "game_id": "g1"})
    assert r.status_code == 503


def test_cancel_download_no_libtorrent(client):
    r = client.delete("/api/downloads/xyz")
    assert r.status_code == 503


def test_start_download_unknown_peer(tmp_path, monkeypatch):
    """With a mock transfer, unknown peer returns 404."""
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)

    mock_transfer = object()  # not None, but registry has no peer
    app_state.init(cfg, Library(), PeerRegistry(), transfer=mock_transfer)
    c = TestClient(create_app())

    r = c.post("/api/download", json={"peer_id": "unknown", "game_id": "g1"})
    assert r.status_code == 404


def test_peers_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)
    registry = PeerRegistry()
    registry.upsert_sync("p1", "Alice", "192.168.1.2", 7373)
    app_state.init(cfg, Library(), registry)
    c = TestClient(create_app())

    r = c.get("/api/peers")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["peer_id"] == "p1"
    assert data[0]["name"] == "Alice"


def test_network_games_empty(client):
    r = client.get("/api/network/games")
    assert r.status_code == 200
    assert r.json() == []


def test_peer_games_not_found(client):
    r = client.get("/api/peers/nobody/games")
    assert r.status_code == 404
