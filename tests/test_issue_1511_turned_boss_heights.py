"""Approved end-cap heights survive alongside a turned profile (#1511)."""

from dataclasses import replace

import pytest
from build123d import Align, Compound, Cylinder, Pos, Rot

from draftwright import build_drawing
from draftwright.builder import detect_part_model
from draftwright.model.compiled import compile_dimensions
from draftwright.model.ir import RequestedDimension
from draftwright.sheet_emit import emit_sheet_script


@pytest.fixture(scope="module", params=("x", "y", "z"))
def capped_shaft(request):
    axis = request.param
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    shaft = Cylinder(18, 25, align=align) + Pos(0, 0, 25) * Cylinder(16, 25, align=align)
    rotation = {"x": Rot(0, 90, 0), "y": Rot(-90, 0, 0), "z": Rot(0, 0, 0)}[axis]
    part = rotation * Compound(
        children=[
            shaft,
            Pos(0, 0, -6) * Cylinder(20, 6, align=align),
            Pos(0, 0, 50) * Cylinder(20, 6, align=align),
        ]
    )
    drawing = build_drawing(part, page="A3", scale=1, scale_policy="strict")
    plan = compile_dimensions(drawing.model())
    assert len(plan.of_kind("step")) == 2
    heights = [group.dim(role="boss_height", kind="length") for group in plan.of_kind("boss")]
    assert len(heights) == 2 and all(height.value == 6 for height in heights)
    assert all(group.facts.frame.axis == axis for group in plan.of_kind("boss"))
    return part, drawing, heights, axis


def test_cap_heights_have_their_own_claims_and_physical_supports(capped_shaft):
    _part, drawing, heights, axis = capped_shaft
    for height in heights:
        marks = [
            (name, mark)
            for name, mark in drawing.iter_annotations()
            if height.id in drawing.registry.measurement_of(name)
        ]
        assert len(marks) == 1
        name, mark = marks[0]
        assert mark.label == "6"
        coordinate = 1 if axis == "z" else 0
        view = drawing.registry.view_of(name)
        stations = [drawing.at(view, *point)[coordinate] for point in height.span]
        strokes = [
            first[coordinate]
            for first, second in mark.segments
            if abs(first[coordinate] - second[coordinate]) < 1e-6
            and abs(first[1 - coordinate] - second[1 - coordinate]) > 1
        ]
        assert all(any(abs(station - stroke) < 1e-6 for stroke in strokes) for station in stations)
    assert not [issue for issue in drawing.lint() if issue.code == "boss_height_missing"]


def test_removing_one_cap_height_exposes_exactly_that_missing_measurement(capped_shaft):
    part, _drawing, heights, _axis = capped_shaft
    drawing = build_drawing(part, page="A3", scale=1, scale_policy="strict")
    names = [
        name
        for name in drawing.annotations()
        if heights[0].id in drawing.registry.measurement_of(name)
    ]
    assert len(names) == 1
    drawing.remove(names[0])
    assert any(
        heights[1].id in drawing.registry.measurement_of(name) for name in drawing.annotations()
    )
    issues = [issue for issue in drawing.lint() if issue.code == "boss_height_missing"]
    assert len(issues) == 1
    assert issues[0].message == "1 boss height(s) are not dimensioned"
    assert drawing.lint() == drawing.lint()


def test_cap_heights_survive_generated_sheet_code(capped_shaft, tmp_path):
    part, original, _heights, _axis = capped_shaft
    source = emit_sheet_script(
        original.model(),
        "part",
        str(tmp_path / "caps"),
        title="T",
        number="N",
        page="A3",
        scale=1,
        scale_policy="strict",
        formats=("svg",),
    )
    namespace = {"part": part}
    exec(source, namespace)
    replay = namespace["drawing"]
    marks = [
        mark
        for name, mark in replay.iter_annotations()
        if any(
            mid.parameter == "boss_height.length" for mid in replay.registry.measurement_of(name)
        )
    ]
    assert sorted(mark.label for mark in marks) == ["6", "6"]


def test_authored_omission_is_not_rendered_as_an_end_cap(capped_shaft):
    part, original, heights, _axis = capped_shaft
    model = original.model()
    authored = tuple(
        RequestedDimension(feature, parameter.parameter_id)
        for feature in model.features
        for parameter in feature.parameters()
        if not (
            feature == heights[0].id.feature and parameter.parameter_id == "boss_height.length"
        )
    )
    drawing = build_drawing(
        part,
        model=replace(model, authored_dimensions=authored),
        page="A3",
        scale=1,
        scale_policy="strict",
    )
    assert not any(
        heights[0].id in drawing.registry.measurement_of(name) for name in drawing.annotations()
    )
    assert any(
        heights[1].id in drawing.registry.measurement_of(name) for name in drawing.annotations()
    )


def test_cap_height_witnesses_do_not_require_diameter_annotation(capped_shaft):
    part, original, heights, axis = capped_shaft
    model = original.model()
    authored = tuple(
        RequestedDimension(feature, parameter.parameter_id)
        for feature in model.features
        for parameter in feature.parameters()
        if not (feature.kind == "boss" and parameter.kind == "diameter")
    )
    drawing = build_drawing(
        part,
        model=replace(model, authored_dimensions=authored),
        page="A3",
        scale=1,
        scale_policy="strict",
    )
    for height in heights:
        names = [
            name
            for name in drawing.annotations()
            if height.id in drawing.registry.measurement_of(name)
        ]
        assert len(names) == 1
        boss = height.id.feature
        assert not any(
            mid.feature == boss and mid.parameter == "boss.diameter"
            for name in drawing.annotations()
            for mid in drawing.registry.measurement_of(name)
        )
        radial = 0 if axis == "z" else 2
        raw_span = next(
            parameter.span
            for parameter in boss.parameters()
            if parameter.parameter_id == "boss_height.length"
        )
        assert raw_span[0][radial] != height.span[0][radial]
        assert height.span[0][radial] == pytest.approx(boss.frame.origin[radial] + 20)
        view = drawing.registry.view_of(names[0])
        coordinate = 1 if axis == "z" else 0
        witness_start = (
            drawing.at(view, *height.span[0])[1 - coordinate] + drawing.draft.extension_gap
        )
        strokes = [
            (first, second)
            for first, second in drawing.get_annotation(names[0]).segments
            if abs(first[coordinate] - second[coordinate]) < 1e-6
            and abs(first[1 - coordinate] - second[1 - coordinate]) > 1
        ]
        assert len(strokes) >= 2
        assert all(
            min(first[1 - coordinate], second[1 - coordinate]) == pytest.approx(witness_start)
            for first, second in strokes
        )


@pytest.mark.parametrize("axis", ("x", "y", "z"))
@pytest.mark.parametrize("tolerance", (None, 0.1))
def test_full_height_boss_yields_to_overall_only_when_untoleranced(tolerance, axis):
    part = {"x": Rot(0, 90, 0), "y": Rot(-90, 0, 0), "z": Rot(0, 0, 0)}[axis] * Cylinder(15, 40)
    model = detect_part_model(part)
    boss = next(feature for feature in model.features if feature.kind == "boss")
    assert boss.height == 40
    if tolerance is not None:
        model = replace(model, decorations={(boss, "length", "boss_height"): tolerance})
    drawing = build_drawing(part, model=model)
    plan = compile_dimensions(drawing.model())
    marks = [
        (name, mark)
        for name, mark in drawing.iter_annotations()
        if any(
            mid.parameter == "boss_height.length" for mid in drawing.registry.measurement_of(name)
        )
    ]
    if tolerance is None:
        assert marks == []
        omissions = [row for row in plan.diagnostics if row.parameter_id == "boss_height.length"]
        assert len(omissions) == 1 and omissions[0].conveyed_by is not None
        owner_names = [
            name
            for name in drawing.annotations()
            if omissions[0].conveyed_by in drawing.registry.measurement_of(name)
        ]
        assert len(owner_names) == 1
        assert drawing.get_annotation(owner_names[0]).label == "40"
        assert not [issue for issue in drawing.lint() if issue.code == "boss_height_missing"]
        drawing.remove(owner_names[0])
        assert [issue.code for issue in drawing.lint() if issue.code == "boss_height_missing"] == [
            "boss_height_missing"
        ]
    else:
        assert len(marks) == 1 and marks[0][1].label == "40 ±0.1"


@pytest.mark.parametrize("selection", ("requested_dimensions", "authored_dimensions"))
def test_explicit_full_height_boss_keeps_its_own_measurement(selection):
    model = detect_part_model(Cylinder(15, 40))
    automatic = compile_dimensions(model)
    boss = next(feature for feature in model.features if feature.kind == "boss")
    assert any(
        row.parameter_id == "boss_height.length" and row.conveyed_by
        for row in automatic.diagnostics
    )
    request = RequestedDimension(boss, "boss_height.length")
    plan = compile_dimensions(replace(model, **{selection: (request,)}))
    heights = [group.dim(role="boss_height", kind="length") for group in plan.of_kind("boss")]
    assert len(heights) == 1 and heights[0].value == 40
    assert not any(row.parameter_id == "boss_height.length" for row in plan.diagnostics)


def test_a_synthetic_overall_owner_survives_script_replay(tmp_path):
    part = Cylinder(15, 40)
    original = build_drawing(part)
    assert not any(feature.kind == "envelope" for feature in original.model().features)
    source = emit_sheet_script(
        original.model(),
        "part",
        str(tmp_path / "cylinder"),
        title="T",
        number="N",
        formats=("svg",),
    )
    assert '"boss_height.length"' not in source
    namespace = {"part": part}
    exec(source, namespace)
    replay = namespace["drawing"]
    omissions = [
        row
        for row in compile_dimensions(replay.model()).diagnostics
        if row.parameter_id == "boss_height.length"
    ]
    assert len(omissions) == 1 and omissions[0].authored and omissions[0].conveyed_by is not None
    assert not [issue for issue in replay.lint() if issue.code == "boss_height_missing"]
    replay.remove("dim_height")
    assert [issue.code for issue in replay.lint() if issue.code == "boss_height_missing"] == [
        "boss_height_missing"
    ]


def test_an_inactive_overall_contingency_cannot_own_a_boss_height():
    from draftwright.model.ir import BossFeature, Frame

    align = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Cylinder(18, 25, align=align) + Pos(0, 0, 25) * Cylinder(16, 25, align=align)
    model = detect_part_model(part)
    boss = BossFeature(
        Frame((0, 0, 50), "z"), diameter=12, height=50, span=((0, 0, 0), (0, 0, 50))
    )
    model = replace(model, features=(*model.features, boss))
    plan = compile_dimensions(model)
    assert plan.ladder("overall_height") is None
    assert plan.contingency("step_length").fallback.rungs[0].value == 50
    heights = [group.dim(role="boss_height", kind="length") for group in plan.of_kind("boss")]
    assert len(heights) == 1 and heights[0].value == 50
    assert not any(
        row.parameter_id == "boss_height.length" and row.conveyed_by for row in plan.diagnostics
    )
