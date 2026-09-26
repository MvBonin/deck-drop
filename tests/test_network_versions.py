"""peer_registry.all_network_games / peers_for_version / best_update_for (Phase 4)."""

from __future__ import annotations

from types import SimpleNamespace

from deckdrop.network.peer_registry import PeerRegistry


def _game(revision, content_hash, updated_at, **extra):
    return {
        "id": "g1",
        "name": "Stardew Valley",
        "size_bytes": 1000,
        "has_torrent": True,
        "shareable": True,
        "revision": revision,
        "version_label": f"1.{revision}",
        "version_note": "",
        "created_by": "alice",
        "content_updated_by": "alice",
        "content_updated_at": updated_at,
        "content_hash": content_hash,
        "info_hash": "a" * 40,
        **extra,
    }


def test_three_peers_two_versions_grouped_and_sorted():
    registry = PeerRegistry()
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.upsert_sync("p2", "Bob", "192.168.1.11", 7373)
    registry.upsert_sync("p3", "Carol", "192.168.1.12", 7373)

    # p1 and p2 share the same (older) content_hash/revision; p3 published a newer one.
    registry.get("p1").games = [_game(1, "hash-old", "2026-09-01T00:00:00+00:00")]
    registry.get("p2").games = [_game(1, "hash-old", "2026-09-01T00:00:00+00:00")]
    registry.get("p3").games = [
        _game(
            2,
            "hash-new",
            "2026-09-20T00:00:00+00:00",
            content_updated_by="carol",
            info_hash="b" * 40,
        )
    ]

    games = registry.all_network_games()
    assert len(games) == 1
    game = games[0]
    assert game["peer_count"] == 3
    assert game["version_count"] == 2
    assert len(game["versions"]) == 2

    # Newest revision first.
    assert game["versions"][0]["revision"] == 2
    assert game["versions"][0]["version_key"] == "hash-new"
    assert {p["peer_name"] for p in game["versions"][0]["peers"]} == {"Carol"}

    assert game["versions"][1]["revision"] == 1
    assert game["versions"][1]["version_key"] == "hash-old"
    assert {p["peer_name"] for p in game["versions"][1]["peers"]} == {"Alice", "Bob"}

    # The recommended (primary) version is the newest shareable one.
    assert game["info_hash"] == "b" * 40


def test_update_available_when_local_revision_is_older():
    registry = PeerRegistry()
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.get("p1").games = [_game(2, "hash-new", "2026-09-20T00:00:00+00:00")]

    local_game = SimpleNamespace(
        id="g1",
        content=SimpleNamespace(revision=1, content_hash="hash-old"),
        torrent=SimpleNamespace(info_hash=""),
    )
    library = SimpleNamespace(all=lambda: [local_game])
    registry.set_library(library)

    games = registry.all_network_games()
    game = games[0]
    assert game["installed"] is True
    assert game["local_revision"] == 1
    assert game["update_available"] is True


def test_not_installed_when_no_local_game():
    registry = PeerRegistry()
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.get("p1").games = [_game(1, "hash-old", "2026-09-01T00:00:00+00:00")]
    registry.set_library(SimpleNamespace(all=lambda: []))

    games = registry.all_network_games()
    game = games[0]
    assert game["installed"] is False
    assert game["local_revision"] is None
    assert game["update_available"] is False


def test_unshareable_version_appears_but_is_not_selectable():
    registry = PeerRegistry()
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.get("p1").games = [_game(1, "hash-old", "2026-09-01T00:00:00+00:00", shareable=False)]

    games = registry.all_network_games()
    game = games[0]
    assert game["version_count"] == 0  # not shareable → doesn't count
    assert len(game["versions"]) == 1
    assert game["versions"][0]["shareable"] is False


def test_peers_for_version_filters_by_key_and_shareable():
    registry = PeerRegistry()
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.upsert_sync("p2", "Bob", "192.168.1.11", 7373)
    registry.get("p1").games = [_game(1, "hash-old", "2026-09-01T00:00:00+00:00")]
    registry.get("p2").games = [_game(1, "hash-old", "2026-09-01T00:00:00+00:00", shareable=False)]

    peers = registry.peers_for_version("g1", "hash-old")
    assert [p.peer_id for p in peers] == ["p1"]


def test_best_update_for_returns_highest_newer_revision():
    registry = PeerRegistry()
    registry.upsert_sync("p1", "Alice", "192.168.1.10", 7373)
    registry.upsert_sync("p2", "Bob", "192.168.1.11", 7373)
    registry.get("p1").games = [_game(2, "hash-2", "2026-09-10T00:00:00+00:00")]
    registry.get("p2").games = [_game(3, "hash-3", "2026-09-20T00:00:00+00:00")]

    best = registry.best_update_for("g1", local_revision=1)
    assert best is not None
    assert best["revision"] == 3

    assert registry.best_update_for("g1", local_revision=3) is None
