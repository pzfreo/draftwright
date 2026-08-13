"""Shared low-level primitives for the draftwright drawing engine.

This module sits below the stage modules (``analysis``/``projection``/
``compose``/``annotations/``/``export``/``repair`` — its real consumers since
the ADR 0005 split): it holds the data structures and small helpers they all
depend on (the :class:`Analysis` namespace and its field types, the
dimension/format helpers, and the layout constants).  It imports only from the
leaf tier (:mod:`draftwright.layout`, :mod:`draftwright._geometry`,
:mod:`draftwright.fits`, :mod:`draftwright.fonts`) and third-party libraries —
never upward — so the module graph stays a DAG (machine-enforced by
``tests/test_import_boundaries.py``).
"""

from __future__ import annotations

import functools
import logging
import math
import re
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from draftwright.compose import StripDepths
    from draftwright.recognition import RecognitionResult, TurnedProfile

from build123d import Align, BoundBox, Compound, Edge, Location, Mode, Shape, Text, Vector
from build123d_drafting.helpers import (
    Dimension,
    TitleBlock,
    annotate,
    draft_preset,
    format_drawing_scale,
)

# The model-neutral geometry primitives (`_END_ON`, `_xyz`, `HoleRef`, `_axis_letter`)
# now live in the leaf `draftwright._geometry` so the IR waist (`model/`) can use them
# without importing this stage-level grab-bag (ADR 0008; #584 WP2). Re-exported here for
# the above-`_core` consumers (annotations/sheet/drawing/linting) that already import them.
from draftwright._geometry import _END_ON, HoleRef, _axis_letter, _fmt, _xyz  # noqa: F401
from draftwright.fits import FitClass
from draftwright.fonts import PLEX_MONO, PLEX_SANS_CONDENSED
from draftwright.layout import _greedy_strip_1d, _solve_strip_1d

_log = logging.getLogger(__name__)


def place_annotation(registry, items, obj, name=None, view=None, feature=None, measurement=None):
    """The annotation-placement primitive (#817): register *obj* under *name* — replacing any
    prior object of that name (dropped from the render list *items*) so a name maps to one
    object — append it to *items*, and record its owning *view* + source *feature* in *registry*.
    Shared by :meth:`Drawing._add` and :meth:`PlacementContext.place` so the render passes place
    annotations without reaching into the ``Drawing``. Returns *obj*.

    *measurement* is the `DimensionId` *obj* draws, where the caller holds one (#1002) —
    one axis finer than *feature*, since a feature has several measurements."""
    displaced = registry.named(name) if name is not None else None
    if displaced is not None:
        items.remove(displaced)
    annotate(obj, name)
    items.append(obj)
    registry.add(obj, name, view, feature, measurement)
    return obj


_MARGIN = 10.0

# When a sheet frame is drawn (#767), content reserves this extra band inside the border so
# it clears the drawn line rather than sitting on it. The frame draws AT _MARGIN (the old
# drawable boundary); content insets to _content_margin(frame).
_FRAME_BAND = 6.0


def _content_margin(frame: bool) -> float:
    """The effective content margin: ``_MARGIN``, plus the frame clearance band when a sheet
    frame is drawn (#767). Threaded into the layout authority so the reservation flows through
    scale/page selection (ADR 0004), not just the render."""
    return _MARGIN + (_FRAME_BAND if frame else 0.0)


# ISO 5457 zone-grid letters (vertical edges): A.. skipping I and O (confusable with 1 / 0).
_ZONE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"

# ISO 5457 reference-grid division counts (columns=numbers × rows=letters) per A-series page,
# keyed by the full (width, height) so a same-width custom page doesn't borrow the count.
_ZONE_DIVISIONS = {
    (297, 210): (6, 4),
    (420, 297): (8, 6),
    (594, 420): (12, 8),
    (841, 594): (16, 12),
    (1189, 841): (24, 16),
}


def _zone_divisions(page_w: float, page_h: float) -> tuple[int, int]:
    """``(columns, rows)`` for the ISO 5457 zone grid (#768) — the standard count for an
    A-series page (matched on BOTH dimensions), else ~50 mm zones for a custom page. Rows are
    clamped to the available letters (I/O skipped) so a very tall custom page can't over-index."""
    std = _ZONE_DIVISIONS.get((int(round(page_w)), int(round(page_h))))
    if std is not None:
        return std
    cols = max(2, round(page_w / 49.5))
    rows = min(len(_ZONE_LETTERS), max(2, round(page_h / 49.5)))
    return (cols, rows)


_TB_CLEAR = _MARGIN + 1.0  # title-block inset: one extra mm over _MARGIN for clearance

_FONT_SIZE = 3.0  # annotation text height (page-mm); the draft preset is built with this


_TB_H = 35.0


def _shape_box2d(shape):
    """``(x0, y0, x1, y1)`` page-plane bbox of a build123d shape, or ``None`` on
    failure — the one home of the bounding-box-read idiom (#700; the memoised /
    logging variants in ``linting.structural`` and ``annotations._common`` layer
    their policies over the same read)."""
    try:
        bb = shape.bounding_box()
        return (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y)
    except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
        return None


def _anno_box(o):
    """Page-space bbox ``(x0, y0, x1, y1)`` of an annotation — its text
    ``label_bbox`` if it has one, else its geometric bounding box; ``None`` if
    neither resolves (logged at debug: a silently un-bbox-able annotation drops
    out of overlap counts and measured footprints, #121). The one copy behind
    ``annotations._common`` and ``compose`` (#700)."""
    lb = getattr(o, "label_bbox", None)
    if lb is not None:
        return lb
    box = _shape_box2d(o)
    if box is None:
        _log.debug("annotation %r has no resolvable bbox", type(o).__name__)
    return box


def _wrap_rows(header, data, ncols):
    """Reshape *data* rows into *ncols* side-by-side blocks (a wider, shorter
    table), each block headed by *header* — so a long hole chart fits the page.
    Shared by the annotation pass and the compose-time footprint estimate (#700).
    """
    per = math.ceil(len(data) / ncols)
    blank = ("",) * len(header)
    wide = [tuple(header) * ncols]
    for r in range(per):
        row: tuple = ()
        for c in range(ncols):
            idx = c * per + r
            row += data[idx] if idx < len(data) else blank
        wide.append(row)
    return wide


def _table_metrics(rows, font_size, pad_around_text, block_cols=None):
    """The sizing model of a data table: per-column left/right edges (page-mm,
    block gaps inserted), total width/height, row height and effective block
    width, as ``(lefts, rights, total_w, total_h, row_h, bc)``.

    The ONE place table geometry is computed (#700): :func:`_build_table` (below,
    since #699) draws from it and ``compose._est_table_size`` estimates from it, so the
    ADR 0004 ``table_fits`` fitness check can never desynchronise from what
    renders (the drift ADR 0004 names as the failure mode to guard against).
    """
    fs = font_size
    pad = pad_around_text
    row_h = fs + 2 * pad
    ncol = len(rows[0])
    # Only treat as multi-block when block_cols evenly divides the row width.
    bc = block_cols if (block_cols and ncol % block_cols == 0 and block_cols < ncol) else ncol
    block_gap = 3 * pad  # whitespace between side-by-side blocks
    col_w = [
        max(max(_text_width(str(r[c]), fs) for r in rows) + 2 * pad, fs * 2.5) for c in range(ncol)
    ]
    # Per-column left/right edges, inserting block_gap before each new block.
    lefts, rights, cursor = [], [], 0.0
    for c in range(ncol):
        if c > 0 and c % bc == 0:
            cursor += block_gap
        lefts.append(cursor)
        cursor += col_w[c]
        rights.append(cursor)
    return lefts, rights, cursor, row_h * len(rows), row_h, bc


def _build_table(rows, draft, block_cols=None):
    """Build a generic data-table annotation at the origin (bottom-left ``(0, 0)``).

    *rows* is a list of equal-length string tuples; ``rows[0]`` is the header,
    drawn at the top. Returns a :class:`Compound` of grid rules + cell text,
    carrying ``.table_size = (w, h)`` so it can be positioned via
    :func:`fit_box`. Column widths are sized to their content via real glyph
    metrics (:func:`_text_width`). Generic — the hole table, and gear/BOM/
    revision tables, all build through here.

    When the rows are a wrapped chart of *block_cols*-wide side-by-side blocks
    (see :func:`_wrap_rows`), each block is drawn as its own bordered grid with
    a whitespace gap between them, so the blocks read as separate tables rather
    than one run-on grid.
    """
    fs = draft.font_size
    ncol = len(rows[0])
    # One sizing model, shared with compose's footprint estimate (#700).
    lefts, rights, total_w, total_h, row_h, bc = _table_metrics(
        rows, fs, draft.pad_around_text, block_cols
    )
    ys = [i * row_h for i in range(len(rows) + 1)]
    children = []
    for b in range(ncol // bc):  # one bordered grid per block
        cols = range(b * bc, b * bc + bc)
        bl, br = lefts[b * bc], rights[b * bc + bc - 1]
        for x in [lefts[c] for c in cols] + [br]:  # column rules + block edges
            children.append(Edge.make_line(Vector(x, 0, 0), Vector(x, total_h, 0)))
        for y in ys:  # horizontal rules stop at the block edge, not the gap
            children.append(Edge.make_line(Vector(bl, y, 0), Vector(br, y, 0)))
    for ri, row in enumerate(rows):  # rows[0] (header) sits at the top
        cy = total_h - (ri + 0.5) * row_h
        for ci, cell in enumerate(row):
            if not str(cell):
                continue
            cx = (lefts[ci] + rights[ci]) / 2
            text = Text(
                txt=str(cell),
                font_size=fs,
                font_path=PLEX_MONO,
                align=(Align.CENTER, Align.CENTER),
                mode=Mode.PRIVATE,
            ).locate(Location((cx, cy, 0)))
            children.extend(text.faces())
    table = Compound(children=children)
    table.table_size = (total_w, total_h)
    return table


def _tol_suffix(tolerance, draft) -> str:
    """The ``±`` / limit tolerance suffix to append to a **callout** label (a ø leader
    or a hole callout), matching byte-for-byte what ``Dimension(tolerance=…)`` renders
    on a linear dim (helpers ``_format_label``): a symmetric ``float`` → ``" ±t"``; an
    ``(lower, upper)`` pair → ``" +upper -lower"`` — all rounded to the draft precision.

    draftwright owns this suffix ONLY because the pinned helpers' ``Leader`` /
    ``HoleCallout`` take no ``tolerance=`` yet, so we bake it into the label string
    ourselves. Delete this once helpers grows a first-class tolerance parameter for
    those two (extraction tracked as #449) — ``Dimension`` formats its own (#28 / P2a).

    A resolved fit (:class:`~draftwright.fits.FitClass`, P2a.2) renders its own class-code
    or deviation suffix — it rides the same ``tolerance`` field as an aspect marker."""
    if tolerance is None:
        return ""
    if isinstance(tolerance, FitClass):
        return tolerance.suffix()
    prec = draft.decimal_precision
    if isinstance(tolerance, (int, float)):
        return f" ±{round(tolerance, prec):.{prec}f}"
    lo, hi = tolerance
    return f" +{round(hi, prec):.{prec}f} -{round(lo, prec):.{prec}f}"


def _tag_sequence(n):
    """``A, B, …, Z, AA, AB, …`` — deterministic hole-table tags for *n* rows."""
    tags = []
    for i in range(n):
        s, k = "", i
        while True:
            s = chr(ord("A") + k % 26) + s
            k = k // 26 - 1
            if k < 0:
                break
        tags.append(s)
    return tags


_DIAM_RE = re.compile(r"[øØ⌀]\s*(\d+(?:\.\d+)?)")


def _annotation_diameter_sources(annotation) -> tuple[tuple[float, ...], tuple[float, ...]]:
    """Return ``(structured, text)`` diameter mentions for one annotation.

    Engine-produced annotations with ``covers_diameters`` describe their physical-feature
    correspondence structurally.  Their formatted labels may contain other diameters (for
    example ``ø60 BC``), so text parsing is a legacy/unstructured fallback and is never
    combined with structured coverage (ADR 0017 / #1142).
    """
    structured = tuple(float(value) for value in getattr(annotation, "covers_diameters", ()))
    if structured:
        return structured, ()
    label = getattr(annotation, "label", None) or ""
    return (), tuple(float(match.group(1)) for match in _DIAM_RE.finditer(label))


# A single-quoted label lifted from a lint message, e.g. "labels 'A' and 'B' …".
# Shared by the #29 lint suggestions (linting.py) and the #30 repair loop.
_QUOTED_RE = re.compile(r"'([^']*)'")


@functools.lru_cache(maxsize=512)
def _text_size(
    text: str, font_size: float, font_path: str | None = PLEX_MONO, font: str = "Arial"
) -> tuple[float, float]:
    """Measured rendered (width, height) (page-mm) of *text* at *font_size*.

    Uses build123d's ``Text`` — the same primitive ``Dimension``/``HoleCallout``
    stroke their labels with — so callout-width estimates use real glyph metrics
    instead of a character-count fudge (#31).  Pinned to a vendored font **file**
    (``font_path``), not a system font *name*: name resolution substitutes a
    different font on Linux, which makes this estimate — and the layout it feeds —
    platform-variant (#149). The default is the same face the annotations render
    with, so estimate and render agree.  *font* only matters when a caller opts out
    of path-pinning (``font_path=None``) — then the *name* resolves through the OS
    font stack, exactly as the renderer would.  Cached because the same numeric
    labels recur across holes and the rasterisation is the costly part.
    """
    if not text:
        return (0.0, 0.0)
    bb = Text(
        txt=text,
        font_size=font_size,
        font=font,
        font_path=font_path,
        align=(Align.CENTER, Align.CENTER),
        mode=Mode.PRIVATE,
    ).bounding_box()
    return (bb.size.X, bb.size.Y)


def _text_width(text: str, font_size: float, font_path: str = PLEX_MONO) -> float:
    """Measured rendered width (page-mm) of *text* — see :func:`_text_size`."""
    return _text_size(text, font_size, font_path)[0]


# Cheap mean glyph advance as a fraction of the em (font size), for label-width ESTIMATES in
# the diameter row/column capacity checks. Plex Mono is monospaced at ~0.6 em, padded a touch.
# The exact metric is `_text_width()` above (measured per string), which the corridor/label
# placers use; these capacity gates only need a close bound, not the metric, so they trade
# exactness for not rasterising a text layout per candidate. The two may diverge for unusually
# wide/narrow glyph runs.
_EST_CHAR_WIDTH_EM = 0.62


_CONCENTRIC_TOL_MM = 0.5


def _first_free_index(prefix: str, taken) -> int:
    """The lowest ``j`` for which ``f"{prefix}{j}"`` is not in *taken* (a set or any container
    supporting ``in``). The shared kernel of the first-free annotation-name allocators
    (``_loc_name``/``_uniq``/``_hc_name``) — used where reusing a freed gap is fine. NOTE: the
    step-length / diameter runs deliberately use ``max+1`` instead (``_next_start`` /
    ``_next_steplen_start``), because a contiguous run started at a gap below an occupied index
    would wrap onto it (#432); don't fold those onto this."""
    j = 0
    while f"{prefix}{j}" in taken:
        j += 1
    return j


def _concentric_with_axis(a, x: float, y: float) -> bool:
    """True when the page/world point ``(x, y)`` lies on the rotational part's turned axis
    (within :data:`_CONCENTRIC_TOL_MM`). A bore/pattern centred on the axis needs no location
    dim — its position is the axis — so several passes filter such refs; this is the single
    radial test they share (the perpendicular-plane distance to the axis centre ``(a.cx,
    a.cy)``). Callers still gate on ``a.is_rotational`` / role / axis as their context needs."""
    return math.hypot(x - a.cx, y - a.cy) <= _CONCENTRIC_TOL_MM


def _dim(p1, p2, side, distance, draft, **kwargs):
    """Build a :class:`Dimension`, tagged with its placement spec.

    Identical to constructing ``Dimension`` directly, but records ``p1``,
    ``p2``, ``side``, ``distance`` and the label kwargs on the result as
    ``_dw_spec`` so the #30 repair loop can re-place the dimension (flip the
    side, widen the offset) without re-deriving any geometry. Only dimensions
    built this way are re-placeable by :meth:`Drawing.repair`.
    """
    d = Dimension(p1, p2, side, distance, draft, **kwargs)
    d._dw_spec = SimpleNamespace(
        p1=p1, p2=p2, side=side, distance=abs(distance), draft=draft, kwargs=kwargs
    )
    return d


# Dimension-line spacing (page-mm, scale-independent), the single source of truth for
# BOTH the ADR 0009 strip carve (via the `Strip` dataclass defaults below) and the
# compose.py halo/depth estimates that must reserve the same space. Per ISO 129-1 / ASME
# Y14.5, the FIRST dimension line sits furthest from the outline (clears the outline +
# extension-line origins) and subsequent parallel lines stack tighter and uniform (#347).
_STRIP_GAP = 10.0  # clearance between the view outline and the first dimension line
_STRIP_SPACING = 2.5  # clear gap between successive parallel dimension lines (beyond the label)
_BALLOON_RADIUS_FONT_FACTOR = 1.5


def _balloon_radius(font_size: float = _FONT_SIZE) -> float:
    """Balloon glyph radius in page-mm for the active annotation font."""

    return _BALLOON_RADIUS_FONT_FACTOR * font_size


def _balloon_halo(font_size: float = _FONT_SIZE) -> float:
    """Reserved/rendered plan-balloon extent from the view edge, in page-mm."""

    return _STRIP_GAP + 2 * _balloon_radius(font_size) + _STRIP_SPACING


# Small lift (page-mm) of an overall/envelope witness line off the projected silhouette edge,
# so its extension line reads as distinct from the outline rather than sitting on top of it.
_WITNESS_LIFT_MM = 2.0


@dataclass
class Strip:
    """A one-dimensional annotation band adjacent to an orthographic view.

    A plain geometry record: the collect-then-solve placers (ADR 0009) read its
    bounds (:func:`~draftwright.annotations._common.strip_free_span`) and carve
    around the placed annotations. The mutable ``allocate``/``peek`` cursor was
    retired once every placer moved to the carve (#150).

    Attributes:
        anchor:      Page coordinate of the view edge this strip starts from.
        outer_limit: Page coordinate at which the strip ends (page margin,
                     neighbouring view, or title-block boundary).
        direction:   ``+1`` — stacks away from anchor (right/above);
                     ``-1`` — stacks back toward smaller coords (left/below).
        gap:         Clearance between the view edge and the first annotation.
        spacing:     Clearance between successive annotations.
    """

    anchor: float
    outer_limit: float
    direction: float = 1.0
    gap: float = _STRIP_GAP
    spacing: float = _STRIP_SPACING

    @property
    def available(self) -> float:
        """Total space available in this strip (mm)."""
        return abs(self.outer_limit - self.anchor)


@dataclass
class ViewZones:
    """The four annotation strips surrounding one orthographic view.

    The ``right``/``above``/``below`` strips are always present for the three
    orthographic views; only ``left`` can be ``None`` (a side view's left strip
    abuts the front view, so it has no usable space).
    """

    right: Strip
    above: Strip
    below: Strip
    left: Strip | None = None


_PAD = draft_preset(font_size=_FONT_SIZE, decimal_precision=1).pad_around_text


_SLOT_DIM_WIDTH = 2 * _FONT_SIZE + _PAD  # pv_zones.below: overall width dimension


_SLOT_DIM_DEPTH = 2 * _FONT_SIZE + _PAD  # sv_zones.below: overall depth dimension


_SLOT_DIM_HEIGHT = 2 * _FONT_SIZE + 2 * _PAD  # fv_zones.right: overall height dim


_MIN_VIEW_MM = (
    10.0  # legibility floor: the projected size below which an *explicit* scale earns a warning.
    # It is NOT a bound on the auto scale (choose_scale is a pure geometric page fit) and does NOT
    # gate which annotations exist (step/location legibility use _MIN_STEP_*/_MIN_LOC_SEP_MM). Its
    # only use is the explicit-scale advisory in analysis.py: below it a user scale is honoured
    # with a warning, not rejected (#489).
)


# Hard geometry floor: below this projected size OCCT's annotation arcs collapse
# (Geom_TrimmedCurve U1==U2), which happens near 1e-4 mm empirically — 0.1 mm is a conservative
# floor far above that and far below any real drawing. An explicit scale under it is rejected with
# a clean message rather than a cryptic OCP error (#489).
_MIN_RENDER_MM = 0.1


_SLOT_DIM_STEP = 4 * _FONT_SIZE + _PAD  # fv_zones.right: step-height dimension


_TABULATE_MIN_HOLES = 16


_MIN_STEP_DIM_MM = (
    _FONT_SIZE
    + 2 * draft_preset(font_size=_FONT_SIZE, decimal_precision=1).arrow_length
    + 2 * draft_preset(font_size=_FONT_SIZE, decimal_precision=1).pad_around_text
)


_MIN_STEP_SEP_MM = _FONT_SIZE + 2 * _PAD


_MIN_LOC_SEP_MM = draft_preset(font_size=_FONT_SIZE, decimal_precision=1).arrow_length + _PAD


def _legible_steps(step_zs, bb_min_z, scale, *, allow_short=False):
    """Step heights worth dimensioning at *scale*, and how many were too close.

    A step is dimensioned when it can carry a label, unless ``allow_short`` is
    true, and is at least ``_MIN_STEP_SEP_MM`` (page-mm)
    above the previously kept step — consecutive shoulders closer than that are
    page-coincident and cannot be told apart (#41). A short first rise is still
    dimensionable: ``DimensionLine`` moves its arrows/text outside the witnesses
    when they do not fit inline (#565). Returns ``(kept_zs, n_too_close)``:
    the heights to dimension, and the count dropped for spacing (the caller
    surfaces these via lint; the full-fidelity answer is a detail view, #42).
    """
    kept: list[float] = []
    n_too_close = 0
    last = None
    for z in sorted(step_zs):
        if not allow_short and (z - bb_min_z) * scale < _MIN_STEP_DIM_MM:
            continue
        if last is not None and (z - last) * scale < _MIN_STEP_SEP_MM:
            n_too_close += 1
            continue
        kept.append(z)
        last = z
    return kept, n_too_close


def _legible_locations(positions, scale):
    """Axis positions far enough apart on the page to dimension legibly.

    Given world-coordinate *positions* along one axis, keep a position only if it
    is at least ``_MIN_LOC_SEP_MM`` page-mm from the previously kept one;
    consecutive holes closer than that produce baseline witness lines that read
    as a single busy cluster (#43). Returns ``(kept, n_too_close)``: the positions
    to dimension and the count dropped for spacing (the caller surfaces these via
    ``location_ref_dropped`` lint; the full-fidelity answer is a detail view, #42).
    Mirrors :func:`_legible_steps` for hole locations.
    """
    kept: list[float] = []
    n_too_close = 0
    last = None
    for p in sorted(positions):
        if last is not None and (p - last) * scale < _MIN_LOC_SEP_MM:
            n_too_close += 1
            continue
        kept.append(p)
        last = p
    return kept, n_too_close


class _FreeRunIndex:
    """Range-covered Y coordinates with the longest remaining free run.

    Coordinate points are represented as zero-length cells between the ordinary
    intervals.  That lets a degenerate obstacle split a gap exactly as the open
    AABB overlap predicate does, without assigning the point any physical width.
    """

    def __init__(self, coordinates):
        self.coordinates = coordinates
        lengths = []
        for i, value in enumerate(coordinates):
            lengths.append(0.0)
            if i + 1 < len(coordinates):
                lengths.append(coordinates[i + 1] - value)
        self._lengths = lengths
        self._n = len(lengths)
        size = 4 * self._n
        self._cover = [0] * size
        self._total = [0.0] * size
        self._prefix = [0.0] * size
        self._suffix = [0.0] * size
        self._longest = [0.0] * size
        self._all_free = [True] * size
        self._build(1, 0, self._n)

    def _build(self, node, lo, hi):
        if hi - lo == 1:
            length = self._lengths[lo]
            self._total[node] = length
            self._prefix[node] = length
            self._suffix[node] = length
            self._longest[node] = length
            return
        mid = (lo + hi) // 2
        self._build(node * 2, lo, mid)
        self._build(node * 2 + 1, mid, hi)
        self._pull(node)

    def _pull(self, node):
        if self._cover[node]:
            self._all_free[node] = False
            self._prefix[node] = self._suffix[node] = self._longest[node] = 0.0
            return
        left, right = node * 2, node * 2 + 1
        self._total[node] = self._total[left] + self._total[right]
        self._all_free[node] = self._all_free[left] and self._all_free[right]
        self._prefix[node] = self._prefix[left]
        if self._all_free[left]:
            self._prefix[node] += self._prefix[right]
        self._suffix[node] = self._suffix[right]
        if self._all_free[right]:
            self._suffix[node] += self._suffix[left]
        self._longest[node] = max(
            self._longest[left],
            self._suffix[left] + self._prefix[right],
            self._longest[right],
        )

    @property
    def longest(self):
        return self._longest[1]

    def block(self, lo, hi):
        """Add one obstacle interval in coordinate space."""
        first = bisect_left(self.coordinates, lo)
        last = bisect_left(self.coordinates, hi)
        if lo == hi:
            unit_lo, unit_hi = 2 * first, 2 * first + 1
        else:
            # Block the open span but not its endpoints: touching an obstacle is
            # permitted by the placement AABB predicate.
            unit_lo, unit_hi = 2 * first + 1, 2 * last
        self._block(1, 0, self._n, unit_lo, unit_hi)

    def _block(self, node, lo, hi, block_lo, block_hi):
        if block_lo <= lo and hi <= block_hi:
            self._cover[node] += 1
            self._pull(node)
            return
        mid = (lo + hi) // 2
        if block_lo < mid:
            self._block(node * 2, lo, mid, block_lo, block_hi)
        if mid < block_hi:
            self._block(node * 2 + 1, mid, hi, block_lo, block_hi)
        self._pull(node)

    def first_run(self, minimum):
        """Lowest-Y free run at least *minimum* long."""
        return self._first_run(1, 0, self._n, self.coordinates[0], minimum)

    def _first_run(self, node, lo, hi, origin, minimum):
        if hi - lo == 1:
            return origin
        left, right = node * 2, node * 2 + 1
        mid = (lo + hi) // 2
        if self._longest[left] >= minimum:
            return self._first_run(left, lo, mid, origin, minimum)
        if self._suffix[left] + self._prefix[right] >= minimum:
            return origin + self._total[left] - self._suffix[left]
        return self._first_run(
            right,
            mid,
            hi,
            origin + self._total[left],
            minimum,
        )


def _largest_empty_rect(drawable, obstacles, *, target_size=None, warn: bool = True):
    """Largest axis-aligned empty rectangle in *drawable* avoiding *obstacles*.

    *drawable* and each obstacle are ``(x0, y0, x1, y1)`` page-mm boxes.  Returns
    the empty sub-rectangle of *drawable* (overlapping no obstacle) that maximises
    the uniform scale of *target_size*. The default ``(1, 1)`` maximises the side
    of the largest square it can hold, preserving the iso-view policy. A caller
    placing a wide or tall fixed-aspect footprint can supply its minimum
    ``(width, height)`` instead.

    Every maximal empty rectangle has edges drawn from the drawable bounds and
    the obstacle bounds.  Sweep candidate X strips while adding their blocking Y
    intervals to a compressed-coordinate free-run index.  This is equivalent to
    enumerating every pair of Y edges, without the quartic blow-up when a detail
    view includes hundreds of decomposed annotation segments (#1065).
    """
    target_w, target_h = target_size or (1.0, 1.0)
    if not (math.isfinite(target_w) and target_w > 0 and math.isfinite(target_h) and target_h > 0):
        raise ValueError("target_size must contain finite positive dimensions")

    dx0, dy0, dx1, dy1 = drawable
    xs = sorted({dx0, dx1, *(c for o in obstacles for c in (o[0], o[2]) if dx0 < c < dx1)})
    ys = sorted({dy0, dy1, *(c for o in obstacles for c in (o[1], o[3]) if dy0 < c < dy1)})

    events = []
    for ox0, oy0, ox1, oy1 in obstacles:
        block_lo = max(dy0, oy0)
        block_hi = min(dy1, oy1)
        if block_hi < dy0 or block_lo > dy1:
            continue
        events.append((ox0, ox1, block_lo, block_hi))
    events.sort(key=lambda event: event[0])

    # The score is min(width / target_w, height / target_h), so any candidate whose
    # width or height cannot exceed the corresponding scaled target cannot beat the
    # best found so far. This is the same exact prune as the square-default path,
    # normalized by the requested footprint.
    best = None
    best_score = 0.0
    nx = len(xs)
    for i in range(nx - 1):
        rx0 = xs[i]
        if xs[-1] - rx0 <= best_score * target_w:
            break  # widest strip from here on can't beat best (rx0 only grows)
        free_y = _FreeRunIndex(ys)
        event_index = 0
        for j in range(bisect_right(xs, rx0 + best_score * target_w), nx):
            rx1 = xs[j]
            width = rx1 - rx0
            while event_index < len(events) and events[event_index][0] < rx1:
                _, ox1, block_lo, block_hi = events[event_index]
                if rx0 < ox1:
                    free_y.block(block_lo, block_hi)
                event_index += 1

            height = free_y.longest
            score = min(width / target_w, height / target_h)
            if score <= best_score:
                continue

            # If width caps the score, select the first global Y edge that makes
            # the target fit.  Otherwise select the end of the first tallest gap.
            # These are the same deterministic winners as the exhaustive search.
            needed_height = min(width * target_h / target_w, height)
            ry0 = free_y.first_run(needed_height)
            edge = bisect_left(ys, ry0 + needed_height)
            ry1 = ys[min(edge, len(ys) - 1)]
            best_score = score
            best = (rx0, ry0, rx1, ry1)
    if best is None:
        # No empty rectangle exists (obstacles cover the drawable area). This
        # is unreachable in practice — choose_scale always leaves a gap — but
        # if it ever happens the iso would render over the other views, so flag
        # it rather than fail silently.
        if warn:
            _log.warning(
                "No empty rectangle found for the iso view; obstacles fill the "
                "drawable area — iso may overlap other views"
            )
        return drawable
    return best


@dataclass
class DetailRequest:
    """A renderer's request for an enlarged detail of a region it could not draw
    legibly at sheet scale (#307). Renderers append these to the run's
    ``PlacementContext.detail_requests``
    instead of building bespoke detail views; ``_resolve_details`` resolves them all
    through one generic detailer (crop → project → place → caption → marker), then
    calls ``redraw`` to draw the feature's own dims inside the placed detail view.

    The single ``detect → request → generic render`` path that folds the prismatic
    step detail (#42) and the turned-head detail (#304) into one, mirroring the
    section pipeline (``plan_sections``/``SectionPlan``).

    Fields:
        axis:         part axis the band spans / is cropped along ("x"/"y"/"z").
        lo, hi:       band bounds along ``axis`` (world mm).
        scale_needed: detail world→page scale that makes the region legible.
        redraw:       ``redraw(dwg, view_name, coords, detail_scale) -> int`` — draws the
                      detail's dimensions against *coords* (the detail's
                      :class:`ViewCoordinates`, mapped via ``coords.pp``) and returns the count
                      placed (0 → the detailer drops the detail without committing a view, #840).
                      Called on the SCRATCH-projected coords before the view is committed, so a
                      zero result needs no rollback; the main view always carries the located
                      head/block inline regardless, so a placement failure loses no coverage
                      (lint reports the un-located interior instead).
        pad_top:      page-mm band reserved above the detail view (a horizontal
                      chain); reserved in the fit + placement.
        pads:         optional ``pads(detail_scale) -> (pad_right, pad_top)`` for a
                      footprint that depends on the chosen scale (the prismatic
                      ladder reserves one rung per *legible-at-that-scale* step, so it
                      shrinks with the scale during the fit). Overrides ``pad_top``.
        source_view:  orthographic direction to preserve in the detail (``"front"``
                      or ``"side"``). The latter is used for Y-turned axial profiles.
        crop_lo/crop_hi: optional crop bounds along ``axis`` when the projected
                      detail must include context outside the marked feature band.
                      Prismatic absolute-height details use this to retain their
                      base datum without enlarging the source-view marker.
        cross_axis/cross_lo/cross_hi: optional second crop band, used to isolate a
                      narrow profile strip when enlarging the full radial extent
                      would not fit on the sheet.
        kind:         short label for logging.
    """

    axis: str
    lo: float
    hi: float
    scale_needed: float
    redraw: Callable[..., int]
    pad_top: float = 0.0
    pads: Callable[[float], tuple[float, float]] | None = None
    source_view: Literal["front", "side"] = "front"
    crop_lo: float | None = None
    crop_hi: float | None = None
    cross_axis: Literal["x", "y", "z"] | None = None
    cross_lo: float | None = None
    cross_hi: float | None = None
    kind: str = "detail"


@dataclass(frozen=True)
class _Projector:
    """Model → page coordinate projection for the orthographic views.

    Each in-plane view axis projects as ``origin + (value - centroid) * scale``.
    Built once in :func:`_analyse` and hung off the analysis namespace as
    ``a.proj`` so the annotation passes share one projector instead of each
    re-deriving the ``FX``/``FZ``/``SX``/``SZ``/``PX``/``PY`` closures.

    This deliberately mirrors those analysis-phase closures byte-for-byte (an
    unsigned ``+1`` projection), so the consolidation is provably
    behaviour-preserving. The helpers library's ``ViewCoordinates.px``/``.py``
    (already built per view as ``dwg._coords``) computes a *signed* projection
    from ``view_axes()``; routing through it would couple the annotation passes
    to render-order ``_coords`` population and could change output where a view
    axis projects with a negative sign. Unifying onto ``ViewCoordinates`` is
    therefore tracked as separate follow-up work, not part of this dedup.

    Convention at call sites: bind a short local alias (``FX = a.proj.front_x``)
    when a function projects repeatedly through its body; call ``a.proj.*()``
    directly for one-off projections.
    """

    fv_x: float
    fv_y: float
    sv_x: float
    sv_y: float
    pv_x: float
    pv_y: float
    cx: float
    cy: float
    cz: float
    scale: float

    def front_x(self, x: float) -> float:
        return self.fv_x + (x - self.cx) * self.scale

    def front_z(self, z: float) -> float:
        return self.fv_y + (z - self.cz) * self.scale

    def side_x(self, y: float) -> float:
        return self.sv_x + (y - self.cy) * self.scale

    def side_z(self, z: float) -> float:
        return self.sv_y + (z - self.cz) * self.scale

    def plan_x(self, x: float) -> float:
        return self.pv_x + (x - self.cx) * self.scale

    def plan_y(self, y: float) -> float:
        return self.pv_y + (y - self.cy) * self.scale


@dataclass(frozen=True)
class LayoutFrame:
    """The page geometry a **dimensional** renderer may use — and nothing more.

    ADR 0016's boundary rule splits a renderer's job in two: the compiled plan says WHAT is
    drawn, this says where there is room to draw it. So a dimensional renderer takes a
    `LayoutFrame` instead of the full :class:`Analysis`, which carries the feature
    inventory, the part, and the raw bounding box — everything needed to reconstruct a
    dimension the compiler decided not to approve, which is precisely what eight review
    rounds on #921 found renderers doing.

    What is deliberately absent: ``part``, ``bb``, the recognised-feature lists, the sizes.
    A renderer that needs a part-space coordinate gets it from its approved entry's
    ``span``, and projects it with :meth:`project`. View EDGES are page-space rectangles
    here rather than projected bbox extents, so an anchor like "just right of the front
    view" needs no part geometry at all — the form that let the height ladder read
    ``a.bb.max.Z`` for the overall height while claiming to be doing layout.

    ``scale`` remains because legibility is a genuine placement decision: whether two rungs
    are too close to dimension depends on the page, not the model.
    """

    proj: _Projector
    scale: float
    front: tuple[float, float, float, float]  # page-space (left, right, bottom, top)
    plan: tuple[float, float, float, float]
    side: tuple[float, float, float, float]
    fv_zones: ViewZones
    pv_zones: ViewZones
    sv_zones: ViewZones

    def project(self, view: str, point) -> tuple[float, float]:
        """A part-space *point* in *view*'s page coordinates."""
        x, y, z = point
        if view == "front":
            return self.proj.front_x(x), self.proj.front_z(z)
        if view == "side":
            return self.proj.side_x(y), self.proj.side_z(z)
        if view == "plan":
            return self.proj.plan_x(x), self.proj.plan_y(y)
        raise ValueError(f"unknown view {view!r}")

    def edges(self, view: str) -> tuple[float, float, float, float]:
        """*view*'s ordered page-space ``(left, right, bottom, top)``.

        The stored endpoints come from projected world minima/maxima, whose handedness
        need not match page left/right. Normalise here so callers can rely on the names
        this API exposes rather than knowing projector orientation."""
        x0, x1, y0, y1 = {"front": self.front, "plan": self.plan, "side": self.side}[view]
        return min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)

    def zones(self, view: str) -> ViewZones:
        return {"front": self.fv_zones, "plan": self.pv_zones, "side": self.sv_zones}[view]


def layout_frame(a: Analysis) -> LayoutFrame:
    """The :class:`LayoutFrame` view of an :class:`Analysis`.

    An adapter rather than a replacement: `_analyse` keeps producing the full `Analysis`
    (recognition, sizing and the compose stage all need it), and each renderer narrows to
    this as it migrates. When none take `Analysis` for dimensional work, the narrowing is
    complete and this is the only door.
    """
    return LayoutFrame(
        proj=a.proj,
        scale=a.SCALE,
        front=(
            a.proj.front_x(a.bb.min.X),
            a.proj.front_x(a.bb.max.X),
            a.proj.front_z(a.bb.min.Z),
            a.proj.front_z(a.bb.max.Z),
        ),
        plan=(
            a.proj.plan_x(a.bb.min.X),
            a.proj.plan_x(a.bb.max.X),
            a.proj.plan_y(a.bb.min.Y),
            a.proj.plan_y(a.bb.max.Y),
        ),
        side=(
            a.proj.side_x(a.bb.min.Y),
            a.proj.side_x(a.bb.max.Y),
            a.proj.side_z(a.bb.min.Z),
            a.proj.side_z(a.bb.max.Z),
        ),
        fv_zones=a.fv_zones,
        pv_zones=a.pv_zones,
        sv_zones=a.sv_zones,
    )


@dataclass(frozen=True)
class Analysis:
    """Typed geometry+layout analysis produced by :func:`_analyse`.

    The single data structure threaded through the whole annotation layer
    (exposed as ``dwg._analysis`` and passed to the passes as ``a``). It was a
    ``SimpleNamespace`` — invisible to mypy; making it a frozen dataclass type-
    checks every ``a.<field>`` access and documents the contract (#98).

    Page-coordinate fields (``FV_X`` … ``SV_Y``, ``ISO_X``/``ISO_Y``, the
    ``*_limit`` and half-extent fields) are in page mm; ``cx``/``cy``/``cz`` and
    the size fields are world mm; ``SCALE`` is the page-per-world factor.
    """

    part: Shape
    #: The ADR 0017 aggregate, or ``None`` on a DECLARED build — which recognises nothing
    #: (ADR 0011 / #1022).  ``None`` means "not detected", never "detected and empty": a
    #: consumer needing an inventory on that path must go through the lazy
    #: ``Drawing._recognition()``, which builds one on demand rather than reading an absence
    #: as an answer.
    recognition: RecognitionResult | None
    bb: BoundBox
    x_size: float
    y_size: float
    z_size: float
    cx: float
    cy: float
    cz: float
    bbox_max: float
    holes: list
    patterns: list
    bosses: list  # external bosses (recognise_bosses), detected once — the one inventory (#244)
    slots: list
    pockets: list
    pocket_patterns: list
    pads: list  # geometry-derived rectangular-pad coverage inventory (#885)
    z_diams: list[float]
    cross_diams: list[float]
    cyls: tuple[tuple, tuple]
    prof: TurnedProfile | None  # turned step profile (recognise_turned_steps), detected once
    od_diam: float | None
    is_rotational: bool
    od_axis: str  # rotation/turning axis of a rotational part ("z" default; "x"/"y" #222)
    step_zs: list[float]
    layout_strips: StripDepths
    layout_n_steps: int
    layout_section: bool
    layout_table_sizes: tuple[tuple[float, float], ...]
    sv_right: float
    iso_right_limit: float
    SCALE: float
    PAGE_W: float
    PAGE_H: float
    TB_W: float
    DIM_PAD: float
    margin: float
    x_offset: float
    FV_X: float
    FV_Y: float
    PV_X: float
    PV_Y: float
    SV_X: float
    SV_Y: float
    proj: _Projector
    ISO_X: float
    ISO_Y: float
    iso_left_limit: float
    iso_bottom_limit: float
    iso_top_limit: float
    fv_hw: float
    fv_hh: float
    pv_hh: float
    sv_hw: float
    fv_zones: ViewZones
    pv_zones: ViewZones
    sv_zones: ViewZones
    step_file: str | Path | Shape
    title: str
    number: str
    tolerance: str
    drawn_by: str
    out: str
    # Canonical AP242 source census + extraction outcomes for a STEP input, including off mode
    # (which inventories but does not lower records); None for an in-memory Shape. Typed as
    # object to keep the rank-1 core from importing rank-2 pmi; the compatibility `pmi` property
    # below is its records projection.
    pmi_report: object | None
    pmi_mode: str
    # True when the caller omitted `pmi` and the public default selected off. Kept separate
    # from the effective mode so the ignored-PMI diagnostic can distinguish that default from
    # an explicit opt-out without widening the three-mode renderer contract (#623).
    pmi_defaulted: bool
    # Standing ISO 7200 title-block fields (#766) — defaulted, so they sit after the
    # non-default fields above. Defaults preserve the prior output: revision "A", the rest
    # blank (the TitleBlock helper's own defaults).
    material: str = ""
    date: str = ""
    revision: str = "A"
    company: str = ""
    # Draw a sheet border/frame (#767). When True, `margin` is already the reserved content
    # margin (`_content_margin(True)`), so content clears the frame drawn at `_MARGIN`.
    frame: bool = False
    # Projection-method symbol (#769): "third" / "first" (ISO 5456-2) or None (omit).
    projection: str | None = None
    # Draw the ISO 5457 zone-grid border ruler (#768). Implies a frame (the ticks sit on it).
    zones: bool = False
    # The PartModel built by _analyse's pre-scale sizing pass (#584 WP1 A) — stored so
    # the render path reuses it instead of re-running the detectors (ADR 0008 Amdt 5:
    # one inventory, detected once; #602). Typed `object` to keep _core free of a
    # runtime model/ import (model/ sits BELOW _core in _LAYERS, so a typed
    # `PartModel | None` is legal — a possible tightening). None when the caller
    # declared a model (ADR 0011) or on a manually-built Analysis — consumers fall
    # back to build_model(a).
    model: object | None = None

    @property
    def pmi(self) -> list:
        """Successful PMI records, derived from the canonical extraction report."""
        return list(getattr(self.pmi_report, "records", ()))


_greedy_strip_ys = _greedy_strip_1d


_solve_strip_ys = _solve_strip_1d


_DRAFTWRIGHT_URL = "https://github.com/pzfreo/draftwright"


def _attribution_author(drawn_by: str | None) -> str:
    """ISO 7200 "drawn by" value: the human author and draftwright, or just
    draftwright when no author was supplied."""
    author = (drawn_by or "").strip()
    return f"{author} / draftwright" if author else "draftwright"


def _make_title_block(dwg, a: Analysis):
    """Construct + page-locate the title block, returning ``(tb, cell)`` where *cell* is its
    drawn-by cell bbox (for the hyperlink rect). Shared by :func:`_add_title_block` (which adds
    it, last) and :func:`_title_block_box` (which measures its footprint for GD&T avoidance, #481)
    so the two never drift."""
    tb = TitleBlock(
        a.title,
        a.number,
        scale=format_drawing_scale(a.SCALE),
        general_tolerance=a.tolerance,
        designed_by=_attribution_author(a.drawn_by),
        material=a.material,
        date=a.date,
        revision=a.revision,
        legal_owner=a.company,
        width=a.TB_W,
        # Title block renders in condensed sans (the tight ISO 7200 cells), a
        # different face from the monospace dimensions — so it carries its own
        # pinned-font draft rather than reusing dwg.draft (#149).
        draft=draft_preset(
            font_size=dwg.draft.font_size,
            decimal_precision=dwg.draft.decimal_precision,
            font_path=PLEX_SANS_CONDENSED,
        ),
    )
    # Drawn-by cell geometry, from the block's own public cell bbox (#139) rather
    # than hardcoded column fractions, so the hyperlink rect tracks any upstream
    # TitleBlock layout change. Build-frame bbox; translated to page space below.
    cell = tb.drawn_by_cell_bbox()
    tb = tb.locate(Location((a.PAGE_W - a.TB_W - _TB_CLEAR, _TB_CLEAR, 0)))
    return tb, cell


def _title_block_box(dwg, a: Analysis):
    """The title block's real page-space bbox ``(x0, y0, x1, y1)``. GD&T placement avoids it
    (#481): the block is added last, so strip placement can't see it, but it's deterministic."""
    tb, _ = _make_title_block(dwg, a)
    b = tb.bounding_box()
    return (b.min.X, b.min.Y, b.max.X, b.max.Y)


def _add_title_block(dwg, a: Analysis):
    """Add the title block annotation."""
    tb, cell = _make_title_block(dwg, a)

    # Record that cell's page-space rectangle so export() can place a clickable
    # draftwright hyperlink over the "… / draftwright" author text. The build-frame
    # cell corners are offset by the block's page location (bx, _TB_CLEAR). The
    # rect rides the title-block annotation itself (like ``covers_diameters`` /
    # ``is_centerline`` riders), NOT an expando poked onto the drawing — the
    # drawing is not the state bus (#699 slice d); export reads it back via
    # ``get_annotation("title_block")``, so a removed block drops its link too.
    bx = a.PAGE_W - a.TB_W - _TB_CLEAR
    tb.draftwright_link_rect = (
        bx + cell["min_x"],
        _TB_CLEAR + cell["min_y"],
        bx + cell["max_x"],
        _TB_CLEAR + cell["max_y"],
    )
    place_annotation(dwg.registry, dwg.items, tb, "title_block")


def _make_sheet_frame(a: Analysis) -> Compound:
    """The sheet border rectangle (#767) — a closed outline at the ``_MARGIN`` inset (the old
    drawable boundary). Content clears it because ``a.margin`` is the reserved content margin.
    Carries an ``is_sheet_frame`` rider (like ``is_centerline``) so lint skips its page-spanning
    box, and so ``get_annotation`` / a removed frame drop it cleanly."""
    x0, y0 = _MARGIN, _MARGIN
    x1, y1 = a.PAGE_W - _MARGIN, a.PAGE_H - _MARGIN
    frame = Compound(
        children=[
            Edge.make_line(Vector(x0, y0, 0), Vector(x1, y0, 0)),
            Edge.make_line(Vector(x1, y0, 0), Vector(x1, y1, 0)),
            Edge.make_line(Vector(x1, y1, 0), Vector(x0, y1, 0)),
            Edge.make_line(Vector(x0, y1, 0), Vector(x0, y0, 0)),
        ]
    )
    frame.is_sheet_frame = True  # furniture, not a dimension/view — exempt from overlap lint
    return frame


def _add_sheet_frame(dwg, a: Analysis):
    """Add the sheet border (#767), drawn last like the title block. No-op is the caller's
    (gated on ``a.frame``)."""
    place_annotation(dwg.registry, dwg.items, _make_sheet_frame(a), "sheet_frame")


def _add_projection_symbol(dwg, a: Analysis):
    """Place the ISO 5456-2 projection-method glyph (#769) in the reserved title-block band,
    just above the drawn title block (deterministic empty space — the block reserves _TB_H but
    draws shorter). Registered ``projection_symbol`` with an ``is_projection_symbol`` identity
    rider. Unlike the page-spanning frame it is NOT lint-exempt: it's a small, well-placed glyph,
    so lint covers it and a future mispositioning is caught. Gated on ``a.projection``."""
    from build123d_drafting import ProjectionSymbol

    sym = ProjectionSymbol(
        a.projection,
        draft=draft_preset(
            font_size=dwg.draft.font_size,
            decimal_precision=dwg.draft.decimal_precision,
            font_path=PLEX_SANS_CONDENSED,
        ),
    )
    b = sym.bounding_box()
    bx, by = (b.min.X + b.max.X) / 2, (b.min.Y + b.max.Y) / 2
    w, h = b.max.X - b.min.X, b.max.Y - b.min.Y
    # Right side of the title-block column, near the top of its reserved band.
    cx = a.PAGE_W - _TB_CLEAR - w / 2 - 3
    cy = _TB_CLEAR + _TB_H - h / 2 - 2
    sym = sym.locate(Location((cx - bx, cy - by, 0)))
    sym.is_projection_symbol = True
    place_annotation(dwg.registry, dwg.items, sym, "projection_symbol")


def _add_zone_grid(dwg, a: Analysis):
    """Draw the ISO 5457 zone-grid border ruler (#768) — numbers 1.. along the top/bottom
    edges, letters A.. (skipping I/O) down the left/right — in the band between the frame
    (at ``_MARGIN``) and the page edge. Requires ``a.frame`` (the ticks sit on the border);
    the caller gates on ``a.zones`` and ensures the frame. Tick lines register as ``zone_grid``
    (``is_zone_grid``); each label carries an ``is_zone_label`` rider so it is exempt from the
    page-bounds lint (it legitimately sits outside the drawable) — but NOT from overlap lint."""
    from build123d_drafting import Note

    cols, rows = _zone_divisions(a.PAGE_W, a.PAGE_H)
    x0, y0, x1, y1 = _MARGIN, _MARGIN, a.PAGE_W - _MARGIN, a.PAGE_H - _MARGIN
    cw, rh = (x1 - x0) / cols, (y1 - y0) / rows
    band = _MARGIN
    tick = min(3.0, band * 0.6)
    draft = draft_preset(
        font_size=dwg.draft.font_size * 0.8,
        decimal_precision=dwg.draft.decimal_precision,
        font_path=PLEX_SANS_CONDENSED,
    )
    ticks = []
    for i in range(1, cols):  # interior column boundaries → ticks on top + bottom edges
        xb = x0 + i * cw
        ticks.append(Edge.make_line(Vector(xb, y0, 0), Vector(xb, y0 - tick, 0)))
        ticks.append(Edge.make_line(Vector(xb, y1, 0), Vector(xb, y1 + tick, 0)))
    for j in range(1, rows):  # interior row boundaries → ticks on left + right edges
        yb = y0 + j * rh
        ticks.append(Edge.make_line(Vector(x0, yb, 0), Vector(x0 - tick, yb, 0)))
        ticks.append(Edge.make_line(Vector(x1, yb, 0), Vector(x1 + tick, yb, 0)))
    grid = Compound(children=ticks)
    grid.is_zone_grid = True
    place_annotation(dwg.registry, dwg.items, grid, "zone_grid")

    def _label(text, cx, cy, name):
        note = Note(text, (cx, cy), draft, align=(Align.CENTER, Align.CENTER))
        note.is_zone_label = True
        place_annotation(dwg.registry, dwg.items, note, name)

    for i in range(cols):  # numbers 1.. left→right, in the bottom + top bands
        cx = x0 + (i + 0.5) * cw
        _label(str(i + 1), cx, y0 - band / 2, f"zone_num_b_{i}")
        _label(str(i + 1), cx, y1 + band / 2, f"zone_num_t_{i}")
    for j in range(rows):  # letters A.. top→bottom, in the left + right bands
        cy = y1 - (j + 0.5) * rh
        _label(_ZONE_LETTERS[j], x0 - band / 2, cy, f"zone_ltr_l_{j}")
        _label(_ZONE_LETTERS[j], x1 + band / 2, cy, f"zone_ltr_r_{j}")


def _iso_bbox(dwg):
    """(min_x, min_y, max_x, max_y) of the placed iso view, hidden lines included."""
    return dwg.view_bounds("iso")


# --- page/scale selection + sheet-layout constants and helpers --------------
# Relocated from make_drawing for the compose.py (née sheet.py) split (#162). Shared by compose.py
# (choose_scale/_layout_geometry) and make_drawing's repack pass, so they live
# here in the shared base to keep the DAG acyclic.
# The base inter-view corridor: one first-line gap + one dimension tier. Tracks
# _STRIP_GAP so widening the first-line gap (#347) keeps the below-plan / between-view
# corridors from razor-fitting the first dim line (the #130 slack guarantee): 10 + 10.
_DIM_PAD = _STRIP_GAP + _SLOT_DIM_HEIGHT  # 20.0
# _STRIP_GAP / _STRIP_SPACING are defined above (beside the `Strip` dataclass they seed).

_PAGE_SIZES = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}

# ISO 5455 scale series (1-2-5 decades). Enlargements + 1:1 first, then reductions
# down to 1:10000 so a very large part still gets a scale that FITS rather than an
# overflowing layout (#350). Ordered largest-scale-first for "least reduction first".
_SCALES = [10.0, 5.0, 2.0, 1.0]
_SCALES += [0.5, 0.2, 0.1, 0.05, 0.02, 0.01, 0.005, 0.002, 0.001, 0.0005, 0.0002, 0.0001]

# Horizontal page budget to reserve for the isometric view during scale
# selection and view placement, as a fraction of bbox_max * scale.  This is a
# deliberate *under-estimate*, not the true projected size (a cube's iso
# projection is ~1.63*bbox_max wide): the iso is the last column and is fitted
# to the actual largest-empty-rect afterwards by _fit_iso_view(), which shrinks
# it to whatever space is genuinely left.  A true fit test here is circular —
# the empty rect depends on the very view positions this estimate feeds — so
# the budget stays a single, named factor rather than a recomputed fit (#31).
_ISO_WIDTH_BUDGET = 0.7

# Scale selection accepts a layout when the largest empty rectangle left for the
# iso view can hold a square of at least this fraction of the iso's natural size
# (bbox_max * scale * _ISO_WIDTH_BUDGET).  Below 1.0 because _fit_iso_view scales
# the iso down to whatever space remains, so a modestly smaller rectangle still
# renders a legible iso — letting a long/short part enlarge onto a sheet (e.g.
# 2:1 on A3) where the strict row model would have under-scaled it.
_ISO_MIN_FIT_FRAC = 0.6


# Automatic scale/page preference ladder, first-fit.  The enlargement/unity
# region is page-major: every standard scale on the smallest sheet (A4) is tried
# before moving to the next sheet, so a part lands on the smallest sheet it fits
# at the largest scale that sheet allows — e.g. a 20×15×10 part gets 2:1 on A4,
# not 5:1 on A3.  Reductions (below 1:1) keep their legibility-vs-sheet balance
# (least reduction first) so a large part is not over-reduced onto a small sheet.
_LADDER = [
    # A4 — smallest sheet first, largest scale first
    (10.0, 297.0, 210.0, 120.0),  # A4 10:1
    (5.0, 297.0, 210.0, 120.0),  # A4 5:1
    (2.0, 297.0, 210.0, 120.0),  # A4 2:1
    (1.0, 297.0, 210.0, 120.0),  # A4 1:1
    # A3
    (5.0, 420.0, 297.0, 150.0),  # A3 5:1
    (2.0, 420.0, 297.0, 150.0),  # A3 2:1
    (1.0, 420.0, 297.0, 150.0),  # A3 1:1
    # A2
    (2.0, 594.0, 420.0, 150.0),  # A2 2:1
    (1.0, 594.0, 420.0, 150.0),  # A2 1:1
    # A1
    (1.0, 841.0, 594.0, 150.0),  # A1 1:1
    # Reductions — least reduction first, so a too-big part is not crammed onto a
    # small sheet at an illegible scale.
    (0.5, 594.0, 420.0, 150.0),  # A2 1:2
    (0.2, 420.0, 297.0, 150.0),  # A3 1:5
    (0.2, 594.0, 420.0, 150.0),  # A2 1:5
    (0.5, 841.0, 594.0, 150.0),  # A1 1:2
    (0.2, 841.0, 594.0, 150.0),  # A1 1:5
    (0.5, 1189.0, 841.0, 150.0),  # A0 1:2
    (0.2, 1189.0, 841.0, 150.0),  # A0 1:5
    # Past 1:5 keep reducing on A0 (the largest sheet) through the rest of the ISO 5455
    # series, so a part too big for A0 1:5 still gets a scale that FITS rather than an
    # overflowing layout (#350). A0 1:10000 holds anything up to ~8.4 m of drawn height.
    (0.1, 1189.0, 841.0, 150.0),  # A0 1:10
    (0.05, 1189.0, 841.0, 150.0),  # A0 1:20
    (0.02, 1189.0, 841.0, 150.0),  # A0 1:50
    (0.01, 1189.0, 841.0, 150.0),  # A0 1:100
    (0.005, 1189.0, 841.0, 150.0),  # A0 1:200
    (0.002, 1189.0, 841.0, 150.0),  # A0 1:500
    (0.001, 1189.0, 841.0, 150.0),  # A0 1:1000
    (0.0005, 1189.0, 841.0, 150.0),  # A0 1:2000
    (0.0002, 1189.0, 841.0, 150.0),  # A0 1:5000
    (0.0001, 1189.0, 841.0, 150.0),  # A0 1:10000
]


def _tb_width(page_w: float) -> float:
    """Title-block width for a page: 120 mm on A4, 150 mm on A3 and larger."""
    return 120.0 if page_w <= 297.0 else 150.0


def _parse_page(page) -> tuple:
    """Resolve a page spec to ``(PAGE_W, PAGE_H, TB_W)``.

    Accepts an ISO name (``"A4"``…``"A0"``, case-insensitive), a
    ``"WIDTHxHEIGHT"`` string in mm (e.g. ``"420x297"``), or a
    ``(width, height)`` tuple in mm.
    """
    if isinstance(page, str):
        name = page.strip().upper()
        if name in _PAGE_SIZES:
            pw, ph = _PAGE_SIZES[name]
        else:
            m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)", page.strip())
            if not m:
                raise ValueError(
                    f"unknown page size {page!r} — expected one of "
                    f"{', '.join(_PAGE_SIZES)} or WIDTHxHEIGHT in mm (e.g. '420x297')"
                )
            pw, ph = float(m.group(1)), float(m.group(2))
    else:
        try:
            pw, ph = float(page[0]), float(page[1])
        except (TypeError, ValueError, IndexError):
            raise ValueError(
                f"invalid page size {page!r} — expected an ISO name, "
                f"'WIDTHxHEIGHT', or a (width, height) tuple in mm"
            ) from None
    if pw <= 0 or ph <= 0:
        raise ValueError(f"page dimensions must be positive, got {page!r}")
    return pw, ph, _tb_width(pw)
