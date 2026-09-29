"""Flat and polygonal physical correspondence, declaration, and drawing evidence.

The evidence oracle remains independent of production formatting and routing. Engine
imports stay inside functions to preserve evaluation's lazy-load seam.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

from draftwright.evaluation._groove_evidence import _groove_expected_tolerance_suffix

Scalar: TypeAlias = int | float | str | bool
Value: TypeAlias = Scalar | tuple[float, ...]
Outcome: TypeAlias = Literal["supported", "unknown", "unsupported"]


def _flat_point(flat) -> tuple[float, float, float]:
    point = getattr(flat, "at", None)
    if point is None:
        point = flat.frame.origin
    return tuple(round(float(component), 3) for component in point)  # type: ignore[return-value]


def _flat_identity(flat) -> tuple:
    """Physical stock identity, deliberately excluding its scored A/F value."""
    from draftwright._geometry import _canonical_axis_direction

    return (
        str(flat.axis),
        _canonical_axis_direction(flat.axis, getattr(flat, "axis_direction", None)),
        tuple(round(float(component), 3) for component in flat.axis_line),
        tuple(round(float(component), 3) for component in flat.stock_span),
    )


def _flat_groups(flats) -> list[tuple[tuple, tuple]]:
    grouped: dict[tuple, list] = {}
    for flat in flats:
        grouped.setdefault(_flat_identity(flat), []).append(flat)
    return [
        (identity, tuple(sorted(members, key=_flat_point)))
        for identity, members in sorted(grouped.items())
    ]


def _flat_parameters(members) -> dict[str, Value]:
    across_values = tuple(sorted({round(float(member.across), 3) for member in members}))
    across: Value = across_values[0] if len(across_values) == 1 else across_values
    anchors = tuple(component for member in members for component in _flat_point(member))
    return {"across": across, "face_count": len(members), "anchors": anchors}


def _flat_correspondence(flats, recognition, features) -> list[tuple[bool, tuple]]:
    """Per physical requirement, retain exact member IR and production-ledger evidence."""
    from draftwright.linting.flat_coverage import flat_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    ledger = flat_requirement_outcomes(recognition, features, AnnotationRegistry())
    ledger_by_identity: dict[tuple, list] = {}
    for outcome in ledger:
        ledger_by_identity.setdefault(_flat_identity(outcome), []).append(outcome)
    feature_by_identity: dict[tuple, list] = {}
    for feature in features:
        if getattr(feature, "kind", None) == "flat":
            feature_by_identity.setdefault(_flat_identity(feature), []).append(feature)

    result = []
    for identity, members in _flat_groups(flats):
        candidate_features = tuple(sorted(feature_by_identity.get(identity, ()), key=_flat_point))
        candidate_outcomes = ledger_by_identity.get(identity, ())
        exact_members = _flat_parameters(candidate_features) == _flat_parameters(
            members
        ) and tuple(_flat_point(feature) for feature in candidate_features) == tuple(
            _flat_point(member) for member in members
        )
        production_join = (
            len(candidate_outcomes) == 1
            and candidate_outcomes[0].state != "unverifiable"
            and round(float(candidate_outcomes[0].across), 3)
            in {round(float(member.across), 3) for member in members}
        )
        result.append((exact_members and production_join, candidate_features))
    return result


def _flat_model_outcomes(flats, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _ in _flat_correspondence(flats, recognition, features)
    ]


def _flat_drawing_outcomes(flats, drawing) -> list[Outcome]:
    """Per physical A/F requirement, verify placed semantic ownership and rendered value."""
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.linting.flat_coverage import flat_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    ledger = flat_requirement_outcomes(
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
    )
    ledger_by_identity: dict[tuple, list] = {}
    for outcome in ledger:
        ledger_by_identity.setdefault(_flat_identity(outcome), []).append(outcome)
    confirmed = {
        claim.measurement
        for claim in verify_measurement_claims(drawing.registry, plan)
        if claim.state == "confirmed" and claim.measurement is not None
    }
    placed = {"placed", "satisfied_by_structured_note"}
    result: list[Outcome] = []
    for (identity, _members), (exact, features) in zip(
        _flat_groups(flats),
        _flat_correspondence(flats, recognition, model.features),
        strict=True,
    ):
        outcomes = ledger_by_identity.get(identity, ())
        if not exact or len(outcomes) != 1:
            result.append("unknown")
        elif outcomes[0].state not in placed or any(
            not any(
                getattr(claim, "feature", None) == feature
                and str(getattr(claim, "parameter", "")) == "flat.length"
                for claim in confirmed
            )
            for feature in features
        ):
            result.append("unsupported")
        else:
            result.append("supported")
    return result


def _declared_flat_model(part, flats):
    """Declare observed flat faces through public ``Sheet.flat`` and return its IR."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for observed in flats:
        sheet.flat(
            axis=observed.axis,
            across=observed.across,
            at=observed.at,
            axis_line=observed.axis_line,
            stock_span=observed.stock_span,
            axis_direction=observed.axis_direction,
        )
    return sheet.model()


def _polygonal_boss_center(boss) -> tuple[float, float, float]:
    from draftwright.linting.polygonal_boss_coverage import polygonal_boss_center

    return polygonal_boss_center(boss)


def _polygonal_boss_identity(boss) -> tuple[str, tuple[float, float, float]]:
    axis = getattr(boss, "axis", None)
    if axis is None:
        axis = boss.frame.axis
    return str(axis), _polygonal_boss_center(boss)


def _polygonal_boss_parameters(boss) -> dict[str, Value]:
    supports = sorted(
        (
            tuple(round(float(component), 3) for component in direction),
            tuple(round(float(component), 3) for component in centre),
        )
        for direction, centre in zip(boss.flat_directions, boss.flat_centres, strict=True)
    )
    return {
        "side_count": int(boss.side_count),
        "across_flats": round(float(boss.across_flats), 3),
        "height": round(float(boss.height), 3),
        "flat_supports": tuple(
            component
            for direction, centre in supports
            for point in (direction, centre)
            for component in point
        ),
    }


def _polygonal_boss_correspondence(bosses, recognition, features, registry=None, omissions=()):
    """Per physical prism, retain exact IR and production-ledger evidence."""
    from draftwright.linting.polygonal_boss_coverage import (
        polygonal_boss_key,
        polygonal_boss_requirement_outcomes,
    )
    from draftwright.registry import AnnotationRegistry

    ledger = polygonal_boss_requirement_outcomes(
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
    expected_ids = {"polygon_across_flats.length", "boss_height.length"}
    for source in bosses:
        candidates = [
            outcome
            for outcome in by_at.get(_polygonal_boss_center(source), ())
            if outcome.features
            and polygonal_boss_key(outcome.features[0]) == polygonal_boss_key(source)
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


def _polygonal_boss_model_outcomes(bosses, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _features, _outcomes in _polygonal_boss_correspondence(
            bosses, recognition, features
        )
    ]


def _polygonal_boss_drawing_outcomes(bosses, drawing) -> list[Outcome]:
    """Verify exact A/F and height identities plus finished semantic ink."""
    from build123d_drafting import Dimension, Leader

    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    correspondence = _polygonal_boss_correspondence(
        bosses,
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

    af_labels: dict[object, str] = {}
    for group in plan.of_kind("polygonal_boss"):
        approved = next(
            (
                item
                for item in group.dims
                if (item.role, item.kind) == ("polygon_across_flats", "length")
                and item.id is not None
            ),
            None,
        )
        if approved is None or approved.id is None:
            continue
        prefix = "HEX" if group.facts.side_count == 6 else f"{group.facts.side_count}-SIDED"
        suffix = _groove_expected_tolerance_suffix(approved.tolerance, drawing.draft)
        af_labels[approved.id.feature] = f"{prefix} {approved.value_text}{suffix} A/F"

    def rendered_label(name: str) -> str | None:
        annotation = drawing.registry.named(name)
        label = getattr(annotation, "label", None) or getattr(annotation, "_annotate_label", None)
        return label if isinstance(label, str) else None

    def leader_targets_flat(name: str, feature) -> bool:
        """Validate rendered ink after semantic correspondence is already established."""
        from draftwright._geometry import _END_ON

        view = _END_ON.get(feature.frame.axis)
        if view is None or drawing.registry.view_of(name) != view:
            return False
        annotation = drawing.registry.named(name)
        try:
            tip = annotation.tip
            projected = (drawing.at(view, *centre) for centre in feature.flat_centres)
            return any(
                len(tip) >= 2
                and all(
                    abs(float(tip[index]) - float(expected[index])) <= 1e-6 for index in range(2)
                )
                for expected in projected
            )
        except Exception:  # noqa: BLE001 — malformed finished ink cannot earn credit
            return False

    result: list[Outcome] = []
    for exact, features, outcomes in correspondence:
        if not exact or len(features) != 1:
            result.append("unknown")
            continue
        feature = features[0]
        states_ok = all(outcome.state == "placed" for outcome in outcomes)
        af_names = confirmed.get((feature, "polygon_across_flats.length"), set())
        height_names = confirmed.get((feature, "boss_height.length"), set())
        af_ok = any(
            drawing.registry.feature_of(name) == feature
            and isinstance(drawing.registry.named(name), Leader)
            and rendered_label(name) == af_labels.get(feature)
            and leader_targets_flat(name, feature)
            for name in af_names
        )
        height_ok = any(
            drawing.registry.feature_of(name) == feature
            and isinstance(drawing.registry.named(name), Dimension)
            for name in height_names
        )
        result.append("supported" if states_ok and af_ok and height_ok else "unsupported")
    return result


def _declared_polygonal_boss_model(part, bosses):
    """Declare observed prisms through public ``Sheet.polygonal_boss``."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for observed in bosses:
        axis = getattr(observed, "axis", None)
        if axis is None:
            axis = observed.frame.axis
        center = _polygonal_boss_center(observed)
        span = getattr(observed, "span", None)
        if span is None:
            axis_index = "xyz".index(str(axis))
            start = list(center)
            end = list(center)
            start[axis_index] = observed.base
            end[axis_index] = observed.top
            span = (tuple(start), tuple(end))
        sheet.polygonal_boss(
            side_count=observed.side_count,
            across_flats=observed.across_flats,
            height=observed.height,
            at=center,
            axis=axis,
            span=span,
            flat_directions=observed.flat_directions,
            flat_centres=observed.flat_centres,
        )
    return sheet.model()


def _polygonal_stock_center(stock) -> tuple[float, float, float]:
    from draftwright.linting.polygonal_stock_coverage import polygonal_stock_center

    return polygonal_stock_center(stock)


def _polygonal_stock_identity(stock) -> tuple[str, tuple[float, float, float]]:
    axis = getattr(stock, "axis", None)
    if axis is None:
        axis = stock.frame.axis
    return str(axis), _polygonal_stock_center(stock)


def _polygonal_stock_parameters(stock) -> dict[str, Value]:
    supports = sorted(
        (
            tuple(round(float(component), 3) for component in direction),
            tuple(round(float(component), 3) for component in centre),
        )
        for direction, centre in zip(stock.flat_directions, stock.flat_centres, strict=True)
    )
    length = getattr(stock, "length", None)
    if length is None:
        length = stock.top - stock.base
    return {
        "side_count": int(stock.side_count),
        "across_flats": round(float(stock.across_flats), 3),
        "length": round(float(length), 3),
        "flat_supports": tuple(
            component
            for direction, centre in supports
            for point in (direction, centre)
            for component in point
        ),
    }


def _polygonal_stock_correspondence(stocks, recognition, features, registry=None, omissions=()):
    """Per whole-stock occurrence, retain exact IR and production-ledger evidence."""
    from draftwright.linting.polygonal_stock_coverage import (
        polygonal_stock_key,
        polygonal_stock_outcomes,
    )
    from draftwright.registry import AnnotationRegistry

    ledger = polygonal_stock_outcomes(
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
    expected_ids = {"polygon_across_flats.length", "stock_length.length"}
    for source in stocks:
        candidates = [
            outcome
            for outcome in by_at.get(_polygonal_stock_center(source), ())
            if outcome.features
            and polygonal_stock_key(outcome.features[0]) == polygonal_stock_key(source)
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


def _polygonal_stock_model_outcomes(stocks, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _features, _outcomes in _polygonal_stock_correspondence(
            stocks, recognition, features
        )
    ]


def _polygonal_stock_drawing_outcomes(stocks, drawing) -> list[Outcome]:
    """Verify exact A/F and stock-length identities plus finished semantic ink."""
    from build123d_drafting import Dimension, Leader

    from draftwright.linting.coverage import _dim_vertices
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    correspondence = _polygonal_stock_correspondence(
        stocks,
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

    af_labels: dict[object, str] = {}
    length_labels: dict[object, str] = {}
    for group in plan.of_kind("polygonal_stock"):
        approved_af = next(
            (
                item
                for item in group.dims
                if (item.role, item.kind) == ("polygon_across_flats", "length")
                and item.id is not None
            ),
            None,
        )
        if approved_af is not None and approved_af.id is not None:
            prefix = "HEX" if group.facts.side_count == 6 else f"{group.facts.side_count}-SIDED"
            suffix = _groove_expected_tolerance_suffix(approved_af.tolerance, drawing.draft)
            af_labels[approved_af.id.feature] = f"{prefix} {approved_af.value_text}{suffix} A/F"
        approved_length = next(
            (
                item
                for item in group.dims
                if (item.role, item.kind) == ("stock_length", "length") and item.id is not None
            ),
            None,
        )
        if approved_length is not None and approved_length.id is not None:
            suffix = _groove_expected_tolerance_suffix(approved_length.tolerance, drawing.draft)
            length_labels[approved_length.id.feature] = f"{approved_length.value_text}{suffix}"

    def rendered_label(name: str) -> str | None:
        annotation = drawing.registry.named(name)
        label = getattr(annotation, "label", None) or getattr(annotation, "_annotate_label", None)
        return label if isinstance(label, str) else None

    def leader_targets_flat(name: str, feature) -> bool:
        """Validate rendered ink only after semantic correspondence is established."""
        from draftwright._geometry import _END_ON

        view = _END_ON.get(feature.frame.axis)
        if view is None or drawing.registry.view_of(name) != view:
            return False
        annotation = drawing.registry.named(name)
        try:
            tip = annotation.tip
            projected = (drawing.at(view, *centre) for centre in feature.flat_centres)
            return any(
                len(tip) >= 2
                and all(
                    abs(float(tip[index]) - float(expected[index])) <= 1e-6 for index in range(2)
                )
                for expected in projected
            )
        except Exception:  # noqa: BLE001 — malformed finished ink cannot earn credit
            return False

    def dimension_targets_span(name: str, feature) -> bool:
        """Require the finished length witness to remain on the physical cap span."""
        from draftwright._geometry import _EDGE_ON

        view = _EDGE_ON.get(feature.frame.axis)
        if view is None or drawing.registry.view_of(name) != view:
            return False
        try:
            observed = _dim_vertices(drawing.registry.named(name))
            expected = [drawing.at(view, *point) for point in feature.span]
            if len(observed) != 2:
                return False
            return any(
                all(
                    abs(float(observed[index][component]) - float(candidate[index][component]))
                    <= 1e-6
                    for index in range(2)
                    for component in range(2)
                )
                for candidate in (expected, list(reversed(expected)))
            )
        # `_dim_vertices` absorbs malformed dimension geometry; this is a final fail-closed
        # guard against impossible corrupt registry/view state, not an executable contract path.
        except Exception:  # noqa: BLE001  # pragma: no cover
            return False

    result: list[Outcome] = []
    for exact, features, outcomes in correspondence:
        if not exact or len(features) != 1:
            result.append("unknown")
            continue
        feature = features[0]
        states_ok = all(outcome.state == "placed" for outcome in outcomes)
        af_names = confirmed.get((feature, "polygon_across_flats.length"), set())
        length_names = confirmed.get((feature, "stock_length.length"), set())
        af_ok = any(
            drawing.registry.feature_of(name) == feature
            and isinstance(drawing.registry.named(name), Leader)
            and rendered_label(name) == af_labels.get(feature)
            and leader_targets_flat(name, feature)
            for name in af_names
        )
        length_ok = any(
            drawing.registry.feature_of(name) == feature
            and isinstance(drawing.registry.named(name), Dimension)
            and rendered_label(name) == length_labels.get(feature)
            and dimension_targets_span(name, feature)
            for name in length_names
        )
        result.append("supported" if states_ok and af_ok and length_ok else "unsupported")
    return result


def _declared_polygonal_stock_model(part, stocks):
    """Declare observed whole prisms through public ``Sheet.polygonal_stock``."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for observed in stocks:
        axis = getattr(observed, "axis", None)
        if axis is None:
            axis = observed.frame.axis
        center = _polygonal_stock_center(observed)
        span = getattr(observed, "span", None)
        if span is None:
            axis_index = "xyz".index(str(axis))
            start = list(center)
            end = list(center)
            start[axis_index] = observed.base
            end[axis_index] = observed.top
            span = (tuple(start), tuple(end))
        sheet.polygonal_stock(
            side_count=observed.side_count,
            across_flats=observed.across_flats,
            length=getattr(observed, "length", observed.top - observed.base),
            at=center,
            axis=axis,
            span=span,
            flat_directions=observed.flat_directions,
            flat_centres=observed.flat_centres,
        )
    return sheet.model()
