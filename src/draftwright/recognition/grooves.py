"""grooves — turned / circlip-groove recognition on round stock (ADR 0007, #148c).

A *groove* is an annular channel turned into round stock — a circlip / retaining-ring
groove, an O-ring groove, a run-out relief. It reads as a band of the OD whose diameter
is a **strict local minimum**: smaller than the contiguous bands on *both* axial sides,
bounded by the two annular walls where the OD steps down then up again. It is dimensioned
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


def recognise_grooves(part) -> list[Groove]:
    """Recognise the turned grooves of *part* (see module docstring). Returns one
    :class:`Groove` per external band whose OD is a strict local minimum between two
    contiguous larger bands on the same shaft, sorted deterministically. Empty when the
    part has no round stock or no groove."""
    z_cyls, cross_cyls = analyse_cylinders(part)
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
            bb = cur["face"].bounding_box()
            at = (
                round(0.5 * (bb.min.X + bb.max.X), 3),
                round(0.5 * (bb.min.Y + bb.max.Y), 3),
                round(0.5 * (bb.min.Z + bb.max.Z), 3),
            )
            out.append(
                Groove(
                    axis=cur["axis"],
                    width=round(cur["s_hi"] - cur["s_lo"], 3),
                    diameter=round(cur["diameter"], 3),
                    at=at,
                )
            )
    return sorted(out, key=lambda g: (g.axis, g.at))
