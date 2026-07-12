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
    HoleFeature,
    PartModel,
    PatternFeature,
    SlotFeature,
    StepFeature,
    boss,
    chamfer,
    envelope,
    hole,
    pattern,
    slot,
    step,
)
from draftwright.sheet_dsl import _parse_scale


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
        import draftwright.sheet_dsl as sd

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
        import draftwright.sheet_dsl as sd

        monkeypatch.setattr(sd, "build_drawing", _boom)
        part = Box(80, 50, 8) - Pos(20, 10, 4) * Cylinder(3, 8)
        sheet = Sheet.from_part(part)  # must not call build_drawing
        assert "hole" in {f.kind for f in sheet.features}
