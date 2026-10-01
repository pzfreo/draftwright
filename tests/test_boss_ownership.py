"""#1438 — accepted round bosses retain their exact consumer outcome."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Compound, Cone, Cylinder, Pos, import_step
from quiddity import BossRecord
from quiddity.evidence import build_recognition_evidence

from draftwright import build_drawing
from draftwright.linting.boss_coverage import boss_requirement_outcomes
from draftwright.model.detect import _records_share_defining_target
from draftwright.recognition_ownership import OccurrenceBinding, RecognitionOwnershipBuilder
from draftwright.registry import AnnotationRegistry

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


def _boss_and_step_evidence():
    # The current provider excludes bosses from a fully recognised turned profile.
    # The builder contract needs both record families in one evidence authority.
    evidence = build_recognition_evidence(
        Compound(children=[_two_equal_bosses(), Pos(250, 0, 0) * _stepped_shaft()])
    )
    assert len(evidence.result.bosses) == len(evidence.result.turned_steps) == 2
    return evidence


def _boss_and_groove_evidence():
    evidence = build_recognition_evidence(
        Compound(
            children=[
                _two_equal_bosses(),
                Pos(250, 0, 0) * import_step(FIXTURES / "groove-narrow.step"),
            ]
        )
    )
    assert len(evidence.result.bosses) == 2
    assert len(evidence.result.grooves) == 1
    return evidence


def test_evidence_identity_distinguishes_foreign_from_ambiguous_records() -> None:
    boss = object()
    step = object()
    unique_step = object()
    refs = (object(), object(), object(), object(), object())
    families = dict(
        zip(
            refs,
            ("bosses", "bosses", "turned_steps", "turned_steps", "turned_steps"),
            strict=True,
        )
    )
    records = {refs[0]: boss, refs[1]: boss, refs[2]: step, refs[3]: step, refs[4]: unique_step}
    evidence = SimpleNamespace(
        features=refs,
        family=families.__getitem__,
        record=records.__getitem__,
        defining_faces=lambda _occurrence: frozenset({"face:1"}),
    )

    assert (
        _records_share_defining_target(evidence, "bosses", object(), "turned_steps", unique_step)
        is None
    )
    assert (
        _records_share_defining_target(evidence, "bosses", boss, "turned_steps", object()) is False
    )
    assert (
        _records_share_defining_target(evidence, "bosses", object(), "turned_steps", step) is False
    )


def _tapered_transition_shaft():
    """A profile whose boss and step records disagree about one exact target's extent."""

    def cylinder(radius, lo, hi):
        return Pos(0, 0, lo) * Cylinder(
            radius,
            hi - lo,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )

    def transition(lo_radius, hi_radius, lo, hi):
        return Pos(0, 0, lo) * Cone(
            lo_radius,
            hi_radius,
            hi - lo,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )

    return (
        cylinder(30.5, -35, -20)
        + cylinder(27.5, -20, -5)
        + transition(27.5, 44, -5, -3)
        + cylinder(44, -3, 10)
        + transition(44, 80, 10, 13)
        + cylinder(80, 13, 35)
    )


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


def test_report_projects_each_plain_boss_requirement() -> None:
    drawing = build_drawing(Box(60, 60, 10) + Pos(0, 0, 9) * Cylinder(12, 8))
    report = drawing.report()
    bosses = [
        occurrence
        for occurrence in report["recognition"]["occurrences"]
        if occurrence["family"] == "bosses"
    ]

    assert bosses
    assert all(occurrence["requirements"]["coverage"] == "ledger" for occurrence in bosses)
    requirements = {
        requirement["parameter_id"]: requirement
        for requirement in report["recognition"]["requirements"]
        if requirement["family"] == "bosses"
    }
    assert set(requirements) == {"boss.diameter", "boss_height.length"}
    assert {requirement["state"] for requirement in requirements.values()} == {"placed"}


def test_boss_ledger_fails_closed_when_its_exact_owner_leaves_final_ir() -> None:
    drawing = build_drawing(Box(60, 60, 10) + Pos(0, 0, 9) * Cylinder(12, 8))
    evidence = drawing.recognition_evidence()
    ownership = drawing.recognition_ownership()

    outcomes = boss_requirement_outcomes(
        drawing.recognition(),
        (),
        drawing.registry,
        (),
        evidence=evidence,
        ownership=ownership,
    )

    assert len(outcomes) == 1
    assert outcomes[0].parameter_id == "?"
    assert outcomes[0].state == "unverifiable"
    assert outcomes[0].requirement_count == 2
    assert outcomes[0].source_records == (evidence.result.bosses[0],)


def test_boss_ledger_fails_closed_when_conversion_never_bound_the_occurrence() -> None:
    recognition = object()
    reference = object()
    source = BossRecord(
        axis=(0.0, 0.0, 1.0),
        location=(0.0, 0.0, 8.0),
        diameter=16.0,
        height=8.0,
    )
    evidence = SimpleNamespace(
        result=recognition,
        features=(reference,),
        family=lambda occurrence: "bosses" if occurrence is reference else "foreign",
        record=lambda occurrence: source if occurrence is reference else None,
    )
    ownership = SimpleNamespace(
        evidence=evidence,
        binding_for=lambda occurrence: None,
    )

    outcomes = boss_requirement_outcomes(
        recognition,
        (),
        AnnotationRegistry(),
        evidence=evidence,
        ownership=ownership,
    )

    assert len(outcomes) == 1
    assert outcomes[0].parameter_id == "?"
    assert outcomes[0].state == "unverifiable"
    assert outcomes[0].requirement_count == 2
    assert outcomes[0].source_records == (source,)


def test_boss_ledger_fails_closed_on_a_nonprincipal_provider_axis() -> None:
    recognition = object()
    reference = object()
    source = BossRecord(
        axis=(1.0, 1.0, 0.0),
        location=(0.0, 0.0, 8.0),
        diameter=16.0,
        height=8.0,
    )
    evidence = SimpleNamespace(
        result=recognition,
        features=(reference,),
        family=lambda occurrence: "bosses" if occurrence is reference else "foreign",
        record=lambda occurrence: source if occurrence is reference else None,
    )
    ownership = SimpleNamespace(evidence=evidence, binding_for=lambda occurrence: None)

    outcomes = boss_requirement_outcomes(
        recognition,
        (),
        AnnotationRegistry(),
        evidence=evidence,
        ownership=ownership,
    )

    assert len(outcomes) == 1
    assert outcomes[0].parameter_id == "?"
    assert outcomes[0].state == "unverifiable"
    assert outcomes[0].requirement_count == 2
    assert outcomes[0].source_at is None
    assert outcomes[0].source_records == (source,)


def test_equal_diameter_bosses_keep_distinct_owners_and_one_counted_callout() -> None:
    drawing = build_drawing(_two_equal_bosses())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrences = _occurrences(ownership, "bosses")
    bindings = tuple(ownership.binding_for(occurrence) for occurrence in occurrences)

    assert len(occurrences) == 2
    assert all(binding is not None for binding in bindings)
    assert all(
        binding is not None
        and binding.disposition == "represented"
        and binding.reason_code == "boss_adapter"
        for binding in bindings
    )
    assert bindings[0].feature is not bindings[1].feature
    assert sum(feature.kind == "boss" for feature in drawing.model().features) == 2
    diameter_names = [name for name in drawing.annotations() if name.startswith("m_bossdia_")]
    assert len(diameter_names) == 1
    diameter = drawing.get_annotation(diameter_names[0])
    assert diameter.label == "2× ø16"
    assert diameter.covers_count == 2
    assert {
        measurement.parameter for measurement in drawing.registry.measurement_of(diameter_names[0])
    } == {"boss.diameter", "grouping.count"}
    boss_requirements = [
        requirement
        for requirement in drawing.report()["recognition"]["requirements"]
        if requirement["family"] == "bosses"
    ]
    assert [requirement["parameter_id"] for requirement in boss_requirements].count(
        "boss_height.length"
    ) == 2
    count_requirement = next(
        requirement
        for requirement in boss_requirements
        if requirement["parameter_id"] == "grouping.count"
    )
    assert count_requirement["state"] == "placed"
    assert count_requirement["annotations"] == diameter_names
    assert len(count_requirement["occurrence_ids"]) == 2
    assert ownership.unexpectedly_missing == ()


def test_boss_count_requires_one_truthful_counted_carrier() -> None:
    drawing = build_drawing(_two_equal_bosses())
    (name,) = [name for name in drawing.annotations() if name.startswith("m_bossdia_")]

    drawing.get_annotation(name).covers_count = 1

    count_requirement = next(
        requirement
        for requirement in drawing.report()["recognition"]["requirements"]
        if requirement["family"] == "bosses" and requirement["parameter_id"] == "grouping.count"
    )
    assert count_requirement["state"] == "missing"


def test_turned_steps_have_no_redundant_boss_occurrences() -> None:
    drawing = build_drawing(_stepped_shaft())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    boss_occurrences = _occurrences(ownership, "bosses")
    step_occurrences = _occurrences(ownership, "turned_steps")
    boss_bindings = tuple(ownership.binding_for(occurrence) for occurrence in boss_occurrences)
    step_bindings = tuple(ownership.binding_for(occurrence) for occurrence in step_occurrences)

    assert boss_bindings == ()
    assert len(step_bindings) == 2
    assert all(
        binding is not None
        and binding.disposition == "represented"
        and binding.reason_code == "turned_step_adapter"
        and binding.feature.kind == "step"
        for binding in step_bindings
    )
    assert ownership.unexpectedly_missing == ()


def test_tapered_target_is_dimensioned_once_without_a_redundant_boss() -> None:
    drawing = build_drawing(_tapered_transition_shaft())
    evidence = drawing.recognition_evidence()
    ownership = drawing.recognition_ownership()

    assert evidence is not None
    assert ownership is not None
    step_occurrence = next(
        occurrence
        for occurrence in _occurrences(ownership, "turned_steps")
        if evidence.record(occurrence).diameter == pytest.approx(88.0)
    )
    step = evidence.record(step_occurrence)
    assert evidence.defining_faces(step_occurrence)
    assert not any(
        evidence.record(occurrence).diameter == pytest.approx(88.0)
        for occurrence in _occurrences(ownership, "bosses")
    )
    step_binding = ownership.binding_for(step_occurrence)
    assert step_binding is not None
    assert step_binding.reason_code == "turned_step_adapter"

    diameter_88 = [
        name
        for name in drawing.annotations()
        if getattr(drawing.get_annotation(name), "label", None) == "ø88"
    ]
    assert len(diameter_88) == 1
    assert set(drawing.registry.features_of(diameter_88[0])) == {step_binding.feature}

    report = drawing.report()["recognition"]["occurrences"]
    assert not any(
        row["family"] == "bosses" and row["record"]["diameter"] == pytest.approx(88.0)
        for row in report
    )
    step_report_row = next(
        row
        for row in report
        if row["family"] == "turned_steps"
        and row["record"]["diameter"] == pytest.approx(step.diameter)
    )
    assert step_report_row["disposition"] == "represented"
    assert len(step_report_row["owners"]) == 1
    assert step_report_row["owners"][0]["kind"] == "step"


def test_groove_floor_step_has_no_redundant_boss_owner() -> None:
    drawing = build_drawing(import_step(FIXTURES / "groove-narrow.step"))
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    assert _occurrences(ownership, "bosses") == ()
    groove_binding = ownership.binding_for(_occurrences(ownership, "grooves")[0])

    assert groove_binding is not None
    floor_occurrence = next(
        occurrence
        for occurrence in _occurrences(ownership, "turned_steps")
        if ownership.evidence.record(occurrence).diameter == pytest.approx(18.0)
    )
    floor_binding = ownership.binding_for(floor_occurrence)
    assert floor_binding is not None
    assert floor_binding.feature is groove_binding.feature
    assert floor_binding.reason_code == "turned_step_groove_owner"
    assert all(
        (binding := ownership.binding_for(occurrence)) is not None
        and binding.feature.kind == "step"
        for occurrence in _occurrences(ownership, "turned_steps")
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


def test_a_groove_member_does_not_erase_a_distinct_equal_diameter_boss() -> None:
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
    cross_binding = ownership.binding_for(cross_axis)
    assert cross_binding is not None
    assert cross_binding.reason_code == "boss_adapter"
    assert cross_binding.feature.kind == "boss"
    assert ownership.unexpectedly_missing == ()


def test_exact_faces_keep_a_separate_coaxial_boss_distinct_from_steps() -> None:
    part = Compound(children=[_stepped_shaft(), Cylinder(20, 60)])
    ownership = build_drawing(part).recognition_ownership()

    assert ownership is not None
    competing = tuple(
        occurrence
        for occurrence in _occurrences(ownership, "bosses")
        if ownership.evidence.record(occurrence).diameter == pytest.approx(40.0)
    )

    assert len(competing) == 1
    binding = ownership.binding_for(competing[0])
    assert binding is not None
    assert binding.reason_code == "boss_adapter"
    assert all(
        not ownership.evidence.defining_faces(competing[0])
        & ownership.evidence.defining_faces(step)
        for step in _occurrences(ownership, "turned_steps")
    )


def test_nearby_profile_steps_have_no_redundant_boss_occurrences() -> None:
    part = Compound(children=[_stepped_shaft(), Pos(0.25, 0, 0) * _stepped_shaft()])
    ownership = build_drawing(part).recognition_ownership()

    assert ownership is not None
    assert _occurrences(ownership, "bosses") == ()
    steps = _occurrences(ownership, "turned_steps")
    assert len(steps) == 4
    assert all(
        (binding := ownership.binding_for(occurrence)) is not None
        and binding.reason_code == "turned_step_adapter"
        and ownership.evidence.defining_faces(occurrence)
        for occurrence in steps
    )


def test_two_coaxial_bosses_use_exact_faces_to_select_the_groove_floor() -> None:
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
    groove_occurrence = _occurrences(ownership, "grooves")[0]
    floor = next(
        occurrence
        for occurrence in competing
        if ownership.evidence.defining_faces(occurrence)
        == ownership.evidence.defining_faces(groove_occurrence)
    )
    remote = next(occurrence for occurrence in competing if occurrence is not floor)
    assert ownership.status(floor) == "absorbed"
    assert ownership.binding_for(floor).reason_code == "boss_groove_owner"
    assert ownership.status(remote) == "represented"
    assert ownership.binding_for(remote).reason_code == "boss_adapter"


def test_disconnected_equal_diameter_boss_cannot_claim_a_groove() -> None:
    main = import_step(FIXTURES / "groove-narrow.step")
    remote = Pos(0, 0, 40) * (Cylinder(9, 8) + Box(30, 8, 3))
    ownership = build_drawing(Compound(children=[main, remote])).recognition_ownership()

    assert ownership is not None
    diameter_18 = tuple(
        occurrence
        for occurrence in _occurrences(ownership, "bosses")
        if ownership.evidence.record(occurrence).diameter == pytest.approx(18.0)
    )
    assert len(diameter_18) == 1
    remote_boss = diameter_18[0]
    groove_binding = ownership.binding_for(_occurrences(ownership, "grooves")[0])

    assert groove_binding is not None
    assert not ownership.evidence.defining_faces(remote_boss) & ownership.evidence.defining_faces(
        _occurrences(ownership, "grooves")[0]
    )
    remote_binding = ownership.binding_for(remote_boss)
    assert remote_binding is not None
    assert remote_binding.reason_code == "boss_adapter"
    assert remote_binding.feature is not groove_binding.feature


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
    evidence = _boss_and_step_evidence()
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    boss = evidence.record(_occurrences(ownership, "bosses")[0])
    step = evidence.record(_occurrences(ownership, "turned_steps")[0])

    with pytest.raises(ValueError, match="requires an existing exact owner"):
        builder.absorb_via(boss, step, reason_code="boss_turned_step_owner")

    foreign = _boss_and_step_evidence()
    foreign_step = foreign.record(
        _occurrences(RecognitionOwnershipBuilder(foreign).snapshot(), "turned_steps")[0]
    )
    with pytest.raises(ValueError, match="does not belong"):
        builder.absorb_via(boss, foreign_step, reason_code="boss_turned_step_owner")


def test_chained_boss_ownership_rejects_wrong_reason_families_and_duplicates() -> None:
    evidence = _boss_and_step_evidence()
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
    evidence = _boss_and_step_evidence()
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    bosses = tuple(evidence.record(item) for item in _occurrences(ownership, "bosses"))
    step = evidence.record(_occurrences(ownership, "turned_steps")[0])

    builder.bind(step, object(), reason_code="turned_step_adapter")
    builder.absorb_via(bosses[0], step, reason_code="boss_turned_step_owner")
    with pytest.raises(ValueError, match="owner occurrence already has a dependent"):
        builder.absorb_via(bosses[1], step, reason_code="boss_turned_step_owner")


def test_two_bosses_cannot_claim_the_same_intermediate_groove_occurrence() -> None:
    evidence = _boss_and_groove_evidence()
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    bosses = tuple(evidence.record(item) for item in _occurrences(ownership, "bosses"))
    groove = evidence.record(_occurrences(ownership, "grooves")[0])

    builder.bind(groove, object())
    builder.absorb_via(bosses[0], groove, reason_code="boss_groove_owner")
    with pytest.raises(ValueError, match="owner occurrence already has a dependent"):
        builder.absorb_via(bosses[1], groove, reason_code="boss_groove_owner")


def test_two_chain_paths_cannot_claim_the_same_final_groove_owner() -> None:
    evidence = _boss_and_groove_evidence()
    builder = RecognitionOwnershipBuilder(evidence)
    ownership = builder.snapshot()
    boss_occurrences = _occurrences(ownership, "bosses")
    floor_boss = evidence.record(boss_occurrences[0])
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
    evidence = _boss_and_step_evidence()
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
    evidence = _boss_and_step_evidence()
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
    evidence = _boss_and_groove_evidence()
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
    evidence = _boss_and_step_evidence()
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
