"""#1370 — hole-pattern completeness is independent and does not recount member holes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("hole-patterns",)
    assert sum(len(case.expected) for case in corpus.cases) == 5
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {"positive", "negative", "ambiguous", "compound", "topology-order-variant"} <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)


def test_real_pattern_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.parameter_fidelity.score == 1.0
    assert first.downstream_usefulness.score == 1.0
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_pattern_projection_owns_disjoint_groups_without_recounting_members() -> None:
    from b123d_recognisers import build_raw_recognition_result
    from build123d import import_step

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
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert _states(boundary) == {"supported"}


def test_removing_patterns_from_the_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_patterns(self):
        model = original(self)
        return replace(
            model,
            features=[feature for feature in model.features if feature.kind != "pattern"],
        )

    monkeypatch.setattr(Drawing, "model", without_patterns)
    assert _states("ir_adapter") == {"unknown"}


def test_a_boundary_with_missing_per_pattern_outcomes_fails_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_pattern_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_pattern_observer_fails_closed_when_build_or_recognition_is_unavailable(
    monkeypatch,
) -> None:
    import draftwright.builder as builder

    def broken_build(*_args, **_kwargs):
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(builder, "build_drawing", broken_build)
    observer = _default_observers()["hole-patterns"]
    assert observer(_grid_part()) == ()

    class DrawingWithoutRecognition:
        def recognition(self):
            return None

    monkeypatch.setattr(
        builder, "build_drawing", lambda *_args, **_kwargs: DrawingWithoutRecognition()
    )
    assert observer(_grid_part()) == ()


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
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_pattern_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if " = sheet.pattern(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_pattern_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


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
            prefix, _nominal = dimension.label.split(" ", 1)
            assert prefix.endswith("×")
            dimension.label = f"{prefix} 9999 WRONG"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_pitch_ink)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_wrong_linear_pitch_and_bcd_nominals_lose_only_their_drawing_credit(
    monkeypatch,
) -> None:
    from build123d import import_step

    import draftwright.builder as builder

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
            if "bolt_circle.diameter" in parameters:
                assert "4× " in annotation.label and "ø32 BC" in annotation.label
                annotation.label = annotation.label.replace("ø32 BC", "ø33 BC")
                changed.add("bolt_circle")
        assert changed == {"linear", "bolt_circle"}
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_compound_nominals)
    compound = import_step(CORPUS.parent / "pattern-topology-a.step")
    observed = _default_observers()["hole-patterns"](compound)
    assert len(observed) == 2
    assert {fact.downstream["ir_adapter"] for fact in observed} == {"supported"}
    assert {fact.downstream["drawing_consumer"] for fact in observed} == {"unsupported"}


def test_wrong_placed_grid_interval_count_loses_drawing_credit(monkeypatch) -> None:
    import re

    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_interval_count(*args, **kwargs):
        drawing = original(*args, **kwargs)
        names = [name for name in drawing.annotations() if name.startswith("dim_pitch_")]
        assert names, "fixture must place pitch dimensions"
        for name in names:
            dimension = drawing.registry.named(name)
            assert re.match(r"^\d+× ", dimension.label)
            dimension.label = re.sub(r"^\d+× ", "9× ", dimension.label)
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
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def weakened_patterns(*args, **kwargs):
        result = original(*args, **kwargs)
        changed = []
        for pattern in result.hole_patterns:
            if hasattr(pattern, "pitch"):
                changed.append(replace(pattern, pitch=pattern.pitch + 1.0))
            elif hasattr(pattern, "row_pitch"):
                changed.append(replace(pattern, row_pitch=pattern.row_pitch + 1.0))
            else:
                changed.append(replace(pattern, diameter=pattern.diameter + 1.0))
        return replace(result, hole_patterns=tuple(changed))

    monkeypatch.setattr(analysis, "_result_from_evidence", weakened_patterns)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.passed == 14
    assert damaged.parameter_fidelity.total == 19
    assert damaged.parameter_fidelity.score == 14 / 19
