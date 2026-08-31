"""Consumer evidence for the prepared frame boundary and recognisers 0.4.9 (#1357)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from b123d_recognisers import (
    FramedRecognitionResult,
    FrameGauge,
    FrameRefusalReason,
    PartFrame,
    PreparedFramedPart,
    RefusedPartFrame,
    build_raw_recognition_result,
    prepare_framed_part,
)
from build123d import Align, Axis, Box, Compound, Cylinder, Pos

import draftwright.recognition_frame as frame_module
from draftwright.model.detect import build_part_model
from draftwright.recognition_frame import (
    FramedDetection,
    FramedDetectionRefusal,
    FramedRecognitionContractError,
    MultipleTurnedProfilesError,
    prepare_framed_detection,
    single_turned_profile,
)


def _asymmetric_prism():
    return Box(10, 20, 30) + Pos(9, 18, 28) * Box(2, 3, 4)


def _stepped_shaft():
    return Cylinder(15, 20) + Pos(0, 0, 20) * Cylinder(10, 20)


def test_prepared_boundary_keeps_the_exact_local_part_frame_result_and_cylinders() -> None:
    source = Pos(17, -23, 9) * _stepped_shaft().rotate(Axis.X, 37)

    detection = prepare_framed_detection(source)

    assert isinstance(detection, FramedDetection)
    assert detection.source_part is source
    assert detection.part is detection.prepared.part
    assert detection.frame is detection.prepared.frame
    assert detection.result.rotational is detection.classification.is_rotational is True
    assert detection.result.cylinders == detection.prepared.cylinders
    assert detection.classification.z_cyls is detection.prepared.cylinders[0]
    assert detection.classification.cross_cyls is detection.prepared.cylinders[1]
    assert isinstance(detection.classification.z_diams, tuple)
    assert isinstance(detection.classification.cross_diams, tuple)
    with pytest.raises(AttributeError):
        detection.classification.cross_cyls.clear()
    assert all(
        left is right
        for source_group, result_group in zip(
            detection.prepared.cylinders, detection.result.cylinders, strict=True
        )
        for left, right in zip(source_group, result_group, strict=True)
    )


def test_prepared_boundary_reuses_provider_cylinders_for_draftwright_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _stepped_shaft()
    prepared = prepare_framed_part(source)
    assert isinstance(prepared, PreparedFramedPart)
    classify = frame_module.classify_geometry

    def tracked_classification(part, *, cylinders):
        assert part is prepared.part
        assert cylinders is prepared.cylinders
        return classify(part, cylinders=cylinders)

    # ``prepare_framed_part`` owns the scan. The boundary must pass its exact inventory to
    # Draftwright's classifier; ``PreparedFramedPart.recognise`` then guarantees aggregate-side
    # reuse, whose record identity the preceding test checks.
    monkeypatch.setattr(frame_module, "prepare_framed_part", lambda _part: prepared)
    monkeypatch.setattr(frame_module, "classify_geometry", tracked_classification)

    detection = prepare_framed_detection(source)

    assert isinstance(detection, FramedDetection)
    assert sum(map(len, detection.result.cylinders)) == 2


@pytest.mark.parametrize(
    ("part", "gauge", "capabilities"),
    [
        (_asymmetric_prism(), FrameGauge.FULL, (True, True, True)),
        (Box(10, 20, 30), FrameGauge.ORTHOGONAL, (False, False, True)),
        (Cylinder(10, 30), FrameGauge.AXIAL, (False, False, False)),
    ],
)
def test_every_successful_gauge_has_an_explicit_fail_closed_policy(part, gauge, capabilities):
    detection = prepare_framed_detection(part)

    assert isinstance(detection, FramedDetection)
    assert detection.frame.gauge is detection.policy.gauge is gauge
    assert (
        detection.policy.directed_ordered_basis,
        detection.policy.material_axis_identity,
        detection.policy.roll_observable,
    ) == capabilities


@pytest.mark.parametrize("reason", list(FrameRefusalReason))
def test_every_typed_frame_refusal_propagates_without_a_raw_fallback(
    monkeypatch: pytest.MonkeyPatch, reason: FrameRefusalReason
) -> None:
    source = Box(10, 20, 30)
    monkeypatch.setattr(
        frame_module, "prepare_framed_part", lambda _part: RefusedPartFrame(reason)
    )
    monkeypatch.setattr(
        frame_module,
        "classify_geometry",
        lambda *_args, **_kwargs: pytest.fail("a refused frame must not be classified"),
    )

    refusal = prepare_framed_detection(source)

    assert refusal == FramedDetectionRefusal(source, reason)


class _PreparedOutcome:
    def __init__(self, prepared: PreparedFramedPart, framed: FramedRecognitionResult):
        self.frame = prepared.frame
        self.part = prepared.part
        self.cylinders = prepared.cylinders
        self._framed = framed

    def recognise(self, *, rotational: bool = False) -> FramedRecognitionResult:
        del rotational
        return self._framed


@pytest.mark.parametrize("mismatch", ["frame", "part", "cylinders", "cylinder_groups"])
def test_boundary_rejects_every_mismatched_prepared_result_pair(
    monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    source = _stepped_shaft()
    prepared = prepare_framed_part(source)
    assert isinstance(prepared, PreparedFramedPart)
    framed = prepared.recognise(rotational=True)
    if mismatch == "frame":
        bad_frame = PartFrame(
            framed.frame.origin,
            framed.frame.x,
            framed.frame.y,
            framed.frame.z,
            framed.frame.gauge,
        )
        framed = replace(framed, frame=bad_frame)
    elif mismatch == "part":
        framed = replace(framed, part=Box(1, 2, 3))
    elif mismatch == "cylinders":
        copied = tuple(
            tuple(dict(cylinder) for cylinder in group) for group in framed.result.cylinders
        )
        framed = replace(framed, result=replace(framed.result, cylinders=copied))
    else:
        framed = replace(
            framed,
            result=replace(framed.result, cylinders=framed.result.cylinders[:1]),
        )
    monkeypatch.setattr(
        frame_module,
        "prepare_framed_part",
        lambda _part: _PreparedOutcome(prepared, framed),
    )

    with pytest.raises(
        FramedRecognitionContractError, match="prepared frame, part, and cylinders"
    ):
        prepare_framed_detection(source)


def test_compound_levels_and_risers_keep_body_local_evidence_in_the_framed_unit() -> None:
    def stepped_block():
        return Box(40, 30, 10) + Pos(0, 0, 10) * Box(20, 30, 10)

    source = Compound(children=[Pos(-60, 0, 0) * stepped_block(), Pos(60, 0, 0) * stepped_block()])
    detection = prepare_framed_detection(source)

    assert isinstance(detection, FramedDetection)
    assert len(detection.result.step_levels) == 4
    assert len(detection.result.risers) == 4
    body_level_sets = {riser.body_levels for riser in detection.result.risers}
    assert None not in body_level_sets
    assert len(body_level_sets) == 2
    assert {len(levels) for levels in body_level_sets if levels is not None} == {2}
    assert set().union(*(set(levels or ()) for levels in body_level_sets)) == set(
        detection.result.step_levels
    )


def test_equal_body_local_level_occurrences_project_to_one_global_height_rung() -> None:
    aligned = (Align.MIN, Align.MIN, Align.MIN)

    def stepped_block():
        return Box(40, 30, 10, align=aligned) + Pos(0, 0, 10) * Box(20, 30, 10, align=aligned)

    source = Compound(children=[Pos(-60, 0, 0) * stepped_block(), Pos(60, 0, 0) * stepped_block()])
    recognition = build_raw_recognition_result(source, rotational=False)
    # Hold the higher-level through-step owner out so this test reaches the legacy global
    # height projection whose equal-rung behavior it guards.
    model = build_part_model(source, through_steps=[])
    ladder = next(feature for feature in model.features if feature.kind == "step_level")

    equal_occurrences = [level for level in recognition.step_levels if level.z == 10.0]
    assert len(equal_occurrences) == 2
    assert ladder.levels == (10.0,)
    assert len(ladder.level_supports) == 1
    support = ladder.level_supports[0]
    assert support.x_span[1] - support.x_span[0] == pytest.approx(20.0)


def test_multiple_body_local_turned_profiles_are_visible_but_fail_closed_at_the_singular_waist():
    source = Compound(
        children=[Pos(-50, 0, 0) * _stepped_shaft(), Pos(50, 0, 0) * _stepped_shaft()]
    )
    result = build_raw_recognition_result(source, rotational=True)

    assert len(result.turned_profiles) == 2
    assert len({profile.profile for profile in result.turned_profiles}) == 2
    with pytest.raises(MultipleTurnedProfilesError, match=r"recognised 2"):
        single_turned_profile(result)
    with pytest.raises(MultipleTurnedProfilesError, match=r"recognised 2"):
        build_part_model(source)


def test_framed_classifier_rejects_an_eccentric_parallel_cylinder_compound() -> None:
    source = Compound(children=[Cylinder(10, 40), Pos(8, 0, 0) * Cylinder(2, 20)]).rotate(
        Axis.Y, 90
    )

    detection = prepare_framed_detection(source)

    assert isinstance(detection, FramedDetection)
    assert detection.frame.gauge is FrameGauge.AXIAL
    assert detection.classification.is_rotational is False
    assert detection.result.rotational is False


def test_framed_classifier_rejects_equal_diameter_eccentric_bands_on_one_body() -> None:
    aligned = (Align.CENTER, Align.CENTER, Align.MIN)
    source = (
        Cylinder(10, 20, align=aligned) + Pos(0.5, 0, 20) * Cylinder(10, 20, align=aligned)
    ).rotate(Axis.Y, 90)
    assert len(source.solids()) == 1

    detection = prepare_framed_detection(source)

    assert isinstance(detection, FramedDetection)
    assert detection.frame.gauge is FrameGauge.AXIAL
    assert detection.classification.is_rotational is False
    assert detection.result.rotational is False


def test_raw_production_preserves_plural_compounds_without_selecting_or_merging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from draftwright import build_drawing

    source = Compound(
        children=[Pos(-50, 0, 0) * _stepped_shaft(), Pos(50, 0, 0) * _stepped_shaft()]
    )

    with caplog.at_level("WARNING", logger="draftwright.analysis"):
        drawing = build_drawing(source)

    assert len(drawing.recognition().turned_profiles) == 2
    assert not [feature for feature in drawing.model().features if feature.kind == "step"]
    assert "singular Analysis waist defers them to #1357" in caplog.text


def test_zero_or_one_physical_turned_profile_preserves_the_existing_analysis_shape() -> None:
    empty = build_raw_recognition_result(Box(10, 20, 30), rotational=False)
    one = build_raw_recognition_result(_stepped_shaft(), rotational=True)

    assert single_turned_profile(empty) is None
    assert single_turned_profile(one) == one.turned_profiles[0]


def test_production_remains_on_the_explicit_raw_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from draftwright import analysis as analysis_module
    from draftwright import build_drawing

    raw_calls = []
    raw_builder = analysis_module.build_raw_recognition_result

    def tracked_raw_builder(*args, **kwargs):
        raw_calls.append((args, kwargs))
        return raw_builder(*args, **kwargs)

    monkeypatch.setattr(analysis_module, "build_raw_recognition_result", tracked_raw_builder)
    monkeypatch.setattr(
        frame_module,
        "prepare_framed_part",
        lambda _part: pytest.fail("production must not enter framed preparation yet"),
    )

    drawing = build_drawing(Box(10, 20, 30))

    assert len(raw_calls) == 1
    assert drawing.recognition() is not None
