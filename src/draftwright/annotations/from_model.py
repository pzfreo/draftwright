"""from_model — render planner output into placed annotations (ADR 0008).

The renderer back-end of the compiler: a `DimensionGroup` (read *purely from its
planned parameters* + the feature's metadata) becomes placed `HoleCallout` /
`Dimension` annotations via the existing projection (`Drawing.at`), layout search,
and rendering primitives. GD&T symbols (⌴/↧) are the helper's geometry, which is
exactly why the IR carries semantic `role`s, not glyph strings.

This lives in `annotations/` (not `model/`) so the IR package stays pure — it
imports *down* into `model` + `_core`, and is called by the orchestrator (ADR 0008
Amendment 3: one path, this is its render stage). Judged by **correctness** (lint),
not equivalence to the engine. `render_step_lengths` is wired into production;
`render_into`/`render_callouts` drive the end-to-end slice + seam tests.
"""

from __future__ import annotations

import math

from build123d_drafting.helpers import CenterMark, Dimension, HoleCallout, Leader, TitleBlock

from draftwright._core import (
    _CONCENTRIC_TOL_MM,
    _DIAM_RE,
    _END_ON,
    _MARGIN,
    _SLOT_DIM_DEPTH,
    _SLOT_DIM_WIDTH,
    _dim,
    _fmt,
    _greedy_strip_ys,
    _iso_bbox,
    _legible_locations,
    _log,
    _solve_strip_ys,
)
from draftwright.annotations._common import _anno_box, _box_hits, _occupied_boxes
from draftwright.model.planner import DimensionGroup, plan_dimensions, plan_locations

# Which view + side an overall (envelope) dimension lands on, by its role.
_ENVELOPE_PLACEMENT = {
    "width": ("plan", "below"),  # X extent
    "height": ("front", "right"),  # Z extent
    "depth": ("side", "below"),  # Y extent
}

# Candidate leader-elbow offsets from the hole, in rings of increasing radius and
# eight directions — the renderer's local placement search (ADR 0003 layout: keep
# the callout near its feature but clear of the views and other callouts).
_ELBOW_OFFSETS = [
    (r * ux, r * uy)
    for r in (14.0, 22.0, 34.0, 50.0, 72.0)
    for ux, uy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (0, 1), (-1, 0), (0, -1))
]


def _first(group: DimensionGroup, kind: str, *roles: str) -> float | None:
    """First parameter value matching *kind* and any of *roles*, in role order."""
    for role in roles:
        for pd in group.dims:
            if pd.param.kind == kind and pd.param.role == role:
                return pd.param.value
    return None


def hole_callout_spec(group: DimensionGroup) -> dict | None:
    """A hole/pattern group's plan → `HoleCallout` kwargs, mirroring the engine's
    convention. ``None`` if not a hole-bearing callout.

    From the plan: bore from `DimParameter` roles; the cbore/spotface *step* with
    counterbore precedence (``step = cbore or spotface``, as the engine does);
    ``through`` inferred from the absence of a bore-depth param; ``count`` and the
    pattern *suffix* (``EQ SP ON ø50 BC`` / ``(3×3)``) from the source feature."""
    if group.feature_kind not in ("hole", "pattern"):
        return None
    bore = _first(group, "diameter", "bore")
    if bore is None:
        return None
    depth = _first(group, "depth", "bore")
    feat = group.feature
    count = getattr(feat, "count", 1)
    suffix = None
    pattern = getattr(feat, "pattern", None)
    bcd = getattr(feat, "bcd", None)
    rows, cols = getattr(feat, "rows", None), getattr(feat, "cols", None)
    if pattern == "bolt_circle" and bcd is not None:
        suffix = f"EQ SP ON ø{_fmt(bcd)} BC"
    elif pattern == "grid" and rows and cols:
        suffix = f"({rows}×{cols})"
    return {
        "diameter": bore,
        "count": count if count and count > 1 else None,
        "through": depth is None,
        "depth": depth,
        # counterbore precedence, spotface fallback — the engine's mapping
        "cbore_dia": _first(group, "diameter", "counterbore", "spotface"),
        "cbore_depth": _first(group, "depth", "counterbore", "spotface"),
        "suffix": suffix,
    }


def callout_from_spec(spec, draft, count) -> HoleCallout | None:
    """Build a `HoleCallout` from a :func:`hole_callout_spec` dict. *count* is passed
    explicitly (the bare/test path uses the spec's own count; the engine pass uses its
    view-local hole count) — the single callout builder both the IR and the migrating
    engine pass share, so the bore/cbore/suffix mapping lives in one place (#238 B1)."""
    if spec is None:
        return None

    def f(v):  # the IR carries clean floats (no baked labels); the renderer formats
        return _fmt(v) if v is not None else None

    return HoleCallout(
        f(spec["diameter"]),
        count=count,
        through=spec["through"],
        depth=f(spec["depth"]),
        cbore_dia=f(spec["cbore_dia"]),
        cbore_depth=f(spec["cbore_depth"]),
        suffix=spec["suffix"],
        draft=draft,
    )


def _hole_callout(dwg, group) -> HoleCallout | None:
    """The `HoleCallout` for a hole/pattern group from its planned spec, or ``None``
    if the group is not hole-bearing. The shared callout builder for both the placed
    (`render_into`) and the bare (`render_callouts`) paths."""
    spec = hole_callout_spec(group)
    return callout_from_spec(spec, dwg.draft, spec["count"]) if spec is not None else None


def _place_leader(dwg, view, tip_model, obstacles, *, label="", callout=None) -> Leader | None:
    """A leader (callout or plain ø label) placed clear of *obstacles* by searching
    outward from the feature (ADR 0003 layout): the first elbow whose label box is
    on-page and non-colliding wins; the farthest candidate is the fallback. With no
    obstacles the first on-page ring wins, so a bare call is deterministic."""
    tx, ty, *_ = dwg.at(view, *tip_model)

    def _on_page(b) -> bool:
        return bool(
            b[0] >= _MARGIN
            and b[1] >= _MARGIN
            and b[2] <= dwg.page_w - _MARGIN
            and b[3] <= dwg.page_h - _MARGIN
        )

    fallback = None
    for dx, dy in _ELBOW_OFFSETS:
        leader = Leader(
            tip=(tx, ty, 0),
            elbow=(tx + dx, ty + dy, 0),
            label=label,
            draft=dwg.draft,
            callout=callout,
        )
        box = _anno_box(leader)
        if box is None:
            return leader
        if _on_page(box) and not _box_hits(box, obstacles):
            return leader
        fallback = leader
    return fallback


def _hole_leader(dwg, group, obstacles) -> Leader | None:
    """A `HoleCallout` leader for a hole/pattern group, placed clear of *obstacles*.
    Tips at a real member hole (not the empty pattern centre)."""
    callout = _hole_callout(dwg, group)
    if callout is None:
        return None
    members = getattr(group.feature, "members", ())
    tip = members[0] if members else group.anchor
    return _place_leader(dwg, group.view, tip, obstacles, callout=callout)


def render_callouts(dwg, groups) -> list[Leader]:
    """The hole/pattern callout leaders for *groups* (does not mutate *dwg*). The
    bare path: placed against no obstacles, so deterministic."""
    return [ldr for g in groups if (ldr := _hole_leader(dwg, g, [])) is not None]


def _diameter_leader(dwg, group, obstacles) -> Leader | None:
    """A plain ø diameter callout for a boss/step group (the external diameter)."""
    dia = _first(group, "diameter", "boss", "step")
    if dia is None:
        return None
    return _place_leader(dwg, group.view, group.anchor, obstacles, label=f"ø{_fmt(dia)}")


def render_slots(dwg, model, a) -> int:
    """Dimension milled slots from the IR — width (the defining size, across
    ``width_axis``) + length (along ``long_axis``) + a position dim from the part
    datum, in the view the two axes span. Places through the engine's zone strips
    (shared infra, ADR 0008 Amend. 4); a dim with no clear room is dropped and
    recorded at info severity (place-what-fits). Sources `SlotFeature`s from the
    model; replaces the engine's `_annotate_slots`. Returns the count placed."""
    slots = [f for f in model.features if f.kind == "slot"]
    if not slots:
        return 0
    draft = dwg.draft
    external = _occupied_boxes(dwg)  # tested against candidate's full geometry
    placed: list = []  # this pass's own dims, tested label-box to label-box
    tier = draft.font_size + 2 * draft.pad_around_text
    views = {
        frozenset("xy"): ("plan", a.pv_zones, "x", a.proj.plan_x, "y", a.proj.plan_y),
        frozenset("xz"): ("front", a.fv_zones, "x", a.proj.front_x, "z", a.proj.front_z),
        frozenset("yz"): ("side", a.sv_zones, "y", a.proj.side_x, "z", a.proj.side_z),
    }

    def _bb(axis, hi):
        return getattr(a.bb.max if hi else a.bb.min, axis.upper())

    def _drop(kind, idx, view):
        dwg._record_build_issue(
            "info",
            "slot_dim_dropped",
            f"slot{idx} {kind} dim not placed (no room beside the {view})",
        )

    count = 0
    for i, s in enumerate(slots):
        view = views[frozenset((s.width_axis, s.long_axis))]
        name, zones, h_axis, h_proj, _v_axis, v_proj = view

        def _place(
            meas_axis,
            p_lo,
            p_hi,
            perp_lo,
            perp_hi,
            label,
            kind,
            anchor="center",
            vw=view,
            zn=zones,
            ha=h_axis,
            hp=h_proj,
            vp=v_proj,
            idx=i,
        ):
            # Snap the geometric span to the displayed (1-dp) value so drawn length
            # matches the label (else label-vs-measured lint trips).
            disp = float(_fmt(label))
            sgn = 1.0 if p_hi >= p_lo else -1.0
            if anchor == "center":
                mid = (p_lo + p_hi) / 2
                p_lo, p_hi = mid - sgn * disp / 2, mid + sgn * disp / 2
            else:
                p_hi = p_lo + sgn * disp
            if meas_axis == ha:
                meas_proj, perp_proj = hp, vp
                cands = (("above", zn.above, True), ("below", zn.below, False))
            else:
                meas_proj, perp_proj = vp, hp
                cands = (("right", zn.right, True), ("left", zn.left, False))
            for side, strip, hi in cands:
                if strip is None:
                    continue
                coord = strip.peek(tier)  # peek, don't allocate until it clears
                if coord is None:
                    continue
                witness = perp_proj(perp_hi if hi else perp_lo)  # off the slot's own edge
                if side in ("above", "below"):
                    e_lo = (meas_proj(p_lo), witness, 0)
                    e_hi = (meas_proj(p_hi), witness, 0)
                else:
                    e_lo = (witness, meas_proj(p_lo), 0)
                    e_hi = (witness, meas_proj(p_hi), 0)
                dim = _dim(e_lo, e_hi, side, abs(coord - witness), draft, label=_fmt(label))
                gbb = dim.bounding_box()
                full = (gbb.min.X, gbb.min.Y, gbb.max.X, gbb.max.Y)
                if _box_hits(full, external) or _box_hits(_anno_box(dim), placed):
                    continue
                strip.allocate(tier)
                dwg.add(dim, f"m_slot{idx}_{kind}", view=vw[0])
                placed.append(_anno_box(dim))
                return True
            return False

        half = s.width / 2
        if _place(
            s.width_axis, s.w_center - half, s.w_center + half, s.lo, s.hi, s.width, "width"
        ):
            count += 1
        else:
            _drop("width", i, name)
        if _place(
            s.long_axis, s.lo, s.hi, s.w_center - half, s.w_center + half, s.length, "length"
        ):
            count += 1
        else:
            _drop("length", i, name)
        datum = _bb(s.long_axis, False)
        if (s.lo - datum) * a.SCALE >= 1.0:
            if _place(
                s.long_axis,
                datum,
                s.lo,
                s.w_center - half,
                s.w_center + half,
                s.lo - datum,
                "pos",
                anchor="lo",
            ):
                count += 1
            else:
                _drop("position", i, name)
    return count


def render_locations(dwg, model, a) -> int:
    """Baseline X/Y hole-location dims from the IR (#238). The planner decides the
    intent (`plan_locations`: which refs, from which datum); this renderer owns the
    layout (Amendment 4) — X dims tier above the plan view, Y dims above the side
    view, nearest-datum-first, legibility-gated, allocated from the existing strips;
    a ref with no room is dropped as `location_ref_dropped`. Replaces the engine's
    `_add_location_dims`. Returns the count placed."""
    planned = plan_locations(model)
    if not planned:
        return 0
    draft = dwg.draft
    datum = planned[0].datum
    assert datum is not None  # plan_locations always sets the datum
    datum_x, datum_y = datum.at[0], datum.at[1]
    refs = []
    for pd in planned:
        if pd.param.span is None:
            continue
        rx, ry = pd.param.span[1][0], pd.param.span[1][1]
        # A rotational part's on-axis (concentric) *hole* bore is located by the
        # centreline, not a position dim (matches the engine's feature_holes
        # filter). A pattern ref (role "location_pattern" — e.g. a bolt-circle
        # centre) is NOT filtered, even on the axis.
        if (
            pd.param.role == "location"
            and a.is_rotational
            and math.hypot(rx - a.cx, ry - a.cy) <= _CONCENTRIC_TOL_MM
        ):
            continue
        refs.append((rx, ry))
    if not refs:
        return 0
    tier = draft.font_size + 2 * draft.pad_around_text
    n = 0

    # --- X locations: tier above the plan view ---
    PX, PY = a.proj.plan_x, a.proj.plan_y
    plan_top = PY(a.bb.max.Y)
    x_refs: list = []
    for r in refs:
        if not any(abs(r[0] - u[0]) < 0.5 for u in x_refs):
            x_refs.append(r)
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
    for nm, ann in dwg.iter_annotations():
        if nm.startswith("dim_pitch_plan") and getattr(ann, "dim_level_y", 0) > plan_top:
            a.pv_zones.above.allocate(10.0)  # consume space used by a pitch dim
    for i, (rx, ry) in enumerate(sorted(x_refs, key=lambda r: abs(r[0] - datum_x))):
        if abs(rx - datum_x) * a.SCALE < 1.0:
            continue  # on the datum edge — nothing to dimension
        _py = a.pv_zones.above.allocate(tier)
        if _py is None:
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
            f"m_locx{i}",
            view="plan",
        )
        n += 1

    # --- Y locations: tier above the side view (which maps world-Y horizontally) ---
    SX, SZ = a.proj.side_x, a.proj.side_z
    side_top = SZ(a.bb.max.Z)
    iso_x0, iso_y0, _, _ = _iso_bbox(dwg)
    y_refs: list = []
    for r in refs:
        if not any(abs(r[1] - u[1]) < 0.5 for u in y_refs):
            y_refs.append(r)
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
    for nm, ann in dwg.iter_annotations():
        if nm.startswith("dim_pitch_side") and getattr(ann, "dim_level_y", 0) > side_top:
            a.sv_zones.above.allocate(10.0)
    if y_refs and any(SX(ry) + 10 > iso_x0 - 4 for _, ry in y_refs):
        cap = iso_y0 - 4
        above = a.sv_zones.above
        if cap > above._cursor:
            above.outer_limit = min(above.outer_limit, cap)
        else:
            _log.warning(
                "sv_zones.above cursor %.1f >= iso_y0 cap %.1f: Y-location dims may overlap iso",
                above._cursor,
                cap,
            )
    for i, (rx, ry) in enumerate(sorted(y_refs, key=lambda r: abs(r[1] - datum_y))):
        if abs(ry - datum_y) * a.SCALE < 1.0:
            continue
        _py = a.sv_zones.above.allocate(tier)
        if _py is None:
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
            f"m_locy{i}",
            view="side",
        )
        n += 1
    return n


def render_centermarks(dwg, model) -> int:
    """A centre mark on every hole (plain holes + each pattern member), in the view
    normal to the hole's axis (`_END_ON`), sized by its diameter — the IR migration
    of the engine's inline centre-mark loop. Returns the count placed."""
    n = 0
    for g in plan_dimensions(model):
        if g.feature_kind not in ("hole", "pattern"):
            continue
        dia = _first(g, "diameter", "bore") or 0.0
        size = max(2.5, dia * dwg.scale + 2.0)
        view = _END_ON.get(g.feature.frame.axis, "plan")
        members = getattr(g.feature, "members", ()) or [g.anchor]
        for loc in members:
            px, py, *_ = dwg.at(view, *loc)
            dwg.add(CenterMark((px, py, 0), size, dwg.draft), f"m_cm{n}", view=view)
            n += 1
    return n


def _mentioned_diameters(dwg) -> set[float]:
    """Diameters already called out on the drawing (ø-labels + ``covers_diameters``)
    — so a diameter another annotation already documents is not repeated."""
    diams: set[float] = set()
    for _, ann in dwg.iter_annotations():
        if isinstance(ann, TitleBlock):
            continue
        for m in _DIAM_RE.finditer(getattr(ann, "label", None) or ""):
            diams.add(float(m.group(1)))
        for v in getattr(ann, "covers_diameters", ()):
            diams.add(float(v))
    return diams


def _diameter_row_below(dwg, items) -> int:
    """ø-callout row BELOW the front view for X-turned step/boss diameters (#77).
    *items* is ``[(anchor, diameter), ...]``. The row is dropped clear of anything
    already below the profile; labels spread along page-x by the ADR-0003 strip
    solve. Skips (returns 0) if there is no room — the diameters then surface as
    ``feature_not_dimensioned``."""
    if not items:
        return 0
    draft = dwg.draft
    fx0, fy0, fx1, _ = dwg.view_bounds("front")
    obstacle_bottom = fy0
    for o in dwg.items:
        try:
            ob = o.bounding_box()
        except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
            continue
        if ob.min.Y < fy0 and ob.max.X > fx0 and ob.min.X < fx1:
            obstacle_bottom = min(obstacle_bottom, ob.min.Y)
    label_y = obstacle_bottom - (draft.font_size + 4 * draft.pad_around_text)
    if label_y < _MARGIN + draft.font_size:
        return 0
    specs = []  # (tip_page, label), tip on the step's bottom silhouette
    for anchor, dia in items:
        ax, ay, az = anchor
        tip = dwg.at("front", ax, ay, az - dia / 2)
        specs.append((tip, f"ø{_fmt(dia)}"))
    specs.sort(key=lambda s: s[0][0])
    half_w = max(len(label) for _, label in specs) * draft.font_size * 0.62 / 2
    min_gap = 2 * half_w + 2 * draft.pad_around_text
    naturals = [tip[0] for tip, _ in specs]
    xs = _solve_strip_ys(naturals, min_gap, fx0 + half_w, fx1 - half_w) or _greedy_strip_ys(
        naturals, min_gap, fx0 + half_w, fx1 - half_w
    )
    if xs is None:
        return 0
    for i, ((tip, label), lx) in enumerate(zip(specs, xs, strict=True)):
        dwg.add(
            Leader(tip=(tip[0], tip[1], 0), elbow=(lx, label_y, 0), label=label, draft=draft),
            f"m_dia_x{i}",
            view="front",
        )
    return len(specs)


def _diameter_column_left(dwg, items) -> int:
    """ø-callout column to the LEFT of the front view for Z-turned step/boss
    diameters (#131) — the page-Y mirror of the row-below. A per-label occupancy
    gate drops only a label that would overprint a bore leader / existing callout
    sharing the left region (#144), never the whole column. Returns the count placed."""
    if not items:
        return 0
    draft = dwg.draft
    fx0, fy0, _, fy1 = dwg.view_bounds("front")
    label_w = max(len(f"ø{_fmt(dia)}") for _, dia in items) * draft.font_size * 0.62
    elbow_x = fx0 - (draft.font_size + 2 * draft.pad_around_text)
    if elbow_x - label_w < _MARGIN:
        return 0
    specs = []  # (tip_page, label), tip on the step's left silhouette
    for anchor, dia in items:
        ax, ay, az = anchor
        tip = dwg.at("front", ax - dia / 2, ay, az)
        specs.append((tip, f"ø{_fmt(dia)}"))
    specs.sort(key=lambda s: s[0][1])
    half_h = draft.font_size / 2 + draft.pad_around_text
    min_gap = 2 * half_h
    naturals = [tip[1] for tip, _ in specs]
    ys = _solve_strip_ys(naturals, min_gap, fy0 + half_h, fy1 - half_h) or _greedy_strip_ys(
        naturals, min_gap, fy0 + half_h, fy1 - half_h
    )
    if ys is None:
        return 0
    occupied = _occupied_boxes(dwg)  # bore leaders + other left-column callouts
    placed = 0
    for i, ((tip, label), ly) in enumerate(zip(specs, ys, strict=True)):
        ldr = Leader(tip=(tip[0], tip[1], 0), elbow=(elbow_x, ly, 0), label=label, draft=draft)
        if _box_hits(_anno_box(ldr), occupied):
            continue  # would overprint a bore leader / existing callout — drop just this one
        dwg.add(ldr, f"m_dia_z{i}", view="front")
        occupied.append(_anno_box(ldr))
        placed += 1
    return placed


def render_diameters(dwg, model, tol: float = 0.15) -> int:
    """ø leaders for a turned part's external step/boss diameters, from the IR —
    one distinct callout per diameter, in a tidy row below the front view
    (X-turning) or a column to its left (Z-turning). Orientation is the feature
    frame's axis, not two passes. Replaces the engine's ``_annotate_turned_diameters``
    (ADR 0008 convergence). Diameters another annotation already covers are skipped."""
    mentioned = _mentioned_diameters(dwg)
    seen: set[tuple[str, float]] = set()
    rows: list = []  # X-turned (anchor, dia)
    cols: list = []  # Z-turned (anchor, dia)
    for g in plan_dimensions(model):
        if g.feature_kind not in ("step", "boss"):
            continue
        dia = next((pd.param.value for pd in g.dims if pd.param.kind == "diameter"), None)
        if dia is None or any(abs(dia - m) <= tol for m in mentioned):
            continue
        axis = g.feature.frame.axis
        key = (axis, round(dia, 2))
        if key in seen:  # one distinct callout per diameter
            continue
        seen.add(key)
        if axis == "x":
            rows.append((g.anchor, dia))
        elif axis == "z":
            cols.append((g.anchor, dia))
    return _diameter_row_below(dwg, rows) + _diameter_column_left(dwg, cols)


def _env_pd(group, role):
    """The PlannedDimension for an envelope role (width/depth/height), or None."""
    return next((pd for pd in group.dims if pd.param.role == role), None)


def render_envelope(dwg, model, a) -> int:
    """Overall width (plan, below) + depth (side, below) envelope dims via the IR,
    placed through the engine's below-strip zone allocators (the zone-aware render
    stage — so a migrated dim still coordinates with the un-migrated passes sharing
    those strips). The **planner** decides suppression (square footprint / X-turned;
    #250); this renderer just skips suppressed dims and places the rest. Returns the
    count placed."""
    env = next((g for g in plan_dimensions(model) if g.feature_kind == "envelope"), None)
    if env is None:
        return 0
    n = 0
    width = _env_pd(env, "width")
    if width is not None and not width.suppressed and width.param.span is not None:
        (x0, y0, z0), (x1, _, _) = width.param.span
        p1, p2 = dwg.at("plan", x0, y0, z0), dwg.at("plan", x1, y0, z0)
        witness = p1[1] - 2
        py = a.pv_zones.below.allocate(_SLOT_DIM_WIDTH)
        if py is not None:
            dwg.add(
                _dim(
                    (p1[0], witness, 0),
                    (p2[0], witness, 0),
                    "below",
                    witness - py,
                    dwg.draft,
                    label=_fmt(width.param.value),
                ),
                "m_env_width",
                view="plan",
            )
            n += 1
    depth = _env_pd(env, "depth")
    if depth is not None and not depth.suppressed and depth.param.span is not None:
        (x0, y0, z0), (_, y1, _) = depth.param.span
        p1, p2 = dwg.at("side", x0, y0, z0), dwg.at("side", x0, y1, z0)
        witness = p1[1] - 2
        pd = a.sv_zones.below.allocate(_SLOT_DIM_DEPTH)
        if pd is not None:
            dwg.add(
                _dim(
                    (p1[0], witness, 0),
                    (p2[0], witness, 0),
                    "below",
                    witness - pd,
                    dwg.draft,
                    label=_fmt(depth.param.value),
                ),
                "m_env_depth",
                view="side",
            )
            n += 1
    return n


def _envelope_dims(dwg, group) -> list[tuple[Dimension, str]]:
    """Overall (width/height/depth) linear dims, each placed just outside its view."""
    out: list[tuple[Dimension, str]] = []
    for pd in group.dims:
        place = _ENVELOPE_PLACEMENT.get(pd.param.role)
        if place is None or pd.param.span is None:
            continue
        view, side = place
        a, b = pd.param.span
        p1, p2 = dwg.at(view, *a), dwg.at(view, *b)
        dim = _dim(
            (p1[0], p1[1], 0), (p2[0], p2[1], 0), side, 9.0, dwg.draft, label=_fmt(pd.param.value)
        )
        out.append((dim, view))
    return out


def render_step_lengths(dwg, model) -> int:
    """Unified turned step-length chain (ADR 0008 #223) — one IR-driven path that
    replaces the engine's asymmetric X-chain / Z-ladder. Each `StepFeature`'s length
    span is projected into the front view; the chain runs *along the projected axis*
    just outside the view — **horizontal** for an X-turned part, **vertical** for a
    Z-turned part. Orientation is the projected span direction, not a branch, so X
    and Z get the same complete chain. Returns the number of step dims placed.

    The chain is collinear (all segments share one offset line) and tiles end to
    end, so every shoulder is located. Crowded labels are spread along the line by
    the ADR-0003 strip solve (the primitive the engine's X chain already used)."""
    segs = []  # (page_lo, page_hi, value), in axis order
    for g in plan_dimensions(model):
        if g.feature_kind != "step":
            continue
        length = next(
            (pd.param for pd in g.dims if pd.param.kind == "length" and pd.param.span is not None),
            None,
        )
        if length is None or length.span is None:
            continue
        a, b = length.span
        pa, pb = dwg.at("front", *a), dwg.at("front", *b)
        segs.append((pa, pb, length.value))
    if not segs:
        return 0
    vb = dwg.view_bounds("front")
    if vb is None:
        return 0
    x0, y0, x1, y1 = vb
    draft = dwg.draft
    gap = draft.font_size + 4 * draft.pad_around_text
    # Orientation is data: the projected span direction. Horizontal → X-turned
    # (chain above the view); vertical → Z-turned (chain left of the view).
    horizontal = abs(segs[0][1][0] - segs[0][0][0]) >= abs(segs[0][1][1] - segs[0][0][1])

    # Spread crowded labels along a horizontal chain (ADR-0003 strip solve), then
    # carry each label back to its segment via label_offset_x (the only along-line
    # offset the Dimension primitive supports). A vertical chain places plain dims.
    offsets = [0.0] * len(segs)
    if horizontal:
        centers = [(pa[0] + pb[0]) / 2 for pa, pb, _ in segs]
        half_w = max(len(_fmt(v)) for *_, v in segs) * draft.font_size * 0.62 / 2
        min_gap = 2 * half_w + 2 * draft.pad_around_text
        solved = _solve_strip_ys(centers, min_gap, x0 + half_w, x1 - half_w) or _greedy_strip_ys(
            centers, min_gap, x0 + half_w, x1 - half_w
        )
        if solved:
            offsets = [s - c for s, c in zip(solved, centers)]

    candidates = []
    for i, (pa, pb, value) in enumerate(segs):
        if horizontal:  # X-turned: chain above the view, witnesses rise from the top
            p1, p2, side = (pa[0], y1, 0), (pb[0], y1, 0), "above"
            kw = {"label": _fmt(value), "label_offset_x": offsets[i]}
        else:  # Z-turned: chain right of the view (the clear zone), witnesses from the right edge
            p1, p2, side = (x1, pa[1], 0), (x1, pb[1], 0), "right"
            kw = {"label": _fmt(value)}
        candidates.append((f"m_steplen{i}", _dim(p1, p2, side, gap, draft, **kw)))

    # Room guard (the engine's contract): if any dim would fall off the drawable
    # page, place NONE and let lint report axial_length_missing — never run the
    # chain off the page edge.
    page = (_MARGIN, _MARGIN, dwg.page_w - _MARGIN, dwg.page_h - _MARGIN)
    for _, dim in candidates:
        box = _anno_box(dim)
        if box is not None and not (
            page[0] <= box[0] and box[2] <= page[2] and page[1] <= box[1] and box[3] <= page[3]
        ):
            return 0
    for name, dim in candidates:
        dwg.add(dim, name, view="front")
    return len(candidates)


def render_into(dwg, model) -> int:
    """End-to-end demonstration seam: plan *model* and **add** its annotations to
    *dwg* (which must already have its views, e.g. ``build_drawing(part,
    auto_dims=False)``). Diameter callouts (holes/patterns/bosses) are placed clear
    of the views and of each other (ADR-0003 layout); overall envelope dims sit just
    outside their view. Returns the count added; lint *dwg* to judge correctness.

    **Test-only.** This drives the e2e-slice tests; production uses the per-feature
    renderers (``render_diameters``/``render_step_lengths``/``render_envelope``/…)
    wired into the orchestrator. To be retired once the holes epic supersedes its
    remaining hole-callout capability (#251)."""
    view_boxes = [vb for v in dwg.views if (vb := dwg.view_bounds(v)) is not None]
    placed: list = []
    n = 0
    for g in plan_dimensions(model):
        if g.feature_kind in ("hole", "pattern"):
            ann = _hole_leader(dwg, g, view_boxes + placed)
        elif g.feature_kind in ("boss", "step"):
            ann = _diameter_leader(dwg, g, view_boxes + placed)
        elif g.feature_kind == "envelope":
            for dim, view in _envelope_dims(dwg, g):
                dwg.add(dim, f"m_env{n}", view=view)
                n += 1
            continue
        else:
            ann = None
        if ann is None:
            continue
        dwg.add(ann, f"m_callout{n}", view=g.view)
        n += 1
        box = _anno_box(ann)
        if box is not None:
            placed.append(box)
    return n
