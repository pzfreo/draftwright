"""Rectangular-pad and plate evidence for STEP evaluation.

Physical correspondence, public declaration and finished drawing checks share this
owner. Engine imports stay inside functions to preserve evaluation's lazy-load seam.
"""

from __future__ import annotations

from collections import Counter
from math import isclose
from typing import Literal, TypeAlias

Scalar: TypeAlias = int | float | str | bool
Value: TypeAlias = Scalar | tuple[float, ...]
Outcome: TypeAlias = Literal["supported", "unknown", "unsupported"]


_PAD_PLANE_AXES = {"x": ("y", "z"), "y": ("z", "x"), "z": ("x", "y")}


def _pad_pair(values) -> tuple[float, float]:
    lo, hi = values
    return round(float(lo), 3), round(float(hi), 3)


def _pad_bounds(pad) -> dict[str, tuple[float, float]]:
    if hasattr(pad, "bounds"):
        return {axis: _pad_pair(pad.bounds(axis)) for axis in "xyz"}
    return {
        axis: (
            round(float(getattr(pad, f"{axis}0")), 3),
            round(float(getattr(pad, f"{axis}1")), 3),
        )
        for axis in "xyz"
    }


def _pad_axis(pad) -> str:
    axis = getattr(pad, "axis", None)
    if axis is None:
        axis = pad.frame.axis
    return str(axis)


def _pad_identity(pad) -> tuple[str, int, tuple[float, float, float]]:
    from draftwright.linting.pad_coverage import pad_attachment_point

    return _pad_axis(pad), int(pad.direction), pad_attachment_point(pad)


def _pad_parameters(pad) -> dict[str, Value]:
    axis = _pad_axis(pad)
    long_axis, width_axis = _PAD_PLANE_AXES[axis]
    bounds = _pad_bounds(pad)
    return {
        "width": round(bounds[width_axis][1] - bounds[width_axis][0], 3),
        "length": round(bounds[long_axis][1] - bounds[long_axis][0], 3),
        "height": round(bounds[axis][1] - bounds[axis][0], 3),
    }


def _pad_expected_parameters(pad) -> set[str]:
    axis = _pad_axis(pad)
    long_axis, width_axis = _PAD_PLANE_AXES[axis]
    locations = (
        {"location_pad.location.x", "location_pad.location.y"}
        if axis == "z"
        else {f"location_pad.{long_axis}", f"location_pad.{width_axis}"}
    )
    return {"pad_width.length", "pad_length.length", "pad_height.length", *locations}


def _pad_correspondence(pads, recognition, features, registry=None, omissions=()):
    """Per physical pad, retain exact IR and production-ledger evidence."""
    from draftwright.linting.pad_coverage import (
        pad_attachment_point,
        pad_key,
        pad_requirement_outcomes,
    )
    from draftwright.registry import AnnotationRegistry

    ledger = pad_requirement_outcomes(
        recognition,
        features,
        AnnotationRegistry() if registry is None else registry,
        omissions,
    )
    by_at: dict[tuple[float, float, float], list] = {}
    for outcome in ledger:
        if outcome.source_at is not None:
            by_at.setdefault(outcome.source_at, []).append(outcome)
    result = []
    for source in pads:
        candidates = [
            outcome
            for outcome in by_at.get(pad_attachment_point(source), ())
            if outcome.features and pad_key(outcome.features[0]) == pad_key(source)
        ]
        candidate_features = tuple(
            dict.fromkeys(feature for outcome in candidates for feature in outcome.features)
        )
        exact = (
            len(candidate_features) == 1
            and len(candidates) == 5
            and {outcome.parameter_id for outcome in candidates}
            == _pad_expected_parameters(source)
            and all(outcome.features == candidate_features for outcome in candidates)
        )
        result.append((exact, candidate_features, tuple(candidates)))
    return result


def _pad_model_outcomes(pads, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _features, _outcomes in _pad_correspondence(pads, recognition, features)
    ]


def _pad_drawing_outcomes(pads, drawing) -> list[Outcome]:
    """Verify all five pad requirements through exact semantic drawing evidence."""
    from draftwright._core import _decode_hole_location_fact
    from draftwright.linting.evidence import (
        compiled_values,
        rendered_numbers,
        verify_measurement_claims,
    )
    from draftwright.linting.pad_coverage import pad_center
    from draftwright.model.compiled import DimensionId, compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    correspondence = _pad_correspondence(
        pads,
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
    )
    claims = verify_measurement_claims(drawing.registry, plan)
    approved_by_id = compiled_values(plan)
    confirmed = {
        (claim.annotation, claim.measurement)
        for claim in claims
        if claim.state == "confirmed" and claim.measurement is not None
    }
    location_names: dict[tuple[object, str, tuple[float, float, float]], set[str]] = {}
    for name in drawing.registry.names():
        annotation = drawing.registry.named(name)
        for fact in getattr(annotation, "covers_hole_locations", ()):
            decoded = _decode_hole_location_fact(fact)
            if decoded is None:
                continue
            feature, parameter, point = decoded
            if getattr(feature, "kind", None) != "pad":
                continue
            point_x, point_y, point_z = point
            rounded_point = (
                round(float(point_x), 3),
                round(float(point_y), 3),
                round(float(point_z), 3),
            )
            location_names.setdefault((feature, parameter, rounded_point), set()).add(name)
    accepted_states = {"placed", "satisfied_by_structured_note", "inapplicable"}
    result: list[Outcome] = []
    for exact, features, outcomes in correspondence:
        if not exact or len(features) != 1:
            result.append("unknown")
            continue
        feature = features[0]
        states_ok = all(outcome.state in accepted_states for outcome in outcomes)
        ink_ok = True
        for outcome in outcomes:
            if outcome.state != "placed":
                continue
            parameter = outcome.parameter_id
            evidence_parameter = (
                "location_pad.location"
                if parameter.startswith("location_pad.location.")
                else parameter
            )
            matching_claims = {
                name
                for name, claim in confirmed
                if getattr(claim, "feature", None) == feature
                and str(getattr(claim, "parameter", "")) == evidence_parameter
            }
            if parameter.startswith("location_pad.location."):
                measured_axis = parameter.rsplit(".", 1)[-1]
                directional_approvals = tuple(
                    approved
                    for approved in approved_by_id.get(
                        DimensionId(feature, "location_pad.location"), ()
                    )
                    if approved.discriminator == measured_axis
                )
                expected_text = (
                    directional_approvals[0].value_text if len(directional_approvals) == 1 else ""
                )
                try:
                    expected_value = float(expected_text)
                except (TypeError, ValueError):
                    matching_claims = set()
                else:
                    matching_claims = {
                        name
                        for name in matching_claims
                        if (numbers := rendered_numbers(drawing.registry.named(name))) is not None
                        and any(
                            isclose(number, expected_value, rel_tol=0.0, abs_tol=1e-6)
                            for number in numbers
                        )
                    }
                matching_claims &= location_names.get(
                    (feature, parameter, pad_center(feature)), set()
                )
            if not matching_claims:
                ink_ok = False
                break
        result.append("supported" if states_ok and ink_ok else "unsupported")
    return result


def _declared_pad_model(part, pads):
    """Declare observed pads through public ``Sheet.pad`` and return its IR."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for observed in pads:
        bounds = _pad_bounds(observed)
        sheet.pad(
            x0=bounds["x"][0],
            x1=bounds["x"][1],
            y0=bounds["y"][0],
            y1=bounds["y"][1],
            z0=bounds["z"][0],
            z1=bounds["z"][1],
            axis=_pad_axis(observed),
            direction=observed.direction,
        )
    return sheet.model()


def _plate_identity(plate) -> tuple[str, float, float, float]:
    from draftwright.linting.plate_coverage import plate_center

    centre = plate_center(plate)
    axis = str(plate.axis)
    index = "xyz".index(axis)
    other = [candidate for candidate in range(3) if candidate != index]
    return axis, centre[index], centre[other[0]], centre[other[1]]


def _plate_parameters(plate) -> dict[str, Value]:
    return {"thickness": round(float(plate.hi) - float(plate.lo), 3)}


def _plate_correspondence(
    plates, recognition, features, registry=None, omissions=(), *, part=None
):
    """Per body-local slab, retain exact IR and production-ledger evidence."""
    from draftwright.linting.plate_coverage import (
        plate_center,
        plate_key,
        plate_requirement_outcomes,
    )
    from draftwright.registry import AnnotationRegistry

    ledger = plate_requirement_outcomes(
        recognition,
        features,
        AnnotationRegistry() if registry is None else registry,
        omissions,
        part=part,
    )
    by_at: dict[tuple[float, float, float], list] = {}
    for outcome in ledger:
        if outcome.source_at is not None:
            by_at.setdefault(outcome.source_at, []).append(outcome)
    result = []
    for source in plates:
        candidates = [
            outcome
            for outcome in by_at.get(plate_center(source), ())
            if outcome.features and plate_key(outcome.features[0]) == plate_key(source)
        ]
        candidate_features = tuple(
            dict.fromkeys(feature for outcome in candidates for feature in outcome.features)
        )
        exact = (
            len(candidate_features) == 1
            and len(candidates) == 1
            and candidates[0].parameter_id == "thickness.length"
            and candidates[0].features == candidate_features
        )
        result.append((exact, candidate_features, tuple(candidates)))
    return result


def _plate_model_outcomes(plates, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _features, _outcomes in _plate_correspondence(plates, recognition, features)
    ]


def _plate_drawing_outcomes(plates, drawing) -> list[Outcome]:
    """Verify each slab thickness through exact compiler identity and finished ink."""
    from build123d_drafting import Dimension

    from draftwright.linting._registry import satisfaction_ids
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    correspondence = _plate_correspondence(
        plates,
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
        part=drawing.working_part,
    )
    confirmed: dict[tuple[object, str], set[str]] = {}
    confirmed_counts: Counter[tuple[object, str]] = Counter()
    for claim in verify_measurement_claims(drawing.registry, plan):
        measurement = claim.measurement
        if claim.state != "confirmed" or measurement is None:
            continue
        key = (
            getattr(measurement, "feature", None),
            str(getattr(measurement, "parameter", "")),
        )
        confirmed.setdefault(key, set()).add(claim.annotation)
        confirmed_counts[key] += 1
    satisfied = {
        (identity.feature, identity.parameter)
        for identity in satisfaction_ids(drawing.registry)
        if identity.feature is not None and isinstance(identity.parameter, str)
    }

    result: list[Outcome] = []
    for exact, features, outcomes in correspondence:
        if not exact or len(features) != 1:
            result.append("unknown")
            continue
        feature = features[0]
        names = confirmed.get((feature, "thickness.length"), set())
        states_ok = all(
            outcome.state == "placed"
            or (
                outcome.state == "inapplicable"
                and bool(outcome.dependencies)
                and all(
                    dependency in satisfied or confirmed_counts[dependency] >= count
                    for dependency, count in Counter(outcome.dependencies).items()
                )
            )
            for outcome in outcomes
        )
        ink_ok = all(
            outcome.state != "placed"
            or any(
                drawing.registry.feature_of(name) == feature
                and isinstance(drawing.registry.named(name), Dimension)
                for name in names
            )
            for outcome in outcomes
        )
        result.append("supported" if states_ok and ink_ok else "unsupported")
    return result


def _declared_plate_model(part, plates):
    """Declare observed slabs through public ``Sheet.plate`` and return its IR."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for observed in plates:
        sheet.plate(
            axis=observed.axis,
            lo=observed.lo,
            hi=observed.hi,
            u=observed.u,
            v=observed.v,
        )
    return sheet.model()
