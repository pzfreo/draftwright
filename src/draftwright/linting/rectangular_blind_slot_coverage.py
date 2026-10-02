"""Semantic completeness for recognised rectangular blind slots (#1421).

Each aggregate ``RectangularBlindSlot`` owns three independently auditable requirements:
the U-section width, the capped mouth-to-terminal run and the flat-bottom depth.  Every
released structural fact joins the provider record to one IR feature; exact parameter
identities then follow those requirements to drawing outcomes.  Labels, annotation names,
views, projected geometry, leader tips and page coordinates are never correspondence evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from quiddity import RecognitionResult, SectionRecess

from draftwright.linting._coverage_common import recess_point as _point
from draftwright.linting._coverage_common import recess_positive as _positive
from draftwright.linting._coverage_common import recess_source_at as _source_at
from draftwright.linting._coverage_common import recess_span as _span
from draftwright.linting._coverage_common import section_recess_sources
from draftwright.linting._parameter_coverage import (
    ParameterRequirementState as RectangularBlindSlotRequirementState,
)
from draftwright.linting._parameter_coverage import (
    lint_parameter_coverage,
    parameter_outcomes,
)
from draftwright.linting._registry import RequirementCarrier
from draftwright.linting.issues import LintIssue
from draftwright.section_recess_contract import section_recess_fields

_PARAMETERS = (
    "rectangular_blind_slot_width.length",
    "rectangular_blind_slot_length.length",
    "rectangular_blind_slot_depth.length",
)


@dataclass(frozen=True)
class RectangularBlindSlotRequirementOutcome:
    """The observable engine outcome of one width, run or depth requirement."""

    source_at: tuple[float, float, float]
    parameter_id: str
    state: RectangularBlindSlotRequirementState
    requirement_count: int = 1
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)
    carriers: tuple[RequirementCarrier, ...] = field(default=(), kw_only=True)


def rectangular_blind_slot_key(slot, *, require_frame: bool = False) -> tuple:
    """Return all public structural facts after validating their exact schema.

    Rounding is limited to the generated Sheet program's documented 0.001 mm precision.
    Ambiguous duplicate keys fail closed in :func:`rectangular_blind_slot_requirement_outcomes`.
    """

    if not require_frame:
        actual, data = section_recess_fields(slot)
        if actual != "rectangular_blind_slot":
            raise ValueError("recess does not use this blind-slot grammar")
        return (
            data["axis"],
            data["open_sign"],
            data["width_axis"],
            data["depth_axis"],
            data["depth_sign"],
            _positive(data["width"]),
            _positive(data["length"]),
            _positive(data["depth"]),
            _point(data["origin"]),
        )

    axes = (slot.axis, slot.width_axis, slot.depth_axis)
    if (
        any(not isinstance(axis, str) or axis not in {"x", "y", "z"} for axis in axes)
        or len(set(axes)) != 3
    ):
        raise ValueError
    for sign in (slot.open_sign, slot.depth_sign):
        if not isinstance(sign, int) or isinstance(sign, bool) or sign not in (-1, 1):
            raise ValueError
    if require_frame:
        if slot.frame.axis != slot.axis:
            raise ValueError
        at = _point(slot.frame.origin)
    else:
        at = _point(slot.at)
    return (
        slot.axis,
        slot.open_sign,
        slot.width_axis,
        slot.depth_axis,
        slot.depth_sign,
        _positive(slot.width),
        _positive(slot.length),
        _positive(slot.depth),
        at,
    )


def _parameter_ids(feature, source) -> tuple[str, ...] | None:
    try:
        _kind, data = section_recess_fields(source)
        parameters = tuple(feature.parameters())
        observed = {}
        for parameter in parameters:
            parameter_id = parameter.parameter_id
            if not isinstance(parameter_id, str) or parameter_id in observed:
                return None
            span = (
                tuple(_point(point) for point in parameter.span)
                if parameter.span is not None
                else None
            )
            observed[parameter_id] = (_positive(parameter.value), span)
        source_at = _point(data["origin"])
        expected_values = (
            _positive(data["width"]),
            _positive(data["length"]),
            _positive(data["depth"]),
        )
        expected_spans = (
            _span(source_at, data["width_axis"], expected_values[0]),
            _span(source_at, data["axis"], expected_values[1]),
            _span(source_at, data["depth_axis"], expected_values[2]),
        )
        expected = dict(
            zip(_PARAMETERS, zip(expected_values, expected_spans, strict=True), strict=True)
        )
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None
    if observed != expected:
        return None
    return _PARAMETERS


def rectangular_blind_slot_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[RectangularBlindSlotRequirementOutcome]:
    """Follow all three requirements of every recognised rectangular blind slot."""
    return parameter_outcomes(
        recognition,
        features,
        registry,
        omissions,
        sources_fn=lambda run: section_recess_sources(run, "rectangular_blind_slot"),
        kind="rectangular_blind_slot",
        key_fn=rectangular_blind_slot_key,
        feature_key_fn=lambda feature: rectangular_blind_slot_key(feature, require_frame=True),
        source_at_fn=_source_at,
        source_type=SectionRecess,
        key_errors=(AttributeError, IndexError, OverflowError, TypeError, ValueError),
        parameter_ids=_PARAMETERS,
        parameter_ids_fn=_parameter_ids,
        outcome_type=RectangularBlindSlotRequirementOutcome,
        entrypoint="rectangular_blind_slot_requirement_outcomes",
    )


def lint_rectangular_blind_slot_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered rectangular blind-slot requirements without duplicate drops."""

    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    return lint_parameter_coverage(
        rectangular_blind_slot_requirement_outcomes(recognition, features, registry, omissions),
        missing_message="has no placed, suppressed, or dropped measurement outcome",
        issue_factory=lambda outcome, reason: LintIssue(
            severity=severity,
            code=f"rectangular_blind_slot_requirement_{outcome.state}",
            message=(
                f"rectangular blind slot {outcome.parameter_id} at {outcome.source_at} {reason}"
            ),
        ),
    )
