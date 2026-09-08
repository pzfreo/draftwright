"""A quantity angle retains every independently editable corner measurement."""

from dataclasses import replace
from math import sqrt

import pytest
from build123d import Polygon, extrude

from draftwright import Sheet
from draftwright.linting.angular import profile_angle_requirement_outcomes
from draftwright.linting.evidence import verify_measurement_claims
from draftwright.model import AngularReference, angle_pattern
from draftwright.model.compiled import compile_dimensions
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import emit_sheet_script


@pytest.fixture(scope="module")
def triangle():
    points = ((0, 0, 3), (30, 0, 3), (15, 15 * sqrt(3), 3))
    references = tuple(
        AngularReference(point, points[(i + 1) % 3], points[(i + 2) % 3], sector="opposite")
        for i, point in enumerate(points)
    )
    part = extrude(Polygon(*(point[:2] for point in points), align=None), amount=3)
    return part, references


def _sheet(triangle, *, omitted=None, tolerance=None):
    part, references = triangle
    sheet = Sheet(part, page="A2", scale=1)
    handle = sheet.angle_pattern(*references)
    if tolerance:
        handle.tolerance(0.05, on=tolerance)
    for parameter in handle.dimension_ids():
        if parameter != omitted:
            sheet.dimension(handle, parameter)
    return sheet, handle


def test_pattern_members_keep_referential_values_and_distinct_ids(triangle):
    _, references = triangle
    pattern = angle_pattern(*references)
    parameters = pattern.parameters()
    assert [p.parameter_id for p in parameters] == [
        "included.angle.member1",
        "included.angle.member2",
        "included.angle.member3",
    ]
    assert [p.value for p in parameters] == pytest.approx([60, 60, 60])
    assert tuple(p.angular_reference for p in parameters) == references


@pytest.mark.parametrize("case", ("singleton", "duplicate", "different-angle", "different-plane"))
def test_pattern_rejects_incoherent_members(triangle, case):
    _, references = triangle
    if case == "singleton":
        references = references[:1]
    elif case == "duplicate":
        references = (references[0], references[0])
    elif case == "different-angle":
        references = (references[0], replace(references[1], second=(15, 15, 3)))
    else:
        moved = references[1]
        references = (
            references[0],
            replace(
                moved,
                **{
                    field: (getattr(moved, field)[0], getattr(moved, field)[1], 5)
                    for field in ("vertex", "first", "second")
                },
            ),
        )
    with pytest.raises(ValueError):
        angle_pattern(*references)


@pytest.mark.parametrize("case", ("complete", "uniform-tolerance", "member-tolerance", "omitted"))
def test_quantity_approval_never_hides_a_member_omission_or_tolerance(triangle, case):
    sheet, _ = _sheet(
        triangle,
        omitted="included.angle.member2" if case == "omitted" else None,
        tolerance={
            "uniform-tolerance": "included",
            "member-tolerance": "included.angle.member2",
        }.get(case),
    )
    plan = compile_dimensions(sheet.model())
    (group,) = plan.of_kind("angle")
    expected = {"complete": "3× 60°", "uniform-tolerance": "3× 60° ±0.05°"}.get(case)
    assert group.shared_label == expected
    assert len(group.dims) == (2 if case == "omitted" else 3)
    if case == "omitted":
        assert [p.id.parameter for p in group.dims] == [
            "included.angle.member1",
            "included.angle.member3",
        ]
    if case == "member-tolerance":
        assert [p.final_label for p in group.dims] == ["60°", "60° ±0.05°", "60°"]
    drawing = sheet.build()
    marks = [
        (n, item) for n, item in drawing.iter_annotations() if hasattr(item, "measured_angle")
    ]
    assert len(marks) == (1 if expected else len(group.dims))
    assert sorted(
        identity.parameter
        for name, _ in marks
        for identity in drawing.registry.measurement_of(name)
    ) == sorted(p.id.parameter for p in group.dims)
    assert not [
        finding
        for finding in drawing.lint(physical=True)
        if finding.code
        in {"claimed_value_absent", "angular_label_vs_geometry", "angular_geometry_mismatch"}
        or finding.code.startswith("angular_support_")
    ]
    outcomes = profile_angle_requirement_outcomes(
        drawing.recognition_evidence(),
        None,
        drawing.model().features,
        drawing.registry,
        plan.diagnostics,
    )
    assert len(outcomes) == 3
    assert sorted(outcome.state for outcome in outcomes) == (
        ["placed", "placed", "suppressed"] if case == "omitted" else ["placed"] * 3
    )


@pytest.mark.parametrize("deferred", (False, True))
def test_individual_pattern_member_can_be_replaced_through_the_shared_edit_path(
    triangle, deferred
):
    sheet, _ = _sheet(triangle, tolerance="included.angle.member2")
    drawing = sheet.build()
    owner = drawing.model().features[0]
    member = "included.angle.member2"
    existing = next(
        name
        for name in drawing.annotations_of(owner)
        if any(identity.parameter == member for identity in drawing.registry.measurement_of(name))
    )
    drawing.remove(existing)
    if deferred:
        with drawing.deferred():
            drawing.dimension(owner, member, name="edited_member", priority=2)
    else:
        assert (
            drawing.dimension(owner, member, name="edited_member", priority=2) == "edited_member"
        )
    mark = drawing.registry.named("edited_member")
    assert mark.label == "60° ±0.05°"
    (identity,) = drawing.registry.measurement_of("edited_member")
    assert identity.feature is owner and identity.parameter == member
    outcomes = verify_measurement_claims(drawing.registry, compile_dimensions(drawing.model()))
    assert outcomes and all(outcome.state == "confirmed" for outcome in outcomes)


def test_pattern_member_side_preferences_split_the_quantity_without_retargeting(triangle):
    sheet = Sheet(triangle[0], page="A2", scale=1)
    handle = sheet.angle_pattern(*triangle[1])
    sides = ("left", "right", "above")
    for parameter, side in zip(handle.dimension_ids(), sides, strict=True):
        sheet.dimension(handle, parameter, side=side)
    (group,) = compile_dimensions(sheet.model()).of_kind("angle")
    assert group.shared_label is None
    assert tuple(dim.side for dim in group.dims) == sides
    drawing = sheet.build()
    assert len(drawing.annotations_of(drawing.model().features[0])) == 3


@pytest.fixture(scope="module")
def built_pattern(triangle):
    sheet, _ = _sheet(triangle)
    return sheet, sheet.build()


@pytest.mark.parametrize(
    "corruption", ("quantity", "nominal", "missing-member", "duplicate-member")
)
def test_quantity_claim_requires_the_complete_approved_roster(built_pattern, corruption):
    sheet, drawing = built_pattern
    ((name, mark),) = [
        (n, item) for n, item in drawing.iter_annotations() if hasattr(item, "measured_angle")
    ]
    plan = compile_dimensions(sheet.model())
    claims = drawing.registry.measurement_of(name)
    assert len(claims) == 3
    original = mark.label
    registry = AnnotationRegistry()
    try:
        if corruption == "quantity":
            mark.label = "4× 60°"
        elif corruption == "nominal":
            mark.label = "3× 99°"
        elif corruption == "missing-member":
            claims = claims[:2]
        else:
            claims = (claims[0], claims[0], claims[2])
        registry.add(mark, name, "plan", measurement=claims)
        outcomes = verify_measurement_claims(registry, plan)
        assert outcomes and all(outcome.state == "value_absent" for outcome in outcomes)
    finally:
        mark.label = original


def test_pattern_round_trips_all_members_and_member_specific_tolerance(triangle, tmp_path):
    sheet, _ = _sheet(triangle, tolerance="included.angle.member2")
    source = emit_sheet_script(
        sheet.model(), "part", str(tmp_path / "pattern"), title="T", number="N", formats=("svg",)
    )
    namespace = {"part": triangle[0]}
    exec(source, namespace)
    original = compile_dimensions(sheet.model()).of_kind("angle")[0]
    replayed = compile_dimensions(namespace["sheet"].model()).of_kind("angle")[0]
    assert [(d.id.parameter, d.angular_reference, d.final_label) for d in replayed.dims] == [
        (d.id.parameter, d.angular_reference, d.final_label) for d in original.dims
    ]
