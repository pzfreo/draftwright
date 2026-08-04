"""Shared test helpers.

``counting_calls`` is here rather than in one suite because more than one needs it:
``test_detect_once`` counts the shared cylinder substrate, and the ADR 0017 manifest
guard (#1019) counts recogniser families the same way.
"""

import sys
from contextlib import contextmanager


@contextmanager
def counting_calls(functions: dict):
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

    The hook is thread-local (only the calling thread is measured, which is what the tests
    want) and saves/restores any profiler already installed, so it composes with a coverage
    run — ``coverage`` drives ``sys.settrace``/``sys.monitoring``, a separate mechanism.
    """
    by_code = {fn.__code__: name for name, fn in functions.items()}
    counts: dict[str, int] = {}

    def hook(frame, event, arg):
        if event == "call":
            name = by_code.get(frame.f_code)
            if name is not None:
                counts[name] = counts.get(name, 0) + 1

    previous = sys.getprofile()
    sys.setprofile(hook)
    try:
        yield counts
    finally:
        sys.setprofile(previous)
