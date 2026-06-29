"""The annotation orchestrator (#138 / ADR 0005, P5e).

`_auto_annotate` is the single entry point: it builds the IR (`build_part_model`),
plans the dimensions once, and drives the IR renderers (`from_model.render_*`) +
the capability passes (annotations.{sections,turned,pmi,holes}) + the title block.
Takes a duck-typed `dwg` and the `Analysis` namespace `a`. Imports only `_core`,
`layout`, the annotations passes, and third-party libs -- never make_drawing --
so the module graph stays a DAG.

The OD/centreline/bore furniture and the prismatic step-height + overall-height
ladder are now IR renderers (`render_rotational`/`render_height_ladder`, #237), not
inline. What remains inline is the orchestration/classification glue (concentric
bore set, side-drilled locations, the hole table) + the section/PMI passes.
"""

from __future__ import annotations

import math

from draftwright._core import (
    _CONCENTRIC_TOL_MM,
    _TABULATE_MIN_HOLES,
    Analysis,
    HoleRef,
    _add_title_block,
    _axis_letter,
    _fmt,
    _iso_bbox,
    _log,
    _tag_sequence,
)
from draftwright.annotations.from_model import (
    render_centermarks,
    render_diameters,
    render_envelope,
    render_height_ladder,
    render_locations,
    render_pmi,
    render_rotational,
    render_slots,
    render_step_lengths,
)
from draftwright.annotations.holes import (
    _annotate_holes,
    _locate_off_axis_holes,
)
from draftwright.annotations.sections import _add_detail_view, _add_section_view
from draftwright.model import build_part_model, plan_dimensions, plan_sections
from draftwright.recognition import (
    full_cylinders,
)


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


def _auto_annotate(dwg, a: Analysis, *, detail_view: bool = False):
    """Add the standard automatic dimensions, centrelines, and title block."""
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

    # Per-hole annotations from the feature records (#91, #92, #95): each
    # hole is annotated in the view its axis is normal to.
    # to_page maps a model-space *location* (x, y, z) → page coords (IR-typed, not a
    # recogniser Hole — ADR 0008 Amendment 6).
    view_of_axis = {
        "z": ("plan", lambda loc: (PX(loc[0]), PY(loc[1]))),
        "y": ("front", lambda loc: (FX(loc[0]), FZ(loc[2]))),
        "x": ("side", lambda loc: (SX(loc[1]), SZ(loc[2]))),
    }

    # The part model — built once and rendered from for the IR-migrated passes
    # (centre marks, turned diameters/lengths); ADR 0008 convergence / #229.
    _bores = tuple(_concentric_bore_diams(a)) if a.is_rotational else ()
    _model = build_part_model(
        a.part,
        holes=a.holes,
        patterns=a.patterns,
        bosses=a.bosses,
        slots=a.slots,
        prof=a.prof,
        step_zs=a.step_zs,
        rotational=(a.od_diam, _bores) if a.is_rotational else None,
        pmi=a.pmi,
    )
    # Plan the dimensions ONCE and thread the groups to every renderer that reads them
    # (was recomputed per renderer, #275). One rule set over DimParameters, literally.
    _groups = plan_dimensions(_model)

    # Rotational furniture — OD dim + axis centrelines + concentric bore leaders — IR
    # renderer (#237), placed early like the engine's inline block it replaces.
    render_rotational(dwg, _model, a)

    # Centre marks for every hole (all part classes) — IR renderer.
    render_centermarks(dwg, _groups)

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
    if a.is_rotational:
        feature_holes = [h for h in a.holes if not _is_concentric_hole(h, a)]
    # The surviving feature-hole *positions* (concentric bores excluded on rotational
    # parts) — the IR gates callouts/furniture/sections on membership in this set, so
    # no recogniser Hole object crosses into the renderers (Amendment 6, #263/#207).
    feature_keys = {HoleRef.of(h.location) for h in feature_holes}
    if feature_holes:
        _annotate_holes(dwg, a, view_of_axis, _groups, feature_keys)
    # Hole location dims — IR renderer (planner picks the refs + datum, #238); placed
    # through the existing above-view strips. Replaces the engine's _add_location_dims.
    render_locations(dwg, _model, a)

    if a.cross_diams and a.is_rotational and not feature_holes:
        _log.info(
            "Cross-hole ø%s detected but not annotated (requires section view)",
            _fmt(a.cross_diams[0]),
        )

    # Front-view right ladder: prismatic step heights + overall height — IR renderer,
    # through fv_zones.right preserving the leapfrog cursor (#237). Replaces the inline
    # dim_step_* + dim_height; the turned step-length chain (render_step_lengths) handles
    # turned parts, and a Z-turned overall height is suppressed there (ISO 129).
    render_height_ladder(dwg, _model, a)

    # Overall width (plan, below) + depth (side, below) envelope dims — IR renderer,
    # placed through the same below-strip zone allocators the engine used (zone-aware
    # render stage, ADR 0008). Suppression (square footprint / X-turned width) is now
    # the planner's decision (#250); the renderer just skips suppressed dims.
    render_envelope(dwg, _groups, a)

    # The section view goes last: its room check clears every annotation already
    # placed right of the side view (callout labels, height/step dim ladders). The
    # *trigger* + cut-plane row are the planner's decision (`plan_sections`, #207);
    # the renderer just draws the planned section. Concentric bores on a turned part
    # are excluded via feature_keys (the ldr_z leaders cover them).
    section = plan_sections(_model, feature_keys)
    if section is not None:
        _add_section_view(dwg, a, section)

    # Detail view: only when explicitly requested via build_drawing(detail_view=True).
    if detail_view:
        _add_detail_view(dwg, a)

    # Turned-part dimensions via the IR (ADR 0008 convergence). The model is built
    # once and fed to both renderers (#229 — no per-pass rebuild):
    #  - diameters: ø leaders, row below (X) / column left (Z), one path by frame
    #    axis. Replaces _annotate_turned_diameters.
    #  - step lengths: the chain that locates every shoulder, X and Z from one path
    #    (#223). Replaces the old X-only chain + the Z step-height ladder (skipped
    #    above for turned parts); the envelope dim along the turning axis was
    #    suppressed so the chain does not double-dimension the length.
    render_diameters(dwg, _groups)
    if a.prof is not None:
        render_step_lengths(dwg, _groups)

    # Side-drilled (X/Y-axis) hole locations — last, so the envelope and
    # turned-diameter dims claim their strip space first and are never evicted (#133).
    if feature_holes:
        _locate_off_axis_holes(dwg, a, holes_in=feature_holes)

    # Non-cylindrical machined features: slots / reduced across-flats sections
    # (#135) — IR renderer, placed through the zone strips (shared infra). Runs
    # after every hole/diameter pass so it claims strip space last.
    render_slots(dwg, _model, a)

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
        render_pmi(dwg, _model, a)

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
    holes = [
        h
        for h in a.holes
        if _axis_letter(h) == "z" and not dwg._is_hole_patterned(HoleRef.of(h.location))
    ]
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
        n: o
        for n, o in list(dwg.iter_annotations())
        if n.startswith(("hc_plan", "m_locx", "m_locy")) and not dwg._is_pattern_callout(n)
    }
    replaced_view = {n: dwg.view_of(n) for n in replaced}
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
