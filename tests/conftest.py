"""Shared test helpers.

``counting_calls`` is here rather than in one suite because more than one needs it:
``test_detect_once`` counts the shared cylinder substrate, and the ADR 3 (was 0017) guards observe
the public aggregate and any consumer-side recogniser bypasses the same way.
"""

import inspect
import sys
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

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
    import quiddity as recognition
    import quiddity.evidence as recognition_evidence
    from _recogniser_public_contract import public_recogniser_member, public_recogniser_names

    aggregates = {
        recognition.build_raw_recognition_result.__code__: "build_raw_recognition_result",
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
        "test_clone_budget.py",
        "test_counting_calls.py",
        "test_deprecation_dates.py",
        "test_import_boundaries.py",
        "test_inspection_contract.py",
        "test_label_provenance.py",
        "test_layout.py",
        "test_issue_1312_engine_costs.py",
        "test_issue_1332_overlap_remedy.py",
        "test_issue_1471_evidence_schema.py",
        "test_lint_ink_overlap.py",
        "test_linting.py",
        "test_pmi_part21.py",
        "test_principal_profile_classifier.py",
        "test_private_test_attr_reads.py",
        "test_private_test_imports.py",
        "test_quality_components.py",
        "test_recogniser_adoption.py",
        "test_registry.py",
        "test_suite_shape.py",
        "test_workflows.py",
        "test_version_bump_ci.py",
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


# ── Shared built drawings (#1637 step 4) ─────────────────────────────────────────────
#
# Roughly a third of the suite's test functions pay for a real `build_drawing` in their
# own body, and hundreds of those builds are the same plain block with the same options:
# the drawing is the *substrate* the test critiques, not its subject. `shared_drawing`
# builds each (recipe, options) pair once per worker session and hands the same Drawing
# to every read-only borrower.
#
# Read-only is the whole bargain, and it is checked rather than asked for: the cache
# fingerprints the sheet's membership after the build and re-checks it on every later
# handout, so a borrower that mutates is caught at the *next* borrower's setup and named.
# A test that means to mutate calls `unshared_drawing_for_mutation` instead — deliberately
# the longer name, because the cheap call should be the safe one.
#
# ADR 3 is unaffected: a cache hit returns a Drawing that has already been built, so it
# runs no builder code and no recognition at all. Sharing can only lower the number of
# recognition runs in a session, never raise it, and never adds a second run to one build.
# Guards that *count* recognition (`test_detect_once`, `test_declared_recognition_gate`)
# must keep building their own drawings inside their counting context; see
# `tests/test_shared_drawing_cache.py`.


@dataclass
class _SharedDrawing:
    drawing: object
    fingerprint: tuple
    borrower: str


def _sheet_membership(drawing) -> tuple:
    """The part of a Drawing a read-only borrower must leave exactly as it found it.

    Annotation names, item count and view names between them move under every mutating
    surface the fixture forbids — `.add()` and `.place_dim()` extend the first two,
    `.repair()` and `.export()`'s `finalize()` replace items, and a private write that
    re-composes changes the views. It is a membership check, not a deep equality: a test
    that reaches in and edits one annotation's coordinates in place is not caught here,
    which is why the docstring asks and this only enforces the common forms.
    """
    return (
        tuple(drawing.annotations()),
        len(drawing.items),
        tuple(sorted(drawing.views)),
    )


@pytest.fixture(scope="session")
def _built_drawing_cache():
    cache: dict[tuple, _SharedDrawing] = {}
    yield cache
    cache.clear()


@pytest.fixture
def shared_drawing(_built_drawing_cache, request):
    """`shared_drawing(recipe, **options)` → a READ-ONLY Drawing, built once per session.

    *recipe* names an entry in `tests/_parts.py`; *options* are `build_drawing` keywords
    and are part of the cache key, so `page="A3"` and the default page are two builds.

    Do not mutate what comes back. Call `unshared_drawing_for_mutation` with the same
    arguments for a private copy.
    """
    from _parts import part

    from draftwright import build_drawing

    def _shared(recipe: str, **options):
        key = (recipe, tuple(sorted(options.items())))
        try:
            hash(key)
        except TypeError as exc:
            raise TypeError(
                f"build option values must be hashable to key the shared-build cache; "
                f"{options!r} is not. Build it with unshared_drawing_for_mutation."
            ) from exc
        entry = _built_drawing_cache.get(key)
        if entry is None:
            drawing = build_drawing(part(recipe), **options)
            _built_drawing_cache[key] = _SharedDrawing(
                drawing, _sheet_membership(drawing), request.node.nodeid
            )
            return drawing
        if _sheet_membership(entry.drawing) != entry.fingerprint:
            raise AssertionError(
                f"the shared drawing for {key!r} was mutated after it was built — "
                f"{entry.borrower} is the test that last borrowed it. A test that adds, "
                "repairs, places a dimension or exports must ask for its own build with "
                "the unshared_drawing_for_mutation fixture."
            )
        entry.borrower = request.node.nodeid
        return entry.drawing

    return _shared


@pytest.fixture
def unshared_drawing_for_mutation():
    """`unshared_drawing_for_mutation(recipe, **options)` → a private Drawing, built now.

    Same arguments as `shared_drawing`, no cache: the caller owns the result and may
    mutate it freely.
    """
    from _parts import part

    from draftwright import build_drawing

    def _build(recipe: str, **options):
        return build_drawing(part(recipe), **options)

    return _build
