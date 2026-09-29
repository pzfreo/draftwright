"""Groove physical correspondence, declaration, and finished-drawing evidence.

The evidence oracle remains independent of production formatting and routing. Engine
imports stay inside functions to preserve evaluation's lazy-load seam.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

Scalar: TypeAlias = int | float | str | bool
Value: TypeAlias = Scalar | tuple[float, ...]
Outcome: TypeAlias = Literal["supported", "unknown", "unsupported"]


def _groove_point(groove) -> tuple[float, float, float]:
    point = getattr(groove, "at", None)
    if point is None:
        point = groove.frame.origin
    return tuple(round(float(component), 3) for component in point)  # type: ignore[return-value]


def _groove_identity(groove) -> tuple[str, tuple[float, float, float]]:
    return str(groove.axis), _groove_point(groove)


def _groove_parameters(groove) -> dict[str, Value]:
    return {
        "width": round(float(groove.width), 3),
        "diameter": round(float(groove.diameter), 3),
    }


def _groove_correspondence(grooves, recognition, features, registry=None, omissions=()):
    """Per physical groove, retain exact IR and production-ledger evidence."""
    from draftwright.linting.groove_coverage import groove_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    ledger = groove_requirement_outcomes(
        recognition,
        features,
        AnnotationRegistry() if registry is None else registry,
        omissions,
    )
    by_at: dict[tuple[float, float, float], list] = {}
    for outcome in ledger:
        by_at.setdefault(outcome.source_at, []).append(outcome)
    result = []
    expected_ids = {"groove.length", "groove.diameter"}
    for source in grooves:
        candidates = [
            outcome
            for outcome in by_at.get(_groove_point(source), ())
            if outcome.features
            and _groove_identity(outcome.features[0]) == _groove_identity(source)
            and _groove_parameters(outcome.features[0]) == _groove_parameters(source)
        ]
        candidate_features = tuple(
            dict.fromkeys(feature for outcome in candidates for feature in outcome.features)
        )
        exact = (
            len(candidate_features) == 1
            and len(candidates) == 2
            and {outcome.parameter_id for outcome in candidates} == expected_ids
            and all(outcome.features == candidate_features for outcome in candidates)
        )
        result.append((exact, candidate_features, tuple(candidates)))
    return result


def _groove_model_outcomes(grooves, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _features, _outcomes in _groove_correspondence(grooves, recognition, features)
    ]


def _groove_expected_tolerance_suffix(tolerance, draft) -> str:
    """Independently state the callout tolerance grammar used by the evidence oracle.

    This deliberately does not call the production formatter: otherwise a renderer regression
    changes both the ink and its expected answer and the completeness metric certifies itself.
    """
    from draftwright.fits import FitClass

    if tolerance is None:
        return ""
    if isinstance(tolerance, FitClass):
        if tolerance.show == "class":
            return f" {tolerance.code}"

        def deviation(value: float) -> str:
            if abs(value) < 5e-5:
                return "0"
            text = f"{value:+.4f}"
            return text[:-1] if text.endswith("0") else text

        return f" {deviation(tolerance.upper)}/{deviation(tolerance.lower)}"
    precision = draft.decimal_precision
    if isinstance(tolerance, (int, float)):
        return f" ±{round(tolerance, precision):.{precision}f}"
    lower, upper = tolerance
    return f" +{round(upper, precision):.{precision}f} -{round(lower, precision):.{precision}f}"


def _groove_drawing_outcomes(grooves, drawing) -> list[Outcome]:
    """Verify both exact measurement identities on one compiler-approved groove callout."""
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    correspondence = _groove_correspondence(
        grooves,
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
    )
    confirmed: dict[tuple[object, str], set[str]] = {}
    for claim in verify_measurement_claims(drawing.registry, plan):
        measurement = claim.measurement
        if claim.state != "confirmed" or measurement is None:
            continue
        key = (
            getattr(measurement, "feature", None),
            str(getattr(measurement, "parameter", "")),
        )
        confirmed.setdefault(key, set()).add(claim.annotation)

    expected_labels: dict[object, str] = {}
    for group in plan.of_kind("groove"):
        by_parameter = {
            str(approved.id.parameter): approved
            for approved in group.dims
            if approved.id is not None
        }
        width = by_parameter.get("groove.length")
        diameter = by_parameter.get("groove.diameter")
        if width is None or diameter is None or width.id is None:
            continue
        width_text = width.value_text + _groove_expected_tolerance_suffix(
            width.tolerance, drawing.draft
        )
        diameter_text = diameter.value_text + _groove_expected_tolerance_suffix(
            diameter.tolerance, drawing.draft
        )
        expected_labels[width.id.feature] = f"{width_text} WIDE × ø{diameter_text}"

    def rendered_label(name: str) -> str | None:
        annotation = drawing.registry.named(name)
        label = getattr(annotation, "label", None) or getattr(annotation, "_annotate_label", None)
        return label if isinstance(label, str) else None

    def leader_targets_groove(name: str, feature) -> bool:
        """Observe the live arrow tip at the feature's projected profile-view station.

        Semantic correspondence is already established above from provider and IR facts.  This
        page-space comparison is therefore rendered-evidence validation, never feature identity:
        retained registry metadata and correct text cannot certify a leader moved onto plain stock.
        """
        # Observe the drafting fact instead of re-spelling or importing the renderer's routing
        # table: a profile projection preserves displacement along the groove's shaft axis.
        # Prefer front when both principal profiles preserve it, matching the drawing convention.
        # A mutation of the renderer's mutable routing can therefore move only the production
        # ink; it cannot rewrite this evidence oracle along with the finished drawing.
        try:
            origin = tuple(float(value) for value in feature.frame.origin)
            displaced = list(origin)
            displaced["xyz".index(feature.axis)] += 1.0
            expected_view = next(
                view
                for view in ("front", "side")
                if any(
                    abs(float(projected) - float(start)) > 1e-9
                    for projected, start in zip(
                        drawing.at(view, *displaced)[:2],
                        drawing.at(view, *origin)[:2],
                        strict=True,
                    )
                )
            )
        except (KeyError, StopIteration, ValueError):
            expected_view = None
        if expected_view is None or drawing.registry.view_of(name) != expected_view:
            return False
        annotation = drawing.registry.named(name)
        try:
            tip = annotation.tip
            expected = drawing.at(expected_view, *feature.frame.origin)
            return len(tip) >= 2 and all(
                abs(float(tip[index]) - float(expected[index])) <= 1e-6 for index in range(2)
            )
        except Exception:  # noqa: BLE001 — malformed finished ink cannot earn credit
            return False

    result: list[Outcome] = []
    for exact, features, outcomes in correspondence:
        if not exact or len(features) != 1:
            result.append("unknown")
            continue
        feature = features[0]
        names = set.intersection(
            *(confirmed.get((feature, outcome.parameter_id), set()) for outcome in outcomes)
        )
        supported = all(outcome.state == "placed" for outcome in outcomes) and any(
            drawing.registry.feature_of(name) == feature
            and rendered_label(name) == expected_labels.get(feature)
            and leader_targets_groove(name, feature)
            for name in names
        )
        result.append("supported" if supported else "unsupported")
    return result


def _declared_groove_model(part, grooves):
    """Declare observed grooves through public ``Sheet.groove`` and return its IR."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for observed in grooves:
        sheet.groove(
            axis=observed.axis,
            width=observed.width,
            diameter=observed.diameter,
            at=observed.at,
        )
    return sheet.model()
