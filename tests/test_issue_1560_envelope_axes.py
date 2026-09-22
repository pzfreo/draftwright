"""A missing envelope proposal cannot make body extents disappear silently."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from build123d import Align, Box, Cylinder, Rotation
from quiddity import TurnedProfile, TurnedProfileKey, TurnedStep

from draftwright import Drawing, Sheet, build_drawing
from draftwright.model.compiled import compile_dimensions
from draftwright.model.detect import build_part_model
from draftwright.model.ir import PartModel
from draftwright.sheet_emit import emit_sheet_script


def test_undimensioned_prismatic_axes_have_compiler_and_lint_outcomes():
    # The original bracket has this bounding box and only its synthesized
    # overall-height ladder. A sparse recognition-shaped model reproduces
    # that planning boundary without depending on the sheet-metal CAD importer.
    part = Box(70, 73.033, 36.933)
    model = PartModel(bbox=part.bounding_box(), orientation=None, features=[], detected=True)
    plan = compile_dimensions(model)
    assert [
        (ladder.kind, [rung.value_text for rung in ladder.rungs]) for ladder in plan.ladders
    ] == [("overall_height", ["36.9"])]
    missing = [o for o in plan.diagnostics if o.code == "overall_dim_withheld"]
    assert {(o.parameter_id, round(o.value, 3)) for o in missing} == {
        ("width.length", 70.0),
        ("depth.length", 73.033),
    }

    drawing = build_drawing(part, model=model)
    issues = [i for i in drawing.lint() if i.code == "overall_dim_withheld"]
    assert {i.message for i in issues} == {
        "overall X extent has no approved bounding measurement",
        "overall Y extent has no approved bounding measurement",
    }
    assert all(not issue.measurement_ids for issue in issues)
    completeness = drawing.lint_summary()["quality"]["completeness"]
    envelope_axes = completeness["envelope_axes"]
    assert envelope_axes["requirements"] == 3
    assert envelope_axes["placed"] == 1
    assert envelope_axes["suppressed"] == 2
    assert envelope_axes["score"] == 1 / 3

    source = emit_sheet_script(model, "part", "s", title="T", number="N", formats=("svg",))
    captured = {"part": part}
    with patch.object(Drawing, "export", lambda self, *a, **k: captured.setdefault("dwg", self)):
        exec(compile(source, "<generated-sheet>", "exec"), captured)  # noqa: S102
    assert [
        (issue.code, issue.severity)
        for issue in captured["dwg"].lint()
        if issue.code == "overall_dim_withheld"
    ] == [("overall_dim_withheld", "error")] * 2


def test_ordinary_box_and_round_stock_do_not_gain_false_envelope_withholdings():
    for part in (Box(70, 73.033, 36.933), Cylinder(10, 20)):
        drawing = build_drawing(part)
        assert not [i for i in drawing.lint() if i.code == "overall_dim_withheld"]
        envelope_axes = drawing.lint_summary()["quality"]["completeness"]["envelope_axes"]
        assert envelope_axes["requirements"] == envelope_axes["placed"] == 3
        assert envelope_axes["affected_count"] == 0


@pytest.mark.parametrize(("axis", "parameter_id"), (("x", "width.length"), ("y", "depth.length")))
def test_local_turned_profile_cannot_hide_a_longer_body_axis(axis, parameter_id):
    """Transverse OD coverage does not prove the profile owns the body's axial extent."""

    part = Box(100 if axis == "x" else 80, 100 if axis == "y" else 80, 80)
    bounds = part.bounding_box()
    key = TurnedProfileKey(
        axis,
        (0.0, 0.0, 0.0),
        (
            float(bounds.min.X),
            float(bounds.min.Y),
            float(bounds.min.Z),
            float(bounds.max.X),
            float(bounds.max.Y),
            float(bounds.max.Z),
        ),
    )
    profile = TurnedProfile.from_steps(
        (
            TurnedStep(axis, -9, -3, 80, key),
            TurnedStep(axis, -3, 9, 80, key),
        )
    )
    assert profile is not None and profile.profile == key

    model = build_part_model(part, profiles=(profile,))
    envelope = next(feature for feature in model.features if feature.kind == "envelope")
    assert any(parameter.parameter_id == parameter_id for parameter in envelope.parameters())

    plan = compile_dimensions(model)
    assert any(
        dimension.id.feature is envelope and dimension.id.parameter == parameter_id
        for group in plan.groups
        for dimension in group.dims
    )


def test_legacy_profile_without_axis_origin_cannot_hide_transverse_extents():
    """Public legacy profiles have axial spans but no evidence for their axis line."""

    part = Box(100, 100, 100)
    profile = TurnedProfile.from_steps(
        (
            TurnedStep("x", -50, 0, 80),
            TurnedStep("x", 0, 50, 60),
        )
    )
    assert profile is not None and profile.profile is None

    model = build_part_model(part, profiles=(profile,))
    assert any(feature.kind == "envelope" for feature in model.features)


def test_overlapping_step_spans_do_not_claim_an_overall_axis():
    """Two overlapping local lengths are not an end-to-end overall-length chain."""

    part = Rotation(0, 90, 0) * Cylinder(10, 60, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    sheet = Sheet(part, title="OVERLAP", number="1560-OVERLAP")
    sheet.authored_dimensions()
    left = sheet.step(
        diameter=20,
        length=40,
        at=(-10, 0, 0),
        axis="x",
        span=((-30, 0, 0), (10, 0, 0)),
        profile_group="one-body",
    )
    right = sheet.step(
        diameter=20,
        length=40,
        at=(10, 0, 0),
        axis="x",
        span=((-10, 0, 0), (30, 0, 0)),
        profile_group="one-body",
    )
    for step in (left, right):
        sheet.dimension(step, "step.length")
        sheet.dimension(step, "step.diameter")
    drawing = sheet.build()

    axes = drawing.lint_summary()["quality"]["completeness"]["envelope_axes"]
    assert axes["requirements"] == 3
    assert axes["placed"] == 2
    assert axes["missing"] == axes["affected_count"] == 1


def test_adjacent_legacy_steps_on_one_axis_line_convey_the_overall_axis():
    """Ungrouped declared steps use their shared physical axis line as chain identity."""

    part = Rotation(0, 90, 0) * Cylinder(10, 60, align=(Align.CENTER,) * 3)
    sheet = Sheet(part, title="ADJACENT", number="1560-ADJACENT")
    sheet.authored_dimensions()
    left = sheet.step(
        diameter=20,
        length=30,
        at=(-15, 0, 0),
        axis="x",
        span=((-30, 0, 0), (0, 0, 0)),
    )
    right = sheet.step(
        diameter=20,
        length=30,
        at=(15, 0, 0),
        axis="x",
        span=((0, 0, 0), (30, 0, 0)),
    )
    for step in (left, right):
        sheet.dimension(step, "step.length")
        sheet.dimension(step, "step.diameter")

    axes = sheet.build().lint_summary()["quality"]["completeness"]["envelope_axes"]
    assert axes["requirements"] == axes["placed"] == 3
    assert axes["affected_count"] == 0


def test_each_removed_envelope_axis_remains_in_the_physical_denominator():
    annotation_by_axis = {
        "x": "m_env_width",
        "y": "m_env_depth",
        "z": "dim_height",
    }
    for axis, annotation in annotation_by_axis.items():
        drawing = build_drawing(Box(70, 73.033, 36.933))
        baseline = drawing.lint_summary()["quality"]["completeness"]
        assert baseline["envelope_axes"]["requirements"] == 3
        assert baseline["envelope_axes"]["missing"] == 0

        drawing.remove(annotation)
        completeness = drawing.lint_summary()["quality"]["completeness"]
        assert completeness["requirements"] == baseline["requirements"]
        assert completeness["envelope_axes"]["requirements"] == 3
        assert completeness["envelope_axes"]["missing"] == 1, (
            f"removed {axis.upper()} envelope was not noticed"
        )
        assessment = drawing.report()["lint"]["assessment"]["axes"]["completeness"]
        assert assessment["status"] == "affected"
        assert assessment["affected_count"] == 1


def test_complete_authored_set_can_intentionally_omit_body_extents():
    part = Box(70, 73.033, 36.933)
    model = PartModel(bbox=part.bounding_box(), orientation=None, features=[], detected=True)
    authored = replace(model, authored_dimensions=())
    assert not [
        o for o in compile_dimensions(authored).diagnostics if o.code == "overall_dim_withheld"
    ]


def test_declared_inventory_can_intentionally_omit_body_extents():
    part = Box(70, 73.033, 36.933)
    model = PartModel(bbox=part.bounding_box(), orientation=None, features=[])
    assert not [
        o for o in compile_dimensions(model).diagnostics if o.code == "overall_dim_withheld"
    ]
