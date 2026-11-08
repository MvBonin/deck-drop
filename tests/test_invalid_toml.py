"""Invalid deckdrop.toml: repair what can be repaired, flag the rest for re-adding."""

from __future__ import annotations

import tomllib

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import deps as deps_mod
from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core import game as game_mod
from deckdrop.core.library import Library


@pytest.fixture(autouse=True)
def _config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "cfg" / "config.toml")


def _write(folder, text):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / game_mod.TOML_FILENAME).write_text(text, encoding="utf-8")


def _backups():
    d = game_mod.backup_dir()
    return sorted(d.iterdir()) if d.exists() else []


# ── game.load_from_path ───────────────────────────────────────────────────────


def test_syntax_error_raises_invalid(tmp_path):
    _write(tmp_path / "G", '[game]\nname = "Broken\n')
    with pytest.raises(game_mod.InvalidGameToml) as exc:
        game_mod.load_from_path(tmp_path / "G")
    assert exc.value.path == tmp_path / "G" / game_mod.TOML_FILENAME


def test_missing_game_section_raises_invalid(tmp_path):
    _write(tmp_path / "G", '[files]\n"a.bin" = "abc"\n')
    with pytest.raises(game_mod.InvalidGameToml, match=r"\[game\]"):
        game_mod.load_from_path(tmp_path / "G")


def test_valid_toml_is_not_rewritten(tmp_path):
    info = game_mod.create_new(tmp_path / "G", name="Fine", added_by="me")
    game_mod.save(info)
    before = (tmp_path / "G" / game_mod.TOML_FILENAME).read_bytes()
    loaded = game_mod.load_from_path(tmp_path / "G")
    assert loaded.id == info.id
    assert (tmp_path / "G" / game_mod.TOML_FILENAME).read_bytes() == before
    assert _backups() == []


def test_wrong_types_are_repaired_keeping_id(tmp_path):
    _write(
        tmp_path / "Celeste",
        """
[game]
id = "ab12cd34"
name = ""
version = "3"
size_bytes = -5
platform = "amiga"
description = 42

[steam]
app_id = "nope"

[files]
"ok.bin" = "deadbeef"
"bad.bin" = 7

[content]
revision = 0
ignore = "saves/"

[[history]]
revision = 2
by = "me"
""",
    )
    g = game_mod.load_from_path(tmp_path / "Celeste")
    assert g.id == "ab12cd34"
    assert g.name == "Celeste"
    assert g.version == 3
    assert g.size_bytes == 0
    assert g.platform == "any"
    assert g.description == ""
    assert g.steam.app_id is None
    assert g.files == {"ok.bin": "deadbeef"}
    assert g.content.revision == 1
    assert g.content.ignore == []
    assert g.history[0].revision == 2

    # Written back once, original kept outside the game folder.
    on_disk = tomllib.loads((tmp_path / "Celeste" / game_mod.TOML_FILENAME).read_text())
    assert on_disk["game"]["platform"] == "any"
    assert on_disk["files"] == {"ok.bin": "deadbeef"}
    backups = _backups()
    assert len(backups) == 1 and 'platform = "amiga"' in backups[0].read_text()
    assert not any(p.name != game_mod.TOML_FILENAME for p in (tmp_path / "Celeste").iterdir())


def test_missing_id_gets_a_stable_one(tmp_path):
    _write(tmp_path / "G", '[game]\nname = "No ID"\n')
    first = game_mod.load_from_path(tmp_path / "G")
    second = game_mod.load_from_path(tmp_path / "G")
    assert first.id and first.id == second.id


def test_unsafe_id_is_replaced(tmp_path):
    _write(tmp_path / "G", '[game]\nid = "../../etc"\nname = "X"\n')
    assert game_mod.load_from_path(tmp_path / "G").id != "../../etc"


# ── Library ───────────────────────────────────────────────────────────────────


def test_library_skips_broken_and_loads_the_rest(tmp_path):
    cfg = cfg_mod.load()
    cfg.download_dir = tmp_path / "games"
    good = game_mod.create_new(tmp_path / "games" / "Good", name="Good", added_by="me")
    game_mod.save(good)
    _write(tmp_path / "games" / "Bad", "not toml at all [[[")

    lib = Library()
    lib.reload(cfg)
    assert [g.name for g in lib.all()] == ["Good"]
    assert list(lib.broken()) == [tmp_path / "games" / "Bad"]


# ── API ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    cfg = cfg_mod.load()
    cfg.user_name = "me"
    cfg.download_dir = tmp_path / "games"
    cfg_mod.save(cfg)
    app_state.init(cfg, Library())
    return TestClient(create_app())


def test_api_lists_broken_games(client, tmp_path):
    _write(tmp_path / "games" / "Bad", "[game\n")
    r = client.get("/api/games")
    assert r.status_code == 200 and r.json() == []
    broken = client.get("/api/games/broken").json()
    assert [b["path"] for b in broken] == [str(tmp_path / "games" / "Bad")]
    assert "deckdrop.toml" in broken[0]["error"]


def test_readd_broken_game_asks_for_wizard_then_replaces(client, tmp_path):
    folder = tmp_path / "games" / "Bad"
    _write(folder, "[game\n")

    r = client.post("/api/games", json={"path": str(folder)})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "toml_invalid"

    r = client.post("/api/games", json={"path": str(folder), "name": "Bad Game"})
    assert r.status_code == 201
    assert r.json()["name"] == "Bad Game"
    assert game_mod.load_from_path(folder).name == "Bad Game"
    assert [b.read_text() for b in _backups()] == ["[game\n"]

    client.get("/api/games")
    assert client.get("/api/games/broken").json() == []


def test_broken_list_is_local_only(client, monkeypatch):
    monkeypatch.setattr(deps_mod, "_LOCAL_HOSTS", {"127.0.0.1", "::1", "localhost"})
    assert client.get("/api/games/broken").status_code == 403
