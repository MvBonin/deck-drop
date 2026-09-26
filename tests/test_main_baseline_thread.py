"""Correction B: startup baseline hashing must not block the lifespan.

`_baseline_all_then_scan` is the function main.py's lifespan now runs in a
background daemon thread instead of a synchronous loop; this exercises it in
isolation (no real uvicorn/lifespan needed).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from deckdrop.main import _baseline_all_then_scan


def test_baseline_all_then_scan_baselines_every_game_then_scans():
    game_a = MagicMock(id="a1")
    game_b = MagicMock(id="b2")
    library = MagicMock()
    library.all.return_value = [game_a, game_b]
    tracker = MagicMock()

    _baseline_all_then_scan(library, tracker)

    tracker.ensure_baseline.assert_any_call("a1")
    tracker.ensure_baseline.assert_any_call("b2")
    assert tracker.ensure_baseline.call_count == 2
    tracker.scan_all_async.assert_called_once()


def test_lifespan_hands_baseline_to_a_background_thread():
    """Regression guard: `_run`'s lifespan must not call ensure_baseline itself.

    Reading the source is the only way to check this without executing
    `_run` (which ends in a blocking `uvicorn.run`); it fails loudly if a
    future edit reintroduces a synchronous baseline loop in the lifespan.
    """
    import inspect

    import deckdrop.main as main_mod

    source = inspect.getsource(main_mod._run)
    lifespan_source = source[source.index("async def lifespan") :]
    assert "threading.Thread" in lifespan_source
    assert "target=_baseline_all_then_scan" in lifespan_source
    assert "for g in library.all():\n            content_tracker.ensure_baseline" not in source
