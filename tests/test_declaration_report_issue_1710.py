"""Declared reports join editable intent to exact final representation (#1710)."""

import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Cylinder, export_step
from jsonschema.validators import validator_for

from draftwright import Sheet
from draftwright.model import DeclarationIdentity
from draftwright.reporting import declared_drawing_report
from draftwright.sheet_emit import generate_sheet_script, inspection_sidecar_path

_SCHEMA = Path(__file__).parents[1] / "docs/reference/draftwright-report-v7.schema.json"


def test_report_maps_selector_owner_parameter_and_exact_live_ink() -> None:
    part = Box(60, 40, 5) - Cylinder(3, 10)
    sheet = Sheet(part)
    hole = sheet.hole(diameter=6, at=(0, 0, 2.5), axis="z", depth=5).identify(
        "declaration:1",
        provenance="detected-geometry",
        occurrence_ids=("holes:1",),
    )
    sheet.authored_dimensions()
    sheet.dimension(hole, "bore.diameter")

    report = sheet.build().report()

    (entry,) = report["declarations"]["entries"]
    assert entry["selector"] == {
        "verb": "sheet.by_declaration",
        "argument": "declaration:1",
    }
    assert entry["owner"] == {"id": "hole:1", "kind": "hole"}
    assert "bore.diameter" in entry["parameters"]
    assert entry["recognition"]["occurrence_ids"] == ["holes:1"]
    assert any(
        "bore.diameter" in representation["measurements"]
        and representation["name"]
        and representation["view"] in {"front", "plan", "side", "iso"}
        for representation in entry["representations"]
    )
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    validator_for(schema).check_schema(schema)
    validator_for(schema)(schema).validate(report)


def test_equal_valued_features_keep_distinct_report_owners() -> None:
    sheet = Sheet(Box(40, 30, 10))
    sheet.authored_dimensions()
    sheet.hole(diameter=4, at=(0, 0, 0), axis="z").identify("declaration:1")
    sheet.hole(diameter=4, at=(0, 0, 0), axis="z").identify("declaration:2")
    model = sheet.model()

    report = declared_drawing_report(
        model=model,
        lint={
            "passed": True,
            "warnings": 0,
            "quality": {"completeness": {"coverage": "complete", "audited_score": 1.0}},
        },
        source=None,
    )

    assert [entry["id"] for entry in report["declarations"]["entries"]] == [
        "declaration:1",
        "declaration:2",
    ]
    assert [entry["owner"]["id"] for entry in report["declarations"]["entries"]] == [
        "hole:1",
        "hole:2",
    ]
    with pytest.raises(ValueError, match="unique declaration IDs"):
        replace(
            model,
            declaration_identities=(
                DeclarationIdentity("declaration:1"),
                DeclarationIdentity("declaration:1"),
            ),
        )


def test_structured_note_has_its_own_selector_and_exact_ink() -> None:
    part = Box(60, 40, 5) - Cylinder(3, 10)
    sheet = Sheet(part)
    hole = sheet.hole(diameter=6, at=(0, 0, 2.5), axis="z", depth=5).identify("declaration:hole")
    sheet.structured_note(
        "BORE DIAMETER VERIFIED",
        hole,
        satisfies=("bore.diameter",),
    ).identify("declaration:note", provenance="structured-note")
    sheet.authored_dimensions()
    sheet.dimension(sheet.envelope(), "width.length")

    entries = {entry["id"]: entry for entry in sheet.build().report()["declarations"]["entries"]}

    note = entries["declaration:note"]
    assert note["feature_kind"] == "note"
    assert note["selector"]["argument"] == "declaration:note"
    assert note["representations"]
    assert any(
        "bore.diameter" in representation["satisfactions"]
        for representation in entries["declaration:hole"]["representations"]
    )


def test_generated_occurrence_references_are_corroborated_by_the_exact_sidecar(tmp_path) -> None:
    step = tmp_path / "source.step"
    export_step(Box(40, 30, 5) - Cylinder(2, 10), step)
    script = Path(generate_sheet_script(step, out=str(tmp_path / "generated")))
    source = script.read_text(encoding="utf-8")
    marker = "drawing = sheet.build()"
    namespace = {}
    exec(  # noqa: S102 - exercising the generated public DSL is the contract
        compile(source[: source.index(marker) + len(marker)], str(script), "exec"),
        namespace,
    )

    report = namespace["drawing"].report()
    sidecar = json.loads(Path(inspection_sidecar_path(str(script))).read_text(encoding="utf-8"))
    by_owner = defaultdict(list)
    for occurrence in sidecar["found"]:
        for owner_id in occurrence["draftwright"]["owners"]:
            by_owner[owner_id].append(occurrence["id"])

    linked = [
        entry
        for entry in report["declarations"]["entries"]
        if entry["recognition"]["occurrence_ids"]
    ]
    assert linked
    for entry in linked:
        assert entry["recognition"]["occurrence_ids"] == by_owner[entry["owner"]["id"]]
        assert entry["recognition"]["identity_scope"] == "generation-run-local"
    assert "corroborat" in report["declarations"]["reason"]
