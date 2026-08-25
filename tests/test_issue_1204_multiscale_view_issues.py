"""#1204 — lint counts must describe the drawing, not how its annotations are grouped.

`Drawing._lint` used to split annotations by `_dw_scale` so `label_vs_measured` could compare
each against its own scale — an enlarged detail view (#42) carries one. It then called
`lint_drawing` once per group, passing the full view list every time, so every VIEW-level
finding was emitted once per group:

    one scale group   view_overlap: 1, view_out_of_bounds: 1
    two scale groups  view_overlap: 2, view_out_of_bounds: 2

The drawing is identical; only the number of groups changed. That made
`lint_summary()["by_code"]`, the error and warning counts, and the quality score a function
of annotation grouping rather than of the drawing — the same class of defect as #1196,
where lint TEXT depended on memory addresses.

**#1204's fix was a `check_view_placement` flag suppressing those checks for every group after
the first. #1216 removed the split instead** — `_lint_dim` reads each annotation's own
`_dw_scale`, so `lint_drawing` runs once over everything — and the flag went with it, because
the double-count no longer happens rather than being suppressed after the fact. (The split was
also what made the PAIRWISE checks blind across groups: a detail view's dims and its own
caption are always in different groups and spatially adjacent by construction, so the pair most
likely to collide was the one pair never checked.)

**What this file still guards is the property, not the mechanism.** Retagging annotations into
N scale groups must not move any lint count — which is now true because nothing consumes the
tag except the per-annotation scale lookup. These tests are therefore the regression net for
re-introducing ANY grouping-dependent lint pass, and they are written against `drawing.lint()`
rather than against the flag they originally described.

The finer distinction the flag encoded is worth keeping written down, because it is what any
future grouping would have to respect: `view_overlap` and `view_out_of_bounds` compare views
against each other and against the page, so they are group-independent; `view_annotation_overlap`,
`view_annotation_inside_extents` and `leader_crosses_silhouette` compare views against
ANNOTATIONS, and each group holds different ones. #1204's first cut nulled `view_shapes` for
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
    # Derived from the sheet scale so a tag cannot COLLIDE with it. Fixed values silently
    # produced one group too few on an earlier fixture that happened to be drawn at one of
    # them, since an untagged annotation reports the sheet scale.
    for index, annotation in enumerate(measured[:extra]):
        annotation._dw_scale = drawing.scale * (index + 2)
    assert len({getattr(a, "_dw_scale", drawing.scale) for a in drawing.items}) == groups
    return measured[:extra]


def _split_into_two_scale_groups(drawing):
    return _split_into_scale_groups(drawing, 2)


def _drawing_producing_both_families():
    """A drawing reporting EVERY code in both families, none of it dependent on layout.

    Three earlier fixtures each failed a different way, and all three failures were the
    same mistake — asserting on a finding the engine might or might not produce:

    * `Box(50,50,50)` at A1 relied on the engine overlapping two views and dropping a label
      inside a third. That held on macOS and produced NO view findings on Linux, so every
      test here failed in CI.
    * Replacing it with a runtime-placed stub plus a page shrink forced
      `view_annotation_*` and `view_out_of_bounds`, but never `view_overlap` — so the loop
      over `_VIEW_ONLY` asserted `0 == 0` for that code and a mutation restoring its
      duplication passed the whole suite.
    * The stub carried no `measured_length`, so it could never be retagged into a later
      scale group: the forced finding only protected whichever group it happened to land
      in, by luck of item order.

    So every finding is now constructed:

    * a stub annotation inside the REAL `view_bounds("front")`, read at runtime, forces
      `view_annotation_*` wherever the engine put that view — and it carries a
      `measured_length` so the retag helper can move it into a non-first group;
    * a second NAME for the front view's own shape forces `view_overlap`: one shape cannot
      fail to overlap itself;
    * shrinking the drawable area after the build forces `view_out_of_bounds`, since the
      part is wider than the whole remaining area at any scale A3 permits.
    """
    drawing = build_drawing(Box(60, 40, 20), page="A3")
    bounds = drawing.view_bounds("front")
    assert bounds is not None, "the fixture no longer has a front view to place inside"
    cx, cy = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    drawing.items.append(
        SimpleNamespace(
            label="STUB", label_bbox=(cx - 4, cy - 1.5, cx + 4, cy + 1.5), measured_length=12.0
        )
    )
    drawing.views["front_echo"] = drawing.views["front"]
    drawing.page_w, drawing.page_h = 60.0, 60.0
    return drawing


def _retag_into_a_later_group(drawing, label="STUB"):
    """Move the annotation carrying a forced finding into a NON-FIRST scale group.

    Which group a finding lands in is what the flag actually gates, and leaving it to item
    order meant the tests protected whichever group the stub fell in by accident.
    """
    first = next(iter({getattr(a, "_dw_scale", drawing.scale) for a in drawing.items}))
    for annotation in drawing.items:
        if getattr(annotation, "label", None) == label:
            annotation._dw_scale = drawing.scale * 7
            assert getattr(annotation, "_dw_scale") != first
            return annotation
    pytest.fail(f"no annotation labelled {label!r} to move")


class TestTheCountsDoNotMoveWithTheGrouping:
    @pytest.mark.parametrize("groups", [2, 3])
    def test_view_level_findings_are_reported_once(self, groups):
        # The reported defect. Mutation, in the shape it exists now: restore the per-group
        # split in `Drawing._lint` and these scale linearly with the group count. (Until
        # #1216 the mutation was `check_view_placement=True` for every group; that flag no
        # longer exists, because the split it compensated for no longer exists.)
        #
        # THREE groups as well as two: `first = index != 0` — an off-by-one that restores
        # the defect at (N-1)x — is coincidentally correct at exactly two groups, and every
        # test created exactly two, so it passed the whole suite.
        drawing = _drawing_producing_both_families()
        before = _counts(drawing)
        assert _VIEW_ONLY <= set(before), (
            f"the fixture stopped forcing a view-level code: {before}. Both must be present "
            f"or the loop below asserts 0 == 0 for the missing one, which is how a mutation "
            f"restoring `view_overlap` duplication passed the whole suite."
        )

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
        # Only `inside_extents` is forced by CONSTRUCTION: the stub sits at the centre of
        # the view's bounds, so it is inside them everywhere. Whether it ALSO crosses a
        # projected edge — which is what promotes it to `view_annotation_overlap` — depends
        # on where the edges fall, and that differs by platform: macOS produced both codes,
        # Linux only the first. Requiring both was the fourth fixture in this file to assert
        # on a layout outcome.
        assert "view_annotation_inside_extents" in present, (
            f"the stub stopped landing inside the view it was placed in: {before}"
        )
        # The finding must be in a LATER group, which is what the flag gates. Leaving that
        # to item order let a mutation dropping these for later groups pass.
        _retag_into_a_later_group(drawing)

        after = _counts(drawing)
        for code in present:
            assert after[code] == before[code], (
                f"{code} went {before[code]} -> {after[code]}; splitting the annotations by "
                f"scale must not lose the findings of a group"
            )

    @pytest.mark.slow  # >=30 s inherent dense build; post-merge tier (#656)
    def test_a_real_multi_group_part_keeps_its_leader_findings(self):
        # `leader_crosses_silhouette` is annotation-dependent, and the synthetic fixture
        # produces none, so only a real part covers it. CTC-05 has two genuine scale groups
        # (0.2 and 1.0).
        #
        # An earlier version stopped there and constrained NOTHING: the leader findings
        # sit at scale 0.2, which was group index 0 and always got the flag. Moving the
        # guard above the leader loop — dropping those findings for every later group —
        # passed all 4,178 tests, while the comment here claimed the half was covered.
        # Tagging the first item pushes the leader-carrying group off index 0, which is
        # what any reintroduced grouping would have to survive. With #1216's single call
        # there is no index at all, so this now asserts the property directly: the tag
        # changes nothing.
        drawing = build_drawing(step_file="tests/fixtures/nist_ctc_05_asme1_ap242.stp")
        before = _counts(drawing)["leader_crosses_silhouette"]
        assert before > 0, "fixture no longer supplies a real leader finding"

        drawing.items[0]._dw_scale = drawing.scale * 97
        order = []
        for annotation in drawing.items:
            scale = getattr(annotation, "_dw_scale", drawing.scale)
            if scale not in order:
                order.append(scale)
        leaders = {
            getattr(a, "_dw_scale", drawing.scale)
            for a in drawing.items
            if getattr(a, "elbow", None) is not None
        }
        assert order.index(next(iter(leaders))) > 0, (
            f"the leader-carrying group is still first ({order}, leaders at {leaders}), so "
            f"this asserts nothing about the flag"
        )
        assert _counts(drawing)["leader_crosses_silhouette"] == before, (
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


class TestTheReasonForTheSplitIsStillHonoured:
    def test_an_annotation_is_linted_at_its_own_scale(self):
        # The split existed so `label_vs_measured` compares an annotation against ITS OWN
        # scale; #1216 keeps that and drops the split by reading the tag per annotation. So
        # the item carries `_dw_scale` and the SHEET is 1:1 — the arrangement a detail view
        # actually produces. Passing the detail's scale as `drawing_scale` with no tag on the
        # item, as this test did, exercises only the fallback and would pass against a
        # `_lint_dim` that ignored `_dw_scale` entirely (#1216 review r9, F6).
        from types import SimpleNamespace

        from draftwright.linting.structural import _lint_dim

        # A 4:1 detail: 40 mm of drawn path for a 10 mm feature, on a 1:1 sheet.
        tagged = SimpleNamespace(label="10", measured_length=40.0, _dw_scale=4.0)
        issues: list = []
        _lint_dim(tagged, None, issues, 1.0)
        assert not [i for i in issues if i.code == "label_vs_measured"], (
            f"a correctly scaled detail dimension was reported as contradictory: "
            f"{[i.message for i in issues]}"
        )

        # The control: the SAME item untagged is a contradiction at 1:1, so the assertion
        # above is about the tag being read and not about the comparison being lax.
        untagged = SimpleNamespace(label="10", measured_length=40.0)
        control: list = []
        _lint_dim(untagged, None, control, 1.0)
        assert [i for i in control if i.code == "label_vs_measured"], (
            "the untagged control is clean too, so this test cannot tell whether `_dw_scale` "
            "was read"
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

    def test_the_message_names_the_divisor_it_used(self):
        # #1216 review r9, F4. `_lint_dim` divides by the item's own `_dw_scale` and printed
        # the SHEET scale: `÷5.0 = 15.000` for 45 mm divided by 3.0. The clause was also gated
        # on the sheet scale, so on a 1:1 sheet it vanished entirely and the percentage could
        # not be derived from any number in the message — the #1196 defect class, where lint
        # text says something the lint did not do.
        from types import SimpleNamespace

        from draftwright.linting.structural import _lint_dim

        issues: list = []
        item = SimpleNamespace(label="10", measured_length=45.0, _dw_scale=3.0)
        _lint_dim(item, None, issues, 5.0)
        contradictions = [i for i in issues if i.code == "label_vs_measured"]
        assert len(contradictions) == 1, f"precondition: expected one finding, got {issues}"
        message = contradictions[0].message
        assert "(÷3.0 = 15.000)" in message, message
        assert "÷5.0" not in message, message
        # And it is present on a 1:1 sheet, which is where it used to be omitted.
        one_to_one: list = []
        _lint_dim(item, None, one_to_one, 1.0)
        assert "(÷3.0 = 15.000)" in one_to_one[0].message, one_to_one[0].message

    def test_a_degenerate_item_scale_falls_back_to_the_sheet_scale(self):
        # `drawing_scale` is validated positive by `lint_drawing`; a per-annotation
        # `_dw_scale` is not on that path. A zero tag must fall back rather than divide:
        # before the guard, `measured / 0` raised past the validated entry point.
        from types import SimpleNamespace

        from draftwright.linting.structural import _lint_dim

        issues: list = []
        item = SimpleNamespace(label="10", measured_length=20.0, _dw_scale=0.0)
        _lint_dim(item, None, issues, 2.0)  # 20 / 2.0 = 10 — clean via the fallback
        assert not [i for i in issues if i.code == "label_vs_measured"], (
            f"the zero-tag fallback did not use the sheet scale: {[i.message for i in issues]}"
        )
