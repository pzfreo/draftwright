"""#924: datum-coincident shoulders are compiler omissions, never render geometry."""

from __future__ import annotations

import pytest
from build123d import Box, Pos

from draftwright import build_drawing
from draftwright.builder import detect_part_model
from draftwright.model.compiled import FeatureRef, compile_dimensions
from draftwright.model.ir import Frame, PartModel, StepLevelFeature
from draftwright.model.planner import plan_dimensions


def _declared_step(axis: str, *, with_nonzero_sibling: bool = False):
    part = Box(90, 60, 30)
    datum = (-45.0, -30.0, 0.0)
    coincident = datum["xyz".index(axis)]
    shoulders = [(axis, coincident)]
    if with_nonzero_sibling:
        shoulders.append((axis, coincident + 20.0))
    step = StepLevelFeature(
        Frame((0.0, 0.0, 0.0), "z"),
        base=0.0,
        levels=(10.0,),
        shoulders=tuple(shoulders),
        datum=datum,
    )
    model = PartModel(
        bbox=part.bounding_box(),
        orientation="prismatic",
        features=[step],
    )
    return part, step, model


@pytest.mark.parametrize("axis", ["x", "y"])
def test_the_compiler_omits_a_shoulder_coincident_with_its_datum(axis):
    _part, step, model = _declared_step(axis)

    plan = compile_dimensions(model)

    assert plan.ladder("step_position") is None
    omissions = [
        omission
        for omission in plan.diagnostics
        if omission.feature is step and omission.parameter_id == "step_position.length"
    ]
    assert len(omissions) == 1
    assert omissions[0].value == 0.0
    assert omissions[0].code == "step_position_coincident_with_datum"
    assert f"{axis.upper()} shoulder" in omissions[0].reason
    assert "coincident with its datum" in omissions[0].reason
    step_group = next(group for group in plan.groups if group.ref == FeatureRef(step))
    assert step_group.dim(role="step_position") is None


@pytest.mark.parametrize("axis", ["x", "y"])
def test_the_real_build_reports_the_omission_without_constructing_a_border(axis):
    part, step, _model = _declared_step(axis)

    # Exercise the issue's exact public reproducer shape, not only a prebuilt PartModel.
    drawing = build_drawing(part, model=[step])

    assert not [name for name in drawing.annotations() if name.startswith("dim_shoulder")]
    suppression = next(
        row for row in drawing.suppressions() if row["parameter_id"] == "step_position.length"
    )
    assert suppression["value"] == 0.0
    assert suppression["authored"] is False
    assert "coincident with its datum" in suppression["reason"]
    issue = next(
        issue for issue in drawing.lint() if issue.code == "step_position_coincident_with_datum"
    )
    assert issue.severity == "info"
    assert f"{axis.upper()} shoulder" in issue.message


@pytest.mark.parametrize("axis", ["x", "y"])
def test_only_the_degenerate_member_is_removed_from_a_mixed_shoulder_chain(axis):
    _part, step, model = _declared_step(axis, with_nonzero_sibling=True)

    plan = compile_dimensions(model)

    ladder = plan.ladder("step_position")
    assert ladder is not None
    assert [(rung.axis, rung.value) for rung in ladder.rungs] == [(axis, 20.0)]
    assert ladder.rungs[0].id is not None
    assert ladder.rungs[0].id.feature == step
    assert ladder.rungs[0].id.parameter == "step_position.length"
    assert [omission.value for omission in plan.diagnostics if omission.feature is step] == [0.0]
    step_group = next(group for group in plan.groups if group.ref == FeatureRef(step))
    assert [dim.value for dim in step_group.dims if dim.role == "step_position"] == [20.0]


@pytest.mark.parametrize(
    ("axis", "planned_views"),
    [("x", ("front", "side")), ("y", ("front", "plan"))],
)
def test_an_omitted_shoulder_does_not_require_its_directional_view(axis, planned_views):
    _part, _step, model = _declared_step(axis)

    groups = plan_dimensions(model, planned_views=planned_views)

    assert groups


def test_a_detected_non_degenerate_step_chain_is_unchanged():
    part = Box(90, 60, 10) + Pos(0, 0, 10) * Box(45, 60, 10)
    model = detect_part_model(part)
    assert any(isinstance(feature, StepLevelFeature) for feature in model.features)

    ladder = compile_dimensions(model).ladder("step_position")

    assert ladder is not None
    assert [(rung.axis, rung.value) for rung in ladder.rungs] == [("x", 22.5), ("x", 67.5)]
    drawing = build_drawing(part)
    assert len([name for name in drawing.annotations() if name.startswith("dim_shoulder")]) == 2
