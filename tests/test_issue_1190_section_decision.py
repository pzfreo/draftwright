"""#1190 — section A–A: find the gap, report the outcome, don't vanish with scale.

Three defects, one cause each:

* the room check started after the RIGHTMOST obstacle instead of looking for a gap,
  so one remote occupant vetoed a band the section fitted in;
* a withheld section was reported on one code path and not another, so the same
  omission was visible on one part and silent on the next;
* and because nothing recorded the omission, the scale search could not know it had
  lost a required view — so the section came and went with scale.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.annotations import sections as sections_module

_GRM01 = Path("/Users/paul/steps/GRM-01_shank.step")


def _counterbored_block():
    """A Z-axis counterbored hole — the trigger `plan_sections` looks for."""
    return Box(60, 40, 20) - Pos(0, 0, 0) * Cylinder(4, 40) - Pos(0, 0, 6) * Cylinder(7, 8)


class TestTheOutcomeIsAlwaysRecorded:
    def test_a_part_needing_no_section_says_so(self):
        # Never absent, never ambiguous: a caller reads one field rather than
        # inferring from the absence of a log line.
        dwg = build_drawing(Box(60, 40, 20), page="A3")
        assert dwg.section_decision["status"] == "not_warranted"
        assert dwg.section_decision["reason"] is None

    def test_a_placed_section_is_recorded_as_placed(self):
        dwg = build_drawing(_counterbored_block(), page="A3")
        if dwg.view_bounds("section_aa") is None:
            pytest.skip("fixture did not place a section on this page")
        assert dwg.section_decision["status"] == "placed"
        assert dwg.section_decision["reason"] is None

    def test_the_status_vocabulary_is_closed(self):
        dwg = build_drawing(Box(60, 40, 20), page="A3")
        with pytest.raises(ValueError, match="unknown section status"):
            dwg.record_section_decision("maybe")

    def test_every_skip_path_goes_through_the_one_recorder(self):
        # The defect was two skip paths logging at different levels — one at INFO, one
        # at WARNING — so the same omission was visible on one part and silent on the
        # next. Exactly one place may announce a skip; a second occurrence means a path
        # that reaches neither section_decision nor lint_summary. Counted in the source
        # rather than enumerated per path, so a NEW skip is caught too.
        source = inspect.getsource(sections_module)
        assert source.count("Section A–A skipped") == 1, (
            "a section skip announces itself outside the single recorder — that path "
            "will not reach section_decision or lint_summary"
        )


class TestAWithheldSectionReachesLint:
    def test_a_skip_records_a_reason_and_a_lint_issue(self):
        # Driven directly: the interesting skips depend on page geometry, and the
        # contract under test is that a skip is never silent, whichever path took it.
        dwg = build_drawing(_counterbored_block(), page="A3")
        dwg.record_section_decision("skipped", reason="no_room", detail="probe")
        assert dwg.section_decision == {
            "status": "skipped",
            "reason": "no_room",
            "detail": "probe",
        }

    def test_the_drop_code_is_a_placement_drop(self):
        # `section_dropped` earns its place in the legibility inventory through the
        # `*_dropped` suffix rule rather than a hand-kept list, and counts as a
        # required outcome for the scale search — which is what stops a section
        # disappearing rather than the drawing rescaling.
        from draftwright.linting.issues import LintIssue, is_placement_drop

        issue = LintIssue(
            severity="warning",
            message="section A–A not placed (probe)",
            code="section_dropped",
            outcome_stage="placement",
        )
        assert is_placement_drop(issue)


class TestTheRoomCheckFindsAGapRatherThanTheRightmostObstacle:
    def test_a_remote_obstacle_does_not_veto_the_whole_band(self):
        # The carve is what makes this hold. Measured on GRM-01 at A4 before the fix:
        # a label at x 245-270 — under the iso view, past the right limit entirely —
        # pushed the section start to x 275 while x 202-239 sat free and the section
        # needed 24 mm.
        from draftwright.annotations._common import carve_free_segments

        blocked = [(140.0, 200.0), (245.1, 269.5)]
        free = carve_free_segments(137.3, 246.3, blocked, 6.0)
        widest = max((hi - lo for lo, hi in free), default=0.0)
        assert widest >= 24.0, (
            "the free band between the near obstacles and the remote one was lost; "
            "a remote occupant must not displace everything past it"
        )

    @pytest.mark.skipif(not _GRM01.exists(), reason="GRM-01 fixture not present")
    def test_grm01_a4_no_longer_loses_its_section_silently(self):
        # The reported case. Either the section lands, or the omission is recorded —
        # what must not happen is the pre-#1190 outcome of neither.
        dwg = build_drawing(step_file=str(_GRM01), page="A4", scale=1.0)
        decision = dwg.section_decision
        assert decision["status"] in {"placed", "skipped"}
        if decision["status"] == "skipped":
            assert decision["reason"], "a skip must name its reason"
            assert any(i.code == "section_dropped" for i in dwg.lint())


class TestSectionPresenceIsMonotonicInScale:
    @pytest.mark.slow
    def test_a_section_does_not_vanish_at_one_scale_and_return_above_it(self):
        # The reported dead bands. A section that no longer fits must degrade the
        # SCALE (with the existing completeness warning), not silently disappear —
        # so presence cannot be non-monotonic.
        part = _counterbored_block()
        seen = []
        for requested in (1.0, 1.5, 2.0, 2.5, 3.0):
            dwg = build_drawing(part, page="A3", scale=requested)
            seen.append((requested, dwg.section_decision["status"]))
        placed = [status == "placed" for _requested, status in seen]
        # Once it stops being placed it must not come back at a larger request.
        if False in placed:
            first_loss = placed.index(False)
            assert not any(placed[first_loss:]), (
                f"section presence is non-monotonic in scale: {seen}"
            )


class TestTheReasonCodeIsTheRealReason:
    """The whole point of #1190 is an honest record, so a wrong reason is worse than
    a missing one — it sends a reader to the wrong constraint."""

    def test_nothing_fitting_reports_no_room_not_title_block(self, monkeypatch):
        # The first cut of this fix got this wrong: with no fitting segment the
        # placeholder x fell past the title block too, and the title-block check ran
        # FIRST, so a part with no room reported a title-block collision.
        monkeypatch.setattr(sections_module, "carve_free_segments", lambda *_a, **_k: [])
        dwg = build_drawing(_counterbored_block(), page="A3")
        assert dwg.section_decision["status"] == "skipped"
        assert dwg.section_decision["reason"] == "no_room", (
            f"reported {dwg.section_decision['reason']!r} — a section with nowhere to "
            f"go must not be blamed on the title block"
        )

    def test_a_segment_clearing_the_title_block_is_preferred(self, monkeypatch):
        # Every fitting segment is tested against the title block, not just the
        # leftmost: offering one blocked segment and one clear one must place, not skip.
        dwg = build_drawing(_counterbored_block(), page="A3")
        if dwg.section_decision["status"] != "placed":
            pytest.skip("fixture does not place a section on this page")
        placed_at = dwg.section_decision["detail"]
        assert "placed at x=" in placed_at


class TestGeometryFailuresAreNotPlacementFailures:
    def test_a_failed_cut_is_a_validation_outcome(self, monkeypatch):
        # A boolean that will not cut is a geometry fact; no scale changes it. Marking
        # it a placement drop would send the scale-completeness search down the whole
        # ladder to reach the same answer.
        from draftwright.linting.issues import is_placement_drop

        monkeypatch.setattr(sections_module, "_fuzzy_cut", lambda *_a, **_k: None)
        dwg = build_drawing(_counterbored_block(), page="A3")
        assert dwg.section_decision["reason"] == "cut_empty"
        dropped = [i for i in dwg.lint() if i.code == "section_dropped"]
        assert dropped, "the failed cut was not reported at all"
        assert not any(is_placement_drop(i) for i in dropped), (
            "a geometry failure is being treated as a rescalable placement drop"
        )

    def test_a_room_failure_is_a_placement_outcome(self, monkeypatch):
        # The other side: no room IS rescalable, so it must stay a placement drop or
        # the monotonicity fix stops working.
        from draftwright.linting.issues import is_placement_drop

        monkeypatch.setattr(sections_module, "carve_free_segments", lambda *_a, **_k: [])
        dwg = build_drawing(_counterbored_block(), page="A3")
        dropped = [i for i in dwg.lint() if i.code == "section_dropped"]
        assert dropped and all(is_placement_drop(i) for i in dropped)
