"""S1 of #1202 — the `drawing_consumer` score must observe the drawing, not a declaration.

#1169 closed on the principle that a completeness score derived only from what the system
reports **can validate itself**: missed geometry never enters the denominator. The
evaluation module was built on that principle and then broke it at its own last layer.

`_default_observers` set every boundary — including `drawing_consumer` — from
`consumer_capability_declaration()`, a static per-family claim that a module exists:

    drawing_consumer -> {'state': 'supported',
                         'implementation': 'draftwright.annotations.holes._annotate_holes'}

So every hole in every fixture reported `drawing_consumer: supported`, whether or not any
hole was dimensioned. A drawing that recognised a hole and then dropped its callout scored
identically to one that drew it — which is #1176's defect, inside the module meant to
detect it.

`drawing_consumer` is now derived from a real build: did this hole's size reach the sheet,
by either sanctioned route — an `hc_*` callout, or the hole TABLE the engine substitutes
above ~16 scattered holes. The other three boundaries remain declared and are tracked
separately; they are the same shape one layer along, and pretending otherwise here would be
its own overclaim.

`unknown` means no IR feature accounts for the hole. It is an honest label, not an
exemption: `evaluate_case` credits a unit only when the state is `supported`, so downstream
an `unknown` scores as a MISS.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.evaluation.step_analysis import (
    _drawing_consumer_outcomes,
    evaluate_step_corpus,
    load_corpus,
)

_CORPUS = "tests/fixtures/evaluation/corpus-v1.json"

# The engine's own dense fixture, imported once. An earlier version re-ran
# `sys.path.insert(0, "tests")` on every call — unbounded duplication, and dependent on the
# working directory.
_TESTS = str(Path(__file__).parent)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
from test_issue_1143_hole_completeness import _dense_scattered_plate  # noqa: E402


def _part():
    """Two through-holes of different diameter — the corpus's `topology-a` shape, built
    in code so the unit tests do not depend on a fixture path."""
    return Box(60, 30, 12) - Pos(-15, 0, 0) * Cylinder(3, 40) - Pos(15, 0, 0) * Cylinder(5, 40)


def _recognised(part):
    from b123d_recognisers import build_recognition_result

    return build_recognition_result(part).holes


def _size_callouts(drawing):
    """Annotation names carrying a hole's SIZE, read off the provenance seam.

    Not a name prefix. `hc_*` matching is what made a bore printed as a plain `Leader`
    score `unsupported` on a turned part, and it went with the duplicate correspondence
    in #1217.
    """
    # `sorted`, because `registry.names()` is a set: callers index [0], so which annotation
    # a test corrupts would otherwise vary per process (ADR 0006; `evidence.py` took the
    # same fix one PR ago).
    return [
        name
        for name in sorted(drawing.registry.names())
        for measurement in drawing.registry.measurement_of(name)
        if str(getattr(measurement, "parameter", "")) == "bore.diameter"
    ]


class TestTheOutcomeIsReadOffTheDrawing:
    """Driven by REMOVING a real annotation, not by a hand-built drawing stand-in.

    The stubs these tests used (`_StubDrawing`, `_StrippedDrawing`) emptied
    `annotations_of`, which the consumer no longer reads — it goes through the registry's
    measurement provenance (ADR 0010). A stub of a seam the code has stopped using would
    have kept passing while testing nothing, which is this file's own founding lesson.
    """

    def test_a_hole_the_drawing_calls_out_is_supported(self):
        part = _part()
        drawing = build_drawing(part)
        holes = _recognised(part)
        assert holes, "fixture produced no recognised holes"
        assert _size_callouts(drawing), "no annotation claims a bore diameter"
        assert set(_drawing_consumer_outcomes(holes, drawing)) == {"supported"}

    def test_a_hole_whose_callout_never_reached_the_sheet_is_unsupported(self):
        # THE case the declared state could not express. Removing the callout is the real
        # mechanism a dropped placement produces, so nothing here depends on a stand-in.
        part = _part()
        drawing = build_drawing(part)
        holes = _recognised(part)
        assert set(_drawing_consumer_outcomes(holes, drawing)) == {"supported"}
        for name in _size_callouts(drawing):
            drawing.remove(name)
        assert set(_drawing_consumer_outcomes(holes, drawing)) == {"unsupported"}

    def test_furniture_without_a_callout_does_not_count_as_consumed(self):
        # A centre mark and location dims are still placed for a hole whose SIZE callout
        # was dropped. Counting any annotation would score that as consumed.
        part = _part()
        drawing = build_drawing(part)
        holes = _recognised(part)
        for name in _size_callouts(drawing):
            drawing.remove(name)
        remaining = set(drawing.registry.names())
        assert any(n.startswith(("m_cm", "m_loc")) for n in remaining), (
            f"no furniture survived the removal, so this proves nothing: {sorted(remaining)}"
        )
        assert set(_drawing_consumer_outcomes(holes, drawing)) == {"unsupported"}

    @pytest.mark.parametrize(
        ("fixture", "expected"),
        [
            pytest.param("tests/fixtures/nist_ctc_04_asme1_ap203.stp", 8, id="ctc04"),
            # CTC-03 carries a 45-degree bore and is the only fixture whose raw record axis
            # and spec-rounded axis LETTER differ — the components tie after rounding and
            # `max` takes the first. CTC-04 has non-principal-axis holes too (eight of its 54
            # sit on `(0, -0.3746, 0.9272)`, which is why they are `unverifiable`), but its
            # raw and rounded letters agree, so it cannot expose a lettering mismatch.
            #
            # SLOW-tier: this build timed out at 300 s on ubuntu 3.10 while taking ~40 s on
            # macOS. CTC fixture builds are slow-tier by policy in this repo
            # (`test_e2e_standards.py`), and putting one in the fast tier was the mistake, not
            # the timeout. `TestTheCanonicalSpaceIsTheLedgersOwn` below pins the same property
            # in milliseconds, so the fast tier keeps a guard.
            pytest.param(
                "tests/fixtures/nist_ctc_03_asme1_ap203.stp",
                5,
                id="ctc03",
                marks=pytest.mark.slow,
            ),
        ],
    )
    def test_a_hole_the_ledger_cannot_join_is_unknown(self, fixture, expected):
        # ADR 0017 states plainly that recognition-record -> IR-feature correspondence is
        # NOT yet provided. Where it fails the ledger says `unverifiable` and this says
        # `unknown` — which still scores as a MISS, so it is an honest label, not an
        # exemption. Pinned on real geometry: CTC-04 has eight such holes.
        from draftwright.linting.hole_coverage import (
            canonical_hole_sites,
            hole_requirement_outcomes,
        )
        from draftwright.model.compiled import compile_dimensions

        drawing = build_drawing(fixture)
        holes = list(drawing.recognition().holes)
        outcomes = _drawing_consumer_outcomes(holes, drawing)
        assert outcomes.count("unknown") == expected, (
            f"the fixture no longer carries unjoinable holes: {set(outcomes)}"
        )
        # The COUNT alone is equally satisfied by a hole with no ledger entry at all — which
        # is what a canonical-space mismatch produces, and did on CTC-03 until #1223's review.
        # Assert the stated property: every `unknown` joins an `unverifiable` entry.
        ledger = hole_requirement_outcomes(
            drawing.recognition(),
            drawing.model().features,
            drawing.registry,
            compile_dimensions(drawing.model()).diagnostics,
        )
        unverifiable = {m for e in ledger if e.state == "unverifiable" for m in e.members}
        for hole, outcome in zip(holes, outcomes, strict=True):
            if outcome == "unknown":
                assert set(canonical_hole_sites(hole)) & unverifiable, (
                    f"hole at {tuple(hole.location)} reached `unknown` through a lookup "
                    "default, not through an `unverifiable` ledger entry"
                )


class TestTheCorrespondenceIsNotFooledByGeometry:
    """The properties that matter, asserted end to end.

    The matcher these tests used to drive — axis + diameter + position, first match wins,
    living in this module — was deleted in #1217: `hole_requirement_outcomes` already
    answered the same question, and two implementations of one question drift. The tests
    that poked its internals (`_hole_candidates`'s kind and axis filters, the position
    tolerance, the depth compare) went with it; those guarantees are now
    `linting/hole_coverage.py`'s and are covered by `test_issue_1143_hole_completeness.py`.

    What survives here is the OBSERVABLE property, which must hold whoever computes it.
    """

    def test_two_coaxial_holes_of_equal_diameter_do_not_alias(self):
        # Two opposed bores at the same (x, y) must resolve distinctly, or a dropped
        # callout on the second is invisible. Blind one and exactly one outcome moves.
        part = Box(60, 30, 20) - Pos(0, 0, 8) * Cylinder(3, 8) - Pos(0, 0, -5) * Cylinder(3, 14)
        drawing = build_drawing(part)
        holes = list(drawing.recognition().holes)
        if len({tuple(h.location) for h in holes}) < 2:
            pytest.skip("this build did not recognise two distinct coaxial holes")
        callouts = _size_callouts(drawing)
        assert len(callouts) >= 2, f"the two bores share one callout: {callouts}"
        before = _drawing_consumer_outcomes(holes, drawing)
        assert before.count("supported") == len(holes), before
        drawing.remove(callouts[0])
        after = _drawing_consumer_outcomes(holes, drawing)
        assert after.count("supported") == len(holes) - 1, (
            f"removing one bore's callout lost {before.count('supported') - after.count('supported')} "
            f"holes, not exactly one: {before} -> {after}"
        )

    def test_a_patterned_hole_is_accounted_for(self):
        # Patterned holes lower to a PatternFeature, not a HoleFeature. Matching on that
        # type alone made recognising a pattern — a capability — LOWER the score.
        part = Box(80, 80, 10)
        for x, y in ((-25, -25), (25, -25), (-25, 25), (25, 25)):
            part -= Pos(x, y, 0) * Cylinder(3, 40)
        drawing = build_drawing(part)
        holes = list(drawing.recognition().holes)
        assert len(holes) == 4, f"the fixture no longer makes four holes: {len(holes)}"
        outcomes = _drawing_consumer_outcomes(holes, drawing)
        assert set(outcomes) == {"supported"}, (
            f"a recognised pattern scored worse than four loose holes would: {outcomes}"
        )

    def test_a_grouped_callout_accounts_for_every_member(self):
        # One `4× ⌀6` callout covers four holes through a single feature. Crediting only
        # the representative would score three of the four as lost.
        part = Box(80, 80, 10)
        for x, y in ((-25, -25), (25, -25), (-25, 25), (25, 25)):
            part -= Pos(x, y, 0) * Cylinder(3, 40)
        drawing = build_drawing(part)
        holes = list(drawing.recognition().holes)
        outcomes = _drawing_consumer_outcomes(holes, drawing)
        assert len(outcomes) == len(holes) == 4
        assert outcomes.count("supported") == 4, (
            f"a grouped callout credited only some of its members: {outcomes}"
        )


class TestAHoleTableIsAlsoTheFactReachingTheSheet:
    """Above ~16 scattered holes the engine WITHDRAWS the individual `hc_*` callouts and
    places one table plus balloons. Reading only `hc_` scored every hole on such a sheet
    as lost — 16 of 16 on a drawing with no lint issues at all — which inverted the
    metric: a correct sheet scored worse than the same part with the table forced to fail.

    Driven through a REAL build. The first version of this test hand-built a
    `SimpleNamespace` carrying `covers_hole_representations_by_feature`, an attribute
    nothing in the engine ever populated — its `representation_features` argument appeared
    in the whole tree only at its own definition and its own use. The stub was the entire
    reason the fix looked correct, and adversarial review caught that the real path still
    scored 16/16 `unsupported`. The dead field itself was removed in #1217.
    """

    def _dense(self):
        return _dense_scattered_plate()

    def test_a_table_represented_hole_is_consumed_on_a_real_build(self):
        part = self._dense()
        drawing = build_drawing(part, page="A3")
        annotations = {name for name, _o in drawing.iter_annotations()}
        assert any(n.startswith("hole_table") for n in annotations), (
            "fixture no longer escalates to a hole table; this tests nothing"
        )
        assert not any(n.startswith("hc_") for n in annotations), (
            "the engine still placed individual callouts, so the table route is untested"
        )
        outcomes = _drawing_consumer_outcomes(drawing.recognition().holes, drawing)
        assert outcomes and set(outcomes) == {"supported"}, (
            f"a correct table-escalated sheet scored {sorted(set(outcomes))}"
        )

    def test_the_metric_is_not_inverted_on_dense_parts(self):
        # The sharpest statement of the defect: a correct sheet must not score WORSE than
        # the same part whose table failed to fit. Before the fix the correct sheet scored
        # 16/16 unsupported and the broken one only 12/16.
        part = self._dense()
        good = build_drawing(part, page="A3")
        good_outcomes = _drawing_consumer_outcomes(good.recognition().holes, good)
        good_lost = sum(1 for o in good_outcomes if o != "supported")

        from draftwright import drawing as drawing_module

        original = drawing_module.fit_box
        drawing_module.fit_box = lambda *a, **k: None
        try:
            broken = build_drawing(part, page="A3")
            broken_outcomes = _drawing_consumer_outcomes(broken.recognition().holes, broken)
        finally:
            drawing_module.fit_box = original
        broken_lost = sum(1 for o in broken_outcomes if o != "supported")

        assert broken_lost > 0, "the table did not actually fail to fit; nothing is compared"
        assert good_lost < broken_lost, (
            f"the correct sheet lost {good_lost} holes and the broken one {broken_lost} — "
            f"the metric rewards a worse drawing"
        )

    def test_a_dropped_table_is_not_credited(self):
        # The ledger is written only after the table is placed and the balloon gate passes,
        # and the annotation transaction rolls it back before that — so a `table_dropped`
        # sheet must credit nothing. Asserted on the OUTCOME now that `_table_represented`
        # is gone with the duplicate correspondence.
        from draftwright import drawing as drawing_module

        original = drawing_module.fit_box
        drawing_module.fit_box = lambda *a, **k: None
        try:
            broken = build_drawing(self._dense(), page="A3")
        finally:
            drawing_module.fit_box = original
        names = {n for n, _o in broken.iter_annotations()}
        assert not any(n.startswith("hole_table") for n in names), (
            "the table reached the sheet after all; nothing is under test"
        )
        outcomes = _drawing_consumer_outcomes(broken.recognition().holes, broken)
        assert outcomes.count("supported") < len(outcomes), (
            "a table that never reached the sheet still credited every hole"
        )

    def test_only_the_size_requirement_counts(self):
        # A table row documents location and through-ness as well. Crediting those would let
        # a hole with a located row but no diameter score as consumed. The filter now lives
        # in `hole_coverage`; this pins that the table really does carry more than size, so
        # the filter cannot be vacuous.
        from draftwright.evaluation.step_analysis import _SIZE_REQUIREMENT

        drawing = build_drawing(self._dense(), page="A3")
        parameters = {
            entry[1]
            for _n, a in drawing.iter_annotations()
            for entry in getattr(a, "covers_hole_representations_by_requirement", ())
        }
        assert parameters > {_SIZE_REQUIREMENT}, (
            f"the table documents only {parameters}; the filter cannot be shown to matter"
        )
        outcomes = _drawing_consumer_outcomes(drawing.recognition().holes, drawing)
        assert set(outcomes) == {"supported"}, outcomes


class TestTheCreditGoesToTheRightFeature:
    def test_removing_one_holes_callout_moves_exactly_one_outcome(self):
        # `_consumed` reduced to `bool(represented)` once passed all 21 tests: the credit was
        # only ever asserted where EVERY feature happened to be carried, so nothing pinned it
        # to the right one. Per-hole attribution is the property that matters, and it is what
        # `HoleRequirementOutcome.members` exists to make possible.
        part = Box(90, 40, 12)
        for x, r in ((-30, 2.0), (0, 3.0), (30, 4.0)):
            part -= Pos(x, 0, 0) * Cylinder(r, 40)
        drawing = build_drawing(part)
        holes = list(drawing.recognition().holes)
        assert len(holes) == 3, f"the fixture no longer makes three distinct holes: {len(holes)}"
        before = _drawing_consumer_outcomes(holes, drawing)
        assert before.count("supported") == 3, before
        drawing.remove(_size_callouts(drawing)[0])
        after = _drawing_consumer_outcomes(holes, drawing)
        assert after.count("supported") == 2, (
            f"removing one callout changed {3 - after.count('supported')} outcomes: {after}"
        )


class TestThePointerIsFollowedNotBelieved:
    """@pzfreo's decision on #1206: the ledger is a pointer to the claimed representation,
    NOT final proof. `placed` means the engine recorded an annotation as carrying the
    requirement; the consumer then has to check the annotation actually renders the value.

    Deleting that check left every test in this file passing — which is the shape of defect
    the whole epic exists to remove, so it is pinned here rather than assumed.
    """

    def test_a_placed_requirement_the_drawing_contradicts_is_not_supported(self):
        part = _part()
        drawing = build_drawing(part)
        holes = _recognised(part)
        assert set(_drawing_consumer_outcomes(holes, drawing)) == {"supported"}

        # The ledger still says `placed` — the annotation is on the sheet and claims the
        # measurement. What changes is what it DRAWS.
        name = _size_callouts(drawing)[0]
        drawing.registry.named(name).label = "⌀999 THRU"
        from draftwright.linting.evidence import verify_measurement_claims
        from draftwright.linting.hole_coverage import hole_requirement_outcomes
        from draftwright.model.compiled import compile_dimensions

        plan = compile_dimensions(drawing.model())
        ledger = hole_requirement_outcomes(
            drawing.recognition(), drawing.model().features, drawing.registry, plan.diagnostics
        )
        assert any(o.parameter_id == "bore.diameter" and o.state == "placed" for o in ledger), (
            "the ledger stopped saying `placed`, so the pointer is not what is under test"
        )
        assert any(
            c.state == "value_absent" for c in verify_measurement_claims(drawing.registry, plan)
        ), "the verifier did not refute the label, so nothing is being followed"

        outcomes = _drawing_consumer_outcomes(holes, drawing)
        assert outcomes.count("supported") < len(holes), (
            f"a hole whose callout draws ⌀999 was credited as carried: {outcomes}"
        )

    def test_a_contradicted_LOCATION_does_not_unsupport_the_SIZE(self):
        # `drawing_consumer` asks one question: did this hole's SIZE reach the sheet. A
        # location dimension drawing the wrong number is a real defect and a different
        # requirement — crediting it here would make the metric answer a question it does
        # not ask, and would drag the score down for a fault another check owns.
        part = _part()
        drawing = build_drawing(part)
        holes = _recognised(part)
        located = [
            name
            for name in sorted(drawing.registry.names())
            for measurement in drawing.registry.measurement_of(name)
            if "location" in str(getattr(measurement, "parameter", ""))
        ]
        assert located, "the fixture draws no location dimension, so this proves nothing"
        drawing.registry.named(located[0]).label = "999"
        from draftwright.linting.evidence import verify_measurement_claims
        from draftwright.model.compiled import compile_dimensions

        assert any(
            c.state == "value_absent"
            for c in verify_measurement_claims(
                drawing.registry, compile_dimensions(drawing.model())
            )
        ), "the verifier did not refute the location label"
        assert set(_drawing_consumer_outcomes(holes, drawing)) == {"supported"}, (
            "a wrong LOCATION label made the hole's SIZE count as not carried"
        )

    def test_a_contradicted_callout_unsupports_only_its_own_hole(self):
        # The scoping had no test: making ANY refuted size claim discredit EVERY hole passed
        # the entire fast tier, because the two-hole fixture above asserts only
        # `count("supported") < len(holes)` — true at 0 as well as at 1 (#1223 review).
        part = Box(90, 40, 12)
        for x, r in ((-30, 2.0), (0, 3.0), (30, 4.0)):
            part -= Pos(x, 0, 0) * Cylinder(r, 40)
        drawing = build_drawing(part)
        holes = list(drawing.recognition().holes)
        assert len(holes) == 3, f"the fixture no longer makes three distinct holes: {len(holes)}"
        assert _drawing_consumer_outcomes(holes, drawing).count("supported") == 3
        drawing.registry.named(_size_callouts(drawing)[0]).label = "⌀999 THRU"
        after = _drawing_consumer_outcomes(holes, drawing)
        assert after.count("supported") == 2, (
            f"one contradicted callout unsupported {3 - after.count('supported')} holes: {after}"
        )

    def test_an_annotation_that_draws_nothing_is_not_credited(self):
        # `supported` is meant to mean the annotation carrying the size RENDERS it. Rejecting
        # only `value_absent` credited an `unreadable` annotation — one drawing no text at
        # all — as carrying the size, which is the PR body's own sentence being false of the
        # code beneath it.
        part = _part()
        drawing = build_drawing(part)
        holes = _recognised(part)
        assert set(_drawing_consumer_outcomes(holes, drawing)) == {"supported"}
        drawing.registry.named(_size_callouts(drawing)[0]).label = ""
        from draftwright.linting.evidence import verify_measurement_claims
        from draftwright.model.compiled import compile_dimensions

        states = {
            c.state
            for c in verify_measurement_claims(
                drawing.registry, compile_dimensions(drawing.model())
            )
        }
        assert "unreadable" in states, f"the fixture did not produce an unreadable claim: {states}"
        assert _drawing_consumer_outcomes(holes, drawing).count("supported") < len(holes), (
            "an annotation drawing no text at all was credited as carrying the size"
        )


class TestTheCanonicalSpaceIsTheLedgersOwn:
    """`canonical_hole_sites` must key in the same space `HoleRequirementOutcome.members` is
    published in, and the producer letters the axis from `HoleSpec`'s 6-dp-rounded vector.

    Deriving it from the raw record vector instead put the consumer in a different space
    whenever rounding changed which component wins: CTC-03's 45-degree bore rounds to an exact
    tie, `max` takes the first, and five holes then had no ledger member at all. Direct, because
    the end-to-end case needs a CTC build that belongs in the slow tier — and because whether a
    given solid produces the tie is a floating-point coin flip, which is no basis for a guard.
    """

    def test_the_grouped_branch_letters_from_the_spec_too(self):
        # `_members` has two branches. The consumer's join goes through the single-record one,
        # so reverting the GROUPED branch to the raw axis broke nothing any test noticed —
        # while silently undoing the coordinates `hole_requirement_unverifiable` reports
        # (#1223 review). Both branches key the same space or neither does.
        from types import SimpleNamespace

        from draftwright.linting.hole_coverage import canonical_hole_sites

        def _hole(location):
            return SimpleNamespace(
                location=location,
                axis=(0.707106781186547, 0.0, -0.707106781186548),
                diameter=8.0,
                depth=None,
                bottom="through",
                cbore=None,
                spotface=None,
                csink=None,
            )

        group = SimpleNamespace(holes=[_hole((30.0, 244.0, 141.0)), _hole((43.0, 225.0, 153.0))])
        sites = canonical_hole_sites(group)
        assert all(site[0] == 0.0 for site in sites), (
            f"the grouped branch zeroed along the raw-vector axis, not the spec axis: {sites}"
        )

    def test_the_axis_letter_comes_from_the_rounded_spec(self):
        from types import SimpleNamespace

        from b123d_recognisers import HoleSpec

        from draftwright.linting.hole_coverage import canonical_hole_sites

        # Raw components differ in the last bit — `max` picks z. Rounded to 6 dp they tie, and
        # `max` picks x. A through hole's site is zeroed along the SPEC axis, so the two
        # readings put the same bore in different places.
        record = SimpleNamespace(
            location=(30.0, 244.0, 141.0),
            axis=(0.707106781186547, 0.0, -0.707106781186548),
            diameter=8.0,
            depth=None,
            bottom="through",
            cbore=None,
            spotface=None,
            csink=None,
        )
        spec_axis = HoleSpec.from_hole(record).axis
        assert spec_axis == (0.707107, 0.0, -0.707107), spec_axis
        site = canonical_hole_sites(record)[0]
        assert site[0] == 0.0, (
            f"the site was zeroed along the raw-vector axis, not the spec axis: {site}"
        )
        assert site[2] == 141.0, f"the spec axis component was zeroed instead: {site}"


class TestTheCorpusScoreMovesWithTheDrawing:
    def test_the_corpus_downstream_layer_is_perfect_when_every_hole_is_drawn(self):
        # Not a tautology now: on these fixtures every hole really is called out, so 1.0
        # is EARNED by observation where it used to be asserted by declaration.
        evaluation = evaluate_step_corpus(load_corpus(_CORPUS))
        assert evaluation.downstream_usefulness.score == 1.0
        assert evaluation.downstream_usefulness.total > 0, "nothing was scored at all"

    def test_the_layer_drops_when_the_sheet_stops_drawing_holes(self, monkeypatch):
        # The load-bearing claim: this score is a function of the DRAWING. Empty the
        # provenance answer and the layer must fall. Under the old declared constant it
        # stayed at 1.0 no matter what the sheet did.
        #
        # The lever is the REGISTRY's measurement provenance — the seam the consumer reads
        # since #1217. Emptying `annotations_of`, which this test used to do, would now
        # leave the score at 1.0 and prove the opposite of what it claims.
        from draftwright.registry import AnnotationRegistry

        monkeypatch.setattr(AnnotationRegistry, "measurement_of", lambda _s, _n: ())
        evaluation = evaluate_step_corpus(load_corpus(_CORPUS))
        assert evaluation.downstream_usefulness.score is not None
        assert evaluation.downstream_usefulness.score < 1.0, (
            "the downstream layer stayed perfect while the sheet drew no callouts — it "
            "is not reading the drawing"
        )


class TestTheRemainingBoundariesAreStillDeclared:
    """Stated, not hidden. Three boundaries are still scored from a capability
    declaration, and a reader must not infer from this PR that all four are observed."""

    @pytest.mark.parametrize("boundary", ["ir_adapter", "dsl_declaration", "generated_code"])
    def test_a_declared_boundary_does_not_move_with_the_drawing(self, boundary, monkeypatch):
        from draftwright.evaluation.step_analysis import _default_observers
        from draftwright.registry import AnnotationRegistry

        # Same lever as the corpus test above: the registry's measurement provenance, not
        # `annotations_of`, is what the drawing_consumer boundary reads since #1217.
        monkeypatch.setattr(AnnotationRegistry, "measurement_of", lambda _s, _n: ())
        observed = _default_observers()["holes"](_part())
        assert observed, "no facts observed"
        assert {fact.downstream[boundary] for fact in observed} == {"supported"}
        assert {fact.downstream["drawing_consumer"] for fact in observed} == {"unsupported"}


class TestAnUnbuildableFixtureIsScoredNotCrashed:
    """The two fail-open paths. A corpus run must degrade to a scored non-answer rather
    than a traceback out of its middle — and the crash must still be *visible*, because a
    benchmark whose point is that a self-reported number cannot validate itself should not
    quietly equate "the compiler has no correspondence" with "the engine crashed"."""

    def _observe(self, part):
        from draftwright.evaluation.step_analysis import _default_observers

        return _default_observers()["holes"](part)

    def test_a_build_failure_scores_every_hole_unknown_and_says_so(self, monkeypatch, caplog):
        import draftwright.builder as builder_module

        def explode(*_a, **_k):
            raise RuntimeError("Standard_DomainError")

        monkeypatch.setattr(builder_module, "build_drawing", explode)
        with caplog.at_level("WARNING"):
            observed = self._observe(_part())
        assert observed, "recognition still works, so facts must still be observed"
        assert {f.downstream["drawing_consumer"] for f in observed} == {"unknown"}
        assert any("Standard_DomainError" in r.getMessage() for r in caplog.records), (
            "the crash was scored but never announced, so it is indistinguishable from a "
            "correspondence gap"
        )

    def test_a_fixture_that_neither_builds_nor_recognises_observes_nothing(self, monkeypatch):
        # Not an empty score with invented facts — no observation at all, which the oracle
        # scores as a detection miss against its independent denominator.
        import draftwright.builder as builder_module

        def explode(*_a, **_k):
            raise RuntimeError("unanalysable")

        monkeypatch.setattr(builder_module, "build_drawing", explode)
        assert self._observe(object()) == ()
