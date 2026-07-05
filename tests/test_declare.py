"""Tests for the declarative drawing surface (ADR 0011): the ``model=`` seam, the
object→feature constructors (:mod:`draftwright.model.declare`), and the fluent
:class:`draftwright.Sheet` façade.

The constructors are pure geometry reads (fast); the seam / Sheet tests do a real
OCC build (fast tier — not marked slow).
"""

import pytest
from build123d import Box, Cylinder, GeomType, Pos, Rot

from draftwright import Sheet, build_drawing
from draftwright.model import (
    BossFeature,
    EnvelopeFeature,
    HoleFeature,
    PartModel,
    PatternFeature,
    SlotFeature,
    StepFeature,
    boss,
    envelope,
    hole,
    pattern,
    slot,
    step,
)
from draftwright.sheet_dsl import _parse_scale


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

    def test_declared_pattern_off_detected_positions_is_flagged_not_rendered(self):
        # ADR 0011 caveat (widened after review): the hole/pattern render path gates on
        # feature_keys built from DETECTION (a.holes), so a declared pattern whose members
        # do not coincide (to 3 dp) with the detected holes is not rendered — it surfaces
        # as a coverage warning, not silently. Full model-driven hole rendering is a
        # follow-up (#448). Here the holes sit at 45° but the pattern is declared at 0°.
        r, z = 25.0, 4.0
        plate, part = self._bolt_circle_part(r, z, (45, 135, 225, 315))
        member = hole(diameter=6, at=(r, 0, z), axis="z")
        pat = pattern(member, kind="bolt_circle", count=4, bcd=2 * r, at=(0, 0, z), angle=0)
        dwg = build_drawing(part, model=[envelope(plate), pat])
        assert not any(n.startswith("bc_") for n in dwg._named)  # not rendered
        warns = {i.code for i in dwg.lint() if i.severity in ("warning", "error")}
        assert warns  # but flagged, not silent

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
