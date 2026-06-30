"""levels — horizontal face-level recognition (ADR 0007).

``analyse_face_levels`` returns the Z-coords of a part's horizontal planar faces —
the step levels of a *prismatic* part. It is the complement of ``find_turned_steps``
(turned.py): a box-stepped part has no cylinders, so the OD-silhouette recogniser
cannot see its steps, while a turned shaft's shoulders are better filtered by the OD
silhouette than by a raw face scan (#191 — the two are dispatched by part class in
`analysis`, not duplicates). Bottom of the recognition DAG: depends only on
build123d/OCP.
"""

from __future__ import annotations

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Plane
from OCP.GProp import GProp_GProps


def analyse_face_levels(part, tol: float = 0.5, min_area_frac: float = 0.0) -> list:
    """Return sorted unique Z-coords of horizontal (normal≈±Z) planar faces.

    Uses tol-bucket deduplication but returns the actual face Z, not the rounded
    bucket centre, so dimension labels match the true geometry.

    When *min_area_frac* > 0, a Z level is kept only if the total area of its
    horizontal faces is at least ``min_area_frac × (x_size × y_size)`` (the
    part's plan footprint). This drops sub-feature faces — e.g. fragments of
    engraved text/numbers — that are not real steps and would otherwise be
    dimensioned as phantom shoulders (staircase.step review).
    """
    buckets: dict = {}  # bucket key -> representative z
    areas: dict = {}  # bucket key -> total horizontal-face area
    for face in part.faces():
        surf = BRepAdaptor_Surface(face.wrapped)
        if surf.GetType() == GeomAbs_Plane:
            ax = surf.Plane().Axis().Direction()
            if abs(ax.Z()) > 0.99:
                z = surf.Plane().Location().Z()
                key = round(z / tol) * tol
                buckets.setdefault(key, z)
                if min_area_frac > 0.0:
                    props = GProp_GProps()
                    BRepGProp.SurfaceProperties_s(face.wrapped, props)
                    areas[key] = areas.get(key, 0.0) + props.Mass()
    if min_area_frac > 0.0:
        bb = part.bounding_box()
        footprint = (bb.max.X - bb.min.X) * (bb.max.Y - bb.min.Y)
        threshold = min_area_frac * footprint
        return sorted(z for key, z in buckets.items() if areas.get(key, 0.0) >= threshold)
    return sorted(buckets.values())
