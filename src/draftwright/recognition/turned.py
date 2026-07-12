"""turned — step/shoulder recognition for turned (rotational) parts.

``recognise_turned_steps`` extracts the axial steps of a stepped shaft — the
contiguous segments between shoulders — so the engine can dimension each step
*length* (the drive-screw gap: every diameter dimensioned, but no shoulder
locatable). It is draftwright's recognition (ADR 0007), built on the owned
``analyse_cylinders`` primitive.

Why not ``recognise_bosses``: a boss's ``.height`` is its *cylindrical-face* length,
shortened by the chamfers at each shoulder, so boss spans neither tile the axis
nor sum to the overall length — wrong for axial dims. ``analyse_cylinders``
instead gives each cylinder's true axial span (``s_lo``/``s_hi``) and an
``external`` flag.

Algorithm:

1. Take the **external** cylinders on the dominant turning axis (≥2 distinct
   diameters, else the part is not a stepped turned part → ``None``). Internal
   bores are excluded by the ``external`` flag, so a bored shaft is handled and
   a blind bore's flat bottom never reads as a shoulder.
2. The shoulders/end faces are the part's transverse planar faces (normal along
   the axis). Keep a face only when its outer radius **reaches the local OD
   silhouette** (the max external-band radius spanning that axial position,
   within a chamfer allowance). This separates true shoulders/ends — which reach
   the OD — from internal feature faces (a bore bottom sits well inside the OD).
   The allowance tolerates the chamfers that shrink a real shoulder face below
   the nominal OD.
3. Sorted shoulder positions delimit the steps; each step carries the local OD.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from draftwright.recognition._features import analyse_cylinders

# A face's axial position counts as on a band edge / its normal counts as
# axis-aligned within these tolerances (mm / unit-vector component).
_AXIS_NORMAL_TOL = 0.05
# Pad a band's [s_lo, s_hi] when asking "what is the OD here", so a shoulder face
# sitting exactly at a (chamfer-shortened) band edge still sees that band's OD.
_OD_SPAN_PAD = 0.7
# A transverse face is a shoulder/end when its outer radius is within this of the
# local OD. Constant + proportional terms cover both a fixed edge-break and a
# chamfer that scales with the feature — enough to keep a chamfered shoulder, far
# less than the gap between an internal bore radius and the OD.
_CHAMFER_ALLOWANCE_ABS = 0.5
_CHAMFER_ALLOWANCE_FRAC = 0.12
# A genuine turned body is round about its axis: the perpendicular cross-section is
# roughly square and the OD silhouette fills it (#293). Looser than the rotational
# classifier's gate (chamfers/features perturb the bbox), but firmly rejects an
# incidental cylinder on a prismatic part (a tiny OD in a large oblong bbox).
_SQUARENESS_TOL = 0.15
_OD_FILL_MIN = 0.6


@dataclass(frozen=True)
class TurnedStep:
    """One axial segment of a stepped shaft, between two shoulders (or ends)."""

    lo: float
    hi: float
    diameter: float  # the external OD over this segment

    @property
    def length(self) -> float:
        return self.hi - self.lo


@dataclass(frozen=True)
class TurnedProfile:
    """The axial step breakdown of a turned part along its turning ``axis``."""

    axis: str  # "x" / "y" / "z"
    steps: tuple[TurnedStep, ...]

    @property
    def shoulders(self) -> tuple[float, ...]:
        """Sorted axial positions of the shoulders and the two end faces."""
        if not self.steps:
            return ()
        return (*(s.lo for s in self.steps), self.steps[-1].hi)


def recognise_turned_steps(part) -> TurnedProfile | None:
    """Recognise the axial steps of a stepped turned ``part``, or ``None``.

    Returns ``None`` for a non-turned part, a plain (single-diameter) cylinder,
    or anything with fewer than two steps — nothing to dimension axially.
    """
    z_cyls, cross_cyls = analyse_cylinders(part)
    ext = [c for c in (*z_cyls, *cross_cyls) if c.get("external")]
    if not ext:
        return None
    axis, _ = Counter(c["axis"] for c in ext).most_common(1)[0]
    bands = [c for c in ext if c["axis"] == axis]
    if len({round(c["diameter"], 2) for c in bands}) < 2:
        return None  # one OD → not a stepped turned part
    idx = "xyz".index(axis)

    # A genuine turned shaft is a body of revolution about *axis*: its OD silhouette
    # (largest external band) fills a roughly-square cross-section perpendicular to the
    # axis. Reject incidental small cylinders on a prismatic part — e.g. a case shell's
    # side screw-holes — whose unrelated feature faces would otherwise be read as a
    # spurious multi-step profile (#293).
    pbb = part.bounding_box()
    perp = [s for i, s in enumerate((pbb.size.X, pbb.size.Y, pbb.size.Z)) if i != idx]
    cross = max(perp)
    max_od = max(c["diameter"] for c in bands)
    if (
        cross <= 0
        or max_od < _OD_FILL_MIN * cross
        or abs(perp[0] - perp[1]) > _SQUARENESS_TOL * cross
    ):
        return None

    def local_od(pos: float) -> float:
        radii = [
            c["diameter"] / 2
            for c in bands
            if c["s_lo"] - _OD_SPAN_PAD <= pos <= c["s_hi"] + _OD_SPAN_PAD
        ]
        return max(radii) if radii else 0.0

    shoulders: set[float] = set()
    for face in part.faces():
        try:
            nrm = face.normal_at(face.center())
        except Exception:  # noqa: BLE001 — a face whose normal won't evaluate isn't a shoulder
            continue
        nv = (nrm.X, nrm.Y, nrm.Z)
        if abs(abs(nv[idx]) - 1) > _AXIS_NORMAL_TOL or any(
            abs(nv[j]) > _AXIS_NORMAL_TOL for j in range(3) if j != idx
        ):
            continue  # not transverse to the axis
        bb = face.bounding_box()
        pos = (face.center().X, face.center().Y, face.center().Z)[idx]
        spans = ((bb.min.X, bb.max.X), (bb.min.Y, bb.max.Y), (bb.min.Z, bb.max.Z))
        outer = max(max(abs(spans[j][0]), abs(spans[j][1])) for j in range(3) if j != idx)
        od = local_od(pos)
        if outer >= od - (_CHAMFER_ALLOWANCE_ABS + _CHAMFER_ALLOWANCE_FRAC * od):
            shoulders.add(round(pos, 3))

    planes = sorted(shoulders)
    if len(planes) < 3:  # fewer than two steps
        return None
    # A segment whose midpoint has no external band over it (`local_od` → 0) is a
    # gap between disconnected bands, not a real step — drop it so it never renders
    # as a phantom ø0 diameter (#279).
    steps = tuple(
        s
        for i in range(len(planes) - 1)
        if (
            s := TurnedStep(
                lo=planes[i],
                hi=planes[i + 1],
                diameter=2 * local_od((planes[i] + planes[i + 1]) / 2),
            )
        ).diameter
        > 0
    )
    if len(steps) < 2:  # fewer than two real steps → nothing to dimension axially
        return None
    return TurnedProfile(axis=axis, steps=steps)
