"""Compiler-approved circular blind-step and channel leader jobs."""

from __future__ import annotations


def circular_recess_jobs(dwg, plan, *, only, kind, reach, tol_suffix, step_candidates):
    """Group coincident seats and retain each approved measurement identity."""
    draft = dwg.draft
    jobs = []
    grammar = {
        "circular_blind_step": (
            ("circular_step_radius", "radius", "R", ""),
            ("circular_step_depth", "length", "", " DEEP"),
        ),
        "circular_channel": (
            ("seat_diameter", "diameter", "ø", ""),
            ("seat_run", "length", "", " LONG"),
            ("seat_sweep", "angle", "", "° ARC"),
        ),
    }[kind]
    batches: dict[tuple, list] = {}
    for index, group in enumerate(plan.of_kind(kind)):
        if only is not None and group.ref not in only:
            continue
        dimensions = []
        text = []
        for role, dimension_kind, prefix, suffix in grammar:
            dimension = group.dim(role=role, kind=dimension_kind)
            if dimension is not None:
                dimensions.append(dimension)
                text.append(
                    f"{prefix}{dimension.value_text}{tol_suffix(dimension.tolerance, draft)}{suffix}"
                )
        if not dimensions or group.view is None:
            continue
        # Repeated coaxial seats project onto one arc. A counted callout preserves all
        # approved dimensions without contesting that same wall with duplicate leaders.
        key: tuple = (index,)
        if kind == "circular_channel":
            run = "xyz".index(group.facts.axis)
            centre = tuple(value for i, value in enumerate(group.facts.axis_origin) if i != run)
            key = (
                group.view,
                group.facts.axis,
                centre,
                tuple(
                    # Provider interval subtraction can leave a few binary ULPs (6 vs
                    # 5.999999999999996). Keep original values/ids; only the grouping
                    # key ignores sub-picometre arithmetic residue. Text must still agree.
                    (dimension.parameter_id, round(dimension.value, 12), rendered)
                    for dimension, rendered in zip(dimensions, text, strict=True)
                ),
            )
        batches.setdefault(key, []).append((index, group, dimensions, " × ".join(text)))
    for members in batches.values():
        index, group, _dimensions, label = members[0]
        view = group.view
        bounds = dwg.view_bounds(view)
        if bounds is None:
            continue
        if len(members) > 1:
            label = f"{len(members)}× {label}"

        def candidates(members=members, view=view, bounds=bounds, label=label):
            for _index, member, _dimensions, _text in members:
                for tip, elbow, owner in step_candidates(
                    dwg, view, bounds, member.facts, reach, label, provenance=member.ref
                ):
                    # Shared ink retains every measurement identity without making one
                    # seat's drop erase its siblings' dimensions.
                    yield tip, elbow, owner if len(members) == 1 else None

        jobs.append(
            (
                f"m_{kind}_{group.facts.axis}{index}",
                view,
                bounds,
                label,
                candidates(),
                tuple(
                    dimension.id
                    for _i, _g, dimensions, _text in members
                    for dimension in dimensions
                ),
            )
        )
    return jobs


def circular_step_candidates(
    dwg,
    view,
    bounds,
    feature,
    reach,
    label,
    *,
    provenance=None,
    radial_candidates,
    directions,
    text_size,
    font_path,
    leader_geometry,
    box_hits,
    segment_clips_box,
):
    """Yield solver candidates whose complete analytical leader clears every other view.

    The shared feature-leader solve is deliberately decomposed by semantic view.  A live
    single-feature replay therefore cannot rely on another view's annotations to represent
    that view's footprint.  Circular-step end-view leaders are one bounded four-candidate
    family, so reject routes whose label or shaft enters another composed view before handing
    the survivors to the same solver used by automatic and deferred placement (#1382).
    """
    width, height = text_size(
        str(label),
        float(dwg.draft.font_size),
        getattr(dwg.draft, "font_path", font_path),
        getattr(dwg.draft, "font", "Arial"),
    )
    callout_box = (0.0, 0.0, width, height)
    pad = max(float(dwg.draft.line_width), float(dwg.draft.arrow_length)) / 2
    other_views = []
    for other_view in dwg.views:
        if other_view == view:
            continue
        other = dwg.view_bounds(other_view)
        if other is not None:
            other_views.append((other[0] - pad, other[1] - pad, other[2] + pad, other[3] + pad))

    for tip, elbow, owner in radial_candidates(
        dwg,
        view,
        bounds,
        feature,
        reach,
        provenance=provenance,
        directions=directions,
    ):
        geometry = leader_geometry(tip, elbow, dwg.draft, callout_box=callout_box)
        if geometry is None:
            continue
        label_box, segments = geometry
        if label_box is None or box_hits(label_box, other_views):
            continue
        if any(
            segment_clips_box(first, second, other)
            for first, second in segments
            for other in other_views
        ):
            continue
        yield tip, elbow, owner
