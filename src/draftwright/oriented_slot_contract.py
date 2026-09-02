"""Strict shared boundary contract for released oriented-slot records (#1432).

This leaf is used by both the recognition adapter and the independent completeness
observer.  Keeping one validator prevents those two trust boundaries from disagreeing while
preserving ADR 0015's rule that ``linting`` must not import the compiler model.
"""

from __future__ import annotations

from math import dist, hypot, isfinite
from numbers import Real

from b123d_recognisers import (
    OrientedSlot,
    OrientedSlotArray,
    OrientedSlotGrid,
    PassageEnds,
    PassageFrame,
    PassageSection,
    PassageSectionVertex,
    SectionPassage,
    recognise_oriented_slot_patterns,
)


def _real(value, *, name: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite real number") from exc
    if not isfinite(result) or (positive and result <= 0.0):
        raise ValueError(f"{name} must be a finite real number")
    return result


def _integer(value, *, name: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer of at least {minimum}")
    return value


def _vector(value, *, size: int, name: str) -> tuple[float, ...]:
    if type(value) is not tuple or len(value) != size:
        raise ValueError(f"{name} must be an immutable {size}-vector")
    return tuple(_real(component, name=f"{name} component") for component in value)


def _serialized(values: tuple[float, ...], digits: int, *, name: str) -> None:
    if any(value != round(value, digits) for value in values):
        raise ValueError(f"{name} must use the released {digits}-decimal serialization")


def _unit(value, *, name: str, squared_tolerance: float = 4e-5) -> tuple[float, float, float]:
    result = _vector(value, size=3, name=name)
    if abs(_dot(result, result) - 1.0) > squared_tolerance:
        raise ValueError(f"{name} must be unit length")
    return (result[0], result[1], result[2])


def _dot(first: tuple[float, ...], second: tuple[float, ...]) -> float:
    return float(sum(left * right for left, right in zip(first, second, strict=True)))


def _cross(first, second) -> tuple[float, float, float]:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _normalised(value) -> tuple[float, float, float]:
    length = hypot(*value)
    return tuple(component / length for component in value)


def _validate_frame(origin, run, u, v) -> None:
    if any(abs(_dot(first, second)) > 2e-6 for first, second in ((run, u), (run, v), (u, v))):
        raise ValueError("oriented slot passage frame must be orthogonal")
    if max(abs(left - right) for left, right in zip(_cross(run, u), v, strict=True)) > 3e-6:
        raise ValueError("oriented slot passage frame must be right handed")
    rounded = tuple(round(abs(value), 6) for value in run)
    peak = max(rounded)
    dominant = next(index for index in (2, 1, 0) if rounded[index] == peak)
    if run[dominant] < -3e-6:
        raise ValueError("oriented slot passage run is not in the canonical gauge")
    seed = ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))[dominant]
    normalised_run = _normalised(run)
    expected_u = _normalised(
        tuple(
            seed[index] - _dot(seed, normalised_run) * normalised_run[index] for index in range(3)
        )
    )
    expected_v = _cross(normalised_run, expected_u)
    if dist(u, expected_u) > 3e-6 or dist(v, expected_v) > 3e-6:
        raise ValueError("oriented slot passage in-plane basis is not canonical")
    if abs(_dot(origin, run)) > 8e-4:
        raise ValueError("oriented slot passage origin must be the canonical run-axis foot")


def _canonical_direction(u, v, edge) -> tuple[float, float, float]:
    local_length = hypot(*edge)
    local = (edge[0] / local_length, edge[1] / local_length)
    result = tuple(local[0] * u[index] + local[1] * v[index] for index in range(3))
    result = _normalised(result)
    pivot = max(range(3), key=lambda index: (abs(result[index]), index))
    if result[pivot] < 0:
        result = tuple(-component for component in result)
    return result


def _rectangle(u, v, boundary):
    points = tuple(point for point, bulge in boundary if bulge == 0.0)
    if len(points) != 4:
        raise ValueError("oriented slot passage boundary must be straight")
    if (
        hypot(
            sum(point[0] for point in points) / 4.0,
            sum(point[1] for point in points) / 4.0,
        )
        > 8e-4
    ):
        raise ValueError("oriented slot passage boundary must be origin-centred")
    edges = tuple(
        tuple(points[(index + 1) % 4][axis] - point[axis] for axis in range(2))
        for index, point in enumerate(points)
    )
    lengths = tuple(hypot(*edge) for edge in edges)
    error = 0.004
    if min(lengths) <= 2 * error:
        raise ValueError("oriented slot rectangle edges are too short")
    if any(abs(lengths[index] - lengths[index + 2]) > error for index in (0, 1)):
        raise ValueError("oriented slot opposite-edge lengths disagree")
    if any(
        hypot(*(edges[index][axis] + edges[index + 2][axis] for axis in range(2))) > error
        for index in (0, 1)
    ):
        raise ValueError("oriented slot opposite edges are not parallel")
    if abs(_dot(edges[0], edges[1]) / (lengths[0] * lengths[1])) > error / min(lengths):
        raise ValueError("oriented slot adjacent edges are not orthogonal")
    if abs(lengths[0] - lengths[1]) <= error:
        raise ValueError("oriented slot rectangle has no distinct long direction")
    long_at = 0 if lengths[0] > lengths[1] else 1
    width_at = 1 - long_at
    return (
        _canonical_direction(u, v, edges[width_at]),
        _canonical_direction(u, v, edges[long_at]),
        lengths[width_at],
        lengths[long_at],
    )


def oriented_slot_provider_key(slot) -> tuple:
    """Validate one exact released record and return its lossless adapter identity."""
    if type(slot) is not OrientedSlot:
        raise TypeError("oriented slot inventory members must be OrientedSlot records")
    source = slot.source
    if (
        type(source) is not SectionPassage
        or type(source.frame) is not PassageFrame
        or type(source.section) is not PassageSection
        or type(source.ends) is not PassageEnds
    ):
        raise TypeError("oriented slot source must use the released passage record schema")
    center = _vector(slot.center, size=3, name="oriented slot center")
    width_direction = _unit(slot.width_direction, name="oriented slot width_direction")
    long_direction = _unit(slot.long_direction, name="oriented slot long_direction")
    width = _real(slot.width, name="oriented slot width", positive=True)
    length = _real(slot.length, name="oriented slot length", positive=True)
    origin = _vector(source.frame.origin, size=3, name="oriented slot passage origin")
    run = _unit(
        source.frame.run,
        name="oriented slot passage run",
        squared_tolerance=1e-6,
    )
    u = _unit(source.frame.u, name="oriented slot passage u", squared_tolerance=1e-6)
    v = _unit(source.frame.v, name="oriented slot passage v", squared_tolerance=1e-6)
    _serialized(center, 3, name="oriented slot center")
    _serialized(width_direction, 6, name="oriented slot width direction")
    _serialized(long_direction, 6, name="oriented slot long direction")
    _serialized((width, length), 3, name="oriented slot dimensions")
    _serialized(origin, 3, name="oriented slot passage origin")
    _serialized(run, 6, name="oriented slot passage run")
    _serialized(u, 6, name="oriented slot passage u")
    _serialized(v, 6, name="oriented slot passage v")
    _validate_frame(origin, run, u, v)
    width_long_tolerance = max(3e-5, 0.004 / min(width, length) + 3e-6)
    if abs(_dot(width_direction, long_direction)) > width_long_tolerance:
        raise ValueError("oriented slot width/long directions must be orthogonal")
    if abs(_dot(width_direction, run)) > 3e-5 or abs(_dot(long_direction, run)) > 3e-5:
        raise ValueError("oriented slot dimensions must be perpendicular to its passage run")
    if type(source.run_interval) is not tuple or len(source.run_interval) != 2:
        raise ValueError("oriented slot run interval must contain two values")
    lo, hi = (_real(value, name="oriented slot run interval") for value in source.run_interval)
    _serialized((lo, hi), 3, name="oriented slot run interval")
    if hi <= lo:
        raise ValueError("oriented slot run interval must increase")
    boundary_source = source.section.boundary
    if type(boundary_source) is not tuple or len(boundary_source) != 4:
        raise ValueError("oriented slot boundary must contain four vertices")
    boundary = []
    for vertex in boundary_source:
        if type(vertex) is not PassageSectionVertex:
            raise TypeError("oriented slot boundary must use released vertex records")
        raw_point = _vector(vertex.point, size=2, name="oriented slot boundary point")
        point = (raw_point[0], raw_point[1])
        bulge = _real(vertex.bulge, name="oriented slot boundary bulge")
        _serialized(point, 3, name="oriented slot boundary point")
        _serialized((bulge,), 12, name="oriented slot boundary bulge")
        boundary.append((point, bulge))
    try:
        # Reconstruct through the released public value type so cyclic shifts, reversed
        # windings, and any other non-canonical serialization fail at the same boundary as
        # the provider itself.  This is validation only; the original exact facts remain the
        # lossless occurrence key.
        PassageSection(tuple(PassageSectionVertex(point, bulge) for point, bulge in boundary))
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("oriented slot boundary must use the canonical public ordering") from exc
    if type(source.ends.low_capped) is not bool or type(source.ends.high_capped) is not bool:
        raise ValueError("oriented slot end states must be booleans")
    if source.ends.low_capped or source.ends.high_capped:
        raise ValueError("oriented slot passage must be open through both ends")
    if slot.body_key is None:
        body_key = None
    else:
        if type(slot.body_key) is not tuple:
            raise ValueError("oriented slot body key must be an immutable tuple")
        body_key = tuple(
            _real(value, name="oriented slot body key value") for value in slot.body_key
        )
    derived_width_direction, derived_long_direction, derived_width, derived_length = _rectangle(
        u, v, boundary
    )
    expected_width_direction = tuple(round(value, 6) for value in derived_width_direction)
    expected_long_direction = tuple(round(value, 6) for value in derived_long_direction)
    expected_center = tuple(
        round(origin[index] + 0.5 * (lo + hi) * run[index], 3) for index in range(3)
    )
    if (
        width_direction != expected_width_direction
        or long_direction != expected_long_direction
        or width != round(derived_width, 3)
        or length != round(derived_length, 3)
        or center != expected_center
    ):
        raise ValueError("oriented slot claims must be the public projection of its passage")
    if (
        max(abs(value) for value in width_direction) >= 0.99
        and max(abs(value) for value in long_direction) >= 0.99
    ):
        raise ValueError("principal rectangular passages belong to the legacy slot family")
    expected_axis = "xyz"[max(range(3), key=lambda index: abs(run[index]))]
    return (
        center,
        expected_axis,
        width_direction,
        long_direction,
        width,
        length,
        origin,
        run,
        u,
        v,
        (lo, hi),
        tuple(boundary),
        source.ends.low_capped,
        source.ends.high_capped,
        body_key,
    )


def _pattern_key(pattern, member_keys: tuple[tuple, ...]) -> tuple:
    if type(pattern) is OrientedSlotArray:
        pitch = _real(pattern.pitch, name="oriented slot array pitch", positive=True)
        direction = _unit(pattern.direction, name="oriented slot array direction")
        _serialized(direction, 6, name="oriented slot array direction")
        return (OrientedSlotArray, member_keys, pitch, direction)
    if type(pattern) is OrientedSlotGrid:
        rows = _integer(pattern.rows, name="oriented slot grid rows", minimum=2)
        cols = _integer(pattern.cols, name="oriented slot grid cols", minimum=2)
        if rows * cols != len(member_keys):
            raise ValueError("oriented slot grid shape must own every member exactly once")
        row_pitch = _real(pattern.row_pitch, name="oriented slot grid row pitch", positive=True)
        col_pitch = _real(pattern.col_pitch, name="oriented slot grid col pitch", positive=True)
        angle = _real(pattern.angle, name="oriented slot grid angle")
        center = _vector(pattern.center, size=3, name="oriented slot grid center")
        _serialized(center, 3, name="oriented slot grid center")
        return (
            OrientedSlotGrid,
            member_keys,
            rows,
            cols,
            row_pitch,
            col_pitch,
            angle,
            center,
        )
    raise TypeError("oriented slot pattern inventory contains an unknown record")


def validate_oriented_slot_pattern(pattern) -> tuple[tuple[OrientedSlot, tuple], ...]:
    """Validate pattern schema and public projection without record-level equality."""
    if type(pattern) not in (OrientedSlotArray, OrientedSlotGrid):
        raise TypeError("oriented slot pattern inventory contains an unknown record")
    if type(pattern.slots) is not tuple or len(pattern.slots) < 3:
        raise ValueError("oriented slot pattern members must be an immutable tuple of at least 3")
    members = tuple((member, oriented_slot_provider_key(member)) for member in pattern.slots)
    actual = _pattern_key(pattern, tuple(key for _member, key in members))
    try:
        projected = recognise_oriented_slot_patterns(pattern.slots)
    except (AttributeError, IndexError, OverflowError, TypeError, ValueError) as exc:
        raise ValueError("oriented slot pattern projection failed") from exc
    if len(projected) != 1:
        raise ValueError("oriented slot pattern does not have one public projection")
    projected_pattern = projected[0]
    if type(projected_pattern.slots) is not tuple:
        raise ValueError("projected oriented slot members must be immutable")
    projected_members = tuple(
        oriented_slot_provider_key(member) for member in projected_pattern.slots
    )
    expected = _pattern_key(projected_pattern, projected_members)
    if actual != expected:
        raise ValueError("oriented slot pattern does not match the public projection")
    return members
