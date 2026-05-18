from pathlib import Path

import tomli_w

from deckdrop.core import config as cfg_mod


def test_load_creates_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg = cfg_mod.load()
    assert cfg.peer_id  # auto-generated
    assert cfg.download_dir == Path.home() / "Games" / "DeckDrop-Games"
    assert cfg.game_paths == []


def test_save_and_reload(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", config_path)

    cfg = cfg_mod.load()
    cfg.user_name = "TestUser"
    cfg.add_game_path(tmp_path / "MyGame")
    cfg_mod.save(cfg)

    cfg2 = cfg_mod.load()
    assert cfg2.user_name == "TestUser"
    assert tmp_path / "MyGame" in cfg2.game_paths


def test_peer_id_stable(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    cfg1 = cfg_mod.load()
    cfg2 = cfg_mod.load()
    assert cfg1.peer_id == cfg2.peer_id


def test_deep_merge_keeps_new_defaults(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", config_path)

    # Write a minimal config missing the transfer section
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("wb") as f:
        tomli_w.dump({"user": {"name": "X", "peer_id": "abc", "onboarding_complete": False}}, f)

    cfg = cfg_mod.load()
    # Should fall back to defaults for missing keys
    assert cfg.seed_after_download is True
