"""Run-local measurement witnesses and shared location applicability rules."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from draftwright.registry import MeasurementCell


@dataclass(frozen=True)
class MeasurementSupport:
    """An exact owner and optional interval or location-member restriction."""

    feature: object
    parameter_id: str
    axis: str | None = None
    lo: float | None = None
    hi: float | None = None
    location_point: tuple[float, float, float] | None = None

    @property
    def identity(self) -> tuple[object, str]:
        return self.feature, self.parameter_id

    @property
    def value(self) -> float:
        if self.lo is None or self.hi is None:
            raise ValueError("this measurement support has no interval")
        return abs(self.hi - self.lo)


@dataclass(frozen=True)
class RequirementAlternative:
    """A conjunction with both legacy local identities and exact carrying witnesses."""

    measurements: tuple[tuple[object, str], ...]
    supports: tuple[MeasurementSupport, ...]


@dataclass(frozen=True)
class RequirementExclusion:
    """A source-owned rule, optionally scoped to an exact common location datum."""

    reason_code: str
    source_records: tuple[object, ...]
    datum: object | None = None
    span: tuple | None = None


@dataclass(frozen=True)
class RequirementCarrier:
    """A named annotation accepted by a physical requirement producer."""

    annotation: str
    kind: str
    cell: MeasurementCell | None = None


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
