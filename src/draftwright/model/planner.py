"""planner — the dimensioning back-end over the IR (ADR 0008).

One rule set over `DimParameter`s, regardless of which feature produced them.
"A turned part's step lengths", "a hole's depth", "a slot's width" all arrive as
`DimParameter`s and are planned uniformly: a *convention* (how to dimension it) and
a *view* (where) are chosen by rule, and redundant measurements are dropped. This
is the logic currently reimplemented per-pass; here it is centralised so a new
feature inherits placement for free.

Prototype scope: enough rules to prove the protocol (convention + view selection,
diameter de-duplication). The full ISO/ASME rule set — datum selection, ordinate
vs chain by crowding, GD&T — grows here as real features demand it, not before.
"""

from __future__ import annotations

from dataclasses import dataclass

from draftwright.model.ir import DimParameter, PartModel

# How each (feature kind, parameter kind) is drawn. Defaults keep the table small.
_CONVENTION = {
    ("step", "length"): "chain",  # shoulder-to-shoulder run
    ("step", "diameter"): "leader",
    ("hole", "diameter"): "leader",
    ("hole", "depth"): "leader",  # part of the hole callout
    ("boss", "diameter"): "leader",
}


@dataclass(frozen=True)
class PlannedDimension:
    """A `DimParameter` with the convention and view the planner chose for it."""

    param: DimParameter
    feature_kind: str
    convention: str  # "chain" | "ordinate" | "leader" | "linear"
    view: str  # "front" | "plan" | "side" (placement intent)


def _view(orientation: str | None, param_kind: str) -> str:
    """The view that shows this parameter. For a turned part the lengthwise view is
    the front view (the axis lies in the X–Z plane); holes default to the plan."""
    if param_kind in ("length", "diameter"):
        return "front"
    return "plan"


def plan_dimensions(model: PartModel) -> list[PlannedDimension]:
    """Plan one dimension per `DimParameter`, applying convention/view rules and
    dropping redundant diameters (the same OD measured by two features)."""
    planned: list[PlannedDimension] = []
    seen_diam: set[float] = set()
    for feature in model.features:
        for p in feature.parameters():
            if p.kind == "location":
                continue  # datum-relative location: out of prototype scope
            if p.kind == "diameter":
                key = round(p.value, 2)
                if key in seen_diam:
                    continue  # redundancy avoidance (ISO 129)
                seen_diam.add(key)
            planned.append(
                PlannedDimension(
                    param=p,
                    feature_kind=feature.kind,
                    convention=_CONVENTION.get((feature.kind, p.kind), "linear"),
                    view=_view(model.orientation, p.kind),
                )
            )
    return planned
