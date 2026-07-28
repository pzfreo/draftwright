"""Balloon-ring standoff after hole-table escalation (#901).

When a plan view is too dense to dimension every hole inline, the engine escalates
(#93): the individual callouts and location dims are removed and replaced by a hole
table plus a tagged balloon per hole. That much is by design. What is *not* designed
is where the resulting ring sits — its standoff from the view is whatever falls out
of residual strip occupancy, and it is not bounded below.

Measured standoffs above the plan-view top edge: 19.0 mm here, 14 mm on NIST CTC-04,
6.5 mm on CTC-02 — the last is visually on top of the view it annotates.

This is the FAST reproduction of the same defect #893 hits through CTC-02: ~15 s and
no STEP import, against ~4 min for the fixture. It landed xfail while #901 was open
and became a real pass when #903 fixed it — so it now guards the fix rather than
tracking the bug.
"""

from __future__ import annotations

from build123d import Box, Cylinder, Pos

from draftwright import build_drawing

#: Minimum extent from the plan edge to the balloon ring's outer edge. Below this
#: the glyphs crowd the geometry; the CTC-02 e2e check uses the same figure.
_MIN_STANDOFF_MM = 20.0


def _dense_plate(n: int = 18):
    """A plate whose holes each need their own callout and location dims.

    **Distinct diameters are load-bearing.** Identical holes collapse into one grouped
    ``n×`` pattern callout, which is cheap and never congests the strip — a plate with
    twenty *identical* holes shows nothing at all (first ladder rung 24 mm, no
    balloons, no drops). Per-hole callouts plus per-hole location dims are what fill
    the plan-above strip and trip the escalation.
    """
    part = Box(200, 140, 14)
    for i in range(n):
        x = -90 + i * (180 / (n - 1))
        y = -60 + (i * 37) % 115
        part -= Pos(x, y, 0) * Cylinder(2.0 + 0.35 * i, 40)
    return part


def test_balloon_ring_clears_the_plan_view_after_escalation():
    drawing = build_drawing(_dense_plate())
    plan_top = drawing.view_bounds("plan")[3]
    tops = [
        obj.bounding_box().max.Y
        for name, obj in drawing.iter_annotations()
        if name.startswith("balloon_plan")
    ]
    assert tops, (
        "the dense plate must escalate to balloons — otherwise this is not the case under test"
    )
    assert max(tops) > plan_top + _MIN_STANDOFF_MM


def test_the_dense_plate_actually_escalates():
    """Guard on the fixture itself, so the standoff test above cannot pass for the
    wrong reason. If a future change stops this part escalating, that test would
    'pass' by having no balloons at all — this fails loudly instead."""
    drawing = build_drawing(_dense_plate())
    names = set(drawing.annotations())
    assert any(n.startswith("balloon_plan") for n in names), "expected balloons"
    assert any("table" in n for n in names), "expected the hole table"


def test_identical_holes_do_not_escalate():
    """The contrast case that explains the fixture's distinct diameters: a regular
    pattern collapses to one grouped callout and never congests the strip."""
    part = Box(200, 140, 14)
    for i in range(18):
        x = -90 + i * (180 / 17)
        part -= Pos(x, 0, 0) * Cylinder(3.0, 40)
    drawing = build_drawing(part)
    assert not [n for n in drawing.annotations() if n.startswith("balloon_plan")]
