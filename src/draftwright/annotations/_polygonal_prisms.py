"""Compiler-approved polygonal prism leader jobs and physical wall anchors.

The public passes in ``from_model`` submit these jobs to the shared late leader
inventory. The owner reads the compiled dimensions and the IR's supported flat
faces; it does not commit placement or recognise geometry.
"""

from __future__ import annotations

import math

from draftwright._core import _END_ON, _tol_suffix
from draftwright.annotations._common import _ray_exit_dist


def _polygonal_boss_candidates(dwg, view, vb, boss, reach, *, provenance):
    """Leader candidates anchored on the prism's physical side faces."""
    origin = dwg.at(view, *boss.frame.origin)
    for flat_centre, flat_direction in zip(boss.flat_centres, boss.flat_directions, strict=True):
        tip = dwg.at(view, *flat_centre)
        direction_end = dwg.at(
            view,
            boss.frame.origin[0] + flat_direction[0],
            boss.frame.origin[1] + flat_direction[1],
            boss.frame.origin[2] + flat_direction[2],
        )
        dx, dy = direction_end[0] - origin[0], direction_end[1] - origin[1]
        distance = math.hypot(dx, dy)
        if distance <= 1e-9:
            continue
        ux, uy = dx / distance, dy / distance
        exit_distance = _ray_exit_dist(tip[0], tip[1], ux, uy, vb)
        elbow = (
            tip[0] + ux * (exit_distance + reach),
            tip[1] + uy * (exit_distance + reach),
            0,
        )
        yield (tip, elbow, provenance)


def polygonal_prism_jobs(
    dwg, plan, *, kind: str, name_prefix: str, only=None, leader_callout_reach
):
    """Compile polygonal wall anchors and approved dimensions into shared leader jobs."""
    draft = dwg.draft
    reach = leader_callout_reach(draft)
    jobs = []
    groups = sorted(
        plan.of_kind(kind),
        key=lambda group: (group.facts.frame.axis, group.facts.frame.origin),
    )
    batches: dict[tuple, list] = {}
    for index, group in enumerate(groups):
        dimensions, text = [], []
        across = group.dim(role="polygon_across_flats", kind="length")
        if across is not None:
            prefix = "HEX" if group.facts.side_count == 6 else f"{group.facts.side_count}-SIDED"
            text.append(f"{prefix} {across.value_text}{_tol_suffix(across.tolerance, draft)} A/F")
            dimensions.append(across)
        if kind == "hex_pocket":
            depth = group.dim(role="pocket_depth", kind="length")
            if depth is not None:
                text.append(f"{depth.value_text}{_tol_suffix(depth.tolerance, draft)} DEEP")
                dimensions.append(depth)
        if not dimensions:
            continue
        key: tuple = (index,)
        if kind == "hex_pocket":
            key = (
                group.facts.frame.axis,
                group.facts.open_sign,
                tuple(
                    (dimension.parameter_id, round(dimension.value, 12), label)
                    for dimension, label in zip(dimensions, text, strict=True)
                ),
            )
        batches.setdefault(key, []).append((index, group, dimensions, " × ".join(text)))
    for members in batches.values():
        index = members[0][0]
        if only is not None:
            members = [member for member in members if member[1].ref in only]
            if not members:
                continue
        _, group, _, label = members[0]
        view = _END_ON.get(group.facts.frame.axis)
        if view is None:
            continue
        bounds = dwg.view_bounds(view)
        if bounds is None:
            continue
        if len(members) > 1:
            label = f"{len(members)}× {label}"

        def candidates(members=members, view=view, bounds=bounds):
            for _, member, _, _ in members:
                yield from _polygonal_boss_candidates(
                    dwg,
                    view,
                    bounds,
                    member.facts,
                    reach,
                    provenance=member.ref if len(members) == 1 else None,
                )

        jobs.append(
            (
                f"{name_prefix}_{group.facts.frame.axis}{index}",
                view,
                bounds,
                label,
                candidates(),
                tuple(dimension.id for _, _, dimensions, _ in members for dimension in dimensions),
            )
        )
    return jobs
