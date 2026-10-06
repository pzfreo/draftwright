"""Exact fixed-annotation ink lowering for the shared feature-leader solve.

This owner converts settled rendered metadata and OCC residual faces to bounded,
component-named obstacles. Candidate ranking and assignment remain in leaders.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import islice
from typing import Any

from build123d import Face, Vector, Wire

from draftwright._geometry import _leader_ink_polygons, _stroke_polygon
from draftwright.annotations._common import DerivedViewReservation, _geom_box
from draftwright.fixed_ink_cache import _active_fixed_ink_mesh_cache
from draftwright.progress import checkpoint

_FIXED_INVENTORY_EXHAUSTED = object()


@dataclass(frozen=True)
class _FixedInkComponent:
    """One rendered fixed-annotation component with a stable trace identity."""

    name: str
    polygons: tuple[tuple[tuple[float, float], ...], ...] = ()
    box: tuple[float, float, float, float] | None = None
    owner: Any = None
    kind: str = ""
    segment: tuple[tuple[float, float], tuple[float, float]] | None = None
    global_axis: bool = False


def _coerce_segments(raw) -> tuple:
    out = []
    try:
        for segment in raw:
            first, second = segment
            parsed = (
                (float(first[0]), float(first[1])),
                (float(second[0]), float(second[1])),
            )
            if any(not math.isfinite(value) for point in parsed for value in point):
                return ()
            out.append(parsed)
    except Exception:  # noqa: BLE001 — optional metadata iterators may fail
        return ()
    return tuple(out)


def _coerce_box(raw):
    if raw is None:
        return None
    try:
        box = tuple(float(value) for value in raw)
    except Exception:  # noqa: BLE001 — optional metadata iterators may fail
        return None
    return (
        box
        if len(box) == 4
        and all(math.isfinite(value) for value in box)
        and box[0] < box[2]
        and box[1] < box[3]
        else None
    )


def _point_in_convex_component(point, polygon, *, tol=1e-6) -> bool:
    signs = []
    for first, second in zip(polygon, (*polygon[1:], polygon[0]), strict=True):
        cross = (second[0] - first[0]) * (point[1] - first[1]) - (second[1] - first[1]) * (
            point[0] - first[0]
        )
        if abs(cross) > tol:
            signs.append(cross > 0.0)
    return not signs or all(sign == signs[0] for sign in signs)


def _face_exactly_covered(face, polygons, label, *, tol=1e-8) -> bool:
    """Whether continuous OCC face ink is contained by analytical components."""

    try:
        if label is not None:
            bbox = face.bounding_box()
            if (
                label[0] - tol <= float(bbox.min.X)
                and label[1] - tol <= float(bbox.min.Y)
                and float(bbox.max.X) <= label[2] + tol
                and float(bbox.max.Y) <= label[3] + tol
            ):
                return True
        cover_faces = [
            Face(
                Wire.make_polygon(
                    [Vector(float(x), float(y), 0.0) for x, y in polygon],
                    close=True,
                )
            )
            for polygon in polygons
            if len(polygon) >= 3
        ]
        if not cover_faces:
            return False
        residual = face.cut(*cover_faces)
        # build123d 0.10 returns a ``ShapeList`` for multi-tool cuts, while
        # 0.11 returns one area-bearing shape.  Both represent the same OCC
        # residual; normalise that public-version boundary before applying the
        # continuous-ink containment test.
        if hasattr(residual, "area"):
            residual_area = float(residual.area)
        else:
            residual_area = sum(float(piece.area) for piece in residual)
        return bool(residual_area <= max(tol, abs(float(face.area)) * 1e-9))
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return False


def _validated_face_mesh(face, tolerance):
    """Return one complete finite triangular mesh, or ``None`` when malformed."""

    checkpoint()
    cache = _active_fixed_ink_mesh_cache()
    key = None
    if cache is not None:
        try:
            key = cache._key(face, tolerance)
            cached = cache.get(key)
            if cached is not None:
                return cached
        except Exception:  # noqa: BLE001 — optional cache must fail open to validation
            key = None
    try:
        vertices, raw_triangles = face.tessellate(tolerance)
        points = tuple((float(vertex.X), float(vertex.Y)) for vertex in vertices)
        if not points or any(not math.isfinite(value) for point in points for value in point):
            return None
        triangles = []
        referenced: set[int] = set()
        for raw_triangle in raw_triangles:
            triangle = tuple(raw_triangle)
            if (
                len(triangle) != 3
                or any(type(index) is not int for index in triangle)
                or len(set(triangle)) != 3
                or any(index < 0 or index >= len(points) for index in triangle)
            ):
                return None
            triangles.append(triangle)
            referenced.update(triangle)
        if not triangles or referenced != set(range(len(points))):
            return None
        edge_kinds = tuple(
            getattr(getattr(edge, "geom_type", None), "name", "") for edge in face.edges()
        )
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return None
    mesh = points, tuple(triangles), edge_kinds
    if cache is not None and key is not None:
        try:
            cache.put(key, mesh)
        except Exception:  # noqa: BLE001 — cache failure must not fail ink validation
            pass
    return mesh


def _convex_hull(points):
    """Deterministic convex hull for a small rendered face sample."""

    unique = sorted(set(points))
    if len(unique) < 3:
        return ()

    def cross(origin, first, second):
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
            second[0] - origin[0]
        )

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0.0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0.0:
            upper.pop()
        upper.append(point)
    return tuple((*lower[:-1], *upper[:-1]))


def _rendered_face_hull(face, *, curve_envelope=0.01):
    """Lower one planar OCC face to a bounded containing polygon.

    Straight-edged faces retain their exact vertices.  Curved helper ink is
    tessellated once while the fixed inventory is collected, then receives a
    tiny square envelope matching the tessellation tolerance.  Candidate
    exploration remains pure polygon arithmetic; unlike an annotation AABB,
    each dashed arc remains a local actual-width component and does not flood a
    circle's empty interior.
    """

    mesh = _validated_face_mesh(face, curve_envelope)
    if mesh is None:
        return (), (), (), ()
    points, triangles, edge_kinds = mesh
    curved = any(edge_kind != "LINE" for edge_kind in edge_kinds)
    if curved:
        hull_points = tuple(
            (x + dx, y + dy)
            for x, y in points
            for dx in (-curve_envelope, curve_envelope)
            for dy in (-curve_envelope, curve_envelope)
        )
    else:
        hull_points = points
    return _convex_hull(hull_points), points, tuple(triangles), edge_kinds


def _rendered_residual_components(
    name,
    annotation,
    components,
    label,
    owner,
    kind,
    *,
    max_components=None,
    allow_empty_faces=False,
):
    """Rendered faces not already represented by segment/label metadata.

    ``Dimension.segments`` is intentionally variable: a shifted label may
    suppress either dimension-line span while both arrowheads still render.
    ``CenterlineCircle`` exposes no linear segments because its chain ring is
    made from OCC arcs, while filled datum/GD&T glyphs expose faces that have no
    segment metadata at all. Face-local lowering handles every rendered
    annotation kind without guessing segment count/order and keeps stable
    component identities.
    """

    def component_covers(points):
        if label is not None and all(
            label[0] - 1e-6 <= point[0] <= label[2] + 1e-6
            and label[1] - 1e-6 <= point[1] <= label[3] + 1e-6
            for point in points
        ):
            return True
        return any(
            all(_point_in_convex_component(point, polygon, tol=1e-5) for point in points)
            for component in components
            for polygon in component.polygons
        )

    residual = []
    try:
        raw_faces = annotation.faces()
        if max_components is None:
            faces = tuple(raw_faces)
        else:
            faces = tuple(islice(iter(raw_faces), max_components + 1))
            if len(faces) > max_components:
                return _FIXED_INVENTORY_EXHAUSTED
    except Exception:  # noqa: BLE001 — optional placement must fail closed
        return None
    if not faces:
        # Some provisional/final furniture publishes a complete analytical
        # footprint through ``fixed_ink_polygons`` while its OCC carrier is
        # edge-only.  That explicit contract is sufficient.  Empty faces with
        # only generic segment/label metadata remain unavailable because those
        # fields may be only a partial description of the rendered object.
        return () if allow_empty_faces else None
    for face in faces:
        try:
            hull, rendered_points, triangles, edge_kinds = _rendered_face_hull(face)
            if not hull:
                return None
            represented = (
                rendered_points
                and triangles
                and all(
                    component_covers(tuple(rendered_points[index] for index in triangle))
                    for triangle in triangles
                )
            )
            curved = any(edge_kind != "LINE" for edge_kind in edge_kinds)
            if represented and (
                not curved
                or _face_exactly_covered(
                    face,
                    tuple(polygon for component in components for polygon in component.polygons),
                    label,
                )
            ):
                continue
            component_kind = (
                "arc"
                if kind == "CenterlineCircle"
                else "arrow"
                if kind == "Dimension"
                and (len(edge_kinds) == 3 or any(edge_kind != "LINE" for edge_kind in edge_kinds))
                else "ink"
            )
        except Exception:  # noqa: BLE001 — malformed rendered ink is unavailable
            return None
        residual.append((component_kind, hull))
        if max_components is not None and len(residual) > max_components:
            return _FIXED_INVENTORY_EXHAUSTED
    residual.sort(
        key=lambda item: (
            item[0],
            min(point[0] for point in item[1]),
            min(point[1] for point in item[1]),
            max(point[0] for point in item[1]),
            max(point[1] for point in item[1]),
        )
    )
    indices: dict[str, int] = {}
    out = []
    for component_kind, polygon in residual:
        index = indices.get(component_kind, 0)
        indices[component_kind] = index + 1
        out.append(
            _FixedInkComponent(
                f"{name}:{component_kind}:{index}",
                polygons=(polygon,),
                owner=owner,
                kind=kind,
            )
        )
    return tuple(out)


def _fixed_ink_metadata(annotation, max_components):
    """Read a bounded, complete snapshot of the annotation's exact ink metadata."""

    try:
        raw_segments = getattr(annotation, "segments", ()) or ()
        raw_polygons = getattr(annotation, "fixed_ink_polygons", ()) or ()
        raw_label = getattr(annotation, "label_bbox", None)
        global_axis = bool(getattr(annotation, "is_global_axis_centerline", False))
        if max_components is None:
            segment_prefix = tuple(raw_segments)
            raw_fixed_polygons = tuple(raw_polygons)
        else:
            segment_prefix = tuple(islice(iter(raw_segments), max_components + 1))
            if len(segment_prefix) > max_components:
                return _FIXED_INVENTORY_EXHAUSTED
            raw_fixed_polygons = tuple(islice(iter(raw_polygons), max_components + 1))
            if len(raw_fixed_polygons) > max_components:
                return _FIXED_INVENTORY_EXHAUSTED
    except Exception:  # noqa: BLE001 — unavailable fixed metadata is not exact ink
        return None
    segments = _coerce_segments(segment_prefix)
    if len(segments) != len(segment_prefix):
        return None
    return segments, raw_fixed_polygons, raw_label, global_axis


def _fixed_ink_hull(polygon):
    """Accept only a finite, nondegenerate polygon as an exact ink footprint."""

    try:
        points = tuple((float(point[0]), float(point[1])) for point in polygon)
    except Exception:  # noqa: BLE001 — partial exact metadata must fail closed
        return None
    if len(points) < 3 or any(
        not math.isfinite(coordinate) for point in points for coordinate in point
    ):
        return None
    hull = _convex_hull(points)
    return hull if len(hull) >= 3 else None


def _reserved_fixed_ink(dwg, name, annotation, max_components):
    """Return a complete block reservation when the annotation owns one."""
    if isinstance(annotation, DerivedViewReservation):
        # A future required view owns its entire planned rectangle, not merely
        # the strokes it will eventually draw. Keep it hard even in the bounded
        # feature-leader solve; optional section reservations use a separate
        # provisional flag and may yield to those leaders.
        return (
            _FixedInkComponent(
                f"{name}:reserved", box=annotation.box, kind="DerivedViewReservation"
            ),
        )

    if getattr(annotation, "table_rows", None) is not None:
        # A table reserves its cells as a block. Individual glyph/line faces leave
        # apparently free pockets where another annotation can cross a cell.
        if max_components is not None and max_components < 1:
            return _FIXED_INVENTORY_EXHAUSTED
        box = _coerce_box(_geom_box(annotation, getattr(dwg, "box_cache", None)))
        if box is None:
            if max_components is not None:
                return _FIXED_INVENTORY_EXHAUSTED
            box = (0.0, 0.0, float(dwg.page_w), float(dwg.page_h))
        return (_FixedInkComponent(f"{name}:table", box=box, kind="Table"),)
    return None


def _annotation_fixed_ink(dwg, name, annotation, *, max_components=None):
    """Exact-width fixed ink components for one already-rendered annotation."""

    reserved = _reserved_fixed_ink(dwg, name, annotation, max_components)
    if reserved is not None:
        return reserved

    components: list[_FixedInkComponent] = []
    owner = dwg.registry.feature_of(name)
    kind = type(annotation).__name__
    line_width = (
        0.15 if kind in {"CenterMark", "Centerline", "CenterlineCircle"} else dwg.draft.line_width
    )

    def unavailable():
        if max_components is not None:
            return _FIXED_INVENTORY_EXHAUSTED
        page = (0.0, 0.0, float(dwg.page_w), float(dwg.page_h))
        return (
            *components,
            _FixedInkComponent(
                f"{name}:geometry_unverified",
                box=page,
                owner=owner,
                kind=kind,
            ),
        )

    metadata = _fixed_ink_metadata(annotation, max_components)
    if metadata is _FIXED_INVENTORY_EXHAUSTED:
        return _FIXED_INVENTORY_EXHAUSTED
    if metadata is None:
        return unavailable()
    segments, raw_fixed_polygons, raw_label, global_axis = metadata
    if raw_label is not None and _coerce_box(raw_label) is None:
        return unavailable()

    def exhausted():
        return max_components is not None and len(components) > max_components

    for index, polygon in enumerate(raw_fixed_polygons):
        hull = _fixed_ink_hull(polygon)
        if hull is None:
            return unavailable()
        components.append(
            _FixedInkComponent(
                f"{name}:ink:{index}",
                polygons=(hull,),
                owner=owner,
                kind=kind,
            )
        )
        if exhausted():
            return _FIXED_INVENTORY_EXHAUSTED
    if type(annotation).__name__ == "Leader" and segments:
        components.append(
            _FixedInkComponent(
                f"{name}:segment:0",
                polygons=_leader_ink_polygons(
                    segments[0][0],
                    segments[0][1],
                    arrow_length=dwg.draft.arrow_length,
                    line_width=dwg.draft.line_width,
                ),
                owner=owner,
                kind=kind,
            )
        )
        if exhausted():
            return _FIXED_INVENTORY_EXHAUSTED
        segment_start = 1
    else:
        segment_start = 0
    for index, (first, second) in enumerate(segments[segment_start:], start=segment_start):
        polygon = _stroke_polygon(first, second, line_width)
        if polygon is not None:
            components.append(
                _FixedInkComponent(
                    f"{name}:segment:{index}",
                    polygons=(polygon,),
                    owner=owner,
                    kind=kind,
                    segment=(first, second),
                    global_axis=global_axis,
                )
            )
            if exhausted():
                return _FIXED_INVENTORY_EXHAUSTED
    label = _coerce_box(raw_label)
    if label is not None:
        components.append(_FixedInkComponent(f"{name}:label", box=label, owner=owner, kind=kind))
        if exhausted():
            return _FIXED_INVENTORY_EXHAUSTED

    # The stock helper Leader has a closed analytical description here: segment zero is
    # lowered by ``_leader_ink_polygons`` (shaft plus arrowhead), every later shelf segment
    # is lowered at exact line width, and ``label_bbox`` carries its text. Walking and
    # meshing all of its OCC faces only re-proves those same components. Dense real-part
    # sheets can contain dozens of leaders and were spending seconds per assembly on that
    # duplicate validation. Keep the exact-type gate: routed/custom leader subclasses may
    # render extra ink and must continue through the conservative residual-face scan.
    if type(annotation).__name__ == "Leader" and segments and label is not None:
        return tuple(components)

    remaining = None if max_components is None else max_components - len(components)
    residual = _rendered_residual_components(
        name,
        annotation,
        components,
        label,
        owner,
        kind,
        max_components=remaining,
        allow_empty_faces=bool(raw_fixed_polygons),
    )
    if residual is _FIXED_INVENTORY_EXHAUSTED:
        return _FIXED_INVENTORY_EXHAUSTED
    if residual is None:
        if max_components is not None:
            return _FIXED_INVENTORY_EXHAUSTED
        geometry = _coerce_box(_geom_box(annotation, getattr(dwg, "box_cache", None)))
        if geometry is None:
            return unavailable()
        else:
            components.append(
                _FixedInkComponent(
                    f"{name}:geometry",
                    box=geometry,
                    owner=owner,
                    kind=kind,
                )
            )
            if exhausted():
                return _FIXED_INVENTORY_EXHAUSTED
    else:
        components.extend(residual)
    if not components:
        geometry = _coerce_box(_geom_box(annotation, getattr(dwg, "box_cache", None)))
        if geometry is not None:
            components.append(
                _FixedInkComponent(f"{name}:geometry", box=geometry, owner=owner, kind=kind)
            )
            if exhausted():
                return _FIXED_INVENTORY_EXHAUSTED
    return tuple(components)
