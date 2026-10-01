"""Shared recognition-to-outcome driver for one-parameter physical families."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Literal, Protocol, TypeVar

from quiddity import RecognitionResult

from draftwright.linting._coverage_common import index_evidence, state
from draftwright.linting._registry import with_measurement_carriers
from draftwright.linting.issues import LintIssue

SingleParameterRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]


class _SingleParameterOutcome(Protocol):
    @property
    def state(self) -> SingleParameterRequirementState: ...


_Outcome = TypeVar("_Outcome", bound=_SingleParameterOutcome)


def _has_parameters(feature, expected: tuple[str, ...]) -> bool:
    try:
        return tuple(parameter.parameter_id for parameter in feature.parameters()) == expected
    except (AttributeError, TypeError):
        return False


def single_parameter_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions,
    *,
    source_inventory: str,
    kind: str,
    key_fn: Callable[[object], tuple],
    parameter_ids: tuple[str, ...],
    outcome_type: Callable[..., _Outcome],
    entrypoint: str,
) -> list[_Outcome]:
    """Join exact physical keys to one-feature, one-parameter registry outcomes."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            f"{entrypoint}() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources = tuple(getattr(recognition, source_inventory))
    if not sources:
        return []

    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        source_counts[key_fn(source)] += 1
    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) == kind:
            ir_by_key[key_fn(feature)].append(feature)

    placed, satisfied, dropped = index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    outcomes: list[_Outcome] = []
    parameter = parameter_ids[0]
    for source in sources:
        key = key_fn(source)
        matches = ir_by_key.get(key, ())
        feature = matches[0] if len(matches) == source_counts[key] == 1 else None
        if feature is None or not _has_parameters(feature, parameter_ids):
            outcomes.append(outcome_type(key[1], "unverifiable", source_records=(source,)))
            continue
        outcome_state: SingleParameterRequirementState = state(
            feature,
            parameter,
            placed=placed,
            satisfied=satisfied,
            suppressed=suppressed,
            dropped=dropped,
            registry=registry,
        )
        outcomes.append(
            outcome_type(key[1], outcome_state, features=(feature,), source_records=(source,))
        )
    return with_measurement_carriers(outcomes, registry)


def lint_single_parameter_coverage(
    outcomes: list[_Outcome], *, issue_factory: Callable[[_Outcome, str], LintIssue]
) -> list[LintIssue]:
    """Report uncovered states without repeating placement-drop findings."""
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped callout outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in outcomes:
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        issues.append(issue_factory(outcome, messages[outcome.state]))
    return issues
