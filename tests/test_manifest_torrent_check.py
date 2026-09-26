"""manifest_torrent_mismatches: peer manifest vs. torrent, without reading files."""

from __future__ import annotations

import pytest


def _game(root, files: dict[str, bytes]):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


@pytest.fixture
def torrent(tmp_path):
    lt = pytest.importorskip("libtorrent")
    from deckdrop.core.torrent import create_torrent_data

    game = tmp_path / "Dawnwalker"
    _game(game, {"bin.exe": b"x" * 3000, "data/a.pak": b"y" * 70000, "debug.log": b"z"})
    ti = lt.torrent_info(lt.bdecode(create_torrent_data(game)))
    return lt, ti


def test_matching_manifest_has_no_mismatches(torrent):
    from deckdrop.core.torrent import manifest_torrent_mismatches

    lt, ti = torrent
    files = {"bin.exe": "h1", "data/a.pak": "h2"}
    sizes = {"bin.exe": 3000, "data/a.pak": 70000}
    # debug.log is only in the (legacy, whole-folder) torrent but ignored by
    # the default patterns (*.log), so it is not a mismatch.
    assert manifest_torrent_mismatches(lt, ti, files, sizes) == []


def test_reports_missing_extra_and_size_mismatch(torrent):
    from deckdrop.core.torrent import manifest_torrent_mismatches

    lt, ti = torrent
    files = {"bin.exe": "h1", "gone.dat": "h3"}  # data/a.pak missing
    sizes = {"bin.exe": 2999, "gone.dat": 1}
    assert manifest_torrent_mismatches(lt, ti, files, sizes) == [
        "bin.exe",
        "data/a.pak",
        "gone.dat",
    ]


def test_ignore_patterns_from_manifest_and_missing_sizes(torrent):
    from deckdrop.core.torrent import manifest_torrent_mismatches

    lt, ti = torrent
    files = {"bin.exe": "h1"}
    # No sizes at all (old manifest) → only the file set is compared.
    assert manifest_torrent_mismatches(lt, ti, files, {}, ["data/**"]) == []
