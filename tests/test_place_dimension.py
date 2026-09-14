"""Deprecated raw dimension-placement compatibility."""

import warnings

import pytest
from build123d import Box

from draftwright import build_drawing


def _place_dim(dwg, *args, **kwargs):
    # White-box: these exercise the private placement primitive directly (#817).
    # The deprecated public shim's warning is covered by
    # test_dimension_does_not_warn_when_using_place_dim_internally.
    return dwg._place_dim(*args, **kwargs)


class TestPlaceDim:
    """#25: the raw page-coordinate dim primitive stacks with the auto-dimension strip."""

    def test_place_dim_adds_named_annotation(self):
        dwg = build_drawing(Box(80, 60, 20))
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        _place_dim(dwg, p1, p2, "below", "plan", dwg.draft, name="my_dim", label="80")
        assert "my_dim" in dwg.annotations()
        assert dwg.get_annotation("my_dim").label == "80"

    def test_place_dim_returns_dimension_object(self):
        from build123d_drafting.helpers import Dimension

        dwg = build_drawing(Box(60, 40, 20))
        p1 = dwg.at("front", -30, 0, -10)
        p2 = dwg.at("front", 30, 0, -10)
        result = _place_dim(dwg, p1, p2, "below", "front", dwg.draft)
        assert isinstance(result, Dimension)

    def test_two_place_dim_calls_stack_without_overlap(self):
        # Two dims on the same strip must land at different page positions.
        # Use auto_dims=False so the strip has no prior allocations, and
        # "above" where there is ample headroom for two consecutive allocations.
        dwg = build_drawing(Box(80, 60, 20), auto_dims=False)
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        d1 = _place_dim(dwg, p1, p2, "above", "plan", dwg.draft, name="d1")
        d2 = _place_dim(dwg, p1, p2, "above", "plan", dwg.draft, name="d2")
        # dim_level_y is the y-coordinate of the dim line on the page;
        # two stacked dims must land at different y values.
        assert d1.dim_level_y != d2.dim_level_y

    def test_place_dim_no_analysis_falls_back_to_slot(self):
        # _analysis is None → no strip available → falls back to slot offset, no error.
        from build123d_drafting.helpers import Dimension, draft_preset

        from draftwright import Drawing

        d = draft_preset(font_size=3.0, decimal_precision=1)
        dwg = Drawing(
            scale=1.0,
            page_w=297,
            page_h=210,
            tb_w=100,
            draft=d,
            look_at=(0, 0, 0),
            dist=100,
            centroid=(0, 0, 0),
            out="",
        )
        result = _place_dim(dwg, (0, 0, 0), (80, 0, 0), "below", "plan", d, slot=8.0)
        assert isinstance(result, Dimension)

    def test_place_dim_labels_real_world_length_at_non_unity_scale(self):
        # place_dim receives page-coordinate points; at 1:2 the page span is 2× the
        # world size. The auto label must read the real-world length, not the page
        # distance, or it disagrees with the geometry (and trips label_vs_measured).
        from draftwright.linting import lint_drawing

        dwg = build_drawing(Box(80, 60, 20), scale=2.0)
        assert dwg.scale == 2.0
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        d = _place_dim(dwg, p1, p2, "below", "plan", dwg.draft, name="w")
        assert d.label == "80"
        assert [
            i for i in lint_drawing([d], drawing_scale=dwg.scale) if i.code == "label_vs_measured"
        ] == []

    def test_place_dim_explicit_label_wins_over_scale_autolabel(self):
        dwg = build_drawing(Box(80, 60, 20), scale=2.0)
        p1 = dwg.at("plan", -40, 0, 0)
        p2 = dwg.at("plan", 40, 0, 0)
        d = _place_dim(dwg, p1, p2, "below", "plan", dwg.draft, label="CUSTOM")
        assert d.label == "CUSTOM"

    def test_dimension_does_not_warn_when_using_place_dim_internally(self):
        dwg = build_drawing(Box(80, 50, 20), auto_dims=False)
        env = next(f for f in dwg.model().features if f.kind == "envelope")
        with pytest.warns(DeprecationWarning) as caught:
            dwg.place_dim(
                dwg.at("front", -40, 0, -10),
                dwg.at("front", 40, 0, -10),
                "below",
                "front",
                dwg.draft,
            )
        assert caught

        with warnings.catch_warnings(record=True) as no_warnings:
            warnings.simplefilter("always")
            dwg.dimension(env, "length", role="width", name="semantic_width")
        assert [
            w
            for w in no_warnings
            if issubclass(w.category, DeprecationWarning) and "place_dim" in str(w.message)
        ] == []
