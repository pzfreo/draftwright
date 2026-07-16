"""Tests for draftwright.make_drawing."""

import logging
import math
import os
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from _kernel import B123D_GE_011, SKIP_011
from build123d import Box, Compound, Cylinder, Edge, Pos, Rotation, export_step
from build123d_drafting import HoleCallout, Leader, ViewCoordinates, view_axes

from draftwright import Drawing, build_drawing, make_drawing
from draftwright._core import _MIN_VIEW_MM, _fmt
from draftwright.analysis import (
    _converge_step_sizing,
    _is_rotational,
    dedup_diams,
)
from draftwright.compose import StripDepths, _fits, choose_scale
from draftwright.drawing import analyse_cylinders
from draftwright.export import _export_shape
from draftwright.make_drawing import generate_script, lint_feature_coverage
from draftwright.recognition import (
    Slot,
    recognise_face_levels,
    recognise_pockets,
    recognise_slots,
)

_skip_011 = pytest.mark.skipif(B123D_GE_011, reason=SKIP_011)


def _state_snapshot(dwg):
    """The mutable state a read-only test must not touch — annotation count,
    names, pins, and the per-view (visible, hidden) tuples (by identity)."""
    return (
        len(dwg.items),
        frozenset(dwg._named),
        frozenset(dwg._pinned),
        {k: (id(vis), id(hid)) for k, (vis, hid) in dwg.views.items()},
    )


@pytest.fixture(scope="module")
def dwg_box_60_40_20():
    """A built ``Box(60, 40, 20)`` drawing, built once and shared by the
    **read-only** tests in this module (#153 — the hot part is otherwise rebuilt
    dozens of times). A teardown guard asserts the drawing was not mutated, so a
    consumer that accidentally adds/removes/pins an annotation or swaps a view
    fails loudly here instead of silently contaminating its neighbours."""
    dwg = build_drawing(Box(60, 40, 20))
    before = _state_snapshot(dwg)
    yield dwg
    assert _state_snapshot(dwg) == before, (
        "a shared-fixture consumer mutated dwg_box_60_40_20 — give that test its "
        "own build_drawing(Box(60, 40, 20)) (see #153)"
    )


# ---------------------------------------------------------------------------
# Pure-function unit tests (fast, no OCP projection)
# ---------------------------------------------------------------------------


class TestFmt:
    def test_integer_value(self):
        assert _fmt(36.0) == "36"

    def test_fractional_value(self):
        assert _fmt(14.7) == "14.7"

    def test_zero(self):
        assert _fmt(0.0) == "0"

    def test_step_float_noise(self):
        # STEP-imported bounding boxes carry fp noise; near-integers must label cleanly
        assert _fmt(800.0000000000001) == "800"
        assert _fmt(-5.9999999999999) == "-6"


class TestDedupDiams:
    def test_empty(self):
        assert dedup_diams([]) == []

    def test_single(self):
        assert dedup_diams([{"diameter": 10.0, "area": 1}]) == [10.0]

    def test_deduplicates_close_values(self):
        cyls = [{"diameter": 10.0, "area": 1}, {"diameter": 10.05, "area": 1}]
        result = dedup_diams(cyls)
        assert len(result) == 1

    def test_keeps_distinct_values(self):
        cyls = [{"diameter": 10.0, "area": 1}, {"diameter": 20.0, "area": 1}]
        result = dedup_diams(cyls)
        assert len(result) == 2

    def test_sorted_descending(self):
        cyls = [
            {"diameter": 5.0, "area": 1},
            {"diameter": 20.0, "area": 1},
            {"diameter": 10.0, "area": 1},
        ]
        result = dedup_diams(cyls)
        assert result == [20.0, 10.0, 5.0]


def _plate_labels(d):
    return sorted(str(o.label) for o in d._named.values() if getattr(o, "label", None))


def _l_bracket():
    # A multi-plate L-prismatic (#559): base plate 10 thick (Z) + upright wall 10 thick
    # (Y), each drilled. The regression fixture the issue asks for.
    part = Pos(0, 0, 5) * Box(80, 60, 10) + Pos(0, 25, 35) * Box(80, 10, 50)
    for cx in (-24, 24):
        part -= Pos(cx, -15, 5) * Cylinder(5, 12)  # base holes (Z)
    for cx in (-22, 22):
        part -= Pos(cx, 25, 38) * Rotation(90, 0, 0) * Cylinder(4, 14)  # wall holes (Y)
    return part


class TestPlateThickness:
    """#559: plate/wall thicknesses on a multi-plate prismatic are dimensioned via a
    recognised `PlateFeature`, not left to the overall envelope."""

    def test_bracket_plate_thicknesses_dimensioned(self):
        # The issue's acceptance test. Both plates are 10 thick; on `main` neither
        # thickness was dimensioned. The `15` is the base-hole row Y-location — confirmed
        # ground-truth-valid (base plate -Y edge -30 → hole row -15), so it STAYS (its
        # placement legibility is tracked separately as #564); the original comment's
        # `"15" not in lbl` was stale against the issue body and is corrected here.
        dwg = build_drawing(_l_bracket(), number="X")
        lbl = _plate_labels(dwg)
        assert lbl.count("10") == 2  # BOTH plate thicknesses (base Z + wall Y) — were ABSENT
        assert "15" in lbl  # valid base-hole location dim — unchanged (see #564)
        # thickness dims come from recognised prismatic feature intent, not a view heuristic
        plate_dims = {n: dwg._named[n].label for n in dwg._named if n.startswith("dim_plate")}
        assert sorted(plate_dims.values()) == ["10", "10"]
        # base thickness in the front elevation, wall thickness in the side (end) view —
        # different characteristic views so the two legs read as distinct features (#559).
        assert {dwg.view_of(n) for n in plate_dims} == {"front", "side"}
        assert dwg.view_of("dim_plate_z0") == "front"  # base plate (Z)
        assert dwg.view_of("dim_plate_y0") == "side"  # wall (Y)
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_single_flat_plate_has_no_plate_thickness_dim(self):
        # A single plate's thickness IS the overall height (dim_height) — the plate
        # recogniser must not add a duplicate.
        dwg = build_drawing(Box(80, 60, 10), number="X")
        assert not [n for n in dwg._named if n.startswith("dim_plate")]
        assert _plate_labels(dwg).count("10") == 1  # only dim_height

    def test_channel_gap_is_not_read_as_a_plate(self):
        # A U-channel has two upright walls with AIR between them (facing +Y/-Y inward) —
        # the opposite face arrangement from a plate. The recogniser must not emit a
        # thickness across the gap.
        part = (
            Box(80, 60, 10) + Pos(0, -25, 30) * Box(80, 10, 40) + Pos(0, 25, 30) * Box(80, 10, 40)
        )
        dwg = build_drawing(part, number="X")
        plate_vals = sorted(dwg._named[n].label for n in dwg._named if n.startswith("dim_plate"))
        # each 10-thick wall is a plate (Y), but the 40 mm air gap between them is NOT
        assert "40" not in plate_vals
        assert "50" not in plate_vals

    def test_rotational_part_has_no_plate_dims(self):
        # A turned/rotational part's extents are the OD / length chain, not plate
        # thicknesses — plate detection is gated off for it.
        part = Cylinder(20, 8)  # a thin disc: thin in Z, but rotational
        dwg = build_drawing(part, number="X")
        assert not [n for n in dwg._named if n.startswith("dim_plate")]


class TestStepPosition:
    """#555: a prismatic step/rebate's along-axis POSITION is dimensioned, not just its
    two heights, so the part is fully constrained."""

    def test_step_position_dimensioned(self):
        # The issue's acceptance test: an asymmetric step so the position can't hide
        # behind another value. shelf 20 deep at the front, back 40 deep, lowered by 15.
        part = Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15)
        dwg = build_drawing(part, number="X")
        lbl = _plate_labels(dwg)
        assert {"80", "60", "30", "15"} <= set(lbl)  # overall + heights already present
        assert "20" in lbl or "40" in lbl  # step position / shelf depth — was ABSENT
        # from recognised step intent, in the side (profile) view where the step reads
        pos = {n: dwg._named[n].label for n in dwg._named if n.startswith("dim_shoulder")}
        assert list(pos.values()) == ["20"]
        assert all(dwg.view_of(n) == "side" for n in pos)
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_finalize_places_step_positions_without_other_corridor_work(self):
        # #636 regression: render_step_positions now only REGISTERS corridor candidates,
        # so finalize() must drain them even when there are no locations/slots/user-dims
        # to otherwise trigger the shared drain. A stepped part with no holes is exactly
        # that gap — before the fix the shoulder-position dims queued and vanished.
        part = Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15)
        dwg = build_drawing(part, auto_dims=False)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        with dwg.deferred():
            dwg.dimension(step, "length", role="step_position")
        placed = sorted(dwg._named[n].label for n in dwg._named if n.startswith("dim_shoulder"))
        assert placed == ["20"]  # the shoulder position survives the recompose

    def test_finalize_drains_step_positions_after_a_mid_replay_raise(self, monkeypatch):
        # #636 review: A0b registers the step-position corridor candidates and drops their
        # intents BEFORE the fallible callout phase. If that phase raises and finalize() is
        # retried, the recomputed step_position_ids is empty — so the drain must key off the
        # pending corridor batch, not the intent set, or the candidates strand and vanish.
        from draftwright.annotations import holes as _holes_mod

        part = (
            Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15) - Pos(20, 20, 0) * Cylinder(4, 30)
        )
        dwg = build_drawing(part, auto_dims=False)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg._defer_intents = True
        dwg.dimension(step, "length", role="step_position")
        dwg.callout(hole)  # a fallible B1 phase between A0b registration and the B2 drain

        real = _holes_mod._annotate_holes
        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected callout failure")
            return real(*a, **k)

        monkeypatch.setattr(_holes_mod, "_annotate_holes", _boom)
        with pytest.raises(RuntimeError):
            dwg.finalize()  # A0b registered the step candidates; B1 raises → batch stranded
        assert dwg._corridor_batch  # candidates persist, not yet drained
        assert not [n for n in dwg._named if n.startswith("dim_shoulder")]

        dwg.finalize()  # retry: step_position_ids is now empty, but the batch still drains
        placed = sorted(dwg._named[n].label for n in dwg._named if n.startswith("dim_shoulder"))
        assert placed == ["20"]

    def test_finalize_retries_when_the_drain_itself_raised(self, monkeypatch):
        # #647: finalize is transactional. If drain_corridors raises (step-only, no other
        # intents), the rollback restores the pre-finalize state — so the step-position INTENT is
        # back on the drawing and a clean retry re-runs from it and drains.
        from draftwright.annotations import _common

        part = Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15)  # step only, no holes
        dwg = build_drawing(part, auto_dims=False)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        dwg._defer_intents = True
        dwg.dimension(step, "length", role="step_position")

        real = _common.drain_corridors
        calls = {"n": 0}

        def _boom(d):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected drain failure")
            return real(d)

        monkeypatch.setattr(_common, "drain_corridors", _boom)
        with pytest.raises(RuntimeError):
            dwg.finalize()  # A0b registered the candidates; the drain then raises
        # The rollback restored the step-position INTENT; a retry re-runs from it and drains.
        assert any(it.kwargs.get("role") == "step_position" for it in dwg._intents)
        dwg.finalize()  # retry: re-registers from the surviving intent and drains
        placed = sorted(dwg._named[n].label for n in dwg._named if n.startswith("dim_shoulder"))
        assert placed == ["20"]

    def test_finalize_rolls_back_a_partial_commit(self, monkeypatch):
        # #647: finalize is transactional. A drain that raises AFTER an earlier stage already
        # committed annotations must not leave them behind — else a retry re-runs the source
        # intents and duplicates the measurement (the m_locx0 + m_locx1 defect). The rollback
        # restores _named/items/_intents to the pre-finalize state, so a clean retry places each
        # dimension exactly once.
        from draftwright.annotations import _common

        # A step positioned in B2's drain, plus an off-centre hole whose callout live-places in
        # leg B1 — so an annotation is COMMITTED before the B2 drain raises, and must roll back.
        part = (
            Box(80, 60, 30)
            - Pos(0, -20, 7.5) * Box(80, 20, 15)
            - Pos(20, 15, 0) * Cylinder(3, 30)
        )
        dwg = build_drawing(part, auto_dims=False)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg._defer_intents = True
        dwg.callout(hole)  # commits in leg B1, before the B2 drain
        dwg.dimension(step, "length", role="step_position")  # drained in B2
        names_before, items_before = set(dwg._named), len(dwg.items)
        intents_before = len(dwg._intents)

        calls = {"n": 0}
        real = _common.drain_corridors

        def _boom(d):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected drain failure")
            return real(d)

        monkeypatch.setattr(_common, "drain_corridors", _boom)
        with pytest.raises(RuntimeError):
            dwg.finalize()
        # Rolled back to exactly the pre-finalize state — the B1 callout is gone again.
        assert set(dwg._named) == names_before
        assert len(dwg.items) == items_before
        assert len(dwg._intents) == intents_before

        monkeypatch.undo()
        dwg.finalize()  # clean retry — the shoulder position places exactly once (no duplicate)
        assert len([n for n in dwg._named if n.startswith("dim_shoulder")]) == 1

    def test_centered_rebate_dimensions_both_shoulders(self):
        # A symmetric central channel has TWO shoulders; both positions must be given
        # (20 and 40 from the front datum), else the channel is under-constrained.
        dwg = build_drawing(Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15), number="X")
        pos = sorted(dwg._named[n].label for n in dwg._named if n.startswith("dim_shoulder"))
        assert pos == ["20", "40"]
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_x_axis_step_positioned_in_plan(self):
        # A step whose shoulder runs along X is located above the plan view (the axis→view
        # mapping the hole-location ladder uses), not the side view.
        dwg = build_drawing(Box(80, 60, 30) - Pos(-25, 0, 7.5) * Box(30, 60, 15), number="X")
        pos = {n: dwg.view_of(n) for n in dwg._named if n.startswith("dim_shoulder")}
        assert pos and set(pos.values()) == {"plan"}

    def test_plain_block_has_no_step_position(self):
        # No step → no shoulder dim.
        dwg = build_drawing(Box(40, 30, 12), number="X")
        assert not [n for n in dwg._named if n.startswith("dim_shoulder")]

    def test_through_slot_is_not_a_step_shoulder(self):
        # A slot's walls are interior vertical faces but not step risers (no step level);
        # the slot recogniser dimensions them, so no spurious step-position dim appears.
        dwg = build_drawing(Box(50, 30, 20) - Box(20, 8, 30), number="X")
        assert not [n for n in dwg._named if n.startswith("dim_shoulder")]

    def test_raised_pad_is_not_a_step_shoulder(self):
        # #555 review: a raised rectangular pad/island rises from the base-top level, but
        # its walls do NOT span the part edge-to-edge — only a genuine step/rebate does. A
        # pad must not be mis-located as a shoulder.
        dwg = build_drawing(Box(80, 60, 10) + Pos(0, 0, 10) * Box(40, 40, 10), number="X")
        assert not [n for n in dwg._named if n.startswith("dim_shoulder")]

    def test_blind_pocket_is_not_a_step_shoulder(self):
        # #555 review: a blind pocket's floor IS a step level, but its walls are bounded
        # (not full-span), so it is not read as a step shoulder.
        dwg = build_drawing(Box(80, 60, 30) - Pos(0, 0, 5) * Box(30, 20, 20), number="X")
        assert not [n for n in dwg._named if n.startswith("dim_shoulder")]

    def test_step_position_round_trips_through_generated_script(self, tmp_path):
        # #555 review: the --script reconstruction (record→finalize) must keep the step
        # position, else a regenerated drawing is under-constrained again — the very bug.
        # A CENTERED rebate (two shoulders sharing role="step_position") is the case a
        # per-shoulder verb would crash on: one verb must rebuild both via finalize.
        from build123d import export_step

        from draftwright.make_drawing import generate_script

        part = Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)  # two shoulders: 20 and 40
        step = tmp_path / "stepped.step"
        export_step(part, str(step))
        script_path = generate_script(str(step), out=str(tmp_path / "gen"))
        src = Path(script_path).read_text()
        assert src.count('role="step_position"') == 1  # ONE verb rebuilds all shoulders
        ns = {"__file__": script_path}
        exec(src, ns)  # noqa: S102 — executing our own generated reconstruction (must not crash)
        labels = [str(o.label) for o in ns["dwg"]._named.values() if getattr(o, "label", None)]
        assert "20" in labels and "40" in labels  # both shoulder positions survive

    def test_step_position_round_trips_through_declared_model(self):
        # #555 review: a declared StepLevelFeature carrying shoulders renders the position
        # (the sheet-emit declarative path relies on this).
        from draftwright.model import StepLevelFeature
        from draftwright.model.ir import Frame

        part = Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15)
        step = StepLevelFeature(
            frame=Frame((0, 0, -15), "z"),
            base=-15,
            levels=(0.0,),
            shoulders=(("y", -10.0),),
            datum=(-40, -30, -15),
        )
        dwg = build_drawing(part, model=[step], number="X")
        labels = [str(o.label) for o in dwg._named.values() if getattr(o, "label", None)]
        assert "20" in labels


def _chamfer_text(dwg):
    return " ".join(
        str(getattr(o, "label", "") or getattr(o, "text", "") or "") for o in dwg._named.values()
    )


class TestChamferCallout:
    """#560: a chamfered edge is called out (C{leg} / {leg}×{angle}°) from a recognised
    ChamferFeature, not left as an undimensioned bevel."""

    def _chamfered_plate(self, *legs):
        from build123d import Axis, chamfer

        plate = Box(90, 60, 20)
        e = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
        return chamfer(e, *legs)

    def test_chamfer_called_out(self):
        # The issue's acceptance test: a 45° equal-leg 12 chamfer must carry "12".
        dwg = build_drawing(self._chamfered_plate(12), number="X")
        assert "12" in _chamfer_text(dwg)  # C12 — was ABSENT

    def test_equal_leg_45_uses_c_form_and_participates_in_lint(self):
        # The callout is a real placed leader (named, in a view) and the sheet lints clean.
        dwg = build_drawing(self._chamfered_plate(12), number="X")
        callouts = {n: dwg._named[n].label for n in dwg._named if n.startswith("m_chamfer")}
        assert list(callouts.values()) == ["C12"]
        assert all(dwg.view_of(n) == "plan" for n in callouts)  # Z-edge reads in the plan
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_recognised_through_ir_not_inferred(self):
        # Represented as a ChamferFeature carrying both legs + angle (so equal vs
        # asymmetric is recovered from geometry, not the rendered view).
        m = build_drawing(self._chamfered_plate(12), number="X").model()
        ch = next((f for f in m.features if f.kind == "chamfer"), None)
        assert ch is not None
        assert (
            abs(ch.leg1 - ch.leg2) < 0.05
            and abs(ch.angle - 45.0) < 0.5
            and abs(ch.leg1 - 12) < 0.05
        )

    def test_asymmetric_chamfer_distinguished(self):
        # Unequal legs → NOT a C-form callout. Pin the recovered magnitudes: leg1 the
        # larger (14), leg2 the smaller (8), angle = atan2(8, 14) ≈ 29.7° — the asymmetric
        # size is the whole point of carrying both legs in the IR (#560), so a mis-measured
        # magnitude must not slip through on callout-form alone.
        dwg = build_drawing(self._chamfered_plate(8, 14), number="X")
        ch = next(f for f in dwg.model().features if f.kind == "chamfer")
        assert abs(ch.leg1 - ch.leg2) >= 0.05
        assert abs(ch.leg1 - 14) < 0.05 and abs(ch.leg2 - 8) < 0.05
        assert abs(ch.angle - 29.74) < 0.5
        callout = next(dwg._named[n].label for n in dwg._named if n.startswith("m_chamfer"))
        assert "C" not in callout and "°" in callout

    def test_leader_anchors_on_the_bevel_interior_not_an_endpoint(self):
        # #621: the leader anchor must sit ON the chamfer bevel, near the middle of its run — not
        # at the supporting plane's parametric origin, which projects to an endpoint/corner.
        from build123d import Axis, GeomType, Vertex, chamfer

        from draftwright.recognition import recognise_chamfers

        part = chamfer(Box(60, 40, 30).edges().filter_by(Axis.Z).sort_by(Axis.X)[-1], 6)
        (ch,) = recognise_chamfers(part)
        bevel = next(
            f
            for f in part.faces()
            if f.geom_type == GeomType.PLANE
            and max(abs(c) for c in f.normal_at().to_tuple()) < 0.99
        )
        assert Vertex(*ch.at).distance_to(bevel) < 1e-6  # on the bevel face
        ei = "xyz".index(ch.axis)
        bb = bevel.bounding_box()
        lo, hi = ([bb.min.X, bb.min.Y, bb.min.Z][ei], [bb.max.X, bb.max.Y, bb.max.Z][ei])
        frac = (ch.at[ei] - lo) / (hi - lo)
        assert 0.3 < frac < 0.7  # interior, not an endpoint — the plane origin gave frac 0.0

    @pytest.mark.slow
    def test_ctc01_c50_chamfer_anchors_on_the_bevel_midpoint_not_the_corner(self):
        # #621's *in-plane* (visible) symptom only appears where OCC's plane parametric origin is
        # off-centre in the placement plane — which axis-aligned box chamfers never are (their
        # plane origin is already in-plane-centred). On the NIST CTC01 C50 chamfer the old plane
        # origin was the corner (400, 175); the fix anchors on the bevel centroid (375, 200), the
        # diagonal midpoint. render_chamfers projects the in-plane X, Y, so this is what the
        # rendered leader tip actually uses.
        from draftwright.analysis import _import_step
        from draftwright.recognition import recognise_chamfers

        fixture = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap242.stp"
        part = _import_step(str(fixture))
        c50 = next(c for c in recognise_chamfers(part) if abs(c.leg1 - 50) < 1)
        assert c50.axis == "z"  # runs along Z, so X/Y are the in-plane placement coords
        assert abs(c50.at[0] - 375) < 2 and abs(c50.at[1] - 200) < 2  # the bevel midpoint
        assert (
            abs(c50.at[0] - 400) > 10 or abs(c50.at[1] - 175) > 10
        )  # not the plane-origin corner

    def test_x_edge_chamfer_reads_in_side_view(self):
        from build123d import Axis, chamfer

        part = Box(60, 40, 30)
        e = part.edges().filter_by(Axis.X).sort_by(lambda e: e.center().Y + e.center().Z)[-1]
        dwg = build_drawing(chamfer(e, 6), number="X")
        callouts = {n: dwg.view_of(n) for n in dwg._named if n.startswith("m_chamfer")}
        assert callouts and set(callouts.values()) == {"side"}

    def test_plain_box_has_no_chamfer_callout(self):
        dwg = build_drawing(Box(40, 30, 12), number="X")
        assert not [n for n in dwg._named if n.startswith("m_chamfer")]

    def test_turned_part_has_no_chamfer_feature(self):
        # A turned part's chamfers are conical (not oblique planar) — none recognised.
        dwg = build_drawing(Cylinder(20, 10), number="X")
        assert not [f for f in dwg.model().features if f.kind == "chamfer"]

    def test_leg_is_measured_from_the_local_face_not_the_outermost(self):
        # #560 review: the leg must come from the chamfer face's own extent, not the
        # distance to the part's outermost wall. A 6 mm chamfer on the top box of a
        # stepped part must read C6, NOT C36 (base wall at x=45 vs top wall at x=25).
        from build123d import Axis, chamfer

        part = Box(90, 60, 10) + Pos(0, 0, 10) * Box(50, 40, 10)
        e = (
            part.edges()
            .filter_by(Axis.Z)
            .group_by(lambda e: e.center().Z)[-1]
            .sort_by(lambda e: e.center().X + e.center().Y)[-1]
        )
        from draftwright.annotations.from_model import _chamfer_label

        dwg = build_drawing(chamfer(e, 6), number="X")
        chamfers = [f for f in dwg.model().features if f.kind == "chamfer"]
        assert len(chamfers) == 1
        assert _chamfer_label(chamfers[0]) == "C6"

    def test_hex_prism_side_faces_are_not_chamfers(self):
        # #560 review: a polygon prism's oblique sides are REAL faces, not chamfers — they
        # abut oblique neighbours, not two perpendicular axis-aligned faces. None fire.
        from build123d import RegularPolygon, extrude

        dwg = build_drawing(extrude(RegularPolygon(20, 6), 30), number="X")
        assert not [f for f in dwg.model().features if f.kind == "chamfer"]

    def test_structural_ramp_is_not_a_chamfer(self):
        # #560 review: a large sloped face spanning the part (a wedge/ramp) is structural,
        # not an edge break — the size gate excludes it.
        from build123d import Axis, chamfer

        wedge = chamfer(
            Box(60, 40, 40).edges().filter_by(Axis.X).sort_by(lambda e: e.center().Z)[-1], 30
        )
        dwg = build_drawing(wedge, number="X")
        assert not [f for f in dwg.model().features if f.kind == "chamfer"]

    def test_corner_gusset_is_not_a_chamfer(self):
        # #560 review (BLOCKER): a structural triangular gusset/rib bracing a wall to a
        # floor bevels a CONCAVE re-entrant corner — its virtual corner is buried inside
        # the material, so the convex-edge test rejects it. A chamfer removes material from
        # a CONVEX edge (virtual corner in vacuum). Face-normal + adjacency alone can't tell
        # them apart; both abut two perpendicular walls.
        from build123d import Edge, Face, Vector, Wire, extrude

        base = Box(120, 80, 8)  # top z=4
        wall = Pos(-56, 0, 24) * Box(8, 80, 40)  # inner face x=-52
        # Right-triangle prism flush on the wall (x=-52) and floor (z=4), hypotenuse facing
        # out — the classic corner brace.
        pts = [Vector(-52, -35, 4), Vector(-40, -35, 4), Vector(-52, -35, 16)]
        tri = Face(Wire([Edge.make_line(pts[i], pts[(i + 1) % 3]) for i in range(3)]))
        dwg = build_drawing(base + wall + extrude(tri, amount=70), number="X")
        assert not [f for f in dwg.model().features if f.kind == "chamfer"]

    def test_single_axis_spanning_ramp_is_not_a_chamfer(self):
        # #560 review r3 (BLOCKER): a long shallow ramp that spans most of one axis but is
        # thin on the other is a structural wedge, not an edge break. The size gate rejects
        # any bevel whose larger leg exceeds a fraction of the part's largest dimension —
        # measured against the whole part, so it catches a single-axis ramp yet keeps a
        # small plate edge-break.
        from build123d import Axis, chamfer

        e = Box(100, 20, 30).edges().filter_by(Axis.Y).sort_by(lambda e: e.center().Z)[-1]
        dwg = build_drawing(chamfer(e, 12, 80), number="X")
        assert not [f for f in dwg.model().features if f.kind == "chamfer"]

    def test_thin_plate_edge_break_is_recognised(self):
        # #560 review (BLOCKER): a routine 2.5 mm edge break on 4 mm sheet was silently
        # dropped because one leg (into the thin thickness axis) exceeded half that small
        # extent. The wedge gate now excludes only a ramp large on BOTH in-plane axes, so a
        # plate edge chamfer survives.
        from build123d import chamfer

        from draftwright.annotations.from_model import _chamfer_label

        p = Box(80, 50, 4)
        e = p.edges().group_by(lambda e: e.center().Z)[-1].sort_by(lambda e: e.center().Y)[-1]
        dwg = build_drawing(chamfer(e, 2.5), number="X")
        chamfers = [f for f in dwg.model().features if f.kind == "chamfer"]
        assert len(chamfers) == 1
        assert _chamfer_label(chamfers[0]) == "C2.5"


class TestFlatCallout:
    """#148b: machined flats on round stock — the recogniser recovers the across-flats size
    from the geometry (flat-to-flat for opposed faces, the D height for a lone flat)."""

    @staticmethod
    def _hex_on_stock(d=9.3, r=10):
        # Six flats 60° apart, cut shallow (d near r) so OD arcs survive between them.
        from build123d import Rot

        bar = Cylinder(r, 30)
        for k in range(6):
            bar = bar - Rot(0, 0, 60 * k) * Pos(d + 1, 0, 0) * Box(2, 40, 40)
        return bar

    def test_hex_reads_across_flats(self):
        from draftwright.recognition import recognise_flats

        flats = recognise_flats(self._hex_on_stock(9.3, 10))
        assert len(flats) == 6
        # Every opposed pair reads flat-to-flat = 2d = 18.6, one shared A/F value.
        assert {round(f.across, 1) for f in flats} == {18.6}

    def test_odd_polygon_falls_back_to_D_height(self):
        # Three flats 120° apart have no opposing face → each reads flat-to-opposite-OD (R+d).
        from build123d import Rot

        from draftwright.recognition import recognise_flats

        bar = Cylinder(10, 30)
        for k in range(3):
            bar = bar - Rot(0, 0, 120 * k) * Pos(10.3, 0, 0) * Box(2, 40, 40)
        flats = recognise_flats(bar)
        assert len(flats) == 3 and {round(f.across, 1) for f in flats} == {19.3}

    def test_flat_on_x_axis_stock(self):
        from build123d import Rot

        from draftwright.recognition import recognise_flats

        xbar = Rot(0, 90, 0) * Cylinder(8, 30) - Pos(0, 0, 8) * Box(40, 40, 6)
        flats = recognise_flats(xbar)
        assert len(flats) == 1 and flats[0].axis == "x"
        assert flats[0].across == pytest.approx(13, abs=0.05)  # R + d = 8 + 5

    def test_shallow_tangent_sliver_is_not_a_flat(self):
        # A cut that barely grazes the OD (depth R − d below the min) is not a machined flat.
        from draftwright.recognition import recognise_flats

        grazed = Cylinder(10, 30) - Pos(10, 0, 0) * Box(0.4, 40, 40)  # depth ≈ 0.2 mm
        assert recognise_flats(grazed) == []

    def test_flat_renders_in_the_axis_view(self):
        from draftwright.annotations.from_model import _flat_label

        dwg = build_drawing(Cylinder(10, 30) - Pos(10, 0, 0) * Box(10, 40, 40), number="X")
        names = [n for n in dwg.annotations() if n.startswith("m_flat")]
        assert len(names) == 1
        assert dwg._named[names[0]].label == _flat_label(15)
        assert dwg._anno_view[names[0]] == "plan"  # a Z-axis bar reads down the axis (plan)

    def test_offcentre_recess_wall_is_not_a_flat(self):
        # A slot/recess offset to one side of the axis has a near wall whose outward normal
        # points *away* from the axis — the sign test alone would pass it. But that wall reaches
        # the OD on one end only (the other abuts the slot floor), so it is not a flat.
        from draftwright.recognition import recognise_flats

        recessed = Cylinder(30, 40) - Pos(20, 25, 0) * Box(10, 30, 50)
        assert recognise_flats(recessed) == []

    def test_lone_flats_on_two_parallel_shafts_are_not_paired(self):
        # Two distinct z-shafts, each with one flat facing opposite ways. They share the axis
        # *letter* but not the axis *line*, so neither is the other's opposite: each reads the
        # D height (R + d = 15), not a spurious flat-to-flat (2d = 10).
        from draftwright.recognition import recognise_flats

        left = Cylinder(10, 30) - Pos(10, 0, 0) * Box(10, 40, 40)
        right = Pos(50, 0, 0) * (Cylinder(10, 30) - Pos(-10, 0, 0) * Box(10, 40, 40))
        flats = recognise_flats(left + right)
        assert len(flats) == 2 and {round(f.across, 1) for f in flats} == {15.0}


class TestGrooveCallout:
    """#148c: turned / circlip grooves on round stock — the recogniser recovers the groove
    width + floor diameter from the OD band geometry (a strict local-minimum diameter), and
    the callout reads ``{width} WIDE × ø{diameter}``."""

    @staticmethod
    def _grooved(floor_r=8, width=4, r=10, length=40):
        # Round bar with one annular groove: the OD (r) is reduced to floor_r over `width`.
        return Cylinder(r, length) - (Cylinder(r, width) - Cylinder(floor_r, width))

    def test_single_groove_reads_width_and_diameter(self):
        from draftwright.recognition import recognise_grooves

        grooves = recognise_grooves(self._grooved(8, 4, 10))
        assert len(grooves) == 1
        assert grooves[0].width == pytest.approx(4, abs=0.05)
        assert grooves[0].diameter == pytest.approx(16, abs=0.05)

    def test_two_grooves_on_one_shaft(self):
        from draftwright.recognition import recognise_grooves

        shaft = Cylinder(10, 60)
        shaft -= Pos(0, 0, 15) * (Cylinder(10, 4) - Cylinder(8, 4))
        shaft -= Pos(0, 0, -15) * (Cylinder(10, 4) - Cylinder(7, 4))
        grooves = recognise_grooves(shaft)
        assert len(grooves) == 2
        assert {round(g.diameter, 1) for g in grooves} == {16.0, 14.0}

    def test_monotonic_step_is_not_a_groove(self):
        # A plain stepped shaft (OD changes once, not a local minimum) has no groove.
        from draftwright.recognition import recognise_grooves

        stepped = Cylinder(10, 20) + Pos(0, 0, 15) * Cylinder(6, 10)
        assert recognise_grooves(stepped) == []

    def test_plain_cylinder_has_no_groove(self):
        from draftwright.recognition import recognise_grooves

        assert recognise_grooves(Cylinder(10, 40)) == []

    def test_slot_on_round_stock_is_not_a_groove(self):
        # A milled slot's walls are rectangular / radial — not the annular walls of a groove.
        from draftwright.recognition import recognise_grooves

        assert recognise_grooves(Cylinder(10, 30) - Box(6, 40, 40)) == []

    def test_alternating_fine_steps_are_not_grooves(self):
        # An alternating fine-step head (⌀ dips to a local minimum but the band is as wide as
        # its neighbours) is a stepped profile, not a channel — a groove must be NARROWER than
        # both bounding walls (#148c review: else a staircase dip is misread as a groove).
        from build123d import Align, Rotation

        from draftwright.recognition import recognise_grooves

        b = Align.MIN
        shaft = None
        z = 0.0
        for d, ln in [(8, 3.1), (12, 2.9), (8, 3.2), (12, 2.8), (6, 3.0)]:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        assert recognise_grooves(Rotation(0, 90, 0) * shaft) == []

    def test_grooves_on_two_parallel_shafts_are_not_confused(self):
        # Two distinct z-shafts, each with one groove. Grouped by axis *line* (not letter),
        # so their bands are never interleaved into a phantom third groove.
        from draftwright.recognition import recognise_grooves

        a = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))
        b = Pos(40, 0, 0) * (Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(6, 4)))
        grooves = recognise_grooves(a + b)
        assert len(grooves) == 2
        assert {round(g.diameter, 1) for g in grooves} == {16.0, 12.0}

    def test_groove_renders_in_the_profile_view(self):
        from draftwright.annotations.from_model import _groove_label

        dwg = build_drawing(self._grooved(8, 4, 10), number="X")
        names = [n for n in dwg.annotations() if n.startswith("m_groove")]
        assert len(names) == 1
        assert dwg._named[names[0]].label == _groove_label(4, 16)
        # A groove's width is axial → it reads in a profile view (axis in-plane), not down it.
        assert dwg._anno_view[names[0]] == "front"
        assert not any(i.severity == "error" for i in dwg.lint())

    def test_groove_floor_diameter_is_not_double_dimensioned(self):
        # The groove floor band's two walls read as shoulders, so recognise_turned_steps
        # also delimits it as a middle step. detect.py must exclude that band from the step
        # chain so the floor ø is dimensioned ONCE (the groove callout), never also as a
        # separate step ø — ISO 129 / ADR 0008 one-band-one-owner (#148c review).
        dwg = build_drawing(self._grooved(8, 4, 10), number="X")
        floor_labels = [
            n for n in dwg.annotations() if "ø16" in str(getattr(dwg._named[n], "label", ""))
        ]
        assert floor_labels == [n for n in floor_labels if n.startswith("m_groove")]
        assert len(floor_labels) == 1

    def test_two_identical_grooves_each_get_their_own_callout(self):
        # Two grooves of the SAME size on one shaft must each be dimensioned — not collapsed
        # to one callout that leaves the other silently undimensioned (#148c review).
        shaft = Cylinder(10, 60)
        shaft -= Pos(0, 0, 15) * (Cylinder(10, 4) - Cylinder(8, 4))
        shaft -= Pos(0, 0, -15) * (Cylinder(10, 4) - Cylinder(8, 4))
        dwg = build_drawing(shaft, number="X")
        names = [n for n in dwg.annotations() if n.startswith("m_groove")]
        assert len(names) == 2

    def test_parallel_shafts_each_groove_gets_a_callout(self):
        # Identical grooves on two parallel shafts must each be dimensioned — grouping by
        # axis *letter* would collapse them onto one shaft (#148c review).
        g = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))
        part = Pos(-30, 0, 0) * g + Pos(30, 0, 0) * g
        dwg = build_drawing(part, number="X")
        names = [n for n in dwg.annotations() if n.startswith("m_groove")]
        assert len(names) == 2

    def test_coaxial_separate_solids_are_not_a_groove(self):
        # Three coaxial butted but SEPARATE bodies (a disc between two collars) form no single
        # channel — solid_idx in the shaft key keeps them distinct, so no phantom groove
        # (#148c review; mirrors #68).
        from build123d import Compound

        from draftwright.recognition import recognise_grooves

        stack = Compound(
            [Cylinder(20, 4), Pos(0, 0, 4) * Cylinder(5, 4), Pos(0, 0, 8) * Cylinder(20, 4)]
        )
        assert recognise_grooves(stack) == []

    def test_narrow_circlip_groove_floor_dimensioned_once(self):
        # A typical DIN 471 circlip groove is NARROW (~1.3 mm). recognise_turned_steps reports
        # its step at the WALL ø (local_od's pad engulfs both walls), so the step-exclusion must
        # key on axial position, not floor ø — else the floor ø double-dimensions via a spurious
        # step / boss (#148c 2nd-pass review, the primary use case).
        from draftwright.recognition import recognise_grooves

        narrow = Cylinder(10, 40) - Pos(0, 0, 10) * (Cylinder(10, 1.3) - Cylinder(9, 1.3))
        assert len(recognise_grooves(narrow)) == 1
        dwg = build_drawing(narrow, number="X")
        floor = [n for n in dwg.annotations() if "ø18" in str(getattr(dwg._named[n], "label", ""))]
        assert floor == [n for n in floor if n.startswith("m_groove")]
        assert len(floor) == 1

    def test_end_adjacent_groove_with_narrow_land_is_recognised(self):
        # A groove near the shaft end leaves a thin retaining LAND on the end side. That wall is
        # narrow because of end-proximity, not a staircase — the recogniser tests the WIDER wall,
        # so the real groove is still recognised (#148c 2nd-pass review).
        from build123d import Align

        from draftwright.recognition import recognise_grooves

        b = Align.MIN
        part = Cylinder(10, 30, align=(Align.CENTER, Align.CENTER, b))
        part += Pos(0, 0, 30) * Cylinder(9.25, 1.3, align=(Align.CENTER, Align.CENTER, b))
        part += Pos(0, 0, 31.3) * Cylinder(10, 1.0, align=(Align.CENTER, Align.CENTER, b))
        grooves = recognise_grooves(part)
        assert len(grooves) == 1
        assert grooves[0].diameter == pytest.approx(18.5, abs=0.05)

    def test_groove_floor_not_double_dimensioned_when_profile_gate_fails(self):
        # A grooved round body can fail the turned-step squareness gate (here a rectangular
        # flange), so detection falls to the boss (prof=None) branch. The groove floor must not
        # be emitted as BOTH a boss ø and the groove callout (#148c 3rd-pass review).
        part = Cylinder(10, 40) - Pos(0, 0, 5) * (Cylinder(10, 2) - Cylinder(8, 2))
        part += Box(40, 12, 4)
        dwg = build_drawing(part, number="X")
        floor = [n for n in dwg.annotations() if "ø16" in str(getattr(dwg._named[n], "label", ""))]
        assert floor == [n for n in floor if n.startswith("m_groove")]
        assert len(floor) == 1


class TestCountersinkCallout:
    """#558: a countersunk hole was called out as a plain THRU hole — no major-Ø /
    included-angle. It must now carry a csk callout (⌵ Ø14 × 90°), like a counterbore."""

    @staticmethod
    def _csk_plate():
        # The issue's repro: Ø6 through + a 90° csk flaring to Ø14 at the top face.
        from build123d import Cone

        plate = Box(90, 60, 12)
        for x, y in [(-30, -15), (5, 12), (30, -8)]:
            plate -= Pos(x, y, 0) * Cylinder(3, 12)
            plate -= Pos(x, y, 4) * Cone(3, 7, 4)
        return plate

    def test_countersink_recognised(self):
        from draftwright.recognition import recognise_countersinks

        cs = recognise_countersinks(self._csk_plate())
        assert len(cs) == 3
        for c in cs:
            assert abs(c.major_diameter - 14.0) < 0.1
            assert abs(c.drill_diameter - 6.0) < 0.1
            assert abs(c.included_angle - 90.0) < 0.5

    def test_countersunk_hole_carries_csink_in_ir(self):
        # Recovered as a HoleFeature.csink = (major_diameter, angle), grouped 3×.
        dwg = build_drawing(self._csk_plate(), number="X")
        holes = [f for f in dwg.model().features if f.kind == "hole"]
        assert len(holes) == 1 and holes[0].count == 3
        assert holes[0].csink is not None
        maj, ang = holes[0].csink
        assert abs(maj - 14.0) < 0.1 and abs(ang - 90.0) < 0.5

    def test_countersink_callout_is_placed_not_dropped(self):
        # The wider csk callout must reserve room in the layout estimate and place —
        # NOT drop like it did before the estimator learned about countersinks.
        dwg = build_drawing(self._csk_plate(), number="X")
        assert not any(getattr(i, "code", None) == "callout_dropped" for i in dwg._build_issues)
        leaders = [dwg._named[n] for n in dwg._named if n.startswith("hc_")]
        assert leaders, "no hole callout placed"
        # The placed callout covers both the bore (6) and the csk major (14).
        assert any(14.0 in ldr.covers_diameters for ldr in leaders)

    def test_plain_hole_has_no_countersink(self):
        plate = Box(90, 60, 12) - Pos(0, 0, 0) * Cylinder(3, 12)
        holes = [f for f in build_drawing(plate, number="X").model().features if f.kind == "hole"]
        assert holes and holes[0].csink is None

    def test_counterbore_is_not_a_countersink(self):
        # A ⌀18 counterbore (a cylindrical recess) must not register as a countersink.
        plate = Box(90, 60, 12)
        plate -= Pos(0, 0, 0) * Cylinder(3, 12)
        plate -= Pos(0, 0, 3) * Cylinder(9, 6)
        from draftwright.recognition import recognise_countersinks

        assert recognise_countersinks(plate) == []
        holes = [f for f in build_drawing(plate, number="X").model().features if f.kind == "hole"]
        assert holes and holes[0].csink is None and holes[0].cbore is not None

    def test_deburr_mouth_chamfer_is_not_a_countersink(self):
        # #558 review (BLOCKER): a 0.5 mm edge-break / deburr at a hole mouth is the same
        # cone shape as a shallow csk — the flare-ratio floor must exclude it, else every
        # chamfered hole mouth gets a spurious csk callout.
        from build123d import Axis, chamfer

        from draftwright.recognition import recognise_countersinks, recognise_holes

        plate = Box(30, 30, 10) - Pos(0, 0, 0) * Cylinder(3, 20)
        edge = plate.edges().filter_by(Axis.Z).group_by(lambda e: e.center().Z)[-1]
        plate = chamfer(edge, 0.5)
        assert recognise_countersinks(plate) == []
        assert recognise_holes(plate, csinks=recognise_countersinks(plate))[0].csink is None

    def test_opposite_face_coaxial_hole_is_not_mis_associated(self):
        # #558 review (BLOCKER): a countersink must attach only to the bore at its mouth,
        # facing the same way — NOT to a coaxial hole drilled from the opposite face.
        from build123d import Cone

        from draftwright.recognition import recognise_countersinks, recognise_holes

        p = Box(40, 40, 30)
        p -= Pos(0, 0, 9) * Cylinder(3, 12)  # top hole, opening at z=15
        p -= Pos(0, 0, 13) * Cone(3, 7, 4)  # csk at the top face
        p -= Pos(0, 0, -9) * Cylinder(3, 12)  # coaxial bottom hole, same bore, NO csk
        by_open = {
            round(h.location[2]): h for h in recognise_holes(p, csinks=recognise_countersinks(p))
        }
        top = max(by_open)  # the top (csk) hole
        assert by_open[top].csink is not None
        assert by_open[min(by_open)].csink is None  # the opposite-face hole stays plain

    def test_through_hole_countersink_is_orientation_independent(self):
        # #558 review round 2 (BLOCKER): a through hole is open at both faces, so
        # recognise_holes may call either end the "opening". The countersink must attach
        # regardless of which — a Z-flip must not drop it to plain THRU.
        from build123d import Rotation

        from draftwright.recognition import recognise_countersinks, recognise_holes

        flipped = Rotation(180, 0, 0) * self._csk_plate()
        holes = [
            h
            for h in recognise_holes(flipped, csinks=recognise_countersinks(flipped))
            if h.csink is not None
        ]
        assert len(holes) == 3  # all three csk holes keep their countersink after the flip

    def test_callout_angle_is_formatted_not_raw_float(self):
        # #558 review (BLOCKER): the angle must cross as a _fmt string so it renders
        # "× 90°" (not "× 90.0°") AND matches the width estimators — a raw float renders
        # wider and would re-drop the callout.
        from build123d_drafting import HoleCallout

        from draftwright.annotations.from_model import callout_from_spec, hole_callout_spec
        from draftwright.model.planner import plan_dimensions

        dwg = build_drawing(self._csk_plate(), number="X")
        m = dwg.model()
        hole = next(f for f in m.features if f.kind == "hole")
        g = next(gg for gg in plan_dimensions(m) if getattr(gg, "feature", None) is hole)
        built = callout_from_spec(hole_callout_spec(g), dwg.draft, 3)
        ref_str = HoleCallout(
            "6", count=3, through=True, csink_dia="14", csink_angle="90", draft=dwg.draft
        )
        ref_float = HoleCallout(
            "6", count=3, through=True, csink_dia="14", csink_angle=90.0, draft=dwg.draft
        )
        assert abs(built.callout_width - ref_str.callout_width) < 0.05  # "× 90°"
        assert abs(built.callout_width - ref_float.callout_width) > 1.0  # not "× 90.0°"


class TestStepSizingConvergence:
    def test_step_sizing_converges_past_the_old_three_pass_limit(self):
        measure_calls = []

        def measure(n_steps):
            measure_calls.append(n_steps)
            return StripDepths(right=float(n_steps), left=0.0)

        def pick(n_steps, strips):
            assert strips.right == pytest.approx(n_steps)
            return float(n_steps), 297.0, 210.0, 120.0

        def legible_count(scale):
            return {7.0: 5, 5.0: 4, 4.0: 2, 2.0: 2}[scale]

        pick_result, strips, n_steps = _converge_step_sizing(7, measure, pick, legible_count)

        assert pick_result == (2.0, 297.0, 210.0, 120.0)
        assert strips.right == pytest.approx(2.0)
        assert n_steps == 2
        assert measure_calls == [7, 5, 4, 2]

    def test_step_sizing_cycle_uses_the_larger_reservation(self, caplog):
        measure_calls = []

        def measure(n_steps):
            measure_calls.append(n_steps)
            return StripDepths(right=float(n_steps), left=0.0)

        def pick(n_steps, strips):
            assert strips.right == pytest.approx(n_steps)
            return float(n_steps), 297.0, 210.0, 120.0

        def legible_count(scale):
            return {4.0: 2, 2.0: 4}[scale]

        with caplog.at_level(logging.WARNING, logger="draftwright.analysis"):
            pick_result, strips, n_steps = _converge_step_sizing(4, measure, pick, legible_count)

        assert pick_result == (4.0, 297.0, 210.0, 120.0)
        assert strips.right == pytest.approx(4.0)
        assert n_steps == 4
        assert measure_calls == [4, 2, 4]
        assert "did not converge" in caplog.text


class TestChooseScale:
    def test_tiny_part_fits_A4(self):
        # 20×20×20 mm — enlargement scales don't fit A4/A3, lands on A4 2:1
        scale, pw, ph, tbw = choose_scale(20, 20, 20)
        assert int(pw) == 297
        assert scale == 2.0

    def test_medium_part_gets_A3(self):
        # 80×80×80 mm — fits A3 1:1 because the view rows clear the title block,
        # so its width no longer forces the jump to A2 (#62)
        scale, pw, ph, tbw = choose_scale(80, 80, 80)
        assert int(pw) == 420

    def test_ctc01_sized_part_gets_A2_not_A1(self):
        # 800×450×150 mm (NIST CTC-01) — iso sits above the title block so tb_w
        # is dropped from the width constraint. A2 fits; A1 is no longer chosen (#103).
        scale, pw, ph, tbw = choose_scale(800, 450, 150)
        assert scale == pytest.approx(0.2)
        assert int(pw) == 594  # A2 (594 mm), not A1 (841 mm)

    def test_large_part_gets_bigger_page(self):
        scale, pw, ph, tbw = choose_scale(300, 300, 300)
        assert pw > 420

    def test_returns_four_values(self):
        result = choose_scale(50, 50, 50)
        assert len(result) == 4

    def test_result_fits_on_page(self):
        # The chosen scale+page should actually fit the layout
        x, y, z = 60, 60, 15
        scale, pw, ph, tbw = choose_scale(x, y, z)
        assert _fits(x, y, z, scale, pw, ph, tbw)

    def test_section_participant_can_reduce_auto_scale(self):
        # A section view is real furniture, not a fixed-offset afterthought. The
        # compact 40×10×20 layout fits A4 at 2:1 without a section, but not once
        # the section block must share the side-view row with the iso and title.
        assert choose_scale(40, 10, 20, section=False)[:3] == (2.0, 297.0, 210.0)
        assert choose_scale(40, 10, 20, section=True)[:3] == (1.0, 297.0, 210.0)

    def test_table_footprint_participates_in_auto_scale_choice(self):
        # The compact 40×10×20 layout fits A4 at 2:1, but once a hole table must
        # share the sheet with the view blocks and iso, A4 has no table slot and
        # the shared fitness model escalates the page instead of dropping it later.
        assert choose_scale(40, 10, 20)[:3] == (2.0, 297.0, 210.0)
        assert choose_scale(40, 10, 20, table_sizes=((100.0, 60.0),))[:3] == (
            2.0,
            420.0,
            297.0,
        )

    def test_table_footprint_uses_composed_view_blocks(self):
        from draftwright.compose import _layout_geometry

        bare = _layout_geometry(
            40, 10, 20, 1.0, 420.0, 297.0, 150.0, None, table_sizes=((100.0, 60.0),)
        )
        stripped = _layout_geometry(
            40,
            10,
            20,
            1.0,
            420.0,
            297.0,
            150.0,
            StripDepths(right=20.0, left=20.0, top=60.0, pv_halo=30.0),
            table_sizes=((100.0, 60.0),),
        )

        assert bare.table_fits
        assert not stripped.table_fits

    # Enlargement scales for small parts (#62)

    def test_small_part_gets_enlargement_scale(self):
        # 28 × 8.5 × 12.5 mm (issue #62 part) → enlarged, and kept on the
        # smallest sheet: 2:1 on A4, not 5:1 on A3.  The ladder is page-major,
        # so a smaller sheet is preferred over a larger enlargement scale.
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5)
        assert scale == 2.0
        assert int(pw) == 297

    def test_very_small_part_gets_10x(self):
        scale, pw, ph, tbw = choose_scale(8, 4, 4)
        assert scale == 10.0
        assert int(pw) == 297

    # #350 — never return an overflowing layout for an oversized part.

    def test_oversized_part_gets_a_fitting_reduction(self):
        # 4200 × 1600 × 5400 mm (a civil/weldment-scale part) overflowed A0 1:5 before —
        # the ladder floored at 1:5, so choose_scale returned a layout it had just proved
        # did not fit. It now walks the rest of the ISO 5455 reductions to A0 1:10.
        x, y, z = 4200.0, 1600.0, 5400.0
        scale, pw, ph, tbw = choose_scale(x, y, z)
        assert scale == 0.1 and (pw, ph) == (1189.0, 841.0)  # A0 1:10
        assert _fits(x, y, z, scale, pw, ph, tbw)

    def test_choose_scale_never_overflows_across_the_size_range(self):
        # The invariant: automatic choose_scale never hands back a (scale, page) that
        # _fits reports as overflowing — from tiny to absurdly large (#350).
        for x, y, z in [
            (5, 5, 5),
            (300, 300, 300),
            (4200, 1600, 5400),
            (40000, 2000, 60000),
            (500000, 5000, 800000),
        ]:
            scale, pw, ph, tbw = choose_scale(x, y, z)
            assert _fits(x, y, z, scale, pw, ph, tbw), (
                f"{(x, y, z)} -> {(scale, pw, ph)} overflows"
            )

    def test_backstop_computes_a_fit_beyond_the_ladder(self):
        # A part too large even for A0 1:10000 falls to the bisection backstop and still
        # returns a scale that fits — a non-standard scale is acceptable for an
        # out-of-domain part; anything beats an overflowing layout.
        x, y, z = 20_000_000.0, 5000.0, 30_000_000.0  # ~30 km — deliberately absurd
        scale, pw, ph, tbw = choose_scale(x, y, z)
        assert 0.0 < scale < 0.0001
        assert _fits(x, y, z, scale, pw, ph, tbw)


class TestChooseScaleOverrides:
    def test_scale_and_page_used_verbatim(self):
        assert choose_scale(28, 8.5, 12.5, scale=5, page="A3") == (5.0, 420.0, 297.0, 150.0)

    def test_scale_and_page_honoured_even_when_too_small(self):
        # Explicit overrides win even if the layout doesn't fit (warning only)
        scale, pw, ph, tbw = choose_scale(300, 300, 300, scale=1, page="A4")
        assert (scale, pw) == (1.0, 297.0)

    def test_page_only_picks_largest_fitting_scale(self):
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5, page="A3")
        assert (pw, ph) == (420.0, 297.0)
        assert scale == 5.0

    def test_specified_page_enlarges_long_short_part_via_2d_iso(self):
        # A long, short part (100 × 10 × 11, e.g. a staircase) fills a specified
        # A3 sheet at 2:1. The automatic path stays conservative, but fixed-page
        # requests use the packed verdict exposed by the same layout geometry.
        from draftwright.compose import _fits

        assert _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0, pack_iso_2d=True)
        assert not _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0, pack_iso_2d=False)
        scale, pw, ph, _ = choose_scale(100, 10, 11, page="A3")
        assert scale == 2.0
        assert (pw, ph) == (420.0, 297.0)
        # Automatic selection remains page-major: A4 at 1:1 is tried before A3 at 2:1.
        assert choose_scale(100, 10, 11)[:3] == (1.0, 297.0, 210.0)

    def test_scale_only_picks_smallest_fitting_page(self):
        scale, pw, ph, tbw = choose_scale(28, 8.5, 12.5, scale=2)
        assert scale == 2.0
        assert int(pw) == 297

    def test_scale_only_enlarges_long_short_part_via_2d_iso(self):
        # Fixed scale, no page: choose_scale walks the page list with the packed
        # verdict exposed by the shared geometry. At 2:1 the part overruns A4
        # but fits A3.
        from draftwright.compose import _fits

        assert not _fits(100, 10, 11, 2.0, 297.0, 210.0, 120.0, pack_iso_2d=True)
        assert _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0, pack_iso_2d=True)
        assert not _fits(100, 10, 11, 2.0, 420.0, 297.0, 150.0)
        assert choose_scale(100, 10, 11, scale=2) == (2.0, 420.0, 297.0, 150.0)

    def test_page_tuple(self):
        scale, pw, ph, tbw = choose_scale(10, 10, 10, page=(420, 297))
        assert (pw, ph, tbw) == (420.0, 297.0, 150.0)

    def test_page_wxh_string(self):
        scale, pw, ph, tbw = choose_scale(10, 10, 10, page="420x297")
        assert (pw, ph) == (420.0, 297.0)

    def test_page_name_case_insensitive(self):
        scale, pw, ph, tbw = choose_scale(10, 10, 10, page="a3")
        assert (pw, ph) == (420.0, 297.0)

    def test_unknown_page_raises(self):
        with pytest.raises(ValueError, match="page size"):
            choose_scale(10, 10, 10, page="B5")

    def test_nonpositive_scale_raises(self):
        with pytest.raises(ValueError, match="scale"):
            choose_scale(10, 10, 10, scale=0)


class TestIsometricOrientation:
    """#620: the isometric is an orientation aid, so it must show the same front (-Y) /
    right (+X) / top face combination the orthographic views do — not the rear (+Y) side.
    The old camera viewed from +Y, mirroring asymmetric features against the front view."""

    @staticmethod
    def _iso_visible_edges(part):
        dwg = build_drawing(part, number="X")
        vis, _ = dwg.views["iso"]
        return len(vis.edges())

    def test_iso_shows_the_minus_y_face_like_the_front_view(self):
        # A pocket on the -Y face (the face the front view sees) is on the iso's NEAR side, so
        # more of its edges are visible than the same pocket on the +Y (rear) face. The old +Y
        # camera reversed this — it showed the rear side against a front-view ortho set.
        front = Box(60, 40, 30) - Pos(15, -20, 0) * Box(8, 8, 8)
        rear = Box(60, 40, 30) - Pos(15, 20, 0) * Box(8, 8, 8)
        assert self._iso_visible_edges(front) > self._iso_visible_edges(rear)

    def test_iso_shows_the_plus_x_face_like_the_right_view(self):
        # The right-side view sees the +X face; the iso must expose it too (the camera's X is
        # +X, unchanged — this guards the other two signs while #620 fixes Y).
        right = Box(40, 60, 30) - Pos(20, 15, 0) * Box(8, 8, 8)
        left = Box(40, 60, 30) - Pos(-20, 15, 0) * Box(8, 8, 8)
        assert self._iso_visible_edges(right) > self._iso_visible_edges(left)


class TestIsoEmptyRect:
    def test_largest_empty_rect_fallback_when_fully_covered(self):
        # When obstacles leave no genuine gap, _largest_empty_rect returns the
        # whole drawable (documented fallback) — the mechanism iso_valid checks.
        from draftwright._core import _largest_empty_rect

        drawable = (10.0, 10.0, 90.0, 90.0)
        assert _largest_empty_rect(drawable, [drawable]) == drawable

    def test_layout_geometry_iso_valid_false_when_no_gap(self):
        # A part that fills the sheet leaves no empty rectangle for the iso, so
        # the fallback returns the drawable (overlapping the view obstacles) and
        # iso_valid is False — the flag _fits uses to reject such a layout.
        from draftwright.compose import _layout_geometry

        g = _layout_geometry(200, 150, 150, 2.0, 297.0, 210.0, 120.0, None)
        assert g.iso_valid is False

    def test_layout_geometry_iso_valid_true_for_normal_part(self):
        from draftwright.compose import _layout_geometry

        g = _layout_geometry(20, 20, 20, 1.0, 297.0, 210.0, 120.0, None)
        assert g.iso_valid is True


class TestScaleMinimum:
    """An explicit scale below the legibility floor is honoured with a warning (#489);
    only a genuinely degenerate scale (below the hard geometry floor) raises."""

    def test_explicit_illegible_scale_warns_and_renders(self, tmp_path):
        # 80 mm thin part at scale=0.1 → 8 mm projection < _MIN_VIEW_MM (10) but ≫ the hard
        # floor. The user asked for it (#489): honour it with a legibility warning, don't raise.
        part = Box(680, 860, 80)
        with pytest.warns(UserWarning, match="legibility floor"):
            result = make_drawing(part, out=str(tmp_path / "out"), scale=0.1)
        assert result is not None

    def test_warning_suggests_safe_scale(self, tmp_path):
        import re

        part = Box(680, 860, 80)
        with pytest.warns(UserWarning) as record:
            make_drawing(part, out=str(tmp_path / "out"), scale=0.1)
        msg = str(record[0].message)
        assert "scale" in msg.lower()
        # Names the minimum legible scale (≥ 10/80 = 0.125).
        nums = re.findall(r"\d+\.?\d*", msg)
        safe_scales = [float(n) for n in nums if 0.1 < float(n) < 1.0]
        assert any(s >= _MIN_VIEW_MM / 80 for s in safe_scales)

    def test_degenerate_scale_raises(self, tmp_path):
        # Below the hard geometry floor (80 mm × 0.001 = 0.08 mm < _MIN_RENDER_MM): no meaningful
        # drawing exists and OCCT arcs would degenerate — raise a clean error, not a cryptic OCP one.
        part = Box(680, 860, 80)
        with pytest.raises(ValueError, match="geometry degenerates"):
            make_drawing(part, out=str(tmp_path / "out"), scale=0.001)

    def test_safe_scale_does_not_warn(self, tmp_path):
        # 0.2 → 80*0.2 = 16 mm > _MIN_VIEW_MM: legible, no warning.
        import warnings

        part = Box(680, 860, 80)
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any legibility warning would fail here
            result = make_drawing(part, out=str(tmp_path / "out"), scale=0.2)
        assert result is not None

    def test_auto_scale_thin_part_does_not_raise(self, tmp_path):
        # Auto scale for a thin plate is never bound by the legibility floor (it's the auto path).
        part = Box(80, 50, 8)
        result = make_drawing(part, out=str(tmp_path / "out"))
        assert result is not None

    def test_inherently_subfloor_part_does_not_warn(self, tmp_path):
        # A huge, thin part is below the legibility floor at EVERY page-fitting scale (even auto),
        # so a bigger scale can't help — the legibility warning would be false advice. It must stay
        # silent (like the auto path) and still render. (min_view 0.75 mm is > the 0.1 mm hard floor.)
        import warnings

        part = Box(3000, 3000, 15)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = make_drawing(part, out=str(tmp_path / "out"), scale=0.05)
        assert result is not None
        assert not [w for w in caught if "legibility floor" in str(w.message)]

    def test_sheet_explicit_scale_below_floor_is_honoured(self, tmp_path):
        # #489 public surface: Sheet(scale="1:10") on a thin part is below the legibility floor
        # (80 mm × 0.1 = 8 mm) but is the user's intentional choice — warn, render, don't raise.
        from draftwright import Sheet

        with pytest.warns(UserWarning, match="legibility floor"):
            sheet = Sheet(Box(680, 860, 80), scale="1:10")
            sheet.export(str(tmp_path / "s"))
        assert (tmp_path / "s.svg").exists()


class TestSectionHatchEdges:
    """Unit tests for _section_hatch_edges even-odd fill algorithm."""

    def test_rectangle_hatch_line_through_corner_fills_interior(self):
        # A 45° hatch line passing exactly through a corner vertex must not
        # produce an odd-length hits list — the span must still be drawn.
        # Face.make_rect(10, 5, Plane.XZ) gives corners at X∈[-5,5], Z∈[-2.5,2.5].
        # With spacing=5, c=0 gives hatch line through corner (-5,-2.5).
        from build123d import Face, Plane

        from draftwright.annotations.sections import _section_hatch_edges

        face = Face.make_rect(10, 5, Plane.XZ)
        edges = _section_hatch_edges(face, lambda x: x, lambda z: z, spacing=5.0)
        assert len(edges) > 0, "corner vertex hit must not suppress all hatch spans"
        for e in edges:
            p0, p1 = e.position_at(0), e.position_at(1)
            assert p1.X - p0.X > 0.1, f"zero-length hatch span dx={p1.X - p0.X}"

    def test_hatch_edges_are_45_degrees(self):
        from build123d import Face, Plane

        from draftwright.annotations.sections import _section_hatch_edges

        face = Face.make_rect(20, 15, Plane.XZ)
        edges = _section_hatch_edges(face, lambda x: x, lambda z: z, spacing=4.5)
        assert len(edges) > 0
        for e in edges:
            p0, p1 = e.position_at(0), e.position_at(1)
            dx, dy = p1.X - p0.X, p1.Y - p0.Y
            assert abs(dy / dx - 1.0) < 0.01, f"hatch not at 45°: slope={dy / dx}"


class TestStripZones:
    """Unit tests for the Strip / ViewZones layout primitives (issue #105)."""

    def test_strip_import(self):
        pass

    def test_strip_available(self):
        from draftwright._core import Strip

        s = Strip(anchor=50.0, outer_limit=150.0, direction=1)
        assert s.available == pytest.approx(100.0)

    def test_analyse_returns_view_zones(self):
        from build123d import Box, Cylinder

        from draftwright import build_drawing
        from draftwright._core import Strip, ViewZones

        part = Box(80, 60, 20) - Cylinder(5, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        assert isinstance(a.fv_zones, ViewZones)
        assert isinstance(a.pv_zones, ViewZones)
        assert isinstance(a.sv_zones, ViewZones)
        assert isinstance(a.fv_zones.right, Strip)
        assert isinstance(a.pv_zones.above, Strip)
        assert isinstance(a.pv_zones.below, Strip)  # dim_width goes here
        assert a.sv_zones.left is None  # abuts front view

    def test_strip_limits_are_within_page(self):
        from build123d import Box, Cylinder

        from draftwright import build_drawing

        part = Box(80, 60, 20) - Cylinder(5, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        margin = a.margin
        # Outer limits should be within the page
        assert a.fv_zones.right.outer_limit <= a.PAGE_W
        assert a.pv_zones.above.outer_limit <= a.PAGE_H
        assert a.fv_zones.left.outer_limit >= margin

    def test_dim_height_routed_through_fv_right_strip(self):
        # dim_height must be placed via the strip; its dimension line must
        # land within the fv_zones.right corridor (anchor..outer_limit).
        from build123d import Box

        from draftwright import build_drawing

        part = Box(60, 40, 30)
        dwg = build_drawing(part)
        assert "dim_height" in dwg._named
        ann = dwg._named["dim_height"]
        # label is the part height
        assert ann.label == "30"

    def test_pv_below_strip_is_now_active(self):
        # pv_zones.below should be a Strip (not None) after Phase 3
        from build123d import Box

        from draftwright import build_drawing
        from draftwright._core import Strip

        part = Box(80, 60, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        assert isinstance(a.pv_zones.below, Strip)
        assert a.pv_zones.below.direction == -1
        # The outer_limit must be above the front view top edge (fv_hh from FV_Y)
        assert a.pv_zones.below.outer_limit < a.pv_zones.below.anchor

    def test_dim_width_routed_through_pv_below_strip(self):
        # dim_width must exist below the plan view, with depth_used > 0
        from build123d import Box

        from draftwright import build_drawing

        # non-square part → width != depth → the width dim should appear (IR
        # renderer m_env_width, still routed through pv_zones.below).
        part = Box(80, 40, 20)
        dwg = build_drawing(part)
        assert "m_env_width" in dwg._named
        ann = dwg._named["m_env_width"]
        assert ann.label == "80"

    def test_dim_locx_routed_through_pv_above_strip(self):
        # dim_locx dims must be above plan_top and allocated from pv_zones.above
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        part = Box(80, 60, 20) - Pos(20, 10, 0) * Cylinder(5, 20)
        dwg = build_drawing(part)
        locx_dims = [v for n, v in dwg._named.items() if n.startswith("m_locx")]
        assert len(locx_dims) >= 1, "expected m_locx0 to be generated for off-datum cylinder"
        plan_top = dwg.views["plan"][0].bounding_box().max.Y
        assert all(d.dim_level_y > plan_top for d in locx_dims)

    def test_dim_locy_routed_through_sv_above_strip(self):
        # dim_locy dims must be above side_top and allocated from sv_zones.above
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        # Cylinder at Y=10 → offset from datum_y=bb.min.Y → generates dim_locy0
        part = Box(80, 60, 20) - Pos(0, 10, 0) * Cylinder(5, 20)
        dwg = build_drawing(part)
        locy_dims = [v for n, v in dwg._named.items() if n.startswith("m_locy")]
        assert len(locy_dims) >= 1, "expected m_locy0 to be generated for off-datum cylinder"
        side_top = dwg.views["side"][0].bounding_box().max.Y
        assert all(d.dim_level_y > side_top for d in locy_dims)

    def test_dim_step_placed_after_phase3_corridor_widening(self):
        # Phase 3 widens fv_zones.right dynamically for stepped parts.
        # A part with one step face gets gap_fv_sv = 36 mm (vs 18 mm fixed),
        # which is enough for dim_height (10 mm) + spacing (4 mm) + dim_step (14 mm).
        # Both annotations must now appear without overlapping the side view.
        from build123d import Box, Pos

        from draftwright import build_drawing
        from draftwright.compose import _est_right_strip_depth

        part = Box(40, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        # dim_height and at least one dim_step must be generated
        assert "dim_height" in dwg._named
        step_dims = [n for n in dwg._named if n.startswith("dim_step")]
        assert len(step_dims) >= 1, "dim_step must appear after Phase 3 corridor widening"
        # The FV→SV gap must equal the estimator value for the height-gated count.
        # Use the same gate _analyse() applies: (z - bb.min.Z) * SCALE >= 20.
        # a.step_zs is the raw (ungated) list; using len(a.step_zs) would give the
        # wrong expected gap for parts with shallow step faces.
        n = len([z for z in a.step_zs[:3] if (z - a.bb.min.Z) * a.SCALE >= 20])
        expected_gap = _est_right_strip_depth(n)
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        assert sv_left - fv_right == pytest.approx(expected_gap, abs=0.1)
        # Annotations must not enter the side view geometry
        assert sv_left - fv_right > 0

    def test_fv_right_outer_limit_does_not_enter_side_view(self):
        # Phase 1: fv_zones.right outer_limit must be <= the side view left edge.
        # Previously it was iso_right_limit (far right of page), causing dim_step
        # annotations to be placed inside the side view geometry.
        # pv_zones.right is intentionally unrestricted — hole callouts for the
        # plan view go to the right of the plan/side pair (different Y band) and
        # need the full iso-bounded corridor.
        from build123d import Box

        from draftwright import build_drawing

        part = Box(80, 60, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        sv_left = a.SV_X - a.sv_hw
        assert a.fv_zones.right.outer_limit <= sv_left + 0.5

    def test_dim_height_still_placed_after_outer_limit_fix(self):
        # Phase 1: dim_height must still be generated — it fits in the 18 mm
        # corridor (gap=8 + slot=10 = 18 mm exactly).
        from build123d import Box

        from draftwright import build_drawing

        part = Box(60, 40, 30)
        dwg = build_drawing(part)
        assert "dim_height" in dwg._named
        assert dwg._named["dim_height"].label == "30"

    def test_overall_height_dim_sits_outside_step_dims(self):
        # staircase.step review: the overall-height dimension must nest OUTSIDE
        # the step-height dims (placed last so it is outermost), so extension
        # lines nest rather than leapfrog. A stepped part exercises both.
        from build123d import Box, Pos

        from draftwright import build_drawing

        part = (
            Box(40, 12, 60) - Pos(10, 0, 30) * Box(20, 12, 30) - Pos(-10, 0, 40) * Box(20, 12, 20)
        )
        dwg = build_drawing(part)
        assert "dim_height" in dwg._named
        step_dims = [n for n in dwg._named if n.startswith("dim_step")]
        assert step_dims, "expected at least one step dim"
        height_x = dwg._named["dim_height"].bounding_box().max.X
        for n in step_dims:
            step_x = dwg._named[n].bounding_box().max.X
            assert height_x > step_x, f"overall height must sit outside {n}"

    def test_right_strip_outer_limits_tightened_to_iso(self):
        # fv.right and pv.right are both bounded by sv_left_edge so bore callout
        # labels cannot cross into the side view.  The sv.right strip is only
        # iso-tightened (to iso_x0 - 4) when the iso shares the side view's
        # y-range; with the #11 free-rectangle placement the iso may instead sit
        # above the side view, in which case sv.right keeps its full width.
        # Use a plain box (no holes) so bore callout overhead doesn't push the
        # iso view right and interfere with the sv tightening check.
        from build123d import Box

        from draftwright import build_drawing
        from draftwright._core import _iso_bbox

        part = Box(80, 60, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        sv_left = a.SV_X - a.sv_hw
        iso_x0, iso_y0, _, iso_y1 = _iso_bbox(dwg)
        iso_limit = iso_x0 - 4
        # fv right must not extend past the side view left edge
        assert a.fv_zones.right.outer_limit == pytest.approx(sv_left, abs=0.1)
        # pv right is also bounded by sv_left so bore callout labels cannot
        # cross dim_locy extension lines in the side view corridor
        assert a.pv_zones.right.outer_limit == pytest.approx(sv_left, abs=0.1)
        # sv right strip is iso-tightened only when the iso overlaps its y-range.
        sv_y0, sv_y1 = a.SV_Y - a.fv_hh, a.SV_Y + a.fv_hh
        if sv_y0 < iso_y1 and iso_y0 < sv_y1:
            assert a.sv_zones.right.outer_limit == pytest.approx(iso_limit, abs=0.1)
        else:
            assert a.sv_zones.right.outer_limit > iso_limit

    def test_sv_zones_below_strip_is_active(self):
        # sv_zones.below must be a Strip (not None) after _analyse().
        from build123d import Box

        from draftwright import build_drawing

        part = Box(80, 60, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        assert a.sv_zones.below is not None, "sv_zones.below should be a Strip"

    def test_dim_depth_routed_through_sv_zones_below(self):
        # dim_depth (Y envelope) must be placed below side_top via sv_zones.below.
        # Uses a part where x_size != y_size by > 5% to trigger the annotation.
        from build123d import Box

        from draftwright import build_drawing

        # 80×40×20 box: width=80, depth=40 — differ by > 5%, so the depth dim fires
        # (IR renderer m_env_depth, still routed through sv_zones.below).
        part = Box(80, 40, 20)
        dwg = build_drawing(part)
        assert "m_env_depth" in dwg._named, "expected m_env_depth for part with width != depth"
        ann = dwg._named["m_env_depth"]
        assert ann.label == "40", f"depth label should be y_size=40, got {ann.label!r}"

    def test_dim_depth_absent_for_square_plan(self):
        # dim_depth must be omitted when x_size == y_size (within 5%).
        from build123d import Box

        from draftwright import build_drawing

        part = Box(60, 60, 20)  # square plan: x_size == y_size
        dwg = build_drawing(part)
        assert "m_env_depth" not in dwg._named, "depth dim should be skipped for square plan"


# ---------------------------------------------------------------------------
# Phase 2 annotation depth estimators (#118)
# ---------------------------------------------------------------------------


class TestDepthEstimators:
    """Pure-function tests for _est_right_strip_depth / _est_pv_below_depth."""

    def test_right_depth_no_steps_equals_dim_pad(self):
        from draftwright._core import _DIM_PAD
        from draftwright.compose import _est_right_strip_depth

        # 0 steps → dim_height only → gap(10) + slot(10) = 20 = _DIM_PAD
        assert _est_right_strip_depth(0) == pytest.approx(_DIM_PAD, abs=0.01)

    def test_right_depth_one_step(self):
        from draftwright.compose import _est_right_strip_depth

        # gap(10) + dim_height(10) + spacing(2.5) + 1×dim_step(14) = 10 + 10 + 2.5 + 14 = 36.5
        assert _est_right_strip_depth(1) == pytest.approx(36.5, abs=0.01)

    def test_right_depth_three_steps(self):
        from draftwright.compose import _est_right_strip_depth

        # gap(10) + dim_height(10) + 3×dim_step(14) + 3×spacing(2.5) = 10+10+3×(2.5+14) = 69.5
        assert _est_right_strip_depth(3) == pytest.approx(69.5, abs=0.01)

    def test_right_depth_grows_per_step_uncapped(self):
        from draftwright._core import _SLOT_DIM_STEP
        from draftwright.compose import _est_right_strip_depth
        from draftwright.drawing import _STRIP_SPACING

        # #36: no cap — each further step adds one slot + one spacing.
        assert _est_right_strip_depth(10) > _est_right_strip_depth(3)
        assert _est_right_strip_depth(10) - _est_right_strip_depth(3) == pytest.approx(
            7 * (_STRIP_SPACING + _SLOT_DIM_STEP), abs=0.01
        )

    def test_right_depth_increases_with_steps(self):
        from draftwright.compose import _est_right_strip_depth

        assert _est_right_strip_depth(0) < _est_right_strip_depth(1) < _est_right_strip_depth(3)

    def test_pv_below_depth(self):
        from draftwright.compose import _est_pv_below_depth

        # gap(10) + dim_width slot(8) = 18
        assert _est_pv_below_depth() == pytest.approx(18.0, abs=0.01)

    def test_right_depth_fits_in_exact_corridor(self):
        # _est_right_strip_depth(n) must reserve enough corridor for dim_height + n
        # dim_steps stacked from the view edge (gap, then `spacing` between dims) — the
        # cursor-free capacity condition the carve places into (ADR 0009 / #150).
        from draftwright._core import _SLOT_DIM_HEIGHT, _SLOT_DIM_STEP, _STRIP_SPACING
        from draftwright.compose import _est_right_strip_depth
        from draftwright.drawing import _STRIP_GAP

        for n_steps in (0, 1, 3):
            est = _est_right_strip_depth(n_steps)
            sizes = [_SLOT_DIM_HEIGHT] + [_SLOT_DIM_STEP] * n_steps
            needed = _STRIP_GAP + sum(sizes) + _STRIP_SPACING * (len(sizes) - 1)
            assert needed <= est + 1e-9, f"n_steps={n_steps}: needs {needed} > est {est}"

    def test_pv_below_depth_fits_in_exact_corridor(self):
        # _est_pv_below_depth() must reserve enough for one dim_width from the view edge.
        from draftwright._core import _SLOT_DIM_WIDTH
        from draftwright.compose import _est_pv_below_depth
        from draftwright.drawing import _STRIP_GAP

        assert _STRIP_GAP + _SLOT_DIM_WIDTH <= _est_pv_below_depth() + 1e-9


# ---------------------------------------------------------------------------
# #31: layout constants derived from text metrics
# ---------------------------------------------------------------------------


class TestDerivedLayoutConstants:
    """Slots / callout widths / iso budget derive from text metrics, not bare mm."""

    def test_slots_derive_from_font_metrics(self):
        from draftwright._core import (
            _FONT_SIZE,
            _PAD,
            _SLOT_DIM_DEPTH,
            _SLOT_DIM_HEIGHT,
            _SLOT_DIM_STEP,
            _SLOT_DIM_WIDTH,
        )

        assert _SLOT_DIM_WIDTH == pytest.approx(2 * _FONT_SIZE + _PAD)
        assert _SLOT_DIM_DEPTH == pytest.approx(2 * _FONT_SIZE + _PAD)
        assert _SLOT_DIM_HEIGHT == pytest.approx(2 * _FONT_SIZE + 2 * _PAD)
        assert _SLOT_DIM_STEP == pytest.approx(4 * _FONT_SIZE + _PAD)
        # The slots are linear in font metrics — a hypothetical larger font
        # would yield larger slots — so they are not frozen mm constants.
        assert (2 * (2 * _FONT_SIZE) + 2 * _PAD) > _SLOT_DIM_HEIGHT

    def test_text_width_returns_real_glyph_metrics(self):
        from draftwright._core import _text_width

        assert _text_width("", 3.0) == 0.0
        # A real measurement is positive and grows with the string.
        w1 = _text_width("8", 3.0)
        w3 = _text_width("888", 3.0)
        assert 0.0 < w1 < w3
        # Real glyph metrics, not a character-count fudge (#31): equal-length
        # strings of wide vs narrow glyphs measure differently. (Pinned to the
        # vendored Plex Mono via font_path, #149, so these widths are also
        # deterministic across platforms; Plex Mono being monospace made the old
        # Arial "wider-than-0.6*font" threshold both font-specific and moot.)
        assert _text_width("WXYZ", 3.0) > _text_width("iiii", 3.0)

    def test_bore_callout_width_scales_with_font_size(self):
        from build123d_drafting.helpers import draft_preset

        from draftwright.annotations.orchestrator import build_model
        from draftwright.compose import _est_planned_bore_callout_width
        from draftwright.model import plan_dimensions

        part = Box(60, 40, 12) - Pos(0, 0, 6) * Cylinder(3, 12)
        groups = plan_dimensions(build_model(build_drawing(part, number="X")._analysis))
        draft = draft_preset(decimal_precision=1)
        small = _est_planned_bore_callout_width(groups, draft, font_size=3.0)
        large = _est_planned_bore_callout_width(groups, draft, font_size=6.0)
        assert large > small


def _sizing_model(part):
    """The sizing IR + planner callout width for *part*, mirroring `_analyse` — the
    detected-path input the sheet estimators now consume (ADR 0008; #584 WP1 A)."""
    from build123d_drafting.helpers import draft_preset

    from draftwright._core import _FONT_SIZE
    from draftwright.annotations.orchestrator import build_model
    from draftwright.compose import _est_planned_bore_callout_width
    from draftwright.model import plan_dimensions

    m = build_model(build_drawing(part, number="X")._analysis)
    draft = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
    w = _est_planned_bore_callout_width(
        plan_dimensions(m), draft, font_size=_FONT_SIZE, pad_around_text=draft.pad_around_text
    )
    return m, w


class TestComposeAnnoBoxes:
    """Step 4a (#112): the AnnoBox composer reduces to the identical StripDepths
    that _measure_strips computes — the byte-identical box-model foundation that
    later steps make honest."""

    def _assert_match(self, model, n_steps, bb, w=0.0, label=""):
        from draftwright.builder import _FONT_SIZE, draft_preset
        from draftwright.compose import _compose_anno_boxes, _footprint_from_boxes, _measure_strips

        # The composer is the footprint authority (#112): _measure_strips is only
        # a compatibility reducer around these boxes. Exercise defaults, the
        # production draft preset, and deliberately divergent clearance values.
        preset = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
        arg_sets = (
            {},
            {"arrow_length": preset.arrow_length, "pad_around_text": preset.pad_around_text},
            {"arrow_length": 4.3, "pad_around_text": 3.1},
        )
        for kw in arg_sets:
            composed = _footprint_from_boxes(
                _compose_anno_boxes(model, n_steps, bore_callout_width=w, **kw)
            )
            scalar = _measure_strips(model, n_steps, bb, bore_callout_width=w, **kw)
            assert composed == scalar, (label, n_steps, kw)

    def test_bore_callout_width_flows_through_boxes(self):
        # #540/#584 WP1 A: the planner-derived callout width (authored tolerances
        # included) must be represented as an annotation box, not a scalar-only side
        # channel in _measure_strips.
        from draftwright.builder import _FONT_SIZE, draft_preset
        from draftwright.compose import _compose_anno_boxes, _footprint_from_boxes, _measure_strips

        part = Box(60, 40, 12) - Pos(0, 0, 6) * Cylinder(3, 12)
        model, _ = _sizing_model(part)
        bb = part.bounding_box()
        draft = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
        width = 55.0
        expected_bore_depth = width + draft.pad_around_text + draft.arrow_length

        boxes = _compose_anno_boxes(
            model,
            0,
            bore_callout_width=width,
            arrow_length=draft.arrow_length,
            pad_around_text=draft.pad_around_text,
        )
        assert expected_bore_depth in [b.depth for b in boxes if b.side == "right"]
        assert expected_bore_depth in [b.depth for b in boxes if b.side == "left"]
        assert _footprint_from_boxes(boxes) == _measure_strips(
            model,
            0,
            bb,
            bore_callout_width=width,
            arrow_length=draft.arrow_length,
            pad_around_text=draft.pad_around_text,
        )

    def test_matches_for_plain_part(self):
        part = Box(60, 40, 12)
        model, w = _sizing_model(part)
        bb = part.bounding_box()
        for n_steps in (0, 1, 3):
            self._assert_match(model, n_steps, bb, w)

    def test_matches_for_bored_part(self):
        part = Box(60, 40, 12) - Pos(0, 0, 6) * Cylinder(3, 12)
        model, w = _sizing_model(part)
        bb = part.bounding_box()
        for n_steps in (0, 2):
            self._assert_match(model, n_steps, bb, w)

    def test_matches_for_dense_ballooning_part(self):
        # _dense_plate triggers _will_balloon → exercises the plan_halo band.
        from draftwright.compose import _will_balloon

        part = _dense_plate()
        model, w = _sizing_model(part)
        bb = part.bounding_box()
        assert _will_balloon(model)  # guard: this case must balloon
        self._assert_match(model, 0, bb, w)

    def test_pattern_plus_same_spec_loose_size_as_separate_callouts(self):
        # #584 WP1 A (accepted divergence, more-correct): a pattern and same-spec LOOSE
        # holes are separate IR features, so sizing reserves for the pattern's
        # "8× …EQ SP ON ø… BC" callout — NOT a phantom merged "11×". The renderer emits
        # them as distinct callouts, so adding same-spec loose holes must not widen the
        # pattern's callout corridor (the old record-based estimator merged them and
        # over-reserved for a callout that never renders).
        from math import cos, radians, sin

        ring = Box(120, 120, 8)
        for i in range(8):
            a = radians(45 * i)
            ring -= Pos(35 * cos(a), 35 * sin(a), 0) * Cylinder(3, 8)
        part = ring
        for x, y in [(-52, -52), (52, 52), (-52, 52)]:
            part -= Pos(x, y, 0) * Cylinder(3, 8)  # 3 loose ø6 holes, same spec

        model_both, w_both = _sizing_model(part)
        kinds = sorted(f.kind for f in model_both.features if f.kind in ("hole", "pattern"))
        assert kinds == ["hole", "pattern"]  # separate features, not merged
        _, w_ring = _sizing_model(ring)  # pattern alone
        assert w_both == pytest.approx(w_ring)  # loose same-spec holes don't widen it

    def test_footprint_reduction_and_left_floor(self):
        # Direct unit test of the reducer: deepest band per side wins, and the
        # left keeps its _DIM_PAD floor even when the deepest left band is
        # shallower — the branch real parts rarely make the deciding one.
        from draftwright._core import _DIM_PAD
        from draftwright.compose import AnnoBox, StripDepths, _footprint_from_boxes

        fp = _footprint_from_boxes(
            [
                AnnoBox("right", 5.0),
                AnnoBox("right", 30.0),  # deeper right band wins
                AnnoBox("left", 5.0),  # below the floor → floor wins
                AnnoBox("plan_halo", 21.0),
            ]
        )
        assert fp == StripDepths(right=30.0, left=_DIM_PAD, pv_halo=21.0)

        # No bands at all → zero depths, but the left floor still applies.
        assert _footprint_from_boxes([]) == StripDepths(right=0.0, left=_DIM_PAD, pv_halo=0.0)


class TestComposeAnnoBoxesCorpus:
    """Step 4b (#112): de-risk the 4c reservation switch by proving the AnnoBox
    composer is a faithful drop-in for _measure_strips across the full part
    archetype corpus, and by pinning the per-side box *structure* that 4c will
    consume. The strip estimate now reduces these boxes, so byte-identity here
    guards the active layout path."""

    @staticmethod
    def _corpus():
        """The part archetypes draftwright draws, spanning every branch of
        _compose_anno_boxes: a plain prismatic block (right ladder only), a
        single bore and a multi-spec / corner-holed plate (left+right bore
        bands), and a dense plate that escalates to the leadered hole chart
        (plan halo band). The right dim ladder depth is a pure function of the
        n_steps argument (not geometry), so it is swept per part below rather
        than via a dedicated stepped fixture. Each entry carries the sizing IR
        model + planner callout width the estimators now consume (#584 WP1 A)."""
        parts = {
            "plain_block": Box(60, 40, 12),
            "single_bore": Box(60, 40, 12) - Pos(0, 0, 6) * Cylinder(3, 12),
            "multi_hole": _multi_hole_plate(),
            "holed_plate": _holed_plate(),
            "dense_balloon": _dense_plate(),
        }
        corpus = []
        for label, part in parts.items():
            model, w = _sizing_model(part)
            corpus.append((label, model, w, part.bounding_box()))
        return corpus

    def test_byte_identity_across_corpus(self):
        helper = TestComposeAnnoBoxes()
        for label, model, w, bb in self._corpus():
            for n_steps in (0, 1, 4):
                helper._assert_match(model, n_steps, bb, w, label=label)

    def test_box_structure_contract(self):
        """The per-side box structure 4c consumes: the right dim ladder is
        always emitted at the estimated depth; bore bands come as one
        equal-depth left/right pair iff the part has annotatable holes; the
        plan halo appears iff the plan view will balloon. (_footprint_from_boxes
        folding these back to the StripDepths estimate is covered above.)"""
        from draftwright.compose import (
            _compose_anno_boxes,
            _est_right_strip_depth,
            _will_balloon,
        )

        for label, model, w, _bb in self._corpus():
            for n_steps in (0, 2):
                boxes = _compose_anno_boxes(model, n_steps, bore_callout_width=w)
                rights = [b.depth for b in boxes if b.side == "right"]
                lefts = [b.depth for b in boxes if b.side == "left"]
                halos = [b for b in boxes if b.side == "plan_halo"]

                # The right dim ladder is always present, at the estimated depth.
                assert _est_right_strip_depth(n_steps) in rights, (label, n_steps)

                if w > 0:
                    # Bore bands are emitted as a single equal-depth left/right
                    # pair — the symmetry _measure_strips' max() collapses.
                    assert len(lefts) == 1, (label, n_steps)
                    assert lefts[0] in rights, (label, n_steps)
                else:
                    assert lefts == [], (label, n_steps)

                # The halo band is emitted exactly when the part will balloon.
                assert bool(halos) == _will_balloon(model), (label, n_steps)


class TestComposeViewBlocks:
    """#112: estimated view footprints are explicit ViewBlocks."""

    def test_fallback_blocks_without_measured_strips(self):
        from draftwright._core import _DIM_PAD
        from draftwright.compose import (
            _compose_view_blocks,
            _est_pv_below_depth,
            _est_right_strip_depth,
        )

        blocks = _compose_view_blocks(60.0, 40.0, 20.0, 2.0, None, n_steps=3)
        assert set(blocks) == {"front", "plan", "side"}
        assert blocks["front"].hw == pytest.approx(60.0)
        assert blocks["front"].hh == pytest.approx(20.0)
        assert blocks["front"].right == pytest.approx(_est_right_strip_depth(3))
        assert blocks["front"].left == pytest.approx(_DIM_PAD)
        assert blocks["front"].top == pytest.approx(_DIM_PAD - _est_pv_below_depth())
        assert blocks["plan"].bottom == pytest.approx(_est_pv_below_depth())
        assert blocks["side"].right == pytest.approx(_DIM_PAD)

    def test_ballooned_plan_halo_is_part_of_plan_block(self):
        from draftwright._core import _DIM_PAD
        from draftwright.compose import StripDepths, _compose_view_blocks, _est_pv_below_depth

        strips = StripDepths(right=10.0, left=12.0, top=20.0, pv_halo=30.0)
        blocks = _compose_view_blocks(60.0, 40.0, 20.0, 1.0, strips)

        # The shared column corridors must hold the plan halo, even though the
        # scalar right/left strip estimates are smaller.
        assert blocks["front"].right == pytest.approx(30.0)
        assert blocks["front"].left == pytest.approx(30.0)
        assert blocks["plan"].right == pytest.approx(30.0)
        assert blocks["plan"].left == pytest.approx(30.0)
        assert blocks["plan"].top == pytest.approx(max(_DIM_PAD, strips.top) + strips.pv_halo)
        assert blocks["plan"].bottom == pytest.approx(max(_est_pv_below_depth(), strips.pv_halo))

    def test_section_layout_reserves_side_right_band(self):
        from draftwright._core import _DIM_PAD
        from draftwright.compose import StripDepths, _compose_view_blocks

        strips = StripDepths(right=42.0, left=10.0)
        without_section = _compose_view_blocks(60.0, 40.0, 20.0, 1.0, strips, section=False)
        with_section = _compose_view_blocks(60.0, 40.0, 20.0, 1.0, strips, section=True)

        assert without_section["side"].right == pytest.approx(_DIM_PAD)
        assert with_section["side"].right == pytest.approx(strips.right)

    def test_layout_geometry_consumes_composed_blocks(self, monkeypatch):
        import draftwright.compose as compose

        calls = []
        original = compose._compose_view_blocks

        def wrapped(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(compose, "_compose_view_blocks", wrapped)
        strips = compose.StripDepths(right=42.0, left=10.0)
        g = compose._layout_geometry(
            60.0,
            40.0,
            20.0,
            1.0,
            420.0,
            297.0,
            150.0,
            strips,
            n_steps=2,
            section=True,
        )

        assert g.fits
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert args[:6] == (60.0, 40.0, 20.0, 1.0, strips, 2)
        assert kwargs == {"section": True}

    def test_estimator_vertical_stack_is_centred_from_composed_blocks(self):
        from draftwright._core import _MARGIN
        from draftwright.compose import _compose_view_blocks, _layout_geometry

        page_h = 297.0
        blocks = _compose_view_blocks(60.0, 40.0, 20.0, 1.0, None, n_steps=3)
        g = _layout_geometry(
            60.0,
            40.0,
            20.0,
            1.0,
            420.0,
            page_h,
            150.0,
            None,
            n_steps=3,
            warn_no_iso=False,
        )

        fv, pv = blocks["front"], blocks["plan"]
        block_stack_h = fv.bottom + 2 * fv.hh + fv.top + pv.bottom + 2 * pv.hh + pv.top
        expected_y_offset = max(0.0, (page_h - 2 * _MARGIN - block_stack_h) / 2)
        actual_y_offset = g.FV_Y - _MARGIN - fv.bottom - fv.hh

        assert actual_y_offset == pytest.approx(expected_y_offset)

    def test_estimator_vertical_stack_centres_ballooned_plan_block(self):
        from draftwright._core import _MARGIN
        from draftwright.compose import StripDepths, _compose_view_blocks, _layout_geometry

        page_h = 297.0
        strips = StripDepths(right=10.0, left=12.0, top=20.0, pv_halo=30.0)
        blocks = _compose_view_blocks(60.0, 40.0, 20.0, 1.0, strips, n_steps=3)
        g = _layout_geometry(
            60.0,
            40.0,
            20.0,
            1.0,
            420.0,
            page_h,
            150.0,
            strips,
            n_steps=3,
            warn_no_iso=False,
        )

        fv, pv = blocks["front"], blocks["plan"]
        block_stack_h = fv.bottom + 2 * fv.hh + fv.top + pv.bottom + 2 * pv.hh + pv.top
        expected_y_offset = max(0.0, (page_h - 2 * _MARGIN - block_stack_h) / 2)
        actual_y_offset = g.FV_Y - _MARGIN - fv.bottom - fv.hh

        assert pv.bottom > 20.0
        assert actual_y_offset == pytest.approx(expected_y_offset)


# ---------------------------------------------------------------------------
# Phase 3 (#118): dynamic FV→SV corridor
# ---------------------------------------------------------------------------


class TestDynamicCorridors:
    """Phase 3 (#118): SV_X and _fits() use the depth estimator for the FV→SV gap."""

    def test_fits_widens_required_space_for_stepped_part(self):
        # x=5, y=90, z=100 at 1:1 on A3 (420×297, tb=150):
        #   n_steps=0 (gap_fv_sv=20): fits.
        #   n_steps=3 (gap_fv_sv=69.5): auto_fits rejects the conservative
        #     composed row, using the same verdict later used by auto repack (#519).
        from draftwright.compose import _fits

        assert _fits(5.0, 90.0, 100.0, 1.0, 420.0, 297.0, 150.0, n_steps=0)
        assert not _fits(5.0, 90.0, 100.0, 1.0, 420.0, 297.0, 150.0, n_steps=3)

    def test_fits_zero_steps_same_as_default(self):
        # n_steps=0 must produce the same result as the old signature (no kwarg).
        from draftwright.compose import _fits

        page_w, page_h, tb = 297.0, 210.0, 120.0
        scale, x_size, y_size, z_size = 1.0, 20.0, 20.0, 20.0
        assert _fits(x_size, y_size, z_size, scale, page_w, page_h, tb, n_steps=0) == _fits(
            x_size, y_size, z_size, scale, page_w, page_h, tb
        )

    def test_gap_fv_sv_equals_dim_pad_for_flat_part(self):
        # A plain box (no step faces) → sv_left - fv_right == _DIM_PAD.
        from build123d import Box

        from draftwright import build_drawing
        from draftwright._core import _DIM_PAD

        a = build_drawing(Box(60, 40, 20))._analysis
        assert len(a.step_zs) == 0
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        assert sv_left - fv_right == pytest.approx(_DIM_PAD, abs=0.1)

    def test_choose_scale_picks_larger_page_for_deep_step_corridor(self):
        # With n_steps=0, x=5 y=90 z=100 fits A3 at 1:1 (420 mm wide).
        # With n_steps=3, gap_fv_sv jumps to 69.5 mm — the shared auto_fits
        # verdict rejects A3 and choose_scale returns A2.
        from draftwright.compose import choose_scale

        _, page_w_flat, _, _ = choose_scale(5.0, 90.0, 100.0, n_steps=0)
        _, page_w_deep, _, _ = choose_scale(5.0, 90.0, 100.0, n_steps=3)
        assert page_w_deep > page_w_flat, (
            "n_steps=3 corridor must force a larger page than n_steps=0"
        )

    def test_gap_fv_sv_widens_for_stepped_part(self):
        # A part with one step ≥20 mm tall (so dim_step is actually placed) gets
        # gap = _est_right_strip_depth(1) = 36 mm.  The ≥20 mm gate matches what
        # _auto_annotate applies — bore floors or shallow faces don't count.
        from build123d import Box, Pos

        from draftwright import build_drawing
        from draftwright.compose import _est_right_strip_depth

        # Box(60, 40, 50): Z -25..+25.  Carve top-right quadrant so the step
        # floor is at Z=0, giving a 25 mm step height (≥20 mm threshold).
        cutout = Pos(15, 0, 12.5) * Box(30, 40, 25)
        part = Box(60, 40, 50) - cutout

        a = build_drawing(part)._analysis
        assert len(a.step_zs) >= 1, "expected at least one step face"
        # The 25 mm step height passes the dim_step ≥20 mm gate → n_steps=1 → gap=36 mm
        expected_gap = _est_right_strip_depth(1)
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        assert sv_left - fv_right == pytest.approx(expected_gap, abs=0.1)


# ---------------------------------------------------------------------------
# Two-pass layout (#131): bore callout width drives gap_fv_sv
# ---------------------------------------------------------------------------


class TestTwoPassLayout:
    """Two-pass layout (#131): bore callout widths widen the FV→SV corridor."""

    def test_bore_callout_widens_gap_fv_sv(self):
        # A part with many small holes generates wide callout labels (e.g.
        # "4× ⌀15.9 THRU") that need more than _DIM_PAD right of the plan view.
        # The two-pass layout must size gap_fv_sv >= bore callout depth.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing
        from draftwright._core import _DIM_PAD

        # Four identical cylinders → "4× ⌀16 THRU" callout with a count prefix
        part = (
            Box(100, 80, 20)
            - Pos(30, 25, 0) * Cylinder(16, 20)
            - Pos(-30, 25, 0) * Cylinder(16, 20)
            - Pos(30, -25, 0) * Cylinder(16, 20)
            - Pos(-30, -25, 0) * Cylinder(16, 20)
        )
        dwg = build_drawing(part)
        a = dwg._analysis
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        actual_gap = sv_left - fv_right

        _, bore_depth = _sizing_model(part)
        # bore callout width must exceed DIM_PAD for the test to be meaningful
        assert bore_depth > _DIM_PAD, (
            f"bore callout width {bore_depth:.1f} mm must exceed _DIM_PAD={_DIM_PAD} mm"
        )
        assert actual_gap >= bore_depth - 0.1, (
            f"gap_fv_sv={actual_gap:.1f} mm must be >= bore_depth={bore_depth:.1f} mm"
        )

    def test_plain_box_gap_unchanged(self):
        # A box with no holes: bore callout depth = 0 → gap_fv_sv stays _DIM_PAD.
        from build123d import Box

        from draftwright import build_drawing
        from draftwright._core import _DIM_PAD

        a = build_drawing(Box(60, 40, 20))._analysis
        sv_left = a.SV_X - a.sv_hw
        fv_right = a.FV_X + a.fv_hw
        assert sv_left - fv_right == pytest.approx(_DIM_PAD, abs=0.1)

    def test_bore_callout_fits_within_gap(self):
        # Verify actual callout label does not reach sv_left.
        # The Leader label_bbox right edge must stay left of sv_left.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        part = (
            Box(100, 80, 20)
            - Pos(30, 25, 0) * Cylinder(16, 20)
            - Pos(-30, 25, 0) * Cylinder(16, 20)
            - Pos(30, -25, 0) * Cylinder(16, 20)
            - Pos(-30, -25, 0) * Cylinder(16, 20)
        )
        dwg = build_drawing(part)
        a = dwg._analysis
        sv_left = a.SV_X - a.sv_hw
        for name, ann in dwg._named.items():
            if name.startswith("hc_plan") and getattr(ann, "label_bbox", None):
                lx1 = ann.label_bbox[2]  # right edge of callout label
                assert lx1 <= sv_left + 0.5, (
                    f"{name}: label right edge {lx1:.1f} mm exceeds sv_left {sv_left:.1f} mm"
                )

    def test_bolt_circle_suffix_widens_estimate(self):
        # BoltCircle callouts carry the "EQ SP ON ø… BC" suffix (~34 mm wide); the
        # planner callout-width estimate must include it, so a bolt-circle part is wider
        # than the same ⌀8 bore alone (#584 WP1 A — via _est_planned_bore_callout_width).
        from build123d import Box, Cylinder, Pos

        # Six ⌀8 holes at equal 60° spacing on R=35 → BoltCircle pattern
        part = (
            Box(100, 100, 20)
            - Pos(35.0, 0.0, 0) * Cylinder(8, 20)
            - Pos(17.5, 30.31, 0) * Cylinder(8, 20)
            - Pos(-17.5, 30.31, 0) * Cylinder(8, 20)
            - Pos(-35.0, 0.0, 0) * Cylinder(8, 20)
            - Pos(-17.5, -30.31, 0) * Cylinder(8, 20)
            - Pos(17.5, -30.31, 0) * Cylinder(8, 20)
        )
        plain = Box(100, 100, 20) - Pos(0, 0, 0) * Cylinder(8, 20)  # one ⌀16 bore, no BC suffix

        _, width_with = _sizing_model(part)
        _, width_without = _sizing_model(plain)
        assert width_with > width_without, (
            f"BoltCircle suffix should widen estimate: {width_without:.1f} → {width_with:.1f} mm"
        )

    def test_pv_below_strip_has_slack(self):
        # pv_zones.below outer_limit = fv_top_edge (not fv_top_edge + 2), giving
        # 18 mm available vs 16 mm needed for dim_width — no razor-fit (#130).
        from build123d import Box

        from draftwright import build_drawing
        from draftwright.compose import _est_pv_below_depth

        part = Box(80, 40, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        available = a.pv_zones.below.anchor - a.pv_zones.below.outer_limit
        needed = _est_pv_below_depth()
        assert available > needed, (
            f"pv_zones.below available {available:.1f} mm must exceed needed {needed:.1f} mm"
        )
        assert "m_env_width" in dwg._named, "width dim must not be skipped"


class TestComposeThenPackRepack:
    """Ownership-based compose-then-pack + measure-and-repack (#121, ADR 0004):
    the cross-view collision detector, the disjoint block packing, the candidate
    floor, and the annotation-ownership map lifecycle.  Pure/fast — no OCP."""

    # --- cross-view collision detector -----------------------------------

    @staticmethod
    def _label(bb):
        from types import SimpleNamespace

        return SimpleNamespace(label_bbox=bb)

    @staticmethod
    def _line(bb):
        # Bare geometry: no label_bbox attribute, bbox via bounding_box().
        from types import SimpleNamespace

        class _Bare:
            def bounding_box(self):
                return SimpleNamespace(
                    min=SimpleNamespace(X=bb[0], Y=bb[1]),
                    max=SimpleNamespace(X=bb[2], Y=bb[3]),
                )

        return _Bare()

    def _fake_dwg(self, named, views):
        from types import SimpleNamespace

        # Mirror Drawing's annotation read surface (#249) so stage helpers that now
        # call dwg.iter_annotations()/view_of() work against the fake.
        return SimpleNamespace(
            _named=named,
            _anno_view=views,
            iter_annotations=lambda: named.items(),
            view_of=lambda n: views.get(n),
            annotations_in_view=lambda v: ((n, o) for n, o in named.items() if views.get(n) == v),
        )

    def test_overlap_counts_label_vs_label_across_views(self):
        from draftwright.builder import _cross_view_overlaps

        dwg = self._fake_dwg(
            {"a": self._label((0, 0, 10, 10)), "b": self._label((5, 5, 15, 15))},
            {"a": "front", "b": "plan"},
        )
        assert _cross_view_overlaps(dwg, None) == 1

    def test_overlap_counts_label_vs_line_across_views(self):
        # The literal #121 case: a plan balloon (bare geometry) over a front-view
        # dimension (label) — counted because at least one side is a label.
        from draftwright.builder import _cross_view_overlaps

        dwg = self._fake_dwg(
            {"dim": self._label((0, 0, 10, 10)), "balloon": self._line((5, 5, 15, 15))},
            {"dim": "front", "balloon": "plan"},
        )
        assert _cross_view_overlaps(dwg, None) == 1

    def test_overlap_ignores_same_view(self):
        from draftwright.builder import _cross_view_overlaps

        dwg = self._fake_dwg(
            {"a": self._label((0, 0, 10, 10)), "b": self._label((5, 5, 15, 15))},
            {"a": "front", "b": "front"},
        )
        assert _cross_view_overlaps(dwg, None) == 0

    def test_overlap_ignores_line_vs_line(self):
        # Two bare lines crossing between views is normal drafting, not a clash.
        from draftwright.builder import _cross_view_overlaps

        dwg = self._fake_dwg(
            {"a": self._line((0, 0, 10, 10)), "b": self._line((5, 5, 15, 15))},
            {"a": "front", "b": "side"},
        )
        assert _cross_view_overlaps(dwg, None) == 0

    def test_overlap_ignores_untagged_furniture(self):
        # An annotation with no ortho-view tag (iso/section/detail/title) is
        # invisible to the detector, even when it overlaps a tagged one.
        from draftwright.builder import _cross_view_overlaps

        dwg = self._fake_dwg(
            {"dim": self._label((0, 0, 10, 10)), "note": self._label((5, 5, 15, 15))},
            {"dim": "front", "note": "iso"},
        )
        assert _cross_view_overlaps(dwg, None) == 0

    # --- annotation-over-view-linework trigger (#293) ---------------------

    def test_annotation_view_overlap_counts_label_over_other_view(self):
        # The third repack trigger: a view-owned LABEL grown into a *different*
        # view's geometry box (the staggered step chain bumping the plan view).
        # A bare line over another view is normal drafting; a label in its OWN
        # view is fine. Only a label over another view's box counts.
        from types import SimpleNamespace

        from draftwright.builder import _annotation_view_overlaps

        a = SimpleNamespace(
            FV_X=0.0,
            FV_Y=0.0,
            fv_hw=10.0,
            fv_hh=10.0,
            PV_X=0.0,
            PV_Y=40.0,
            pv_hh=10.0,
            SV_X=40.0,
            SV_Y=0.0,
            sv_hw=10.0,
        )  # plan box spans x[-10,10] y[30,50]
        over = self._fake_dwg({"d": self._label((-5, 32, 5, 42))}, {"d": "front"})
        assert _annotation_view_overlaps(over, a) == 1  # front label inside plan box
        bare = self._fake_dwg({"d": self._line((-5, 32, 5, 42))}, {"d": "front"})
        assert _annotation_view_overlaps(bare, a) == 0  # bare line — normal drafting
        own = self._fake_dwg({"d": self._label((-5, -5, 5, 5))}, {"d": "front"})
        assert _annotation_view_overlaps(own, a) == 0  # inside its own view

    # --- out-of-bounds escalation trigger (#92) ---------------------------

    def test_out_of_bounds_trigger(self):
        # The second repack trigger: a view-owned annotation past the drawable
        # (e.g. a ballooned plan view overflowing the page top) escalates even
        # without a cross-view overlap. Untagged overflow is ignored — a repack
        # can only move view-owned annotations.
        from types import SimpleNamespace

        from draftwright.builder import _annotations_out_of_bounds

        a = SimpleNamespace(margin=10.0, PAGE_W=200.0, PAGE_H=100.0)
        inb = self._fake_dwg({"d": self._line((20, 20, 40, 40))}, {"d": "plan"})
        assert not _annotations_out_of_bounds(inb, a)
        over = self._fake_dwg({"d": self._line((20, 20, 40, 120))}, {"d": "plan"})
        assert _annotations_out_of_bounds(over, a)
        untagged = self._fake_dwg({"d": self._line((20, 20, 40, 120))}, {"d": "iso"})
        assert not _annotations_out_of_bounds(untagged, a)

    # --- disjoint block packing ------------------------------------------

    def test_repacked_blocks_are_disjoint(self):
        from draftwright.compose import ViewBlock, _layout_geometry

        blocks = {
            "front": ViewBlock(10, 10, top=12, right=12, bottom=12, left=12),
            "plan": ViewBlock(10, 10, top=12, right=12, bottom=12, left=12),
            "side": ViewBlock(10, 10, top=12, right=12, bottom=12, left=12),
        }
        g = _layout_geometry(20, 20, 20, 1.0, 841.0, 594.0, 150.0, None, blocks=blocks)
        # FV and PV share X and stack vertically — PV's bottom must clear FV's top.
        assert (g.PV_Y - g.pv_hh) > (g.FV_Y + g.fv_hh)
        # SV abuts the column to the right — its left edge must clear FV's right.
        assert (g.SV_X - g.sv_hw) > (g.FV_X + g.fv_hw)

    def test_left_corridor_uses_shared_band_not_front_only(self):
        # The MAJOR fix: when the plan view's measured left band is the deeper of
        # the two, the FV/PV column must clear it (col_left), or PV slides off the
        # left margin.  front.left tiny, plan.left huge.
        #
        # The page is sized so the content FILLS it (x_offset == 0): on a wide
        # sheet the centring slack would absorb the mis-anchoring and the buggy
        # code (FV_X anchored on fv.left) would pass anyway.  With x_offset == 0
        # the bug puts the plan-view left edge at margin - 120 = -110 mm.
        from draftwright._core import _MARGIN
        from draftwright.compose import ViewBlock, _layout_geometry

        blocks = {
            "front": ViewBlock(10, 10, left=0.0, right=8, top=8, bottom=8),
            "plan": ViewBlock(10, 10, left=120.0, right=8, top=8, bottom=8),
            "side": ViewBlock(10, 10, left=0.0, right=8, top=8, bottom=8),
        }
        g = _layout_geometry(20, 20, 20, 1.0, 380.0, 300.0, 150.0, None, blocks=blocks)
        # Precondition: no centring slack, or the test cannot catch the bug.
        assert g.x_offset == pytest.approx(0.0, abs=0.01), (
            f"test needs x_offset==0 to be meaningful, got {g.x_offset:.1f}"
        )
        pv_left_footprint_edge = g.PV_X - g.fv_hw - 120.0
        assert pv_left_footprint_edge >= _MARGIN - 0.5, (
            f"plan-view left footprint edge {pv_left_footprint_edge:.1f} slid past "
            f"the {_MARGIN} mm margin — column anchored on front.left, not col_left"
        )

    # --- candidate ladder -------------------------------------------------

    def test_repack_candidates_use_the_full_auto_ladder(self):
        from types import SimpleNamespace

        from draftwright._core import _LADDER
        from draftwright.builder import _repack_candidates

        a = SimpleNamespace(SCALE=0.2, PAGE_W=594.0, PAGE_H=420.0)
        cands = _repack_candidates(a, None, None)
        # #519: repack and choose_scale now share one composed-footprint fit, so
        # repack no longer needs a pass-1 floor to defend against a looser model.
        assert cands == list(_LADDER)
        assert (0.2, 1189.0, 841.0, 150.0) in cands

    def test_auto_repack_fitness_uses_pass1_step_reservations(self):
        from draftwright.compose import ViewBlock, _layout_geometry

        x_size, y_size, z_size = 5.0, 90.0, 100.0
        scale, page_w, page_h, tb_w = 1.0, 420.0, 297.0, 150.0
        blocks = {
            "front": ViewBlock(x_size * scale / 2, z_size * scale / 2),
            "plan": ViewBlock(x_size * scale / 2, y_size * scale / 2),
            "side": ViewBlock(y_size * scale / 2, z_size * scale / 2),
        }

        assert _layout_geometry(
            x_size,
            y_size,
            z_size,
            scale,
            page_w,
            page_h,
            tb_w,
            None,
            0,
            blocks=blocks,
            warn_no_iso=False,
        ).auto_fits
        assert not _layout_geometry(
            x_size,
            y_size,
            z_size,
            scale,
            page_w,
            page_h,
            tb_w,
            None,
            3,
            blocks=blocks,
            warn_no_iso=False,
        ).auto_fits

    def test_section_reservation_sits_beyond_side_right_corridor(self):
        from draftwright.compose import _layout_geometry

        strips = StripDepths(right=42.0, left=10.0)
        g = _layout_geometry(40, 10, 20, 1.0, 420.0, 297.0, 150.0, strips, section=True)
        section_hw = max(g.fv_hw, 12.0)
        side_right = g.SV_X + g.sv_hw
        assert g.SECTION_X - section_hw == pytest.approx(side_right + strips.right + 10.0)

    def test_repack_candidates_honour_fixed_scale_and_page(self):
        from types import SimpleNamespace

        from draftwright.builder import _repack_candidates

        a = SimpleNamespace(SCALE=1.0, PAGE_W=297.0, PAGE_H=210.0)
        cands = _repack_candidates(a, 2.0, "A3")
        assert len(cands) == 1 and cands[0][0] == 2.0

    def test_repack_to_fixed_point_iterates_until_stable(self, monkeypatch):
        from types import SimpleNamespace

        import draftwright.builder as builder

        a0, d0 = SimpleNamespace(pass_id=0), SimpleNamespace(pass_id=0)
        a1, d1 = SimpleNamespace(pass_id=1), SimpleNamespace(pass_id=1)
        a2, d2 = SimpleNamespace(pass_id=2), SimpleNamespace(pass_id=2)
        returns = [(a1, d1), (a2, d2), None]
        calls = []

        def fake_repack(a, dwg, *args, **kwargs):
            calls.append((a.pass_id, dwg.pass_id))
            return returns.pop(0)

        monkeypatch.setattr(builder, "_repack", fake_repack)
        monkeypatch.setattr(builder, "_needs_repack", lambda dwg, a: False)

        out = builder._repack_to_fixed_point(a0, d0, "out", None, False)

        assert out == (a2, d2)
        assert calls == [(0, 0), (1, 1), (2, 2)]

    def test_repack_to_fixed_point_warns_at_iteration_limit(self, monkeypatch, caplog):
        from types import SimpleNamespace

        import draftwright.builder as builder

        calls = []

        def fake_repack(a, dwg, *args, **kwargs):
            calls.append((a.pass_id, dwg.pass_id))
            pass_id = len(calls)
            return SimpleNamespace(pass_id=pass_id), SimpleNamespace(pass_id=pass_id)

        monkeypatch.setattr(builder, "_repack", fake_repack)
        monkeypatch.setattr(builder, "_needs_repack", lambda dwg, a: True)

        with caplog.at_level(logging.WARNING):
            out_a, out_dwg = builder._repack_to_fixed_point(
                SimpleNamespace(pass_id=0),
                SimpleNamespace(pass_id=0),
                "out",
                None,
                False,
            )

        assert len(calls) == builder._REPACK_MAX_ITER
        assert out_a.pass_id == out_dwg.pass_id == builder._REPACK_MAX_ITER
        assert "reached iteration limit" in caplog.text

    def test_repack_to_fixed_point_warns_on_stalled_trigger(self, monkeypatch, caplog):
        from types import SimpleNamespace

        import draftwright.builder as builder

        monkeypatch.setattr(builder, "_repack", lambda *args, **kwargs: None)
        monkeypatch.setattr(builder, "_needs_repack", lambda dwg, a: True)

        with caplog.at_level(logging.WARNING):
            out = builder._repack_to_fixed_point(
                SimpleNamespace(pass_id=0),
                SimpleNamespace(pass_id=0),
                "out",
                None,
                False,
            )

        assert out is None
        assert "stalled after 0 iteration" in caplog.text

    @pytest.mark.timeout(120)
    def test_repack_honours_pinned_scale_on_oversized_part(self):
        # #350 review: when no candidate fits the measured layout, the repack backstop
        # bisects for a fitting scale ONLY when the scale is not pinned. A user-pinned
        # scale must be honoured (overflow accepted, as asked) — not silently reduced —
        # and the backstop must never crash on the degenerate no-positive-scale case.
        dwg = build_drawing(Box(4200, 1600, 5400), scale=1)
        assert dwg.scale == 1.0  # pin honoured, not silently rescaled

    @pytest.mark.timeout(120)
    @_skip_011
    def test_repack_reduces_an_oversized_part_when_scale_is_free(self):
        # The complement: with the scale free, an oversized part is reduced to a scale
        # that fits rather than overflowing (#350) — through the full pass-1 + repack.
        dwg = build_drawing(Box(4200, 1600, 5400))
        assert dwg.scale < 0.2  # a deeper ISO 5455 reduction than the old A0 1:5 floor
        assert not any(i.code.endswith("out_of_bounds") for i in dwg.lint())

    # --- ownership-map lifecycle -----------------------------------------

    @pytest.mark.timeout(60)
    def test_anno_view_lifecycle(self):
        # add(view=) records; re-add view-less clears the stale tag; remove pops;
        # clear_annotations prunes — the map never lags _named (#121).
        dwg = build_drawing(Box(30, 20, 10))

        def _leader(label):
            return Leader(
                tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label=label, draft=dwg.draft
            )

        dwg.add(_leader("A"), "tag", view="front")
        assert dwg._anno_view["tag"] == "front"

        dwg.add(_leader("B"), "tag")  # replacement, view-less → clears stale tag
        assert "tag" not in dwg._anno_view

        dwg.add(_leader("C"), "tag2", view="plan")
        dwg.remove("tag2")
        assert "tag2" not in dwg._anno_view

        dwg.add(_leader("D"), "tag3", view="side")
        dwg.clear_annotations()  # keeps title_block only
        assert "tag3" not in dwg._anno_view


class TestLayoutCleanlinessInvariant:
    """End-to-end invariant (#293): for a spread of representative part shapes the
    *finished* drawing must place its views and annotations without layout
    collisions — the OUTCOME, not just the trigger mechanics the unit tests above
    cover. This is the check that would have caught the GRM-03 staggered chain
    bumping the plan view: the trigger unit tests were green while a real part
    rendered with overlapping annotations. A regression here means the layout
    engine (estimate → measure-and-repack) let a real collision through."""

    # Genuine layout defects — NOT view_annotation_inside_extents, the soft info
    # code for a callout legitimately sitting inside a large face.
    _DEFECTS = {
        "view_annotation_overlap",
        "view_overlap",
        "view_out_of_bounds",
        "annotation_out_of_bounds",
        "annotation_overlap",
    }

    @staticmethod
    def _x_simple():
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        s = Cylinder(8, 20, align=(Align.CENTER, Align.CENTER, b)) + Pos(0, 0, 20) * Cylinder(
            5, 20, align=(Align.CENTER, Align.CENTER, b)
        )
        return Rotation(0, 90, 0) * s  # roomy X-turned chain → one tier, no zig-zag

    @staticmethod
    def _x_crowded():
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN  # GRM-03 shape: fine head steps + long shaft → stagger + view lift
        s = None
        z = 0.0
        for d, ln in [(8, 1.0), (12, 1.0), (8, 1.0), (12, 1.0), (6, 30.0)]:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            s = seg if s is None else s + seg
            z += ln
        return Rotation(0, 90, 0) * s

    @staticmethod
    def _z_stepped():
        from build123d import Cylinder, Pos

        return Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)  # Z-turned ladder

    @staticmethod
    def _prism_holes():
        part = Box(80, 60, 20)  # a row of holes → location dims above the plan view
        for x in (-30, -10, 10, 30):
            part -= Pos(x, 20, 0) * Cylinder(3, 30)
        return part

    @staticmethod
    def _bolt_circle():
        import math

        part = Box(60, 60, 15)  # 6-hole bolt circle → ballooned plan-view halo
        for i in range(6):
            a = i * math.pi / 3
            part -= Pos(20 * math.cos(a), 20 * math.sin(a), 0) * Cylinder(2.5, 30)
        return part

    @staticmethod
    def _counterbored():
        part = Box(60, 40, 20)  # counterbore → full section A-A
        part -= Cylinder(4, 30)
        part -= Pos(0, 0, 2) * Cylinder(7, 20)
        return part

    @pytest.mark.parametrize(
        "factory",
        ["_x_simple", "_x_crowded", "_z_stepped", "_prism_holes", "_bolt_circle", "_counterbored"],
    )
    def test_finished_sheet_has_no_layout_collisions(self, factory):
        dwg = build_drawing(getattr(self, factory)())
        hits = sorted({i.code for i in dwg.lint()} & self._DEFECTS)
        assert not hits, f"{factory}: layout defects in finished drawing: {hits}"


class TestHolePatternCallouts:
    """Grouped pattern callouts from the helpers v0.12.0 recognition (RectGrid +
    sub-clustered LinearArrays): a recognised set collapses to one ``n× ⌀``
    callout plus its pattern dimensions, never per-hole balloons/table (#92,
    #111). Coverage lint stays quiet because the grouped callout carries the
    full diameter count."""

    @staticmethod
    def _grid_part():
        # 2 rows × 4 cols of ⌀8 through-holes; 20 mm pitch one way, 25 mm the
        # other → a single RectGrid(2×4).
        part = Box(140, 70, 12)
        for r in range(2):
            for c in range(4):
                part -= Pos(-37.5 + c * 25, -10 + r * 20, 0) * Cylinder(4, 12)
        return part

    @staticmethod
    def _perimeter_part():
        # Rectangular perimeter of ⌀6 holes — five along the top and bottom
        # edges (recognised as two LinearArrays), the rest unpatterned.
        part = Box(140, 100, 12)
        pos = set()
        for x in (-50, -25, 0, 25, 50):
            pos.add((x, -35))
            pos.add((x, 35))
        for y in (-35, 0, 35):
            pos.add((-50, y))
            pos.add((50, y))
        for x, y in pos:
            part -= Pos(x, y, 0) * Cylinder(3, 12)
        return part

    @pytest.mark.timeout(120)
    def test_rect_grid_one_callout_and_two_pitch_dims(self):
        dwg = build_drawing(self._grid_part())
        named = dwg._named
        hc = [n for n in named if n.startswith("hc_")]
        pitch = [n for n in named if n.startswith("dim_pitch_")]
        # one grouped callout covering all eight holes — not eight callouts
        assert len(hc) == 1, f"expected one grouped callout, got {hc}"
        assert named[hc[0]].covers_count == 8
        assert named[hc[0]].covers_diameters == (8.0,)
        # both grid pitch dimensions, labelled (n-1)× pitch
        assert len(pitch) == 2, f"expected two pitch dims, got {pitch}"
        assert {named[n].label for n in pitch} == {"1× 20", "3× 25"}
        # each dim runs ALONG one lattice axis — its endpoints share a coordinate
        # — not diagonally across the grid; and the two are perpendicular.
        axes = set()
        for n in pitch:
            sp = named[n]._dw_spec
            dx, dy = abs(sp.p1[0] - sp.p2[0]), abs(sp.p1[1] - sp.p2[1])
            assert dx < 0.5 or dy < 0.5, f"{n} drawn diagonally: p1={sp.p1} p2={sp.p2}"
            axes.add("vertical" if dx < 0.5 else "horizontal")
        assert axes == {"vertical", "horizontal"}, f"grid dims not perpendicular: {axes}"
        # the grouped callout replaces — never coexists with — per-hole furniture
        assert not [n for n in named if n.startswith("balloon")]
        assert not [n for n in named if "table" in n]

    @pytest.mark.timeout(120)
    def test_rect_grid_pitch_dims_not_diagonal_when_rotated(self):
        # Regression guard for the high-aspect ROTATED grid: each pitch dim must
        # measure along one lattice edge (endpoint span == label span), not
        # corner-to-corner. A 2×5 grid (10 × 45 pitch) rotated 25° — the short-
        # axis dim spans 10 mm; the diagonal bug would make it ~180 mm.
        ang = math.radians(25)
        ca, sa = math.cos(ang), math.sin(ang)
        part = Box(220, 120, 12)
        for r in range(2):
            for c in range(5):
                x, y = (c - 2) * 45, (r - 0.5) * 10
                part -= Pos(x * ca - y * sa, x * sa + y * ca, 0) * Cylinder(4, 12)
        dwg = build_drawing(part)
        scale = dwg._analysis.SCALE
        pitch = [n for n in dwg._named if n.startswith("dim_pitch_")]
        assert len(pitch) == 2, f"expected two grid pitch dims, got {pitch}"
        for n in pitch:
            dim = dwg._named[n]
            sp = dim._dw_spec
            span = math.hypot(sp.p2[0] - sp.p1[0], sp.p2[1] - sp.p1[1]) / scale
            k, p = dim.label.split("× ")
            expected = int(k) * float(p)
            assert abs(span - expected) < 1.0, (
                f"{n} ({dim.label!r}) endpoint span {span:.1f} ≠ {expected:.1f} — drawn diagonally"
            )

    @pytest.mark.timeout(120)
    def test_rect_grid_coverage_lint_quiet(self):
        codes = {i.code for i in build_drawing(self._grid_part()).lint()}
        assert "feature_not_dimensioned" not in codes
        assert "feature_count_mismatch" not in codes

    @pytest.mark.timeout(120)
    def test_perimeter_rows_dimensioned_not_per_hole(self):
        dwg = build_drawing(self._perimeter_part())
        named = dwg._named
        pitch = [n for n in named if n.startswith("dim_pitch_")]
        # each recognised edge row (5 holes, pitch 25) gets its own pitch dim
        assert len(pitch) >= 2, f"expected the edge rows dimensioned, got {pitch}"
        assert any(named[n].label == "4× 25" for n in pitch)
        # the rows are not exploded into a per-hole table / balloons
        assert not [n for n in named if n.startswith("balloon")]
        assert not [n for n in named if "table" in n]
        assert "feature_not_dimensioned" not in {i.code for i in dwg.lint()}


# ---------------------------------------------------------------------------
# Integration test — requires build123d + OCP (slow)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(120)
def test_make_drawing_box(tmp_path):
    """make_drawing() produces SVG and DXF for a simple box STEP file."""
    # Build a simple box and export to STEP
    box = Box(30, 20, 10)
    step_file = str(tmp_path / "box.step")
    export_step(box, step_file)

    out_stem = str(tmp_path / "box_drawing")
    svg_path, dxf_path = make_drawing(
        step_file,
        out=out_stem,
        title="TEST BOX",
        number="TST-001",
    )

    assert Path(svg_path).exists()
    assert Path(dxf_path).exists()
    assert Path(svg_path).stat().st_size > 1000
    assert Path(dxf_path).stat().st_size > 100

    # SVG should have the full page dimensions injected
    svg_content = Path(svg_path).read_text()
    assert 'mm"' in svg_content  # width/height in mm


@pytest.mark.timeout(120)
def test_make_drawing_cylinder_uses_centerline_and_holecallout(tmp_path):
    """make_drawing() adds Centerline and HoleCallout for cylindrical parts."""
    cyl = Cylinder(radius=15, height=40)
    step_file = str(tmp_path / "cyl.step")
    export_step(cyl, step_file)

    svg_path, _ = make_drawing(step_file, out=str(tmp_path / "cyl_drawing"), title="CYL")

    # The drawing must exist and be non-trivial
    assert Path(svg_path).exists()
    assert Path(svg_path).stat().st_size > 1000


@pytest.mark.timeout(120)
def test_make_drawing_default_title(tmp_path):
    """Title defaults to uppercased stem when not provided."""
    box = Box(10, 10, 10)
    step_file = str(tmp_path / "my_part.step")
    export_step(box, step_file)

    svg_path, _ = make_drawing(step_file, out=str(tmp_path / "out"))
    assert Path(svg_path).exists()


@pytest.mark.timeout(120)
def test_make_drawing_accepts_build123d_object(tmp_path):
    """make_drawing() draws an in-memory build123d Shape without a STEP file."""
    box = Box(30, 20, 10)
    out_stem = str(tmp_path / "box_obj")

    svg_path, dxf_path = make_drawing(box, out=out_stem, title="BOX OBJ")

    assert Path(svg_path).exists()
    assert Path(dxf_path).exists()
    assert Path(svg_path).stat().st_size > 1000


@pytest.mark.timeout(120)
def test_make_drawing_object_defaults_out_to_drawing(tmp_path, monkeypatch):
    """Passing an object with no out= writes to 'drawing.svg' in the cwd."""
    monkeypatch.chdir(tmp_path)
    box = Box(10, 10, 10)

    svg_path, dxf_path = make_drawing(box)

    assert Path(svg_path).name == "drawing.svg"
    assert Path(dxf_path).name == "drawing.dxf"
    assert (tmp_path / "drawing.svg").exists()


def test_generate_script_rejects_build123d_object():
    """generate_script() needs a path — a live object cannot be embedded."""
    box = Box(10, 10, 10)
    with pytest.raises(TypeError, match="STEP file path"):
        generate_script(box)


@pytest.mark.smoke
def test_make_drawing_module_entrypoint_runs_cli_help():
    """The compat facade remains executable as ``python -m draftwright.make_drawing``."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [
            sys.executable,
            "-W",
            "error::RuntimeWarning",
            "-m",
            "draftwright.make_drawing",
            "--help",
        ],
        capture_output=True,
        env=env,
        text=True,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "Usage:" in result.stdout  # Typer/rich help capitalises (was argparse "usage:")
    assert "step_file" in result.stdout
    # rich degrades its box-drawing to ASCII on a cp1252 stream, so help stays safe.
    assert result.stdout.isascii()


def test_cli_version_reports_installed_version():
    """``--version`` prints the installed distribution version (the PyPI version
    once pip-installed) and exits cleanly, without needing a STEP file."""
    from importlib.metadata import version as _pkg_version

    result = subprocess.run(
        [sys.executable, "-m", "draftwright.make_drawing", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.stdout.strip() == f"draftwright {_pkg_version('draftwright')}"


def test_cli_import_does_not_load_the_engine():
    """Importing the CLI must not pull in build123d/OCP (#313). Shell completion,
    --help and --version import this module on every invocation; loading the
    ~5 s CAD kernel there made each TAB press take ~6 s. Guard the lazy boundary:
    the engine is imported only on the actual build path, in a fresh process so
    other tests' imports can't mask a regression."""
    code = (
        "import sys, draftwright.cli; "
        "heavy = [m for m in ('build123d', 'OCP') if m in sys.modules]; "
        "print(','.join(heavy)); sys.exit(1 if heavy else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, (
        f"`import draftwright.cli` eagerly loaded: {result.stdout.strip()}"
    )


def test_lazy_public_api_preserves_make_drawing_identity():
    """The lazy package __init__ (#313) must still expose the public API, and
    `draftwright.make_drawing` must stay the FUNCTION even after the compat
    submodule of the same name is imported and would otherwise shadow it."""
    code = (
        "import types, draftwright as d; "
        "from draftwright import make_drawing, build_drawing, Drawing, choose_scale; "
        "assert callable(make_drawing) and not isinstance(make_drawing, types.ModuleType); "
        "assert d.make_drawing is make_drawing; "
        "import draftwright.make_drawing; "  # provoke the shadowing path
        "assert callable(d.make_drawing) and not isinstance(d.make_drawing, types.ModuleType); "
        "print('ok')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "ok", (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# --format selector (#288) — pure logic, no OCP build needed
# ---------------------------------------------------------------------------


class TestFormatSelector:
    def test_parse_default_is_pdf(self):
        from draftwright.cli import _parse_formats

        assert _parse_formats("pdf") == ["pdf"]

    def test_parse_comma_list_keeps_order_and_dedupes(self):
        from draftwright.cli import _parse_formats

        assert _parse_formats("dxf, pdf ,dxf") == ["dxf", "pdf"]

    def test_parse_all_expands_to_three(self):
        from draftwright.cli import _parse_formats

        assert _parse_formats("all") == ["pdf", "svg", "dxf"]

    def test_parse_unknown_format_raises(self):
        import typer

        from draftwright.cli import _parse_formats

        with pytest.raises(typer.BadParameter, match="unknown format 'jpg'"):
            _parse_formats("pdf,jpg")

    def test_parse_empty_raises(self):
        import typer

        from draftwright.cli import _parse_formats

        with pytest.raises(typer.BadParameter, match="no output format"):
            _parse_formats(" , ")

    class _FakeDwg:
        """Records export() calls and writes real placeholder files so _emit's
        temp-SVG cleanup can be observed."""

        def __init__(self, tmp):
            self.tmp = tmp
            self.calls = []
            self.svg_path = None

        def export(self, *, svg=True, dxf=True):
            self.calls.append(("export", svg, dxf))
            sp = str(self.tmp / "o.svg") if svg else None
            dp = str(self.tmp / "o.dxf") if dxf else None
            for p in (sp, dp):
                if p:
                    open(p, "w").close()
            self.svg_path = sp
            return sp, dp

        def export_pdf(self):
            self.calls.append(("export_pdf",))
            pp = str(self.tmp / "o.pdf")
            open(pp, "w").close()
            return pp

    def test_emit_pdf_only_discards_temp_svg(self, tmp_path):
        from draftwright.cli import _emit

        dwg = self._FakeDwg(tmp_path)
        out = _emit(dwg, ["pdf"])

        assert out == [str(tmp_path / "o.pdf")]
        # SVG was written to drive the PDF, then removed; DXF never written.
        assert dwg.calls == [("export", True, False), ("export_pdf",)]
        assert not (tmp_path / "o.svg").exists()
        assert not (tmp_path / "o.dxf").exists()
        assert (tmp_path / "o.pdf").exists()

    def test_emit_svg_dxf_skips_pdf(self, tmp_path):
        from draftwright.cli import _emit

        dwg = self._FakeDwg(tmp_path)
        out = _emit(dwg, ["svg", "dxf"])

        assert out == [str(tmp_path / "o.svg"), str(tmp_path / "o.dxf")]
        assert dwg.calls == [("export", True, True)]
        assert (tmp_path / "o.svg").exists()
        assert (tmp_path / "o.dxf").exists()

    def test_emit_all_keeps_requested_svg(self, tmp_path):
        from draftwright.cli import _emit

        dwg = self._FakeDwg(tmp_path)
        out = _emit(dwg, ["pdf", "svg", "dxf"])

        assert out == [
            str(tmp_path / "o.pdf"),
            str(tmp_path / "o.svg"),
            str(tmp_path / "o.dxf"),
        ]
        # SVG requested → kept, not discarded.
        assert (tmp_path / "o.svg").exists()


# ---------------------------------------------------------------------------
# ViewCoordinates (pure-Python, no OCP needed)
# ---------------------------------------------------------------------------


class TestViewCoordinates:
    def _front_vc(self):
        # Front view: camera at (0, -100, 0), up=(0,0,1), look_at=(0,0,0)
        # → world X → page_X (+1), world Z → page_Y (+1)
        axes = view_axes((0.0, -100.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        return ViewCoordinates(axes, view_x=100.0, view_y=80.0, cx=0.0, cy=0.0, cz=0.0, scale=1.0)

    def test_px_at_origin(self):
        vc = self._front_vc()
        assert vc.px(0.0) == pytest.approx(100.0)

    def test_py_at_origin(self):
        vc = self._front_vc()
        assert vc.py(0.0) == pytest.approx(80.0)

    def test_px_positive_offset(self):
        vc = self._front_vc()
        assert vc.px(10.0) == pytest.approx(110.0)

    def test_py_positive_offset(self):
        vc = self._front_vc()
        assert vc.py(5.0) == pytest.approx(85.0)

    def test_scale_applied(self):
        axes = view_axes((0.0, -100.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        vc = ViewCoordinates(axes, view_x=0.0, view_y=0.0, cx=0.0, cy=0.0, cz=0.0, scale=2.0)
        assert vc.px(5.0) == pytest.approx(10.0)

    def test_centroid_offset(self):
        axes = view_axes((0.0, -100.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        vc = ViewCoordinates(axes, view_x=50.0, view_y=50.0, cx=10.0, cy=0.0, cz=5.0, scale=1.0)
        assert vc.px(10.0) == pytest.approx(50.0)  # at centroid → view centre
        assert vc.py(5.0) == pytest.approx(50.0)  # at centroid → view centre

    # px_axis / py_axis attributes

    def test_front_view_px_axis(self):
        vc = self._front_vc()
        assert vc.px_axis == "world_X"

    def test_front_view_py_axis(self):
        vc = self._front_vc()
        assert vc.py_axis == "world_Z"

    # pp() matches px()/py() for orthographic views

    def test_pp_front_view_matches_px_py(self):
        vc = self._front_vc()
        page_x, page_y = vc.pp(10.0, 0.0, 5.0)
        assert page_x == pytest.approx(vc.px(10.0))
        assert page_y == pytest.approx(vc.py(5.0))

    def test_pp_front_view_ignores_depth_axis(self):
        # world_Y is depth in front view — varying it should not change the page point
        vc = self._front_vc()
        pt_a = vc.pp(10.0, 0.0, 5.0)
        pt_b = vc.pp(10.0, 50.0, 5.0)
        assert pt_a == pytest.approx(pt_b)

    # Side view: camera on +X axis → world_Y → page_X, world_Z → page_Y

    def _side_vc(self):
        axes = view_axes((100.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, 0.0))
        return ViewCoordinates(axes, view_x=150.0, view_y=80.0, cx=0.0, cy=0.0, cz=0.0, scale=1.0)

    def test_side_view_px_axis_is_world_y(self):
        vc = self._side_vc()
        assert vc.px_axis == "world_Y"

    def test_side_view_py_axis_is_world_z(self):
        vc = self._side_vc()
        assert vc.py_axis == "world_Z"

    def test_side_view_px_maps_y_coordinate(self):
        vc = self._side_vc()
        assert vc.px(8.0) == pytest.approx(158.0)

    def test_side_view_py_maps_z_coordinate(self):
        vc = self._side_vc()
        assert vc.py(3.0) == pytest.approx(83.0)

    def test_side_view_pp_matches_px_py(self):
        vc = self._side_vc()
        page_x, page_y = vc.pp(0.0, 8.0, 3.0)
        assert page_x == pytest.approx(vc.px(8.0))
        assert page_y == pytest.approx(vc.py(3.0))

    # Plan view: camera on +Z axis → world_X → page_X, world_Y → page_Y

    def _plan_vc(self):
        axes = view_axes((0.0, 0.0, 100.0), (0.0, 1.0, 0.0), (0.0, 0.0, 0.0))
        return ViewCoordinates(axes, view_x=100.0, view_y=150.0, cx=0.0, cy=0.0, cz=0.0, scale=1.0)

    def test_plan_view_px_axis_is_world_x(self):
        vc = self._plan_vc()
        assert vc.px_axis == "world_X"

    def test_plan_view_py_axis_is_world_y(self):
        vc = self._plan_vc()
        assert vc.py_axis == "world_Y"

    def test_plan_view_pp_matches_px_py(self):
        vc = self._plan_vc()
        page_x, page_y = vc.pp(7.0, 4.0, 0.0)
        assert page_x == pytest.approx(vc.px(7.0))
        assert page_y == pytest.approx(vc.py(4.0))

    # ISO view: camera at (-DIST, -DIST, DIST) → two world axes → page_X

    def _iso_vc(self):
        # Standard ISO camera. Built from the raw viewport so pp() carries the
        # real foreshortening basis (helpers >=0.11 requires it for oblique views).
        return ViewCoordinates.from_viewport(
            (-100.0, -100.0, 100.0),
            (0.0, 0.0, 1.0),
            (0.0, 0.0, 0.0),
            view_x=100.0,
            view_y=80.0,
            cx=0.0,
            cy=0.0,
            cz=0.0,
            scale=1.0,
        )

    def test_iso_view_px_axis_is_none(self):
        vc = self._iso_vc()
        assert vc.px_axis is None

    def test_iso_view_px_raises_with_helpful_message(self):
        vc = self._iso_vc()
        with pytest.raises(ValueError, match="pp"):
            vc.px(5.0)

    def test_iso_view_py_raises_with_helpful_message(self):
        # world_Z → page_Y uniquely, so py_axis should be set
        # (ISO typically only has the page_X clash, not page_Y)
        vc = self._iso_vc()
        # world_Z maps cleanly to page_Y — py() should still work
        assert vc.py_axis == "world_Z"
        assert vc.py(3.0) == pytest.approx(83.0)

    def test_iso_view_pp_correct(self):
        # ISO camera at (-100,-100,100), look_at=(0,0,0), up=(0,0,1). pp() now
        # uses the true foreshortening basis (helpers >=0.11) rather than the old
        # collapsed view_axes mapping, so an off-centre point projects with real
        # axonometric foreshortening (was the un-foreshortened (105, 83)).
        vc = self._iso_vc()
        page_x, page_y = vc.pp(10.0, 5.0, 3.0)
        assert page_x == pytest.approx(103.5355339)
        assert page_y == pytest.approx(88.5732141)

    def test_iso_view_pp_at_centroid_gives_view_centre(self):
        vc = self._iso_vc()
        page_x, page_y = vc.pp(0.0, 0.0, 0.0)
        assert page_x == pytest.approx(100.0)
        assert page_y == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# analyse_cylinders / recognise_face_levels — require OCP (slow)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_analyse_cylinders_box_has_no_z_cylinders():
    from build123d import Box

    box = Box(30, 20, 10)
    z_cyls, cross_cyls = analyse_cylinders(box)
    assert z_cyls == []
    assert cross_cyls == []


@pytest.mark.timeout(60)
def test_analyse_cylinders_finds_cylinder():
    from build123d import Cylinder

    cyl = Cylinder(5, 20)  # radius=5, height=20 → diameter=10
    z_cyls, cross_cyls = analyse_cylinders(cyl)
    assert len(z_cyls) >= 1
    diameters = [c["diameter"] for c in z_cyls]
    assert any(abs(d - 10.0) < 0.5 for d in diameters)


@pytest.mark.timeout(60)
def test_analyse_face_levels_box():
    from build123d import Box

    box = Box(30, 20, 10)
    levels = recognise_face_levels(box)
    # Box centred at origin has Z faces at -5 and +5
    assert any(abs(fl.z - (-5.0)) < 0.1 for fl in levels)
    assert any(abs(fl.z - 5.0) < 0.1 for fl in levels)


@pytest.mark.timeout(60)
def test_analyse_face_levels_returns_sorted():
    from build123d import Box

    box = Box(30, 20, 10)
    levels = recognise_face_levels(box)
    assert levels == sorted(levels)


@pytest.mark.timeout(60)
def test_analyse_face_levels_area_filter_drops_tiny_faces():
    # A sub-feature horizontal face (e.g. a fragment of engraved text) is far
    # smaller than the plan footprint and must not be counted as a real step.
    # staircase.step review: a 0.57 mm² digit face was dimensioned as z=6.4.
    from build123d import Box, Pos

    # 30×20 footprint (600 mm²); a 1×1 pip on top (1 mm² top face at z=7).
    part = Box(30, 20, 10) + Pos(0, 0, 6) * Box(1, 1, 2)

    # Without the filter the tiny face shows up as a phantom level.
    unfiltered = recognise_face_levels(part)
    assert any(abs(fl.z - 7.0) < 0.1 for fl in unfiltered)

    # With a 1%-of-footprint threshold (6 mm²) the 1 mm² face is dropped,
    # leaving only the real slab faces.
    filtered = recognise_face_levels(part, min_area_frac=0.01)
    assert not any(abs(fl.z - 7.0) < 0.1 for fl in filtered)
    assert any(abs(fl.z - 5.0) < 0.1 for fl in filtered)
    assert any(abs(fl.z - (-5.0)) < 0.1 for fl in filtered)


# ---------------------------------------------------------------------------
# Drawing builder (build_drawing / Drawing / add_view)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
def test_build_drawing_returns_populated_drawing(tmp_path):
    dwg = build_drawing(Box(30, 20, 10), out=str(tmp_path / "b"), title="B", number="DWG-1")
    assert isinstance(dwg, Drawing)
    assert set(dwg.views) == {"front", "plan", "side", "iso"}
    assert dwg.items, "expected automatic annotations"
    # build_drawing must not write any files — that is export()'s job.
    assert not (tmp_path / "b.svg").exists()
    assert not (tmp_path / "b.dxf").exists()


@pytest.mark.timeout(60)
def test_build_drawing_export_writes_files(tmp_path):
    stem = str(tmp_path / "b")
    dwg = build_drawing(Box(30, 20, 10), out=stem)
    svg, dxf = dwg.export(stem)
    assert Path(svg).exists() and Path(dxf).exists()
    assert dwg.svg_path == svg and dwg.dxf_path == dxf


@pytest.mark.timeout(60)
def test_build_drawing_scale_and_page_override(tmp_path):
    # Issue #63 — explicit scale/page reach the Drawing instead of choose_scale's pick
    dwg = build_drawing(Box(28, 8.5, 12.5), out=str(tmp_path / "o"), scale=5, page="A3")
    assert dwg.scale == 5.0
    assert (dwg.page_w, dwg.page_h) == (420.0, 297.0)


@pytest.mark.timeout(60)
def test_build_drawing_auto_dims_false():
    # #74 — views, scale, page, and title block only; no turned-part dims.
    dwg = build_drawing(Cylinder(15, 40), auto_dims=False)
    assert set(dwg.views) == {"front", "plan", "side", "iso"}
    assert [a for a in dwg.items] == [dwg._named["title_block"]]


@pytest.mark.timeout(60)
def test_clear_annotations_keeps_title_block():
    # #74 — wholesale removal without knowing the auto-name scheme.
    dwg = build_drawing(Cylinder(15, 40))  # cylinder → od dim, centerlines, …
    assert len(dwg.items) > 1
    removed = dwg.clear_annotations()
    assert removed
    assert all(a not in dwg.items for a in removed)
    assert len(dwg.items) == 1
    assert "title_block" in dwg._named and len(dwg._named) == 1


@pytest.mark.timeout(60)
def test_clear_annotations_keep_custom_and_unnamed_removed():
    dwg = build_drawing(Box(30, 20, 10))
    keep_me = dwg.add(
        Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="K", draft=dwg.draft), "ldr_k"
    )
    dwg.add(Leader(tip=dwg.at("front", 0, 0, 0), elbow=(6, 6, 0), label="U", draft=dwg.draft))
    dwg.clear_annotations(keep=("title_block", "ldr_k"))
    assert set(dwg._named) == {"title_block", "ldr_k"}
    assert keep_me in dwg.items
    assert len(dwg.items) == 2  # unnamed leader removed too


@pytest.fixture(scope="module")
def ctc01_a3_drawing():
    # Fixture — NIST CTC-01-like plate at 1:5 on A3.  Module-scoped; tests must not mutate it.
    return build_drawing(Box(800, 450, 150), scale=0.2, page="A3")


@pytest.mark.timeout(120)
@_skip_011
def test_ctc01_iso_uses_upper_right_zone(ctc01_a3_drawing):
    # #75 updated — wide/flat part on A3: the iso is repositioned into the upper-right
    # zone (above the SV, right of FV/PV) where it fits at sheet scale.  No NTS label.
    from draftwright._core import _iso_bbox

    dwg = ctc01_a3_drawing
    labels = [getattr(a, "label", "") for a in dwg.items]
    assert "ISO VIEW (NTS)" not in labels  # iso now fits at sheet scale — no NTS
    x0, y0, x1, y1 = _iso_bbox(dwg)
    assert (
        x1 <= dwg.page_w - 10 + 0.5 and x0 >= 0 and y0 >= 10 - 0.5 and y1 <= dwg.page_h - 10 + 0.5
    )
    # iso must be significantly larger than the old 65 mm (shrunken) view
    assert (x1 - x0) > 100


@pytest.mark.timeout(120)
@_skip_011
def test_ctc01_iso_world_to_page_mapping(ctc01_a3_drawing):
    # dwg.at("iso", ...) must map world points to page even after the iso is
    # repositioned to the upper-right zone (still projected at sheet scale).
    dwg = ctc01_a3_drawing
    cx, cy, cz = dwg.centroid
    centre = dwg.at("iso", cx, cy, cz)
    vis, _hid = dwg.views["iso"]
    bb = vis.bounding_box()
    assert bb.min.X < centre[0] < bb.max.X and bb.min.Y < centre[1] < bb.max.Y
    iso_scale = dwg._coords["iso"]._scale
    raised = dwg.at("iso", cx, cy, cz + 100)
    # Raising world Z lifts the iso page point by the foreshortened amount: the
    # vertical axis of a (1,1,1)-camera isometric projects at sqrt(2/3) (helpers
    # >=0.11 uses the real basis instead of a 1:1 collapsed mapping).
    assert raised[1] - centre[1] == pytest.approx(100 * iso_scale * (2 / 3) ** 0.5)


@pytest.mark.timeout(60)
def test_iso_view_grow_capped_at_max():
    # The iso is an orientation aid, not a measured view: fitted to a large empty
    # zone it must not balloon past _ISO_MAX_GROW × sheet scale (was ~8× before).
    from draftwright.projection import _ISO_MAX_GROW

    # Small part forced onto a big sheet → large empty rectangle → would over-grow.
    dwg = build_drawing(Box(40, 30, 20), scale=1, page="A1")
    iso_scale = dwg._coords["iso"]._scale
    sheet_scale = dwg._analysis.SCALE
    assert iso_scale <= _ISO_MAX_GROW * sheet_scale + 1e-6
    assert iso_scale == pytest.approx(_ISO_MAX_GROW * sheet_scale, abs=1e-6)


@pytest.mark.timeout(60)
def test_iso_stays_within_page_bounds():
    # Whether scaled up or not, the iso must always lie within the page margin.
    from draftwright._core import _iso_bbox

    dwg = build_drawing(Box(30, 20, 10))
    x0, y0, x1, y1 = _iso_bbox(dwg)
    margin = 10
    assert x0 >= margin - 0.5
    assert y0 >= margin - 0.5
    assert x1 <= dwg.page_w - margin + 0.5
    assert y1 <= dwg.page_h - margin + 0.5


@pytest.mark.timeout(120)
def test_ctc01_iso_picks_upper_right_rectangle(ctc01_a3_drawing):
    # #11 — the general largest-empty-rectangle search must reproduce the #9
    # outcome for the wide/flat-on-A3 case: the chosen iso zone is the
    # upper-right region (right of the FV/PV column, above the SV row).
    dwg = ctc01_a3_drawing
    a = dwg._analysis
    # FV/PV occupy the left column; SV the lower-middle.  The picked rectangle
    # must sit to the right of the FV/PV column and above the SV row.
    fv_right = a.FV_X + a.fv_hw
    sv_top = a.SV_Y + a.fv_hh
    assert a.iso_left_limit >= fv_right
    assert a.iso_bottom_limit >= sv_top
    # And it reaches into the upper-right corner of the drawable area.
    assert a.iso_right_limit >= a.PAGE_W - a.margin - 0.5
    assert a.iso_top_limit >= a.PAGE_H - a.margin - 0.5
    assert a.ISO_X > a.PAGE_W / 2 and a.ISO_Y > a.PAGE_H / 2


@pytest.mark.timeout(120)
def test_tall_part_iso_in_largest_free_zone():
    # #11 — a tall/narrow part has no per-shape branch; the iso must land in the
    # largest empty rectangle, clear of every view bbox and the title block, and
    # stay within the page margins.
    from draftwright._core import _iso_bbox

    dwg = build_drawing(Box(40, 40, 300))
    a = dwg._analysis
    x0, y0, x1, y1 = _iso_bbox(dwg)
    margin = a.margin
    # Within page margins.
    assert x0 >= margin - 0.5
    assert y0 >= margin - 0.5
    assert x1 <= a.PAGE_W - margin + 0.5
    assert y1 <= a.PAGE_H - margin + 0.5

    iso_bb = (x0, y0, x1, y1)

    def overlaps(b1, b2):
        return b1[0] < b2[2] and b2[0] < b1[2] and b1[1] < b2[3] and b2[1] < b1[3]

    # No overlap with any orthographic view bounding box.
    for name in ("front", "plan", "side"):
        vis, hid = dwg.views[name]
        vb = vis.bounding_box()
        view_bb = (vb.min.X, vb.min.Y, vb.max.X, vb.max.Y)
        assert not overlaps(iso_bb, view_bb), f"iso overlaps {name} view"

    # No overlap with the title-block region (bottom-right corner).
    tb_bb = (a.PAGE_W - a.TB_W - 11, 11, a.PAGE_W - 11, 11 + 35)
    assert not overlaps(iso_bb, tb_bb), "iso overlaps title block"


@pytest.mark.timeout(60)
def test_drawing_add_and_remove():
    dwg = build_drawing(Box(30, 20, 10))
    n0 = len(dwg.items)
    ldr = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="X", draft=dwg.draft)
    dwg.add(ldr, "ldr_test")
    assert len(dwg.items) == n0 + 1
    removed = dwg.remove("ldr_test")
    assert removed is ldr
    assert len(dwg.items) == n0
    with pytest.raises(KeyError):
        dwg.remove("does_not_exist")


@pytest.mark.timeout(60)
def test_drawing_add_replaces_reused_name():
    dwg = build_drawing(Box(30, 20, 10))
    n0 = len(dwg.items)
    first = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(5, 5, 0), label="A", draft=dwg.draft)
    second = Leader(tip=dwg.at("front", 0, 0, 0), elbow=(6, 6, 0), label="B", draft=dwg.draft)
    dwg.add(first, "ldr")
    dwg.add(second, "ldr")  # same name → replaces, no orphan left behind
    assert len(dwg.items) == n0 + 1
    assert first not in dwg.items
    assert dwg.remove("ldr") is second


@pytest.mark.timeout(60)
def test_drawing_at_maps_world_to_page():
    dwg = build_drawing(Box(30, 20, 10))
    cx, cy, cz = dwg.centroid
    base = dwg.at("front", cx, cy, cz)
    # Front view: world +X → page +X, world +Z → page +Y.
    dx = dwg.at("front", cx + 10, cy, cz)
    dz = dwg.at("front", cx, cy, cz + 10)
    assert dx[0] > base[0] and dx[1] == pytest.approx(base[1])
    assert dz[1] > base[1] and dz[0] == pytest.approx(base[0])


@pytest.mark.timeout(60)
def test_drawing_add_view(tmp_path):
    dwg = build_drawing(Box(30, 20, 10))
    look = dwg.look_at
    bottom_cam = (look[0], look[1], look[2] - dwg.dist)
    vc = dwg.add_view("bottom", Box(30, 20, 10), bottom_cam, (0, 1, 0), (260.0, 60.0))
    assert "bottom" in dwg.views
    assert isinstance(vc, ViewCoordinates)
    # The custom view exports alongside the standard ones.
    svg, _ = dwg.export(str(tmp_path / "b"))
    assert Path(svg).exists()


@pytest.mark.timeout(60)
def test_generate_script_emits_build_drawing(tmp_path):
    box = Box(30, 20, 10)
    step = tmp_path / "p.step"
    export_step(box, str(step))
    py = generate_script(str(step), out=str(tmp_path / "p"))
    content = Path(py).read_text(encoding="utf-8")
    assert "build_drawing(" in content
    assert "dwg.export(" in content
    assert "Customise here" in content


def test_generate_script_preserves_pmi_scale_page(tmp_path):
    # #388: the generated script must carry the CLI's build intent — pmi/scale/page as
    # config fields AND threaded into the emitted build_drawing() call — so running the
    # script reproduces what the CLI would have built.
    step = tmp_path / "p.step"
    export_step(Box(30, 20, 10), str(step))
    py = generate_script(str(step), out=str(tmp_path / "p"), pmi="annotate", scale=5.0, page="A3")
    content = Path(py).read_text(encoding="utf-8")
    assert "PMI = 'annotate'" in content
    assert "SCALE = 5.0" in content
    assert "PAGE = 'A3'" in content
    assert "pmi=PMI," in content and "scale=SCALE," in content and "page=PAGE," in content


def test_generate_script_defaults_are_auto(tmp_path):
    # Defaults: no overrides → PMI off, SCALE/PAGE None (auto) — still emitted so the
    # fields exist for the user to set.
    step = tmp_path / "p.step"
    export_step(Box(30, 20, 10), str(step))
    content = Path(generate_script(str(step), out=str(tmp_path / "p"))).read_text()
    assert "PMI = 'off'" in content
    assert "SCALE = None" in content and "PAGE = None" in content


def test_generate_script_imports_lint_suggestion_classes(tmp_path):
    # #388: lint suggestions (dwg.lint_summary()) reference Leader/HoleCallout/Dimension;
    # the script must import them so a suggestion pastes+runs without manual imports.
    step = tmp_path / "p.step"
    export_step(Box(30, 20, 10), str(step))
    content = Path(generate_script(str(step), out=str(tmp_path / "p"))).read_text()
    assert "from build123d_drafting import Dimension, HoleCallout, Leader" in content


def test_generate_script_defers_invalid_scale_page(tmp_path):
    # #388/#401: an out-of-range scale/page must NOT crash generation — the script is
    # written with the value embedded and validation deferred to run time (consistent
    # with a large unfittable scale, which already defers).
    step = tmp_path / "p.step"
    export_step(Box(30, 20, 10), str(step))
    py = generate_script(str(step), out=str(tmp_path / "p"), scale=0.001, page="A9")
    content = Path(py).read_text()
    assert "SCALE = 0.001" in content and "PAGE = 'A9'" in content


def test_generate_script_reconstructs_features_as_intent_calls(tmp_path):
    # #400 Ph2 / #426 Ph5: the Customise section is a runnable detect-only reconstruction —
    # each feature redrawn by the domain add verbs against model().features[i]. The verbs
    # record inside `with dwg.deferred()`; finalize() (on block exit) batch-solves them.
    step = tmp_path / "p.step"
    export_step(Box(60, 60, 12) - Pos(0, 0, 0) * Cylinder(4, 40), str(step))
    content = Path(generate_script(str(step), out=str(tmp_path / "p"))).read_text(encoding="utf-8")
    assert "# ── Reconstruct the drawing at intent level (record → finalize, #426)" in content
    assert "auto_dims=False" in content  # the build is detect-only
    assert "with dwg.deferred():" in content  # #426 Ph5: verbs record, finalize on exit
    assert "# features[0]  hole @" in content  # indexed by model().features[i]
    assert "    dwg.callout(f)" in content  # hole ø via the callout verb (indented in block)
    assert "    dwg.locate(f)" in content  # its datum position
    assert "    dwg.furniture(f)" in content  # its centre mark
    assert '    dwg.dimension(f, "length", role="width")' in content  # envelope, editable
    assert "dwg.repair()" in content  # a peephole net after the batch solve


def test_generate_script_emits_prismatic_step_level_intent(tmp_path):
    # The prismatic height ladder is a correlated chain, but it now has a semantic
    # reconstruction verb instead of a gap comment.
    step = tmp_path / "stepped.step"
    part = Box(40, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20)
    export_step(part, str(step))
    content = Path(generate_script(str(step), out=str(tmp_path / "stepped"))).read_text(
        encoding="utf-8"
    )
    assert 'dwg.dimension(f, "length", role="step_height")' in content
    assert 'dwg.dimension(f, "length", role="height")' in content
    assert "step_level — auto-pass draws the prismatic height ladder" not in content


def test_feature_listing_is_deferred_intent_calls(tmp_path):
    # #400 Ph2 / #426 Ph5 (was Ph1 "fully inert"): the reconstruction block now contains
    # BARE, uncommented verb calls recorded inside `with dwg.deferred()`. Verify there are
    # runnable calls (indented under the with), they reference the read surface, and the
    # deferred block is present.
    step = tmp_path / "p.step"
    export_step(Box(40, 30, 8) - Pos(0, 0, 0) * Cylinder(3, 20), str(step))
    content = Path(generate_script(str(step), out=str(tmp_path / "p"))).read_text(encoding="utf-8")
    start = content.index("# ── Reconstruct the drawing at intent level")
    end = content.index("# ── Export", start)
    block = [ln for ln in content[start:end].splitlines() if ln.strip()]
    assert "with dwg.deferred():" in block  # the record → finalize scope
    live = [ln for ln in block if not ln.lstrip().startswith("#")]
    assert live, "reconstruction must contain runnable (uncommented) calls"
    assert any(ln.lstrip().startswith("dwg.") for ln in live)  # indented under the with
    assert any("dwg.model().features[" in ln for ln in live)


@pytest.mark.timeout(180)
def test_generated_script_runs_and_preserves_pmi(tmp_path):
    # #388 acceptance: a generated --pmi annotate script preserves pmi when RUN — execute
    # it in a subprocess and assert it builds output without error.
    import os
    import subprocess
    import sys

    step = tmp_path / "p.step"
    # A hole so the #400 listing carries a non-ASCII ø in a comment — proves the utf-8
    # source runs even under an ASCII stdout (source encoding is independent of stdout).
    export_step(Box(80, 50, 8) - Pos(0, 0, 0) * Cylinder(4, 40), str(step))
    py = generate_script(str(step), out=str(tmp_path / "p"), pmi="annotate")
    # Force an ASCII stdout so a non-ASCII char in the script's own print() (e.g. a
    # Unicode arrow) fails HERE on every platform, not only on a Windows cp1252 console.
    env = {**os.environ, "PYTHONIOENCODING": "ascii"}
    r = subprocess.run(
        [sys.executable, py],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=150,
        env=env,
    )
    assert r.returncode == 0, f"generated script failed:\n{r.stderr[-1500:]}"
    assert (tmp_path / "p.svg").exists(), "generated script did not write the SVG"


# ---------------------------------------------------------------------------
# Part classification (#81) — prismatic parts skip turned-part annotations
# ---------------------------------------------------------------------------


class TestPrismaticClassification:
    @pytest.mark.timeout(60)
    def test_prismatic_part_with_bores_skips_turned_annotations(self):
        # A housing-like plate: Z-axis bores exist, but they are holes — not
        # an OD. dim_od / centrelines / ldr_z* would all be wrong.
        part = (
            Box(100, 60, 20)
            - Pos(20, 10, 0) * Cylinder(5, 30)
            - Pos(-30, -15, 0) * Cylinder(8, 30)
        )
        dwg = build_drawing(part)
        assert "dim_od" not in dwg._named
        assert "centerline_front" not in dwg._named
        assert "centerline_side" not in dwg._named
        assert not any(name.startswith("ldr_z") for name in dwg._named)

    @pytest.mark.timeout(60)
    def test_rotational_part_keeps_turned_annotations(self):
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert "dim_od" in dwg._named
        assert "centerline_front" in dwg._named
        assert "ldr_z0" in dwg._named

    @pytest.mark.timeout(60)
    def test_z_axis_stepped_shaft_calls_out_step_diameters(self):
        # A vertical (Z-axis) stepped shaft: dim_od dimensions the OD, and the
        # intermediate step diameter gets a ø callout in the left-hand column
        # (#131 — the page-Y mirror of the X-axis #77 row-below). Without it the
        # ⌀20 step surfaces only as feature_not_dimensioned.
        shaft = Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(10, 30)
        dwg = build_drawing(shaft)
        diam_labels = {o.label for o in dwg.items if getattr(o, "label", "") and "ø" in o.label}
        assert "ø30" in diam_labels and "ø20" in diam_labels
        # The step diameter is placed by the IR renderer's left-hand column (#131,
        # migrated to from_model.render_diameters → m_dia_z names).
        assert any(name.startswith("m_dia_z") for name in dwg._named)
        assert not [i for i in dwg.lint() if i.code == "feature_not_dimensioned"]

    @pytest.mark.timeout(60)
    def test_locates_side_drilled_holes(self):
        # A side-drilled (X-axis) hole appears as a circle in the side view and
        # must be located THERE. _add_location_dims was plan-view (z-hole) only,
        # so off-axis holes got a diameter callout but no position (#133).
        from build123d import Rot

        part = Box(12, 40, 30) - Pos(0, 8, 6) * Rot(0, 90, 0) * Cylinder(3, 12)
        dwg = build_drawing(part)
        loc = {name for name in dwg._named if name.startswith("dim_loc")}
        # Fully located: the in-plane (Y) offset below the side view AND the
        # height (Z) offset to its right (#133). The Z routes to whichever right
        # strip is free — side here (no section view to contend it).
        assert any(n.startswith("dim_loc_side_y") for n in loc), "in-plane offset missing"
        assert any(n.endswith("_z2100") for n in loc), "height offset missing"
        # The location dims must never overprint the callouts/section that share
        # the right strips — the sheet stays lint-clean (#133 rework).
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_side_view_location_dim_stacks_inside_the_envelope(self):
        # ISO stacking: the overall (envelope) dim sits OUTERMOST, the feature/location
        # dim nearer the view. A side-drilled hole's in-plane location must therefore
        # be CLOSER to the side view than the envelope-depth dim. The inverted stack
        # (envelope innermost) forced the shorter location dim's arrows to flip outward
        # and clash with the envelope (GRM-01 / GRM-02).
        from build123d import Rot

        # Off-centre hole (y=2, not the centreline) so the location dim is real, not a
        # redundant centred one — this isolates the stacking order.
        part = Box(12, 11, 40) - Pos(0, 2, 6) * Rot(0, 90, 0) * Cylinder(3, 12)
        dwg = build_drawing(part)
        env = dwg._named.get("m_env_depth")
        loc = [o for n, o in dwg._named.items() if n.startswith("dim_loc_side_y")]
        assert env is not None and loc, "expected an envelope-depth dim and a side location dim"

        def ymid(o):
            bb = o.bounding_box()
            return (bb.min.Y + bb.max.Y) / 2

        # The below strip extends downward from the side view, so nearer the view =
        # higher Y. The location dim must sit nearer the view than the overall dim.
        assert min(ymid(o) for o in loc) > ymid(env), "location must stack inside the envelope"
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_envelope_depth_survives_many_side_location_dims(self):
        # The mandatory overall depth dim must always be placed, even when several
        # side-drilled holes fill the side-below strip with location dims. The
        # location pass now queues with the envelope (for ISO stacking), and the
        # envelope's corridor priority means best-effort location dims can never starve it
        # (the #316-review regression, now enforced without a manual reservation).
        from build123d import Cylinder, Pos, Rot

        part = Box(12, 24, 60)
        for y, z in [(-9, -20), (-5, -8), (7, 4), (10, 16)]:
            part -= Pos(0, y, z) * Rot(0, 90, 0) * Cylinder(1.5, 12)
        dwg = build_drawing(part)
        ylocs = [n for n in dwg._named if n.startswith("dim_loc_side_y")]
        assert len(ylocs) >= 2, "expected several side-below location dims for strip pressure"
        assert "m_env_depth" in dwg._named, "the mandatory overall depth dim was starved"
        assert dwg.lint_summary()["by_code"].get("missing_principal_dimension", 0) == 0

        def ymid(o):
            bb = o.bounding_box()
            return (bb.min.Y + bb.max.Y) / 2

        env = dwg._named["m_env_depth"]
        assert all(ymid(dwg._named[n]) > ymid(env) for n in ylocs), "locations must stack inside"

    def test_square_footprint_does_not_reserve_a_suppressed_envelope_tier(self):
        # When the planner suppresses the depth dim (square footprint / X-turned),
        # the side-below envelope-tier reservation must NOT fire — reserving a tier
        # render_envelope never claims would needlessly shrink the strip and drop a
        # side location that otherwise fits (#316 review).
        from build123d import Cylinder, Pos, Rot

        part = Box(20, 20, 40) - Pos(0, 4, 0) * Rot(0, 90, 0) * Cylinder(2, 20)
        dwg = build_drawing(part)
        assert "m_env_depth" not in dwg._named  # square footprint → depth suppressed
        assert [n for n in dwg._named if n.startswith("dim_loc_side_y")], "location was dropped"
        assert dwg.lint_summary()["by_code"].get("off_axis_location_dropped", 0) == 0

    @pytest.mark.timeout(60)
    def test_locates_every_side_drilled_hole_not_just_the_first(self):
        # Two side-drilled (Y-axis) holes at distinct x: each must get its own
        # in-plane (X) location dim. The first hole's own front-view callout sits
        # in the below strip, so the location dim collided and was DROPPED after a
        # single tier — only the first hole ended up located (#225). _place now
        # retries the next tier past the callout, so both are located.
        from build123d import Rot

        part = (
            Box(80, 40, 30)
            - Pos(-20, 0, 5) * Rot(90, 0, 0) * Cylinder(2.5, 50)
            - Pos(25, 0, -5) * Rot(90, 0, 0) * Cylinder(4, 50)
        )
        dwg = build_drawing(part)
        xlocs = {n for n in dwg._named if n.startswith("dim_loc_front_x")}
        assert len(xlocs) == 2, f"both side-drilled holes must be located, got {xlocs}"
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(60)
    def test_corner_fillets_do_not_make_a_plate_rotational(self):
        # Big quarter-cylinder corner fillets on a square plate must not be
        # mistaken for an OD.
        from build123d import Axis, fillet

        box = Box(60, 60, 20)
        part = fillet(box.edges().filter_by(Axis.Z), 25)
        dwg = build_drawing(part)
        assert "dim_od" not in dwg._named


# ---------------------------------------------------------------------------
# Export fallback (#83) — element-wise retry with view/layer context
# ---------------------------------------------------------------------------


class _FlakyExporter:
    """Stand-in exporter: rejects multi-element shapes (or everything)."""

    def __init__(self, fail_all=False):
        self.added = []
        self.fail_all = fail_all

    def add_shape(self, shape, layer=None):
        if self.fail_all:
            raise AssertionError("Constraint failed")
        elements = shape.faces() or shape.edges()
        if len(elements) > 1:
            raise AssertionError("Constraint failed")
        self.added.append(shape)


class TestExportShapeFallback:
    def test_compound_falls_back_to_edges(self):
        edges = Compound(
            [
                Edge.make_line((0, 0, 0), (1, 0, 0)),
                Edge.make_line((0, 1, 0), (1, 1, 0)),
            ]
        )
        exporter = _FlakyExporter()
        _export_shape(exporter, edges, "hidden", "view 'iso'")
        assert len(exporter.added) == 2

    def test_all_elements_failing_raises_with_context(self):
        edges = Compound([Edge.make_line((0, 0, 0), (1, 0, 0))])
        with pytest.raises(RuntimeError, match=r"view 'iso' \(layer 'hidden'\)"):
            _export_shape(_FlakyExporter(fail_all=True), edges, "hidden", "view 'iso'")

    def test_annotation_falls_back_to_faces(self):
        from build123d import Draft
        from build123d_drafting import Note

        note = Note("AB", (10, 10), Draft(font_size=3.0))  # two glyphs → ≥2 faces
        exporter = _FlakyExporter()
        _export_shape(exporter, note, "dims", "annotation 'AB'")
        assert len(exporter.added) == len(note.faces())

    def test_mixed_faces_and_loose_edges_all_exported(self):
        # A compound mixing text faces with bare stroke edges must not lose
        # the edges in the element-wise path.
        from build123d import Text

        mixed = Compound([*Text("A", 3).faces(), Edge.make_line((5, 5, 0), (9, 5, 0))])
        exporter = _FlakyExporter()
        _export_shape(exporter, mixed, "dims", "annotation 'mixed'")
        assert len(exporter.added) == len(mixed.faces()) + 1

    def test_svg_exporter_failure_raises_when_nothing_exports(self, monkeypatch):
        # Atomic (SVG) path: whole-shape add fails and the shape decomposes
        # to nothing — the original error must surface, not be swallowed.
        from build123d import ExportSVG

        svg = ExportSVG()
        svg.add_layer("part")

        def boom(self, shape, layer="", **kwargs):
            raise AssertionError("Constraint failed")

        monkeypatch.setattr(ExportSVG, "add_shape", boom)
        with pytest.raises(RuntimeError, match="nothing could be exported"):
            _export_shape(svg, Compound([]), "part", "view 'iso'")

    @pytest.mark.timeout(60)
    def test_export_survives_one_bad_compound(self, tmp_path, monkeypatch):
        # Simulate #83: OCCT raises a bare AssertionError for one view
        # compound. export() must degrade element-wise and still write files.
        from build123d import ExportSVG

        dwg = build_drawing(Box(30, 20, 10))
        real = ExportSVG.add_shape
        state = {"tripped": False}

        def flaky(self, shape, layer="default", **kwargs):
            if not state["tripped"] and layer == "part":
                state["tripped"] = True
                raise AssertionError("Constraint failed")
            return real(self, shape, layer=layer, **kwargs)

        monkeypatch.setattr(ExportSVG, "add_shape", flaky)
        svg, dxf = dwg.export(str(tmp_path / "f"))
        assert Path(svg).exists() and Path(dxf).exists()


# ---------------------------------------------------------------------------
# Feature-coverage lint (#80) — size coverage of hole/boss diameters
# ---------------------------------------------------------------------------


class TestLintFeatureCoverage:
    @pytest.mark.timeout(60)
    def test_uncovered_bore_is_flagged(self):
        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(4, 30)
        issues = lint_feature_coverage(part, [])
        assert [i.code for i in issues] == ["feature_not_dimensioned"]
        assert "ø8" in issues[0].message
        assert issues[0].severity == "warning"

    @pytest.mark.timeout(60)
    def test_diameter_callout_covers_feature(self):
        from build123d import Draft
        from build123d_drafting import Note

        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(4, 30)
        ann = Note("4× ø8 THRU", (10, 10), Draft(font_size=3.0))
        assert lint_feature_coverage(part, [ann]) == []

    @pytest.mark.timeout(60)
    def test_radius_note_does_not_cover(self):
        # An "R4 TYP" fillet note must not mask an undimensioned ø8 bore.
        from build123d import Draft
        from build123d_drafting import Note

        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(4, 30)
        ann = Note("R4 TYP", (10, 10), Draft(font_size=3.0))
        assert [i.code for i in lint_feature_coverage(part, [ann])] == ["feature_not_dimensioned"]

    @pytest.mark.timeout(60)
    def test_slot_split_bore_is_still_a_feature(self):
        # Two opposed keyway notches leave the bore wall as two cylinder patches
        # under half a turn each — together they are still one undimensioned ø10
        # hole. (A single full-width slot would bisect the block into two solids,
        # i.e. two half-bores rather than one keyed hole; coaxial bores in
        # *different* solids are kept distinct, helpers #68.)
        part = (
            Box(60, 40, 10)
            - Cylinder(5, 12)
            - Pos(0, 5, 0) * Box(2, 4, 12)
            - Pos(0, -5, 0) * Box(2, 4, 12)
        )
        assert len(part.solids()) == 1
        issues = lint_feature_coverage(part, [])
        assert any("ø10" in i.message for i in issues)

    @pytest.mark.timeout(60)
    def test_single_part_feature_warns(self):
        # A lone part keeps strict severity: an undimensioned bore is a warning.
        part = Box(40, 40, 12) - Cylinder(4, 12)
        issues = lint_feature_coverage(part, [])
        fnd = [i for i in issues if i.code == "feature_not_dimensioned"]
        assert fnd and all(i.severity == "warning" for i in fnd)

    @pytest.mark.timeout(60)
    def test_multisolid_assembly_downgrades_to_info(self):
        # A general-arrangement (multi-solid) drawing omits each part's bores by
        # design, so feature_not_dimensioned drops to info — out of the warning
        # count but still queryable (#69).
        a = Pos(0, 0, 0) * (Box(20, 20, 12) - Cylinder(3, 12))
        b = Pos(40, 0, 0) * (Box(20, 20, 12) - Cylinder(2.5, 12))
        asm = Compound(children=[a, b])
        assert len(asm.solids()) == 2
        issues = lint_feature_coverage(asm, [])
        fnd = [i for i in issues if i.code == "feature_not_dimensioned"]
        assert fnd and all(i.severity == "info" for i in fnd)

    @pytest.mark.timeout(60)
    def test_assembly_override_forces_strict(self):
        # assembly=False forces strict severity even on a multi-solid part.
        a = Pos(0, 0, 0) * (Box(20, 20, 12) - Cylinder(3, 12))
        b = Pos(40, 0, 0) * (Box(20, 20, 12) - Cylinder(2.5, 12))
        asm = Compound(children=[a, b])
        issues = lint_feature_coverage(asm, [], assembly=False)
        fnd = [i for i in issues if i.code == "feature_not_dimensioned"]
        assert fnd and all(i.severity == "warning" for i in fnd)

    @pytest.mark.timeout(120)
    def test_build_drawing_assembly_keeps_warnings_clean(self):
        # End to end: a GA's uncovered bores land as infos, not warnings, so the
        # warning count and quality score are not polluted; assembly=False
        # restores the strict warnings.
        a = Pos(0, 0, 0) * (Box(20, 20, 12) - Cylinder(3, 12))
        b = Pos(40, 0, 0) * (Box(20, 20, 12) - Cylinder(2.5, 12))
        asm = Compound(children=[a, b])
        auto = build_drawing(asm, page="A4", auto_dims=False).lint_summary()
        strict = build_drawing(asm, page="A4", auto_dims=False, assembly=False).lint_summary()
        assert auto["by_code"].get("feature_not_dimensioned", 0) > 0
        assert auto["warnings"] == 0 and auto["infos"] > 0
        assert strict["warnings"] > 0 and strict["infos"] == 0

    @pytest.mark.timeout(60)
    def test_hole_callout_accepts_string_diameter(self):
        from build123d_drafting import HoleCallout

        callout = HoleCallout("8.5 H7", through=True)
        assert callout.covers_diameters == (8.5,)

    @pytest.mark.timeout(60)
    def test_fillets_are_not_features(self):
        from build123d import Axis, fillet

        box = Box(60, 40, 20)
        part = fillet(box.edges().filter_by(Axis.Z), 3)
        assert lint_feature_coverage(part, []) == []

    @pytest.mark.timeout(60)
    def test_drawing_lint_reports_unannotated_bore(self):
        # Prismatic bores now get automatic callouts (#91) — so the sheet is
        # born clean, and removing the callout must surface the bore through
        # the coverage lint as the missing-dimension signal (#80).
        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(5, 30)
        dwg = build_drawing(part)
        assert "feature_not_dimensioned" not in [i.code for i in dwg.lint()]
        for name in [n for n in dwg._named if n.startswith("hc_")]:
            dwg.remove(name)
        codes = [i.code for i in dwg.lint()]
        assert "feature_not_dimensioned" in codes

    @pytest.mark.timeout(60)
    def test_drawing_lint_clean_for_annotated_rotational_part(self):
        dwg = build_drawing(Cylinder(15, 40) - Cylinder(5, 40))
        assert [i for i in dwg.lint() if i.code == "feature_not_dimensioned"] == []

    @pytest.mark.timeout(60)
    def test_title_block_text_is_not_a_callout(self):
        # "BRACKET R8" in the title must not mark ø16 as covered.
        from build123d import Draft
        from build123d_drafting import TitleBlock

        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(8, 30)
        tb = TitleBlock("BRACKET R8", "DWG-1", draft=Draft(font_size=3.0))
        issues = lint_feature_coverage(part, [tb])
        assert [i.code for i in issues] == ["feature_not_dimensioned"]

    @pytest.mark.timeout(60)
    def test_hole_callout_covers_via_structured_metadata(self):
        # HoleCallout draws its ø glyphs geometrically (label is "") — it must
        # still count as coverage.
        from build123d_drafting import HoleCallout

        part = Box(100, 60, 20) - Pos(20, 10, 0) * Cylinder(4.25, 30)
        callout = HoleCallout(8.5, count=4, through=True)
        assert lint_feature_coverage(part, [callout]) == []


class TestLintLocationCoverage:
    """lint_location_coverage (#218) — centre-mark + location coverage, derived
    from the drawing so it judges any producer."""

    @pytest.mark.timeout(60)
    def test_engine_drawing_is_located_and_centermarked(self):
        from draftwright.linting import lint_location_coverage

        # The engine centre-marks and locates ordinary prismatic holes.
        part = (
            Box(100, 60, 12) - Pos(-30, 0, 0) * Cylinder(4, 30) - Pos(30, 0, 0) * Cylinder(4, 30)
        )
        dwg = build_drawing(part, number="X")
        assert lint_location_coverage(part, dwg) == []

    @pytest.mark.timeout(60)
    def test_bare_scaffold_flags_missing_marks_and_location(self):
        from draftwright.linting import lint_location_coverage

        # auto_dims=False → views but no annotations → every hole uncovered.
        part = (
            Box(100, 60, 12) - Pos(-30, 0, 0) * Cylinder(4, 30) - Pos(30, 0, 0) * Cylinder(4, 30)
        )
        dwg = build_drawing(part, number="X", auto_dims=False)
        codes = {i.code for i in lint_location_coverage(part, dwg)}
        assert codes == {"feature_no_centermark", "feature_not_located"}

    @pytest.mark.timeout(60)
    def test_bolt_circle_holes_exempt_from_location(self):
        import math

        from draftwright.linting import lint_location_coverage

        part = Cylinder(40, 8)
        for i in range(6):
            a = i * math.pi / 3
            part -= Pos(25 * math.cos(a), 25 * math.sin(a), 0) * Cylinder(3, 20)
        dwg = build_drawing(part, number="X", auto_dims=False)
        codes = {i.code for i in lint_location_coverage(part, dwg)}
        # patterned → located by the BCD, so only centre marks are flagged
        assert codes == {"feature_no_centermark"}

    @pytest.mark.timeout(60)
    def test_coaxial_bore_exempt_from_location(self):
        from draftwright.linting import lint_location_coverage

        # A bore on the part's centre axis is located by centrelines, not a dim.
        part = Cylinder(15, 30) - Cylinder(4, 40)
        dwg = build_drawing(part, number="X", auto_dims=False)
        assert not any(i.code == "feature_not_located" for i in lint_location_coverage(part, dwg))


class TestAutoHoleAnnotations:
    """Auto hole callouts (#91), count grouping (#92), centre marks (#95)."""

    @pytest.fixture(scope="class")
    def plate_drawing(self):
        # 4× ø10 thru corners + centre ø8 thru with ø16×6 cbore + ø6 x-axis
        # cross hole + ø12 blind hole
        part = (
            Box(100, 100, 20)
            - Pos(35, 35, 0) * Cylinder(5, 20)
            - Pos(-35, 35, 0) * Cylinder(5, 20)
            - Pos(35, -35, 0) * Cylinder(5, 20)
            - Pos(-35, -35, 0) * Cylinder(5, 20)
            - Cylinder(4, 20)
            - Pos(0, 0, 7) * Cylinder(8, 6)
            - Pos(0, 25, 0) * Cylinder(3, 100, rotation=(0, 90, 0))
            - Pos(-20, -10, 10 - 4) * Cylinder(6, 8)
        )
        return build_drawing(part)

    @pytest.mark.timeout(120)
    def test_identical_holes_share_one_counted_callout(self, plate_drawing):
        hc = [n for n in plate_drawing._named if n.startswith("hc_plan")]
        # 3 distinct Z specs (4× ø10 thru, ø8 cbore stack, ø12 blind), not 6
        assert len(hc) == 3

    @pytest.mark.timeout(120)
    def test_callouts_cover_all_feature_diameters(self, plate_drawing):
        covered = set()
        for name, ann in plate_drawing._named.items():
            if name.startswith("hc_"):
                covered.update(getattr(ann, "covers_diameters", ()))
        assert covered == {10.0, 8.0, 16.0, 6.0, 12.0}

    @pytest.mark.timeout(120)
    def test_cross_axis_hole_gets_side_view_callout(self, plate_drawing):
        (name,) = [n for n in plate_drawing._named if n.startswith("hc_side")]
        assert plate_drawing._named[name].covers_diameters == (6.0,)

    @pytest.mark.timeout(120)
    def test_every_hole_gets_a_centre_mark(self, plate_drawing):
        cm = [n for n in plate_drawing._named if n.startswith("m_cm")]
        assert len(cm) == 7  # 6 z-holes in plan + 1 x-hole in side
        assert all(plate_drawing._named[n].is_centerline for n in cm)

    @pytest.mark.timeout(120)
    def test_sheet_is_lint_clean(self, plate_drawing):
        issues = [i for i in plate_drawing.lint() if i.severity != "info"]
        assert [i.code for i in issues] == []

    @pytest.mark.timeout(60)
    def test_bore_callout_elbow_at_boundary_without_section_line(self):
        # When no section line is placed (no cbore/spotface/blind holes) the
        # plan-view elbow must sit at the view boundary, not past it — the shaft
        # must not cross the view outline (#127).
        part = Box(80, 60, 10) - Pos(25, 15, 0) * Cylinder(4, 10)
        dwg = build_drawing(part)
        assert "section_line" not in dwg._named
        hc = dwg._named.get("hc_plan0")
        assert hc is not None
        plan_right = (
            dwg._analysis.PV_X + (dwg._analysis.bb.max.X - dwg._analysis.cx) * dwg._analysis.SCALE
        )
        elbow_x = hc.elbow[0]
        assert abs(elbow_x - plan_right) < 0.5  # elbow at boundary, not past it

    @pytest.mark.timeout(60)
    def test_through_holes_group_across_wall_thicknesses(self):
        # The same drill through a 10mm and a 7.5mm wall is one "2× ø5 THRU"
        # callout — through specs group regardless of depth.
        part = (
            Box(80, 40, 10)
            - Pos(20, 0, 5) * Box(40, 40, 5)
            - Pos(-20, 0, 0) * Cylinder(2.5, 10)
            - Pos(20, 0, -1.25) * Cylinder(2.5, 7.5)
        )
        dwg = build_drawing(part)
        assert len([n for n in dwg._named if n.startswith("hc_")]) == 1

    @pytest.mark.timeout(60)
    def test_two_front_view_specs_fit_below_the_view(self):
        # The title block only constrains rows that reach its x-range, so
        # the strip below the front view holds multiple callouts (review
        # round 1: the old veto blanked the whole strip on A4).
        part = (
            Box(80, 40, 30)
            - Pos(-20, 0, 5) * Cylinder(2.5, 50, rotation=(90, 0, 0))
            - Pos(25, 0, -5) * Cylinder(4, 50, rotation=(90, 0, 0))
        )
        dwg = build_drawing(part)
        assert len([n for n in dwg._named if n.startswith("hc_front")]) == 2
        # Both side-drilled holes are now located (#225 fixed), so the sheet is
        # fully lint-clean — no filtered feature_not_located warning.
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(60)
    def test_all_distinct_bores_get_callouts(self):
        # #36: no per-view callout cap — six distinct-diameter holes in a row
        # all get callouts (previously capped at the four largest), and nothing
        # is dropped because they fit.
        part = Box(120, 80, 10)
        for i, r in enumerate([1, 1.5, 2, 2.5, 3, 4]):
            part = part - Pos(-50 + i * 20, 0, 0) * Cylinder(r, 10)
        dwg = build_drawing(part)
        covered = set()
        for name, ann in dwg._named.items():
            if name.startswith("hc_"):
                covered.update(ann.covers_diameters)
        assert covered == {2.0, 3.0, 4.0, 5.0, 6.0, 8.0}
        assert "callout_dropped" not in {i.code for i in dwg.lint()}

    @pytest.mark.timeout(60)
    def test_rotational_part_keeps_leader_annotations(self):
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert "ldr_z0" in dwg._named
        assert not any(n.startswith("hc_") for n in dwg._named)
        # the central bore still gets a centre mark in the plan view
        assert any(n.startswith("m_cm") and dwg._anno_view.get(n) == "plan" for n in dwg._named)

    @pytest.mark.timeout(60)
    def test_plan_bore_leaders_elbow_outside_view(self):
        # Bore callout elbows must sit at or beyond the plan view right boundary
        # (one arrow_length past it), not deep in the annotation corridor.
        # Old code used 0.6 × DIM_PAD ≈ 10.8 mm; new code uses arrow_length ≈ 2.7 mm.
        part = Box(40, 40, 20) - Pos(15, 0, 0) * Cylinder(3, 20)
        dwg = build_drawing(part)
        a = dwg._analysis
        plan_right = a.PV_X + (a.bb.max.X - a.cx) * a.SCALE
        arrow_len = dwg.draft.arrow_length
        old_corridor = 0.6 * a.DIM_PAD  # ≈ 10.8 mm — the old, oversized offset
        hc_plan_names = [n for n in dwg._named if n.startswith("hc_plan")]
        assert hc_plan_names, "Expected at least one plan-view bore callout"
        for name in hc_plan_names:
            ldr = dwg._named[name]
            assert ldr.elbow[0] >= plan_right - 1e-6, (
                f"{name}: elbow x={ldr.elbow[0]:.3f} is inside the view "
                f"(plan_right={plan_right:.3f})"
            )
            assert ldr.elbow[0] < plan_right + old_corridor, (
                f"{name}: elbow x={ldr.elbow[0]:.3f} is too far from view "
                f"(should be < plan_right + 0.6×DIM_PAD = {plan_right + old_corridor:.3f})"
            )
            # Arrowhead must still sit inside the view.
            assert ldr.tip[0] + arrow_len <= plan_right + 1e-6, (
                f"{name}: arrowhead back at {ldr.tip[0] + arrow_len:.3f} "
                f"exceeds plan_right={plan_right:.3f}"
            )

    @pytest.mark.timeout(60)
    def test_solve_strip_ys_returns_feasible_positions(self):
        from draftwright._core import _solve_strip_ys

        # Four natural positions, solver must spread them to respect min_gap=8.
        result = _solve_strip_ys([10.0, 12.0, 14.0, 16.0], min_gap=8.0, lo=0.0, hi=100.0)
        assert result is not None
        assert len(result) == 4
        for y in result:
            assert 0.0 <= y <= 100.0
        for a, b in zip(result, result[1:]):
            assert b - a >= 8.0 - 1e-9

    @pytest.mark.timeout(60)
    def test_solve_strip_ys_infeasible_returns_none(self):
        from draftwright._core import _solve_strip_ys

        # Three items need 2 × 8 = 16mm gap, but range is only 10mm.
        result = _solve_strip_ys([5.0, 10.0, 15.0], min_gap=8.0, lo=0.0, hi=10.0)
        assert result is None

    @pytest.mark.timeout(60)
    def test_solve_strip_ys_empty_input(self):
        from draftwright._core import _solve_strip_ys

        assert _solve_strip_ys([], min_gap=8.0, lo=0.0, hi=100.0) == []


class TestHolePatternAnnotations:
    """Bolt-circle and linear-array sheet furniture + count-aware lint (#92)."""

    @pytest.mark.timeout(120)
    def test_bolt_circle_gets_suffix_and_pitch_circle(self):
        import math

        part = Box(100, 100, 12) - Cylinder(10, 12)
        for i in range(6):
            ang = math.radians(60 * i + 15)
            part = part - Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(4, 12)
        dwg = build_drawing(part)
        assert any(n.startswith("bc_plan") for n in dwg._named)
        (hc8,) = [
            a
            for n, a in dwg._named.items()
            if n.startswith("hc_") and 8.0 in getattr(a, "covers_diameters", ())
        ]
        assert hc8.covers_count == 6
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_linear_array_gets_pitch_dimension(self):
        part = Box(140, 50, 10)
        for i in range(5):
            part = part - Pos(-40 + i * 20, 0, 0) * Cylinder(3, 10)
        dwg = build_drawing(part)
        assert dwg._named["dim_pitch_plan0"].label == "4× 20"
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_opposite_face_arrays_get_separate_callouts_and_pitch_dims(self):
        # Blind holes drilled from opposite faces are different machining
        # operations: two counted callouts, two (tiered) pitch dims.
        part = Box(140, 50, 14)
        for i in range(3):
            part = part - Pos(-30 + i * 20, 8, 4) * Cylinder(3, 6)
            part = part - Pos(-30 + i * 20, -8, -4) * Cylinder(3, 6)
        dwg = build_drawing(part)
        assert len([n for n in dwg._named if n.startswith("hc_plan")]) == 2
        assert len([n for n in dwg._named if n.startswith("dim_pitch_plan")]) == 2
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_top_edge_array_dimensions_above_the_plan_view(self):
        # Below the plan view sit dim_width and the front view — plan pitch
        # dims always go up, with short extension lines for top-edge rows.
        part = Box(140, 50, 10)
        for i in range(4):
            part = part - Pos(-30 + i * 20, 18, 0) * Cylinder(3, 10)
        dwg = build_drawing(part)
        dim = dwg._named["dim_pitch_plan0"]
        plan_top = dwg.views["plan"][0].bounding_box().max.Y
        assert dim.dim_level_y > plan_top
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_pitch_dim_skipped_when_off_page(self):
        # Two parallel vertical arrays on a snug layout: the second tier
        # would cross the page margin — it must skip, never force-place.
        part = Box(60, 180, 10)
        for i in range(5):
            part = part - Pos(-15, -70 + i * 35, 0) * Cylinder(3.5, 10)
        for i in range(4):
            part = part - Pos(15, -52.5 + i * 35, 0) * Cylinder(2.5, 10)
        dwg = build_drawing(part)
        assert "dim_pitch_plan0" in dwg._named
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(60)
    def test_count_mismatch_surfaces_in_lint(self):
        from build123d import Draft
        from build123d_drafting import HoleCallout

        part = Box(100, 100, 10)
        for x in (-30, -10, 10, 30):
            part = part - Pos(x, 0, 0) * Cylinder(5, 10)
        d = Draft(font_size=2.5)
        under = lint_feature_coverage(part, [HoleCallout(10, count=2, draft=d)])
        assert [i.code for i in under] == ["feature_count_mismatch"]
        assert lint_feature_coverage(part, [HoleCallout(10, count=4, draft=d)]) == []

    @pytest.mark.timeout(60)
    def test_text_labels_are_exempt_from_count_check(self):
        from build123d import Draft
        from build123d_drafting import Note

        part = Box(100, 100, 10)
        for x in (-30, 30):
            part = part - Pos(x, 0, 0) * Cylinder(5, 10)
        d = Draft(font_size=2.5)
        assert lint_feature_coverage(part, [Note("ø10 (2 PL)", (0, 0), d)]) == []

    @pytest.mark.timeout(60)
    def test_repetition_label_passes_measured_check(self):
        from build123d import Draft
        from build123d_drafting import Dimension

        from draftwright.linting import lint_drawing

        d = Draft(font_size=2.5)
        dim = Dimension((0, 0, 0), (80, 0, 0), "above", 8, d, label="4× 20")
        assert [i for i in lint_drawing([dim]) if i.code == "label_vs_measured"] == []


class TestLocationDimsAndSection:
    """Baseline location dims (#93) and auto section views (#94)."""

    @pytest.fixture(scope="class")
    def plate_drawing(self):
        # corners (a square → bolt-circle group) + centre cbore stack +
        # off-centre blind hole: refs are the BC centre (= cbore hole,
        # deduped) and the blind hole
        part = (
            Box(100, 100, 20)
            - Pos(35, 35, 0) * Cylinder(5, 20)
            - Pos(-35, 35, 0) * Cylinder(5, 20)
            - Pos(35, -35, 0) * Cylinder(5, 20)
            - Pos(-35, -35, 0) * Cylinder(5, 20)
            - Cylinder(4, 20)
            - Pos(0, 0, 7) * Cylinder(8, 6)
            - Pos(-20, -10, 6) * Cylinder(6, 8)
        )
        return build_drawing(part)

    @pytest.mark.timeout(120)
    def test_x_dims_above_the_plan_view(self, plate_drawing):
        labels = {a.label for n, a in plate_drawing._named.items() if n.startswith("m_locx")}
        assert labels == {"50", "30"}
        plan_top = plate_drawing.views["plan"][0].bounding_box().max.Y
        assert all(
            a.dim_level_y > plan_top
            for n, a in plate_drawing._named.items()
            if n.startswith("m_locx")
        )

    @pytest.mark.timeout(120)
    def test_y_dims_above_the_side_view(self, plate_drawing):
        labels = {a.label for n, a in plate_drawing._named.items() if n.startswith("m_locy")}
        assert labels == {"50", "40"}
        side_top = plate_drawing.views["side"][0].bounding_box().max.Y
        assert all(
            a.dim_level_y > side_top
            for n, a in plate_drawing._named.items()
            if n.startswith("m_locy")
        )

    @pytest.mark.timeout(120)
    def test_section_view_with_cutting_plane_markers(self, plate_drawing):
        assert "section_aa" in plate_drawing.views
        assert plate_drawing._named["section_caption"].label == "SECTION A–A"
        assert plate_drawing._named["section_line"].is_centerline
        assert plate_drawing._named["section_a_left"].label == "A"
        assert plate_drawing._named["section_a_right"].label == "A"

    @pytest.mark.timeout(120)
    def test_section_end_arrows_present(self, plate_drawing):
        # ISO 128-44: cutting-plane ends must have wings + solid filled arrowheads
        for side in ("left", "right"):
            wing = plate_drawing._named[f"section_wing_{side}"]
            arrow = plate_drawing._named[f"section_arrow_{side}"]
            # wing is a single-edge Compound (the perpendicular stub stroke)
            assert len(wing.edges()) == 1
            # arrow is a filled solid (Arrow produces faces, not open barbs)
            assert len(list(arrow.faces())) >= 1
        # wings are below the section line (tip_y < line y)
        sl_y = plate_drawing._named["section_line"].bounding_box().min.Y
        wl_y = plate_drawing._named["section_wing_left"].bounding_box().min.Y
        assert wl_y < sl_y

    @pytest.mark.timeout(120)
    def test_section_hatch_present_and_45_degrees(self, plate_drawing):
        # ISO 128-50: 45° hatching on the cut face
        assert "section_hatch" in plate_drawing._named
        hatch = plate_drawing._named["section_hatch"]
        edges = list(hatch.edges())
        assert len(edges) > 0
        # Each hatch edge should be at approximately 45° (slope ≈ 1)
        for e in edges:
            p0, p1 = e.position_at(0), e.position_at(1)
            dx, dy = p1.X - p0.X, p1.Y - p0.Y
            if abs(dx) > 0.01:
                assert abs(dy / dx - 1.0) < 0.05  # slope ≈ 1 → 45°

    @pytest.mark.timeout(120)
    def test_sheet_is_lint_clean(self, plate_drawing):
        assert [i for i in plate_drawing.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_through_only_plate_gets_no_section(self):
        part = Box(80, 60, 10) - Pos(20, 10, 0) * Cylinder(5, 10)
        dwg = build_drawing(part)
        assert "section_aa" not in dwg.views
        assert "section_line" not in dwg._named
        # but it still gets located
        assert any(n.startswith("m_locx") for n in dwg._named)

    @pytest.mark.timeout(120)
    def test_underside_cbore_triggers_a_section(self):
        # The issue's acceptance case: a blind cbore from the underside is
        # hidden-line-only everywhere — the section shows it as line-work.
        part = Box(80, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
        dwg = build_drawing(part)
        assert "section_aa" in dwg.views
        vis, _hid = dwg.views["section_aa"]
        assert len(vis.edges()) > 0
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_declared_blind_hole_reserves_section_layout(self):
        from draftwright.model import hole

        part = Box(40, 10, 20)
        dwg = build_drawing(
            part,
            model=[hole(diameter=4, at=(0, 0, 10), axis="z", through=False, depth=6)],
        )
        assert dwg._analysis.layout_section is True
        assert dwg.scale == 1.0
        assert "section_aa" in dwg.views

    @pytest.mark.timeout(120)
    def test_rotational_concentric_bore_does_not_reserve_section_layout(self):
        part = Cylinder(20, 20) - Cylinder(4, 20) - Pos(0, 0, 7) * Cylinder(8, 6)
        dwg = build_drawing(part)
        assert dwg._analysis.is_rotational is True
        assert dwg._analysis.layout_section is False
        assert "section_aa" not in dwg.views

    @pytest.mark.timeout(120)
    def test_section_clears_the_step_dim_ladder(self):
        # Step dims are placed before the section; the section's room check
        # must clear their labels (here: no room at all → skip, never a
        # section with a dim ladder through it).
        part = (
            Box(40, 12, 40)
            - Pos(10, 0, 20) * Box(20, 12, 40)
            - Pos(-10, 0, 0) * Cylinder(3, 40)
            - Pos(-10, 0, 16) * Cylinder(5, 8)
        )
        dwg = build_drawing(part)
        if "section_aa" in dwg.views:
            sb = dwg.views["section_aa"][0].bounding_box()
            for name, ann in dwg._named.items():
                if name.startswith("dim_step") and getattr(ann, "label_bbox", None):
                    x0, y0, x1, y1 = ann.label_bbox
                    assert not (
                        x1 > sb.min.X and x0 < sb.max.X and y1 > sb.min.Y and y0 < sb.max.Y
                    )
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_side_drilled_callouts_survive_capacity_aware_carve(self):
        # Each unpatterned side-drilled hole reserves its own row as a keep-out
        # band (#318), so its callout is pushed off its own natural Y into the
        # nearest carved segment. Four holes packed close enough together that
        # their bands merge push the callout selection past a single tight
        # segment: a per-segment-only assignment (the #381 regression) can drop
        # one that overflows its nearest segment even though a farther segment
        # has spare room — the global priority-ordered assignment must not.
        part = Box(60, 40, 80)
        for z, r in ((-20, 1.0), (-12, 1.2), (-4, 1.4), (20, 1.6)):
            part -= Pos(0, 0, z) * Cylinder(r, 60, rotation=(0, 90, 0))
        dwg = build_drawing(part)
        assert len([n for n in dwg._named if n.startswith("hc_side")]) == 4
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_fully_blocked_plan_strip_defers_instead_of_unsafe_snap(self):
        # A stepped part whose bands+obstacles carve leaves the plan view's left
        # strip with no free segment at all: unlike the band-only carve (which may
        # safely snap to the nearest strip edge), a snap here isn't rechecked
        # against every other blocking interval and can land inside a different
        # one. The bands+obstacles call must defer cleanly to the bands-only
        # baseline instead of snapping (distinct geometry from
        # test_section_clears_the_step_dim_ladder, which exercises the same
        # defer but wasn't written to assert it).
        part = (
            Box(44, 12, 44)
            - Pos(11, 0, 22) * Box(22, 12, 44)
            - Pos(-11, 0, 0) * Cylinder(3, 44)
            - Pos(-11, 0, 18) * Cylinder(5, 8)
        )
        dwg = build_drawing(part)
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(120)
    def test_linear_array_locates_its_nearest_member(self):
        # The baseline dim goes to the hole nearest the datum corner; the
        # pitch dim chains the rest outward.
        part = Box(100, 50, 10)
        for x in (-30, -10, 10, 30):
            part = part - Pos(x, 0, 6) * Cylinder(4, 8)
        dwg = build_drawing(part)
        labels = sorted(a.label for n, a in dwg._named.items() if n.startswith("m_locx"))
        assert labels == ["20"]

    @pytest.mark.timeout(120)
    def test_section_letters_clear_the_bolt_circle(self, plate_drawing):
        # The corner-hole bolt circle sweeps wider than the part; the
        # cutting-plane letters must sit outside it (lint flags the overlap
        # otherwise).
        codes = [i.code for i in plate_drawing.lint() if i.severity != "info"]
        assert "label_centerline_overlap" not in codes

    @pytest.mark.timeout(120)
    def test_y_dims_tier_past_side_pitch_dims(self):
        # An x-axis array's pitch dim lives above the side view too — the
        # Y-location ladder must start beyond it, not on top of it.
        part = Box(60, 40, 30) - Pos(0, 5, 11) * Cylinder(3, 8)
        for y in (-12, 0, 12):
            part = part - Pos(15, y, 8) * Cylinder(2, 60, rotation=(0, 90, 0))
        dwg = build_drawing(part)
        locy = [a.dim_level_y for n, a in dwg._named.items() if n.startswith("m_locy")]
        pitch = [a.dim_level_y for n, a in dwg._named.items() if n.startswith("dim_pitch_side")]
        assert locy and pitch
        assert min(abs(ly - py) for ly in locy for py in pitch) >= 8

    @pytest.mark.timeout(120)
    def test_pmi_compound_draws_the_solid_only(self):
        # AP242 STEP with PMI imports as a Compound of solid + annotation
        # geometry (plane border wires, leader curves). The drawing is of
        # the solids: no phantom rectangles in the views, no bbox inflation
        # corrupting the scale and envelope dims, and the section cut works.
        solid = Box(80, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
        pmi = Edge.make_line((-80, 0, 40), (80, 0, 40))  # well outside the part
        part = Compound(children=[solid, pmi])
        dwg = build_drawing(part)
        assert "section_aa" in dwg.views
        assert dwg._named["dim_height"].label == "20"  # not the PMI z-extent
        # the views contain no line-work above the solid's top
        for vis, hid in dwg.views.values():
            assert vis.bounding_box().size.Y < 200  # sanity: no 160mm phantom

    @pytest.mark.timeout(60)
    def test_rotational_part_gets_neither(self):
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert "section_aa" not in dwg.views
        assert not any(n.startswith(("m_loc", "dim_loc")) for n in dwg._named)


class TestIsRotational:
    def test_plain_cylinder(self):
        assert _is_rotational(30.0, 30.0, 30.0, 0.0)

    def test_prismatic_envelope(self):
        assert not _is_rotational(100.0, 60.0, 24.0, 0.0)

    def test_small_boss_on_square_plate(self):
        assert not _is_rotational(100.0, 100.0, 40.0, 0.0)

    def test_off_centre_boss(self):
        assert not _is_rotational(100.0, 100.0, 84.0, 8.0)

    def test_no_external_cylinder(self):
        # Bores never qualify as an OD — od_diam is None for hole-only parts
        assert not _is_rotational(100.0, 100.0, None, 0.0)

    @pytest.mark.timeout(60)
    def test_square_plate_with_big_bore_is_prismatic(self):
        # ø85 bore in a 100-square plate: fills the envelope and is
        # concentric, but it is a hole — not an OD.
        part = Box(100, 100, 10) - Cylinder(42.5, 12)
        dwg = build_drawing(part)
        assert "dim_od" not in dwg._named

    @pytest.mark.timeout(60)
    def test_off_centre_bore_is_prismatic(self):
        part = Box(100, 100, 20) - Pos(8, 0, 0) * Cylinder(42, 30)
        dwg = build_drawing(part)
        assert "dim_od" not in dwg._named

    @pytest.mark.timeout(60)
    def test_mirrored_turned_part_stays_rotational(self):
        # Mirroring flips face orientations AND the cylinder frame handedness;
        # the external/bore split must survive it.
        from build123d import Plane, mirror

        part = mirror(Cylinder(30, 40) - Cylinder(10, 40), about=Plane.XZ)
        z_cyls, _ = analyse_cylinders(part)
        flags = {c["diameter"]: c["external"] for c in z_cyls}
        assert flags[60.0] is True and flags[20.0] is False
        dwg = build_drawing(part)
        assert "dim_od" in dwg._named

    @pytest.mark.timeout(60)
    def test_dim_od_uses_the_external_cylinder(self):
        # An internal recess wider than the boss must not be labelled as the
        # OD: dim_od comes from the classified external cylinder.
        part = (
            Box(100, 100, 20)
            + Pos(0, 0, 20) * Cylinder(42.5, 20)
            - Pos(0, 0, -7.5) * Cylinder(45, 5)
        )
        dwg = build_drawing(part)
        assert dwg._named["dim_od"].label == "ø85"

    @pytest.mark.timeout(60)
    def test_unrounded_od_does_not_duplicate_a_bore_leader(self, monkeypatch):
        # analyse_cylinders rounds diameters at source today, which masks the
        # #86 scenario — but the OD/bore exclusion must not depend on that:
        # feature records may carry raw OCCT diameters after the #87 lift.
        # With an unrounded OD (59.9999999 vs the dedup'd 60.0), a float !=
        # leaks the OD into the bore leaders as a duplicate ø60 callout.
        import importlib

        md = importlib.import_module("draftwright.analysis")
        real = md.analyse_cylinders

        def unrounded(part):
            z_cyls, cross_cyls = real(part)
            for c in z_cyls:
                if c["external"]:
                    c["diameter"] = 59.9999999
            return z_cyls, cross_cyls

        monkeypatch.setattr(md, "analyse_cylinders", unrounded)
        dwg = build_drawing(Cylinder(30, 40) - Cylinder(10, 40))
        assert dwg._named["dim_od"].label == "ø60"
        leader_labels = [a.label for n, a in dwg._named.items() if n.startswith("ldr_z")]
        assert leader_labels == ["ø20"]

    @pytest.mark.timeout(60)
    def test_lint_reuses_build_drawing_cylinder_analysis(self, monkeypatch):
        # build_drawing seeds the cache, so lint()/export() must not re-scan
        # the solid with analyse_cylinders.
        import importlib

        # (the package re-exports the make_drawing *function*, shadowing the
        # submodule attribute, so plain `import ... as md` grabs the function)
        md = importlib.import_module("draftwright.analysis")

        dwg = build_drawing(Box(30, 20, 10))
        calls = {"n": 0}
        real = md.analyse_cylinders

        def counting(part):
            calls["n"] += 1
            return real(part)

        monkeypatch.setattr(md, "analyse_cylinders", counting)
        dwg.lint()
        dwg.lint()
        assert calls["n"] == 0


# ---------------------------------------------------------------------------
# Layout-overfitting regression tests (issue #13)
#
# The fixtures above exercise the prismatic path well but leave the turned
# path and several hard-coded thresholds under-tested — which is how the
# overfitting in #10–#12 went unnoticed. These cases pin the *general*
# behaviour the algorithm should have. Where current `main` does not yet
# meet it, the test is marked xfail(strict=True) so it auto-flags (xpass)
# the moment the corresponding fix lands.
# ---------------------------------------------------------------------------


class TestTurnedPlusDrilledFlange:
    """A flange is turned (square envelope, dominant OD) yet carries discrete
    off-axis holes — the most common turned-and-drilled part. The binary
    turned/prismatic split (#10) classifies it rotational and then withholds
    every hole callout, location dim, and bolt-circle furniture, leaving the
    bolt holes with bare centre marks.
    """

    @staticmethod
    def _flange():
        # ø100 × 20 disc, ø30 central bore, 6 × ø8 holes on an ø80 bolt circle.
        flange = Cylinder(50, 20) - Cylinder(15, 20)
        for i in range(6):
            ang = 2 * math.pi * i / 6
            flange -= Pos(40 * math.cos(ang), 40 * math.sin(ang), 0) * Cylinder(4, 20)
        return flange

    @pytest.mark.timeout(60)
    def test_flange_classifies_rotational_with_od(self):
        # The turned base set is correct today and must stay so.
        dwg = build_drawing(self._flange())
        assert dwg._analysis.is_rotational
        assert "dim_od" in dwg._named
        assert "centerline_front" in dwg._named

    @pytest.mark.timeout(60)
    def test_flange_composes_od_with_bolt_circle_furniture(self):
        dwg = build_drawing(self._flange())
        # Turned base set — already works.
        assert "dim_od" in dwg._named
        # Feature-driven furniture for the bolt circle — withheld today.
        assert any(n.startswith("hc_") for n in dwg._named), "expected hole callouts"
        assert any(n.startswith("m_loc") for n in dwg._named), "expected location dims"
        assert any(n.startswith("bc_") for n in dwg._named), "expected bolt-circle furniture"


class TestTurnedMultiBoreOverflow:
    """A turned part with 4+ distinct concentric bores. The leader stack caps
    at three (`bores[:3]`); the overflow must not vanish silently — it should
    be annotated or surfaced through the coverage lint (#10).
    """

    @staticmethod
    def _telescoping():
        # ø80 OD with four concentric counterbore steps: ø60 / ø44 / ø30 / ø16.
        part = Cylinder(40, 80)
        part -= Pos(0, 0, 30) * Cylinder(30, 20)
        part -= Pos(0, 0, 10) * Cylinder(22, 30)
        part -= Pos(0, 0, -10) * Cylinder(15, 30)
        part -= Pos(0, 0, -30) * Cylinder(8, 20)
        return part

    @pytest.mark.timeout(60)
    def test_no_bore_silently_dropped(self):
        dwg = build_drawing(self._telescoping())
        a = dwg._analysis
        bores = {d for d in a.z_diams if d != a.od_diam}
        assert bores == {60.0, 44.0, 30.0, 16.0}
        annotated = {
            float(ann.label.lstrip("ø")) for n, ann in dwg._named.items() if n.startswith("ldr_z")
        }
        # Acceptance (#10): annotate all, or surface the overflow via lint —
        # never drop a bore with no trace.
        if annotated != bores:
            assert any(i.code == "feature_not_dimensioned" for i in dwg.lint()), (
                f"bores {bores - annotated} dropped with no lint coverage"
            )


class TestStepHeightThreshold:
    """The step-height gate dimensions a step only when it projects to ≥20 mm
    on the page (`(z - bb.min.Z) * SCALE >= 20`). That page-mm cutoff is
    incidental: a genuine, well-separated step should be dimensioned whatever
    its scaled height (#13).
    """

    @staticmethod
    def _stepped(base_h):
        # Prismatic two-level block: a base of height ``base_h`` (bottom at
        # z=0) with a smaller platform on top. The single interior step face
        # sits ``base_h`` above the part bottom, so at 1:1 it projects to
        # exactly ``base_h`` mm on the page.
        base = Pos(0, 0, base_h / 2) * Box(100, 100, base_h)
        platform = Pos(0, 0, base_h + 5) * Box(60, 60, 10)
        return base + platform

    @pytest.mark.timeout(60)
    def test_step_above_page_gate_is_dimensioned(self):
        # 21 mm of page height — dimensioned. Guards the gate's upper side.
        dwg = build_drawing(self._stepped(21), scale=1.0, page="A2")
        assert any(n.startswith("dim_step") for n in dwg._named)

    @pytest.mark.timeout(60)
    def test_real_step_just_below_page_gate_still_dimensioned(self):
        dwg = build_drawing(self._stepped(19), scale=1.0, page="A2")
        assert any(n.startswith("dim_step") for n in dwg._named)


# ---------------------------------------------------------------------------
# Degenerate near-zero-radius arc sanitisation (CTC-02 "black line" fix)
# ---------------------------------------------------------------------------


class TestSanitizeSvgArcs:
    """build123d's ExportSVG writes a circle seen edge-on as an elliptical arc
    with a vanishing minor radius (ry ~ 1e-7). Renderers blow that up into a
    spurious full-page line. sanitize_svg_arcs rewrites such arcs as the straight
    line segments they actually are, leaving real-radius arcs untouched."""

    def _write(self, tmp_path, body):
        from pathlib import Path

        p = Path(tmp_path) / "t.svg"
        p.write_text(f'<svg><g id="part">{body}</g></svg>', encoding="utf-8")
        return str(p)

    @pytest.mark.timeout(30)
    def test_degenerate_arc_rewritten_to_line(self, tmp_path):
        from pathlib import Path

        from draftwright.export import sanitize_svg_arcs

        f = self._write(
            tmp_path, '<path d="M 441.547 224.55 A 3.65627 5.88651e-7 90.0 0 0 441.547 222.627" />'
        )
        n = sanitize_svg_arcs(f)
        out = Path(f).read_text(encoding="utf-8")
        assert n == 1
        assert "L 441.547 222.627" in out
        assert " A " not in out  # the degenerate arc command is gone

    @pytest.mark.timeout(30)
    def test_real_radius_arc_preserved(self, tmp_path):
        from pathlib import Path

        from draftwright.export import sanitize_svg_arcs

        arc = '<path d="M 10 10 A 5.0 5.0 0 0 1 20 20" />'
        f = self._write(tmp_path, arc)
        n = sanitize_svg_arcs(f)
        assert n == 0
        assert "A 5.0 5.0 0 0 1 20 20" in Path(f).read_text(encoding="utf-8")


class TestSilhouetteCircleRefit:
    """Imported-STEP turned features (and concentric arc features like gear-tooth
    tips) project via HLR as faceted BSpline silhouette polylines, not true
    circles (#67). ``add_view`` refits any silhouette whose vertices are
    equidistant from a recognised revolution axis back to an exact circle/arc, so
    DXF carries CIRCLE entities and feature radii read exactly. A silhouette with
    no supporting revolution axis is left untouched."""

    @staticmethod
    def _nurbs(shape):
        # NurbsConvert erases analytic surface types (Cylinder -> BSplineSurface),
        # mimicking a STEP whose turned features come in as NURBS — and forcing
        # the silhouette to project as a spline rather than a native circle.
        from build123d import Solid
        from OCP.BRepBuilderAPI import BRepBuilderAPI_NurbsConvert

        return Solid(BRepBuilderAPI_NurbsConvert(shape.wrapped, True).Shape())

    @staticmethod
    def _circle_radii(view_compound):
        from build123d import GeomType

        return sorted(
            round(e.radius, 2) for e in view_compound.edges() if e.geom_type == GeomType.CIRCLE
        )

    @pytest.mark.timeout(120)
    def test_analytic_revolution_silhouette_is_circle(self):
        # Baseline: build123d already recovers an analytic cylinder's on-axis
        # silhouette as a circle. The refit pass must not regress this.
        from build123d import GeomType

        dwg = build_drawing(Cylinder(8, 30), page="A4")
        vis, _ = dwg.views["plan"]
        assert any(e.geom_type == GeomType.CIRCLE for e in vis.edges())

    @pytest.mark.timeout(120)
    def test_concentric_nurbs_ring_silhouette_refit_to_circle(self):
        # The inner analytic cylinder supplies the Z axis; the outer NURBS ring's
        # silhouette (a faceted BSpline at R18) is concentric, so it refits to an
        # exact circle at the true radius instead of staying a spline.
        inner = Cylinder(5, 12)
        outer = self._nurbs(Cylinder(18, 4))
        dwg = build_drawing(Compound([inner, outer]), page="A4")
        vis, _ = dwg.views["plan"]
        radii = [round(r / dwg.scale, 2) for r in self._circle_radii(vis)]
        assert 18.0 in radii  # outer NURBS rim recovered as an exact circle
        assert 5.0 in radii  # inner analytic bore

    @pytest.mark.timeout(120)
    def test_no_axis_silhouette_left_untouched(self):
        # A lone NURBS cylinder has no recognised revolution face (NurbsConvert
        # erased its analytic type), so there is no axis to refit against. The
        # silhouette must stay a spline rather than fabricate a circle.
        from build123d import GeomType

        part = self._nurbs(Cylinder(8, 30))
        dwg = build_drawing(part, page="A4")
        vis, _ = dwg.views["plan"]
        assert all(e.geom_type != GeomType.CIRCLE for e in vis.edges())


# ---------------------------------------------------------------------------
# Lint summary + surfacing of build-time annotation drops (#32)
# ---------------------------------------------------------------------------


class TestLintSummaryAndDrops:
    def test_summary_shape_is_consistent_with_lint(self):
        from build123d import Box, Cylinder

        from draftwright import build_drawing

        dwg = build_drawing(Box(80, 60, 20) - Cylinder(5, 20))
        issues = dwg.lint()
        s = dwg.lint_summary()

        assert set(s) == {
            "passed",
            "score",
            "errors",
            "warnings",
            "infos",
            "by_code",
            "geometry_issues",
            "issues",
        }
        assert s["errors"] + s["warnings"] + s["infos"] == len(issues)
        assert s["passed"] is (s["errors"] == 0)
        assert 0.0 <= s["score"] <= 1.0
        assert sum(s["by_code"].values()) == len(issues)
        assert len(s["issues"]) == len(issues)
        # A single-hole plate doesn't overflow the per-view callout cap.
        assert "callout_dropped" not in s["by_code"]

    def test_recorded_build_issue_surfaces_and_counts(self):
        from build123d import Box

        from draftwright import build_drawing

        dwg = build_drawing(Box(60, 40, 30))
        before = dwg.lint_summary()
        dwg._record_build_issue("warning", "callout_dropped", "synthetic drop")

        codes = {i.code for i in dwg.lint()}
        assert "callout_dropped" in codes

        after = dwg.lint_summary()
        assert after["warnings"] == before["warnings"] + 1
        assert after["by_code"]["callout_dropped"] == 1
        # callout_dropped is a geometry-aware code, so it lifts that count too.
        assert after["geometry_issues"] == before["geometry_issues"] + 1

    def test_dropped_callout_diameter_excluded_from_feature_lint(self):
        # The de-dup contract: a diameter recorded as a dropped callout is
        # excluded from feature_not_dimensioned, so a callout the layout could
        # not place (#36) is surfaced once (as callout_dropped) and not
        # double-reported.
        from build123d import Box, Cylinder

        from draftwright.make_drawing import lint_feature_coverage

        part = Box(60, 40, 20) - Cylinder(5, 20)  # one undimensioned ø10 bore
        base = lint_feature_coverage(part, [])
        assert any(i.code == "feature_not_dimensioned" for i in base)
        excluded = lint_feature_coverage(part, [], exclude=[10.0])
        assert not any(i.code == "feature_not_dimensioned" for i in excluded)

    @pytest.mark.timeout(120)
    def test_step_dims_are_adaptive_not_capped(self):
        # #36: no fixed 3-step cap; #45: five equal ledges form a uniform
        # staircase → one representative dim_step_typ labelled "N× rise",
        # no error-severity lint.
        from build123d import Box, Pos

        from draftwright import build_drawing

        tower = Box(120, 120, 15)
        for i in range(1, 6):
            side = 120 - i * 18
            tower += Pos(0, 0, i * 15) * Box(side, side, 15)
        dwg = build_drawing(tower)
        assert "dim_step_typ" in dwg._named, "uniform staircase should get a TYP dim"
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    def test_legible_steps_gate_drops_closely_spaced(self):
        # #41: a step is dimensioned only if tall enough from the base AND at
        # least _MIN_STEP_SEP_MM (page-mm) above the previously kept step;
        # closely-spaced shoulders are dropped (surfaced via lint), too-short
        # ones are silently omitted.
        from draftwright._core import (
            _MIN_STEP_DIM_MM,
            _MIN_STEP_SEP_MM,
            _legible_steps,
        )

        base = _MIN_STEP_DIM_MM + 5.0  # all comfortably tall enough from z=0
        zs = [base, base + 0.5, base + 1.0, base + _MIN_STEP_SEP_MM + 1.0]
        kept, n_too_close = _legible_steps(zs, 0.0, scale=1.0)
        assert kept == [base, base + _MIN_STEP_SEP_MM + 1.0]
        assert n_too_close == 2
        # A sub-legible step (too short to carry a label) is omitted, not dropped.
        kept2, n2 = _legible_steps([1.0, base], 0.0, scale=1.0)
        assert kept2 == [base]
        assert n2 == 0

    @pytest.mark.timeout(120)
    def test_location_dims_are_adaptive_not_capped(self):
        # #36: location dims have no fixed cap. Six scattered holes (distinct X
        # and Y, varied diameters so no array collapses them) get far more than
        # the old cap of four location dims, with nothing dropped — they fit.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        plate = Box(140, 90, 8)
        for x, y, r in [
            (-55, -35, 2.0),
            (-33, -12, 2.5),
            (-11, 15, 3.0),
            (12, -20, 3.5),
            (34, 28, 2.0),
            (55, 5, 2.5),
        ]:
            plate -= Pos(x, y, 0) * Cylinder(r, 8)
        dwg = build_drawing(plate)
        n_loc = len([n for n in dwg._named if n.startswith(("m_locx", "m_locy"))])
        assert n_loc > 4, f"expected adaptive >4 location dims, got {n_loc}"
        assert "location_ref_dropped" not in {i.code for i in dwg.lint()}

    def test_legible_locations_gate_drops_closely_spaced(self):
        # #43: a location is dimensioned only if it is at least _MIN_LOC_SEP_MM
        # (page-mm) from the previously kept one; closer ones read as one busy
        # cluster and are dropped (surfaced via lint).
        from draftwright._core import _MIN_LOC_SEP_MM
        from draftwright.annotations.holes import _legible_locations

        sep = _MIN_LOC_SEP_MM
        positions = [0.0, 1.0, 2.0, sep + 2.0, sep + 2.5, 2 * sep + 5.0]
        kept, n_too_close = _legible_locations(positions, scale=1.0)
        assert kept == [0.0, sep + 2.0, 2 * sep + 5.0]
        assert n_too_close == 3
        # At a larger scale the same world spacing reads fine — nothing dropped.
        kept2, n2 = _legible_locations([0.0, 1.0, 2.0], scale=10.0)
        assert kept2 == [0.0, 1.0, 2.0]
        assert n2 == 0

    @pytest.mark.timeout(120)
    def test_location_tower_trimmed_to_legible_set(self):
        # #43: many unpatterned holes with near-coincident X/Y positions trim to
        # a legible set; the rest surface as location_ref_dropped, no error lint.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        plate = Box(80, 60, 8)
        pts = [
            (-30, -20),
            (-28, 18),
            (-26, -5),
            (-10, 22),
            (-8, -22),
            (6, 10),
            (9, -15),
            (24, 20),
            (27, -8),
            (30, 4),
        ]
        for x, y in pts:
            plate -= Pos(x, y, 0) * Cylinder(1.2, 8)
        dwg = build_drawing(plate)
        codes = {i.code for i in dwg.lint()}
        n_locx = len([n for n in dwg._named if n.startswith("m_locx")])
        n_locy = len([n for n in dwg._named if n.startswith("m_locy")])
        assert "location_ref_dropped" in codes  # closely-spaced refs were trimmed
        assert not any(i.severity == "error" for i in dwg.lint())
        # The kept set is strictly fewer than the ten holes per axis.
        assert 0 < n_locx < 10
        assert 0 < n_locy < 10

    @pytest.mark.timeout(120)
    def test_location_gate_ignores_datum_edge_hole(self):
        # #43 follow-up: a hole on the datum edge is never dimensioned (its dim is
        # ~zero), so the gate must not anchor a cluster on it and drop a real
        # neighbour. Box centred at origin -> datum corner at (-40, -30).
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        part = Box(80, 60, 20)
        part -= Pos(-39.3, 0, 0) * Cylinder(0.4, 20)  # ~0.7 mm from datum_x: skipped
        part -= Pos(-36.5, 0, 0) * Cylinder(1.5, 20)  # ~2.8 mm from the edge hole
        dwg = build_drawing(part)
        # The real neighbour is dimensioned...
        assert any(n.startswith("m_locx") for n in dwg._named)
        # ...and the gate did not record a spurious X spacing drop.
        x_spacing_drops = [
            i for i in dwg.lint() if i.code == "location_ref_dropped" and "X location" in i.message
        ]
        assert x_spacing_drops == []

    @pytest.mark.timeout(120)
    def test_auto_annotate_clears_stale_build_issues(self):
        # Re-annotating starts build-time lint tracking from a clean slate:
        # stale drop records from a prior pass are cleared, not accumulated.
        # (A full second pass is not idempotent — strip cursors advance — but
        # the records always reflect only the latest pass.)
        from build123d import Box

        from draftwright import build_drawing
        from draftwright.annotate import _auto_annotate

        dwg = build_drawing(Box(60, 40, 30))
        dwg._record_build_issue("warning", "callout_dropped", "stale")
        assert any(i.message == "stale" for i in dwg._build_issues)
        _auto_annotate(dwg, dwg._analysis)
        assert not any(i.message == "stale" for i in dwg._build_issues)
        assert dwg._dropped_callout_diams == []

    def test_repeated_lint_is_stable(self):
        # lint()/lint_summary() are idempotent — repeated calls return the same
        # issues and never accumulate the build-time drop records.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        plate = Box(120, 60, 8)
        for x, r in zip((-48, -24, 0, 24, 48), (2.0, 2.5, 3.0, 3.5, 4.0)):
            plate -= Pos(x, 0, 0) * Cylinder(r, 8)
        dwg = build_drawing(plate)
        first, second = dwg.lint(), dwg.lint()
        assert len(first) == len(second)
        assert dwg.lint_summary()["by_code"] == dwg.lint_summary()["by_code"]

    def test_placement_unsatisfiable_is_error_severity(self):
        # placement_unsatisfiable (engine could not place a wanted annotation)
        # is error-severity, so it fails the `passed` gate.
        from build123d import Box

        from draftwright import build_drawing

        dwg = build_drawing(Box(60, 40, 30))
        assert dwg.lint_summary()["passed"] is True
        dwg._record_build_issue("error", "placement_unsatisfiable", "synthetic")
        s = dwg.lint_summary()
        assert s["passed"] is False
        assert s["errors"] >= 1
        assert s["by_code"]["placement_unsatisfiable"] == 1


# ---------------------------------------------------------------------------
# Layout generalisation guards (#13) — pin the *general* behaviour the
# algorithm should have on turned/hybrid parts and at the step-legibility
# boundary, so the overfitting that #10–#12/#31 removed cannot creep back.
# ---------------------------------------------------------------------------


class TestLayoutGeneralisation:
    @staticmethod
    def _lines_crossing_label(dwg, callout_name, label_bbox):
        """Horizontal lines (other than the callout's own shelf) that cross the
        callout's text box — the #305 'line through the callout text' defect. Shared
        by the coaxial-bore tests."""
        tx0, ty0, tx1, ty1 = label_bbox
        crossings = []
        for n, o in dwg._named.items():
            if n == callout_name:
                continue
            try:
                edges = list(o.edges())
            except Exception:
                continue
            for e in edges:
                vs = e.vertices()
                if len(vs) != 2:
                    continue
                (x0, y0), (x1, y1) = (vs[0].X, vs[0].Y), (vs[1].X, vs[1].Y)
                if abs(y0 - y1) < 0.05 and abs(x0 - x1) > 1.0:  # a horizontal line
                    ym = (y0 + y1) / 2
                    xa, xb = min(x0, x1), max(x0, x1)
                    if ty0 + 0.3 < ym < ty1 - 0.3 and xb > tx0 + 0.3 and xa < tx1 - 0.3:
                        crossings.append((n, round(ym, 2)))
        return crossings

    @pytest.mark.timeout(120)
    def test_turned_flange_gets_both_od_and_hole_furniture(self):
        # A turned-and-drilled flange (cylinder OD + centre bore + bolt circle)
        # must get the turned base set (OD dim + centrelines) AND the drilled
        # furniture (hole callout + pitch circle) — not one or the other. This
        # is the feature-presence composition from #10, on a genuinely
        # rotational part rather than a prismatic plate.
        import math

        from build123d import Cylinder, Pos

        from draftwright import build_drawing

        flange = Cylinder(radius=40, height=10) - Cylinder(radius=8, height=10)
        for i in range(6):
            ang = math.radians(60 * i)
            flange -= Pos(28 * math.cos(ang), 28 * math.sin(ang), 0) * Cylinder(2.5, 10)
        dwg = build_drawing(flange)

        assert dwg._analysis.is_rotational, "flange should classify as rotational"
        # Turned base set.
        assert "dim_od" in dwg._named
        assert "centerline_front" in dwg._named
        assert "centerline_side" in dwg._named
        # Drilled furniture.
        assert any(n.startswith("hc_") for n in dwg._named), "expected a hole callout"
        assert any(n.startswith("bc_") for n in dwg._named), "expected a pitch circle"
        # No error-severity lint (warnings tolerated).
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    @pytest.mark.timeout(120)
    def test_turned_flange_dimensions_all_its_bores(self):
        # #36: no per-view callout cap — a turned part with five distinct bores
        # gets a callout for every one (was capped at four largest), more than
        # the old cap, with nothing dropped because they fit.
        import math

        from build123d import Cylinder, Pos

        from draftwright import build_drawing

        flange = Cylinder(radius=45, height=10) - Cylinder(radius=8, height=10)
        for i, r in enumerate((2.0, 2.5, 3.0, 3.5, 4.0)):
            ang = math.radians(72 * i)
            flange -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(r, 10)
        dwg = build_drawing(flange)

        n_callouts = len([n for n in dwg._named if n.startswith("hc_")])
        assert n_callouts > 4, f"expected adaptive >4 callouts, got {n_callouts}"
        assert "callout_dropped" not in {i.code for i in dwg.lint()}

    def test_coaxial_bore_callout_clears_centre_axis(self):
        # #305: a coaxial axial bore on the round (end) view must be leadered OFF
        # the view's horizontal centre axis. Led out along it, the centre mark and
        # the bore's own location-dim extension line run straight through the
        # "⌀… ↓…" callout text (a drafting defect). Assert no horizontal line
        # crosses the callout text box.
        from build123d import BuildPart, Cylinder, Hole, Rotation

        from draftwright import build_drawing

        with BuildPart() as p:
            Cylinder(radius=6, height=20)
            Hole(0.8, depth=8)  # coaxial axial bore: ⌀1.6, depth 8
        dwg = build_drawing(Rotation(0, 90, 0) * p.part, scale=2.0)  # axis along X

        hc = [(n, o) for n, o in dwg._named.items() if n.startswith("hc_side")]
        assert hc, "expected a bore callout on the round (side) view"
        name, leader = hc[0]
        crossings = self._lines_crossing_label(dwg, name, leader.label_bbox)
        assert not crossings, f"line(s) cross the bore callout text: {crossings}"

    def test_coaxial_bore_callout_clears_centre_axis_on_stepped_shaft(self):
        # #305 regression: the lift must also fire for a *stepped* turned shaft (the
        # GRM-03 drive screw), which has a turned step profile but is NOT
        # is_rotational (its varying OD doesn't fill a square cross-section) — the
        # original is_rotational-only gate missed it, leaving the bore callout led
        # straight along the centre axis. Assert no horizontal line crosses the text.
        from build123d import Align, Cylinder, Pos, Rotation

        from draftwright import build_drawing

        b = Align.MIN
        part = (
            Cylinder(6, 12, align=(Align.CENTER, Align.CENTER, b))
            + Pos(0, 0, 12) * Cylinder(4, 12, align=(Align.CENTER, Align.CENTER, b))
            - Cylinder(0.8, 8, align=(Align.CENTER, Align.CENTER, b))
        )
        dwg = build_drawing(Rotation(0, 90, 0) * part, scale=2.0)
        assert dwg._analysis.prof is not None and not dwg._analysis.is_rotational

        hc = [(n, o) for n, o in dwg._named.items() if n.startswith("hc_side")]
        assert hc, "expected a bore callout on the round (side) view"
        name, leader = hc[0]
        crossings = self._lines_crossing_label(dwg, name, leader.label_bbox)
        assert not crossings, f"line(s) cross the bore callout text: {crossings}"

    def test_prismatic_central_hole_callout_not_lifted(self):
        # Scope-lock for #305: the coaxial-bore lift is gated to rotational parts.
        # A *prismatic* part's central hole stays on the plan-view centre row —
        # lifting it (the over-broad first cut of this fix) regressed the section /
        # cbore layouts, because only the rotational round view carries the crossing
        # centre axis. Without the is_rotational gate this callout jumps a
        # font-height off the axis; assert it does not.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        part = Box(80, 60, 20) - Cylinder(4, 20) - Pos(10, 5, -7) * Cylinder(6, 6)
        dwg = build_drawing(part)
        assert not dwg._analysis.is_rotational
        plan_mids = [
            (o.label_bbox[1] + o.label_bbox[3]) / 2
            for n, o in dwg._named.items()
            if n.startswith("hc_plan") and getattr(o, "label_bbox", None)
        ]
        assert plan_mids, "expected plan-view hole callouts"
        assert min(abs(m - dwg._analysis.PV_Y) for m in plan_mids) < dwg.draft.font_size

    @pytest.mark.timeout(120)
    def test_step_height_legibility_threshold(self):
        # The step-height dimension gate is the legibility constant
        # (_MIN_STEP_DIM_MM), not an incidental cutoff: a shoulder whose
        # page-projected height falls just below the gate gets no step dim;
        # just above, it does. Pin the gate, not a magic millimetre value.
        #
        # Exercised on a *prismatic* stepped block: the engine's Z step-height
        # ladder (and its legibility gate) still governs prismatic parts. Turned
        # parts now route through the unified IR step-length chain instead (#223),
        # which is sized to fit rather than gated, so they no longer exercise this.
        from build123d import Box, Pos

        from draftwright import build_drawing
        from draftwright._core import _MIN_STEP_DIM_MM

        def block_with_shoulder_at(length):
            # Square (non-rotational) so it is not a turned part; lower segment
            # height == `length`, shoulder `length` above the base → legibility
            # = length * SCALE.
            return Pos(0, 0, length / 2) * Box(44, 44, length) + Pos(0, 0, length + 12.5) * Box(
                22, 22, 25
            )

        for length, expect in ((12.0, False), (13.0, True)):
            dwg = build_drawing(block_with_shoulder_at(length))
            a = dwg._analysis
            legible = length * a.SCALE >= _MIN_STEP_DIM_MM
            assert legible is expect, (
                f"length={length} scale={a.SCALE}: legibility expectation wrong"
            )
            has_step = "dim_step_0" in dwg._named
            assert has_step is expect, (
                f"length={length}: step dim present={has_step}, expected {expect}"
            )


def _crowded_shoulder_part():
    """A tall, narrow stacked-tier block whose shoulders are 3 mm apart in Z.

    At the auto sheet scale the step-legibility gate (#41) drops at least one
    shoulder (3 mm × scale < _MIN_STEP_SEP_MM), which is exactly the trigger for
    the enlarged detail view (#42). Narrow X/Y so the detail footprint fits the
    free space on the sheet.
    """
    parts = [Pos(0, 0, 3) * Box(20, 16, 6)]  # base plate
    z = 6
    for w in (16, 13, 10, 7, 5):
        h = 3
        parts.append(Pos(0, 0, z + h / 2) * Box(w, 12, h))
        z += h
    part = parts[0]
    for p in parts[1:]:
        part = part + p
    return part


@pytest.mark.timeout(120)
class TestDetailView:
    def test_detail_view_off_by_default(self):
        # detail_view=False (default) — no detail view even when shoulders are crowded.
        dwg = build_drawing(_crowded_shoulder_part())
        assert "detail_a" not in dwg.views
        assert "detail_caption" not in dwg._named
        assert not any(n.startswith("dim_detail") for n in dwg._named)

    def test_crowded_shoulders_get_a_detail_view_when_requested(self):
        from draftwright._core import _legible_steps

        dwg = build_drawing(_crowded_shoulder_part(), detail_view=True)
        a = dwg._analysis
        # Pin the trigger: the gate must actually drop at least one shoulder at
        # the chosen scale, otherwise the test is not exercising #42.
        _, n_dropped = _legible_steps(a.step_zs, a.bb.min.Z, a.SCALE)
        assert n_dropped >= 1
        # The detail view, its caption, and at least one detail step dim exist.
        assert "detail_a" in dwg.views
        assert "detail_caption_A" in dwg._named
        assert any(n.startswith("dim_detail_a_step") for n in dwg._named)
        # Drawn at a larger scale than the sheet.
        assert dwg._coords["detail_a"]._scale > a.SCALE
        # No error-severity lint introduced.
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    def test_prismatic_detail_gates_on_the_step_escalation_not_raw_legibility(self):
        # #351 PR-4b: _request_prismatic_detail previously recomputed the legibility
        # gate straight from a.step_zs as its own trigger — independent of whether
        # render_height_ladder actually dropped anything. A uniform staircase
        # (_detect_step_repeat) collapses to ONE representative dim with no drop at
        # all even when the raw z-list would look "illegible" in isolation, so the old
        # trigger could queue a spurious, unused detail view. Now it gates on the
        # "step"/"illegible" Escalation render_height_ladder emits instead.
        from types import SimpleNamespace

        from draftwright.annotations._common import Escalation
        from draftwright.annotations.sections import _request_prismatic_detail

        a = SimpleNamespace(
            step_zs=[1.0, 1.1, 1.2, 1.3],  # tightly spaced — "illegible" if recomputed raw
            bb=SimpleNamespace(min=SimpleNamespace(Z=0.0), max=SimpleNamespace(Z=2.0)),
            SCALE=1.0,
        )
        no_escalation = SimpleNamespace(_escalations=[], _detail_requests=[])
        _request_prismatic_detail(no_escalation, a)
        assert no_escalation._detail_requests == []

        with_escalation = SimpleNamespace(
            _escalations=[Escalation(kind="step", view="front", feature=None, reason="illegible")],
            _detail_requests=[],
        )
        _request_prismatic_detail(with_escalation, a)
        assert len(with_escalation._detail_requests) == 1

    def test_plain_part_gets_no_detail_view(self):
        dwg = build_drawing(Box(60, 40, 20))
        assert "detail_a" not in dwg.views
        assert "detail_caption" not in dwg._named
        assert not any(n.startswith("dim_detail") for n in dwg._named)
        assert [i for i in dwg.lint() if i.severity == "error"] == []


# ---------------------------------------------------------------------------
# Issue #45: TYP / representative dimensioning for uniform step patterns
# ---------------------------------------------------------------------------


def _uniform_staircase(n_treads=8, rise=15.0, going=20.0, width=30.0):
    """Return a staircase solid with *n_treads* treads of equal rise and going."""
    part = None
    for i in range(n_treads):
        h = (i + 1) * rise
        w = (n_treads - i) * going
        b = Pos(w / 2, 0, h / 2) * Box(w, width, h)
        part = b if part is None else part + b
    return part


class TestTypDimensioning:
    """#45: uniform staircase → single representative dim labelled N× rise."""

    def test_uniform_staircase_gets_typ_dim(self):
        dwg = build_drawing(_uniform_staircase(n_treads=8, rise=15.0))
        named = dwg._named
        assert "dim_step_typ" in named, "expected a single representative step dim"
        assert not any(k.startswith("dim_step_") and k != "dim_step_typ" for k in named)
        assert named["dim_step_typ"].label == "8× 15"
        assert "dim_height" in named
        assert [i for i in dwg.lint() if i.severity == "error"] == []

    def test_typ_label_fractional_rise(self):
        dwg = build_drawing(_uniform_staircase(n_treads=5, rise=12.5, going=18.0))
        assert "dim_step_typ" in dwg._named
        assert dwg._named["dim_step_typ"].label == "5× 12.5"

    def test_irregular_staircase_gets_per_step_dims(self):
        # Non-uniform rises → fall back to per-step ladder.
        # Build as union of full-height slabs with decreasing footprint so
        # OpenCASCADE produces horizontal tread faces at each step level.
        cum_zs = [10.0, 30.0, 40.0, 65.0, 80.0]  # deliberately irregular
        n = len(cum_zs)
        going = 20
        part = None
        for i, total_h in enumerate(cum_zs):
            w = (n - i) * going
            b = Pos(w / 2, 0, total_h / 2) * Box(w, 30, total_h)
            part = b if part is None else part + b
        dwg = build_drawing(part)
        assert "dim_step_typ" not in dwg._named
        assert any(k.startswith("dim_step_") and k != "dim_step_typ" for k in dwg._named)

    def test_two_step_part_not_detected_as_pattern(self):
        # Only 2 interior steps → below the ≥3 threshold; per-step path used.
        from draftwright.annotate import _detect_step_repeat

        step_zs = [10.0, 20.0]
        result = _detect_step_repeat(step_zs, 0.0, 30.0)
        assert result is None

    def test_detect_step_repeat_uniform(self):
        from draftwright.annotate import _detect_step_repeat

        zs = [15.0, 30.0, 45.0, 60.0, 75.0, 90.0, 105.0]
        n, rise = _detect_step_repeat(zs, 0.0, 120.0)
        assert n == 8
        assert abs(rise - 15.0) < 0.01

    def test_detect_step_repeat_nonuniform(self):
        from draftwright.annotate import _detect_step_repeat

        zs = [10.0, 25.0, 35.0, 60.0]
        assert _detect_step_repeat(zs, 0.0, 70.0) is None

    def test_detect_step_repeat_top_gap_mismatch_excluded_from_count(self):
        # When top gap doesn't match the mean rise, n = len(step_zs) not +1.
        from draftwright.annotate import _detect_step_repeat

        zs = [10.0, 20.0, 30.0]  # 3 equal interior rises of 10mm
        # top gap = 55 - 30 = 25 ≠ 10 → should NOT add 1
        n, rise = _detect_step_repeat(zs, 0.0, 55.0)
        assert n == 3
        assert abs(rise - 10.0) < 0.01

    def test_three_step_part_gets_typ_dim(self):
        # Integration: exactly 3 interior steps (the minimum threshold) → TYP path.
        dwg = build_drawing(_uniform_staircase(n_treads=4, rise=20.0))
        assert "dim_step_typ" in dwg._named
        assert not any(k.startswith("dim_step_") and k != "dim_step_typ" for k in dwg._named)


# ---------------------------------------------------------------------------
# Issues #26 + #25: dwg.features() and dwg.place_dim()
# ---------------------------------------------------------------------------


def _holed_plate():
    """80×60×20 plate: 4 corner ø10 through-holes (Z-axis) + 1 centre ø6 blind (Z-axis)."""
    return (
        Box(80, 60, 20)
        - Pos(25, 20, 0) * Cylinder(5, 20)
        - Pos(-25, 20, 0) * Cylinder(5, 20)
        - Pos(25, -20, 0) * Cylinder(5, 20)
        - Pos(-25, -20, 0) * Cylinder(5, 20)
        - Pos(0, 0, 5) * Cylinder(3, 10)
    )


def _model_signature(m):
    """Provenance-agnostic structural signature of a PartModel: orientation + sorted
    (feature-kind, count) + datum count. Byte coordinates differ across a STEP round-trip;
    the semantic structure must not."""
    kinds: dict = {}
    for f in m.features:
        kinds[f.kind] = kinds.get(f.kind, 0) + 1
    return (m.orientation, tuple(sorted(kinds.items())), len(m.datums))


class TestModel:
    """#397: dwg.model() exposes the detected ADR-0008 PartModel as the read surface."""

    def test_model_exposes_detected_features(self):
        m = build_drawing(_holed_plate()).model()
        assert m is not None
        assert m.orientation is None  # prismatic plate
        kinds = {f.kind for f in m.features}
        assert "hole" in kinds
        assert len(m.datums) >= 1

    def test_model_present_without_auto_dims(self):
        # #398 detect-hoist: detection runs in the pipeline, not the annotation pass, so a
        # manual-mode build still exposes the model — a script can dimension detected
        # features even when it suppressed the automatic ones.
        m = build_drawing(_holed_plate(), auto_dims=False).model()
        assert m is not None
        assert any(f.kind == "hole" for f in m.features)

    def test_model_identical_across_auto_and_manual(self):
        # Same detection regardless of whether dimensions were auto-placed — the model is
        # a property of the part, not the annotation pass.
        part = _holed_plate()
        assert _model_signature(build_drawing(part).model()) == _model_signature(
            build_drawing(part, auto_dims=False).model()
        )


# Annotation-name prefixes that are always owned by exactly ONE feature (never a shared
# span), so every one of them on the sheet MUST have a provenance owner. Location dims
# (m_locx/m_locy, dim_loc_*) and turned-diameter callouts (m_dia_*) are excluded from the
# blanket rule — a coordinate OR a diameter shared by two distinct features is
# intentionally unowned (#398c/#406/#412). Their owned cases are checked in dedicated tests.
_ALWAYS_OWNED = ("hc_", "bc_", "m_cm", "dim_pitch", "balloon_", "m_slot")


def _assert_drop_is_complete(dwg):
    """The #408 consistency invariant, in two non-tautological parts.

    (1) COMPLETENESS: every single-feature-owned annotation on the sheet has a provenance
    owner — this catches a render pass that stops tagging (which the drop==annotations_of
    check alone cannot, since both derive from the same name set; #410 review).

    (2) CONSISTENCY: for every feature, drop() removes exactly annotations_of() and leaves
    nothing behind. Distinct features never share an owned annotation, so dropping each in
    turn is independent."""
    reg = dwg._registry
    for name in dwg.annotations():
        if name.startswith(_ALWAYS_OWNED):
            assert reg.feature_of(name) is not None, f"{name}: feature annotation left unowned"
    for f in list(dwg.model().features):
        owned = set(dwg.annotations_of(f))
        removed = set(dwg.drop(f))
        assert removed == owned, f"{f.kind}: drop removed {removed} != annotations_of {owned}"
        assert not dwg.annotations_of(f), f"{f.kind}: annotations remain after drop"


class TestFeatureEdits:
    """#398b: first-class feature provenance — drop()/annotations_of() by feature.

    Coverage today is centre marks (the first render pass to carry provenance); slots,
    locations, callouts and diameters thread `feature` in follow-up PRs. annotations_of()
    returns exactly the covered set, so drop() is transparent about what it removes."""

    def test_annotations_of_returns_a_features_centermarks_and_locations(self):
        # A hole owns its centre mark(s), location dims (#398c, corridor-placed
        # m_locx/m_locy), and its ⌀ callout (#408, hc_).
        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        owned = dwg.annotations_of(hole)
        assert any(n.startswith("m_cm") for n in owned), "hole should own its centre mark(s)"
        assert all(n.startswith(("m_cm", "m_loc", "hc_")) for n in owned)

    def test_drop_removes_a_features_annotations(self):
        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        names = set(dwg.annotations_of(hole))
        removed = dwg.drop(hole)
        assert set(removed) == names
        assert dwg.annotations_of(hole) == {}
        for n in names:
            assert n not in dwg.annotations()  # gone from the registry + render list

    def test_drop_removes_all_slot_dims(self):
        # #398c: slot dims flow through the ADR-0009 corridor; provenance now threads it,
        # so drop(slot) removes the whole set (length + width + position).
        from build123d import Box, Mode, Pos

        part = Box(80, 60, 20) - Pos(0, 0, 0) * Box(24, 8, 30, mode=Mode.SUBTRACT)
        dwg = build_drawing(part)
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        owned = set(dwg.annotations_of(slot))
        assert owned and all(n.startswith("m_slot") for n in owned)
        assert set(dwg.drop(slot)) == owned
        assert dwg.annotations_of(slot) == {}

    def test_dimension_adds_and_tags_a_feature(self):
        # #398e: the add verb — dimension a span-carrying param (a turned step's length),
        # tagged with the feature so it pairs with drop/annotations_of.
        from build123d import Cylinder

        shaft = Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25))
        dwg = build_drawing(shaft)
        step = next(f for f in dwg.model().features if f.kind == "step")
        name = dwg.dimension(step, "length")
        assert isinstance(name, str)
        assert name in dwg.annotations()
        assert name in dwg.annotations_of(step)
        assert name in set(dwg.drop(step))
        assert name not in dwg.annotations()  # drop removed it

    def test_dimension_rejects_callout_param(self):
        # A hole/step diameter is a leader callout, not a linear dim — clear ValueError.
        from build123d import Cylinder

        dwg = build_drawing(Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        step = next(f for f in dwg.model().features if f.kind == "step")
        with pytest.raises(ValueError, match="callout"):
            dwg.dimension(step, "diameter")

    def test_dimension_slot_derives_span_and_tags(self):
        # #411: a slot's dims are value-only; dimension() derives the span from the slot
        # geometry (role= disambiguates length vs width), tags + drops it.
        from build123d import Box, Mode, Pos

        part = Box(80, 60, 20) - Pos(0, 0, 0) * Box(24, 8, 30, mode=Mode.SUBTRACT)
        dwg = build_drawing(part)
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        nl = dwg.dimension(slot, "length", role="slot_length")
        assert dwg.get_annotation(nl).label == "24"  # the long-axis span
        nw = dwg.dimension(slot, "length", role="slot_width")
        assert dwg.get_annotation(nw).label == "8"  # the width-axis span
        assert {nl, nw} <= set(dwg.annotations_of(slot))
        assert nl in dwg.drop(slot)

    def test_deferred_dimension_rebuilds_prismatic_step_height_ladder(self):
        # StepLevelFeature's rungs are a correlated ladder, not independent spans. The
        # deferred dimension intent regenerates the auto-pass ladder on a detect-only build.
        from build123d import Box, Pos

        part = Box(40, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20)
        dwg = build_drawing(part, auto_dims=False)
        step_level = next(f for f in dwg.model().features if f.kind == "step_level")

        with dwg.deferred():
            dwg.dimension(step_level, "length", role="step_height")

        assert "dim_height" in dwg.annotations()
        assert any(n.startswith("dim_step") for n in dwg.annotations())
        assert dwg._intents == []

    def test_callout_adds_a_hole_leader_and_round_trips(self):
        # #414 / #400 Ph2: the callout add verb — detect-only build, then add the hole's
        # ø leader explicitly; it is a leader-attached callout, tagged, and drops.
        from build123d_drafting.helpers import Leader

        dwg = build_drawing(_holed_plate(), auto_dims=False)
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        name = dwg.callout(hole)
        assert name.startswith("hc_")
        assert name in dwg.annotations() and name in dwg.annotations_of(hole)
        assert isinstance(dwg.get_annotation(name), Leader)  # funnels through callout_from_spec
        assert name in set(dwg.drop(hole))
        assert name not in dwg.annotations()  # drop removed it

    def test_callout_rejects_a_non_callout_feature(self):
        # A slot has no ø leader callout (a step/boss now does, #419) — clear ValueError
        # pointing at dimension().
        from build123d import Box, Mode, Pos

        part = Box(80, 60, 20) - Pos(0, 0, 0) * Box(24, 8, 30, mode=Mode.SUBTRACT)
        dwg = build_drawing(part, auto_dims=False)
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        with pytest.raises(ValueError, match="hole"):
            dwg.callout(slot)

    def test_callout_carries_a_pattern_count(self):
        # A bolt circle → one counted callout for the whole pattern, tagged to the pattern.
        import math

        from build123d import Box, Cylinder, Pos

        part = Box(120, 120, 20)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(35 * math.cos(ang), 35 * math.sin(ang), 0) * Cylinder(4, 20)
        dwg = build_drawing(part, auto_dims=False)
        pat = next(f for f in dwg.model().features if f.kind == "pattern")
        name = dwg.callout(pat)
        assert name in dwg.annotations_of(pat)
        assert name in set(dwg.drop(pat))

    def test_callout_rejects_a_foreign_feature(self):
        # #414 review: a hole from a *different* build is value-similar but not identity-equal,
        # so callout() points at model().features rather than the misleading "exposes none".
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        other = build_drawing(_holed_plate(), auto_dims=False)
        foreign = next(f for f in other.model().features if f.kind == "hole")
        with pytest.raises(ValueError, match="not from this drawing"):
            dwg.callout(foreign)

    def test_callout_rejects_a_non_ortho_view(self):
        # #414 review: "iso" is a rendered view (in _coords) but not a hole-callout view —
        # it must raise a clean ValueError, not a raw KeyError from the placement dict.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        with pytest.raises(ValueError, match="hole-callout view"):
            dwg.callout(hole, view="iso")

    def test_locate_adds_position_dims_and_round_trips(self):
        # #418 / #400 Ph2: locate() places datum-referenced X/Y position dims for a
        # Z-hole, tagged + droppable. Centre ø6 at (0,0) vs datum at bbox-min (-40,-30)
        # → X offset 40, Y offset 30.
        from build123d_drafting.helpers import Dimension

        dwg = build_drawing(_holed_plate(), auto_dims=False)
        centre = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        names = dwg.locate(centre)
        assert len(names) == 2
        assert all(isinstance(dwg.get_annotation(n), Dimension) for n in names)
        assert set(names) <= set(dwg.annotations_of(centre))
        labels = {dwg.get_annotation(n).label for n in names}
        assert labels == {"40", "30"}
        assert set(names) <= set(dwg.drop(centre))
        assert not dwg.annotations_of(centre)  # drop removed them all

    def test_locate_axes_filter(self):
        # axes=("x",) emits only the plan-X position dim.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        centre = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        names = dwg.locate(centre, axes=("x",))
        assert len(names) == 1 and names[0].startswith("m_locx")
        assert dwg.get_annotation(names[0]).label == "40"

    def test_locate_pin_marks_live_location_dims(self):
        # #511 slice 1: a user location edit can be declared pinned at creation time, so
        # later repair/finalize work sees the same pin state as if pin(name) ran after.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        centre = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        names = dwg.locate(centre, pin=True)
        assert names
        assert set(names) <= dwg._pinned

    def test_locate_dedups_coincident_members(self):
        # The 4 corner ø10 holes group into one HoleFeature (X∈{25,-25}, Y∈{20,-20});
        # locate() places one dim per distinct axis position, not one per member.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        corners = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 4)
        names = dwg.locate(corners)
        labels = sorted(dwg.get_annotation(n).label for n in names)
        # X offsets 25→65 / -25→15; Y offsets 20→50 / -20→10 — four distinct dims.
        assert labels == ["10", "15", "50", "65"]

    def test_locate_rejects_side_drilled_feature(self):
        # A side-drilled (X-axis) bore has no plan location dim — clear ValueError.
        from build123d import Box, Cylinder, Pos, Rot

        part = Box(120, 90, 40) - Pos(0, 0, 5) * Rot(0, 90, 0) * Cylinder(5, 120)
        dwg = build_drawing(part, auto_dims=False)
        bore = next(f for f in dwg.model().features if f.kind == "hole" and f.frame.axis == "x")
        with pytest.raises(ValueError, match="side-drilled"):
            dwg.locate(bore)

    def test_locate_rejects_a_linear_feature(self):
        # A turned step is not a hole/pattern — point at dimension().
        from build123d import Cylinder

        dwg = build_drawing(
            Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)), auto_dims=False
        )
        step = next(f for f in dwg.model().features if f.kind == "step")
        with pytest.raises(ValueError, match="dimension"):
            dwg.locate(step)

    def test_locate_rejects_a_foreign_feature(self):
        # A hole from a different build is not identity-equal → point at model().features.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        other = build_drawing(_holed_plate(), auto_dims=False)
        foreign = next(f for f in other.model().features if f.kind == "hole")
        with pytest.raises(ValueError, match="not from this drawing"):
            dwg.locate(foreign)

    def test_furniture_adds_hole_centre_mark(self):
        # #419: furniture() places a hole's centre mark(s), tagged + droppable.
        from build123d_drafting.helpers import CenterMark

        dwg = build_drawing(_holed_plate(), auto_dims=False)
        centre = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        names = dwg.furniture(centre)
        assert names and all(n.startswith("m_cm") for n in names)
        assert all(isinstance(dwg.get_annotation(n), CenterMark) for n in names)
        assert set(names) <= set(dwg.annotations_of(centre))
        assert set(names) <= set(dwg.drop(centre))

    def test_furniture_adds_pattern_centre_cross_and_round_trips(self):
        # A bolt circle's furniture is member centre marks + the bc_ centre-cross.
        import math

        from build123d import Box, Cylinder, Pos

        part = Box(120, 120, 20)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(35 * math.cos(ang), 35 * math.sin(ang), 0) * Cylinder(4, 20)
        dwg = build_drawing(part, auto_dims=False)
        pat = next(f for f in dwg.model().features if f.kind == "pattern")
        names = dwg.furniture(pat)
        assert any(n.startswith("bc_") for n in names)
        assert set(names) <= set(dwg.annotations_of(pat))
        assert set(names) <= set(dwg.drop(pat))
        assert not dwg.annotations_of(pat)  # drop removed them all

    def test_furniture_grid_emits_pitch_dims(self):
        # A rectangular grid's furniture includes both (n-1)× pitch dims.
        from build123d import Box, Cylinder, Pos
        from build123d_drafting.helpers import Dimension

        part = Box(140, 70, 12)
        for r in range(2):
            for c in range(4):
                part -= Pos(-37.5 + c * 25, -10 + r * 20, 0) * Cylinder(4, 12)
        dwg = build_drawing(part, auto_dims=False)
        grid = next(f for f in dwg.model().features if f.kind == "pattern")
        names = dwg.furniture(grid)
        pitch = [n for n in names if n.startswith("dim_pitch_")]
        assert len(pitch) == 2
        assert all(isinstance(dwg.get_annotation(n), Dimension) for n in pitch)
        assert set(names) <= set(dwg.annotations_of(grid))
        assert set(names) <= set(dwg.drop(grid))

    def test_furniture_rejects_a_linear_feature(self):
        # A turned step is not a hole/pattern → point at dimension().
        from build123d import Cylinder

        dwg = build_drawing(
            Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)), auto_dims=False
        )
        step = next(f for f in dwg.model().features if f.kind == "step")
        with pytest.raises(ValueError, match="dimension"):
            dwg.furniture(step)

    def test_callout_adds_a_turned_step_diameter(self):
        # #419: callout() extended to a turned step's ø leader (Z-turned → column left).
        from build123d import Cylinder
        from build123d_drafting.helpers import Leader

        dwg = build_drawing(
            Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)), auto_dims=False
        )
        step = next(f for f in dwg.model().features if f.kind == "step")
        name = dwg.callout(step)
        assert name.startswith("m_dia")
        assert isinstance(dwg.get_annotation(name), Leader)
        assert name in dwg.annotations_of(step)
        assert name in dwg.drop(step)

    def test_callout_step_x_turned_uses_row_path(self):
        # The X-turned path places m_dia_x in the row below the front view.
        from build123d import Cylinder, Rot
        from build123d_drafting.helpers import Leader

        shaft = Rot(0, 90, 0) * (Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        dwg = build_drawing(shaft, auto_dims=False)
        step = next(f for f in dwg.model().features if f.kind == "step")
        name = dwg.callout(step)
        assert name.startswith("m_dia_x")
        assert isinstance(dwg.get_annotation(name), Leader)
        assert name in dwg.drop(step)

    def test_callout_multiple_step_diameters_do_not_collide(self):
        # #419 review F1: each step gets a DISTINCT m_dia name; a second callout() must
        # not clobber the first's leader or raise a false "no room". X-turned uses the
        # row placer (no occupancy gate), so both leaders always land.
        from build123d import Cylinder, Rot

        shaft = Rot(0, 90, 0) * (
            Cylinder(20, 30)
            + Cylinder(14, 24).translate((0, 0, 27))
            + Cylinder(9, 18).translate((0, 0, 48))
        )
        dwg = build_drawing(shaft, auto_dims=False)
        steps = [f for f in dwg.model().features if f.kind == "step"]
        assert len(steps) >= 2
        names = [dwg.callout(s) for s in steps[:2]]
        assert len(set(names)) == 2, f"expected distinct names, got {names}"
        for s, n in zip(steps[:2], names, strict=False):
            assert n in dwg.annotations() and n in dwg.annotations_of(s)
        # dropping the first step leaves the second's leader intact (no clobber)
        dwg.drop(steps[0])
        assert names[1] in dwg.annotations()

    def test_section_reproduces_the_auto_section(self):
        # #420: section() adds the A–A that a counterbored hole triggers, on a
        # detect-only build (the auto-pass would draw it, but auto_dims=False skips it).
        from build123d import Box, Cylinder, Pos

        part = Box(60, 40, 20) - Cylinder(4, 30) - Pos(0, 0, 2) * Cylinder(7, 20)
        dwg = build_drawing(part, auto_dims=False)
        names = dwg.section()
        assert "section_caption" in names and "section_line" in names
        assert dwg.get_annotation("section_caption").label == "SECTION A–A"

    def test_section_is_a_noop_without_a_trigger(self):
        # A plain through-hole plate warrants no section → honest empty list.
        from build123d import Box, Cylinder, Pos

        part = Box(80, 60, 20) - Pos(20, 0, 0) * Cylinder(4, 30)
        dwg = build_drawing(part, auto_dims=False)
        assert dwg.section() == []

    def test_locate_composes_over_every_feature_without_raising(self):
        # #420 flip fix: locate() returns [] (not ValueError) when a feature's datum ref is
        # deduped/concentric — so the emitted script can call it on every hole/pattern. Here
        # the central hole coincides with the bolt-circle centre, so its ref is deduped.
        import math

        from build123d import Box, Cylinder, Pos

        part = Box(100, 100, 20)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(3, 20)
        part -= Pos(0, 0, 5) * Cylinder(5, 10)  # central hole on the bolt-circle centre
        dwg = build_drawing(part, auto_dims=False)
        holes = [f for f in dwg.model().features if f.kind in ("hole", "pattern")]
        assert len(holes) >= 2
        results = [dwg.locate(f) for f in holes]  # none may raise
        assert all(isinstance(r, list) for r in results)

    @staticmethod
    def _reconstruct(dwg):
        # The per-feature verb dispatch the #400 Ph2 emitter writes (mirrors
        # builder._feature_listing) — used to exercise the reconstruction in-process.
        for f in dwg.model().features:
            if f.kind in ("hole", "pattern"):
                dwg.callout(f)
                if f.frame.axis == "z":  # locate() is Z-axis only (side-drilled → auto-pass)
                    dwg.locate(f)
                dwg.furniture(f)
            elif f.kind in ("step", "boss"):
                if f.frame.axis in ("x", "z"):  # callout() places X/Z-turned diameters only
                    dwg.callout(f)
            elif f.kind == "step_level":
                dwg.dimension(f, "length", role="step_height")
                continue
            for p in f.parameters():
                if p.span is not None or f.kind == "slot":
                    dwg.dimension(f, p.kind, role=p.role)
        dwg.section()

    def test_deferred_reconstruction_avoids_duplicate_prismatic_step_height(self):
        # The envelope owns overall height when the generated reconstruction records it
        # explicitly; the step-level ladder must then emit only the internal rungs.
        from build123d import Box, Pos

        part = Box(60, 12, 40) - Pos(10, 0, 20) * Box(20, 12, 20)
        dwg = build_drawing(part, auto_dims=False)

        with dwg.deferred():
            self._reconstruct(dwg)

        dims = {
            n: getattr(ann, "label", None)
            for n, ann in dwg.iter_annotations()
            if n.startswith(("dim_height", "dim_length", "dim_step"))
        }
        assert "dim_height" not in dims
        assert [name for name, label in dims.items() if label == "40"] == ["dim_length1"]
        assert any(name.startswith("dim_step") for name in dims)

    def test_intent_reconstruction_is_error_free(self):
        # #400 Ph2 soft acceptance: a fully reconstructed prismatic part, after repair(),
        # has no lint ERRORS. Placement WARNINGS from the corridor-free verbs are the
        # documented #424 fidelity gap, not a failure.
        part = (
            Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40) - Pos(-20, -10, 0) * Cylinder(4, 40)
        )
        dwg = build_drawing(part, auto_dims=False)
        self._reconstruct(dwg)
        dwg.repair()
        assert dwg.lint_summary()["errors"] == 0, dwg.lint_summary()["by_code"]

    def test_intent_reconstruction_runs_on_a_side_drilled_part(self):
        # #427 review F1: a side-drilled (non-Z) bore is kind="hole" axis!="z". locate()
        # rejects it by contract (#133), so the emitter must NOT emit locate() for it —
        # else the reconstruction crashes. Exercise the (fixed) dispatch: it must not raise.
        from build123d import Box, Cylinder, Pos, Rot

        part = Box(120, 90, 40) - Pos(0, 0, 5) * Rot(0, 90, 0) * Cylinder(5, 120)
        dwg = build_drawing(part, auto_dims=False)
        assert any(f.kind == "hole" and f.frame.axis != "z" for f in dwg.model().features)
        self._reconstruct(dwg)  # must not raise on the side-drilled bore
        dwg.repair()
        assert dwg.lint_summary()["errors"] == 0, dwg.lint_summary()["by_code"]

    def test_intent_reconstruction_runs_on_a_crowded_turned_shaft(self):
        # #427 review F2: callout() on a step/boss must DEGRADE (drop the overflow leader
        # like the auto-pass), not raise "no room" — else a multi-step turned shaft's
        # reconstruction (one callout() per step) aborts. Must run to completion.
        from build123d import Cylinder

        shaft = Cylinder(24, 12)
        for k in range(1, 10):  # 10 stacked, decreasing-radius steps along Z
            shaft += Cylinder(24 - 2 * k, 12).translate((0, 0, 12 * k))
        dwg = build_drawing(shaft, auto_dims=False)
        assert sum(f.kind == "step" for f in dwg.model().features) >= 5
        self._reconstruct(dwg)  # many callout(step) calls — none may raise "no room"
        dwg.repair()  # the script must reach repair(), not abort before it

    def test_generated_script_flags_side_drilled_locate_as_a_comment(self):
        # #427 review F1: the emitted --script must gate dwg.locate(f) on a Z-axis hole —
        # a side-drilled bore gets a flagged comment, not a bare locate() that would crash.
        import tempfile
        from pathlib import Path

        from build123d import Box, Cylinder, Pos, Rot, export_step

        from draftwright.make_drawing import generate_script

        part = Box(120, 90, 40) - Pos(0, 0, 5) * Rot(0, 90, 0) * Cylinder(5, 120)
        with tempfile.TemporaryDirectory() as d:
            step = Path(d) / "sd.step"
            export_step(part, str(step))
            content = Path(generate_script(str(step), out=str(Path(d) / "sd"))).read_text()
        assert "locate() is Z-axis only" in content  # the gate fired, no bare locate() crash

    def test_intent_reconstruction_comment_drops_exactly_that(self):
        # #400 Ph2 soft acceptance: commenting one verb line drops exactly that annotation.
        # With auto_dims=False nothing is auto-drawn, so omitting callout(f) removes exactly
        # the callout — no double-dimension, no collateral on locate/furniture.
        part = Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40)
        full = build_drawing(part, auto_dims=False)
        hole = next(f for f in full.model().features if f.kind == "hole")
        full.callout(hole)
        full.locate(hole)
        full.furniture(hole)
        before = set(full.annotations())

        partial = build_drawing(part, auto_dims=False)
        h2 = next(f for f in partial.model().features if f.kind == "hole")
        partial.locate(h2)  # callout(h2) "commented out"
        partial.furniture(h2)
        dropped = before - set(partial.annotations())
        assert dropped == {n for n in before if n.startswith("hc_")}
        assert dropped, "commenting callout() should drop the hole's leader"

    def test_deferred_verbs_record_intents_without_placing(self):
        # #426 Phase 1: in deferred mode a verb records an Intent and places nothing.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())  # the detect-only build's title block, no dims
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        assert dwg.callout(hole) == ""  # nothing placed
        assert dwg.locate(hole) == []
        assert dwg.furniture(hole) == []
        assert len(dwg._intents) == 3
        assert [i.kind for i in dwg._intents] == ["callout", "locate", "furniture"]
        assert set(dwg.annotations()) == base  # recorded, nothing new drawn

    def test_deferred_context_manager_records_then_finalizes(self):
        # #426 Phase 5: `with dwg.deferred()` records inside the block (nothing placed) and
        # runs finalize() on block exit — the record-then-finalize surface the emitter uses.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        with dwg.deferred():
            dwg.callout(hole)
            dwg.locate(hole)
            dwg.furniture(hole)
            assert set(dwg.annotations()) == base  # still recording inside the block
            assert len(dwg._intents) == 3
        assert dwg._intents == []  # finalize drained them on exit
        assert dwg._defer_intents is False  # mode restored
        assert set(dwg.annotations()) - base  # annotations placed by the batch solve

    def test_deferred_block_keeps_intents_and_skips_finalize_on_raise(self):
        # #426 Phase 5: if the block raises, the recorded intents are left intact and
        # finalize() is NOT run — the error surfaces cleanly and a retry can re-drain.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        with pytest.raises(RuntimeError, match="boom"):
            with dwg.deferred():
                dwg.callout(hole)
                raise RuntimeError("boom")
        assert [i.kind for i in dwg._intents] == ["callout"]  # kept, not drained
        assert set(dwg.annotations()) == base  # finalize skipped — nothing placed
        assert dwg._defer_intents is False  # mode still restored (finally)

    def test_deferred_reconstruction_is_error_free_and_drained(self):
        # #426 Phase 5 soft acceptance: a full reconstruction through the deferred CM
        # batch-solves to a lint-error-free sheet and drains every recorded intent.
        part = (
            Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40) - Pos(-20, -10, 0) * Cylinder(4, 40)
        )
        dwg = build_drawing(part, auto_dims=False)
        with dwg.deferred():
            self._reconstruct(dwg)  # records every intent inside the block
            assert dwg._intents, "verbs must record inside the deferred block"
        assert dwg._intents == []  # finalize drained on exit
        dwg.repair()
        assert dwg.lint_summary()["errors"] == 0, dwg.lint_summary()["by_code"]

    def test_deferred_reconstruction_lint_no_worse_than_auto_pass(self):
        # #426 acceptance: a deferred reconstruction is no worse than the auto-pass — the
        # recorded intents route through the same batch solvers, so lint score is >= the
        # auto drawing's (faithful, not merely runnable).
        part = (
            Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40) - Pos(-20, -10, 0) * Cylinder(4, 40)
        )
        auto = build_drawing(part).lint_summary()
        recon = build_drawing(part, auto_dims=False)
        with recon.deferred():
            self._reconstruct(recon)
        recon.repair()
        got = recon.lint_summary()
        assert got["errors"] == 0
        assert got["score"] >= auto["score"], (got["by_code"], auto["by_code"])

    def test_finalize_replay_equals_live_placement(self):
        # #426 Phase 1: record-then-finalize == placing live (identical annotations).
        part = Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(4, 40)

        live = build_drawing(part, auto_dims=False)
        h = next(f for f in live.model().features if f.kind == "hole")
        live.callout(h)
        live.locate(h)
        live.furniture(h)

        deferred = build_drawing(part, auto_dims=False)
        base = set(deferred.annotations())
        deferred._defer_intents = True
        h2 = next(f for f in deferred.model().features if f.kind == "hole")
        deferred.callout(h2)
        deferred.locate(h2)
        deferred.furniture(h2)
        assert set(deferred.annotations()) == base  # nothing placed yet
        deferred.finalize()
        assert deferred.annotations() == live.annotations()

    def test_finalize_is_idempotent(self):
        # #426 Phase 1: finalize() twice == once — idempotent via the empty-list early-out.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        dwg._defer_intents = True
        h = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(h)
        dwg.furniture(h)
        dwg.finalize()
        once = set(dwg.annotations())
        dwg.finalize()
        assert set(dwg.annotations()) == once

    def test_finalize_is_a_noop_on_the_live_path(self):
        # #426 Phase 1: the default (non-deferred) build records nothing → finalize no-ops,
        # so the auto-pass / live-verb path is unchanged.
        dwg = build_drawing(_holed_plate())  # auto_dims=True
        assert dwg._defer_intents is False and dwg._intents == []
        before = set(dwg.annotations())
        dwg.finalize()
        assert set(dwg.annotations()) == before

    def test_export_triggers_finalize(self):
        # #426 Phase 1: export() drains recorded intents (calls finalize) before writing.
        import tempfile
        from pathlib import Path

        dwg = build_drawing(_holed_plate(), auto_dims=False)
        base = set(dwg.annotations())
        dwg._defer_intents = True
        h = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(h)
        assert set(dwg.annotations()) == base  # deferred — nothing placed
        with tempfile.TemporaryDirectory() as d:
            dwg.export(str(Path(d) / "x"))
        assert dwg.annotations()  # finalize ran during export → the callout got placed

    def test_finalize_drains_a_second_batch(self):
        # #428 review: record → finalize → record-more → finalize drains each batch —
        # idempotency is list-draining only, so a second batch is not blocked.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(hole)
        dwg.finalize()
        after_first = set(dwg.annotations())
        dwg.furniture(hole)  # a second batch recorded after the first finalize
        dwg.finalize()
        assert set(dwg.annotations()) > after_first  # the second batch placed too

    def test_finalize_is_resilient_to_a_raising_intent(self):
        # #428 review: an intent that raises at replay surfaces the error and leaves the
        # remaining intents recorded (not silently dropped), and does not brick the drawing.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.callout(hole)  # ok
        dwg.dimension(hole, "diameter")  # a hole ø is a callout → raises at replay
        dwg.furniture(hole)  # ok — must survive the raise
        with pytest.raises(ValueError, match="callout"):
            dwg.finalize()
        # the raise leaves everything not-yet-placed recorded — nothing silently dropped
        kinds = [i.kind for i in dwg._intents]
        assert "dimension" in kinds and "furniture" in kinds

    def test_finalize_routes_locations_through_the_corridor_dedup(self):
        # #426 Phase 2a: two DISTINCT holes sharing an X. The live path places a duplicate
        # m_locx (each locate() is independent); finalize routes them through the real
        # ADR-0009 corridor solve, which dedups the coincident X span to ONE dim — matching
        # the auto-pass. The crossing-free / dedup win.
        part = (
            Box(100, 80, 20) - Pos(20, 25, 0) * Cylinder(4, 30) - Pos(20, -25, 0) * Cylinder(6, 30)
        )

        live = build_drawing(part, auto_dims=False)
        for h in (f for f in live.model().features if f.kind == "hole"):
            live.locate(h)
        live_locx = [n for n in live.annotations() if n.startswith("m_locx")]

        deferred = build_drawing(part, auto_dims=False)
        deferred._defer_intents = True
        for h in (f for f in deferred.model().features if f.kind == "hole"):
            deferred.locate(h)
        deferred.finalize()
        fin_locx = [n for n in deferred.annotations() if n.startswith("m_locx")]

        assert len(fin_locx) < len(live_locx)  # corridor deduped the coincident X=20 span
        auto = build_drawing(part)  # auto_dims=True — the reference the corridor matches
        assert len(fin_locx) == len([n for n in auto.annotations() if n.startswith("m_locx")])

    def test_finalize_routes_pinned_locate_as_corridor_candidate(self):
        # #511 slice 1: a deferred user locate(pin=True) is not hand-added after layout.
        # It routes through render_locations' corridor candidates and pins the resulting
        # names after the shared solve chooses legal positions.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        hole = next(f for f in dwg.model().features if f.kind == "hole" and len(f.members) == 1)
        with dwg.deferred():
            dwg.locate(hole, pin=True)
        locs = {n for n in dwg.annotations_of(hole) if n.startswith("m_loc")}
        assert locs
        assert locs <= dwg._pinned
        assert dwg._intents == []

    def test_finalize_pins_shared_location_when_later_ref_requested_pin(self):
        # #511 review: render_locations dedups same-coordinate refs before candidate
        # creation. The pin bit must survive that dedup even when the pinned feature is
        # not the first representative chosen for the shared dimension.
        part = (
            Box(100, 80, 20) - Pos(20, 25, 0) * Cylinder(4, 30) - Pos(20, -25, 0) * Cylinder(6, 30)
        )
        dwg = build_drawing(part, auto_dims=False)
        holes = [f for f in dwg.model().features if f.kind == "hole"]
        dwg._defer_intents = True
        dwg.locate(holes[0])
        dwg.locate(holes[1], pin=True)
        dwg.finalize()
        shared_x = {
            n for n in dwg.annotations() if n.startswith("m_locx") and dwg.get_annotation(n).label
        }
        assert shared_x
        assert shared_x <= dwg._pinned

    def test_finalize_routes_pinned_dimension_as_corridor_candidate(self):
        # #511: a deferred user dimension(pin=True) is a feature intent, not a raw
        # page-coordinate placement after layout. It joins the shared strip solve, remains
        # feature-owned, and pins the placed name only after legal placement.
        dwg = build_drawing(Box(80, 50, 20), auto_dims=False)
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with dwg.deferred():
            dwg.dimension(
                env,
                "length",
                role="width",
                side="below",
                name="user_width",
                slot=12,
                pin=True,
                priority=25,
            )

        assert "user_width" in dwg.annotations_of(env)
        assert "user_width" in dwg._pinned
        assert dwg._named["user_width"]._dw_spec.side == "below"
        assert dwg._named["user_width"]._dw_spec.distance == 12
        assert dwg._intents == []

    def test_deferred_dimension_generated_names_do_not_collide_in_one_batch(self):
        # #511 review: generated names must be reserved before the corridor drain. Otherwise
        # two same-kind dimensions recorded in one deferred batch both choose dim_length0 and
        # the second add silently replaces the first.
        dwg = build_drawing(Box(80, 50, 20), auto_dims=False)
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with dwg.deferred():
            dwg.dimension(env, "length", role="width", side="below", pin=True)
            dwg.dimension(env, "length", role="depth", side="below", pin=True)

        names = {n for n in dwg.annotations_of(env) if n.startswith("dim_length")}
        assert names == {"dim_length0", "dim_length1"}
        assert names <= dwg._pinned

    def test_live_dimension_pin_pins_raw_escape_hatch_result(self):
        # #511/ADR 0012: live dimension() still uses the single-position page-coordinate
        # escape hatch, but pin=True must persist on the resulting annotation name.
        dwg = build_drawing(Box(80, 50, 20), auto_dims=False)
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        name = dwg.dimension(env, "length", role="width", name="live_width", pin=True)

        assert name == "live_width"
        assert "live_width" in dwg.annotations_of(env)
        assert "live_width" in dwg._pinned

    def test_malformed_pinned_dimension_still_surfaces_live_valueerror(self):
        # #511 review: pin=True must not make a non-linear hole diameter look corridor
        # routable. It falls through to live replay and leaves the intent recorded.
        dwg = build_drawing(_holed_plate(), auto_dims=False)
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg._defer_intents = True
        dwg.dimension(hole, "diameter", pin=True)

        with pytest.raises(ValueError, match="callout"):
            dwg.finalize()
        assert any(it.kind == "dimension" for it in dwg._intents)

    def test_finalize_honors_locate_axes_restriction(self):
        # #429 review: a recorded locate(f, axes=("x",)) must place only the X dim. The
        # per-feature corridor filter can't express an axis subset, so finalize live-replays
        # axes-restricted locates (routing only both-axes ones through the corridor).
        part = Box(100, 80, 20) - Pos(20, 15, 0) * Cylinder(4, 30)
        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.locate(hole, axes=("x",))
        dwg.finalize()
        locs = [n for n in dwg.annotations() if n.startswith("m_loc")]
        assert locs and all(n.startswith("m_locx") for n in locs)  # X only — no m_locy

    def test_finalize_replayed_axes_restricted_locate_can_pin(self):
        # #511 slice 1: axes-restricted locates intentionally bypass the shared corridor
        # filter, but their pin intent must still survive live replay during finalize.
        part = Box(100, 80, 20) - Pos(20, 15, 0) * Cylinder(4, 30)
        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.locate(hole, axes=("x",), pin=True)
        dwg.finalize()
        locs = {n for n in dwg.annotations_of(hole) if n.startswith("m_locx")}
        assert locs
        assert locs <= dwg._pinned

    def test_finalize_mixes_axes_restricted_and_both_axes_locate(self):
        # #429 review: an axes-restricted locate (live, names m_locx0) + a both-axes locate
        # (corridor) must NOT collide — the corridor names its dims against _named, so both
        # survive. Regression for the silent-overwrite bug.
        part = (
            Box(120, 80, 20)
            - Pos(-40, 25, 0) * Cylinder(4, 30)
            - Pos(40, -25, 0) * Cylinder(6, 30)
        )
        holes = lambda d: [f for f in d.model().features if f.kind == "hole"]  # noqa: E731

        live = build_drawing(part, auto_dims=False)
        hs = holes(live)
        live.locate(hs[0], axes=("x",))
        live.locate(hs[1])
        live_x = {
            live.get_annotation(n).label for n in live.annotations() if n.startswith("m_locx")
        }

        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        hs2 = holes(dwg)
        dwg.locate(hs2[0], axes=("x",))
        dwg.locate(hs2[1])
        dwg.finalize()
        fin_x = {dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_locx")}
        # both features' X location dims survive — none silently overwritten
        assert fin_x == live_x and len(fin_x) == 2

    def test_finalize_routes_slots_through_render_slots(self):
        # #426 Phase 2b: a slot's recorded size dims route through render_slots' corridor
        # placement (m_slot* names), NOT the live dim_length* replay — reaching auto-pass
        # parity. Routing the feature also regenerates its datum POSITION dim (a superset:
        # model-derived, not a recorded intent).
        part = Box(50, 30, 20) - Box(20, 8, 30)  # an enclosed through-slot (#135)

        auto = build_drawing(part)  # auto_dims=True — the reference
        auto_slot = {n for n in auto.annotations() if n.startswith("m_slot")}
        assert auto_slot, "auto-pass must place m_slot* dims"

        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        dwg.dimension(slot, "length", role="slot_width")
        dwg.dimension(slot, "length", role="slot_length")
        dwg.finalize()
        fin_slot = {n for n in dwg.annotations() if n.startswith("m_slot")}
        assert fin_slot == auto_slot  # same corridor-placed names (m_slot0_width/length/pos)
        # routed, not live-replayed: no dim_length* singles, and both intents drained
        assert not [n for n in dwg.annotations() if n.startswith("dim_length")]
        assert dwg._intents == []

    def test_finalize_malformed_slot_dimension_surfaces_the_live_valueerror(self):
        # #439: slot_ids matches only param="length" + role in (slot_width, slot_length),
        # like len_ids/dia_ids. A malformed slot dim (a param a slot has no parameter for)
        # must NOT be silently routed through render_slots — it falls through to leg-A live
        # replay, where dimension() raises the same ValueError the live (non-deferred) path
        # raises, instead of being swallowed.
        part = Box(50, 30, 20) - Box(20, 8, 30)  # an enclosed through-slot (#135)
        dwg = build_drawing(part, auto_dims=False)
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        with pytest.raises(ValueError):
            with dwg.deferred():
                dwg.dimension(slot, "diameter")  # a slot has no diameter param

    def test_finalize_slot_position_dedups_with_a_coincident_hole_location(self):
        # #426 Phase 2b: slots share the location corridor with hole locates and drain in ONE
        # solve. Here the slot's near edge (x=-10) coincides with a hole's X-location, so the
        # slot POSITION line and that hole location are the SAME datum→10 span — the #345
        # dedup collapses them (no m_slot0_pos survives; the hole's m_locx covers it). The
        # win is exact parity with the auto-pass, which only the combined single drain gives
        # (draining slots and locations separately would place the un-deduped slot position).
        part = (
            Box(60, 40, 20)
            - Box(20, 8, 30)  # slot: long_axis X, near edge x=-10
            - Pos(-10, 14, 0) * Cylinder(3, 30)  # hole X coincides with the slot near edge
            - Pos(20, 14, 0) * Cylinder(3, 30)
            - Pos(8, -14, 0) * Cylinder(3, 30)
        )
        keys = lambda d: {  # noqa: E731
            n for n in d.annotations() if n.startswith("m_slot") or n.startswith("m_loc")
        }
        auto = build_drawing(part)

        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        slot = next(f for f in dwg.model().features if f.kind == "slot")
        dwg.dimension(slot, "length", role="slot_width")
        dwg.dimension(slot, "length", role="slot_length")
        for h in (f for f in dwg.model().features if f.kind in ("hole", "pattern")):
            dwg.locate(h)
        dwg.finalize()

        # exact parity with the auto-pass: the coincident slot-position + hole-location
        # collapsed to one in the shared solve (no stray m_slot0_pos), size dims placed.
        assert keys(dwg) == keys(auto)
        assert "m_slot0_pos" not in dwg.annotations()  # deduped against the coincident hole
        assert dwg._intents == []

    def test_finalize_records_scattered_hole_coverage_without_furniture(self):
        # #426 Phase 4c: finalize routes hole callouts through _annotate_holes with
        # place_furniture=False (furniture is replayed by its own furniture() intents). The
        # scattered-hole-table COVERAGE — which plan callouts _maybe_tabulate_holes may replace
        # — used to be recorded ONLY inside _add_furniture, gated behind place_furniture, so
        # finalize never registered it. The fix records coverage at the callout emit site
        # regardless of the gate; the finalize scattered-doc set must equal the auto-pass set
        # (and be non-empty), so the resolver can find + replace those callouts. Coverage-only,
        # so the auto-pass output is unchanged (guarded by the byte-identity corpus).
        part = _multi_hole_plate()

        def docs(d):
            return {n for n in d.annotations() if d._is_scattered_hole_doc(n)}

        auto = build_drawing(part)
        assert docs(auto), "auto-pass must register scattered-hole-doc coverage"

        dwg = build_drawing(part, auto_dims=False)
        with dwg.deferred():
            for f in dwg.model().features:
                if getattr(f, "kind", None) in ("hole", "pattern"):
                    dwg.callout(f)
                    dwg.locate(f)
        assert docs(dwg) == docs(auto)  # coverage restored under place_furniture=False
        assert dwg._intents == []  # drained
        assert dwg._escalations == []  # leg D ran + cleared them for retry safety

    @staticmethod
    def _hc_ys(d):
        return sorted(
            round(d.get_annotation(n).bounding_box().center().Y, 1)
            for n in d.annotations()
            if n.startswith("hc_")
        )

    def test_finalize_routes_callouts_through_annotate_holes(self):
        # #426 Phase 3a: hole/pattern ø callouts route through the auto-pass's _annotate_holes
        # priority-drop/anchoring solve, so the finalize reconstruction reproduces the
        # auto-pass callout layout exactly (not the live per-feature corridor-free placement).
        part = (
            Box(120, 90, 20)
            - Cylinder(5, 30)  # central ø10 → anchored by the auto-pass
            - Pos(40, 30, 0) * Cylinder(3, 30)
            - Pos(-40, -30, 0) * Cylinder(4, 30)
        )
        auto = build_drawing(part)  # auto_dims=True — the reference

        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        for f in (x for x in dwg.model().features if x.kind in ("hole", "pattern")):
            dwg.callout(f)
            dwg.furniture(f)
        dwg.finalize()

        assert self._hc_ys(dwg) and self._hc_ys(dwg) == self._hc_ys(auto)  # batch == auto-pass

    def test_finalize_does_not_double_place_pattern_furniture(self):
        # #426 Phase 3a: _annotate_holes places a pattern's callout but NOT its furniture
        # (place_furniture=False) — the replayed furniture() intent owns it, so bc_ appears
        # exactly once, not doubled.
        import math

        part = Box(120, 120, 20)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(35 * math.cos(ang), 35 * math.sin(ang), 0) * Cylinder(4, 20)
        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        pat = next(f for f in dwg.model().features if f.kind == "pattern")
        dwg.callout(pat)
        dwg.furniture(pat)
        dwg.finalize()
        assert len([n for n in dwg.annotations() if n.startswith("bc_")]) == 1  # not doubled

    def test_finalize_callouts_survive_a_second_batch(self):
        # #430 review: the batch callout naming is _named-aware, so a second finalize batch
        # doesn't re-emit hc_plan0 and clobber the first batch's callout (cross-batch seam).
        part = (
            Box(120, 80, 20)
            - Pos(-40, 25, 0) * Cylinder(4, 30)
            - Pos(40, -25, 0) * Cylinder(6, 30)
        )
        dwg = build_drawing(part, auto_dims=False)
        holes = [f for f in dwg.model().features if f.kind == "hole"]
        dwg._defer_intents = True
        dwg.callout(holes[0])
        dwg.finalize()  # batch 1
        dwg._defer_intents = True
        dwg.callout(holes[1])
        dwg.finalize()  # batch 2 — must not overwrite batch 1's callout
        assert dwg.annotations_of(holes[0]) and dwg.annotations_of(holes[1])
        assert len([n for n in dwg.annotations() if n.startswith("hc_")]) == 2

    def test_finalize_sectioned_part_reserves_then_renders_section(self):
        # #426 Phase 3b: a sectioned part reserves the section row BEFORE the callout carve
        # (Coupling A), routes callouts through _annotate_holes, and renders the section last
        # — so the reconstruction reproduces the auto-pass callout layout AND places the section.
        part = Box(60, 40, 20) - Cylinder(4, 30) - Pos(0, 0, 2) * Cylinder(7, 20)  # counterbore
        auto = build_drawing(part)  # auto_dims=True — the reference

        dwg = build_drawing(part, auto_dims=False)
        dwg._defer_intents = True
        for f in (x for x in dwg.model().features if x.kind in ("hole", "pattern")):
            dwg.callout(f)
        dwg.section()
        dwg.finalize()  # must not raise
        assert "section_caption" in dwg.annotations() and "section_line" in dwg.annotations()
        # the callout carve saw the reserved section row → callouts match the auto-pass
        assert self._hc_ys(dwg) and self._hc_ys(dwg) == self._hc_ys(auto)

    @staticmethod
    def _dia_ys(d):
        return sorted(
            round(d.get_annotation(n).bounding_box().center().Y, 1)
            for n in d.annotations()
            if n.startswith("m_dia")
        )

    def test_finalize_routes_step_diameters_through_render_diameters(self):
        # #426 Phase 4a: step/boss ø callouts route through render_diameters' row/column
        # set-solve, so each step diameter lands at the same position the auto-pass gives it.
        # finalize may place ONE EXTRA (the OD/base diameter): the auto-pass suppresses it
        # because render_rotational already shows it as dim_od, but that rotational furniture
        # is a gap kind not reconstructed here (#424) — so the auto-pass diameters are a
        # SUBSET of finalize's, matching where they overlap.
        from build123d import Cylinder

        shaft = (
            Cylinder(24, 15)
            + Cylinder(16, 15).translate((0, 0, 15))
            + Cylinder(9, 15).translate((0, 0, 30))
        )
        auto = build_drawing(shaft)  # auto_dims=True — the reference

        dwg = build_drawing(shaft, auto_dims=False)
        dwg._defer_intents = True
        for f in (x for x in dwg.model().features if x.kind in ("step", "boss")):
            dwg.callout(f)
        dwg.finalize()
        assert self._dia_ys(dwg) and set(self._dia_ys(auto)) <= set(self._dia_ys(dwg))

    def test_finalize_step_diameters_survive_a_second_batch(self):
        # #426 Phase 4a: render_diameters names m_dia_{x,z} _named-aware when only set, so a
        # second finalize batch does not overwrite the first batch's diameter leader.
        from build123d import Cylinder

        shaft = (
            Cylinder(24, 15)
            + Cylinder(16, 15).translate((0, 0, 15))
            + Cylinder(9, 15).translate((0, 0, 30))
        )
        dwg = build_drawing(shaft, auto_dims=False)
        steps = [f for f in dwg.model().features if f.kind == "step"]
        assert len(steps) >= 2
        dwg._defer_intents = True
        dwg.callout(steps[0])
        dwg.finalize()
        first = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")
        }
        dwg._defer_intents = True
        dwg.callout(steps[1])
        dwg.finalize()
        after = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")
        }
        for n, lbl in first.items():  # batch 1's leaders survive with their labels
            assert n in after and after[n] == lbl

    def test_finalize_step_diameters_no_overwrite_after_drop(self):
        # #432 review: render_diameters starts past the MAX existing m_dia index (not
        # first-free), so a batch after drop() leaves a GAP can't wrap onto an occupied
        # higher index and silently overwrite an earlier diameter leader.
        from build123d import Cylinder

        shaft = Cylinder(26, 10)
        for k, r in enumerate((22, 18, 14, 10, 6), start=1):
            shaft += Cylinder(r, 10).translate((0, 0, 10 * k))
        dwg = build_drawing(shaft, auto_dims=False)
        steps = [f for f in dwg.model().features if f.kind == "step"]
        assert len(steps) >= 5

        dwg._defer_intents = True
        for s in steps[:3]:
            dwg.callout(s)
        dwg.finalize()  # m_dia_z0/1/2
        dwg.drop(steps[1])  # removes its m_dia → a gap in the index sequence
        survivors = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")
        }

        dwg._defer_intents = True
        for s in steps[3:5]:
            dwg.callout(s)
        dwg.finalize()  # must start past the max index, not wrap onto a survivor
        after = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_dia")
        }
        for n, lbl in survivors.items():
            assert n in after and after[n] == lbl

    def test_finalize_routes_step_lengths_through_render_step_lengths(self):
        # #426 Phase 4b: a turned shaft's step-length dims route through render_step_lengths'
        # chain, so the finalize reconstruction reproduces the auto-pass step-length layout
        # (m_steplen* at the same positions), not the live per-feature independent dims.
        from build123d import Cylinder, Rot

        shaft = Rot(0, 90, 0) * (
            Cylinder(20, 12)
            + Cylinder(15, 18).translate((0, 0, 12))
            + Cylinder(10, 25).translate((0, 0, 30))
        )
        auto = build_drawing(shaft)  # auto_dims=True — the reference

        dwg = build_drawing(shaft, auto_dims=False)
        dwg._defer_intents = True
        for f in dwg.model().features:
            if f.kind == "step":
                dwg.dimension(f, "length", role="step")
        dwg.finalize()

        def steplen_pos(d):
            return sorted(
                (
                    n,
                    round(d.get_annotation(n).bounding_box().center().X, 1),
                    round(d.get_annotation(n).bounding_box().center().Y, 1),
                )
                for n in d.annotations()
                if n.startswith("m_steplen")
            )

        assert steplen_pos(dwg) and steplen_pos(dwg) == steplen_pos(auto)  # the chain, not singles
        assert not any(
            n.startswith("dim_length") for n in dwg.annotations()
        )  # no live single dims

    def test_place_dim_feature_kwarg_tags_provenance(self):
        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        p1, p2 = dwg.at("plan", 0, 0, 0), dwg.at("plan", 20, 0, 0)
        with pytest.warns(DeprecationWarning, match="Drawing.place_dim"):
            dwg.place_dim(p1, p2, "above", "plan", dwg.draft, name="mine", feature=hole)
        assert "mine" in dwg.annotations_of(hole)

    def test_drop_hole_clears_its_callout(self):
        # #408 A: a hole owns its ⌀ callout, so drop clears it (not just centre marks).
        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        owned = dwg.annotations_of(hole)
        assert any(n.startswith("hc_") for n in owned), "hole should own its callout"
        assert any(n.startswith("hc_") for n in dwg.drop(hole))

    def test_drop_pattern_clears_its_furniture(self):
        # #408 B: a pattern owns its callout AND its centre line / pitch furniture.
        import math

        from build123d import Box, Cylinder, Pos

        part = Box(120, 120, 20)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(35 * math.cos(ang), 35 * math.sin(ang), 0) * Cylinder(4, 20)
        dwg = build_drawing(part)
        pat = next(f for f in dwg.model().features if f.kind == "pattern")
        owned = dwg.annotations_of(pat)
        assert any(n.startswith("hc_") for n in owned) and any(n.startswith("bc_") for n in owned)
        removed = set(dwg.drop(pat))
        assert removed == set(owned)

    def test_balloon_is_owned_by_its_hole(self):
        # #408 C: a balloon (which carries a recognition hole) attributes to the IR feature.
        dwg = build_drawing(_holed_plate())
        a = dwg._analysis
        hole_obj = a.holes[0]
        feat = dwg._feature_of_hole_at(hole_obj.location)
        assert feat is not None
        dwg._add_balloon("plan", "A", 0, hole_obj)
        bln = next(n for n in dwg.annotations() if n.startswith("balloon_"))
        assert bln in dwg.annotations_of(feat)

    def test_drop_is_complete_for_a_multi_feature_prismatic_part(self):
        # #408 audit: holes + bolt pattern + slot — drop(feature) leaves nothing behind.
        import math

        from build123d import Box, Cylinder, Mode, Pos

        part = Box(140, 120, 20) - Pos(-45, 0, 0) * Box(24, 8, 30, mode=Mode.SUBTRACT)
        for k in range(6):
            ang = math.radians(60 * k)
            part -= Pos(40 + 25 * math.cos(ang), 25 * math.sin(ang), 0) * Cylinder(4, 20)
        _assert_drop_is_complete(build_drawing(part))

    def test_drop_is_complete_for_a_turned_part(self):
        # #408 audit: a turned stepped shaft (steps + OD).
        from build123d import Cylinder

        shaft = Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25))
        _assert_drop_is_complete(build_drawing(shaft))

    def test_drop_step_clears_its_diameter_callout(self):
        # #412: a turned step owns its ⌀ callout (m_dia_) — the spec-flattening render pass
        # now carries the feature. Without it, m_dia was feature=None and drop left it.
        from build123d import Cylinder

        dwg = build_drawing(Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        mdia = [n for n in dwg.annotations() if n.startswith("m_dia")]
        assert mdia, "expected turned-diameter callouts"
        for n in mdia:
            assert dwg._registry.feature_of(n) is not None, f"{n} unowned (#412 regression)"
        owner = dwg._registry.feature_of(mdia[0])
        assert mdia[0] in dwg.drop(owner)

    def test_drop_step_clears_its_diameter_callout_x_turned(self):
        # #413 review: cover the X-row path (m_dia_x, _diameter_row_below) too — the Z-shaft
        # above only exercises the m_dia_z column path.
        from build123d import Cylinder, Rot

        shaft = Rot(0, 90, 0) * (Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        dwg = build_drawing(shaft)
        mdia_x = [n for n in dwg.annotations() if n.startswith("m_dia_x")]
        assert mdia_x, "expected X-turned diameter callouts (row path)"
        for n in mdia_x:
            assert dwg._registry.feature_of(n) is not None, f"{n} unowned (#412 row path)"

    def test_drop_is_complete_for_side_drilled_holes(self):
        # #410 review F1: a side-drilled (X/Y-axis) hole's location dims (dim_loc_side/
        # front/z) must be owned so drop clears them — they route through
        # _locate_off_axis_holes, which now tags via place_strip_candidates(features=).
        from build123d import Box, Cylinder, Pos, Rot

        part = (
            Box(120, 90, 40)
            - Pos(0, 0, 5) * Rot(0, 90, 0) * Cylinder(5, 120)  # X-axis bore
            - Pos(0, 0, -8) * Rot(90, 0, 0) * Cylinder(5, 90)  # Y-axis bore
        )
        dwg = build_drawing(part)
        side_loc = [n for n in dwg.annotations() if n.startswith("dim_loc_")]
        assert side_loc, "expected side-drilled location dims"
        # Directly: each (distinct-offset) side-drilled dim is owned by its hole — the F1
        # fix. Without it these were feature=None and drop(hole) left them behind.
        for n in side_loc:
            assert dwg._registry.feature_of(n) is not None, f"{n} unowned (F1 regression)"
        _assert_drop_is_complete(dwg)

    def test_dimension_rejects_non_orthographic_view(self):
        # #407 review: a linear dim on the foreshortening iso view mislabels the length.
        from build123d import Cylinder

        dwg = build_drawing(Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        step = next(f for f in dwg.model().features if f.kind == "step")
        with pytest.raises(ValueError, match="front"):
            dwg.dimension(step, "length", view="iso")

    def test_dimension_unknown_view_raises_valueerror_not_keyerror(self):
        # #407 review: a bad view= must be a clean ValueError, not a bare KeyError.
        from build123d import Cylinder

        dwg = build_drawing(Cylinder(20, 30) + Cylinder(12, 20).translate((0, 0, 25)))
        step = next(f for f in dwg.model().features if f.kind == "step")
        with pytest.raises(ValueError):
            dwg.dimension(step, "length", view="back")

    def test_dimension_ambiguous_kind_requires_role(self):
        # #407 review: an envelope exposes width/height/depth all as 'length' — a bare
        # kind must raise (not silently pick width), and role= must disambiguate.
        from build123d import Box

        dwg = build_drawing(Box(40, 30, 10))
        env = next((f for f in dwg.model().features if f.kind == "envelope"), None)
        assert env is not None
        roles = sorted(q.role for q in env.parameters() if q.kind == "length" and q.span)
        assert len(roles) > 1
        with pytest.raises(ValueError, match="role="):
            dwg.dimension(env, "length")
        name = dwg.dimension(env, "length", role=roles[0])
        assert name in dwg.annotations_of(env)

    def test_shared_coordinate_location_dim_is_unowned(self):
        # #398c review (#406): a single location dim shared by two DISTINCT holes at the
        # same X belongs to neither — it must be unowned so drop(one) can't over-strip the
        # dim the sibling still needs.
        from build123d import Box, Cylinder, Pos

        part = (
            Box(80, 60, 20) - Pos(30, -20, 0) * Cylinder(6, 20) - Pos(30, 20, 0) * Cylinder(4, 20)
        )
        dwg = build_drawing(part)
        holes = [f for f in dwg.model().features if f.kind == "hole"]
        assert len(holes) == 2  # distinct specs → not grouped
        locx = {n for n in dwg.annotations() if n.startswith("m_locx")}
        assert locx, "expected a shared X-location dim"
        # Neither hole owns the shared X dim...
        for h in holes:
            assert not (locx & set(dwg.annotations_of(h)))
        # ...so dropping one leaves it in place for the other.
        dwg.drop(holes[0])
        assert locx <= set(dwg.annotations()), "shared location dim was over-stripped by drop"

    def test_drop_feature_with_no_annotations_is_noop(self):
        dwg = build_drawing(_holed_plate())
        # An envelope feature carries no centre marks (its dims aren't tagged yet).
        env = next((f for f in dwg.model().features if f.kind == "envelope"), None)
        if env is not None:
            assert dwg.drop(env) == []

    def test_manual_add_records_feature_provenance(self):
        from build123d_drafting import CenterMark

        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg.add(CenterMark((0, 0, 0), 3.0, dwg.draft), "my_mark", view="plan", feature=hole)
        assert "my_mark" in dwg.annotations_of(hole)

    def test_provenance_survives_repair(self):
        # Snapshot/restore (the repair undo path) must preserve feature ownership.
        dwg = build_drawing(_holed_plate())
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        before = set(dwg.annotations_of(hole))
        dwg.repair()
        assert set(dwg.annotations_of(hole)) == before

    def test_model_structurally_equivalent_across_step_and_b123d_input(self, tmp_path):
        # D5 / the convergence property (ADR 0001 Amendment 1): a STEP import re-tessellates
        # the solid, so coordinates differ — but the DETECTED feature structure must be the
        # same whether the input was a build123d object or a STEP file of that object.
        part = _holed_plate()
        step = tmp_path / "plate.step"
        export_step(part, str(step))
        m_obj = build_drawing(part).model()
        m_step = build_drawing(str(step)).model()
        assert _model_signature(m_obj) == _model_signature(m_step), (
            f"model diverged across provenance: obj={_model_signature(m_obj)} "
            f"step={_model_signature(m_step)}"
        )


class TestFeatures:
    """#26: dwg.features(view) exposes analysis to scripts."""

    def test_feature_info_importable_from_top_level(self):
        from draftwright import FeatureInfo

        f = FeatureInfo(
            type="hole", page_pos=(1.0, 2.0), diameter=5.0, through=True, depth=None, count=1
        )
        assert f.type == "hole"
        assert f.count == 1

    def test_z_axis_holes_appear_in_plan_view(self):
        dwg = build_drawing(_holed_plate())
        feats = dwg.features("plan")
        assert len(feats) == 2  # ø10 group (×4) + ø6 group (×1)
        diams = {f.diameter for f in feats}
        assert diams == {10.0, 6.0}

    def test_through_and_blind_correctly_classified(self):
        dwg = build_drawing(_holed_plate())
        feats = {f.diameter: f for f in dwg.features("plan")}
        assert feats[10.0].through is True
        assert feats[10.0].depth is None
        assert feats[6.0].through is False
        assert feats[6.0].depth == 10.0

    def test_count_groups_identical_holes(self):
        dwg = build_drawing(_holed_plate())
        feats = {f.diameter: f for f in dwg.features("plan")}
        assert feats[10.0].count == 4
        assert feats[6.0].count == 1

    def test_pattern_and_loose_same_spec_holes_are_separate_groups(self):
        # #584 WP1 (B2): features()/the hole table source grouping from the IR, so a
        # recognised pattern and same-spec LOOSE holes are DISTINCT groups (the pattern
        # keeps its bolt-circle callout) — not one flat spec-group. Six ø6 on a bolt
        # circle + two loose ø6 → a count-6 pattern group AND a count-2 loose group,
        # not one count-8 group.
        part = Box(140, 140, 12)
        for i in range(6):
            ang = math.radians(60 * i + 15)
            part -= Pos(30 * math.cos(ang), 30 * math.sin(ang), 0) * Cylinder(3, 12)
        part -= Pos(-60, -60, 0) * Cylinder(3, 12)
        part -= Pos(60, 60, 0) * Cylinder(3, 12)
        dwg = build_drawing(part, number="X")
        six = sorted(f.count for f in dwg.features("plan") if f.diameter == 6.0)
        assert six == [2, 6]  # two ø6 groups, not one merged count-8

    def test_page_pos_is_in_plan_view_coordinate_range(self):
        dwg = build_drawing(_holed_plate())
        a = dwg._analysis
        feats = dwg.features("plan")
        for f in feats:
            px, py = f.page_pos
            # page_pos must lie within the plan view bounds (within half-extents + margin)
            assert abs(px - a.PV_X) <= a.x_size / 2 * a.SCALE + 5
            assert abs(py - a.PV_Y) <= a.y_size / 2 * a.SCALE + 5

    def test_z_axis_holes_absent_from_front_view(self):
        # The holed plate has only Z-axis holes — none should appear in front
        dwg = build_drawing(_holed_plate())
        assert dwg.features("front") == []

    def test_unknown_view_returns_empty(self):
        dwg = build_drawing(Box(40, 30, 20))
        assert dwg.features("nonsense") == []

    def test_no_analysis_returns_empty(self):
        from draftwright import Drawing

        dwg = Drawing(
            scale=1.0,
            page_w=297,
            page_h=210,
            tb_w=100,
            draft=None,
            look_at=(0, 0, 0),
            dist=100,
            centroid=(0, 0, 0),
            out="",
        )
        assert dwg.features("plan") == []


class TestFindSlots:
    """#135: recognition of enclosed through-slots with rectangular walls.

    recognise_slots() is a pure-geometry pass (no projection) so these are fast.
    Scope is deliberately narrow (#148): only through-slots with straight walls.
    """

    def test_through_slot_recognised(self):
        # A 20-long, 8-wide channel milled THROUGH a 60×30×12 bar.
        part = Box(60, 30, 12) - Pos(0, 0, 0) * Box(20, 8, 20)
        slots = recognise_slots(part)
        assert len(slots) == 1
        s = slots[0]
        assert s.width_axis == "y"
        assert s.long_axis == "x"
        assert s.width == 8.0
        assert s.length == 20.0
        assert (s.lo, s.hi) == (-10.0, 10.0)

    def test_plain_box_has_no_slots(self):
        # The stock's own outer faces are parallel and anti-parallel but face
        # AWAY from each other — the facing test must exclude them.
        assert recognise_slots(Box(40, 20, 10)) == []

    def test_single_flat_is_not_a_slot(self):
        # One machined flat has no opposing wall, so it is not a slot.
        part = Box(40, 20, 10) - Pos(0, 12, 0) * Box(40, 10, 10)
        assert recognise_slots(part) == []

    def test_blind_slot_is_not_a_slot(self):
        # A blind slot (cut partway, leaving a floor) is out of scope (#148):
        # the floor test rejects it. Same geometry as the through case but the
        # cutter does not break through the bottom.
        part = Box(60, 30, 12) - Pos(0, 0, 2) * Box(20, 8, 8)
        assert recognise_slots(part) == []

    def test_blind_pocket_is_not_a_slot(self):
        # A rectangular pocket has the same facing rectangular walls as a slot
        # but is capped by a floor — the through/blind test must reject it.
        part = Box(100, 60, 10) - Pos(0, 0, 2) * Box(40, 25, 6)
        assert recognise_slots(part) == []

    def test_split_floor_pocket_is_not_a_slot(self):
        # A blind pocket whose floor is divided into two coplanar faces by a rib
        # (a webbed / twin-cavity pocket): neither floor half covers 50% of the
        # footprint alone, so the floor test must AGGREGATE coverage across both
        # — otherwise the pocket reads as a phantom through-slot (#146 re-review).
        part = (Box(40, 40, 20) - Pos(0, 0, 10) * Box(10, 30, 8)) + Pos(0, 0, 7) * Box(10, 2, 2)
        assert recognise_slots(part) == []

    def test_turned_groove_is_not_a_slot(self):
        # A circumferential groove on a shaft has ANNULAR (circle-bounded) walls;
        # the rectangular-wall test must reject it (otherwise a stepped shaft's
        # circlip groove reads as a slot — the #146 review false positive).
        part = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(7, 4))
        assert recognise_slots(part) == []

    def test_arc_walled_slot_in_round_stock_recognised(self):
        # #148e: a slot milled into a curved surface has walls the OD clips into an
        # arc + a straight floor/chord. The relaxed wall test (LINE/CIRCLE with at
        # least one straight edge) now recognises it, where the old rectangular-only
        # test — which requires every edge to be a straight LINE — missed it.
        bar = Rotation(0, 90, 0) * Cylinder(20, 80)  # X-axis round bar
        part = bar - Pos(0, 0, 14) * Box(6, 24, 12)  # enclosed slot milled into the top
        (p,) = recognise_pockets(part)
        assert p.width == 6.0
        assert p.width_axis == "x"  # width runs ALONG the bar axis → arc-clipped walls
        assert p.length == 24.0

    def test_arc_wall_relaxation_still_excludes_grooves(self):
        # The relaxation must NOT admit a turned groove's pure-annular wall (CIRCLE
        # edges only, no straight edge) as a slot/pocket wall (#148e) — the very
        # distinction the relaxed test preserves.
        part = Cylinder(10, 40) - (Cylinder(10, 4) - Cylinder(8, 4))
        assert recognise_slots(part) == []
        assert recognise_pockets(part) == []

    def test_transverse_notch_spanning_bar_is_not_a_slot(self):
        # A notch cut fully ACROSS a round bar exits both sides of the OD — an open
        # feature spanning the part, rejected by the span cap even with arc walls (#148e).
        bar = Rotation(0, 90, 0) * Cylinder(15, 60)
        part = bar - Pos(0, 0, 9) * Box(6, 40, 20)
        assert recognise_slots(part) == []
        assert recognise_pockets(part) == []

    def test_keyed_groove_does_not_leak_as_a_slot(self):
        # A circlip groove crossed by a wrench flat / keyway notches a straight edge into
        # each annular wall, so a "one straight edge" test would wrongly admit it and the
        # groove would double-report as both a groove AND a phantom slot on a flanged shaft
        # (the span cap can't save it). The annular wall keeps its TWO concentric arcs (OD +
        # floor), so the one-arc cap rejects it (#148e review).
        part = (
            (Box(60, 60, 8) + Pos(0, 0, 34) * Cylinder(10, 60))
            - Pos(0, 0, 34) * (Cylinder(10, 6) - Cylinder(7, 6))
            - Pos(9, 0, 34) * Box(6, 30, 60)
        )
        assert recognise_slots(part) == []
        assert recognise_pockets(part) == []

    def test_obround_slot_reports_overall_length(self):
        # A radiused-end (obround) slot's flat side walls stop at the straight portion; its
        # length must be the OVERALL length (flat + width, the two semicircular ends), not the
        # flat-wall span (#613). Overall 30, width 8 → flat walls span 22; report 30.
        from build123d import Plane, SlotOverall, extrude

        part = Box(60, 30, 10) - extrude(Plane.XY * SlotOverall(30, 8), 10, both=True)
        (s,) = recognise_slots(part)
        assert s.width == 8.0
        assert s.length == 30.0
        assert (s.lo, s.hi) == (-15.0, 15.0)

    def test_obround_pocket_reports_overall_length(self):
        # The blind counterpart — a floored obround pocket likewise reports overall length (#613).
        from build123d import Plane, SlotOverall, extrude

        part = Box(60, 30, 20) - Pos(0, 0, 5) * extrude(Plane.XY * SlotOverall(30, 8), 12)
        (p,) = recognise_pockets(part)
        assert p.length == 30.0

    def test_rectangular_slot_length_is_unchanged(self):
        # A rectangular slot has no semicircular end caps, so the overall extension is inert —
        # its length is its flat span, already the overall length (#613 must not regress it).
        (s,) = recognise_slots(Box(60, 30, 10) - Box(30, 8, 20))
        assert s.length == 30.0

    def test_recognise_matches_declare_on_obround_length(self):
        # #613 also removes a recognise/declare divergence: declare.slot(obj) reads the overall
        # length off the object bbox (30), while recognise used to report the flat span (22).
        # After the fix both agree on the overall length.
        from build123d import Plane, SlotOverall, extrude

        from draftwright.model import slot as declare_slot

        cutter = extrude(Plane.XY * SlotOverall(30, 8), 10)
        part = Box(60, 30, 10) - extrude(Plane.XY * SlotOverall(30, 8), 20, both=True)
        (s,) = recognise_slots(part)
        declared = declare_slot(cutter, depth_axis="z")
        assert s.length == declared.length == 30.0

    def test_pivot_boss_at_slot_end_does_not_extend_length(self):
        # A slotted lever with a cylindrical pivot boss (radius = width/2) protruding at one
        # end must NOT be read as a radiused end: the boss sits at a different depth than the
        # slot, and a true obround is symmetric (both ends). Flat length 22 stays 22 (#613 review).
        part = Box(60, 30, 10) - Box(22, 8, 20) + Pos(11.3, 0, 5) * Cylinder(4, 10)
        (s,) = recognise_slots(part)
        assert s.length == 22.0
        assert (s.lo, s.hi) == (-11.0, 11.0)

    def test_coaxial_blind_hole_does_not_extend_pocket(self):
        # A blind pocket with a separate blind hole (radius = width/2) drilled from the far
        # face, coaxial with one pocket end but at a different depth (solid material between),
        # must NOT extend the pocket length — the cap's depth extent must match the slot's (#613 review).
        part = Box(60, 30, 20) - Pos(0, 0, 4) * Box(22, 8, 12) - Pos(11.2, 0, -10) * Cylinder(4, 8)
        (p,) = recognise_pockets(part)
        assert p.length == 22.0

    def test_coaxial_posts_at_both_ends_do_not_extend_length(self):
        # Symmetric coaxial POSTS (added material, radius = width/2) protruding into both slot
        # ends at the slot's own depth pass the radius/axis/centreline/depth checks — but they
        # are CONVEX (material inside the cylinder), not concave void caps. The concavity test
        # must reject them so the flat-ended slot is not extended (#613 2nd-pass review).
        part = (
            (Box(60, 30, 10) - Box(30, 8, 20))
            + Pos(15, 0, 0) * Cylinder(4, 10)
            + Pos(-15, 0, 0) * Cylinder(4, 10)
        )
        (s,) = recognise_slots(part)
        assert (
            s.length == 30.0
        )  # the flat span, NOT 38 (would be if wrongly extended by the posts)
        assert (s.lo, s.hi) == (-15.0, 15.0)

    def test_gap_between_bosses_is_not_a_slot(self):
        # The floored channel between two raised bosses has facing rectangular
        # walls but is not a cut slot — the floor (the base plate) rejects it.
        part = Box(80, 40, 6) + Pos(-15, 0, 9) * Box(10, 40, 12) + Pos(15, 0, 9) * Box(10, 40, 12)
        assert recognise_slots(part) == []

    def test_full_span_through_slot_is_not_a_slot(self):
        # A through-channel that runs the WHOLE length of the part is an open
        # feature (a U-channel), not an enclosed slot — rejected by the span cap.
        part = Box(20, 30, 12) - Pos(0, 0, 0) * Box(20, 8, 20)
        assert recognise_slots(part) == []

    def test_rectangular_slot_reported_once(self):
        # A through rectangular slot is bounded by two orthogonal opposed-wall
        # pairs; the merge must collapse them to a single Slot (the narrower
        # width), not report the same feature twice.
        part = Box(60, 40, 12) - Pos(0, 0, 0) * Box(10, 24, 20)
        slots = recognise_slots(part)
        assert len(slots) == 1
        assert slots[0].width == 10.0  # the narrower of the two opposed pairs

    def test_near_square_slot_runs_along_the_bar(self):
        # A through slot whose x-extent ≈ z-extent: the length is assigned to the
        # part's longer axis (a slot on a bar runs along the bar), not whichever
        # OCC extent is fractionally larger.
        part = Box(80, 20, 6) - Pos(0, 0, 0) * Box(6, 4, 8)
        (s,) = recognise_slots(part)
        assert s.width_axis == "y"
        assert s.width == 4.0
        assert s.long_axis == "x"  # not z, despite z-extent ≈ x-extent locally

    def test_cross_slot_collapses_to_two_channels(self):
        # A + of two intersecting through-channels: the central intersection
        # splits each channel's walls, so the raw scan finds FOUR arm-slots. The
        # collinear-collapse must recombine them into the TWO channels, each
        # spanning its full length (#148d). Thin plate so the arm length exceeds
        # the (through) thickness, else the depth axis is mistaken for length.
        part = Box(80, 60, 10) - Box(50, 12, 20) - Box(14, 44, 20)
        slots = recognise_slots(part)
        assert len(slots) == 2
        by_long = {s.long_axis: s for s in slots}
        assert by_long["x"].width == 12.0
        assert by_long["x"].length == 50.0  # the full x-channel, not a 18mm arm
        assert (by_long["x"].lo, by_long["x"].hi) == (-25.0, 25.0)
        assert by_long["y"].width == 14.0
        assert by_long["y"].length == 44.0  # the full y-channel, not a 16mm arm
        assert (by_long["y"].lo, by_long["y"].hi) == (-22.0, 22.0)

    def test_collinear_slots_with_solid_bridge_stay_separate(self):
        # Two collinear slots on the SAME centreline but separated by solid
        # material (no crossing channel bridging the gap) are distinct features.
        # The collapse must span arms only when a perpendicular channel fills the
        # gap — here it does not, so both slots survive (#148d guard).
        part = (
            Box(120, 40, 10) - Pos(-35, 0, 0) * Box(40, 12, 20) - Pos(35, 0, 0) * Box(40, 12, 20)
        )
        slots = recognise_slots(part)
        assert len(slots) == 2
        assert all(s.length == 40.0 for s in slots)  # not merged into one 110mm run

    def test_arms_not_fused_by_a_channel_that_misses_their_centreline(self):
        # The bridging channel must actually REACH the arms, not merely match the
        # gap's centre and width.  Two collinear x-arms on centreline y=0 with a
        # SOLID gap, plus a perpendicular channel displaced to y∈[10,50] whose
        # x-centre and x-width coincide with the gap but which never crosses y=0.
        # Position-blind bridging would fuse the arms across solid stock (#610
        # review); the run-overlap check keeps all three slots distinct.
        part = (
            Box(120, 100, 10)
            - Pos(-35, 0, 0) * Box(40, 12, 20)
            - Pos(35, 0, 0) * Box(40, 12, 20)
            - Pos(0, 30, 0) * Box(30, 40, 20)
        )
        slots = recognise_slots(part)
        assert len(slots) == 3  # two 40mm x-arms + one y-channel, none merged
        assert sorted(s.length for s in slots) == [40.0, 40.0, 40.0]

    def test_pinwheel_of_slots_around_a_solid_hub_stays_four(self):
        # Four disjoint slots arranged around a SOLID central hub: two collinear
        # x-arms and two collinear y-arms, each opposed pair straddling — but not
        # reaching — the hub. Reasoning only from the neighbouring slots' extents
        # would fuse each opposed pair across the hub; the gap box over the solid
        # hub is not void, so it is not merged and all four survive (#610 re-review).
        from build123d import Align

        def cut(xlo, xhi, ylo, yhi):
            return Pos(xlo, ylo, -5) * Box(
                xhi - xlo, yhi - ylo, 20, align=(Align.MIN, Align.MIN, Align.MIN)
            )

        part = (
            Box(100, 100, 10, align=(Align.MIN, Align.MIN, Align.MIN))
            - cut(10, 40, 47, 53)
            - cut(60, 90, 47, 53)
            - cut(40, 60, 10, 40)
            - cut(40, 60, 60, 90)
        )
        assert len(recognise_slots(part)) == 4  # solid hub keeps all four apart

    def test_incidental_hole_between_aligned_slots_does_not_fuse_them(self):
        # Two separate collinear slots on a shared centreline with an unrelated
        # through-hole centred between them (a natural mounting-hole layout).  The
        # hole makes the gap CENTRE void, but the gap box is mostly solid, so the
        # slots must stay separate — a crossing channel would carve the whole box,
        # a hole only pierces it (#610 re-review).
        from build123d import Cylinder

        part = (
            Box(120, 40, 10)
            - Pos(-35, 0, 0) * Box(40, 12, 20)
            - Pos(35, 0, 0) * Box(40, 12, 20)
            - Cylinder(4, 30)
        )
        slots = recognise_slots(part)
        assert len(slots) == 2  # not fused into one 110mm slot by the hole
        assert all(s.length == 40.0 for s in slots)

    def test_slot_is_frozen_dataclass(self):
        s = recognise_slots(Box(60, 30, 12) - Pos(0, 0, 0) * Box(20, 8, 20))[0]
        assert isinstance(s, Slot)
        with pytest.raises(Exception):
            s.width = 1.0  # frozen

    def test_output_order_is_deterministic(self):
        # Two equal-width through-slots must be ordered by geometry (not OCC face
        # order), so the slot{i} annotation names are stable.
        part = Box(120, 40, 12) - Pos(-30, 0, 0) * Box(8, 20, 20) - Pos(30, 0, 0) * Box(8, 20, 20)
        runs = [[(s.width, s.lo, s.hi) for s in recognise_slots(part)] for _ in range(3)]
        assert runs[0] == runs[1] == runs[2]
        assert len(runs[0]) == 2


class TestSlotDimensioning:
    """#135: slots carry width / length / position dims, place-what-fits."""

    @pytest.mark.timeout(60)
    def test_slot_gets_width_length_and_position(self):
        # Through slot at x∈[-10,10] in a 60-long bar (datum x=-30): position to
        # the near (lo) edge is -10-(-30) = 20.
        part = Box(60, 30, 12) - Pos(0, 0, 0) * Box(20, 8, 20)
        dwg = build_drawing(part)
        labels = {n: dwg._named[n].label for n in dwg._named if n.startswith("m_slot")}
        assert labels.get("m_slot0_width") == "8"
        assert labels.get("m_slot0_length") == "20"
        assert labels.get("m_slot0_pos") == "20"

    @pytest.mark.timeout(60)
    def test_slot_sheet_is_lint_clean(self):
        part = Box(60, 30, 12) - Pos(0, 0, 0) * Box(20, 8, 20)
        dwg = build_drawing(part)
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    @pytest.mark.timeout(60)
    def test_non_round_width_label_matches_geometry(self):
        # A true 4.75 mm slot labels as "4.8"; the dim geometry must be snapped
        # to the displayed value or the label-vs-measured lint trips (#135).
        part = Box(60, 30, 12) - Pos(0, 0, 0) * Box(20, 4.75, 20)
        dwg = build_drawing(part)
        assert dwg._named["m_slot0_width"].label == "4.8"
        assert [i for i in dwg.lint() if i.code == "label_vs_measured"] == []

    @pytest.mark.timeout(60)
    def test_slot_dims_do_not_overprint_hole_callouts(self):
        # A slot dim's witness/arrow geometry must not cross a hole callout label.
        # The collision gate tests the dim's FULL geometry (not just its label
        # box) against external annotations, which lint is blind to (#146 review).
        part = Box(140, 60, 16) - Pos(0, 0, 0) * Box(10, 40, 24)
        for x, y in [(-45, 20), (45, 20), (-45, -20), (45, -20)]:
            part = part - Pos(x, y, 0) * Cylinder(4, 16)
        dwg = build_drawing(part)

        def overlaps(a, b):
            return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])

        external = [
            o.label_bbox
            for n, o in dwg._named.items()
            if not n.startswith("m_slot") and getattr(o, "label_bbox", None) is not None
        ]
        assert external  # the holes produced callouts
        for n, o in dwg._named.items():
            if not n.startswith("m_slot"):
                continue
            g = o.bounding_box()
            full = (g.min.X, g.min.Y, g.max.X, g.max.Y)
            assert not any(overlaps(full, e) for e in external), f"{n} overprints a callout"


def _deprecated_place_dim(dwg, *args, **kwargs):
    with pytest.warns(DeprecationWarning, match="Drawing.place_dim"):
        return dwg.place_dim(*args, **kwargs)


class TestPlaceDim:
    """#25: dwg.place_dim() stacks with the auto-dimension strip."""

    def test_place_dim_adds_named_annotation(self):
        dwg = build_drawing(Box(80, 60, 20))
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        _deprecated_place_dim(dwg, p1, p2, "below", "plan", dwg.draft, name="my_dim", label="80")
        assert "my_dim" in dwg._named
        assert dwg._named["my_dim"].label == "80"

    def test_place_dim_returns_dimension_object(self):
        from build123d_drafting.helpers import Dimension

        dwg = build_drawing(Box(60, 40, 20))
        p1 = dwg.at("front", -30, 0, -10)
        p2 = dwg.at("front", 30, 0, -10)
        result = _deprecated_place_dim(dwg, p1, p2, "below", "front", dwg.draft)
        assert isinstance(result, Dimension)

    def test_two_place_dim_calls_stack_without_overlap(self):
        # Two dims on the same strip must land at different page positions.
        # Use auto_dims=False so the strip has no prior allocations, and
        # "above" where there is ample headroom for two consecutive allocations.
        dwg = build_drawing(Box(80, 60, 20), auto_dims=False)
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        d1 = _deprecated_place_dim(dwg, p1, p2, "above", "plan", dwg.draft, name="d1")
        d2 = _deprecated_place_dim(dwg, p1, p2, "above", "plan", dwg.draft, name="d2")
        # dim_level_y is the y-coordinate of the dim line on the page;
        # two stacked dims must land at different y values.
        assert d1.dim_level_y != d2.dim_level_y

    def test_place_dim_no_analysis_falls_back_to_slot(self):
        # _analysis is None → no strip available → falls back to slot offset, no error.
        from build123d_drafting.helpers import Dimension, draft_preset

        from draftwright import Drawing

        d = draft_preset(font_size=3.0, decimal_precision=1)
        dwg = Drawing(
            scale=1.0,
            page_w=297,
            page_h=210,
            tb_w=100,
            draft=d,
            look_at=(0, 0, 0),
            dist=100,
            centroid=(0, 0, 0),
            out="",
        )
        result = _deprecated_place_dim(dwg, (0, 0, 0), (80, 0, 0), "below", "plan", d, slot=8.0)
        assert isinstance(result, Dimension)

    def test_place_dim_labels_real_world_length_at_non_unity_scale(self):
        # place_dim receives page-coordinate points; at 1:2 the page span is 2× the
        # world size. The auto label must read the real-world length, not the page
        # distance, or it disagrees with the geometry (and trips label_vs_measured).
        from draftwright.linting import lint_drawing

        dwg = build_drawing(Box(80, 60, 20), scale=2.0)
        assert dwg.scale == 2.0
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        d = _deprecated_place_dim(dwg, p1, p2, "below", "plan", dwg.draft, name="w")
        assert d.label == "80"
        assert [
            i for i in lint_drawing([d], drawing_scale=dwg.scale) if i.code == "label_vs_measured"
        ] == []

    def test_place_dim_explicit_label_wins_over_scale_autolabel(self):
        dwg = build_drawing(Box(80, 60, 20), scale=2.0)
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        d = _deprecated_place_dim(dwg, p1, p2, "below", "plan", dwg.draft, label="CUSTOM")
        assert d.label == "CUSTOM"

    def test_dimension_does_not_warn_when_using_place_dim_internally(self):
        dwg = build_drawing(Box(80, 50, 20), auto_dims=False)
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with pytest.warns(DeprecationWarning) as caught:
            dwg.place_dim(
                dwg.at("front", -40, 0, -10),
                dwg.at("front", 40, 0, -10),
                "below",
                "front",
                dwg.draft,
            )
        assert caught

        with warnings.catch_warnings(record=True) as no_warnings:
            warnings.simplefilter("always")
            dwg.dimension(env, "length", role="width", name="semantic_width")
        assert [
            w
            for w in no_warnings
            if issubclass(w.category, DeprecationWarning) and "place_dim" in str(w.message)
        ] == []


# ---------------------------------------------------------------------------
# Issue #29: lint findings carry a suggested-fix code snippet
# ---------------------------------------------------------------------------


class TestLintSuggestions:
    """#29: each LintIssue carries a `suggestion` (str | None) with a fix snippet."""

    def test_feature_not_dimensioned_has_suggestion(self):
        # auto_dims=False leaves the ø10 hole undimensioned → coverage lint fires.
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(5, 20)
        dwg = build_drawing(part, auto_dims=False)
        issues = [i for i in dwg.lint() if i.code == "feature_not_dimensioned"]
        assert issues, "expected a feature_not_dimensioned issue"
        sug = issues[0].suggestion
        assert sug is not None
        assert "dwg.features(" in sug
        assert "HoleCallout(" in sug
        assert "Leader(" in sug

    def test_feature_not_dimensioned_suggestion_is_runnable(self):
        # The headline #29 promise: paste the snippet and the lint resolves.
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(5, 20)
        dwg = build_drawing(part, auto_dims=False)
        assert any(i.code == "feature_not_dimensioned" for i in dwg.lint())

        for f in dwg.features("plan"):
            if abs(f.diameter - 10.0) < 1e-6:
                callout = HoleCallout(
                    f.diameter, count=f.count, through=f.through, depth=f.depth, draft=dwg.draft
                )
                elbow = (f.page_pos[0] + 15, f.page_pos[1] + 10, 0)
                leader = Leader((*f.page_pos, 0), elbow, "", dwg.draft, callout=callout)
                dwg.add(leader, name="hole_10")

        assert not any(i.code == "feature_not_dimensioned" for i in dwg.lint())

    def test_clean_drawing_has_no_suggestions(self, dwg_box_60_40_20):
        # A fully auto-dimensioned plain box should lint clean → no suggestions.
        for i in dwg_box_60_40_20.lint():
            assert i.suggestion is None

    def test_lint_summary_omits_none_suggestion(self, dwg_box_60_40_20):
        # A clean box: issue dicts (if any) must not carry a suggestion key.
        for d in dwg_box_60_40_20.lint_summary()["issues"]:
            assert "suggestion" not in d

    def test_lint_summary_includes_present_suggestion(self):
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(5, 20)
        dwg = build_drawing(part, auto_dims=False)
        dicts = [d for d in dwg.lint_summary()["issues"] if d["code"] == "feature_not_dimensioned"]
        assert dicts
        assert "suggestion" in dicts[0]
        assert dicts[0]["suggestion"]

    def test_step_dim_dropped_suggestion_mentions_detail_view(self):
        dwg = build_drawing(_crowded_shoulder_part())
        issues = [i for i in dwg.lint() if i.code == "step_dim_dropped"]
        assert issues, "crowded shoulders should drop a step dim"
        assert "detail_view=True" in issues[0].suggestion

    def test_annotation_overlap_suggestion_prefers_dimension_with_place_dim_fallback(self):
        # Synthetic issue — exercise the _suggest_fix branch directly.
        from draftwright.linting import LintIssue, _suggest_fix

        dwg = build_drawing(Box(60, 40, 20))
        issue = LintIssue(
            severity="warning",
            message="labels 'dim_width' and 'dim_height' overlap by 3.0×2.0 mm",
            code="annotation_overlap",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "dwg.dimension" in sug
        assert "pin=True" in sug
        assert "place_dim" in sug
        assert "dim_width" in sug

    def test_dim_inside_part_suggestion_prefers_dimension_with_place_dim_fallback(self):
        from draftwright.linting import LintIssue, _suggest_fix

        dwg = build_drawing(Box(60, 40, 20))
        issue = LintIssue(
            severity="warning",
            message="Dim 'dim_height': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "dwg.dimension" in sug
        assert "pin=True" in sug
        assert "place_dim" in sug
        assert "dim_height" in sug

    def test_unknown_code_has_no_suggestion(self):
        from draftwright.linting import LintIssue, _suggest_fix

        dwg = build_drawing(Box(60, 40, 20))
        issue = LintIssue(severity="info", message="something", code="some_unhandled_code")
        assert _suggest_fix(issue, dwg) is None

    def test_non_integer_diameter_still_gets_suggestion(self):
        # Regression guard for the 1e-6-vs-_fmt bug: radius 4.111 gives a raw
        # diameter of 8.22, but the message reports the 1dp-rounded ø8.2 — a
        # 0.02 gap that a 1e-6 match would drop. The diameter must round-trip
        # with tolerance so the suggestion still appears.
        part = Box(80, 60, 20) - Pos(20, 15, 0) * Cylinder(4.111, 20)
        dwg = build_drawing(part, auto_dims=False)
        issues = [i for i in dwg.lint() if i.code == "feature_not_dimensioned"]
        assert issues
        assert "ø8.2" in issues[0].message  # rounded, differs from raw 8.22
        assert issues[0].suggestion is not None
        assert 'dwg.features("plan")' in issues[0].suggestion

    def test_feature_count_mismatch_suggestion_sets_count(self):
        # The leading number is `need`; diameter digits (even fractional) must
        # not interfere with the parse.
        from draftwright.linting import LintIssue, _suggest_fix

        dwg = build_drawing(Box(60, 40, 20))
        issue = LintIssue(
            severity="warning",
            message="4 ø8.5 features on the part but callouts account for 1",
            code="feature_count_mismatch",
        )
        sug = _suggest_fix(issue, dwg)
        assert sug is not None
        assert "count=4" in sug


class TestRepair:
    """#30/#521: repair is a narrow safety net, not a second placement engine."""

    def test_repair_does_not_fixed_step_annotation_overlap(self):
        # Two dimensions forced onto the same page location → their labels collide. The
        # solver path owns placement; repair must not hide this with a fixed-step nudge.
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        d = dwg.draft
        p1, p2 = (40.0, 20.0, 0.0), (80.0, 20.0, 0.0)
        dwg.add(_dim(p1, p2, "above", 8, d, label="AA"), "ov1")
        dwg.add(_dim(p1, p2, "above", 8, d, label="BB"), "ov2")
        assert [i for i in dwg.lint() if i.code == "annotation_overlap"]

        dwg.repair()
        assert dwg._named["ov1"]._dw_spec.distance == 8
        assert dwg._named["ov2"]._dw_spec.distance == 8
        assert [i for i in dwg.lint() if i.code == "annotation_overlap"]

    def test_repair_dim_inside_part_flips_side(self):
        # dim_inside_part is dormant in the multi-view sheet (lint passes no
        # part_bbox), so drive the repair directly: a wrong-side dim flips to
        # the opposite side and keeps its name binding.
        from draftwright._core import _dim
        from draftwright.linting import LintIssue
        from draftwright.repair import _repair_dim_inside_part

        dwg = build_drawing(Box(60, 40, 20))
        dim = dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="INSIDE"), "x")
        assert dim._dw_spec.side == "above"

        issue = LintIssue(
            severity="warning",
            message="Dim 'INSIDE': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        assert _repair_dim_inside_part(dwg, issue) is True
        new = dwg._named["x"]
        assert new is not dim
        assert new._dw_spec.side == "below"
        assert new in dwg.items and dim not in dwg.items

    def test_repair_inside_part_attempted_once_no_oscillation(self):
        # A side flip that does not help must not be re-flipped (oscillation).
        # The same label is only flipped once across the whole loop.
        from draftwright._core import _dim
        from draftwright.linting import LintIssue

        dwg = build_drawing(Box(60, 40, 20))
        dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="OSC"), "x")

        # Monkeypatch lint to always report the same dim_inside_part.
        issue = LintIssue(
            severity="warning",
            message="Dim 'OSC': annotation bbox overlaps part outline by 40%",
            code="dim_inside_part",
        )
        dwg.lint = lambda: [issue]
        dwg.repair(max_iter=5)
        # Flipped exactly once → ends on "below", not back to "above".
        assert dwg._named["x"]._dw_spec.side == "below"

    def test_repair_idempotent_on_clean_drawing(self):
        # build_drawing already repairs by default, so a second pass is a no-op:
        # same objects, same order.
        dwg = build_drawing(Box(60, 40, 20))
        before = [id(o) for o in dwg.items]
        assert dwg.repair() is dwg
        assert [id(o) for o in dwg.items] == before

    def test_repair_does_not_increase_issue_counts(self):
        # Acceptance: on the existing fixtures, error+warning counts after the
        # repair pass are <= the raw greedy placement — no regressions.
        def ew(dwg):
            return sum(1 for i in dwg.lint() if i.severity in ("error", "warning"))

        for part in (Box(60, 40, 20), _holed_plate(), _uniform_staircase()):
            raw = ew(build_drawing(part, repair=False))
            fixed = ew(build_drawing(part, repair=True))
            assert fixed <= raw

    def test_repair_ignores_annotation_overlap_without_mutation(self):
        # annotation_overlap is no longer repairable (#521). It remains visible
        # to lint rather than being moved by a fixed-step fallback.
        from draftwright._core import _dim
        from draftwright.linting import LintIssue

        dwg = build_drawing(Box(60, 40, 20))
        orig = dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="RB"), "x")
        overlap = LintIssue(
            severity="warning",
            message="labels 'RB' and 'QQ' overlap",
            code="annotation_overlap",
        )
        calls = {"n": 0}

        def fake_lint():
            calls["n"] += 1
            return [overlap]

        dwg.lint = fake_lint
        dwg.repair(max_iter=3)
        assert dwg._named["x"] is orig
        assert dwg._named["x"]._dw_spec.distance == 8
        assert calls["n"] == 1

    def test_build_drawing_repair_flag_is_respected(self):
        # repair=False leaves the greedy placement untouched; the default repairs.
        from draftwright._core import _dim

        # A clean part is identical either way (nothing to repair).
        a = build_drawing(Box(60, 40, 20), repair=False)
        b = build_drawing(Box(60, 40, 20), repair=True)
        assert [getattr(o, "label", None) for o in a.items] == [
            getattr(o, "label", None) for o in b.items
        ]
        # The factory tags engine dims so repair can re-place them.
        d = _dim((0, 0, 0), (40, 0, 0), "above", 8, a.draft, label="Z")
        assert d._dw_spec.side == "above"


class TestPin:
    """#89: a pinned annotation is never moved by the engine (repair today)."""

    def _two_overlapping(self):
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        p1, p2 = (40.0, 20.0, 0.0), (80.0, 20.0, 0.0)
        dwg.add(_dim(p1, p2, "above", 8, dwg.draft, label="AA"), "a")
        dwg.add(_dim(p1, p2, "above", 8, dwg.draft, label="BB"), "b")
        return dwg

    def test_repair_does_not_move_a_pinned_dim(self):
        # Overlaps are not repaired by fixed-step placement anymore, so pinned and
        # unpinned dimensions alike stay put.
        dwg = self._two_overlapping()
        dwg.pin("a")
        dwg.repair()
        assert dwg._named["a"]._dw_spec.distance == 8
        assert dwg._named["b"]._dw_spec.distance == 8

    def test_unpin_lets_repair_move_it_again(self):
        dwg = self._two_overlapping()
        dwg.pin("a").unpin("a")
        dwg.repair()
        assert dwg._named["a"]._dw_spec.distance == 8
        assert dwg._named["b"]._dw_spec.distance == 8
        assert [i for i in dwg.lint() if i.code == "annotation_overlap"]

    def test_pin_unknown_name_raises(self):
        dwg = build_drawing(Box(60, 40, 20))
        with pytest.raises(KeyError):
            dwg.pin("does_not_exist")

    def test_pin_and_unpin_are_chainable(self):
        dwg = self._two_overlapping()
        assert dwg.pin("a") is dwg
        assert dwg.unpin("a") is dwg

    def test_pinning_both_overlap_labels_is_a_noop(self):
        # Both deliberate → the engine respects both and leaves the overlap.
        dwg = self._two_overlapping()
        dwg.pin("a").pin("b")
        dwg.repair()
        assert dwg._named["a"]._dw_spec.distance == 8
        assert dwg._named["b"]._dw_spec.distance == 8

    def test_pinning_a_non_dim_then_repair_does_not_crash(self):
        # _find_dim builds an id-set over pinned objects of any type; pinning a
        # Leader (not a re-placeable dim) must not break repair.
        dwg = self._two_overlapping()
        dwg.add(Leader((0, 0, 0), (10, 10, 0), "L", dwg.draft), "ldr")
        dwg.pin("ldr")
        dwg.repair()  # must not raise
        assert "ldr" in dwg.annotations()

    def test_removed_then_readded_name_is_not_still_pinned(self):
        from draftwright._core import _dim

        dwg = self._two_overlapping()
        dwg.pin("a")
        dwg.remove("a")
        # Re-add a fresh "a" at the same overlapping spot; it must NOT inherit
        # the old pin, so repair is free to move it.
        dwg.add(_dim((40.0, 20.0, 0.0), (80.0, 20.0, 0.0), "above", 8, dwg.draft, label="AA"), "a")
        assert "a" not in dwg._pinned
        dwg.repair()
        assert dwg._named["a"]._dw_spec.distance == 8


class TestAnnotationsQuery:
    """#27: introspect existing annotations by name and type."""

    def test_annotations_maps_name_to_type(self):
        dwg = build_drawing(Box(60, 40, 20))
        anns = dwg.annotations()
        # A dict keyed by the names actually registered, valued by class name.
        assert isinstance(anns, dict)
        assert anns  # a box drawing has named annotations
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in anns.items())
        # Every key resolves, and its reported type matches the live object.
        for name, type_name in anns.items():
            assert type(dwg._named[name]).__name__ == type_name

    def test_annotations_omits_unnamed(self):
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        before = dict(dwg.annotations())
        dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="U"))  # no name
        # Unnamed annotation lands in items but not in the name→type map.
        assert dwg.annotations() == before
        assert len(dwg.items) == len(before) + 1

    def test_annotations_reflects_add_and_membership(self):
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        assert "q_dim" not in dwg.annotations()
        dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="Q"), "q_dim")
        assert dwg.annotations()["q_dim"] == "Dimension"

    def test_get_annotation_returns_object_or_none(self):
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        obj = dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="G"), "g")
        assert dwg.get_annotation("g") is obj
        assert dwg.get_annotation("does_not_exist") is None

    def test_get_annotation_follows_remove(self):
        from draftwright._core import _dim

        dwg = build_drawing(Box(60, 40, 20))
        dwg.add(_dim((0, 0, 0), (40, 0, 0), "above", 8, dwg.draft, label="R"), "r")
        assert dwg.get_annotation("r") is not None
        dwg.remove("r")
        assert dwg.get_annotation("r") is None
        assert "r" not in dwg.annotations()


class TestViewBounds:
    """#28: page bounding box of a named view's projected geometry."""

    def test_view_bounds_returns_page_bbox(self, dwg_box_60_40_20):
        dwg = dwg_box_60_40_20
        b = dwg.view_bounds("front")
        assert b is not None and len(b) == 4
        x0, y0, x1, y1 = b
        assert x1 > x0 and y1 > y0
        # Front view (looking along Y) shows X=60 wide, Z=20 tall, at sheet scale.
        assert (x1 - x0) == pytest.approx(60 * dwg.scale, rel=1e-3)
        assert (y1 - y0) == pytest.approx(20 * dwg.scale, rel=1e-3)

    def test_view_bounds_contains_projected_centroid(self, dwg_box_60_40_20):
        # The part centroid (world origin for a centred Box) projects inside.
        dwg = dwg_box_60_40_20
        x0, y0, x1, y1 = dwg.view_bounds("front")
        px, py, _ = dwg.at("front", 0, 0, 0)
        assert x0 <= px <= x1
        assert y0 <= py <= y1

    def test_view_bounds_unknown_view_is_none(self, dwg_box_60_40_20):
        assert dwg_box_60_40_20.view_bounds("does_not_exist") is None

    def test_view_bounds_for_each_standard_view(self, dwg_box_60_40_20):
        dwg = dwg_box_60_40_20
        for v in ("front", "plan", "side", "iso"):
            b = dwg.view_bounds(v)
            assert b is not None, v
            x0, y0, x1, y1 = b
            assert x1 > x0 and y1 > y0, v

    def test_view_bounds_includes_hidden_lines(self):
        # Bounds union the visible and hidden silhouettes. Replace the front
        # view's hidden compound with one that extends past the visible box and
        # confirm the right edge moves out to it.
        dwg = build_drawing(Box(60, 40, 20))
        vis, _ = dwg.views["front"]
        _, _, x1, _ = dwg.view_bounds("front")
        far = Compound(children=[Edge.make_line((x1 + 10, 0, 0), (x1 + 10, 5, 0))])
        dwg.views["front"] = (vis, far)
        assert dwg.view_bounds("front")[2] == pytest.approx(x1 + 10)


def _x_stepped_shaft():
    """A turned shaft lying along X: ø30 (len 40) then ø16 (len 30).

    Built about Z then rotated so the turning axis is X — the orientation that
    is *not* flagged rotational (the OD logic is Z-centric), exercising #77.
    """
    return Rotation(0, 90, 0) * (Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(8, 30))


class TestPrismaticBossDiameter:
    """#629: a boss on a PRISMATIC part gets its ø as a plan-view leader to the boss
    circle (names ``m_bossdia_*``), free to exit into clear margin — not the turned
    column-left strip (``m_dia_z``), which strands the ø when that narrow strip is
    tight even on a half-empty sheet. Turned parts keep the OD-stack column."""

    @staticmethod
    def _box_boss():
        return Box(90, 64, 38) + Pos(0, 0, 24) * Cylinder(14, 10)

    @staticmethod
    def _shelled_cover():
        # The #629 report: a shelled cover whose front view hugs the left margin, so
        # the column-left ø strip has no room and the boss ø28 was dropped.
        return (
            Box(90, 64, 38)
            - Pos(0, 0, -3) * Box(84, 58, 38)
            + Pos(0, 0, 24) * Cylinder(14, 10)
            - Pos(0, 0, 15) * Cylinder(6, 40)
        )

    def test_prismatic_boss_diameter_is_a_plan_leader(self):
        dwg = build_drawing(self._box_boss())
        # the boss ø routes to the plan-view leader path, not the turned column
        assert any(n.startswith("m_bossdia_") for n in dwg._named)
        boss = next(o for n, o in dwg._named.items() if n.startswith("m_bossdia_"))
        assert boss.label == "ø28"
        # and it is not ALSO emitted by the turned column (no double-dimensioning)
        assert not any(o.label == "ø28" for n, o in dwg._named.items() if n.startswith("m_dia_z"))

    def test_shelled_cover_boss_diameter_not_dropped(self):
        # The regression: even forced onto A4 (scale 0.5, front view against the left
        # margin) the boss ø28 places into the clear sheet, so it never lints uncovered.
        dwg = build_drawing(self._shelled_cover(), page="A4")
        assert any(o.label == "ø28" for o in dwg._named.values() if getattr(o, "label", None))
        assert dwg.lint_summary()["by_code"].get("feature_not_dimensioned", 0) == 0

    def test_boss_diameter_carries_authored_tolerance(self):
        # The pass must consume the planner's DimParameter (value + tolerance/fit), not raw
        # geometry — formatting b.diameter directly dropped an authored ⌀ tolerance, and then
        # blocked render_diameters via `mentioned`, losing it silently (gpt-5.6-sol review).
        from draftwright import Sheet

        s = Sheet.from_part(Box(90, 64, 38) + Pos(0, 0, 24) * Cylinder(14, 10))
        s.of(Pos(0, 0, 24) * Cylinder(14, 10)).tolerance(0.0, 0.1)  # tolerance the boss ⌀28
        dwg = s.build()
        labels = [o.label for n, o in dwg._named.items() if n.startswith("m_bossdia_")]
        assert labels and any("0.1" in str(lbl) for lbl in labels), labels

    def test_turned_part_boss_stays_in_the_column(self):
        # A rotational shaft's step/OD diameters keep the m_dia column — render_boss_diameters
        # is a prismatic-only pass and must not fire on a turned body.
        dwg = build_drawing(Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(10, 30))
        assert not any(n.startswith("m_bossdia_") for n in dwg._named)
        assert any(n.startswith("m_dia_z") for n in dwg._named)


class TestTurnedDiameters:
    """#77/#131: external turned diameters get ø leader callouts. Migrated onto the
    IR renderer (from_model.render_diameters, names ``m_dia_*``) — one path, row
    below (X) / column left (Z) by frame axis (ADR 0008 convergence)."""

    def test_each_external_diameter_gets_a_callout(self):
        dwg = build_drawing(_x_stepped_shaft())
        labels = {o.label for n, o in dwg._named.items() if n.startswith("m_dia")}
        assert "ø30" in labels
        assert "ø16" in labels

    def test_no_feature_not_dimensioned_left(self):
        # The whole point: the external diameters no longer lint as uncovered.
        dwg = build_drawing(_x_stepped_shaft())
        codes = dwg.lint_summary()["by_code"]
        assert codes.get("feature_not_dimensioned", 0) == 0

    def test_callouts_are_leaders_on_the_constraint_solver(self):
        # Placed via _solve_strip_ys (ADR 0003 layer-2), so two distinct
        # diameters never share an x and never collide: label xs are min_gap
        # apart and inside the front view's page bounds.
        dwg = build_drawing(_x_stepped_shaft())
        leaders = [o for n, o in dwg._named.items() if n.startswith("m_dia")]
        assert len(leaders) >= 2
        xs = sorted(ldr.elbow[0] for ldr in leaders)
        assert all(b - a > 1.0 for a, b in zip(xs, xs[1:]))  # spread, not stacked

    def test_z_rotational_part_is_untouched(self):
        # A plain Z disc's OD is covered by dim_od (rotational), so render_diameters
        # skips it (already mentioned) — no m_dia callouts appear.
        dwg = build_drawing(Cylinder(15, 40))  # plain Z disc/shaft
        assert not any(n.startswith("m_dia") for n in dwg._named)

    def test_horizontal_round_body_od_on_profile(self):
        # A horizontal (X-axis) single-OD cylinder shows its OD as a clean profile-view
        # diameter dim (dim_od) — not an end-on/corner boss leader — with the envelope
        # dims that duplicate the OD suppressed, so no double-dimensioning (#222).
        from build123d import Rot

        for rot, axis in ((Rot(0, 90, 0), "x"), (Rot(90, 0, 0), "y")):
            dwg = build_drawing(rot * Cylinder(25, 40), number="X")
            assert dwg._analysis.od_axis == axis
            assert "dim_od" in dwg._named, f"{axis}: OD not on profile"
            assert not any(n.startswith("m_dia") for n in dwg._named), f"{axis}: end-on leader"
            # the OD (50) appears once (ø50), not also as a bare envelope "50"
            labels = [str(o.label) for o in dwg._named.values() if getattr(o, "label", None)]
            assert "50" not in labels, f"{axis}: OD double-dimensioned as envelope"
            assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_unfittable_row_skips_without_crashing(self, monkeypatch):
        # When the labels do not fit the row, both solvers return None; the pass
        # must skip gracefully, not crash the whole build on a None unpack.
        import sys

        # render_diameters looks the strip solvers up in its own module's namespace
        # (annotations.from_model) — patch them there.
        m = sys.modules["draftwright.annotations.from_model"]
        monkeypatch.setattr(m, "_solve_strip_ys", lambda *a, **k: None)
        monkeypatch.setattr(m, "_greedy_strip_ys", lambda *a, **k: None)
        dwg = build_drawing(_x_stepped_shaft())  # must not raise
        assert not any(n.startswith("m_dia") for n in dwg._named)

    def test_nested_band_under_silhouette_gets_a_callout(self):
        # #298: a narrow ø6 external band sits under the ø30 flange silhouette, so
        # recognise_turned_steps' local_od max() reads it as ø30 and it never becomes a step
        # diameter. detect.py now emits the missed band as a boss, so it still gets a ø
        # callout (matching the feature_diameters coverage inventory) and the part lints
        # clean. The overall part is large enough for all three callouts to fit the row.
        from build123d import Align

        def cyl(r, h, z):
            return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))

        part = Rotation(0, 90, 0) * (cyl(3, 0.5, 0.0) + cyl(15, 20, 0.5) + cyl(10, 15, 20.5))
        dwg = build_drawing(part)
        labels = {o.label for n, o in dwg._named.items() if n.startswith("m_dia")}
        assert {"ø6", "ø30", "ø20"} <= labels  # the nested ø6 is now called out
        assert dwg.lint_summary()["by_code"].get("feature_not_dimensioned", 0) == 0

    def test_diameter_row_places_what_fits_not_all_or_nothing(self):
        # #298 hardening: on a part too small to fit every ø callout in the row, the
        # placer keeps the significant ODs and drops only the smallest — never the whole
        # row (the pre-fix all-or-nothing dropped all three). The finest band honestly
        # surfaces as feature_not_dimensioned.
        from build123d import Align

        def cyl(r, h, z):
            return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))

        # A ~4 mm shaft: ø6 tip, ø10 flange, ø8 body — three callouts won't fit the row.
        part = Rotation(0, 90, 0) * (cyl(3, 0.5, 0.0) + cyl(5, 1.7, 0.5) + cyl(4, 2.0, 2.2))
        dwg = build_drawing(part)
        labels = {o.label for n, o in dwg._named.items() if n.startswith("m_dia")}
        assert {"ø10", "ø8"} <= labels  # the significant ODs survive, not dropped wholesale
        undim = {i.message.split()[2] for i in dwg.lint() if i.code == "feature_not_dimensioned"}
        assert "ø6" in undim  # only the finest band falls to honest lint


class TestTurnedLengths:
    """Axial step-length chain for X-axis turned parts (the drive-screw gap:
    every diameter dimensioned, no shoulder locatable)."""

    def test_each_step_length_is_dimensioned(self):
        dwg = build_drawing(_x_stepped_shaft())  # ø30 l40 then ø16 l30
        labels = {o.label for n, o in dwg._named.items() if n.startswith("m_steplen")}
        assert labels == {"40", "30"}

    def test_overall_width_suppressed_for_turned_part(self):
        # The complete chain conveys the overall length, so the envelope width dim
        # is dropped — no double dimensioning (ISO 129).
        dwg = build_drawing(_x_stepped_shaft())
        assert "m_env_width" not in dwg._named

    def test_turned_part_lints_clean(self):
        dwg = build_drawing(_x_stepped_shaft())
        codes = dwg.lint_summary()["by_code"]
        assert codes.get("axial_length_missing", 0) == 0
        assert codes.get("annotation_overlap", 0) == 0

    def test_three_step_shaft_dimensions_all_steps(self):
        # Non-uniform step lengths (10/8/12), base-stacked so they sit flush → each
        # segment dimensioned individually (the uniform-run collapse, #230, is
        # exercised separately below).
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        stack = Cylinder(10, 10, align=(Align.CENTER, Align.CENTER, b))
        stack += Pos(0, 0, 10) * Cylinder(7, 8, align=(Align.CENTER, Align.CENTER, b))
        stack += Pos(0, 0, 18) * Cylinder(4, 12, align=(Align.CENTER, Align.CENTER, b))
        dwg = build_drawing(Rotation(0, 90, 0) * stack)
        assert len([n for n in dwg._named if n.startswith("m_steplen")]) == 3

    def test_uniform_staircase_collapses_to_n_times(self):
        # A uniform run (4 equal-length steps) collapses to one "N× length" dim
        # instead of four identical segment dims (#230) — and the collapsed dim must
        # still satisfy axial coverage (lint clean, every shoulder located).
        from build123d import Align, Cylinder, Pos

        b = Align.MIN
        shaft = Cylinder(30, 10, align=(Align.CENTER, Align.CENTER, b))
        for i, r in enumerate([25, 20, 15], start=1):
            shaft += Pos(0, 0, 10 * i) * Cylinder(r, 10, align=(Align.CENTER, Align.CENTER, b))
        dwg = build_drawing(shaft)
        steplen = {n: o.label for n, o in dwg._named.items() if n.startswith("m_steplen")}
        assert steplen == {"m_steplen_typ": "4× 10"}, steplen
        assert "axial_length_missing" not in {i.code for i in dwg.lint()}

    def test_prismatic_part_has_no_step_lengths(self):
        dwg = build_drawing(Box(80, 60, 20))
        assert not any(n.startswith("m_steplen") for n in dwg._named)

    def test_grooved_shaft_step_chain_not_flagged_axial_missing(self):
        # A groove band is excluded from the step-length chain (#606) and dimensioned by its
        # WIDTH callout instead. The axial-coverage lint counts prof.steps (which still includes
        # the groove band), so it must credit the rendered groove-width callout as covering that
        # band — else an otherwise fully-dimensioned grooved shaft false-fires axial_length_missing
        # (#628, a regression from the #606 groove exclusion).
        from build123d import Cylinder, Pos

        shaft = (
            Pos(0, 0, 7.5) * Cylinder(30, 15)
            + Pos(0, 0, 32) * Cylinder(20, 34)
            + Pos(0, 0, 53) * Cylinder(13, 8)  # ø26 local-minimum band → recognised as a groove
            + Pos(0, 0, 74) * Cylinder(20, 34)
            + Pos(0, 0, 107) * Cylinder(14, 32)
        ) - Pos(0, 0, 61.5) * Cylinder(8, 123)
        dwg = build_drawing(shaft, number="X")
        assert any(n.startswith("m_groove") for n in dwg._named)  # the ø26 band IS a groove
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_chain_skips_gracefully_when_no_room(self):
        # Forced onto a too-small page, the chain must SKIP rather than run off the
        # page edge (the parity guard the diameter row has). Lint then reports the
        # gap instead of the engine emitting off-page dims.
        from build123d import Cylinder, Pos, Rotation

        z = 0.0
        part = None
        for i in range(10):
            seg = Pos(0, 0, z + 1.0) * Cylinder((12 - 0.6 * i) / 2, 2.0)
            part = seg if part is None else part + seg
            z += 2.0
        dwg = build_drawing(Rotation(0, 90, 0) * part, page="90x70", scale=4.0)
        assert not any(n.startswith("m_steplen") for n in dwg._named)  # skipped, not off-page
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) >= 1

    def test_dense_chain_skips_instead_of_cramming(self):
        # A genuinely dense turned shaft (many fine non-uniform steps) whose labels
        # cannot be spaced legibly must SKIP the chain, not overprint a wall of
        # overlapping dims (#293). Any placed step-length dims must not overlap.
        from build123d import Align, Cylinder, Pos, Rotation

        from draftwright.annotations._common import _anno_box

        b = Align.MIN
        shaft = None
        z = 0.0
        for i in range(16):
            d = 20 if i % 2 == 0 else 16  # alternating ø → truly stepped, fine pitch
            ln = 3.0 + (i % 3) * 0.4  # non-uniform (no N× collapse)
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft)
        boxes = [_anno_box(o) for n, o in dwg._named.items() if n.startswith("m_steplen")]

        def overlap(a, c):
            return a and c and not (a[2] <= c[0] or a[0] >= c[2] or a[3] <= c[1] or a[1] >= c[3])

        assert not any(
            overlap(boxes[i], boxes[j])
            for i in range(len(boxes))
            for j in range(i + 1, len(boxes))
        ), "step-length dims overprint — chain crammed instead of skipping"

    def test_crowded_chain_staggers_into_two_tiers_at_current_scale(self):
        # A *moderately* crowded chain — steps just ABOVE the arrowhead floor (so no
        # detail view is triggered), but with labels that would collide on one tier.
        # Rather than cram, the chain staggers successive dims between a near and a far
        # tier (ISO 129-1) so every step length stays legible at the drawing's own
        # scale (#293). Scale pinned so the crowding regime is deterministic.
        from build123d import Align, Cylinder, Pos, Rotation

        from draftwright.annotations._common import _anno_box

        b = Align.MIN
        specs = [(8, 3.1), (12, 2.9), (8, 3.2), (12, 2.8), (6, 3.0)]  # ~3 mm, > floor
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, scale=2.0)
        assert "detail_a" not in dwg.views  # above floor → no detail, staggered in place
        steps = {n: o for n, o in dwg._named.items() if n.startswith("m_steplen")}
        assert len(steps) == 5  # every segment dimensioned, none dropped
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0
        # Two tiers: the dims sit at (at least) two distinct offset rows.
        boxes = [_anno_box(o) for o in steps.values()]
        rows = {round((bb[1] + bb[3]) / 2, 1) for bb in boxes if bb}
        assert len(rows) >= 2, "chain did not stagger into multiple tiers"

        # Labels don't overprint each other.
        def overlap(a, c):
            return a and c and not (a[2] <= c[0] or a[0] >= c[2] or a[3] <= c[1] or a[1] >= c[3])

        assert not any(
            overlap(boxes[i], boxes[j])
            for i in range(len(boxes))
            for j in range(i + 1, len(boxes))
        ), "staggered step-length labels overprint"

    def test_subfloor_head_gets_detail_view(self):
        # A fine head (sub-floor steps) + a long shaft (the GRM-03 pattern). The head
        # can't be dimensioned legibly in line, so the unified detail pipeline (#307)
        # locates it as one block on the main view + breaks it down in DETAIL A, with
        # axial coverage satisfied across the two views (no double-dimensioning).
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        specs = [(4, 1.5), (6, 2.0), (4, 2.5), (3, 25.0)]  # non-uniform sub-floor head
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, scale=2.0)
        assert "detail_a" in dwg.views  # crowded head → enlarged detail
        assert "25" in {o.label for n, o in dwg._named.items() if n.startswith("m_steplen")}
        assert len([n for n in dwg._named if n.startswith("dim_detail_a_steplen")]) >= 3
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_two_sub_floor_runs_get_separate_non_colliding_details(self):
        # Two separated fine-step clusters → two detail views (A, B). Their dims use
        # view-scoped names, so detail B's dims don't evict detail A's (the #307-review
        # name-collision regression) and axial coverage holds across all views.
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        specs = [(4, 1.5), (6, 2.0), (4, 2.5), (3, 22), (6, 1.5), (4, 2.0), (5, 2.5), (2, 22)]
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, page="A2", scale=2.0)
        assert {"detail_a", "detail_b"} <= set(dwg.views)
        names = [n for n in dwg._named if "steplen" in n and "detail" in n]
        assert len(names) == len(set(names))  # no eviction — all detail dims survive
        assert any(n.startswith("dim_detail_a_") for n in names)
        assert any(n.startswith("dim_detail_b_") for n in names)
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0

    def test_head_block_does_not_collapse_main_chain_to_n_times(self):
        # When the head-block extent happens to match the legible step lengths, the main
        # chain (block + steps) must NOT collapse to a uniform "N× v" — the block is a
        # compound region, not a repeated step, and "N× v" would be a false claim of N
        # equal steps (#307 review).
        from build123d import Align, Cylinder, Pos, Rotation

        b = Align.MIN
        # head 1.5/2.0/2.5 (sub-floor, sums to 6) + two legible 6 mm steps
        specs = [(4, 1.5), (6, 2.0), (4, 2.5), (7, 6.0), (5, 6.0)]
        shaft = None
        z = 0.0
        for d, ln in specs:
            seg = Pos(0, 0, z) * Cylinder(d / 2, ln, align=(Align.CENTER, Align.CENTER, b))
            shaft = seg if shaft is None else shaft + seg
            z += ln
        dwg = build_drawing(Rotation(0, 90, 0) * shaft, scale=2.0)
        main = {o.label for n, o in dwg._named.items() if n.startswith("m_steplen")}
        assert not any("×" in v for v in main)  # no false uniform-staircase collapse
        assert dwg.lint_summary()["by_code"].get("axial_length_missing", 0) == 0


class TestStepLadderRecognition:
    """ADR 0008 step 1: the Z step-height ladder draws its step levels from the
    unified turned-step model, which filters by the OD silhouette."""

    def test_blind_bore_floor_is_not_a_phantom_shoulder(self):
        from build123d import Cylinder, Pos

        # Two OD steps (one real interior shoulder at z=15) + a blind axial bore
        # whose flat floor sits at z=30. The floor must NOT be dimensioned as a
        # step height — that was the area-filter phantom the model removes.
        shaft = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
        part = shaft - Pos(0, 0, 45) * Cylinder(5, 30)
        dwg = build_drawing(part, number="D-1")
        # The turned part is now dimensioned by the unified IR step-length chain
        # (#223): two real OD segments (each length 30), and crucially NO '45'
        # bore-floor phantom — recognise_turned_steps excludes the internal bore.
        labels = [o.label for n, o in dwg._named.items() if n.startswith("m_steplen")]
        assert labels == ["30", "30"]  # both real segments
        assert "45" not in labels  # no bore-floor phantom

    def test_plain_z_stepped_shaft_dimensioned_by_ir_chain(self):
        from build123d import Cylinder, Pos

        # A Z-turned stepped shaft is now located by the unified IR step-length
        # chain (#223), not the old engine ladder. Both segments are dimensioned.
        dwg = build_drawing(Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30), number="D-1")
        labels = [o.label for n, o in dwg._named.items() if n.startswith("m_steplen")]
        assert labels == ["30", "30"]
        assert not any(n.startswith("dim_step") for n in dwg._named)  # ladder retired for turned


class TestAxialCoverageLint:
    """lint_axial_coverage — the scoring signal for undimensioned turned steps,
    now counted from the drawing (not the CoverageState side channel, #219)."""

    def test_flags_uncovered_turned_part(self):
        from draftwright.linting import lint_axial_coverage

        # A bare scaffold (views, no step-length dims) → all steps uncovered.
        part = _x_stepped_shaft()
        dwg = build_drawing(part, number="D-1", auto_dims=False)
        issues = lint_axial_coverage(part, dwg)
        assert [i.code for i in issues] == ["axial_length_missing"]
        assert issues[0].severity == "warning"

    def test_clean_when_all_steps_covered(self):
        from draftwright.linting import lint_axial_coverage

        # The engine places the full step-length chain → drawing-derived coverage
        # finds every step located.
        part = _x_stepped_shaft()
        dwg = build_drawing(part, number="D-1")
        assert lint_axial_coverage(part, dwg) == []

    def test_silent_for_non_turned_part(self):
        from draftwright.linting import lint_axial_coverage

        part = Box(80, 60, 20)
        dwg = build_drawing(part, number="D-1", auto_dims=False)
        assert lint_axial_coverage(part, dwg) == []

    def test_z_turned_chain_is_covered(self):
        # A Z-turned shaft is now located by the vertical IR chain (#223), so axial
        # coverage must recognise it (no false positive on a correctly chained Z part).
        from build123d import Cylinder, Pos

        from draftwright.linting import lint_axial_coverage

        part = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
        dwg = build_drawing(part, number="D-1")
        assert lint_axial_coverage(part, dwg) == []

    def test_z_turned_flags_when_uncovered(self):
        # The X-only restriction is gone (#223): a Z-turned shaft with no chain
        # (bare scaffold) is flagged, not silently under-dimensioned.
        from build123d import Cylinder, Pos

        from draftwright.linting import lint_axial_coverage

        part = Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30)
        dwg = build_drawing(part, number="D-1", auto_dims=False)
        assert [i.code for i in lint_axial_coverage(part, dwg)] == ["axial_length_missing"]

    def test_coverage_survives_repair_and_is_idempotent(self):
        # Drawing-derived coverage must stay clean after the repair loop re-places
        # dims (witnesses stay anchored to geometry) and across repeated lint()s.
        part = _x_stepped_shaft()
        dwg = build_drawing(part, number="D-1")  # repair on
        first = [i.code for i in dwg.lint() if i.code == "axial_length_missing"]
        again = [i.code for i in dwg.lint() if i.code == "axial_length_missing"]
        assert first == [] and again == []

    def test_axial_length_missing_is_geometry_aware(self):
        # It is a completeness/standards code, so lint_summary must count it under
        # geometry_issues, not as layout (#226 review follow-through).
        from draftwright.drawing import _GEOMETRY_AWARE_CODES

        assert "axial_length_missing" in _GEOMETRY_AWARE_CODES


def _multi_hole_plate():
    """A plate with three spec-groups of Z-holes (two ø10, one ø16)."""
    from build123d import Box, Cylinder, Pos

    return (
        Box(120, 80, 20)
        - Pos(40, 25, 0) * Cylinder(5, 30)
        - Pos(-40, 25, 0) * Cylinder(5, 30)
        - Pos(0, -25, 0) * Cylinder(8, 30)
    )


def _dense_plate():
    """A small plate crowded with 24 Z-holes in 5 diameter groups. Dense enough
    to stress the layout, but on the auto-sized sheet (#121) its location dims +
    grouped spec-callouts fit, so it group-and-types rather than escalating to a
    hole chart (#93)."""
    import itertools

    from build123d import Box, Cylinder, Pos

    part = Box(70, 50, 12)
    for i, (gx, gy) in enumerate(itertools.product([-25, -15, -5, 5, 15, 25], [-15, -5, 5, 15])):
        part -= Pos(gx, gy, 0) * Cylinder(1.0 + (i % 5) * 0.4, 20)
    return part


class TestHoleTable:
    """#93: hole table placed in a free corner via place_box."""

    class _Boxed:
        def __init__(self, bb):
            from types import SimpleNamespace

            x0, y0, x1, y1 = bb
            self._bb = SimpleNamespace(
                min=SimpleNamespace(X=x0, Y=y0),
                max=SimpleNamespace(X=x1, Y=y1),
            )

        def bounding_box(self):
            return self._bb

    @staticmethod
    def _area(a, b):
        ox = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        oy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        return ox * oy

    def _bbox(self, obj):
        bb = obj.bounding_box()
        return (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y)

    def test_table_has_a_row_per_spec_group(self):
        dwg = build_drawing(_multi_hole_plate())
        n_groups = len([f for f in dwg.features("plan") if f.type == "hole"])
        assert n_groups == 2  # ø10 (×2) and ø16
        tbl = dwg.add_hole_table("plan")
        assert tbl is not None
        assert "hole_table_plan" in dwg.annotations()
        # header + one row per group; the table is a grid Compound.
        assert tbl.table_size[0] > 0 and tbl.table_size[1] > 0

    def test_a_balloon_per_hole_keyed_to_a_row(self):
        # 3 physical holes (1 ø16 → A, 2 ø10 → B) get 3 balloons; tags A,B exist.
        dwg = build_drawing(_multi_hole_plate())
        dwg.add_hole_table("plan")
        balloons = [n for n in dwg.annotations() if n.startswith("balloon_plan_")]
        assert len(balloons) == 3
        tags = {n.split("_")[2] for n in balloons}
        assert tags == {"A", "B"}

    def test_balloons_false_suppresses_them(self):
        dwg = build_drawing(_multi_hole_plate())
        dwg.add_hole_table("plan", balloons=False)
        assert not any(n.startswith("balloon_") for n in dwg.annotations())

    def test_place_band_reports_dropped_overflow(self):
        # #1a review follow-up: a band too small for every balloon drops its tail
        # (the strip solver's prefix fallback) — _place_band must REPORT the dropped
        # count so _add_balloons can surface it as `balloon_dropped` lint, instead of
        # the balloons vanishing silently. 5 balloons needing a 10 mm gap in a 20 mm
        # band fit only 3 (at 0, 10, 20); the other 2 are dropped and reported.
        from types import SimpleNamespace

        rendered: list = []
        stub = SimpleNamespace(_render_balloon=lambda *a: rendered.append(a))
        members = [("t", 0, object(), 0.0, float(i)) for i in range(5)]
        dropped = Drawing._place_band(stub, "plan", members, "y", 50.0, 0.0, 20.0, 10.0, 3.0, 5.0)
        assert dropped == 2 and len(rendered) == 3

    def test_balloon_ring_depth_uses_bare_obstacle_footprints(self):
        from types import SimpleNamespace

        from draftwright._core import _STRIP_GAP

        calls = []
        a = SimpleNamespace(
            PV_X=50.0,
            PV_Y=50.0,
            fv_hw=20.0,
            pv_hh=10.0,
            SV_X=95.0,
            sv_hw=10.0,
            margin=0.0,
            PAGE_H=120.0,
            PAGE_W=120.0,
            FV_Y=20.0,
            fv_hh=5.0,
        )
        pt = a.PV_Y + a.pv_hh
        bare_obstacle = self._Boxed((35.0, pt + 2.0, 65.0, pt + 12.0))

        def place_band(view, members, axis, line, lo, hi, gap, fs, r):
            calls.append((view, members, axis, line, lo, hi, gap, fs, r))
            return 0

        stub = SimpleNamespace(
            _analysis=a,
            _coords={"plan": SimpleNamespace(pp=lambda *_loc: (50.0, 58.0))},
            draft=SimpleNamespace(font_size=3.0),
            iter_annotations=lambda: iter([("bare_obstacle", bare_obstacle)]),
            view_of=lambda _name: "plan",
            _place_band=place_band,
            _record_build_issue=lambda *_args: None,
        )
        hole = SimpleNamespace(location=(0.0, 0.0, 0.0), diameter=4.0)

        Drawing._add_balloons(stub, "plan", [("A", 0, hole)])

        top_call = next(call for call in calls if call[2] == "x" and call[1])
        _view, _members, _axis, line, *_rest, fs, r = top_call
        assert line == pytest.approx(pt + 12.0 + _STRIP_GAP + r)
        assert fs == 3.0

    def test_balloon_assignment_rebalances_across_bands_before_dropping(self):
        from types import SimpleNamespace

        from draftwright._core import _STRIP_GAP, _STRIP_SPACING
        from draftwright.drawing import _strip_capacity

        calls = []
        a = SimpleNamespace(
            PV_X=50.0,
            PV_Y=50.0,
            fv_hw=20.0,
            pv_hh=10.0,
            SV_X=82.0,
            sv_hw=10.0,
            margin=0.0,
            PAGE_H=120.0,
            PAGE_W=120.0,
            FV_Y=30.0,
            fv_hh=5.0,
        )

        def place_band(view, members, axis, line, lo, hi, gap, fs, r):
            calls.append((view, list(members), axis, line, lo, hi, gap, fs, r))
            return 0

        stub = SimpleNamespace(
            _analysis=a,
            _coords={"plan": SimpleNamespace(pp=lambda *_loc: (50.0, 58.0))},
            draft=SimpleNamespace(font_size=3.0),
            iter_annotations=lambda: iter(()),
            view_of=lambda _name: "plan",
            _place_band=place_band,
            _record_build_issue=lambda *_args: None,
        )
        holes = [SimpleNamespace(location=(float(i), 0.0, 0.0), diameter=4.0) for i in range(6)]

        Drawing._add_balloons(
            stub, "plan", [(chr(ord("A") + i), 0, h) for i, h in enumerate(holes)]
        )

        fs = stub.draft.font_size
        r = fs * 1.5
        gap = 2 * r + 2 * _STRIP_SPACING
        top_cap = _strip_capacity(a.PV_X - a.fv_hw - _STRIP_GAP, a.SV_X - a.sv_hw - r, gap)
        top_members = next(call[1] for call in calls if call[2] == "x" and call[3] > a.PV_Y)
        side_members = [m for call in calls if call[2] == "y" for m in call[1]]

        assert len(top_members) == top_cap
        assert len(side_members) == len(holes) - top_cap

    def test_balloon_assignment_cost_uses_actual_band_line_after_furniture_depth(self):
        from types import SimpleNamespace

        calls = []
        a = SimpleNamespace(
            PV_X=50.0,
            PV_Y=50.0,
            fv_hw=20.0,
            pv_hh=10.0,
            SV_X=34.0,
            sv_hw=10.0,
            margin=0.0,
            PAGE_H=120.0,
            PAGE_W=140.0,
            FV_Y=30.0,
            fv_hh=5.0,
        )
        right_obstacle = self._Boxed((71.0, 45.0, 115.0, 55.0))

        def place_band(view, members, axis, line, lo, hi, gap, fs, r):
            calls.append((view, list(members), axis, line, lo, hi, gap, fs, r))
            return 0

        stub = SimpleNamespace(
            _analysis=a,
            _coords={"plan": SimpleNamespace(pp=lambda *_loc: (60.0, 50.0))},
            draft=SimpleNamespace(font_size=3.0),
            iter_annotations=lambda: iter([("right_obstacle", right_obstacle)]),
            view_of=lambda _name: "plan",
            _place_band=place_band,
            _record_build_issue=lambda *_args: None,
        )
        hole = SimpleNamespace(location=(0.0, 0.0, 0.0), diameter=4.0)

        Drawing._add_balloons(stub, "plan", [("A", 0, hole)])

        left_members = next(call[1] for call in calls if call[2] == "y" and call[3] < a.PV_X)
        right_members = next(call[1] for call in calls if call[2] == "y" and call[3] > a.PV_X)
        assert [m[0] for m in left_members] == ["A"]
        assert right_members == []

    def test_table_and_balloons_keep_lint_clean(self):
        # covers_diameters lets coverage lint count the tabulated holes, and the
        # balloons are furniture (is_centerline) so they do not trip overlap lint.
        dwg = build_drawing(_multi_hole_plate())
        before = {i.code for i in dwg.lint()}
        dwg.add_hole_table("plan")
        assert {i.code for i in dwg.lint()} == before
        assert dwg._named["hole_table_plan"].covers_diameters == (16.0, 10.0)

    def test_table_does_not_overlap_views_or_title_block(self):
        dwg = build_drawing(_multi_hole_plate())
        dwg.add_hole_table("plan")
        tb = self._bbox(dwg._named["hole_table_plan"])
        for v in dwg.views:
            assert self._area(tb, dwg.view_bounds(v)) == 0.0, v
        assert self._area(tb, self._bbox(dwg._named["title_block"])) == 0.0

    def test_no_holes_in_view_returns_none(self):
        from build123d import Box

        dwg = build_drawing(Box(60, 40, 20))
        assert dwg.add_hole_table("plan") is None
        assert "hole_table_plan" not in dwg.annotations()

    def test_table_dropped_when_it_will_not_fit(self, monkeypatch):
        import sys

        m = sys.modules["draftwright.drawing"]
        monkeypatch.setattr(m, "fit_box", lambda *a, **k: None)
        dwg = build_drawing(_multi_hole_plate())
        assert dwg.add_hole_table("plan") is None
        assert "table_dropped" in {i.code for i in dwg.lint()}

    def test_tag_sequence_rolls_over_past_z(self):
        from draftwright._core import _tag_sequence

        seq = _tag_sequence(28)
        assert seq[:3] == ["A", "B", "C"]
        assert seq[25] == "Z"
        assert seq[26] == "AA"
        assert seq[27] == "AB"
        # The base-26 rollover boundary and uniqueness.
        full = _tag_sequence(703)
        assert full[701] == "ZZ"
        assert full[702] == "AAA"
        assert len(set(full)) == 703  # bijective — no dup or skip

    def test_table_keeps_lint_clean(self, tmp_path):
        # The label-less table must not trip annotation_overlap / view-overlap
        # lint, and the mixed Edge+Text Compound must export cleanly.
        dwg = build_drawing(_multi_hole_plate())
        before = {i.code for i in dwg.lint()}
        dwg.add_hole_table("plan")
        after = {i.code for i in dwg.lint()}
        assert after == before  # no new lint codes from the table
        svg, dxf = dwg.export(str(tmp_path / "t"))
        assert Path(svg).stat().st_size > 0 and Path(dxf).stat().st_size > 0

    def test_table_geometry_is_deterministic(self):
        from draftwright.drawing import _build_table

        rows = [("TAG", "⌀", "QTY"), ("A", "ø10", "2")]
        a = build_drawing(Box(60, 40, 20)).draft
        assert _build_table(rows, a).table_size == _build_table(rows, a).table_size

    def test_generic_add_table_places_arbitrary_rows(self):
        # The builder is generic: a gear/BOM-style param table places like a
        # hole table, clear of the views and title block.
        dwg = build_drawing(_multi_hole_plate())
        rows = [("PARAMETER", "VALUE"), ("MODULE", "0.5"), ("RATIO", "13:1")]
        tbl = dwg.add_table(rows, name="gear_data")
        assert tbl is not None and "gear_data" in dwg.annotations()
        tb = self._bbox(tbl)
        for v in dwg.views:
            assert self._area(tb, dwg.view_bounds(v)) == 0.0, v


@pytest.fixture(scope="module")
def dense_plate_dwg():
    """Shared **read-only** build of ``_dense_plate()`` for the escalation assertions
    that only inspect the finished drawing (#153 — each rebuilt the ~20 s dense-plate
    just to read a different property). Tests that mutate the drawing (append an
    escalation, record an issue, run the resolver) must build their own."""
    return build_drawing(_dense_plate())


class TestEscalation:
    """#93: a too-dense plan view auto-escalates to a hole chart + balloons."""

    def test_dense_part_groups_and_types(self, dense_plate_dwg):
        # Sized honestly for its real annotation footprint (#121, ADR 0004), the
        # sheet grows so the X-location dims + grouped spec-callouts fit — so this
        # moderately-dense plate no longer escalates to a per-hole table + balloon
        # ring (the worse representation for a dense varying-diameter field). It
        # group-and-types instead: spec-group callouts (5× ⌀…) + location dims,
        # lint clean. The table/balloon escalation path remains for parts too
        # dense to fit even that — covered by the CTC-02 slow-tier test.
        dwg = dense_plate_dwg
        ann = dwg.annotations()
        assert "hole_table_plan" not in ann
        assert not any(n.startswith("balloon_") for n in ann)
        assert sum(1 for n in ann if n.startswith("hc_plan")) >= 1  # spec-group callouts
        assert any(n.startswith("m_locx") for n in ann)  # location dims placed, not dropped
        assert [i for i in dwg.lint() if i.severity in ("warning", "error")] == []

    def test_escalation_clears_density_lint(self, dense_plate_dwg):
        # No callout_dropped / location_ref_dropped / count-mismatch warnings
        # survive once the dense plate is dimensioned — whether by group-and-type
        # (now, on the auto-sized sheet) or by the table escalation it used to need.
        dwg = dense_plate_dwg
        warns = {i.code for i in dwg.lint() if i.severity in ("warning", "error")}
        assert "callout_dropped" not in warns
        assert "location_ref_dropped" not in warns
        assert "feature_count_mismatch" not in warns

    def test_sparse_part_is_not_tabulated(self):
        # A sparse plate dimensions every hole individually — no table, unchanged.
        dwg = build_drawing(_multi_hole_plate())
        assert "hole_table_plan" not in dwg.annotations()
        assert not any(n.startswith("balloon_") for n in dwg.annotations())
        assert any(n.startswith("hc_plan") for n in dwg.annotations())

    def test_wrap_rows_reshapes_into_blocks(self):
        from draftwright.annotate import _wrap_rows

        header = ("T", "D")
        data = [("a", "1"), ("b", "2"), ("c", "3"), ("d", "4"), ("e", "5")]
        wide = _wrap_rows(header, data, 2)  # 5 rows → 3 per block, 2 blocks
        assert wide[0] == ("T", "D", "T", "D")  # header repeated per block
        assert wide[1] == ("a", "1", "d", "4")  # row 0 of each block
        assert wide[3] == ("c", "3", "", "")  # ragged tail padded blank


class TestPatternGroupBalloon:
    """#351 PR-3 (ADR 0009 Amdt 1 decision 1, the #348 fix): a dropped ISO
    pattern callout gets ONE balloon tagging the whole pattern, not one per
    member. Exercised directly against the resolver with a synthetic dropped
    Escalation — forcing a real drop needs a part crowded enough that even the
    auto-grown page can't fit it (the CTC-02 slow-tier fixture is the
    naturally-occurring case)."""

    @staticmethod
    def _fake_pattern(count, diameter, origin=(0.0, 0.0, 0.0)):
        from draftwright.model import Frame, HoleFeature, PatternFeature

        member = HoleFeature(
            frame=Frame(origin=origin, axis="z"), diameter=diameter, depth=None, through=True
        )
        # Real recognised patterns always populate `members` (detect.py's
        # `_pattern_feature`) — the resolver anchors the balloon on a real member,
        # not the pattern's abstract centre, so the fixture must match.
        members = tuple((origin[0] + i, origin[1], origin[2]) for i in range(count))
        return PatternFeature(
            frame=Frame(origin=origin, axis="z"),
            pattern="bolt_circle",
            count=count,
            member=member,
            members=members,
        )

    def test_dropped_pattern_gets_one_grouped_balloon(self):
        from draftwright.annotations._common import Escalation
        from draftwright.annotations.orchestrator import _maybe_tabulate_holes

        dwg = build_drawing(_multi_hole_plate())  # sparse — density gate stays shut
        before = set(dwg.annotations())
        feat = self._fake_pattern(count=6, diameter=5.0)
        dwg._escalations.append(
            Escalation(kind="callout", view="plan", feature=feat, reason="strip_full")
        )
        # Mirror what _record_callout_drop does in production, so clearing it below
        # actually exercises the resolve path rather than trivially passing.
        dwg._record_build_issue("warning", "callout_dropped", "synthetic plan-view drop")
        _maybe_tabulate_holes(dwg, dwg._analysis)

        assert "hole_table_plan" not in dwg.annotations()  # density gate untouched
        new_balloons = [
            n for n in dwg.annotations() if n.startswith("balloon_") and n not in before
        ]
        assert len(new_balloons) == 1
        assert new_balloons[0].split("_")[2] == "6×A"
        assert "callout_dropped" not in {i.code for i in dwg.lint()}  # resolved, not just hidden

    def test_multiple_dropped_patterns_get_distinct_non_overlapping_balloons(self):
        from draftwright.annotations._common import Escalation
        from draftwright.annotations.orchestrator import _maybe_tabulate_holes

        dwg = build_drawing(_multi_hole_plate())
        feats = [
            self._fake_pattern(count=4, diameter=3.0, origin=(-15.0, -8.0, 0.0)),
            self._fake_pattern(count=6, diameter=5.0, origin=(15.0, 8.0, 0.0)),
        ]
        for feat in feats:
            dwg._escalations.append(
                Escalation(kind="callout", view="plan", feature=feat, reason="strip_full")
            )
        _maybe_tabulate_holes(dwg, dwg._analysis)

        balloons = [n for n in dwg.annotations() if n.startswith("balloon_plan_")]
        assert {n.split("_")[2] for n in balloons} == {"4×A", "6×B"}
        # The shared-band placement (one _add_balloons call) must not stack them.
        boxes = [dwg._named[n].bounding_box() for n in balloons]
        b0, b1 = boxes
        overlaps = (
            b0.min.X < b1.max.X
            and b1.min.X < b0.max.X
            and (b0.min.Y < b1.max.Y and b1.min.Y < b0.max.Y)
        )
        assert not overlaps

    def test_unresolved_pattern_in_other_view_keeps_the_drop_lint(self):
        # A pattern drop the resolver does not cover (a non-plan view) must not
        # have its callout_dropped warning silently cleared.
        from draftwright.annotations._common import Escalation
        from draftwright.annotations.orchestrator import _maybe_tabulate_holes

        dwg = build_drawing(_multi_hole_plate())
        feat = self._fake_pattern(count=3, diameter=4.0)
        dwg._escalations.append(
            Escalation(kind="callout", view="front", feature=feat, reason="front strip full")
        )
        dwg._record_build_issue("warning", "callout_dropped", "synthetic front-view drop")
        _maybe_tabulate_holes(dwg, dwg._analysis)

        assert not any(n.startswith("balloon_") for n in dwg.annotations())
        assert "callout_dropped" in {i.code for i in dwg.lint()}


class TestDraftwrightAttribution:
    """draftwright self-attribution in the title block + clickable SVG link."""

    def test_author_appends_draftwright(self):
        from draftwright._core import _attribution_author

        assert _attribution_author("P. Fremantle") == "P. Fremantle / draftwright"

    def test_author_defaults_to_draftwright(self):
        from draftwright._core import _attribution_author

        assert _attribution_author("") == "draftwright"
        assert _attribution_author(None) == "draftwright"
        assert _attribution_author("   ") == "draftwright"

    def test_link_rect_sits_over_the_drawn_by_cell(self):
        # The hyperlink rect must cover the "drawn by" cell of the *rendered*
        # title block: bottom row (half the two-row block height), from the
        # drawn-by cell's left edge to the block's right edge. The left edge is
        # derived from the block's public cell bbox (#139); everything is asserted
        # against the placed block's bounding box so it catches drift if the rect
        # or the upstream TitleBlock layout ever diverge.
        dwg = build_drawing(Box(60, 40, 20))
        x0, y0, x1, y1 = dwg._draftwright_link_rect
        tb = dwg._named["title_block"]
        bb = tb.bounding_box()
        cell = tb.drawn_by_cell_bbox()  # build-frame; block min corner is at bb.min
        assert x1 == pytest.approx(bb.max.X, abs=0.5)  # flush to block right edge
        assert y0 == pytest.approx(bb.min.Y, abs=0.5)  # block bottom
        assert y1 - y0 == pytest.approx((bb.max.Y - bb.min.Y) / 2, abs=0.5)  # one row
        assert x0 == pytest.approx(bb.min.X + cell["min_x"], abs=0.5)  # drawn-by cell left
        assert 0 < x0 < x1 <= dwg.page_w and 0 < y0 < y1 <= dwg.page_h

    def test_add_svg_hyperlink_injects_anchor(self, tmp_path):
        from draftwright.export import _DRAFTWRIGHT_URL, add_svg_hyperlink

        svg = tmp_path / "x.svg"
        svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 -100 200 100"><g/></svg>',
            encoding="utf-8",
        )
        add_svg_hyperlink(str(svg), (150.0, 10.0, 190.0, 18.0))
        out = svg.read_text(encoding="utf-8")
        assert "xmlns:xlink" in out  # namespace declared so xlink:href is valid
        assert f'href="{_DRAFTWRIGHT_URL}"' in out
        # page (x, y) -> svg (x, -y): rect top-left = (150, -18), size 40 x 8
        assert 'x="150.000" y="-18.000" width="40.000" height="8.000"' in out
        assert 'pointer-events="all"' in out

    def test_export_svg_carries_the_clickable_link(self, tmp_path):
        dwg = build_drawing(Box(60, 40, 20))
        svg_path, _ = dwg.export(str(tmp_path / "out"))
        svg = Path(svg_path).read_text(encoding="utf-8")
        assert "github.com/pzfreo/draftwright" in svg
        assert "<a " in svg and "</a>" in svg

    def test_export_embeds_metadata_in_svg_and_dxf(self, tmp_path):
        dwg = build_drawing(Box(60, 40, 20), drawn_by="P. Fremantle")
        svg_path, dxf_path = dwg.export(str(tmp_path / "m"))
        svg = Path(svg_path).read_text(encoding="utf-8")
        assert "<dc:creator>draftwright</dc:creator>" in svg
        assert "Generated by draftwright" in svg
        dxf = Path(dxf_path).read_text(encoding="utf-8", errors="ignore")
        assert "GeneratedBy" in dxf and "draftwright" in dxf

    def test_export_pdf_carries_clickable_link(self, tmp_path):
        # Exercises the load-bearing SVG->PDF coordinate transform + reportlab
        # link annotation. svglib + reportlab are core deps (pure Python, no
        # native cairo), so this runs on every platform. The URI may live in a
        # FlateDecode object stream, so scan the decompressed streams too.
        import re as _re
        import zlib

        dwg = build_drawing(Box(60, 40, 20))
        pdf_path = dwg.export_pdf(str(tmp_path / "p"))
        data = Path(pdf_path).read_bytes()
        found = b"pzfreo/draftwright" in data
        for m in _re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, _re.S):
            try:
                chunk = zlib.decompress(m.group(1))
            except Exception:
                continue
            if b"/URI" in chunk and b"pzfreo" in chunk:
                found = True
        assert found, "PDF must embed a clickable draftwright URI link annotation"
