"""The Specify AP242 plate keeps source locations ahead of generated grid pitch (#2192)."""

from pathlib import Path
from types import SimpleNamespace

from draftwright import build_drawing
from draftwright.annotations.orchestrator import _defer_generated_pitch_for_source_pmi

FIXTURE = Path(__file__).parent / "fixtures/specify_plate_2192_ap242.step"
FORTY_SOURCES = {
    "dimension:0:1:4:2",
    "dimension:0:1:4:13",
    "dimension:0:1:4:15",
}


def test_source_first_pitch_order_is_independent_of_declared_front_door():
    source = SimpleNamespace(kind="authored_dimension", source="ap242_pmi")
    model = SimpleNamespace(features=(source,), authored_dimensions=None)
    analysis = SimpleNamespace(pmi_mode="annotate")
    detected = SimpleNamespace(model_declared=False, document_member=False)
    declared = SimpleNamespace(model_declared=True, document_member=False)
    document = SimpleNamespace(model_declared=True, document_member=True)

    assert all(
        _defer_generated_pitch_for_source_pmi(analysis, ctx, model)
        for ctx in (detected, declared, document)
    )
    assert not _defer_generated_pitch_for_source_pmi(
        analysis,
        declared,
        SimpleNamespace(features=(source,), authored_dimensions=frozenset({"pitch"})),
    )
    assert not _defer_generated_pitch_for_source_pmi(
        SimpleNamespace(pmi_mode="off"), document, model
    )


def test_source_basic_locations_and_generated_grid_pitch_share_a_diagnostic_sheet():
    # Isolate this priority rule at the default planner's 1:1 A1 candidate.
    # The complete automatic-plan result is checked separately on the stack.
    drawing = build_drawing(
        FIXTURE,
        pmi="annotate",
        out=None,
        page="A1",
        scale=1,
        scale_policy="permissive",
        repair=False,
    )
    authored = {
        feature.source_id: feature
        for feature in drawing.model().features
        if feature.kind == "authored_dimension" and feature.source_id in FORTY_SOURCES
    }

    assert set(authored) == FORTY_SOURCES
    for feature in authored.values():
        ink = drawing.annotations_of(feature)
        assert len(ink) == 1
        assert next(iter(ink.values())).label == "40"
    assert {"dim_pitch_plan0_0", "dim_pitch_plan0_1"} <= set(drawing.annotations())
    assert not [
        issue
        for issue in drawing.lint()
        if issue.code
        in {
            "hole_pattern_dim_dropped",
            "annotation_overlap",
            "annotation_ink_overlap",
        }
        or (issue.code == "pmi_dropped" and "'40'" in issue.message)
    ]
