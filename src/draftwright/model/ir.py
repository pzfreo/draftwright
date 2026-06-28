"""ir — the part-drawing compiler's intermediate representation (ADR 0008).

The narrow waist between recognition and dimensioning. Two protocols carry the
weight:

- `DimParameter` — the universal currency of dimensioning: a single measurable
  quantity (a diameter, a length, a depth, …) with the value, the rendered label,
  the model-space extent it spans, and the datums it is measured from. Every
  feature, of every kind, describes itself as a list of these.
- `Feature` — anything dimensionable (a hole, a turned step, a boss, later a slot
  / chamfer / gear …). It exposes its `parameters()` and `references()`. **Adding
  a new shape is adding a new `Feature` type** — no change to the planner, the
  layout, or any other feature (Open/Closed).

`PartModel` is the whole-part IR the planner consumes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal, Protocol, runtime_checkable

Point = tuple[float, float, float]
ParamKind = Literal["diameter", "length", "depth", "radius", "angle", "location", "thread"]


@dataclass(frozen=True)
class Frame:
    """A feature's location and dominant orientation in part space.

    ``axis`` is the dominant axis letter (``"x"``/``"y"``/``"z"``) — enough for the
    prototype; it generalises to a full direction vector when a feature needs it.
    """

    origin: Point
    axis: str


@dataclass(frozen=True)
class Datum:
    """A reference the planner measures from (a face/axis/point). Minimal here."""

    id: str
    kind: Literal["plane", "axis", "point"]
    at: Point


@dataclass(frozen=True)
class DimParameter:
    """One measurable quantity a drawing must show for a feature."""

    kind: ParamKind
    value: float
    label: str
    span: tuple[Point, Point] | None = None
    refs: tuple[str, ...] = ()


@runtime_checkable
class Feature(Protocol):
    """Anything dimensionable. Implementations are plain (frozen) dataclasses, so
    ``kind`` is a class variable and ``frame`` is read-only."""

    kind: ClassVar[str]

    @property
    def frame(self) -> Frame: ...

    def parameters(self) -> list[DimParameter]: ...

    def references(self) -> list[Datum]: ...


@dataclass(frozen=True)
class HoleFeature:
    """A drilled hole — bore diameter, depth (blind), and location from datums."""

    frame: Frame
    diameter: float
    depth: float | None
    through: bool
    count: int = 1
    kind: ClassVar[str] = "hole"

    def parameters(self) -> list[DimParameter]:
        n = f"{self.count}× " if self.count > 1 else ""
        ps = [DimParameter("diameter", self.diameter, f"{n}ø{_fmt(self.diameter)}")]
        if not self.through and self.depth is not None:
            ps.append(DimParameter("depth", self.depth, f"↧{_fmt(self.depth)}"))
        ps.append(
            DimParameter("location", 0.0, "loc", span=(self.frame.origin, self.frame.origin))
        )
        return ps

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class StepFeature:
    """One axial segment of a turned profile — its length and its OD."""

    frame: Frame
    length: float
    diameter: float
    span: tuple[Point, Point]
    kind: ClassVar[str] = "step"

    def parameters(self) -> list[DimParameter]:
        return [
            DimParameter("length", self.length, _fmt(self.length), span=self.span),
            DimParameter("diameter", self.diameter, f"ø{_fmt(self.diameter)}"),
        ]

    def references(self) -> list[Datum]:
        return []


@dataclass(frozen=True)
class BossFeature:
    """An external cylindrical boss/OD on a non-turned part — its diameter."""

    frame: Frame
    diameter: float
    kind: ClassVar[str] = "boss"

    def parameters(self) -> list[DimParameter]:
        return [DimParameter("diameter", self.diameter, f"ø{_fmt(self.diameter)}")]

    def references(self) -> list[Datum]:
        return []


@dataclass
class PartModel:
    """The whole-part IR: the oriented part plus its features and datums."""

    bbox: object  # build123d BoundBox
    orientation: str | None  # turning axis if rotational, else None
    features: list[Feature] = field(default_factory=list)
    datums: list[Datum] = field(default_factory=list)


def _fmt(v: float) -> str:
    """Local number formatter (mirrors `_core._fmt`; kept local so the prototype
    has no upward import). Integers render without a trailing ``.0``."""
    return f"{v:.0f}" if abs(v - round(v)) < 1e-6 else f"{v:.1f}"
