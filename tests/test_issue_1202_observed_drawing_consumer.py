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
        # Dropping the axis component made two opposed bores at the same (x, y) resolve
        # to the SAME feature, so a dropped callout on the second was invisible. Matching
        # is now full-3D and consumes the matched position.
        part = Box(60, 30, 20) - Pos(0, 0, 8) * Cylinder(3, 8) - Pos(0, 0, -5) * Cylinder(3, 14)
        drawing = build_drawing(part)
        holes = _recognised(part)
        if len({(h.diameter, tuple(h.location)) for h in holes}) < 2:
            pytest.skip("this build did not recognise two distinct coaxial holes")
        candidates = _hole_candidates(drawing)
        matched = [c for c in candidates for _p in c.positions]
        assert len(matched) >= 2, "the two bores collapsed to a single position"

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
    def test_a_table_represented_feature_counts_as_consumed(self):
        # Above ~16 scattered holes the engine WITHDRAWS the individual `hc_*` callouts
        # and places one table plus balloons, recording the substitution in
        # `covers_hole_representations_by_feature`. Looking only for `hc_` scored every
        # hole on such a sheet as lost — a false alarm on exactly the dense sheets #1176
        # and ADR 0018 are about. Mutation: drop the ledger read and this fails.
        from draftwright.evaluation.step_analysis import _consumed

        feature = object()
        table = SimpleNamespace(
            covers_hole_representations_by_feature=((feature, "hole_table", "escalation"),)
        )
        drawing = SimpleNamespace(
            annotations_of=lambda _f: {"m_cm0": object()},
            iter_annotations=lambda: (("hole_table_plan", table),),
        )
        from draftwright.evaluation.step_analysis import _table_represented

        assert _consumed(feature, drawing, _table_represented(drawing)) is True
        assert _consumed(object(), drawing, _table_represented(drawing)) is False


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
