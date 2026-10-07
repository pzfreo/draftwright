"""Compiler-approved pocket and pad-height leader job producers.

The public passes in ``from_model`` submit these jobs to the shared late
feature-leader inventory. This owner decides label and candidate content only.
"""

from __future__ import annotations

from draftwright._core import _END_ON, _tol_suffix


def _pocket_label(
    width_text, length_text, depth_text, wsfx="", lsfx="", dsfx="", *, maximum_depth=False
) -> str:
    """Keep each approved tolerance beside its pocket value.

    A plain leader cannot draw the helper's geometric depth glyph, so its label
    uses the font-safe ``DEEP`` word.
    """
    depth_word = "MAX DEEP" if maximum_depth else "DEEP"
    if all(value is not None for value in (width_text, length_text, depth_text)):
        label = f"{width_text}{wsfx} × {length_text}{lsfx} × {depth_text}{dsfx} {depth_word}"
    else:
        label = "POCKET " + ", ".join(
            f"{value}{suffix} {role}"
            for value, suffix, role in (
                (width_text, wsfx, "WIDE"),
                (length_text, lsfx, "LONG"),
                (depth_text, dsfx, depth_word),
            )
            if value is not None
        )
    return label


def _pad_height_label(height_text, suffix="") -> str:
    """The font-safe attachment-axis height callout for a side-normal pad."""
    return f"{height_text}{suffix} HIGH"


# Unit lead directions. Diagonals remain first as the stable tie-break, while
# within-pass assignment normally selects the shortest jointly compatible ray.
_POCKET_LEAD_DIRS = (
    (1, 1),
    (-1, 1),
    (-1, -1),
    (1, -1),
    (1, 0),
    (0, 1),
    (-1, 0),
    (0, -1),
)

# A side-view pad height belongs in the exterior upper-right quadrant.  Keeping its
# leader there prevents it from entering the adjacent front view, which a view-scoped
# feature-leader solve deliberately does not own.  The lower-right ray is excluded too:
# the side-below overall/location ladder can otherwise run through the HIGH label even
# though the shared solver legitimately retains the required leader under Policy B.
# Front and plan pads retain the established full fan; those views already participate in
# the fixed-ink solve without the side/front adjacency that motivated this constraint.
_PAD_HEIGHT_LEAD_DIRS = {
    "x": ((1, 1), (1, 0)),
    "y": _POCKET_LEAD_DIRS,
    "z": _POCKET_LEAD_DIRS,
}


def _rectangular_rim_bounds(
    dwg, view, feature, *, long_axis: str, width_axis: str, length: float, width: float
) -> tuple[float, float, float, float]:
    """Projected bounds of a rectangular opening in its face-on view."""
    centre = list(feature.frame.origin)
    points = []
    for long_sign in (-1, 1):
        for width_sign in (-1, 1):
            corner = centre.copy()
            corner["xyz".index(long_axis)] += long_sign * length / 2
            corner["xyz".index(width_axis)] += width_sign * width / 2
            points.append(dwg.at(view, *corner))
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def pocket_jobs(dwg, plan, *, only=None, leader_callout_reach, radial_candidates):
    """Build pocket jobs from approved values, each bound by its role and kind.

    The callout reads in the view normal to pocket depth. ``g.view`` instead
    follows its long axis, so the producer keeps its separate depth-to-view map.
    A mid-face pocket offers rays toward each margin to the shared assignment.
    """
    draft = dwg.draft
    reach = leader_callout_reach(draft)
    view_of = _END_ON
    pocket_groups = list(plan.of_kind("pocket"))
    jobs = []
    requested_sides = {}
    for i, g in enumerate(
        sorted(pocket_groups, key=lambda g: (g.facts.width_axis, g.facts.frame.origin))
    ):
        pk = g.facts
        if only is not None and g.ref not in only:
            continue  # #426 Ph2b subset (finalize): skip in place — i stays the model index
        by_key = {(pd.role, pd.kind): pd for pd in g.dims}
        wpd = by_key.get(("pocket_width", "length"))
        lpd = by_key.get(("pocket_length", "length"))
        dpd = by_key.get(("pocket_depth", "length")) or by_key.get(("pocket_max_depth", "length"))
        dimensions = tuple(d for d in (wpd, lpd, dpd) if d is not None)
        if not dimensions:
            continue
        view = view_of.get(pk.depth_axis)
        if view is None:
            continue
        vb = dwg.view_bounds(view)
        if vb is None:
            continue
        name = f"m_pocket_{pk.width_axis}{pk.long_axis}{i}"
        if g.side is not None:
            requested_sides[name] = g.side
        directions = tuple(
            (dx, dy)
            for dx, dy in _POCKET_LEAD_DIRS
            if g.side is None
            or (g.side == "left" and dx < 0)
            or (g.side == "right" and dx > 0)
            or (g.side == "above" and dy > 0)
            or (g.side == "below" and dy < 0)
        )
        jobs.append(
            (
                name,
                view,
                vb,
                _pocket_label(
                    wpd.value_text if wpd is not None else None,
                    lpd.value_text if lpd is not None else None,
                    dpd.value_text if dpd is not None else None,
                    wsfx=_tol_suffix(wpd.tolerance, draft) if wpd is not None else "",
                    lsfx=_tol_suffix(lpd.tolerance, draft) if lpd is not None else "",
                    maximum_depth=dpd is not None and dpd.role == "pocket_max_depth",
                    dsfx=_tol_suffix(dpd.tolerance, draft) if dpd is not None else "",
                ),
                radial_candidates(
                    dwg,
                    view,
                    vb,
                    pk,
                    reach,
                    source_bounds=_rectangular_rim_bounds(
                        dwg,
                        view,
                        pk,
                        long_axis=pk.long_axis,
                        width_axis=pk.width_axis,
                        length=lpd.value,
                        width=wpd.value,
                    )
                    if wpd is not None and lpd is not None
                    else None,
                    directions=directions,
                    provenance=g.ref,
                ),
                tuple(d.id for d in dimensions),
            )
        )
    return jobs, requested_sides


def pad_height_jobs(dwg, plan, *, only=None, leader_callout_reach, radial_candidates):
    """Prepare pad heights as solver-owned leaders in each pad's end-on view.

    The footprint dimensions remain linear corridor candidates. A Z profile
    level measures datum to attachment, so Z pads also need their local height.
    """
    draft = dwg.draft
    reach = leader_callout_reach(draft)
    jobs = []
    groups = sorted(
        plan.of_kind("pad"), key=lambda group: (group.facts.frame.axis, group.facts.frame.origin)
    )
    for index, group in enumerate(groups):
        if only is not None and group.ref not in only:
            continue
        by_key = {(item.role, item.kind): item for item in group.dims}
        height = by_key.get(("pad_height", "length"))
        if height is None:
            continue
        # Structural placement facts come through the compiled boundary.  Resolving the
        # opaque provenance handle here would let this renderer recover measurements the
        # compiler withheld under authored intent (ADR 1 (was 0015) / ADR 4 (was 0016)).
        pad = group.facts
        view = _END_ON[pad.frame.axis]
        bounds = dwg.view_bounds(view)
        if bounds is None:
            continue
        # An authored set may request the independently addressable height while omitting
        # both footprint measurements.  In that case the terminal face centre is still a
        # complete structural leader target; do not recover the suppressed sizes through
        # provenance merely to move the arrow to the rim (ADR 1 (was 0015) / ADR 4 (was 0016)).  When both approved
        # sizes are present, their values may refine that same target to the footprint edge.
        width = by_key.get(("pad_width", "length"))
        length = by_key.get(("pad_length", "length"))
        source_bounds = (
            _rectangular_rim_bounds(
                dwg,
                view,
                pad,
                long_axis=pad.long_axis,
                width_axis=pad.width_axis,
                length=length.value,
                width=width.value,
            )
            if width is not None and length is not None
            else None
        )
        jobs.append(
            (
                f"m_pad_height_{pad.frame.axis}{index}",
                view,
                bounds,
                _pad_height_label(
                    height.value_text,
                    _tol_suffix(height.tolerance, draft),
                ),
                radial_candidates(
                    dwg,
                    view,
                    bounds,
                    pad,
                    reach,
                    source_bounds=source_bounds,
                    directions=_PAD_HEIGHT_LEAD_DIRS[pad.frame.axis],
                    provenance=group.ref,
                ),
                (height.id,),
            )
        )
    return jobs
