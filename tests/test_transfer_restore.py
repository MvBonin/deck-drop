"""TransferManager: restore downloads after restart."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from deckdrop.network.transfer import DownloadStatus, TransferManager, _PersistedRecord


def _record(transfer, **overrides) -> _PersistedRecord:
    base = dict(
        download_id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        magnet="magnet:?xt=urn:btih:00",
        dest_path=str(transfer._cfg.download_dir / "game"),
        started_at=0.0,
    )
    base.update(overrides)
    rec = _PersistedRecord(**base)
    transfer._paused[rec.download_id] = rec
    return rec


@pytest.fixture
def transfer(isolated_config, monkeypatch):
    from deckdrop.core import torrent as torrent_mod

    monkeypatch.setattr(torrent_mod, "lan_session", lambda port: MagicMock())
    return TransferManager(isolated_config)


def test_paused_status_queued_without_handle(transfer):
    rec = _PersistedRecord(
        download_id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        magnet="magnet:?xt=urn:btih:00",
        dest_path="/tmp/game",
        started_at=0.0,
        user_paused=False,
    )
    transfer._paused["d1"] = rec
    status = transfer.get_status("d1")
    assert status is not None
    assert status.status == "queued"


def test_restore_active_downloads_skips_user_paused(transfer):
    rec = _PersistedRecord(
        download_id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        magnet="magnet:?xt=urn:btih:00",
        dest_path=str(transfer._cfg.download_dir / "game"),
        started_at=0.0,
        user_paused=True,
    )
    transfer._paused["d1"] = rec
    with patch.object(transfer, "_reattach_download", return_value=True) as mock_reattach:
        assert transfer.restore_active_downloads() == 0
        mock_reattach.assert_not_called()


def test_restore_active_downloads_reattaches(transfer):
    rec = _PersistedRecord(
        download_id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        magnet="magnet:?xt=urn:btih:00",
        dest_path=str(transfer._cfg.download_dir / "game"),
        started_at=0.0,
        peer_address="192.168.1.5",
    )
    transfer._paused["d1"] = rec
    with patch.object(transfer, "_reattach_download", return_value=True) as mock_reattach:
        assert transfer.restore_active_downloads() == 1
        mock_reattach.assert_called_once_with(rec)


def test_rate_limit_bytes():
    assert TransferManager._rate_limit_bytes(0) == 0
    assert TransferManager._rate_limit_bytes(100) == 102400


def test_restore_retries_record_with_stale_error_once(transfer):
    """A leftover error from yesterday must not mean "do nothing today"."""
    rec = _record(transfer, error="Host weg", error_recoverable=True)
    with patch.object(transfer, "_reattach_download", return_value=True) as mock_reattach:
        assert transfer.restore_active_downloads() == 1
        mock_reattach.assert_called_once_with(rec)
    assert rec.error is None
    assert rec.restore_attempts == 1


def test_restore_skips_errored_record_on_second_attempt(transfer):
    rec = _record(transfer, error="Host weg", restore_attempts=1)
    with patch.object(transfer, "_reattach_download", return_value=True) as mock_reattach:
        assert transfer.restore_active_downloads() == 0
        mock_reattach.assert_not_called()
    assert rec.error == "Host weg"


def test_restore_uses_current_registry_address(transfer):
    rec = _record(transfer, peer_address="192.168.1.5")
    with (
        patch.object(transfer, "_registry_peer_address", return_value="192.168.1.42"),
        patch.object(transfer, "_reattach_download", return_value=True),
    ):
        assert transfer.restore_active_downloads() == 1
    assert rec.peer_address == "192.168.1.42"


def test_registry_address_ignored_when_peer_unknown(transfer):
    rec = _record(transfer, peer_address="192.168.1.5")
    with patch.object(transfer, "_registry_peer_address", return_value=""):
        assert transfer._peer_address_for(rec) == "192.168.1.5"


def test_sync_keeps_known_progress_while_metadata_missing(transfer):
    """The remembered "64 MB" must survive the metadata phase after a restart."""
    rec = _record(transfer, progress=0.4, downloaded_bytes=64_000_000, total_bytes=160_000_000)
    status = DownloadStatus(
        id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        status="queued",
        progress=0.0,
        speed_bytes_sec=0,
        downloaded_bytes=0,
        total_bytes=0,
        num_peers=0,
    )
    transfer._sync_rec_from_status(rec, status)
    assert rec.downloaded_bytes == 64_000_000
    assert rec.total_bytes == 160_000_000
    assert rec.progress == 0.4


def test_sync_updates_progress_once_metadata_arrived(transfer):
    rec = _record(transfer, downloaded_bytes=64_000_000, total_bytes=160_000_000)
    status = DownloadStatus(
        id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        status="downloading",
        progress=0.5,
        speed_bytes_sec=10,
        downloaded_bytes=80_000_000,
        total_bytes=160_000_000,
        num_peers=1,
    )
    transfer._sync_rec_from_status(rec, status)
    assert rec.downloaded_bytes == 80_000_000
    assert rec.progress == 0.5


def test_state_round_trip_keeps_resume_fields(transfer, isolated_config, monkeypatch):
    from deckdrop.core import torrent as torrent_mod

    _record(
        transfer,
        info_hash="ab" * 20,
        downloaded_bytes=64_000_000,
        total_bytes=160_000_000,
        has_resume_data=True,
        restore_attempts=1,
    )
    transfer._save_state()

    monkeypatch.setattr(torrent_mod, "lan_session", lambda port: MagicMock())
    reloaded = TransferManager(isolated_config)
    rec = reloaded._paused["d1"]
    assert rec.downloaded_bytes == 64_000_000
    assert rec.has_resume_data is True
    assert rec.restore_attempts == 1
    assert reloaded._by_info_hash["ab" * 20] == "d1"


def test_load_state_ignores_unknown_keys(transfer):
    import json

    path = transfer._cfg.downloads_state_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [
                {
                    "download_id": "d9",
                    "game_id": "g9",
                    "game_name": "Old",
                    "peer_id": "p9",
                    "peer_name": "Host",
                    "magnet": "magnet:?xt=urn:btih:00",
                    "dest_path": "/tmp/old",
                    "started_at": 0.0,
                    "from_a_future_version": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    transfer._paused.clear()
    transfer._load_state()
    assert "d9" in transfer._paused


def test_save_state_is_throttled(transfer):
    with patch.object(transfer, "_save_state") as mock_save:
        transfer._save_state_throttled()
        transfer._save_state_throttled()
        assert mock_save.call_count == 1
