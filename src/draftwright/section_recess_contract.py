"""Published SectionRecess geometry interpreted in Draftwright's drafting vocabulary.

This leaf validates provider values for both detection and independent completeness.
Occurrence ownership always retains the original provider record; the fields returned here
are consumer geometry, not replacement recognition records (ADR 3 and ADR 5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from math import dist, isfinite

from quiddity import (
    ClosedSectionProfile,
    OpenSectionProfile,
    PassageFrame,
    PassageSectionVertex,
    SectionEnd,
    SectionRecess,
    SectionRecessArray,
    SectionRecessClassification,
    SectionRecessEnds,
    SectionRecessEvidence,
    SectionRecessGeometry,
    SectionRecessGrid,
)


class UnsupportedSectionRecess(ValueError):
    """Valid recess geometry outside the first migration adapter's drawing vocabulary."""


def _section_object(value: object, keys: set[str], name: str) -> Mapping:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError(f"{name} must contain exactly {sorted(keys)}")
    return value


def _section_numbers(value: object, count: int, name: str) -> tuple[float, ...]:
    if (
        not isinstance(value, (tuple, list))
        or type(value) not in (tuple, list)
        or len(value) != count
    ):
        raise ValueError(f"{name} must contain {count} finite numbers")
    if any(type(item) not in (int, float) for item in value):
        raise ValueError(f"{name} requires built-in non-boolean numbers")
    try:
        result = tuple(float(item) for item in value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not all(isfinite(item) for item in result):
        raise ValueError(f"{name} must be finite")
    return result


def _section_axis(vector: tuple[float, ...]) -> tuple[int, int]:
    index = max(range(3), key=lambda i: abs(vector[i]))
    sign = 1 if vector[index] > 0 else -1
    if any(abs(value - (sign if i == index else 0)) > 1e-9 for i, value in enumerate(vector)):
        raise UnsupportedSectionRecess("pocket requires principal section and run directions")
    return index, sign


def section_recess_pocket_fields(record: Mapping, *, schema_version: int) -> dict:
    """Validate the schema-2 pocket projection and return consumer geometry fields."""

    if type(schema_version) is not int or schema_version != 2:
        raise ValueError("unsupported SectionRecess schema version")
    row = _section_object(
        record, {"index", "body", "geometry", "classification", "evidence"}, "occurrence"
    )
    if any(type(row[key]) is not int or row[key] < 0 for key in ("index", "body")):
        raise ValueError("occurrence and body indices must be non-negative integers")
    evidence = _section_object(
        row["evidence"], {"defining_faces", "constituent_faces"}, "evidence"
    )
    for refs in evidence.values():
        if (
            type(refs) not in (tuple, list)
            or any(type(ref) is not int or ref < 0 for ref in refs)
            or list(refs) != sorted(set(refs))
        ):
            raise ValueError("face references must be sorted unique non-negative integers")
    if not evidence["defining_faces"] or not set(evidence["defining_faces"]) <= set(
        evidence["constituent_faces"]
    ):
        raise ValueError("pocket requires defining faces contained in constituent faces")
    classification = _section_object(
        row["classification"], {"feature_kind", "section_shape"}, "classification"
    )
    kind, shape = classification["feature_kind"], classification["section_shape"]
    if (kind, shape) not in (("pocket", "rectangular"), ("edge_open_recess", "polygonal")):
        raise UnsupportedSectionRecess("recess is outside the rectangular pocket vocabulary")
    geometry = _section_object(
        row["geometry"], {"type", "frame", "run_interval", "profile", "ends"}, "geometry"
    )
    if geometry["type"] != "section_recess":
        raise ValueError("expected section_recess geometry")
    frame = _section_object(geometry["frame"], {"origin", "run", "u", "v"}, "frame")
    origin = _section_numbers(frame["origin"], 3, "frame origin")
    run, u, v = (_section_numbers(frame[key], 3, key) for key in ("run", "u", "v"))
    run_index, run_sign = _section_axis(run)
    u_index, u_sign = _section_axis(u)
    v_index, v_sign = _section_axis(v)
    if len({run_index, u_index, v_index}) != 3:
        raise ValueError("section frame directions must be perpendicular")
    cross = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    if any(abs(a - b) > 1e-9 for a, b in zip(cross, run)):
        raise ValueError("section frame must be right-handed")
    low, high = _section_numbers(geometry["run_interval"], 2, "run interval")
    if high <= low:
        raise ValueError("run interval must increase")
    ends = _section_object(geometry["ends"], {"low", "high"}, "ends")
    conditions = []
    for key in ("low", "high"):
        end = _section_object(ends[key], {"condition", "gradient"}, "end")
        conditions.append(end["condition"])
        if any(_section_numbers(end["gradient"], 2, "end gradient")):
            raise UnsupportedSectionRecess("pocket requires perpendicular run ends")
    if sorted(conditions, key=str) != ["capped", "open"]:
        raise ValueError("pocket requires exactly one capped and one open end")

    edge_anchored = kind == "edge_open_recess"
    profile = _section_object(
        geometry["profile"],
        {"closure", "boundary", "opening"} if edge_anchored else {"closure", "boundary"},
        "profile",
    )
    if profile["closure"] != ("open" if edge_anchored else "closed"):
        raise ValueError("profile closure disagrees with pocket classification")
    boundary = profile["boundary"]
    if type(boundary) not in (tuple, list) or len(boundary) not in (
        (3, 4) if edge_anchored else (4,)
    ):
        raise UnsupportedSectionRecess("pocket requires a rectangular boundary or open chain")
    points = []
    for item in boundary:
        vertex = _section_object(item, {"point", "bulge"}, "profile vertex")
        point = _section_numbers(vertex["point"], 2, "profile point")
        if _section_numbers([vertex["bulge"]], 1, "bulge")[0] != 0:
            raise UnsupportedSectionRecess("curved profiles need their own drafting semantics")
        points.append(point)
    if edge_anchored:
        opening = profile["opening"]
        if type(opening) not in (tuple, list) or len(opening) != 2:
            raise ValueError("opening must join the two loose endpoints")
        endpoints = tuple(_section_numbers(point, 2, "opening point") for point in opening)
        if endpoints != (points[-1], points[0]):
            raise ValueError("opening must join the two loose endpoints")
    bounds = [(min(p[i] for p in points), max(p[i] for p in points)) for i in range(2)]
    if any(hi <= lo for lo, hi in bounds):
        raise ValueError("pocket section must have positive extents")
    if len(set(points)) != len(points) or any(
        p[0] not in bounds[0] or p[1] not in bounds[1] for p in points
    ):
        raise UnsupportedSectionRecess("profile does not follow rectangular supports")
    chain = points if edge_anchored else [*points, points[0]]
    if any(sum(a[i] != b[i] for i in range(2)) != 1 for a, b in zip(chain, chain[1:])):
        raise UnsupportedSectionRecess("profile contains a diagonal or crossing support")

    return _pocket_from_bounds(
        origin,
        run,
        u,
        v,
        (low, high),
        bounds,
        edge_anchored=edge_anchored,
        open_high=conditions[1] == "open",
    )


def _pocket_from_bounds(origin, run, u, v, interval, bounds, *, edge_anchored, open_high) -> dict:
    run_index, run_sign = _section_axis(run)
    u_index, u_sign = _section_axis(u)
    v_index, v_sign = _section_axis(v)
    low, high = interval
    world_bounds = {
        run_index: sorted(origin[run_index] + run_sign * n for n in (low, high)),
        u_index: sorted(origin[u_index] + u_sign * n for n in bounds[0]),
        v_index: sorted(origin[v_index] + v_sign * n for n in bounds[1]),
    }
    span_index = max(
        (u_index, v_index), key=lambda i: (world_bounds[i][1] - world_bounds[i][0], i)
    )
    width_index = v_index if span_index == u_index else u_index
    center = tuple(lo / 2 + hi / 2 for lo, hi in (world_bounds[i] for i in range(3)))
    width = world_bounds[width_index][1] - world_bounds[width_index][0]
    length = world_bounds[span_index][1] - world_bounds[span_index][0]
    if not all(isfinite(n) for n in (*center, width, length, high - low)):
        raise ValueError("projected pocket measurements must be finite")
    if any(hi <= lo for lo, hi in world_bounds.values()):
        raise ValueError("projected pocket extents must remain positive")
    return dict(
        origin=(center[0], center[1], center[2]),
        axis="xyz"[run_index],
        width_axis="xyz"[width_index],
        long_axis="xyz"[span_index],
        width=width,
        length=length,
        depth=high - low,
        w_center=center[width_index],
        lo=world_bounds[span_index][0],
        hi=world_bounds[span_index][1],
        edge_anchored=edge_anchored,
        open_sign=run_sign * (1 if open_high else -1),
    )


_PUBLIC_RECORDS = frozenset(
    {
        SectionRecess,
        SectionRecessArray,
        SectionRecessGrid,
        SectionRecessGeometry,
        SectionRecessClassification,
        SectionRecessEvidence,
        SectionRecessEnds,
        SectionEnd,
        PassageFrame,
        ClosedSectionProfile,
        OpenSectionProfile,
        PassageSectionVertex,
    }
)


def _validate_public_value(value: object) -> None:
    """Check exact nested types and rerun their public constructor invariants."""
    value_type = type(value)
    if value_type in _PUBLIC_RECORDS:
        assert is_dataclass(value) and not isinstance(value, type)
        for item in fields(value):
            _validate_public_value(getattr(value, item.name))
        # Reconstruct only for validation: callers retain the original occurrence identity.
        replace(value)
    elif value_type is tuple:
        assert isinstance(value, tuple)
        for item in value:
            _validate_public_value(item)
    elif value_type in (int, float):
        _section_numbers((value,), 1, "published numeric value")
    elif value_type is not str:
        raise TypeError("SectionRecess must contain exact public record and primitive types")


def section_recess_fields(source: object) -> tuple[str, dict]:
    """Return bounded existing drafting semantics, or an explicit unsupported result.

    Only published frame, boundary and end values are read. No CAD surfaces are examined,
    and the original occurrence remains the authority for ownership and requirement counts.
    """
    if type(source) is not SectionRecess:
        raise TypeError("section recess inventory requires exact SectionRecess records")
    _validate_public_value(source)
    if not source.evidence.defining_faces:
        raise ValueError("section recess requires defining-face evidence")
    kind = source.classification.feature_kind
    shape = source.classification.section_shape
    if (kind, shape) == ("pocket", "obround"):
        return "pocket", _obround_pocket_fields(source)
    if (kind, shape) in (("pocket", "rectangular"), ("edge_open_recess", "polygonal")):
        return "pocket", section_recess_pocket_fields(source.to_dict(), schema_version=2)
    if kind not in ("edge_open_recess", "channel"):
        raise UnsupportedSectionRecess("profile has no supported drafting grammar")
    geometry = source.geometry
    if any((*geometry.ends.low.gradient, *geometry.ends.high.gradient)):
        raise UnsupportedSectionRecess("drafting grammar requires perpendicular run ends")
    frame = geometry.frame
    origin = frame.origin
    run_index, run_sign = _section_axis(frame.run)
    u_index, u_sign = _section_axis(frame.u)
    v_index, v_sign = _section_axis(frame.v)
    if len({run_index, u_index, v_index}) != 3:
        raise ValueError("section frame must use perpendicular principal directions")
    if type(geometry.profile) is not OpenSectionProfile:
        raise ValueError("channel and edge-open recess must have an open profile")
    vertices = geometry.profile.boundary
    if len(vertices) != 4:
        raise UnsupportedSectionRecess("open profile needs a supported four-vertex U section")
    points = tuple(vertex.point for vertex in vertices)
    # The opening is a published gap, never a manufactured closing physical edge.
    width_components = [i for i in range(2) if points[0][i] != points[-1][i]]
    if len(width_components) != 1:
        raise UnsupportedSectionRecess("U-section opening must follow a principal direction")
    width_local = width_components[0]
    depth_local = 1 - width_local
    depth_open = points[0][depth_local]
    depth_floor = points[1][depth_local]
    if points[2][depth_local] != depth_floor or depth_open == depth_floor:
        raise UnsupportedSectionRecess("U-section requires a parallel flat floor")
    width_index, width_sign = ((u_index, u_sign), (v_index, v_sign))[width_local]
    depth_index, depth_basis_sign = ((u_index, u_sign), (v_index, v_sign))[depth_local]
    depth_sign = depth_basis_sign * (1 if depth_open > depth_floor else -1)
    width_lo, width_hi = sorted((points[0][width_local], points[-1][width_local]))
    width = width_hi - width_lo
    depth = abs(depth_open - depth_floor)
    low, high = geometry.run_interval
    world_bounds = {
        run_index: sorted(origin[run_index] + run_sign * n for n in (low, high)),
        width_index: sorted(origin[width_index] + width_sign * n for n in (width_lo, width_hi)),
        depth_index: sorted(
            origin[depth_index] + depth_basis_sign * n for n in (depth_floor, depth_open)
        ),
    }
    center = tuple(sum(world_bounds[i]) / 2 for i in range(3))
    length = high - low
    if not all(isfinite(n) for n in (*center, width, depth, length)) or any(
        hi <= lo for lo, hi in world_bounds.values()
    ):
        raise ValueError("projected section measurements must remain finite and positive")
    common = dict(origin=center, axis="xyz"[run_index])
    bulges = tuple(vertex.bulge for vertex in vertices)
    straight = bulges == (0.0, 0.0, 0.0, 0.0)
    if straight:
        if shape != "rectangular" or any(
            points[a][width_local] != points[b][width_local] for a, b in ((0, 1), (2, 3))
        ):
            raise UnsupportedSectionRecess(
                "rectangular U-section requires two perpendicular walls"
            )
        if kind == "channel":
            return "channel", dict(
                **common,
                width_axis="xyz"[width_index],
                long_axis="xyz"[run_index],
                width=width,
                w_center=center[width_index],
                lo=world_bounds[run_index][0],
                hi=world_bounds[run_index][1],
                d_lo=world_bounds[depth_index][0],
                d_hi=world_bounds[depth_index][1],
                open_sign=depth_sign,
            )
        return "rectangular_blind_slot", dict(
            **common,
            width_axis="xyz"[width_index],
            depth_axis="xyz"[depth_index],
            open_sign=run_sign * (1 if geometry.ends.high.condition == "open" else -1),
            depth_sign=depth_sign,
            width=width,
            length=length,
            depth=depth,
        )
    if kind != "edge_open_recess" or shape != "general":
        raise UnsupportedSectionRecess("curved channel has no supported drafting grammar")
    # The existing round-bottom grammar is two equal quarter arcs and a straight floor.
    # Validate their signed sweeps as well as endpoints; a major arc is different geometry.
    quarter_bulge = 0.414213562373
    winding = (points[1][0] - points[0][0]) * (points[2][1] - points[1][1]) - (
        points[1][1] - points[0][1]
    ) * (points[2][0] - points[1][0])
    expected_bulge = quarter_bulge * (1 if winding > 0 else -1)
    inset = 1 if points[-1][width_local] > points[0][width_local] else -1
    if (
        bulges != (expected_bulge, 0.0, expected_bulge, 0.0)
        or abs(points[1][width_local] - points[0][width_local] - inset * depth) > 1e-9
        or abs(points[-1][width_local] - points[2][width_local] - inset * depth) > 1e-9
        or width <= 2 * depth
    ):
        raise UnsupportedSectionRecess("round-bottom profile requires two equal quarter arcs")
    return "round_bottom_blind_slot", dict(
        **common,
        width_axis="xyz"[width_index],
        depth_axis="xyz"[depth_index],
        open_sign=run_sign * (1 if geometry.ends.high.condition == "open" else -1),
        depth_sign=depth_sign,
        length=length,
        radius=depth,
        flat_width=width - 2 * depth,
    )


def recesses_with_kind(records: tuple, kind: str) -> tuple[SectionRecess, ...]:
    """Select a drafting grammar from the complete published inventory."""
    selected = []
    for record in records:
        try:
            actual, _ = section_recess_fields(record)
        except UnsupportedSectionRecess:
            continue
        if actual == kind:
            selected.append(record)
    return tuple(selected)


def section_recess_pattern_members(pattern, records: tuple) -> tuple[SectionRecess, ...]:
    """Validate published pattern geometry and resolve indices against this exact inventory."""
    if type(pattern) not in (SectionRecessArray, SectionRecessGrid):
        raise TypeError("recess patterns require exact public array or grid records")
    _validate_public_value(pattern)
    if type(records) is not tuple or any(type(record) is not SectionRecess for record in records):
        raise TypeError("recess pattern members require the exact immutable inventory")
    by_index = {record.index: record for record in records}
    if len(by_index) != len(records):
        raise ValueError("recess inventory repeats an occurrence index")
    if any(index not in by_index for index in pattern.members):
        raise ValueError("recess pattern member is outside the supplied inventory")
    members = tuple(by_index[index] for index in pattern.members)
    for member in members:
        _validate_public_value(member)
    first = members[0]
    if any(
        member.body != first.body
        or member.classification != first.classification
        or member.geometry.profile != first.geometry.profile
        or member.geometry.run_interval != first.geometry.run_interval
        or member.geometry.ends != first.geometry.ends
        or any(
            getattr(member.geometry.frame, axis) != getattr(first.geometry.frame, axis)
            for axis in ("run", "u", "v")
        )
        for member in members[1:]
    ):
        raise ValueError("recess pattern members do not have the same body-local geometry")
    positions = tuple(
        tuple(
            member.geometry.frame.origin[i]
            + sum(member.geometry.run_interval) / 2 * member.geometry.frame.run[i]
            for i in range(3)
        )
        for member in members
    )
    if type(pattern) is SectionRecessArray:
        center = tuple(sum(point[i] for point in positions) / len(positions) for i in range(3))
        expected = tuple(
            tuple(
                center[i]
                + (index - (len(positions) - 1) / 2) * pattern.pitch * pattern.direction[i]
                for i in range(3)
            )
            for index in range(len(positions))
        )
    else:
        expected = tuple(
            tuple(
                pattern.center[i]
                + (row - (pattern.rows - 1) / 2) * pattern.row_pitch * pattern.row_direction[i]
                + (col - (pattern.cols - 1) / 2) * pattern.col_pitch * pattern.col_direction[i]
                for i in range(3)
            )
            for row in range(pattern.rows)
            for col in range(pattern.cols)
        )
    unmatched = list(positions)
    for point in expected:
        matches = [i for i, actual in enumerate(unmatched) if dist(point, actual) <= 0.003]
        if len(matches) != 1:
            raise ValueError("recess pattern lattice does not identify each member exactly once")
        unmatched.pop(matches[0])
    if unmatched:
        raise ValueError("recess pattern omits a physical member")
    return members


def _obround_pocket_fields(source: SectionRecess) -> dict:
    """Measure an exact straight-sided capsule from its two published semicircular ends."""
    geometry = source.geometry
    if any((*geometry.ends.low.gradient, *geometry.ends.high.gradient)):
        raise UnsupportedSectionRecess("obround pocket requires perpendicular run ends")
    vertices = geometry.profile.boundary
    if len(vertices) != 4 or sorted(v.bulge for v in vertices) != [0.0, 0.0, 1.0, 1.0]:
        raise UnsupportedSectionRecess(
            "obround pocket requires two straight sides and semicircular ends"
        )
    arcs = [i for i, vertex in enumerate(vertices) if vertex.bulge == 1.0]
    if (arcs[1] - arcs[0]) % 4 != 2:
        raise UnsupportedSectionRecess("obround end arcs must be opposite")
    centers = [
        tuple((vertices[i].point[j] + vertices[(i + 1) % 4].point[j]) / 2 for j in range(2))
        for i in arcs
    ]
    long_axes = [j for j in range(2) if centers[0][j] != centers[1][j]]
    if len(long_axes) != 1:
        raise UnsupportedSectionRecess("obround pocket requires a principal long direction")
    long_axis = long_axes[0]
    width_axis = 1 - long_axis
    radii = []
    for i, center in zip(arcs, centers, strict=True):
        first, last = vertices[i].point, vertices[(i + 1) % 4].point
        if first[long_axis] != last[long_axis]:
            raise UnsupportedSectionRecess(
                "obround end chord must be perpendicular to its long direction"
            )
        radius = abs(last[width_axis] - first[width_axis]) / 2
        if radius <= 0:
            raise ValueError("obround radius must be positive")
        radii.append(radius)
        # Positive semicircle bulges traverse CCW: their midpoint lies to the chord's right.
        midpoint = (center[0] + (last[1] - first[1]) / 2, center[1] - (last[0] - first[0]) / 2)
        other = centers[1 if center == centers[0] else 0]
        if (midpoint[long_axis] - center[long_axis]) * (center[long_axis] - other[long_axis]) <= 0:
            raise UnsupportedSectionRecess("obround caps must face away from the straight span")
    if abs(radii[0] - radii[1]) > 1e-9:
        raise UnsupportedSectionRecess("obround end radii must agree")
    radius = radii[0]
    bounds = tuple(
        (min(c[j] for c in centers) - radius, max(c[j] for c in centers) + radius)
        for j in range(2)
    )
    for i, vertex in enumerate(vertices):
        if vertex.bulge == 0:
            last = vertices[(i + 1) % 4].point
            if vertex.point[width_axis] != last[width_axis]:
                raise UnsupportedSectionRecess("obround straight sides must be parallel")
    frame = geometry.frame
    return _pocket_from_bounds(
        frame.origin,
        frame.run,
        frame.u,
        frame.v,
        geometry.run_interval,
        bounds,
        edge_anchored=False,
        open_high=geometry.ends.high.condition == "open",
    )
