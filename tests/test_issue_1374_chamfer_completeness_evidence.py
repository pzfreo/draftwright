"""#1374 — chamfer completeness uses independent physical bevel facts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Axis, Box, chamfer, import_step

from draftwright.evaluation.step_analysis import (
    ObservationError,
    _default_observers,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-chamfers-v1.json"


def _lone():
    part = Box(60, 40, 30)
    edge = part.edges().filter_by(Axis.Z).sort_by(lambda item: item.center().X + item.center().Y)[
        -1
    ]
    return chamfer(edge, 6)


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["chamfers"](_lone())
    assert len(observed) == 1
    return {fact.downstream[boundary] for fact in observed}


def test_versioned_chamfer_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("chamfers",)
    assert len(corpus.cases) == 10
    assert sum(len(case.expected) for case in corpus.cases) == 12
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "ambiguous",
        "asymmetric",
        "compound",
        "multiple-equal",
        "negative",
        "overlapping-family",
        "positive",
        "rotated",
        "topology-order-variant",
        "turned",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)


def test_real_chamfer_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 12
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 36
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 48
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_public_framed_route_preserves_arbitrarily_rotated_planar_and_turned_bevels() -> None:
    from b123d_recognisers import FramedRecognitionResult, build_framed_recognition_result
    from build123d import Cylinder, GeomType, Rot

    shaft = Cylinder(15, 60)
    end = shaft.edges().filter_by(GeomType.CIRCLE).sort_by(lambda edge: edge.center().Z)[-1]
    fixtures = ((Rot(23, 37, 11) * _lone(), False, 6.0), (Rot(17, 31, 43) * chamfer(end, 3), True, 3.0))

    for part, turned, leg in fixtures:
        framed = build_framed_recognition_result(part)
        assert isinstance(framed, FramedRecognitionResult)
        assert len(framed.result.chamfers) == 1
        observed = framed.result.chamfers[0]
        assert (observed.leg1, observed.leg2, observed.angle, observed.turned) == (
            leg,
            leg,
            45.0,
            turned,
        )
        # The inferred local axis and circumferential anchor are gauge representatives. Their
        # world labels/roll are deliberately not manufacturing identity; only the preserved
        # physical bevel facts are asserted here.


def test_angled_step_slant_and_independent_chamfer_have_one_owner_each() -> None:
    from b123d_recognisers import (
        build_recognition_result,
        recognise_angled_steps,
        recognise_chamfers,
    )

    part = import_step(CORPUS.parent / "chamfer-overlap.step")
    direct_chamfers = recognise_chamfers(part)
    direct_steps = recognise_angled_steps(part)
    recognition = build_recognition_result(part)

    assert len(direct_chamfers) == 2
    assert len(direct_steps) == 1
    step = direct_steps[0]
    assert len(
        [
            item
            for item in direct_chamfers
            if (item.axis, item.leg1, item.leg2, item.angle, item.at)
            == (step.axis, step.leg1, step.leg2, step.angle, step.at)
        ]
    ) == 1
    assert len(recognition.chamfers) == 1
    assert len(recognition.angled_steps) == 1


def test_equal_compound_chamfers_share_ink_but_keep_both_measurement_identities() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(import_step(CORPUS.parent / "chamfer-compound.step"))
    names = [name for name in drawing.annotations() if name.startswith("m_chamfer_")]

    assert len(names) == 1
    assert drawing.registry.named(names[0]).label == "2× C6"
    assert len(drawing.registry.measurement_of(names[0])) == 2
    assert {
        measurement.parameter for measurement in drawing.registry.measurement_of(names[0])
    } == {"chamfer.length"}


def test_chamfer_ledger_tracks_one_callout_per_physical_bevel_and_fails_closed() -> None:
    from draftwright import build_drawing
    from draftwright.linting.chamfer_coverage import chamfer_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = chamfer_requirement_outcomes(
        recognition, drawing.model().features, drawing.registry
    )
    assert [(item.parameter_id, item.state) for item in outcomes] == [
        ("chamfer.length", "placed")
    ]
    assert chamfer_requirement_outcomes(
        recognition, drawing.model().features, AnnotationRegistry()
    )[0].state == "missing"
    assert chamfer_requirement_outcomes(
        recognition,
        [feature for feature in drawing.model().features if feature.kind != "chamfer"],
        AnnotationRegistry(),
    )[0].state == "unverifiable"


def test_chamfer_ledger_rejects_foreign_results_and_malformed_ir_without_guessing() -> None:
    from b123d_recognisers import build_recognition_result

    from draftwright.linting.chamfer_coverage import chamfer_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    recognition = build_recognition_result(_lone())
    source = recognition.chamfers[0]

    class MalformedChamfer:
        kind = "chamfer"
        axis = source.axis
        frame = type("Frame", (), {"origin": source.at})()
        turned = source.turned
        leg1 = source.leg1
        leg2 = source.leg2
        angle = source.angle

        @staticmethod
        def parameters():
            raise TypeError("broken parameter contract")

    assert chamfer_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="run's RecognitionResult"):
        chamfer_requirement_outcomes(object(), (), AnnotationRegistry())
    outcome = chamfer_requirement_outcomes(
        recognition, (MalformedChamfer(),), AnnotationRegistry()
    )[0]
    assert outcome.state == "unverifiable"


def test_chamfer_ledger_distinguishes_suppressed_dropped_and_orphan_evidence() -> None:
    from types import SimpleNamespace

    from draftwright import build_drawing
    from draftwright.linting.chamfer_coverage import chamfer_requirement_outcomes
    from draftwright.linting.issues import LintIssue
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "chamfer")

    omission = SimpleNamespace(
        feature=feature,
        parameter_id="chamfer.length",
        authored=True,
    )
    suppressed = chamfer_requirement_outcomes(
        recognition,
        drawing.model().features,
        AnnotationRegistry(),
        (omission,),
    )
    assert suppressed[0].state == "suppressed"

    dropped_registry = AnnotationRegistry()
    dropped_registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="chamfer_dropped",
            measurement_ids=(DimensionId(feature, "chamfer.length"),),
        )
    )
    dropped = chamfer_requirement_outcomes(
        recognition, drawing.model().features, dropped_registry
    )
    assert dropped[0].state == "dropped"

    orphan_registry = AnnotationRegistry()
    orphan_registry.add(object(), "orphan", "plan", feature=feature)
    orphan = chamfer_requirement_outcomes(
        recognition, drawing.model().features, orphan_registry
    )
    assert orphan[0].state == "unverifiable"


def test_every_chamfer_boundary_is_observed_supported_on_the_real_public_path() -> None:
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert _states(boundary) == {"supported"}


def test_chamfer_observer_uses_one_build_owned_recognition_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_recognition_result
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_recognition_result", counted)
    assert _default_observers()["chamfers"](_lone())
    assert calls == 1


def test_removing_chamfers_from_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_chamfers(self):
        model = original(self)
        return replace(model, features=[item for item in model.features if item.kind != "chamfer"])

    monkeypatch.setattr(Drawing, "model", without_chamfers)
    assert _states("ir_adapter") == {"unknown"}


def test_missing_per_chamfer_boundary_outcomes_fail_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_chamfer_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_observer_failure_cannot_pass_even_the_zero_chamfer_negative(monkeypatch) -> None:
    import draftwright.builder as builder

    corpus = load_corpus(CORPUS)
    negative = next(case for case in corpus.cases if not case.expected)

    def failed_build(*_args, **_kwargs):
        raise RuntimeError("negative-case probe")

    monkeypatch.setattr(builder, "build_drawing", failed_build)
    with pytest.raises(ObservationError, match="drawing build failed"):
        _default_observers()["chamfers"](Box(20, 20, 20))
    damaged = evaluate_step_corpus(replace(corpus, cases=(negative,)))
    assert damaged.complete_cases == damaged.conformant_cases == 0
    assert [(issue.layer, issue.family) for issue in damaged.cases[0].diagnostics] == [
        ("analysis", "chamfers")
    ]


def test_corrupting_public_chamfer_declaration_loses_declaration_credit(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.chamfer

    def wrong_leg(self, obj=None, **kw):
        kw["leg1"] = float(kw["leg1"]) + 1.0
        kw["angle"] = None
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "chamfer", wrong_leg)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_chamfer_lines_loses_generated_code_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_chamfer_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if "sheet.chamfer(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_chamfer_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


def test_removing_placed_chamfer_callout_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_callout(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_chamfer_"))
        drawing.remove(name)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_callout)
    assert _states("drawing_consumer") == {"unsupported"}


def test_wrong_chamfer_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_chamfer_"))
        drawing.registry.named(name).label = "C7"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_ink)
    assert _states("drawing_consumer") == {"unsupported"}


def test_moving_chamfer_leader_off_the_physical_bevel_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_tip(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_chamfer_"))
        drawing.registry.named(name).position = (10.0, 0.0, 0.0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_tip)
    assert _states("drawing_consumer") == {"unsupported"}


def test_severing_chamfer_measurement_provenance_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_provenance(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_chamfer_"))
        identity = drawing.registry.identity_of(name)
        identity["measurement"] = ()
        drawing.registry.reapply(name, identity)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_provenance)
    assert _states("drawing_consumer") == {"unsupported"}


def test_deleting_provider_chamfers_cannot_shrink_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_recognition_result

    def without_chamfers(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, chamfers=())

    monkeypatch.setattr(analysis, "build_recognition_result", without_chamfers)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 12
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


@pytest.mark.parametrize("field", ["leg1", "leg2", "angle"])
def test_weakening_provider_chamfer_parameters_reduces_fidelity(monkeypatch, field) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_recognition_result

    def weakened(*args, **kwargs):
        result = original(*args, **kwargs)
        values = tuple(
            replace(item, **{field: getattr(item, field) + 1.0}) for item in result.chamfers
        )
        return replace(result, chamfers=values)

    monkeypatch.setattr(analysis, "build_recognition_result", weakened)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.passed == 24
    assert damaged.parameter_fidelity.total == 36


def test_quality_summary_counts_chamfers_as_audited_requirements() -> None:
    from draftwright import build_drawing

    completeness = build_drawing(_lone()).lint_summary()["quality"]["completeness"]

    assert completeness["by_family"]["chamfers"] == 1
    assert completeness["placed"] == completeness["requirements"] == 1
    assert completeness["audited_score"] == 1.0
    assert "chamfers" not in completeness["unscored_recognized_families"]
