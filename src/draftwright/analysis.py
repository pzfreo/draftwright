"""Geometry/feature analysis — build the Analysis namespace from a part (#138 / ADR 0005, P4).

`_analyse` imports the part (STEP or Shape), runs feature detection (holes,
patterns, cylinders, face levels), classifies it (rotational vs prismatic),
chooses the sheet (scale/page via `sheet.choose_scale`) and lays out the view
zones (`sheet._layout_geometry`/`_build_zones`) — returning the `Analysis`
namespace the rest of the pipeline reads. Sits above `sheet` and below
`make_drawing` in the DAG.
"""

from __future__ import annotations

import logging
import math

from build123d import Compound, Shape
from build123d_drafting.helpers import draft_preset
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader

from draftwright._core import (
    _DIM_PAD,
    _FONT_SIZE,
    _MARGIN,
    _MIN_VIEW_MM,
    Analysis,
    _legible_steps,
    _Projector,
)
from draftwright.recognition import (
    analyse_cylinders,
    analyse_face_levels,
    find_bosses,
    find_hole_patterns,
    find_holes,
    find_slots,
    find_turned_steps,
    full_cylinders,
)
from draftwright.sheet import (
    _build_zones,
    _layout_geometry,
    _measure_strips,
    choose_scale,
)

_log = logging.getLogger(__name__)

# Turned-part classification (#81): a rotational part's bounding box is square
# in XY to within _SQUARENESS_TOL, and its OD (largest full external Z cylinder)
# fills >= _OD_FILL_MIN of that envelope, axis within _OD_AXIS_TOL of centre.
_SQUARENESS_TOL = 0.05
_OD_FILL_MIN = 0.8
_OD_AXIS_TOL = 0.05


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
    return bool(
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
    od_axis = "z"
    if not is_rotational:
        # Fallback: a *horizontal* (X/Y) round body — its OD is a cross-axis cylinder
        # filling the square envelope perpendicular to that axis (#222). The Z check
        # above is untouched, so vertical parts classify exactly as before; this only
        # fires when Z-rotational fails and a cross-axis round body is present.
        sizes = {"x": x_size, "y": y_size, "z": z_size}
        ctr = {"x": cx, "y": cy, "z": cz}
        cross_full = full_cylinders(cross_cyls)
        for ax in ("x", "y"):
            ext = [c for c in cross_full if c.get("axis") == ax and c["external"]]
            # #222 targets a *single-OD* round body. A stepped shaft (multiple distinct
            # cross diameters) stays on the turned-diameter path, not the OD furniture.
            if len({round(c["diameter"], 1) for c in ext}) != 1:
                continue
            oc = max(ext, key=lambda c: c["diameter"], default=None)
            if oc is None:
                continue
            p0, p1 = (a for a in "xyz" if a != ax)
            off = math.hypot(
                oc["axis_xyz"]["xyz".index(p0)] - ctr[p0],
                oc["axis_xyz"]["xyz".index(p1)] - ctr[p1],
            )
            if _is_rotational(sizes[p0], sizes[p1], oc["diameter"], off):
                od_diam, od_axis_offset, od_axis, is_rotational = oc["diameter"], off, ax, True
                break
    if z_diams and not is_rotational:
        _log.info("Part classified prismatic; skipping OD/centreline/bore annotations")

    # Step Z-levels feed both the step-height ladder and the page-sizing step
    # count. For a vertical (Z-axis) turned part, take them from the unified
    # turned-step model (ADR 0008 step 1): it filters shoulders by the OD
    # silhouette, so an internal feature face — a blind bore's flat floor — is
    # never read as a phantom OD shoulder (the area-only filter in
    # analyse_face_levels admitted it). Prismatic and other parts keep the
    # general face-level scan, which find_turned_steps cannot replace (no
    # cylinders → no profile).
    _turned = find_turned_steps(part)
    if _turned is not None and _turned.axis == "z":
        step_zs = [z for z in _turned.shoulders if bb.min.Z + 0.6 < z < bb.max.Z - 0.6]
    else:
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
    bosses = find_bosses(part, cyls=(z_cyls, cross_cyls))  # detect once — the one inventory (#264)
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
        bosses=bosses,
        slots=slots,
        z_diams=z_diams,
        cross_diams=cross_diams,
        cyls=(z_cyls, cross_cyls),
        prof=_turned,
        od_diam=od_diam,
        is_rotational=is_rotational,
        od_axis=od_axis,
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
