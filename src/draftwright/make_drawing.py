"""make_drawing — Zero-AI STEP-to-technical-drawing pipeline.

Produces a 4-view third-angle technical drawing (front, plan, side, isometric)
with automatic dimension selection from face-geometry analysis.

Typical usage::

    from build123d_drafting.make_drawing import make_drawing
    svg_path, dxf_path = make_drawing("part.step", title="BRACKET", number="DWG-042")

CLI (registered as ``make-drawing``)::

    make-drawing part.step
    make-drawing part.step --title "BRACKET" --number DWG-042
    make-drawing part.step --script   # write editable .py instead
    make-drawing part.step --out /tmp/output
"""

import argparse
import functools
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from build123d import (
    Align,
    Arrow,
    Box,
    Color,
    Compound,
    Edge,
    ExportDXF,
    ExportSVG,
    GeomType,
    HeadType,
    LineType,
    Location,
    Mode,
    Pos,
    Shape,
    Text,
    Vector,
)
from build123d_drafting.features import (
    BoltCircle,
    LinearArray,
    _full_cyls,
    _spec_key,
    analyse_cylinders,
    find_hole_patterns,
    find_holes,
)
from build123d_drafting.helpers import (
    Centerline,
    CenterlineCircle,
    CenterMark,
    Dimension,
    HoleCallout,
    Leader,
    LintIssue,
    Note,
    TitleBlock,
    ViewCoordinates,
    annotate,
    draft_preset,
    format_drawing_scale,
    lint_drawing,
    set_page,
    view_axes,
)
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import GeomAbs_Plane
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopTools import TopTools_ListOfShape

_log = logging.getLogger(__name__)

_TB_W = 150.0
_MARGIN = 10.0
_TB_CLEAR = _MARGIN + 1.0  # title-block inset: one extra mm over _MARGIN for clearance
_FONT_SIZE = 3.0  # annotation text height (page-mm); the draft preset is built with this
_DIM_PAD = 18.0
_TB_H = 35.0
# Minimum acceptable projected view dimension (page-mm).  Below this, annotation
# geometry (leader wires, centre marks, bore callout elbows) can degenerate and
# cause OCCT Standard_DomainError / SIGABRT (#129).
_MIN_VIEW_MM = 10.0

_PAGE_SIZES = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
}


# ---------------------------------------------------------------------------
# SVG post-processing
# ---------------------------------------------------------------------------


def fix_svg_page_size(svg_path: str, page_w: float, page_h: float) -> None:
    """Rewrite the SVG width/height/viewBox to match the full ISO page size.

    ExportSVG crops to content bounding box; this expands it to the declared
    page so the rendering fills the correct A-series sheet.
    """
    data = Path(svg_path).read_text(encoding="utf-8")
    data = re.sub(r'width="[^"]*"', f'width="{page_w:.3f}mm"', data, count=1)
    data = re.sub(r'height="[^"]*"', f'height="{page_h:.3f}mm"', data, count=1)
    data = re.sub(
        r'viewBox="[^"]*"',
        f'viewBox="0 -{page_h:.3f} {page_w:.3f} {page_h:.3f}"',
        data,
        count=1,
    )
    Path(svg_path).write_text(data, encoding="utf-8")


# Below this, an elliptical-arc radius (page-mm) is treated as degenerate.
# Real feature arcs are orders of magnitude larger; the bad ones are ~1e-7.
_MIN_ARC_RADIUS = 1e-3

_SVG_NUM = r"(-?\d+\.?\d*(?:[eE][-+]?\d+)?)"
_SVG_ARC_RE = re.compile(
    r"A\s*"
    + _SVG_NUM
    + r"[ ,]+"
    + _SVG_NUM
    + r"[ ,]+"
    + _SVG_NUM
    + r"[ ,]+([01])[ ,]*([01])[ ,]+"
    + _SVG_NUM
    + r"[ ,]+"
    + _SVG_NUM
)


def sanitize_svg_arcs(svg_path: str) -> int:
    """Rewrite near-degenerate elliptical arcs as straight line segments.

    build123d's ``ExportSVG`` projects a circular edge seen edge-on (a hole or
    fillet rim whose plane is parallel to the view direction) as an elliptical
    arc with a vanishing minor radius (``ry`` ≈ 1e-7).  The SVG spec says a
    zero-radius arc is a straight line, but because the radius is not *exactly*
    zero, renderers (librsvg, cairosvg) treat it as a hugely eccentric ellipse
    and draw a spurious full-page line.  Each such arc (``A rx ry rot lf sf x
    y``) with ``rx`` or ``ry`` below :data:`_MIN_ARC_RADIUS` is replaced by
    ``L x y`` — its true geometry.  Returns the number of arcs rewritten.
    """
    data = Path(svg_path).read_text(encoding="utf-8")
    n = 0

    def _repl(m):
        nonlocal n
        if abs(float(m.group(1))) < _MIN_ARC_RADIUS or abs(float(m.group(2))) < _MIN_ARC_RADIUS:
            n += 1
            return f"L {m.group(6)} {m.group(7)}"
        return m.group(0)

    fixed = _SVG_ARC_RE.sub(_repl, data)
    if n:
        Path(svg_path).write_text(fixed, encoding="utf-8")
    return n


def _import_step(path) -> Compound:
    """Read solid geometry from a STEP file via OCCT's ``STEPControl_Reader``.

    build123d's ``import_step`` uses the XCAF reader (colours, names, PMI), which
    **segfaults** on some AP242 files carrying semantic PMI — e.g. NIST CTC-02
    AP242 (#20) — before any Python code can intervene. draftwright needs only
    the solid geometry (it drops PMI presentation data anyway), so we read the
    geometry directly. Verified to produce identical shapes (solids, edges, bbox)
    to ``import_step`` on the files that read in both, minus the unused metadata.
    """
    reader = STEPControl_Reader()
    if reader.ReadFile(str(path)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise ValueError(f"could not read STEP file {path!r}")
    reader.TransferRoots()
    return Compound(reader.OneShape())


# ---------------------------------------------------------------------------
# Geometry analysis
# ---------------------------------------------------------------------------


def dedup_diams(cyls, tol: float = 0.15) -> list:
    """Return sorted-descending deduplicated diameter list from cylinder records."""
    raw = sorted({c["diameter"] for c in cyls}, reverse=True)
    merged: list[float] = []
    for d in raw:
        if not merged or abs(d - merged[-1]) > tol:
            merged.append(round(d, 2))
    return merged


def _fmt(v: float) -> str:
    """Format a float as integer string if whole, otherwise 1 dp."""
    r = round(v)
    return str(r) if abs(v - r) < 1e-6 else f"{v:.1f}"


@functools.lru_cache(maxsize=512)
def _text_width(text: str, font_size: float, font: str = "Arial") -> float:
    """Measured rendered width (page-mm) of *text* in *font* at *font_size*.

    Uses build123d's ``Text`` — the same primitive ``Dimension``/``HoleCallout``
    stroke their labels with — so callout-width estimates use real glyph metrics
    instead of a character-count fudge (#31).  Cached because the same numeric
    labels recur across holes and the rasterisation is the costly part.
    """
    if not text:
        return 0.0
    return (
        Text(
            txt=text,
            font_size=font_size,
            font=font,
            align=(Align.CENTER, Align.CENTER),
            mode=Mode.PRIVATE,
        )
        .bounding_box()
        .size.X
    )


_DIAM_RE = re.compile(r"[øØ⌀]\s*(\d+(?:\.\d+)?)")

# Turned-part classification (#81): a rotational part's bounding box is
# square in XY to within _SQUARENESS_TOL, and its OD — the largest full
# *external* Z cylinder — fills at least _OD_FILL_MIN of that envelope, with
# its axis within _OD_AXIS_TOL of the envelope centre. Anything else is
# prismatic — its Z cylinders are holes or local bosses, not an OD.
_SQUARENESS_TOL = 0.05
_OD_FILL_MIN = 0.8
_OD_AXIS_TOL = 0.05


def _is_rotational(x_size, y_size, od_diam, od_axis_offset) -> bool:
    """True for turned parts: an outward-facing Z cylinder, concentric with
    the bounding box, filling a square envelope.

    ``od_diam`` is the largest full *external* Z-cylinder diameter (``None``
    when there is none — bores never qualify as an OD) and
    ``od_axis_offset`` that cylinder's axis distance from the bbox centre.
    """
    if od_diam is None:
        return False
    envelope = max(x_size, y_size)
    return (
        abs(x_size - y_size) <= _SQUARENESS_TOL * envelope
        and od_diam >= _OD_FILL_MIN * envelope
        and od_axis_offset <= _OD_AXIS_TOL * envelope
    )


# A hole is "concentric" with a turned part's rotation axis when its drilling
# axis is the Z (OD) axis and its opening sits on the part centreline.  Such
# bores are already dimensioned by the ldr_z bore leaders, so they must not
# also receive a hole callout / location dim (#10).  Off-axis holes (a bolt
# circle, a cross-hole) fall through to the feature-presence path.
_CONCENTRIC_TOL_MM = 0.5


def _is_concentric_hole(h, a, axis_letter) -> bool:
    """True when *h* is an axial bore on the part centreline (turned base set)."""
    if axis_letter(h) != "z":
        return False
    return math.hypot(h.location[0] - a.cx, h.location[1] - a.cy) <= _CONCENTRIC_TOL_MM


def _concentric_bore_diams(a) -> list:
    """Distinct bore diameters on the rotation axis, in z_diams order (#10).

    ``a.z_diams`` carries every Z cylinder diameter — including off-axis ones
    such as a bolt circle's holes — so the bore-leader set is restricted to
    diameters that actually have an *internal* Z cylinder whose axis sits on
    the part centreline.  The OD is excluded.  Returned in z_diams order so
    label ordering is stable.
    """
    z_cyls, _ = a.cyls
    concentric = {
        c["diameter"]
        for c in _full_cyls(z_cyls)
        if not c["external"]
        and math.hypot(c["axis_xyz"][0] - a.cx, c["axis_xyz"][1] - a.cy) <= _CONCENTRIC_TOL_MM
    }
    return [d for d in a.z_diams if d != a.od_diam and any(abs(d - c) <= 0.15 for c in concentric)]


def lint_feature_coverage(part, annotations, tol: float = 0.15, cyls=None, exclude=None) -> list:
    """Coarse completeness check: report part diameters with no callout (#80).

    ``exclude`` is an optional iterable of diameters already accounted for by a
    more specific build-time lint (e.g. the per-view callout cap's
    ``callout_dropped``); these are skipped here so a dropped callout is not
    double-reported as ``feature_not_dimensioned``.

    Builds a feature inventory from *part*'s hole/boss diameters (cylinder
    patches spanning at least ~half a turn around their axis in total, so
    fillets are ignored) and diffs it against every ø value mentioned in the
    annotations' labels, plus the structured ``covers_diameters`` metadata on
    annotations that draw their values geometrically (e.g. ``HoleCallout``).
    Radius callouts are *not* counted — "R5 TYP" fillet notes would otherwise
    mask an undimensioned ø10 bore. Title blocks are skipped — part numbers
    like "BRACKET R8" are not callouts. Each uncovered diameter yields one
    ``feature_not_dimensioned`` warning.

    ``cyls`` accepts a precomputed ``analyse_cylinders(part)`` result so
    repeated lint runs need not re-scan the solid.

    Counts are checked too (#92): the part's holes (via ``find_holes``) give
    a required count per diameter (each bore, counterbore, and spotface
    occurrence counts one), and structured callouts declare how many holes
    they dimension (``covers_count`` — the ``n×`` prefix). A shortfall
    yields a ``feature_count_mismatch`` warning. A diameter covered by any
    free-text ø-label is exempt from the count check — text labels carry no
    count semantics. Location coverage remains out of scope (#93).
    """
    z_cyls, cross_cyls = cyls if cyls is not None else analyse_cylinders(part)
    inventory = dedup_diams(_full_cyls(z_cyls + cross_cyls), tol=tol)

    mentioned: set[float] = set()
    text_mentioned: set[float] = set()
    provided: dict[float, int] = {}
    for ann in annotations:
        if isinstance(ann, TitleBlock):
            continue
        label = getattr(ann, "label", None) or ""
        for m in _DIAM_RE.finditer(label):
            mentioned.add(float(m.group(1)))
            text_mentioned.add(float(m.group(1)))
        count = getattr(ann, "covers_count", 1)
        for v in getattr(ann, "covers_diameters", ()):
            mentioned.add(float(v))
            provided[float(v)] = provided.get(float(v), 0) + count

    exclude = exclude or ()
    issues = [
        LintIssue(
            severity="warning",
            code="feature_not_dimensioned",
            message=f"cylindrical feature ø{_fmt(d)} has no diameter callout on the sheet",
        )
        for d in inventory
        if not any(abs(d - v) <= tol for v in mentioned)
        and not any(abs(d - e) <= tol for e in exclude)
    ]

    required: dict[float, int] = {}
    for h in find_holes(part, cyls=(z_cyls, cross_cyls)):
        for d in (h.diameter, *(s.diameter for s in (h.cbore, h.spotface) if s)):
            key = next((k for k in required if abs(k - d) <= tol), d)
            required[key] = required.get(key, 0) + 1
    for d, need in sorted(required.items(), reverse=True):
        if any(abs(d - v) <= tol for v in text_mentioned):
            continue  # free-text coverage carries no count to check against
        have = sum(c for v, c in provided.items() if abs(d - v) <= tol)
        if 0 < have < need:
            issues.append(
                LintIssue(
                    severity="warning",
                    code="feature_count_mismatch",
                    message=(
                        f"{need} ø{_fmt(d)} features on the part but callouts account for {have}"
                    ),
                )
            )
    return issues


# --- lint scoring (see Drawing.lint_summary) -------------------------------
# Codes that check standards/geometry correctness rather than pure page
# layout. Grouped so a caller (and the #30 repair loop) can tell a wrong
# drawing from a merely tight one.
_GEOMETRY_AWARE_CODES = frozenset(
    {
        "feature_not_dimensioned",
        "feature_count_mismatch",
        "missing_principal_dimension",
        "label_vs_measured",
        "dim_inside_part",
        "callout_dropped",
        "location_ref_dropped",
        "step_dim_dropped",
        "placement_unsatisfiable",
    }
)

# Coarse 0–1 quality heuristic: a clean sheet scores 1.0; each issue subtracts
# a flat per-severity penalty (clamped at 0). A convenience signal only — the
# severity/code counts in the summary are the authoritative output.
_SCORE_ERROR_PENALTY = 0.2
_SCORE_WARNING_PENALTY = 0.05


def analyse_face_levels(part, tol: float = 0.5, min_area_frac: float = 0.0) -> list:
    """Return sorted unique Z-coords of horizontal (normal≈±Z) planar faces.

    Uses tol-bucket deduplication but returns the actual face Z, not the rounded
    bucket centre, so dimension labels match the true geometry.

    When *min_area_frac* > 0, a Z level is kept only if the total area of its
    horizontal faces is at least ``min_area_frac × (x_size × y_size)`` (the
    part's plan footprint). This drops sub-feature faces — e.g. fragments of
    engraved text/numbers — that are not real steps and would otherwise be
    dimensioned as phantom shoulders (staircase.step review).
    """
    buckets: dict = {}  # bucket key -> representative z
    areas: dict = {}  # bucket key -> total horizontal-face area
    for face in part.faces():
        surf = BRepAdaptor_Surface(face.wrapped)
        if surf.GetType() == GeomAbs_Plane:
            ax = surf.Plane().Axis().Direction()
            if abs(ax.Z()) > 0.99:
                z = surf.Plane().Location().Z()
                key = round(z / tol) * tol
                buckets.setdefault(key, z)
                if min_area_frac > 0.0:
                    props = GProp_GProps()
                    BRepGProp.SurfaceProperties_s(face.wrapped, props)
                    areas[key] = areas.get(key, 0.0) + props.Mass()
    if min_area_frac > 0.0:
        bb = part.bounding_box()
        footprint = (bb.max.X - bb.min.X) * (bb.max.Y - bb.min.Y)
        threshold = min_area_frac * footprint
        return sorted(z for key, z in buckets.items() if areas.get(key, 0.0) >= threshold)
    return sorted(buckets.values())


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

_SCALES = [10.0, 5.0, 2.0, 1.0, 0.5, 0.2]


# ---------------------------------------------------------------------------
# Strip / zone layout model
# ---------------------------------------------------------------------------


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

    Any strip that has no usable space (e.g. a side view's left strip, which
    abuts the front view) is ``None``.
    """

    right: Strip | None = None
    left: Strip | None = None
    above: Strip | None = None
    below: Strip | None = None


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


_STRIP_GAP = 8.0
_STRIP_SPACING = 4.0

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

# Upper bound on how far the iso view may grow beyond sheet scale when fitted to
# its zone.  The iso is an orientation aid, not a measured view: left uncapped it
# fills the (now often large) empty rectangle and can dwarf the dimensioned
# orthographic views (up to ~8× on an oversized sheet).  Capped just above sheet
# scale so it still fills modest zones without dominating.  Shrinking to fit a
# small zone is never capped.
_ISO_MAX_GROW = 1.3

# Slot sizes for the annotations that allocate from fv/pv/sv strips.
# Shared between the depth estimators below and the allocate() call-sites in
# _auto_annotate() so that a slot-size change is automatically reflected in
# the estimator-driven corridor widths.
#
# A slot is the perpendicular depth (page-mm) reserved for one Dimension: its
# dim-line offset from the view edge plus the label, which sits exactly
# pad_around_text beyond the line (measured: a "right"/"below" Dimension's
# perpendicular span equals offset + pad_around_text - extension_gap, and
# pad == extension_gap in the draft preset).  Each slot is therefore derived
# from text metrics (font_size + pad_around_text), like _MIN_STEP_DIM_MM, so it
# rescales with _FONT_SIZE instead of being a bare mm guess (#31).
_PAD = draft_preset(font_size=_FONT_SIZE, decimal_precision=1).pad_around_text
# Single overall dim: two glyph-heights of line offset + the outboard label pad.
_SLOT_DIM_WIDTH = 2 * _FONT_SIZE + _PAD  # pv_zones.below: overall width dimension
_SLOT_DIM_DEPTH = 2 * _FONT_SIZE + _PAD  # sv_zones.below: overall depth dimension
# The overall height dim leads the right ladder, so it carries an extra pad of
# clearance from the view above the first step dim's witness.
_SLOT_DIM_HEIGHT = 2 * _FONT_SIZE + 2 * _PAD  # fv_zones.right: overall height dim
# Stacked step dims sit deeper so each ladder rung's label clears the rung below.
_SLOT_DIM_STEP = 4 * _FONT_SIZE + _PAD  # fv_zones.right: step-height dimension

# Smallest projected step height (page-mm) that can still carry a *legible*
# stacked dimension between its two extension lines.  Derived from what has to
# fit vertically: the label (font height) plus an arrowhead at each end plus
# the text clearance above and below — not an arbitrary page-mm cutoff (#13).
# Used as the single gate in BOTH _analyse (n_steps) and _auto_annotate
# (dim_step placement) so the two can never diverge.
_MIN_STEP_DIM_MM = (
    _FONT_SIZE
    + 2 * draft_preset(font_size=_FONT_SIZE, decimal_precision=1).arrow_length
    + 2 * draft_preset(font_size=_FONT_SIZE, decimal_precision=1).pad_around_text
)

# Minimum page-mm separation between two *consecutive* dimensioned step heights.
# Shoulders closer than this on the page read as one, so only the first of such
# a cluster is dimensioned and the rest surface via lint (#41). Sized to the
# value-label footprint (one glyph height + clearance) — enough to tell two
# stacked step dims apart, without dropping genuinely-distinct shoulders.
_MIN_STEP_SEP_MM = _FONT_SIZE + 2 * _PAD

# A horizontal face counts as a real step only if its area is at least this
# fraction of the part's plan footprint (x_size × y_size). Filters out tiny
# faces from engraved text/numbers that are not steps (staircase.step review).
_STEP_MIN_AREA_FRAC = 0.01

# Minimum page-mm separation between two *consecutive* hole-location dimensions
# along one axis. Stacked location dims sit on separate tiers, so their value
# labels never collide (the tier pitch handles that); the legibility limit is the
# extension lines / arrowheads merging when two holes share almost the same
# position on that axis. Sized to one arrowhead plus clearance — smaller than the
# step-spacing gate, which also stacks labels in one column. Holes closer than
# this read as one, so only the first of such a run is dimensioned and the rest
# surface via lint (#43): "fits" is not the same as "legible".
_MIN_LOC_SEP_MM = draft_preset(font_size=_FONT_SIZE, decimal_precision=1).arrow_length + _PAD


def _legible_locations(positions, scale):
    """Axis positions far enough apart on the page to dimension legibly.

    Given world-coordinate *positions* along one axis, keep a position only if it
    is at least ``_MIN_LOC_SEP_MM`` page-mm from the previously kept one;
    consecutive holes closer than that produce baseline witness lines that read
    as a single busy cluster (#43). Returns ``(kept, n_too_close)``: the
    positions to dimension and the count dropped for spacing (the caller surfaces
    these via ``location_ref_dropped`` lint; the full-fidelity answer is a detail
    view, #42). Mirrors :func:`_legible_steps` for hole locations.
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


# ---------------------------------------------------------------------------
# Annotation depth estimators (Phase 2 of #118)
#
# These pure functions estimate the strip depth (mm) required for each
# inter-view boundary BEFORE view positions are fixed.  They are intentionally
# conservative (may over-estimate slightly).  Used by _analyse() (Phase 3) to
# set minimum corridor widths, and by _fits() (Phase 3) for consistent sheet
# selection.
# ---------------------------------------------------------------------------


def _est_right_strip_depth(n_steps: int) -> float:
    """Depth needed to the right of the front view.

    Always includes dim_height (1 slot).  *n_steps* dim_step slots follow if
    any step levels are present.  Returns the minimum corridor width (from view
    edge to outer_limit) that makes all those allocations succeed.
    """
    n = 1 + max(n_steps, 0)  # dim_height + one slot per step dim
    # gap + dim_height + (n-1) step slots each preceded by one spacing
    return _STRIP_GAP + _SLOT_DIM_HEIGHT + (n - 1) * (_STRIP_SPACING + _SLOT_DIM_STEP)


def _est_pv_below_depth() -> float:
    """Depth needed below the plan view: dim_width (always one slot)."""
    return _STRIP_GAP + _SLOT_DIM_WIDTH


# Inter-constant invariant: the gap between the front view top edge and the
# plan view bottom edge equals _DIM_PAD.  The pv_zones.below strip occupies
# that gap, so _DIM_PAD must be at least as wide as the depth pv_below needs.
# If _DIM_PAD is shrunk below _est_pv_below_depth(), dim_width would silently
# overlap the front view rather than failing an allocate().
assert _DIM_PAD >= _est_pv_below_depth(), (
    f"_DIM_PAD ({_DIM_PAD}) is smaller than pv_below slot depth "
    f"({_est_pv_below_depth()}); bump _DIM_PAD or shrink the slot constants."
)


# ---------------------------------------------------------------------------
# Two-pass layout — Pass 1: annotation strip depth measurement (#131)
#
# font_size = 3.0 mm is a fixed page-mm constant, so all annotation depths
# are scale-independent and can be computed before choose_scale() is called.
# ---------------------------------------------------------------------------


def _est_bore_callout_width(
    holes, font_size: float = _FONT_SIZE, patterns=None, pad_around_text: float = 2.0
) -> float:
    """Estimate the maximum bore callout label width (page-mm) across all holes.

    Groups holes by machining spec (same as _annotate_holes), then estimates
    the HoleCallout token sequence width using a character-based formula.
    Includes the BoltCircle suffix ("EQ SP ON ø… BC") when patterns are supplied.
    Returns the label width only — elbow_dx and gap clearance are NOT included;
    callers that need the full strip depth should add those overheads separately.
    Returns 0.0 when the hole list is empty.
    *pad_around_text* should come from ``draft_preset(...).pad_around_text``.
    """
    if not holes:
        return 0.0
    groups: dict = {}
    for h in holes:
        groups.setdefault(_spec_key(h), []).append(h)

    # Map spec_key → BoltCircle so BoltCircle groups get their suffix estimated.
    bc_by_spec: dict = {}
    if patterns:
        for p in patterns:
            if isinstance(p, BoltCircle):
                bc_by_spec[_spec_key(p.holes[0])] = p

    h_fs = font_size
    # gap (inter-token spacing) and sym_w (geometry-symbol cell width) mirror
    # HoleCallout's own internal layout constants so the estimate matches what
    # the primitive actually strokes.  Variable text tokens are measured with
    # real glyph metrics (_text_width) rather than a character-count fudge (#31).
    gap = 0.45 * h_fs
    sym_w = h_fs
    pad = pad_around_text

    max_w = 0.0
    for spec_key, group in groups.items():
        rep = group[0]
        count = len(group) if len(group) > 1 else None
        through = rep.bottom == "through"
        step = rep.cbore or rep.spotface

        token_w: list[float] = []
        if count:
            token_w.append(_text_width(f"{count}×", h_fs))
        token_w.append(sym_w)  # ⌀ symbol
        token_w.append(_text_width(_fmt(rep.diameter), h_fs))
        if through:
            token_w.append(_text_width("THRU", h_fs))
        elif rep.depth:
            token_w.append(sym_w)  # depth symbol
            token_w.append(_text_width(_fmt(rep.depth), h_fs))
        if step:
            token_w.append(sym_w)  # counterbore/spotface symbol
            token_w.append(sym_w)  # ⌀
            token_w.append(_text_width(_fmt(step.diameter), h_fs))
            if step.depth:
                token_w.append(sym_w)  # depth symbol
                token_w.append(_text_width(_fmt(step.depth), h_fs))

        # BoltCircle suffix: "EQ SP ON ø{bc_dia} BC"
        bc = bc_by_spec.get(spec_key)
        if bc is not None:
            token_w.append(_text_width(f"EQ SP ON ø{_fmt(bc.diameter)} BC", h_fs))

        n = len(token_w)
        w = sum(token_w) + max(n - 1, 0) * gap + pad
        max_w = max(max_w, w)

    return max_w


@dataclass
class StripDepths:
    """Annotation strip depths (page-mm) computed before view positions are fixed.

    Drives the inter-view corridor widths in the two-pass layout (#131).
    """

    right: float  # horizontal corridor right of FV/PV → gap_fv_sv
    left: float  # horizontal corridor left of FV/PV


def _measure_strips(
    holes,
    patterns,
    n_steps: int,
    bb,
    font_size: float = _FONT_SIZE,
    arrow_length: float = 2.7,
    pad_around_text: float = 2.0,
) -> StripDepths:
    """Compute annotation strip depths from hole geometry (Pass 1 of #131).

    All annotation sizes are scale-independent because font_size is a fixed
    page-mm constant, so there is no circularity with choose_scale().
    *arrow_length* and *pad_around_text* should come from ``draft_preset(...)``.
    """
    bore_depth = _est_bore_callout_width(
        holes, font_size, patterns=patterns, pad_around_text=pad_around_text
    )
    # Add elbow clearance and leader-to-label gap so gap_fv_sv fully contains
    # the composed leader: elbow_dx (= draft.arrow_length) + gap
    # (= draft.pad_around_text), always present.
    if bore_depth > 0:
        bore_depth += pad_around_text + arrow_length
    right = max(_est_right_strip_depth(n_steps), bore_depth)
    left = max(_DIM_PAD, bore_depth)
    return StripDepths(right=right, left=left)


def _fits(
    x_size,
    y_size,
    z_size,
    scale,
    page_w,
    page_h,
    tb_w,
    n_steps: int = 0,
    strips: StripDepths | None = None,
    pack_iso_2d: bool = False,
) -> bool:
    """True if the 4-view layout fits the page at this scale.

    Default (``pack_iso_2d=False``) is the conservative row model used by
    automatic scale selection: the iso view is charged a column in the view row
    alongside the title block.  This deliberately over-reserves horizontal space,
    which keeps annotation-heavy parts on a sheet large enough to place all their
    dimensions rather than dropping some onto a tighter sheet.

    When ``pack_iso_2d=True`` — used when the caller fixes the page or scale —
    the iso is instead fitted into the largest empty rectangle the placement
    engine actually uses (:func:`_layout_geometry`), so it may occupy vertical
    headroom above the views rather than a row column.  A long, short part can
    then be enlarged onto the requested sheet (e.g. 2:1 on A3), where the row
    model would have under-scaled it.

    The title block occupies only the bottom ``_TB_H`` mm of the sheet, so the
    views may extend over it horizontally as long as they clear it vertically.
    """
    bbox_max = max(x_size, y_size, z_size)
    gap_fv_sv = max(_DIM_PAD, strips.right if strips else _est_right_strip_depth(n_steps))
    gap_left = max(_DIM_PAD, strips.left if strips else _DIM_PAD)
    h = _MARGIN + _DIM_PAD + y_size * scale + _DIM_PAD + z_size * scale + _DIM_PAD + _MARGIN
    if h > page_h:
        return False

    if not pack_iso_2d:
        # Conservative row model: iso + title block both charged to the row.
        w = (
            _MARGIN
            + gap_left
            + x_size * scale
            + gap_fv_sv
            + y_size * scale
            + _DIM_PAD
            + bbox_max * scale * _ISO_WIDTH_BUDGET
            + _DIM_PAD
            + tb_w
            + _MARGIN
        )
        if w <= page_w:
            return True
        views_bottom = max(0.0, (page_h - h) / 2) + _MARGIN + _DIM_PAD
        # When views clear the title block row, the iso sits above it and the
        # title block no longer constrains horizontal space — drop tb_w from w.
        return w - tb_w <= page_w and views_bottom >= _MARGIN + _TB_H

    # 2D packing: views + title block fit the row; the iso fits leftover space.
    w_views_tb = (
        _MARGIN
        + gap_left
        + x_size * scale
        + gap_fv_sv
        + y_size * scale
        + _DIM_PAD
        + tb_w
        + _MARGIN
    )
    if w_views_tb > page_w:
        views_bottom = max(0.0, (page_h - h) / 2) + _MARGIN + _DIM_PAD
        if not (w_views_tb - tb_w <= page_w and views_bottom >= _MARGIN + _TB_H):
            return False
    g = _layout_geometry(x_size, y_size, z_size, scale, page_w, page_h, tb_w, strips, n_steps)
    if not g.iso_valid:
        return False
    iso_fit = min(g.iso_right - g.iso_left, g.iso_top - g.iso_bottom)
    return iso_fit >= _ISO_MIN_FIT_FRAC * g.iso_natural


def choose_scale(
    x_size: float,
    y_size: float,
    z_size: float,
    n_steps: int = 0,
    scale=None,
    page=None,
    strips: StripDepths | None = None,
) -> tuple:
    """Return (SCALE, PAGE_W, PAGE_H, TB_W) for a 4-view layout.

    Layout columns: [front(x×z)] [side(y×z)] [iso(~0.7*max)] [title block].
    Rows: [plan(x×y)] above [front/side].
    Tries ISO A-series pages (A4→A3→A2→A1→A0) at preferred scales, including
    ISO 5455 enlargement scales (10:1, 5:1) so small parts get legible views.
    A4 uses a 120 mm title block; A3+ use 150 mm. The title block only
    constrains row width when the view rows would overlap it vertically.

    Args:
        scale: optional fixed scale factor (e.g. ``5`` for 5:1, ``0.5`` for
            1:2); the page is then chosen as the smallest A-series sheet that
            fits.
        page: optional fixed page — an ISO name (``"A3"``), ``"WIDTHxHEIGHT"``
            in mm, or a ``(width, height)`` tuple; the scale is then chosen as
            the largest standard scale that fits. When both ``scale`` and
            ``page`` are given they are used as-is (a warning is logged if the
            layout does not fit).
    """
    if scale is not None and float(scale) <= 0:
        raise ValueError(f"scale must be positive, got {scale!r}")
    if scale is not None and page is not None:
        pw, ph, tb = _parse_page(page)
        if not _fits(
            x_size,
            y_size,
            z_size,
            float(scale),
            pw,
            ph,
            tb,
            n_steps=n_steps,
            strips=strips,
            pack_iso_2d=True,
        ):
            _log.warning(
                "Requested scale %s on %s page may not fit the 4-view layout", scale, page
            )
        return float(scale), pw, ph, tb
    # When the caller fixes the page or scale, pack the iso into 2D space so the
    # largest scale that genuinely fits the requested sheet is chosen (the iso
    # may sit in vertical headroom).  Automatic selection stays on the
    # conservative row model, which reserves enough space for all annotations.
    if page is not None:
        pw, ph, tb = _parse_page(page)
        candidates = [(s, pw, ph, tb) for s in _SCALES]
        pack_iso_2d = True
    elif scale is not None:
        candidates = [(float(scale), pw, ph, _tb_width(pw)) for pw, ph in _PAGE_SIZES.values()]
        pack_iso_2d = True
    else:
        candidates = _LADDER
        pack_iso_2d = False
    for cand in candidates:
        if _fits(
            x_size, y_size, z_size, *cand, n_steps=n_steps, strips=strips, pack_iso_2d=pack_iso_2d
        ):
            return cand
    _log.warning(
        "No layout fits %.0f × %.0f × %.0f mm; falling back to %s",
        x_size,
        y_size,
        z_size,
        candidates[-1],
    )
    return candidates[-1]


# ---------------------------------------------------------------------------
# Shared analysis step
# ---------------------------------------------------------------------------


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


def _layout_geometry(x_size, y_size, z_size, scale, page_w, page_h, tb_w, strips, n_steps=0):
    """Compute the 4-view layout geometry for a part at a given scale/page.

    Single source of truth shared by scale selection (:func:`_fits`) and view
    placement (:func:`_analyse`): the orthographic FV/PV/SV view centres and
    half-sizes, the annotation-strip gaps, and the largest empty rectangle the
    isometric view is fitted into.  Returns a :class:`SimpleNamespace`.

    When *strips* is ``None`` the annotation-corridor gaps fall back to the
    step-count estimate (used during scale selection before strips are
    measured); otherwise the measured strip depths are used.
    """
    margin = _MARGIN
    DIM_PAD = _DIM_PAD
    bbox_max = max(x_size, y_size, z_size)
    gap_fv_sv = max(DIM_PAD, strips.right if strips else _est_right_strip_depth(n_steps))
    gap_left = max(DIM_PAD, strips.left if strips else DIM_PAD)

    fv_hw = x_size * scale / 2
    fv_hh = z_size * scale / 2
    pv_hh = y_size * scale / 2
    sv_hw = y_size * scale / 2

    total_h = 2 * margin + 3 * DIM_PAD + z_size * scale + y_size * scale
    y_offset = max(0.0, (page_h - total_h) / 2)

    total_content_w = (
        gap_left
        + gap_fv_sv
        + x_size * scale
        + y_size * scale
        + 2 * DIM_PAD
        + bbox_max * scale * _ISO_WIDTH_BUDGET
    )
    x_offset = max(0.0, (page_w - 2 * margin - tb_w - total_content_w) / 2)

    FV_X = margin + x_offset + gap_left + fv_hw
    FV_Y = y_offset + margin + DIM_PAD + fv_hh
    PV_X = FV_X
    PV_Y = FV_Y + fv_hh + DIM_PAD + pv_hh
    SV_X = FV_X + fv_hw + gap_fv_sv + sv_hw
    SV_Y = FV_Y
    sv_right = SV_X + sv_hw + DIM_PAD
    sv_right_wall = (
        (page_w - margin) if (PV_Y - pv_hh) > (margin + _TB_H) else (page_w - tb_w - margin)
    )

    drawable = (margin, margin, page_w - margin, page_h - margin)
    obstacles = [
        (
            FV_X - fv_hw - DIM_PAD,
            FV_Y - fv_hh - DIM_PAD,
            FV_X + fv_hw + DIM_PAD,
            FV_Y + fv_hh + DIM_PAD,
        ),
        (
            PV_X - fv_hw - DIM_PAD,
            PV_Y - pv_hh - DIM_PAD,
            PV_X + fv_hw + DIM_PAD,
            PV_Y + pv_hh + DIM_PAD,
        ),
        (
            SV_X - sv_hw - DIM_PAD,
            SV_Y - fv_hh - DIM_PAD,
            SV_X + sv_hw + DIM_PAD,
            SV_Y + fv_hh + DIM_PAD,
        ),
        (page_w - tb_w - 11 - DIM_PAD, margin, page_w - 11 + DIM_PAD, 11 + _TB_H + DIM_PAD),
    ]
    iso_left, iso_bottom, iso_right, iso_top = _largest_empty_rect(drawable, obstacles)
    # _largest_empty_rect falls back to the full drawable when the obstacles
    # leave no genuine gap; detect that (rect overlaps an obstacle) so callers
    # can treat "no room for the iso" as not-fitting rather than a huge phantom.
    iso_valid = not any(
        iso_left < o[2] and o[0] < iso_right and iso_bottom < o[3] and o[1] < iso_top
        for o in obstacles
    )

    return SimpleNamespace(
        x_offset=x_offset,
        fv_hw=fv_hw,
        fv_hh=fv_hh,
        pv_hh=pv_hh,
        sv_hw=sv_hw,
        FV_X=FV_X,
        FV_Y=FV_Y,
        PV_X=PV_X,
        PV_Y=PV_Y,
        SV_X=SV_X,
        SV_Y=SV_Y,
        sv_right=sv_right,
        sv_right_wall=sv_right_wall,
        iso_left=iso_left,
        iso_bottom=iso_bottom,
        iso_right=iso_right,
        iso_top=iso_top,
        ISO_X=(iso_left + iso_right) / 2,
        ISO_Y=(iso_bottom + iso_top) / 2,
        iso_valid=iso_valid,
        iso_natural=bbox_max * scale * _ISO_WIDTH_BUDGET,
    )


def _analyse(step_file, title, number, tolerance, drawn_by, out, scale=None, page=None, pmi="off"):
    """Load STEP or use a build123d Shape, analyse geometry, compute layout.

    Returns SimpleNamespace.
    """
    if isinstance(step_file, Shape):
        part = step_file
        src = "build123d object"
    else:
        part = _import_step(step_file)
        src = str(step_file)
    # AP242 STEP files carry PMI presentation geometry (annotation-plane
    # border wires, leader curves) beside the solid; left in, it draws as
    # phantom rectangles in every view and inflates the bounding box —
    # corrupting the scale choice and the envelope dimensions. The drawing
    # is of the solids.
    solids = part.solids()
    if solids:
        body = solids[0] if len(solids) == 1 else Compound(children=list(solids))
        if body.bounding_box().size != part.bounding_box().size or len(part.edges()) != len(
            body.edges()
        ):
            _log.info(
                "Dropping non-solid geometry from %s (PMI presentation data)",
                src,
            )
        part = body

    # Semantic PMI extraction (AP242 only; separate read-only pass).
    pmi_records: list = []
    if pmi != "off" and not isinstance(step_file, Shape):
        try:
            from draftwright.pmi import extract_pmi

            pmi_records = extract_pmi(step_file)
        except Exception as exc:
            _log.warning("PMI extraction failed: %s", exc)

    bb = part.bounding_box()
    x_size = bb.max.X - bb.min.X
    y_size = bb.max.Y - bb.min.Y
    z_size = bb.max.Z - bb.min.Z
    cx = (bb.min.X + bb.max.X) / 2
    cy = (bb.min.Y + bb.max.Y) / 2
    cz = (bb.min.Z + bb.max.Z) / 2
    bbox_max = max(x_size, y_size, z_size)

    _log.info("Loaded %s  bbox: %.2f × %.2f × %.2f mm", src, x_size, y_size, z_size)

    z_cyls, cross_cyls = analyse_cylinders(part)
    # Partial (fillet) faces are not features: they would pollute the OD,
    # the bore leaders, and the rotational classification alike (#81)
    full_z = _full_cyls(z_cyls)
    z_diams = dedup_diams(full_z)
    cross_diams = dedup_diams(_full_cyls(cross_cyls))

    _log.info("Z-axis diameters: %s", z_diams)
    if cross_diams:
        _log.info("Cross-hole diams: %s", cross_diams)

    od_cyl = max((c for c in full_z if c["external"]), key=lambda c: c["diameter"], default=None)
    od_diam = None
    if od_cyl:
        # Snap to the dedup_diams representative so comparisons against
        # z_diams entries (bore-leader exclusion, labels) are exact even if
        # the cylinder records ever carry unrounded OCCT diameters (#86)
        raw_od = od_cyl["diameter"]
        od_diam = min(z_diams, key=lambda d: abs(d - raw_od))
    od_axis_offset = (
        math.hypot(od_cyl["axis_xyz"][0] - cx, od_cyl["axis_xyz"][1] - cy) if od_cyl else 0.0
    )
    is_rotational = _is_rotational(x_size, y_size, od_diam, od_axis_offset)
    if z_diams and not is_rotational:
        _log.info("Part classified prismatic; skipping OD/centreline/bore annotations")

    face_zs = analyse_face_levels(part, min_area_frac=_STEP_MIN_AREA_FRAC)
    step_zs = [z for z in face_zs if z > bb.min.Z + 0.6 and z < bb.max.Z - 0.6]

    # Pass 1 (two-pass layout, #131): measure annotation strip depths before
    # view positions are fixed.  font_size=3.0 is a fixed page-mm constant so
    # all annotation sizes are scale-independent — no circularity.
    # Construct the same draft preset used later in build_drawing() to read
    # arrow_length and pad_around_text from their authoritative source rather
    # than re-stating them as magic literals in the estimators.
    _draft_est = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
    _arrow_length = _draft_est.arrow_length
    _pad_around_text = _draft_est.pad_around_text
    holes = find_holes(part, cyls=(z_cyls, cross_cyls))
    patterns = find_hole_patterns(holes)

    # Choose scale/page, iterating so the reserved step corridor matches the
    # number of steps the legibility gate will actually place (#1) — not the raw
    # face count. Otherwise a part with many sub-legible faces (e.g. a staircase
    # with 15 tiny treads) reserves a phantom step ladder that blocks a larger
    # scale. Seed conservatively (all faces), then re-gate at the chosen scale;
    # converges in a couple of rounds.
    n_for_sizing = len(step_zs)
    strips_i = None
    for _ in range(3):
        strips_i = _measure_strips(
            holes,
            patterns,
            n_for_sizing,
            bb,
            arrow_length=_arrow_length,
            pad_around_text=_pad_around_text,
        )
        SCALE, PAGE_W, PAGE_H, TB_W = choose_scale(
            x_size, y_size, z_size, n_steps=n_for_sizing, scale=scale, page=page, strips=strips_i
        )
        n_next = len(_legible_steps(step_zs, bb.min.Z, SCALE)[0])
        if n_next == n_for_sizing:
            break
        n_for_sizing = n_next
    if scale is not None:
        auto_scale, _, _, _ = choose_scale(
            x_size, y_size, z_size, n_steps=n_for_sizing, scale=None, page=page, strips=strips_i
        )
        if SCALE < auto_scale:
            min_dim = min(x_size, y_size, z_size)
            min_view = min_dim * SCALE
            if min_view < _MIN_VIEW_MM:
                safe = _MIN_VIEW_MM / min_dim
                raise ValueError(
                    f"scale {SCALE!r} projects the smallest part dimension "
                    f"({min_dim:.0f} mm) to {min_view:.1f} mm — "
                    f"annotation geometry degenerates below {_MIN_VIEW_MM:.0f} mm "
                    f"(OCCT Standard_DomainError / SIGABRT). "
                    f"Use scale ≥ {safe:.3g} or omit --scale for automatic selection."
                )
    DIM_PAD = _DIM_PAD
    margin = _MARGIN
    # Refine: apply the same legibility gate _auto_annotate uses for dim_step.
    n_steps = len(_legible_steps(step_zs, bb.min.Z, SCALE)[0])
    strips = _measure_strips(
        holes,
        patterns,
        n_steps,
        bb,
        arrow_length=_arrow_length,
        pad_around_text=_pad_around_text,
    )
    # View positions + iso empty-rectangle, shared with scale selection (_fits)
    # via _layout_geometry so placement and fit never diverge (#11).  _fit_iso_view
    # later scales the iso to fill its rectangle.
    _g = _layout_geometry(x_size, y_size, z_size, SCALE, PAGE_W, PAGE_H, TB_W, strips, n_steps)
    fv_hw = _g.fv_hw
    fv_hh = _g.fv_hh
    pv_hh = _g.pv_hh
    sv_hw = _g.sv_hw
    x_offset = _g.x_offset
    FV_X = _g.FV_X
    FV_Y = _g.FV_Y
    PV_X = _g.PV_X
    PV_Y = _g.PV_Y
    SV_X = _g.SV_X
    SV_Y = _g.SV_Y
    sv_right = _g.sv_right
    sv_right_wall = _g.sv_right_wall
    iso_left_limit = _g.iso_left
    iso_bottom_limit = _g.iso_bottom
    iso_right_limit = _g.iso_right
    iso_top_limit = _g.iso_top
    ISO_X = _g.ISO_X
    ISO_Y = _g.ISO_Y

    # ------------------------------------------------------------------
    # Strip / zone construction.
    # Phase 1: defines regions only — annotation functions still use their
    # own hard-coded offsets.  Later phases will route each annotation
    # through strip.allocate().  The iso view's outer limits are conservative
    # here (PAGE_H - margin / iso_right_limit); _auto_annotate() tightens
    # them once the iso has been projected.
    fv_right_edge = FV_X + fv_hw
    fv_left_edge = FV_X - fv_hw
    fv_top_edge = FV_Y + fv_hh
    fv_bottom_edge = FV_Y - fv_hh
    pv_right_edge = PV_X + fv_hw  # plan has the same X half-width as front
    pv_left_edge = PV_X - fv_hw
    pv_top_edge = PV_Y + pv_hh
    pv_bottom_edge = PV_Y - pv_hh  # = fv_top_edge + DIM_PAD
    sv_top_edge = SV_Y + fv_hh  # side view has the same Z height as front
    # Outer limit for fv/pv right strips: must not enter the side view.
    sv_left_edge = SV_X - sv_hw  # = fv_right_edge + gap_fv_sv

    fv_zones = ViewZones(
        right=Strip(fv_right_edge, sv_left_edge, direction=1),
        left=Strip(fv_left_edge, margin, direction=-1),
        # Stop the front-view 'above' strip short of pv_bottom_edge by the
        # slack the pv_below slot leaves in the gap, derived (not re-typed) so
        # it tracks _DIM_PAD and the slot constants.
        above=Strip(fv_top_edge, pv_bottom_edge - (_DIM_PAD - _est_pv_below_depth()), direction=1),
        below=Strip(fv_bottom_edge, margin, direction=-1),
    )
    pv_zones = ViewZones(
        # Outer limit = sv_left_edge (not iso_right_limit) so bore callouts in
        # the plan view are bounded by the same hard wall as the FV right strip,
        # preventing labels from crossing dim_locy extension lines in the side
        # view.  gap_fv_sv is sized by _measure_strips to accommodate the widest
        # callout, so well-estimated labels will always fit within this bound.
        right=Strip(pv_right_edge, sv_left_edge, direction=1),
        left=Strip(pv_left_edge, margin, direction=-1),
        above=Strip(pv_top_edge, PAGE_H - margin, direction=1),
        # gap_fv_pv = _DIM_PAD; pv_below needs _est_pv_below_depth() mm,
        # leaving (_DIM_PAD - _est_pv_below_depth()) mm slack (assert above).
        below=Strip(pv_bottom_edge, fv_top_edge, direction=-1),
    )
    sv_bottom_edge = SV_Y - fv_hh  # same as fv_bottom_edge; side and front share Z height
    sv_zones = ViewZones(
        # sv_right already includes DIM_PAD; anchor here so the strip never
        # places annotations inside that gap
        right=Strip(sv_right, sv_right_wall, direction=1),
        left=None,  # immediately abuts the front view's right edge
        above=Strip(sv_top_edge, PAGE_H - margin, direction=1),
        below=Strip(sv_bottom_edge, margin, direction=-1),
    )

    page_label = {297: "A4", 420: "A3", 594: "A2", 841: "A1", 1189: "A0"}.get(
        int(PAGE_W), f"{PAGE_W:.0f}mm"
    )
    _log.info(
        "Scale %s:1  page %s  FV(%.0f,%.0f) PV(%.0f,%.0f) SV(%.0f,%.0f) ISO(%.0f,%.0f)",
        SCALE,
        page_label,
        FV_X,
        FV_Y,
        PV_X,
        PV_Y,
        SV_X,
        SV_Y,
        ISO_X,
        ISO_Y,
    )

    return SimpleNamespace(
        part=part,
        bb=bb,
        x_size=x_size,
        y_size=y_size,
        z_size=z_size,
        cx=cx,
        cy=cy,
        cz=cz,
        bbox_max=bbox_max,
        holes=holes,
        patterns=patterns,
        z_diams=z_diams,
        cross_diams=cross_diams,
        cyls=(z_cyls, cross_cyls),
        od_diam=od_diam,
        is_rotational=is_rotational,
        step_zs=step_zs,
        sv_right=sv_right,
        iso_right_limit=iso_right_limit,
        SCALE=SCALE,
        PAGE_W=PAGE_W,
        PAGE_H=PAGE_H,
        TB_W=TB_W,
        DIM_PAD=DIM_PAD,
        margin=margin,
        x_offset=x_offset,
        FV_X=FV_X,
        FV_Y=FV_Y,
        PV_X=PV_X,
        PV_Y=PV_Y,
        SV_X=SV_X,
        SV_Y=SV_Y,
        ISO_X=ISO_X,
        ISO_Y=ISO_Y,
        iso_left_limit=iso_left_limit,
        iso_bottom_limit=iso_bottom_limit,
        iso_top_limit=iso_top_limit,
        # View half-extents in page units (convenient for strip arithmetic)
        fv_hw=fv_hw,
        fv_hh=fv_hh,
        pv_hh=pv_hh,
        sv_hw=sv_hw,
        # Strip / zone layout model (Phase 1 — regions defined, not yet used)
        fv_zones=fv_zones,
        pv_zones=pv_zones,
        sv_zones=sv_zones,
        step_file=step_file,
        title=title,
        number=number,
        tolerance=tolerance,
        drawn_by=drawn_by,
        out=out,
        pmi=pmi_records,
        pmi_mode=pmi,
    )


# ---------------------------------------------------------------------------
# Drawing builder (composable; make_drawing == build_drawing + export)
# ---------------------------------------------------------------------------


class Drawing:
    """A composable technical drawing — the editable form of :func:`make_drawing`.

    A ``Drawing`` holds the projected views, the annotation list, and per-view
    coordinate helpers. :func:`build_drawing` returns one pre-populated with the
    standard 4-view layout and automatic dimensions; you then add or remove
    annotations, add section/auxiliary views, and finally :meth:`export`.

    Attributes:
        scale: drawing scale factor (e.g. ``2.0`` for 2:1).
        page_w, page_h: sheet size in mm.
        tb_w: title-block width in mm.
        draft: the shared ``Draft`` preset used by the automatic annotations.
        look_at: scaled centroid ``(x, y, z)`` — the default ``look_at`` and a
            building block for custom view cameras (see :meth:`add_view`).
        dist: orthographic camera distance in scaled space.
        centroid: unscaled centroid ``(x, y, z)``.
        views: ``{name: (visible_compound, hidden_compound_or_None)}``.
        annotations: ordered list of annotation objects (mutable).
        part: the source solid, when known — enables the feature-coverage lint.

    The constructor also accepts ``cyls``, a precomputed
    ``analyse_cylinders(part)`` result (cached privately; computed lazily on
    first :meth:`lint` otherwise).
    """

    def __init__(
        self,
        *,
        scale,
        page_w,
        page_h,
        tb_w,
        draft,
        look_at,
        dist,
        centroid,
        out,
        part=None,
        cyls=None,
    ):
        self.scale = scale
        self.part = part
        self._cyl_cache = cyls
        self.page_w = page_w
        self.page_h = page_h
        self.tb_w = tb_w
        self.draft = draft
        self.look_at = look_at
        self.dist = dist
        self.centroid = centroid
        self.out = out
        self.views: dict = {}
        self.annotations: list = []
        self._coords: dict = {}
        self._named: dict = {}
        self.svg_path: str | None = None
        self.dxf_path: str | None = None
        self._analysis: SimpleNamespace | None = None
        # Lint issues found while building (e.g. annotations the layout had to
        # drop). Recorded here so :meth:`lint` can surface them — a dropped
        # feature must never be silent. Diameters dropped by the per-view
        # callout cap are tracked separately so :meth:`lint` can suppress the
        # redundant feature_not_dimensioned for them. Both are reset at the
        # top of :func:`_auto_annotate` so re-annotation does not accumulate.
        self._build_issues: list = []
        self._dropped_callout_diams: list = []

    # -- views ----------------------------------------------------------------
    def add_view(self, name, shape, camera, up, position, *, look_at=None, scaled=False):
        """Project ``shape`` from ``camera`` and place it at ``position``.

        Args:
            name: view name (key in :attr:`views`); also used for coordinate lookups.
            shape: a build123d ``Shape`` to project. Given in world (unscaled)
                coordinates and scaled internally unless ``scaled=True``.
            camera, up, look_at: viewport parameters in **scaled** space (the same
                convention the standard views use). ``look_at`` defaults to
                :attr:`look_at` (the scaled centroid). Compose custom cameras from
                :attr:`look_at` and :attr:`dist`.
            position: ``(x, y)`` page position for the view centre, in mm.
            scaled: set ``True`` if ``shape`` is already scaled by :attr:`scale`.

        Returns:
            The :class:`ViewCoordinates` for this view (also via :meth:`coords`),
            for mapping world points to page coordinates.
        """
        la = self.look_at if look_at is None else look_at
        shape_s = shape if scaled else shape.scale(self.scale)
        vis, hid = shape_s.project_to_viewport(camera, up, la)
        vl, hl = list(vis), list(hid)
        if not vl and not hl:
            raise ValueError(
                f"project_to_viewport returned empty geometry for view {name!r} "
                f"(camera {camera}) — check the camera position and look_at."
            )
        loc = Location((position[0], position[1], 0))
        placed = Compound(children=vl).locate(loc)
        placed_hid = Compound(children=hl).locate(loc) if hl else None
        self.views[name] = (placed, placed_hid)
        axes = view_axes(camera, up, la)
        cx, cy, cz = la[0] / self.scale, la[1] / self.scale, la[2] / self.scale
        self._coords[name] = ViewCoordinates(
            axes, position[0], position[1], cx, cy, cz, self.scale
        )
        _log.info("  %s: %d visible / %d hidden", name, len(vl), len(hl))
        return self._coords[name]

    def coords(self, view):
        """Return the :class:`ViewCoordinates` for a named view."""
        return self._coords[view]

    def at(self, view, x, y, z):
        """Map a world point to a page point ``(px, py, 0)`` in ``view``."""
        px, py = self._coords[view].pp(x, y, z)
        return (px, py, 0.0)

    # -- annotations ----------------------------------------------------------
    def add(self, obj, name=None):
        """Register an annotation so lint and export include it; returns ``obj``.

        Re-using an existing ``name`` replaces the previously added object (it is
        dropped from :attr:`annotations`), so a name always maps to one object.
        """
        if name is not None and name in self._named:
            self.annotations.remove(self._named[name])
        annotate(obj, name)
        self.annotations.append(obj)
        if name is not None:
            self._named[name] = obj
        return obj

    def remove(self, name):
        """Remove a previously named annotation. Raises ``KeyError`` if absent."""
        obj = self._named.pop(name, None)
        if obj is None:
            raise KeyError(f"no annotation named {name!r}")
        self.annotations.remove(obj)
        return obj

    def clear_annotations(self, keep=("title_block",)):
        """Remove all annotations except those named in *keep* (#74).

        Wholesale removal that does not depend on the automatic naming
        scheme — ``dwg.clear_annotations()`` strips every automatic dimension,
        leader, and centreline but keeps the title block.

        Returns:
            The list of removed annotation objects.
        """
        keep_set = set(keep)
        kept_named = {n: o for n, o in self._named.items() if n in keep_set}
        kept_ids = {id(o) for o in kept_named.values()}
        removed = [o for o in self.annotations if id(o) not in kept_ids]
        self.annotations = [o for o in self.annotations if id(o) in kept_ids]
        self._named = kept_named
        return removed

    def _record_build_issue(self, severity, code, message):
        """Record a lint issue discovered during construction (e.g. an
        annotation the layout had to drop). Surfaced by :meth:`lint` so a
        dropped feature is never silent."""
        self._build_issues.append(LintIssue(severity=severity, code=code, message=message))

    # -- output ---------------------------------------------------------------
    def lint(self):
        """Lint all annotations against all views; returns the list of issues.

        When :attr:`part` is set, also runs :func:`lint_feature_coverage`.
        Build-time drops recorded via :meth:`_record_build_issue` are included.
        """
        set_page(self.page_w, self.page_h, margin=10)
        view_shapes = [vis for vis, _ in self.views.values()]
        # Most annotations are at sheet scale, but a non-sheet-scale view (the
        # enlarged detail view, #42) tags its dims with `_dw_scale`. Lint each
        # scale group with its own drawing_scale so label-vs-measured is correct
        # per view. The common single-scale case is byte-identical to before.
        by_scale: dict = {}
        for ann in self.annotations:
            by_scale.setdefault(getattr(ann, "_dw_scale", self.scale), []).append(ann)
        if len(by_scale) <= 1:
            issues = lint_drawing(
                self.annotations, drawing_scale=self.scale, view_shapes=view_shapes
            )
        else:
            issues = []
            for _scale, _anns in by_scale.items():
                issues += lint_drawing(_anns, drawing_scale=_scale, view_shapes=view_shapes)
        if self.part is not None:
            if self._cyl_cache is None:
                self._cyl_cache = analyse_cylinders(self.part)
            issues += lint_feature_coverage(
                self.part,
                self.annotations,
                cyls=self._cyl_cache,
                exclude=self._dropped_callout_diams,
            )
        issues += list(self._build_issues)
        return issues

    def lint_summary(self) -> dict:
        """Aggregate :meth:`lint` into a JSON-friendly quality summary.

        Gives a non-interactive caller (a script, or an LLM via the API) a
        single signal to gate and optimise on without rendering the SVG:

        - ``passed`` — no error-severity issues;
        - ``score`` — coarse 0–1 quality heuristic (see ``_SCORE_*``);
        - ``errors`` / ``warnings`` / ``infos`` — counts by severity;
        - ``by_code`` — per-check counts;
        - ``geometry_issues`` — count of standards/geometry-correctness issues
          as opposed to pure layout (see ``_GEOMETRY_AWARE_CODES``);
        - ``issues`` — the full list, each as a plain dict.
        """
        issues = self.lint()
        errors = sum(1 for i in issues if i.severity == "error")
        warnings = sum(1 for i in issues if i.severity == "warning")
        infos = sum(1 for i in issues if i.severity == "info")
        by_code: dict[str, int] = {}
        for i in issues:
            by_code[i.code] = by_code.get(i.code, 0) + 1
        score = max(
            0.0,
            1.0 - errors * _SCORE_ERROR_PENALTY - warnings * _SCORE_WARNING_PENALTY,
        )
        return {
            "passed": errors == 0,
            "score": score,
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
            "by_code": by_code,
            "geometry_issues": sum(1 for i in issues if i.code in _GEOMETRY_AWARE_CODES),
            "issues": [
                {
                    "severity": i.severity,
                    "code": i.code,
                    "message": i.message,
                    "location": i.location,
                }
                for i in issues
            ],
        }

    def export(self, out=None):
        """Lint, then write SVG and DXF. Returns ``(svg_path, dxf_path)``."""
        out = out if out is not None else self.out
        for _ext in (".svg", ".dxf"):
            if out.endswith(_ext):
                out = out[: -len(_ext)]
                break

        issues = self.lint()
        if issues:
            _log.warning("Lint issues:")
            for iss in issues:
                _log.warning("  [%s] %s: %s", iss.severity, iss.code, iss.message)
        else:
            _log.info("Lint: OK")

        blk = Color(0, 0, 0)
        grey = Color(0.5, 0.5, 0.5)
        blue = Color(0, 0.2, 0.7)

        svg = ExportSVG(margin=10)
        svg.add_layer("part", line_color=blk, line_weight=0.5)
        svg.add_layer("hidden", line_color=grey, line_weight=0.25, line_type=LineType.HIDDEN)
        svg.add_layer("dims", line_color=blue, fill_color=blue, line_weight=0.05)
        self._add_shapes(svg)
        svg_path = out + ".svg"
        svg.write(svg_path)
        fix_svg_page_size(svg_path, self.page_w, self.page_h)
        n_arcs = sanitize_svg_arcs(svg_path)
        if n_arcs:
            _log.info("Rewrote %d degenerate (near-zero-radius) arc(s) as line segments", n_arcs)
        _log.info("SVG → %s", svg_path)

        dxf = ExportDXF()
        dxf.add_layer("part", line_weight=0.5)
        dxf.add_layer("hidden", line_weight=0.25)
        dxf.add_layer("dims", line_weight=0.05)
        self._add_shapes(dxf)
        dxf_path = out + ".dxf"
        dxf.write(dxf_path)
        _log.info("DXF → %s", dxf_path)

        self.svg_path = svg_path
        self.dxf_path = dxf_path
        return svg_path, dxf_path

    def export_pdf(self, out=None) -> str:
        """Write a PDF rendered from the SVG.  Requires ``cairosvg`` (install with
        ``pip install draftwright[pdf]``).  Calls :meth:`export` first if the SVG
        hasn't been written yet.  Returns the PDF path."""
        try:
            import cairosvg
        except ImportError as exc:
            raise ImportError(
                "PDF export requires cairosvg. Install it with:  pip install draftwright[pdf]"
            ) from exc

        svg_path: str
        if not hasattr(self, "svg_path") or self.svg_path is None:
            svg_path, _ = self.export(out=out)
        else:
            svg_path = self.svg_path
        pdf_path = svg_path[:-4] + ".pdf" if svg_path.endswith(".svg") else svg_path + ".pdf"
        cairosvg.svg2pdf(url=svg_path, write_to=pdf_path)
        _log.info("PDF → %s", pdf_path)
        return pdf_path

    def _add_shapes(self, exporter):
        """Add every view layer and annotation to *exporter* with error context."""
        for name, (vis, hid) in self.views.items():
            _export_shape(exporter, vis, "part", f"view {name!r}")
            if hid:
                _export_shape(exporter, hid, "hidden", f"view {name!r}")
        for ann in self.annotations:
            label = getattr(ann, "label", "") or type(ann).__name__
            _export_shape(exporter, ann, "dims", f"annotation {label!r}")


def _elements(shape):
    """Decompose *shape* for export retry: faces plus any loose edges."""
    faces = list(shape.faces())
    if not faces:
        return list(shape.edges())
    owned = {e for f in faces for e in f.edges()}
    return faces + [e for e in shape.edges() if e not in owned]


def _export_shape(exporter, shape, layer, ctx):
    """Add *shape* to *exporter*, degrading element-by-element on failure.

    build123d's exporters abort the whole export on the first edge whose
    curve cannot be approximated (a bare ``AssertionError`` from OCCT, #83).
    Instead, drop only the offending elements with a warning naming the
    view/layer, and raise (with that context) only if nothing exported.

    ``ExportSVG.add_shape`` is atomic — it appends converted elements only
    after the whole shape succeeds — so the shape is tried in one call first.
    ``ExportDXF`` writes edge-by-edge as it converts, so a mid-shape failure
    would leave partial output that a blind retry duplicates; for it (and any
    unknown exporter) every element is added individually from the start.
    """
    first_err = None
    if isinstance(exporter, ExportSVG):
        try:
            exporter.add_shape(shape, layer=layer)
            return
        except Exception as exc:
            first_err = exc
            _log.warning(
                "%s (layer %r) failed to export as one shape: %s — retrying element-wise",
                ctx,
                layer,
                exc,
            )
    elements = _elements(shape)
    skipped = 0
    for element in elements:
        try:
            exporter.add_shape(element, layer=layer)
        except Exception as exc:
            first_err = first_err or exc
            skipped += 1
            _log.debug("%s (layer %r): element failed to convert: %s", ctx, layer, exc)
    if skipped == len(elements) and first_err is not None:
        raise RuntimeError(f"{ctx} (layer {layer!r}): nothing could be exported") from first_err
    if skipped:
        _log.warning(
            "%s (layer %r): skipped %d of %d elements that failed to convert",
            ctx,
            layer,
            skipped,
            len(elements),
        )


def _auto_annotate(dwg, a):
    """Add the standard automatic dimensions, centrelines, and title block."""
    draft = dwg.draft
    # Idempotent: clear build-time lint state so a second annotation pass does
    # not accumulate duplicate drop records.
    dwg._build_issues = []
    dwg._dropped_callout_diams = []

    def FX(x):
        return a.FV_X + (x - a.cx) * a.SCALE

    def FZ(z):
        return a.FV_Y + (z - a.cz) * a.SCALE

    def SX(y):
        return a.SV_X + (y - a.cy) * a.SCALE

    def SZ(z):
        return a.SV_Y + (z - a.cz) * a.SCALE

    def PX(x):
        return a.PV_X + (x - a.cx) * a.SCALE

    def PY(y):
        return a.PV_Y + (y - a.cy) * a.SCALE

    # Tighten right-strip outer_limits to the actual iso view left edge now
    # that the iso has been projected and fitted.  Always apply so that any
    # future allocations are bounded; warn when the cursor has already passed
    # the limit (dims already placed may overlap the iso view).
    _iso_x0, _iso_y0, _, _iso_y1 = _iso_bbox(dwg)
    _iso_x_limit = _iso_x0 - 4
    # Only tighten a right strip when the iso shares the strip's y-range: a strip
    # that abuts the iso horizontally would otherwise lose annotation space, while
    # one sitting entirely above/below the iso (e.g. the SV strip when the iso is
    # in an upper-right zone) must keep its full width — capping it could push the
    # outer_limit below the strip anchor and break all its allocations.
    _right_strips = []
    for _rs, _y0, _y1 in (
        (a.fv_zones.right, a.FV_Y - a.fv_hh, a.FV_Y + a.fv_hh),
        (a.pv_zones.right, a.PV_Y - a.pv_hh, a.PV_Y + a.pv_hh),
        (a.sv_zones.right, a.SV_Y - a.fv_hh, a.SV_Y + a.fv_hh),
    ):
        if _y0 < _iso_y1 and _iso_y0 < _y1:
            _right_strips.append(_rs)
    for _rs in _right_strips:
        _rs.outer_limit = min(_rs.outer_limit, _iso_x_limit)
        if _rs._cursor >= _iso_x_limit:
            _log.warning(
                "right-strip cursor %.1f >= iso_x limit %.1f: right-strip dims"
                " may overlap iso view (iso view overflows into annotation zone)",
                _rs._cursor,
                _iso_x_limit,
            )

    # Height dimensions stack to the right of the front view, smallest nearest
    # the part and the overall height OUTERMOST so extension lines nest without
    # leapfrogging (#staircase review). _right_ladder tracks the witness x; each
    # successive dim witnesses from the previous dim's line. The step dims are
    # placed first (inner) below; the overall height is placed last (outer).
    _right_ladder = FX(a.bb.max.X) + 2

    # Outer diameter — only for rotational (turned) parts, and from the
    # classified external OD cylinder, never a bore that happens to be the
    # largest diameter (#81)
    if a.is_rotational:
        od = a.od_diam
        dwg.add(
            Dimension(
                (FX(a.cx - od / 2), FZ(a.bb.max.Z) + 2, 0),
                (FX(a.cx + od / 2), FZ(a.bb.max.Z) + 2, 0),
                "above",
                8,
                draft,
                label=f"ø{_fmt(od)}",
            ),
            "dim_od",
        )
        # Centreline through the rotation axis — front and side views
        dwg.add(
            Centerline(
                (FX(a.cx), FZ(a.bb.min.Z) - 5, 0),
                (FX(a.cx), FZ(a.bb.max.Z) + 5, 0),
            ),
            "centerline_front",
        )
        dwg.add(
            Centerline(
                (SX(a.cy), SZ(a.bb.min.Z) - 5, 0),
                (SX(a.cy), SZ(a.bb.max.Z) + 5, 0),
            ),
            "centerline_side",
        )

    # Z-axis bore leaders to the left of the front view — these assume bores
    # concentric with the rotation axis, so rotational only (#81).  z_diams
    # carries *every* Z cylinder diameter including off-axis ones (e.g. a bolt
    # circle's holes), so the bore set is restricted to diameters that actually
    # belong to an internal cylinder on the rotation axis (#10): an off-axis
    # ø8 bolt hole must not surface as a phantom concentric bore leader.
    bores = _concentric_bore_diams(a) if a.is_rotational else []
    if a.is_rotational and bores:
        left_edge = FX(a.bb.min.X)
        left_space = left_edge - a.margin
        if left_space >= a.DIM_PAD:
            ldr_length = a.DIM_PAD * 0.6
            elbow_x = left_edge - ldr_length
            # Stack all distinct bores, centred on the axis (generalised beyond
            # the old hard cap of 3 — #10); any not annotated would surface via
            # the coverage lint, but all are placed here.
            n = len(bores)
            pitch = max(10.0, draft.font_size * 3.0)
            for i, d in enumerate(bores):
                tip_z = FZ(a.cz) + (i - (n - 1) / 2) * pitch
                dwg.add(
                    Leader(
                        tip=(FX(a.cx - d / 2), tip_z, 0),
                        elbow=(elbow_x, tip_z, 0),
                        label=f"ø{_fmt(d)}",
                        draft=draft,
                    ),
                    f"ldr_z{i}",
                )
        else:
            _log.info("Additional diameters %s not annotated (insufficient left margin)", bores)

    # Per-hole annotations from the feature records (#91, #92, #95): each
    # hole is annotated in the view its axis is normal to.
    view_of_axis = {
        "z": ("plan", lambda h: (PX(h.location[0]), PY(h.location[1]))),
        "y": ("front", lambda h: (FX(h.location[0]), FZ(h.location[2]))),
        "x": ("side", lambda h: (SX(h.location[1]), SZ(h.location[2]))),
    }

    def _axis_letter(h):
        return max(zip("xyz", h.axis, strict=True), key=lambda t: abs(t[1]))[0]

    # Centre marks for every hole (all part classes)
    for i, h in enumerate(a.holes):
        view, to_page = view_of_axis[_axis_letter(h)]
        size = max(2.5, h.diameter * a.SCALE + 2.0)
        dwg.add(CenterMark(to_page(h), size, draft), f"cm_{view}{i}")

    # Hole callouts, location dims, and the section view fire on *feature
    # presence*, independent of the turned/prismatic class (#10): the
    # classification only selects the base set (OD+centreline+ldr_z vs envelope
    # dims).  A turned flange (round OD + a bolt circle) must get BOTH.
    #
    # On a turned part the concentric, axis-aligned bores are already
    # dimensioned by the ldr_z leaders, so they are excluded here to avoid a
    # duplicate hole callout; only the off-axis features get callouts.  On a
    # prismatic part every hole flows through unchanged.
    feature_holes = a.holes
    feature_patterns = a.patterns
    if a.is_rotational:
        feature_holes = [h for h in a.holes if not _is_concentric_hole(h, a, _axis_letter)]
        present = set(map(id, feature_holes))
        feature_patterns = [p for p in a.patterns if all(id(h) in present for h in p.holes)]
    if feature_holes:
        _annotate_holes(
            dwg, a, view_of_axis, _axis_letter, feature_patterns, holes_in=feature_holes
        )
        _add_location_dims(dwg, a, _axis_letter, feature_patterns, holes_in=feature_holes)

    if a.cross_diams and a.is_rotational and not feature_holes:
        _log.info(
            "Cross-hole ø%s detected but not annotated (requires section view)",
            _fmt(a.cross_diams[0]),
        )

    # Step heights — only steps that are tall enough to carry a label AND far
    # enough apart on the page to tell from their neighbours (#41). Each step
    # witnesses from the previous dim's line (_right_ladder) so extension lines
    # are adjacent rather than coincident. Steps dropped for being too closely
    # spaced surface via lint (use a detail view, #42); the corridor is sized
    # for the kept count, so the strip is only the bound in degenerate cases.
    _step_zs, _n_too_close = _legible_steps(a.step_zs, a.bb.min.Z, a.SCALE)
    if _n_too_close:
        dwg._record_build_issue(
            "warning",
            "step_dim_dropped",
            f"{_n_too_close} step height(s) too closely spaced to dimension at this "
            "scale (use a detail view)",
        )
    for col, z in enumerate(_step_zs):
        _px = a.fv_zones.right.allocate(_SLOT_DIM_STEP)
        if _px is None:
            _log.warning("dim_step_%d skipped: fv_zones.right strip full", col)
            dwg._record_build_issue(
                "error",
                "placement_unsatisfiable",
                f"{len(_step_zs) - col} step-height dimension(s) dropped "
                "(front-view right strip full)",
            )
            break
        dwg.add(
            Dimension(
                (_right_ladder, FZ(a.bb.min.Z), 0),
                (_right_ladder, FZ(z), 0),
                "right",
                _px - _right_ladder,
                draft,
                label=_fmt(z - a.bb.min.Z),
            ),
            f"dim_step_{col}",
        )
        _right_ladder = _px

    # Overall height — placed last so it sits OUTERMOST, beyond the step dims.
    _px = a.fv_zones.right.allocate(_SLOT_DIM_HEIGHT)
    if _px is not None:
        dwg.add(
            Dimension(
                (_right_ladder, FZ(a.bb.min.Z), 0),
                (_right_ladder, FZ(a.bb.max.Z), 0),
                "right",
                _px - _right_ladder,
                draft,
                label=_fmt(a.z_size),
            ),
            "dim_height",
        )
        _right_ladder = _px
    else:
        _log.warning("dim_height skipped: fv_zones.right strip full")

    # Width (non-round / non-square parts only) — routed through pv_zones.below
    if abs(a.x_size - a.y_size) > max(a.x_size, a.y_size) * 0.05:
        _below_witness = PY(a.bb.min.Y) - 2
        _py = a.pv_zones.below.allocate(_SLOT_DIM_WIDTH)
        if _py is not None:
            dwg.add(
                Dimension(
                    (PX(a.bb.min.X), _below_witness, 0),
                    (PX(a.bb.max.X), _below_witness, 0),
                    "below",
                    _below_witness - _py,
                    draft,
                    label=_fmt(a.x_size),
                ),
                "dim_width",
            )
        else:
            _log.warning("dim_width skipped: pv_zones.below strip full")

    # Depth (Y envelope) — same guard as dim_width; routed through sv_zones.below
    if abs(a.x_size - a.y_size) > max(a.x_size, a.y_size) * 0.05:
        _below_witness_d = SZ(a.bb.min.Z) - 2
        _pd = a.sv_zones.below.allocate(_SLOT_DIM_DEPTH)
        if _pd is not None:
            dwg.add(
                Dimension(
                    (SX(a.bb.min.Y), _below_witness_d, 0),
                    (SX(a.bb.max.Y), _below_witness_d, 0),
                    "below",
                    _below_witness_d - _pd,
                    draft,
                    label=_fmt(a.y_size),
                ),
                "dim_depth",
            )
        else:
            _log.warning("dim_depth skipped: sv_zones.below strip full")

    # The section view goes last: its room check clears every annotation
    # already placed right of the side view (callout labels, height/step
    # dim ladders).  Fires on feature presence, not class (#10); concentric
    # bores on a turned part are excluded (the ldr_z leaders cover them).
    if feature_holes:
        _add_section_view(dwg, a, _axis_letter, holes=feature_holes)

    # Detail view: enlarge the stepped region when the legibility gate dropped
    # crowded shoulders (#42).  Returns early when nothing was dropped, so parts
    # without crowded steps are untouched.
    _add_detail_view(dwg, a)

    # Phase 7 — strip footprint debug logging + post-placement overflow check.
    # Overflow can only occur when outer_limit was tightened after allocations
    # were already committed (e.g. iso-x tightening or iso-y cap guard).
    _all_strips = [
        ("fv.right", a.fv_zones.right),
        ("fv.left", a.fv_zones.left),
        ("fv.above", a.fv_zones.above),
        ("fv.below", a.fv_zones.below),
        ("pv.right", a.pv_zones.right),
        ("pv.left", a.pv_zones.left),
        ("pv.above", a.pv_zones.above),
        ("pv.below", a.pv_zones.below),
        ("sv.right", a.sv_zones.right),
        ("sv.left", a.sv_zones.left),
        ("sv.above", a.sv_zones.above),
        ("sv.below", a.sv_zones.below),
    ]
    for _sn, _st in _all_strips:
        if _st is None:
            continue
        _log.debug(
            "strip %-10s  anchor=%.1f  limit=%.1f  used=%.1f/%.1f mm",
            _sn,
            _st.anchor,
            _st.outer_limit,
            _st.depth_used,
            _st.available,
        )
        # Overflow check: if at least one allocation was made, the end of the
        # last slot must not have exceeded outer_limit.
        _initial = _st.anchor + _st.direction * _st.gap
        if abs(_st._cursor - _initial) > 0.1:  # at least one allocation
            _last_end = _st._cursor - _st.direction * _st.spacing
            _over = _st.direction * (_last_end - _st.outer_limit)
            if _over > 0.5:
                _log.warning(
                    "strip %s overflowed outer_limit by %.1f mm "
                    "(limit=%.1f, last-slot-end=%.1f) — limit was likely "
                    "tightened after allocations were committed",
                    _sn,
                    _over,
                    _st.outer_limit,
                    _last_end,
                )

    if getattr(a, "pmi_mode", "off") == "annotate":
        _annotate_pmi(dwg, a, draft)

    _add_title_block(dwg, a)


def _annotate_pmi(dwg, a, draft) -> None:
    """Add PMI-derived dimension annotations to *dwg* using remaining strip space.

    Called from ``_auto_annotate`` after all automatic dimensions are placed so
    PMI dims consume the strips' leftover capacity.  Skips records whose page
    projection is degenerate (< 3 mm span) or whose extension lines would exceed
    twice the nominal value.

    View assignment:
    - dominant X → front view, fv_zones.above / fv_zones.below
    - dominant Z → front view, fv_zones.right / fv_zones.left
    - dominant Y → side view, sv_zones.above / sv_zones.below
                   (falls back to pv_zones.below for Y dims that are
                    too compressed in the side view)
    """
    pmi = getattr(a, "pmi", [])
    usable = [r for r in pmi if r.value > 0 and len(r.ref_pts) >= 2]
    n_gtol = sum(
        1
        for r in pmi
        if r.kind
        not in (
            "linear",
            "diameter",
            "radius",
            "angular",
            "curved_dist",
            "oriented",
            "curve_length",
            "thickness",
            "label",
            "presentation",
        )
        and r.value > 0
    )
    if n_gtol:
        _log.debug("PMI annotate: %d gtol/datum record(s) not yet annotatable (Phase 4)", n_gtol)
    if not usable:
        _log.info("PMI annotate: no usable records (value>0 with 2+ ref pts)")
        return

    def FX(x):
        return a.FV_X + (x - a.cx) * a.SCALE

    def FZ(z):
        return a.FV_Y + (z - a.cz) * a.SCALE

    def SX(y):
        return a.SV_X + (y - a.cy) * a.SCALE

    def SZ(z):
        return a.SV_Y + (z - a.cz) * a.SCALE

    def PX(x):
        return a.PV_X + (x - a.cx) * a.SCALE

    def PY(y):
        return a.PV_Y + (y - a.cy) * a.SCALE

    _SLOT = 10.0  # mm — slot size for PMI dim lines in the strip

    def _bore_info(rec):
        """For Size_Diameter / Size_Radius records, return (bore_axis, cx, cy, cz).

        bore_axis is the bbox's LONGEST extent (the bore's depth direction).
        Reuses rec.dominant_axis set by extract_pmi; falls back to re-sorting
        the bbox spans only when dominant_axis is '?' (degenerate bbox).
        The diameter/radius is then placed perpendicular to the bore axis in the
        view where the bore appears as a circle.  Returns None if ref_bbox absent.
        """
        bb = rec.ref_bbox
        if bb is None:
            return None
        bore_axis = rec.dominant_axis
        if bore_axis == "?":
            xmin, ymin, zmin, xmax, ymax, zmax = bb
            spans = sorted(
                [("X", abs(xmax - xmin)), ("Y", abs(ymax - ymin)), ("Z", abs(zmax - zmin))],
                key=lambda t: t[1],
                reverse=True,
            )
            bore_axis = spans[0][0]
        cx_f = sum(p[0] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
        cy_f = sum(p[1] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
        cz_f = sum(p[2] for p in rec.ref_pts) / len(rec.ref_pts) if rec.ref_pts else 0.0
        return bore_axis, cx_f, cy_f, cz_f

    def _witness_from_bbox(rec, view: str):
        """Witness points from the outer edges of the combined reference bbox.

        Gives the correct span for linear dims where both ref faces are flush
        (e.g. two parallel faces of a slot or step).  Not suitable for bore
        diameters — use _bore_info instead.
        """
        bb = rec.ref_bbox
        if bb is None:
            return None
        xmin, ymin, zmin, xmax, ymax, zmax = bb
        ax = rec.dominant_axis

        if view == "front" and ax == "X":
            p1 = (FX(xmin), FZ((zmin + zmax) / 2), 0)
            p2 = (FX(xmax), FZ((zmin + zmax) / 2), 0)
            avg_t = FZ((zmin + zmax) / 2)
        elif view == "front" and ax == "Z":
            p1 = (FX((xmin + xmax) / 2), FZ(zmin), 0)
            p2 = (FX((xmin + xmax) / 2), FZ(zmax), 0)
            avg_t = FX((xmin + xmax) / 2)
        elif view == "side" and ax == "Y":
            p1 = (SX(ymin), SZ((zmin + zmax) / 2), 0)
            p2 = (SX(ymax), SZ((zmin + zmax) / 2), 0)
            avg_t = SZ((zmin + zmax) / 2)
        elif view == "plan" and ax == "Y":
            avg_x = (xmin + xmax) / 2
            p1 = (PX(avg_x), PY(ymin), 0)
            p2 = (PX(avg_x), PY(ymax), 0)
            avg_t = PX(avg_x)
        else:
            return None

        span = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if span < 3:
            return None
        return p1, p2, avg_t

    def _try_above(p1, p2, strip, label, name):
        """Place a horizontal dimension line ABOVE the witness points."""
        if strip is None:
            return False
        witness_y = max(p1[1], p2[1]) + 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) <= witness_y:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            Dimension(
                (p1[0], witness_y, 0),
                (p2[0], witness_y, 0),
                "above",
                slot - witness_y,
                draft,
                label=label,
            ),
            name,
        )
        return True

    def _try_below(p1, p2, strip, label, name):
        """Place a horizontal dimension line BELOW the witness points."""
        if strip is None:
            return False
        witness_y = min(p1[1], p2[1]) - 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) >= witness_y:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            Dimension(
                (p1[0], witness_y, 0),
                (p2[0], witness_y, 0),
                "below",
                witness_y - slot,
                draft,
                label=label,
            ),
            name,
        )
        return True

    def _try_right(p1, p2, strip, label, name):
        """Place a vertical dimension line to the RIGHT of the witness points."""
        if strip is None:
            return False
        witness_x = max(p1[0], p2[0]) + 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) <= witness_x:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            Dimension(
                (witness_x, p1[1], 0),
                (witness_x, p2[1], 0),
                "right",
                slot - witness_x,
                draft,
                label=label,
            ),
            name,
        )
        return True

    def _try_left(p1, p2, strip, label, name):
        """Place a vertical dimension line to the LEFT of the witness points."""
        if strip is None:
            return False
        witness_x = min(p1[0], p2[0]) - 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) >= witness_x:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            Dimension(
                (witness_x, p1[1], 0),
                (witness_x, p2[1], 0),
                "left",
                witness_x - slot,
                draft,
                label=label,
            ),
            name,
        )
        return True
        return False

    emitted = 0
    for idx, rec in enumerate(usable):
        ax = rec.dominant_axis
        label = rec.label
        placed = False
        name_x = f"pmi_x_{idx}"
        name_z = f"pmi_z_{idx}"
        name_y = f"pmi_y_{idx}"
        name_d = f"pmi_d_{idx}"

        if rec.kind in ("diameter", "radius"):
            # --- Bore size: centroid ± value/2 perpendicular to bore axis ---
            info = _bore_info(rec)
            if info is None:
                _log.debug("PMI dim[%d] diam: no ref_bbox, skip", idx)
                continue
            bore_axis, cx_f, cy_f, cz_f = info
            half = rec.value / 2 if rec.kind == "diameter" else rec.value

            # Bore diameter page span = diameter × scale.  When the span is
            # narrower than ~8 mm the centred label text overflows the gap
            # and the extension lines punch through it.  Use a Leader
            # (arrowhead at bore edge, text on a horizontal shelf) for
            # narrow bores; bracket dims only when span fits the text.
            half_pg = half * a.SCALE  # bore radius on page (mm)

            if bore_axis == "Z":
                # Z-axis bore: circle visible in plan view.
                if half_pg >= 4.0:
                    p1 = (PX(cx_f - half), PY(cy_f), 0)
                    p2 = (PX(cx_f + half), PY(cy_f), 0)
                    placed = _try_above(p1, p2, a.pv_zones.above, label, name_d) or _try_below(
                        p1, p2, a.pv_zones.below, label, name_d
                    )
                else:
                    tip = (PX(cx_f), PY(cy_f) + half_pg, 0)
                    slot = a.pv_zones.above.allocate(_SLOT)
                    if slot is not None:
                        dwg.add(Leader(tip, (PX(cx_f), slot, 0), label, draft), name_d)
                        placed = True
                    else:
                        slot = a.pv_zones.below.allocate(_SLOT)
                        if slot is not None:
                            tip = (PX(cx_f), PY(cy_f) - half_pg, 0)
                            dwg.add(Leader(tip, (PX(cx_f), slot, 0), label, draft), name_d)
                            placed = True

            elif bore_axis == "X":
                # X-axis bore: circle visible in side view.
                if half_pg >= 4.0:
                    p1 = (SX(cy_f - half), SZ(cz_f), 0)
                    p2 = (SX(cy_f + half), SZ(cz_f), 0)
                    placed = _try_above(p1, p2, a.sv_zones.above, label, name_d) or _try_below(
                        p1, p2, a.sv_zones.below, label, name_d
                    )
                else:
                    tip = (SX(cy_f), SZ(cz_f) + half_pg, 0)
                    slot = a.sv_zones.above.allocate(_SLOT)
                    if slot is not None:
                        dwg.add(Leader(tip, (SX(cy_f), slot, 0), label, draft), name_d)
                        placed = True
                    else:
                        slot = a.sv_zones.below.allocate(_SLOT)
                        if slot is not None:
                            tip = (SX(cy_f), SZ(cz_f) - half_pg, 0)
                            dwg.add(Leader(tip, (SX(cy_f), slot, 0), label, draft), name_d)
                            placed = True

            elif bore_axis == "Y":
                # Y-axis bore: circle visible in front view as a circle.
                if half_pg >= 4.0:
                    p1 = (FX(cx_f - half), FZ(cz_f), 0)
                    p2 = (FX(cx_f + half), FZ(cz_f), 0)
                    placed = _try_above(p1, p2, a.fv_zones.above, label, name_d) or _try_below(
                        p1, p2, a.fv_zones.below, label, name_d
                    )
                else:
                    # Narrow bore: leader from bore bottom into the below strip.
                    tip = (FX(cx_f), FZ(cz_f) - half_pg, 0)
                    slot = a.fv_zones.below.allocate(_SLOT)
                    if slot is not None:
                        elbow = (FX(cx_f), slot, 0)
                        dwg.add(Leader(tip, elbow, label, draft), name_d)
                        placed = True
                    else:
                        # Fall back: leader upward into the above strip.
                        slot = a.fv_zones.above.allocate(_SLOT)
                        if slot is not None:
                            tip = (FX(cx_f), FZ(cz_f) + half_pg, 0)
                            elbow = (FX(cx_f), slot, 0)
                            dwg.add(Leader(tip, elbow, label, draft), name_d)
                            placed = True

        elif ax == "X":
            wp = _witness_from_bbox(rec, "front")
            if wp is None:
                _log.debug("PMI dim[%d] X: degenerate bbox", idx)
                continue
            p1, p2, avg_pz = wp
            if avg_pz >= a.FV_Y:
                placed = _try_above(p1, p2, a.fv_zones.above, label, name_x)
            if not placed:
                placed = _try_below(p1, p2, a.fv_zones.below, label, name_x)

        elif ax == "Z":
            wp = _witness_from_bbox(rec, "front")
            if wp is None:
                _log.debug("PMI dim[%d] Z: degenerate bbox", idx)
                continue
            p1, p2, avg_px = wp
            if avg_px >= a.FV_X:
                placed = _try_right(p1, p2, a.fv_zones.right, label, name_z)
            if not placed:
                placed = _try_left(p1, p2, a.fv_zones.left, label, name_z)

        elif ax == "Y":
            # Try side view (Y maps to SX horizontal).
            wp = _witness_from_bbox(rec, "side")
            if wp is not None:
                p1, p2, avg_sz = wp
                if avg_sz >= a.SV_Y:
                    placed = _try_above(p1, p2, a.sv_zones.above, label, name_y)
                if not placed:
                    placed = _try_below(p1, p2, a.sv_zones.below, label, name_y)
            # Fall back: plan view (Y maps to PY vertical).
            if not placed:
                wp = _witness_from_bbox(rec, "plan")
                if wp is not None:
                    p1, p2, _ = wp
                    placed = _try_below(p1, p2, a.pv_zones.below, label, name_y)

        if placed:
            emitted += 1
            _log.info("PMI dim[%d] %s %.3g → annotated (%s)", idx, ax, rec.value, label)
        else:
            _log.info("PMI dim[%d] %s %.3g → no strip space", idx, ax, rec.value)

    _log.info("PMI annotate: %d/%d dims placed", emitted, len(usable))


def _record_callout_drop(dwg, view, diam, reason):
    """Record a hole callout the layout could not place (#36).

    A warning (the drawing is incomplete, not invalid), whose diameter is
    excluded from ``feature_not_dimensioned`` like the old per-view cap drop —
    so a callout that genuinely doesn't fit is surfaced once, with a reason,
    and not double-reported.
    """
    dwg._dropped_callout_diams.append(diam)
    dwg._record_build_issue(
        "warning",
        "callout_dropped",
        f"hole callout ø{_fmt(diam)} dropped from the {view} view ({reason})",
    )


def _add_location_dims(dwg, a, axis_letter, patterns, holes_in=None):
    """Baseline X/Y location dimensions in the plan view (#93).

    The datum corner is a *default* — the part's minimum-X/minimum-Y corner
    (lower-left in the plan view), per inspection practice; a human/LLM pass
    can re-anchor it. One reference per pattern (bolt-circle centre, array
    first hole) plus each unpatterned hole. There is no fixed cap: dims are
    placed nearest-datum-first (baseline practice) until the above-view tier
    strips fill — a tier that would leave the page is skipped, never
    force-placed, and the unplaced ref surfaces as ``location_ref_dropped``
    (#36). X dims tier above the plan view (below sit dim_width and the front
    view), Y dims tier above the side view. Cross-axis holes are not located
    yet (logged).
    """
    draft = dwg.draft
    all_holes = a.holes if holes_in is None else holes_in
    z_holes = [h for h in all_holes if axis_letter(h) == "z"]
    if len(z_holes) < len(all_holes):
        _log.info("Cross-axis holes present; their locations are not auto-dimensioned")
    patterned = {h for p in patterns for h in p.holes}
    refs = []  # (world_x, world_y, sort_diameter)
    for p in patterns:
        if axis_letter(p.holes[0]) != "z":
            continue
        if isinstance(p, BoltCircle):
            refs.append((p.center[0], p.center[1], p.holes[0].diameter))
        else:
            # locate the array's member nearest the datum corner — the pitch
            # dim chains the rest outward (shortest baseline, per practice)
            near = min(
                p.holes,
                key=lambda h: (
                    (h.location[0] - a.bb.min.X) ** 2 + (h.location[1] - a.bb.min.Y) ** 2
                ),
            )
            refs.append((near.location[0], near.location[1], near.diameter))
    refs += [(h.location[0], h.location[1], h.diameter) for h in z_holes if h not in patterned]
    # dedupe coincident references (e.g. a hole at a bolt-circle's centre)
    unique: list = []
    for r in refs:
        if not any(abs(r[0] - u[0]) < 0.5 and abs(r[1] - u[1]) < 0.5 for u in unique):
            unique.append(r)
    refs = unique
    if not refs:
        return

    def PX(x):
        return a.PV_X + (x - a.cx) * a.SCALE

    def PY(y):
        return a.PV_Y + (y - a.cy) * a.SCALE

    plan_top = PY(a.bb.max.Y)
    datum_x, datum_y = a.bb.min.X, a.bb.min.Y
    # Vertical pitch between stacked location dims: the value label (one glyph
    # height) plus clearance above and below, so consecutive tiers pack as
    # tightly as they can without a label touching the next dim line. (Was a
    # looser font_size*3.)
    tier = draft.font_size + 2 * draft.pad_around_text

    # X locations: dims above the plan view, routed through pv_zones.above.
    # Pre-advance the strip past any pitch dims already placed above plan_top.
    x_refs: list = []
    for r in refs:
        if not any(abs(r[0] - u[0]) < 0.5 for u in x_refs):
            x_refs.append(r)
    # Legibility gate (#43): drop X refs whose baseline witness lines would be
    # page-coincident with a kept one — "fits" is not "legible" (cf. #41). Gate
    # only the refs that will actually be drawn: a hole on the datum edge is
    # skipped below, so it must not anchor a cluster and drop a real neighbour.
    _x_drawable = {r[0] for r in x_refs if abs(r[0] - datum_x) * a.SCALE >= 1.0}
    _kept_x, _n_x_close = _legible_locations(_x_drawable, a.SCALE)
    if _n_x_close:
        dwg._record_build_issue(
            "warning",
            "location_ref_dropped",
            f"{_n_x_close} X location dim(s) too closely spaced to dimension legibly "
            "(use a detail view)",
        )
    _kept_x_set = set(_kept_x)
    x_refs = [r for r in x_refs if r[0] not in _x_drawable or r[0] in _kept_x_set]
    for n, ann in dwg._named.items():
        if n.startswith("dim_pitch_plan") and getattr(ann, "dim_level_y", 0) > plan_top:
            a.pv_zones.above.allocate(10.0)  # consume space used by pitch dim
    for i, (rx, ry, _) in enumerate(sorted(x_refs, key=lambda r: abs(r[0] - datum_x))):
        if abs(rx - datum_x) * a.SCALE < 1.0:
            continue  # on the datum edge — nothing to dimension
        _py = a.pv_zones.above.allocate(tier)
        if _py is None:
            _log.info("X location dim for x=%s skipped (no room above plan view)", _fmt(rx))
            dwg._record_build_issue(
                "warning",
                "location_ref_dropped",
                f"X location dim for x={_fmt(rx)} not placed (no room above the plan view)",
            )
            continue
        dwg.add(
            Dimension(
                (PX(datum_x), PY(ry), 0),
                (PX(rx), PY(ry), 0),
                "above",
                _py - PY(ry),
                draft,
                label=_fmt(rx - datum_x),
            ),
            f"dim_locx{i}",
        )

    # Y locations: the side view maps world Y horizontally, and the strip
    # above it is open (the plan view's left margin fits barely one tier) —
    # dims go above the side view, witness lines rising from its top edge at
    # each hole's axis position
    def SX(y):
        return a.SV_X + (y - a.cy) * a.SCALE

    def SZ(z):
        return a.SV_Y + (z - a.cz) * a.SCALE

    side_top = SZ(a.bb.max.Z)
    iso_x0, iso_y0, _, _ = _iso_bbox(dwg)
    y_refs: list = []
    for rx, ry, dia in refs:
        if not any(abs(ry - u[1]) < 0.5 for u in y_refs):
            y_refs.append((rx, ry, dia))
    # Legibility gate (#43): drop Y refs page-coincident with a kept one. Gate
    # only drawable refs (the placement loop skips datum-edge ones), so the gate
    # never anchors a cluster on a hole that isn't dimensioned.
    _y_drawable = {r[1] for r in y_refs if abs(r[1] - datum_y) * a.SCALE >= 1.0}
    _kept_y, _n_y_close = _legible_locations(_y_drawable, a.SCALE)
    if _n_y_close:
        dwg._record_build_issue(
            "warning",
            "location_ref_dropped",
            f"{_n_y_close} Y location dim(s) too closely spaced to dimension legibly "
            "(use a detail view)",
        )
    _kept_y_set = set(_kept_y)
    y_refs = [r for r in y_refs if r[1] not in _y_drawable or r[1] in _kept_y_set]
    # Y locations: dims above the side view, routed through sv_zones.above.
    # Pre-advance past any pitch dims already placed above side_top.
    for n, ann in dwg._named.items():
        if n.startswith("dim_pitch_side") and getattr(ann, "dim_level_y", 0) > side_top:
            a.sv_zones.above.allocate(10.0)  # consume space used by pitch dim
    # Tighten outer_limit if any witness line approaches the iso view boundary.
    # Guard: only cap if iso_y0-4 is above the strip's current cursor — an iso
    # view that overflows left (too large to fit) can have iso_y0 below
    # sv_top_edge, which would make all allocations return None if applied.
    if y_refs and any(SX(ry) + 10 > iso_x0 - 4 for _, ry, _ in y_refs):
        cap = iso_y0 - 4
        above = a.sv_zones.above
        if cap > above._cursor:
            above.outer_limit = min(above.outer_limit, cap)
        else:
            _log.warning(
                "sv_zones.above cursor %.1f >= iso_y0 cap %.1f: Y-location dims may overlap iso view",
                above._cursor,
                cap,
            )
    for i, (_rx, ry, _) in enumerate(sorted(y_refs, key=lambda r: abs(r[1] - datum_y))):
        if abs(ry - datum_y) * a.SCALE < 1.0:
            continue
        _py = a.sv_zones.above.allocate(tier)
        if _py is None:
            _log.info("Y location dim for y=%s skipped (no room above the side view)", _fmt(ry))
            dwg._record_build_issue(
                "warning",
                "location_ref_dropped",
                f"Y location dim for y={_fmt(ry)} not placed (no room above the side view)",
            )
            continue
        dwg.add(
            Dimension(
                (SX(datum_y), SZ(a.bb.max.Z), 0),
                (SX(ry), SZ(a.bb.max.Z), 0),
                "above",
                _py - side_top,
                draft,
                label=_fmt(ry - datum_y),
            ),
            f"dim_locy{i}",
        )


def _section_hatch_edges(face, SX, SZ, spacing):
    """Return 45° ISO 128-50 hatch Edge objects for one cut face in page coords.

    Uses the even-odd rule: all boundary wires (outer + inner) are traversed;
    intersections of each hatch line with the boundary are sorted and filled in
    alternating spans.  Curved edges are tessellated to straight segments so
    circular hole outlines clip correctly.
    """
    segs = []
    for wire in [face.outer_wire()] + list(face.inner_wires()):
        for edge in wire.edges():
            if edge.geom_type == GeomType.LINE:
                pts = [edge.position_at(0), edge.position_at(1)]
            else:
                n = max(8, int(edge.length / spacing) + 1)
                pts = [edge.position_at(i / n) for i in range(n + 1)]
            ppts = [(SX(v.X), SZ(v.Z)) for v in pts]
            for j in range(len(ppts) - 1):
                segs.append((ppts[j], ppts[j + 1]))

    if not segs:
        return []

    all_xs = [p[0] for s in segs for p in s]
    all_ys = [p[1] for s in segs for p in s]
    # 45° lines satisfy y − x = c; step by spacing (perpendicular to lines)
    step = spacing
    c_min = min(all_ys) - max(all_xs) - step
    c_max = max(all_ys) - min(all_xs) + step

    result = []
    c = c_min + step
    while c < c_max:
        hits = []
        for (x1, y1), (x2, y2) in segs:
            denom = (y2 - y1) - (x2 - x1)
            if abs(denom) < 1e-9:
                continue
            t = (c - (y1 - x1)) / denom
            if -1e-6 <= t < 1 - 1e-6:  # half-open: each shared vertex counted once
                hits.append(x1 + t * (x2 - x1))
        hits.sort()
        for i in range(0, len(hits) - 1, 2):
            xa, xb = hits[i], hits[i + 1]
            if xb - xa > 0.2:
                result.append(Edge.make_line(Vector(xa, xa + c, 0), Vector(xb, xb + c, 0)))
        c += step
    return result


def _fuzzy_cut(body, cutter, fuzzy: float = 1e-3):
    """Boolean-subtract *cutter* from *body* with a fuzzy tolerance.

    Returns a build123d ``Solid`` (or ``Compound`` of solids), or ``None`` if the
    boolean fails or yields no solid.

    build123d's plain ``body - cutter`` runs an exact (zero-fuzzy) boolean which
    raises an *uncatchable* ``Standard_DomainError`` on some cast geometry — the
    C++ exception escapes to ``libc++abi: terminating`` (SIGABRT) and a
    surrounding ``try/except`` never sees it, killing the whole drawing (NIST
    CTC-04 section cut, #20). A small fuzzy value makes ``BRepAlgoAPI_Cut`` robust
    on the same input. We drive the OCCT op directly (build123d's
    ``Shape._bool_op`` result-processing aborts on the resulting compound) and
    keep only the solids, so non-solid boolean artefacts can't crash the
    downstream hidden-line projection.
    """
    args = TopTools_ListOfShape()
    args.Append(body.wrapped)
    tools = TopTools_ListOfShape()
    tools.Append(cutter.wrapped)
    op = BRepAlgoAPI_Cut()
    op.SetArguments(args)
    op.SetTools(tools)
    op.SetFuzzyValue(fuzzy)
    op.Build()
    if not op.IsDone():
        return None
    solids = Compound(op.Shape()).solids()
    if not solids:
        return None
    return solids[0] if len(solids) == 1 else Compound(children=list(solids))


def _add_section_view(dwg, a, axis_letter, holes=None):
    """Full section A–A when blind or stepped holes hide their structure (#94).

    Trigger: any Z-axis hole with a counterbore/spotface or a non-through
    bottom — its internal profile is hidden-line-only in every standard
    view. The cut plane passes through the densest row of qualifying hole
    axes, parallel to the front view; material on the viewer's side is
    removed so the cut face shows the hole profiles as visible line-work.
    The section is placed right of the side view when there is room
    (skipped with a log otherwise), captioned, marked with ISO 128-44
    cutting-plane arrows and 'A' letters on the plan view, and filled with
    ISO 128-50 45° hatching on the cut face.
    """
    cands = [
        h
        for h in (a.holes if holes is None else holes)
        if axis_letter(h) == "z" and (h.cbore or h.spotface or h.bottom != "through")
    ]
    if not cands:
        return
    ys = [h.location[1] for h in cands]
    y_star = max(
        {round(y, 1) for y in ys},
        key=lambda v: (sum(1 for y in ys if abs(y - v) <= 0.5), -abs(v - a.cy)),
    )

    # room check: same row as the front/side views, to the right — past any
    # side-view callout labels already placed there.
    # 12.0 mm floor: conservative minimum half-width so very narrow sections
    # have enough room for the "SECTION A–A" caption and arrows.
    half_w = max(a.x_size * a.SCALE / 2, 12.0)
    half_h = a.z_size * a.SCALE / 2
    side_vis, side_hid = dwg.views["side"]
    side_right = side_vis.bounding_box().max.X
    if side_hid:
        side_right = max(side_right, side_hid.bounding_box().max.X)
    left_edge = side_right + 10
    for name, ann in dwg._named.items():
        # past side-view callout labels and the height/step dim ladder
        if name.startswith(("hc_side", "dim_height", "dim_step")) and getattr(
            ann, "label_bbox", None
        ):
            left_edge = max(left_edge, ann.label_bbox[2] + 6)
    pos_x = left_edge + half_w
    iso_x0, iso_y0, _, _ = _iso_bbox(dwg)
    right_limit = a.PAGE_W - a.margin
    if a.FV_Y + half_h + 6 > iso_y0 - 2:
        right_limit = min(right_limit, iso_x0 - 4)
    tb_left = a.PAGE_W - a.TB_W - _TB_CLEAR
    if a.FV_Y - half_h - 10 < _TB_CLEAR + _TB_H and pos_x + half_w > tb_left - 4:
        _log.info("Section A–A skipped (would collide with the title block)")
        return
    if pos_x + half_w > right_limit:
        _log.warning(
            "Section A–A skipped (no room right of the side view; "
            "a wider step-dimension corridor may have reduced the available space)"
        )
        return

    big = 4 * a.bbox_max
    # STEP imports with PMI carry annotation curves beside the solid, and a
    # mixed-dimension compound cannot be cut — section the solids only, and
    # never let a failed boolean abort the whole drawing
    solids = a.part.solids()
    if not solids:
        _log.info("Section A–A skipped (no solid bodies to cut)")
        return
    body = solids[0] if len(solids) == 1 else Compound(children=list(solids))
    try:
        # Fuzzy boolean: the exact `body - Box(...)` aborts uncatchably
        # (Standard_DomainError) on some cast geometry — see _fuzzy_cut / #20.
        keep_behind = _fuzzy_cut(body, Pos(a.cx, y_star - big / 2, a.cz) * Box(big, big, big))
    except Exception as exc:  # noqa: BLE001 — OCC booleans raise broadly
        _log.warning("Section A–A skipped (cut failed: %s)", exc)
        return
    if keep_behind is None:
        _log.warning("Section A–A skipped (boolean cut produced no solid)")
        return
    camera = (dwg.look_at[0], dwg.look_at[1] - dwg.dist, dwg.look_at[2])
    dwg.add_view("section_aa", keep_behind, camera, (0, 0, 1), (pos_x, a.FV_Y))
    dwg.add(
        Note("SECTION A–A", (pos_x, a.FV_Y - half_h - 7), dwg.draft),
        "section_caption",
    )

    # cutting-plane line + identification letters on the plan view
    def PX(x):
        return a.PV_X + (x - a.cx) * a.SCALE

    def PY(y):
        return a.PV_Y + (y - a.cy) * a.SCALE

    y_page = PY(y_star)
    # the line and its letters must clear pattern centrelines that sweep
    # past the part outline (a corner-hole bolt circle is always wider)
    ext_x0, ext_x1 = PX(a.bb.min.X), PX(a.bb.max.X)
    for name, ann in dwg._named.items():
        if name.startswith("bc_plan"):
            cb = ann.bounding_box()
            if cb.min.Y - 3 < y_page < cb.max.Y + 3:
                ext_x0 = min(ext_x0, cb.min.X)
                ext_x1 = max(ext_x1, cb.max.X)
    x0, x1 = ext_x0 - 4, ext_x1 + 4
    dwg.add(Centerline((x0, y_page, 0), (x1, y_page, 0)), "section_line")

    # ISO 128-44: cutting-plane end indicators — thick wing stubs with solid
    # filled arrowheads at the tips pointing in the viewing direction (−Y).
    arrow_sz = dwg.draft.arrow_length
    wing_h = 2.5 * arrow_sz  # perpendicular stub length
    for x_end, side in ((x0, "left"), (x1, "right")):
        tip_y = y_page - wing_h
        shaft = Edge.make_line(Vector(x_end, y_page, 0), Vector(x_end, tip_y, 0))
        filled = Arrow(
            arrow_size=arrow_sz,
            shaft_path=shaft,
            shaft_width=dwg.draft.line_width,
            head_at_start=False,
            head_type=HeadType.STRAIGHT,
            mode=Mode.PRIVATE,
        )
        dwg.add(Compound(children=list(filled.faces())), f"section_arrow_{side}")
        dwg.add(
            Compound(children=[Edge.make_line(Vector(x_end, y_page, 0), Vector(x_end, tip_y, 0))]),
            f"section_wing_{side}",
        )

    # 'A' letters sit above the line ends, clear of any callout leaders
    lift = dwg.draft.font_size * 1.4
    dwg.add(Note("A", (x0 - 3, y_page + lift), dwg.draft), "section_a_left")
    dwg.add(Note("A", (x1 + 3, y_page + lift), dwg.draft), "section_a_right")

    # ISO 128-50: 45° hatching on the cut face, in page coordinates
    def SX(wx):
        return pos_x + (wx - a.cx) * a.SCALE

    def SZ(wz):
        return a.FV_Y + (wz - a.cz) * a.SCALE

    hatch_spacing = dwg.draft.font_size * 1.5
    cut_faces = [f for f in keep_behind.faces() if f.normal_at().Y < -0.9]
    hatch_edges = []
    for cf in cut_faces:
        hatch_edges.extend(_section_hatch_edges(cf, SX, SZ, hatch_spacing))
    if hatch_edges:
        hatch = Compound(children=hatch_edges)
        hatch.is_section_hatch = True  # exempt from view_annotation_overlap lint
        dwg.add(hatch, "section_hatch")


def _add_detail_view(dwg, a):
    """Enlarged detail of a stepped region whose shoulders the legibility gate
    dropped (#42).

    Trigger: the step-height legibility gate (#41) drops one or more shoulders
    because they are page-coincident at sheet scale. We crop the part to the
    full step Z-band, project it at a larger standard scale into the largest
    free rectangle on the sheet, re-draw the dropped step dimensions there at
    a scale where they separate, mark the region on the front view, and caption
    it "DETAIL A". Mirrors :func:`_add_section_view`: every risky boolean /
    projection is wrapped and skips-with-log rather than aborting the drawing,
    and the function returns early (drawing unchanged) when there is nothing to
    detail.
    """
    if len(a.step_zs) < 2:
        return
    kept, _ = _legible_steps(a.step_zs, a.bb.min.Z, a.SCALE)
    crowded = [z for z in a.step_zs if z not in set(kept)]
    if len(crowded) < 1:
        return

    # Region: the full step Z-band, padded and clamped to the part bbox.
    z0, z1 = min(a.step_zs), max(a.step_zs)
    pad = 0.08 * (z1 - z0) + 1.0
    band_lo = max(a.bb.min.Z, z0 - pad)
    band_hi = min(a.bb.max.Z, z1 + pad)

    # Detail scale: the smallest standard multiple in [2, 5, 10] of sheet scale
    # that separates the closest shoulder pair (≥ _MIN_STEP_SEP_MM). Always at
    # least 2× so the detail is a genuine enlargement.
    s_zs = sorted(a.step_zs)
    gaps = [b - aa for aa, b in zip(s_zs, s_zs[1:])]
    min_gap = min(gaps)
    need = _MIN_STEP_SEP_MM / min_gap if min_gap > 0 else float("inf")
    detail_scale = a.SCALE * 2
    for factor in (2, 5, 10):
        detail_scale = a.SCALE * factor
        if detail_scale >= need:
            break

    # Crop to the Z-band with two fuzzy cuts (remove z<band_lo and z>band_hi).
    # Solids only — a mixed-dimension compound (PMI curves) cannot be cut.
    solids = a.part.solids()
    if not solids:
        _log.info("Detail view skipped (no solid bodies to crop)")
        return
    body = solids[0] if len(solids) == 1 else Compound(children=list(solids))
    big = 4 * a.bbox_max
    try:
        cropped = _fuzzy_cut(body, Pos(a.cx, a.cy, band_lo - big / 2) * Box(big, big, big))
        if cropped is not None:
            cropped = _fuzzy_cut(cropped, Pos(a.cx, a.cy, band_hi + big / 2) * Box(big, big, big))
    except Exception as exc:  # noqa: BLE001 — OCC booleans raise broadly
        _log.warning("Detail view skipped (crop failed: %s)", exc)
        return
    if cropped is None:
        _log.warning("Detail view skipped (boolean crop produced no solid)")
        return

    # Placement: largest empty rectangle avoiding every placed view + title block.
    drawable = (a.margin, a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin)
    obstacles = []
    for vis, hid in dwg.views.values():
        for shp in (vis, hid):
            if shp is None:
                continue
            vb = shp.bounding_box()
            obstacles.append((vb.min.X, vb.min.Y, vb.max.X, vb.max.Y))
    obstacles.append(
        (a.PAGE_W - a.TB_W - _TB_CLEAR, a.margin, a.PAGE_W - _TB_CLEAR, _TB_CLEAR + _TB_H)
    )
    rx0, ry0, rx1, ry1 = _largest_empty_rect(drawable, obstacles)
    rect_w, rect_h = rx1 - rx0, ry1 - ry0

    # Detail footprint at the chosen scale, including the step-dim ladder on the
    # right (one rung per kept step + the overall band height) and breathing
    # room for the caption below.  Shrink the scale to fit if necessary.
    step_pad = _MIN_STEP_SEP_MM
    n_rungs = len(_legible_steps(a.step_zs, a.bb.min.Z, detail_scale)[0]) + 1
    ladder_w = n_rungs * step_pad + 6
    while detail_scale > a.SCALE * 1.2:
        detail_w = a.x_size * detail_scale + ladder_w
        detail_h = (band_hi - band_lo) * detail_scale + a.DIM_PAD
        if detail_w <= rect_w and detail_h <= rect_h:
            break
        detail_scale -= a.SCALE
    n_rungs = len(_legible_steps(a.step_zs, a.bb.min.Z, detail_scale)[0]) + 1
    ladder_w = n_rungs * step_pad + 6
    detail_w = a.x_size * detail_scale + ladder_w
    detail_h = (band_hi - band_lo) * detail_scale + a.DIM_PAD
    if detail_scale <= a.SCALE * 1.2 or detail_w > rect_w or detail_h > rect_h:
        _log.info("Detail view skipped (no room)")
        return

    # Centre the view+ladder footprint in the rect; the view itself sits left of
    # centre so its right-hand ladder stays inside the chosen rectangle.
    DX = (rx0 + rx1) / 2 - ladder_w / 2
    DY = (ry0 + ry1) / 2

    # Project the cropped band front-on (look from −Y, up +Z), mirroring the
    # front view but at detail_scale around the band's own centroid (#42, like
    # _project_iso). Then rebuild ViewCoordinates so dwg.at("detail_a", ...)
    # maps world→page at the detail scale.
    cb = cropped.bounding_box()
    dcx = (cb.min.X + cb.max.X) / 2
    dcy = (cb.min.Y + cb.max.Y) / 2
    dcz = (cb.min.Z + cb.max.Z) / 2
    la = (dcx * detail_scale, dcy * detail_scale, dcz * detail_scale)
    dist_d = a.bbox_max * detail_scale + 100
    camera = (la[0], la[1] - dist_d, la[2])
    try:
        band_s = cropped.scale(detail_scale)
        dwg.add_view("detail_a", band_s, camera, (0, 0, 1), (DX, DY), look_at=la, scaled=True)
    except Exception as exc:  # noqa: BLE001 — projection raises broadly on cast geometry
        _log.warning("Detail view skipped (projection failed: %s)", exc)
        return
    dwg._coords["detail_a"] = ViewCoordinates(
        view_axes(camera, (0, 0, 1), la), DX, DY, dcx, dcy, dcz, detail_scale
    )

    # Caption below the detail.
    detail_bottom = DY - detail_h / 2
    dwg.add(
        Note(
            f"DETAIL A — SCALE {format_drawing_scale(detail_scale)}",
            (DX, detail_bottom - 7),
            dwg.draft,
        ),
        "detail_caption",
    )

    # Marker on the front view: a rectangle around the Z-band, with an 'A' label.
    def FX(x):
        return a.FV_X + (x - a.cx) * a.SCALE

    def FZ(z):
        return a.FV_Y + (z - a.cz) * a.SCALE

    mx0, mx1 = FX(a.bb.min.X), FX(a.bb.max.X)
    my0, my1 = FZ(band_lo), FZ(band_hi)
    marker = Compound(
        children=[
            Edge.make_line(Vector(mx0, my0, 0), Vector(mx1, my0, 0)),
            Edge.make_line(Vector(mx1, my0, 0), Vector(mx1, my1, 0)),
            Edge.make_line(Vector(mx1, my1, 0), Vector(mx0, my1, 0)),
            Edge.make_line(Vector(mx0, my1, 0), Vector(mx0, my0, 0)),
        ]
    )
    marker.is_centerline = True  # furniture, not a dimension — exempt from overlap lint
    dwg.add(marker, "detail_marker")
    dwg.add(Note("A", (mx1 + 3, my1 + 2), dwg.draft), "detail_marker_label")

    # Detail dimensions: step heights now legible at detail_scale, plus the
    # overall band height. Baseline-ladder to the right of the detail, mirroring
    # the main-view step dims.
    det_kept, _ = _legible_steps(a.step_zs, a.bb.min.Z, detail_scale)
    base_x = dwg.at("detail_a", a.bb.max.X, dcy, a.bb.min.Z)[0] + 2
    ladder = base_x
    for i, z in enumerate(det_kept):
        try:
            p_lo = dwg.at("detail_a", a.bb.max.X, dcy, a.bb.min.Z)
            p_hi = dwg.at("detail_a", a.bb.max.X, dcy, z)
            _dim = Dimension(
                (ladder, p_lo[1], 0),
                (ladder, p_hi[1], 0),
                "right",
                step_pad,
                dwg.draft,
                label=_fmt(z - a.bb.min.Z),
            )
            # The detail view is drawn at detail_scale, not sheet scale; tag the
            # dim so lint() checks label-vs-measured against the right scale (#42).
            _dim._dw_scale = detail_scale
            dwg.add(_dim, f"dim_detail_step_{i}")
            ladder += step_pad
        except Exception as exc:  # noqa: BLE001 — placement may fail on degenerate geometry
            _log.info("dim_detail_step_%d skipped (%s)", i, exc)

    # Overall band height — outermost.
    try:
        p_lo = dwg.at("detail_a", a.bb.max.X, dcy, a.bb.min.Z)
        p_hi = dwg.at("detail_a", a.bb.max.X, dcy, a.bb.max.Z)
        _dim = Dimension(
            (ladder, p_lo[1], 0),
            (ladder, p_hi[1], 0),
            "right",
            step_pad,
            dwg.draft,
            label=_fmt(a.z_size),
        )
        _dim._dw_scale = detail_scale  # detail view scale, for label-vs-measured lint (#42)
        dwg.add(_dim, "dim_detail_height")
    except Exception as exc:  # noqa: BLE001 — placement may fail on degenerate geometry
        _log.info("dim_detail_height skipped (%s)", exc)


def _add_furniture(dwg, a, view, j, pattern, to_page):
    """Pattern sheet furniture, added once its callout is placed (#92)."""
    if isinstance(pattern, BoltCircle):
        cx = sum(to_page(h)[0] for h in pattern.holes) / len(pattern.holes)
        cy = sum(to_page(h)[1] for h in pattern.holes) / len(pattern.holes)
        dwg.add(CenterlineCircle((cx, cy), pattern.diameter * a.SCALE), f"bc_{view}{j}")
    elif isinstance(pattern, LinearArray):
        _add_pitch_dim(dwg, a, view, j, pattern, to_page)


def _add_pitch_dim(dwg, a, view, j, pattern, to_page):
    """Pitch dimension for a linear hole array: first→last hole centres,
    labelled ``(n-1)× pitch``, placed just outside the view on the side of
    the row's outward perpendicular (#92)."""
    p1 = to_page(pattern.holes[0])
    p2 = to_page(pattern.holes[-1])
    ux, uy = p2[0] - p1[0], p2[1] - p1[1]
    norm = math.hypot(ux, uy)
    if norm < 1e-9:
        return
    ux, uy = ux / norm, uy / norm
    mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    # view extents in page coordinates, to push the dim line outside
    if view == "plan":
        corners = [
            (a.PV_X + (x - a.cx) * a.SCALE, a.PV_Y + (y - a.cy) * a.SCALE)
            for x in (a.bb.min.X, a.bb.max.X)
            for y in (a.bb.min.Y, a.bb.max.Y)
        ]
    elif view == "front":
        corners = [
            (a.FV_X + (x - a.cx) * a.SCALE, a.FV_Y + (z - a.cz) * a.SCALE)
            for x in (a.bb.min.X, a.bb.max.X)
            for z in (a.bb.min.Z, a.bb.max.Z)
        ]
    else:
        corners = [
            (a.SV_X + (y - a.cy) * a.SCALE, a.SV_Y + (z - a.cz) * a.SCALE)
            for y in (a.bb.min.Y, a.bb.max.Y)
            for z in (a.bb.min.Z, a.bb.max.Z)
        ]
    # Pick the perpendicular side from the page layout, not raw distance:
    # below the plan view sit dim_width and the front view, above the front
    # view sits the plan — so plan dims go up, front dims go down, and
    # vertical rows go left (callouts own the right strip). The side view
    # alone uses the shorter reach. A row far from its chosen side simply
    # gets long extension lines — standard practice when the near side is
    # occupied.
    reach_pos = max((c[0] - mid[0]) * -uy + (c[1] - mid[1]) * ux for c in corners)
    reach_neg = max((c[0] - mid[0]) * uy + (c[1] - mid[1]) * -ux for c in corners)
    cands = (((-uy, ux, 0), reach_pos), ((uy, -ux, 0), reach_neg))
    if view == "side":
        side, reach = min(cands, key=lambda c: c[1])
    else:
        pref = (-0.3, 1.0) if view == "plan" else (-0.3, -1.0)
        side, reach = max(cands, key=lambda c: c[0][0] * pref[0] + c[0][1] * pref[1])
    # stack further pitch dims in this view on outer tiers
    prior = sum(1 for name in dwg._named if name.startswith(f"dim_pitch_{view}"))
    offset = reach + 8 + 10 * prior
    # never force-place: skip (and log) when the dim line would leave the page
    ox = mid[0] + side[0] * (offset + 6)
    oy = mid[1] + side[1] * (offset + 6)
    if not (a.margin <= ox <= a.PAGE_W - a.margin and a.margin <= oy <= a.PAGE_H - a.margin):
        _log.info(
            "Pitch dimension for the %s× %s array skipped (no room)",
            len(pattern.holes),
            _fmt(pattern.pitch),
        )
        return
    n = len(pattern.holes)
    dwg.add(
        Dimension(
            (p1[0], p1[1], 0),
            (p2[0], p2[1], 0),
            side,
            offset,
            dwg.draft,
            label=f"{n - 1}× {_fmt(pattern.pitch)}",
        ),
        f"dim_pitch_{view}{j}",
    )


def _greedy_strip_ys(natural_ys, min_gap, y_min, y_max, *, prefix=False):
    """Greedy Y-placement: push each value down until the gap clears.

    With *prefix=False* (default): returns None if any item overflows y_max.
    With *prefix=True*: stops at the first overflow and returns the placed prefix.
    """
    result = []
    prev = y_min - min_gap
    for ny in natural_ys:
        y = max(prev + min_gap, ny)
        if y > y_max:
            if prefix:
                break
            return None
        result.append(y)
        prev = y
    return result


def _solve_strip_ys(natural_ys, min_gap, y_min, y_max):
    """Cassowary Y-placement for bore-callout leaders sharing one strip.

    Returns solved Y positions (same length as *natural_ys*), or ``None`` when
    the callouts don't fit within [y_min, y_max].  Falls back to the greedy
    cursor when kiwisolver is unavailable.

    *natural_ys* must be sorted ascending; each solved value is bounded to
    [y_min, y_max] and adjacent values are at least *min_gap* apart.
    """
    if not natural_ys:
        return []
    n = len(natural_ys)
    if (n - 1) * min_gap > y_max - y_min:
        return None  # provably infeasible

    try:
        import kiwisolver as ki
    except ImportError:
        return _greedy_strip_ys(natural_ys, min_gap, y_min, y_max)

    solver = ki.Solver()
    ys = [ki.Variable(f"y{i}") for i in range(n)]
    try:
        for v in ys:
            solver.addConstraint((v >= y_min) | "required")
            solver.addConstraint((v <= y_max) | "required")
        for i in range(n - 1):
            solver.addConstraint((ys[i + 1] - ys[i] >= min_gap) | "required")
        for v, ny in zip(ys, natural_ys, strict=True):
            solver.addConstraint((v == ny) | "strong")
        solver.updateVariables()
        return [v.value() for v in ys]
    except ki.UnsatisfiableConstraint:
        return None


def _annotate_holes(dwg, a, view_of_axis, axis_letter, found_patterns, holes_in=None):
    """Leader-attached HoleCallouts, one per distinct hole spec per view (#91).

    Identical holes share one callout with an ``n×`` count prefix (#92's
    grouping half) — through holes group on diameter and steps regardless of
    wall thickness. The leader tip lands on the hole's circumference, on the
    group's hole nearest the callout.

    Placement: plan- and side-view callouts go to the right of their view
    (the strip before the iso view / page margin; plan falls back to its
    left, the side view has no usable left strip), front-view callouts go
    below the front view, deconflicted so no leader shaft crosses an earlier
    callout's text. Each callout is width-checked; anything that fits
    nowhere is logged and skipped — never force-placed — and then surfaces
    through the coverage lint as ``feature_not_dimensioned``.
    """
    draft = dwg.draft
    gap = draft.pad_around_text
    # Minimum vertical separation between stacked bore-callout labels: one label
    # height (font_size) plus pad_around_text clearance above and below, so
    # adjacent labels never touch.  Derived from text metrics rather than a bare
    # font-size ratio (#31).
    min_gap = draft.font_size + 2 * gap
    # Group on the same machining-spec key pattern detection uses (snapped
    # axis vector included): blind holes drilled from opposite faces are
    # different operations and get separate callouts, and a spec group's
    # hole set therefore lines up exactly with find_hole_patterns' groups.
    groups: dict = {}
    for h in a.holes if holes_in is None else holes_in:
        groups.setdefault(_spec_key(h), []).append(h)

    by_view: dict = {}
    for holes in groups.values():
        by_view.setdefault(view_of_axis[axis_letter(holes[0])][0], []).append(holes)

    _, iso_y0, _, _ = _iso_bbox(dwg)
    plan_right = a.PV_X + (a.bb.max.X - a.cx) * a.SCALE
    plan_left = a.PV_X + (a.bb.min.X - a.cx) * a.SCALE
    side_right = a.SV_X + (a.bb.max.Y - a.cy) * a.SCALE
    front_bottom = a.FV_Y + (a.bb.min.Z - a.cz) * a.SCALE
    tb_left = a.PAGE_W - a.TB_W - _TB_CLEAR
    tb_top = _TB_CLEAR + _TB_H

    # A section line will be placed when the part has z-axis holes with
    # counterbores, spotfaces, or blind bottoms (_add_section_view trigger).
    # When present, its extension lines overhang the plan view boundary by
    # ~arrow_length, so plan-view elbow must sit that far outside to clear them.
    # Room-check failures may still skip the section, but the offset is harmless.
    will_have_section_line = any(
        axis_letter(h) == "z" and (h.cbore or h.spotface or h.bottom != "through") for h in a.holes
    )

    # A pattern annotates only when it accounts for the whole spec group —
    # a 7th same-size hole off the circle would make "7× ... EQ SP ON BC"
    # a lie about six of them.
    patterns = {frozenset(p.holes): p for p in found_patterns}

    def _build_callout(holes, pattern):
        h = holes[0]
        step = h.cbore or h.spotface
        if h.cbore and h.spotface:
            _log.info(
                "Hole ø%s has both cbore and spotface; spotface not in the callout",
                _fmt(h.diameter),
            )
            step = h.cbore
        through = h.bottom == "through"
        suffix = (
            f"EQ SP ON ø{_fmt(pattern.diameter)} BC" if isinstance(pattern, BoltCircle) else None
        )
        return HoleCallout(
            _fmt(h.diameter),
            count=len(holes) if len(holes) > 1 else None,
            through=through,
            depth=None if through else _fmt(h.depth),
            cbore_dia=_fmt(step.diameter) if step else None,
            cbore_depth=_fmt(step.depth) if step else None,
            suffix=suffix,
            draft=draft,
        )

    def _rim_tip(centre, elbow, holes):
        """Pull the tip from the hole centre to its circumference."""
        r = holes[0].diameter * a.SCALE / 2
        dx, dy = elbow[0] - centre[0], elbow[1] - centre[1]
        norm = math.hypot(dx, dy)
        if norm <= r:
            return centre
        return (centre[0] + dx / norm * r, centre[1] + dy / norm * r)

    def _add(view, i, tip, elbow, side, callout):
        dwg.add(
            Leader(
                tip=(tip[0], tip[1], 0),
                elbow=(elbow[0], elbow[1], 0),
                label="",
                draft=draft,
                text_side=side,
                callout=callout,
            ),
            f"hc_{view}{i}",
        )

    for view, view_groups in by_view.items():
        to_page = view_of_axis[{"plan": "z", "front": "y", "side": "x"}[view]][1]
        specs = []
        for holes in view_groups:
            pattern = patterns.get(frozenset(holes))
            specs.append((holes, _build_callout(holes, pattern), pattern))
        # No fixed cap (#36): every spec is attempted; the per-view placement
        # bounds below (front-view shaft rows, plan/side strip Y-solver) are the
        # real limit, and any callout that genuinely doesn't fit surfaces as
        # callout_dropped. Largest diameters first so the most significant
        # features win the available room.
        specs.sort(key=lambda s: s[0][0].diameter, reverse=True)

        if view == "front":
            # Below the view, vertical shafts. Rows are assigned right-to-
            # left so a deeper row's shaft never crosses a shallower row's
            # right-running label; left-side labels get an explicit guard.
            specs.sort(key=lambda s: max(to_page(h)[0] for h in s[0]), reverse=True)
            occupied: list[tuple] = []  # (x0, x1, row_y) of placed labels
            for i, (holes, callout, pattern) in enumerate(specs):
                w = callout.callout_width
                centre = to_page(max(holes, key=lambda h: to_page(h)[0]))
                elbow_y = front_bottom - 0.6 * a.DIM_PAD - i * min_gap
                if centre[0] + gap + w <= a.PAGE_W - a.margin:
                    side, x0, x1 = "right", centre[0] + gap, centre[0] + gap + w
                elif centre[0] - gap - w >= a.margin:
                    side, x0, x1 = "left", centre[0] - gap - w, centre[0] - gap
                else:
                    _log.info("Hole callout ø%s skipped (no room)", _fmt(holes[0].diameter))
                    _record_callout_drop(dwg, view, holes[0].diameter, "no room beside the view")
                    continue
                # the title block only constrains rows that reach its x-range
                floor = (tb_top + 4) if x1 > tb_left - 4 else a.margin + 4
                if elbow_y < floor:
                    _log.info(
                        "Hole callout ø%s skipped (front strip full)", _fmt(holes[0].diameter)
                    )
                    _record_callout_drop(dwg, view, holes[0].diameter, "front strip full")
                    continue
                if any(
                    ox0 <= centre[0] <= ox1 and row_y > elbow_y for ox0, ox1, row_y in occupied
                ):
                    _log.info(
                        "Hole callout ø%s skipped (shaft would cross another callout)",
                        _fmt(holes[0].diameter),
                    )
                    _record_callout_drop(
                        dwg, view, holes[0].diameter, "shaft would cross another callout"
                    )
                    continue
                elbow = (centre[0], elbow_y)
                occupied.append((x0, x1, elbow_y))
                _add(view, i, _rim_tip(centre, elbow, holes), elbow, side, callout)
                _add_furniture(dwg, a, view, i, pattern, to_page)
            continue

        # plan / side: two-pass leader placement.
        # Pass 1 — boundary assignment: each spec goes to the nearest strip
        #   boundary (right or left) whose label fits within the page.
        # Pass 2 — Y placement via Cassowary: leaders stay within the view's
        #   Y extent, are at least min_gap apart, and stay near their natural
        #   (hole-centre) Y position.
        edge_right = plan_right if view == "plan" else side_right
        edge_left = plan_left if view == "plan" else None

        right_strip = a.pv_zones.right if view == "plan" else a.sv_zones.right
        # Elbow offset past the view boundary: only needed in the plan view when
        # a section line will be placed (its extension lines overhang by
        # ~arrow_length).  Side view and section-free plan views use 0 so the
        # shaft terminates at the boundary instead of crossing it.
        elbow_dx = draft.arrow_length if view == "plan" and will_have_section_line else 0.0

        # Y bounds: elbows must stay within the view's projected Y extent.
        if view == "plan":
            y_min, y_max = a.PV_Y - a.pv_hh, a.PV_Y + a.pv_hh
        else:
            y_min, y_max = a.SV_Y - a.fv_hh, a.SV_Y + a.fv_hh

        # --- Pass 1: boundary assignment ---
        right_queue = []  # (holes, callout, pattern, natural_y, rep)
        left_queue = []

        for holes, callout, pattern in specs:
            w = callout.callout_width
            rep_r = max(holes, key=lambda h: to_page(h)[0])
            centre_r = to_page(rep_r)
            d_right = edge_right - centre_r[0]

            if edge_left is not None:
                rep_l = min(holes, key=lambda h: to_page(h)[0])
                centre_l = to_page(rep_l)
                d_left = centre_l[0] - edge_left
            else:
                rep_l = centre_l = None
                d_left = float("inf")

            # Side callouts below the iso view (always the case in practice) may
            # reach the full page width; plan callouts are constrained by the iso.
            right_limit = (
                right_strip.outer_limit
                if view == "plan" or centre_r[1] >= iso_y0 - draft.font_size
                else a.PAGE_W - a.margin
            )
            can_right = (edge_right + elbow_dx) + gap + w <= right_limit
            can_left = edge_left is not None and (edge_left - elbow_dx) - gap - w >= a.margin

            if not can_right and not can_left:
                _log.info("Hole callout ø%s skipped (no room)", _fmt(holes[0].diameter))
                _record_callout_drop(dwg, view, holes[0].diameter, "no room beside the view")
                continue

            if can_right and (not can_left or d_right <= d_left):
                right_queue.append((holes, callout, pattern, centre_r[1], rep_r))
            else:
                left_queue.append((holes, callout, pattern, centre_l[1], rep_l))

        # Sort each queue by natural Y so leaders don't cross.
        right_queue.sort(key=lambda s: s[3])
        left_queue.sort(key=lambda s: s[3])

        # --- Pass 2: Y placement ---
        right_ys = _solve_strip_ys([s[3] for s in right_queue], min_gap, y_min, y_max)
        left_ys = _solve_strip_ys([s[3] for s in left_queue], min_gap, y_min, y_max)

        if right_ys is None and right_queue:
            right_ys = _greedy_strip_ys(
                [s[3] for s in right_queue], min_gap, y_min, y_max, prefix=True
            )
            n_drop = len(right_queue) - len(right_ys)
            if n_drop:
                _log.warning(
                    "plan/side right strip: %d of %d bore callouts skipped (strip full)",
                    n_drop,
                    len(right_queue),
                )
                for holes, *_ in right_queue[len(right_ys) :]:
                    _record_callout_drop(dwg, view, holes[0].diameter, "right strip full")
            right_queue = right_queue[: len(right_ys)]
        if left_ys is None and left_queue:
            left_ys = _greedy_strip_ys(
                [s[3] for s in left_queue], min_gap, y_min, y_max, prefix=True
            )
            n_drop = len(left_queue) - len(left_ys)
            if n_drop:
                _log.warning(
                    "plan/side left strip: %d of %d bore callouts skipped (strip full)",
                    n_drop,
                    len(left_queue),
                )
                for holes, *_ in left_queue[len(left_ys) :]:
                    _record_callout_drop(dwg, view, holes[0].diameter, "left strip full")
            left_queue = left_queue[: len(left_ys)]

        for i, ((holes, callout, pattern, _, rep), elbow_y) in enumerate(
            zip(right_queue, right_ys, strict=True)
        ):
            centre = to_page(rep)
            elbow = (edge_right + elbow_dx, elbow_y)
            tip = _rim_tip(centre, elbow, holes)
            # Safety clamp: arrowhead must sit inside the view boundary.
            tip = (min(tip[0], edge_right - draft.arrow_length), tip[1])
            _add(view, i, tip, elbow, "right", callout)
            _add_furniture(dwg, a, view, i, pattern, to_page)

        assert edge_left is not None or not left_queue  # populated only when edge_left is set
        for i, ((holes, callout, pattern, _, rep), elbow_y) in enumerate(
            zip(left_queue, left_ys, strict=True), start=len(right_queue)
        ):
            centre = to_page(rep)
            elbow = (edge_left - elbow_dx, elbow_y)  # type: ignore[operator]
            tip = _rim_tip(centre, elbow, holes)
            tip = (max(tip[0], edge_left + draft.arrow_length), tip[1])
            _add(view, i, tip, elbow, "left", callout)
            _add_furniture(dwg, a, view, i, pattern, to_page)


def _add_title_block(dwg, a):
    """Add the title block annotation."""
    tb = TitleBlock(
        a.title,
        a.number,
        scale=format_drawing_scale(a.SCALE),
        general_tolerance=a.tolerance,
        designed_by=a.drawn_by,
        revision="A",
        legal_owner="",
        width=a.TB_W,
        draft=dwg.draft,
    ).locate(Location((a.PAGE_W - a.TB_W - 11, 11, 0)))
    dwg.add(tb, "title_block")


def _iso_bbox(dwg):
    """(min_x, min_y, max_x, max_y) of the placed iso view, hidden lines included."""
    vis, hid = dwg.views["iso"]
    bb = vis.bounding_box()
    x0, y0, x1, y1 = bb.min.X, bb.min.Y, bb.max.X, bb.max.Y
    if hid:
        hb = hid.bounding_box()
        x0, y0 = min(x0, hb.min.X), min(y0, hb.min.Y)
        x1, y1 = max(x1, hb.max.X), max(y1, hb.max.Y)
    return x0, y0, x1, y1


def _bbox_within(bb, region, tol: float = 0.5) -> bool:
    """True if (min_x, min_y, max_x, max_y) *bb* fits inside *region* within *tol*."""
    return (
        bb[0] >= region[0] - tol
        and bb[1] >= region[1] - tol
        and bb[2] <= region[2] + tol
        and bb[3] <= region[3] + tol
    )


def _project_iso(dwg, a, scale, shape_s=None):
    """(Re-)project the iso view at *scale* (an absolute factor, not a fraction).

    Pass *shape_s* when the part is already scaled by *scale* to skip the copy.
    """
    la = (a.cx * scale, a.cy * scale, a.cz * scale)
    off = (a.bbox_max * scale + 100) / math.sqrt(3)
    camera = (la[0] + off, la[1] + off, la[2] + off)
    dwg.add_view(
        "iso",
        shape_s if shape_s is not None else a.part.scale(scale),
        camera,
        (0, 0, 1),
        (a.ISO_X, a.ISO_Y),
        look_at=la,
        scaled=True,
    )
    if scale != dwg.scale:
        # add_view derives ViewCoordinates from the drawing scale; an iso
        # projected at a different scale needs them rebuilt so
        # dwg.at("iso", ...) keeps mapping world points correctly.
        axes = view_axes(camera, (0, 0, 1), la)
        dwg._coords["iso"] = ViewCoordinates(axes, a.ISO_X, a.ISO_Y, a.cx, a.cy, a.cz, scale)


def _fit_iso_view(dwg, a, annotate: bool = True):
    """Scale the iso view to fill its page zone, captioning it NTS when the
    scale differs from sheet scale.  Pass ``annotate=False`` to suppress the
    NTS note (used when ``auto_dims=False``).

    The iso is always centred at (ISO_X, ISO_Y) which sits at the centre of
    the available zone.  The projection is linear, so the factor needed to
    fill the zone can be computed from the measured extents without iteration.

    - Overflow (needed < 1): shrink with 2 % safety margin.
    - Under-fill (needed > 1): grow to 90 % of zone, leaving breathing room.
    - Within 5 % of sheet scale: leave as-is (no NTS label).
    """
    # Use the precomputed iso zone (largest empty rectangle).  A section view
    # only constrains the iso region when it shares the iso's y-range; one that
    # sits entirely below the iso (e.g. when the iso is in an upper-right zone)
    # leaves the region's left edge untouched.
    region_left = a.iso_left_limit
    if "section_aa" in dwg.views:
        sec_vis, sec_hid = dwg.views["section_aa"]
        sec_bb = sec_vis.bounding_box()
        sec_right = sec_bb.max.X
        sec_y0, sec_y1 = sec_bb.min.Y, sec_bb.max.Y
        if sec_hid:
            shb = sec_hid.bounding_box()
            sec_right = max(sec_right, shb.max.X)
            sec_y0, sec_y1 = min(sec_y0, shb.min.Y), max(sec_y1, shb.max.Y)
        if sec_y0 < a.iso_top_limit and a.iso_bottom_limit < sec_y1:
            region_left = max(region_left, sec_right + 4)
    region = (region_left, a.iso_bottom_limit, a.iso_right_limit, a.iso_top_limit)
    bb = _iso_bbox(dwg)
    ratios = [
        avail / extent
        for extent, avail in (
            (a.ISO_X - bb[0], a.ISO_X - region[0]),
            (bb[2] - a.ISO_X, region[2] - a.ISO_X),
            (a.ISO_Y - bb[1], a.ISO_Y - region[1]),
            (bb[3] - a.ISO_Y, region[3] - a.ISO_Y),
        )
        if extent > 0
    ]
    needed = min(ratios, default=1.0)
    if needed >= 1.0:
        # Iso fits; grow to 90 % of zone — leaves comfortable breathing room.
        margin_pct = 0.90
    else:
        # Iso overflows; shrink to just fit with 2 % safety margin.
        margin_pct = 0.98
    factor = math.floor(needed * margin_pct * 10000) / 10000
    if needed >= 1.0:
        factor = max(factor, 1.0)  # grow branch must never shrink
        factor = min(factor, _ISO_MAX_GROW)  # never dwarf the dimensioned views
    if abs(factor - 1.0) < 0.05:
        return  # within 5 % of sheet scale — no rescale, no NTS label
    _project_iso(dwg, a, a.SCALE * factor)
    bb = _iso_bbox(dwg)
    if factor < 1.0 and not _bbox_within(bb, region):
        _log.warning("Iso view still overflows its page region at %g× sheet scale", factor)
    if annotate:
        font = dwg.draft.font_size
        dwg.add(
            Note(
                "ISO VIEW (NTS)",
                (a.ISO_X, max(bb[1] - 2 * font, a.margin + font)),
                dwg.draft,
            ),
            "note_iso_nts",
        )
    _log.info("Iso view scaled to %g× sheet scale%s", factor, " (NTS)" if annotate else "")


def build_drawing(
    step_file: str | Path | Shape,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    scale: float | None = None,
    page: str | tuple | None = None,
    auto_dims: bool = True,
    pmi: Literal["off", "report", "annotate"] = "off",
) -> Drawing:
    """Build a customisable 4-view :class:`Drawing` without exporting it.

    Same arguments as :func:`make_drawing`, but returns the live :class:`Drawing`
    so you can add or remove annotations and add section/auxiliary views before
    calling :meth:`Drawing.export`. ``make_drawing(...)`` is exactly
    ``build_drawing(...).export()``.

    Args:
        auto_dims: pass ``False`` to skip the automatic dimensions,
            centrelines, and leaders (#74) — the automatic set assumes a
            turned part and is wrong for prismatic geometry. Views, scale,
            page, and title block are still produced; add your own
            annotations before export. (Annotations added by the default can
            also be removed wholesale with :meth:`Drawing.clear_annotations`.)

    Returns:
        A :class:`Drawing` with the standard front/plan/side/iso views projected
        and the automatic dimensions + title block already added.
    """
    stem = "drawing" if isinstance(step_file, Shape) else Path(step_file).stem
    out = out or stem
    for _ext in (".svg", ".dxf"):
        if out.endswith(_ext):
            out = out[: -len(_ext)]
            break
    title = title or stem.replace("_", " ").upper()

    a = _analyse(
        step_file, title, number, tolerance, drawn_by, out, scale=scale, page=page, pmi=pmi
    )

    cxs, cys, czs = a.cx * a.SCALE, a.cy * a.SCALE, a.cz * a.SCALE
    look_at = (cxs, cys, czs)
    dist = a.bbox_max * a.SCALE + 100

    dwg = Drawing(
        scale=a.SCALE,
        page_w=a.PAGE_W,
        page_h=a.PAGE_H,
        tb_w=a.TB_W,
        draft=draft_preset(font_size=_FONT_SIZE, decimal_precision=1),
        look_at=look_at,
        dist=dist,
        centroid=(a.cx, a.cy, a.cz),
        out=out,
        part=a.part,
        cyls=a.cyls,
    )
    dwg._analysis = a  # expose analysis namespace for testing and future strip access

    part_s = a.part.scale(a.SCALE)
    dwg.add_view("front", part_s, (cxs, cys - dist, czs), (0, 0, 1), (a.FV_X, a.FV_Y), scaled=True)
    dwg.add_view("plan", part_s, (cxs, cys, czs + dist), (0, 1, 0), (a.PV_X, a.PV_Y), scaled=True)
    dwg.add_view("side", part_s, (cxs + dist, cys, czs), (0, 0, 1), (a.SV_X, a.SV_Y), scaled=True)
    _project_iso(dwg, a, a.SCALE, shape_s=part_s)

    if auto_dims:
        # Snapshot outer_limits before _auto_annotate tightens them against the
        # initial (possibly overflowing) iso.  After _fit_iso_view rescales the
        # iso we restore all three right strips to min(original, final_iso_x_limit)
        # so each strip reflects actual final geometry, not the transient state.
        _fv_ol = a.fv_zones.right.outer_limit
        _pv_ol = a.pv_zones.right.outer_limit
        _sv_ol = a.sv_zones.right.outer_limit
        _auto_annotate(dwg, a)
        _fit_iso_view(dwg, a)
        _ix0, _iy0, _, _iy1 = _iso_bbox(dwg)
        _final_iso_x_lim = _ix0 - 4
        a.fv_zones.right.outer_limit = min(_fv_ol, _final_iso_x_lim)
        a.pv_zones.right.outer_limit = min(_pv_ol, _final_iso_x_lim)
        # Only re-cap the SV right strip when the iso shares its y-range (see the
        # matching guard in _auto_annotate); otherwise restore its full width.
        if (a.SV_Y - a.fv_hh) < _iy1 and _iy0 < (a.SV_Y + a.fv_hh):
            a.sv_zones.right.outer_limit = min(_sv_ol, _final_iso_x_lim)
        else:
            a.sv_zones.right.outer_limit = _sv_ol
    else:
        _fit_iso_view(dwg, a, annotate=False)
        _add_title_block(dwg, a)
    return dwg


# ---------------------------------------------------------------------------
# Direct export (SVG + DXF)
# ---------------------------------------------------------------------------


def make_drawing(
    step_file: str | Path | Shape,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    scale: float | None = None,
    page: str | tuple | None = None,
    auto_dims: bool = True,
    pmi: Literal["off", "report", "annotate"] = "off",
) -> tuple[str, str]:
    """Generate a 4-view technical drawing from a STEP file or build123d object.

    Args:
        step_file: Path to a STEP/STP file, or a build123d ``Shape`` (e.g. a
            ``Part``, ``Solid``, or ``Compound``) to draw directly.
        out: Output path stem (default: input filename stem, or ``"drawing"``
            when a build123d object is passed).
        title: Part title for the title block (default: stem uppercased).
        number: Drawing number (e.g. ``"DWG-042"``).
        tolerance: General tolerance string (e.g. ``"ISO 2768-m"``).
        drawn_by: Designer name for the title block.
        scale: Drawing-scale override (e.g. ``5`` for 5:1, ``0.5`` for 1:2).
            Default: chosen automatically by :func:`choose_scale`.
        page: Page-size override — an ISO name (``"A3"``), ``"WIDTHxHEIGHT"``
            in mm, or a ``(width, height)`` tuple. Default: chosen
            automatically by :func:`choose_scale`.
        auto_dims: pass ``False`` to skip the automatic dimensions,
            centrelines, and leaders (#74) — views, scale, page, and title
            block only.

    Returns:
        Tuple of ``(svg_path, dxf_path)`` for the generated files.

    This is a thin wrapper: ``make_drawing(...)`` is ``build_drawing(...).export()``.
    To add or remove annotations or add section/auxiliary views before export,
    call :func:`build_drawing` and use the returned :class:`Drawing`.
    """
    return build_drawing(
        step_file,
        out=out,
        title=title,
        number=number,
        tolerance=tolerance,
        drawn_by=drawn_by,
        scale=scale,
        page=page,
        auto_dims=auto_dims,
        pmi=pmi,
    ).export()


# ---------------------------------------------------------------------------
# Script generation (Cog-enabled .py output)
# ---------------------------------------------------------------------------


def _write_script(a) -> str:
    """Write an editable script at ``a.out + '.py'`` that calls make_drawing()."""
    py_path = a.out + ".py"
    py_name = Path(py_path).name

    cog_output = "\n".join(
        [
            f"STEP_FILE = {a.step_file!r}",
            f"TITLE = {a.title!r}",
            f"NUMBER = {a.number!r}",
            f"TOLERANCE = {a.tolerance!r}",
            f"DRAWN_BY = {a.drawn_by!r}",
        ]
    )

    cog_block = (
        "# [[[cog\n"
        "# ── Config: edit these, then run `cog -r <script>.py` to update ────────────\n"
        f"_STEP_FILE = {a.step_file!r}\n"
        f"_TITLE     = {a.title!r}\n"
        f"_NUMBER    = {a.number!r}\n"
        f"_TOLERANCE = {a.tolerance!r}\n"
        f"_DRAWN_BY  = {a.drawn_by!r}\n"
        "try:\n"
        "    cog  # NameError → not under cog\n"
        "    for _k, _v in [\n"
        "        ('STEP_FILE', repr(_STEP_FILE)), ('TITLE', repr(_TITLE)),\n"
        "        ('NUMBER', repr(_NUMBER)), ('TOLERANCE', repr(_TOLERANCE)),\n"
        "        ('DRAWN_BY', repr(_DRAWN_BY)),\n"
        "    ]:\n"
        "        cog.outl(f'{_k} = {_v}')\n"
        "except NameError:\n"
        "    pass\n"
        "# ]]]\n"
        f"{cog_output}\n"
        "# [[[end]]]"
    )

    _tq = '"""'
    _safe_doc_title = a.title.replace(_tq, "'''")
    _safe_doc_number = a.number.replace(_tq, "'''")
    header = (
        f"#!/usr/bin/env python3\n"
        f'"""\n'
        f"{_safe_doc_title} — Technical drawing ({_safe_doc_number}).\n"
        f"\n"
        f"Auto-generated by make-drawing. Edit freely.\n"
        f"To update metadata: edit _STEP_FILE / _TITLE / etc. in the cog block, then run:\n"
        f"  cog -r {py_name}   (pip install cogapp)\n"
        f"\n"
        f"Run:  uv run python {py_name}\n"
        f'"""\n'
        f"import os as _os\n"
        f"from draftwright import build_drawing\n"
        f"\n"
        f"# ── Config (auto-updated by cog) ──────────────────────────────────────────────\n"
    )

    run_section = (
        "\n"
        "# ── Build drawing (standard 4-view layout + automatic dimensions) ─────────────\n"
        "_stem = _os.path.splitext(__file__)[0]\n"
        "dwg = build_drawing(\n"
        "    STEP_FILE,\n"
        "    out=_stem,\n"
        "    title=TITLE,\n"
        "    number=NUMBER,\n"
        "    tolerance=TOLERANCE,\n"
        "    drawn_by=DRAWN_BY,\n"
        ")\n"
        "\n"
        "# ── Customise here — runs BEFORE export, so edits land in the output ───────────\n"
        "# dwg.views        'front' 'plan' 'side' 'iso'  → (visible, hidden) compounds\n"
        "# dwg.annotations  mutable list of annotation objects\n"
        "# dwg.at(view, x, y, z)  → page point (px, py, 0) mapped from world coordinates\n"
        "# dwg.add(obj, name) / dwg.remove(name)\n"
        "# dwg.add_view(name, shape, camera, up, position)  → section / auxiliary view\n"
        "# Example:\n"
        "#   from build123d_drafting import Leader\n"
        "#   dwg.add(Leader(tip=dwg.at('front', 10, 0, 5), elbow=(8, 40, 0),\n"
        "#                  label='ø4 BORE', draft=dwg.draft), 'ldr_bore')\n"
        "#   dwg.remove('dim_height')\n"
        "\n"
        "# ── Export ────────────────────────────────────────────────────────────────────\n"
        "svg_path, dxf_path = dwg.export(_stem)\n"
        'print(f"SVG \\u2192 {svg_path}")\n'
        'print(f"DXF \\u2192 {dxf_path}")\n'
    )

    content = header + cog_block + run_section
    Path(py_path).write_text(content, encoding="utf-8")
    _log.info("Script → %s", py_path)
    return py_path


def generate_script(
    step_file: str,
    out: str | None = None,
    title: str | None = None,
    number: str = "DWG-001",
    tolerance: str = "ISO 2768-m",
    drawn_by: str = "",
    pmi: Literal["off", "report", "annotate"] = "off",
) -> str:
    """Generate an editable Cog-enabled drawing script from a STEP file.

    Returns:
        Path to the generated ``.py`` file.
    """
    if isinstance(step_file, Shape):
        raise TypeError(
            "generate_script() requires a STEP file path — the generated script "
            "reloads geometry from disk and cannot embed a live build123d object. "
            "Use make_drawing() directly to draw an in-memory object."
        )
    stem = Path(step_file).stem
    out = out or stem
    for _ext in (".py", ".svg", ".dxf"):
        if out.endswith(_ext):
            out = out[: -len(_ext)]
            break
    title = title or stem.replace("_", " ").upper()
    a = _analyse(step_file, title, number, tolerance, drawn_by, out, pmi=pmi)
    return _write_script(a)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli():
    ap = argparse.ArgumentParser(
        description="Zero-AI STEP → technical drawing (SVG + DXF, or editable .py script)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("step_file", help="Input STEP file (.step / .stp)")
    ap.add_argument("--out", default=None, help="Output prefix (default: input stem)")
    ap.add_argument("--title", default=None, help="Part title for title block")
    ap.add_argument("--number", default="DWG-001", help="Drawing number")
    ap.add_argument("--tolerance", default="ISO 2768-m", help="General tolerance")
    ap.add_argument("--drawn-by", default="", help="Designer name")
    ap.add_argument(
        "--scale",
        type=float,
        default=None,
        help="Drawing-scale override, e.g. 5 for 5:1 or 0.5 for 1:2 (default: auto)",
    )
    ap.add_argument(
        "--page",
        default=None,
        help="Page-size override: A4..A0 or WIDTHxHEIGHT in mm, e.g. 420x297 (default: auto)",
    )
    ap.add_argument(
        "--script",
        action="store_true",
        help="Write an editable .py drawing script instead of SVG+DXF",
    )
    ap.add_argument(
        "--pmi",
        default="off",
        choices=["off", "report", "annotate"],
        help=(
            "AP242 PMI handling: 'off' (default) — ignore; "
            "'report' — log extracted PMI without annotating; "
            "'annotate' — add PMI-derived dimensions to the drawing"
        ),
    )
    ap.add_argument(
        "--pdf",
        action="store_true",
        help="Also write a PDF (requires draftwright[pdf] / cairosvg)",
    )
    ap.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed progress (default: warnings and errors only)",
    )
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    if args.script and (args.scale is not None or args.page is not None):
        ap.error("--scale/--page only apply to direct output; edit the generated script instead")

    if args.script:
        py_path = generate_script(
            step_file=args.step_file,
            out=args.out,
            title=args.title,
            number=args.number,
            tolerance=args.tolerance,
            drawn_by=args.drawn_by,
            pmi=args.pmi,
        )
        print(py_path)
    else:
        dwg = build_drawing(
            step_file=args.step_file,
            out=args.out,
            title=args.title,
            number=args.number,
            tolerance=args.tolerance,
            drawn_by=args.drawn_by,
            scale=args.scale,
            page=args.page,
            pmi=args.pmi,
        )
        svg_path, dxf_path = dwg.export()
        print(svg_path)
        print(dxf_path)
        if args.pdf:
            print(dwg.export_pdf())


if __name__ == "__main__":
    _cli()
