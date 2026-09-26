"""Publishing must stick even if the library reloads while it runs.

`Library.reload()` (every GET /api/games – the UI polls every 3 s while a game
is publishing) replaces all GameInfo objects with fresh loads from disk.
`torrent_prep.invalidate_torrent()` then saved such a *stale* object (loaded
before the publish wrote the new manifest) and wrote the old files/sizes
back – the next scan found the patched file "modified" again, and the card
went straight back to "Verändert / Update veröffentlichen…".
"""

from __future__ import annotations

from deckdrop.api import state as app_state
from deckdrop.core import game as game_mod
from deckdrop.core import torrent_prep
from deckdrop.core.content_tracker import ContentTracker
from deckdrop.core.library import Library


def test_publish_survives_library_reload(isolated_config, make_game, monkeypatch):
    cfg = isolated_config
    cfg.download_dir.mkdir(parents=True, exist_ok=True)
    info = make_game(cfg.download_dir, "The Blood of Dawnwalker")
    (info.path / "bin.exe").write_bytes(b"x" * 100)
    (info.path / "patch.pak").write_bytes(b"old" * 10)

    library = Library()
    library.reload(cfg)
    tracker = ContentTracker(cfg, library)
    app_state.init(cfg, library, content=tracker)
    tracker.ensure_baseline(info.id)
    g = library.get(info.id)
    g.torrent.magnet = "magnet:?xt=urn:btih:" + "a" * 40
    g.torrent.info_hash = "a" * 40
    game_mod.save(g)
    library.reload(cfg)
    assert tracker.state(info.id) == "clean"

    # The 1.0.5 patch changes one file (other size).
    (info.path / "patch.pak").write_bytes(b"new!" * 20)
    assert tracker.scan(info.id) == "modified"

    # The UI polls /api/games while publishing → library reloads mid-publish.
    from deckdrop.core import integrity

    real_hash = integrity.hash_file

    def hash_and_reload(path, progress=None):
        library.reload(cfg)
        return real_hash(path)

    monkeypatch.setattr("deckdrop.core.content_tracker.integrity.hash_file", hash_and_reload)
    prepared = []
    monkeypatch.setattr(torrent_prep, "schedule_prepare", lambda gid, **kw: prepared.append(gid))

    tracker._publish_worker(info.id, "1.0.5", "", [])
    monkeypatch.setattr("deckdrop.core.content_tracker.integrity.hash_file", real_hash)

    on_disk = game_mod.load_from_path(info.path)
    assert on_disk.content.version_label == "1.0.5"
    assert on_disk.sizes["patch.pak"] == 80  # new manifest, not reverted
    assert on_disk.torrent.magnet == ""  # torrent invalidated for the rebuild
    assert prepared == [info.id]
    assert library.get(info.id).sizes["patch.pak"] == 80  # library not stale either

    # Next periodic scan (and the one after a UI reload) keeps it clean.
    assert tracker.scan(info.id) == "clean"
    library.reload(cfg)
    assert tracker.scan(info.id) == "clean"
