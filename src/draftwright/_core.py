"""Shared low-level primitives for the draftwright drawing engine.

This module sits below :mod:`draftwright.make_drawing` and
:mod:`draftwright.annotate`: it holds the data structures and small helpers
both layers depend on (the :class:`Analysis` namespace and its field types,
the dimension/format helpers, and the layout constants).  It imports only from
:mod:`draftwright.layout` and third-party libraries -- never from
``make_drawing`` -- so the module graph stays a DAG (#98 Phase C).
"""

from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from draftwright.recognition import TurnedProfile

from build123d import Align, BoundBox, Location, Mode, Shape, Text
from build123d_drafting.helpers import (
    Dimension,
    TitleBlock,
    draft_preset,
    format_drawing_scale,
)

from draftwright.fonts import PLEX_MONO, PLEX_SANS_CONDENSED
from draftwright.layout import _greedy_strip_1d, _solve_strip_1d

_log = logging.getLogger(__name__)


_MARGIN = 10.0


_TB_CLEAR = _MARGIN + 1.0  # title-block inset: one extra mm over _MARGIN for clearance

# The orthographic view a feature is dimensioned end-on in — the view normal to its
# axis. Single source of truth shared by the planner (view choice) and the lint
# coverage checks (where to look for a feature's dims). Orientation is data.
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


_FONT_SIZE = 3.0  # annotation text height (page-mm); the draft preset is built with this


_TB_H = 35.0


def _fmt(v: float) -> str:
    """Format a float as integer string if whole, otherwise 1 dp."""
    r = round(v)
    return str(r) if abs(v - r) < 1e-6 else f"{v:.1f}"


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

# A single-quoted label lifted from a lint message, e.g. "labels 'A' and 'B' …".
# Shared by the #29 lint suggestions (linting.py) and the #30 repair loop.
_QUOTED_RE = re.compile(r"'([^']*)'")


@functools.lru_cache(maxsize=512)
def _text_width(text: str, font_size: float, font_path: str = PLEX_MONO) -> float:
    """Measured rendered width (page-mm) of *text* at *font_size*.

    Uses build123d's ``Text`` — the same primitive ``Dimension``/``HoleCallout``
    stroke their labels with — so callout-width estimates use real glyph metrics
    instead of a character-count fudge (#31).  Pinned to a vendored font **file**
    (``font_path``), not a system font *name*: name resolution substitutes a
    different font on Linux, which makes this estimate — and the layout it feeds —
    platform-variant (#149). The default is the same face the annotations render
    with, so estimate and render agree.  Cached because the same numeric labels
    recur across holes and the rasterisation is the costly part.
    """
    if not text:
        return 0.0
    return (
        Text(
            txt=text,
            font_size=font_size,
            font_path=font_path,
            align=(Align.CENTER, Align.CENTER),
            mode=Mode.PRIVATE,
        )
        .bounding_box()
        .size.X
    )


def _axis_letter(obj) -> str:
    """Letter (``"x"``/``"y"``/``"z"``) of ``obj.axis``'s dominant component.

    ``obj`` is anything carrying an ``.axis`` 3-vector (a hole or a boss).
    """
    return max(zip("xyz", obj.axis, strict=True), key=lambda t: abs(t[1]))[0]


_CONCENTRIC_TOL_MM = 0.5


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


@dataclass
class Strip:
    """A one-dimensional annotation band adjacent to an orthographic view.

    Annotations are stacked outward from the view edge by calling
    :meth:`allocate`.  The cursor starts at ``anchor + direction * gap`` and
    advances after each successful allocation.

    Attributes:
        anchor:      Page coordinate of the view edge this strip starts from.
        outer_limit: Page coordinate at which the strip ends (page margin,
                     neighbouring view, or title-block boundary).
        direction:   ``+1`` — cursor moves away from anchor (right/above);
                     ``-1`` — cursor retreats from anchor (left/below).
        gap:         Clearance between the view edge and the first annotation.
        spacing:     Clearance between successive annotations.
    """

    anchor: float
    outer_limit: float
    direction: float = 1.0
    gap: float = 8.0
    spacing: float = 4.0
    _cursor: float = field(init=False, compare=False, repr=False)

    def __post_init__(self):
        self._cursor = self.anchor + self.direction * self.gap

    # ------------------------------------------------------------------
    # Public API

    @property
    def available(self) -> float:
        """Total space available in this strip (mm)."""
        return abs(self.outer_limit - self.anchor)

    @property
    def depth_used(self) -> float:
        """How far the cursor has advanced from the anchor (mm)."""
        return abs(self._cursor - self.anchor)

    def peek(self, size: float) -> float | None:
        """Return what ``allocate(size)`` would return without advancing the cursor."""
        if self.direction == 1:
            start = self._cursor
            return start if (start + size) <= self.outer_limit else None
        else:
            end = self._cursor
            return end if (end - size) >= self.outer_limit else None

    def allocate(self, size: float) -> float | None:
        """Reserve *size* mm; return the near-edge page coordinate, or ``None`` if full.

        The returned value is the page coordinate of the annotation's
        dimension line (or leader elbow).  Convert to a relative offset with::

            distance = abs(page_coord - strip.anchor)
        """
        if self.direction == 1:
            start = self._cursor
            end = start + size
            if end > self.outer_limit:
                return None
            self._cursor = end + self.spacing
            return start
        else:
            end = self._cursor
            start = end - size
            if start < self.outer_limit:
                return None
            self._cursor = start - self.spacing
            return end


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


_STRIP_SPACING = 4.0  # page-mm between successive annotations in a strip
_MIN_VIEW_MM = (
    10.0  # min projected view dimension; below it annotation geometry degenerates (#129)
)


_SLOT_DIM_STEP = 4 * _FONT_SIZE + _PAD  # fv_zones.right: step-height dimension


_TABULATE_MIN_HOLES = 16


_MIN_STEP_DIM_MM = (
    _FONT_SIZE
    + 2 * draft_preset(font_size=_FONT_SIZE, decimal_precision=1).arrow_length
    + 2 * draft_preset(font_size=_FONT_SIZE, decimal_precision=1).pad_around_text
)


_MIN_STEP_SEP_MM = _FONT_SIZE + 2 * _PAD


_MIN_LOC_SEP_MM = draft_preset(font_size=_FONT_SIZE, decimal_precision=1).arrow_length + _PAD


def _legible_steps(step_zs, bb_min_z, scale):
    """Step heights worth dimensioning at *scale*, and how many were too close.

    A step is dimensioned only if it is tall enough from the base to carry a
    label *and* at least ``_MIN_STEP_SEP_MM`` (page-mm) above the previously
    kept step — consecutive shoulders closer than that are page-coincident and
    cannot be told apart (#41). Returns ``(kept_zs, n_too_close)``: the heights
    to dimension, and the count of tall-enough steps dropped for spacing (the
    caller surfaces these via lint; the full-fidelity answer is a detail view,
    #42). Steps too short to carry a label at all are silently omitted — they
    are simply not dimensionable, not dropped.
    """
    kept: list[float] = []
    n_too_close = 0
    last = None
    for z in sorted(step_zs):
        if (z - bb_min_z) * scale < _MIN_STEP_DIM_MM:
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


def _largest_empty_rect(drawable, obstacles):
    """Largest axis-aligned empty rectangle in *drawable* avoiding *obstacles*.

    *drawable* and each obstacle are ``(x0, y0, x1, y1)`` page-mm boxes.  Returns
    the empty sub-rectangle of *drawable* (overlapping no obstacle) that maximises
    the side of the largest square it can hold — i.e. ``min(width, height)`` — so
    the (near-square) iso view can be scaled up as far as possible.

    The obstacle set is tiny (front/plan/side views + title block), so a
    gap-based search over candidate edges is both exact enough and cheap: every
    maximal empty rectangle has edges drawn from the drawable bounds and the
    obstacle bounds, so enumerating those cut lines finds the optimum.
    """
    dx0, dy0, dx1, dy1 = drawable
    xs = sorted({dx0, dx1, *(c for o in obstacles for c in (o[0], o[2]) if dx0 < c < dx1)})
    ys = sorted({dy0, dy1, *(c for o in obstacles for c in (o[1], o[3]) if dy0 < c < dy1)})

    best = None
    best_score = 0.0
    for i in range(len(xs) - 1):
        for j in range(i + 1, len(xs)):
            rx0, rx1 = xs[i], xs[j]
            for k in range(len(ys) - 1):
                for m in range(k + 1, len(ys)):
                    ry0, ry1 = ys[k], ys[m]
                    if any(
                        rx0 < o[2] and o[0] < rx1 and ry0 < o[3] and o[1] < ry1 for o in obstacles
                    ):
                        continue
                    score = min(rx1 - rx0, ry1 - ry0)
                    if score > best_score:
                        best_score = score
                        best = (rx0, ry0, rx1, ry1)
    if best is None:
        # No empty rectangle exists (obstacles cover the drawable area). This
        # is unreachable in practice — choose_scale always leaves a gap — but
        # if it ever happens the iso would render over the other views, so flag
        # it rather than fail silently.
        _log.warning(
            "No empty rectangle found for the iso view; obstacles fill the "
            "drawable area — iso may overlap other views"
        )
        return drawable
    return best


@dataclass
class DetailRequest:
    """A renderer's request for an enlarged detail of a region it could not draw
    legibly at sheet scale (#307). Renderers append these to ``dwg._detail_requests``
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
        redraw:       ``redraw(dwg, view_name, detail_scale) -> int`` — draws the
                      detail's dimensions in the placed detail view's coordinate system
                      and returns the count placed (0 → the detailer rolls the view
                      back rather than leave an empty box). Called once the detail is
                      placed; the main view always carries the located head/block
                      inline regardless, so a placement failure loses no coverage (lint
                      reports the un-located interior instead).
        pad_top:      page-mm band reserved above the detail view (a horizontal
                      chain); reserved in the fit + placement.
        pads:         optional ``pads(detail_scale) -> (pad_right, pad_top)`` for a
                      footprint that depends on the chosen scale (the prismatic
                      ladder reserves one rung per *legible-at-that-scale* step, so it
                      shrinks with the scale during the fit). Overrides ``pad_top``.
        kind:         short label for logging.
    """

    axis: str
    lo: float
    hi: float
    scale_needed: float
    redraw: object
    pad_top: float = 0.0
    pads: object = None
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
    bosses: list  # external bosses (find_bosses), detected once — the one inventory (#244)
    slots: list
    z_diams: list[float]
    cross_diams: list[float]
    cyls: tuple[list, list]
    prof: TurnedProfile | None  # turned step profile (find_turned_steps), detected once
    od_diam: float | None
    is_rotational: bool
    od_axis: str  # rotation/turning axis of a rotational part ("z" default; "x"/"y" #222)
    step_zs: list[float]
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
    pmi: list
    pmi_mode: str


_greedy_strip_ys = _greedy_strip_1d


_solve_strip_ys = _solve_strip_1d


_DRAFTWRIGHT_URL = "https://github.com/pzfreo/draftwright"


def _attribution_author(drawn_by: str | None) -> str:
    """ISO 7200 "drawn by" value: the human author and draftwright, or just
    draftwright when no author was supplied."""
    author = (drawn_by or "").strip()
    return f"{author} / draftwright" if author else "draftwright"


def _add_title_block(dwg, a: Analysis):
    """Add the title block annotation."""
    tb = TitleBlock(
        a.title,
        a.number,
        scale=format_drawing_scale(a.SCALE),
        general_tolerance=a.tolerance,
        designed_by=_attribution_author(a.drawn_by),
        revision="A",
        legal_owner="",
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
    dwg.add(tb, "title_block")

    # Record that cell's page-space rectangle so export() can place a clickable
    # draftwright hyperlink over the "… / draftwright" author text. The build-frame
    # cell corners are offset by the block's page location (bx, _TB_CLEAR).
    bx = a.PAGE_W - a.TB_W - _TB_CLEAR
    dwg._draftwright_link_rect = (
        bx + cell["min_x"],
        _TB_CLEAR + cell["min_y"],
        bx + cell["max_x"],
        _TB_CLEAR + cell["max_y"],
    )


def _iso_bbox(dwg):
    """(min_x, min_y, max_x, max_y) of the placed iso view, hidden lines included."""
    return dwg.view_bounds("iso")


# --- page/scale selection + sheet-layout constants and helpers --------------
# Relocated from make_drawing for the sheet.py split (#162). Shared by sheet.py
# (choose_scale/_layout_geometry) and make_drawing's repack pass, so they live
# here in the shared base to keep the DAG acyclic.
_DIM_PAD = 18.0
_STRIP_GAP = 8.0

_PAGE_SIZES = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}

_SCALES = [10.0, 5.0, 2.0, 1.0, 0.5, 0.2]

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
