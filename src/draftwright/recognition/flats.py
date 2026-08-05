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
# A genuine flat's chord reaches the OD at *both* ends (radius R); a slot wall reaches it at
# one end only (the other abuts the slot floor). Two flats are opposed across the *same* axis
# line (not merely the same axis letter) within these mm tolerances.
_OD_REACH_TOL = 0.1
_AXIS_LINE_TOL = 0.1


def _both_chord_ends_reach_od(verts, ax, dv, nv, r):
    """A genuine flat is a chord of the OD: both transverse ends of the face lie *on* the
    cylinder (radius ≈ R). A slot/pocket near-wall — outward-facing but offset to one side of
    the axis — has one end on the OD and the other on the slot floor (radius < R), so it is
    rejected. ``dv`` is the axis direction, ``nv`` the (radial) face normal; the chord runs
    along ``nv × dv`` within the plane perpendicular to the axis."""
    cx = nv[1] * dv[2] - nv[2] * dv[1]
    cy = nv[2] * dv[0] - nv[0] * dv[2]
    cz = nv[0] * dv[1] - nv[1] * dv[0]
    cm = (cx * cx + cy * cy + cz * cz) ** 0.5
    if cm < 1e-9:  # normal parallel to axis (shouldn't happen — already radial-gated)
        return False
    cx, cy, cz = cx / cm, cy / cm, cz / cm
    lo_t = hi_t = lo_r = hi_r = None
    for vx, vy, vz in verts:
        rx, ry, rz = vx - ax[0], vy - ax[1], vz - ax[2]
        t = rx * cx + ry * cy + rz * cz  # position along the chord
        adot = rx * dv[0] + ry * dv[1] + rz * dv[2]
        px, py, pz = rx - adot * dv[0], ry - adot * dv[1], rz - adot * dv[2]
        rad = (px * px + py * py + pz * pz) ** 0.5  # perpendicular distance to the axis line
        if lo_t is None or t < lo_t:
            lo_t, lo_r = t, rad
        if hi_t is None or t > hi_t:
            hi_t, hi_r = t, rad
    return lo_r is not None and lo_r >= r - _OD_REACH_TOL and hi_r >= r - _OD_REACH_TOL


def _same_axis_line(a_ax, a_dir, b_ax):
    """Two radial flats are opposed across one shaft only if their turning axes are the *same
    line* — the vector between the axis points has no component perpendicular to the shared
    direction. Guards against pairing lone flats on two distinct parallel shafts."""
    vx, vy, vz = b_ax[0] - a_ax[0], b_ax[1] - a_ax[1], b_ax[2] - a_ax[2]
    adot = vx * a_dir[0] + vy * a_dir[1] + vz * a_dir[2]
    px, py, pz = vx - adot * a_dir[0], vy - adot * a_dir[1], vz - adot * a_dir[2]
    return (px * px + py * py + pz * pz) ** 0.5 <= _AXIS_LINE_TOL


@dataclass(frozen=True)
class Flat(Record):
    """A recognised machined flat on round stock. ``axis`` is the turning axis the stock is
    coaxial about ("x"/"y"/"z"); ``across`` is the across-flats size — flat-to-flat for a
    face opposed across the axis (double-D / hex A/F), else flat-to-opposite-OD (the D
    height); ``at`` is the flat face centre in part space (the callout leader's tip)."""

    axis: str
    across: float
    at: tuple[float, float, float]
    #: The stock's axis LINE — the two coordinates perpendicular to ``axis``, in xyz order
    #: with the axis component dropped (z → (x, y); x → (y, z); y → (x, z)).
    #:
    #: The axis letter alone cannot say whether two flats belong to the same piece of round
    #: stock, and that is the one thing both consumers need (#1013). Two faces at
    #: ``(-12.5, 0, 0)`` and ``(12.5, 0, 0)`` are one double-D — one A/F definition. Two at
    #: ``(0, 0, 0)`` and ``(100, 0, 0)`` are two parallel lobes — two independent ones. From
    #: ``axis``/``across``/``at`` those are the same shape of data, so the renderer collapsed
    #: the second case into one callout and coverage, mirroring it, called the result
    #: complete.
    #:
    #: ADR 0013's rule for a record that looks too thin: the fix is the record. The
    #: recogniser already had the owning cylinder in hand, so this costs nothing to carry.
    axis_line: tuple[float, float] = (0.0, 0.0)
    #: The owning stock's axial extent ``(lo, hi)`` along ``axis``.
    #:
    #: The axis line alone is NOT stock identity, and this code already knew it: #1015 taught
    #: the opposition test that "the same infinite axis is not the same piece of stock", and
    #: the same holds for grouping. Two D-shafts stacked coaxially with a gap share an axis
    #: line, so grouping on that alone merged two independent A/F definitions into one callout
    #: — the very defect #1013 set out to fix, in a coaxial arrangement instead of a parallel
    #: one (Codex #1035 r1).
    #:
    #: Together with ``axis_line`` this is the stock identity. Purely geometric — the
    #: recogniser's internal ``stock`` tuple also carries a solid index, which is a same-run
    #: equality check and deliberately NOT propagated: it is not stable across runs.
    stock_span: tuple[float, float] = (0.0, 0.0)


def recognise_flats(part, *, cyls=None) -> list[Flat]:
    """Recognise the machined flats of *part* (see module docstring). Returns one
    :class:`Flat` per qualifying planar face truncating round stock, sorted
    deterministically. Empty when the part has no round stock or no flat.

    Pass *cyls* — a precomputed ``analyse_cylinders(part)`` result — to avoid
    re-scanning the solid (mirrors ``recognise_holes``'s parameter, #703)."""
    z_cyls, cross_cyls = cyls if cyls is not None else analyse_cylinders(part)
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
            verts = [(v.X, v.Y, v.Z) for v in f.vertices()]
            if not _both_chord_ends_reach_od(verts, ax, d, nv, r):
                continue  # one end abuts a slot floor, not the OD — a recess wall, not a flat
            cands.append(
                {
                    "axis": c["axis"],
                    "axis_line": _axis_line(c["axis"], ax),
                    "stock_span": (round(c["s_lo"], 3), round(c["s_hi"], 3)),
                    "n": nv,
                    "s": s,
                    "r": r,
                    "at": pcv,
                    "ax": ax,
                    "dir": d,
                    # Which piece of stock this face was matched to, for the opposition test
                    # below. Internal to one recognition run — a same-run equality check, not
                    # an identity that propagates — so the solid index is safe here.
                    "stock": (c.get("solid_idx", 0), round(c["s_lo"], 3), round(c["s_hi"], 3)),
                }
            )
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
            if not _same_axis_line(cand["ax"], cand["dir"], other["ax"]):
                continue  # a lone flat on a *different* parallel shaft — not opposed
            if other["stock"] != cand["stock"]:
                # The same infinite axis is not the same piece of stock. Two lone D-flats on
                # separate COAXIAL regions were each taken for the other's opposite face, so
                # both got `across` = the sum of two unrelated chord offsets — a wrong number
                # on the feature's only size parameter (#1015). The axial extent separates
                # them; the axis line alone cannot.
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
                axis_line=cand["axis_line"],
                stock_span=cand["stock_span"],
            )
        )
    return sorted(out, key=lambda fl: (fl.axis, fl.at))


def _axis_line(axis: str, ax) -> tuple[float, float]:
    """The two coordinates of *ax* perpendicular to *axis* — the stock's axis line (#1013).

    Rounded to the same 3 dp as every other coordinate a record carries, so two faces on one
    piece of stock compare equal rather than differing in float noise.

    **Axis-ALIGNED stock only, and deliberately so.** Dropping the dominant axis letter's
    coordinate identifies a line only when the axis is along X, Y or Z. For a slanted
    cylinder — ``analyse_cylinders`` classifies one by its dominant component, so a
    ``(0.707, 0, 0.707)`` axis reads as ``"x"`` — this is neither canonical (the value depends
    on WHICH point along the axis the cylinder record reports) nor sufficient (two different
    slanted directions through one point encode identically). A canonical key would be the
    perpendicular foot from the origin plus the direction, as :func:`_same_axis_line` already
    computes for the opposition test.

    That is not done here because slanted flats do not render at all today: the callout is
    dropped (`flat_dropped`) before identity matters, so a canonical key would fix a
    component of an already-broken path with no observable improvement. #1036 covers both
    halves together. The narrow claim this function makes is the one it can keep.
    """
    keep = [i for i, letter in enumerate("xyz") if letter != axis]
    return (round(ax[keep[0]], 3), round(ax[keep[1]], 3))
