"""Shared annotation-placement helpers (#138 / ADR 0005, P5).

Page-box geometry the passes share: an annotation's bbox (`_anno_box`), the
set of already-placed boxes a candidate must not overprint (`_occupied_boxes`),
and an AABB overlap test (`_box_hits`). Bottom of the annotations DAG.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from build123d_drafting.helpers import Dimension, SafeDimension

from draftwright.layout import StripCandidate, plan_strip
from draftwright.linting.structural import _centerline_extent

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Escalation:
    """A first-class "could not place this here" signal (ADR 0009 Amendment 1, P5-strand-2).

    Placers *collect* one of these into ``dwg._escalations`` at the point of failure —
    instead of recording a stringly-typed ``*_dropped`` lint code and letting the escalators
    grep for it — and one later resolver pass groups them by ``(view, feature-or-pattern)``,
    picks a remedy per group (ISO pattern-grouped balloon / table / detail / drop), and emits
    the ``*_dropped`` lint codes only for what stays unresolved (so coverage lint + the
    cleanliness ratchet keep working). See the ADR / epic #351.

    The hole callout/location placers emit these (#351 PR-2); the resolver in
    ``annotations/orchestrator.py`` (``_maybe_tabulate_holes``) consumes them, including
    the ISO pattern-grouped balloon fallback for a dropped pattern callout (#351 PR-3).

    Attributes:
        kind:     what could not be placed — ``"callout" | "location" | "slot" | "step" | "pmi"``.
        view:     the owning orthographic view (``None`` for drawing-level).
        feature:  reference to the IR feature / ``HoleRef`` / key it belongs to — carries the
                  pattern membership the resolver groups on (a ``"callout"`` escalation's
                  feature is the dropped group's ``PatternFeature`` when it is a
                  fully-surviving recognised pattern, else ``None``). Left untyped to keep
                  this module a leaf (no dependency on ``model.ir``).
        reason:   why placement failed — ``"strip_full" | "illegible" | "corridor_blocked" | "no_room"``.
        remedies: ranked candidate remedies the resolver may pick, e.g.
                  ``("group_balloon", "table", "detail", "drop")``. Empty = resolver's default ladder.
    """

    kind: str
    view: str | None
    feature: object
    reason: str
    remedies: tuple[str, ...] = field(default_factory=tuple)


def _anno_box(o):
    """Page-space bbox ``(x0, y0, x1, y1)`` of an annotation — its text
    ``label_bbox`` if it has one, else its geometric bounding box; ``None`` if
    neither resolves.  Local mirror of ``make_drawing._anno_bbox`` (annotate sits
    below make_drawing, so it cannot import from it)."""
    lb = getattr(o, "label_bbox", None)
    if lb is not None:
        return lb
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception:  # noqa: BLE001 — not every annotation bbox-es cleanly
        return None


def _occupied_boxes(dwg):
    """Boxes of already-placed annotations a location dim must not overprint:
    every label-bearing annotation (hole callouts, other dims) plus the section
    hatch.  Bare centrelines/leaders are excluded — those legitimately cross a
    dimension and lint does not flag them."""
    boxes = []
    for name, o in dwg.iter_annotations():
        if getattr(o, "label_bbox", None) is None and name != "section_hatch":
            continue
        bb = _anno_box(o)
        if bb is not None:
            boxes.append(bb)
    return boxes


def _geom_box(o):
    """Full rendered-geometry bbox ``(x0, y0, x1, y1)`` of an annotation — leader
    shafts and arrow tips, dimension witness/extension lines, centrelines, hatch —
    *not* just its label box. ``None`` if it does not bbox cleanly (logged at
    debug: a silently dropped occupant is the wrong failure mode for an occupancy
    model, so the omission is at least observable)."""
    try:
        b = o.bounding_box()
        return (b.min.X, b.min.Y, b.max.X, b.max.Y)
    except Exception as exc:  # noqa: BLE001 — not every annotation bbox-es cleanly
        _log.debug("strip occupancy: %s did not bbox (%s); omitted", type(o).__name__, exc)
        return None


CROSSABLE_TYPES = frozenset({"Centerline", "CenterlineCircle", "CenterMark"})
"""Annotation types a *dimension* may legitimately cross (ISO 128): centre lines
and centre marks. A **leader**, by contrast, must avoid them (#305) — so this is a
per-consumer choice, passed as ``crossable`` to :func:`strip_obstacles`."""


def clear_label_of_centerlines(label_bbox, centerlines, gap):
    """Cumulative ``label_offset_x`` so *label_bbox* clears every crossing
    centre-line-family annotation in *centerlines* (#129) — both a turned part's
    thin vertical/horizontal axis :class:`Centerline` and a bolt-circle's wide
    :class:`CenterlineCircle`. Mirrors the overlap test
    :func:`draftwright.linting.structural.lint_drawing` itself uses
    (``label_centerline_overlap``): a thin line (extent < 0.1 mm in one axis) is
    cleared past its midpoint by half the label width + *gap*; a wide bbox
    (patterns/circles) is cleared past its nearer edge, and only when the two
    boxes would actually overlap by more than 0.5 mm in **both** axes (the same
    threshold the lint check flags). A thin **horizontal** line can't be cleared
    by an X shift alone, so it is left to the lint/repair safety net.

    With more than one centre line, clearing the nearest-edge shift for one can
    re-cross another already cleared — so this re-scans from scratch after every
    single shift (a bounded fixed-point iteration, not a one-pass walk) rather
    than compounding shifts blindly. This is a local search, not a joint solve:
    when two centre lines sit closer together than the label needs to clear both
    (rare — the two documented #129 sources, a part's one turning-axis line and a
    pattern's own bolt circle, aren't normally that close), it can oscillate
    between their two nearest edges and never find the single "go around both"
    position that does exist further out; the iteration cap then returns
    whichever total the last pass left it at (residual overlaps still surface via
    lint, same safety net as the thin-horizontal-line case above)."""
    if label_bbox is None:
        return 0.0
    lmin_x, lmin_y, lmax_x, lmax_y = label_bbox
    extents = []
    for cl in centerlines:
        if not getattr(cl, "is_centerline", False):
            continue
        try:
            extents.append(_centerline_extent(cl))
        except Exception:
            continue
    total = 0.0
    for _ in range(len(extents) + 1):
        eff_lmin_x, eff_lmax_x = lmin_x + total, lmax_x + total
        shift = 0.0
        for cl_min_x, cl_min_y, cl_max_x, cl_max_y in extents:
            cl_w, cl_h = cl_max_x - cl_min_x, cl_max_y - cl_min_y
            if cl_h < 0.1:
                continue  # a horizontal line's clash can't be fixed by an X shift
            oy = min(lmax_y, cl_max_y) - max(lmin_y, cl_min_y)
            if oy <= 0.5:
                continue  # no real vertical overlap — matches the lint's own oy>0.5 gate
            if cl_w < 0.1:
                cl_x = (cl_min_x + cl_max_x) / 2.0
                if not (eff_lmin_x < cl_x < eff_lmax_x):
                    continue
                half_w = (lmax_x - lmin_x) / 2.0
                eff_cx = (eff_lmin_x + eff_lmax_x) / 2.0
                shift_right = cl_x + half_w + gap - eff_cx
                shift_left = cl_x - half_w - gap - eff_cx
            else:
                ox = min(eff_lmax_x, cl_max_x) - max(eff_lmin_x, cl_min_x)
                if ox <= 0.5:
                    continue
                shift_right = (cl_max_x + gap) - eff_lmin_x
                shift_left = (cl_min_x - gap) - eff_lmax_x
            shift = shift_right if abs(shift_right) <= abs(shift_left) else shift_left
            break
        if shift == 0.0:
            break
        total += shift
    return total


def strip_obstacles(dwg, view=None, *, crossable=()):
    """The COMPLETE occupancy for strip placement (ADR 0009): every placed
    annotation's full rendered footprint, optionally restricted to *view*, minus
    any annotation whose type name is in *crossable* (things this particular
    consumer may legitimately overlap — e.g. a location dim crosses a centre line
    but a leader does not; see :data:`CROSSABLE_TYPES`).

    Unlike :func:`_occupied_boxes` (label boxes only, with bare centrelines
    excluded), this captures the geometry a label box hides — leader shafts and
    arrow tips, dimension witness/extension lines, centrelines, and the section
    hatch. That hidden geometry is the 'invisible occupant' class behind the
    recurring strip overlaps (#133/#225/#305): a placer that consults only label
    boxes commits a callout into space a leader or extension line already crosses.

    *view* scoping keeps this view's own annotations **and** drawing-level obstacles
    that no orthographic view owns (the section hatch, title block, …) — those a
    strip placer must still avoid — and drops only the *other* ortho views' blocks
    (which compose-then-pack keeps disjoint, ADR 0004). The section hatch
    (``view_of`` ``None``) is therefore present in every per-view query, the way
    :func:`_occupied_boxes` special-cased it; restricting it to ``view=None`` would
    re-open the very blind spot this closes.

    Boxes are AABBs ``(x0, y0, x1, y1)`` (use with :func:`_box_hits`) — intentionally
    conservative: a diagonal leader's box over-claims its empty triangle (ADR 0009
    notes angled leaders weaken the bound), which only ever over-avoids, never
    under-avoids.

    The occupancy source for the collect-then-solve carve — every migrated renderer's
    ``place_strip_candidates`` call wires this in (#321/#150/P3)."""
    boxes = []
    for name, o in dwg.iter_annotations():
        if view is not None:
            owner = dwg.view_of(name)
            if owner is not None and owner != view:
                continue  # owned by a different ortho view → its own (disjoint) block
        if type(o).__name__ in crossable:
            continue  # this consumer may cross it (centre lines/marks for a dim)
        bb = _geom_box(o)
        if bb is not None:
            boxes.append(bb)
    return boxes


def strip_free_span(strip):
    """``(lo, hi, inner)`` page coords of *strip* along its stacking axis, where
    *inner* is the end nearest the view edge (the first tier a dim fills). Reads the
    live ``outer_limit`` so an orchestrator reservation (#133) stays honoured. The
    cursor-free counterpart of :meth:`Strip.allocate` — a collect-then-solve pass
    (ADR 0009) reads these bounds and carves, rather than advancing a mutable cursor."""
    near = strip.anchor + strip.direction * strip.gap
    if strip.direction == 1:
        return near, strip.outer_limit, near  # lo, hi, inner (=lo)
    return strip.outer_limit, near, near  # lo, hi, inner (=hi)


def carve_free_segments(lo, hi, intervals, pad):
    """``[lo, hi]`` minus every obstacle interval inflated by *pad*, merged and
    complemented — the option-(c) occupancy carve (ADR 0009 / #321). A dim is then
    spaced only WITHIN a clear segment, so it can never overprint a placed occupant
    (a leader shaft, the section hatch, a location-dim tier): the old per-tier
    ``allocate`` + post-hoc ``_box_hits`` retry becomes structural. *intervals* are
    ``(a, b)`` pairs along the strip's stacking axis (e.g. ``(box_y0, box_y1)`` for a
    below strip). Returns a list of ``(seg_lo, seg_hi)`` free segments, lo→hi."""
    blocked = []
    for a0, b0 in intervals:
        a1, b1 = max(lo, a0 - pad), min(hi, b0 + pad)
        if b1 > a1:
            blocked.append((a1, b1))
    blocked.sort()
    merged: list[list[float]] = []
    for a0, b0 in blocked:
        if merged and a0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b0)
        else:
            merged.append([a0, b0])
    free, cur = [], lo
    for a0, b0 in merged:
        if a0 > cur:
            free.append((cur, a0))
        cur = b0
    if cur < hi:
        free.append((cur, hi))
    return free


def corridor_blockers(dwg, view):
    """Boxes of annotations a dimension's *witness corridor* (the span from the view
    edge out to its dim line) must not cross — leaders/callouts, the section hatch, the
    title block: everything that is neither a datum-chained ``Dimension`` nor a
    crossable centre line/mark (:data:`CROSSABLE_TYPES`).

    :func:`strip_obstacles` carves the 1-D strip so a dim *line* clears every occupant,
    but a right/below dim also occupies the 2-D corridor back to the view — and a bore
    callout's leader sitting in that corridor is crossed however far out the line is
    placed (the #133/#225/#305 leader class, in its witness-corridor form). A dim whose
    full footprint hits one of these must route to another view, not overprint it (ISO
    128). Sibling location/envelope dims are excluded: they chain off the shared datum
    and legitimately share the corridor. View scoping mirrors :func:`strip_obstacles`
    (this view's own annotations + drawing-level occupants that no ortho view owns)."""
    boxes = []
    for name, o in dwg.iter_annotations():
        if view is not None:
            owner = dwg.view_of(name)
            if owner is not None and owner != view:
                continue
        if isinstance(o, (Dimension, SafeDimension)) or type(o).__name__ in CROSSABLE_TYPES:
            continue  # datum-chained dims share the corridor; centre lines are crossable
        bb = _geom_box(o)
        if bb is not None:
            boxes.append(bb)
    return boxes


def _box_hits(bb, boxes):
    """True when ``bb`` overlaps any box in ``boxes`` (strict AABB test). Slightly
    more conservative than the within-view label lint (which tolerates a 0.5 mm
    sliver): a touch counts as a hit, so a candidate never overprints — at worst
    it is dropped a hair early."""
    if bb is None:
        return False
    for c in boxes:
        if min(bb[2], c[2]) > max(bb[0], c[0]) and min(bb[3], c[3]) > max(bb[1], c[1]):
            return True
    return False


def _segment_hits_box(p1, p2, box) -> bool:
    """True when line segment *p1*-*p2* intersects axis-aligned *box*
    ``(x0, y0, x1, y1)`` — the precise counterpart of :func:`_box_hits` for a
    genuinely diagonal shaft (ADR 0009 P4/#318, #305: "a diagonal leader's box
    over-claims its empty triangle"). Boxing an angled segment for a coarse
    reject is correct and cheap; boxing it for the final accept/reject decision
    over-avoids free space a real diagonal never crosses. Endpoint-in-box and
    the 4 edge-crossing cases (a standard segment/AABB test).

    The crossing test uses strict inequality deliberately, not an inclusive
    ``<= 0`` — an inclusive test also treats a segment merely COLLINEAR with
    one of the box's (infinite) edge lines as a hit, regardless of whether it
    is anywhere near the box along that line (verified: a vertical segment at
    ``x == box.x0`` but far outside ``[y0, y1]`` false-hits under `<=`). That
    false-positive class is common (any axis-aligned shaft sharing an X or Y
    coordinate with an edge), unlike the strict form's own known gap — a
    segment passing exactly through two opposite corners is a measure-zero
    event for the continuous, non-integer leader positions this computes over
    (review finding, #351 P5 strand 3: tried the inclusive form, reverted)."""
    x0, y0, x1, y1 = box

    def _inside(p):
        return x0 <= p[0] <= x1 and y0 <= p[1] <= y1

    if _inside(p1) or _inside(p2):
        return True

    def _cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    def _seg_seg(a1, a2, b1, b2):
        d1, d2 = _cross(b1, b2, a1), _cross(b1, b2, a2)
        d3, d4 = _cross(a1, a2, b1), _cross(a1, a2, b2)
        return ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
            (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
        )

    corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
    return any(_seg_seg(p1, p2, corners[i], corners[(i + 1) % 4]) for i in range(4))


@dataclass
class CorridorCandidate:
    """One datum-referenced linear dim collected for a shared corridor's single solve
    (ADR 0009 end state, #345/#346). Multiple render passes (`render_locations`,
    `render_slots`) feed the SAME above-view strip; committing per-pass interleaves the
    dims and cannot dedup coincident spans. Each pass instead registers a candidate here;
    one :func:`solve_corridor` per strip dedups, orders, and places the whole set.

    Attributes:
        name/build: the ``(name, pos->Dimension)`` pair :func:`place_strip_candidates`
            consumes — unchanged.
        order:      sort key placing the candidate in the corridor ladder. Location dims
            key on datum distance (the monotonic ISO ladder); size dims form a separate
            contiguous run so a slot length never lands mid-ladder (#346).
        dedup:      coincidence key ``(view, meas-origin, meas-endpoint)`` on the MEASURED
            axis, or ``None`` to never dedup (size dims). Two candidates with equal keys are
            the same physical dimension; the higher-``precedence`` one survives (#345).
        precedence: dedup survivor rank — a hole *location* dim (feeds coverage/table
            escalation) outranks a coincident slot *position* line.
        priority:   over-capacity survival rank (#357). When a strip cannot hold every
            candidate, :func:`plan_strip` drops the lowest ``(priority, key)`` — so a higher
            ``priority`` is kept. An authored GD&T frame sets this above the auto dims so it
            is not dropped in favour of a lower-value auto dim purely by stacking-key order.
            Default 0 (every auto dim) → key order, unchanged.
        anchored/natural: when ``anchored`` is true, the strip solve keeps this candidate
            near its own natural stacking-axis page coordinate instead of the segment edge.
            This is how user-authored pinned dimension intents join the shared solve
            without being invalidated by a later first-fit pass.
        on_place/on_drop: the pass's own post-placement bookkeeping — coverage
            registration / drop lint + `Escalation`, or a slot's below-side fallthrough.
        force:      policy-B force-keep after the corridor-respecting pass (locations have
            no alternate view); size/position slot dims fall through instead (``on_drop``).
    """

    name: str
    build: object
    order: tuple
    on_place: object
    on_drop: object
    dedup: tuple | None = None
    precedence: int = 0
    priority: float = 0
    anchored: bool = False
    natural: float | None = None
    force: bool = False
    # The source IR feature this dim was rendered for — recorded as provenance when the
    # dim is placed at drain (ADR 0010). ``None`` leaves the annotation feature-less.
    feature: object | None = None
    # Real stacking-axis + perpendicular footprint ``(w, h)`` in page-mm, or ``None`` to
    # use the dimension default ``(tier, tier)``. Wide/tall occupants (a GD&T feature
    # control frame is ~24×6 mm) set this so the strip solve reserves their true extent
    # instead of one label-height (ADR 0009 real-footprint plumbing, #61). A dim leaves
    # it ``None`` — byte-identical to the pre-plumbing placement.
    size: tuple | None = None
    # An ``(x0, y0, x1, y1)`` page-box this candidate must NOT overlap even when force-kept —
    # the title block, which is placed after the corridor drain so the strip carve can't see
    # it (#481). ``None`` (every dim) skips the check → byte-identical.
    forbid: object | None = None


def solve_corridor(dwg, strip, view, axis, cands, tier):
    """One collect-then-solve over every :class:`CorridorCandidate` a shared strip
    accumulated across passes (ADR 0009 end state). Dedup → order → one non-force
    :func:`place_strip_candidates` pass → a force pass for the force-eligible leftovers →
    dispatch each candidate's ``on_place``/``on_drop``. This is what removes the duplicate
    span (#345) and the interleaved ladder (#346) by construction: a single solve sees the
    full set, so coincident spans collapse and the order is one monotonic chain."""
    if not cands:
        return
    # Dedup: keep the highest-precedence candidate per coincidence key (tie-break on name,
    # deterministic — ADR 0001). A displaced duplicate is a *loser*: while its winner is
    # drawn it is silently dropped (never starved, so firing its pass's drop lint would be a
    # false report) — but if the winner itself fails to place, the top loser is promoted so
    # the measurement still gets its pass's fallthrough/drop handling (no silent vanish).
    winners: dict = {}
    for c in cands:
        if c.dedup is None:
            continue
        prev = winners.get(c.dedup)
        # Winner: highest precedence, ties broken by the lexicographically smaller name.
        if (
            prev is None
            or c.precedence > prev.precedence
            or (c.precedence == prev.precedence and c.name < prev.name)
        ):
            winners[c.dedup] = c
    kept = [c for c in cands if c.dedup is None or winners.get(c.dedup) is c]
    losers: dict = {}  # dedup key → its displaced candidates (highest precedence first)
    for c in cands:
        if c.dedup is not None and winners.get(c.dedup) is not c:
            losers.setdefault(c.dedup, []).append(c)
    for group in losers.values():
        group.sort(key=lambda c: (-c.precedence, c.name))
    kept.sort(key=lambda c: c.order)

    def _promote_losers(dropped_winner):
        # The winner did not place → hand its measurement to the best surviving loser
        # (e.g. the slot position's below-strip fallthrough), then stop.
        for loser in losers.get(dropped_winner.dedup, ()):
            loser.on_drop(loser.name)
            break

    if strip is None:  # no such strip on this drawing — every candidate drops
        for c in kept:
            c.on_drop(c.name)
            if c.dedup is not None:
                _promote_losers(c)
        return
    pairs = [(c.name, c.build) for c in kept]
    feats = {c.name: c.feature for c in kept if c.feature is not None}  # provenance (ADR 0010)
    sizes = {c.name: c.size for c in kept if c.size is not None}  # real footprint (#61)
    forbid = {c.name: c.forbid for c in kept if c.forbid is not None}  # title-block box (#481)
    prio = {c.name: c.priority for c in kept if c.priority}  # over-capacity survival rank (#357)
    anchored = {c.name: c.anchored for c in kept if c.anchored}
    naturals = {c.name: c.natural for c in kept if c.natural is not None}
    left = {
        n
        for n, _ in place_strip_candidates(
            dwg,
            strip,
            view,
            axis,
            pairs,
            tier,
            features=feats,
            sizes=sizes,
            forbid=forbid,
            priorities=prio,
            anchored=anchored,
            naturals=naturals,
        )
    }
    force_pairs = [(c.name, c.build) for c in kept if c.name in left and c.force]
    still = (
        {
            n
            for n, _ in place_strip_candidates(
                dwg,
                strip,
                view,
                axis,
                force_pairs,
                tier,
                force=True,
                features=feats,
                sizes=sizes,
                forbid=forbid,
                priorities=prio,
                anchored=anchored,
                naturals=naturals,
            )
        }
        if force_pairs
        else set()
    )
    for c in kept:
        placed = c.name not in left or (c.force and c.name not in still)
        if placed:
            c.on_place(c.name)  # placed in the corridor-respecting pass or the force pass
        else:
            c.on_drop(c.name)  # dropped / not force-kept — the pass's drop handler runs
            if c.dedup is not None:  # a deduped winner failed → promote its top loser
                _promote_losers(c)


def register_corridor(dwg, key, strip, view, axis, tier, cand):
    """Queue a :class:`CorridorCandidate` under a shared corridor *key* so one
    :func:`drain_corridors` places the whole cross-pass set together (ADR 0009 end state).
    The first registration for a key fixes its ``(strip, view, axis)``; mixed producers on
    the same corridor use the largest requested tier so spacing is not registration-order
    dependent."""
    b = dwg._corridor_batch.setdefault(
        key, {"strip": strip, "view": view, "axis": axis, "tier": tier, "cands": []}
    )
    b["tier"] = max(b["tier"], tier)
    b["cands"].append(cand)


def drain_corridors(dwg):
    """Solve every registered corridor (one :func:`solve_corridor` per strip), then clear
    the batch. Called once, after all corridor-feeding passes have registered."""
    for b in dwg._corridor_batch.values():
        solve_corridor(dwg, b["strip"], b["view"], b["axis"], b["cands"], b["tier"])
    dwg._corridor_batch = {}


def place_strip_candidates(
    dwg,
    strip,
    view,
    axis,
    cands,
    tier,
    *,
    force=False,
    features=None,
    sizes=None,
    forbid=None,
    priorities=None,
    anchored=None,
    naturals=None,
):
    """Collect-then-solve placement of location/feature dims on one strip (ADR 0009).
    The single shared strip placer that retires the ``Strip.allocate`` cursor (#150,
    P3): each candidate in *cands* — an ``(name, build(pos)->dim)`` pair — is spaced by
    one :func:`plan_strip` solve per free segment of the CARVED strip (`strip` carved
    around :func:`strip_obstacles`), replacing the per-dim ``allocate`` + ``_box_hits``
    tier-retry. *tier* is the label height (sets the inter-dim gap ``tier + spacing``).

    Occupancy is THIS view's own placed annotations plus the drawing-level obstacles no
    ortho view owns (the section hatch), recomputed per call so a dim placed earlier in
    the pass is avoided; other ortho views are disjoint (ADR 0004) and excluded so their
    rows never over-carve this strip. This makes the old post-hoc collision retry
    structural: a dim can never land on a bore-callout leader shaft the label-only
    occupancy missed (#133/#225/#305).

    A right/below dim also occupies the 2-D corridor back to the view edge, which the
    1-D strip carve cannot represent: a leader in that corridor is crossed no matter how
    far out the dim line lands. By default such a placement is rejected so the caller can
    route the dim to the other view (its disjoint block cannot cross this leader).

    *sizes* maps a candidate's name to its real page-mm footprint ``(w, h)``; absent
    names use the dimension default ``(tier, tier)``. A wide/tall occupant (a GD&T
    frame, #61) sets it so :func:`plan_strip` enforces its true stacking gap — over
    capacity it is relocated to the next segment or dropped, never overlapped.

    *priorities* maps a candidate's name to its over-capacity survival rank (#357);
    absent names default to 0. When a segment is over capacity :func:`plan_strip` drops
    the lowest ``(priority, key)``, so a higher priority is kept — an authored GD&T frame
    is not dropped for a lower-value auto dim purely by stacking-key order.

    *anchored* and *naturals* opt individual candidates into the weighted anchoring
    mode in :func:`plan_strip`. This preserves the old segment-edge natural for every
    caller that does not pass them, while letting authored pinned candidates express the
    page coordinate they asked for inside the same shared solve.

    ``force=True`` skips that corridor check — the caller's last resort when no view took
    the dim cleanly: keep it on its natural view and accept the (same-feature) leader
    crossing rather than drop a real dimension (policy B). Candidates that find no strip
    tier AT ALL are still returned (a physically full strip — the caller records the
    genuine drop)."""
    if strip is None or not cands:
        return list(cands)
    lo, hi, inner = strip_free_span(strip)
    idx = 1 if axis == "y" else 0

    # Reserve the outermost label's OUTWARD extent at the strip boundary. plan_strip bounds
    # the dim-LINE position, but the label extends outward from it — so without this the last
    # tier's label overshoots outer_limit (into the iso view / page margin), unlike the old
    # Strip.allocate which checked `start + tier <= outer_limit` (#338 review). A plain dim's
    # label extends one `tier` outward (one-sided). A GD&T glyph (#61) hangs off a Leader that
    # CENTRES it on the elbow for an above/below strip (real outward extent = height/2) but
    # places it one-sided for a left/right strip (extent = full width). Reserve the MAX real
    # outward extent among these candidates — else a glyph wider than `tier` renders off the
    # sheet (annotation_out_of_bounds) instead of dropping when the strip is too narrow (ADR
    # 0009 Amdt 7 fixed inter-candidate gaps but not this edge). With no `sizes` (every dim)
    # this is `tier`, byte-identical. The strip edge is not an obstacle (obstacles carry their
    # own footprint + pad), so only the boundary needs it.
    def _outward(name):
        sz = (sizes or {}).get(name)
        if sz is None:
            return tier  # a dim: one-sided tier reservation (unchanged)
        return sz[idx] if axis == "x" else sz[idx] / 2  # GD&T: one-sided (L/R) vs centred (A/B)

    reserve = max([tier, *(_outward(n) for n, _ in cands)])
    if inner == lo:
        hi -= reserve
    else:
        lo += reserve
    perp = 0 if axis == "y" else 1  # the axis the dims do NOT stack along
    pad = tier + strip.spacing  # min separation between stacked dim lines
    # Perpendicular band of these candidates. The 1-D carve projects obstacles onto the
    # stacking axis only, so an obstacle on ANOTHER strip of this view — disjoint in the
    # perpendicular axis, never actually touching — would falsely block (e.g. the overall
    # width dim below the view blocking a slot-width dim on the right strip). Filter such
    # obstacles out first. The perpendicular extent is independent of the tier position,
    # so a single probe build per candidate suffices; the corridor check below already
    # uses the full 2-D box, so it needs no such filter.
    pbands = [
        (b[perp], b[perp + 2]) for _n, build in cands if (b := _geom_box(build(lo))) is not None
    ]
    occupied = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
    if pbands:
        band_lo, band_hi = min(p[0] for p in pbands), max(p[1] for p in pbands)
        occupied = [b for b in occupied if b[perp] < band_hi and b[perp + 2] > band_lo]
    blockers = () if force else corridor_blockers(dwg, view)
    segs = carve_free_segments(lo, hi, [(b[idx], b[idx + 2]) for b in occupied], pad)
    # Fill innermost-first (nearest the view), matching the old cursor's stack order.
    segs.sort(key=lambda s: abs((s[0] if inner == lo else s[1]) - inner))
    todo = list(cands)

    def _take_for_segment(items, n):
        if len(items) <= n:
            return items, []
        # Do not let segment-cap slicing preempt the ranked selection step (#357/#393).
        # `plan_strip` drops the lowest (priority, generated-key), but a narrow segment
        # can only see the candidates we hand it. Preselect the highest-priority members
        # for this segment, preserving their original order for crossing-free placement;
        # ties mirror the generated key below (inner=lo keeps later candidates, inner=hi
        # keeps earlier candidates).
        ranked = sorted(
            enumerate(items),
            key=lambda item: (
                (priorities or {}).get(item[1][0], 0.0),
                item[0] if inner == lo else -item[0],
            ),
            reverse=True,
        )
        chosen = {i for i, _ in ranked[:n]}
        take = [nb for i, nb in enumerate(items) if i in chosen]
        rest = [nb for i, nb in enumerate(items) if i not in chosen]
        return take, rest

    def _evaluate_segment(take, seg_lo, seg_hi):
        nat = seg_lo if inner == lo else seg_hi
        # Keys order the tiers so the FIRST candidate lands on the inner tier: for an
        # inner=lo strip that is the lowest position (ascending keys); for a below strip
        # (inner=hi) it is the highest, so the keys reverse.
        triples = [
            (
                StripCandidate(
                    f"{(k if inner == lo else len(take) - 1 - k):04d}",
                    (
                        (0.0, (naturals or {}).get(nb[0], nat))
                        if axis == "y"
                        else ((naturals or {}).get(nb[0], nat), 0.0)
                    ),
                    (sizes or {}).get(nb[0], (tier, tier)),
                    priority=(priorities or {}).get(nb[0], 0.0),
                    anchored=(anchored or {}).get(nb[0], False),
                ),
                nb,
            )
            for k, nb in enumerate(take)
        ]
        res = plan_strip([sc for sc, _ in triples], seg_lo, seg_hi, pad, axis=axis)
        accepted = []
        rejected = []
        for sc, (name, build) in triples:
            pos = res.placed.get(sc.key)
            if pos is None:  # segment over its estimated capacity (shouldn't occur)
                rejected.append((name, build))
                continue
            dim = build(pos)
            if not force and _box_hits(_geom_box(dim), blockers):  # corridor crosses a leader
                rejected.append((name, build))
                continue
            # A forbidden box (the title block, #481) is rejected even under force — it is
            # placed after the drain, so the strip carve can't see it; a force-kept GD&T frame
            # must still not stack onto it. `forbid` maps names to their box (only GD&T sets it,
            # so dims are byte-identical). Returned unplaced → the caller's on_drop fallthrough.
            fb = (forbid or {}).get(name)
            if fb is not None and _box_hits(_geom_box(dim), (fb,)):
                rejected.append((name, build))
                continue
            accepted.append(((name, build), dim))
        return accepted, rejected

    for seg_lo, seg_hi in segs:
        if not todo:
            break
        cap = int((seg_hi - seg_lo) / pad) + 1
        take, todo = _take_for_segment(todo, cap)
        rejected_total = []
        while take:
            accepted, rejected = _evaluate_segment(take, seg_lo, seg_hi)
            rejected_total.extend(rejected)
            vacancies = cap - len(accepted)
            if vacancies <= 0 or not todo:
                break
            fill, todo = _take_for_segment(todo, vacancies)
            take = [nb for nb, _dim in accepted] + fill
        todo = todo + rejected_total
        for (name, _build), dim in accepted:
            # Record feature provenance (ADR 0010): the drain-time seam for corridor-placed
            # dims — `features` maps this batch's names to their source IR feature.
            dwg.add(dim, name, view=view, feature=(features or {}).get(name))
    return todo


def carve_free_position(dwg, strip, view, axis, tier, perp_span, *, outermost=False):
    """The single free tier POSITION on *strip* at which a dim of height *tier* spanning
    *perp_span* ``(lo, hi)`` on the perpendicular axis clears every placed obstacle in
    *view* — the innermost (nearest the view) tier by default, or the outermost fitting
    one when *outermost*. Returns the dim-line page coord, or None if the strip is full.

    The position-returning counterpart of :func:`place_strip_candidates` (which batches,
    builds and adds): a caller that needs a dim's assigned position BEFORE building the
    next — the height-ladder leapfrog chain, where each step dim's witness base is the
    previous dim's line — uses this. Same carve: outer-label tier reservation, the
    perpendicular-band filter (*perp_span* drops obstacles disjoint from this dim's own
    perpendicular extent), and innermost-first fill.

    **No corridor check, by construction — not just omission.** This avoids obstacle
    *tiers* on the strip but does not reject a position whose witness *corridor* (feature
    → dim line, across *perp_span*) crosses a leader/callout. Crucially, a single-position
    return *cannot* fix a corridor crossing by choosing a different tier: every tier on
    one side shares that corridor, and a farther tier's corridor is a **superset** of a
    nearer one's, so the innermost free tier this already returns has the shortest
    corridor and the fewest crossings — moving outward only adds crossings. Corridor
    avoidance is therefore inherently a **relocation** problem (reject this position →
    place on another view/side), which is :func:`place_strip_candidates`' job and out of
    scope for a position return. Per caller: the height-ladder chain has no alternate
    view (correct to omit); public ``Drawing.place_dim`` takes the view AND side from the
    caller, so it cannot relocate; the PMI dim helpers already fall through sides
    (``_try_above(...) or _try_below(...)``) and are where a corridor-reject would go if
    ever wanted. Left as a documented known-limitation — the crossing is unobserved on
    the corpus (the cleanliness ratchet would catch it)."""
    if strip is None:
        return None
    lo, hi, inner = strip_free_span(strip)
    idx = 1 if axis == "y" else 0
    perp = 0 if axis == "y" else 1
    pad = tier + strip.spacing
    band_lo, band_hi = perp_span
    occ = [
        b
        for b in strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES)
        if b[perp] < band_hi and b[perp + 2] > band_lo
    ]
    segs = carve_free_segments(lo, hi, [(b[idx], b[idx + 2]) for b in occ], pad)
    # A segment holds the dim iff it is at least `tier` wide (the label height). This IS
    # the outer-label reservation — inclusive at the boundary (a strip exactly `gap+tier`
    # wide fits one dim, as the old `allocate` did) — so it must NOT be combined with a
    # separate `hi -= tier` pull-in, which would double-reserve and drop that dim.
    fitting = [s for s in segs if s[1] - s[0] >= tier - 1e-9]
    if not fitting:
        return None
    if inner == lo:  # inner edge = seg lo; outermost = the segment reaching furthest out
        seg = max(fitting, key=lambda s: s[1]) if outermost else min(fitting, key=lambda s: s[0])
        return seg[0]
    seg = min(fitting, key=lambda s: s[0]) if outermost else max(fitting, key=lambda s: s[1])
    return seg[1]
