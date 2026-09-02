"""Standalone free-direction oriented-slot consumer semantics (#1432)."""

from __future__ import annotations

import math
from copy import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from b123d_recognisers import (
    OrientedSlot,
    OrientedSlotArray,
    OrientedSlotGrid,
    recognise_oriented_slot_patterns,
    recognise_oriented_slots,
)
from build123d import Align, Axis, Box, Pos, Rot

from draftwright import Sheet, build_drawing
from draftwright.feature_identity import register_oriented_slot_feature_type
from draftwright.linting import LintIssue
from draftwright.linting.oriented_slot_coverage import oriented_slot_requirement_outcomes
from draftwright.model import Frame, PartModel, oriented_slot
from draftwright.model.detect import _CONVERTERS, build_part_model
from draftwright.model.planner import _parameter_view_preferences, plan_dimensions
from draftwright.oriented_slot_contract import oriented_slot_provider_key
from draftwright.registry import AnnotationRegistry
from draftwright.sheet_emit import _feature_line, emit_sheet_script

_AUTHORED_CASES = ((-35.0, -18.0, 17.0), (8.0, 13.0, 43.0), (31.0, -7.0, 77.0))


def _part(points=((0, 0),)):
    part = Box(120, 90, 10)
    for x, y in points:
        part -= (
            Pos(x, y, 0)
            * Rot(0, 0, 30)
            * Box(24, 6, 20, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        )
    return part


def _rotated_part(angle, *, center=(0.0, 0.0)):
    return Box(120, 90, 10) - (
        Pos(*center, 0)
        * Rot(0, 0, angle)
        * Box(24, 6, 20, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    )


def _authored_corpus_part():
    part = Box(140, 100, 10)
    for x, y, angle in _AUTHORED_CASES:
        part -= (
            Pos(x, y, 0)
            * Rot(0, 0, angle)
            * Box(24, 6, 20, align=(Align.CENTER, Align.CENTER, Align.CENTER))
        )
    return part


def _assert_authored_corpus_model(model) -> None:
    features = sorted(
        (feature for feature in model.features if feature.kind == "oriented_slot"),
        key=lambda feature: feature.frame.origin[0],
    )
    assert len(features) == 3, "the authored corpus contains three independent physical slots"
    for feature, (x, y, _angle) in zip(features, _AUTHORED_CASES, strict=True):
        assert feature.frame.origin == pytest.approx((x, y, 0.0), abs=1e-6)
        assert feature.width == pytest.approx(6.0, abs=0.002)
        assert feature.length == pytest.approx(24.0, abs=0.002)
        assert [parameter.parameter_id for parameter in feature.parameters()] == [
            "oriented_slot_width.length",
            "oriented_slot_length.length",
        ]


def _feature(part=None):
    model = build_part_model(_part() if part is None else part)
    return next(feature for feature in model.features if feature.kind == "oriented_slot")


def _kwargs(feature):
    passage = feature.passage
    return {
        "width": feature.width,
        "length": feature.length,
        "center": feature.frame.origin,
        "width_direction": feature.width_direction,
        "long_direction": feature.long_direction,
        "run_direction": feature.run_direction,
        "source_origin": passage.origin,
        "source_u": passage.u,
        "source_v": passage.v,
        "run_interval": passage.run_interval,
        "source_boundary": passage.boundary,
        "low_capped": passage.low_capped,
        "high_capped": passage.high_capped,
        "body_key": passage.body_key,
    }


def _rectangular_boundary(width, length, width_direction, long_direction):
    return tuple(
        (
            (
                long_sign * length / 2 * long_direction[0]
                + width_sign * width / 2 * width_direction[0],
                long_sign * length / 2 * long_direction[1]
                + width_sign * width / 2 * width_direction[1],
            ),
            0.0,
        )
        for long_sign, width_sign in ((-1, 1), (-1, -1), (1, -1), (1, 1))
    )


def _requirement_codes(drawing):
    return [
        issue.code
        for issue in drawing.lint()
        if issue.code.startswith("oriented_slot_requirement_")
    ]


def test_public_aggregate_lowers_to_a_dedicated_lossless_ir() -> None:
    feature = _feature()

    assert feature == oriented_slot(**_kwargs(feature))
    assert feature.width == 6.0
    assert feature.length == 23.999
    assert feature.width_direction == (-0.500011, 0.866019, 0.0)
    assert feature.long_direction == (0.866019, 0.500011, 0.0)
    assert feature.run_direction == feature.passage.run == (0.0, 0.0, 1.0)
    assert [parameter.parameter_id for parameter in feature.parameters()] == [
        "oriented_slot_width.length",
        "oriented_slot_length.length",
    ]


def test_sheet_word_and_generated_line_replay_the_exact_feature() -> None:
    detected = _feature()
    sheet = Sheet(_part()).authored_dimensions()
    sheet.oriented_slot(**_kwargs(detected))
    assert sheet.model().features == [detected]

    line = _feature_line(detected)
    assert line.startswith("sheet.oriented_slot(")
    generated = type("GeneratedSheet", (), {"oriented_slot": staticmethod(oriented_slot)})()
    assert eval(line, {"__builtins__": {}}, {"sheet": generated}) == detected  # noqa: S307


def test_generated_sheet_losslessly_replays_high_precision_authored_values() -> None:
    angle = math.radians(30.123456789)
    width = 6.123456789
    length = 24.123456789
    width_direction = (-math.sin(angle), math.cos(angle), 0.0)
    long_direction = (math.cos(angle), math.sin(angle), 0.0)
    kwargs = {
        **_kwargs(_feature()),
        "width": width,
        "length": length,
        "width_direction": width_direction,
        "long_direction": long_direction,
        "source_boundary": _rectangular_boundary(width, length, width_direction, long_direction),
    }
    expected = oriented_slot(**kwargs)
    sheet = Sheet(_part()).authored_dimensions()
    sheet.oriented_slot(**kwargs)

    source = emit_sheet_script(sheet.model(), "part", "s", title="T", number="N")
    namespace = {"part": _part()}
    exec(compile(source, "<precise-oriented-slot>", "exec"), namespace)  # noqa: S102

    rebuilt = next(
        feature
        for feature in namespace["drawing"].model().features
        if feature.kind == "oriented_slot"
    )
    assert rebuilt == expected


def test_generated_line_executes_with_the_public_empty_body_key_default() -> None:
    kwargs = _kwargs(_feature())
    kwargs.pop("body_key")
    expected = oriented_slot(**kwargs)
    line = _feature_line(expected)

    compile(line, "<empty-oriented-slot-body-key>", "eval")
    generated = type("GeneratedSheet", (), {"oriented_slot": staticmethod(oriented_slot)})()

    assert eval(line, {"__builtins__": {}}, {"sheet": generated}) == expected  # noqa: S307


def test_authored_roles_tolerance_and_executed_generated_sheet_round_trip() -> None:
    part = _part()
    sheet = Sheet(part)
    handle = sheet.oriented_slot(**_kwargs(_feature()))
    assert handle.dimension_ids() == (
        "oriented_slot_length.length",
        "oriented_slot_width.length",
    )
    handle.tolerance(0.1, on="width")
    sheet.authored_dimensions()
    sheet.dimension(handle, "oriented_slot_width.length")
    sheet.dimension(handle, "oriented_slot_length.length")
    direct = sheet.build()

    source = emit_sheet_script(sheet.model(), "part", "s", title="T", number="N")
    namespace = {"part": part}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<oriented-slot>", "exec"),
        namespace,
    )
    regenerated = namespace["sheet"].build()

    expected = {"ORIENTED SLOT 6 ±0.1 WIDE × 24.0 LONG"}
    assert {
        annotation.label for annotation in direct.annotations_of(_feature()).values()
    } == expected
    rebuilt_feature = next(
        feature for feature in regenerated.model().features if feature.kind == "oriented_slot"
    )
    assert {
        annotation.label for annotation in regenerated.annotations_of(rebuilt_feature).values()
    } == expected


@pytest.mark.parametrize(
    ("parameter_id", "expected"),
    [
        ("oriented_slot_width.length", "ORIENTED SLOT 6 WIDE"),
        ("oriented_slot_length.length", "ORIENTED SLOT 24.0 LONG"),
    ],
)
def test_authored_subsets_name_the_remaining_value(parameter_id, expected) -> None:
    sheet = Sheet(_part())
    handle = sheet.oriented_slot(**_kwargs(_feature()))
    sheet.authored_dimensions().dimension(handle, parameter_id)
    drawing = sheet.build()
    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")

    assert {annotation.label for annotation in drawing.annotations_of(feature).values()} == {
        expected
    }


def _point_segment_distance(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    fraction = (
        0.0
        if not length_squared
        else max(
            0.0,
            min(
                1.0,
                ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared,
            ),
        )
    )
    nearest = (start[0] + fraction * dx, start[1] + fraction * dy)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


@pytest.mark.parametrize("angle", [17.0, 43.0, 77.0])
@pytest.mark.parametrize(
    "parameter_id",
    [None, "oriented_slot_width.length", "oriented_slot_length.length"],
    ids=("automatic", "width-only", "length-only"),
)
def test_every_callout_tip_lands_on_the_physical_rotated_rim(angle, parameter_id) -> None:
    center = (7.0, -9.0)
    part = _rotated_part(angle, center=center)
    if parameter_id is None:
        drawing = build_drawing(part)
    else:
        detected = _feature(part)
        sheet = Sheet(part)
        handle = sheet.oriented_slot(**_kwargs(detected))
        sheet.authored_dimensions().dimension(handle, parameter_id)
        drawing = sheet.build()
    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
    annotation = next(iter(drawing.annotations_of(feature).values()))

    radians = math.radians(angle)
    long_direction = (math.cos(radians), math.sin(radians))
    width_direction = (-math.sin(radians), math.cos(radians))
    corners = tuple(
        drawing.at(
            "plan",
            center[0] + long_sign * 12 * long_direction[0] + width_sign * 3 * width_direction[0],
            center[1] + long_sign * 12 * long_direction[1] + width_sign * 3 * width_direction[1],
            0.0,
        )[:2]
        for long_sign, width_sign in ((-1, -1), (-1, 1), (1, 1), (1, -1))
    )
    distance = min(
        _point_segment_distance(annotation.tip[:2], start, end)
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True)
    )
    assert distance < 0.01
    assert annotation.tip[:2] != pytest.approx(drawing.at("plan", *feature.frame.origin)[:2])


def test_drawing_places_one_solver_owned_two_requirement_callout() -> None:
    drawing = build_drawing(_part())
    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
    annotations = drawing.annotations_of(feature)

    assert len(annotations) == 1
    name, annotation = next(iter(annotations.items()))
    assert name.startswith("m_oriented_slot_")
    assert annotation.label == "ORIENTED SLOT 6 WIDE × 24.0 LONG"
    assert {key["parameter_id"] for key in drawing.measurement_keys(name)} == {
        "oriented_slot_width.length",
        "oriented_slot_length.length",
    }
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == completeness["placed"] == 2
    assert completeness["audited_score"] == 1.0
    assert completeness["by_family"]["oriented_slots"] == 2


def test_pattern_members_remain_exclusively_deferred_to_the_pattern_family() -> None:
    drawing = build_drawing(_part(((-30, 0), (0, 0), (30, 0))))

    assert not [feature for feature in drawing.model().features if "slot" in feature.kind]
    assert not [name for name in drawing.annotations() if "oriented_slot" in name]
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == 0
    assert completeness["unscored_recognized_families"] == ["oriented_slot_patterns"]


def test_raw_and_framed_rigid_motion_preserve_sizes_and_outcomes() -> None:
    raw = build_drawing(_part())
    moved = _part().rotate(Axis.X, 23).rotate(Axis.Z, 31)
    framed = build_drawing(moved, framed_recognition=True)

    raw_feature = next(item for item in raw.model().features if item.kind == "oriented_slot")
    framed_feature = next(item for item in framed.model().features if item.kind == "oriented_slot")
    assert (raw_feature.width, raw_feature.length) == (
        framed_feature.width,
        framed_feature.length,
    )
    assert {annotation.label for annotation in raw.annotations_of(raw_feature).values()} == {
        "ORIENTED SLOT 6 WIDE × 24.0 LONG"
    }
    assert {annotation.label for annotation in framed.annotations_of(framed_feature).values()} == {
        "ORIENTED SLOT 6 WIDE × 24.0 LONG"
    }


def test_raw_moved_leader_targets_a_section_inside_the_finite_passage() -> None:
    moved = Pos(10, 20, 30) * _part().rotate(Axis.X, 23).rotate(Axis.Z, 31)
    drawing = build_drawing(moved)
    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
    annotation = next(iter(drawing.annotations_of(feature).values()))
    passage = feature.passage
    view = {"x": "side", "y": "front", "z": "plan"}[feature.frame.axis]

    def projected_polygon(station):
        origin = tuple(passage.origin[index] + station * passage.run[index] for index in range(3))
        return tuple(
            drawing.at(
                view,
                *(
                    origin[index] + point[0] * passage.u[index] + point[1] * passage.v[index]
                    for index in range(3)
                ),
            )[:2]
            for point, _bulge in passage.boundary
        )

    physical = projected_polygon(0.5 * sum(passage.run_interval))
    gauge_continuation = projected_polygon(0.0)

    def rim_distance(polygon):
        return min(
            _point_segment_distance(annotation.tip[:2], start, end)
            for start, end in zip(polygon, polygon[1:] + polygon[:1], strict=True)
        )

    assert rim_distance(physical) < 1e-6
    assert rim_distance(gauge_continuation) > 0.1


def test_source_inventory_independently_sets_two_requirements_per_slot() -> None:
    drawing = build_drawing(_authored_corpus_part())
    recognition = drawing.recognition()

    assert recognition is not None
    assert len(recognition.oriented_slots) == 3
    assert recognition.oriented_slot_patterns == ()
    _assert_authored_corpus_model(drawing.model())
    outcomes = oriented_slot_requirement_outcomes(
        recognition,
        drawing.model().features,
        AnnotationRegistry(),
    )
    assert len(outcomes) == 6
    assert {outcome.parameter_id for outcome in outcomes} == {
        "oriented_slot_width.length",
        "oriented_slot_length.length",
    }
    assert {outcome.state for outcome in outcomes} == {"missing"}


def test_supplied_standalone_inventory_derives_its_pattern_projection() -> None:
    part = _part(((-30, 0), (0, 0), (30, 0)))
    records = recognise_oriented_slots(part)

    model = build_part_model(part, oriented_slots=records)

    assert len(records) == 3
    assert not [feature for feature in model.features if feature.kind == "oriented_slot"]


def test_authored_corpus_oracle_detects_a_corrupt_lowering(monkeypatch) -> None:
    original = _CONVERTERS[OrientedSlot]

    def corrupt_width(record, context):
        feature = original(record, context)
        return replace(feature, width=feature.width + 1.0)

    monkeypatch.setitem(_CONVERTERS, OrientedSlot, corrupt_width)
    with pytest.raises(ValueError, match="dimensions must match its passage rectangle"):
        build_part_model(_authored_corpus_part())


def test_removing_one_grouped_callout_exposes_both_requirements() -> None:
    drawing = build_drawing(_part())
    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
    name = next(iter(drawing.annotations_of(feature)))

    drawing.remove(name)

    assert _requirement_codes(drawing) == [
        "oriented_slot_requirement_missing",
        "oriented_slot_requirement_missing",
    ]


def test_severing_compiler_provenance_fails_closed_for_both_values() -> None:
    drawing = build_drawing(_part())
    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
    name = next(iter(drawing.annotations_of(feature)))
    identity = drawing.registry.identity_of(name)
    identity["measurement"] = ()
    drawing.registry.reapply(name, identity)

    assert _requirement_codes(drawing) == [
        "oriented_slot_requirement_unverifiable",
        "oriented_slot_requirement_unverifiable",
    ]


def test_full_source_passage_identity_is_load_bearing() -> None:
    automatic = build_drawing(_part())
    recognition = automatic.recognition()
    detected = _feature()
    assert detected.passage.body_key is not None
    mutated = oriented_slot(
        **{
            **_kwargs(detected),
            "body_key": tuple(value + 1.0 for value in detected.passage.body_key),
        }
    )

    assert recognition is not None
    outcomes = oriented_slot_requirement_outcomes(
        recognition,
        [mutated],
        AnnotationRegistry(),
    )
    assert [(outcome.parameter_id, outcome.state) for outcome in outcomes] == [
        ("oriented_slot_width.length", "unverifiable"),
        ("oriented_slot_length.length", "unverifiable"),
    ]


def test_drop_evidence_retains_both_compiler_measurement_ids() -> None:
    drawing = build_drawing(_part())
    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
    name = next(iter(drawing.annotations_of(feature)))
    measurement_ids = drawing.registry.measurement_of(name)
    drawing.remove(name)
    drawing.registry.record_issue(
        LintIssue(
            severity="warning",
            code="oriented_slot_dropped",
            message="forced placement failure",
            measurement_ids=measurement_ids,
            outcome_stage="placement",
        )
    )

    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = oriented_slot_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
    )
    assert [outcome.state for outcome in outcomes] == ["dropped", "dropped"]


@pytest.mark.parametrize("structured_note", [False, True], ids=("suppressed", "note"))
def test_authored_omission_and_structured_note_have_distinct_outcomes(structured_note) -> None:
    sheet = Sheet(_part())
    handle = sheet.oriented_slot(**_kwargs(_feature()))
    sheet.authored_dimensions()
    if structured_note:
        sheet.note(
            "CONTROLLED BY PROCESS",
            handle,
            satisfies=("oriented_slot_width.length", "oriented_slot_length.length"),
        )
    drawing = sheet.build()
    summary = drawing.lint_summary()["quality"]["completeness"]

    state = "satisfied_by_structured_note" if structured_note else "suppressed"
    assert summary[state] == 2
    assert summary["requirements"] == 2


def test_completeness_input_contracts_and_malformed_records_fail_closed() -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")

    assert recognition is not None
    assert oriented_slot_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="RecognitionResult"):
        oriented_slot_requirement_outcomes(object(), (), AnnotationRegistry())
    with pytest.raises(TypeError, match="oriented_slots must be an immutable tuple"):
        oriented_slot_requirement_outcomes(
            replace(recognition, oriented_slots=list(recognition.oriented_slots)),
            (),
            AnnotationRegistry(),
        )
    with pytest.raises(TypeError, match="oriented_slot_patterns must be an immutable tuple"):
        oriented_slot_requirement_outcomes(
            replace(
                recognition,
                oriented_slot_patterns=list(recognition.oriented_slot_patterns),
            ),
            (),
            AnnotationRegistry(),
        )

    malformed_source = replace(
        recognition,
        oriented_slots=(object(),),
        oriented_slot_patterns=(),
    )
    outcomes = oriented_slot_requirement_outcomes(
        malformed_source,
        (),
        AnnotationRegistry(),
    )
    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}

    malformed_pattern = replace(
        recognition,
        oriented_slot_patterns=(object(),),
    )
    outcomes = oriented_slot_requirement_outcomes(
        malformed_pattern,
        (feature,),
        AnnotationRegistry(),
    )
    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}

    malformed_feature = SimpleNamespace(kind="oriented_slot", passage=None)
    outcomes = oriented_slot_requirement_outcomes(
        recognition,
        (malformed_feature,),
        AnnotationRegistry(),
    )
    assert len(outcomes) == 2

    def broken_parameters():
        raise ValueError("corrupt parameter inventory")

    parameter_failure = SimpleNamespace(
        kind="oriented_slot",
        frame=feature.frame,
        width_direction=feature.width_direction,
        long_direction=feature.long_direction,
        width=feature.width,
        length=feature.length,
        passage=feature.passage,
        parameters=broken_parameters,
    )
    outcomes = oriented_slot_requirement_outcomes(
        recognition,
        (parameter_failure,),
        AnnotationRegistry(),
    )
    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_pattern_ownership_consumes_each_equal_source_occurrence_only_once() -> None:
    part = _part(((-30, 0), (0, 0), (30, 0)))
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    sources = tuple(recognise_oriented_slots(part))
    patterns = tuple(recognise_oriented_slot_patterns(sources))
    assert len(sources) == 3
    assert len(patterns) == 1
    duplicated = replace(
        recognition,
        oriented_slots=(*sources, sources[0]),
        oriented_slot_patterns=patterns,
    )
    model = build_part_model(
        part,
        oriented_slots=duplicated.oriented_slots,
        oriented_slot_patterns=patterns,
    )

    outcomes = oriented_slot_requirement_outcomes(
        duplicated,
        model.features,
        AnnotationRegistry(),
    )

    assert len([feature for feature in model.features if feature.kind == "oriented_slot"]) == 1
    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"missing"}


@pytest.mark.parametrize("kind", ["object", "singleton", "wrong-pitch", "repeated"])
def test_corrupt_pattern_ownership_never_suppresses_the_source_denominator(kind) -> None:
    part = _part(((-30, 0), (0, 0), (30, 0)))
    sources = tuple(recognise_oriented_slots(part))
    real_pattern = recognise_oriented_slot_patterns(sources)[0]
    if kind == "object":
        pattern = object()
    elif kind == "singleton":
        pattern = OrientedSlotArray((sources[0],), 30.0, (1.0, 0.0, 0.0))
    elif kind == "wrong-pitch":
        assert isinstance(real_pattern, OrientedSlotArray)
        pattern = replace(real_pattern, pitch=real_pattern.pitch + 1.0)
    else:
        pattern = OrientedSlotArray((sources[0],) * 3, 30.0, (1.0, 0.0, 0.0))

    model = build_part_model(
        part,
        oriented_slots=sources,
        oriented_slot_patterns=(pattern,),
    )
    recognition = build_drawing(part).recognition()
    assert recognition is not None
    corrupted = replace(
        recognition,
        oriented_slots=sources,
        oriented_slot_patterns=(pattern,),
    )
    outcomes = oriented_slot_requirement_outcomes(
        corrupted,
        model.features,
        AnnotationRegistry(),
    )

    assert len([feature for feature in model.features if feature.kind == "oriented_slot"]) == 3
    assert len(outcomes) == 6
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


@pytest.mark.parametrize("family", ["array", "grid"])
def test_overloaded_pattern_scalar_equality_cannot_erase_the_denominator(family) -> None:
    class EqualToEverything:
        def __eq__(self, other) -> bool:
            return True

        def __ne__(self, other) -> bool:
            return False

    points = (
        ((-30, 0), (0, 0), (30, 0))
        if family == "array"
        else tuple((x, y) for x in (-30, 0, 30) for y in (-20, 0, 20))
    )
    part = _part(points)
    sources = tuple(recognise_oriented_slots(part))
    real_pattern = recognise_oriented_slot_patterns(sources)[0]
    if family == "array":
        assert isinstance(real_pattern, OrientedSlotArray)
        pattern = replace(real_pattern, pitch=EqualToEverything())
    else:
        assert isinstance(real_pattern, OrientedSlotGrid)
        pattern = replace(real_pattern, row_pitch=EqualToEverything())

    model = build_part_model(
        part,
        oriented_slots=sources,
        oriented_slot_patterns=(pattern,),
    )
    recognition = build_drawing(part).recognition()
    assert recognition is not None
    corrupted = replace(
        recognition,
        oriented_slots=sources,
        oriented_slot_patterns=(pattern,),
    )
    outcomes = oriented_slot_requirement_outcomes(
        corrupted,
        model.features,
        AnnotationRegistry(),
    )

    assert len([feature for feature in model.features if feature.kind == "oriented_slot"]) == len(
        sources
    )
    assert len(outcomes) == 2 * len(sources)
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_overflowing_pattern_member_fails_closed_without_leaking_overflow() -> None:
    part = _part(((-30, 0), (0, 0), (30, 0)))
    sources = tuple(recognise_oriented_slots(part))
    real_pattern = recognise_oriented_slot_patterns(sources)[0]
    malformed_member = replace(sources[0], center=(10**400, 0.0, 0.0))
    pattern = replace(real_pattern, slots=(malformed_member, *real_pattern.slots[1:]))

    model = build_part_model(
        part,
        oriented_slots=sources,
        oriented_slot_patterns=(pattern,),
    )
    recognition = build_drawing(part).recognition()
    assert recognition is not None
    corrupted = replace(
        recognition,
        oriented_slots=sources,
        oriented_slot_patterns=(pattern,),
    )
    outcomes = oriented_slot_requirement_outcomes(
        corrupted,
        model.features,
        AnnotationRegistry(),
    )

    assert len([feature for feature in model.features if feature.kind == "oriented_slot"]) == 3
    assert len(outcomes) == 6
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_tuple_subclass_pattern_members_fail_closed_without_protocol_leaks() -> None:
    class ShortTuple(tuple):
        def __len__(self) -> int:
            return 3

        def __iter__(self):
            return iter(tuple.__getitem__(self, slice(0, 2)))

    part = _part(((-30, 0), (0, 0), (30, 0)))
    sources = tuple(recognise_oriented_slots(part))
    pattern = copy(recognise_oriented_slot_patterns(sources)[0])
    object.__setattr__(pattern, "slots", ShortTuple(pattern.slots))

    model = build_part_model(
        part,
        oriented_slots=sources,
        oriented_slot_patterns=(pattern,),
    )

    assert len([feature for feature in model.features if feature.kind == "oriented_slot"]) == 3


@pytest.mark.parametrize("mutation", ["row-type", "shape"])
def test_grid_pattern_schema_is_strict_before_projection(mutation) -> None:
    part = _part(tuple((x, y) for x in (-30, 0, 30) for y in (-20, 0, 20)))
    sources = tuple(recognise_oriented_slots(part))
    pattern = recognise_oriented_slot_patterns(sources)[0]
    assert isinstance(pattern, OrientedSlotGrid)
    malformed = (
        replace(pattern, rows=True) if mutation == "row-type" else replace(pattern, rows=2, cols=2)
    )

    model = build_part_model(
        part,
        oriented_slots=sources,
        oriented_slot_patterns=(malformed,),
    )

    assert len([feature for feature in model.features if feature.kind == "oriented_slot"]) == 9


@pytest.mark.parametrize(
    "mutation",
    [
        {"width": "6"},
        {"width": 10**400},
        {"width": float("nan")},
        {"center": [0.0, 0.0, 0.0]},
        {"center": (False, 0.0, 0.0)},
        {"width_direction": (2.0, 0.0, 0.0)},
        {"width_direction": ("-0.5", "0.866", "0")},
        {"body_key": [0.0]},
    ],
)
def test_malformed_provider_numerics_cannot_self_certify(mutation) -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    malformed = replace(recognition.oriented_slots[0], **mutation)
    corrupted = replace(recognition, oriented_slots=(malformed,))

    outcomes = oriented_slot_requirement_outcomes(
        corrupted,
        drawing.model().features,
        drawing.registry,
    )

    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_duck_typed_provider_passage_cannot_self_certify() -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    slot = recognition.oriented_slots[0]
    source = slot.source
    duck_source = SimpleNamespace(
        frame=SimpleNamespace(
            origin=source.frame.origin,
            run=source.frame.run,
            u=source.frame.u,
            v=source.frame.v,
        ),
        run_interval=source.run_interval,
        section=SimpleNamespace(
            boundary=tuple(
                SimpleNamespace(point=vertex.point, bulge=vertex.bulge)
                for vertex in source.section.boundary
            )
        ),
        ends=SimpleNamespace(
            low_capped=source.ends.low_capped,
            high_capped=source.ends.high_capped,
        ),
    )
    corrupted = replace(recognition, oriented_slots=(replace(slot, source=duck_source),))

    with pytest.raises(TypeError, match="released passage record schema"):
        build_part_model(
            _part(),
            oriented_slots=corrupted.oriented_slots,
            oriented_slot_patterns=(),
        )

    outcomes = oriented_slot_requirement_outcomes(
        corrupted,
        drawing.model().features,
        drawing.registry,
    )

    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


@pytest.mark.parametrize("field", ["vector", "boundary"])
def test_tuple_subclasses_are_not_released_provider_schema(field) -> None:
    class ShortTuple(tuple):
        def __len__(self) -> int:
            return 3

        def __iter__(self):
            return iter(tuple.__getitem__(self, slice(0, 2)))

    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    slot = recognition.oriented_slots[0]
    if field == "vector":
        malformed = replace(slot, width_direction=ShortTuple(slot.width_direction))
    else:
        section = copy(slot.source.section)
        object.__setattr__(section, "boundary", ShortTuple(section.boundary))
        source = copy(slot.source)
        object.__setattr__(source, "section", section)
        malformed = replace(slot, source=source)

    with pytest.raises(ValueError):
        build_part_model(
            _part(),
            oriented_slots=(malformed,),
            oriented_slot_patterns=(),
        )


@pytest.mark.parametrize("mutation", ["orthogonal", "handed", "gauge", "basis", "origin"])
def test_provider_frame_is_revalidated_at_the_adapter_boundary(mutation) -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    slot = recognition.oriented_slots[0]
    frame = copy(slot.source.frame)
    if mutation == "orthogonal":
        object.__setattr__(frame, "u", frame.run)
    elif mutation == "handed":
        object.__setattr__(frame, "v", tuple(-value for value in frame.v))
    elif mutation == "gauge":
        object.__setattr__(frame, "run", tuple(-value for value in frame.run))
        object.__setattr__(frame, "v", tuple(-value for value in frame.v))
    elif mutation == "basis":
        object.__setattr__(frame, "u", tuple(-value for value in frame.u))
        object.__setattr__(frame, "v", tuple(-value for value in frame.v))
    else:
        object.__setattr__(frame, "origin", (0.0, 0.0, 1.0))
    source = copy(slot.source)
    object.__setattr__(source, "frame", frame)

    with pytest.raises(ValueError):
        oriented_slot_provider_key(replace(slot, source=source))


def test_provider_and_ir_share_the_released_squared_length_limit() -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    slot = recognition.oriented_slots[0]
    scale = 1.000001
    frame = copy(slot.source.frame)
    object.__setattr__(frame, "run", tuple(scale * value for value in frame.run))
    object.__setattr__(frame, "v", tuple(scale * value for value in frame.v))
    source = copy(slot.source)
    object.__setattr__(source, "frame", frame)

    with pytest.raises(ValueError, match="unit length"):
        oriented_slot_provider_key(replace(slot, source=source))

    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
    with pytest.raises(ValueError, match="unit length"):
        replace(
            feature.passage,
            run=tuple(scale * value for value in feature.passage.run),
            v=tuple(scale * value for value in feature.passage.v),
        )


@pytest.mark.parametrize("ordering", ["cyclic", "reversed"])
def test_provider_and_ir_require_the_public_canonical_section_order(ordering) -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    slot = recognition.oriented_slots[0]
    boundary = slot.source.section.boundary
    malformed_boundary = (
        boundary[1:] + boundary[:1] if ordering == "cyclic" else tuple(reversed(boundary))
    )
    section = copy(slot.source.section)
    object.__setattr__(section, "boundary", malformed_boundary)
    source = copy(slot.source)
    object.__setattr__(source, "section", section)

    with pytest.raises(ValueError, match="canonical public ordering"):
        oriented_slot_provider_key(replace(slot, source=source))

    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
    ir_boundary = feature.passage.boundary
    malformed_ir = (
        ir_boundary[1:] + ir_boundary[:1] if ordering == "cyclic" else tuple(reversed(ir_boundary))
    )
    with pytest.raises(ValueError, match="canonical"):
        replace(feature.passage, boundary=malformed_ir)


def test_none_body_identity_remains_distinct_and_valid() -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    slot = replace(recognition.oriented_slots[0], body_key=None)

    assert oriented_slot_provider_key(slot)[-1] is None


@pytest.mark.parametrize("mutation", ["width", "center", "feature-body", "source-body"])
def test_source_to_ir_correspondence_is_exact_after_boundary_validation(mutation) -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    source = recognition.oriented_slots[0]
    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
    features = (feature,)
    if mutation == "width":
        features = (replace(feature, width=feature.width + 0.0004),)
    elif mutation == "center":
        center = (feature.frame.origin[0] + 4e-7, *feature.frame.origin[1:])
        features = (replace(feature, frame=Frame(center, feature.frame.axis)),)
    elif mutation == "feature-body":
        assert feature.passage.body_key is not None
        body_key = (feature.passage.body_key[0] + 0.0004, *feature.passage.body_key[1:])
        passage = replace(feature.passage, body_key=body_key)
        features = (replace(feature, passage=passage),)
    else:
        assert source.body_key is not None
        body_key = (source.body_key[0] + 0.0004, *source.body_key[1:])
        recognition = replace(
            recognition,
            oriented_slots=(replace(source, body_key=body_key),),
        )

    outcomes = oriented_slot_requirement_outcomes(
        recognition,
        features,
        AnnotationRegistry(),
    )

    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_spoofed_ir_class_name_cannot_join_provider_evidence() -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
    spoof_type = type(
        "OrientedSlotFeature",
        (),
        {
            "__module__": "draftwright.model.ir",
            "kind": "oriented_slot",
            "parameters": lambda self: feature.parameters(),
        },
    )
    spoof = spoof_type()
    for name in (
        "frame",
        "width_direction",
        "long_direction",
        "run_direction",
        "width",
        "length",
        "passage",
    ):
        setattr(spoof, name, getattr(feature, name))

    outcomes = oriented_slot_requirement_outcomes(
        recognition,
        (spoof,),
        AnnotationRegistry(),
    )

    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_exact_ir_identity_registration_is_write_once() -> None:
    feature = _feature()
    register_oriented_slot_feature_type(type(feature))
    spoof = type(
        "OrientedSlotFeature",
        (),
        {"__module__": "draftwright.model.ir", "kind": "oriented_slot"},
    )

    with pytest.raises(RuntimeError, match="already registered"):
        register_oriented_slot_feature_type(spoof)


@pytest.mark.parametrize("width", [7.0, 6.0004])
def test_provider_nominals_must_be_the_serialized_passage_projection(width) -> None:
    drawing = build_drawing(_part())
    recognition = drawing.recognition()
    assert recognition is not None
    source = recognition.oriented_slots[0]
    corrupted = replace(recognition, oriented_slots=(replace(source, width=width),))

    outcomes = oriented_slot_requirement_outcomes(
        corrupted,
        drawing.model().features,
        drawing.registry,
    )

    assert len(outcomes) == 2
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}
    if width == 7.0:
        feature = next(item for item in drawing.model().features if item.kind == "oriented_slot")
        with pytest.raises(ValueError, match="dimensions must match its passage rectangle"):
            replace(feature, width=width)


def test_ir_validation_rejects_every_malformed_normative_field() -> None:
    feature = _feature()
    passage = feature.passage
    bad_boundary = list(passage.boundary)

    with pytest.raises(ValueError, match="run_interval must contain two finite values"):
        replace(passage, run_interval=("bad", 5.0))
    with pytest.raises(ValueError, match="run_interval must be finite and increasing"):
        replace(passage, run_interval=(5.0, 5.0))
    bad_boundary[0] = (("bad", 0.0), 0.0)
    with pytest.raises(ValueError, match="boundary values must be finite numbers"):
        replace(passage, boundary=tuple(bad_boundary))
    bad_boundary[0] = ((float("nan"), 0.0), 0.0)
    with pytest.raises(ValueError, match="boundary values must be finite numbers"):
        replace(passage, boundary=tuple(bad_boundary))
    with pytest.raises(ValueError, match="body_key must contain only finite values"):
        replace(passage, body_key=(float("nan"),))
    with pytest.raises(ValueError, match="open through both ends"):
        replace(passage, low_capped=True)
    curved_boundary = list(passage.boundary)
    curved_boundary[0] = (curved_boundary[0][0], 0.25)
    with pytest.raises(ValueError, match="only straight edges"):
        replace(passage, boundary=tuple(curved_boundary))
    with pytest.raises(ValueError, match="needs a Frame"):
        replace(feature, frame="not-a-frame")
    with pytest.raises(ValueError, match="needs an OrientedSlotPassage"):
        replace(feature, passage="not-a-passage")
    with pytest.raises(ValueError, match="must be unit length"):
        replace(feature, width_direction=(2.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="width and length must be finite and positive"):
        replace(feature, width=0.0)
    with pytest.raises(ValueError, match="width_direction/long_direction must be orthogonal"):
        replace(feature, width_direction=(1.0, 0.0, 0.0))
    with pytest.raises(ValueError, match="run_direction must equal its passage run"):
        replace(feature, run_direction=(0.0, 0.0, -1.0))
    with pytest.raises(ValueError, match="dominant run_direction axis"):
        replace(feature, frame=Frame(feature.frame.origin, "x"))
    assert feature.references() == []


def test_declared_ir_has_one_canonical_nonprincipal_family_identity() -> None:
    feature = _feature()
    passage = feature.passage
    flipped_boundary = tuple(((point[0], -point[1]), bulge) for point, bulge in passage.boundary)
    with pytest.raises(ValueError, match="right handed"):
        replace(
            passage,
            v=tuple(-value for value in passage.v),
            boundary=flipped_boundary,
        )

    shifted_origin = tuple(
        origin + run for origin, run in zip(passage.origin, passage.run, strict=True)
    )
    shifted_interval = tuple(value - 1.0 for value in passage.run_interval)
    with pytest.raises(ValueError, match="canonical run-axis foot"):
        replace(passage, origin=shifted_origin, run_interval=shifted_interval)

    principal_boundary = (
        ((-12.0, -3.0), 0.0),
        ((12.0, -3.0), 0.0),
        ((12.0, 3.0), 0.0),
        ((-12.0, 3.0), 0.0),
    )
    principal_passage = replace(passage, boundary=principal_boundary)
    with pytest.raises(ValueError, match="legacy slot family"):
        replace(
            feature,
            passage=principal_passage,
            width_direction=(0.0, 1.0, 0.0),
            long_direction=(1.0, 0.0, 0.0),
            width=6.0,
            length=24.0,
        )


def test_declared_run_direction_must_have_three_components() -> None:
    with pytest.raises(ValueError, match="run must be a finite real-number 3-vector"):
        oriented_slot(**{**_kwargs(_feature()), "run_direction": (0.0, 1.0)})


@pytest.mark.parametrize(
    "override",
    [
        {"width": "6"},
        {"center": 42},
        {"center": (False, 0.0, 0.0)},
        {"center": ("0", "0", "0")},
        {"width_direction": (False, 1.0, 0.0)},
        {"run_direction": ("0", "0", "1")},
        {"source_origin": (False, 0.0, 0.0)},
        {"run_interval": (-(10**400), 5.0)},
        {"source_boundary": None},
        {"source_boundary": ((((0.0, 0.0, 0.0), 0.0),) * 4)},
        {"source_boundary": (((False, 0.0), 0.0),) * 4},
        {"body_key": (10**400,)},
    ],
)
def test_public_declaration_rejects_coercible_or_overflowing_numerics(override) -> None:
    with pytest.raises(ValueError):
        oriented_slot(**{**_kwargs(_feature()), **override})


def test_oriented_parameter_view_preference_uses_the_presentation_axis() -> None:
    feature = _feature()
    model = PartModel(_part().bounding_box(), "prismatic", [feature])
    dimension = plan_dimensions(model)[0].dims[0]

    assert _parameter_view_preferences(feature, dimension) == ("plan",)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"run_direction": (0.0, 0.0, 2.0)}, "run must be unit length"),
        ({"source_u": (0.0, 0.0, 1.0)}, "run/u must be orthogonal"),
        ({"low_capped": 1}, "end states must be booleans"),
        ({"source_boundary": (((0.0, 0.0), 0.0),) * 3}, "must contain four vertices"),
    ],
)
def test_malformed_declared_source_evidence_fails_closed(override, message) -> None:
    with pytest.raises(ValueError, match=message):
        oriented_slot(**{**_kwargs(_feature()), **override})


def test_declared_oriented_slot_requires_a_straight_uncapped_passage() -> None:
    feature = _feature()
    curved = list(feature.passage.boundary)
    curved[0] = (curved[0][0], 0.1)
    with pytest.raises(ValueError, match="only straight edges"):
        oriented_slot(**{**_kwargs(feature), "source_boundary": tuple(curved)})
    with pytest.raises(ValueError, match="open through both ends"):
        oriented_slot(**{**_kwargs(feature), "low_capped": True})
