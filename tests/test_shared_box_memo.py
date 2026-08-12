"""Placement and lint share ONE annotation bounding-box memo (#1138).

An *optimal* ``bounding_box()`` tessellates, so boxing a placed annotation costs ~10 ms
for a Dimension and ~16 ms for a Leader. Placement boxes every occupant while solving a
strip; lint then boxes the same objects for its overlap checks. Measuring twice was ~1/3
of every labelling pass's bounding-box time.

The guard below is deliberately NOT "the cache attribute exists" — an unused dict passes
that. It asserts the observable consequence: an annotation placement already measured is
never handed to OCC again by lint. Reverting either seeding call site (``strip_obstacles``
or ``corridor_blockers`` back to a bare ``_geom_box(o)``) empties the memo and fails the
first assertion; keeping the memo but giving lint a *separate* one fails the second.
"""

from __future__ import annotations

import pytest
from build123d import Box, Cylinder, Pos
from build123d.topology import Shape

from draftwright.analysis import _analyse
from draftwright.builder import _assemble


def _plate():
    """Enough distinct annotations — hole callouts, envelope dims, a notch — that the
    memo has real occupants rather than a single dimension's worth."""
    part = Box(120, 80, 12)
    for x in (-40, 0, 40):
        part -= Pos(x, 20, 0) * Cylinder(4, 40)
    part -= Pos(-30, -20, 0) * Box(40, 12, 40)
    return part


@pytest.fixture(scope="module")
def built():
    part = _plate()
    a = _analyse(
        part, title="", number="", tolerance="ISO 2768-m", drawn_by="", out="memo", pmi="off"
    )
    return _assemble(a, "memo", None, None, auto_dims=True)


def test_placement_seeds_the_memo(built):
    """Placement must leave measured boxes behind, not throw them away."""
    placed = {name: o for name, o in built.iter_annotations()}
    cached = {k for k in built.box_cache if any(id(o) == k for o in placed.values())}

    assert cached, (
        "placement measured every strip occupant and cached none of them: "
        f"{len(placed)} annotations placed, box_cache holds {len(built.box_cache)} entries"
    )


def test_lint_does_not_re_measure_what_placement_measured(built):
    """The consequence that makes the shared memo worth having."""
    seeded = {k for k in built.box_cache}
    assert seeded, "nothing was seeded, so this test cannot prove anything"

    boxed_during_lint: list[int] = []
    original = Shape.bounding_box

    def recording(self, *args, **kwargs):
        boxed_during_lint.append(id(self))
        return original(self, *args, **kwargs)

    Shape.bounding_box = recording
    try:
        built.lint()
    finally:
        Shape.bounding_box = original

    re_measured = seeded & set(boxed_during_lint)
    assert not re_measured, (
        f"lint re-measured {len(re_measured)} of the {len(seeded)} annotations placement had "
        "already boxed — the two paths are not sharing one memo"
    )


def test_the_memo_is_optional(built):
    """The annotations layer duck-types ``dwg``; a stand-in without ``box_cache`` must
    still measure rather than crash, or every test double becomes a Drawing."""
    from draftwright.annotations._common import _geom_box

    _name, obj = next(iter(built.iter_annotations()))
    assert _geom_box(obj, None) == _geom_box(obj)
