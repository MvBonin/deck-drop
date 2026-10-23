"""host_env() must not leak the bundle's library paths to host programs."""

from __future__ import annotations

import os
import sys

from deckdrop.core import hostenv


def _frozen(monkeypatch, tmp_path):
    bundle = tmp_path / "mount" / "usr" / "bin" / "_internal"
    bundle.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("APPDIR", str(tmp_path / "mount"))
    return bundle


def test_not_frozen_keeps_env(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/x")
    assert hostenv.host_env()["LD_LIBRARY_PATH"] == "/opt/x"


def test_frozen_restores_original(monkeypatch, tmp_path):
    bundle = _frozen(monkeypatch, tmp_path)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", f"{tmp_path / 'mount' / 'usr' / 'bin'}:/opt/host")
    env = hostenv.host_env()
    assert env["LD_LIBRARY_PATH"] == "/opt/host"
    assert "LD_LIBRARY_PATH_ORIG" not in env


def test_frozen_without_original_drops_var(monkeypatch, tmp_path):
    bundle = _frozen(monkeypatch, tmp_path)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(bundle))
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setenv("PATH", f"{tmp_path / 'mount' / 'usr' / 'bin'}{os.pathsep}/usr/bin")
    env = hostenv.host_env({"X": "1"})
    assert "LD_LIBRARY_PATH" not in env
    assert env["PATH"] == "/usr/bin"
    assert env["X"] == "1"
