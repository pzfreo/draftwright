"""Chamfer and fillet physical correspondence, declaration, and drawing evidence.

The evidence oracle remains independent of production formatting and routing. Engine
imports stay inside functions to preserve evaluation's lazy-load seam.
"""

from __future__ import annotations

import math
from typing import Literal, TypeAlias

from draftwright.evaluation._groove_evidence import _groove_expected_tolerance_suffix

Scalar: TypeAlias = int | float | str | bool
Value: TypeAlias = Scalar | tuple[float, ...]
Outcome: TypeAlias = Literal["supported", "unknown", "unsupported"]


def _chamfer_point(chamfer) -> tuple[float, float, float]:
    point = getattr(chamfer, "at", None)
    if point is None:
        point = chamfer.frame.origin
    return tuple(round(float(component), 3) for component in point)  # type: ignore[return-value]


def _chamfer_identity(chamfer) -> tuple:
    return str(chamfer.axis), _chamfer_point(chamfer), bool(chamfer.turned)


def _chamfer_parameters(chamfer) -> dict[str, Value]:
    return {
        "leg1": round(float(chamfer.leg1), 3),
        "leg2": round(float(chamfer.leg2), 3),
        "angle": round(float(chamfer.angle), 2),
    }


def _chamfer_correspondence(chamfers, recognition, features, registry=None, omissions=()):
    """Per physical bevel, retain exact IR and production-ledger evidence."""
    from draftwright.linting.chamfer_coverage import chamfer_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    ledger = chamfer_requirement_outcomes(
        recognition,
        features,
        AnnotationRegistry() if registry is None else registry,
        omissions,
    )
    by_at: dict[tuple[float, float, float], list] = {}
    for outcome in ledger:
        by_at.setdefault(outcome.source_at, []).append(outcome)
    result = []
    for source in chamfers:
        candidates = [
            outcome
            for outcome in by_at.get(_chamfer_point(source), ())
            if outcome.features
            and _chamfer_identity(outcome.features[0]) == _chamfer_identity(source)
            and _chamfer_parameters(outcome.features[0]) == _chamfer_parameters(source)
        ]
        candidate_features = tuple(
            dict.fromkeys(feature for outcome in candidates for feature in outcome.features)
        )
        exact = (
            len(candidate_features) == 1
            and len(candidates) == 1
            and candidates[0].parameter_id == "chamfer.length"
            and candidates[0].features == candidate_features
        )
        result.append((exact, candidate_features, tuple(candidates)))
    return result


def _chamfer_model_outcomes(chamfers, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _features, _outcomes in _chamfer_correspondence(chamfers, recognition, features)
    ]


def _edge_expected_view(drawing, feature) -> str | None:
    origin = tuple(float(value) for value in feature.frame.origin)
    displaced = list(origin)
    displaced["xyz".index(feature.axis)] += 1.0
    candidates = []
    for view in ("front", "side", "plan"):
        try:
            start = drawing.at(view, *origin)[:2]
            end = drawing.at(view, *displaced)[:2]
        except (KeyError, ValueError):
            continue
        visible = math.hypot(*(float(b) - float(a) for a, b in zip(start, end, strict=True)))
        if (feature.turned and visible > 1e-9) or (not feature.turned and visible <= 1e-9):
            candidates.append(view)
    if feature.turned:
        return next((view for view in ("front", "side") if view in candidates), None)
    return candidates[0] if len(candidates) == 1 else None


def _edge_leader_targets(drawing, recognition, name: str, feature) -> bool:
    view = _edge_expected_view(drawing, feature)
    if view is None or drawing.registry.view_of(name) != view:
        return False
    try:
        tip = drawing.registry.named(name).tip
        origin = tuple(float(value) for value in feature.frame.origin)
        projected = drawing.at(view, *origin)
        if not feature.turned:
            return all(
                abs(float(tip[index]) - float(projected[index])) <= 1e-6 for index in range(2)
            )

        # Independently derive the physical profile point from the provider's public
        # finite-cylinder substrate.  Do not call the production
        # ``_turned_profile_site`` helper: this observer must be able to catch a broken
        # placement implementation rather than repeat its answer by construction.
        from quiddity import full_cylinders

        axis_i = "xyz".index(feature.axis)
        radial = tuple(index for index in range(3) if index != axis_i)
        candidates = []
        for group in recognition.cylinders:
            for cylinder in full_cylinders(list(group)):
                if not cylinder.get("external") or cylinder.get("axis") != feature.axis:
                    continue
                centre = tuple(float(value) for value in cylinder["axis_xyz"])
                radial_distance = math.hypot(
                    origin[radial[0]] - centre[radial[0]],
                    origin[radial[1]] - centre[radial[1]],
                )
                radius = float(cylinder["diameter"]) / 2.0
                surface_gap = abs(radial_distance - radius)
                direction = tuple(float(value) for value in cylinder["dir_xyz"])
                station = sum(value * component for value, component in zip(origin, direction))
                s_lo = float(cylinder["s_lo"])
                s_hi = float(cylinder["s_hi"])
                axial_gap = max(s_lo - station, 0.0, station - s_hi)
                patch_distance = math.hypot(surface_gap, axial_gap)
                if patch_distance > radius:
                    continue
                candidates.append(
                    (
                        (
                            patch_distance,
                            axial_gap,
                            surface_gap,
                            radial_distance,
                            int(cylinder["solid_idx"]),
                            centre[radial[0]],
                            centre[radial[1]],
                        ),
                        centre,
                    )
                )
        if not candidates:
            # A cone can adjoin a partial OD (for example a D- or half-shaft) that the
            # public substrate filter correctly declines to call a complete cylinder.
            # In that case production preserves the recogniser's already-physical site;
            # independently require the finished leader to preserve that projected point.
            return all(
                abs(float(tip[index]) - float(projected[index])) <= 1e-6 for index in range(2)
            )
        _score, centre = min(candidates, key=lambda candidate: candidate[0])

        # A profile view projects exactly one of the two radial axes.  Determine that
        # axis from the drawing transform itself, then rotate the recogniser's arbitrary
        # circumferential representative onto the visible shaft silhouette while
        # preserving both its radius and axial station.
        visible = []
        for index in radial:
            displaced = list(origin)
            displaced[index] += 1.0
            page = drawing.at(view, *displaced)
            magnitude = math.hypot(
                float(page[0]) - float(projected[0]),
                float(page[1]) - float(projected[1]),
            )
            if magnitude > 1e-9:
                visible.append(index)
        if len(visible) != 1:
            return False
        visible_i = visible[0]
        hidden_i = next(index for index in radial if index != visible_i)
        visible_delta = origin[visible_i] - centre[visible_i]
        hidden_delta = origin[hidden_i] - centre[hidden_i]
        radius = math.hypot(visible_delta, hidden_delta)
        sign_source = visible_delta if abs(visible_delta) > 1e-12 else hidden_delta
        expected_world = list(origin)
        expected_world[visible_i] = centre[visible_i] + math.copysign(radius, sign_source or 1.0)
        expected_world[hidden_i] = centre[hidden_i]
        expected_tip = drawing.at(view, *expected_world)
        return all(
            abs(float(tip[index]) - float(expected_tip[index])) <= 1e-6 for index in range(2)
        )
    except Exception:  # noqa: BLE001 — malformed finished ink cannot earn credit
        return False


def _edge_rendered_label(drawing, name: str) -> str | None:
    annotation = drawing.registry.named(name)
    label = getattr(annotation, "label", None) or getattr(annotation, "_annotate_label", None)
    return label if isinstance(label, str) else None


def _chamfer_drawing_outcomes(chamfers, drawing) -> list[Outcome]:
    """Verify exact callout form, compiler identity, view, and physical arrow station."""
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    correspondence = _chamfer_correspondence(
        chamfers,
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
    )
    confirmed: dict[object, set[str]] = {}
    for claim in verify_measurement_claims(drawing.registry, plan):
        measurement = claim.measurement
        if (
            claim.state == "confirmed"
            and measurement is not None
            and str(getattr(measurement, "parameter", "")) == "chamfer.length"
        ):
            confirmed.setdefault(getattr(measurement, "feature", None), set()).add(
                claim.annotation
            )

    # Independently reproduce the drafting statement, not the production renderer call. The
    # second leg and angle are form facts; the approved first-leg text/tolerance comes from the
    # compiler. Equal specifications share one physical statement, while every member retains
    # its own measurement identity.
    by_spec: dict[tuple, list] = {}
    for group in plan.of_kind("chamfer"):
        approved = next(
            (
                item
                for item in group.dims
                if (item.role, item.kind) == ("chamfer", "length") and item.id is not None
            ),
            None,
        )
        if approved is None:
            continue
        approved_id = approved.id
        if approved_id is None:  # narrowed explicitly for the static checker
            continue
        feature = approved_id.feature
        facts = group.facts
        spec = (
            round(float(approved.value), 3),
            round(float(facts.leg2), 3),
            round(float(facts.angle), 2),
            _groove_expected_tolerance_suffix(approved.tolerance, drawing.draft),
        )
        by_spec.setdefault(spec, []).append((feature, facts, approved))

    presentations: dict[object, tuple[str, tuple]] = {}
    for _spec, members in by_spec.items():
        representative, representative_facts, approved = sorted(
            members, key=lambda item: item[0].frame.origin
        )[0]
        if (
            abs(float(approved.value) - float(representative_facts.leg2)) < 0.05
            and abs(float(representative_facts.angle) - 45.0) < 0.5
        ):
            label = f"C{approved.value_text}"
        else:
            angle = float(representative_facts.angle)
            whole = round(angle)
            angle_text = str(whole) if abs(angle - whole) < 1e-6 else f"{angle:.1f}"
            label = f"{approved.value_text} × {angle_text}°"
        if len(members) > 1:
            label = f"{len(members)}× {label}"
        label += _groove_expected_tolerance_suffix(approved.tolerance, drawing.draft)
        group_features = tuple(feature for feature, _facts, _item in members)
        for feature in group_features:
            presentations[feature] = (label, group_features)

    result: list[Outcome] = []
    for exact, features, outcomes in correspondence:
        if not exact or len(features) != 1:
            result.append("unknown")
            continue
        feature = features[0]
        expected = presentations.get(feature)
        names = confirmed.get(feature, set())
        supported = (
            expected is not None
            and outcomes[0].state == "placed"
            and any(
                drawing.registry.feature_of(name) in expected[1]
                and _edge_rendered_label(drawing, name) == expected[0]
                and _edge_leader_targets(
                    drawing, recognition, name, drawing.registry.feature_of(name)
                )
                for name in names
            )
        )
        result.append("supported" if supported else "unsupported")
    return result


def _declared_chamfer_model(part, chamfers):
    """Declare observed chamfers through public ``Sheet.chamfer`` and return its IR."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for observed in chamfers:
        sheet.chamfer(
            axis=observed.axis,
            leg1=observed.leg1,
            leg2=observed.leg2,
            angle=observed.angle,
            at=observed.at,
            turned=observed.turned,
        )
    return sheet.model()


def _fillet_point(fillet) -> tuple[float, float, float]:
    point = getattr(fillet, "at", None)
    if point is None:
        point = fillet.frame.origin
    return tuple(round(float(component), 3) for component in point)  # type: ignore[return-value]


def _fillet_identity(fillet) -> tuple:
    return str(fillet.axis), _fillet_point(fillet), bool(fillet.turned)


def _fillet_parameters(fillet) -> dict[str, Value]:
    return {"radius": round(float(fillet.radius), 3)}


def _fillet_correspondence(fillets, recognition, features, registry=None, omissions=()):
    """Per physical round, retain exact IR and production-ledger evidence."""
    from draftwright.linting.fillet_coverage import fillet_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    ledger = fillet_requirement_outcomes(
        recognition,
        features,
        AnnotationRegistry() if registry is None else registry,
        omissions,
    )
    by_at: dict[tuple[float, float, float], list] = {}
    for outcome in ledger:
        by_at.setdefault(outcome.source_at, []).append(outcome)
    result = []
    for source in fillets:
        candidates = [
            outcome
            for outcome in by_at.get(_fillet_point(source), ())
            if outcome.features
            and _fillet_identity(outcome.features[0]) == _fillet_identity(source)
            and _fillet_parameters(outcome.features[0]) == _fillet_parameters(source)
        ]
        candidate_features = tuple(
            dict.fromkeys(feature for outcome in candidates for feature in outcome.features)
        )
        exact = (
            len(candidate_features) == 1
            and len(candidates) == 1
            and candidates[0].parameter_id == "fillet.radius"
            and candidates[0].features == candidate_features
        )
        result.append((exact, candidate_features, tuple(candidates)))
    return result


def _fillet_model_outcomes(fillets, recognition, features) -> list[Outcome]:
    return [
        "supported" if exact else "unknown"
        for exact, _features, _outcomes in _fillet_correspondence(fillets, recognition, features)
    ]


def _fillet_drawing_outcomes(fillets, drawing) -> list[Outcome]:
    """Verify exact radius ink, compiler identity, semantic view, and physical arrow station."""
    from draftwright.linting.evidence import verify_measurement_claims
    from draftwright.model.compiled import compile_dimensions

    recognition = drawing.recognition()
    model = drawing.model()
    plan = compile_dimensions(model)
    correspondence = _fillet_correspondence(
        fillets,
        recognition,
        model.features,
        drawing.registry,
        plan.diagnostics,
    )
    confirmed: dict[object, set[str]] = {}
    for claim in verify_measurement_claims(drawing.registry, plan):
        measurement = claim.measurement
        if (
            claim.state == "confirmed"
            and measurement is not None
            and str(getattr(measurement, "parameter", "")) == "fillet.radius"
        ):
            confirmed.setdefault(getattr(measurement, "feature", None), set()).add(
                claim.annotation
            )

    # Independently reproduce the drafting statement. Equal radii share one visible statement,
    # while every physical round must retain its own compiler identity on that ink. A tolerance
    # is truthful on collapsed ink only when every member carries exactly the same one.
    by_radius: dict[float, list] = {}
    for group in plan.of_kind("fillet"):
        approved = next(
            (
                item
                for item in group.dims
                if (item.role, item.kind) == ("fillet", "radius") and item.id is not None
            ),
            None,
        )
        if approved is None or approved.id is None:
            continue
        by_radius.setdefault(round(float(approved.value), 3), []).append(
            (approved.id.feature, approved)
        )

    presentations: dict[object, tuple[str, tuple]] = {}
    for _radius, members in sorted(by_radius.items()):
        representative, approved = sorted(members, key=lambda item: item[0].frame.origin)[0]
        tolerances = [item.tolerance for _feature, item in members]
        collapsed = (
            tolerances[0] if tolerances and all(t == tolerances[0] for t in tolerances) else None
        )
        label = f"R{approved.value_text}"
        if len(members) > 1:
            label = f"{len(members)}× {label}"
        label += _groove_expected_tolerance_suffix(collapsed, drawing.draft)
        group_features = tuple(feature for feature, _item in members)
        for feature in group_features:
            presentations[feature] = (label, group_features)

    result: list[Outcome] = []
    for exact, features, outcomes in correspondence:
        if not exact or len(features) != 1:
            result.append("unknown")
            continue
        feature = features[0]
        expected = presentations.get(feature)
        names = confirmed.get(feature, set())
        supported = (
            expected is not None
            and outcomes[0].state == "placed"
            and any(
                drawing.registry.feature_of(name) in expected[1]
                and _edge_rendered_label(drawing, name) == expected[0]
                and _edge_leader_targets(
                    drawing, recognition, name, drawing.registry.feature_of(name)
                )
                for name in names
            )
        )
        result.append("supported" if supported else "unsupported")
    return result


def _declared_fillet_model(part, fillets):
    """Declare observed fillets through public ``Sheet.fillet`` and return its IR."""
    from draftwright.sheet import Sheet

    sheet = Sheet(part)
    sheet.authored_dimensions()
    for observed in fillets:
        sheet.fillet(
            axis=observed.axis,
            radius=observed.radius,
            at=observed.at,
            turned=observed.turned,
        )
    return sheet.model()
