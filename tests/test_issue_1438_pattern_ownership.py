"""#1438 — grouped and patterned members retain exact final IR ownership."""

from __future__ import annotations

import pytest
from b123d_recognisers.evidence import build_recognition_evidence
from build123d import Align, Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.model.detect import _build_part_model_from_recognition
from draftwright.pmi import PmiRecord
from draftwright.recognition_ownership import GROUPABLE_FAMILIES, RecognitionOwnershipBuilder

_XYZ_MIN = (Align.CENTER, Align.CENTER, Align.MIN)


def _single_hole():
    return Box(80, 50, 10, align=_XYZ_MIN) - Pos(12, 7, 0) * Cylinder(2, 10, align=_XYZ_MIN)


def _scattered_holes():
    part = Box(80, 60, 10, align=_XYZ_MIN)
    for x, y in ((-20, -10), (15, 12)):
        part -= Pos(x, y, 0) * Cylinder(2, 10, align=_XYZ_MIN)
    return part


def _three_scattered_holes():
    part = Box(100, 80, 10, align=_XYZ_MIN)
    for x, y in ((-25, -12), (5, 16), (27, -17)):
        part -= Pos(x, y, 0) * Cylinder(2, 10, align=_XYZ_MIN)
    return part


def _hole_row():
    part = Box(120, 40, 10, align=_XYZ_MIN)
    for x in (-30, -10, 10, 30):
        part -= Pos(x, 0, 0) * Cylinder(3, 10, align=_XYZ_MIN)
    return part


def _single_slot():
    return Box(60, 40, 20) - Box(30, 8, 20)


def _slot_row():
    part = Box(60, 180, 20)
    for y in (-45, -15, 15, 45):
        part -= Pos(0, y, 0) * Box(30, 8, 20)
    return part


def _single_pocket():
    return Box(60, 40, 20) - Pos(0, 0, 7) * Box(30, 18, 6)


def _pocket_row():
    part = Box(30, 150, 20)
    for y in (-45, -15, 15, 45):
        part -= Pos(0, y, 7) * Box(10, 12, 6)
    return part


def _occurrences(ownership, family):
    return tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == family
    )


def test_groupable_family_roster_is_explicit() -> None:
    assert GROUPABLE_FAMILIES == {"holes", "pockets", "slots"}


def test_each_unpatterned_groupable_family_has_a_direct_final_owner() -> None:
    cases = (
        (_single_hole(), "holes", "hole", "hole_adapter"),
        (_single_slot(), "slots", "slot", "slot_adapter"),
        (_single_pocket(), "pockets", "pocket", "pocket_adapter"),
    )

    for part, family, feature_kind, reason_code in cases:
        drawing = build_drawing(part)
        ownership = drawing.recognition_ownership()
        assert ownership is not None
        (occurrence,) = _occurrences(ownership, family)
        binding = ownership.binding_for(occurrence)

        assert binding is not None
        assert ownership.status(occurrence) == "represented"
        assert binding.reason_code == reason_code
        assert binding.feature.kind == feature_kind
        assert any(binding.feature is feature for feature in drawing.model().features)
        assert not tuple(
            candidate
            for candidate in ownership.unexpectedly_missing
            if ownership.evidence.family(candidate) == family
        )


def test_same_spec_scattered_holes_are_explicitly_absorbed_by_one_group() -> None:
    drawing = build_drawing(_scattered_holes())
    ownership = drawing.recognition_ownership()
    assert ownership is not None
    occurrences = _occurrences(ownership, "holes")
    assert len(occurrences) == 2
    bindings = tuple(ownership.binding_for(occurrence) for occurrence in occurrences)

    assert all(binding is not None for binding in bindings)
    assert all(ownership.status(occurrence) == "absorbed" for occurrence in occurrences)
    assert {binding.reason_code for binding in bindings if binding is not None} == {
        "grouped_hole_member"
    }
    assert {binding.member_index for binding in bindings if binding is not None} == {0, 1}
    assert len({id(binding.feature) for binding in bindings if binding is not None}) == 1
    owner = next(binding.feature for binding in bindings if binding is not None)
    assert owner.kind == "hole"
    assert owner.count == 2
    assert any(owner is feature for feature in drawing.model().features)
    assert ownership.unexpectedly_missing == ()


def test_pattern_members_are_absorbed_by_the_exact_shared_pattern_owner() -> None:
    cases = (
        (_hole_row(), "holes", "pattern", "hole_pattern_member", 4),
        (_slot_row(), "slots", "slot_pattern", "slot_pattern_member", 4),
        (_pocket_row(), "pockets", "pocket_pattern", "pocket_pattern_member", 4),
    )

    for part, family, feature_kind, reason_code, count in cases:
        drawing = build_drawing(part)
        ownership = drawing.recognition_ownership()
        assert ownership is not None
        occurrences = _occurrences(ownership, family)
        assert len(occurrences) == count
        bindings = tuple(ownership.binding_for(occurrence) for occurrence in occurrences)

        assert all(binding is not None for binding in bindings)
        assert all(ownership.status(occurrence) == "absorbed" for occurrence in occurrences)
        assert {binding.reason_code for binding in bindings if binding is not None} == {
            reason_code
        }
        assert {binding.member_index for binding in bindings if binding is not None} == set(
            range(count)
        )
        assert len({id(binding.feature) for binding in bindings if binding is not None}) == 1
        owner = next(binding.feature for binding in bindings if binding is not None)
        assert owner.kind == feature_kind
        assert owner.count == count
        assert any(owner is feature for feature in drawing.model().features)
        assert ownership.unexpectedly_missing == ()


def test_member_specific_pmi_split_rebinds_each_occurrence_to_its_final_owner() -> None:
    part = _three_scattered_holes()
    evidence = build_recognition_evidence(part)
    builder = RecognitionOwnershipBuilder(evidence)
    occurrences = tuple(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "holes"
    )
    assert len(occurrences) == 3
    selected = evidence.record(occurrences[1])
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
            -0.1,
            x + radius + 0.1,
            y + radius + 0.1,
            z + 0.1,
        ),
        dominant_axis="Z",
        label=f"ø{selected.diameter:g} +0.2/-0.1",
        source_id="dimension:one-member",
        source_category="dimension",
    )

    model = _build_part_model_from_recognition(
        part,
        evidence.result,
        ownership=builder,
        pmi=(pmi,),
    )
    ownership = builder.snapshot()
    bindings = tuple(ownership.binding_for(occurrence) for occurrence in occurrences)

    assert all(binding is not None for binding in bindings)
    assert [ownership.status(occurrence) for occurrence in occurrences].count("represented") == 1
    assert [ownership.status(occurrence) for occurrence in occurrences].count("absorbed") == 2
    assert {binding.reason_code for binding in bindings if binding is not None} == {
        "grouped_hole_member",
        "pmi_split_member",
    }
    assert len({id(binding.feature) for binding in bindings if binding is not None}) == 2
    assert sorted({binding.feature.count for binding in bindings if binding is not None}) == [1, 2]
    assert all(
        any(binding.feature is feature for feature in model.features)
        for binding in bindings
        if binding is not None
    )
    assert (
        sum(
            requirement.source_ids == ("dimension:one-member",)
            for requirement in model.decorations.values()
        )
        == 1
    )
    assert ownership.unexpectedly_missing == ()


def test_unbound_groupable_occurrence_fails_closed_as_unexpectedly_missing() -> None:
    evidence = build_recognition_evidence(_single_hole())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    (occurrence,) = ownership.expected_groupable

    assert ownership.status(occurrence) == "unexpectedly_missing"
    assert ownership.unexpectedly_missing == (occurrence,)


def test_absorption_rejects_unknown_reasons_repeats_and_foreign_records() -> None:
    evidence = build_recognition_evidence(_scattered_holes())
    occurrences = RecognitionOwnershipBuilder(evidence).snapshot().expected_groupable
    records = tuple(evidence.record(occurrence) for occurrence in occurrences)

    with pytest.raises(ValueError, match="unknown absorbed"):
        RecognitionOwnershipBuilder(evidence).absorb(records, object(), reason_code="invented")
    with pytest.raises(ValueError, match="unknown absorbed"):
        RecognitionOwnershipBuilder(evidence).absorb(
            records, object(), reason_code=" grouped_hole_member "
        )
    with pytest.raises(ValueError, match="repeats"):
        RecognitionOwnershipBuilder(evidence).absorb(
            (records[0], records[0]), object(), reason_code="grouped_hole_member"
        )
    foreign = build_recognition_evidence(_scattered_holes())
    with pytest.raises(ValueError, match="does not belong"):
        RecognitionOwnershipBuilder(evidence).absorb(
            (foreign.record(foreign.features[0]),),
            object(),
            reason_code="grouped_hole_member",
        )

    with pytest.raises(ValueError, match="does not match occurrence family"):
        RecognitionOwnershipBuilder(evidence).absorb(
            records,
            object(),
            reason_code="slot_pattern_member",
        )
    with pytest.raises(ValueError, match="does not match occurrence family"):
        RecognitionOwnershipBuilder(evidence).bind(
            records[0],
            object(),
            reason_code="slot_adapter",
        )
    with pytest.raises(ValueError, match="requires a direct occurrence family"):
        RecognitionOwnershipBuilder(evidence).bind(records[0], object())


def test_builder_guards_invalid_group_and_lowering_lineage() -> None:
    evidence = build_recognition_evidence(_scattered_holes())
    occurrences = RecognitionOwnershipBuilder(evidence).snapshot().expected_groupable
    records = tuple(evidence.record(occurrence) for occurrence in occurrences)

    with pytest.raises(ValueError, match="unknown represented"):
        RecognitionOwnershipBuilder(evidence).bind(records[0], object(), reason_code="invented")
    with pytest.raises(ValueError, match="unknown represented"):
        RecognitionOwnershipBuilder(evidence).bind(
            records[0], object(), reason_code=" hole_adapter "
        )
    with pytest.raises(ValueError, match="non-negative"):
        RecognitionOwnershipBuilder(evidence).bind(
            records[0], object(), reason_code="hole_adapter", member_index=-1
        )
    with pytest.raises(ValueError, match="at least one"):
        RecognitionOwnershipBuilder(evidence).absorb(
            (), object(), reason_code="grouped_hole_member"
        )

    duplicate_occurrence = RecognitionOwnershipBuilder(evidence)
    duplicate_occurrence.bind(records[0], object(), reason_code="hole_adapter")
    with pytest.raises(ValueError, match="occurrence already"):
        duplicate_occurrence.absorb(records, object(), reason_code="grouped_hole_member")

    duplicate_owner = RecognitionOwnershipBuilder(evidence)
    owner = object()
    duplicate_owner.bind(records[0], owner, reason_code="hole_adapter")
    with pytest.raises(ValueError, match="IR feature already"):
        duplicate_owner.absorb((records[1],), owner, reason_code="grouped_hole_member")

    misaligned = RecognitionOwnershipBuilder(evidence)
    source = object()
    misaligned.absorb(records, source, reason_code="grouped_hole_member")
    with pytest.raises(ValueError, match="must align"):
        misaligned.remap_feature(source, (object(), object()), ((0,),))
    with pytest.raises(ValueError, match="repeat a source member"):
        misaligned.remap_feature(source, (object(), object()), ((0,), (0,)))

    incomplete = RecognitionOwnershipBuilder(evidence)
    source = object()
    incomplete.absorb(records, source, reason_code="grouped_hole_member")
    replacement = object()
    incomplete.remap_feature(source, (replacement,), ((0,),))
    ownership = incomplete.snapshot()

    assert ownership.binding_for(occurrences[0]).feature is replacement
    assert ownership.status(occurrences[1]) == "unexpectedly_missing"


def test_remap_rejects_an_owner_collision_without_mutating_the_ledger() -> None:
    evidence = build_recognition_evidence(_scattered_holes())
    occurrences = RecognitionOwnershipBuilder(evidence).snapshot().expected_groupable
    records = tuple(evidence.record(occurrence) for occurrence in occurrences)

    for member_groups in (None, ((0,),)):
        builder = RecognitionOwnershipBuilder(evidence)
        source = object()
        occupied = object()
        builder.bind(records[0], source, reason_code="hole_adapter", member_index=0)
        builder.bind(records[1], occupied, reason_code="hole_adapter", member_index=0)

        with pytest.raises(ValueError, match="already owns"):
            builder.remap_feature(source, (occupied,), member_groups)

        ownership = builder.snapshot()
        assert ownership.binding_for(occurrences[0]).feature is source
        assert ownership.binding_for(occurrences[1]).feature is occupied


def test_member_remap_requires_distinct_replacement_objects() -> None:
    evidence = build_recognition_evidence(_scattered_holes())
    builder = RecognitionOwnershipBuilder(evidence)
    occurrences = builder.snapshot().expected_groupable
    records = tuple(evidence.record(occurrence) for occurrence in occurrences)
    source = object()
    builder.absorb(records, source, reason_code="grouped_hole_member")
    repeated = object()

    with pytest.raises(ValueError, match="distinct replacement"):
        builder.remap_feature(source, (repeated, repeated), ((0,), (1,)))

    assert all(builder.snapshot().binding_for(item).feature is source for item in occurrences)
