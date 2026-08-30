"""#1371 — flat completeness is independent, grouped by physical A/F requirement."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from build123d import Box, Cylinder, Pos, import_step

from draftwright.evaluation.step_analysis import (
    _default_observers,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-flats-v1.json"


def _double_d():
    lobe = Cylinder(15, 40) - Pos(12.5, 0, 0) * Box(10, 40, 50)
    return lobe - Pos(-12.5, 0, 0) * Box(10, 40, 50)


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["flats"](_double_d())
    assert len(observed) == 1, "fixture must produce one grouped Double-D requirement"
    return {fact.downstream[boundary] for fact in observed}


def test_versioned_flat_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("flats",)
    assert len(corpus.cases) == 7
    assert sum(len(case.expected) for case in corpus.cases) == 9
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "ambiguous",
        "compound",
        "grouped",
        "multiple-equal",
        "negative",
        "positive",
        "rotated",
        "slanted",
        "topology-order-variant",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)


def test_real_flat_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 9
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 27
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 36
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_flat_projection_groups_faces_but_not_distinct_stock() -> None:
    from b123d_recognisers import build_raw_recognition_result

    fixtures = CORPUS.parent
    double_d = import_step(fixtures / "flat-double-d.step")
    parallel = import_step(fixtures / "flat-topology-a.step")
    coaxial = import_step(fixtures / "flat-coaxial.step")

    assert len(build_raw_recognition_result(double_d).flats) == 2
    (grouped,) = _default_observers()["flats"](double_d)
    assert grouped.parameters == {
        "across": 15.0,
        "face_count": 2,
        "anchors": (-7.5, 0.0, 0.0, 7.5, 0.0, 0.0),
    }

    parallel_facts = _default_observers()["flats"](parallel)
    coaxial_facts = _default_observers()["flats"](coaxial)
    assert len(parallel_facts) == len(coaxial_facts) == 2
    assert {fact.identity["axis_line"] for fact in parallel_facts} == {
        (-50.0, 0.0),
        (50.0, 0.0),
    }
    assert {fact.identity["stock_span"] for fact in coaxial_facts} == {
        (-20.0, 20.0),
        (60.0, 100.0),
    }


def test_every_flat_boundary_is_observed_supported_on_the_real_public_path() -> None:
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert _states(boundary) == {"supported"}


def test_flat_observer_uses_one_build_owned_recognition_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", counted)
    assert _default_observers()["flats"](_double_d())
    assert calls == 1


def test_removing_flats_from_the_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_flats(self):
        model = original(self)
        return replace(
            model,
            features=[feature for feature in model.features if feature.kind != "flat"],
        )

    monkeypatch.setattr(Drawing, "model", without_flats)
    assert _states("ir_adapter") == {"unknown"}


def test_a_boundary_with_missing_per_requirement_outcomes_fails_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_flat_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_flat_observer_fails_closed_when_build_or_recognition_is_unavailable(monkeypatch) -> None:
    import draftwright.builder as builder

    def broken_build(*_args, **_kwargs):
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(builder, "build_drawing", broken_build)
    observer = _default_observers()["flats"]
    assert observer(_double_d()) == ()

    class DrawingWithoutRecognition:
        def recognition(self):
            return None

    monkeypatch.setattr(
        builder, "build_drawing", lambda *_args, **_kwargs: DrawingWithoutRecognition()
    )
    assert observer(_double_d()) == ()


def test_corrupting_public_flat_declaration_loses_declaration_credit(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.flat

    def wrong_stock(self, obj=None, **kw):
        first, second = kw["axis_line"]
        kw["axis_line"] = (first + 1.0, second)
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "flat", wrong_stock)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_flat_lines_loses_generated_code_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_flat_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if "sheet.flat(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_flat_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


def test_removing_the_placed_flat_callout_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_callout(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_flat_"))
        drawing.remove(name)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_callout)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_wrong_flat_nominal_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_flat_"))
        callout = drawing.registry.named(name)
        assert callout.label == "15 A/F"
        callout.label = "16 A/F"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_ink)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_severing_flat_measurement_provenance_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_provenance(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_flat_"))
        identity = drawing.registry.identity_of(name)
        assert len(identity["measurement"]) == 2
        identity["measurement"] = ()
        drawing.registry.reapply(name, identity)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_provenance)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_deleting_provider_flats_cannot_shrink_the_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def without_flats(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, flats=())

    monkeypatch.setattr(analysis, "build_raw_recognition_result", without_flats)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 9
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


def test_weakening_provider_across_values_reduces_parameter_fidelity(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def weakened_flats(*args, **kwargs):
        result = original(*args, **kwargs)
        flats = tuple(replace(flat, across=flat.across + 1.0) for flat in result.flats)
        return replace(result, flats=flats)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", weakened_flats)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.passed == 18
    assert damaged.parameter_fidelity.total == 27
    assert damaged.parameter_fidelity.score == 2 / 3
