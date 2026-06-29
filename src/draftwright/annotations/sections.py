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
    _dim,
    _fmt,
    _iso_bbox,
    _largest_empty_rect,
    _legible_steps,
    _log,
)


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
        return
    if pos_x + half_w > right_limit:
        _log.warning(
            "Section A–A skipped (no room right of the side view; "
            "a wider step-dimension corridor may have reduced the available space)"
        )
        return

    big = 4 * a.bbox_max
    # STEP imports with PMI carry annotation curves beside the solid, and a
    # mixed-dimension compound cannot be cut — section the solids only, and
    # never let a failed boolean abort the whole drawing
    solids = a.part.solids()
    if not solids:
        _log.info("Section A–A skipped (no solid bodies to cut)")
        return
    body = solids[0] if len(solids) == 1 else Compound(children=list(solids))
    try:
        # Fuzzy boolean: the exact `body - Box(...)` aborts uncatchably
        # (Standard_DomainError) on some cast geometry — see _fuzzy_cut / #20.
        keep_behind = _fuzzy_cut(body, Pos(a.cx, y_star - big / 2, a.cz) * Box(big, big, big))
    except Exception as exc:  # noqa: BLE001 — OCC booleans raise broadly
        _log.warning("Section A–A skipped (cut failed: %s)", exc)
        return
    if keep_behind is None:
        _log.warning("Section A–A skipped (boolean cut produced no solid)")
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

    # ISO 128-44: cutting-plane end indicators — thick wing stubs with solid
    # filled arrowheads at the tips pointing in the viewing direction (−Y).
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

    # 'A' letters sit above the line ends, clear of any callout leaders
    lift = dwg.draft.font_size * 1.4
    dwg.add(Note("A", (x0 - 3, y_page + lift), dwg.draft), "section_a_left")
    dwg.add(Note("A", (x1 + 3, y_page + lift), dwg.draft), "section_a_right")

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


def _add_detail_view(dwg, a: Analysis):
    """Enlarged detail of a stepped region whose shoulders the legibility gate
    dropped (#42).

    Trigger: the step-height legibility gate (#41) drops one or more shoulders
    because they are page-coincident at sheet scale. We crop the part to the
    full step Z-band, project it at a larger standard scale into the largest
    free rectangle on the sheet, re-draw the dropped step dimensions there at
    a scale where they separate, mark the region on the front view, and caption
    it "DETAIL A". Mirrors :func:`_add_section_view`: every risky boolean /
    projection is wrapped and skips-with-log rather than aborting the drawing,
    and the function returns early (drawing unchanged) when there is nothing to
    detail.
    """
    if len(a.step_zs) < 2:
        return
    kept, _ = _legible_steps(a.step_zs, a.bb.min.Z, a.SCALE)
    crowded = [z for z in a.step_zs if z not in set(kept)]
    if len(crowded) < 1:
        return

    # Region: the full step Z-band, padded and clamped to the part bbox.
    z0, z1 = min(a.step_zs), max(a.step_zs)
    pad = 0.08 * (z1 - z0) + 1.0
    band_lo = max(a.bb.min.Z, z0 - pad)
    band_hi = min(a.bb.max.Z, z1 + pad)

    # Detail scale: the smallest standard multiple in [2, 5, 10] of sheet scale
    # that separates the closest shoulder pair (≥ _MIN_STEP_SEP_MM). Always at
    # least 2× so the detail is a genuine enlargement.
    s_zs = sorted(a.step_zs)
    gaps = [b - aa for aa, b in zip(s_zs, s_zs[1:])]
    min_gap = min(gaps)
    need = _MIN_STEP_SEP_MM / min_gap if min_gap > 0 else float("inf")
    detail_scale = a.SCALE * 2
    for factor in (2, 5, 10):
        detail_scale = a.SCALE * factor
        if detail_scale >= need:
            break

    # Crop to the Z-band with two fuzzy cuts (remove z<band_lo and z>band_hi).
    # Solids only — a mixed-dimension compound (PMI curves) cannot be cut.
    solids = a.part.solids()
    if not solids:
        _log.info("Detail view skipped (no solid bodies to crop)")
        return
    body = solids[0] if len(solids) == 1 else Compound(children=list(solids))
    big = 4 * a.bbox_max
    try:
        cropped = _fuzzy_cut(body, Pos(a.cx, a.cy, band_lo - big / 2) * Box(big, big, big))
        if cropped is not None:
            cropped = _fuzzy_cut(cropped, Pos(a.cx, a.cy, band_hi + big / 2) * Box(big, big, big))
    except Exception as exc:  # noqa: BLE001 — OCC booleans raise broadly
        _log.warning("Detail view skipped (crop failed: %s)", exc)
        return
    if cropped is None:
        _log.warning("Detail view skipped (boolean crop produced no solid)")
        return

    # Placement: largest empty rectangle avoiding every placed view + title block.
    drawable = (a.margin, a.margin, a.PAGE_W - a.margin, a.PAGE_H - a.margin)
    obstacles = []
    for vis, hid in dwg.views.values():
        for shp in (vis, hid):
            if shp is None:
                continue
            vb = shp.bounding_box()
            obstacles.append((vb.min.X, vb.min.Y, vb.max.X, vb.max.Y))
    obstacles.append(
        (a.PAGE_W - a.TB_W - _TB_CLEAR, a.margin, a.PAGE_W - _TB_CLEAR, _TB_CLEAR + _TB_H)
    )
    rx0, ry0, rx1, ry1 = _largest_empty_rect(drawable, obstacles)
    rect_w, rect_h = rx1 - rx0, ry1 - ry0

    # Detail footprint at the chosen scale, including the step-dim ladder on the
    # right (one rung per kept step + the overall band height) and breathing
    # room for the caption below.  Shrink the scale to fit if necessary.
    step_pad = _MIN_STEP_SEP_MM
    n_rungs = len(_legible_steps(a.step_zs, a.bb.min.Z, detail_scale)[0]) + 1
    ladder_w = n_rungs * step_pad + 6
    while detail_scale > a.SCALE * 1.2:
        detail_w = a.x_size * detail_scale + ladder_w
        detail_h = (band_hi - band_lo) * detail_scale + a.DIM_PAD
        if detail_w <= rect_w and detail_h <= rect_h:
            break
        detail_scale -= a.SCALE
    n_rungs = len(_legible_steps(a.step_zs, a.bb.min.Z, detail_scale)[0]) + 1
    ladder_w = n_rungs * step_pad + 6
    detail_w = a.x_size * detail_scale + ladder_w
    detail_h = (band_hi - band_lo) * detail_scale + a.DIM_PAD
    if detail_scale <= a.SCALE * 1.2 or detail_w > rect_w or detail_h > rect_h:
        _log.info("Detail view skipped (no room)")
        return

    # Centre the view+ladder footprint in the rect; the view itself sits left of
    # centre so its right-hand ladder stays inside the chosen rectangle.
    DX = (rx0 + rx1) / 2 - ladder_w / 2
    DY = (ry0 + ry1) / 2

    # Project the cropped band front-on (look from −Y, up +Z), mirroring the
    # front view but at detail_scale around the band's own centroid (#42, like
    # _project_iso). Then rebuild ViewCoordinates so dwg.at("detail_a", ...)
    # maps world→page at the detail scale.
    cb = cropped.bounding_box()
    dcx = (cb.min.X + cb.max.X) / 2
    dcy = (cb.min.Y + cb.max.Y) / 2
    dcz = (cb.min.Z + cb.max.Z) / 2
    la = (dcx * detail_scale, dcy * detail_scale, dcz * detail_scale)
    dist_d = a.bbox_max * detail_scale + 100
    camera = (la[0], la[1] - dist_d, la[2])
    try:
        band_s = cropped.scale(detail_scale)
        dwg.add_view("detail_a", band_s, camera, (0, 0, 1), (DX, DY), look_at=la, scaled=True)
    except Exception as exc:  # noqa: BLE001 — projection raises broadly on cast geometry
        _log.warning("Detail view skipped (projection failed: %s)", exc)
        return
    dwg._coords["detail_a"] = ViewCoordinates(
        view_axes(camera, (0, 0, 1), la), DX, DY, dcx, dcy, dcz, detail_scale
    )

    # Caption below the detail.
    detail_bottom = DY - detail_h / 2
    dwg.add(
        Note(
            f"DETAIL A — SCALE {format_drawing_scale(detail_scale)}",
            (DX, detail_bottom - 7),
            dwg.draft,
        ),
        "detail_caption",
    )

    # Marker on the front view: a rectangle around the Z-band, with an 'A' label.
    FX = a.proj.front_x
    FZ = a.proj.front_z

    mx0, mx1 = FX(a.bb.min.X), FX(a.bb.max.X)
    my0, my1 = FZ(band_lo), FZ(band_hi)
    marker = Compound(
        children=[
            Edge.make_line(Vector(mx0, my0, 0), Vector(mx1, my0, 0)),
            Edge.make_line(Vector(mx1, my0, 0), Vector(mx1, my1, 0)),
            Edge.make_line(Vector(mx1, my1, 0), Vector(mx0, my1, 0)),
            Edge.make_line(Vector(mx0, my1, 0), Vector(mx0, my0, 0)),
        ]
    )
    marker.is_centerline = True  # furniture, not a dimension — exempt from overlap lint
    dwg.add(marker, "detail_marker")
    dwg.add(Note("A", (mx1 + 3, my1 + 2), dwg.draft), "detail_marker_label")

    # Detail dimensions: step heights now legible at detail_scale, plus the
    # overall band height. Baseline-ladder to the right of the detail, mirroring
    # the main-view step dims.
    det_kept, _ = _legible_steps(a.step_zs, a.bb.min.Z, detail_scale)
    base_x = dwg.at("detail_a", a.bb.max.X, dcy, a.bb.min.Z)[0] + 2
    ladder = base_x
    for i, z in enumerate(det_kept):
        try:
            p_lo = dwg.at("detail_a", a.bb.max.X, dcy, a.bb.min.Z)
            p_hi = dwg.at("detail_a", a.bb.max.X, dcy, z)
            det_dim = _dim(
                (ladder, p_lo[1], 0),
                (ladder, p_hi[1], 0),
                "right",
                step_pad,
                dwg.draft,
                label=_fmt(z - a.bb.min.Z),
            )
            # The detail view is drawn at detail_scale, not sheet scale; tag the
            # dim so lint() checks label-vs-measured against the right scale (#42).
            det_dim._dw_scale = detail_scale
            dwg.add(det_dim, f"dim_detail_step_{i}")
            ladder += step_pad
        except Exception as exc:  # noqa: BLE001 — placement may fail on degenerate geometry
            _log.info("dim_detail_step_%d skipped (%s)", i, exc)

    # Overall band height — outermost.
    try:
        p_lo = dwg.at("detail_a", a.bb.max.X, dcy, a.bb.min.Z)
        p_hi = dwg.at("detail_a", a.bb.max.X, dcy, a.bb.max.Z)
        det_dim = _dim(
            (ladder, p_lo[1], 0),
            (ladder, p_hi[1], 0),
            "right",
            step_pad,
            dwg.draft,
            label=_fmt(a.z_size),
        )
        det_dim._dw_scale = detail_scale  # detail view scale, for label-vs-measured lint (#42)
        dwg.add(det_dim, "dim_detail_height")
    except Exception as exc:  # noqa: BLE001 — placement may fail on degenerate geometry
        _log.info("dim_detail_height skipped (%s)", exc)
