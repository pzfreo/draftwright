"""#1204 — lint counts must describe the drawing, not how its annotations are grouped.

`Drawing._lint` splits annotations by `_dw_scale` so `label_vs_measured` compares each
against its own scale — an enlarged detail view (#42) carries one. It then called
`lint_drawing` once per group, passing the full view list every time, so every VIEW-level
finding was emitted once per group:

    one scale group   view_overlap: 1, view_out_of_bounds: 1
    two scale groups  view_overlap: 2, view_out_of_bounds: 2

The drawing is identical; only the number of groups changed. That made
`lint_summary()["by_code"]`, the error and warning counts, and the quality score a function
of annotation grouping rather than of the drawing — the same class of defect as #1196,
where lint TEXT depended on memory addresses.

The split is finer than "don't pass the views twice". `view_overlap` and
`view_out_of_bounds` compare views against each other and against the page, so they are
group-independent and run once. `view_annotation_overlap`, `view_annotation_inside_extents`
and `leader_crosses_silhouette` compare views against ANNOTATIONS, and each group holds
different ones — so they must run for every group. A first cut nulled `view_shapes` for
later groups and lost their findings: `view_annotation_inside_extents` went 2 to 1 on the
fixture below, trading a double-count for a missed defect.
"""

from __future__ import annotations

from collections import Counter

import pytest
from build123d import Box

from draftwright import build_drawing

#: Codes emitted by `_lint_view_shapes` comparing views to EACH OTHER or to the page. These
#: say nothing about annotations, so their count must not move with the grouping.
_VIEW_ONLY = frozenset({"view_overlap", "view_out_of_bounds"})

#: Codes comparing a view to an ANNOTATION. Every group holds different annotations, so
#: these must be evaluated for all of them.
_VIEW_VS_ANNOTATION = frozenset(
    {"view_annotation_overlap", "view_annotation_inside_extents", "leader_crosses_silhouette"}
)


def _counts(drawing):
    return Counter(i.code for i in drawing.lint() if i.code.startswith(("view_", "leader_")))


def _split_into_two_scale_groups(drawing):
    """Tag one annotation with a second scale, as an enlarged detail view does."""
    for annotation in drawing.items:
        if getattr(annotation, "measured_length", None) is not None:
            annotation._dw_scale = 4.0
            return annotation
    pytest.fail("fixture has no measured annotation to retag; the split cannot be exercised")


def _part():
    """A cube on a large sheet — its views overlap and overflow, and its envelope labels
    land inside the front view, so it produces both families of code at once."""
    return Box(50, 50, 50)


class TestTheCountsDoNotMoveWithTheGrouping:
    def test_view_level_findings_are_reported_once(self):
        # The reported defect. Mutation: pass `view_geometry=True` for every group and
        # these double.
        drawing = build_drawing(_part(), page="A1")
        before = _counts(drawing)
        assert set(before) & _VIEW_ONLY, f"fixture produced no view-level finding: {before}"

        _split_into_two_scale_groups(drawing)
        after = _counts(drawing)
        for code in _VIEW_ONLY:
            assert after[code] == before[code], (
                f"{code} went {before[code]} -> {after[code]} purely from adding a scale "
                f"group; the drawing did not change"
            )

    def test_annotation_findings_survive_in_every_group(self):
        # The regression the first cut introduced. Nulling the views for later groups made
        # these vanish for the retagged annotation.
        drawing = build_drawing(_part(), page="A1")
        before = _counts(drawing)
        assert set(before) & _VIEW_VS_ANNOTATION, (
            f"fixture produced no annotation-vs-view finding: {before}"
        )

        _split_into_two_scale_groups(drawing)
        after = _counts(drawing)
        for code in _VIEW_VS_ANNOTATION:
            assert after[code] == before[code], (
                f"{code} went {before[code]} -> {after[code]}; splitting the annotations by "
                f"scale must not lose the findings of a group"
            )

    def test_the_summarys_view_counts_do_not_move_with_the_grouping(self):
        # The property that matters to a caller, stated over the VIEW families only.
        #
        # Not over the whole summary: retagging an annotation's `_dw_scale` genuinely
        # changes what that annotation asserts — its label no longer matches its geometry
        # at 4:1 — so a new `label_vs_measured` is correct, not drift. A first version of
        # this test compared the entire `by_code` and failed on exactly that, which would
        # have been a real finding about the test rather than the code.
        drawing = build_drawing(_part(), page="A1")
        families = _VIEW_ONLY | _VIEW_VS_ANNOTATION
        before = {k: v for k, v in drawing.lint_summary()["by_code"].items() if k in families}
        _split_into_two_scale_groups(drawing)
        after = {k: v for k, v in drawing.lint_summary()["by_code"].items() if k in families}
        assert before, "the fixture reports no view-family findings at all"
        assert after == before, (
            f"view-family counts moved with the grouping:\n  before {before}\n  after  {after}"
        )


class TestTheSplitStillDoesWhatItIsFor:
    def test_each_group_is_linted_at_its_own_scale(self):
        # The scale split exists so `label_vs_measured` compares an annotation against ITS
        # OWN scale. Restricting the view checks must not disturb that: a detail-view
        # annotation whose label matches its own scale stays clean.
        from types import SimpleNamespace

        from draftwright.linting.structural import _lint_dim

        issues: list = []
        # A 4:1 detail: 40 mm of drawn path for a 10 mm feature.
        _lint_dim(SimpleNamespace(label="10", measured_length=40.0), None, issues, 4.0)
        assert not [i for i in issues if i.code == "label_vs_measured"], (
            "a correctly scaled detail dimension was reported as contradictory"
        )

    def test_a_single_group_drawing_is_untouched(self):
        # The common case takes the other branch entirely and must be unaffected.
        drawing = build_drawing(_part(), page="A1")
        assert {getattr(a, "_dw_scale", None) for a in drawing.items} == {None}, (
            "the fixture already has more than one scale group"
        )
        assert set(_counts(drawing)), "the single-group path stopped reporting view findings"
