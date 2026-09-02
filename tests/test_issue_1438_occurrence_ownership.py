"""#1438 — accepted occurrences retain exact run-local IR ownership."""

from __future__ import annotations

import pickle
from dataclasses import replace
from pathlib import Path

import pytest
from b123d_recognisers.evidence import build_recognition_evidence
from build123d import Align, Axis, Box, Cylinder, fillet, import_step

from draftwright import build_drawing
from draftwright.analysis import _analyse
from draftwright.drawing import BuildState
from draftwright.recognition_ownership import RecognitionOwnershipBuilder

FIXTURES = Path(__file__).parent / "fixtures" / "evaluation"


def _single_fillet():
    stock = Box(60, 40, 30)
    return fillet(stock.edges().filter_by(Axis.Z)[0], 4)


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


def test_grouped_family_stays_unclassified_instead_of_becoming_a_false_missing() -> None:
    part = Box(40, 30, 10, align=(Align.CENTER, Align.CENTER, Align.CENTER)) - Cylinder(3, 20)
    drawing = build_drawing(part)
    ownership = drawing.recognition_ownership()

    assert ownership is not None
    (hole,) = tuple(
        occurrence
        for occurrence in ownership.evidence.features
        if ownership.evidence.family(occurrence) == "holes"
    )
    assert ownership.status(hole) == "unclassified"
    assert ownership.binding_for(hole) is None
    assert ownership.unexpectedly_missing == ()


def test_an_unbound_direct_occurrence_fails_closed_as_unexpectedly_missing() -> None:
    evidence = build_recognition_evidence(_single_fillet())
    ownership = RecognitionOwnershipBuilder(evidence).freeze()
    (occurrence,) = ownership.expected_direct

    assert ownership.status(occurrence) == "unexpectedly_missing"
    assert ownership.unexpectedly_missing == (occurrence,)


def test_value_equal_record_cannot_impersonate_the_provider_occurrence() -> None:
    evidence = build_recognition_evidence(_single_fillet())
    builder = RecognitionOwnershipBuilder(evidence)
    record = evidence.record(builder.freeze().expected_direct[0])
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
    occurrence = builder.freeze().expected_direct[0]
    feature = object()
    builder.bind(evidence.record(occurrence), feature)
    with pytest.raises(ValueError, match="occurrence already"):
        builder.bind(evidence.record(occurrence), object())

    repeated = build_recognition_evidence(import_step(FIXTURES / "fillet-repeated.step"))
    repeated_builder = RecognitionOwnershipBuilder(repeated)
    first, second, *_ = repeated_builder.freeze().expected_direct
    shared_feature = object()
    repeated_builder.bind(repeated.record(first), shared_feature)
    with pytest.raises(ValueError, match="IR feature already"):
        repeated_builder.bind(repeated.record(second), shared_feature)


def test_declared_and_framed_builds_do_not_invent_occurrence_ownership() -> None:
    part = _single_fillet()
    declared = build_drawing(part, model=[])
    framed = build_drawing(part, framed_recognition=True)

    assert declared.recognition_ownership() is None
    assert framed.recognition_evidence() is None
    assert framed.recognition_ownership() is None


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
    ownership = RecognitionOwnershipBuilder(first).freeze()

    with pytest.raises(ValueError, match="same run"):
        BuildState().attach_recognition(second.result, evidence=second, ownership=ownership)


def test_ownership_ledger_is_deliberately_not_serializable() -> None:
    evidence = build_recognition_evidence(_single_fillet())
    ownership = RecognitionOwnershipBuilder(evidence).freeze()

    with pytest.raises(TypeError, match="run-local"):
        pickle.dumps(ownership)
