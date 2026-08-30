"""#1372 — groove completeness uses independent physical annular-recess facts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Align, Cylinder, Pos, import_step

from draftwright.evaluation.step_analysis import (
    _default_observers,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-grooves-v1.json"
_MIN = (Align.CENTER, Align.CENTER, Align.MIN)


def _lone():
    shaft = Pos(0, 0, -30) * Cylinder(10, 60, align=_MIN)
    tool = Pos(0, 0, -2) * (Cylinder(12, 4, align=_MIN) - Cylinder(8, 4, align=_MIN))
    return shaft - tool


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["grooves"](_lone())
    assert len(observed) == 1
    return {fact.downstream[boundary] for fact in observed}


def test_versioned_groove_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("grooves",)
    assert len(corpus.cases) == 8
    assert sum(len(case.expected) for case in corpus.cases) == 10
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "ambiguous",
        "compound",
        "multiple",
        "multiple-equal",
        "negative",
        "overlapping-family",
        "positive",
        "rotated",
        "topology-order-variant",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)


def test_real_groove_corpus_scores_all_layers_and_boolean_order_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 10
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 20
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 40
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_narrow_floor_has_one_drafting_owner_despite_raw_family_overlap() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright import build_drawing

    part = import_step(CORPUS.parent / "groove-narrow.step")
    recognition = build_raw_recognition_result(part)
    drawing = build_drawing(part)
    features = drawing.model().features

    assert len(recognition.grooves) == 1
    assert any(round(item.diameter, 3) == 18.0 for item in recognition.bosses)
    assert any(round(item.diameter, 3) == 18.0 for item in recognition.turned_steps)
    assert len([feature for feature in features if feature.kind == "groove"]) == 1
    assert not [
        feature
        for feature in features
        if feature.kind in {"boss", "step"}
        and round(float(getattr(feature, "diameter", -1)), 3) == 18.0
    ]
    floor_labels = [
        name
        for name in drawing.annotations()
        if "ø18" in str(getattr(drawing.registry.named(name), "label", ""))
    ]
    assert len(floor_labels) == 1
    assert floor_labels[0].startswith("m_groove_")


def test_groove_ledger_tracks_width_and_floor_diameter_and_fails_closed() -> None:
    from draftwright import build_drawing
    from draftwright.linting.groove_coverage import groove_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    outcomes = groove_requirement_outcomes(recognition, drawing.model().features, drawing.registry)

    assert {outcome.parameter_id for outcome in outcomes} == {
        "groove.length",
        "groove.diameter",
    }
    assert {outcome.state for outcome in outcomes} == {"placed"}
    missing = groove_requirement_outcomes(
        recognition, drawing.model().features, AnnotationRegistry()
    )
    assert {outcome.state for outcome in missing} == {"missing"}
    unverifiable = groove_requirement_outcomes(
        recognition,
        [feature for feature in drawing.model().features if feature.kind != "groove"],
        AnnotationRegistry(),
    )
    assert len(unverifiable) == 1
    assert (unverifiable[0].state, unverifiable[0].requirement_count) == ("unverifiable", 2)


def test_groove_ledger_rejects_foreign_results_and_malformed_ir_without_guessing() -> None:
    from b123d_recognisers import build_raw_recognition_result

    from draftwright.linting.groove_coverage import groove_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    recognition = build_raw_recognition_result(_lone())
    source = recognition.grooves[0]

    class MalformedGroove:
        kind = "groove"
        axis = source.axis
        at = source.at
        width = source.width
        diameter = source.diameter

        @staticmethod
        def parameters():
            raise TypeError("broken parameter contract")

    assert groove_requirement_outcomes(None, (), AnnotationRegistry()) == []
    with pytest.raises(TypeError, match="run's RecognitionResult"):
        groove_requirement_outcomes(object(), (), AnnotationRegistry())
    outcomes = groove_requirement_outcomes(recognition, (MalformedGroove(),), AnnotationRegistry())
    assert len(outcomes) == 1
    assert (outcomes[0].state, outcomes[0].requirement_count) == ("unverifiable", 2)


def test_owner_without_measurement_or_note_provenance_is_unverifiable() -> None:
    from draftwright import build_drawing
    from draftwright.linting.groove_coverage import groove_requirement_outcomes
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    feature = next(item for item in drawing.model().features if item.kind == "groove")
    registry = AnnotationRegistry()
    registry.add(object(), "orphan", "front", feature=feature)

    outcomes = groove_requirement_outcomes(recognition, drawing.model().features, registry)
    assert {outcome.state for outcome in outcomes} == {"unverifiable"}


def test_every_groove_boundary_is_observed_supported_on_the_real_public_path() -> None:
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert _states(boundary) == {"supported"}


def test_groove_observer_uses_one_build_owned_recognition_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", counted)
    assert _default_observers()["grooves"](_lone())
    assert calls == 1


def test_removing_grooves_from_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_grooves(self):
        model = original(self)
        return replace(model, features=[f for f in model.features if f.kind != "groove"])

    monkeypatch.setattr(Drawing, "model", without_grooves)
    assert _states("ir_adapter") == {"unknown"}


def test_missing_per_groove_boundary_outcomes_fail_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_groove_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_observer_fails_closed_when_build_or_recognition_is_unavailable(monkeypatch) -> None:
    import draftwright.builder as builder
    from draftwright.evaluation.step_analysis import ObservationError

    def failed_build(*_args, **_kwargs):
        raise RuntimeError("probe")

    monkeypatch.setattr(builder, "build_drawing", failed_build)
    with pytest.raises(ObservationError, match="drawing build failed: probe"):
        _default_observers()["grooves"](_lone())


def test_observer_fails_closed_when_built_recognition_is_unavailable(monkeypatch) -> None:
    from draftwright.drawing import Drawing
    from draftwright.evaluation.step_analysis import ObservationError

    monkeypatch.setattr(Drawing, "recognition", lambda _self: None)
    with pytest.raises(ObservationError, match="recognition access failed"):
        _default_observers()["grooves"](_lone())


def test_observer_failure_cannot_pass_the_zero_groove_negative_case(monkeypatch) -> None:
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
        ("analysis", "grooves")
    ]


def test_corrupting_public_groove_declaration_loses_declaration_credit(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.groove

    def wrong_diameter(self, obj=None, **kw):
        kw["diameter"] = float(kw["diameter"]) + 1.0
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "groove", wrong_diameter)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_groove_lines_loses_generated_code_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_groove_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if "sheet.groove(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_groove_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


def test_removing_placed_groove_callout_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_callout(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_groove_"))
        drawing.remove(name)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_callout)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_moving_groove_leader_off_its_physical_station_loses_drawing_credit(
    monkeypatch,
) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_station(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_groove_"))
        callout = drawing.registry.named(name)
        assert callout.label == "4 WIDE × ø16"
        assert len(drawing.registry.measurement_of(name)) == 2
        # Move the actual placed Leader and its live tip 10 mm along the Z-profile axis.  Its
        # semantic metadata remains intact, but the arrow now identifies plain shaft.
        callout.position = (0.0, 10.0, 0.0)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_station)
    assert _states("drawing_consumer") == {"unsupported"}


def test_production_profile_view_routing_cannot_rewrite_the_groove_oracle(monkeypatch) -> None:
    from draftwright._geometry import _EDGE_ON

    # An X-axis groove's width is visible in front/profile, never side/end-on.  Mutating the
    # renderer's shared routing table must move only production ink, not its expected answer.
    monkeypatch.setitem(_EDGE_ON, "x", "side")
    part = import_step(CORPUS.parent / "groove-lone-x.step")
    observed = _default_observers()["grooves"](part)

    assert len(observed) == 1
    assert observed[0].identity["axis"] == "x"
    assert observed[0].downstream["drawing_consumer"] == "unsupported"


@pytest.mark.parametrize("wrong_label", ("16 WIDE × ø4", "4 WIDE ø16", "4 WIDE × ø16 999"))
def test_wrong_groove_nominal_or_syntax_ink_loses_drawing_credit(monkeypatch, wrong_label) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_groove_"))
        callout = drawing.registry.named(name)
        assert callout.label == "4 WIDE × ø16"
        callout.label = wrong_label
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_ink)
    assert _states("drawing_consumer") == {"unsupported"}


@pytest.mark.parametrize(
    "wrong_label",
    (
        "4 ø16",
        "4 WIDE ø16",
        "4 WIDE x ø16",
        "4 WIDE × 16",
        "16 WIDE × ø4",
        "4 WIDE × ø16 EXTRA",
    ),
)
def test_production_groove_formatter_cannot_rewrite_its_own_oracle(
    monkeypatch, wrong_label
) -> None:
    import draftwright.annotations.from_model as from_model

    monkeypatch.setattr(from_model, "_groove_label", lambda *_args, **_kwargs: wrong_label)
    assert _states("drawing_consumer") == {"unsupported"}


def test_severing_one_measurement_claim_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_diameter_claim(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_groove_"))
        identity = drawing.registry.identity_of(name)
        assert len(identity["measurement"]) == 2
        drawing.registry.reapply(name, {**identity, "measurement": identity["measurement"][:1]})
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_diameter_claim)
    assert _states("drawing_consumer") == {"unsupported"}


def test_exact_groove_ink_contract_accepts_compiler_approved_tolerances() -> None:
    from draftwright import build_drawing
    from draftwright.evaluation.step_analysis import _groove_drawing_outcomes

    part = _lone()
    baseline = build_drawing(part)
    groove = next(feature for feature in baseline.model().features if feature.kind == "groove")
    model = replace(
        baseline.model(),
        decorations={(groove, "length"): 0.2, (groove, "diameter"): 0.1},
    )
    drawing = build_drawing(part, model=model)
    drawing.lint()
    recognition = drawing.recognition()
    assert recognition is not None

    assert _groove_drawing_outcomes(recognition.grooves, drawing) == ["supported"]
    labels = {
        drawing.registry.named(name).label
        for name in drawing.annotations()
        if name.startswith("m_groove_")
    }
    assert labels == {"4 ±0.2 WIDE × ø16 ±0.1"}


def test_independent_groove_tolerance_oracle_covers_every_supported_form() -> None:
    from types import SimpleNamespace

    from draftwright.evaluation.step_analysis import _groove_expected_tolerance_suffix
    from draftwright.fits import FitClass

    draft = SimpleNamespace(decimal_precision=2)
    assert _groove_expected_tolerance_suffix(None, draft) == ""
    assert _groove_expected_tolerance_suffix(0.125, draft) == " ±0.12"
    assert _groove_expected_tolerance_suffix((0.1, 0.2), draft) == " +0.20 -0.10"
    assert _groove_expected_tolerance_suffix(FitClass("H7", 0.0, 0.021), draft) == " H7"
    assert (
        _groove_expected_tolerance_suffix(FitClass("H7", 0.0, 0.021, show="deviation"), draft)
        == " +0.021/0"
    )
    assert (
        _groove_expected_tolerance_suffix(
            FitClass("js6", -0.0045, 0.0045, show="deviation"), draft
        )
        == " +0.0045/-0.0045"
    )


@pytest.mark.parametrize("damage", ("wrong_view", "malformed_tip"))
def test_wrong_or_unreadable_groove_leader_target_loses_drawing_credit(
    monkeypatch, damage
) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_bad_target(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_groove_"))
        if damage == "wrong_view":
            identity = drawing.registry.identity_of(name)
            drawing.registry.reapply(name, {**identity, "view": "side"})
        else:
            drawing.registry.named(name)._tip_local = None
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_bad_target)
    assert _states("drawing_consumer") == {"unsupported"}


def test_incomplete_compiler_group_cannot_certify_groove_ink(monkeypatch) -> None:
    import draftwright.model.compiled as compiled
    from draftwright import build_drawing
    from draftwright.evaluation.step_analysis import _groove_drawing_outcomes

    drawing = build_drawing(_lone())
    recognition = drawing.recognition()
    assert recognition is not None
    plan = compiled.compile_dimensions(drawing.model())
    broken = replace(
        plan,
        groups=tuple(
            replace(group, dims=()) if group.feature_kind == "groove" else group
            for group in plan.groups
        ),
    )
    monkeypatch.setattr(compiled, "compile_dimensions", lambda _model: broken)

    assert _groove_drawing_outcomes(recognition.grooves, drawing) == ["unsupported"]


def test_deleting_provider_grooves_cannot_shrink_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def without_grooves(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, grooves=())

    monkeypatch.setattr(analysis, "build_raw_recognition_result", without_grooves)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 10
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


@pytest.mark.parametrize("field", ("width", "diameter"))
def test_weakening_provider_measurements_reduces_parameter_fidelity(monkeypatch, field) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def weakened_grooves(*args, **kwargs):
        result = original(*args, **kwargs)
        grooves = tuple(
            replace(groove, **{field: getattr(groove, field) + 1.0}) for groove in result.grooves
        )
        return replace(result, grooves=grooves)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", weakened_grooves)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.passed == 10
    assert damaged.parameter_fidelity.total == 20
    assert damaged.parameter_fidelity.score == 0.5


@pytest.mark.parametrize("field", ("axis", "at"))
def test_weakening_provider_identity_reduces_detection_recall(monkeypatch, field) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def weakened_grooves(*args, **kwargs):
        result = original(*args, **kwargs)
        if field == "axis":
            values = tuple(
                replace(groove, axis={"x": "y", "y": "z", "z": "x"}[groove.axis])
                for groove in result.grooves
            )
        else:
            values = tuple(
                replace(groove, at=(groove.at[0] + 1.0, groove.at[1], groove.at[2]))
                for groove in result.grooves
            )
        return replace(result, grooves=values)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", weakened_grooves)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 0.0
    assert damaged.detection.missed == 10
    assert damaged.detection.false_positives == 10


def test_deleting_groove_declaration_cannot_shrink_quality_denominator() -> None:
    from draftwright import Sheet, build_drawing

    complete = build_drawing(_lone())
    sparse = Sheet(_lone())
    envelope = sparse.envelope()
    sparse.dimension(envelope, "width.length")

    complete_quality = complete.lint_summary()["quality"]["completeness"]
    sparse_quality = sparse.build().lint_summary()["quality"]["completeness"]
    assert complete_quality["requirements"] == sparse_quality["requirements"] == 2
    assert complete_quality["by_family"]["grooves"] == 2
    assert complete_quality["placed"] == 2
    assert sparse_quality["unverifiable"] == 2
    assert complete_quality["audited_score"] == 1.0
    assert sparse_quality["audited_score"] == 0.0
