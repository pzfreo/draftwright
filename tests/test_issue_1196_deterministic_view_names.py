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

from build123d import Box, Pos

from draftwright import build_drawing
from draftwright._core import _MARGIN
from draftwright._geometry import _boxes_overlap
from draftwright.linting.structural import _lint_view_shapes, lint_drawing

_QUOTED_VIEW = re.compile(r"view '([^']+)'")

#: A quoted view name followed by the box the message prints for it. Two formats print
#: one — `view 'x' bbox [...]` (`view_overlap`) and `... view 'x' extents [...]`
#: (`view_annotation_inside_extents`) — and matching only the first left the FRONT view
#: with no name/geometry cross-check at all: its only messages are the second kind.
#: Bounds are signed: a large part on a small page projects to negative page
#: coordinates, and `[\d.]+` would silently drop such a pair, leaving the
#: `len(named) >= 2` guard as the only thing between the test and vacuity.
#: The dash between bounds is an EN dash in the rendered message.
_NAMED_BOX = re.compile(
    r"view '([^']+)'(?: bbox| extents)? \[x=(-?[\d.]+)[-\u2013\u2014](-?[\d.]+), "
    r"y=(-?[\d.]+)[-\u2013\u2014](-?[\d.]+)\]"
)


def _quoted_views(message):
    """Every view name a message quotes, in order."""
    return _QUOTED_VIEW.findall(message)


def _named_boxes(message):
    """``[(name, (x0, y0, x1, y1))]`` — each view the message names, with the box it
    printed for that view. This is what makes a name/shape check permutation-complete:
    the numbers travel in the message beside the name, so a mislabelling is visible
    without any symmetric predicate to hide behind."""
    return [
        (name, (float(x0), float(y0), float(x1), float(y1)))
        for name, x0, x1, y0, y1 in _NAMED_BOX.findall(message)
    ]


def _lint_box(dwg, name):
    """The box lint measures for *name*: the VISIBLE compound only.

    Deliberately not `Drawing.view_bounds`, which unions visible and hidden geometry —
    a superset. They coincide on this fixture, but comparing against the superset would
    make the assertion weaker than the property it claims to check.
    """
    visible = dwg.views[name][0]
    bb = visible.bounding_box()
    return (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y)


_ADDRESS = re.compile(r"view@\d+")


#: A deliberately tiny drawable area, so EVERY view of any part on any page falls outside
#: it and `_lint_view_shapes` must emit a named message. The first version of these tests
#: relied on the engine's own layout producing an overflow — `Box(50,50,50)` at A1 — which
#: held on macOS and produced NO view lint at all on Linux, so all four failed in CI. The
#: preconditions caught it rather than passing vacuously, but the fixture had no business
#: depending on layout: what is under test is that a caller-supplied name reaches the
#: message attached to the right shape, and that needs controlled bounds, not a real page.
_TIGHT_PAGE = (200.0, 200.0, 260.0, 260.0)

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
    one and asserted nothing.

    NOTE for whoever lands ADR 0018 / #1130: this fixture works *because* the sheet is
    badly composed — the plan view runs 73 mm off the drawable area and `side` overlaps
    `iso`. Requirement-driven view planning is meant to remove exactly that, at which
    point these tests lose their subject and fail loudly (their preconditions are hard
    asserts, by design). What they need is any drawing where lint names two or more
    DISTINCT views in one message; `view_annotation_*` on a crowded sheet would do.
    """
    return Box(50, 50, 50)


def _view_lint(dwg, page_bbox=_TIGHT_PAGE):
    """Lint *dwg*'s real views and names against a controlled drawable area."""
    view_items = list(dwg.views.items())
    return lint_drawing(
        list(dwg.items),
        page_bbox=page_bbox,
        view_shapes=[visible for _n, (visible, _h) in view_items],
        view_names=[name for name, _pair in view_items],
    )


def _view_messages(dwg):
    return [i.message for i in _view_lint(dwg) if i.code in _NAMED_VIEW_CODES]


def _lint_box(dwg, name):
    """The box lint measures for *name*: the VISIBLE compound only.

    Deliberately not `Drawing.view_bounds`, which unions visible and hidden geometry — a
    superset that would make the assertion weaker than the property it claims to check.
    """
    bb = dwg.views[name][0].bounding_box()
    return (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y)


_OVERFLOW = re.compile(
    r"view '([^']+)' extends past drawable area \((left|right|above|below) by ([\d.]+) mm\)"
)


class TestTheSameSheetLintsToTheSameText:
    def test_two_builds_of_one_part_produce_identical_view_messages(self):
        # The reported reproduction. Mutation: restore `f"view@{id(vs)}"` and the two
        # message lists differ on every run.
        first = _view_messages(build_drawing(_part(), page="A3"))
        second = _view_messages(build_drawing(_part(), page="A3"))
        assert first, "fixture produced no view-related lint, so this asserts nothing"
        assert first == second, (
            "identical inputs produced different lint text:\n"
            f"  first:  {first[:2]}\n  second: {second[:2]}"
        )

    def test_no_message_carries_an_object_address(self):
        # Stronger and cheaper than comparing two runs: an address cannot appear at all.
        # Two runs could also coincide by luck if an allocator reused the same block.
        dwg = build_drawing(_part(), page="A3")
        offenders = [
            i.message for i in list(dwg.lint()) + _view_lint(dwg) if _ADDRESS.search(i.message)
        ]
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

    def test_the_drawings_own_pairing_is_correct_end_to_end(self):
        # THE guard on `Drawing._lint`'s own name/shape pairing, which is where a
        # permutation would actually happen. The other tests here call `lint_drawing`
        # directly with names they construct, so they cannot see that code at all — an
        # intermediate version of this file made every one of them layout-independent and
        # in doing so let the whole permutation battery pass.
        #
        # Deterministic without depending on layout: shrink the drawable area AFTER the
        # build, so every view necessarily overflows and `Drawing._lint` must name each.
        dwg = build_drawing(_part(), page="A3")
        boxes = {name: _lint_box(dwg, name) for name in dwg.views}
        dwg.page_w, dwg.page_h = 60.0, 60.0
        messages = [i.message for i in dwg.lint() if i.code == "view_out_of_bounds"]
        assert len(messages) >= 4, f"expected several overflows, got {messages}"

        page = (_MARGIN, _MARGIN, dwg.page_w - _MARGIN, dwg.page_h - _MARGIN)
        for message in messages:
            match = _OVERFLOW.search(message)
            assert match, f"unparsed overflow message: {message!r}"
            name, direction, amount = match.group(1), match.group(2), float(match.group(3))
            assert name in boxes, f"lint named {name!r}, which is not a view"
            x0, y0, x1, y1 = boxes[name]
            actual = {
                "left": page[0] - x0,
                "right": x1 - page[2],
                "below": page[1] - y0,
                "above": y1 - page[3],
            }[direction]
            assert abs(actual - amount) < 0.06, (
                f"lint said view {name!r} overflows {direction} by {amount} mm; its real "
                f"box {(x0, y0, x1, y1)} overflows by {actual:.2f} mm — `Drawing._lint` "
                f"paired the name with the wrong shape"
            )

    def test_every_overflow_message_names_the_view_that_actually_overflows(self):
        # Checks the name against the GEOMETRY, and against the amount the message states,
        # so any permutation of `view_names` is caught rather than only the one a reviewer
        # happened to try. Independent of where the engine chose to put the views: the
        # drawable area is supplied, so the overflows are ours.
        dwg = build_drawing(_part(), page="A3")
        messages = [i.message for i in _view_lint(dwg) if i.code == "view_out_of_bounds"]
        assert messages, "the tight page produced no overflow at all"
        checked = 0
        for message in messages:
            match = _OVERFLOW.search(message)
            assert match, f"unparsed overflow message: {message!r}"
            name, direction, amount = match.group(1), match.group(2), float(match.group(3))
            assert name in dwg.views, f"lint named {name!r}, which is not a view"
            x0, y0, x1, y1 = _lint_box(dwg, name)
            actual = {
                "left": _TIGHT_PAGE[0] - x0,
                "right": x1 - _TIGHT_PAGE[2],
                "below": _TIGHT_PAGE[1] - y0,
                "above": y1 - _TIGHT_PAGE[3],
            }[direction]
            assert abs(actual - amount) < 0.06, (
                f"lint said view {name!r} overflows {direction} by {amount} mm, but its "
                f"real box {(x0, y0, x1, y1)} overflows by {actual:.2f} mm — the name is "
                f"on the wrong shape"
            )
            checked += 1
        assert checked >= 2, f"only {checked} view(s) cross-checked"

    def test_each_named_view_is_reported_with_its_own_bbox(self):
        # Overlap is SYMMETRIC, so an earlier version — asserting merely that the two
        # named views overlap — was invariant under swapping them. Adversarial review
        # swapped `side` and `iso`, producing both names on each other's boxes, and all
        # 4,092 fast-tier tests passed. The message PRINTS the boxes, so compare those:
        # the numbers cannot move with the name.
        #
        # Driven on two placed shapes rather than on whichever views the engine happens
        # to overlap. Whether real views overlap is a layout outcome and differs by
        # platform — the version of this test that relied on it produced no message at
        # all on Linux. The shapes are real `Compound`s and go through the real
        # `lint_drawing`; only their positions are ours.
        alpha = Pos(100, 100, 0) * Box(60, 40, 1)
        beta = Pos(130, 120, 0) * Box(60, 40, 1)  # overlaps alpha by 30 x 20
        issues = lint_drawing(
            [],
            page_bbox=(0.0, 0.0, 400.0, 400.0),
            view_shapes=[alpha, beta],
            view_names=["alpha", "beta"],
        )
        overlaps = [i.message for i in issues if i.code == "view_overlap"]
        assert overlaps, f"the two placed views did not report an overlap: {issues}"
        named = _named_boxes(overlaps[0])
        assert len(named) == 2, f"expected two named boxes, parsed {named} from {overlaps[0]!r}"
        assert {name for name, _b in named} == {"alpha", "beta"}, named

        real = {}
        for shape, name in ((alpha, "alpha"), (beta, "beta")):
            bb = shape.bounding_box()
            real[name] = (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y)
        for name, printed in named:
            # 0.06 mm: the message rounds to one decimal place.
            assert all(abs(p - a) < 0.06 for p, a in zip(printed, real[name], strict=True)), (
                f"lint printed {printed} for view {name!r}, whose real box is "
                f"{tuple(round(v, 2) for v in real[name])} — the name is on the wrong shape"
            )
        assert _boxes_overlap(*(box for _n, box in named)), (
            "the views reported as overlapping do not overlap"
        )

    def test_a_swapped_name_pair_is_caught(self):
        # The property the bbox comparison exists for, stated directly: hand lint the two
        # names in the wrong order and the printed boxes must no longer match.
        alpha = Pos(100, 100, 0) * Box(60, 40, 1)
        beta = Pos(130, 120, 0) * Box(60, 40, 1)
        issues = lint_drawing(
            [],
            page_bbox=(0.0, 0.0, 400.0, 400.0),
            view_shapes=[alpha, beta],
            view_names=["beta", "alpha"],  # deliberately swapped
        )
        overlaps = [i.message for i in issues if i.code == "view_overlap"]
        assert overlaps, "no overlap reported, so the swap cannot be observed"
        named = dict(_named_boxes(overlaps[0]))
        bb = alpha.bounding_box()
        alpha_box = (bb.min.X, bb.min.Y, bb.max.X, bb.max.Y)
        assert not all(
            abs(p - a) < 0.06 for p, a in zip(named["alpha"], alpha_box, strict=True)
        ), "a swapped name pair printed the correct boxes, so the check cannot detect one"


class TestAViewIsNamedByWhatTheCallerCallsIt:
    def test_the_drawings_own_view_name_reaches_the_message(self):
        # The point of threading names rather than inventing them: "view 'front'" is
        # actionable where "view[0]" is not. Asserted against the drawing's real keys
        # so this cannot pass on a hard-coded guess.
        dwg = build_drawing(_part(), page="A3")
        named = [i.message for i in _view_lint(dwg) if i.code in _NAMED_VIEW_CODES]
        # Hard assert, not a skip: this covers the PR's headline claim, and this file's
        # whole thesis is that a precondition must FAIL rather than quietly vanish.
        assert named, "fixture produced no view-named lint, so this asserts nothing"
        keys = set(dwg.views)
        quoted = {v for m in named for v in _quoted_views(m)}
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

    def test_a_supplied_name_also_wins_over_a_label_on_the_shape(self):
        # The caller is the authority. Untested until adversarial review pointed it out:
        # reverting to `label or name or supplied` passed every test, because no fixture
        # anywhere has a TRUTHY label — every real view compound carries `label == ""`.
        # DXF layer naming is the obvious future reason one would, and it would then
        # silently override the drawing's own key.
        labelled = self._Shape((0.0, 0.0, 10.0, 10.0))
        labelled.label = "compound-42"
        messages = self._messages(view_names=["plan"], shape=labelled)
        assert any("view 'plan'" in m for m in messages), messages
        assert not any("compound-42" in m for m in messages), messages

    def test_lint_drawing_accepts_view_shapes_without_names(self):
        # The parameter is optional: an existing caller passing only `view_shapes` must
        # keep working, or this is a breaking change to a documented surface.
        # `assert isinstance(issues, list)` was the earlier assertion and held even with
        # view handling removed entirely — `lint_drawing` always returns a list, and this
        # scenario produced zero issues. Assert the view was actually PROCESSED.
        annotation = SimpleNamespace(label="A", label_bbox=(2.0, 2.0, 4.0, 4.0))
        issues = lint_drawing(
            [annotation],
            page_bbox=(0.0, 0.0, 100.0, 100.0),
            view_shapes=[self._Shape((0.0, 0.0, 10.0, 10.0))],
        )
        assert any("view[0]" in i.message for i in issues), [i.message for i in issues]

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
