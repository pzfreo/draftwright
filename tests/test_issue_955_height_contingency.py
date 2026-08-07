"""#955: a dropped Z-turned step chain releases a compiler-approved overall height."""

from __future__ import annotations

from dataclasses import replace

import pytest
from build123d import Align, Box, Cylinder, Pos

from draftwright import Sheet, build_drawing
from draftwright.builder import detect_part_model
from draftwright.model.compiled import (
    ApprovedContingency,
    ApprovedLadder,
    Omission,
    RenderableDimensionPlan,
    compile_dimensions,
)
from draftwright.model.ir import EnvelopeFeature, Frame, RequestedDimension, StepFeature
from draftwright.model.planner import plan_dimensions


def _crowded_grooved_shaft():
    shaft = Cylinder(12, 60, align=(Align.CENTER, Align.CENTER, Align.MIN))
    shaft -= Pos(0, 0, 30) * (
        Cylinder(12, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
        - Cylinder(9, 4, align=(Align.CENTER, Align.CENTER, Align.MIN))
    )
    return shaft


def _height_rows(plan):
    return [o for o in plan.diagnostics if o.parameter_id == "height.length"]


def _envelope_for(model):
    bb = model.bbox
    center = bb.center()
    return EnvelopeFeature(
        Frame((center.X, center.Y, center.Z), "z"),
        width=float(bb.size.X),
        height=float(bb.size.Z),
        depth=float(bb.size.Y),
        bbox_min=(float(bb.min.X), float(bb.min.Y), float(bb.min.Z)),
        bbox_max=(float(bb.max.X), float(bb.max.Y), float(bb.max.Z)),
    )


def test_the_compiler_owns_the_z_turned_height_contingency():
    plan = compile_dimensions(detect_part_model(_crowded_grooved_shaft()))

    assert plan.ladder("overall_height") is None
    contingency = plan.contingency("step_length")
    assert contingency is not None
    assert contingency.fallback.kind == "overall_height"
    assert contingency.fallback.rungs[0].value == pytest.approx(60)
    assert [o.reason for o in _height_rows(plan)] == ["Z-turned (the step chain tiles the height)"]

    released = plan.release_contingency("step_length")
    assert released.ladder("overall_height") == contingency.fallback
    assert _height_rows(released) == []


def test_an_unregistered_contingency_kind_fails_closed():
    inactive = Omission(None, "height.length", 1.0, "inactive")
    plan = RenderableDimensionPlan(
        contingencies=(
            ApprovedContingency(
                "primary",
                ApprovedLadder("unregistered", ()),
                inactive,
            ),
        )
    )

    with pytest.raises(KeyError, match="unregistered"):
        plan.addressable()


def test_an_authored_omission_never_becomes_a_contingency():
    model = detect_part_model(_crowded_grooved_shaft())
    envelope = _envelope_for(model)
    steps = [feature for feature in model.features if isinstance(feature, StepFeature)]
    model = replace(
        model,
        features=[*model.features, envelope],
        authored_dimensions=tuple(RequestedDimension(step, "step.length") for step in steps),
    )

    plan = compile_dimensions(model)

    assert len(plan.of_kind("step")) == 2
    assert all(group.dim(kind="length") is not None for group in plan.of_kind("step"))
    assert plan.contingency("step_length") is None
    assert plan.ladder("overall_height") is None
    assert len(_height_rows(plan)) == 1
    assert _height_rows(plan)[0].authored


def test_z_turned_without_an_approved_chain_gets_the_height_directly():
    model = detect_part_model(_crowded_grooved_shaft())
    model = replace(
        model,
        features=[feature for feature in model.features if not isinstance(feature, StepFeature)]
        + [_envelope_for(model)],
    )

    plan = compile_dimensions(model)

    assert plan.contingency("step_length") is None
    overall = plan.ladder("overall_height")
    assert overall is not None
    assert overall.rungs[0].value == pytest.approx(60)
    assert _height_rows(plan) == []


def test_an_unrelated_planner_suppression_still_withholds_height():
    model = detect_part_model(Box(40, 30, 20))
    groups = []
    for group in plan_dimensions(model):
        units = tuple(
            replace(
                unit,
                members=tuple(
                    replace(pd, suppressed=True, reason="test policy")
                    if pd.param.parameter_id == "height.length"
                    else pd
                    for pd in unit.members
                ),
            )
            for unit in group.units
        )
        groups.append(replace(group, units=units))

    plan = compile_dimensions(model, groups=groups)

    assert plan.ladder("overall_height") is None
    assert plan.contingency("step_length") is None
    assert [o.reason for o in _height_rows(plan)] == ["test policy"]


@pytest.mark.timeout(120)
def test_a_dropped_chain_releases_the_height_and_clears_the_planner_omission():
    drawing = build_drawing(_crowded_grooved_shaft(), number="X")
    labels = {
        label
        for _, annotation in drawing.iter_annotations()
        if (label := getattr(annotation, "label", None))
    }
    codes = drawing.lint_summary()["by_code"]

    assert "60" in labels
    assert "dim_height" in drawing.annotations()
    assert codes.get("axial_length_missing", 0) == 0
    assert codes.get("step_dim_dropped") == 1
    assert not [row for row in drawing.suppressions() if row["parameter_id"] == "height.length"]


@pytest.mark.timeout(120)
def test_a_surviving_z_chain_does_not_release_a_redundant_height():
    drawing = build_drawing(
        Cylinder(12, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))
        + Pos(0, 0, 30) * Cylinder(8, 30, align=(Align.CENTER, Align.CENTER, Align.MIN))
    )

    assert any(name.startswith("m_steplen") for name in drawing.annotations())
    assert "dim_height" not in drawing.annotations()
    assert [
        row["reason"] for row in drawing.suppressions() if row["parameter_id"] == "height.length"
    ] == ["Z-turned (the step chain tiles the height)"]


@pytest.mark.timeout(120)
def test_an_authored_overall_height_does_not_claim_the_shoulders_are_located():
    sheet = Sheet.from_part(_crowded_grooved_shaft())
    envelope = sheet.envelope()
    sheet.dimension(envelope, "height.length")

    drawing = sheet.build()
    codes = drawing.lint_summary()["by_code"]

    assert "dim_height" in drawing.annotations()
    assert codes.get("step_dim_dropped", 0) == 0
    assert codes.get("axial_length_missing") == 1


@pytest.mark.timeout(120)
def test_an_unplaceable_fallback_is_a_drop_not_a_stale_suppression():
    drawing = build_drawing(_crowded_grooved_shaft(), page="90x70", scale=4.0, repair=False)
    codes = drawing.lint_summary()["by_code"]

    assert "dim_height" not in drawing.annotations()
    assert codes.get("placement_unsatisfiable", 0) >= 1
    assert codes.get("axial_length_missing") == 1
    assert not [row for row in drawing.suppressions() if row["parameter_id"] == "height.length"]
