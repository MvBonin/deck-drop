"""libtorrent state mapping regression tests."""

import libtorrent as lt

from deckdrop.network.transfer import _map_torrent_state


def test_downloading_state_not_mapped_to_done() -> None:
    """libtorrent 2.x: downloading=3, finished=4 (old code treated 3 as done)."""
    assert _map_torrent_state(lt, int(lt.torrent_status.downloading)) == "downloading"
    assert _map_torrent_state(lt, int(lt.torrent_status.finished)) == "done"
    assert _map_torrent_state(lt, 3) == "downloading"
