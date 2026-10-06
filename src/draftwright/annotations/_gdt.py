"""Declared GD&T glyphs and shared-corridor candidate registration."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from build123d import Align, Location, Mode, ShapeList, Sketch, Text, Vector
from build123d_drafting import DatumFeature, FeatureControlFrame, SurfaceFinish, TextBlock
from build123d_drafting.helpers import DEFAULT_FONT_PATH

from draftwright._core import (
    Analysis,
    ViewZones,
    _font_safe_text,
    _text_line_spacing_em,
    _text_size,
)
from draftwright._geometry import _turned_profile_site, material_span
from draftwright.annotations._common import (
    PRIORITY,
    CorridorCandidate,
    DeferredCompactCandidate,
    _anno_box,
    _box_hits,
    annotation_ink_clear,
    register_corridor,
)
from draftwright.annotations._sheet_furniture import _title_block_box
from draftwright.annotations.routed import RoutedLeader
from draftwright.model.compiled import DimensionId
from draftwright.model.ir import (
    ChamferFeature,
    ControlFrame,
    DatumRef,
    FilletFeature,
    Finish,
    Note,
)

# GD&T aspect side-layer (ADR 4 (was 0011 §4)) — declared feature control frames / datum
# feature symbols / surface finishes. Placed as first-class ADR 2 (was 0009) corridor candidates,
# NOT through the dimension planner (their IR items carry no DimParameters). "note" is a
# free-text manufacturing note — the same leader-into-a-strip mechanism, glyph = text.
_GDT_KINDS = ("control_frame", "datum_ref", "finish", "note")
# Authored-intent run of the shared corridor ladder: GD&T frames tier BEYOND the
# feature-size (_SIZE_SUBCHAIN=0), datum-location (_LOC_SUBCHAIN=1), and overall
# envelope (_OVERALL_SUBCHAIN=2) dim runs, so a frame never lands mid-ladder among
# the dimensions it annotates.
_GDT_SUBCHAIN = 3
# Over-capacity survival rank for an authored GD&T frame: a declared control frame /
# datum / finish / note is deliberate intent, so on a strip too full for every candidate it is
# kept over the auto dims (locations/slots, priority 0) rather than dropped by stacking-key order.
_GDT_CORRIDOR_PRIORITY = PRIORITY.AUTHORED
# Minimum GD&T leader shaft length (page-mm). A zero-length Leader (site == solved tier)
# makes OCC's edge builder raise; nudging to this keeps `_build` total.
_MIN_LEADER = 0.05


def _datum_alternate_view(item, views):
    """Retarget a Y-normal datum to its other edge-on view if its strip is absent."""
    if not (
        isinstance(item, DatumRef)
        and item.reference_surface_kind == "plane"
        and item.frame.axis == "y"
        and item.view == "side"
        and item.side in ("left", "right")
        and getattr(views["side"][0], item.side) is None
    ):
        return item
    side = "below" if item.side == "left" else "above"
    if getattr(views["plan"][0], side) is None:
        return item
    return replace(item, view="plan", side=side)


@dataclass(frozen=True, slots=True)
class _GdtDropState:
    """One declared frame's identity and geometry retained until post-drain retry."""

    view: str
    side: str
    zones: ViewZones
    px: float
    py: float
    size: tuple[float, float]
    build: Callable[..., Any]
    feature: object
    title_block_box: tuple[float, float, float, float]
    source_ids: tuple[str, ...]
    satisfaction: tuple[DimensionId, ...]
    build_at: Callable[..., Any]
    build_routed: Callable[..., Any]
    declaration: ControlFrame | DatumRef | Finish | Note


def _gdt_glyph(item, draft):
    """Build the ISO 1101/5459/1302 glyph sketch for one GD&T IR item at the origin
    (the :class:`Leader` repositions a moved copy of it)."""
    if item.kind == "control_frame":
        tolerance = item.display_tolerance or item.tolerance
        if item.spherical_diameter:
            tolerance = "Sø" + tolerance
        return FeatureControlFrame(
            item.characteristic,
            tolerance,
            datums=item.datums,
            draft=draft,
            diameter=item.diameter,
            modifier=item.modifier,
        )
    if item.kind == "datum_ref":
        return DatumFeature(item.letter, draft=draft)
    if item.kind == "note":  # free-text manufacturing note — a single-line text glyph
        return TextBlock([_font_safe_text(item.text)], position=(0.0, 0.0), draft=draft)
    return SurfaceFinish(item.ra, position=(0.0, 0.0), draft=draft)


def _gdt_pdf_text_specs(glyph, item, draft) -> tuple:
    """Token-level semantic text in coordinates relative to *glyph*'s centre.

    GD&T characteristic/diameter/material-condition rings and surface-finish marks remain
    vector geometry. Their adjacent values and datum letters use the exact cell anchors from
    the helper renderer so selection aligns without treating the whole compound glyph as one
    centred string.
    """
    box = glyph.bounding_box()
    gcx, gcy = (box.min.X + box.max.X) / 2.0, (box.min.Y + box.max.Y) / 2.0
    h = draft.font_size
    font_path = getattr(draft, "font_path", DEFAULT_FONT_PATH)
    font_name = getattr(draft, "font", "Arial")
    specs = []

    def add(value, x, y, size=h, *, h_align="center"):
        specs.append(
            (
                _font_safe_text(value),
                x - gcx,
                y - gcy,
                size,
                font_path,
                font_name,
                "REGULAR",
                h_align,
                "middle",
            )
        )

    if item.kind == "datum_ref":
        x0, y0, x1, y1 = glyph.label_bbox
        add(glyph.label, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
        return tuple(specs)
    if item.kind == "finish":
        x0, y0, x1, y1 = glyph.label_bbox
        add(glyph.label, (x0 + x1) / 2.0, (y0 + y1) / 2.0)
        return tuple(specs)
    if item.kind != "control_frame":
        return ()

    # Mirror helpers._gdt_tol_cell / _gdt_datum_cell. The renderer deliberately
    # uses regular text even when the surrounding Draft requests another style.
    H = 2.0 * h
    pad, diameter_radius, modifier_radius = 0.6 * h, 0.42 * h, 0.62 * h
    x = H + pad
    if item.diameter:
        diameter_cx = x + diameter_radius
        add("ø", diameter_cx, H / 2.0)
        x = diameter_cx + diameter_radius + pad
    tolerance = item.display_tolerance or item.tolerance
    if item.spherical_diameter:
        prefix_width = _text_size("Sø", h, font_path, font_name)[0]
        tolerance_width = _text_size("Sø" + tolerance, h, font_path, font_name)[0]
        value_width = _text_size(tolerance, h, font_path, font_name)[0]
        add("Sø", x + prefix_width / 2.0, H / 2.0)
        add(tolerance, x + tolerance_width - value_width / 2.0, H / 2.0)
        x += tolerance_width + pad
    else:
        tolerance_width = _text_size(tolerance, h, font_path, font_name)[0]
        tolerance_cx = x + tolerance_width / 2.0
        add(tolerance, tolerance_cx, H / 2.0)
        x = tolerance_cx + tolerance_width / 2.0 + pad
    if item.modifier:
        modifier_cx = x + modifier_radius
        add(item.modifier.upper(), modifier_cx, H / 2.0, size=0.8 * h)

    tolerance_cell_width = (
        pad
        + tolerance_width
        + pad
        + (2.0 * diameter_radius + pad if item.diameter else 0.0)
        + (2.0 * modifier_radius + pad if item.modifier else 0.0)
    )
    datum_start = H + tolerance_cell_width
    for index, letter in enumerate(item.datums):
        add(letter, datum_start + (index + 0.5) * H, H / 2.0)
    return tuple(specs)


def _gdt_visual_zone(glyph, draft) -> str:
    """Read the zone qualifier from the rendered frame glyph, not its IR spec."""
    if glyph.tolerance_str.startswith("Sø"):
        return "spherical_diameter_zone"

    # The helper draws a diametral-zone sign as a ring and diagonal stroke in
    # the tolerance cell. Its text label omits that sign, and `segments` keeps
    # pretrace strokes even when the helper drops a failed stroke. Require ink
    # at the slash centre as well as the two ring poles in the finished sketch.
    h = draft.font_size
    radius = 0.42 * h
    center = ((2.0 + 0.6 + 0.42) * h, h)
    first = (center[0] + 0.9 * radius, center[1] - 0.9 * radius)
    second = (center[0] - 0.9 * radius, center[1] + 0.9 * radius)

    def near(left, right):
        return all(abs(a - b) <= 1e-4 for a, b in zip(left, right, strict=True))

    slash = any(
        (near(a, first) and near(b, second)) or (near(a, second) and near(b, first))
        for a, b in glyph.segments
    )
    if (
        slash
        and glyph.is_inside((center[0], center[1], 0.0))
        and all(glyph.is_inside((center[0], center[1] + sign * radius, 0.0)) for sign in (-1, 1))
    ):
        return "diameter_zone"
    return ""


def _cut_area(result: Any) -> float:
    """Account for OCC cuts that return one shape or several pieces."""
    if isinstance(result, ShapeList):
        return sum(float(piece.area) for piece in result)
    return float(result.area)


def _gdt_visual_tolerance(glyph, draft, zone: str, *, modifier: bool = False) -> str:
    """Trust a tolerance label only when its finished ink matches the font text."""
    text = str(glyph.tolerance_str)
    h = draft.font_size
    font_path = getattr(draft, "font_path", DEFAULT_FONT_PATH)
    font_name = getattr(draft, "font", "Arial")
    left = (2.0 + 0.6) * h
    if zone == "diameter_zone":
        left += (2.0 * 0.42 + 0.6) * h
    # Start inside the cell divider, or just after the Ø ring. Include the
    # padding so unexpected printed characters cannot hide beside the value.
    stroke_clearance = max(0.1, 0.1 * h)
    ink_left = left - 0.6 * h + stroke_clearance if zone == "diameter_zone" else 2.0 * h
    try:
        # Construct the expected font outlines independently of the helper's text
        # builder, so a same-width wrong digit cannot validate its own output.
        expected = Text(
            text,
            font_size=h,
            font=font_name,
            font_path=font_path,
            align=(Align.CENTER, Align.CENTER),
            mode=Mode.PRIVATE,
        )
        width = expected.bounding_box().size.X
        right = left + width
        expected = expected.moved(Location(Vector(left + width / 2.0, h, 0)))
        tolerance = max(1e-3, h * 1e-3)
        glyph_faces = glyph.faces()
        borders = [
            (box.min.X + box.max.X) / 2.0
            for face in glyph_faces
            if (box := face.bounding_box()).min.X > 2.0 * h + tolerance
            and box.max.X - box.min.X < max(0.3, 0.1 * h)
            and box.min.Y < 0.1 * h
            and box.max.Y > 1.9 * h
        ]
        if not borders:
            return ""
        ink_right = min(borders)
        if modifier:
            ink_right -= (0.6 + 2.0 * 0.62) * h + stroke_clearance
        if ink_right < right - tolerance:
            return ""
        faces = []
        for face in glyph_faces:
            box = face.bounding_box()
            if (
                box.max.X >= ink_left - tolerance
                and box.min.X <= ink_right + tolerance
                and box.min.Y > 0.1 * h
                and box.max.Y < 1.9 * h
            ):
                faces.append(face)
        if not faces:
            return ""
        actual = Sketch(children=faces)
        area_tolerance = max(1e-8, h * h * 1e-8)
        if (
            _cut_area(expected.cut(actual)) <= area_tolerance
            and _cut_area(actual.cut(expected)) <= area_tolerance
        ):
            return text
    except Exception:
        return ""
    return ""


def _gdt_visual_finish(glyph, draft, value: str) -> str:
    """Trust a finish value only when its glyph faces spell that value."""
    try:
        expected = Text(
            value,
            font_size=draft.font_size,
            font=getattr(draft, "font", "Arial"),
            font_path=getattr(draft, "font_path", DEFAULT_FONT_PATH),
            align=(Align.MIN, Align.MIN),
            mode=Mode.PRIVATE,
        )
        x0, y0, x1, y1 = glyph.label_bbox
        box = expected.bounding_box()
        expected = expected.moved(Location(Vector(x0 - box.min.X, y0 - box.min.Y, 0)))
        tolerance = max(1e-3, draft.font_size * 1e-3)
        faces = [
            face
            for face in glyph.faces()
            if (face_box := face.bounding_box()).min.X >= x0 - tolerance
            and face_box.max.X <= x1 + tolerance
            and face_box.min.Y >= y0 - tolerance
            and face_box.max.Y <= y1 + tolerance
        ]
        if not faces:
            return ""
        actual = Sketch(children=faces)
        area_tolerance = max(1e-8, draft.font_size**2 * 1e-8)
        if (
            _cut_area(expected.cut(actual)) <= area_tolerance
            and _cut_area(actual.cut(expected)) <= area_tolerance
        ):
            return value
    except Exception:
        return ""
    return ""


def _gdt_text_evidence(glyph, item, draft) -> dict[str, object]:
    """Validate one immutable glyph once, independently of its leader position."""
    evidence: dict[str, object] = {
        "pdf_text_relative_specs": _gdt_pdf_text_specs(glyph, item, draft)
    }
    if item.kind == "finish":
        evidence["gdt_visual_finish"] = _gdt_visual_finish(glyph, draft, item.ra)
    if item.kind == "control_frame":
        zone = _gdt_visual_zone(glyph, draft)
        tolerance = _gdt_visual_tolerance(glyph, draft, zone, modifier=bool(item.modifier))
        evidence["gdt_visual_tolerance"] = tolerance
        evidence["gdt_visual_zone"] = zone if tolerance else ""
    return evidence


def _apply_gdt_text_evidence(leader, evidence: dict[str, object]) -> None:
    """Keep the validated glyph's value beside PDF text for independent PMI lint."""
    for name, value in evidence.items():
        setattr(leader, name, value)


def _attach_gdt_text_evidence(leader, glyph, item, draft) -> None:
    """Attach independently checked glyph evidence to a placed leader."""
    _apply_gdt_text_evidence(leader, _gdt_text_evidence(glyph, item, draft))


def _gdt_retry_sides(side: str, *, normal_side_only: bool) -> tuple[str, ...]:
    """Imported datums keep their proven outward side; other glyphs may relax."""
    if normal_side_only:
        return (side,)
    return {
        "above": ("below", "right", "left"),
        "below": ("above", "right", "left"),
        "left": ("right", "above", "below"),
        "right": ("left", "above", "below"),
    }[side]


def _datum_label_has_whitespace(label, strip, horizontal, field) -> bool:
    """A local datum glyph must clear projected material or sit beyond the view."""
    x0, y0, x1, y1 = label
    if horizontal:
        outside = y1 <= strip.anchor if strip.direction < 0 else y0 >= strip.anchor
    else:
        outside = x1 <= strip.anchor if strip.direction < 0 else x0 >= strip.anchor
    if outside:
        return True
    if not field:
        return False
    # The shared view-edge guard rejects partial boundary crossings. These
    # interior probes reject a glyph sitting wholly on a blank projected face.
    return all(
        material_span((x0, y), (x1, y), field) <= 1e-6
        for y in (y0 + (y1 - y0) * 0.1, (y0 + y1) / 2.0, y1 - (y1 - y0) * 0.1)
    )


def _gdt_retry_geometry(state, tier, alt):
    """Give the deferred carve its strip and glyph extent."""
    alt_strip = getattr(state.zones, alt, None)
    if alt_strip is None:
        return None
    hz = alt in ("above", "below")
    axis = "y" if hz else "x"
    extent = state.size[1] if hz else state.size[0]
    perp = (
        (state.px, state.px + state.size[0])
        if hz
        else (state.py - state.size[1] / 2, state.py + state.size[1] / 2)
    )
    return alt_strip, hz, axis, max(tier, extent), perp


def _gdt_retry_blocker(dwg, state, dim) -> str | None:
    """Check a post-drain glyph against the title block and settled ink."""
    if _box_hits(_anno_box(dim), (state.title_block_box,)):
        return "title_block_conflict"
    if not annotation_ink_clear(dwg, dim):
        return "ink_conflict"
    return None


def _gdt_retry_trace(ctx, state, nm):
    """Start one optional trace entry for the deferred retry."""
    trace = getattr(ctx, "trace", None)
    event = (
        trace.pass_event("gdt_post_drain_fallback", view=state.view, requested_side=state.side)
        if trace is not None
        else None
    )
    trace_item = {"name": nm, "outcome": "unmet", "attempts": []} if event is not None else None
    if event is not None:
        event["items"].append(trace_item)
    return trace_item


def _gdt_retry_unmet(ctx, state, nm, normal_side_only):
    """Record why a required glyph could not stay on its legal corridor."""
    ctx.record_issue(
        "warning",
        "pmi_dropped" if state.source_ids else "gdt_dropped",
        (
            f"{nm} not placed (no legible room on its surface-normal {state.side} strip)"
            if normal_side_only
            else f"{nm} not placed (no legible room in any {state.view} strip or sheet fallback)"
        ),
        source=state.source_ids,
        outcome_stage="placement",
    )


def _gdt_drop_callback(
    dwg,
    ctx,
    *,
    tier,
    carve_position,
    sheet_fallback,
    item,
    zones,
    px,
    py,
    size,
    tb_box,
    source_ids,
    satisfaction,
    build,
    build_at,
    build_routed,
):
    """Return the deferred shared-solver fallback for one declared GD&T candidate."""
    state = _GdtDropState(
        view=item.view,
        side=item.side,
        zones=zones,
        px=px,
        py=py,
        size=size,
        build=build,
        feature=item.origin or item,
        title_block_box=tb_box,
        source_ids=source_ids,
        satisfaction=satisfaction,
        build_at=build_at,
        build_routed=build_routed,
        declaration=item,
    )

    def _drop(nm):
        def _retry():
            trace_item = _gdt_retry_trace(ctx, state, nm)
            normal_side_only = item.kind == "datum_ref" and bool(item.reference_surface_kind)
            for alt in _gdt_retry_sides(state.side, normal_side_only=normal_side_only):
                geometry = _gdt_retry_geometry(state, tier, alt)
                if geometry is None:
                    continue
                strip, hz, axis, extent, perp = geometry
                pos = carve_position(dwg, strip, state.view, axis, extent, perp)
                if pos is None:
                    if trace_item is not None:
                        trace_item["attempts"].append({"side": alt, "outcome": "no_free_position"})
                    continue
                dim = state.build(pos, _hz=hz)
                blocker = _gdt_retry_blocker(dwg, state, dim)
                if blocker:
                    if trace_item is not None:
                        trace_item["attempts"].append({"side": alt, "outcome": blocker})
                    continue
                ctx.place(
                    dim,
                    nm,
                    view=state.view,
                    feature=state.feature,
                    satisfaction=state.satisfaction,
                    declaration=state.declaration,
                )
                if alt != state.side:
                    ctx.record_issue(
                        "info",
                        "gdt_side_relaxed",
                        f"{nm}: the {state.view} {state.side} strip was full — placed on {alt} instead",
                    )
                if trace_item is not None:
                    trace_item["attempts"].append({"side": alt, "outcome": "placed"})
                    trace_item.update(outcome="placed", side=alt)
                return
            fallback = (
                sheet_fallback(
                    dwg,
                    (state.px, state.py),
                    state.view,
                    state.build_at,
                    state.build_routed,
                    state.size,
                )
                if not normal_side_only
                else None
            )
            if fallback is not None:
                ctx.place(
                    fallback,
                    nm,
                    view=state.view,
                    feature=state.feature,
                    satisfaction=state.satisfaction,
                    declaration=state.declaration,
                )
                ctx.record_issue(
                    "info",
                    "gdt_sheet_fallback",
                    f"{nm}: adjacent {state.view} strips were full — placed in clear sheet space",
                    source=state.source_ids,
                )
                if trace_item is not None:
                    trace_item["attempts"].append({"side": "sheet", "outcome": "placed"})
                    trace_item.update(outcome="placed", side="sheet")
                return
            if trace_item is not None and not normal_side_only:
                trace_item["attempts"].append({"side": "sheet", "outcome": "no_clear_route"})
            _gdt_retry_unmet(ctx, state, nm, normal_side_only)

        ctx.post_drain.append(_retry)

    return _drop


def _gdt_compact_candidates(
    original, build, ink_at, strip, size, horizontal, item, site, tier, material_field
):
    """Nearest-first same-strip landings checked later against exact ink.

    Dimension extension lines make their conservative boxes intentionally
    broad. A GD&T leader may pass through the empty part of such a box, so
    the corridor result is an upper bound rather than necessarily the best
    landing. Keep this search finite and inside the requested strip.
    """
    if original is None:
        return
    original_pos = original.elbow[1 if horizontal else 0]
    extent = size[1 if horizontal else 0]
    if item.kind == "datum_ref" and item.reference_surface_kind:
        # A whole-view strip can be far from a small datum face. First try
        # bounded positions between the face and that strip, while keeping
        # the shaft on the proven surface normal. The shared postsolve
        # checks the complete glyph, view edges and settled annotation ink.
        distance = (original_pos - site) * strip.direction
        first = extent / 2.0 + 1.0
        step = max(tier / 2.0, 1.0)
        for index in range(min(12, max(0, int((distance - first) / step) + 1))):
            travel = first + index * step
            if travel >= distance - 1e-6:
                break
            pos = site + strip.direction * travel
            proposal = ink_at(pos)
            # A symbol inside the whole-view box must sit in projected
            # whitespace, not on a blank face with no visible edge. The
            # shared postsolve also checks visible edges and exact ink.
            if _datum_label_has_whitespace(proposal.label_bbox, strip, horizontal, material_field):
                yield proposal
    near = strip.anchor + strip.direction * (strip.gap + extent / 2.0)
    distance = (original_pos - near) * strip.direction
    if distance <= 1e-6:
        return
    step = max(tier + strip.spacing, 1.0)
    count = min(64, int(math.ceil(distance / step)) + 1)
    for index in range(count):
        travel = min(distance, index * step)
        pos = near + strip.direction * travel
        if abs(pos - original_pos) <= 1e-6:
            return
        yield ink_at(pos)


def _gdt_candidate_builders(
    item, draft, leader_ctor, fallback_glyph, px, py, horizontal, strip, size, tier, material_field
):
    """Build one glyph's primary and fallback leaders plus bounded strip retries."""
    # Every retry moves the same validated glyph. Its value/zone checks are expensive
    # OCC operations, but do not depend on the eventual leader elbow.
    evidence = _gdt_text_evidence(fallback_glyph, item, draft) if item.kind != "note" else {}
    glyph_box = fallback_glyph.bounding_box()

    def _elbow(pos, _hz=horizontal):
        # Keep the analytical preflight and actual helper's zero-shaft guard identical.
        if _hz:
            delta = pos - py
            return px, pos if abs(delta) >= _MIN_LEADER else py + math.copysign(
                _MIN_LEADER, delta or 1.0
            )
        delta = pos - px
        return pos if abs(delta) >= _MIN_LEADER else px + math.copysign(
            _MIN_LEADER, delta or 1.0
        ), py

    def _label_bbox_at(pos):
        elbow_x, elbow_y = _elbow(pos)
        shelf_dir = 1.0 if elbow_x >= px else -1.0
        anchor_x = glyph_box.min.X if shelf_dir > 0 else glyph_box.max.X
        dx = elbow_x + shelf_dir * draft.pad_around_text - anchor_x
        dy = elbow_y - (glyph_box.min.Y + glyph_box.max.Y) / 2.0
        return (
            glyph_box.min.X + dx,
            glyph_box.min.Y + dy,
            glyph_box.max.X + dx,
            glyph_box.max.Y + dy,
        )

    def _ink_at(pos):
        elbow = _elbow(pos)
        shelf_dir = 1.0 if elbow[0] >= px else -1.0
        shelf_end = (elbow[0] + shelf_dir * draft.pad_around_text, elbow[1])
        return DeferredCompactCandidate(
            label_bbox=_label_bbox_at(pos),
            materialize=lambda _pos=pos: _build(_pos),
            tip=(px, py),
            elbow=elbow,
            segments=(((px, py), elbow), (elbow, shelf_end)),
            analytical_straight_leader=True,
        )

    def _build(pos, _hz=horizontal):
        tip = (px, py)
        # A zero-length leader shaft (the projected site coincides with the solved tier —
        # `pos == py` above/below, `pos == px` left/right) makes OCC's edge builder raise,
        # which would crash the whole build on a public-IR declaration. Guarantee a
        # minimum shaft along the stacking axis (nudge outward; 0.05 mm is invisible) so
        # `_build` is total — the drop-don't-crash invariant holds for every build call.
        elbow = _elbow(pos, _hz)
        leader = leader_ctor(
            tip=tip,
            elbow=elbow,
            label="",
            draft=draft,
            callout=fallback_glyph,
            all_around=getattr(item, "all_around", False),
            all_over=getattr(item, "all_over", False),
        )
        if item.kind == "note":
            # The outer leader intentionally has label="" because the visible
            # payload is a TextBlock callout. Preserve the authored note and measure
            # the embedded Text renderer's face-dependent newline pitch for PDF.
            leader.pdf_text = _font_safe_text(item.text)
            leader.pdf_text_font_style = "REGULAR"
            leader.pdf_text_line_spacing = _text_line_spacing_em(
                draft.font_size,
                getattr(draft, "font_path", DEFAULT_FONT_PATH),
                getattr(draft, "font", "Arial"),
            )
        else:
            _apply_gdt_text_evidence(leader, evidence)
        return leader

    def _build_at(elbow):
        leader = leader_ctor(
            tip=(px, py),
            elbow=(*elbow, 0),
            label="",
            draft=draft,
            callout=fallback_glyph,
            all_around=getattr(item, "all_around", False),
            all_over=getattr(item, "all_over", False),
        )
        if item.kind == "note":
            leader.pdf_text = _font_safe_text(item.text)
            leader.pdf_text_font_style = "REGULAR"
            leader.pdf_text_line_spacing = _text_line_spacing_em(
                draft.font_size,
                getattr(draft, "font_path", DEFAULT_FONT_PATH),
                getattr(draft, "font", "Arial"),
            )
        else:
            _apply_gdt_text_evidence(leader, evidence)
        return leader

    def _build_routed(bends, elbow):
        leader = RoutedLeader(
            (px, py),
            bends,
            elbow,
            "",
            draft,
            callout=fallback_glyph,
            all_around=getattr(item, "all_around", False),
            all_over=getattr(item, "all_over", False),
        )
        if item.kind == "note":
            leader.pdf_text = _font_safe_text(item.text)
            leader.pdf_text_font_style = "REGULAR"
            leader.pdf_text_line_spacing = _text_line_spacing_em(
                draft.font_size,
                getattr(draft, "font_path", DEFAULT_FONT_PATH),
                getattr(draft, "font", "Arial"),
            )
        else:
            _apply_gdt_text_evidence(leader, evidence)
        return leader

    def _compact_candidates(original):
        return _gdt_compact_candidates(
            original,
            _build,
            _ink_at,
            strip,
            size,
            horizontal,
            item,
            py if horizontal else px,
            tier,
            material_field,
        )

    def _ink_repair_candidates(
        original,
        _build=_build,
        _strip=strip,
        _size=size,
        _horizontal=horizontal,
    ):
        """Bounded outward tiers for a frame whose complete ink still conflicts.

        Inward exact-ink contraction has already run. This is the same
        corridor's remaining feature-relative space, not a raw page position.
        The shared placer checks each rebuilt frame and shaft against dimensions,
        leaders, other frames, page bounds, and fixed furniture before commit.
        """
        original_pos = original.elbow[1 if _horizontal else 0]
        extent = _size[1 if _horizontal else 0]
        outward_extent = extent / 2.0 if _horizontal else extent
        outer = _strip.outer_limit - _strip.direction * outward_extent
        available = (outer - original_pos) * _strip.direction
        step = max(tier + _strip.spacing, 1.0)
        for index in range(1, min(9, int(available // step) + 1)):
            yield _build(original_pos + _strip.direction * index * step)

    return _build, _build_at, _build_routed, _compact_candidates, _ink_repair_candidates


def render_gdt(
    dwg, model, a: Analysis, *, ctx, leader_ctor, carve_position, sheet_fallback, source_ids_for
) -> int:
    """Place declared GD&T frames / datum symbols / surface finishes (#61) as first-class
    ADR 2 (was 0009) corridor candidates — registered into the SAME strip the feature's dimensions
    use, BEFORE ``drain_corridors``, so one solve orders and spaces them crossing-free with
    the dims. Each item carries its target ``(view, side)`` strip + model-space site; the
    leader hangs the glyph off the site into that strip. The strip footprint is the GLYPH's
    own box — NOT the leader+glyph box, whose shaft back to the feature would inflate the
    stacking extent (the same reason dims reserve one label-height). Cross-view separation
    is the compose-then-pack repack's job (ADR 2 (was 0004)): every placed frame is ``view=``-tagged,
    so ``_measure_blocks`` folds it into the block. Returns the count registered."""
    items = [
        f
        for f in model.features
        if f.kind in _GDT_KINDS
        # Automatic AP242 discovery keeps typed IR available in report mode, but report must
        # not draw it. Explicit/round-tripped models remain declarations and therefore render.
        and (
            not source_ids_for(f)
            or a.pmi_mode == "annotate"
            or (ctx.model_declared and id(f) not in ctx.document_source_annotation_ids)
        )
    ]
    if not items:
        return 0
    draft = dwg.draft
    tier = draft.font_size + 2 * draft.pad_around_text
    # (zones, h-projector, v-projector, h-model-index, v-model-index) per view.
    views = {
        "plan": (a.pv_zones, a.proj.plan_x, a.proj.plan_y, 0, 1),
        "front": (a.fv_zones, a.proj.front_x, a.proj.front_z, 0, 2),
        "side": (a.sv_zones, a.proj.side_x, a.proj.side_z, 1, 2),
    }
    # The title block (bottom-right) is added AFTER drain_corridors, so strip placement can't
    # see it — a below/right strip runs down into its region. Its box is deterministic, so
    # reject any GD&T placement that would land on it (BOTH the primary corridor path, via the
    # candidate's `forbid`, AND the fallthrough) else the frame overlaps 'DRAWING'.
    tb_box = _title_block_box(dwg, a)
    n = 0
    for i, item in enumerate(items):
        item = _datum_alternate_view(item, views)
        name = f"m_gdt{i}"
        source_ids = source_ids_for(item)
        satisfaction = tuple(
            DimensionId(item.origin, parameter) for parameter in getattr(item, "satisfies", ())
        )
        vk = views.get(item.view)
        if vk is None or item.side not in ("above", "below", "left", "right"):
            ctx.record_issue(
                "warning",
                "pmi_dropped" if source_ids else "gdt_dropped",
                f"{name}: bad target {item.view!r}/{item.side!r}",
                source=source_ids,
                outcome_stage="validation",
            )
            continue
        zones, hproj, vproj, hi, vi = vk
        strip = getattr(zones, item.side)
        o = item.frame.origin
        if (
            item.kind in ("finish", "note")
            and isinstance(item.origin, ChamferFeature | FilletFeature)
            and item.origin.turned
        ):
            o = _turned_profile_site(item.origin.frame.origin, item.origin.axis, item.view, a.cyls)
        px, py = hproj(o[hi]), vproj(o[vi])
        horizontal = item.side in ("above", "below")  # frame stacks along y
        axis = "y" if horizontal else "x"
        # Coincident projected datum shafts can cover the nearer datum's tip.
        # Prefer the one nearest this strip's anchor; the farther datum retains
        # the ordinary side/sheet fallback if its first corridor becomes full.
        datum_stem_rank = 0
        if item.kind == "datum_ref" and strip is not None:
            perp, stack = (px, py) if horizontal else (py, px)
            distance = abs(stack - strip.anchor)
            for other in items:
                if other is item or other.kind != "datum_ref":
                    continue
                if (other.view, other.side) != (item.view, item.side):
                    continue
                other_origin = other.frame.origin
                other_perp = hproj(other_origin[hi]) if horizontal else vproj(other_origin[vi])
                other_stack = vproj(other_origin[vi]) if horizontal else hproj(other_origin[hi])
                if (
                    abs(other_perp - perp) <= 1e-6
                    and abs(other_stack - strip.anchor) > distance + 1e-6
                ):
                    datum_stem_rank += 1
        # The IR is public input (ADR 4 (was 0011)), so an invalid glyph spec (a mistyped
        # characteristic, a bad tolerance) must drop THIS item with a warning — never crash
        # the whole drawing build. The helper raises on a bad spec; catch it at the measure
        # (the first build) and drop. `_build` below re-runs `_gdt_glyph` with the same args
        # (so a spec error can't reappear there) AND is made total against the OTHER raise
        # source — a zero-length Leader shaft (see the min-leader guard in `_build`).
        try:
            fallback_glyph = _gdt_glyph(item, draft)
            gb = fallback_glyph.bounding_box().size
        except Exception as e:  # noqa: BLE001 — any glyph-spec error drops one item, not the build
            ctx.record_issue(
                "warning",
                "pmi_dropped" if source_ids else "gdt_dropped",
                f"{name}: cannot render ({type(e).__name__}: {e})",
                source=source_ids,
                outcome_stage="validation",
            )
            continue
        size = (gb.X, gb.Y)
        placed_view = dwg.views.get(item.view)
        field = (
            dwg.material_fields().get(id(placed_view[0]))
            if item.kind == "datum_ref" and item.reference_surface_kind and placed_view
            else None
        )

        (
            _build,
            _build_at,
            _build_routed,
            _compact_candidates,
            _ink_repair_candidates,
        ) = _gdt_candidate_builders(
            item,
            draft,
            leader_ctor,
            fallback_glyph,
            px,
            py,
            horizontal,
            strip,
            size,
            tier,
            field,
        )

        _drop = _gdt_drop_callback(
            dwg,
            ctx,
            tier=tier,
            carve_position=carve_position,
            sheet_fallback=sheet_fallback,
            item=item,
            zones=zones,
            px=px,
            py=py,
            size=size,
            tb_box=tb_box,
            source_ids=source_ids,
            satisfaction=satisfaction,
            build=_build,
            build_at=_build_at,
            build_routed=_build_routed,
        )

        register_corridor(
            ctx,
            (item.view, item.side),
            strip,
            item.view,
            axis,
            tier,
            CorridorCandidate(
                name=name,
                build=_build,
                order=(_GDT_SUBCHAIN, px if horizontal else py, name),
                on_place=lambda nm: None,
                on_drop=_drop,
                dedup=None,
                precedence=0,
                priority=(
                    _GDT_CORRIDOR_PRIORITY + datum_stem_rank * PRIORITY.AUTHORED_DATUM_STEM_STEP
                ),
                # A declared frame has no alternate view — force-keep (policy B) rather than
                # drop a user-authored annotation; only a physically full strip drops.
                force=True,
                # Declared frames belong to their decorated feature. An imported frame has
                # external source provenance but no separately-owned geometric IR feature.
                feature=item.origin or item,
                satisfaction=satisfaction or None,
                declaration=item,
                size=size,
                compact_candidates=_compact_candidates,
                ink_repair_candidates=_ink_repair_candidates,
                require_clear_ink=True,
                # Even a force-kept frame must not stack into the title block —
                # place_strip_candidates rejects a placement hitting this box, then on_drop's
                # fallthrough tries the other side.
                forbid=tb_box,
            ),
        )
        n += 1
    return n
