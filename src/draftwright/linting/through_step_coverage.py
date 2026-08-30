"""Semantic completeness for recognised rectangular through-step requirements (#1382).

Each aggregate ``ThroughStep`` owns the two orthogonal legs of its canonical open section.
The provider's axis, run length, anchor and complete section join that record to exactly one
IR feature; parameter identities then follow each leg to its drawing outcome.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from b123d_recognisers import RecognitionResult, ThroughStep

from draftwright._geometry import _fmt
from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.evidence import compiled_values, rendered_numbers
from draftwright.linting.issues import LintIssue, is_placement_drop
from draftwright.linting.structural import _label_reading

ThroughStepRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
    "inapplicable",
]


@dataclass(frozen=True)
class ThroughStepRequirementOutcome:
    """The observable engine outcome of one open-section leg requirement."""

    source_at: tuple[float, float, float]
    parameter_id: str
    state: ThroughStepRequirementState
    requirement_count: int = 1
    features: tuple = ()


@dataclass(frozen=True)
class _LegacyTerm:
    """One exact member of a legacy dimension grammar.

    ``DimensionId`` deliberately addresses an entire correlated ladder, so it cannot by
    itself distinguish (for example) a 10 mm step rung from a 15 mm rung.  The physical
    interval retained here selects the exact compiled member and prevents another member of
    the same ladder from receiving credit for a through-step leg.
    """

    feature: object
    parameter_id: str
    axis: str
    lo: float
    hi: float

    @property
    def identity(self) -> tuple[object, str]:
        return (self.feature, self.parameter_id)

    @property
    def value(self) -> float:
        return abs(self.hi - self.lo)


def _rounded(value) -> float:
    try:
        return round(float(value), 3)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError from error


def _point(value) -> tuple[float, ...]:
    return tuple(_rounded(coordinate) for coordinate in value)


def through_step_key(step) -> tuple:
    """Facts retained identically by the public record and Draftwright IR."""
    at = getattr(step, "at", None)
    if at is None:
        at = step.frame.origin
    return (
        str(step.axis),
        _point(at),
        _rounded(step.length),
        tuple(_point(point) for point in step.section),
    )


def _source_at(source) -> tuple[float, float, float]:
    try:
        point = _point(source.at)
        if len(point) != 3:
            raise ValueError
        return (point[0], point[1], point[2])
    except (AttributeError, OverflowError, TypeError, ValueError):
        return (float("nan"), float("nan"), float("nan"))


def _parameter_ids(source) -> tuple[str, str]:
    transverse = tuple(axis for axis in "xyz" if axis != source.axis)
    raw_section = tuple(tuple(point) for point in source.section)
    raw_at = tuple(source.at)
    if (
        isinstance(source.length, bool)
        or any(isinstance(value, bool) for value in raw_at)
        or any(isinstance(value, bool) for point in raw_section for value in point)
    ):
        raise ValueError
    section = tuple(tuple(float(value) for value in point) for point in raw_section)
    at = tuple(float(value) for value in raw_at)
    if (
        source.axis not in ("x", "y", "z")
        or len(at) != 3
        or any(not math.isfinite(value) for value in at)
        or len(section) != 3
        or any(len(point) != 2 for point in section)
        or not math.isfinite(float(source.length))
        or float(source.length) <= 0
        or any(not math.isfinite(value) for point in section for value in point)
    ):
        raise ValueError
    ids = []
    for start, end in zip(section, section[1:]):
        changed = [index for index in (0, 1) if start[index] != end[index]]
        if len(changed) != 1:
            raise ValueError
        ids.append(f"through_step_leg.length.{transverse[changed[0]]}")
    if len(ids) != 2 or ids[0] == ids[1]:
        raise ValueError
    return (ids[0], ids[1])


def _source_leg_intervals(source) -> tuple[tuple[str, float, float], ...]:
    transverse = tuple(axis for axis in "xyz" if axis != source.axis)
    intervals = []
    for start, end in zip(source.section, source.section[1:]):
        changed = next(index for index in (0, 1) if start[index] != end[index])
        lo, hi = sorted((float(start[changed]), float(end[changed])))
        intervals.append((transverse[changed], lo, hi))
    return tuple(intervals)


def _legacy_owner_plans(source, features) -> dict[str, list[tuple[_LegacyTerm, ...]]]:
    """Alternative measurement identities that prove each physical source leg."""
    parameters = _parameter_ids(source)
    legs = dict(zip(parameters, _source_leg_intervals(source), strict=True))
    plans: dict[str, list[tuple[_LegacyTerm, ...]]] = {parameter: [] for parameter in parameters}
    envelope = next(
        (candidate for candidate in features if getattr(candidate, "kind", None) == "envelope"),
        None,
    )
    envelope_parameters = {"x": "width.length", "y": "depth.length", "z": "height.length"}
    maxima = dict(zip("xyz", envelope.bbox_max, strict=True)) if envelope is not None else {}

    def _matches(owner, leg) -> bool:
        owner_axis, owner_lo, owner_hi = owner
        axis, lo, hi = leg
        return bool(owner_axis == axis and abs(owner_lo - lo) < 0.5 and abs(owner_hi - hi) < 0.5)

    for feature in features:
        kind = getattr(feature, "kind", None)
        if kind == "plate":
            owner = (feature.axis, *sorted((feature.lo, feature.hi)))
            for parameter, leg in legs.items():
                if _matches(owner, leg):
                    plans[parameter].append((_LegacyTerm(feature, "thickness.length", *owner),))
                if envelope is not None:
                    axis = feature.axis
                    bound_lo = envelope.bbox_min["xyz".index(axis)]
                    bound_hi = envelope.bbox_max["xyz".index(axis)]
                    plate_lo, plate_hi = owner[1:]
                    complements = []
                    if abs(plate_lo - bound_lo) < 0.5:
                        complements.append((axis, plate_hi, bound_hi))
                    if abs(plate_hi - bound_hi) < 0.5:
                        complements.append((axis, bound_lo, plate_lo))
                    if any(_matches(complement, leg) for complement in complements):
                        plans[parameter].append(
                            (
                                _LegacyTerm(feature, "thickness.length", *owner),
                                _LegacyTerm(
                                    envelope,
                                    envelope_parameters[axis],
                                    axis,
                                    bound_lo,
                                    bound_hi,
                                ),
                            )
                        )
        elif kind == "step_level":
            for level in feature.levels:
                direct = ("z", *sorted((feature.base, level)))
                complement = (
                    ("z", *sorted((level, maxima["z"])))
                    if "z" in maxima
                    and envelope is not None
                    and abs(feature.base - envelope.bbox_min[2]) < 0.5
                    else None
                )
                for parameter, leg in legs.items():
                    if _matches(direct, leg):
                        plans[parameter].append(
                            (_LegacyTerm(feature, "step_height.length", *direct),)
                        )
                    if complement is not None and _matches(complement, leg):
                        assert envelope is not None
                        plans[parameter].append(
                            (
                                _LegacyTerm(feature, "step_height.length", *direct),
                                _LegacyTerm(
                                    envelope,
                                    envelope_parameters["z"],
                                    "z",
                                    envelope.bbox_min[2],
                                    envelope.bbox_max[2],
                                ),
                            )
                        )
            for axis, position in feature.shoulders:
                datum = feature.datum["xyz".index(axis)]
                direct = (axis, *sorted((datum, position)))
                complement = (
                    (axis, *sorted((position, maxima[axis])))
                    if axis in maxima
                    and envelope is not None
                    and abs(datum - envelope.bbox_min["xyz".index(axis)]) < 0.5
                    else None
                )
                for parameter, leg in legs.items():
                    if _matches(direct, leg):
                        plans[parameter].append(
                            (_LegacyTerm(feature, "step_position.length", *direct),)
                        )
                    if complement is not None and _matches(complement, leg):
                        assert envelope is not None
                        plans[parameter].append(
                            (
                                _LegacyTerm(feature, "step_position.length", *direct),
                                _LegacyTerm(
                                    envelope,
                                    envelope_parameters[axis],
                                    axis,
                                    envelope.bbox_min["xyz".index(axis)],
                                    envelope.bbox_max["xyz".index(axis)],
                                ),
                            )
                        )
    return plans


def _legacy_plan_signature(plans) -> tuple:
    """Hashable identity of the alternate owners available to one source record.

    Legacy correlation currently proves section intervals, not the recogniser record's run
    anchor.  Two records that resolve to this same signature are therefore ambiguous: giving
    either one the legacy ink would necessarily give both the same ink.  Fail closed rather
    than count one owner twice.
    """
    return tuple(
        (
            parameter,
            tuple(
                tuple(
                    (term.identity, term.axis, _rounded(term.lo), _rounded(term.hi))
                    for term in alternative
                )
                for alternative in alternatives
            ),
        )
        for parameter, alternatives in sorted(plans.items())
    )


def _has_parameters(feature, expected: tuple[str, str]) -> bool:
    try:
        return tuple(parameter.parameter_id for parameter in feature.parameters()) == expected
    except (AttributeError, TypeError):
        return False


def _interval_matches(term: _LegacyTerm, span) -> bool:
    try:
        if span is None or len(span) != 2:
            return False
        raw_points = tuple(tuple(point) for point in span)
        if any(len(point) != 3 for point in raw_points):
            return False
        if any(isinstance(value, bool) for point in raw_points for value in point):
            return False
        points = tuple(tuple(float(value) for value in point) for point in raw_points)
    except (OverflowError, TypeError, ValueError):
        return False
    if any(not math.isfinite(value) for point in points for value in point):
        return False
    index = "xyz".index(term.axis)
    lo, hi = sorted((points[0][index], points[1][index]))
    return abs(lo - term.lo) < 0.5 and abs(hi - term.hi) < 0.5


def _span_matches(term: _LegacyTerm, approved) -> bool:
    return _interval_matches(term, getattr(approved, "span", None))


def _term_values(term: _LegacyTerm, approved_by_id) -> frozenset[float]:
    """Compiler-approved labels for the exact correlated member named by *term*."""
    entries = (
        tuple(
            entry for entry in approved_by_id.get(term.identity, ()) if _span_matches(term, entry)
        )
        if approved_by_id is not None
        else ()
    )
    values = set()
    for entry in entries:
        try:
            value_text = entry.value_text
            if not isinstance(value_text, str):
                continue
            value = float(value_text)
            if not math.isfinite(value):
                continue
            values.add(value)
        except (AttributeError, OverflowError, TypeError, ValueError):
            continue
    # Direct helper callers do not necessarily have a compiled plan.  Default formatting is
    # still a stricter fallback than accepting any member carrying the shared DimensionId.
    # Once a compiler plan is supplied, however, it is the content authority: an absent exact
    # occurrence (same correlated id, wrong span) or malformed approved text is no evidence and
    # must fail closed rather than be reconstructed from geometry (ADRs 0015/0016/0017).
    if approved_by_id is None:
        values.add(float(_fmt(term.value)))
    return frozenset(values)


def _term_is_asserted(term, asserted, rendered, approved_by_id) -> bool:
    if term.identity not in asserted:
        return False
    expected = _term_values(term, approved_by_id)
    actual = rendered.get(term.identity, ())
    return any(
        abs(want - number) <= 1e-6 and _interval_matches(term, span)
        for want in expected
        for number, span in actual
    )


def _term_is_dropped(term, dropped) -> bool:
    return any(_interval_matches(term, span) for span in dropped.get(term.identity, ()))


def _alternative_state(plans, placed, satisfied, dropped, suppressed, rendered, approved_by_id):
    """Outcome of one physical leg expressed by an alternate dimension grammar."""
    asserted = placed | satisfied
    for plan in plans:
        if all(_term_is_asserted(term, asserted, rendered, approved_by_id) for term in plan):
            return "inapplicable"
    for plan in plans:
        if all(
            _term_is_asserted(term, asserted, rendered, approved_by_id)
            or term.identity in suppressed
            for term in plan
        ) and any(term.identity in suppressed for term in plan):
            return "suppressed"
    for plan in plans:
        if all(
            _term_is_asserted(term, asserted, rendered, approved_by_id)
            or _term_is_dropped(term, dropped)
            for term in plan
        ) and any(_term_is_dropped(term, dropped) for term in plan):
            return "dropped"
    return "missing"


def _index_evidence(registry):
    placed = {
        (measurement.feature, measurement.parameter)
        for name in registry.names()
        for measurement in registry.measurement_of(name)
    }
    satisfied = {
        (identity.feature, identity.parameter)
        for identity in satisfaction_ids(registry)
        if identity.feature is not None and isinstance(identity.parameter, str)
    }
    dropped: dict[tuple[object, str], list] = defaultdict(list)
    for issue in registry.issues:
        # ``detail_unplaceable`` is an optional recovery-view outcome, not a sheet/scale
        # placement blocker. It nevertheless proves that the exact compiler rungs carried
        # on the request were not recovered. Consume that narrow, provenance-bearing outcome
        # here without globally reclassifying optional detail furniture as required ink.
        exact_detail_failure = bool(
            issue.code == "detail_unplaceable"
            and getattr(issue, "measurement_ids", ())
            and getattr(issue, "measurement_spans", ())
        )
        if not is_placement_drop(issue) and not exact_detail_failure:
            continue
        spans = getattr(issue, "measurement_spans", ())
        for index, measurement in enumerate(getattr(issue, "measurement_ids", ())):
            if getattr(measurement, "feature", None) is None or not isinstance(
                getattr(measurement, "parameter", None), str
            ):
                continue
            dropped.setdefault((measurement.feature, measurement.parameter), [])
            if index < len(spans):
                dropped[(measurement.feature, measurement.parameter)].append(spans[index])
    rendered: dict[tuple[object, str], list[tuple[float, object]]] = defaultdict(list)
    for name in registry.names():
        annotation = registry.named(name)
        label = str(getattr(annotation, "label", "") or "")
        primary = _label_reading(annotation, label) if label else None
        for identity in registry.measurement_of(name):
            if getattr(identity, "feature", None) is None or not isinstance(
                getattr(identity, "parameter", None), str
            ):
                continue
            if primary is not None:
                rendered[(identity.feature, identity.parameter)].append(
                    (primary, getattr(annotation, "_dw_measurement_span", None))
                )
        # A structured note may legitimately carry several measurements in one compound
        # label, unlike a linear Dimension whose primary reading is singular.
        numbers = rendered_numbers(annotation)
        if numbers is None:
            continue
        for identity in satisfaction_of(registry, name):
            if getattr(identity, "feature", None) is None or not isinstance(
                getattr(identity, "parameter", None), str
            ):
                continue
            rendered[(identity.feature, identity.parameter)].extend(
                (number, getattr(annotation, "_dw_measurement_span", None)) for number in numbers
            )
    return placed, satisfied, dict(dropped), dict(rendered)


def through_step_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    *,
    plan=None,
) -> list[ThroughStepRequirementOutcome]:
    """Follow both leg requirements of every recognised through step."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "through_step_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources = tuple(recognition.through_steps)
    if not sources:
        return []

    keyed_sources: list[tuple[ThroughStep, tuple | None, tuple[str, str] | None]] = []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        key: tuple | None
        record_parameters: tuple[str, str] | None
        try:
            key = through_step_key(source)
            record_parameters = _parameter_ids(source)
        except (AttributeError, OverflowError, TypeError, ValueError):
            key = None
            record_parameters = None
        keyed_sources.append((source, key, record_parameters))
        if key is not None:
            source_counts[key] += 1

    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "through_step":
            continue
        try:
            ir_by_key[through_step_key(feature)].append(feature)
        except (AttributeError, OverflowError, TypeError, ValueError):
            continue

    placed, satisfied, dropped, rendered = _index_evidence(registry)
    approved = compiled_values(plan) if plan is not None else {}
    approved_by_id = (
        {
            (identity.feature, identity.parameter): entries
            for identity, entries in approved.items()
            if identity is not None
        }
        if plan is not None
        else None
    )
    # A declared model may omit the envelope from ``model.features`` while the compiler
    # synthesizes and approves it for the ordinary overall dimensions.  Complement proofs
    # (step/plate + envelope) must use that actual compiler owner, not invent a parallel bbox
    # identity and not fall back to every declared parameter.
    owner_features = tuple(
        dict.fromkeys(
            (
                *features,
                *(
                    identity.feature
                    for identity in approved
                    if identity is not None
                    and getattr(identity.feature, "kind", None)
                    in {"envelope", "plate", "step_level"}
                ),
            )
        )
    )
    # Build alternate-owner correspondence once for the whole inventory.  Uniqueness must
    # be judged on the legacy proof itself, not on the richer recogniser record key: legacy
    # dimensions encode the section intervals but not necessarily the run anchor, so two
    # differently anchored sources can otherwise reuse the same two pieces of ink.
    legacy_plans: dict[int, dict[str, list[tuple[_LegacyTerm, ...]]]] = {}
    legacy_signature_counts: dict[tuple, int] = defaultdict(int)
    for index, (source, _key, record_parameters) in enumerate(keyed_sources):
        if record_parameters is None or source.axis not in ("x", "y"):
            continue
        candidate = _legacy_owner_plans(source, owner_features)
        if not all(candidate.values()):
            continue
        legacy_plans[index] = candidate
        legacy_signature_counts[_legacy_plan_signature(candidate)] += 1
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[ThroughStepRequirementOutcome] = []
    fallback = ("through_step_leg.length.unknown.0", "through_step_leg.length.unknown.1")
    for index, (source, key, record_parameters) in enumerate(keyed_sources):
        matches = ir_by_key.get(key, ()) if key is not None else ()
        feature = (
            matches[0] if key is not None and len(matches) == source_counts[key] == 1 else None
        )
        expected = record_parameters or fallback
        if feature is None and index in legacy_plans:
            plans = legacy_plans[index]
            if legacy_signature_counts[_legacy_plan_signature(plans)] == 1:
                outcomes.extend(
                    ThroughStepRequirementOutcome(
                        _source_at(source),
                        parameter,
                        _alternative_state(
                            plans[parameter],
                            placed,
                            satisfied,
                            dropped,
                            suppressed,
                            rendered,
                            approved_by_id,
                        ),
                    )
                    for parameter in expected
                )
                continue
        if (
            feature is None
            or record_parameters is None
            or not _has_parameters(feature, record_parameters)
        ):
            outcomes.extend(
                ThroughStepRequirementOutcome(_source_at(source), parameter, "unverifiable")
                for parameter in expected
            )
            continue
        for parameter in record_parameters:
            identity = (feature, parameter)
            if identity in placed:
                state: ThroughStepRequirementState = "placed"
            elif identity in satisfied:
                state = "satisfied_by_structured_note"
            elif identity in suppressed:
                state = "suppressed"
            elif identity in dropped:
                state = "dropped"
            else:
                associated = registry.names_for_feature(feature)
                state = (
                    "unverifiable"
                    if any(
                        not registry.measurement_of(name) and not satisfaction_of(registry, name)
                        for name in associated
                    )
                    else "missing"
                )
            outcomes.append(
                ThroughStepRequirementOutcome(
                    _source_at(source), parameter, state, features=(feature,)
                )
            )
    return outcomes


def lint_through_step_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
    plan=None,
) -> list[LintIssue]:
    """Report uncovered through-step legs without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped dimension outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in through_step_requirement_outcomes(
        recognition,
        features,
        registry,
        omissions,
        plan=plan,
    ):
        if outcome.state in {
            "placed",
            "satisfied_by_structured_note",
            "dropped",
            "inapplicable",
        }:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"through_step_requirement_{outcome.state}",
                message=(
                    f"through-step {outcome.parameter_id} at {outcome.source_at} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
