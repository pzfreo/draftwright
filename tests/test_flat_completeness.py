"""Flat completeness follows semantic provenance, never presentation (#1018 Gate 1).

The discovery branch #1011 tried to recover "does this annotation define this flat?" from
labels, leader tips, projected chords, and stock radii. These tests start at the opposite end:
physical requirements come from the shared recognition result, and engine outcomes are joined
through the IR feature and compiler-owned ``DimensionId`` already stored in the annotation
registry.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos

from draftwright import Sheet, build_drawing
from draftwright.linting import LintIssue
from draftwright.linting.flat_coverage import flat_requirement_outcomes
from draftwright.recognition import recognise_flats
from draftwright.registry import AnnotationRegistry


def _double_d():
    """One Z-axis stock region with two opposed faces: one physical A/F requirement."""
    return (
        Cylinder(15, 40) - Pos(12.5, 0, 0) * Box(10, 40, 50) - Pos(-12.5, 0, 0) * Box(10, 40, 50)
    )


def _lone_d():
    return Cylinder(15, 40) - Pos(12.5, 0, 0) * Box(10, 40, 50)


def _two_parallel_lobes():
    return _lone_d() + Pos(100, 0, 0) * _lone_d()


def _two_coaxial_regions():
    return _lone_d() + Pos(0, 0, 80) * _lone_d()


def _flatted_shaft():
    """The #1011 drop fixture: a stepped shaft truncated to one 25 mm double-D."""
    part = Cylinder(20, 40) + Pos(0, 0, 30) * Cylinder(12, 20)
    return part & Box(
        25,
        60,
        100,
        align=(Align.CENTER, Align.CENTER, Align.CENTER),
    )


def _flat_callout(dwg) -> str:
    names = [name for name in dwg.annotations() if name.startswith("m_flat_")]
    assert len(names) == 1, f"fixture needs one placed flat callout, got {names}"
    return names[0]


def _flat_codes(dwg) -> list[str]:
    return [issue.code for issue in dwg.lint() if issue.code.startswith("flat_requirement_")]


def _outcomes_without_annotations(part):
    dwg = build_drawing(part)
    recognition = dwg.recognition()
    assert recognition is not None
    return flat_requirement_outcomes(
        recognition,
        dwg.model().features,
        AnnotationRegistry(),
    )


def _declared_flat_drawing(*, suppress: bool = False):
    part = _double_d()
    recognised = recognise_flats(part)
    sheet = Sheet(part)
    for flat in recognised:
        sheet.flat(
            axis=flat.axis,
            across=flat.across,
            at=flat.at,
            axis_line=flat.axis_line,
            stock_span=flat.stock_span,
        )
    if suppress:
        envelope = sheet.envelope()
        sheet.dimension(envelope, "width.length")
    else:
        sheet.auto_dimensions()
    return sheet.build()


def test_a_placed_engine_callout_satisfies_the_recognised_requirement():
    dwg = build_drawing(_double_d())
    name = _flat_callout(dwg)
    assert len(dwg.measurement_keys(name)) == 2, (
        "a double-D callout must retain both compiler measurement ids; otherwise a clean "
        "coverage result would have no semantic evidence"
    )
    assert _flat_codes(dwg) == []


def test_removing_the_callout_produces_missing():
    dwg = build_drawing(_double_d())
    dwg.remove(_flat_callout(dwg))
    assert _flat_codes(dwg) == ["flat_requirement_missing"]


def test_free_text_quoting_the_size_does_not_certify_the_requirement():
    dwg = build_drawing(_double_d())
    dwg.remove(_flat_callout(dwg))
    dwg.note("25 A/F", (20, 20), view="plan")
    assert _flat_codes(dwg) == ["flat_requirement_missing"]


def test_severing_measurement_provenance_is_unverifiable_not_placed():
    """The targeted mutation: keep the real callout and its feature ownership, but remove
    only its compiler measurement identity. A guard that inspects label/type/tip stays green;
    a semantic guard must change from placed to unverifiable."""
    dwg = build_drawing(_double_d())
    name = _flat_callout(dwg)
    identity = dwg.registry.identity_of(name)
    identity["measurement"] = ()
    dwg.registry.reapply(name, identity)

    assert _flat_codes(dwg) == ["flat_requirement_unverifiable"]


def test_stock_identity_defines_requirement_cardinality_without_page_matching():
    double_d = _outcomes_without_annotations(_double_d())
    parallel = _outcomes_without_annotations(_two_parallel_lobes())
    coaxial = _outcomes_without_annotations(_two_coaxial_regions())

    assert [outcome.state for outcome in double_d] == ["missing"], (
        "two faces on one stock region are one physical A/F requirement"
    )
    assert [outcome.state for outcome in parallel] == ["missing", "missing"], (
        "equal flats on distinct parallel axis lines are two requirements"
    )
    assert [outcome.state for outcome in coaxial] == ["missing", "missing"], (
        "equal flats on disjoint spans of one axis line are two requirements"
    )


def test_authored_omission_is_suppressed_not_missing():
    dwg = _declared_flat_drawing(suppress=True)
    assert not [name for name in dwg.annotations() if name.startswith("m_flat_")], (
        "precondition: the authored set omitted the A/F measurement"
    )
    assert _flat_codes(dwg) == ["flat_requirement_suppressed"]


def test_a_planner_omission_is_not_authored_suppression():
    dwg = build_drawing(_double_d())
    recognition = dwg.recognition()
    assert recognition is not None
    features = dwg.model().features
    omissions = tuple(
        SimpleNamespace(feature=feature, parameter_id="flat.length", authored=False)
        for feature in features
        if feature.kind == "flat"
    )

    outcomes = flat_requirement_outcomes(
        recognition,
        features,
        AnnotationRegistry(),
        omissions,
    )
    assert [outcome.state for outcome in outcomes] == ["missing"]


def test_forced_placement_failure_is_dropped_not_missing():
    dwg = build_drawing(_flatted_shaft(), page="A4", scale=2.0)
    issues = dwg.lint()
    drop = next(issue for issue in issues if issue.code == "flat_dropped")
    assert len(drop.measurement_ids) == 2, (
        "the placement outcome must retain the grouped compiler identities"
    )
    recognition = dwg.recognition()
    assert recognition is not None
    outcomes = flat_requirement_outcomes(recognition, dwg.model().features, dwg.registry)
    assert [outcome.state for outcome in outcomes] == ["dropped"]
    assert [
        issue.code
        for issue in issues
        if issue.code in {"flat_dropped", "flat_requirement_dropped"}
    ] == ["flat_dropped"], "one placement failure must not be reported and scored twice"

    # Mutation: a generic `flat_dropped` code is not semantic evidence by itself.
    dwg.registry.reset_issues()
    dwg.registry.record_issue(replace(drop, measurement_ids=()))
    assert _flat_codes(dwg) == ["flat_requirement_missing"]


def test_a_recognised_requirement_missing_from_the_declared_ir_is_unverifiable():
    sheet = Sheet(_double_d())
    envelope = sheet.envelope()
    sheet.dimension(envelope, "width.length")
    dwg = sheet.build()

    assert _flat_codes(dwg) == ["flat_requirement_unverifiable"]


def test_requirement_identity_without_source_record_correspondence_is_unverifiable():
    part = _double_d()
    sheet = Sheet(part)
    for flat in recognise_flats(part):
        shifted_at = (flat.at[0], flat.at[1] + 1.0, flat.at[2])
        sheet.flat(
            axis=flat.axis,
            across=flat.across,
            at=shifted_at,
            axis_line=flat.axis_line,
            stock_span=flat.stock_span,
        )
    sheet.auto_dimensions()
    dwg = sheet.build()

    assert _flat_callout(dwg), "precondition: the stale declaration still rendered a callout"
    assert _flat_codes(dwg) == ["flat_requirement_unverifiable"]


def test_automatic_and_declared_paths_agree_when_provenance_is_complete():
    automatic = build_drawing(_double_d())
    declared = _declared_flat_drawing()

    assert _flat_callout(automatic)
    assert _flat_callout(declared)
    assert _flat_codes(automatic) == _flat_codes(declared) == []
    assert declared.recognition() is not None, "declared critique must cache its one aggregate"


def test_a_caller_assembled_empty_inventory_cannot_silence_coverage():
    with pytest.raises(TypeError, match="RecognitionResult"):
        flat_requirement_outcomes(SimpleNamespace(flats=()), (), AnnotationRegistry())


def test_measurement_provenance_keeps_lint_issue_positional_compatibility():
    issue = LintIssue("warning", "message", None, "code", "suggestion")
    assert issue.suggestion == "suggestion"
    assert issue.measurement_ids == ()
