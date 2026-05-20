"""Shared fixtures for multi-instance scenario tests and Playwright e2e tests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
import tomli_w

from deckdrop.api import deps as deps_mod
from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core import game as game_mod
from deckdrop.core.library import Library


@pytest.fixture(autouse=True)
def _allow_testclient_as_local(monkeypatch):
    """Make Starlette's TestClient host ('testclient') pass the local_only check."""
    monkeypatch.setattr(deps_mod, "_LOCAL_HOSTS", deps_mod._LOCAL_HOSTS | {"testclient"})


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


# ── Playwright live-server fixture ────────────────────────────────────────────


@pytest.fixture(scope="session")
def live_server_url(tmp_path_factory):
    """Start a real DeckDrop HTTP server for Playwright e2e tests.

    Uses DECKDROP_CONFIG env var to point the subprocess at a fresh tmp config,
    so it never touches the developer's real ~/.config/deckdrop/config.toml.
    Discovery (zeroconf) is the only startup step that may fail on CI; the
    lifespan wraps it in a try/except, so the server comes up regardless.
    """
    tmp = tmp_path_factory.mktemp("e2e_server")
    games_dir = tmp / "games"
    cache_dir = tmp / "cache"
    games_dir.mkdir()
    cache_dir.mkdir()

    # Find a free port
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    # Write a minimal config so the server starts with known onboarding state
    config_file = tmp / "config.toml"
    config_data = {
        "user": {
            "name": "E2EUser",
            "peer_id": "e2e-peer-id",
            "onboarding_complete": True,
        },
        "paths": {
            "download_dir": str(games_dir),
            "torrent_cache": str(cache_dir),
            "game_paths": [],
        },
        "network": {"port": port, "torrent_port": port + 1, "announce_interval": 30},
        "transfer": {
            "max_upload_speed": 0,
            "max_download_speed": 0,
            "max_connections": 50,
            "seed_after_download": True,
        },
    }
    with open(config_file, "wb") as fh:
        tomli_w.dump(config_data, fh)

    env = {**os.environ, "DECKDROP_CONFIG": str(config_file)}
    proc_args = [sys.executable, "-m", "deckdrop.main", "--headless", "--port", str(port)]
    proc = subprocess.Popen(
        proc_args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # Wait up to 10 s for the server to accept connections
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=0.5)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        pytest.skip("live server did not start in time")

    yield f"http://127.0.0.1:{port}"

    proc.terminate()
    proc.wait(timeout=5)
