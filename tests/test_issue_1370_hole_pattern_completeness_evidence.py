"""#1370 — hole-pattern completeness is independent and does not recount member holes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from _evidence_contract import (
    assert_deleted_generated_line_loses_code_credit,
    assert_every_boundary_is_supported,
    assert_missing_ir_feature_loses_adapter_credit,
    assert_missing_model_outcomes_fail_closed,
    assert_observer_fails_closed_without_build_or_recognition,
)
from build123d import Box, Cylinder, Pos

from draftwright.evaluation.step_analysis import (
    _default_observers,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-hole-patterns-v1.json"


def _grid_part():
    part = Box(120, 100, 12)
    for y in (-10, 10):
        for x in (-15, 0, 15):
            part -= Pos(x, y, 0) * Cylinder(3, 12)
    return part


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["hole-patterns"](_grid_part())
    assert len(observed) == 1, "fixture must produce one grid observation"
    return {fact.downstream[boundary] for fact in observed}


def test_versioned_pattern_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.1.0", 1)
    assert corpus.scope == ("hole-patterns",)
    assert sum(len(case.expected) for case in corpus.cases) == 5
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {"positive", "negative", "ambiguous", "compound", "topology-order-variant"} <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)


@pytest.mark.scheduled
def test_real_pattern_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    evaluation = evaluate_step_corpus(corpus)
    assert evaluation.detection.recall == 1.0
    assert evaluation.detection.false_positive_rate == 0.0
    assert evaluation.parameter_fidelity.score == 1.0
    assert evaluation.downstream_usefulness.score == 1.0
    assert evaluation.conformant_cases == evaluation.complete_cases == len(corpus.cases)
    variants = [case for case in evaluation.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_pattern_projection_owns_disjoint_groups_without_recounting_members() -> None:
    from build123d import import_step
    from quiddity import build_raw_recognition_result

    compound = import_step(CORPUS.parent / "pattern-topology-a.step")
    for part, hole_count, pattern_count in ((_grid_part(), 6, 1), (compound, 7, 2)):
        recognition = build_raw_recognition_result(part)
        assert len(recognition.holes) == hole_count
        assert len(recognition.hole_patterns) == pattern_count
        aggregate_holes = {id(hole) for hole in recognition.holes}
        allocated: set[int] = set()
        for pattern in recognition.hole_patterns:
            member_ids = {id(hole) for hole in pattern.holes}
            assert member_ids <= aggregate_holes
            assert not allocated & member_ids
            allocated.update(member_ids)
        assert allocated == aggregate_holes

    (fact,) = _default_observers()["hole-patterns"](_grid_part())
    assert fact.parameters == {
        "count": 6,
        "rows": 2,
        "cols": 3,
        "row_pitch": 20.0,
        "col_pitch": 15.0,
        "angle": 0.0,
        "center": (0.0, 0.0, 6.0),
    }
    assert not ({"diameter", "depth", "bottom"} & set(fact.parameters))


def test_every_pattern_boundary_is_observed_supported_on_the_real_public_path() -> None:
    assert_every_boundary_is_supported(
        "hole-patterns",
        _grid_part(),
        message="fixture must produce one grid observation",
    )


def test_removing_patterns_from_the_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    assert_missing_ir_feature_loses_adapter_credit(monkeypatch, "pattern", _states)


def test_a_boundary_with_missing_per_pattern_outcomes_fails_closed(monkeypatch) -> None:
    assert_missing_model_outcomes_fail_closed(monkeypatch, "_pattern_model_outcomes", _states)


def test_pattern_observer_fails_closed_when_build_or_recognition_is_unavailable(
    monkeypatch,
) -> None:
    assert_observer_fails_closed_without_build_or_recognition(
        monkeypatch, "hole-patterns", _grid_part
    )


def test_corrupting_public_pattern_declaration_loses_declaration_credit(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.pattern

    def wrong_arrangement(self, member, **kw):
        if kw["kind"] == "grid":
            row, col = kw["grid"]
            kw["grid"] = (row + 1.0, col)
        return original(self, member, **kw)

    monkeypatch.setattr(Sheet, "pattern", wrong_arrangement)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_pattern_lines_loses_generated_code_credit(monkeypatch) -> None:
    assert_deleted_generated_line_loses_code_credit(monkeypatch, " = sheet.pattern(", _states)


def test_removing_a_placed_grid_pitch_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_one_pitch(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("dim_pitch_"))
        drawing.remove(name)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_one_pitch)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_wrong_placed_grid_pitch_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_pitch_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        names = [name for name in drawing.annotations() if name.startswith("dim_pitch_")]
        assert names, "fixture must place pitch dimensions"
        for name in names:
            dimension = drawing.registry.named(name)
            assert dimension.label in {"20", "2× 15"}
            dimension.label = "9999 WRONG"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_pitch_ink)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_wrong_linear_pitch_on_compound_part_loses_its_drawing_credit(
    monkeypatch,
) -> None:
    from build123d import import_step

    import draftwright.builder as builder

    compound = import_step(CORPUS.parent / "pattern-topology-a.step")
    baseline = _default_observers()["hole-patterns"](compound)
    assert {fact.identity["kind"]: fact.downstream["drawing_consumer"] for fact in baseline} == {
        "grid": "supported",
        "linear": "supported",
    }
    original = builder.build_drawing

    def with_wrong_compound_nominals(*args, **kwargs):
        drawing = original(*args, **kwargs)
        changed: set[str] = set()
        for name, annotation in drawing.registry.iter_named():
            parameters = {
                str(getattr(measurement, "parameter", ""))
                for measurement in drawing.registry.measurement_of(name)
            }
            if "pitch.length" in parameters:
                assert annotation.label == "2× 18"
                annotation.label = "2× 19"
                changed.add("linear")
        assert changed == {"linear"}
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_compound_nominals)
    observed = _default_observers()["hole-patterns"](compound)
    assert len(observed) == 2
    assert {fact.downstream["ir_adapter"] for fact in observed} == {"supported"}
    by_kind = {fact.identity["kind"]: fact.downstream["drawing_consumer"] for fact in observed}
    assert by_kind == {"grid": "supported", "linear": "unsupported"}


def test_wrong_placed_grid_interval_count_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_interval_count(*args, **kwargs):
        drawing = original(*args, **kwargs)
        pitches = [
            drawing.registry.named(name)
            for name in drawing.annotations()
            if name.startswith("dim_pitch_")
        ]
        assert {pitch.label for pitch in pitches} == {"20", "2× 15"}
        single_gap = next(pitch for pitch in pitches if pitch.label == "20")
        single_gap.label = "9× 20"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_interval_count)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_wrong_placed_group_count_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_group_count(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("hc_"))
        callout = drawing.registry.named(name)
        assert callout.covers_count == 6
        assert callout.label.startswith("6× ")
        callout.label = callout.label.replace("6× ", "5× ", 1)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_group_count)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_deleting_provider_patterns_cannot_shrink_the_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def without_patterns(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, hole_patterns=())

    monkeypatch.setattr(analysis, "_result_from_evidence", without_patterns)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 5
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


def test_weakening_provider_arrangement_values_reduces_parameter_fidelity(monkeypatch) -> None:
    from quiddity import RectangularHoleSet

    import draftwright.analysis as analysis

    baseline = evaluate_step_corpus(load_corpus(CORPUS))
    assert baseline.detection.matched == 5
    assert baseline.parameter_fidelity.score == 1.0
    original = analysis._result_from_evidence

    def weakened_patterns(*args, **kwargs):
        result = original(*args, **kwargs)
        changed = []
        for pattern in result.hole_patterns:
            if hasattr(pattern, "pitch"):
                changed.append(replace(pattern, pitch=pattern.pitch + 1.0))
            elif isinstance(pattern, RectangularHoleSet):
                changed.append(replace(pattern, width=pattern.width + 1.0))
            elif hasattr(pattern, "row_pitch"):
                changed.append(replace(pattern, row_pitch=pattern.row_pitch + 1.0))
            else:
                changed.append(replace(pattern, diameter=pattern.diameter + 1.0))
        return replace(result, hole_patterns=tuple(changed))

    monkeypatch.setattr(analysis, "_result_from_evidence", weakened_patterns)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection == baseline.detection
    assert damaged.parameter_fidelity.total == baseline.parameter_fidelity.total
    assert damaged.parameter_fidelity.score < baseline.parameter_fidelity.score
