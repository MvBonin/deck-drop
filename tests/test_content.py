"""core/content.py: pure manifest/diff/plan helpers (no libtorrent)."""

from __future__ import annotations

from deckdrop.core import content


def test_is_ignored_default_patterns():
    assert content.is_ignored("game.log", [])
    assert content.is_ignored("sub/dir/shader.dxvk-cache", [])
    assert content.is_ignored("deckdrop.toml", [])
    assert content.is_ignored("comments.toml", [])
    assert not content.is_ignored("bin/game.exe", [])


def test_is_ignored_custom_pattern_folder():
    patterns = ["saves/**"]
    assert content.is_ignored("saves/slot1.sav", patterns)
    assert content.is_ignored("saves", patterns)
    assert not content.is_ignored("other/saves_backup.sav", patterns)


def test_is_ignored_custom_pattern_glob():
    assert content.is_ignored("config.ini", ["config.ini"])
    assert not content.is_ignored("config.ini.bak", ["config.ini"])


def test_iter_content_files_sorted_and_filtered(tmp_path):
    (tmp_path / "b.bin").write_bytes(b"1")
    (tmp_path / "a.bin").write_bytes(b"2")
    (tmp_path / "skip.log").write_text("x")
    (tmp_path / "deckdrop.toml").write_text("x")

    files = content.iter_content_files(tmp_path, [])
    assert files == ["a.bin", "b.bin"]


def test_compute_content_hash_order_independent():
    files_a = {"a": "h1", "b": "h2"}
    files_b = {"b": "h2", "a": "h1"}
    sizes = {"a": 1, "b": 2}
    assert content.compute_content_hash(files_a, sizes) == content.compute_content_hash(
        files_b, sizes
    )


def test_compute_content_hash_empty():
    assert content.compute_content_hash({}, {}) == ""


def test_compute_content_hash_changes_with_path():
    sizes = {"a": 1}
    h1 = content.compute_content_hash({"a": "hash"}, sizes)
    h2 = content.compute_content_hash({"b": "hash"}, {"b": 1})
    assert h1 != h2


def test_compute_content_hash_changes_with_size():
    h1 = content.compute_content_hash({"a": "hash"}, {"a": 1})
    h2 = content.compute_content_hash({"a": "hash"}, {"a": 2})
    assert h1 != h2


def test_take_snapshot_missing_file_skipped(tmp_path):
    (tmp_path / "present.bin").write_bytes(b"data")
    snap = content.take_snapshot(tmp_path, ["present.bin", "missing.bin"])
    assert "present.bin" in snap
    assert "missing.bin" not in snap
    assert snap["present.bin"][0] == 4


def test_compare_snapshot_changed_removed_added(tmp_path):
    (tmp_path / "unchanged.bin").write_bytes(b"aaaa")
    (tmp_path / "changed.bin").write_bytes(b"bbbbbbbb")
    (tmp_path / "new.bin").write_bytes(b"cccc")

    manifest_files = {"unchanged.bin": "h1", "changed.bin": "h2", "removed.bin": "h3"}
    snapshot = content.take_snapshot(tmp_path, ["unchanged.bin", "changed.bin"])
    # Simulate changed.bin having grown since the snapshot was taken.
    snapshot["changed.bin"][0] = 4

    result = content.compare_snapshot(tmp_path, manifest_files, snapshot, [])
    assert result.changed == ["changed.bin"]
    assert result.removed == ["removed.bin"]
    assert result.added == ["new.bin"]
    assert result.mtime_only == []


def test_compare_snapshot_mtime_only(tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"data")
    manifest_files = {"f.bin": "h1"}
    snapshot = content.take_snapshot(tmp_path, ["f.bin"])
    snapshot["f.bin"][1] += 1  # same size, different mtime

    result = content.compare_snapshot(tmp_path, manifest_files, snapshot, [])
    assert result.mtime_only == ["f.bin"]
    assert result.changed == []


def test_compare_snapshot_ignored_files_invisible(tmp_path):
    (tmp_path / "saves").mkdir()
    (tmp_path / "saves" / "slot1.sav").write_bytes(b"save")
    (tmp_path / "game.bin").write_bytes(b"game")

    result = content.compare_snapshot(tmp_path, {"game.bin": "h1"}, {}, ["saves/**"])
    assert result.added == []
    assert result.changed == ["game.bin"]


def test_compare_snapshot_rejects_path_traversal_manifest_entry(tmp_path):
    """An untrusted (peer) manifest with a traversal path must never be
    stat()ed outside root – it's treated like a missing file (-> removed),
    never surfacing in changed/mtime_only."""
    manifest_files = {"../outside.bin": "h1", "/etc/passwd": "h2"}
    result = content.compare_snapshot(tmp_path, manifest_files, {}, [])
    assert sorted(result.removed) == ["../outside.bin", "/etc/passwd"]
    assert result.changed == []
    assert result.mtime_only == []


def test_take_snapshot_rejects_path_traversal(tmp_path):
    snapshot = content.take_snapshot(tmp_path, ["../outside.bin", "/etc/passwd"])
    assert snapshot == {}


def test_diff_manifests_added_changed_removed_unchanged():
    old_files = {"a": "h1", "b": "h2", "c": "h3"}
    old_sizes = {"a": 1, "b": 2, "c": 3}
    new_files = {"a": "h1", "b": "h2new", "d": "h4"}
    new_sizes = {"a": 1, "b": 20, "d": 4}

    diff = content.diff_manifests(old_files, old_sizes, new_files, new_sizes)
    assert diff.unchanged == ["a"]
    assert diff.changed == ["b"]
    assert diff.added == ["d"]
    assert diff.removed == ["c"]
    assert diff.download_estimate == 20 + 4


def test_diff_manifests_detects_moved_file():
    old_files = {"old/path.bin": "samehash"}
    old_sizes = {"old/path.bin": 100}
    new_files = {"new/path.bin": "samehash"}
    new_sizes = {"new/path.bin": 100}

    diff = content.diff_manifests(old_files, old_sizes, new_files, new_sizes)
    assert diff.moved == {"new/path.bin": "old/path.bin"}
    assert diff.added == []
    assert diff.removed == []
    assert diff.download_estimate == 0


def test_diff_manifests_estimate_none_without_sizes():
    diff = content.diff_manifests({}, {}, {"a": "h1"}, {})
    assert diff.download_estimate is None


def test_plan_local_prep_move_copy_truncate_delete(tmp_path):
    (tmp_path / "old.bin").write_bytes(b"x" * 10)
    (tmp_path / "big.bin").write_bytes(b"y" * 10)
    (tmp_path / "gone.bin").write_bytes(b"z" * 5)
    (tmp_path / "saves").mkdir()
    (tmp_path / "saves" / "slot.sav").write_bytes(b"s")

    old_files = {"old.bin": "h1", "big.bin": "h2", "gone.bin": "h3"}
    new_files = {"new.bin": "h1", "big.bin": "h2changed"}
    old_sizes = {"old.bin": 10, "big.bin": 10, "gone.bin": 5}
    new_sizes = {"new.bin": 10, "big.bin": 4}

    diff = content.diff_manifests(old_files, old_sizes, new_files, new_sizes)
    plan = content.plan_local_prep(tmp_path, diff, new_sizes, ["saves/**"])

    assert plan.moves == [("old.bin", "new.bin")]
    assert plan.copies == []
    assert plan.truncates == [("big.bin", 4)]
    assert plan.deletes_after == ["gone.bin"]  # saves/slot.sav is ignored, never deleted


def test_apply_local_prep_and_delete_removed(tmp_path):
    (tmp_path / "old.bin").write_bytes(b"hello world")
    (tmp_path / "big.bin").write_bytes(b"y" * 10)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "gone.bin").write_bytes(b"z" * 5)

    plan = content.LocalPrepPlan(
        moves=[("old.bin", "renamed/new.bin")],
        copies=[],
        truncates=[("big.bin", 4)],
        deletes_after=["sub/gone.bin"],
    )
    content.apply_local_prep(tmp_path, plan)
    assert not (tmp_path / "old.bin").exists()
    assert (tmp_path / "renamed" / "new.bin").read_bytes() == b"hello world"
    assert (tmp_path / "big.bin").stat().st_size == 4

    content.delete_removed(tmp_path, plan.deletes_after)
    assert not (tmp_path / "sub" / "gone.bin").exists()
    assert not (tmp_path / "sub").exists()  # emptied directory removed


def test_safe_join_rejects_traversal_and_absolute(tmp_path):
    assert content.safe_join(tmp_path, "../outside.bin") is None
    assert content.safe_join(tmp_path, "/etc/passwd") is None
    assert content.safe_join(tmp_path, "sub/../../escape.bin") is None


def test_safe_join_accepts_normal_relpath(tmp_path):
    result = content.safe_join(tmp_path, "sub/dir/file.bin")
    assert result == (tmp_path / "sub" / "dir" / "file.bin").resolve()


def test_safe_join_never_raises_on_weird_input(tmp_path):
    assert content.safe_join(tmp_path, "") is None
    assert content.safe_join(tmp_path, "\\\\server\\share") is None


def test_pieces_for_file_aligned_single_piece():
    # A file starting exactly on a piece boundary, smaller than one piece.
    assert content.pieces_for_file(0, 500, 1024) == range(0, 1)
    assert content.pieces_for_file(1024, 500, 1024) == range(1, 2)


def test_pieces_for_file_spans_multiple_pieces():
    # Starts on piece 2's boundary, spans into piece 4.
    assert content.pieces_for_file(2048, 2500, 1024) == range(2, 5)


def test_pieces_for_file_exact_multiple_of_piece_length():
    assert content.pieces_for_file(0, 2048, 1024) == range(0, 2)


def test_pieces_for_file_empty_file_touches_no_piece():
    r = content.pieces_for_file(4096, 0, 1024)
    assert list(r) == []
