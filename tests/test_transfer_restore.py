"""TransferManager: restore downloads after restart."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from deckdrop.network.transfer import TransferManager, _PersistedRecord

lt = pytest.importorskip("libtorrent")


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    from deckdrop.core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)
    tm = TransferManager(cfg)
    return tm


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
