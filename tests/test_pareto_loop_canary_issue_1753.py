"""Real AP242 generate-edit-replay proof for the Pareto-first agent loop (#1753)."""

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
from draftwright.sheet_emit import generate_sheet_script

pytestmark = [pytest.mark.slow, pytest.mark.real_part_canary, pytest.mark.timeout(120)]

_FIXTURE = Path(__file__).parent / "fixtures/nist_ctc_01_asme1_ap242.stp"
_FIXTURE_SHA256 = "85a5752da05f53c456ca3a9e038c90358e1d5a3141d1f0d6e5f0970f2356e821"
_TARGET = LayoutFindingIdentity(
    "annotation_ink_overlap",
    ("declaration:57", "declaration:58"),
    ("m_gdt0", "m_gdt1"),
)


def _fixed_requirements() -> tuple[ExpectedRequirement, ...]:
    """Reviewed Quiddity-0.3.3/AP242 claims; never derive the denominator from output."""

    rows: list[tuple[str, str]] = []
    rows.append(("declaration:1", "bore.diameter"))
    rows.extend(
        ("declaration:1", f"location.location.member.{member}.{axis}")
        for member in range(3)
        for axis in ("x", "y")
    )
    for declaration in range(2, 7):
        rows.append((f"declaration:{declaration}", "bore.diameter"))
        rows.extend(
            (f"declaration:{declaration}", f"location.location.member.0.{axis}")
            for axis in ("x", "y")
        )
    for declaration in (7, 8):
        rows.extend(
            (f"declaration:{declaration}", parameter)
            for parameter in (
                "bore.depth",
                "bore.diameter",
                "location_off_axis.location.member.0.x",
                "location_off_axis.location.member.0.z",
            )
        )
    rows.extend(
        ("declaration:9", parameter)
        for parameter in (
            "location_slot.length",
            "slot_end_radius.radius",
            "slot_length.length",
            "slot_width.length",
        )
    )
    rows.extend(
        ("declaration:10", parameter)
        for parameter in ("location_slot.length", "slot_length.length", "slot_width.length")
    )
    rows.append(("declaration:11", "boss_height.length"))
    rows.extend(
        ("declaration:12", parameter)
        for parameter in ("depth.length", "height.length", "width.length")
    )
    rows.append(("declaration:13", "step_height.length"))
    rows.extend((f"declaration:{declaration}", "chamfer.length") for declaration in range(14, 17))
    rows.extend((f"declaration:{declaration}", "fillet.radius") for declaration in range(17, 24))
    rows.extend((f"declaration:{declaration}", "blend.radius") for declaration in range(24, 52))
    assert len(rows) == 80
    return tuple(ExpectedRequirement(*row) for row in rows)


_EXPECTED = _fixed_requirements()


def _run(script: Path, trace: Path) -> dict[str, Any]:
    trace.mkdir()
    environment = os.environ.copy()
    environment["DRAFTWRIGHT_TRACE"] = str(trace)
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=script.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=100,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return cast(
        dict[str, Any],
        json.loads(Path(assessment_sidecar_path(script)).read_text(encoding="utf-8")),
    )


def _script_variant(source: str, old_prefix: Path, new_prefix: Path, lines: str) -> Path:
    marker = "# ── Views"
    assert source.count(marker) == 1
    rewritten = source.replace(str(old_prefix), str(new_prefix))
    script = new_prefix.with_suffix(".py")
    script.write_text(rewritten.replace(marker, f"{lines}\n\n{marker}"), encoding="utf-8")
    return script


def _declaration(document: dict[str, Any], declaration_id: str) -> dict[str, Any]:
    return next(
        row
        for row in document["drawing"]["declarations"]["entries"]
        if row["id"] == declaration_id
    )


def _overlaps(document: dict[str, Any]) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    return [
        (tuple(row["annotation_names"]), tuple(row["declaration_ids"]))
        for row in document["drawing"]["layout"]["findings"]
        if row["code"] == "annotation_ink_overlap"
    ]


def test_ctc01_side_only_edit_is_pareto_dominant_without_certifying_the_drawing(
    tmp_path,
) -> None:
    assert hashlib.sha256(_FIXTURE.read_bytes()).hexdigest() == _FIXTURE_SHA256
    baseline_prefix = tmp_path / "baseline"
    baseline_script = Path(
        generate_sheet_script(
            str(_FIXTURE),
            out=str(baseline_prefix),
            title="CTC-01 PARETO LOOP CANARY",
            page="A2",
            scale=0.2,
            scale_policy="permissive",
            pmi="annotate",
            formats=("svg",),
        )
    )
    baseline = _run(baseline_script, tmp_path / "baseline-trace")
    assert baseline["producer"]["quiddity"] == "0.3.3"
    assert baseline["source"]["sha256"] == _FIXTURE_SHA256

    # The agent reads typed finding identities and the bounded remedy vocabulary. It does not
    # parse prose, optimize a score, or infer a coordinate from the rendered sheet.
    target = next(
        row
        for row in baseline["drawing"]["layout"]["findings"]
        if set(row["declaration_ids"]) == {"declaration:57", "declaration:58"}
    )
    assert target["code"] == "annotation_ink_overlap"
    assert "side" in target["remedies"]
    assert baseline["drawing"]["layout"]["edit_surface"] == "semantic-dsl-only"

    candidate_prefix = tmp_path / "candidate"
    candidate_script = _script_variant(
        baseline_script.read_text(encoding="utf-8"),
        baseline_prefix,
        candidate_prefix,
        'sheet.layout_override("declaration:57", side="above")\n'
        'sheet.layout_override("declaration:63", side="below")',
    )
    candidate = _run(candidate_script, tmp_path / "candidate-trace")
    assert candidate["source"] == baseline["source"]
    assert candidate["producer"] == baseline["producer"]
    assert candidate["run"] == baseline["run"]
    assert len(list((tmp_path / "baseline-trace").glob("*.trace.json"))) == 1
    assert len(list((tmp_path / "candidate-trace").glob("*.trace.json"))) == 1

    assert _overlaps(baseline) == [
        (("hc_plan1", "m_slot0_width"), ("declaration:1", "declaration:9")),
        (("hc_plan2", "m_slot0_width"), ("declaration:2", "declaration:9")),
        (("m_gdt1", "m_gdt0"), ("declaration:57", "declaration:58")),
    ]
    assert _overlaps(candidate) == [
        (("hc_plan1", "m_slot0_width"), ("declaration:1", "declaration:9")),
        (("hc_plan2", "m_slot0_width"), ("declaration:2", "declaration:9")),
    ]
    assert candidate["drawing"]["layout"]["overrides"] == [
        {
            "declaration_id": "declaration:57",
            "control": "side",
            "authored_value": "above",
            "resolved_value": "above",
            "intent_class": "layout-only",
            "status": "applied",
        },
        {
            "declaration_id": "declaration:63",
            "control": "side",
            "authored_value": "below",
            "resolved_value": "below",
            "intent_class": "layout-only",
            "status": "applied",
        },
    ]

    expected = {(row.declaration_id, row.parameter_id) for row in _EXPECTED}
    for document in (baseline, candidate):
        assert {
            (row["declaration_id"], row["parameter_id"])
            for row in document["measurements"]["entries"]
        } == expected
        assert len(document["measurements"]["entries"]) == 80
        assert document["measurements"]["unknown"] == [
            *(
                {"annotation": f"m_gdt{index}", "reason": "measurement_identity_unavailable"}
                for index in (0, 1, 10, 2, 3, 4, 5, 6, 7, 8, 9)
            ),
            {"annotation": "pmi_angle_0", "reason": "measurement_identity_unavailable"},
        ]

    comparison = compare_assessments(
        baseline,
        candidate,
        expected_requirements=_EXPECTED,
        selected_layout_finding=_TARGET,
    )
    assert comparison["pareto"]["relation"] == "dominates"
    assert comparison["pareto"]["improved_axes"] == ["legibility"]
    assert comparison["pareto"]["regressed_axes"] == []
    assert comparison["axes"]["requirements"]["relation"] == "unchanged"
    assert comparison["axes"]["completeness"]["relation"] == "unchanged"
    assert comparison["axes"]["fidelity"]["relation"] == "unchanged"
    assert comparison["layout"]["selected_transition"] == "resolved"
    assert comparison["layout"]["introduced"] == []
    assert len(comparison["uncertainty"]["carried"]) == 12
    assert comparison["uncertainty"]["introduced"] == []
    assert comparison["uncertainty"]["changed"] == []
    assert all(not row["changes"] for row in comparison["requirements"]["transitions"])
    assert comparison["restraint"]["availability"] == "unavailable"
    assert comparison["manufacturing_readiness"]["availability"] == "unavailable"
    assert comparison["decision"] == "no-preference"  # deprecated v1 projection
    assert not comparison["policy"]["blockers"]

    # Output bytes are deliberately outside the semantic comparison contract.
    changed_output = copy.deepcopy(candidate)
    changed_output["outputs"][0]["sha256"] = "0" * 64
    assert (
        compare_assessments(
            baseline,
            changed_output,
            expected_requirements=_EXPECTED,
            selected_layout_finding=_TARGET,
        )
        == comparison
    )

    # Removing the crossed unresolved frame cannot masquerade as resolved uncertainty.
    deleted = copy.deepcopy(candidate)
    deleted["measurements"]["unknown"] = [
        row for row in deleted["measurements"]["unknown"] if row["annotation"] != "m_gdt0"
    ]
    declaration = _declaration(deleted, "declaration:57")
    declaration["representations"] = [
        row for row in declaration["representations"] if row["name"] != "m_gdt0"
    ]
    deletion = compare_assessments(
        baseline, deleted, expected_requirements=_EXPECTED, selected_layout_finding=_TARGET
    )
    assert deletion["decision"] == "rejected"
    assert deletion["pareto"]["relation"] == "unavailable"
    assert {row["code"] for row in deletion["policy"]["blockers"]} == {
        "measurement_uncertainty_carrier_removed"
    }

    # An equal-valued diameter moved to another physical occurrence is still substitution.
    substituted = copy.deepcopy(candidate)
    _declaration(substituted, "declaration:3")["recognition"]["occurrence_ids"] = ["holes:3"]
    substitution = compare_assessments(
        baseline, substituted, expected_requirements=_EXPECTED, selected_layout_finding=_TARGET
    )
    assert substitution["decision"] == "rejected"
    assert any(
        "physical_owner" in row["changes"] for row in substitution["requirements"]["blockers"]
    )
