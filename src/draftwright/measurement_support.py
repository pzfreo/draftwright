"""Run-local measurement witnesses issued by physical requirement producers."""

from __future__ import annotations

from dataclasses import dataclass

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
