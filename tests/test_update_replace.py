"""Replacing a running update: the host published a newer version or rebuilt
the torrent mid-update, so the running one may never finish. Starting a new
update must be possible and reuse what is already on disk.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import deckdrop.network.transfer as transfer_mod
from deckdrop.api import state as app_state
from deckdrop.api.routes import games as games_routes
from deckdrop.api.server import create_app
from deckdrop.core import debuglog
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library
from deckdrop.network.peer_registry import PeerEntry, PeerRegistry
from deckdrop.network.transfer import UpdateAlreadyRunning
from tests.test_update_flow import (  # noqa: F401 – fixture + helpers
    _FakeLt,
    _manifest_for,
    _Peer,
    _setup_game,
    tm_factory,
)


class _TI:
    """Minimal torrent_info whose v1 info hash we control."""

    def __init__(self, info_hash: str):
        self._hash = info_hash

    def files(self):
        return SimpleNamespace(num_files=lambda: 0)

    def rename_file(self, i, p):  # pragma: no cover - no files
        pass

    def info_hashes(self):
        return SimpleNamespace(v1=self._hash)


class _LtByTorrent(_FakeLt):
    """torrent bytes → torrent_info with info hash = the bytes' text."""

    def __init__(self):
        super().__init__(None)

    def torrent_info(self, decoded):
        return _TI(decoded.decode() if isinstance(decoded, bytes) else str(decoded))


@pytest.fixture
def env(tmp_path, make_game, tm_factory, monkeypatch):  # noqa: F811
    cfg, tm = tm_factory()
    info = _setup_game(tmp_path, make_game)
    library = Library()
    library.add(info)
    tm._library = library
    tracker = ContentTracker(cfg, library)
    app_state.init(cfg, library, transfer=tm, content=tracker)
    monkeypatch.setattr(transfer_mod, "_lt", lambda: _LtByTorrent())
    debuglog.clear()
    return tm, info, tracker


def _manifest(info, revision: int, label: str, files: dict, sizes: dict) -> dict:
    m = _manifest_for(info)
    m["content"] = {**m["content"], "revision": revision, "version_label": label}
    m["files"], m["sizes"] = files, sizes
    return m


PEERS = [_Peer("peer1", "PC", "192.168.1.5")]


def test_new_version_replaces_running_update(env, tmp_path):
    tm, info, tracker = env
    v2 = _manifest(
        info,
        2,
        "1.0.5",
        {"bin.exe": "h_bin_new", "assets/data.pak": "h_pak", "extra_v2.dat": "h_x"},
        {"bin.exe": 100, "assets/data.pak": 50, "extra_v2.dat": 4},
    )
    first = tm.start_update(info, PEERS, b"torrent-v2", v2)
    first_handle = tm._handles[first].handle
    # The v2 transfer already wrote its new file before the host moved on.
    (info.path / "extra_v2.dat").write_bytes(b"v2v2")
    (info.path / "only_in_v3.dat").write_bytes(b"xx")  # partially there already

    v3 = _manifest(
        info,
        3,
        "1.0.6",
        {"bin.exe": "h_bin_v3", "assets/data.pak": "h_pak", "only_in_v3.dat": "h_y"},
        {"bin.exe": 100, "assets/data.pak": 50, "only_in_v3.dat": 2},
    )
    second = tm.start_update(info, PEERS, b"torrent-v3", v3)

    assert second != first
    assert first not in tm._paused and first not in tm._handles
    tm._session.remove_torrent.assert_any_call(first_handle)
    assert tm.active_update_for(info.id).download_id == second
    assert tracker.state(info.id) == "updating"
    pending = tracker.get_entry(info.id)["pending_update"]
    assert pending["download_id"] == second
    # extra_v2.dat only existed in the replaced target → deleted at finalize;
    # old_readme.txt was removed from the game long before (v1 → v3).
    assert "extra_v2.dat" in pending["deletes_after"]
    assert "old_readme.txt" in pending["deletes_after"]
    # Files are kept on disk for reuse – nothing deleted yet.
    assert (info.path / "extra_v2.dat").exists()
    ev = debuglog.events(info.id)
    assert any(e["reason"] == "update_replaced" and "1.0.5 → 1.0.6" in e["detail"] for e in ev)


def test_same_torrent_is_not_restarted(env):
    tm, info, tracker = env
    v2 = _manifest(info, 2, "1.0.5", {"bin.exe": "h"}, {"bin.exe": 100})
    first = tm.start_update(info, PEERS, b"torrent-v2", v2)

    with pytest.raises(UpdateAlreadyRunning):
        tm.start_update(info, PEERS, b"torrent-v2", v2)

    assert tm.active_update_for(info.id).download_id == first
    assert first in tm._handles
    assert tracker.state(info.id) == "updating"


def test_existing_new_file_is_piece_checked_not_redownloaded(env, monkeypatch):
    tm, info, _tracker = env
    (info.path / "new.dat").write_bytes(b"z" * 10)  # longer than in v2
    seen = {}

    def spy(lt, ti, game_path, diff, tracker_, game_id, params, reason="x", **kw):
        seen["changed"], seen["added"] = sorted(diff.changed), sorted(diff.added)

    tm._apply_have_pieces_fast_path = spy
    v2 = _manifest(
        info,
        2,
        "1.0.5",
        {"bin.exe": "h_bin_old", "new.dat": "h_new"},
        {"bin.exe": 100, "new.dat": 4},
    )
    tm.start_update(info, PEERS, b"torrent-v2", v2)

    assert "new.dat" in seen["changed"] and "new.dat" not in seen["added"]
    assert (info.path / "new.dat").stat().st_size == 4  # truncated to the new size


def _app_with_running_update(env, offers):
    tm, info, tracker = env
    v2 = _manifest(info, 2, "1.0.5", {"bin.exe": "h"}, {"bin.exe": 100})
    tm.start_update(info, PEERS, b"torrent-v2", v2)
    registry = PeerRegistry()
    registry._peers["peer1"] = PeerEntry("peer1", "PC", "192.168.1.5", 7373, games=offers)
    app_state.init(tm._cfg, tm._library, registry, transfer=tm, content=tracker)
    return TestClient(create_app()), info


def _offer(info, revision, label, info_hash):
    return {
        "id": info.id,
        "name": info.name,
        "revision": revision,
        "version_label": label,
        "info_hash": info_hash,
        "content_hash": f"c{revision}",
        "shareable": True,
        "has_torrent": True,
    }


@pytest.mark.parametrize(
    ("offers", "expected"),
    [
        ([], (False, "")),  # host offline
        (["same"], (False, "")),  # host still seeds the running torrent
        (["rebuilt"], (True, "1.0.5")),  # same version, torrent rebuilt
        (["same", "newer"], (True, "1.0.6")),  # newer version published
    ],
)
def test_card_offers_restart(env, offers, expected):
    _tm, info, _tracker = env
    kinds = {
        "same": _offer(info, 2, "1.0.5", "torrent-v2"),
        "rebuilt": _offer(info, 2, "1.0.5", "torrent-v2-rebuilt"),
        "newer": _offer(info, 3, "1.0.6", "torrent-v3"),
    }
    _client, info = _app_with_running_update(env, [kinds[k] for k in offers])
    # GameOut directly: GET /api/games would reload the library from the
    # configured download dir, where this test game doesn't live.
    card = games_routes.GameOut.from_info(info)
    assert (card.update_restart_available, card.update_restart_label) == expected


def test_route_restarts_running_update_and_rejects_same(env, monkeypatch):
    tm, info, tracker = env
    client, info = _app_with_running_update(env, [_offer(info, 3, "1.0.6", "torrent-v3")])
    v3 = _manifest(info, 3, "1.0.6", {"bin.exe": "h3"}, {"bin.exe": 100})
    torrent = {"bytes": b"torrent-v3"}
    monkeypatch.setattr(games_routes, "_fetch_peer_manifest", lambda *a: v3)
    monkeypatch.setattr(games_routes, "_fetch_peer_torrent", lambda *a: torrent["bytes"])
    monkeypatch.setattr(app_state.get().peer_registry, "peers_for_version", lambda gid, key: PEERS)
    monkeypatch.setattr(tm, "_begin_update_prep", MagicMock())

    r = client.post(f"/api/games/{info.id}/update", json={"version_key": "c3"})
    assert r.status_code == 202, r.text
    assert tm.active_update_for(info.id).target_version_label == "1.0.6"

    r = client.post(f"/api/games/{info.id}/update", json={"version_key": "c3"})
    assert r.status_code == 409
    assert "läuft bereits" in r.json()["detail"]
    assert tracker.state(info.id) == "updating"


def test_repair_replaces_running_update_even_with_same_torrent(env):
    tm, info, _tracker = env
    v2 = _manifest(info, 2, "1.0.5", {"bin.exe": "h"}, {"bin.exe": 100})
    first = tm.start_update(info, PEERS, b"torrent-v2", v2)
    fast_path = MagicMock()
    tm._apply_have_pieces_fast_path = fast_path

    second = tm.start_update(info, PEERS, b"torrent-v2", v2, repair=True)

    assert second != first and first not in tm._paused
    rec = tm._paused[second]
    assert rec.repair is True
    fast_path.assert_not_called()  # nothing on disk is trusted
    tm._handles[second].handle.force_recheck.assert_called_once()
    assert tm.get_status(second).repair is True
