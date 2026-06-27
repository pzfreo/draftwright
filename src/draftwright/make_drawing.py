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

from __future__ import annotations

import argparse
import logging
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from build123d import (
    Align,
    Circle,
    Color,
    Compound,
    Edge,
    ExportDXF,
    ExportSVG,
    LineType,
    Location,
    Mode,
    Shape,
    Text,
    Vector,
)
from build123d_drafting.features import (
    HoleSpec,
    analyse_cylinders,
    find_hole_patterns,
    find_holes,
    full_cylinders,
)
from build123d_drafting.helpers import (
    Leader,
    LintIssue,
    ViewCoordinates,
    annotate,
    draft_preset,
    lint_drawing,
    set_page,
    view_axes,
)
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GeomAbs import (
    GeomAbs_Plane,
)
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader

from draftwright._core import (
    _DIM_PAD,
    _FONT_SIZE,
    _LADDER,
    _MARGIN,
    _PAGE_SIZES,
    _SCALES,
    _STRIP_GAP,
    _STRIP_SPACING,
    Analysis,
    _add_title_block,
    _axis_letter,
    _dim,
    _fmt,
    _iso_bbox,
    _legible_steps,
    _log,
    _parse_page,
    _Projector,
    _tag_sequence,
    _tb_width,
    _text_width,
)
from draftwright.annotate import _auto_annotate
from draftwright.export import (
    _export_shape,
    _render_pdf,
    add_svg_hyperlink,
    add_svg_metadata,
    fix_svg_page_size,
    sanitize_svg_arcs,
    set_dxf_metadata,
)
from draftwright.features import find_slots
from draftwright.fonts import PLEX_MONO
from draftwright.layout import (
    _greedy_strip_1d,
    _solve_strip_1d,
    fit_box,
)
from draftwright.linting import CoverageState, _suggest_fix, lint_feature_coverage
from draftwright.projection import (
    _exactify_silhouettes,
    _fit_iso_view,
    _project_iso,
    _raw_view_projector,
)
from draftwright.registry import AnnotationRegistry
from draftwright.repair import repair_drawing
from draftwright.sheet import (
    ViewBlock,
    _attribute_annotations,
    _build_zones,
    _layout_geometry,
    _measure_strips,
    _view_geom,
    choose_scale,
)

_TB_W = 150.0
# Minimum acceptable projected view dimension (page-mm).  Below this, annotation
# geometry (leader wires, centre marks, bore callout elbows) can degenerate and
# cause OCCT Standard_DomainError / SIGABRT (#129).
_MIN_VIEW_MM = 10.0


# ---------------------------------------------------------------------------
# SVG post-processing
# ---------------------------------------------------------------------------


# Equidistance tolerance (page-mm) for accepting a sampled silhouette spline as
# a circle about a known projected axis.  Loose enough to swallow HLR's spline
# approximation error, tight enough not to round a genuinely off-axis curve.
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
    pad = draft.pad_around_text
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
    total_w = cursor
    total_h = row_h * len(rows)
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


# ---------------------------------------------------------------------------
# Strip / zone layout model
# ---------------------------------------------------------------------------


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
# Single overall dim: two glyph-heights of line offset + the outboard label pad.
# The overall height dim leads the right ladder, so it carries an extra pad of
# clearance from the view above the first step dim's witness.
# Stacked step dims sit deeper so each ladder rung's label clears the rung below.

# A plan view with at least this many holes escalates to a hole chart when it is
# too dense to dimension every hole individually (#93). Below it, a dropped ref
# stays a legibility drop rather than tabulating a handful of holes.

# Smallest projected step height (page-mm) that can still carry a *legible*
# stacked dimension between its two extension lines.  Derived from what has to
# fit vertically: the label (font height) plus an arrowhead at each end plus
# the text clearance above and below — not an arbitrary page-mm cutoff (#13).
# Used as the single gate in BOTH _analyse (n_steps) and _auto_annotate
# (dim_step placement) so the two can never diverge.

# Minimum page-mm separation between two *consecutive* dimensioned step heights.
# Shoulders closer than this on the page read as one, so only the first of such
# a cluster is dimensioned and the rest surface via lint (#41). Sized to the
# value-label footprint (one glyph height + clearance) — enough to tell two
# stacked step dims apart, without dropping genuinely-distinct shoulders.

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


# ---------------------------------------------------------------------------
# Annotation depth estimators (Phase 2 of #118)
#
# These pure functions estimate the strip depth (mm) required for each
# inter-view boundary BEFORE view positions are fixed.  They are intentionally
# conservative (may over-estimate slightly).  Used by _analyse() (Phase 3) to
# set minimum corridor widths, and by _fits() (Phase 3) for consistent sheet
# selection.
# ---------------------------------------------------------------------------


def _cross_view_overlaps(dwg, a) -> int:
    """Count pairs of annotations attributed to *different* views whose boxes
    overlap — the #121 failure (a plan-view balloon over a front-view dimension).

    This is the repack trigger: a clean sheet (no cross-view overlap) is left
    exactly as pass 1 placed it, so well-estimated parts stay byte-identical;
    only a sheet with a real collision is re-packed (ADR 0004).
    """
    items = list(_attribute_annotations(dwg, a))
    n = 0
    for i in range(len(items)):
        _, vi, bi, li = items[i]
        for j in range(i + 1, len(items)):
            _, vj, bj, lj = items[j]
            # Only a collision involving a text label matters — two bare lines
            # (extension/leader) crossing between views is normal drafting.
            if vi == vj or not (li or lj):
                continue
            if min(bi[2], bj[2]) > max(bi[0], bj[0]) and min(bi[3], bj[3]) > max(bi[1], bj[1]):
                n += 1
    return n


def _annotations_out_of_bounds(dwg, a, tol: float = 1.0) -> bool:
    """True when any view-owned annotation's footprint extends past the drawable
    area — the second repack trigger besides cross-view overlap.  A ballooned
    plan view can overflow the page top (the balloon ring) without crossing
    another view, so the page must still escalate; the measure-and-repack pass
    re-sizes it because the overflowing balloons are part of the plan footprint
    (#92).  Only view-owned annotations count — those are what a repack can move
    by escalating the sheet."""
    lo, hi_x, hi_y = a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin
    for name, o in dwg._named.items():
        if dwg._anno_view.get(name) not in ("front", "plan", "side"):
            continue
        # Match the lint, which tests each item's FULL bounding_box (extension
        # lines, arrowheads, leader + balloon ring) — not just the label rect —
        # so a dimension whose extension lines overrun the page is caught too.
        try:
            b = o.bounding_box()
            bb = (b.min.X, b.min.Y, b.max.X, b.max.Y)
        except Exception:  # noqa: BLE001 — fall back to the label rect, else skip
            lb = getattr(o, "label_bbox", None)
            if lb is None:
                continue
            bb = lb
        if bb[0] < lo - tol or bb[1] < lo - tol or bb[2] > hi_x + tol or bb[3] > hi_y + tol:
            return True
    return False


def _measure_blocks(dwg, a) -> dict:
    """Measure each orthographic view's *actual* annotation footprint from the
    laid-out drawing (#121, ADR 0004 — "lay out, don't predict").

    Each view's four band depths are how far its annotations extend beyond its
    geometry box, **measured** from what the annotation passes produced — not
    estimated. Every annotation is attributed to the nearest view (by its
    label/box centre), and the band depth on a side is the furthest that view's
    annotations reach past the geometry edge there. Returns ``{view_name:
    ViewBlock}`` whose bands the packer can place disjoint, no ``_est_*`` needed.
    """
    geom = _view_geom(a)
    ext: dict = {v: None for v in geom}
    for _name, v, bb, _label in _attribute_annotations(dwg, a):
        e = ext[v]
        ext[v] = (
            bb
            if e is None
            else (min(e[0], bb[0]), min(e[1], bb[1]), max(e[2], bb[2]), max(e[3], bb[3]))
        )

    blocks: dict = {}
    for v, (cx, cy, hw, hh) in geom.items():
        e = ext[v]
        if e is None:
            blocks[v] = ViewBlock(hw, hh)
            continue
        blocks[v] = ViewBlock(
            hw,
            hh,
            top=max(0.0, e[3] - (cy + hh)),
            right=max(0.0, e[2] - (cx + hw)),
            bottom=max(0.0, (cy - hh) - e[1]),
            left=max(0.0, (cx - hw) - e[0]),
        )
    return blocks


def _analyse(
    step_file, title, number, tolerance, drawn_by, out, scale=None, page=None, pmi="off"
) -> Analysis:
    """Load STEP or use a build123d Shape, analyse geometry, compute layout.

    Returns an :class:`Analysis`.
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
    full_z = full_cylinders(z_cyls)
    z_diams = dedup_diams(full_z)
    cross_diams = dedup_diams(full_cylinders(cross_cyls))

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
    slots = find_slots(part)

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
    fv_zones, pv_zones, sv_zones = _build_zones(_g, margin, PAGE_H)

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

    return Analysis(
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
        slots=slots,
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
        proj=_Projector(
            fv_x=FV_X,
            fv_y=FV_Y,
            sv_x=SV_X,
            sv_y=SV_Y,
            pv_x=PV_X,
            pv_y=PV_Y,
            cx=cx,
            cy=cy,
            cz=cz,
            scale=SCALE,
        ),
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


@dataclass
class FeatureInfo:
    """A detected geometric feature, expressed in page coordinates.

    Returned by :meth:`Drawing.features`.  ``page_pos`` is in the coordinate
    system of the view passed to that call.
    """

    type: str
    page_pos: tuple
    diameter: float
    through: bool
    depth: float | None
    count: int


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
        items: ordered list of annotation objects (mutable).
        part: the source solid, when known — enables the feature-coverage lint.
        assembly: feature-coverage severity control — ``None`` auto-detects a
            multi-solid part as an assembly (per-part bores at ``info``),
            ``True``/``False`` forces it (#69).

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
        assembly=None,
    ):
        self.scale = scale
        self.part = part
        self._cyl_cache = cyls
        # None → the coverage lint auto-detects a multi-solid part as an
        # assembly; True/False forces assembly/strict severity (#69).
        self.assembly = assembly
        self.page_w = page_w
        self.page_h = page_h
        self.tb_w = tb_w
        self.draft = draft
        self.look_at = look_at
        self.dist = dist
        self.centroid = centroid
        self.out = out
        self.views: dict = {}
        self.items: list = []
        self._coords: dict = {}
        # Annotation identity, ownership, pins, and build issues live in the
        # registry (#138 / ADR 0005, Step 2). `_named` / `_anno_view` / `_pinned`
        # / `_build_issues` remain reachable as properties (below) so tests and
        # helpers that read through them keep working during the migration.
        self._registry = AnnotationRegistry()
        # Lint-side coverage signal (pattern callouts, patterned holes, dropped
        # callout diameters) lives in its own owner (#138 / ADR 0005, Step 3).
        # _pattern_callouts / _patterned_holes / _dropped_callout_diams remain
        # reachable as properties below.
        self._coverage = CoverageState()
        # One per-drawing cache for lint_drawing's per-view edge bboxes, keyed on
        # id(view shape). repair() / lint_summary() lint the SAME projected view
        # objects (self.views) repeatedly, so persisting it recomputes each
        # view's edges once instead of every lint (helpers #143/#164).
        self._view_edge_cache: dict = {}
        self.svg_path: str | None = None
        self.dxf_path: str | None = None
        self._analysis: Analysis | None = None

    # -- annotation registry (compat accessors, ADR 0005 §4) ------------------
    # The registry owns these four; they are exposed as their live containers so
    # code that reads or mutates ``dwg._named`` / ``_anno_view`` / ``_pinned`` /
    # ``_build_issues`` keeps working until those call sites are redirected.
    @property
    def _named(self) -> dict:
        return self._registry._named

    @_named.setter
    def _named(self, value) -> None:
        self._registry._named = value

    @property
    def _anno_view(self) -> dict:
        return self._registry._anno_view

    @_anno_view.setter
    def _anno_view(self, value) -> None:
        self._registry._anno_view = value

    @property
    def _pinned(self) -> set:
        return self._registry._pinned

    @_pinned.setter
    def _pinned(self, value) -> None:
        self._registry._pinned = value

    @property
    def _build_issues(self) -> list:
        return self._registry._build_issues

    @_build_issues.setter
    def _build_issues(self, value) -> None:
        self._registry._build_issues = value

    # -- coverage state (compat accessors, ADR 0005 §4) -----------------------
    # The CoverageState owner holds these three; exposed as their live containers
    # so code reading dwg._pattern_callouts / _patterned_holes /
    # _dropped_callout_diams keeps working until those sites are redirected.
    @property
    def _pattern_callouts(self) -> set:
        return self._coverage._pattern_callouts

    @_pattern_callouts.setter
    def _pattern_callouts(self, value) -> None:
        self._coverage._pattern_callouts = value

    @property
    def _patterned_holes(self) -> set:
        return self._coverage._patterned_holes

    @_patterned_holes.setter
    def _patterned_holes(self, value) -> None:
        self._coverage._patterned_holes = value

    @property
    def _dropped_callout_diams(self) -> list:
        return self._coverage._dropped_callout_diams

    @_dropped_callout_diams.setter
    def _dropped_callout_diams(self, value) -> None:
        self._coverage._dropped_callout_diams = value

    # -- coverage state operations (used by the annotation passes) ------------
    def _cover_pattern(self, callout_name, holes):
        """Record that placed *callout_name* documents *holes* (#92)."""
        self._coverage.cover_pattern(callout_name, holes)

    def _is_pattern_callout(self, name) -> bool:
        """Is *name* a placed pattern (grouped ``n× ⌀``) callout?"""
        return self._coverage.is_pattern_callout(name)

    def _is_hole_patterned(self, hole) -> bool:
        """Is *hole* already documented by a placed pattern callout?"""
        return self._coverage.is_hole_patterned(hole)

    def _reset_dropped_callout_diams(self):
        """Clear dropped-diameter tracking (top of :func:`_auto_annotate`)."""
        self._coverage.reset_dropped()

    def _drop_callout_diam(self, diam):
        """Record a diameter dropped by the per-view callout cap."""
        self._coverage.drop_diam(diam)

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
        axes = view_axes(camera, up, la)
        # Recover exact circles for revolution silhouettes that HLR projected as
        # approximating splines (#67) — a no-op when no revolution axis is
        # parallel to the view direction (e.g. iso/section views).
        if vl:
            vd = Vector(la[0] - camera[0], la[1] - camera[1], la[2] - camera[2])
            vd = vd.normalized()
            proj = _raw_view_projector(axes, la)
            vl, n_circ = _exactify_silhouettes(vl, shape_s.faces(), (vd.X, vd.Y, vd.Z), proj)
            if n_circ:
                _log.info("  %s: %d silhouette spline(s) refit to circles", name, n_circ)
        loc = Location((position[0], position[1], 0))
        placed = Compound(children=vl).locate(loc)
        placed_hid = Compound(children=hl).locate(loc) if hl else None
        self.views[name] = (placed, placed_hid)
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

    def view_bounds(self, view):
        """Return ``(x_min, y_min, x_max, y_max)`` of the projected geometry in
        *view*, or ``None`` if the view is unknown (#28).

        The box is the tight bounding box of the placed silhouette — visible
        plus hidden lines — in page coordinates (mm from the sheet origin), the
        same space :meth:`at` returns. Use it to place free-form notes, leader
        elbows and the like just outside a view without guessing offsets::

            x0, y0, x1, y1 = dwg.view_bounds("front")
            dwg.add(Note("SEE NOTE 1", (x1 + 5, (y0 + y1) / 2), dwg.draft))
        """
        placed = self.views.get(view)
        if placed is None:
            return None
        vis, hid = placed
        bb = vis.bounding_box()
        x0, y0, x1, y1 = bb.min.X, bb.min.Y, bb.max.X, bb.max.Y
        if hid:
            hb = hid.bounding_box()
            x0, y0 = min(x0, hb.min.X), min(y0, hb.min.Y)
            x1, y1 = max(x1, hb.max.X), max(y1, hb.max.Y)
        return (x0, y0, x1, y1)

    def features(self, view="front"):
        """Return detected geometric features in page coordinates for *view*.

        Holes are grouped by machining spec (diameter + depth + cbore) and
        returned as :class:`FeatureInfo` objects with ``count`` set to the
        number of identical holes at that spec.  Each group's ``page_pos``
        is the page position of the first hole in the group.

        The view determines which holes appear as circles (and are therefore
        annotatable from that view):

        - ``"plan"``  → Z-axis holes
        - ``"front"`` → Y-axis holes
        - ``"side"``  → X-axis holes

        Returns an empty list when no analysis is available or the view name
        is unrecognised.
        """
        a = self._analysis
        if a is None:
            return []

        _axis_for_view = {"plan": "z", "front": "y", "side": "x"}
        target_axis = _axis_for_view.get(view)
        if target_axis is None:
            return []

        if view not in self._coords:
            return []

        def _to_page(h):
            return self._coords[view].pp(*h.location)

        groups: dict = {}
        for h in a.holes:
            if _axis_letter(h) != target_axis:
                continue
            groups.setdefault(HoleSpec.from_hole(h), []).append(h)

        result = []
        for group in groups.values():
            rep = group[0]
            result.append(
                FeatureInfo(
                    type="hole",
                    page_pos=_to_page(rep),
                    diameter=rep.diameter,
                    through=rep.bottom == "through",
                    depth=None if rep.bottom == "through" else rep.depth,
                    count=len(group),
                )
            )
        return result

    def place_dim(self, p1, p2, side, view, draft, *, name=None, slot=8.0, **kwargs):
        """Add a :class:`~build123d_drafting.helpers.Dimension` that stacks cleanly
        with the auto-generated dimensions by delegating to the same strip-allocation
        system (:class:`Strip`) that :func:`build_drawing` uses internally.

        Args:
            p1, p2: page-coordinate tuples ``(px, py, 0)`` — use :meth:`at` to
                convert world coordinates.
            side: ``"above"``, ``"below"``, ``"left"``, or ``"right"``.
            view: ``"front"``, ``"plan"``, or ``"side"``.
            draft: the drawing's :attr:`draft` preset.
            name: optional annotation name for later :meth:`remove` / replace.
            slot: strip slot depth (mm); the perpendicular space reserved per dim.
            **kwargs: forwarded to ``Dimension`` (e.g. ``label=``, ``tolerance=``).

        Falls back to a fixed ``slot`` offset when the strip is full or when no
        layout analysis is available (e.g. when ``auto_dims=False`` was not used
        with :func:`build_drawing`).
        """
        a = self._analysis
        _view_zones = {"front": "fv_zones", "plan": "pv_zones", "side": "sv_zones"}
        strip = None
        if a is not None:
            zones = getattr(a, _view_zones.get(view, ""), None)
            if zones is not None:
                strip = getattr(zones, side, None)
        dist = slot
        if strip is not None:
            coord = strip.allocate(slot)
            if coord is not None:
                ax = 0 if side in ("left", "right") else 1
                if side in ("right", "above"):
                    dist = coord - max(p[ax] for p in (p1, p2))
                else:
                    dist = min(p[ax] for p in (p1, p2)) - coord
        # p1/p2 are page coordinates; Dimension labels the raw page distance when
        # no label is given, which is scale-too-big at non-1:1 scales. Supply the
        # real-world length (page distance ÷ drawing scale) unless the caller set
        # an explicit label.
        if "label" not in kwargs:
            page_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            kwargs["label"] = _fmt(page_len / self.scale)
        return self.add(_dim(p1, p2, side, max(dist, 4.0), draft, **kwargs), name)

    # -- annotations ----------------------------------------------------------
    def add(self, obj, name=None, view=None):
        """Register an annotation so lint and export include it; returns ``obj``.

        Re-using an existing ``name`` replaces the previously added object (it is
        dropped from :attr:`items`), so a name always maps to one object.

        ``view`` records which orthographic view ("front"/"plan"/"side") owns
        this annotation, so the layout can compose each view with its own
        annotations as a single footprint block (#121).  Pass ``None`` for
        drawing-level marks (title block, iso/section notes) that belong to no
        single view.
        """
        displaced = self._registry.named(name) if name is not None else None
        if displaced is not None:
            self.items.remove(displaced)
        annotate(obj, name)
        self.items.append(obj)
        # The registry records name -> obj and the owning view, and drops any pin
        # the replaced name carried — a replacement is a fresh object (#89) — and
        # clears a stale ownership tag when re-added view-less (#121).
        self._registry.add(obj, name, view)
        return obj

    def remove(self, name):
        """Remove a previously named annotation. Raises ``KeyError`` if absent."""
        obj = self._registry.remove(name)  # forgets object, view, and pin (#89)
        if obj is None:
            raise KeyError(f"no annotation named {name!r}")
        self.items.remove(obj)
        return obj

    def annotations(self) -> dict:
        """Return ``{name: type_name}`` for every *named* annotation (#27).

        Lets a script introspect what is already on the drawing before adding
        more — e.g. ``if "dim_width" not in dwg.annotations()`` — so it can do
        incremental edits without risking a silent name-collision replace.
        Unnamed annotations are omitted; iterate :attr:`items` for those.
        """
        return self._registry.annotations()

    def get_annotation(self, name):
        """Return the named annotation object, or ``None`` if no such name (#27)."""
        return self._registry.named(name)

    def add_table(self, rows, *, prefer="tr", name="table", block_cols=None):
        """Add a generic data table, placed in a free corner (#93).

        *rows* is a list of equal-length string tuples (``rows[0]`` is the
        header). The table is positioned by :func:`fit_box` clear of the views,
        title block, and existing annotations; *prefer* is the page corner to sit
        nearest. Returns the table annotation, or ``None`` if it has no rows or
        will not fit (recorded as ``table_dropped`` lint). Gear-data, BOM, and
        revision tables all go through here; :meth:`add_hole_table` is the
        hole-specific convenience built on it.
        """
        if not rows:
            return None
        table = _build_table(rows, self.draft, block_cols=block_cols)
        w, h = table.table_size
        a = self._analysis
        margin = a.margin if a is not None else 10.0
        pw = a.PAGE_W if a is not None else self.page_w
        ph = a.PAGE_H if a is not None else self.page_h
        region = (margin, margin, pw - margin, ph - margin)
        obstacles = [b for v in self.views if (b := self.view_bounds(v)) is not None]
        for o in self.items:
            try:
                bb = o.bounding_box()
            except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
                continue
            obstacles.append((bb.min.X, bb.min.Y, bb.max.X, bb.max.Y))
        pos = fit_box((w, h), region, obstacles, prefer)
        if pos is None:
            self._record_build_issue(
                "warning", "table_dropped", f"table {name!r} did not fit the sheet"
            )
            return None
        return self.add(table.locate(Location((pos[0], pos[1], 0))), name)

    def _hole_spec_groups(self, view):
        """Ordered ``(tag, [holes])`` spec-groups of *view*'s holes (tags A, B,
        …). The shared basis for the hole table's rows and its balloons, so the
        TAG column and the balloon glyphs line up."""
        a = self._analysis
        target = {"plan": "z", "front": "y", "side": "x"}.get(view)
        if a is None or target is None or view not in self._coords:
            return []

        groups: dict = {}
        for h in a.holes:
            if _axis_letter(h) == target:
                groups.setdefault(HoleSpec.from_hole(h), []).append(h)
        glist = list(groups.values())
        return list(zip(_tag_sequence(len(glist)), glist, strict=True))

    def _add_balloons(self, view, specs):
        """Place a leadered balloon for each ``(tag, j, hole)`` in *specs*,
        fitted into the halo the layout reserved around the view (#111).

        Each hole is assigned to the nearest reserved band — left, right, or top
        of the plan view (never the bottom: the front view abuts it there) — and
        the balloons in each band are spread along it with the 1D strip solver so
        none overlap, each pulled toward its hole's coordinate.  A :class:`Leader`
        then runs from the hole rim to the glyph.  Because the layout reserved
        this band before placing the views (:func:`_est_plan_halo` /
        :func:`_will_balloon`), the balloons sit in clear space off the part and
        no leader crosses a neighbouring view.
        """
        a = self._analysis
        if view not in self._coords or a is None:
            return
        pp = self._coords[view].pp
        fs = self.draft.font_size
        r = fs * 1.5  # circle comfortably larger than the glyph
        standoff = _STRIP_GAP
        gap = 2 * r + 2 * _STRIP_SPACING  # min centre-to-centre: balloon + padding both sides

        # Plan-view page edges; the reserved bands sit just outside them.
        pl, pr = a.PV_X - a.fv_hw, a.PV_X + a.fv_hw
        pt, pb = a.PV_Y + a.pv_hh, a.PV_Y - a.pv_hh
        sv_left = a.SV_X - a.sv_hw
        margin, ph = a.margin, a.PAGE_H

        # Stack the balloon ring *beyond* the annotations already placed around
        # the plan view, not on top of them (#121).
        za = a.pv_zones
        bot_dim = za.below.depth_used if za and za.below else 0.0
        left_dim = za.left.depth_used if za and za.left else 0.0
        right_dim = za.right.depth_used if za and za.right else 0.0
        # The TOP band is the one that goes stale: the hole-table escalation
        # deletes the X-location dims but never rewinds the above-strip cursor, so
        # za.above.depth_used keeps their high-water mark (~240 mm of phantom
        # corridor on CTC-02) and the top ring floats ~150 mm over empty space
        # (#125). Top balloons vary in X at a fixed Y, so they must clear the
        # DIMENSIONS spanning the plan's width above it — measure the real depth
        # of those (pitch dims dim_* AND PMI bore dims pmi_*). Construction
        # centrelines (bc_*) are crossable, not obstructions, so they are
        # excluded — their bolt-circle bbox would otherwise re-inflate the band.
        # Left/right/bottom are NOT stale: the deleted X-location dims only ever
        # allocated into the above strip (dim_locy tiers above the *side* view,
        # the width dim below is never removed), so those keep their correct
        # shallow strip depth.
        top_dim = 0.0
        for nm, obj in self._named.items():
            if not nm.startswith(("dim_", "pmi_")) or self._anno_view.get(nm) != view:
                continue
            try:
                ob = obj.bounding_box()
            except Exception:  # noqa: BLE001 — a mark with no bbox can't obstruct
                continue
            if ob.max.X > pl and ob.min.X < pr and ob.max.Y > pt:
                top_dim = max(top_dim, ob.max.Y - pt)

        # A bottom band (below PV, beyond the overall-width dim) is usable only
        # when the FV↔PV gap has room for the width dim *and* a balloon row;
        # otherwise bottom-edge holes fall back to the nearest side/top band.
        bottom_line = pb - bot_dim - standoff - r
        has_bottom = pb - (a.FV_Y + a.fv_hh) > bot_dim + standoff + 2 * r

        # Assign each hole to the nearest reserved band.
        bands: dict = {"left": [], "right": [], "top": [], "bottom": []}
        for tag, j, hole in specs:
            cx, cy = pp(*hole.location)
            choices = {"left": cx - pl, "right": pr - cx, "top": pt - cy}
            if has_bottom:
                choices["bottom"] = cy - pb
            bands[min(choices, key=lambda s: choices[s])].append((tag, j, hole, cx, cy))

        # left/right balloons vary in Y at a fixed X just outside the part; top
        # and bottom balloons vary in X at a fixed Y just beyond it. Each line is
        # offset by its side's dim depth so the ring sits clear of the dims.
        self._place_band(
            view,
            bands["left"],
            "y",
            pl - left_dim - standoff - r,
            margin + r,
            ph - margin - r,
            gap,
            fs,
            r,
        )
        self._place_band(
            view,
            bands["right"],
            "y",
            pr + right_dim + standoff + r,
            margin + r,
            ph - margin - r,
            gap,
            fs,
            r,
        )
        self._place_band(
            view,
            bands["top"],
            "x",
            pt + top_dim + standoff + r,
            pl - standoff,
            sv_left - r,
            gap,
            fs,
            r,
        )
        self._place_band(
            view,
            bands["bottom"],
            "x",
            bottom_line,
            pl - standoff,
            sv_left - r,
            gap,
            fs,
            r,
        )

    def _place_band(self, view, members, axis, line, lo, hi, gap, fs, r):
        """Spread *members* (``(tag, j, hole, cx, cy)``) along one reserved band
        with the strip solver, then render a leadered balloon for each (#111).

        *axis* is the band's free axis (``"y"`` for the left/right bands, ``"x"``
        for the top); *line* is the fixed coordinate of the other axis.  Overflow
        beyond ``[lo, hi]`` drops the tail rather than running balloons off-page.
        """
        if not members:
            return
        k = 4 if axis == "y" else 3  # index of cy / cx in the member tuple
        members.sort(key=lambda m: m[k])
        naturals = [m[k] for m in members]
        coords = (
            _solve_strip_1d(naturals, gap, lo, hi)
            or _greedy_strip_1d(naturals, gap, lo, hi)
            or _greedy_strip_1d(naturals, gap, lo, hi, prefix=True)
        )
        for (tag, j, hole, cx, cy), c in zip(members, coords):
            bx, by = (line, c) if axis == "y" else (c, line)
            self._render_balloon(view, tag, j, hole, cx, cy, bx, by, fs, r)

    def _render_balloon(self, view, tag, j, hole, cx, cy, bx, by, fs, r):
        """Build and add one balloon glyph + leader at solved centre ``(bx, by)``
        for hole ``(cx, cy)`` (#111)."""
        loc = Location((bx, by, 0))
        # The annotation layer fills closed paths, so a circle edge renders as a
        # disc. A thin annular FACE fills as a ring — i.e. a circle outline.
        ring_faces = [f.moved(loc) for f in (Circle(r) - Circle(r - 0.35)).faces()]
        text = Text(
            txt=tag,
            font_size=fs,
            font_path=PLEX_MONO,
            align=(Align.CENTER, Align.CENTER),
            mode=Mode.PRIVATE,
        ).locate(loc)
        parts = [*ring_faces, *text.faces()]
        # Leader from the hole rim to the balloon's near edge — the glyph is the
        # label, so label="".  Skipped when the balloon could not clear the hole
        # (degenerate fallback), where a leader would be a stub through the ring.
        dx, dy = bx - cx, by - cy
        dist = math.hypot(dx, dy)
        hole_r = hole.diameter * self.scale / 2
        if dist > hole_r + r:
            ux, uy = dx / dist, dy / dist
            tip = (cx + ux * hole_r, cy + uy * hole_r, 0)
            elbow = (bx - ux * r, by - uy * r, 0)
            parts.append(Leader(tip, elbow, "", self.draft))
        balloon = Compound(children=parts)
        # Furniture that legitimately sits on the view geometry — exempt from the
        # annotation-overlap / centreline lint, as the section arrows do.
        balloon.is_centerline = True
        self.add(balloon, f"balloon_{view}_{tag}_{j}", view=view)

    def _add_balloon(self, view, tag, j, hole):
        """Single-balloon convenience over :meth:`_add_balloons` (#111)."""
        self._add_balloons(view, [(tag, j, hole)])

    def add_hole_table(self, view="plan", *, prefer="tr", name=None, balloons=True):
        """Add a hole table for *view*'s holes, placed in a free corner (#93).

        One row per hole spec-group — ``TAG | ⌀ | DEPTH | QTY`` with tags
        ``A, B, …`` — placed via :meth:`add_table`. With *balloons* (the
        default) a circled tag is added at each hole keyed to its row. The table
        carries ``covers_diameters`` so the coverage lint counts the tabulated
        holes as dimensioned. Returns the table, or ``None`` when *view* has no
        holes or it will not fit.
        """
        groups = self._hole_spec_groups(view)
        if not groups:
            return None
        rows = [("TAG", "⌀", "DEPTH", "QTY")]
        diams = []
        for tag, holes in groups:
            h = holes[0]
            depth = "THRU" if h.bottom == "through" else (_fmt(h.depth) if h.depth else "")
            rows.append((tag, f"ø{_fmt(h.diameter)}", depth, str(len(holes))))
            diams.append(h.diameter)
        table = self.add_table(rows, prefer=prefer, name=name or f"hole_table_{view}")
        if table is None:
            return None
        # The table documents these diameters — let lint see that (#93).
        table.covers_diameters = tuple(diams)
        if balloons:
            self._add_balloons(
                view,
                [(tag, j, h) for tag, holes in groups for j, h in enumerate(holes)],
            )
        return table

    def pin(self, name):
        """Pin a named annotation so the engine never moves it (#89).

        A deliberate placement — by you or an AI — must win over automatic
        layout. :meth:`repair` will not re-place a pinned annotation, and the
        constraint solver (ADR 0003) treats it as fixed. Pinning fixes the
        *position*, not existence: :meth:`remove` and :meth:`clear_annotations`
        still apply. Raises ``KeyError`` if *name* is not a known annotation.
        Returns ``self`` for chaining.
        """
        if name not in self._registry:
            raise KeyError(f"no annotation named {name!r}")
        self._registry.pin(name)
        return self

    def unpin(self, name):
        """Release a pin so the engine may move *name* again (#89). Returns
        ``self``; a no-op if *name* was not pinned."""
        self._registry.unpin(name)
        return self

    def clear_annotations(self, keep=("title_block",)):
        """Remove all annotations except those named in *keep* (#74).

        Wholesale removal that does not depend on the automatic naming
        scheme — ``dwg.clear_annotations()`` strips every automatic dimension,
        leader, and centreline but keeps the title block.

        Returns:
            The list of removed annotation objects.
        """
        kept_named = self._registry.clear(keep)  # prunes names, views, and pins
        kept_ids = {id(o) for o in kept_named.values()}
        removed = [o for o in self.items if id(o) not in kept_ids]
        self.items = [o for o in self.items if id(o) in kept_ids]
        return removed

    def _record_build_issue(self, severity, code, message):
        """Record a lint issue discovered during construction (e.g. an
        annotation the layout had to drop). Surfaced by :meth:`lint` so a
        dropped feature is never silent."""
        self._registry.record_issue(LintIssue(severity=severity, code=code, message=message))

    def _reset_build_issues(self):
        """Clear build-time issues — called at the top of :func:`_auto_annotate`
        so re-annotation does not accumulate them."""
        self._registry.reset_issues()

    def _drop_build_issues(self, *codes):
        """Drop recorded build issues whose code is in *codes* (a fallback that
        restored tentatively-dropped annotations un-records their drop)."""
        self._registry.drop_issues(codes)

    # -- repair ---------------------------------------------------------------
    def repair(self, max_iter: int = 3):
        """Close the lint→repair loop: act on violations, don't only report them.

        After the greedy initial placement, re-place the dimensions behind the
        mechanically-clear violations and re-lint, bounded to *max_iter* passes:

        - ``dim_inside_part`` — the offset is on the wrong side; flip it once.
        - ``annotation_overlap`` — two labels collide; push one further out.

        Only engine-built dimensions (carrying ``_dw_spec``) are re-placeable;
        leaders, callouts and standards-judgement issues (e.g.
        ``missing_principal_dimension``) are left for the caller. Each side flip
        is attempted at most once and overlap pushes only move outward, so the
        loop terminates and a clean drawing is returned unchanged.

        A pass that would *net-increase* the issue count (e.g. an overlap push
        that shoves a label out of frame on a tight sheet) is rolled back and the
        loop stops, so :meth:`repair` never makes a drawing worse.

        Returns ``self`` for chaining.
        """
        return repair_drawing(self, max_iter)

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
        for ann in self.items:
            by_scale.setdefault(getattr(ann, "_dw_scale", self.scale), []).append(ann)
        if len(by_scale) <= 1:
            issues = lint_drawing(
                self.items,
                drawing_scale=self.scale,
                view_shapes=view_shapes,
                view_edge_cache=self._view_edge_cache,
            )
        else:
            issues = []
            for _scale, _anns in by_scale.items():
                issues += lint_drawing(
                    _anns,
                    drawing_scale=_scale,
                    view_shapes=view_shapes,
                    view_edge_cache=self._view_edge_cache,
                )
        if self.part is not None:
            if self._cyl_cache is None:
                self._cyl_cache = analyse_cylinders(self.part)
            issues += lint_feature_coverage(
                self.part,
                self.items,
                cyls=self._cyl_cache,
                exclude=self._coverage.dropped_diams,
                assembly=self.assembly,
            )
        issues += list(self._build_issues)
        # Attach a ready-to-paste fix snippet where one is computable (#29).
        # str | None — None when no concrete repair can be inferred.
        for i in issues:
            i.suggestion = _suggest_fix(i, self)
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
                    # Omit suggestion when None to keep the JSON non-breaking (#29).
                    **(
                        {"suggestion": s}
                        if (s := getattr(i, "suggestion", None)) is not None
                        else {}
                    ),
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
        link_rect = getattr(self, "_draftwright_link_rect", None)
        if link_rect is not None:
            add_svg_hyperlink(svg_path, link_rect)
        add_svg_metadata(svg_path)
        _log.info("SVG → %s", svg_path)

        dxf = ExportDXF()
        dxf.add_layer("part", line_weight=0.5)
        dxf.add_layer("hidden", line_weight=0.25)
        dxf.add_layer("dims", line_weight=0.05)
        self._add_shapes(dxf)
        set_dxf_metadata(dxf)
        dxf_path = out + ".dxf"
        dxf.write(dxf_path)
        _log.info("DXF → %s", dxf_path)

        self.svg_path = svg_path
        self.dxf_path = dxf_path
        return svg_path, dxf_path

    def export_pdf(self, out=None) -> str:
        """Write a PDF rendered from the SVG.  Requires ``cairosvg`` (install with
        ``pip install draftwright[pdf]``).  Calls :meth:`export` first if the SVG
        hasn't been written yet.  Returns the PDF path.

        The PDF carries a 'generated by draftwright' Creator metadata field and,
        over the title-block URL row, a clickable hyperlink to the project (a
        real PDF link annotation — the SVG ``<a>`` element is not understood by
        the PDF renderer, so it is added here via cairo)."""
        try:
            import cairosvg  # noqa: F401  (import-guard; the real work is in _render_pdf)
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
        _render_pdf(svg_path, pdf_path, self.page_h, getattr(self, "_draftwright_link_rect", None))
        _log.info("PDF → %s", pdf_path)
        return pdf_path

    def _add_shapes(self, exporter):
        """Add every view layer and annotation to *exporter* with error context."""
        for name, (vis, hid) in self.views.items():
            _export_shape(exporter, vis, "part", f"view {name!r}")
            if hid:
                _export_shape(exporter, hid, "hidden", f"view {name!r}")
        for ann in self.items:
            label = getattr(ann, "label", "") or type(ann).__name__
            _export_shape(exporter, ann, "dims", f"annotation {label!r}")


# 1D strip placement now lives in draftwright.layout (ADR 0003 phase 1, #79).
# These aliases keep the existing axis-specific callers and their tests working
# while the primitive is axis-neutral; later phases route through LayoutSolver.


# A view centre must move by more than this (mm) for the measure-and-repack
# pass to re-assemble.  Below it, the estimate already matched the measured
# footprint and pass 1 stands (the common, non-ballooned case).
_REPACK_TOL = 0.75


def _assemble(a, out, assembly, detail_view, auto_dims):
    """Project the 4 views for analysis *a*, run the automatic annotation
    passes, and fit the iso.  This is pass 1 of :func:`build_drawing`; with a
    repacked analysis it is also pass 2 of the measure-and-repack loop (#121)."""
    cxs, cys, czs = a.cx * a.SCALE, a.cy * a.SCALE, a.cz * a.SCALE
    dist = a.bbox_max * a.SCALE + 100

    dwg = Drawing(
        scale=a.SCALE,
        page_w=a.PAGE_W,
        page_h=a.PAGE_H,
        tb_w=a.TB_W,
        draft=draft_preset(font_size=_FONT_SIZE, decimal_precision=1, font_path=PLEX_MONO),
        look_at=(cxs, cys, czs),
        dist=dist,
        centroid=(a.cx, a.cy, a.cz),
        out=out,
        part=a.part,
        cyls=a.cyls,
        assembly=assembly,
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
        _auto_annotate(dwg, a, detail_view=detail_view)
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


def _repack_candidates(a, scale, page):
    """The (scale, page_w, page_h, tb_w) candidates the repack may choose from,
    mirroring :func:`choose_scale`: a user-fixed scale and/or page is honoured;
    otherwise the auto ladder (smallest legible sheet first) is searched."""
    if scale is not None and page is not None:
        pw, ph, tb = _parse_page(page)
        return [(float(scale), pw, ph, tb)]
    if page is not None:
        pw, ph, tb = _parse_page(page)
        return [(s, pw, ph, tb) for s in _SCALES]
    if scale is not None:
        return [(float(scale), pw, ph, _tb_width(pw)) for pw, ph in _PAGE_SIZES.values()]
    # Auto ladder, but floored at pass 1's chosen sheet: the measured blocks are
    # never smaller than the estimate that pass 1 already rejected the earlier
    # rungs against, and the repack's .fits is more permissive than choose_scale's
    # row model — so without this floor the repack could pick a *smaller* sheet
    # than pass 1 and make things worse (#121). Start the search at pass 1's rung.
    start = next(
        (
            i
            for i, (s, pw, ph, _tb) in enumerate(_LADDER)
            if s == a.SCALE and pw == a.PAGE_W and ph == a.PAGE_H
        ),
        0,
    )
    return list(_LADDER[start:])


def _repack(a, dwg, out, assembly, detail_view, scale=None, page=None):
    """Measure the laid-out drawing's *real* per-view annotation footprints and,
    when a view collides across views, pack the blocks disjoint — escalating the
    sheet/scale until the packed layout fits — then re-assemble (#121, ADR 0004 —
    "lay out, don't predict"; the (scale, page) choice is the outer search whose
    fitness is *do the packed disjoint blocks fit*).

    Returns ``(a2, dwg2)`` for the repacked drawing, or ``None`` when pass 1 has
    no cross-view overlap AND nothing overflows the drawable (the common case — a
    clean sheet is left exactly as placed, so well-estimated parts stay
    byte-identical) or when the repack would change nothing (same sheet/scale and
    no view actually moves).
    """
    if _cross_view_overlaps(dwg, a) == 0 and not _annotations_out_of_bounds(dwg, a):
        return None
    blocks = _measure_blocks(dwg, a)

    def _geom(cand):
        s, pw, ph, tb = cand
        return _layout_geometry(
            a.x_size, a.y_size, a.z_size, s, pw, ph, tb, None, 0, blocks=blocks
        )

    candidates = _repack_candidates(a, scale, page)
    fit = next(((c, gg) for c in candidates if (gg := _geom(c)).fits), None)
    if fit is None:
        # Nothing fits — keep the largest candidate and let lint report the
        # overflow (mirrors choose_scale's fallback rather than crashing).
        chosen = candidates[-1]
        g = _geom(chosen)
        _log.warning(
            "measure-repack: no standard sheet fits the measured layout; using %s", chosen
        )
    else:
        chosen, g = fit
    s, pw, ph, tb = chosen
    moved = max(
        abs(g.FV_X - a.FV_X),
        abs(g.FV_Y - a.FV_Y),
        abs(g.PV_X - a.PV_X),
        abs(g.PV_Y - a.PV_Y),
        abs(g.SV_X - a.SV_X),
        abs(g.SV_Y - a.SV_Y),
    )
    if s == a.SCALE and pw == a.PAGE_W and ph == a.PAGE_H and moved < _REPACK_TOL:
        return None
    fv_zones, pv_zones, sv_zones = _build_zones(g, a.margin, ph)
    a2 = replace(
        a,
        SCALE=s,
        PAGE_W=pw,
        PAGE_H=ph,
        TB_W=tb,
        x_offset=g.x_offset,
        FV_X=g.FV_X,
        FV_Y=g.FV_Y,
        PV_X=g.PV_X,
        PV_Y=g.PV_Y,
        SV_X=g.SV_X,
        SV_Y=g.SV_Y,
        fv_hw=g.fv_hw,
        fv_hh=g.fv_hh,
        pv_hh=g.pv_hh,
        sv_hw=g.sv_hw,
        sv_right=g.sv_right,
        iso_right_limit=g.iso_right,
        ISO_X=g.ISO_X,
        ISO_Y=g.ISO_Y,
        iso_left_limit=g.iso_left,
        iso_bottom_limit=g.iso_bottom,
        iso_top_limit=g.iso_top,
        proj=_Projector(
            fv_x=g.FV_X,
            fv_y=g.FV_Y,
            sv_x=g.SV_X,
            sv_y=g.SV_Y,
            pv_x=g.PV_X,
            pv_y=g.PV_Y,
            cx=a.cx,
            cy=a.cy,
            cz=a.cz,
            scale=s,
        ),
        fv_zones=fv_zones,
        pv_zones=pv_zones,
        sv_zones=sv_zones,
    )
    dwg2 = _assemble(a2, out, assembly, detail_view, auto_dims=True)
    return a2, dwg2


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
    detail_view: bool = False,
    pmi: Literal["off", "report", "annotate"] = "off",
    repair: bool = True,
    assembly: bool | None = None,
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
        repair: run the bounded lint→repair loop (:meth:`Drawing.repair`) after
            placement to fix mechanically-clear violations (a dim on the wrong
            side, two overlapping labels). Default ``True``; a no-op on a clean
            sheet. Pass ``False`` to inspect the raw greedy placement (#30).
        assembly: severity of the feature-coverage lint for a general-arrangement
            drawing. ``None`` (default) auto-detects — a multi-solid part is an
            assembly, whose per-part bores are reported at ``info`` rather than
            ``warning`` (a GA omits them by design). Force with ``True``/``False``
            (#69).

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

    # Pass 1: place + annotate from the estimated layout, then measure the real
    # per-view footprints and re-pack the blocks disjoint if a view actually
    # moves (#121, ADR 0004 — "lay out, don't predict").  Non-ballooned parts
    # measure ≈ estimate, so they skip pass 2 and stand byte-identical.
    dwg = _assemble(a, out, assembly, detail_view, auto_dims)
    if auto_dims:
        repacked = _repack(a, dwg, out, assembly, detail_view, scale=scale, page=page)
        if repacked is not None:
            a, dwg = repacked
    if repair:
        # Close the loop on the greedy placement: re-place dims behind any
        # mechanically-clear violations (overlap, wrong-side) and re-lint (#30).
        # A no-op on a clean sheet, so default-on costs nothing when there is
        # nothing to fix.
        dwg.repair()
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
    detail_view: bool = False,
    pmi: Literal["off", "report", "annotate"] = "off",
    assembly: bool | None = None,
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
        detail_view=detail_view,
        pmi=pmi,
        assembly=assembly,
    ).export()


# ---------------------------------------------------------------------------
# Script generation (Cog-enabled .py output)
# ---------------------------------------------------------------------------


def _write_script(a: Analysis) -> str:
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
        "# Prefer domain edits (place_dim / features) over page mechanics (at / Leader);\n"
        "# the engine places annotations automatically — say WHAT, not WHERE.\n"
        "# dwg.features(view)       → detected features → [FeatureInfo(.diameter .count .page_pos)]\n"
        "# dwg.place_dim(p1, p2, side, view, dwg.draft, name=…)  → add a dimension, auto-placed\n"
        "# dwg.annotations()        → {name: type} of every named annotation\n"
        "# dwg.get_annotation(name) → the named annotation object, or None\n"
        "# dwg.remove(name) / dwg.add(obj, name)\n"
        "# dwg.pin(name) / dwg.unpin(name)  → fix a placement so repair never moves it\n"
        "# dwg.lint_summary()       → {passed, score, by_code, issues:[…suggestion]}\n"
        "# dwg.repair()             → auto-fix mechanically-fixable lint (never worsens)\n"
        "# dwg.add_view(name, shape, camera, up, position)  → section / auxiliary view\n"
        "# dwg.items / dwg.views / dwg.at(view,x,y,z) / dwg.view_bounds(view)  → low-level escape\n"
        "# Example — add a linear dim (place_dim auto-stacks; endpoints via dwg.at):\n"
        "#   p1, p2 = dwg.at('front', 0, 0, 0), dwg.at('front', 40, 0, 0)\n"
        "#   dwg.place_dim(p1, p2, 'above', 'front', dwg.draft, name='dim_len')\n"
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
