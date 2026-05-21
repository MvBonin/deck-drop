"""Incomplete downloads must not appear in Meine Spiele (library scan)."""

import pytest

from deckdrop.core import config as cfg_mod
from deckdrop.core import game as game_mod
from deckdrop.core.library import Library
from deckdrop.network.transfer import TransferManager, _PersistedRecord


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    c = cfg_mod.load()
    c.download_dir = tmp_path / "Games"
    cfg_mod.save(c)
    return c


def _transfer_without_session(cfg) -> TransferManager:
    """TransferManager shell for path queries (no libtorrent required)."""
    tm = TransferManager.__new__(TransferManager)
    tm._handles = {}
    tm._paused = {}
    tm._completed_ids = set()
    tm._pending_download_dests = set()
    return tm


def test_reload_excludes_pending_download_dest(cfg):
    game_dir = cfg.download_dir / "Pending"
    game_dir.mkdir(parents=True)
    game_mod.save(game_mod.create_new(game_dir, name="Pending", added_by="test"))

    tm = _transfer_without_session(cfg)
    tm.reserve_download_dest(game_dir.resolve())

    lib = Library()
    lib.reload(cfg, exclude_paths=tm.incomplete_download_dest_paths())
    assert lib.all() == []

    tm.release_download_dest(game_dir.resolve())
    lib.reload(cfg, exclude_paths=tm.incomplete_download_dest_paths())
    assert len(lib.all()) == 1


def test_reload_excludes_incomplete_download_dest(cfg):
    game_dir = cfg.download_dir / "HalfDone"
    game_dir.mkdir(parents=True)
    game_mod.save(game_mod.create_new(game_dir, name="HalfDone", added_by="test"))

    tm = _transfer_without_session(cfg)
    tm._paused["dl1"] = _PersistedRecord(
        download_id="dl1",
        game_id="g1",
        game_name="HalfDone",
        peer_id="p1",
        peer_name="Host",
        magnet="magnet:?xt=urn:btih:" + "a" * 40,
        dest_path=str(game_dir),
        started_at=0.0,
        downloaded_bytes=100,
        total_bytes=1000,
    )

    lib = Library()
    lib.reload(cfg, exclude_paths=tm.incomplete_download_dest_paths())
    assert lib.all() == []

    tm._paused["dl1"].downloaded_bytes = 1000
    lib.reload(cfg, exclude_paths=tm.incomplete_download_dest_paths())
    assert len(lib.all()) == 1
    assert lib.all()[0].name == "HalfDone"


def test_reload_includes_just_finalized_download(cfg):
    game_dir = cfg.download_dir / "Done"
    game_dir.mkdir(parents=True)
    game_mod.save(game_mod.create_new(game_dir, name="Done", added_by="test"))

    tm = _transfer_without_session(cfg)
    tm._completed_ids.add("dl-done")
    tm._paused["dl-done"] = _PersistedRecord(
        download_id="dl-done",
        game_id="g2",
        game_name="Done",
        peer_id="p1",
        peer_name="Host",
        magnet="magnet:?xt=urn:btih:" + "b" * 40,
        dest_path=str(game_dir),
        started_at=0.0,
        downloaded_bytes=500,
        total_bytes=1000,
    )

    lib = Library()
    lib.reload(cfg, exclude_paths=tm.incomplete_download_dest_paths())
    assert len(lib.all()) == 1
