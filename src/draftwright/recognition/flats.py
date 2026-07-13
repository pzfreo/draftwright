"""flats — machined-flat recognition on round stock (ADR 0007, #148b).

``recognise_flats`` recovers the *across-flats* size of each machined flat — a single
planar face truncating round stock (a spanner flat / D-shaft / hex A/F) — so it can be
called out rather than left as a rendered-but-undimensioned chord. A flat is distinct
from a milled slot: a slot needs **two facing walls** a slot-width apart; a flat has
**one** face, cut against the curved OD, opening to the outside. The gates recover the
right feature from the geometry, not the rendered view:

- **on round stock** — the planar face is edge-adjacent to an *external* cylindrical face
  (the OD, from :func:`analyse_cylinders`), which supplies the turning axis and radius;
- **radial** — the face normal is perpendicular to that axis (a chord cut, not a
  transverse end/shoulder face whose normal runs *along* the axis);
- **faces outward** — the outward normal points *away from* the axis
  (``(centre − axis)·n̂ > 0``). This is the discriminator a slot wall fails: a slot wall's
  outward normal points *into* the slot void, back toward the axis (``< 0``). It cleanly
  separates a flat (one outward face) from a slot (two inward-facing walls), and admits
  every face of a double-D or hex (all face outward);
- **a real cut** — the plane sits inside the OD (``0 < d < R``) and removes more than a
  deburr's worth of material (``R − d`` above ``min_depth``), so a tangent sliver is not a
  flat.

The across-flats size is measured definitively: a flat opposed by a parallel flat across
the axis (double-D / hex) reads **flat-to-flat**; a lone flat reads **flat-to-opposite-OD**
(the D height, ``R + d``). The opposing flat is another recognised face, so no separate
size estimate is made. Bottom of the recognition DAG: depends only on build123d/OCP.
"""

from __future__ import annotations

from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Plane

from draftwright.recognition._features import analyse_cylinders
from draftwright.recognition._record import Record

# A normal counts as radial (perpendicular to the axis) / antiparallel to another within
# these unit-vector tolerances.
_RADIAL_TOL = 0.05
_ANTIPARALLEL_TOL = 0.05
# The plane must sit strictly inside the OD to be a chord cut (mm off the axis / off the OD).
_CHORD_MIN = 0.05
_CHORD_MARGIN = 0.05
# A flat must remove more than this depth of material (R − d); below it is a tangent sliver.
_MIN_FLAT_DEPTH = 0.5


@dataclass(frozen=True)
class Flat(Record):
    """A recognised machined flat on round stock. ``axis`` is the turning axis the stock is
    coaxial about ("x"/"y"/"z"); ``across`` is the across-flats size — flat-to-flat for a
    face opposed across the axis (double-D / hex A/F), else flat-to-opposite-OD (the D
    height); ``at`` is the flat face centre in part space (the callout leader's tip)."""

    axis: str
    across: float
    at: tuple[float, float, float]


def recognise_flats(part) -> list[Flat]:
    """Recognise the machined flats of *part* (see module docstring). Returns one
    :class:`Flat` per qualifying planar face truncating round stock, sorted
    deterministically. Empty when the part has no round stock or no flat."""
    z_cyls, cross_cyls = analyse_cylinders(part)
    ext = [c for c in (*z_cyls, *cross_cyls) if c.get("external")]
    if not ext:
        return []
    # Edge shapes of each external OD face, for O(faces × stock) adjacency.
    stock = [(c, [e.wrapped for e in c["face"].edges()]) for c in ext]

    # Phase 1 — collect candidate flat faces with the geometry the size needs.
    cands: list[dict] = []
    for f in part.faces():
        if BRepAdaptor_Surface(f.wrapped).GetType() != GeomAbs_Plane:
            continue
        try:
            nrm = f.normal_at(f.center())
        except Exception:  # noqa: BLE001 — a degenerate face has no clean normal
            continue
        nv = (nrm.X, nrm.Y, nrm.Z)
        pc = f.center()
        pcv = (pc.X, pc.Y, pc.Z)
        my_edges = [e.wrapped for e in f.edges()]
        for c, c_edges in stock:
            if not any(a.IsSame(b) for a in my_edges for b in c_edges):
                continue  # not adjacent to this OD
            d = c["dir_xyz"]
            if abs(nv[0] * d[0] + nv[1] * d[1] + nv[2] * d[2]) > _RADIAL_TOL:
                continue  # not radial (a transverse end/shoulder face)
            ax = c["axis_xyz"]
            s = (pcv[0] - ax[0]) * nv[0] + (pcv[1] - ax[1]) * nv[1] + (pcv[2] - ax[2]) * nv[2]
            r = c["diameter"] / 2
            if not (_CHORD_MIN < s < r - _CHORD_MARGIN):
                continue  # outward normal points toward the axis (a slot wall), or outside OD
            if r - s < _MIN_FLAT_DEPTH:
                continue  # a tangent sliver, not a machined flat
            cands.append({"axis": c["axis"], "n": nv, "s": s, "r": r, "at": pcv})
            break

    # Phase 2 — size each flat. A parallel flat opposed across the axis (antiparallel
    # normal, same stock axis) makes it flat-to-flat; otherwise flat-to-opposite-OD.
    out: list[Flat] = []
    for i, cand in enumerate(cands):
        n = cand["n"]
        opp = None
        for j, other in enumerate(cands):
            if j == i or other["axis"] != cand["axis"]:
                continue
            dot = n[0] * other["n"][0] + n[1] * other["n"][1] + n[2] * other["n"][2]
            if abs(dot + 1.0) <= _ANTIPARALLEL_TOL:
                opp = other
                break
        across = cand["s"] + opp["s"] if opp else cand["s"] + cand["r"]
        out.append(
            Flat(
                axis=cand["axis"],
                across=round(across, 3),
                at=(round(cand["at"][0], 3), round(cand["at"][1], 3), round(cand["at"][2], 3)),
            )
        )
    return sorted(out, key=lambda fl: (fl.axis, fl.at))
