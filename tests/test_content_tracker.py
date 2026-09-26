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

    # A later scan must still see the patch. The snapshot stays at the
    # published stat until the new hash is stored.
    assert tracker.scan(info.id) == "modified"
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


def test_baseline_hash_reports_progress(isolated_config, tmp_path, make_game, monkeypatch):
    info = make_game(tmp_path, "Hades")
    (info.path / "a.bin").write_bytes(b"a")
    (info.path / "b.bin").write_bytes(b"bb")

    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    noted: list[float] = []
    real_note = tracker._note_hash_progress

    def wrapped(game_id, progress):
        noted.append(progress)
        return real_note(game_id, progress)

    monkeypatch.setattr(tracker, "_note_hash_progress", wrapped)
    tracker.ensure_baseline(info.id)

    assert tracker.state(info.id) == "clean"
    assert noted == [0.5, 1.0]
    assert tracker.hash_progress(info.id) is None


def test_ensure_baseline_leaves_patched_game_modified(
    isolated_config, tmp_path, game_with_files, monkeypatch
):
    info = game_with_files
    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    tracker.ensure_baseline(info.id)
    old_hash = info.files["bin.exe"]

    (info.path / "bin.exe").write_bytes(b"x" * 999)

    def boom(_path):
        raise AssertionError("patched game must not be hashed before publish")

    monkeypatch.setattr("deckdrop.core.content_tracker.integrity.hash_file", boom)
    tracker.ensure_baseline(info.id)

    assert tracker.state(info.id) == "modified"
    assert not tracker.is_shareable(info.id)
    assert info.files["bin.exe"] == old_hash


def test_ensure_baseline_without_snapshot_does_not_adopt_patch(
    isolated_config, tmp_path, game_with_files, monkeypatch
):
    info = game_with_files
    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    tracker.ensure_baseline(info.id)
    old_hash = info.files["bin.exe"]
    (isolated_config.content_state_dir / f"{info.id}.json").unlink()

    (info.path / "bin.exe").write_bytes(b"x" * 999)
    fresh = _tracker(isolated_config, library)

    def boom(_path):
        raise AssertionError("patched game must not be hashed before publish")

    monkeypatch.setattr("deckdrop.core.content_tracker.integrity.hash_file", boom)
    fresh.ensure_baseline(info.id)

    assert fresh.state(info.id) == "modified"
    assert info.files["bin.exe"] == old_hash


def test_game_out_shows_progress_while_hashing(isolated_config, tmp_path, game_with_files):
    from deckdrop.api.routes.games import GameOut

    info = game_with_files
    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    tracker.ensure_baseline(info.id)
    tracker.set_state(info.id, "hashing", hash_progress=0.4)
    app_state.init(isolated_config, library, content=tracker)

    out = GameOut.from_info(info)
    assert out.content_state == "hashing"
    assert out.torrent_preparing is True
    assert out.torrent_prep_progress == 0.4


# -- hash reasons + cancelling a baseline hash (debug mode / update while hashing) --


def test_hash_full_records_reason_and_clears_pending(isolated_config, tmp_path, game_with_files):
    from deckdrop.core import debuglog

    info = game_with_files
    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    debuglog.clear()

    tracker.request_rehash(info.id, "manifest_mismatch")
    tracker.ensure_baseline(info.id)

    (ev,) = debuglog.events(info.id)
    assert ev["kind"] == "blake2b"
    assert ev["reason"] == "manifest_mismatch"
    assert ev["files"] == ["bin.exe", "data.pak"]
    assert "pending_hash_reason" not in tracker.get_entry(info.id)
    # Persisted: a fresh tracker sees the cleared entry too.
    assert "pending_hash_reason" not in _tracker(isolated_config, library).get_entry(info.id)


def test_hash_full_default_reason_local_vs_downloaded(isolated_config, tmp_path, make_game):
    from deckdrop.core import debuglog

    local = make_game(tmp_path, "Local Game")
    (local.path / "a.bin").write_bytes(b"a")
    downloaded = make_game(tmp_path, "From Peer")
    (downloaded.path / "b.bin").write_bytes(b"b")
    downloaded.origin.peer_id = "peer1"
    downloaded.origin.peer_name = "PC"
    game_mod.save(downloaded)

    library = Library()
    library.add(local)
    library.add(downloaded)
    tracker = _tracker(isolated_config, library)
    debuglog.clear()
    tracker.ensure_baseline(local.id)
    tracker.ensure_baseline(downloaded.id)

    assert debuglog.events(local.id)[0]["reason"] == "new_local_game"
    assert debuglog.events(downloaded.id)[0]["reason"] == "download_without_manifest"


def test_scan_records_mtime_only_rehash(isolated_config, tmp_path, game_with_files):
    from deckdrop.core import debuglog

    info = game_with_files
    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)
    tracker.ensure_baseline(info.id)
    debuglog.clear()

    st = (info.path / "data.pak").stat()
    os.utime(info.path / "data.pak", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert tracker.scan(info.id) == "clean"

    (ev,) = debuglog.events(info.id)
    assert ev["reason"] == "mtime_changed"
    assert ev["files"] == ["data.pak"]


def test_cancel_hash_stops_baseline_without_writing(
    isolated_config, tmp_path, game_with_files, monkeypatch
):
    import threading

    from deckdrop.core import integrity

    info = game_with_files
    library = Library()
    library.add(info)
    tracker = _tracker(isolated_config, library)

    started = threading.Event()
    real_hash = integrity.hash_file

    def slow_hash(path, progress=None):
        started.set()
        # Keep "reading" until the cancel request is seen via the callback.
        for _ in range(500):
            progress(1)
            time.sleep(0.01)
        return real_hash(path)

    monkeypatch.setattr("deckdrop.core.content_tracker.integrity.hash_file", slow_hash)
    t = threading.Thread(target=tracker.ensure_baseline, args=(info.id,))
    t.start()
    assert started.wait(5)
    assert tracker.state(info.id) == "hashing"

    assert tracker.cancel_hash(info.id, timeout=5) is True
    t.join(5)
    assert not t.is_alive()

    reloaded = game_mod.load_from_path(info.path)
    assert reloaded.files == {}
    assert tracker.state(info.id) == "unverified"
    assert tracker.get_entry(info.id)["pending_hash_reason"] == "update_started"

    # While held (update being set up), no new baseline hash starts.
    monkeypatch.setattr("deckdrop.core.content_tracker.integrity.hash_file", real_hash)
    tracker.ensure_baseline(info.id)
    assert game_mod.load_from_path(info.path).files == {}

    # Released (update failed): the next baseline catches up, with the reason.
    tracker.release_hash_hold(info.id)
    tracker.ensure_baseline(info.id)
    assert set(game_mod.load_from_path(info.path).files) == {"bin.exe", "data.pak"}
    assert tracker.state(info.id) == "clean"


def test_cancel_hash_without_running_hash_is_noop(isolated_config, tmp_path, game_with_files):
    library = Library()
    library.add(game_with_files)
    tracker = _tracker(isolated_config, library)
    assert tracker.cancel_hash(game_with_files.id) is True
    tracker.release_hash_hold(game_with_files.id)
