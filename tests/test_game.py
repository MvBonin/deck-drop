from pathlib import Path

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


def test_bump_version(tmp_path):
    info = game_mod.create_new(tmp_path, name="Hollow Knight", added_by="carol")
    game_mod.save(info)
    assert info.version == 1
    game_mod.bump_version(info, "carol")
    assert info.version == 2
    # Should be persisted
    loaded = game_mod.load_from_path(tmp_path)
    assert loaded.version == 2
