"""The document denominator exists before member placement and keeps source witnesses."""

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos
from quiddity import HoleSpec

from draftwright.builder import _detect_part_model_analysis
from draftwright.reporting import (
    ReportUnavailableError,
    build_requirement_catalog,
    match_requirement_catalog,
)


@pytest.fixture(scope="module", params=("holes", "u_channel", "shoulders"))
def intake(request):
    return detect_intake(request.param)


def detect_intake(kind):
    if kind == "holes":
        part = Box(60, 40, 20) - Pos(-15, 0, 0) * Cylinder(2, 30)
        part -= Pos(15, 0, 8) * Cylinder(2, 4)
    elif kind == "u_channel":
        part = Path(__file__).parent / "fixtures/evaluation/plate-u-additive.step"
    else:
        part = Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)
    model, analysis = _detect_part_model_analysis(part)
    return kind, model, analysis


def catalog_for(model, analysis):
    return build_requirement_catalog(
        evidence=analysis.recognition_evidence,
        ownership=analysis.recognition_ownership,
        model=model,
        part=analysis.part,
    )


def outcomes_for(model, analysis, registry=None):
    from draftwright.linting.requirements import recognized_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    return recognized_requirement_outcomes(
        analysis.recognition_evidence.result,
        model.features,
        registry if registry is not None else AnnotationRegistry(),
        (),
        part=analysis.part,
        evidence=analysis.recognition_evidence,
        ownership=analysis.recognition_ownership,
    )


def project_outcomes(model, analysis, outcomes):
    return build_requirement_catalog(
        evidence=analysis.recognition_evidence,
        ownership=analysis.recognition_ownership,
        model=model,
        part=analysis.part,
        requirement_outcomes=outcomes,
    )


def test_live_catalog_joins_by_source_and_parameter_despite_reordered_rows(intake):
    _, model, analysis = intake
    baseline = catalog_for(model, analysis)
    outcomes = outcomes_for(model, analysis)
    reordered = {family: tuple(reversed(rows)) for family, rows in reversed(outcomes.items())}
    live = project_outcomes(model, analysis, reordered)
    aligned = match_requirement_catalog(baseline, live)
    assert len(aligned) == len(baseline.requirements)
    for original, current in zip(baseline.requirements, aligned, strict=True):
        assert original.parameter_id == current.parameter_id
        assert len(original.source_records) == len(current.source_records)
        assert all(a is b for a, b in zip(original.source_records, current.source_records))


@pytest.mark.parametrize("kind", ("holes", "u_channel"))
def test_member_cannot_erase_one_parameter_while_retaining_its_source(kind):
    _, model, analysis = detect_intake(kind)
    baseline = catalog_for(model, analysis)
    outcomes = dict(outcomes_for(model, analysis))
    family, rows = next(
        (family, rows)
        for family, rows in outcomes.items()
        if len(rows) > 1
        and rows[0].source_records
        and any(other.source_records == rows[0].source_records for other in rows[1:])
    )
    outcomes[family] = rows[1:]
    live = project_outcomes(model, analysis, outcomes)
    # The original occurrence remains represented, so the whole-source census alone
    # cannot detect this loss. The sealed parameter denominator must reject it.
    assert len(live.requirements) == len(baseline.requirements) - 1
    with pytest.raises(ReportUnavailableError, match="requirement identities"):
        match_requirement_catalog(baseline, live)


def test_local_evidence_state_does_not_remove_a_catalog_requirement(intake):
    _, model, analysis = intake
    baseline = catalog_for(model, analysis)
    outcomes = outcomes_for(model, analysis)
    locally_inapplicable = {
        family: tuple(
            replace(row, state="inapplicable") if row.state != "unsupported" else row
            for row in rows
        )
        for family, rows in outcomes.items()
    }
    live = project_outcomes(model, analysis, locally_inapplicable)
    aligned = match_requirement_catalog(baseline, live)
    assert len(aligned) == len(baseline.requirements)
    assert all(row.intrinsic_exclusion is None for row in aligned)


def test_preink_catalog_keeps_complete_source_groups_and_conditional_obligations(intake):
    kind, model, analysis = intake
    catalog = catalog_for(model, analysis)
    assert catalog.evidence is analysis.recognition_evidence
    assert catalog.ownership is analysis.recognition_ownership
    assert len(catalog.families) == 25
    assert catalog.requirements
    issued = tuple(catalog.evidence.record(ref) for ref in catalog.evidence.features)
    for row in catalog.requirements:
        assert row.source_records or row.source_profile is not None
        assert all(any(record is source for source in issued) for record in row.source_records)
        assert type(row.requirement_count_known) is bool
        assert row.requirement_count > 0
    if kind == "holes":
        holes = analysis.recognition.holes
        assert {HoleSpec.from_hole(hole).bottom == "through" for hole in holes} == {False, True}
        diameter = [row for row in catalog.requirements if row.parameter_id == "bore.diameter"]
        assert len(diameter) == 2
        assert all(len(row.source_records) == 1 for row in diameter)
        assert diameter[0].source_records[0] is not diameter[1].source_records[0]
    else:
        plates = [row for row in catalog.requirements if row.family == "plates"]
        assert len(plates) == (3 if kind == "u_channel" else 2)
        assert {row.parameter_id for row in plates} == {"thickness.length"}
        derived = [row for row in plates if row.dependency_alternatives]
        assert len(derived) == (1 if kind == "u_channel" else 2)
        assert all(row.intrinsic_exclusion is None for row in plates)
        assert all(recipe.supports for row in derived for recipe in row.dependency_alternatives)


def test_catalog_refuses_equal_clone_conversion_owners(intake):
    _, model, analysis = intake
    first = next(
        binding.features[0]
        for binding in analysis.recognition_ownership.bindings
        if binding.features
    )
    clone = replace(first)
    assert clone == first and clone is not first
    with pytest.raises(ReportUnavailableError, match="conversion-time"):
        catalog_for(
            replace(
                model,
                features=[clone if feature is first else feature for feature in model.features],
            ),
            analysis,
        )


def test_catalog_rejects_foreign_source_records_before_storing_authority(intake, monkeypatch):
    from draftwright.linting import requirements

    _, model, analysis = intake
    original = requirements.recognized_requirement_outcomes
    changed = []

    def substitute(*args, **kwargs):
        rows = dict(original(*args, **kwargs))
        for family, outcomes in rows.items():
            for index, outcome in enumerate(outcomes):
                if outcome.source_records:
                    source = outcome.source_records[0]
                    clone = replace(source)
                    assert clone == source and clone is not source
                    altered = replace(outcome, source_records=(clone, *outcome.source_records[1:]))
                    rows[family] = (*outcomes[:index], altered, *outcomes[index + 1 :])
                    changed.append(source)
                    return rows
        raise AssertionError("fixture emitted no exact source records")

    monkeypatch.setattr(requirements, "recognized_requirement_outcomes", substitute)
    with pytest.raises(ReportUnavailableError, match="evidence authority"):
        catalog_for(model, analysis)
    assert changed


@pytest.mark.parametrize("remove_family", (False, True))
def test_catalog_refuses_an_erased_nonempty_ledger(intake, monkeypatch, remove_family):
    from draftwright.linting import requirements

    _, model, analysis = intake
    original = requirements.recognized_requirement_outcomes
    changed = []

    def erase(*args, **kwargs):
        rows = dict(original(*args, **kwargs))
        family = next(
            name for name, values in rows.items() if values and name != "outer_profile_angles"
        )
        assert rows[family]
        changed.append(family)
        if remove_family:
            del rows[family]
        else:
            rows[family] = ()
        return rows

    monkeypatch.setattr(requirements, "recognized_requirement_outcomes", erase)
    with pytest.raises(ReportUnavailableError, match="family roster|no ledger outcome"):
        catalog_for(model, analysis)
    assert changed


def test_catalog_rejects_foreign_and_collapsed_dependency_witnesses(monkeypatch):
    from draftwright.linting import requirements

    model, analysis = _detect_part_model_analysis(
        Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)
    )
    original = requirements.recognized_requirement_outcomes
    calls = []
    mode = "foreign"

    def damage(*args, **kwargs):
        rows = dict(original(*args, **kwargs))
        first, *rest = rows["plates"]
        (recipe,) = first.dependency_alternatives
        envelope, left, right = recipe.supports
        assert left.feature is right.feature and left.hi != right.hi
        if mode == "foreign":
            clone = replace(left.feature)
            assert clone == left.feature and clone is not left.feature
            altered = replace(left, feature=clone)
            supports = (envelope, altered, right)
        else:
            supports = (envelope, left, left)
        calls.append(mode)
        rows["plates"] = (
            replace(first, dependency_alternatives=(replace(recipe, supports=supports),)),
            *rest,
        )
        return rows

    monkeypatch.setattr(requirements, "recognized_requirement_outcomes", damage)
    with pytest.raises(ReportUnavailableError, match="foreign or unrelated owner"):
        catalog_for(model, analysis)
    mode = "collapsed"
    with pytest.raises(ReportUnavailableError, match="distinct measurement witness"):
        catalog_for(model, analysis)
    assert calls == ["foreign", "collapsed"]


def test_groove_floor_absorption_does_not_demand_a_second_step_ledger():
    source = Path(__file__).parent / "fixtures/evaluation/groove-lone-y.step"
    model, analysis = _detect_part_model_analysis(source)
    catalog = catalog_for(model, analysis)
    bindings = [
        binding
        for binding in analysis.recognition_ownership.bindings
        if binding.reason_code == "turned_step_groove_owner"
    ]
    assert bindings
    for binding in bindings:
        record = analysis.recognition_evidence.record(binding.occurrence)
        assert not any(
            any(source is record for source in row.source_records)
            for row in catalog.requirements
            if row.family == "turned_steps"
        )
    assert any(row.family == "grooves" for row in catalog.requirements)


@pytest.mark.parametrize(
    "damage",
    (
        {"lo": float("nan")},
        {"hi": float("inf")},
        {"lo": True},
        {"hi": None},
        {"axis": None},
        {"axis": "u"},
        {"lo": 99},
        {"location_point": (0, float("nan"), 0)},
        {"location_point": (0, 1)},
    ),
)
def test_catalog_refuses_malformed_physical_dependency_witnesses(damage):
    _, model, analysis = detect_intake("shoulders")
    outcomes = dict(outcomes_for(model, analysis))
    first, *rest = outcomes["plates"]
    (recipe,) = first.dependency_alternatives
    envelope, left, right = recipe.supports
    damaged = replace(left, **damage)
    outcomes["plates"] = (
        replace(
            first, dependency_alternatives=(replace(recipe, supports=(envelope, damaged, right)),)
        ),
        *rest,
    )
    with pytest.raises(ReportUnavailableError, match="invalid physical witness"):
        project_outcomes(model, analysis, outcomes)


@pytest.mark.parametrize("erase", (False, True))
def test_live_catalog_refuses_changed_dependency_rule_or_interval(erase):
    _, model, analysis = detect_intake("shoulders")
    baseline = catalog_for(model, analysis)
    outcomes = dict(outcomes_for(model, analysis))
    first, *rest = outcomes["plates"]
    (recipe,) = first.dependency_alternatives
    envelope, left, right = recipe.supports
    alternatives = (
        ()
        if erase
        else (replace(recipe, supports=(envelope, replace(left, hi=left.hi + 1), right)),)
    )
    outcomes["plates"] = (replace(first, dependency_alternatives=alternatives), *rest)
    live = project_outcomes(model, analysis, outcomes)
    with pytest.raises(ReportUnavailableError, match="requirement shape"):
        match_requirement_catalog(baseline, live)
