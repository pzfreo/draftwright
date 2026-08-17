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
from draftwright.linting.structural import _lint_view_shapes, lint_drawing

_ADDRESS = re.compile(r"view@\d+")


#: Codes emitted by `_lint_view_shapes` using the view NAME — the ones the defect
#: reaches. Chosen by inspection of the emit sites, not by "any message mentioning a
#: view": `view_out_of_bounds` is produced elsewhere and already carried a real name,
#: so a fixture that only triggered it would prove nothing.
_NAMED_VIEW_CODES = {"view_annotation_overlap", "view_annotation_inside_extents"}


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

    def _messages(self, view_names=None):
        """Lint one view with one label sitting inside it, returning the messages."""
        issues: list = []
        annotation = SimpleNamespace(label="A", label_bbox=(2.0, 2.0, 4.0, 4.0))
        _lint_view_shapes(
            [self._Shape((0.0, 0.0, 10.0, 10.0))],
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

    def test_two_calls_on_distinct_objects_agree(self):
        # Distinct shapes each call, so distinct ids; the text must not differ. An
        # earlier draft compared two empty lists and held under every mutation.
        assert self._messages() == self._messages()

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
