"""The smallest corpus subset that still exercises a provider mutation.

The `weakening_provider_*` / `deleting_provider_*` tests damage the provider and assert
that the benchmark's score falls. They read `detection` and `parameter_fidelity` only —
never `downstream_usefulness`, `complete_cases` or `conformant_cases` — yet they scored
every fixture in the family's corpus, and the drawing_consumer observer builds a full
drawing per case. Measured on the groove corpus: 4.77 s to re-prove one direction across
eight fixtures, of which 98% is `build_drawing`.

Scoring two fixtures proves the same direction. What it must NOT do is prove it vacuously,
so the subset is chosen rather than truncated, and chosen by TAG rather than by position:

* **one negative case**, always. Without a fixture the family must NOT match,
  `false_positives == 0` is satisfied by a corpus that cannot produce a false positive at
  all, and `detection.recall == 1.0` is trivially true of nothing.

  Which negative matters. Taking the first in file order picked `chamfer-plain-negative`
  and `fillet-plain-negative` — plain blocks carrying no bevel geometry whatsoever — so a
  recogniser mutated to accept any `overlapping-family` shape would still have scored
  `false_positives == 0`. That is exactly the vacuity the negative is here to prevent, so
  an `ambiguous` or `overlapping-family` negative is preferred: one that the family could
  plausibly claim and must not. `_NO_CONFUSABLE_NEGATIVE` records the two corpora that
  offer none, rather than letting the weaker choice pass unremarked.

* **the positive expecting the most facts** (`BenchmarkCase.expected`), so the damaged
  parameters are actually present to be scored. Ties are the norm — five positives tie in
  pockets, six in polygonal-stock — so the tie is broken on `case_id`. Without that, the
  exact ratios these tests assert would be pinned to JSON key order, and reformatting a
  corpus file (touching no code) could silently re-point a test at different geometry.

`assert_reduced_corpus_is_sound` is the precondition that keeps this honest: the same
subset must score a clean 1.0. A subset that is already failing would let a damage
assertion pass for the wrong reason. `reduced_baseline_fixture` builds the per-module
fixture, so a new family adds a call rather than a tenth copy of the same body — the
growth pattern `test_clone_budget.py` exists to stop.

The full corpus is still scored in full by each module's `test_real_*_corpus_scores_*`,
which is where the absolute per-family counts live.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

#: Corpora offering no `ambiguous` / `overlapping-family` negative, so the plain-block
#: negative is the only one available. Their `false_positives == 0` assertions are
#: correspondingly weaker, and adding a confusable negative fixture would strengthen them.
_NO_CONFUSABLE_NEGATIVE = frozenset({"corpus-chamfers-v1.json", "corpus-turned-steps-v1.json"})

#: Tags marking a negative the family could plausibly claim, and must not.
_CONFUSABLE = ("ambiguous", "overlapping-family")


def reduced_corpus(corpus):
    """One confusable negative plus the fact-richest positive, chosen deterministically."""
    negatives = [case for case in corpus.cases if "negative" in _tags(case)]
    positives = [
        case
        for case in corpus.cases
        if "positive" in _tags(case) and "negative" not in _tags(case)
    ]
    assert negatives, (
        "no negative case: a corpus that cannot produce a false positive makes "
        "`false_positives == 0` vacuous, so it cannot be reduced this way"
    )
    assert positives, "no positive case: `detection.recall` would be vacuous"

    confusable = sorted(
        (case for case in negatives if any(tag in _tags(case) for tag in _CONFUSABLE)),
        key=lambda case: case.case_id,
    )
    negative = confusable[0] if confusable else sorted(negatives, key=lambda c: c.case_id)[0]

    # `case_id` breaks the tie: `len(expected)` alone leaves the choice to JSON key order.
    richest = max(positives, key=lambda case: (len(case.expected), case.case_id))
    assert richest.expected, (
        f"richest positive {richest.case_id!r} expects no facts, so parameter fidelity "
        "would be scored over an empty denominator"
    )
    return replace(corpus, cases=(negative, richest))


def _tags(case) -> tuple[str, ...]:
    """The case's tags, which `load_corpus` joins into `classification`."""
    return tuple(case.classification.split("+"))


def assert_reduced_corpus_is_sound(evaluate, corpus) -> object:
    """Score the subset CLEAN and require a perfect result; return it for denominators.

    Every damage assertion in the module is relative to this. If the subset were already
    imperfect, `score == 0.5` after damage could hold because the fixture was broken
    rather than because the damage was detected.
    """
    clean = evaluate(reduced_corpus(corpus))
    assert clean.detection.recall == 1.0, (
        f"reduced corpus does not detect cleanly (recall {clean.detection.recall}); "
        "damage assertions built on it would pass for the wrong reason"
    )
    assert clean.detection.false_positives == 0, (
        f"reduced corpus false-positives cleanly ({clean.detection.false_positives})"
    )
    assert clean.parameter_fidelity.score == 1.0, (
        f"reduced corpus scores {clean.parameter_fidelity.score} clean, not 1.0"
    )
    assert clean.parameter_fidelity.total > 0, "reduced corpus scores no parameters at all"
    return clean


def reduced_baseline_fixture(corpus_path):
    """The per-module `reduced_baseline` fixture, as a call rather than a tenth copy.

    Module scope, so the clean evaluation is paid once per module — note that under
    `--dist worksteal` (which `scripts/pr-check --full` uses) xdist may distribute one
    module across workers and set it up once per worker. CI runs `loadscope` and pays it
    once.
    """

    @pytest.fixture(scope="module")
    def reduced_baseline():
        from draftwright.evaluation.step_analysis import evaluate_step_corpus, load_corpus

        return assert_reduced_corpus_is_sound(evaluate_step_corpus, load_corpus(corpus_path))

    return reduced_baseline
