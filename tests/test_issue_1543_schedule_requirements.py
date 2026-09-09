"""Physical ledgers accept only verified ink from feature schedule cells."""

import pytest
from build123d import Box, Cylinder, Pos, export_step

from draftwright import Document


@pytest.fixture(scope="module")
def scheduled_holes(tmp_path_factory):
    source = tmp_path_factory.mktemp("schedule-requirements") / "part.step"
    part = Box(60, 50, 10)
    for point in ((-12, -8, 0), (14, 9, 0)):
        part -= Pos(*point) * Cylinder(2, 10)
    export_step(part, source)
    document = Document.from_part(source)
    holes = [feature for feature in document.features if feature.kind == "hole"]
    assert len(holes) == 1 and holes[0].count == 2
    sheet = document.sheet("features", detail_view=False, page="A3").authored_views()
    sheet.view("front")
    sheet.schedule([(holes[0], ("bore.diameter", "location"))], name="holes")
    result = document.build()
    return result, result.sheets["features"]


def hole_outcomes(drawing):
    outcomes = drawing.requirement_snapshot().outcomes["holes"]
    return {row.parameter_id: row for row in outcomes}


def test_verified_cells_feed_existing_physical_requirement_producers(scheduled_holes):
    _result, drawing = scheduled_holes
    rows = hole_outcomes(drawing)
    assert rows["bore.diameter"].state == "placed"
    assert rows["bore.through"].state == "placed"
    assert rows["grouping.count"].state == "placed"
    locations = [row for key, row in rows.items() if key.startswith("location.")]
    assert len(locations) == 2 and all(row.state == "placed" for row in locations)
    (carrier,) = rows["bore.diameter"].carriers
    assert carrier.annotation == "holes"
    assert carrier.cell in drawing.registry.cells_of("holes")
    assert carrier.cell.measurement.parameter == "bore.diameter"
    for key in ("bore.through", "grouping.count"):
        assert rows[key].carriers == (
            type(carrier)("holes", "physical_requirement", carrier.cell),
        )
    for row in locations:
        assert len(row.carriers) == 2
        assert all(item.cell in drawing.registry.cells_of("holes") for item in row.carriers)
        assert all(
            item.cell.measurement.parameter.endswith(row.parameter_id[-2:])
            for item in row.carriers
        )


@pytest.mark.parametrize("damage", ("wrong-value", "missing-row", "lost-provenance"))
def test_damaged_diameter_cell_loses_its_physical_riders(scheduled_holes, monkeypatch, damage):
    _result, drawing = scheduled_holes
    before = hole_outcomes(drawing)
    assert all(
        before[key].state == "placed"
        for key in ("bore.diameter", "bore.through", "grouping.count")
    )
    table = drawing.get_annotation("holes")
    cell = next(
        cell
        for cell in drawing.registry.cells_of("holes")
        if cell.measurement.parameter == "bore.diameter"
    )
    if damage == "lost-provenance":
        cells_of = drawing.registry.cells_of
        monkeypatch.setattr(
            drawing.registry,
            "cells_of",
            lambda name: tuple(item for item in cells_of(name) if item != cell),
        )
    else:
        content = [list(row) for row in table.table_rows]
        if damage == "wrong-value":
            content[cell.row][cell.column] = "Ø99 THRU"
        else:
            del content[cell.row]
        monkeypatch.setattr(table, "table_rows", tuple(tuple(row) for row in content))
    after = hole_outcomes(drawing)
    assert set(after) == set(before)
    for key in ("bore.diameter", "bore.through", "grouping.count"):
        assert after[key].state != "placed", (key, after[key])
        assert not after[key].carriers


def test_one_bad_location_cell_loses_only_that_axis_proof(scheduled_holes, monkeypatch):
    _result, drawing = scheduled_holes
    before = hole_outcomes(drawing)
    assert not any(issue.code == "feature_not_located" for issue in drawing.lint())
    target = next(
        cell
        for cell in drawing.registry.cells_of("holes")
        if cell.measurement.parameter.endswith(".x")
    )
    table = drawing.get_annotation("holes")
    content = [list(row) for row in table.table_rows]
    content[target.row][target.column] = "999"
    monkeypatch.setattr(table, "table_rows", tuple(tuple(row) for row in content))
    after = hole_outcomes(drawing)
    assert before["location.location.x"].state == "placed"
    assert after["location.location.x"].state != "placed"
    assert not after["location.location.x"].carriers
    assert any(issue.code == "feature_not_located" for issue in drawing.lint())
    for key in ("bore.diameter", "bore.through", "grouping.count", "location.location.y"):
        assert after[key].state == "placed"
        assert after[key].carriers == before[key].carriers


def test_direct_lint_uses_verified_schedule_evidence(scheduled_holes, monkeypatch):
    _result, drawing = scheduled_holes
    before_codes = {issue.code for issue in drawing.lint()}
    assert not ({"feature_not_dimensioned", "feature_not_located"} & before_codes)
    assert "feature_no_centermark" in before_codes
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code == "hole_requirement_missing" and "bore.diameter" in issue.message
    ]
    table = drawing.get_annotation("holes")
    target = next(
        cell
        for cell in drawing.registry.cells_of("holes")
        if cell.measurement.parameter == "bore.diameter"
    )
    content = [list(row) for row in table.table_rows]
    content[target.row][target.column] = "Ø99 THRU"
    monkeypatch.setattr(table, "table_rows", tuple(tuple(row) for row in content))
    issues = drawing.lint()
    assert any(issue.code == "feature_not_dimensioned" for issue in issues)
    assert [
        issue
        for issue in issues
        if issue.code == "hole_requirement_missing" and "bore.diameter" in issue.message
    ]


def test_rounded_schedule_cell_proves_exact_through_step_interval(monkeypatch):
    from build123d import Rot

    from draftwright import Sheet
    from draftwright.linting.schedule_evidence import verified_schedule_registry
    from draftwright.linting.through_step_coverage import through_step_requirement_outcomes
    from draftwright.model import plate
    from draftwright.model.compiled import compile_dimensions

    part = Rot(90, 0, 0) * (Box(40, 30.246, 20) - Pos(15, 10.123, 0) * Box(20, 20.246, 30))
    height = plate(axis="z", lo=0, hi=15.123, u=0, v=0)
    position = plate(axis="x", lo=5, hi=20, u=0, v=0)
    sheet = Sheet(part, page="A3", detail_view=False).authored_views()
    sheet.view("front")
    for feature in (height, position):
        sheet.add(feature)
    sheet.schedule(
        [(feature, ("thickness.length",)) for feature in (height, position)], name="steps"
    )
    drawing = sheet.build()
    drawing.lint()  # Explicit physical critique obtains the declared build's one aggregate.
    plan = compile_dimensions(drawing.model())
    table = drawing.get_annotation("steps")
    (cell,) = [
        cell for cell in drawing.registry.cells_of("steps") if cell.measurement.feature is height
    ]
    assert height.hi - height.lo == 15.123
    assert table.table_rows[cell.row][cell.column] == "15.1"

    def outcomes():
        return through_step_requirement_outcomes(
            drawing.recognition(),
            drawing.model().features,
            verified_schedule_registry(drawing.registry, plan),
            plan=plan,
        )

    before = outcomes()
    assert len(before) == 2 and {row.state for row in before} == {"inapplicable"}
    vertical = next(row for row in before if row.parameter_id.endswith(".z"))
    (alternative,) = vertical.dependency_alternatives
    (support,) = alternative
    assert support.lo == 0 and support.hi == 15.123 and support.feature is height
    content = [list(row) for row in table.table_rows]
    content[cell.row][cell.column] = "999"
    monkeypatch.setattr(table, "table_rows", tuple(tuple(row) for row in content))
    after = outcomes()
    assert {row.parameter_id: row.state for row in after} == {
        "through_step_leg.length.z": "missing",
        "through_step_leg.length.x": "inapplicable",
    }
    assert all(
        current.source_records == previous.source_records
        for current, previous in zip(after, before, strict=True)
    )


def validate_schedule_report(report):
    import json
    from pathlib import Path

    from jsonschema.validators import validator_for

    schema = json.loads(
        (
            Path(__file__).parents[1] / "docs/reference/draftwright-report-v5.schema.json"
        ).read_text()
    )
    validator_for(schema).check_schema(schema)
    validator_for(schema)(schema).validate(report)
    json.dumps(report, allow_nan=False)


def test_document_report_preserves_exact_cells_and_schedule_intent(scheduled_holes):
    result, drawing = scheduled_holes
    report = result.report()
    assert report["schema_version"] == 5
    validate_schedule_report(report)
    assert drawing.report()["schema_version"] == 3
    (sheet,) = report["sheets"]
    (intent,) = sheet["schedule_intents"]
    assert intent["name"] == "holes" and intent["rows"][0]["parameters"] == [
        "bore.diameter",
        "location",
    ]
    cells = drawing.registry.cells_of("holes")
    assert len(report["claims"]) == len(cells) == 5
    assert {(claim["cell"]["row"], claim["cell"]["column"]) for claim in report["claims"]} == {
        (cell.row, cell.column) for cell in cells
    }
    assert {claim["owner_id"] for claim in report["claims"]} == {intent["rows"][0]["owner_id"]}
    assert all(
        claim["sheet_id"] == sheet["id"] and claim["annotation"] == "holes"
        for claim in report["claims"]
    )
    carried = [row for row in report["recognition"]["requirements"] if row["state"] == "placed"]
    assert carried and all(row["carrying_annotations"] for row in carried)
    assert all(
        carrier["cell"] is not None for row in carried for carrier in row["carrying_annotations"]
    )
    assert "feature-schedules" in report["replay"]["serialized_declaration_scope"]
    assert report["replay"]["script_deserialization"] is False


def test_bad_cells_keep_specific_and_unlocated_document_uncertainty(scheduled_holes, monkeypatch):
    result, drawing = scheduled_holes
    cell = drawing.registry.cells_of("holes")[1]
    table = drawing.get_annotation("holes")
    content = [list(row) for row in table.table_rows]
    content[cell.row][cell.column] = "999"
    monkeypatch.setattr(table, "table_rows", tuple(tuple(row) for row in content))
    report = result.report()
    validate_schedule_report(report)
    unknown = report["assessment"]["fidelity"]["unknown_claims"]
    assert any(item["cell"] == {"row": cell.row, "column": cell.column} for item in unknown)
    assert any(item["cell"] is None for item in unknown)
    assert len(report["claims"]) == 4
    assert all(
        claim["cell"] != {"row": cell.row, "column": cell.column} for claim in report["claims"]
    )
    assert report["status"] == "needs-attention"


def test_removing_schedule_retains_v5_intent_and_the_requirement_denominator(scheduled_holes):
    result, drawing = scheduled_holes
    before = result.report()
    items = list(drawing.items)
    registry = drawing.registry.snapshot()
    issues = drawing.registry.issues
    try:
        drawing.remove("holes")
        after = result.report()
        validate_schedule_report(after)
        assert after["schema_version"] == 5 and not after["claims"]
        assert after["sheets"][0]["schedule_intents"] == before["sheets"][0]["schedule_intents"]
        old_rows = before["recognition"]["requirements"]
        rows = after["recognition"]["requirements"]
        assert [(row["id"], row["parameter_id"], row["requirement_count"]) for row in rows] == [
            (row["id"], row["parameter_id"], row["requirement_count"]) for row in old_rows
        ]
        assert not any(row["coverage_credit"] for row in rows)
    finally:
        drawing.items[:] = items
        drawing.registry.restore(registry)
        drawing.registry.restore_issues(issues)


@pytest.fixture(scope="module")
def schedule_report_inputs(scheduled_holes):
    from unittest.mock import patch

    import draftwright.reporting as reporting

    result, _drawing = scheduled_holes
    with patch("draftwright.document.document_report", wraps=reporting.document_report) as project:
        report = result.report()
    assert report["schema_version"] == 5 and report["claims"]
    return project.call_args.kwargs


@pytest.mark.parametrize(
    "damage",
    (
        "absent-claim-cell",
        "copied-carrier-cell",
        "invalid-unknown-cell",
        "missing-recipe",
        "foreign-recipe-owner",
    ),
)
def test_v5_projection_refuses_unproven_cell_and_recipe_references(schedule_report_inputs, damage):
    from dataclasses import replace

    from draftwright import ReportUnavailableError
    from draftwright.audit import MeasurementCellUncertainty
    from draftwright.reporting import document_report

    inputs = dict(schedule_report_inputs)
    evaluation = inputs["evaluation"]
    expected = "cell"
    if damage == "absent-claim-cell":
        snapshots = list(evaluation.claims)
        snapshot = snapshots[0]
        claims = list(snapshot.claims)
        claims[0] = replace(claims[0], cell=(999, 4))
        snapshots[0] = replace(snapshot, claims=tuple(claims))
        inputs["evaluation"] = replace(evaluation, claims=tuple(snapshots))
    elif damage == "copied-carrier-cell":
        rows = list(evaluation.requirements)
        index = next(
            index
            for index, row in enumerate(rows)
            if any(carrier.cell is not None for carrier in row.combined.outcome.carriers)
        )
        row = rows[index]
        carriers = list(row.combined.outcome.carriers)
        carrier = next(item for item in carriers if item.cell is not None)
        carriers[carriers.index(carrier)] = replace(carrier, cell=replace(carrier.cell))
        outcome = replace(row.combined.outcome, carriers=tuple(carriers))
        rows[index] = replace(row, combined=replace(row.combined, outcome=outcome))
        inputs["evaluation"] = replace(evaluation, requirements=tuple(rows))
    elif damage == "invalid-unknown-cell":
        snapshots = list(evaluation.claims)
        snapshots[0] = replace(
            snapshots[0],
            cell_unknown=(("features", MeasurementCellUncertainty("holes", (0, 4), "invalid")),),
        )
        inputs["evaluation"] = replace(evaluation, claims=tuple(snapshots))
    else:
        recipes = {name: dict(recipe) for name, recipe in inputs["member_recipes"].items()}
        recipe = recipes["features"]
        if damage == "missing-recipe":
            del recipe["schedules"]
            expected = "schedule intent"
        else:
            schedule = recipe["schedules"][0]
            row = schedule.rows[0]
            recipe["schedules"] = (
                replace(schedule, rows=(replace(row, feature=replace(row.feature)),)),
            )
            expected = "exact common owner"
        inputs["member_recipes"] = recipes
    with pytest.raises(ReportUnavailableError, match=expected):
        document_report(**inputs)


def test_notes_only_receive_explicit_satisfaction_beside_schedule_cells(tmp_path):
    source = tmp_path / "part.step"
    export_step(Box(30, 20, 5) - Cylinder(2, 10), source)
    document = Document.from_part(source)
    hole = next(feature for feature in document.features if feature.kind == "hole")
    schedule = document.sheet("schedule", detail_view=False, page="A3").authored_views()
    schedule.view("front")
    schedule.schedule([(hole, ("location",))], name="locations")
    schedule.note("HOLE DIAMETER 4", hole)
    schedule.table((("Description", "Value"), ("HOLE DIAMETER", "4")), name="description")
    note = (
        document.sheet("instruction", detail_view=False, page="A3")
        .authored_dimensions()
        .authored_views()
    )
    note.view("front")
    note.note("HOLE DIAMETER 4", hole, satisfies=("bore.diameter",))
    result = document.build()
    before = result.report()
    validate_schedule_report(before)
    row = next(
        row
        for row in before["recognition"]["requirements"]
        if row["parameter_id"] == "bore.diameter"
    )
    assert row["state"] == "satisfied_by_structured_note" and row["coverage_credit"] == 1
    (carrier,) = row["carrying_annotations"]
    assert carrier["evidence_kind"] == "structured_note" and carrier["cell"] is None
    assert all(claim["parameter_id"] != "bore.diameter" for claim in before["claims"])
    result.sheets["instruction"].remove(carrier["annotation"])
    after = result.report()
    row = next(
        row
        for row in after["recognition"]["requirements"]
        if row["parameter_id"] == "bore.diameter"
    )
    assert row["coverage_credit"] == 0 and not row["carrying_annotations"]
    assert "description" in result.sheets["schedule"].annotations()


def test_mixed_ordinary_and_schedule_members_keep_both_conflicting_claims(tmp_path):
    from test_issue_1542_document_report import hole_document

    document, _path, _raw = hole_document(tmp_path, (0.02, 0.05))
    hole = next(feature for feature in document.features if feature.kind == "hole")
    schedule = document.sheet("schedule", detail_view=False, page="A3").authored_views()
    schedule.view("front")
    schedule.of(hole).tolerance(0.02)
    schedule.schedule([(hole, ("bore.diameter",))], name="diameter")
    result = document.build()
    report = result.report()
    validate_schedule_report(report)
    claims = report["claims"]
    assert len(claims) == 3
    assert sum(claim["cell"] is None for claim in claims) == 2
    (scheduled,) = [claim for claim in claims if claim["cell"] is not None]
    assert scheduled["cell"] == {"row": 1, "column": 4}
    (conflict,) = report["assessment"]["fidelity"]["conflicts"]
    assert set(conflict["claim_ids"]) == {claim["id"] for claim in claims}
    diameter = next(
        row
        for row in report["recognition"]["requirements"]
        if row["parameter_id"] == "bore.diameter"
    )
    assert diameter["coverage_credit"] == 1
    assert len(diameter["carrying_annotations"]) == 3


def test_descriptive_table_cannot_credit_unsupported_profile_values(tmp_path):
    from test_issue_1438_report_projection import _passage_part

    source = tmp_path / "profile.step"
    export_step(_passage_part(), source)
    document = Document.from_part(source)
    envelope = next(feature for feature in document.features if feature.kind == "envelope")
    sheet = document.sheet("dimensions", detail_view=False, page="A3").authored_views()
    sheet.view("front")
    sheet.schedule([(envelope, ("width.length",))], name="envelope")
    result = document.build()
    before = result.report()
    unsupported = [
        row for row in before["recognition"]["requirements"] if row["family"] == "section_recesses"
    ]
    assert unsupported and all(row["coverage_credit"] == 0 for row in unsupported)
    result.sheets["dimensions"].add_table(
        (("Feature", "Size"), ("HEX PROFILE", "AF 10.4 DEPTH 10")), name="description"
    )
    after = result.report()
    validate_schedule_report(after)
    assert [
        row for row in after["recognition"]["requirements"] if row["family"] == "section_recesses"
    ] == unsupported
    assert not any(claim["annotation"] == "description" for claim in after["claims"])


def test_bolt_circle_cell_cannot_cover_a_physical_bore_of_the_same_diameter(tmp_path):
    from math import cos, pi, sin

    source = tmp_path / "bolt-circle.step"
    part = Box(100, 60, 10) - Pos(25, 0, 0) * Cylinder(10, 10)
    for index in range(3):
        angle = index * 2 * pi / 3
        part -= Pos(-25 + 10 * cos(angle), 10 * sin(angle), 0) * Cylinder(1, 10)
    export_step(part, source)
    document = Document.from_part(source)
    pattern = next(
        feature
        for feature in document.features
        if getattr(feature, "pattern", None) == "bolt_circle"
    )
    assert pattern.bcd == pytest.approx(20)
    assert any(feature.kind == "hole" and feature.diameter == 20 for feature in document.features)
    sheet = document.sheet("bolts", page="A3", detail_view=False).authored_views()
    sheet.view("front")
    sheet.schedule([(pattern, ("bore.diameter", "bolt_circle.diameter"))], name="bolts")
    drawing = document.build().sheets["bolts"]
    claims = drawing.measurement_snapshot().claims
    assert any(claim.parameter == "bolt_circle.diameter" for claim in claims)
    missing = [issue for issue in drawing.lint() if issue.code == "feature_not_dimensioned"]
    assert len(missing) == 1 and "ø20" in missing[0].message


@pytest.mark.parametrize("kind", ("boss", "step", "pad"))
@pytest.mark.parametrize("front_only", (False, True))
def test_verified_schedule_cells_reconcile_legacy_geometric_coverage(
    kind, front_only, monkeypatch
):
    from draftwright import Sheet

    if kind == "boss":
        solid = Pos(0, 0, 24) * Cylinder(7, 10)
        sheet = Sheet(Box(90, 64, 38) + solid, page="A3", detail_view=False)
        feature = sheet.boss(solid)
        rows = [(feature, ("boss_height.length",))]
        code = "boss_height_missing"
    else:
        part = {
            "step": lambda: Cylinder(15, 30) + Pos(0, 0, 30) * Cylinder(8, 30),
            "pad": lambda: Box(60, 50, 10) + Pos(15, 8, 10) * Box(20, 16, 10),
        }[kind]()
        sheet = Sheet.from_part(part, page="A3", detail_view=False).take_over(
            dimensions="authored",
            principal_views="authored" if front_only else "automatic",
            derived_views="authored",
        )
        features = [feature for feature in sheet.features if feature.kind == kind]
        assert features
        rows = [
            (
                feature,
                ("step.length",)
                if kind == "step"
                else tuple(parameter.parameter_id for parameter in feature.parameters()),
            )
            for feature in features
        ]
        code = {
            "step": "axial_length_missing",
            "pad": "pad_footprint_not_defined",
        }[kind]
    if kind == "pad":
        sheet.note("LOCATED BY AUTHORED COORDINATES", features[0], satisfies=("location",))
    if front_only:
        sheet.authored_views().view("front")
    sheet.schedule(rows, name="features")
    drawing = sheet.build()
    if front_only:
        assert set(drawing.views) == {"front"}
    assert not any(issue.code == code for issue in drawing.lint())
    cells = drawing.registry.cells_of("features")
    assert cells
    target = next((cell for cell in cells if cell.measurement.parameter.endswith(".x")), cells[0])
    table = drawing.get_annotation("features")
    content = [list(row) for row in table.table_rows]
    content[target.row][target.column] = "999"
    monkeypatch.setattr(table, "table_rows", tuple(tuple(row) for row in content))
    assert any(issue.code == code for issue in drawing.lint())
