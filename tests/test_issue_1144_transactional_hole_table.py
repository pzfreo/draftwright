"""Regression coverage for transactional hole-table replacement (#1144)."""

from __future__ import annotations

import itertools

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.linting.hole_coverage import hole_requirement_outcomes
from draftwright.model.compiled import compile_dimensions


def _multi_hole_plate():
    """Two semantic groups: one Ø16 bore and a two-member Ø10 group."""
    return (
        Box(120, 80, 20)
        - Pos(40, 25, 0) * Cylinder(5, 30)
        - Pos(-40, 25, 0) * Cylinder(5, 30)
        - Pos(0, -25, 0) * Cylinder(8, 30)
    )


def _dense_scattered_plate():
    """Twenty distinct-spec bores, forcing automatic table escalation."""
    part = Box(90, 60, 12)
    columns = [-40 + i * 20 for i in range(5)]
    for i, (column, y) in enumerate(itertools.product(range(5), (-18, -6, 6, 18))):
        part -= Pos(columns[column], y, 0) * Cylinder(1.0 + i * 0.2, 20)
    return part


def _build_multi_hole_drawing(*, declared):
    part = _multi_hole_plate()
    model = build_drawing(part, auto_dims=False).model() if declared else None
    return build_drawing(part, page="A4", model=model)


def _outcomes(drawing):
    drawing.lint()
    return hole_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )


def _plan_bore_callouts(drawing):
    """Map semantic bore owner to its placed plan-view callout."""
    callouts = {}
    for name in drawing.annotations():
        if drawing.registry.view_of(name) != "plan":
            continue
        measurements = tuple(drawing.registry.measurement_of(name))
        diameter_ids = [item for item in measurements if item.parameter == "bore.diameter"]
        if len(diameter_ids) == 1:
            callouts[diameter_ids[0].feature] = (name, drawing.get_annotation(name))
    return callouts


def _drop_one_diameter_balloon(monkeypatch, diameter):
    """Force one otherwise feasible balloon assignment to become partial."""
    import draftwright.annotations.balloons as balloons_module

    original = balloons_module._assign_balloon_bands

    def partial_assignment(*args, **kwargs):
        bands, dropped = original(*args, **kwargs)
        for members in bands.values():
            for index, member in enumerate(members):
                if member[2].diameter == pytest.approx(diameter):
                    members.pop(index)
                    return bands, dropped + 1
        raise AssertionError(f"no assigned Ø{diameter:g} balloon to remove")

    monkeypatch.setattr(balloons_module, "_assign_balloon_bands", partial_assignment)


@pytest.mark.parametrize("declared", [False, True], ids=["automatic", "declared-ir"])
def test_successful_public_replacement_commits_only_after_every_balloon_lands(declared):
    drawing = _build_multi_hole_drawing(declared=declared)
    original = _plan_bore_callouts(drawing)

    table = drawing.add_hole_table("plan", replace_callouts=True)

    assert table is not None
    assert all(name not in drawing.annotations() for name, _annotation in original.values())
    assert len([name for name in drawing.annotations() if name.startswith("balloon_plan_")]) == 3
    assert set(table.covers_diameters) == {10.0, 16.0}
    table_features = {
        measurement.feature for measurement in drawing.registry.measurement_of("hole_table_plan")
    }
    assert table_features == set(original)
    represented = [
        outcome
        for outcome in _outcomes(drawing)
        if outcome.parameter_id in {"bore.diameter", "bore.through", "grouping.count"}
    ]
    assert represented
    assert {
        (outcome.state, outcome.representation, outcome.representation_reason)
        for outcome in represented
    } == {("placed", "hole_table", "required_balloons_placed")}
    assert not {"hole_requirement_missing", "feature_not_dimensioned"} & {
        issue.code for issue in drawing.lint()
    }


def test_public_table_remains_additive_by_default_for_compatibility():
    drawing = build_drawing(_multi_hole_plate(), page="A4")
    original = _plan_bore_callouts(drawing)

    assert drawing.add_hole_table("plan") is not None

    assert all(name in drawing.annotations() for name, _annotation in original.values())
    assert {
        (outcome.representation, outcome.representation_reason)
        for outcome in _outcomes(drawing)
        if outcome.parameter_id == "bore.diameter"
    } == {(None, None)}


def test_constrained_a4_table_failure_restores_exact_callouts_and_identity(monkeypatch):
    import draftwright.drawing as drawing_module

    drawing = build_drawing(_multi_hole_plate(), page="A4")
    original = _plan_bore_callouts(drawing)
    identities = {
        name: drawing.registry.identity_of(name) for name, _annotation in original.values()
    }
    pinned_name = next(iter(identities))
    drawing.pin(pinned_name)
    identities[pinned_name] = drawing.registry.identity_of(pinned_name)
    monkeypatch.setattr(drawing_module, "fit_box", lambda *_args, **_kwargs: None)

    assert drawing.add_hole_table("plan", replace_callouts=True) is None

    for name, annotation in original.values():
        assert drawing.get_annotation(name) is annotation
        assert drawing.registry.identity_of(name) == identities[name]
    assert drawing.registry.is_pinned(pinned_name)
    codes = [issue.code for issue in drawing.lint()]
    assert codes.count("table_dropped") == 1
    assert "hole_requirement_missing" not in codes
    represented = [
        outcome
        for outcome in _outcomes(drawing)
        if outcome.parameter_id in {"bore.diameter", "bore.through", "grouping.count"}
    ]
    assert {
        (outcome.state, outcome.representation, outcome.representation_reason)
        for outcome in represented
    } == {("placed", "feature_annotation", "table_not_placed")}


def test_partial_public_balloon_commit_restores_only_the_unresolved_group(monkeypatch):
    drawing = build_drawing(_multi_hole_plate(), page="A4")
    original = _plan_bore_callouts(drawing)
    grouped_feature = next(feature for feature in original if feature.count == 2)
    single_feature = next(feature for feature in original if feature.count == 1)
    _drop_one_diameter_balloon(monkeypatch, 10.0)

    table = drawing.add_hole_table("plan", replace_callouts=True)

    assert table is not None
    grouped_name, grouped_callout = original[grouped_feature]
    assert drawing.get_annotation(grouped_name) is grouped_callout
    assert original[single_feature][0] not in drawing.annotations()
    assert table.covers_diameters == (16.0,)
    assert {
        measurement.feature for measurement in drawing.registry.measurement_of("hole_table_plan")
    } == {single_feature}
    assert not [
        name
        for name in drawing.annotations()
        if name.startswith("balloon_plan_")
        and drawing.registry.feature_of(name) == grouped_feature
    ]
    warning_codes = {
        issue.code for issue in drawing.lint() if issue.severity in {"warning", "error"}
    }
    assert warning_codes == {"balloon_dropped"}
    outcomes = _outcomes(drawing)
    assert all(outcome.state == "placed" for outcome in outcomes)
    represented = {
        (
            outcome.representation,
            outcome.representation_reason,
        )
        for outcome in outcomes
        if outcome.parameter_id == "bore.diameter"
    }
    assert represented == {
        ("hole_table", "required_balloons_placed"),
        ("feature_annotation", "required_balloon_not_placed"),
    }


def test_automatic_partial_balloon_result_never_claims_the_unkeyed_feature(monkeypatch):
    _drop_one_diameter_balloon(monkeypatch, 2.0)

    drawing = build_drawing(_dense_scattered_plate())

    table = drawing.get_annotation("hole_table_plan")
    assert table is not None
    assert len(table.covers_diameters) == 19
    table_features = {
        measurement.feature for measurement in drawing.registry.measurement_of("hole_table_plan")
    }
    assert len(table_features) == 19
    assert {feature.diameter for feature in table_features} == set(table.covers_diameters)
    assert 2.0 not in table.covers_diameters
    assert len([name for name in drawing.annotations() if name.startswith("balloon_plan_")]) == 19
    assert "balloon_dropped" in {issue.code for issue in drawing.lint()}
    outcomes = _outcomes(drawing)
    assert all(outcome.state == "placed" for outcome in outcomes)
    diameter_representations = {
        (outcome.representation, outcome.representation_reason)
        for outcome in outcomes
        if outcome.parameter_id == "bore.diameter"
    }
    assert diameter_representations == {
        ("hole_table", "required_balloons_placed"),
        ("feature_annotation", "required_balloon_not_placed"),
    }


def test_replacement_without_balloons_fails_before_mutating_the_drawing():
    drawing = build_drawing(_multi_hole_plate(), page="A4")
    before = tuple(drawing.annotations())

    with pytest.raises(ValueError, match="requires balloons=True"):
        drawing.add_hole_table("plan", balloons=False, replace_callouts=True)

    assert tuple(drawing.annotations()) == before
    assert "hole_table_plan" not in drawing.annotations()


def test_replacement_without_a_complete_callout_fallback_fails_before_mutation():
    drawing = build_drawing(_multi_hole_plate(), page="A4", auto_dims=False)
    before = tuple(drawing.annotations())

    with pytest.raises(ValueError, match="existing bore callout for every"):
        drawing.add_hole_table("plan", replace_callouts=True)

    assert tuple(drawing.annotations()) == before
    assert "hole_table_plan" not in drawing.annotations()
