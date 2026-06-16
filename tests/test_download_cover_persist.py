"""Downloaded games keep their cover + steam_app_id after the host goes offline."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from deckdrop.network.transfer import TransferManager, _Handle


@pytest.fixture
def transfer(tmp_path, monkeypatch):
    from deckdrop.core import config as cfg_mod
    from deckdrop.core import torrent as torrent_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(torrent_mod, "lan_session", lambda port: MagicMock())
    cfg = cfg_mod.load()
    cfg_mod.save(cfg)
    return TransferManager(cfg)


def _handle(dest):
    return _Handle(
        download_id="d1",
        game_id="g1",
        game_name="My Game",
        peer_id="peer1",
        peer_name="Host",
        handle=MagicMock(),
        dest_path=dest,
    )


def _mock_peer_state(monkeypatch, *, games):
    peer = SimpleNamespace(address="192.168.1.5", port=7373, games=games)
    registry = MagicMock()
    registry.get.return_value = peer
    state = SimpleNamespace(peer_registry=registry)
    from deckdrop.api import state as app_state

    monkeypatch.setattr(app_state, "get", lambda: state)
    return peer


def test_register_downloaded_game_persists_steam_app_id(transfer, tmp_path, monkeypatch):
    from deckdrop.core import game as game_mod

    dest = tmp_path / "game"
    dest.mkdir()
    (dest / "data.bin").write_bytes(b"x" * 16)

    _mock_peer_state(
        monkeypatch,
        games=[{"id": "g1", "steam_app_id": 2073850, "has_local_cover": True}],
    )

    transfer._register_downloaded_game(_handle(dest))

    info = game_mod.load_from_path(dest)
    assert info is not None
    assert info.steam.app_id == 2073850
    assert info.origin.peer_name == "Host"


def test_fetch_and_save_cover_downloads_from_host(transfer, tmp_path, monkeypatch):
    dest = tmp_path / "game"
    dest.mkdir()

    _mock_peer_state(
        monkeypatch,
        games=[{"id": "g1", "has_local_cover": True}],
    )

    response = MagicMock()
    response.status_code = 200
    response.content = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    response.headers = {"content-type": "image/png"}

    with patch("deckdrop.core.cover.httpx.Client") as mock_client:
        mock_client.return_value.__enter__.return_value.get.return_value = response
        transfer._fetch_and_save_cover(_handle(dest))

    assert (dest / "deckdrop.png").exists()


def test_fetch_and_save_cover_skips_when_host_has_no_cover(transfer, tmp_path, monkeypatch):
    dest = tmp_path / "game"
    dest.mkdir()

    _mock_peer_state(
        monkeypatch,
        games=[{"id": "g1", "has_local_cover": False}],
    )

    with patch("deckdrop.core.cover.httpx.Client") as mock_client:
        transfer._fetch_and_save_cover(_handle(dest))
        mock_client.assert_not_called()

    assert not any(dest.iterdir())


def test_fetch_and_save_cover_skips_when_cover_exists(transfer, tmp_path, monkeypatch):
    dest = tmp_path / "game"
    dest.mkdir()
    (dest / "deckdrop.jpg").write_bytes(b"already-here")

    _mock_peer_state(
        monkeypatch,
        games=[{"id": "g1", "has_local_cover": True}],
    )

    with patch("deckdrop.core.cover.httpx.Client") as mock_client:
        transfer._fetch_and_save_cover(_handle(dest))
        mock_client.assert_not_called()

    assert (dest / "deckdrop.jpg").read_bytes() == b"already-here"
