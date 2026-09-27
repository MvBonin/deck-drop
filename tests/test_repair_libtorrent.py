""" "Reparieren" with real libtorrent: a copy of the *same* version with a
corrupted byte and a too-long file is rechecked against the host's torrent;
only the broken piece is downloaded and the folder ends up byte-identical.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import time
from dataclasses import dataclass

import pytest


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@dataclass
class _Peer:
    peer_id: str
    name: str
    address: str
    port: int


@pytest.mark.slow
def test_repair_rechecks_and_fetches_only_broken_piece(tmp_path, monkeypatch):
    lt = pytest.importorskip("libtorrent")

    from deckdrop.api import state as app_state
    from deckdrop.core import config as cfg_mod
    from deckdrop.core import content, debuglog, integrity
    from deckdrop.core import game as game_mod
    from deckdrop.core.content_tracker import ContentTracker
    from deckdrop.core.library import Library
    from deckdrop.core.torrent import create_torrent_data, lan_session, retarget_root
    from deckdrop.network.transfer import TransferManager

    mib = 1024 * 1024
    salt = os.urandom(32)  # unique info hash per run (no LSD cross-talk)
    files = {
        "a.pak": salt + b"A" * (4 * mib - len(salt)),
        "b.pak": salt + b"B" * (2 * mib - len(salt)),
    }

    def _cfg(name, root):
        monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / f"{name}_config.toml")
        cfg = cfg_mod.load()
        cfg._data["paths"]["download_dir"] = str(root)
        cfg._data["paths"]["torrent_cache"] = str(tmp_path / f"{name}_torrents")
        cfg._data["paths"]["resume_dir"] = str(tmp_path / f"{name}_resume")
        cfg._data["network"]["torrent_port"] = _free_port()
        cfg_mod.save(cfg)
        return cfg

    host_game = tmp_path / "host" / "Dawnwalker"
    host_game.mkdir(parents=True)
    for rel, data in files.items():
        (host_game / rel).write_bytes(data)
    host_cfg = _cfg("host", host_game.parent)
    host_info = game_mod.create_new(host_game, "Dawnwalker", added_by="PC")
    rels = content.iter_content_files(host_game, [])
    host_info.files = {r: integrity.hash_file(host_game / r) for r in rels}
    host_info.sizes = {r: (host_game / r).stat().st_size for r in rels}
    host_info.content.content_hash = content.compute_content_hash(host_info.files, host_info.sizes)
    host_info.content.version_label = "1.0.5"
    game_mod.save(host_info)
    manifest = game_mod.manifest_dict(host_info)
    torrent = create_torrent_data(host_game, files=sorted(host_info.files))

    host_session = lan_session(host_cfg.torrent_port)
    try:
        ti = lt.torrent_info(lt.bdecode(torrent))
        retarget_root(lt, ti, host_game.name)
        params = lt.add_torrent_params()
        params.ti = ti
        params.save_path = str(host_game.parent)
        params.flags |= lt.torrent_flags.seed_mode
        host_session.add_torrent(params)

        # Receiver: same version on disk, but a flipped byte in a.pak (2nd
        # piece) and b.pak with garbage appended – "startet nicht".
        recv_game = tmp_path / "recv" / "Dawnwalker"
        recv_game.mkdir(parents=True)
        broken = bytearray(files["a.pak"])
        broken[mib + 123] ^= 0xFF
        (recv_game / "a.pak").write_bytes(bytes(broken))
        (recv_game / "b.pak").write_bytes(files["b.pak"] + b"junk" * 100)
        recv_cfg = _cfg("recv", recv_game.parent)
        recv_info = game_mod.load_from_path(host_game)  # same id/manifest
        recv_info.path = recv_game
        game_mod.save(recv_info)
        recv_info = game_mod.load_from_path(recv_game)

        library = Library()
        library.add(recv_info)
        tracker = ContentTracker(recv_cfg, library)
        tm = TransferManager(recv_cfg)
        tm.set_library(library)
        app_state.init(recv_cfg, library, transfer=tm, content=tracker)
        debuglog.clear()

        peers = [_Peer("pc", "PC", "127.0.0.1", host_cfg.torrent_port)]
        did = tm.start_update(recv_info, peers, torrent, manifest, background=True, repair=True)
        prep = tm._update_preps[did]
        deadline = time.monotonic() + 15
        while not prep.done and time.monotonic() < deadline:
            time.sleep(0.05)
        tm._finish_update_preps()
        h = tm._handles[did]
        h.handle.connect_peer(("127.0.0.1", host_cfg.torrent_port))

        # Not `progress`: while libtorrent rechecks, that is the check progress.
        deadline = time.monotonic() + 30
        status = h.handle.status()
        while time.monotonic() < deadline and not (status.is_finished or status.is_seeding):
            time.sleep(0.2)
            status = h.handle.status()
        assert status.is_finished or status.is_seeding, f"repair did not finish ({status.state})"

        assert asyncio.run(tm._finalize_update(h, tm._paused[did])) is True
        for rel, data in files.items():
            got = (recv_game / rel).read_bytes()
            assert hashlib.sha256(got).digest() == hashlib.sha256(data).digest(), rel
        assert tracker.state(recv_info.id) == "clean"

        # Only the broken piece came over the wire.
        fetched = (
            status.total_payload_download - status.total_redundant_bytes - status.total_failed_bytes
        )
        assert 0 < fetched <= mib
        assert any(e["reason"] == "repair" for e in debuglog.events(recv_info.id))
    finally:
        del host_session
