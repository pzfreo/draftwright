"""Run-local measurement witnesses and shared location applicability rules."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


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


def square_polygonal_boss_pad_owner(boss, pads, evidence):
    """Return the unique exact same-run pad whose faces include a four-flat boss."""
    if evidence is None or boss.side_count != 4:
        return None
    boss_occurrences = tuple(
        occurrence
        for occurrence in evidence.features
        if evidence.family(occurrence) == "polygonal_bosses"
        and evidence.record(occurrence) is boss
    )
    if len(boss_occurrences) != 1:
        return None
    boss_faces = evidence.defining_faces(boss_occurrences[0])
    if not boss_faces:
        return None
    candidates = []
    for pad in pads:
        pad_occurrences = tuple(
            occurrence
            for occurrence in evidence.features
            if evidence.family(occurrence) == "pads" and evidence.record(occurrence) is pad
        )
        if len(pad_occurrences) != 1 or not boss_faces < evidence.defining_faces(
            pad_occurrences[0]
        ):
            continue
        bounds = {
            "x": (pad.x0, pad.x1),
            "y": (pad.y0, pad.y1),
            "z": (pad.z0, pad.z1),
        }
        if (
            boss.axis == pad.axis
            and abs(boss.base - bounds[pad.axis][0]) <= 1e-6
            and abs(boss.top - bounds[pad.axis][1]) <= 1e-6
            and all(
                abs(boss.center[index] - sum(bounds[axis]) / 2) <= 1e-6
                for index, axis in enumerate("xyz")
            )
            and all(
                abs(boss.across_flats - (bounds[axis][1] - bounds[axis][0])) <= 1e-6
                for axis in "xyz"
                if axis != pad.axis
            )
        ):
            candidates.append(pad)
    return candidates[0] if len(candidates) == 1 else None


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
