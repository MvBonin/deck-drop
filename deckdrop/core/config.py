"""User configuration – stored at ~/.config/deckdrop/config.toml"""

from __future__ import annotations

import tomllib
import uuid
from pathlib import Path
from typing import Any

import tomli_w

CONFIG_PATH = Path.home() / ".config" / "deckdrop" / "config.toml"

_DEFAULTS: dict[str, Any] = {
    "user": {
        "name": "",
        "peer_id": "",
        "onboarding_complete": False,
    },
    "paths": {
        "download_dir": str(Path.home() / "Games" / "DeckDrop-Games"),
        "torrent_cache": str(Path.home() / ".local" / "share" / "deckdrop" / "torrents"),
        # List of individual game folder paths added manually
        "game_paths": [],
    },
    "network": {
        "port": 7373,
        "torrent_port": 7374,
        "announce_interval": 30,
    },
    "transfer": {
        "max_upload_speed": 0,
        "max_download_speed": 0,
        "max_connections": 50,
        "seed_after_download": True,
    },
}


class Config:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    # -- user --

    @property
    def user_name(self) -> str:
        return self._data["user"]["name"]

    @user_name.setter
    def user_name(self, value: str) -> None:
        self._data["user"]["name"] = value

    @property
    def peer_id(self) -> str:
        return self._data["user"]["peer_id"]

    @property
    def onboarding_complete(self) -> bool:
        return self._data["user"]["onboarding_complete"]

    @onboarding_complete.setter
    def onboarding_complete(self, value: bool) -> None:
        self._data["user"]["onboarding_complete"] = value

    # -- paths --

    @property
    def download_dir(self) -> Path:
        return Path(self._data["paths"]["download_dir"]).expanduser()

    @download_dir.setter
    def download_dir(self, value: Path) -> None:
        self._data["paths"]["download_dir"] = str(value)

    @property
    def torrent_cache(self) -> Path:
        return Path(self._data["paths"]["torrent_cache"]).expanduser()

    @property
    def downloads_state_path(self) -> Path:
        base = Path(self._data["paths"]["torrent_cache"]).expanduser().parent
        return base / "downloads-state.json"

    @property
    def game_paths(self) -> list[Path]:
        return [Path(p).expanduser() for p in self._data["paths"]["game_paths"]]

    def add_game_path(self, path: Path) -> None:
        p = str(path.expanduser().resolve())
        if p not in self._data["paths"]["game_paths"]:
            self._data["paths"]["game_paths"].append(p)

    def remove_game_path(self, path: Path) -> None:
        p = str(path.expanduser().resolve())
        self._data["paths"]["game_paths"] = [x for x in self._data["paths"]["game_paths"] if x != p]

    # -- network --

    @property
    def port(self) -> int:
        return self._data["network"]["port"]

    @property
    def torrent_port(self) -> int:
        return self._data["network"]["torrent_port"]

    @property
    def announce_interval(self) -> int:
        return self._data["network"]["announce_interval"]

    # -- transfer --

    @property
    def seed_after_download(self) -> bool:
        return self._data["transfer"]["seed_after_download"]

    @seed_after_download.setter
    def seed_after_download(self, value: bool) -> None:
        self._data["transfer"]["seed_after_download"] = value

    @property
    def max_upload_speed(self) -> int:
        return self._data["transfer"]["max_upload_speed"]

    @max_upload_speed.setter
    def max_upload_speed(self, value: int) -> None:
        self._data["transfer"]["max_upload_speed"] = value

    @property
    def max_download_speed(self) -> int:
        return self._data["transfer"]["max_download_speed"]

    @max_download_speed.setter
    def max_download_speed(self, value: int) -> None:
        self._data["transfer"]["max_download_speed"] = value

    def to_dict(self) -> dict[str, Any]:
        return self._data


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base without losing keys added in future versions."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load() -> Config:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as f:
            on_disk = tomllib.load(f)
        data = _deep_merge(_DEFAULTS, on_disk)
    else:
        import copy

        data = copy.deepcopy(_DEFAULTS)

    # Ensure peer_id is always set
    if not data["user"]["peer_id"]:
        data["user"]["peer_id"] = str(uuid.uuid4())
        _save_raw(data)

    return Config(data)


def save(cfg: Config) -> None:
    _save_raw(cfg.to_dict())


def _save_raw(data: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("wb") as f:
        tomli_w.dump(data, f)
