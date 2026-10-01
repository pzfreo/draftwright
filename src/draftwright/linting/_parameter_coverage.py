"""Shared recognition-to-outcome driver for fixed-parameter physical families."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Literal, Protocol, TypeVar

from quiddity import RecognitionResult

from draftwright.linting._coverage_common import index_evidence, state
from draftwright.linting._registry import with_measurement_carriers
from draftwright.linting.issues import LintIssue

ParameterRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]


class _ParameterOutcome(Protocol):
    @property
    def state(self) -> ParameterRequirementState: ...


_Outcome = TypeVar("_Outcome", bound=_ParameterOutcome)


def _has_parameters(
    feature, expected: tuple[str, ...], errors: tuple[type[Exception], ...]
) -> bool:
    try:
        return tuple(parameter.parameter_id for parameter in feature.parameters()) == expected
    except errors:
        return False


def parameter_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions,
    *,
    source_inventory: str | None = None,
    kind: str,
    key_fn: Callable[[object], tuple],
    parameter_ids: tuple[str, ...],
    outcome_type: Callable[..., _Outcome],
    entrypoint: str,
    feature_key_fn: Callable[[object], tuple] | None = None,
    source_at_fn: Callable[[object], tuple[float, float, float]] | None = None,
    source_type: type | None = None,
    sources_fn: Callable[[RecognitionResult], tuple] | None = None,
    parameter_ids_fn: Callable[[object, object], tuple[str, ...] | None] | None = None,
    key_errors: tuple[type[Exception], ...] = (),
    parameter_errors: tuple[type[Exception], ...] = (AttributeError, TypeError),
) -> list[_Outcome]:
    """Join exact physical keys to one-feature, fixed-parameter registry outcomes."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            f"{entrypoint}() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    if sources_fn is None:
        if source_inventory is None:
            raise ValueError("a source inventory or source selector is required")
        sources = tuple(getattr(recognition, source_inventory))
    else:
        sources = sources_fn(recognition)
    if not sources:
        return []

    keyed_sources: list[tuple[object, tuple | None]] = []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        try:
            if source_type is not None and not isinstance(source, source_type):
                raise TypeError
            key = key_fn(source)
        except key_errors:
            key = None
        keyed_sources.append((source, key))
        if key is not None:
            source_counts[key] += 1
    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != kind:
            continue
        try:
            ir_by_key[(feature_key_fn or key_fn)(feature)].append(feature)
        except key_errors:
            continue

    placed, satisfied, dropped = index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[_Outcome] = []
    for source, key in keyed_sources:
        matches = ir_by_key.get(key, ()) if key is not None else ()
        feature = (
            matches[0] if key is not None and len(matches) == source_counts[key] == 1 else None
        )
        if source_at_fn is None:
            assert key is not None  # These families require a valid source key.
            source_at = key[1]
        else:
            source_at = source_at_fn(source)
        valid = feature is not None and (
            parameter_ids_fn(feature, source) == parameter_ids
            if parameter_ids_fn is not None
            else _has_parameters(feature, parameter_ids, parameter_errors)
        )
        for parameter in parameter_ids:
            if not valid:
                outcomes.append(
                    outcome_type(
                        source_at=source_at,
                        parameter_id=parameter,
                        state="unverifiable",
                        source_records=(source,),
                    )
                )
                continue
            outcome_state: ParameterRequirementState = state(
                feature,
                parameter,
                placed=placed,
                satisfied=satisfied,
                suppressed=suppressed,
                dropped=dropped,
                registry=registry,
            )
            outcomes.append(
                outcome_type(
                    source_at=source_at,
                    parameter_id=parameter,
                    state=outcome_state,
                    features=(feature,),
                    source_records=(source,),
                )
            )
    return with_measurement_carriers(outcomes, registry)


def lint_parameter_coverage(
    outcomes: list[_Outcome],
    *,
    issue_factory: Callable[[_Outcome, str], LintIssue],
    missing_message: str = "has no placed, suppressed, or dropped callout outcome",
) -> list[LintIssue]:
    """Report uncovered states without repeating placement-drop findings."""
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": missing_message,
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in outcomes:
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        issues.append(issue_factory(outcome, messages[outcome.state]))
    return issues
