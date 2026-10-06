"""Datum-referenced turned steps in the shared vertical dimension corridor."""

from draftwright._core import _dim
from draftwright.annotations._common import (
    _LOC_SUBCHAIN,
    PRIORITY,
    CorridorCandidate,
    dim_footprint,
    register_corridor,
)


def register_vertical_baseline_steps(
    dwg, view, segments, labels, prefix, start, bounds, ctx, report_drop
):
    analysis = dwg._analysis
    zones = getattr(analysis, {"front": "fv_zones", "side": "sv_zones"}.get(view, ""), None)
    strip = getattr(zones, "right", None) if zones is not None else None
    if strip is None:
        return None

    draft = dwg.draft
    base_x = bounds[2] + 2.0
    tier = draft.font_size + 2 * draft.pad_around_text
    for index, (segment, label) in enumerate(zip(segments, labels, strict=True)):
        name = f"{prefix}{start + index}"
        low, high = sorted((segment.pa[1], segment.pb[1]))

        def build(position, low=low, high=high, label=label):
            return _dim(
                (base_x, low, 0),
                (base_x, high, 0),
                "right",
                position - base_x,
                draft,
                label=label,
            )

        def footprint(position, low=low, high=high, label=label):
            return dim_footprint(
                (base_x, low, 0),
                (base_x, high, 0),
                "right",
                position - base_x,
                draft,
                label,
            )

        def drop(_name, measurements=segment.measurements):
            report_drop(measurements)

        register_corridor(
            ctx,
            (view, "right"),
            strip,
            view,
            "x",
            tier,
            CorridorCandidate(
                name=name,
                build=build,
                order=(_LOC_SUBCHAIN, abs(high - low), name),
                on_place=lambda _name: None,
                on_drop=drop,
                force=True,
                priority=PRIORITY.PRINCIPAL,
                measurement=segment.measurements,
                footprint=footprint,
            ),
        )
    return len(segments)
