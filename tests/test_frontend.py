"""Playwright smoke tests: verify the frontend loads and key UI elements are present.

These tests start a real DeckDrop server (via the session-scoped live_server_url
fixture) and drive a headless Chromium browser against it.

Run separately from unit tests:
    pytest tests/test_frontend.py --headed   # show browser window
    pytest tests/test_frontend.py            # headless
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


@pytest.fixture(autouse=True)
def _set_base_url(page: Page, live_server_url: str) -> None:
    """Navigate every test to the app root before the test body runs."""
    page.goto(live_server_url, wait_until="domcontentloaded")


def test_nav_renders(page: Page) -> None:
    """The nav bar with all four tab buttons is visible after the app mounts."""
    nav = page.locator("nav.nav")
    expect(nav).to_be_visible(timeout=10_000)
    # All four German-labelled tabs must be present
    for label in ("Meine Spiele", "Netzwerk", "Downloads", "Einstellungen"):
        expect(nav.get_by_text(label)).to_be_visible()


def test_default_view_is_my_games(page: Page) -> None:
    """The active tab on first load is 'Meine Spiele' (My Games)."""
    active = page.locator("button.nav-tab.active")
    expect(active).to_contain_text("Meine Spiele", timeout=10_000)


def test_settings_view_loads(page: Page) -> None:
    """Clicking the settings tab shows the settings panel with a save button."""
    page.locator("nav.nav").get_by_text("Einstellungen").click()
    expect(page.get_by_role("button", name="Speichern")).to_be_visible(timeout=10_000)


def test_api_status_endpoint(page: Page, live_server_url: str) -> None:
    """The /api/status endpoint returns a JSON object with peer_id."""
    response = page.request.get(f"{live_server_url}/api/status")
    assert response.status == 200
    data = response.json()
    assert "peer_id" in data
    assert data["name"] == "E2EUser"


def test_debug_mode_via_url(page: Page, live_server_url: str) -> None:
    """`?debug=1` adds a Debug tab listing hash events; `?debug=0` removes it."""
    nav = page.locator("nav.nav")
    expect(nav).to_be_visible(timeout=10_000)
    expect(nav.get_by_text("Debug")).to_have_count(0)

    page.goto(f"{live_server_url}/?debug=1", wait_until="domcontentloaded")
    nav.get_by_text("Debug").click()
    expect(page.get_by_text("Hash-Ereignisse (warum wird gehasht?)")).to_be_visible(timeout=10_000)

    # Remembered without the parameter, switched off with ?debug=0.
    page.goto(live_server_url, wait_until="domcontentloaded")
    expect(nav.get_by_text("Debug")).to_be_visible(timeout=10_000)
    page.goto(f"{live_server_url}/?debug=0", wait_until="domcontentloaded")
    expect(nav).to_be_visible(timeout=10_000)
    expect(nav.get_by_text("Debug")).to_have_count(0)


def test_update_button_visible_while_hashing(page: Page, live_server_url: str) -> None:
    """A game still hashing its old version must already offer "Aktualisieren…"
    (the update replaces that hash). /api/games is stubbed – no peer needed."""
    game = {
        "id": "dawn1",
        "name": "Dawnwalker",
        "version": 1,
        "size_bytes": 1024,
        "platform": "linux",
        "available": True,
        "added_by": "E2EUser",
        "updated_at": "2026-09-26T00:00:00+00:00",
        "has_torrent": True,
        "torrent_preparing": True,
        "torrent_prep_progress": 0.3,
        "content_state": "hashing",
        "revision": 1,
        "version_label": "1.0",
        "update_available": True,
        "update_version_label": "1.0.5",
    }
    page.route("**/api/games", lambda route: route.fulfill(json=[game]))
    page.goto(live_server_url, wait_until="domcontentloaded")

    card = page.get_by_role("article", name="Dawnwalker")
    expect(card.get_by_text("Update verfügbar: 1.0.5")).to_be_visible(timeout=10_000)
    expect(card.get_by_role("button", name="Aktualisieren…")).to_be_visible()
    expect(card.get_by_text("Game-Hashes berechnen…")).to_be_visible()


def test_downloads_show_update_preparation(page: Page, live_server_url: str) -> None:
    """An update that is still reading existing files shows its own phase
    (and can be paused) instead of looking like a frozen download."""
    dl = {
        "id": "u1",
        "game_id": "dawn1",
        "game_name": "Dawnwalker",
        "peer_id": "pc",
        "peer_name": "MVB Desktop",
        "status": "verifying",
        "progress": 0.0,
        "speed_bytes_sec": 0,
        "downloaded_bytes": 0,
        "total_bytes": 0,
        "num_peers": 0,
        "phase": "preparing",
        "phase_progress": 0.42,
        "kind": "update",
        "target_version_label": "1.0.5",
    }
    page.route("**/api/downloads", lambda route: route.fulfill(json=[dl]))
    page.goto(live_server_url, wait_until="domcontentloaded")
    page.locator("nav.nav").get_by_text("Downloads").click()

    expect(page.get_by_text("Update: Dawnwalker → 1.0.5")).to_be_visible(timeout=10_000)
    expect(page.get_by_text("Prüfe vorhandene Dateien 42 %")).to_be_visible()
    expect(page.get_by_role("button", name="Pause")).to_be_visible()


def test_restart_button_while_update_runs(page: Page, live_server_url: str) -> None:
    """A running update whose host moved on offers "Update neu starten"."""
    game = {
        "id": "dawn1",
        "name": "Dawnwalker",
        "version": 1,
        "size_bytes": 1024,
        "platform": "linux",
        "available": True,
        "added_by": "E2EUser",
        "updated_at": "2026-09-26T00:00:00+00:00",
        "has_torrent": True,
        "content_state": "updating",
        "revision": 1,
        "version_label": "1.0",
        "update_available": True,
        "update_version_label": "1.0.6",
        "update_restart_available": True,
        "update_restart_label": "1.0.6",
    }
    page.route("**/api/games", lambda route: route.fulfill(json=[game]))
    page.goto(live_server_url, wait_until="domcontentloaded")

    card = page.get_by_role("article", name="Dawnwalker")
    expect(card.get_by_text("Wird aktualisiert…", exact=False)).to_be_visible(timeout=10_000)
    expect(card.get_by_role("button", name="Update neu starten: 1.0.6…")).to_be_visible()


def test_repair_dialog_offers_version_and_source(page: Page, live_server_url: str) -> None:
    game = {
        "id": "dawn1",
        "name": "Dawnwalker",
        "version": 1,
        "size_bytes": 1024,
        "platform": "linux",
        "available": True,
        "added_by": "E2EUser",
        "updated_at": "2026-09-26T00:00:00+00:00",
        "has_torrent": True,
        "content_state": "clean",
        "revision": 2,
        "version_label": "1.0.5",
    }
    updates = {
        "current": {
            "revision": 2,
            "version_label": "1.0.5",
            "content_hash": "k2",
            "state": "clean",
        },
        "versions": [
            {
                "version_key": "k2",
                "revision": 2,
                "version_label": "1.0.5",
                "shareable": True,
                "has_torrent": True,
                "is_current": True,
                "is_newer": False,
                "peers": [
                    {"peer_id": "pc", "peer_name": "MVB Desktop"},
                    {"peer_id": "d2", "peer_name": "Deck 2"},
                ],
            }
        ],
    }
    page.route("**/api/games", lambda route: route.fulfill(json=[game]))
    page.route("**/api/games/dawn1/updates", lambda route: route.fulfill(json=updates))
    page.goto(live_server_url, wait_until="domcontentloaded")

    page.get_by_role("button", name="Spiel reparieren").click()
    dialog = page.get_by_role("dialog", name="Spiel reparieren")
    expect(dialog.get_by_text("1.0.5").first).to_be_visible(timeout=10_000)
    expect(dialog.get_by_label("Quelle")).to_have_value("pc")
    expect(dialog.get_by_role("button", name="Reparieren", exact=True)).to_be_enabled()
