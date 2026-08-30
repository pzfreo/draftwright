"""#1372 — pocket-pattern completeness owns groups, not their member pockets."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from build123d import Box, Pos, import_step

from draftwright.evaluation.step_analysis import (
    _default_observers,
    _pocket_pattern_pitch_gaps,
    evaluate_step_corpus,
    load_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "evaluation" / "corpus-pocket-patterns-v1.json"


def _grid_part():
    part = Box(140, 110, 20)
    for x in (-24, 0, 24):
        for y in (-16, 16):
            part -= Pos(x, y, 7) * Box(12, 8, 6)
    return part


def _states(boundary: str) -> set[str]:
    observed = _default_observers()["pocket-patterns"](_grid_part())
    assert len(observed) == 1, "fixture must produce one pocket-grid observation"
    return {fact.downstream[boundary] for fact in observed}


def _annotation_for_parameter(drawing, parameter: str) -> str:
    return next(
        name
        for name in drawing.annotations()
        if any(
            str(getattr(measurement, "parameter", "")) == parameter
            for measurement in drawing.registry.measurement_of(name)
        )
    )


def test_versioned_pocket_pattern_corpus_covers_every_required_case_class() -> None:
    corpus = load_corpus(CORPUS)

    assert (corpus.corpus_version, corpus.metric_version) == ("1.1.0", 1)
    assert corpus.scope == ("pocket-patterns",)
    assert len(corpus.cases) == 7
    assert sum(len(case.expected) for case in corpus.cases) == 4
    tags = {tag for case in corpus.cases for tag in case.classification.split("+")}
    assert {
        "ambiguous",
        "compound",
        "negative",
        "positive",
        "topology-order-variant",
    } <= tags
    assert all(case.provenance["author"] for case in corpus.cases)
    assert all(case.provenance["license"] == "CC0-1.0" for case in corpus.cases)
    expected = [fact for case in corpus.cases for fact in case.expected]
    linear = next(fact for fact in expected if fact.identity["kind"].value == "linear")
    rotated = next(
        fact
        for fact in expected
        if fact.identity["kind"].value == "grid" and fact.parameters["angle"].value != 0.0
    )
    assert linear.parameters["direction"].value == pytest.approx(
        (math.cos(math.radians(30)), math.sin(math.radians(30)), 0.0), abs=0.01
    )
    assert rotated.parameters["angle"].value == 30.0


def test_real_pocket_pattern_corpus_scores_all_layers_and_topology_variants() -> None:
    corpus = load_corpus(CORPUS)

    first = evaluate_step_corpus(corpus)
    second = evaluate_step_corpus(corpus)

    assert first == second
    assert first.detection.recall == 1.0
    assert first.detection.false_positive_rate == 0.0
    assert first.detection.matched == 4
    assert first.parameter_fidelity.passed == first.parameter_fidelity.total == 41
    assert first.downstream_usefulness.passed == first.downstream_usefulness.total == 16
    assert first.conformant_cases == first.complete_cases == len(corpus.cases)
    variants = [case for case in first.cases if "topology" in case.case_id]
    assert len(variants) == 2
    assert variants[0].detection == variants[1].detection
    assert variants[0].parameter_fidelity == variants[1].parameter_fidelity
    assert variants[0].downstream_usefulness == variants[1].downstream_usefulness


def test_pattern_projection_owns_members_once_and_lone_pockets_stay_disjoint() -> None:
    from b123d_recognisers import build_recognition_result

    from draftwright.builder import build_drawing
    from draftwright.linting.pocket_coverage import pocket_requirement_outcomes

    for part, pocket_count, pattern_count in (
        (_grid_part(), 6, 1),
        (import_step(CORPUS.parent / "pocket-pattern-topology-a.step"), 7, 1),
    ):
        recognition = build_recognition_result(part)
        assert len(recognition.pockets) == pocket_count
        assert len(recognition.pocket_patterns) == pattern_count
        aggregate = {id(pocket) for pocket in recognition.pockets}
        allocated: set[int] = set()
        for pattern in recognition.pocket_patterns:
            member_ids = {id(pocket) for pocket in pattern.pockets}
            assert member_ids <= aggregate
            assert not allocated & member_ids
            allocated.update(member_ids)
        drawing = build_drawing(part)
        lone = pocket_requirement_outcomes(
            drawing.recognition(), drawing.model().features, drawing.registry
        )
        assert len(lone) == (pocket_count - len(allocated)) * 5

    (fact,) = _default_observers()["pocket-patterns"](_grid_part())
    assert fact.parameters == {
        "count": 6,
        "width": 8.0,
        "length": 12.0,
        "depth": 6.0,
        "edge_anchored": False,
        "center": (0.0, 0.0, 7.0),
        "rows": 2,
        "cols": 3,
        "row_pitch": 32.0,
        "col_pitch": 24.0,
        "angle": 0.0,
    }


def test_pattern_requirement_ledger_tracks_count_size_pitch_and_both_locations() -> None:
    from draftwright.builder import build_drawing
    from draftwright.linting.pocket_pattern_coverage import (
        pocket_pattern_requirement_outcomes,
    )

    drawing = build_drawing(_grid_part())
    outcomes = pocket_pattern_requirement_outcomes(
        drawing.recognition(), drawing.model().features, drawing.registry
    )

    assert {outcome.parameter_id for outcome in outcomes} == {
        "grouping.count",
        "pocket_width.length",
        "pocket_length.length",
        "pocket_depth.length",
        "grid_pitch.length.row",
        "grid_pitch.length.col",
        "location_pocket_pattern.location.x",
        "location_pocket_pattern.location.y",
    }
    assert {outcome.state for outcome in outcomes} == {"placed"}
    assert {outcome.member_count for outcome in outcomes} == {6}
    completeness = drawing.lint_summary()["quality"]["completeness"]
    assert completeness["by_family"]["pocket_patterns"] == 8
    assert "pocket_patterns" not in completeness["unscored_recognized_families"]


def test_pattern_ledger_fail_closed_states_and_canonical_direction_helpers() -> None:
    from draftwright.builder import build_drawing
    from draftwright.linting.pocket_pattern_coverage import (
        _parameter_ids,
        _state,
        _unoriented_direction,
        lint_pocket_pattern_coverage,
        pocket_pattern_requirement_outcomes,
    )
    from draftwright.registry import AnnotationRegistry

    drawing = build_drawing(_grid_part())
    (feature,) = [item for item in drawing.model().features if item.kind == "pocket_pattern"]
    empty = AnnotationRegistry()
    common = {
        "point": feature.frame.origin,
        "member_count": feature.count,
        "placed": set(),
        "locations": {},
        "counts": {},
        "satisfied": set(),
        "suppressed": set(),
        "dropped": set(),
        "registry": empty,
    }

    assert _unoriented_direction(None) is None
    assert _unoriented_direction((0, 0, 0)) is None
    assert _unoriented_direction((0, -2, 0)) == (0.0, 1.0, 0.0)
    assert _parameter_ids(object()) is None
    assert (
        _parameter_ids(
            SimpleNamespace(
                parameters=lambda: (),
                pattern="linear",
                frame=SimpleNamespace(axis="z"),
                LOCATION_STEM="location_pocket_pattern",
            )
        )
        is None
    )
    assert _parameter_ids(replace(feature, frame=replace(feature.frame, axis="x"))) is None

    def state(parameter: str, **changes):
        arguments = {**common, **changes}
        return _state(feature, parameter, **arguments)

    assert state("grouping.count", satisfied={(feature, "grouping.count")}) == (
        "satisfied_by_structured_note"
    )
    assert state("grouping.count", suppressed={(feature, "pocket_width.length")}) == "suppressed"
    assert state("grouping.count", dropped={(feature, "pocket_depth.length")}) == "dropped"
    assert (
        state(
            "location_pocket_pattern.location.x",
            satisfied={(feature, "location_pocket_pattern.location")},
        )
        == "satisfied_by_structured_note"
    )
    assert state("pitch.length", satisfied={(feature, "pitch.length")}) == (
        "satisfied_by_structured_note"
    )
    assert state("pitch.length", suppressed={(feature, "pitch.length")}) == "suppressed"
    assert state("pitch.length", dropped={(feature, "pitch.length")}) == "dropped"

    unmeasured = AnnotationRegistry()
    unmeasured.add(object(), "unmeasured", "plan", feature=feature)
    assert state("pitch.length", registry=unmeasured) == "unverifiable"
    assert pocket_pattern_requirement_outcomes(None, (), empty) == []
    with pytest.raises(TypeError, match="requires the run's RecognitionResult"):
        pocket_pattern_requirement_outcomes(object(), (), empty)

    issues = lint_pocket_pattern_coverage(
        _grid_part(),
        recognition=drawing.recognition(),
        features=(),
        registry=empty,
    )
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].code == "pocket_pattern_requirement_unverifiable"
    assembly_issues = lint_pocket_pattern_coverage(
        _grid_part(),
        recognition=drawing.recognition(),
        features=(),
        registry=empty,
        assembly=True,
    )
    assert len(assembly_issues) == 1
    assert assembly_issues[0].severity == "info"


def test_underside_pattern_survives_declaration_and_generated_code() -> None:
    from draftwright.builder import build_drawing
    from draftwright.sheet_emit import emit_sheet_script

    part = import_step(CORPUS.parent / "pocket-pattern-topology-a.step")
    drawing = build_drawing(part)
    (feature,) = [item for item in drawing.model().features if item.kind == "pocket_pattern"]

    assert feature.member.open_sign == -1
    source = emit_sheet_script(
        drawing.model(), "part", "evidence", title="EVIDENCE", number="EVIDENCE"
    )
    assert "sheet.pocket_pattern(" in source
    assert "open_sign=-1" in source
    (fact,) = _default_observers()["pocket-patterns"](part)
    assert fact.identity["open_sign"] == -1
    assert set(fact.downstream.values()) == {"supported"}


def test_every_pocket_pattern_boundary_is_observed_on_the_real_public_path() -> None:
    for boundary in ("ir_adapter", "dsl_declaration", "generated_code", "drawing_consumer"):
        assert _states(boundary) == {"supported"}


def test_pocket_pattern_observer_uses_one_build_owned_recognition_aggregate(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_raw_recognition_result", counted)
    assert _default_observers()["pocket-patterns"](_grid_part())
    assert calls == 1


def test_removing_patterns_from_built_ir_loses_ir_adapter_credit(monkeypatch) -> None:
    from draftwright.drawing import Drawing

    original = Drawing.model

    def without_patterns(self):
        model = original(self)
        return replace(
            model,
            features=[feature for feature in model.features if feature.kind != "pocket_pattern"],
        )

    monkeypatch.setattr(Drawing, "model", without_patterns)
    assert _states("ir_adapter") == {"unknown"}


def test_missing_per_pattern_boundary_outcomes_fail_closed(monkeypatch) -> None:
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, "_pocket_pattern_model_outcomes", lambda *_args: [])
    assert _states("ir_adapter") == {"unknown"}


def test_observer_fails_closed_when_build_or_recognition_is_unavailable(monkeypatch) -> None:
    import draftwright.builder as builder

    def broken_build(*_args, **_kwargs):
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(builder, "build_drawing", broken_build)
    observer = _default_observers()["pocket-patterns"]
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

    original = Sheet.pocket_pattern

    def wrong_grid(self, member, **kwargs):
        row, col = kwargs["grid"]
        kwargs["grid"] = (row + 1.0, col)
        return original(self, member, **kwargs)

    monkeypatch.setattr(Sheet, "pocket_pattern", wrong_grid)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"unknown"}


def test_deleting_generated_pattern_lines_loses_generated_code_credit(monkeypatch) -> None:
    import draftwright.sheet_emit as sheet_emit

    original = sheet_emit.emit_sheet_script

    def without_pattern_lines(*args, **kwargs):
        source = original(*args, **kwargs)
        return "\n".join(
            f"# deleted by boundary mutation: {line}" if "sheet.pocket_pattern(" in line else line
            for line in source.splitlines()
        )

    monkeypatch.setattr(sheet_emit, "emit_sheet_script", without_pattern_lines)
    assert _states("ir_adapter") == {"supported"}
    assert _states("dsl_declaration") == {"supported"}
    assert _states("generated_code") == {"unknown"}


def test_removing_grouped_size_callout_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_callout(*args, **kwargs):
        drawing = original(*args, **kwargs)
        drawing.remove(_annotation_for_parameter(drawing, "pocket_width.length"))
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_callout)
    assert _states("ir_adapter") == {"supported"}
    assert _states("drawing_consumer") == {"unsupported"}


def test_removing_one_grid_pitch_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_pitch(*args, **kwargs):
        drawing = original(*args, **kwargs)
        drawing.remove(_annotation_for_parameter(drawing, "grid_pitch.length.row"))
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_pitch)
    assert _states("drawing_consumer") == {"unsupported"}


def test_severing_one_directional_location_fact_loses_drawing_credit(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def without_x_location(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(
            name
            for name, annotation in drawing.registry.iter_named()
            if any(
                fact[1] == "location_pocket_pattern.location.x"
                for fact in getattr(annotation, "covers_hole_locations", ())
            )
        )
        drawing.registry.named(name).covers_hole_locations = ()
        return drawing

    monkeypatch.setattr(builder, "build_drawing", without_x_location)
    assert _states("drawing_consumer") == {"unsupported"}


def test_malformed_location_metadata_is_ignored_without_hiding_valid_evidence(monkeypatch) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def with_malformed_fact(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = next(
            name
            for name, annotation in drawing.registry.iter_named()
            if getattr(annotation, "covers_hole_locations", ())
        )
        annotation = drawing.registry.named(name)
        annotation.covers_hole_locations = (("malformed",), *annotation.covers_hole_locations)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", with_malformed_fact)
    assert _states("drawing_consumer") == {"supported"}


@pytest.mark.parametrize("corruption", ["metadata", "ink"])
def test_wrong_group_count_metadata_or_ink_loses_drawing_credit(
    monkeypatch, corruption: str
) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def wrong_count(*args, **kwargs):
        drawing = original(*args, **kwargs)
        name = _annotation_for_parameter(drawing, "pocket_width.length")
        callout = drawing.registry.named(name)
        assert callout.covers_count == 6 and callout.label.startswith("6× ")
        if corruption == "metadata":
            callout.covers_count = 5
        else:
            callout.label = callout.label.replace("6× ", "5× ", 1)
        return drawing

    monkeypatch.setattr(builder, "build_drawing", wrong_count)
    assert _states("drawing_consumer") == {"unsupported"}


@pytest.mark.parametrize("corruption", ["size", "pitch_nominal", "pitch_interval", "location"])
def test_wrong_size_pitch_or_location_ink_loses_drawing_credit(
    monkeypatch, corruption: str
) -> None:
    import draftwright.builder as builder

    original = builder.build_drawing

    def wrong_ink(*args, **kwargs):
        drawing = original(*args, **kwargs)
        size = drawing.registry.named(_annotation_for_parameter(drawing, "pocket_width.length"))
        pitch = drawing.registry.named(_annotation_for_parameter(drawing, "grid_pitch.length.col"))
        location_name = next(
            name
            for name, annotation in drawing.registry.iter_named()
            if any(
                fact[1] == "location_pocket_pattern.location.y"
                for fact in getattr(annotation, "covers_hole_locations", ())
            )
        )
        if corruption == "size":
            assert size.label.startswith("6× 8 × ")
            size.label = size.label.replace("6× 8 × ", "6× 9 × ", 1)
        elif corruption == "pitch_nominal":
            interval, _nominal = pitch.label.split(" ", 1)
            pitch.label = f"{interval} 999"
        elif corruption == "pitch_interval":
            _interval, nominal = pitch.label.split(" ", 1)
            pitch.label = f"9× {nominal}"
        else:
            drawing.registry.named(location_name).label = "999"
        return drawing

    monkeypatch.setattr(builder, "build_drawing", wrong_ink)
    assert _states("drawing_consumer") == {"unsupported"}


def test_legitimate_pitch_tolerance_survives_exact_drawing_observation() -> None:
    from draftwright import Sheet, SoftDeprecationWarning
    from draftwright.evaluation.step_analysis import _pocket_pattern_drawing_outcomes
    from draftwright.model import pocket

    part = _grid_part()
    sheet = Sheet(part)
    with pytest.warns(SoftDeprecationWarning):
        sheet.auto_dimensions()
    member = pocket(
        width=8,
        length=12,
        depth=6,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        at=(-24, -16, 7),
        lo=-30,
        hi=-18,
        w_center=-16,
    )
    sheet.pocket_pattern(
        member,
        kind="grid",
        count=6,
        at=(0, 0, 7),
        grid=(32, 24),
        rows=2,
        cols=3,
        angle=0,
    ).tolerance(0.2, on="grid_pitch")
    drawing = sheet.build()
    drawing.lint()  # declared critique populates the one build-owned recognition aggregate

    labels = {
        drawing.registry.named(name).label
        for name in drawing.registry.names()
        if name.startswith("dim_pocketpat_pitch")
    }
    assert labels == {"1× 32 ±0.2", "2× 24 ±0.2"}
    assert _pocket_pattern_drawing_outcomes(
        tuple(drawing.recognition().pocket_patterns), drawing
    ) == ["supported"]

    angle = math.radians(30)
    direction = (math.cos(angle), math.sin(angle), 0.0)
    centres = tuple(
        (distance * direction[0], distance * direction[1], 7.0) for distance in (-45, -15, 15, 45)
    )
    linear_part = Box(180, 140, 20)
    for x, y, z in centres:
        linear_part -= Pos(x, y, z) * Box(12, 8, 6)
    linear_sheet = Sheet(linear_part)
    with pytest.warns(SoftDeprecationWarning):
        linear_sheet.auto_dimensions()
    first_x, first_y, first_z = centres[0]
    linear_member = pocket(
        width=8,
        length=12,
        depth=6,
        long_axis="x",
        width_axis="y",
        depth_axis="z",
        at=(first_x, first_y, first_z),
        lo=first_x - 6,
        hi=first_x + 6,
        w_center=first_y,
    )
    linear_sheet.pocket_pattern(
        linear_member,
        kind="linear",
        count=4,
        at=(0, 0, 7),
        pitch=30,
        direction=direction,
    ).tolerance(0.2, on="pitch")
    linear_drawing = linear_sheet.build()
    linear_drawing.lint()

    assert {
        linear_drawing.registry.named(name).label
        for name in linear_drawing.registry.names()
        if name.startswith("dim_pocketpat_pitch")
    } == {"3× 30 ±0.2"}
    assert _pocket_pattern_drawing_outcomes(
        tuple(linear_drawing.recognition().pocket_patterns), linear_drawing
    ) == ["supported"]


def test_invalid_linear_direction_cannot_justify_collapsed_pitch_tolerance() -> None:
    feature = SimpleNamespace(
        members=((0.0, 0.0, 0.0), (30.0, 0.0, 0.0)),
        direction=(0.0, 0.0, 0.0),
    )

    assert _pocket_pattern_pitch_gaps(feature, "pitch.length", 30.0) == ()


def test_diagonal_dimension_gate_checks_label_ink_in_both_directions() -> None:
    from draftwright.annotations._common import annotation_ink_clear

    class Drawing:
        def __init__(self, annotation, owner="plan"):
            self.annotation = annotation
            self.owner = owner

        def iter_annotations(self):
            return iter((("existing", self.annotation),))

        def view_of(self, _name):
            return self.owner

    clean_candidate = SimpleNamespace(
        label_bbox=(10.0, 10.0, 20.0, 14.0),
        segments=(((5.0, 5.0), (25.0, 5.0)),),
    )
    clear = SimpleNamespace(
        label_bbox=(30.0, 20.0, 40.0, 24.0),
        segments=(((30.0, 18.0), (40.0, 18.0)),),
    )
    assert annotation_ink_clear(Drawing(clear), clean_candidate, view="plan")
    assert not annotation_ink_clear(
        Drawing(clear), SimpleNamespace(label_bbox=None, segments=()), view="plan"
    )

    overlapping_label = SimpleNamespace(
        label_bbox=(15.0, 12.0, 25.0, 16.0),
        segments=(((15.0, 18.0), (25.0, 18.0)),),
    )
    assert not annotation_ink_clear(Drawing(overlapping_label), clean_candidate, view="plan")

    candidate_crosses_existing = SimpleNamespace(
        label_bbox=(10.0, 10.0, 20.0, 14.0),
        segments=(((5.0, 22.0), (45.0, 22.0)),),
    )
    assert not annotation_ink_clear(Drawing(clear), candidate_crosses_existing, view="plan")

    existing_crosses_candidate = SimpleNamespace(
        label_bbox=(30.0, 20.0, 40.0, 24.0),
        segments=(((5.0, 12.0), (25.0, 12.0)),),
    )
    assert not annotation_ink_clear(
        Drawing(existing_crosses_candidate), clean_candidate, view="plan"
    )
    assert annotation_ink_clear(
        Drawing(existing_crosses_candidate, owner="front"), clean_candidate, view="plan"
    )

    crossing_shaft = SimpleNamespace(
        label_bbox=(30.0, 20.0, 40.0, 24.0),
        segments=(((15.0, 0.0), (15.0, 10.0)),),
    )
    assert not annotation_ink_clear(Drawing(crossing_shaft), clean_candidate, view="plan")

    page_frame = SimpleNamespace(label_bbox=None, segments=(), is_sheet_frame=True)
    assert annotation_ink_clear(Drawing(page_frame), clean_candidate, view="plan")

    class BrokenFixedInk:
        label_bbox = None

        @property
        def segments(self):
            raise RuntimeError("unreadable fixed ink")

    assert not annotation_ink_clear(Drawing(BrokenFixedInk()), clean_candidate, view="plan")

    malformed_label = SimpleNamespace(
        label_bbox="not a box",
        segments=(((30.0, 18.0), (40.0, 18.0)),),
    )
    assert not annotation_ink_clear(Drawing(malformed_label), clean_candidate, view="plan")

    class UnreadableLabel:
        segments = (((30.0, 18.0), (40.0, 18.0)),)

        @property
        def label_bbox(self):
            raise RuntimeError("unreadable label")

    assert not annotation_ink_clear(Drawing(UnreadableLabel()), clean_candidate, view="plan")
    assert not annotation_ink_clear(Drawing(clear), UnreadableLabel(), view="plan")


def test_diagonal_dimension_gate_keeps_label_less_table_furniture_conservative() -> None:
    from draftwright import build_drawing
    from draftwright.annotations._common import annotation_ink_clear

    drawing = build_drawing(Box(30, 20, 8), page="A4", auto_dims=False)
    table = drawing.add_table([("A", "B"), ("123", "456")], prefer="bl")
    assert table is not None
    box = table.bounding_box()
    middle_y = (box.min.Y + box.max.Y) / 2
    candidate = SimpleNamespace(
        label_bbox=(box.max.X + 5.0, box.max.Y + 5.0, box.max.X + 15.0, box.max.Y + 9.0),
        segments=(((box.min.X - 5.0, middle_y), (box.max.X + 5.0, middle_y)),),
    )

    assert not annotation_ink_clear(drawing, candidate)


def test_diagonal_dimension_gate_keeps_legacy_untight_labels_conservative(monkeypatch) -> None:
    from draftwright.annotations import _common

    class LegacyDimension:
        label_bbox = (10.0, 10.0, 20.0, 20.0)
        measured_length = 30.0
        segments = (((0.0, 0.0), (30.0, 30.0)),)

    class Drawing:
        def iter_annotations(self):
            return iter((("legacy", LegacyDimension()),))

        def view_of(self, _name):
            return "plan"

    monkeypatch.setattr(_common, "Dimension", LegacyDimension)
    candidate = SimpleNamespace(
        label_bbox=(30.0, 30.0, 40.0, 34.0),
        segments=(((10.0, 12.0), (11.0, 12.0)),),
    )

    assert not _common.annotation_ink_clear(Drawing(), candidate, view="plan")


@pytest.mark.parametrize(
    "drop_code,invalid_real_geometry",
    [
        ("pocket_pattern_dim_dropped", "component_boxes"),
        ("hole_pattern_dim_dropped", "geometry_box"),
    ],
)
def test_pitch_fallback_rejects_unverifiable_real_geometry(
    monkeypatch, drop_code: str, invalid_real_geometry: str
) -> None:
    from build123d import Draft

    from draftwright.annotations import holes
    from draftwright.annotations._common import PlacementContext
    from draftwright.linting.coverage import CoverageState
    from draftwright.registry import AnnotationRegistry

    registry = AnnotationRegistry()
    drawing = SimpleNamespace(
        draft=Draft(),
        registry=registry,
        coverage=CoverageState(),
        items=[],
        box_cache={},
        iter_annotations=lambda: iter(()),
        view_of=lambda _name: None,
        annotations_in_view=lambda _view: (),
    )

    def point(value: float) -> SimpleNamespace:
        return SimpleNamespace(X=value, Y=value, Z=value)

    project = SimpleNamespace(
        plan_x=float,
        plan_y=float,
        front_x=float,
        front_z=float,
        side_x=float,
        side_z=float,
    )
    zones = SimpleNamespace(left=None, right=None, above=None, below=None)
    analysis = SimpleNamespace(
        margin=10.0,
        PAGE_W=210.0,
        PAGE_H=297.0,
        bb=SimpleNamespace(min=point(50.0), max=point(130.0)),
        proj=project,
        pv_zones=zones,
        fv_zones=zones,
        sv_zones=zones,
    )
    context = PlacementContext(
        registry=registry,
        coverage=drawing.coverage,
        items=drawing.items,
    )
    monkeypatch.setattr(
        holes,
        "_dim",
        lambda *_args, **_kwargs: SimpleNamespace(
            label_bbox=(20.0, 20.0, 30.0, 24.0),
            segments=(((15.0, 18.0), (35.0, 18.0)),),
        ),
    )
    monkeypatch.setattr(
        holes,
        "dim_footprint",
        lambda *_args, **_kwargs: (20.0, 20.0, 30.0, 24.0),
    )
    if invalid_real_geometry == "component_boxes":
        monkeypatch.setattr(holes, "annotation_obstacle_boxes", lambda *_args, **_kwargs: ())
    else:
        monkeypatch.setattr(holes, "_geom_box", lambda *_args, **_kwargs: None)

    holes._place_pitch_dim(
        drawing,
        analysis,
        "plan",
        (70.0, 70.0, 0.0),
        (110.0, 110.0, 0.0),
        2,
        "40",
        lambda location: location,
        "test_rejected_pitch",
        drop_code=drop_code,
        ctx=context,
    )

    assert "test_rejected_pitch" not in registry.names()
    assert [issue.code for issue in registry.issues] == [drop_code]


def test_deleting_provider_patterns_cannot_shrink_independent_denominator(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def without_patterns(*args, **kwargs):
        result = original(*args, **kwargs)
        return replace(result, pocket_patterns=())

    monkeypatch.setattr(analysis, "build_raw_recognition_result", without_patterns)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.matched == 0
    assert damaged.detection.missed == 4
    assert damaged.detection.recall == 0.0
    assert damaged.complete_cases < len(damaged.cases)


def test_weakening_provider_pitch_reduces_parameter_fidelity(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def weakened_patterns(*args, **kwargs):
        result = original(*args, **kwargs)
        changed = []
        for pattern in result.pocket_patterns:
            if hasattr(pattern, "row_pitch"):
                changed.append(replace(pattern, row_pitch=pattern.row_pitch + 1.0))
            else:
                changed.append(replace(pattern, pitch=pattern.pitch + 1.0))
        return replace(result, pocket_patterns=tuple(changed))

    monkeypatch.setattr(analysis, "build_raw_recognition_result", weakened_patterns)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.detection.false_positives == 0
    assert damaged.parameter_fidelity.passed == 37
    assert damaged.parameter_fidelity.total == 41
    assert damaged.parameter_fidelity.score == 37 / 41


def test_hardcoding_arrangement_orientation_reduces_parameter_fidelity(monkeypatch) -> None:
    import draftwright.analysis as analysis

    original = analysis.build_raw_recognition_result

    def axis_aligned_patterns(*args, **kwargs):
        result = original(*args, **kwargs)
        changed = []
        for pattern in result.pocket_patterns:
            if hasattr(pattern, "row_pitch"):
                changed.append(replace(pattern, angle=0.0))
            else:
                changed.append(replace(pattern, direction=(0.0, 1.0, 0.0)))
        return replace(result, pocket_patterns=tuple(changed))

    monkeypatch.setattr(analysis, "build_raw_recognition_result", axis_aligned_patterns)
    damaged = evaluate_step_corpus(load_corpus(CORPUS))

    assert damaged.detection.recall == 1.0
    assert damaged.parameter_fidelity.passed == 39
    assert damaged.parameter_fidelity.total == 41
    assert damaged.parameter_fidelity.score == 39 / 41


def test_deleting_pattern_declaration_cannot_shrink_quality_denominator() -> None:
    from draftwright import Sheet, build_drawing

    complete = build_drawing(_grid_part())
    sparse = Sheet(_grid_part())
    envelope = sparse.envelope()
    sparse.dimension(envelope, "width.length")

    complete_quality = complete.lint_summary()["quality"]["completeness"]
    sparse_quality = sparse.build().lint_summary()["quality"]["completeness"]

    assert complete_quality["requirements"] == sparse_quality["requirements"] == 8
    assert complete_quality["by_family"]["pocket_patterns"] == 8
    assert complete_quality["placed"] == 8
    assert sparse_quality["unverifiable"] == 8
    assert complete_quality["audited_score"] == 1.0
    assert sparse_quality["audited_score"] == 0.0
