"""Analytical dimension typography, footprint, and stroke probes for placement.

The corridor solver consumes these boxes and segments before it constructs the
selected build123d annotation. The arithmetic stays beside the render passes at
the annotation rank and uses only lower-layer typography and arrow bounds.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from build123d import FontStyle
from build123d_drafting.helpers import DEFAULT_FONT_PATH

from draftwright._core import _dimension_head_bounds, _text_size


def short_dimension_label_offset(p1, p2, draft, label):
    """Hang a tight inline label beyond its second witness so both heads stay visible.

    ``Dimension`` centres every supplied label, even after its outside-arrow flip.
    On a 3.5 mm location this puts text over a terminator and erases one head.
    The offset follows the helper's own external-label distance; the corridor
    candidate and its analytical footprint use the same value.
    """
    if (
        getattr(draft, "text_position", "inline"),
        getattr(draft, "text_orientation", "aligned"),
    ) != ("inline", "aligned"):
        return 0.0
    length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if length <= 1e-9:
        return 0.0
    width = _text_size(
        label,
        draft.font_size,
        getattr(draft, "font_path", DEFAULT_FONT_PATH),
        getattr(draft, "font", "Arial"),
        getattr(draft, "font_style", FontStyle.REGULAR),
    )[0]
    arrow = draft.arrow_length
    pad = draft.pad_around_text
    if width + 2 * arrow < length and length / 2 - width / 2 - pad > arrow / 2:
        return 0.0
    return length / 2 + 2 * arrow + pad + width / 2


def dim_footprint(p1, p2, side, distance, draft, label, *, label_offset_x=0.0):
    """Analytical page-mm AABB ``(x0, y0, x1, y1)`` of the :class:`Dimension` that
    ``_dim(p1, p2, side, distance, draft, label=label)`` would build — WITHOUT
    constructing any OCC geometry (#602: a rejected candidate must not pay the
    rendering cost).

    Mirrors ``helpers.Dimension`` / build123d ``ExtensionLine``: each extension line
    is the object→dimension-line segment *translated* ``extension_gap`` along itself,
    so it spans ``p + side·gap`` → ``p + side·(distance + gap)`` — starting ``gap``
    clear of the object and overshooting the dimension line by the same ``gap``. The
    measured label is centred on the line's midpoint, or moved along the line by
    ``label_offset_x`` when the producer hangs a tight label beyond its second
    witness. Its extents swap for a vertical measured segment (the label is
    rotated); everything strokes at ``line_width``. With inside arrows the heads
    lie within the extension-line overshoot (at preset sizes).

    Tight spans mirror helpers ≥0.14's outside-arrows flip (``_dim_line_ink``):
    when the label and both heads don't fit — ``w + 2·al ≥ length`` or the shaft
    piece beside a head would vanish — the ink extends ``2·arrow_length`` past
    each end along the line. ``Dimension`` otherwise centres its label regardless
    of width, so producers requiring two visible heads supply an explicit offset.
    When the label's keep-clear reaches a witness end, that witness's overshoot
    past the dimension line is cut away with it. Without this model
    the estimate under-covers exactly the dims v0.14 widens, and the
    accept-time rebuild fails validation until the candidate is dropped (the
    declared-plate 8 mm thickness dim was the first casualty). Assumes the
    centred label by default (``label_offset_x=0``).
    Callers accepting a candidate off this footprint must still build the real
    geometry once and re-validate its box (the #602 validation fallback) so any
    residual mismatch degrades to a wasted probe, never a collision.
    """
    if isinstance(side, str):  # mirror helpers._SIDE_VECTORS ("above"/"below"/"left"/"right")
        side = {
            "above": (0.0, 1.0, 0.0),
            "below": (0.0, -1.0, 0.0),
            "left": (-1.0, 0.0, 0.0),
            "right": (1.0, 0.0, 0.0),
        }[side]
    sx, sy = side[0], side[1]
    off = abs(distance)
    gap = draft.extension_gap
    dxp, dyp = p2[0] - p1[0], p2[1] - p1[1]
    # Mirror the renderer's font resolution exactly (helpers._font_path): the pinned
    # font *file* when set, else the helpers default; an explicit font_path=None means
    # name-based resolution of draft.font — so pass the name through too.
    w, h = _text_size(
        label,
        draft.font_size,
        getattr(draft, "font_path", DEFAULT_FONT_PATH),
        getattr(draft, "font", "Arial"),
        getattr(draft, "font_style", FontStyle.REGULAR),
    )
    if (
        getattr(draft, "text_position", "inline"),
        getattr(draft, "text_orientation", "aligned"),
    ) != ("inline", "aligned"):
        return _styled_dimension_footprint(p1, p2, side, distance, draft, (w, h))
    hx, hy = (h / 2.0, w / 2.0) if abs(dyp) > abs(dxp) else (w / 2.0, h / 2.0)
    length = math.hypot(dxp, dyp)
    along_x, along_y = (dxp / length, dyp / length) if length > 0 else (0.0, 0.0)
    lcx = (p1[0] + p2[0]) / 2.0 + sx * off + along_x * label_offset_x
    lcy = (p1[1] + p2[1]) / 2.0 + sy * off + along_y * label_offset_x
    far = off + gap
    xs = [p1[0] + sx * gap, p2[0] + sx * gap, lcx - hx, lcx + hx]
    ys = [p1[1] + sy * gap, p2[1] + sy * gap, lcy - hy, lcy + hy]
    # Helpers ≥0.14 tight-span behaviour. Along the line: the label's half-extent
    # is w/2 regardless of orientation (the text rotates with the line); outside
    # arrows extend the ink 2·al past each end when label+heads don't fit. Along
    # the witness: the label keep-clear (h/2 + pad either side of the line) is
    # cut out of a witness the shifted label reaches, removing its overshoot
    # unless a stub past the keep-clear survives (gap > h/2 + pad).
    al = getattr(draft, "arrow_length", 0.9 * draft.font_size)
    tpad = getattr(draft, "pad_around_text", 0.0)
    fits = length > 0 and (w + 2.0 * al < length) and (length / 2.0 - w / 2.0 - tpad > al / 2.0)
    if not fits and length > 0:
        ux, uy = dxp / length, dyp / length
        xs += [p1[0] + sx * off - ux * 2.0 * al, p2[0] + sx * off + ux * 2.0 * al]
        ys += [p1[1] + sy * off - uy * 2.0 * al, p2[1] + sy * off + uy * 2.0 * al]
    label_covers_witness = (
        length > 0
        and min(abs(length / 2.0 + label_offset_x), abs(length / 2.0 - label_offset_x))
        < w / 2.0 + tpad
    )
    if not label_covers_witness or gap > h / 2.0 + tpad:
        xs += [p1[0] + sx * far, p2[0] + sx * far]
        ys += [p1[1] + sy * far, p2[1] + sy * far]
    pad = draft.line_width / 2.0
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


@dataclass(frozen=True)
class AnalyticalDimensionInk:
    """Cheap collision metadata for one axis-aligned dimension candidate."""

    label_bbox: tuple[float, float, float, float]
    segments: tuple[tuple[tuple[float, float], tuple[float, float]], ...]
    box: tuple[float, float, float, float]
    _dw_dimension_candidate: bool = True


@dataclass(frozen=True)
class _DimensionInkProbe:
    """Exact label and straight-stroke metadata before building helper geometry."""

    label_bbox: tuple[float, float, float, float]
    segments: tuple[tuple[tuple[float, float], tuple[float, float]], ...]
    box: tuple[float, float, float, float]
    _dw_spec: Any


def _subtract_probe_span(spans, lo, hi):
    """Remove one label keep-clear interval from dimension-line spans."""
    result = []
    for first, last in spans:
        if hi <= first or lo >= last:
            result.append((first, last))
        else:
            if first < lo:
                result.append((first, lo))
            if hi < last:
                result.append((hi, last))
    return [(first, last) for first, last in result if last - first > 1e-9]


def _dimension_probe_ink(p1, p2, side, distance, draft, label, label_offset_x):
    """Mirror inline, axis-aligned Dimension stroke clipping for the local ink solve."""
    dx, dy = float(p2[0] - p1[0]), float(p2[1] - p1[1])
    if (abs(dx) > 1e-9 and side not in ("above", "below")) or (
        abs(dy) > 1e-9 and side not in ("left", "right")
    ):
        return None
    analytical = dimension_candidate_geometry(
        p1, p2, side, distance, draft, label, label_offset_x=label_offset_x
    )
    if analytical is None:
        return None
    directions = {
        "above": (0.0, 1.0),
        "below": (0.0, -1.0),
        "left": (-1.0, 0.0),
        "right": (1.0, 0.0),
    }
    sx, sy = directions[side]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    axis = 1 if abs(dy) > abs(dx) else 0
    other = 1 - axis
    label_box = analytical.label_bbox
    half_along = (label_box[axis + 2] - label_box[axis]) / 2.0
    half_normal = (label_box[other + 2] - label_box[other]) / 2.0
    pad = draft.pad_around_text
    arrow = draft.arrow_length
    label_t = length / 2.0 + label_offset_x

    fits = 2 * half_along + 2 * arrow < length and length / 2.0 - half_along - pad > arrow / 2.0
    shaft = (
        [(arrow / 2.0, length - arrow / 2.0)]
        if fits
        else [
            (-2 * arrow, -arrow / 2.0),
            (0.0, length),
            (length + arrow / 2.0, length + 2 * arrow),
        ]
    )
    shaft = _subtract_probe_span(shaft, label_t - half_along - pad, label_t + half_along + pad)
    origin = (float(p1[0]) + sx * abs(distance), float(p1[1]) + sy * abs(distance))
    segments = [
        (
            (origin[0] + ux * first, origin[1] + uy * first),
            (origin[0] + ux * last, origin[1] + uy * last),
        )
        for first, last in shaft
    ]
    gap = draft.extension_gap
    for endpoint, point in ((0.0, p1), (length, p2)):
        witness = [(gap, abs(distance) + gap)]
        if abs(label_t - endpoint) < half_along + pad:
            witness = _subtract_probe_span(
                witness, abs(distance) - half_normal - pad, abs(distance) + half_normal + pad
            )
        segments.extend(
            (
                (float(point[0]) + sx * first, float(point[1]) + sy * first),
                (float(point[0]) + sx * last, float(point[1]) + sy * last),
            )
            for first, last in witness
        )
    # The older generic footprint can omit an uncrossed witness overshoot or a
    # short-span arrowhead. This private hull covers the exact stroke pieces,
    # label, both complete witness extents and all current arrowhead styles.
    xs = [label_box[0], label_box[2]]
    ys = [label_box[1], label_box[3]]
    for start, end in segments:
        xs.extend((start[0], end[0]))
        ys.extend((start[1], end[1]))
    head_half = arrow / 3.0
    for point in (p1, p2):
        cx, cy = float(point[0]) + sx * abs(distance), float(point[1]) + sy * abs(distance)
        wx, wy = (
            float(point[0]) + sx * (abs(distance) + gap),
            float(point[1]) + sy * (abs(distance) + gap),
        )
        xs.append(wx)
        ys.append(wy)
        for along in (-2.0 * arrow, 2.0 * arrow):
            xs.extend((cx + ux * along - uy * head_half, cx + ux * along + uy * head_half))
            ys.extend((cy + uy * along - ux * head_half, cy + uy * along + ux * head_half))
    stroke_pad = draft.line_width / 2.0
    box = (min(xs) - stroke_pad, min(ys) - stroke_pad, max(xs) + stroke_pad, max(ys) + stroke_pad)
    return label_box, tuple(segments), box


def dimension_candidate_geometry(
    p1,
    p2,
    side,
    distance,
    draft,
    label,
    *,
    label_offset_x=0.0,
):
    """Analytical label/line ink for an axis-aligned ``Dimension``.

    Interior retries use this during candidate exploration and construct OCC
    geometry only for the selected survivor.  The inputs mirror :func:`_dim`;
    diagonal dimensions fail closed because no current interior producer emits
    one and their rotated label polygon needs a stronger representation.
    """

    if isinstance(side, str):
        side = {
            "above": (0.0, 1.0, 0.0),
            "below": (0.0, -1.0, 0.0),
            "left": (-1.0, 0.0, 0.0),
            "right": (1.0, 0.0, 0.0),
        }[side]
    sx, sy = float(side[0]), float(side[1])
    dx, dy = float(p2[0] - p1[0]), float(p2[1] - p1[1])
    length = math.hypot(dx, dy)
    if length <= 1e-9 or (abs(dx) > 1e-9 and abs(dy) > 1e-9):
        return None
    ux, uy = dx / length, dy / length
    off = abs(float(distance))
    width, height = _text_size(
        label,
        draft.font_size,
        getattr(draft, "font_path", DEFAULT_FONT_PATH),
        getattr(draft, "font", "Arial"),
        getattr(draft, "font_style", FontStyle.REGULAR),
    )
    inline_aligned = (
        getattr(draft, "text_position", "inline"),
        getattr(draft, "text_orientation", "aligned"),
    ) == ("inline", "aligned")
    if inline_aligned:
        wx, wy = sx, sy
        cx = (p1[0] + p2[0]) / 2.0 + wx * off + ux * label_offset_x
        cy = (p1[1] + p2[1]) / 2.0 + wy * off + uy * label_offset_x
        hx, hy = (height / 2.0, width / 2.0) if abs(dy) > abs(dx) else (width / 2.0, height / 2.0)
        along = width / 2.0
    else:
        sign = 1 if uy * sx - ux * sy >= 0 else -1
        wx, wy = sign * uy, -sign * ux
        angle = math.atan2(uy, ux)
        aligned = (
            angle if -math.pi / 2 < angle <= math.pi / 2 else angle - math.copysign(math.pi, angle)
        )
        reading = aligned if draft.text_orientation == "aligned" else 0.0
        relative = reading - angle
        along = abs(math.cos(relative)) * width / 2 + abs(math.sin(relative)) * height / 2
        normal = abs(math.sin(relative)) * width / 2 + abs(math.cos(relative)) * height / 2
        cx = (p1[0] + p2[0]) / 2.0 + wx * off
        cy = (p1[1] + p2[1]) / 2.0 + wy * off
        if draft.text_position == "above":
            head_box = _dimension_head_bounds(draft.arrow_length, draft.head_type)
            head_height = max(abs(head_box[1]), abs(head_box[3]))
            text_offset = normal + draft.pad_around_text + max(head_height, draft.line_width / 2)
            cx -= math.sin(aligned) * text_offset
            cy += math.cos(aligned) * text_offset
        hx = abs(math.cos(reading)) * width / 2 + abs(math.sin(reading)) * height / 2
        hy = abs(math.sin(reading)) * width / 2 + abs(math.cos(reading)) * height / 2
    label_box = (cx - hx, cy - hy, cx + hx, cy + hy)

    first = (float(p1[0]) + wx * off, float(p1[1]) + wy * off)
    second = (float(p2[0]) + wx * off, float(p2[1]) + wy * off)
    arrow = draft.arrow_length
    fits = (
        2 * along + 2 * arrow < length and length / 2 - along - draft.pad_around_text > arrow / 2
    )
    if fits:
        line_start, line_end = first, second
    else:
        line_start = (first[0] - ux * 2 * arrow, first[1] - uy * 2 * arrow)
        line_end = (second[0] + ux * 2 * arrow, second[1] + uy * 2 * arrow)
    gap = draft.extension_gap
    segments = (
        (line_start, line_end),
        (
            (float(p1[0]) + wx * gap, float(p1[1]) + wy * gap),
            (float(p1[0]) + wx * (off + gap), float(p1[1]) + wy * (off + gap)),
        ),
        (
            (float(p2[0]) + wx * gap, float(p2[1]) + wy * gap),
            (float(p2[0]) + wx * (off + gap), float(p2[1]) + wy * (off + gap)),
        ),
    )
    box = dim_footprint(
        p1,
        p2,
        side,
        off,
        draft,
        label,
        label_offset_x=label_offset_x,
    )
    return AnalyticalDimensionInk(label_box, segments, box)


def _styled_dimension_footprint(p1, p2, side, distance, draft, text_size):
    """Conservative full-ink hull with the helper's resolved orientation and offset.

    The builder warms the fixed arrow envelope before placement; candidates use
    scalar arithmetic and cached typography. Unclipped witness extents are a
    conservative bound, followed by the existing actual-ink acceptance check.
    """
    width, height = text_size
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    sign = 1 if uy * side[0] - ux * side[1] >= 0 else -1
    wx, wy = sign * uy, -sign * ux
    off = abs(distance)
    ends = [(point[0] + wx * off, point[1] + wy * off) for point in (p1, p2)]
    angle = math.atan2(uy, ux)
    aligned = (
        angle if -math.pi / 2 < angle <= math.pi / 2 else angle - math.copysign(math.pi, angle)
    )
    reading = aligned if draft.text_orientation == "aligned" else 0.0
    relative = reading - angle
    along = abs(math.cos(relative)) * width / 2 + abs(math.sin(relative)) * height / 2
    normal = abs(math.sin(relative)) * width / 2 + abs(math.cos(relative)) * height / 2
    cx, cy = ((ends[0][index] + ends[1][index]) / 2 for index in (0, 1))
    head_box = _dimension_head_bounds(draft.arrow_length, draft.head_type)
    head_height = max(abs(head_box[1]), abs(head_box[3]))
    if draft.text_position == "above":
        offset = normal + draft.pad_around_text + max(head_height, draft.line_width / 2)
        cx -= math.sin(aligned) * offset
        cy += math.cos(aligned) * offset
    hx = abs(math.cos(reading)) * width / 2 + abs(math.sin(reading)) * height / 2
    hy = abs(math.sin(reading)) * width / 2 + abs(math.cos(reading)) * height / 2
    points = [(cx - hx, cy - hy), (cx + hx, cy + hy)]
    for point in (p1, p2):
        points.extend(
            (point[0] + wx * t, point[1] + wy * t)
            for t in (draft.extension_gap, off + draft.extension_gap)
        )
    fits = (
        2 * along + 2 * draft.arrow_length < length
        and length / 2 - along - draft.pad_around_text > draft.arrow_length / 2
    )
    # Local arrow heads extend behind their tip. Rotate the complete envelope:
    # separate tip/shaft and transverse extrema miss oblique body corners.
    for end, direction in zip(ends, (-1, 1) if fits else (1, -1), strict=True):
        points.extend(
            (
                end[0] + direction * (ux * x - uy * y),
                end[1] + direction * (uy * x + ux * y),
            )
            for x in (head_box[0], head_box[2])
            for y in (head_box[1], head_box[3])
        )
    if not fits:
        points.extend(
            (
                end[0] + ux * direction * 2 * draft.arrow_length,
                end[1] + uy * direction * 2 * draft.arrow_length,
            )
            for end, direction in zip(ends, (-1, 1), strict=True)
        )
    xs, ys = zip(*points, strict=True)
    pad = draft.line_width / 2
    return min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad
