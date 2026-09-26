"""Phase 6 "have_pieces" fast path: build_have_pieces against a real
torrent_info (no network needed – just create_torrent_data + local files)."""

from __future__ import annotations

import pytest


def _make_game(root, files: dict[str, bytes]):
    for rel, data in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _piece_range_for(ti, folder_name: str, rel: str):
    """libtorrent reorders/pads `file_storage` internally (e.g. by size, to
    minimize padding) – never assume the caller's file order survives into
    the torrent. Look the file up by its (already-retargeted) path instead.
    """
    from deckdrop.core.content import pieces_for_file

    fs = ti.files()
    for i in range(fs.num_files()):
        if fs.file_path(i).replace("\\", "/") == f"{folder_name}/{rel}":
            return pieces_for_file(fs.file_offset(i), fs.file_size(i), ti.piece_length())
    raise AssertionError(f"{rel} not found in torrent")


def test_build_have_pieces_trusts_unchanged_verifies_changed(tmp_path):
    lt = pytest.importorskip("libtorrent")

    from deckdrop.core.torrent import build_have_pieces, create_torrent_data, retarget_root

    game = tmp_path / "Game"
    piece = 1024 * 1024
    unchanged_bytes = b"A" * (3 * piece)
    changed_bytes_old = b"B" * (2 * piece)
    _make_game(
        game,
        {
            "unchanged.bin": unchanged_bytes,
            "changed.bin": changed_bytes_old,
        },
    )

    torrent_bytes = create_torrent_data(game, files=["unchanged.bin", "changed.bin"])

    # Simulate the update: change.bin's *content* changes on the remote side
    # (new torrent hashes), but locally we still only have the old bytes.
    changed_bytes_new = bytearray(changed_bytes_old)
    changed_bytes_new[10:20] = b"Z" * 10  # inside the first piece of changed.bin
    (game / "changed.bin").write_bytes(bytes(changed_bytes_new))
    torrent_bytes_v2 = create_torrent_data(game, files=["unchanged.bin", "changed.bin"])
    # Revert the local file back to what it actually is on disk before the
    # update starts (still old content) – the new torrent describes the goal.
    (game / "changed.bin").write_bytes(changed_bytes_old)

    ti = lt.torrent_info(lt.bdecode(torrent_bytes_v2))
    retarget_root(lt, ti, game.name)

    have = build_have_pieces(lt, ti, game, ["unchanged.bin"], ["changed.bin"])

    assert len(have) == ti.num_pieces()
    unchanged_pieces = _piece_range_for(ti, game.name, "unchanged.bin")
    changed_pieces = list(_piece_range_for(ti, game.name, "changed.bin"))
    # unchanged.bin: all pieces trusted without reading.
    assert all(have[p] for p in unchanged_pieces)
    # changed.bin's first piece has different content on disk than what the
    # new torrent expects -> not marked present. Its second piece (bytes
    # 11-20 untouched) still matches -> marked present even though the file
    # as a whole is "changed".
    assert have[changed_pieces[0]] is False
    assert have[changed_pieces[1]] is True

    del torrent_bytes  # unused reference kept for clarity


def test_build_have_pieces_skips_pad_and_added_files(tmp_path):
    lt = pytest.importorskip("libtorrent")

    from deckdrop.core.torrent import build_have_pieces, create_torrent_data, retarget_root

    game = tmp_path / "Game"
    piece = 1024 * 1024
    _make_game(
        game,
        {
            "small.bin": b"A" * 100,  # forces a pad file to the next piece
            "new.bin": b"C" * piece,
        },
    )
    torrent_bytes = create_torrent_data(game, files=["small.bin", "new.bin"])
    ti = lt.torrent_info(lt.bdecode(torrent_bytes))
    retarget_root(lt, ti, game.name)

    # small.bin unchanged, new.bin is genuinely new (not in either set).
    have = build_have_pieces(lt, ti, game, ["small.bin"], [])

    small_piece = _piece_range_for(ti, game.name, "small.bin")[0]
    new_piece = _piece_range_for(ti, game.name, "new.bin")[0]
    assert have[small_piece] is True  # small.bin's only piece, trusted
    assert have[new_piece] is False  # never touched, stays False


def test_build_have_pieces_missing_local_file_stays_false(tmp_path):
    lt = pytest.importorskip("libtorrent")

    from deckdrop.core.torrent import build_have_pieces, create_torrent_data, retarget_root

    game = tmp_path / "Game"
    _make_game(game, {"gone.bin": b"X" * (1024 * 1024)})
    torrent_bytes = create_torrent_data(game, files=["gone.bin"])
    ti = lt.torrent_info(lt.bdecode(torrent_bytes))
    retarget_root(lt, ti, game.name)

    (game / "gone.bin").unlink()

    have = build_have_pieces(lt, ti, game, [], ["gone.bin"])
    assert have == [False]


def test_build_have_pieces_locally_corrupted_unchanged_file_excluded(tmp_path):
    """The caller (TransferManager) must exclude a file the local tracker
    flagged as changed/removed from `unchanged_rels` even if its manifest hash
    matches the old one, otherwise a locally corrupted file would be trusted
    without ever being read. This test proves build_have_pieces itself has no
    such safety net – it trusts whatever the caller puts in `unchanged_rels`
    – so that guarantee has to (and does, see transfer.py
    `_apply_have_pieces_fast_path`) live in the caller.
    """
    lt = pytest.importorskip("libtorrent")

    from deckdrop.core.torrent import build_have_pieces, create_torrent_data, retarget_root

    game = tmp_path / "Game"
    original = b"A" * (1024 * 1024)
    _make_game(game, {"save.bin": original})
    torrent_bytes = create_torrent_data(game, files=["save.bin"])
    ti = lt.torrent_info(lt.bdecode(torrent_bytes))
    retarget_root(lt, ti, game.name)

    # Corrupt the file locally after the torrent (describing the *old*,
    # correct content) was built – a bit-rot / accidental edit scenario.
    (game / "save.bin").write_bytes(b"B" * (1024 * 1024))

    # Misused as "unchanged" (what the caller must NOT do for a file the
    # tracker reported changed): the corrupted bytes are trusted blindly.
    trusted_wrongly = build_have_pieces(lt, ti, game, ["save.bin"], [])
    assert trusted_wrongly == [True]

    # Used correctly as "changed" instead: the mismatch is actually detected.
    verified = build_have_pieces(lt, ti, game, [], ["save.bin"])
    assert verified == [False]


def test_build_have_pieces_reports_progress_and_can_be_cancelled(tmp_path):
    import threading

    lt = pytest.importorskip("libtorrent")

    from deckdrop.core.torrent import (
        PieceCheckCancelled,
        build_have_pieces,
        create_torrent_data,
        retarget_root,
    )

    game = tmp_path / "Game"
    piece = 1024 * 1024
    _make_game(game, {"a.bin": b"A" * (4 * piece), "b.bin": b"B" * (2 * piece)})
    ti = lt.torrent_info(lt.bdecode(create_torrent_data(game)))
    retarget_root(lt, ti, "Game")

    seen: list[float] = []
    have = build_have_pieces(lt, ti, game, [], ["a.bin", "b.bin"], on_progress=seen.append)
    assert all(have[:6])
    assert seen and seen[-1] == pytest.approx(1.0)
    assert seen == sorted(seen)

    cancel = threading.Event()

    def _cancel_after_first(frac: float) -> None:
        cancel.set()

    with pytest.raises(PieceCheckCancelled):
        build_have_pieces(
            lt, ti, game, [], ["a.bin", "b.bin"], on_progress=_cancel_after_first, cancel=cancel
        )
