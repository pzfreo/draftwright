"""ir — the part-drawing compiler's intermediate representation (ADR 0008).

The narrow waist between recognition and dimensioning. Two protocols carry the
weight:

- `DimParameter` — the universal currency of dimensioning: one measurable quantity
  with a `kind` (diameter / length / depth / …), a semantic `role` (bore /
  counterbore / step / boss / …), the value, the model-space extent it spans, and
  the datums it is measured from. **It carries no rendered label** — formatting
  (and GD&T symbols, which are drawn as geometry, not font text — the pinned font
  has no ⌴/⌵/↧ glyphs) is a renderer concern. `display()` gives a font-safe text
  form for debug/tests.
- `Feature` — anything dimensionable. It exposes `parameters()` and `references()`.
  **Adding a new shape is adding a new `Feature` type** (Open/Closed).

`PartModel` is the whole-part IR the planner consumes. The planner groups a
feature's parameters so compound callouts (a hole's bore + counterbore + depth)
stay one callout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Literal, Protocol, runtime_checkable

Point = tuple[float, float, float]
ParamKind = Literal["diameter", "length", "depth", "radius", "angle", "location", "thread"]
# `role` is the semantic origin of the measurement (open set — new features add
# roles): "bore", "counterbore", "spotface", "od", "step", "boss", "thread",
# "pattern", "slot", "envelope", "location", …
Role = str


@dataclass(frozen=True)
class Frame:
    """A feature's location and dominant orientation in part space (the axis
    letter is enough for now; it generalises to a direction vector)."""

    origin: Point
    axis: str


@dataclass(frozen=True)
class Datum:
    """A reference the planner measures from (a face/axis/point)."""

    id: str
    kind: Literal["plane", "axis", "point"]
    at: Point


@dataclass(frozen=True)
class DimParameter:
    """One measurable quantity a drawing must show. No rendered label — see module
    docstring; use :func:`display` for font-safe text."""

    kind: ParamKind
    role: Role
    value: float
    span: tuple[Point, Point] | None = None
    refs: tuple[str, ...] = ()


def _fmt(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 1e-6 else f"{v:.1f}"


def display(p: DimParameter) -> str:
    """A font-safe text form of a parameter (uses only glyphs the pinned font has;
    GD&T symbols are the renderer's job). For debug and tests, not output."""
    if p.kind == "diameter":
        return f"ø{_fmt(p.value)}"
    if p.kind == "depth":
        return f"{_fmt(p.value)} deep"
    return _fmt(p.value)


@runtime_checkable
class Feature(Protocol):
    """Anything dimensionable. Implementations are frozen dataclasses, so ``kind``
    is a class variable and ``frame`` is read-only."""

    kind: ClassVar[str]

    @property
    def frame(self) -> Frame: ...

    def parameters(self) -> list[DimParameter]: ...

    def references(self) -> list[Datum]: ...


@dataclass(frozen=True)
class HoleFeature:
    """A drilled hole — bore + optional counterbore / spotface steps. The bore,
    counterbore, and spotface share one feature so the planner renders them as one
    compound callout. ``cbore``/``spotface`` are ``(diameter, depth)`` or ``None``
    (plain tuples — the IR stays decoupled from the recogniser's types)."""

    frame: Frame
    diameter: float
    depth: float | None
    through: bool
    count: int = 1
    cbore: tuple[float, float] | None = None
    spotface: tuple[float, float] | None = None
    kind: ClassVar[str] = "hole"

    def parameters(self) -> list[DimParameter]:
        # Location is the group's anchor (Feature.frame), not a parameter.
        ps = [DimParameter("diameter", "bore", self.diameter)]
        if not self.through and self.depth is not None:
            ps.append(DimParameter("depth", "bore", self.depth))
        if self.cbore is not None:
            cd, cdp = self.cbore
            ps.append(DimParameter("diameter", "counterbore", cd))
            ps.append(DimParameter("depth", "counterbore", cdp))
        if self.spotface is not None:
            sd, sdp = self.spotface
            ps.append(DimParameter("diameter", "spotface", sd))
            ps.append(DimParameter("depth", "spotface", sdp))
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
            DimParameter("length", "step", self.length, span=self.span),
            DimParameter("diameter", "step", self.diameter),
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
        return [DimParameter("diameter", "boss", self.diameter)]

    def references(self) -> list[Datum]:
        return []


@dataclass
class PartModel:
    """The whole-part IR: the oriented part plus its features and datums."""

    bbox: object  # build123d BoundBox
    orientation: str | None  # turning axis if rotational, else None
    features: list[Feature] = field(default_factory=list)
    datums: list[Datum] = field(default_factory=list)
