"""Layout-cleanliness invariants for the ADR 0009 strip-layout refactor (#319/#301).

Two *property* guards over the snapshot corpus, pulled forward ahead of the
terminal P5 phase so they protect every output-changing phase (P1b–P4) as it
lands:

1. **Determinism** — ``build_drawing`` is a pure function of its input: two builds
   of the same part produce an identical layout signature. This one is absolute
   and holds today.

2. **No invisible-occupant overlap** — no two *non-crossable* named annotations that
   can share a view's strip space overlap in their FULL rendered geometry (leader
   shafts, witness/extension lines — the footprint a label box hides), the
   `#133/#225/#305` blind-spot class. Scoping mirrors ``strip_obstacles``: same-view
   pairs AND any pair involving a drawing-level (``view_of`` ``None``) occupant — the
   section hatch/arrows a per-view strip solve must still avoid; distinct ortho views
   are disjoint by ADR 0004 and out of scope. This invariant is the *end state* ADR 0009
   converges to, and it is **not yet globally true**: HEAD still has the exact
   defect overlaps P1b/P4 exist to remove. So it is expressed as a *ratchet* — the
   observed overlap set must equal :data:`_KNOWN_OVERLAPS` exactly:

   - a NEW overlap (regression) fails the test, and
   - a REMOVED overlap (a phase cleaned it) also fails, forcing that PR to burn
     down the allowlist — the entry moves out as the fix lands.

   Two categories live in the allowlist (see the inline tags): ``BENIGN`` overlaps
   are permanent and legitimate (two location dimensions off a shared datum share
   their extension-line region — standard ISO practice); ``PENDING`` overlaps are
   the real defects a named phase removes. When the PENDING set empties (post-P4)
   this file's ratchet collapses to the absolute invariant and the throwaway
   snapshot gate can retire (P5).

Crossable annotations (centre lines / marks — :data:`CROSSABLE_TYPES`) are excluded
the way a *dimension* excludes them: a dim may legitimately cross a centreline
(ISO 128). The stricter leader-vs-centreline crossing-free guarantee (#305) is a
P4 property and is asserted there, not here.
"""

from __future__ import annotations

import pytest

# The corpus + determinism fingerprint, now in a shared helper after the byte-exact
# snapshot gate was retired (#319/#641 gap 3): this relational cleanliness invariant is
# part of what replaces it, on every kernel.
from _layout_sig import CORPUS, _signature

from draftwright import build_drawing
from draftwright.annotations._common import CROSSABLE_TYPES

# Overlaps beyond a sliver this small are ignored — matches the within-view label
# lint's 0.5 mm tolerance, so FP-noise slivers on a shared edge don't register.
_TOL_MM = 0.5


# Every non-crossable full-geometry overlap present on HEAD, as {part: {frozenset
# of the two annotation names}}. Three kinds:
#   BENIGN            = permanent (shared-datum witness corridors of a dimension chain).
#   SPACE-CONSTRAINED = a real crossing that placement cannot clear without dropping a
#                       dim; kept under policy B until an outer-layout rescale (ADR 0004).
#   PENDING <issue>   = a real invisible-occupant defect the named phase removes (delete
#                       the entry in that PR).
_KNOWN_OVERLAPS: dict[str, set[frozenset[str]]] = {
    # bracket: the two SPACE-CONSTRAINED central-row crossings (policy B, user
    # 2026-07-02): hc_plan0's wide label straddles the thin section arrow on the
    # plan centre row, and hc_plan1's DIAGONAL leader shaft's AABB clips the same
    # arrow by <1 mm — the placer verifies the diagonal precisely
    # (_segment_hits_box), so the flag is AABB-of-diagonal conservatism, not ink
    # contact. Every datum pair burned down with L-shaped occupancy (#685).
    "bracket": {
        frozenset({"hc_plan0", "section_arrow_right"}),
        frozenset({"hc_plan1", "section_arrow_right"}),
    },
    # dshape PENDING #636: the Z-location dim's line passes through dim_height's
    # label — dim_height is placed by the height-ladder CARVE, invisible to the
    # corridor solve, so the solve cannot space the pair. Burns down when the
    # ladder joins the solve (#636).
    "dshape": {frozenset({"dim_height", "dim_loc_front_z400"})},
}


def _geom_box(o):
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception:
        return None


def _overlaps(a, b, tol):
    return (min(a[2], b[2]) - max(a[0], b[0]) > tol) and (min(a[3], b[3]) - max(a[1], b[1]) > tol)


def _observed_overlaps(dwg) -> set[frozenset[str]]:
    """Every non-crossable full-geometry overlap between named annotations that can
    share a view's strip space.

    View scoping mirrors :func:`strip_obstacles`: a drawing-level occupant
    (``view_of`` is ``None`` — section hatch/arrows, title block) is present in
    EVERY per-view query, so it is compared against every view; two *different*
    ortho views are kept disjoint by compose-then-pack (ADR 0004) so are not
    compared here (that cross-view class is ADR 0004's concern, not ADR 0009's).
    Named annotations only — like ``strip_obstacles``, which iterates
    ``iter_annotations`` (unnamed items contribute to the determinism ``item_count``
    but carry no position guard; production places none through the strip stage)."""

    # Decomposed occupancy (#685): an annotation with `.segments` (helpers ≥0.14
    # reports the drawn line pieces) is a set of stroke boxes + its label box, not
    # one hull — a dimension's empty L-corners no longer count as overlap. The
    # local mirror of annotations/_common.occupancy_boxes (the private-import
    # ratchet keeps tests off annotations/ internals); hull fallback otherwise.
    def _boxes(o):
        # (box, direction) pairs: direction is the stroke's unit vector, or None for
        # a label / hull-fallback box. A TRANSVERSE stroke-stroke crossing (>=30 deg
        # between directions) is a legitimate ISO 129-1 crossing (an outer dim's
        # witness passes through inner tiers) and does not count; near-parallel
        # stroke overlap (collinear overprint) and any label/hull involvement do
        # (#688 review - the blanket stroke exemption hid overprints).
        import math as _m

        segs = getattr(o, "segments", None)
        if not segs:
            b = _geom_box(o)
            return [(b, None)] if b is not None else []
        # Mirror production (strip_obstacles): the pad scales with the preset's
        # arrow geometry, so the oracle exercises the same occupancy model.
        al = getattr(getattr(dwg, "draft", None), "arrow_length", None)
        pad = max(1.2, al / 2) if al else 1.2
        out = []
        for (x0, y0), (x1, y1) in segs:
            ln = _m.hypot(x1 - x0, y1 - y0) or 1.0
            out.append(
                (
                    (min(x0, x1) - pad, min(y0, y1) - pad, max(x0, x1) + pad, max(y0, y1) + pad),
                    ((x1 - x0) / ln, (y1 - y0) / ln),
                )
            )
        lb = getattr(o, "label_bbox", None)
        if lb is not None:
            out.append(((lb[0], lb[1], lb[2], lb[3]), None))
        return out

    def _benign_crossing(d1, d2):
        if d1 is None or d2 is None:
            return False
        cross = abs(d1[0] * d2[1] - d1[1] * d2[0])  # |sin| of the angle between
        return cross >= 0.5  # >=30 deg: a transverse crossing, not an overprint

    named = [
        (name, _boxes(o), type(o).__name__, dwg.view_of(name))
        for name, o in dwg.iter_annotations()
    ]
    hits: set[frozenset[str]] = set()
    for i in range(len(named)):
        n1, bs1, t1, v1 = named[i]
        if not bs1 or t1 in CROSSABLE_TYPES:
            continue
        for j in range(i + 1, len(named)):
            n2, bs2, t2, v2 = named[j]
            if not bs2 or t2 in CROSSABLE_TYPES:
                continue
            if not (v1 == v2 or v1 is None or v2 is None):
                continue  # two distinct ortho views → disjoint blocks (ADR 0004)
            if any(
                _overlaps(b1, b2, _TOL_MM) and not (s1 and s2) for b1, s1 in bs1 for b2, s2 in bs2
            ):
                hits.add(frozenset({n1, n2}))
    return hits


@pytest.mark.parametrize("name", list(CORPUS))
def test_build_is_deterministic(name):
    # build_drawing is a pure function of its input — no Date.now/random/hash-order
    # leakage into placement. Guards every output-changing phase against a
    # non-reproducible layout that the snapshot gate (single build) can't catch.
    a = _signature(build_drawing(CORPUS[name]()))
    b = _signature(build_drawing(CORPUS[name]()))
    assert a == b, f"{name!r}: two builds produced different layouts"


@pytest.mark.parametrize("name", list(CORPUS))
def test_no_invisible_occupant_overlap(name):
    # Ratchet: the observed non-crossable overlap set must equal the known set. A new
    # overlap = regression; a vanished one = a phase cleaned it → burn down
    # _KNOWN_OVERLAPS in that PR (the goal is an empty PENDING set post-P4).
    observed = _observed_overlaps(build_drawing(CORPUS[name]()))
    known = _KNOWN_OVERLAPS.get(name, set())
    new = observed - known
    gone = known - observed
    assert not new, f"{name!r}: NEW invisible-occupant overlap(s) {new} — regression"
    assert not gone, (
        f"{name!r}: known overlap(s) {gone} no longer present — a phase cleaned them; "
        f"remove the entry from _KNOWN_OVERLAPS in this PR"
    )
