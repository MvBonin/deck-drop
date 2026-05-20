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
