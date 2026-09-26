"""deckdrop.core.debuglog: ring buffer of hash/recheck reasons + GET /api/debug."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import debuglog
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library


@pytest.fixture(autouse=True)
def _clean_log():
    debuglog.clear()
    yield
    debuglog.clear()


def test_record_and_events_newest_first_with_filter():
    debuglog.record("blake2b", "new_local_game", "g1", ["a", "b"])
    debuglog.record("recheck", "stall", "g2")

    events = debuglog.events()
    assert [e["reason"] for e in events] == ["stall", "new_local_game"]
    assert events[1]["file_count"] == 2
    assert events[1]["reason_text"] == debuglog.REASON_TEXT["new_local_game"]
    assert [e["game_id"] for e in debuglog.events("g1")] == ["g1"]


def test_record_caps_file_list_and_buffer():
    debuglog.record("blake2b", "publish", "g1", [f"f{i}" for i in range(500)])
    ev = debuglog.events()[0]
    assert ev["file_count"] == 500
    assert len(ev["files"]) == debuglog.MAX_FILES_PER_EVENT

    for _ in range(debuglog.MAX_EVENTS + 10):
        debuglog.record("recheck", "stall", "g1")
    assert len(debuglog.events()) == debuglog.MAX_EVENTS


def test_unknown_reason_falls_back_to_code():
    assert debuglog.reason_text("something_new") == "something_new"


def test_api_debug_lists_games_events_and_pending_reason(isolated_config, tmp_path, make_game):
    info = make_game(tmp_path, "Dawnwalker")
    (info.path / "game.bin").write_bytes(b"x" * 10)
    library = Library()
    library.add(info)
    tracker = ContentTracker(isolated_config, library)
    tracker.request_rehash(info.id, "manifest_mismatch")
    app_state.init(isolated_config, library, content=tracker)
    debuglog.record("blake2b", "mtime_changed", info.id, ["game.bin"])
    debuglog.record("recheck", "stall", "other")

    client = TestClient(create_app())
    data = client.get("/api/debug").json()
    assert data["peer_id"] == isolated_config.peer_id
    assert [e["reason"] for e in data["hash_events"]] == ["stall", "mtime_changed"]
    (game,) = data["games"]
    assert game["id"] == info.id
    assert game["has_file_hashes"] is False
    assert game["content"]["pending_hash_reason"] == "manifest_mismatch"
    assert game["content"]["pending_hash_reason_text"]
    assert data["downloads"] == []

    filtered = client.get(f"/api/debug?game_id={info.id}").json()
    assert [e["reason"] for e in filtered["hash_events"]] == ["mtime_changed"]
