"""Grouped hole-pattern callout behavior."""

import math
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Align, Box, Cylinder, Pos, Rot, export_step
from quiddity import RectangularHoleSet, recognise_hole_patterns, recognise_holes

from draftwright import build_drawing


@pytest.mark.slow
def test_coincident_flange_patterns_and_diameters_name_both_supports_issue_2129(
    tmp_path, monkeypatch
):
    from draftwright import Drawing
    from draftwright.annotations.from_model import callout_from_spec
    from draftwright.compose import _est_planned_bore_callout_width
    from draftwright.model.callout import hole_callout_batches
    from draftwright.model.planner import plan_dimensions
    from draftwright.sheet_emit import generate_sheet_script

    # The source STEP has two separate flanges, each with its own twelve holes at
    # identical X/Y sites. The end view therefore overlays the two inventories.
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    part = (
        Cylinder(65, 5, align=align)
        + Pos(0, 0, 5) * Cylinder(35, 103, align=align)
        + Pos(0, 0, 108) * Cylinder(65, 5, align=align)
    )
    for z in (0, 108):
        for index in range(12):
            angle = 2 * math.pi * index / 12
            part -= Pos(55 * math.cos(angle), 55 * math.sin(angle), z) * Cylinder(
                4, 5, align=align
            )
    source = tmp_path / "flanged.step"
    export_step(part, str(source))
    options = dict(pmi="annotate", scale=1, page="A2", scale_policy="permissive")
    drawing = build_drawing(source, **options)

    def same_support_claims(dwg):
        diameters = [
            (name, annotation, dwg.registry.features_of(name))
            for name, annotation in dwg.iter_annotations()
            if annotation.label == "2× ø130"
        ]
        patterns = [
            (name, annotation, dwg.registry.features_of(name)[0])
            for name, annotation in dwg.iter_annotations()
            if name.startswith("hc_plan") and annotation.label.startswith("12× ⌀8")
        ]
        # The fixture contains two different axial supports for each identical
        # measurement. Its two patterns coincide in plan projection, which is
        # precisely when equal text cannot tell the reader which face it names.
        assert len(diameters) == 1 and len(patterns) == 2
        assert sorted(feature.frame.origin[2] for feature in diameters[0][2]) == [2.5, 110.5]
        assert sorted(feature.frame.origin[2] for _, _, feature in patterns) == [5.0, 113.0]
        assert (
            len(
                {
                    tuple(sorted((point[0], point[1]) for point in feature.members))
                    for _, _, feature in patterns
                }
            )
            == 1
        )
        assert diameters[0][0] == "dim_od"
        assert len(dwg.registry.measurement_of(diameters[0][0])) == 3
        assert all(dwg.registry.measurement_of(name) for name, _, _ in patterns)
        assert not any(
            name.startswith("m_dia_z") and "ø130" in str(getattr(annotation, "label", ""))
            for name, annotation in dwg.iter_annotations()
        )
        assert all(diameters[0][0] in dwg.annotations_of(feature) for feature in diameters[0][2])
        before = tuple(dwg.iter_annotations())
        with pytest.raises(ValueError, match="also measures other features"):
            dwg.drop(diameters[0][2][0])
        assert tuple(dwg.iter_annotations()) == before
        assert len(dwg.registry.measurement_of(diameters[0][0])) == 3
        assert {annotation.label for _, annotation, _ in patterns} == {
            "12× ⌀8 THRU EQ SP ON ø110 BC LOWER FACE",
            "12× ⌀8 THRU EQ SP ON ø110 BC UPPER FACE",
        }
        assert all(
            annotation.label.endswith("LOWER FACE") == (feature.frame.origin[2] == 5.0)
            for _, annotation, feature in patterns
        )
        return {name: annotation.label for name, annotation, _ in diameters + patterns}

    direct_labels = same_support_claims(drawing)
    from draftwright._core import _dim
    from draftwright.repair import _replace_dim

    original_od = drawing.get_annotation("dim_od")
    placement = original_od.placement_spec
    _replace_dim(
        drawing,
        original_od,
        _dim(
            placement.p1,
            placement.p2,
            placement.side,
            placement.distance,
            placement.draft,
            **placement.kwargs,
        ),
    )
    assert drawing.get_annotation("dim_od") is not original_od
    assert drawing.get_annotation("dim_od").indivisible_measurements
    assert same_support_claims(drawing) == direct_labels
    groups = plan_dimensions(drawing.model())
    estimate = _est_planned_bore_callout_width(groups, drawing.draft)
    rendered = [
        callout_from_spec(batch.spec, drawing.draft, batch.spec["count"])
        for batch in hole_callout_batches(groups)
        if batch.spec["count"] == 12
    ]
    assert len(rendered) == 2
    assert estimate >= max(callout.callout_width for callout in rendered)

    captured = {}
    monkeypatch.setattr(
        Drawing, "export", lambda self, *a, **k: captured.setdefault("drawing", self)
    )
    script = generate_sheet_script(source, out=str(tmp_path / "flanged"), **options)
    exec(compile(Path(script).read_text(encoding="utf-8"), script, "exec"), {})
    assert same_support_claims(captured["drawing"]) == direct_labels


@pytest.mark.slow
@pytest.mark.parametrize("radii", ((4, 4, 4), (5, 4, 4)))
def test_three_coincident_flange_patterns_name_each_axial_support_issue_2129(radii):
    from draftwright.annotations.from_model import callout_from_spec
    from draftwright.compose import _est_planned_bore_callout_width
    from draftwright.model.callout import hole_callout_batches
    from draftwright.model.planner import plan_dimensions

    align = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Cylinder(65, 5, align=align) + Pos(0, 0, 5) * Cylinder(35, 103, align=align)
    for z in (54, 108):
        part += Pos(0, 0, z) * Cylinder(65, 5, align=align)
    for z, radius in zip((0, 54, 108), radii, strict=True):
        for index in range(12):
            angle = 2 * math.pi * index / 12
            part -= Pos(55 * math.cos(angle), 55 * math.sin(angle), z) * Cylinder(
                radius, 5, align=align
            )
    drawing = build_drawing(part, scale=1, page="A2", scale_policy="permissive")
    patterns = [
        (name, annotation, drawing.registry.features_of(name)[0])
        for name, annotation in drawing.iter_annotations()
        if name.startswith("hc_plan") and annotation.label.startswith("12× ⌀")
    ]
    assert len(patterns) == 3
    assert sorted(feature.frame.origin[2] for _name, _annotation, feature in patterns) == [
        5.0,
        59.0,
        113.0,
    ]
    assert (
        len(
            {
                tuple(sorted((point[0], point[1]) for point in feature.members))
                for _name, _annotation, feature in patterns
            }
        )
        == 1
    )
    assert {
        feature.frame.origin[2]: annotation.label.rsplit(" FACE ", 1)[-1]
        for _name, annotation, feature in patterns
    } == {
        5.0: "1 OF 3 FROM LOWER END",
        59.0: "2 OF 3 FROM LOWER END",
        113.0: "3 OF 3 FROM LOWER END",
    }
    assert {
        feature.frame.origin[2]: annotation.label.split(" ", 2)[1]
        for _name, annotation, feature in patterns
    } == {5.0: f"⌀{2 * radii[0]}", 59.0: "⌀8", 113.0: "⌀8"}
    assert all(drawing.registry.measurement_of(name) for name, _ann, _feature in patterns)

    groups = plan_dimensions(drawing.model())
    batches = [batch for batch in hole_callout_batches(groups) if batch.spec["count"] == 12]
    assert len(batches) == 3
    rendered = [callout_from_spec(batch.spec, drawing.draft, 12) for batch in batches]
    assert _est_planned_bore_callout_width(groups, drawing.draft) >= max(
        callout.callout_width for callout in rendered
    )
    original_group = batches[0].groups[0]
    duplicate_group = replace(original_group, feature=replace(original_group.feature))
    assert duplicate_group.feature is not original_group.feature
    assert duplicate_group.feature.frame.origin[2] == original_group.feature.frame.origin[2]
    with pytest.raises(ValueError, match="no distinct axial stations"):
        hole_callout_batches((original_group, duplicate_group))


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
    def test_four_corner_rectangle_states_two_pitches_without_a_bolt_circle(self):
        part = Box(80, 100, 12)
        for x in (-10, 10):
            for y in (-15, 15):
                part -= Pos(x, y, 0) * Cylinder(3, 12)

        (pattern,) = recognise_hole_patterns(recognise_holes(part))
        assert isinstance(pattern, RectangularHoleSet)
        assert (pattern.width, pattern.height) == (30.0, 20.0)

        drawing = build_drawing(part)
        (feature,) = (item for item in drawing.model().features if item.kind == "pattern")
        assert feature.pattern == "grid"
        assert (feature.grid, feature.rows, feature.cols) == ((20.0, 30.0), 2, 2)
        callouts = [
            annotation.label
            for name, annotation in drawing.iter_annotations()
            if name.startswith("hc_")
        ]
        assert callouts == ["4× ⌀6 THRU (2×2)"]
        assert {
            annotation.label
            for name, annotation in drawing.iter_annotations()
            if name.startswith("dim_pitch_")
        } == {"1× 20", "1× 30"}
        assert drawing.lint() == []

    @pytest.mark.parametrize("variant", ("a", "b"))
    def test_diagonal_square_grid_pitches_survive_the_exact_ink_gate(self, variant):
        source = Path(__file__).parent / f"fixtures/evaluation/pattern-topology-{variant}.step"
        drawing = build_drawing(source, page="A3")
        patterns = [
            pattern
            for pattern in drawing.recognition().hole_patterns
            if isinstance(pattern, RectangularHoleSet)
        ]
        assert len(patterns) == 1 and patterns[0].angle == 45.0
        grid_pitches = [
            annotation
            for name, annotation in drawing.iter_annotations()
            if name.startswith("dim_pitch_plan") and annotation.label == "1× 22.6"
        ]
        assert len(grid_pitches) == 2
        assert not any(issue.code == "hole_pattern_dim_dropped" for issue in drawing.lint())
        states = {
            row["parameter_id"]: row["state"]
            for row in drawing.report()["recognition"]["requirements"]
            if row["family"] == "hole_patterns"
        }
        assert states["grid_pitch.length.row"] == states["grid_pitch.length.col"] == "placed"

    @pytest.mark.timeout(120)
    def test_rect_grid_one_callout_and_two_pitch_dims(self):
        dwg = build_drawing(self._grid_part())
        named = dict(dwg.iter_annotations())
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
            sp = named[n].placement_spec
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
        scale = dwg.scale
        pitch = [n for n in dwg.annotations() if n.startswith("dim_pitch_")]
        assert len(pitch) == 2, f"expected two grid pitch dims, got {pitch}"
        for n in pitch:
            dim = dwg.get_annotation(n)
            sp = dim.placement_spec
            span = math.hypot(sp.p2[0] - sp.p1[0], sp.p2[1] - sp.p1[1]) / scale
            k, p = dim.label.split("× ")
            expected = int(k) * float(p)
            assert abs(span - expected) < 1.0, (
                f"{n} ({dim.label!r}) endpoint span {span:.1f} ≠ {expected:.1f} — drawn diagonally"
            )

    @pytest.mark.timeout(120)
    def test_x_axis_rect_grid_keeps_both_pitches_in_the_side_view(self):
        """0.4.6 coordinate rounding must not select an interior side-view witness row."""

        ang = math.radians(25)
        ca, sa = math.cos(ang), math.sin(ang)
        centre = (Align.CENTER, Align.CENTER, Align.CENTER)
        part = Box(12, 220, 120, align=centre)
        cutter = Rot(0, 90, 0) * Cylinder(4, 20, align=centre)
        for r in range(2):
            for c in range(5):
                y, z = (c - 2) * 45, (r - 0.5) * 10
                part -= Pos(0, y * ca - z * sa, y * sa + z * ca) * cutter

        dwg = build_drawing(part)
        pitch = [name for name in dwg.annotations() if name.startswith("dim_pitch_")]

        assert len(pitch) == 2, f"expected two side-grid pitch dims, got {pitch}"
        assert {dwg.view_of(name) for name in pitch} == {"side"}
        assert {dwg.get_annotation(name).label for name in pitch} == {"1× 10", "4× 45"}
        assert "hole_pattern_dim_dropped" not in {issue.code for issue in dwg.lint()}

    @pytest.mark.timeout(120)
    def test_rect_grid_coverage_lint_quiet(self):
        codes = {i.code for i in build_drawing(self._grid_part()).lint()}
        assert "feature_not_dimensioned" not in codes
        assert "feature_count_mismatch" not in codes

    @pytest.mark.timeout(120)
    def test_perimeter_rows_dimensioned_not_per_hole(self):
        dwg = build_drawing(self._perimeter_part())
        named = dict(dwg.iter_annotations())
        pitch = [n for n in named if n.startswith("dim_pitch_")]
        # Both recognised edge rows have the same projected stations, so one
        # dimension carries both approved pitch measurements.
        shared = [n for n in pitch if named[n].label == "4× 25"]
        assert len(shared) == 1, f"expected one shared edge pitch, got {pitch}"
        assert len(dwg.measurement_keys(shared[0])) == 2
        # the rows are not exploded into a per-hole table / balloons
        assert not [n for n in named if n.startswith("balloon")]
        assert not [n for n in named if "table" in n]
        assert "feature_not_dimensioned" not in {i.code for i in dwg.lint()}

    @pytest.mark.timeout(120)
    @pytest.mark.parametrize("middle_offset, expected_count", [(0.0, 1), (0.5, 2)])
    def test_parallel_pitch_shares_only_matching_stations(self, middle_offset, expected_count):
        from draftwright.model.ir import Frame, HoleFeature, PatternFeature

        part = Box(100, 100, 20)
        patterns = []
        for x in (-20, 20):
            members = tuple(
                (x, y + (middle_offset if x == 20 and y == 0 else 0), 0) for y in (-20, 0, 20)
            )
            for px, py, _ in members:
                part -= Pos(px, py, 0) * Cylinder(3, 20)
            member = HoleFeature(Frame(members[0], "z"), 6.0, depth=None, through=True)
            patterns.append(
                PatternFeature(
                    Frame(members[0], "z"),
                    "linear",
                    3,
                    member,
                    members=members,
                    pitch=20,
                    direction=(0, 1, 0),
                )
            )
        drawing = build_drawing(part, model=patterns, page="A3", scale=0.5)
        pitch = [name for name in drawing.annotations() if name.startswith("dim_pitch_plan")]
        assert len(pitch) == expected_count
        assert all(drawing.get_annotation(name).label == "2× 20" for name in pitch)
        if middle_offset == 0:
            assert len(drawing.measurement_keys(pitch[0])) == 2
            assert all(pitch[0] in drawing.annotations_of(feature) for feature in patterns)
        else:
            assert all(len(drawing.measurement_keys(name)) == 1 for name in pitch)
