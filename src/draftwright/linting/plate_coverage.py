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
from math import cos, hypot, isclose, isfinite, radians, sin
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


def _depth_axis(width_axis, long_axis) -> str | None:
    width = str(width_axis)
    long = str(long_axis)
    if width not in {"x", "y", "z"} or long not in {"x", "y", "z"} or width == long:
        return None
    remaining = {"x", "y", "z"} - {width, long}
    return next(iter(remaining)) if len(remaining) == 1 else None


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
        depth_axis = _depth_axis(member.width_axis, member.long_axis)
        if depth_axis is None or str(pattern.frame.axis) != depth_axis:
            return False
        point = plate_center(source)
        long_index = "xyz".index(str(member.long_axis))
        depth_index = "xyz".index(depth_axis)
        return _between(point[long_index], (member.lo, member.hi)) and _same(
            point[depth_index], pattern.frame.origin[depth_index]
        )
    except (AttributeError, TypeError, ValueError):
        return False


def _recognised_slot_pattern_supports_source(source, pattern) -> bool:
    """Use the public source-body bounds retained on provider Slot records."""
    try:
        representative = _validated_recognised_pattern(pattern)
        if representative is None:
            return False
        slots = tuple(pattern.slots)
        depth_axis = _depth_axis(representative.width_axis, representative.long_axis)
        assert depth_axis is not None
        point = plate_center(source)
        body_key = tuple(representative.body_key)
        if len(body_key) < 6:
            return False
        body_bounds = tuple(zip(body_key[:3], body_key[3:6], strict=True))
        return any(
            _between(
                point["xyz".index(str(slot.long_axis))],
                (slot.lo, slot.hi),
            )
            and _between(point["xyz".index(depth_axis)], (slot.d_lo, slot.d_hi))
            for slot in slots
        ) and all(_between(point[index], bounds) for index, bounds in enumerate(body_bounds))
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
        return False


def _validated_recognised_slots(slots):
    """Return one representative only when every provider member has one body-local schema."""
    try:
        values = tuple(slots)
        if len(values) < 3:
            return None
        representative = values[0]
        width_axis = str(representative.width_axis)
        long_axis = str(representative.long_axis)
        depth_axis = _depth_axis(width_axis, long_axis)
        body_key = tuple(_rounded(value) for value in representative.body_key)
        if (
            depth_axis is None
            or len(body_key) < 6
            or not all(isfinite(value) for value in body_key)
        ):
            return None
        bounds = tuple(zip(body_key[:3], body_key[3:6], strict=True))
        if any(lo > hi for lo, hi in bounds):
            return None
        shared = (
            width_axis,
            long_axis,
            body_key,
            _rounded(representative.width),
            _rounded(representative.length),
        )
        if shared[3] <= 0 or shared[4] <= 0:
            return None
        width_index = "xyz".index(width_axis)
        long_index = "xyz".index(long_axis)
        depth_index = "xyz".index(depth_axis)
        locations = set()
        for slot in values:
            lo, hi = _rounded(slot.lo), _rounded(slot.hi)
            d_lo, d_hi = _rounded(slot.d_lo), _rounded(slot.d_hi)
            current = (
                str(slot.width_axis),
                str(slot.long_axis),
                tuple(_rounded(value) for value in slot.body_key),
                _rounded(slot.width),
                _rounded(slot.length),
            )
            width = current[3]
            center = float(slot.w_center)
            cut = (center - width / 2, center + width / 2)
            if (
                current != shared
                or _depth_axis(slot.width_axis, slot.long_axis) != depth_axis
                or not all(
                    isfinite(value)
                    for value in (*current[2], *current[3:], lo, hi, d_lo, d_hi, center)
                )
                or width <= 0
                or lo > hi
                or d_lo > d_hi
                or not _same(current[4], hi - lo)
                or not bounds[width_index][0] <= cut[0] < cut[1] <= bounds[width_index][1]
                or not bounds[long_index][0] <= lo < hi <= bounds[long_index][1]
                or not bounds[depth_index][0] <= d_lo < d_hi <= bounds[depth_index][1]
            ):
                return None
            locations.add((_rounded(center), _rounded((lo + hi) / 2), _rounded((d_lo + d_hi) / 2)))
        if len(locations) != len(values):
            return None
        return representative
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None


def _slot_location(slot) -> Point:
    values = tuple(float(value) for value in slot.location)
    if len(values) != 3 or not all(isfinite(value) for value in values):
        raise ValueError("a slot location must be one finite 3-vector")
    return values


def _pattern_tolerance(nominal: float) -> float:
    """Public provider modelling tolerance: a 0.1 mm floor or two percent of pitch."""
    return max(0.1, abs(nominal) * 0.02)


def _validated_recognised_pattern(pattern):
    """Validate the aggregate lattice as well as every provider Slot member."""
    try:
        slots = tuple(pattern.slots)
        representative = _validated_recognised_slots(slots)
        if representative is None:
            return None
        locations = tuple(_slot_location(slot) for slot in slots)
        depth_axis = _depth_axis(representative.width_axis, representative.long_axis)
        assert depth_axis is not None
        depth_index = "xyz".index(depth_axis)
        coordinate_tolerance = 2e-3

        is_linear = all(hasattr(pattern, name) for name in ("pitch", "direction"))
        is_grid = all(
            hasattr(pattern, name)
            for name in ("rows", "cols", "row_pitch", "col_pitch", "angle", "center")
        )
        if is_linear == is_grid:
            return None

        if is_linear:
            pitch = float(pattern.pitch)
            direction = tuple(float(value) for value in pattern.direction)
            if (
                len(direction) != 3
                or not all(isfinite(value) for value in (*direction, pitch))
                or pitch <= 0
            ):
                return None
            norm = hypot(*direction)
            if not isclose(norm, 1.0, abs_tol=1e-3) or abs(direction[depth_index]) > 1e-6:
                return None
            tolerance = _pattern_tolerance(pitch)
            unit = tuple(value / norm for value in direction)
            origin = locations[0]
            projections = []
            for location in locations:
                delta = tuple(value - base for value, base in zip(location, origin, strict=True))
                projection = sum(
                    value * component for value, component in zip(delta, unit, strict=True)
                )
                residual = tuple(
                    value - projection * component
                    for value, component in zip(delta, unit, strict=True)
                )
                if hypot(*residual) > tolerance:
                    return None
                projections.append(projection)
            ordered = sorted(projections)
            if any(
                not isclose(current - previous, pitch, abs_tol=tolerance)
                for previous, current in zip(ordered, ordered[1:])
            ):
                return None
            return representative

        rows, cols = pattern.rows, pattern.cols
        if (
            type(rows) is not int
            or type(cols) is not int
            or rows < 2
            or cols < 2
            or max(rows, cols) < 3
            or rows * cols != len(slots)
        ):
            return None
        row_pitch = float(pattern.row_pitch)
        col_pitch = float(pattern.col_pitch)
        angle = float(pattern.angle)
        center = tuple(float(value) for value in pattern.center)
        if (
            len(center) != 3
            or not all(isfinite(value) for value in (*center, row_pitch, col_pitch, angle))
            or row_pitch <= 0
            or col_pitch <= 0
        ):
            return None
        mean = tuple(
            sum(point[index] for point in locations) / len(locations) for index in range(3)
        )
        if any(
            not isclose(value, expected, abs_tol=coordinate_tolerance)
            for value, expected in zip(mean, center, strict=True)
        ):
            return None

        plane_axes = {
            "x": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            "y": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
            "z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        }
        first, second = plane_axes[depth_axis]
        theta = radians(angle % 180.0)
        col_direction = tuple(
            cos(theta) * u + sin(theta) * v for u, v in zip(first, second, strict=True)
        )
        row_direction = tuple(
            -sin(theta) * u + cos(theta) * v for u, v in zip(first, second, strict=True)
        )
        row_tolerance = _pattern_tolerance(row_pitch)
        col_tolerance = _pattern_tolerance(col_pitch)
        expected = []
        for row in range(rows):
            row_offset = (row - (rows - 1) / 2) * row_pitch
            for col in range(cols):
                col_offset = (col - (cols - 1) / 2) * col_pitch
                expected.append(
                    tuple(
                        center[index]
                        + row_offset * row_direction[index]
                        + col_offset * col_direction[index]
                        for index in range(3)
                    )
                )
        unmatched = list(expected)

        def matches(location, candidate) -> bool:
            delta = tuple(
                value - wanted for value, wanted in zip(location, candidate, strict=True)
            )
            row_error = abs(
                sum(
                    value * component
                    for value, component in zip(delta, row_direction, strict=True)
                )
            )
            col_error = abs(
                sum(
                    value * component
                    for value, component in zip(delta, col_direction, strict=True)
                )
            )
            return bool(
                row_error <= row_tolerance
                and col_error <= col_tolerance
                and abs(delta[depth_index]) <= coordinate_tolerance
            )

        for location in locations:
            match = next(
                (candidate for candidate in unmatched if matches(location, candidate)),
                None,
            )
            if match is None:
                return None
            unmatched.remove(match)
        return representative if not unmatched else None
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError):
        return None


def _polygonal_boss_supports_source(source, boss) -> bool:
    """Conservatively require the Plate witness inside the owner's support polygon."""
    try:
        axis = str(source.axis)
        boss_axis = getattr(getattr(boss, "frame", None), "axis", getattr(boss, "axis", None))
        if str(boss_axis) != axis:
            return False
        side_count = boss.side_count
        directions = tuple(boss.flat_directions)
        centres = tuple(boss.flat_centres)
        if (
            type(side_count) is not int
            or side_count < 4
            or side_count % 2
            or len(directions) != side_count
            or len(centres) != side_count
            or len(set(directions)) != side_count
            or len(set(centres)) != side_count
        ):
            return False
        indices = [index for index in range(3) if index != "xyz".index(axis)]
        point = tuple(plate_center(source)[index] for index in indices)
        polygon = [tuple(float(centre[index]) for index in indices) for centre in centres]
        signs = []
        for start, end in zip(polygon, (*polygon[1:], polygon[0]), strict=True):
            cross = (end[0] - start[0]) * (point[1] - start[1]) - (end[1] - start[1]) * (
                point[0] - start[0]
            )
            if abs(cross) > 1e-6:
                signs.append(cross > 0)
        return bool(signs) and all(sign == signs[0] for sign in signs)
    except (AttributeError, TypeError, ValueError):
        return False


def _valid_polygonal_boss_source(boss) -> bool:
    try:
        from draftwright.linting.polygonal_boss_coverage import (
            _validate_polygonal_boss_source,
        )

        _validate_polygonal_boss_source(boss)
        return True
    except (AttributeError, OverflowError, TypeError, ValueError):
        return False


class _SolidMembership:
    """Cache physical-witness ownership for one completeness-ledger invocation."""

    def __init__(self, part) -> None:
        self.solids = tuple(part.solids()) if part is not None else ()
        self._owners: dict[Point, tuple[int, ...]] = {}

    def owners(self, point) -> tuple[int, ...]:
        values = tuple(float(value) for value in point)
        if len(values) != 3 or not all(isfinite(value) for value in values):
            raise ValueError("a solid-membership witness must be a 3-vector")
        key = (values[0], values[1], values[2])
        if key not in self._owners:
            from build123d import Vector

            witness = Vector(*key)
            self._owners[key] = tuple(
                index for index, solid in enumerate(self.solids) if solid.is_inside(witness)
            )
        return self._owners[key]


def _boss_material_witnesses(boss) -> tuple[Point, ...]:
    """Move just inside each outer flat so a coaxial bore cannot erase boss ownership."""
    points = []
    for direction, centre in zip(boss.flat_directions, boss.flat_centres, strict=True):
        normals = tuple(float(normal) for normal in direction)
        coordinates = tuple(float(component) for component in centre)
        if len(normals) != 3 or len(coordinates) != 3:
            raise ValueError("a boss material witness must be a paired 3-vector")
        points.append(
            tuple(
                float(component) - 1e-3 * float(normal)
                for component, normal in zip(coordinates, normals, strict=True)
            )
        )
    return tuple((point[0], point[1], point[2]) for point in points)


def _shares_one_solid(membership, source, boss) -> bool:
    """Prove the complete boss ring and one material Plate witness share one solid."""
    if membership is None or len(membership.solids) != 1:
        return False
    try:
        witnesses = _boss_material_witnesses(boss)
        if not witnesses:
            return False
        boss_owners = tuple(membership.owners(point) for point in witnesses)
        if any(len(owners) != 1 or owners != boss_owners[0] for owners in boss_owners):
            return False
        source_center_owners = membership.owners(plate_center(source))
        if source_center_owners:
            return len(source_center_owners) == 1 and source_center_owners == boss_owners[0]
        # With one physical solid, complete boss-ring ownership proves the Plate and boss share
        # it even when a bore removes the retained Plate centroid.  With plural solids the Plate
        # record has no body key, so a point inside/nearest another body is not correspondence.
        return bool(boss_owners[0] == (0,))
    except (AttributeError, OverflowError, TypeError, ValueError):
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


def _alternate_dependencies(
    source,
    features,
    counts,
    satisfied,
) -> tuple[tuple[object, str], ...]:
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


def _without_provider_owned_ir(recognition, features) -> tuple:
    """Keep declared owners while withholding exact IR compiled from provider records."""
    from draftwright.linting.polygonal_boss_coverage import polygonal_boss_key

    boss_keys = []
    boss_sources = tuple(recognition.polygonal_bosses)
    boss_inventory_untrusted = False
    for source in boss_sources:
        try:
            if not _valid_polygonal_boss_source(source):
                boss_inventory_untrusted = True
                continue
            boss_keys.append(polygonal_boss_key(source))
        except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
            boss_inventory_untrusted = True
            continue
    slot_sources = tuple(recognition.slot_patterns)
    slot_inventory_untrusted = any(
        _validated_recognised_pattern(source) is None for source in slot_sources
    )
    remaining = []
    for feature in features:
        kind = getattr(feature, "kind", None)
        try:
            provider_owned = bool(
                kind == "polygonal_boss"
                and (boss_inventory_untrusted or polygonal_boss_key(feature) in boss_keys)
            ) or bool(
                kind == "slot_pattern"
                and (
                    slot_inventory_untrusted
                    or any(_slot_pattern_corresponds(source, feature) for source in slot_sources)
                )
            )
        except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
            provider_owned = False
        if not provider_owned:
            remaining.append(feature)
    return tuple(remaining)


def _slot_pattern_corresponds(source, feature) -> bool:
    """Join provider pattern IR exactly, with a partial-location fallback for malformed members."""
    from draftwright.linting.slot_coverage import _pattern_key, _slot_spec_key

    try:
        return _pattern_key(source) == _pattern_key(feature)
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
        pass
    try:
        slots = tuple(source.slots)
        if (
            not slots
            or feature.pattern != "linear"
            or feature.count != len(slots)
            or _slot_spec_key(slots[0]) != _slot_spec_key(feature.member)
            or _rounded(source.pitch) != _rounded(feature.pitch)
        ):
            return False
        retained_locations = []
        for slot in slots:
            try:
                retained_locations.append(tuple(_rounded(value) for value in slot.location))
            except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
                continue
        feature_locations = {
            tuple(_rounded(value) for value in point) for point in feature.members
        }
        return bool(retained_locations) and all(
            location in feature_locations for location in retained_locations
        )
    except (AttributeError, IndexError, KeyError, OverflowError, TypeError, ValueError):
        return False


def _dependencies_are_evidenced(dependencies, counts, satisfied) -> bool:
    required = Counter(dependencies)
    return all(
        dependency in satisfied or counts[dependency] >= count
        for dependency, count in required.items()
    )


def _recognition_owner_supersedes_plate(source, recognition, features, membership=None) -> bool:
    """Exact aggregate ownership keeps derived Plate fragments out of a second denominator."""
    envelope = _envelope(features)
    if envelope is None:
        return False
    try:
        axis = str(source.axis)
        index = "xyz".index(axis)
        envelope_interval = (
            _rounded(envelope.bbox_min[index]),
            _rounded(envelope.bbox_max[index]),
        )
        source_interval = (_rounded(source.lo), _rounded(source.hi))

        for boss in recognition.polygonal_bosses:
            if str(boss.axis) != axis or not _valid_polygonal_boss_source(boss):
                continue
            boss_interval = (_rounded(boss.base), _rounded(boss.top))
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
            if (supports_below or supports_above) and _shares_one_solid(membership, source, boss):
                return True

        for pattern in recognition.slot_patterns:
            slots = tuple(pattern.slots)
            representative = _validated_recognised_pattern(pattern)
            if representative is None or str(representative.width_axis) != axis:
                continue
            if not _recognised_slot_pattern_supports_source(source, pattern):
                continue
            body_key = tuple(representative.body_key)
            pattern_interval = (_rounded(body_key[index]), _rounded(body_key[index + 3]))
            point = plate_center(source)
            depth_axis = _depth_axis(representative.width_axis, representative.long_axis)
            if depth_axis is None:
                continue
            long_index = "xyz".index(str(representative.long_axis))
            depth_index = "xyz".index(depth_axis)
            crossing = (
                slot
                for slot in slots
                if _between(point[long_index], (slot.lo, slot.hi))
                and _between(point[depth_index], (slot.d_lo, slot.d_hi))
            )
            cuts = sorted(
                {
                    (
                        _rounded(slot.w_center - slot.width / 2),
                        _rounded(slot.w_center + slot.width / 2),
                    )
                    for slot in crossing
                }
            )
            if not cuts:
                continue
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
        depth_axis = _depth_axis(axis, channel.long_axis)
        if depth_axis is None:
            return ()
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
    *,
    part=None,
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
    membership = _SolidMembership(part) if part is not None else None
    alternate_features = _without_provider_owned_ir(recognition, features)
    outcomes: list[PlateRequirementOutcome] = []
    for source_record, key, at in keyed_sources:
        matches = ir_by_key.get(key, ()) if key is not None else ()
        feature = (
            matches[0] if key is not None and len(matches) == source_counts[key] == 1 else None
        )
        parameter = _parameter_id(feature, source_record) if feature is not None else None
        if parameter is None:
            recognised_owner = _recognition_owner_supersedes_plate(
                source_record, recognition, features, membership
            )
            dependencies = _alternate_dependencies(
                source_record,
                alternate_features,
                placed_counts,
                satisfied,
            )
            if dependencies or recognised_owner:
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
    for outcome in plate_requirement_outcomes(
        recognition, features, registry, omissions, part=part
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
                code=f"plate_requirement_{outcome.state}",
                message=(
                    f"plate at {outcome.source_at} measurement {outcome.parameter_id} "
                    f"{messages[outcome.state]}"
                ),
            )
        )
    return issues
