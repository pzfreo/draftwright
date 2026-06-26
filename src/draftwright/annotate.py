"""Automatic annotation passes for the draftwright drawing engine.

Every pass takes a duck-typed ``dwg`` (a :class:`~draftwright.make_drawing.Drawing`)
and the :class:`~draftwright._core.Analysis` namespace ``a``, and adds dimensions,
callouts, centrelines, section/detail views, and the title block.  ``_auto_annotate``
is the single entry point; the others are reached through it.

This module imports only from :mod:`draftwright._core`, :mod:`draftwright.layout`,
and third-party libraries -- never from :mod:`draftwright.make_drawing` -- so the
module graph stays a DAG (#98 Phase C).
"""

from __future__ import annotations

import math

from build123d import (
    Arrow,
    Box,
    Compound,
    Edge,
    GeomType,
    HeadType,
    Mode,
    Pos,
    Vector,
)
from build123d_drafting.features import (
    BoltCircle,
    LinearArray,
    RectGrid,
    _full_cyls,
    _spec_key,
    find_bosses,
)
from build123d_drafting.helpers import (
    Centerline,
    CenterlineCircle,
    CenterMark,
    HoleCallout,
    Leader,
    Note,
    TitleBlock,
    ViewCoordinates,
    format_drawing_scale,
    view_axes,
)
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.TopTools import TopTools_ListOfShape

from draftwright._core import (
    _CONCENTRIC_TOL_MM,
    _DIAM_RE,
    _MIN_LOC_SEP_MM,
    _MIN_STEP_SEP_MM,
    _SLOT_DIM_DEPTH,
    _SLOT_DIM_HEIGHT,
    _SLOT_DIM_STEP,
    _SLOT_DIM_WIDTH,
    _TABULATE_MIN_HOLES,
    _TB_CLEAR,
    _TB_H,
    Analysis,
    _add_title_block,
    _axis_letter,
    _dim,
    _fmt,
    _greedy_strip_ys,
    _iso_bbox,
    _largest_empty_rect,
    _legible_steps,
    _log,
    _solve_strip_ys,
    _tag_sequence,
)
from draftwright.layout import LayoutSolver, Placeable


def _wrap_rows(header, data, ncols):
    """Reshape *data* rows into *ncols* side-by-side blocks (a wider, shorter
    table), each block headed by *header* — so a long hole chart fits the page.
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


def _is_concentric_hole(h, a: Analysis) -> bool:
    """True when *h* is an axial bore on the part centreline (turned base set)."""
    if _axis_letter(h) != "z":
        return False
    return math.hypot(h.location[0] - a.cx, h.location[1] - a.cy) <= _CONCENTRIC_TOL_MM


def _concentric_bore_diams(a: Analysis) -> list:
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


def _detect_step_repeat(step_zs, bb_min_z, bb_max_z, tol_frac=0.10):
    """Return (n, rise) if step_zs form a uniform staircase, else None.

    A uniform staircase has all inter-step rises (including from bb_min_z to the
    first step) within *tol_frac* of their mean.  Requires ≥3 detected interior
    steps to avoid false positives.  *n* is len(step_zs) + 1 when the top gap
    (bb_max_z − last step) also matches the mean, otherwise len(step_zs).
    """
    if len(step_zs) < 3:
        return None
    sorted_zs = sorted(step_zs)
    rises = [sorted_zs[0] - bb_min_z] + [
        sorted_zs[i + 1] - sorted_zs[i] for i in range(len(sorted_zs) - 1)
    ]
    mean_rise = sum(rises) / len(rises)
    if mean_rise <= 0:
        return None
    if not all(abs(r - mean_rise) / mean_rise <= tol_frac for r in rises):
        return None
    top_gap = bb_max_z - sorted_zs[-1]
    n = len(rises) + (1 if abs(top_gap - mean_rise) / mean_rise <= tol_frac else 0)
    return n, mean_rise


def _mentioned_diams(annotations):
    """Diameters already called out by an annotation — from ø-labels and from
    structured ``covers_diameters`` metadata (e.g. ``HoleCallout``). Mirrors the
    coverage :func:`lint_feature_coverage` checks, so a diameter in this set will
    not lint as ``feature_not_dimensioned``."""
    diams: set = set()
    for ann in annotations:
        if isinstance(ann, TitleBlock):
            continue
        for m in _DIAM_RE.finditer(getattr(ann, "label", None) or ""):
            diams.add(float(m.group(1)))
        for v in getattr(ann, "covers_diameters", ()):
            diams.add(float(v))
    return diams


def _distinct_bosses(bosses, mentioned):
    """One representative boss per distinct external diameter (tallest wins),
    dropping any diameter another annotation already covers (#77)."""
    by_diam: dict = {}
    for b in bosses:
        key = next((k for k in by_diam if abs(k - b.diameter) <= 0.15), b.diameter)
        if key not in by_diam or b.height > by_diam[key].height:
            by_diam[key] = b
    return [b for d, b in by_diam.items() if not any(abs(d - m) <= 0.15 for m in mentioned)]


def _annotate_turned_diameters(dwg, a: Analysis):
    """Leader ø-callouts for external turned step diameters (#77, #131).

    draftwright dimensions holes and, for a Z-rotational part, the OD; the
    external stepped diameters of a turned part lying along X — a peg body, a
    stepped shaft drawn on its side — are otherwise undimensioned and surface
    only as ``feature_not_dimensioned``. This pass places one ø leader per
    distinct external diameter, the thread/worm patches collapsed by
    :func:`find_bosses` into a single boss, below the front-view profile.
    Diameters another annotation already covers are skipped.

    X-axis turning (a shaft drawn on its side) gets a row of callouts below the
    front view; Z-axis turning (a vertical stepped shaft) gets a column to its
    left (#131). Y-axis turning, gear/thread module notes, and axial-length dims
    are out of scope.
    """
    draft = dwg.draft
    try:
        bosses = find_bosses(a.part)
    except Exception as exc:  # noqa: BLE001 — recognition may fail on odd geometry
        _log.info("turned-diameter annotation skipped (%s)", exc)
        return

    mentioned = _mentioned_diams(dwg.items)
    # Z-axis turning (a vertical stepped shaft) gets a column of ø callouts to the
    # left of the front view (#131); X-axis turning keeps the row below (#77).
    _turned_diameters_beside(
        dwg, a, _distinct_bosses([b for b in bosses if _axis_letter(b) == "z"], mentioned)
    )
    todo = _distinct_bosses([b for b in bosses if _axis_letter(b) == "x"], mentioned)
    if not todo:
        return

    # Each callout's label sits in a row below the front view, pulled toward the
    # page-x of its feature; a shared 1D Cassowary solve spreads any that would
    # overlap. This is ADR 0003's layer-2 primitive (_solve_strip_ys reused on
    # the x axis) standing in for the manual pitch stacking the other leaders
    # still use — the first pass to place on the constraint solver (#77).
    fx0, fy0, fx1, _ = dwg.view_bounds("front")  # page bbox of the profile (#28)
    # Drop the row clear of anything already placed below the profile (hole
    # callouts, envelope dims). This is a coarse single-pass guard against the
    # cross-pass overlap a global solve would handle exactly (ADR 0003 / #80):
    # it deconflicts the whole row vertically, not per-label.
    obstacle_bottom = fy0
    for o in dwg.items:
        try:
            ob = o.bounding_box()
        except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
            continue
        if ob.min.Y < fy0 and ob.max.X > fx0 and ob.min.X < fx1:
            obstacle_bottom = min(obstacle_bottom, ob.min.Y)
    label_y = obstacle_bottom - (draft.font_size + 4 * draft.pad_around_text)
    # No room below the profile within the page — skip rather than run the row
    # off the sheet. The diameters then surface as feature_not_dimensioned; the
    # escalation ladder (#82) will tabulate instead of dropping.
    if label_y < a.margin + draft.font_size:
        _log.info("turned-diameter callouts skipped (no room below the front view)")
        return

    specs = []  # (tip_page, label) ordered by feature x
    for b in todo:
        mid_x = b.location[0] - b.axis[0] * (b.height / 2)
        tip = dwg.at("front", mid_x, b.location[1], b.location[2] - b.diameter / 2)
        specs.append((tip, f"ø{_fmt(b.diameter)}"))
    specs.sort(key=lambda s: s[0][0])

    half_w = max(len(label) for _, label in specs) * draft.font_size * 0.62 / 2
    min_gap = 2 * half_w + 2 * draft.pad_around_text
    naturals = [tip[0] for tip, _ in specs]
    x_lo, x_hi = fx0 + half_w, fx1 - half_w
    label_xs = _solve_strip_ys(naturals, min_gap, x_lo, x_hi) or _greedy_strip_ys(
        naturals, min_gap, x_lo, x_hi
    )
    if label_xs is None:
        # The labels do not fit the row even greedily; skip rather than crash on
        # a None unpack. They surface as feature_not_dimensioned (#82 tabulates).
        _log.info("turned-diameter callouts skipped (%d will not fit the row)", len(specs))
        return
    for i, ((tip, label), lx) in enumerate(zip(specs, label_xs, strict=True)):
        dwg.add(
            Leader(
                tip=(tip[0], tip[1], 0),
                elbow=(lx, label_y, 0),
                label=label,
                draft=draft,
            ),
            f"ldr_d{i}",
            view="front",
        )


def _turned_diameters_beside(dwg, a: Analysis, todo):
    """ø-callout column to the LEFT of the front view for Z-axis turned (vertical
    stepped) diameters — the page-Y mirror of the #77 row-below (#131)."""
    if not todo:
        return
    draft = dwg.draft
    fx0, fy0, fx1, fy1 = dwg.view_bounds("front")
    # Drop the column clear of anything already left of the profile within the
    # front view's y-range (the single-pass guard #77 uses, mirrored onto x).
    left_limit = fx0
    for o in dwg.items:
        try:
            ob = o.bounding_box()
        except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
            continue
        if ob.min.X < fx0 and ob.max.Y > fy0 and ob.min.Y < fy1:
            left_limit = min(left_limit, ob.min.X)
    label_w = max(len(f"ø{_fmt(b.diameter)}") for b in todo) * draft.font_size * 0.62
    elbow_x = left_limit - (draft.font_size + 2 * draft.pad_around_text)
    # No room left of the profile within the page — skip rather than run off the
    # sheet; the diameters then surface as feature_not_dimensioned.
    if elbow_x - label_w < a.margin:
        _log.info("turned-diameter callouts skipped (no room left of the front view)")
        return
    specs = []  # (tip_page, label) — tip on the step's left silhouette at mid-height
    for b in todo:
        mid_z = b.location[2] - b.axis[2] * (b.height / 2)
        tip = dwg.at("front", b.location[0] - b.diameter / 2, b.location[1], mid_z)
        specs.append((tip, f"ø{_fmt(b.diameter)}"))
    specs.sort(key=lambda s: s[0][1])
    half_h = draft.font_size / 2 + draft.pad_around_text
    min_gap = 2 * half_h
    naturals = [tip[1] for tip, _ in specs]
    y_lo, y_hi = fy0 + half_h, fy1 - half_h
    label_ys = _solve_strip_ys(naturals, min_gap, y_lo, y_hi) or _greedy_strip_ys(
        naturals, min_gap, y_lo, y_hi
    )
    if label_ys is None:
        _log.info("turned-diameter callouts skipped (%d will not fit the column)", len(specs))
        return
    for i, ((tip, label), ly) in enumerate(zip(specs, label_ys, strict=True)):
        dwg.add(
            Leader(
                tip=(tip[0], tip[1], 0),
                elbow=(elbow_x, ly, 0),
                label=label,
                draft=draft,
            ),
            f"ldr_dz{i}",
            view="front",
        )


def _auto_annotate(dwg, a: Analysis, *, detail_view: bool = False):
    """Add the standard automatic dimensions, centrelines, and title block."""
    draft = dwg.draft
    # Idempotent: clear build-time lint state so a second annotation pass does
    # not accumulate duplicate drop records.
    dwg._build_issues = []
    dwg._dropped_callout_diams = []

    FX = a.proj.front_x
    FZ = a.proj.front_z
    SX = a.proj.side_x
    SZ = a.proj.side_z
    PX = a.proj.plan_x
    PY = a.proj.plan_y

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
        assert od is not None  # is_rotational ⇒ od_diam is set (see _is_rotational)
        dwg.add(
            _dim(
                (FX(a.cx - od / 2), FZ(a.bb.max.Z) + 2, 0),
                (FX(a.cx + od / 2), FZ(a.bb.max.Z) + 2, 0),
                "above",
                8,
                draft,
                label=f"ø{_fmt(od)}",
            ),
            "dim_od",
            view="front",
        )
        # Centreline through the rotation axis — front and side views
        dwg.add(
            Centerline(
                (FX(a.cx), FZ(a.bb.min.Z) - 5, 0),
                (FX(a.cx), FZ(a.bb.max.Z) + 5, 0),
            ),
            "centerline_front",
            view="front",
        )
        dwg.add(
            Centerline(
                (SX(a.cy), SZ(a.bb.min.Z) - 5, 0),
                (SX(a.cy), SZ(a.bb.max.Z) + 5, 0),
            ),
            "centerline_side",
            view="side",
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
                    view="front",
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

    # Centre marks for every hole (all part classes)
    for i, h in enumerate(a.holes):
        view, to_page = view_of_axis[_axis_letter(h)]
        size = max(2.5, h.diameter * a.SCALE + 2.0)
        dwg.add(CenterMark(to_page(h), size, draft), f"cm_{view}{i}", view=view)

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
        feature_holes = [h for h in a.holes if not _is_concentric_hole(h, a)]
        present = set(map(id, feature_holes))
        feature_patterns = [p for p in a.patterns if all(id(h) in present for h in p.holes)]
    if feature_holes:
        _annotate_holes(dwg, a, view_of_axis, feature_patterns, holes_in=feature_holes)
        _add_location_dims(dwg, a, feature_patterns, holes_in=feature_holes)

    if a.cross_diams and a.is_rotational and not feature_holes:
        _log.info(
            "Cross-hole ø%s detected but not annotated (requires section view)",
            _fmt(a.cross_diams[0]),
        )

    # Step heights.  If the steps form a uniform staircase (#45) place a single
    # representative dim labelled "N× rise" instead of one dim per step.
    # Otherwise fall back to the per-step ladder (legibility-gated, #41).
    _step_rep = _detect_step_repeat(a.step_zs, a.bb.min.Z, a.bb.max.Z)
    if _step_rep is not None:
        n_rep, rise_mm = _step_rep
        first_step_z = sorted(a.step_zs)[0]
        _px = a.fv_zones.right.allocate(_SLOT_DIM_STEP)
        if _px is not None:
            dwg.add(
                _dim(
                    (_right_ladder, FZ(a.bb.min.Z), 0),
                    (_right_ladder, FZ(first_step_z), 0),
                    "right",
                    _px - _right_ladder,
                    draft,
                    label=f"{n_rep}× {_fmt(rise_mm)}",
                ),
                "dim_step_typ",
                view="front",
            )
            _right_ladder = _px
        else:
            _log.warning("dim_step_typ skipped: fv_zones.right strip full")
            dwg._record_build_issue(
                "error",
                "placement_unsatisfiable",
                "representative step-height dimension dropped (front-view right strip full)",
            )
    else:
        # Per-step ladder: only steps tall enough AND far enough apart on the
        # page (#41). Extension lines witness from the previous dim's line so
        # they are adjacent rather than coincident.
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
                _dim(
                    (_right_ladder, FZ(a.bb.min.Z), 0),
                    (_right_ladder, FZ(z), 0),
                    "right",
                    _px - _right_ladder,
                    draft,
                    label=_fmt(z - a.bb.min.Z),
                ),
                f"dim_step_{col}",
                view="front",
            )
            _right_ladder = _px

    # Overall height — placed last so it sits OUTERMOST, beyond the step dims.
    _px = a.fv_zones.right.allocate(_SLOT_DIM_HEIGHT)
    if _px is not None:
        dwg.add(
            _dim(
                (_right_ladder, FZ(a.bb.min.Z), 0),
                (_right_ladder, FZ(a.bb.max.Z), 0),
                "right",
                _px - _right_ladder,
                draft,
                label=_fmt(a.z_size),
            ),
            "dim_height",
            view="front",
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
                _dim(
                    (PX(a.bb.min.X), _below_witness, 0),
                    (PX(a.bb.max.X), _below_witness, 0),
                    "below",
                    _below_witness - _py,
                    draft,
                    label=_fmt(a.x_size),
                ),
                "dim_width",
                view="plan",
            )
        else:
            _log.warning("dim_width skipped: pv_zones.below strip full")

    # Depth (Y envelope) — same guard as dim_width; routed through sv_zones.below
    if abs(a.x_size - a.y_size) > max(a.x_size, a.y_size) * 0.05:
        _below_witness_d = SZ(a.bb.min.Z) - 2
        _pd = a.sv_zones.below.allocate(_SLOT_DIM_DEPTH)
        if _pd is not None:
            dwg.add(
                _dim(
                    (SX(a.bb.min.Y), _below_witness_d, 0),
                    (SX(a.bb.max.Y), _below_witness_d, 0),
                    "below",
                    _below_witness_d - _pd,
                    draft,
                    label=_fmt(a.y_size),
                ),
                "dim_depth",
                view="side",
            )
        else:
            _log.warning("dim_depth skipped: sv_zones.below strip full")

    # The section view goes last: its room check clears every annotation
    # already placed right of the side view (callout labels, height/step
    # dim ladders).  Fires on feature presence, not class (#10); concentric
    # bores on a turned part are excluded (the ldr_z leaders cover them).
    if feature_holes:
        _add_section_view(dwg, a, holes=feature_holes)

    # Detail view: only when explicitly requested via build_drawing(detail_view=True).
    if detail_view:
        _add_detail_view(dwg, a)

    # External turned diameters (X-axis turning) the passes above do not cover.
    _annotate_turned_diameters(dwg, a)

    # Side-drilled (X/Y-axis) hole locations — last, so the envelope and
    # turned-diameter dims claim their strip space first and are never evicted (#133).
    if feature_holes:
        _locate_off_axis_holes(dwg, a, holes_in=feature_holes)

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

    if a.pmi_mode == "annotate":
        _annotate_pmi(dwg, a, draft)

    _add_title_block(dwg, a)

    # Escalate to a hole table when the plan view is too dense to dimension
    # every hole — runs last so the table avoids every placed annotation
    # including the title block (#93).
    _maybe_tabulate_holes(dwg, a)


def _maybe_tabulate_holes(dwg, a: Analysis):
    """Escalate to a per-instance hole table + balloons when the plan view is too
    dense to dimension every hole individually (#93).

    When callouts or location references had to be dropped, the individual
    plan-view callouts and X/Y location dims are removed and replaced by a
    complete **hole chart** — one row per hole (``TAG | ⌀ | X | Y``, X/Y from the
    min-corner datum) and a uniquely-tagged balloon at each hole. The table
    carries ``covers_diameters`` so the coverage lint still counts the holes.
    Sparse parts drop nothing, so this is a no-op for them — unchanged.

    If the table itself will not fit, nothing is removed and the drop lint is
    kept — the sheet is never left with neither.
    """
    if not any(i.code in ("callout_dropped", "location_ref_dropped") for i in dwg._build_issues):
        return

    # Tabulate only the genuinely UNpatterned plan-view holes: holes in a
    # recognised pattern are documented by their grouped ``n× ⌀`` callout +
    # pattern dimension, so they must not become table rows or per-hole balloons
    # (#92).  Excluding them is also what keeps a densely-but-regularly drilled
    # part (e.g. NIST CTC-02) off the 61-row escalation (#111).
    holes = [h for h in a.holes if _axis_letter(h) == "z" and h not in dwg._patterned_holes]
    # A chart is warranted only for a *genuinely* dense plan view — a part that
    # merely dropped one too-close location ref keeps its individual dims (the
    # legibility gate already handled it). #93.
    if len(holes) < _TABULATE_MIN_HOLES:
        return
    dx, dy = a.bb.min.X, a.bb.min.Y
    tags = _tag_sequence(len(holes))
    header = ("TAG", "⌀", "X", "Y")
    data = [
        (tag, f"ø{_fmt(h.diameter)}", _fmt(h.location[0] - dx), _fmt(h.location[1] - dy))
        for tag, h in zip(tags, holes, strict=True)
    ]
    # Remove the callouts and location dims the table replaces FIRST: it frees
    # their space for the table and shrinks the obstacle set fit_box scans (the
    # dense parts have dozens), which is the dominant cost on heavy sheets (#93).
    replaced = {
        n: dwg._named[n]
        for n in list(dwg._named)
        if n.startswith(("hc_plan", "dim_locx", "dim_locy")) and n not in dwg._pattern_callouts
    }
    replaced_view = {n: dwg._anno_view.get(n) for n in replaced}
    for n in replaced:
        dwg.remove(n)

    # Widen the chart into more column-blocks until it fits the page.
    table = None
    for ncols in (1, 2, 3, 4):
        table = dwg.add_table(
            _wrap_rows(header, data, ncols), name="hole_table_plan", block_cols=len(header)
        )
        if table is not None:
            break
    dwg._build_issues = [i for i in dwg._build_issues if i.code != "table_dropped"]
    if table is None:
        # Even wrapped it will not fit — restore the callouts/dims and keep the
        # drop lint, so the sheet is never left with neither.
        for n, obj in replaced.items():
            dwg.add(obj, n, view=replaced_view.get(n))
        dwg._record_build_issue("warning", "table_dropped", "hole table did not fit the sheet")
        return
    # One entry per hole (with repeats) so the coverage *count* check sees that
    # the table documents every instance, not just each distinct diameter.
    table.covers_diameters = tuple(h.diameter for h in holes)
    dwg._add_balloons("plan", [(tag, 0, h) for tag, h in zip(tags, holes, strict=True)])
    dwg._build_issues = [
        i for i in dwg._build_issues if i.code not in ("callout_dropped", "location_ref_dropped")
    ]


def _annotate_pmi(dwg, a: Analysis, draft) -> None:
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
    pmi = a.pmi
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

    FX = a.proj.front_x
    FZ = a.proj.front_z
    SX = a.proj.side_x
    SZ = a.proj.side_z
    PX = a.proj.plan_x
    PY = a.proj.plan_y

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

    def _try_above(p1, p2, strip, label, name, view):
        """Place a horizontal dimension line ABOVE the witness points."""
        if strip is None:
            return False
        witness_y = max(p1[1], p2[1]) + 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) <= witness_y:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            _dim(
                (p1[0], witness_y, 0),
                (p2[0], witness_y, 0),
                "above",
                slot - witness_y,
                draft,
                label=label,
            ),
            name,
            view=view,
        )
        return True

    def _try_below(p1, p2, strip, label, name, view):
        """Place a horizontal dimension line BELOW the witness points."""
        if strip is None:
            return False
        witness_y = min(p1[1], p2[1]) - 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) >= witness_y:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            _dim(
                (p1[0], witness_y, 0),
                (p2[0], witness_y, 0),
                "below",
                witness_y - slot,
                draft,
                label=label,
            ),
            name,
            view=view,
        )
        return True

    def _try_right(p1, p2, strip, label, name, view):
        """Place a vertical dimension line to the RIGHT of the witness points."""
        if strip is None:
            return False
        witness_x = max(p1[0], p2[0]) + 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) <= witness_x:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            _dim(
                (witness_x, p1[1], 0),
                (witness_x, p2[1], 0),
                "right",
                slot - witness_x,
                draft,
                label=label,
            ),
            name,
            view=view,
        )
        return True

    def _try_left(p1, p2, strip, label, name, view):
        """Place a vertical dimension line to the LEFT of the witness points."""
        if strip is None:
            return False
        witness_x = min(p1[0], p2[0]) - 2
        if strip.peek(_SLOT) is None or strip.peek(_SLOT) >= witness_x:
            return False
        slot = strip.allocate(_SLOT)
        dwg.add(
            _dim(
                (witness_x, p1[1], 0),
                (witness_x, p2[1], 0),
                "left",
                witness_x - slot,
                draft,
                label=label,
            ),
            name,
            view=view,
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
                    placed = _try_above(
                        p1, p2, a.pv_zones.above, label, name_d, "plan"
                    ) or _try_below(p1, p2, a.pv_zones.below, label, name_d, "plan")
                else:
                    tip = (PX(cx_f), PY(cy_f) + half_pg, 0)
                    slot = a.pv_zones.above.allocate(_SLOT)
                    if slot is not None:
                        dwg.add(
                            Leader(tip, (PX(cx_f), slot, 0), label, draft), name_d, view="plan"
                        )
                        placed = True
                    else:
                        slot = a.pv_zones.below.allocate(_SLOT)
                        if slot is not None:
                            tip = (PX(cx_f), PY(cy_f) - half_pg, 0)
                            dwg.add(
                                Leader(tip, (PX(cx_f), slot, 0), label, draft), name_d, view="plan"
                            )
                            placed = True

            elif bore_axis == "X":
                # X-axis bore: circle visible in side view.
                if half_pg >= 4.0:
                    p1 = (SX(cy_f - half), SZ(cz_f), 0)
                    p2 = (SX(cy_f + half), SZ(cz_f), 0)
                    placed = _try_above(
                        p1, p2, a.sv_zones.above, label, name_d, "side"
                    ) or _try_below(p1, p2, a.sv_zones.below, label, name_d, "side")
                else:
                    tip = (SX(cy_f), SZ(cz_f) + half_pg, 0)
                    slot = a.sv_zones.above.allocate(_SLOT)
                    if slot is not None:
                        dwg.add(
                            Leader(tip, (SX(cy_f), slot, 0), label, draft), name_d, view="side"
                        )
                        placed = True
                    else:
                        slot = a.sv_zones.below.allocate(_SLOT)
                        if slot is not None:
                            tip = (SX(cy_f), SZ(cz_f) - half_pg, 0)
                            dwg.add(
                                Leader(tip, (SX(cy_f), slot, 0), label, draft), name_d, view="side"
                            )
                            placed = True

            elif bore_axis == "Y":
                # Y-axis bore: circle visible in front view as a circle.
                if half_pg >= 4.0:
                    p1 = (FX(cx_f - half), FZ(cz_f), 0)
                    p2 = (FX(cx_f + half), FZ(cz_f), 0)
                    placed = _try_above(
                        p1, p2, a.fv_zones.above, label, name_d, "front"
                    ) or _try_below(p1, p2, a.fv_zones.below, label, name_d, "front")
                else:
                    # Narrow bore: leader from bore bottom into the below strip.
                    tip = (FX(cx_f), FZ(cz_f) - half_pg, 0)
                    slot = a.fv_zones.below.allocate(_SLOT)
                    if slot is not None:
                        elbow = (FX(cx_f), slot, 0)
                        dwg.add(Leader(tip, elbow, label, draft), name_d, view="front")
                        placed = True
                    else:
                        # Fall back: leader upward into the above strip.
                        slot = a.fv_zones.above.allocate(_SLOT)
                        if slot is not None:
                            tip = (FX(cx_f), FZ(cz_f) + half_pg, 0)
                            elbow = (FX(cx_f), slot, 0)
                            dwg.add(Leader(tip, elbow, label, draft), name_d, view="front")
                            placed = True

        elif ax == "X":
            wp = _witness_from_bbox(rec, "front")
            if wp is None:
                _log.debug("PMI dim[%d] X: degenerate bbox", idx)
                continue
            p1, p2, avg_pz = wp
            if avg_pz >= a.FV_Y:
                placed = _try_above(p1, p2, a.fv_zones.above, label, name_x, "front")
            if not placed:
                placed = _try_below(p1, p2, a.fv_zones.below, label, name_x, "front")

        elif ax == "Z":
            wp = _witness_from_bbox(rec, "front")
            if wp is None:
                _log.debug("PMI dim[%d] Z: degenerate bbox", idx)
                continue
            p1, p2, avg_px = wp
            if avg_px >= a.FV_X:
                placed = _try_right(p1, p2, a.fv_zones.right, label, name_z, "front")
            if not placed:
                placed = _try_left(p1, p2, a.fv_zones.left, label, name_z, "front")

        elif ax == "Y":
            # Try side view (Y maps to SX horizontal).
            wp = _witness_from_bbox(rec, "side")
            if wp is not None:
                p1, p2, avg_sz = wp
                if avg_sz >= a.SV_Y:
                    placed = _try_above(p1, p2, a.sv_zones.above, label, name_y, "side")
                if not placed:
                    placed = _try_below(p1, p2, a.sv_zones.below, label, name_y, "side")
            # Fall back: plan view (Y maps to PY vertical).
            if not placed:
                wp = _witness_from_bbox(rec, "plan")
                if wp is not None:
                    p1, p2, _ = wp
                    placed = _try_below(p1, p2, a.pv_zones.below, label, name_y, "plan")

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


def _add_location_dims(dwg, a: Analysis, patterns, holes_in=None):
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
    z_holes = [h for h in all_holes if _axis_letter(h) == "z"]
    if len(z_holes) < len(all_holes):
        _log.info("Cross-axis holes present; their locations are not auto-dimensioned")
    patterned = {h for p in patterns for h in p.holes}
    refs = []  # (world_x, world_y, sort_diameter)
    for p in patterns:
        if _axis_letter(p.holes[0]) != "z":
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

    PX = a.proj.plan_x
    PY = a.proj.plan_y

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
            _dim(
                (PX(datum_x), PY(ry), 0),
                (PX(rx), PY(ry), 0),
                "above",
                _py - PY(ry),
                draft,
                label=_fmt(rx - datum_x),
            ),
            f"dim_locx{i}",
            view="plan",
        )

    # Y locations: the side view maps world Y horizontally, and the strip
    # above it is open (the plan view's left margin fits barely one tier) —
    # dims go above the side view, witness lines rising from its top edge at
    # each hole's axis position
    SX = a.proj.side_x
    SZ = a.proj.side_z

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
            _dim(
                (SX(datum_y), SZ(a.bb.max.Z), 0),
                (SX(ry), SZ(a.bb.max.Z), 0),
                "above",
                _py - side_top,
                draft,
                label=_fmt(ry - datum_y),
            ),
            f"dim_locy{i}",
            view="side",
        )


def _locate_off_axis_holes(dwg, a: Analysis, holes_in=None):
    """Location dimensions for side-drilled holes (#133).

    An X-axis hole is a circle in the SIDE view (locate its Y below the view and
    its Z to the right — the side view has no left strip); a Y-axis hole is a
    circle in the FRONT view (locate its X below and its Z to the right). Each
    offset is allocated from the view's strip so dims stack without overlap, and
    this pass runs AFTER the envelope and turned-diameter passes so it can never
    evict an overall dimension. A tier with no room is dropped and recorded as
    ``location_ref_dropped`` — never force-stacked. Holes already covered by a
    pattern callout are skipped, as in the plan path.
    """
    draft = dwg.draft
    all_holes = a.holes if holes_in is None else holes_in
    patterned = {h for p in a.patterns for h in p.holes}
    off = [h for h in all_holes if _axis_letter(h) in ("x", "y") and h not in patterned]
    if not off:
        return
    SX, SZ = a.proj.side_x, a.proj.side_z
    FX, FZ = a.proj.front_x, a.proj.front_z
    dx, dy, dz = a.bb.min.X, a.bb.min.Y, a.bb.min.Z
    tier = draft.font_size + 2 * draft.pad_around_text

    def _drop(axis, view):
        dwg._record_build_issue(
            "warning",
            "location_ref_dropped",
            f"{axis} location dim for a {view}-view hole not placed (no room beside the view)",
        )

    def _below(strip, view, p_lo, p_hi, witness, label, axis):
        coord = strip.allocate(tier) if strip is not None else None
        if coord is None:
            _drop(axis, view)
            return
        dwg.add(
            _dim(p_lo, p_hi, "below", witness - coord, draft, label=_fmt(label)),
            f"dim_loc_{view}_{axis}{round(label * 100)}",
            view=view,
        )

    def _right(strip, view, p_lo, p_hi, edge, label):
        coord = strip.allocate(tier) if strip is not None else None
        if coord is None:
            _drop("Z", view)
            return
        dwg.add(
            _dim(p_lo, p_hi, "right", coord - edge, draft, label=_fmt(label)),
            f"dim_loc_{view}_z{round(label * 100)}",
            view=view,
        )

    # X-axis holes -> side view: Y offset below, Z offset right.
    yw, zr = SZ(dz) - 2, SX(a.bb.max.Y)
    seen_y, seen_zs = set(), set()
    for h in (h for h in off if _axis_letter(h) == "x"):
        yo = round(abs(h.location[1] - dy), 2)
        if yo * a.SCALE >= 1.0 and yo not in seen_y:
            seen_y.add(yo)
            _below(
                a.sv_zones.below, "side", (SX(dy), yw, 0), (SX(h.location[1]), yw, 0), yw, yo, "y"
            )
        zo = round(abs(h.location[2] - dz), 2)
        if zo * a.SCALE >= 1.0 and zo not in seen_zs:
            seen_zs.add(zo)
            _right(a.sv_zones.right, "side", (zr, SZ(dz), 0), (zr, SZ(h.location[2]), 0), zr, zo)

    # Y-axis holes -> front view: X offset below, Z offset right.
    xw, zrf = FZ(dz) - 2, FX(a.bb.max.X)
    seen_x, seen_zf = set(), set()
    for h in (h for h in off if _axis_letter(h) == "y"):
        xo = round(abs(h.location[0] - dx), 2)
        if xo * a.SCALE >= 1.0 and xo not in seen_x:
            seen_x.add(xo)
            _below(
                a.fv_zones.below, "front", (FX(dx), xw, 0), (FX(h.location[0]), xw, 0), xw, xo, "x"
            )
        zo = round(abs(h.location[2] - dz), 2)
        if zo * a.SCALE >= 1.0 and zo not in seen_zf:
            seen_zf.add(zo)
            _right(
                a.fv_zones.right, "front", (zrf, FZ(dz), 0), (zrf, FZ(h.location[2]), 0), zrf, zo
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


def _add_section_view(dwg, a: Analysis, holes=None):
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
        if _axis_letter(h) == "z" and (h.cbore or h.spotface or h.bottom != "through")
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
    PX = a.proj.plan_x
    PY = a.proj.plan_y

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

    # ISO 128-50: 45° hatching on the cut face, in page coordinates. The section
    # is drawn in its own frame: X is offset to the section's page slot (pos_x),
    # while the height axis matches the front view — so SZ is exactly front_z.
    def SX(wx):
        return pos_x + (wx - a.cx) * a.SCALE

    SZ = a.proj.front_z

    hatch_spacing = dwg.draft.font_size * 1.5
    cut_faces = [f for f in keep_behind.faces() if f.normal_at().Y < -0.9]
    hatch_edges = []
    for cf in cut_faces:
        hatch_edges.extend(_section_hatch_edges(cf, SX, SZ, hatch_spacing))
    if hatch_edges:
        hatch = Compound(children=hatch_edges)
        hatch.is_section_hatch = True  # exempt from view_annotation_overlap lint
        dwg.add(hatch, "section_hatch")


def _add_detail_view(dwg, a: Analysis):
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
    FX = a.proj.front_x
    FZ = a.proj.front_z

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
            det_dim = _dim(
                (ladder, p_lo[1], 0),
                (ladder, p_hi[1], 0),
                "right",
                step_pad,
                dwg.draft,
                label=_fmt(z - a.bb.min.Z),
            )
            # The detail view is drawn at detail_scale, not sheet scale; tag the
            # dim so lint() checks label-vs-measured against the right scale (#42).
            det_dim._dw_scale = detail_scale
            dwg.add(det_dim, f"dim_detail_step_{i}")
            ladder += step_pad
        except Exception as exc:  # noqa: BLE001 — placement may fail on degenerate geometry
            _log.info("dim_detail_step_%d skipped (%s)", i, exc)

    # Overall band height — outermost.
    try:
        p_lo = dwg.at("detail_a", a.bb.max.X, dcy, a.bb.min.Z)
        p_hi = dwg.at("detail_a", a.bb.max.X, dcy, a.bb.max.Z)
        det_dim = _dim(
            (ladder, p_lo[1], 0),
            (ladder, p_hi[1], 0),
            "right",
            step_pad,
            dwg.draft,
            label=_fmt(a.z_size),
        )
        det_dim._dw_scale = detail_scale  # detail view scale, for label-vs-measured lint (#42)
        dwg.add(det_dim, "dim_detail_height")
    except Exception as exc:  # noqa: BLE001 — placement may fail on degenerate geometry
        _log.info("dim_detail_height skipped (%s)", exc)


def _add_furniture(dwg, a: Analysis, view, j, pattern, to_page):
    """Pattern sheet furniture, added once its callout is placed (#92)."""
    if pattern is not None:
        # Remember the bore-callout name AND the holes it documents, so a later
        # hole-table escalation leaves the grouped pattern callout standing and
        # tabulates only the holes no *placed* pattern callout covers (#92).
        # Recording here (callout already placed) — not from a.patterns — means a
        # pattern dropped for lack of room, or filtered off a rotational part,
        # correctly falls back to the table instead of going undocumented.
        dwg._pattern_callouts.add(f"hc_{view}{j}")
        dwg._patterned_holes.update(pattern.holes)
    if isinstance(pattern, BoltCircle):
        cx = sum(to_page(h)[0] for h in pattern.holes) / len(pattern.holes)
        cy = sum(to_page(h)[1] for h in pattern.holes) / len(pattern.holes)
        dwg.add(CenterlineCircle((cx, cy), pattern.diameter * a.SCALE), f"bc_{view}{j}", view=view)
    elif isinstance(pattern, LinearArray):
        _place_pitch_dim(
            dwg,
            a,
            view,
            pattern.holes[0],
            pattern.holes[-1],
            len(pattern.holes),
            pattern.pitch,
            to_page,
            f"dim_pitch_{view}{j}",
        )
    elif isinstance(pattern, RectGrid):
        _add_grid_pitch_dims(dwg, a, view, j, pattern, to_page)


def _add_grid_pitch_dims(dwg, a: Analysis, view, j, grid, to_page):
    """Both pitch dimensions of a rectangular grid — one along each lattice axis,
    each labelled ``(n-1)× pitch`` (#92).  The two axes are recovered as the two
    shortest near-orthogonal inter-hole page vectors (the recogniser's own
    basis); this is used only to pick the dimension endpoints and the per-axis
    count, not to re-recognise the grid (recognition stays upstream)."""
    pts = [to_page(h) for h in grid.holes]
    diffs = []
    for ia in range(len(pts)):
        for ib in range(len(pts)):
            if ia == ib:
                continue
            dx, dy = pts[ib][0] - pts[ia][0], pts[ib][1] - pts[ia][1]
            length = math.hypot(dx, dy)
            if length > 1e-6:
                diffs.append((length, dx, dy))
    if not diffs:
        return
    diffs.sort()
    l1, ax, ay = diffs[0]
    u1 = (ax / l1, ay / l1)
    basis2 = next(
        (
            (length, dx, dy)
            for length, dx, dy in diffs
            if abs((dx * u1[0] + dy * u1[1]) / length) < 0.2
        ),
        None,
    )
    if basis2 is None:
        return
    l2, bx, by = basis2
    u2 = (bx / l2, by / l2)
    nominals = (grid.row_pitch, grid.col_pitch)

    def _axis_dim(u, pitch_page, sub):
        perp = (-u[1], u[0])

        def along(idx):
            return pts[idx][0] * u[0] + pts[idx][1] * u[1]

        def across(idx):
            return pts[idx][0] * perp[0] + pts[idx][1] * perp[1]

        lo = min(range(len(pts)), key=along)
        # Keep the dimension on ONE lattice line: of the holes sharing lo's
        # perpendicular coordinate, take the far one along u. Picking the global
        # max-projection hole instead lands on the opposite diagonal corner and
        # draws the pitch dim diagonally across the grid (#92).
        # Tolerance must be below the PERPENDICULAR lattice-line spacing — which
        # is the *other* axis' pitch, so use the smaller of the two pitches.
        # (pitch_page * 0.25 fails on a high-aspect grid: for the long axis the
        # perpendicular lines are only the short pitch apart, and a quarter of
        # the long pitch can exceed that, merging two lines → diagonal again.)
        lo_across = across(lo)
        line_tol = min(l1, l2) * 0.25
        line = [idx for idx in range(len(pts)) if abs(across(idx) - lo_across) < line_tol]
        hi = max(line, key=along)
        span = along(hi) - along(lo)
        n = round(span / pitch_page) + 1
        # Label with the recogniser's nominal pitch nearest this axis' page step.
        pitch = min(nominals, key=lambda v: abs(v - pitch_page / a.SCALE))
        _place_pitch_dim(
            dwg,
            a,
            view,
            grid.holes[lo],
            grid.holes[hi],
            n,
            pitch,
            to_page,
            f"dim_pitch_{view}{j}_{sub}",
        )

    _axis_dim(u1, l1, 0)
    _axis_dim(u2, l2, 1)


def _place_pitch_dim(dwg, a: Analysis, view, h1, h2, n, pitch, to_page, name):
    """Pitch dimension between two hole centres ``h1``→``h2``, labelled
    ``(n-1)× pitch``, placed just outside the view on the side of the row's
    outward perpendicular (#92)."""
    p1 = to_page(h1)
    p2 = to_page(h2)
    ux, uy = p2[0] - p1[0], p2[1] - p1[1]
    norm = math.hypot(ux, uy)
    if norm < 1e-9:
        return
    ux, uy = ux / norm, uy / norm
    mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    # view extents in page coordinates, to push the dim line outside
    if view == "plan":
        corners = [
            (a.proj.plan_x(x), a.proj.plan_y(y))
            for x in (a.bb.min.X, a.bb.max.X)
            for y in (a.bb.min.Y, a.bb.max.Y)
        ]
    elif view == "front":
        corners = [
            (a.proj.front_x(x), a.proj.front_z(z))
            for x in (a.bb.min.X, a.bb.max.X)
            for z in (a.bb.min.Z, a.bb.max.Z)
        ]
    else:
        corners = [
            (a.proj.side_x(y), a.proj.side_z(z))
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
    prior = sum(1 for nm in dwg._named if nm.startswith(f"dim_pitch_{view}"))
    offset = reach + 8 + 10 * prior
    # never force-place: skip (and log) when the dim line would leave the page
    ox = mid[0] + side[0] * (offset + 6)
    oy = mid[1] + side[1] * (offset + 6)
    if not (a.margin <= ox <= a.PAGE_W - a.margin and a.margin <= oy <= a.PAGE_H - a.margin):
        _log.info(
            "Pitch dimension for the %s× %s array skipped (no room)",
            n,
            _fmt(pitch),
        )
        return
    dwg.add(
        _dim(
            (p1[0], p1[1], 0),
            (p2[0], p2[1], 0),
            side,
            offset,
            dwg.draft,
            label=f"{n - 1}× {_fmt(pitch)}",
        ),
        name,
        view=view,
    )


def _solve_strip_via_layout(naturals, min_gap, lo, hi, key_prefix):
    """Place a pre-sorted, uniform-gap 1D stack through the shared LayoutSolver
    (ADR 0003 phase 2, #80), returning positions in input order, or ``None`` if
    the stack does not fit.

    *naturals* must be ascending (the caller sorts the queue), so the solver's
    ``(natural, key)`` ordering — with the zero-padded keys built here — is the
    identity, and the result is byte-identical to the bare ``_solve_strip_1d``
    this replaces. The label width is irrelevant to a vertical stack, so each
    placeable carries the uniform ``min_gap`` as its height.
    """
    solver = LayoutSolver()
    keys = [f"{key_prefix}{j:04d}" for j in range(len(naturals))]
    for key, nat in zip(keys, naturals, strict=True):
        solver.register(
            Placeable(
                key=key,
                anchors=((0.0, nat),),
                size=(0.0, min_gap),
                dof_axis="y",
                natural=nat,
                min_gap=min_gap,
            )
        )
    # greedy_fallback=False so this returns exactly what the bare primitive did:
    # None when the strip is full, leaving the caller's prefix-drop to fire (#80).
    placed = solver.solve_strip(lo=lo, hi=hi, axis="y", greedy_fallback=False)
    if placed is None:
        return None
    return [placed[k] for k in keys]


def _annotate_holes(dwg, a: Analysis, view_of_axis, found_patterns, holes_in=None):
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
        by_view.setdefault(view_of_axis[_axis_letter(holes[0])][0], []).append(holes)

    _, iso_y0, _, _ = _iso_bbox(dwg)
    plan_right = a.proj.plan_x(a.bb.max.X)
    plan_left = a.proj.plan_x(a.bb.min.X)
    side_right = a.proj.side_x(a.bb.max.Y)
    front_bottom = a.proj.front_z(a.bb.min.Z)
    tb_left = a.PAGE_W - a.TB_W - _TB_CLEAR
    tb_top = _TB_CLEAR + _TB_H

    # A section line will be placed when the part has z-axis holes with
    # counterbores, spotfaces, or blind bottoms (_add_section_view trigger).
    # When present, its extension lines overhang the plan view boundary by
    # ~arrow_length, so plan-view elbow must sit that far outside to clear them.
    # Room-check failures may still skip the section, but the offset is harmless.
    will_have_section_line = any(
        _axis_letter(h) == "z" and (h.cbore or h.spotface or h.bottom != "through")
        for h in a.holes
    )

    # v0.12.0 sub-clusters a machining-spec group into >=0 patterns: a filled
    # lattice -> one RectGrid, a rectangular perimeter -> its edge LinearArray
    # rows, plus a same-spec second bolt circle, etc.  Each hole belongs to at
    # most one pattern, so map hole -> pattern and split every spec group into
    # one callout PER pattern + one for the leftover unpatterned holes (#92).
    hole_pattern = {h: p for p in found_patterns for h in p.holes}

    def _subspecs(holes):
        """Split a spec group's holes into ``(subholes, pattern)`` entries — one
        per recognised pattern (its full hole set) plus a trailing ``(rest,
        None)`` for any holes no pattern claimed."""
        by_pat: dict = {}
        remainder = []
        for h in holes:
            p = hole_pattern.get(h)
            if p is None:
                remainder.append(h)
            else:
                by_pat.setdefault(p, []).append(h)
        out = [(list(p.holes), p) for p in by_pat]
        if remainder:
            out.append((remainder, None))
        return out

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
        if isinstance(pattern, BoltCircle):
            suffix = f"EQ SP ON ø{_fmt(pattern.diameter)} BC"
        elif isinstance(pattern, RectGrid):
            suffix = f"({pattern.rows}×{pattern.cols})"
        else:
            suffix = None
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
            view=view,
        )

    for view, view_groups in by_view.items():
        to_page = view_of_axis[{"plan": "z", "front": "y", "side": "x"}[view]][1]
        specs = []
        for holes in view_groups:
            for subholes, pattern in _subspecs(holes):
                specs.append((subholes, _build_callout(subholes, pattern), pattern))
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

        # --- Pass 2: Y placement (through the LayoutSolver, #80) ---
        right_ys = _solve_strip_via_layout(
            [s[3] for s in right_queue], min_gap, y_min, y_max, "hc_r"
        )
        left_ys = _solve_strip_via_layout(
            [s[3] for s in left_queue], min_gap, y_min, y_max, "hc_l"
        )

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
