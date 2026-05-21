"""Transfer helper functions (no libtorrent session required)."""

from deckdrop.network.transfer import (
    _bytes_complete,
    _friendly_transfer_error,
    _map_torrent_state,
    _transfer_error_hint,
)


def test_bytes_complete():
    assert _bytes_complete(100, 100) is True
    assert _bytes_complete(99, 100) is False
    assert _bytes_complete(0, 0) is False
    assert _bytes_complete(90, 100, pieces_missing=0, pieces_total=50) is True
    assert _bytes_complete(90, 100, pieces_missing=1, pieces_total=50) is False


def test_transfer_error_hint_hash():
    msg = _friendly_transfer_error("piece hash check failed")
    assert "Prüfsumme" in msg or "fehl" in msg.lower()
    assert "Host" in _transfer_error_hint(msg)


def test_map_checking_state():
    lt = __import__("pytest").importorskip("libtorrent")
    ts = lt.torrent_status
    assert _map_torrent_state(lt, int(ts.checking_files)) == "checking"
    assert _map_torrent_state(lt, int(ts.downloading)) == "downloading"
