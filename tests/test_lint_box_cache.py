"""#602: lint measures each annotation's optimal bounding box at most once.

An optimal ``bounding_box()`` on fused annotation geometry costs ~10 ms, and the
structural checks (page bounds, pairwise labels, centreline pairs, view overlap)
each used to measure independently — the centreline pair check even re-measured a
label-less dim once per (dim, centreline) pair. All full-box lookups now go
through one identity-checked memo (``_ann_box``), persisted on the Drawing as
``_ann_box_cache`` beside the #143 view-edge cache, so a re-lint (every
``export()``, every repair-loop iteration) re-measures nothing that hasn't been
replaced.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.linting.structural import _ann_box, lint_drawing


class _FakeAnn:
    """Duck-typed annotation with a countable full-bbox and no label_bbox."""

    label = "fake"

    def __init__(self):
        self.calls = 0

    def bounding_box(self):
        self.calls += 1
        return SimpleNamespace(
            min=SimpleNamespace(X=0.0, Y=0.0), max=SimpleNamespace(X=10.0, Y=5.0)
        )


def test_ann_box_memoises_and_identity_checks():
    cache: dict = {}
    a = _FakeAnn()
    assert _ann_box(a, cache) == (0.0, 0.0, 10.0, 5.0)
    assert _ann_box(a, cache) == (0.0, 0.0, 10.0, 5.0)
    assert a.calls == 1
    # A replacement object is re-measured even if the cache is stale.
    b = _FakeAnn()
    cache[id(b)] = (a, None, (9.9, 9.9, 9.9, 9.9))  # simulated id-collision entry
    assert _ann_box(b, cache) == (0.0, 0.0, 10.0, 5.0)
    assert b.calls == 1


def test_ann_box_remeasures_after_in_place_relocation():
    # Shape.locate() transforms in place, and Drawing.items exposes live shapes —
    # identity alone can't see the move, so the entry also carries a location
    # token and a relocated object re-measures instead of serving its old box.
    cache: dict = {}
    ann = _FakeAnn()
    ann.location = (0.0, 0.0, 0.0)
    _ann_box(ann, cache)
    _ann_box(ann, cache)
    assert ann.calls == 1
    ann.location = (25.0, 0.0, 0.0)  # simulate .locate() on the live object
    _ann_box(ann, cache)
    assert ann.calls == 2


def test_lint_drawing_measures_once_across_calls():
    ann = _FakeAnn()
    cache: dict = {}
    for _ in range(3):
        lint_drawing([ann], page_bbox=(0, 0, 100, 100), ann_box_cache=cache)
    assert ann.calls == 1, "persistent ann_box_cache must survive repeated lints"


def test_lint_prunes_cache_entries_for_replaced_items():
    # A replaced annotation (the repair loop swaps in a fresh object) must not
    # stay strongly referenced by the persistent cache: lint() prunes entries
    # whose object is no longer on the sheet, so the cache is bounded by live
    # membership and released geometry can be collected.
    dwg = build_drawing(Box(60, 40, 20) - Pos(10, 5, 0) * Cylinder(4, 20))
    dwg.lint()
    view_ids = {id(vis) for vis, _ in dwg.views.values()}
    ann_keys = [k for k in dwg._ann_box_cache if k not in view_ids]
    assert ann_keys, "lint cached no annotation boxes — the guard lost its subject"
    departed = ann_keys[0]
    dwg.items = [i for i in dwg.items if id(i) != departed]
    dwg.lint()
    assert departed not in dwg._ann_box_cache
    assert set(dwg._ann_box_cache) <= {id(i) for i in dwg.items} | view_ids


def test_relint_recomputes_no_annotation_boxes():
    # Integration: on a real drawing the second lint() must not re-measure any
    # annotation/view geometry — every full-box lookup hits the persisted cache.
    import build123d.topology.shape_core as shape_core

    dwg = build_drawing(Box(60, 40, 20) - Pos(10, 5, 0) * Cylinder(4, 20))
    first = dwg.lint()

    calls = 0
    real = shape_core.Shape.bounding_box

    def counting(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        return real(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(shape_core.Shape, "bounding_box", counting)
        second = dwg.lint()

    # The one allowed call is lint_location_coverage's part.bounding_box() — a single
    # cheap query on the 3D solid, not annotation-geometry re-measurement.
    assert calls <= 1, f"re-lint recomputed {calls} bounding boxes despite the cache"
    assert [(i.severity, i.code, i.message) for i in second] == [
        (i.severity, i.code, i.message) for i in first
    ]
