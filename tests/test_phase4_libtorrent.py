"""Phase 4 with real libtorrent: save_torrent → find_metadata → params_from_torrent_file
with folder rename (a peer's host folder name differs from the receiver's dest folder)."""

from __future__ import annotations

import pytest

from deckdrop.network.resume import ResumeStore, params_from_torrent_file


def test_save_torrent_then_load_with_folder_rename(tmp_path):
    lt = pytest.importorskip("libtorrent")

    from deckdrop.core.torrent import create_torrent_data, make_magnet

    host_root = tmp_path / "HostFolderName"
    host_root.mkdir()
    (host_root / "game.bin").write_bytes(b"x" * (2 * 1024 * 1024))

    torrent_bytes = create_torrent_data(host_root)
    _magnet, info_hash = make_magnet(torrent_bytes)

    store = ResumeStore(tmp_path / "resume")
    assert store.save_torrent("d1", info_hash, torrent_bytes) is True

    cached = store.find_metadata("d1", info_hash)
    assert cached is not None

    receiver_save_path = tmp_path / "receiver"
    params = params_from_torrent_file(
        lt,
        cached,
        str(receiver_save_path),
        info_hash,
        folder_name="ReceiverFolderName",
    )
    assert params is not None

    fs = params.ti.files()
    top_level = {fs.file_path(i).replace("\\", "/").split("/", 1)[0] for i in range(fs.num_files())}
    assert top_level == {"ReceiverFolderName"}
