"""Transfer helper functions (no libtorrent session required)."""

from unittest.mock import MagicMock

from deckdrop.network.transfer import (
    _bytes_complete,
    _friendly_transfer_error,
    _map_torrent_state,
    _pieces_from_status,
    _torrent_is_complete,
    _transfer_error_hint,
)


def test_bytes_complete():
    assert _bytes_complete(100, 100) is True
    assert _bytes_complete(99, 100) is False
    assert _bytes_complete(0, 0) is False
    # Piece kwargs are ignored – byte-only check.
    assert _bytes_complete(90, 100, pieces_missing=0, pieces_total=50) is False
    assert _bytes_complete(0, 0, pieces_missing=0, pieces_total=50) is False


def test_torrent_is_complete():
    lt = MagicMock()
    ts = lt.torrent_status
    ts.downloading = 3
    ts.finished = 4
    ts.seeding = 5

    def status(state, downloaded, total):
        s = MagicMock()
        s.state = state
        s.total_wanted = total
        s.total_done = downloaded
        s.total_wanted_done = downloaded
        s.progress = downloaded / total if total else 0.0
        return s

    # downloading + full bytes → not complete (state matters)
    assert _torrent_is_complete(lt, status(ts.downloading, 100, 100)) is False

    # finished + full bytes → complete
    assert _torrent_is_complete(lt, status(ts.finished, 100, 100)) is True

    # finished + 0 bytes → not complete
    assert _torrent_is_complete(lt, status(ts.finished, 0, 1_000_000)) is False

    # seeding + full bytes → complete
    assert _torrent_is_complete(lt, status(ts.seeding, 500, 500)) is True

    # finished + partial bytes → not complete
    assert _torrent_is_complete(lt, status(ts.finished, 90, 100)) is False


def test_transfer_error_hint_hash():
    msg = _friendly_transfer_error("piece hash check failed")
    assert "Prüfsumme" in msg or "fehl" in msg.lower()
    assert "Host" in _transfer_error_hint(msg)


def test_pieces_from_status_none():
    # When libtorrent doesn't populate pieces (pieces=None), must NOT signal completion.
    s = MagicMock()
    s.num_pieces = 500
    s.pieces = None
    total, missing = _pieces_from_status(s)
    assert total == 0 and missing == 0
    assert _bytes_complete(0, 1_000_000, pieces_missing=missing, pieces_total=total) is False


def test_pieces_from_status_zero_total():
    s = MagicMock()
    s.num_pieces = 0
    total, missing = _pieces_from_status(s)
    assert total == 0 and missing == 0


def test_map_checking_state():
    lt = __import__("pytest").importorskip("libtorrent")
    ts = lt.torrent_status
    assert _map_torrent_state(lt, int(ts.checking_files)) == "checking"
    assert _map_torrent_state(lt, int(ts.downloading)) == "downloading"
