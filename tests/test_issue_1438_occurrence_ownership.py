"""#1438 — accepted occurrences retain exact run-local IR ownership."""

from __future__ import annotations

import pickle
from dataclasses import replace
from pathlib import Path

import pytest
from b123d_recognisers.evidence import build_recognition_evidence
from build123d import (
    Align,
    Axis,
    Box,
    BuildLine,
    BuildSketch,
    Cylinder,
    Line,
    Plane,
    Polygon,
    Pos,
    RadiusArc,
    Rot,
    Vector,
    extrude,
    fillet,
    import_step,
    make_face,
)

from draftwright import build_drawing
from draftwright.analysis import _analyse
from draftwright.drawing import BuildState
from draftwright.model.detect import _build_part_model_from_recognition
from draftwright.pmi import PmiRecord
from draftwright.recognition_ownership import DIRECT_FAMILIES, RecognitionOwnershipBuilder

FIXTURES = Path(__file__).parent / "fixtures" / "evaluation"


def _single_fillet():
    stock = Box(60, 40, 30)
    return fillet(stock.edges().filter_by(Axis.Z)[0], 4)


def _small_blends():
    stock = Box(40, 30, 20)
    return fillet(list(stock.edges().filter_by(Axis.Z)), 0.2)


def _circular_blind_step():
    return Box(40, 30, 20) - Pos(7.5, 15, 10) * Rot(0, 90, 0) * Cylinder(4, 25)


def _paired_ramp_step():
    profile = Polygon((0, -8), (0, 8), (-10, 0))
    cutter = Pos(20, 20, 0) * extrude(Plane.XZ * profile, 25)
    return Box(40, 40, 30) - cutter


def _rectangular_blind_slot():
    stock = Box(30, 20, 40, align=(Align.CENTER, Align.CENTER, Align.MIN))
    tool = Pos(0, 5, 0) * Box(10, 5, 20, align=(Align.CENTER, Align.MIN, Align.MIN))
    return stock - tool


def _round_bottom_blind_slot():
    with BuildLine() as boundary:
        Line((-5, 0), (5, 0))
        RadiusArc((5, 0), (2, -3), 3)
        Line((2, -3), (-2, -3))
        RadiusArc((-2, -3), (-5, 0), 3)
    with BuildSketch() as sketch:
        make_face(boundary.line)
    stock = Pos(0, -5, 0) * Box(30, 10, 40)
    tool = extrude(sketch.sketch, amount=20, dir=Vector(0, 0, 1))
    return stock - tool


DIRECT_CASES = (
    ("blends", _small_blends),
    ("chamfers", lambda: import_step(FIXTURES / "chamfer-planar-z.step")),
    ("circular_blind_steps", _circular_blind_step),
    ("double_d_bores", lambda: import_step(FIXTURES / "double-d-single-z.step")),
    ("fillets", _single_fillet),
    ("flats", lambda: import_step(FIXTURES / "flat-lone-d.step")),
    ("grooves", lambda: import_step(FIXTURES / "groove-lone-z.step")),
    ("pads", lambda: import_step(FIXTURES / "pad-x-positive.step")),
    ("paired_ramp_steps", _paired_ramp_step),
    ("polygonal_bosses", lambda: import_step(FIXTURES / "polygonal-boss-x.step")),
    ("polygonal_stock", lambda: import_step(FIXTURES / "polygonal-stock-x.step")),
    ("rectangular_blind_slots", _rectangular_blind_slot),
    ("round_bottom_blind_slots", _round_bottom_blind_slot),
)


def test_direct_case_roster_is_independent_and_complete() -> None:
    assert tuple(family for family, _factory in DIRECT_CASES) == (
        "blends",
        "chamfers",
        "circular_blind_steps",
        "double_d_bores",
        "fillets",
        "flats",
        "grooves",
        "pads",
        "paired_ramp_steps",
        "polygonal_bosses",
        "polygonal_stock",
        "rectangular_blind_slots",
        "round_bottom_blind_slots",
    )
    assert set(family for family, _factory in DIRECT_CASES) == DIRECT_FAMILIES


@pytest.mark.parametrize(
    ("family", "part_factory"), DIRECT_CASES, ids=[family for family, _factory in DIRECT_CASES]
)
def test_every_advertised_direct_family_binds_to_finished_model(family, part_factory) -> None:
    drawing = build_drawing(part_factory())
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    occurrences = tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == family
    )
    assert occurrences
    assert not tuple(
        occurrence
        for occurrence in ownership.unexpectedly_missing
        if ownership.evidence.family(occurrence) == family
    )
    for occurrence in occurrences:
        binding = ownership.binding_for(occurrence)
        assert binding is not None
        assert ownership.status(occurrence) == "represented"
        assert any(binding.feature is feature for feature in drawing.model().features)


def test_automatic_build_binds_each_direct_occurrence_to_its_exact_ir_feature() -> None:
    drawing = build_drawing(import_step(FIXTURES / "fillet-repeated.step"))
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    assert ownership.evidence is drawing.recognition_evidence()
    fillets = tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == "fillets"
    )
    assert len(fillets) == 4
    assert ownership.unexpectedly_missing == ()
    assert all(ownership.status(occurrence) == "represented" for occurrence in fillets)

    bindings = tuple(ownership.binding_for(occurrence) for occurrence in fillets)
    assert all(binding is not None for binding in bindings)
    assert len({id(binding.feature) for binding in bindings if binding is not None}) == 4
    assert all(
        any(binding.feature is feature for feature in drawing.model().features)
        for binding in bindings
        if binding is not None
    )
    assert {
        id(ownership.evidence.record(binding.occurrence))
        for binding in bindings
        if binding is not None
    } == {id(record) for record in drawing.recognition().fillets}


def test_singleton_hole_is_represented_by_its_exact_finished_ir_feature() -> None:
    part = Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER)) - Cylinder(3, 20)
    drawing = build_drawing(part)
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    (hole,) = tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == "holes"
    )
    binding = ownership.binding_for(hole)

    assert ownership.status(hole) == "represented"
    assert binding is not None
    assert binding.reason_code == "hole_adapter"
    assert binding.member_index == 0
    assert any(binding.feature is feature for feature in drawing.model().features)
    assert ownership.unexpectedly_missing == ()


def test_an_unbound_direct_occurrence_fails_closed_as_unexpectedly_missing() -> None:
    evidence = build_recognition_evidence(_single_fillet())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()
    (occurrence,) = ownership.expected_direct

    assert ownership.status(occurrence) == "unexpectedly_missing"
    assert ownership.unexpectedly_missing == (occurrence,)


def test_explicit_pmi_lowering_lineage_rebinds_double_d_to_the_final_ir_object() -> None:
    part = import_step(FIXTURES / "double-d-single-z.step")
    evidence = build_recognition_evidence(part)
    builder = RecognitionOwnershipBuilder(evidence)
    pmi = PmiRecord(
        kind="diameter",
        type_code=15,
        value=10.0,
        upper_tol=0.1,
        lower_tol=0.1,
        ref_pts=((-5.0, 0.0, 5.0), (5.0, 0.0, 5.0)),
        ref_bbox=(-5.1, -5.1, -0.1, 5.1, 5.1, 10.1),
        dominant_axis="Z",
        label="ø10 ±0.1",
        source_id="dimension:double-d",
        source_category="dimension",
    )

    model = _build_part_model_from_recognition(
        part,
        evidence.result,
        ownership=builder,
        pmi=(pmi,),
    )
    ownership = builder.snapshot()
    (occurrence,) = tuple(
        item for item in evidence.features if evidence.family(item) == "double_d_bores"
    )
    binding = ownership.binding_for(occurrence)

    assert binding is not None
    assert ownership.status(occurrence) == "represented"
    assert any(binding.feature is feature for feature in model.features)
    assert model.decorations[(binding.feature, "diameter")].source_ids == ("dimension:double-d",)


def test_a_bound_feature_split_fails_closed_instead_of_retaining_stale_identity() -> None:
    evidence = build_recognition_evidence(_single_fillet())
    builder = RecognitionOwnershipBuilder(evidence)
    occurrence = builder.snapshot().expected_direct[0]
    source = object()
    builder.bind(evidence.record(occurrence), source)

    builder.remap_feature(source, (object(), object()))
    ownership = builder.snapshot()

    assert ownership.status(occurrence) == "unexpectedly_missing"
    assert ownership.binding_for(occurrence) is None


def test_value_equal_record_cannot_impersonate_the_provider_occurrence() -> None:
    evidence = build_recognition_evidence(_single_fillet())
    builder = RecognitionOwnershipBuilder(evidence)
    record = evidence.record(builder.snapshot().expected_direct[0])
    copied_record = replace(record)

    assert copied_record == record
    assert copied_record is not record
    with pytest.raises(ValueError, match="does not belong"):
        builder.bind(copied_record, object())


def test_builder_rejects_invalid_evidence_and_duplicate_ownership() -> None:
    with pytest.raises(TypeError, match="exact RecognitionEvidence"):
        RecognitionOwnershipBuilder(object())  # type: ignore[arg-type]

    evidence = build_recognition_evidence(_single_fillet())
    builder = RecognitionOwnershipBuilder(evidence)
    occurrence = builder.snapshot().expected_direct[0]
    feature = object()
    builder.bind(evidence.record(occurrence), feature)
    with pytest.raises(ValueError, match="occurrence already"):
        builder.bind(evidence.record(occurrence), object())

    repeated = build_recognition_evidence(import_step(FIXTURES / "fillet-repeated.step"))
    repeated_builder = RecognitionOwnershipBuilder(repeated)
    first, second, *_ = repeated_builder.snapshot().expected_direct
    shared_feature = object()
    repeated_builder.bind(repeated.record(first), shared_feature)
    with pytest.raises(ValueError, match="IR feature already"):
        repeated_builder.bind(repeated.record(second), shared_feature)


def test_declared_build_does_not_invent_occurrence_ownership() -> None:
    part = _single_fillet()
    declared = build_drawing(part, model=[])

    assert declared.recognition_ownership() is None


def test_scale_retry_reuses_the_exact_model_evidence_and_ownership_authority() -> None:
    first = _analyse(_single_fillet(), "", "", "ISO 2768-m", "", "drawing")
    second = _analyse(
        _single_fillet(),
        "",
        "",
        "ISO 2768-m",
        "",
        "drawing",
        scale=first.SCALE,
        _reuse=first,
    )

    assert second.model is first.model
    assert second.recognition is first.recognition
    assert second.recognition_evidence is first.recognition_evidence
    assert second.recognition_ownership is first.recognition_ownership


def test_build_state_rejects_ownership_from_another_evidence_authority() -> None:
    part = _single_fillet()
    first = build_recognition_evidence(part)
    second = build_recognition_evidence(part)
    ownership = RecognitionOwnershipBuilder(first).snapshot()

    with pytest.raises(ValueError, match="same run"):
        BuildState().attach_recognition(second.result, evidence=second, ownership=ownership)


def test_ownership_ledger_is_deliberately_not_serializable() -> None:
    evidence = build_recognition_evidence(_single_fillet())
    ownership = RecognitionOwnershipBuilder(evidence).snapshot()

    with pytest.raises(TypeError, match="run-local"):
        pickle.dumps(ownership)
