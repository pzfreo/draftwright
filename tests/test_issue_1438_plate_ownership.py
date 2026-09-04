"""#1438 — every accepted Plate has exact direct, absorbed, or missing ownership."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from b123d_recognisers.evidence import build_recognition_evidence
from build123d import Align, Box, Compound, Pos, RegularPolygon, Rot, extrude

from draftwright import build_drawing
from draftwright import plate_correspondence as correspondence
from draftwright import reporting as reporting_module
from draftwright.model import detect as detect_module
from draftwright.recognition_ownership import RecognitionOwnershipBuilder

_CENTER = (Align.CENTER, Align.CENTER, Align.CENTER)
_CENTER_MIN = (Align.CENTER, Align.CENTER, Align.MIN)


def _namespace_with(value: SimpleNamespace, **changes) -> SimpleNamespace:
    """Return a copied namespace for compact malformed-contract probes."""

    return SimpleNamespace(**{**vars(value), **changes})


def _tee():
    return Box(80, 60, 10, align=_CENTER_MIN) + Pos(0, 0, 10) * Box(80, 10, 40, align=_CENTER_MIN)


def _boss_on_plate():
    return Box(100, 80, 10, align=_CENTER) + (
        Pos(13, -7, 5) * Rot(0, 0, 11) * extrude(RegularPolygon(20, 6), 30)
    )


def _rebate(*, width: float = 60, opening: float = 20):
    return Box(80, width, 30) - Pos(0, 0, 7.5) * Box(80, opening, 15)


def _slotted_plate():
    part = Box(60, 180, 20)
    for y in (-45, -15, 15, 45):
        part -= Pos(0, y, 0) * Box(30, 8, 20)
    return part


def _plate_bindings(drawing):
    evidence = drawing.recognition_evidence()
    ownership = drawing.recognition_ownership()
    assert evidence is not None and ownership is not None
    occurrences = tuple(
        occurrence for occurrence in evidence.features if evidence.family(occurrence) == "plates"
    )
    return (
        evidence,
        ownership,
        occurrences,
        tuple(ownership.binding_for(occurrence) for occurrence in occurrences),
    )


def _plate_report(drawing):
    return tuple(
        occurrence
        for occurrence in drawing.report()["recognition"]["occurrences"]
        if occurrence["family"] == "plates"
    )


def test_multi_axis_plates_keep_distinct_direct_ir_owners() -> None:
    drawing = build_drawing(_tee())
    _evidence, ownership, occurrences, bindings = _plate_bindings(drawing)

    assert len(occurrences) == len(bindings) == 2
    assert all(binding is not None for binding in bindings)
    assert all(
        binding is not None
        and binding.disposition == "represented"
        and binding.reason_code == "plate_adapter"
        and binding.feature.kind == "plate"
        and any(binding.feature is feature for feature in drawing.model().features)
        for binding in bindings
    )
    assert len({id(binding.feature) for binding in bindings if binding is not None}) == 2
    assert ownership.unexpectedly_missing == ()
    assert [item["owners"] for item in _plate_report(drawing)] == [
        [{"id": "plate:1", "kind": "plate"}],
        [{"id": "plate:2", "kind": "plate"}],
    ]


def test_axial_plate_is_absorbed_only_by_its_aag_backed_step_level() -> None:
    drawing = build_drawing(_boss_on_plate())
    _evidence, ownership, occurrences, bindings = _plate_bindings(drawing)

    assert len(occurrences) == 1
    (binding,) = bindings
    assert binding is not None
    assert binding.disposition == "absorbed"
    assert binding.reason_code == "plate_step_level_owner"
    assert [feature.kind for feature in binding.features] == ["step_level"]
    assert ownership.unexpectedly_missing == ()
    (projected,) = _plate_report(drawing)
    assert projected["owners"] == [{"id": "step_level:1", "kind": "step_level"}]


def test_side_plates_are_jointly_owned_by_envelope_and_step_ladder() -> None:
    drawing = build_drawing(_rebate())
    _evidence, ownership, occurrences, bindings = _plate_bindings(drawing)

    assert len(occurrences) == 2
    assert all(binding is not None for binding in bindings)
    assert all(
        binding is not None
        and binding.disposition == "absorbed"
        and binding.reason_code == "plate_step_ladder_owner"
        and [feature.kind for feature in binding.features] == ["envelope", "step_level"]
        for binding in bindings
    )
    assert ownership.unexpectedly_missing == ()
    assert all(
        occurrence["owners"]
        == [
            {"id": "envelope:1", "kind": "envelope"},
            {"id": "step_level:1", "kind": "step_level"},
        ]
        for occurrence in _plate_report(drawing)
    )


def test_slot_cut_webs_share_the_exact_pattern_owner_of_adjacent_slots() -> None:
    drawing = build_drawing(_slotted_plate())
    evidence, ownership, occurrences, bindings = _plate_bindings(drawing)

    assert len(occurrences) == 5
    assert all(binding is not None for binding in bindings)
    assert all(
        binding is not None
        and binding.disposition == "absorbed"
        and binding.reason_code == "plate_slot_pattern_owner"
        and [feature.kind for feature in binding.features] == ["envelope", "slot_pattern"]
        for binding in bindings
    )
    pattern = next(
        feature for feature in drawing.model().features if feature.kind == "slot_pattern"
    )
    slot_bindings = tuple(
        ownership.binding_for(occurrence)
        for occurrence in evidence.features
        if evidence.family(occurrence) == "slots"
    )
    assert len(slot_bindings) == 4
    assert all(
        binding is not None
        and binding.feature is pattern
        and binding.reason_code == "slot_pattern_member"
        for binding in slot_bindings
    )
    assert ownership.unexpectedly_missing == ()


def test_equal_valued_disconnected_plates_cannot_borrow_a_slot_pattern() -> None:
    slotted = _slotted_plate()
    # These remote rebate walls deliberately have the same published intervals as two local webs.
    remote = Pos(150, 0, 0) * _rebate(width=82, opening=38)
    drawing = build_drawing(Compound(children=[slotted, remote]))
    matching = tuple(
        occurrence
        for occurrence in _plate_report(drawing)
        if (occurrence["record"]["lo"], occurrence["record"]["hi"])
        in {(-41.0, -19.0), (19.0, 41.0)}
    )

    assert len(matching) == 4
    local = tuple(item for item in matching if abs(item["record"]["u"]) < 1)
    remote = tuple(item for item in matching if item["record"]["u"] > 100)
    assert len(local) == len(remote) == 2
    assert {item["disposition"] for item in local} == {"absorbed"}
    assert {item["reason_code"] for item in local} == {"plate_slot_pattern_owner"}
    assert {item["disposition"] for item in remote} == {"unexpectedly_missing"}
    assert {item["reason_code"] for item in remote} == {"supported_owner_missing"}
    assert all(item["owners"] == [] for item in remote)


def test_removing_one_conjunctive_owner_revokes_every_plate_binding() -> None:
    drawing = build_drawing(_slotted_plate())
    evidence = drawing.recognition_evidence()
    ownership = drawing.recognition_ownership()
    model = drawing.model()
    assert evidence is not None and ownership is not None

    report = reporting_module.drawing_report(
        evidence=evidence,
        ownership=ownership,
        model=replace(
            model,
            features=tuple(
                feature for feature in model.features if feature.kind != "slot_pattern"
            ),
        ),
        lint=drawing.lint_summary(),
        source=None,
    )
    plates = tuple(
        occurrence
        for occurrence in report["recognition"]["occurrences"]
        if occurrence["family"] == "plates"
    )

    assert len(plates) == 5
    assert {occurrence["disposition"] for occurrence in plates} == {"unexpectedly_missing"}
    assert {occurrence["reason_code"] for occurrence in plates} == {"recorded_owner_not_in_model"}
    assert all(occurrence["owners"] == [] for occurrence in plates)


def test_slot_pattern_absorption_requires_an_existing_exact_member_owner() -> None:
    evidence = build_recognition_evidence(_slotted_plate())
    (pattern,) = evidence.result.slot_patterns
    plate = evidence.result.plates[0]
    envelope = SimpleNamespace(kind="envelope")
    pattern_feature = SimpleNamespace(kind="slot_pattern")
    builder = RecognitionOwnershipBuilder(evidence)

    with pytest.raises(ValueError, match="existing exact recognition owner"):
        builder.absorb_into_many(
            plate,
            (envelope, pattern_feature),
            reason_code="plate_slot_pattern_owner",
        )

    builder.absorb(pattern.slots, pattern_feature, reason_code="slot_pattern_member")
    builder.absorb_into_many(
        plate,
        (envelope, pattern_feature),
        reason_code="plate_slot_pattern_owner",
    )
    ownership = builder.snapshot()
    plate_occurrence = next(
        occurrence
        for occurrence in evidence.features
        if evidence.family(occurrence) == "plates" and evidence.record(occurrence) is plate
    )
    binding = ownership.binding_for(plate_occurrence)
    assert binding is not None
    assert binding.features == (envelope, pattern_feature)


def test_multi_owner_builder_guards_the_closed_plate_contract() -> None:
    evidence = build_recognition_evidence(_slotted_plate())
    plate = evidence.result.plates[0]
    slot = evidence.result.slots[0]
    step = SimpleNamespace(kind="step_level")
    builder = RecognitionOwnershipBuilder(evidence)

    with pytest.raises(ValueError, match="unknown multi-feature absorption"):
        builder.absorb_into_many(plate, (step,), reason_code="direct_adapter")
    with pytest.raises(ValueError, match="does not match occurrence family"):
        builder.absorb_into_many(slot, (step,), reason_code="plate_step_level_owner")
    with pytest.raises(TypeError, match="exact tuple"):
        builder.absorb_into_many(
            plate,
            [step],  # type: ignore[arg-type]
            reason_code="plate_step_level_owner",
        )
    with pytest.raises(ValueError, match="does not match IR owners"):
        builder.absorb_into_many(
            plate,
            (SimpleNamespace(kind="envelope"),),
            reason_code="plate_step_level_owner",
        )

    builder.absorb_into_many(plate, (step,), reason_code="plate_step_level_owner")
    with pytest.raises(ValueError, match="occurrence already"):
        builder.absorb_into_many(plate, (step,), reason_code="plate_step_level_owner")


def test_plate_aag_scope_rejects_foreign_or_wrongly_typed_owners() -> None:
    evidence = build_recognition_evidence(_boss_on_plate())
    plate = evidence.result.plates[0]
    envelope = SimpleNamespace(kind="envelope")

    assert not detect_module._plate_owner_has_evidence_scope(
        evidence,
        replace(plate),
        (SimpleNamespace(kind="step_level"),),
        slot_pattern_members={},
    )
    assert not detect_module._plate_owner_has_evidence_scope(
        evidence,
        plate,
        (SimpleNamespace(kind="step_level"),),
        slot_pattern_members={},
    )
    assert not detect_module._plate_owner_has_evidence_scope(
        evidence,
        plate,
        (envelope, SimpleNamespace(kind="step_level")),
        slot_pattern_members={},
    )
    assert not detect_module._plate_owner_has_evidence_scope(
        evidence,
        plate,
        (SimpleNamespace(kind="polygonal_boss"),),
        slot_pattern_members={},
    )


def test_plate_aag_scope_fails_closed_on_malformed_level_and_riser_records() -> None:
    level_drawing = build_drawing(_boss_on_plate())
    level_evidence = level_drawing.recognition_evidence()
    assert level_evidence is not None
    level_plate = level_evidence.result.plates[0]
    level_owner = next(
        feature for feature in level_drawing.model().features if feature.kind == "step_level"
    )
    (source_level,) = level_evidence.result.step_levels
    malformed_level = SimpleNamespace(
        z=None,
        x_span=source_level.x_span,
        y_span=source_level.y_span,
    )
    level_proxy = SimpleNamespace(
        features=level_evidence.features,
        family=level_evidence.family,
        defining_faces=level_evidence.defining_faces,
        record=lambda occurrence: (
            malformed_level
            if level_evidence.record(occurrence) is source_level
            else level_evidence.record(occurrence)
        ),
        result=SimpleNamespace(step_levels=(malformed_level,)),
    )
    assert not detect_module._plate_owner_has_evidence_scope(
        level_proxy,
        level_plate,
        (level_owner,),
        slot_pattern_members={},
    )

    riser_drawing = build_drawing(_rebate())
    riser_evidence = riser_drawing.recognition_evidence()
    assert riser_evidence is not None
    riser_plate = riser_evidence.result.plates[0]
    riser_owner = next(
        feature for feature in riser_drawing.model().features if feature.kind == "step_level"
    )
    source_riser = next(
        riser for riser in riser_evidence.result.risers if str(riser.axis) == str(riser_plate.axis)
    )
    malformed_riser = SimpleNamespace(axis=source_riser.axis, positions=(None,))
    riser_proxy = SimpleNamespace(
        features=riser_evidence.features,
        family=riser_evidence.family,
        defining_faces=riser_evidence.defining_faces,
        record=lambda occurrence: (
            malformed_riser
            if riser_evidence.record(occurrence) is source_riser
            else riser_evidence.record(occurrence)
        ),
        result=SimpleNamespace(risers=(malformed_riser,)),
    )
    assert not detect_module._plate_owner_has_evidence_scope(
        riser_proxy,
        riser_plate,
        (SimpleNamespace(kind="envelope"), riser_owner),
        slot_pattern_members={},
    )


def test_shared_plate_correspondence_fails_closed_at_each_owner_boundary() -> None:
    source = SimpleNamespace(axis="x", lo=-0.5, hi=0.5, u=0.0, v=0.0)
    support = SimpleNamespace(x_span=(-10.0, 10.0), y_span=(-10.0, 10.0))
    step = SimpleNamespace(
        kind="step_level",
        base=0.0,
        levels=(),
        level_supports=(support,),
        shoulders=(),
    )
    envelope = SimpleNamespace(
        kind="envelope",
        bbox_min=(-10.0, -10.0, -10.0),
        bbox_max=(10.0, 10.0, 10.0),
        frame=SimpleNamespace(origin=(0.0, 0.0, 0.0)),
    )

    assert correspondence._depth_axis("x", "x") is None
    assert not correspondence._step_supports_source(
        source, _namespace_with(step, level_supports=())
    )
    assert not correspondence._step_supports_source(_namespace_with(source, axis="invalid"), step)

    wrong_width = SimpleNamespace(member=SimpleNamespace(width_axis="y"))
    assert not correspondence._slot_pattern_supports_source(source, wrong_width)
    invalid_axes = SimpleNamespace(
        member=SimpleNamespace(width_axis="x", long_axis="x"),
        frame=SimpleNamespace(axis="z"),
    )
    assert not correspondence._slot_pattern_supports_source(source, invalid_axes)
    assert not correspondence._slot_pattern_supports_source(source, SimpleNamespace())

    assert not correspondence._polygonal_boss_supports_source(
        source, SimpleNamespace(frame=SimpleNamespace(axis="y"))
    )
    malformed_polygon = SimpleNamespace(
        frame=SimpleNamespace(axis="x"),
        side_count=3,
        flat_directions=(),
        flat_centres=(),
    )
    assert not correspondence._polygonal_boss_supports_source(source, malformed_polygon)
    assert not correspondence._polygonal_boss_supports_source(
        source, SimpleNamespace(frame=SimpleNamespace(axis="x"))
    )

    zero_height = SimpleNamespace(axis="z", lo=0.0, hi=0.0, u=0.0, v=0.0)
    assert correspondence._step_level_dependencies(zero_height, (step,)) == ()
    assert correspondence._step_level_dependencies(source, (step,)) == ()
    assert correspondence._step_level_dependencies(source, (envelope, step)) == ()
    assert correspondence._step_level_dependencies(SimpleNamespace(), (step,)) == ()
    assert correspondence._polygonal_boss_dependencies(SimpleNamespace(), (envelope,)) == ()

    member = SimpleNamespace(width_axis="x", long_axis="y", lo=-5.0, hi=5.0, width=2.0)
    pattern = SimpleNamespace(
        kind="slot_pattern",
        member=member,
        frame=SimpleNamespace(axis="z", origin=(0.0, 0.0, 0.0)),
        members=((0.0, 0.0, 0.0),),
    )
    assert correspondence._slot_pattern_dependencies(source, (envelope, pattern)) == ()
    assert (
        correspondence._slot_pattern_dependencies(
            source, (envelope, SimpleNamespace(kind="slot_pattern"))
        )
        == ()
    )

    whole_axis = _namespace_with(source, lo=-10.0, hi=10.0)
    assert correspondence._envelope_owned_dependencies(whole_axis, (envelope,)) == (
        (envelope, "width.length"),
    )
    assert correspondence._envelope_owned_dependencies(SimpleNamespace(), (envelope,)) == ()
