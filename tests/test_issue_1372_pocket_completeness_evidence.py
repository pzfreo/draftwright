"""#1372 — lone-pocket completeness uses independent physical blind-recess facts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Box, Pos, import_step

from draftwright.evaluation.step_analysis import (
    _default_observers,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-pockets-v1.json"


def _lone():
    return Box(100, 60, 20) - Pos(22, -11, 7) * Box(30, 12, 6)


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["pockets"](_lone())
    assert len(observed) == 1
    return {fact.downstream[boundary] for fact in observed}


def test_versioned_pocket_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.0.0", 1)
    assert corpus.scope == ("pockets",)
    assert len(corpus.cases) == 10
    assert sum(len(case.expected) for case in corpus.cases) == 13
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "ambiguous",
        "compound",
        "edge-anchored",
        "inapplicable-location",
        "multiple-equal",
        "negative",
        "opposed",
        "overlapping-family",
        "positive",
        "rotated",
        "topology-order-variant",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)


def test_real_pocket_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 13
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 52
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 52
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_overlapping_recess_families_retain_one_physical_owner() -> None:
    from b123d_recognisers import build_recognition_result

    fixtures = CORPUS.parent
    through = build_recognition_result(import_step(fixtures / "pocket-through-negative.step"))
    polygonal = build_recognition_result(import_step(fixtures / "pocket-prismatic-negative.step"))

    assert through.pockets == ()
    assert len(through.slots) == 1
    assert polygonal.pockets == ()
    assert len(polygonal.prismatic_pockets) == 1


def test_opposite_openings_survive_ir_declaration_and_generated_code() -> None:
    from draftwright.builder import build_drawing
    from draftwright.sheet_emit import _feature_line

    part = import_step(CORPUS.parent / "pocket-opposed.step")
    drawing = build_drawing(part)
    pockets = [feature for feature in drawing.model().features if feature.kind == "pocket"]

    assert {feature.open_sign for feature in pockets} == {-1, 1}
    source = "\n".join(_feature_line(feature) for feature in pockets)
    assert source.count("sheet.pocket(") == 2
    assert source.count("open_sign=-1") == 1
    observed = _default_observers()["pockets"](part)
    assert {fact.identity["open_sign"] for fact in observed} == {-1, 1}
    assert {state for fact in observed for state in fact.downstream.values()} == {"supported"}


def test_edge_anchored_pocket_reports_location_as_intentionally_inapplicable() -> None:
    from draftwright.builder import build_drawing
    from draftwright.linting.pocket_coverage import pocket_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    part = import_step(CORPUS.parent / "pocket-edge-anchored.step")
    drawing = build_drawing(part)
    outcomes = pocket_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )

    placed = {outcome.parameter_id for outcome in outcomes if outcome.state == "placed"}
    inapplicable = {
        outcome.parameter_id for outcome in outcomes if outcome.state == "inapplicable"
    }
    assert placed == {
        "pocket_width.length",
        "pocket_length.length",
        "pocket_depth.length",
    }
    assert inapplicable == {
        "location_pocket.location.x",
        "location_pocket.location.y",
    }
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["requirements"] == completeness["placed"] == 3
    assert completeness["inapplicable"] == 2


def test_pattern_members_are_not_counted_again_as_lone_pockets() -> None:
    from b123d_recognisers import build_recognition_result

    from draftwright.builder import build_drawing
    from draftwright.linting.pocket_coverage import pocket_requirement_outcomes

    part = Box(30, 150, 20)
    for y in (-45, -15, 15, 45):
        part -= Pos(0, y, 7) * Box(10, 12, 6)
    drawing = build_drawing(part)
    recognition = build_recognition_result(part)

    assert len(recognition.pockets) == 4
    assert len(recognition.pocket_patterns) == 1
    assert (
        pocket_requirement_outcomes(
            drawing.recognition(), drawing.model().features, drawing.registry
        )
        == []
    )
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert "pockets" not in completeness["by_family"] or completeness["by_family"]["pockets"] == 0
    assert completeness["by_family"]["pocket_patterns"] == 7
    assert completeness["placed"] == completeness["requirements"] == 7
    assert "pocket_patterns" not in completeness["unscored_recognized_families"]


def test_every_pocket_boundary_is_observed_supported_on_the_real_public_path() -> None:
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert _states(boundary) == {"supported"}


def test_pocket_observer_uses_one_build_owned_recognition_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", counted)
    assert _default_observers()["pockets"](_lone())
    assert calls == 1


def test_removing_pockets_from_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_pockets(self):
        model = original(self)
        return replace(model, features=[f for f in model.features if f.kind != "pocket"])

    monkeypatch.setattr(Drawing, "model", without_pockets)
    assert _states("ir_adapter") == {"unknown"}


def test_a_boundary_with_missing_per_requirement_outcomes_fails_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_pocket_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_corrupting_public_pocket_declaration_loses_declaration_credit(monkeypatch) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.pocket

    def wrong_opening(self, obj=None, **kw):
        kw["open_sign"] = -int(kw["open_sign"])
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "pocket", wrong_opening)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_pocket_lines_loses_generated_code_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_pocket_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if "sheet.pocket(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_pocket_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


def test_removing_placed_pocket_callout_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_callout(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_pocket_"))
        drawing.remove(name)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_callout)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_severing_one_directional_location_fact_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_x_location_fact(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(
            name
            for name in drawing.annotations()
            if any(
                fact[1] == "location_pocket.location.x"
                for fact in getattr(drawing.registry.named(name), "covers_hole_locations", ())
            )
        )
        drawing.registry.named(name).covers_hole_locations = ()
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_x_location_fact)
    assert _states("drawing_consumer") == {"unsupported"}


@pytest.mark.parametrize(
    "wrong_label",
    ("6 × 30 × 12 DEEP", "12 999 × 30 × 6 DEEP"),
)
def test_wrong_pocket_nominal_ink_loses_drawing_credit(monkeypatch, wrong_label) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(name for name in drawing.annotations() if name.startswith("m_pocket_"))
        callout = drawing.registry.named(name)
        assert callout.label == "12 × 30 × 6 DEEP"
        callout.label = wrong_label
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_ink)
    assert _states("drawing_consumer") == {"unsupported"}


def test_exact_pocket_ink_contract_accepts_compiler_approved_tolerances() -> None:
    from draftwright.builder import build_drawing
    from draftwright.evaluation.step_analysis import _pocket_drawing_outcomes

    part = _lone()
    baseline = build_drawing(part)
    pocket = next(feature for feature in baseline.model().features if feature.kind == "pocket")
    model = replace(baseline.model(), decorations={(pocket, "length"): 0.2})
    drawing = build_drawing(part, model=model)
    drawing.lint()  # acquire the build-owned recognition aggregate
    recognition = drawing.recognition()
    assert recognition is not None

    assert _pocket_drawing_outcomes(recognition.pockets, drawing) == ["supported"]
    labels = {
        drawing.registry.named(name).label
        for name in drawing.annotations()
        if name.startswith("m_pocket_")
    }
    assert labels == {"12 ±0.2 × 30 ±0.2 × 6 ±0.2 DEEP"}


@pytest.mark.parametrize("wrong_label", ("19", "72 999"))
def test_wrong_directional_pocket_location_ink_loses_drawing_credit(
    monkeypatch, wrong_label
) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_location_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(
            name
            for name in drawing.annotations()
            if any(
                fact[1] == "location_pocket.location.x"
                for fact in getattr(drawing.registry.named(name), "covers_hole_locations", ())
            )
        )
        location = drawing.registry.named(name)
        assert location.label == "72"
        location.label = wrong_label
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_location_ink)
    assert _states("drawing_consumer") == {"unsupported"}


def test_wrong_side_opening_pocket_location_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_location_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        location = drawing.registry.named("m_pocket0_pos_long")
        assert location.label == "28"
        location.label = "28 999"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_location_ink)
    part = import_step(CORPUS.parent / "pocket-side.step")
    observed = _default_observers()["pockets"](part)
    assert {fact.downstream["drawing_consumer"] for fact in observed} == {"unsupported"}


def test_side_opening_authored_location_omission_is_suppressed_not_missing() -> None:
    from draftwright import Sheet
    from draftwright.linting.pocket_coverage import pocket_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    part = import_step(CORPUS.parent / "pocket-side.step")
    sheet = Sheet.from_part(part).take_over(
        dimensions="authored",
        principal_views="automatic",
        derived_views="automatic",
    )
    pocket = next(feature for feature in sheet.features if feature.kind == "pocket")
    for parameter in (
        "pocket_width.length",
        "pocket_length.length",
        "pocket_depth.length",
    ):
        sheet.dimension(pocket, parameter)
    drawing = sheet.build()
    # Sheet builds acquire recognition lazily when lint first asks for completeness.
    drawing.lint()
    plan = compile_dimensions(drawing.model())
    outcomes = pocket_requirement_outcomes(
        drawing.recognition(),
        drawing.model().features,
        drawing.registry,
        plan.diagnostics,
    )

    assert {outcome.parameter_id for outcome in outcomes if outcome.state == "suppressed"} == {
        "location_pocket.z",
        "location_pocket.y",
    }
    assert not [issue for issue in drawing.lint() if issue.code == "pocket_requirement_missing"]


def test_deleting_provider_pockets_cannot_shrink_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def without_pockets(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, pockets=(), pocket_patterns=())

    monkeypatch.setattr(analysis, "build_raw_recognition_result", without_pockets)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 13
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


def test_weakening_provider_widths_reduces_parameter_fidelity(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def weakened_pockets(*args, **kwargs):
        result = original(*args, **kwargs)
        pockets = tuple(replace(pocket, width=pocket.width + 1.0) for pocket in result.pockets)
        return replace(result, pockets=pockets)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", weakened_pockets)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.passed == 39
    assert damaged.parameter_fidelity.total == 52
    assert damaged.parameter_fidelity.score == 0.75


def test_deleting_pocket_declaration_cannot_shrink_quality_denominator() -> None:
    from draftwright import Sheet, build_drawing

    complete = build_drawing(_lone())
    sparse = Sheet(_lone())
    envelope = sparse.envelope()
    sparse.dimension(envelope, "width.length")

    complete_quality = complete.lint_summary()["quality"]["completeness"]
    sparse_quality = sparse.build().lint_summary()["quality"]["completeness"]
    assert complete_quality["requirements"] == sparse_quality["requirements"] == 5
    assert complete_quality["by_family"]["pockets"] == 5
    assert complete_quality["placed"] == 5
    assert sparse_quality["unverifiable"] == 5
    assert complete_quality["audited_score"] == 1.0
    assert sparse_quality["audited_score"] == 0.0
