"""#1397 — an `unverifiable` finding must not print `?` where a requirement id belongs.

The message read

    hole at (12.0, 7.0, 0.0) requirement ? cannot be joined to measurement provenance
    without guessing

which is a diagnostic about guessing that requires one. The issue guessed the cause was a
formatter reached with an unresolved id. It is not: `unverifiable` is recorded exactly when
a recognised source matched **no** IR feature, so `_parameter_ids` was never called and no
per-requirement id exists to print. `requirement_count` already carried the truth — how many
physical requirements the recognised source has, all of them unattributable — and only the
message threw it away.

So the fix names the count instead of inventing ids: `all 4 physical requirements, which no
IR feature claimed, cannot be joined ...`. Nine coverage modules printed the same sentinel;
`turned_step_coverage` records it in its ledger but builds no message from it, so it is
deliberately untouched.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.builder import detect_part_model
from draftwright.linting.issues import UNJOINED_PARAMETER_ID, requirement_subject

_XYZ_MIN = (None, None, None)


def _uncertifiable_hole_drawing():
    """A circular hole declared as a `double_d` profile: recognised, but joinable to nothing.

    This is the fixture `test_declared_structural_profile_cannot_certify_a_circular_hole`
    already relies on, reused because it is the narrowest way to reach the state — exactly
    one recognised source, exactly one `unverifiable` outcome, four physical requirements.
    """

    from build123d import Align

    align = (Align.MIN, Align.MIN, Align.MIN)
    part = Box(80, 50, 10, align=align) - Pos(12, 7, 0) * Cylinder(4, 10, align=align)
    detected = detect_part_model(part)
    circular = next(feature for feature in detected.features if feature.kind == "hole")
    profiled = replace(
        circular, profile="double_d", across_flats=3.0, profile_direction=(1.0, 0.0, 0.0)
    )
    declared = replace(
        detected,
        features=[profiled if feature is circular else feature for feature in detected.features],
    )
    return build_drawing(part, model=declared)


@pytest.fixture(scope="module")
def unverifiable_drawing():
    return _uncertifiable_hole_drawing()


def test_the_defect_state_is_actually_reached(unverifiable_drawing):
    """Precondition: the ledger really does hold the sentinel, with a count above one.

    Without this the message assertions below could pass on a drawing that never produced an
    unverifiable outcome at all — the failure mode CLAUDE.md names, and the reason a message
    test that only greps for absence of `?` would be worthless.
    """

    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    unverifiable_drawing.lint()
    outcomes = hole_requirement_outcomes(
        unverifiable_drawing.recognition(),
        unverifiable_drawing.model().features,
        unverifiable_drawing.registry,
        compile_dimensions(unverifiable_drawing.model()).diagnostics,
    )
    unverifiable = [outcome for outcome in outcomes if outcome.state == "unverifiable"]
    assert [(outcome.parameter_id, outcome.requirement_count) for outcome in unverifiable] == [
        (UNJOINED_PARAMETER_ID, 4)
    ]


def test_the_finding_names_the_requirements_instead_of_printing_a_question_mark(
    unverifiable_drawing,
):
    findings = [
        issue for issue in unverifiable_drawing.lint() if issue.code.endswith("_unverifiable")
    ]
    assert findings, "fixture produced no unverifiable finding"
    for issue in findings:
        assert "requirement ?" not in issue.message, issue.message
        assert "measurement ?" not in issue.message, issue.message
    assert any("all 4 physical requirements" in issue.message for issue in findings), [
        issue.message for issue in findings
    ]
    assert any("no IR feature claimed" in issue.message for issue in findings)


def test_a_joined_requirement_still_prints_its_own_id(unverifiable_drawing):
    """The converse. A message that named nothing specific would also pass the test above."""

    part = Box(80, 50, 10) - Pos(12, 7, 0) * Cylinder(4, 10)
    drawing = build_drawing(part)
    named = [
        issue
        for issue in drawing.lint()
        if issue.code.endswith(("_suppressed", "_missing")) and "bore." in issue.message
    ]
    for issue in named:
        assert "requirement ?" not in issue.message
    # And directly, so this cannot pass by the corpus happening to emit no such finding.
    assert (
        requirement_subject("bore.diameter", 1, noun="requirement") == "requirement bore.diameter"
    )


@pytest.mark.parametrize(
    ("parameter_id", "count", "noun", "expected"),
    [
        ("bore.depth", 1, "requirement", "requirement bore.depth"),
        ("bore.depth", 9, "measurement", "measurement bore.depth"),
        ("?", 1, "measurement", "its sole physical measurement, which no IR feature claimed,"),
        ("?", 6, "requirement", "all 6 physical requirements, which no IR feature claimed,"),
    ],
)
def test_requirement_subject_table(parameter_id, count, noun, expected):
    assert requirement_subject(parameter_id, count, noun=noun) == expected


def test_no_module_can_both_record_the_sentinel_and_print_it_raw():
    """The pairing that IS the defect: a module that mints `?` must not interpolate it.

    Nine copies of one sentence produced nine copies of one defect, so the guard has to see
    a tenth. It is deliberately not "no module may interpolate `parameter_id`": six coverage
    modules do that and are correct, because none of them ever records the sentinel — their
    outcomes always carry a real id. Converting those would be churn against no defect, and
    a guard that demanded it would be asserting a style, not a property.
    """

    from pathlib import Path

    linting = Path(__file__).resolve().parent.parent / "src" / "draftwright" / "linting"
    offenders = sorted(
        path.name
        for path in linting.glob("*_coverage.py")
        for text in [path.read_text(encoding="utf-8")]
        if f'"{UNJOINED_PARAMETER_ID}"' in text and "{outcome.parameter_id}" in text
    )
    assert offenders == [], offenders

    # Both halves of the predicate must be capable of firing, or this passes for the wrong
    # reason. Each is checked against a real file rather than a synthetic string.
    mints = sorted(
        path.name
        for path in linting.glob("*_coverage.py")
        if f'"{UNJOINED_PARAMETER_ID}"' in path.read_text(encoding="utf-8")
    )
    prints_raw = sorted(
        path.name
        for path in linting.glob("*_coverage.py")
        if "{outcome.parameter_id}" in path.read_text(encoding="utf-8")
    )
    assert len(mints) >= 9, mints
    assert len(prints_raw) >= 6, prints_raw
    assert not set(mints) & set(prints_raw)

    # And that the nine really were rewired, rather than the sentinel having vanished.
    assert "_subject(outcome)" in (linting / "hole_coverage.py").read_text(encoding="utf-8")
