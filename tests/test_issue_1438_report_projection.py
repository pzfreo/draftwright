"""#1438 — the run-local ownership ledger has a versioned JSON projection."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Compound, Cylinder, Pos, RegularPolygon, Rot, extrude
from jsonschema import ValidationError
from jsonschema.validators import validator_for

from draftwright import ReportUnavailableError, build_drawing
from draftwright import analysis as analysis_module
from draftwright import reporting as reporting_module

_SCHEMA_PATH = Path(__file__).parents[1] / "docs/reference/draftwright-report-v1.schema.json"


def _schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _through_step_part():
    return Box(40, 30, 20) - Pos(15, 10, 0) * Box(20, 20, 30)


def _oriented_slot_part():
    centre = (Align.CENTER, Align.CENTER, Align.CENTER)
    part = Box(120, 90, 10)
    for x in (-30, 0, 30):
        part -= Pos(x, 0, 0) * Rot(0, 0, 30) * Box(24, 6, 20, align=centre)
    return part


def _grouped_holes_part():
    aligned = (Align.CENTER, Align.CENTER, Align.MIN)
    part = Box(80, 60, 10, align=aligned)
    for x, y in ((-20, -10), (15, 12)):
        part -= Pos(x, y, 0) * Cylinder(2, 10, align=aligned)
    return part


def _passage_part():
    return Box(40, 40, 10) - extrude(RegularPolygon(6, 6), amount=12, both=True)


def _step_projection_evidence_part():
    aligned = (Align.MIN, Align.MIN, Align.MIN)
    return Box(60, 40, 20, align=aligned) - Pos(30, 0, 10) * Box(30, 40, 10, align=aligned)


def _coincident_body_local_evidence_part():
    def stepped_block():
        return Box(40, 30, 10) + Pos(0, 0, 10) * Box(20, 30, 10)

    return Compound(children=[stepped_block(), stepped_block()])


def _unclassified_plate_part():
    return Box(60, 40, 4) + Pos(0, -18, 12) * Box(60, 4, 20)


def test_raw_report_has_the_closed_v1_shape_and_exact_owner() -> None:
    drawing = build_drawing(_through_step_part())

    report = drawing.report()

    assert set(report) == {
        "schema",
        "schema_version",
        "status",
        "producer",
        "source",
        "outputs",
        "recognition",
        "lint",
    }
    assert report["schema"] == "draftwright-report"
    assert report["schema_version"] == 1
    assert report["status"] == "bounded-clear"
    assert set(report["producer"]) == {"draftwright", "b123d-recognisers"}
    assert report["source"] == {"kind": "build123d", "name": None}
    assert report["outputs"] == {}

    recognition = report["recognition"]
    assert recognition["coverage"] == "accepted-occurrences"
    assert recognition["identity_scope"] == "report-local"
    (occurrence,) = recognition["occurrences"]
    assert occurrence == {
        "id": "through_steps:1",
        "family": "through_steps",
        "record_type": "ThroughStep",
        "record_schema_version": 1,
        "record": {
            "at": [12.5, 7.5, 0.0],
            "axis": "z",
            "length": 20.0,
            "section": [[5.0, 15.0], [5.0, 0.0], [20.0, 0.0]],
        },
        "disposition": "represented",
        "reason_code": "through_step_adapter",
        "tracking": None,
        "owners": [{"id": "through_step:1", "kind": "through_step"}],
        "requirements": {"coverage": "not-projected", "outcomes": []},
    }
    assert recognition["summary"] == {
        "total": 1,
        "represented": 1,
        "absorbed": 0,
        "unsupported": 0,
        "deferred": 0,
        "evidence_only": 0,
        "unexpectedly_missing": 0,
    }
    assert isinstance(report["lint"], dict)
    json.dumps(report, allow_nan=False)
    schema = _schema()
    validator_for(schema).check_schema(schema)
    validator_for(schema)(schema).validate(report)


def test_separate_deferred_occurrences_keep_distinct_report_local_ids() -> None:
    report = build_drawing(_oriented_slot_part()).report()
    occurrences = [
        occurrence
        for occurrence in report["recognition"]["occurrences"]
        if occurrence["family"] == "oriented_slots"
    ]

    assert [occurrence["id"] for occurrence in occurrences] == [
        "oriented_slots:1",
        "oriented_slots:2",
        "oriented_slots:3",
    ]
    assert all(occurrence["disposition"] == "deferred" for occurrence in occurrences)
    assert all(occurrence["owners"] == [] for occurrence in occurrences)
    assert all(occurrence["tracking"].endswith("/1430") for occurrence in occurrences)
    assert report["recognition"]["summary"]["deferred"] == 3
    assert report["status"] == "needs-attention"


@pytest.mark.parametrize(
    (
        "part_factory",
        "family",
        "disposition",
        "reason_code",
        "tracking_suffix",
        "has_owner",
    ),
    (
        (_grouped_holes_part, "holes", "absorbed", "grouped_hole_member", None, True),
        (
            _passage_part,
            "passages",
            "unsupported",
            "consumer_semantics_unsupported",
            "/1245",
            False,
        ),
        (
            _step_projection_evidence_part,
            "step_levels",
            "evidence_only",
            "step_level_projection_evidence",
            None,
            False,
        ),
        (
            _step_projection_evidence_part,
            "risers",
            "evidence_only",
            "riser_projection_evidence",
            None,
            False,
        ),
    ),
    ids=("absorbed", "unsupported", "face-level-evidence", "riser-evidence"),
)
def test_report_projects_each_settled_consumer_outcome(
    part_factory,
    family: str,
    disposition: str,
    reason_code: str,
    tracking_suffix: str | None,
    has_owner: bool,
) -> None:
    report = build_drawing(part_factory()).report()
    occurrences = [
        occurrence
        for occurrence in report["recognition"]["occurrences"]
        if occurrence["family"] == family
    ]

    assert occurrences
    assert all(occurrence["disposition"] == disposition for occurrence in occurrences)
    assert all(occurrence["reason_code"] == reason_code for occurrence in occurrences)
    if tracking_suffix is None:
        assert all(occurrence["tracking"] is None for occurrence in occurrences)
    else:
        assert all(occurrence["tracking"].endswith(tracking_suffix) for occurrence in occurrences)
    assert all(bool(occurrence["owners"]) is has_owner for occurrence in occurrences)
    if has_owner:
        assert len({occurrence["owners"][0]["id"] for occurrence in occurrences}) == 1
    assert report["recognition"]["summary"][disposition] >= len(occurrences)
    expected_status = "bounded-clear" if disposition == "absorbed" else "needs-attention"
    assert report["status"] == expected_status
    validator_for(_schema())(_schema()).validate(report)


def test_equal_public_records_on_distinct_bodies_keep_distinct_report_local_ids() -> None:
    drawing = build_drawing(_coincident_body_local_evidence_part())
    report = drawing.report()
    evidence = drawing.recognition_evidence()
    assert evidence is not None
    all_occurrences = report["recognition"]["occurrences"]
    occurrences = [
        occurrence for occurrence in all_occurrences if occurrence["family"] == "step_levels"
    ]

    assert len(occurrences) == 2
    assert occurrences[0]["record"] == occurrences[1]["record"]
    assert [occurrence["id"] for occurrence in occurrences] == [
        "step_levels:1",
        "step_levels:2",
    ]
    assert all(occurrence["disposition"] == "evidence_only" for occurrence in occurrences)
    assert len(all_occurrences) == len(evidence.features)
    assert len({occurrence["id"] for occurrence in all_occurrences}) == len(all_occurrences)
    summary = report["recognition"]["summary"]
    assert (
        sum(summary[disposition] for disposition in reporting_module._DISPOSITIONS)
        == summary["total"]
    )


def test_supported_owner_loss_is_machine_visible_and_cannot_look_clear() -> None:
    drawing = build_drawing(_through_step_part())
    evidence = drawing.recognition_evidence()
    ownership = drawing.recognition_ownership()
    model = drawing.model()
    assert evidence is not None and ownership is not None and model is not None

    report = reporting_module.drawing_report(
        evidence=evidence,
        ownership=replace(ownership, bindings=()),
        model=model,
        lint=drawing.lint_summary(),
        source=None,
    )
    (occurrence,) = report["recognition"]["occurrences"]

    assert occurrence["disposition"] == "unexpectedly_missing"
    assert occurrence["reason_code"] == "supported_owner_missing"
    assert occurrence["owners"] == []
    assert report["recognition"]["summary"]["unexpectedly_missing"] == 1
    assert report["status"] == "needs-attention"


def test_a_recorded_owner_removed_from_the_final_model_cannot_retain_credit() -> None:
    drawing = build_drawing(_through_step_part())
    evidence = drawing.recognition_evidence()
    ownership = drawing.recognition_ownership()
    model = drawing.model()
    assert evidence is not None and ownership is not None and model is not None

    report = reporting_module.drawing_report(
        evidence=evidence,
        ownership=ownership,
        model=replace(model, features=()),
        lint=drawing.lint_summary(),
        source=None,
    )
    occurrence = next(
        occurrence
        for occurrence in report["recognition"]["occurrences"]
        if occurrence["family"] == "through_steps"
    )

    assert occurrence["disposition"] == "unexpectedly_missing"
    assert occurrence["reason_code"] == "recorded_owner_not_in_model"
    assert occurrence["owners"] == []
    assert report["recognition"]["summary"]["unexpectedly_missing"] >= 1
    assert report["status"] == "needs-attention"


def test_repeated_report_reuses_one_recognition_run(monkeypatch) -> None:
    calls = 0
    original = analysis_module.build_recognition_evidence

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis_module, "build_recognition_evidence", counted)
    drawing = build_drawing(_through_step_part())

    first = drawing.report()
    second = drawing.report()

    assert first == second
    assert calls == 1


def test_reproducible_builds_emit_the_same_report_document() -> None:
    first = build_drawing(_through_step_part(), reproducible=True).report()
    second = build_drawing(_through_step_part(), reproducible=True).report()

    assert first == second


@pytest.mark.parametrize("value", (math.nan, math.inf, -math.inf))
def test_report_json_boundary_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        reporting_module._json_value({"value": value})


def test_report_json_boundary_never_falls_back_to_python_repr() -> None:
    with pytest.raises(TypeError, match="not JSON serializable"):
        reporting_module._json_value({"value": object()})


def test_report_refuses_an_unknown_consumer_record_schema() -> None:
    with pytest.raises(ReportUnavailableError, match="no unique supported schema version"):
        reporting_module._record_schema_version("unknown_family", object())


def test_report_projects_only_the_step_basename_as_source() -> None:
    drawing = build_drawing(_through_step_part())

    report = reporting_module.drawing_report(
        evidence=drawing.recognition_evidence(),
        ownership=drawing.recognition_ownership(),
        model=drawing.model(),
        lint=drawing.lint_summary(),
        source=Path("private/input/bracket.step"),
    )

    assert report["source"] == {"kind": "step", "name": "bracket.step"}


def test_report_refuses_malformed_or_repeated_final_ir_features() -> None:
    feature = SimpleNamespace(kind="hole")

    with pytest.raises(ReportUnavailableError, match="without a kind"):
        reporting_module._feature_ids(SimpleNamespace(features=(SimpleNamespace(kind=None),)))
    with pytest.raises(ReportUnavailableError, match="repeats the same IR feature"):
        reporting_module._feature_ids(SimpleNamespace(features=(feature, feature)))


def test_report_refuses_a_missing_final_model() -> None:
    drawing = build_drawing(_through_step_part())

    with pytest.raises(ReportUnavailableError, match="no final IR model"):
        reporting_module.validate_report_inputs(
            drawing.recognition_evidence(),
            drawing.recognition_ownership(),
            None,
        )


def test_report_refuses_an_unknown_ledger_status() -> None:
    drawing = build_drawing(_through_step_part())
    ownership = drawing.recognition_ownership()
    assert ownership is not None
    (binding,) = ownership.bindings
    malformed = replace(
        ownership,
        bindings=(replace(binding, disposition="invalid"),),  # type: ignore[arg-type]
    )

    with pytest.raises(ReportUnavailableError, match="unsupported status 'invalid'"):
        reporting_module.drawing_report(
            evidence=drawing.recognition_evidence(),
            ownership=malformed,
            model=drawing.model(),
            lint=drawing.lint_summary(),
            source=None,
        )


def test_raw_report_refuses_an_unclassified_accepted_occurrence() -> None:
    drawing = build_drawing(_unclassified_plate_part())
    evidence = drawing.recognition_evidence()
    assert evidence is not None
    assert any(evidence.family(occurrence) == "plates" for occurrence in evidence.features)

    with pytest.raises(
        ReportUnavailableError,
        match="accepted occurrence family 'plates' has no reportable disposition",
    ):
        drawing.report()


@pytest.mark.parametrize("boundary", ("declared", "framed"))
def test_report_refuses_to_invent_ownership_across_an_unavailable_boundary(boundary) -> None:
    drawing = build_drawing(
        _through_step_part(),
        model=[] if boundary == "declared" else None,
        framed_recognition=boundary == "framed",
    )
    with pytest.raises(ReportUnavailableError, match="occurrence ownership is unavailable"):
        drawing.report()
    if boundary == "declared":
        assert drawing.recognition_evidence() is None


def test_report_projection_does_not_change_visual_output(tmp_path) -> None:
    drawing = build_drawing(_through_step_part(), reproducible=True)
    before = drawing.export(str(tmp_path / "before"), formats=("svg",))["svg"]

    drawing.report()
    after = drawing.export(str(tmp_path / "after"), formats=("svg",))["svg"]

    assert Path(before).read_bytes() == Path(after).read_bytes()


def test_documented_schema_has_the_same_closed_top_level() -> None:
    schema = _schema()

    assert schema["$id"].endswith("draftwright-report-v1.schema.json")
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "schema",
        "schema_version",
        "status",
        "producer",
        "source",
        "outputs",
        "recognition",
        "lint",
    }

    unknown = build_drawing(_through_step_part()).report()
    unknown["unknown"] = True
    with pytest.raises(ValidationError):
        validator_for(schema)(schema).validate(unknown)
