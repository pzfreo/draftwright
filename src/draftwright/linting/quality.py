"""Separable, honest drawing-quality components (#1127).

The legacy lint ``score`` is a severity-weighted diagnostic convenience, not a drawing-
quality verdict.  This module exposes the independently observable terms without composing
them into another blessed scalar:

- completeness follows recognition-owned physical requirements to compiler outcomes;
- legibility contains only placement/layout diagnostics;
- restraint fails closed until measurement provenance can classify every annotation.

Completeness is deliberately marked partial.  Only the feature families with a semantic
``*_outcomes`` ledger participate today; warning counts and declared IR are never substituted
for the missing physical denominator.
"""

from __future__ import annotations

from collections import Counter

from draftwright.linting.channel_coverage import channel_requirement_outcomes
from draftwright.linting.flat_coverage import flat_requirement_outcomes
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

# Codes whose meaning is unambiguously "a valid annotation candidate did not land". The two
# historically ambiguous codes (``gdt_dropped`` and ``pmi_dropped``) are deliberately absent;
# their producers attach ``outcome_stage`` so validation and placement are separable.
_PLACEMENT_DROP_CODES = frozenset(
    {
        "balloon_dropped",
        "boss_dia_dropped",
        "callout_dropped",
        "chamfer_dropped",
        "channel_width_dropped",
        "diameter_dropped",
        "dimension_dropped",
        "fillet_dropped",
        "flat_dropped",
        "groove_dropped",
        "location_ref_dropped",
        "off_axis_location_dropped",
        "pad_dim_dropped",
        "plate_thickness_dropped",
        "pocket_dim_dropped",
        "pocket_dropped",
        "polygonal_boss_dropped",
        "polygonal_stock_dropped",
        "polygonal_stock_length_dropped",
        "slot_dim_dropped",
        "slot_dropped",
        "step_dim_dropped",
        "step_position_dropped",
        "table_dropped",
    }
)

# The two ambiguous codes, named rather than merely absent so the vocabulary ratchet can tell
# "deliberately classified as stage-carrying" from "nobody classified this at all".
_STAGE_CLASSIFIED_DROP_CODES = frozenset({"gdt_dropped", "pmi_dropped"})

# Recognition inventories that represent potentially dimension-bearing physical families.
_RECOGNISED_REQUIREMENT_FAMILIES = {
    "holes": "holes",
    "countersinks": "countersinks",
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
# same physical requirement a second time, and ``rotational`` is a classification flag rather
# than an inventory. Kept explicit rather than implied by absence so that a new
# ``RecognitionResult`` inventory cannot silently join the blind spot the completeness
# component exists to report (``tests/test_quality_components.py``).
_NON_REQUIREMENT_INVENTORIES = frozenset(
    {"cylinders", "plates", "risers", "rotational", "step_levels"}
)

_AUDITED_FAMILIES = ("channels", "flats", "polygonal_stock", "slot_patterns", "slots")


def _is_placement_drop(issue) -> bool:
    stage = getattr(issue, "outcome_stage", None)
    if stage is not None:
        return bool(stage == "placement")
    return issue.code in _PLACEMENT_DROP_CODES


def _is_legibility_issue(issue) -> bool:
    return issue.code in _LEGIBILITY_CODES or _is_placement_drop(issue)


def _issue_component(issues, *, error_penalty: float, warning_penalty: float) -> dict:
    errors = sum(issue.severity == "error" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    infos = sum(issue.severity == "info" for issue in issues)
    placement_drops = sum(_is_placement_drop(issue) for issue in issues)
    # "Place what fits" feature drops are intentionally info severity in lint, but they are
    # still lost annotations. Give each info-level drop the warning floor so legibility cannot
    # remain 1.0 after arbitrarily many dimensions disappear (#1127 adversarial review).
    info_drops = sum(issue.severity == "info" and _is_placement_drop(issue) for issue in issues)
    by_code = Counter(issue.code for issue in issues)
    return {
        "available": True,
        "score": max(
            0.0,
            1.0 - errors * error_penalty - (warnings + info_drops) * warning_penalty,
        ),
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "placement_drops": placement_drops,
        "by_code": dict(sorted(by_code.items())),
        "basis": "layout_issue_severity_with_drop_floor",
    }


def _empty_completeness(reason: str) -> dict:
    return {
        "available": False,
        "score": None,
        "scope": "audited_recognized_requirements",
        "coverage": "unavailable",
        "reason": reason,
        "denominator": "recognition",
        "audited_families": list(_AUDITED_FAMILIES),
        "unscored_recognized_families": [],
        "requirements": 0,
        **{state: 0 for state in _OUTCOME_STATES},
        "by_family": {},
    }


def _completeness_component(recognition, features, registry, omissions) -> dict:
    if recognition is None:
        return _empty_completeness("physical recognition inventory unavailable")

    outcomes = {
        "channels": channel_requirement_outcomes(recognition, features, registry, omissions),
        "flats": flat_requirement_outcomes(recognition, features, registry, omissions),
        "polygonal_stock": polygonal_stock_outcomes(recognition, features, registry, omissions),
        "slots": [],
        "slot_patterns": [],
    }
    for outcome in slot_requirement_outcomes(recognition, features, registry, omissions):
        outcomes["slot_patterns" if outcome.source_kind == "slot_pattern" else "slots"].append(
            outcome
        )

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
        reason = "score covers recognized requirements in audited families"
    elif unaudited:
        reason = "recognized requirements exist only in families without outcome ledgers"
    else:
        reason = "no auditable recognized requirements"
    return {
        # Conditional completeness, not physical recall: score the requirements this run
        # recognised AND for which Draftwright has a semantic outcome ledger. Missing
        # recognisers are outside the scope; recognised-but-unscored families remain explicit.
        "available": requirements > 0,
        "score": audited_score,
        "scope": "audited_recognized_requirements",
        "coverage": "partial",
        "reason": reason,
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
) -> dict:
    """Return independently usable drawing-quality observations.

    No composite score is returned.  In particular, an unavailable restraint component is
    data, not zero: treating unclassified annotations as redundant would recreate the false
    confidence this API is intended to remove.
    """

    legibility_issues = [issue for issue in issues if _is_legibility_issue(issue)]
    return {
        "completeness": _completeness_component(recognition, features, registry, omissions),
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
        ),
    }


__all__ = ["quality_components"]
