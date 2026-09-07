"""An angle is a named compiled measurement, including tolerances and omissions."""

from dataclasses import replace
from math import cos, radians, sin, sqrt

import pytest
from build123d import Polygon, extrude

from draftwright import Sheet, build_drawing
from draftwright.audit import compare_measurements
from draftwright.model import AngularReference, angle
from draftwright.model.compiled import compile_dimensions
from draftwright.sheet_emit import emit_sheet_script


@pytest.fixture(scope="module")
def angle_part():
    return extrude(Polygon((0, 0), (30, 0), (15, 15 * sqrt(3)), align=None), amount=3)


def _declared(part, tolerance=None, *, scale=1):
    sheet = Sheet(part, scale=scale)
    handle = sheet.angle(vertex=(0, 0, 3), first=(15, 0, 3), second=(7.5, 7.5 * sqrt(3), 3))
    if tolerance is not None:
        handle.tolerance(*tolerance, on="included.angle")
    sheet.dimension(handle, "included.angle")
    return sheet, handle


def _approved(model):
    return [entry for group in compile_dimensions(model).of_kind("angle") for entry in group.dims]


@pytest.mark.parametrize(
    ("tolerance", "label"),
    [
        (None, "60°"),
        ((0.05,), "60° ±0.05°"),
        ((0.0, 0.1), "60° +0.1° -0.0°"),
        ((0.000000123,), "60° ±0.000000123°"),
    ],
)
@pytest.mark.parametrize("scale", (None, 1))
def test_canonical_angle_and_complete_tolerance_reach_the_same_mark(
    angle_part, tolerance, label, scale
):
    sheet, handle = _declared(angle_part, tolerance, scale=scale)
    assert handle.dimension_ids() == ("included.angle",)
    (entry,) = _approved(sheet.model())
    assert entry.value == pytest.approx(60)
    assert entry.final_label == label and entry.angular_reference is not None
    drawing = sheet.build()
    marks = [
        (name, item)
        for name, item in drawing.iter_annotations()
        if hasattr(item, "measured_angle")
    ]
    assert len(marks) == 1 and marks[0][1].label == label
    (identity,) = drawing.registry.measurement_of(marks[0][0])
    assert identity.parameter == "included.angle" and identity.feature is entry.id.feature
    assert compare_measurements(drawing, drawing)["status"] == "preserved"


def test_omission_removes_angular_ink_and_reports_a_lost_named_measurement(angle_part):
    sheet, _ = _declared(angle_part)
    model = sheet.model()
    assert len(_approved(model)) == 1
    omitted = replace(model, authored_dimensions=())
    assert _approved(omitted) == []
    plan = compile_dimensions(omitted)
    assert any(
        item.parameter_id == "included.angle" and "authored" in item.reason
        for item in plan.diagnostics
    )
    before, after = [build_drawing(angle_part, model=value, scale=1) for value in (model, omitted)]
    comparison = compare_measurements(before, after)
    assert comparison["status"] == "changed"
    assert [item["parameter_id"] for item in comparison["lost"]] == ["included.angle"]
    assert not [item for _, item in after.iter_annotations() if hasattr(item, "measured_angle")]


def test_script_executes_with_exact_references_and_small_tolerances(angle_part, tmp_path):
    sheet, _ = _declared(angle_part, (0.000000123,))
    source = emit_sheet_script(
        sheet.model(), "part", str(tmp_path / "angle"), title="T", number="N", formats=("svg",)
    )
    namespace = {"part": angle_part}
    exec(source, namespace)
    (original,) = _approved(sheet.model())
    (replayed,) = _approved(namespace["sheet"].model())
    assert replayed.angular_reference == original.angular_reference
    assert replayed.final_label == original.final_label
    assert (tmp_path / "angle.svg").is_file()


def test_equal_valued_support_substitution_is_not_reported_preserved(angle_part):
    sheet, _ = _declared(angle_part)
    old = sheet.model()
    owner = old.features[0]
    reference = owner.angular_reference
    moved = replace(reference, vertex=(3, 0, 3), first=(18, 0, 3), second=(10.5, 7.5 * sqrt(3), 3))
    replacement = replace(owner, angular_reference=moved)
    new = replace(
        old,
        features=(replacement,),
        authored_dimensions=tuple(
            replace(request, feature=replacement) for request in old.authored_dimensions
        ),
    )
    assert _approved(old)[0].value == _approved(new)[0].value
    before, after = [build_drawing(angle_part, model=model, scale=1) for model in (old, new)]
    comparison = compare_measurements(before, after, feature_pairs=((owner, replacement),))
    assert comparison["status"] == "changed" and comparison["changed"]


def test_angular_options_cannot_choose_a_distorted_view_or_opposite_sector(angle_part):
    sheet, handle = _declared(angle_part)
    assert sheet.validate_dimension(handle, "included.angle", view="plan", side="right")[
        "supported"
    ]
    assert not sheet.validate_dimension(handle, "included.angle", view="front")["supported"]
    assert not sheet.validate_dimension(handle, "included.angle", side="left")["supported"]


def test_canonical_oblique_angle_is_refused_before_it_can_select_an_apparent_view():
    reference = AngularReference((0, 0, 0), (1, 1, 0), (0, 0, 1))
    assert reference.principal_axis == "?"
    with pytest.raises(ValueError, match="oblique is unsupported"):
        angle(vertex=reference.vertex, first=reference.first, second=reference.second)


@pytest.mark.parametrize(
    ("deferred", "pin", "priority"), ((False, True, 2), (True, True, 2), (True, False, 3))
)
def test_live_and_deferred_edits_preserve_the_compiled_angle_and_pin(
    angle_part, tmp_path, deferred, pin, priority
):
    sheet, _ = _declared(angle_part, (0.05,))
    drawing = build_drawing(
        angle_part, model=sheet.model(), scale=1, trace=True, out=str(tmp_path / "edit")
    )
    owner = drawing.model().features[0]
    before = drawing.measurement_snapshot()
    drawing.drop(owner)
    assert not drawing.annotations_of(owner)
    if deferred:
        with drawing.deferred():
            drawing.dimension(
                owner, "included.angle", name="edited_angle", pin=pin, priority=priority
            )
    else:
        assert (
            drawing.dimension(
                owner, "included.angle", name="edited_angle", pin=pin, priority=priority
            )
            == "edited_angle"
        )
    assert drawing.registry.is_pinned("edited_angle") is pin
    assert drawing.registry.named("edited_angle").label == "60° ±0.05°"
    assert compare_measurements(before, drawing)["status"] == "preserved"
    candidates = [
        candidate
        for solve in drawing.solve_trace.solves
        for candidate in solve["candidates"]
        if candidate["name"] == "edited_angle"
    ]
    assert candidates and all(candidate["anchored"] is pin for candidate in candidates)
    assert all(
        candidate["priority"] == (max(priority, 100) if pin else priority)
        for candidate in candidates
    )


def test_omitting_an_approved_angular_tolerance_is_not_a_confirmed_claim(angle_part):
    sheet, _ = _declared(angle_part, (0.05,))
    drawing = sheet.build()
    assert not [finding for finding in drawing.lint() if finding.code == "claimed_value_absent"]
    mark = drawing.registry.named("m_angle_0")
    assert mark.label == "60° ±0.05°"
    mark.label = "60°"
    assert mark.measured_angle == pytest.approx(60)
    assert [finding for finding in drawing.lint() if finding.code == "claimed_value_absent"]
    assert drawing.measurement_snapshot().unknown


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"param": "length"}, "included.angle"),
        ({"view": "front"}, "cannot render"),
        ({"side": "left"}, "cannot render"),
        ({"label": "120°"}, "unsupported angular edit controls"),
    ],
)
def test_angular_edits_refuse_wrong_measurements_views_and_raw_content(
    angle_part, options, message
):
    sheet, _ = _declared(angle_part)
    drawing = sheet.build()
    owner = drawing.model().features[0]
    arguments = {"param": "included.angle", **options}
    before = drawing.measurement_snapshot()
    with pytest.raises(ValueError, match=message):
        drawing.dimension(owner, **arguments)
    assert compare_measurements(before, drawing)["status"] == "preserved"


def test_an_equal_valued_foreign_angle_does_not_acquire_edit_authority(angle_part):
    sheet, _ = _declared(angle_part)
    drawing = sheet.build()
    owner = drawing.model().features[0]
    foreign = replace(owner)
    assert owner == foreign and owner is not foreign
    with pytest.raises(ValueError, match="exact feature"):
        drawing.dimension(foreign, "included.angle")


@pytest.mark.parametrize("tolerance", (-0.1, float("inf"), float("nan")))
def test_invalid_angular_tolerances_fail_before_ink(angle_part, tolerance):
    sheet, _ = _declared(angle_part, (tolerance,))
    with pytest.raises(ValueError, match="angular tolerance"):
        compile_dimensions(sheet.model())


def test_unplaceable_canonical_angle_reports_its_measurement_identity():
    theta = radians(0.25)
    part = extrude(
        Polygon((0, 0), (30, 0), (30 * cos(theta), 30 * sin(theta)), align=None), amount=3
    )
    assert min(part.bounding_box().size) > 0.1
    sheet = Sheet(part, scale=1, scale_policy="permissive")
    handle = sheet.angle(
        vertex=(0, 0, 3), first=(15, 0, 3), second=(15 * cos(theta), 15 * sin(theta), 3)
    )
    sheet.dimension(handle, "included.angle")
    assert len(_approved(sheet.model())) == 1
    drawing = sheet.build()
    findings = [
        finding
        for finding in drawing.lint(physical=False)
        if finding.code == "angular_dimension_dropped"
    ]
    assert findings and any(
        key.parameter == "included.angle" and key.feature is drawing.model().features[0]
        for finding in findings
        for key in finding.measurement_ids
    )
    assert not [item for _, item in drawing.iter_annotations() if hasattr(item, "measured_angle")]
