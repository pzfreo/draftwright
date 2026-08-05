"""Warning categories — a dependency-free leaf.

Deliberately its own module rather than living in :mod:`draftwright._core`. `_core` imports
build123d, so a category defined there costs the full CAD kernel (~6 s) to reach — and the
pytest ``filterwarnings`` entry that names it pays that on **every** invocation, including
``--collect-only`` and the ~30 s smoke tier. A warning class is the last thing that should
drag a geometry kernel behind it (#1043 review).

Nothing here imports anything.
"""

from __future__ import annotations


class SoftDeprecationWarning(UserWarning):
    """A surface that is **discouraged but supported**, with no removal planned.

    Deliberately NOT a :class:`DeprecationWarning`. ``docs/deprecations.md`` carries ADR 0005
    §4's rule — a compat surface names a tracking issue *and* a removal target, because "a
    facade with no exit date is a failure mode, not a success" — and
    ``tests/test_deprecation_dates.py`` enforces it. Raising a ``DeprecationWarning`` for
    something we intend to keep would mean writing a removal date we do not mean, which is
    that failure mode wearing a date.

    "Soft deprecated" is the term Python uses for exactly this: still supported, not
    scheduled for removal, but not what you should reach for in new code.

    A ``UserWarning`` subclass rather than a bare one so callers can silence this category
    alone::

        import warnings
        from draftwright import SoftDeprecationWarning

        warnings.filterwarnings("ignore", category=SoftDeprecationWarning)

    It is also, in practice, the louder signal: ``DeprecationWarning`` is filtered by default
    in notebooks, several test runners, and library-internal call paths, whereas this shows
    unconditionally.
    """
