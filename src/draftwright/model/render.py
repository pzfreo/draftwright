"""render — the renderer seam: DimensionGroup → placed annotations (ADR 0008).

The **first real consumer** of the planner output, validating the IR/planner
contract end-to-end. A hole/pattern group is read *purely from its planned
parameters* (+ the feature's `count`) and turned into a placed `HoleCallout`
leader via the existing projection (`Drawing.at`) and rendering primitives. GD&T
symbols (⌴/↧) are the helper's geometry, which is exactly why the IR carries
semantic `role`s, not glyph strings.

This is the planner→layout seam: `render_into` places each callout clear of the
views and of other callouts via the ADR-0003 layout search. The pipeline runs
end-to-end and is judged by **correctness** (lint), per the out-grow strategy
(ADR 0008 Amendment 2) — it is the path for new/poorly-handled shapes, not a
reproduce-and-swap of the engine. Kept out of `draftwright.model.__init__` so the
pure IR stays free of any helpers/annotations import.
"""

from __future__ import annotations

from build123d_drafting.helpers import HoleCallout, Leader

from draftwright._core import _fmt
from draftwright.annotations._common import _anno_box, _box_hits
from draftwright.model.planner import DimensionGroup, plan_dimensions

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


def _callout_leader(dwg, group) -> Leader | None:
    """A placed `HoleCallout` leader for a hole/pattern group, or ``None``. Tips at
    a real member hole (not the empty pattern centre), projected into the group's
    view."""
    spec = hole_callout_spec(group)
    if spec is None:
        return None
    callout = HoleCallout(
        spec["diameter"],
        count=spec["count"],
        through=spec["through"],
        depth=spec["depth"],
        cbore_dia=spec["cbore_dia"],
        cbore_depth=spec["cbore_depth"],
        suffix=spec["suffix"],
        draft=dwg.draft,
    )
    members = getattr(group.feature, "members", ())
    tip_model = members[0] if members else group.anchor
    tip = dwg.at(group.view, *tip_model)
    elbow = (tip[0] + 12.0, tip[1] + 8.0, 0)
    return Leader(tip=(tip[0], tip[1], 0), elbow=elbow, label="", draft=dwg.draft, callout=callout)


def render_callouts(dwg, groups) -> list[Leader]:
    """The hole/pattern callout leaders for *groups* (does not mutate *dwg*)."""
    return [ldr for g in groups if (ldr := _callout_leader(dwg, g)) is not None]


def _placed_callout_leader(dwg, group, obstacles) -> Leader | None:
    """A `HoleCallout` leader placed clear of *obstacles* by searching outward from
    the hole (ADR 0003 layout): the first elbow whose leader box hits nothing wins;
    the farthest candidate is the fallback."""
    spec = hole_callout_spec(group)
    if spec is None:
        return None
    callout = HoleCallout(
        spec["diameter"],
        count=spec["count"],
        through=spec["through"],
        depth=spec["depth"],
        cbore_dia=spec["cbore_dia"],
        cbore_depth=spec["cbore_depth"],
        suffix=spec["suffix"],
        draft=dwg.draft,
    )
    members = getattr(group.feature, "members", ())
    tip_model = members[0] if members else group.anchor
    tx, ty, *_ = dwg.at(group.view, *tip_model)
    fallback = None
    for dx, dy in _ELBOW_OFFSETS:
        leader = Leader(
            tip=(tx, ty, 0),
            elbow=(tx + dx, ty + dy, 0),
            label="",
            draft=dwg.draft,
            callout=callout,
        )
        box = _anno_box(leader)
        if box is None:
            return leader  # can't measure → accept
        if not _box_hits(box, obstacles):
            return leader
        fallback = leader
    return fallback


def render_into(dwg, model) -> int:
    """The end-to-end seam: plan *model* and **add** its annotations to *dwg*
    (which must already have its views, e.g. ``build_drawing(part, auto_dims=False)``).
    Each callout is placed clear of the views and of callouts already added (the
    layout solver, not fixed offsets). Returns the count added. Hole/pattern
    callouts today; other feature kinds follow as the framework out-grows the
    engine. Lint *dwg* to judge correctness."""
    view_boxes = [vb for v in dwg.views if (vb := dwg.view_bounds(v)) is not None]
    placed: list = []
    n = 0
    for g in plan_dimensions(model):
        leader = _placed_callout_leader(dwg, g, view_boxes + placed)
        if leader is None:
            continue
        dwg.add(leader, f"m_callout{n}", view=g.view)
        n += 1
        box = _anno_box(leader)
        if box is not None:
            placed.append(box)
    return n
