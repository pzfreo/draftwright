"""Public document reports keep common authority, independent assessments and live evidence."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Cylinder, export_step
from jsonschema.validators import validator_for
from test_issue_1542_catalog_families import _CASES, family_part

from draftwright import Document, ReportUnavailableError


def member(document, name):
    sheet = document.sheet(name, detail_view=False).authored_dimensions().authored_views()
    for view in ("front", "plan", "side"):
        sheet.view(view)
    return sheet


def hole_document(tmp_path, tolerances=(0.02, 0.05)):
    path = tmp_path / "hole.step"
    export_step(Box(30, 20, 5) - Cylinder(2, 10), path)
    raw = path.read_bytes()
    document = Document.from_part(path)
    hole = next(feature for feature in document.features if feature.kind == "hole")
    for index, tolerance in enumerate(tolerances):
        sheet = member(document, str(index))
        sheet.dimension(hole, "bore.diameter")
        sheet.of(hole).tolerance(tolerance)
    return document, path, raw


def test_report_conflict_keeps_both_confirmed_values_and_one_coverage_credit(tmp_path):
    document, path, raw = hole_document(tmp_path)
    path.unlink()
    result = document.build()
    report = result.report()
    assert report["schema"] == "draftwright-report"
    assert report["schema_version"] == 4 and report["scope"] == "document"
    assert report["source"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert report["run_options"] == {"pmi": "off", "frame": "raw"}
    assert report["recognition"]["identity_scope"] == "document-local"
    assert report["replay"]["identity_lifetime"] == "this-report-only"
    assert report["assessment"]["manufacturing"]["readiness"] == "not-certified"
    row = next(
        row
        for row in report["recognition"]["requirements"]
        if row["parameter_id"] == "bore.diameter"
    )
    assert row["state"] == "placed" and row["coverage_credit"] == 1
    assert [local["state"] for local in row["local_outcomes"]] == ["placed", "placed"]
    conflicts = report["assessment"]["fidelity"]["conflicts"]
    assert len(conflicts) == 1 and report["status"] == "needs-attention"
    claims = {claim["id"]: claim for claim in report["claims"]}
    disputed = [claims[key] for key in conflicts[0]["claim_ids"]]
    assert {claim["meaning"]["tolerance"] for claim in disputed} == {0.02, 0.05}
    assert len({claim["owner_id"] for claim in disputed}) == 1
    assert len({claim["sheet_id"] for claim in disputed}) == 2
    assert all(
        sheet["lint"]["quality"]["fidelity"]["raw_issues"] == 0 for sheet in report["sheets"]
    )
    assert all(sheet["dimension_intents"]["source"] == "authored" for sheet in report["sheets"])
    json.dumps(report, allow_nan=False)
    assert all(drawing.report()["schema_version"] == 3 for drawing in result.sheets.values())


def test_returned_document_is_detached_and_fresh_reads_lose_removed_credit(tmp_path):
    document, _path, _raw = hole_document(tmp_path, (0.02, 0.02))
    result = document.build()
    before = result.report()
    saved = json.dumps(before, sort_keys=True)
    for drawing in result.sheets.values():
        for claim in drawing.measurement_snapshot().claims:
            if claim.parameter == "bore.diameter":
                drawing.remove(claim.annotation)
    after = result.report()
    assert json.dumps(before, sort_keys=True) == saved
    previous = before["recognition"]["requirements"]
    current = after["recognition"]["requirements"]
    assert [(row["id"], row["occurrence_ids"], row["parameter_id"]) for row in previous] == [
        (row["id"], row["occurrence_ids"], row["parameter_id"]) for row in current
    ]
    row = next(row for row in current if row["parameter_id"] == "bore.diameter")
    assert row["state"] == "uncovered" and row["coverage_credit"] == 0
    assert not after["claims"]


def test_ordinary_carriers_and_dependency_witnesses_name_existing_sheets_and_ink():
    document = Document.from_part(
        Path(__file__).parent / "fixtures/evaluation/plate-u-additive.step"
    )
    conditional = next(
        row for row in document._catalog.requirements if row.dependency_alternatives
    )
    (recipe,) = conditional.dependency_alternatives
    for index, support in enumerate(recipe.supports):
        member(document, str(index)).dimension(support.feature, support.parameter_id)
    result = document.build()
    report = result.report()
    sheets = {row["id"]: result.sheets[row["name"]] for row in report["sheets"]}
    claims = {row["id"]: row for row in report["claims"]}
    derived = [
        row
        for row in report["recognition"]["requirements"]
        if row["state"] == "dependency-derived"
    ]
    assert len(derived) == 1
    assert derived[0]["carrier_attribution"] == "available"
    assert not derived[0]["carrying_annotations"]
    (proof,) = derived[0]["dependency_proofs"]
    assert len(proof["distinct_witnesses"]) == 3
    assert len({claims[key]["sheet_id"] for key in proof["distinct_witnesses"]}) == 3
    placed = [row for row in report["recognition"]["requirements"] if row["state"] == "placed"]
    assert placed and all(row["carrying_annotations"] for row in placed)
    for row in placed:
        assert row["carrier_attribution"] == "available"
        for carrier in row["carrying_annotations"]:
            assert carrier["annotation"] in sheets[carrier["sheet_id"]].annotations()
            assert carrier["evidence_kind"] == "measurement"


def test_writer_is_explicit_strict_and_preserves_destination_on_failure(tmp_path, monkeypatch):
    import draftwright.reporting as reporting

    document, _path, _raw = hole_document(tmp_path)
    result = document.build()
    output = tmp_path / "report.json"
    result.write_report(output)
    first = output.read_bytes()
    assert json.loads(first)["schema_version"] == 4

    def fail_replace(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(reporting.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        result.write_report(output)
    assert output.read_bytes() == first
    assert not list(tmp_path.glob(".draftwright-*"))


def test_public_report_refuses_changed_exact_member_without_overwriting_report(tmp_path):
    document, _path, _raw = hole_document(tmp_path)
    result = document.build()
    output = tmp_path / "report.json"
    result.write_report(output)
    original = output.read_bytes()
    features = result.sheets["0"].model().features
    index = next(i for i, feature in enumerate(features) if feature.kind == "hole")
    features[index] = replace(features[index])
    with pytest.raises(ReportUnavailableError, match="document sheet.*(sealed|matches nothing)"):
        result.write_report(output)
    assert output.read_bytes() == original


@pytest.mark.parametrize("case", _CASES, ids=[case[0] for case in _CASES])
def test_every_family_projects_live_carriers_and_preserves_catalog_after_removal(case, tmp_path):
    source = family_part(case)
    if not isinstance(source, Path):
        path = tmp_path / "part.step"
        export_step(source, path)
        source = path
    document = Document.from_part(source)
    document.sheet("drawing", detail_view=False).auto_dimensions().auto_views()
    result = document.build()
    before = result.report()
    schema = json.loads(
        (
            Path(__file__).parents[1] / "docs/reference/draftwright-report-v4.schema.json"
        ).read_text()
    )
    validator_for(schema).check_schema(schema)
    validator_for(schema)(schema).validate(before)
    rows = before["recognition"]["requirements"]
    family = [row for row in rows if row["family"] == case[0]]
    assert family, "the public report must retain this source family's requirements"
    direct = [row for row in family if row["state"] in {"placed", "satisfied_by_structured_note"}]
    if case[0] not in {"section_recesses", "through_steps"}:
        assert direct, "this positive fixture must exercise actual carrier attribution"
    drawing = result.sheets["drawing"]
    for row in direct:
        assert row["carrier_attribution"] == "available", row
        assert row["carrying_annotations"], row
        assert all(
            carrier["annotation"] in drawing.annotations()
            for carrier in row["carrying_annotations"]
        )
    for name in tuple(drawing.annotations()):
        drawing.remove(name)
    after = result.report()

    def identity(row):
        return (
            row["id"],
            row["family"],
            row["occurrence_ids"],
            row["parameter_id"],
            row["requirement_count"],
            row["requirement_count_known"],
        )

    assert [identity(row) for row in after["recognition"]["requirements"]] == [
        identity(row) for row in rows
    ]
    assert not any(row["coverage_credit"] for row in after["recognition"]["requirements"])
    assert not after["claims"]
    assert after["status"] == "needs-attention"


def test_mixed_axis_operations_move_live_without_substitution_or_denominator_drift(tmp_path):
    from build123d import Pos, Rot

    path = tmp_path / "mixed.step"
    part = Box(40, 30, 20) - Pos(-10, 0, 0) * Cylinder(2, 30)
    part -= Pos(15, 5, 3) * Rot(0, 90, 0) * Cylinder(2, 10)
    export_step(part, path)
    document = Document.from_part(path)
    holes = [feature for feature in document.features if feature.kind == "hole"]
    assert len(holes) == 2
    through = next(feature for feature in holes if feature.through)
    blind = next(feature for feature in holes if not feature.through)
    assert through.frame.axis == "z" and blind.frame.axis == "x"
    assert through.diameter == blind.diameter == 4
    assert blind.depth == 10
    member(document, "through").dimension(through, "bore.diameter")
    sockets = member(document, "blind")
    sockets.dimension(blind, "bore.diameter")
    sockets.dimension(blind, "bore.depth")
    # A live callout respects authored omissions; declare the destination intent first.
    sockets.dimension(through, "bore.diameter")
    sockets.notes(["REVIEW ONLY"], number=False)
    result = document.build()
    destination = result.sheets["blind"]
    destination.drop(through)
    before = result.report()
    diameter_rows = [
        row
        for row in before["recognition"]["requirements"]
        if row["parameter_id"] == "bore.diameter"
    ]
    assert len(diameter_rows) == 2 and all(row["coverage_credit"] == 1 for row in diameter_rows)
    assert len({tuple(row["owner_ids"]) for row in diameter_rows}) == 2
    assert not before["assessment"]["fidelity"]["conflicts"]
    view_intents = before["sheets"][1]["view_intents"]
    assert view_intents["principal_source"] == "authored"
    assert [item["spec"]["name"] for item in view_intents["principals"]] == [
        "front",
        "plan",
        "side",
    ]
    assert before["sheets"][1]["table_intents"][0]["rows"][-1] == ["REVIEW ONLY"]
    first, second = result.sheets.values()
    original = next(
        claim.annotation for claim in first.measurement_snapshot().claims if claim.owner is through
    )
    moved_name = second.callout(through)
    assert moved_name in second.annotations()
    first.remove(original)
    moved = result.report()
    assert not moved["assessment"]["fidelity"]["conflicts"]
    assert [row["coverage_credit"] for row in moved["recognition"]["requirements"]] == [
        row["coverage_credit"] for row in before["recognition"]["requirements"]
    ]
    second.remove(moved_name)
    after = result.report()
    assert [
        (row["id"], row["occurrence_ids"], row["parameter_id"])
        for row in after["recognition"]["requirements"]
    ] == [
        (row["id"], row["occurrence_ids"], row["parameter_id"])
        for row in before["recognition"]["requirements"]
    ]
    remaining = [
        row
        for row in after["recognition"]["requirements"]
        if row["parameter_id"] == "bore.diameter"
    ]
    assert sorted(row["coverage_credit"] for row in remaining) == [0, 1]


def test_actual_through_step_conjunctions_span_members_and_reject_wrong_support(tmp_path):
    from build123d import Pos, Rot

    path = tmp_path / "through-step.step"
    export_step(Rot(90, 0, 0) * (Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30)), path)
    document = Document.from_part(path)
    steps = member(document, "steps")
    overall = member(document, "overall")
    rows = [row for row in document._catalog.requirements if row.family == "through_steps"]
    assert len(rows) == 2
    for row in rows:
        (alternative,) = row.dependency_alternatives
        assert len(alternative) == 2
        first, second = alternative
        assert first.feature.kind == "step_level" and second.feature.kind == "envelope"
        steps.dimension(first.feature, first.parameter_id)
        overall.dimension(second.feature, second.parameter_id)
    result = document.build()
    before = result.report()
    carried = [
        row for row in before["recognition"]["requirements"] if row["family"] == "through_steps"
    ]
    assert all(row["state"] == "dependency-derived" for row in carried)
    claims = {claim["id"]: claim for claim in before["claims"]}
    for row in carried:
        (proof,) = row["dependency_proofs"]
        assert len(proof["distinct_witnesses"]) == 2
        assert {claims[key]["sheet_id"] for key in proof["distinct_witnesses"]} == {
            "sheet:1",
            "sheet:2",
        }
    drawing = result.sheets["steps"]
    height = next(
        claim
        for claim in drawing.measurement_snapshot().claims
        if claim.parameter == "step_height.length"
    )
    annotation = drawing.registry.named(height.annotation)
    original = annotation.label
    assert original != "999"
    annotation.label = "999"
    wrong = result.report()
    damaged = [
        row for row in wrong["recognition"]["requirements"] if row["family"] == "through_steps"
    ]
    assert sorted(row["state"] for row in damaged) == ["dependency-derived", "uncovered"]
    assert wrong["assessment"]["fidelity"]["unknown_claims"]
    annotation.label = original
    assert all(
        row["state"] == "dependency-derived"
        for row in result.report()["recognition"]["requirements"]
        if row["family"] == "through_steps"
    )
    drawing.remove(height.annotation)
    removed = result.report()
    assert [
        (row["id"], row["parameter_id"]) for row in removed["recognition"]["requirements"]
    ] == [(row["id"], row["parameter_id"]) for row in before["recognition"]["requirements"]]
    assert sorted(
        row["state"]
        for row in removed["recognition"]["requirements"]
        if row["family"] == "through_steps"
    ) == ["dependency-derived", "uncovered"]


@pytest.mark.parametrize("source_less", (False, True))
def test_producer_unknown_cardinality_never_becomes_a_known_clean_denominator(
    monkeypatch, source_less
):
    from draftwright.linting import requirements

    original = requirements.recognized_requirement_outcomes

    def unknown_count(*args, **kwargs):
        outcomes = dict(original(*args, **kwargs))
        first, *rest = outcomes["grooves"]
        assert first.source_records and first.requirement_count_known
        outcomes["grooves"] = (
            replace(
                first,
                parameter_id="?",
                requirement_count=1,
                requirement_count_known=False,
                source_records=() if source_less else first.source_records,
            ),
            *rest,
        )
        return outcomes

    monkeypatch.setattr(requirements, "recognized_requirement_outcomes", unknown_count)
    source = Path(__file__).parent / "fixtures/evaluation/groove-lone-z.step"
    if source_less:
        with pytest.raises(ReportUnavailableError, match="no exact source"):
            Document.from_part(source)
        return
    document = Document.from_part(source)
    document.sheet("drawing", detail_view=False).auto_dimensions().auto_views()
    report = document.build().report()
    unknown = [
        row for row in report["recognition"]["requirements"] if not row["requirement_count_known"]
    ]
    assert len(unknown) == 1 and unknown[0]["state"] == "unresolved"
    assert unknown[0]["coverage_credit"] == 0
    coverage = report["assessment"]["coverage"]
    assert coverage["unknown_cardinality_rows"] == 1 and coverage["audited_score"] is None
    assert report["status"] == "needs-attention"
