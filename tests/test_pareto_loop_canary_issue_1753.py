"""Real AP242 proof that resolved findings leave the Pareto agent loop (#1753)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from draftwright.audit import ExpectedRequirement
from draftwright.replay_assessment import assessment_sidecar_path
from draftwright.sheet_emit import generate_sheet_script

pytestmark = [pytest.mark.slow, pytest.mark.real_part_canary, pytest.mark.timeout(120)]

_FIXTURE = Path(__file__).parent / "fixtures/nist_ctc_01_asme1_ap242.stp"
_FIXTURE_SHA256 = "85a5752da05f53c456ca3a9e038c90358e1d5a3141d1f0d6e5f0970f2356e821"


def _fixed_requirements() -> tuple[ExpectedRequirement, ...]:
    """Reviewed CTC-01/AP242 claims; never derive the denominator from output."""

    rows: list[tuple[str, str]] = []
    # The physical four-hole lattices are Ø25 at X±160/Y±45 and Ø35 at
    # X±325/Y±175. Quiddity #791 gives each proved rectangle one grid owner.
    for declaration in (1, 2):
        rows.append((f"declaration:{declaration}", "bore.diameter"))
        rows.extend(
            (f"declaration:{declaration}", parameter)
            for parameter in (
                "grid_pitch.length.row",
                "grid_pitch.length.col",
                "location_pattern.location.centre.x",
                "location_pattern.location.centre.y",
            )
        )
    for declaration in (3, 4):
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
        ("declaration:5", parameter)
        for parameter in (
            "location_slot.length",
            "slot_end_radius.radius",
            "slot_length.length",
            "slot_width.length",
        )
    )
    rows.extend(
        ("declaration:6", parameter)
        for parameter in ("location_slot.length", "slot_length.length", "slot_width.length")
    )
    rows.extend(
        ("declaration:7", parameter)
        for parameter in ("boss_height.length", "polygon_across_flats.length")
    )
    rows.extend(
        ("declaration:8", parameter)
        for parameter in ("depth.length", "height.length", "width.length")
    )
    rows.append(("declaration:9", "step_height.length"))
    rows.extend((f"declaration:{declaration}", "chamfer.length") for declaration in range(10, 13))
    rows.extend((f"declaration:{declaration}", "fillet.radius") for declaration in range(13, 20))
    rows.extend((f"declaration:{declaration}", "blend.radius") for declaration in range(20, 48))
    assert len(rows) == 69
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


def _overlaps(document: dict[str, Any]) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    return [
        (tuple(row["annotation_names"]), tuple(row["declaration_ids"]))
        for row in document["drawing"]["layout"]["findings"]
        if row["code"] == "annotation_ink_overlap"
    ]


def test_ctc01_resolved_gdt_finding_is_not_offered_to_the_pareto_loop(
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
    source = baseline_script.read_text(encoding="utf-8")
    marker = "drawing = sheet.build()"
    assert source.count(marker) == 1
    baseline_script.write_text(
        source.replace(
            marker,
            marker
            + "\n# #1756: co-sited datum B / perpendicularity leaders stay local after final solve."
            + "\nfor _name in ('m_gdt4', 'm_gdt9'):"
            + "\n    _leader = drawing.get_annotation(_name)"
            + "\n    _points = (_leader.tip, *getattr(_leader, 'bends', ()), _leader.elbow)"
            + "\n    _length = sum(((b[0]-a[0])**2 + (b[1]-a[1])**2)**0.5 "
            + "for a, b in zip(_points, _points[1:]))"
            + "\n    assert _length < 45.0, (_name, _length, _points)"
            + "\n# #1797: a required polygonal-boss callout recovers into clear sheet space."
            + "\nassert 'm_polygonal_boss_z0' in drawing.annotations()",
        ),
        encoding="utf-8",
    )
    baseline = _run(baseline_script, tmp_path / "baseline-trace")
    assert baseline["producer"]["quiddity"] == "0.3.9"
    assert baseline["source"]["sha256"] == _FIXTURE_SHA256

    # #1756 resolves this exact finding during ordinary placement. A Pareto loop must
    # therefore not offer a stale semantic edit for it. The two position frames
    # (AP242 geometric_tolerance:0:1:4:1 and :5) moved from declarations 57/58
    # to 58/59 when member holes became the two grid declarations.
    assert not [
        row
        for row in baseline["drawing"]["layout"]["findings"]
        if set(row["declaration_ids"]) == {"declaration:58", "declaration:59"}
    ]
    assert baseline["drawing"]["layout"]["edit_surface"] == "semantic-dsl-only"
    assert len(list((tmp_path / "baseline-trace").glob("*.trace.json"))) == 1

    assert _overlaps(baseline) == []

    expected = {(row.declaration_id, row.parameter_id) for row in _EXPECTED}
    assert {
        (row["declaration_id"], row["parameter_id"]) for row in baseline["measurements"]["entries"]
    } == expected
    assert len(baseline["measurements"]["entries"]) == 69
