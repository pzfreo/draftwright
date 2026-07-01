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

# Shared with the throwaway snapshot gate (test_layout_snapshot); both retire
# together at P5 (#319) — do not delete that file before this one.
from test_layout_snapshot import CORPUS, _signature

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
    # BENIGN: two location dims off a common datum share their extension-line span.
    "plate_holes": {frozenset({"m_locx0", "m_locx1"}), frozenset({"m_locy0", "m_locy1"})},
    # bracket: two BENIGN datum pairs (as plate_holes) PLUS one PENDING defect —
    # PENDING (P3/P4): a plan-view hole-callout leader runs under the drawing-level
    # section cutting-plane arrow (a view=None occupant). An invisible-occupant
    # overlap that clears once this callout's placer consults the full strip_obstacles.
    "bracket": {
        frozenset({"m_locx0", "m_locx1"}),
        frozenset({"m_locy0", "m_locy1"}),
        frozenset({"hc_plan0", "section_arrow_right"}),
    },
    # side_drilled: one BENIGN datum overlap PLUS one PENDING defect.
    #  - BENIGN {dim_loc_side_y2000, m_env_depth}: the envelope depth (dy→y1) and the
    #    location (dy→hole_y) are a dimension chain off the common `dy` datum — they
    #    share the view-edge witness corridor, exactly like the datum pairs above.
    #    The cursor→carve envelope migration (#321) moves the depth dim to its
    #    box-consistent tier but the shared witness corridor persists — structural,
    #    not a placer defect. (Was mislabelled PENDING before the migration measured it.)
    #  - SPACE-CONSTRAINED {hc_side0, dim_loc_side_z2300}: the bore-callout leader
    #    crosses the Z location dim's witness corridor. The #321 P1b corridor-aware pass
    #    rejects the side strip and tries to RELOCATE the dim to the front view — but for
    #    this part the front-right slot (≈10.7 mm between dim_height and the side view) is
    #    too narrow for a tier (≈11 mm). With no roomy alternate, policy B KEEPS the dim
    #    on its natural view (never drop a real dimension) and accepts the same-feature
    #    crossing. Not a placer blind spot — a tight-packing constraint that only an
    #    outer-layout rescale (ADR 0004) or a roomier part would let the pass clear.
    "side_drilled": {
        frozenset({"hc_side0", "dim_loc_side_z2300"}),
        frozenset({"dim_loc_side_y2000", "m_env_depth"}),
    },
    # BENIGN (as side_drilled): envelope depth + location share the datum witness corridor.
    "dshape": {frozenset({"dim_loc_side_y200", "m_env_depth"})},
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
    named = [
        (name, _geom_box(o), type(o).__name__, dwg.view_of(name))
        for name, o in dwg.iter_annotations()
    ]
    hits: set[frozenset[str]] = set()
    for i in range(len(named)):
        n1, b1, t1, v1 = named[i]
        if b1 is None or t1 in CROSSABLE_TYPES:
            continue
        for j in range(i + 1, len(named)):
            n2, b2, t2, v2 = named[j]
            if b2 is None or t2 in CROSSABLE_TYPES:
                continue
            if not (v1 == v2 or v1 is None or v2 is None):
                continue  # two distinct ortho views → disjoint blocks (ADR 0004)
            if _overlaps(b1, b2, _TOL_MM):
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
