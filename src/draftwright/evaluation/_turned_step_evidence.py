"""Turned-step IR correspondence and finished-drawing evidence for STEP evaluation.

Engine imports stay inside functions so importing the benchmark remains inexpensive.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal, TypeAlias

Outcome: TypeAlias = Literal["supported", "unknown", "unsupported"]


def _turned_step_correspondence(
    sources,
    recognition,
    features,
    registry=None,
    omissions=(),
    *,
    allow_declared_profile_omission=False,
):
    from draftwright.linting.turned_step_coverage import (
        turned_step_ir_profile_identity,
        turned_step_key,
        turned_step_requirement_outcomes,
        turned_step_source_key,
        turned_step_source_profile_identity,
    )
    from draftwright.registry import AnnotationRegistry

    outcomes = turned_step_requirement_outcomes(
        recognition,
        features,
        registry if registry is not None else AnnotationRegistry(),
        omissions,
        allow_declared_profile_omission=allow_declared_profile_omission,
    )
    if len(outcomes) != 2 * len(sources):
        return [(False, (), ()) for _source in sources]
    try:
        source_inventory = Counter(
            turned_step_source_key(profile, step) for profile, step in sources
        )
        ir_inventory = Counter(
            turned_step_key(feature)
            for feature in features
            if getattr(feature, "kind", None) == "step"
        )
        inventory_exact = source_inventory == ir_inventory
    except (AttributeError, TypeError, ValueError):
        inventory_exact = False
    correspondence = []
    for index, source in enumerate(sources):
        profile, step = source
        requirement_outcomes = tuple(outcomes[index * 2 : index * 2 + 2])
        candidates = {
            feature
            for outcome in requirement_outcomes
            for feature in outcome.features
            if feature is not None
        }
        feature = next(iter(candidates)) if len(candidates) == 1 else None
        exact = bool(
            inventory_exact
            and feature is not None
            and turned_step_source_key(profile, step) == turned_step_key(feature)
            and (
                (feature_profile := turned_step_ir_profile_identity(feature))
                == turned_step_source_profile_identity(profile, step)
                or (allow_declared_profile_omission and feature_profile is None)
            )
            and {outcome.parameter_id for outcome in requirement_outcomes}
            == {"step.length", "step.diameter"}
        )
        correspondence.append(
            (exact, (feature,) if feature is not None else (), requirement_outcomes)
        )
    return correspondence


def _turned_step_model_outcomes(
    sources, recognition, features, *, allow_declared_profile_omission=False
) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _features, _outcomes in _turned_step_correspondence(
            sources,
            recognition,
            features,
            allow_declared_profile_omission=allow_declared_profile_omission,
        )
    ]


def _turned_step_drawing_outcomes(
    sources,
    drawing,
    *,
    recognition_override=None,
    allow_declared_profile_omission=False,
) -> list[Outcome]:
    """Verify both exact claims and their physical shoulder/silhouette targets."""
    from draftwright._core import _tol_suffix
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.linting.turned_step_coverage import turned_step_key
    from draftwright.model.compiled import compile_dimensions, resolve_feature

    recognition = (
        recognition_override if recognition_override is not None else drawing.recognition()
    )
    model = drawing.model()
    plan = compile_dimensions(model)
    step_length_text = {
        resolve_feature(group.ref): length.value_text
        for group in plan.of_kind("step")
        for length in (group.dim(kind="length"),)
        if length is not None
    }
    exact_labels = {
        (resolve_feature(group.ref), dimension.parameter_id): (
            ("ø" if dimension.kind == "diameter" else "")
            + dimension.value_text
            + _tol_suffix(dimension.tolerance, drawing.draft)
        )
        for kind in ("step", "rotational")
        for group in plan.of_kind(kind)
        for dimension in group.dims
        if dimension.parameter_id in {"step.length", "step.diameter", "od.diameter"}
    }
    # Exercise the generated Drawing's own lint path too. Per-family measurement outcomes below
    # decide turned-step credit; unrelated family diagnostics do not erase valid band evidence.
    drawing.lint()
    correspondence = _turned_step_correspondence(
        sources,
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
        allow_declared_profile_omission=allow_declared_profile_omission,
    )
    confirmed: dict[tuple[object, str], set[str]] = {}
    for claim in verify_measurement_claims(drawing.registry, plan):
        measurement = claim.measurement
        if claim.state != "confirmed" or measurement is None:
            continue
        feature = getattr(measurement, "feature", None)
        parameter = str(getattr(measurement, "parameter", ""))
        if (
            getattr(feature, "kind", None) == "step"
            and parameter in {"step.length", "step.diameter"}
        ) or (getattr(feature, "kind", None) == "rotational" and parameter == "od.diameter"):
            confirmed.setdefault((feature, parameter), set()).add(claim.annotation)

    def xy(value) -> tuple[float, float]:
        try:
            return float(value[0]), float(value[1])
        except TypeError:
            return float(value.X), float(value.Y)

    def close(first, second, tolerance=1e-6) -> bool:
        return all(abs(a - b) <= tolerance for a, b in zip(xy(first), xy(second), strict=True))

    def length_targets_span(name: str, feature) -> bool:
        view = drawing.registry.view_of(name)
        if view is None:
            return False
        annotation = drawing.registry.named(name)
        label = getattr(annotation, "label", None) or getattr(annotation, "_annotate_label", None)
        measurements = tuple(drawing.registry.measurement_of(name))
        repeated_features = tuple(
            measurement.feature
            for measurement in measurements
            if getattr(measurement.feature, "kind", None) == "step"
            and measurement.parameter == "step.length"
        )
        spec = drawing.registry.dimension_spec_of(name)
        if spec is None:
            return False
        try:
            expected = [drawing.at(view, *point) for point in feature.span]
            actual = [spec.p1, spec.p2]
            origin = tuple(float(value) for value in feature.frame.origin)
            displaced = list(origin)
            displaced["xyz".index(feature.frame.axis)] += 1.0
            projected_origin = drawing.at(view, *origin)
            projected_axis = drawing.at(view, *displaced)
        except Exception:  # noqa: BLE001 — malformed finished ink earns no credit
            return False
        deltas = [
            abs(float(projected_axis[index]) - float(projected_origin[index]))
            for index in range(2)
        ]
        measured_index = 0 if deltas[0] >= deltas[1] else 1
        cross_index = 1 - measured_index
        actual_xy = [xy(point) for point in actual]
        expected_xy = [xy(point) for point in expected]
        actual_stations = sorted(point[measured_index] for point in actual_xy)
        expected_stations = sorted(point[measured_index] for point in expected_xy)
        exact_span = bool(
            deltas[measured_index] > 1e-9
            and abs(actual_stations[0] - expected_stations[0]) <= 1e-6
            and abs(actual_stations[1] - expected_stations[1]) <= 1e-6
            and abs(actual_xy[0][cross_index] - actual_xy[1][cross_index]) <= 1e-6
        )
        if (
            exact_span
            and repeated_features == (feature,)
            and len(measurements) == 1
            and label == exact_labels.get((feature, "step.length"))
        ):
            return True

        # Equal adjacent lengths deliberately collapse to one provenance-rich ``n× value``
        # chain. Credit each claimed band only when the finished dimension spans exactly the
        # complete claimed run; merely enclosing this feature is not sufficient.
        expected_texts = {
            step_length_text[candidate]
            for candidate in repeated_features
            if candidate in step_length_text
        }
        expected_label = (
            f"{len(repeated_features)}× {next(iter(expected_texts))}"
            if len(expected_texts) == 1
            and len(repeated_features)
            == len([candidate for candidate in repeated_features if candidate in step_length_text])
            else None
        )
        if (
            not isinstance(label, str)
            or label != expected_label
            or len(repeated_features) < 2
            or feature not in repeated_features
            or len(repeated_features) != len(measurements)
            or len(set(repeated_features)) != len(repeated_features)
            or any(
                abs(float(candidate.length) - float(feature.length)) > 1e-6
                for candidate in repeated_features
            )
        ):
            return False
        try:
            keys = [turned_step_key(candidate) for candidate in repeated_features]
            if any(key[:2] != keys[0][:2] for key in keys):
                return False
            intervals = sorted((key[2][0], key[2][1]) for key in keys)
            if any(
                abs(left[1] - right[0]) > 1e-6
                for left, right in zip(intervals, intervals[1:], strict=False)
            ):
                return False
            run_stations = sorted(
                xy(drawing.at(view, *point))[measured_index]
                for candidate in repeated_features
                for point in candidate.span
            )
        except Exception:  # noqa: BLE001 — malformed claimed geometry earns no credit
            return False
        return bool(
            deltas[measured_index] > 1e-9
            and abs(actual_stations[0] - run_stations[0]) <= 1e-6
            and abs(actual_stations[1] - run_stations[-1]) <= 1e-6
            and abs(actual_xy[0][cross_index] - actual_xy[1][cross_index]) <= 1e-6
        )

    def diameter_targets_surface(name: str, feature, representation, parameter: str) -> bool:
        view = drawing.registry.view_of(name)
        if view is None:
            return False
        annotation = drawing.registry.named(name)
        label = getattr(annotation, "label", None) or getattr(annotation, "_annotate_label", None)
        if not isinstance(label, str) or label != exact_labels.get((representation, parameter)):
            return False
        try:
            origin = tuple(float(value) for value in feature.frame.origin)
            centre = drawing.at(view, *origin)
            radius = float(feature.diameter) / 2.0
            scales: list[float] = []
            for radial_index in range(3):
                if radial_index == "xyz".index(feature.frame.axis):
                    continue
                unit = list(origin)
                unit[radial_index] += 1.0
                projected = drawing.at(view, *unit)
                scale = (
                    sum((a - b) ** 2 for a, b in zip(xy(projected), xy(centre), strict=True))
                    ** 0.5
                )
                if scale > 1e-9:
                    scales.append(scale)
            if not scales or max(scales) - min(scales) > 1e-6:
                return False
            tip = getattr(annotation, "tip", None)
            if tip is not None:
                radial_distance: float = float(
                    sum((a - b) ** 2 for a, b in zip(xy(tip), xy(centre), strict=True)) ** 0.5
                )
                return abs(radial_distance / scales[0] - radius) <= 1e-6

            # The largest band OD is represented by the whole rotational profile's linear
            # diameter. Its witness pair must span the exact OD, transverse to and centred on
            # the projected turning axis.
            spec = drawing.registry.dimension_spec_of(name)
            if spec is None:
                return False
            first, second = xy(spec.p1), xy(spec.p2)
            span_vector = (second[0] - first[0], second[1] - first[1])
            span_length = sum(component**2 for component in span_vector) ** 0.5
            if abs(span_length / scales[0] - 2.0 * radius) > 1e-6:
                return False
            axis_point = list(origin)
            axis_point["xyz".index(feature.frame.axis)] += 1.0
            projected_axis = xy(drawing.at(view, *axis_point))
            centre_xy = xy(centre)
            axis_vector = (
                projected_axis[0] - centre_xy[0],
                projected_axis[1] - centre_xy[1],
            )
            axis_length = sum(component**2 for component in axis_vector) ** 0.5
            midpoint = (
                (first[0] + second[0]) / 2.0,
                (first[1] + second[1]) / 2.0,
            )
            if axis_length <= 1e-9:
                return close(midpoint, centre_xy)
            transverse_dot = sum(span_vector[index] * axis_vector[index] for index in range(2))
            midpoint_cross = (midpoint[0] - centre_xy[0]) * axis_vector[1] - (
                midpoint[1] - centre_xy[1]
            ) * axis_vector[0]
            return bool(abs(transverse_dot) <= 1e-6 and abs(midpoint_cross) / axis_length <= 1e-6)
        except Exception:  # noqa: BLE001 — malformed finished ink earns no credit
            return False

    result: list[Outcome] = []
    for exact, features, outcomes in correspondence:
        if not exact or len(features) != 1:
            result.append("unknown")
            continue
        feature = features[0]
        by_parameter = {outcome.parameter_id: outcome for outcome in outcomes}
        states = {parameter: outcome.state for parameter, outcome in by_parameter.items()}
        length = by_parameter["step.length"]
        diameter = by_parameter["step.diameter"]
        length_names = confirmed.get(
            (length.representation_feature, length.representation_parameter), set()
        )
        diameter_names = confirmed.get(
            (diameter.representation_feature, diameter.representation_parameter), set()
        )
        supported = bool(
            states == {"step.length": "placed", "step.diameter": "placed"}
            and any(length_targets_span(name, feature) for name in length_names)
            and any(
                diameter_targets_surface(
                    name,
                    feature,
                    diameter.representation_feature,
                    diameter.representation_parameter,
                )
                for name in diameter_names
            )
        )
        result.append("supported" if supported else "unsupported")
    return result
