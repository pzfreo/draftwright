"""from_model — render planner output into placed annotations (ADR 0008).

The renderer back-end of the compiler: a `DimensionGroup` (read *purely from its
planned parameters* + the feature's metadata) becomes placed `HoleCallout` /
`Dimension` annotations via the existing projection (`Drawing.at`), layout search,
and rendering primitives. GD&T symbols (⌴/↧) are the helper's geometry, which is
exactly why the IR carries semantic `role`s, not glyph strings.

This lives in `annotations/` (not `model/`) so the IR package stays pure — it
imports *down* into `model` + `_core`, and is called by the orchestrator (ADR 0008
Amendment 3: one path, this is its render stage). Judged by **correctness** (lint),
not equivalence to the engine. All renderers here (`render_diameters`/`render_envelope`/
`render_locations`/`render_centermarks`/`render_step_lengths`/`render_slots`, and the
shared `hole_callout_spec`/`callout_from_spec` consumed by the holes pass) are wired
into production — the test-only `render_into`/`render_callouts` parallel was retired
once the holes epic landed (#251).
"""

from __future__ import annotations

import math

from build123d_drafting.helpers import Centerline, CenterMark, HoleCallout, Leader, TitleBlock

from draftwright._core import (
    _CONCENTRIC_TOL_MM,
    _DIAM_RE,
    _END_ON,
    _MARGIN,
    _MIN_STEP_SEP_MM,
    _SLOT_DIM_DEPTH,
    _SLOT_DIM_HEIGHT,
    _SLOT_DIM_STEP,
    _SLOT_DIM_WIDTH,
    DetailRequest,
    _dim,
    _fmt,
    _greedy_strip_ys,
    _iso_bbox,
    _legible_locations,
    _legible_steps,
    _log,
    _solve_strip_ys,
)
from draftwright.annotations._common import (
    CROSSABLE_TYPES,
    Escalation,
    _anno_box,
    _box_hits,
    carve_free_position,
    carve_free_segments,
    place_strip_candidates,
    strip_free_span,
    strip_obstacles,
)
from draftwright.model.ir import HoleFeature, PatternFeature
from draftwright.model.planner import DimensionGroup, plan_locations


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
    feat = group.feature
    if not isinstance(feat, HoleFeature | PatternFeature):
        return None
    bore = _first(group, "diameter", "bore")
    if bore is None:
        return None
    depth = _first(group, "depth", "bore")
    count = feat.count
    suffix = None
    if isinstance(feat, PatternFeature):
        if feat.pattern == "bolt_circle" and feat.bcd is not None:
            suffix = f"EQ SP ON ø{_fmt(feat.bcd)} BC"
        elif feat.pattern == "grid" and feat.rows and feat.cols:
            suffix = f"({feat.rows}×{feat.cols})"
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
    engine pass share, so the bore/cbore/suffix mapping lives in one place (#238 B1).

    **This is the only place draftwright constructs a `HoleCallout`** — and it MUST
    pass each numeric value as a `_fmt` string, never a raw float: `HoleCallout`
    renders a float (``ø8.0``) wider than the equivalent string (``ø8``), which
    shifts placement and can drop callouts (#261). Keep the formatting here; the IR
    carries clean floats (no baked labels). The robust fix — `HoleCallout` formatting
    its own numeric inputs — is upstream in build123d-drafting-helpers."""
    if spec is None:
        return None

    def f(v):  # see the #261 note above — every value crosses as a formatted string
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


def _record_slot_drop(dwg, kind, idx, view, feat):
    """Record a slot dim the layout could not place (#135).

    Info severity — a dim with no clear room is dropped as "place what fits",
    not an error. Alongside the lint code, appends a first-class ``Escalation``
    (ADR 0009 Amdt 1, #351 PR-4a) so the drop is object-visible too; slots have
    no natural grouping remedy like a recognised hole pattern, so no resolver
    consumes this yet — purely additive.
    """
    dwg._record_build_issue(
        "info",
        "slot_dim_dropped",
        f"slot{idx} {kind} dim not placed (no room beside the {view})",
    )
    dwg._escalations.append(
        Escalation(kind="slot", view=view, feature=feat, reason=f"no room beside the {view}")
    )


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
    tier = draft.font_size + 2 * draft.pad_around_text
    views = {
        frozenset("xy"): ("plan", a.pv_zones, "x", a.proj.plan_x, "y", a.proj.plan_y),
        frozenset("xz"): ("front", a.fv_zones, "x", a.proj.front_x, "z", a.proj.front_z),
        frozenset("yz"): ("side", a.sv_zones, "y", a.proj.side_x, "z", a.proj.side_z),
    }

    def _bb(axis, hi):
        return getattr(a.bb.max if hi else a.bb.min, axis.upper())

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
            # Try each candidate side through the shared carve (P3, #150): occupancy is
            # every placed annotation's FULL footprint (strip_obstacles) — not the old
            # label-box `external`/`placed` check, which missed leader shafts a slot dim
            # could overprint. A side whose carve/corridor rejects the dim falls through
            # to the next; if none takes it, the caller drops it (place-what-fits).
            for side, strip, hi in cands:
                if strip is None:
                    continue
                witness = perp_proj(perp_hi if hi else perp_lo)  # off the slot's own edge
                axis = "y" if side in ("above", "below") else "x"
                if side in ("above", "below"):
                    e_lo = (meas_proj(p_lo), witness, 0)
                    e_hi = (meas_proj(p_hi), witness, 0)
                else:
                    e_lo = (witness, meas_proj(p_lo), 0)
                    e_hi = (witness, meas_proj(p_hi), 0)
                cand = (
                    f"m_slot{idx}_{kind}",
                    lambda pos, _el=e_lo, _eh=e_hi, _s=side, _w=witness: _dim(
                        _el, _eh, _s, abs(pos - _w), draft, label=_fmt(label)
                    ),
                )
                if not place_strip_candidates(dwg, strip, vw[0], axis, [cand], tier):
                    return True
            return False

        half = s.width / 2
        if _place(
            s.width_axis, s.w_center - half, s.w_center + half, s.lo, s.hi, s.width, "width"
        ):
            count += 1
        else:
            _record_slot_drop(dwg, "width", i, name, s)
        if _place(
            s.long_axis, s.lo, s.hi, s.w_center - half, s.w_center + half, s.length, "length"
        ):
            count += 1
        else:
            _record_slot_drop(dwg, "length", i, name, s)
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
                _record_slot_drop(dwg, "position", i, name, s)
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
        dwg._escalations.append(Escalation("location", "plan", None, "illegible"))
    _kept_x_set = set(_kept_x)
    x_refs = [r for r in x_refs if r[0] not in _x_drawable or r[0] in _kept_x_set]
    # Collect X-location dims nearest-datum-first and place them through the shared
    # carve (P3, #150): the dim_pitch_plan dims above the view are now obstacles the
    # carve avoids structurally, retiring the old manual pv_zones.above.allocate(10.0)
    # pitch reservation + the per-dim cursor. No alternate view for a plan-X location,
    # so a corridor-blocked dim is kept (force pass) rather than relocated; only a
    # physically full strip drops (→ location_ref_dropped, escalates the hole table).
    x_cands = []
    for i, (rx, ry) in enumerate(sorted(x_refs, key=lambda r: abs(r[0] - datum_x))):
        if abs(rx - datum_x) * a.SCALE < 1.0:
            continue  # on the datum edge — nothing to dimension
        x_cands.append(
            (
                f"m_locx{i}",
                lambda pos, _rx=rx, _ry=ry: _dim(
                    (PX(datum_x), PY(_ry), 0),
                    (PX(_rx), PY(_ry), 0),
                    "above",
                    pos - PY(_ry),
                    draft,
                    label=_fmt(_rx - datum_x),
                ),
            )
        )
    _left = place_strip_candidates(dwg, a.pv_zones.above, "plan", "y", x_cands, tier)
    _left = place_strip_candidates(dwg, a.pv_zones.above, "plan", "y", _left, tier, force=True)
    _dropped_x = {_name for _name, _ in _left}
    for _name, _ in x_cands:
        if _name not in _dropped_x:
            # A candidate the scattered-hole table may replace (#351 PR-4c).
            dwg._cover_scattered_hole_doc(_name)
    for _name, _ in _left:
        dwg._record_build_issue(
            "warning",
            "location_ref_dropped",
            f"{_name} not placed (no room above the plan view)",
        )
        dwg._escalations.append(Escalation("location", "plan", _name, "strip_full"))
    n += len(x_cands) - len(_left)

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
        dwg._escalations.append(Escalation("location", "side", None, "illegible"))
    _kept_y_set = set(_kept_y)
    y_refs = [r for r in y_refs if r[1] not in _y_drawable or r[1] in _kept_y_set]
    # Cap the side-above strip below the iso view so Y-location dims never run under it
    # (the carve respects outer_limit); the dim_pitch_side dims are obstacles the carve
    # avoids structurally, retiring the old manual allocate(10.0) reservation + cursor.
    if y_refs and any(SX(ry) + 10 > iso_x0 - 4 for _, ry in y_refs):
        a.sv_zones.above.outer_limit = min(a.sv_zones.above.outer_limit, iso_y0 - 4)
    y_cands = []
    for i, (rx, ry) in enumerate(sorted(y_refs, key=lambda r: abs(r[1] - datum_y))):
        if abs(ry - datum_y) * a.SCALE < 1.0:
            continue
        y_cands.append(
            (
                f"m_locy{i}",
                lambda pos, _ry=ry: _dim(
                    (SX(datum_y), SZ(a.bb.max.Z), 0),
                    (SX(_ry), SZ(a.bb.max.Z), 0),
                    "above",
                    pos - side_top,
                    draft,
                    label=_fmt(_ry - datum_y),
                ),
            )
        )
    _left = place_strip_candidates(dwg, a.sv_zones.above, "side", "y", y_cands, tier)
    _left = place_strip_candidates(dwg, a.sv_zones.above, "side", "y", _left, tier, force=True)
    _dropped_y = {_name for _name, _ in _left}
    for _name, _ in y_cands:
        if _name not in _dropped_y:
            # A candidate the scattered-hole table may replace (#351 PR-4c).
            dwg._cover_scattered_hole_doc(_name)
    for _name, _ in _left:
        dwg._record_build_issue(
            "warning",
            "location_ref_dropped",
            f"{_name} not placed (no room above the side view)",
        )
        dwg._escalations.append(Escalation("location", "side", _name, "strip_full"))
    n += len(y_cands) - len(_left)
    return n


def render_centermarks(dwg, groups) -> int:
    """A centre mark on every hole (plain holes + each pattern member), in the view
    normal to the hole's axis (`_END_ON`), sized by its diameter — the IR migration
    of the engine's inline centre-mark loop. Returns the count placed."""
    n = 0
    for g in groups:
        feat = g.feature
        if not isinstance(feat, HoleFeature | PatternFeature):
            continue
        dia = _first(g, "diameter", "bore") or 0.0
        size = max(2.5, dia * dwg.scale + 2.0)
        view = _END_ON.get(feat.frame.axis, "plan")
        members = feat.members or (g.anchor,)
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
    # Full-footprint occupancy (leader shafts, witness/extension lines, hatch) — NOT
    # the label-box-only `_occupied_boxes`, which is blind to a bore callout's leader
    # SHAFT, so a ø label could silently overprint it (the #133/#225/#305 invisible-
    # occupant class, #358). Centre lines stay crossable (a diameter dim may cross one).
    occupied = strip_obstacles(dwg, view="front", crossable=CROSSABLE_TYPES)
    placed = 0
    for i, ((tip, label), ly) in enumerate(zip(specs, ys, strict=True)):
        ldr = Leader(tip=(tip[0], tip[1], 0), elbow=(elbow_x, ly, 0), label=label, draft=draft)
        if _box_hits(_anno_box(ldr), occupied):
            continue  # would overprint a bore leader / existing callout — drop just this one
        dwg.add(ldr, f"m_dia_z{i}", view="front")
        occupied.append(_anno_box(ldr))
        placed += 1
    return placed


def render_diameters(dwg, groups, tol: float = 0.15) -> int:
    """ø leaders for a turned part's external step/boss diameters, from the IR —
    one distinct callout per diameter, in a tidy row below the front view
    (X-turning) or a column to its left (Z-turning). Orientation is the feature
    frame's axis, not two passes. Replaces the engine's ``_annotate_turned_diameters``
    (ADR 0008 convergence). Diameters another annotation already covers are skipped."""
    mentioned = _mentioned_diameters(dwg)
    seen: set[tuple[str, float]] = set()
    rows: list = []  # X-turned (anchor, dia)
    cols: list = []  # Z-turned (anchor, dia)
    for g in groups:
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


def envelope_group(groups):
    """The envelope `DimensionGroup` in *groups*, or None."""
    return next((g for g in groups if g.feature_kind == "envelope"), None)


def env_dim_placed(pd) -> bool:
    """Whether :func:`render_envelope` will actually place an envelope dim for the
    PlannedDimension *pd* — present, not suppressed by the planner (square footprint /
    X-turned, #250), and carrying a span. The single source of truth for that
    decision, shared with the orchestrator's side-below tier reservation so the two
    can never drift (#316 review)."""
    return pd is not None and not pd.suppressed and pd.param.span is not None


def _envelope_tier(dwg, strip, view, size):
    """The page-coord at which an envelope dim of *size* stacks OUTSIDE every placed
    obstacle in *view* on *strip* — the outermost free tier that fits, placed at its
    inner (view-facing) edge — or None if no free tier fits.

    Cursor-free (ADR 0009 carve), unlike the ``Strip.allocate`` it replaces: the
    envelope dim lands beyond the feature/location dims already placed on this strip
    (they become obstacles here), giving the ISO 'overall dim outermost' stack **by
    construction**. That is enforced here by choosing the *outermost* fitting free
    segment — NOT merely the one nearest the view, which would land the envelope
    inside any obstacle sitting in a middle/outer tier (a callout label, a leader
    shaft) whenever an inner tier happened to be free, inverting the stack. The old
    ``allocate`` gave the right order only because an earlier pass had advanced a
    shared cursor; that coupling inverted the moment the location pass moved to
    ``plan_strip`` (#321), which never advances the cursor. Reading obstacle boxes
    decouples the two passes. #133 mandatory-dim starvation is still guarded upstream
    by the orchestrator's tier reservation.

    Assumes a below/above strip (Y stacking axis) — the only strips ``render_envelope``
    uses; a left/right strip would need the X interval of each obstacle box."""
    lo, hi, inner = strip_free_span(strip)
    obst = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
    segs = carve_free_segments(lo, hi, [(b[1], b[3]) for b in obst], strip.spacing)
    # Fit tolerant of float error at the reservation boundary (#133): the guaranteed
    # segment is exactly `size` wide in the saturated worst case.
    fitting = [s for s in segs if s[1] - s[0] >= size - 1e-9]
    if not fitting:
        return None
    if inner == hi:  # below/left: outermost = smallest coords; place at seg inner (hi) edge
        return min(fitting, key=lambda s: s[0])[1]
    return max(fitting, key=lambda s: s[1])[0]  # above/right: outermost = largest coords


def render_envelope(dwg, groups, a) -> int:
    """Overall width (plan, below) + depth (side, below) envelope dims via the IR,
    placed by carving each below-strip around the feature/location dims already on it
    (ADR 0009) — so the overall dim stacks outermost by construction, no longer via a
    shared strip cursor an earlier pass had to advance. The **planner** decides
    suppression (square footprint / X-turned; #250); this renderer just skips
    suppressed dims and places the rest. Returns the count placed."""
    env = envelope_group(groups)
    if env is None:
        return 0
    n = 0
    width = _env_pd(env, "width")
    if env_dim_placed(width):
        (x0, y0, z0), (x1, _, _) = width.param.span
        p1, p2 = dwg.at("plan", x0, y0, z0), dwg.at("plan", x1, y0, z0)
        witness = p1[1] - 2
        py = _envelope_tier(dwg, a.pv_zones.below, "plan", _SLOT_DIM_WIDTH)
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
    if env_dim_placed(depth):
        (x0, y0, z0), (_, y1, _) = depth.param.span
        p1, p2 = dwg.at("side", x0, y0, z0), dwg.at("side", x0, y1, z0)
        witness = p1[1] - 2
        pd = _envelope_tier(dwg, a.sv_zones.below, "side", _SLOT_DIM_DEPTH)
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


def _record_step_chain_drop(dwg, why: str) -> None:
    """Record the ``step_dim_dropped`` lint warning when a turned step-length chain
    is dropped whole (#362). These drops were silent (debug log only) — the user got
    a drawing with no step-length dimensioning and no signal. Mirrors
    ``render_height_ladder``'s prismatic drop, but records ONLY the lint code (not an
    ``Escalation(kind="step")``): that escalation is consumed by
    ``_request_prismatic_detail`` (sections.py), which would redraw *prismatic*
    height-above-base dims for a *turned* chain — the wrong semantics #351 PR-4b
    removed. A Z-turned-appropriate detail-view remedy is a tracked follow-up."""
    dwg._record_build_issue(
        "warning",
        "step_dim_dropped",
        f"step-length chain dropped: {why} at this scale (use a detail view)",
    )


def _draw_step_chain(dwg, view, segs, name_prefix, detail_scale=None, allow_collapse=True) -> int:
    """Place a turned step-length chain in *view* from *segs* — each ``(pa, pb,
    value)`` already projected to *view*'s page coords, in axis order. Orientation is
    data (the projected span direction): horizontal → chain above the view, vertical
    → chain to the right. A uniform run collapses to one ``N× v`` dim (#230); else a
    per-segment chain, staggered into a near/far tier only when crowded (ISO 129-1,
    #293); skipped if even two tiers can't separate the labels, or if any dim would
    fall off the page. ``detail_scale`` tags the dims for label-vs-measured lint when
    drawing inside a scaled detail view. ``allow_collapse=False`` disables the ``N× v``
    collapse — used when the chain mixes a synthetic head-*block* with real steps, where
    a uniform-staircase representative would be a false claim of N equal steps (#307
    review). Returns the count placed."""
    if not segs:
        return 0
    vb = dwg.view_bounds(view)
    if vb is None:
        return 0
    x0, y0, x1, y1 = vb
    draft = dwg.draft
    gap = draft.font_size + 4 * draft.pad_around_text
    horizontal = abs(segs[0][1][0] - segs[0][0][0]) >= abs(segs[0][1][1] - segs[0][0][1])
    vals = [v for *_, v in segs]
    mean_v = sum(vals) / len(vals)
    if allow_collapse and len(segs) >= 3 and (max(vals) - min(vals)) <= 0.10 * mean_v:
        label = f"{len(segs)}× {_fmt(mean_v)}"
        xs = [p[0] for pa, pb, _ in segs for p in (pa, pb)]
        ys = [p[1] for pa, pb, _ in segs for p in (pa, pb)]
        if horizontal:
            dim = _dim((min(xs), y1, 0), (max(xs), y1, 0), "above", gap, draft, label=label)
        else:
            dim = _dim((x1, min(ys), 0), (x1, max(ys), 0), "right", gap, draft, label=label)
        candidates = [(f"{name_prefix}_typ", dim)]
    else:
        tier_step = draft.font_size + 2 * draft.pad_around_text
        tiers = [0] * len(segs)
        if horizontal:
            cw = [
                ((pa[0] + pb[0]) / 2, len(_fmt(v)) * draft.font_size * 0.62) for pa, pb, v in segs
            ]

            def _clear(items):  # (center, width) pairs in x order — labels don't overlap
                return all(
                    c2 - c1 >= (w1 + w2) / 2 + draft.pad_around_text
                    for (c1, w1), (c2, w2) in zip(items, items[1:])
                )

            if _clear(cw):
                pass  # one tier suffices — no needless zig-zag for a roomy chain
            elif _clear(cw[0::2]) and _clear(cw[1::2]):
                tiers = [i % 2 for i in range(len(segs))]  # alternate to make room
            else:
                _log.info("step-length chain skipped: too dense even when staggered")
                _record_step_chain_drop(
                    dwg, "shoulders too dense to dimension even when staggered"
                )
                return 0
        else:
            shoulder_ys = sorted({c for pa, pb, _ in segs for c in (pa[1], pb[1])})
            if any(b - a < tier_step for a, b in zip(shoulder_ys, shoulder_ys[1:])):
                _log.info("step-length chain skipped: shoulders too close to dimension")
                _record_step_chain_drop(dwg, "turned shoulders too closely spaced to dimension")
                return 0

        candidates = []
        for i, (pa, pb, value) in enumerate(segs):
            if horizontal:
                p1, p2, side = (pa[0], y1, 0), (pb[0], y1, 0), "above"
                dist = gap + tiers[i] * tier_step
            else:
                p1, p2, side = (x1, pa[1], 0), (x1, pb[1], 0), "right"
                dist = gap
            candidates.append(
                (f"{name_prefix}{i}", _dim(p1, p2, side, dist, draft, label=_fmt(value)))
            )

    # Room guard: if any dim would fall off the drawable page, place NONE.
    page = (_MARGIN, _MARGIN, dwg.page_w - _MARGIN, dwg.page_h - _MARGIN)
    for _, dim in candidates:
        box = _anno_box(dim)
        if box is not None and not (
            page[0] <= box[0] and box[2] <= page[2] and page[1] <= box[1] and box[3] <= page[3]
        ):
            _record_step_chain_drop(dwg, "a dimension would fall off the drawable page")
            return 0
    for name, dim in candidates:
        if detail_scale is not None:
            dim._dw_scale = detail_scale
        dwg.add(dim, name, view=view)
    return len(candidates)


def render_step_lengths(dwg, groups) -> int:
    """Unified turned step-length chain (ADR 0008 #223): each `StepFeature`'s length
    span projects into the front view and joins the chain that tiles the turning axis
    so every shoulder is located. X-turned → horizontal chain above the view;
    Z-turned → vertical chain to the right.

    A crowded **X-turned head** — a contiguous run of steps too short to dimension
    legibly even staggered (shoulders below the page arrowhead floor) — is not crammed
    in line: the main view locates that run as one *block* dim and an enlarged
    `DetailRequest` (#304/#307) is queued to break it down. If the detail later can't
    place, the block still locates the head extent and lint reports the un-located
    interior shoulders — never worse than the prior skip. Returns the count placed on
    the front view."""
    rows = []  # (a_world, b_world, value) in axis order
    for g in groups:
        if g.feature_kind != "step":
            continue
        length = next(
            (pd.param for pd in g.dims if pd.param.kind == "length" and pd.param.span is not None),
            None,
        )
        if length is None or length.span is None:
            continue
        rows.append((length.span[0], length.span[1], length.value))
    if not rows:
        return 0
    draft = dwg.draft
    fsegs = [(dwg.at("front", *a), dwg.at("front", *b), v) for a, b, v in rows]
    horizontal = abs(fsegs[0][1][0] - fsegs[0][0][0]) >= abs(fsegs[0][1][1] - fsegs[0][0][1])

    # X-turned crowded-head detour (#307): split off each contiguous *run of ≥2*
    # sub-floor steps (segment narrower than two arrowheads on the page), locate it as
    # a block, and queue an enlarged detail. A single isolated thin step is left in the
    # main chain — a one-step block would just be that step at its sub-floor width
    # (#307 review). The legible steps + blocks stay as the main chain.
    if horizontal:
        floor_pg = 2 * draft.arrow_length
        sub = [i for i, (pa, pb, _) in enumerate(fsegs) if abs(pb[0] - pa[0]) < floor_pg]
        runs: list[list[int]] = []
        for j in sub:
            (runs[-1].append(j) if runs and j == runs[-1][-1] + 1 else runs.append([j]))
        heads = [run for run in runs if len(run) >= 2]
        if heads:
            blocks = []
            for run in heads:
                ra = [rows[i] for i in run]
                hlo = min(min(a[0], b[0]) for a, b, _ in ra)
                hhi = max(max(a[0], b[0]) for a, b, _ in ra)
                minlen = min(v for *_, v in ra)
                # World→page scale for the detail (no sheet factor — detail_scale is an
                # absolute world→page scale). (#307 review)
                scale_needed = _MIN_STEP_SEP_MM / minlen if minlen > 0 else float("inf")
                blocks.append((dwg.at("front", hlo, 0, 0), dwg.at("front", hhi, 0, 0), hhi - hlo))

                def _redraw(dwg, view, detail_scale, _hw=ra):
                    # View-scoped name prefix so two detail views never collide (#307 review).
                    hsegs = [(dwg.at(view, *a), dwg.at(view, *b), v) for a, b, v in _hw]
                    return _draw_step_chain(dwg, view, hsegs, f"dim_{view}_steplen", detail_scale)

                dwg._detail_requests.append(
                    DetailRequest(
                        axis="x",
                        lo=hlo,
                        hi=hhi,
                        scale_needed=scale_needed,
                        redraw=_redraw,
                        pad_top=2 * (draft.font_size + 2 * draft.pad_around_text)
                        + draft.arrow_length,
                        kind="turned-head",
                    )
                )
            head = {i for run in heads for i in run}
            main = [fsegs[i] for i in range(len(fsegs)) if i not in head] + blocks
            main.sort(key=lambda s: s[0][0])
            # The chain now mixes head-block(s) with real steps — never collapse it to a
            # uniform "N× v" representative (a block is not a repeated step, #307 review).
            return _draw_step_chain(dwg, "front", main, "m_steplen", allow_collapse=False)

    return _draw_step_chain(dwg, "front", fsegs, "m_steplen")


def _detect_step_repeat(step_zs, bb_min_z, bb_max_z, tol_frac=0.10):
    """Return (n, rise) if *step_zs* form a uniform staircase, else None.

    A uniform staircase has all inter-step rises (including from bb_min_z to the
    first step) within *tol_frac* of their mean. Requires >=3 detected interior
    steps to avoid false positives. *n* is len(step_zs) + 1 when the top gap
    (bb_max_z - last step) also matches the mean, otherwise len(step_zs).
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


def render_height_ladder(dwg, model, a) -> int:
    """Front-view right ladder: prismatic step heights (from `StepLevelFeature`)
    stacked inner→outer, then the overall height outermost — through `fv_zones.right`,
    preserving the leapfrog witness cursor (#237). Replaces the engine's inline
    `dim_step_*` + `dim_height`. A turned part has no `StepLevelFeature` (its steps are
    the IR length chain); a Z-turned part suppresses the overall height (the chain
    tiles it, ISO 129). Returns the count placed."""
    draft = dwg.draft
    FX, FZ = a.proj.front_x, a.proj.front_z
    right_ladder = FX(a.bb.max.X) + 2
    n = 0
    step = next((f for f in model.features if f.kind == "step_level"), None)
    levels = list(step.levels) if step is not None else []
    # Uniform staircase → one representative "N× rise" dim; else the per-step ladder
    # (legibility-gated). Turned parts have no levels, so neither fires.
    rep = _detect_step_repeat(levels, a.bb.min.Z, a.bb.max.Z) if levels else None
    if rep is not None:
        n_rep, rise = rep
        first = sorted(levels)[0]
        perp = tuple(sorted((FZ(a.bb.min.Z), FZ(first))))
        px = carve_free_position(dwg, a.fv_zones.right, "front", "x", _SLOT_DIM_STEP, perp)
        if px is not None:
            dwg.add(
                _dim(
                    (right_ladder, FZ(a.bb.min.Z), 0),
                    (right_ladder, FZ(first), 0),
                    "right",
                    px - right_ladder,
                    draft,
                    label=f"{n_rep}× {_fmt(rise)}",
                ),
                "dim_step_typ",
                view="front",
            )
            right_ladder = px
            n += 1
        else:
            dwg._record_build_issue(
                "error",
                "placement_unsatisfiable",
                "representative step-height dimension dropped (front-view right strip full)",
            )
    elif levels:
        kept, n_close = _legible_steps(levels, a.bb.min.Z, a.SCALE)
        if n_close:
            dwg._record_build_issue(
                "warning",
                "step_dim_dropped",
                f"{n_close} step height(s) too closely spaced to dimension at this scale "
                "(use a detail view)",
            )
            # First-class escalation alongside the lint code (ADR 0009 Amdt 1, #351
            # PR-4b) — `_request_prismatic_detail` (sections.py) triggers the detail-view
            # remedy on this instead of independently recomputing the same legibility
            # gate, which previously could queue a spurious detail even when the uniform-
            # staircase branch above already fully documented the part with one
            # representative dim (a real bug this routing fixes as a side effect).
            dwg._escalations.append(
                Escalation(kind="step", view="front", feature=step, reason="illegible")
            )
        for col, z in enumerate(kept):
            perp = tuple(sorted((FZ(a.bb.min.Z), FZ(z))))
            px = carve_free_position(dwg, a.fv_zones.right, "front", "x", _SLOT_DIM_STEP, perp)
            if px is None:
                dwg._record_build_issue(
                    "error",
                    "placement_unsatisfiable",
                    f"{len(kept) - col} step-height dimension(s) dropped "
                    "(front-view right strip full)",
                )
                break
            dwg.add(
                _dim(
                    (right_ladder, FZ(a.bb.min.Z), 0),
                    (right_ladder, FZ(z), 0),
                    "right",
                    px - right_ladder,
                    draft,
                    label=_fmt(z - a.bb.min.Z),
                ),
                f"dim_step_{col}",
                view="front",
            )
            right_ladder = px
            n += 1

    # Overall height — placed last so it sits OUTERMOST; suppressed for a Z-turned
    # part (its IR step-length chain already tiles the full height, ISO 129) and for
    # an X/Y rotational body (its Z extent IS the OD, dimensioned by render_rotational
    # — #222).
    rot = next((f for f in model.features if f.kind == "rotational"), None)
    od_is_height = rot is not None and rot.frame.axis in ("x", "y")
    suppress_height = model.orientation == "z" or od_is_height
    px = (
        None
        if suppress_height
        else carve_free_position(
            dwg,
            a.fv_zones.right,
            "front",
            "x",
            _SLOT_DIM_HEIGHT,
            tuple(sorted((FZ(a.bb.min.Z), FZ(a.bb.max.Z)))),
            outermost=True,
        )
    )
    if px is not None:
        dwg.add(
            _dim(
                (right_ladder, FZ(a.bb.min.Z), 0),
                (right_ladder, FZ(a.bb.max.Z), 0),
                "right",
                px - right_ladder,
                draft,
                label=_fmt(a.z_size),
            ),
            "dim_height",
            view="front",
        )
        n += 1
    elif not suppress_height:
        _log.warning("dim_height skipped: fv_zones.right strip full")
    return n


def render_rotational(dwg, model, a) -> int:
    """Rotational furniture from the IR `RotationalFeature` (#237): the OD dim (above
    the front view), the rotation-axis centrelines (front + side), and the concentric
    bore leaders stacked to the left of the front view. Replaces the engine's inline
    OD / centreline / `ldr_z` blocks. Returns the count placed."""
    rot = next((f for f in model.features if f.kind == "rotational"), None)
    if rot is None:
        return 0
    draft = dwg.draft
    FX, FZ = a.proj.front_x, a.proj.front_z
    SX, SZ = a.proj.side_x, a.proj.side_z
    PX, PY = a.proj.plan_x, a.proj.plan_y
    n = 0
    od = rot.od
    axis = rot.frame.axis

    if axis == "z":
        # Vertical turning axis (the common case): OD across the top of the front
        # (profile) view; axis centrelines vertical on front + side.
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
        n += 1
        dwg.add(
            Centerline((FX(a.cx), FZ(a.bb.min.Z) - 5, 0), (FX(a.cx), FZ(a.bb.max.Z) + 5, 0)),
            "centerline_front",
            view="front",
        )
        dwg.add(
            Centerline((SX(a.cy), SZ(a.bb.min.Z) - 5, 0), (SX(a.cy), SZ(a.bb.max.Z) + 5, 0)),
            "centerline_side",
            view="side",
        )

        # Concentric bore leaders to the left of the front view, centred on the axis.
        if rot.bores:
            left_edge = FX(a.bb.min.X)
            if left_edge - a.margin >= a.DIM_PAD:
                elbow_x = left_edge - a.DIM_PAD * 0.6
                nb = len(rot.bores)
                pitch = max(10.0, draft.font_size * 3.0)
                for i, d in enumerate(rot.bores):
                    tip_z = FZ(a.cz) + (i - (nb - 1) / 2) * pitch
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
                    n += 1
            else:
                _log.info(
                    "Additional diameters %s not annotated (insufficient left margin)",
                    list(rot.bores),
                )
    elif axis == "x":
        # Horizontal turning axis along X (#222): the OD is the Z extent — a vertical
        # ø dim left of the front (profile) view; axis centrelines run horizontally
        # through z=cz on front and y=cy on plan.
        dwg.add(
            _dim(
                (FX(a.bb.min.X) - 2, FZ(a.cz - od / 2), 0),
                (FX(a.bb.min.X) - 2, FZ(a.cz + od / 2), 0),
                "left",
                8,
                draft,
                label=f"ø{_fmt(od)}",
            ),
            "dim_od",
            view="front",
        )
        n += 1
        dwg.add(
            Centerline((FX(a.bb.min.X) - 5, FZ(a.cz), 0), (FX(a.bb.max.X) + 5, FZ(a.cz), 0)),
            "centerline_front",
            view="front",
        )
        dwg.add(
            Centerline((PX(a.bb.min.X) - 5, PY(a.cy), 0), (PX(a.bb.max.X) + 5, PY(a.cy), 0)),
            "centerline_plan",
            view="plan",
        )
    elif axis == "y":
        # Horizontal turning axis along Y (#222): the OD is the Z extent — a vertical
        # ø dim left of the side (profile) view; axis centrelines run horizontally
        # through z=cz on side and vertically through x=cx on plan.
        dwg.add(
            _dim(
                (SX(a.bb.min.Y) - 2, SZ(a.cz - od / 2), 0),
                (SX(a.bb.min.Y) - 2, SZ(a.cz + od / 2), 0),
                "left",
                8,
                draft,
                label=f"ø{_fmt(od)}",
            ),
            "dim_od",
            view="side",
        )
        n += 1
        dwg.add(
            Centerline((SX(a.bb.min.Y) - 5, SZ(a.cz), 0), (SX(a.bb.max.Y) + 5, SZ(a.cz), 0)),
            "centerline_side",
            view="side",
        )
        dwg.add(
            Centerline((PX(a.cx), PY(a.bb.min.Y) - 5, 0), (PX(a.cx), PY(a.bb.max.Y) + 5, 0)),
            "centerline_plan",
            view="plan",
        )
    return n


def _record_pmi_drop(dwg, ax, label, rec):
    """Record a PMI dim the layout could not place (#208).

    Previously silent (#351 PR-4a) — a PMI dim that found no strip space just
    vanished with no trace beyond a debug log line, unlike every other placer.
    Now records a warning-severity lint code plus a first-class ``Escalation``
    (ADR 0009 Amdt 1). No resolver remedy yet — purely additive visibility.

    *ax* is ``rec.dominant_axis`` (resolved, never ``"?"`` — see the bore-diameter
    call site). The view table differs by ``rec.pmi_kind``: a bore diameter/radius
    is placed in the view where the bore appears as a circle (Z→plan, X→side,
    Y→front — the bbox-perpendicular view), while a linear dim follows the
    dominant-axis table above (X/Z→front, Y→side primary). Conflating the two
    mislabels every dropped bore diameter/radius (review finding, #351 PR-4a).
    """
    if rec.pmi_kind in ("diameter", "radius"):
        view = {"Z": "plan", "X": "side", "Y": "front"}.get(ax, "front")
    else:
        view = "front" if ax in ("X", "Z") else "side"
    dwg._record_build_issue(
        "warning", "pmi_dropped", f"PMI {label!r} not placed (no room beside the {view})"
    )
    dwg._escalations.append(
        Escalation(kind="pmi", view=view, feature=rec, reason="no room beside the view")
    )


def _bore_half_span(pmi_kind: str, value: float) -> float:
    """Half the perpendicular span of a bore-size dim from the bore centroid — the
    distance to each witness base point. A ``"diameter"`` record stores the full
    diameter (so half = radius = value/2); a ``"radius"`` record already stores the
    radius (half = value). Keyed on ``PmiFeature.pmi_kind`` (the PMI category), NOT
    ``.kind`` (the feature kind, always ``"pmi"``) — the #360 bug used the latter, so
    the diameter branch was dead and every diameter dim spanned ±diameter (2× wide)."""
    return value / 2 if pmi_kind == "diameter" else value


def render_pmi(dwg, model, a) -> int:
    """Render pre-authored PMI annotations (STEP AP242) from the IR `PmiFeature`s
    into remaining strip space (#208). Replaces the engine's `_annotate_pmi`.

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
    draft = dwg.draft
    pmi = [f for f in model.features if f.kind == "pmi"]
    usable = [r for r in pmi if r.value > 0 and len(r.ref_pts) >= 2]
    n_gtol = sum(
        1
        for r in pmi
        if r.pmi_kind
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
        return 0

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
        slot = carve_free_position(dwg, strip, view, "y", _SLOT, tuple(sorted((p1[0], p2[0]))))
        if slot is None or slot <= witness_y:
            return False
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
        slot = carve_free_position(dwg, strip, view, "y", _SLOT, tuple(sorted((p1[0], p2[0]))))
        if slot is None or slot >= witness_y:
            return False
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
        slot = carve_free_position(dwg, strip, view, "x", _SLOT, tuple(sorted((p1[1], p2[1]))))
        if slot is None or slot <= witness_x:
            return False
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
        slot = carve_free_position(dwg, strip, view, "x", _SLOT, tuple(sorted((p1[1], p2[1]))))
        if slot is None or slot >= witness_x:
            return False
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

        if rec.pmi_kind in ("diameter", "radius"):
            # --- Bore size: centroid ± value/2 perpendicular to bore axis ---
            info = _bore_info(rec)
            if info is None:
                _log.debug("PMI dim[%d] diam: no ref_bbox, skip", idx)
                continue
            bore_axis, cx_f, cy_f, cz_f = info
            # Resolved axis (handles the '?' degenerate-bbox fallback _bore_info does
            # internally) — reused below for the drop escalation's view, since the
            # bore-diameter view table (Z→plan, X→side, Y→front) differs from the
            # linear-dim one just below (#351 PR-4a review).
            ax = bore_axis
            half = _bore_half_span(rec.pmi_kind, rec.value)

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
                    slot = carve_free_position(
                        dwg, a.pv_zones.above, "plan", "y", _SLOT, (PX(cx_f), PX(cx_f))
                    )
                    if slot is not None:
                        dwg.add(
                            Leader(tip, (PX(cx_f), slot, 0), label, draft), name_d, view="plan"
                        )
                        placed = True
                    else:
                        slot = carve_free_position(
                            dwg, a.pv_zones.below, "plan", "y", _SLOT, (PX(cx_f), PX(cx_f))
                        )
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
                    slot = carve_free_position(
                        dwg, a.sv_zones.above, "side", "y", _SLOT, (SX(cy_f), SX(cy_f))
                    )
                    if slot is not None:
                        dwg.add(
                            Leader(tip, (SX(cy_f), slot, 0), label, draft), name_d, view="side"
                        )
                        placed = True
                    else:
                        slot = carve_free_position(
                            dwg, a.sv_zones.below, "side", "y", _SLOT, (SX(cy_f), SX(cy_f))
                        )
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
                    slot = carve_free_position(
                        dwg, a.fv_zones.below, "front", "y", _SLOT, (FX(cx_f), FX(cx_f))
                    )
                    if slot is not None:
                        elbow = (FX(cx_f), slot, 0)
                        dwg.add(Leader(tip, elbow, label, draft), name_d, view="front")
                        placed = True
                    else:
                        # Fall back: leader upward into the above strip.
                        slot = carve_free_position(
                            dwg, a.fv_zones.above, "front", "y", _SLOT, (FX(cx_f), FX(cx_f))
                        )
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
            _record_pmi_drop(dwg, ax, label, rec)

    _log.info("PMI annotate: %d/%d dims placed", emitted, len(usable))
    return emitted
