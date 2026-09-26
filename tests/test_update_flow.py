"""Phase 5: applying an update on the receiver (TransferManager.start_update /
_finalize_update), without libtorrent (mocked session, like
tests/test_auto_torrent_upgrade.py). The real libtorrent delta-transfer proof
lives in test_update_flow_libtorrent.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from deckdrop.api import state as app_state
from deckdrop.core import config as cfg_mod
from deckdrop.core import game as game_mod
from deckdrop.core import torrent as torrent_mod
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library
from deckdrop.network.transfer import TransferManager


@dataclass
class _FakeFileStorage:
    paths: list[str]

    def num_files(self) -> int:
        return len(self.paths)

    def file_path(self, i: int) -> str:
        return self.paths[i]


class _FakeTorrentInfo:
    def __init__(self, paths: list[str]):
        self._fs = _FakeFileStorage(list(paths))
        self.renamed: dict[int, str] = {}

    def files(self):
        return self._fs

    def rename_file(self, i: int, new_path: str) -> None:
        self._fs.paths[i] = new_path
        self.renamed[i] = new_path

    def info_hashes(self):
        return MagicMock(v1="deadbeef" * 5)


class _FakeLt:
    """Just enough of the libtorrent module surface for start_update()."""

    def __init__(self, ti: _FakeTorrentInfo):
        self._ti = ti

    def bdecode(self, data: bytes):
        return data

    def torrent_info(self, _decoded):
        return self._ti

    def add_torrent_params(self):
        return MagicMock()


@pytest.fixture
def tm_factory(tmp_path, monkeypatch):
    """Build a TransferManager with a mocked libtorrent session (no libtorrent needed)."""
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(torrent_mod, "lan_session", lambda port: MagicMock())
    cfg = cfg_mod.load()
    cfg._data["paths"]["download_dir"] = str(tmp_path / "games")
    cfg._data["paths"]["torrent_cache"] = str(tmp_path / "torrents")
    cfg._data["paths"]["resume_dir"] = str(tmp_path / "resume")
    cfg_mod.save(cfg)

    def _make():
        tm = TransferManager(cfg)
        tm._session = MagicMock()
        return cfg, tm

    return _make


@dataclass
class _Peer:
    peer_id: str
    name: str
    address: str
    port: int = 7374


def _setup_game(tmp_path, make_game):
    info = make_game(tmp_path, "Stardew Valley")
    (info.path / "bin.exe").write_bytes(b"x" * 100)
    (info.path / "assets").mkdir()
    (info.path / "assets" / "data.pak").write_bytes(b"y" * 50)
    (info.path / "old_readme.txt").write_bytes(b"old")
    (info.path / "saves").mkdir()
    (info.path / "saves" / "slot1.sav").write_bytes(b"save-data")

    info.files = {
        "bin.exe": "h_bin_old",
        "assets/data.pak": "h_pak",
        "old_readme.txt": "h_readme",
    }
    info.sizes = {"bin.exe": 100, "assets/data.pak": 50, "old_readme.txt": 3}
    info.content.content_hash = "old_hash"
    info.content.revision = 1
    game_mod.save(info)
    return info


def _manifest_for(info) -> dict:
    return {
        "id": info.id,
        "name": info.name,
        "platform": info.platform,
        "steam_app_id": None,
        "description": "",
        "launch_exe": "",
        "launch_args": "",
        "runner": "",
        "added_by": info.added_by,
        "added_at": info.added_at,
        "content": {
            "revision": 2,
            "version_label": "1.1",
            "note": "Patch",
            "created_by": info.content.created_by,
            "created_at": info.content.created_at,
            "updated_by": "bob",
            "updated_at": "2026-09-26T12:00:00+00:00",
            "content_hash": "new_hash",
            "ignore": ["saves/**"],
        },
        "history": [
            {
                "revision": 1,
                "version_label": "",
                "note": "Erstveröffentlichung",
                "by": info.added_by,
                "at": info.added_at,
                "content_hash": "old_hash",
            },
            {
                "revision": 2,
                "version_label": "1.1",
                "note": "Patch",
                "by": "bob",
                "at": "2026-09-26T12:00:00+00:00",
                "content_hash": "new_hash",
            },
        ],
        # bin.exe shrinks (same content up to 60 bytes); data.pak moves to a new
        # name (same hash → moved, not re-downloaded); old_readme.txt is gone.
        "files": {"bin.exe": "h_bin_new", "assets/data2.pak": "h_pak"},
        "sizes": {"bin.exe": 60, "assets/data2.pak": 50},
        "info_hash": "",
    }


def test_start_update_preps_files_sets_updating_and_hides_from_incomplete(
    tmp_path, make_game, tm_factory
):
    cfg, tm = tm_factory()
    info = _setup_game(tmp_path, make_game)
    manifest = _manifest_for(info)

    library = Library()
    library.add(info)
    tracker = ContentTracker(cfg, library)
    app_state.init(cfg, library, transfer=tm, content=tracker)

    fake_ti = _FakeTorrentInfo([f"{info.path.name}/x"])
    fake_lt = _FakeLt(fake_ti)
    import deckdrop.network.transfer as transfer_mod

    monkeypatch_lt = MagicMock(return_value=fake_lt)
    orig_lt = transfer_mod._lt
    transfer_mod._lt = monkeypatch_lt
    try:
        peers = [_Peer("peer1", "Bob", "192.168.1.5")]
        download_id = tm.start_update(info, peers, b"torrent-bytes", manifest)
    finally:
        transfer_mod._lt = orig_lt

    # Moved (same hash, new path) and truncated (new size < old size).
    assert not (info.path / "assets" / "data.pak").exists()
    assert (info.path / "assets" / "data2.pak").is_file()
    assert (info.path / "bin.exe").stat().st_size == 60
    # old_readme.txt is only deleted at finalize time, not during prep.
    assert (info.path / "old_readme.txt").exists()

    assert tracker.state(info.id) == "updating"
    pending = tracker.get_entry(info.id)["pending_update"]
    assert pending["download_id"] == download_id
    assert pending["deletes_after"] == ["old_readme.txt"]

    rec = tm._paused[download_id]
    assert rec.kind == "update"
    assert rec.local_game_path == str(info.path)
    assert rec.target_version_label == "1.1"

    # The update target is the game's own live folder – it must never
    # disappear from Meine Spiele while the update is in progress.
    assert info.path.resolve() not in tm.incomplete_download_dest_paths()


@pytest.mark.asyncio
async def test_finalize_update_deletes_removed_keeps_id_and_local_files(
    tmp_path, make_game, tm_factory, monkeypatch
):
    cfg, tm = tm_factory()
    info = _setup_game(tmp_path, make_game)
    manifest = _manifest_for(info)

    library = Library()
    library.add(info)
    tracker = ContentTracker(cfg, library)
    app_state.init(cfg, library, transfer=tm, content=tracker)

    fake_ti = _FakeTorrentInfo([f"{info.path.name}/x"])
    fake_lt = _FakeLt(fake_ti)
    import deckdrop.network.transfer as transfer_mod

    orig_lt = transfer_mod._lt
    transfer_mod._lt = MagicMock(return_value=fake_lt)
    monkeypatch.setattr(
        torrent_mod, "make_magnet", lambda data: ("magnet:?xt=urn:btih:deadbeef", "deadbeef")
    )
    try:
        peers = [_Peer("peer1", "Bob", "192.168.1.5")]
        download_id = tm.start_update(info, peers, b"torrent-bytes", manifest)
    finally:
        transfer_mod._lt = orig_lt

    h = tm._handles[download_id]
    rec = tm._paused[download_id]
    tm._library = library

    await tm._finalize_update(h, rec)

    reloaded = game_mod.load_from_path(info.path)
    assert reloaded is not None
    assert reloaded.id == info.id  # never a fresh id for an update
    assert reloaded.content.revision == 2
    assert reloaded.content.content_hash == "new_hash"
    assert reloaded.files == {"bin.exe": "h_bin_new", "assets/data2.pak": "h_pak"}

    # Removed file gone, ignored/local file (not in the manifest) untouched.
    assert not (info.path / "old_readme.txt").exists()
    assert (info.path / "saves" / "slot1.sav").read_bytes() == b"save-data"

    assert tracker.state(info.id) == "clean"
    assert tracker.get_entry(info.id).get("pending_update") is None

    cache_path = Path(cfg.torrent_cache) / f"{info.id}.torrent"
    assert cache_path.read_bytes() == b"torrent-bytes"
    assert reloaded.torrent.info_hash == "deadbeef"


def test_remove_download_never_deletes_update_folder_and_sets_modified(
    tmp_path, make_game, tm_factory
):
    cfg, tm = tm_factory()
    info = _setup_game(tmp_path, make_game)

    library = Library()
    library.add(info)
    tracker = ContentTracker(cfg, library)
    app_state.init(cfg, library, transfer=tm, content=tracker)

    from deckdrop.network.transfer import _Handle, _PersistedRecord

    rec = _PersistedRecord(
        download_id="u1",
        game_id=info.id,
        game_name=info.name,
        peer_id="peer1",
        peer_name="Bob",
        magnet="",
        dest_path=str(info.path),
        started_at=0.0,
        kind="update",
        local_game_path=str(info.path),
    )
    tm._paused["u1"] = rec
    tm._handles["u1"] = _Handle(
        download_id="u1",
        game_id=info.id,
        game_name=info.name,
        peer_id="peer1",
        peer_name="Bob",
        handle=MagicMock(),
        dest_path=info.path,
    )

    assert tm.remove_download("u1", delete_files=True) is True

    assert info.path.is_dir()
    assert (info.path / "bin.exe").exists()
    assert tracker.state(info.id) == "modified"
