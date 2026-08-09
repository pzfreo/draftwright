"""Geometry-only evidence for complete repeating radial boundary profiles (#1087).

The recogniser proves cyclic correspondence over every edge of two opposed outer wires.  It
does not attach gear semantics to the result: module, pressure angle, tooth form, quality and
manufacturing intent remain authored requirements.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, hypot, pi, sin

from build123d import GeomType

from draftwright.recognition._record import Record
from draftwright.recognition.profiled_bores import principal_boundary_plane

_SAMPLES_PER_CURVE = 9
# The only production consumer supports declarations with z >= 5. Lower-order symmetry has
# no correspondence consumer and would make ordinary prismatic stock enter this inventory.
_MIN_REPEAT_COUNT = 5


@dataclass(frozen=True, order=True)
class RepeatingRadialProfile(Record):
    """A complete physical profile proved invariant under one sector rotation.

    ``sector_signature`` is a traversal- and phase-neutral inventory of the curves in one
    sector.  Each entry carries the curve kind, length and sampled polar shape.  It is evidence
    that the repeat count came from the complete wire rather than a tip-arc proxy; consumers
    still correspond the physical occurrence by ``axis``, ``centre`` and ``span``.
    """

    axis: str
    centre: tuple[float, float, float]
    span: tuple[float, float]
    repeat_count: int
    edge_count: int
    sector_signature: tuple[tuple[str, float, tuple[tuple[float, float], ...]], ...]


@dataclass(frozen=True)
class _CurveEvidence:
    kind: str
    length: float
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class _BoundaryEvidence:
    axis: str
    at: float
    plane_axes: tuple[str, str]
    centre: tuple[float, float]
    repeat_count: int
    edges: tuple[_CurveEvidence, ...]
    orbits: tuple[tuple[int, ...], ...]


def _distance(left: tuple[float, float], right: tuple[float, float]) -> float:
    return hypot(left[0] - right[0], left[1] - right[1])


def _one_closed_cycle(edges: tuple[_CurveEvidence, ...], *, tol: float) -> bool:
    """Require one connected degree-two endpoint graph, independent of traversal order."""
    if not edges:
        return False
    nodes: list[tuple[float, float]] = []
    incident: list[list[int]] = []
    edge_nodes: list[tuple[int, int]] = []
    for edge_index, edge in enumerate(edges):
        endpoints: list[int] = []
        for point in (edge.points[0], edge.points[-1]):
            matches = [index for index, node in enumerate(nodes) if _distance(point, node) <= tol]
            if len(matches) > 1:
                return False
            if matches:
                node_index = matches[0]
            else:
                node_index = len(nodes)
                nodes.append(point)
                incident.append([])
            incident[node_index].append(edge_index)
            endpoints.append(node_index)
        if endpoints[0] == endpoints[1]:
            return False
        edge_nodes.append((endpoints[0], endpoints[1]))
    if any(len(edge_ids) != 2 for edge_ids in incident):
        return False

    reached: set[int] = set()
    frontier = [0]
    while frontier:
        edge_index = frontier.pop()
        if edge_index in reached:
            continue
        reached.add(edge_index)
        frontier.extend(
            neighbour
            for node_index in edge_nodes[edge_index]
            for neighbour in incident[node_index]
            if neighbour not in reached
        )
    return len(reached) == len(edges)


def _rotate(
    point: tuple[float, float], angle: float, centre: tuple[float, float]
) -> tuple[float, float]:
    x, y = point[0] - centre[0], point[1] - centre[1]
    return (
        centre[0] + x * cos(angle) - y * sin(angle),
        centre[1] + x * sin(angle) + y * cos(angle),
    )


def _curves_match(
    source: _CurveEvidence,
    target: _CurveEvidence,
    *,
    angle: float,
    centre: tuple[float, float],
    tol: float,
) -> bool:
    if source.kind != target.kind or abs(source.length - target.length) > tol:
        return False
    rotated = tuple(_rotate(point, angle, centre) for point in source.points)
    return any(
        max(_distance(left, right) for left, right in zip(rotated, candidate, strict=True)) <= tol
        for candidate in (target.points, tuple(reversed(target.points)))
    )


def _cyclic_edge_orbits(
    edges: tuple[_CurveEvidence, ...],
    *,
    centre: tuple[float, float],
    repeat_count: int,
    tol: float,
) -> tuple[tuple[int, ...], ...] | None:
    """Prove a bijective complete-wire mapping under one sector rotation."""
    if (
        len(edges) <= repeat_count
        or len(edges) % repeat_count
        or not _one_closed_cycle(edges, tol=tol)
    ):
        return None
    angle = 2 * pi / repeat_count
    mapping: list[int] = []
    for edge in edges:
        matches = [
            index
            for index, candidate in enumerate(edges)
            if _curves_match(edge, candidate, angle=angle, centre=centre, tol=tol)
        ]
        if len(matches) != 1:
            return None
        mapping.append(matches[0])
    if len(set(mapping)) != len(edges):
        return None

    unseen = set(range(len(edges)))
    orbits: list[tuple[int, ...]] = []
    while unseen:
        start = min(unseen)
        orbit: list[int] = []
        current = start
        while current not in orbit:
            orbit.append(current)
            current = mapping[current]
        if current != start or len(orbit) != repeat_count:
            return None
        unseen -= set(orbit)
        orbits.append(tuple(orbit))
    return tuple(orbits)


def _sample_wire(wire, plane_axes: tuple[str, str]) -> tuple[_CurveEvidence, ...] | None:
    sampled: list[_CurveEvidence] = []
    try:
        for edge in wire.edges():
            points = tuple(
                (
                    float(getattr(point, plane_axes[0].upper())),
                    float(getattr(point, plane_axes[1].upper())),
                )
                for index in range(_SAMPLES_PER_CURVE)
                for point in (edge.position_at(index / (_SAMPLES_PER_CURVE - 1)),)
            )
            kind = getattr(edge.geom_type, "name", str(edge.geom_type))
            sampled.append(_CurveEvidence(kind, float(edge.length), points))
    except (AttributeError, RuntimeError, ValueError, ZeroDivisionError):
        return None
    return tuple(sampled)


def _prove_boundary(face, bbox, *, tol: float) -> _BoundaryEvidence | None:
    boundary = principal_boundary_plane(face, bbox)
    if boundary is None:
        return None
    axis, plane_axes, at = boundary
    wire = face.outer_wire()
    edges = _sample_wire(wire, plane_axes)
    # A collection of common-circle arcs is only a candidate-count proxy, never the full
    # profile proof.  At least one intervening non-circular curve must participate.
    if not edges or all(edge.kind == GeomType.CIRCLE.name for edge in edges):
        return None
    # Common-circle centres are legitimate geometric evidence for the rotation axis, but
    # never sufficient evidence for the repeat itself.  The imported wheel's approximate
    # spline extrema make its wire-bbox centre drift by 0.03 mm, while all 13 tip arcs carry
    # the exact source centre.  Fall back to the bbox only when there is no unique common
    # circle centre; the complete-wire bijection below remains the acceptance guard.
    circle_centres: list[tuple[float, float]] = []
    for edge in wire.edges():
        if edge.geom_type != GeomType.CIRCLE:
            continue
        try:
            circle_centres.append(
                (
                    float(getattr(edge.arc_center, plane_axes[0].upper())),
                    float(getattr(edge.arc_center, plane_axes[1].upper())),
                )
            )
        except (AttributeError, ValueError):
            circle_centres = []
            break
    common_circle_centre: tuple[float, float] | None = None
    if len(circle_centres) >= 2:
        mean = (
            sum(point[0] for point in circle_centres) / len(circle_centres),
            sum(point[1] for point in circle_centres) / len(circle_centres),
        )
        if all(_distance(point, mean) <= tol for point in circle_centres):
            common_circle_centre = mean
    if common_circle_centre is None:
        wbb = wire.bounding_box()
        raw = tuple(float(getattr(wbb.center(), candidate.upper())) for candidate in plane_axes)
        centre = (raw[0], raw[1])
    else:
        centre = common_circle_centre
    candidates = (
        count
        for count in range(len(edges) - 1, _MIN_REPEAT_COUNT - 1, -1)
        if len(edges) % count == 0
    )
    for repeat_count in candidates:
        orbits = _cyclic_edge_orbits(
            edges,
            centre=centre,
            repeat_count=repeat_count,
            tol=tol,
        )
        if orbits is not None:
            return _BoundaryEvidence(
                axis,
                at,
                plane_axes,
                centre,
                repeat_count,
                edges,
                orbits,
            )
    return None


def _profiles_correspond(
    lower: _BoundaryEvidence, upper: _BoundaryEvidence, *, tol: float
) -> bool:
    return (
        lower.axis == upper.axis
        and lower.plane_axes == upper.plane_axes
        and lower.repeat_count == upper.repeat_count
        and len(lower.edges) == len(upper.edges)
        and all(abs(a - b) <= tol for a, b in zip(lower.centre, upper.centre, strict=True))
        # Each boundary has already proved its own bijective mapping.  Compare their
        # traversal/phase-neutral sector inventories rather than absolute edge positions:
        # opposed source faces may carry reversed parameterisation or a sector phase shift.
        and _sector_signature(lower) == _sector_signature(upper)
    )


def _polar_signature(
    points: tuple[tuple[float, float], ...], centre: tuple[float, float]
) -> tuple[tuple[float, float], ...]:
    """Canonical sampled curve shape modulo phase, reflection and traversal direction."""

    def one_direction(candidate: tuple[tuple[float, float], ...]):
        polar = [
            (_distance(point, centre), atan2(point[1] - centre[1], point[0] - centre[0]))
            for point in candidate
        ]
        unwrapped = [polar[0][1]]
        for _radius, angle in polar[1:]:
            previous = unwrapped[-1]
            while angle - previous > pi:
                angle -= 2 * pi
            while angle - previous < -pi:
                angle += 2 * pi
            unwrapped.append(angle)
        phase = unwrapped[0]
        relative = tuple(
            (round(radius, 6), round(angle - phase, 6))
            for (radius, _raw), angle in zip(polar, unwrapped, strict=True)
        )
        reflected = tuple((radius, -angle) for radius, angle in relative)
        return min(relative, reflected)

    return min(one_direction(points), one_direction(tuple(reversed(points))))


def _sector_signature(
    boundary: _BoundaryEvidence,
) -> tuple[tuple[str, float, tuple[tuple[float, float], ...]], ...]:
    signature = []
    for orbit in boundary.orbits:
        edge = boundary.edges[orbit[0]]
        signature.append(
            (
                edge.kind,
                round(edge.length, 6),
                _polar_signature(edge.points, boundary.centre),
            )
        )
    return tuple(sorted(signature))


def _recognise_solid(solid, *, tol: float) -> list[RepeatingRadialProfile]:
    bbox = solid.bounding_box()
    scale = max(float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z))
    metric_tol = max(tol, scale * 1e-5)
    boundaries = []
    for face in solid.faces():
        try:
            evidence = _prove_boundary(face, bbox, tol=metric_tol)
        except Exception:  # noqa: BLE001 - malformed B-rep evidence must fail closed
            evidence = None
        if evidence is not None:
            boundaries.append(evidence)
    found: list[RepeatingRadialProfile] = []
    for axis in "xyz":
        attr = axis.upper()
        lo = float(getattr(bbox.min, attr))
        hi = float(getattr(bbox.max, attr))
        lowers = [b for b in boundaries if b.axis == axis and abs(b.at - lo) <= metric_tol]
        uppers = [b for b in boundaries if b.axis == axis and abs(b.at - hi) <= metric_tol]
        for lower in lowers:
            matches = [
                upper for upper in uppers if _profiles_correspond(lower, upper, tol=metric_tol)
            ]
            if len(matches) != 1:
                continue
            # Source-face correspondence is one-to-one in both directions. Two lower faces
            # claiming the same upper face is ambiguous even if their serialised facts happen
            # to be equal; collapsing that ambiguity would violate source/body identity.
            if (
                sum(
                    _profiles_correspond(candidate, matches[0], tol=metric_tol)
                    for candidate in lowers
                )
                != 1
            ):
                continue
            coords = [0.0, 0.0, 0.0]
            coords["xyz".index(lower.plane_axes[0])] = lower.centre[0]
            coords["xyz".index(lower.plane_axes[1])] = lower.centre[1]
            coords["xyz".index(axis)] = (lo + hi) / 2
            found.append(
                RepeatingRadialProfile(
                    axis=axis,
                    centre=(coords[0], coords[1], coords[2]),
                    span=(lo, hi),
                    repeat_count=lower.repeat_count,
                    edge_count=len(lower.edges),
                    sector_signature=_sector_signature(lower),
                )
            )
    return found


def recognise_repeating_radial_profiles(
    part, *, tol: float = 1e-5
) -> list[RepeatingRadialProfile]:
    """Return complete repeating outer-profile evidence for each independently owned solid."""
    solids = list(part.solids())
    if not solids:
        solids = [part]
    found = [profile for solid in solids for profile in _recognise_solid(solid, tol=tol)]
    # Preserve equal records from distinct solids. Lint must see two possible physical
    # occurrences as ambiguous rather than deduplicating them into false uniqueness.
    return sorted(found)


__all__ = ["RepeatingRadialProfile", "recognise_repeating_radial_profiles"]
