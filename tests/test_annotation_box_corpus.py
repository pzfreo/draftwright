"""Annotation-box composition corpus and structure contracts."""

from _layout_helpers import _sizing_model
from _parts import dense_plate as _dense_plate
from _parts import holed_plate as _holed_plate
from _parts import multi_hole_plate as _multi_hole_plate
from build123d import Box, Cylinder, Pos
from test_annotation_box_composition import TestComposeAnnoBoxes as _ComposeAnnoBoxes


class TestComposeAnnoBoxesCorpus:
    """Step 4b (#112): de-risk the 4c reservation switch by proving the AnnoBox
    composer is a faithful drop-in for _measure_strips across the full part
    archetype corpus, and by pinning the per-side box *structure* that 4c will
    consume. The strip estimate now reduces these boxes, so byte-identity here
    guards the active layout path."""

    @staticmethod
    def _corpus():
        """The part archetypes draftwright draws, spanning every branch of
        _compose_anno_boxes: a plain prismatic block (right ladder only), a
        single bore and a multi-spec / corner-holed plate (left+right bore
        bands), and a dense plate that escalates to the leadered hole chart
        (plan halo band). The right dim ladder depth is a pure function of the
        n_steps argument (not geometry), so it is swept per part below rather
        than via a dedicated stepped fixture. Each entry carries the sizing IR
        model + planner callout width the estimators now consume (#584 WP1 A)."""
        parts = {
            "plain_block": Box(60, 40, 12),
            "single_bore": Box(60, 40, 12) - Pos(0, 0, 6) * Cylinder(3, 12),
            "multi_hole": _multi_hole_plate(),
            "holed_plate": _holed_plate(),
            "dense_balloon": _dense_plate(),
        }
        corpus = []
        for label, part in parts.items():
            model, w = _sizing_model(part)
            corpus.append((label, model, w, part.bounding_box()))
        return corpus

    def test_byte_identity_across_corpus(self):
        helper = _ComposeAnnoBoxes()
        for label, model, w, bb in self._corpus():
            for n_steps in (0, 1, 4):
                helper._assert_match(model, n_steps, bb, w, label=label)

    def test_box_structure_contract(self):
        """The per-side box structure 4c consumes: the right dim ladder is
        always emitted at the estimated depth; bore bands come as one
        equal-depth left/right pair iff the part has annotatable holes; the
        plan halo appears iff the plan view will balloon. (_footprint_from_boxes
        folding these back to the StripDepths estimate is covered above.)"""
        from draftwright.compose import (
            _compose_anno_boxes,
            _est_right_strip_depth,
            _will_balloon,
        )

        for label, model, w, _bb in self._corpus():
            for n_steps in (0, 2):
                boxes = _compose_anno_boxes(model, n_steps, bore_callout_width=w)
                rights = [b.depth for b in boxes if b.side == "right"]
                lefts = [b.depth for b in boxes if b.side == "left"]
                halos = [b for b in boxes if b.side == "plan_halo"]

                # The right dim ladder is always present, at the estimated depth.
                assert _est_right_strip_depth(n_steps) in rights, (label, n_steps)

                if w > 0:
                    # Bore bands are emitted as a single equal-depth left/right
                    # pair — the symmetry _measure_strips' max() collapses.
                    assert len(lefts) == 1, (label, n_steps)
                    assert lefts[0] in rights, (label, n_steps)
                else:
                    assert lefts == [], (label, n_steps)

                # The halo band is emitted exactly when the part will balloon.
                assert bool(halos) == _will_balloon(model), (label, n_steps)


# ---------------------------------------------------------------------------
