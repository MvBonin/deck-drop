"""Starting an update must not wait for a baseline hash of the old version.

POST /api/games/{id}/update used to answer 409 while the game was "hashing";
now it cancels that hash and starts the update right away (without the
Phase 6 fast path, since there are no old hashes to diff against).
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.routes import games as games_routes
from deckdrop.api.server import create_app
from deckdrop.core import game as game_mod
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library
from deckdrop.network.transfer import DownloadStatus


def _status(game_id: str) -> DownloadStatus:
    return DownloadStatus(
        id="d1",
        game_id=game_id,
        game_name="Dawnwalker",
        peer_id="peer1",
        peer_name="PC",
        status="queued",
        progress=0.0,
        speed_bytes_sec=0,
        downloaded_bytes=0,
        total_bytes=0,
        num_peers=0,
        kind="update",
    )


@pytest.fixture
def app_with_hashing_game(isolated_config, tmp_path, make_game, monkeypatch):
    info = make_game(tmp_path, "Dawnwalker")
    (info.path / "game.bin").write_bytes(b"x" * 64)
    library = Library()
    library.add(info)
    tracker = ContentTracker(isolated_config, library)

    transfer = MagicMock()
    transfer.start_update.return_value = "d1"
    transfer.get_status.return_value = _status(info.id)
    registry = MagicMock()
    registry.peers_for_version.return_value = [
        SimpleNamespace(peer_id="peer1", name="PC", address="192.168.1.5", port=7373)
    ]
    app_state.init(isolated_config, library, registry, transfer=transfer, content=tracker)
    monkeypatch.setattr(games_routes, "_fetch_peer_manifest", lambda *a: {"files": {}})
    monkeypatch.setattr(games_routes, "_fetch_peer_torrent", lambda *a: b"torrent")
    return TestClient(create_app()), info, tracker, transfer


def test_update_cancels_running_baseline_hash(app_with_hashing_game, monkeypatch):
    client, info, tracker, transfer = app_with_hashing_game

    started = threading.Event()

    def slow_hash(path, progress=None):
        started.set()
        for _ in range(1000):
            progress(1)
            time.sleep(0.01)
        return "never"

    monkeypatch.setattr("deckdrop.core.content_tracker.integrity.hash_file", slow_hash)
    hasher = threading.Thread(target=tracker.ensure_baseline, args=(info.id,))
    hasher.start()
    assert started.wait(5)
    assert tracker.state(info.id) == "hashing"

    t0 = time.monotonic()
    r = client.post(f"/api/games/{info.id}/update", json={"version_key": "v2"})
    hasher.join(5)

    assert r.status_code == 202, r.text
    assert time.monotonic() - t0 < 5  # did not wait for the (10 s) hash
    assert not hasher.is_alive()
    transfer.start_update.assert_called_once()
    game_arg = transfer.start_update.call_args.args[0]
    assert game_arg.files == {}  # old version never got hashed
    assert game_mod.load_from_path(info.path).files == {}
    # Hold released again after the request.
    assert info.id not in tracker._hash_hold


def test_update_with_stale_hashing_state_is_not_blocked(app_with_hashing_game):
    """A "hashing" state with no hash actually running (e.g. left over from
    before a restart) must not block the update either."""
    client, info, tracker, transfer = app_with_hashing_game
    tracker.set_state(info.id, "hashing", hash_progress=0.4)

    r = client.post(f"/api/games/{info.id}/update", json={"version_key": "v2"})

    assert r.status_code == 202, r.text
    transfer.start_update.assert_called_once()


def test_update_still_blocked_while_publishing(app_with_hashing_game):
    client, info, tracker, transfer = app_with_hashing_game
    tracker.set_state(info.id, "publishing")

    r = client.post(f"/api/games/{info.id}/update", json={"version_key": "v2"})

    assert r.status_code == 409
    transfer.start_update.assert_not_called()


def test_interrupted_hashing_state_is_reset_on_load(isolated_config, tmp_path, make_game):
    info = make_game(tmp_path, "Dawnwalker")
    (info.path / "game.bin").write_bytes(b"x" * 64)
    library = Library()
    library.add(info)
    tracker = ContentTracker(isolated_config, library)
    tracker.set_state(info.id, "hashing", hash_progress=0.5)

    # "Restart": a new tracker loads the persisted state.
    restarted = ContentTracker(isolated_config, library)
    assert restarted.state(info.id) == "unverified"
    assert restarted.get_entry(info.id)["pending_hash_reason"] == "interrupted"
    assert "hash_progress" not in restarted.get_entry(info.id)

    # ...and the startup baseline is no longer skipped as "busy".
    restarted.ensure_baseline(info.id)
    assert restarted.state(info.id) == "clean"
    assert game_mod.load_from_path(info.path).files


def test_interrupted_publish_is_reset_to_modified(isolated_config, tmp_path, make_game):
    info = make_game(tmp_path, "Dawnwalker")
    library = Library()
    library.add(info)
    tracker = ContentTracker(isolated_config, library)
    tracker.set_state(info.id, "publishing")

    restarted = ContentTracker(isolated_config, library)
    assert restarted.state(info.id) == "modified"
    assert "pending_hash_reason" not in restarted.get_entry(info.id)


def test_update_from_unverified_state(app_with_hashing_game):
    """After an interrupted hash the game is "unverified" – updatable, and
    starting the update must not kick off the full hash in the request."""
    client, info, tracker, transfer = app_with_hashing_game
    tracker.set_state(info.id, "unverified", pending_hash_reason="interrupted")

    r = client.post(f"/api/games/{info.id}/update", json={"version_key": "v2"})

    assert r.status_code == 202, r.text
    transfer.start_update.assert_called_once()
    assert game_mod.load_from_path(info.path).files == {}
