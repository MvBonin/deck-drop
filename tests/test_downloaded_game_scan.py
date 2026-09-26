"""Correction A: downloaded games are tracked/scanned like local ones.

Per docs/plans/game-updates.md decision 2, anyone (including the Steam Deck)
may publish an update and downloaded copies are seeded too, so
`POST /api/games/scan` (and the startup/periodic scan) must not skip games
with an `origin` (peer_id/peer_name) set.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import game as game_mod
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library


def _downloaded_game(tmp_path, make_game):
    info = make_game(tmp_path, "Portal 2", added_by="alice")
    (info.path / "game.bin").write_bytes(b"x" * 100)
    info.origin.peer_id = "peer1"
    info.origin.peer_name = "PC1"
    game_mod.save(info)
    return info


def test_scan_all_games_includes_downloaded_game(isolated_config, tmp_path, make_game):
    info = _downloaded_game(tmp_path, make_game)

    library = Library()
    library.add(info)
    tracker = ContentTracker(isolated_config, library)
    tracker.ensure_baseline(info.id)

    app_state.init(isolated_config, library, content=tracker)
    client = TestClient(create_app())

    r = client.post("/api/games/scan")
    assert r.status_code == 200
    assert r.json() == {info.id: "clean"}


def test_modified_downloaded_game_becomes_modified(isolated_config, tmp_path, make_game):
    info = _downloaded_game(tmp_path, make_game)

    library = Library()
    library.add(info)
    tracker = ContentTracker(isolated_config, library)
    tracker.ensure_baseline(info.id)
    assert tracker.state(info.id) == "clean"

    # Simulate the peer having patched the file locally on this (downloaded) copy.
    (info.path / "game.bin").write_bytes(b"y" * 500)

    app_state.init(isolated_config, library, content=tracker)
    client = TestClient(create_app())

    r = client.post("/api/games/scan")
    assert r.status_code == 200
    assert r.json() == {info.id: "modified"}
    assert not tracker.is_shareable(info.id)


def test_tracker_scan_directly_detects_change_on_downloaded_game(
    isolated_config, tmp_path, make_game
):
    info = _downloaded_game(tmp_path, make_game)

    library = Library()
    library.add(info)
    tracker = ContentTracker(isolated_config, library)
    tracker.ensure_baseline(info.id)

    (info.path / "game.bin").write_bytes(b"z" * 999)

    assert tracker.scan(info.id) == "modified"
