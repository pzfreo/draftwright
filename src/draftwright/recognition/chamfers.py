"""chamfers — chamfer (bevelled edge) recognition for prismatic parts (ADR 0007).

``find_chamfers`` recovers the manufacturing size of each chamfer so it can be called
out (``C12`` / ``12 × 45°``) rather than left as a rendered-but-undimensioned bevel
(#560). A chamfer is an **oblique** planar face — one whose normal is not aligned with
any principal axis (a 45° equal-leg chamfer on a Z edge has normal ≈ (0.707, 0.707, 0)).
The two legs (the depth the chamfer cuts into each adjacent face) and the angle are
recovered from where the chamfer plane meets the two adjacent axis-aligned faces, so an
equal-leg and an asymmetric chamfer are distinguished from the geometry, not estimated
from the rendered view.

Excluded by the obliqueness test: axis-aligned faces (a real face) and shallow draft
angles (their normal is within ~8° of an axis, so a component stays > 0.99); a turned
part's conical chamfer is not planar. Bottom of the recognition DAG: depends only on
build123d/OCP.
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
    chamfer); ``angle`` is the chamfer angle in degrees (45 for equal-leg); ``at`` is the
    chamfer face centre in part space (the callout leader's tip)."""

    axis: str
    leg1: float
    leg2: float
    angle: float
    at: tuple[float, float, float]

    @property
    def equal_leg(self) -> bool:
        return abs(self.leg1 - self.leg2) < 0.05


def find_chamfers(part, tol: float = 0.5) -> list[Chamfer]:
    """Recognise the chamfers of *part* (see module docstring). Returns one
    :class:`Chamfer` per oblique planar face, sorted deterministically. Empty when the
    part has no chamfer. Only single-axis chamfers (running along one principal axis) are
    recovered; a compound corner bevel (oblique on all three axes) is skipped."""
    faces = []
    for f in part.faces():
        s = BRepAdaptor_Surface(f.wrapped)
        if s.GetType() != GeomAbs_Plane:
            continue
        try:
            nvec = f.normal_at()
        except Exception:  # noqa: BLE001 — a degenerate face has no clean normal
            continue
        loc = s.Plane().Location()
        faces.append((f, (nvec.X, nvec.Y, nvec.Z), (loc.X(), loc.Y(), loc.Z())))

    def adj_coord(ai: int, sign: int) -> float | None:
        """Coord of the outermost axis-aligned face perpendicular to axis *ai* on the
        *sign* side (the face the chamfer bevels back from)."""
        best = None
        for _g, gnv, gloc in faces:
            if abs(gnv[ai]) > 0.99 and (1 if gnv[ai] > 0 else -1) == sign:
                if best is None or abs(gloc[ai]) > abs(best):
                    best = gloc[ai]
        return best

    out: list[Chamfer] = []
    for f, nv, p0 in faces:
        if max(abs(c) for c in nv) > 0.99:
            continue  # axis-aligned (a real face) or a shallow draft angle — not a chamfer
        # The chamfered edge runs along the axis whose normal component is ~0; the other
        # two carry the bevel. A compound corner (no ~0 component) is skipped.
        edge_i = next((i for i in (0, 1, 2) if abs(nv[i]) < 0.05), None)
        if edge_i is None:
            continue
        oi = [j for j in (0, 1, 2) if j != edge_i]
        if abs(nv[oi[0]]) < 0.05 or abs(nv[oi[1]]) < 0.05:
            continue  # degenerate — would divide by ~0 below
        cA = adj_coord(oi[0], 1 if nv[oi[0]] > 0 else -1)
        cB = adj_coord(oi[1], 1 if nv[oi[1]] > 0 else -1)
        if cA is None or cB is None:
            continue
        # Chamfer plane nv·(X − p0) = 0. Where it meets face A (coord oi0 == cA), solve
        # oi1; where it meets face B, solve oi0. The corner is (cA, cB); each leg is the
        # cut depth from that corner to where the chamfer meets the adjacent face.
        y_at_A = p0[oi[1]] - nv[oi[0]] * (cA - p0[oi[0]]) / nv[oi[1]]
        x_at_B = p0[oi[0]] - nv[oi[1]] * (cB - p0[oi[1]]) / nv[oi[0]]
        leg_a = abs(cB - y_at_A)  # depth into face A (toward the corner along oi1)
        leg_b = abs(cA - x_at_B)  # depth into face B (along oi0)
        if leg_a < tol or leg_b < tol:
            continue
        angle = math.degrees(math.atan2(leg_a, leg_b))
        out.append(
            Chamfer(
                axis="xyz"[edge_i],
                leg1=round(leg_b, 3),
                leg2=round(leg_a, 3),
                angle=round(angle, 2),
                at=(round(p0[0], 3), round(p0[1], 3), round(p0[2], 3)),
            )
        )
    return sorted(out, key=lambda c: (c.axis, c.at))
