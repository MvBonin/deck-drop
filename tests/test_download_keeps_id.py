"""A downloaded game must keep the host's ID (Phase 1 bug fix)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deckdrop.network.transfer import TransferManager, _Handle


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    from deckdrop.core import config as cfg_mod
    from deckdrop.core import torrent as torrent_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(torrent_mod, "lan_session", lambda port: MagicMock())
    cfg = cfg_mod.load()
    cfg.user_name = "DeckUser"
    cfg_mod.save(cfg)
    return TransferManager(cfg)


def _handle(dest):
    return _Handle(
        download_id="d1",
        game_id="hostgame1",
        game_name="Stardew Valley",
        peer_id="peer1",
        peer_name="PC1",
        handle=MagicMock(),
        dest_path=dest,
    )


def test_register_downloaded_game_keeps_host_id(transfer, tmp_path, monkeypatch):
    from deckdrop.core import game as game_mod

    dest = tmp_path / "Stardew_Valley"
    dest.mkdir()
    (dest / "data.bin").write_bytes(b"x" * 16)

    peer = SimpleNamespace(
        address="192.168.1.5",
        port=7373,
        games=[{"id": "hostgame1", "added_by": "alice", "has_local_cover": False}],
    )
    registry = MagicMock()
    registry.get.return_value = peer
    state = SimpleNamespace(peer_registry=registry)
    from deckdrop.api import state as app_state

    monkeypatch.setattr(app_state, "get", lambda: state)

    transfer._register_downloaded_game(_handle(dest))

    info = game_mod.load_from_path(dest)
    assert info is not None
    assert info.id == "hostgame1"
    # added_by stays the creator (host), not the receiver's own user name.
    assert info.added_by == "alice"
