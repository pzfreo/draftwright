"""chamfers — chamfer (bevelled edge) recognition for prismatic parts (ADR 0007).

``find_chamfers`` recovers the manufacturing size of each chamfer so it can be called
out (``C12`` / ``12 × 45°``) rather than left as a rendered-but-undimensioned bevel
(#560). A chamfer is a small **oblique** planar face — one whose normal is not aligned
with any principal axis (a 45° equal-leg chamfer on a Z edge has normal ≈ (0.707, 0.707,
0)) — that **bridges two mutually-perpendicular axis-aligned faces**, replacing a sharp
edge. Three gates keep it to genuine chamfers and recover the right size:

- **oblique** — the face's steepest normal component is < 0.99, excluding real
  axis-aligned faces and shallow draft angles; a turned part's conical chamfer is not
  planar (none found);
- **local size** — each leg (the chamfer face's own in-plane extent, so the size is read
  from the chamfer face itself, never from a distant outermost wall) is under
  ``max_leg_frac`` of the part on that axis, excluding a structural ramp/wedge/oversized
  bevel that spans the part; and
- **bridges two axis-aligned faces** — the face is edge-adjacent to axis-aligned faces on
  two distinct in-plane axes, excluding a hex/polygon prism's real oblique sides and a
  dovetail flank / gusset (whose neighbours are themselves oblique).

The two legs are the chamfer face's in-plane bbox extents, so an equal-leg and an
asymmetric chamfer are distinguished from the geometry, not estimated from the rendered
view. Bottom of the recognition DAG: depends only on build123d/OCP.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Plane


@dataclass(frozen=True)
class Chamfer:
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

    @property
    def equal_leg(self) -> bool:
        return abs(self.leg1 - self.leg2) < 0.05


def _axis_aligned_axis(face_wrapped) -> int | None:
    """The index of the axis a planar face's normal aligns with, or None if the face is
    not planar or not axis-aligned. Sign-agnostic (only alignment matters here)."""
    s = BRepAdaptor_Surface(face_wrapped)
    if s.GetType() != GeomAbs_Plane:
        return None
    d = s.Plane().Axis().Direction()
    comp = (abs(d.X()), abs(d.Y()), abs(d.Z()))
    return max(range(3), key=lambda i: comp[i]) if max(comp) > 0.99 else None


def find_chamfers(part, tol: float = 0.5, max_leg_frac: float = 0.5) -> list[Chamfer]:
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
        leg_u = span[oi[0]][1] - span[oi[0]][0]
        leg_v = span[oi[1]][1] - span[oi[1]][0]
        if leg_u < tol or leg_v < tol:
            continue
        if leg_u > max_leg_frac * ext[oi[0]] or leg_v > max_leg_frac * ext[oi[1]]:
            continue  # a structural ramp/wedge/oversized bevel spanning the part
        # Must bridge two axis-aligned faces on distinct in-plane axes (a chamfer replaces
        # a sharp 90° edge). A hex side / dovetail flank / gusset abuts oblique faces.
        my_edges = [e.wrapped for e in f.edges()]
        neigh_axes: set[int] = set()
        for g, g_edges in face_edges:
            if g.wrapped.IsSame(fw):
                continue
            if any(a.IsSame(b) for a in my_edges for b in g_edges):
                ax = _axis_aligned_axis(g.wrapped)
                if ax is not None and ax != edge_i:
                    neigh_axes.add(ax)
        if len(neigh_axes) < 2:
            continue
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
