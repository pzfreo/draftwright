"""Feature and location coverage lint behavior."""

import math

import pytest
from build123d import Axis, Box, Compound, Cylinder, Pos
from build123d_drafting import HoleCallout

from draftwright import build_drawing
from draftwright.make_drawing import lint_feature_coverage


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

        callout = HoleCallout("8.5 H7", through=True)
        assert callout.covers_diameters == (8.5,)

    @pytest.mark.timeout(60)
    def test_fillets_are_not_features(self):
        from build123d import fillet

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
        for name in [n for n in dwg.annotations() if n.startswith("hc_")]:
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

        class ExternalDrawing:
            """The documented lint duck contract deliberately has no model()."""

            at = dwg.at
            iter_annotations = dwg.iter_annotations
            view_of = dwg.view_of

        assert lint_location_coverage(part, ExternalDrawing()) == []

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

    @pytest.mark.timeout(60)
    def test_structured_location_is_owned_by_the_exact_hole_and_axis(self):
        from types import SimpleNamespace

        from build123d_drafting import Dimension

        from draftwright.linting import lint_location_coverage

        part = Box(100, 60, 12) - Pos(30, 0, 0) * Cylinder(4, 30)
        dwg = build_drawing(part, number="X", auto_dims=False)
        feature = next(item for item in dwg.model().features if item.kind == "hole")
        point = feature.frame.origin
        px, py, *_ = dwg.at("plan", *point)
        # Its witness geometry aligns with the required X ordinate.  Because it also
        # carries structured evidence, that wrong-axis semantic claim is authoritative
        # and the same annotation may not rescue itself through geometric fallback.
        dim = Dimension((px, py, 0), (px + 10, py, 0), "above", 8, dwg.draft)
        dim.covers_hole_locations = ((feature, "location.location.y", point),)
        # The registry is the public evidence seam this lint reads.  Register directly so
        # the fixture can model an external producer's invalid semantic claim without using
        # Drawing's private low-level placement primitive.
        dwg.registry.add(dim, "wrong_axis", "plan", feature=feature)

        assert any(i.code == "feature_not_located" for i in lint_location_coverage(part, dwg))

        # Malformed structured evidence is not authoritative. External producers
        # retain the documented geometric fallback when no fact can be decoded.
        dim.covers_hole_locations = ((SimpleNamespace(feature=feature), point),)
        assert not any(i.code == "feature_not_located" for i in lint_location_coverage(part, dwg))

        # The legacy two-tuple carries the same ownership through its measurement object.
        dim.covers_hole_locations = (
            (SimpleNamespace(feature=feature, parameter="location.location.x"), point),
        )
        assert not any(i.code == "feature_not_located" for i in lint_location_coverage(part, dwg))
