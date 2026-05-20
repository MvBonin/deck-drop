"""Downloads API: list, cancel; no-libtorrent path."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core.library import Library
from deckdrop.network.peer_registry import PeerEntry, PeerRegistry
from deckdrop.network.transfer import DownloadStatus


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
    assert "libtorrent" in r.json()["detail"]


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


def test_start_download_fetches_magnet_without_has_torrent(tmp_path, monkeypatch):
    """has_torrent=false must not block download; magnet is fetched from peer."""
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)

    registry = PeerRegistry()
    registry._peers["host"] = PeerEntry(
        peer_id="host",
        name="Host",
        address="192.168.1.10",
        port=7373,
        games=[{"id": "g1", "name": "Test Game", "has_torrent": False}],
    )

    mock_transfer = MagicMock()
    mock_transfer.start_download.return_value = "abcd1234"
    mock_transfer.get_status.return_value = DownloadStatus(
        id="abcd1234",
        game_id="g1",
        game_name="Test Game",
        peer_id="host",
        peer_name="Host",
        status="queued",
        progress=0.0,
        speed_bytes_sec=0,
        downloaded_bytes=0,
        total_bytes=0,
        num_peers=0,
    )

    app_state.init(cfg, Library(), registry, transfer=mock_transfer)
    c = TestClient(create_app())

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"magnet": "magnet:?xt=urn:btih:abc"}

    with patch("httpx.get", return_value=mock_resp) as mock_get:
        r = c.post("/api/download", json={"peer_id": "host", "game_id": "g1"})

    assert r.status_code == 202
    mock_get.assert_called_once()
    mock_transfer.start_download.assert_called_once()
    assert registry._peers["host"].games[0]["has_torrent"] is True


def test_download_out_accepts_float_rates_from_status(tmp_path, monkeypatch):
    """Regression: libtorrent rates are floats; response model must coerce to int."""
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)

    registry = PeerRegistry()
    registry._peers["host"] = PeerEntry(
        peer_id="host",
        name="Host",
        address="192.168.1.10",
        port=7373,
        games=[{"id": "g1", "name": "Test Game", "has_torrent": True}],
    )

    mock_transfer = MagicMock()
    mock_transfer.start_download.return_value = "abcd1234"
    mock_transfer.get_status.return_value = DownloadStatus(
        id="abcd1234",
        game_id="g1",
        game_name="Test Game",
        peer_id="host",
        peer_name="Host",
        status="downloading",
        progress=0.1,
        speed_bytes_sec=1234,  # int after _build_status fix
        downloaded_bytes=500,
        total_bytes=1000,
        num_peers=1,
    )

    app_state.init(cfg, Library(), registry, transfer=mock_transfer)
    c = TestClient(create_app())

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"magnet": "magnet:?xt=urn:btih:abc"}

    with patch("httpx.get", return_value=mock_resp):
        r = c.post("/api/download", json={"peer_id": "host", "game_id": "g1"})

    assert r.status_code == 202
    assert r.json()["speed_bytes_sec"] == 1234


def test_pause_resume_remove_download(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)

    mock_transfer = MagicMock()
    mock_transfer.pause_download.return_value = True
    mock_transfer.resume_download.return_value = True
    mock_transfer.retry_download.return_value = True
    mock_transfer.remove_download.return_value = True
    mock_transfer.get_status.return_value = DownloadStatus(
        id="dl1",
        game_id="g1",
        game_name="Game",
        peer_id="p1",
        peer_name="Host",
        status="paused",
        progress=0.5,
        speed_bytes_sec=0,
        downloaded_bytes=500,
        total_bytes=1000,
        num_peers=0,
        dest_path="/tmp/Game",
    )

    app_state.init(cfg, Library(), PeerRegistry(), transfer=mock_transfer)
    c = TestClient(create_app())

    r = c.post("/api/downloads/dl1/pause")
    assert r.status_code == 200
    mock_transfer.pause_download.assert_called_once_with("dl1")

    r = c.post("/api/downloads/dl1/resume")
    assert r.status_code == 200
    mock_transfer.resume_download.assert_called_once_with("dl1")

    r = c.post("/api/downloads/dl1/retry")
    assert r.status_code == 200

    r = c.delete("/api/downloads/dl1?delete_files=true")
    assert r.status_code == 204
    mock_transfer.remove_download.assert_called_once_with("dl1", delete_files=True)


def test_start_download_german_404_peer(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)
    app_state.init(cfg, Library(), PeerRegistry(), transfer=MagicMock())
    c = TestClient(create_app())

    r = c.post("/api/download", json={"peer_id": "missing", "game_id": "g1"})
    assert r.status_code == 404
    assert "nicht gefunden" in r.json()["detail"]
