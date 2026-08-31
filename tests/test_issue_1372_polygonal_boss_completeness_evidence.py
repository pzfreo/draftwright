"""#1372 — polygonal-boss completeness uses independent physical facts."""

from __future__ import annotations

from dataclasses import replace
from math import cos, pi, sin
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Align, Box, Pos, RegularPolygon, Rot, extrude, import_step

from draftwright.evaluation.step_analysis import (
    _default_observers,
    _polygonal_boss_drawing_outcomes,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-polygonal-bosses-v1.json"
_CENTER = (Align.CENTER, Align.CENTER, Align.CENTER)


def _boss_part():
    stock = Box(100, 80, 10, align=_CENTER)
    boss = Pos(13, -7, 5) * Rot(0, 0, 11) * extrude(RegularPolygon(20, 6), 30)
    return stock + boss


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["polygonal-bosses"](_boss_part())
    assert len(observed) == 1
    return {fact.downstream[boundary] for fact in observed}


def _annotation_for_parameter(drawing, parameter: str) -> str:
    return next(
        name
        for name in drawing.annotations()
        if any(
            identity.parameter == parameter for identity in drawing.registry.measurement_of(name)
        )
    )


def test_versioned_polygonal_boss_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("polygonal-bosses",)
    assert len(corpus.cases) == 11
    assert sum(len(case.expected) for case in corpus.cases) == 10
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "ambiguous",
        "compound",
        "in-plane-rotation",
        "multiple",
        "multiple-equal",
        "negative",
        "overlapping-family",
        "positive",
        "principal-orientation",
        "topology-order-variant",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    assert all(
        "'1970-01-01T00:00:00'"
        in (CORPUS.parent / case.provenance["fixture"]).read_text().splitlines()[3]
        for case in corpus.cases
    )


def test_real_polygonal_boss_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 10
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 40
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 40
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


@pytest.mark.parametrize("axis", tuple("xyz"))
def test_every_principal_polygonal_boss_boundary_is_observed(axis) -> None:
    part = import_step(CORPUS.parent / f"polygonal-boss-{axis}.step")
    observed = _default_observers()["polygonal-bosses"](part)

    assert len(observed) == 1
    assert observed[0].identity["axis"] == axis
    assert set(observed[0].downstream.values()) == {"supported"}


def test_arbitrary_rigid_motion_survives_the_owned_framed_pipeline() -> None:
    from draftwright import build_drawing
    from draftwright.linting.polygonal_boss_coverage import (
        polygonal_boss_requirement_outcomes,
    )
    from draftwright.model.compiled import compile_dimensions

    baseline = build_drawing(_boss_part())
    moved = build_drawing(
        Pos(91, -37, 48) * Rot(31, 47, 13) * _boss_part(), framed_recognition=True
    )

    def requirements(drawing):
        recognition = drawing.recognition()
        assert recognition is not None
        outcomes = polygonal_boss_requirement_outcomes(
            recognition,
            drawing.model().features,
            drawing.registry,
            compile_dimensions(drawing.model()).diagnostics,
        )
        feature = next(item for item in drawing.model().features if item.kind == "polygonal_boss")
        return (
            feature.side_count,
            round(feature.across_flats, 3),
            round(feature.height, 3),
            {(outcome.parameter_id, outcome.state) for outcome in outcomes},
        )

    assert moved.recognition_frame_decision["status"] == "framed"
    assert requirements(moved) == requirements(baseline)
    assert _polygonal_boss_drawing_outcomes(
        tuple(moved.recognition().polygonal_bosses), moved
    ) == ["supported"]


def test_polygonal_boss_ledger_tracks_two_requirements_and_fails_closed() -> None:
    from draftwright import build_drawing
    from draftwright.linting.polygonal_boss_coverage import (
        polygonal_boss_requirement_outcomes,
    )
    from draftwright.model.compiled import compile_dimensions
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_boss_part())
    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = polygonal_boss_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )

    assert {outcome.parameter_id for outcome in outcomes} == {
        "polygon_across_flats.length",
        "boss_height.length",
    }
    assert {outcome.state for outcome in outcomes} == {"placed"}
    missing = polygonal_boss_requirement_outcomes(
        recognition, drawing.model().features, AnnotationRegistry()
    )
    assert len(missing) == 2
    assert {outcome.state for outcome in missing} == {"missing"}
    unverifiable = polygonal_boss_requirement_outcomes(
        recognition,
        [feature for feature in drawing.model().features if feature.kind != "polygonal_boss"],
        AnnotationRegistry(),
    )
    assert len(unverifiable) == 1
    assert (unverifiable[0].state, unverifiable[0].requirement_count) == (
        "unverifiable",
        2,
    )


def test_polygonal_boss_ledger_rejects_foreign_malformed_and_duplicate_ir() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing
    from draftwright.linting.polygonal_boss_coverage import (
        polygonal_boss_requirement_outcomes,
    )
    from draftwright.registry import AnnotationRegistry

    recognition = build_raw_recognition_result(_boss_part(), rotational=False)
    source = recognition.polygonal_bosses[0]

    class MalformedBoss:
        kind = "polygonal_boss"
        frame = SimpleNamespace(axis=source.axis, origin=source.center)
        side_count = source.side_count
        across_flats = source.across_flats
        height = source.height
        span = ((0, 0, source.base), (0, 0, source.top))
        flat_directions = source.flat_directions
        flat_centres = source.flat_centres

        @staticmethod
        def parameters():
            raise TypeError("broken parameter contract")

    assert polygonal_boss_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="run's RecognitionResult"):
        polygonal_boss_requirement_outcomes(object(), (), AnnotationRegistry())
    malformed = polygonal_boss_requirement_outcomes(
        recognition, (MalformedBoss(),), AnnotationRegistry()
    )
    assert len(malformed) == 1
    assert (malformed[0].state, malformed[0].requirement_count) == ("unverifiable", 2)

    drawing = build_drawing(_boss_part())
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_boss")
    duplicate = polygonal_boss_requirement_outcomes(
        recognition, (feature, feature), AnnotationRegistry()
    )
    assert len(duplicate) == 1
    assert (duplicate[0].state, duplicate[0].requirement_count) == ("unverifiable", 2)


@pytest.mark.parametrize(
    "corruption",
    ("raises", "parameter_ids", "parameter_values", "af_span", "height_span"),
)
def test_polygonal_boss_ledger_rejects_malformed_parameter_contract(corruption) -> None:
    from draftwright import build_drawing
    from draftwright.linting.polygonal_boss_coverage import (
        polygonal_boss_requirement_outcomes,
    )
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_boss_part())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_boss")
    parameters = list(feature.parameters())
    if corruption == "parameter_ids":
        parameters[0] = replace(parameters[0], role="wrong_across_flats")
    elif corruption == "parameter_values":
        parameters[0] = replace(parameters[0], value=parameters[0].value + 1.0)
    elif corruption == "af_span":
        parameters[0] = replace(parameters[0], span=feature.span)
    elif corruption == "height_span":
        parameters[1] = replace(parameters[1], span=tuple(reversed(feature.span)))

    class ParameterProxy:
        kind = "polygonal_boss"

        def __getattr__(self, name):
            return getattr(feature, name)

        def parameters(self):
            if corruption == "raises":
                raise ValueError("invalid parameter contract")
            return parameters

    outcomes = polygonal_boss_requirement_outcomes(
        recognition, (ParameterProxy(),), AnnotationRegistry()
    )
    assert len(outcomes) == 1
    assert (outcomes[0].state, outcomes[0].requirement_count) == ("unverifiable", 2)


def test_polygonal_boss_ledger_distinguishes_suppressed_dropped_and_missing() -> None:
    from draftwright import build_drawing
    from draftwright.linting.issues import LintIssue
    from draftwright.linting.polygonal_boss_coverage import (
        polygonal_boss_requirement_outcomes,
    )
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_boss_part())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_boss")
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="polygonal_boss_dropped",
            measurement_ids=(DimensionId(feature, "polygon_across_flats.length"),),
            outcome_stage="placement",
        )
    )
    omissions = (
        SimpleNamespace(
            feature=feature,
            parameter_id="boss_height.length",
            authored=True,
        ),
    )

    states = {
        outcome.parameter_id: outcome.state
        for outcome in polygonal_boss_requirement_outcomes(
            recognition, drawing.model().features, registry, omissions
        )
    }
    assert states == {
        "polygon_across_flats.length": "dropped",
        "boss_height.length": "suppressed",
    }


def test_polygonal_boss_structured_note_satisfaction_is_not_ink() -> None:
    from draftwright import build_drawing
    from draftwright.linting.polygonal_boss_coverage import (
        polygonal_boss_requirement_outcomes,
    )
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_boss_part())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_boss")
    registry = AnnotationRegistry()
    registry.add(
        SimpleNamespace(),
        "boss_height_note",
        "front",
        feature=feature,
        satisfaction=DimensionId(feature, "boss_height.length"),
    )

    states = {
        outcome.parameter_id: outcome.state
        for outcome in polygonal_boss_requirement_outcomes(
            recognition, drawing.model().features, registry
        )
    }
    assert states["boss_height.length"] == "satisfied_by_structured_note"
    assert states["polygon_across_flats.length"] == "missing"


def test_polygonal_boss_coverage_does_not_duplicate_a_placement_drop() -> None:
    from draftwright import build_drawing
    from draftwright.linting.issues import LintIssue
    from draftwright.linting.polygonal_boss_coverage import lint_polygonal_boss_coverage
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_boss_part())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "polygonal_boss")
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="polygonal_boss_dropped",
            measurement_ids=(DimensionId(feature, "polygon_across_flats.length"),),
            outcome_stage="placement",
        )
    )

    issues = lint_polygonal_boss_coverage(
        drawing.part,
        recognition=recognition,
        features=drawing.model().features,
        registry=registry,
    )
    assert not [issue for issue in issues if issue.code.endswith("_dropped")]
    assert [issue.code for issue in issues] == ["polygonal_boss_requirement_missing"]


def test_polygonal_boss_observer_uses_one_build_owned_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", counted)
    assert _default_observers()["polygonal-bosses"](_boss_part())
    assert calls == 1


def test_removing_polygonal_bosses_from_built_ir_loses_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_bosses(self):
        model = original(self)
        return replace(
            model,
            features=[feature for feature in model.features if feature.kind != "polygonal_boss"],
        )

    monkeypatch.setattr(Drawing, "model", without_bosses)
    assert _states("ir_adapter") == {"unknown"}


def test_observer_failure_cannot_pass_a_zero_boss_negative_case(monkeypatch) -> None:
    import draftwright.builder as builder

    corpus = load_corpus(CORPUS)
    negative = next(case for case in corpus.cases if not case.expected)

    def failed_build(*_args, **_kwargs):
        raise RuntimeError("negative-case probe")

    monkeypatch.setattr(builder, "build_drawing", failed_build)
    damaged = evaluate_step_corpus(replace(corpus, cases=(negative,)))

    assert damaged.complete_cases == damaged.conformant_cases == 0
    assert damaged.cases[0].outcome == "unknown"
    assert [(issue.layer, issue.family) for issue in damaged.cases[0].diagnostics] == [
        ("analysis", "polygonal-bosses")
    ]


def test_corrupting_public_polygonal_boss_declaration_loses_declaration_credit(
    monkeypatch,
) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.polygonal_boss

    def wrong_height(self, **kw):
        kw["height"] = float(kw["height"]) + 1.0
        span = list(kw["span"])
        endpoint = list(span[1])
        endpoint["xyz".index(str(kw["axis"]))] += 1.0
        span[1] = tuple(endpoint)
        kw["span"] = tuple(span)
        return original(self, **kw)

    monkeypatch.setattr(Sheet, "polygonal_boss", wrong_height)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_polygonal_boss_lines_loses_generated_code_credit(
    monkeypatch,
) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_boss_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if "sheet.polygonal_boss(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_boss_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


@pytest.mark.parametrize("parameter", ("polygon_across_flats.length", "boss_height.length"))
def test_wrong_polygonal_boss_ink_loses_drawing_credit(monkeypatch, parameter) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = _annotation_for_parameter(drawing, parameter)
        drawing.registry.named(name).label = "999"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_ink)
    assert _states("drawing_consumer") == {"unsupported"}


def test_moving_polygonal_boss_leader_off_its_flat_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_moved_tip(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = _annotation_for_parameter(drawing, "polygon_across_flats.length")
        annotation = drawing.registry.named(name)
        annotation._tip_local = (annotation.tip[0] + 7.0, annotation.tip[1] + 9.0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_moved_tip)
    assert _states("drawing_consumer") == {"unsupported"}


def test_severing_one_polygonal_boss_claim_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_claim(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = _annotation_for_parameter(drawing, "boss_height.length")
        identity = drawing.registry.identity_of(name)
        drawing.registry.reapply(name, {**identity, "measurement": ()})
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_claim)
    assert _states("drawing_consumer") == {"unsupported"}


def test_deleting_provider_bosses_cannot_shrink_the_independent_denominator(
    monkeypatch,
) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def without_bosses(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, polygonal_bosses=())

    monkeypatch.setattr(analysis, "build_raw_recognition_result", without_bosses)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 10
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


@pytest.mark.parametrize(
    "parameter",
    ("side_count", "across_flats", "height", "flat_supports"),
)
def test_weakening_provider_parameters_reduces_parameter_fidelity(monkeypatch, parameter) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def weakened(*args, **kwargs):
        result = original(*args, **kwargs)
        values = []
        for boss in result.polygonal_bosses:
            if parameter == "side_count":
                plane = [index for index, name in enumerate("xyz") if name != boss.axis]
                directions = []
                centres = []
                for index in range(8):
                    direction = [0.0, 0.0, 0.0]
                    angle = 2 * pi * index / 8
                    direction[plane[0]] = cos(angle)
                    direction[plane[1]] = sin(angle)
                    centre = [float(component) for component in boss.center]
                    for component in plane:
                        centre[component] += direction[component] * boss.across_flats / 2
                    directions.append(tuple(direction))
                    centres.append(tuple(centre))
                values.append(
                    replace(
                        boss,
                        side_count=8,
                        flat_directions=tuple(directions),
                        flat_centres=tuple(centres),
                    )
                )
            elif parameter == "across_flats":
                values.append(replace(boss, across_flats=boss.across_flats + 0.1))
            elif parameter == "height":
                values.append(replace(boss, base=boss.base - 0.05, top=boss.top + 0.05))
            elif parameter == "flat_supports":
                plane = [index for index, name in enumerate("xyz") if name != boss.axis]
                delta = 0.1
                directions = []
                centres = []
                for old_direction, old_centre in zip(
                    boss.flat_directions, boss.flat_centres, strict=True
                ):
                    direction = list(old_direction)
                    first = direction[plane[0]]
                    second = direction[plane[1]]
                    direction[plane[0]] = first * cos(delta) - second * sin(delta)
                    direction[plane[1]] = first * sin(delta) + second * cos(delta)
                    offset = [
                        float(old_centre[index]) - float(boss.center[index]) for index in range(3)
                    ]
                    centre = [float(component) for component in boss.center]
                    centre[plane[0]] += offset[plane[0]] * cos(delta) - offset[plane[1]] * sin(
                        delta
                    )
                    centre[plane[1]] += offset[plane[0]] * sin(delta) + offset[plane[1]] * cos(
                        delta
                    )
                    directions.append(tuple(direction))
                    centres.append(tuple(centre))
                values.append(
                    replace(
                        boss,
                        flat_directions=tuple(directions),
                        flat_centres=tuple(centres),
                    )
                )
            else:
                raise AssertionError(parameter)
        return replace(result, polygonal_bosses=tuple(values))

    monkeypatch.setattr(analysis, "build_raw_recognition_result", weakened)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.total == 40
    assert damaged.parameter_fidelity.passed < damaged.parameter_fidelity.total


def test_shifting_provider_identity_reduces_detection_recall(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def shifted(*args, **kwargs):
        result = original(*args, **kwargs)
        values = []
        for boss in result.polygonal_bosses:
            center = list(boss.center)
            axis = next(candidate for candidate in "xyz" if candidate != boss.axis)
            index = "xyz".index(axis)
            center[index] += 1.0
            centres = [list(point) for point in boss.flat_centres]
            for point in centres:
                point[index] += 1.0
            values.append(
                replace(
                    boss,
                    center=tuple(center),
                    flat_centres=tuple(tuple(point) for point in centres),
                )
            )
        return replace(result, polygonal_bosses=tuple(values))

    monkeypatch.setattr(analysis, "build_raw_recognition_result", shifted)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 0.0
    assert damaged.detection.missed == 10
    assert damaged.detection.false_positives == 10


def test_deleting_declared_boss_cannot_shrink_quality_denominator() -> None:
    from draftwright import Sheet, build_drawing

    complete = build_drawing(_boss_part())
    sparse = Sheet(_boss_part())
    envelope = sparse.envelope()
    sparse.dimension(envelope, "width.length")

    complete_summary = complete.lint_summary()
    sparse_summary = sparse.build().lint_summary()
    complete_quality = complete_summary["quality"]["completeness"]
    sparse_quality = sparse_summary["quality"]["completeness"]
    assert complete_quality["requirements"] == sparse_quality["requirements"] == 2
    assert complete_quality["by_family"]["polygonal_bosses"] == 2
    assert complete_quality["placed"] == 2
    assert sparse_quality["unverifiable"] == 2
    assert complete_quality["audited_score"] == 1.0
    assert sparse_quality["audited_score"] == 0.0
    assert "polygonal_boss_requirement_unverifiable" in sparse_summary["by_code"]
