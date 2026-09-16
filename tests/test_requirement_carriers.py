"""Carrier names follow accepted evidence without changing requirement outcomes."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos, RegularPolygon, Rot, extrude

from draftwright import build_drawing
from draftwright.document_evidence import bind_document_claims, document_support_proofs
from draftwright.model.compiled import DimensionId

FIXTURES = Path(__file__).parent / "fixtures/evaluation"


def _carriers(outcome):
    return {(carrier.annotation, carrier.kind) for carrier in outcome.carriers}


def test_through_step_alternates_use_confirmed_proofs_not_registered_names():
    part = Rot(90, 0, 0) * (Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30))
    drawing = build_drawing(part)
    outcomes = drawing.requirement_snapshot().outcomes["through_steps"]
    assert len(outcomes) == 2
    assert all(row.state == "inapplicable" and row.dependency_alternatives for row in outcomes)
    assert all(not row.carriers for row in outcomes)
    snapshot = bind_document_claims("before", drawing.measurement_snapshot(), drawing.registry)
    proofs = tuple(
        document_support_proofs(row.dependency_alternatives, (snapshot,), ()) for row in outcomes
    )
    assert all(proofs), "The fixture must first prove both complete physical legs"
    names = {
        claim.annotation
        for alternatives in proofs
        for proof in alternatives
        for claim in proof.witnesses
    }
    assert names
    for name in names:
        annotation = drawing.registry.named(name)
        assert annotation.label != "999"
        annotation.label = "999"

    damaged = drawing.requirement_snapshot().outcomes["through_steps"]
    assert len(damaged) == len(outcomes)
    assert all(row.state == "missing" and not row.carriers for row in damaged)
    snapshot = bind_document_claims("after", drawing.measurement_snapshot(), drawing.registry)
    assert snapshot.unknown
    assert all(
        not document_support_proofs(row.dependency_alternatives, (snapshot,), ())
        for row in damaged
    )


def test_native_through_step_keeps_every_direct_carrier():
    drawing = build_drawing(Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30))
    rows = drawing.requirement_snapshot().outcomes["through_steps"]
    assert len(rows) == 2
    for row in rows:
        assert row.state == "placed" and not row.dependency_alternatives
        assert row.carriers
        for carrier in row.carriers:
            assert carrier.kind == "measurement"
            assert any(
                identity.feature is row.features[0] and identity.parameter == row.parameter_id
                for identity in drawing.registry.measurement_of(carrier.annotation)
            )


@pytest.mark.parametrize(
    "fixture,family,kind,prefix,aliases",
    (
        ("pad-z-positive.step", "pads", "pad", "location_pad.", ("location",)),
        ("pocket-lone.step", "pockets", "pocket", "location_pocket.", ("location",)),
        (
            "pocket-pattern-linear.step",
            "pocket_patterns",
            "pocket_pattern",
            "location_pocket_pattern.",
            ("location", "location_pocket_pattern.location"),
        ),
    ),
)
def test_locations_retain_physical_ink_and_every_valid_note_alias(
    fixture, family, kind, prefix, aliases
):
    drawing = build_drawing(FIXTURES / fixture)
    owner = next(feature for feature in drawing.model().features if feature.kind == kind)
    original = {
        row.parameter_id: row
        for row in drawing.requirement_snapshot().outcomes[family]
        if row.parameter_id.startswith(prefix)
    }
    assert len(original) == 2
    assert all(row.state == "placed" and row.carriers for row in original.values())
    expected_notes = set()
    for index, parameter in enumerate(aliases):
        name = f"location_note_{index}"
        drawing.registry.add(
            SimpleNamespace(),
            name,
            "plan",
            feature=owner,
            satisfaction=DimensionId(owner, parameter),
        )
        expected_notes.add((name, "structured_note"))

    both = {
        row.parameter_id: row
        for row in drawing.requirement_snapshot().outcomes[family]
        if row.parameter_id in original
    }
    for parameter, row in both.items():
        assert row.state == "placed"
        assert _carriers(row) == _carriers(original[parameter]) | expected_notes
    for name in {carrier.annotation for row in original.values() for carrier in row.carriers}:
        drawing.remove(name)
    remaining = [
        row
        for row in drawing.requirement_snapshot().outcomes[family]
        if row.parameter_id in original
    ]
    assert len(remaining) == 2
    assert all(row.state == "satisfied_by_structured_note" for row in remaining)
    assert all(_carriers(row) == expected_notes for row in remaining)


def test_pocket_pattern_count_keeps_note_and_rejects_conflicting_printed_quantity():
    drawing = build_drawing(FIXTURES / "pocket-pattern-linear.step")
    owner = next(
        feature for feature in drawing.model().features if feature.kind == "pocket_pattern"
    )

    def count_outcome():
        return next(
            row
            for row in drawing.requirement_snapshot().outcomes["pocket_patterns"]
            if row.parameter_id == "grouping.count"
        )

    original = count_outcome()
    assert original.state == "placed" and original.carriers
    drawing.registry.add(
        SimpleNamespace(),
        "count_note",
        "plan",
        feature=owner,
        satisfaction=DimensionId(owner, "grouping.count"),
    )
    assert _carriers(count_outcome()) == _carriers(original) | {("count_note", "structured_note")}
    drawing.registry.add(SimpleNamespace(covers_count=99), "wrong_count", "plan", feature=owner)
    conflicted = count_outcome()
    assert conflicted.state == "satisfied_by_structured_note"
    assert _carriers(conflicted) == {("count_note", "structured_note")}


def test_hole_carriers_exclude_wrong_quantities_and_unrelated_member_points():
    part = Box(80, 60, 10)
    for x, y in ((-20, -15), (20, 15)):
        part -= Pos(x, y, 0) * Cylinder(2, 20)
    drawing = build_drawing(part)
    owner = next(feature for feature in drawing.model().features if feature.kind == "hole")
    assert owner.count == 2
    parameters = {"grouping.count", "location.location.x", "location.location.y"}
    original = {
        row.parameter_id: row
        for row in drawing.requirement_snapshot().outcomes["holes"]
        if row.parameter_id in parameters
    }
    assert set(original) == parameters
    assert all(row.state == "placed" and row.carriers for row in original.values())
    drawing.registry.add(
        SimpleNamespace(covers_count=99, covers_hole_requirements=("grouping.count",)),
        "wrong_count",
        "plan",
        feature=owner,
        measurement=DimensionId(owner, "bore.diameter"),
    )
    drawing.registry.add(
        SimpleNamespace(covers_hole_locations=((owner, "location.location.x", (999, 999, 5)),)),
        "wrong_point",
        "plan",
        feature=owner,
    )
    for row in drawing.requirement_snapshot().outcomes["holes"]:
        if row.parameter_id in original:
            assert row.state == "placed"
            assert _carriers(row) == _carriers(original[row.parameter_id])


def test_profile_angle_carrier_requires_angular_annotation_type():
    drawing = build_drawing(extrude(RegularPolygon(30, 3), amount=4))
    original = next(
        row
        for row in drawing.requirement_snapshot().outcomes["outer_profile_angles"]
        if row.state == "placed"
    )
    assert original.carriers
    owner = original.features[0]
    drawing.registry.add(
        SimpleNamespace(),
        "not_an_angle",
        "plan",
        feature=owner,
        measurement=DimensionId(owner, original.parameter_id),
    )
    current = next(
        row
        for row in drawing.requirement_snapshot().outcomes["outer_profile_angles"]
        if any(feature is owner for feature in row.features)
    )
    assert current.state == "placed"
    assert _carriers(current) == _carriers(original)
