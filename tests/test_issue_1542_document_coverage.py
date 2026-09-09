"""Shared coverage retains the denominator and proves complete dependency recipes."""

from dataclasses import replace
from pathlib import Path

from build123d import Box, Cylinder, Pos, export_step

from draftwright import Document, build_drawing
from draftwright.document_evidence import (
    bind_document_claims,
    document_conflicts,
    document_support_proofs,
)
from draftwright.measurement_support import MeasurementSupport, RequirementAlternative


def member(document, name):
    sheet = document.sheet(name, detail_view=False).authored_dimensions().authored_views()
    for view in ("front", "plan", "side"):
        sheet.view(view)
    return sheet


def test_shared_coverage_survives_move_and_repetition_but_not_removal(tmp_path):
    path = tmp_path / "hole.step"
    export_step(Box(30, 20, 5) - Cylinder(2, 10), path)
    document = Document.from_part(path)
    hole = next(feature for feature in document.features if feature.kind == "hole")
    member(document, "first").dimension(hole, "bore.diameter")
    member(document, "second").dimension(hole, "bore.diameter")
    result = document.build()
    initial = result._evaluate()
    row = next(
        row for row in initial.requirements if row.requirement.parameter_id == "bore.diameter"
    )
    assert row.state == "placed"
    assert len(initial.claims) == 2
    assert not initial.conflicts
    first, second = result.sheets.values()
    names = [claim.annotation for claim in initial.claims[0].claims if claim.owner is hole]
    for name in names:
        first.remove(name)
    moved = result._evaluate()
    row = next(
        row for row in moved.requirements if row.requirement.parameter_id == "bore.diameter"
    )
    assert row.state == "placed"
    assert dict(row.local)["first"].state != "placed"
    assert dict(row.local)["second"].state == "placed"
    for claim in moved.claims[1].claims:
        if claim.owner is hole:
            second.remove(claim.annotation)
    removed = result._evaluate()
    row = next(
        row for row in removed.requirements if row.requirement.parameter_id == "bore.diameter"
    )
    assert row.state == "uncovered"
    for changed in (moved, removed):
        assert len(changed.requirements) == len(initial.requirements)
        assert all(
            before.requirement is after.requirement
            for before, after in zip(initial.requirements, changed.requirements, strict=True)
        )


def test_dependency_conjunction_requires_each_exact_confirmed_interval():
    drawing = build_drawing(Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15))
    snapshot = drawing.measurement_snapshot()
    coarse = tuple(claim for claim in snapshot.claims if claim.parameter == "step_position.length")
    snapshots = tuple(
        bind_document_claims(
            name, replace(snapshot, claims=(claim,), unknown=()), drawing.registry
        )
        for name, claim in zip(("first", "second"), coarse, strict=True)
    )
    assert len(snapshots) == 2 and all(not item.unknown for item in snapshots)
    supports = tuple(
        MeasurementSupport(
            claim.owner,
            claim.parameter,
            axis="y",
            lo=min(point[1] for point in claim.witnesses[0]),
            hi=max(point[1] for point in claim.witnesses[0]),
        )
        for claim in coarse
    )
    recipe = RequirementAlternative(tuple(term.identity for term in supports), supports)
    proofs = document_support_proofs((recipe,), snapshots, ())
    assert len(proofs) == 1
    assert [{claim.sheet for claim in carriers} for carriers in proofs[0].carriers] == [
        {"first"},
        {"second"},
    ]
    assert not document_support_proofs((recipe,), snapshots[:1], ())
    # Empty rich witnesses and incomplete branches cannot become vacuous coverage.
    assert not document_support_proofs((replace(recipe, supports=()),), snapshots, ())
    absent = replace(supports[0], lo=200, hi=220)
    wrong_axis = replace(supports[1], axis="x")
    alternatives = ((supports[0], absent), (supports[1], wrong_axis))
    assert not document_support_proofs(alternatives, snapshots, ())
    # A different confirmed engineering claim on one interval blocks derived credit;
    # direct ink and both conflicting sides remain inspectable.
    claim = snapshots[0].claims[0]
    other = replace(
        claim, sheet="conflicting", meaning=(claim.meaning[0], 0.02, *claim.meaning[2:])
    )
    conflicting = (*snapshots, replace(snapshots[0], claims=(other,)))
    conflicts = document_conflicts(conflicting)
    assert len(conflicts) == 1
    assert not document_support_proofs((recipe,), conflicting, conflicts)
    # Only actual confirmed claims establish prerequisites; derived outcomes aren't inputs.
    unknown = replace(
        snapshots[0], claims=(), unknown=(("first", claim.annotation, "unconfirmed"),)
    )
    assert not document_support_proofs((recipe,), (unknown, snapshots[1]), ())


def test_repeating_one_interval_cannot_fill_two_nearby_support_terms():
    from draftwright.document_evidence import DocumentClaim, DocumentClaimSnapshot

    owner = object()
    span = ((0.0, 0.0, 0.0), (0.0, 20.0, 0.0))
    claim = DocumentClaim(
        "a",
        owner,
        "step.length",
        "first",
        ("span", span),
        (20.0, None, span, "y", None, None, None),
        (),
        (span, ()),
    )
    other = replace(claim, sheet="b", annotation="repeat")
    support = MeasurementSupport(owner, "step.length", "y", 0.0, 20.0)
    adjacent = replace(support, hi=20.2)
    snapshots = (DocumentClaimSnapshot((claim, other), ()),)
    assert not document_support_proofs(((support, adjacent),), snapshots, ())
    next_span = (span[0], (0.0, 20.2, 0.0))
    distinct = replace(
        claim,
        annotation="second",
        address=("span", next_span),
        meaning=(20.2, None, next_span, "y", None, None, None),
        witnesses=(next_span, ()),
    )
    proven = document_support_proofs(
        ((support, adjacent),), (DocumentClaimSnapshot((claim, other, distinct), ()),), ()
    )
    assert len(proven) == 1
    assert len({item.address for item in proven[0].witnesses}) == 2


def test_u_channel_prerequisites_split_across_sheets_and_live_loss():
    source = Path(__file__).parent / "fixtures/evaluation/plate-u-additive.step"
    document = Document.from_part(source)
    conditional = next(
        row for row in document._catalog.requirements if row.dependency_alternatives
    )
    (recipe,) = conditional.dependency_alternatives
    assert len(recipe.supports) == 3
    for index, support in enumerate(recipe.supports):
        member(document, str(index)).dimension(support.feature, support.parameter_id)
    result = document.build()
    before = result._evaluate()
    target = next(row for row in before.requirements if row.requirement is conditional)
    assert target.state == "dependency-derived"
    assert len(target.dependencies) == 1
    assert {claim.sheet for claim in target.dependencies[0].witnesses} == {"0", "1", "2"}
    assert all(outcome.state == "suppressed" for _name, outcome in target.local)
    carrier = next(claim for claim in target.dependencies[0].witnesses if claim.sheet == "1")
    drawing = result.sheets["1"]
    label = drawing.registry.named(carrier.annotation).label
    drawing.registry.named(carrier.annotation).label = "999"
    unconfirmed = result._evaluate()
    target = next(row for row in unconfirmed.requirements if row.requirement is conditional)
    assert target.state == "uncovered" and not target.dependencies
    assert any(snapshot.unknown for snapshot in unconfirmed.claims)
    drawing.registry.named(carrier.annotation).label = label
    assert (
        next(
            row for row in result._evaluate().requirements if row.requirement is conditional
        ).state
        == "dependency-derived"
    )
    drawing.remove(carrier.annotation)
    removed = result._evaluate()
    target = next(row for row in removed.requirements if row.requirement is conditional)
    assert target.state == "uncovered" and not target.dependencies
    assert len(removed.requirements) == len(before.requirements)


def test_native_plate_does_not_become_derived_when_other_sheets_carry_prerequisites():
    source = Path(__file__).parent / "fixtures/evaluation/plate-u-additive.step"
    document = Document.from_part(source)
    conditional = next(
        row for row in document._catalog.requirements if row.dependency_alternatives
    )
    (recipe,) = conditional.dependency_alternatives
    for index, support in enumerate(recipe.supports):
        member(document, str(index)).dimension(support.feature, support.parameter_id)
    (owner,) = conditional.features
    member(document, "direct").dimension(owner, conditional.parameter_id)
    result = document.build()
    target = next(row for row in result._evaluate().requirements if row.requirement is conditional)
    assert target.state == "placed"
    assert target.dependencies
    assert dict(target.local)["direct"].state == "placed"


def test_grouped_hole_locations_need_all_members_across_sheets(tmp_path):
    part = Box(80, 60, 10)
    for x, y in ((-20, -15), (20, 15)):
        part -= Pos(x, y, 0) * Cylinder(2, 20)
    path = tmp_path / "group.step"
    export_step(part, path)
    document = Document.from_part(path)
    hole = next(feature for feature in document.features if feature.kind == "hole")
    assert hole.count == 2
    for index in (0, 1):
        sheet = member(document, str(index))
        for axis in ("x", "y"):
            sheet.dimension(hole, "location", member=index, axis=axis)
    result = document.build()
    before = result._evaluate()
    locations = [
        row
        for row in before.requirements
        if row.requirement.family == "holes"
        and row.requirement.parameter_id.startswith("location")
    ]
    assert locations and all(row.state == "placed" for row in locations)
    assert all(all(outcome.state != "placed" for _, outcome in row.local) for row in locations)
    snapshot = before.claims[1]
    claim = next(claim for claim in snapshot.claims if claim.parameter.endswith(".x"))
    result.sheets["1"].remove(claim.annotation)
    after = result._evaluate()
    changed = [
        row
        for row in after.requirements
        if row.requirement.family == "holes"
        and row.requirement.parameter_id.startswith("location")
    ]
    assert len(changed) == len(locations)
    assert any(row.state == "uncovered" for row in changed)
