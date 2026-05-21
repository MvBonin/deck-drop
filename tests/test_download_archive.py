"""Completed downloads archived from API list; seeding continues in background."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deckdrop.network.transfer import (
    _ACTIVE_DOWNLOAD_STATUSES,
    TransferManager,
    _Handle,
    _PersistedRecord,
)


def test_active_download_statuses_excludes_done_and_seeding():
    assert "done" not in _ACTIVE_DOWNLOAD_STATUSES
    assert "seeding" not in _ACTIVE_DOWNLOAD_STATUSES
    assert "downloading" in _ACTIVE_DOWNLOAD_STATUSES


def test_all_statuses_filters_done_paused_record(tmp_path, monkeypatch):
    from deckdrop.core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)

    tm = TransferManager(cfg)
    rec = _PersistedRecord(
        download_id="d1",
        game_id="g1",
        game_name="Game",
        peer_id="p1",
        peer_name="Host",
        magnet="magnet:?xt=urn:btih:" + "a" * 40,
        dest_path=str(cfg.download_dir / "Game"),
        started_at=0.0,
        downloaded_bytes=100,
        total_bytes=100,
        info_hash="a" * 40,
    )
    tm._paused["d1"] = rec

    statuses = tm.all_statuses()
    assert statuses == []


def test_promote_download_to_seed_keeps_handle(tmp_path, monkeypatch):
    pytest.importorskip("libtorrent")
    from deckdrop.core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)

    tm = TransferManager(cfg)
    tm._session = MagicMock()
    mock_handle = MagicMock()
    h = _Handle(
        download_id="d1",
        game_id="g1",
        game_name="Game",
        peer_id="p1",
        peer_name="Host",
        handle=mock_handle,
        dest_path=Path(cfg.download_dir / "Game"),
    )
    tm._handles["d1"] = h

    tm._promote_download_to_seed(h)
    assert tm._seed_handles["g1"] is mock_handle
    # _promote only registers the handle for seeding; _poll_loop's done_ids
    # cleanup removes it from _handles. Verify the torrent was NOT removed.
    tm._session.remove_torrent.assert_not_called()
