"""Phase 5 "5.4": real libtorrent proof that an update only transfers the
pieces that actually changed (piece-level delta), not the whole game again.

Two real libtorrent sessions on 127.0.0.1 (different ports): a seeding "host"
and a receiver that already has revision 1 on disk and runs the actual
`TransferManager.start_update` / `_finalize_update` to pick up revision 2.

Slow (real I/O, real network loop) and skipped entirely without libtorrent.
"""

from __future__ import annotations

import hashlib
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


def _blake2b(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


@dataclass
class _Peer:
    peer_id: str
    name: str
    address: str
    port: int


@pytest.mark.slow
@pytest.mark.parametrize("background", [False, True], ids=["inline", "background"])
def test_update_transfers_only_changed_pieces(tmp_path, monkeypatch, background):
    lt = pytest.importorskip("libtorrent")

    from deckdrop.api import state as app_state
    from deckdrop.core import config as cfg_mod
    from deckdrop.core import content, integrity
    from deckdrop.core import game as game_mod
    from deckdrop.core.content_tracker import ContentTracker
    from deckdrop.core.library import Library
    from deckdrop.core.torrent import create_torrent_data
    from deckdrop.network.transfer import TransferManager

    # -- Host: revision 1 on disk --
    host_root = tmp_path / "host"
    host_game = host_root / "Big Game"
    host_game.mkdir(parents=True)
    unchanged = b"A" * (6 * 1024 * 1024)
    changed_v1 = b"B" * (3 * 1024 * 1024)
    (host_game / "unchanged.bin").write_bytes(unchanged)
    (host_game / "changed.bin").write_bytes(changed_v1)
    (host_game / "removed.bin").write_bytes(b"C" * (1024 * 1024))

    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "host_config.toml")
    host_cfg = cfg_mod.load()
    host_cfg._data["paths"]["download_dir"] = str(host_root)
    host_cfg._data["paths"]["torrent_cache"] = str(tmp_path / "host_torrents")
    host_cfg._data["paths"]["resume_dir"] = str(tmp_path / "host_resume")
    host_cfg._data["network"]["torrent_port"] = _free_port()
    host_cfg.user_name = "Alice"
    cfg_mod.save(host_cfg)

    host_info = game_mod.create_new(host_game, "Big Game", added_by="Alice")

    def _hash_all(g):
        rels = content.iter_content_files(g.path, g.content.ignore)
        files, sizes = {}, {}
        for rel in rels:
            p = g.path / rel
            files[rel] = integrity.hash_file(p)
            sizes[rel] = p.stat().st_size
        g.files, g.sizes = files, sizes
        g.size_bytes = sum(sizes.values())
        g.content.content_hash = content.compute_content_hash(files, sizes)

    _hash_all(host_info)
    game_mod.save(host_info)

    torrent_v1 = create_torrent_data(host_game, files=sorted(host_info.files))
    host_session = None
    try:
        from deckdrop.core.torrent import lan_session

        host_session = lan_session(host_cfg.torrent_port)

        def _seed(session, path, torrent_bytes):
            ti = lt.torrent_info(lt.bdecode(torrent_bytes))
            from deckdrop.core.torrent import retarget_root

            retarget_root(lt, ti, path.name)
            params = lt.add_torrent_params()
            params.ti = ti
            params.save_path = str(path.parent)
            params.flags |= lt.torrent_flags.seed_mode
            params.flags |= lt.torrent_flags.auto_managed
            return session.add_torrent(params)

        host_handle = _seed(host_session, host_game, torrent_v1)

        # -- Receiver: already has revision 1 on disk under the same game id --
        recv_root = tmp_path / "receiver"
        recv_game = recv_root / "Big Game"
        recv_game.mkdir(parents=True)
        for p in host_game.iterdir():
            if p.name == game_mod.TOML_FILENAME:
                continue
            (recv_game / p.name).write_bytes(p.read_bytes())

        monkeypatch.setattr(cfg_mod, "CONFIG_PATH", tmp_path / "recv_config.toml")
        recv_cfg = cfg_mod.load()
        recv_cfg._data["paths"]["download_dir"] = str(recv_root)
        recv_cfg._data["paths"]["torrent_cache"] = str(tmp_path / "recv_torrents")
        recv_cfg._data["paths"]["resume_dir"] = str(tmp_path / "recv_resume")
        recv_cfg._data["network"]["torrent_port"] = _free_port()
        recv_cfg.user_name = "Bob"
        cfg_mod.save(recv_cfg)

        recv_info = game_mod.GameInfo(
            id=host_info.id,
            name=host_info.name,
            version=1,
            added_at=host_info.added_at,
            added_by=host_info.added_by,
            updated_at=host_info.updated_at,
            updated_by=host_info.updated_by,
            size_bytes=host_info.size_bytes,
            platform="any",
            path=recv_game,
            files=dict(host_info.files),
            sizes=dict(host_info.sizes),
            content=game_mod.ContentInfo(
                revision=1,
                created_by="Alice",
                created_at=host_info.added_at,
                updated_by="Alice",
                updated_at=host_info.added_at,
                content_hash=host_info.content.content_hash,
            ),
        )
        game_mod.save(recv_info)

        recv_library = Library()
        recv_library.add(recv_info)
        recv_tracker = ContentTracker(recv_cfg, recv_library)
        recv_tracker.ensure_baseline(recv_info.id)

        recv_tm = TransferManager(recv_cfg)
        recv_tm.set_library(recv_library)
        app_state.init(recv_cfg, recv_library, transfer=recv_tm, content=recv_tracker)

        # -- Host publishes revision 2: one file changed in the middle (same
        # size), one added, one removed. --
        changed_v2 = bytearray(changed_v1)
        mid = 1024 * 1024 + 10  # inside the 2nd of 3 pieces (1 MiB pieces)
        changed_v2[mid : mid + 256] = b"Z" * 256
        (host_game / "changed.bin").write_bytes(bytes(changed_v2))
        (host_game / "new.bin").write_bytes(b"D" * (512 * 1024))
        (host_game / "removed.bin").unlink()

        _hash_all(host_info)
        host_info.content.revision = 2
        host_info.content.version_label = "1.1"
        host_info.content.updated_by = "Alice"
        host_info.history.append(
            game_mod.HistoryEntry(
                revision=2,
                version_label="1.1",
                note="Patch",
                by="Alice",
                at=host_info.updated_at,
                content_hash=host_info.content.content_hash,
            )
        )
        game_mod.save(host_info)
        manifest_v2 = game_mod.manifest_dict(host_info)
        total_size_v2 = sum(host_info.sizes.values())

        torrent_v2 = create_torrent_data(host_game, files=sorted(host_info.files))
        try:
            host_session.remove_torrent(host_handle)
        except Exception:
            pass
        host_handle = _seed(host_session, host_game, torrent_v2)

        # -- Receiver applies the update via the real TransferManager path --
        peers = [_Peer("host", "Alice", "127.0.0.1", host_cfg.torrent_port)]
        download_id = recv_tm.start_update(
            recv_info, peers, torrent_v2, manifest_v2, background=background
        )
        if background:
            # What the poll loop does: wait for the prep thread, then add.
            prep = recv_tm._update_preps[download_id]
            deadline = time.monotonic() + 15.0
            while not prep.done and time.monotonic() < deadline:
                time.sleep(0.05)
            assert prep.done and prep.error is None
            recv_tm._finish_update_preps()

        h = recv_tm._handles[download_id]
        rec = recv_tm._paused[download_id]
        # start_update's connect_peer uses the receiver's own configured
        # torrent_port (same-LAN-port assumption, see transfer.py) – on one
        # machine the two sessions use different ports, so connect explicitly.
        h.handle.connect_peer(("127.0.0.1", host_cfg.torrent_port))

        deadline = time.monotonic() + 30.0
        status = h.handle.status()
        while time.monotonic() < deadline and status.progress < 1.0:
            time.sleep(0.2)
            status = h.handle.status()
        assert status.progress >= 1.0, f"Update did not finish in time (progress={status.progress})"

        import asyncio

        asyncio.run(recv_tm._finalize_update(h, rec))

        # -- Verify: folder matches host exactly, id kept, deletions applied --
        for name in ("unchanged.bin", "changed.bin", "new.bin"):
            host_bytes = (host_game / name).read_bytes()
            recv_bytes = (recv_game / name).read_bytes()
            assert _blake2b(recv_bytes) == _blake2b(host_bytes), f"{name} differs after update"
        assert not (recv_game / "removed.bin").exists()

        reloaded = game_mod.load_from_path(recv_game)
        assert reloaded.id == host_info.id
        assert reloaded.content.revision == 2
        assert reloaded.content.content_hash == host_info.content.content_hash

        # -- Delta proof: far less than the whole new game size was downloaded --
        downloaded = status.total_payload_download
        assert downloaded > 0
        assert downloaded < total_size_v2 * 0.5, (
            f"Update looks like a full re-download: {downloaded} bytes of {total_size_v2} total"
        )
    finally:
        if host_session is not None:
            del host_session
