"""Renderer-seam tests (ADR 0008) — the first real consumer of the planner output.

Validates that the IR/planner contract carries what a renderer needs to build a
hole callout: the bore/counterbore values, blind-vs-through, and the pattern count
all reconstruct from the plan via `hole_callout_spec`. (Placement of the production
callouts is covered by the hole-callout tests in `test_make_drawing`; the test-only
`render_callouts` seam was retired with `render_into` in #251.)
"""

import math

from build123d import Box, Cylinder, Pos

from draftwright.annotations.from_model import hole_callout_spec
from draftwright.model import build_part_model, plan_dimensions


def _groups(part):
    return plan_dimensions(build_part_model(part))


def _hole_or_pattern_spec(part):
    g = next(g for g in _groups(part) if g.feature_kind in ("hole", "pattern"))
    return hole_callout_spec(g)


class TestCalloutSpec:
    def test_simple_through_hole(self):
        part = Box(60, 60, 12) - Pos(0, 0, 0) * Cylinder(4, 30)
        s = _hole_or_pattern_spec(part)
        assert s["diameter"] == 8.0 and s["through"] is True
        assert s["count"] is None and s["cbore_dia"] is None

    def test_blind_hole_carries_depth(self):
        part = Box(60, 60, 20) - Pos(0, 0, 6) * Cylinder(4, 16)  # blind ø8
        s = _hole_or_pattern_spec(part)
        assert s["diameter"] == 8.0 and s["through"] is False and s["depth"] is not None

    def test_counterbored_hole(self):
        part = Box(60, 60, 16) - Pos(0, 0, 0) * Cylinder(4, 30) - Pos(0, 0, 4) * Cylinder(8, 12)
        s = _hole_or_pattern_spec(part)
        assert s["diameter"] == 8.0
        assert s["cbore_dia"] == 16.0 and s["cbore_depth"] is not None

    def test_bolt_circle_is_one_counted_callout_with_bc_suffix(self):
        part = Cylinder(40, 8)
        for i in range(6):
            a = i * math.pi / 3
            part -= Pos(25 * math.cos(a), 25 * math.sin(a), 0) * Cylinder(3, 20)
        s = _hole_or_pattern_spec(part)
        assert s["diameter"] == 6.0 and s["count"] == 6  # 6× ø6, not six callouts
        assert s["suffix"] is not None and "BC" in s["suffix"]  # BCD in the suffix

    def test_spotface_maps_to_the_step(self):
        # The renderer must not drop a spotface (review): step = cbore or spotface.
        from draftwright.model import Frame, HoleFeature, PartModel

        hole = HoleFeature(
            Frame((0, 0, 0), "z"), 6.0, depth=None, through=True, spotface=(14.0, 1.0)
        )
        g = plan_dimensions(PartModel(bbox=None, orientation=None, features=[hole]))[0]
        s = hole_callout_spec(g)
        assert s["cbore_dia"] == 14.0 and s["cbore_depth"] == 1.0


class TestBoreHalfSpan:
    """#360: a PMI bore-size dim's half-span from the bore centroid. A diameter
    record stores the diameter (half = radius); a radius record stores the radius
    (half = value). The bug keyed on the feature `.kind` (always 'pmi') instead of
    `.pmi_kind`, so the diameter branch was dead and every diameter dim spanned
    ±diameter — 2× too wide."""

    def test_diameter_halves_to_the_radius(self):
        from draftwright.annotations.from_model import _bore_half_span

        assert _bore_half_span("diameter", 35.0) == 17.5

    def test_radius_is_used_as_is(self):
        from draftwright.annotations.from_model import _bore_half_span

        assert _bore_half_span("radius", 8.0) == 8.0

    def test_the_pmifeature_kind_attr_never_triggers_the_diameter_branch(self):
        # The regression itself: a PmiFeature's `.kind` is always "pmi" (a ClassVar),
        # so keying on it (as the old code did) never halves. Pin that pmi_kind is
        # the right key and .kind is the wrong one.
        from draftwright.annotations.from_model import _bore_half_span
        from draftwright.model import Frame, PmiFeature

        rec = PmiFeature(
            frame=Frame((0, 0, 0), "z"),
            pmi_kind="diameter",
            value=35.0,
            label="ø35",
            dominant_axis="Z",
        )
        assert rec.kind == "pmi"  # feature kind (ClassVar), NOT the PMI category
        assert _bore_half_span(rec.kind, rec.value) == 35.0  # the old bug: no halving
        assert _bore_half_span(rec.pmi_kind, rec.value) == 17.5  # the fix: radius
