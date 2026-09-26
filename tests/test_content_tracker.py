"""ContentTracker: baseline, change detection, enforced lock (Phase 2)."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import game as game_mod
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library


@pytest.fixture
def game_with_files(tmp_path, make_game):
    info = make_game(tmp_path, "Stardew Valley")
    (info.path / "bin.exe").write_bytes(b"x" * 100)
    (info.path / "data.pak").write_bytes(b"y" * 50)
    return info


def _tracker(cfg, library):
    return ContentTracker(cfg, library)


def test_ensure_baseline_hashes_and_sets_clean(isolated_config, tmp_path, make_game):
    info = make_game(tmp_path, "Portal 2")
    (info.path / "game.bin").write_bytes(b"a" * 42)

    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)

    tracker.ensure_baseline(info.id)

    reloaded = game_mod.load_from_path(info.path)
    assert reloaded is not None
    assert reloaded.files == {"game.bin": reloaded.files["game.bin"]}
    assert reloaded.sizes == {"game.bin": 42}
    assert reloaded.content.content_hash != ""
    assert tracker.state(info.id) == "clean"
    assert tracker.is_shareable(info.id)


def test_scan_size_change_sets_modified_and_drops_seed(isolated_config, tmp_path, game_with_files):
    info = game_with_files
    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    tracker.ensure_baseline(info.id)
    assert tracker.state(info.id) == "clean"

    mock_transfer = MagicMock()
    app_state.init(isolated_config, library, transfer=mock_transfer, content=tracker)

    (info.path / "bin.exe").write_bytes(b"x" * 200)  # size change

    state = tracker.scan(info.id)

    assert state == "modified"
    assert not tracker.is_shareable(info.id)
    mock_transfer.drop_seed.assert_called_once_with(info.id)
    assert tracker.summary(info.id)["changed"] == 1


def test_scan_mtime_only_stays_clean(isolated_config, tmp_path, game_with_files):
    info = game_with_files
    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    tracker.ensure_baseline(info.id)

    path = info.path / "bin.exe"
    now = time.time()
    os.utime(path, (now + 10, now + 10))  # touch mtime only, same content/size

    state = tracker.scan(info.id)

    assert state == "clean"
    assert tracker.is_shareable(info.id)


def test_scan_new_file_reported_as_added_but_stays_clean(
    isolated_config, tmp_path, game_with_files
):
    info = game_with_files
    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    tracker.ensure_baseline(info.id)

    (info.path / "save.dat").write_bytes(b"save")

    state = tracker.scan(info.id)

    assert state == "clean"
    assert tracker.summary(info.id)["added"] == 1


def test_scan_ignores_files_in_ignored_folder(isolated_config, tmp_path, game_with_files):
    info = game_with_files
    info.content.ignore = ["saves/**"]
    game_mod.save(info)

    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    tracker.ensure_baseline(info.id)

    (info.path / "saves").mkdir()
    (info.path / "saves" / "slot1.sav").write_bytes(b"save-data")

    state = tracker.scan(info.id)

    assert state == "clean"
    assert tracker.summary(info.id)["added"] == 0


def test_magnet_returns_409_when_modified(isolated_config, tmp_path, game_with_files, monkeypatch):
    info = game_with_files
    info.torrent.magnet = "magnet:?xt=urn:btih:" + "a" * 40
    info.torrent.info_hash = "a" * 40
    game_mod.save(info)

    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    tracker.ensure_baseline(info.id)

    (info.path / "bin.exe").write_bytes(b"x" * 999)  # modify after baseline

    app_state.init(isolated_config, library, content=tracker)
    client = TestClient(create_app())

    r = client.get(f"/api/games/{info.id}/magnet")
    assert r.status_code == 409
