"""#1374 — fillet completeness uses independent physical rounded-edge facts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Axis, Box, Cylinder, GeomType, Pos, fillet, import_step

from draftwright.evaluation.step_analysis import (
    ObservationError,
    _default_observers,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-fillets-v1.json"


def _lone():
    part = Box(60, 40, 30)
    edge = sorted(
        part.edges().filter_by(Axis.Z),
        key=lambda item: item.center().X + item.center().Y,
    )[-1]
    return fillet(edge, 4)


def _turned():
    shaft = Cylinder(15, 60)
    edge = sorted(shaft.edges().filter_by(GeomType.CIRCLE), key=lambda item: item.center().Z)[-1]
    return fillet(edge, 3)


def _states(boundary: str, part=None) -> set[str]:
    observed = _default_observers()["fillets"](_lone() if part is None else part)
    assert len(observed) == 1
    return {fact.downstream[boundary] for fact in observed}


def test_versioned_fillet_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("fillets",)
    assert len(corpus.cases) == 10
    assert sum(len(case.expected) for case in corpus.cases) == 14
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "ambiguous",
        "compound",
        "multiple-equal",
        "negative",
        "overlapping-family",
        "positive",
        "repeated-edge",
        "rotated",
        "topology-order-variant",
        "turned",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)


def test_real_fillet_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 14
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 14
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 56
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_public_framed_route_preserves_arbitrarily_rotated_planar_and_turned_rounds() -> None:
    from b123d_recognisers import FramedRecognitionResult, build_framed_recognition_result
    from build123d import Rot

    fixtures = (
        (Rot(23, 37, 11) * _lone(), False, 4.0),
        (Rot(17, 31, 43) * _turned(), True, 3.0),
    )

    for part, turned, radius in fixtures:
        framed = build_framed_recognition_result(part, rotational=turned)
        assert isinstance(framed, FramedRecognitionResult)
        assert len(framed.result.fillets) == 1
        observed = framed.result.fillets[0]
        assert (observed.radius, observed.turned) == (radius, turned)


def test_circular_blind_step_curved_wall_has_one_aggregate_owner() -> None:
    from b123d_recognisers import (
        build_raw_recognition_result,
        recognise_circular_blind_steps,
        recognise_fillets,
    )

    part = import_step(CORPUS.parent / "fillet-overlap.step")
    direct_fillets = recognise_fillets(part)
    direct_steps = recognise_circular_blind_steps(part)
    recognition = build_raw_recognition_result(part)

    assert len(direct_fillets) == len(direct_steps) == 1
    assert direct_fillets[0].radius == direct_steps[0].radius == 8.0
    assert recognition.fillets == ()
    assert recognition.circular_blind_steps == tuple(direct_steps)


def test_equal_repeated_fillets_share_ink_but_keep_every_measurement_identity() -> None:
    from draftwright import build_drawing

    drawing = build_drawing(import_step(CORPUS.parent / "fillet-repeated.step"))
    names = [name for name in drawing.annotations() if name.startswith("m_fillet_")]

    assert len(names) == 1
    assert drawing.registry.named(names[0]).label == "4× R5"
    assert len(drawing.registry.measurement_of(names[0])) == 4
    assert {
        measurement.parameter for measurement in drawing.registry.measurement_of(names[0])
    } == {"fillet.radius"}


def test_fillet_ledger_tracks_one_callout_per_physical_round_and_fails_closed() -> None:
    from draftwright import build_drawing
    from draftwright.linting.fillet_coverage import fillet_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = fillet_requirement_outcomes(recognition, drawing.model().features, drawing.registry)
    assert [(item.parameter_id, item.state) for item in outcomes] == [("fillet.radius", "placed")]
    assert (
        fillet_requirement_outcomes(recognition, drawing.model().features, AnnotationRegistry())[
            0
        ].state
        == "missing"
    )
    assert (
        fillet_requirement_outcomes(
            recognition,
            [feature for feature in drawing.model().features if feature.kind != "fillet"],
            AnnotationRegistry(),
        )[0].state
        == "unverifiable"
    )


def test_fillet_ledger_rejects_foreign_results_and_malformed_ir_without_guessing() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.fillet_coverage import fillet_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    recognition = build_raw_recognition_result(_lone())
    source = recognition.fillets[0]

    class MalformedFillet:
        kind = "fillet"
        axis = source.axis
        frame = type("Frame", (), {"origin": source.at})()
        turned = source.turned
        radius = source.radius

        @staticmethod
        def parameters():
            raise TypeError("broken parameter contract")

    assert fillet_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="run's RecognitionResult"):
        fillet_requirement_outcomes(object(), (), AnnotationRegistry())
    outcome = fillet_requirement_outcomes(recognition, (MalformedFillet(),), AnnotationRegistry())[
        0
    ]
    assert outcome.state == "unverifiable"


def test_fillet_ledger_distinguishes_suppressed_dropped_and_orphan_evidence() -> None:
    from types import SimpleNamespace

    from draftwright import build_drawing
    from draftwright.linting.fillet_coverage import fillet_requirement_outcomes
    from draftwright.linting.issues import LintIssue
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "fillet")

    omission = SimpleNamespace(feature=feature, parameter_id="fillet.radius", authored=True)
    suppressed = fillet_requirement_outcomes(
        recognition, drawing.model().features, AnnotationRegistry(), (omission,)
    )
    assert suppressed[0].state == "suppressed"

    dropped_registry = AnnotationRegistry()
    dropped_registry.record_issue(
        LintIssue(
            "warning",
            "synthetic placement failure",
            code="fillet_dropped",
            measurement_ids=(DimensionId(feature, "fillet.radius"),),
        )
    )
    dropped = fillet_requirement_outcomes(recognition, drawing.model().features, dropped_registry)
    assert dropped[0].state == "dropped"

    orphan_registry = AnnotationRegistry()
    orphan_registry.add(object(), "orphan", "plan", feature=feature)
    orphan = fillet_requirement_outcomes(recognition, drawing.model().features, orphan_registry)
    assert orphan[0].state == "unverifiable"


def test_fillet_ledger_distinguishes_structured_satisfaction() -> None:
    from draftwright import build_drawing
    from draftwright.linting.fillet_coverage import fillet_requirement_outcomes
    from draftwright.model.compiled import DimensionId
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "fillet")
    registry = AnnotationRegistry()
    registry.add(
        object(),
        "structured_note",
        "plan",
        feature=feature,
        satisfaction=DimensionId(feature, "fillet.radius"),
    )

    outcome = fillet_requirement_outcomes(recognition, drawing.model().features, registry)[0]
    assert outcome.state == "satisfied_by_structured_note"


def test_every_fillet_boundary_is_observed_supported_on_the_real_public_path() -> None:
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert _states(boundary) == {"supported"}


def test_fillet_observer_uses_one_build_owned_recognition_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_recognition_evidence
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_recognition_evidence", counted)
    assert _default_observers()["fillets"](_lone())
    assert calls == 1


def test_removing_fillets_from_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_fillets(self):
        model = original(self)
        return replace(model, features=[item for item in model.features if item.kind != "fillet"])

    monkeypatch.setattr(Drawing, "model", without_fillets)
    assert _states("ir_adapter") == {"unknown"}


def test_missing_per_fillet_boundary_outcomes_fail_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_fillet_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_observer_failure_cannot_pass_even_the_zero_fillet_negative(monkeypatch) -> None:
    import draftwright.builder as builder

    corpus = load_corpus(CORPUS)
    negative = next(case for case in corpus.cases if case.case_id == "fillet-plain-negative")

    def failed_build(*_args, **_kwargs):
        raise RuntimeError("negative-case probe")

    monkeypatch.setattr(builder, "build_drawing", failed_build)
    with pytest.raises(ObservationError, match="drawing build failed"):
        _default_observers()["fillets"](Box(20, 20, 20))
    damaged = evaluate_step_corpus(replace(corpus, cases=(negative,)))
    assert damaged.complete_cases == damaged.conformant_cases == 0
    assert [(issue.layer, issue.family) for issue in damaged.cases[0].diagnostics] == [
        ("analysis", "fillets")
    ]


def test_missing_build_owned_recognition_fails_closed(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_recognition(*args, **kwargs):
        drawing = original(*args, **kwargs)
        monkeypatch.setattr(type(drawing), "recognition", lambda _drawing: None)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_recognition)
    with pytest.raises(ObservationError, match="recognition access failed"):
        _default_observers()["fillets"](_lone())


def test_corrupting_public_fillet_declaration_loses_declaration_credit(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.fillet

    def wrong_radius(self, obj=None, **kw):
        kw["radius"] = float(kw["radius"]) + 1.0
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "fillet", wrong_radius)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_fillet_lines_loses_generated_code_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_fillet_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if "sheet.fillet(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_fillet_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


def test_removing_placed_fillet_callout_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_callout(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_fillet_"))
        drawing.remove(name)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_callout)
    assert _states("drawing_consumer") == {"unsupported"}


def test_wrong_fillet_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_fillet_"))
        drawing.registry.named(name).label = "R7"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_ink)
    assert _states("drawing_consumer") == {"unsupported"}


def test_wrong_fillet_view_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_view(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_fillet_"))
        identity = drawing.registry.identity_of(name)
        identity["view"] = "side"
        drawing.registry.reapply(name, identity)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_view)
    assert _states("drawing_consumer") == {"unsupported"}


def test_moving_fillet_leader_off_the_physical_round_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_tip(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_fillet_"))
        drawing.registry.named(name).position = (10.0, 0.0, 0.0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_tip)
    assert _states("drawing_consumer") == {"unsupported"}


def test_moving_turned_fillet_leader_off_the_profile_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_tip(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_fillet_"))
        drawing.registry.named(name).position = (50.0, 0.0, 0.0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_tip)
    assert _states("drawing_consumer", _turned()) == {"unsupported"}


def test_partial_od_turned_fillets_keep_their_physical_source_target() -> None:
    half_shaft = Cylinder(10, 40) & (Pos(-10, 0, 0) * Box(20, 10, 40))
    circular_edges = [edge for edge in half_shaft.edges() if edge.geom_type == GeomType.CIRCLE]
    part = fillet(circular_edges, 1)

    observed = _default_observers()["fillets"](part)

    assert len(observed) == 2
    assert all(fact.downstream["drawing_consumer"] == "supported" for fact in observed)


def test_moving_partial_od_fillet_leader_off_source_loses_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_tip(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_fillet_"))
        drawing.registry.named(name).position = (50.0, 0.0, 0.0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_tip)
    half_shaft = Cylinder(10, 40) & (Pos(-10, 0, 0) * Box(20, 10, 40))
    circular_edges = [edge for edge in half_shaft.edges() if edge.geom_type == GeomType.CIRCLE]
    observed = _default_observers()["fillets"](fillet(circular_edges, 1))

    assert len(observed) == 2
    assert all(fact.downstream["drawing_consumer"] == "unsupported" for fact in observed)


def test_severing_fillet_measurement_provenance_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_provenance(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_fillet_"))
        identity = drawing.registry.identity_of(name)
        identity["measurement"] = ()
        drawing.registry.reapply(name, identity)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_provenance)
    assert _states("drawing_consumer") == {"unsupported"}


def test_deleting_provider_fillets_cannot_shrink_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def without_fillets(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, fillets=())

    monkeypatch.setattr(analysis, "_result_from_evidence", without_fillets)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 14
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


def test_weakening_provider_fillet_radius_reduces_fidelity(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def weakened(*args, **kwargs):
        result = original(*args, **kwargs)
        values = tuple(replace(item, radius=item.radius + 0.5) for item in result.fillets)
        return replace(result, fillets=values)

    monkeypatch.setattr(analysis, "_result_from_evidence", weakened)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.passed == 0
    assert damaged.parameter_fidelity.total == 14


def test_quality_summary_counts_fillets_as_audited_requirements() -> None:
    from draftwright import build_drawing

    completeness = build_drawing(_lone()).lint_summary()["quality"]["completeness"]

    assert completeness["by_family"]["fillets"] == 1
    assert completeness["placed"] == completeness["requirements"] == 1
    assert completeness["audited_score"] == 1.0
    assert "fillets" not in completeness["unscored_recognized_families"]
