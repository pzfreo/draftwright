"""Regression coverage for transactional hole-table replacement (#1144)."""

from __future__ import annotations

import itertools
from dataclasses import replace
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.fits import fit_class
from draftwright.linting.hole_coverage import hole_requirement_outcomes
from draftwright.linting.issues import LintIssue
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


def _dense_plate_with_counterbore():
    """Dense escalation with one recognised compound hole callout."""
    part = Box(90, 60, 12)
    columns = [-40 + i * 20 for i in range(5)]
    for i, (column, y) in enumerate(itertools.product(range(5), (-18, -6, 6, 18))):
        diameter = 2.0 + i * 0.4
        part -= Pos(columns[column], y, 0) * Cylinder(diameter / 2, 20)
        if i == 0:
            part -= Pos(columns[column], y, 4.5) * Cylinder(2.5, 3)
    return part


def _dense_plate_with_double_d():
    """Dense escalation with one recognised DOUBLE-D through bore."""
    part = Box(90, 60, 12)
    columns = [-40 + i * 20 for i in range(5)]
    centered = (Align.CENTER, Align.CENTER, Align.CENTER)
    for i, (column, y) in enumerate(itertools.product(range(5), (-18, -6, 6, 18))):
        if i == 0:
            cutter = Cylinder(2.5, 20, align=centered) & Box(3.6, 20, 30, align=centered)
        else:
            cutter = Cylinder(1.0 + i * 0.2, 20)
        part -= Pos(columns[column], y, 0) * cutter
    return part


def _double_d_plate():
    centered = (Align.CENTER, Align.CENTER, Align.CENTER)
    cutter = Cylinder(5, 20, align=centered) & Box(7.2, 20, 30, align=centered)
    return Box(30, 30, 10, align=centered) - cutter


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


def _transaction_state(drawing):
    """Every mutable surface the public table transaction promises to restore."""
    registry = drawing.registry.snapshot()
    return {
        "items": tuple(id(item) for item in drawing.items),
        "named": {name: id(item) for name, item in registry["named"].items()},
        "views": registry["anno_view"],
        "features": registry["anno_feature"],
        "measurements": registry["anno_measurement"],
        "pins": registry["pinned"],
        "issues": drawing.registry.issues,
        "coverage": drawing.coverage.snapshot(),
        "markers": {
            name: (
                getattr(drawing.get_annotation(name), "hole_representation", None),
                getattr(drawing.get_annotation(name), "hole_representation_reason", None),
            )
            for name in drawing.annotations()
        },
    }


def _source_outcomes(drawing, feature):
    point = list(feature.frame.origin)
    if feature.through:
        point["xyz".index(feature.frame.axis)] = 0.0
    expected = tuple(round(float(value), 3) for value in point)
    return [outcome for outcome in _outcomes(drawing) if outcome.source_at == expected]


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


def test_transaction_collects_every_structured_feature_seam():
    from draftwright.annotations._common import (
        _annotation_hole_features,
        _hole_table_replaceable_annotation,
        _stash_annotations,
    )
    from draftwright.registry import AnnotationRegistry

    feature = type("Feature", (), {"kind": "hole"})()
    representation_feature = type("Feature", (), {"kind": "hole"})()
    annotation = SimpleNamespace(
        covers_hole_locations=(),
        covers_hole_requirements_by_feature=((feature, "bore.through", 1),),
        covers_hole_centers=((feature, (0.0, 0.0, 0.0), "plan"),),
        covers_hole_representations_by_feature=(
            (representation_feature, "hole_table", "required_balloons_placed"),
        ),
    )
    registry = AnnotationRegistry()

    assert _annotation_hole_features(registry, "missing", annotation) == frozenset(
        {feature, representation_feature}
    )
    assert not _hole_table_replaceable_annotation(
        registry, "missing", SimpleNamespace(covers_hole_locations=())
    )
    assert _stash_annotations(SimpleNamespace(registry=registry), ["missing"]) == {}


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


def test_partial_public_commit_refits_clear_of_the_restored_fallback(monkeypatch):
    import draftwright.drawing as drawing_module

    drawing = build_drawing(_multi_hole_plate(), page="A4")
    original = _plan_bore_callouts(drawing)
    grouped_feature = next(feature for feature in original if feature.count == 2)
    grouped_callout = original[grouped_feature][1]
    bbox = grouped_callout.bounding_box()
    original_fit_box = drawing_module.fit_box
    fit_calls = 0

    def occupy_freed_fallback(size, region, obstacles, prefer):
        nonlocal fit_calls
        fit_calls += 1
        if fit_calls == 1:
            return (bbox.min.X, bbox.min.Y)
        return original_fit_box(size, region, obstacles, prefer)

    monkeypatch.setattr(drawing_module, "fit_box", occupy_freed_fallback)
    _drop_one_diameter_balloon(monkeypatch, 10.0)

    table = drawing.add_hole_table("plan", replace_callouts=True)

    assert table is not None
    assert fit_calls == 2
    assert drawing.get_annotation(original[grouped_feature][0]) is grouped_callout
    assert not {
        "annotation_overlap",
        "view_annotation_overlap",
    } & {issue.code for issue in drawing.lint()}


def test_partial_public_commit_rolls_back_when_the_fallback_aware_refit_fails(monkeypatch):
    import draftwright.drawing as drawing_module

    drawing = build_drawing(_multi_hole_plate(), page="A4")
    original = _plan_bore_callouts(drawing)
    original_fit_box = drawing_module.fit_box
    fit_calls = 0

    def fail_second_fit(size, region, obstacles, prefer):
        nonlocal fit_calls
        fit_calls += 1
        if fit_calls == 2:
            return None
        return original_fit_box(size, region, obstacles, prefer)

    monkeypatch.setattr(drawing_module, "fit_box", fail_second_fit)
    _drop_one_diameter_balloon(monkeypatch, 10.0)

    assert drawing.add_hole_table("plan", replace_callouts=True) is None

    assert fit_calls == 2
    assert all(
        drawing.get_annotation(name) is annotation for name, annotation in original.values()
    )
    assert "hole_table_plan" not in drawing.annotations()
    assert not [name for name in drawing.annotations() if name.startswith("balloon_plan_")]
    codes = {issue.code for issue in drawing.lint()}
    assert {"balloon_dropped", "table_dropped"} <= codes
    assert "hole_requirement_missing" not in codes


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


def test_automatic_partial_result_rolls_back_when_the_fallback_aware_refit_fails(
    monkeypatch,
):
    import draftwright.drawing as drawing_module

    original_fit_box = drawing_module.fit_box
    first_success_seen = False

    def fail_after_first_success(size, region, obstacles, prefer):
        nonlocal first_success_seen
        result = original_fit_box(size, region, obstacles, prefer)
        if result is None:
            return None
        if first_success_seen:
            return None
        first_success_seen = True
        return result

    monkeypatch.setattr(drawing_module, "fit_box", fail_after_first_success)
    _drop_one_diameter_balloon(monkeypatch, 2.0)

    drawing = build_drawing(_dense_scattered_plate())

    assert first_success_seen
    assert "hole_table_plan" not in drawing.annotations()
    assert not [name for name in drawing.annotations() if name.startswith("balloon_plan_")]
    codes = {issue.code for issue in drawing.lint()}
    assert {"balloon_dropped", "table_dropped"} <= codes
    represented = {
        (outcome.representation, outcome.representation_reason)
        for outcome in _outcomes(drawing)
        if outcome.state == "placed" and outcome.parameter_id == "bore.diameter"
    }
    assert represented == {("feature_annotation", "required_balloon_not_placed")}


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


def test_an_existing_table_cannot_substitute_for_a_missing_leader_fallback():
    drawing = build_drawing(_multi_hole_plate(), page="A4")
    callout_name = next(iter(_plan_bore_callouts(drawing).values()))[0]
    assert drawing.add_hole_table("plan") is not None
    drawing.remove(callout_name)
    for name in tuple(drawing.annotations()):
        if name.startswith("balloon_plan_"):
            drawing.remove(name)
    before = tuple(drawing.annotations())

    with pytest.raises(ValueError, match="existing bore callout for every"):
        drawing.add_hole_table("plan", name="replacement_table", replace_callouts=True)

    assert tuple(drawing.annotations()) == before
    assert "replacement_table" not in drawing.annotations()


def test_declared_qty_without_member_positions_cannot_commit_one_balloon_as_two():
    part = _multi_hole_plate()
    detected = build_drawing(part, auto_dims=False).model()
    grouped = next(
        feature for feature in detected.features if feature.kind == "hole" and feature.count == 2
    )
    declared_group = replace(grouped, members=())
    declared = replace(
        detected,
        features=[
            declared_group if feature is grouped else feature for feature in detected.features
        ],
    )
    drawing = build_drawing(part, page="A4", model=declared)
    original = _plan_bore_callouts(drawing)
    before_codes = {issue.code for issue in drawing.lint()}

    table = drawing.add_hole_table("plan", replace_callouts=True)

    assert table is not None
    grouped_name, grouped_callout = original[declared_group]
    assert drawing.get_annotation(grouped_name) is grouped_callout
    assert not [
        name
        for name in drawing.annotations()
        if name.startswith("balloon_plan_") and drawing.registry.feature_of(name) == declared_group
    ]
    assert declared_group not in {
        measurement.feature for measurement in drawing.registry.measurement_of("hole_table_plan")
    }
    assert table.covers_diameters == (16.0,)
    after_codes = {issue.code for issue in drawing.lint()}
    assert after_codes == before_codes
    assert "hole_requirement_missing" not in after_codes


@pytest.mark.parametrize("failing_stage", ["table", "balloons"])
def test_public_replacement_rolls_back_exactly_when_placement_raises(monkeypatch, failing_stage):
    import draftwright.drawing as drawing_module

    drawing = build_drawing(_multi_hole_plate(), page="A4")
    before = _transaction_state(drawing)

    def mutate_then_fail():
        drawing.registry.record_issue(
            LintIssue(
                severity="warning",
                code="temporary_transaction_issue",
                message="must be rolled back",
            )
        )
        drawing.coverage.cover_scattered_hole_doc("temporary_transaction_coverage")
        raise RuntimeError(f"forced {failing_stage} failure")

    if failing_stage == "table":
        original = drawing.add_table

        def fail_after_table(*args, **kwargs):
            assert original(*args, **kwargs) is not None
            mutate_then_fail()

        monkeypatch.setattr(drawing, "add_table", fail_after_table)
    else:
        original = drawing_module.render_balloons

        def fail_after_balloons(*args, **kwargs):
            original(*args, **kwargs)
            assert any(name.startswith("balloon_plan_") for name in drawing.annotations())
            mutate_then_fail()

        monkeypatch.setattr(drawing_module, "render_balloons", fail_after_balloons)

    with pytest.raises(RuntimeError, match=f"forced {failing_stage} failure"):
        drawing.add_hole_table("plan", replace_callouts=True)

    assert _transaction_state(drawing) == before


def test_public_replacement_rolls_back_if_stashing_itself_raises(monkeypatch):
    drawing = build_drawing(_multi_hole_plate(), page="A4")
    before = _transaction_state(drawing)
    original_remove = drawing.remove
    removals = 0

    def fail_during_second_stash(name):
        nonlocal removals
        removals += 1
        if removals == 2:
            drawing.registry.record_issue(
                LintIssue(
                    severity="warning",
                    code="partial_stash_issue",
                    message="must be rolled back",
                )
            )
            drawing.coverage.cover_scattered_hole_doc("partial_stash_coverage")
            raise RuntimeError("forced partial stash failure")
        return original_remove(name)

    monkeypatch.setattr(drawing, "remove", fail_during_second_stash)

    with pytest.raises(RuntimeError, match="forced partial stash failure"):
        drawing.add_hole_table("plan", replace_callouts=True)

    assert removals == 2
    assert _transaction_state(drawing) == before


def test_exception_after_partial_restore_clears_temporary_representation_markers(monkeypatch):
    import draftwright.drawing as drawing_module

    drawing = build_drawing(_multi_hole_plate(), page="A4")
    original = _plan_bore_callouts(drawing)
    for _name, annotation in original.values():
        annotation.hole_representation = "existing_representation"
        annotation.hole_representation_reason = "existing_reason"
    before_markers = {
        name: (
            getattr(annotation, "hole_representation", None),
            getattr(annotation, "hole_representation_reason", None),
        )
        for name, annotation in original.values()
    }
    before = _transaction_state(drawing)
    _drop_one_diameter_balloon(monkeypatch, 10.0)
    original_coverage = drawing_module._register_hole_table_coverage

    def fail_coverage(*args, **kwargs):
        original_coverage(*args, **kwargs)
        drawing.registry.record_issue(
            LintIssue(
                severity="warning",
                code="temporary_coverage_issue",
                message="must be rolled back",
            )
        )
        drawing.coverage.cover_scattered_hole_doc("temporary_coverage_state")
        raise RuntimeError("forced coverage failure")

    monkeypatch.setattr(drawing_module, "_register_hole_table_coverage", fail_coverage)

    with pytest.raises(RuntimeError, match="forced coverage failure"):
        drawing.add_hole_table("plan", replace_callouts=True)

    assert {
        name: (
            getattr(drawing.get_annotation(name), "hole_representation", None),
            getattr(drawing.get_annotation(name), "hole_representation_reason", None),
        )
        for name, _annotation in original.values()
    } == before_markers
    assert _transaction_state(drawing) == before


def test_replacement_rejects_a_table_name_reserved_by_a_fallback_before_mutation():
    drawing = build_drawing(_multi_hole_plate(), page="A4")
    fallback_name = next(iter(_plan_bore_callouts(drawing).values()))[0]
    before = tuple(drawing.annotations())

    with pytest.raises(ValueError, match="unused table/balloon names"):
        drawing.add_hole_table("plan", name=fallback_name, replace_callouts=True)

    assert tuple(drawing.annotations()) == before
    assert drawing.get_annotation(fallback_name) is not None


def test_preexisting_balloon_name_cannot_spoof_semantic_commit():
    drawing = build_drawing(Box(60, 40, 10) - Cylinder(4, 20), page="A4")
    original = _plan_bore_callouts(drawing)
    note_name = drawing.note("EXISTING", (20, 20), view="plan", name="balloon_plan_A_0")
    note = drawing.get_annotation(note_name)

    with pytest.raises(ValueError, match="balloon_plan_A_0"):
        drawing.add_hole_table("plan", replace_callouts=True)

    assert drawing.get_annotation(note_name) is note
    assert all(
        drawing.get_annotation(name) is annotation for name, annotation in original.values()
    )
    assert "hole_table_plan" not in drawing.annotations()


def test_table_name_cannot_collide_with_a_balloon_from_the_same_attempt():
    drawing = build_drawing(Box(60, 40, 10) - Cylinder(4, 20), page="A4")
    before = _transaction_state(drawing)

    with pytest.raises(ValueError, match="must differ.*balloon_plan_A_0"):
        drawing.add_hole_table("plan", name="balloon_plan_A_0", replace_callouts=True)

    assert _transaction_state(drawing) == before


@pytest.mark.parametrize(
    ("decoration", "visible_token"),
    [(0.1, "±0.1"), (fit_class("H7", 8), "H7")],
    ids=["tolerance", "fit"],
)
def test_public_replacement_retains_toleranced_and_fitted_callouts(decoration, visible_token):
    part = Box(60, 40, 8) - Pos(0, 0, 4) * Cylinder(4, 8)
    detected = build_drawing(part, auto_dims=False).model()
    hole = next(feature for feature in detected.features if feature.kind == "hole")
    declared = replace(detected, decorations={(hole, "diameter"): decoration})
    drawing = build_drawing(part, page="A4", model=declared)
    callout_name, callout = _plan_bore_callouts(drawing)[hole]
    assert visible_token in callout.label

    table = drawing.add_hole_table("plan", replace_callouts=True)

    assert table is not None
    assert drawing.get_annotation(callout_name) is callout
    assert visible_token in drawing.get_annotation(callout_name).label
    assert {
        (outcome.representation, outcome.representation_reason)
        for outcome in _source_outcomes(drawing, hole)
    } == {(None, None)}


@pytest.mark.parametrize(
    "decoration",
    [0.1, fit_class("H7", 5.2)],
    ids=["tolerance", "fit"],
)
def test_automatic_table_cannot_resolve_a_dropped_decorated_callout(monkeypatch, decoration):
    import draftwright.annotations.orchestrator as orchestrator

    part = _dense_scattered_plate()
    with monkeypatch.context() as disabled:
        disabled.setattr(orchestrator, "_maybe_tabulate_holes", lambda *_args, **_kwargs: None)
        baseline = build_drawing(part)
    dropped_issue = next(
        issue
        for issue in baseline.registry.issues
        if issue.code == "callout_dropped" and issue.measurement_ids
    )
    dropped_feature = dropped_issue.measurement_ids[0].feature
    declared = replace(baseline.model(), decorations={(dropped_feature, "diameter"): decoration})

    drawing = build_drawing(part, model=declared)

    assert "hole_table_plan" in drawing.annotations()
    assert any(
        issue.code == "callout_dropped"
        and any(measurement.feature == dropped_feature for measurement in issue.measurement_ids)
        for issue in drawing.registry.issues
    )
    assert {
        (outcome.representation, outcome.representation_reason)
        for outcome in _source_outcomes(drawing, dropped_feature)
    } == {(None, None)}


def test_automatic_table_cannot_resolve_a_dropped_thread_callout(monkeypatch):
    import draftwright.annotations.orchestrator as orchestrator

    part = _dense_scattered_plate()
    with monkeypatch.context() as disabled:
        disabled.setattr(orchestrator, "_maybe_tabulate_holes", lambda *_args, **_kwargs: None)
        baseline = build_drawing(part)
    dropped_issue = next(
        issue
        for issue in baseline.registry.issues
        if issue.code == "callout_dropped" and issue.measurement_ids
    )
    dropped_feature = dropped_issue.measurement_ids[0].feature
    threaded = replace(dropped_feature, thread="M5x0.8")
    declared = replace(
        baseline.model(),
        features=[
            threaded if feature is dropped_feature else feature
            for feature in baseline.model().features
        ],
    )

    drawing = build_drawing(part, model=declared)

    assert "hole_table_plan" in drawing.annotations()
    assert any(
        issue.code == "callout_dropped"
        and any(measurement.feature == threaded for measurement in issue.measurement_ids)
        for issue in drawing.registry.issues
    )
    assert {
        (outcome.representation, outcome.representation_reason)
        for outcome in _source_outcomes(drawing, threaded)
    } == {(None, None)}


def test_public_replacement_retains_a_compound_callout_the_table_cannot_cover():
    part = Box(60, 40, 20) - Cylinder(4, 30) - Pos(0, 0, 2) * Cylinder(7, 20)
    drawing = build_drawing(part, page="A4")
    feature = next(feature for feature in drawing.model().features if feature.kind == "hole")
    assert feature.cbore is not None
    callout_name, callout = _plan_bore_callouts(drawing)[feature]

    table = drawing.add_hole_table("plan", replace_callouts=True)

    assert table is not None
    assert drawing.get_annotation(callout_name) is callout
    assert {
        measurement.parameter for measurement in drawing.registry.measurement_of(callout_name)
    } == {
        "bore.diameter",
        "counterbore.diameter",
        "counterbore.depth",
    }
    assert {
        measurement.parameter for measurement in drawing.registry.measurement_of("hole_table_plan")
    } == {"bore.diameter"}
    assert all(outcome.state == "placed" for outcome in _outcomes(drawing))
    assert feature not in {
        owner for owner, _representation, _reason in table.covers_hole_representations_by_feature
    }
    assert {
        (outcome.representation, outcome.representation_reason)
        for outcome in _source_outcomes(drawing, feature)
    } == {(None, None)}
    assert not {"feature_not_dimensioned", "hole_requirement_missing"} & {
        issue.code for issue in drawing.lint()
    }


def test_public_replacement_retains_a_declared_thread_callout():
    part = Box(60, 40, 10) - Cylinder(4, 20)
    detected = build_drawing(part, auto_dims=False).model()
    hole = next(feature for feature in detected.features if feature.kind == "hole")
    threaded = replace(hole, thread="M8x1")
    declared = replace(
        detected,
        features=[threaded if feature is hole else feature for feature in detected.features],
    )
    drawing = build_drawing(part, page="A4", model=declared)
    callout_name, callout = _plan_bore_callouts(drawing)[threaded]
    assert "M8x1" in callout.label

    table = drawing.add_hole_table("plan", replace_callouts=True)

    assert table is not None
    assert drawing.get_annotation(callout_name) is callout
    assert all(outcome.state == "placed" for outcome in _outcomes(drawing))


def test_public_replacement_retains_a_double_d_profile_callout():
    drawing = build_drawing(_double_d_plate(), page="A4")
    feature = next(
        feature for feature in drawing.model().features if feature.profile == "double_d"
    )
    callout_name, callout = _plan_bore_callouts(drawing)[feature]
    assert "DOUBLE-D 7.2 A/F" in callout.label

    table = drawing.add_hole_table("plan", replace_callouts=True)

    assert table is not None
    assert drawing.get_annotation(callout_name) is callout
    assert {
        measurement.parameter for measurement in drawing.registry.measurement_of(callout_name)
    } == {"bore.diameter", "profile_across_flats.length"}
    assert feature not in {
        owner for owner, _representation, _reason in table.covers_hole_representations_by_feature
    }


def test_public_replacement_retains_a_pattern_callout_with_geometry_the_table_omits():
    part = Box(80, 40, 10)
    for x in (-20, 0, 20):
        part -= Pos(x, 0, 0) * Cylinder(3, 20)
    drawing = build_drawing(part, page="A4")
    pattern = next(feature for feature in drawing.model().features if feature.kind == "pattern")
    callout_name, callout = _plan_bore_callouts(drawing)[pattern]

    table = drawing.add_hole_table("plan", replace_callouts=True)

    assert table is not None
    assert drawing.get_annotation(callout_name) is callout
    assert any(
        outcome.parameter_id == "pitch.length" and outcome.state == "placed"
        for outcome in _outcomes(drawing)
    )


def test_automatic_table_never_removes_a_recognised_compound_callout():
    drawing = build_drawing(_dense_plate_with_counterbore())
    feature = next(
        feature
        for feature in drawing.model().features
        if feature.kind == "hole" and feature.cbore is not None
    )
    callouts = _plan_bore_callouts(drawing)

    assert "hole_table_plan" in drawing.annotations()
    assert feature in callouts
    callout_name, callout = callouts[feature]
    assert drawing.get_annotation(callout_name) is callout
    assert {
        measurement.parameter for measurement in drawing.registry.measurement_of(callout_name)
    } >= {
        "counterbore.diameter",
        "counterbore.depth",
    }
    outcome_states = {outcome.parameter_id: outcome.state for outcome in _outcomes(drawing)}
    assert outcome_states["counterbore.diameter"] == "placed"
    assert outcome_states["counterbore.depth"] == "placed"
    assert {
        (outcome.representation, outcome.representation_reason)
        for outcome in _source_outcomes(drawing, feature)
    } == {(None, None)}
    assert not {"feature_not_dimensioned", "hole_requirement_missing"} & {
        issue.code for issue in drawing.lint()
    }


def test_automatic_table_never_removes_a_recognised_double_d_callout():
    drawing = build_drawing(_dense_plate_with_double_d())
    feature = next(
        feature for feature in drawing.model().features if feature.profile == "double_d"
    )
    callout_name, callout = _plan_bore_callouts(drawing)[feature]

    assert "hole_table_plan" in drawing.annotations()
    assert drawing.get_annotation(callout_name) is callout
    assert "DOUBLE-D 3.6 A/F" in callout.label
    assert {
        measurement.parameter for measurement in drawing.registry.measurement_of(callout_name)
    } == {"bore.diameter", "profile_across_flats.length"}
    table = drawing.get_annotation("hole_table_plan")
    assert feature not in {
        owner for owner, _representation, _reason in table.covers_hole_representations_by_feature
    }
