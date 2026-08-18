"""Separable, honest drawing-quality components (#1127).

The legacy lint ``score`` is a severity-weighted diagnostic convenience, not a drawing-
quality verdict.  This module exposes the independently observable terms without composing
them into another blessed scalar:

- completeness follows recognition-owned physical requirements to compiler outcomes;
- fidelity asks whether the content the drawing DOES carry is contradicted by the geometry
  it is drawn from — a drawing can be complete, restrained and legible and still assert a
  measurement, a callout or a data-table value the part does not have (#1176);
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
from draftwright.linting.issues import LintIssue, is_placement_drop
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
        "feature_leader_crossing",
        "feature_leader_fixed_ink_unverified",
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
# ``outcome_stage`` from their producers instead; see ``is_placement_drop``.

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

#: Codes meaning the drawing ASSERTS SOMETHING FALSE — not that it is incomplete, hard to
#: read, or over-dimensioned, but that a reader following it would be misled.
#:
#: This is a fourth axis, and its absence was a hole the other three could not cover. A
#: drawing labelled 99 over a 16 mm path — a 518% contradiction — reported
#: ``legibility: 1.0``, because legibility scores LAYOUT and a false dimension is perfectly
#: legible. With completeness and restraint unavailable on that drawing, every component a
#: caller could read said perfect (#1176).
#:
#: The first cut of this set held ``label_vs_measured`` alone and claimed it was "currently
#: the only such code". That was false, and the two reproductions are worse than the case
#: it was built for:
#:
#: * ``declared_feature_absent`` — a ``⌀6 THRU`` leader and a centre mark drawn pointing at
#:   SOLID MATERIAL, because the declared hole is not in the part. The sheet tells a
#:   machinist to drill something the model does not have.
#: * ``gear_repeat_count_mismatch`` — a normative ISO data table stating 12 teeth on a part
#:   the linter independently proves has 13. The wrong number is printed on the sheet.
#:
#: ``gear_axis_mismatch`` and ``gear_requirement_mismatch`` (a table preserving stale
#: authored values) join them, and the membership rule has to be stated carefully because
#: three of the five are *declaration*-vs-geometry checks that never inspect what was
#: placed. What makes them belong is that the drawing does place something FOR the
#: declaration: an absent hole gets its ``⌀6 THRU`` leader, and a gear feature gets a data
#: table bound to it by provenance (``registry.names_for_feature`` — the reconciliation
#: fails as ``gear_requirement_missing`` when there is not exactly one). A table whose
#: declared axis the geometry contradicts is describing a feature the part does not have
#: there, exactly as the leader is. That the axis has no printed row does not make the table
#: silent about it, any more than the leader prints the hole's coordinates.
#:
#: Hence the basis is "contradicted by its SOURCE", not "by the geometry": the source is the
#: part for four of the five, and the authored requirement for ``gear_requirement_mismatch``,
#: where the sheet's table has drifted from the values it claims to carry.
#:
#: What stays OUT is content that was not drawn — ``dimension_kind_unsupported``, the
#: ``*_dropped`` family, the ``*_missing`` family. An omission is a gap, not a lie. Note
#: this is a statement about what fidelity means, not about where those codes go instead:
#: most of them score on NO component at all (see :func:`quality_components`).
#:
#: Hand-maintained, and that is a known weakness — nine lines below,
#: :data:`_LEGIBILITY_CODES` argues for a suffix rule precisely because "a list has to be
#: remembered". There is no suffix that means "false", so this list must be. What keeps it
#: from rotting silently is :data:`_UNSCORED_CODES`: every code the engine emits is in
#: exactly one of the three, and a new one fails the audit until somebody classifies it.
_FIDELITY_CODES = frozenset(
    {
        "label_vs_measured",
        "declared_feature_absent",
        "gear_repeat_count_mismatch",
        "gear_axis_mismatch",
        "gear_requirement_mismatch",
    }
)


#: Every lint code that scores on NO quality component, named so that a new one cannot
#: arrive unclassified (#1176 review r3).
#:
#: Nothing here is a bug by itself. Most are omissions — completeness's territory, and it
#: builds its ledger from requirement outcomes rather than from lint codes, so it never
#: reads this. What the register buys is the property the three sets could not have
#: separately: the union of fidelity, legibility and this is exactly the set of codes the
#: engine emits, audited by ``tests/test_issue_1176_quality_fidelity.py``. A new truth-class
#: code would otherwise score as perfectly truthful and nothing would say so — the failure
#: mode the ``_FIDELITY_CODES`` note above admits to and, before this, only admitted to.
#:
#: Membership is not an endorsement: several of these arguably SHOULD score somewhere, and
#: this register is what makes that visible instead of implicit.
_UNSCORED_CODES = frozenset(
    {
        "authored_dim_degenerate",
        "authored_omission",
        "axial_length_missing",
        "boss_height_missing",
        "dimension_kind_unsupported",
        "feature_count_mismatch",
        "feature_no_centermark",
        "feature_not_dimensioned",
        "feature_not_located",
        "gdt_side_relaxed",
        "gear_correspondence_unverifiable",
        "gear_semantics_missing",
        "missing_principal_dimension",
        "pad_footprint_not_defined",
        "pmi_not_extracted",
        "pmi_not_lowered",
        "pmi_not_rendered",
        "pmi_present_but_ignored",
        "pocket_not_located",
        "profiled_bore_not_dimensioned",
        "unrecognised_defining_geometry",
    }
)

#: Code FAMILIES built by interpolating a requirement state (``hole_requirement_missing``,
#: ``…_suppressed``, ``…_unverifiable``). Registered by prefix because the state comes from a
#: `Literal` alias rather than a literal at the call site.
#:
#: Coarser than the sets above, deliberately: ``gear_requirement_`` appears here AND
#: ``gear_requirement_mismatch`` is a fidelity code. The audit classifies every literal code
#: individually, so the prefix register governs only the interpolating sites — it can never
#: reclassify a code somebody spelled out.
_UNSCORED_CODE_PREFIXES = (
    "channel_requirement_",
    "flat_requirement_",
    "gear_requirement_",
    "hole_requirement_",
    "polygonal_stock_requirement_",
    "slot_requirement_",
)


def _is_fidelity_issue(issue) -> bool:
    return issue.code in _FIDELITY_CODES


def _is_legibility_issue(issue) -> bool:
    return issue.code in _LEGIBILITY_CODES or is_placement_drop(issue)


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
    issues,
    *,
    error_penalty: float,
    warning_penalty: float,
    aggregation=None,
    basis: str = "layout_issue_severity_with_info_floor",
    available: bool = True,
    unavailable_reason: str | None = None,
) -> dict:
    # Compatibility fields remain raw lint-finding counts. Only the scalar penalty uses the
    # explicitly grouped primary inventory; both inventories are exposed and documented.
    #
    # An issue OUTRANKS the availability argument. Unavailability means "no evidence either
    # way", so a component that HAS a finding is by definition available — and the caller's
    # predicate is an approximation of the checks' domains, while the finding is the check
    # having fired. The first cut let the argument win, and a `declared_feature_absent` on a
    # 20 mm box was reported as `{available: False, score: None}`: the gate fell closed over
    # a detected falsehood, which is worse than the fail-open it was added to remove (#1176
    # review r3).
    available = available or bool(issues)
    errors = sum(issue.severity == "error" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    infos = sum(issue.severity == "info" for issue in issues)
    placement_drops = sum(is_placement_drop(issue) for issue in issues)
    by_code = Counter(issue.code for issue in issues)
    primary = _primary_issues(issues, aggregation)
    primary_errors = sum(issue.severity == "error" for issue in primary)
    primary_warnings = sum(issue.severity == "warning" for issue in primary)
    primary_infos = sum(issue.severity == "info" for issue in primary)
    primary_by_code = Counter(issue.code for issue in primary)
    component = {
        "available": True,
        # Info severity takes the WARNING floor: an issue reaching here is a defect in the
        # axis being scored, and the component cannot report 1.0 while itemising a finding it
        # has just made. Lint uses `info` for "place what fits" drops and readability faults
        # like a leader crossing a silhouette (#1127), and — since #1176 — for every gear
        # reconciliation in an ASSEMBLY context (`gear_coverage.py`), where a data table
        # contradicting the geometry is no less false for the part having siblings.
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
        "basis": basis,
        "score_inventory": "primary_issues",
    }
    if available:
        return component
    # Same contract as an unavailable completeness or restraint component: no evidence is
    # DATA, not a perfect score. Fidelity affirming "nothing false was said" over a drawing
    # that says nothing is the fail-open this module's own docstring argues against — and
    # unlike legibility, where an empty sheet genuinely is legible, the truthfulness of an
    # empty utterance is not the same kind of 1.0.
    #
    # The SHAPE is kept whole, unlike completeness's `_empty_completeness`, because this one
    # is the second dict under a key whose first (`legibility`) is always available: a caller
    # that reads `quality["fidelity"]["by_code"]` the way README reads legibility's must get
    # an empty mapping, not a `KeyError`. Every count is genuinely zero here — an issue would
    # have made the component available two lines above.
    return {**component, "available": False, "score": None, "reason": unavailable_reason}


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
    has_asserted_content: bool,
    _aggregation=None,
) -> dict:
    """Return independently usable drawing-quality observations.

    No composite score is returned.  In particular, an unavailable restraint component is
    data, not zero: treating unclassified annotations as redundant would recreate the false
    confidence this API is intended to remove.  For the same reason completeness reports an
    ``audited_score`` over a stated denominator rather than a ``score``, and lists what that
    denominator ``excludes`` — a feature recognition never identified is absent from the
    ledger entirely, so a perfect audited score is not a complete drawing.

    The four axes answer different questions and none substitutes for another:
    completeness asks whether required content landed, restraint whether there is too much
    of it, legibility whether a reader can make it out, and **fidelity whether what it says
    is true**. A drawing can be complete, restrained and legible while asserting a
    measurement the part does not have, and before fidelity existed that combination scored
    perfect on the only component available (#1176).

    Legibility's ``errors``/``warnings``/``infos``/``by_code`` fields retain their original
    raw-finding semantics. ``primary_*`` fields describe the inventory used by ``score``;
    ``affected_pairs`` is the number of raw pair findings that opted into that aggregation.
    Full pair messages remain in :meth:`Drawing.lint` and ``lint_summary()['issues']``.
    """

    legibility_issues = [issue for issue in issues if _is_legibility_issue(issue)]
    fidelity_issues = [issue for issue in issues if _is_fidelity_issue(issue)]
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
        "fidelity": _issue_component(
            fidelity_issues,
            error_penalty=error_penalty,
            warning_penalty=warning_penalty,
            aggregation=_aggregation,
            basis="drawn_assertion_contradicted_by_its_source",
            available=has_asserted_content,
            unavailable_reason="the drawing asserts no measurable content",
        ),
    }


__all__ = ["quality_components"]
