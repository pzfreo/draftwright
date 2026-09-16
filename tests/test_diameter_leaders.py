"""Diameter-leader anchoring and silhouette-crossing behavior."""

from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos, Rotation

from draftwright import build_drawing


def _ctx_for(dwg):
    """A `PlacementContext` wired to a real `Drawing`'s public seams (#817) — for white-box unit
    tests that call an internal render helper (`ctx.place` routes to `dwg`'s registry + item list)."""
    from draftwright.annotations._common import PlacementContext

    return PlacementContext(registry=dwg.registry, coverage=dwg.coverage, items=dwg.items)


class TestDiameterStepAnchor:
    """Unit tests for the shared-diameter leader anchor (#794 review)."""

    @staticmethod
    def _step(axis, origin, lo, hi):
        from draftwright.model.ir import Frame, StepFeature

        idx = {"x": 0, "y": 1, "z": 2}[axis]
        p_lo, p_hi = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        p_lo[idx], p_hi[idx] = lo, hi
        return StepFeature(
            frame=Frame(tuple(origin), axis),
            length=hi - lo,
            diameter=10.0,
            span=(tuple(p_lo), tuple(p_hi)),
        )

    def test_uses_the_longest_runs_own_origin_for_non_coaxial_steps(self):
        # Two X-axis ø10 steps on DIFFERENT axes: a short run centred at y=0 and a
        # longer run centred at y=20. The anchor must be the LONG step's own point
        # (axial mid 30, radial y=20), not a hybrid that takes the axial from the
        # long step and the radial from the bucket's first (short) step — which
        # would land the arrow off the selected step's silhouette.

        from draftwright.annotations.from_model import _diameter_step_anchor
        from draftwright.model.compiled import compile_dimensions
        from draftwright.model.ir import PartModel
        from draftwright.model.planner import plan_dimensions

        short = self._step("x", (0.0, 0.0, 0.0), -2.5, 2.5)  # len 5, centre x=0
        long = self._step("x", (30.0, 20.0, 0.0), 20.0, 40.0)  # len 20, centre x=30, y=20
        # A real BoundBox, not a namespace modelling whichever fields one caller happens to
        # read. `_compile_overall_height` now mints the height's identity from the bbox (#1230)
        # via `_envelope_from_bbox`, which calls `center()`. Two successive stub versions broke
        # here — the first lacked X and Y, the second added them but asserted an impossible
        # geometry (`size.Y=30` with `max.Y-min.Y=15`) and still had no `center()`. A stub of a
        # value type should be the value type (#1233 review).
        from build123d import Location

        bbox = (Location((0, 7.5, 10)) * Box(80, 15, 20)).bounding_box()
        model = PartModel(bbox=bbox, orientation="x", features=[short, long])
        groups = plan_dimensions(model)
        plan = compile_dimensions(model, groups=groups)
        # `anchor` is the FIRST-bucketed feature's origin (the short step, y=0).
        got = _diameter_step_anchor(short.frame.origin, plan.of_kind("step"))
        assert got == (30.0, 20.0, 0.0), f"hybrid/wrong anchor: {got}"

    def test_no_step_in_the_group_falls_back_to_the_given_anchor(self):
        # A boss-only ⌀ (no step span to centre on) keeps the frame origin, which
        # round-trips through the emitted script (#707).
        from draftwright.annotations.from_model import _diameter_step_anchor

        assert _diameter_step_anchor((1.0, 2.0, 3.0), set()) == (1.0, 2.0, 3.0)


class TestLeaderCrossesSilhouette:
    """#796 Phase 1: an info-level lint notice when a ⌀ leader shaft cuts through
    the part body. Integration-level (real projected views); the discriminator
    itself is unit-tested in test_lint_structural."""

    @staticmethod
    def _cyl(r, h, z):
        from build123d import Align

        return Pos(0, 0, z) * Cylinder(r, h, align=(Align.CENTER, Align.CENTER, Align.MIN))

    def test_groove_leader_uses_the_clear_single_exit_candidate(self):
        # The shared #1166 inventory can choose the vertical candidate that exits
        # the thin neck once, instead of the old diagonal route through a flange.
        # A single silhouette exit is legitimate and must remain lint-clean.
        part = Rotation(0, 90, 0) * (
            self._cyl(15, 10, 0.0) + self._cyl(3, 2, 10) + self._cyl(15, 10, 12)
        )
        dwg = build_drawing(part, number="X")
        issues = [i for i in dwg.lint() if i.code == "leader_crosses_silhouette"]
        assert issues == []
        groove = next(
            annotation
            for name, annotation in dwg.iter_annotations()
            if name.startswith("m_groove")
        )
        assert groove.segments[0][0][0] == pytest.approx(groove.segments[0][1][0])

    def test_nested_boss_diameter_routes_to_the_clear_side(self):
        # #798: the ø6 boss stub whose row-solved leader would cut through the ø30
        # flange is re-routed to the clear margin instead — no crossing survives, and
        # the ø6 is still called out on the main view as a normal m_dia leader whose
        # elbow now sits past the part's left edge (the clear side), not in the body.
        part = Rotation(0, 90, 0) * (
            self._cyl(3, 0.5, 0.0) + self._cyl(15, 20, 0.5) + self._cyl(10, 15, 20.5)
        )
        dwg = build_drawing(part)
        assert dwg.lint_summary()["by_code"].get("leader_crosses_silhouette", 0) == 0
        ldr = next(
            o
            for n, o in dwg.iter_annotations()
            if n.startswith("m_dia") and str(getattr(o, "label", "")) == "ø6"
        )
        fb = dwg.view_bounds("front")
        assert ldr.elbow[0] <= fb[0], "ø6 leader should route to the clear left margin"

    def test_grm03_end_boss_routes_off_the_body(self):
        # The GRM-03 ø6 end boss: its row-solved leader diagonals back INTO the disc
        # (the near-miss clip). #798 pulls an end-boss leader out to its clear margin,
        # so its elbow ends up LEFT of the tip, not diagonally right into the body.
        fixture = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw.step"
        dwg = build_drawing(step_file=str(fixture))
        ldr = next(
            o
            for n, o in dwg.iter_annotations()
            if n.startswith("m_dia") and str(getattr(o, "label", "")) == "ø6"
        )
        assert ldr.elbow[0] < ldr.tip[0], "ø6 end-boss leader should route left, off the body"

    def test_z_turned_end_boss_routes_to_the_axial_margin(self):
        # #801 review: an m_dia_z (Z-turned) leader's axial run is page-Y, so a
        # Z-turned end boss must route to the TOP/BOTTOM margin, not left/right. A ø6
        # boss stub on top of a ø30 disc routes straight up to the top margin (same X
        # as the tip) — the X-only trigger would move it sideways instead.
        from build123d import Align

        b = Align.MIN
        part = Cylinder(15, 20, align=(Align.CENTER, Align.CENTER, b)) + Pos(0, 0, 20) * Cylinder(
            3, 0.5, align=(Align.CENTER, Align.CENTER, b)
        )
        dwg = build_drawing(part)
        assert dwg.lint_summary()["by_code"].get("leader_crosses_silhouette", 0) == 0
        ldr = next(
            o
            for n, o in dwg.iter_annotations()
            if n.startswith("m_dia_z") and str(getattr(o, "label", "")) == "ø6"
        )
        fb = dwg.view_bounds("front")
        assert ldr.elbow[1] >= fb[3], "ø6 Z-turned boss should route to the top margin"
        assert abs(ldr.elbow[0] - ldr.tip[0]) < 1e-6, "routed along the wrong (radial) axis"

    def _crossing_boss_drawing(self):
        # A built nested-boss drawing whose ø6 leader has been forced back to a
        # crossing diagonal (build_drawing already re-routes it, so put it back).
        from build123d_drafting import Leader

        part = Rotation(0, 90, 0) * (
            self._cyl(3, 0.5, 0.0) + self._cyl(15, 20, 0.5) + self._cyl(10, 15, 20.5)
        )
        dwg = build_drawing(part)
        tip = dwg.get_annotation("m_dia_x0").tip
        dwg.remove("m_dia_x0")
        crossing = (tip[0] + 3.0, tip[1] - 15.0, 0.0)  # diagonal down into the flange body
        dwg._add(
            Leader(tip=(tip[0], tip[1], 0), elbow=crossing, label="ø6", draft=dwg.draft),
            "m_dia_x0",
            view="front",
        )
        return dwg, crossing

    def test_reroute_skips_a_pinned_leader(self):
        # A pin is the user's "this stays put" (ADR 2 (was 0012)) — the re-router must never
        # move a pinned ø leader, even one that crosses.
        from draftwright.annotations.from_model import _reroute_crossing_diameters

        dwg, crossing = self._crossing_boss_drawing()
        dwg.pin("m_dia_x0")
        _reroute_crossing_diameters(dwg, ctx=_ctx_for(dwg))
        moved = dwg.get_annotation("m_dia_x0").elbow
        assert (moved[0], moved[1]) == crossing[:2], "pinned leader moved"

    def test_reroute_restores_the_leader_when_no_candidate_is_clear(self, monkeypatch):
        # If no candidate is clear+safe, the original leader is RESTORED (Phase-1 then
        # flags it) — a re-route must never lose the dimension.
        from draftwright.annotations.from_model import _reroute_crossing_diameters

        dwg, crossing = self._crossing_boss_drawing()
        # Force every route to read as cutting, so no candidate is ever accepted. The
        # re-router now measures against the shared material field (#798), so this
        # patches that predicate rather than the retired outline-crossing one.
        monkeypatch.setattr(
            "draftwright.annotations.from_model.material_penalty_units", lambda *a, **k: 1
        )
        _reroute_crossing_diameters(dwg, ctx=_ctx_for(dwg))
        restored = dwg.get_annotation("m_dia_x0")
        assert restored is not None, "leader lost by the re-route"
        assert (restored.elbow[0], restored.elbow[1]) == crossing[:2], "leader not restored"

    def test_plain_stepped_shaft_is_not_flagged(self):
        # A clean turned shaft: every ⌀ leader runs outward to the row, none cut
        # through the body.
        dwg = build_drawing(
            Rotation(0, 90, 0) * (Cylinder(15, 40) + Pos(0, 0, 35) * Cylinder(8, 30))
        )
        assert not any(i.code == "leader_crosses_silhouette" for i in dwg.lint())

    def test_hole_callout_exiting_the_part_is_not_flagged(self):
        # GRM-03: the side-view hole callout legitimately exits the disc from an
        # internal hole (covers_diameters), and the ⌀ leaders don't cut the body.
        fixture = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw.step"
        dwg = build_drawing(step_file=str(fixture))
        assert not any(i.code == "leader_crosses_silhouette" for i in dwg.lint())
