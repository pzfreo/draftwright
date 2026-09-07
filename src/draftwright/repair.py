"""The deterministic lint→repair safety net (#138 / ADR 1 (was 0005); #30 / ADR 5 (was 0002)).

The solver path owns annotation placement. Repair handles wrong-side dimensions
and a bounded dimension-ink candidate supplied by that same solver. Drawing
supplies the higher-ranked candidate function; this module never imports it.
"""

from __future__ import annotations

from collections import Counter

from draftwright._core import _QUOTED_RE, _dim
from draftwright.audit import compare_measurements

# Lint codes the repair loop can mechanically resolve, and the side flip used to
# move a dimension that landed on the wrong side of its witness points.
_REPAIRABLE_CODES = frozenset({"dim_inside_part", "annotation_ink_overlap"})
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


def _swap_annotation(dwg, old, new):
    dwg.items[next(i for i, item in enumerate(dwg.items) if item is old)] = new
    dwg.registry.replace_object(old, new)


def _replace_dim(dwg, old, new):
    """Swap *old* for *new* in ``dwg.items``, preserving its name and any per-view
    scale tag (so a re-placed detail-view dim stays at scale)."""
    if getattr(old, "_dw_scale", None) is not None:
        new._dw_scale = old._dw_scale
    # And the per-unit meaning of an `N× v` label (#1153). `repair()` runs on every build,
    # and a tagged `dim_step_typ` is a legal `dim_inside_part` target — so dropping this on
    # a rebuild turns a correct drawing into a FAILING one, because lint then reads the
    # label as a span and reports a material contradiction. Its own docstring called this
    # "the same seam `_dw_scale` uses"; that was only true once it was carried here too.
    if getattr(old, "_dw_label_value", None) is not None:
        new._dw_label_value = old._dw_label_value
    if getattr(old, "_dw_measurement_span", None) is not None:
        new._dw_measurement_span = old._dw_measurement_span
    if getattr(old, "_dw_authored_side", None) is not None:
        new._dw_authored_side = old._dw_authored_side
    _swap_annotation(dwg, old, new)


def _repair_dim_inside_part(dwg, issue) -> bool:
    """Flip a dimension that sits inside the view onto the opposite side."""
    labels = _QUOTED_RE.findall(issue.message)
    dim = _find_dim(dwg, labels[0]) if labels else None
    if dim is None:
        return False
    # A side override is an authored constraint, including after a same-side label
    # reconciliation rebuild. Leave the diagnosis visible instead of flipping it.
    if getattr(dim, "_dw_authored_side", None) is not None:
        return False
    s = dim._dw_spec
    new_side = _OPPOSITE_SIDE.get(s.side)
    if new_side is None:
        return False
    _replace_dim(dwg, dim, _dim(s.p1, s.p2, new_side, s.distance, s.draft, **s.kwargs))
    return True


def _repair_annotation_ink(dwg, choose_candidates, before):
    """Try one shared-solver batch; commit only a content-preserving improvement."""
    if "annotation_ink_overlap" not in _REPAIRABLE_CODES or not any(
        issue.code == "annotation_ink_overlap" for issue in before
    ):
        return
    original = list(dwg.iter_annotations())
    pins = dwg.registry.pinned_names()
    measurements = dwg.measurement_snapshot()
    if measurements.unknown:
        return  # an unconfirmed measurement cannot authorise an automatic move
    candidates = choose_candidates(original, pins)
    if [name for name, _ in candidates] != [name for name, _ in original]:
        return
    changes = [
        (old, new)
        for (_, old), (_, new) in zip(original, candidates, strict=True)
        if old is not new
    ]
    if not changes:
        return
    for (name, old), (_, new) in zip(original, candidates, strict=True):
        if old is new:
            continue
        a, b = getattr(old, "_dw_spec", None), getattr(new, "_dw_spec", None)
        if (
            name in pins
            or a is None
            or b is None
            or (a.p1, a.p2, a.side) != (b.p1, b.p2, b.side)
            or getattr(old, "_dw_authored_side", None) != getattr(new, "_dw_authored_side", None)
        ):
            return
    items = list(dwg.items)
    registry = dwg.registry.snapshot()
    accepted = False
    try:
        for old, new in changes:
            # The shared solver already carries provenance. Judge its candidate
            # as returned, without overwriting evidence that the comparison reads.
            _swap_annotation(dwg, old, new)
        after = dwg.lint(physical=False)
        before_counts = Counter((issue.code, issue.severity) for issue in before)
        after_counts = Counter((issue.code, issue.severity) for issue in after)
        accepted = (
            bool(before_counts - after_counts)
            and not (after_counts - before_counts)
            and compare_measurements(measurements, dwg)["status"] == "preserved"
        )
    finally:
        if not accepted:
            dwg.items[:] = items
            dwg.registry.restore(registry)


def repair_drawing(dwg, max_iter: int = 3, *, ink_candidates=None):
    """Close the lint→repair loop; see :meth:`Drawing.repair` for the contract.
    Returns *dwg* for chaining.

    Lints ``physical=False`` — the placement critique only. This loop acts on
    ``_REPAIRABLE_CODES`` and nothing else, so the feature-coverage
    half was computed and discarded on every iteration; on a declared build it also forced
    the recognition ADR 4 (was 0011) says that path skips (#1022). It makes the net-worsened
    comparison below stricter too: coverage issues cannot change from re-placing a
    dimension, so counting them only diluted the ratio.
    """
    if max_iter <= 0:
        return dwg
    before = dwg.lint(physical=False)
    flipped: set = set()
    for _ in range(max_iter):
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
        after = dwg.lint(physical=False)
        if len(after) > len(before):
            # Preserve the wrong-side handler's existing rollback contract. Ink
            # candidates below use the stricter per-code/severity comparison.
            dwg.items[:] = snap_annotations
            dwg.registry.restore(snap_registry)
            break
        before = after
    if ink_candidates is not None:
        _repair_annotation_ink(dwg, ink_candidates, before)
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
