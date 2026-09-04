"""ADR 2 (was 0018 §5): the arrangement as the fourth dimension of one constrained choice.

The ADR treats `view sets x scales x sheets x arrangements` as a single choice, gated on four
hard conditions of which the first is *preserve every supported requirement or reject the
candidate*. This module covers the arrangement end of that: a second arrangement exists, the
choice is made once and carried, and a candidate that fits geometrically is still rejected if
compiling it loses a requirement.

`stacked-iso` puts the isometric in the title block's column instead of giving it a column of
its own, which wins back that column's width. The engine will take it only when it costs
nothing — three things measured during #1130 say why every part of that sentence is load
bearing, and each has a test below:

* it must not change the SCALE. Left to compete freely the alternative reaches 2:1 on the
  dense plate where `columns` reaches 1:1, and the enlarged views leave the location dims
  nowhere to go. Packing is not allowed to bid up legibility.
* it must not be re-derived per stage. `_layout_geometry` is one shared authority, but scale
  selection passes it ESTIMATED strip depths and placement MEASURED ones, so resolving there
  lets the stages disagree about the same sheet.
* fitting is not preserving. Even at the same scale a smaller sheet has less free area, and
  `centered_rebate` and `scattered_plate` lose dimensions on one. Only a real compile can
  tell, so the gate measures rather than predicts (ADR 2 (was 0014 Amdt 3)).
"""

import itertools
from types import SimpleNamespace

import pytest
from build123d import Axis, Box, Cylinder, Pos, chamfer

import draftwright.analysis as analysis_mod
import draftwright.builder as builder_mod
import draftwright.compose as compose_mod
from draftwright import build_drawing
from draftwright.compose import _layout_geometry, choose_scale
from draftwright.view_plan import ARRANGEMENTS, ScalePick, arrangement_of

A4 = (297.0, 210.0, 120.0)
A3 = (420.0, 297.0, 150.0)
ONLY_PREFERRED = (ARRANGEMENTS[0],)


def _chamfered():
    """The `chamfered` golden's part — the corpus's smallest arrangement-sensitive case."""
    plate = Box(90, 60, 20)
    edge = plate.edges().filter_by(Axis.Z).sort_by(lambda e: e.center().X + e.center().Y)[-1]
    return chamfer(edge, 12)


def _dense_plate():
    """`test_make_drawing`'s crowded plate: 24 Z-holes in 5 diameter groups."""
    part = Box(70, 50, 12)
    for i, (gx, gy) in enumerate(itertools.product([-25, -15, -5, 5, 15, 25], [-15, -5, 5, 15])):
        part -= Pos(gx, gy, 0) * Cylinder(1.0 + (i % 5) * 0.4, 20)
    return part


def _centered_rebate():
    """The `centered_rebate` golden — a part the gate rejects the alternative for.

    A central channel giving two shoulders, both positions dimensioned. It fits the smaller
    sheet the alternative offers and loses a step position on it.
    """
    return Box(80, 60, 30) - Pos(0, 0, 7.5) * Box(80, 20, 15)


def _geom(arrangement, page=A4, size=(90.0, 60.0, 20.0), n_steps=0):
    page_w, page_h, tb_w = page
    return _layout_geometry(
        *size, 1.0, page_w, page_h, tb_w, None, n_steps, arrangement=arrangement
    )


def _lint_codes(drawing):
    return {issue.code for issue in drawing.lint()}


class TestTheAlternativeArrangementIsRealGeometry:
    def test_columns_does_not_fit_a4_and_stacked_iso_does(self):
        # The precondition the module rests on: a case where the two disagree. Without it
        # everything below would pass vacuously.
        assert _geom("columns").auto_fits is False
        assert _geom("stacked-iso").auto_fits is True

    def test_the_saving_is_exactly_the_reclaimed_iso_column(self):
        columns, stacked = _geom("columns"), _geom("stacked-iso")
        assert stacked.iso_natural > 0.0
        assert columns.auto_row_w - stacked.auto_row_w == pytest.approx(columns.iso_natural)

    def test_the_iso_still_gets_a_real_gap_to_live_in(self):
        # Reclaiming the width would be worthless if the iso then had nowhere to go: the
        # largest-empty-rect search must return a genuine rectangle, not the whole-drawable
        # fallback that overlaps the views.
        assert _geom("stacked-iso").iso_fits is True

    def test_an_unknown_arrangement_is_refused_rather_than_silently_composed(self):
        with pytest.raises(ValueError, match="unknown arrangement"):
            _geom("diagonal")


class TestTheDecisionIsMadeOnceAndCarried:
    """One choice, one answer — every stage composes under the arrangement that was proved."""

    def test_the_pick_is_still_a_four_tuple(self):
        # The whole reason the arrangement can ride along: callers unpack four values, and
        # every one of them keeps working.
        pick = ScalePick(1.0, 420.0, 297.0, 150.0, "stacked-iso")
        scale, page_w, page_h, tb_w = pick
        assert (scale, page_w, page_h, tb_w) == (1.0, 420.0, 297.0, 150.0)
        assert pick == (1.0, 420.0, 297.0, 150.0)
        assert pick.arrangement == "stacked-iso"

    def test_a_plain_tuple_means_the_long_standing_arrangement(self):
        # Hand-built picks and `_repack_candidates`' own alternatives are bare tuples; they
        # mean "as the engine always composed", not "unknown".
        assert arrangement_of((1.0, 420.0, 297.0, 150.0)) == ARRANGEMENTS[0]

    def test_choose_scale_reports_the_arrangement_it_proved(self):
        assert arrangement_of(choose_scale(90.0, 60.0, 20.0)) == "stacked-iso"
        assert (
            arrangement_of(choose_scale(90.0, 60.0, 20.0, arrangements=ONLY_PREFERRED))
            == (ARRANGEMENTS[0])
        )

    def test_placement_composes_under_the_carried_arrangement(self):
        # The sheet is the evidence: A4 is only reachable under `stacked-iso`, so a placement
        # that had re-derived `columns` could not have produced it.
        drawing = build_drawing(_chamfered())
        assert drawing.arrangement_decision["chosen"] == "stacked-iso"
        assert (drawing.page_w, drawing.page_h) == A4[:2]
        assert choose_scale(90.0, 60.0, 20.0, arrangements=ONLY_PREFERRED)[1:3] == A3[:2]

    def test_re_deriving_per_stage_instead_loses_requirements_on_an_unchanged_sheet(
        self, monkeypatch
    ):
        # Why the decision is carried rather than recomputed. `_layout_geometry` is a single
        # shared authority, but sharing a function is not sharing an INPUT: selection resolves
        # against estimated strip depths and placement against measured ones. Resolving at
        # each call site costs the dense plate location requirements on a sheet whose size
        # does not change at all — so this is not a smaller-sheet effect. The physical
        # The semantic critique and compiler outcome both report this real loss.  The two
        # codes are intentionally retained: one is physical coverage, the other is the exact
        # failed compiler requirement.
        baseline = build_drawing(_dense_plate())

        original = compose_mod._layout_geometry

        def resolving(*args, **kwargs):
            kwargs["arrangement"] = "auto"
            return original(*args, **kwargs)

        for module in (compose_mod, analysis_mod, builder_mod):
            monkeypatch.setattr(module, "_layout_geometry", resolving)

        drawing = build_drawing(_dense_plate())
        assert (drawing.page_w, drawing.page_h) == (baseline.page_w, baseline.page_h)
        assert _lint_codes(drawing) - _lint_codes(baseline) == {
            "feature_not_located",
            "location_ref_dropped",
        }


class TestTheRepackLoopComposesUnderTheCarriedArrangement:
    """The one seam that no real part reaches — pinned deliberately rather than left bare.

    `_repack_to_fixed_point` re-lays-out the sheet after measuring real annotation
    footprints. It must compose under the arrangement PLACEMENT used; the parameter defaults
    to `columns`, so omitting it would silently recompose the sheet the other way — exactly
    the stage disagreement this decision is carried to prevent, resurfacing one stage later.

    No part reaches it. Measured, not assumed: across the whole golden corpus plus parts
    built to provoke it, `_repack_to_fixed_point` is ENTERED under `stacked-iso` and always
    returns `None` — nothing re-assembles, because a well-estimated part measures as it was
    predicted and skips pass 2. And a part annotated densely enough to need re-assembly is
    also dense enough to lose a requirement on the smaller sheet the alternative offers, so
    the requirement gate rejects it first. The two conditions are anti-correlated BY the
    gate, which is why hunting for a natural fixture does not terminate.

    So the trigger is forced, in the same idiom the existing repack tests already use
    (`test_repack_to_fixed_point_*` all drive `_needs_repack` directly). This is a narrower
    claim than an end-to-end one and is worth being explicit about: it pins the wiring, not
    a drawing outcome.
    """

    @pytest.mark.parametrize(
        ("part", "expected"),
        [
            (_chamfered, "stacked-iso"),
            # BOTH directions, so the assertion cannot be satisfied by a constant: a repack
            # hardcoded to either arrangement fails one case or the other.
            (lambda: Box(80, 60, 25), "columns"),
        ],
        ids=["alternative", "preferred"],
    )
    def test_the_repack_geometry_is_asked_for_the_arrangement_placement_used(
        self, monkeypatch, part, expected
    ):
        # Precondition: this part really is composed under the arrangement asserted below,
        # so the expected value cannot appear merely as somebody's default.
        built = part()
        assert build_drawing(built).arrangement_decision["chosen"] == expected

        # True once, then False, so the fixed-point loop re-assembles exactly one round and
        # terminates instead of warning at the iteration limit.
        rounds = iter([True])
        monkeypatch.setattr(builder_mod, "_needs_repack", lambda dwg, a: next(rounds, False))

        seen = []
        original = builder_mod._layout_geometry

        def spy(*args, **kwargs):
            seen.append(kwargs.get("arrangement"))
            return original(*args, **kwargs)

        # `builder` holds its own reference and uses it at exactly one site — the repack's
        # `_geom` — so this observes the repack alone, not selection or placement.
        monkeypatch.setattr(builder_mod, "_layout_geometry", spy)
        build_drawing(built)

        assert seen, "the repack never composed — the forced trigger did not fire"
        assert set(seen) == {expected}, (
            f"repack composed under {set(seen)}, not the arrangement placement used"
        )


class TestPackingMayNotBidUpLegibility:
    """The arrangement compacts a chosen scale; it never chooses one."""

    def test_the_alternative_wins_a_sheet_at_the_same_scale(self):
        preferred = choose_scale(90.0, 60.0, 20.0, arrangements=ONLY_PREFERRED)
        chosen = choose_scale(90.0, 60.0, 20.0)
        assert preferred[1:3] == A3[:2] and chosen[1:3] == A4[:2]
        assert chosen[0] == preferred[0], "the arrangement must not change the scale"

    def test_it_cannot_reach_a_scale_the_preferred_arrangement_could_not(self):
        # The dense plate is the case: unconstrained, `stacked-iso` is feasible at 2:1 where
        # `columns` reaches only 1:1, and the drawing at twice the size drops its location
        # dims. Scale selection must therefore see only the preferred arrangement.
        assert _geom("columns", page=A3, size=(70.0, 50.0, 12.0)).auto_fits is True
        dense_scale = choose_scale(70.0, 50.0, 12.0)[0]
        assert dense_scale == choose_scale(70.0, 50.0, 12.0, arrangements=ONLY_PREFERRED)[0]

    def test_a_corridor_that_forces_a_larger_sheet_still_does(self):
        flat = choose_scale(5.0, 90.0, 100.0, n_steps=0, arrangements=ONLY_PREFERRED)
        deep = choose_scale(5.0, 90.0, 100.0, n_steps=3, arrangements=ONLY_PREFERRED)
        assert deep[1] > flat[1]
        assert deep[0] == choose_scale(5.0, 90.0, 100.0, n_steps=3)[0]


class TestFittingIsNotPreserving:
    """ADR 2 (was 0018 §5)'s first hard gate, measured on the finished drawing."""

    def test_an_alternative_that_costs_nothing_is_kept_in_one_compile(self):
        drawing = build_drawing(_chamfered())
        decision = drawing.arrangement_decision
        assert decision["chosen"] == "stacked-iso"
        assert [a["status"] for a in decision["attempts"]] == ["chosen"]
        assert _lint_codes(drawing) == set()

    def test_an_alternative_that_drops_a_requirement_is_rejected(self):
        # The precondition first: the alternative really is geometrically feasible here, so
        # the gate is what rejects it and not the fit.
        assert arrangement_of(choose_scale(80.0, 60.0, 30.0, n_steps=2)) == "stacked-iso"

        drawing = build_drawing(_centered_rebate())
        decision = drawing.arrangement_decision
        assert decision["chosen"] == ARRANGEMENTS[0]
        statuses = [(a["arrangement"], a["status"]) for a in decision["attempts"]]
        assert statuses == [("stacked-iso", "rejected"), ("columns", "chosen")]
        rejected = next(a for a in decision["attempts"] if a["status"] == "rejected")
        assert rejected["blockers"], "a rejection must say what it lost"
        assert not [code for code in _lint_codes(drawing) if code.endswith("_dropped")]

    def test_the_rejection_is_reported_rather_than_silent(self):
        # ADR 2 (was 0018 §6): infeasibility is a first-class result. A caller must not have to infer
        # from a log line that an alternative was tried, or what it cost.
        decision = build_drawing(_centered_rebate()).arrangement_decision
        assert set(decision) == {"chosen", "attempts"}
        for attempt in decision["attempts"]:
            assert set(attempt) == {"arrangement", "status", "blockers"}
            assert attempt["arrangement"] in ARRANGEMENTS

    def test_a_drawing_composed_as_always_records_one_attempt(self):
        decision = build_drawing(Box(80, 60, 25)).arrangement_decision
        assert decision["chosen"] == ARRANGEMENTS[0]
        assert [a["status"] for a in decision["attempts"]] == ["chosen"]


class TestTheGateFailsClosed:
    """Nothing measured means nothing proved."""

    def test_a_build_with_no_automatic_dimensioning_keeps_the_preferred_arrangement(self):
        # The gate establishes feasibility by compiling requirements and reading what failed
        # to place. `auto_dims=False` compiles none, so an alternative would be accepted on an
        # empty ledger — and annotations added afterwards through the deferred/`Sheet` seams
        # would be the ones to lose. Measured: a mixed deferred batch loses its shoulder dim.
        drawing = build_drawing(_chamfered(), auto_dims=False)
        assert drawing.arrangement_decision["chosen"] == ARRANGEMENTS[0]
        # And the sheet proves it reached layout: with dimensioning on, this part is on A4.
        assert (drawing.page_w, drawing.page_h) == A3[:2]


# --- the gate's comparison, driven directly ---------------------------------------------


def _blocker(code, parameter):
    """One required placement failure, shaped as `_scale_blockers` emits it."""
    return {
        "severity": "error",
        "code": code,
        "message": f"{code} at some position that differs between layouts",
        "measurements": ({"feature": "envelope", "parameter": parameter},),
        "hole_requirements": (),
        "source_ids": (),
    }


class TestTheGateComparesWhatWasLostNotHowMuch:
    """Cardinality alone accepts a DIFFERENT loss, which is not "preserve every requirement"."""

    @staticmethod
    def _decide(alternative_blockers, preferred_blockers):
        """Drive the real gate, so reverting it to a count comparison fails here.

        An earlier version of these tests recomputed the multiset difference itself and
        asserted on that — which passes whatever the production code does. Stubs stand in
        for the two builds because what is under test is the COMPARISON, not the compiler.
        """
        alternative = SimpleNamespace(name="alternative")
        preferred = SimpleNamespace(name="preferred")
        blockers = {id(alternative): alternative_blockers, id(preferred): preferred_blockers}
        winner = builder_mod._preserve_requirements_under_arrangement(
            alternative,
            "stacked-iso",
            lambda _scale, _arrangements: preferred,
            lambda built: blockers[id(built)],
        )
        return winner.name

    def test_an_alternative_that_loses_something_else_is_rejected(self):
        # The defect: preferred drops B, alternative drops A. Both have one blocker, so a
        # `len(preferred) < len(alt)` test keeps the alternative — even though the default
        # preserved A.
        assert (
            self._decide(
                [_blocker("location_ref_dropped", "loc_a")],
                [_blocker("location_ref_dropped", "loc_b")],
            )
            == "preferred"
        )

    def test_an_alternative_losing_the_same_thing_is_not_penalised(self):
        # The converse, so the rule is not simply "always reject": a blocker the default
        # produces too is not the alternative's fault, and it keeps its smaller sheet.
        same = [_blocker("location_ref_dropped", "loc_a")]
        assert self._decide(list(same), list(same)) == "alternative"

    def test_an_alternative_that_loses_more_of_the_same_is_rejected(self):
        # A multiset, not a set: losing the same requirement twice where the default lost it
        # once is a new loss.
        one = [_blocker("location_ref_dropped", "loc_a")]
        assert self._decide(one * 2, list(one)) == "preferred"

    def test_the_alternative_is_not_required_to_beat_the_default_on_volume(self):
        # One-sided by design. The default is the baseline every drawing had before this
        # choice existed, so an alternative earns its place by costing nothing NEW — not by
        # costing less. Here it introduces nothing and preserves strictly more.
        shared = _blocker("location_ref_dropped", "loc_a")
        assert (
            self._decide([shared], [shared, _blocker("hole_pattern_dim_dropped", "pat_b")])
            == "alternative"
        )

    def test_identity_ignores_the_message(self):
        # Messages carry positions and sheet sizes, so two layouts of the SAME defect read
        # differently. Keying on them would make every blocker unique and the comparison
        # vacuous — it would reject every alternative, always.
        a = _blocker("location_ref_dropped", "loc_a")
        b = dict(a, message="the same defect described from another sheet")
        assert builder_mod._blocker_identity(a) == builder_mod._blocker_identity(b)

    def test_identity_separates_different_requirements(self):
        # The precondition for the whole comparison meaning anything.
        assert builder_mod._blocker_identity(
            _blocker("location_ref_dropped", "loc_a")
        ) != builder_mod._blocker_identity(_blocker("location_ref_dropped", "loc_b"))
