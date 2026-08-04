"""Shared test helpers.

``counting_calls`` is here rather than in one suite because more than one needs it:
``test_detect_once`` counts the shared cylinder substrate, and the ADR 0017 manifest
guard (#1019) counts recogniser families the same way.
"""

import sys
from collections.abc import Callable, Mapping
from contextlib import contextmanager


@contextmanager
def counting_calls(functions: Mapping[str, Callable[..., object]]):
    """Count invocations of *functions* (``{name: callable}``) by CODE OBJECT.

    Counted with a profile hook rather than by installing spies over the modules that
    import the name. That choice is the whole point, and it was arrived at the hard way:
    four successive review rounds each closed one more *binding form* the spy approach
    could not see — first the modules nobody had listed, then aliased imports
    (``as _rescan``), and the next rung would have been a function held in a container, a
    class attribute, a closure cell or a default argument. Patching bindings can only ever
    cover the forms someone has thought of, and a missed form is silent: the call happens,
    the count does not move, and the guard reports success.

    A code object cannot be re-bound. Every call reaches the same ``__code__``, however the
    caller got hold of the function, so this closes all of those forms at once and needs no
    list of modules to keep in step with the source.

    Two functions may share one code object (two closures off the same factory), and then
    a call to either is indistinguishable from a call to the other. Rejected rather than
    silently attributed to whichever name came last: a helper that exists to remove silent
    blind spots must not open one.

    The hook is thread-local — only the calling thread is measured, which is what the tests
    want. A Python-level profiler already installed is **chained**, not suspended:
    ``sys.setprofile`` holds one function, so an outer profiler (a nested ``counting_calls``,
    a plugin) would otherwise go blind for the duration and never know.

    A C-level profiler cannot be chained, and this refuses to run rather than pretend.
    Under cProfile on Python <= 3.11, ``sys.getprofile()`` returns the ``Profile`` object,
    which is not callable: chaining to it raises ``TypeError`` mid-context, and so does
    restoring it on the way out. (On 3.12+ cProfile moved to ``sys.monitoring``, so
    ``getprofile()`` is ``None`` and there is nothing to collide with.) Refusing on entry is
    the same fail-closed choice as the duplicate-code-object check above: the alternatives
    are a crash from inside a context manager, or counts taken with the outer profiler
    silently switched off.

    Coverage is unaffected throughout — it drives ``sys.settrace``/``sys.monitoring``, a
    separate mechanism.
    """
    by_code: dict = {}
    for name, fn in functions.items():
        code = fn.__code__
        if code in by_code:
            raise ValueError(
                f"{name!r} and {by_code[code]!r} share one code object "
                f"({code.co_name} at {code.co_filename}:{code.co_firstlineno}), so their "
                "calls cannot be told apart. Count them separately."
            )
        by_code[code] = name
    counts: dict[str, int] = {}
    previous = sys.getprofile()
    if previous is not None and not callable(previous):
        raise RuntimeError(
            f"a non-callable profiler is installed ({type(previous).__name__}) — a C-level "
            "profiler such as cProfile on Python <= 3.11. counting_calls can neither chain "
            "to it nor restore it, so it refuses rather than crash mid-context or return "
            "counts taken with the outer profiler silently switched off."
        )

    def hook(frame, event, arg):
        if event == "call":
            name = by_code.get(frame.f_code)
            if name is not None:
                counts[name] = counts.get(name, 0) + 1
        if previous is not None:
            previous(frame, event, arg)

    sys.setprofile(hook)
    try:
        yield counts
    finally:
        sys.setprofile(previous)
