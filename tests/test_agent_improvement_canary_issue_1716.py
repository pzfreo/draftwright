"""One real-part proof that assessed DSL edits cannot claim unsupported improvement.

The NIST CTC-01 STEP is recognised once while generating the editable script, then that
script is built twice: as generated and after one sanctioned ``Sheet`` layout edit.  The
automatic planner now resolves the historical overlap itself, so the larger-sheet edit
must remain a no-op rather than receiving stale improvement credit. All negative policy
checks below mutate the two resulting JSON documents in memory; they do not pay for
additional CAD builds.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from draftwright.audit import (
    ExpectedRequirement,
    LayoutFindingIdentity,
    compare_assessments,
)
from draftwright.replay_assessment import assessment_sidecar_path
from draftwright.sheet_emit import generate_sheet_script, inspection_sidecar_path

pytestmark = [pytest.mark.slow, pytest.mark.real_part_canary, pytest.mark.timeout(120)]

_FIXTURE = Path(__file__).parent / "fixtures/nist_ctc_01_asme1_ap203.stp"
_FIXTURE_SHA256 = "e081d518484d5c708c6729353237f94a7d97da2b74a10d60d4c0d146d67a5855"
_TARGET = LayoutFindingIdentity(
    "annotation_ink_overlap",
    ("declaration:2", "declaration:4"),
    ("hc_plan1", "m_slot0_width"),
)


def _fixed_requirements() -> tuple[ExpectedRequirement, ...]:
    """CTC-01's reviewed 79 claims, independent of either observed assessment."""

    rows: list[tuple[str, str]] = []
    for declaration in (1, 2):
        rows.append((f"declaration:{declaration}", "bore.diameter"))
        rows.extend(
            (f"declaration:{declaration}", f"location.location.member.{member}.{axis}")
            for member in range(4)
            for axis in ("x", "y")
        )
    rows.extend(
        ("declaration:3", parameter)
        for parameter in (
            "bore.depth",
            "bore.diameter",
            "location_off_axis.location.member.0.x",
            "location_off_axis.location.member.0.z",
            "location_off_axis.location.member.1.x",
            "location_off_axis.location.member.1.z",
        )
    )
    for declaration in (4, 5):
        rows.extend(
            (f"declaration:{declaration}", parameter)
            for parameter in ("location_slot.length", "slot_length.length", "slot_width.length")
        )
    rows.append(("declaration:4", "slot_end_radius.radius"))
    rows.extend(
        ("declaration:6", parameter)
        for parameter in ("boss_height.length", "polygon_across_flats.length")
    )
    rows.extend(
        ("declaration:7", parameter)
        for parameter in ("depth.length", "height.length", "width.length")
    )
    rows.append(("declaration:8", "step_height.length"))
    rows.extend((f"declaration:{declaration}", "chamfer.length") for declaration in range(9, 12))
    rows.extend((f"declaration:{declaration}", "fillet.radius") for declaration in range(12, 20))
    rows.extend((f"declaration:{declaration}", "blend.radius") for declaration in range(20, 51))
    assert len(rows) == 79
    return tuple(ExpectedRequirement(*row) for row in rows)


_EXPECTED = _fixed_requirements()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(script: Path, trace_dir: Path) -> subprocess.CompletedProcess[str]:
    trace_dir.mkdir()
    environment = os.environ.copy()
    environment["DRAFTWRIGHT_TRACE"] = str(trace_dir)
    return subprocess.run(
        [sys.executable, str(script)],
        cwd=script.parent,
        env=environment,
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )


def _load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _declaration(document: dict[str, Any], declaration_id: str) -> dict[str, Any]:
    return next(
        row
        for row in document["drawing"]["declarations"]["entries"]
        if row["id"] == declaration_id
    )


def test_ctc01_agent_edit_cannot_claim_an_already_resolved_overlap(tmp_path) -> None:
    baseline_prefix = tmp_path / "baseline"
    candidate_prefix = tmp_path / "candidate"
    baseline_script = Path(
        generate_sheet_script(
            str(_FIXTURE),
            out=str(baseline_prefix),
            title="CTC-01 AGENT CANARY",
            formats=("svg",),
        )
    )
    inspection_path = Path(inspection_sidecar_path(str(baseline_script)))
    inspection = _load(inspection_path)

    baseline_run = _run(baseline_script, tmp_path / "baseline-trace")
    assert baseline_run.returncode == 0, baseline_run.stderr
    baseline_path = Path(assessment_sidecar_path(baseline_script))
    baseline = _load(baseline_path)

    # This is the autonomous-loop edit under test: use only a sanctioned Sheet layout
    # declaration, never raw annotation coordinates. The automatic planner has already
    # selected a clean A2, so a larger A1 must not receive stale credit for resolving the
    # old A4 overlap. Path changes merely keep the two replay artifacts separate and are
    # not drawing semantics.
    source = baseline_script.read_text(encoding="utf-8")
    assert source.count(str(baseline_prefix)) == 2
    source = source.replace(str(baseline_prefix), str(candidate_prefix))
    old_sheet_tail = ', pmi=_replay_options["pmi_mode"])'
    new_sheet_tail = (
        ", page='A1', scale=0.2, scale_policy='strict', pmi=_replay_options[\"pmi_mode\"])"
    )
    assert source.count(old_sheet_tail) == 1
    candidate_script = candidate_prefix.with_suffix(".py")
    candidate_script.write_text(source.replace(old_sheet_tail, new_sheet_tail), encoding="utf-8")

    candidate_run = _run(candidate_script, tmp_path / "candidate-trace")
    assert candidate_run.returncode == 0, candidate_run.stderr
    candidate_path = Path(assessment_sidecar_path(candidate_script))
    candidate = _load(candidate_path)

    assert _sha256(_FIXTURE) == _FIXTURE_SHA256
    assert inspection["source"]["sha256"] == _FIXTURE_SHA256
    assert baseline["source"]["sha256"] == candidate["source"]["sha256"] == _FIXTURE_SHA256
    assert baseline["script"] == {"name": "baseline.py", "sha256": _sha256(baseline_script)}
    assert candidate["script"] == {"name": "candidate.py", "sha256": _sha256(candidate_script)}
    assert baseline["script"]["sha256"] != candidate["script"]["sha256"]
    assert baseline["producer"] == candidate["producer"]
    assert baseline["run"] == candidate["run"]
    assert len(list((tmp_path / "baseline-trace").glob("*.trace.json"))) == 1
    assert len(list((tmp_path / "candidate-trace").glob("*.trace.json"))) == 1
    assert baseline["drawing"]["layout"]["placement"]["availability"] == "available"
    assert candidate["drawing"]["layout"]["placement"]["availability"] == "available"
    assert baseline["drawing"]["layout"]["page"]["width"] == 594.0
    assert candidate["drawing"]["layout"]["page"]["width"] == 841.0
    assert baseline["drawing"]["layout"]["page"]["scale"] == 0.2
    assert candidate["drawing"]["layout"]["page"]["scale"] == 0.2

    # The generation-time inspection closes the public join from recognised source faces,
    # through occurrences and final-IR ownership, to the two crossed representations.
    expected_occurrences = {"holes:5", "holes:6", "holes:7", "holes:8"}
    inspected_holes = {
        row["id"]: row for row in inspection["found"] if row["id"] in expected_occurrences
    }
    assert set(inspected_holes) == expected_occurrences
    assert all(row["defining_face_ids"] for row in inspected_holes.values())
    assert all(row["constituent_face_ids"] for row in inspected_holes.values())
    assert all(row["draftwright"]["owners"] == ["hole:2"] for row in inspected_holes.values())
    hole_declaration = _declaration(baseline, "declaration:2")
    assert hole_declaration["owner"]["id"] == "hole:2"
    assert set(hole_declaration["recognition"]["occurrence_ids"]) == expected_occurrences
    inspected_slot = next(row for row in inspection["found"] if row["id"] == "slots:1")
    assert inspected_slot["defining_face_ids"] == ["face:54", "face:55", "face:84", "face:87"]
    assert inspected_slot["draftwright"]["owners"] == ["slot:1"]
    slot_declaration = _declaration(baseline, "declaration:4")
    assert slot_declaration["owner"]["id"] == "slot:1"
    assert slot_declaration["recognition"]["occurrence_ids"] == ["slots:1"]

    comparison = compare_assessments(
        baseline,
        candidate,
        expected_requirements=_EXPECTED,
        selected_layout_finding=_TARGET,
    )
    assert comparison["decision"] == "no-preference", comparison
    assert comparison["reasons"] == ["no_evidence-backed_improvement"]
    assert comparison["layout"]["selected_transition"] == "absent"
    assert not comparison["policy"]["blockers"]
    assert not comparison["unavailable"]["reasons"]
    assert all(not row["changes"] for row in comparison["requirements"]["transitions"])
    assert len(baseline["measurements"]["entries"]) == 79
    assert len(candidate["measurements"]["entries"]) == 79
    assert baseline["measurements"]["unknown"] == candidate["measurements"]["unknown"] == []
    assert baseline["measurements"]["unavailable_owner_claims"] == []
    assert candidate["measurements"]["unavailable_owner_claims"] == []
    assert comparison["restraint"]["availability"] == "unavailable"
    assert comparison["manufacturing_readiness"]["availability"] == "unavailable"
    assert (
        "physical geometry that recognition did not identify"
        in candidate["drawing"]["lint"]["quality"]["completeness"]["excludes"]
    )
    assert inspection["missed"]["face_count"] == {
        "total": 139,
        "claimed": 99,
        "unclaimed": 40,
    }

    # Counterfactual policy checks reuse the real assessment. Clearing the crossing by
    # deleting its slot-width claim or swapping the hole's physical occurrences must lose.
    deleted = copy.deepcopy(candidate)
    deleted["measurements"]["entries"] = [
        row
        for row in deleted["measurements"]["entries"]
        if (row["declaration_id"], row["parameter_id"]) != ("declaration:4", "slot_width.length")
    ]
    deleted_slot_declaration = _declaration(deleted, "declaration:4")
    for representation in deleted_slot_declaration["representations"]:
        representation["measurements"] = [
            parameter
            for parameter in representation["measurements"]
            if parameter != "slot_width.length"
        ]
    deletion_result = compare_assessments(
        baseline,
        deleted,
        expected_requirements=_EXPECTED,
        selected_layout_finding=_TARGET,
    )
    assert deletion_result["decision"] == "rejected"
    assert any(
        row["declaration_id"] == "declaration:4" and row["parameter_id"] == "slot_width.length"
        for row in deletion_result["requirements"]["blockers"]
    )

    substituted = copy.deepcopy(candidate)
    _declaration(substituted, "declaration:2")["recognition"]["occurrence_ids"] = ["holes:1"]
    substitution_result = compare_assessments(
        baseline,
        substituted,
        expected_requirements=_EXPECTED,
        selected_layout_finding=_TARGET,
    )
    assert substitution_result["decision"] == "rejected"
    assert any(
        "physical_owner" in row["changes"]
        for row in substitution_result["requirements"]["blockers"]
    )

    for field, value, reason in (
        ("source", {**candidate["source"], "sha256": "0" * 64}, "source_hash_mismatch"),
        (
            "producer",
            {**candidate["producer"], "quiddity": "different"},
            "producer_versions_mismatch",
        ),
        ("run", {**candidate["run"], "pmi_mode": "report"}, "run_options_mismatch"),
    ):
        mismatched = copy.deepcopy(candidate)
        mismatched[field] = value
        refused = compare_assessments(baseline, mismatched, expected_requirements=_EXPECTED)
        assert refused["decision"] == "incomparable"
        assert reason in refused["reasons"]
