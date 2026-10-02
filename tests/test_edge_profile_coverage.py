"""Shared edge-profile coverage refuses ambiguous physical ownership."""

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import import_step

from draftwright import build_drawing
from draftwright.linting.chamfer_coverage import (
    ChamferRequirementOutcome,
    chamfer_requirement_outcomes,
    lint_chamfer_coverage,
)
from draftwright.linting.fillet_coverage import (
    FilletRequirementOutcome,
    fillet_requirement_outcomes,
    lint_fillet_coverage,
)


@pytest.mark.parametrize(
    "family,fixture,outcomes_for,lint_for,outcome_type",
    (
        (
            "chamfer",
            "chamfer-compound.step",
            chamfer_requirement_outcomes,
            lint_chamfer_coverage,
            ChamferRequirementOutcome,
        ),
        (
            "fillet",
            "fillet-repeated.step",
            fillet_requirement_outcomes,
            lint_fillet_coverage,
            FilletRequirementOutcome,
        ),
    ),
)
def test_duplicate_physical_keys_do_not_guess_an_ir_owner(
    family, fixture, outcomes_for, lint_for, outcome_type
) -> None:
    part = import_step(Path(__file__).parent / "fixtures" / "evaluation" / fixture)
    drawing = build_drawing(part)
    recognition = drawing.recognition()
    assert recognition is not None
    features = drawing.model().features
    source_inventory = family + "s"
    sources = getattr(recognition, source_inventory)
    assert sources
    assert outcomes_for(recognition, features, drawing.registry)[0].state == "placed"

    source = sources[0]
    duplicated = replace(recognition, **{source_inventory: (source, source)})
    outcomes = outcomes_for(duplicated, features, drawing.registry)
    assert len(outcomes) == 2
    assert all(type(outcome) is outcome_type for outcome in outcomes)
    assert all(
        outcome.state == "unverifiable"
        and outcome.features == ()
        and outcome.source_records == (source,)
        for outcome in outcomes
    )
    issues = lint_for(
        part,
        recognition=duplicated,
        features=features,
        registry=drawing.registry,
        assembly=False,
    )
    assert [(issue.severity, issue.code) for issue in issues] == [
        ("warning", f"{family}_requirement_unverifiable"),
        ("warning", f"{family}_requirement_unverifiable"),
    ]
