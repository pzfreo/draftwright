"""#1369 — hole completeness is earned by independent, observed evidence.

The corpus owns the physical denominator.  These mutations sever the production seams the
observer claims to inspect; a static capability declaration would stay green under every one.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from build123d import Box, Cylinder, Pos

from draftwright.evaluation.step_analysis import (
    _default_observers,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-v1.json"


def _part():
    return Box(60, 30, 12) - Pos(-15, 0, 0) * Cylinder(3, 40) - Pos(15, 0, 0) * Cylinder(5, 40)


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["holes"](_part())
    assert observed, "fixture produced no hole observations"
    return {fact.downstream[boundary] for fact in observed}


def test_every_hole_boundary_is_observed_supported_on_the_real_public_path() -> None:
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert _states(boundary) == {"supported"}


def test_removing_holes_from_the_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_holes(self):
        model = original(self)
        return replace(
            model,
            features=[
                feature for feature in model.features if feature.kind not in {"hole", "pattern"}
            ],
        )

    monkeypatch.setattr(Drawing, "model", without_holes)
    assert _states("ir_adapter") == {"unknown"}


def test_corrupting_the_public_sheet_declaration_loses_declaration_credit(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.hole

    def wrong_diameter(self, obj=None, **kw):
        kw["diameter"] += 1.0
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "hole", wrong_diameter)
    assert _states("ir_adapter") == {"supported"}, "the automatic adapter remains intact"
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_hole_lines_loses_generated_code_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_hole_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if " = sheet.hole(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_hole_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


def test_a_boundary_with_missing_per_hole_outcomes_fails_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_hole_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_a_post_build_model_access_failure_is_scored_not_raised(monkeypatch) -> None:
    import draftwright.builder as builder

    drawing = builder.build_drawing(_part())

    class ModelUnavailable:
        def __getattr__(self, name):
            return getattr(drawing, name)

        def model(self):
            raise RuntimeError("public model unavailable")

    monkeypatch.setattr(builder, "build_drawing", lambda *_args, **_kwargs: ModelUnavailable())
    observed = _default_observers()["holes"](_part())

    assert observed, "the successful build's recognition still supplies observed facts"
    assert {fact.downstream["dsl_declaration"] for fact in observed} == {"supported"}
    for boundary in ("ir_adapter", "generated_code", "drawing_consumer"):
        assert {fact.downstream[boundary] for fact in observed} == {"unknown"}


def test_a_missing_build_owned_recognition_result_does_not_rescan(monkeypatch) -> None:
    import b123d_recognisers

    import draftwright.builder as builder

    drawing = builder.build_drawing(_part())

    class RecognitionUnavailable:
        def __getattr__(self, name):
            return getattr(drawing, name)

        def recognition(self):
            return None

    provider_calls = []

    def forbidden_rescan(*args, **kwargs):
        provider_calls.append((args, kwargs))
        raise AssertionError("a consumer must not replace the build-owned aggregate")

    monkeypatch.setattr(
        builder, "build_drawing", lambda *_args, **_kwargs: RecognitionUnavailable()
    )
    monkeypatch.setattr(b123d_recognisers, "build_raw_recognition_result", forbidden_rescan)

    assert _default_observers()["holes"](_part()) == ()
    assert provider_calls == []


def test_deleting_provider_holes_cannot_shrink_the_independent_denominator(monkeypatch) -> None:
    """The targeted anti-self-validation mutation required by #1369.

    The automatic model, plan and completeness ledger are all rebuilt from the weakened
    aggregate, so they remain internally consistent.  The hand-authored corpus still expects
    five holes and therefore records five misses.
    """
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def without_holes(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, holes=(), hole_patterns=())

    monkeypatch.setattr(analysis, "_result_from_evidence", without_holes)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 5
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)
