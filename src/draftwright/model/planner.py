"""planner — the dimensioning back-end over the IR (ADR 0008).

One rule set over `DimParameter`s, regardless of which feature produced them. Two
contract points the adversarial review of the counterbore work forced:

- **Grouping.** A feature's parameters stay together as a `DimensionGroup`, so a
  compound callout — a hole's bore + counterbore + depth — is one callout, not
  three independent dims. The planner returns groups, not a flat list.
- **Redundancy is feature-aware.** The planner does *not* collapse two parameters
  just because they share a value: a `counterbore` ø16 and a `boss` ø16 are
  distinct (different `role`) and both survive. Only genuinely identical
  measurements within a group are de-duplicated. Count-aggregation of repeated
  identical features ("3× ø8") is upstream (pattern detection), not here.

Prototype scope: convention + view selection and grouping. The full ISO/ASME rule
set grows here as real features demand it.
"""

from __future__ import annotations

from dataclasses import dataclass

from draftwright.model.ir import DimParameter, PartModel

# How each (role, kind) is drawn. Defaults keep the table small.
_CONVENTION = {
    ("step", "length"): "chain",
    ("step", "diameter"): "leader",
    ("bore", "diameter"): "leader",
    ("bore", "depth"): "leader",
    ("counterbore", "diameter"): "leader",
    ("counterbore", "depth"): "leader",
    ("spotface", "diameter"): "leader",
    ("spotface", "depth"): "leader",
    ("boss", "diameter"): "leader",
}


@dataclass(frozen=True)
class PlannedDimension:
    param: DimParameter
    convention: str  # "chain" | "ordinate" | "leader" | "linear"
    view: str  # "front" | "plan" | "side"


@dataclass(frozen=True)
class DimensionGroup:
    """A feature's planned dimensions, kept together so a compound callout (e.g. a
    hole's bore + counterbore + depth) renders as one callout."""

    feature_kind: str
    dims: tuple[PlannedDimension, ...]


def _view(orientation: str | None, kind: str) -> str:
    if kind in ("length", "diameter"):
        return "front"
    return "plan"


def plan_dimensions(model: PartModel) -> list[DimensionGroup]:
    """Plan each feature's parameters into a `DimensionGroup`. No cross-feature
    value-collapse; identical measurements *within* a group are de-duplicated."""
    groups: list[DimensionGroup] = []
    for feature in model.features:
        seen: set[tuple[str, str, float]] = set()
        dims: list[PlannedDimension] = []
        for p in feature.parameters():
            if p.kind == "location":
                continue  # datum-relative location: out of prototype scope
            key = (p.kind, p.role, round(p.value, 2))
            if key in seen:
                continue  # genuine duplicate within the feature
            seen.add(key)
            dims.append(
                PlannedDimension(
                    param=p,
                    convention=_CONVENTION.get((p.role, p.kind), "linear"),
                    view=_view(model.orientation, p.kind),
                )
            )
        if dims:
            groups.append(DimensionGroup(feature_kind=feature.kind, dims=tuple(dims)))
    return groups
