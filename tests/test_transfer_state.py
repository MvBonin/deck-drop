"""libtorrent state mapping regression tests."""

import pytest

from deckdrop.network.transfer import _map_torrent_state, _phase_from_state


def test_downloading_state_not_mapped_to_done() -> None:
    """libtorrent 2.x: downloading=3, finished=4 (old code treated 3 as done)."""
    lt = pytest.importorskip("libtorrent")
    assert _map_torrent_state(lt, int(lt.torrent_status.downloading)) == "downloading"
    assert _map_torrent_state(lt, int(lt.torrent_status.finished)) == "done"
    assert _map_torrent_state(lt, int(lt.torrent_status.checking_files)) == "checking"
    assert _map_torrent_state(lt, 3) == "downloading"


def test_phase_reports_file_check_instead_of_waiting() -> None:
    """A re-hash after a restart must be distinguishable from "waiting"."""
    assert _phase_from_state("checking", 100, False) == "checking"
    assert _phase_from_state("checking", 100, True) == "verifying"


def test_phase_metadata_only_without_total_bytes() -> None:
    assert _phase_from_state("queued", 0, False) == "metadata"
    assert _phase_from_state("queued", 500, False) == "queued"


def test_phase_passes_through_transfer_states() -> None:
    assert _phase_from_state("downloading", 500, False) == "downloading"
    assert _phase_from_state("seeding", 500, True) == "done"
    assert _phase_from_state("done", 500, True) == "done"
