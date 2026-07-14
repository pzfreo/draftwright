"""fillets — fillet (rounded edge) radius recognition for prismatic parts (ADR 0007).

``recognise_fillets`` recovers the radius of each external edge fillet so it can be
called out (``R3`` / ``4× R3``) rather than left as a rendered-but-undimensioned round
(#561). A fillet is the **arc analog** of a chamfer (:mod:`.chamfers`): where a chamfer
is a small *oblique planar* face bevelling a convex edge, a fillet is a small *partial
cylindrical* face rounding one. The gates mirror the chamfer's, swapping the plane test
for a cylinder test and the leg geometry for the cylinder radius:

- **partial cylinder** — the face's angular span is less than a bore's, so a real hole /
  boss (a full or near-full turn) is excluded; a convex edge fillet is a quarter-turn;
- **axis-aligned run** — the cylinder axis is one principal direction (the edge the fillet
  runs along); a compound corner round (a sphere-like blend) is skipped;
- **bridges two axis-aligned faces** on distinct in-plane axes — a fillet rounds a sharp
  90° edge between two axis-aligned walls (this also excludes a milled-slot end cap, whose
  two neighbours are parallel walls on the *same* axis); and
- **convex** — the virtual sharp corner the round replaces (where the two neighbour planes
  cross) lies *outside* the solid. An **internal** round filling a concave re-entrant
  corner buries that corner *inside* the material — the discriminator that face type +
  adjacency alone cannot make (the same convex test the chamfer recogniser uses), so an
  internal fillet / a slot-wall blend / a counterbore-floor round is excluded.

The radius is the cylinder radius, read from the geometry, not estimated from the view.
A too-small round (an edge-break / deburr, below ``min_radius``) is not a dimensioned
feature. Bottom of the recognition DAG: depends only on build123d/OCP.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_IN

from draftwright.recognition._record import Record

# A convex edge fillet is a quarter-turn (≈π/2); a real bore keeps more than half a turn.
# Gate below this (the same threshold that keeps partial cylinders out of hole/boss
# recognition, `_features._FULL_CYL_MIN_EXTENT`) so a bore is excluded; the convex test
# then drops the internal rounds / slot end caps (half turns) that also pass this gate.
_FILLET_MAX_EXTENT = math.pi * 1.05


@dataclass(frozen=True)
class Fillet(Record):
    """A recognised external edge fillet. ``axis`` is the rounded edge's direction
    ("x"/"y"/"z"); ``radius`` is the fillet radius (the cylinder radius); ``at`` is the
    fillet face centre in part space (the ``R`` callout leader's tip)."""

    axis: str
    radius: float
    at: tuple[float, float, float]


def _axis_aligned_axis(face_wrapped) -> tuple[int, float] | None:
    """``(axis_index, coordinate)`` of a planar axis-aligned face — the plane's axis and
    where it sits — or None if the face is not planar or not axis-aligned. Sign-agnostic
    (only alignment matters); the coordinate locates the plane for the convex-corner test."""
    s = BRepAdaptor_Surface(face_wrapped)
    if s.GetType() != GeomAbs_Plane:
        return None
    d = s.Plane().Axis().Direction()
    comp = (abs(d.X()), abs(d.Y()), abs(d.Z()))
    if max(comp) <= 0.99:
        return None
    ax = max(range(3), key=lambda i: comp[i])
    loc = s.Plane().Location()
    return ax, (loc.X(), loc.Y(), loc.Z())[ax]


def recognise_fillets(
    part, *, min_radius: float = 0.6, max_radius_frac: float = 0.45
) -> list[Fillet]:
    """Recognise the external edge fillets of *part* (see module docstring). Returns one
    :class:`Fillet` per qualifying cylindrical blend face, sorted deterministically. Empty
    when the part has no dimension-worthy fillet. Only single-axis fillets (running along
    one principal axis) are recovered; a compound corner round is skipped."""
    bb = part.bounding_box()
    max_ext = max(bb.max.X - bb.min.X, bb.max.Y - bb.min.Y, bb.max.Z - bb.min.Z)
    all_faces = list(part.faces())
    face_edges = [(g, [e.wrapped for e in g.edges()]) for g in all_faces]

    out: list[Fillet] = []
    for f in all_faces:
        fw = f.wrapped
        s = BRepAdaptor_Surface(fw)
        if s.GetType() != GeomAbs_Cylinder:
            continue
        if s.LastUParameter() - s.FirstUParameter() >= _FILLET_MAX_EXTENT:
            continue  # a full/near-full turn — a real bore/boss, not an edge blend
        cyl = s.Cylinder()
        radius = cyl.Radius()
        if radius < min_radius or radius > max_radius_frac * max_ext:
            continue  # a deburr edge-break (too small) or a large cove (not an edge break)
        d = cyl.Axis().Direction()
        comp = (abs(d.X()), abs(d.Y()), abs(d.Z()))
        if max(comp) <= 0.99:
            continue  # a compound corner round (axis not a principal direction) — out of scope
        edge_i = max(range(3), key=lambda i: comp[i])  # the edge the fillet runs along
        oi = [j for j in (0, 1, 2) if j != edge_i]  # the two in-plane axes it bridges
        fb = f.bounding_box()
        span = {0: (fb.min.X, fb.max.X), 1: (fb.min.Y, fb.max.Y), 2: (fb.min.Z, fb.max.Z)}
        fc = {i: 0.5 * (span[i][0] + span[i][1]) for i in (0, 1, 2)}  # face centre

        # Must bridge two axis-aligned faces on distinct in-plane axes (rounds a 90° edge).
        # Record each neighbour plane's coordinate so the convex test can rebuild the corner.
        my_edges = [e.wrapped for e in f.edges()]
        neigh_coord: dict[int, float] = {}
        for g, g_edges in face_edges:
            if g.wrapped.IsSame(fw):
                continue
            if any(a.IsSame(b) for a in my_edges for b in g_edges):
                aa = _axis_aligned_axis(g.wrapped)
                if aa is not None and aa[0] != edge_i:
                    ax, coord = aa
                    if ax not in neigh_coord or abs(coord - fc[ax]) < abs(
                        neigh_coord[ax] - fc[ax]
                    ):
                        neigh_coord[ax] = coord
        if oi[0] not in neigh_coord or oi[1] not in neigh_coord:
            continue

        # Convex-edge test (mirrors the chamfer's): the virtual sharp corner the round
        # replaces sits where the two neighbour planes cross, at the fillet's own edge
        # position. Nudged toward the fillet face it lands in the removed-round *vacuum* for
        # a real (convex) fillet (OUT), but in filled *material* for an internal round
        # bevelling a concave re-entrant corner (IN).
        corner = [0.0, 0.0, 0.0]
        corner[edge_i] = fc[edge_i]
        corner[oi[0]] = neigh_coord[oi[0]]
        corner[oi[1]] = neigh_coord[oi[1]]
        probe = tuple(corner[i] + 0.05 * (fc[i] - corner[i]) for i in (0, 1, 2))
        clsf = BRepClass3d_SolidClassifier(part.wrapped)
        clsf.Perform(gp_Pnt(*probe), 1e-6)
        if clsf.State() == TopAbs_IN:
            continue  # concave corner — an internal round / slot-wall blend, not an edge fillet

        # Anchor the leader on the curved radius surface itself, not the face bounding-box
        # centre — that centre sits near the arc's centre of curvature / virtual sharp corner,
        # off the surface (#622). Evaluate a point at the middle of the trimmed face's angular
        # (U) and axial (V) parameter spans; the adaptor's bounds are the FACE's trimmed range
        # (already gated below a full turn above), so a periodic seam is handled by using those
        # bounds directly rather than the raw 0..2π surface range. On-face for the ordinary
        # quarter-round edge blend (a UV-rectangular patch); a fillet whose UV region is punched
        # by an interior hole through its centre could still land mid-UV in that void — rare, and
        # no worse than the bbox centre it replaces (review).
        u_mid = 0.5 * (s.FirstUParameter() + s.LastUParameter())
        v_mid = 0.5 * (s.FirstVParameter() + s.LastVParameter())
        p = s.Value(u_mid, v_mid)
        out.append(
            Fillet(
                axis="xyz"[edge_i],
                radius=round(radius, 3),
                at=(round(p.X(), 3), round(p.Y(), 3), round(p.Z(), 3)),
            )
        )
    return sorted(out, key=lambda c: (c.axis, c.at))
