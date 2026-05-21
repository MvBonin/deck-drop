"""Background torrent preparation."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core import torrent_prep
from deckdrop.core.library import Library


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.user_name = "testuser"
    cfg_mod.save(cfg)

    library = Library()
    library.reload(cfg)
    app_state.init(cfg, library)

    app = create_app()
    return TestClient(app)


def test_get_magnet_returns_409_while_preparing(client, tmp_path):
    game_dir = tmp_path / "BigGame"
    game_dir.mkdir()
    r = client.post("/api/games", json={"path": str(game_dir), "name": "Big Game"})
    game_id = r.json()["id"]

    with torrent_prep._lock:
        torrent_prep._errors.pop(game_id, None)
        torrent_prep._preparing.add(game_id)
        torrent_prep._progress[game_id] = 0.1

    r2 = client.get(f"/api/games/{game_id}/magnet")
    assert r2.status_code == 409


def test_list_games_shows_preparing_state(client, tmp_path):
    game_dir = tmp_path / "G"
    game_dir.mkdir()
    (game_dir / "data.bin").write_bytes(b"x" * 100)
    r = client.post("/api/games", json={"path": str(game_dir), "name": "G"})
    game_id = r.json()["id"]
    assert r.json()["size_bytes"] == 100

    with torrent_prep._lock:
        torrent_prep._errors.pop(game_id, None)
        torrent_prep._preparing.add(game_id)
        torrent_prep._progress[game_id] = 0.42

    listed = client.get("/api/games").json()
    game = next(g for g in listed if g["id"] == game_id)
    assert game["torrent_preparing"] is True
    assert game["torrent_prep_progress"] == 0.42


def test_add_game_schedules_torrent_prep_before_integrity_hash(client, tmp_path, monkeypatch):
    """Torrent prep must start immediately, not after slow blake2b hashing."""
    order: list[str] = []

    def fake_schedule(game_id: str, *, force: bool = False) -> None:
        order.append("prep")

    def slow_hash(game_id: str) -> None:
        order.append("integrity")

    monkeypatch.setattr(torrent_prep, "schedule_prepare", fake_schedule)
    monkeypatch.setattr(
        "deckdrop.api.routes.games._hash_game_files",
        slow_hash,
    )

    game_dir = tmp_path / "Slow"
    game_dir.mkdir()
    (game_dir / "big.bin").write_bytes(b"x" * 50)
    client.post("/api/games", json={"path": str(game_dir), "name": "Slow"})

    assert order == ["prep", "integrity"]


def test_schedule_prepare_rebuilds_when_cache_unreadable(tmp_path, monkeypatch):
    """Orphan .torrent cache without magnet must not block preparation forever."""
    from deckdrop.core import game as game_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg._data["paths"]["torrent_cache"] = str(tmp_path / "torrents")
    cfg_mod.save(cfg)
    cfg.torrent_cache.mkdir(parents=True, exist_ok=True)

    game_dir = tmp_path / "BrokenCache"
    game_dir.mkdir()
    info = game_mod.create_new(game_dir, name="Broken", added_by="test")
    game_mod.save(info)

    library = Library()
    library.add(info)
    app_state.init(cfg, library)

    (cfg.torrent_cache / f"{info.id}.torrent").write_bytes(b"not-a-torrent")

    started: list[str] = []
    mock_thread = MagicMock()
    mock_thread.start = lambda: started.append(info.id)

    with patch("deckdrop.core.torrent_prep.threading.Thread", return_value=mock_thread):
        torrent_prep.schedule_prepare(info.id)

    assert started == [info.id]


def test_list_games_preparing_without_active_thread(client, tmp_path):
    """After reload, UI should show prep while magnet is missing (no thread required)."""
    game_dir = tmp_path / "G2"
    game_dir.mkdir()
    r = client.post("/api/games", json={"path": str(game_dir), "name": "G2"})
    game_id = r.json()["id"]

    with torrent_prep._lock:
        torrent_prep._preparing.discard(game_id)
        torrent_prep._progress.pop(game_id, None)
        torrent_prep._errors.pop(game_id, None)

    listed = client.get("/api/games").json()
    game = next(g for g in listed if g["id"] == game_id)
    assert game["has_torrent"] is False
    assert game["torrent_preparing"] is True
    assert game["torrent_prep_progress"] == 0.0


def test_restore_from_cache_skips_rehash(tmp_path, monkeypatch):
    from deckdrop.core import game as game_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg._data["paths"]["torrent_cache"] = str(tmp_path / "torrents")
    cfg_mod.save(cfg)
    cfg.torrent_cache.mkdir(parents=True, exist_ok=True)

    game_dir = tmp_path / "Cached"
    game_dir.mkdir()
    info = game_mod.create_new(game_dir, name="Cached", added_by="test")
    game_mod.save(info)

    library = Library()
    library.add(info)
    app_state.init(cfg, library)

    (cfg.torrent_cache / f"{info.id}.torrent").write_bytes(b"cached-torrent")

    with (
        patch(
            "deckdrop.core.torrent.make_magnet",
            return_value=("magnet:?xt=urn:btih:abc", "abc"),
        ),
        patch("deckdrop.core.torrent.create_torrent_data") as mock_create,
    ):
        assert torrent_prep.restore_from_cache(info.id) is True
        mock_create.assert_not_called()

    loaded = game_mod.load_from_path(game_dir)
    assert loaded.torrent.magnet == "magnet:?xt=urn:btih:abc"
    assert loaded.torrent.info_hash == "abc"


def test_fetch_magnet_retries_on_409(tmp_path, monkeypatch):
    from deckdrop.api.routes import downloads as dl_mod

    calls = {"n": 0}

    class FakeResponse:
        def __init__(self, status_code: int, magnet: str | None = None):
            self.status_code = status_code
            self.text = "preparing"
            self._magnet = magnet

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                import httpx

                req = httpx.Request("GET", "http://peer/api/games/g1/magnet")
                resp = httpx.Response(self.status_code, request=req, text=self.text)
                raise httpx.HTTPStatusError("err", request=req, response=resp)

        def json(self) -> dict:
            return {"magnet": self._magnet}

    def fake_get(url, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            return FakeResponse(409)
        return FakeResponse(200, "magnet:?xt=urn:btih:abc")

    monkeypatch.setattr(dl_mod.httpx, "get", fake_get)
    monkeypatch.setattr(dl_mod, "_MAGNET_PREP_POLL", 0.01)
    magnet = dl_mod._fetch_magnet_from_peer("192.168.1.10", 7373, "g1")
    assert magnet == "magnet:?xt=urn:btih:abc"
    assert calls["n"] == 3
