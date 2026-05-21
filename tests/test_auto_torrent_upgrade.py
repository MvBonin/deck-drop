"""Automatic torrent migration and peer game change detection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deckdrop.core import torrent_prep
from deckdrop.network.peer_registry import PeerRegistry


def test_torrent_cache_has_metadata_detects_deckdrop_toml(tmp_path):
    cache = tmp_path / "game.torrent"
    cache.write_bytes(b"d4:filesl1:10:deckdrop.toml6:lengthi100ee")
    assert torrent_prep.torrent_cache_has_metadata(cache) is True


def test_torrent_cache_has_metadata_clean(tmp_path):
    cache = tmp_path / "game.torrent"
    cache.write_bytes(b"d4:filesl1:8:game.dat6:lengthi100ee")
    assert torrent_prep.torrent_cache_has_metadata(cache) is False


def test_games_changed_detects_info_hash():
    old = [{"id": "g1", "has_torrent": True, "info_hash": "aaa"}]
    new = [{"id": "g1", "has_torrent": True, "info_hash": "bbb"}]
    assert PeerRegistry._games_changed(old, new) is True


def test_games_changed_same_info_hash():
    games = [{"id": "g1", "has_torrent": True, "info_hash": "abc"}]
    assert PeerRegistry._games_changed(games, list(games)) is False


def test_upgrade_download_swaps_handle(tmp_path, monkeypatch):
    pytest.importorskip("libtorrent")
    from deckdrop.core import config as cfg_mod
    from deckdrop.network.transfer import TransferManager, _PersistedRecord

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)

    tm = TransferManager(cfg)
    tm._session = MagicMock()
    rec = _PersistedRecord(
        download_id="d1",
        game_id="g1",
        game_name="Game",
        peer_id="p1",
        peer_name="Host",
        magnet="magnet:?xt=urn:btih:" + "a" * 40,
        dest_path=str(cfg.download_dir / "Game"),
        started_at=0.0,
        peer_address="192.168.1.2",
        info_hash="a" * 40,
    )
    tm._paused["d1"] = rec
    mock_handle = MagicMock()
    tm._handles["d1"] = MagicMock(
        download_id="d1",
        game_id="g1",
        game_name="Game",
        peer_id="p1",
        peer_name="Host",
        handle=mock_handle,
        dest_path=Path(rec.dest_path),
    )

    new_hash = "b" * 40
    with patch.object(tm, "_reattach_download", return_value=True) as mock_reattach:
        assert tm.upgrade_download("d1", "magnet:?xt=urn:btih:" + new_hash, new_hash) is True
        tm._session.remove_torrent.assert_called_once_with(mock_handle)
        assert rec.info_hash == new_hash
        mock_reattach.assert_called_once_with(rec)


def test_migrate_stale_caches_invalidates(tmp_path, monkeypatch):
    from deckdrop.core import config as cfg_mod
    from deckdrop.core import game as game_mod
    from deckdrop.core.library import Library

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg._data["paths"]["torrent_cache"] = str(tmp_path / "torrents")
    cfg_mod.save(cfg)

    gpath = tmp_path / "Game"
    gpath.mkdir()
    (gpath / "data.bin").write_bytes(b"x")
    info = game_mod.create_new(gpath, "Game", added_by="u")
    game_mod.save(info)

    cache = Path(cfg.torrent_cache) / f"{info.id}.torrent"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"deckdrop.toml" + b"\x00" * 50)

    lib = Library()
    lib._games[info.id] = info

    with patch.object(torrent_prep, "invalidate_torrent") as mock_inv:
        n = torrent_prep.migrate_stale_caches(lib, cfg, None)
    assert n == 1
    mock_inv.assert_called_once_with(info.id)
