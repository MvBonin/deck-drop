"""Shared fixtures for multi-instance scenario tests."""

from __future__ import annotations

import httpx
import pytest

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core import game as game_mod
from deckdrop.core.library import Library


@pytest.fixture
def make_game():
    """Factory: write a game to disk and return its GameInfo."""

    def _factory(parent_dir, name, added_by="test"):
        path = parent_dir / name.replace(" ", "_")
        path.mkdir(parents=True, exist_ok=True)
        info = game_mod.create_new(path, name=name, added_by=added_by)
        game_mod.save(info)
        return info

    return _factory


@pytest.fixture
def peer_b_setup(tmp_path, monkeypatch):
    """Build Peer B: isolated config, library seeded with Portal 2, real FastAPI app."""
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config_b.toml")
    cfg_b = cfg_mod.load()
    cfg_b.user_name = "Bob"
    games_dir = tmp_path / "peer_b_games"
    games_dir.mkdir()
    cfg_b.download_dir = games_dir
    cfg_mod.save(cfg_b)

    game_dir = games_dir / "Portal_2"
    game_dir.mkdir()
    portal = game_mod.create_new(game_dir, "Portal 2", "bob")
    game_mod.save(portal)

    lib_b = Library()
    lib_b.reload(cfg_b)
    app_state.init(cfg_b, lib_b)
    app_b = create_app()

    return {
        "cfg": cfg_b,
        "library": lib_b,
        "app": app_b,
        "peer_id": cfg_b.peer_id,
        "address": "192.168.1.20",
        "port": 7374,
    }


@pytest.fixture
def asgi_forwarder(peer_b_setup):
    """Async side_effect for respx that routes requests to Peer B's real ASGI app.

    This lets _fetch_games make a "real" HTTP call that hits Peer B's actual
    /api/games route (which calls library.reload() internally) without a live socket.
    """
    transport = httpx.ASGITransport(app=peer_b_setup["app"])
    peer_b_http = httpx.AsyncClient(transport=transport, base_url="http://test-peer-b")

    async def forward(request: httpx.Request) -> httpx.Response:
        return await peer_b_http.get(request.url.path)

    return forward
