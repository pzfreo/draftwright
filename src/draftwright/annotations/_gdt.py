"""Declared GD&T glyphs and shared-corridor candidate registration."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from build123d_drafting import DatumFeature, FeatureControlFrame, SurfaceFinish, TextBlock
from build123d_drafting.helpers import DEFAULT_FONT_PATH

from draftwright._core import (
    Analysis,
    ViewZones,
    _font_safe_text,
    _text_line_spacing_em,
    _text_size,
)
from draftwright._geometry import _turned_profile_site
from draftwright.annotations._common import (
    PRIORITY,
    CorridorCandidate,
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
    (the :class:`Leader` repositions it). A fresh sketch per call — the leader translate
    must not alias a shared object across the strip solve's repeated probe builds."""
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
    # the tolerance cell. Its text label omits that sign, so inspect the actual
    # sketch ink as well as the stroke path before recording visual evidence.
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
    if slash and all(
        glyph.is_inside((center[0], center[1] + sign * radius, 0.0)) for sign in (-1, 1)
    ):
        return "diameter_zone"
    return ""


def _attach_gdt_text_evidence(leader, glyph, item, draft) -> None:
    """Keep the placed glyph's value beside PDF text for independent PMI lint."""
    leader.pdf_text_relative_specs = _gdt_pdf_text_specs(glyph, item, draft)
    if item.kind == "control_frame":
        leader.gdt_visual_tolerance = glyph.tolerance_str
        leader.gdt_visual_zone = _gdt_visual_zone(glyph, draft)


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
        # Fallthrough: the declared/derived side is full — try the OPPOSITE side of
        # the same view before dropping, so a congested default still places somewhere
        # legible rather than vanishing. DEFERRED via ctx.post_drain (the plate
        # pattern): the carve then runs after EVERY corridor has drained, so it cannot
        # preempt a corner a later sibling's force candidate needs. Force semantics
        # (no corridor-cross check) match the primary path, BUT reject a spot over the
        # (not-yet-placed) title block — a below/right strip runs into it, and the
        # carve can't see it.
        def _retry():
            trace = getattr(ctx, "trace", None)
            event = (
                trace.pass_event(
                    "gdt_post_drain_fallback", view=state.view, requested_side=state.side
                )
                if trace is not None
                else None
            )
            trace_item = (
                {"name": nm, "outcome": "unmet", "attempts": []} if event is not None else None
            )
            if event is not None:
                event["items"].append(trace_item)

            # Relax the requested side when its strip is full, so
            # try the OPPOSITE side, then the two PERPENDICULAR sides, placing on the first
            # with room. A note the caller asked to see should appear somewhere legible
            # rather than vanish; when the requested strip has no room, an explicit `side=`
            # is a preference, not a hard constraint. A perpendicular side flips the leader
            # orientation (`state.build(pos, _hz=hz)`). If the placement lands on a side other than
            # requested, record an INFO issue so the relaxation is visible.
            # A requested annotation must never be silently lost.
            relax_order = {
                "above": ("below", "right", "left"),
                "below": ("above", "right", "left"),
                "left": ("right", "above", "below"),
                "right": ("left", "above", "below"),
            }[state.side]
            for alt in relax_order:
                alt_strip = getattr(state.zones, alt, None)
                if alt_strip is None:
                    continue
                hz = alt in ("above", "below")  # perpendicular sides flip the leader axis
                axis2 = "y" if hz else "x"
                extent = (
                    state.size[1] if axis2 == "y" else state.size[0]
                )  # the glyph's stacking-axis size
                perp = (
                    (state.px, state.px + state.size[0])
                    if hz
                    else (state.py - state.size[1] / 2, state.py + state.size[1] / 2)
                )
                pos = carve_position(dwg, alt_strip, state.view, axis2, max(tier, extent), perp)
                if pos is None:
                    if trace_item is not None:
                        trace_item["attempts"].append({"side": alt, "outcome": "no_free_position"})
                    continue
                dim = state.build(pos, _hz=hz)
                if _box_hits(_anno_box(dim), (state.title_block_box,)):
                    if trace_item is not None:
                        trace_item["attempts"].append(
                            {"side": alt, "outcome": "title_block_conflict"}
                        )
                    continue
                if not annotation_ink_clear(dwg, dim):
                    if trace_item is not None:
                        trace_item["attempts"].append({"side": alt, "outcome": "ink_conflict"})
                    continue
                ctx.place(
                    dim,
                    nm,
                    view=state.view,
                    feature=state.feature,
                    satisfaction=state.satisfaction,
                    declaration=state.declaration,
                )  # relaxed side
                ctx.record_issue(
                    "info",
                    "gdt_side_relaxed",
                    f"{nm}: the {state.view} {state.side} strip was full — placed on {alt} instead",
                )
                if trace_item is not None:
                    trace_item["attempts"].append({"side": alt, "outcome": "placed"})
                    trace_item.update(outcome="placed", side=alt)
                return
            fallback = sheet_fallback(
                dwg,
                (state.px, state.py),
                state.view,
                state.build_at,
                state.build_routed,
                state.size,
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
            if trace_item is not None:
                trace_item["attempts"].append({"side": "sheet", "outcome": "no_clear_route"})
            ctx.record_issue(
                "warning",
                "pmi_dropped" if state.source_ids else "gdt_dropped",
                f"{nm} not placed (no legible room in any {state.view} strip or sheet fallback)",
                source=state.source_ids,
                outcome_stage="placement",
            )

        ctx.post_drain.append(_retry)

    return _drop


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

        def _build(pos, _px=px, _py=py, _hz=horizontal, _it=item):
            g = _gdt_glyph(_it, draft)
            tip = (_px, _py)
            # A zero-length leader shaft (the projected site coincides with the solved tier —
            # `pos == py` above/below, `pos == px` left/right) makes OCC's edge builder raise,
            # which would crash the whole build on a public-IR declaration. Guarantee a
            # minimum shaft along the stacking axis (nudge outward; 0.05 mm is invisible) so
            # `_build` is total — the drop-don't-crash invariant holds for every build call.
            if _hz:
                dy = pos - _py
                pos = (
                    pos if abs(dy) >= _MIN_LEADER else _py + math.copysign(_MIN_LEADER, dy or 1.0)
                )
                elbow = (_px, pos)
            else:
                dx = pos - _px
                pos = (
                    pos if abs(dx) >= _MIN_LEADER else _px + math.copysign(_MIN_LEADER, dx or 1.0)
                )
                elbow = (pos, _py)
            leader = leader_ctor(
                tip=tip,
                elbow=elbow,
                label="",
                draft=draft,
                callout=g,
                all_around=getattr(_it, "all_around", False),
                all_over=getattr(_it, "all_over", False),
            )
            if _it.kind == "note":
                # The outer leader intentionally has label="" because the visible
                # payload is a TextBlock callout. Preserve the authored note and measure
                # the embedded Text renderer's face-dependent newline pitch for PDF.
                leader.pdf_text = _font_safe_text(_it.text)
                leader.pdf_text_font_style = "REGULAR"
                leader.pdf_text_line_spacing = _text_line_spacing_em(
                    draft.font_size,
                    getattr(draft, "font_path", DEFAULT_FONT_PATH),
                    getattr(draft, "font", "Arial"),
                )
            else:
                _attach_gdt_text_evidence(leader, g, _it, draft)
            return leader

        def _build_at(elbow, _px=px, _py=py, _it=item, _g=fallback_glyph):
            leader = leader_ctor(
                tip=(_px, _py),
                elbow=(*elbow, 0),
                label="",
                draft=draft,
                callout=_g,
                all_around=getattr(_it, "all_around", False),
                all_over=getattr(_it, "all_over", False),
            )
            if _it.kind == "note":
                leader.pdf_text = _font_safe_text(_it.text)
                leader.pdf_text_font_style = "REGULAR"
                leader.pdf_text_line_spacing = _text_line_spacing_em(
                    draft.font_size,
                    getattr(draft, "font_path", DEFAULT_FONT_PATH),
                    getattr(draft, "font", "Arial"),
                )
            else:
                _attach_gdt_text_evidence(leader, _g, _it, draft)
            return leader

        def _build_routed(bends, elbow, _px=px, _py=py, _it=item, _g=fallback_glyph):
            leader = RoutedLeader(
                (_px, _py),
                bends,
                elbow,
                "",
                draft,
                callout=_g,
                all_around=getattr(_it, "all_around", False),
                all_over=getattr(_it, "all_over", False),
            )
            if _it.kind == "note":
                leader.pdf_text = _font_safe_text(_it.text)
                leader.pdf_text_font_style = "REGULAR"
                leader.pdf_text_line_spacing = _text_line_spacing_em(
                    draft.font_size,
                    getattr(draft, "font_path", DEFAULT_FONT_PATH),
                    getattr(draft, "font", "Arial"),
                )
            else:
                _attach_gdt_text_evidence(leader, _g, _it, draft)
            return leader

        def _compact_candidates(
            original,
            _build=_build,
            _strip=strip,
            _size=size,
            _horizontal=horizontal,
        ):
            """Nearest-first same-strip landings checked later against exact ink.

            Dimension extension lines make their conservative boxes intentionally
            broad.  A GD&T leader may pass through the empty part of such a box, so
            the corridor result is an upper bound rather than necessarily the best
            landing.  Keep this search finite and inside the requested strip.
            """
            if original is None:
                return
            original_pos = original.elbow[1 if _horizontal else 0]
            extent = _size[1 if _horizontal else 0]
            near = _strip.anchor + _strip.direction * (_strip.gap + extent / 2.0)
            distance = (original_pos - near) * _strip.direction
            if distance <= 1e-6:
                return
            step = max(tier + _strip.spacing, 1.0)
            count = min(64, int(math.ceil(distance / step)) + 1)
            for index in range(count):
                travel = min(distance, index * step)
                pos = near + _strip.direction * travel
                if abs(pos - original_pos) <= 1e-6:
                    return
                yield _build(pos)

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
                priority=_GDT_CORRIDOR_PRIORITY,  # authored intent outranks auto dims
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
