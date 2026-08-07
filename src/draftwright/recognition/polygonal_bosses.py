"""Recognition of bounded regular polygonal bosses (#676)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Plane

from draftwright.recognition._record import Record


@dataclass(frozen=True, order=True)
class PolygonalBoss(Record):
    """A regular even-sided prism attached to a support face.

    ``flat_directions`` preserve the ordered outward evidence that established the regular
    polygon. ``flat_centres`` are real points on the defining side faces, so rendering can
    anchor a leader without reconstructing the polygon from A/F.
    """

    axis: str
    center: tuple[float, float, float]
    side_count: int
    across_flats: float
    base: float
    top: float
    flat_directions: tuple[tuple[float, float, float], ...]
    flat_centres: tuple[tuple[float, float, float], ...]

    @property
    def height(self) -> float:
        return self.top - self.base


def _normal(face):
    try:
        normal = face.normal_at(face.center())
    except Exception:  # noqa: BLE001 - a degenerate face cannot prove a boss
        return None
    return (float(normal.X), float(normal.Y), float(normal.Z))


def _bbox_tuple(face) -> tuple[float, float, float, float, float, float]:
    bb = face.bounding_box()
    return (
        float(bb.min.X),
        float(bb.max.X),
        float(bb.min.Y),
        float(bb.max.Y),
        float(bb.min.Z),
        float(bb.max.Z),
    )


def _recognise_one(part, *, tol: float, angle_tol: float) -> list[PolygonalBoss]:
    faces = list(part.faces())
    edges = [[edge.wrapped for edge in face.edges()] for face in faces]
    adjacency: dict[tuple[int, int], bool] = {}

    def shares_edge(i: int, j: int) -> bool:
        key = (min(i, j), max(i, j))
        if key not in adjacency:
            adjacency[key] = any(a.IsSame(b) for a in edges[i] for b in edges[j])
        return adjacency[key]

    normals: dict[int, tuple[float, float, float]] = {}
    bounds: dict[int, tuple[float, float, float, float, float, float]] = {}
    vertical: list[int] = []
    for i, face in enumerate(faces):
        if BRepAdaptor_Surface(face.wrapped).GetType() != GeomAbs_Plane:
            continue
        normal = _normal(face)
        if normal is None or abs(normal[2]) > 0.02:
            continue
        bb = _bbox_tuple(face)
        if bb[5] - bb[4] <= tol:
            continue
        normals[i] = normal
        bounds[i] = bb
        vertical.append(i)

    def same_span(i: int, j: int) -> bool:
        return abs(bounds[i][4] - bounds[j][4]) <= tol and abs(bounds[i][5] - bounds[j][5]) <= tol

    components: list[tuple[int, ...]] = []
    unseen = set(vertical)
    while unseen:
        seed = unseen.pop()
        connected = {seed}
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            joined = {
                other
                for other in unseen
                if same_span(current, other) and shares_edge(current, other)
            }
            unseen -= joined
            connected |= joined
            frontier.extend(joined)
        components.append(tuple(connected))

    def adjacent_to(index: int) -> set[int]:
        return {
            other for other in range(len(faces)) if other != index and shares_edge(index, other)
        }

    def positive_z_cap(index: int, *, lower_than: float | None, higher_than: float | None):
        face = faces[index]
        if BRepAdaptor_Surface(face.wrapped).GetType() != GeomAbs_Plane:
            return None
        normal = _normal(face)
        if normal is None or normal[2] < 0.99:
            return None
        bb = _bbox_tuple(face)
        if bb[5] - bb[4] > tol:
            return None
        z = (bb[4] + bb[5]) / 2
        if lower_than is not None and z > lower_than + tol:
            return None
        if higher_than is not None and z < higher_than - tol:
            return None
        return z

    def common_cap(
        component: tuple[int, ...], *, upper: bool, wall_lo: float, wall_hi: float
    ) -> float | None:
        boundary: list[int] = []
        component_set = set(component)
        for side in component:
            choices = []
            for other in adjacent_to(side) - component_set:
                bb = _bbox_tuple(faces[other])
                reaches_end = abs(bb[4] - wall_hi) <= tol if upper else abs(bb[5] - wall_lo) <= tol
                if reaches_end:
                    choices.append(other)
            if len(choices) != 1:
                return None
            boundary.append(choices[0])

        boundary_set = set(boundary)
        if len(boundary_set) == 1:
            candidates = boundary_set
        else:
            candidates = set.intersection(*(adjacent_to(face) for face in boundary_set))
            candidates -= component_set | boundary_set
        cap_zs = [
            cap
            for index in candidates
            if (
                cap := positive_z_cap(
                    index,
                    lower_than=None if upper else wall_lo,
                    higher_than=wall_hi if upper else None,
                )
            )
            is not None
        ]
        return cap_zs[0] if len(cap_zs) == 1 else None

    found: list[PolygonalBoss] = []
    for component in components:
        side_count = len(component)
        # #676 slice 1 proves hexagonal bosses. Broader polygon classes need their own
        # corpus evidence before automatic recognition can claim them.
        if side_count != 6:
            continue
        component_set = set(component)
        if any(
            len({other for other in component_set if other != side and shares_edge(side, other)})
            != 2
            for side in component
        ):
            continue

        ordered = sorted(component, key=lambda i: math.atan2(normals[i][1], normals[i][0]))
        angles = [math.atan2(normals[i][1], normals[i][0]) % (2 * math.pi) for i in ordered]
        gaps = [
            (angles[(i + 1) % side_count] - angles[i]) % (2 * math.pi) for i in range(side_count)
        ]
        expected_gap = 2 * math.pi / side_count
        if any(abs(gap - expected_gap) > angle_tol for gap in gaps):
            continue
        if any(
            normals[ordered[i]][0] * normals[ordered[i + side_count // 2]][0]
            + normals[ordered[i]][1] * normals[ordered[i + side_count // 2]][1]
            > -math.cos(angle_tol)
            for i in range(side_count // 2)
        ):
            continue

        centres = [faces[i].center() for i in ordered]
        plane_offsets = [
            normals[index][0] * float(point.X) + normals[index][1] * float(point.Y)
            for index, point in zip(ordered, centres, strict=True)
        ]
        midplanes = [
            (
                normals[ordered[i]][0],
                normals[ordered[i]][1],
                (plane_offsets[i] - plane_offsets[i + side_count // 2]) / 2,
            )
            for i in range(side_count // 2)
        ]
        sxx = sum(nx * nx for nx, _ny, _offset in midplanes)
        sxy = sum(nx * ny for nx, ny, _offset in midplanes)
        syy = sum(ny * ny for _nx, ny, _offset in midplanes)
        bx = sum(nx * offset for nx, _ny, offset in midplanes)
        by = sum(ny * offset for _nx, ny, offset in midplanes)
        determinant = sxx * syy - sxy * sxy
        # Six normals that passed the near-60-degree ring gate necessarily span the plane.
        cx = (bx * syy - by * sxy) / determinant
        cy = (sxx * by - sxy * bx) / determinant
        supports = [
            offset - normals[index][0] * cx - normals[index][1] * cy
            for index, offset in zip(ordered, plane_offsets, strict=True)
        ]
        if min(supports) <= tol:
            continue  # inward-facing walls describe a recess, not material projecting out
        across_values = [
            supports[i] + supports[i + side_count // 2] for i in range(side_count // 2)
        ]
        across = sum(across_values) / len(across_values)
        if max(abs(value - across) for value in across_values) > tol:
            continue
        if max(abs(value - across / 2) for value in supports) > tol:
            continue

        wall_lo = sum(bounds[i][4] for i in component) / side_count
        wall_hi = sum(bounds[i][5] for i in component) / side_count
        base = common_cap(component, upper=False, wall_lo=wall_lo, wall_hi=wall_hi)
        top = common_cap(component, upper=True, wall_lo=wall_lo, wall_hi=wall_hi)
        if base is None or top is None or top - base <= tol:
            continue
        flat_centres = tuple(
            (round(float(point.X), 3), round(float(point.Y), 3), round(float(point.Z), 3))
            for point in centres
        )
        flat_directions = tuple(
            (round(normals[index][0], 3), round(normals[index][1], 3), 0.0) for index in ordered
        )
        found.append(
            PolygonalBoss(
                axis="z",
                center=(round(cx, 4), round(cy, 4), round((base + top) / 2, 4)),
                side_count=side_count,
                across_flats=round(across, 4),
                base=round(base, 4),
                top=round(top, 4),
                flat_directions=flat_directions,
                flat_centres=flat_centres,
            )
        )
    return found


def recognise_polygonal_bosses(
    part, *, tol: float = 0.2, angle_tol: float = math.radians(2)
) -> list[PolygonalBoss]:
    """Return regular hexagonal Z-axis bosses independently per physical solid.

    A candidate is accepted from a closed ring of outward planar side faces, opposed
    support planes with one A/F value, and common attached support/top caps. A whole prism,
    a blind recess, or faces assembled across separate solids cannot satisfy that evidence.
    """
    solids = list(part.solids())
    sources = solids if len(solids) > 1 else [part]
    return sorted(
        boss for solid in sources for boss in _recognise_one(solid, tol=tol, angle_tol=angle_tol)
    )
