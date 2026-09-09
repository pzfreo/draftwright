"""Shared datum applicability survives authored filtering and retains exact authority."""

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Pos

from draftwright.builder import _detect_part_model_analysis
from draftwright.linting.requirements import recognized_requirement_outcomes
from draftwright.model.compiled import compile_dimensions
from draftwright.registry import AnnotationRegistry
from draftwright.reporting import (
    ReportUnavailableError,
    build_requirement_catalog,
    match_requirement_catalog,
)


@pytest.fixture(scope="module", params=("pad", "pocket"))
def common(request):
    source = (
        Path(__file__).parent / "fixtures/evaluation/pad-x-positive.step"
        if request.param == "pad"
        else Box(60, 40, 20) - Pos(0, 0, 8) * Box(10, 12, 6)
    )
    model, analysis = _detect_part_model_analysis(source)
    feature = next(feature for feature in model.features if feature.kind == request.param)
    datum = next(datum for datum in model.datums if datum.id == "datum_xy")
    at = list(datum.at)
    index = "xyz".index(feature.long_axis)
    at[index] = feature.frame.origin[index] if request.param == "pad" else feature.lo
    datum = replace(datum, at=tuple(at))
    model = replace(model, datums=[datum])
    return request.param, model, analysis, feature, datum


def project(model, analysis, *, outcomes=None):
    return build_requirement_catalog(
        evidence=analysis.recognition_evidence,
        ownership=analysis.recognition_ownership,
        model=model,
        part=analysis.part,
        requirement_outcomes=outcomes,
    )


def live_outcomes(model, analysis, *, extra_omissions=()):
    plan = compile_dimensions(model)
    return recognized_requirement_outcomes(
        analysis.recognition,
        model.features,
        AnnotationRegistry(),
        (*plan.diagnostics, *extra_omissions),
        part=analysis.part,
        evidence=analysis.recognition_evidence,
        ownership=analysis.recognition_ownership,
        dimension_plan=plan,
        datum=next((datum for datum in model.datums if datum.id == "datum_xy"), None),
    )


def test_datum_proof_survives_authored_suppression(common):
    kind, model, analysis, feature, datum = common
    catalog = project(model, analysis)
    proof_rows = [
        row
        for row in catalog.requirements
        if row.intrinsic_exclusion is not None
        and row.intrinsic_exclusion.reason_code == "location_coincident_with_common_datum"
    ]
    assert len(proof_rows) == 1
    expected = proof_rows[0]
    proof = expected.intrinsic_exclusion
    assert proof.datum is datum
    assert proof.source_records[0] is expected.source_records[0]
    assert proof.span[0] == datum.at
    if kind == "pocket":
        assert not feature.edge_anchored
        assert proof.span[1]["xyz".index(feature.long_axis)] == feature.lo
    states = []
    for current in (model, replace(model, authored_dimensions=())):
        aligned = match_requirement_catalog(
            catalog, project(current, analysis, outcomes=live_outcomes(current, analysis))
        )
        row = next(
            row
            for row in aligned
            if row.parameter_id == expected.parameter_id and row.family == expected.family
        )
        assert row.intrinsic_exclusion.datum is datum
        assert row.intrinsic_exclusion.span == proof.span
        states.append(row.outcome.state)
    assert states == ["inapplicable", "suppressed"]


@pytest.mark.parametrize("missing", (False, True))
def test_changed_or_missing_common_datum_cannot_keep_exclusion(common, missing):
    _, model, analysis, _, datum = common
    baseline = project(model, analysis)
    moved = replace(datum, at=tuple(value + 3 for value in datum.at))
    changed = replace(model, datums=[] if missing else [moved])
    current = project(changed, analysis)
    assert not any(
        row.intrinsic_exclusion is not None
        and row.intrinsic_exclusion.reason_code == "location_coincident_with_common_datum"
        for row in current.requirements
    )
    with pytest.raises(ReportUnavailableError, match="requirement shape"):
        match_requirement_catalog(baseline, current)


def test_synthetic_local_omission_cannot_manufacture_datum_proof(common):
    _, model, analysis, _, _ = common
    original = live_outcomes(model, analysis)
    target = next(
        row
        for rows in original.values()
        for row in rows
        if row.intrinsic_exclusion is not None
        and row.intrinsic_exclusion.reason_code == "location_coincident_with_common_datum"
    )
    missing = replace(model, datums=[])
    fabricated = {
        family: tuple(
            replace(row, state="inapplicable")
            if row.parameter_id == target.parameter_id and row.features == target.features
            else row
            for row in rows
        )
        for family, rows in live_outcomes(missing, analysis).items()
    }
    current = project(missing, analysis, outcomes=fabricated)
    assert not any(row.intrinsic_exclusion is not None for row in current.requirements)
