"""Per-family evidence contracts that every `*_completeness_evidence` module asserts.

These bodies were written out once per feature family — thirteen copies of the
`_<family>_model_outcomes` check, eleven of the one-recognition check — differing only
in which family symbol they named. That is the growth pattern `test_clone_budget.py`
exists to stop: the same assertion re-paid on every CI run, and one more copy to write
(and to get subtly wrong) with each new recogniser family.

Each function here is the single body; the per-family test calls it with its own
symbols, so the assertion still runs once per family and the evidence is unchanged.
Adding a family should add a call, not a copy.
"""

from __future__ import annotations

from collections.abc import Callable


def assert_missing_model_outcomes_fail_closed(
    monkeypatch,
    outcomes_attr: str,
    states: Callable[..., object],
) -> None:
    """A family whose per-occurrence outcomes vanish scores `unknown`, never `supported`.

    `outcomes_attr` is the family's `_<family>_model_outcomes` in
    `draftwright.evaluation.step_analysis`; `states` is the module's own `_states`
    reader, so each family still exercises its own observer and fixture part.
    """
    import draftwright.evaluation.step_analysis as step_analysis

    monkeypatch.setattr(step_analysis, outcomes_attr, lambda *_args: [])
    # `set(...)`: a few families' `_states` returns a list, the rest a set. Both mean
    # "every observed boundary state", and both must be exactly `unknown` here.
    assert set(states("ir_adapter")) == {"unknown"}


def assert_observer_uses_one_build_owned_recognition(
    monkeypatch,
    family: str,
    part: object,
) -> None:
    """ADR 3: the observer scores the build's OWN aggregate, recognising exactly once.

    A second `build_recognition_evidence` call would mean the facts being scored and the
    features they are matched against came from different recognition runs.
    """
    import draftwright.analysis as analysis
    from draftwright.evaluation.step_analysis import _default_observers

    original = analysis.build_recognition_evidence
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(analysis, "build_recognition_evidence", counted)
    assert _default_observers()[family](part)
    assert calls == 1
