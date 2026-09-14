"""Annotation-box composition and footprint reduction behavior."""

import pytest
from build123d import Box, Cylinder, Pos

from _layout_helpers import _sizing_model
from _parts import dense_plate as _dense_plate


class TestComposeAnnoBoxes:
    """Step 4a (#112): the AnnoBox composer reduces to the identical StripDepths
    that _measure_strips computes — the byte-identical box-model foundation that
    later steps make honest."""

    def _assert_match(self, model, n_steps, bb, w=0.0, label=""):
        from draftwright.builder import _FONT_SIZE, draft_preset
        from draftwright.compose import _compose_anno_boxes, _footprint_from_boxes, _measure_strips

        # The composer is the footprint authority (#112): _measure_strips is only
        # a compatibility reducer around these boxes. Exercise defaults, the
        # production draft preset, and deliberately divergent clearance values.
        preset = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
        arg_sets = (
            {},
            {"arrow_length": preset.arrow_length, "pad_around_text": preset.pad_around_text},
            {"arrow_length": 4.3, "pad_around_text": 3.1},
        )
        for kw in arg_sets:
            composed = _footprint_from_boxes(
                _compose_anno_boxes(model, n_steps, bore_callout_width=w, **kw)
            )
            scalar = _measure_strips(model, n_steps, bb, bore_callout_width=w, **kw)
            assert composed == scalar, (label, n_steps, kw)

    def test_bore_callout_width_flows_through_boxes(self):
        # #540/#584 WP1 A: the planner-derived callout width (authored tolerances
        # included) must be represented as an annotation box, not a scalar-only side
        # channel in _measure_strips.
        from draftwright.builder import _FONT_SIZE, draft_preset
        from draftwright.compose import _compose_anno_boxes, _footprint_from_boxes, _measure_strips

        part = Box(60, 40, 12) - Pos(0, 0, 6) * Cylinder(3, 12)
        model, _ = _sizing_model(part)
        bb = part.bounding_box()
        draft = draft_preset(font_size=_FONT_SIZE, decimal_precision=1)
        width = 55.0
        expected_bore_depth = width + draft.pad_around_text + draft.arrow_length

        boxes = _compose_anno_boxes(
            model,
            0,
            bore_callout_width=width,
            arrow_length=draft.arrow_length,
            pad_around_text=draft.pad_around_text,
        )
        assert expected_bore_depth in [b.depth for b in boxes if b.side == "right"]
        assert expected_bore_depth in [b.depth for b in boxes if b.side == "left"]
        assert _footprint_from_boxes(boxes) == _measure_strips(
            model,
            0,
            bb,
            bore_callout_width=width,
            arrow_length=draft.arrow_length,
            pad_around_text=draft.pad_around_text,
        )

    def test_matches_for_plain_part(self):
        part = Box(60, 40, 12)
        model, w = _sizing_model(part)
        bb = part.bounding_box()
        for n_steps in (0, 1, 3):
            self._assert_match(model, n_steps, bb, w)

    def test_matches_for_bored_part(self):
        part = Box(60, 40, 12) - Pos(0, 0, 6) * Cylinder(3, 12)
        model, w = _sizing_model(part)
        bb = part.bounding_box()
        for n_steps in (0, 2):
            self._assert_match(model, n_steps, bb, w)

    def test_matches_for_dense_ballooning_part(self):
        # _dense_plate triggers _will_balloon → exercises the plan_halo band.
        from draftwright.compose import _will_balloon

        part = _dense_plate()
        model, w = _sizing_model(part)
        bb = part.bounding_box()
        assert _will_balloon(model)  # guard: this case must balloon
        self._assert_match(model, 0, bb, w)

    def test_pattern_plus_same_spec_loose_size_as_separate_callouts(self):
        # #584 WP1 A (accepted divergence, more-correct): a pattern and same-spec LOOSE
        # holes are separate IR features, so sizing reserves for the pattern's
        # "8× …EQ SP ON ø… BC" callout — NOT a phantom merged "11×". The renderer emits
        # them as distinct callouts, so adding same-spec loose holes must not widen the
        # pattern's callout corridor (the old record-based estimator merged them and
        # over-reserved for a callout that never renders).
        from math import cos, radians, sin

        ring = Box(120, 120, 8)
        for i in range(8):
            a = radians(45 * i)
            ring -= Pos(35 * cos(a), 35 * sin(a), 0) * Cylinder(3, 8)
        part = ring
        for x, y in [(-52, -52), (52, 52), (-52, 52)]:
            part -= Pos(x, y, 0) * Cylinder(3, 8)  # 3 loose ø6 holes, same spec

        model_both, w_both = _sizing_model(part)
        kinds = sorted(f.kind for f in model_both.features if f.kind in ("hole", "pattern"))
        assert kinds == ["hole", "pattern"]  # separate features, not merged
        _, w_ring = _sizing_model(ring)  # pattern alone
        assert w_both == pytest.approx(w_ring)  # loose same-spec holes don't widen it

    def test_footprint_reduction_and_left_floor(self):
        # Direct unit test of the reducer: deepest band per side wins, and the
        # left keeps its _DIM_PAD floor even when the deepest left band is
        # shallower — the branch real parts rarely make the deciding one.
        from draftwright._core import _DIM_PAD
        from draftwright.compose import AnnoBox, StripDepths, _footprint_from_boxes

        fp = _footprint_from_boxes(
            [
                AnnoBox("right", 5.0),
                AnnoBox("right", 30.0),  # deeper right band wins
                AnnoBox("left", 5.0),  # below the floor → floor wins
                AnnoBox("plan_halo", 21.0),
            ]
        )
        assert fp == StripDepths(right=30.0, left=_DIM_PAD, pv_halo=21.0)

        # No bands at all → zero depths, but the left floor still applies.
        assert _footprint_from_boxes([]) == StripDepths(right=0.0, left=_DIM_PAD, pv_halo=0.0)

