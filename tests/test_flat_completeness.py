"""Flat completeness follows semantic provenance, never presentation (#1018 Gate 1).

The discovery branch #1011 tried to recover "does this annotation define this flat?" from
labels, leader tips, projected chords, and stock radii. These tests start at the opposite end:
physical requirements come from the shared recognition result, and engine outcomes are joined
through the IR feature and compiler-owned ``DimensionId`` already stored in the annotation
registry.
"""

from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos, import_step

from draftwright import Sheet, build_drawing
from draftwright.linting import LintIssue
from draftwright.linting.flat_coverage import flat_requirement_outcomes
from draftwright.model.declare import flat as declare_flat
from draftwright.recognition import recognise_flats
from draftwright.recognition.flats import Flat
from draftwright.registry import AnnotationRegistry

_ISSUE_914 = Path(__file__).parent / "fixtures" / "if_step_flat_across_cylinder.step"


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
            axis_direction=flat.axis_direction,
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
    completeness = dwg.lint_summary()["quality"]["completeness"]
    assert completeness["denominator"] == "recognition"
    assert completeness["coverage"] == "partial"
    assert completeness["scope"] == "audited_recognized_requirements"
    assert completeness["requirements"] == completeness["placed"] == 1
    assert completeness["available"] is True and completeness["audited_score"] == 1.0


def test_issue_914_case_study_keeps_its_only_flat_definition():
    """The real topology, normalized from metres to millimetres for a stable regression.

    Before #1046 the flat was recognised but its only size definition was dropped at both
    scales.  The boundary-aware fallback must retain the A/F callout without disturbing the
    diameter or step dimensions that already survived in the reported drawing.
    """
    part = import_step(str(_ISSUE_914)).scale(0.001)
    dwg = build_drawing(part)

    assert Counter(feature.kind for feature in dwg.model().features) == Counter(
        {"boss": 1, "envelope": 1, "step_level": 1, "flat": 1, "rotational": 1}
    ), "the imported case must keep the inventory that made #914 a flat-placement defect"
    callouts = [name for name in dwg.annotations() if name.startswith("m_flat_")]
    assert callouts == ["m_flat_y0"], "one physical requirement needs exactly one definition"
    assert dwg.get_annotation(callouts[0]).label == "25 A/F"
    assert len(dwg.measurement_keys(callouts[0])) == 1, (
        "the visible callout must retain compiler provenance for the recognised requirement"
    )
    assert dwg.get_annotation("dim_od").label == "ø40"
    assert dwg.get_annotation("dim_step_0").label == "25"

    prohibited = {
        "annotation_out_of_bounds",
        "annotation_overlap",
        "flat_dropped",
        "leader_crosses_silhouette",
    }
    issues = dwg.lint()
    assert not [
        issue
        for issue in issues
        if issue.code in prohibited or issue.code.startswith("flat_requirement_")
    ]


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


def test_direction_is_a_load_bearing_part_of_slanted_requirement_identity():
    """Two directions through one perpendicular foot are different stock lines.

    This deliberately holds axis letter, line coordinates, span and A/F equal. Removing
    direction from the key collapses the two physical requirements into one and must fail.
    """
    drawing = build_drawing(_double_d())
    recognition = drawing.recognition()
    assert recognition is not None
    directions = ((1.0, 0.0, 1.0), (1.0, 0.0, -1.0))
    points = ((3.0, 0.0, -3.0), (3.0, 0.0, 3.0))
    records = tuple(
        Flat(
            axis="x",
            across=13.0,
            at=point,
            axis_line=(0.0, 0.0),
            stock_span=(-20.0, 20.0),
            axis_direction=direction,
        )
        for point, direction in zip(points, directions, strict=True)
    )
    features = tuple(
        declare_flat(
            axis=record.axis,
            across=record.across,
            at=record.at,
            axis_line=record.axis_line,
            stock_span=record.stock_span,
            axis_direction=record.axis_direction,
        )
        for record in records
    )

    outcomes = flat_requirement_outcomes(
        replace(recognition, flats=records),
        features,
        AnnotationRegistry(),
    )
    assert [outcome.state for outcome in outcomes] == ["missing", "missing"]
    assert len({outcome.axis_direction for outcome in outcomes}) == 2


def test_authored_omission_is_suppressed_not_missing():
    dwg = _declared_flat_drawing(suppress=True)
    assert not [name for name in dwg.annotations() if name.startswith("m_flat_")], (
        "precondition: the authored set omitted the A/F measurement"
    )
    assert _flat_codes(dwg) == ["flat_requirement_suppressed"]
    completeness = dwg.lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == completeness["suppressed"] == 1
    assert completeness["placed"] == 0
    assert completeness["audited_score"] == 0.0


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
    dwg = build_drawing(_flatted_shaft(), page="A4", scale=3.0)
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
    completeness = dwg.lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == completeness["unverifiable"] == 1
    assert completeness["placed"] == 0
    assert completeness["audited_score"] == 0.0


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
            axis_direction=flat.axis_direction,
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


def test_hole_evidence_keeps_full_lint_issue_positional_compatibility():
    issue = LintIssue(
        "warning",
        "message",
        None,
        "code",
        "suggestion",
        ("measurement",),
        ("source:1",),
        "placement",
    )

    assert issue.measurement_ids == ("measurement",)
    assert issue.source_ids == ("source:1",)
    assert issue.outcome_stage == "placement"
    assert issue.hole_requirement_ids == ()
