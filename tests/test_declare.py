"""Tests for the declarative drawing surface (ADR 0011): the ``model=`` seam, the
object→feature constructors (:mod:`draftwright.model.declare`), and the fluent
:class:`draftwright.Sheet` façade.

The constructors are pure geometry reads (fast); the seam / Sheet tests do a real
OCC build (fast tier — not marked slow).
"""

import pytest
from build123d import Axis, Box, Cylinder, GeomType, Pos, Rot

from draftwright import Sheet, build_drawing
from draftwright.model import (
    BossFeature,
    ChamferFeature,
    EnvelopeFeature,
    FilletFeature,
    FlatFeature,
    GrooveFeature,
    HoleFeature,
    PartModel,
    PatternFeature,
    PlateFeature,
    PocketFeature,
    SlotFeature,
    StepFeature,
    StepLevelFeature,
    boss,
    chamfer,
    envelope,
    fillet,
    flat,
    groove,
    hole,
    pattern,
    plate,
    pocket,
    slot,
    step,
    step_level,
)
from draftwright.sheet import _parse_scale


def _boom(*a, **k):
    raise AssertionError("build_drawing must not be called for a no-render model path (#453)")


class TestConstructors:
    def test_hole_reads_diameter_axis_location_off_object(self):
        h = Pos(20, 10, 4) * Cylinder(3, 8)  # r3 -> ø6, axis z, centre (20,10,4)
        f = hole(h)
        assert isinstance(f, HoleFeature)
        assert f.frame.axis == "z"
        assert f.diameter == pytest.approx(6.0)
        assert f.frame.origin == pytest.approx((20, 10, 4))
        assert f.through is True and f.depth is None

    def test_hole_explicit_values(self):
        f = hole(diameter=6, at=(1, 2, 3), axis="x", through=False, depth=5)
        assert f.diameter == 6 and f.frame.axis == "x"
        assert f.through is False and f.depth == 5
        assert f.frame.origin == (1, 2, 3)

    def test_hole_explicit_cbore(self):
        f = hole(diameter=6, at=(0, 0, 0), axis="z", cbore=(10, 3), count=4)
        assert f.cbore == (10, 3) and f.count == 4

    def test_declared_hole_count_is_faithful_in_features(self):
        # #584 WP1 B2 review: features()/the hole table QTY read the feature's own
        # count, not len(members) — a declared count-N hole with unspecified members
        # (only pattern() auto-populates them) must still report N, not 1.
        from build123d import Box, Cylinder, Pos

        from draftwright import build_drawing

        part = Box(80, 60, 12) - Pos(20, 10, 0) * Cylinder(3, 12)
        dwg = build_drawing(
            part,
            model=[hole(diameter=6, at=(20, 10, 0), axis="z", through=True, count=3)],
            number="X",
        )
        (feat,) = dwg.features("plan")
        assert feat.diameter == 6 and feat.count == 3

    def test_read_cylinder_diameter_is_face_radius(self):
        c = Pos(0, 0, 0) * Cylinder(3, 8)
        r = max(fc.radius for fc in c.faces().filter_by(GeomType.CYLINDER))
        assert boss(c).diameter == pytest.approx(2 * r)

    def test_read_cylinder_falls_back_to_bbox_without_a_cylindrical_face(self):
        # A box has no cylindrical face — the reader falls back to the bbox heuristic
        # rather than raising.
        b = Box(4, 4, 10)  # equal pair (X,Y)=4 -> ø4, odd axis z
        f = boss(b)
        assert f.frame.axis == "z" and f.diameter == pytest.approx(4.0)

    def test_boss_explicit(self):
        f = boss(diameter=6, at=(0, 0, 0), axis="x")
        assert isinstance(f, BossFeature) and f.diameter == 6 and f.frame.axis == "x"

    def test_step_reads_diameter_length_off_object(self):
        seg = Rot(0, 90, 0) * Cylinder(2, 10)  # r2 -> ø4, length 10, axis x
        f = step(seg)
        assert isinstance(f, StepFeature)
        assert f.frame.axis == "x"
        assert f.diameter == pytest.approx(4.0)
        assert f.length == pytest.approx(10.0)
        # span is the two axial end-points, ±length/2 along the axis
        (lo, hi) = f.span
        assert hi[0] - lo[0] == pytest.approx(10.0)

    def test_step_explicit_derives_span(self):
        f = step(diameter=4, length=10, at=(0, 0, 0), axis="x")
        assert f.span == ((-5.0, 0.0, 0.0), (5.0, 0.0, 0.0))

    def test_slot_explicit(self):
        f = slot(width=6, length=20, long_axis="x", width_axis="y", lo=-10, hi=10, w_center=0)
        assert isinstance(f, SlotFeature)
        assert f.width == 6 and f.length == 20
        assert f.long_axis == "x" and f.width_axis == "y"

    def test_slot_reads_axes_off_object_by_span(self):
        # A milled slot tool: longest span = length/long axis, middle = width/width axis.
        tool = Box(20, 6, 4)  # X longest -> long_axis x, Y middle -> width_axis y
        f = slot(tool)
        assert f.long_axis == "x" and f.width_axis == "y"
        assert f.length == pytest.approx(20.0) and f.width == pytest.approx(6.0)

    def test_slot_through_z_cutter_reads_with_depth_axis(self):
        # #490 regression: a through-Z slot cut by a tall cutter has Z as the LONGEST span,
        # so the shortest-span default would mistake Z for the long axis. Naming depth_axis="z"
        # excludes it, so long/width are read from the two in-plane axes.
        tool = Box(6, 20, 40)  # Z longest (through), Y=20 (long), X=6 (width)
        f = slot(tool, depth_axis="z")
        assert f.long_axis == "y" and f.width_axis == "x"
        assert f.length == pytest.approx(20.0) and f.width == pytest.approx(6.0)

    def test_slot_long_axis_override_re_reads_length(self):
        # #490: an explicit long_axis override must re-read length/lo/hi FROM that axis, not
        # leave them at the sort-position span (the pre-#490 latent bug). Here X (span 6) is
        # named long, so length must be 6 — the X span — not the longest (Z=40) span.
        f = slot(Box(6, 20, 40), long_axis="x", width_axis="y")
        assert f.long_axis == "x" and f.length == pytest.approx(6.0)

    def test_slot_uppercase_axis_override_normalises(self):
        # #490 review (major regression): an explicit override is used as a lowercase-dict key,
        # so a build123d-style uppercase letter ("Y"/"Z") must be normalised, not KeyError.
        f = slot(Box(6, 20, 40), long_axis="Y", depth_axis="Z")
        assert f.long_axis == "y" and f.width_axis == "x"
        assert f.length == pytest.approx(20.0) and f.width == pytest.approx(6.0)

    def test_slot_ambiguous_top_spans_warn(self):
        # Two near-equal top spans (X≈Y) make the long/width read a coin-flip — warn when the
        # caller named none of the axes.
        with pytest.warns(UserWarning, match="ambiguous"):
            slot(Box(20, 20, 4))

    def test_slot_ambiguous_width_depth_spans_warn(self):
        # #490 review: with the shortest-span depth default, a near-equal WIDTH-vs-DEPTH pair
        # (the two shortest spans) is equally a coin-flip — which is kept as width vs dropped as
        # depth is decided by sort order alone. It must warn too, not only the top-span tie.
        with pytest.warns(UserWarning, match="ambiguous"):
            slot(Box(40, 6, 6))

    def test_slot_depth_named_still_warns_on_inplane_tie(self):
        # #490 re-review: naming ONLY the through axis resolves the depth split but not the
        # long-vs-width one. With the two in-plane spans near-equal (X≈Y) the assignment is
        # still a silent coin-flip, so it must warn — the same as the un-named ambiguous case.
        with pytest.warns(UserWarning, match="ambiguous"):
            slot(Box(20, 20, 40), depth_axis="z")

    def test_slot_middle_pinned_still_warns_on_outer_tie(self):
        # #490 r4: pinning the MIDDLE-span axis (long_axis="y" on X>Y>Z) leaves the two OUTER
        # auto axes (X, Z) — which are non-adjacent in the span order — to split width vs depth.
        # If those two are near-equal the split is a silent coin-flip, so it must still warn.
        with pytest.warns(UserWarning, match="ambiguous"):
            slot(Box(40, 39, 38), long_axis="y")

    def test_slot_middle_depth_still_warns_on_inplane_tie(self):
        # The depth mirror: depth_axis="y" pins the middle span; the two outer auto axes (X, Z)
        # then split long vs width, and a near-equal outer pair is a coin-flip that must warn.
        with pytest.warns(UserWarning, match="ambiguous"):
            slot(Box(50, 49, 48), depth_axis="y")

    def test_slot_named_axes_suppress_ambiguity_warning(self):
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            slot(Box(20, 20, 4), long_axis="x", width_axis="y")
        assert not [w for w in caught if "ambiguous" in str(w.message)]

    def test_slot_depth_axis_must_differ_raises(self):
        with pytest.raises(ValueError, match="depth_axis must differ"):
            slot(Box(20, 6, 4), depth_axis="x", long_axis="x", width_axis="y")

    def test_slot_depth_plus_long_override_resolves_width(self):
        # #490 re-review: depth_axis= + a long_axis= that names the SHORTER in-plane axis must
        # leave the other in-plane axis free for width — not collide into "must differ".
        f = slot(Box(6, 20, 40), depth_axis="z", long_axis="x")  # z=depth, x named long
        assert f.long_axis == "x" and f.width_axis == "y"
        assert f.length == pytest.approx(6.0) and f.width == pytest.approx(20.0)

    def test_slot_depth_plus_width_override_resolves_long(self):
        # The mirror: depth_axis= + a width_axis= must leave the other in-plane axis free for long.
        f = slot(Box(6, 20, 40), depth_axis="z", width_axis="x")  # z=depth, x named width
        assert f.width_axis == "x" and f.long_axis == "y"
        assert f.width == pytest.approx(6.0) and f.length == pytest.approx(20.0)

    def test_pocket_explicit(self):
        f = pocket(
            width=20, length=30, depth=8, long_axis="x", width_axis="y", lo=-15, hi=15, w_center=0
        )
        assert isinstance(f, PocketFeature)
        assert f.width == 20 and f.length == 30 and f.depth == 8
        assert f.long_axis == "x" and f.width_axis == "y" and f.depth_axis == "z"

    def test_pocket_reads_axes_and_depth_off_object(self):
        # Shallow recess cavity: longest span = length, middle = width, shortest = depth.
        f = pocket(Box(30, 20, 8))  # X long, Y width, Z shortest -> depth
        assert f.long_axis == "x" and f.width_axis == "y" and f.depth_axis == "z"
        assert f.length == pytest.approx(30.0) and f.width == pytest.approx(20.0)
        assert f.depth == pytest.approx(8.0)

    def test_pocket_deep_recess_reads_with_depth_axis(self):
        # A recess deeper than it is wide: naming depth_axis excludes the deep span from the
        # long/width read (the shortest-span default would otherwise mistake width for depth).
        f = pocket(Box(30, 6, 20), depth_axis="z")  # Z=20 depth, X=30 long, Y=6 width
        assert f.long_axis == "x" and f.width_axis == "y"
        assert f.length == pytest.approx(30.0) and f.width == pytest.approx(6.0)
        assert f.depth == pytest.approx(20.0)

    def test_pocket_explicit_kwarg_overrides_object_read(self):
        f = pocket(Box(30, 20, 8), depth=5)
        assert f.depth == pytest.approx(5.0)  # explicit depth wins over the object's 8 span

    def test_pocket_ambiguous_spans_warn(self):
        with pytest.warns(UserWarning, match="ambiguous"):
            pocket(Box(20, 20, 8))

    def test_pocket_needs_depth(self):
        with pytest.raises(ValueError, match="needs an object"):
            pocket(width=20, length=30, long_axis="x", width_axis="y", lo=-15, hi=15)

    def test_pocket_negative_depth_raises(self):
        with pytest.raises(ValueError, match="depth"):
            pocket(width=20, length=30, depth=-8, long_axis="x", width_axis="y", lo=-15, hi=15)

    def test_pocket_length_must_match_span(self):
        with pytest.raises(ValueError, match="must equal hi - lo"):
            pocket(width=20, length=99, depth=8, long_axis="x", width_axis="y", lo=-15, hi=15)

    def test_envelope_reads_bbox(self):
        f = envelope(Box(80, 50, 8))
        assert isinstance(f, EnvelopeFeature)
        assert f.width == pytest.approx(80) and f.depth == pytest.approx(50)
        assert f.height == pytest.approx(8)

    def test_pattern_composes_member(self):
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        f = pattern(member, kind="bolt_circle", count=6, bcd=40)
        assert isinstance(f, PatternFeature)
        assert f.pattern == "bolt_circle" and f.count == 6 and f.bcd == 40
        assert f.member is member

    def test_pattern_populates_member_locations(self):
        # A declared pattern must be shaped like a detected one: members populated with
        # the arrangement's hole centres (the balloon / BCD furniture anchors on them).
        member = hole(diameter=3, at=(0, 0, 5), axis="z")
        f = pattern(member, kind="bolt_circle", count=6, bcd=40, at=(0, 0, 5))
        assert len(f.members) == 6
        # every member sits on the ⌀40 bolt circle (r=20) about the centre, in the z-plane
        for mx, my, mz in f.members:
            assert (mx**2 + my**2) == pytest.approx(20.0**2)
            assert mz == pytest.approx(5.0)
        # the frame origin is the pattern CENTRE (detector convention), not a member
        assert f.frame.origin == pytest.approx((0, 0, 5))

    def test_pattern_explicit_members_preserved(self):
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        locs = ((10, 0, 0), (-10, 0, 0))
        f = pattern(member, kind="other", count=2, members=locs)
        assert f.members == locs

    def test_linear_pattern_members_spaced_by_pitch(self):
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        f = pattern(member, kind="linear", count=3, pitch=15, direction=(1, 0, 0), at=(0, 0, 0))
        xs = sorted(m[0] for m in f.members)
        assert xs == pytest.approx([-15, 0, 15])

    def test_constructors_raise_valueerror_not_assert(self):
        # Under python -O bare asserts vanish; required-arg validation must be a real error.
        with pytest.raises(ValueError):
            hole()
        with pytest.raises(ValueError):
            boss(diameter=5)  # missing at / axis
        with pytest.raises(ValueError):
            slot(width=6, length=20)  # missing axes / lo / hi


class TestChamfer:
    """#576: declare a chamfer (bevelled edge) — the third ADR-0011 surface for #560."""

    def test_explicit_equal_leg(self):
        f = chamfer(axis="z", leg=6, at=(25, 20, 10))
        assert isinstance(f, ChamferFeature)
        assert f.axis == "z" and f.frame.origin == pytest.approx((25, 20, 10))
        assert f.leg1 == 6 and f.leg2 == 6 and f.angle == pytest.approx(45.0)

    def test_reads_off_the_bevel_face_and_matches_detection(self):
        # #576 review (BLOCKER): the object flavour reads the OBLIQUE bevel face, so `at`
        # lands ON the bevel — not on the removed sharp corner — and round-trips with the
        # detected feature.
        from build123d import GeomType
        from build123d import chamfer as bd_chamfer

        box = Box(90, 60, 10)
        e = box.edges().filter_by(Axis.Z).sort_by(lambda x: x.center().X + x.center().Y)[-1]
        solid = bd_chamfer(e, 6)
        bevel = next(
            f
            for f in solid.faces().filter_by(GeomType.PLANE)
            if max(abs(f.normal_at().X), abs(f.normal_at().Y), abs(f.normal_at().Z)) < 0.99
        )
        f = chamfer(bevel)
        assert f.axis == "z" and abs(f.leg1 - 6) < 0.1 and abs(f.leg2 - 6) < 0.1
        # `at` is the bevel centre (~(42, 27)), NOT the removed corner (45, 30)
        assert abs(f.frame.origin[0] - 45) > 1 and abs(f.frame.origin[1] - 30) > 1
        det = next(
            x for x in build_drawing(solid, number="X").model().features if x.kind == "chamfer"
        )
        # The in-plane (plan-view) leader position matches detection; the along-axis (Z)
        # coord is view depth for a Z-edge chamfer, so it does not affect placement.
        assert f.frame.origin[0] == pytest.approx(det.frame.origin[0], abs=1.0)
        assert f.frame.origin[1] == pytest.approx(det.frame.origin[1], abs=1.0)

    def test_asymmetric_computes_angle(self):
        f = chamfer(axis="x", leg1=14, leg2=8, at=(0, 0, 0))
        assert f.leg1 == 14 and f.leg2 == 8  # leg1 the larger
        assert f.angle == pytest.approx(29.74, abs=0.1)  # atan2(8, 14)

    def test_declared_chamfer_renders_its_callout(self):
        # A declared chamfer draws its C6 leader even on a part with no *detected* chamfer.
        from draftwright.annotations.from_model import _chamfer_label

        part = Box(90, 60, 10) + Pos(0, 0, 10) * Box(50, 40, 10)
        dwg = build_drawing(part, model=[chamfer(axis="z", leg=6, at=(25, 20, 10))], number="X")
        chs = [f for f in dwg.model().features if f.kind == "chamfer"]
        assert len(chs) == 1 and _chamfer_label(chs[0]) == "C6"

    def test_needs_axis_at_and_leg(self):
        with pytest.raises(ValueError):
            chamfer(leg=6)  # no axis / at

    def test_rejects_out_of_range_angle(self):
        # #576 review (MAJOR): an impossible angle must be rejected, not become valid IR.
        with pytest.raises(ValueError, match="0, 90"):
            chamfer(axis="z", leg=6, at=(0, 0, 0), angle=120)

    def test_rejects_contradictory_angle(self):
        # legs 14/8 imply atan2(8,14) ≈ 29.74°, not 60° — reject the contradiction.
        with pytest.raises(ValueError, match="contradict"):
            chamfer(axis="z", leg1=14, leg2=8, at=(0, 0, 0), angle=60)

    def test_non_oblique_face_is_rejected(self):
        box = Box(20, 20, 20)
        flat = box.faces().sort_by(lambda f: f.center().Z)[-1]  # an axis-aligned face
        with pytest.raises(ValueError, match="chamfer"):
            chamfer(flat)

    def test_emitted_values_re_declare_without_contradiction(self):
        # sheet_emit writes angle=atan2(lo,hi) alongside the legs; re-declaring with that same
        # (leg1, leg2, angle) must be accepted as consistent — the emit round-trip must not trip
        # the angle-contradiction guard.
        f = chamfer(axis="x", leg1=14, leg2=8, at=(0, 0, 0))
        f2 = chamfer(axis="x", leg1=14, leg2=8, at=(0, 0, 0), angle=f.angle)
        assert (f2.leg1, f2.leg2, f2.angle) == (f.leg1, f.leg2, f.angle)

    def test_object_leg_override_re_derives_angle(self):
        # #580 review (BLOCKER): overriding a leg on an object-declared chamfer must re-derive
        # the angle from the new legs (#451 independent override) — not keep the object's stale
        # angle and falsely raise "contradicts legs".
        import math

        from build123d import GeomType
        from build123d import chamfer as bd_chamfer

        box = Box(90, 60, 10)
        e = box.edges().filter_by(Axis.Z).sort_by(lambda x: x.center().X + x.center().Y)[-1]
        bevel = next(
            f
            for f in bd_chamfer(e, 6).faces().filter_by(GeomType.PLANE)
            if max(abs(f.normal_at().X), abs(f.normal_at().Y), abs(f.normal_at().Z)) < 0.99
        )
        f = chamfer(bevel, leg2=3)  # override one leg → asymmetric; must NOT raise
        assert abs(f.leg1 - 6) < 0.1 and f.leg2 == 3
        assert f.angle == pytest.approx(math.degrees(math.atan2(3, 6)), abs=1.0)


class TestFillet:
    """#561: fillet (rounded edge) across all three ADR-0011 surfaces — recognise + emit
    + declare. The arc analog of the chamfer."""

    @staticmethod
    def _filleted(radius=3):
        from build123d import fillet as bd_fillet

        return bd_fillet(Box(60, 40, 20).edges().filter_by(Axis.Z), radius)

    def test_explicit(self):
        f = fillet(axis="z", radius=3, at=(28.5, 18.5, 0))
        assert isinstance(f, FilletFeature)
        assert f.axis == "z" and f.radius == 3 and f.frame.origin == pytest.approx((28.5, 18.5, 0))

    def test_reads_off_the_round_face_and_matches_detection(self):
        # The object flavour reads the CYLINDRICAL blend face — radius off the cylinder, `at`
        # on the round — and round-trips (in-plane) with the detected feature.
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder

        solid = self._filleted(3)
        face = next(
            g
            for g in solid.faces()
            if BRepAdaptor_Surface(g.wrapped).GetType() == GeomAbs_Cylinder
        )
        f = fillet(face)
        assert f.axis == "z" and abs(f.radius - 3) < 0.01
        det = next(
            x for x in build_drawing(solid, number="X").model().features if x.kind == "fillet"
        )
        # Exact in-plane parity: declare reads the same on-round anchor the recogniser does — a
        # point at mid angular/axial of the trimmed face (#622), not the off-surface bbox centre
        # (the along-edge Z coord is view depth, so it need not match).
        assert f.frame.origin[0] == pytest.approx(det.frame.origin[0], abs=0.01)
        assert f.frame.origin[1] == pytest.approx(det.frame.origin[1], abs=0.01)
        from build123d import Vertex

        assert Vertex(*f.frame.origin).distance_to(solid) < 0.05  # declared anchor on the round

    def test_anchor_lies_on_the_radius_surface_not_the_virtual_corner(self):
        # #622: the R leader must terminate on the curved radius surface, not the face bbox
        # centre — which sits in the removed-wedge void near the arc's centre of curvature /
        # virtual sharp corner, OFF the solid (~0.71R away). Cover X/Y/Z edge axes + off-origin,
        # and the grouped case (every equal-R member anchored on its own round).
        from build123d import Axis, Pos, Vertex
        from build123d import fillet as bd_fillet

        from draftwright.recognition import recognise_fillets

        z = bd_fillet(Box(60, 40, 30).edges().filter_by(Axis.Z).sort_by(Axis.X)[-1], 5)
        cases = {
            "z": z,
            "x": bd_fillet(Box(60, 40, 30).edges().filter_by(Axis.X).sort_by(Axis.Z)[-1], 5),
            "y": bd_fillet(Box(60, 40, 30).edges().filter_by(Axis.Y).sort_by(Axis.Z)[-1], 5),
            "off-origin": Pos(100, 50, 20) * z,
        }
        for label, part in cases.items():
            (fl,) = recognise_fillets(part)
            assert Vertex(*fl.at).distance_to(part) < 0.05, (
                f"{label}: anchor off the round surface"
            )
        # grouped: all four vertical edges rounded → four members, each on its own round.
        grouped = bd_fillet(Box(60, 40, 30).edges().filter_by(Axis.Z), 5)
        members = recognise_fillets(grouped)
        assert len(members) == 4
        assert all(Vertex(*m.at).distance_to(grouped) < 0.05 for m in members)

    def test_recognises_external_fillet(self):
        dwg = build_drawing(self._filleted(3), number="X")
        fs = [f for f in dwg.model().features if f.kind == "fillet"]
        assert fs and all(abs(f.radius - 3) < 0.01 for f in fs)

    def test_internal_round_is_not_a_fillet(self):
        # The convex test excludes an internal (concave re-entrant) round. An L-bracket's
        # inner corner fillet must NOT be called out; only the outer convex ones.
        from build123d import fillet as bd_fillet

        from draftwright.recognition import recognise_fillets

        L = Box(60, 20, 20) + Pos(-20, 20, 0) * Box(20, 20, 20)
        filleted = bd_fillet(L.edges().filter_by(Axis.Z), 3)
        fs = recognise_fillets(filleted)
        assert len(fs) == 5  # 6 vertical edges, the 1 concave inner corner excluded

    def test_tiny_deburr_is_not_a_fillet(self):
        # A sub-min_radius edge-break is not a dimensioned feature.
        from draftwright.recognition import recognise_fillets

        assert recognise_fillets(self._filleted(0.4)) == []

    def test_hole_and_boss_safeguards_intact(self):
        # A filleted part's holes/bosses are still recognised (fillet recognition must not
        # break the existing blend-face exclusions in hole/boss recognition, #561 acceptance).
        from build123d import Cylinder

        from draftwright.recognition import recognise_bosses, recognise_holes

        holed = Box(40, 40, 12) - Pos(0, 0, 0) * Cylinder(3, 12)
        assert len(recognise_holes(holed)) == 1
        assert len(recognise_bosses(Cylinder(10, 20))) >= 1

    def test_declared_fillet_renders_R_callout(self):
        from draftwright.annotations.from_model import _fillet_label

        dwg = build_drawing(
            Box(60, 40, 20), model=[fillet(axis="z", radius=3, at=(28.5, 18.5, 0))], number="X"
        )
        fs = [f for f in dwg.model().features if f.kind == "fillet"]
        assert len(fs) == 1 and _fillet_label(fs[0].radius, 1) == "R3"
        assert not any(i.severity == "error" for i in dwg.lint())

    def test_equal_radii_group_as_n_times_R(self):
        # #561 acceptance #2: repeated equal-radius fillets share one n× R callout.
        dwg = build_drawing(self._filleted(3), number="X")
        names = [n for n in dwg.annotations() if n.startswith("m_fillet")]
        assert len(names) == 1  # ONE grouped callout, not four
        assert dwg._named[names[0]].label == "4× R3"

    def test_needs_axis_radius_at(self):
        with pytest.raises(ValueError):
            fillet(radius=3)  # no axis / at

    def test_rejects_non_positive_radius(self):
        with pytest.raises(ValueError):
            fillet(axis="z", radius=-3, at=(0, 0, 0))

    def test_non_cylindrical_face_rejected(self):
        box = Box(20, 20, 20)
        face = box.faces().sort_by(lambda f: f.center().Z)[-1]  # an axis-aligned planar face
        with pytest.raises(ValueError, match="fillet"):
            fillet(face)


class TestFlat:
    """#148b: machined flats on round stock — recognise + emit + declare, called out by the
    across-flats size (flat-to-flat for a double-D / hex, the D height for a lone flat)."""

    @staticmethod
    def _dshaft(d=5, r=10):
        # Round bar (R=r, axis Z) with one flat at x=d.
        return Cylinder(r, 30) - Pos(r, 0, 0) * Box(2 * (r - d), 40, 40)

    @staticmethod
    def _double_d(d=5, r=10):
        bar = Cylinder(r, 30)
        return (
            bar
            - Pos(r, 0, 0) * Box(2 * (r - d), 40, 40)
            - Pos(-r, 0, 0) * Box(2 * (r - d), 40, 40)
        )

    def test_explicit(self):
        f = flat(axis="z", across=15, at=(5, 0, 0))
        assert isinstance(f, FlatFeature)
        assert f.axis == "z" and f.across == 15 and f.frame.origin == pytest.approx((5, 0, 0))

    def test_reads_at_off_the_planar_face(self):
        # The object flavour reads the leader point off the planar flat face; axis/across
        # stay explicit (a plane carries neither the stock radius nor which axis is the run).
        solid = self._dshaft(5, 10)
        face = next(
            g
            for g in solid.faces()
            if g.geom_type == GeomType.PLANE and abs(g.center().X - 5) < 0.01
        )
        f = flat(face, axis="z", across=15)
        assert f.axis == "z" and f.across == 15
        assert f.frame.origin[0] == pytest.approx(5, abs=0.01)
        assert f.frame.origin[1] == pytest.approx(0, abs=0.01)

    def test_recognises_single_flat_as_D_height(self):
        # A lone flat reads flat-to-opposite-OD: R + d = 10 + 5 = 15.
        fs = [
            f
            for f in build_drawing(self._dshaft(5, 10), number="X").model().features
            if f.kind == "flat"
        ]
        assert len(fs) == 1 and fs[0].across == pytest.approx(15, abs=0.05)

    def test_recognises_double_D_as_flat_to_flat(self):
        # Two opposing flats read flat-to-flat: 2d = 10 (grouped to ONE A/F callout at render).
        from draftwright.annotations.from_model import _flat_label

        dwg = build_drawing(self._double_d(5, 10), number="X")
        fs = [f for f in dwg.model().features if f.kind == "flat"]
        assert len(fs) == 2 and all(f.across == pytest.approx(10, abs=0.05) for f in fs)
        names = [n for n in dwg.annotations() if n.startswith("m_flat")]
        assert len(names) == 1  # one across-flats callout, not two
        assert dwg._named[names[0]].label == _flat_label(10)

    def test_slot_wall_is_not_a_flat(self):
        # A slot's two facing walls point *toward* the axis — not flats (the #148b distinction).
        from draftwright.recognition import recognise_flats

        slotted = Cylinder(10, 30) - Box(6, 40, 40)
        assert recognise_flats(slotted) == []

    def test_plain_cylinder_has_no_flat(self):
        from draftwright.recognition import recognise_flats

        assert recognise_flats(Cylinder(10, 30)) == []

    def test_declared_flat_renders_A_F_callout(self):
        from draftwright.annotations.from_model import _flat_label

        dwg = build_drawing(
            self._dshaft(5, 10), model=[flat(axis="z", across=15, at=(5, 0, 0))], number="X"
        )
        fs = [f for f in dwg.model().features if f.kind == "flat"]
        assert len(fs) == 1 and _flat_label(fs[0].across) == "15 A/F"
        assert not any(i.severity == "error" for i in dwg.lint())

    def test_needs_axis_across_at(self):
        with pytest.raises(ValueError):
            flat(axis="z", across=15)  # no at
        with pytest.raises(ValueError):
            flat(across=15, at=(5, 0, 0))  # no axis

    def test_rejects_non_positive_across(self):
        with pytest.raises(ValueError):
            flat(axis="z", across=-5, at=(0, 0, 0))

    def test_non_planar_face_rejected(self):
        # The object flavour needs the planar flat face, not the round OD.
        cyl_face = next(g for g in self._dshaft().faces() if g.geom_type == GeomType.CYLINDER)
        with pytest.raises(ValueError, match="flat"):
            flat(cyl_face, axis="z", across=15)


class TestGroove:
    """#148c: turned / circlip grooves on round stock — recognise + emit + declare, called out
    by the groove width + floor diameter (an annular channel: a strict local-minimum OD band,
    distinct from a slot and from a monotonic step)."""

    @staticmethod
    def _grooved(floor_r=8, width=4, r=10, length=40):
        return Cylinder(r, length) - (Cylinder(r, width) - Cylinder(floor_r, width))

    @staticmethod
    def _floor_face(solid):
        # The reduced-OD floor face = the smallest-radius cylindrical face.
        cyls = [g for g in solid.faces() if g.geom_type == GeomType.CYLINDER]
        return min(cyls, key=lambda f: f.bounding_box().size.X)

    def test_explicit(self):
        g = groove(axis="z", width=4, diameter=16, at=(0, 0, 0))
        assert isinstance(g, GrooveFeature)
        assert g.axis == "z" and g.width == 4 and g.diameter == 16
        assert g.frame.origin == pytest.approx((0, 0, 0))

    def test_reads_all_off_the_floor_face(self):
        # The object flavour reads axis, width, diameter and the leader point off the floor face.
        g = groove(self._floor_face(self._grooved(8, 4, 10)))
        assert g.axis == "z"
        assert g.width == pytest.approx(4, abs=0.05)
        assert g.diameter == pytest.approx(16, abs=0.05)
        assert g.frame.origin[2] == pytest.approx(0, abs=0.05)

    def test_recognises_single_groove(self):
        fs = [
            f
            for f in build_drawing(self._grooved(8, 4, 10), number="X").model().features
            if f.kind == "groove"
        ]
        assert len(fs) == 1
        assert fs[0].width == pytest.approx(4, abs=0.05)
        assert fs[0].diameter == pytest.approx(16, abs=0.05)

    def test_monotonic_step_is_not_a_groove(self):
        from draftwright.recognition import recognise_grooves

        assert recognise_grooves(Cylinder(10, 20) + Pos(0, 0, 15) * Cylinder(6, 10)) == []

    def test_plain_cylinder_has_no_groove(self):
        from draftwright.recognition import recognise_grooves

        assert recognise_grooves(Cylinder(10, 40)) == []

    def test_declared_groove_renders_callout(self):
        from draftwright.annotations.from_model import _groove_label

        dwg = build_drawing(
            self._grooved(8, 4, 10),
            model=[groove(axis="z", width=4, diameter=16, at=(0, 0, 0))],
            number="X",
        )
        fs = [f for f in dwg.model().features if f.kind == "groove"]
        assert len(fs) == 1 and _groove_label(fs[0].width, fs[0].diameter) == "4 WIDE × ø16"
        assert not any(i.severity == "error" for i in dwg.lint())

    def test_needs_axis_width_diameter_at(self):
        with pytest.raises(ValueError):
            groove(axis="z", width=4, diameter=16)  # no at
        with pytest.raises(ValueError):
            groove(width=4, diameter=16, at=(0, 0, 0))  # no axis
        with pytest.raises(ValueError):
            groove(axis="z", diameter=16, at=(0, 0, 0))  # no width

    def test_rejects_non_positive(self):
        with pytest.raises(ValueError):
            groove(axis="z", width=-4, diameter=16, at=(0, 0, 0))
        with pytest.raises(ValueError):
            groove(axis="z", width=4, diameter=0, at=(0, 0, 0))

    def test_non_cylindrical_face_rejected(self):
        # The object flavour needs the floor cylindrical face, not a flat end face.
        end = self._grooved().faces().sort_by(Axis.Z)[-1]
        with pytest.raises(ValueError, match="groove"):
            groove(end, width=4, diameter=16)


class TestPocket:
    """#148a: blind rectangular recesses (floored slots/pockets) — recognise + render +
    declare, dimensioned W × L × D DEEP."""

    def test_recognises_pocket_disjoint_from_slots(self):
        from draftwright.recognition import Pocket, recognise_pockets, recognise_slots

        part = Box(80, 60, 20) - Pos(0, 0, 6) * Box(30, 20, 8)  # blind recess, depth 8
        pockets = recognise_pockets(part)
        assert len(pockets) == 1 and isinstance(pockets[0], Pocket)
        assert pockets[0].width == 20 and pockets[0].length == 30 and pockets[0].depth == 8
        assert recognise_slots(part) == []  # the through-slot recogniser stays silent

    def test_through_slot_not_read_as_pocket(self):
        from draftwright.recognition import recognise_pockets, recognise_slots

        part = Box(80, 60, 20) - Box(40, 10, 40)  # open both ends
        assert recognise_pockets(part) == []
        assert len(recognise_slots(part)) == 1  # still a through-slot

    def test_deep_pocket_reads_depth_axis_from_the_floor_not_size(self):
        # #609 review (major): a pocket DEEPER than it is long. The depth axis must come from
        # the capped (floor) end, not the size heuristic — else the deep span is mislabelled
        # 'length' and an end-wall is taken for the floor, swapping two of three dims + the view.
        from draftwright.recognition import recognise_pockets

        part = Box(80, 60, 40) - Pos(0, 0, 7.5) * Box(20, 10, 25)  # footprint 20×10, depth 25
        pockets = recognise_pockets(part)
        assert len(pockets) == 1
        p = pockets[0]
        assert (p.width, p.length, p.depth) == (10, 20, 25)
        assert p.depth_axis == "z"  # opening is up the Z axis → callout reads in the plan view

    def test_sealed_internal_void_is_not_a_pocket(self):
        # A cavity capped on BOTH ends of every axis (no opening) is not a pocket — the depth
        # axis must be open on one side. A plain solid trivially has none; the guard is the
        # exactly-one-capped-end rule that also excludes a sealed void.
        from draftwright.recognition import recognise_pockets

        assert recognise_pockets(Box(60, 60, 60)) == []

    def test_recognised_pocket_gets_callout(self):
        dwg = build_drawing(Box(80, 60, 20) - Pos(0, 0, 6) * Box(30, 20, 8), number="X")
        names = [n for n in dwg.annotations() if n.startswith("m_pocket")]
        assert len(names) == 1
        assert dwg._named[names[0]].label == "20 × 30 × 8 DEEP"
        assert dwg._anno_view[names[0]] == "plan"  # z-depth opening → plan view
        assert not any(i.severity == "error" for i in dwg.lint())

    def test_side_depth_pocket_reads_in_the_side_view(self):
        # #609 review: exercise the x-depth (view_of x→side) render branch, not just z→plan.
        dwg = build_drawing(Box(60, 60, 60) - Pos(24, 0, 0) * Box(12, 20, 30), number="X")
        names = [n for n in dwg.annotations() if n.startswith("m_pocket")]
        assert len(names) == 1
        assert dwg._named[names[0]].label == "20 × 30 × 12 DEEP"
        assert dwg._anno_view[names[0]] == "side"  # x-depth opening → side view
        assert not any(i.severity == "error" for i in dwg.lint())

    def test_declared_pocket_renders_its_callout(self):
        dwg = build_drawing(
            Box(80, 60, 20),
            model=[
                pocket(
                    width=20,
                    length=30,
                    depth=8,
                    long_axis="x",
                    width_axis="y",
                    lo=-15,
                    hi=15,
                    w_center=0,
                )
            ],
            number="X",
        )
        pks = [f for f in dwg.model().features if f.kind == "pocket"]
        assert len(pks) == 1 and pks[0].depth == 8
        names = [n for n in dwg.annotations() if n.startswith("m_pocket")]
        assert len(names) == 1
        assert dwg._named[names[0]].label == "20 × 30 × 8 DEEP"  # declare-path number wiring
        assert not any(i.severity == "error" for i in dwg.lint())

    def test_hole_and_boss_safeguards_intact(self):
        # A pocketed part's holes are still recognised; a pocket does not spawn a phantom hole.
        from build123d import Cylinder

        from draftwright.recognition import recognise_holes, recognise_pockets

        part = Box(80, 60, 20) - Pos(0, 0, 6) * Box(30, 20, 8) - Pos(-30, 0, 0) * Cylinder(3, 20)
        assert len(recognise_holes(part)) == 1
        assert len(recognise_pockets(part)) == 1


class TestCountersink:
    """#575: declare a countersink (flat-head seat) — the third ADR-0011 surface for #558."""

    def test_reads_major_and_angle_off_the_cone(self):
        from build123d import Cone

        from draftwright.model.declare import read_countersink

        maj, ang = read_countersink(Cone(3, 7, 4))  # ⌀6 drill flaring to ⌀14, 90° included
        assert maj == pytest.approx(14.0) and ang == pytest.approx(90.0, abs=0.5)

    def test_explicit_csink_renders_the_callout(self):
        part = Box(90, 60, 12) - Pos(0, 0, 0) * Cylinder(3, 12)
        dwg = build_drawing(
            part, model=[hole(diameter=6, at=(0, 0, 6), axis="z", csink=(14, 90))], number="X"
        )
        leaders = [dwg._named[n] for n in dwg._named if n.startswith("hc_")]
        assert leaders and any(14.0 in ldr.covers_diameters for ldr in leaders)

    def test_fluent_countersink_reads_the_cone(self):
        from build123d import Cone

        part = Box(90, 60, 12) - Pos(0, 0, 0) * Cylinder(3, 12)
        s = Sheet(part)
        s.hole(diameter=6, at=(0, 0, 6), axis="z").countersink(Cone(3, 7, 4))
        assert s._features[0].csink is not None
        assert abs(s._features[0].csink[0] - 14) < 0.1 and abs(s._features[0].csink[1] - 90) < 0.5

    def test_needs_cone_or_explicit(self):
        s = Sheet(Box(10, 10, 10))
        with pytest.raises(ValueError):
            s.hole(diameter=6, at=(0, 0, 0), axis="z").countersink()

    def test_drill_point_cone_is_rejected(self):
        # #582 review: a single-rim drill-point cone is not a countersink (mirror recogniser).
        from build123d import Cone

        from draftwright.model.declare import read_countersink

        with pytest.raises(ValueError, match="flared cone"):
            read_countersink(Cone(0, 5, 4))

    def test_rejects_impossible_angle(self):
        # #582 review: an included angle >= 180° is not a real cone.
        with pytest.raises(ValueError, match="180"):
            hole(diameter=6, at=(0, 0, 0), axis="z", csink=(14, 200))


class TestPlate:
    """#577: declare a thin slab's thickness — the third ADR-0011 surface for #559."""

    def test_reads_thin_axis_and_extent_off_the_slab(self):
        from draftwright.model.declare import _read_plate

        # Off the origin so u/v (the slab centre on the two NON-thin axes, in axis order)
        # are non-zero and a u/v swap or a min/max-vs-centre bug can't hide.
        axis, lo, hi, u, v = _read_plate(Pos(12, 7, 0) * Box(80, 50, 8))  # thinnest span = Z (8)
        assert axis == "z" and hi - lo == pytest.approx(8.0)
        assert (u, v) == pytest.approx((12.0, 7.0))  # u=X-centre, v=Y-centre (axis order)

    def test_explicit_plate(self):
        f = plate(axis="z", lo=0, hi=4, u=10, v=5)
        assert isinstance(f, PlateFeature)
        assert f.axis == "z" and f.hi - f.lo == pytest.approx(4.0) and (f.u, f.v) == (10, 5)

    def test_declared_plates_render_thickness_dims(self):
        # An L-bracket declared as two plates renders both thickness dims — assert the
        # dims actually LAND (named + labelled + lint-clean), not just that the model
        # echoes back what we handed it (detection is skipped for a declared model).
        lbr = Box(80, 50, 8) + Pos(-36, 0, 29) * Box(8, 50, 50)
        model = [plate(Box(80, 50, 8)), plate(axis="x", lo=-40, hi=-32, u=0, v=27)]
        dwg = build_drawing(lbr, model=model, number="X")
        plate_dims = {n: dwg._named[n].label for n in dwg._named if n.startswith("dim_plate")}
        assert sorted(plate_dims) == ["dim_plate_x0", "dim_plate_z0"]  # both slabs dimensioned
        assert sorted(plate_dims.values()) == ["8", "8"]  # each 8 thick
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_needs_object_or_explicit(self):
        with pytest.raises(ValueError):
            plate(axis="z", lo=0)  # missing hi / u / v

    def test_rejects_nonpositive_thickness(self):
        with pytest.raises(ValueError, match="hi > lo"):
            plate(axis="z", lo=4, hi=4, u=0, v=0)


class TestStepLevel:
    """#578: declare a prismatic height ladder + step-position shoulders — the emit +
    declare surfaces for #555 (recognition landed there)."""

    # A rebated block: base slab Z∈[-5,5], a raised step (x∈[-40,0]) rising from Z=5.
    def _stepped(self):
        return Box(80, 40, 10) + Pos(-20, 0, 10) * Box(40, 40, 12)

    def test_reads_ladder_off_the_part(self):
        f = step_level(self._stepped())
        assert isinstance(f, StepLevelFeature)
        assert f.base == pytest.approx(-5.0)
        assert f.levels == pytest.approx((5.0,))  # interior level (base<z<top)
        assert f.shoulders == (("x", 0.0),)  # single-level rebate → riser position read
        assert f.datum == pytest.approx((-40.0, -20.0, -5.0))
        assert f.frame.origin == pytest.approx(
            (0.0, 0.0, -5.0)
        )  # bbox centre X/Y — matches detect

    def test_object_flavour_matches_detection(self):
        # #578 review: the object flavour must read the SAME area-filtered levels detection
        # does — a tiny incidental face must not leak in as a phantom level (which would both
        # add a spurious height rung AND, via len(levels)>1, suppress the shoulder dim).
        from draftwright.builder import detect_part_model

        # single-step rebate + a tiny 3×3 pad (top area 9 ≪ 1% of the 3200 footprint)
        part = self._stepped() + Pos(35, 15, 7) * Box(3, 3, 4)
        f = step_level(part)
        det = next(x for x in detect_part_model(part).features if x.kind == "step_level")
        assert f.levels == (5.0,) and f.shoulders == (("x", 0.0),)  # NOT (5.0, 9.0), ()
        assert (f.levels, f.shoulders) == (det.levels, det.shoulders)  # parity with detection

    def test_explicit_step_level(self):
        f = step_level(base=0, levels=(10,), shoulders=(("X", 30),), datum=(0, 0, 0))
        assert f.base == 0 and f.levels == (10,)
        assert f.shoulders == (("x", 30.0),)  # axis normalised to lowercase

    def test_declared_step_renders_shoulder_and_height(self):
        # The step POSITION lands as dim_shoulder_x0 alongside the height ladder — assert
        # the dim renders with the SAME value detection computes (position − datum) in the
        # plan view, not just that the model echoes back (detection is skipped for a
        # declared model). Cross-checking the declared render against the detect-path math
        # catches a datum/axis/sign slip a hard-coded literal would miss.
        from draftwright.builder import detect_part_model

        part = self._stepped()
        det = next(x for x in detect_part_model(part).features if x.kind == "step_level")
        expected = str(int(det.shoulders[0][1] - det.datum[0]))
        dwg = build_drawing(part, model=[step_level(part)], number="X")
        assert dwg._named["dim_shoulder_x0"].label == expected
        assert dwg.view_of("dim_shoulder_x0") == "plan"
        assert "dim_height" in dwg._named
        assert [i for i in dwg.lint() if i.severity != "info"] == []

    def test_needs_base_and_levels(self):
        with pytest.raises(ValueError, match="base= and levels="):
            step_level(base=0)  # no levels

    def test_rejects_level_below_base(self):
        with pytest.raises(ValueError, match="above base"):
            step_level(base=10, levels=(5,))

    def test_rejects_duplicate_levels(self):
        # #578 review: a duplicate / non-increasing level double-dimensions a rung.
        with pytest.raises(ValueError, match="strictly increasing"):
            step_level(base=0, levels=(10, 10))

    def test_rejects_z_shoulder_axis(self):
        # A shoulder POSITION is horizontal; Z is the height, not a position.
        with pytest.raises(ValueError, match="'x' or 'y'"):
            step_level(base=0, levels=(10,), shoulders=(("z", 5),))


class TestExplicitOverridesObject:
    """#451: an object supplies DEFAULTS; each explicit keyword overrides that field
    independently. The reliable escape hatch for tricky geometry (tubes, counterbores,
    multi-cylinder tools) is to pass the object and override the fields inference gets wrong."""

    def test_hole_explicit_kwargs_override_object_read(self):
        # the exact probe from #451: object reads ø6/z/(0,0,0); the kwargs must win.
        f = hole(Cylinder(3, 8), diameter=2, at=(9, 9, 9), axis="x")
        assert f.diameter == 2
        assert f.frame.origin == (9, 9, 9)
        assert f.frame.axis == "x"

    def test_hole_partial_override_keeps_read_defaults(self):
        # override only the diameter; axis + location still come from the object.
        f = hole(Pos(20, 10, 4) * Cylinder(3, 8), diameter=2)
        assert f.diameter == 2
        assert f.frame.axis == "z" and f.frame.origin == pytest.approx((20, 10, 4))

    def test_boss_explicit_kwargs_override_object_read(self):
        f = boss(Cylinder(3, 8), diameter=99, at=(1, 2, 3), axis="y")
        assert f.diameter == 99 and f.frame.origin == (1, 2, 3) and f.frame.axis == "y"

    def test_step_explicit_kwargs_override_object_read(self):
        f = step(Rot(0, 90, 0) * Cylinder(2, 10), diameter=99, length=77, at=(1, 2, 3), axis="y")
        assert f.diameter == 99 and f.length == 77
        assert f.frame.origin == (1, 2, 3) and f.frame.axis == "y"

    def test_slot_explicit_kwarg_overrides_object_read(self):
        # override only the width; length / axes still read off the Box(20, 6, 4) tool.
        f = slot(Box(20, 6, 4), width=5)
        assert f.width == 5
        assert f.length == pytest.approx(20.0) and f.long_axis == "x" and f.width_axis == "y"


class TestConstructorInvariants:
    """#452: these constructors are a public compiler input; invalid data must fail at
    declaration with a clear ValueError, not later in layout or as a misleading drawing."""

    def test_hole_negative_diameter_raises(self):
        with pytest.raises(ValueError):
            hole(diameter=-6, at=(0, 0, 0), axis="z")

    def test_hole_zero_diameter_raises(self):
        with pytest.raises(ValueError):
            hole(diameter=0, at=(0, 0, 0), axis="z")

    def test_hole_negative_depth_raises(self):
        with pytest.raises(ValueError):
            hole(diameter=6, at=(0, 0, 0), axis="z", depth=-1)

    def test_hole_malformed_cbore_raises(self):
        with pytest.raises(ValueError):
            hole(diameter=6, at=(0, 0, 0), axis="z", cbore=(10,))  # not a (dia, depth) pair
        with pytest.raises(ValueError):
            hole(diameter=6, at=(0, 0, 0), axis="z", cbore=(10, -1))  # negative depth

    def test_hole_malformed_at_raises(self):
        with pytest.raises(ValueError):
            hole(diameter=6, at=(0, 0), axis="z")  # not an (x, y, z) triple

    def test_boss_negative_diameter_raises(self):
        with pytest.raises(ValueError):
            boss(diameter=-6, at=(0, 0, 0), axis="x")

    def test_step_nonpositive_length_raises(self):
        with pytest.raises(ValueError):
            step(diameter=4, length=0, at=(0, 0, 0), axis="x")

    def test_slot_same_axes_raises(self):
        with pytest.raises(ValueError):
            slot(width=6, length=20, long_axis="x", width_axis="x", lo=-10, hi=10)

    def test_slot_lo_not_less_than_hi_raises(self):
        with pytest.raises(ValueError):
            slot(width=6, length=20, long_axis="x", width_axis="y", lo=10, hi=-10)

    def test_slot_length_must_match_span(self):
        with pytest.raises(ValueError):
            slot(width=6, length=25, long_axis="x", width_axis="y", lo=-10, hi=10)  # span=20≠25

    def test_slot_negative_width_raises(self):
        with pytest.raises(ValueError):
            slot(width=-6, length=20, long_axis="x", width_axis="y", lo=-10, hi=10)

    def test_pattern_negative_bcd_raises(self):
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="bolt_circle", count=4, bcd=-40)

    def test_pattern_zero_direction_raises(self):
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="linear", count=3, pitch=10, direction=(0, 0, 0))

    def test_pattern_grid_rows_cols_must_multiply_to_count(self):
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="grid", count=4, grid=(10, 10), rows=1, cols=1)

    def test_pattern_explicit_members_must_match_count(self):
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="bolt_circle", count=4, bcd=40, members=((1, 0, 0), (2, 0, 0)))

    def test_pattern_malformed_member_point_raises(self):
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="other", count=1, members=((1, 0),))  # not an (x, y, z)

    def test_pattern_malformed_direction_raises(self):
        # a 2-tuple direction must fail cleanly, not IndexError deep in _pattern_members
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="linear", count=3, pitch=5, direction=(1, 0))

    def test_hole_cbore_none_element_raises(self):
        # a required (diameter, depth) pair must reject a None slot, not store it
        with pytest.raises(ValueError):
            hole(diameter=6, at=(0, 0, 0), axis="z", cbore=(10, None))

    def test_fractional_count_raises(self):
        with pytest.raises(ValueError):
            hole(diameter=6, at=(0, 0, 0), axis="z", count=2.5)
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="bolt_circle", count=None, bcd=20)

    def test_infinite_size_raises(self):
        with pytest.raises(ValueError):
            step(diameter=float("inf"), length=10, at=(0, 0, 0), axis="x")


class TestModelSeam:
    def test_declared_model_skips_detection(self):
        # The plate has TWO holes; declare only ONE. Detection would find both, so a
        # model with exactly one hole proves detection was bypassed.
        plate = Box(80, 50, 8)
        h1 = Pos(20, 10, 4) * Cylinder(3, 8)
        h2 = Pos(-20, 10, 4) * Cylinder(3, 8)
        part = plate - h1 - h2
        dwg = build_drawing(part, model=[envelope(plate), hole(h1)])
        kinds = [f.kind for f in dwg.model().features]
        assert kinds.count("hole") == 1

    def test_fully_declared_plate_is_lint_clean(self):
        plate = Box(80, 50, 8)
        h1 = Pos(20, 10, 4) * Cylinder(3, 8)
        h2 = Pos(-20, 10, 4) * Cylinder(3, 8)
        part = plate - h1 - h2
        dwg = build_drawing(part, model=[envelope(plate), hole(h1), hole(h2)])
        warns = [i for i in dwg.lint() if i.severity in ("warning", "error")]
        assert warns == [], [i.code for i in warns]

    def test_partial_declaration_is_flagged_by_coverage_lint(self):
        # ADR 0011 caveat: the coverage lint re-detects, so a partial declaration is
        # correctly flagged for the geometry it left undimensioned.
        plate = Box(80, 50, 8)
        h1 = Pos(20, 10, 4) * Cylinder(3, 8)
        h2 = Pos(-20, 10, 4) * Cylinder(3, 8)
        part = plate - h1 - h2
        dwg = build_drawing(part, model=[envelope(plate), hole(h1)])
        codes = {i.code for i in dwg.lint() if i.severity in ("warning", "error")}
        assert "feature_count_mismatch" in codes

    def test_orientation_inferred_from_step(self):
        shaft = Rot(0, 90, 0) * Cylinder(4, 30)  # a round bar along x
        dwg = build_drawing(shaft, model=[step(diameter=8, length=30, at=(0, 0, 0), axis="x")])
        assert dwg.model().orientation == "x"

    @staticmethod
    def _bolt_circle_part(r, z, angles):
        import math

        plate = Box(80, 80, 8)
        part = plate
        for a in angles:
            part -= Pos(
                r * math.cos(math.radians(a)), r * math.sin(math.radians(a)), z
            ) * Cylinder(3, 8)
        return plate, part

    def test_declared_bolt_circle_actually_renders(self):
        # Regression: declare.pattern once defaulted members=(), so a declared pattern
        # was shaped unlike a detected one and the balloon / BCD furniture crashed
        # (IndexError at orchestrator.py:395, ZeroDivisionError at holes.py:708) or
        # silently under-rendered. With the arrangement matching the real holes, the
        # bolt-circle furniture must actually appear — a wrong _pattern_members basis
        # would populate members but render nothing, which this asserts against.
        r, z = 25.0, 4.0
        plate, part = self._bolt_circle_part(r, z, (0, 90, 180, 270))
        member = hole(diameter=6, at=(r, 0, z), axis="z")
        pat = pattern(member, kind="bolt_circle", count=4, bcd=2 * r, at=(0, 0, z))
        dwg = build_drawing(part, model=[envelope(plate), pat])
        assert len(dwg.model().features[-1].members) == 4
        assert not [i for i in dwg.lint() if i.severity == "error"]
        # the bolt-circle centreline furniture (bc_*) proves the pattern rendered, not
        # just that members were populated
        assert any(n.startswith("bc_") for n in dwg._named), sorted(dwg._named)

    def test_declared_pattern_renders_at_its_declared_position(self):
        # #448: a declared hole/pattern renders at its DECLARED position even where it does
        # not coincide with a detected hole — the callout membership is now sourced from the
        # declared IR, not only detection (was the ADR 0011 caveat: gated on a.holes, so a
        # detection-missed declaration was silently undrawn). Here the holes physically sit at
        # 45° but the pattern is declared at 0°; the declared pattern must still render (its
        # bc_ furniture appears at the declared position), and the coverage lint still flags
        # the 45° holes the declaration left undimensioned — the declaration is authoritative,
        # not silently dropped.
        r, z = 25.0, 4.0
        plate, part = self._bolt_circle_part(r, z, (45, 135, 225, 315))
        member = hole(diameter=6, at=(r, 0, z), axis="z")
        pat = pattern(member, kind="bolt_circle", count=4, bcd=2 * r, at=(0, 0, z), angle=0)
        dwg = build_drawing(part, model=[envelope(plate), pat])
        assert any(n.startswith("bc_") for n in dwg._named), sorted(dwg._named)  # rendered
        warns = {i.code for i in dwg.lint() if i.severity in ("warning", "error")}
        assert warns  # the physically-present 45° holes remain flagged as undimensioned

    def test_detection_only_hole_render_unchanged_by_the_declared_gate(self):
        # The #448 model-driven membership is gated on model= being supplied — a plain
        # detection build is untouched (a.holes still drives the callouts).
        plate = Box(80, 50, 8)
        h1 = Pos(20, 10, 4) * Cylinder(3, 8)
        part = plate - h1
        dwg = build_drawing(part)  # no model= → detection path
        assert dwg._model_declared is False
        assert not [i for i in dwg.lint() if i.severity == "error"]

    def test_pattern_requires_arrangement_dim(self):
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="bolt_circle", count=4)  # no bcd
        with pytest.raises(ValueError):
            pattern(member, kind="linear", count=3)  # no pitch
        with pytest.raises(ValueError):
            pattern(member, kind="other", count=2)  # needs explicit members

    def test_pattern_grid_requires_rows_and_cols(self):
        # A grid pitch alone (grid=) doesn't define the layout — without rows/cols the
        # member loop collapses to a single 1×1 centre point (count disagrees with members).
        # Fail loudly instead, like the other arrangements.
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="grid", count=4, grid=(10, 10))  # no rows/cols
        # a full grid spec succeeds and yields rows×cols members
        f = pattern(member, kind="grid", count=4, grid=(10, 10), rows=2, cols=2, at=(0, 0, 0))
        assert len(f.members) == 4

    def test_pattern_zero_arrangement_dim_raises(self):
        # A zero-valued defining dim collapses every member onto the centre exactly like a
        # missing one; the guard must reject truthiness, not just is-None.
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="bolt_circle", count=4, bcd=0)
        with pytest.raises(ValueError):
            pattern(member, kind="linear", count=3, pitch=0)
        with pytest.raises(ValueError):
            pattern(member, kind="grid", count=4, grid=(0, 0), rows=2, cols=2)

    def test_pattern_explicit_members_still_need_defining_dim(self):
        # A known rendered kind needs its defining dim even when the caller overrides the
        # member layout with explicit members= — the furniture pass reads feat.bcd/pitch/grid
        # to draw the BCD / pitch / grid dims, so bcd=None would crash the render (not just
        # the computed-members path). Only kind='other' is exempt.
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        locs = ((20, 0, 0), (-20, 0, 0))
        with pytest.raises(ValueError):
            pattern(member, kind="bolt_circle", count=2, members=locs)  # bcd missing
        with pytest.raises(ValueError):
            pattern(member, kind="linear", count=2, members=locs)  # pitch missing
        # kind="other" needs no defining dim
        assert pattern(member, kind="other", count=2, members=locs).members == locs

    def test_pattern_requires_positive_count(self):
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="bolt_circle", count=0, bcd=40)
        with pytest.raises(ValueError):
            pattern(member, kind="linear", count=-3, pitch=10)

    def test_pattern_unknown_kind_raises(self):
        # A typo'd / unsupported arrangement name must fail loudly, not fall through to an
        # empty-member degenerate pattern (which renders a wrong count× callout).
        member = hole(diameter=3, at=(0, 0, 0), axis="z")
        with pytest.raises(ValueError):
            pattern(member, kind="circular", count=6, bcd=50)

    def test_constructors_normalize_uppercase_axis(self):
        # build123d callers naturally write axis="X"/"Z"; the lowercase IR convention is
        # internal, so uppercase must be accepted (normalized), not crash in "xyz".index().
        assert hole(diameter=6, at=(0, 0, 0), axis="Z").frame.axis == "z"
        assert step(diameter=4, length=10, at=(0, 0, 0), axis="X").frame.axis == "x"
        member = hole(diameter=3, at=(0, 0, 0), axis="Z")
        assert pattern(member, kind="bolt_circle", count=4, bcd=40).frame.axis == "z"
        # an invalid axis letter fails clearly
        with pytest.raises(ValueError):
            hole(diameter=6, at=(0, 0, 0), axis="w")

    def test_partmodel_used_verbatim(self):
        plate = Box(40, 40, 6)
        m = PartModel(
            bbox=plate.bounding_box(),
            orientation=None,
            features=[envelope(plate)],
            datums=[],
        )
        dwg = build_drawing(plate, model=m)
        assert [f.kind for f in dwg.model().features] == ["envelope"]


class TestSheet:
    def test_parse_scale(self):
        assert _parse_scale("2:1") == 2.0
        assert _parse_scale("1:2") == 0.5
        assert _parse_scale(3.0) == 3.0
        assert _parse_scale("3") == 3.0
        assert _parse_scale(None) is None

    def test_parse_scale_rejects_malformed(self):
        with pytest.raises(ValueError):
            _parse_scale("2:0")  # zero denominator
        with pytest.raises(ValueError):
            _parse_scale("abc")  # not a number, not a ratio

    def test_sheet_builds_declared_drawing_lint_clean(self):
        plate = Box(80, 50, 8)
        h1 = Pos(20, 10, 4) * Cylinder(3, 8)
        h2 = Pos(-20, 10, 4) * Cylinder(3, 8)
        part = plate - h1 - h2
        sheet = Sheet(part, title="PLATE", number="DWG-777", scale="2:1")
        sheet.envelope()
        sheet.hole(h1)
        sheet.hole(h2)
        dwg = sheet.build()
        assert sheet._opts["scale"] == 2.0
        warns = [i for i in dwg.lint() if i.severity in ("warning", "error")]
        assert warns == [], [i.code for i in warns]

    def test_sheet_export_defaults_to_pdf_dict(self, tmp_path):
        # #702: the facade speaks the modern export API — PDF by default (matching
        # the CLI), {format: path} return — not the deprecated (svg, dxf) tuple.
        sheet = Sheet(Box(40, 40, 10), title="EXPORT", number="DWG-702")
        sheet.envelope()
        paths = sheet.export(str(tmp_path / "dwg702"))
        assert set(paths) == {"pdf"}
        assert paths["pdf"].endswith(".pdf") and (tmp_path / "dwg702.pdf").exists()

    def test_sheet_export_formats_passthrough(self, tmp_path):
        sheet = Sheet(Box(40, 40, 10), title="EXPORT", number="DWG-702B")
        sheet.envelope()
        paths = sheet.export(str(tmp_path / "dwg702b"), formats=("svg", "dxf"))
        assert set(paths) == {"svg", "dxf"}
        assert (tmp_path / "dwg702b.svg").exists() and (tmp_path / "dwg702b.dxf").exists()

    def test_hole_depth_makes_it_blind(self):
        part = Box(40, 40, 10) - Pos(0, 0, 5) * Cylinder(3, 10)
        sheet = Sheet(part)
        sheet.hole(Pos(0, 0, 5) * Cylinder(3, 10)).depth(6)
        f = sheet.features[0]
        assert f.through is False and f.depth == 6

    def test_hole_through_is_default(self):
        sheet = Sheet(Box(10, 10, 10))
        h = sheet.hole(diameter=3, at=(0, 0, 0), axis="z")
        assert sheet.features[0].through is True
        # the handle is chainable and idempotent
        h.through()
        assert sheet.features[0].through is True

    def test_from_part_seeds_detection(self):
        plate = Box(80, 50, 8)
        h1 = Pos(20, 10, 4) * Cylinder(3, 8)
        part = plate - h1
        sheet = Sheet.from_part(part)
        # detection recovered at least the envelope + the hole
        kinds = {f.kind for f in sheet.features}
        assert "hole" in kinds
        # ... and the seeded set is editable for a hybrid override
        assert isinstance(sheet.features, list)

    def test_envelope_defaults_to_the_part(self):
        part = Box(30, 20, 5)
        sheet = Sheet(part)
        sheet.envelope()
        f = sheet.features[0]
        assert f.width == pytest.approx(30) and f.depth == pytest.approx(20)

    def test_model_does_not_render_a_drawing(self, monkeypatch):
        # #453: Sheet.model() must wrap the features into a PartModel WITHOUT building a
        # drawing — no projection/annotation/repack/render. Patch build_drawing to explode
        # so any accidental render is caught.
        import draftwright.sheet as sd

        monkeypatch.setattr(sd, "build_drawing", _boom)
        part = Box(40, 40, 8) - Pos(10, 10, 4) * Cylinder(3, 8)
        sheet = Sheet(part)
        sheet.envelope()
        sheet.hole(Pos(10, 10, 4) * Cylinder(3, 8))
        m = sheet.model()  # must not call build_drawing
        assert [f.kind for f in m.features] == ["envelope", "hole"]

    def test_model_matches_what_build_would_draw(self):
        # The cheap model() returns the same IR build() hands the engine — features AND the
        # bbox/datum (the wrapping the engine draws), not just the feature list.
        part = Box(80, 50, 8) - Pos(20, 10, 4) * Cylinder(3, 8)
        sheet = Sheet(part)
        sheet.envelope()
        sheet.hole(Pos(20, 10, 4) * Cylinder(3, 8))
        m, built = sheet.model(), sheet.build().model()
        assert m.features == built.features
        assert m.bbox.min.X == pytest.approx(built.bbox.min.X)
        assert m.bbox.max.X == pytest.approx(built.bbox.max.X)
        assert [d.at for d in m.datums] == [d.at for d in built.datums]

    def test_model_wraps_the_solids_body_like_build(self):
        # #453 review: model() must wrap the SOLIDS body, matching _analyse — else a part
        # carrying bbox-extending non-solid geometry (a stray edge) gives model() a wider
        # bbox/datum than build() draws.
        from build123d import Compound, Edge

        box = Box(40, 40, 8)
        stray = Edge.make_line((-80, 0, 0), (0, 0, 0))  # extends the min-X corner past the solid
        part = Compound(children=[*box.solids(), stray])
        sheet = Sheet(part)
        sheet.envelope()
        m, built = sheet.model(), sheet.build().model()
        assert m.bbox.min.X == pytest.approx(built.bbox.min.X)  # both drop the stray edge
        assert [d.at for d in m.datums] == [d.at for d in built.datums]

    def test_from_part_does_not_render_a_drawing(self, monkeypatch):
        # #453: the hybrid seed detects the model without a full drawing.
        import draftwright.sheet as sd

        monkeypatch.setattr(sd, "build_drawing", _boom)
        part = Box(80, 50, 8) - Pos(20, 10, 4) * Cylinder(3, 8)
        sheet = Sheet.from_part(part)  # must not call build_drawing
        assert "hole" in {f.kind for f in sheet.features}


class TestSheetDslShim:
    """The deprecated ``sheet_dsl`` alias (renamed to ``sheet``, #640) still resolves."""

    def test_shim_warns_and_aliases(self):
        import importlib
        import sys

        sys.modules.pop("draftwright.sheet_dsl", None)
        with pytest.warns(DeprecationWarning, match="renamed to.*draftwright.sheet"):
            shim = importlib.import_module("draftwright.sheet_dsl")
        import draftwright.sheet as sheet

        assert shim.Sheet is sheet.Sheet
        assert shim._parse_scale is sheet._parse_scale  # private helpers alias too

    def test_shim_star_import_and_dir_surface(self):
        # __getattr__ alone is invisible to `import *` (no __all__ → only real
        # globals bind) and to dir(); the shim must mirror the pre-rename surface.
        import draftwright.sheet as sheet
        import draftwright.sheet_dsl as shim

        ns: dict = {}
        exec("from draftwright.sheet_dsl import *", ns)  # noqa: S102 — the pattern under test
        assert ns["Sheet"] is sheet.Sheet
        assert "Sheet" in dir(shim)
        public = {n for n in dir(sheet) if not n.startswith("_")}
        assert public <= set(shim.__all__)
