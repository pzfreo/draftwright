"""Separable, honest drawing-quality components (#1127).

The legacy lint ``score`` is a severity-weighted diagnostic convenience, not a drawing-
quality verdict.  This module exposes the independently observable terms without composing
them into another blessed scalar:

- completeness follows recognition-owned physical requirements to compiler outcomes;
- legibility contains only placement/layout diagnostics. Its compatibility counts remain raw
  lint findings, while its score and ``primary_*`` counts collapse only producer-identified
  pair observations from one annotation and failure mechanism;
- restraint fails closed until measurement provenance can classify every annotation.

Completeness is deliberately marked partial.  Only the feature families with a semantic
``*_outcomes`` ledger participate today; warning counts and declared IR are never substituted
for the missing physical denominator.

Its scalar is therefore named ``audited_score``, not ``score``.  A part reaches 1.0 whenever
every requirement recognition *did* identify was placed, however much of the part it missed:
what was never recognised never became a requirement, so it is absent from the ledger rather
than counted against it.  (Where nothing auditable was recognised at all, the component
reports ``available: False`` and a ``None`` score — not a perfect one.)  The qualifier
belongs in the field name, where it survives being quoted, and not only in the metadata
beside it.  **It is not a completion gate**: gate on issue codes and severities (ADR 0002),
and read ``excludes`` for what the denominator cannot see.
"""

from __future__ import annotations

from collections import Counter

from draftwright.linting.channel_coverage import channel_requirement_outcomes
from draftwright.linting.flat_coverage import flat_requirement_outcomes
from draftwright.linting.hole_coverage import hole_requirement_outcomes
from draftwright.linting.issues import LintIssue
from draftwright.linting.polygonal_stock_coverage import polygonal_stock_outcomes
from draftwright.linting.slot_coverage import slot_requirement_outcomes

_OUTCOME_STATES = ("placed", "suppressed", "dropped", "missing", "unverifiable")

# Structural diagnostics that describe whether the composed sheet remains readable. Placement
# drops have their own explicit inventory below because a ``*_dropped`` suffix alone cannot
# distinguish validation from layout.
_LEGIBILITY_CODES = frozenset(
    {
        "annotation_out_of_bounds",
        "annotation_overlap",
        "detail_unplaceable",
        "dim_inside_part",
        "label_centerline_overlap",
        "leader_crosses_silhouette",
        "leader_line_through_text",
        "placement_unsatisfiable",
        "view_annotation_inside_extents",
        "view_annotation_overlap",
        "view_out_of_bounds",
        "view_overlap",
    }
)

# Codes whose meaning is unambiguously "a valid annotation candidate did not land" are
# recognised by their ``*_dropped`` suffix rather than by a listed vocabulary. A list has to
# be remembered; the suffix cannot be forgotten, so a drop code introduced tomorrow counts
# against legibility on the day it is introduced instead of scoring as perfectly legible
# until somebody notices (#1127 review). The two codes that cannot be read off their suffix
# (``gdt_dropped``/``pmi_dropped``, which cover validation failures too) carry an explicit
# ``outcome_stage`` from their producers instead; see ``_is_placement_drop``.

# Recognition inventories that represent potentially dimension-bearing physical families.
_RECOGNISED_REQUIREMENT_FAMILIES = {
    "holes": "holes",
    "double_d_bores": "profiled_bores",
    "hole_patterns": "hole_patterns",
    "bosses": "bosses",
    "polygonal_bosses": "polygonal_bosses",
    "polygonal_stock": "polygonal_stock",
    "channels": "channels",
    "slots": "slots",
    "slot_patterns": "slot_patterns",
    "grooves": "grooves",
    "flats": "flats",
    "pockets": "pockets",
    "pocket_patterns": "pocket_patterns",
    "pads": "pads",
    "repeating_radial_profiles": "repeating_radial_profiles",
    "turned_steps": "turned_steps",
    "chamfers": "chamfers",
    "fillets": "fillets",
}

# Inventories that are deliberately NOT requirement families: the substrates would list the
# same physical requirement a second time (a countersink is retained on its recognised
# ``HoleRecord``; unmatched standalone seats fail closed in the hole ledger), and
# ``rotational`` is a classification flag rather than an inventory. Kept
# explicit rather than implied by absence so that a new
# ``RecognitionResult`` inventory cannot silently join the blind spot the completeness
# component exists to report (``tests/test_quality_components.py``).
_NON_REQUIREMENT_INVENTORIES = frozenset(
    {"countersinks", "cylinders", "plates", "risers", "rotational", "step_levels"}
)

_AUDITED_FAMILIES = (
    "channels",
    "flats",
    "hole_patterns",
    "holes",
    "polygonal_stock",
    "slot_patterns",
    "slots",
)

# What the audited score does not cover, emitted as data rather than left to prose. The
# first entry is the dangerous one: a requirement recognition never produced cannot appear
# in any state, so it is absent rather than "missing".
_EXCLUDES = (
    "physical geometry that recognition did not identify",
    "recognized families without a semantic outcome ledger",
)

# The lint check that reports geometry recognition could not account for. Its count is a
# FLOOR on the unrecognised-geometry blind spot, never a measure of it: zero means nothing
# was noticed, not that nothing was missed.
_UNRECOGNISED_GEOMETRY_CODE = "unrecognised_defining_geometry"


def _is_placement_drop(issue) -> bool:
    """Whether this issue reports a valid annotation candidate that did not land.

    ``gdt_dropped`` and ``pmi_dropped`` each cover both malformed input and a candidate that
    simply did not fit, so their producers attach an explicit ``outcome_stage``; that always
    decides. Every other drop is read off the code suffix, which fails closed — an
    unclassified new drop code is counted as a lost annotation rather than ignored.
    """
    stage = getattr(issue, "outcome_stage", None)
    if stage is not None:
        return bool(stage == "placement")
    return bool(issue.code.endswith("_dropped"))


def _is_legibility_issue(issue) -> bool:
    return issue.code in _LEGIBILITY_CODES or _is_placement_drop(issue)


_SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2}


def _primary_issues(issues, aggregation=None) -> list[LintIssue]:
    """Collapse explicit pair observations to one issue per subject and mechanism.

    Pair-producing lint checks opt in through a summary-scoped side ledger. Everything else
    is deliberately independent, even when code and message happen to match: blanket
    text/code deduplication would hide genuinely distinct collisions. If a malformed producer
    gives one group mixed severities, its strongest observation owns the score penalty.
    """
    primary: dict[tuple, LintIssue] = {}
    for ordinal, issue in enumerate(issues):
        token = aggregation.token_for(issue) if aggregation is not None else None
        key = (issue.code, token) if token is not None else (None, ordinal)
        previous = primary.get(key)
        if previous is None or _SEVERITY_RANK[issue.severity] > _SEVERITY_RANK[previous.severity]:
            primary[key] = issue
    return list(primary.values())


def _issue_component(
    issues, *, error_penalty: float, warning_penalty: float, aggregation=None
) -> dict:
    # Compatibility fields remain raw lint-finding counts. Only the scalar penalty uses the
    # explicitly grouped primary inventory; both inventories are exposed and documented.
    errors = sum(issue.severity == "error" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    infos = sum(issue.severity == "info" for issue in issues)
    placement_drops = sum(_is_placement_drop(issue) for issue in issues)
    by_code = Counter(issue.code for issue in issues)
    primary = _primary_issues(issues, aggregation)
    primary_errors = sum(issue.severity == "error" for issue in primary)
    primary_warnings = sum(issue.severity == "warning" for issue in primary)
    primary_infos = sum(issue.severity == "info" for issue in primary)
    primary_by_code = Counter(issue.code for issue in primary)
    return {
        "available": True,
        # Every issue reaching here is a legibility defect by construction, so info severity
        # takes the warning floor too. Lint uses it for "place what fits" drops and for
        # readability faults like a leader crossing a silhouette; either way the component
        # cannot report 1.0 while itemising output it has just called unreadable (#1127).
        "score": max(
            0.0,
            1.0
            - primary_errors * error_penalty
            - (primary_warnings + primary_infos) * warning_penalty,
        ),
        # Raw compatibility inventory. These fields keep their #1127 semantics even when
        # several pair observations contribute only one penalty.
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "placement_drops": placement_drops,
        "by_code": dict(sorted(by_code.items())),
        "raw_issues": len(issues),
        # Score inventory: one entry per independent finding, or per explicitly identified
        # annotation/failure-mechanism group. ``affected_pairs`` counts the raw opt-in pair
        # observations; their complete messages stay in ``lint_summary()['issues']``.
        "primary_issues": len(primary),
        "primary_errors": primary_errors,
        "primary_warnings": primary_warnings,
        "primary_infos": primary_infos,
        "primary_by_code": dict(sorted(primary_by_code.items())),
        "affected_pairs": sum(
            aggregation is not None and aggregation.token_for(issue) is not None
            for issue in issues
        ),
        # Keep the established basis value: severity and the info floor did not change.
        # This additive field names the inventory to which that basis is now applied.
        "basis": "layout_issue_severity_with_info_floor",
        "score_inventory": "primary_issues",
    }


def _empty_completeness(reason: str, unrecognised: int) -> dict:
    return {
        "available": False,
        "audited_score": None,
        "scope": "audited_recognized_requirements",
        "coverage": "unavailable",
        "reason": reason,
        "excludes": list(_EXCLUDES),
        "unrecognised_geometry_reports": unrecognised,
        "denominator": "recognition",
        "audited_families": list(_AUDITED_FAMILIES),
        "unscored_recognized_families": [],
        "requirements": 0,
        **{state: 0 for state in _OUTCOME_STATES},
        "by_family": {},
    }


def _completeness_component(recognition, features, registry, omissions, issues) -> dict:
    unrecognised = sum(issue.code == _UNRECOGNISED_GEOMETRY_CODE for issue in issues)
    if recognition is None:
        return _empty_completeness("physical recognition inventory unavailable", unrecognised)

    outcomes = {
        "channels": channel_requirement_outcomes(recognition, features, registry, omissions),
        "flats": flat_requirement_outcomes(recognition, features, registry, omissions),
        "holes": [],
        "hole_patterns": [],
        "polygonal_stock": polygonal_stock_outcomes(recognition, features, registry, omissions),
        "slots": [],
        "slot_patterns": [],
    }
    for outcome in slot_requirement_outcomes(recognition, features, registry, omissions):
        outcomes["slot_patterns" if outcome.source_kind == "slot_pattern" else "slots"].append(
            outcome
        )
    for hole_outcome in hole_requirement_outcomes(recognition, features, registry, omissions):
        outcomes[
            "hole_patterns" if hole_outcome.source_kind == "hole_pattern" else "holes"
        ].append(hole_outcome)

    counts: Counter = Counter()
    by_family: dict[str, int] = {}
    for family, family_outcomes in outcomes.items():
        family_count = 0
        for outcome in family_outcomes:
            requirement_count = int(getattr(outcome, "requirement_count", 1))
            counts[outcome.state] += requirement_count
            family_count += requirement_count
        by_family[family] = family_count
    requirements = sum(counts.values())
    recognised = {
        family
        for attribute, family in _RECOGNISED_REQUIREMENT_FAMILIES.items()
        if getattr(recognition, attribute, ())
    }
    unaudited = sorted(recognised - set(_AUDITED_FAMILIES))
    audited_score = counts["placed"] / requirements if requirements else None
    if requirements:
        reason = (
            "audited_score covers recognized requirements in audited families only; it is "
            "not evidence that the drawing is complete"
        )
    elif unaudited:
        reason = "recognized requirements exist only in families without outcome ledgers"
    else:
        reason = "no auditable recognized requirements"
    return {
        # Conditional completeness, not physical recall: score the requirements this run
        # recognised AND for which Draftwright has a semantic outcome ledger. Missing
        # recognisers are outside the scope; recognised-but-unscored families remain explicit.
        "available": requirements > 0,
        "audited_score": audited_score,
        "scope": "audited_recognized_requirements",
        "coverage": "partial",
        "reason": reason,
        "excludes": list(_EXCLUDES),
        "unrecognised_geometry_reports": unrecognised,
        "denominator": "recognition",
        "audited_families": list(_AUDITED_FAMILIES),
        "unscored_recognized_families": unaudited,
        "requirements": requirements,
        **{state: counts[state] for state in _OUTCOME_STATES},
        "by_family": by_family,
    }


def quality_components(
    *,
    recognition,
    features,
    registry,
    omissions,
    issues,
    error_penalty: float,
    warning_penalty: float,
    _aggregation=None,
) -> dict:
    """Return independently usable drawing-quality observations.

    No composite score is returned.  In particular, an unavailable restraint component is
    data, not zero: treating unclassified annotations as redundant would recreate the false
    confidence this API is intended to remove.  For the same reason completeness reports an
    ``audited_score`` over a stated denominator rather than a ``score``, and lists what that
    denominator ``excludes`` — a feature recognition never identified is absent from the
    ledger entirely, so a perfect audited score is not a complete drawing.

    Legibility's ``errors``/``warnings``/``infos``/``by_code`` fields retain their original
    raw-finding semantics. ``primary_*`` fields describe the inventory used by ``score``;
    ``affected_pairs`` is the number of raw pair findings that opted into that aggregation.
    Full pair messages remain in :meth:`Drawing.lint` and ``lint_summary()['issues']``.
    """

    legibility_issues = [issue for issue in issues if _is_legibility_issue(issue)]
    return {
        "completeness": _completeness_component(
            recognition, features, registry, omissions, issues
        ),
        "restraint": {
            "available": False,
            "score": None,
            "reason": (
                "measurement provenance and physical requirement equivalence are incomplete"
            ),
        },
        "legibility": _issue_component(
            legibility_issues,
            error_penalty=error_penalty,
            warning_penalty=warning_penalty,
            aggregation=_aggregation,
        ),
    }


__all__ = ["quality_components"]
