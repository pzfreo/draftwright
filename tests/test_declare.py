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
        assert _parse_scale(None) is None

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
