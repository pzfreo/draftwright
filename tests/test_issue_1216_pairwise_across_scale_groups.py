"""#1216 — the pairwise lint checks must compare annotations across scale groups.

`Drawing._lint` split the annotation list by `_dw_scale` and called `lint_drawing` once per
group, so `label_vs_measured` saw the right scale for an enlarged detail view (#42). The
**pairwise** checks — `annotation_overlap`, `label_centerline_overlap`,
`leader_line_through_text` — run inside that per-group call, so two annotations in different
groups were never compared with each other:

    TOGETHER (one group):  ['annotation_overlap']
    SPLIT    (two groups): []

That is the pair most likely to collide, not the least: a detail view's dimensions and its own
caption are always in different groups *and* spatially adjacent by construction. On the issue's
corpus the lone detail-scale annotation sat 12.7 mm from its nearest sheet-scale neighbour, with
the next nearest 115 mm away.

The fix is the issue's option 1 — per-annotation scale. `_lint_dim` receives each item's
registry-owned detail scale, so `lint_drawing` runs ONCE over every annotation. Option 2 (keep the split, add a
second pairwise pass over the union) was rejected: it needs a second place that decides which
checks are scale-sensitive, and a split owner in two places is the defect this codebase keeps
paying for — #1215 alone cost eight sites of it.

Collapsing the split also retires #1204's first-group restriction: with one call there are no
groups for the view-vs-view checks to be counted once per.
"""

from __future__ import annotations

import pytest
from build123d import Box, Pos
from build123d_drafting.helpers import Note

from draftwright.builder import build_drawing
from draftwright.linting.structural import lint_drawing


def _crowded_tiers():
    """Tiers 3 mm apart: the legibility gate moves rungs into an enlarged detail view, which is
    what produces a second scale group at all."""
    part = Pos(0, 0, 3) * Box(20, 16, 6)
    z = 6
    for w in (16, 13, 10, 7, 5):
        part += Pos(0, 0, z + 1.5) * Box(w, 12, 3)
        z += 3
    return part


class TestTheFixtureReallyHasTwoScaleGroups:
    def test_a_detail_view_produces_a_second_scale_group(self):
        # The precondition. Without two groups nothing below can distinguish the fix from the
        # defect — every assertion would hold on unfixed code.
        drawing = build_drawing(_crowded_tiers(), title="T", number="N")
        scales = {drawing.scale} | {
            drawing.registry.scale_of(name)
            for name in drawing.registry.names()
            if drawing.registry.scale_of(name) is not None
        }
        assert len(scales) >= 2, scales


class TestPairwiseChecksSeeAcrossGroups:
    def test_two_overlapping_annotations_in_different_groups_are_compared(self):
        """The issue's own demonstration, through the real `Drawing` lint path.

        Two `Note`s at the same point, tagged with different scales. Before the fix the split
        put them in separate calls and no pairwise check ever saw the pair.
        """
        drawing = build_drawing(Box(50, 40, 20), title="T", number="N")
        drawing.views.clear()
        drawing.part = None
        here = (60, 60, 0)
        first, second = (
            Note("HELLO", here, draft=drawing.draft),
            Note("WORLD", here, draft=drawing.draft),
        )
        drawing.items = [first, second]
        drawing.registry.add(first, "first", None, scale=drawing.scale)
        drawing.registry.add(second, "second", None, scale=drawing.scale * 2)

        codes = {issue.code for issue in drawing.lint()}
        assert "annotation_overlap" in codes, codes

    def test_the_same_pair_in_one_group_was_always_caught(self):
        # The control: the defect was never about the check itself, only about which pairs
        # reached it. If this fails the fixture stopped overlapping and the test above proves
        # nothing.
        drawing = build_drawing(Box(50, 40, 20), title="T", number="N")
        drawing.views.clear()
        drawing.part = None
        here = (60, 60, 0)
        first, second = (
            Note("HELLO", here, draft=drawing.draft),
            Note("WORLD", here, draft=drawing.draft),
        )
        drawing.items = [first, second]

        assert "annotation_overlap" in {issue.code for issue in drawing.lint()}


class TestTheScaleSensitiveCheckStaysCorrect:
    """The split existed for a reason: `label_vs_measured` compares each annotation against its
    OWN scale. Reading the tag per item has to preserve that, or the fix trades one defect for
    another."""

    def test_a_detail_scale_dim_is_not_flagged_against_the_sheet_scale(self):
        drawing = build_drawing(_crowded_tiers(), title="T", number="N")
        detail = [
            n
            for n in drawing.registry.names()
            if drawing.registry.scale_of(n) not in (None, drawing.scale)
        ]
        assert detail, "no detail-scale annotation; this asserts nothing"
        assert "label_vs_measured" not in {issue.code for issue in drawing.lint()}

    @pytest.mark.parametrize("scale", [1.0, 2.5, 5.0])
    def test_lint_dim_reads_the_items_own_scale(self, scale):
        """Directly: an item whose label matches its own scale is clean, whatever the sheet's
        default says. A `measured_length` of `7.5 * scale` against label `7.5`."""
        from types import SimpleNamespace

        item = SimpleNamespace(
            label="7.5",
            measured_length=7.5 * scale,
            elbow=None,
            is_dimension=True,
        )
        issues = lint_drawing([item], drawing_scale=1.0, annotation_scales={id(item): scale})
        assert not [i for i in issues if i.code == "label_vs_measured"], (scale, issues)
