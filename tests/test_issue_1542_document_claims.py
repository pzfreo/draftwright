"""Document agreement uses exact interval/member witnesses and approved engineering meaning."""

from dataclasses import replace

import pytest
from build123d import Box, Cylinder, Pos, export_step

from draftwright import Document, ReportUnavailableError, build_drawing
from draftwright.audit import compare_measurements
from draftwright.document_evidence import bind_document_claims, document_conflicts


@pytest.fixture(scope="module")
def hole_source(tmp_path_factory):
    path = tmp_path_factory.mktemp("document-claims") / "hole.step"
    export_step(Box(30, 20, 5) - Cylinder(2, 10), path)
    return path


def member(document, name):
    sheet = document.sheet(name, detail_view=False).authored_dimensions().authored_views()
    for view in ("front", "plan", "side"):
        sheet.view(view)
    return sheet


@pytest.mark.parametrize("name", ["", "  ", None, 4])
def test_document_refuses_empty_or_nontext_sheet_names(hole_source, name):
    document = Document.from_part(hole_source)
    with pytest.raises(ValueError, match="nonempty name"):
        document.sheet(name)


def test_document_cannot_build_without_a_member(hole_source):
    with pytest.raises(ValueError, match="at least one sheet"):
        Document.from_part(hole_source).build()


def test_document_names_the_member_whose_intent_snapshot_is_incomplete(hole_source):
    from draftwright.document import DocumentBuildError

    document = Document.from_part(hole_source)
    document.sheet("incomplete").authored_views().view("front")
    with pytest.raises(DocumentBuildError, match="incomplete"):
        document.build()


def bound(sheet, drawing):
    return bind_document_claims(sheet, drawing.measurement_snapshot(), drawing.registry)


@pytest.mark.parametrize("aspects", ((0.02, 0.05), (0.02, 0.02), ("class", "deviation")))
def test_confirmed_diameter_tolerances_conflict_but_fit_display_does_not(hole_source, aspects):
    document = Document.from_part(hole_source)
    hole = next(feature for feature in document.features if feature.kind == "hole")
    for name, aspect in zip(("first", "second"), aspects, strict=True):
        sheet = member(document, name)
        sheet.dimension(hole, "bore.diameter")
        if isinstance(aspect, str):
            sheet.of(hole).fit("H7", show=aspect)
        else:
            sheet.of(hole).tolerance(aspect)
    result = document.build()
    result._project_members()
    snapshots = tuple(bound(name, drawing) for name, drawing in result.sheets.items())
    assert all(not snapshot.unknown for snapshot in snapshots)
    assert all(len(snapshot.claims) == 1 for snapshot in snapshots)
    assert all(snapshot.claims[0].owner is hole for snapshot in snapshots)
    conflicts = document_conflicts(snapshots)
    if aspects == (0.02, 0.05):
        assert len(conflicts) == 1
        assert {claim.sheet for claim in conflicts[0].claims} == {"first", "second"}
        assert {claim.meaning[1] for claim in conflicts[0].claims} == {0.02, 0.05}
    else:
        assert not conflicts
    if isinstance(aspects[0], str):
        first, second = (drawing.measurement_snapshot() for drawing in result.sheets.values())
        assert first.claims[0].meaning != second.claims[0].meaning
        assert compare_measurements(first, second)["status"] == "changed"
        assert snapshots[0].claims[0].meaning == snapshots[1].claims[0].meaning
        assert snapshots[0].claims[0].rendered != snapshots[1].claims[0].rendered
        report = result.report()
        assert not report["assessment"]["fidelity"]["conflicts"]
        first_fit, second_fit = (claim["meaning"]["tolerance"] for claim in report["claims"])
        assert first_fit == second_fit
        assert first_fit["kind"] == "fit" and first_fit["code"] == "H7"


@pytest.fixture(scope="module")
def shoulders():
    return build_drawing(Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15))


def test_equal_coarse_claim_bags_bind_to_distinct_shoulder_intervals(shoulders):
    snapshot = shoulders.measurement_snapshot()
    claims = tuple(claim for claim in snapshot.claims if claim.parameter == "step_position.length")
    assert len(claims) == 2 and claims[0].meaning == claims[1].meaning
    first, second = (
        bind_document_claims(
            name, replace(snapshot, claims=(claim,), unknown=()), shoulders.registry
        )
        for name, claim in zip(("first", "second"), claims, strict=True)
    )
    assert not first.unknown and not second.unknown
    assert {first.claims[0].meaning[0], second.claims[0].meaning[0]} == {20, 40}
    assert first.claims[0].address != second.claims[0].address
    assert not document_conflicts((first, second))


def test_wrong_interval_cannot_borrow_sibling_label_confirmation(shoulders):
    snapshot = shoulders.measurement_snapshot()
    first, second = (
        claim for claim in snapshot.claims if claim.parameter == "step_position.length"
    )
    altered = replace(first, witnesses=second.witnesses)
    narrowed = bind_document_claims(
        "damaged", replace(snapshot, claims=(altered,), unknown=()), shoulders.registry
    )
    assert not narrowed.claims
    assert narrowed.unknown == (("damaged", first.annotation, "bound_claim_unconfirmed"),)


def test_missing_witness_cannot_choose_one_of_multiple_approved_intervals(shoulders):
    snapshot = shoulders.measurement_snapshot()
    claim = next(claim for claim in snapshot.claims if claim.parameter == "step_position.length")
    altered = replace(claim, witnesses=())
    narrowed = bind_document_claims(
        "damaged", replace(snapshot, claims=(altered,), unknown=()), shoulders.registry
    )
    assert not narrowed.claims
    assert narrowed.unknown == (("damaged", claim.annotation, "measurement_meaning_ambiguous"),)


def test_equal_pocket_offsets_keep_their_own_measured_axes():
    drawing = build_drawing(Box(50, 50, 30) - Pos(10, 10, 12) * Box(22, 14, 10))
    snapshot = drawing.measurement_snapshot()
    coarse = tuple(
        claim for claim in snapshot.claims if claim.parameter == "location_pocket.location"
    )
    assert len(coarse) == 2 and coarse[0].meaning == coarse[1].meaning
    narrowed = bind_document_claims(
        "pocket", replace(snapshot, claims=coarse, unknown=()), drawing.registry
    )
    assert not narrowed.unknown
    assert len(narrowed.claims) == 2
    assert {claim.address[1] for claim in narrowed.claims} == {
        "location_pocket.location.x",
        "location_pocket.location.y",
    }
    assert all(claim.meaning[3] == "z" for claim in narrowed.claims)
    assert not document_conflicts((narrowed,))


def test_member_cannot_change_layout_datum_to_evade_agreement(hole_source):
    document = Document.from_part(hole_source)
    member(document, "changed")
    result = document.build()
    model = result.sheets["changed"].model()
    original = next(datum for datum in model.datums if datum.id == "datum_xy")
    index = next(index for index, datum in enumerate(model.datums) if datum is original)
    model.datums[index] = replace(original, at=tuple(value + 1 for value in original.at))
    with pytest.raises(ReportUnavailableError, match="changed.*datum is sealed"):
        result._project_members()


def test_unequal_pocket_offset_cannot_borrow_sibling_axis_confirmation():
    drawing = build_drawing(Box(60, 50, 30) - Pos(10, 5, 12) * Box(22, 14, 10))
    snapshot = drawing.measurement_snapshot()
    coarse = tuple(
        claim for claim in snapshot.claims if claim.parameter == "location_pocket.location"
    )
    first = next(claim for claim in coarse if claim.witnesses[1][0][0].endswith(".x"))
    second = next(claim for claim in coarse if claim.witnesses[1][0][0].endswith(".y"))
    assert first.meaning == second.meaning
    assert first.rendered != second.rendered
    clean = bind_document_claims(
        "pocket", replace(snapshot, claims=coarse, unknown=()), drawing.registry
    )
    assert not clean.unknown and len(clean.claims) == 2
    changed = replace(first, witnesses=second.witnesses)
    narrowed = bind_document_claims(
        "damaged", replace(snapshot, claims=(changed,), unknown=()), drawing.registry
    )
    assert not narrowed.claims
    assert narrowed.unknown == (("damaged", first.annotation, "bound_claim_unconfirmed"),)


def test_unique_compiled_span_keeps_address_without_optional_rendered_witness():
    drawing = build_drawing(Box(30, 20, 10))
    snapshot = drawing.measurement_snapshot()
    claim = next(claim for claim in snapshot.claims if claim.parameter == "width.length")
    assert len(claim.meaning) == 1 and claim.witnesses[0]
    first = bind_document_claims(
        "first", replace(snapshot, claims=(claim,), unknown=()), drawing.registry
    )
    without = replace(claim, witnesses=(None, ()))
    second = bind_document_claims(
        "second", replace(snapshot, claims=(without,), unknown=()), drawing.registry
    )
    assert not first.unknown and not second.unknown
    assert first.claims[0].address == second.claims[0].address
    changed = replace(second.claims[0], meaning=(30.0, 0.02, *second.claims[0].meaning[2:]))
    conflicts = document_conflicts((first, replace(second, claims=(changed,))))
    assert len(conflicts) == 1


@pytest.mark.parametrize("coarse", (False, True))
def test_location_witness_removal_keeps_fine_identity_and_exposes_coarse_uncertainty(coarse):
    part = Box(60, 50, 30 if coarse else 10)
    part -= Pos(10, 5, 12) * Box(22, 14, 10) if coarse else Pos(10, 5, 0) * Cylinder(2, 20)
    drawing = build_drawing(part)
    snapshot = drawing.measurement_snapshot()
    original = next(claim for claim in snapshot.claims if claim.witnesses[1])
    before = bind_document_claims(
        "before", replace(snapshot, claims=(original,), unknown=()), drawing.registry
    )
    assert not before.unknown
    annotation = drawing.registry.named(original.annotation)
    annotation.covers_hole_locations = ()
    changed_snapshot = drawing.measurement_snapshot()
    changed = next(
        claim
        for claim in changed_snapshot.claims
        if claim.annotation == original.annotation and claim.parameter == original.parameter
    )
    after = bind_document_claims(
        "after", replace(changed_snapshot, claims=(changed,), unknown=()), drawing.registry
    )
    if coarse:
        assert not after.claims
        assert after.unknown == (("after", original.annotation, "location_component_unavailable"),)
    else:
        assert not after.unknown
        assert after.claims[0].address == before.claims[0].address
        assert after.claims[0].meaning == before.claims[0].meaning


@pytest.mark.parametrize(
    "axis,at,rotation", (("x", (0, -10, -5), (0, 90, 0)), ("y", (-15, 0, -5), (90, 0, 0)))
)
def test_off_axis_locations_keep_compiler_component_with_or_without_rider(axis, at, rotation):
    drawing = build_drawing(Box(60, 50, 30) - Pos(*at) * Cylinder(2, 80, rotation=rotation))
    snapshot = drawing.measurement_snapshot()
    locations = tuple(
        claim for claim in snapshot.claims if claim.parameter.startswith("location_off_axis")
    )
    assert locations
    for claim in locations:
        assert all(item.is_location_measurement for item in claim.approved)
        assert all(item.kind == "length" for item in claim.approved)
        first = bind_document_claims(
            "first", replace(snapshot, claims=(claim,), unknown=()), drawing.registry
        )
        assert not first.unknown and first.claims
        component = first.claims[0].address[1]
        assert component in {"location_off_axis.x", "location_off_axis.y", "location_off_axis.z"}
        drawing.registry.named(claim.annotation).covers_hole_locations = ()
        changed_snapshot = drawing.measurement_snapshot()
        changed = next(
            item
            for item in changed_snapshot.claims
            if item.annotation == claim.annotation and item.parameter == claim.parameter
        )
        second = bind_document_claims(
            "second", replace(changed_snapshot, claims=(changed,), unknown=()), drawing.registry
        )
        assert not second.unknown
        assert first.claims[0].address == second.claims[0].address
