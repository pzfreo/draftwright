"""Feature declaration formatting for generated Sheet scripts.

The functions here turn one IR feature and its source metadata into editable
Python declarations. Page placement remains the drawing engine's decision.
"""

from __future__ import annotations

from draftwright.model.ir import KnurlRequirement, ThreadOperation, ThreadRequirement


def _n(v) -> float | int:
    """A tidy number: int when integral, else rounded to 3 dp."""
    v = round(float(v), 3)
    return int(v) if v == int(v) else v


def _pt(p) -> str:
    return "(" + ", ".join(str(_n(c)) for c in p) + ")"


def _authored_n(value) -> str:
    """Lossless spelling for normative values and linked correspondence facts.

    Detected geometry is intentionally made readable at 3 dp by :func:`_n`; an
    authored normative value or a set of mutually constrained record facts must instead
    survive an emit/execute round trip.
    """
    value = float(value)
    return str(int(value)) if value.is_integer() else repr(value)


def _parameter_n(value, parameter_id: str, exact_parameter: str | None):
    """Spell an imported nominal owner's geometry losslessly; keep normal scripts tidy."""
    return _authored_n(value) if parameter_id == exact_parameter else _n(value)


def _authored_pt(point) -> str:
    return "(" + ", ".join(_authored_n(value) for value in point) + ")"


def _direction(p) -> str:
    """A direction vector needs angular precision, not the 3 dp used for mm coordinates."""
    values = (round(float(component), 6) for component in p)
    return "(" + ", ".join(str(int(v)) if v == int(v) else str(v) for v in values) + ")"


def _pts_arg(points) -> str:
    return "[" + ", ".join(_pt(p) for p in points) + "]"


def _bbox_arg(bbox) -> str:
    return "None" if bbox is None else _pt(bbox)


def _cylindrical_refs_arg(references) -> str:
    values = []
    for reference in references:
        values.append(
            "{"
            f"'axis_origin': {_authored_pt(reference.axis_origin)}, "
            f"'axis_direction': {_authored_pt(reference.axis_direction)}, "
            f"'radius': {_authored_n(reference.radius)}, "
            f"'axial_interval': {_authored_pt(reference.axial_interval)}, "
            f"'sense': {reference.sense!r}"
            "}"
        )
    return "(" + ", ".join(values) + ("," if len(values) == 1 else "") + ")"


def _circular_refs_arg(references) -> str:
    values = []
    for reference in references:
        values.append(
            "{"
            f"'center': {_authored_pt(reference.center)}, "
            f"'normal': {_authored_pt(reference.normal)}, "
            f"'radius': {_authored_n(reference.radius)}"
            "}"
        )
    return "(" + ", ".join(values) + ("," if len(values) == 1 else "") + ")"


def _angular_refs_arg(references) -> str:
    values = [
        repr(
            {
                "vertex": reference.vertex,
                "first": reference.first,
                "second": reference.second,
                "sector": reference.sector,
                "virtual_vertex": reference.virtual_vertex,
            }
        )
        for reference in references
    ]
    return "(" + ", ".join(values) + ("," if len(values) == 1 else "") + ")"


def _cylindrical_refs_expr(references) -> str:
    values = [
        "CylindricalReference("
        f"axis_origin={reference.axis_origin!r}, "
        f"axis_direction={reference.axis_direction!r}, "
        f"radius={reference.radius!r}, "
        f"axial_interval={reference.axial_interval!r}, "
        f"sense={reference.sense!r})"
        for reference in references
    ]
    return "(" + ", ".join(values) + ("," if len(values) == 1 else "") + ")"


def _thread_requirement_expr(requirement: ThreadRequirement) -> str:
    return (
        "ThreadRequirement("
        f"application={requirement.application!r}, designation={requirement.designation!r}, "
        f"nominal_diameter={requirement.nominal_diameter!r}, pitch={requirement.pitch!r}, "
        f"tolerance_class={requirement.tolerance_class!r}, hand={requirement.hand!r}, "
        f"text={requirement.text!r}, source_ids={requirement.source_ids!r}, "
        f"part21_id={requirement.part21_id!r}, shape_aspect_ids={requirement.shape_aspect_ids!r}, "
        f"reference_item_ids={requirement.reference_item_ids!r}, "
        f"cylindrical_refs={_cylindrical_refs_expr(requirement.cylindrical_refs)}, "
        f"full_available_length={requirement.full_available_length!r}, "
        f"minimum_full_thread={requirement.minimum_full_thread!r}, "
        f"drill_diameter={requirement.drill_diameter!r}, drill_depth={requirement.drill_depth!r}, "
        f"drill_point_angle={requirement.drill_point_angle!r}, source={requirement.source!r})"
    )


def _knurl_requirement_expr(requirement: KnurlRequirement) -> str:
    return (
        "KnurlRequirement("
        f"pattern={requirement.pattern!r}, pitch={requirement.pitch!r}, "
        f"full_width={requirement.full_width!r}, text={requirement.text!r}, "
        f"source_ids={requirement.source_ids!r}, part21_id={requirement.part21_id!r}, "
        f"shape_aspect_ids={requirement.shape_aspect_ids!r}, "
        f"reference_item_ids={requirement.reference_item_ids!r}, "
        f"cylindrical_refs={_cylindrical_refs_expr(requirement.cylindrical_refs)}, "
        f"edge_chamfer={requirement.edge_chamfer!r}, "
        f"maximum_diameter={requirement.maximum_diameter!r}, "
        f"processes={requirement.processes!r}, source={requirement.source!r})"
    )


def _thread_arg(thread) -> str:
    if isinstance(thread, ThreadOperation):
        return f"ThreadOperation(designation={thread.designation!r}, depth={thread.depth!r})"
    return (
        _thread_requirement_expr(thread) if isinstance(thread, ThreadRequirement) else repr(thread)
    )


def _knurl_arg(knurl: KnurlRequirement) -> str:
    return _knurl_requirement_expr(knurl)


def _tuple_arg(values) -> str:
    vals = [str(_n(v)) for v in values]
    if len(vals) == 1:
        return f"({vals[0]},)"
    return "(" + ", ".join(vals) + ")"


def _hole_group_args(f) -> list[str]:
    kw = []
    if f.count and f.count > 1:
        kw.append(f"count={f.count}")
    # Preserve members independently of the callout count. A singleton at the frame
    # origin already has the same member-0 location through the constructor default.
    if f.members and (len(f.members) > 1 or f.members[0] != f.frame.origin):
        kw.append("members=[" + ", ".join(_pt(m) for m in f.members) + "]")
    return kw


def _hole_line(f, object_ref: str | None = None, *, exact_parameter: str | None = None) -> str:
    if getattr(f, "profile", None) == "double_d":
        kw = [
            f"major_diameter={_parameter_n(f.diameter, 'bore.diameter', exact_parameter)}",
            f"across_flats={_n(f.across_flats)}",
            f"at={_pt(f.frame.origin)}",
            f'axis="{f.frame.axis}"',
            f"through={f.through!r}",
        ]
        if f.depth is not None:
            kw.append(f"depth={_n(f.depth)}")
        if f.profile_direction is not None:
            kw.append(f"profile_direction={_direction(f.profile_direction)}")
        if f.through_indicator is not None:
            kw.append(f"through_indicator={f.through_indicator!r}")
        kw.extend(_hole_group_args(f))
        return f"sheet.double_d_bore({', '.join(kw)})"
    kw = (
        [object_ref]
        if object_ref is not None
        else [
            f"diameter={_parameter_n(f.diameter, 'bore.diameter', exact_parameter)}",
            f"at={_pt(f.frame.origin)}",
            f'axis="{f.frame.axis}"',
        ]
    )
    kw.extend(_hole_group_args(f))
    if f.cbore:
        kw.append(f"cbore=({_n(f.cbore[0])}, {_n(f.cbore[1])})")
    if f.spotface:
        kw.append(f"spotface=({_n(f.spotface[0])}, {_n(f.spotface[1])})")
    if f.csink:
        kw.append(f"csink=({_n(f.csink[0])}, {_n(f.csink[1])})")
    if getattr(f, "thread", None):  # internal thread round-trips too (#859, symmetric with steps)
        kw.append(f"thread={_thread_arg(f.thread)}")
    # Blindness is a FACT on the feature, so it round-trips whether or not a depth was
    # measured. Guarding it on `depth is not None` emitted neither `through=False` nor a
    # depth for a `HoleFeature(through=False, depth=None)`, and `declare.hole` defaults
    # `through=True` — so the script rebuilt a THROUGH hole and the blindness vanished on
    # the emit path (#878). Same rule as #868, which fixed the render side: a renderer (or
    # an emitter) may not infer an engineering fact from a dimension's presence.
    if not f.through and f.depth is None:
        kw.append("through=False")
    elif f.through and f.depth is not None:
        # A THROUGH hole's measured depth is a fact too — it is the bore length through the
        # material — and dropping it left the declared feature with `depth=None` where
        # detection had 8.0 (#962, epic #964's model-fidelity oracle). It changes no
        # annotation: the callout reads THRU either way, checked. `.depth()` is not usable
        # here because it also sets `through=False`, which would invert the very fact above.
        kw.append(f"depth={_n(f.depth)}")
    if f.through_indicator is not None:
        kw.append(f"through_indicator={f.through_indicator!r}")
    line = f"sheet.hole({', '.join(kw)})"
    if not f.through and f.depth is not None:
        line += f".depth({_n(f.depth)})"  # sets through=False too
    return line


def _member_hole_str(m, *, exact_parameter: str | None = None) -> str:
    """The ``hole(...)`` template for a pattern member — carries its ⌀ AND its
    counterbore / spotface / countersink / blind-depth so a counterbored or countersunk
    bolt circle keeps those callouts on re-run (declare.hole takes depth=/through=/cbore=/
    spotface=/csink= kwargs)."""
    if getattr(m, "profile", None) == "double_d":
        kw = [
            f"major_diameter={_parameter_n(m.diameter, 'bore.diameter', exact_parameter)}",
            f"across_flats={_n(m.across_flats)}",
            f"at={_pt(m.frame.origin)}",
            f'axis="{m.frame.axis}"',
            f"through={m.through!r}",
        ]
        if m.depth is not None:
            kw.append(f"depth={_n(m.depth)}")
        if m.profile_direction is not None:
            kw.append(f"profile_direction={_direction(m.profile_direction)}")
        if m.through_indicator is not None:
            kw.append(f"through_indicator={m.through_indicator!r}")
        return f"double_d_bore({', '.join(kw)})"
    kw = [
        f"diameter={_parameter_n(m.diameter, 'bore.diameter', exact_parameter)}",
        f"at={_pt(m.frame.origin)}",
        f'axis="{m.frame.axis}"',
    ]
    if m.cbore:
        kw.append(f"cbore=({_n(m.cbore[0])}, {_n(m.cbore[1])})")
    if m.spotface:
        kw.append(f"spotface=({_n(m.spotface[0])}, {_n(m.spotface[1])})")
    if m.csink:
        kw.append(f"csink=({_n(m.csink[0])}, {_n(m.csink[1])})")
    if getattr(m, "thread", None):  # a patterned threaded bore keeps its thread on re-run (#859)
        kw.append(f"thread={_thread_arg(m.thread)}")
    # `through=False` independent of the depth — see `_hole_line` (#878).
    if not m.through:
        if m.depth is not None:
            kw.append(f"depth={_n(m.depth)}")
        kw.append("through=False")
    elif m.depth is not None:
        # A THROUGH member's measured depth is a fact too, exactly as on the standalone verb
        # — and this template was the sibling that still dropped it after `_hole_line` was
        # fixed, which the model-fidelity oracle missed because its corpus carried no hole
        # pattern (#967). The two templates diverging is the recurring shape here.
        kw.append(f"depth={_n(m.depth)}")
    if m.through_indicator is not None:
        kw.append(f"through_indicator={m.through_indicator!r}")
    return f"hole({', '.join(kw)})"


def _member_pocket_str(m) -> str:
    """The ``pocket(...)`` template for a pocket-pattern member — carries its width × length ×
    depth, orientation and position. Planar-pocket length is derived from emitted lo/hi;
    cylindrical mouths preserve every mutually constrained size and position losslessly.

    ``at=`` is carried for the same reason as the standalone verb (#962): without it
    `declare.pocket` synthesises an origin whose DEPTH-axis component is zero, so a member
    recessed into a face came back at 0 and the declared model diverged from the detected one.
    It cannot move the array — `declare.pocket_pattern` takes its centre from the pattern's own
    ``at=`` and falls back to the member's only when that is absent."""
    pocket_n = _authored_n if m.mouth_axis else _n
    lo, hi = pocket_n(m.lo), pocket_n(m.hi)
    length = pocket_n(m.length) if m.mouth_axis else _n(round(float(hi) - float(lo), 3))
    return (
        f"pocket(width={pocket_n(m.width)}, length={length}, depth={pocket_n(m.depth)}, "
        f'long_axis="{m.long_axis}", width_axis="{m.width_axis}", '
        f"lo={lo}, hi={hi}, w_center={pocket_n(m.w_center)}, at={_authored_pt(m.frame.origin) if m.mouth_axis else _pt(m.frame.origin)}"
        + (", edge_anchored=True" if m.edge_anchored else "")
        + (", open_sign=-1" if m.open_sign == -1 else "")
        + (f", corner_radius={_authored_n(m.corner_radius)}" if m.corner_radius else "")
        + (
            f", mouth_axis={m.mouth_axis!r}, mouth_radius={_authored_n(m.mouth_radius)}, mouth_at={_authored_pt(m.mouth_at)}"
            if m.mouth_axis
            else ""
        )
        + ")"
    )


def _member_slot_str(m) -> str:
    """The ``slot(...)`` template for a slot-pattern member — carries its width × length,
    orientation and position (a slot has no depth). Length is derived from the emitted lo/hi so
    ``hi - lo == length`` exactly (declare.slot rejects a 1e-6 mismatch).

    ``at=`` rides along for the same reason as the pocket member above (#962). A through-slot's
    depth-axis coordinate happens to be zero either way today, so this changes nothing now —
    it is here so the two member templates cannot drift apart, which is how the pocket one
    came to be the only place still dropping a position."""
    lo, hi = _n(m.lo), _n(m.hi)
    length = _n(round(float(hi) - float(lo), 3))
    return (
        f"slot(width={_n(m.width)}, length={length}, "
        f'long_axis="{m.long_axis}", width_axis="{m.width_axis}", '
        f"lo={lo}, hi={hi}, w_center={_n(m.w_center)}, at={_pt(m.frame.origin)}"
        + (f", end_radius={_n(m.end_radius)}" if m.end_radius is not None else "")
        + ")"
    )


def _measured_dimension_line(f) -> str:
    angular = getattr(f, "angular_reference", None)
    number = repr if angular is not None else _n
    reference_points = (
        repr((angular.first, angular.vertex, angular.second))
        if angular is not None
        else _pts_arg(f.ref_pts)
    )
    kw = [
        f"kind={f.dimension_kind!r}",
        f"value={number(f.value)}",
        f"label={f.label!r}",
        f"dominant_axis={f.dominant_axis!r}",
        f"ref_pts={reference_points}",
        f"ref_bbox={_bbox_arg(f.ref_bbox)}",
        f"at={_pt(f.frame.origin)}",
        f"axis={f.frame.axis!r}",
    ]
    if f.upper_tol is not None:
        kw.append(f"upper_tol={number(f.upper_tol)}")
    if f.lower_tol is not None:
        kw.append(f"lower_tol={number(f.lower_tol)}")
    if f.lower_bound is not None:
        kw.append(f"lower_bound={number(f.lower_bound)}")
    if f.upper_bound is not None:
        kw.append(f"upper_bound={number(f.upper_bound)}")
    if f.source != "sheet":
        kw.append(f"source={f.source!r}")
    if f.source_kind is not None and f.source_kind != f.dimension_kind:
        kw.append(f"source_kind={f.source_kind!r}")
    if f.source_id:
        kw.append(f"source_id={f.source_id!r}")
    if getattr(f, "lowering_blockers", ()):
        kw.append(f"lowering_blockers={f.lowering_blockers!r}")
    if getattr(f, "rendering_blockers", ()):
        kw.append(f"rendering_blockers={f.rendering_blockers!r}")
    if getattr(f, "cylindrical_refs", ()):
        kw.append(f"cylindrical_refs={_cylindrical_refs_arg(f.cylindrical_refs)}")
    if getattr(f, "circular_refs", ()):
        kw.append(f"circular_refs={_circular_refs_arg(f.circular_refs)}")
    if angular is not None:
        # Preserve full point precision: short angular rays amplify coordinate rounding.
        reference = {
            "vertex": angular.vertex,
            "first": angular.first,
            "second": angular.second,
            "sector": angular.sector,
            "virtual_vertex": angular.virtual_vertex,
        }
        kw.append(f"angular_reference={reference!r}")
    if angular_pattern := getattr(f, "angular_references", ()):
        kw.append(f"angular_references={_angular_refs_arg(angular_pattern)}")
    if member_ids := getattr(f, "angular_member_ids", ()):
        kw.append(f"angular_member_ids={member_ids!r}")
    if item_groups := getattr(f, "angular_reference_item_groups", ()):
        kw.append(f"angular_reference_item_groups={item_groups!r}")
    if getattr(f, "view", None) is not None:
        kw.append(f"view={f.view!r}")
    if getattr(f, "side", None) is not None:
        kw.append(f"side={f.side!r}")
    # `measured_dimension` since #873 — a generated script must not emit the transitional
    # overload, or every regenerated AP242 script would arrive pre-deprecated.
    return "sheet.measured_dimension(" + ", ".join(kw) + ")"


def _raw_pmi_expr(f) -> str:
    source_id = f", source_id={f.source_id!r}" if f.source_id else ""
    datum_refs = f", datum_refs={f.datum_refs!r}" if f.datum_refs else ""
    part21_id = f", part21_id={f.part21_id!r}" if f.part21_id else ""
    source_category = f", source_category={f.source_category!r}" if f.source_category else ""
    gtol_modifiers = f", gtol_modifiers={f.gtol_modifiers!r}" if f.gtol_modifiers else ""
    lowering_blockers = (
        f", lowering_blockers={f.lowering_blockers!r}" if f.lowering_blockers else ""
    )
    source_ids = f", source_ids={f.source_ids!r}" if getattr(f, "source_ids", ()) else ""
    datum_contexts = (
        f", datum_contexts={f.datum_contexts!r}" if getattr(f, "datum_contexts", ()) else ""
    )
    reference_item_ids = (
        f", reference_item_ids={f.reference_item_ids!r}"
        if getattr(f, "reference_item_ids", ())
        else ""
    )
    reference_axis = (
        f", reference_axis={f.reference_axis!r}" if getattr(f, "reference_axis", "") else ""
    )
    semantic_name = (
        f", semantic_name={f.semantic_name!r}" if getattr(f, "semantic_name", "") else ""
    )
    shape_aspect_ids = (
        f", shape_aspect_ids={f.shape_aspect_ids!r}" if getattr(f, "shape_aspect_ids", ()) else ""
    )
    cylindrical_refs = (
        f", cylindrical_refs={_cylindrical_refs_expr(f.cylindrical_refs)}"
        if getattr(f, "cylindrical_refs", ())
        else ""
    )
    reference_bboxes = (
        f", reference_bboxes={f.reference_bboxes!r}" if getattr(f, "reference_bboxes", ()) else ""
    )
    return (
        "PmiFeature("
        f"frame=Frame({_pt(f.frame.origin)}, {f.frame.axis!r}), "
        f"pmi_kind={f.pmi_kind!r}, value={_n(f.value)}, label={f.label!r}, "
        f"dominant_axis={f.dominant_axis!r}, ref_bbox={_bbox_arg(f.ref_bbox)}, "
        f"ref_pts=tuple({_pts_arg(f.ref_pts)}){source_id}{datum_refs}{part21_id}"
        f"{source_category}{gtol_modifiers}{lowering_blockers}{source_ids}{datum_contexts}"
        f"{reference_item_ids}{reference_axis}{semantic_name}{shape_aspect_ids}{cylindrical_refs}"
        f"{reference_bboxes}"
        ")"
    )


def _raw_pmi_line(f) -> str:
    return (
        f"sheet.add({_raw_pmi_expr(f)})"
        "   # raw AP242 PMI fallback; not yet lowered to a drafting concept"
    )


def _control_frame_line(f, origin_ref: str | None = None) -> str:
    kw = [
        f"frame=Frame({_pt(f.frame.origin)}, {f.frame.axis!r})",
        f"characteristic={f.characteristic!r}",
        f"tolerance={f.tolerance!r}",
        f"view={f.view!r}",
        f"side={f.side!r}",
    ]
    if f.datums:
        kw.append(f"datums={f.datums!r}")
    if f.diameter:
        kw.append("diameter=True")
    if f.spherical_diameter:
        kw.append("spherical_diameter=True")
    if f.modifier is not None:
        kw.append(f"modifier={f.modifier!r}")
    if f.all_around:
        kw.append("all_around=True")
    if f.all_over:
        kw.append("all_over=True")
    if f.display_tolerance is not None:
        kw.append(f"display_tolerance={f.display_tolerance!r}")
    if f.source_id:
        kw.append(f"source_id={f.source_id!r}")
    if f.part21_id:
        kw.append(f"part21_id={f.part21_id!r}")
    if origin_ref is not None:
        kw.append(f"origin={origin_ref}")
    elif getattr(f.origin, "kind", None) == "pmi":
        # Imported frames decorate an extraction record that is not itself a top-level model
        # feature. Keep that structural provenance nested rather than silently discarding it.
        kw.append(f"origin={_raw_pmi_expr(f.origin)}")
    return (
        "sheet.add(ControlFrame(" + ", ".join(kw) + "))"
        "   # control frame declaration; placement remains solver-owned"
    )


def _datum_ref_line(f, origin_ref: str | None = None) -> str:
    kw = [
        f"frame=Frame({_pt(f.frame.origin)}, {f.frame.axis!r})",
        f"letter={f.letter!r}",
        f"view={f.view!r}",
        f"side={f.side!r}",
    ]
    if f.source_id:
        kw.append(f"source_id={f.source_id!r}")
    if f.source_ids:
        kw.append(f"source_ids={f.source_ids!r}")
    if f.part21_id:
        kw.append(f"part21_id={f.part21_id!r}")
    if origin_ref is not None:
        kw.append(f"origin={origin_ref}")
    elif getattr(f.origin, "kind", None) == "pmi":
        kw.append(f"origin={_raw_pmi_expr(f.origin)}")
    return (
        "sheet.add(DatumRef(" + ", ".join(kw) + "))"
        "   # datum feature declaration; placement remains solver-owned"
    )


def _note_line(f, origin_ref: str | None = None) -> str:
    """Emit fluent authored notes and provenance-rich imported labels without loss."""
    raw_origin = getattr(f.origin, "kind", None) == "pmi"
    if (
        origin_ref is not None
        and not raw_origin
        and not (f.source_id or f.source_ids or f.part21_id)
    ):
        kwargs = [f"view={f.view!r}", f"side={f.side!r}"]
        if f.satisfies:
            kwargs.append(f"satisfies={f.satisfies!r}")
        return f"sheet.structured_note({f.text!r}, {origin_ref}, {', '.join(kwargs)})"
    if raw_origin or origin_ref is not None:
        origin = _raw_pmi_expr(f.origin) if raw_origin else origin_ref
        kw = [
            f"frame=Frame({_pt(f.frame.origin)}, {f.frame.axis!r})",
            f"text={f.text!r}",
            f"view={f.view!r}",
            f"side={f.side!r}",
            f"origin={origin}",
        ]
        if f.satisfies:
            kw.append(f"satisfies={f.satisfies!r}")
        if f.source_id:
            kw.append(f"source_id={f.source_id!r}")
        if f.source_ids:
            kw.append(f"source_ids={f.source_ids!r}")
        if f.part21_id:
            kw.append(f"part21_id={f.part21_id!r}")
        return (
            "sheet.add(Note(" + ", ".join(kw) + "))"
            "   # imported surface label; placement remains solver-owned"
        )
    return f"# note {f.text!r} — no declarative origin"


def _stock_feature_line(
    f,
    part_envelope,
    *,
    object_ref: str | None,
    exact_parameter: str | None,
    exact_step_length: bool,
    profile_group: str | None,
) -> str:
    """Emit whole-part, stock, and turned-feature declarations."""
    k = f.kind
    if k == "envelope":
        if part_envelope is not None and f == part_envelope:
            # `sheet.envelope()` defaults to the whole part and now measures its SOLIDS with a
            # centred frame (#977/#976), so for the whole-part envelope it reconstructs exactly
            # this feature — verified on the CTC-01 STEP seam, where the raw import is
            # 1170 × 650 and the part is 800 × 450. Restating six numbers the object already
            # carries is what ADR 4 (was 0011) exists to avoid.
            #
            # Guarded by equality rather than assumed: an envelope declared on a SUB-OBJECT is
            # not the part, and the bare verb would silently measure something else — the exact
            # shape of the four defects #977 closed.
            return "sheet.envelope()   # envelope " + (
                f"{_n(f.width)} × {_n(f.height)} × {_n(f.depth)}"
            )
        return (
            "sheet.add(EnvelopeFeature("
            f"frame=Frame({_pt(f.frame.origin)}, {f.frame.axis!r}), "
            f"width={_n(f.width)}, height={_n(f.height)}, depth={_n(f.depth)}, "
            f"bbox_min={_pt(f.bbox_min)}, bbox_max={_pt(f.bbox_max)}"
            f"))   # envelope {_n(f.width)} × {_n(f.height)} × {_n(f.depth)}"
        )
    if k == "step_level":
        # Carry shoulders + datum (#555/#578) so the declared model still constrains the step
        # POSITION, not just its heights. The fluent verb rebuilds the frame from base+datum.
        _items = [f"({a!r}, {_n(p)})" for a, p in f.shoulders]
        _sh = "(" + ", ".join(_items) + ("," if len(_items) == 1 else "") + ")"
        _support_items = [
            f"({_n(s.level)}, ({_n(s.x_span[0])}, {_n(s.x_span[1])}), "
            f"({_n(s.y_span[0])}, {_n(s.y_span[1])}))"
            for s in f.level_supports
        ]
        _supports = (
            "(" + ", ".join(_support_items) + ("," if len(_support_items) == 1 else "") + ")"
        )
        _support_arg = f", level_supports={_supports}" if _support_items else ""
        return (
            f"sheet.step_level(base={_n(f.base)}, levels={_tuple_arg(f.levels)}, "
            f"shoulders={_sh}, datum={_pt(f.datum)}, at={_pt(f.frame.origin)}{_support_arg})"
            "   # prismatic height ladder + shoulder position(s)"
        )
    if k == "rotational":
        bores = f", bores=({', '.join(str(_n(b)) for b in f.bores)},)" if f.bores else ""
        return (
            "sheet.rotational("
            f"od={_parameter_n(f.od, 'od.diameter', exact_parameter)}{bores}, "
            f"at={_pt(f.frame.origin)}, "
            f'axis="{f.frame.axis}")'
        )
    if k == "hole":
        return _hole_line(f, object_ref, exact_parameter=exact_parameter)
    if k == "boss":
        thr = (
            f", thread={_thread_arg(f.thread)}" if getattr(f, "thread", None) else ""
        )  # external thread (#859)
        knurl = f", knurl={_knurl_arg(f.knurl)}" if getattr(f, "knurl", None) else ""
        # The HEIGHT round-trips too (#938). Dropping it made the declared boss carry only
        # `boss.diameter` while the detected one also carries `boss_height.length`, so the
        # regenerated model could not express a dimension its source had — silently before
        # the mirror named it, and as a raise afterwards. ADR 4 (was 0011)'s round-trip rule is that
        # recognise, emit and declare agree about a feature's parameters.
        height = (
            f", height={_n(f.height)}" if object_ref is None and getattr(f, "height", None) else ""
        )
        # The SPAN too, not just the height. Detection reports `frame.origin` as the boss TOP
        # while `declare.boss` reads `at` as its CENTRE, so round-tripping the origin alone
        # shifted the height dimension by half the boss — same value, wrong witness lines
        # (#947, found by a boss fixture the corpus previously lacked). Emitting the
        # span states the geometry outright instead of relying on the two ends agreeing about
        # a coordinate convention.
        span = (
            f", span=({_pt(f.span[0])}, {_pt(f.span[1])})"
            if object_ref is None and getattr(f, "span", None)
            else ""
        )
        if object_ref is not None:
            return f"sheet.diameter({object_ref}{thr}{knurl})"
        return (
            "sheet.diameter("
            f"diameter={_parameter_n(f.diameter, 'boss.diameter', exact_parameter)}"
            f"{height}{span}, "
            f'at={_pt(f.frame.origin)}, axis="{f.frame.axis}"{thr}{knurl})'
        )
    if k == "polygonal_boss":
        span = f"({_pt(f.span[0])}, {_pt(f.span[1])})"
        directions = f"tuple({_pts_arg(f.flat_directions)})"
        flats = f"tuple({_pts_arg(f.flat_centres)})"
        return (
            "sheet.polygonal_boss("
            f"side_count={f.side_count}, across_flats={_n(f.across_flats)}, "
            f"height={_n(f.height)}, at={_pt(f.frame.origin)}, axis={f.frame.axis!r}, "
            f"span={span}, flat_directions={directions}, flat_centres={flats})"
        )
    if k == "polygonal_stock":
        span = f"({_pt(f.span[0])}, {_pt(f.span[1])})"
        directions = f"tuple({_pts_arg(f.flat_directions)})"
        flats = f"tuple({_pts_arg(f.flat_centres)})"
        return (
            "sheet.polygonal_stock("
            f"side_count={f.side_count}, across_flats={_n(f.across_flats)}, "
            f"length={_n(f.length)}, at={_pt(f.frame.origin)}, axis={f.frame.axis!r}, "
            f"span={span}, flat_directions={directions}, flat_centres={flats})"
        )
    if k == "external_spur_gear":
        lower, upper = f.tooth_thickness_tolerance
        return (
            f"sheet.external_spur_gear(at={_authored_pt(f.frame.origin)}, "
            f'axis="{f.frame.axis}", tooth_count={f.tooth_count}, '
            f"module={_authored_n(f.module)}, "
            f"pressure_angle={_authored_n(f.pressure_angle)}, "
            f"profile_shift={_authored_n(f.profile_shift)}, "
            f"face_width={_authored_n(f.face_width)}, "
            f"tooth_thickness={_authored_n(f.tooth_thickness)}, "
            "tooth_thickness_tolerance="
            f"({_authored_n(lower)}, {_authored_n(upper)}), "
            f"flank_tolerance_class={f.flank_tolerance_class})"
        )
    if k == "step":
        thr = (
            f", thread={_thread_arg(f.thread)}" if getattr(f, "thread", None) else ""
        )  # external thread (#859)
        knurl = f", knurl={_knurl_arg(f.knurl)}" if getattr(f, "knurl", None) else ""
        group = f", profile_group={profile_group!r}" if profile_group is not None else ""
        if object_ref is not None:
            return f"sheet.step({object_ref}{thr}{knurl}{group})"
        return (
            "sheet.step("
            f"diameter={_parameter_n(f.diameter, 'step.diameter', exact_parameter)}, "
            f"length={_authored_n(f.length) if exact_step_length else _n(f.length)}, "
            # Public shoulder stations are at 0.001 mm, so an odd-thousandth span has a
            # half-thousandth midpoint.  Preserve that coupled fact: independently rounding
            # ``length`` and ``at`` would reconstruct both endpoints 0.0005 mm away.
            f'at={_authored_pt(f.frame.origin)}, axis="{f.frame.axis}"{thr}{knurl}{group})'
        )
    raise AssertionError(f"unexpected stock feature kind: {k}")


def _machined_feature_line(f, *, exact_parameter: str | None) -> str:
    """Emit machined features and their repeated arrangements."""
    k = f.kind
    if k == "slot":
        lo, hi = _n(f.lo), _n(f.hi)
        # Derive length from the EMITTED lo/hi so hi - lo == length exactly — declare.slot()
        # rejects the recogniser's independently-rounded (lo, hi, length) with a 1e-6 tolerance.
        length = _n(round(float(hi) - float(lo), 3))
        return (
            f"sheet.slot(width={_n(f.width)}, length={length}, "
            f'long_axis="{f.long_axis}", width_axis="{f.width_axis}", '
            f"lo={lo}, hi={hi}, w_center={_n(f.w_center)}, at={_pt(f.frame.origin)}"
            + (f", end_radius={_n(f.end_radius)}" if f.end_radius is not None else "")
            + ")"
        )
    if k == "blend":
        path = (
            ""
            if f.path_kind == "straight"
            else f", path_kind='circular', path_radius={_authored_n(f.path_radius)}"
        )
        return (
            "sheet.blend("
            f"axis={f.axis!r}, radius={_authored_n(f.radius)}, "
            f"at={_authored_pt(f.frame.origin)}, side={f.side!r}, "
            f"axis_direction={_authored_pt(f.axis_direction)}{path})"
        )
    if k == "oriented_slot":
        passage = f.passage
        boundary = (
            "("
            + ", ".join(
                f"({_authored_pt(point)}, {_authored_n(bulge)})"
                for point, bulge in passage.boundary
            )
            + ",)"
        )
        body_key = (
            "None"
            if passage.body_key is None
            else (
                "()"
                if not passage.body_key
                else "(" + ", ".join(_authored_n(value) for value in passage.body_key) + ",)"
            )
        )
        return (
            "sheet.oriented_slot("
            f"width={_authored_n(f.width)}, length={_authored_n(f.length)}, "
            f"center={_authored_pt(f.frame.origin)}, "
            f"width_direction={_authored_pt(f.width_direction)}, "
            f"long_direction={_authored_pt(f.long_direction)}, "
            f"run_direction={_authored_pt(f.run_direction)}, "
            f"source_origin={_authored_pt(passage.origin)}, "
            f"source_u={_authored_pt(passage.u)}, "
            f"source_v={_authored_pt(passage.v)}, "
            f"run_interval=({_authored_n(passage.run_interval[0])}, "
            f"{_authored_n(passage.run_interval[1])}), source_boundary={boundary}, "
            f"low_capped={passage.low_capped!r}, high_capped={passage.high_capped!r}, "
            f"body_key={body_key})"
        )
    if k == "rectangular_blind_slot":
        return (
            "sheet.rectangular_blind_slot("
            f"axis={f.axis!r}, open_sign={f.open_sign}, length={_n(f.length)}, "
            f"width_axis={f.width_axis!r}, depth_axis={f.depth_axis!r}, "
            f"depth_sign={f.depth_sign}, width={_n(f.width)}, depth={_n(f.depth)}, "
            f"at={_pt(f.frame.origin)})"
        )
    if k == "round_bottom_blind_slot":
        return (
            "sheet.round_bottom_blind_slot("
            f"axis={f.axis!r}, open_sign={f.open_sign}, length={_n(f.length)}, "
            f"width_axis={f.width_axis!r}, depth_axis={f.depth_axis!r}, "
            f"depth_sign={f.depth_sign}, radius={_n(f.radius)}, "
            f"flat_width={_n(f.flat_width)}, at={_pt(f.frame.origin)})"
        )
    if k == "pocket":
        return "sheet." + _member_pocket_str(f)
    if k == "channel":
        return (
            f"sheet.channel(width={_n(f.width)}, "
            f'long_axis="{f.long_axis}", width_axis="{f.width_axis}", '
            f"w_center={_n(f.w_center)}, lo={_n(f.lo)}, hi={_n(f.hi)}, "
            f"d_lo={_n(f.d_lo)}, d_hi={_n(f.d_hi)}, open_sign={f.open_sign}, "
            f"at={_pt(f.frame.origin)})"
        )
    if k == "pad":
        x0, x1 = f.bounds("x")
        y0, y1 = f.bounds("y")
        z0, z1 = f.bounds("z")
        return (
            f"sheet.pad(x0={_n(x0)}, x1={_n(x1)}, "
            f"y0={_n(y0)}, y1={_n(y1)}, "
            f"z0={_n(z0)}, z1={_n(z1)}, axis={f.frame.axis!r}, "
            f"direction={f.direction}, at={_pt(f.frame.origin)})"
        )
    if k == "pattern":
        # Defining dims for the furniture (BCD centreline / pitch / grid dims) PLUS the exact
        # member positions. The arrangement alone can't be recomputed faithfully — the
        # detector records no bolt-circle START ANGLE (nor a linear direction reliably) — so
        # spelling out members= is the only fidelity-safe form (declare uses them as-is).
        parts = [f'kind="{f.pattern}"', f"count={f.count}"]
        if f.pattern == "bolt_circle" and f.bcd:
            parts.append(f"bcd={_n(f.bcd)}")
        elif f.pattern == "linear" and f.pitch:
            parts.append(f"pitch={_n(f.pitch)}")
            if f.direction:
                # Redundant for the LAYOUT, since `members=` below is spelled out — but it is
                # a field on the feature, and leaving it None made the declared pattern differ
                # from the detected one (#967). The emitted script is a representation
                # of the model, not only a program that reproduces its positions.
                parts.append(f"direction={_pt(f.direction)}")
        elif f.pattern == "grid" and f.grid:
            parts.append(f"grid=({_n(f.grid[0])}, {_n(f.grid[1])}), rows={f.rows}, cols={f.cols}")
            # `is not None`, not truthiness: 0.0 is a MEANINGFUL angle (an
            # unrotated grid) and `if f.angle:` silently dropped it, so a grid
            # pattern came back with angle=None (#967).
            if f.angle is not None:
                parts.append(f"angle={_n(f.angle)}")
        if f.members:
            parts.append("members=[" + ", ".join(_pt(p) for p in f.members) + "]")
        return (
            f"sheet.pattern({_member_hole_str(f.member, exact_parameter=exact_parameter)}, "
            + ", ".join(parts)
            + ")"
        )
    if k == "pocket_pattern":
        # declare.pocket_pattern() REJECTS members= (it recomputes the layout from
        # count + pitch/grid), so emit the array CENTRE as at= and the arrangement params —
        # the computed layout then lands where detected. direction= is required for a linear
        # array (else declare defaults to the first in-plane axis, mis-orienting the row).
        parts = [
            f'kind="{f.pattern}"',
            f"count={f.count}",
            f"at={_authored_pt(f.frame.origin) if f.member.mouth_axis else _pt(f.frame.origin)}",
        ]
        if f.pattern == "linear":
            parts.append(f"pitch={_n(f.pitch)}")
            if f.direction:
                parts.append(f"direction={_pt(f.direction)}")
        elif f.pattern == "grid" and f.grid:
            parts.append(f"grid=({_n(f.grid[0])}, {_n(f.grid[1])}), rows={f.rows}, cols={f.cols}")
            if f.angle is not None:
                parts.append(f"angle={_n(f.angle)}")
        return f"sheet.pocket_pattern({_member_pocket_str(f.member)}, " + ", ".join(parts) + ")"
    if k == "slot_pattern":
        # Like pocket_pattern: declare rejects members=, so emit the array centre + arrangement
        # params and let the computed layout land where detected. direction= carries orientation.
        parts = [f'kind="{f.pattern}"', f"count={f.count}", f"at={_pt(f.frame.origin)}"]
        if f.pattern == "linear":
            parts.append(f"pitch={_n(f.pitch)}")
            if f.direction:
                parts.append(f"direction={_pt(f.direction)}")
        elif f.pattern == "grid" and f.grid:
            parts.append(f"grid=({_n(f.grid[0])}, {_n(f.grid[1])}), rows={f.rows}, cols={f.cols}")
            if f.angle is not None:
                parts.append(f"angle={_n(f.angle)}")
        return f"sheet.slot_pattern({_member_slot_str(f.member)}, " + ", ".join(parts) + ")"
    raise AssertionError(f"unexpected machined feature kind: {k}")


def _profile_feature_line(f, *, profile_group: str | None) -> str:
    """Emit edge, section-profile, and other measured shape declarations."""
    k = f.kind
    if k == "chamfer":
        turned = ", turned=True" if f.turned else ""
        provenance = ""
        if f.source_ids:
            provenance += f", source_ids={f.source_ids!r}"
        if f.part21_id:
            provenance += f", part21_id={f.part21_id!r}"
        if f.shape_aspect_ids:
            provenance += f", shape_aspect_ids={f.shape_aspect_ids!r}"
        if f.reference_item_ids:
            provenance += f", reference_item_ids={f.reference_item_ids!r}"
        return (
            f'sheet.chamfer(axis="{f.axis}", leg1={_n(f.leg1)}, leg2={_n(f.leg2)}, '
            f"angle={_n(f.angle)}, at={_pt(f.frame.origin)}{turned}{provenance})"
        )
    if k == "fillet":
        turned = ", turned=True" if f.turned else ""
        return (
            f'sheet.fillet(axis="{f.axis}", radius={_n(f.radius)}, '
            f"at={_pt(f.frame.origin)}{turned})"
        )
    if k == "angle":
        if members := getattr(f, "members", ()):
            return "sheet.angle_pattern(" + ", ".join(repr(member) for member in members) + ")"
        reference = f.angular_reference
        return (
            f"sheet.angle(vertex={reference.vertex!r}, first={reference.first!r}, "
            f"second={reference.second!r}, sector={reference.sector!r}, "
            f"virtual_vertex={reference.virtual_vertex!r})"
        )
    if k == "paired_ramp_step":
        return (
            f'sheet.paired_ramp_step(axis="{f.axis}", angle={_n(f.angle)}, '
            f"length={_n(f.length)}, at={_pt(f.frame.origin)})"
        )
    if k == "gusset_rib":
        parts = [
            f'axis="{f.axis}"',
            f"supports={f.supports!r}",
            f"legs={f.legs!r}",
            f"directions={f.directions!r}",
            f"member_bounds={f.member_bounds!r}",
            f"datum={_n(f.datum)}",
        ]
        if f.pattern != "single":
            parts.append(f"pattern={f.pattern!r}")
        if f.pitch is not None:
            parts.append(f"pitch={_n(f.pitch)}")
        if f.mirror_plane is not None:
            parts.append(f"mirror_plane={f.mirror_plane!r}")
        return "sheet.gusset_rib(" + ", ".join(parts) + ")"
    if k == "hex_pocket":
        section = "(" + ", ".join(_authored_pt(point) for point in f.section) + ")"
        return (
            f'sheet.hex_pocket(axis="{f.frame.axis}", depth={_authored_n(f.depth)}, '
            f"open_sign={f.open_sign}, at={_authored_pt(f.frame.origin)}, section={section})"
        )
    if k == "circular_channel":
        centreline = "(" + ", ".join(_authored_pt(point) for point in f.centreline) + ")"
        section = "(" + ", ".join(_authored_pt(point) for point in f.section) + ")"
        return (
            f'sheet.circular_channel(axis="{f.axis}", radius={_authored_n(f.radius)}, '
            f"length={_authored_n(f.length)}, centreline={centreline}, section={section})"
        )
    if k == "circular_blind_step":
        centreline = "(" + ", ".join(_authored_pt(point) for point in f.centreline) + ")"
        section = "(" + ", ".join(_authored_pt(point) for point in f.section) + ")"
        return (
            f'sheet.circular_blind_step(axis="{f.axis}", radius={_authored_n(f.radius)}, '
            f"length={_authored_n(f.length)}, centreline={centreline}, section={section})"
        )
    if k == "through_step":
        section = "(" + ", ".join(_pt(point) for point in f.section) + ")"
        return (
            f'sheet.through_step(axis="{f.axis}", length={_n(f.length)}, '
            f"at={_pt(f.frame.origin)}, section={section})"
        )
    if k == "flat":
        # `axis_line`/`stock_span` are the stock identity (#1013). Emitted ALWAYS, not only
        # when non-default: they are what stops two same-sized flats on separate stock
        # collapsing into one callout, and a script that omits them regenerates the very
        # drawing the detection was fixing (#1035). The declared defaults reproduce
        # pre-#1013 grouping, so silence here is not neutral — it is the old bug.
        principal = tuple(1.0 if letter == f.axis else 0.0 for letter in "xyz")
        direction = (
            ""
            if f.axis_direction == principal
            else f", axis_direction={_direction(f.axis_direction)}"
        )
        return (
            f'sheet.flat(axis="{f.axis}", across={_n(f.across)}, at={_pt(f.frame.origin)}, '
            f"axis_line={_pt(f.axis_line)}, stock_span={_pt(f.stock_span)}{direction})"
        )
    if k == "groove":
        group = f", profile_group={profile_group!r}" if profile_group is not None else ""
        return (
            f'sheet.groove(axis="{f.axis}", width={_n(f.width)}, '
            f"diameter={_n(f.diameter)}, at={_pt(f.frame.origin)}{group})"
        )
    if k == "plate":
        return (
            f'sheet.plate(axis="{f.axis}", lo={_n(f.lo)}, hi={_n(f.hi)}, u={_n(f.u)}, v={_n(f.v)})'
        )
    raise AssertionError(f"unexpected profile feature kind: {k}")
