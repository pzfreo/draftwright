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

from build123d_drafting.helpers import CenterMark, Dimension, HoleCallout, Leader, TitleBlock

from draftwright._core import (
    _DIAM_RE,
    _END_ON,
    _MARGIN,
    _SLOT_DIM_DEPTH,
    _SLOT_DIM_WIDTH,
    _dim,
    _fmt,
    _greedy_strip_ys,
    _solve_strip_ys,
)
from draftwright.annotations._common import _anno_box, _box_hits, _occupied_boxes
from draftwright.model.planner import DimensionGroup, plan_dimensions

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


def _hole_callout(dwg, group) -> HoleCallout | None:
    """The `HoleCallout` for a hole/pattern group from its planned spec, or ``None``
    if the group is not hole-bearing. The shared callout builder for both the placed
    (`render_into`) and the bare (`render_callouts`) paths."""
    spec = hole_callout_spec(group)
    if spec is None:
        return None
    return HoleCallout(
        spec["diameter"],
        count=spec["count"],
        through=spec["through"],
        depth=spec["depth"],
        cbore_dia=spec["cbore_dia"],
        cbore_depth=spec["cbore_depth"],
        suffix=spec["suffix"],
        draft=dwg.draft,
    )


def _place_leader(dwg, view, tip_model, obstacles, *, label="", callout=None) -> Leader | None:
    """A leader (callout or plain ø label) placed clear of *obstacles* by searching
    outward from the feature (ADR 0003 layout): first non-colliding elbow wins; the
    farthest candidate is the fallback. With no obstacles the first ring wins, so a
    bare call is deterministic."""
    tx, ty, *_ = dwg.at(view, *tip_model)
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
        if not _box_hits(box, obstacles):
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
    for ann in dwg._named.values():
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


def _env_param(group, role):
    """The EnvelopeFeature DimParameter with the given role (width/depth/height)."""
    return next((pd.param for pd in group.dims if pd.param.role == role), None)


def render_envelope(dwg, model, a, *, suppress_width: bool = False) -> int:
    """Overall width (plan, below) + depth (side, below) envelope dims via the IR,
    placed through the engine's below-strip zone allocators (the zone-aware render
    stage — so a migrated dim still coordinates with the un-migrated passes sharing
    those strips). Sources values/spans from `EnvelopeFeature`. Skips a square
    footprint (a single dim suffices) and, when *suppress_width*, the width (an
    X-turned part's step chain already conveys the length, ISO 129). Returns the
    count placed."""
    env = next((g for g in plan_dimensions(model) if g.feature_kind == "envelope"), None)
    if env is None:
        return 0
    width, depth = _env_param(env, "width"), _env_param(env, "depth")
    if width is None or depth is None:
        return 0
    # Square footprint: width ≈ depth → one dim suffices (the engine's gate).
    if abs(width.value - depth.value) <= max(width.value, depth.value) * 0.05:
        return 0
    n = 0
    if not suppress_width and width.span is not None:
        (x0, y0, z0), (x1, _, _) = width.span
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
                    label=_fmt(width.value),
                ),
                "m_env_width",
                view="plan",
            )
            n += 1
    if depth.span is not None:
        (x0, y0, z0), (_, y1, _) = depth.span
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
                    label=_fmt(depth.value),
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
    """The end-to-end seam: plan *model* and **add** its annotations to *dwg*
    (which must already have its views, e.g. ``build_drawing(part, auto_dims=False)``).
    Diameter callouts (holes/patterns/bosses) are placed clear of the views and of
    each other (ADR-0003 layout); overall envelope dims sit just outside their view.
    Returns the count added; lint *dwg* to judge correctness. Turned stepped parts
    remain the engine's domain (out-grow, not reproduce — ADR 0008 Amendment 2)."""
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
