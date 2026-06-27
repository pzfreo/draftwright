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

from build123d_drafting.features import (
    full_cylinders,
)
from build123d_drafting.helpers import (
    Centerline,
    CenterMark,
    Leader,
)

from draftwright._core import (
    _CONCENTRIC_TOL_MM,
    _SLOT_DIM_DEPTH,
    _SLOT_DIM_HEIGHT,
    _SLOT_DIM_STEP,
    _SLOT_DIM_WIDTH,
    _TABULATE_MIN_HOLES,
    Analysis,
    _add_title_block,
    _axis_letter,
    _dim,
    _fmt,
    _iso_bbox,
    _legible_steps,
    _log,
    _tag_sequence,
)
from draftwright.annotations.holes import (
    _add_location_dims,
    _annotate_holes,
    _annotate_slots,
    _locate_off_axis_holes,
)
from draftwright.annotations.pmi import _annotate_pmi
from draftwright.annotations.sections import _add_detail_view, _add_section_view
from draftwright.annotations.turned import _annotate_turned_diameters


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
        for c in full_cylinders(z_cyls)
        if not c["external"]
        and math.hypot(c["axis_xyz"][0] - a.cx, c["axis_xyz"][1] - a.cy) <= _CONCENTRIC_TOL_MM
    }
    return [d for d in a.z_diams if d != a.od_diam and any(abs(d - c) <= 0.15 for c in concentric)]


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


def _auto_annotate(dwg, a: Analysis, *, detail_view: bool = False):
    """Add the standard automatic dimensions, centrelines, and title block."""
    draft = dwg.draft
    # Idempotent: clear build-time lint state so a second annotation pass does
    # not accumulate duplicate drop records.
    dwg._reset_build_issues()
    dwg._reset_dropped_callout_diams()

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

    # Non-cylindrical machined features: slots / reduced across-flats sections
    # (#135). Runs after every hole/diameter pass so it claims strip space last.
    _annotate_slots(dwg, a)

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
    holes = [h for h in a.holes if _axis_letter(h) == "z" and not dwg._is_hole_patterned(h)]
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
        if n.startswith(("hc_plan", "dim_locx", "dim_locy")) and not dwg._is_pattern_callout(n)
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
    dwg._drop_build_issues("table_dropped")
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
    dwg._drop_build_issues("callout_dropped", "location_ref_dropped")
