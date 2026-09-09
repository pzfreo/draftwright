"""Shared datum-reference and coincidence rules for pocket and pad locations."""

from __future__ import annotations

from math import isfinite

from draftwright.measurement_support import RequirementExclusion


def pocket_location_reference(feature, datum_at):
    """Retain the planner's centre/near-end reference selection exactly."""
    point = list(feature.frame.origin)
    index = "xyz".index(feature.long_axis)
    point[index] = (
        feature.lo if abs(feature.lo - datum_at[index]) <= 1e-6 else (feature.lo + feature.hi) / 2
    )
    point["xyz".index(feature.width_axis)] = feature.w_center
    return tuple(point)


def coincident_location_axes(feature, span):
    """Existing compiler applicability, without expanding the Z-pad grammar."""
    kind, axis = feature.kind, feature.frame.axis
    if kind not in {"pad", "pocket"} or (kind == "pad" and axis == "z"):
        return ()
    axes = ("x", "y") if axis == "z" else (feature.long_axis, feature.width_axis)
    return tuple(
        measured
        for measured in axes
        if isfinite(span[0]["xyz".index(measured)])
        and isfinite(span[1]["xyz".index(measured)])
        and abs(span[1]["xyz".index(measured)] - span[0]["xyz".index(measured)]) <= 1e-6
    )


def datum_location_exclusion(feature, source, parameter, datum):
    """Retain coincidence proof before authored filtering, without inspecting ink."""
    if datum is None or getattr(datum, "id", None) != "datum_xy":
        return None
    try:
        reference = (
            pocket_location_reference(feature, datum.at)
            if feature.kind == "pocket"
            else tuple(feature.frame.origin)
        )
        span = (tuple(datum.at), reference)
        prefix = "location_pocket.location" if feature.kind == "pocket" else "location_pad"
        if parameter not in {
            f"{prefix}.{axis}" for axis in coincident_location_axes(feature, span)
        }:
            return None
        return RequirementExclusion(
            "location_coincident_with_common_datum", (source,), datum, span
        )
    except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
        return None
