"""plates — thin-slab (plate/wall) recognition for multi-plate prismatics (ADR 0007).

``recognise_plates`` returns the plate/wall thicknesses of a prismatic part — the thin
extent of each slab that makes up an L-/T-/U-bracket and kin (#559). It is the
complement of the other prismatic recognisers: ``recognise_face_levels`` (levels.py)
finds a monotonic Z staircase and ``EnvelopeFeature`` gives the overall bbox, but
neither recovers a *plate thickness* that is (a) along X or Y, or (b) along Z yet
too thin to survive the step-ladder legibility gate. A single flat plate needs no
help — its thickness IS the envelope, already dimensioned by ``dim_height``.

A plate along axis *a* is a slab of solid material between two large parallel
planar faces perpendicular to *a*: an **outward-−a** face at the low coord and an
**outward-+a** face at the high coord (solid lies between them). The opposite
arrangement — +a at the low coord, −a at the high — is a *slot / channel* with air
between the faces, and is correctly rejected. Two gates keep it to genuine plates:

- **large area** — each bounding face must cover at least ``min_area_frac`` of the
  part's cross-section on that axis, so a small internal feature face (a
  counterbore floor, a boss end) is never read as a plate; and
- **thin** — the thickness must be under ``max_thick_frac`` of the part's overall
  extent on that axis, so the full-envelope span of a single flat plate (thickness
  == extent) is excluded (``dim_height``/envelope already own it). A slab thicker
  than that fraction of its axis reads as a block, not a plate, and is left to the
  step/envelope dims — the conservative side of the cut.

Only the low−a/high+a *adjacent* pair along an axis is a plate: a pairing that skips
an intervening face crosses an air gap (two stacked plates on a common post) and is
rejected, so a slab thickness never spans a void.

Bottom of the recognition DAG: depends only on build123d/OCP.
"""

from __future__ import annotations

from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Plane
from OCP.GProp import GProp_GProps


@dataclass(frozen=True)
class Plate:
    """A recognised thin slab. ``axis`` is the thin (thickness) axis ("x"/"y"/"z");
    ``lo``/``hi`` are the slab's two bounding coords along it (``hi - lo`` is the
    thickness); ``u``/``v`` are the slab centre on the other two axes (in axis order),
    a representative point the renderer places the thickness dim beside."""

    axis: str
    lo: float
    hi: float
    u: float
    v: float

    @property
    def thickness(self) -> float:
        return self.hi - self.lo


def recognise_plates(
    part,
    *,
    min_area_frac: float = 0.4,
    max_thick_frac: float = 0.5,
    tol: float = 0.5,
) -> list[Plate]:
    """Recognise the plate/wall thicknesses of a prismatic *part* (see module docstring).

    Returns one :class:`Plate` per recognised slab, deduplicated by (axis, lo, hi).
    Deterministic: sorted by (axis, lo, hi). Empty for a single flat plate (its
    thickness is the envelope) or a part with no thin slabs.
    """
    bb = part.bounding_box()
    ext = {"x": bb.max.X - bb.min.X, "y": bb.max.Y - bb.min.Y, "z": bb.max.Z - bb.min.Z}
    axidx = {"x": 0, "y": 1, "z": 2}

    # Collect, per axis, the large planar faces perpendicular to it — bucketed by
    # coord and split by OUTWARD-normal sign. `.normal_at()` respects face
    # orientation (the raw OCC plane axis is always +, useless for inside/outside).
    faces = [f for f in part.faces() if BRepAdaptor_Surface(f.wrapped).GetType() == GeomAbs_Plane]

    out: list[Plate] = []
    for axis, i in axidx.items():
        cross = 1.0
        for o in axidx:
            if o != axis:
                cross *= ext[o]
        if cross <= 0:
            continue
        neg: dict = {}  # coord bucket -> [total area, centre-u accum, centre-v accum]
        pos: dict = {}
        oi = [j for j in (0, 1, 2) if j != i]  # the two in-plane axis indices
        for f in faces:
            s = BRepAdaptor_Surface(f.wrapped)
            try:
                nv = f.normal_at()
            except Exception:  # noqa: BLE001 — a degenerate face has no clean normal
                continue
            comp = (nv.X, nv.Y, nv.Z)[i]
            if abs(comp) < 0.99:
                continue
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(f.wrapped, props)
            area = props.Mass()
            c = props.CentreOfMass()
            cp = (c.X(), c.Y(), c.Z())
            loc = (s.Plane().Location().X(), s.Plane().Location().Y(), s.Plane().Location().Z())[i]
            k = round(loc / tol) * tol
            bucket = pos if comp > 0 else neg
            acc = bucket.setdefault(k, [0.0, 0.0, 0.0])
            acc[0] += area
            acc[1] += cp[oi[0]] * area
            acc[2] += cp[oi[1]] * area

        thresh = min_area_frac * cross
        max_t = max_thick_frac * ext[axis]
        # A slab is a −a face IMMEDIATELY below a +a face with nothing between — solid
        # fills the gap. Sort all large faces along the axis and pair only *adjacent*
        # (−a, +a) neighbours: a −a low / +a high pairing that skips an intervening face
        # crosses an air gap (two stacked plates on a common post) and must not be read
        # as one plate. Same-coord ties order −a first so a degenerate pair is t≈0.
        events = [(c, -1, a, u, v) for c, (a, u, v) in neg.items() if a >= thresh]
        events += [(c, 1, a, u, v) for c, (a, u, v) in pos.items() if a >= thresh]
        events.sort(key=lambda e: (e[0], e[1]))
        for (c0, s0, a0, u0, v0), (c1, s1, a1, u1, v1) in zip(events, events[1:]):
            if s0 != -1 or s1 != 1:
                continue
            t = c1 - c0
            if t <= tol or t >= max_t:
                continue
            # Slab centre on the two in-plane axes — area-weighted over both faces.
            aw = a0 + a1
            u = (u0 + u1) / aw
            v = (v0 + v1) / aw
            out.append(Plate(axis=axis, lo=round(c0, 3), hi=round(c1, 3), u=u, v=v))

    # Dedup by (axis, lo, hi); keep the first (deterministic) representative point.
    seen: set = set()
    uniq: list[Plate] = []
    for p in sorted(out, key=lambda p: (p.axis, p.lo, p.hi)):
        key = (p.axis, p.lo, p.hi)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq
