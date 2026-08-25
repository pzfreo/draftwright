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

from b123d_recognisers import full_cylinders
from build123d import Compound

_log = logging.getLogger(__name__)

# Axis letter -> the orthographic view a feature on that axis reads end-on in: a z-hole is
# a circle in plan, an x-channel is a cross-section in side.
#
# The ONE owner of that routing. It was duplicated as a bare literal at eight sites across
# six modules, which is ADR 0018's own sentence about itself — "which views should exist is
# a decision nothing currently owns" — in miniature: a mapping copied eight times cannot be
# taught that a view is absent, and the engine's answer to a missing view was a `KeyError`
# raised inside a centermark pass. Route through this, never re-spell it (#1130).
_END_ON = {"x": "side", "y": "front", "z": "plan"}

# Axis letter -> the orthographic view a planar face with that NORMAL shows as an edge in
# (prefer front, else side). The companion routing to `_END_ON`, and its second owner: this
# one was spelled out three times — the groove profile callouts, `declare._EDGE_ON_VIEW` and
# `planner._PROFILE`, the last of which documented the duplication in a comment rather than
# resolving it. Same reasoning as above: route through it, never re-spell it (#1130).
_EDGE_ON = {"x": "front", "y": "side", "z": "front"}

# Model axes retained by each orthographic projection.  Besides documenting the projection
# convention once, this lets surface-bound annotations choose a radial direction that the
# requested view can actually show (#1276).
_VIEW_AXES = {"plan": ("x", "y"), "front": ("x", "z"), "side": ("y", "z")}


def _radial_axis_in_view(axis: str, view: str) -> str:
    """Return a principal radial axis visible in *view* for a shaft along *axis*.

    Profile views contain the shaft axis and exactly one radial axis.  A caller may also
    explicitly choose the face-on view, where both projected axes are radial; the first is a
    deterministic, equally physical surface direction.
    """
    try:
        return next(candidate for candidate in _VIEW_AXES[view] if candidate != axis)
    except (KeyError, StopIteration) as e:
        raise ValueError(f"no radial axis for shaft axis {axis!r} in view {view!r}") from e


def _canonical_profile_site(site, centre, axis: str, view: str) -> tuple[float, float, float]:
    """Rotate a turned surface *site* about its shaft onto the selected profile plane.

    Conical and toroidal recognisers may return any circumferential point on the same physical
    edge treatment.  Orthographic projection can discard that point's sole radial component
    (notably an X-offset site viewed in the Y-Z side view).  Preserve its axial station and
    radial distance while rotating it to the radial axis visible in *view*.
    """
    values = [float(value) for value in site]
    centre_values = [float(value) for value in centre]
    axis_i = "xyz".index(axis)
    radial = [i for i in (0, 1, 2) if i != axis_i]
    visible_i = "xyz".index(_radial_axis_in_view(axis, view))
    if visible_i not in radial:
        raise ValueError(f"view {view!r} is not a profile view of axis {axis!r}")
    hidden_i = next(i for i in radial if i != visible_i)
    visible_delta = values[visible_i] - centre_values[visible_i]
    hidden_delta = values[hidden_i] - centre_values[hidden_i]
    radius = math.hypot(visible_delta, hidden_delta)
    direction = visible_delta if abs(visible_delta) > 1e-12 else hidden_delta
    values[visible_i] = centre_values[visible_i] + math.copysign(radius, direction or 1.0)
    values[hidden_i] = centre_values[hidden_i]
    return (values[0], values[1], values[2])


def _turned_profile_site(site, axis: str, view: str, cylinders) -> tuple[float, float, float]:
    """Return *site* rotated onto *view* about its nearest external shaft axis.

    A part may contain several parallel shafts, so the part bounding-box centre is not an
    axis. Rank the shared cylinder substrate by the site's distance from each finite cylinder
    patch (radial surface gap plus axial-span gap), then rotate about that cylinder's own
    ``axis_xyz``.  The finite span matters for compounds: an unrelated cylinder at a remote
    axial station can coincidentally have the exact radial distance (#1276 review).
    When no matching external cylinder exists, preserve the physical site: inventing an axis
    would be worse than retaining a possibly edge-on circumferential anchor.
    """
    values = (float(site[0]), float(site[1]), float(site[2]))
    axis_i = "xyz".index(axis)
    radial = tuple(i for i in (0, 1, 2) if i != axis_i)
    candidates = []
    # ``analyse_cylinders`` is deliberately a complete face inventory: longitudinal blends
    # and slot caps are cylindrical too.  Only its public substrate filter proves which
    # records collectively describe a shaft, including an OD split by a keyway/slot.
    substrates = (full_cylinders(list(group)) for group in cylinders)
    for cylinder in (item for group in substrates for item in group):
        if not cylinder.get("external") or cylinder.get("axis") != axis:
            continue
        centre = tuple(float(value) for value in cylinder["axis_xyz"])
        radial_distance = math.hypot(
            values[radial[0]] - centre[radial[0]],
            values[radial[1]] - centre[radial[1]],
        )
        surface_gap = abs(radial_distance - float(cylinder["diameter"]) / 2)
        direction = tuple(float(value) for value in cylinder["dir_xyz"])
        axial_station = sum(value * component for value, component in zip(values, direction))
        s_lo = float(cylinder["s_lo"])
        s_hi = float(cylinder["s_hi"])
        axial_gap = max(s_lo - axial_station, 0.0, axial_station - s_hi)
        patch_distance = math.hypot(surface_gap, axial_gap)
        # A complete cylinder elsewhere in a compound is not evidence for this feature.  If
        # the recogniser's physical point is more than one radius from the finite patch, keep
        # that point rather than rotating it around an invented remote axis.  This also keeps
        # a chamfer on a partial OD (for example a D-shaft) safe when ``full_cylinders`` quite
        # correctly declines to call the partial wall a complete shaft substrate.
        radius = float(cylinder["diameter"]) / 2
        if patch_distance > radius:
            continue
        candidates.append(
            (
                (
                    patch_distance,
                    axial_gap,
                    surface_gap,
                    radial_distance,
                    int(cylinder["solid_idx"]),
                    centre[radial[0]],
                    centre[radial[1]],
                ),
                centre,
            )
        )
    if not candidates:
        return values
    _score, centre = min(candidates, key=lambda candidate: candidate[0])
    return _canonical_profile_site(values, centre, axis, view)


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


def _convex_polygons_overlap(left, right) -> bool:
    """Whether two convex page-plane polygons have positive-area overlap.

    The separating-axis test is strict: touching edges/vertices are clear, the
    same placement convention as :func:`_boxes_overlap`.
    """

    if len(left) < 3 or len(right) < 3:
        return False
    left_x, left_y = zip(*left, strict=True)
    right_x, right_y = zip(*right, strict=True)
    if (
        max(left_x) <= min(right_x)
        or max(right_x) <= min(left_x)
        or max(left_y) <= min(right_y)
        or max(right_y) <= min(left_y)
    ):
        return False
    axes: list[tuple[float, float]] = []
    for points in (left, right):
        axes.extend(
            (-(second[1] - first[1]), second[0] - first[0])
            for first, second in zip(points, (*points[1:], points[0]), strict=True)
        )
    for ax, ay in axes:
        if abs(ax) + abs(ay) <= 1e-12:
            continue
        left_projection = [x * ax + y * ay for x, y in left]
        right_projection = [x * ax + y * ay for x, y in right]
        if max(left_projection) <= min(right_projection) or max(right_projection) <= min(
            left_projection
        ):
            return False
    return True


def _convex_polygon_overlaps_box(points, box) -> bool:
    """Whether a convex page-plane polygon has positive-area overlap with an AABB.

    This is the small separating-axis primitive used by
    :func:`_leader_ink_crosses_box`.  Keeping it here makes the rendered-ink model pure
    rectangle/vector maths: placement does not build temporary OCC geometry merely to ask
    whether a candidate is clear (ADRs 0004/0014).
    """
    corners = (
        (box[0], box[1]),
        (box[2], box[1]),
        (box[2], box[3]),
        (box[0], box[3]),
    )
    return _convex_polygons_overlap(points, corners)


def _stroke_polygon(first, second, width: float):
    """The exact swept rectangle for one rendered straight stroke, or ``None``."""

    dx, dy = float(second[0]) - float(first[0]), float(second[1]) - float(first[1])
    length = math.hypot(dx, dy)
    half = max(0.0, float(width)) / 2.0
    if length <= 1e-12 or half <= 0.0:
        return None
    vx, vy = -dy / length * half, dx / length * half
    return (
        (float(first[0]) + vx, float(first[1]) + vy),
        (float(second[0]) + vx, float(second[1]) + vy),
        (float(second[0]) - vx, float(second[1]) - vy),
        (float(first[0]) - vx, float(first[1]) - vy),
    )


def _leader_ink_polygons(tip, elbow, *, arrow_length: float, line_width: float):
    """Convex components of a leader's rendered tip→elbow shaft and arrow."""

    dx, dy = float(elbow[0]) - float(tip[0]), float(elbow[1]) - float(tip[1])
    length = math.hypot(dx, dy)
    half_line = max(0.0, float(line_width)) / 2.0
    head_length = max(0.0, float(arrow_length))
    if length <= 1e-12:
        half = max(half_line, head_length / 3.0)
        if half <= 0.0:
            return ()
        return (
            (
                (float(tip[0]) - half, float(tip[1]) - half),
                (float(tip[0]) + half, float(tip[1]) - half),
                (float(tip[0]) + half, float(tip[1]) + half),
                (float(tip[0]) - half, float(tip[1]) + half),
            ),
        )

    ux, uy = dx / length, dy / length
    vx, vy = -uy, ux
    polygons = []
    shaft = _stroke_polygon(tip, elbow, line_width)
    if shaft is not None:
        polygons.append(shaft)
    if head_length > 0.0:
        half_head = head_length / 3.0
        base_x, base_y = tip[0] + ux * head_length, tip[1] + uy * head_length
        polygons.append(
            (
                (float(tip[0]), float(tip[1])),
                (base_x + vx * half_head, base_y + vy * half_head),
                (base_x - vx * half_head, base_y - vy * half_head),
            )
        )
    return tuple(polygons)


def _leader_ink_crosses_box(
    tip,
    elbow,
    box,
    *,
    arrow_length: float,
    line_width: float,
) -> bool:
    """Whether a rendered leader's tip-to-elbow ink overlaps *box*.

    A leader is not a zero-width segment.  Its shaft is a swept rectangle of
    ``line_width`` and each supported arrowhead style is contained by the triangle from
    the tip to a base ``arrow_length`` along the shaft, with the helper's
    ``arrow_length / 3`` lateral flare.  Testing those two local convex footprints keeps
    the arrow clearance near the tip; inflating the entire shaft by the arrow size would
    reject genuinely clear obstacles near the elbow (#367).

    Touching boundaries are clear, matching :func:`_boxes_overlap` and the placement-side
    semantics of :func:`_segment_crosses_box`.
    """
    return any(
        _convex_polygon_overlaps_box(polygon, box)
        for polygon in _leader_ink_polygons(
            tip,
            elbow,
            arrow_length=arrow_length,
            line_width=line_width,
        )
    )


# ── Filled material field (#798) ────────────────────────────────────────────────────────
# Leader routing needs "does this shaft travel THROUGH the part", which an outline
# CROSSING COUNT cannot answer: a shaft passing over an internal hole crosses that
# circle twice and has entered no material at all. The filled model subtracts every
# void for free, so the void exemptions a count-based test needs (hole, pocket
# opening, bore) stop being policy and become geometry.
#
# The field is a bounded set of page-plane triangles — the caller lowers the projected
# view faces (that half needs OCC and lives above this leaf). Everything below is exact
# rational-arithmetic clipping: no rasterisation, no sampling, no tolerance sweep, so
# the answer is identical on every platform (ADR 0001).

#: Page mm of material below which a shaft is not treated as cutting the part.
#:
#: A visibility floor, not a tuning knob: rendered line work is ~0.25 mm wide, so a cut
#: thinner than a stroke cannot be seen on the sheet, and a curved boundary's chord
#: approximation must not be reported as a defect of the drawing.
#:
#: Defined HERE, once, because the router and the critique must agree by construction —
#: that is the point of #798. Two copies of this number is one edit away from the
#: disagreement the shared field removes.
MATERIAL_VISIBLE_FLOOR = 0.25

_MATERIAL_SPAN_TICKS = 1_000_000  # fixed-point resolution of the parametric interval union
_MATERIAL_MAX_GRID = 128  # per-axis cell cap: bounds index memory for a dense mesh


@dataclass(frozen=True)
class MaterialField:
    """A view's filled projected material, as page-plane triangles with a uniform grid index.

    Built by :func:`material_field`. ``triangles`` are page-mm coordinates; ``box`` is their
    aggregate AABB; ``index`` maps a grid cell to the triangles overlapping it, so a probe
    visits only the cells its segment actually crosses rather than the whole mesh.
    """

    triangles: tuple[tuple[tuple[float, float], tuple[float, float], tuple[float, float]], ...]
    box: tuple[float, float, float, float] | None
    index: dict[tuple[int, int], tuple[int, ...]]
    cell: float

    def __bool__(self) -> bool:
        return bool(self.triangles)


def material_field(triangles) -> MaterialField:
    """Index page-plane *triangles* into a :class:`MaterialField`.

    Degenerate (zero-area) and non-finite triangles are dropped — they carry no material and
    would only add probe cost. The grid is sized from the triangle count (``ceil(sqrt(n))``
    cells per axis, capped), so the cell size is a deterministic function of the input rather
    than a tuned constant.
    """
    kept: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    for triangle in triangles:
        if len(triangle) != 3:
            continue
        try:
            first, second, third = ((float(point[0]), float(point[1])) for point in triangle)
        except Exception:  # noqa: BLE001 — a malformed lowering entry is simply not material
            continue
        corners = (first, second, third)
        if any(not math.isfinite(value) for point in corners for value in point):
            continue
        (ax, ay), (bx, by), (cx, cy) = corners
        if abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) <= 1e-12:
            continue
        kept.append(corners)
    if not kept:
        return MaterialField((), None, {}, 0.0)
    xs = [x for triangle in kept for x, _ in triangle]
    ys = [y for triangle in kept for _, y in triangle]
    box = (min(xs), min(ys), max(xs), max(ys))
    per_axis = min(_MATERIAL_MAX_GRID, max(1, math.ceil(math.sqrt(len(kept)))))
    extent = max(box[2] - box[0], box[3] - box[1])
    cell = (extent / per_axis) if extent > 0 else 0.0
    index: dict[tuple[int, int], list[int]] = {}
    if cell > 0:
        for position, triangle in enumerate(kept):
            txs = [x for x, _ in triangle]
            tys = [y for _, y in triangle]
            for column in range(
                int((min(txs) - box[0]) // cell), int((max(txs) - box[0]) // cell) + 1
            ):
                for row in range(
                    int((min(tys) - box[1]) // cell), int((max(tys) - box[1]) // cell) + 1
                ):
                    index.setdefault((column, row), []).append(position)
    return MaterialField(
        tuple(kept),
        box,
        {key: tuple(value) for key, value in index.items()},
        cell,
    )


def _segment_triangle_interval(p, q, triangle):
    """Parametric ``(lo, hi)`` sub-interval of *p*→*q* inside *triangle*, or ``None``.

    Exact half-plane clipping against the triangle's three edges (winding-independent), the
    same Liang–Barsky shape :func:`_segment_clip_extent` uses for a box.
    """
    (ax, ay), (bx, by), (cx, cy) = triangle
    winding = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if winding == 0.0:
        return None
    sign = 1.0 if winding > 0 else -1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    lo, hi = 0.0, 1.0
    for (ex, ey), (fx, fy) in (((ax, ay), (bx, by)), ((bx, by), (cx, cy)), ((cx, cy), (ax, ay))):
        edge_x, edge_y = fx - ex, fy - ey
        constant = (edge_x * (p[1] - ey) - edge_y * (p[0] - ex)) * sign
        slope = (edge_x * dy - edge_y * dx) * sign
        if slope == 0.0:
            # Parallel to this edge; only a strictly-outside segment is rejected. The
            # boundary is INCLUSIVE here, unlike _convex_polygons_overlap's touching-is-
            # clear rule, and the difference is forced: these triangles are a decomposition
            # of a filled region, so most edges are interior artefacts of the mesher. A
            # segment running along the diagonal of a meshed square lies on a shared edge
            # of both triangles while being wholly inside the body. Excluding boundaries
            # would make the answer depend on how the region happened to be cut — exactly
            # the platform-dependence this field exists to avoid.
            if constant < 0.0:
                return None
            continue
        bound = -constant / slope
        if slope > 0.0:
            lo = max(lo, bound)
        else:
            hi = min(hi, bound)
        if lo >= hi:
            return None
    return (lo, hi) if hi > lo else None


def material_span(p, q, field: MaterialField) -> float:
    """Page-mm length of segment *p*→*q* that lies inside *field*'s material.

    Zero for a clear route; the traversed length otherwise — a magnitude, not a flag, so a
    0.2 mm graze can rank below a 40 mm cut instead of tying with it. Overlapping triangles
    (adjacent projected faces share edges, and a mesh may double up) are unioned, so the
    result is bounded by the segment's own length however many triangles cover it.

    The union is accumulated in fixed point (:data:`_MATERIAL_SPAN_TICKS` per unit of the
    segment parameter) so summing it is associative and platform-stable, the same convention
    ``layout``'s cost scaling uses.
    """
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    return (
        length
        * sum(hi - lo for lo, hi in material_intervals(p, q, field))
        / (_MATERIAL_SPAN_TICKS)
    )


def material_intervals(
    p, q, field: MaterialField, *, bridge: float = 0.0
) -> tuple[tuple[int, int], ...]:
    """Disjoint fixed-point sub-intervals of *p*→*q* that lie inside *field*'s material.

    Ordered along the segment, in :data:`_MATERIAL_SPAN_TICKS` per unit of the segment
    parameter. The *structure* is what distinguishes a legitimate route from a defective
    one — a leader that exits its own feature makes one traversal, a shaft cutting a
    neighbouring body makes a second — so callers that need that distinction read the
    intervals rather than the summed span.

    *bridge* (page mm) closes gaps narrower than itself before the intervals are returned.
    The triangles approximate curved boundaries, so a shaft grazing one can be split into
    slivers by where the facets happen to fall; without a bridge those slivers read as
    extra traversals and a mesh detail becomes a routing verdict. Set it to the lowering's
    chord tolerance — never larger, or a genuine thin gap between two bodies is absorbed
    and a real cut is reported as one traversal.
    """
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    if not field.triangles or field.box is None:
        return ()
    if length <= 1e-12:
        return ()
    if not _segment_clips_box(p, q, field.box):
        return ()
    raw: list[tuple[int, int]] = []
    for position in _probe_triangles(p, q, field):
        span = _segment_triangle_interval(p, q, field.triangles[position])
        if span is None:
            continue
        lo = max(0, min(_MATERIAL_SPAN_TICKS, round(span[0] * _MATERIAL_SPAN_TICKS)))
        hi = max(0, min(_MATERIAL_SPAN_TICKS, round(span[1] * _MATERIAL_SPAN_TICKS)))
        if hi > lo:
            raw.append((lo, hi))
    if not raw:
        return ()
    raw.sort()
    gap = (
        0
        if bridge <= 0.0
        else max(0, min(_MATERIAL_SPAN_TICKS, round(bridge / length * _MATERIAL_SPAN_TICKS)))
    )
    merged: list[tuple[int, int]] = []
    current_lo, current_hi = raw[0]
    for lo, hi in raw[1:]:
        if lo > current_hi + gap:
            merged.append((current_lo, current_hi))
            current_lo, current_hi = lo, hi
        else:
            current_hi = max(current_hi, hi)
    merged.append((current_lo, current_hi))
    return tuple(merged)


def material_reentry_span(p, q, field: MaterialField, *, bridge: float = 0.0) -> float:
    """Page-mm of *p*→*q* inside material **after** its first traversal — the routing defect.

    A leader is attached to the feature it names, so its tip starts on or inside the body and
    its shaft must pass through material to reach clear page. That first traversal is the
    legitimate exit every ⌀, hole and pocket callout makes, and :func:`material_span` counts
    it; charging for it would condemn every correct leader on the sheet. What is *not*
    legitimate is going back in: a second traversal means the shaft left the body and cut
    into something else — a neighbouring step, a flange, another lobe — which is exactly the
    defect #798 names.

    This is the filled-region form of the Phase-1 crossing-count rule (one outline crossing
    is an exit, two is a cut), and it inherits none of that rule's void problem: a shaft
    passing over a through-hole re-enters no material, so it is not a second traversal.

    The one assumption is that *p* is the leader's own attachment point. Every candidate
    generator in the engine builds tips that way (a projected feature origin, or a rim point
    advanced along the lead direction), so a shaft that begins in clear space and ploughs
    through the body would be charged for one traversal fewer than it deserves — a case the
    geometry cannot distinguish and the tip convention does not produce.
    """
    intervals = material_intervals(p, q, field, bridge=bridge)
    if len(intervals) <= 1:
        return 0.0
    length = math.hypot(q[0] - p[0], q[1] - p[1])
    return length * sum(hi - lo for lo, hi in intervals[1:]) / _MATERIAL_SPAN_TICKS


def _probe_triangles(p, q, field: MaterialField):
    """Indices of *field* triangles whose cells segment *p*→*q* crosses, each yielded once.

    An incremental cell walk, not a bounding-box sweep: a long diagonal shaft's bbox can
    cover the entire grid, which would defeat the index exactly where it matters most.
    """
    if field.cell <= 0:
        return range(len(field.triangles))
    box = field.box
    assert box is not None  # guarded by the caller's empty-field check
    columns = max(1, int((box[2] - box[0]) // field.cell) + 1)
    rows = max(1, int((box[3] - box[1]) // field.cell) + 1)

    def cell_of(point):
        return (
            min(columns - 1, max(0, int((point[0] - box[0]) // field.cell))),
            min(rows - 1, max(0, int((point[1] - box[1]) // field.cell))),
        )

    # No clip needed: cell_of clamps to the grid, so the walk already spans exactly the
    # cells the segment can reach. The entry-time maths below uses the true endpoints, so
    # clamping the ENDS cannot change which interior cells the line passes through.
    start, end = (float(p[0]), float(p[1])), (float(q[0]), float(q[1]))
    column, row = cell_of(start)
    last_column, last_row = cell_of(end)
    seen: dict[int, None] = {}
    steps = 0
    limit = columns + rows + 2  # a monotone walk visits at most this many cells
    while steps <= limit:
        steps += 1
        for position in field.index.get((column, row), ()):
            seen.setdefault(position, None)
        if (column, row) == (last_column, last_row):
            break
        # Advance along whichever axis reaches its next cell boundary first; an exact tie
        # (a corner-crossing diagonal) advances the column. The cell that is only touched
        # at that corner is skipped, which is sound because a segment through a single
        # point spans zero length of any triangle there — but it IS a skip, so do not read
        # this as visiting every incident cell.
        next_column = column + (1 if last_column > column else -1 if last_column < column else 0)
        next_row = row + (1 if last_row > row else -1 if last_row < row else 0)
        if next_column == column and next_row == row:
            break
        span_x = _cell_entry_t(start, end, box[0] + max(column, next_column) * field.cell, 0)
        span_y = _cell_entry_t(start, end, box[1] + max(row, next_row) * field.cell, 1)
        if next_column != column and (span_y is None or (span_x is not None and span_x <= span_y)):
            column = next_column
        elif next_row != row:
            row = next_row
        else:
            column = next_column
    return tuple(seen)


def _cell_entry_t(start, end, boundary: float, axis: int):
    """Segment parameter at which *start*→*end* meets the *axis* plane at *boundary*."""
    delta = end[axis] - start[axis]
    if delta == 0.0:
        return None
    return (boundary - start[axis]) / delta


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
