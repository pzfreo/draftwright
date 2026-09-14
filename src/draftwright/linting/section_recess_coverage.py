"""Source-owned outcomes for supported and unsupported published recess geometry."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from quiddity import RecognitionResult, SectionRecess, SectionRecessRefusal

from draftwright.feature_identity import is_exact_envelope_feature
from draftwright.linting._registry import measurement_outcome_index, with_measurement_carriers
from draftwright.linting.issues import UNJOINED_PARAMETER_ID, LintIssue, requirement_subject
from draftwright.measurement_support import RequirementCarrier, RequirementExclusion
from draftwright.section_recess_contract import (
    UnsupportedSectionRecess,
    circular_channel_fields,
    circular_channel_geometry,
    hex_pocket_fields,
    hex_pocket_geometry,
    section_recess_fields,
)


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


@dataclass(frozen=True)
class RecessRequirementOutcome:
    """One physical recess requirement, retaining its exact source and ink carriers."""

    source_at: tuple[float, float, float]
    parameter_id: str
    state: str
    requirement_count: int = 1
    features: tuple = ()
    source_records: tuple[SectionRecess, ...] = field(
        default=(), repr=False, compare=False, kw_only=True
    )
    carriers: tuple[RequirementCarrier, ...] = field(default=(), kw_only=True)
    intrinsic_exclusion: RequirementExclusion | None = field(default=None, kw_only=True)
    measurement_ids: tuple[tuple[object, str], ...] = field(default=(), kw_only=True)


_SEAT_SIZES = ("seat_diameter.diameter", "seat_run.length", "seat_sweep.angle")
_SEAT_LOCATIONS = tuple(f"seat_location.location.{axis}" for axis in "xyz")


def _seat_key(axis, values):
    return (axis, values["radius"], values["length"], values["centreline"], values["section"])


def _seat_feature_key(feature):
    values = circular_channel_geometry(
        feature.axis, feature.radius, feature.length, feature.centreline, feature.section
    )
    if feature.frame.axis != feature.axis or feature.frame.origin != values["origin"]:
        raise ValueError("seat frame does not anchor its physical arc")
    parameters = tuple(feature.parameters())
    actual = {
        parameter.parameter_id: (parameter.value, parameter.span) for parameter in parameters
    }
    expected = dict(
        zip(
            _SEAT_SIZES,
            (
                (2 * values["radius"], None),
                (values["length"], values["centreline"]),
                (values["sweep"], None),
            ),
            strict=True,
        )
    )
    if len(parameters) != 3 or actual != expected:
        raise ValueError("seat parameters do not preserve its physical cylinder")
    return _seat_key(feature.axis, values)


def _seat_run_representation(feature, values, omissions, features):
    """Accept a consolidated overall extent only on the seat's actual end planes."""
    run = "xyz".index(feature.axis)
    parameter = ("width.length", "depth.length", "height.length")[run]
    for omission in omissions:
        owner = getattr(omission, "conveyed_by", None)
        if (
            omission.feature is not feature
            or omission.parameter_id != "seat_run.length"
            or owner is None
        ):
            continue
        envelope = owner.feature
        if not is_exact_envelope_feature(envelope) or not any(
            item is envelope for item in features
        ):
            continue
        dimension = next(p for p in envelope.parameters() if p.parameter_id == parameter)
        if (
            owner.parameter == parameter
            and abs(dimension.value - values["length"]) <= 1e-6
            and abs(envelope.bbox_min[run] - values["centreline"][0][run]) <= 1e-6
            and abs(envelope.bbox_max[run] - values["centreline"][1][run]) <= 1e-6
        ):
            return envelope, parameter
    return None


def _recess_matches(
    recognition, features, *, classification, kind, source_fields, source_key, feature_key
):
    """Join exact physical geometry only when both source and IR occurrence are unique."""
    if recognition is None:
        return
    if type(recognition) is not RecognitionResult:
        raise TypeError("recess completeness requires the exact RecognitionResult")
    sources = [
        record
        for record in recognition.section_recesses
        if (record.classification.feature_kind, record.classification.section_shape)
        == classification
    ]
    keyed = []
    for source in sources:
        try:
            values = source_fields(source)
            key = source_key(values["axis"], values)
        except UnsupportedSectionRecess:
            # Unaccepted end/profile forms retain the unsupported grammar outcome.
            continue
        except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
            values, key = None, None
        keyed.append((source, values, key))
    counts = Counter(key for _source, _values, key in keyed if key is not None)
    candidates = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != kind:
            continue
        try:
            candidates[feature_key(feature)].append(feature)
        except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
            continue
    for source, values, key in keyed:
        matches = candidates.get(key, ()) if key is not None else ()
        feature = matches[0] if len(matches) == counts[key] == 1 else None
        yield source, values, feature


def _recess_state(identity, placed, satisfied, dropped, suppressed):
    return (
        "placed"
        if identity in placed
        else "satisfied_by_structured_note"
        if identity in satisfied
        else "suppressed"
        if identity in suppressed
        else "dropped"
        if identity in dropped
        else "missing"
    )


def circular_channel_requirement_outcomes(
    recognition, features, registry, omissions=(), *, bbox=None, part=None
):
    """Independently account for six requirements per released circular seat."""
    if bbox is None and part is not None:
        bbox = part.bounding_box()
    placed, satisfied, dropped = measurement_outcome_index(registry)
    suppressed = {(item.feature, item.parameter_id) for item in omissions if item.authored}
    outcomes = []
    for source, values, feature in _recess_matches(
        recognition,
        features,
        classification=("channel", "circular"),
        kind="circular_channel",
        source_fields=circular_channel_fields,
        source_key=_seat_key,
        feature_key=_seat_feature_key,
    ):
        origin = source.geometry.frame.origin if values is None else values["origin"]
        at = (origin[0], origin[1], origin[2])
        for parameter in (*_SEAT_SIZES, *_SEAT_LOCATIONS):
            identity = (feature, parameter)
            representation = None
            if (
                feature is not None
                and parameter == "seat_run.length"
                and identity not in placed | satisfied
            ):
                representation = _seat_run_representation(feature, values, omissions, features)
                if representation is not None:
                    identity = representation
            exclusion = None
            if feature is None:
                state = "unverifiable"
            elif parameter in _SEAT_LOCATIONS and bbox is None:
                state = "unverifiable"
            else:
                if parameter in _SEAT_LOCATIONS:
                    assert values is not None
                    axis = "xyz".index(parameter[-1])
                    point = tuple(
                        (a + b) / 2
                        for a, b in zip(
                            values["centreline"][0], values["centreline"][1], strict=True
                        )
                    )
                    start = list(point)
                    start[axis] = float(getattr(bbox.min, parameter[-1].upper()))
                    if abs(point[axis] - start[axis]) <= 1e-9:
                        exclusion = RequirementExclusion(
                            "seat_axis_coincident_with_stock_datum",
                            (source,),
                            span=(tuple(start), point),
                        )
                state = (
                    "inapplicable"
                    if exclusion is not None
                    else _recess_state(identity, placed, satisfied, dropped, suppressed)
                )
            outcomes.append(
                RecessRequirementOutcome(
                    at,
                    parameter,
                    state,
                    features=() if feature is None else (feature,),
                    source_records=(source,),
                    intrinsic_exclusion=exclusion,
                    measurement_ids=() if representation is None else (representation,),
                )
            )
    return with_measurement_carriers(outcomes, registry)


_HEX_SIZES = ("polygon_across_flats.length", "pocket_depth.length")


def _hex_key(axis, values):
    return (axis, values["origin"], values["depth"], values["open_sign"], values["section"])


def _hex_feature_key(feature):
    values = hex_pocket_geometry(
        feature.frame.axis, feature.depth, feature.open_sign, feature.frame.origin, feature.section
    )
    parameters = tuple(feature.parameters())
    actual = {
        parameter.parameter_id: (parameter.value, parameter.span) for parameter in parameters
    }
    expected = dict(
        zip(_HEX_SIZES, ((values["across_flats"], None), (values["depth"], None)), strict=True)
    )
    if len(parameters) != 2 or actual != expected:
        raise ValueError("hex parameters do not preserve the physical opposed walls and floor")
    return _hex_key(feature.frame.axis, values)


def hex_pocket_requirement_outcomes(recognition, features, registry, omissions=()):
    """Account for the across-flats size and blind depth of every supported hex pocket."""
    placed, satisfied, dropped = measurement_outcome_index(registry)
    suppressed = {(item.feature, item.parameter_id) for item in omissions if item.authored}
    outcomes = []
    for source, values, feature in _recess_matches(
        recognition,
        features,
        classification=("pocket", "hexagonal"),
        kind="hex_pocket",
        source_fields=hex_pocket_fields,
        source_key=_hex_key,
        feature_key=_hex_feature_key,
    ):
        at = source.geometry.frame.origin if values is None else values["origin"]
        for parameter in _HEX_SIZES:
            state = (
                "unverifiable"
                if feature is None
                else _recess_state((feature, parameter), placed, satisfied, dropped, suppressed)
            )
            outcomes.append(
                RecessRequirementOutcome(
                    at,
                    parameter,
                    state,
                    features=() if feature is None else (feature,),
                    source_records=(source,),
                )
            )
    return with_measurement_carriers(outcomes, registry)


def lint_hex_pocket_coverage(recognition, features, registry, omissions=()):
    return [
        LintIssue(
            severity="warning",
            code=f"hex_pocket_requirement_{outcome.state}",
            message=f"hex pocket {requirement_subject(outcome)} at {outcome.source_at} is {outcome.state}",
        )
        for outcome in hex_pocket_requirement_outcomes(recognition, features, registry, omissions)
        if outcome.state not in {"placed", "satisfied_by_structured_note", "dropped"}
    ]


def lint_circular_channel_coverage(recognition, features, registry, omissions=(), *, bbox=None):
    """Report uncovered seats using the same source-owned ledger as drawing reports."""
    return [
        LintIssue(
            severity="warning",
            code=f"circular_channel_requirement_{outcome.state}",
            message=f"circular seat {requirement_subject(outcome)} at {outcome.source_at} is {outcome.state}",
        )
        for outcome in circular_channel_requirement_outcomes(
            recognition, features, registry, omissions, bbox=bbox
        )
        if outcome.state
        not in {"placed", "satisfied_by_structured_note", "dropped", "inapplicable"}
    ]
