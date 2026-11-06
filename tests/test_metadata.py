"""Online metadata lookup (Steam store) for Add/Edit game."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from deckdrop.api import state as app_state
from deckdrop.api.server import create_app
from deckdrop.core import config as cfg_mod
from deckdrop.core import metadata
from deckdrop.core.library import Library

SEARCH_PAYLOAD = {
    "total": 3,
    "items": [
        {
            "type": "app",
            "name": "Celeste",
            "id": 504230,
            "tiny_image": "https://example.invalid/capsule.jpg",
            "platforms": {"windows": True, "mac": True, "linux": True},
        },
        {"type": "sub", "name": "Celeste Bundle", "id": 1},
        {"type": "app", "name": "Celeste", "id": 504230},  # duplicate
        {"type": "app", "name": "", "id": 5},  # no name
        {"type": "app", "name": "Celeste Classic", "id": "77", "platforms": {"windows": True}},
    ],
}

DETAILS_PAYLOAD = {
    "504230": {
        "success": True,
        "data": {
            "name": "Celeste",
            "short_description": "Help Madeline survive her <b>inner demons</b> &amp; climb.",
            "developers": ["Maddy Makes Games"],
            "publishers": ["Maddy Makes Games"],
            "platforms": {"windows": True, "mac": True, "linux": True},
            "release_date": {"coming_soon": False, "date": "25 Jan, 2018"},
        },
    }
}


def test_parse_search_keeps_apps_dedupes_and_builds_urls():
    results = metadata.parse_search(SEARCH_PAYLOAD)
    assert [r.steam_app_id for r in results] == [504230, 77]
    first = results[0]
    assert first.name == "Celeste"
    assert first.thumb_url == "https://example.invalid/capsule.jpg"
    assert first.cover_url.endswith("/504230/library_600x900.jpg")
    assert first.platforms == ["windows", "linux", "mac"]
    # no tiny_image → capsule from the CDN
    assert results[1].thumb_url.endswith("/77/capsule_231x87.jpg")


def test_parse_search_garbage_is_empty():
    assert metadata.parse_search(None) == []
    assert metadata.parse_search({"items": "nope"}) == []


def test_parse_details_strips_html_and_suggests_platform():
    d = metadata.parse_details(504230, DETAILS_PAYLOAD)
    assert d is not None
    assert d.description == "Help Madeline survive her inner demons & climb."
    assert d.developers == ["Maddy Makes Games"]
    assert d.release_date == "25 Jan, 2018"
    assert d.suggested_platform == "linux"


def test_parse_details_unknown_app():
    assert metadata.parse_details(1, {"1": {"success": False}}) is None
    assert metadata.parse_details(1, {}) is None


@pytest.mark.parametrize(
    ("platforms", "expected"),
    [(["windows"], "windows"), (["windows", "linux"], "linux"), ([], "any"), (["mac"], "any")],
)
def test_suggest_platform(platforms, expected):
    assert metadata.suggest_platform(platforms) == expected


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    cfg.user_name = "testuser"
    cfg_mod.save(cfg)
    app_state.init(cfg, Library())
    return TestClient(create_app())


def test_search_route(client, monkeypatch):
    seen = {}

    async def fake_search(q, *, lang="english", timeout=8.0):
        seen["q"], seen["lang"] = q, lang
        return metadata.parse_search(SEARCH_PAYLOAD)

    monkeypatch.setattr(metadata, "search", fake_search)
    r = client.get("/api/metadata/search", params={"q": "celeste"})
    assert r.status_code == 200
    assert [x["steam_app_id"] for x in r.json()["results"]] == [504230, 77]
    assert seen == {"q": "celeste", "lang": "english"}


def test_search_route_empty_query_does_not_hit_network(client, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("must not search")

    monkeypatch.setattr(metadata, "search", boom)
    r = client.get("/api/metadata/search", params={"q": "  "})
    assert r.status_code == 200
    assert r.json() == {"results": []}


def test_search_route_offline_is_502(client, monkeypatch):
    async def offline(*a, **k):
        raise metadata.MetadataError("no network")

    monkeypatch.setattr(metadata, "search", offline)
    assert client.get("/api/metadata/search", params={"q": "x"}).status_code == 502


def test_details_route(client, monkeypatch):
    async def fake_details(app_id, *, lang="english", timeout=8.0):
        return metadata.parse_details(app_id, DETAILS_PAYLOAD)

    monkeypatch.setattr(metadata, "details", fake_details)
    r = client.get("/api/metadata/steam/504230")
    assert r.status_code == 200
    assert r.json()["suggested_platform"] == "linux"
    assert client.get("/api/metadata/steam/1").status_code == 404


def test_add_game_wizard_stores_description(client, tmp_path):
    game_dir = tmp_path / "Celeste"
    game_dir.mkdir()
    r = client.post(
        "/api/games",
        json={"path": str(game_dir), "name": "Celeste", "description": "Climb a mountain."},
    )
    assert r.status_code == 201
    assert r.json()["description"] == "Climb a mountain."
