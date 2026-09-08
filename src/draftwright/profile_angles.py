"""Drafting requirements projected from the provider's ordered outer profiles.

The provider owns topology, adjacency and body identity. This module chooses
which of its face profiles to dimension and extends their supplied straight
supports; it never traverses part topology or creates recognition occurrences.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import cos, dist, fsum, pi, sin
from typing import TYPE_CHECKING

from quiddity import BoltCircle, Chamfer, PolygonalBoss, PolygonalStock
from quiddity.evidence import (
    FaceRef,
    FeatureRef,
    PlanarOuterProfileEvidence,
    ProfileArc,
    ProfileLine,
)

if TYPE_CHECKING:
    from build123d import Edge


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b, strict=True))


def _dot(a, b):
    return fsum(x * y for x, y in zip(a, b, strict=True))


def _cross(a, b):
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


@dataclass(frozen=True)
class ProfileAngle:
    """One same-run support pair, separate from its eventual IR owner."""

    source: PlanarOuterProfileEvidence
    first_index: int
    second_index: int
    vertex: tuple[float, float, float]
    first: tuple[float, float, float]
    second: tuple[float, float, float]
    virtual_vertex: bool


def _corners(source):
    supports = source.profile.supports
    normal = source.profile.normal
    for index, first in enumerate(supports):
        if type(first) is not ProfileLine:
            continue
        next_index = (index + 1) % len(supports)
        transition = supports[next_index]
        rounded = type(transition) is ProfileArc
        if rounded:
            next_index = (next_index + 1) % len(supports)
        second = supports[next_index]
        if type(second) is not ProfileLine:
            continue
        u, v = first.direction, second.direction
        turn = _dot(_cross(u, v), normal)
        # Straight continuations have no corner. Orthogonal corners need no
        # automatic angle label; an author can still request one explicitly.
        if turn <= 1e-8 or abs(_dot(u, v)) <= 1e-8:
            continue
        distance = _dot(_cross(_sub(second.start, first.end), v), normal) / turn
        vertex = tuple(p + distance * d for p, d in zip(first.end, u, strict=True))
        along_second = _dot(_sub(vertex, second.start), v)
        reconstructed = tuple(p + along_second * d for p, d in zip(second.start, v, strict=True))
        if dist(vertex, reconstructed) > 1e-6:
            continue
        if rounded:
            # A rounded convex corner extends the preceding line forward and
            # the following line backward. An arbitrary line/arc/line sequence
            # with its intersection elsewhere does not establish that corner.
            if distance <= 1e-6 or along_second >= -1e-6:
                continue
            # Both finite lines must be tangent to the supplied circular join.
            if abs(_dot(u, _sub(first.end, transition.center))) > 1e-6:
                continue
            if abs(_dot(v, _sub(second.start, transition.center))) > 1e-6:
                continue
            witnesses = first.end, second.start
        else:
            if dist(vertex, first.end) > 1e-6 or first.end != second.start:
                continue
            witnesses = first.start, second.end
        yield ProfileAngle(source, index, next_index, vertex, *witnesses, rounded)


def _profile_callout_definitions(evidence):
    """Issued faces/edges whose existing callout defines the profile angles.

    This reads the provider's cached face profiles, not part topology. A shared
    numerical angle or projected location cannot establish this relationship.
    """
    definitions = []
    for occurrence in evidence.features:
        record = evidence.record(occurrence)
        if not isinstance(record, (Chamfer, PolygonalStock, PolygonalBoss)):
            continue
        if isinstance(record, Chamfer) and record.turned:
            continue  # no planar bevel-face authority for a conical treatment
        faces = (
            evidence.defining_faces(occurrence)
            if isinstance(record, Chamfer)
            else evidence.constituent_faces(occurrence)
        )
        edges: list[Edge] = []
        for face in faces:
            source = evidence.planar_outer_profile(face)
            if type(source) is PlanarOuterProfileEvidence:
                edges.extend(
                    evidence.profile_edge(source, index)
                    for index in range(len(source.profile.supports))
                )
        definitions.append((record, faces, edges))
    return definitions


def _standalone_corners(source, evidence, definitions):
    """Keep angles not already defined by an exact same-body feature callout."""
    edges = None
    bevel_supports: set[int] = set()
    for record, faces, defining_edges in definitions:
        if (
            not faces
            or not faces <= source.body_faces
            or abs(source.profile.normal["xyz".index(record.axis)]) < 1 - 1e-8
        ):
            continue
        if edges is None:
            edges = tuple(
                evidence.profile_edge(source, index)
                for index in range(len(source.profile.supports))
            )
        shared = {
            index
            for index, edge in enumerate(edges)
            if any(edge.is_same(defining) for defining in defining_edges)
        }
        if isinstance(record, (PolygonalStock, PolygonalBoss)):
            if (
                len(edges) == record.side_count
                and all(type(support) is ProfileLine for support in source.profile.supports)
                and (source.face in faces or len(shared) == len(edges))
            ):
                return  # the regular-polygon callout defines the complete corner ring
        else:
            bevel_supports.update(shared)
    for corner in _corners(source):
        if corner.virtual_vertex or not bevel_supports.intersection(
            (corner.first_index, corner.second_index)
        ):
            yield corner


def profile_angle_requirements(evidence) -> tuple[ProfileAngle, ...]:
    """Choose non-right corners on the foremost camera-facing profile per body/view.

    This is a bounded face-profile drafting policy, not a claim that a face's
    outer wire is the stock envelope. Inner wires never supply corners. Tied
    coplanar faces stay distinct, as do equal-valued corners and separate bodies.
    Angles already defined by an issued chamfer or regular-polygon callout do
    not create duplicate automatic requirements; explicit angles remain valid.
    Back faces are not a second copy of the view's requirements. The standard
    cameras face the part from +X (side), -Y (front) and +Z (plan).
    """
    if evidence is None:
        return ()
    candidates: dict[tuple[frozenset[FaceRef], int], list[PlanarOuterProfileEvidence]] = {}
    for face in evidence.faces:
        source = evidence.planar_outer_profile(face)
        if type(source) is not PlanarOuterProfileEvidence:
            continue
        profile = source.profile
        if profile.schema_version != 1 or profile.boundary_kind != "outer":
            raise ValueError("unsupported planar outer-profile schema")
        axis = max(range(3), key=lambda index: abs(profile.normal[index]))
        camera_side = -1 if axis == 1 else 1
        if camera_side * profile.normal[axis] < 1 - 1e-8 or any(
            abs(component) > 1e-8
            for index, component in enumerate(profile.normal)
            if index != axis
        ):
            continue
        candidates.setdefault((source.body_faces, axis), []).append(source)
    definitions = _profile_callout_definitions(evidence)
    selected = []
    for (_body, axis), profiles in candidates.items():
        camera_side = -1 if axis == 1 else 1
        foremost = max(camera_side * source.profile.origin[axis] for source in profiles)
        for source in profiles:
            if abs(camera_side * source.profile.origin[axis] - foremost) <= 1e-6:
                selected.extend(_standalone_corners(source, evidence, definitions))
    # Ordering controls presentation only. The binding retains the issued
    # profile and its support indices; numerical sorting establishes no owner.
    return tuple(sorted(selected, key=lambda angle: (angle.vertex, angle.first, angle.second)))


@dataclass(frozen=True)
class ProfileAngleRepetition:
    """Angles carried by one verified rotation of a same-body bolt-circle pattern.

    This is run-local drafting evidence, not IR. It never establishes a new
    recognition occurrence or groups measurements merely because values agree.
    """

    pattern: BoltCircle
    members: tuple[ProfileAngle, ...]


def profile_angle_repetitions(evidence) -> tuple[ProfileAngleRepetition, ...]:
    """Prove repeated-angle presentations using an already recognised rotation.

    Every pattern member must belong to the profile's exact body, and the entire
    ordered outer profile must map to itself under that rotation, including arc
    centres, radii and sweeps. A shared numerical angle or partial resemblance
    supplies no repetition authority. Unproved corners remain individual.
    """
    if evidence is None:
        return ()
    by_profile: dict[int, list[ProfileAngle]] = {}
    for requirement in profile_angle_requirements(evidence):
        by_profile.setdefault(id(requirement.source), []).append(requirement)
    refs_by_record: dict[int, list[FeatureRef]] = {}
    for occurrence in evidence.features:
        refs_by_record.setdefault(id(evidence.record(occurrence)), []).append(occurrence)
    repeated = []
    for requirements in by_profile.values():
        source = requirements[0].source
        profile = source.profile
        supports = profile.supports
        for pattern in evidence.result.hole_patterns:
            if not isinstance(pattern, BoltCircle):
                continue
            count = len(pattern.holes)
            if count < 3 or len(supports) % count:
                continue
            owned = True
            for hole in pattern.holes:
                refs = refs_by_record.get(id(hole), [])
                if len(refs) != 1 or abs(_dot(hole.axis, profile.normal)) < 1 - 1e-8:
                    owned = False
                    break
                faces = evidence.constituent_faces(refs[0])
                if not faces or not faces <= source.body_faces:
                    owned = False
                    break
            if not owned:
                continue
            # The recognised bolt-circle axis intersects this profile's plane.
            # This allows a through pattern to support either cap of its body.
            normal = profile.normal
            along = _dot(_sub(profile.origin, pattern.center), normal)
            centre = tuple(p + along * n for p, n in zip(pattern.center, normal, strict=True))
            theta = 2 * pi / count

            def rotate(point):
                delta = _sub(point, centre)
                crossed = _cross(normal, delta)
                axial = _dot(normal, delta)
                return tuple(
                    c + d * cos(theta) + cross * sin(theta) + n * axial * (1 - cos(theta))
                    for c, d, cross, n in zip(centre, delta, crossed, normal, strict=True)
                )

            shift = len(supports) // count
            for index, support in enumerate(supports):
                target = supports[(index + shift) % len(supports)]
                if type(support) is not type(target):
                    break
                if (
                    max(
                        dist(rotate(support.start), target.start),
                        dist(rotate(support.end), target.end),
                    )
                    > 1e-6
                ):
                    break
                if isinstance(support, ProfileArc) and (
                    not isinstance(target, ProfileArc)
                    or dist(rotate(support.center), target.center) > 1e-6
                    or abs(support.radius - target.radius) > 1e-6
                    or abs(support.sweep - target.sweep) > 1e-6
                ):
                    break
            else:
                by_index = {item.first_index: item for item in requirements}
                remaining = set(by_index)
                while remaining:
                    first = min(remaining)
                    indices = tuple(
                        (first + member * shift) % len(supports) for member in range(count)
                    )
                    if not set(indices) <= remaining:
                        remaining.remove(first)
                        continue
                    repeated.append(
                        ProfileAngleRepetition(pattern, tuple(by_index[i] for i in indices))
                    )
                    remaining.difference_update(indices)
                break  # One proved complete pattern supplies this profile's presentation.
    return tuple(repeated)
