"""Chamfer, flat, groove, and countersink callouts."""

from pathlib import Path

import pytest
from _kernel import B123D_GE_011, SKIP_011
from build123d import Align, Axis, Box, Compound, Cylinder, Edge, Pos, Rot, Rotation
from build123d_drafting import HoleCallout

from draftwright import build_drawing

_skip_011 = pytest.mark.skipif(B123D_GE_011, reason=SKIP_011)


def _chamfer_text(dwg):
    return " ".join(
        str(getattr(o, "label", "") or getattr(o, "text", "") or "")
        for _, o in dwg.iter_annotations()
    )


class TestChamferCallout:
    """#560: a chamfered edge is called out (C{leg} / {leg}×{angle}°) from a recognised
    ChamferFeature, not left as an undimensioned bevel."""

    def _chamfered_plate(self, *legs):
        from build123d import chamfer

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
        callouts = {
            n: dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_chamfer")
        }
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
        callout = next(
            dwg.get_annotation(n).label for n in dwg.annotations() if n.startswith("m_chamfer")
        )
        assert "C" not in callout and "°" in callout

    def test_leader_anchors_on_the_bevel_interior_not_an_endpoint(self):
        # #621: the leader anchor must sit ON the chamfer bevel, near the middle of its run — not
        # at the supporting plane's parametric origin, which projects to an endpoint/corner.
        from build123d import GeomType, Vertex, chamfer
        from quiddity import recognise_chamfers

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
        from quiddity import recognise_chamfers

        from draftwright.analysis import _import_step

        fixture = Path(__file__).parent / "fixtures" / "nist_ctc_01_asme1_ap242.stp"
        part = _import_step(str(fixture))
        c50 = next(c for c in recognise_chamfers(part) if abs(c.leg1 - 50) < 1)
        assert c50.axis == "z"  # runs along Z, so X/Y are the in-plane placement coords
        assert abs(c50.at[0] - 375) < 2 and abs(c50.at[1] - 200) < 2  # the bevel midpoint
        assert (
            abs(c50.at[0] - 400) > 10 or abs(c50.at[1] - 175) > 10
        )  # not the plane-origin corner

    def test_x_edge_chamfer_reads_in_side_view(self):
        from build123d import chamfer

        part = Box(60, 40, 30)
        e = part.edges().filter_by(Axis.X).sort_by(lambda e: e.center().Y + e.center().Z)[-1]
        dwg = build_drawing(chamfer(e, 6), number="X")
        callouts = {n: dwg.view_of(n) for n in dwg.annotations() if n.startswith("m_chamfer")}
        assert callouts and set(callouts.values()) == {"side"}

    def test_plain_box_has_no_chamfer_callout(self):
        dwg = build_drawing(Box(40, 30, 12), number="X")
        assert not [n for n in dwg.annotations() if n.startswith("m_chamfer")]

    def test_plain_cylinder_without_an_edge_break_has_no_chamfer_feature(self):
        dwg = build_drawing(Cylinder(20, 10), number="X")
        assert not [f for f in dwg.model().features if f.kind == "chamfer"]

    def test_leg_is_measured_from_the_local_face_not_the_outermost(self):
        # #560 review: the leg must come from the chamfer face's own extent, not the
        # distance to the part's outermost wall. A 6 mm chamfer on the top box of a
        # stepped part must read C6, NOT C36 (base wall at x=45 vs top wall at x=25).
        from build123d import chamfer

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
        assert _chamfer_label("6", chamfers[0].leg1, chamfers[0]) == "C6"

    def test_hex_prism_side_faces_are_not_chamfers(self):
        # #560 review: a polygon prism's oblique sides are REAL faces, not chamfers — they
        # abut oblique neighbours, not two perpendicular axis-aligned faces. None fire.
        from build123d import RegularPolygon, extrude

        dwg = build_drawing(extrude(RegularPolygon(20, 6), 30), number="X")
        assert not [f for f in dwg.model().features if f.kind == "chamfer"]

    def test_structural_ramp_is_not_a_chamfer(self):
        # #560 review: a large sloped face spanning the part (a wedge/ramp) is structural,
        # not an edge break — the size gate excludes it.
        from build123d import chamfer

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
        from build123d import Face, Vector, Wire, extrude

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
        from build123d import chamfer

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
        assert _chamfer_label("2.5", chamfers[0].leg1, chamfers[0]) == "C2.5"


class TestFlatCallout:
    """#148b: machined flats on round stock — the recogniser recovers the across-flats size
    from the geometry (flat-to-flat for opposed faces, the D height for a lone flat)."""

    @staticmethod
    def _hex_on_stock(d=9.3, r=10):
        # Six flats 60° apart, cut shallow (d near r) so OD arcs survive between them.

        bar = Cylinder(r, 30)
        for k in range(6):
            bar = bar - Rot(0, 0, 60 * k) * Pos(d + 1, 0, 0) * Box(2, 40, 40)
        return bar

    def test_hex_reads_across_flats(self):
        from quiddity import recognise_flats

        flats = recognise_flats(self._hex_on_stock(9.3, 10))
        assert len(flats) == 6
        # Every opposed pair reads flat-to-flat = 2d = 18.6, one shared A/F value.
        assert {round(f.across, 1) for f in flats} == {18.6}

    def test_odd_polygon_falls_back_to_D_height(self):
        # Three flats 120° apart have no opposing face → each reads flat-to-opposite-OD (R+d).
        from quiddity import recognise_flats

        bar = Cylinder(10, 30)
        for k in range(3):
            bar = bar - Rot(0, 0, 120 * k) * Pos(10.3, 0, 0) * Box(2, 40, 40)
        flats = recognise_flats(bar)
        assert len(flats) == 3 and {round(f.across, 1) for f in flats} == {19.3}

    def test_flat_on_x_axis_stock(self):
        from quiddity import recognise_flats

        xbar = Rot(0, 90, 0) * Cylinder(8, 30) - Pos(0, 0, 8) * Box(40, 40, 6)
        flats = recognise_flats(xbar)
        assert len(flats) == 1 and flats[0].axis == "x"
        assert flats[0].across == pytest.approx(13, abs=0.05)  # R + d = 8 + 5

    def test_shallow_tangent_sliver_is_not_a_flat(self):
        # A cut that barely grazes the OD (depth R − d below the min) is not a machined flat.
        from quiddity import recognise_flats

        grazed = Cylinder(10, 30) - Pos(10, 0, 0) * Box(0.4, 40, 40)  # depth ≈ 0.2 mm
        assert recognise_flats(grazed) == []

    def test_flat_renders_in_the_axis_view(self):
        from draftwright.annotations.from_model import _flat_label

        dwg = build_drawing(Cylinder(10, 30) - Pos(10, 0, 0) * Box(10, 40, 40), number="X")
        names = [n for n in dwg.annotations() if n.startswith("m_flat")]
        assert len(names) == 1
        assert dwg.get_annotation(names[0]).label == _flat_label(15)
        assert dwg.view_of(names[0]) == "plan"  # a Z-axis bar reads down the axis (plan)

    def test_offcentre_recess_wall_is_not_a_flat(self):
        # A slot/recess offset to one side of the axis has a near wall whose outward normal
        # points *away* from the axis — the sign test alone would pass it. But that wall reaches
        # the OD on one end only (the other abuts the slot floor), so it is not a flat.
        from quiddity import recognise_flats

        recessed = Cylinder(30, 40) - Pos(20, 25, 0) * Box(10, 30, 50)
        assert recognise_flats(recessed) == []

    def test_lone_flats_on_two_parallel_shafts_are_not_paired(self):
        # Two distinct z-shafts, each with one flat facing opposite ways. They share the axis
        # *letter* but not the axis *line*, so neither is the other's opposite: each reads the
        # D height (R + d = 15), not a spurious flat-to-flat (2d = 10).
        from quiddity import recognise_flats

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
        from quiddity import recognise_grooves

        grooves = recognise_grooves(self._grooved(8, 4, 10))
        assert len(grooves) == 1
        assert grooves[0].width == pytest.approx(4, abs=0.05)
        assert grooves[0].diameter == pytest.approx(16, abs=0.05)

    def test_two_grooves_on_one_shaft(self):
        from quiddity import recognise_grooves

        shaft = Cylinder(10, 60)
        shaft -= Pos(0, 0, 15) * (Cylinder(10, 4) - Cylinder(8, 4))
        shaft -= Pos(0, 0, -15) * (Cylinder(10, 4) - Cylinder(7, 4))
        grooves = recognise_grooves(shaft)
        assert len(grooves) == 2
        assert {round(g.diameter, 1) for g in grooves} == {16.0, 14.0}

    def test_monotonic_step_is_not_a_groove(self):
        # A plain stepped shaft (OD changes once, not a local minimum) has no groove.
        from quiddity import recognise_grooves

        stepped = Cylinder(10, 20) + Pos(0, 0, 15) * Cylinder(6, 10)
        assert recognise_grooves(stepped) == []

    def test_plain_cylinder_has_no_groove(self):
        from quiddity import recognise_grooves

        assert recognise_grooves(Cylinder(10, 40)) == []

    def test_slot_on_round_stock_is_not_a_groove(self):
        # A milled slot's walls are rectangular / radial — not the annular walls of a groove.
        from quiddity import recognise_grooves

        assert recognise_grooves(Cylinder(10, 30) - Box(6, 40, 40)) == []

    def test_alternating_fine_steps_are_not_grooves(self):
        # An alternating fine-step head (⌀ dips to a local minimum but the band is as wide as
        # its neighbours) is a stepped profile, not a channel — a groove must be NARROWER than
        # both bounding walls (#148c review: else a staircase dip is misread as a groove).
        from quiddity import recognise_grooves

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
        from quiddity import recognise_grooves

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
        assert dwg.get_annotation(names[0]).label == _groove_label(4, 16)
        # A groove's width is axial → it reads in a profile view (axis in-plane), not down it.
        assert dwg.view_of(names[0]) == "front"
        # The shoulder recovery keeps both outer bands independently dimensioned.
        assert sorted(
            item.label for name, item in dwg.iter_annotations() if name.startswith("m_steplen")
        ) == ["18", "18"]
        completeness = dwg.lint_summary()["quality"]["completeness"]
        assert completeness["requirements"] == completeness["placed"] == 6
        assert not [issue for issue in dwg.lint() if issue.severity == "error"]

    def test_groove_floor_diameter_is_not_double_dimensioned(self):
        # The groove floor band's two walls read as shoulders, so recognise_turned_steps
        # also delimits it as a middle step. detect.py must exclude that band from the step
        # chain so the floor ø is dimensioned ONCE (the groove callout), never also as a
        # separate step ø — ISO 129 / ADR 1 (was 0008) one-band-one-owner (#148c review).
        dwg = build_drawing(self._grooved(8, 4, 10), number="X")
        floor_labels = [
            n
            for n in dwg.annotations()
            if "ø16" in str(getattr(dwg.get_annotation(n), "label", ""))
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
        from quiddity import recognise_grooves

        stack = Compound(
            [Cylinder(20, 4), Pos(0, 0, 4) * Cylinder(5, 4), Pos(0, 0, 8) * Cylinder(20, 4)]
        )
        assert recognise_grooves(stack) == []

    def test_narrow_circlip_groove_floor_dimensioned_once(self):
        # A typical DIN 471 circlip groove is NARROW (~1.3 mm). recognise_turned_steps reports
        # its step at the WALL ø (local_od's pad engulfs both walls), so the step-exclusion must
        # key on axial position, not floor ø — else the floor ø double-dimensions via a spurious
        # step / boss (#148c 2nd-pass review, the primary use case).
        from quiddity import recognise_grooves

        narrow = Cylinder(10, 40) - Pos(0, 0, 10) * (Cylinder(10, 1.3) - Cylinder(9, 1.3))
        assert len(recognise_grooves(narrow)) == 1
        dwg = build_drawing(narrow, number="X")
        floor = [
            n
            for n in dwg.annotations()
            if "ø18" in str(getattr(dwg.get_annotation(n), "label", ""))
        ]
        assert floor == [n for n in floor if n.startswith("m_groove")]
        assert len(floor) == 1

    def test_end_adjacent_groove_with_narrow_land_is_recognised(self):
        # A groove near the shaft end leaves a thin retaining LAND on the end side. That wall is
        # narrow because of end-proximity, not a staircase — the recogniser tests the WIDER wall,
        # so the real groove is still recognised (#148c 2nd-pass review).
        from quiddity import recognise_grooves

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
        floor = [
            n
            for n in dwg.annotations()
            if "ø16" in str(getattr(dwg.get_annotation(n), "label", ""))
        ]
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
        from quiddity import recognise_countersinks

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
        assert not any(getattr(i, "code", None) == "callout_dropped" for i in dwg.registry.issues)
        leaders = [dwg.get_annotation(n) for n in dwg.annotations() if n.startswith("hc_")]
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
        from quiddity import recognise_countersinks

        assert recognise_countersinks(plate) == []
        holes = [f for f in build_drawing(plate, number="X").model().features if f.kind == "hole"]
        assert holes and holes[0].csink is None and holes[0].cbore is not None

    def test_deburr_mouth_chamfer_is_not_a_countersink(self):
        # #558 review (BLOCKER): a 0.5 mm edge-break / deburr at a hole mouth is the same
        # cone shape as a shallow csk — the flare-ratio floor must exclude it, else every
        # chamfered hole mouth gets a spurious csk callout.
        from build123d import chamfer
        from quiddity import recognise_countersinks, recognise_holes

        plate = Box(30, 30, 10) - Pos(0, 0, 0) * Cylinder(3, 20)
        edge = plate.edges().filter_by(Axis.Z).group_by(lambda e: e.center().Z)[-1]
        plate = chamfer(edge, 0.5)
        assert recognise_countersinks(plate) == []
        assert recognise_holes(plate, csinks=recognise_countersinks(plate))[0].csink is None

    def test_opposite_face_coaxial_hole_is_not_mis_associated(self):
        # #558 review (BLOCKER): a countersink must attach only to the bore at its mouth,
        # facing the same way — NOT to a coaxial hole drilled from the opposite face.
        from build123d import Cone
        from quiddity import recognise_countersinks, recognise_holes

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
        from quiddity import recognise_countersinks, recognise_holes

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
