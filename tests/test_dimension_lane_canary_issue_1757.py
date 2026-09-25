"""Real AP242 proof for declaration-scoped dimension lanes (#1757)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from draftwright.audit import ExpectedRequirement, compare_assessments
from draftwright.replay_assessment import assessment_sidecar_path
from draftwright.sheet_emit import generate_sheet_script

pytestmark = [pytest.mark.slow, pytest.mark.real_part_canary, pytest.mark.timeout(120)]

_FIXTURE = Path(__file__).parent / "fixtures/nist_ctc_01_asme1_ap242.stp"
_FIXTURE_SHA256 = "85a5752da05f53c456ca3a9e038c90358e1d5a3141d1f0d6e5f0970f2356e821"


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


def _annotation(document: dict[str, Any], name: str) -> dict[str, Any]:
    return next(row for row in document["drawing"]["layout"]["annotations"] if row["name"] == name)


def test_ctc01_feature_relative_lanes_preserve_clear_slot_widths(tmp_path) -> None:
    assert hashlib.sha256(_FIXTURE.read_bytes()).hexdigest() == _FIXTURE_SHA256
    generated_prefix = tmp_path / "generated"
    generated_script = Path(
        generate_sheet_script(
            str(_FIXTURE),
            out=str(generated_prefix),
            title="CTC-01 DIMENSION LANE CANARY",
            page="A2",
            scale=0.2,
            scale_policy="permissive",
            pmi="annotate",
            formats=("svg",),
        )
    )
    generated_source = generated_script.read_text(encoding="utf-8")
    side_prefix = tmp_path / "side"
    side_script = _script_variant(
        generated_source,
        generated_prefix,
        side_prefix,
        'sheet.layout_override("declaration:57", side="above")\n'
        'sheet.layout_override("declaration:63", side="below")',
    )
    baseline = _run(side_script, tmp_path / "side-trace")

    lane_prefix = tmp_path / "lane"
    lane_script = _script_variant(
        side_script.read_text(encoding="utf-8"),
        side_prefix,
        lane_prefix,
        'sheet.layout_override("declaration:9", parameter="slot_width.length", lane=3)\n'
        'sheet.layout_override("declaration:10", parameter="slot_width.length", lane=4)',
    )
    candidate = _run(lane_script, tmp_path / "lane-trace")

    baseline_overlaps = [
        (tuple(row["annotation_names"]), tuple(row["declaration_ids"]))
        for row in baseline["drawing"]["layout"]["findings"]
        if row["code"] == "annotation_ink_overlap"
    ]
    # The shared corridor planner now clears these crossings before an authored lane is
    # needed. Explicit lanes must preserve that result and the same semantic ownership.
    assert baseline_overlaps == []
    assert not [
        row
        for row in candidate["drawing"]["layout"]["findings"]
        if row["code"] == "annotation_ink_overlap"
    ]
    assert not [
        row for row in candidate["drawing"]["lint"]["issues"] if row["code"] == "slot_dim_dropped"
    ]

    for name in ("m_slot0_width", "m_slot1_width"):
        assert _annotation(baseline, name)["semantic"] == _annotation(candidate, name)["semantic"]
    assert candidate["drawing"]["layout"]["overrides"][-2:] == [
        {
            "declaration_id": "declaration:9",
            "parameter_id": "slot_width.length",
            "control": "lane",
            "authored_value": 3,
            "resolved_value": 3,
            "intent_class": "layout-only",
            "status": "applied",
        },
        {
            "declaration_id": "declaration:10",
            "parameter_id": "slot_width.length",
            "control": "lane",
            "authored_value": 4,
            "resolved_value": 4,
            "intent_class": "layout-only",
            "status": "applied",
        },
    ]

    expected = tuple(
        ExpectedRequirement(row["declaration_id"], row["parameter_id"])
        for row in baseline["measurements"]["entries"]
    )
    comparison = compare_assessments(
        baseline,
        candidate,
        expected_requirements=expected,
    )
    assert comparison["pareto"]["relation"] == "equivalent"
    assert comparison["pareto"]["improved_axes"] == []
    assert comparison["pareto"]["regressed_axes"] == []
    assert comparison["axes"]["requirements"]["relation"] == "unchanged"
    assert comparison["axes"]["completeness"]["relation"] == "unchanged"
    assert comparison["axes"]["fidelity"]["relation"] == "unchanged"
    assert all(not row["changes"] for row in comparison["requirements"]["transitions"])
    assert baseline["measurements"]["unknown"] == candidate["measurements"]["unknown"]
