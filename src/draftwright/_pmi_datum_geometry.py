"""Exact imported datum attachment geometry in source or recognition frame."""

from __future__ import annotations

import math
from typing import cast

from quiddity import PartFrame


def _bbox_centroid(bbox: tuple) -> tuple[float, float, float]:
    xmin, ymin, zmin, xmax, ymax, zmax = bbox
    return ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


def _frame_point(
    point: tuple[float, float, float], frame: PartFrame | None
) -> tuple[float, float, float]:
    """Express one source-space point in *frame*, preserving default extraction exactly."""
    if frame is None:
        return point
    return frame.to_local(point)


def _frame_vector(
    vector: tuple[float, float, float], frame: PartFrame | None
) -> tuple[float, float, float]:
    """Express one free vector in *frame* without applying the frame origin."""
    if frame is None:
        return vector
    return cast(
        tuple[float, float, float],
        tuple(
            sum(component * basis[index] for index, component in enumerate(vector))
            for basis in (frame.x, frame.y, frame.z)
        ),
    )


def _cylindrical_datum_site(face, bbox, axis_index, frame):
    """Find a normal axis-aligned projection site on the exact trimmed face."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    cylinder = BRepAdaptor_Surface(face).Cylinder()
    centre = _bbox_centroid(bbox)
    location = cylinder.Axis().Location()
    axis_site = _frame_point((location.X(), location.Y(), location.Z()), frame)
    radial_index = 2 if axis_index < 2 else 0
    preferred_signs = (-1, 1) if axis_index < 2 else (1, -1)
    # The upper station avoids CTC-01's lower source label, but a trimmed
    # sidewall may lose that station to a cross-hole. Try the midpoint too.
    stations: tuple[float, ...] = (centre[axis_index],)
    if axis_index == 2:
        stations = (centre[axis_index] + min((bbox[5] - bbox[2]) / 4, 10.0), *stations)
    for station in stations:
        for sign in preferred_signs:
            silhouette = list(axis_site)
            silhouette[axis_index] = station
            silhouette[radial_index] += sign * cylinder.Radius()
            local_site = tuple(silhouette)
            if not all(
                bbox[index] - 1e-6 <= local_site[index] <= bbox[index + 3] + 1e-6
                for index in range(3)
            ):
                continue
            world_site = local_site if frame is None else frame.to_world(local_site)
            vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*world_site)).Vertex()
            distance = BRepExtrema_DistShapeShape(vertex, face)
            distance.Perform()
            if distance.IsDone() and distance.Value() <= 1e-6:
                return local_site
    return None


def _datum_face_site(shape, bbox, kind, axis_index, frame):
    """Choose a proven face site, on the projected silhouette for cylinders."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt, gp_Pnt2d
    from OCP.TopAbs import TopAbs_IN
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    face = TopoDS.Face_s(shape)
    if kind == "cylinder":
        return _cylindrical_datum_site(face, bbox, axis_index, frame)
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    surface = BRepAdaptor_Surface(face)
    classifier = BRepClass_FaceClassifier()
    centre = _bbox_centroid(bbox)
    candidates = []
    if all(math.isfinite(value) for value in (u0, u1, v0, v1)):
        for ui in range(13):
            u = u0 + (ui + 0.5) * (u1 - u0) / 13
            for vi in range(13):
                v = v0 + (vi + 0.5) * (v1 - v0) / 13
                classifier.Perform(face, gp_Pnt2d(u, v), 1e-7)
                if classifier.State() == TopAbs_IN:
                    point = surface.Value(u, v)
                    candidates.append((point.X(), point.Y(), point.Z()))
    if not candidates:
        # A narrow ring can lie entirely between grid samples. Mesh triangles
        # cover the trimmed face, and their centroids supply interior sites.
        for deflection in (0.1, 0.01, 0.001, 0.0001):
            BRepMesh_IncrementalMesh(face, deflection, False, 0.5, False).Perform()
            location = TopLoc_Location()
            mesh = BRep_Tool.Triangulation_s(face, location)
            if mesh is None:
                continue
            transform = location.Transformation()
            for index in range(1, mesh.NbTriangles() + 1):
                vertices = mesh.Triangle(index).Get()
                nodes = [mesh.Node(vertex).Transformed(transform) for vertex in vertices]
                point = tuple(sum(node.Coord()[axis] for node in nodes) / 3 for axis in range(3))
                classifier.Perform(face, gp_Pnt(*point), 1e-7)
                if classifier.State() == TopAbs_IN:
                    candidates.append(point)
            if candidates:
                break
    sites = []
    for point in candidates:
        site = _frame_point(point, frame)
        hidden = 0 if axis_index == 1 else 1
        key = (
            abs(site[hidden] - centre[hidden]),
            sum((site[i] - centre[i]) ** 2 for i in range(3)),
        )
        sites.append((key, site))
    return min(sites)[1] if sites else None


def _datum_reference_normal(shapes, points, frame) -> tuple[tuple[float, ...], str]:
    """Measure a planar datum's oriented outward normal."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.TopAbs import TopAbs_FORWARD, TopAbs_REVERSED
    from OCP.TopoDS import TopoDS

    normals = []
    for shape, _site in zip(shapes, points, strict=True):
        face = TopoDS.Face_s(shape)
        surface = BRepAdaptor_Surface(face)
        kind = surface.GetType()
        if kind == GeomAbs_Cylinder:
            continue
        if kind != GeomAbs_Plane:
            return (), "datum reference surface has no supported oriented normal"
        direction = surface.Plane().Axis().Direction()
        vector: tuple[float, ...] = _frame_vector(
            (direction.X(), direction.Y(), direction.Z()), frame
        )
        orientation = face.Orientation()
        if orientation == TopAbs_REVERSED:
            vector = tuple(-component for component in vector)
        elif orientation != TopAbs_FORWARD:
            return (), "datum reference surface has no outward orientation"
        magnitude = math.sqrt(sum(component * component for component in vector))
        if magnitude < 1e-9:
            return (), "datum reference surface has no usable normal"
        normals.append(tuple(component / magnitude for component in vector))
    if not normals:
        return (), ""
    if any(
        sum(a * b for a, b in zip(normals[0], normal, strict=True)) < 0.999
        for normal in normals[1:]
    ):
        return (), "datum reference faces disagree about outward normal"
    return normals[0], ""
