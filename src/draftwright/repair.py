"""The deterministic lint→repair safety net (#138 / ADR 0005; #30 / ADR 0002).

The solver path now owns annotation placement. Repair is deliberately narrow:
it only handles the mechanically-clear wrong-side dimension case and never performs
fixed-step overlap placement. `Drawing.repair()` remains the public wrapper; these
helpers take the drawing as `dwg` (duck-typed — `lint` / `items` / `_registry`), so
this module imports only `_core`, never `make_drawing` — no cycle.
"""

from __future__ import annotations

from draftwright._core import _QUOTED_RE, _dim

# Lint codes the repair loop can mechanically resolve, and the side flip used to
# move a dimension that landed on the wrong side of its witness points.
_REPAIRABLE_CODES = frozenset({"dim_inside_part"})
_OPPOSITE_SIDE = {"above": "below", "below": "above", "left": "right", "right": "left"}


def _find_dim(dwg, label):
    """Return the re-placeable dimension whose label is *label*, or None.

    Only dimensions built by :func:`_dim` (carrying ``_dw_spec``) qualify;
    leaders, callouts and hand-built annotations are left untouched. A pinned
    dimension (#89) is also skipped — a deliberate placement must win over
    automatic repair.
    """
    # Identity-based, matching clear_annotations: "this specific object", not
    # build123d's geometric Shape equality.
    pinned_ids = dwg._registry.pinned_object_ids()
    for o in dwg.items:
        if id(o) in pinned_ids:
            continue
        if getattr(o, "_dw_spec", None) is not None and getattr(o, "label", None) == label:
            return o
    return None


def _replace_dim(dwg, old, new):
    """Swap *old* for *new* in ``dwg.items``, preserving its name and any per-view
    scale tag (so a re-placed detail-view dim stays at scale)."""
    if getattr(old, "_dw_scale", None) is not None:
        new._dw_scale = old._dw_scale
    dwg.items[dwg.items.index(old)] = new
    dwg._registry.replace_object(old, new)


def _repair_dim_inside_part(dwg, issue) -> bool:
    """Flip a dimension that sits inside the view onto the opposite side."""
    labels = _QUOTED_RE.findall(issue.message)
    dim = _find_dim(dwg, labels[0]) if labels else None
    if dim is None:
        return False
    s = dim._dw_spec
    new_side = _OPPOSITE_SIDE.get(s.side)
    if new_side is None:
        return False
    _replace_dim(dwg, dim, _dim(s.p1, s.p2, new_side, s.distance, s.draft, **s.kwargs))
    return True


def repair_drawing(dwg, max_iter: int = 3):
    """Close the lint→repair loop; see :meth:`Drawing.repair` for the contract.
    Returns *dwg* for chaining."""
    flipped: set = set()
    for _ in range(max_iter):
        before = dwg.lint()
        if not before:
            break
        snap_annotations = list(dwg.items)
        snap_registry = dwg._registry.snapshot()
        changed = False
        for issue in before:
            if issue.code not in _REPAIRABLE_CODES:
                continue
            if issue.code == "dim_inside_part":
                labels = _QUOTED_RE.findall(issue.message)
                key = labels[0] if labels else None
                if key in flipped:
                    continue
                if _repair_dim_inside_part(dwg, issue):
                    flipped.add(key)
                    changed = True
        if not changed:
            break
        if len(dwg.lint()) > len(before):
            # The repairs net-worsened the sheet — undo this pass and stop.
            dwg.items[:] = snap_annotations
            dwg._registry.restore(snap_registry)
            break
    return dwg
