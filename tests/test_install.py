"""AppImage self-install, kiosk browser lookup, attaching to a running server."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deckdrop import __version__
from deckdrop import main as main_mod
from deckdrop.core import install as install_mod
from deckdrop.core import kiosk as kiosk_mod
from deckdrop.core import service as svc


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return home


@pytest.fixture
def appimage(tmp_path, monkeypatch):
    src = tmp_path / "Downloads" / "DeckDrop-1.0.3-x86_64.AppImage"
    src.parent.mkdir()
    src.write_bytes(b"\x7fELF fake appimage")
    monkeypatch.setattr(install_mod, "_STARTED_FROM", str(src))
    return src


def test_install_copies_to_stable_path_and_writes_menu_entries(fake_home, appimage):
    target = install_mod.install()
    assert target == fake_home / "Applications" / "DeckDrop.AppImage"
    assert target.read_bytes() == appimage.read_bytes()
    assert target.stat().st_mode & 0o111
    apps = fake_home / ".local" / "share" / "applications"
    normal = (apps / "deckdrop.desktop").read_text()
    kiosk = (apps / "deckdrop-gaming-mode.desktop").read_text()
    assert f"Exec={target}\n" in normal
    assert f"Exec={target} --kiosk\n" in kiosk

    st = install_mod.status()
    assert st.installed and st.desktop_entries and st.can_install
    assert not st.running_installed  # still running from ~/Downloads


def test_install_from_the_installed_copy_is_a_noop_copy(fake_home, monkeypatch, appimage):
    target = install_mod.install()
    monkeypatch.setattr(install_mod, "_STARTED_FROM", str(target))
    assert install_mod.install() == target
    assert install_mod.status().running_installed


def test_install_without_appimage_raises(fake_home, monkeypatch):
    monkeypatch.setattr(install_mod, "_STARTED_FROM", "")
    monkeypatch.delenv("APPIMAGE", raising=False)
    with pytest.raises(ValueError):
        install_mod.install()


def test_uninstall_removes_entries_keeps_file(fake_home, appimage):
    target = install_mod.install()
    install_mod.uninstall()
    assert not (fake_home / ".local/share/applications/deckdrop.desktop").exists()
    assert target.exists()


def test_desktop_entry_quotes_paths_with_spaces():
    entry = install_mod.desktop_entry(install_mod.Path('/home/me/My Apps/Deck"Drop'), kiosk=True)
    assert 'Exec="/home/me/My Apps/Deck\\"Drop" --kiosk' in entry


def test_service_unit_quotes_paths_and_marks_the_service():
    unit = svc._unit_content(
        svc._exec_start("appimage", "/home/me/My Apps/DeckDrop.AppImage"),
        appimage_path="/home/me/My Apps/DeckDrop.AppImage",
    )
    assert 'ExecStart="/home/me/My Apps/DeckDrop.AppImage" --headless' in unit
    assert 'Environment="APPIMAGE=/home/me/My Apps/DeckDrop.AppImage"' in unit
    assert "Environment=DECKDROP_SERVICE=1" in unit


def test_user_bus_env_fills_missing_session_vars():
    env = svc.user_bus_env({})
    assert env["XDG_RUNTIME_DIR"].startswith("/run/user/")
    assert env["DBUS_SESSION_BUS_ADDRESS"] == f"unix:path={env['XDG_RUNTIME_DIR']}/bus"
    kept = svc.user_bus_env({"XDG_RUNTIME_DIR": "/x", "DBUS_SESSION_BUS_ADDRESS": "y"})
    assert kept == {"XDG_RUNTIME_DIR": "/x", "DBUS_SESSION_BUS_ADDRESS": "y"}


# -- kiosk browser --


def test_kiosk_prefers_native_chromium_with_own_profile(fake_home, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "chromium"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    cmd = kiosk_mod.browser_command("http://127.0.0.1:7373", {"PATH": str(bindir)})
    assert cmd[0] == str(exe)
    assert any(a.startswith("--user-data-dir=") for a in cmd)
    assert "--app=http://127.0.0.1:7373" in cmd


def test_kiosk_falls_back_to_flatpak_chromium(fake_home, monkeypatch):
    monkeypatch.setattr(
        kiosk_mod, "_flatpak_installed", lambda app_id, env: app_id == "org.chromium.Chromium"
    )
    cmd = kiosk_mod.browser_command("http://x", {"PATH": "/nonexistent"})
    assert cmd[:3] == ["flatpak", "run", "org.chromium.Chromium"]
    assert f"--user-data-dir={fake_home}/.var/app/org.chromium.Chromium/data/deckdrop-kiosk" in cmd


def test_kiosk_none_without_browser(monkeypatch):
    monkeypatch.setattr(kiosk_mod, "_flatpak_installed", lambda app_id, env: False)
    assert kiosk_mod.browser_command("http://x", {"PATH": "/nonexistent"}) is None


# -- attach instead of replacing a running server --


def test_attach_opens_ui_of_running_server(monkeypatch):
    monkeypatch.setattr(
        main_mod, "_server_status", lambda port: {"peer_id": "p", "version": __version__}
    )
    opened = []
    from deckdrop.core import hostenv

    monkeypatch.setattr(hostenv, "open_url", opened.append)
    assert main_mod._attach_to_running(7373, kiosk=False) is True
    assert opened == ["http://127.0.0.1:7373"]


def test_attach_kiosk_waits_for_window(monkeypatch):
    monkeypatch.setattr(
        main_mod, "_server_status", lambda port: {"peer_id": "p", "version": __version__}
    )
    waited = []
    proc = SimpleNamespace(wait=lambda: waited.append(True))
    monkeypatch.setattr(kiosk_mod, "open_window", lambda url: proc)
    assert main_mod._attach_to_running(7373, kiosk=True) is True
    assert waited == [True]


def test_no_attach_when_nothing_runs(monkeypatch):
    monkeypatch.setattr(main_mod, "_server_status", lambda port: None)
    assert main_mod._attach_to_running(7373, kiosk=False) is False


def test_other_version_without_service_is_replaced(monkeypatch):
    monkeypatch.setattr(main_mod, "_server_status", lambda port: {"peer_id": "p", "version": "0.1"})
    monkeypatch.setattr(svc, "is_installed", lambda: False)
    assert main_mod._attach_to_running(7373, kiosk=False) is False


def test_other_version_in_service_restarts_service_then_attaches(monkeypatch):
    calls = iter([{"peer_id": "p", "version": "0.1"}])
    monkeypatch.setattr(main_mod, "_server_status", lambda port: next(calls, None))
    monkeypatch.setattr(main_mod, "_wait_for_server", lambda port, timeout: {"peer_id": "p"})
    monkeypatch.setattr(svc, "is_installed", lambda: True)
    monkeypatch.setattr(svc, "is_active", lambda: True)
    restarted = []
    monkeypatch.setattr(svc, "restart", lambda: restarted.append(True) or True)
    from deckdrop.core import hostenv

    monkeypatch.setattr(hostenv, "open_url", lambda url: None)
    assert main_mod._attach_to_running(7373, kiosk=False) is True
    assert restarted == [True]
