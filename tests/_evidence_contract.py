"""Per-family evidence contracts that every `*_completeness_evidence` module asserts.

These bodies were written out once per feature family — thirteen copies of the
`_<family>_model_outcomes` check, eleven of the one-recognition check, eleven more of the
real-corpus layer scores — differing only in which family symbol they named and which
counts they expected. That is the growth pattern `test_clone_budget.py` exists to stop: the
same assertion re-paid on every CI run, and one more copy to write (and to get subtly
wrong) with each new recogniser family.

Each function here is the single body; the per-family test calls it with its own symbols
and its own expected numbers, so the assertion still runs once per family and the evidence
is unchanged. The numbers stay at the call site deliberately — they are the family's
evidence, and a helper that computed them would be asserting nothing.

Adding a family should add a call, not a copy.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

#: The four downstream boundaries every completeness fact carries a state for.
_BOUNDARIES = ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer")


def assert_missing_model_outcomes_fail_closed(
    monkeypatch,
    outcomes_attr: str,
    states: Callable[..., object],
) -> None:
    """A family whose per-occurrence outcomes vanish scores `unknown`, never `supported`.

    `outcomes_attr` is the family's `_<family>_model_outcomes` in
    `draftwright.evaluation.step_analysis`; `states` is the module's own `_states`
    reader, so each family still exercises its own observer and fixture part.
    """
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, outcomes_attr, lambda *_args: [])
    # `set(...)`: a few families' `_states` returns a list, the rest a set. Both mean
    # "every observed boundary state", and both must be exactly `unknown` here.
    assert set(states("ir_adapter")) == {"unknown"}


def assert_observer_uses_one_build_owned_recognition(
    monkeypatch,
    family: str,
    part: object,
) -> None:
    """ADR 3: the observer scores the build's OWN aggregate, recognising exactly once.

    A second `build_recognition_evidence` call would mean the facts being scored and the
    features they are matched against came from different recognition runs.
    """
    import draftwright.analysis as analysis
    from draftwright.evaluation.step_analysis import _default_observers

    original = analysis.build_recognition_evidence
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_recognition_evidence", counted)
    assert _default_observers()[family](part)
    assert calls == 1


def assert_real_corpus_scores_all_layers(
    corpus_path: Path,
    *,
    matched: int,
    parameter_fidelity: int,
    downstream_usefulness: int,
) -> None:
    """The family's real STEP corpus scores every layer, and its variants score alike.

    Each family keeps its own three counts — they are the evidence — while the layer
    assertions and the topology/boolean-order variant comparison are one body.
    """
    from draftwright.evaluation.step_analysis import evaluate_step_corpus, load_corpus

    corpus = load_corpus(corpus_path)

    evaluation = evaluate_step_corpus(corpus)
    assert evaluation.detection.recall == 1.0
    assert evaluation.detection.false_positive_rate == 0.0
    assert evaluation.detection.matched == matched
    assert (
        evaluation.parameter_fidelity.passed
        == evaluation.parameter_fidelity.total
        == parameter_fidelity
    )
    assert (
        evaluation.downstream_usefulness.passed
        == evaluation.downstream_usefulness.total
        == downstream_usefulness
    )
    assert evaluation.conformant_cases == evaluation.complete_cases == len(corpus.cases)
    variants = [case for case in evaluation.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def assert_versioned_corpus_covers_every_required_case_class(
    corpus_path: Path,
    *,
    scope: tuple[str, ...],
    cases: int,
    expected: int,
    tags: Iterable[str],
) -> None:
    """The family's corpus is versioned, scoped, sized, classified and CC0-provenanced.

    `tags` is the family's own required classification set; the fixed provenance line
    is the reproducibility pin every corpus shares.
    """
    from draftwright.evaluation.step_analysis import load_corpus

    corpus = load_corpus(corpus_path)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == scope
    assert len(corpus.cases) == cases
    assert sum(len(case.expected) for case in corpus.cases) == expected
    observed = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert set(tags) <= observed
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    assert all(
        "'1970-01-01T00:00:00'"
        in (corpus_path.parent / case.provenance["fixture"]).read_text().splitlines()[3]
        for case in corpus.cases
    )


def assert_every_boundary_is_supported(
    family: str,
    part: Any,
    *,
    message: str | None = None,
) -> None:
    """Every downstream boundary of the family's observation reads `supported`.

    One observation answers all four boundaries: re-reading them through the module's
    own `_states` would pay for four identical `build_drawing` runs of the same part.
    """
    from draftwright.evaluation.step_analysis import _default_observers

    observed = _default_observers()[family](part)
    assert len(observed) == 1, message or f"fixture must produce one {family} observation"
    for boundary in _BOUNDARIES:
        assert {fact.downstream[boundary] for fact in observed} == {"supported"}


def assert_observer_fails_closed_without_build_or_recognition(
    monkeypatch,
    family: str,
    make_part: Callable[[], Any],
) -> None:
    """A failed build and a drawing without recognition both score nothing, not credit.

    `make_part` is called once per probe, as the copies did, so each probe observes a
    part built the same way the passing path builds it.
    """
    import draftwright.builder as builder
    from draftwright.evaluation.step_analysis import _default_observers

    def broken_build(*_args, **_kwargs):
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(builder, "build_drawing", broken_build)
    observer = _default_observers()[family]
    assert observer(make_part()) == ()

    class DrawingWithoutRecognition:
        def recognition(self):
            return None

    monkeypatch.setattr(
        builder, "build_drawing", lambda *_args, **_kwargs: DrawingWithoutRecognition()
    )
    assert observer(make_part()) == ()


def assert_missing_build_owned_recognition_fails_closed(
    monkeypatch,
    family: str,
    part: Any,
    *,
    feature_kind: str | None = None,
) -> None:
    """A drawing whose recognition returns `None` raises, rather than scoring zero.

    ADR 5: the observer refuses rather than reporting an absence as a measurement.
    """
    import pytest

    import draftwright.builder as builder
    from draftwright.evaluation.step_analysis import ObservationError, _default_observers

    original = builder.build_drawing

    def without_recognition(*args, **kwargs):
        drawing = original(*args, **kwargs)
        assert drawing.recognition() is not None
        if feature_kind is not None:
            assert any(feature.kind == feature_kind for feature in drawing.model().features)
        monkeypatch.setattr(type(drawing), "recognition", lambda _drawing: None)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_recognition)
    with pytest.raises(ObservationError, match="recognition access failed"):
        _default_observers()[family](part)


def assert_observer_failure_cannot_pass_the_negative_case(
    monkeypatch,
    corpus_path: Path,
    family: str,
) -> None:
    """A broken build cannot turn the corpus's zero-feature case into a pass.

    The negative case is the one a silently-failing observer would score as correct,
    so the damaged run must read `unknown` and diagnose the family's analysis layer.
    """
    from dataclasses import replace

    import draftwright.builder as builder
    from draftwright.evaluation.step_analysis import evaluate_step_corpus, load_corpus

    corpus = load_corpus(corpus_path)
    negative = next(case for case in corpus.cases if not case.expected)

    def failed_build(*_args, **_kwargs):
        raise RuntimeError("negative-case probe")

    monkeypatch.setattr(builder, "build_drawing", failed_build)
    damaged = evaluate_step_corpus(replace(corpus, cases=(negative,)))

    assert damaged.complete_cases == damaged.conformant_cases == 0
    assert damaged.cases[0].outcome == "unknown"
    assert [(issue.layer, issue.family) for issue in damaged.cases[0].diagnostics] == [
        ("analysis", family)
    ]


def assert_removing_the_placed_callout_loses_drawing_credit(
    monkeypatch,
    annotation_prefix: str,
    states: Callable[[str], Any],
) -> None:
    """Deleting the family's placed callout keeps IR credit and loses drawing credit.

    `states` is the module's own `_states` reader, so each family still scores its own
    observer and fixture part.
    """
    import draftwright.builder as builder
    import draftwright.sheet as sheet_module

    original = builder.build_drawing

    def without_callout(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith(annotation_prefix))
        drawing.remove(name)
        return drawing

    with monkeypatch.context() as patch:
        patch.setattr(sheet_module, "build_drawing", sheet_module.build_drawing)
        patch.setattr(builder, "build_drawing", without_callout)
        assert states("ir_adapter") == {"supported"}
        assert states("drawing_consumer") == {"unsupported"}


def assert_missing_ir_feature_loses_adapter_credit(
    monkeypatch,
    feature_kind: str,
    states: Callable[[str], Any],
) -> None:
    """Removing an observed feature from the built IR must lose adapter credit."""
    from dataclasses import replace

    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_feature(self):
        model = original(self)
        assert any(feature.kind == feature_kind for feature in model.features)
        return replace(
            model,
            features=[feature for feature in model.features if feature.kind != feature_kind],
        )

    monkeypatch.setattr(Drawing, "model", without_feature)
    assert states("ir_adapter") == {"unknown"}


def assert_deleted_generated_line_loses_code_credit(
    monkeypatch,
    line_fragment: str,
    states: Callable[[str], Any],
) -> None:
    """Commenting out the family's emitted call must lose generated-code credit."""
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_call(*args, **kwargs):
        source = original(*args, **kwargs)
        lines = source.splitlines()
        assert any(line_fragment in line for line in lines)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if line_fragment in line else line
            for line in lines
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_call)
    assert states("ir_adapter") == {"supported"}
    assert states("dsl_declaration") == {"supported"}
    assert states("generated_code") == {"unknown"}


def assert_edge_callout_mutation_loses_drawing_credit(
    monkeypatch,
    states: Callable[..., set[str]],
    annotation_prefix: str,
    mutation: Literal["remove", "wrong_ink", "wrong_view", "wrong_tip", "sever_provenance"],
    *,
    wrong_label: str | None = None,
    wrong_tip: tuple[float, float, float] | None = None,
    part: Any = None,
) -> None:
    """A damaged chamfer/fillet callout must lose physical drawing credit."""
    import draftwright.builder as builder

    assert states("drawing_consumer", part) == {"supported"}
    original = builder.build_drawing

    def damaged(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith(annotation_prefix))
        if mutation == "remove":
            drawing.remove(name)
        elif mutation == "wrong_ink":
            assert wrong_label is not None
            annotation = drawing.registry.named(name)
            assert annotation.label != wrong_label
            annotation.label = wrong_label
        elif mutation == "wrong_view":
            identity = drawing.registry.identity_of(name)
            assert identity["view"] != "side"
            identity["view"] = "side"
            drawing.registry.reapply(name, identity)
        elif mutation == "wrong_tip":
            assert wrong_tip is not None
            annotation = drawing.registry.named(name)
            assert tuple(annotation.position) != wrong_tip
            annotation.position = wrong_tip
        elif mutation == "sever_provenance":
            identity = drawing.registry.identity_of(name)
            assert identity["measurement"]
            identity["measurement"] = ()
            drawing.registry.reapply(name, identity)
        else:
            raise AssertionError(f"unknown callout mutation: {mutation}")
        return drawing

    monkeypatch.setattr(builder, "build_drawing", damaged)
    assert states("drawing_consumer", part) == {"unsupported"}


def assert_quality_summary_counts_audited_requirements(
    part: Any,
    family: str,
    *,
    requirements: int,
) -> None:
    """The family's placed callouts are counted as audited completeness requirements."""
    from draftwright import build_drawing

    completeness = build_drawing(part).lint_summary()["quality"]["completeness"]

    assert completeness["by_family"][family] == requirements
    assert completeness["placed"] == completeness["requirements"] == requirements
    assert completeness["audited_score"] == 1.0
    assert family not in completeness["unscored_recognized_families"]
