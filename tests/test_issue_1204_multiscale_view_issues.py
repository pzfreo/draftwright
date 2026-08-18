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
from types import SimpleNamespace

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


def _split_into_scale_groups(drawing, groups=2):
    """Retag annotations so the drawing has *groups* distinct scales, as detail views do."""
    measured = [a for a in drawing.items if getattr(a, "measured_length", None) is not None]
    extra = groups - 1
    # STRICTLY more, not `>=`: one annotation must stay at the sheet scale or the default
    # group disappears and the split is one group short. Tagging every measured annotation
    # gave 2 groups when 3 were asked for.
    assert len(measured) > extra, (
        f"fixture has {len(measured)} measured annotations; cannot form {groups} groups"
    )
    # Derived from the sheet scale so a tag cannot COLLIDE with it. Fixed values of 4.0/5.0
    # silently produced one group too few, because `Box(50,50,50)` at A1 is itself drawn at
    # one of them and an untagged annotation reports the sheet scale.
    for index, annotation in enumerate(measured[:extra]):
        annotation._dw_scale = drawing.scale * (index + 2)
    assert len({getattr(a, "_dw_scale", drawing.scale) for a in drawing.items}) == groups
    return measured[:extra]


def _split_into_two_scale_groups(drawing):
    return _split_into_scale_groups(drawing, 2)


def _drawing_producing_both_families():
    """A drawing that reports both code families, WITHOUT depending on layout.

    An earlier version used `Box(50,50,50)` at A1 and relied on the engine happening to
    overlap two views and drop an envelope label inside a third. That held on macOS and
    produced NO view findings at all on Linux, so every test in this file failed in CI —
    the same trap #1196's tests hit, repeated here after the lesson had been written down.

    Both families are now forced:

    * a stub annotation is placed inside the REAL bounds of a real view, read at runtime,
      so `view_annotation_*` fires wherever the engine put that view;
    * the drawable area is shrunk AFTER the build, so every view necessarily overflows and
      `view_out_of_bounds` fires whatever the layout chose.
    """
    drawing = build_drawing(Box(60, 40, 20), page="A3")
    bounds = drawing.view_bounds("front")
    assert bounds is not None, "the fixture no longer has a front view to place inside"
    cx, cy = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    drawing.items.append(
        SimpleNamespace(
            label="STUB", label_bbox=(cx - 4, cy - 1.5, cx + 4, cy + 1.5), measured_length=None
        )
    )
    drawing.page_w, drawing.page_h = 60.0, 60.0
    return drawing


class TestTheCountsDoNotMoveWithTheGrouping:
    @pytest.mark.parametrize("groups", [2, 3])
    def test_view_level_findings_are_reported_once(self, groups):
        # The reported defect. Mutation: pass `check_view_placement=True` for every group
        # and these scale linearly with the group count.
        #
        # THREE groups as well as two: `first = index != 0` — an off-by-one that restores
        # the defect at (N-1)x — is coincidentally correct at exactly two groups, and every
        # test created exactly two, so it passed the whole suite.
        drawing = _drawing_producing_both_families()
        before = _counts(drawing)
        assert set(before) & _VIEW_ONLY, f"fixture produced no view-level finding: {before}"

        _split_into_scale_groups(drawing, groups)
        after = _counts(drawing)
        for code in _VIEW_ONLY:
            assert after[code] == before[code], (
                f"{code} went {before[code]} -> {after[code]} purely from adding a scale "
                f"group; the drawing did not change"
            )

    def test_annotation_findings_survive_in_every_group(self):
        # The regression the first cut introduced. Nulling the views for later groups made
        # these vanish for the retagged annotation.
        #
        # Only codes the fixture ACTUALLY produces are compared. An earlier version looped
        # over all three and asserted `0 == 0` for two of them, so moving the guard one loop
        # earlier — which silently drops `leader_crosses_silhouette` for every group after
        # the first — passed the whole suite. `test_a_real_multi_group_part_keeps_its_leader
        # _findings` below covers that code on a real part.
        drawing = _drawing_producing_both_families()
        before = _counts(drawing)
        present = {c for c in _VIEW_VS_ANNOTATION if before[c] > 0}
        assert present, f"fixture produced no annotation-vs-view finding: {before}"

        _split_into_two_scale_groups(drawing)
        after = _counts(drawing)
        for code in present:
            assert after[code] == before[code], (
                f"{code} went {before[code]} -> {after[code]}; splitting the annotations by "
                f"scale must not lose the findings of a group"
            )

    def test_a_real_multi_group_part_keeps_its_leader_findings(self):
        # `leader_crosses_silhouette` is annotation-dependent and the synthetic fixture
        # produces none, so nothing guarded it: moving the guard above that loop passed
        # everything. CTC-05 has two GENUINE scale groups (0.2 and 1.0) and two leader
        # findings, so it exercises the preserved half on a real part rather than a retag.
        drawing = build_drawing(step_file="tests/fixtures/nist_ctc_05_asme1_ap242.stp")
        groups = {getattr(a, "_dw_scale", drawing.scale) for a in drawing.items}
        assert len(groups) >= 2, f"fixture no longer has multiple scale groups: {groups}"
        assert _counts(drawing)["leader_crosses_silhouette"] == 2, (
            "the leader findings of a later scale group were lost"
        )

    def test_the_summarys_view_counts_do_not_move_with_the_grouping(self):
        # The property that matters to a caller, stated over the VIEW families only.
        #
        # Not over the whole summary: retagging an annotation's `_dw_scale` genuinely
        # changes what that annotation asserts — its label no longer matches its geometry
        # at 4:1 — so a new `label_vs_measured` is correct, not drift. A first version of
        # this test compared the entire `by_code` and failed on exactly that, which would
        # have been a real finding about the test rather than the code.
        drawing = _drawing_producing_both_families()
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
        drawing = _drawing_producing_both_families()
        assert {getattr(a, "_dw_scale", None) for a in drawing.items} == {None}, (
            "the fixture already has more than one scale group"
        )
        assert set(_counts(drawing)) >= {"view_out_of_bounds"}, (
            "the forced overflow stopped firing, so this asserts nothing"
        )
        assert set(_counts(drawing)), "the single-group path stopped reporting view findings"
