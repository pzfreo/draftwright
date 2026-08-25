"""#1298 — topology-owned AP242 thread, tap/drill, and knurl requirements."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from build123d import Align, Axis, Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.analysis import _import_step
from draftwright.linting.pmi_coverage import lint_pmi_lowering
from draftwright.model.detect import build_part_model
from draftwright.model.ir import (
    AuthoredDimension,
    BossFeature,
    CylindricalReference,
    Frame,
    HoleFeature,
    KnurlRequirement,
    PartModel,
    PatternFeature,
    PmiFeature,
    StepFeature,
    ThreadRequirement,
)
from draftwright.model.pmi_lowering import lower_ap242_manufacturing_requirements
from draftwright.pmi import PmiExtractionReport, PmiRecord, extract_pmi_report
from draftwright.sheet_emit import emit_sheet_script

GRM03 = Path(__file__).parent / "fixtures" / "grm03_thumbwheel_drive_screw_ap242_pmi.step"
GRM03_SHA256 = "4b6462b9cc9f0d419250933bd77fb305f9cfebb7ec2b3f377008732876010a21"

EXTERNAL_TEXT = "M3 x 0.5-6g RH, full available length on nominal DIA 3 region"
INTERNAL_TEXT = (
    "M2 x 0.4-6H RH, 6 mm minimum full thread; DIA 1.6 tapping drill x 8 mm "
    "full-diameter depth; conventional 118 degree drill point"
)
KNURL_TEXT = (
    "Straight knurl, 1.0 mm pitch, full width between C0.3 chamfers, DIA 10 mm "
    "maximum after knurling; cut or formed process permitted"
)


def _manufacturing_signature(model):
    """Typed requirements plus stable canonical-owner facts for emit/rebuild parity."""
    signature = []
    for feature in model.features:
        for aspect_name, requirement_type in (
            ("thread", ThreadRequirement),
            ("knurl", KnurlRequirement),
        ):
            requirement = getattr(feature, aspect_name, None)
            if not isinstance(requirement, requirement_type):
                continue
            signature.append(
                (
                    feature.kind,
                    round(float(getattr(feature, "diameter", 0.0)), 3),
                    round(
                        float(
                            getattr(
                                feature,
                                "length",
                                getattr(feature, "depth", 0.0) or 0.0,
                            )
                        ),
                        3,
                    ),
                    aspect_name,
                    requirement,
                )
            )
    return tuple(sorted(signature, key=repr))


def _reference(*, diameter, interval, sense, axis_origin=(0.0, 0.0, 0.0)):
    return CylindricalReference(
        axis_origin=axis_origin,
        axis_direction=(1.0, 0.0, 0.0),
        radius=diameter / 2,
        axial_interval=interval,
        sense=sense,
    )


def _raw(kind, text, reference, entity):
    return PmiFeature(
        frame=Frame((0.0, 0.0, 0.0), "x"),
        pmi_kind=kind,
        value=0.0,
        label=text,
        dominant_axis="X",
        source_id=f"manufacturing_requirement:{entity}",
        part21_id=entity,
        source_category="manufacturing_requirement",
        reference_item_ids=(f"{entity}:face",),
        semantic_name=kind.replace("_", " "),
        shape_aspect_ids=(f"{entity}:aspect",),
        cylindrical_refs=(reference,),
    )


def _step(diameter, lo, hi):
    return StepFeature(
        frame=Frame(((lo + hi) / 2, 0.0, 0.0), "x"),
        length=hi - lo,
        diameter=diameter,
        span=((lo, 0.0, 0.0), (hi, 0.0, 0.0)),
    )


def _model(*features):
    return PartModel(Box(40, 20, 20).bounding_box(), "x", list(features))


def test_external_thread_uses_finite_source_topology_to_choose_one_equal_diameter_step():
    first = _step(3.0, 0.0, 10.0)
    second = _step(3.0, 12.0, 20.0)
    raw = _raw(
        "external_thread",
        EXTERNAL_TEXT,
        _reference(diameter=3.0, interval=(0.5, 9.5), sense="external"),
        "#2000",
    )

    lowered = lower_ap242_manufacturing_requirements(_model(first, second, raw))

    assert isinstance(lowered.features[0].thread, ThreadRequirement)
    assert lowered.features[0].thread.designation == "M3 x 0.5-6g RH"
    assert lowered.features[0].thread.full_available_length
    assert lowered.features[1].thread is None
    assert not any(isinstance(feature, PmiFeature) for feature in lowered.features)


def test_equal_nominal_without_matching_axis_line_remains_explicitly_unlowered():
    owner = _step(3.0, 0.0, 10.0)
    raw = _raw(
        "external_thread",
        EXTERNAL_TEXT,
        _reference(
            diameter=3.0,
            interval=(0.5, 9.5),
            sense="external",
            axis_origin=(0.0, 1.0, 0.0),
        ),
        "#2000",
    )

    lowered = lower_ap242_manufacturing_requirements(_model(owner, raw))

    assert lowered.features[0].thread is None
    fallback = next(feature for feature in lowered.features if isinstance(feature, PmiFeature))
    assert fallback.lowering_blockers == (
        "unmatched manufacturing requirement: no canonical feature matches source topology",
    )


def test_internal_thread_retains_tap_drill_and_drill_point_as_typed_values():
    hole = HoleFeature(
        frame=Frame((0.0, 0.0, 0.0), "x"),
        diameter=1.6,
        depth=8.0,
        through=False,
    )
    raw = _raw(
        "internal_thread",
        INTERNAL_TEXT,
        _reference(diameter=1.6, interval=(0.0, 8.0), sense="internal"),
        "#2004",
    )

    lowered = lower_ap242_manufacturing_requirements(_model(hole, raw))

    requirement = lowered.features[0].thread
    assert isinstance(requirement, ThreadRequirement)
    assert (
        requirement.application,
        requirement.nominal_diameter,
        requirement.pitch,
        requirement.tolerance_class,
        requirement.minimum_full_thread,
        requirement.drill_diameter,
        requirement.drill_depth,
        requirement.drill_point_angle,
    ) == ("internal", 2.0, 0.4, "6H", 6.0, 1.6, 8.0, 118.0)
    assert requirement.source_ids == ("manufacturing_requirement:#2004",)


def test_internal_minimum_full_thread_cannot_exceed_tap_drill_depth():
    hole = HoleFeature(
        frame=Frame((0.0, 0.0, 0.0), "x"),
        diameter=1.6,
        depth=8.0,
        through=False,
    )
    raw = _raw(
        "internal_thread",
        INTERNAL_TEXT.replace("6 mm minimum full thread", "9 mm minimum full thread"),
        _reference(diameter=1.6, interval=(0.0, 8.0), sense="internal"),
        "#2004",
    )

    lowered = lower_ap242_manufacturing_requirements(_model(hole, raw))

    assert lowered.features[0].thread is None
    fallback = next(feature for feature in lowered.features if isinstance(feature, PmiFeature))
    assert fallback.lowering_blockers == ("thread minimum full thread cannot exceed drill depth",)

    valid = (
        lower_ap242_manufacturing_requirements(
            _model(
                hole,
                _raw(
                    "internal_thread",
                    INTERNAL_TEXT,
                    _reference(diameter=1.6, interval=(0.0, 8.0), sense="internal"),
                    "#2004",
                ),
            )
        )
        .features[0]
        .thread
    )
    assert isinstance(valid, ThreadRequirement)
    with pytest.raises(ValueError, match="minimum full thread cannot exceed drill depth"):
        replace(valid, minimum_full_thread=9.0)


@pytest.mark.parametrize(
    ("text_depth", "source_depth", "hole_depth", "blocker"),
    [
        (7.0, 8.0, 8.0, "manufacturing requirement text disagrees with source cylinder"),
        (
            7.0,
            7.0,
            8.0,
            "unmatched manufacturing requirement: no canonical feature matches source topology",
        ),
        (
            8.0,
            8.0,
            7.0,
            "unmatched manufacturing requirement: no canonical feature matches source topology",
        ),
    ],
)
def test_internal_tap_drill_depth_must_match_text_source_cylinder_and_blind_hole(
    text_depth, source_depth, hole_depth, blocker
):
    hole = HoleFeature(
        frame=Frame((0.0, 0.0, 0.0), "x"),
        diameter=1.6,
        depth=hole_depth,
        through=False,
    )
    text = INTERNAL_TEXT.replace(
        "tapping drill x 8 mm",
        f"tapping drill x {text_depth:g} mm",
    )
    raw = _raw(
        "internal_thread",
        text,
        _reference(diameter=1.6, interval=(0.0, source_depth), sense="internal"),
        "#2004",
    )

    lowered = lower_ap242_manufacturing_requirements(_model(hole, raw))

    assert lowered.features[0].thread is None
    fallback = next(feature for feature in lowered.features if isinstance(feature, PmiFeature))
    assert fallback.lowering_blockers == (blocker,)


def test_knurl_is_a_typed_aspect_on_the_topology_owned_step():
    head = _step(10.0, 0.0, 2.0)
    raw = _raw(
        "knurl",
        KNURL_TEXT,
        _reference(diameter=10.0, interval=(0.2, 1.8), sense="external"),
        "#2008",
    )

    lowered = lower_ap242_manufacturing_requirements(_model(head, raw))

    requirement = lowered.features[0].knurl
    assert isinstance(requirement, KnurlRequirement)
    assert (
        requirement.pattern,
        requirement.pitch,
        requirement.full_width,
        requirement.edge_chamfer,
        requirement.maximum_diameter,
        requirement.processes,
    ) == ("straight", 1.0, True, 0.3, 10.0, ("cut", "formed"))
    assert requirement.text == KNURL_TEXT


def test_generated_sheet_round_trip_preserves_typed_aspects_and_source_topology():
    hole = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 1.6, 8.0, False)
    head = _step(10.0, 10.0, 12.0)
    thread = _raw(
        "internal_thread",
        INTERNAL_TEXT,
        _reference(diameter=1.6, interval=(0.0, 8.0), sense="internal"),
        "#2004",
    )
    knurl = _raw(
        "knurl",
        KNURL_TEXT,
        _reference(diameter=10.0, interval=(10.2, 11.8), sense="external"),
        "#2008",
    )
    part = Box(40, 20, 20)
    lowered = lower_ap242_manufacturing_requirements(
        PartModel(part.bounding_box(), "x", [hole, head, thread, knurl])
    )

    source = emit_sheet_script(lowered, "part", "requirements", title="P", number="N")
    namespace = {"part": part}
    body = source[: source.index("drawing = sheet.build()")]
    exec(compile(body, "<requirements-emit>", "exec"), namespace)  # noqa: S102
    restored = namespace["sheet"].model()

    restored_hole = next(feature for feature in restored.features if feature.kind == "hole")
    restored_head = next(
        feature
        for feature in restored.features
        if feature.kind == "step" and feature.diameter == 10.0
    )
    assert restored_hole.thread == lowered.features[0].thread
    assert restored_head.knurl == lowered.features[1].knurl
    assert "ThreadRequirement(" in source
    assert "KnurlRequirement(" in source
    assert "CylindricalReference(" in source


def test_typed_external_thread_renders_once_through_shared_solve_and_drops_by_owner():
    part = Cylinder(
        1.5,
        10,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).rotate(Axis.Y, 90)
    step = _step(3.0, 0.0, 10.0)
    raw = _raw(
        "external_thread",
        EXTERNAL_TEXT,
        _reference(diameter=3.0, interval=(0.0, 10.0), sense="external"),
        "#2000",
    )
    model = lower_ap242_manufacturing_requirements(
        PartModel(part.bounding_box(), "x", [step, raw])
    )

    drawing = build_drawing(part, model=model, pmi="annotate", page="A3")
    labels = {
        name: drawing.registry.named(name).label
        for name in drawing.registry.names()
        if getattr(drawing.registry.named(name), "label", "")
    }
    matches = [
        name
        for name, label in labels.items()
        if label == "ø3 M3 x 0.5-6g RH, FULL AVAILABLE LENGTH"
    ]
    assert matches == ["m_dia_x0"]
    assert drawing.registry.measurement_of(matches[0])[0].parameter == "step.diameter"
    assert not [issue for issue in drawing.lint() if issue.code == "pmi_not_rendered"]

    owner = next(
        feature
        for feature in drawing.model().features
        if isinstance(feature, StepFeature) and isinstance(feature.thread, ThreadRequirement)
    )
    assert matches[0] in drawing.annotations_of(owner)
    assert matches[0] in drawing.drop(owner)
    assert matches[0] not in drawing.annotations()


def test_typed_internal_thread_and_knurl_render_manufacturing_complete_labels_once():
    box = Box(20, 20, 20)
    drill = Cylinder(
        0.8,
        8,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).rotate(Axis.Y, 90)
    tapped_part = box - Pos(-10, 0, 0) * drill
    hole = HoleFeature(Frame((-10.0, 0.0, 0.0), "x"), 1.6, 8.0, False)
    internal = _raw(
        "internal_thread",
        INTERNAL_TEXT,
        _reference(diameter=1.6, interval=(-10.0, -2.0), sense="internal"),
        "#2004",
    )
    tapped_model = lower_ap242_manufacturing_requirements(
        PartModel(tapped_part.bounding_box(), "x", [hole, internal])
    )
    tapped = build_drawing(tapped_part, model=tapped_model, pmi="annotate", page="A0")
    tapped_labels = [
        tapped.registry.named(name).label
        for name in tapped.registry.names()
        if getattr(tapped.registry.named(name), "label", "")
    ]
    tap_labels = [label for label in tapped_labels if "M2 x 0.4-6H RH" in label]
    assert len(tap_labels) == 1
    assert "⌀1.6 ↧ 8" in tap_labels[0]
    assert "6 MIN FULL THREAD" in tap_labels[0]
    assert "118° CONVENTIONAL DRILL POINT" in tap_labels[0]
    assert not [issue for issue in tapped.lint() if issue.code == "pmi_not_rendered"]

    knurled_part = Cylinder(
        5,
        20,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    ).rotate(Axis.Y, 90)
    head = _step(10.0, 0.0, 20.0)
    knurl = _raw(
        "knurl",
        KNURL_TEXT,
        _reference(diameter=10.0, interval=(0.0, 20.0), sense="external"),
        "#2008",
    )
    knurled_model = lower_ap242_manufacturing_requirements(
        PartModel(knurled_part.bounding_box(), "x", [head, knurl])
    )
    knurled = build_drawing(knurled_part, model=knurled_model, pmi="annotate", page="A2")
    knurl_labels = [
        knurled.registry.named(name).label
        for name in knurled.registry.names()
        if "KNURL" in getattr(knurled.registry.named(name), "label", "")
    ]
    assert knurl_labels == [
        "ø10 MAX AFTER KNURL; STRAIGHT KNURL P1 FULL WIDTH TO C0.3 CHAMFERS; CUT/FORMED PERMITTED"
    ]
    assert not [issue for issue in knurled.lint() if issue.code == "pmi_not_rendered"]


def test_typed_manufacturing_row_keeps_plain_sibling_diameters_in_the_shared_solve():
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    segments = [
        Pos(lo, 0, 0) * Cylinder(diameter / 2, hi - lo, align=align).rotate(Axis.Y, 90)
        for diameter, lo, hi in (
            (4.0, 0.0, 3.2),
            (6.0, 3.2, 3.7),
            (10.0, 3.7, 5.7),
            (5.0, 5.7, 8.7),
            (3.0, 8.7, 28.7),
        )
    ]
    part = segments[0]
    for segment in segments[1:]:
        part += segment
    features = [
        _step(4.0, 0.0, 3.2),
        _step(6.0, 3.2, 3.7),
        _step(10.0, 3.7, 5.7),
        _step(5.0, 5.7, 8.7),
        _step(3.0, 8.7, 28.7),
        _raw(
            "knurl",
            KNURL_TEXT,
            _reference(diameter=10.0, interval=(4.0, 5.4), sense="external"),
            "#2008",
        ),
        _raw(
            "external_thread",
            EXTERNAL_TEXT,
            _reference(diameter=3.0, interval=(8.7, 28.2), sense="external"),
            "#2000",
        ),
    ]
    model = lower_ap242_manufacturing_requirements(PartModel(part.bounding_box(), "x", features))

    drawing = build_drawing(part, model=model, pmi="annotate", page="A1")

    assert {
        name: drawing.get_annotation(name).label
        for name in drawing.annotations()
        if name.startswith("m_dia_x")
    } == {
        "m_dia_x0": "ø4",
        "m_dia_x1": "ø6",
        "m_dia_x2": (
            "ø10 MAX AFTER KNURL; STRAIGHT KNURL P1 FULL WIDTH TO C0.3 CHAMFERS; "
            "CUT/FORMED PERMITTED"
        ),
        "m_dia_x3": "ø5",
        "m_dia_x4": "ø3 M3 x 0.5-6g RH, FULL AVAILABLE LENGTH",
    }
    knurl_owner = next(
        feature
        for feature in drawing.model().features
        if isinstance(getattr(feature, "knurl", None), KnurlRequirement)
    )
    thread_owner = next(
        feature
        for feature in drawing.model().features
        if isinstance(getattr(feature, "thread", None), ThreadRequirement)
    )
    assert drawing.registry.feature_of("m_dia_x2") == knurl_owner
    assert drawing.registry.feature_of("m_dia_x4") == thread_owner
    assert "m_dia_x2" in drawing.annotations_of(knurl_owner)
    assert "m_dia_x4" not in drawing.annotations_of(knurl_owner)
    assert "m_dia_x4" in drawing.annotations_of(thread_owner)
    assert "m_dia_x2" not in drawing.annotations_of(thread_owner)
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code
        in {
            "diameter_dropped",
            "pmi_not_rendered",
            "annotation_overlap",
            "feature_leader_crossing",
        }
    ]
    axis_y = drawing.at("front", 0.0, 0.0, 0.0)[1]
    for name, diameter in {
        "m_dia_x0": 4.0,
        "m_dia_x1": 6.0,
        "m_dia_x2": 10.0,
        "m_dia_x3": 5.0,
        "m_dia_x4": 3.0,
    }.items():
        tip_y = drawing.get_annotation(name).tip[1]
        assert abs(tip_y - axis_y) == pytest.approx(diameter / 2 * drawing.scale)


def test_identical_typed_threads_on_distinct_owners_keep_two_owned_annotations():
    align = (Align.CENTER, Align.CENTER, Align.MIN)
    segments = [
        Pos(lo, 0, 0) * Cylinder(diameter / 2, hi - lo, align=align).rotate(Axis.Y, 90)
        for diameter, lo, hi in ((3.0, 0.0, 5.0), (5.0, 5.0, 10.0), (3.0, 10.0, 15.0))
    ]
    part = segments[0]
    for segment in segments[1:]:
        part += segment
    model = lower_ap242_manufacturing_requirements(
        PartModel(
            part.bounding_box(),
            "x",
            [
                _step(3.0, 0.0, 5.0),
                _step(5.0, 5.0, 10.0),
                _step(3.0, 10.0, 15.0),
                _raw(
                    "external_thread",
                    EXTERNAL_TEXT,
                    _reference(diameter=3.0, interval=(0.2, 4.8), sense="external"),
                    "#2100",
                ),
                _raw(
                    "external_thread",
                    EXTERNAL_TEXT,
                    _reference(diameter=3.0, interval=(10.2, 14.8), sense="external"),
                    "#2104",
                ),
            ],
        )
    )
    drawing = build_drawing(part, model=model, pmi="annotate", page="A1")
    owners = {
        feature.thread.source_ids[0]: feature
        for feature in drawing.model().features
        if isinstance(getattr(feature, "thread", None), ThreadRequirement)
    }
    expected_label = "ø3 M3 x 0.5-6g RH, FULL AVAILABLE LENGTH"
    names_by_source = {
        source_id: [
            name
            for name, annotation in drawing.annotations_of(owner).items()
            if getattr(annotation, "label", "") == expected_label
        ]
        for source_id, owner in owners.items()
    }

    assert set(owners) == {
        "manufacturing_requirement:#2100",
        "manufacturing_requirement:#2104",
    }
    assert all(len(names) == 1 for names in names_by_source.values())
    assert len({names[0] for names in names_by_source.values()}) == 2
    assert all(
        drawing.registry.feature_of(names[0]) == owners[source_id]
        for source_id, names in names_by_source.items()
    )

    first_source, second_source = tuple(sorted(owners))
    first_name = names_by_source[first_source][0]
    second_name = names_by_source[second_source][0]
    drawing.drop(owners[first_source])
    assert first_name not in drawing.annotations()
    assert second_name in drawing.annotations_of(owners[second_source])


def test_source_owned_feature_drop_is_one_reconciled_pmi_drop():
    from draftwright.linting import LintIssue
    from draftwright.linting.pmi_coverage import lint_pmi_rendering, pmi_stage_summary
    from draftwright.pmi import PmiSourceEntity
    from draftwright.registry import AnnotationRegistry

    source_id = "manufacturing_requirement:#2000"
    owner = _step(3.0, 0.0, 10.0)
    raw = _raw(
        "external_thread",
        EXTERNAL_TEXT,
        _reference(diameter=3.0, interval=(0.0, 10.0), sense="external"),
        "#2000",
    )
    model = lower_ap242_manufacturing_requirements(_model(owner, raw))
    report = PmiExtractionReport(
        sources=(PmiSourceEntity(source_id, "manufacturing_requirement", 2000, "extracted"),),
        records=(
            PmiRecord(
                "external_thread",
                None,
                0.0,
                label=EXTERNAL_TEXT,
                source_id=source_id,
                source_category="manufacturing_requirement",
            ),
        ),
    )
    registry = AnnotationRegistry()
    registry.record_issue(
        LintIssue(
            severity="warning",
            code="diameter_dropped",
            message="typed diameter had no clear route",
            source_ids=(source_id,),
            outcome_stage="placement",
        )
    )

    assert pmi_stage_summary(
        report,
        model.features,
        registry,
        "annotate",
        decorations=model.decorations,
    ) == {
        "mode": "annotate",
        "sources": 1,
        "by_category": {"manufacturing_requirement": 1},
        "extracted": 1,
        "lowered": 1,
        "rendered": 0,
        "dropped": 1,
    }
    assert (
        lint_pmi_rendering(
            model.features,
            registry,
            "annotate",
            decorations=model.decorations,
        )
        == []
    )


def test_known_unsupported_manufacturing_intent_is_explicit_without_error_lint():
    unsupported = PmiRecord(
        kind="surface_texture",
        type_code=None,
        value=0.0,
        label="GENERAL SURFACE TEXTURE",
        source_id="manufacturing_requirement:#2012",
        source_category="manufacturing_requirement",
    )
    supported_but_raw = PmiRecord(
        kind="knurl",
        type_code=None,
        value=0.0,
        label=KNURL_TEXT,
        source_id="manufacturing_requirement:#2008",
        source_category="manufacturing_requirement",
    )
    report = PmiExtractionReport(records=(unsupported, supported_but_raw))
    raw_features = [
        PmiFeature(
            Frame((0.0, 0.0, 0.0), "x"),
            record.kind,
            0.0,
            record.label,
            "?",
            source_id=record.source_id,
            source_category="manufacturing_requirement",
        )
        for record in report.records
    ]

    issues = lint_pmi_lowering(report, raw_features, "annotate")
    by_source = {issue.source_ids[0]: issue for issue in issues}
    assert by_source[unsupported.source_id].severity == "warning"
    assert "raw 'surface_texture' PMI fallback" in by_source[unsupported.source_id].message
    assert by_source[supported_but_raw.source_id].severity == "error"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"source_ids": ()}, "at least one source id"),
        ({"application": "unknown"}, "application must be"),
        ({"designation": " "}, "designation must be non-empty"),
        ({"nominal_diameter": 0}, "nominal_diameter must be finite and positive"),
        ({"minimum_full_thread": 0}, "minimum_full_thread must be finite and positive"),
        ({"text": " "}, "source text must be non-empty"),
        ({"part21_id": " "}, "Part21 identity"),
        ({"cylindrical_refs": ()}, "finite-cylinder topology evidence"),
    ],
)
def test_thread_requirement_rejects_incomplete_or_invalid_source_facts(changes, message):
    owner = _step(3.0, 0.0, 10.0)
    raw = _raw(
        "external_thread",
        EXTERNAL_TEXT,
        _reference(diameter=3.0, interval=(0.0, 10.0), sense="external"),
        "#2000",
    )
    requirement = lower_ap242_manufacturing_requirements(_model(owner, raw)).features[0].thread
    assert isinstance(requirement, ThreadRequirement)
    with pytest.raises(ValueError, match=message):
        replace(requirement, **changes)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"source_ids": ()}, "at least one source id"),
        ({"pattern": "unknown"}, "pattern must be"),
        ({"pitch": 0}, "pitch must be finite and positive"),
        ({"edge_chamfer": 0}, "edge_chamfer must be finite and positive"),
        ({"text": " "}, "source text must be non-empty"),
        ({"part21_id": " "}, "Part21 identity"),
        ({"cylindrical_refs": ()}, "finite-cylinder topology evidence"),
        ({"processes": ("rolled",)}, "processes must be"),
    ],
)
def test_knurl_requirement_rejects_incomplete_or_invalid_source_facts(changes, message):
    owner = _step(10.0, 0.0, 2.0)
    raw = _raw(
        "knurl",
        KNURL_TEXT,
        _reference(diameter=10.0, interval=(0.0, 2.0), sense="external"),
        "#2008",
    )
    requirement = lower_ap242_manufacturing_requirements(_model(owner, raw)).features[0].knurl
    assert isinstance(requirement, KnurlRequirement)
    with pytest.raises(ValueError, match=message):
        replace(requirement, **changes)


def test_minimal_typed_aspect_suffixes_omit_absent_optional_claims():
    step = _step(3.0, 0.0, 10.0)
    thread_raw = _raw(
        "external_thread",
        EXTERNAL_TEXT,
        _reference(diameter=3.0, interval=(0.0, 10.0), sense="external"),
        "#2000",
    )
    thread = lower_ap242_manufacturing_requirements(_model(step, thread_raw)).features[0].thread
    assert isinstance(thread, ThreadRequirement)
    minimal_thread = replace(
        thread,
        full_available_length=False,
        minimum_full_thread=None,
        drill_diameter=None,
        drill_depth=None,
        drill_point_angle=None,
    )
    assert minimal_thread.callout_suffix == "M3 x 0.5-6g RH"

    hole = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 1.6, 8.0, False)
    internal_raw = _raw(
        "internal_thread",
        INTERNAL_TEXT,
        _reference(diameter=1.6, interval=(0.0, 8.0), sense="internal"),
        "#2004",
    )
    internal = (
        lower_ap242_manufacturing_requirements(_model(hole, internal_raw)).features[0].thread
    )
    assert isinstance(internal, ThreadRequirement)
    minimal_internal = replace(internal, minimum_full_thread=None, drill_point_angle=None)
    assert minimal_internal.callout_suffix == "M2 x 0.4-6H RH"

    head = _step(10.0, 0.0, 2.0)
    knurl_raw = _raw(
        "knurl",
        KNURL_TEXT,
        _reference(diameter=10.0, interval=(0.0, 2.0), sense="external"),
        "#2008",
    )
    knurl = lower_ap242_manufacturing_requirements(_model(head, knurl_raw)).features[0].knurl
    assert isinstance(knurl, KnurlRequirement)
    minimal_knurl = replace(
        knurl,
        full_width=False,
        edge_chamfer=None,
        maximum_diameter=None,
        processes=(),
    )
    assert minimal_knurl.callout_suffix == "STRAIGHT KNURL P1"
    full_width_without_chamfer = replace(minimal_knurl, full_width=True)
    assert full_width_without_chamfer.callout_suffix == "STRAIGHT KNURL P1 FULL WIDTH"


def test_lowering_fail_closed_paths_preserve_raw_source_and_conflict_reason():
    owner = _step(3.0, 0.0, 10.0)
    assert lower_ap242_manufacturing_requirements(_model(owner)).features == [owner]

    preblocked = replace(
        _raw(
            "external_thread",
            EXTERNAL_TEXT,
            _reference(diameter=3.0, interval=(0.0, 10.0), sense="external"),
            "#2000",
        ),
        lowering_blockers=("source topology unavailable",),
    )
    unchanged = lower_ap242_manufacturing_requirements(_model(owner, preblocked))
    assert unchanged.features[-1].lowering_blockers == ("source topology unavailable",)

    bad_thread = replace(preblocked, lowering_blockers=(), label="not a thread requirement")
    bad_knurl = _raw(
        "knurl",
        "not a knurl requirement",
        _reference(diameter=3.0, interval=(0.0, 10.0), sense="external"),
        "#2008",
    )
    parsed = lower_ap242_manufacturing_requirements(_model(owner, bad_thread, bad_knurl))
    blockers = {
        feature.pmi_kind: feature.lowering_blockers
        for feature in parsed.features
        if isinstance(feature, PmiFeature)
    }
    assert blockers == {
        "external_thread": ("unsupported external thread syntax",),
        "knurl": ("unsupported knurl requirement syntax",),
    }

    contradictory = _raw(
        "external_thread",
        EXTERNAL_TEXT.replace("nominal DIA 3", "nominal DIA 4"),
        _reference(diameter=3.0, interval=(0.0, 10.0), sense="external"),
        "#2003",
    )
    contradicted = lower_ap242_manufacturing_requirements(_model(owner, contradictory))
    fallback = next(
        feature for feature in contradicted.features if isinstance(feature, PmiFeature)
    )
    assert fallback.lowering_blockers == (
        "external thread designation and nominal region disagree",
    )

    multi_reference = replace(
        _raw(
            "external_thread",
            EXTERNAL_TEXT,
            _reference(diameter=3.0, interval=(0.0, 10.0), sense="external"),
            "#2005",
        ),
        cylindrical_refs=(
            _reference(diameter=3.0, interval=(0.0, 10.0), sense="external"),
            _reference(
                diameter=3.0,
                interval=(0.0, 10.0),
                sense="external",
                axis_origin=(0.0, 2.0, 0.0),
            ),
        ),
    )
    multiple = lower_ap242_manufacturing_requirements(_model(owner, multi_reference))
    fallback = next(feature for feature in multiple.features if isinstance(feature, PmiFeature))
    assert fallback.lowering_blockers == (
        "unmatched manufacturing requirement: no canonical feature matches source topology",
    )

    disagreement = _raw(
        "internal_thread",
        INTERNAL_TEXT,
        _reference(diameter=2.0, interval=(0.0, 8.0), sense="internal"),
        "#2004",
    )
    mismatched_hole = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 2.0, 8.0, False)
    disagreed = lower_ap242_manufacturing_requirements(_model(mismatched_hole, disagreement))
    fallback = next(feature for feature in disagreed.features if isinstance(feature, PmiFeature))
    assert fallback.lowering_blockers == (
        "manufacturing requirement text disagrees with source cylinder",
    )


def test_lowering_rejects_duplicate_claims_and_existing_authored_aspects():
    step = _step(3.0, 0.0, 10.0)
    first = _raw(
        "external_thread",
        EXTERNAL_TEXT,
        _reference(diameter=3.0, interval=(0.0, 10.0), sense="external"),
        "#2000",
    )
    second = replace(first, source_id="manufacturing_requirement:#2001", part21_id="#2001")
    duplicate = lower_ap242_manufacturing_requirements(_model(step, first, second))
    raw = next(feature for feature in duplicate.features if isinstance(feature, PmiFeature))
    assert "already claimed" in raw.lowering_blockers[0]

    threaded_step = duplicate.features[0]
    assert isinstance(threaded_step.thread, ThreadRequirement)
    third = replace(first, source_id="manufacturing_requirement:#2002", part21_id="#2002")
    existing_thread = lower_ap242_manufacturing_requirements(_model(threaded_step, third))
    raw = next(feature for feature in existing_thread.features if isinstance(feature, PmiFeature))
    assert "already has a thread aspect" in raw.lowering_blockers[0]

    head = _step(10.0, 0.0, 2.0)
    knurl_raw = _raw(
        "knurl",
        KNURL_TEXT,
        _reference(diameter=10.0, interval=(0.0, 2.0), sense="external"),
        "#2008",
    )
    knurled = lower_ap242_manufacturing_requirements(_model(head, knurl_raw)).features[0]
    existing_knurl = lower_ap242_manufacturing_requirements(_model(knurled, knurl_raw))
    raw = next(feature for feature in existing_knurl.features if isinstance(feature, PmiFeature))
    assert "already has a knurl aspect" in raw.lowering_blockers[0]


def test_single_member_pattern_receives_internal_thread_and_rejects_a_second_one():
    member = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 1.6, 8.0, False)
    pattern = PatternFeature(
        frame=member.frame,
        pattern="linear",
        count=1,
        member=member,
        members=((0.0, 0.0, 0.0),),
    )
    raw = _raw(
        "internal_thread",
        INTERNAL_TEXT,
        _reference(diameter=1.6, interval=(0.0, 8.0), sense="internal"),
        "#2004",
    )
    lowered = lower_ap242_manufacturing_requirements(_model(pattern, raw))
    owner = lowered.features[0]
    assert isinstance(owner, PatternFeature)
    assert isinstance(owner.member.thread, ThreadRequirement)

    from build123d_drafting.helpers import Draft

    from draftwright.annotations._common import PlacementContext
    from draftwright.annotations.from_model import callout_from_spec
    from draftwright.annotations.holes import _record_callout_drop
    from draftwright.linting.coverage import CoverageState
    from draftwright.model.callout import hole_callout_spec
    from draftwright.model.planner import plan_dimensions
    from draftwright.registry import AnnotationRegistry

    group = next(group for group in plan_dimensions(lowered) if group.feature is owner)
    spec = hole_callout_spec(group)
    assert spec is not None
    assert spec["source_ids"] == ("manufacturing_requirement:#2004",)
    callout = callout_from_spec(spec, Draft(), spec["count"])
    assert callout is not None
    assert callout.source_ids == ("manufacturing_requirement:#2004",)
    ctx = PlacementContext(registry=AnnotationRegistry(), coverage=CoverageState())
    _record_callout_drop(
        ctx,
        object(),
        "side",
        1.6,
        "shared leader inventory full",
        owner,
        callout=callout,
        outcome_stage="placement",
    )
    issue = ctx.registry.issues[0]
    assert issue.source_ids == ("manufacturing_requirement:#2004",)
    assert all(measurement.feature is owner for measurement in issue.measurement_ids)

    repeated = lower_ap242_manufacturing_requirements(_model(owner, raw))
    fallback = next(feature for feature in repeated.features if isinstance(feature, PmiFeature))
    assert "canonical hole already has a thread aspect" in fallback.lowering_blockers[0]


def test_emitter_preserves_pattern_member_raw_topology_and_object_backed_knurl():
    member = HoleFeature(Frame((0.0, 0.0, 0.0), "x"), 1.6, 8.0, False)
    pattern = PatternFeature(
        frame=member.frame,
        pattern="linear",
        count=1,
        member=member,
        members=((0.0, 0.0, 0.0),),
    )
    internal = _raw(
        "internal_thread",
        INTERNAL_TEXT,
        _reference(diameter=1.6, interval=(0.0, 8.0), sense="internal"),
        "#2004",
    )
    patterned = lower_ap242_manufacturing_requirements(_model(pattern, internal))
    pattern_source = emit_sheet_script(
        patterned, "part", "patterned-thread", title="P", number="N"
    )
    assert "thread=ThreadRequirement(" in pattern_source

    raw = _raw(
        "surface_texture",
        "GENERAL SURFACE TEXTURE",
        _reference(diameter=10.0, interval=(0.0, 20.0), sense="external"),
        "#2012",
    )
    raw_source = emit_sheet_script(_model(raw), "part", "raw-topology", title="P", number="N")
    assert "PmiFeature(" in raw_source
    assert "cylindrical_refs=(CylindricalReference(" in raw_source

    head = _step(10.0, 0.0, 20.0)
    knurl_raw = _raw(
        "knurl",
        KNURL_TEXT,
        _reference(diameter=10.0, interval=(0.0, 20.0), sense="external"),
        "#2008",
    )
    knurl = lower_ap242_manufacturing_requirements(_model(head, knurl_raw)).features[0].knurl
    assert isinstance(knurl, KnurlRequirement)
    candidate = Cylinder(5, 20)
    boss = BossFeature(Frame((0.0, 0.0, 0.0), "z"), 10.0, height=20.0, knurl=knurl)
    boss_model = PartModel(candidate.bounding_box(), None, [boss])
    boss_source = emit_sheet_script(
        boss_model,
        "part",
        "object-knurl",
        title="P",
        number="N",
        source_part=candidate,
        object_candidates={"features.boss": candidate},
    )
    assert "sheet.diameter(features.boss, knurl=KnurlRequirement(" in boss_source


def test_exact_grm03_lowers_all_three_supported_manufacturing_requirements():
    assert hashlib.sha256(GRM03.read_bytes()).hexdigest() == GRM03_SHA256
    report = extract_pmi_report(GRM03)
    records = {
        record.kind: record
        for record in report.records
        if record.kind in {"external_thread", "internal_thread", "knurl"}
    }
    assert set(records) == {"external_thread", "internal_thread", "knurl"}
    assert all(len(record.cylindrical_refs) == 1 for record in records.values())
    assert all(not record.lowering_blockers for record in records.values())

    model = build_part_model(_import_step(str(GRM03)), pmi=report.records)
    aspects = [
        aspect
        for feature in model.features
        for aspect in (getattr(feature, "thread", None), getattr(feature, "knurl", None))
        if isinstance(aspect, (ThreadRequirement, KnurlRequirement))
    ]
    assert {source_id for aspect in aspects for source_id in aspect.source_ids} == {
        "manufacturing_requirement:#2000",
        "manufacturing_requirement:#2004",
        "manufacturing_requirement:#2008",
    }
    assert not [
        feature
        for feature in model.features
        if isinstance(feature, PmiFeature)
        and feature.pmi_kind in {"external_thread", "internal_thread", "knurl"}
    ]


def test_exact_grm03_renders_complete_source_owned_manufacturing_drawing_once():
    assert hashlib.sha256(GRM03.read_bytes()).hexdigest() == GRM03_SHA256
    drawing = build_drawing(GRM03, pmi="annotate")

    assert (drawing.page_w, drawing.page_h, drawing.scale) == (841.0, 594.0, 10.0)
    assert set(drawing.views) == {"front", "plan", "side"}
    assert drawing.scale_decision["status"] == "automatic_replanned"
    attempts = drawing.scale_decision["attempts"]
    assert attempts[-1]["status"] == "complete"
    assert {
        source_id
        for attempt in attempts[:-1]
        for blocker in attempt.get("blockers", ())
        for source_id in blocker.get("source_ids", ())
    } == {"manufacturing_requirement:#2004"}

    expected_manufacturing = {
        "manufacturing_requirement:#2000": (
            "m_dia_x4",
            "ø3 M3 x 0.5-6g RH, FULL AVAILABLE LENGTH",
        ),
        "manufacturing_requirement:#2004": (
            "hc_side0",
            "⌀1.6 ↧ 8 M2 x 0.4-6H RH; 6 MIN FULL THREAD; 118° CONVENTIONAL DRILL POINT",
        ),
        "manufacturing_requirement:#2008": (
            "m_dia_x2",
            "ø10 MAX AFTER KNURL; STRAIGHT KNURL P1 FULL WIDTH TO C0.3 CHAMFERS; "
            "CUT/FORMED PERMITTED",
        ),
    }
    typed_occurrences = []
    for feature in drawing.model().features:
        for requirement in (getattr(feature, "thread", None), getattr(feature, "knurl", None)):
            if isinstance(requirement, (ThreadRequirement, KnurlRequirement)):
                for source_id in requirement.source_ids:
                    typed_occurrences.append((source_id, feature))
    assert sorted(source_id for source_id, _feature in typed_occurrences) == sorted(
        expected_manufacturing
    )
    typed_owners = dict(typed_occurrences)
    for source_id, (expected_name, expected_label) in expected_manufacturing.items():
        owner = typed_owners[source_id]
        matches = [
            name
            for name, annotation in drawing.annotations_of(owner).items()
            if getattr(annotation, "label", "") == expected_label
        ]
        assert matches == [expected_name]
        assert drawing.registry.feature_of(expected_name) == owner

    expected_axial = {
        "dimension:0:1:4:6": ("pmi_x_0", "3.2"),
        "dimension:0:1:4:7": ("pmi_x_1", "0.5"),
        "dimension:0:1:4:8": ("pmi_x_2", "2"),
        "dimension:0:1:4:9": ("pmi_x_3", "3"),
        "dimension:0:1:4:10": ("pmi_x_4", "20"),
    }
    axial_occurrences = [
        (feature.source_id, feature)
        for feature in drawing.model().features
        if isinstance(feature, AuthoredDimension) and feature.source_id in expected_axial
    ]
    assert sorted(source_id for source_id, _feature in axial_occurrences) == sorted(expected_axial)
    axial_owners = dict(axial_occurrences)
    for source_id, (expected_name, expected_label) in expected_axial.items():
        owner = axial_owners[source_id]
        matches = [
            name
            for name, annotation in drawing.annotations_of(owner).items()
            if getattr(annotation, "label", "") == expected_label
        ]
        assert matches == [expected_name]
        assert drawing.registry.feature_of(expected_name) == owner

    issues = drawing.lint()
    forbidden = {
        "label_vs_measured",
        "pmi_not_rendered",
        "axial_length_missing",
        "annotation_overlap",
        "annotation_out_of_bounds",
        "view_overlap",
        "feature_leader_crossing",
    }
    assert not [
        issue
        for issue in issues
        if issue.severity == "error" or issue.code in forbidden or issue.code.endswith("_dropped")
    ]
    unsupported = [issue for issue in issues if issue.code == "pmi_not_lowered"]
    assert {issue.source_ids[0]: issue.severity for issue in unsupported} == {
        "manufacturing_requirement:#2012": "warning",
        "manufacturing_requirement:#2016": "warning",
        "manufacturing_requirement:#2020": "warning",
        "manufacturing_requirement:#2024": "warning",
        "manufacturing_requirement:#2028": "warning",
    }

    model = drawing.model()
    source = emit_sheet_script(model, "part", "grm03-pmi", title="GRM-03", number="GRM-03")
    namespace = {"part": _import_step(str(GRM03))}
    exec(  # noqa: S102
        compile(source[: source.index("drawing = sheet.build()")], "<grm03-pmi-emit>", "exec"),
        namespace,
    )
    assert _manufacturing_signature(namespace["sheet"].model()) == _manufacturing_signature(model)
