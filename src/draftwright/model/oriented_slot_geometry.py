"""Private validation of oriented-slot IR passage and feature geometry.

The public dataclasses stay in ``model.ir``; this rank-0 leaf imports no engine
module, so detection and declaration keep one shared IR boundary.
"""

from __future__ import annotations

from math import hypot, isclose, isfinite

Point = tuple[float, float, float]


def _strict_finite_point3(name: str, value, strict_real) -> Point:
    try:
        components = tuple(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be a finite real-number 3-vector") from exc
    if len(components) != 3:
        raise ValueError(f"{name} must be a finite real-number 3-vector")
    try:
        result = tuple(strict_real(f"{name} component", item) for item in components)
    except ValueError as exc:
        raise ValueError(f"{name} must be a finite real-number 3-vector") from exc
    return (result[0], result[1], result[2])


_ORIENTED_SLOT_SERIALIZATION_ERROR = 0.004


def _oriented_slot_cross(first: Point, second: Point) -> Point:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _validate_oriented_slot_frame(passage) -> None:
    """Enforce the released passage's one right-handed, canonical frame gauge."""
    cross = _oriented_slot_cross(passage.run, passage.u)
    if hypot(*(left - right for left, right in zip(cross, passage.v, strict=True))) > 3e-6:
        raise ValueError("oriented slot passage frame must be right handed")
    rounded = tuple(round(abs(value), 6) for value in passage.run)
    peak = max(rounded)
    dominant = next(index for index in (2, 1, 0) if rounded[index] == peak)
    if passage.run[dominant] < -3e-6:
        raise ValueError("oriented slot passage run is not in the canonical gauge")
    seed = ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))[dominant]
    run_norm = hypot(*passage.run)
    run = tuple(value / run_norm for value in passage.run)
    projection = sum(left * right for left, right in zip(seed, run, strict=True))
    raw_u = tuple(seed[index] - projection * run[index] for index in range(3))
    u_norm = hypot(*raw_u)
    expected_u = tuple(value / u_norm for value in raw_u)
    expected_v = _oriented_slot_cross(run, expected_u)
    if (
        hypot(*(left - right for left, right in zip(passage.u, expected_u, strict=True))) > 3e-6
        or hypot(*(left - right for left, right in zip(passage.v, expected_v, strict=True))) > 3e-6
    ):
        raise ValueError("oriented slot passage in-plane basis is not canonical")
    if (
        abs(sum(left * right for left, right in zip(passage.origin, passage.run, strict=True)))
        > 8e-4
    ):
        raise ValueError("oriented slot passage origin must be the canonical run-axis foot")


def _validate_oriented_slot_boundary_order(boundary) -> None:
    """Require the released section's positive winding and lexicographically first start."""
    points = tuple(point for point, _bulge in boundary)
    twice_area = sum(
        point[0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * point[1]
        for index, point in enumerate(points)
    )
    if twice_area <= 0.0:
        raise ValueError("oriented slot passage boundary must use canonical positive winding")
    rotations = tuple(boundary[index:] + boundary[:index] for index in range(len(boundary)))
    if tuple(boundary) != min(rotations):
        raise ValueError("oriented slot passage boundary must use its canonical start vertex")


def _canonical_oriented_direction(passage, edge) -> Point:
    length = hypot(*edge)
    local = (edge[0] / length, edge[1] / length)
    result = tuple(local[0] * passage.u[index] + local[1] * passage.v[index] for index in range(3))
    norm = hypot(*result)
    result = tuple(component / norm for component in result)
    pivot = max(range(3), key=lambda index: (abs(result[index]), index))
    if result[pivot] < 0:
        result = tuple(-component for component in result)
    return (result[0], result[1], result[2])


def _oriented_slot_rectangle(passage) -> tuple[Point, Point, float, float]:
    """Reconstruct the rectangular-section claims carried by a passage witness."""
    if passage.low_capped or passage.high_capped:
        raise ValueError("oriented slot passage must be open through both ends")
    if any(bulge != 0.0 for _point, bulge in passage.boundary):
        raise ValueError("oriented slot passage boundary must contain only straight edges")
    points = tuple(point for point, _bulge in passage.boundary)
    edges = tuple(
        tuple(points[(index + 1) % 4][axis] - point[axis] for axis in range(2))
        for index, point in enumerate(points)
    )
    lengths = tuple(hypot(*edge) for edge in edges)
    error = _ORIENTED_SLOT_SERIALIZATION_ERROR
    if min(lengths) <= 2 * error:
        raise ValueError("oriented slot passage rectangle edges are too short")
    if any(abs(lengths[index] - lengths[index + 2]) > error for index in (0, 1)):
        raise ValueError("oriented slot passage opposite edges must have equal length")
    if any(
        hypot(*(edges[index][axis] + edges[index + 2][axis] for axis in range(2))) > error
        for index in (0, 1)
    ):
        raise ValueError("oriented slot passage opposite edges must be parallel")
    orthogonal_error = error / min(lengths[0], lengths[1])
    dot = sum(edges[0][axis] * edges[1][axis] for axis in range(2))
    if abs(dot / (lengths[0] * lengths[1])) > orthogonal_error:
        raise ValueError("oriented slot passage adjacent edges must be orthogonal")
    if abs(lengths[0] - lengths[1]) <= error:
        raise ValueError("oriented slot passage must have distinct width and length")
    long_at = 0 if lengths[0] > lengths[1] else 1
    width_at = 1 - long_at
    return (
        _canonical_oriented_direction(passage, edges[width_at]),
        _canonical_oriented_direction(passage, edges[long_at]),
        lengths[width_at],
        lengths[long_at],
    )


def validate_passage(self, strict_real) -> None:
    if not isinstance(self.low_capped, bool) or not isinstance(self.high_capped, bool):
        raise ValueError("oriented slot passage end states must be booleans")
    for name in ("origin", "run", "u", "v"):
        object.__setattr__(
            self, name, _strict_finite_point3(name, getattr(self, name), strict_real)
        )
    for name in ("run", "u", "v"):
        direction = getattr(self, name)
        if abs(hypot(*direction) - 1.0) > 1e-6 + 1e-12:
            raise ValueError(f"oriented slot passage {name} must be unit length")
    for first, second in (("run", "u"), ("run", "v"), ("u", "v")):
        dot = sum(
            left * right
            for left, right in zip(getattr(self, first), getattr(self, second), strict=True)
        )
        if not isclose(dot, 0.0, rel_tol=0.0, abs_tol=2e-6):
            raise ValueError(f"oriented slot passage {first}/{second} must be orthogonal")
    _validate_oriented_slot_frame(self)
    try:
        interval = tuple(self.run_interval)
        if len(interval) != 2:
            raise ValueError
        lo, hi = (strict_real("oriented slot run_interval value", value) for value in interval)
    except (TypeError, ValueError) as exc:
        raise ValueError("oriented slot run_interval must contain two finite values") from exc
    if not (isfinite(lo) and isfinite(hi) and hi > lo):
        raise ValueError("oriented slot run_interval must be finite and increasing")
    object.__setattr__(self, "run_interval", (lo, hi))
    clean_boundary: list[tuple[tuple[float, float], float]] = []
    try:
        boundary = tuple(self.boundary)
    except TypeError as exc:
        raise ValueError("oriented slot passage boundary must contain four vertices") from exc
    if len(boundary) != 4:
        raise ValueError("oriented slot passage boundary must contain four vertices")
    for vertex in boundary:
        try:
            point, bulge = vertex
            coordinates = tuple(point)
            if len(coordinates) != 2:
                raise ValueError
            x, y = (
                strict_real("oriented slot boundary coordinate", value) for value in coordinates
            )
            clean_bulge = strict_real("oriented slot boundary bulge", bulge)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("oriented slot boundary values must be finite numbers") from exc
        clean_boundary.append(((x, y), clean_bulge))
    object.__setattr__(self, "boundary", tuple(clean_boundary))
    _validate_oriented_slot_boundary_order(tuple(clean_boundary))
    centroid = tuple(
        sum(point[axis] for point, _bulge in clean_boundary) / len(clean_boundary)
        for axis in range(2)
    )
    if hypot(*centroid) > 8e-4:
        raise ValueError("oriented slot passage boundary must be origin-centred")
    _oriented_slot_rectangle(self)
    if self.body_key is not None:
        try:
            body_key = tuple(
                strict_real("oriented slot body_key value", value) for value in self.body_key
            )
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("oriented slot body_key must contain only finite values") from exc
        object.__setattr__(self, "body_key", body_key)


def validate_feature(self, frame_type, passage_type, strict_real) -> None:
    if not isinstance(self.frame, frame_type):
        raise ValueError("an oriented slot needs a Frame")
    if type(self.passage) is not passage_type:
        raise ValueError("an oriented slot needs an OrientedSlotPassage")
    centre = _strict_finite_point3("oriented slot center", self.frame.origin, strict_real)
    object.__setattr__(self, "frame", frame_type(centre, self.frame.axis))
    for name in ("width_direction", "long_direction", "run_direction"):
        direction = _strict_finite_point3(name, getattr(self, name), strict_real)
        if not isclose(hypot(*direction), 1.0, rel_tol=0.0, abs_tol=2e-5):
            raise ValueError(f"oriented slot {name} must be unit length")
        object.__setattr__(self, name, direction)
    try:
        width = strict_real("oriented slot width", self.width)
        length = strict_real("oriented slot length", self.length)
    except ValueError as exc:
        raise ValueError("oriented slot width and length must be finite and positive") from exc
    if width <= 0 or length <= 0:
        raise ValueError("oriented slot width and length must be finite and positive")
    object.__setattr__(self, "width", width)
    object.__setattr__(self, "length", length)
    for first, second in (
        ("width_direction", "long_direction"),
        ("width_direction", "run_direction"),
        ("long_direction", "run_direction"),
    ):
        dot = sum(
            left * right
            for left, right in zip(getattr(self, first), getattr(self, second), strict=True)
        )
        # Bound reconstruction disagreement by the shorter section edge, with a separate
        # allowance for serialized directions. Width/length and the retained section must
        # still agree under the checks below.
        tolerance = (
            max(3e-5, 0.004 / min(self.width, self.length) + 3e-6)
            if (first, second) == ("width_direction", "long_direction")
            else 3e-5
        )
        if not isclose(dot, 0.0, rel_tol=0.0, abs_tol=tolerance):
            raise ValueError(f"oriented slot {first}/{second} must be orthogonal")
    if self.run_direction != self.passage.run:
        raise ValueError("oriented slot run_direction must equal its passage run")
    passage_width_direction, passage_long_direction, passage_width, passage_length = (
        _oriented_slot_rectangle(self.passage)
    )
    if (
        abs(self.width - passage_width) > _ORIENTED_SLOT_SERIALIZATION_ERROR
        or abs(self.length - passage_length) > _ORIENTED_SLOT_SERIALIZATION_ERROR
    ):
        raise ValueError("oriented slot dimensions must match its passage rectangle")
    direction_error = max(
        3e-5,
        _ORIENTED_SLOT_SERIALIZATION_ERROR / min(self.width, self.length) + 3e-6,
    )
    for name, actual, expected in (
        ("width_direction", self.width_direction, passage_width_direction),
        ("long_direction", self.long_direction, passage_long_direction),
    ):
        if (
            hypot(*(left - right for left, right in zip(actual, expected, strict=True)))
            > direction_error
        ):
            raise ValueError(f"oriented slot {name} must match its passage rectangle")
    if (
        max(abs(value) for value in self.width_direction) >= 0.99
        and max(abs(value) for value in self.long_direction) >= 0.99
    ):
        raise ValueError("principal rectangular passages belong to the legacy slot family")
    run_midpoint = 0.5 * sum(self.passage.run_interval)
    expected_center = tuple(
        self.passage.origin[index] + run_midpoint * self.passage.run[index] for index in range(3)
    )
    if any(
        abs(actual - expected) > 1e-3
        for actual, expected in zip(self.frame.origin, expected_center, strict=True)
    ):
        raise ValueError("oriented slot center must lie at its passage midpoint")
    expected_axis = "xyz"[max(range(3), key=lambda index: abs(self.run_direction[index]))]
    if self.frame.axis != expected_axis:
        raise ValueError(
            f"oriented slot frame axis must be the dominant run_direction axis ({expected_axis!r})"
        )
