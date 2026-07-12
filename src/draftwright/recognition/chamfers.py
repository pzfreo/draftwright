"""chamfers — chamfer (bevelled edge) recognition for prismatic parts (ADR 0007).

``recognise_chamfers`` recovers the manufacturing size of each chamfer so it can be called
out (``C12`` / ``12 × 45°``) rather than left as a rendered-but-undimensioned bevel
(#560). A chamfer is a small **oblique** planar face — one whose normal is not aligned
with any principal axis (a 45° equal-leg chamfer on a Z edge has normal ≈ (0.707, 0.707,
0)) — that breaks a **convex** edge where two mutually-perpendicular axis-aligned faces
meet. Four gates keep it to genuine chamfers and recover the right size:

- **oblique** — the face's steepest normal component is < 0.99, excluding real
  axis-aligned faces and shallow draft angles; a turned part's conical chamfer is not
  planar (none found);
- **bridges two axis-aligned faces** — the face is edge-adjacent to axis-aligned faces on
  two distinct in-plane axes, excluding a hex/polygon prism's real oblique sides (whose
  neighbours are themselves oblique);
- **convex** — the virtual sharp corner the bevel replaces (where the two neighbour planes
  cross, at the chamfer's own edge position) lies *outside* the solid. A gusset / rib /
  web fills a **concave** re-entrant corner, so that corner point is buried *inside* the
  material — the discriminator that face-normal + adjacency alone cannot make (a gusset's
  hypotenuse is also edge-adjacent to two perpendicular walls); and
- **not a spanning wedge** — a chamfer's cut is small relative to the part's *overall*
  size, so its larger leg stays under ``max_leg_frac`` of the part's largest dimension; a
  structural ramp/wedge/oversized bevel spans a large fraction and is excluded. Gating
  against the whole-part size (not each leg's own axis extent) keeps a legitimate plate
  edge-break — small in absolute terms — whose cut into a thin thickness axis would trip a
  per-axis fraction of that small extent, while still rejecting a long shallow ramp.

The two legs are the chamfer face's in-plane bbox extents, so an equal-leg and an
asymmetric chamfer are distinguished from the geometry, not estimated from the rendered
view. Bottom of the recognition DAG: depends only on build123d/OCP.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.GeomAbs import GeomAbs_Plane
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_IN

from draftwright.recognition._record import Record


@dataclass(frozen=True)
class Chamfer(Record):
    """A recognised chamfer. ``axis`` is the chamfered edge's direction ("x"/"y"/"z");
    ``leg1``/``leg2`` are the cut depths into the two adjacent faces (equal for a 45°
    chamfer, ``leg1`` the larger); ``angle`` is the chamfer angle in degrees (45 for
    equal-leg); ``at`` is the chamfer face centre in part space (the callout leader's
    tip)."""

    axis: str
    leg1: float
    leg2: float
    angle: float
    at: tuple[float, float, float]


def _axis_aligned_axis(face_wrapped) -> tuple[int, float] | None:
    """The axis a planar face's normal aligns with and that plane's fixed coordinate along
    it, or None if the face is not planar or not axis-aligned. Sign-agnostic (only
    alignment matters here); the coordinate locates the plane for the convex-corner test."""
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


def recognise_chamfers(part, *, tol: float = 0.5, max_leg_frac: float = 0.45) -> list[Chamfer]:
    """Recognise the chamfers of *part* (see module docstring). Returns one
    :class:`Chamfer` per qualifying oblique face, sorted deterministically. Empty when the
    part has no chamfer. Only single-axis chamfers (running along one principal axis) are
    recovered; a compound corner bevel (oblique on all three axes) is skipped."""
    bb = part.bounding_box()
    ext = {0: bb.max.X - bb.min.X, 1: bb.max.Y - bb.min.Y, 2: bb.max.Z - bb.min.Z}
    all_faces = list(part.faces())
    # (face, its edges' wrapped shapes) once, for O(faces²) edge-adjacency.
    face_edges = [(g, [e.wrapped for e in g.edges()]) for g in all_faces]

    out: list[Chamfer] = []
    for f in all_faces:
        fw = f.wrapped
        s = BRepAdaptor_Surface(fw)
        if s.GetType() != GeomAbs_Plane:
            continue
        try:
            nvec = f.normal_at()
        except Exception:  # noqa: BLE001 — a degenerate face has no clean normal
            continue
        nv = (nvec.X, nvec.Y, nvec.Z)
        if max(abs(c) for c in nv) > 0.99:
            continue  # axis-aligned (a real face) or a shallow draft angle — not a chamfer
        edge_i = next((i for i in (0, 1, 2) if abs(nv[i]) < 0.05), None)
        if edge_i is None:
            continue  # a compound corner bevel (oblique on all axes) — out of scope
        oi = [j for j in (0, 1, 2) if j != edge_i]
        if abs(nv[oi[0]]) < 0.05 or abs(nv[oi[1]]) < 0.05:
            continue
        # Legs = the chamfer face's OWN in-plane bbox extents — read from the face itself,
        # not measured against a (possibly distant) outermost wall.
        fb = f.bounding_box()
        span = {0: (fb.min.X, fb.max.X), 1: (fb.min.Y, fb.max.Y), 2: (fb.min.Z, fb.max.Z)}
        fc = {i: 0.5 * (span[i][0] + span[i][1]) for i in (0, 1, 2)}  # face centre
        leg_u = span[oi[0]][1] - span[oi[0]][0]
        leg_v = span[oi[1]][1] - span[oi[1]][0]
        if leg_u < tol or leg_v < tol:
            continue
        if max(leg_u, leg_v) > max_leg_frac * max(ext.values()):
            continue  # a ramp/wedge spanning a large fraction of the part — not an edge break
        # Must bridge two axis-aligned faces on distinct in-plane axes (a chamfer replaces
        # a sharp 90° edge). A hex side abuts oblique faces. Record each neighbour plane's
        # coordinate so the convex-corner test below can reconstruct the virtual corner.
        my_edges = [e.wrapped for e in f.edges()]
        neigh_coord: dict[int, float] = {}
        for g, g_edges in face_edges:
            if g.wrapped.IsSame(fw):
                continue
            if any(a.IsSame(b) for a in my_edges for b in g_edges):
                aa = _axis_aligned_axis(g.wrapped)
                if aa is not None and aa[0] != edge_i:
                    ax, coord = aa
                    # If two neighbours share an axis, keep the one nearest the chamfer —
                    # it forms this local corner.
                    if ax not in neigh_coord or abs(coord - fc[ax]) < abs(
                        neigh_coord[ax] - fc[ax]
                    ):
                        neigh_coord[ax] = coord
        if oi[0] not in neigh_coord or oi[1] not in neigh_coord:
            continue
        # Convex-edge test: the virtual sharp corner the bevel replaces sits where the two
        # neighbour planes cross, at the chamfer's own edge position. Nudged a little toward
        # the chamfer face it lands in the removed-wedge *vacuum* for a real (convex)
        # chamfer (OUT), but in filled *material* for a gusset/rib/web bevelling a concave
        # re-entrant corner (IN). The nudge clears the on-boundary knife-edge at the raw
        # corner; this is the discriminator adjacency alone can't make (#560 review).
        corner = [0.0, 0.0, 0.0]
        corner[edge_i] = fc[edge_i]
        corner[oi[0]] = neigh_coord[oi[0]]
        corner[oi[1]] = neigh_coord[oi[1]]
        probe = tuple(corner[i] + 0.05 * (fc[i] - corner[i]) for i in (0, 1, 2))
        clsf = BRepClass3d_SolidClassifier(part.wrapped)
        clsf.Perform(gp_Pnt(*probe), 1e-6)
        if clsf.State() == TopAbs_IN:
            continue  # concave corner — a gusset / rib / web, not a chamfer
        loc = s.Plane().Location()
        angle = math.degrees(math.atan2(min(leg_u, leg_v), max(leg_u, leg_v)))
        out.append(
            Chamfer(
                axis="xyz"[edge_i],
                leg1=round(max(leg_u, leg_v), 3),
                leg2=round(min(leg_u, leg_v), 3),
                angle=round(angle, 2),
                at=(round(loc.X(), 3), round(loc.Y(), 3), round(loc.Z(), 3)),
            )
        )
    return sorted(out, key=lambda c: (c.axis, c.at))
