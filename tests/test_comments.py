"""Tests for comments storage, merge, and API endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core.comments import (
    Comment,
    load_comments,
    merge_comments,
    new_comment,
    save_comments,
)
from deckdrop.core.library import Library

# ── Unit tests: comments.py ───────────────────────────────────────────────────


def test_new_comment_fields():
    c = new_comment("Alice", "Great game!")
    assert c.author == "Alice"
    assert c.text == "Great game!"
    assert len(c.id) > 0
    assert "T" in c.created_at  # ISO timestamp


def test_save_and_load_roundtrip(tmp_path):
    c1 = new_comment("Alice", "Comment 1")
    c2 = new_comment("Bob", "Comment 2")
    save_comments(tmp_path, [c1, c2])

    loaded = load_comments(tmp_path)
    assert len(loaded) == 2
    assert loaded[0].author == "Alice"
    assert loaded[1].author == "Bob"


def test_load_empty_returns_empty_list(tmp_path):
    assert load_comments(tmp_path) == []


def test_merge_deduplicates_by_id():
    c1 = new_comment("Alice", "Hello")
    c2 = new_comment("Bob", "World")
    merged = merge_comments([c1], [c1, c2])  # c1 appears in both
    assert len(merged) == 2
    ids = {c.id for c in merged}
    assert c1.id in ids
    assert c2.id in ids


def test_merge_sorted_chronologically():
    older = Comment(id="a", author="A", text="first", created_at="2026-01-01T10:00:00+00:00")
    newer = Comment(id="b", author="B", text="second", created_at="2026-01-02T10:00:00+00:00")
    merged = merge_comments([newer], [older])
    assert merged[0].id == "a"
    assert merged[1].id == "b"


# ── API integration tests ─────────────────────────────────────────────────────


@pytest.fixture
def client_with_game(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.user_name = "testuser"
    # Put download_dir inside tmp_path so the library scanner finds subdirs
    cfg.download_dir = tmp_path / "games"
    cfg.download_dir.mkdir()
    cfg_mod.save(cfg)

    library = Library()
    library.reload(cfg)
    app_state.init(cfg, library)

    app = create_app()
    client = TestClient(app)

    # Add a game via the API so it ends up in the library
    game_dir = cfg.download_dir / "TestGame"
    game_dir.mkdir()
    r = client.post("/api/games", json={"path": str(game_dir), "name": "Test Game"})
    assert r.status_code == 201
    game_id = r.json()["id"]

    return client, game_id


def test_list_comments_empty(client_with_game):
    client, game_id = client_with_game
    r = client.get(f"/api/games/{game_id}/comments")
    assert r.status_code == 200
    assert r.json() == []


def test_post_comment(client_with_game):
    client, game_id = client_with_game
    r = client.post(f"/api/games/{game_id}/comments", json={"text": "Runs well on Proton 9!"})
    assert r.status_code == 201
    data = r.json()
    assert data["author"] == "testuser"
    assert data["text"] == "Runs well on Proton 9!"
    assert "id" in data
    assert "created_at" in data


def test_post_and_list_comments(client_with_game):
    client, game_id = client_with_game
    client.post(f"/api/games/{game_id}/comments", json={"text": "First!"})
    client.post(f"/api/games/{game_id}/comments", json={"text": "Second!"})

    r = client.get(f"/api/games/{game_id}/comments")
    comments = r.json()
    assert len(comments) == 2
    assert comments[0]["text"] == "First!"
    assert comments[1]["text"] == "Second!"


def test_post_empty_comment_rejected(client_with_game):
    client, game_id = client_with_game
    r = client.post(f"/api/games/{game_id}/comments", json={"text": "   "})
    assert r.status_code == 400


def test_comment_on_missing_game(client_with_game):
    client, _ = client_with_game
    r = client.get("/api/games/deadbeef/comments")
    assert r.status_code == 404


def test_get_peer_game_comments(tmp_path, monkeypatch):
    """Network view: local API proxies comments from a remote peer."""
    from unittest.mock import MagicMock, patch

    from deckdrop.network.peer_registry import PeerEntry, PeerRegistry

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config_a.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)

    game_id = "game-abc"
    peer_id = "peer-host"
    remote_comments = [
        {
            "id": "c1",
            "author": "Bob",
            "text": "Läuft super auf dem Deck!",
            "created_at": "2026-05-21T12:00:00+00:00",
        }
    ]

    registry = PeerRegistry()
    registry._peers[peer_id] = PeerEntry(
        peer_id=peer_id,
        name="Bob",
        address="192.168.1.20",
        port=7373,
        games=[{"id": game_id, "name": "Portal 2"}],
    )
    app_state.init(cfg, Library(), registry)
    client = TestClient(create_app())

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = remote_comments
    with patch("deckdrop.api.routes.peers.httpx.get", return_value=mock_resp):
        r = client.get(f"/api/peers/{peer_id}/games/{game_id}/comments")

    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["text"] == "Läuft super auf dem Deck!"
    assert data[0]["author"] == "Bob"
