"""A first download whose version left the network (host patched + published)
can be switched to the new version without losing what is already on disk."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deckdrop.network import transfer as transfer_mod
from deckdrop.network.transfer import TransferManager, _Handle, _PersistedRecord

OLD_HASH = "ab" * 20
NEW_HASH = "cd" * 20


class FakeParams:
    def __init__(self) -> None:
        self.save_path = ""
        self.flags = 0
        self.ti = None


class FakeLt:
    class torrent_flags:  # noqa: N801 - mirrors libtorrent
        paused = 1
        stop_when_ready = 2
        upload_mode = 4
        seed_mode = 8

    def parse_magnet_uri(self, magnet: str) -> FakeParams:
        return FakeParams()


@pytest.fixture
def transfer(isolated_config, monkeypatch):
    from deckdrop.core import torrent as torrent_mod

    monkeypatch.setattr(torrent_mod, "lan_session", lambda port: MagicMock())
    monkeypatch.setattr(transfer_mod, "_lt", lambda: FakeLt())
    return TransferManager(isolated_config)


def _record(transfer, **overrides) -> _PersistedRecord:
    base = dict(
        download_id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        magnet=f"magnet:?xt=urn:btih:{OLD_HASH}",
        dest_path=str(transfer._cfg.download_dir / "Test"),
        started_at=0.0,
        info_hash=OLD_HASH,
        expected_content_hash="c-old",
    )
    base.update(overrides)
    rec = _PersistedRecord(**base)
    transfer._paused[rec.download_id] = rec
    return rec


def _registry(monkeypatch, games: list[dict], online: bool = True):
    from deckdrop.api import state as app_state

    peer = SimpleNamespace(peer_id="p1", name="Host", online=online, games=games)
    registry = MagicMock()
    registry.all.return_value = [peer]
    monkeypatch.setattr(app_state, "get", lambda: SimpleNamespace(peer_registry=registry))


def _game(**kw) -> dict:
    base = dict(id="g1", shareable=True, has_torrent=True, revision=1, version_label="")
    base.update(kw)
    return base


def test_source_info_fine_while_the_version_is_offered(transfer, monkeypatch):
    rec = _record(transfer)
    _registry(monkeypatch, [_game(content_hash="c-old", info_hash=OLD_HASH)])
    assert transfer.source_info(rec) == ("", "", "")


def test_source_info_waiting_while_host_has_unpublished_changes(transfer, monkeypatch):
    rec = _record(transfer)
    _registry(monkeypatch, [_game(content_hash="c-old", shareable=False)])
    assert transfer.source_info(rec) == ("waiting", "", "")


def test_source_info_replaced_after_host_published(transfer, monkeypatch):
    rec = _record(transfer)
    _registry(
        monkeypatch,
        [_game(content_hash="c-new", info_hash=NEW_HASH, revision=2, version_label="1.1")],
    )
    assert transfer.source_info(rec) == ("replaced", "c-new", "1.1")


def test_source_info_quiet_when_host_offline_or_for_updates(transfer, monkeypatch):
    rec = _record(transfer)
    _registry(monkeypatch, [_game(content_hash="c-new", revision=2)], online=False)
    assert transfer.source_info(rec) == ("", "", "")
    upd = _record(transfer, download_id="u1", kind="update")
    _registry(monkeypatch, [_game(content_hash="c-new", revision=2)])
    assert transfer.source_info(upd) == ("", "", "")


def test_status_carries_source_info(transfer, monkeypatch):
    _record(transfer)
    _registry(monkeypatch, [_game(content_hash="c-new", info_hash=NEW_HASH, revision=2)])
    status = transfer.get_status("d1")
    assert status.source_state == "replaced"
    assert status.switch_version_label == "Rev. 2"
    assert transfer._progress_payload(status)["switch_version_key"] == "c-new"


def test_switch_keeps_files_deletes_dropped_ones_and_reattaches(transfer, monkeypatch):
    rec = _record(transfer, user_paused=True)
    dest = transfer._cfg.download_dir / "Test"
    (dest / "data").mkdir(parents=True)
    (dest / "data" / "keep.pak").write_bytes(b"k")
    (dest / "data" / "gone.pak").write_bytes(b"g")
    (dest / "settings.ini").write_bytes(b"s")

    old_handle = MagicMock()
    fs = MagicMock()
    paths = ["Test/data/keep.pak", "Test/data/gone.pak", "Test/settings.ini", "Test/.pad/0"]
    fs.num_files.return_value = len(paths)
    fs.file_path.side_effect = lambda i: paths[i]
    old_handle.torrent_file.return_value.files.return_value = fs
    transfer._handles["d1"] = _Handle(
        download_id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        handle=old_handle,
        dest_path=dest,
    )
    reattached = []
    monkeypatch.setattr(transfer, "_reattach_download", lambda r: reattached.append(r) or True)

    manifest = {
        "files": {"data/keep.pak": "h1", "data/new.pak": "h2"},
        "content": {"ignore": ["settings.ini"]},
    }
    ok = transfer.switch_download_version(
        "d1",
        magnet=f"magnet:?xt=urn:btih:{NEW_HASH}",
        torrent_bytes=b"new-torrent",
        manifest=manifest,
        expected_content_hash="c-new",
        peer_id="p2",
        peer_name="Other",
        peer_address="10.0.0.2",
    )

    assert ok
    assert (dest / "data" / "keep.pak").exists()
    assert not (dest / "data" / "gone.pak").exists()
    assert (dest / "settings.ini").exists()  # ignored by the new version → kept
    assert reattached == [rec]
    assert rec.info_hash == NEW_HASH
    assert rec.expected_content_hash == "c-new"
    assert rec.peer_id == "p2" and rec.peer_address == "10.0.0.2"
    assert rec.user_paused is False
    assert transfer._resume_store.find_metadata("d1", NEW_HASH).read_bytes() == b"new-torrent"
    assert transfer._resume_store.load_manifest("d1") == manifest
    transfer._session.remove_torrent.assert_called_once_with(old_handle)


def test_switch_refused_for_updates(transfer):
    _record(transfer, kind="update")
    assert not transfer.switch_download_version(
        "d1",
        magnet=f"magnet:?xt=urn:btih:{NEW_HASH}",
        torrent_bytes=None,
        manifest=None,
        expected_content_hash="c-new",
        peer_id="p1",
        peer_name="Host",
        peer_address="10.0.0.1",
    )
