"""Typed, render-free annotation topology for outer sheet planning.

This is the observational first slice of a drafter-style layout scheme.  It records which
semantic annotations need which view corridors before OCC annotation geometry exists.  The
current renderer still makes every placement decision; consumers may compare this plan with
measured view blocks without changing drawing output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from draftwright.model.ir import authored_dimension_target_view

_VIEWS = frozenset({"front", "plan", "side", "rear"})
_SIDES = frozenset({"above", "below", "left", "right"})


@dataclass(frozen=True)
class AnnotationDemand:
    """One semantic request for a view-side corridor, without page coordinates."""

    identity: str
    family: str
    view: str
    side: str
    feature_index: int
    model_site: tuple[float, float, float]


@dataclass(frozen=True)
class UnplannedAnnotation:
    """A visible semantic carrier whose corridor cannot yet be selected truthfully."""

    identity: str
    family: str
    feature_index: int
    reason: str


@dataclass(frozen=True)
class AnnotationScheme:
    """Pre-render annotation topology plus explicit planning gaps."""

    demands: tuple[AnnotationDemand, ...]
    unplanned: tuple[UnplannedAnnotation, ...]

    def corridor(self, view: str, side: str) -> tuple[AnnotationDemand, ...]:
        return tuple(
            demand for demand in self.demands if (demand.view, demand.side) == (view, side)
        )

    def corridor_counts(self) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = {}
        for demand in self.demands:
            key = (demand.view, demand.side)
            counts[key] = counts.get(key, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "demands": [
                {
                    "identity": demand.identity,
                    "family": demand.family,
                    "view": demand.view,
                    "side": demand.side,
                    "feature_index": demand.feature_index,
                    "model_site": list(demand.model_site),
                }
                for demand in self.demands
            ],
            "unplanned": [
                {
                    "identity": item.identity,
                    "family": item.family,
                    "feature_index": item.feature_index,
                    "reason": item.reason,
                }
                for item in self.unplanned
            ],
        }


def _identity(feature, index: int) -> str:
    return str(
        getattr(feature, "source_id", "")
        or next(iter(getattr(feature, "source_ids", ())), "")
        or f"{getattr(feature, 'kind', type(feature).__name__)}:{index}"
    )


def _site(feature) -> tuple[float, float, float]:
    origin = getattr(getattr(feature, "frame", None), "origin", (0.0, 0.0, 0.0))
    return tuple(float(origin[index]) for index in range(3))


def plan_annotation_scheme(model) -> AnnotationScheme:
    """Collect explicit dimension/PMI furniture corridor demand from a ``PartModel``.

    Geometry-derived dimensions and feature leaders remain in the existing estimators for this
    prototype. Raw PMI is carried as ``unplanned`` rather than assigned to a guessed corridor.
    """

    demands: list[AnnotationDemand] = []
    unplanned: list[UnplannedAnnotation] = []
    for index, feature in enumerate(model.features):
        kind = getattr(feature, "kind", "")
        identity = _identity(feature, index)
        view = getattr(feature, "view", None)
        side = getattr(feature, "side", None)
        family = kind

        if kind == "authored_dimension":
            view = authored_dimension_target_view(
                getattr(feature, "dimension_kind", ""),
                getattr(feature, "dominant_axis", ""),
                view,
                side,
                getattr(feature, "angular_reference", None),
                getattr(feature, "ref_pts", ()),
            )
            if side is None:
                unplanned.append(
                    UnplannedAnnotation(identity, family, index, "no explicit corridor side")
                )
                continue
        elif kind == "pmi":
            unplanned.append(
                UnplannedAnnotation(identity, family, index, "raw PMI has no typed corridor")
            )
            continue
        elif kind not in {"control_frame", "datum_ref", "note"}:
            continue

        if view not in _VIEWS or side not in _SIDES:
            unplanned.append(
                UnplannedAnnotation(identity, family, index, "invalid or absent view-side route")
            )
            continue
        demands.append(AnnotationDemand(identity, family, view, side, index, _site(feature)))

    return AnnotationScheme(tuple(demands), tuple(unplanned))
