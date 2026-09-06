"""Current ownership, reporting and type boundaries for the oriented-slot adoption."""

from copy import copy
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest
from build123d import Box, Pos, Rot

from draftwright import build_drawing
from draftwright.linting.oriented_slot_coverage import oriented_slot_requirement_outcomes
from draftwright.oriented_slot_contract import (
    oriented_slot_provider_key,
    standalone_oriented_slots,
)
from draftwright.registry import AnnotationRegistry


def _part(points=((0, 0),)):
    part = Box(120, 90, 10)
    for x, y in points:
        part -= Pos(x, y, 0) * Rot(0, 0, 30) * Box(24, 6, 20)
    return part


@pytest.fixture(scope="module")
def drawing():
    result = build_drawing(_part())
    assert len(result.recognition().oriented_slots) == 1
    return result


def test_standalone_occurrence_and_requirements_reach_the_shared_report(drawing):
    ownership = drawing.recognition_ownership()
    evidence = ownership.evidence
    occurrence = next(f for f in evidence.features if evidence.family(f) == "oriented_slots")
    feature = next(f for f in drawing.model().features if f.kind == "oriented_slot")
    assert evidence.record(occurrence) is drawing.recognition().oriented_slots[0]
    assert ownership.binding_for(occurrence).feature is feature
    assert ownership.status(occurrence) == "represented"
    report = drawing.report()["recognition"]
    row = next(r for r in report["occurrences"] if r["family"] == "oriented_slots")
    assert row["disposition"] == "represented"
    requirements = [r for r in report["requirements"] if r["family"] == "oriented_slots"]
    assert len(requirements) == 2
    assert {r["parameter_id"] for r in requirements} == {
        "oriented_slot_width.length",
        "oriented_slot_length.length",
    }
    for requirement in requirements:
        assert requirement["occurrence_ids"] == [row["id"]]
        assert requirement["state"] == "placed"
        assert requirement["annotations"]


def test_exact_pattern_members_keep_the_deferred_policy_in_reports():
    drawing = build_drawing(_part(((-30, 0), (0, 0), (30, 0))))
    recognition = drawing.recognition()
    assert len(recognition.oriented_slots) == 3 and len(recognition.oriented_slot_patterns) == 1
    assert (
        standalone_oriented_slots(recognition.oriented_slots, recognition.oriented_slot_patterns)
        == ()
    )
    report = drawing.report()["recognition"]
    rows = [r for r in report["occurrences"] if r["family"] == "oriented_slots"]
    assert len(rows) == 3
    assert {r["disposition"] for r in rows} == {"deferred"}
    assert not [r for r in report["requirements"] if r["family"] == "oriented_slots"]
    pattern = recognition.oriented_slot_patterns[0]
    cloned = replace(pattern, slots=tuple(copy(slot) for slot in pattern.slots))
    assert cloned == pattern and all(
        a is not b for a, b in zip(cloned.slots, pattern.slots, strict=True)
    )
    with pytest.raises(ValueError, match="exact unclaimed"):
        standalone_oriented_slots(recognition.oriented_slots, (cloned,))


def test_repeated_same_record_is_not_an_additional_occurrence(drawing):
    source = drawing.recognition().oriented_slots[0]
    with pytest.raises(ValueError, match="repeats one record"):
        standalone_oriented_slots((source, source), ())


@pytest.mark.parametrize("kind", ["duck", "subclass"])
def test_nested_passage_type_is_exact_in_declaration_and_completeness(drawing, kind):
    feature = next(f for f in drawing.model().features if f.kind == "oriented_slot")
    values = {f.name: getattr(feature.passage, f.name) for f in fields(feature.passage)}
    fake_type = (
        SimpleNamespace
        if kind == "duck"
        else type("PassageSubclass", (type(feature.passage),), {})
    )
    fake = fake_type(**values)
    with pytest.raises(ValueError, match="OrientedSlotPassage"):
        replace(feature, passage=fake)
    corrupted = copy(feature)
    object.__setattr__(corrupted, "passage", fake)
    outcomes = oriented_slot_requirement_outcomes(
        drawing.recognition(), (corrupted,), AnnotationRegistry()
    )
    assert len(outcomes) == 2 and {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_numeric_subclasses_cannot_certify_provider_or_ir_fields(drawing):
    class FloatSubclass(float):
        pass

    source = drawing.recognition().oriented_slots[0]
    feature = next(f for f in drawing.model().features if f.kind == "oriented_slot")
    with pytest.raises(ValueError, match="finite real"):
        oriented_slot_provider_key(replace(source, width=FloatSubclass(source.width)))
    with pytest.raises(ValueError, match="finite and positive"):
        replace(feature, width=FloatSubclass(feature.width))
    corrupted = copy(feature)
    object.__setattr__(corrupted, "width", FloatSubclass(feature.width))
    outcomes = oriented_slot_requirement_outcomes(
        drawing.recognition(), (corrupted,), AnnotationRegistry()
    )
    assert len(outcomes) == 2 and {outcome.state for outcome in outcomes} == {"unverifiable"}


@pytest.mark.parametrize("framed", [False, True])
def test_arbitrary_three_axis_motion_preserves_physical_requirements(framed):
    moved = Pos(13, -27, 41) * Rot(17, 23, 31) * _part()
    drawing = build_drawing(moved, framed_recognition=framed)
    features = [f for f in drawing.model().features if f.kind == "oriented_slot"]
    assert len(features) == 1
    assert features[0].width == pytest.approx(6, abs=0.002)
    assert features[0].length == pytest.approx(24, abs=0.002)
    measurements = {
        key["parameter_id"]
        for name in drawing.annotations_of(features[0])
        for key in drawing.measurement_keys(name)
    }
    assert measurements == {"oriented_slot_width.length", "oriented_slot_length.length"}
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == 2


@pytest.mark.parametrize("points", [((0, 0),), ((-30, 0), (0, 0), (30, 0))])
def test_build_and_repeated_consumers_share_one_aggregate(points):
    from conftest import recognition_consumer_calls

    with recognition_consumer_calls() as counts:
        drawing = build_drawing(_part(points))
        assert len(drawing.recognition().oriented_slots) == len(points)
        drawing.lint()
        drawing.report()
        drawing.lint_summary()
        drawing.report()
    assert counts == {"build_recognition_evidence": 1}


@pytest.mark.parametrize("points", [((0, 0),), ((-30, 0), (0, 0), (30, 0))])
def test_supplied_generator_keeps_the_same_slot_inventory_as_a_tuple(points):
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.model.detect import build_part_model

    part = _part(points)
    source = build_raw_recognition_result(part)
    assert len(source.oriented_slots) == len(points)
    expected = build_part_model(part, oriented_slots=source.oriented_slots)
    expected_slots = [f for f in expected.features if f.kind == "oriented_slot"]
    assert len(expected_slots) == (1 if len(points) == 1 else 0)
    consumed = []

    def records():
        for record in source.oriented_slots:
            consumed.append(record)
            yield record

    actual = build_part_model(part, oriented_slots=records())
    assert [f for f in actual.features if f.kind == "oriented_slot"] == expected_slots
    assert len(consumed) == len(points)
    assert all(a is b for a, b in zip(consumed, source.oriented_slots, strict=True))


@pytest.mark.parametrize(
    "field",
    [
        "low_capped",
        "high_capped",
        "interval",
        "boundary",
        "vertex",
        "point",
        "body_key",
        "run_direction",
        "frame",
        "axis",
    ],
)
def test_malformed_nested_ir_cannot_certify_a_physical_requirement(drawing, field):
    feature = next(f for f in drawing.model().features if f.kind == "oriented_slot")
    control = oriented_slot_requirement_outcomes(
        drawing.recognition(), (feature,), drawing.registry
    )
    assert [o.state for o in control] == ["placed", "placed"]
    corrupted = copy(feature)
    passage = copy(feature.passage)
    object.__setattr__(corrupted, "passage", passage)
    if field in ("low_capped", "high_capped"):
        object.__setattr__(passage, field, 0)
    elif field == "interval":
        object.__setattr__(passage, "run_interval", list(passage.run_interval))
    elif field == "boundary":
        object.__setattr__(passage, "boundary", list(passage.boundary))
    elif field == "vertex":
        object.__setattr__(passage, "boundary", tuple(list(v) for v in passage.boundary))
    elif field == "point":
        object.__setattr__(passage, "boundary", tuple(((*p, 99), b) for p, b in passage.boundary))
    elif field == "body_key":
        assert passage.body_key is not None
        object.__setattr__(passage, "body_key", list(passage.body_key))
    elif field == "run_direction":
        object.__setattr__(corrupted, "run_direction", tuple(-v for v in feature.run_direction))
    elif field == "frame":
        object.__setattr__(
            corrupted,
            "frame",
            SimpleNamespace(origin=feature.frame.origin, axis=feature.frame.axis),
        )
    else:

        class AxisString(str):
            pass

        frame = copy(feature.frame)
        object.__setattr__(frame, "axis", AxisString(frame.axis))
        object.__setattr__(corrupted, "frame", frame)
    outcomes = oriented_slot_requirement_outcomes(
        drawing.recognition(), (corrupted,), drawing.registry
    )
    assert [o.state for o in outcomes] == ["unverifiable", "unverifiable"]


@pytest.mark.parametrize("translation", [(0, 0, 0), (1000, 1000, 1000)])
def test_released_rounded_frame_vectors_remain_valid_at_45_degrees(translation):
    from math import hypot

    from b123d_recognisers import PassageFrame, build_raw_recognition_result

    part = Pos(*translation) * Rot(45, 45, 45) * _part()
    result = build_raw_recognition_result(part)
    assert len(result.oriented_slots) == 1
    frame = result.oriented_slots[0].source.frame
    # The public value type accepts these rounded unit vectors. Their squared length
    # differs from one by more than 1e-6, so using that as a length bound rejects them.
    assert PassageFrame(frame.origin, frame.run, frame.u, frame.v) == frame
    assert abs(sum(v * v for v in frame.u) - 1) > 1e-6
    assert abs(hypot(*frame.u) - 1) < 1e-6
    drawing = build_drawing(part)
    feature = next(f for f in drawing.model().features if f.kind == "oriented_slot")
    assert (feature.width, feature.length) == pytest.approx((6, 24), abs=0.002)
    assert len(drawing.annotations_of(feature)) == 1
    assert drawing.lint_summary()["quality"]["completeness"]["placed"] == 2
