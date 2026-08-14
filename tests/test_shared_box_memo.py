"""Placement and lint share ONE annotation bounding-box memo (#1138).

An *optimal* ``bounding_box()`` tessellates (~16 ms for a Leader), so an annotation both
paths measure is worth measuring once: placement boxes occupants while solving a strip,
and lint boxes them again for its overlap checks.

Be precise about the scope, because the obvious reading of "placement and lint share a
memo" is too generous. Measured on the fixture below, the memo holds **two** entries: the
placed leader (via ``corridor_blockers``) and the callout ``_probe_box`` measured to size
it. It holds no *dimension* and structurally cannot — ``strip_obstacles`` decomposes
anything exposing ``.segments`` rather than boxing it whole, and ``corridor_blockers``
skips ``Dimension``/``SafeDimension``. So this guards a real but small win, and asserting
on the leader specifically is what keeps it from passing on an unrelated entry.

The guard is deliberately NOT "the cache attribute exists" — an unused dict passes that.
It asserts the observable consequence: an annotation placement already measured is never
handed to OCC again by lint. Reverting ``corridor_blockers`` to a bare ``_geom_box(o)``
fails the first assertion; keeping the memo but giving lint a *separate* one fails the
second.
"""

from __future__ import annotations

import pytest
from build123d import Box, Cylinder, Pos
from build123d.topology import Shape
from build123d_drafting.helpers import Leader

from draftwright.analysis import _analyse
from draftwright.builder import _assemble


def _plate():
    """Holes on one face and a notch: enough to place a callout leader, which is the
    annotation kind the memo actually holds."""
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
    """Placement must leave measured boxes behind, not throw them away.

    Asserted on the placed *leader* rather than on "some entry": the probe's callout box
    is also in the memo, so a laxer assertion would still pass with the placement-side
    seeding removed entirely.
    """
    leaders = {id(o): n for n, o in built.iter_annotations() if isinstance(o, Leader)}
    assert leaders, "the fixture must place a callout leader for this test to mean anything"

    cached = [n for k, n in leaders.items() if k in built.box_cache]
    assert cached, (
        "placement boxed every strip occupant and cached none of the leaders it placed: "
        f"{len(leaders)} leaders placed, box_cache holds {len(built.box_cache)} entries"
    )


def test_lint_does_not_re_measure_what_placement_measured(built):
    """The consequence that makes the shared memo worth having."""
    live_annotations = {id(item) for item in built.items}
    seeded = {key for key in built.box_cache if key in live_annotations}
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
