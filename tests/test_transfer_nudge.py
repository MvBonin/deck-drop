"""Reconnect nudges, stall escalation and poll-loop resilience.

Runs without libtorrent: the transfer module's `_lt()` is faked.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deckdrop.network import transfer as transfer_mod
from deckdrop.network.transfer import (
    DownloadStatus,
    TransferManager,
    _Handle,
    _PersistedRecord,
)


@pytest.fixture
def transfer(isolated_config, monkeypatch):
    from deckdrop.core import torrent as torrent_mod

    monkeypatch.setattr(torrent_mod, "lan_session", lambda port: MagicMock())
    monkeypatch.setattr(transfer_mod, "_lt", lambda: MagicMock())
    tm = TransferManager(isolated_config)
    tm._session.pop_alerts.return_value = []
    return tm


def _setup(transfer, *, peer_address: str = "192.168.1.5") -> tuple[_PersistedRecord, _Handle]:
    rec = _PersistedRecord(
        download_id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        magnet="magnet:?xt=urn:btih:00",
        dest_path=str(transfer._cfg.download_dir / "Test"),
        started_at=0.0,
        peer_address=peer_address,
    )
    transfer._paused["d1"] = rec
    h = _Handle(
        download_id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        handle=MagicMock(),
        dest_path=Path(rec.dest_path),
    )
    transfer._handles["d1"] = h
    return rec, h


def _status(**overrides) -> DownloadStatus:
    base = dict(
        id="d1",
        game_id="g1",
        game_name="Test",
        peer_id="p1",
        peer_name="Host",
        status="downloading",
        progress=0.5,
        speed_bytes_sec=0,
        downloaded_bytes=80_000_000,
        total_bytes=160_000_000,
        num_peers=0,
        bytes_remaining=80_000_000,
    )
    base.update(overrides)
    return DownloadStatus(**base)


def test_reconnects_mid_download_when_no_peer_is_attached(transfer):
    """The old code only nudged in the endgame (< 5 MiB left)."""
    rec, h = _setup(transfer)
    with patch.object(transfer, "_registry_peer_address", return_value=""):
        transfer._ensure_peer_connection(h, _status())
    h.handle.connect_peer.assert_called_once_with((rec.peer_address, transfer._cfg.torrent_port))
    h.handle.force_reannounce.assert_called_once()


def test_reconnect_prefers_the_registry_address(transfer):
    rec, h = _setup(transfer, peer_address="192.168.1.5")
    with patch.object(transfer, "_registry_peer_address", return_value="192.168.1.42"):
        transfer._ensure_peer_connection(h, _status())
    h.handle.connect_peer.assert_called_once_with(("192.168.1.42", transfer._cfg.torrent_port))
    assert rec.peer_address == "192.168.1.42"


def test_no_reconnect_while_a_peer_is_connected(transfer):
    _rec, h = _setup(transfer)
    with patch.object(transfer, "_registry_peer_address", return_value=""):
        transfer._ensure_peer_connection(h, _status(num_peers=2))
    h.handle.connect_peer.assert_not_called()


def test_reconnect_is_throttled(transfer):
    _rec, h = _setup(transfer)
    with patch.object(transfer, "_registry_peer_address", return_value=""):
        transfer._ensure_peer_connection(h, _status())
        transfer._ensure_peer_connection(h, _status())
    assert h.handle.connect_peer.call_count == 1


def test_metadata_wait_escalates_to_an_actionable_error(transfer):
    rec, h = _setup(transfer)
    status = _status(status="queued", total_bytes=0, downloaded_bytes=0, bytes_remaining=0)
    transfer._meta_wait_since["d1"] = time.monotonic() - (transfer_mod._METADATA_ERROR_AFTER + 1)
    with patch.object(transfer, "_registry_peer_address", return_value=""):
        transfer._nudge_metadata(h, status)
    assert rec.error is not None
    assert "Keine Verbindung zum Host" in rec.error
    assert rec.error_recoverable is True


def test_metadata_wait_is_quiet_before_the_timeout(transfer):
    rec, h = _setup(transfer)
    status = _status(status="queued", total_bytes=0, downloaded_bytes=0, bytes_remaining=0)
    with patch.object(transfer, "_registry_peer_address", return_value=""):
        transfer._nudge_metadata(h, status)
    assert rec.error is None
    h.handle.connect_peer.assert_called_once()


def test_metadata_error_names_the_host_when_peers_are_present(transfer):
    rec, h = _setup(transfer)
    status = _status(
        status="queued", total_bytes=0, downloaded_bytes=0, bytes_remaining=0, num_peers=1
    )
    transfer._meta_wait_since["d1"] = time.monotonic() - (transfer_mod._METADATA_ERROR_AFTER + 1)
    with patch.object(transfer, "_registry_peer_address", return_value=""):
        transfer._nudge_metadata(h, status)
    assert "liefert keine Torrent-Daten" in (rec.error or "")


def test_metadata_wait_resets_once_metadata_arrives(transfer):
    _rec, h = _setup(transfer)
    transfer._meta_wait_since["d1"] = 1.0
    with patch.object(transfer, "_registry_peer_address", return_value=""):
        transfer._nudge_metadata(h, _status(status="downloading", total_bytes=160_000_000))
    assert "d1" not in transfer._meta_wait_since


def test_mid_download_stall_reports_after_five_minutes(transfer):
    rec, h = _setup(transfer)
    transfer._last_downloaded["d1"] = 80_000_000
    transfer._last_progress_at["d1"] = time.monotonic() - (transfer_mod._MIDSTALL_ERROR_AFTER + 1)
    transfer._nudge_stalled_download(h, _status(num_peers=1))
    assert "keine Daten vom Host" in (rec.error or "")
    assert rec.error_recoverable is True


def test_mid_download_stall_stays_quiet_without_peers(transfer):
    """A host that is simply switched off must not dead-end the download."""
    rec, h = _setup(transfer)
    transfer._last_downloaded["d1"] = 80_000_000
    transfer._last_progress_at["d1"] = time.monotonic() - (transfer_mod._MIDSTALL_ERROR_AFTER + 1)
    transfer._nudge_stalled_download(h, _status(num_peers=0))
    assert rec.error is None


def test_progress_clears_a_recoverable_error(transfer):
    rec, _h = _setup(transfer)
    rec.error = "Seit 5 Minuten keine Daten vom Host"
    rec.error_recoverable = True
    transfer._last_downloaded["d1"] = 80_000_000
    transfer._track_progress("d1", 90_000_000)
    assert rec.error is None
    assert rec.error_recoverable is False


def test_progress_keeps_a_hard_error(transfer):
    rec, _h = _setup(transfer)
    rec.error = "Nicht genug Speicherplatz."
    rec.error_recoverable = False
    transfer._last_downloaded["d1"] = 80_000_000
    transfer._track_progress("d1", 90_000_000)
    assert rec.error == "Nicht genug Speicherplatz."


async def test_recoverable_error_does_not_pause_the_torrent(transfer):
    rec, h = _setup(transfer)
    rec.error = "Seit 5 Minuten keine Daten vom Host"
    rec.error_recoverable = True
    with patch.object(transfer, "_build_status", return_value=_status(status="error")):
        await transfer._poll_download(h)
    h.handle.pause.assert_not_called()


async def test_hard_error_pauses_the_torrent(transfer):
    rec, h = _setup(transfer)
    rec.error = "Nicht genug Speicherplatz."
    with patch.object(transfer, "_build_status", return_value=_status(status="error")):
        await transfer._poll_download(h)
    h.handle.pause.assert_called_once()


async def test_poll_tick_survives_a_broken_handle(transfer):
    """One raising handle must not kill polling for everything else."""
    _rec, good = _setup(transfer)
    bad = _Handle(
        download_id="d2",
        game_id="g2",
        game_name="Broken",
        peer_id="p1",
        peer_name="Host",
        handle=MagicMock(),
        dest_path=Path("/tmp/broken"),
    )
    transfer._handles["d2"] = bad

    calls: list[str] = []

    def build(h):
        calls.append(h.download_id)
        if h.download_id == "d2":
            raise RuntimeError("libtorrent exploded")
        return _status(num_peers=1)

    with patch.object(transfer, "_build_status", side_effect=build):
        await transfer._poll_once()

    assert set(calls) == {"d1", "d2"}
    assert transfer._handles["d1"] is good
