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

from draftwright.model.planner import DimensionGroup


def _value(group: DimensionGroup, kind: str, role: str) -> float | None:
    for pd in group.dims:
        if pd.param.kind == kind and pd.param.role == role:
            return pd.param.value
    return None


def hole_callout_spec(group: DimensionGroup) -> dict | None:
    """A hole/pattern group's planned params → `HoleCallout` kwargs (the renderer's
    reading of the plan). ``None`` if the group is not a hole-bearing callout.

    Everything comes from the plan: the bore/counterbore values from
    `DimParameter` roles, ``through`` inferred from the absence of a bore-depth
    param, and ``count`` from the source feature (so a 6-hole bolt circle is one
    ``6× ø6`` callout)."""
    if group.feature_kind not in ("hole", "pattern"):
        return None
    bore = _value(group, "diameter", "bore")
    if bore is None:
        return None
    depth = _value(group, "depth", "bore")
    count = getattr(group.feature, "count", 1)
    return {
        "diameter": bore,
        "count": count if count and count > 1 else None,
        "through": depth is None,
        "depth": depth,
        "cbore_dia": _value(group, "diameter", "counterbore"),
        "cbore_depth": _value(group, "depth", "counterbore"),
    }


def render_callouts(dwg, groups) -> list[Leader]:
    """Build placed `HoleCallout` leaders for the hole/pattern groups, projecting
    each group's anchor into its view. Returns the annotations; does not mutate
    *dwg* (the swap-in that adds them to the drawing is #201)."""
    out: list[Leader] = []
    for g in groups:
        spec = hole_callout_spec(g)
        if spec is None:
            continue
        callout = HoleCallout(
            spec["diameter"],
            count=spec["count"],
            through=spec["through"],
            depth=spec["depth"],
            cbore_dia=spec["cbore_dia"],
            cbore_depth=spec["cbore_depth"],
            draft=dwg.draft,
        )
        tip = dwg.at(g.view, *g.anchor)
        elbow = (tip[0] + 12.0, tip[1] + 8.0, 0)
        out.append(
            Leader(
                tip=(tip[0], tip[1], 0), elbow=elbow, label="", draft=dwg.draft, callout=callout
            )
        )
    return out
