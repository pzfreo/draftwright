"""#1438 — accepted channels retain their exact conditional IR ownership."""

from __future__ import annotations

import pytest
from b123d_recognisers import Channel, FaceLevel
from b123d_recognisers.evidence import build_recognition_evidence
from build123d import Box, Compound, Cylinder, Pos, Rot

from draftwright import build_drawing
from draftwright.model.detect import _channel_coordinate_matches, _step_level_owns_channel
from draftwright.model.ir import Frame, StepLevelFeature
from draftwright.recognition_ownership import CONDITIONAL_FAMILIES, RecognitionOwnershipBuilder


def _u_channel():
    width = 50.0
    wall = 12.5
    part = (
        Box(50, width, 12)
        + Pos(0, -width / 2 + wall / 2, 15) * Box(50, wall, 18)
        + Pos(0, width / 2 - wall / 2, 15) * Box(50, wall, 18)
    )
    return part


def _monolithic_rebate():
    return Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)


def _two_monolithic_rebates():
    return (
        Box(120, 100, 30)
        - Pos(0, -25, 7.5) * Box(120, 15, 15)
        - Pos(0, 25, 7.5) * Box(120, 15, 15)
    )


def _channel_occurrence(ownership):
    (occurrence,) = tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == "channels"
    )
    return occurrence


def test_conditional_family_roster_is_explicit() -> None:
    assert CONDITIONAL_FAMILIES == {"channels", "turned_steps"}


def test_multi_plate_channel_is_represented_by_its_exact_channel_feature() -> None:
    drawing = build_drawing(_u_channel())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrence = _channel_occurrence(ownership)
    binding = ownership.binding_for(occurrence)

    assert binding is not None
    assert binding.disposition == "represented"
    assert binding.reason_code == "channel_adapter"
    assert binding.feature.kind == "channel"
    assert any(binding.feature is feature for feature in drawing.model().features)
    assert ownership.status(occurrence) == "represented"
    assert ownership.unexpectedly_missing == ()


def test_monolithic_rebate_channel_is_absorbed_by_the_exact_step_ladder() -> None:
    drawing = build_drawing(_monolithic_rebate())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrence = _channel_occurrence(ownership)
    binding = ownership.binding_for(occurrence)

    assert binding is not None
    assert binding.disposition == "absorbed"
    assert binding.reason_code == "channel_step_level_owner"
    assert binding.feature.kind == "step_level"
    assert any(binding.feature is feature for feature in drawing.model().features)
    assert ownership.status(occurrence) == "absorbed"
    assert ownership.unexpectedly_missing == ()


def test_cross_axis_rebate_does_not_inherit_an_unrelated_z_step_ladder() -> None:
    drawing = build_drawing(Rot(90, 0, 0) * _monolithic_rebate())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrence = _channel_occurrence(ownership)
    record = ownership.evidence.record(occurrence)

    assert record.depth_axis == "y"
    assert any(feature.kind == "step_level" for feature in drawing.model().features)
    assert ownership.binding_for(occurrence) is None
    assert ownership.status(occurrence) == "unexpectedly_missing"
    assert ownership.unexpectedly_missing == (occurrence,)


def test_disconnected_body_cannot_donate_a_channel_step_ladder_owner() -> None:
    downward_rebate = Box(80, 60, 30) - Pos(0, 0, -7.5) * Box(80, 20, 15)
    part = Compound(
        children=[
            Pos(-50, 0, 0) * downward_rebate,
            Pos(50, 0.4, 0) * _monolithic_rebate(),
        ]
    )
    drawing = build_drawing(part)
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrences = tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == "channels"
    )
    downward = next(
        occurrence
        for occurrence in occurrences
        if ownership.evidence.record(occurrence).open_sign < 0
    )
    upward = next(
        occurrence
        for occurrence in occurrences
        if ownership.evidence.record(occurrence).open_sign > 0
    )
    ladder = next(feature for feature in drawing.model().features if feature.kind == "step_level")

    assert ladder.level_supports[0].x_span == pytest.approx((10.0, 90.0))
    assert ownership.binding_for(downward) is None
    assert ownership.status(downward) == "unexpectedly_missing"
    assert ownership.status(upward) == "absorbed"


def test_equal_face_level_values_do_not_erase_body_local_channel_ownership() -> None:
    downward_rebate = Box(80, 60, 30) - Pos(0, 0, -7.5) * Box(80, 20, 15)
    drawing = build_drawing(Compound(children=[downward_rebate, _monolithic_rebate()]))
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    level_occurrences = tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == "step_levels"
    )
    assert len(level_occurrences) == 2
    assert ownership.evidence.record(level_occurrences[0]) == ownership.evidence.record(
        level_occurrences[1]
    )
    assert ownership.evidence.record(level_occurrences[0]) is not ownership.evidence.record(
        level_occurrences[1]
    )

    channel_occurrences = tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == "channels"
    )
    downward = next(
        occurrence
        for occurrence in channel_occurrences
        if ownership.evidence.record(occurrence).open_sign < 0
    )
    upward = next(
        occurrence
        for occurrence in channel_occurrences
        if ownership.evidence.record(occurrence).open_sign > 0
    )

    assert ownership.binding_for(downward) is None
    assert ownership.status(downward) == "unexpectedly_missing"
    assert ownership.status(upward) == "absorbed"


@pytest.mark.parametrize(
    "translation",
    [
        (0.123, 0.0, 0.0),
        (0.0, 0.123, 0.0),
        (0.0, 0.0, 0.123),
        (0.0, 1_000_000.123, 0.0),
        (0.0, 2_150_000_000.005, 0.0),
    ],
)
def test_channel_publication_rounding_keeps_the_exact_body_owner(translation) -> None:
    drawing = build_drawing(Pos(*translation) * _monolithic_rebate())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrence = _channel_occurrence(ownership)
    binding = ownership.binding_for(occurrence)

    assert binding is not None
    assert binding.disposition == "absorbed"
    assert binding.reason_code == "channel_step_level_owner"


def test_channel_publication_half_cell_boundary_is_explicit() -> None:
    assert _channel_coordinate_matches(10.0, 10.005)
    assert _channel_coordinate_matches(1_000_000.0, 1_000_000.005)
    assert _channel_coordinate_matches(2_150_000_000.01, 2_150_000_000.005)
    assert not _channel_coordinate_matches(10.0, 10.0051)
    assert not _channel_coordinate_matches(2_150_000_000.01, 2_150_000_000.004)


@pytest.mark.parametrize("offset", [0.0049, 0.0051])
def test_independently_published_center_and_width_keep_their_owner(offset) -> None:
    part = Box(80, 60, 30) - Pos(0, offset, 7.5) * Box(80, 20.0049, 15)
    drawing = build_drawing(part)
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrence = _channel_occurrence(ownership)
    binding = ownership.binding_for(occurrence)

    assert binding is not None
    assert binding.disposition == "absorbed"
    assert binding.reason_code == "channel_step_level_owner"


def test_derived_shoulder_publication_boundary_is_explicit() -> None:
    assert _channel_coordinate_matches(10.0, 10.0075, tolerance=0.0075)
    assert _channel_coordinate_matches(10.0, 10.008, tolerance=0.008)
    assert not _channel_coordinate_matches(10.0, 10.0076, tolerance=0.0075)
    assert not _channel_coordinate_matches(10.0, 10.0081, tolerance=0.008)


def test_channel_is_not_absorbed_when_an_exact_shoulder_left_the_final_ladder() -> None:
    through_step = Box(40, 40, 20) - Pos(15, 10, 0) * Box(20, 20, 30)
    part = Compound(children=[_monolithic_rebate(), Pos(0, 10, 100) * through_step])
    drawing = build_drawing(part)
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrence = _channel_occurrence(ownership)
    channel = ownership.evidence.record(occurrence)
    ladder = next(feature for feature in drawing.model().features if feature.kind == "step_level")

    assert channel.w_center == pytest.approx(0.0)
    assert channel.width == pytest.approx(20.0)
    assert ladder.levels == pytest.approx((0.0,))
    assert ladder.shoulders == (("y", -10.0),)
    assert ownership.binding_for(occurrence) is None
    assert ownership.status(occurrence) == "unexpectedly_missing"


def test_channel_owner_predicate_fails_closed_without_complete_floor_support() -> None:
    channel = Channel(
        width_axis="y",
        long_axis="x",
        width=20.0,
        w_center=0.0,
        lo=-40.0,
        hi=40.0,
        d_lo=0.0,
        d_hi=15.0,
        open_sign=1,
    )
    missing_floor = StepLevelFeature(
        frame=Frame((0.0, 0.0, -15.0), "z"),
        base=-15.0,
        levels=(5.0,),
    )
    floor_only = StepLevelFeature(
        frame=Frame((0.0, 0.0, -15.0), "z"),
        base=-15.0,
        levels=(0.0,),
    )

    assert not _step_level_owns_channel(channel, missing_floor, face_levels=(), risers=())
    assert not _step_level_owns_channel(
        channel,
        floor_only,
        face_levels=(FaceLevel(0.0, None, (-10.0, 10.0)),),
        risers=(),
    )
    assert not _step_level_owns_channel(
        channel,
        floor_only,
        face_levels=(FaceLevel(0.0, (-40.0, 40.0), (-5.0, 5.0)),),
        risers=(),
    )


def test_multiple_exact_channels_may_share_their_final_step_ladder() -> None:
    drawing = build_drawing(_two_monolithic_rebates())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrences = tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == "channels"
    )
    bindings = tuple(ownership.binding_for(occurrence) for occurrence in occurrences)

    assert len(occurrences) == 2
    assert all(binding is not None for binding in bindings)
    owners = {id(binding.feature) for binding in bindings if binding is not None}
    assert len(owners) == 1
    assert all(
        binding is not None
        and binding.disposition == "absorbed"
        and binding.reason_code == "channel_step_level_owner"
        for binding in bindings
    )
    assert ownership.unexpectedly_missing == ()


def test_unbound_channel_fails_closed_as_unexpectedly_missing() -> None:
    evidence = build_recognition_evidence(_u_channel())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    occurrence = _channel_occurrence(ownership)

    assert ownership.expected_conditional == (occurrence,)
    assert occurrence in ownership.owner_expected_occurrences
    assert ownership.status(occurrence) == "unexpectedly_missing"
    assert ownership.unexpectedly_missing == (occurrence,)


def test_feature_absorption_rejects_unknown_wrong_family_wrong_owner_and_duplicates() -> None:
    part = _monolithic_rebate() - Pos(0, 20, 0) * Cylinder(2, 30)
    evidence = build_recognition_evidence(part)
    builder = RecognitionOwnershipBuilder(evidence)
    channel_occurrence = next(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "channels"
    )
    channel = evidence.record(channel_occurrence)
    hole = next(
        evidence.record(occurrence)
        for occurrence in evidence.features
        if evidence.family(occurrence) == "holes"
    )

    class StepOwner:
        kind = "step_level"

    with pytest.raises(ValueError, match="unknown feature-absorption ownership reason_code"):
        builder.absorb_into(channel, StepOwner(), reason_code="future_contract")
    with pytest.raises(ValueError, match="does not match occurrence family"):
        builder.absorb_into(hole, StepOwner(), reason_code="channel_step_level_owner")
    with pytest.raises(ValueError, match="does not match IR owner kind"):
        builder.absorb_into(channel, object(), reason_code="channel_step_level_owner")

    occupied_owner = StepOwner()
    builder.bind(hole, occupied_owner, reason_code="hole_adapter")
    with pytest.raises(ValueError, match="already has an incompatible recognition owner"):
        builder.absorb_into(channel, occupied_owner, reason_code="channel_step_level_owner")

    owner = StepOwner()
    builder.absorb_into(channel, owner, reason_code="channel_step_level_owner")
    with pytest.raises(ValueError, match="already has an IR owner"):
        builder.absorb_into(channel, owner, reason_code="channel_step_level_owner")

    binding = builder.snapshot().binding_for(channel_occurrence)
    assert binding is not None
    assert binding.feature is owner
