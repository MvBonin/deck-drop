"""POST /api/download answers before the host does, and never starts a game twice."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.routes import downloads as dl_routes
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core.library import Library
from deckdrop.network.peer_registry import PeerEntry, PeerRegistry
from deckdrop.network.transfer import DownloadStatus


def _status(download_id: str) -> DownloadStatus:
    return DownloadStatus(
        id=download_id,
        game_id="g1",
        game_name="Test Game",
        peer_id="host",
        peer_name="Host",
        status="queued",
        progress=0.0,
        speed_bytes_sec=0,
        downloaded_bytes=0,
        total_bytes=0,
        num_peers=0,
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.download_dir = tmp_path / "dl"
    cfg_mod.save(cfg)
    monkeypatch.setattr(dl_routes, "_START_GRACE_SECONDS", 0.05)
    dl_routes._pending_starts.clear()
    dl_routes._pending_tasks.clear()
    dl_routes._detached_starts.clear()

    registry = PeerRegistry()
    registry._peers["host"] = PeerEntry(
        peer_id="host",
        name="Host",
        address="192.168.1.10",
        port=7373,
        games=[{"id": "g1", "name": "Test Game", "has_torrent": True}],
    )
    transfer = MagicMock()
    transfer.all_statuses.return_value = []
    started: dict[str, str] = {}

    def _start(**kw):
        started["id"] = kw["download_id"]
        transfer.all_statuses.return_value = [_status(kw["download_id"])]
        return kw["download_id"]

    transfer.start_download.side_effect = _start
    transfer.get_status.side_effect = lambda did: _status(did)
    app_state.init(cfg, Library(), registry, transfer=transfer)
    yield transfer, started
    dl_routes._pending_starts.clear()


def _slow_host(release: threading.Event):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"magnet": "magnet:?xt=urn:btih:abc"}

    def _get(*_a, **_kw):
        release.wait(5)
        return resp

    return _get


def _wait_for(cond, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_slow_host_returns_placeholder_and_dedupes(setup):
    transfer, started = setup
    release = threading.Event()
    with TestClient(create_app()) as c, patch("httpx.get", side_effect=_slow_host(release)):
        r1 = c.post("/api/download", json={"peer_id": "host", "game_id": "g1"})
        assert r1.status_code == 202
        first = r1.json()
        assert first["phase"] == "starting"
        assert first["game_id"] == "g1"

        # Shown in the list right away.
        listed = c.get("/api/downloads").json()
        assert [d["id"] for d in listed] == [first["id"]]

        # Second click while the host is still answering: same download.
        r2 = c.post("/api/download", json={"peer_id": "host", "game_id": "g1"})
        assert r2.status_code == 202
        assert r2.json()["id"] == first["id"]

        release.set()
        assert _wait_for(lambda: "id" in started)
        assert started["id"] == first["id"]
        assert transfer.start_download.call_count == 1
        assert _wait_for(lambda: not dl_routes._pending_starts)

        # Once running, the real download is found instead.
        r3 = c.post("/api/download", json={"peer_id": "host", "game_id": "g1"})
        assert r3.json()["id"] == first["id"]
        assert transfer.start_download.call_count == 1


def test_remove_pending_start_cancels_it(setup):
    transfer, started = setup
    release = threading.Event()
    with TestClient(create_app()) as c, patch("httpx.get", side_effect=_slow_host(release)):
        did = c.post("/api/download", json={"peer_id": "host", "game_id": "g1"}).json()["id"]
        assert c.delete(f"/api/downloads/{did}").status_code == 204
        assert c.get("/api/downloads").json() == []
        release.set()
        assert _wait_for(lambda: not dl_routes._pending_tasks)
        transfer.start_download.assert_not_called()
        transfer.release_download_dest.assert_called()
