"""#1438 — nested accepted occurrences retain their exact parent ownership."""

from __future__ import annotations

from pathlib import Path

import pytest
from b123d_recognisers.evidence import build_recognition_evidence
from build123d import Box, Cone, Cylinder, Pos, import_step

from draftwright import build_drawing
from draftwright.model.detect import _build_part_model_from_recognition
from draftwright.pmi import PmiRecord
from draftwright.recognition_ownership import NESTED_FAMILIES, RecognitionOwnershipBuilder

FIXTURES = Path(__file__).parent / "fixtures" / "evaluation"


def _mixed_countersinks():
    return import_step(FIXTURES / "countersink-mixed-pair.step")


def _same_spec_countersinks():
    part = Box(60, 30, 12)
    for x in (-15, 15):
        part -= Pos(x, 0, 0) * Cylinder(3, 12)
        part -= Pos(x, 0, 4) * Cone(3, 7, 4)
    return part


def _two_ended_countersink():
    return (
        Box(50, 50, 12)
        - Cylinder(3, 12)
        - Pos(0, 0, 4) * Cone(3, 7, 4)
        - Pos(0, 0, -4) * Cone(7, 3, 4)
    )


def test_nested_family_roster_is_explicit() -> None:
    assert NESTED_FAMILIES == {"countersinks"}


def test_each_countersink_is_absorbed_by_its_exact_hole_owner() -> None:
    drawing = build_drawing(_mixed_countersinks())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    evidence = ownership.evidence
    holes = tuple(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "holes"
    )
    countersinks = tuple(
        occurrence
        for occurrence in evidence.features
        if evidence.family(occurrence) == "countersinks"
    )

    assert len(holes) == len(countersinks) == 2
    assert len({id(occurrence) for occurrence in countersinks}) == 2
    for countersink in countersinks:
        seat = evidence.record(countersink)
        hole = next(hole for hole in holes if evidence.record(hole).csink is seat)
        countersink_outcome = ownership.binding_for(countersink)
        hole_outcome = ownership.binding_for(hole)

        assert countersink_outcome is not None
        assert hole_outcome is not None
        assert countersink_outcome.disposition == "absorbed"
        assert countersink_outcome.reason_code == "countersink_hole_owner"
        assert countersink_outcome.feature is hole_outcome.feature
        assert countersink_outcome.member_index == hole_outcome.member_index
        assert ownership.status(countersink) == "absorbed"
        assert countersink in ownership.expected_occurrences
        assert countersink in ownership.owner_expected_occurrences
    assert ownership.unexpectedly_missing == ()


def test_grouped_nested_occurrences_reference_shared_requirements_without_duplication() -> None:
    recognition = build_drawing(_same_spec_countersinks()).report()["recognition"]
    countersinks = [
        occurrence
        for occurrence in recognition["occurrences"]
        if occurrence["family"] == "countersinks"
    ]
    holes = [
        occurrence for occurrence in recognition["occurrences"] if occurrence["family"] == "holes"
    ]

    assert len(countersinks) == len(holes) == 2
    assert countersinks[0]["requirements"] == countersinks[1]["requirements"]
    countersink_requirement_ids = countersinks[0]["requirements"]["ids"]
    assert len(countersink_requirement_ids) == 2
    requirements = {requirement["id"]: requirement for requirement in recognition["requirements"]}
    assert {
        requirements[requirement_id]["parameter_id"]
        for requirement_id in countersink_requirement_ids
    } == {"countersink.diameter", "countersink.angle"}
    assert all(
        requirement["occurrence_ids"] == ["countersinks:1", "countersinks:2", "holes:1", "holes:2"]
        for requirement in (
            requirements[requirement_id] for requirement_id in countersink_requirement_ids
        )
    )


def test_unbound_countersink_fails_closed_as_unexpectedly_missing() -> None:
    evidence = build_recognition_evidence(_mixed_countersinks())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    countersinks = ownership.expected_nested

    assert len(countersinks) == 2
    assert all(
        ownership.status(occurrence) == "unexpectedly_missing" for occurrence in countersinks
    )
    assert (
        tuple(
            occurrence
            for occurrence in ownership.unexpectedly_missing
            if evidence.family(occurrence) == "countersinks"
        )
        == countersinks
    )


def test_grouped_countersinks_share_the_hole_owner_and_member_lineage() -> None:
    ownership = build_drawing(_same_spec_countersinks()).recognition_ownership()

    assert ownership is not None
    evidence = ownership.evidence
    hole_bindings = tuple(
        ownership.binding_for(occurrence)
        for occurrence in evidence.features
        if evidence.family(occurrence) == "holes"
    )
    countersink_bindings = tuple(
        ownership.binding_for(occurrence)
        for occurrence in evidence.features
        if evidence.family(occurrence) == "countersinks"
    )

    assert all(binding is not None for binding in hole_bindings + countersink_bindings)
    assert len({id(binding.feature) for binding in hole_bindings + countersink_bindings}) == 1
    assert tuple(binding.member_index for binding in hole_bindings) == (0, 1)
    assert tuple(binding.member_index for binding in countersink_bindings) == (0, 1)


def test_countersinks_follow_their_exact_hole_through_a_member_specific_pmi_split() -> None:
    part = _same_spec_countersinks()
    evidence = build_recognition_evidence(part)
    builder = RecognitionOwnershipBuilder(evidence)
    holes = tuple(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "holes"
    )
    countersinks = tuple(
        occurrence
        for occurrence in evidence.features
        if evidence.family(occurrence) == "countersinks"
    )
    assert len(holes) == len(countersinks) == 2
    selected_hole = holes[1]
    selected = evidence.record(selected_hole)
    x, y, z = selected.location
    radius = selected.diameter / 2
    pmi = PmiRecord(
        kind="diameter",
        type_code=15,
        value=selected.diameter,
        upper_tol=0.2,
        lower_tol=0.1,
        ref_pts=((x - radius, y, z), (x + radius, y, z)),
        ref_bbox=(
            x - radius - 0.1,
            y - radius - 0.1,
            z - 0.1,
            x + radius + 0.1,
            y + radius + 0.1,
            z + 0.1,
        ),
        dominant_axis="Z",
        label=f"ø{selected.diameter:g} +0.2/-0.1",
        source_id="dimension:one-countersunk-member",
        source_category="dimension",
    )

    model = _build_part_model_from_recognition(
        part,
        evidence.result,
        ownership=builder,
        pmi=(pmi,),
    )
    ownership = builder.snapshot()
    model_feature_ids = {id(feature) for feature in model.features}
    hole_bindings = {
        id(evidence.record(occurrence)): ownership.binding_for(occurrence) for occurrence in holes
    }
    countersink_bindings = {
        id(evidence.record(occurrence)): ownership.binding_for(occurrence)
        for occurrence in countersinks
    }

    assert all(binding is not None for binding in hole_bindings.values())
    assert all(binding is not None for binding in countersink_bindings.values())
    assert len({id(binding.feature) for binding in hole_bindings.values() if binding}) == 2
    for hole in holes:
        hole_record = evidence.record(hole)
        hole_binding = hole_bindings[id(hole_record)]
        countersink_binding = countersink_bindings[id(hole_record.csink)]

        assert hole_binding is not None
        assert countersink_binding is not None
        assert countersink_binding.feature is hole_binding.feature
        assert countersink_binding.member_index == hole_binding.member_index == 0
        assert countersink_binding.disposition == "absorbed"
        assert countersink_binding.reason_code == "countersink_hole_owner"
        assert id(hole_binding.feature) in model_feature_ids
    selected_binding = hole_bindings[id(selected)]
    assert selected_binding is not None
    assert selected_binding.disposition == "represented"
    assert selected_binding.reason_code == "pmi_split_member"
    assert (
        sum(
            requirement.source_ids == ("dimension:one-countersunk-member",)
            for requirement in model.decorations.values()
        )
        == 1
    )
    assert ownership.unexpectedly_missing == ()


def test_an_accepted_second_end_without_a_hole_owner_fails_closed() -> None:
    ownership = build_drawing(_two_ended_countersink(), page="A3").recognition_ownership()

    assert ownership is not None
    evidence = ownership.evidence
    countersinks = tuple(
        occurrence
        for occurrence in evidence.features
        if evidence.family(occurrence) == "countersinks"
    )
    statuses = [ownership.status(occurrence) for occurrence in countersinks]

    assert len(countersinks) == 2
    assert statuses.count("absorbed") == 1
    assert statuses.count("unexpectedly_missing") == 1


def test_nested_binding_requires_its_exact_parent_to_be_bound_first() -> None:
    evidence = build_recognition_evidence(_mixed_countersinks())
    builder = RecognitionOwnershipBuilder(evidence)
    hole = next(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "holes"
    )
    hole_record = evidence.record(hole)

    with pytest.raises(ValueError, match="existing parent owner"):
        builder.absorb_nested(
            hole_record.csink,
            hole_record,
            reason_code="countersink_hole_owner",
        )


def test_nested_binding_rejects_a_different_same_family_parent() -> None:
    evidence = build_recognition_evidence(_mixed_countersinks())
    builder = RecognitionOwnershipBuilder(evidence)
    holes = tuple(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "holes"
    )
    assert len(holes) == 2
    first = evidence.record(holes[0])
    second = evidence.record(holes[1])
    builder.bind(first, object(), reason_code="hole_adapter", member_index=0)
    builder.bind(second, object(), reason_code="hole_adapter", member_index=0)

    with pytest.raises(ValueError, match="does not belong to its exact parent"):
        builder.absorb_nested(
            first.csink,
            second,
            reason_code="countersink_hole_owner",
        )


def test_nested_binding_rejects_wrong_family_and_duplicate_occurrence() -> None:
    evidence = build_recognition_evidence(_mixed_countersinks())
    builder = RecognitionOwnershipBuilder(evidence)
    hole = next(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "holes"
    )
    hole_record = evidence.record(hole)
    feature = object()

    with pytest.raises(ValueError, match="unknown nested ownership reason_code"):
        builder.absorb_nested(hole_record.csink, hole_record, reason_code="future_contract")

    with pytest.raises(ValueError, match="does not match owner family"):
        builder.absorb_nested(
            hole_record.csink,
            hole_record.csink,
            reason_code="countersink_hole_owner",
        )

    builder.bind(hole_record, feature, reason_code="hole_adapter", member_index=0)

    with pytest.raises(ValueError, match="does not match occurrence family"):
        builder.absorb_nested(hole_record, hole_record, reason_code="countersink_hole_owner")

    builder.absorb_nested(
        hole_record.csink,
        hole_record,
        reason_code="countersink_hole_owner",
    )
    with pytest.raises(ValueError, match="already has an IR owner"):
        builder.absorb_nested(
            hole_record.csink,
            hole_record,
            reason_code="countersink_hole_owner",
        )
