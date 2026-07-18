"""Model-neutral geometry primitives — the leaf below both ``_core`` and ``model``.

These helpers read an axis / coordinate / position off a build123d object (or
the IR), or do pure page-plane maths (AABB overlap, segment/box intersection,
number formatting — the #700 shared home), and carry no drawing, layout or page
knowledge. They live here, not in :mod:`draftwright._core`, so the IR waist
(:mod:`draftwright.model`) can use them without importing the stage-level
drawing grab-bag (ADR 0008; #584 WP2). This module imports nothing from
``draftwright`` — it is the bottom of the DAG.
"""

from __future__ import annotations

from dataclasses import dataclass

# Axis letter -> the orthographic view a feature on that axis reads end-on in.
_END_ON = {"x": "side", "y": "front", "z": "plan"}


def _xyz(loc) -> tuple[float, float, float]:
    """A build123d ``Vector`` (has ``.X/.Y/.Z``) or an ``(x, y, z)`` sequence → an
    ``(x, y, z)`` float tuple. Shared by the detectors and the lint coverage checks
    so the Vector-unpacking idiom lives in one place."""
    if hasattr(loc, "X"):
        return (loc.X, loc.Y, loc.Z)
    x, y, z = loc
    return (float(x), float(y), float(z))


@dataclass(frozen=True)
class HoleRef:
    """A position-keyed reference to a hole — the IR-typed value the cover / hole-table
    bookkeeping matches on, so the shared escalation never needs a recogniser ``Hole``
    object (ADR 0008 Amendment 6). Built from any location via :meth:`of` (rounded, so
    two references at the same position compare equal)."""

    x: float
    y: float
    z: float

    @classmethod
    def of(cls, loc) -> HoleRef:
        x, y, z = _xyz(loc)
        return cls(round(x, 3), round(y, 3), round(z, 3))


def _axis_letter(obj) -> str:
    """Letter (``"x"``/``"y"``/``"z"``) of ``obj.axis``'s dominant component.

    ``obj`` is anything carrying an ``.axis`` 3-vector (a hole or a boss).
    """
    return max(zip("xyz", obj.axis, strict=True), key=lambda t: abs(t[1]))[0]


def _fmt(v: float) -> str:
    """Format a float as integer string if whole, otherwise 1 dp. The one number
    formatter the IR (:mod:`draftwright.model.ir`) and the drawing layers
    (:mod:`draftwright._core`) share (#700 — the two copies had already begun to
    drift on ``-0``)."""
    r = round(v)
    return str(r) if abs(v - r) < 1e-6 else f"{v:.1f}"


def _boxes_overlap(a, b) -> bool:
    """True when two ``(x0, y0, x1, y1)`` AABBs overlap (strict: a touch is not
    an overlap). The one pairwise test behind both the placement-side
    ``_box_hits`` and the lint-side overlap checks (#700). Nuance: a degenerate
    (zero-width/height) box strictly inside the other counts as overlapping
    here, where the pre-#700 ``_box_hits`` form said no — the conservative
    direction for obstacle tests (rejects, never overprints)."""
    return bool(a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1])


def _segment_crosses_box(p1, p2, box) -> bool:
    """True when line segment *p1*-*p2* intersects axis-aligned *box*
    ``(x0, y0, x1, y1)`` — the precise counterpart of ``_box_hits`` for a
    genuinely diagonal shaft (ADR 0009 P4/#318, #305: "a diagonal leader's box
    over-claims its empty triangle"). Boxing an angled segment for a coarse
    reject is correct and cheap; boxing it for the final accept/reject decision
    over-avoids free space a real diagonal never crosses. Endpoint-in-box and
    the 4 edge-crossing cases (a standard segment/AABB test).

    The crossing test uses strict inequality deliberately, not an inclusive
    ``<= 0`` — an inclusive test also treats a segment merely COLLINEAR with
    one of the box's (infinite) edge lines as a hit, regardless of whether it
    is anywhere near the box along that line (verified: a vertical segment at
    ``x == box.x0`` but far outside ``[y0, y1]`` false-hits under `<=`). That
    false-positive class is common (any axis-aligned shaft sharing an X or Y
    coordinate with an edge), unlike the strict form's own known gap — a
    segment passing exactly through two opposite corners is a measure-zero
    event for the continuous, non-integer leader positions this computes over
    (review finding, #351 P5 strand 3: tried the inclusive form, reverted).

    Its sibling :func:`_segment_clips_box` is the *inclusive* (Liang–Barsky)
    form lint uses — boundary semantics differ by design; pick per the caller's
    false-positive tolerance (#700)."""
    x0, y0, x1, y1 = box

    def _inside(p):
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    if _inside(p1) or _inside(p2):
        return True

    def _cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def _seg_seg(a1, a2, b1, b2):
        d1, d2 = _cross(b1, b2, a1), _cross(b1, b2, a2)
        d3, d4 = _cross(a1, a2, b1), _cross(a1, a2, b2)
        return ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
            (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
        )

    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    return any(_seg_seg(p1, p2, corners[i], corners[(i + 1) % 4]) for i in range(4))


def _segment_clips_box(p, q, box, pad=0.0) -> bool:
    """Liang–Barsky: does segment *p*→*q* intersect the *pad*-inflated AABB
    *box*? Inclusive at the boundary (a touch is a hit) — the tolerant form the
    lint checks use; :func:`_segment_crosses_box` is the strict placement-side
    sibling (#700)."""
    minx, miny, maxx, maxy = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x0 - minx), (dx, maxx - x0), (-dy, y0 - miny), (dy, maxy - y0)):
        if abs(pp) < 1e-12:
            if qq < 0:
                return False
        else:
            r = qq / pp
            if pp < 0:
                if r > t1:
                    return False
                t0 = max(t0, r)
            else:
                if r < t0:
                    return False
                t1 = min(t1, r)
    return t0 <= t1
