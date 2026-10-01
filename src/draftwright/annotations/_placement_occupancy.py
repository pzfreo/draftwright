"""Placed annotation occupancy and exact ink-clearance policy (ADR 2).

Shared by corridor placement, late furniture, and the isometric fit.
"""

from __future__ import annotations

import math
from itertools import chain

from build123d_drafting.helpers import Dimension, Leader, Note, SafeDimension

from draftwright._core import _analysis_margins, place_annotation
from draftwright._geometry import _boxes_overlap, _segment_clips_box, _segments_cross_or_overlap
from draftwright.annotations._dimension_ink import DimensionInkCandidate
from draftwright.annotations._placement_geometry import (
    _STROKE_PAD,
    _box_hits,
    _geom_box,
    occupancy_boxes,
)
from draftwright.annotations.angular import AngularDimension
from draftwright.linting.ink_overlap import (
    MIN_CROSSING_MM,
    crossable_region,
    crossing_length,
    segments_of,
)
from draftwright.linting.structural import _edges_intersect_rect, _view_edge_entries

CROSSABLE_TYPES = frozenset({"Centerline", "CenterlineCircle", "CenterMark"})
"""Annotation types a *dimension* may legitimately cross (ISO 128): centre lines
and centre marks. A **leader**, by contrast, must avoid them (#305) — so this is a
per-consumer choice, passed as ``crossable`` to :func:`strip_obstacles`."""


#: Annotations whose Compound bounding box spans the whole page. They carry no ``.segments``
#: to decompose, so :func:`annotation_obstacle_boxes` falls back to the full geometry box and
#: they swallow the sheet — any occupancy test that includes them is a silent no-op. Filtered
#: by `late_furniture_obstacles`, by three checks in `linting/structural`, and by
#: `annotation_ink_obstacles`, because the iso fit had picked
#: `strip_obstacles` instead and `--frame` therefore disabled the fit entirely.
_PAGE_SPANNING_RIDERS = ("is_sheet_frame", "is_zone_grid")


def is_page_spanning_rider(annotation) -> bool:
    """Is *annotation* one of the page-spanning riders (see :data:`_PAGE_SPANNING_RIDERS`)?

    One predicate, because the filter has been written out by hand in five places and the
    sixth site got it wrong: the fit's obstacle set omitted it and a framed sheet's iso stopped
    growing, silently, with the whole fast tier green (#1240).
    """
    return any(getattr(annotation, rider, False) for rider in _PAGE_SPANNING_RIDERS)


def annotation_ink_obstacles(dwg, *, named=False):
    """Every placed annotation's decomposed ink, minus the page-spanning riders.

    What a placement or fit outside the strip system needs: the same occupancy
    :func:`strip_obstacles` computes, without the frame/zone-grid boxes that would make the
    test vacuous — and WITHOUT the views that :func:`late_furniture_obstacles` adds, which the
    iso fit must not see (it would treat itself as an obstacle and never grow).
    """
    return [
        (name, box) if named else box
        for name, box in strip_obstacles(dwg, named=True)
        if not is_page_spanning_rider(dwg.get_annotation(name))
    ]


def late_furniture_obstacles(dwg, *, named=False):
    """Everything a POST-FIT placement must keep clear of, as AABBs.

    Late furniture — the gear/BOM/revision tables, the hole table, the iso's NTS
    caption — is placed after ``_fit_iso_view`` has settled the views, so unlike a
    strip occupant it faces the whole finished sheet. All of it needs the same four
    things, and the policy is subtle enough in three places that every copy has got
    one of them wrong:

    * **views**, which own no annotation and so never appear in ``strip_obstacles``;
    * **the decomposed annotation occupancy**, because a placer consulting only label
      boxes commits into space a leader shaft or witness line already crosses — the
      'invisible occupant' class (#133/#225/#305, and again in #1197);
    * **minus the border and zone ruler**, whose page-spanning Compound bbox has no
      ``.segments`` to decompose and would otherwise swallow the entire sheet, making
      the check a silent no-op (#1145);
    * **plus the title block as one hull**, because its grid lines bound text-filled
      cells — it is furniture, not a lattice of free pockets — and it carries no
      ``label_bbox``, so nothing else reaches it.

    Anonymous items (the pre-0.5.0 ``Drawing.add(obj)`` surface) have no registry
    identity for the named walk to find, so they keep the coarse full-bbox fallback.

    With *named*, boxes come back as ``(owner, box)`` pairs for diagnostics.
    """
    obstacles = [
        (f"view:{view}", box) for view in dwg.views if (box := dwg.view_bounds(view)) is not None
    ]
    for annotation_name, box in strip_obstacles(dwg, named=True):
        annotation = dwg.get_annotation(annotation_name)
        if is_page_spanning_rider(annotation):
            continue
        obstacles.append((f"annotation:{annotation_name}", box))

    registered_ids = {id(annotation) for _name, annotation in dwg.iter_annotations()}
    for index, annotation in enumerate(dwg.items):
        if id(annotation) in registered_ids:
            continue
        try:
            bb = annotation.bounding_box()
        except Exception:  # noqa: BLE001 — not every compatibility object bbox-es cleanly
            continue
        obstacles.append(
            (f"anonymous-annotation[{index}]", (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y))
        )

    title_block = dwg.get_annotation("title_block")
    if title_block is not None:
        try:
            bb = title_block.bounding_box()
        except Exception:  # noqa: BLE001 — the decomposed occupancy remains available
            pass
        else:
            obstacles.append(("annotation:title_block", (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y)))

    return obstacles if named else [box for _owner, box in obstacles]


def place_iso_nts_note(dwg, a, bb, *, anno_box) -> None:
    """Place the "ISO VIEW (NTS)" caption clear of what is already on the sheet.

    This ran with **no collision check at all** — the caption was dropped a fixed two
    font-heights below the iso bbox and committed. And `_fit_iso_view` runs AFTER
    `_auto_annotate`, so every dimension and callout is already placed by then: the
    annotations could not avoid a caption that did not exist yet, and the caption did
    not look at them. On a sparse sheet nothing collides and the fault is invisible; on
    a part whose callouts reach under the iso, the caption lands on top of one.

    The caption tries its natural position first — preserving today's placement wherever
    that is clear — then falls back around the iso block. If nothing is clear it is
    placed naturally anyway: a caption saying which view is not to scale is required
    content, and Policy B keeps required content at a visible cost rather than dropping
    it. Be precise about that cost, because an earlier version of this docstring was not:
    `annotation_overlap` compares LABEL boxes, so it reports a retained clash with another
    label and reports nothing at all for a leader shaft or a view. The obstacle set below
    is deliberately wider than the lint for exactly that reason — avoidance covers what
    the backstop cannot.

    Obstacles come from :func:`late_furniture_obstacles` — the same set the tables
    place against. Two earlier cuts of this function hand-rolled their own and each got
    one part wrong: whole-geometry boxes swallowed the sheet frame and rejected every
    candidate (a silent no-op, #1145's trap); label boxes then went blind to the title
    block and walked the caption into it, and blind to leader shafts, which is the
    'invisible occupant' class the shared set exists to close. This function places
    late furniture, so it uses the late-furniture occupancy; it does not get its own.

    """
    font = dwg.draft.font_size
    natural = (a.ISO_X, max(bb[1] - 2 * font, _analysis_margins(a).bottom + font))

    # One Note is built, not one per candidate: its box is position-invariant apart from
    # translation, so the rest are derived arithmetically rather than by six throwaway
    # OCC text builds on a path the repack loop runs up to three times per build.
    probe = Note("ISO VIEW (NTS)", natural, dwg.draft)
    base = anno_box(probe)
    if base is None:
        place_annotation(dwg.registry, dwg.items, probe, "note_iso_nts")
        return
    width, height = base[2] - base[0], base[3] - base[1]
    offset_x, offset_y = base[0] - natural[0], base[1] - natural[1]

    # Sideways candidates are expressed as a desired BOX EDGE and converted back through
    # `offset_x`, so they clear the iso block's real extent whatever the Note's anchor
    # convention is. The previous form stepped `(bb width)/2 + (caption width)/2 + font`
    # from `ISO_X`, which assumes the iso bbox is centred on `ISO_X` — measured on a real
    # A3 build its centre is 332.9 against an `ISO_X` of 310, so the step both overshot
    # one side and undershot the other.
    #
    # Order is by how well the caption still reads as belonging to the iso view: directly
    # below (today's placement), then further below, then beside it, and only last ABOVE
    # — a caption over its view is against drawing convention, so it must not be reached
    # while a below-or-beside position is free. Left before right is arbitrary but fixed,
    # because ADR 4 (was 0001) requires the same sheet twice.
    candidates = [
        natural,
        (a.ISO_X, max(bb[1] - 3 * font - height, _analysis_margins(a).bottom + font)),
        (bb[0] - font - width - offset_x, natural[1]),
        (bb[2] + font - offset_x, natural[1]),
        (a.ISO_X, min(bb[3] + 2 * font, a.PAGE_H - _analysis_margins(a).top - font)),
    ]

    obstacles = late_furniture_obstacles(dwg)
    # The same keep-clear band `add_table` gives its late furniture. Without it a caption
    # 0.01 mm from a dimension label counted as "clear", and the Policy-B backstop could
    # not report it either: `annotation_overlap` fires only past 0.5 mm in BOTH axes, so a
    # flush caption was invisible to the check AND to the lint it defers to.
    clearance = getattr(dwg.draft, "pad_around_text", 0.0)

    chosen, seen = natural, set()
    for position in candidates:
        key = (round(position[0], 6), round(position[1], 6))
        if key in seen:
            continue  # a clamped candidate can coincide with one already rejected
        seen.add(key)
        box = (
            position[0] + offset_x,
            position[1] + offset_y,
            position[0] + offset_x + width,
            position[1] + offset_y + height,
        )
        # Horizontal only, and the reason is narrower than an earlier version of this
        # comment claimed. The y candidates are NOT all clamped both ways — candidates 1
        # and 2 are clamped from below only, candidate 5 from above only. What makes a
        # vertical check unreachable is that each is clamped on the side it can run off,
        # to `margin + font`, while the caption extends only `height` past its position:
        # measured, half-height 1.35 against a font of 3.0, so ~1.65 mm of slack remains
        # and it scales with the font. The x candidates have no clamp at all — the
        # sideways positions deliberately step past the iso block and can leave the
        # sheet, which is what this catches.
        if box[0] < _analysis_margins(a).left or box[2] > a.PAGE_W - _analysis_margins(a).right:
            continue
        keep_clear = (
            box[0] - clearance,
            box[1] - clearance,
            box[2] + clearance,
            box[3] + clearance,
        )
        if any(_boxes_overlap(keep_clear, other) for other in obstacles):
            continue
        chosen = position
        break
    place_annotation(
        dwg.registry,
        dwg.items,
        probe if chosen == natural else Note("ISO VIEW (NTS)", chosen, dwg.draft),
        "note_iso_nts",
    )


def annotation_obstacle_boxes(dwg, annotation):
    """One annotation's obstacle boxes under the shared strip-occupancy policy.

    Candidate-based placers use this before an annotation is registered; factoring
    the preset-aware stroke pad here keeps those candidate conflicts identical to
    the boxes :func:`strip_obstacles` will expose after placement (#740).
    """

    arrow_length = getattr(getattr(dwg, "draft", None), "arrow_length", None)
    pad = max(_STROKE_PAD, arrow_length / 2) if arrow_length else _STROKE_PAD
    return occupancy_boxes(annotation, stroke_pad=pad)


def strip_obstacles(dwg, view=None, *, crossable=(), named=False):
    """The COMPLETE occupancy for strip placement (ADR 2 (was 0009)): every placed
    annotation's full rendered footprint, optionally restricted to *view*, minus
    any annotation whose type name is in *crossable* (things this particular
    consumer may legitimately overlap — e.g. a location dim crosses a centre line
    but a leader does not; see :data:`CROSSABLE_TYPES`).

    With *named* (the #736 trace/diagnosis flavour) each box comes back as an
    ``(owner-name, box)`` pair — the same boxes, tagged with the annotation name
    they decompose from — so a trace or a "what filled this strip" message can
    attribute the occupancy. Default off: the hot placement path carries bare
    boxes, unchanged.

    Unlike the retired label-box-only ``_occupied_boxes`` (which excluded bare
    centrelines), this captures the geometry a label box hides — leader shafts and
    arrow tips, dimension witness/extension lines, centrelines, and the section
    hatch. That hidden geometry is the 'invisible occupant' class behind the
    recurring strip overlaps (#133/#225/#305): a placer that consults only label
    boxes commits a callout into space a leader or extension line already crosses.

    *view* scoping keeps this view's own annotations **and** drawing-level obstacles
    that no orthographic view owns (the section hatch, title block, …) — those a
    strip placer must still avoid — and drops only the *other* ortho views' blocks
    (which compose-then-pack keeps disjoint, ADR 2 (was 0004)). The section hatch
    (``view_of`` ``None``) is therefore present in every per-view query, the way
    ``_occupied_boxes`` special-cased it; restricting it to ``view=None`` would
    re-open the very blind spot this closes.

    Boxes are AABBs ``(x0, y0, x1, y1)`` (use with :func:`_box_hits`) — intentionally
    conservative: a diagonal leader's box over-claims its empty triangle (ADR 2 (was 0009)
    notes angled leaders weaken the bound), which only ever over-avoids, never
    under-avoids.

    The occupancy source for the collect-then-solve carve — every migrated renderer's
    ``place_strip_candidates`` call wires this in (#321/#150/P3)."""
    boxes: list = []
    for name, o in dwg.iter_annotations():
        if view is not None:
            owner = dwg.view_of(name)
            if owner is not None and owner != view:
                continue  # owned by a different ortho view → its own (disjoint) block
        if type(o).__name__ in crossable:
            continue  # this consumer may cross it (centre lines/marks for a dim)
        occ = annotation_obstacle_boxes(dwg, o)  # decomposed, not one hull
        boxes.extend(((name, b) for b in occ) if named else occ)
    return boxes


def pending_title_block_box(dwg):
    """The title block's deterministic page box while it is still UNPLACED, else ``None``.

    The block is drawn near the end of ``_PASS_SEQUENCE``, so every strip placer runs
    before it exists as an annotation — yet its footprint is fixed the moment the sheet
    is (``builder._assemble`` computes it once, ADR 1). This is the read side of that one
    write: a keep-out box the placers can honour, without the block having to be placed
    early.

    NOT an entry in :func:`strip_obstacles`. That occupancy is carved with the dim
    STACKING pad (``tier + spacing``) — the separation two dimension lines need from each
    other — which is several millimetres larger than the clearance a dim needs from a
    solid piece of furniture. Carved, the block refuses dims that clear it by a
    millimetre or two; checked as a hard box against the candidate's own footprint (the
    way ``forbid`` already treats the block for GD&T frames, #481) it refuses exactly the
    dims that would land in it.

    ``getattr`` because the drawing is duck-typed as ``dwg`` (ADR 1): stand-ins and
    partial drawings in tests and in the repair path carry no build state to ask.
    """
    reader = getattr(dwg, "pending_title_block_box", None)
    if not callable(reader) or dwg.get_annotation("title_block") is not None:
        return None
    return reader()


def strip_occupants(dwg, strip, view, axis, limit=3):
    """The names of the annotations whose footprints occupy *strip*'s free span,
    ranked by covered stacking-axis extent (largest first; ties by name) — the
    "what filled this strip" answer the #736 enriched drop message and the solve
    trace share. ``[]`` when the strip is absent or nothing overlaps it."""
    if strip is None:
        return []
    lo, hi, _inner = strip_free_span(strip)
    idx = 1 if axis == "y" else 0
    cover: dict[str, float] = {}
    named = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES, named=True)
    # The not-yet-placed title block is not strip occupancy (it is a keep-out box, not a
    # carve entry — :func:`pending_title_block_box`), but it IS what filled the span when
    # it filled it, and a drop message that cannot say so sends the reader hunting.
    tb = pending_title_block_box(dwg)
    if tb is not None:
        named = [*named, ("title_block", tb)]
    for name, box in named:
        ov = min(hi, box[idx + 2]) - max(lo, box[idx])
        if ov > 0:
            cover[name] = cover.get(name, 0.0) + ov
    return sorted(cover, key=lambda n: (-cover[n], n))[:limit]


def full_strip_message(base, dwg, strip, view, axis):
    """Extend a ``"…strip full)"`` drop message with the top occupant names (#736):
    ``"…strip full; occupied by: dim_a, ldr_b)"`` — so a placement_unsatisfiable
    drop names what filled the strip instead of demanding a custom-script rebuild
    (the #733 diagnosis). Returns *base* unchanged when no occupant is known."""
    occ = strip_occupants(dwg, strip, view, axis)
    if not occ:
        return base
    who = ", ".join(occ)
    if base.endswith(")"):
        return f"{base[:-1]}; occupied by: {who})"
    return f"{base} (occupied by: {who})"


def strip_free_span(strip):
    """``(lo, hi, inner)`` page coords of *strip* along its stacking axis, where
    *inner* is the end nearest the view edge (the first tier a dim fills). Reads the
    live ``outer_limit`` so an orchestrator reservation (#133) stays honoured. The
    cursor-free counterpart of :meth:`Strip.allocate` — a collect-then-solve pass
    (ADR 2 (was 0009)) reads these bounds and carves, rather than advancing a mutable cursor."""
    near = strip.anchor + strip.direction * strip.gap
    if strip.direction == 1:
        return near, strip.outer_limit, near  # lo, hi, inner (=lo)
    return strip.outer_limit, near, near  # lo, hi, inner (=hi)


def corridor_blockers(dwg, view, *, exact_leaders=False):
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
    cache = getattr(dwg, "box_cache", None)  # the drawing's box memo
    boxes = []
    for name, o in dwg.iter_annotations():
        if view is not None:
            owner = dwg.view_of(name)
            if owner is not None and owner != view:
                continue
        if (
            isinstance(o, (Dimension, SafeDimension))
            or type(o).__name__ in CROSSABLE_TYPES
            or (exact_leaders and isinstance(o, Leader) and segments_of(o))
        ):
            continue  # datum-chained dims share the corridor; centre lines are crossable
        bb = _geom_box(o, cache)
        if bb is not None:
            boxes.append(bb)
    return boxes


def strip_dimension_occupancy(dwg, view, axis, *, exact=True):
    """Coarse strip boxes plus ink that a final dimension checks exactly.

    Leader hulls claim empty strip space; parallel dimensions contribute a line station
    rather than their padded witness boxes. Opaque furniture stays in the coarse carve.
    """
    named = strip_obstacles(dwg, view=view, crossable=CROSSABLE_TYPES, named=True)
    if not exact:
        return named, [], []
    annotations = {
        name: annotation
        for name, annotation in dwg.iter_annotations()
        if dwg.view_of(name) in (None, view)
    }
    leaders = [
        (name, annotation)
        for name, annotation in annotations.items()
        if isinstance(annotation, Leader) and segments_of(annotation)
    ]
    sides = {"above", "below"} if axis == "y" else {"left", "right"}
    registry = getattr(dwg, "registry", None)
    dimensions = []
    for name, annotation in annotations.items():
        if not isinstance(annotation, (Dimension, SafeDimension)):
            continue
        spec = registry.dimension_spec_of(name) if registry is not None else None
        if spec is None:
            spec = getattr(annotation, "placement_spec", None)
        if spec is not None and spec.side in sides:
            dimensions.append((name, annotation, spec))
    exact_ink = [*leaders, *((name, annotation) for name, annotation, _spec in dimensions)]
    exact_names = {name for name, _annotation in exact_ink}
    exact_boxes = [box for name, box in named if name in exact_names]
    coarse = [(name, box) for name, box in named if name not in exact_names]
    for name, _annotation, spec in dimensions:
        if axis == "y":
            station = float(spec.p1[1]) + (1 if spec.side == "above" else -1) * float(
                spec.distance
            )
            lo, hi = sorted((float(spec.p1[0]), float(spec.p2[0])))
            coarse.append((name, (lo, station, hi, station)))
        else:
            station = float(spec.p1[0]) + (1 if spec.side == "right" else -1) * float(
                spec.distance
            )
            lo, hi = sorted((float(spec.p1[1]), float(spec.p2[1])))
            coarse.append((name, (station, lo, station, hi)))
    return coarse, exact_ink, exact_boxes


def balloon_annotation_label_boxes(dwg, view):
    """Return the surviving annotation label boxes in *view*.

    Public label metadata keeps dimensions compact (witness lines are not
    obstacles); objects without it, such as notes/tables/section arrows, fall
    back to their rendered box. Collecting once keeps guarded balloon solving
    free of temporary OCC glyph construction and shared-box-cache churn.
    """
    cache = getattr(dwg, "box_cache", None)
    boxes = []
    for name, annotation in dwg.iter_annotations():
        owner = dwg.view_of(name)
        if owner is not None and owner != view:
            continue
        if getattr(annotation, "is_centerline", False):
            continue
        try:
            label_box = getattr(annotation, "label_bbox", None)
        except Exception:  # noqa: BLE001 — external leader metadata may be unreadable
            label_box = None
        if label_box is None:
            label_box = _geom_box(annotation, cache)
        if label_box is None:
            continue
        boxes.append(label_box)
    return tuple(boxes)


def balloon_geometry_hits_annotation_labels(glyph_boxes, segments, label_boxes) -> bool:
    """Whether any rendered glyph component or real shaft crosses a retained label."""
    return any(
        any(_boxes_overlap(glyph_box, label_box) for glyph_box in glyph_boxes)
        or any(_segment_clips_box(start, end, label_box, pad=0.0) for start, end in segments)
        for label_box in label_boxes
    )


def box_within_page_and_clear(bb, page_box, obstacles) -> bool:
    """True when ``bb`` is fully inside *page_box* and hits none of *obstacles*
    (:func:`_box_hits`) — the safety check a shifted label must pass before a
    caller accepts it over an unshifted fallback (#129: this was inline
    in ``holes.py``'s ``_clear_and_validate`` and untestable in isolation)."""
    return (
        bb is not None
        and bb[0] >= page_box[0]
        and bb[1] >= page_box[1]
        and bb[2] <= page_box[2]
        and bb[3] <= page_box[3]
        and not _box_hits(bb, obstacles)
    )


def _joined_dimension_leader_segments(candidate, annotation, candidate_segments):
    """A witness may continue a leader shaft, including its shelf at the elbow."""
    if not isinstance(candidate, (Dimension, SafeDimension)) or type(annotation) is not Leader:
        return lambda _start, _end, _fixed_start, _fixed_end: False
    tip, elbow = getattr(annotation, "tip", None), getattr(annotation, "elbow", None)
    if tip is None or elbow is None:
        return lambda _start, _end, _fixed_start, _fixed_end: False

    def collinear(a, b, c, d):
        def cross(point):
            return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])

        return abs(cross(c)) <= 1e-9 and abs(cross(d)) <= 1e-9

    if not any(collinear(start, end, tip, elbow) for start, end in candidate_segments):
        return lambda _start, _end, _fixed_start, _fixed_end: False

    def joined(start, end, fixed_start, fixed_end):
        if collinear(start, end, fixed_start, fixed_end) and collinear(
            fixed_start, fixed_end, tip, elbow
        ):
            return True
        return (
            tuple(elbow[:2]) in (tuple(fixed_start[:2]), tuple(fixed_end[:2]))
            and collinear(start, end, elbow, elbow)
            and min(start[0], end[0]) - 1e-9 <= elbow[0] <= max(start[0], end[0]) + 1e-9
            and min(start[1], end[1]) - 1e-9 <= elbow[1] <= max(start[1], end[1]) + 1e-9
        )

    return joined


def annotation_ink_clear(dwg, candidate, *, view=None, additional=(), against=None) -> bool:
    """Whether *candidate* clears exact decomposable ink and conservative fixed furniture.

    A diagonal dimension cannot use the strip system's conservative AABB occupancy as its
    acceptance predicate: each slanted line's box contains a large empty triangle, so a clean
    rotated dimension is rejected merely because another annotation sits in that empty area.
    Public segment metadata and exact label polygons avoid those false hulls while preserving
    the crossing-free contract: labels remain clear in both directions and non-crossable shafts
    may not intersect. Dimension/dimension and centre-furniture intersections keep their
    existing explicit exemptions. An annotation with no trustworthy segments is not empty:
    tables, title furniture and other compounds retain their conservative component boxes.
    """
    candidate_segments = segments_of(candidate)
    try:
        candidate_label = getattr(candidate, "label_bbox", None)
    except Exception:  # noqa: BLE001 — unreadable candidate ink must fail closed
        return False
    candidate_region = crossable_region(
        candidate_label,
        item=candidate,
        segments=candidate_segments,
    )
    if candidate_region is None:
        return False
    placed = dwg.iter_annotations() if against is None else against
    for name, annotation in chain(placed, ((None, item) for item in additional)):
        owner = dwg.view_of(name) if name is not None else view
        if view is not None and owner is not None and owner != view:
            continue
        annotation_segments = segments_of(annotation)
        try:
            annotation_label = getattr(annotation, "label_bbox", None)
        except Exception:  # noqa: BLE001 — unreadable fixed ink must fail closed
            return False
        annotation_region = crossable_region(
            annotation_label,
            item=annotation,
            segments=annotation_segments,
        )
        crossable_strokes = (
            isinstance(annotation, (Dimension, SafeDimension, AngularDimension))
            or isinstance(annotation, DimensionInkCandidate)
            or (type(annotation).__name__ in CROSSABLE_TYPES)
        )
        if annotation_label is not None and annotation_region is None:
            try:
                conservative_label_hit = _boxes_overlap(candidate_label, annotation_label) or any(
                    _segment_clips_box(start, end, annotation_label, pad=0.0)
                    for start, end in candidate_segments
                )
            except Exception:  # noqa: BLE001 — malformed fixed labels must fail closed
                return False
            if conservative_label_hit:
                return False
        if annotation_region is not None and _boxes_overlap(candidate_label, annotation_label):
            return False
        if annotation_region is not None and (
            crossing_length(candidate_segments, annotation_region) >= MIN_CROSSING_MM
        ):
            return False
        if annotation_segments and (
            crossing_length(annotation_segments, candidate_region) >= MIN_CROSSING_MM
        ):
            return False
        if annotation_segments:
            joined = _joined_dimension_leader_segments(candidate, annotation, candidate_segments)
            candidate_tip = getattr(candidate, "tip", None)
            annotation_tip = getattr(annotation, "tip", None)
            candidate_elbow = getattr(candidate, "elbow", None)
            annotation_elbow = getattr(annotation, "elbow", None)
            shares_leader_trunk = (
                type(candidate) is Leader
                and type(annotation) is Leader
                and candidate_tip is not None
                and annotation_tip is not None
                and candidate_elbow is not None
                and annotation_elbow is not None
                and math.dist(tuple(candidate_tip[:2]), tuple(annotation_tip[:2])) <= 1e-6
                and abs(
                    (candidate_elbow[0] - candidate_tip[0])
                    * (annotation_elbow[1] - annotation_tip[1])
                    - (candidate_elbow[1] - candidate_tip[1])
                    * (annotation_elbow[0] - annotation_tip[0])
                )
                <= 1e-6
                and (
                    (candidate_elbow[0] - candidate_tip[0])
                    * (annotation_elbow[0] - annotation_tip[0])
                    + (candidate_elbow[1] - candidate_tip[1])
                    * (annotation_elbow[1] - annotation_tip[1])
                )
                > 0.0
            )
            # Collinear, same-direction leaders from one physical target form a trunk with
            # separate shelves. Their label-region checks above remain authoritative. Exact
            # type checks exclude routed leaders, whose paths can cross again away from the tip.
            if (
                not crossable_strokes
                and not shares_leader_trunk
                and any(
                    _segments_cross_or_overlap(start, end, fixed_start, fixed_end)
                    and not joined(start, end, fixed_start, fixed_end)
                    for start, end in candidate_segments
                    for fixed_start, fixed_end in annotation_segments
                )
            ):
                return False
            continue
        if crossable_strokes or is_page_spanning_rider(annotation):
            continue
        try:
            fixed_boxes = annotation_obstacle_boxes(dwg, annotation)
        except Exception:  # noqa: BLE001 — malformed fixed ink must fail closed
            fixed_boxes = ()
        if not fixed_boxes:
            return False
        if any(
            _boxes_overlap(candidate_label, fixed_box)
            or any(
                _segment_clips_box(start, end, fixed_box, pad=0.0)
                for start, end in candidate_segments
            )
            for fixed_box in fixed_boxes
        ):
            return False
    return True


def annotation_text_ink_clear(dwg, candidate) -> bool:
    """Protect labels in both directions without treating every shaft crossing as text damage.

    The immediate dense-hole callout path retains its Policy-B shaft fallback, but
    cannot let a later leader label be struck by an already-placed pitch witness
    (or let its own shaft cross settled text). This uses the same exact segment and
    label metadata as the shared ink predicate; no AABB of an entire dimension is
    used as a proxy for its sparse rendered ink.
    """
    try:
        label = getattr(candidate, "label_bbox", None)
        segments = segments_of(candidate)
    except Exception:  # noqa: BLE001 — unreadable candidate ink cannot prove text clear
        return False
    for _name, annotation in dwg.iter_annotations():
        try:
            fixed_label = getattr(annotation, "label_bbox", None)
            fixed_segments = segments_of(annotation)
        except Exception:  # noqa: BLE001 — unreadable fixed ink cannot prove text clear
            return False
        if label is not None and fixed_label is not None and _boxes_overlap(label, fixed_label):
            return False
        if fixed_label is not None and any(
            _segment_clips_box(start, end, fixed_label, pad=0.0) for start, end in segments
        ):
            return False
        if label is not None and any(
            _segment_clips_box(start, end, label, pad=0.0) for start, end in fixed_segments
        ):
            return False
    return True


def view_label_clearance(dwg, view):
    """A label guard over the same projected edges the structural critic reads.

    Prepare the edges once per batch, outside candidate evaluation. Missing view
    geometry supplies no guard; unreadable geometry cannot establish clearance.
    """
    placed = getattr(dwg, "views", {}).get(view)
    if not placed or placed[0] is None:
        return None
    entries = _view_edge_entries(placed[0], {})
    return lambda box: entries is not None and not _edges_intersect_rect(entries, box)
