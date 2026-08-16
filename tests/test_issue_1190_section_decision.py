"""#1190 — section A–A: find the free gap, and never omit it silently.

Two defects with one cause each, and a third that turned out not to be a defect:

* the room check started after the RIGHTMOST obstacle instead of looking for a gap,
  so one remote occupant vetoed a band the section fitted in;
* a withheld section was reported on one code path and not another — title-block at
  INFO, no-room at WARNING — so the same omission was visible on one part and silent
  on the next, and neither reached lint;
* and the reported "presence is non-monotonic in scale" was really presence being
  *unexplained*. A section that no longer fits is allowed to not fit; what was wrong
  was that nothing said so.
"""

from __future__ import annotations

import inspect

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.annotations import sections as sections_module


def _counterbored_block():
    """A Z-axis counterbored hole — the trigger `plan_sections` looks for."""
    return Box(60, 40, 20) - Pos(0, 0, 0) * Cylinder(4, 40) - Pos(0, 0, 6) * Cylinder(7, 8)


def _yielding_part():
    """A part whose section yields to a required hole-callout leader."""
    return (
        Box(44, 12, 44)
        - Pos(11, 0, 22) * Box(22, 12, 44)
        - Pos(-11, 0, 0) * Cylinder(3, 44)
        - Pos(-11, 0, 18) * Cylinder(5, 8)
    )


class TestTheOutcomeIsAlwaysRecorded:
    def test_a_part_needing_no_section_says_so(self):
        dwg = build_drawing(Box(60, 40, 20), page="A3")
        assert dwg.section_decision["status"] == "not_warranted"
        assert dwg.section_decision["reason"] is None

    def test_a_placed_section_is_recorded_as_placed(self):
        dwg = build_drawing(_counterbored_block(), page="A3")
        if dwg.view_bounds("section_aa") is None:
            pytest.skip("fixture did not place a section on this page")
        assert dwg.section_decision["status"] == "placed"

    def test_a_pass_that_never_ran_is_distinguished_from_one_that_found_nothing(self):
        # `auto_dims=False` skips `_auto_annotate` entirely, so nothing evaluates whether
        # a section is warranted. Reporting "no counterbore" there would be a wrong
        # answer ABOUT THE GEOMETRY on a part that has one — worse than no answer, since
        # the point of this field is that a caller trusts it instead of reading logs.
        dwg = build_drawing(_counterbored_block(), page="A3", auto_dims=False)
        assert dwg.section_decision["status"] == "not_evaluated"

    def test_the_status_vocabulary_is_closed(self):
        dwg = build_drawing(Box(60, 40, 20), page="A3")
        with pytest.raises(ValueError, match="unknown section status"):
            dwg.record_section_decision("maybe")

    def test_every_skip_path_goes_through_the_one_recorder(self):
        # The defect was two skip paths announcing at different levels. Exactly one
        # place may announce a skip; a second means a path reaching neither
        # section_decision nor lint_summary. Counted in the source, so a NEW skip path
        # is caught rather than each existing one being enumerated.
        source = inspect.getsource(sections_module)
        assert source.count("Section A–A skipped") == 1, (
            "a section skip announces itself outside the single recorder — that path "
            "will not reach section_decision or lint_summary"
        )


class TestAWithheldSectionIsNeverSilent:
    def test_the_omission_reaches_both_the_record_and_lint(self, monkeypatch):
        # The reported GRM-01 A4 case in portable form. That part is not in the repo, so
        # an earlier cut gated this on an absolute path that exists on one machine — for
        # the only regression test of the reported defect, never running in CI is the
        # same as not having one.
        monkeypatch.setattr(sections_module, "carve_free_segments", lambda *_a, **_k: [])
        dwg = build_drawing(_counterbored_block(), page="A3")
        assert dwg.section_decision["status"] == "skipped"
        assert dwg.section_decision["reason"], "a skip must name its reason"
        assert dwg.lint_summary()["by_code"].get("section_dropped", 0) >= 1, (
            "the omission did not reach lint_summary, which is where callers look"
        )

    def test_a_yielded_section_is_recorded_too(self):
        # This part's cutting-plane ink crosses a required hole-callout leader, so the
        # section yields. That is correct, and was previously invisible.
        dwg = build_drawing(_yielding_part())
        assert dwg.section_decision["status"] in {"placed", "skipped"}
        if dwg.section_decision["status"] == "skipped":
            assert dwg.section_decision["reason"] == "leader_conflict"


class TestTheReasonCodeIsTheRealReason:
    """A wrong reason is worse than a missing one — it sends a reader to the wrong
    constraint."""

    def test_nothing_fitting_reports_no_room_not_title_block(self, monkeypatch):
        # The first cut of this fix got exactly this wrong: with no fitting segment the
        # placeholder x fell past the title block too, and the title-block check ran
        # FIRST, so a section with nowhere to go was blamed on the title block.
        monkeypatch.setattr(sections_module, "carve_free_segments", lambda *_a, **_k: [])
        dwg = build_drawing(_counterbored_block(), page="A3")
        assert dwg.section_decision["reason"] == "no_room", (
            f"reported {dwg.section_decision['reason']!r} — a section with nowhere to "
            f"go must not be blamed on the title block"
        )

    def test_a_failed_cut_is_reported_as_a_failed_cut(self, monkeypatch):
        monkeypatch.setattr(sections_module, "_fuzzy_cut", lambda *_a, **_k: None)
        dwg = build_drawing(_counterbored_block(), page="A3")
        assert dwg.section_decision["reason"] == "cut_empty"

    def test_a_raising_cut_is_reported_as_a_failed_cut(self, monkeypatch):
        # OCC booleans raise broadly; the section must record that rather than let it
        # escape or vanish. Distinct from `cut_empty`, which returns None cleanly.
        def explode(*_args, **_kwargs):
            raise RuntimeError("Standard_DomainError")

        monkeypatch.setattr(sections_module, "_fuzzy_cut", explode)
        dwg = build_drawing(_counterbored_block(), page="A3")
        assert dwg.section_decision["reason"] == "cut_failed"
        assert "Standard_DomainError" in dwg.section_decision["detail"]

    def test_room_existing_but_blocked_by_the_title_block_says_title_block(self, monkeypatch):
        # The complement of the no_room test, and the other half of the ordering fix:
        # segments DO fit, and none clears the title block. Reporting `no_room` here
        # would be as wrong as blaming the title block when nothing fits.
        monkeypatch.setattr(
            sections_module, "_segments_clearing_title_block", lambda *_a, **_k: []
        )
        dwg = build_drawing(_counterbored_block(), page="A3")
        assert dwg.section_decision["reason"] == "title_block"
        assert "title block" in dwg.section_decision["detail"]


class TestTheRoomCheckFindsAGapRatherThanTheRightmostObstacle:
    def test_a_remote_obstacle_does_not_veto_the_whole_band(self):
        # Measured on GRM-01 at A4 before the fix: a label at x 245-270 — under the iso
        # view, past the right limit entirely — pushed the section start to x 275 while
        # x 202-239 sat free and the section needed 24 mm.
        from draftwright.annotations._common import carve_free_segments

        free = carve_free_segments(137.3, 246.3, [(140.0, 200.0), (245.1, 269.5)], 6.0)
        assert max((hi - lo for lo, hi in free), default=0.0) >= 24.0, (
            "the free band between the near obstacles and the remote one was lost; "
            "a remote occupant must not displace everything past it"
        )

    def test_a_later_segment_is_used_when_the_first_is_blocked(self):
        # Driven as a unit, because the condition only bites when the section's row dips
        # into the title-block band — page- and part-dependent. An earlier version of
        # this test drove it through a built drawing whose row did NOT dip in, so the
        # filter was a no-op and the test passed under a mutation that removed it.
        from draftwright.annotations.sections import _segments_clearing_title_block

        half_w, tb_left = 12.0, 160.0
        blocked_first = (150.0, 250.0)  # right edge 174 > tb_left - 4
        clear_second = (100.0, 140.0)  # right edge 124 <= tb_left - 4
        usable = _segments_clearing_title_block(
            [blocked_first, clear_second],
            half_w,
            shares_title_row=True,
            tb_left=tb_left,
        )
        assert usable == [clear_second], (
            "only the leftmost fitting segment is being tested against the title block"
        )

    def test_the_filter_is_inert_when_the_row_clears_the_title_block(self):
        from draftwright.annotations.sections import _segments_clearing_title_block

        fitting = [(150.0, 250.0), (100.0, 140.0)]
        assert (
            _segments_clearing_title_block(fitting, 12.0, shares_title_row=False, tb_left=160.0)
            == fitting
        )

    def test_no_segment_clearing_the_block_yields_nothing(self):
        # Which is what produces the `title_block` reason rather than `no_room`.
        from draftwright.annotations.sections import _segments_clearing_title_block

        assert (
            _segments_clearing_title_block(
                [(150.0, 250.0)], 12.0, shares_title_row=True, tb_left=160.0
            )
            == []
        )


class TestASkippedSectionIsNeverAScaleBlocker:
    """A section is optional furniture that yields — to a required leader, to the title
    block, to a page with no room. Recording that must not promote it to a REQUIRED
    outcome: an earlier cut marked it ``outcome_stage="placement"``, and adversarial
    review of #1191 found an explicit ``scale=`` request then rebuilding the whole ISO
    ladder and raising ``ScaleIncompatibilityError`` where the engine used to return a
    drawing — overruling, for a leader conflict, a choice the engine had just made in
    favour of a required annotation.
    """

    @pytest.mark.parametrize(
        ("reason", "attribute", "replacement"),
        [
            ("no_room", "carve_free_segments", lambda *_a, **_k: []),
            ("cut_empty", "_fuzzy_cut", lambda *_a, **_k: None),
        ],
    )
    def test_no_skip_reason_is_a_placement_drop(self, monkeypatch, reason, attribute, replacement):
        from draftwright.linting.issues import is_placement_drop

        monkeypatch.setattr(sections_module, attribute, replacement)
        dwg = build_drawing(_counterbored_block(), page="A3")
        assert dwg.section_decision["reason"] == reason
        dropped = [i for i in dwg.lint() if i.code == "section_dropped"]
        assert dropped, "the skip was not reported at all"
        assert not any(is_placement_drop(i) for i in dropped), (
            f"{reason} is a required scale outcome; an optional view must not be able "
            f"to make every scale infeasible"
        )

    def test_an_explicit_scale_survives_a_withheld_section(self):
        # The regression the review reproduced: this must return a drawing at the
        # requested scale, not burn the ISO ladder and raise.
        dwg = build_drawing(_yielding_part(), scale=1.0)
        assert dwg.scale == 1.0, (
            f"the requested scale was abandoned ({dwg.scale}) to chase an optional view"
        )


class TestThePresenceIsExplicableAtEveryScale:
    """The reported symptom was a section coming and going with scale for no visible
    reason. The fix is not to force it to persist — a section that no longer fits must
    be allowed to not fit — but to make every outcome legible."""

    @pytest.mark.slow
    def test_every_scale_records_an_outcome_with_a_reason(self):
        part = _counterbored_block()
        for requested in (1.0, 1.5, 2.0, 2.5, 3.0):
            dwg = build_drawing(part, page="A3", scale=requested, scale_policy="permissive")
            decision = dwg.section_decision
            assert decision["status"] in {"placed", "skipped", "not_warranted"}, (
                f"scale {requested}: {decision}"
            )
            if decision["status"] == "skipped":
                assert decision["reason"], f"scale {requested}: a skip must name its reason"
                assert decision["detail"], f"scale {requested}: a skip must explain itself"
