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
from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.evaluation.step_analysis import (
    _drawing_consumer_outcomes,
    _hole_candidates,
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


def _stub_hole(diameter, origin, members=()):
    """A minimal IR hole feature: what `_hole_candidates` reads, and nothing else."""
    return SimpleNamespace(
        kind="hole",
        frame=SimpleNamespace(origin=origin, axis="z"),
        diameter=diameter,
        members=members,
    )


class _StubDrawing:
    """A drawing stand-in with hand-placed features. *drawn* is the set of feature indices
    that carry a callout, so a test can say precisely which fact reached the sheet."""

    def __init__(self, features, drawn=frozenset()):
        self._features = tuple(features)
        self._drawn = set(drawn)

    def model(self):
        return SimpleNamespace(features=self._features)

    def iter_annotations(self):
        return ()

    def annotations_of(self, feature):
        index = next(i for i, f in enumerate(self._features) if f is feature)
        return {"hc_stub": object()} if index in self._drawn else {}


class _StrippedDrawing:
    """A real drawing with its provenance answers emptied — what a dropped callout looks
    like to a consumer. Delegates everything else, so the IR and the table ledger are the
    engine's own."""

    def __init__(self, drawing, annotations):
        self._drawing = drawing
        self._annotations = annotations

    def __getattr__(self, name):
        return getattr(self._drawing, name)

    def annotations_of(self, _feature):
        return self._annotations


class TestTheOutcomeIsReadOffTheDrawing:
    def test_a_hole_the_drawing_calls_out_is_supported(self):
        part = _part()
        drawing = build_drawing(part)
        assert _hole_candidates(drawing), "no IR candidate accounts for any hole"
        holes = _recognised(part)
        assert holes, "fixture produced no recognised holes"
        assert set(_drawing_consumer_outcomes(holes, drawing)) == {"supported"}

    def test_a_hole_whose_callout_never_reached_the_sheet_is_unsupported(self):
        # THE case the declared state could not express. Mutation: restore the declared
        # constant and this reports `supported` for a hole that was never drawn.
        part = _part()
        drawing = build_drawing(part)
        holes = _recognised(part)
        assert set(_drawing_consumer_outcomes(holes, drawing)) == {"supported"}
        stripped = _StrippedDrawing(drawing, {})
        assert set(_drawing_consumer_outcomes(holes, stripped)) == {"unsupported"}

    def test_furniture_without_a_callout_does_not_count_as_consumed(self):
        # A centre mark and location dims are still placed for a hole whose SIZE callout
        # was dropped — measured on a real drop: the feature keeps `m_cm0`, sometimes
        # `m_locy1`, and no `hc_`. Counting any annotation would score that as consumed.
        part = _part()
        drawing = build_drawing(part)
        holes = _recognised(part)
        furniture = _StrippedDrawing(drawing, {"m_cm0": object(), "m_locx0": object()})
        assert set(_drawing_consumer_outcomes(holes, furniture)) == {"unsupported"}

    def test_a_hole_with_no_corresponding_feature_is_unknown(self):
        # ADR 0017 states plainly that recognition-record -> IR-feature correspondence is
        # NOT yet provided. Where it fails, the label is `unknown`. It still scores as a
        # MISS downstream — `evaluate_case` credits only `supported` — so this is an
        # honest label, not an exemption.
        part = _part()
        holes = _recognised(part)
        no_features = SimpleNamespace(
            model=lambda: SimpleNamespace(features=()),
            iter_annotations=lambda: (),
            annotations_of=lambda _f: {},
        )
        assert set(_drawing_consumer_outcomes(holes, no_features)) == {"unknown"}


class TestTheCorrespondenceIsNotFooledByGeometry:
    """The matcher was originally axis + diameter + IN-PLANE position, first match wins.
    Adversarial review found three ways that answers wrongly."""

    def test_two_coaxial_holes_of_equal_diameter_do_not_alias(self):
        # Dropping the axis component made two opposed bores at the same (x, y) resolve to
        # the SAME feature, so a dropped callout on the second was invisible.
        #
        # An earlier version of this test never called `_drawing_consumer_outcomes` at all
        # — it counted candidate positions — so it passed with the function replaced by
        # `raise NotImplementedError`, and the in-plane mutation it claimed to guard
        # survived. It now blinds ONE of the two features and requires the outcome to move
        # with it, which is the property "these do not alias" actually means.
        part = Box(60, 30, 20) - Pos(0, 0, 8) * Cylinder(3, 8) - Pos(0, 0, -5) * Cylinder(3, 14)
        drawing = build_drawing(part)
        holes = list(drawing.recognition().holes)
        if len({tuple(h.location) for h in holes}) < 2:
            pytest.skip("this build did not recognise two distinct coaxial holes")
        candidates = _hole_candidates(drawing)
        assert len(candidates) >= 2, "the two bores collapsed to one candidate"

        def blinded(index):
            hidden = candidates[index].feature
            view = _StrippedDrawing(drawing, None)
            view.annotations_of = lambda f: {} if f == hidden else drawing.annotations_of(f)
            return _drawing_consumer_outcomes(holes, view)

        first, second = blinded(0), blinded(1)
        assert first.count("unsupported") == 1 and second.count("unsupported") == 1, (
            f"blinding one bore did not lose exactly one hole: {first} / {second}"
        )
        assert first != second, (
            f"blinding either bore gave the same answer {first} — they alias onto one "
            f"feature, so a dropped callout on the second is invisible"
        )

    def test_a_patterned_hole_is_accounted_for(self):
        # Patterned holes lower to a PatternFeature, NOT a HoleFeature. Matching on that
        # type alone made recognising a pattern — a capability — LOWER the score: four
        # identical holes scored `unknown` where the same four with distinct diameters
        # scored `supported`, both fully drawn.
        part = Box(80, 80, 10)
        for x, y in ((-20, -20), (20, -20), (-20, 20), (20, 20)):
            part -= Pos(x, y, 0) * Cylinder(2.5, 40)
        drawing = build_drawing(part)
        holes = _recognised(part)
        assert holes, "fixture recognised no holes"
        outcomes = _drawing_consumer_outcomes(holes, drawing)
        assert "unknown" not in outcomes, (
            f"a patterned hole was not accounted for by any IR feature: {outcomes}"
        )
        assert set(outcomes) == {"supported"}, outcomes

    def test_a_grouped_count_callout_accounts_for_every_member(self):
        # One HoleFeature with `members` covers several holes. The corpus has no two
        # holes of equal diameter, so this path was never exercised there.
        part = Box(60, 30, 12) - Pos(-15, 0, 0) * Cylinder(3, 40) - Pos(15, 0, 0) * Cylinder(3, 40)
        drawing = build_drawing(part)
        grouped = [c for c in _hole_candidates(drawing) if len(c.positions) > 1]
        assert grouped, "fixture no longer produces a grouped feature; the path is untested"
        outcomes = _drawing_consumer_outcomes(_recognised(part), drawing)
        assert set(outcomes) == {"supported"}, outcomes

    def test_one_ir_position_cannot_account_for_two_recognised_holes(self):
        # A matched position is CONSUMED. Without that, one feature answers for every hole
        # that lands on it, so a duplicate or spurious recognition record silently
        # inherits a real feature's "supported" instead of showing up as unaccounted.
        # (With full-3D matching this is the residual case: distinct positions no longer
        # alias, which is what the in-plane projection used to allow.)
        hole = SimpleNamespace(axis=(0.0, 0.0, -1.0), location=(0.0, 0.0, 5.0), diameter=6.0)
        drawing = _StubDrawing(features=(_stub_hole(6.0, (0.0, 0.0, 5.0)),), drawn={0})
        assert _drawing_consumer_outcomes([hole, hole], drawing) == ["supported", "unknown"], (
            "one IR position answered for two recognised holes"
        )

    def test_the_diameter_guard_refuses_a_positional_coincidence(self):
        # With full-3D matching a position is very nearly unique on its own, so the
        # axis+diameter test is a CROSS-CHECK, not the primary key — an earlier version of
        # this test used distinct positions and therefore passed with the guard deleted.
        # It bites exactly where two features share a position and differ in size, which
        # is what a counterbore-like arrangement looks like to the matcher.
        hole = SimpleNamespace(axis=(0.0, 0.0, -1.0), location=(0.0, 0.0, 5.0), diameter=6.0)
        drawing = _StubDrawing(
            features=(
                _stub_hole(diameter=12.0, origin=(0.0, 0.0, 5.0)),  # same place, wrong size
                _stub_hole(diameter=6.0, origin=(0.0, 0.0, 5.0)),  # the right one
            ),
            drawn={1},
        )
        assert _drawing_consumer_outcomes([hole], drawing) == ["supported"], (
            "the hole matched the wrong-diameter feature sharing its position"
        )

    def test_the_position_tolerance_admits_analytic_noise_but_not_a_real_offset(self):
        # The IR carries the recogniser's `hole.location` verbatim, so agreement is EXACT
        # on every fixture (worst residual measured at 0.0, including a side-drilled case
        # whose z is 6.66e-15). The tolerance is therefore never exercised by a build and
        # survived being set to 0.0 — an untested constant. Driven directly instead.
        hole = SimpleNamespace(axis=(0.0, 0.0, -1.0), location=(0.0, 0.0, 5.0), diameter=6.0)
        near = _StubDrawing(features=(_stub_hole(6.0, (0.0, 5e-7, 5.0)),), drawn={0})
        far = _StubDrawing(features=(_stub_hole(6.0, (0.0, 1e-3, 5.0)),), drawn={0})
        assert _drawing_consumer_outcomes([hole], near) == ["supported"]
        assert _drawing_consumer_outcomes([hole], far) == ["unknown"], (
            "a millimetre-scale offset was accepted as the same hole"
        )


class TestAHoleTableIsAlsoTheFactReachingTheSheet:
    """Above ~16 scattered holes the engine WITHDRAWS the individual `hc_*` callouts and
    places one table plus balloons. Reading only `hc_` scored every hole on such a sheet
    as lost — 16 of 16 on a drawing with no lint issues at all — which inverted the
    metric: a correct sheet scored worse than the same part with the table forced to fail.

    Driven through a REAL build. The first version of this test hand-built a
    `SimpleNamespace` carrying `covers_hole_representations_by_feature`, an attribute
    **nothing in the engine ever populates** — `representation_features=` appears in the
    whole tree only at its own definition and its own use. The stub was the entire reason
    the fix looked correct, and adversarial review caught that the real path still scored
    16/16 `unsupported`.
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
        # The ledger is written only after the table is placed and the balloon gate
        # passes, and the annotation transaction rolls it back before that — so a
        # `table_dropped` sheet must carry no entries to credit.
        from draftwright import drawing as drawing_module
        from draftwright.evaluation.step_analysis import _table_represented

        original = drawing_module.fit_box
        drawing_module.fit_box = lambda *a, **k: None
        try:
            broken = build_drawing(self._dense(), page="A3")
        finally:
            drawing_module.fit_box = original
        assert _table_represented(broken) == [], (
            "a table that never reached the sheet still credited its holes"
        )

    def test_a_row_without_the_size_requirement_is_not_credited(self):
        # On a real dense plate every feature gets every requirement, so filtering or not
        # gives the same answer there and the mutation survives. Driven directly. The
        # attribute name and tuple shape are verified against a real build by the test
        # below — this varies the PARAMETER, not the structure, so it is not the kind of
        # invented stub that made the original defect look fixed.
        from draftwright.evaluation.step_analysis import _table_represented

        located_only = SimpleNamespace(
            covers_hole_representations_by_requirement=(
                ("feature-A", "location.location", "hole_table", "escalation"),
            )
        )
        sized = SimpleNamespace(
            covers_hole_representations_by_requirement=(
                ("feature-B", "bore.diameter", "hole_table", "escalation"),
            )
        )
        drawing = SimpleNamespace(iter_annotations=lambda: (("t1", located_only), ("t2", sized)))
        assert _table_represented(drawing) == ["feature-B"], (
            "a hole whose table row carries only its LOCATION was credited as having its "
            "size on the sheet"
        )

    def test_only_the_size_requirement_counts(self):
        # A table row documents location and through-ness as well. Crediting those would
        # let a hole with a located row but no diameter score as consumed.
        from draftwright.evaluation.step_analysis import _SIZE_REQUIREMENT, _table_represented

        drawing = build_drawing(self._dense(), page="A3")
        parameters = {
            entry[1]
            for _n, a in drawing.iter_annotations()
            for entry in getattr(a, "covers_hole_representations_by_requirement", ())
        }
        assert parameters > {_SIZE_REQUIREMENT}, (
            f"the table documents only {parameters}; the filter cannot be shown to matter"
        )
        assert _table_represented(drawing), "nothing was credited at all"


class TestTheCreditGoesToTheRightFeature:
    def test_a_table_credits_only_the_features_it_carries(self):
        # `_consumed` reduced to `bool(represented)` passed all 21 tests: the table route
        # was only ever asserted where EVERY feature happened to be credited, so nothing
        # pinned the credit to the right one. That is round 2's failure shape exactly —
        # an assertion that holds for the wrong reason.
        #
        # Reachable in principle: a grouped pattern marker "has no defining table row and
        # therefore remains deliberately non-certifying" (annotations/orchestrator.py), so
        # a sheet can carry a table covering some features and a pattern covering others.
        from draftwright.evaluation.step_analysis import _consumed

        carried, not_carried = object(), object()
        drawing = SimpleNamespace(annotations_of=lambda _f: {"m_cm0": object()})
        assert _consumed(carried, drawing, [carried]) is True
        assert _consumed(not_carried, drawing, [carried]) is False, (
            "a feature the table does not carry was credited because some other feature was"
        )


class TestTheCandidateFilterIsLoadBearing:
    def test_a_hole_is_not_matched_to_a_feature_on_another_axis(self):
        # The axis half of the axis+diameter cross-check had no test, while the diameter
        # half did — on the stated reasoning that a redundant cross-check needs one.
        hole = SimpleNamespace(axis=(0.0, 0.0, -1.0), location=(0.0, 0.0, 5.0), diameter=6.0)
        sideways = _stub_hole(6.0, (0.0, 0.0, 5.0))
        sideways.frame = SimpleNamespace(origin=(0.0, 0.0, 5.0), axis="x")
        drawing = _StubDrawing(features=(sideways,), drawn={0})
        assert _drawing_consumer_outcomes([hole], drawing) == ["unknown"], (
            "a z-axis hole was matched to an x-axis feature sharing its position"
        )

    def test_only_hole_and_pattern_features_are_candidates(self):
        # Widening the `kind` filter to admit other feature kinds passed everything: no
        # test established that a non-hole feature cannot account for a hole.
        hole = SimpleNamespace(axis=(0.0, 0.0, -1.0), location=(0.0, 0.0, 5.0), diameter=6.0)
        boss = _stub_hole(6.0, (0.0, 0.0, 5.0))
        boss.kind = "boss"
        assert _hole_candidates(_StubDrawing(features=(boss,))) == [], (
            "a boss was admitted as a candidate for a hole"
        )
        assert _drawing_consumer_outcomes([hole], _StubDrawing(features=(boss,))) == ["unknown"]


class TestTheMatchIsFullyThreeDimensional:
    def test_a_hole_is_matched_to_the_candidate_at_its_actual_depth(self):
        # The in-plane defect is MASKED on real geometry by position consumption: two
        # coaxial bores still resolve distinctly because the first hole consumes the
        # first candidate's only position. It bites when a hole's true match is not the
        # first candidate sharing its (x, y) — then the in-plane compare assigns it to
        # the wrong feature, and the outcome is that feature's, not its own.
        hole = SimpleNamespace(axis=(0.0, 0.0, -1.0), location=(0.0, 0.0, 10.0), diameter=6.0)
        drawing = _StubDrawing(
            features=(
                _stub_hole(6.0, (0.0, 0.0, 0.0)),  # same (x, y), different depth, DRAWN
                _stub_hole(6.0, (0.0, 0.0, 10.0)),  # the real match, NOT drawn
            ),
            drawn={0},
        )
        assert _drawing_consumer_outcomes([hole], drawing) == ["unsupported"], (
            "the hole was matched to the candidate at the wrong depth and inherited its outcome"
        )


class TestTheCorpusScoreMovesWithTheDrawing:
    def test_the_corpus_downstream_layer_is_perfect_when_every_hole_is_drawn(self):
        # Not a tautology now: on these fixtures every hole really is called out, so 1.0
        # is EARNED by observation where it used to be asserted by declaration.
        evaluation = evaluate_step_corpus(load_corpus(_CORPUS))
        assert evaluation.downstream_usefulness.score == 1.0
        assert evaluation.downstream_usefulness.total > 0, "nothing was scored at all"

    def test_the_layer_drops_when_the_sheet_stops_drawing_holes(self, monkeypatch):
        # The load-bearing claim: this score is a function of the DRAWING. Empty the
        # provenance answer for every feature and the layer must fall. Under the old
        # declared constant it stayed at 1.0 no matter what the sheet did.
        from draftwright import drawing as drawing_module

        monkeypatch.setattr(drawing_module.Drawing, "annotations_of", lambda _s, _f: {})
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
        from draftwright import drawing as drawing_module
        from draftwright.evaluation.step_analysis import _default_observers

        monkeypatch.setattr(drawing_module.Drawing, "annotations_of", lambda _s, _f: {})
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
