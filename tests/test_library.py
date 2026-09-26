"""Library scan: download_dir subdirs + individual game_paths."""

from pathlib import Path

import pytest

from deckdrop.core import config as cfg_mod
from deckdrop.core import game as game_mod
from deckdrop.core.library import Library, relink_game_id


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    c = cfg_mod.load()
    c.download_dir = tmp_path / "Games"
    cfg_mod.save(c)
    return c


def _make_game(path: Path, name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    game_mod.save(game_mod.create_new(path, name=name, added_by="test"))


def test_reload_finds_games_in_download_dir(cfg, tmp_path):
    _make_game(cfg.download_dir / "Celeste", "Celeste")
    _make_game(cfg.download_dir / "Hollow Knight", "Hollow Knight")

    lib = Library()
    lib.reload(cfg)

    names = {g.name for g in lib.all()}
    assert names == {"Celeste", "Hollow Knight"}


def test_reload_ignores_dirs_without_toml(cfg):
    (cfg.download_dir / "NotAGame").mkdir(parents=True)

    lib = Library()
    lib.reload(cfg)

    assert lib.all() == []


def test_reload_individual_path(cfg, tmp_path):
    game_dir = tmp_path / "external" / "Portal2"
    _make_game(game_dir, "Portal 2")
    cfg.add_game_path(game_dir)

    lib = Library()
    lib.reload(cfg)

    assert lib.get(lib.all()[0].id) is not None
    assert lib.all()[0].name == "Portal 2"


def test_reload_marks_missing_path_unavailable(cfg, tmp_path):
    missing = tmp_path / "ghost_game"
    # Add a path that doesn't exist to config
    cfg.add_game_path(missing)

    lib = Library()
    lib.reload(cfg)

    # No game loaded because toml doesn't exist either
    assert lib.all() == []


def test_reload_marks_existing_path_without_toml_as_wizard_needed(cfg, tmp_path):
    game_dir = tmp_path / "no_toml_game"
    game_dir.mkdir()
    cfg.add_game_path(game_dir)

    lib = Library()
    assert lib.needs_wizard(game_dir) is True


def test_reload_empty_download_dir(cfg):
    lib = Library()
    lib.reload(cfg)
    assert lib.all() == []


def test_reload_nonexistent_download_dir(cfg):
    cfg.download_dir = cfg.download_dir / "does_not_exist"
    lib = Library()
    lib.reload(cfg)
    assert lib.all() == []


def test_add_and_remove(cfg):
    lib = Library()
    lib.reload(cfg)

    info = game_mod.create_new(cfg.download_dir / "TestGame", "TestGame", "u")
    lib.add(info)
    assert lib.get(info.id) is not None

    lib.remove(info.id)
    assert lib.get(info.id) is None


def test_relink_game_id_updates_toml_cache_and_library(cfg, tmp_path):
    cfg._data["paths"]["torrent_cache"] = str(tmp_path / "torrents")
    game_dir = cfg.download_dir / "SomeGame"
    _make_game(game_dir, "SomeGame")

    lib = Library()
    lib.reload(cfg)
    info = lib.all()[0]
    old_id = info.id

    cfg.torrent_cache.mkdir(parents=True, exist_ok=True)
    (cfg.torrent_cache / f"{old_id}.torrent").write_bytes(b"fake-torrent")
    cfg.content_state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.content_state_dir / f"{old_id}.json").write_text("{}", encoding="utf-8")

    changed = relink_game_id(cfg, lib, info, "newid123")

    assert changed is True
    assert info.id == "newid123"
    assert lib.get("newid123") is info
    assert lib.get(old_id) is None

    reloaded = game_mod.load_from_path(game_dir)
    assert reloaded.id == "newid123"
    assert (cfg.torrent_cache / "newid123.torrent").exists()
    assert not (cfg.torrent_cache / f"{old_id}.torrent").exists()
    assert (cfg.content_state_dir / "newid123.json").exists()


def test_relink_game_id_same_id_is_noop(cfg, tmp_path):
    game_dir = cfg.download_dir / "SomeGame"
    _make_game(game_dir, "SomeGame")
    lib = Library()
    lib.reload(cfg)
    info = lib.all()[0]

    assert relink_game_id(cfg, lib, info, info.id) is False


def test_duplicate_game_id_last_wins(cfg, tmp_path):
    """If same game folder appears in both download_dir and game_paths, no duplicate."""
    game_dir = cfg.download_dir / "SomeGame"
    _make_game(game_dir, "SomeGame")
    cfg.add_game_path(game_dir)

    lib = Library()
    lib.reload(cfg)

    assert len(lib.all()) == 1
