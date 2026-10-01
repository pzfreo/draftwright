"""Read-only evidence projections for a finished drawing.

The Drawing facade passes explicit state; this module does not own build state or
recognition and does not mutate annotation placement.
"""

from __future__ import annotations


def suppression_rows(omissions, feature_key) -> list[dict]:
    """Project compiler omissions with the drawing's stable descriptive key."""
    return [
        {
            "feature": feature_key(o.feature),
            "parameter_id": o.parameter_id,
            "value": o.value,
            "reason": o.reason,
            "authored": o.authored,
            "conveyed_by": (
                None
                if o.conveyed_by is None
                else {
                    "feature": feature_key(o.conveyed_by.feature),
                    "parameter_id": o.conveyed_by.parameter,
                }
            ),
        }
        for o in omissions
    ]


def measurement_keys(registry, name, feature_key) -> list[dict]:
    """Project every named claim to the same row key as suppressions."""
    return [
        {"feature": feature_key(mid.feature), "parameter_id": mid.parameter}
        for mid in registry.measurement_of(name)
    ]


def _cell_claim(name, annotation, reference, outcomes, schedules):
    from copy import deepcopy

    from draftwright.audit import MeasurementClaim

    identity = reference.measurement
    address = (reference.row, reference.column)
    evidence = [
        item
        for item in outcomes
        if item.annotation == name
        and item.cell == address
        and item.measurement is not None
        and getattr(item.measurement, "feature", None) is identity.feature
        and item.parameter_id == identity.parameter
    ]
    if not evidence or any(item.state != "confirmed" for item in evidence):
        return None, address
    schedule = schedules[reference.schedule]
    approved_cell = schedule.rows[reference.row][reference.column]
    item = approved_cell.measurement
    # Verification binds this exact cell to its approval; a coarse ID does not enlarge it.
    assert item is not None
    rows = annotation.table_rows
    return (
        MeasurementClaim(
            identity.feature,
            identity.parameter,
            name,
            (
                (
                    item.value,
                    deepcopy(item.tolerance),
                    item.span,
                    item.axis,
                    item.discriminator,
                    item.location_member,
                    item.angular_reference.measurement_key
                    if item.angular_reference is not None
                    else None,
                ),
            ),
            (
                str(rows[reference.row][reference.column]),
                str(rows[0][reference.column]),
                tuple(
                    (column, str(rows[reference.row][column]))
                    for column, cell in enumerate(schedule.rows[reference.row])
                    if cell.measurement is None
                ),
            ),
            approved=(item,),
            cell=address,
        ),
        address,
    )


def measurement_snapshot(model, registry, annotations, scale):
    """Bind named rendered claims to the existing compiled dimension plan."""
    from copy import deepcopy

    from draftwright.audit import (
        _FURNITURE,
        MeasurementCellUncertainty,
        MeasurementClaim,
        MeasurementSnapshot,
    )
    from draftwright.linting.evidence import compiled_values, verify_measurement_claims
    from draftwright.linting.structural import dimension_path_measurement
    from draftwright.model.compiled import compile_dimensions

    if model is None:
        return MeasurementSnapshot((), (), (("", "model_unavailable"),))
    plan = compile_dimensions(model)
    values = compiled_values(plan)
    schedules = {schedule.name: schedule for schedule in plan.schedules}
    outcomes = verify_measurement_claims(registry, plan)
    claims = []
    unknown = []
    cell_unknown = []
    for name, type_name in annotations().items():
        if type_name in _FURNITURE:
            continue
        annotation = registry.named(name)
        path = dimension_path_measurement(annotation, scale, item_scale=registry.scale_of(name))
        # Paper-space arithmetic can introduce sub-nanometre roundoff when
        # a view moves or rescales. Compiled values/spans remain unrounded;
        # this extra observed length only binds the text to its drawn path.
        measured_length = None if path is None else round(path[0] / path[1], 9)
        identities = registry.measurement_of(name)
        if not identities:
            unknown.append((name, "measurement_identity_unavailable"))
        cells = registry.cells_of(name)
        if cells:
            for reference in cells:
                claim, address = _cell_claim(name, annotation, reference, outcomes, schedules)
                if claim is None:
                    unknown.append((name, "compiled_claim_unconfirmed"))
                    cell_unknown.append(
                        MeasurementCellUncertainty(name, address, "compiled_claim_unconfirmed")
                    )
                    continue
                claims.append(claim)
            # Retain unsliced claims as uncertainty, just as the common verifier
            # does. No successful neighbour grants them an implicit address.
            if any(
                item.annotation == name and item.cell is None and item.state != "confirmed"
                for item in outcomes
            ):
                unknown.append((name, "compiled_claim_unconfirmed"))
            continue
        for identity in identities:
            approved = tuple(
                item
                for item in values.get(identity, ())
                if item.id is not None and item.id.feature is identity.feature
            )
            evidence = [
                item
                for item in outcomes
                if item.annotation == name
                and item.measurement is not None
                and getattr(item.measurement, "feature", None) is identity.feature
                and item.parameter_id == identity.parameter
            ]
            if not approved or not evidence or any(item.state != "confirmed" for item in evidence):
                unknown.append((name, "compiled_claim_unconfirmed"))
                continue
            meaning = tuple(
                (
                    item.value,
                    deepcopy(item.tolerance),
                    item.span,
                    item.axis,
                    item.discriminator,
                    item.location_member,
                    item.angular_reference.measurement_key
                    if item.angular_reference is not None
                    else None,
                )
                for item in approved
            )
            claims.append(
                MeasurementClaim(
                    identity.feature,
                    identity.parameter,
                    name,
                    meaning,
                    (
                        str(
                            getattr(registry.named(name), "label", None)
                            or getattr(registry.named(name), "_annotate_label", "")
                        ),
                        tuple(
                            tuple(str(cell) for cell in row)
                            for row in getattr(registry.named(name), "table_rows", ()) or ()
                        ),
                        measured_length,
                    ),
                    (
                        deepcopy(registry.measurement_span_of(name)),
                        tuple(
                            (component, deepcopy(at))
                            for feature, component, at in getattr(
                                annotation, "covers_hole_locations", ()
                            )
                            if feature is identity.feature
                        ),
                    ),
                    approved=approved,
                )
            )
    return MeasurementSnapshot(
        tuple(model.features),
        tuple(claims),
        tuple(unknown),
        cell_unknown=tuple(cell_unknown),
    )


def layout_utilization(page, view_names, view_bounds, annotations) -> dict:
    """Measure the clipped union of view and non-furniture annotation boxes."""
    boxes = [bounds for name in view_names if (bounds := view_bounds(name)) is not None]
    for name, annotation in annotations():
        if name in {"sheet_frame", "title_block"}:
            continue
        try:
            bounds = annotation.bounding_box()
            boxes.append((bounds.min.X, bounds.min.Y, bounds.max.X, bounds.max.Y))
        except Exception:  # noqa: BLE001 — unmeasurable ink stays outside this evidence
            continue

    def clip(box, region):
        clipped = (
            max(box[0], region[0]),
            max(box[1], region[1]),
            min(box[2], region[2]),
            min(box[3], region[3]),
        )
        return clipped if clipped[0] < clipped[2] and clipped[1] < clipped[3] else None

    def union_area(region):
        clipped = [found for box in boxes if (found := clip(box, region)) is not None]
        xs = sorted({value for box in clipped for value in (box[0], box[2])})
        area = 0.0
        for left, right in zip(xs, xs[1:], strict=False):
            intervals = sorted(
                (box[1], box[3]) for box in clipped if box[0] < right and box[2] > left
            )
            covered = 0.0
            end = None
            for low, high in intervals:
                if end is None or low > end:
                    covered += high - low
                    end = high
                elif high > end:
                    covered += high - end
                    end = high
            area += (right - left) * covered
        return area

    x0, y0, x1, y1 = page
    width, height = x1 - x0, y1 - y0
    envelope = None
    clipped_boxes = [found for box in boxes if (found := clip(box, page)) is not None]
    if clipped_boxes:
        envelope = (
            min(box[0] for box in clipped_boxes),
            min(box[1] for box in clipped_boxes),
            max(box[2] for box in clipped_boxes),
            max(box[3] for box in clipped_boxes),
        )
    mid_x, mid_y = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    quadrants = {
        "left-bottom": (x0, y0, mid_x, mid_y),
        "right-bottom": (mid_x, y0, x1, mid_y),
        "left-top": (x0, mid_y, mid_x, y1),
        "right-top": (mid_x, mid_y, x1, y1),
    }
    page_area = width * height
    return {
        "scope": "clipped-view-and-annotation-bounding-boxes",
        "drawable_bounds": page,
        "content_bounds": envelope,
        "content_envelope_fraction": (
            None
            if envelope is None or page_area <= 0.0
            else (envelope[2] - envelope[0]) * (envelope[3] - envelope[1]) / page_area
        ),
        "footprint_fraction": None if page_area <= 0.0 else union_area(page) / page_area,
        "quadrants": {
            name: {
                "bounds": region,
                "footprint_fraction": union_area(region)
                / ((region[2] - region[0]) * (region[3] - region[1])),
            }
            for name, region in quadrants.items()
        },
    }


def lint_summary(
    issues,
    aggregation,
    *,
    analysis,
    model,
    registry,
    recognition,
    evidence,
    ownership,
    omissions,
    working_part,
    items,
    model_declared,
    layout_utilization,
    report_requirements,
    geometry_aware_codes,
    score_error_penalty,
    score_warning_penalty,
) -> dict:
    """Project one independent critique and its quality evidence as plain data."""
    from draftwright.linting import EXAMINABLE_DECLARED_KINDS, is_dimension_like, pmi_stage_summary
    from draftwright.linting.quality import quality_components, review_explanation
    from draftwright.model.compiled import compile_dimensions

    errors = sum(1 for i in issues if i.severity == "error")
    warnings = sum(1 for i in issues if i.severity == "warning")
    infos = sum(1 for i in issues if i.severity == "info")
    by_code: dict[str, int] = {}
    for i in issues:
        by_code[i.code] = by_code.get(i.code, 0) + 1
    score = max(
        0.0,
        1.0 - errors * score_error_penalty - warnings * score_warning_penalty,
    )
    pmi = (
        pmi_stage_summary(
            analysis.pmi_report,
            getattr(model, "features", ()),
            registry,
            analysis.pmi_mode,
            decorations=getattr(model, "decorations", {}),
        )
        if analysis is not None
        else None
    )
    quality = quality_components(
        recognition=recognition,
        features=getattr(model, "features", ()),
        registry=registry,
        omissions=omissions,
        issues=issues,
        part=working_part,
        evidence=evidence,
        ownership=ownership,
        # Fidelity needs an actual measured claim or a declared feature examined by a
        # truth-class check. Furniture text and unexamined feature kinds prove neither.
        has_asserted_content=(
            any(is_dimension_like(item) for item in items)
            or (
                model_declared
                and any(
                    getattr(f, "kind", None) in EXAMINABLE_DECLARED_KINDS
                    for f in getattr(model, "features", ())
                )
            )
        ),
        error_penalty=score_error_penalty,
        warning_penalty=score_warning_penalty,
        dimension_plan=(
            report_requirements[2]
            if report_requirements is not None
            else (compile_dimensions(model) if model is not None else None)
        ),
        requirement_outcomes=(report_requirements[1] if report_requirements is not None else None),
        _aggregation=aggregation,
    )
    return {
        "passed": errors == 0,
        "score": score,
        "diagnostic_score": score,
        "quality": quality,
        "layout_utilization": layout_utilization(),
        "review": review_explanation(
            quality=quality, errors=errors, warnings=warnings, score=score
        ),
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "by_code": by_code,
        "geometry_issues": sum(1 for i in issues if i.code in geometry_aware_codes),
        "issues": [
            {
                "severity": i.severity,
                "code": i.code,
                "message": i.message,
                "location": i.location,
                # Omit suggestion when None to keep the JSON non-breaking (#29).
                **({"suggestion": s} if (s := getattr(i, "suggestion", None)) is not None else {}),
                **({"source_ids": i.source_ids} if i.source_ids else {}),
                **({"annotation_name": i.annotation_name} if i.annotation_name else {}),
                **(
                    {"related_annotation_names": i.related_annotation_names}
                    if i.related_annotation_names
                    else {}
                ),
                **({"view": i.view} if i.view is not None else {}),
                **({"evidence_reason": i.evidence_reason} if i.evidence_reason else {}),
                **(
                    {"outcome_stage": i.outcome_stage}
                    if getattr(i, "outcome_stage", None) is not None
                    else {}
                ),
            }
            for i in issues
        ],
        **({"pmi": pmi} if pmi is not None else {}),
    }
