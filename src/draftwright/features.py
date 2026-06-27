"""Recognition of non-cylindrical machined features (#135).

Draftwright-local **prototype**.  It recognises milled *slots* / reduced
across-flats sections — the class of feature the cylinder-based pipeline
(``analyse_cylinders``/``find_holes`` in ``build123d_drafting.features``) is
blind to.  Once the recognition predicate stabilises this is intended to be
upstreamed into ``build123d-drafting-helpers`` beside the hole/boss recognisers;
it deliberately mirrors their OCC face-scan idioms so the lift is mechanical.

The defining signature of a cut-in slot is a pair of **opposed parallel walls
that face each other**: two axis-aligned planar faces with anti-parallel
outward normals where each normal points *towards* the other face.  The part's
own outer faces are also parallel and anti-parallel, but their outward normals
point *away* from each other, so the facing test excludes them cleanly without
any bounding-box / "is this an outer face" heuristic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Plane
from OCP.TopAbs import TopAbs_Orientation

_AXES = {"x": 0, "y": 1, "z": 2}
# A normal counts as axis-aligned when its dominant component is within this of
# unit length — machined slot walls are square to the stock, so this is tight.
_AXIS_ALIGNED_TOL = 1e-3
# Two slot candidates are the same physical feature when their region centres
# coincide to within this (mm); the narrower wins (the true across-flats width).
_MERGE_TOL = 0.5


@dataclass(frozen=True)
class Slot:
    """A milled slot / reduced across-flats section.

    A slot is bounded by two opposed parallel walls facing each other.  The
    wall separation is the *width* (the defining size); the extent the walls
    share along the part's longer in-plane axis is the *length*.

    width_axis: axis across the slot (the walls' normal axis), 'x'/'y'/'z'.
    long_axis:  axis the slot runs along (largest shared wall extent).
    width:      wall-to-wall separation — the defining size dim.
    length:     slot extent along ``long_axis``.
    w_center:   ``width_axis`` coordinate of the slot centreline.
    lo, hi:     ``long_axis`` coordinates of the slot ends (lo < hi).
    d_lo, d_hi: extent on the third (depth) axis.
    """

    width_axis: str
    long_axis: str
    width: float
    length: float
    w_center: float
    lo: float
    hi: float
    d_lo: float
    d_hi: float

    @property
    def depth_axis(self) -> str:
        return next(a for a in "xyz" if a not in (self.width_axis, self.long_axis))


# When the two non-width slot extents are within this fraction of each other the
# slot is near-square in that plane and "which is the length" is ambiguous; the
# tie is then broken towards the part's longer bounding-box axis.
_LENGTH_TIE_FRAC = 0.05
# A slot is *enclosed* — bounded by material at its ends.  A facing-wall pair
# spanning (almost) the whole part along its length is an OPEN feature instead:
# the concave corner of an L, a U-channel face, or a through step, where the two
# walls run flush to the part boundary rather than being capped. Those are not
# slots, so a pair this long is rejected (this is the open-vs-enclosed cut the
# recogniser is deliberately conservative about — a partial-span open corner
# would still slip through, and belongs to a future step/pocket recogniser).
_SLOT_MAX_SPAN_FRAC = 0.9


def _outward_normal(face):
    """Unit outward normal of a planar face as an (x, y, z) tuple, or None when
    the face is not planar.  Material-side convention matches helpers'
    ``analyse_cylinders``: FORWARD orientation agreeing with the plane frame's
    handedness means the stored normal already points out of the solid."""
    surf = BRepAdaptor_Surface(face.wrapped)
    if surf.GetType() != GeomAbs_Plane:
        return None
    pl = surf.Plane()
    n = pl.Axis().Direction()
    fwd = face.wrapped.Orientation() == TopAbs_Orientation.TopAbs_FORWARD
    sign = 1.0 if (fwd == pl.Position().Direct()) else -1.0
    return (sign * n.X(), sign * n.Y(), sign * n.Z())


def _dominant_axis(nrm):
    """Return the axis letter when ``nrm`` is axis-aligned, else None."""
    for axis, k in _AXES.items():
        if abs(abs(nrm[k]) - 1.0) <= _AXIS_ALIGNED_TOL:
            return axis
    return None


def _planar_walls(part):
    """Axis-aligned planar faces as (normal, axis, bbox) tuples."""
    walls = []
    for face in part.faces():
        nrm = _outward_normal(face)
        if nrm is None:
            continue
        axis = _dominant_axis(nrm)
        if axis is None:
            continue
        walls.append((nrm, axis, face.bounding_box()))
    return walls


def _center(bb, k):
    return (getattr(bb.min, "XYZ"[k]) + getattr(bb.max, "XYZ"[k])) / 2


def _overlap_len(bb_a, bb_b, axis):
    """Length of the overlap of two bboxes along ``axis`` (0 if disjoint)."""
    c = "XYZ"[_AXES[axis]]
    lo = max(getattr(bb_a.min, c), getattr(bb_b.min, c))
    hi = min(getattr(bb_a.max, c), getattr(bb_b.max, c))
    return hi - lo


def find_slots(part) -> list[Slot]:
    """Recognise milled slots / reduced across-flats sections in *part*.

    Returns a list of :class:`Slot`, one per physical feature (co-located
    candidate pairs are merged, keeping the narrower width).
    """
    walls = _planar_walls(part)
    pbb = part.bounding_box()
    part_ext = {a: getattr(pbb.size, "XYZ"[_AXES[a]]) for a in "xyz"}
    candidates: list[Slot] = []
    for i in range(len(walls)):
        n_a, axis, bb_a = walls[i]
        k = _AXES[axis]
        for j in range(i + 1, len(walls)):
            n_b, axis_b, bb_b = walls[j]
            if axis_b != axis:
                continue
            # Anti-parallel outward normals.
            if n_a[k] * n_b[k] >= 0:
                continue
            c_a, c_b = _center(bb_a, k), _center(bb_b, k)
            # Facing each other: A's outward normal points towards B.  Outer
            # faces of the stock fail this (their normals point apart).
            if (c_b - c_a) * n_a[k] <= 0:
                continue
            # The walls must genuinely overlap in both perpendicular axes,
            # otherwise they are unrelated faces that merely happen to be
            # parallel and facing.
            others = [a for a in "xyz" if a != axis]
            ov = [_overlap_len(bb_a, bb_b, a) for a in others]
            if min(ov) <= 0:
                continue
            width = abs(c_b - c_a)
            # The longer shared extent is the slot length; the shorter is depth.
            # When the two are near-equal (a near-square slot, e.g. a channel cut
            # straight through a shaft) the choice is ambiguous, so break the tie
            # towards the part's longer axis — the manufacturing "length" of a
            # slot on a bar runs along the bar.
            (ax0, ov0), (ax1, ov1) = sorted(zip(others, ov), key=lambda t: t[1], reverse=True)
            if (ov0 - ov1) <= _LENGTH_TIE_FRAC * ov0 and part_ext[ax1] > part_ext[ax0]:
                (long_axis, length), (depth_axis, _) = (ax1, ov1), (ax0, ov0)
            else:
                (long_axis, length), (depth_axis, _) = (ax0, ov0), (ax1, ov1)
            # A slot is elongated: its width (the wall separation) is not its
            # largest dimension.  Pairs wider than they are long are not slots
            # but wide steps / pockets, or — when the overlap is a sliver —
            # incidental parallel faces (e.g. two part faces that merely happen
            # to face each other). Both are filtered here, before merging.
            if width > length:
                continue
            # Reject open / full-span features (an enclosed slot is shorter than
            # the part along its length — see _SLOT_MAX_SPAN_FRAC).
            if length >= _SLOT_MAX_SPAN_FRAC * part_ext[long_axis]:
                continue
            lc = "XYZ"[_AXES[long_axis]]
            lo = max(getattr(bb_a.min, lc), getattr(bb_b.min, lc))
            hi = min(getattr(bb_a.max, lc), getattr(bb_b.max, lc))
            dc = "XYZ"[_AXES[depth_axis]]
            d_lo = max(getattr(bb_a.min, dc), getattr(bb_b.min, dc))
            d_hi = min(getattr(bb_a.max, dc), getattr(bb_b.max, dc))
            candidates.append(
                Slot(
                    width_axis=axis,
                    long_axis=long_axis,
                    width=round(width, 2),
                    length=round(hi - lo, 2),
                    w_center=round((c_a + c_b) / 2, 2),
                    lo=round(lo, 2),
                    hi=round(hi, 2),
                    d_lo=round(d_lo, 2),
                    d_hi=round(d_hi, 2),
                )
            )
    return _merge(candidates)


def _region_center(s: Slot):
    """The slot's mid-point in part coordinates (axis-ordered)."""
    c = {
        s.width_axis: s.w_center,
        s.long_axis: (s.lo + s.hi) / 2,
        s.depth_axis: (s.d_lo + s.d_hi) / 2,
    }
    return (c["x"], c["y"], c["z"])


def _merge(candidates: list[Slot]) -> list[Slot]:
    """A rectangular slot is bounded by two orthogonal opposed-wall pairs (the
    width walls and the length end-caps), so the same feature is detected twice
    — once per pair.  Collapse candidates that occupy the same region, keeping
    the one with the smallest width (the true across-flats)."""
    kept: list[Slot] = []
    for s in sorted(candidates, key=lambda c: c.width):
        cs = _region_center(s)
        if any(math.dist(cs, _region_center(k)) <= _MERGE_TOL for k in kept):
            continue
        kept.append(s)
    return kept
