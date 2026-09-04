"""#1438 — accepted round bosses retain their exact consumer outcome."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from b123d_recognisers.evidence import build_recognition_evidence
from build123d import Box, Compound, Cylinder, Pos, import_step

from draftwright import build_drawing
from draftwright.recognition_ownership import OccurrenceBinding, RecognitionOwnershipBuilder

FIXTURES = Path(__file__).parent / "fixtures" / "evaluation"


def _occurrences(ownership, family: str):
    return tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == family
    )


def _two_equal_bosses():
    return Box(100, 60, 10) + Pos(-25, 0, 9) * Cylinder(8, 8) + Pos(25, 0, 9) * Cylinder(8, 8)


def _stepped_shaft():
    return Cylinder(20, 60) + Pos(0, 0, 45) * Cylinder(30, 30)


def _boss_key(record) -> tuple[float, float, float]:
    axis_index = max(range(3), key=lambda index: abs(record.axis[index]))
    lo, hi = sorted(
        (
            float(record.location[axis_index]),
            float(record.location[axis_index] - record.axis[axis_index] * record.height),
        )
    )
    return record.diameter, lo, hi


def test_a_plain_prismatic_boss_is_represented_by_its_exact_feature() -> None:
    drawing = build_drawing(Box(60, 60, 10) + Pos(0, 0, 9) * Cylinder(12, 8))
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    (occurrence,) = _occurrences(ownership, "bosses")
    binding = ownership.binding_for(occurrence)

    assert binding is not None
    assert binding.disposition == "represented"
    assert binding.reason_code == "boss_adapter"
    assert binding.feature.kind == "boss"
    assert any(binding.feature is feature for feature in drawing.model().features)
    assert ownership.unexpectedly_missing == ()


def test_report_marks_a_supported_family_without_a_requirement_ledger_for_attention() -> None:
    drawing = build_drawing(Box(60, 60, 10) + Pos(0, 0, 9) * Cylinder(12, 8))
    report = drawing.report()
    bosses = [
        occurrence
        for occurrence in report["recognition"]["occurrences"]
        if occurrence["family"] == "bosses"
    ]

    assert bosses
    assert all(
        occurrence["requirements"] == {"coverage": "not-projected", "ids": []}
        for occurrence in bosses
    )
    assert report["status"] == "needs-attention"


def test_equal_diameter_bosses_retain_occurrence_membership_in_one_existing_owner() -> None:
    drawing = build_drawing(_two_equal_bosses())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrences = _occurrences(ownership, "bosses")
    bindings = tuple(ownership.binding_for(occurrence) for occurrence in occurrences)

    assert len(occurrences) == 2
    assert all(binding is not None for binding in bindings)
    assert all(
        binding is not None
        and binding.disposition == "absorbed"
        and binding.reason_code == "boss_diameter_group_member"
        for binding in bindings
    )
    assert bindings[0].feature is bindings[1].feature
    assert tuple(binding.member_index for binding in bindings if binding is not None) == (0, 1)
    assert sum(feature.kind == "boss" for feature in drawing.model().features) == 1
    assert ownership.unexpectedly_missing == ()


def test_turned_bosses_follow_their_exact_step_owners() -> None:
    drawing = build_drawing(_stepped_shaft())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    boss_occurrences = _occurrences(ownership, "bosses")
    step_occurrences = _occurrences(ownership, "turned_steps")
    boss_bindings = tuple(ownership.binding_for(occurrence) for occurrence in boss_occurrences)
    step_bindings = tuple(ownership.binding_for(occurrence) for occurrence in step_occurrences)

    assert len(boss_bindings) == len(step_bindings) == 2
    assert all(
        binding is not None
        and binding.disposition == "absorbed"
        and binding.reason_code == "boss_turned_step_owner"
        and binding.feature.kind == "step"
        for binding in boss_bindings
    )
    step_owners = {
        (record.diameter, record.lo, record.hi): binding.feature
        for occurrence, binding in zip(step_occurrences, step_bindings, strict=True)
        if binding is not None
        for record in (ownership.evidence.record(occurrence),)
    }
    assert all(
        binding is not None
        and binding.feature is step_owners[_boss_key(ownership.evidence.record(occurrence))]
        for occurrence, binding in zip(boss_occurrences, boss_bindings, strict=True)
    )
    assert ownership.unexpectedly_missing == ()


def test_groove_floor_boss_follows_the_absorbed_step_to_the_exact_groove() -> None:
    drawing = build_drawing(import_step(FIXTURES / "groove-narrow.step"))
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    boss_bindings = tuple(
        ownership.binding_for(occurrence) for occurrence in _occurrences(ownership, "bosses")
    )
    groove_binding = ownership.binding_for(_occurrences(ownership, "grooves")[0])

    assert groove_binding is not None
    assert len(boss_bindings) == 3
    assert all(
        binding is not None
        and binding.disposition == "absorbed"
        and binding.reason_code == "boss_turned_step_owner"
        for binding in boss_bindings
    )
    floor_occurrence = next(
        occurrence
        for occurrence in _occurrences(ownership, "bosses")
        if ownership.evidence.record(occurrence).diameter == pytest.approx(18.0)
    )
    floor_binding = ownership.binding_for(floor_occurrence)
    assert floor_binding is not None
    assert floor_binding.feature is groove_binding.feature
    assert floor_binding.reason_code == "boss_turned_step_owner"
    assert all(
        binding is not None and binding.feature.kind == "step"
        for occurrence, binding in zip(
            _occurrences(ownership, "bosses"), boss_bindings, strict=True
        )
        if occurrence is not floor_occurrence
    )
    assert ownership.unexpectedly_missing == ()


def test_profile_gate_fallback_binds_the_floor_boss_directly_to_its_groove() -> None:
    part = Cylinder(10, 40) - Pos(0, 0, 5) * (Cylinder(10, 2) - Cylinder(8, 2))
    part += Box(40, 12, 4)
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    ownership = drawing.recognition_ownership()

    assert recognition is not None
    assert recognition.turned_profiles == ()
    assert ownership is not None
    floor_occurrence = next(
        occurrence
        for occurrence in _occurrences(ownership, "bosses")
        if ownership.evidence.record(occurrence).diameter == pytest.approx(16.0)
    )
    floor_binding = ownership.binding_for(floor_occurrence)
    groove_binding = ownership.binding_for(_occurrences(ownership, "grooves")[0])

    assert floor_binding is not None
    assert groove_binding is not None
    assert floor_binding.disposition == "absorbed"
    assert floor_binding.reason_code == "boss_groove_owner"
    assert floor_binding.feature is groove_binding.feature
    assert floor_binding.feature.kind == "groove"
    assert ownership.unexpectedly_missing == ()


def test_a_groove_representative_cannot_claim_a_distinct_equal_diameter_boss() -> None:
    part = Cylinder(10, 40) - Pos(0, 0, 5) * (Cylinder(10, 2) - Cylinder(8, 2))
    part += Box(40, 12, 4)
    part += Pos(24, 0, 0) * Cylinder(8, 8, rotation=(0, 90, 0))
    drawing = build_drawing(part)
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    equal_diameter = tuple(
        occurrence
        for occurrence in _occurrences(ownership, "bosses")
        if ownership.evidence.record(occurrence).diameter == pytest.approx(16.0)
    )
    floor = next(
        occurrence
        for occurrence in equal_diameter
        if ownership.evidence.record(occurrence).axis == pytest.approx((0.0, 0.0, 1.0))
    )
    cross_axis = next(occurrence for occurrence in equal_diameter if occurrence is not floor)
    floor_binding = ownership.binding_for(floor)

    assert floor_binding is not None
    assert floor_binding.reason_code == "boss_groove_owner"
    assert floor_binding.feature.kind == "groove"
    assert ownership.binding_for(cross_axis) is None
    assert ownership.status(cross_axis) == "unexpectedly_missing"
    assert cross_axis in ownership.unexpectedly_missing


def test_two_coaxial_bosses_competing_for_one_step_both_fail_closed() -> None:
    part = Compound(children=[_stepped_shaft(), Cylinder(20, 60)])
    ownership = build_drawing(part).recognition_ownership()

    assert ownership is not None
    competing = tuple(
        occurrence
        for occurrence in _occurrences(ownership, "bosses")
        if ownership.evidence.record(occurrence).diameter == pytest.approx(40.0)
    )

    assert len(competing) == 2
    assert all(ownership.status(occurrence) == "unexpectedly_missing" for occurrence in competing)


def test_one_boss_matching_two_nearby_profile_steps_fails_closed() -> None:
    part = Compound(children=[_stepped_shaft(), Pos(0.25, 0, 0) * _stepped_shaft()])
    ownership = build_drawing(part).recognition_ownership()

    assert ownership is not None
    bosses = _occurrences(ownership, "bosses")

    assert len(bosses) == 4
    assert all(ownership.status(occurrence) == "unexpectedly_missing" for occurrence in bosses)


def test_two_coaxial_bosses_competing_for_one_groove_both_fail_closed() -> None:
    base = Cylinder(10, 40) - Pos(0, 0, 5) * (Cylinder(10, 2) - Cylinder(8, 2))
    base += Box(40, 12, 4)
    part = Compound(children=[base, Pos(0, 0, 60) * Cylinder(8, 8)])
    ownership = build_drawing(part).recognition_ownership()

    assert ownership is not None
    competing = tuple(
        occurrence
        for occurrence in _occurrences(ownership, "bosses")
        if ownership.evidence.record(occurrence).diameter == pytest.approx(16.0)
    )

    assert len(competing) == 2
    assert all(ownership.status(occurrence) == "unexpectedly_missing" for occurrence in competing)


def test_step_route_precedes_a_disconnected_direct_route_to_the_same_groove() -> None:
    main = import_step(FIXTURES / "groove-narrow.step")
    remote = Pos(0, 0, 40) * (Cylinder(9, 8) + Box(30, 8, 3))
    ownership = build_drawing(Compound(children=[main, remote])).recognition_ownership()

    assert ownership is not None
    diameter_18 = tuple(
        occurrence
        for occurrence in _occurrences(ownership, "bosses")
        if ownership.evidence.record(occurrence).diameter == pytest.approx(18.0)
    )
    floor = next(
        occurrence
        for occurrence in diameter_18
        if ownership.evidence.record(occurrence).height == pytest.approx(1.0)
    )
    remote_boss = next(occurrence for occurrence in diameter_18 if occurrence is not floor)
    floor_binding = ownership.binding_for(floor)
    groove_binding = ownership.binding_for(_occurrences(ownership, "grooves")[0])

    assert floor_binding is not None
    assert groove_binding is not None
    assert floor_binding.reason_code == "boss_turned_step_owner"
    assert floor_binding.feature is groove_binding.feature
    assert ownership.binding_for(remote_boss) is None
    assert ownership.status(remote_boss) == "unexpectedly_missing"


def test_unbound_boss_fails_closed_as_unexpectedly_missing() -> None:
    evidence = build_recognition_evidence(_two_equal_bosses())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    occurrences = _occurrences(ownership, "bosses")

    assert ownership.expected_conditional == occurrences
    assert all(
        ownership.status(occurrence) == "unexpectedly_missing" for occurrence in occurrences
    )
    assert ownership.unexpectedly_missing == occurrences


def test_chained_boss_ownership_requires_an_exact_owned_same_run_occurrence() -> None:
    evidence = build_recognition_evidence(_stepped_shaft())
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    boss = evidence.record(_occurrences(ownership, "bosses")[0])
    step = evidence.record(_occurrences(ownership, "turned_steps")[0])

    with pytest.raises(ValueError, match="requires an existing exact owner"):
        builder.absorb_via(boss, step, reason_code="boss_turned_step_owner")

    foreign = build_recognition_evidence(_stepped_shaft())
    foreign_step = foreign.record(
        _occurrences(RecognitionOwnershipBuilder(foreign).snapshot(), "turned_steps")[0]
    )
    with pytest.raises(ValueError, match="does not belong"):
        builder.absorb_via(boss, foreign_step, reason_code="boss_turned_step_owner")


def test_chained_boss_ownership_rejects_wrong_reason_families_and_duplicates() -> None:
    evidence = build_recognition_evidence(_stepped_shaft())
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    boss = evidence.record(_occurrences(ownership, "bosses")[0])
    step = evidence.record(_occurrences(ownership, "turned_steps")[0])

    with pytest.raises(ValueError, match="unknown chained ownership reason_code"):
        builder.absorb_via(boss, step, reason_code="not-a-reason")
    with pytest.raises(ValueError, match="does not match occurrence family"):
        builder.absorb_via(step, step, reason_code="boss_turned_step_owner")
    with pytest.raises(ValueError, match="does not match owner family"):
        builder.absorb_via(boss, boss, reason_code="boss_turned_step_owner")

    builder.bind(step, object(), reason_code="turned_step_adapter")
    builder.absorb_via(boss, step, reason_code="boss_turned_step_owner")
    with pytest.raises(ValueError, match="already has an IR owner"):
        builder.absorb_via(boss, step, reason_code="boss_turned_step_owner")


def test_two_bosses_cannot_claim_the_same_intermediate_step_occurrence() -> None:
    evidence = build_recognition_evidence(_stepped_shaft())
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    bosses = tuple(evidence.record(item) for item in _occurrences(ownership, "bosses"))
    step = evidence.record(_occurrences(ownership, "turned_steps")[0])

    builder.bind(step, object(), reason_code="turned_step_adapter")
    builder.absorb_via(bosses[0], step, reason_code="boss_turned_step_owner")
    with pytest.raises(ValueError, match="owner occurrence already has a dependent"):
        builder.absorb_via(bosses[1], step, reason_code="boss_turned_step_owner")


def test_two_bosses_cannot_claim_the_same_intermediate_groove_occurrence() -> None:
    evidence = build_recognition_evidence(import_step(FIXTURES / "groove-narrow.step"))
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    bosses = tuple(evidence.record(item) for item in _occurrences(ownership, "bosses"))
    groove = evidence.record(_occurrences(ownership, "grooves")[0])

    builder.bind(groove, object())
    builder.absorb_via(bosses[0], groove, reason_code="boss_groove_owner")
    with pytest.raises(ValueError, match="owner occurrence already has a dependent"):
        builder.absorb_via(bosses[1], groove, reason_code="boss_groove_owner")


def test_two_chain_paths_cannot_claim_the_same_final_groove_owner() -> None:
    evidence = build_recognition_evidence(import_step(FIXTURES / "groove-narrow.step"))
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    boss_occurrences = _occurrences(ownership, "bosses")
    floor_boss = next(
        evidence.record(occurrence)
        for occurrence in boss_occurrences
        if evidence.record(occurrence).diameter == pytest.approx(18.0)
    )
    other_boss = next(
        evidence.record(occurrence)
        for occurrence in boss_occurrences
        if evidence.record(occurrence) is not floor_boss
    )
    floor_step = next(
        evidence.record(occurrence)
        for occurrence in _occurrences(ownership, "turned_steps")
        if evidence.record(occurrence).diameter == pytest.approx(18.0)
    )
    groove = evidence.record(_occurrences(ownership, "grooves")[0])
    final_owner = SimpleNamespace(kind="groove")

    builder.bind(groove, final_owner)
    builder.absorb_into(floor_step, final_owner, reason_code="turned_step_groove_owner")
    builder.absorb_via(floor_boss, floor_step, reason_code="boss_turned_step_owner")
    with pytest.raises(ValueError, match="final IR owner already has a dependent"):
        builder.absorb_via(other_boss, groove, reason_code="boss_groove_owner")


def test_removed_step_chain_releases_its_intermediate_occurrence() -> None:
    evidence = build_recognition_evidence(_stepped_shaft())
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    boss_occurrence = _occurrences(ownership, "bosses")[0]
    boss = evidence.record(boss_occurrence)
    step = evidence.record(_occurrences(ownership, "turned_steps")[0])
    first_owner = object()

    builder.bind(step, first_owner, reason_code="turned_step_adapter")
    builder.absorb_via(boss, step, reason_code="boss_turned_step_owner")
    builder.remap_feature(first_owner, ())

    replacement_owner = object()
    builder.bind(step, replacement_owner, reason_code="turned_step_adapter")
    builder.absorb_via(boss, step, reason_code="boss_turned_step_owner")
    binding = builder.snapshot().binding_for(boss_occurrence)
    assert binding is not None
    assert binding.feature is replacement_owner


def test_remapped_step_chain_retains_its_intermediate_reservation() -> None:
    evidence = build_recognition_evidence(_stepped_shaft())
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    boss_occurrences = _occurrences(ownership, "bosses")
    bosses = tuple(evidence.record(occurrence) for occurrence in boss_occurrences)
    step_occurrence = _occurrences(ownership, "turned_steps")[0]
    step = evidence.record(step_occurrence)
    first_owner = object()

    builder.bind(step, first_owner, reason_code="turned_step_adapter")
    builder.absorb_via(bosses[0], step, reason_code="boss_turned_step_owner")
    replacement_owner = object()
    builder.remap_feature(first_owner, (replacement_owner,))

    binding = builder.snapshot().binding_for(boss_occurrences[0])
    assert binding is not None
    assert binding.feature is replacement_owner
    assert binding.via_occurrence is step_occurrence
    with pytest.raises(ValueError, match="owner occurrence already has a dependent"):
        builder.absorb_via(bosses[1], step, reason_code="boss_turned_step_owner")


def test_removed_groove_chain_releases_its_intermediate_occurrence() -> None:
    evidence = build_recognition_evidence(import_step(FIXTURES / "groove-narrow.step"))
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    boss_occurrence = _occurrences(ownership, "bosses")[0]
    boss = evidence.record(boss_occurrence)
    groove = evidence.record(_occurrences(ownership, "grooves")[0])
    first_owner = object()

    builder.bind(groove, first_owner)
    builder.absorb_via(boss, groove, reason_code="boss_groove_owner")
    builder.remap_feature(first_owner, ())

    replacement_owner = object()
    builder.bind(groove, replacement_owner)
    builder.absorb_via(boss, groove, reason_code="boss_groove_owner")
    binding = builder.snapshot().binding_for(boss_occurrence)
    assert binding is not None
    assert binding.feature is replacement_owner


def test_chained_binding_requires_intermediate_lineage_in_both_directions() -> None:
    evidence = build_recognition_evidence(_stepped_shaft())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    boss_occurrence = _occurrences(ownership, "bosses")[0]
    step_occurrence = _occurrences(ownership, "turned_steps")[0]

    with pytest.raises(ValueError, match="require exact intermediate lineage"):
        OccurrenceBinding(
            boss_occurrence,
            object(),
            disposition="absorbed",
            reason_code="boss_turned_step_owner",
        )
    with pytest.raises(ValueError, match="require exact intermediate lineage"):
        OccurrenceBinding(boss_occurrence, object(), via_occurrence=step_occurrence)
