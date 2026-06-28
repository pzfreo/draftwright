"""planner — the dimensioning back-end over the IR (ADR 0008).

One rule set over `DimParameter`s, regardless of which feature produced them. The
contract was tightened twice under adversarial review of the counterbore work:

- **Grouping with an anchor and one view.** A feature's parameters form one
  `DimensionGroup` carrying the feature's `anchor` (so it can be placed) and a
  single `view` (so a compound callout — a hole's bore + counterbore + depth —
  renders as one callout in one place, not split across views/kinds).
- **No value-blind collapse.** The planner does *not* de-duplicate parameters by
  value: a `counterbore` ø16 and a `boss` ø16 are distinct, and a 10×10 pocket's
  two orthogonal 10 mm lengths are distinct. Features own their parameters; they
  do not emit spurious duplicates. Genuine redundancy/count of *repeated identical
  features* ("3× ø8") is upstream (pattern detection), not here.

Prototype scope: convention + group view selection. The full ISO/ASME rule set
grows here as real features demand it.
"""

from __future__ import annotations

from dataclasses import dataclass

from draftwright.model.ir import DimParameter, PartModel, Point

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


@dataclass(frozen=True)
class DimensionGroup:
    """A feature's planned dimensions, kept together with the feature's anchor and a
    single view so a compound callout renders as one callout in one place."""

    feature_kind: str
    view: str  # one view for the whole group
    anchor: Point  # the feature's location (Feature.frame.origin)
    dims: tuple[PlannedDimension, ...]


def _group_view(feature_kind: str, orientation: str | None) -> str:
    """The single view a feature's callout lands on. Turned profile + bosses read
    on the lengthwise (front) view; holes and the rest default to the plan."""
    return "front" if feature_kind in ("step", "boss") else "plan"


def plan_dimensions(model: PartModel) -> list[DimensionGroup]:
    """Plan each feature's parameters into one `DimensionGroup` (anchor + single
    view + planned dims). No cross- or within-feature value de-duplication."""
    groups: list[DimensionGroup] = []
    for feature in model.features:
        dims = [
            PlannedDimension(param=p, convention=_CONVENTION.get((p.role, p.kind), "linear"))
            for p in feature.parameters()
            if p.kind != "location"
        ]
        if dims:
            groups.append(
                DimensionGroup(
                    feature_kind=feature.kind,
                    view=_group_view(feature.kind, model.orientation),
                    anchor=feature.frame.origin,
                    dims=tuple(dims),
                )
            )
    return groups
