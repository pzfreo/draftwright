"""Section A-A and detail views (#138 / ADR 0005, P5a).

The cutting-plane section (ISO 128-44 arrows, ISO 128-50 hatching via
`_section_hatch_edges`/`_fuzzy_cut`) and the enlarged detail view. Pass
functions take the drawing duck-typed as `dwg`; imports stay below annotate.
"""

from __future__ import annotations

from build123d import (
    Arrow,
    Box,
    Compound,
    Edge,
    GeomType,
    HeadType,
    Mode,
    Pos,
    Vector,
)
from build123d_drafting.helpers import (
    Centerline,
    Note,
    ViewCoordinates,
    format_drawing_scale,
    view_axes,
)
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.TopTools import TopTools_ListOfShape

from draftwright._core import (
    _MIN_STEP_SEP_MM,
    _TB_CLEAR,
    _TB_H,
    Analysis,
    DetailRequest,
    _dim,
    _fmt,
    _iso_bbox,
    _largest_empty_rect,
    _legible_steps,
    _log,
)

_DETAIL_LETTERS = "ABCDEFGH"


def _section_hatch_edges(face, SX, SZ, spacing):
    """Return 45° ISO 128-50 hatch Edge objects for one cut face in page coords.

    Uses the even-odd rule: all boundary wires (outer + inner) are traversed;
    intersections of each hatch line with the boundary are sorted and filled in
    alternating spans.  Curved edges are tessellated to straight segments so
    circular hole outlines clip correctly.
    """
    segs = []
    for wire in [face.outer_wire()] + list(face.inner_wires()):
        for edge in wire.edges():
            if edge.geom_type == GeomType.LINE:
                pts = [edge.position_at(0), edge.position_at(1)]
            else:
                n = max(8, int(edge.length / spacing) + 1)
                pts = [edge.position_at(i / n) for i in range(n + 1)]
            ppts = [(SX(v.X), SZ(v.Z)) for v in pts]
            for j in range(len(ppts) - 1):
                segs.append((ppts[j], ppts[j + 1]))

    if not segs:
        return []

    all_xs = [p[0] for s in segs for p in s]
    all_ys = [p[1] for s in segs for p in s]
    # 45° lines satisfy y − x = c; step by spacing (perpendicular to lines)
    step = spacing
    c_min = min(all_ys) - max(all_xs) - step
    c_max = max(all_ys) - min(all_xs) + step

    result = []
    c = c_min + step
    while c < c_max:
        hits = []
        for (x1, y1), (x2, y2) in segs:
            denom = (y2 - y1) - (x2 - x1)
            if abs(denom) < 1e-9:
                continue
            t = (c - (y1 - x1)) / denom
            if -1e-6 <= t < 1 - 1e-6:  # half-open: each shared vertex counted once
                hits.append(x1 + t * (x2 - x1))
        hits.sort()
        for i in range(0, len(hits) - 1, 2):
            xa, xb = hits[i], hits[i + 1]
            if xb - xa > 0.2:
                result.append(Edge.make_line(Vector(xa, xa + c, 0), Vector(xb, xb + c, 0)))
        c += step
    return result


def _fuzzy_cut(body, cutter, fuzzy: float = 1e-3):
    """Boolean-subtract *cutter* from *body* with a fuzzy tolerance.

    Returns a build123d ``Solid`` (or ``Compound`` of solids), or ``None`` if the
    boolean fails or yields no solid.

    build123d's plain ``body - cutter`` runs an exact (zero-fuzzy) boolean which
    raises an *uncatchable* ``Standard_DomainError`` on some cast geometry — the
    C++ exception escapes to ``libc++abi: terminating`` (SIGABRT) and a
    surrounding ``try/except`` never sees it, killing the whole drawing (NIST
    CTC-04 section cut, #20). A small fuzzy value makes ``BRepAlgoAPI_Cut`` robust
    on the same input. We drive the OCCT op directly (build123d's
    ``Shape._bool_op`` result-processing aborts on the resulting compound) and
    keep only the solids, so non-solid boolean artefacts can't crash the
    downstream hidden-line projection.
    """
    args = TopTools_ListOfShape()
    args.Append(body.wrapped)
    tools = TopTools_ListOfShape()
    tools.Append(cutter.wrapped)
    op = BRepAlgoAPI_Cut()
    op.SetArguments(args)
    op.SetTools(tools)
    op.SetFuzzyValue(fuzzy)
    op.Build()
    if not op.IsDone():
        return None
    solids = Compound(op.Shape()).solids()
    if not solids:
        return None
    return solids[0] if len(solids) == 1 else Compound(children=list(solids))


def _add_section_view(dwg, a: Analysis, section):
    """Render the planned full section A–A (#94, #207).

    The *trigger* + cut-plane row are decided by the planner (`plan_sections` →
    `SectionPlan`, ADR 0008 Amendment 4); this is the shared rendering machinery it
    feeds. The cut plane (normal Y at ``section.cut_y``, parallel to the front view)
    removes material on the viewer's side so the cut face shows the hole profiles as
    visible line-work. Placed right of the side view when there is room (skipped with
    a log otherwise), captioned, marked with ISO 128-44 cutting-plane arrows and 'A'
    letters on the plan view, and filled with ISO 128-50 45° hatching on the cut face.
    """
    y_star = section.cut_y

    # room check: same row as the front/side views, to the right — past any
    # side-view callout labels already placed there.
    # 12.0 mm floor: conservative minimum half-width so very narrow sections
    # have enough room for the "SECTION A–A" caption and arrows.
    half_w = max(a.x_size * a.SCALE / 2, 12.0)
    half_h = a.z_size * a.SCALE / 2
    side_vis, side_hid = dwg.views["side"]
    side_right = side_vis.bounding_box().max.X
    if side_hid:
        side_right = max(side_right, side_hid.bounding_box().max.X)
    left_edge = side_right + 10
    for name, ann in dwg.iter_annotations():
        # past side-view callout labels and the height/step dim ladder
        if name.startswith(("hc_side", "dim_height", "dim_step")) and getattr(
            ann, "label_bbox", None
        ):
            left_edge = max(left_edge, ann.label_bbox[2] + 6)
    pos_x = left_edge + half_w
    iso_x0, iso_y0, _, _ = _iso_bbox(dwg)
    right_limit = a.PAGE_W - a.margin
    if a.FV_Y + half_h + 6 > iso_y0 - 2:
        right_limit = min(right_limit, iso_x0 - 4)
    tb_left = a.PAGE_W - a.TB_W - _TB_CLEAR
    if a.FV_Y - half_h - 10 < _TB_CLEAR + _TB_H and pos_x + half_w > tb_left - 4:
        _log.info("Section A–A skipped (would collide with the title block)")
        _clear_section_reservation(dwg)
        return
    if pos_x + half_w > right_limit:
        _log.warning(
            "Section A–A skipped (no room right of the side view; "
            "a wider step-dimension corridor may have reduced the available space)"
        )
        _clear_section_reservation(dwg)
        return

    big = 4 * a.bbox_max
    # STEP imports with PMI carry annotation curves beside the solid, and a
    # mixed-dimension compound cannot be cut — section the solids only, and
    # never let a failed boolean abort the whole drawing
    solids = a.part.solids()
    if not solids:
        _log.info("Section A–A skipped (no solid bodies to cut)")
        _clear_section_reservation(dwg)
        return
    body = solids[0] if len(solids) == 1 else Compound(children=list(solids))
    try:
        # Fuzzy boolean: the exact `body - Box(...)` aborts uncatchably
        # (Standard_DomainError) on some cast geometry — see _fuzzy_cut / #20.
        keep_behind = _fuzzy_cut(body, Pos(a.cx, y_star - big / 2, a.cz) * Box(big, big, big))
    except Exception as exc:  # noqa: BLE001 — OCC booleans raise broadly
        _log.warning("Section A–A skipped (cut failed: %s)", exc)
        _clear_section_reservation(dwg)
        return
    if keep_behind is None:
        _log.warning("Section A–A skipped (boolean cut produced no solid)")
        _clear_section_reservation(dwg)
        return
    camera = (dwg.look_at[0], dwg.look_at[1] - dwg.dist, dwg.look_at[2])
    dwg.add_view("section_aa", keep_behind, camera, (0, 0, 1), (pos_x, a.FV_Y))
    dwg.add(
        Note("SECTION A–A", (pos_x, a.FV_Y - half_h - 7), dwg.draft),
        "section_caption",
    )

    # cutting-plane line + identification letters on the plan view
    PX = a.proj.plan_x
    PY = a.proj.plan_y

    y_page = PY(y_star)
    # the line and its letters must clear pattern centrelines that sweep
    # past the part outline (a corner-hole bolt circle is always wider)
    ext_x0, ext_x1 = PX(a.bb.min.X), PX(a.bb.max.X)
    for name, ann in dwg.iter_annotations():
        if name.startswith("bc_plan"):
            cb = ann.bounding_box()
            if cb.min.Y - 3 < y_page < cb.max.Y + 3:
                ext_x0 = min(ext_x0, cb.min.X)
                ext_x1 = max(ext_x1, cb.max.X)
    x0, x1 = ext_x0 - 4, ext_x1 + 4
    dwg.add(Centerline((x0, y_page, 0), (x1, y_page, 0)), "section_line")

    # The row was reserved early (ADR 0009 P5 strand 3) with a conservative
    # (unwidened) x-extent so the plan-view hole-callout carve could avoid it
    # before this function runs — replace it with the final, possibly-wider
    # geometry now that the bolt-circle extent is known.
    _clear_section_reservation(dwg)
    _add_cutting_plane_arrows(dwg, y_page, x0, x1)
    _add_section_letters(dwg, y_page, x0, x1)

    # ISO 128-50: 45° hatching on the cut face, in page coordinates. The section
    # is drawn in its own frame: X is offset to the section's page slot (pos_x),
    # while the height axis matches the front view — so SZ is exactly front_z.
    def SX(wx):
        return pos_x + (wx - a.cx) * a.SCALE

    SZ = a.proj.front_z

    hatch_spacing = dwg.draft.font_size * 1.5
    cut_faces = [f for f in keep_behind.faces() if f.normal_at().Y < -0.9]
    hatch_edges = []
    for cf in cut_faces:
        hatch_edges.extend(_section_hatch_edges(cf, SX, SZ, hatch_spacing))
    if hatch_edges:
        hatch = Compound(children=hatch_edges)
        hatch.is_section_hatch = True  # exempt from view_annotation_overlap lint
        dwg.add(hatch, "section_hatch")


def _add_cutting_plane_arrows(dwg, y_page, x0, x1):
    """ISO 128-44 cutting-plane end indicators at ``(x0, y_page)``/``(x1, y_page)`` —
    thick wing stubs with solid filled arrowheads pointing in the viewing direction
    (−Y). Named ``section_arrow_{left,right}``/``section_wing_{left,right}``, shared
    between the early row reservation (:func:`_reserve_section_row`) and the final
    section render (:func:`_add_section_view`, ADR 0009 P5 strand 3)."""
    arrow_sz = dwg.draft.arrow_length
    wing_h = 2.5 * arrow_sz  # perpendicular stub length
    for x_end, side in ((x0, "left"), (x1, "right")):
        tip_y = y_page - wing_h
        shaft = Edge.make_line(Vector(x_end, y_page, 0), Vector(x_end, tip_y, 0))
        filled = Arrow(
            arrow_size=arrow_sz,
            shaft_path=shaft,
            shaft_width=dwg.draft.line_width,
            head_at_start=False,
            head_type=HeadType.STRAIGHT,
            mode=Mode.PRIVATE,
        )
        dwg.add(Compound(children=list(filled.faces())), f"section_arrow_{side}")
        dwg.add(
            Compound(children=[Edge.make_line(Vector(x_end, y_page, 0), Vector(x_end, tip_y, 0))]),
            f"section_wing_{side}",
        )


def _add_section_letters(dwg, y_page, x0, x1):
    """The 'A' identification letters above the cutting-plane line ends, clear of
    any callout leaders. Named ``section_a_{left,right}`` — shared between the
    early row reservation and the final section render, same as
    :func:`_add_cutting_plane_arrows` (ADR 0009 P5 strand 3): a callout's full
    footprint can land on the letters just as easily as on the arrows, so both
    need to be visible to the plan-view callout carve before it places."""
    lift = dwg.draft.font_size * 1.4
    dwg.add(Note("A", (x0 - 3, y_page + lift), dwg.draft), "section_a_left")
    dwg.add(Note("A", (x1 + 3, y_page + lift), dwg.draft), "section_a_right")


def _clear_section_reservation(dwg) -> None:
    """Remove the placeholder :func:`_reserve_section_row` may have added, if
    present (idempotent — a no-op once :func:`_add_section_view` has already
    replaced it, or if no section ever triggered)."""
    existing = dwg.annotations()
    for name in (
        "section_arrow_left",
        "section_arrow_right",
        "section_wing_left",
        "section_wing_right",
        "section_a_left",
        "section_a_right",
    ):
        if name in existing:
            dwg.remove(name)


def _reserve_section_row(dwg, a: Analysis, section) -> None:
    """Reserve the section A–A cutting-plane arrows' row BEFORE the plan-view hole
    callouts place (ADR 0009 P5 strand 3, burns down the ``bracket`` fixture's
    ``hc_plan0``/``section_arrow_right`` overlap in ``tests/test_layout_cleanliness.py``).

    ``_add_section_view`` runs last deliberately (its own room check clears
    everything already placed) — so until now, the plan-view hole-callout carve's
    ``strip_obstacles`` had no way to see the section arrows it hadn't drawn yet,
    the textbook invisible-occupant defect ADR 0009 targets. Placing a conservative
    placeholder early (the arrows' actual row, at the un-widened part-bbox extent —
    the bolt-circle widening in ``_add_section_view`` depends on furniture
    ``_annotate_holes`` hasn't placed yet either, so it is not yet knowable) gives
    the carve a real obstacle to avoid; ``_add_section_view`` replaces it with the
    final, possibly-wider geometry once that furniture exists.

    A no-op when *section* is ``None`` (no section triggers) — nothing is reserved,
    and ``_add_section_view`` is never called either.

    **Known residual (review finding, #351 P5 strand 3, filed as #366):** the
    un-widened reservation is a real gap, not just a conservative approximation
    — for a part whose bolt-circle centreline crosses this exact Y-row, the
    FINAL arrow can widen beyond what was reserved, and the callout carve never
    re-checks against that later growth. Rare in practice (needs a bolt-circle
    pattern at the precise section-cut row) and no corpus fixture exercises it
    today; a full fix needs a second, post-widen verification pass, which is
    out of scope for this PR."""
    if section is None:
        return
    PX, PY = a.proj.plan_x, a.proj.plan_y
    y_page = PY(section.cut_y)
    x0, x1 = PX(a.bb.min.X) - 4, PX(a.bb.max.X) + 4
    _add_cutting_plane_arrows(dwg, y_page, x0, x1)
    _add_section_letters(dwg, y_page, x0, x1)


def _render_detail(dwg, a: Analysis, req: DetailRequest, view_name: str, letter: str) -> bool:
    """Generic detail renderer (#307) — the single crop → project → place → caption
    → mark machinery both the prismatic step detail (#42) and the turned-head detail
    (#304) flow through. Crops the part to ``req``'s band along ``req.axis``, projects
    it front-on at the detail scale into the largest free rectangle, captions and
    marks it, then calls ``req.redraw`` to draw the feature's own dimensions in the
    placed detail view. Every risky boolean/projection is wrapped and returns
    ``False`` (drawing unchanged) rather than aborting; ``True`` when the detail is
    placed. Mirrors :func:`_add_section_view`'s skip-with-log discipline."""
    # Detail scale: smallest standard multiple in [2, 5, 10] of sheet scale that
    # makes the region legible (>= the requested scale), always >= 2x.
    detail_scale = a.SCALE * 2
    for factor in (2, 5, 10):
        detail_scale = a.SCALE * factor
        if detail_scale >= req.scale_needed:
            break

    # Crop to the band along req.axis (two fuzzy cuts). Solids only — a mixed
    # compound (PMI curves) cannot be cut.
    solids = a.part.solids()
    if not solids:
        _log.info("Detail %s skipped (no solid bodies to crop)", letter)
        return False
    body = solids[0] if len(solids) == 1 else Compound(children=list(solids))
    big = 4 * a.bbox_max
    idx = "xyz".index(req.axis)

    def _cut(edge):
        c = [a.cx, a.cy, a.cz]
        c[idx] = edge
        return Pos(*c) * Box(big, big, big)

    try:
        cropped = _fuzzy_cut(body, _cut(req.lo - big / 2))
        if cropped is not None:
            cropped = _fuzzy_cut(cropped, _cut(req.hi + big / 2))
    except Exception as exc:  # noqa: BLE001 — OCC booleans raise broadly
        _log.warning("Detail %s skipped (crop failed: %s)", letter, exc)
        return False
    if cropped is None:
        _log.warning("Detail %s skipped (boolean crop produced no solid)", letter)
        return False

    # Placement: largest empty rectangle avoiding placed views + the title block.
    drawable = (a.margin, a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin)
    obstacles = []
    for vis, hid in dwg.views.values():
        for shp in (vis, hid):
            if shp is None:
                continue
            vb = shp.bounding_box()
            obstacles.append((vb.min.X, vb.min.Y, vb.max.X, vb.max.Y))
    # Also avoid placed dimension *labels* (not bare centre/leader lines) — a detail
    # landing on a front-view callout reads as illegible and the repack can't see it
    # (detail_* views are not orthographic) (#307 review). Text boxes only.
    for _name, o in dwg.iter_annotations():
        lb = getattr(o, "label_bbox", None)
        if lb is not None:
            obstacles.append(tuple(lb))
    obstacles.append(
        (a.PAGE_W - a.TB_W - _TB_CLEAR, a.margin, a.PAGE_W - _TB_CLEAR, _TB_CLEAR + _TB_H)
    )
    rx0, ry0, rx1, ry1 = _largest_empty_rect(drawable, obstacles)
    rect_w, rect_h = rx1 - rx0, ry1 - ry0

    # Footprint = the projected cropped band + the request's annotation pads (the
    # dim ladder/chain) + the caption row. Shrink the scale to fit if necessary.
    cb = cropped.bounding_box()
    view_w, view_h = (cb.max.X - cb.min.X), (cb.max.Z - cb.min.Z)
    cap_h = 8.0

    def _pads(s):  # annotation bands may depend on the scale (the prismatic ladder)
        return req.pads(s) if req.pads is not None else (0.0, req.pad_top)

    def _fits(s):
        pr, pt = _pads(s)
        return view_w * s + pr <= rect_w and view_h * s + pt + a.DIM_PAD + cap_h <= rect_h

    while detail_scale > a.SCALE * 1.2 and not _fits(detail_scale):
        detail_scale -= a.SCALE
    if detail_scale <= a.SCALE * 1.2 or not _fits(detail_scale):
        _log.info("Detail %s skipped (no room)", letter)
        return False
    pad_right, pad_top = _pads(detail_scale)

    # Centre the whole footprint: bias left by the right pad, and offset for the
    # asymmetric top pad (annotations above) vs the caption (below).
    DX = (rx0 + rx1) / 2 - pad_right / 2
    DY = (ry0 + ry1) / 2 - (pad_top - cap_h) / 2

    # Project the cropped band front-on (look from −Y, up +Z) at detail_scale around
    # its own centroid; rebuild ViewCoordinates so dwg.at(view_name, ...) maps
    # world→page at the detail scale.
    dcx = (cb.min.X + cb.max.X) / 2
    dcy = (cb.min.Y + cb.max.Y) / 2
    dcz = (cb.min.Z + cb.max.Z) / 2
    la = (dcx * detail_scale, dcy * detail_scale, dcz * detail_scale)
    dist_d = a.bbox_max * detail_scale + 100
    camera = (la[0], la[1] - dist_d, la[2])
    try:
        band_s = cropped.scale(detail_scale)
        dwg.add_view(view_name, band_s, camera, (0, 0, 1), (DX, DY), look_at=la, scaled=True)
    except Exception as exc:  # noqa: BLE001 — projection raises broadly on cast geometry
        _log.warning("Detail %s skipped (projection failed: %s)", letter, exc)
        return False
    dwg._coords[view_name] = ViewCoordinates(
        view_axes(camera, (0, 0, 1), la), DX, DY, dcx, dcy, dcz, detail_scale
    )

    # The feature draws its own dims inside the detail. If nothing legible lands even
    # at the detail scale, roll the view back rather than committing an empty DETAIL
    # box — the request is then simply dropped (the main view already locates the
    # head/block inline, so lint reports any un-located interior) (#307 review).
    if not req.redraw(dwg, view_name, detail_scale):
        dwg.views.pop(view_name, None)
        dwg._coords.pop(view_name, None)
        _log.info("Detail %s skipped (no legible dims at the detail scale)", letter)
        return False

    # Marker on the front view around the band (axis-aware) + letter.
    FX, FZ = a.proj.front_x, a.proj.front_z
    if req.axis == "z":  # band runs along page-y
        mx0, mx1, my0, my1 = FX(a.bb.min.X), FX(a.bb.max.X), FZ(req.lo), FZ(req.hi)
    else:  # x band runs along page-x
        mx0, mx1, my0, my1 = FX(req.lo), FX(req.hi), FZ(a.bb.min.Z), FZ(a.bb.max.Z)
    marker = Compound(
        children=[
            Edge.make_line(Vector(mx0, my0, 0), Vector(mx1, my0, 0)),
            Edge.make_line(Vector(mx1, my0, 0), Vector(mx1, my1, 0)),
            Edge.make_line(Vector(mx1, my1, 0), Vector(mx0, my1, 0)),
            Edge.make_line(Vector(mx0, my1, 0), Vector(mx0, my0, 0)),
        ]
    )
    marker.is_centerline = True  # furniture, not a dimension — exempt from overlap lint
    dwg.add(marker, f"detail_marker_{letter}")
    dwg.add(Note(letter, (mx1 + 3, my1 + 2), dwg.draft), f"detail_marker_label_{letter}")

    # Caption below the placed view (anchored to its real footprint).
    dvb = dwg.views[view_name][0].bounding_box()
    dwg.add(
        Note(
            f"DETAIL {letter} — SCALE {format_drawing_scale(detail_scale)}",
            ((dvb.min.X + dvb.max.X) / 2, dvb.min.Y - cap_h),
            dwg.draft,
        ),
        f"detail_caption_{letter}",
    )
    return True


def _resolve_details(dwg, a: Analysis) -> None:
    """Resolve every queued :class:`DetailRequest` (#307) through the one generic
    detailer, lettering DETAIL A/B/… On a placement bail-out nothing is drawn for that
    request — the main view already carries the located head/block inline, so lint
    reports any un-located interior rather than coverage being silently lost. Clears
    the queue."""
    reqs = list(getattr(dwg, "_detail_requests", ()) or ())
    dwg._detail_requests = []
    n_placed = 0  # letters advance only on a successful placement — no A/B gaps (#307 review)
    for req in reqs:
        if n_placed >= len(_DETAIL_LETTERS):
            _log.info("detail request '%s' dropped: detail letters A–H exhausted", req.kind)
            continue
        letter = _DETAIL_LETTERS[n_placed]
        if _render_detail(dwg, a, req, f"detail_{letter.lower()}", letter):
            n_placed += 1


def _request_prismatic_detail(dwg, a: Analysis) -> None:
    """Queue a detail of a prismatic part's crowded step-height band (#42), routed
    through the unified pipeline (#307).

    Fires on the "step"/"illegible" ``Escalation`` `render_height_ladder`
    (from_model.py) appends (ADR 0009 Amdt 1, #351 PR-4b) rather than
    independently recomputing the legibility gate from ``a.step_zs`` — a uniform
    staircase (``_detect_step_repeat``) collapses to one representative dim with
    no drop at all, and re-deriving legibility straight from the raw z-list here
    missed that case, queuing a spurious detail even though nothing was actually
    dropped (a real bug the escalation routing fixes as a side effect).

    Prismatic only, by construction: `render_height_ladder` never emits this
    escalation for a turned part (no `StepLevelFeature`). A crowded **Z-turned**
    step-length chain has its own, separate drop in `_draw_step_chain`'s vertical
    branch (`return 0`) — this function no longer accidentally, unreliably papers
    over that with prismatic-semantics dims (wrong anchor/labeling for a turned
    chain). That drop is now reported (a `step_dim_dropped` lint warning, #362);
    still outstanding is a Z-turned-appropriate *detail* remedy, analogous to the
    X-turned crowded-head block + `DetailRequest` this docstring's sibling,
    `render_step_lengths`, already has.

    The redraw re-draws the step-height ladder in the detail view at the
    enlarged scale."""
    if len(a.step_zs) < 2:
        return
    if not any(
        e.kind == "step" and e.reason == "illegible" for e in getattr(dwg, "_escalations", ())
    ):
        return
    z0, z1 = min(a.step_zs), max(a.step_zs)
    pad = 0.08 * (z1 - z0) + 1.0
    band_lo, band_hi = max(a.bb.min.Z, z0 - pad), min(a.bb.max.Z, z1 + pad)
    s_zs = sorted(a.step_zs)
    min_gap = min(b - aa for aa, b in zip(s_zs, s_zs[1:]))
    # World→page scale that renders the closest gap at the legibility floor — no sheet
    # factor (detail_scale is itself an absolute world→page scale). (#307 review)
    scale_needed = _MIN_STEP_SEP_MM / min_gap if min_gap > 0 else float("inf")
    step_pad = _MIN_STEP_SEP_MM

    def pads(detail_scale):  # one ladder rung per step legible at this scale, + overall
        return (
            (len(_legible_steps(a.step_zs, a.bb.min.Z, detail_scale)[0]) + 1) * step_pad + 6,
            0.0,
        )

    def redraw(dwg, view, detail_scale):  # returns the count placed (for rollback, #307)
        det_kept, _ = _legible_steps(a.step_zs, a.bb.min.Z, detail_scale)
        ladder = dwg.at(view, a.bb.max.X, a.cy, a.bb.min.Z)[0] + 2
        placed = 0
        for i, z in enumerate([*det_kept, a.bb.max.Z]):
            label = _fmt(a.z_size) if z == a.bb.max.Z else _fmt(z - a.bb.min.Z)
            try:
                p_lo = dwg.at(view, a.bb.max.X, a.cy, a.bb.min.Z)
                p_hi = dwg.at(view, a.bb.max.X, a.cy, z)
                det_dim = _dim(
                    (ladder, p_lo[1], 0),
                    (ladder, p_hi[1], 0),
                    "right",
                    step_pad,
                    dwg.draft,
                    label=label,
                )
                det_dim._dw_scale = detail_scale  # detail scale, for label-vs-measured lint (#42)
                dwg.add(
                    det_dim, f"dim_{view}_step{i}", view=view
                )  # view-scoped name (#307 review)
                ladder += step_pad
                placed += 1
            except Exception as exc:  # noqa: BLE001 — placement may fail on degenerate geometry
                _log.info("detail step dim %d skipped (%s)", i, exc)
        return placed

    dwg._detail_requests.append(
        DetailRequest(
            axis="z",
            lo=band_lo,
            hi=band_hi,
            scale_needed=scale_needed,
            redraw=redraw,
            pads=pads,
            kind="prismatic-steps",
        )
    )
