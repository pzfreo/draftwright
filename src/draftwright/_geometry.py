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

import logging
import math
from dataclasses import dataclass

from build123d import Compound

_log = logging.getLogger(__name__)

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


def _solids_body(part, src: str = "part"):
    """The part reduced to just its solids — the geometry the drawing is *of*.

    AP242 STEP files (and hand-built Compounds) can carry non-solid geometry beside
    the solid — PMI presentation wires, leader curves, construction edges/sketches —
    which, left in, draw as phantom rectangles in every view and inflate the bounding
    box, corrupting the scale choice and the envelope dimensions. Shared by
    :func:`_analyse` and :meth:`draftwright.Sheet.model` (#453) so the model a caller
    *inspects* is wrapped from the exact same body the engine *draws*."""
    solids = part.solids()
    if not solids:
        return part
    body = solids[0] if len(solids) == 1 else Compound(children=list(solids))
    if body.bounding_box().size != part.bounding_box().size or len(part.edges()) != len(
        body.edges()
    ):
        _log.info("Dropping non-solid geometry from %s (PMI presentation data)", src)
    return body


def _axis_letter(obj) -> str:
    """Letter (``"x"``/``"y"``/``"z"``) of ``obj.axis``'s dominant component.

    ``obj`` is anything carrying an ``.axis`` 3-vector (a hole or a boss).
    """
    return _axis_letter_of(obj.axis)


#: The in-plane basis for each axis, as ``(u, v)`` world unit vectors — cyclic and
#: right-handed, so ``u × v = +axis``.
#:
#: THE one choice, deliberately shared: recognition projects a pattern's member centres onto
#: this basis to find its lattice, and declaration lays a declared pattern out along it. A grid
#: ``angle`` therefore means the same thing on both sides of the IR. They disagreed until #969 —
#: recognition built its own basis from a cross product with a reference direction, spanning the
#: same plane a quarter turn round, so every declared grid came back transposed in place.
_PLANE_AXES = {
    "x": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "y": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0)),
    "z": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
}


def plane_axes(axis) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """The two in-plane unit directions for a feature lying perpendicular to *axis*.

    *axis* is an axis letter or a 3-vector; a vector is reduced to its dominant component's
    letter, so ``(0, 0, -1)`` and ``(0, 0, 1)`` share a basis. That is deliberate: the IR
    carries only the letter, so a sign it cannot express must not change the frame.
    """
    letter = axis if isinstance(axis, str) else _axis_letter_of(axis)
    return _PLANE_AXES[letter]


def _axis_letter_of(axis) -> str:
    """Letter of a bare 3-vector's dominant component (:func:`_axis_letter` takes an object)."""
    return max(zip("xyz", axis, strict=True), key=lambda t: abs(t[1]))[0]


def _is_principal_axis(axis) -> bool:
    """Whether *axis* is exactly along X, Y or Z after analytic-noise snapping.

    ``Frame.axis`` can name only these directions. Exactness is evaluated after the same
    ``1e-6`` component snap used for analytic STEP noise. A cosine-tolerance test would
    admit a small but real second component (and millimetres of flattening over a long
    part); requiring exactly one non-zero component expresses the representable contract.
    """
    snapped = [0.0 if abs(float(component)) < 1e-6 else float(component) for component in axis]
    return sum(component != 0.0 for component in snapped) == 1


def _axis_direction_components(axis: str, direction=None):
    """Validate a direction and return ``(raw, norm, dominant_index)``."""
    if axis not in "xyz" or len(axis) != 1:
        raise ValueError("axis must be 'x', 'y', or 'z'")
    if direction is None:
        direction = tuple(1.0 if letter == axis else 0.0 for letter in "xyz")
    try:
        raw = tuple(float(component) for component in direction)
    except (TypeError, ValueError) as exc:
        raise ValueError("axis_direction must be a 3-vector") from exc
    if len(raw) != 3:
        raise ValueError("axis_direction must be a 3-vector")
    if not all(math.isfinite(component) for component in raw):
        raise ValueError("axis_direction must contain only finite values")
    norm = math.hypot(*raw)
    if norm <= 1e-12:
        raise ValueError("axis_direction must be non-zero")
    idx = "xyz".index(axis)
    if abs(raw[idx]) + norm * 1e-9 < max(abs(component) for component in raw):
        raise ValueError(f"axis_direction's dominant component must match axis={axis!r}")
    return raw, norm, idx


def _normalised_axis_direction(axis: str, direction=None) -> tuple[float, float, float]:
    """Full-precision unit direction used for geometric projection."""
    raw, norm, idx = _axis_direction_components(axis, direction)
    sign = -1.0 if raw[idx] < 0 else 1.0
    return (
        sign * raw[0] / norm,
        sign * raw[1] / norm,
        sign * raw[2] / norm,
    )


def _canonical_axis_direction(axis: str, direction=None) -> tuple[float, float, float]:
    """Return a stable unit direction whose named dominant component is positive.

    ``axis`` remains the orthographic routing hint used by the IR. ``direction`` is the
    actual geometric axis needed to distinguish slanted stock lines; omitting it means the
    corresponding principal axis. Six decimal places preserve direction while keeping
    recognition/declaration identities stable across round trips.
    """
    raw, norm, idx = _axis_direction_components(axis, direction)
    sign = -1.0 if raw[idx] < 0 else 1.0
    # A previously canonical six-decimal vector is close enough to unit length that
    # normalising it again can move the last digit. Preserve it to make emit -> declare
    # idempotent; arbitrary vectors are still normalised on first entry.
    unit = (
        tuple(sign * component for component in raw)
        if abs(norm - 1.0) <= 2e-6
        else _normalised_axis_direction(axis, raw)
    )
    rounded = tuple(0.0 if abs(component) < 0.5e-6 else round(component, 6) for component in unit)
    return (rounded[0], rounded[1], rounded[2])


def _canonical_axis_span(axis: str, direction, span) -> tuple[float, float]:
    """Express an axial extent along the canonical positive direction."""
    raw, _norm, idx = _axis_direction_components(axis, direction)
    lo, hi = (float(value) for value in span)
    if raw[idx] < 0:
        lo, hi = -hi, -lo
    return (round(lo, 3), round(hi, 3))


def _axis_line_coordinates(axis: str, point, direction=None) -> tuple[float, float]:
    """Canonical in-plane coordinates of a 3-D axis line.

    The perpendicular foot from the origin makes the result invariant to which point on the
    line a geometry kernel reports. The named dominant coordinate is omitted; together with
    the direction it is recoverable from the foot's perpendicularity, so two numbers retain
    the aligned-stock representation while remaining sufficient for slanted stock.
    """
    px, py, pz = (float(component) for component in point)
    # Use the unrounded unit vector here. Rounding before projection amplifies angular error
    # into millimetres when the reported axis point is tens of metres from the origin.
    vector = _normalised_axis_direction(axis, direction)
    along = px * vector[0] + py * vector[1] + pz * vector[2]
    foot = tuple(component - along * delta for component, delta in zip((px, py, pz), vector))
    keep = [i for i, letter in enumerate("xyz") if letter != axis]
    coordinates = tuple(round(foot[i], 3) for i in keep)
    return (
        0.0 if coordinates[0] == 0 else coordinates[0],
        0.0 if coordinates[1] == 0 else coordinates[1],
    )


def _axis_direction_is_aligned(axis: str, direction, *, tol: float = 1e-3) -> bool:
    """Whether a canonical direction follows the principal axis named by ``axis``."""
    vector = _canonical_axis_direction(axis, direction)
    idx = "xyz".index(axis)
    return abs(vector[idx] - 1.0) <= tol and all(
        abs(vector[j]) <= tol for j in range(3) if j != idx
    )


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


def _segment_clip_extent(p, q, box, pad=0.0):
    """AABB of the part of *p*→*q* inside the *pad*-inflated *box*.

    Return ``None`` when the segment is disjoint.  The Liang–Barsky clip is
    inclusive at the boundary: lint uses the returned extent both as its
    tolerant hit gate and to measure only the rendered part that actually
    reaches a label (#1144).
    """
    minx, miny, maxx, maxy = box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad
    x0, y0 = p
    dx, dy = q[0] - x0, q[1] - y0
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x0 - minx), (dx, maxx - x0), (-dy, y0 - miny), (dy, maxy - y0)):
        if abs(pp) < 1e-12:
            if qq < 0:
                return None
        else:
            r = qq / pp
            if pp < 0:
                if r > t1:
                    return None
                t0 = max(t0, r)
            else:
                if r < t0:
                    return None
                t1 = min(t1, r)
    if t0 > t1:
        return None
    first = (x0 + t0 * dx, y0 + t0 * dy)
    second = (x0 + t1 * dx, y0 + t1 * dy)
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[0], second[0]),
        max(first[1], second[1]),
    )


def _segment_clips_box(p, q, box, pad=0.0) -> bool:
    """Liang–Barsky: does segment *p*→*q* intersect the *pad*-inflated AABB
    *box*? Inclusive at the boundary (a touch is a hit) — the tolerant form the
    lint checks use; :func:`_segment_crosses_box` is the strict placement-side
    sibling (#700)."""
    return _segment_clip_extent(p, q, box, pad=pad) is not None


def _segments_cross_or_overlap(a1, a2, b1, b2) -> bool:
    """Whether two page-plane segments cross or overlap beyond a shared endpoint.

    Proper interior crossings, a T-junction into the other segment's interior,
    and collinear overlap are conflicts. Merely sharing one endpoint is not: two
    leaders may legitimately terminate at the same feature/ring boundary.
    """
    epsilon = 1e-9

    def cross(origin, first, second):
        return (first[0] - origin[0]) * (second[1] - origin[1]) - (first[1] - origin[1]) * (
            second[0] - origin[0]
        )

    def same(first, second):
        return abs(first[0] - second[0]) <= epsilon and abs(first[1] - second[1]) <= epsilon

    def on_segment(point, first, second):
        return (
            abs(cross(first, second, point)) <= epsilon
            and min(first[0], second[0]) - epsilon
            <= point[0]
            <= max(first[0], second[0]) + epsilon
            and min(first[1], second[1]) - epsilon
            <= point[1]
            <= max(first[1], second[1]) + epsilon
        )

    oa1 = cross(b1, b2, a1)
    oa2 = cross(b1, b2, a2)
    ob1 = cross(a1, a2, b1)
    ob2 = cross(a1, a2, b2)
    if oa1 * oa2 < -(epsilon**2) and ob1 * ob2 < -(epsilon**2):
        return True

    if all(abs(value) <= epsilon for value in (oa1, oa2, ob1, ob2)):
        axis = (
            0
            if max(abs(a2[0] - a1[0]), abs(b2[0] - b1[0]))
            >= max(abs(a2[1] - a1[1]), abs(b2[1] - b1[1]))
            else 1
        )
        overlap = min(max(a1[axis], a2[axis]), max(b1[axis], b2[axis])) - max(
            min(a1[axis], a2[axis]), min(b1[axis], b2[axis])
        )
        return bool(overlap > epsilon)

    for point, first, second in (
        (a1, b1, b2),
        (a2, b1, b2),
        (b1, a1, a2),
        (b2, a1, a2),
    ):
        if on_segment(point, first, second) and not (same(point, first) or same(point, second)):
            return True
    return False
