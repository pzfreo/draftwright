"""Prismatic plate-thickness and step-position dimensioning."""

import pytest
from _drawing_helpers import sheet_script_drawing as _sheet_script_drawing
from _kernel import B123D_GE_011, SKIP_011
from build123d import Box, Cylinder, Pos, Rotation

from draftwright import build_drawing
from draftwright.linting import LintIssue

_skip_011 = pytest.mark.skipif(B123D_GE_011, reason=SKIP_011)


def _plate_labels(d):
    return sorted(str(o.label) for _, o in d.iter_annotations() if getattr(o, "label", None))


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
        plate_dims = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("dim_plate")
        }
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
        assert not [n for n in dwg.annotations() if n.startswith("dim_plate")]
        assert _plate_labels(dwg).count("10") == 1  # only dim_height

    def test_channel_gap_is_not_read_as_a_plate(self):
        # A U-channel has two upright walls with AIR between them (facing +Y/-Y inward) —
        # the opposite face arrangement from a plate. The recogniser must not emit a
        # thickness across the gap.
        part = (
            Box(80, 60, 10) + Pos(0, -25, 30) * Box(80, 10, 40) + Pos(0, 25, 30) * Box(80, 10, 40)
        )
        dwg = build_drawing(part, number="X")
        plate_vals = sorted(
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("dim_plate")
        )
        # each 10-thick wall is a plate (Y), but the 40 mm air gap between them is NOT
        assert "40" not in plate_vals
        assert "50" not in plate_vals

    def test_rotational_part_has_no_plate_dims(self):
        # A turned/rotational part's extents are the OD / length chain, not plate
        # thicknesses — plate detection is gated off for it.
        part = Cylinder(20, 8)  # a thin disc: thin in Z, but rotational
        dwg = build_drawing(part, number="X")
        assert not [n for n in dwg.annotations() if n.startswith("dim_plate")]


class TestStepPosition:
    """#555: a prismatic step/rebate's along-axis POSITION is dimensioned, not just its
    two heights, so the part is fully constrained."""

    def test_step_position_dimensioned(self):
        # The issue's acceptance test: an asymmetric step so the position can't hide
        # behind another value. shelf 20 deep at the front, back 40 deep, lowered by 15.
        part = Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15)
        # A3 pinned: this test is about the step POSITION being dimensioned, not about
        # which sheet the chooser lands on. At the auto A4/1:1 the overall depth is
        # withheld — the title block fills the side view's below strip, and the chooser
        # picked the sheet without knowing that dimension needed the room (#1590). On A3
        # or at 1:2 it places. Before #1593 the same `60` was DRAWN with its label inside
        # the block (measured at 174.9-178.1 x 38.9-41.1, against a block topping out at
        # 43.1), reported only as `annotation_overlap` between '60' and 'DRAWING'.
        dwg = build_drawing(part, number="X", page="A3")
        lbl = _plate_labels(dwg)
        assert {"80", "60", "30", "15"} <= set(lbl)  # overall + heights already present
        assert "20" in lbl or "40" in lbl  # step position / shelf depth — was ABSENT
        # from recognised step intent, in the side (profile) view where the step reads
        pos = {
            n: dwg.get_annotation(n).label
            for n in dwg.annotations()
            if n.startswith("dim_shoulder")
        }
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
        placed = sorted(
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("dim_shoulder")
        )
        assert placed == ["20"]  # the shoulder position survives the recompose

    def test_finalize_drains_step_positions_after_a_mid_replay_raise(self, monkeypatch):
        # #636/#647: A0b registers the step-position corridor candidates and drops their
        # intents before the fallible B1 callout phase. If that phase raises, finalize's
        # transactional rollback (#647) restores the step-position intent — so a retry
        # re-registers from it and drains, rather than stranding the candidates.
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
            dwg.finalize()  # A0b registered the step candidates; B1 raises → rollback
        # The rollback restored the step-position INTENT; nothing was committed.
        assert any(it.kwargs.get("role") == "step_position" for it in dwg._intents)
        assert not [n for n in dwg.annotations() if n.startswith("dim_shoulder")]

        dwg.finalize()  # retry: re-registers from the surviving intent and drains
        placed = sorted(
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("dim_shoulder")
        )
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

        def _boom(ctx, d):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected drain failure")
            return real(ctx, d)

        monkeypatch.setattr(_common, "drain_corridors", _boom)
        with pytest.raises(RuntimeError):
            dwg.finalize()  # A0b registered the candidates; the drain then raises
        # The rollback restored the step-position INTENT; a retry re-runs from it and drains.
        assert any(it.kwargs.get("role") == "step_position" for it in dwg._intents)
        dwg.finalize()  # retry: re-registers from the surviving intent and drains
        placed = sorted(
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("dim_shoulder")
        )
        assert placed == ["20"]

    def test_finalize_rolls_back_a_partial_commit(self, monkeypatch):
        # #647: finalize is transactional. A drain that raises AFTER an earlier stage already
        # committed annotations must not leave them behind — else a retry re-runs the source
        # intents and duplicates the measurement (the m_locx0 + m_locx1 defect). The rollback
        # restores _named/items/_intents AND the coverage bookkeeping to the pre-finalize state
        # (the B1 callout records scattered-hole coverage; leaving it mutated would make a later
        # lint() false-clean, #647 review), so a clean retry places each dimension exactly once.
        from draftwright.annotations import _common

        # A step positioned in B2's drain, plus an off-centre hole whose callout live-places in
        # leg B1 — so an annotation is COMMITTED before the B2 drain raises, and must roll back.
        part = (
            Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15) - Pos(20, 15, 0) * Cylinder(3, 30)
        )
        dwg = build_drawing(part, auto_dims=False)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        hole = next(f for f in dwg.model().features if f.kind == "hole")
        dwg._defer_intents = True
        dwg.callout(hole)  # commits in leg B1, before the B2 drain
        dwg.dimension(step, "length", role="step_position")  # drained in B2
        names_before, items_before = set(dwg.annotations()), len(dwg.items)
        intents_before = len(dwg._intents)
        coverage_before = dwg.coverage.snapshot()
        issues_before = dwg.registry.issues
        suppressions_before = dwg.suppressions()

        calls = {"n": 0}
        real = _common.drain_corridors

        def _boom(ctx, d):
            calls["n"] += 1
            if calls["n"] == 1:
                # A pass that records a build issue and THEN raises: the issue is part of the
                # transaction too (#720 canary — the rollback restored _named/items/coverage
                # but nothing asserted the build issues, so a no-op restore passed this suite).
                dwg.registry.record_issue(
                    LintIssue(severity="warning", message="mid-drain", code="injected")
                )
                raise RuntimeError("injected drain failure")
            return real(ctx, d)

        monkeypatch.setattr(_common, "drain_corridors", _boom)
        with pytest.raises(RuntimeError):
            dwg.finalize()
        # Rolled back to exactly the pre-finalize state — the B1 callout is gone again.
        assert set(dwg.annotations()) == names_before
        assert len(dwg.items) == items_before
        assert len(dwg._intents) == intents_before
        assert dwg.coverage.snapshot() == coverage_before  # coverage restored (#647 review)
        assert dwg.registry.issues == issues_before  # the mid-drain issue rolled out too
        # The audit ledger (#996) is NOT part of the transaction, and that is the guarantee
        # rather than an oversight: finalize recompiles the same immutable model and never
        # writes _build.omissions, so there is nothing to roll back. Asserted explicitly
        # (Codex #996 r4) so a future stage that DOES write it has to notice this and add it
        # to the snapshot set above.
        assert dwg.suppressions() == suppressions_before

        monkeypatch.undo()
        dwg.finalize()  # clean retry — the shoulder position places exactly once (no duplicate)
        assert len([n for n in dwg.annotations() if n.startswith("dim_shoulder")]) == 1

    def test_finalize_rolls_back_a_narrowed_analysis_bound(self, monkeypatch):
        # #647 review: render_locations narrows a.sv_zones.above.outer_limit IN PLACE on the
        # shared Analysis. A raise after that narrowing must restore it — else a corrected retry
        # solves a side-above dim against a stale cap (the one Analysis field a replay mutates).
        # Inject the in-place narrowing at the drain, then raise, and assert the bound is restored.
        from draftwright.annotations import _common

        part = Box(80, 60, 30) - Pos(0, -20, 7.5) * Box(80, 20, 15)  # step → the B2 drain runs
        dwg = build_drawing(part, auto_dims=False)
        step = next(f for f in dwg.model().features if f.kind == "step_level")
        dwg._defer_intents = True
        dwg.dimension(step, "length", role="step_position")
        strip = dwg._analysis.sv_zones.above
        limit_before = strip.outer_limit

        def _boom(ctx, d):
            strip.outer_limit = limit_before - 50.0  # mimic render_locations' in-place narrowing
            raise RuntimeError("injected drain failure")

        monkeypatch.setattr(_common, "drain_corridors", _boom)
        with pytest.raises(RuntimeError):
            dwg.finalize()
        assert dwg._analysis.sv_zones.above.outer_limit == limit_before  # restored (#647 review)

    def test_centered_rebate_dimensions_both_shoulders(self):
        # A symmetric central channel has TWO shoulders; both positions must be given
        # (20 and 40 from the front datum), else the channel is under-constrained.
        dwg = build_drawing(Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15), number="X")
        pos = sorted(
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("dim_shoulder")
        )
        assert pos == ["20", "40"]
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_x_axis_step_positioned_in_plan(self):
        # A step whose shoulder runs along X is located above the plan view (the axis→view
        # mapping the hole-location ladder uses), not the side view.
        dwg = build_drawing(Box(80, 60, 30) - Pos(-25, 0, 7.5) * Box(30, 60, 15), number="X")
        pos = {n: dwg.view_of(n) for n in dwg.annotations() if n.startswith("dim_shoulder")}
        assert pos and set(pos.values()) == {"plan"}

    def test_plain_block_has_no_step_position(self):
        # No step → no shoulder dim.
        dwg = build_drawing(Box(40, 30, 12), number="X")
        assert not [n for n in dwg.annotations() if n.startswith("dim_shoulder")]

    def test_through_slot_is_not_a_step_shoulder(self):
        # A slot's walls are interior vertical faces but not step risers (no step level);
        # the slot recogniser dimensions them, so no spurious step-position dim appears.
        dwg = build_drawing(Box(50, 30, 20) - Box(20, 8, 30), number="X")
        assert not [n for n in dwg.annotations() if n.startswith("dim_shoulder")]

    def test_raised_pad_is_not_a_step_shoulder(self):
        # #555 review: a raised rectangular pad/island rises from the base-top level, but
        # its walls do NOT span the part edge-to-edge — only a genuine step/rebate does. A
        # pad must not be mis-located as a shoulder.
        dwg = build_drawing(Box(80, 60, 10) + Pos(0, 0, 10) * Box(40, 40, 10), number="X")
        assert not [n for n in dwg.annotations() if n.startswith("dim_shoulder")]

    def test_blind_pocket_is_not_a_step_shoulder(self):
        # #555 review: a blind pocket's floor IS a step level, but its walls are bounded
        # (not full-span), so it is not read as a step shoulder.
        dwg = build_drawing(Box(80, 60, 30) - Pos(0, 0, 5) * Box(30, 20, 20), number="X")
        assert not [n for n in dwg.annotations() if n.startswith("dim_shoulder")]

    def test_step_position_round_trips_through_generated_script(self, tmp_path):
        # #555 review: the emitted script must keep the step position, else a regenerated
        # drawing is under-constrained again — the very bug. A CENTERED rebate (two shoulders
        # on one step_level) is the case a per-shoulder verb would crash on: one line carries
        # both. Migrated to the Sheet script when #940 retired the imperative one; the
        # "exactly one verb rebuilds all shoulders" claim is now "one step_level line", and
        # full parity with the automatic drawing replaces the two label spot-checks.
        part = Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)  # two shoulders: 20 and 40
        source, scripted = _sheet_script_drawing(part, tmp_path, "stepped")

        assert len([ln for ln in source.splitlines() if "sheet.step_level(" in ln]) == 1
        labels = [
            str(o.label) for _, o in scripted.iter_annotations() if getattr(o, "label", None)
        ]
        assert "20" in labels and "40" in labels  # both shoulder positions survive
        auto = build_drawing(part)
        assert {n for n, _ in scripted.iter_annotations()} == {
            n for n, _ in auto.iter_annotations()
        }

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
        labels = [str(o.label) for _, o in dwg.iter_annotations() if getattr(o, "label", None)]
        assert "20" in labels
