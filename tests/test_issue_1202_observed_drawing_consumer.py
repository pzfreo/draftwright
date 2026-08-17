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

`drawing_consumer` is now derived from a real build: did a hole callout actually reach the
sheet for this fact? The other three boundaries remain declared and are tracked separately
— they are the same shape one layer along, and pretending otherwise here would be its own
overclaim.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from build123d import Box, Cylinder, Pos

from draftwright import build_drawing
from draftwright.evaluation.step_analysis import (
    _drawing_consumer_outcome,
    _hole_features,
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


class TestTheOutcomeIsReadOffTheDrawing:
    def test_a_hole_the_drawing_calls_out_is_supported(self):
        part = _part()
        drawing = build_drawing(part)
        features = _hole_features(drawing)
        assert features, "fixture produced no IR hole features, so this asserts nothing"
        holes = _recognised(part)
        assert holes, "fixture produced no recognised holes"
        outcomes = {_drawing_consumer_outcome(h, features, drawing) for h in holes}
        assert outcomes == {"supported"}, outcomes

    def test_a_hole_whose_callout_never_reached_the_sheet_is_unsupported(self):
        # THE case the declared state could not express. The drawing is real; only the
        # provenance answer is emptied, which is exactly what a dropped callout looks
        # like to a consumer. Mutation: restore the declared constant and this passes
        # while reporting `supported` for a hole that was never drawn.
        part = _part()
        drawing = build_drawing(part)
        features = _hole_features(drawing)
        hole = _recognised(part)[0]
        assert _drawing_consumer_outcome(hole, features, drawing) == "supported"

        stripped = SimpleNamespace(annotations_of=lambda _feature: {})
        assert _drawing_consumer_outcome(hole, features, stripped) == "unsupported"

    def test_furniture_without_a_callout_does_not_count_as_consumed(self):
        # A centre mark and location dims are placed for a hole whose SIZE callout was
        # dropped. They are not the fact reaching the sheet — the callout states the
        # diameter — so counting any annotation would let a dropped callout score as
        # supported. Measured names on a real build: hc_plan0, m_cm0, m_locx0, m_locy0.
        part = _part()
        drawing = build_drawing(part)
        features = _hole_features(drawing)
        hole = _recognised(part)[0]

        furniture_only = SimpleNamespace(
            annotations_of=lambda _f: {"m_cm0": object(), "m_locx0": object()}
        )
        assert _drawing_consumer_outcome(hole, features, furniture_only) == "unsupported"

    def test_a_hole_with_no_corresponding_feature_is_unknown(self):
        # ADR 0017 states plainly that recognition-record -> IR-feature correspondence is
        # NOT yet provided. Where it fails, the honest answer is `unknown` — the oracle's
        # third outcome — never a silent success or a false failure.
        part = _part()
        drawing = build_drawing(part)
        hole = _recognised(part)[0]
        assert _drawing_consumer_outcome(hole, [], drawing) == "unknown"


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
