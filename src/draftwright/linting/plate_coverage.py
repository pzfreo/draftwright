"""Semantic completeness for recognised multi-plate slab requirements (#1373).

Each aggregate ``Plate`` is one body-local slab whose thickness is not already owned by the
whole-part envelope.  Axis, axial interval and the provider's physical witness locate exactly
one ``PlateFeature``; the compiler's ``thickness.length`` identity then joins that requirement
to a placed, explicitly satisfied, suppressed, dropped, missing, or unverifiable outcome.
Annotation names, views, labels, projections and page coordinates are never correspondence
evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Literal

from b123d_recognisers import RecognitionResult

from draftwright.linting._registry import satisfaction_ids, satisfaction_of
from draftwright.linting.issues import LintIssue, is_placement_drop

PlateRequirementState = Literal[
    "placed",
    "satisfied_by_structured_note",
    "suppressed",
    "inapplicable",
    "dropped",
    "missing",
    "unverifiable",
]
Point = tuple[float, float, float]


@dataclass(frozen=True)
class PlateRequirementOutcome:
    """The observable engine outcome of one physical slab-thickness requirement."""

    source_at: Point | None
    parameter_id: str
    state: PlateRequirementState
    requirement_count: int = 1
    features: tuple = ()
    dependencies: tuple[tuple[object, str], ...] = ()


def _rounded(value) -> float:
    return round(float(value), 3)


def plate_center(plate) -> Point:
    """Reconstruct the body-local slab witness retained on both sides of the IR waist."""
    axis = str(plate.axis)
    index = "xyz".index(axis)
    other = [candidate for candidate in range(3) if candidate != index]
    point = [0.0, 0.0, 0.0]
    point[index] = (_rounded(plate.lo) + _rounded(plate.hi)) / 2
    point[other[0]] = _rounded(plate.u)
    point[other[1]] = _rounded(plate.v)
    return tuple(_rounded(value) for value in point)  # type: ignore[return-value]


def plate_span(plate) -> tuple[Point, Point]:
    """Return the exact physical thickness witness used by ``PlateFeature.parameters``."""
    axis = str(plate.axis)
    index = "xyz".index(axis)
    other = [candidate for candidate in range(3) if candidate != index]
    start = [0.0, 0.0, 0.0]
    end = [0.0, 0.0, 0.0]
    start[index], end[index] = _rounded(plate.lo), _rounded(plate.hi)
    start[other[0]] = end[other[0]] = _rounded(plate.u)
    start[other[1]] = end[other[1]] = _rounded(plate.v)
    return tuple(start), tuple(end)  # type: ignore[return-value]


def plate_key(plate) -> tuple:
    """Every compiler-significant fact retained by the provider record and public IR."""
    return (
        str(plate.axis),
        _rounded(plate.lo),
        _rounded(plate.hi),
        _rounded(plate.u),
        _rounded(plate.v),
    )


def _parameter_id(feature, source) -> str | None:
    try:
        parameters = tuple(feature.parameters())
        if len(parameters) != 1:
            return None
        (parameter,) = parameters
        if parameter.parameter_id != "thickness.length":
            return None
        if _rounded(parameter.value) != _rounded(source.hi - source.lo):
            return None
        if parameter.span is None:
            return None
        span = tuple(tuple(_rounded(component) for component in point) for point in parameter.span)
        if span != plate_span(source):
            return None
    except (AttributeError, TypeError, ValueError):
        return None
    return "thickness.length"


def _index_evidence(registry):
    placed_measurements = [
        (measurement.feature, measurement.parameter)
        for name in registry.names()
        for measurement in registry.measurement_of(name)
    ]
    placed = set(placed_measurements)
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
    return placed, Counter(placed_measurements), satisfied, dropped


def _axis_parameter(axis: str) -> str:
    return {"x": "width.length", "y": "depth.length", "z": "height.length"}[axis]


def _envelope(features):
    values = [item for item in features if getattr(item, "kind", None) == "envelope"]
    return values[0] if len(values) == 1 else None


def _same(value, expected) -> bool:
    return _rounded(value) == _rounded(expected)


def _between(value, bounds) -> bool:
    lo, hi = sorted((_rounded(bounds[0]), _rounded(bounds[1])))
    return lo <= _rounded(value) <= hi


def _step_supports_source(source, step) -> bool:
    """Require the Plate witness to lie on this level record's physical support."""
    try:
        point = plate_center(source)
        supports = tuple(step.level_supports)
        if not supports:
            return False
        if str(source.axis) == "z":
            return any(
                _between(point[0], support.x_span) and _between(point[1], support.y_span)
                for support in supports
            )
        transverse = "x" if str(source.axis) == "y" else "y"
        index = "xyz".index(transverse)
        span_name = f"{transverse}_span"
        return any(_between(point[index], getattr(support, span_name)) for support in supports)
    except (AttributeError, TypeError, ValueError):
        return False


def _slot_pattern_supports_source(source, pattern) -> bool:
    """Join a material web only to a pattern whose physical cut crosses its witness."""
    try:
        member = pattern.member
        if str(source.axis) != str(member.width_axis):
            return False
        point = plate_center(source)
        long_index = "xyz".index(str(member.long_axis))
        depth_axis = str(pattern.frame.axis)
        depth_index = "xyz".index(depth_axis)
        return _between(point[long_index], (member.lo, member.hi)) and _same(
            point[depth_index], pattern.frame.origin[depth_index]
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _recognised_slot_pattern_supports_source(source, slots) -> bool:
    """Use the public source-body bounds retained on provider Slot records."""
    try:
        representative = slots[0]
        axes = {"x", "y", "z"}
        depth_axis = (axes - {str(representative.width_axis), str(representative.long_axis)}).pop()
        point = plate_center(source)
        body_key = tuple(representative.body_key)
        if len(body_key) < 6:
            return False
        body_bounds = tuple(zip(body_key[:3], body_key[3:6], strict=True))
        return (
            _between(
                point["xyz".index(str(representative.long_axis))],
                (representative.lo, representative.hi),
            )
            and _between(
                point["xyz".index(depth_axis)], (representative.d_lo, representative.d_hi)
            )
            and all(_between(point[index], bounds) for index, bounds in enumerate(body_bounds))
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return False


def _polygonal_boss_supports_source(source, boss) -> bool:
    """Conservatively require the Plate witness inside the owner's support polygon."""
    try:
        axis = str(source.axis)
        boss_axis = getattr(getattr(boss, "frame", None), "axis", getattr(boss, "axis", None))
        if str(boss_axis) != axis:
            return False
        indices = [index for index in range(3) if index != "xyz".index(axis)]
        point = tuple(plate_center(source)[index] for index in indices)
        polygon = [
            tuple(float(centre[index]) for index in indices) for centre in boss.flat_centres
        ]
        if len(polygon) < 3:
            return False
        signs = []
        for start, end in zip(polygon, (*polygon[1:], polygon[0]), strict=True):
            cross = (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (
                point[0] - start[0]
            )
            if abs(cross) > 1e-6:
                signs.append(cross > 0)
        return not signs or all(sign == signs[0] for sign in signs)
    except (AttributeError, TypeError, ValueError):
        return False


def _level_continues_beyond_source(source, recognition, boundary: float) -> bool:
    """A support level at the far Plate face proves material continues past that slab."""
    if str(source.axis) != "z":
        return False
    point = plate_center(source)
    try:
        return any(
            _same(level.z, boundary)
            and _between(point[0], level.x_span)
            and _between(point[1], level.y_span)
            for level in recognition.step_levels
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _step_level_dependencies(source, features) -> tuple[tuple[object, str], ...]:
    """Exact legacy level/shoulder grammar that already states a Plate interval."""
    try:
        axis = str(source.axis)
        lo, hi = _rounded(source.lo), _rounded(source.hi)
        envelope = _envelope(features)
        for step in (item for item in features if getattr(item, "kind", None) == "step_level"):
            if not _step_supports_source(source, step):
                continue
            if axis == "z":
                coordinates = tuple(_rounded(value) for value in (step.base, *step.levels))
                if lo not in coordinates or hi not in coordinates:
                    continue
                count = int(lo != _rounded(step.base)) + int(hi != _rounded(step.base))
                if count:
                    return ((step, "step_height.length"),) * count
                continue
            if envelope is None:
                continue
            index = "xyz".index(axis)
            bounds = (_rounded(envelope.bbox_min[index]), _rounded(envelope.bbox_max[index]))
            shoulders = tuple(
                _rounded(position)
                for shoulder_axis, position in step.shoulders
                if str(shoulder_axis) == axis
            )
            if not shoulders:
                continue
            boundaries = {bounds[0], *shoulders, bounds[1]}
            if lo in boundaries and hi in boundaries:
                return (
                    (envelope, _axis_parameter(axis)),
                    *((step, "step_position.length"),) * len(shoulders),
                )
    except (AttributeError, TypeError, ValueError):
        return ()
    return ()


def _polygonal_boss_dependencies(source, features) -> tuple[tuple[object, str], ...]:
    """Envelope minus an attached boss height can state the supporting slab thickness."""
    try:
        axis = str(source.axis)
        index = "xyz".index(axis)
        envelope = _envelope(features)
        if envelope is None:
            return ()
        envelope_interval = (
            _rounded(envelope.bbox_min[index]),
            _rounded(envelope.bbox_max[index]),
        )
        source_interval = (_rounded(source.lo), _rounded(source.hi))
        for boss in (item for item in features if getattr(item, "kind", None) == "polygonal_boss"):
            if (
                str(boss.frame.axis) != axis
                or boss.span is None
                or not _polygonal_boss_supports_source(source, boss)
            ):
                continue
            boss_interval = tuple(sorted(_rounded(point[index]) for point in boss.span))
            supports_below = (
                source_interval[0] == envelope_interval[0]
                and source_interval[1] == boss_interval[0]
                and boss_interval[1] == envelope_interval[1]
            )
            supports_above = (
                boss_interval[0] == envelope_interval[0]
                and boss_interval[1] == source_interval[0]
                and source_interval[1] == envelope_interval[1]
            )
            if supports_below or supports_above:
                return (
                    (envelope, _axis_parameter(axis)),
                    (boss, "boss_height.length"),
                )
    except (AttributeError, TypeError, ValueError):
        return ()
    return ()


def _slot_pattern_dependencies(source, features) -> tuple[tuple[object, str], ...]:
    """Exact patterned slot cuts plus their envelope state every intervening material web."""
    try:
        axis = str(source.axis)
        index = "xyz".index(axis)
        envelope = _envelope(features)
        if envelope is None:
            return ()
        for pattern in (
            item for item in features if getattr(item, "kind", None) == "slot_pattern"
        ):
            member = pattern.member
            if str(member.width_axis) != axis or not _slot_pattern_supports_source(
                source, pattern
            ):
                continue
            half_width = float(member.width) / 2
            cuts = sorted(
                (_rounded(point[index] - half_width), _rounded(point[index] + half_width))
                for point in pattern.members
            )
            material = []
            cursor = _rounded(envelope.bbox_min[index])
            for cut_lo, cut_hi in cuts:
                if cut_lo > cursor:
                    material.append((cursor, cut_lo))
                cursor = max(cursor, cut_hi)
            envelope_hi = _rounded(envelope.bbox_max[index])
            if cursor < envelope_hi:
                material.append((cursor, envelope_hi))
            if (_rounded(source.lo), _rounded(source.hi)) not in material:
                continue
            plane_axes = [candidate for candidate in "xyz" if candidate != pattern.frame.axis]
            return (
                (envelope, _axis_parameter(axis)),
                *((pattern, parameter.parameter_id) for parameter in pattern.parameters()),
                *(
                    (pattern, f"{pattern.LOCATION_STEM}.location.{candidate}")
                    for candidate in plane_axes
                ),
            )
    except (AttributeError, TypeError, ValueError):
        return ()
    return ()


def _envelope_owned_dependencies(source, features) -> tuple[tuple[object, str], ...]:
    """A whole-axis slab is already the envelope size, never a second Plate requirement."""
    try:
        axis = str(source.axis)
        index = "xyz".index(axis)
        envelope = _envelope(features)
        if (
            envelope is not None
            and _same(source.lo, envelope.bbox_min[index])
            and _same(source.hi, envelope.bbox_max[index])
            and all(
                _same(plate_center(source)[other], envelope.frame.origin[other])
                for other in range(3)
                if other != index
            )
        ):
            return ((envelope, _axis_parameter(axis)),)
    except (AttributeError, TypeError, ValueError):
        return ()
    return ()


def _alternate_dependencies(source, features, counts, satisfied) -> tuple[tuple[object, str], ...]:
    for establish in (
        _envelope_owned_dependencies,
        _step_level_dependencies,
        _polygonal_boss_dependencies,
        _slot_pattern_dependencies,
    ):
        if (dependencies := establish(source, features)) and _dependencies_are_evidenced(
            dependencies, counts, satisfied
        ):
            return dependencies
    return ()


def _dependencies_are_evidenced(dependencies, counts, satisfied) -> bool:
    required = Counter(dependencies)
    return all(
        dependency in satisfied or counts[dependency] >= count
        for dependency, count in required.items()
    )


def _recognition_owner_supersedes_plate(source, recognition, features) -> bool:
    """Exact aggregate ownership keeps derived Plate fragments out of a second denominator."""
    envelope = _envelope(features)
    if envelope is None:
        return False
    try:
        axis = str(source.axis)
        index = "xyz".index(axis)
        source_interval = (_rounded(source.lo), _rounded(source.hi))

        for boss in recognition.polygonal_bosses:
            if str(boss.axis) != axis or not _polygonal_boss_supports_source(source, boss):
                continue
            boss_interval = (_rounded(boss.base), _rounded(boss.top))
            supports_below = source_interval[1] == boss_interval[
                0
            ] and not _level_continues_beyond_source(source, recognition, source_interval[0])
            supports_above = source_interval[0] == boss_interval[
                1
            ] and not _level_continues_beyond_source(source, recognition, source_interval[1])
            if supports_below or supports_above:
                return True

        for pattern in recognition.slot_patterns:
            slots = tuple(pattern.slots)
            if not slots or any(str(slot.width_axis) != axis for slot in slots):
                continue
            if not _recognised_slot_pattern_supports_source(source, slots):
                continue
            body_key = tuple(slots[0].body_key)
            if len(body_key) < 6:
                continue
            pattern_interval = (_rounded(body_key[index]), _rounded(body_key[index + 3]))
            cuts = sorted(
                (
                    _rounded(slot.w_center - slot.width / 2),
                    _rounded(slot.w_center + slot.width / 2),
                )
                for slot in slots
            )
            material = []
            cursor = pattern_interval[0]
            for cut_lo, cut_hi in cuts:
                if cut_lo > cursor:
                    material.append((cursor, cut_lo))
                cursor = max(cursor, cut_hi)
            if cursor < pattern_interval[1]:
                material.append((cursor, pattern_interval[1]))
            if source_interval in material:
                return True
    except (AttributeError, TypeError, ValueError):
        return False
    return False


def _derived_opposite_wall_dependencies(features, feature) -> tuple[tuple[object, str], ...]:
    """Return the exact placed facts that derive one U-channel's upper wall."""
    try:
        channels = [item for item in features if getattr(item, "kind", None) == "channel"]
        envelopes = [item for item in features if getattr(item, "kind", None) == "envelope"]
        if len(channels) != 1 or len(envelopes) != 1:
            return ()
        channel = channels[0]
        envelope = envelopes[0]
        axis = str(channel.width_axis)
        if feature.axis != axis:
            return ()
        index = "xyz".index(axis)
        long_index = "xyz".index(str(channel.long_axis))
        bbox_lo = float(envelope.bbox_min[index])
        bbox_hi = float(envelope.bbox_max[index])
        if (
            abs(float(channel.lo) - float(envelope.bbox_min[long_index])) > 1e-6
            or abs(float(channel.hi) - float(envelope.bbox_max[long_index])) > 1e-6
        ):
            return ()
        channel_lo = float(channel.w_center) - float(channel.width) / 2
        channel_hi = float(channel.w_center) + float(channel.width) / 2
        same_axis = [
            item
            for item in features
            if getattr(item, "kind", None) == "plate" and item.axis == axis
        ]
        lower = [
            plate
            for plate in same_axis
            if abs(float(plate.lo) - bbox_lo) <= 1e-6 and abs(float(plate.hi) - channel_lo) <= 1e-6
        ]
        upper = [
            plate
            for plate in same_axis
            if abs(float(plate.lo) - channel_hi) <= 1e-6 and abs(float(plate.hi) - bbox_hi) <= 1e-6
        ]
        if len(lower) != 1 or len(upper) != 1 or feature != upper[0]:
            return ()
        if not (_same(lower[0].u, feature.u) and _same(lower[0].v, feature.v)):
            return ()
        centre = plate_center(feature)
        depth_axis = ({"x", "y", "z"} - {axis, str(channel.long_axis)}).pop()
        if not (
            _between(centre[long_index], (channel.lo, channel.hi))
            and _between(centre["xyz".index(depth_axis)], (channel.d_lo, channel.d_hi))
        ):
            return ()
        envelope_parameter = {"x": "width.length", "y": "depth.length", "z": "height.length"}[axis]
    except (AttributeError, TypeError, ValueError):
        return ()
    return (
        (lower[0], "thickness.length"),
        (channel, "channel_width.length"),
        (envelope, envelope_parameter),
    )


def _state(
    feature,
    parameter,
    *,
    placed,
    satisfied,
    suppressed,
    inapplicable,
    dropped,
    registry,
):
    if (feature, parameter) in inapplicable:
        return "inapplicable"
    if (feature, parameter) in placed:
        return "placed"
    if (feature, parameter) in satisfied:
        return "satisfied_by_structured_note"
    if (feature, parameter) in suppressed:
        return "suppressed"
    if (feature, parameter) in dropped:
        return "dropped"
    associated = registry.names_for_feature(feature)
    if any(
        not registry.measurement_of(name) and not satisfaction_of(registry, name)
        for name in associated
    ):
        return "unverifiable"
    return "missing"


def plate_requirement_outcomes(
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
) -> list[PlateRequirementOutcome]:
    """Follow every recognised body-local slab thickness to its semantic outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError(
            "plate_requirement_outcomes() requires the run's RecognitionResult; "
            f"got {type(recognition).__name__}"
        )
    sources = tuple(recognition.plates)
    if not sources:
        return []

    keyed_sources: list[tuple[object, tuple | None, Point | None]] = []
    source_counts: dict[tuple, int] = defaultdict(int)
    for source in sources:
        try:
            key = plate_key(source)
            at = plate_center(source)
        except (AttributeError, TypeError, ValueError):
            key = None
            at = None
        keyed_sources.append((source, key, at))
        if key is not None:
            source_counts[key] += 1

    ir_by_key: dict[tuple, list] = defaultdict(list)
    for feature in features:
        if getattr(feature, "kind", None) != "plate":
            continue
        try:
            ir_by_key[plate_key(feature)].append(feature)
        except (AttributeError, TypeError, ValueError):
            continue

    placed, placed_counts, satisfied, dropped = _index_evidence(registry)
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    evidenced = placed | satisfied
    derived_dependencies = {
        feature: dependencies
        for feature in features
        if (dependencies := _derived_opposite_wall_dependencies(features, feature))
        and all(dependency in evidenced for dependency in dependencies)
    }
    inapplicable = {(feature, "thickness.length") for feature in derived_dependencies}
    outcomes: list[PlateRequirementOutcome] = []
    for source_record, key, at in keyed_sources:
        matches = ir_by_key.get(key, ()) if key is not None else ()
        feature = (
            matches[0] if key is not None and len(matches) == source_counts[key] == 1 else None
        )
        parameter = _parameter_id(feature, source_record) if feature is not None else None
        if parameter is None:
            dependencies = _alternate_dependencies(
                source_record, features, placed_counts, satisfied
            )
            if dependencies or _recognition_owner_supersedes_plate(
                source_record, recognition, features
            ):
                outcomes.append(
                    PlateRequirementOutcome(
                        at,
                        "thickness.length",
                        "inapplicable",
                        dependencies=dependencies,
                    )
                )
                continue
            outcomes.append(PlateRequirementOutcome(at, "?", "unverifiable"))
            continue
        outcomes.append(
            PlateRequirementOutcome(
                at,
                parameter,
                _state(
                    feature,
                    parameter,
                    placed=placed,
                    satisfied=satisfied,
                    suppressed=suppressed,
                    inapplicable=inapplicable,
                    dropped=dropped,
                    registry=registry,
                ),
                features=(feature,),
                dependencies=derived_dependencies.get(feature, ()),
            )
        )
    return outcomes


def lint_plate_coverage(
    part,
    *,
    recognition: RecognitionResult | None,
    features,
    registry,
    omissions=(),
    assembly=None,
) -> list[LintIssue]:
    """Report uncovered Plate requirements without duplicating placement drops."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    severity: Literal["info", "warning"] = "info" if assembly else "warning"
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to measurement provenance without guessing",
    }
    issues = []
    for outcome in plate_requirement_outcomes(recognition, features, registry, omissions):
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
                code=f"plate_requirement_{outcome.state}",
                message=(
                    f"plate at {outcome.source_at} measurement {outcome.parameter_id} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
