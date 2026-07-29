"""Shutdown must persist progress and fast-resume data before the process dies."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deckdrop.network import transfer as transfer_mod
from deckdrop.network.transfer import TransferManager, _Handle, _PersistedRecord

INFO_HASH = "ab" * 20


class save_resume_data_alert:  # noqa: N801 - libtorrent alert name
    def __init__(self, handle: object) -> None:
        self.handle = handle
        self.params = object()


class FakeLt:
    class torrent_handle:  # noqa: N801 - mirrors libtorrent
        save_info_dict = 2
        flush_disk_cache = 1

    def write_resume_data_buf(self, params: object) -> bytes:
        return b"resume-blob"


@pytest.fixture
def transfer(isolated_config, monkeypatch):
    from deckdrop.core import torrent as torrent_mod

    monkeypatch.setattr(torrent_mod, "lan_session", lambda port: MagicMock())
    monkeypatch.setattr(transfer_mod, "_lt", lambda: FakeLt())
    tm = TransferManager(isolated_config)
    tm._session.pop_alerts.return_value = []
    return tm


def _download(transfer, download_id: str = "d1") -> tuple[_PersistedRecord, _Handle]:
    rec = _PersistedRecord(
        download_id=download_id,
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        magnet=f"magnet:?xt=urn:btih:{INFO_HASH}",
        dest_path=str(transfer._cfg.download_dir / "Test"),
        started_at=0.0,
        info_hash=INFO_HASH,
        progress=0.4,
        downloaded_bytes=64_000_000,
        total_bytes=160_000_000,
    )
    transfer._paused[download_id] = rec
    h = _Handle(
        download_id=download_id,
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        handle=MagicMock(),
        dest_path=Path(rec.dest_path),
    )
    transfer._handles[download_id] = h
    transfer._by_info_hash[INFO_HASH] = download_id
    return rec, h


def test_shutdown_persists_progress_to_disk(transfer):
    """The regression: state was only written on pause/error, never on exit."""
    _download(transfer)
    transfer.shutdown(timeout=0.05)

    data = json.loads(transfer._cfg.downloads_state_path.read_text(encoding="utf-8"))
    assert data[0]["downloaded_bytes"] == 64_000_000
    assert data[0]["total_bytes"] == 160_000_000


def test_shutdown_requests_resume_data_for_every_handle(transfer):
    _rec, h1 = _download(transfer, "d1")
    _rec2, h2 = _download(transfer, "d2")
    transfer.shutdown(timeout=0.05)

    h1.handle.save_resume_data.assert_called_once_with(3)
    h2.handle.save_resume_data.assert_called_once_with(3)
    assert transfer._session.pause.called


def test_shutdown_writes_the_blob_it_receives(transfer):
    rec, h = _download(transfer)
    transfer._session.pop_alerts.side_effect = [[save_resume_data_alert(h.handle)], []]

    transfer.shutdown(timeout=0.5)
    assert transfer._resume_store.load_resume(rec.download_id, rec.info_hash) == b"resume-blob"


def test_shutdown_gives_up_within_the_time_budget(transfer, caplog):
    import time

    _download(transfer)
    started = time.monotonic()
    with caplog.at_level("WARNING"):
        transfer.shutdown(timeout=0.2)
    elapsed = time.monotonic() - started

    assert elapsed < 1.5
    assert any("Resume-Daten" in r.message for r in caplog.records)


def test_shutdown_is_idempotent(transfer):
    _rec, h = _download(transfer)
    transfer.shutdown(timeout=0.05)
    transfer.shutdown(timeout=0.05)
    assert h.handle.save_resume_data.call_count == 1


def test_shutdown_without_libtorrent_still_saves_state(transfer, monkeypatch):
    def missing():
        raise RuntimeError("libtorrent is not installed")

    monkeypatch.setattr(transfer_mod, "_lt", missing)
    _download(transfer)
    transfer.shutdown(timeout=0.05)
    assert transfer._cfg.downloads_state_path.exists()
