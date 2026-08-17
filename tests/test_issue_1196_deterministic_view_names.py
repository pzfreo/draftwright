"""#1196 — lint text must not depend on where objects happen to live in memory.

Several lint messages identify a view by name, and for a view carrying neither `label`
nor `name` that name was `f"view@{id(vs)}"` — a CPython object address, fresh on every
render. So the *same* sheet linted twice produced different message text, and any
consumer diffing lint between two renders saw a change that does not exist in the
drawing.

ADR 0006 makes the same argument about layout: a drawing must not depend on incidental
state of the process that produced it. A diagnostic is no different — it is read by
tooling, and the epic in #1202 diffs lint between runs as its core loop, which cannot
work while every run disagrees with itself.

The fix is not a better fallback. `Drawing.views` is keyed by name, so the caller knew
what the view was called and lint had no way to ask; the names are now passed in, and
the fallback is positional rather than identity-based.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from build123d import Box

from draftwright import build_drawing
from draftwright._geometry import _boxes_overlap
from draftwright.linting.structural import _lint_view_shapes, lint_drawing

_QUOTED_VIEW = re.compile(r"view '([^']+)'")


def _quoted_views(message):
    """Every view name a message quotes, in order."""
    return _QUOTED_VIEW.findall(message)


_ADDRESS = re.compile(r"view@\d+")


#: Every code emitted by `_lint_view_shapes` that formats the view NAME — so every code
#: the defect reached. An earlier version of this list held only the two
#: `view_annotation_*` codes, on the false premise that `view_out_of_bounds` came from
#: elsewhere and already carried a real name; it has exactly one emit site, inside this
#: same block, and rendered `view 'view@4859113120'` like the rest. The omission mattered:
#: for this fixture the excluded codes are the ones naming SEVERAL distinct views, which
#: is what makes a mislabelling visible at all.
_NAMED_VIEW_CODES = {
    "view_annotation_overlap",
    "view_annotation_inside_extents",
    "view_overlap",
    "view_out_of_bounds",
    "leader_crosses_silhouette",
}


def _part():
    """A cube on a large sheet: its envelope dimension labels land inside the front
    view's extents, which is what drives the name-carrying codes. Measured — a denser
    holed part on A3/A4 produces none of them, and an earlier draft of this test used
    one and asserted nothing."""
    return Box(50, 50, 50)


def _view_messages(dwg):
    return [i.message for i in dwg.lint() if i.code in _NAMED_VIEW_CODES]


class TestTheSameSheetLintsToTheSameText:
    def test_two_builds_of_one_part_produce_identical_view_messages(self):
        # The reported reproduction. Mutation: restore `f"view@{id(vs)}"` and the two
        # message lists differ on every run.
        first = _view_messages(build_drawing(_part(), page="A1"))
        second = _view_messages(build_drawing(_part(), page="A1"))
        assert first, "fixture produced no view-related lint, so this asserts nothing"
        assert first == second, (
            "identical inputs produced different lint text:\n"
            f"  first:  {first[:2]}\n  second: {second[:2]}"
        )

    def test_no_message_carries_an_object_address(self):
        # Stronger and cheaper than comparing two runs: an address cannot appear at all.
        # Two runs could also coincide by luck if an allocator reused the same block.
        dwg = build_drawing(_part(), page="A1")
        offenders = [i.message for i in dwg.lint() if _ADDRESS.search(i.message)]
        assert not offenders, f"lint text still names views by memory address: {offenders}"


class TestTheNameMatchesTheViewItDescribes:
    """`view_names` and `view_shapes` are matched BY INDEX, and nothing held that
    correspondence. Adversarial review shifted the name list by one in `drawing.py` —
    mislabelling every view — and the **entire fast tier still passed**. Lint then said
    "view 'side' extends past drawable area" when it was the plan view that overflowed:
    a confident wrong answer, which is worse than the address it replaced.

    These check the name against the GEOMETRY rather than against a hard-coded string, so
    any permutation fails, not just the one the review happened to try.
    """

    def test_the_view_reported_out_of_bounds_is_the_one_that_is_out_of_bounds(self):
        dwg = build_drawing(_part(), page="A1")
        above = [
            i.message
            for i in dwg.lint()
            if i.code == "view_out_of_bounds" and "(above" in i.message
        ]
        assert above, "fixture no longer overflows upwards; this asserts nothing"
        named = _quoted_views(above[0])[0]
        highest = max(dwg.views, key=lambda n: (dwg.view_bounds(n) or (0, 0, 0, -1e9))[3])
        assert named == highest, (
            f"lint blamed view {named!r} for overflowing above, but {highest!r} is the "
            f"view that actually reaches highest — the name/shape correspondence is off"
        )

    def test_the_two_views_reported_as_overlapping_actually_overlap(self):
        dwg = build_drawing(_part(), page="A1")
        overlaps = [i.message for i in dwg.lint() if i.code == "view_overlap"]
        assert overlaps, "fixture no longer reports overlapping views"
        first, second = _quoted_views(overlaps[0])[:2]
        a, b = dwg.view_bounds(first), dwg.view_bounds(second)
        assert a is not None and b is not None, (first, second)
        assert _boxes_overlap(a, b), (
            f"lint named {first!r} and {second!r} as overlapping, but their bounds "
            f"{a} and {b} are disjoint — the names do not match the shapes"
        )


class TestAViewIsNamedByWhatTheCallerCallsIt:
    def test_the_drawings_own_view_name_reaches_the_message(self):
        # The point of threading names rather than inventing them: "view 'front'" is
        # actionable where "view[0]" is not. Asserted against the drawing's real keys
        # so this cannot pass on a hard-coded guess.
        dwg = build_drawing(_part(), page="A1")
        named = [i.message for i in dwg.lint() if i.code in _NAMED_VIEW_CODES]
        if not named:
            import pytest

            pytest.skip("fixture produced no view-named lint on this page")
        keys = set(dwg.views)
        quoted = {m.split("view '", 1)[1].split("'", 1)[0] for m in named}
        assert quoted & keys, (
            f"no message names a real view; messages quote {quoted}, drawing has {keys}"
        )


class TestTheFallbackIsPositionalNotIdentity:
    """Direct cover for the branch a real `Drawing` never reaches, because it always
    supplies names. An external caller of `lint_drawing` may not, and the fallback must
    still be deterministic."""

    class _Shape:
        # `label = ""` is what the engine actually sets — an EMPTY STRING, which is
        # falsy, which is why the `or` chain fell through to `id()` on every real view.
        # A stand-in using `None` here would miss the mechanism entirely.
        label = ""
        name = None

        def __init__(self, box):
            self._box = box

        def bounding_box(self):
            x0, y0, x1, y1 = self._box
            return SimpleNamespace(
                min=SimpleNamespace(X=x0, Y=y0, Z=0.0), max=SimpleNamespace(X=x1, Y=y1, Z=0.0)
            )

        def edges(self):
            return []  # no line-work, so an enclosed label reports `inside_extents`

    def _messages(self, view_names=None, shape=None):
        """Lint one view with one label sitting inside it, returning the messages."""
        issues: list = []
        annotation = SimpleNamespace(label="A", label_bbox=(2.0, 2.0, 4.0, 4.0))
        _lint_view_shapes(
            [shape if shape is not None else self._Shape((0.0, 0.0, 10.0, 10.0))],
            [annotation],
            issues,
            view_names=view_names,
            page_bbox=(0.0, 0.0, 100.0, 100.0),
        )
        messages = [i.message for i in issues]
        assert messages, "the scenario produced no message, so it asserts nothing"
        return messages

    def test_an_unnamed_view_falls_back_to_its_position(self):
        messages = self._messages()
        assert any("view[0]" in m for m in messages), messages
        assert not any(_ADDRESS.search(m) for m in messages), messages

    def test_two_calls_on_simultaneously_live_objects_agree(self):
        # The shapes must be alive AT THE SAME TIME. An earlier version linted one, let
        # it be freed, then allocated the second — and CPython reused the address, so
        # under the `id()` mutation the two runs agreed and the test passed in a
        # whole-file run (it failed under `--dist loadscope`, which is the only reason
        # CI caught it). That is precisely the coincidence its own comment claimed to
        # rule out.
        first_shape = self._Shape((0.0, 0.0, 10.0, 10.0))
        second_shape = self._Shape((0.0, 0.0, 10.0, 10.0))
        assert id(first_shape) != id(second_shape)
        assert self._messages(shape=first_shape) == self._messages(shape=second_shape)

    def test_a_supplied_name_wins_over_the_positional_fallback(self):
        assert any("view 'plan'" in m for m in self._messages(view_names=["plan"]))

    def test_lint_drawing_accepts_view_shapes_without_names(self):
        # The parameter is optional: an existing caller passing only `view_shapes` must
        # keep working, or this is a breaking change to a documented surface.
        issues = lint_drawing(
            [],
            page_bbox=(0.0, 0.0, 100.0, 100.0),
            view_shapes=[self._Shape((0.0, 0.0, 10.0, 10.0))],
        )
        assert isinstance(issues, list)

    def test_a_positional_call_in_the_upstream_argument_order_still_binds_the_cache(self):
        # `lint_drawing` is exported in `linting.__all__`, and the upstream helpers'
        # signature has `view_edge_cache` at position 5. A first cut INSERTED
        # `view_names` there, so this call silently bound the cache dict to the names —
        # no error, no caching, degraded view name — and then raised `KeyError: 0` once
        # the cache was warm and its `id()` keys were indexed as a name list. The
        # parameter is appended for exactly this reason; keyword-only tests would not
        # have caught it.
        annotation = SimpleNamespace(label="A", label_bbox=(2.0, 2.0, 4.0, 4.0))
        view, cache = self._Shape((0.0, 0.0, 10.0, 10.0)), {}
        first = lint_drawing([annotation], None, (0.0, 0.0, 100.0, 100.0), 1.0, [view], cache)
        assert cache, "the positional cache argument was bound to something else"
        assert first, "the scenario produced no issue, so it asserts nothing"
        # Again with the now-warm cache: this is where the misbinding raised.
        assert lint_drawing([annotation], None, (0.0, 0.0, 100.0, 100.0), 1.0, [view], cache)
