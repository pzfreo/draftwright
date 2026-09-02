"""#1370 — countersink completeness is independent and keeps bore ownership singular."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Compound, Cone, Cylinder, Pos, import_step

from draftwright.evaluation.step_analysis import (
    _countersink_claim_has_role_specific_ink,
    _countersink_sites,
    _default_observers,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-countersinks-v1.json"


def _single_part():
    return import_step(CORPUS.parent / "countersink-single.step")


def _mixed_part():
    return import_step(CORPUS.parent / "countersink-mixed-pair.step")


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["countersinks"](_single_part())
    assert len(observed) == 1, "fixture must produce one countersink observation"
    return {fact.downstream[boundary] for fact in observed}


def _two_face_part():
    return (
        Box(50, 50, 12)
        - Cylinder(3, 12)
        - Pos(0, 0, 4) * Cone(3, 7, 4)
        - Pos(0, 0, -4) * Cone(7, 3, 4)
    )


def _coaxial_compound_part():
    def seat(z: float):
        return Pos(0, 0, z) * (Box(30, 30, 12) - Cylinder(3, 12) - Pos(0, 0, 4) * Cone(3, 7, 4))

    return Compound(children=[seat(0), seat(30)])


def test_versioned_countersink_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.1.0", 1)
    assert corpus.scope == ("countersinks",)
    assert sum(len(case.expected) for case in corpus.cases) == 7
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {"positive", "negative", "ambiguous", "compound", "topology-order-variant"} <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    countersink_fixtures = sorted(CORPUS.parent.glob("countersink-*.step"))
    assert len(countersink_fixtures) == 6
    assert all(
        "'2000-01-01T00:00:00'" in fixture.read_text(encoding="utf-8").split("ENDSEC;", 1)[0]
        for fixture in countersink_fixtures
    )


def test_real_countersink_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert (first.parameter_fidelity.passed, first.parameter_fidelity.total) == (28, 28)
    assert first.parameter_fidelity.score == 1.0
    assert (first.downstream_usefulness.passed, first.downstream_usefulness.total) == (28, 28)
    assert first.downstream_usefulness.score == 1.0
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_aggregate_reuses_each_seat_on_one_hole_without_recounting_the_bore() -> None:
    from b123d_recognisers import build_raw_recognition_result, countersink_matches_hole

    recognition = build_raw_recognition_result(_mixed_part())
    countersink_ids = {id(seat) for seat in recognition.countersinks}
    attached_ids = {id(hole.csink) for hole in recognition.holes if hole.csink is not None}

    assert len(recognition.countersinks) == len(recognition.holes) == 2
    assert attached_ids == countersink_ids
    assert all(
        hole.csink is not None and countersink_matches_hole(hole.csink, hole)
        for hole in recognition.holes
    )
    observed = _default_observers()["countersinks"](_mixed_part())
    assert len(observed) == 2
    assert {tuple(sorted(fact.parameters.items())) for fact in observed} == {
        (
            ("depth", 4.0),
            ("drill_diameter", 6.0),
            ("included_angle", 90.0),
            ("major_diameter", 14.0),
        ),
        (
            ("depth", 4.0),
            ("drill_diameter", 8.0),
            ("included_angle", 90.0),
            ("major_diameter", 16.0),
        ),
    }


def test_swapped_nested_seats_fail_closed_at_every_downstream_boundary(monkeypatch) -> None:
    from b123d_recognisers import countersink_matches_hole

    from draftwright.drawing import Drawing

    original = Drawing.recognition

    def with_swapped_seats(self):
        recognition = original(self)
        assert recognition is not None
        holes = tuple(recognition.holes)
        assert len(holes) == 2
        swapped = (
            replace(holes[0], csink=holes[1].csink),
            replace(holes[1], csink=holes[0].csink),
        )
        assert all(
            hole.csink is not None and not countersink_matches_hole(hole.csink, hole)
            for hole in swapped
        )
        return replace(recognition, holes=swapped)

    monkeypatch.setattr(Drawing, "recognition", with_swapped_seats)
    observed = _default_observers()["countersinks"](_mixed_part())

    assert len(observed) == 2
    assert all(set(fact.downstream.values()) == {"unknown"} for fact in observed)


def test_duplicate_nested_seat_attachment_fails_exact_multiplicity() -> None:
    from b123d_recognisers import build_raw_recognition_result

    recognition = build_raw_recognition_result(_mixed_part())
    first = recognition.holes[0]
    duplicated = replace(recognition, holes=(first, first))

    assert _countersink_sites(recognition.countersinks, duplicated) == [(), ()]


def test_duplicate_provider_inventory_record_fails_exact_multiplicity() -> None:
    from b123d_recognisers import build_raw_recognition_result

    recognition = build_raw_recognition_result(_single_part())
    seat = recognition.countersinks[0]

    assert _countersink_sites((seat, seat), recognition) == [(), ()]


def test_nested_seat_outside_the_provider_inventory_invalidates_every_site() -> None:
    from b123d_recognisers import build_raw_recognition_result

    recognition = build_raw_recognition_result(_single_part())
    hole = recognition.holes[0]
    assert hole.csink is not None
    foreign_seat = replace(hole.csink)
    corrupted = replace(recognition, holes=(*recognition.holes, replace(hole, csink=foreign_seat)))

    assert _countersink_sites(recognition.countersinks, corrupted) == [()]


def test_disconnected_coaxial_seats_cannot_share_one_canonical_hole_site() -> None:
    from draftwright import build_drawing

    part = _coaxial_compound_part()
    drawing = build_drawing(part, page="A3")
    recognition = drawing.recognition()
    assert recognition is not None

    assert len(recognition.countersinks) == len(recognition.holes) == 2
    assert len([feature for feature in drawing.model().features if feature.kind == "hole"]) == 1
    observed = _default_observers()["countersinks"](part)
    assert len(observed) == 2
    assert all(set(fact.downstream.values()) == {"unknown"} for fact in observed)


def test_equal_seats_still_remain_two_physical_observations() -> None:
    observed = _default_observers()["countersinks"](
        import_step(CORPUS.parent / "countersink-topology-a.step")
    )

    assert len(observed) == 2
    assert all(
        fact.parameters
        == {
            "major_diameter": 14.0,
            "drill_diameter": 6.0,
            "included_angle": 90.0,
            "depth": 4.0,
        }
        for fact in observed
    )


def test_every_countersink_boundary_is_observed_supported_on_the_real_public_path() -> None:
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert _states(boundary) == {"supported"}


def test_removing_the_seat_from_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_countersinks(self):
        model = original(self)
        return replace(
            model,
            features=[
                replace(feature, csink=None)
                if feature.kind in {"hole", "pattern"} and feature.csink is not None
                else feature
                for feature in model.features
            ],
        )

    monkeypatch.setattr(Drawing, "model", without_countersinks)
    assert _states("ir_adapter") == {"unknown"}


def test_corrupting_public_countersink_declaration_loses_declaration_credit(
    monkeypatch,
) -> None:
    from draftwright.sheet import Sheet

    original = Sheet.hole

    def wrong_major_diameter(self, obj=None, **kw):
        if kw.get("csink") is not None:
            major, angle = kw["csink"]
            kw["csink"] = (major + 1.0, angle)
        return original(self, obj, **kw)

    monkeypatch.setattr(Sheet, "hole", wrong_major_diameter)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_countersink_argument_loses_generated_code_credit(
    monkeypatch,
) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_countersink_argument(*args, **kwargs):
        source = original(*args, **kwargs)
        damaged = source.replace(", csink=(14, 90)", "")
        assert damaged != source, "fixture must emit the countersink argument"
        return damaged

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_countersink_argument)
    assert _states("ir_adapter") == {"supported"}
    assert _states("generated_code") == {"unknown"}


def test_a_boundary_with_missing_per_seat_outcomes_fails_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_countersink_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_removing_placed_countersink_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_countersink_callout(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(
            name
            for name in drawing.annotations()
            if {
                key["parameter_id"]
                for key in drawing.measurement_keys(name)
                if key["parameter_id"].startswith("countersink.")
            }
        )
        drawing.remove(name)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_countersink_callout)
    assert _states("drawing_consumer") == {"unsupported"}


def test_wrong_placed_countersink_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_wrong_countersink_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(
            name
            for name in drawing.annotations()
            if any(
                key["parameter_id"] == "countersink.diameter"
                for key in drawing.measurement_keys(name)
            )
        )
        annotation = drawing.registry.named(name)
        assert "14" in annotation.label
        annotation.label = annotation.label.replace("14", "99")
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_wrong_countersink_ink)
    assert _states("drawing_consumer") == {"unsupported"}


def test_role_swapped_countersink_ink_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_role_swapped_countersink_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(
            name
            for name in drawing.annotations()
            if any(
                key["parameter_id"] == "countersink.diameter"
                for key in drawing.measurement_keys(name)
            )
        )
        annotation = drawing.registry.named(name)
        assert "⌵ ⌀14 × 90°" in annotation.label
        annotation.label = annotation.label.replace("⌵ ⌀14 × 90°", "⌵ ⌀90 × 14°")
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_role_swapped_countersink_ink)
    assert _states("drawing_consumer") == {"unsupported"}


@pytest.mark.parametrize("replacement", ["90", "90abc°"])
def test_missing_or_corrupt_countersink_angle_unit_loses_drawing_credit(
    monkeypatch, replacement: str
) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_malformed_countersink_angle(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(
            name
            for name in drawing.annotations()
            if any(
                key["parameter_id"] == "countersink.angle"
                for key in drawing.measurement_keys(name)
            )
        )
        annotation = drawing.registry.named(name)
        assert "⌵ ⌀14 × 90°" in annotation.label
        annotation.label = annotation.label.replace("⌵ ⌀14 × 90°", f"⌵ ⌀14 × {replacement}")
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_malformed_countersink_angle)
    assert _states("drawing_consumer") == {"unsupported"}


def test_unparseable_claim_expectation_cannot_receive_role_specific_ink_credit() -> None:
    claim = SimpleNamespace(
        measurement=SimpleNamespace(parameter="countersink.diameter"),
        expected=("not-a-number",),
    )
    annotation = SimpleNamespace(label="⌵ ⌀14 × 90°")

    assert not _countersink_claim_has_role_specific_ink(claim, annotation)


def test_deleting_provider_seats_cannot_shrink_the_independent_denominator(
    monkeypatch,
) -> None:
    import draftwright.analysis as analysis

    original = analysis._result_from_evidence

    def without_countersinks(*args, **kwargs):
        result = original(*args, **kwargs)
        holes = tuple(replace(hole, csink=None) for hole in result.holes)
        return replace(result, countersinks=(), holes=holes, hole_patterns=())

    monkeypatch.setattr(analysis, "_result_from_evidence", without_countersinks)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 7
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


def test_two_sided_bore_keeps_the_second_seat_explicitly_unverifiable() -> None:
    from draftwright import build_drawing
    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    drawing = build_drawing(_two_face_part(), page="A3")
    recognition = drawing.recognition()
    assert recognition is not None
    observed = _default_observers()["countersinks"](_two_face_part())

    assert len(observed) == 2
    assert {fact.downstream["drawing_consumer"] for fact in observed} == {
        "supported",
        "unknown",
    }
    outcomes = hole_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )
    unverifiable = [
        outcome
        for outcome in outcomes
        if outcome.state == "unverifiable" and outcome.parameter_id.startswith("countersink.")
    ]
    assert {outcome.parameter_id for outcome in unverifiable} == {
        "countersink.angle",
        "countersink.diameter",
    }
    assert all(outcome.members == () for outcome in unverifiable)


def test_countersink_observer_fails_closed_when_build_or_recognition_is_unavailable(
    monkeypatch,
) -> None:
    import draftwright.builder as builder

    def broken_build(*_args, **_kwargs):
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(builder, "build_drawing", broken_build)
    observer = _default_observers()["countersinks"]
    assert observer(_single_part()) == ()

    class DrawingWithoutRecognition:
        def recognition(self):
            return None

    monkeypatch.setattr(
        builder, "build_drawing", lambda *_args, **_kwargs: DrawingWithoutRecognition()
    )
    assert observer(_single_part()) == ()


@pytest.mark.parametrize("corruption", ["nonnumeric-location", "missing-axis"])
def test_malformed_public_countersink_records_become_missed_instead_of_aborting(
    monkeypatch, corruption: str
) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.recognition

    def with_malformed_record(self):
        recognition = original(self)
        assert recognition is not None
        countersinks = tuple(recognition.countersinks)
        if not countersinks:
            return recognition
        first = countersinks[0]
        if corruption == "nonnumeric-location":
            malformed = replace(first, location=("bad", first.location[1], first.location[2]))
        else:
            malformed = SimpleNamespace(
                location=first.location,
                major_diameter=first.major_diameter,
                drill_diameter=first.drill_diameter,
                included_angle=first.included_angle,
                depth=first.depth,
            )
        return replace(recognition, countersinks=(malformed, *countersinks[1:]))

    monkeypatch.setattr(Drawing, "recognition", with_malformed_record)

    assert _default_observers()["countersinks"](_single_part()) == ()
    damaged = evaluate_step_corpus(load_corpus(CORPUS))
    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 7
    assert damaged.detection.false_positives == 0


@pytest.mark.parametrize(
    "fixture",
    ["countersink-external-cone.step", "countersink-deburr.step"],
)
def test_negative_controls_do_not_create_hole_requirements(fixture: str) -> None:
    from draftwright import build_drawing
    from draftwright.linting.hole_coverage import hole_requirement_outcomes
    from draftwright.model.compiled import compile_dimensions

    drawing = build_drawing(import_step(CORPUS.parent / fixture), auto_dims=False)
    recognition = drawing.recognition()
    assert recognition is not None
    assert recognition.countersinks == ()
    outcomes = hole_requirement_outcomes(
        recognition,
        drawing.model().features,
        drawing.registry,
        compile_dimensions(drawing.model()).diagnostics,
    )
    assert not [outcome for outcome in outcomes if outcome.parameter_id.startswith("countersink.")]
