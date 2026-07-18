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
    pinned_ids = dwg.registry.pinned_object_ids()
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
    dwg.registry.replace_object(old, new)


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
        snap_registry = dwg.registry.snapshot()
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
            dwg.registry.restore(snap_registry)
            break
    return dwg


def reconcile_witness_labels(dwg) -> int:
    """Shift a dimension's label along its own line when ANOTHER dimension's
    stroke crosses it (#690) — the perpendicular-axis conflict class tier
    co-solving cannot fix (a location dim's witness must cross the whole strip
    to reach its tier; any inner label at that height gets crossed wherever
    the tiers land — the dshape ``dim_height`` case).

    Runs as a deterministic late pipeline pass (both build paths call it after
    every corridor has drained), using the repair machinery: offenders rebuild
    once via :func:`_replace_dim` with the minimal clearing ``label_offset_x``.
    Detection mirrors the cleanliness ratchet's decomposed model (#685): a
    foreign drawn stroke (helpers ``.segments``) TRANSVERSE to the label's own
    dim line, crossing its ``label_bbox`` by >0.5 mm on both axes. Parallel
    strokes are the legitimate stacked-shaft pattern and never count. The shift
    is clamped to the dimension's own span (a label pushed past its witness
    ends reads as the neighbour's); an unshiftable label is left where it is —
    unchanged output, and lint reports it exactly as before. Returns the count
    shifted."""

    def _free_segments(lo, hi, blocked):
        # Local minimal interval-subtraction (repair sits below annotations/ in the
        # DAG, so it cannot import the corridor carve; ~the same ten lines).
        segs = [(lo, hi)]
        for b_lo, b_hi in sorted(blocked):
            nxt = []
            for s_lo, s_hi in segs:
                if b_hi <= s_lo or b_lo >= s_hi:
                    nxt.append((s_lo, s_hi))
                    continue
                if b_lo > s_lo:
                    nxt.append((s_lo, b_lo))
                if b_hi < s_hi:
                    nxt.append((b_hi, s_hi))
            segs = nxt
        return segs

    pad = 1.0  # keep-clear each side of a crossing stroke
    pinned_ids = dwg.registry.pinned_object_ids()  # a pin is deliberate — never moved (#693 r1)
    dims = [
        (name, o)
        for name, o in dwg.iter_annotations()
        if getattr(o, "_dw_spec", None) is not None
        and getattr(o, "label_bbox", None) is not None
        and id(o) not in pinned_ids
    ]
    shifted = 0
    for name, dim in dims:
        s = dim._dw_spec
        lb = dim.label_bbox
        dx, dy = s.p2[0] - s.p1[0], s.p2[1] - s.p1[1]
        if min(abs(dx), abs(dy)) > 0.1:
            # A diagonal dim's label_offset_x moves BOTH page coordinates — the
            # axis-aligned solve below cannot describe it (#693 r2). Skip, same
            # tolerance as the stroke rule; the diagonal pitch fallback already
            # places with its own clearance search.
            continue
        vertical = abs(dy) > abs(dx)  # the label travels along the dim line
        ax = 1 if vertical else 0  # page axis the label moves along
        span_lo, span_hi = sorted((s.p1[ax], s.p2[ax]))
        mid = (lb[ax] + lb[ax + 2]) / 2.0
        half = (lb[ax + 2] - lb[ax]) / 2.0
        # Threats = EVERY axis-aligned transverse stroke whose fixed-axis extent
        # reaches the label's band, across the WHOLE span — not just those crossing
        # the label's current position (#693 r1: a shift must not land ON another
        # witness further along the line). Diagonal strokes (leader shafts) are
        # skipped: a single travel coordinate does not describe them, and moving a
        # label off an AABB-midpoint guess produced false positives; they were
        # never this pass's target class.
        oth = 1 - ax
        threats = []
        for other, oo in dwg.iter_annotations():
            if other == name:
                continue
            for seg in getattr(oo, "segments", None) or ():
                (x0, y0), (x1, y1) = seg
                sdx, sdy = abs(x1 - x0), abs(y1 - y0)
                if min(sdx, sdy) > 0.1:  # diagonal — skip (see above)
                    continue
                if (sdy > sdx) == vertical:
                    continue  # parallel = stacked shafts, exempt
                sb = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                if min(sb[oth + 2], lb[oth + 2]) - max(sb[oth], lb[oth]) > 0.5:
                    threats.append((sb[ax] + sb[ax + 2]) / 2.0)
        # Shift only a label that is CURRENTLY crossed; but block every threat as a
        # destination, so the chosen spot cannot trade one crossing for another.
        if not any(lb[ax] + 0.3 < t < lb[ax + 2] - 0.3 for t in threats):
            continue
        segs = _free_segments(
            span_lo, span_hi, [(t - pad - half, t + pad + half) for t in threats]
        )
        best = None
        for g_lo, g_hi in segs:
            if g_hi - g_lo < 2 * half:
                continue
            c = min(max(mid, g_lo + half), g_hi - half)
            if best is None or abs(c - mid) < abs(best - mid):
                best = c
        if best is None or abs(best - mid) <= 0.05:
            continue
        off = best - mid
        kwargs = dict(s.kwargs)
        kwargs["label_offset_x"] = kwargs.get("label_offset_x", 0.0) + off
        _replace_dim(dwg, dim, _dim(s.p1, s.p2, s.side, s.distance, s.draft, **kwargs))
        shifted += 1
    return shifted
