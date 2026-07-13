"""Recognition of non-cylindrical machined features (#135).

It recognises milled *slots* — the class of feature the cylinder-based pipeline
(``analyse_cylinders``/``recognise_holes`` in :mod:`._features`) is blind to.  It
sits beside the hole/boss recognisers in :mod:`draftwright.recognition`, the
single home for feature recognition (ADR 0007 — recognition lives in
draftwright, not upstream); it mirrors their OCC face-scan idioms.

Scope: **enclosed rectangular recesses with straight walls** — through-slots
(:func:`recognise_slots`) and their blind counterparts, floored slots/pockets
(:func:`recognise_pockets`, #148a).  The recogniser proves a candidate is such a
recess rather than some other facing-wall feature with three predicates a naive
"opposed facing walls" test gets wrong:

1. **Facing walls** — two axis-aligned planar faces with anti-parallel outward
   normals each pointing *towards* the other.  The part's own outer faces face
   *away*, so this excludes them without any "is this an outer face" heuristic.
2. **Rectangular walls** — both walls are bounded by straight (LINE) edges only.
   A turned groove / circlip recess has *annular* walls (CIRCLE edges); this
   rejects them (otherwise a stepped shaft's groove reads as a slot).
3. **Through vs blind** — whether a planar floor caps the cut.  A blind pocket
   (or the floored gap between two bosses) has a floor face spanning the
   footprint; a through-slot does not.  ``_has_floor`` is the sole split between
   the two recognisers — through candidates go to :func:`recognise_slots`, blind
   ones (carrying their depth) to :func:`recognise_pockets`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeVar

from build123d import Box, GeomType, Pos
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Plane
from OCP.TopAbs import TopAbs_Orientation

from draftwright.recognition._record import Record

_AXES = {"x": 0, "y": 1, "z": 2}
# Shared by the through-slot (:func:`recognise_slots`) and blind-recess
# (:func:`recognise_pockets`) merges so each keeps its own record type.
_R = TypeVar("_R", "Slot", "Pocket")
# A normal counts as axis-aligned when its dominant component is within this of
# unit length — machined slot walls are square to the stock, so this is tight.
_AXIS_ALIGNED_TOL = 1e-3
# Two slot candidates are the same physical feature when their region centres
# coincide to within this (mm); the narrower wins (the true across-flats width).
_MERGE_TOL = 0.5
# A planar face counts as a slot *floor* (capping the slot, so it is blind not
# through) when its centre on the depth axis is within this of a slot end and it
# covers at least _FLOOR_COVER_FRAC of the slot footprint on each in-plane axis.
_FLOOR_TOL = 0.3
_FLOOR_COVER_FRAC = 0.5
# The gap between two collinear arms counts as void (a crossing channel runs
# through it) when the intersection of the inset gap box with the solid is at most
# this fraction of the box — a channel carves it to ~0, a hole/solid leaves more.
_VOID_INSET = 0.1
_VOID_VOL_FRAC = 0.01


@dataclass(frozen=True)
class Slot(Record):
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


@dataclass(frozen=True)
class Pocket(Record):
    """A blind rectangular recess — a milled slot/pocket capped by a floor (#148a).

    The through/blind split (:func:`_has_floor`) is the only difference from a
    :class:`Slot`: a pocket is floored, so it carries a third defining size — the
    ``depth`` from the open face down to the floor (``d_hi - d_lo``, the walls' extent
    on the depth axis). ``width``/``length`` are the in-plane footprint, exactly as for
    a slot; a "blind slot" (elongated) and a "pocket" (near-square) are the same feature
    — both a floored rectangular recess dimensioned width × length × depth.

    width_axis: axis across the recess (the walls' normal axis), 'x'/'y'/'z'.
    long_axis:  axis the recess runs along (largest shared wall extent).
    width:      wall-to-wall separation — the shorter in-plane size.
    length:     recess extent along ``long_axis`` — the longer in-plane size.
    depth:      open-face-to-floor depth (along ``depth_axis``).
    w_center:   ``width_axis`` coordinate of the recess centreline.
    lo, hi:     ``long_axis`` coordinates of the recess ends (lo < hi).
    d_lo, d_hi: extent on the depth axis (``depth == d_hi - d_lo``).
    """

    width_axis: str
    long_axis: str
    width: float
    length: float
    depth: float
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


@dataclass(frozen=True)
class _Face:
    """An axis-aligned planar face reduced to the data the recogniser needs."""

    normal: tuple
    axis: str
    bb: object
    rect: bool  # bounded by straight (LINE) edges only


def _planar_faces(part):
    """All axis-aligned planar faces as :class:`_Face` records (computed once)."""
    faces = []
    for face in part.faces():
        nrm = _outward_normal(face)
        if nrm is None:
            continue
        axis = _dominant_axis(nrm)
        if axis is None:
            continue
        rect = all(e.geom_type == GeomType.LINE for e in face.edges())
        faces.append(_Face(nrm, axis, face.bounding_box(), rect))
    return faces


def _center(bb, k):
    return (getattr(bb.min, "XYZ"[k]) + getattr(bb.max, "XYZ"[k])) / 2


def _overlap_len(bb_a, bb_b, axis):
    """Length of the overlap of two bboxes along ``axis`` (0 if disjoint)."""
    c = "XYZ"[_AXES[axis]]
    lo = max(getattr(bb_a.min, c), getattr(bb_b.min, c))
    hi = min(getattr(bb_a.max, c), getattr(bb_b.max, c))
    return hi - lo


def _candidate(fa, fb, part_ext):
    """Build a :class:`Slot` from two facing rectangular walls, or None if the
    pair is not a slot (not facing, not overlapping, wider than long, or
    spanning the full part).  Geometry only — the through/blind test is applied
    by the caller, which needs the whole face set."""
    axis = fa.axis
    k = _AXES[axis]
    bb_a, bb_b = fa.bb, fb.bb
    # Anti-parallel outward normals.
    if fa.normal[k] * fb.normal[k] >= 0:
        return None
    c_a, c_b = _center(bb_a, k), _center(bb_b, k)
    # Facing each other: A's outward normal points towards B.  Outer faces of
    # the stock fail this (their normals point apart).
    if (c_b - c_a) * fa.normal[k] <= 0:
        return None
    # The walls must genuinely overlap in both perpendicular axes, otherwise
    # they are unrelated faces that merely happen to be parallel and facing.
    others = [a for a in "xyz" if a != axis]
    ov = [_overlap_len(bb_a, bb_b, a) for a in others]
    if min(ov) <= 0:
        return None
    width = abs(c_b - c_a)
    # The longer shared extent is the slot length; the shorter is depth.  When
    # the two are near-equal (a near-square slot) the choice is ambiguous, so
    # break the tie towards the part's longer axis — a slot on a bar runs along
    # the bar.
    (ax0, ov0), (ax1, ov1) = sorted(zip(others, ov), key=lambda t: t[1], reverse=True)
    if (ov0 - ov1) <= _LENGTH_TIE_FRAC * ov0 and part_ext[ax1] > part_ext[ax0]:
        (long_axis, length), depth_axis = (ax1, ov1), ax0
    else:
        (long_axis, length), depth_axis = (ax0, ov0), ax1
    # A slot is elongated: its width (the wall separation) is not its largest
    # dimension.  A wider-than-long pair is a step/pocket or a sliver of two
    # incidental parallel faces.
    if width > length:
        return None
    # Reject open / full-span features along the length (see _SLOT_MAX_SPAN_FRAC).
    if length >= _SLOT_MAX_SPAN_FRAC * part_ext[long_axis]:
        return None
    lc = "XYZ"[_AXES[long_axis]]
    lo = max(getattr(bb_a.min, lc), getattr(bb_b.min, lc))
    hi = min(getattr(bb_a.max, lc), getattr(bb_b.max, lc))
    dc = "XYZ"[_AXES[depth_axis]]
    d_lo = max(getattr(bb_a.min, dc), getattr(bb_b.min, dc))
    d_hi = min(getattr(bb_a.max, dc), getattr(bb_b.max, dc))
    return Slot(
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


def _end_capped(faces, foot, foot_area, depth_axis, end, want) -> bool:
    """True when inward-facing planar faces at ``end`` on ``depth_axis`` together cover at
    least :data:`_FLOOR_COVER_FRAC` of the ``foot`` (width×length) footprint — one end's
    half of the floor test.

    ``want`` is the sign the covering normal must point (+depth at the low end, -depth at the
    high end) so the cap faces *into* the cavity; the part's own outer face at that level
    faces the other way and is excluded (that is what separates a real floor from a
    through-open end). Coverage is *aggregated* over all qualifying faces (not tested per
    face), so a floor split by a rib/divider still counts (#146). The sum (not union) is
    exact for the coplanar floor faces of a valid solid; overlaps only arise from
    interpenetrating solids (degenerate input)."""
    dk = _AXES[depth_axis]
    covered = 0.0
    for f in faces:
        if f.axis != depth_axis or abs(_center(f.bb, dk) - end) > _FLOOR_TOL:
            continue
        if f.normal[dk] * want <= 0:
            continue
        area = 1.0
        for ax, (lo, hi) in foot.items():
            c = "XYZ"[_AXES[ax]]
            ov = min(getattr(f.bb.max, c), hi) - max(getattr(f.bb.min, c), lo)
            area *= max(ov, 0.0)
        covered += area
    return bool(covered >= _FLOOR_COVER_FRAC * foot_area)


def _has_floor(faces, s: Slot) -> bool:
    """True when a planar floor caps the slot at *either* depth end — i.e. it is blind,
    not through. The through/blind split for :func:`recognise_slots`; :func:`recognise_pockets`
    uses the finer *which end* count (:func:`_end_capped`) to recover the depth axis."""
    foot = {
        s.width_axis: (s.w_center - s.width / 2, s.w_center + s.width / 2),
        s.long_axis: (s.lo, s.hi),
    }
    foot_area = math.prod(hi - lo for lo, hi in foot.values())
    return _end_capped(faces, foot, foot_area, s.depth_axis, s.d_lo, 1.0) or _end_capped(
        faces, foot, foot_area, s.depth_axis, s.d_hi, -1.0
    )


def recognise_slots(part) -> list[Slot]:
    """Recognise enclosed through-slots with rectangular walls in *part*.

    Returns a list of :class:`Slot`, one per physical feature, in a
    deterministic order (co-located candidate pairs are merged, keeping the
    narrower width).  See the module docstring for the recognition predicate and
    its (deliberately narrow) scope.
    """
    faces = _planar_faces(part)
    pbb = part.bounding_box()
    part_ext = {a: getattr(pbb.size, "XYZ"[_AXES[a]]) for a in "xyz"}
    # Only straight-walled faces can be slot walls; bucket them by axis so the
    # O(n^2) pairing runs within each axis instead of across all planar faces.
    by_axis: dict[str, list[_Face]] = {}
    for f in faces:
        if f.rect:
            by_axis.setdefault(f.axis, []).append(f)
    candidates: list[Slot] = []
    for walls in by_axis.values():
        for i in range(len(walls)):
            for j in range(i + 1, len(walls)):
                s = _candidate(walls[i], walls[j], part_ext)
                # Keep only through-slots: a blind pocket (or the floored gap
                # between bosses) is capped by a floor and is out of scope (#148).
                if s is not None and not _has_floor(faces, s):
                    candidates.append(s)
    # Recombine arms of a crossing channel split by the intersection (#604).
    return _collapse_collinear(_merge(candidates), part)


def _same_channel_line(a: Slot, b: Slot):
    """When ``a`` and ``b`` are collinear co-axial slot *arms* — same wall plane
    (width axis, centreline, width and depth extent) but disjoint along their run
    — return the gap ``(g_lo, g_hi)`` between them along ``long_axis``; else None.

    Two arms of one channel that a crossing cut has split (#604) share every
    dimension but their run; two genuinely parallel slots have different
    centrelines (``w_center``) and never reach here."""
    if a.width_axis != b.width_axis or a.long_axis != b.long_axis:
        return None
    if abs(a.w_center - b.w_center) > _MERGE_TOL or abs(a.width - b.width) > _MERGE_TOL:
        return None
    if abs(a.d_lo - b.d_lo) > _MERGE_TOL or abs(a.d_hi - b.d_hi) > _MERGE_TOL:
        return None
    if a.hi <= b.lo:
        gap = (a.hi, b.lo)
    elif b.hi <= a.lo:
        gap = (b.hi, a.lo)
    else:
        return None  # overlapping along the run — not two disjoint arms
    return gap if gap[1] - gap[0] > 0 else None


def _gap_is_void(gap, arm: Slot, part) -> bool:
    """True when the *whole* gap between two collinear arms is empty space — a
    crossing channel of matching cross-section runs through it — rather than solid
    stock or merely pierced by an incidental void.

    The gap region is the box of its full run (along ``long_axis``) × the arm's
    width × the arm's depth, inset slightly off the arm walls to avoid
    coincident-face noise.  A crossing channel carves this box away entirely, so
    its intersection with the solid is (near) zero volume.  A solid bridge fills
    it; a small unrelated hole between two aligned slots leaves the box corners
    solid — both keep a substantial intersection, so the arms stay separate.
    Testing the whole box (not a single sample point) is what distinguishes a
    channel from an incidental hole at the gap centre (#610 re-reviews).

    Known limitation: a wide *enclosed* void (a square window/pocket) flush with
    the arm ends also empties the box and so fuses the arms.  This is a continuum
    with the accepted symmetric-cross case — which likewise leaves the merged
    slot wall-less where the crossing channel passes — and distinguishing a
    narrow crossing channel from a wide window is an aspect-ratio judgement with
    no clean line; #604's scope is intersecting *channels*, so it is left as-is."""
    span = {
        arm.long_axis: (gap[0], gap[1]),
        arm.width_axis: (arm.w_center - arm.width / 2, arm.w_center + arm.width / 2),
        arm.depth_axis: (arm.d_lo, arm.d_hi),
    }
    size, centre = {}, {}
    for ax, (lo, hi) in span.items():
        inset = min(_VOID_INSET, (hi - lo) / 4)
        size[ax] = (hi - lo) - 2 * inset
        centre[ax] = (lo + hi) / 2
    if min(size.values()) <= 0:
        return False
    probe = Pos(centre["x"], centre["y"], centre["z"]) * Box(size["x"], size["y"], size["z"])
    inter = part.intersect(probe)
    # ``intersect`` returns None (empty), a single shape with ``.volume`` (older
    # build123d), or a ShapeList of shapes (newer build123d) — sum either way.
    if inter is None:
        inter_vol = 0.0
    elif hasattr(inter, "volume"):
        inter_vol = inter.volume
    else:
        inter_vol = sum(s.volume for s in inter)
    box_vol = size["x"] * size["y"] * size["z"]
    return bool(inter_vol <= _VOID_VOL_FRAC * box_vol)


def _collapse_collinear(slots: list[Slot], part) -> list[Slot]:
    """Recombine slot arms split by a crossing channel into whole channels (#604).

    A ``+`` of two intersecting through-channels is milled as one continuous slot
    each, but the central intersection removes the middle of both channels' walls,
    so the wall scan yields two collinear arm-slots per channel (four total).
    Union collinear co-axial arms whose gap is void (a crossing channel passes
    between them), and span each group into a single slot running its full length.
    Arms separated by solid material — two genuinely distinct slots — are left as
    separate features."""
    parent = list(range(len(slots)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            gap = _same_channel_line(slots[i], slots[j])
            if gap is not None and _gap_is_void(gap, slots[i], part):
                parent[find(i)] = find(j)

    groups: dict[int, list[Slot]] = {}
    for idx, s in enumerate(slots):
        groups.setdefault(find(idx), []).append(s)

    out: list[Slot] = []
    for members in groups.values():
        if len(members) == 1:
            out.append(members[0])
            continue
        base = members[0]
        lo = min(m.lo for m in members)
        hi = max(m.hi for m in members)
        out.append(
            Slot(
                width_axis=base.width_axis,
                long_axis=base.long_axis,
                width=base.width,
                length=round(hi - lo, 2),
                w_center=base.w_center,
                lo=round(lo, 2),
                hi=round(hi, 2),
                d_lo=base.d_lo,
                d_hi=base.d_hi,
            )
        )
    return sorted(out, key=lambda c: (c.width, _region_center(c)))


def _region_center(s: Slot | Pocket):
    """The slot's mid-point in part coordinates (axis-ordered)."""
    c = {
        s.width_axis: s.w_center,
        s.long_axis: (s.lo + s.hi) / 2,
        s.depth_axis: (s.d_lo + s.d_hi) / 2,
    }
    return (c["x"], c["y"], c["z"])


def _merge(candidates: list[_R]) -> list[_R]:
    """A rectangular slot is bounded by two orthogonal opposed-wall pairs (the
    width walls and the length end-caps), so the same feature is detected twice
    — once per pair.  Collapse candidates that occupy the same region, keeping
    the one with the smallest width (the true across-flats).

    Sorted by ``(width, region_centre)`` so the output order — and therefore the
    ``slot{i}`` annotation names downstream — is determined by geometry alone,
    not by OCC face-iteration order (which is not stable across kernels)."""
    kept: list[_R] = []
    for s in sorted(candidates, key=lambda c: (c.width, _region_center(c))):
        cs = _region_center(s)
        if any(math.dist(cs, _region_center(k)) <= _MERGE_TOL for k in kept):
            continue
        kept.append(s)
    return kept


def _pocket_candidate(fa, fb, faces, part_ext) -> Pocket | None:
    """Build a :class:`Pocket` from two facing width-walls, or None if the pair is not a
    blind recess.  Unlike :func:`_candidate` (which splits the two non-width axes into
    long/depth by *size*), the depth axis is read from the geometry: a pocket's depth axis
    is the one capped on **exactly one** end (the floor) and open on the other (the
    opening), while the footprint (width, length) axes are enclosed on both ends. That
    distinction is why a pocket deeper than it is long must not reuse the size heuristic —
    the deep axis would be mislabelled 'length' and an end-wall taken for the floor (#609
    review)."""
    axis = fa.axis  # the width axis: the facing walls' shared normal axis
    k = _AXES[axis]
    if fa.normal[k] * fb.normal[k] >= 0:
        return None  # not anti-parallel — not a facing pair
    c_a, c_b = _center(fa.bb, k), _center(fb.bb, k)
    if (c_b - c_a) * fa.normal[k] <= 0:
        return None  # normals face away from each other (outer faces), not a cavity
    width = abs(c_b - c_a)
    others = [a for a in "xyz" if a != axis]
    ranges = {}  # per non-width axis: (lo, hi) overlap of the two walls
    for a in others:
        c = "XYZ"[_AXES[a]]
        lo = max(getattr(fa.bb.min, c), getattr(fb.bb.min, c))
        hi = min(getattr(fa.bb.max, c), getattr(fb.bb.max, c))
        if hi - lo <= 0:
            return None  # walls do not overlap on this axis — not a slot
        ranges[a] = (lo, hi)
    w_range = (c_a + c_b) / 2 - width / 2, (c_a + c_b) / 2 + width / 2
    # The depth axis is the non-width axis capped on exactly one end (floor + opening).
    for depth_axis in others:
        (long_axis,) = [a for a in others if a != depth_axis]
        d_lo, d_hi = ranges[depth_axis]
        l_lo, l_hi = ranges[long_axis]
        foot = {axis: w_range, long_axis: (l_lo, l_hi)}
        foot_area = width * (l_hi - l_lo)
        capped = _end_capped(faces, foot, foot_area, depth_axis, d_lo, 1.0) + _end_capped(
            faces, foot, foot_area, depth_axis, d_hi, -1.0
        )
        if capped != 1:
            continue  # 0 = through on this axis; 2 = an enclosed end-cap pair, not a floor
        length = l_hi - l_lo
        if width > length:
            return None  # width is the smaller footprint dim (the wrong wall pair)
        if length >= _SLOT_MAX_SPAN_FRAC * part_ext[long_axis]:
            return None  # footprint spans the part — an open feature, not an enclosed pocket
        return Pocket(
            width_axis=axis,
            long_axis=long_axis,
            width=round(width, 2),
            length=round(length, 2),
            depth=round(d_hi - d_lo, 2),
            w_center=round((c_a + c_b) / 2, 2),
            lo=round(l_lo, 2),
            hi=round(l_hi, 2),
            d_lo=round(d_lo, 2),
            d_hi=round(d_hi, 2),
        )
    return None


def recognise_pockets(part) -> list[Pocket]:
    """Blind rectangular recesses — floored slots/pockets (#148a).

    The blind counterpart of :func:`recognise_slots`: the same facing-rectangular-wall
    candidate scan, but keeping the pairs a floor caps.  The depth (open-face-to-floor
    extent) is read from the axis the floor is normal to — see :func:`_pocket_candidate` —
    not from a size heuristic, so a pocket deeper than it is long is dimensioned correctly.
    A "blind slot" (elongated) and a "pocket" (near-square) are the same floored feature,
    dimensioned width × length × depth."""
    faces = _planar_faces(part)
    pbb = part.bounding_box()
    part_ext = {a: getattr(pbb.size, "XYZ"[_AXES[a]]) for a in "xyz"}
    by_axis: dict[str, list[_Face]] = {}
    for f in faces:
        if f.rect:
            by_axis.setdefault(f.axis, []).append(f)
    candidates: list[Pocket] = []
    for walls in by_axis.values():
        for i in range(len(walls)):
            for j in range(i + 1, len(walls)):
                p = _pocket_candidate(walls[i], walls[j], faces, part_ext)
                if p is not None:
                    candidates.append(p)
    return _merge(candidates)
