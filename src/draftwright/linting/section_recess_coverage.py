"""Explicit outcomes for published recess geometry outside the supported drawing grammar."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from quiddity import RecognitionResult, SectionRecess, SectionRecessRefusal

from draftwright.linting.issues import UNJOINED_PARAMETER_ID, LintIssue
from draftwright.section_recess_contract import UnsupportedSectionRecess, section_recess_fields


@dataclass(frozen=True)
class UnsupportedRecessOutcome:
    source_at: tuple[float, float, float] | None
    reason: str
    parameter_id: str = UNJOINED_PARAMETER_ID
    state: str = "unsupported"
    requirement_count: int = 1
    features: tuple = ()
    source_records: tuple[SectionRecess | SectionRecessRefusal, ...] = field(
        default=(), repr=False, compare=False, kw_only=True
    )


def unsupported_section_recess_outcomes(recognition) -> list[UnsupportedRecessOutcome]:
    if recognition is None:
        return []
    if type(recognition) is not RecognitionResult:
        raise TypeError("section recess completeness requires the exact RecognitionResult")
    outcomes = []
    for source in recognition.section_recesses:
        try:
            section_recess_fields(source)
        except UnsupportedSectionRecess as exc:
            geometry = source.geometry
            x, y, z = (
                geometry.frame.origin[i] + sum(geometry.run_interval) / 2 * geometry.frame.run[i]
                for i in range(3)
            )
            outcomes.append(
                UnsupportedRecessOutcome((x, y, z), str(exc), source_records=(source,))
            )
    for refusal in recognition.section_recess_refusals:
        if type(refusal) is not SectionRecessRefusal:
            raise TypeError("recess refusals require exact public refusal records")
        outcomes.append(UnsupportedRecessOutcome(None, refusal.reason, source_records=(refusal,)))
    return outcomes


def lint_section_recess_coverage(recognition) -> list[LintIssue]:
    outcomes = unsupported_section_recess_outcomes(recognition)
    totals = Counter(
        source.classification.feature_kind
        for outcome in outcomes
        for source in outcome.source_records
        if isinstance(source, SectionRecess)
    )
    ordinals: Counter[str] = Counter()
    issues = []
    for outcome in outcomes:
        source = outcome.source_records[0]
        if isinstance(source, SectionRecessRefusal):
            issues.append(
                LintIssue(
                    severity="warning",
                    code="section_recess_recognition_refused",
                    message=f"section recess recognition on body {source.body} refused: {source.reason}",
                )
            )
            continue
        kind = source.classification.feature_kind
        ordinals[kind] += 1
        count = f"({ordinals[kind]} of {totals[kind]})"
        sides = len(source.geometry.profile.boundary)
        if kind == "passage":
            description = f"recognised {sides}-edge prismatic through-opening {count}"
        elif kind == "pocket":
            low, high = source.geometry.run_interval
            description = (
                f"recognised {sides}-sided blind prismatic recess {high - low:g} mm deep {count}"
            )
        else:
            description = f"section recess {source.index} on body {source.body}"
        issues.append(
            LintIssue(
                severity="warning",
                code=(
                    "passage_requirement_unsupported"
                    if source.classification.feature_kind == "passage"
                    else "prismatic_pocket_requirement_unsupported"
                    if source.classification.feature_kind == "pocket"
                    else "section_recess_requirement_unsupported"
                ),
                message=(
                    f"{description} is not represented by Draftwright dimensions: {outcome.reason}; "
                    "review and define its manufacturing section outside automatic drawing approval"
                ),
            )
        )
    return issues
