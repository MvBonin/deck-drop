"""Torrent file filter and invalidation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from deckdrop.core import torrent_prep
from deckdrop.core.integrity import (
    TORRENT_SKIP_FILENAMES,
    iter_torrent_files,
    should_include_in_torrent,
)


def test_torrent_skip_filenames():
    assert "deckdrop.toml" in TORRENT_SKIP_FILENAMES
    assert "comments.toml" in TORRENT_SKIP_FILENAMES
    assert "deckdrop.png" in TORRENT_SKIP_FILENAMES
    assert "deckdrop.jpg" in TORRENT_SKIP_FILENAMES


def test_iter_torrent_files_excludes_metadata(tmp_path):
    game = tmp_path / "MyGame"
    game.mkdir()
    (game / "data.bin").write_bytes(b"x" * 10)
    (game / "deckdrop.toml").write_text("id = 'x'\n", encoding="utf-8")
    (game / "comments.toml").write_text("[[comment]]\n", encoding="utf-8")
    (game / "deckdrop.jpg").write_bytes(b"cover")

    files = iter_torrent_files(game)
    assert len(files) == 1
    assert files[0].name == "data.bin"
    assert should_include_in_torrent(game / "data.bin")
    assert not should_include_in_torrent(game / "deckdrop.toml")
    assert not should_include_in_torrent(game / "deckdrop.jpg")


def test_invalidate_torrent_clears_cache_and_schedules_reprepare(tmp_path, monkeypatch):
    from deckdrop.core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg._data["paths"]["torrent_cache"] = str(tmp_path / "torrents")
    cfg._data["paths"]["download_dir"] = str(tmp_path / "downloads")
    cfg_mod.save(cfg)

    game_dir = tmp_path / "games"
    game_dir.mkdir(parents=True, exist_ok=True)
    gpath = game_dir / "TestGame"
    gpath.mkdir(exist_ok=True)
    (gpath / "game.dat").write_bytes(b"data")

    from deckdrop.core import game as game_mod

    info = game_mod.create_new(gpath, "Test", added_by="u")
    info.torrent.magnet = "magnet:?xt=urn:btih:aa"
    info.torrent.info_hash = "aa"
    game_mod.save(info)

    cache = cfg.torrent_cache / f"{info.id}.torrent"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"torrent")

    mock_transfer = MagicMock()
    scheduled: list[str] = []

    def _capture_schedule(gid: str, **kw: object) -> None:
        scheduled.append(gid)

    with patch.object(torrent_prep, "schedule_prepare", side_effect=_capture_schedule):
        from deckdrop.api import state as app_state
        from deckdrop.core.library import Library

        lib = Library()
        lib._games[info.id] = info
        app_state.init(cfg, lib, None, transfer=mock_transfer)
        torrent_prep.invalidate_torrent(info.id)

    assert not cache.exists()
    reloaded = game_mod.load_from_path(gpath)
    assert reloaded is not None
    assert reloaded.torrent.magnet == ""
    mock_transfer.drop_seed.assert_called_once_with(info.id)
    assert scheduled == [info.id]
