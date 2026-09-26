"""GET /api/games/{id}/manifest + /torrent (Phase 4, public peer endpoints).

Also covers download-start with an unknown version_key (404) and starting a
download for an already-installed game (409).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import game as game_mod
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library


@pytest.fixture
def shareable_game(tmp_path, isolated_config, make_game):
    info = make_game(tmp_path, "Stardew Valley")
    (info.path / "bin.exe").write_bytes(b"x" * 100)
    info.torrent.magnet = "magnet:?xt=urn:btih:" + "a" * 40
    info.torrent.info_hash = "a" * 40
    game_mod.save(info)

    library = Library()
    library.add(info)
    tracker = ContentTracker(isolated_config, library)
    tracker.ensure_baseline(info.id)

    cache_path = isolated_config.torrent_cache / f"{info.id}.torrent"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(b"fake-torrent-bytes")

    app_state.init(isolated_config, library, content=tracker)
    client = TestClient(create_app())
    return client, info, tracker, isolated_config


def test_manifest_returns_content(shareable_game):
    client, info, _tracker, _cfg = shareable_game
    r = client.get(f"/api/games/{info.id}/manifest")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == info.id
    assert data["name"] == "Stardew Valley"
    assert "bin.exe" in data["files"]
    assert data["content"]["revision"] == 1
    assert data["info_hash"] == "a" * 40


def test_manifest_404_unknown_game(shareable_game):
    client, _info, _tracker, _cfg = shareable_game
    r = client.get("/api/games/doesnotexist/manifest")
    assert r.status_code == 404


def test_manifest_409_when_modified(shareable_game):
    client, info, _tracker, _cfg = shareable_game
    (info.path / "bin.exe").write_bytes(b"y" * 999)
    r = client.get(f"/api/games/{info.id}/manifest")
    assert r.status_code == 409


def test_torrent_returns_bytes(shareable_game):
    client, info, _tracker, _cfg = shareable_game
    r = client.get(f"/api/games/{info.id}/torrent")
    assert r.status_code == 200
    assert r.content == b"fake-torrent-bytes"
    assert r.headers["content-type"] == "application/x-bittorrent"


def test_torrent_409_when_modified(shareable_game):
    client, info, _tracker, _cfg = shareable_game
    (info.path / "bin.exe").write_bytes(b"y" * 999)
    r = client.get(f"/api/games/{info.id}/torrent")
    assert r.status_code == 409


def test_torrent_409_when_not_yet_prepared(shareable_game, monkeypatch):
    client, info, _tracker, cfg = shareable_game
    (cfg.torrent_cache / f"{info.id}.torrent").unlink()

    from deckdrop.core import torrent_prep

    monkeypatch.setattr(torrent_prep, "schedule_prepare", lambda game_id, **kw: None)
    r = client.get(f"/api/games/{info.id}/torrent")
    assert r.status_code == 409


# -- POST /api/download: version_key handling --


@pytest.fixture
def downloads_client(isolated_config, tmp_path, monkeypatch):
    from deckdrop.core import torrent as torrent_mod
    from deckdrop.network.transfer import TransferManager

    monkeypatch.setattr(torrent_mod, "lan_session", lambda port: MagicMock())
    transfer = TransferManager(isolated_config)
    library = Library()
    app_state.init(isolated_config, library, transfer=transfer)
    client = TestClient(create_app())
    return client


def test_download_start_unknown_version_key_404(downloads_client):
    s = app_state.get()
    s.peer_registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    s.peer_registry.get("p1").games = [
        {"id": "g1", "name": "Stardew Valley", "has_torrent": True, "shareable": True}
    ]

    r = downloads_client.post(
        "/api/download",
        json={"peer_id": "p1", "game_id": "g1", "version_key": "does-not-exist"},
    )
    assert r.status_code == 404


def test_download_start_already_installed_409(downloads_client, tmp_path, make_game):
    s = app_state.get()
    info = make_game(tmp_path, "Stardew Valley")
    s.library.add(info)
    s.peer_registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    s.peer_registry.get("p1").games = [
        {"id": info.id, "name": "Stardew Valley", "has_torrent": True, "shareable": True}
    ]

    r = downloads_client.post(
        "/api/download",
        json={"peer_id": "p1", "game_id": info.id},
    )
    assert r.status_code == 409
