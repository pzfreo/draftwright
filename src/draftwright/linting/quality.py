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
        "annotation_ink_overlap",
        "detail_unplaceable",
        "dim_inside_part",
        "feature_leader_crossing",
        "feature_leader_fixed_ink_unverified",
        "label_centerline_overlap",
        "leader_crosses_silhouette",
        "leader_line_through_text",
        # The overall extents' own placement failure. Separate from `placement_unsatisfiable`
        # because that code is a required *scale* drop and reporting through it refused to
        # build drawings that previously built (#1216 review r9); the sheet is equally less
        # readable either way, so it scores the same.
        "overall_dim_withheld",
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
# until somebody notices (#1127 review). Codes that cannot be read off their suffix carry an
# explicit ``outcome_stage`` from their producers instead (see ``is_placement_drop``); there
# were two when this was written and there are fourteen now, enumerated in
# :data:`_STAGE_ROUTED_CODES` and :data:`_UNSCORED_CODES` — and the suffix shortcut turned
# out to be exactly as forgettable as the list it replaced, because nothing checked that the
# suffix still meant what it says.

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
#: Inventories that are not requirement families because of what they ARE — nested sub-records,
#: raw geometry, classification flags, evidence aggregated into another feature. Permanent by
#: design, not pending a decision.
_NON_REQUIREMENT_INVENTORIES = frozenset(
    {"countersinks", "cylinders", "plates", "risers", "rotational", "step_levels"}
)

#: Inventories the installed package proves and this consumer has not decided about, each with
#: the issue deciding it. Held SEPARATELY from the set above, because the two mean different
#: things: those will never be requirement families, these have simply not been ruled on, and
#: merging them would let an undecided family look settled (#1244).
#:
#: The effect on the score is deliberate and worth stating: a drawing that omits a recognised
#: passage, prismatic pocket or angled step scores complete today. That is a blind spot — the
#: register is what stops it being a silent one, and closing each issue closes the gap.
_UNDECIDED_INVENTORIES: dict[str, str] = {
    "angled_steps": "https://github.com/pzfreo/draftwright/issues/1247",
    "passages": "https://github.com/pzfreo/draftwright/issues/1245",
    "prismatic_pockets": "https://github.com/pzfreo/draftwright/issues/1246",
}

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
#: declaration: an absent hole gets a centre mark, and on the automatic path a ``⌀6 THRU``
#: leader as well (the authored path draws the mark alone — the check is
#: declaration-vs-geometry and never inspects what landed, so it fires either way); and a
#: gear feature gets a data table bound to it by provenance (``registry.names_for_feature``
#: — the reconciliation fails as ``gear_requirement_missing`` when there is not exactly
#: one). A table whose
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
        # An annotation claiming a measurement it does not render, and one claiming a
        # measurement the compiler never approved (#1217). Both are the sheet contradicting
        # its own provenance: the drawing asserts, through the seam coverage reads, that it
        # carries a value it demonstrably does not. Same shape as `label_vs_measured` — the
        # source contradicted is the compiled plan rather than the projected path.
        "claimed_value_absent",
        "claimed_measurement_not_compiled",
    }
)


#: Every lint code that scores on NO quality component, named so that a new one cannot
#: arrive unclassified (#1176 review r3).
#:
#: Nothing here is a bug by itself. Most are omissions — completeness's territory, and it
#: builds its ledger from requirement outcomes rather than from lint codes, so it never
#: reads this. What the register buys is the property the three sets could not have
#: separately: every code the engine emits is in fidelity, legibility, this, or
#: :data:`_STAGE_ROUTED_CODES`, or carries a ``*_dropped`` suffix that no producer overrides
#: with a validation stage — audited over the source by
#: ``tests/test_issue_1176_quality_fidelity.py``. A new truth-class code would otherwise
#: score as perfectly truthful and nothing would say so — the failure mode the
#: ``_FIDELITY_CODES`` note above admits to and, before this, only admitted to.
#:
#: Stated that carefully because the first version of this sentence claimed the union was
#: "exactly the set of codes the engine emits", which was 41 against 55 (#1176 review r5).
#:
#: Membership is not an endorsement: several of these arguably SHOULD score somewhere, and
#: this register is what makes that visible instead of implicit.
_UNSCORED_CODES = frozenset(
    {
        "authored_dim_degenerate",
        # A source relationship withheld because its geometry cannot prove a truthful
        # witness. Like the adjacent degenerate/unsupported cases, this is an explicit
        # omission rather than a false claim or a misplaced annotation (#1209).
        "authored_dim_source_unresolved",
        "authored_omission",
        "axial_length_missing",
        "boss_height_missing",
        # The `*_withheld` family (#1216), kept together and in place alphabetically. Each is a
        # claim the drawing declined to make because it could not make it honestly, and said
        # so: a jittered pattern pitch, a step height the page cannot carry, and an `n×` mark
        # whose collapsed members do not all carry the same band. Not a fidelity fault (nothing
        # false was printed) and not a legibility one (nothing was misplaced); an omission is
        # completeness's ledger, which builds from requirement outcomes rather than from lint.
        "collapsed_tolerance_withheld",
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
        # A SUMMARY of required placement failures the automatic path would otherwise return
        # while reporting success (#1250). Unscored because it double-counts by construction:
        # every loss it names is already reported by the `*_dropped` code that produced it, and
        # those score wherever they score. It prints nothing false and misplaces nothing; what
        # it adds is an error-severity verdict so `passed` cannot be true over a drawing the
        # engine would refuse if the same sheet were requested explicitly.
        "plan_incomplete",
        "pmi_not_extracted",
        "pmi_not_lowered",
        "pmi_not_rendered",
        "pmi_present_but_ignored",
        "step_dim_withheld",
        "pattern_pitch_tolerance_withheld",
        "pocket_not_located",
        "step_position_coincident_with_datum",
        # Neither confirmed nor refuted: the annotation renders no readable text, or the
        # compiler approved the measurement with no displayable value. Reported so an
        # unverifiable claim is visible rather than counted as a pass — but it is evidence
        # of nothing, so it scores nowhere (#1217).
        "claimed_representation_unreadable",
        "claimed_representation_no_expected_value",
        # A `_dropped` code that scores NOWHERE, which is why the suffix cannot be trusted
        # on its own. `is_placement_drop` consults `outcome_stage` FIRST and only falls back
        # to the suffix, and every `_skip_section` emission is `outcome_stage="validation"`
        # — a deliberate choice, because a placement stage would make an optional section's
        # absence a scale blocker. So the section-A–A loss reaches no component (#1176
        # review r4). `sections.py` said the opposite four lines above the call that makes
        # it false; that comment is corrected.
        "section_dropped",
        "profiled_bore_not_dimensioned",
        "unrecognised_defining_geometry",
    }
)

#: `_dropped` codes whose component depends on the ISSUE, not the code — emitted with
#: ``outcome_stage="placement"`` at one site and ``"validation"`` at another, so the same code
#: scores on legibility for one instance and on nothing for the next.
#:
#: Registered because the audit's "a `_dropped` suffix means legibility" shortcut is only
#: true where no site sets a validation stage, and that is an assumption about code the audit
#: can check rather than assume. It found `section_dropped` scoring nowhere while a comment
#: beside its emission asserted the opposite.
#:
#: Eleven of these — every entry except `gdt_dropped` and `pmi_dropped` — are codes handed
#: to a leader job as ``drop_code`` data: `leaders.py`'s one drop recorder stages them
#: ``"validation"`` on a rendered-geometry failure and ``"placement"`` otherwise, so all
#: eleven have the `section_dropped` shape. TEN of them were invisible to the first audit,
#: which read only codes written as literals AT a producer call, and were new to these
#: registers; `callout_dropped` is the eleventh and was neither — it is also written as a
#: literal at three producer calls, so the audit always saw it (#1176 review r5, corrected
#: twice: the first note said "the eleven below `pmi_dropped`", which is ten, and the second
#: still said all eleven had been invisible).
_STAGE_ROUTED_CODES = frozenset(
    {
        "callout_dropped",
        "gdt_dropped",
        "pmi_dropped",
        "boss_dia_dropped",
        "chamfer_dropped",
        "diameter_dropped",
        "fillet_dropped",
        "flat_dropped",
        "groove_dropped",
        "pocket_dropped",
        "polygonal_boss_dropped",
        "polygonal_stock_dropped",
        "slot_dropped",
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


def _is_unscored_issue(issue) -> bool:
    return not _is_legibility_issue(issue) and not _is_fidelity_issue(issue)


def _unscored_component(issues) -> dict:
    """The findings NO quality component scored, as data (#1176 review r4).

    Reported for the same reason completeness reports ``excludes``: a caller reading four
    components all saying "fine" would otherwise have no way to see that a third of this
    drawing's findings reached none of them. Twenty-two of the engine's codes score nowhere
    unconditionally, and another thirteen do whenever their producer stages them, and
    before this the only record of that was a comment.

    ``unclassified`` is the fail-open signal made visible. A code here that is not in
    :data:`_UNSCORED_CODES` reached no component AND nobody decided it should — which is
    precisely how a new truth-class code would be silently reported as perfectly truthful.
    The audit in ``tests/test_issue_1176_quality_fidelity.py`` catches that statically over
    every code the engine can emit; this catches it at runtime, over the ones it just did.
    """
    unscored = [issue for issue in issues if _is_unscored_issue(issue)]
    by_code = Counter(issue.code for issue in unscored)
    return {
        # `available`/`score` so a caller can walk `quality.values()` uniformly. This is an
        # inventory, not a fifth axis: it is always computable, and it is deliberately not a
        # number — pricing "how much went unscored" would be a fifth score nobody asked for
        # (#1176 review r5, which found `for c in quality.values(): c["available"]` raising).
        "available": True,
        "score": None,
        "issues": len(unscored),
        "by_code": dict(sorted(by_code.items())),
        "unclassified": sorted(
            code
            for code in by_code
            if code not in _UNSCORED_CODES
            and not code.startswith(_UNSCORED_CODE_PREFIXES)
            and code not in _STAGE_ROUTED_CODES
        ),
        "reason": "reported by lint and deliberately scored on no quality component",
    }


def _is_legibility_issue(issue) -> bool:
    """Whether *issue* is a layout defect this component should price.

    Fidelity codes are excluded OUTRIGHT rather than merely being absent from
    :data:`_LEGIBILITY_CODES`. `is_placement_drop` accepts any issue carrying
    ``outcome_stage="placement"`` whatever its code, so a truth defect that also reported a
    placement outcome would be penalised on both axes and make two independent observations
    look correlated. Comparing the two SETS cannot prevent that, and the guard that claimed
    to was inert because its probe left `outcome_stage` at its `None` default (#1176 review
    r4). The precedence is stated here instead, where it is a fact about the code rather
    than about the registers.
    """
    if _is_fidelity_issue(issue):
        return False
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
    # a detected falsehood (#1176 review r3).
    #
    # A FAIL-SAFE, not a live branch: once the predicate covers the checks' domains, an issue
    # cannot arise while it is False, so no drawing reaches this line with work to do. Kept
    # because the predicate has drifted from those domains three times in this issue's
    # review history, and this is the difference between a drift that misreports and a drift
    # that loses a finding. Guarded by a direct test of this function rather than through
    # `lint_summary()` for exactly that reason — a consumer-level test of it would have to
    # reproduce the drift it exists to survive (#1176 review r5).
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
            unavailable_reason=(
                "the drawing states nothing this axis can check: no measured quantity is "
                "drawn and no feature is declared"
            ),
        ),
        "unscored": _unscored_component(issues),
    }


__all__ = ["quality_components"]
