"""Recognition of bounded, axis-aligned rectangular raised pads (#885)."""

from __future__ import annotations

from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Plane
from OCP.GProp import GProp_GProps

from draftwright.recognition._record import Record


@dataclass(frozen=True, order=True)
class RaisedPad(Record):
    """A bounded rectangular island, including its plan footprint and height."""

    x0: float
    x1: float
    y0: float
    y1: float
    z0: float
    z1: float


def recognise_rectangular_pads(part, *, tol: float = 0.2) -> list[RaisedPad]:
    """Return rectangular horizontal faces that are bounded in plan and raised.

    A candidate is a planar +Z face whose area fills its XY bounding rectangle
    and is bounded on both in-plane axes. Full-span steps are excluded;
    non-rectangular pocket floors and perforated plate faces fail the area test.
    """
    bb = part.bounding_box()
    horizontal_levels: list[float] = []
    for face in part.faces():
        surf = BRepAdaptor_Surface(face.wrapped)
        if surf.GetType() == GeomAbs_Plane:
            normal = surf.Plane().Axis().Direction()
            if abs(normal.Z()) > 0.99:
                horizontal_levels.append(float(surf.Plane().Location().Z()))
    out: set[RaisedPad] = set()
    for face in part.faces():
        surf = BRepAdaptor_Surface(face.wrapped)
        if surf.GetType() != GeomAbs_Plane:
            continue
        try:
            normal = face.normal_at()
        except Exception:  # noqa: BLE001 - degenerate faces are not pads
            continue
        if normal.Z < 0.99:
            continue
        fb = face.bounding_box()
        dx = fb.max.X - fb.min.X
        dy = fb.max.Y - fb.min.Y
        if dx <= tol or dy <= tol or fb.max.Z <= bb.min.Z + tol:
            continue
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face.wrapped, props)
        if abs(props.Mass() - dx * dy) > max(tol * tol, 0.005 * dx * dy):
            continue
        full_x = fb.min.X <= bb.min.X + tol and fb.max.X >= bb.max.X - tol
        full_y = fb.min.Y <= bb.min.Y + tol and fb.max.Y >= bb.max.Y - tol
        if full_x or full_y:
            continue
        below = [z for z in horizontal_levels if z < fb.max.Z - tol]
        if not below:
            continue
        out.add(
            RaisedPad(
                round(fb.min.X, 3),
                round(fb.max.X, 3),
                round(fb.min.Y, 3),
                round(fb.max.Y, 3),
                round(max(below), 3),
                round(fb.max.Z, 3),
            )
        )
    # A tiered/staircase tower exposes rectangular ledge fragments at several
    # heights. Those are correlated prismatic steps, not independent pads.
    # Stay conservative once the candidates form a multi-level staircase.
    if len({pad.z1 for pad in out}) > 2:
        return []

    # Keep coplanar repeated pads, but reject every member of a vertically nested stack.
    def overlaps_plan(a: RaisedPad, b: RaisedPad) -> bool:
        return (
            min(a.x1, b.x1) - max(a.x0, b.x0) > tol
            and min(a.y1, b.y1) - max(a.y0, b.y0) > tol
        )

    return sorted(
        pad
        for pad in out
        if not any(
            other != pad
            and abs(other.z1 - pad.z1) > tol
            and overlaps_plan(pad, other)
            for other in out
        )
    )
