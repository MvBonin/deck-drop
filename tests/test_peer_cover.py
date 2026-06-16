"""Network view: local API proxies a game cover image from a remote peer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core.library import Library
from deckdrop.network.peer_registry import PeerEntry, PeerRegistry


def _client_with_peer(tmp_path, monkeypatch, *, game_id="game-abc", peer_id="peer-host"):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)

    registry = PeerRegistry()
    registry._peers[peer_id] = PeerEntry(
        peer_id=peer_id,
        name="Bob",
        address="192.168.1.20",
        port=7373,
        games=[{"id": game_id, "name": "Portal 2"}],
    )
    app_state.init(cfg, Library(), registry)
    return TestClient(create_app()), peer_id, game_id


def test_get_peer_game_cover_success(tmp_path, monkeypatch):
    client, peer_id, game_id = _client_with_peer(tmp_path, monkeypatch)

    image_bytes = b"\x89PNG\r\n\x1a\n" + b"x" * 50
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.content = image_bytes
    mock_resp.headers = {"content-type": "image/png"}

    with patch("deckdrop.api.routes.peers.httpx.get", return_value=mock_resp):
        r = client.get(f"/api/peers/{peer_id}/games/{game_id}/cover")

    assert r.status_code == 200
    assert r.content == image_bytes
    assert r.headers["content-type"] == "image/png"


def test_get_peer_game_cover_remote_404(tmp_path, monkeypatch):
    client, peer_id, game_id = _client_with_peer(tmp_path, monkeypatch)

    response = httpx.Response(404, request=httpx.Request("GET", "http://x/cover"))
    err = httpx.HTTPStatusError("404", request=response.request, response=response)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock(side_effect=err)

    with patch("deckdrop.api.routes.peers.httpx.get", return_value=mock_resp):
        r = client.get(f"/api/peers/{peer_id}/games/{game_id}/cover")

    assert r.status_code == 404


def test_get_peer_game_cover_unknown_peer(tmp_path, monkeypatch):
    client, _peer_id, game_id = _client_with_peer(tmp_path, monkeypatch)

    r = client.get(f"/api/peers/does-not-exist/games/{game_id}/cover")
    assert r.status_code == 404


def test_get_peer_game_cover_unknown_game(tmp_path, monkeypatch):
    client, peer_id, _game_id = _client_with_peer(tmp_path, monkeypatch)

    r = client.get(f"/api/peers/{peer_id}/games/not-a-game/cover")
    assert r.status_code == 404
