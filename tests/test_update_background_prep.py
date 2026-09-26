"""Update start no longer blocks on reading existing files: the preparation
(local prep + piece check) runs in a thread and shows up in "Downloads" as
phase "preparing"; the poll loop adds the torrent once it is done.
"""

from __future__ import annotations

import threading
import time

import pytest

import deckdrop.network.transfer as transfer_mod
from deckdrop.api import state as app_state
from deckdrop.core import debuglog
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library
from deckdrop.core.torrent import PieceCheckCancelled
from tests.test_update_flow import (  # noqa: F401 – fixtures + helpers
    _FakeLt,
    _FakeTorrentInfo,
    _manifest_for,
    _Peer,
    _setup_game,
    tm_factory,
)


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def update_env(tmp_path, make_game, tm_factory, monkeypatch):  # noqa: F811
    cfg, tm = tm_factory()
    info = _setup_game(tmp_path, make_game)
    manifest = _manifest_for(info)
    library = Library()
    library.add(info)
    tm._library = library
    tracker = ContentTracker(cfg, library)
    app_state.init(cfg, library, transfer=tm, content=tracker)
    fake_lt = _FakeLt(_FakeTorrentInfo([f"{info.path.name}/x"]))
    monkeypatch.setattr(transfer_mod, "_lt", lambda: fake_lt)

    gate = threading.Event()
    started = threading.Event()
    calls = []

    def blocking_fast_path(
        lt,
        ti,
        game_path,
        diff,
        tracker_,
        game_id,
        params,
        reason="x",
        *,
        on_progress=None,
        cancel=None,
    ):
        calls.append(reason)
        started.set()
        on_progress(0.25)
        while not gate.is_set():
            if cancel is not None and cancel.is_set():
                raise PieceCheckCancelled()
            time.sleep(0.01)
        params.have_pieces = [True]

    tm._apply_have_pieces_fast_path = blocking_fast_path
    peers = [_Peer("peer1", "Bob", "192.168.1.5")]
    return tm, info, manifest, tracker, peers, gate, started, calls


def test_start_update_background_returns_before_piece_check(update_env):
    tm, info, manifest, tracker, peers, gate, started, _calls = update_env

    t0 = time.monotonic()
    did = tm.start_update(info, peers, b"torrent-bytes", manifest, background=True)
    assert time.monotonic() - t0 < 2
    assert started.wait(5)

    # Visible in "Downloads" right away, no torrent in the session yet.
    st = tm.get_status(did)
    assert st.status == "verifying" and st.phase == "preparing"
    assert st.phase_progress == pytest.approx(0.25)
    assert st.kind == "update"
    assert did not in tm._handles
    assert tracker.state(info.id) == "updating"
    assert [s.id for s in tm.all_statuses()] == [did]
    tm._session.add_torrent.assert_not_called()

    gate.set()
    assert _wait(lambda: tm._update_preps[did].done)
    tm._finish_update_preps()

    assert did in tm._handles
    assert tm._paused[did].preparing is False
    params = tm._session.add_torrent.call_args.args[0]
    assert params.have_pieces == [True]
    tm._handles[did].handle.connect_peer.assert_called_with(("192.168.1.5", tm._cfg.torrent_port))


def test_remove_during_preparation_cancels_it(update_env):
    tm, info, manifest, tracker, peers, _gate, started, _calls = update_env
    did = tm.start_update(info, peers, b"torrent-bytes", manifest, background=True)
    assert started.wait(5)
    prep = tm._update_preps[did]

    assert tm.remove_download(did) is True
    assert _wait(lambda: prep.done)
    tm._finish_update_preps()

    assert did not in tm._handles and did not in tm._paused
    tm._session.add_torrent.assert_not_called()
    assert tracker.state(info.id) == "modified"


def test_pause_cancels_and_resume_restarts_preparation(update_env):
    tm, info, manifest, _tracker, peers, gate, started, calls = update_env
    did = tm.start_update(info, peers, b"torrent-bytes", manifest, background=True)
    assert started.wait(5)
    first = tm._update_preps[did]

    assert tm.pause_download(did) is True
    assert _wait(lambda: first.done)
    assert tm.get_status(did).status == "paused"
    tm._finish_update_preps()
    tm._session.add_torrent.assert_not_called()

    started.clear()
    assert tm.resume_download(did) is True
    assert started.wait(5)
    assert len(calls) == 2
    gate.set()
    assert _wait(lambda: tm._update_preps[did].done)
    tm._finish_update_preps()
    assert did in tm._handles


def test_preparation_error_shows_error_and_retry_restarts(update_env, monkeypatch):
    tm, info, manifest, _tracker, peers, gate, started, calls = update_env
    from deckdrop.core import content

    def broken_prep(root, plan):
        raise OSError("Datenträger voll")

    monkeypatch.setattr(content, "apply_local_prep", broken_prep)
    did = tm.start_update(info, peers, b"torrent-bytes", manifest, background=True)
    assert _wait(lambda: tm._update_preps[did].done)
    tm._finish_update_preps()

    st = tm.get_status(did)
    assert st.status == "error"
    assert "Datenträger voll" in st.error

    monkeypatch.undo()
    monkeypatch.setattr(transfer_mod, "_lt", lambda: _FakeLt(_FakeTorrentInfo(["x"])))
    gate.set()
    assert tm.retry_download(did) is True
    assert _wait(lambda: tm._update_preps[did].done)
    tm._finish_update_preps()
    assert did in tm._handles


def test_preparing_flag_survives_restart(update_env, tm_factory):  # noqa: F811
    tm, info, manifest, _tracker, peers, _gate, started, _calls = update_env
    did = tm.start_update(info, peers, b"torrent-bytes", manifest, background=True)
    assert started.wait(5)

    reloaded = transfer_mod.TransferManager(tm._cfg)
    assert reloaded._paused[did].preparing is True
    assert reloaded.get_status(did).phase == "preparing"
    tm.remove_download(did)


def test_piece_check_debug_event_reports_reuse(tmp_path):
    lt = pytest.importorskip("libtorrent")
    from deckdrop.core.torrent import create_torrent_data, retarget_root

    game = tmp_path / "Game"
    game.mkdir()
    (game / "a.bin").write_bytes(b"A" * (3 * 1024 * 1024))
    ti = lt.torrent_info(lt.bdecode(create_torrent_data(game)))
    retarget_root(lt, ti, game.name)
    have = [True] * ti.num_pieces()
    have[-1] = False
    debuglog.clear()
    transfer_mod.TransferManager._record_piece_check(
        ti, have, "g1", ["a.bin"], "update_reuse_local"
    )
    (ev,) = debuglog.events("g1")
    assert f"{ti.num_pieces() - 1} von {ti.num_pieces()} Pieces vorhanden" in ev["detail"]
    assert "1 zu laden" in ev["detail"]
