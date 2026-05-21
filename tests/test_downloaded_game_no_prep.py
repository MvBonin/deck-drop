"""Downloaded games (peer origin) must not show local torrent prep in API."""

from deckdrop.api.routes.games import GameOut
from deckdrop.core import game as game_mod
from deckdrop.core.game import GameInfo, OriginInfo


def test_game_out_no_prep_for_peer_download(tmp_path, monkeypatch):
    from deckdrop.api import state as app_state
    from deckdrop.core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    app_state.init(cfg_mod.load(), __import__("deckdrop.core.library", fromlist=["Library"]).Library())

    info = GameInfo(
        id="abc12345",
        name="From Peer",
        version=1,
        added_at="2025-01-01T00:00:00+00:00",
        added_by="me",
        updated_at="2025-01-01T00:00:00+00:00",
        updated_by="me",
        size_bytes=1000,
        platform="linux",
        path=__import__("pathlib").Path("/tmp/x"),
        origin=OriginInfo(peer_id="peer1", peer_name="Host"),
    )
    out = GameOut.from_info(info)
    assert out.torrent_preparing is False
    assert out.has_torrent is False


def test_game_out_prep_for_local_share(tmp_path, monkeypatch):
    from deckdrop.api import state as app_state
    from deckdrop.core import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    app_state.init(cfg, __import__("deckdrop.core.library", fromlist=["Library"]).Library())

    info = game_mod.create_new(tmp_path / "Local", "Local", added_by="me")
    out = GameOut.from_info(info)
    assert out.torrent_preparing is True
