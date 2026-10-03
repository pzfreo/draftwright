"""Lone-pocket and pocket-pattern evidence for STEP evaluation.

Physical occurrence correspondence, public declaration and finished drawing evidence share
one owner. Engine imports stay inside functions to preserve the evaluation lazy-load seam.
"""

from __future__ import annotations

from typing import Literal, TypeAlias, cast

Scalar: TypeAlias = int | float | str | bool
Value: TypeAlias = Scalar | tuple[float, ...]
Outcome: TypeAlias = Literal["supported", "unknown", "unsupported"]


def _pocket_geometry(pocket) -> dict:
    from quiddity import SectionRecess

    from draftwright.section_recess_contract import section_recess_fields

    if type(pocket) is SectionRecess:
        kind, geometry = section_recess_fields(pocket)
        if kind != "pocket":
            raise ValueError("pocket observation requires the supported pocket grammar")
        return geometry
    return {
        **{
            key: getattr(pocket, key)
            for key in (
                "width_axis",
                "long_axis",
                "width",
                "length",
                "depth",
                "w_center",
                "lo",
                "hi",
                "edge_anchored",
                "open_sign",
            )
        },
        "origin": pocket.frame.origin,
        "axis": pocket.depth_axis,
    }


def _pocket_point(pocket) -> tuple[float, float, float]:
    x, y, z = (round(float(c), 3) for c in _pocket_geometry(pocket)["origin"])
    return x, y, z


def _pocket_depth_axis(pocket) -> str:
    axis: str = _pocket_geometry(pocket)["axis"]
    return axis


def _pocket_identity(pocket) -> tuple:
    data = _pocket_geometry(pocket)
    return (
        data["width_axis"],
        data["long_axis"],
        data["axis"],
        data["open_sign"],
        _pocket_point(pocket),
    )


def _pocket_parameters(pocket) -> dict[str, Value]:
    data = _pocket_geometry(pocket)
    return {
        **{key: round(float(data[key]), 3) for key in ("width", "length", "depth")},
        "edge_anchored": data["edge_anchored"],
    }


def _pocket_parameter_ids(pocket) -> tuple[str, ...]:
    data = _pocket_geometry(pocket)
    ids = ("pocket_width.length", "pocket_length.length", "pocket_depth.length")
    locations: tuple[str, ...]
    if data["axis"] == "z":
        locations = ("location_pocket.location.x", "location_pocket.location.y")
    else:
        locations = tuple(
            f"location_pocket.{axis}" for axis in (data["long_axis"], data["width_axis"])
        )
    return (*ids, *locations)


def _supported_pocket_patterns(recognition) -> tuple:
    from draftwright.section_recess_contract import (
        recesses_with_kind,
        section_recess_pattern_members,
    )

    pocket_ids = {
        id(record) for record in recesses_with_kind(recognition.section_recesses, "pocket")
    }
    return tuple(
        pattern
        for pattern in recognition.section_recess_patterns
        if all(
            id(member) in pocket_ids
            for member in section_recess_pattern_members(pattern, recognition.section_recesses)
        )
    )


def _lone_pockets(recognition) -> tuple:
    from draftwright.section_recess_contract import (
        recesses_with_kind,
        section_recess_pattern_members,
    )

    pattern_members = {
        id(member)
        for pattern in _supported_pocket_patterns(recognition)
        for member in section_recess_pattern_members(pattern, recognition.section_recesses)
    }
    return tuple(
        record
        for record in recesses_with_kind(recognition.section_recesses, "pocket")
        if id(record) not in pattern_members
    )


def _pocket_declaration_arguments(source) -> dict:
    data = _pocket_geometry(source).copy()
    data["at"] = data.pop("origin")
    data["depth_axis"] = data.pop("axis")
    return data


def _pocket_pattern_angle(pattern, source) -> float:
    from math import atan2, degrees

    plane = tuple(axis for axis in "xyz" if axis != _pocket_depth_axis(source))
    return degrees(
        atan2(
            pattern.col_direction["xyz".index(plane[1])],
            pattern.col_direction["xyz".index(plane[0])],
        )
    )


def _pocket_correspondence(pockets, recognition, features, registry=None, omissions=()):
    """Per physical pocket, retain exact IR and production-ledger evidence."""
    from draftwright.linting.pocket_coverage import pocket_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    ledger = pocket_requirement_outcomes(
        recognition,
        features,
        AnnotationRegistry() if registry is None else registry,
        omissions,
    )
    by_at: dict[tuple[float, float, float], list] = {}
    for outcome in ledger:
        by_at.setdefault(outcome.source_at, []).append(outcome)
    result = []
    for source in pockets:
        at = _pocket_point(source)
        expected_ids = set(_pocket_parameter_ids(source))
        candidates = [
            outcome
            for outcome in by_at.get(at, ())
            if outcome.features
            and _pocket_identity(outcome.features[0]) == _pocket_identity(source)
            and _pocket_parameters(outcome.features[0]) == _pocket_parameters(source)
        ]
        candidate_features = tuple(
            dict.fromkeys(feature for outcome in candidates for feature in outcome.features)
        )
        exact = (
            len(candidate_features) == 1
            and len(candidates) == len(expected_ids)
            and {outcome.parameter_id for outcome in candidates} == expected_ids
        )
        result.append((exact, candidate_features, tuple(candidates)))
    return result


def _pocket_model_outcomes(pockets, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _features, _outcomes in _pocket_correspondence(pockets, recognition, features)
    ]


def _pocket_drawing_outcomes(pockets, drawing) -> list[Outcome]:
    """Verify semantic size ink and directional location evidence per physical pocket."""
    from draftwright._core import _decode_hole_location_fact, _tol_suffix
    from draftwright._geometry import _fmt
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    correspondence = _pocket_correspondence(
        pockets,
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
    )
    confirmed = {
        (claim.annotation, claim.measurement)
        for claim in verify_measurement_claims(drawing.registry, plan)
        if claim.state == "confirmed" and claim.measurement is not None
    }
    pocket_labels: dict[object, str] = {}
    for group in plan.of_kind("pocket"):
        by_parameter = {
            str(approved.id.parameter): approved
            for approved in group.dims
            if approved.id is not None
        }
        width = by_parameter.get("pocket_width.length")
        length = by_parameter.get("pocket_length.length")
        depth = by_parameter.get("pocket_depth.length")
        if width is None or length is None or depth is None or width.id is None:
            continue
        pocket_feature = width.id.feature
        fields = tuple(
            approved.value_text + _tol_suffix(approved.tolerance, drawing.draft)
            for approved in (width, length, depth)
        )
        pocket_labels[pocket_feature] = f"{fields[0]} × {fields[1]} × {fields[2]} DEEP"

    location_labels: dict[tuple[object, str], set[str]] = {}
    for approved in plan.locations:
        measurement = approved.id
        location_feature = getattr(measurement, "feature", None)
        parameter = str(getattr(measurement, "parameter", ""))
        if getattr(location_feature, "kind", None) != "pocket":
            continue
        if parameter == "location_pocket.location" and approved.span is not None:
            start, end = approved.span
            for axis in ("x", "y"):
                index = "xyz".index(axis)
                label = _fmt(abs(float(end[index]) - float(start[index])))
                location_labels.setdefault(
                    (location_feature, f"location_pocket.location.{axis}"), set()
                ).add(label)
            continue
        location_labels.setdefault((location_feature, parameter), set()).add(
            approved.value_text + _tol_suffix(approved.tolerance, drawing.draft)
        )

    def rendered_label(name) -> str | None:
        label = getattr(drawing.registry.named(name), "label", None)
        return label if isinstance(label, str) else None

    location_names: dict[tuple[object, str, tuple[float, float, float]], set[str]] = {}
    for name in drawing.registry.names():
        annotation = drawing.registry.named(name)
        for fact in getattr(annotation, "covers_hole_locations", ()):
            decoded = _decode_hole_location_fact(fact)
            if decoded is None:
                continue
            fact_feature, parameter, point = decoded
            if getattr(fact_feature, "kind", None) != "pocket":
                continue
            point_x, point_y, point_z = point
            rounded_point = (
                round(float(point_x), 3),
                round(float(point_y), 3),
                round(float(point_z), 3),
            )
            key = (fact_feature, parameter, rounded_point)
            location_names.setdefault(key, set()).add(name)
    placed = {"placed", "satisfied_by_structured_note", "inapplicable"}
    result: list[Outcome] = []
    for exact, features, outcomes in correspondence:
        if not exact or len(features) != 1:
            result.append("unknown")
            continue
        feature = features[0]
        states_ok = all(outcome.state in placed for outcome in outcomes)
        ink_ok = True
        for outcome in outcomes:
            if outcome.state != "placed":
                continue
            parameter = outcome.parameter_id
            evidence_parameter = (
                "location_pocket.location"
                if parameter.startswith("location_pocket.location.")
                else parameter
            )
            matching_claims = {
                name
                for name, claim in confirmed
                if getattr(claim, "feature", None) == feature
                and str(getattr(claim, "parameter", "")) == evidence_parameter
            }
            if parameter in {
                "pocket_width.length",
                "pocket_length.length",
                "pocket_depth.length",
            }:
                expected_label = pocket_labels.get(feature)
                matching_claims = {
                    name for name in matching_claims if rendered_label(name) == expected_label
                }
            # A feature-level Z-normal location id has two rendered members.  Join each
            # directional physical fact to the exact annotation that bears its value so
            # one correct axis cannot confirm corrupted ink on the other (#1372 review).
            if parameter.startswith("location_pocket.location."):
                matching_claims &= location_names.get(
                    (feature, parameter, outcome.source_at), set()
                )
            if parameter.startswith("location_pocket."):
                expected_labels = location_labels.get((feature, parameter), set())
                matching_claims = {
                    name for name in matching_claims if rendered_label(name) in expected_labels
                }
            if not matching_claims:
                ink_ok = False
                break
        result.append("supported" if states_ok and ink_ok else "unsupported")
    return result


def _declared_pocket_model(part, pockets):
    """Declare observed lone pockets through public ``Sheet.pocket`` and return its IR."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for observed in pockets:
        sheet.pocket(**_pocket_declaration_arguments(observed))
    return sheet.model()


def _pocket_pattern_correspondence(patterns, recognition, features, registry=None, omissions=()):
    """Per recognised arrangement, retain its exact IR owner and requirement ledger."""
    from draftwright.linting.pocket_pattern_coverage import (
        pocket_pattern_members,
        pocket_pattern_requirement_outcomes,
    )
    from draftwright.registry import AnnotationRegistry

    ledger = pocket_pattern_requirement_outcomes(
        recognition,
        features,
        AnnotationRegistry() if registry is None else registry,
        omissions,
    )
    by_members: dict[tuple, list] = {}
    for outcome in ledger:
        by_members.setdefault(outcome.members, []).append(outcome)
    result = []
    for pattern in patterns:
        members = pocket_pattern_members(pattern, inventory=recognition.section_recesses)
        candidates = by_members.get(members, ())
        expected = {
            "grouping.count",
            "pocket_width.length",
            "pocket_length.length",
            "pocket_depth.length",
            "location_pocket_pattern.location.x",
            "location_pocket_pattern.location.y",
        }
        if hasattr(pattern, "row_pitch"):
            expected.update(("grid_pitch.length.row", "grid_pitch.length.col"))
        else:
            expected.add("pitch.length")
        candidate_features = tuple(
            dict.fromkeys(feature for outcome in candidates for feature in outcome.features)
        )
        exact = (
            len(candidate_features) == 1
            and {outcome.parameter_id for outcome in candidates} == expected
            and all(outcome.features == candidate_features for outcome in candidates)
        )
        result.append((exact, candidate_features, tuple(candidates)))
    return result


def _pocket_pattern_model_outcomes(patterns, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _features, _outcomes in _pocket_pattern_correspondence(
            patterns, recognition, features
        )
    ]


def _pocket_pattern_pitch_gaps(feature, parameter: str, nominal: float) -> tuple[float, ...]:
    """Physical adjacent gaps represented by one collapsed pitch measurement."""
    import math

    from draftwright._geometry import plane_axes

    members = tuple(feature.members)
    if parameter == "pitch.length":
        direction = tuple(float(value) for value in feature.direction)
        norm = math.hypot(*direction)
        if norm <= 1e-12:
            return ()
        direction = tuple(value / norm for value in direction)
        ordered = sorted(members, key=lambda point: sum(point[i] * direction[i] for i in range(3)))
        return tuple(
            math.dist(first, second) for first, second in zip(ordered, ordered[1:], strict=False)
        )

    u, v = plane_axes(feature.member.depth_axis)
    angle = math.radians(float(feature.angle or 0.0))
    col_direction = tuple(
        math.cos(angle) * u[index] + math.sin(angle) * v[index] for index in range(3)
    )
    row_direction = tuple(
        -math.sin(angle) * u[index] + math.cos(angle) * v[index] for index in range(3)
    )
    direction = row_direction if parameter.endswith(".row") else col_direction
    expected = (
        int(feature.cols) * (int(feature.rows) - 1)
        if parameter.endswith(".row")
        else int(feature.rows) * (int(feature.cols) - 1)
    )
    gaps = []
    for left, first in enumerate(members):
        for second in members[left + 1 :]:
            delta = tuple(float(second[index]) - float(first[index]) for index in range(3))
            along = abs(sum(delta[index] * direction[index] for index in range(3)))
            across = math.sqrt(
                sum(
                    (
                        delta[index]
                        - sum(delta[j] * direction[j] for j in range(3)) * direction[index]
                    )
                    ** 2
                    for index in range(3)
                )
            )
            if across <= 0.02 and 1e-9 < along <= nominal * 1.5:
                gaps.append(math.dist(first, second))
    return tuple(gaps) if len(gaps) == expected else ()


def _pocket_pattern_drawing_outcomes(patterns, drawing) -> list[Outcome]:
    """Verify exact compiler-approved arrangement ink and structured ownership."""
    from draftwright._core import _decode_hole_location_fact, _tol_suffix
    from draftwright._geometry import _fmt
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.model.compiled import compile_dimensions
    from draftwright.model.ir import PocketPatternFeature

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    correspondence = _pocket_pattern_correspondence(
        patterns,
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
    )
    confirmed = {
        (claim.annotation, claim.measurement)
        for claim in verify_measurement_claims(drawing.registry, plan)
        if claim.state == "confirmed" and claim.measurement is not None
    }

    size_labels: dict[object, str] = {}
    pitch_labels: dict[tuple[object, str], str] = {}

    for group in plan.of_kind("pocket_pattern"):
        by_parameter = {
            str(approved.id.parameter): approved
            for approved in group.dims
            if approved.id is not None
        }
        feature = cast(
            PocketPatternFeature,
            next(approved.id.feature for approved in group.dims if approved.id is not None),
        )
        width = by_parameter.get("pocket_width.length")
        length = by_parameter.get("pocket_length.length")
        depth = by_parameter.get("pocket_depth.length")
        if width is not None and length is not None and depth is not None:
            fields = tuple(
                approved.value_text + _tol_suffix(approved.tolerance, drawing.draft)
                for approved in (width, length, depth)
            )
            size_labels[feature] = f"{feature.count}× {fields[0]} × {fields[1]} × {fields[2]} DEEP"
        for parameter in (
            "pitch.length",
            "grid_pitch.length.row",
            "grid_pitch.length.col",
        ):
            approved = by_parameter.get(parameter)
            if approved is None:
                continue
            if parameter == "pitch.length":
                intervals = feature.count - 1
            elif parameter.endswith(".row"):
                intervals = cast(int, feature.rows) - 1
            else:
                intervals = cast(int, feature.cols) - 1
            suffix = ""
            if approved.tolerance is not None:
                gaps = _pocket_pattern_pitch_gaps(feature, parameter, float(approved.value))
                nominal = round(float(approved.value), drawing.draft.decimal_precision)
                if gaps and all(
                    round(gap, drawing.draft.decimal_precision) == nominal for gap in gaps
                ):
                    suffix = _tol_suffix(approved.tolerance, drawing.draft)
            prefix = f"{intervals}× " if intervals > 1 else ""
            pitch_labels[(feature, parameter)] = f"{prefix}{approved.value_text}{suffix}"

    location_labels: dict[tuple[object, str], str] = {}
    for approved in plan.locations:
        location_feature = getattr(approved.id, "feature", None)
        if getattr(location_feature, "kind", None) != "pocket_pattern":
            continue
        axis = approved.discriminator
        assert axis in {"x", "y"} and approved.span is not None
        index = "xyz".index(axis)
        value = abs(float(approved.span[1][index]) - float(approved.span[0][index]))
        location_labels[(location_feature, f"location_pocket_pattern.location.{axis}")] = _fmt(
            value
        )

    def rendered_label(name) -> str | None:
        annotation = drawing.registry.named(name)
        label = getattr(annotation, "label", None) or getattr(annotation, "_annotate_label", None)
        return label if isinstance(label, str) else None

    location_names: dict[tuple[object, str, tuple[float, float, float]], set[str]] = {}
    for name, annotation in drawing.registry.iter_named():
        for fact in getattr(annotation, "covers_hole_locations", ()):
            decoded = _decode_hole_location_fact(fact)
            if decoded is None:
                continue
            feature, parameter, point = decoded
            if getattr(feature, "kind", None) != "pocket_pattern":
                continue
            point_x, point_y, point_z = point
            rounded = (
                round(float(point_x), 2),
                round(float(point_y), 2),
                round(float(point_z), 2),
            )
            location_names.setdefault((feature, parameter, rounded), set()).add(name)

    result: list[Outcome] = []
    for _pattern, (exact, features, outcomes) in zip(patterns, correspondence, strict=True):
        if not exact or len(features) != 1:
            result.append("unknown")
            continue
        feature = features[0]
        expected_size = size_labels.get(feature)
        ink_ok = True
        for outcome in outcomes:
            if outcome.state != "placed":
                ink_ok = False
                break
            parameter = outcome.parameter_id
            if parameter == "grouping.count":
                matching = {
                    name
                    for name, annotation in drawing.registry.iter_named()
                    if drawing.registry.feature_of(name) == feature
                    and getattr(annotation, "covers_count", None) == outcome.member_count
                    and rendered_label(name) == expected_size
                }
            elif parameter.startswith("location_pocket_pattern.location."):
                matching = location_names.get((feature, parameter, outcome.source_at), set())
                matching = {
                    name
                    for name in matching
                    if rendered_label(name) == location_labels.get((feature, parameter))
                }
            else:
                matching = {
                    name
                    for name, claim in confirmed
                    if getattr(claim, "feature", None) == feature
                    and str(getattr(claim, "parameter", "")) == parameter
                }
                expected_label = (
                    expected_size
                    if parameter.startswith("pocket_")
                    else pitch_labels.get((feature, parameter))
                )
                matching = {name for name in matching if rendered_label(name) == expected_label}
            if not matching:
                ink_ok = False
                break
        result.append("supported" if ink_ok else "unsupported")
    return result


def _declared_pocket_pattern_model(part, patterns, recognition):
    """Declare observed arrays through public ``Sheet.pocket_pattern`` and return IR."""
    from draftwright.model import pocket
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    from draftwright.section_recess_contract import section_recess_pattern_members

    for observed in patterns:
        sources = section_recess_pattern_members(observed, recognition.section_recesses)
        source = sources[0]
        member = pocket(**_pocket_declaration_arguments(source))
        members = tuple(_pocket_geometry(item)["origin"] for item in sources)
        center = getattr(observed, "center", None)
        if center is None:
            center = tuple(
                sum(point[index] for point in members) / len(members) for index in range(3)
            )
        kwargs: dict[str, object] = {
            "kind": "grid" if hasattr(observed, "row_pitch") else "linear",
            "count": len(members),
            "at": center,
        }
        if hasattr(observed, "row_pitch"):
            kwargs.update(
                grid=(observed.row_pitch, observed.col_pitch),
                rows=observed.rows,
                cols=observed.cols,
                angle=_pocket_pattern_angle(observed, source),
            )
        else:
            kwargs.update(pitch=observed.pitch, direction=observed.direction)
        sheet.pocket_pattern(member, **kwargs)
    return sheet.model()
