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
    """#360: an imported bore-size dim's half-span from the bore centroid. A diameter
    record stores the diameter (half = radius); a radius record stores the radius
    (half = value). The bug keyed on the IR feature `.kind` instead of the drafting
    dimension category, so the diameter branch was dead and every diameter dim spanned
    ±diameter — 2× too wide."""

    def test_diameter_halves_to_the_radius(self):
        from draftwright.annotations.from_model import _bore_half_span

        assert _bore_half_span("diameter", 35.0) == 17.5

    def test_radius_is_used_as_is(self):
        from draftwright.annotations.from_model import _bore_half_span

        assert _bore_half_span("radius", 8.0) == 8.0

    def test_the_ir_kind_attr_never_triggers_the_diameter_branch(self):
        # The regression itself: an authored dimension's `.kind` identifies the IR concept,
        # so keying on it (as the old code did) never halves. Pin that pmi_kind is the
        # compatibility category and .kind is the wrong one.
        from draftwright.annotations.from_model import _bore_half_span
        from draftwright.model import AuthoredDimension, Frame

        rec = AuthoredDimension(
            frame=Frame((0, 0, 0), "z"),
            dimension_kind="diameter",
            value=35.0,
            label="ø35",
            dominant_axis="Z",
        )
        assert rec.kind == "authored_dimension"  # IR concept, NOT the dimension category
        assert _bore_half_span(rec.kind, rec.value) == 35.0  # the old bug: no halving
        assert _bore_half_span(rec.pmi_kind, rec.value) == 17.5  # the fix: radius

    def test_raw_unsupported_pmi_is_not_renderable_as_a_dimension(self):
        from draftwright.annotations.from_model import _renderable_pmi_records
        from draftwright.model import AuthoredDimension, Frame, PmiFeature

        dim = AuthoredDimension(
            frame=Frame((0, 0, 0), "x"),
            dimension_kind="linear",
            value=10.0,
            label="10",
            dominant_axis="X",
            ref_pts=((0, 0, 0), (10, 0, 0)),
        )
        raw_gtol = PmiFeature(
            frame=Frame((0, 0, 0), "x"),
            pmi_kind="position",
            value=0.1,
            label="position 0.1",
            dominant_axis="X",
            ref_pts=((0, 0, 0), (10, 0, 0)),
        )

        assert _renderable_pmi_records([dim, raw_gtol]) == [dim]


class TestStepChainDrop:
    """#362: dropping a whole turned step-length chain (shoulders too crowded to
    dimension) must record the `step_dim_dropped` lint warning — it was silent
    (debug log only), leaving the user a drawing with no step dims and no signal.
    Must NOT emit an Escalation(kind='step') (that is the prismatic-detail consumer,
    wrong semantics for a turned chain)."""

    def _stub_dwg(self):
        from types import SimpleNamespace

        class _Dwg:
            def __init__(s):
                s.draft = SimpleNamespace(font_size=3.0, pad_around_text=0.5)
                s.issues = []
                # NOTE: deliberately no _escalations — if the code tried to append a
                # step Escalation it would AttributeError, so a clean run proves it doesn't.

            def view_bounds(s, view):
                return (0.0, 0.0, 100.0, 100.0)

            def _record_build_issue(s, sev, code, msg):
                s.issues.append((sev, code, msg))

        return _Dwg()

    def test_crowded_vertical_chain_records_the_drop(self):
        from draftwright.annotations._common import PlacementContext
        from draftwright.annotations.from_model import _draw_step_chain
        from draftwright.registry import AnnotationRegistry

        dwg = self._stub_dwg()
        # The drop lint routes through the ctx's registry now (#639), not the drawing.
        ctx = PlacementContext(registry=AnnotationRegistry())
        # Vertical (chain-to-the-right) segs whose shoulder Ys are 0.1 mm apart —
        # far below tier_step (= font_size + 2*pad = 4). Values differ so the
        # uniform-collapse path is not taken. The chain is dropped whole.
        segs = [
            ((80.0, 10.0, 0), (80.0, 10.1, 0), 5.0),
            ((80.0, 10.1, 0), (80.0, 10.2, 0), 8.0),
        ]
        placed = _draw_step_chain(dwg, "front", segs, "m_steplen", ctx=ctx)
        assert placed == 0
        codes = [i.code for i in ctx.registry.issues]
        assert "step_dim_dropped" in codes, "silent drop no longer allowed (#362)"
        assert ctx.escalations == []  # _record_step_chain_drop records lint only, no Escalation


class TestDiameterColumnOccupancy:
    """#358: the left ø-diameter column's occupancy is the FULL footprint
    (`strip_obstacles`), not the label-box-only `_occupied_boxes` that was blind to a
    bore callout's leader SHAFT. A ø label overprinting a shaft is now dropped."""

    def _dwg(self, occupants):
        from types import SimpleNamespace

        from build123d_drafting.helpers import Draft

        class _Occ:  # a fake placed annotation exposing only a full bounding_box
            def __init__(s, box):
                s._box = box

            def bounding_box(s):
                x0, y0, x1, y1 = s._box
                return SimpleNamespace(
                    min=SimpleNamespace(X=x0, Y=y0), max=SimpleNamespace(X=x1, Y=y1)
                )

        class _Dwg:
            draft = Draft(font_size=3.0)

            def view_bounds(s, v):
                return (40.0, 0.0, 80.0, 40.0)  # ample room to the left of fx0=40

            def at(s, v, x, y, z):
                return (x, z, 0.0)  # identity-ish projection: tip Y follows the axial z

            def iter_annotations(s):
                return [(f"hc_front{i}", _Occ(b)) for i, b in enumerate(occupants)]

            def view_of(s, n):
                return "front"

            def add(s, ann, name, view, feature=None):
                pass

        return _Dwg()

    # (anchor, dia, feature, tolerance) — feature=None (unit test of placement; #412 added the
    # tag); tolerance=None (untoleranced — the item grew a P2a ± field, #28)
    _ITEMS = [
        ((10.0, 0.0, 8.0), 12.0, None, None),
        ((10.0, 0.0, 24.0), 8.0, None, None),
    ]  # two Z-turned ø steps

    def test_control_no_occupant_places_both(self):
        from draftwright.annotations.from_model import _diameter_column_left

        # No occupant → both labels placed (proves the column has room; the drop below
        # is due to occupancy, not the room guard).
        assert _diameter_column_left(self._dwg([]), self._ITEMS) == 2

    def test_bore_shaft_footprint_drops_the_overprinting_labels(self):
        from draftwright.annotations.from_model import _diameter_column_left

        # A bore leader whose FULL footprint blankets the left column. The old
        # label-box-only `_occupied_boxes` never recorded this shaft (the occupant has
        # no label_bbox), so both ø labels would have been placed straight over it;
        # strip_obstacles sees the full box, so both are dropped.
        assert _diameter_column_left(self._dwg([(-100.0, -100.0, 100.0, 100.0)]), self._ITEMS) == 0
