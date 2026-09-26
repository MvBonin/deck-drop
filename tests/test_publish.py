"""ContentTracker.publish + /api/games/{id}/publish (Phase 3)."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import game as game_mod
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library


@pytest.fixture
def published_game(tmp_path, isolated_config, make_game):
    info = make_game(tmp_path, "Stardew Valley")
    (info.path / "bin.exe").write_bytes(b"x" * 100)
    (info.path / "data.pak").write_bytes(b"y" * 50)

    library = Library()
    library.add(info)
    tracker = ContentTracker(isolated_config, library)
    tracker.ensure_baseline(info.id)
    return isolated_config, library, tracker, info


def _run_publish_sync(tracker, game_id, version_label, note, exclude):
    """Run the publish worker directly (no thread) so tests stay deterministic."""
    tracker._publish_worker(game_id, version_label, note, exclude)


def test_publish_bumps_revision_and_content_hash(published_game, monkeypatch):
    cfg, library, tracker, info = published_game
    old_hash = info.content.content_hash

    with patch("deckdrop.core.torrent_prep.invalidate_torrent") as mock_invalidate:
        (info.path / "bin.exe").write_bytes(b"x" * 500)
        tracker.scan(info.id)
        _run_publish_sync(tracker, info.id, "1.1", "Hotfix", [])
        mock_invalidate.assert_called_once_with(info.id)

    reloaded = game_mod.load_from_path(info.path)
    assert reloaded.content.revision == 2
    assert reloaded.content.content_hash != old_hash
    assert reloaded.history[-1].note == "Hotfix"
    assert reloaded.history[-1].version_label == "1.1"
    assert tracker.state(info.id) == "clean"


def test_publish_unchanged_keeps_revision(published_game):
    cfg, library, tracker, info = published_game

    with patch("deckdrop.core.torrent_prep.invalidate_torrent") as mock_invalidate:
        tracker.scan(info.id)
        _run_publish_sync(tracker, info.id, "1.1", "no-op", [])
        mock_invalidate.assert_not_called()

    reloaded = game_mod.load_from_path(info.path)
    assert reloaded.content.revision == 1
    assert reloaded.content.version_label == "1.1"
    assert reloaded.content.note == "no-op"
    assert tracker.state(info.id) == "clean"


def test_publish_exclude_removes_file_from_manifest(published_game):
    cfg, library, tracker, info = published_game
    (info.path / "save.dat").write_bytes(b"save-data")

    with patch("deckdrop.core.torrent_prep.invalidate_torrent"):
        tracker.scan(info.id)
        _run_publish_sync(tracker, info.id, "1.1", "", ["save.dat"])

    reloaded = game_mod.load_from_path(info.path)
    assert "save.dat" not in reloaded.files
    assert "save.dat" in reloaded.content.ignore


def test_publish_after_advanced_snapshot_still_bumps_revision(published_game):
    """A snapshot moved onto the patched file must not swallow the new version."""
    from deckdrop.core import content

    cfg, library, tracker, info = published_game
    (info.path / "bin.exe").write_bytes(b"patched" * 40)
    snap = content.take_snapshot(info.path, info.files.keys())
    tracker.set_state(
        info.id,
        "clean",
        snapshot=snap,
        summary={"changed": 0, "removed": 0, "added": 0},
        changed=[],
        removed=[],
        added=[],
    )

    with patch("deckdrop.core.torrent_prep.invalidate_torrent"):
        _run_publish_sync(tracker, info.id, "1.6.8", "Patch", [])

    reloaded = game_mod.load_from_path(info.path)
    assert reloaded.content.revision == 2
    assert reloaded.content.version_label == "1.6.8"


def test_scan_detects_patch_when_snapshot_already_matches_disk(published_game):
    from deckdrop.core import content

    cfg, library, tracker, info = published_game
    (info.path / "bin.exe").write_bytes(b"patched" * 40)
    snap = content.take_snapshot(info.path, info.files.keys())
    tracker.set_state(
        info.id,
        "clean",
        snapshot=snap,
        summary={"changed": 0, "removed": 0, "added": 0},
        changed=[],
        removed=[],
        added=[],
    )

    assert tracker.scan(info.id) == "modified"
    assert "bin.exe" in tracker.change_lists(info.id)["changed"]


def test_publish_mtime_only_change_is_unchanged(published_game, tmp_path):
    import os

    cfg, library, tracker, info = published_game
    path = info.path / "bin.exe"
    now = time.time()
    os.utime(path, (now + 5, now + 5))

    with patch("deckdrop.core.torrent_prep.invalidate_torrent") as mock_invalidate:
        tracker.scan(info.id)
        _run_publish_sync(tracker, info.id, "", "", [])
        mock_invalidate.assert_not_called()

    reloaded = game_mod.load_from_path(info.path)
    assert reloaded.content.revision == 1


def test_publish_api_rejects_path_traversal(published_game):
    cfg, library, tracker, info = published_game
    app_state.init(cfg, library, content=tracker)
    client = TestClient(create_app())

    r = client.post(f"/api/games/{info.id}/publish", json={"exclude": ["../x"]})
    assert r.status_code == 400


def test_publish_api_returns_202(published_game):
    cfg, library, tracker, info = published_game
    app_state.init(cfg, library, transfer=MagicMock(), content=tracker)
    client = TestClient(create_app())

    with patch.object(tracker, "publish") as mock_publish:
        r = client.post(
            f"/api/games/{info.id}/publish",
            json={"version_label": "1.2", "note": "x", "exclude": []},
        )
    assert r.status_code == 202
    mock_publish.assert_called_once_with(info.id, "1.2", "x", [])


def test_publish_api_400_while_busy(published_game):
    cfg, library, tracker, info = published_game
    tracker.set_state(info.id, "publishing")
    app_state.init(cfg, library, content=tracker)
    client = TestClient(create_app())

    r = client.post(f"/api/games/{info.id}/publish", json={})
    assert r.status_code == 400


def test_create_torrent_data_with_file_subset():
    pytest.importorskip("libtorrent")
    import tempfile
    from pathlib import Path

    from deckdrop.core.torrent import create_torrent_data

    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "Game"
        root.mkdir()
        (root / "keep.bin").write_bytes(b"a" * (2 * 1024 * 1024))
        (root / "drop.bin").write_bytes(b"b" * 1024)

        data = create_torrent_data(root, files=["keep.bin"])

        import libtorrent as lt

        info = lt.torrent_info(lt.bdecode(data))
        fs = info.files()
        names = {
            fs.file_path(i).replace("\\", "/").split("/", 1)[-1] for i in range(fs.num_files())
        }
        assert "keep.bin" in names
        assert "drop.bin" not in names
        assert info.info_hashes().has_v2()
        for i in range(fs.num_files()):
            if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                continue
            assert fs.file_offset(i) % info.piece_length() == 0
