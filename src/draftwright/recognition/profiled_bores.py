"""Recognition of non-circular bores whose profile has explicit drafting semantics.

The first supported profile is a through double-D: two parallel chords joined by two arcs
of one circle.  That correspondence matters.  Two lines plus two circular edges also
describes an obround, while arbitrary circular arcs can describe a lens; neither is a
double-D bore.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import asin, sqrt

from build123d import Face, GeomType, Solid, Vector

from draftwright.recognition._record import Record


@dataclass(frozen=True)
class DoubleDBore(Record):
    """A geometrically proven through double-D bore.

    ``major_diameter`` is the parent circle diameter; ``across_flats`` is the distance
    between its parallel chords. ``flat_direction`` is their canonical unit normal and
    preserves the profile's in-plane orientation independently of the bore axis.
    """

    axis: tuple[float, float, float]
    location: tuple[float, float, float]
    major_diameter: float
    across_flats: float
    depth: float
    through: bool
    flat_direction: tuple[float, float, float]


@dataclass(frozen=True)
class DoubleDProfile:
    """Private B-rep reading shared by recognition, declaration and physical critique."""

    centre: tuple[float, float, float]
    major_diameter: float
    across_flats: float
    flat_direction: tuple[float, float, float]


def principal_boundary_plane(face, bbox) -> tuple[str, tuple[str, str], float] | None:
    """Return ``(normal axis, in-plane axes, boundary coordinate)`` for an extremal face."""
    if face.geom_type != GeomType.PLANE:
        return None
    fbb = face.bounding_box()
    extent = {axis: float(getattr(fbb.size, axis.upper())) for axis in "xyz"}
    part_extent = (float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z))
    tol = max(1e-5, max(part_extent) * 1e-5)
    flat = [axis for axis, value in extent.items() if value <= tol]
    if len(flat) != 1:
        return None
    axis = flat[0]
    attr = axis.upper()
    at = float(getattr(fbb.center(), attr))
    if (
        min(
            abs(at - float(getattr(bbox.min, attr))),
            abs(at - float(getattr(bbox.max, attr))),
        )
        > tol
    ):
        return None
    plane_axes = tuple(candidate for candidate in "xyz" if candidate != axis)
    return axis, (plane_axes[0], plane_axes[1]), at


def double_d_profile(wire, plane_axes: tuple[str, str], *, tol: float) -> DoubleDProfile | None:
    """Read a double-D wire, rejecting merely topology-similar loops.

    The proof checks one common parent circle, opposed parallel chords, chord length and
    arc length.  Those metric correspondences distinguish the profile from true obrounds,
    lenses and arbitrary line/arc loops.
    """
    edges = list(wire.edges())
    lines = [edge for edge in edges if edge.geom_type == GeomType.LINE]
    arcs = [edge for edge in edges if edge.geom_type == GeomType.CIRCLE]
    if len(edges) != 4 or len(lines) != 2 or len(arcs) != 2:
        return None

    def coord(obj, axis: str) -> float:
        return float(getattr(obj, axis.upper()))

    wbb = wire.bounding_box()
    profile_scale = max(coord(wbb.size, axis) for axis in plane_axes)
    metric_tol = max(8 * tol, profile_scale * 1e-3)
    try:
        radius = float(arcs[0].radius)
        centre = arcs[0].arc_center
        if radius <= tol:
            return None
        if any(
            abs(float(edge.radius) - radius) > metric_tol
            or any(
                abs(coord(edge.arc_center, axis) - coord(centre, axis)) > metric_tol
                for axis in plane_axes
            )
            for edge in arcs[1:]
        ):
            return None
    except (AttributeError, ValueError):
        return None

    centre_xyz = (float(centre.X), float(centre.Y), float(centre.Z))
    axis_i = "xyz".index(next(axis for axis in "xyz" if axis not in plane_axes))
    directions: list[tuple[float, float, float]] = []
    midpoints: list[tuple[float, float, float]] = []
    for line in lines:
        vertices = list(line.vertices())
        if len(vertices) != 2:
            return None
        ends = [(float(vertex.X), float(vertex.Y), float(vertex.Z)) for vertex in vertices]
        delta = tuple(ends[1][i] - ends[0][i] for i in range(3))
        length = sqrt(sum(component * component for component in delta))
        if length <= tol or abs(delta[axis_i]) > metric_tol:
            return None
        directions.append((delta[0] / length, delta[1] / length, delta[2] / length))
        midpoints.append(
            (
                (ends[0][0] + ends[1][0]) / 2.0,
                (ends[0][1] + ends[1][1]) / 2.0,
                (ends[0][2] + ends[1][2]) / 2.0,
            )
        )
        if any(
            abs(sqrt(sum((end[i] - centre_xyz[i]) ** 2 for i in range(3))) - radius) > metric_tol
            for end in ends
        ):
            return None
    parallel = abs(sum(directions[0][i] * directions[1][i] for i in range(3)))
    if abs(parallel - 1.0) > 1e-4:
        return None

    u_i, v_i = ("xyz".index(axis) for axis in plane_axes)
    direction = directions[0]
    flat_direction = [0.0, 0.0, 0.0]
    flat_direction[u_i] = -direction[v_i]
    flat_direction[v_i] = direction[u_i]
    first = next(component for component in flat_direction if abs(component) > 1e-12)
    if first < 0:
        flat_direction = [-component for component in flat_direction]
    offsets = sorted(
        sum((midpoint[i] - centre_xyz[i]) * flat_direction[i] for i in range(3))
        for midpoint in midpoints
    )
    if offsets[0] >= -tol or offsets[1] <= tol or abs(offsets[0] + offsets[1]) > metric_tol:
        return None
    half_af = (offsets[1] - offsets[0]) / 2.0
    if half_af <= tol or half_af >= radius - tol:
        return None

    expected_chord = 2.0 * sqrt(radius * radius - half_af * half_af)
    if any(abs(float(line.length) - expected_chord) > metric_tol for line in lines):
        return None
    expected_arc = 2.0 * radius * asin(half_af / radius)
    if any(abs(float(arc.length) - expected_arc) > metric_tol for arc in arcs):
        return None

    clean_direction = tuple(
        0.0 if abs(component) <= 1e-12 else round(component, 12) for component in flat_direction
    )
    canonical_direction = (clean_direction[0], clean_direction[1], clean_direction[2])
    return DoubleDProfile(
        centre=centre_xyz,
        major_diameter=round(2.0 * radius, 4),
        across_flats=round(2.0 * half_af, 4),
        flat_direction=canonical_direction,
    )


def _profiles_correspond(
    first: DoubleDProfile,
    second: DoubleDProfile,
    plane_axes: tuple[str, str],
    *,
    tol: float,
) -> bool:
    """Whether two end profiles prove the same coaxial double-D cross-section."""
    return (
        abs(first.major_diameter - second.major_diameter) <= tol
        and abs(first.across_flats - second.across_flats) <= tol
        and all(
            abs(a - b) <= max(tol, 1e-6)
            for a, b in zip(first.flat_direction, second.flat_direction, strict=True)
        )
        and all(
            abs(first.centre["xyz".index(axis)] - second.centre["xyz".index(axis)]) <= tol
            for axis in plane_axes
        )
    )


def double_d_bores_from_openings(
    openings: list[tuple[str, float, DoubleDProfile, object]], bbox, *, part, tol: float
) -> list[DoubleDBore]:
    """Pair extremal openings and prove their entire profile prism is void.

    Opposed equal openings are not sufficient: two blind recesses can leave an internal web.
    Extruding the actual opening wire across the claimed depth and intersecting that prism with
    the part proves the whole cross-section is open, not merely a sampled centre line. Physical
    critique uses the same proof over the openings in its independent boundary scan.
    """
    out: list[DoubleDBore] = []
    for axis in "xyz":
        attr = axis.upper()
        lo = float(getattr(bbox.min, attr))
        hi = float(getattr(bbox.max, attr))
        low = [(at, p, wire) for a, at, p, wire in openings if a == axis and abs(at - lo) <= tol]
        high = [(at, p, wire) for a, at, p, wire in openings if a == axis and abs(at - hi) <= tol]
        in_plane = [candidate for candidate in "xyz" if candidate != axis]
        plane_axes = (in_plane[0], in_plane[1])
        for _lo_at, low_profile, low_wire in low:
            high_profile = next(
                (
                    profile
                    for _hi_at, profile, _high_wire in high
                    if _profiles_correspond(profile, low_profile, plane_axes, tol=tol)
                ),
                None,
            )
            if high_profile is None:
                continue
            axis_vector = (
                1.0 if axis == "x" else 0.0,
                1.0 if axis == "y" else 0.0,
                1.0 if axis == "z" else 0.0,
            )
            try:
                prism = Solid.extrude(
                    Face(low_wire),
                    Vector(*(component * (hi - lo) for component in axis_vector)),
                )
                overlap = part & prism
                volume_tol = max(tol**3, float(prism.volume) * 1e-6)
                # build123d returns ``None`` for a disjoint Solid/Solid boolean and an empty
                # Compound for the equivalent Compound/Solid operation. Both prove no material.
                overlap_volume = 0.0 if overlap is None else float(overlap.volume)
                if overlap_volume > volume_tol:
                    continue
            except Exception:  # noqa: BLE001 - a failed topology proof is not recognition
                continue
            location = list(high_profile.centre)
            location["xyz".index(axis)] = hi
            out.append(
                DoubleDBore(
                    axis=axis_vector,
                    location=(location[0], location[1], location[2]),
                    major_diameter=high_profile.major_diameter,
                    across_flats=high_profile.across_flats,
                    depth=round(hi - lo, 4),
                    through=True,
                    flat_direction=high_profile.flat_direction,
                )
            )
    return sorted(out, key=lambda bore: (bore.axis, bore.location, bore.major_diameter))


def _recognise_double_d_bores_one(part, *, tol: float) -> list[DoubleDBore]:
    """Recognise double-D bores within one solid's own boundary."""
    bbox = part.bounding_box()
    part_scale = max(float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z))
    scan_tol = max(tol, part_scale * 1e-5)
    openings: list[tuple[str, float, DoubleDProfile, object]] = []
    for face in part.faces():
        boundary = principal_boundary_plane(face, bbox)
        if boundary is None:
            continue
        axis, plane_axes, at = boundary
        for wire in face.inner_wires():
            profile = double_d_profile(wire, plane_axes, tol=scan_tol)
            if profile is not None:
                openings.append((axis, at, profile, wire))
    return double_d_bores_from_openings(openings, bbox, part=part, tol=scan_tol)


def recognise_double_d_bores(part, *, tol: float = 1e-5) -> list[DoubleDBore]:
    """Recognise principal-axis through double-D bores, independently per solid.

    This slice is deliberately through-only: the same profile must appear at both opposite
    extremal faces.  A single opening could be a blind recess, whose floor/depth needs a
    separate proof before it can be called a bore. Each solid owns its own extrema so two
    components in an assembly cannot be paired into one fictitious bore across their gap.
    """
    solids = list(part.solids())
    sources = solids if len(solids) > 1 else [part]
    bores = [bore for solid in sources for bore in _recognise_double_d_bores_one(solid, tol=tol)]
    return sorted(bores, key=lambda bore: (bore.axis, bore.location, bore.major_diameter))


def read_double_d_tool(obj, *, tol: float = 1e-5) -> tuple:
    """Read one constant double-D extrusion, rejecting merely matching end geometry."""
    bbox = obj.bounding_box()
    part_scale = max(float(bbox.size.X), float(bbox.size.Y), float(bbox.size.Z))
    scan_tol = max(tol, part_scale * 1e-5)
    ends: list[tuple[str, float, DoubleDProfile, object]] = []
    for face in obj.faces():
        boundary = principal_boundary_plane(face, bbox)
        if boundary is None:
            continue
        axis, plane_axes, at = boundary
        profile = double_d_profile(face.outer_wire(), plane_axes, tol=scan_tol)
        if profile is not None:
            ends.append((axis, at, profile, face.outer_wire()))

    for axis in "xyz":
        i = "xyz".index(axis)
        attr = axis.upper()
        lo = float(getattr(bbox.min, attr))
        hi = float(getattr(bbox.max, attr))
        in_plane = [candidate for candidate in "xyz" if candidate != axis]
        plane_axes = (in_plane[0], in_plane[1])
        low = [
            (profile, wire)
            for a, at, profile, wire in ends
            if a == axis and abs(at - lo) <= scan_tol
        ]
        high = [
            profile for a, at, profile, _wire in ends if a == axis and abs(at - hi) <= scan_tol
        ]
        for low_profile, low_wire in low:
            high_profile = next(
                (
                    profile
                    for profile in high
                    if _profiles_correspond(
                        low_profile,
                        profile,
                        (plane_axes[0], plane_axes[1]),
                        tol=scan_tol,
                    )
                ),
                None,
            )
            if high_profile is None:
                continue
            axis_vector = [0.0, 0.0, 0.0]
            axis_vector[i] = hi - lo
            try:
                prism = Solid.extrude(Face(low_wire), Vector(*axis_vector))
                overlap = obj & prism
                overlap_volume = 0.0 if overlap is None else float(overlap.volume)
                volume_tol = max(scan_tol**3, float(prism.volume) * 1e-6)
                if (
                    abs(overlap_volume - float(prism.volume)) > volume_tol
                    or abs(float(obj.volume) - float(prism.volume)) > volume_tol
                ):
                    continue
            except Exception:  # noqa: BLE001 - a failed topology proof is not a declaration
                continue
            centre = list(low_profile.centre)
            centre[i] = (lo + hi) / 2.0
            return (
                axis,
                low_profile.major_diameter,
                low_profile.across_flats,
                (centre[0], centre[1], centre[2]),
                hi - lo,
                low_profile.flat_direction,
            )
    raise ValueError(
        "double_d_bore(object) needs one constant extrusion of a two-chord, "
        "common-circle double-D profile"
    )
