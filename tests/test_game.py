import tomli_w

from deckdrop.core import game as game_mod


def test_create_and_save(tmp_path):
    info = game_mod.create_new(tmp_path, name="Stardew Valley", added_by="alice")
    assert len(info.id) == 8
    game_mod.save(info)
    assert (tmp_path / "deckdrop.toml").exists()


def test_roundtrip(tmp_path):
    info = game_mod.create_new(tmp_path, name="Celeste", added_by="bob", platform="linux")
    game_mod.save(info)

    loaded = game_mod.load_from_path(tmp_path)
    assert loaded is not None
    assert loaded.name == "Celeste"
    assert loaded.platform == "linux"
    assert loaded.id == info.id


def test_load_missing_returns_none(tmp_path):
    assert game_mod.load_from_path(tmp_path / "nonexistent") is None


def test_origin_roundtrip(tmp_path):
    info = game_mod.create_new(tmp_path, name="Portal 2", added_by="local")
    info.origin.peer_id = "peer123"
    info.origin.peer_name = "Steam Deck"
    game_mod.save(info)

    loaded = game_mod.load_from_path(tmp_path)
    assert loaded.origin.peer_name == "Steam Deck"
    assert loaded.origin.peer_id == "peer123"


def test_bump_version(tmp_path):
    info = game_mod.create_new(tmp_path, name="Hollow Knight", added_by="carol")
    game_mod.save(info)
    assert info.version == 1
    game_mod.bump_version(info, "carol")
    assert info.version == 2
    # Should be persisted
    loaded = game_mod.load_from_path(tmp_path)
    assert loaded.version == 2


def test_create_new_sets_content_defaults(tmp_path):
    info = game_mod.create_new(tmp_path, name="Stardew Valley", added_by="alice")
    assert info.content.revision == 1
    assert info.content.created_by == "alice"
    assert info.content.updated_by == "alice"
    assert info.content.version_label == ""
    assert info.history == []
    assert info.sizes == {}


def test_legacy_toml_without_content_gets_defaults(tmp_path):
    """A pre-Phase-1 deckdrop.toml has no [content]/[[history]]/[sizes]."""
    data = {
        "game": {
            "id": "abcd1234",
            "name": "Old Game",
            "version": 1,
            "added_at": "2025-01-01T00:00:00+00:00",
            "added_by": "alice",
            "updated_at": "2025-01-01T00:00:00+00:00",
            "updated_by": "alice",
            "size_bytes": 123,
            "platform": "any",
        },
        "files": {"a.bin": "hash1"},
    }
    with (tmp_path / "deckdrop.toml").open("wb") as f:
        tomli_w.dump(data, f)

    loaded = game_mod.load_from_path(tmp_path)
    assert loaded is not None
    assert loaded.content.revision == 1
    assert loaded.content.version_label == ""
    assert loaded.content.note == ""
    assert loaded.content.created_by == "alice"
    assert loaded.content.created_at == "2025-01-01T00:00:00+00:00"
    assert loaded.content.updated_by == "alice"
    assert loaded.content.content_hash == ""
    assert loaded.content.ignore == []
    assert loaded.history == []
    assert loaded.sizes == {}


def test_content_history_sizes_roundtrip(tmp_path):
    info = game_mod.create_new(tmp_path, name="Celeste", added_by="bob")
    info.content.revision = 2
    info.content.version_label = "1.6.8"
    info.content.note = "Patch"
    info.content.content_hash = "deadbeef"
    info.content.ignore = ["saves/**"]
    info.history = [
        game_mod.HistoryEntry(
            revision=1,
            version_label="",
            note="Erstveröffentlichung",
            by="bob",
            at="2026-01-01T00:00:00+00:00",
            content_hash="cafebabe",
        )
    ]
    info.files = {"a.bin": "hash1"}
    info.sizes = {"a.bin": 42}
    game_mod.save(info)

    loaded = game_mod.load_from_path(tmp_path)
    assert loaded.content.revision == 2
    assert loaded.content.version_label == "1.6.8"
    assert loaded.content.content_hash == "deadbeef"
    assert loaded.content.ignore == ["saves/**"]
    assert len(loaded.history) == 1
    assert loaded.history[0].note == "Erstveröffentlichung"
    assert loaded.sizes == {"a.bin": 42}


def test_manifest_dict_contains_expected_fields(tmp_path):
    info = game_mod.create_new(tmp_path, name="Portal 2", added_by="alice")
    info.files = {"a.bin": "hash1"}
    info.sizes = {"a.bin": 10}
    info.torrent.info_hash = "abc123"
    m = game_mod.manifest_dict(info)
    assert m["id"] == info.id
    assert m["name"] == "Portal 2"
    assert m["content"]["revision"] == 1
    assert m["files"] == {"a.bin": "hash1"}
    assert m["sizes"] == {"a.bin": 10}
    assert m["info_hash"] == "abc123"


def test_apply_manifest_keep_local_meta_false_overwrites_id(tmp_path):
    info = game_mod.create_new(tmp_path, name="Local Name", added_by="me")
    manifest = {
        "id": "host1234",
        "name": "Host Name",
        "platform": "linux",
        "added_by": "alice",
        "added_at": "2025-01-01T00:00:00+00:00",
        "description": "desc",
        "launch_exe": "run.exe",
        "content": {
            "revision": 3,
            "version_label": "1.1",
            "note": "note",
            "created_by": "alice",
            "created_at": "2025-01-01T00:00:00+00:00",
            "updated_by": "alice",
            "updated_at": "2025-01-02T00:00:00+00:00",
            "content_hash": "hash",
            "ignore": [],
        },
        "history": [],
        "files": {"a.bin": "hash1"},
        "sizes": {"a.bin": 5},
    }
    game_mod.apply_manifest(info, manifest, keep_local_meta=False)
    assert info.id == "host1234"
    assert info.name == "Host Name"
    assert info.added_by == "alice"
    assert info.content.revision == 3
    assert info.files == {"a.bin": "hash1"}
    assert info.sizes == {"a.bin": 5}


def test_apply_manifest_keep_local_meta_true_preserves_id(tmp_path):
    info = game_mod.create_new(tmp_path, name="Local Name", added_by="me")
    original_id = info.id
    manifest = {
        "id": "host1234",
        "name": "Host Name",
        "content": {"revision": 2},
        "history": [],
        "files": {"a.bin": "hash1"},
        "sizes": {"a.bin": 5},
    }
    game_mod.apply_manifest(info, manifest, keep_local_meta=True)
    assert info.id == original_id
    assert info.name == "Local Name"
    assert info.content.revision == 2
    assert info.files == {"a.bin": "hash1"}
