"""Fast resume: blob storage, alert handling and the add-torrent fallback chain.

These tests run without libtorrent installed (CI has no `transfer` extra), so
everything libtorrent-facing goes through a small fake module.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deckdrop.network import resume as resume_mod
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

    def __init__(self) -> None:
        self.info_hash_for_file = INFO_HASH

    def read_resume_data(self, blob: bytes) -> FakeParams:
        return FakeParams("resume")

    def write_resume_data_buf(self, params: object) -> bytes:
        return b"resume-blob"

    def torrent_info(self, path: str) -> object:
        info = MagicMock()
        info.info_hashes.return_value.v1 = self.info_hash_for_file
        return info

    def add_torrent_params(self) -> FakeParams:
        return FakeParams("torrent", info_hash=self.info_hash_for_file)

    def parse_magnet_uri(self, magnet: str) -> FakeParams:
        return FakeParams("magnet")

    def bencode(self, value: object) -> bytes:
        return b"torrent-bytes"

    def create_torrent(self, info: object) -> object:
        return MagicMock()


# Alerts are dispatched by class name, so the stubs carry libtorrent's names.
class save_resume_data_alert:  # noqa: N801 - libtorrent alert name
    def __init__(self, handle: object) -> None:
        self.handle = handle
        self.params = object()


class metadata_received_alert:  # noqa: N801 - libtorrent alert name
    def __init__(self, handle: object) -> None:
        self.handle = handle


class save_resume_data_failed_alert:  # noqa: N801 - libtorrent alert name
    def __init__(self, handle: object) -> None:
        self.handle = handle


class UnknownAlert:
    def __init__(self) -> None:
        self.handle = None


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


def _handle(transfer, rec: _PersistedRecord, handle: object) -> _Handle:
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
    if rec.info_hash:
        transfer._by_info_hash[rec.info_hash] = rec.download_id
    return h


# -- ResumeStore --


def test_resume_blob_round_trip(tmp_path):
    store = ResumeStore(tmp_path / "resume")
    assert store.save_resume("d1", INFO_HASH, b"blob") is True
    assert store.load_resume("d1", INFO_HASH) == b"blob"


def test_resume_blob_not_found_for_other_info_hash(tmp_path):
    store = ResumeStore(tmp_path / "resume")
    store.save_resume("d1", INFO_HASH, b"blob")
    assert store.load_resume("d1", OTHER_HASH) is None


def test_resume_write_leaves_no_tmp_file(tmp_path):
    store = ResumeStore(tmp_path / "resume")
    store.save_resume("d1", INFO_HASH, b"blob")
    assert list((tmp_path / "resume").glob("*.tmp")) == []


def test_discard_removes_every_blob_of_a_download(tmp_path):
    store = ResumeStore(tmp_path / "resume")
    store.save_resume("d1", INFO_HASH, b"blob")
    store.save_metadata("d1", INFO_HASH, b"torrent")
    store.save_resume("d1", OTHER_HASH, b"old")
    store.save_resume("d2", INFO_HASH, b"keep")
    store.discard("d1")
    assert store.load_resume("d1", INFO_HASH) is None
    assert store.find_metadata("d1", INFO_HASH) is None
    assert store.load_resume("d2", INFO_HASH) == b"keep"


def test_missing_blob_returns_none(tmp_path):
    assert ResumeStore(tmp_path / "resume").load_resume("nope", INFO_HASH) is None


# -- libtorrent helpers --


def test_resume_flags_combine_available_bits(fake_lt):
    assert resume_mod.resume_flags(fake_lt) == 3


def test_resume_flags_zero_without_handle_class():
    assert resume_mod.resume_flags(object()) == 0


def test_encode_resume_alert_uses_2x_api(fake_lt):
    alert = save_resume_data_alert(MagicMock())
    assert resume_mod.encode_resume_alert(fake_lt, alert) == b"resume-blob"


def test_encode_resume_alert_falls_back_to_1x_resume_data():
    class Lt1x:
        def bencode(self, value):
            return b"legacy-blob"

    alert = MagicMock(spec=["resume_data"])
    alert.resume_data = {"pieces": []}
    assert resume_mod.encode_resume_alert(Lt1x(), alert) == b"legacy-blob"


def test_encode_resume_alert_returns_none_when_unsupported():
    assert resume_mod.encode_resume_alert(object(), object()) is None


def test_decode_resume_params_sets_path_and_clears_paused_flag(fake_lt):
    params = resume_mod.decode_resume_params(fake_lt, b"blob", "/games")
    assert params is not None
    assert params.save_path == "/games"
    # paused / stop_when_ready / upload_mode / seed_mode must all be gone,
    # otherwise the restored torrent comes back paused and looks frozen.
    assert params.flags == 0


def test_decode_resume_params_returns_none_on_unreadable_blob():
    class Lt:
        def read_resume_data(self, blob):
            raise RuntimeError("corrupt")

    assert resume_mod.decode_resume_params(Lt(), b"junk", "/games") is None


def test_decode_resume_params_returns_none_without_api():
    assert resume_mod.decode_resume_params(object(), b"blob", "/games") is None


def test_params_from_torrent_file_rejects_info_hash_mismatch(fake_lt, tmp_path):
    path = tmp_path / "cached.torrent"
    path.write_bytes(b"data")
    assert resume_mod.params_from_torrent_file(fake_lt, path, "/games", OTHER_HASH) is None


def test_params_from_torrent_file_accepts_matching_hash(fake_lt, tmp_path):
    path = tmp_path / "cached.torrent"
    path.write_bytes(b"data")
    params = resume_mod.params_from_torrent_file(fake_lt, path, "/games", INFO_HASH)
    assert params is not None
    assert params.save_path == "/games"


# -- TransferManager: fallback chain --


def test_params_prefer_resume_blob(transfer, fake_lt):
    rec = _record(transfer)
    transfer._resume_store.save_resume(rec.download_id, rec.info_hash, b"blob")
    params, source = transfer._params_for_record(fake_lt, rec, Path("/games"))
    assert source == "resume"
    assert params.source == "resume"


def test_params_fall_back_to_cached_metadata(transfer, fake_lt):
    rec = _record(transfer)
    transfer._resume_store.save_metadata(rec.download_id, rec.info_hash, b"torrent")
    _params, source = transfer._params_for_record(fake_lt, rec, Path("/games"))
    assert source == "torrent"


def test_params_fall_back_to_shared_torrent_cache(transfer, fake_lt):
    rec = _record(transfer)
    cache = transfer._cfg.torrent_cache
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{rec.game_id}.torrent").write_bytes(b"data")
    _params, source = transfer._params_for_record(fake_lt, rec, Path("/games"))
    assert source == "cache"


def test_params_fall_back_to_magnet(transfer, fake_lt):
    rec = _record(transfer)
    params, source = transfer._params_for_record(fake_lt, rec, Path("/games"))
    assert source == "magnet"
    assert params.save_path == "/games"


def test_unreadable_resume_blob_is_dropped_and_magnet_used(transfer, fake_lt, monkeypatch):
    rec = _record(transfer)
    transfer._resume_store.save_resume(rec.download_id, rec.info_hash, b"blob")
    monkeypatch.setattr(resume_mod, "decode_resume_params", lambda *a, **k: None)
    _params, source = transfer._params_for_record(fake_lt, rec, Path("/games"))
    assert source == "magnet"
    assert transfer._resume_store.load_resume(rec.download_id, rec.info_hash) is None


def test_reattach_uses_resume_blob_without_parsing_the_magnet(transfer, monkeypatch):
    rec = _record(transfer)
    transfer._resume_store.save_resume(rec.download_id, rec.info_hash, b"blob")

    def fail(*_args, **_kwargs):
        raise AssertionError("magnet must not be parsed when resume data exists")

    monkeypatch.setattr(transfer_mod, "_parse_magnet_params", fail)
    assert transfer._reattach_download(rec) is True
    assert rec.download_id in transfer._handles


# -- TransferManager: alerts --


def test_resume_alert_persists_blob(transfer):
    rec = _record(transfer)
    handle = MagicMock()
    _handle(transfer, rec, handle)
    transfer._pending_resume[rec.download_id] = 0.0
    transfer._session.pop_alerts.return_value = [save_resume_data_alert(handle)]

    assert transfer._pump_alerts() == 1
    assert transfer._resume_store.load_resume(rec.download_id, rec.info_hash) == b"resume-blob"
    assert rec.has_resume_data is True
    assert transfer._pending_resume == {}


def test_failed_resume_alert_clears_pending(transfer):
    rec = _record(transfer)
    handle = MagicMock()
    _handle(transfer, rec, handle)
    transfer._pending_resume[rec.download_id] = 0.0
    transfer._session.pop_alerts.return_value = [save_resume_data_failed_alert(handle)]

    transfer._pump_alerts()
    assert transfer._pending_resume == {}
    assert transfer._resume_store.load_resume(rec.download_id, rec.info_hash) is None


def test_unknown_alerts_are_ignored(transfer):
    transfer._session.pop_alerts.return_value = [UnknownAlert(), UnknownAlert()]
    assert transfer._pump_alerts() == 0


def test_pump_never_raises_when_session_fails(transfer):
    transfer._session.pop_alerts.side_effect = RuntimeError("boom")
    assert transfer._pump_alerts() == 0


def test_metadata_alert_caches_torrent_and_saves_resume(transfer):
    rec = _record(transfer)
    handle = MagicMock()
    _handle(transfer, rec, handle)
    transfer._meta_wait_since[rec.download_id] = 0.0
    transfer._session.pop_alerts.return_value = [metadata_received_alert(handle)]

    transfer._pump_alerts()
    assert transfer._resume_store.find_metadata(rec.download_id, rec.info_hash) is not None
    assert handle.save_resume_data.called
    assert rec.download_id not in transfer._meta_wait_since


def test_request_resume_save_passes_flags(transfer):
    rec = _record(transfer)
    handle = MagicMock()
    h = _handle(transfer, rec, handle)
    assert transfer._request_resume_save(h, force=True) is True
    handle.save_resume_data.assert_called_once_with(3)
    assert rec.download_id in transfer._pending_resume


def test_request_resume_save_respects_need_save_resume_data(transfer):
    rec = _record(transfer)
    handle = MagicMock()
    handle.need_save_resume_data.return_value = False
    h = _handle(transfer, rec, handle)
    assert transfer._request_resume_save(h) is False
    handle.save_resume_data.assert_not_called()


def test_pause_saves_resume_data_first(transfer):
    rec = _record(transfer)
    handle = MagicMock()
    _handle(transfer, rec, handle)
    transfer._session.pop_alerts.return_value = []

    assert transfer.pause_download(rec.download_id) is True
    assert handle.save_resume_data.called
    assert handle.pause.called


def test_remove_download_discards_resume_blobs(transfer):
    rec = _record(transfer)
    transfer._resume_store.save_resume(rec.download_id, rec.info_hash, b"blob")
    transfer._resume_store.save_metadata(rec.download_id, rec.info_hash, b"torrent")

    assert transfer.remove_download(rec.download_id) is True
    assert transfer._resume_store.load_resume(rec.download_id, rec.info_hash) is None
    assert transfer._resume_store.find_metadata(rec.download_id, rec.info_hash) is None
    assert transfer._by_info_hash == {}


def test_upgrade_discards_blobs_of_the_old_info_hash(transfer):
    rec = _record(transfer)
    _handle(transfer, rec, MagicMock())
    transfer._resume_store.save_resume(rec.download_id, rec.info_hash, b"blob")

    magnet = "magnet:?xt=urn:btih:" + OTHER_HASH
    assert transfer.upgrade_download(rec.download_id, magnet, OTHER_HASH)
    assert transfer._resume_store.load_resume(rec.download_id, INFO_HASH) is None
    assert rec.info_hash == OTHER_HASH
    assert rec.has_resume_data is False
