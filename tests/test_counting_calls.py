"""The shared call-counting helper's own contract (#1027).

``counting_calls`` exists to remove silent blind spots from the recogniser guards, so its
two ways of *being* a blind spot get counterexamples of their own: an outer profiler going
dark for the duration, and two functions sharing one code object.
"""

import sys

import pytest
from conftest import counting_calls


def _target():
    return "t"


def _other():
    return "o"


def _closure_factory(n):
    """Two calls return two function objects that share ONE code object."""

    def made():
        return n

    return made


def test_an_outer_profiler_still_sees_calls_made_inside():
    """``sys.setprofile`` holds exactly one function, so installing the hook replaces
    whatever was there. Restoring it afterwards is not enough — an outer profiler that is
    blind for the duration reports a call count that is silently short, which is the exact
    failure mode this helper was written to end.
    """
    seen: list[str] = []

    def outer(frame, event, arg):
        if event == "call":
            seen.append(frame.f_code.co_name)

    before = sys.getprofile()
    sys.setprofile(outer)
    try:
        with counting_calls({"target": _target}) as counts:
            _target()
            inside = sys.getprofile()
        restored = sys.getprofile()
    finally:
        # Restore what was there, not None: clobbering an outer profiler for the rest of the
        # session is the same defect this test exists to catch, committed by the test.
        sys.setprofile(before)

    assert counts == {"target": 1}
    assert "_target" in seen, "the outer profiler went blind inside counting_calls"
    assert inside is not outer, "the hook was never installed"
    assert restored is outer, "the outer profiler was not restored"


def test_counting_two_functions_that_share_a_code_object_is_refused():
    """Two closures off one factory have the same ``__code__``, so a call to either is
    indistinguishable from a call to the other. The dict comprehension this replaces kept
    whichever name came last and attributed both to it — a wrong count reported as a
    successful one.
    """
    first, second = _closure_factory(1), _closure_factory(2)
    assert first.__code__ is second.__code__, "fixture no longer shares a code object"

    with pytest.raises(ValueError, match="share one code object"):
        with counting_calls({"first": first, "second": second}):
            pass  # pragma: no cover — the raise happens on entry


def test_the_refusal_leaves_the_profiler_untouched():
    """Validation runs before the hook is installed, so a refused call cannot leave the
    interpreter profiling into a context that has already unwound."""
    first, second = _closure_factory(1), _closure_factory(2)
    before = sys.getprofile()

    with pytest.raises(ValueError):
        with counting_calls({"first": first, "second": second}):
            pass  # pragma: no cover

    assert sys.getprofile() is before


def test_distinct_functions_are_counted_separately():
    with counting_calls({"target": _target, "other": _other}) as counts:
        _target()
        _target()
        _other()

    assert counts == {"target": 2, "other": 1}


def test_a_c_level_profiler_is_refused_rather_than_crashed_into(monkeypatch):
    """Under cProfile on Python <= 3.11, ``sys.getprofile()`` returns the ``Profile``
    object, which is **not callable**. Chaining to it raises ``TypeError`` from inside the
    context, and so does restoring it on exit — verified on 3.10, where both paths blow up.

    ``sys.setprofile`` rejects non-callables, so the object cannot be installed for real
    here; the guard reads ``sys.getprofile()``, which is what is faked.
    """

    class _NotCallable:
        pass

    monkeypatch.setattr(sys, "getprofile", lambda: _NotCallable())
    installed = []
    monkeypatch.setattr(sys, "setprofile", lambda fn: installed.append(fn))

    with pytest.raises(RuntimeError, match="non-callable profiler"):
        with counting_calls({"target": _target}):
            pass  # pragma: no cover — the raise happens on entry

    assert not installed, "the hook was installed before the profiler was checked"
