"""Semantic completeness for whole-part polygonal stock (#1082)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from draftwright.linting.issues import LintIssue
from draftwright.recognition import RecognitionResult

PolygonalStockState = Literal["placed", "suppressed", "dropped", "missing", "unverifiable"]


@dataclass(frozen=True)
class PolygonalStockOutcome:
    parameter_id: str
    state: PolygonalStockState


def _rounded(value) -> float:
    return round(float(value), 3)


def _source_key(stock) -> tuple:
    return (
        stock.axis,
        tuple(_rounded(value) for value in stock.center),
        stock.side_count,
        _rounded(stock.across_flats),
        _rounded(stock.length),
    )


def _feature_key(feature) -> tuple:
    return (
        feature.frame.axis,
        tuple(_rounded(value) for value in feature.frame.origin),
        feature.side_count,
        _rounded(feature.across_flats),
        _rounded(feature.length),
    )


def polygonal_stock_outcomes(recognition, features, registry, omissions=()):
    """Join each geometric stock requirement to its compiler/placement outcome."""
    if recognition is None:
        return []
    if not isinstance(recognition, RecognitionResult):
        raise TypeError("polygonal_stock_outcomes() requires the run's RecognitionResult")
    by_key: dict[tuple, list] = {}
    for feature in features:
        if getattr(feature, "kind", None) == "polygonal_stock":
            by_key.setdefault(_feature_key(feature), []).append(feature)
    placed = {
        measurement for name in registry.names() for measurement in registry.measurement_of(name)
    }
    suppressed = {
        (omission.feature, omission.parameter_id)
        for omission in omissions
        if omission.feature is not None and omission.authored
    }
    dropped = {
        measurement
        for issue in registry.issues
        for measurement in getattr(issue, "measurement_ids", ())
    }
    outcomes = []
    for source in recognition.polygonal_stock:
        matches = by_key.get(_source_key(source), ())
        feature = matches[0] if len(matches) == 1 else None
        for parameter in ("polygon_across_flats.length", "stock_length.length"):
            if feature is None:
                state: PolygonalStockState = "unverifiable"
            elif any(
                measurement.feature == feature and measurement.parameter == parameter
                for measurement in placed
            ):
                state = "placed"
            elif (feature, parameter) in suppressed:
                state = "suppressed"
            elif any(
                measurement.feature == feature and measurement.parameter == parameter
                for measurement in dropped
            ):
                state = "dropped"
            else:
                state = "missing"
            outcomes.append(PolygonalStockOutcome(parameter, state))
    return outcomes


def lint_polygonal_stock_coverage(
    part, *, recognition, features, registry, omissions=(), assembly=None
) -> list[LintIssue]:
    """Require A/F and axial length, without duplicating an explicit placement drop."""
    if assembly is None:
        assembly = len(part.solids()) > 1
    messages = {
        "suppressed": "was deliberately omitted by the authored dimension set",
        "missing": "has no placed, suppressed, or dropped measurement outcome",
        "unverifiable": "cannot be joined to imported geometry without guessing",
    }
    issues = []
    for outcome in polygonal_stock_outcomes(recognition, features, registry, omissions):
        if outcome.state in {"placed", "dropped"}:
            continue
        issues.append(
            LintIssue(
                severity="info" if assembly else "warning",
                code=f"polygonal_stock_requirement_{outcome.state}",
                message=f"polygonal stock {outcome.parameter_id} {messages[outcome.state]}",
            )
        )
    return issues
