"""Verify that local-only endpoints reject remote clients (403)
and that peer-accessible endpoints remain open."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import deps as deps_mod
from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core.library import Library

_REMOTE_ONLY = {"127.0.0.1", "::1", "localhost"}  # excludes "testclient"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.user_name = "alice"
    cfg_mod.save(cfg)
    app_state.init(cfg, Library())
    return TestClient(create_app())


@pytest.fixture
def remote_client(tmp_path, monkeypatch):
    """Client whose requests appear remote (403 expected on local-only endpoints)."""
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.user_name = "alice"
    cfg_mod.save(cfg)
    app_state.init(cfg, Library())
    # Remove "testclient" so the host check behaves as if the request is remote
    monkeypatch.setattr(deps_mod, "_LOCAL_HOSTS", _REMOTE_ONLY)
    return TestClient(create_app(), raise_server_exceptions=False)


# ── Peer-accessible endpoints: must work from remote ──────────────────────────

def test_get_games_open_to_peers(remote_client):
    r = remote_client.get("/api/games")
    assert r.status_code == 200


def test_get_status_open_to_peers(remote_client):
    r = remote_client.get("/api/status")
    assert r.status_code == 200


# ── Local-only endpoints: must return 403 from remote ─────────────────────────

@pytest.mark.parametrize("method,path,body", [
    ("GET",    "/api/settings",          None),
    ("PUT",    "/api/settings",          {"user_name": "evil"}),
    ("POST",   "/api/games",             {"path": "/tmp/game", "name": "Hack"}),
    ("PATCH",  "/api/games/deadbeef",    {"name": "Hack"}),
    ("DELETE", "/api/games/deadbeef",    None),
    ("GET",    "/api/downloads",         None),
    ("POST",   "/api/download",          {"peer_id": "x", "game_id": "y"}),
    ("DELETE", "/api/downloads/abc",     None),
    ("GET",    "/api/peers",             None),
    ("GET",    "/api/network/games",     None),
])
def test_local_only_endpoint_rejects_remote(remote_client, method, path, body):
    r = remote_client.request(method, path, json=body)
    assert r.status_code == 403, f"{method} {path} should return 403 for remote clients"


# ── Same endpoints must work from localhost ────────────────────────────────────

def test_settings_accessible_from_localhost(client):
    r = client.get("/api/settings")
    assert r.status_code == 200


def test_peers_accessible_from_localhost(client):
    r = client.get("/api/peers")
    assert r.status_code == 200


def test_downloads_accessible_from_localhost(client):
    r = client.get("/api/downloads")
    assert r.status_code == 200
