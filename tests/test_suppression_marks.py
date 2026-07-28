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

        checked = 0
        for r in range(len(suppressible) + 1):
            for combo in itertools.combinations(suppressible, r):
                marked = _suppress(group, *combo) if combo else group
                spec = hole_callout_spec(marked)
                if spec is None:
                    continue  # the bore ⌀ itself is suppressed — nothing is printed at all
                checked += 1
                assert spec["through"] is False, (
                    f"suppressing {list(combo)} made a blind hole read as THRU — a renderer "
                    "inferred an engineering fact from a missing parameter"
                )
        assert checked, "every combination suppressed the callout; the gate tested nothing"

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

    def test_a_plain_hole_may_suppress_its_head(self):
        """With no dependent segment there is no dependency to violate, so the callout simply
        does not print. The rule is about orphaning, not about the head being sacred."""
        group = _suppress(_group(_blind()), ("diameter", "bore"))
        assert hole_callout_spec(group) is None
