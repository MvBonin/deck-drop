"""GET /api/fs/browse – folder picker for "Add game"."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import deps as deps_mod
from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core.library import Library


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.user_name = "testuser"
    cfg_mod.save(cfg)
    app_state.init(cfg, Library())
    return TestClient(create_app())


def test_browse_lists_subfolders_sorted_without_hidden_or_files(client, tmp_path):
    root = tmp_path / "games"
    for name in ("celeste", "Baba Is You", ".cache"):
        (root / name).mkdir(parents=True)
    (root / "readme.txt").write_text("x")

    r = client.get("/api/fs/browse", params={"path": str(root)})
    assert r.status_code == 200
    data = r.json()
    assert data["path"] == str(root.resolve())
    assert data["parent"] == str(root.resolve().parent)
    assert [d["name"] for d in data["dirs"]] == ["Baba Is You", "celeste"]
    assert data["dirs"][1]["path"] == str(root.resolve() / "celeste")
    assert any(s["name"] == "~" for s in data["shortcuts"])


def test_browse_defaults_to_home(client, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = client.get("/api/fs/browse")
    assert r.status_code == 200
    assert r.json()["path"] == str(tmp_path.resolve())


def test_browse_root_has_no_parent(client):
    r = client.get("/api/fs/browse", params={"path": "/"})
    assert r.status_code == 200
    assert r.json()["parent"] is None


def test_browse_missing_folder_404(client, tmp_path):
    r = client.get("/api/fs/browse", params={"path": str(tmp_path / "nope")})
    assert r.status_code == 404


def test_browse_rejects_remote(client, monkeypatch):
    monkeypatch.setattr(deps_mod, "_LOCAL_HOSTS", {"127.0.0.1", "::1", "localhost"})
    assert client.get("/api/fs/browse").status_code == 403
