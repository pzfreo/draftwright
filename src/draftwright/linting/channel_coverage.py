"""Semantic completeness for full-span open-channel widths (#917)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from quiddity import RecognitionResult, SectionRecess, has_multi_axis_plates

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue
from draftwright.section_recess_contract import recesses_with_kind, section_recess_fields

ChannelRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]


@dataclass(frozen=True)
class ChannelRequirementOutcome:
    source_at: tuple[float, float, float]
    width: float
    feature_kind: str
    parameter_id: str
    state: ChannelRequirementState
    features: tuple = ()
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)


def _rounded(value) -> float:
    return round(float(value), 3)


def _point(value) -> tuple[float, float, float]:
    return tuple(_rounded(component) for component in value)  # type: ignore[return-value]


def _key(channel) -> tuple:
    if type(channel) is SectionRecess:
        kind, data = section_recess_fields(channel)
        if kind != "channel":
            raise ValueError("channel correspondence requires the channel grammar")
        return (
            data["width_axis"],
            data["long_axis"],
            *(_rounded(data[key]) for key in ("width", "w_center", "lo", "hi", "d_lo", "d_hi")),
            data["open_sign"],
        )
    return (
        channel.width_axis,
        channel.long_axis,
        _rounded(channel.width),
        _rounded(channel.w_center),
        _rounded(channel.lo),
        _rounded(channel.hi),
        _rounded(channel.d_lo),
        _rounded(channel.d_hi),
        int(channel.open_sign),
    )


def _matches(measurement, feature, parameter: str) -> bool:
    return (
        getattr(measurement, "feature", None) == feature
        and getattr(measurement, "parameter", None) == parameter
    )


def _state(feature, parameter, *, placed, satisfied, suppressed, dropped, registry):
    if feature is None:
        return "unverifiable"
    if any(_matches(measurement, feature, parameter) for measurement in placed):
        return "placed"
    if any(_matches(identity, feature, parameter) for identity in satisfied):
        return "satisfied_by_structured_note"
    if (feature, parameter) in suppressed:
        return "suppressed"
    if any(_matches(measurement, feature, parameter) for measurement in dropped):
        return "dropped"
    associated = registry.names_for_feature(feature)
    if any(
        not registry.measurement_of(name) and not satisfaction_of(registry, name)
        for name in associated
    ):
        return "unverifiable"
    return "missing"


def channel_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[ChannelRequirementOutcome]:
    """Follow every recognised channel width to a compiler/placement outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "channel_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )

    # The same full-span recess geometry can be a monolithic centred rebate. That domain
    # stays with the correlated step ladder; the explicit channel-width scheme is required
    # only when recognition also proves a multi-axis plate construction (base + walls).
    sources = (
        list(recesses_with_kind(recognition.section_recesses, "channel"))
        if has_multi_axis_plates(recognition.plates)
        else []
    )
    if not sources:
        return []
    features_by_key: dict[tuple, list] = {}
    for feature in features:
        if getattr(feature, "kind", None) == "channel":
            features_by_key.setdefault(_key(feature), []).append(feature)

    placed = {
        measurement for name in registry.names() for measurement in registry.measurement_of(name)
    }
    satisfied = satisfaction_ids(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    dropped = {
        measurement
        for issue in registry.issues
        for measurement in getattr(issue, "measurement_ids", ())
    }

    outcomes = []
    for source in sources:
        _, data = section_recess_fields(source)
        matches = features_by_key.get(_key(source), ())
        channel = matches[0] if len(matches) == 1 else None
        envelopes = [
            feature for feature in features if getattr(feature, "kind", None) == "envelope"
        ]
        envelope = envelopes[0] if len(envelopes) == 1 else None
        lower_plate = None
        envelope_parameter = {
            "x": "width.length",
            "y": "depth.length",
            "z": "height.length",
        }[data["width_axis"]]
        if channel is not None and envelope is not None:
            axis_index = "xyz".index(data["width_axis"])
            bbox_lo = envelope.bbox_min[axis_index]
            channel_lo = data["w_center"] - data["width"] / 2
            lower = [
                feature
                for feature in features
                if getattr(feature, "kind", None) == "plate"
                and feature.axis == data["width_axis"]
                and abs(feature.lo - bbox_lo) <= 1e-3
                and abs(feature.hi - channel_lo) <= 1e-3
            ]
            lower_plate = lower[0] if len(lower) == 1 else None
        for feature_kind, feature, parameter in (
            ("channel", channel, "channel_width.length"),
            ("plate", lower_plate, "thickness.length"),
            ("envelope", envelope, envelope_parameter),
        ):
            outcomes.append(
                ChannelRequirementOutcome(
                    source_at=_point(data["origin"]),
                    width=_rounded(data["width"]),
                    feature_kind=feature_kind,
                    parameter_id=parameter,
                    state=_state(
                        feature,
                        parameter,
                        placed=placed,
                        satisfied=satisfied,
                        suppressed=suppressed,
                        dropped=dropped,
                        registry=registry,
                    ),
                    features=(feature,) if feature is not None else (),
                    source_records=(source,),
                )
            )
    return outcomes


def lint_channel_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report a missing channel-width outcome without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in channel_requirement_outcomes(recognition, features, registry, omissions):
        if outcome.state in {"placed", "satisfied_by_structured_note", "dropped"}:
            continue
        issues.append(
            LintIssue(
                severity=severity,
                code=f"channel_requirement_{outcome.state}",
                message=(
                    f"open channel at {outcome.source_at} width {outcome.width:g} measurement "
                    f"{outcome.feature_kind}.{outcome.parameter_id} {messages[outcome.state]}"
                ),
            )
        )
    return issues
