from deckdrop.core.integrity import hash_directory, hash_file, verify_files


def test_hash_file(tmp_path):
    f = tmp_path / "test.bin"
    f.write_bytes(b"hello world")
    h = hash_file(f)
    assert len(h) == 128  # blake2b default is 64 bytes = 128 hex chars
    # Deterministic
    assert hash_file(f) == h


def test_hash_directory(tmp_path):
    (tmp_path / "a.txt").write_text("foo")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("bar")

    hashes, total = hash_directory(tmp_path)
    assert "a.txt" in hashes
    assert "sub/b.txt" in hashes
    assert total == 6  # "foo" + "bar"


def test_hash_directory_excludes_toml(tmp_path):
    (tmp_path / "game.exe").write_bytes(b"x" * 100)
    (tmp_path / "deckdrop.toml").write_text("[game]")
    hashes, _ = hash_directory(tmp_path)
    assert "deckdrop.toml" not in hashes


def test_verify_files_passes(tmp_path):
    (tmp_path / "file.txt").write_text("content")
    hashes, _ = hash_directory(tmp_path)
    failures = verify_files(tmp_path, hashes)
    assert failures == []


def test_verify_files_detects_change(tmp_path):
    (tmp_path / "file.txt").write_text("content")
    hashes, _ = hash_directory(tmp_path)
    (tmp_path / "file.txt").write_text("tampered")
    failures = verify_files(tmp_path, hashes)
    assert "file.txt" in failures


def test_verify_files_detects_missing(tmp_path):
    hashes = {"missing.bin": "abc123"}
    failures = verify_files(tmp_path, hashes)
    assert "missing.bin" in failures
