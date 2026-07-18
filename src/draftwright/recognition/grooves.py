"""grooves — turned / circlip-groove recognition on round stock (ADR 0007, #148c).

A *groove* is an annular channel turned into round stock — a circlip / retaining-ring
groove, an O-ring groove, a run-out relief. It reads as a *narrow* band of the OD whose diameter
is a **strict local minimum**: smaller than the contiguous bands on *both* axial sides,
bounded by the two annular walls where the OD steps down then up again. It is a channel cut in
uniform stock — the two walls step back to the **same OD**, and the band is **narrower than the
wider wall** (not a segment of an alternating fine-step staircase). It is dimensioned
by its **width** (the axial span) and its **floor diameter** — not as a slot (whose walls
are rectangular / radial, so :func:`recognise_slots` rejects it) and not as a plain step
(a *monotonic* OD change, one wall, handled by :func:`recognise_turned_steps`).

The recognition is definitive from :func:`analyse_cylinders`. The two annular walls a groove
must have are implied by the band structure — a smaller band contiguous with a larger band
on each side *is* a step-down then a step-up — so no separate wall-face search is needed, and
the corner fillets/chamfers a real groove carries are tori / cones (never cylinders), so they
never appear as spurious bands. Bands are grouped by axis **line** (not merely the axis
letter) so two lone grooves on distinct parallel shafts are never confused for one channel.
Bottom of the recognition DAG: depends only on the owned ``analyse_cylinders`` primitive.
"""

from __future__ import annotations

from dataclasses import dataclass

from draftwright.recognition._features import analyse_cylinders
from draftwright.recognition._record import Record

# Two bands are axially contiguous (one is the other's wall) when the gap between them is
# within this (mm). A neighbour must be wider than the floor by more than this (mm of
# diameter) to count as a step up out of the groove — enough to reject turning noise, far
# less than any real groove depth.
_ADJ_TOL = 0.1
_DIA_MARGIN = 0.2
# A groove is a *narrow channel* cut into UNIFORM stock. Two signatures separate it from a
# segment of an alternating fine-step staircase:
#  - its two walls step back to (nearly) the same OD — within this (mm). Unequal walls are a
#    stepped profile (a shoulder), not a channel in round bar.
_WALL_DIA_TOL = 0.5
#  - it is narrower than the WIDER of its two walls (by more than this, mm). A band as wide as
#    its walls is a staircase step; an end-adjacent groove keeps one wide wall (the shaft
#    continues) even when the other is a thin retaining land, so the *wider* wall is the test.
_WIDTH_MARGIN = 0.05


def _shaft_key(c) -> tuple:
    """The axis line a band is turned about: the axis letter plus the axis point projected
    onto the plane perpendicular to the axis direction (position-independent along the axis),
    plus the owning solid. Bands with the same key are coaxial on one shaft; distinct parallel
    shafts differ, and — like the sibling ``_line_key`` (#68) — coaxial bands in *separate*
    butted solids stay distinct so three stacked bodies are never misread as one channel."""
    px, py, pz = c["axis_xyz"]
    dx, dy, dz = c["dir_xyz"]
    t = px * dx + py * dy + pz * dz
    return (
        c["axis"],
        c["solid_idx"],
        round(px - t * dx, 2),
        round(py - t * dy, 2),
        round(pz - t * dz, 2),
    )


@dataclass(frozen=True)
class Groove(Record):
    """A recognised turned / circlip groove on round stock. ``axis`` is the turning axis the
    stock is coaxial about ("x"/"y"/"z"); ``width`` is the groove's axial span (wall to
    wall); ``diameter`` is the floor (reduced-OD) diameter; ``at`` is the groove centre on
    the axis (the callout leader's tip)."""

    axis: str
    width: float
    diameter: float
    at: tuple[float, float, float]


def floor_face_anchor(face) -> tuple[float, float, float]:
    """The groove leader-tip anchor: the **bbox centre** of the floor face (the reduced-OD
    band), unrounded. The one anchor contract shared by :func:`recognise_grooves` and the
    declared front-end (``model/declare._read_groove_face``), #704 — the substrates differ
    (band dicts from ``analyse_cylinders`` vs a single user-supplied face), but the leader
    tip must not."""
    bb = face.bounding_box()
    return (
        0.5 * (bb.min.X + bb.max.X),
        0.5 * (bb.min.Y + bb.max.Y),
        0.5 * (bb.min.Z + bb.max.Z),
    )


def recognise_grooves(part, *, cyls=None) -> list[Groove]:
    """Recognise the turned grooves of *part* (see module docstring). Returns one
    :class:`Groove` per external band whose OD is a strict local minimum between two
    contiguous larger bands on the same shaft, sorted deterministically. Empty when the
    part has no round stock or no groove.

    Pass *cyls* — a precomputed ``analyse_cylinders(part)`` result — to avoid
    re-scanning the solid (mirrors ``recognise_holes``'s parameter, #703)."""
    z_cyls, cross_cyls = cyls if cyls is not None else analyse_cylinders(part)
    ext = [c for c in (*z_cyls, *cross_cyls) if c.get("external")]
    if not ext:
        return []

    shafts: dict[tuple, list] = {}
    for c in ext:
        shafts.setdefault(_shaft_key(c), []).append(c)

    out: list[Groove] = []
    for bands in shafts.values():
        bands = sorted(bands, key=lambda c: c["s_lo"])
        for i in range(1, len(bands) - 1):
            prev, cur, nxt = bands[i - 1], bands[i], bands[i + 1]
            # The neighbours must be the groove's own walls — contiguous with the floor band.
            if abs(prev["s_hi"] - cur["s_lo"]) > _ADJ_TOL:
                continue
            if abs(cur["s_hi"] - nxt["s_lo"]) > _ADJ_TOL:
                continue
            # A strict local OD minimum: the OD steps *down* into the band and *up* out of it.
            # A monotonic change (a plain step / shoulder) fails one side and is not a groove.
            if cur["diameter"] > prev["diameter"] - _DIA_MARGIN:
                continue
            if cur["diameter"] > nxt["diameter"] - _DIA_MARGIN:
                continue
            # Cut into uniform stock: the two walls step back to (nearly) the same OD. Unequal
            # walls are a shoulder / stepped profile, not an annular channel.
            if abs(prev["diameter"] - nxt["diameter"]) > _WALL_DIA_TOL:
                continue
            # A narrow channel: narrower than the WIDER of its two walls. A band as wide as its
            # walls is a staircase step (#148c review); an end-adjacent groove keeps one wide
            # wall (the shaft) even when the other is a thin retaining land, so test the wider.
            cur_w = cur["s_hi"] - cur["s_lo"]
            wider_wall = max(prev["s_hi"] - prev["s_lo"], nxt["s_hi"] - nxt["s_lo"])
            if cur_w >= wider_wall - _WIDTH_MARGIN:
                continue
            cx, cy, cz = floor_face_anchor(cur["face"])
            at = (round(cx, 3), round(cy, 3), round(cz, 3))
            out.append(
                Groove(
                    axis=cur["axis"],
                    width=round(cur["s_hi"] - cur["s_lo"], 3),
                    diameter=round(cur["diameter"], 3),
                    at=at,
                )
            )
    return sorted(out, key=lambda g: (g.axis, g.at))
