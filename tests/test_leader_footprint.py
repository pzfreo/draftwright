"""The analytic leader footprint must CONTAIN the geometry it stands in for (#1138).

`_probe_box` used to build a real ``Leader`` -- fusing an Arrow, a swept shelf rectangle
and the callout sketch -- purely to read its bounding box, at 0.12-0.21 s per probe. It is
now arithmetic, mirroring ``helpers.Leader.__init__`` the way ``dim_footprint`` mirrors
``Dimension``.

The safety contract differs from ``dim_footprint``'s, and that difference is the whole
point of this file. A dimension candidate accepted off its footprint is REBUILT and
re-validated, so a mismatch there costs a wasted probe. A leader probe box is not
re-validated: it sets the x-band deciding which obstacles the carve considers. An analytic
box that is too SMALL therefore drops a real obstacle silently -- the invisible-occupant
defect (#133/#225/#305) the probe exists to prevent. Too big is harmless.

So the assertion is containment, not equality, and it is checked against real built
geometry rather than against a golden number, which would only restate the arithmetic.
The reference geometry is helpers' ``Leader`` itself rather than draftwright's thin
construction wrapper, because helpers' rendering IS the thing the footprint must mirror.
"""

from __future__ import annotations

import pytest
from build123d import Box, Cylinder, Pos
from build123d_drafting.helpers import Draft, HoleCallout, Leader

from draftwright.analysis import _analyse
from draftwright.annotations._common import _geom_box, leader_footprint
from draftwright.builder import _assemble

# The footprint reads its padding off ``Draft``, and ``Drawing.draft`` is public, live state
# — so the styles are an axis of the contract, not a constant. ``inverted`` is the one that
# matters: while the arrowhead is the widest thing on the shaft, padding by the arrow alone
# happens to contain the stroke, but a heavy line with a small arrow inverts that and a
# diagonal shaft escapes an arrow-only box by ~1.4 mm.
DRAFT_STYLES = {
    "default": Draft(),
    "inverted": Draft(line_width=4, arrow_length=1),
    "equal": Draft(line_width=2, arrow_length=2),
    "hairline": Draft(line_width=0.1, arrow_length=6),
}


@pytest.fixture(scope="module", params=sorted(DRAFT_STYLES), ids=sorted(DRAFT_STYLES))
def draft(request):
    return DRAFT_STYLES[request.param]


@pytest.fixture(scope="module")
def callout(draft):
    return HoleCallout(diameter=8.0, draft=draft)


@pytest.fixture(scope="module")
def callout_box(callout):
    return _geom_box(callout)


def _build(tip, elbow, side, draft, callout):
    return Leader(
        tip=(tip[0], tip[1], 0),
        elbow=(elbow[0], elbow[1], 0),
        label="",
        draft=draft,
        text_side=side,
        callout=callout,
    )


# Both shelf directions, tips above/below/level with the elbow, short and long shafts:
# the shaft's diagonal is what makes a leader's box position-dependent.
CASES = [
    (side, dx, dy)
    for side in ("right", "left")
    for dy in (-30.0, -8.0, 0.0, 8.0, 30.0)
    for dx in (5.0, 20.0, 60.0)
]


@pytest.mark.parametrize("side,dx,dy", CASES)
def test_analytic_footprint_contains_the_built_leader(side, dx, dy, draft, callout, callout_box):
    sign = 1.0 if side == "right" else -1.0
    tip, elbow = (0.0, 0.0), (sign * dx, dy)

    leader = _build(tip, elbow, side, draft, callout)
    real = _geom_box(leader)
    analytic = leader_footprint(tip, elbow, draft, text_side=side, callout_box=callout_box)

    assert analytic is not None, "helpers built this leader, so the footprint must describe it"
    assert real is not None

    # Containment, with a hair of float tolerance: the callout half is measured rather than
    # estimated, so the two boxes coincide exactly on whichever side the callout dominates.
    tol = 1e-6
    assert analytic[0] <= real[0] + tol, f"under-claims left by {analytic[0] - real[0]:.4f} mm"
    assert analytic[1] <= real[1] + tol, f"under-claims bottom by {analytic[1] - real[1]:.4f} mm"
    assert analytic[2] >= real[2] - tol, f"under-claims right by {real[2] - analytic[2]:.4f} mm"
    assert analytic[3] >= real[3] - tol, f"under-claims top by {real[3] - analytic[3]:.4f} mm"


def test_it_declines_exactly_where_helpers_refuses_to_build(draft, callout, callout_box):
    """A forced side that runs the shaft through the label makes ``Leader`` raise. The
    analytic path must decline it too, or a candidate helpers cannot draw would be probed
    as though it were placeable."""
    # side forced "right" while the elbow sits LEFT of the tip: the shelf and label run back
    # across the shaft.
    tip, elbow = (0.0, 0.0), (-25.0, 0.0)

    with pytest.raises(ValueError):
        _build(tip, elbow, "right", draft, callout)

    assert leader_footprint(tip, elbow, draft, text_side="right", callout_box=callout_box) is None


@pytest.mark.parametrize("dx", (25.0, -25.0))
@pytest.mark.parametrize("dy", (-12.0, 0.0, 12.0))
def test_auto_side_picks_the_shelf_direction_helpers_picks(dx, dy, draft, callout, callout_box):
    """``text_side="auto"`` is the footprint's default and the case a forced side cannot
    stand in for: the shelf direction is *derived* from where the elbow sits rather than
    given. Deriving it the other way would put the label on the wrong side of the elbow and
    silently mis-state the footprint by the label's whole width.
    """
    tip, elbow = (0.0, 0.0), (dx, dy)

    real = _geom_box(_build(tip, elbow, "auto", draft, callout))
    analytic = leader_footprint(tip, elbow, draft, callout_box=callout_box)

    assert analytic is not None
    tol = 1e-6
    assert analytic[0] <= real[0] + tol, f"under-claims left by {analytic[0] - real[0]:.4f} mm"
    assert analytic[1] <= real[1] + tol, f"under-claims bottom by {analytic[1] - real[1]:.4f} mm"
    assert analytic[2] >= real[2] - tol, f"under-claims right by {real[2] - analytic[2]:.4f} mm"
    assert analytic[3] >= real[3] - tol, f"under-claims top by {real[3] - analytic[3]:.4f} mm"


def _plate():
    part = Box(120, 80, 12)
    for x in (-40, 0, 40):
        part -= Pos(x, 20, 0) * Cylinder(4, 40)
    part -= Pos(-30, -20, 0) * Box(40, 12, 40)
    return part


def test_a_build_constructs_no_leader_it_throws_away(monkeypatch):
    """The reason the change exists, stated behaviourally.

    A probe that builds its subject shows up as a Leader constructed and never placed. This
    counts constructions across a whole build rather than reaching into the probe helper, so
    it holds through renames and needs no white-box import (the #641 gap-2 ratchet).
    """
    constructed = []
    original = Leader.__init__

    def counting(self, *args, **kwargs):
        constructed.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Leader, "__init__", counting)

    a = _analyse(
        _plate(), title="", number="", tolerance="ISO 2768-m", drawn_by="", out="lf", pmi="off"
    )
    dwg = _assemble(a, "lf", None, None, auto_dims=True)

    placed = [o for _n, o in dwg.iter_annotations() if isinstance(o, Leader)]
    assert placed, "the fixture must place at least one callout leader for this to mean anything"
    assert len(constructed) == len(placed), (
        f"{len(constructed)} Leaders built but only {len(placed)} placed — "
        f"{len(constructed) - len(placed)} were constructed just to be measured"
    )
