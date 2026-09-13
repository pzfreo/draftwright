"""A dense internal station warrants a readable section in both build routes."""

from dataclasses import replace
from unittest.mock import patch

import pytest
from build123d import import_step

from draftwright import Drawing, build_drawing
from draftwright.builder import detect_part_model
from draftwright.model.ir import CircularChannelFeature, HexPocketFeature
from draftwright.model.planner import internal_section_rows, plan_sections
from draftwright.sheet_emit import emit_sheet_script


@pytest.fixture(scope="module")
def frame_case():
    part = import_step("tests/fixtures/issue_1595_whistle_key_frame.step")
    model = detect_part_model(part)
    direct = build_drawing(part)
    script = emit_sheet_script(model, "part", "frame", title="FRAME", number="F", formats=("svg",))
    captured = {"part": part}
    with patch.object(
        Drawing, "export", lambda self, *a, **k: captured.setdefault("drawing", self)
    ):
        exec(compile(script, "<frame-sheet>", "exec"), captured)  # noqa: S102
    return model, direct, captured["drawing"]


def test_station_needs_multiple_unlike_internal_details(frame_case):
    model, _, _ = frame_case
    assert internal_section_rows(model) == {-32.3: 3, -7.9: 3, 32.3: 3}
    chosen = plan_sections(model, set())
    assert chosen is not None and chosen.cut_y == -7.9 and chosen.internal_detail

    internal = [
        feature
        for feature in model.features
        if isinstance(feature, (CircularChannelFeature, HexPocketFeature))
    ]
    only_pockets = replace(
        model, features=[feature for feature in internal if isinstance(feature, HexPocketFeature)]
    )
    assert plan_sections(only_pockets, set()) is None
    one_of_each = replace(
        model,
        features=[
            next(feature for feature in internal if isinstance(feature, CircularChannelFeature)),
            next(feature for feature in internal if isinstance(feature, HexPocketFeature)),
        ],
    )
    assert plan_sections(one_of_each, set()) is None


def test_original_frame_section_and_required_slots_survive_script_replay(frame_case):
    _, direct, replayed = frame_case
    for drawing in (direct, replayed):
        assert drawing.section_decision["status"] == "placed"
        assert "section_aa" in drawing.views
        assert drawing.get_annotation("section_hatch") is not None
        assert drawing.get_annotation("m_slot0_width").label == "2× 17.4"
        assert drawing.get_annotation("m_slot0_length").label == "18.4"
        assert drawing.get_annotation("m_slot1_length").label == "34.2"
        assert drawing.get_annotation("m_slot1_pos").label == "30.9"
        assert any(
            "6×" in getattr(annotation, "label", "") and "2.4" in getattr(annotation, "label", "")
            for _, annotation in drawing.iter_annotations()
        )
        assert not {
            issue.code
            for issue in drawing.lint()
            if issue.code
            in {
                "annotation_overlap",
                "annotation_ink_overlap",
                "slot_dim_dropped",
                "section_dropped",
                "plan_incomplete",
            }
        }
