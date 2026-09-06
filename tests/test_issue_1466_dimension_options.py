"""An agent can discover supported Sheet edits without trial rendering."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import Sheet
from draftwright.model import hole
from draftwright.model.ir import PLACEMENT_SIDES, PLACEMENT_VIEWS, RequestedDimension
from draftwright.model.planner import plan_dimensions
from draftwright.sheet_emit import emit_sheet_script


def _sheet(axis="z"):
    sheet = Sheet(Box(40, 30, 10)).authored_dimensions()
    handle = sheet.hole(diameter=4, depth=3, at=(0, 0, 0), axis=axis)
    return sheet, handle


def test_hole_options_are_pairs_with_omission_distinct_from_an_override():
    sheet, handle = _sheet()
    options = sheet.dimension_options(handle, "bore.diameter")
    assert options["parameter_id"] == "bore.diameter"
    assert options["scope"] == "single_dimension_placement_rules"
    assert options["requires_build_validation"] is True
    assert options["placements"] == [
        {"view": None, "side": None},
        {"view": None, "side": "left"},
        {"view": None, "side": "right"},
        {"view": "plan", "side": None},
        {"view": "plan", "side": "left"},
        {"view": "plan", "side": "right"},
    ]
    assert json.loads(json.dumps(options)) == options


@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_every_reported_pair_and_refusal_agrees_with_actual_planning(axis):
    sheet, handle = _sheet(axis)
    model = sheet.model()
    target = model.features[0]
    options = sheet.dimension_options(handle, "bore.diameter")["placements"]
    for view in (None, *sorted(PLACEMENT_VIEWS)):
        for side in (None, *sorted(PLACEMENT_SIDES)):
            candidate = replace(
                model,
                authored_dimensions=(
                    RequestedDimension(target, "bore.diameter", view=view, side=side),
                ),
            )
            advertised = {"view": view, "side": side} in options
            result = sheet.validate_dimension(handle, "bore.diameter", view=view, side=side)
            assert result["supported"] == advertised
            if advertised:
                plan_dimensions(candidate)
                assert result["issues"] == []
            else:
                with pytest.raises(ValueError):
                    plan_dimensions(candidate)
                assert result["issues"][0]["code"] == "unsupported_placement"


def test_location_and_envelope_constraints_are_discoverable():
    sheet, hole_handle = _sheet()
    assert sheet.dimension_options(hole_handle, "location")["placements"] == [
        {"view": None, "side": None}
    ]
    envelope = sheet.envelope()
    assert sheet.dimension_options(envelope, "height.length")["placements"] == [
        {"view": None, "side": None},
        {"view": "front", "side": None},
    ]
    assert not sheet.validate_dimension(envelope, "height.length", side="left")["supported"]
    result = sheet.validate_dimension(hole_handle, "location", axis="y", side="below")
    assert result["issues"][0]["code"] == "invalid_measurement"


def test_pattern_pitch_has_no_override_and_its_full_id_names_the_axis():
    sheet = Sheet(Box(40, 30, 10))
    handle = sheet.pattern(
        hole(diameter=4, at=(0, 0, 0), axis="z"),
        kind="grid",
        count=6,
        rows=2,
        cols=3,
        grid=(10, 12),
        at=(0, 0, 0),
    )
    ids = handle.dimension_ids()
    assert "grid_pitch.length.row" in ids and "grid_pitch.length.col" in ids
    options = sheet.dimension_options(handle, "grid_pitch", axis="row")
    assert options["parameter_id"] == "grid_pitch.length.row"
    assert options["axis"] == "row"
    assert options["placements"] == [{"view": None, "side": None}]
    assert sheet.dimension_options(handle, "grid_pitch.length.row") == options
    assert not sheet.validate_dimension(handle, "grid_pitch.length.row", axis="col")["supported"]
    assert not sheet.validate_dimension(handle, "grid_pitch")["supported"]


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"side": "bottom"}, "unsupported_placement"),
        ({"view": "iso"}, "unsupported_placement"),
        ({"side": []}, "unsupported_placement"),
        ({"lane": "outer"}, "unsupported_control"),
        ({"pin": True, "priority": 2}, "unsupported_control"),
    ],
)
def test_bad_controls_return_json_refusals_and_valid_alternatives(kwargs, code):
    sheet, handle = _sheet()
    result = sheet.validate_dimension(handle, "bore.diameter", **kwargs)
    assert not result["supported"]
    assert result["issues"][0]["code"] == code
    assert {"view": "plan", "side": "right"} in result["options"]["placements"]
    assert json.loads(json.dumps(result)) == result


def test_queries_preserve_existing_intent_and_require_no_build_or_preparation(monkeypatch):
    sheet, handle = _sheet()
    sheet.dimension(handle, "bore.diameter", view="plan", side="left")
    before = sheet.model()
    features = tuple(sheet.features)

    def forbidden(*args, **kwargs):
        pytest.fail("discovery must not prepare, build, or recognise")

    import b123d_recognisers

    monkeypatch.setattr(Sheet, "_prepare", forbidden)
    monkeypatch.setattr(Sheet, "build", forbidden)
    monkeypatch.setattr(b123d_recognisers, "build_raw_recognition_result", forbidden)
    result = sheet.validate_dimension(handle, "bore.diameter", view="plan", side="right")
    assert result["supported"] and result["scope"] == "single_dimension_placement_rules"
    assert result["requires_build_validation"] is True
    assert all(a is b for a, b in zip(features, sheet.features, strict=True))
    monkeypatch.undo()
    after = sheet.model()
    assert after.authored_dimensions == before.authored_dimensions
    assert len(after.authored_dimensions) == 1
    assert after.authored_dimensions[0].side == "left"


def test_handles_survive_reorder_but_foreign_removed_and_unknown_targets_are_refused():
    sheet, handle = _sheet()
    other = sheet.hole(diameter=8, at=(5, 0, 0), axis="x")
    before = sheet.dimension_options(handle, "bore.diameter")
    sheet.features.reverse()
    assert sheet.dimension_options(handle, "bore.diameter") == before
    assert sheet.dimension_options(other, "bore.diameter")["placements"] != before["placements"]
    foreign, foreign_handle = _sheet()
    assert not sheet.validate_dimension(foreign_handle, "bore.diameter")["supported"]
    assert not sheet.validate_dimension(handle, "not.a.measurement")["supported"]
    sheet.features.pop()
    assert not sheet.validate_dimension(handle, "bore.diameter")["supported"]
    with pytest.raises(ValueError):
        sheet.dimension_options(handle, "bore.diameter")


@pytest.mark.parametrize("populated,index", [(True, 1), (True, -2), (False, 0), (False, -1)])
def test_out_of_range_indices_return_structured_refusals(populated, index):
    sheet = Sheet(Box(20, 20, 10))
    if populated:
        sheet.envelope()
        assert sheet.validate_dimension(0, "height.length")["supported"]
        assert sheet.validate_dimension(-1, "height.length")["supported"]
    with pytest.raises(IndexError, match="out of range"):
        sheet.dimension_options(index, "height.length")
    result = sheet.validate_dimension(index, "height.length")
    assert not result["supported"]
    assert result["options"] is None
    assert result["issues"][0]["code"] == "invalid_measurement"
    assert "out of range" in result["issues"][0]["message"]
    assert json.loads(json.dumps(result)) == result


def test_supported_placement_survives_generated_script_and_real_build(tmp_path):
    part = Box(40, 30, 10) - Cylinder(2, 10)
    sheet = Sheet(part).authored_dimensions()
    handle = sheet.hole(diameter=4, depth=10, at=(0, 0, 0), axis="z")
    assert sheet.validate_dimension(handle, "bore.diameter", view="plan", side="left")["supported"]
    sheet.dimension(handle, "bore.diameter", view="plan", side="left")
    source = emit_sheet_script(
        sheet.model(),
        "part",
        str(tmp_path / "options"),
        title="Options",
        number="1466",
        formats=(),
    )
    namespace = {"part": part}
    exec(compile(source, "<options-roundtrip>", "exec"), namespace)
    drawing = namespace["drawing"]
    assert any(
        key["parameter_id"] == "bore.diameter"
        for name in drawing.annotations()
        for key in drawing.measurement_keys(name)
    )
    request = namespace["sheet"].model().authored_dimensions[0]
    assert (request.view, request.side) == ("plan", "left")


@pytest.mark.parametrize("axis,rotation", [("x", (0, 90, 0)), ("y", (90, 0, 0)), ("z", (0, 0, 0))])
@pytest.mark.parametrize("parameter_id", ["step.diameter", "step.length"])
def test_step_options_match_the_view_that_actually_renders(axis, rotation, parameter_id):
    part = Cylinder(5, 20, rotation=rotation)
    sheet = Sheet(part)
    handle = sheet.step(part)
    assert sheet.features[0].frame.axis == axis
    expected_view = "side" if axis == "y" and parameter_id == "step.length" else "front"
    options = sheet.dimension_options(handle, parameter_id)
    assert options["placements"] == [
        {"view": None, "side": None},
        {"view": expected_view, "side": None},
    ]
    for view in PLACEMENT_VIEWS:
        assert sheet.validate_dimension(handle, parameter_id, view=view)["supported"] == (
            view == expected_view
        )
    sheet.dimension(handle, parameter_id, view=expected_view)
    plan_dimensions(sheet.model(), planned_views=(expected_view,))
    drawing = sheet.build()
    names = [
        name
        for name in drawing.annotations()
        if any(key["parameter_id"] == parameter_id for key in drawing.measurement_keys(name))
    ]
    assert names, "the requested measurement must reach a rendered annotation"
    assert {drawing.view_of(name) for name in names} == {expected_view}


def test_grm04_discovers_supported_edits_without_exploratory_renders(monkeypatch):
    path = Path(__file__).parent / "fixtures/grm04_drive_plate.step"
    sheet = Sheet.from_part(path)
    holes = [f for f in sheet.features if f.kind == "hole" and abs(f.diameter - 2.4) < 1e-6]
    assert holes, "the reported small hole must be present"
    envelopes = [f for f in sheet.features if f.kind == "envelope"]
    assert len(envelopes) == 1

    def forbidden(*args, **kwargs):
        pytest.fail("an agent must discover these restrictions without rendering")

    monkeypatch.setattr(Sheet, "build", forbidden)
    assert not sheet.validate_dimension(holes[0], "location", side="below")["supported"]
    assert not sheet.validate_dimension(envelopes[0], "height.length", side="left")["supported"]
    options = sheet.dimension_options(holes[0], "bore.diameter")
    assert options["placements"]
    assert not sheet.validate_dimension(holes[0], "bore.diameter", side="bottom")["supported"]
    for pair in options["placements"]:
        assert sheet.validate_dimension(holes[0], "bore.diameter", **pair)["supported"]


@pytest.mark.parametrize("role", ["location", "missing.length"])
def test_planner_support_query_refuses_an_absent_ir_measurement(role):
    from draftwright.model.planner import validate_dimension_placement

    sheet = Sheet(Box(20, 20, 10))
    sheet.envelope()
    with pytest.raises(ValueError, match="no location|no parameter"):
        validate_dimension_placement(RequestedDimension(sheet.features[0], role))


def test_single_dimension_support_does_not_claim_authored_view_feasibility():
    from draftwright import ViewPlanIncomplete

    sheet, handle = _sheet()
    sheet.authored_views().view("front")
    assert sheet.validate_dimension(handle, "bore.diameter", view="plan")["supported"]
    sheet.dimension(handle, "bore.diameter", view="plan")
    with pytest.raises(ViewPlanIncomplete):
        plan_dimensions(sheet.model(), planned_views=("front",))


def test_boss_rule_acceptance_requires_whole_part_renderer_validation():
    sheets = [
        Sheet(Cylinder(5, 20)),
        Sheet(Box(20, 20, 20) + Pos(0, 0, 15) * Cylinder(5, 10)),
    ]
    options = []
    rendered_views = []
    for sheet, height, at in zip(sheets, (20, 10), ((0, 0, 0), (0, 0, 15)), strict=True):
        handle = sheet.boss(diameter=10, height=height, at=at, axis="z")
        result = sheet.validate_dimension(handle, "boss.diameter", view="plan")
        assert result["supported"]
        assert result["scope"] == "single_dimension_placement_rules"
        assert result["requires_build_validation"] is True
        assert result["options"]["requires_build_validation"] is True
        options.append(result["options"])
        sheet.dimension(handle, "boss.diameter", view="plan")
        drawing = sheet.build()
        names = [
            name
            for name in drawing.annotations()
            if any(
                key["parameter_id"] == "boss.diameter" for key in drawing.measurement_keys(name)
            )
        ]
        assert names
        rendered_views.append({drawing.view_of(name) for name in names})
    assert options[0] == options[1]
    assert rendered_views == [{"front"}, {"plan"}], "whole-part context changes the renderer"
