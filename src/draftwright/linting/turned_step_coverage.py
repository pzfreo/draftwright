"""Semantic completeness outcomes for recognised turned-profile bands (#1374).

Each body-local ``TurnedStep`` that is not owned by a correlated ``Groove`` represents one
physical outside-diameter band with two requirements: axial length and OD.  Axis line and
axial span join the provider record to one ``StepFeature``; compiler measurement identities
then follow both requirements to explicit outcomes.  Names, labels, views, and page positions
are deliberately not correspondence evidence.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import isfinite
from numbers import Real
from typing import Literal

from b123d_recognisers import RecognitionResult, TurnedProfile, TurnedProfileKey

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import UNJOINED_PARAMETER_ID, is_placement_drop
from draftwright.recognition_frame import (
    AmbiguousTurnedOwnershipError,
    groove_owns_turned_step_band,
    require_unambiguous_groove_owner,
)

TurnedStepRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "dropped",
    "missing",
    "unverifiable",
]

# TurnedStep shoulder stations are public at 0.001 mm while TurnedProfileKey body bounds retain
# eight decimal places.  A station at either physical end can therefore lie half a published
# quantum beyond the higher-precision bound without contradicting that ownership record.
_TURNED_STEP_COORD_TOL = 0.0005 + 1e-9


@dataclass(frozen=True)
class TurnedStepRequirementOutcome:
    """The observable engine outcome of one physical band requirement."""

    source_axis: str | None
    source_line: tuple[float, float] | None
    source_span: tuple[float, float] | None
    parameter_id: str
    state: TurnedStepRequirementState
    requirement_count: int = 1
    features: tuple = ()
    representation_feature: object | None = None
    representation_parameter: str | None = None
    source_records: tuple[object, ...] = field(default=(), repr=False, compare=False, kw_only=True)


def _number(value, *, digits: int | None = 6) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"expected a finite real number, got {type(value).__name__}")
    try:
        result = float(value)
    except OverflowError as error:
        raise ValueError("expected a finite representable real number") from error
    if not isfinite(result):
        raise ValueError("expected a finite real number")
    return result if digits is None else round(result, digits)


def _axis(value) -> str:
    if not isinstance(value, str) or value not in {"x", "y", "z"}:
        raise ValueError(f"expected principal axis x/y/z, got {value!r}")
    return value


def _point3(value, *, digits: int | None = 6) -> tuple[float, float, float]:
    if not isinstance(value, tuple):
        raise TypeError("a point must be a three-number tuple")
    components = value
    if len(components) != 3:
        raise ValueError("a point must contain exactly three coordinates")
    return tuple(  # type: ignore[return-value]
        _number(component, digits=digits) for component in components
    )


def _axis_line(axis: str, origin) -> tuple[float, float]:
    axis = _axis(axis)
    axis_index = "xyz".index(axis)
    point = _point3(origin)
    return tuple(value for index, value in enumerate(point) if index != axis_index)  # type: ignore[return-value]


def _profile_key(profile, step):
    wrapper_key = getattr(profile, "profile", None)
    step_key = getattr(step, "profile", None)
    if wrapper_key is None and step_key is None:
        raise ValueError("turned-step source lacks body-local profile identity")
    if wrapper_key is not None and step_key is not None and wrapper_key != step_key:
        raise ValueError("turned-step source disagrees with its containing profile identity")
    return step_key if step_key is not None else wrapper_key


def _profile_identity(
    key, span: tuple[float, float]
) -> tuple[str, tuple[float, float, float], tuple[float, ...]]:
    if not isinstance(key, TurnedProfileKey):
        raise TypeError(
            f"turned-step profile identity must be TurnedProfileKey, got {type(key).__name__}"
        )
    axis = _axis(key.axis)
    axis_index = "xyz".index(axis)
    origin = _point3(key.axis_origin, digits=None)
    if origin[axis_index] != 0.0:
        raise ValueError("turned-profile axis origin must be canonical on its axial coordinate")
    if not isinstance(key.body_bounds, tuple):
        raise TypeError("turned-profile body bounds must be a six-number tuple")
    bounds = tuple(_number(value, digits=None) for value in key.body_bounds)
    if len(bounds) != 6:
        raise ValueError("turned-profile body bounds must contain exactly six coordinates")
    pairs = tuple((bounds[index], bounds[index + 1]) for index in range(0, 6, 2))
    if any(lo >= hi for lo, hi in pairs):
        raise ValueError("turned-profile body bounds must be strictly ordered on every axis")
    for index, (lo, hi) in enumerate(pairs):
        if index == axis_index:
            if span[0] < lo - _TURNED_STEP_COORD_TOL or span[1] > hi + _TURNED_STEP_COORD_TOL:
                raise ValueError("turned-step span lies outside its profile body bounds")
        elif (
            origin[index] < lo - _TURNED_STEP_COORD_TOL
            or origin[index] > hi + _TURNED_STEP_COORD_TOL
        ):
            # ``body_bounds`` is the complete connected body's ownership discriminator, not
            # a promise of transverse symmetry.  Tabs and other asymmetric additions may
            # move its midpoint while the published turning axis remains inside the body.
            raise ValueError("turned-profile axis line lies outside its body bounds")
    return axis, origin, bounds


def turned_step_source_geometry(
    profile, step
) -> tuple[str, tuple[float, float], tuple[float, float]]:
    """Validate and return the occurrence geometry, excluding scored diameter."""

    axis = _axis(getattr(step, "axis", None))
    if _axis(getattr(profile, "axis", None)) != axis:
        raise ValueError("turned step and containing profile use different axes")
    key = _profile_key(profile, step)
    if _axis(getattr(key, "axis", None)) != axis:
        raise ValueError("turned step and profile key use different axes")
    origin = _point3(getattr(key, "axis_origin", None))
    lo = _number(getattr(step, "lo", None))
    hi = _number(getattr(step, "hi", None))
    if hi <= lo:
        raise ValueError("turned-step axial span must have positive length")
    _profile_identity(key, (lo, hi))
    return axis, _axis_line(axis, origin), (lo, hi)


def turned_step_source_key(profile, step) -> tuple:
    """Canonical axis-line/span/diameter key for one provider-owned band."""

    axis, line, span = turned_step_source_geometry(profile, step)
    diameter = _number(getattr(step, "diameter", None))
    if diameter <= 0:
        raise ValueError("turned-step diameter must be positive")
    return axis, line, span, diameter


def turned_step_key(feature) -> tuple:
    """Facts retained identically by a public turned step and Draftwright IR."""

    axis = _axis(getattr(feature.frame, "axis", None))
    axis_index = "xyz".index(axis)
    frame_origin = _point3(getattr(feature.frame, "origin", None))
    profile = getattr(feature, "profile", None)
    if profile is not None and _axis(getattr(profile, "axis", None)) != axis:
        raise ValueError("turned-step IR profile and frame use different axes")
    origin = _point3(profile.axis_origin if profile is not None else frame_origin)
    span = getattr(feature, "span", None)
    if span is None or len(span) != 2:
        raise ValueError("turned-step feature lacks its two-point axial span")
    points = tuple(_point3(point) for point in span)
    lo, hi = sorted(point[axis_index] for point in points)
    if hi <= lo:
        raise ValueError("turned-step IR span must have positive length")
    if profile is not None:
        _profile_identity(profile, (lo, hi))
    line = _axis_line(axis, origin)
    if any(_axis_line(axis, point) != line for point in points):
        raise ValueError("turned-step span leaves its body-local axis line")
    if _axis_line(axis, frame_origin) != line:
        raise ValueError("turned-step frame leaves its body-local axis line")
    length = _number(getattr(feature, "length", None))
    if length <= 0 or length != _number(hi - lo):
        raise ValueError("turned-step IR length disagrees with its axial span")
    if frame_origin[axis_index] != _number((lo + hi) / 2.0):
        raise ValueError("turned-step frame origin is not the axial-span midpoint")
    diameter = _number(getattr(feature, "diameter", None))
    if diameter <= 0:
        raise ValueError("turned-step IR diameter must be positive")
    return axis, line, (lo, hi), diameter


def turned_step_source_profile_identity(profile, step) -> tuple:
    """Validated public body-local ownership retained by one source record."""

    _axis, _line, span = turned_step_source_geometry(profile, step)
    return _profile_identity(_profile_key(profile, step), span)


def turned_step_ir_profile_identity(feature) -> tuple | None:
    """Validated retained ownership, or ``None`` for an explicit Sheet declaration."""

    key = turned_step_key(feature)
    profile = getattr(feature, "profile", None)
    return None if profile is None else _profile_identity(profile, key[2])


def physical_turned_steps(recognition: RecognitionResult) -> tuple[tuple[object, object], ...]:
    """Return ``(profile, step)`` occurrences after exact groove ownership is removed."""

    if not isinstance(recognition.turned_steps, tuple):
        raise TypeError("RecognitionResult.turned_steps must be an immutable tuple")
    if not isinstance(recognition.grooves, tuple):
        raise TypeError("RecognitionResult.grooves must be an immutable tuple")

    # Validate the occurrence roster before asking the provider to sort/group it.  In
    # particular, ``TurnedProfile.from_steps`` sorts on ``lo`` and a malformed public record
    # such as ``lo="0"`` must become an unverifiable occurrence rather than abort linting.
    raw_steps = tuple(recognition.turned_steps)
    valid_steps = []
    invalid_ids: set[int] = set()
    for step in raw_steps:
        try:
            turned_step_source_key(step, step)
        except (AttributeError, TypeError, ValueError):
            invalid_ids.add(id(step))
        else:
            valid_steps.append(step)

    profiles = TurnedProfile.grouped_from_steps(valid_steps)
    profile_by_step_id = {id(step): profile for profile in profiles for step in profile.steps}
    grooves_by_profile: dict[int, list] = {id(profile): [] for profile in profiles}
    for groove in recognition.grooves:
        owners = require_unambiguous_groove_owner(groove, profiles)
        if owners:
            grooves_by_profile[id(owners[0])].append(groove)
    retained: list[tuple[object, object]] = []
    for step in raw_steps:
        if id(step) in invalid_ids:
            # The step itself supplies the wrapper's public ``axis``/``profile`` attributes;
            # the later source validator records exactly two unverifiable requirements.
            retained.append((step, step))
            continue
        profile = profile_by_step_id[id(step)]
        if not any(
            groove_owns_turned_step_band(groove, step)
            for groove in grooves_by_profile[id(profile)]
        ):
            retained.append((profile, step))
    return tuple(retained)


def _has_parameters(feature) -> bool:
    try:
        return {parameter.parameter_id for parameter in feature.parameters()} == {
            "step.length",
            "step.diameter",
        }
    except (AttributeError, TypeError):
        return False


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
    dropped = {
        (measurement.feature, measurement.parameter)
        for issue in registry.issues
        if is_placement_drop(issue)
        for measurement in getattr(issue, "measurement_ids", ())
        if getattr(measurement, "feature", None) is not None
        and isinstance(getattr(measurement, "parameter", None), str)
    }
    return placed, satisfied, dropped


def turned_step_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    *,
    allow_declared_profile_omission: bool = False,
) -> list[TurnedStepRequirementOutcome]:
    """Follow every physical OD band's length and diameter to compiler outcomes."""

    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "turned_step_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    raw_step_inventory = recognition.turned_steps
    if isinstance(raw_step_inventory, tuple):
        raw_steps = raw_step_inventory
    else:
        try:
            raw_steps = tuple(raw_step_inventory)
        except Exception:
            # Cardinality itself is unknowable, but the corrupted aggregate must remain an
            # explicit audited fact rather than become indistinguishable from a valid empty
            # tuple.  This is one aggregate contract outcome, not an invented physical band.
            return [
                TurnedStepRequirementOutcome(
                    None, None, None, UNJOINED_PARAMETER_ID, "unverifiable", requirement_count=1
                )
            ]
    if not isinstance(raw_step_inventory, tuple) or not isinstance(recognition.grooves, tuple):
        # Snapshot an iterable impostor once so a generator cannot be consumed by ownership
        # and then silently shrink this denominator on the conservative fallback.
        return [
            TurnedStepRequirementOutcome(
                None,
                None,
                None,
                parameter,
                "unverifiable",
                source_records=(_source,),
            )
            for _source in raw_steps
            for parameter in ("step.length", "step.diameter")
        ]
    try:
        sources = physical_turned_steps(recognition)
    except (
        AmbiguousTurnedOwnershipError,
        AttributeError,
        IndexError,
        OverflowError,
        TypeError,
        ValueError,
    ):
        # Groove ownership changes this family's denominator.  If a public groove record is
        # malformed or ownership is ambiguous, retaining every raw band as unverifiable is
        # conservative; guessing an owner would either invent or erase requirements.
        return [
            TurnedStepRequirementOutcome(
                None,
                None,
                None,
                parameter,
                "unverifiable",
                source_records=(_source,),
            )
            for _source in raw_steps
            for parameter in ("step.length", "step.diameter")
        ]
    if not sources:
        return []

    source_counts: dict[tuple, int] = defaultdict(int)
    source_keys: list[tuple | None] = []
    source_profile_ids: list[tuple | None] = []
    valid_by_profile: dict[int, list[tuple]] = defaultdict(list)
    for profile, source in sources:
        try:
            key = turned_step_source_key(profile, source)
            profile_id = turned_step_source_profile_identity(profile, source)
        except (AttributeError, TypeError, ValueError):
            source_keys.append(None)
            source_profile_ids.append(None)
            continue
        source_keys.append(key)
        source_profile_ids.append(profile_id)
        source_counts[key] += 1
        valid_by_profile[id(profile)].append(key)
    ir_by_key: dict[tuple, list] = defaultdict(list)
    rotational_by_key: dict[tuple, list] = defaultdict(list)
    malformed = []
    for feature in features:
        if getattr(feature, "kind", None) != "step":
            if getattr(feature, "kind", None) == "rotational":
                try:
                    axis = _axis(getattr(feature.frame, "axis", None))
                    line = _axis_line(axis, getattr(feature.frame, "origin", None))
                    diameter = _number(getattr(feature, "od", None))
                    if diameter <= 0:
                        raise ValueError("rotational OD must be positive")
                    rotational_by_key[(axis, line, diameter)].append(feature)
                except (AttributeError, TypeError, ValueError):
                    malformed.append(feature)
            continue
        try:
            ir_by_key[turned_step_key(feature)].append(feature)
        except (AttributeError, TypeError, ValueError):
            malformed.append(feature)

    placed, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }

    def evidence_state(feature, parameter) -> TurnedStepRequirementState:
        identity = (feature, parameter)
        if identity in placed:
            return "placed"
        if identity in satisfied:
            return "satisfied_by_structured_note"
        if identity in suppressed:
            return "suppressed"
        if identity in dropped:
            return "dropped"
        associated = registry.names_for_feature(feature)
        return (
            "unverifiable"
            if any(
                not registry.measurement_of(name) and not satisfaction_of(registry, name)
                for name in associated
            )
            else "missing"
        )

    outcomes: list[TurnedStepRequirementOutcome] = []
    for (profile, source), source_key, source_profile_id in zip(
        sources, source_keys, source_profile_ids, strict=True
    ):
        if source_key is None:
            for parameter in ("step.length", "step.diameter"):
                outcomes.append(
                    TurnedStepRequirementOutcome(
                        None,
                        None,
                        None,
                        parameter,
                        "unverifiable",
                        source_records=(source,),
                    )
                )
            continue
        key = source_key
        matches = tuple(
            candidate
            for candidate in ir_by_key.get(key, ())
            if (candidate_profile := turned_step_ir_profile_identity(candidate))
            == source_profile_id
            or (allow_declared_profile_omission and candidate_profile is None)
        )
        feature = matches[0] if len(matches) == source_counts[key] == 1 else None
        for parameter in ("step.length", "step.diameter"):
            if feature is None or malformed or not _has_parameters(feature):
                outcomes.append(
                    TurnedStepRequirementOutcome(
                        key[0],
                        key[1],
                        key[2],
                        parameter,
                        "unverifiable",
                        source_records=(source,),
                    )
                )
                continue
            representation = feature
            representation_parameter = parameter
            state = evidence_state(feature, parameter)
            if parameter == "step.diameter" and state == "missing":
                # A part-global OD can stand in for one profile's unique largest band only when
                # axis line + diameter identify exactly one physical occurrence. It carries no
                # axial span or profile token, so equal disjoint bands must keep their native
                # StepFeature identities rather than multiplying one global measurement.
                profile_keys = valid_by_profile[id(profile)]
                is_unique_profile_maximum = bool(
                    profile_keys
                    and key[3] == max(candidate[3] for candidate in profile_keys)
                    and sum(candidate[3] == key[3] for candidate in profile_keys) == 1
                )
                globally_unique = (
                    sum(
                        candidate[0] == key[0]
                        and candidate[1] == key[1]
                        and candidate[3] == key[3]
                        for candidate in source_keys
                        if candidate is not None
                    )
                    == 1
                )
                rotational_matches = rotational_by_key.get((key[0], key[1], key[3]), ())
                if is_unique_profile_maximum and globally_unique and len(rotational_matches) == 1:
                    alternate = rotational_matches[0]
                    alternate_state = evidence_state(alternate, "od.diameter")
                    if alternate_state in {
                        "placed",
                        "satisfied_by_structured_note",
                        "suppressed",
                        "dropped",
                    }:
                        representation = alternate
                        representation_parameter = "od.diameter"
                        state = alternate_state
            outcomes.append(
                TurnedStepRequirementOutcome(
                    key[0],
                    key[1],
                    key[2],
                    parameter,
                    state,
                    features=(feature,),
                    representation_feature=representation,
                    representation_parameter=representation_parameter,
                    source_records=(source,),
                )
            )
    return outcomes
