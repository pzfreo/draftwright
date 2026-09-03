"""Shared test helpers.

``counting_calls`` is here rather than in one suite because more than one needs it:
``test_detect_once`` counts the shared cylinder substrate, and the ADR 0017 guards observe
the public aggregate and any consumer-side recogniser bypasses the same way.
"""

import inspect
import sys
from collections.abc import Callable, Mapping
from contextlib import contextmanager

import pytest


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


@contextmanager
def recognition_consumer_calls():
    """Count aggregate acquisitions and public physical recogniser calls made outside them.

    Draftwright owns whether and when a build requests recognition, and must not bypass the
    aggregate by invoking a public, ``part``-taking recogniser itself. Part-less pattern
    functions are pure projections over accepted records and remain valid consumer operations.
    The provider owns which registered families execute *inside* the aggregate and tests that
    invariant in its released suite. Tracking public code objects while the aggregate is not
    on the call stack keeps both consumer claims fail-closed without inspecting the provider's
    private registry.
    """
    import b123d_recognisers as recognition
    import b123d_recognisers.evidence as recognition_evidence
    from _recogniser_public_contract import public_recogniser_member, public_recogniser_names

    aggregates = {
        recognition.build_raw_recognition_result.__code__: "build_raw_recognition_result",
        recognition.PreparedFramedPart.recognise_evidence.__code__: "recognise_framed_evidence",
        recognition_evidence.build_recognition_evidence.__code__: "build_recognition_evidence",
    }
    functions = {}
    for name in public_recogniser_names():
        if name != "step_level_records" and not name.startswith("recognise_"):
            continue
        fn = public_recogniser_member(name)
        if "part" in inspect.signature(fn).parameters:
            functions[name] = fn
    by_code: dict = {}
    for name, fn in functions.items():
        code = fn.__code__
        if code in by_code:
            raise ValueError(
                f"public recognisers {name!r} and {by_code[code]!r} share one code object"
            )
        by_code[code] = name

    counts: dict[str, int] = {}
    aggregate_depth = 0
    previous = sys.getprofile()
    if previous is not None and not callable(previous):
        raise RuntimeError(
            f"a non-callable profiler is installed ({type(previous).__name__}) — "
            "recognition_consumer_calls cannot chain to it"
        )

    def hook(frame, event, arg):
        nonlocal aggregate_depth
        if event == "call":
            if (aggregate_name := aggregates.get(frame.f_code)) is not None:
                counts[aggregate_name] = counts.get(aggregate_name, 0) + 1
                aggregate_depth += 1
            elif aggregate_depth == 0 and (name := by_code.get(frame.f_code)) is not None:
                counts[name] = counts.get(name, 0) + 1
        elif event == "return" and frame.f_code in aggregates:
            aggregate_depth -= 1
        if previous is not None:
            previous(frame, event, arg)

    sys.setprofile(hook)
    try:
        yield counts
    finally:
        sys.setprofile(previous)


# ── The `unit` tier (#656): pure-logic tests, zero OCC geometry ──────────────────────
#
# `uv run pytest -m unit` is the inner loop: it must run in seconds and build nothing.
# Membership is centralised here so the tier has one place to grow; honesty is enforced
# by the runtest hooks below — constructing any build123d Shape while a unit-marked
# test runs fails it, and the patch is installed in `pytest_runtest_setup`, BEFORE
# fixture setup, so geometry built in a fixture of any scope on behalf of a unit test
# is intercepted too (a function-scoped autouse fixture missed module-scoped fixtures;
# caught by the #1226 review's probe). **Known gap** (documented, in the sibling
# ratchets' style): geometry constructed at module IMPORT time runs during collection,
# before any runtest hook — none of these modules does that, and an import-time OCC
# build would also show up as collection slowness. A module moves to this list only if
# every test in it passes under the enforcement.

_UNIT_MODULES = frozenset(
    {
        "test_api_docs.py",
        "test_architecture_docs.py",
        "test_carve_free_position_callers.py",
        "test_counting_calls.py",
        "test_deprecation_dates.py",
        "test_import_boundaries.py",
        "test_label_provenance.py",
        "test_layout.py",
        "test_issue_1332_overlap_remedy.py",
        "test_lint_ink_overlap.py",
        "test_linting.py",
        "test_pmi_part21.py",
        "test_principal_profile_classifier.py",
        "test_private_test_attr_reads.py",
        "test_private_test_imports.py",
        "test_quality_components.py",
        "test_recogniser_adoption.py",
        "test_registry.py",
        "test_workflows.py",
    }
)

_SHAPE_INIT = pytest.StashKey()


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.path.name in _UNIT_MODULES:
            item.add_marker(pytest.mark.unit)


def _forbidden_shape_init(self, *args, **kwargs):
    raise AssertionError(
        "this test is in the `unit` tier (conftest._UNIT_MODULES) but constructs "
        "build123d geometry — move the module out of the tier or make the test pure (#656)"
    )


def pytest_runtest_setup(item):
    if item.get_closest_marker("unit") is None:
        return
    from build123d.topology import shape_core

    item.stash[_SHAPE_INIT] = shape_core.Shape.__init__
    shape_core.Shape.__init__ = _forbidden_shape_init


def pytest_runtest_teardown(item, nextitem):
    original = item.stash.get(_SHAPE_INIT, None)
    if original is not None:
        from build123d.topology import shape_core

        shape_core.Shape.__init__ = original
