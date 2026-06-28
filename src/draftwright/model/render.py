"""render — the renderer seam: DimensionGroup → placed annotations (ADR 0008).

The **first real consumer** of the planner output, validating the IR/planner
contract end-to-end. A hole/pattern group is read *purely from its planned
parameters* (+ the feature's `count`) and turned into a placed `HoleCallout`
leader via the existing projection (`Drawing.at`) and rendering primitives. GD&T
symbols (⌴/↧) are the helper's geometry, which is exactly why the IR carries
semantic `role`s, not glyph strings.

This is the planner→layout seam. It does **not** yet replace the engine's hole
placement — that swap-in, under the migration gate, is #201. Here it proves the
contract carries what a renderer needs, and surfaces gaps before wiring. Kept out
of `draftwright.model.__init__` so the pure IR stays free of any helpers import.
"""

from __future__ import annotations

from build123d_drafting.helpers import HoleCallout, Leader

from draftwright._core import _fmt
from draftwright.model.planner import DimensionGroup, plan_dimensions


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


def render_into(dwg, model) -> int:
    """The end-to-end seam: plan *model* and **add** its annotations to *dwg*
    (which must already have its views, e.g. ``build_drawing(part, auto_dims=False)``).
    Returns the count added. Hole/pattern callouts today; other feature kinds are
    added as the framework out-grows the engine. Lint *dwg* to judge correctness."""
    n = 0
    for g in plan_dimensions(model):
        leader = _callout_leader(dwg, g)
        if leader is not None:
            dwg.add(leader, f"m_callout{n}", view=g.view)
            n += 1
    return n
