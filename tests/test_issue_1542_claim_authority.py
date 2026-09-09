"""Withdrawn witness authority cannot prove document agreement or dependency coverage."""

from copy import copy
from dataclasses import replace

import pytest
from build123d import Box, Cylinder, Pos
from test_issue_1542_catalog import detect_intake

from draftwright import build_drawing
from draftwright.document_evidence import bind_document_claims, document_support_proofs
from draftwright.document_input import DocumentInput
from draftwright.measurement_support import MeasurementSupport
from draftwright.plate_correspondence import plate_dependency_alternatives


@pytest.fixture(scope="module")
def located_drawing():
    drawing = build_drawing(Box(60, 50, 10) - Pos(10, 5, 0) * Cylinder(2, 20))
    snapshot = drawing.measurement_snapshot()
    location = next(claim for claim in snapshot.claims if claim.witnesses[1])
    width = next(claim for claim in snapshot.claims if claim.parameter == "width.length")
    return drawing, snapshot, location, width


@pytest.mark.parametrize(
    "damage,reason",
    [
        ("foreign_owner", "physical_owner_unavailable"),
        ("missing_meaning", "compiled_meaning_unavailable"),
        ("old_meaning_shape", "compiled_meaning_unavailable"),
        ("malformed_span", "span_witness_invalid"),
        ("wrong_span", "span_meaning_unbound"),
        ("missing_approvals", "location_meaning_unbound"),
        ("nonfinite_point", "location_witness_invalid"),
        ("wrong_point", "location_meaning_unbound"),
        ("wrong_component", "location_meaning_unbound"),
        ("missing_reference_span", "location_meaning_unbound"),
        ("absent_registry", "bound_claim_unconfirmed"),
    ],
)
def test_damaged_or_detached_measurement_witness_remains_unknown(located_drawing, damage, reason):
    drawing, snapshot, location, width = located_drawing
    variants = {
        "foreign_owner": replace(width, owner=replace(width.owner)),
        "missing_meaning": replace(width, meaning=()),
        "old_meaning_shape": replace(width, meaning=((30, None),)),
        "malformed_span": replace(width, witnesses=((1, 2), ())),
        "wrong_span": replace(width, witnesses=(((100, 0, 0), (130, 0, 0)), ())),
        "missing_approvals": replace(location, approved=()),
        "nonfinite_point": replace(
            location, witnesses=(None, ((location.witnesses[1][0][0], (float("nan"), 0, 0)),))
        ),
        "wrong_point": replace(
            location, witnesses=(None, ((location.witnesses[1][0][0], (1000, 1000, 0)),))
        ),
        "wrong_component": replace(
            location,
            witnesses=(
                None,
                (
                    (
                        "location_other.location."
                        + location.witnesses[1][0][0].rsplit(".", 1)[-1],
                        location.witnesses[1][0][1],
                    ),
                ),
            ),
        ),
        "missing_reference_span": replace(
            location,
            witnesses=(None, ()),
            meaning=tuple((*entry[:2], None, *entry[3:]) for entry in location.meaning),
        ),
        "absent_registry": location,
    }
    claim = variants[damage]
    result = bind_document_claims(
        "damaged",
        replace(snapshot, claims=(claim,), unknown=()),
        None if damage == "absent_registry" else drawing.registry,
    )
    assert not result.claims
    assert result.unknown == (("damaged", claim.annotation, reason),)


def test_real_slot_pattern_dependency_requires_the_exact_location_point_and_axis():
    part = Box(60, 180, 20)
    for y in (-45, -15, 15, 45):
        part -= Pos(0, y, 0) * Box(30, 8, 20)
    drawing = build_drawing(part)
    bound = bind_document_claims("slots", drawing.measurement_snapshot(), drawing.registry)
    recipes = [
        recipe
        for plate in drawing.recognition().plates
        for recipe in plate_dependency_alternatives(plate, drawing.model().features)
        if any(term.location_point is not None for term in recipe.supports)
    ]
    assert recipes and not bound.unknown
    recipe = recipes[0]
    assert document_support_proofs((recipe,), (bound,), ())
    checked = 0
    for term in recipe.supports:
        if term.location_point is None:
            continue
        carrying = tuple(
            claim
            for claim in bound.claims
            if claim.owner is term.feature and claim.parameter == term.parameter_id
        )
        assert carrying
        single = replace(bound, claims=carrying)
        assert document_support_proofs(((term,),), (single,), ())
        wrong_point = tuple(
            value + (axis == term.axis)
            for axis, value in zip("xyz", term.location_point, strict=True)
        )
        assert not document_support_proofs(
            ((replace(term, location_point=wrong_point),),), (single,), ()
        )
        assert not document_support_proofs(
            ((replace(term, axis="y" if term.axis == "x" else "x"),),), (single,), ()
        )
        scalar = replace(single, claims=tuple(replace(claim, address=()) for claim in carrying))
        assert not document_support_proofs(((term,),), (scalar,), ())
        checked += 1
    assert checked == 2


@pytest.fixture(scope="module")
def intake():
    _, model, analysis = detect_intake("holes")
    return model, analysis


@pytest.mark.parametrize(
    "damage",
    [
        "not_raw",
        "no_evidence",
        "no_ownership",
        "foreign_result",
        "foreign_evidence",
        "foreign_model_type",
        "duplicate_owner",
    ],
)
def test_document_intake_requires_one_raw_authority_and_unique_owners(intake, damage):
    model, analysis = intake
    changes = {
        "not_raw": {"recognition_frame_decision": {"status": "reoriented"}},
        "no_evidence": {"recognition_evidence": None},
        "no_ownership": {"recognition_ownership": None},
        "foreign_result": {"recognition": copy(analysis.recognition)},
        "foreign_evidence": {"recognition_evidence": copy(analysis.recognition_evidence)},
        "foreign_model_type": {"model": None},
        "duplicate_owner": {
            "model": replace(model, features=[*model.features, model.features[0]])
        },
    }
    source = DocumentInput(analysis, "fixture.step", b"owned snapshot")
    assert source.features
    with pytest.raises(ValueError):
        DocumentInput(replace(analysis, **changes[damage]), "fixture.step", b"owned snapshot")


def test_equal_copy_of_working_solid_does_not_acquire_document_authority(intake):
    _, analysis = intake
    source = DocumentInput(analysis, "fixture.step", b"owned snapshot")
    source.validate(analysis.part, source.features)
    with pytest.raises(ValueError, match="working solid"):
        source.validate(copy(analysis.part), source.features)


def test_axis_only_support_uses_physical_measurement_axis(located_drawing):
    drawing, snapshot, location, _width = located_drawing
    bound = bind_document_claims(
        "locations", replace(snapshot, claims=(location,), unknown=()), drawing.registry
    )
    assert bound.claims and not bound.unknown
    term = MeasurementSupport(location.owner, location.parameter, axis="z")
    assert document_support_proofs(((term,),), (bound,), ())
    assert not document_support_proofs(((replace(term, axis="x"),),), (bound,), ())
