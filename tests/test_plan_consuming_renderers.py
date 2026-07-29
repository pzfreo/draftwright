"""A renderer fed the plan must honour the plan's suppression.

`PlannedDimension.suppressed` is the planner's decision that a measurement is not
drawn — a square footprint's redundant depth, a turned part's doubled extent, or a
measurement omitted from an authored set (ADR 0016 / #876). Marking is only half the
mechanism: it means nothing unless the renderer *reads* it.

Three review rounds on #921 each found renderers that did not. The first found the
authored set never reaching the planner at all; the second found `render_height_ladder`
and `render_step_positions` rebuilding their marks from the feature and the bounding
box; the third found the whole turned family — `render_diameters`,
`render_boss_diameters`, `render_step_lengths`, `render_rotational` — selecting
parameters with no suppression check, so authoring one step length still drew every
diameter on the part.

Each was caught by a fixture that happened to cover it, which is the wrong way to find
the fourth: round two's fixtures were prismatic, so they were structurally incapable of
seeing round three. Hence two guards, in order of strength:

1. **Behavioural** (`TestNothingSurvivesATotallySuppressedPlan`) — suppress *everything*
   the planner produces and assert no dimension reaches the page. This tests the
   property itself, across whatever renderers the part exercises, and cannot be
   satisfied by a renderer that merely mentions `suppressed`.
2. **Structural** (`TestEveryPlanFedRendererReadsTheFlag`) — a cheap AST check that each
   plan-consuming renderer at least references suppression. This is a smoke alarm, not
   a proof: it cannot tell a live read from a dead one (a round-4 reviewer demonstrated
   both `if False: ... pd.suppressed` and an ignored helper call satisfying it). Its
   value is catching a NEW renderer whose feature kind no fixture above covers.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from build123d import Box, Cylinder, Pos, Rot

from draftwright import Sheet

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "draftwright"
_FROM_MODEL = _SRC / "annotations" / "from_model.py"


def _stepped_block():
    return Box(120, 60, 15) + Pos(-20, 0, 15) * Box(80, 60, 15) + Pos(-40, 0, 30) * Box(40, 60, 15)


def _turned_shaft():
    return Rot(0, 90, 0) * Cylinder(4, 20) + Pos(15, 0, 0) * Rot(0, 90, 0) * Cylinder(6, 10)


def _drilled_plate():
    return Box(120, 80, 25) - Pos(-30, 0, 17) * Cylinder(5, 20) - Pos(30, 20, 17) * Cylinder(4, 20)


# Annotations that are furniture rather than dimensioning, and so legitimately survive a
# plan in which every measurement is suppressed. Each needs a reason it prints no value —
# "it kept failing otherwise" is how the defects above got in.
_FURNITURE = (
    "centerline",  # shows where an axis is; carries no number
    "m_cm",  # centre marks, sized off the hole they mark (#875)
    "m_locx",  # location ladders have no addressable identity yet (#883), so an
    "m_locy",  # authored set neither names nor suppresses them
    "note_",  # the ISO NTS note
    "title_block",
    "section_",  # section arrows/labels — furniture for a view, not a measurement
    "hatch",
)


class TestNothingSurvivesATotallySuppressedPlan:
    """The property itself: if the planner suppresses everything, the page carries no
    dimension. Patching the plan rather than authoring a set reaches renderers no
    authored fixture would — it does not care which feature kinds the part has, so a
    renderer family added later is covered by whichever part exercises it.

    `Sheet.from_part` on purpose, not a bare `Sheet(part).auto_dimensions()`: the bare
    form declares no features, so the plan is empty and nothing drawn can be traced to
    it. Only a DETECTED model puts every measurement on the page under the planner's
    authority, which is what makes "suppress it all" a meaningful instruction."""

    @pytest.fixture
    def suppress_everything(self, monkeypatch):
        from dataclasses import replace

        from draftwright.model import planner

        real = planner.plan_dimensions

        def _all_suppressed(model):
            return [
                replace(
                    g,
                    units=tuple(
                        replace(
                            u,
                            members=tuple(
                                replace(m, suppressed=True, reason="test: total suppression")
                                for m in u.members
                            ),
                        )
                        for u in g.units
                    ),
                )
                for g in real(model)
            ]

        # Patch at every import site: the renderers take `groups` from the orchestrator,
        # which resolves `plan_dimensions` through its own module reference.
        for mod in ("draftwright.model.planner", "draftwright.model", "draftwright.drawing"):
            monkeypatch.setattr(f"{mod}.plan_dimensions", _all_suppressed, raising=False)
        monkeypatch.setattr(
            "draftwright.annotations.orchestrator.plan_dimensions", _all_suppressed, raising=False
        )
        return _all_suppressed

    @pytest.mark.parametrize(
        "make_part",
        [_stepped_block, _turned_shaft, _drilled_plate],
        ids=["stepped", "turned", "drilled"],
    )
    def test_no_dimension_reaches_the_page(self, make_part, suppress_everything):
        part = make_part()
        dwg = Sheet.from_part(part).build()
        drawn = {n for n, _ in dwg.iter_annotations()}
        leaked = sorted(n for n in drawn if not n.startswith(_FURNITURE))

        # The ONE documented exception, pinned rather than waved through: a model with no
        # `EnvelopeFeature` (a round body, an all-`step` turned shaft) still gets an
        # overall height off the bounding box, and no parameter anywhere names it — so no
        # amount of planner suppression can reach it. An AUTHORED build refuses that
        # fallback outright (`render_height_ladder`); an automatic one keeps it, because
        # dropping it would leave those parts with no overall height at all. Narrowing
        # this to `dim_height` on an envelope-less model means the exception cannot grow
        # into cover for the next renderer that ignores the plan.
        has_envelope = any(f.kind == "envelope" for f in dwg.model().features)
        allowed = [] if has_envelope else ["dim_height"]
        assert leaked == [n for n in allowed if n in drawn], (
            f"{leaked} reached the page from a plan in which every measurement is marked "
            "suppressed. Some renderer is rebuilding its marks from the feature or the "
            "bounding box instead of reading the plan — the #921 defect, three times over."
        )

    @pytest.mark.parametrize(
        "make_part",
        [_stepped_block, _turned_shaft, _drilled_plate],
        ids=["stepped", "turned", "drilled"],
    )
    def test_the_same_part_is_richly_dimensioned_without_the_patch(self, make_part):
        """The anchor. Without it the test above passes for a part that draws nothing
        anyway, which is how a guard quietly stops guarding."""
        part = make_part()
        drawn = {n for n, _ in Sheet.from_part(part).build().iter_annotations()}
        assert [n for n in drawn if not n.startswith(_FURNITURE)], (
            "this fixture must produce dimensions to be worth suppressing"
        )


# --- the cheap structural smoke alarm ------------------------------------------------

# Helpers that encapsulate a suppression reading.
_SANCTIONED = {"env_dim_placed", "set_dim_placed"}

# Renderers that legitimately read the FEATURE rather than the planned dimension, with
# the reason each is not a dimension.
_NOT_DIMENSIONS = {
    # Centre marks are furniture sizing themselves off the hole they mark. #875 made
    # them read `hole.diameter` on purpose: sized from the suppressible parameter, a
    # suppressed ⌀20 collapsed its mark from 42 mm to the 2.5 mm floor. A centre mark
    # shows where an axis is; it prints no value, so there is nothing to suppress.
    "render_centermarks",
}


def _plan_consuming_renderers() -> dict[str, ast.FunctionDef]:
    """Every `render_*` in `from_model.py` that is handed the planned groups."""
    tree = ast.parse(_FROM_MODEL.read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("render_"):
            continue
        args = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        if "groups" in args:
            out[node.name] = node
    return out


def _consults_suppression(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr == "suppressed":
            return True
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name in _SANCTIONED:
                return True
    return False


class TestEveryPlanFedRendererReadsTheFlag:
    def test_the_guard_is_looking_at_something(self):
        """A ratchet that matches nothing passes forever. Pin the population so a rename
        or a moved module fails loudly instead of quietly guarding an empty set."""
        found = _plan_consuming_renderers()
        assert len(found) >= 12, f"expected the renderer family, found {sorted(found)}"
        assert "render_diameters" in found and "render_rotational" in found

    def test_every_plan_fed_renderer_mentions_suppression(self):
        offenders = sorted(
            name
            for name, fn in _plan_consuming_renderers().items()
            if name not in _NOT_DIMENSIONS and not _consults_suppression(fn)
        )
        assert not offenders, (
            f"{offenders} take the planned groups but never consult `suppressed`. A "
            "planner decision the renderer ignores is not a decision: the plan will say a "
            "dimension is omitted while the drawing carries it. Read `pd.suppressed` (or "
            f"a sanctioned helper: {sorted(_SANCTIONED)}), or add the renderer to "
            "_NOT_DIMENSIONS with a reason it prints no value."
        )

    def test_the_exemptions_still_exist(self):
        """An exemption for a deleted function silently widens the guard."""
        found = _plan_consuming_renderers()
        assert not (_NOT_DIMENSIONS - set(found)), "stale exemption in _NOT_DIMENSIONS"
