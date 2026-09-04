"""Shared Plate-record to IR correspondence below conversion and completeness consumers."""

from __future__ import annotations

Point = tuple[float, float, float]


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


def plate_owner_dependencies(source, features) -> tuple[tuple[object, str], ...]:
    """Return the first exact final-IR dependency set that states this Plate interval."""

    for establish in (
        _envelope_owned_dependencies,
        _step_level_dependencies,
        _polygonal_boss_dependencies,
        _slot_pattern_dependencies,
    ):
        if dependencies := establish(source, features):
            return dependencies
    return ()
