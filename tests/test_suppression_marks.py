"""Suppression MARKS a dimension; it does not filter the group (ADR 0016 / #875).

The rule the whole ADR rests on:

> **No renderer may infer an engineering fact from the presence or absence of a dimension
> parameter.** Parameters carry values *for display*; facts live on the feature.

Two mechanics follow, and this file pins both.

**Suppression is honoured everywhere.** `PlannedDimension` has carried `suppressed`/`reason`
for some time and thirteen render sites already skipped them. The compound-callout path
(`_first` → `hole_callout_spec`) was the one reader that did not, so a suppressed counterbore
still printed. The group keeps its engineering data either way — what suppression changes is
whether a value reaches the page, which is why "marks, not filters" is the accurate verb.

**The head of a compound callout is a declared dependency.** `⌀20 THRU ⌴ ⌀32 ↓ 1.5` has no
reading with its leading term removed, so suppressing the bore ⌀ while a counterbore or
countersink intent remains raises. The two alternatives were rejected on the same ground:
lint-and-drop silently discards authored intent, and implicitly restoring the head makes the
drawing say something the script does not.

The gate, from #875: **a blind hole never prints `THRU` under any suppression combination.**
That is the #868 rule under adversarial pressure — `through` is read off the feature, so no
amount of removing parameters can turn a blind hole into a through one.

**Why these tests construct suppression by hand.** No user-facing path can suppress a hole
parameter yet: the planner suppresses only envelope spans, and `add_dimension()` can merely
CLEAR a suppression. Authored suppression-by-omission is #876. So #875 lands the mechanism
that makes suppression safe *before* anything can reach it, and these tests exercise it at the
level where it lives — hand-built planner output. When #876 arrives, this file is where the
end-to-end cases belong, and `test_suppression_is_still_unreachable_from_a_user_path` below is
the reminder: it fails the moment a real path can produce one.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from draftwright.annotations.from_model import hole_callout_spec
from draftwright.model import Frame, HoleFeature, PartModel
from draftwright.model.planner import plan_dimensions

_BBOX = (-45.0, -30.0, -10.0, 45.0, 30.0, 10.0)


def _group(feature):
    (group,) = plan_dimensions(PartModel(bbox=_BBOX, orientation="prismatic", features=[feature]))
    return group


def _suppress(group, *targets):
    """Mark the named (kind, role) dimensions suppressed, as the planner would.

    Rebuilds each unit from ITS OWN members. The first draft assigned the flattened `group.dims`
    to every unit, which duplicated each dimension across the units — a two-unit group came back
    with four dims. The tests passed anyway, because `_first` returns the first match either way;
    that is the shape of a harness bug that hides the very thing it exists to measure.
    """
    wanted = set(targets)
    units = tuple(
        replace(
            unit,
            members=tuple(
                replace(pd, suppressed=True, reason="authored omission")
                if (pd.param.kind, pd.param.role) in wanted
                else pd
                for pd in unit.members
            ),
        )
        for unit in group.units
    )
    marked = replace(group, units=units)
    assert sum(pd.suppressed for pd in marked.dims) == len(wanted), (
        f"expected to suppress {sorted(wanted)}, but the group carries "
        f"{sorted({(pd.param.kind, pd.param.role) for pd in group.dims})} — a suppression that "
        "marks nothing would make every assertion below vacuous"
    )
    assert len(marked.dims) == len(group.dims), "suppressing must not add or drop dimensions"
    return marked


def _blind():
    return HoleFeature(Frame((0, 0, 10), "z"), 12.0, depth=8.0, through=False)


def _through_with_cbore():
    return HoleFeature(Frame((0, 0, 10), "z"), 20.0, depth=None, through=True, cbore=(32.0, 1.5))


class TestTheBlindHoleGate:
    """#875's acceptance criterion, run over the whole power set rather than a chosen case."""

    def test_a_blind_hole_never_prints_through_under_any_suppression(self):
        group = _group(_blind())
        suppressible = sorted({(pd.param.kind, pd.param.role) for pd in group.dims})
        assert suppressible, "the fixture must carry parameters, or this proves nothing"

        printed = refused = silent = 0
        for r in range(len(suppressible) + 1):
            for combo in itertools.combinations(suppressible, r):
                marked = _suppress(group, *combo) if combo else group
                try:
                    spec = hole_callout_spec(marked)
                except ValueError:
                    refused += 1  # an orphaned term — an authoring error, and not a THRU
                    continue
                if spec is None:
                    silent += 1  # the whole callout is suppressed — nothing is printed
                    continue
                printed += 1
                assert spec["through"] is False, (
                    f"suppressing {list(combo)} made a blind hole read as THRU — a renderer "
                    "inferred an engineering fact from a missing parameter"
                )
        assert printed, "every combination refused or fell silent; the gate tested nothing"
        assert refused, "no combination orphaned a term — the segment rule is not being exercised"
        assert silent, "no combination suppressed the callout entirely"

    def test_suppressing_the_depth_does_not_change_the_fact(self):
        """The specific inversion #868 fixed, now under deliberate suppression rather than the
        planner's own choice: `through` comes off the feature, and the depth is display only."""
        group = _suppress(_group(_blind()), ("depth", "bore"))
        spec = hole_callout_spec(group)
        assert spec["through"] is False
        assert spec["depth"] is None


class TestSuppressionIsHonoured:
    def test_a_suppressed_counterbore_does_not_print(self):
        group = _suppress(
            _group(_through_with_cbore()), ("diameter", "counterbore"), ("depth", "counterbore")
        )
        spec = hole_callout_spec(group)
        assert spec["cbore_dia"] is None and spec["cbore_depth"] is None
        assert spec["diameter"] == 20.0, "the head is unaffected by suppressing a segment"

    def test_the_group_keeps_its_data(self):
        """ "Marks, not filters": the engineering data survives, so a later pass — the emitter,
        a lint, `Drawing.drop` — can still see what was suppressed and why."""
        group = _suppress(
            _group(_through_with_cbore()), ("diameter", "counterbore"), ("depth", "counterbore")
        )
        marked = [pd for pd in group.dims if pd.suppressed]
        assert {pd.param.role for pd in marked} == {"counterbore"}
        assert all(pd.reason for pd in marked), "a mark without a reason cannot be explained"
        assert all(pd.param.value is not None for pd in marked), "the value is retained"


class TestSegmentsAreAtomic:
    """A segment is one term: `⌴ ⌀32 ↓ 1.5` needs both halves, and half of it is not a shorter
    callout but a different and wrong one.

    The first version of the dependency rule guarded only the bore head, which left this case
    doing exactly the silent discard the rule exists to forbid: suppressing `counterbore.diameter`
    while its depth survived produced `cbore_dia=None, cbore_depth=1.5`, and the renderer quietly
    dropped the whole counterbore — authored intent gone, no error, no mark on the drawing.
    """

    def test_suppressing_a_segments_head_orphans_its_dependents(self):
        group = _suppress(_group(_through_with_cbore()), ("diameter", "counterbore"))
        with pytest.raises(ValueError, match="nothing to attach to"):
            hole_callout_spec(group)

    def test_suppressing_a_dependent_is_legitimate(self):
        """The asymmetry, and the correction to an earlier draft of this file that demanded a
        raise here too: `⌴ ⌀32` is a readable counterbore with unstated depth, whereas
        `⌴ ↓ 1.5` is not a counterbore at all. The dependency runs one way."""
        group = _suppress(_group(_through_with_cbore()), ("depth", "counterbore"))
        spec = hole_callout_spec(group)
        assert spec["cbore_dia"] == 32.0
        assert spec["cbore_depth"] is None

    def test_the_message_names_the_orphan(self):
        group = _suppress(_group(_through_with_cbore()), ("diameter", "counterbore"))
        with pytest.raises(ValueError, match="counterbore.depth"):
            hole_callout_spec(group)

    def test_a_countersink_is_atomic_too(self):
        feature = HoleFeature(
            Frame((0, 0, 10), "z"), 20.0, depth=None, through=True, csink=(30.0, 90.0)
        )
        group = _suppress(_group(feature), ("diameter", "countersink"))
        with pytest.raises(ValueError, match="nothing to attach to"):
            hole_callout_spec(group)

    def test_a_whole_segment_may_be_suppressed(self):
        """The contrast: nothing is orphaned, so nothing raises and the callout simply loses
        that term. This is what suppression is FOR."""
        group = _suppress(
            _group(_through_with_cbore()), ("diameter", "counterbore"), ("depth", "counterbore")
        )
        spec = hole_callout_spec(group)
        assert spec["cbore_dia"] is None and spec["cbore_depth"] is None
        assert spec["diameter"] == 20.0


class TestPrecedenceRespectsSuppression:
    """The counterbore/spotface roles are a FALLBACK CHAIN, so suppression has to be applied
    per role before the precedence between them — otherwise a suppressed counterbore swallows
    the spotface that should have taken its place (#920 review). The first draft resolved
    precedence first and nulled the winner, which dropped an unsuppressed spotface in silence.
    """

    @staticmethod
    def _both():
        return HoleFeature(
            Frame((0, 0, 10), "z"),
            20.0,
            depth=None,
            through=True,
            cbore=(32.0, 1.5),
            spotface=(35.0, 0.5),
        )

    def test_a_suppressed_counterbore_falls_through_to_the_spotface(self):
        group = _group(self._both())
        assert hole_callout_spec(group)["cbore_dia"] == 32.0, "counterbore wins when both print"
        marked = _suppress(group, ("diameter", "counterbore"), ("depth", "counterbore"))
        spec = hole_callout_spec(marked)
        assert spec["cbore_dia"] == 35.0, "the spotface is the documented fallback"
        assert spec["cbore_depth"] == 0.5

    def test_suppressing_both_leaves_neither(self):
        group = _suppress(
            _group(self._both()),
            ("diameter", "counterbore"),
            ("depth", "counterbore"),
            ("diameter", "spotface"),
            ("depth", "spotface"),
        )
        spec = hole_callout_spec(group)
        assert spec["cbore_dia"] is None and spec["cbore_depth"] is None


class TestTheRecessIsOneSegment:
    """`counterbore` takes precedence and `spotface` is the fallback — but the choice is made
    once, for the SEGMENT, not independently for the ⌀ and the depth.

    Reading the two terms through separate fallbacks let a suppressed `counterbore.depth` pair
    the counterbore's ⌀32 with the spotface's 0.5 depth: a recess present on neither feature.
    That is a *wrong* drawing rather than a missing one, which is the worse failure — found by
    a 10,500-outcome review sweep, and 168 accepted combinations produced it.
    """

    @staticmethod
    def _both():
        return HoleFeature(
            Frame((0, 0, 10), "z"),
            20.0,
            depth=None,
            through=True,
            cbore=(32.0, 1.5),
            spotface=(35.0, 0.5),
        )

    def test_suppressing_the_winners_depth_does_not_borrow_the_fallbacks(self):
        group = _suppress(_group(self._both()), ("depth", "counterbore"))
        spec = hole_callout_spec(group)
        assert spec["cbore_dia"] == 32.0, "the counterbore still wins — its head is unsuppressed"
        assert spec["cbore_depth"] is None, "and it has no depth, rather than the spotface's"

    def test_suppressing_a_shadowed_spotface_is_a_no_op(self):
        """The counterbore wins, so the spotface is never rendered — suppressing part of it
        orphans nothing and must not raise. Validating every role regardless produced a
        spurious error about a term the drawing never carried (#920 review)."""
        group = _group(self._both())
        before = hole_callout_spec(group)
        marked = _suppress(group, ("diameter", "spotface"))
        assert hole_callout_spec(marked) == before

    def test_but_a_shadowed_spotface_still_matters_once_it_is_uncovered(self):
        """The contrast that keeps the exemption honest: shadowing is conditional on the
        counterbore printing, so once its head goes the spotface is live again and its own
        half-suppression raises."""
        group = _suppress(
            _group(self._both()),
            ("diameter", "counterbore"),
            ("depth", "counterbore"),
            ("diameter", "spotface"),
        )
        with pytest.raises(ValueError, match="spotface.depth"):
            hole_callout_spec(group)

    def test_terms_always_come_from_one_role(self):
        """The general property, over the whole suppression power set of both roles: whatever
        the callout prints must exist together on ONE of the two features."""
        feature = self._both()
        group = _group(feature)
        roles = [
            ("diameter", "counterbore"),
            ("depth", "counterbore"),
            ("diameter", "spotface"),
            ("depth", "spotface"),
        ]
        legitimate = {(32.0, 1.5), (32.0, None), (35.0, 0.5), (35.0, None), (None, None)}
        seen = set()
        for r in range(len(roles) + 1):
            for combo in itertools.combinations(roles, r):
                marked = _suppress(group, *combo) if combo else group
                try:
                    spec = hole_callout_spec(marked)
                except ValueError:
                    continue
                if spec is None:
                    continue
                pair = (spec["cbore_dia"], spec["cbore_depth"])
                assert pair in legitimate, (
                    f"suppressing {list(combo)} produced recess {pair}, which exists on neither "
                    "the counterbore (32.0, 1.5) nor the spotface (35.0, 0.5)"
                )
                seen.add(pair)
        assert {(32.0, 1.5), (35.0, 0.5)} <= seen, "the sweep never exercised either role intact"


class TestDependentsThatAreNotDimensions:
    """Not everything riding the callout string is a dimension parameter. A thread spec and a
    pattern's `4× … EQ SP ON ⌀50 BC` live on the FEATURE, so they survive any amount of
    parameter suppression — and would then be discarded in silence when the head goes, which is
    the exact outcome the head rule exists to prevent (#920 review)."""

    def test_suppressing_the_head_of_a_threaded_hole_raises(self):
        threaded = HoleFeature(
            Frame((0, 0, 10), "z"), 10.0, depth=None, through=True, thread="M10x1.5"
        )
        group = _suppress(_group(threaded), ("diameter", "bore"))
        with pytest.raises(ValueError, match="M10x1.5"):
            hole_callout_spec(group)

    def test_suppressing_the_head_of_a_pattern_raises(self):
        from draftwright.model import PatternFeature

        member = HoleFeature(Frame((0, 0, 10), "z"), 6.0, depth=None, through=True)
        pattern = PatternFeature(
            Frame((0, 0, 10), "z"), member=member, count=4, pattern="bolt_circle", bcd=50.0
        )
        group = _suppress(_group(pattern), ("diameter", "bore"))
        with pytest.raises(ValueError, match="4× multiplier"):
            hole_callout_spec(group)

    def test_a_grouped_plain_hole_counts_too(self):
        """`4× ⌀6 THRU` is a plain `HoleFeature` with a count, not a `PatternFeature` — the
        multiplier is a dependent of the head for both kinds. Guarding only patterns silently
        discarded the `4×` (#920 review)."""
        grouped = HoleFeature(Frame((0, 0, 10), "z"), 6.0, depth=None, through=True, count=4)
        group = _suppress(_group(grouped), ("diameter", "bore"))
        with pytest.raises(ValueError, match="4× multiplier"):
            hole_callout_spec(group)

    def test_a_suppressed_bolt_circle_diameter_does_not_print(self):
        """The BCD is an addressable dimension (`bolt_circle.diameter`). Reading `feat.bcd`
        unconditionally bypassed its mark, so `EQ SP ON ø50 BC` printed on a callout that had
        asked not to carry it — a 38.4 mm callout where 16.3 mm was intended."""
        from draftwright.model import PatternFeature

        member = HoleFeature(Frame((0, 0, 10), "z"), 6.0, depth=None, through=True)
        pattern = PatternFeature(
            Frame((0, 0, 10), "z"), member=member, count=4, pattern="bolt_circle", bcd=50.0
        )
        plain = _group(pattern)
        assert "BC" in (hole_callout_spec(plain)["suffix"] or "")
        marked = _suppress(plain, ("diameter", "bolt_circle"))
        assert "BC" not in (hole_callout_spec(marked)["suffix"] or "")

    @pytest.mark.parametrize(
        ("kind", "kwargs", "expected"),
        [
            ("bolt_circle", {"pattern": "bolt_circle", "bcd": 50.0}, "EQ SP ON"),
            ("grid", {"pattern": "grid", "rows": 1, "cols": 1}, r"\(1×1\)"),
        ],
    )
    def test_a_one_member_pattern_suffix_is_a_dependent_too(self, kind, kwargs, expected):
        """`count=1` is a legal declaration, and with it the multiplier check never fires — so
        a one-member bolt circle silently lost its `EQ SP ON ø50 BC` when the head went. Every
        earlier fixture used `count > 1`, where the multiplier masked the missing dependency
        (#920 review). The SUFFIX is a dependent in its own right."""
        from draftwright.model import PatternFeature

        member = HoleFeature(Frame((0, 0, 10), "z"), 6.0, depth=None, through=True)
        pattern = PatternFeature(Frame((0, 0, 10), "z"), member=member, count=1, **kwargs)
        plain = _group(pattern)
        assert hole_callout_spec(plain)["suffix"], "the fixture must carry a suffix to lose"

        group = _suppress(plain, ("diameter", "bore"))
        with pytest.raises(ValueError, match=expected):
            hole_callout_spec(group)

    def test_a_plain_unthreaded_hole_still_suppresses_silently(self):
        """The contrast, so the rule is not read as "the head may never be suppressed"."""
        plain = HoleFeature(Frame((0, 0, 10), "z"), 12.0, depth=None, through=True)
        group = _suppress(_group(plain), ("diameter", "bore"))
        assert hole_callout_spec(group) is None


class TestTheEstimatorAgreesWithTheRenderer:
    """The page/scale estimator reserves strip width for a callout before any geometry exists
    (#261's estimator/render agreement). It used to re-derive the callout itself, and the two
    drifted: the estimator inferred `THRU` from a missing depth — the inference #868 removed
    from the renderer — and ignored `suppressed` entirely. A blind hole with its depth
    suppressed was reserved 12.8 mm and rendered at 6.1 mm.

    Both now read one spec, so the classes of drift are structurally gone rather than fixed
    twice. These tests pin the two that were observed.
    """

    @staticmethod
    def _estimate(group):
        from draftwright.compose import _est_planned_bore_callout_width

        return _est_planned_bore_callout_width(
            [group], font_size=2.5, pad_around_text=0.5, draft=None
        )

    def test_a_blind_hole_is_not_reserved_room_for_THRU(self):
        plain = _group(_blind())
        marked = _suppress(plain, ("depth", "bore"))
        assert hole_callout_spec(marked)["through"] is False
        # THRU is four glyphs; reserving them for a hole that prints none is the drift.
        assert self._estimate(marked) < self._estimate(plain)

    def test_a_suppressed_counterbore_is_not_reserved(self):
        plain = _group(_through_with_cbore())
        marked = _suppress(plain, ("diameter", "counterbore"), ("depth", "counterbore"))
        assert self._estimate(marked) < self._estimate(plain)

    def test_the_estimator_sees_the_same_spec_the_renderer_does(self):
        """The structural claim, not just its two symptoms: one reading, two consumers."""
        import draftwright.compose as compose
        from draftwright.model.callout import hole_callout_spec as canonical

        assert compose.hole_callout_spec is canonical


class TestFurnitureIsSizedByTheFeature:
    """Geometry derived from a hole's physical size must not shrink when its dimension is
    suppressed — the governing rule applied to geometry rather than to text.

    This was introduced BY the #875 fix and caught in review: making `_first` suppression-aware
    silently changed `render_centermarks`, which used it to read the bore ⌀. A suppressed ⌀20
    collapsed from a 42 mm centre mark to the 2.5 mm floor. Suppression expresses "do not print
    this value", never "this hole is smaller".
    """

    class _Dwg:
        scale = 2.0
        draft = None

        def at(self, view, *loc):
            return (0.0, 0.0, 0.0)

    @staticmethod
    def _mark_widths(group):
        from draftwright.annotations.from_model import render_centermarks

        widths = []

        class _Ctx:
            def place(self, obj, name, **kw):
                bbox = obj.bounding_box()
                widths.append(round(bbox.max.X - bbox.min.X, 2))

        render_centermarks(TestFurnitureIsSizedByTheFeature._Dwg(), [group], ctx=_Ctx())
        return widths

    def test_a_centre_mark_keeps_its_size_when_the_bore_is_suppressed(self):
        feature = HoleFeature(Frame((0, 0, 10), "z"), 20.0, depth=None, through=True)
        plain = _group(feature)
        marked = _suppress(plain, ("diameter", "bore"))
        assert self._mark_widths(plain) == self._mark_widths(marked)

    def test_and_the_size_actually_tracks_the_diameter(self):
        """Otherwise the test above would pass on any constant, including the 2.5 mm floor that
        was the bug."""
        small = self._mark_widths(
            _group(HoleFeature(Frame((0, 0, 10), "z"), 4.0, depth=None, through=True))
        )
        large = self._mark_widths(
            _group(HoleFeature(Frame((0, 0, 10), "z"), 20.0, depth=None, through=True))
        )
        assert small < large


class TestTheHeadIsADependency:
    def test_suppressing_the_head_alone_raises_and_names_the_orphan(self):
        group = _suppress(_group(_through_with_cbore()), ("diameter", "bore"))
        with pytest.raises(ValueError, match="counterbore"):
            hole_callout_spec(group)

    def test_the_message_says_what_to_do(self):
        group = _suppress(_group(_through_with_cbore()), ("diameter", "bore"))
        with pytest.raises(ValueError, match="suppress those segments too, or keep the bore"):
            hole_callout_spec(group)

    def test_suppressing_head_and_dependents_together_is_coherent(self):
        """Not an error — the author asked for no callout at all, which is a thing to want."""
        group = _suppress(
            _group(_through_with_cbore()),
            ("diameter", "bore"),
            ("diameter", "counterbore"),
            ("depth", "counterbore"),
        )
        assert hole_callout_spec(group) is None

    def test_a_plain_through_hole_may_suppress_its_head(self):
        """With no dependent segment there is no dependency to violate, so the callout simply
        does not print. The rule is about orphaning, not about the head being sacred.

        A THROUGH hole, deliberately: the first version of this test used a blind one and so
        enshrined a bug — a blind hole carries a `bore.depth`, which suppressing the ⌀ alone
        left orphaned and silently discarded (PR #920 review). The case below covers that."""
        plain = HoleFeature(Frame((0, 0, 10), "z"), 12.0, depth=None, through=True)
        group = _suppress(_group(plain), ("diameter", "bore"))
        assert hole_callout_spec(group) is None

    def test_suppressing_a_blind_bore_diameter_orphans_its_depth(self):
        """`⌀12 ↓ 8` is one term. Suppressing the ⌀ while the depth survives is the same
        half-segment error the counterbore case raises on — the bore is not exempt from its
        own rule just because it also heads the string."""
        group = _suppress(_group(_blind()), ("diameter", "bore"))
        with pytest.raises(ValueError, match="nothing to attach to"):
            hole_callout_spec(group)

    def test_a_blind_hole_may_suppress_its_whole_bore_segment(self):
        group = _suppress(_group(_blind()), ("diameter", "bore"), ("depth", "bore"))
        assert hole_callout_spec(group) is None


def test_suppression_is_now_reachable_from_a_user_path():
    """The #875 guard, discharged.

    It was written to FAIL the moment a real authoring path could suppress a hole
    parameter — "which is exactly when end-to-end cases become possible and should be
    written". #876 landed that path (a measurement outside an authored set is marked
    suppressed), so the guard fired as designed and is replaced by its own success
    condition. The end-to-end cases live in `test_add_dimension.py::TestOmissionReachesTheDrawing`.
    """
    from build123d import Box, Cylinder, Pos

    from draftwright import Sheet

    part = Box(90, 60, 20) - Pos(0, 0, 12) * Cylinder(6, 20)
    sheet = Sheet(part, title="T", number="N")
    hole = sheet.hole(diameter=12, at=(0, 0, 20), axis="z").depth(8)
    # `bore.diameter`, not the bare role: naming the ROLE would match the depth too and omit
    # nothing, which is a real distinction in the selector and an easy way to write a test
    # that proves nothing.
    sheet.dimension(hole, "bore.diameter")
    groups = plan_dimensions(sheet.model())
    assert [pd for g in groups for pd in g.dims if pd.suppressed], (
        "an authored set must suppress by omission — if this stops holding, #876 regressed"
    )
