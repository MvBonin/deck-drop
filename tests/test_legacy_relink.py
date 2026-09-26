"""Legacy-ID relink: a copy downloaded with the old "fresh ID" bug must be
recognised as the host's game again – even after the host published an update
that changed the size (Dawnwalker Rev 1 on the Deck, Rev 2 on the PC).
"""

from __future__ import annotations

import threading
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core import debuglog
from deckdrop.core import game as game_mod
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library
from deckdrop.network.peer_registry import PeerEntry, PeerRegistry

GIB = 1024**3


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg._data["paths"]["download_dir"] = str(tmp_path / "games")
    cfg._data["paths"]["torrent_cache"] = str(tmp_path / "torrents")
    cfg._data["paths"]["resume_dir"] = str(tmp_path / "resume")
    cfg_mod.save(cfg)
    lib = Library()
    registry = PeerRegistry()
    registry.set_library(lib)
    tracker = ContentTracker(cfg, lib)
    app_state.init(cfg, lib, registry, content=tracker)
    debuglog.clear()
    return cfg, lib, registry, tracker


def _local_copy(
    cfg,
    lib,
    name="The Blood of Dawnwalker",
    *,
    peer_id="pc",
    peer_name="MVB Desktop",
    size=int(54.6 * GIB),
    folder=None,
):
    game_dir = cfg.download_dir / (folder or name.replace(" ", "_"))
    game_dir.mkdir(parents=True)
    (game_dir / "game.bin").write_bytes(b"x" * 64)
    info = game_mod.create_new(game_dir, name, added_by="MVB Deck")
    info.origin.peer_id = peer_id
    info.origin.peer_name = peer_name
    info.size_bytes = size
    game_mod.save(info)
    lib.add(info)
    return info


def _remote(size=int(58.2 * GIB), revision=2, **extra):
    return {
        "id": "hostdawn",
        "name": "The Blood of Dawnwalker",
        "size_bytes": size,
        "version": 1,
        "revision": revision,
        "version_label": "1.0.5",
        "content_hash": "rev2hash",
        "info_hash": "i" * 40,
        "shareable": True,
        "has_torrent": True,
        **extra,
    }


def _add_peer(registry, games, peer_id="pc", peer_name="MVB Desktop"):
    # Directly, not via upsert_sync: that would schedule a real HTTP fetch.
    registry._peers[peer_id] = PeerEntry(peer_id, peer_name, "192.168.1.10", 7373, games=games)


async def _sync(registry, games, peer_id="pc", peer_name="MVB Desktop"):
    _add_peer(registry, games, peer_id, peer_name)
    with respx.mock:
        respx.get(url__regex=r".*/comments").mock(return_value=httpx.Response(200, json=[]))
        await registry._sync_from_peer(peer_id, games, "192.168.1.10", 7373)


@pytest.mark.asyncio
async def test_relink_despite_size_change_offers_update(env):
    cfg, lib, registry, tracker = env
    info = _local_copy(cfg, lib)
    old_id = info.id

    await _sync(registry, [_remote()])

    assert lib.get(old_id) is None
    relinked = lib.get("hostdawn")
    assert relinked is not None and relinked.path == info.path
    assert game_mod.load_from_path(info.path).id == "hostdawn"
    assert registry.best_update_for("hostdawn", 1)["version_label"] == "1.0.5"
    (net,) = registry.all_network_games()
    assert net["installed"] is True and net["update_available"] is True
    ev = debuglog.events("hostdawn")[0]
    assert ev["kind"] == "relink" and old_id in ev["detail"]

    # "Meine Spiele" now shows the update.
    data = TestClient(create_app()).get("/api/games").json()
    (card,) = [g for g in data if g["id"] == "hostdawn"]
    assert card["update_available"] is True
    assert card["update_version_label"] == "1.0.5"


@pytest.mark.asyncio
async def test_relink_by_peer_name_when_peer_id_changed(env):
    cfg, lib, registry, _tracker = env
    info = _local_copy(cfg, lib, peer_id="old-peer-id")

    await _sync(registry, [_remote()], peer_id="new-peer-id")

    assert lib.get("hostdawn") is not None
    assert lib.get(info.id) is None or info.id == "hostdawn"


@pytest.mark.asyncio
async def test_no_relink_for_other_peer_or_ambiguous_copies(env):
    cfg, lib, registry, _tracker = env
    stranger = _local_copy(cfg, lib, peer_id="someone", peer_name="Other", folder="a")
    await _sync(registry, [_remote()])
    assert lib.get(stranger.id) is stranger  # not from this peer → untouched

    a = _local_copy(cfg, lib, folder="b", size=10 * GIB)
    b = _local_copy(cfg, lib, folder="c", size=20 * GIB)
    await _sync(registry, [_remote()])
    # Two same-named copies from this peer, neither matches size nor hash.
    assert lib.get(a.id) is a and lib.get(b.id) is b
    assert lib.get("hostdawn") is None


@pytest.mark.asyncio
async def test_ambiguous_copies_resolved_by_size(env):
    cfg, lib, registry, _tracker = env
    a = _local_copy(cfg, lib, folder="b", size=10 * GIB)
    _local_copy(cfg, lib, folder="c", size=20 * GIB)
    await _sync(registry, [_remote(size=10 * GIB)])
    assert lib.get("hostdawn") is a


@pytest.mark.asyncio
async def test_relink_during_baseline_hash_stops_it(env, monkeypatch):
    """The Deck case: the legacy copy is still hashing when the relink happens.
    The hash must not save the old ID back, and the game must be updatable."""
    cfg, lib, registry, tracker = env
    info = _local_copy(cfg, lib)
    old_id = info.id

    started = threading.Event()

    def slow_hash(path, progress=None):
        started.set()
        for _ in range(1000):
            progress(1)
            time.sleep(0.01)
        return "never"

    monkeypatch.setattr("deckdrop.core.content_tracker.integrity.hash_file", slow_hash)
    hasher = threading.Thread(target=tracker.ensure_baseline, args=(old_id,))
    hasher.start()
    assert started.wait(5)

    await _sync(registry, [_remote()])
    hasher.join(5)

    assert not hasher.is_alive()
    assert game_mod.load_from_path(info.path).id == "hostdawn"
    assert game_mod.load_from_path(info.path).files == {}
    assert tracker.state("hostdawn") == "unverified"
    assert tracker.get_entry("hostdawn")["pending_hash_reason"] == "relinked"
    assert old_id not in tracker._entries
    assert not (cfg.content_state_dir / f"{old_id}.json").exists()
    assert registry.best_update_for("hostdawn", 1) is not None

    # A later baseline hashes under the new ID.
    monkeypatch.undo()
    tracker.ensure_baseline("hostdawn")
    assert tracker.state("hostdawn") == "clean"
    assert game_mod.load_from_path(info.path).files


def test_debug_shows_same_name_other_id(env):
    cfg, lib, registry, _tracker = env
    info = _local_copy(cfg, lib, peer_id="someone", peer_name="Other")
    _add_peer(registry, [_remote()])

    data = TestClient(create_app()).get("/api/debug").json()

    (g,) = data["games"]
    assert g["id"] == info.id
    assert g["network"] == {"same_id": 0, "best_update": None, "name_matches": ["hostdawn"]}
    (row,) = data["network_games"]
    assert row["id"] == "hostdawn" and row["installed"] is False
    assert row["version_label"] == "1.0.5" and row["peers"] == ["MVB Desktop"]


def test_relink_save_failure_restores_tracker_state(env, monkeypatch):
    from deckdrop.core.library import relink_game_id

    cfg, lib, _registry, tracker = env
    info = _local_copy(cfg, lib)
    old_id = info.id
    tracker.set_state(old_id, "modified", summary={"changed": 1, "removed": 0, "added": 0})

    def boom(_g):
        raise OSError("read-only")

    monkeypatch.setattr(game_mod, "save", boom)
    assert relink_game_id(cfg, lib, info, "hostdawn") is False

    assert info.id == old_id
    assert tracker.state(old_id) == "modified"
    assert "hostdawn" not in tracker._entries
