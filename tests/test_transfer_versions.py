"""Phase 4 TransferManager/ResumeStore extensions: multi-peer, manifest/torrent
fetch plumbing, and the content_hash upgrade guard.

Runs without libtorret installed (fake `lt` module), matching the pattern in
test_transfer_resume.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deckdrop.network import transfer as transfer_mod
from deckdrop.network.resume import ResumeStore
from deckdrop.network.transfer import TransferManager, _Handle, _PersistedRecord

INFO_HASH = "ab" * 20
OTHER_HASH = "cd" * 20


class FakeParams:
    def __init__(self, source: str, info_hash: str = INFO_HASH) -> None:
        self.source = source
        self.save_path = ""
        self.flags = 0b1111
        self.info_hash = info_hash
        self.ti = None


class FakeTorrentFlags:
    paused = 0b0001
    stop_when_ready = 0b0010
    upload_mode = 0b0100
    seed_mode = 0b1000


class FakeTorrentHandle:
    save_info_dict = 2
    flush_disk_cache = 1


class FakeLt:
    torrent_flags = FakeTorrentFlags
    torrent_handle = FakeTorrentHandle

    def torrent_info(self, path: str) -> object:
        info = MagicMock()
        info.info_hashes.return_value.v1 = INFO_HASH
        return info

    def add_torrent_params(self) -> FakeParams:
        return FakeParams("torrent", info_hash=INFO_HASH)

    def parse_magnet_uri(self, magnet: str) -> FakeParams:
        return FakeParams("magnet")

    def bencode(self, value: object) -> bytes:
        return b"torrent-bytes"


@pytest.fixture
def fake_lt() -> FakeLt:
    return FakeLt()


@pytest.fixture
def transfer(isolated_config, monkeypatch, fake_lt):
    from deckdrop.core import torrent as torrent_mod

    monkeypatch.setattr(torrent_mod, "lan_session", lambda port: MagicMock())
    monkeypatch.setattr(transfer_mod, "_lt", lambda: fake_lt)
    return TransferManager(isolated_config)


def _record(transfer, **overrides) -> _PersistedRecord:
    base = dict(
        download_id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        magnet=f"magnet:?xt=urn:btih:{INFO_HASH}",
        dest_path=str(transfer._cfg.download_dir / "Test"),
        started_at=0.0,
        info_hash=INFO_HASH,
    )
    base.update(overrides)
    rec = _PersistedRecord(**base)
    transfer._paused[rec.download_id] = rec
    return rec


# -- ResumeStore: manifest + save_torrent --


def test_manifest_round_trip(tmp_path):
    store = ResumeStore(tmp_path / "resume")
    manifest = {"id": "g1", "content": {"revision": 2}, "files": {"a.bin": "hash"}}
    assert store.save_manifest("d1", manifest) is True
    assert store.load_manifest("d1") == manifest


def test_load_manifest_missing_returns_none(tmp_path):
    store = ResumeStore(tmp_path / "resume")
    assert store.load_manifest("nope") is None


def test_save_torrent_is_found_by_find_metadata(tmp_path):
    store = ResumeStore(tmp_path / "resume")
    assert store.save_torrent("d1", INFO_HASH, b"torrent-bytes") is True
    found = store.find_metadata("d1", INFO_HASH)
    assert found is not None
    assert found.read_bytes() == b"torrent-bytes"


def test_discard_also_removes_manifest(tmp_path):
    store = ResumeStore(tmp_path / "resume")
    store.save_manifest("d1", {"id": "g1"})
    store.save_torrent("d1", INFO_HASH, b"data")
    store.discard("d1")
    assert store.load_manifest("d1") is None
    assert store.find_metadata("d1", INFO_HASH) is None


# -- start_download: torrent_bytes / manifest / extra peers --


def test_start_download_saves_torrent_bytes_and_manifest(transfer, tmp_path):
    dest = tmp_path / "games" / "Test"
    manifest = {"id": "g1", "content": {"revision": 2}}
    did = transfer.start_download(
        game_id="g1",
        game_name="Test",
        magnet=f"magnet:?xt=urn:btih:{INFO_HASH}",
        peer_id="p1",
        peer_name="Host",
        peer_address="192.168.1.5",
        dest_path=dest,
        torrent_bytes=b"torrent-bytes",
        manifest=manifest,
    )
    assert transfer._resume_store.find_metadata(did, INFO_HASH) is not None
    assert transfer._resume_store.load_manifest(did) == manifest


def test_start_download_connects_extra_peers(transfer, tmp_path, monkeypatch):
    from deckdrop.api import state as app_state

    peer2 = SimpleNamespace(peer_id="p2", address="192.168.1.9", online=True)
    registry = MagicMock()
    registry.get.side_effect = lambda pid: peer2 if pid == "p2" else None
    monkeypatch.setattr(app_state, "get", lambda: SimpleNamespace(peer_registry=registry))

    dest = tmp_path / "games" / "Test"
    transfer.start_download(
        game_id="g1",
        game_name="Test",
        magnet=f"magnet:?xt=urn:btih:{INFO_HASH}",
        peer_id="p1",
        peer_name="Host",
        peer_address="192.168.1.5",
        dest_path=dest,
        extra_peer_ids=["p2"],
    )
    handle = transfer._session.add_torrent.return_value
    calls = [c.args[0] for c in handle.connect_peer.call_args_list]
    assert ("192.168.1.5", transfer._cfg.torrent_port) in calls
    assert ("192.168.1.9", transfer._cfg.torrent_port) in calls


# -- _maybe_upgrade_from_peer: content_hash guard --


def _fake_handle(transfer, rec):
    handle = MagicMock()
    h = _Handle(
        download_id=rec.download_id,
        game_id=rec.game_id,
        game_name=rec.game_name,
        peer_id=rec.peer_id,
        peer_name=rec.peer_name,
        handle=handle,
        dest_path=Path(rec.dest_path),
    )
    transfer._handles[rec.download_id] = h
    return h


def test_upgrade_blocked_when_content_hash_differs(transfer, monkeypatch):
    from deckdrop.api import state as app_state

    rec = _record(transfer, expected_content_hash="hash-a", info_hash=INFO_HASH)
    h = _fake_handle(transfer, rec)

    peer = SimpleNamespace(
        online=True,
        games=[
            {
                "id": "g1",
                "has_torrent": True,
                "info_hash": OTHER_HASH,
                "content_hash": "hash-b",
            }
        ],
        address="192.168.1.5",
        port=7373,
    )
    registry = MagicMock()
    registry.get.return_value = peer
    monkeypatch.setattr(app_state, "get", lambda: SimpleNamespace(peer_registry=registry))

    upgraded = asyncio.run(transfer._maybe_upgrade_from_peer(h))
    assert upgraded is False
    assert rec.info_hash == INFO_HASH  # unchanged


def test_upgrade_allowed_when_content_hash_matches(transfer, monkeypatch):
    from deckdrop.api import state as app_state

    rec = _record(transfer, expected_content_hash="hash-a", info_hash=INFO_HASH)
    h = _fake_handle(transfer, rec)

    peer = SimpleNamespace(
        online=True,
        games=[
            {
                "id": "g1",
                "has_torrent": True,
                "info_hash": OTHER_HASH,
                "content_hash": "hash-a",
            }
        ],
        address="192.168.1.5",
        port=7373,
    )
    registry = MagicMock()
    registry.get.return_value = peer
    monkeypatch.setattr(app_state, "get", lambda: SimpleNamespace(peer_registry=registry))
    monkeypatch.setattr(
        transfer_mod,
        "_fetch_peer_magnet",
        lambda address, port, game_id: (f"magnet:?xt=urn:btih:{OTHER_HASH}", OTHER_HASH),
    )

    upgraded = asyncio.run(transfer._maybe_upgrade_from_peer(h))
    assert upgraded is True
    assert rec.info_hash == OTHER_HASH


def test_upgrade_allowed_when_remote_has_no_content_hash_legacy(transfer, monkeypatch):
    """Legacy peer without [content] in its manifest – info_hash alone still upgrades."""
    from deckdrop.api import state as app_state

    rec = _record(transfer, expected_content_hash="hash-a", info_hash=INFO_HASH)
    h = _fake_handle(transfer, rec)

    peer = SimpleNamespace(
        online=True,
        games=[{"id": "g1", "has_torrent": True, "info_hash": OTHER_HASH, "content_hash": ""}],
        address="192.168.1.5",
        port=7373,
    )
    registry = MagicMock()
    registry.get.return_value = peer
    monkeypatch.setattr(app_state, "get", lambda: SimpleNamespace(peer_registry=registry))
    monkeypatch.setattr(
        transfer_mod,
        "_fetch_peer_magnet",
        lambda address, port, game_id: (f"magnet:?xt=urn:btih:{OTHER_HASH}", OTHER_HASH),
    )

    upgraded = asyncio.run(transfer._maybe_upgrade_from_peer(h))
    assert upgraded is True


# -- _register_downloaded_game: apply manifest + baseline --


def test_register_downloaded_game_applies_manifest_and_baselines(transfer, tmp_path, monkeypatch):
    from deckdrop.api import state as app_state
    from deckdrop.core import game as game_mod

    dest = tmp_path / "games" / "Stardew_Valley"
    dest.mkdir(parents=True)
    (dest / "game.bin").write_bytes(b"x" * 42)

    manifest = {
        "id": "hostgame1",
        "name": "Stardew Valley",
        "platform": "linux",
        "added_by": "alice",
        "added_at": "2026-09-01T00:00:00+00:00",
        "content": {
            "revision": 2,
            "version_label": "1.1",
            "note": "",
            "created_by": "alice",
            "created_at": "2026-09-01T00:00:00+00:00",
            "updated_by": "alice",
            "updated_at": "2026-09-02T00:00:00+00:00",
            "content_hash": "deadbeef",
            "ignore": [],
        },
        "history": [],
        "files": {"game.bin": "somehash"},
        "sizes": {"game.bin": 42},
    }

    h = _Handle(
        download_id="d1",
        game_id="hostgame1",
        game_name="Stardew Valley",
        peer_id="peer1",
        peer_name="PC1",
        handle=MagicMock(),
        dest_path=dest,
    )
    transfer._resume_store.save_manifest(h.download_id, manifest)

    tracker = MagicMock()
    peer = SimpleNamespace(address="192.168.1.5", port=7373, games=[])
    registry = MagicMock()
    registry.get.return_value = peer
    state = SimpleNamespace(peer_registry=registry, get_content_tracker=lambda: tracker)
    monkeypatch.setattr(app_state, "get", lambda: state)

    transfer._register_downloaded_game(h)

    info = game_mod.load_from_path(dest)
    assert info is not None
    assert info.id == "hostgame1"
    assert info.content.revision == 2
    assert info.content.version_label == "1.1"
    assert info.files == {"game.bin": "somehash"}
    # apply_manifest(keep_local_meta=False) kept the host's created_by/updated_by.
    assert info.content.created_by == "alice"
    tracker.ensure_baseline.assert_called_once_with("hostgame1")


def _handle_listing(root: str, sizes: dict[str, int]) -> MagicMock:
    """libtorrent handle mock whose torrent_file() lists exactly `sizes`."""
    items = [(f"{root}/{rel}", size) for rel, size in sorted(sizes.items())]
    fs = SimpleNamespace(
        num_files=lambda: len(items),
        file_path=lambda i: items[i][0],
        file_size=lambda i: items[i][1],
        file_flags=lambda i: 0,
    )
    ti = MagicMock()
    ti.files.return_value = fs
    handle = MagicMock()
    handle.torrent_file.return_value = ti
    return handle


@pytest.mark.parametrize(
    ("torrent_sizes", "expect_files", "expect_rehash"),
    [
        ({"game.bin": 42}, {"game.bin": "somehash"}, False),
        ({"game.bin": 41}, {}, True),  # size differs → manifest not trusted
    ],
)
def test_register_downloaded_game_checks_manifest_against_torrent(
    transfer, tmp_path, monkeypatch, torrent_sizes, expect_files, expect_rehash
):
    from deckdrop.api import state as app_state
    from deckdrop.core import debuglog
    from deckdrop.core import game as game_mod

    dest = tmp_path / "games" / "Dawnwalker"
    dest.mkdir(parents=True)
    (dest / "game.bin").write_bytes(b"x" * 42)
    manifest = {
        "id": "hostgame1",
        "name": "Dawnwalker",
        "content": {"revision": 2, "content_hash": "c0ffee", "ignore": []},
        "history": [],
        "files": {"game.bin": "somehash"},
        "sizes": {"game.bin": 42},
    }
    h = _Handle(
        download_id="d1",
        game_id="hostgame1",
        game_name="Dawnwalker",
        peer_id="peer1",
        peer_name="PC1",
        handle=_handle_listing("Dawnwalker", torrent_sizes),
        dest_path=dest,
    )
    transfer._resume_store.save_manifest(h.download_id, manifest)
    tracker = MagicMock()
    registry = MagicMock()
    registry.get.return_value = SimpleNamespace(address="192.168.1.5", port=7373, games=[])
    state = SimpleNamespace(peer_registry=registry, get_content_tracker=lambda: tracker)
    monkeypatch.setattr(app_state, "get", lambda: state)
    monkeypatch.setattr(
        transfer_mod,
        "_lt",
        lambda: SimpleNamespace(file_storage=SimpleNamespace(flag_pad_file=1)),
    )
    debuglog.clear()

    needs_hash = transfer._register_downloaded_game(h)

    info = game_mod.load_from_path(dest)
    assert info.files == expect_files
    assert info.content.revision == 2  # rest of the manifest is still applied
    assert needs_hash is expect_rehash
    if expect_rehash:
        tracker.request_rehash.assert_called_once_with("hostgame1", "manifest_mismatch")
        assert debuglog.events("hostgame1")[0]["reason"] == "manifest_mismatch"
    else:
        tracker.request_rehash.assert_not_called()
        assert debuglog.events("hostgame1")[0]["reason"] == "manifest_ok"
