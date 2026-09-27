"""A mandatory overall extent can use another view when its first strip has no tier."""

from types import SimpleNamespace

import pytest
from build123d_drafting.helpers import Draft

from draftwright._core import _SLOT_DIM_WIDTH
from draftwright.annotations import from_model

_GAP = 10.0


@pytest.mark.parametrize(
    ("plan_depth", "authored_view", "expected_view"),
    [
        (_GAP + _SLOT_DIM_WIDTH, None, "front"),
        (_GAP + _SLOT_DIM_WIDTH + 4, None, "plan"),
        (_GAP + _SLOT_DIM_WIDTH, "plan", "plan"),
    ],
)
def test_width_routes_only_when_plan_has_no_tier_and_view_is_not_authored(
    monkeypatch, plan_depth, authored_view, expected_view
):
    def below(depth):
        return SimpleNamespace(
            anchor=100.0,
            outer_limit=100.0 - depth,
            direction=-1,
            gap=_GAP,
            spacing=2.5,
        )

    zones = {
        "plan": SimpleNamespace(below=below(plan_depth), above=None),
        "front": SimpleNamespace(below=below(60.0), above=None),
    }
    monkeypatch.setattr(
        from_model, "layout_frame", lambda _a: SimpleNamespace(zones=lambda view: zones[view])
    )
    registered = []
    monkeypatch.setattr(
        from_model,
        "register_corridor",
        lambda _ctx, key, _strip, view, _axis, _tier, candidate: registered.append(
            (key, view, candidate.name)
        ),
    )
    extent = SimpleNamespace(
        view=authored_view,
        span=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
        value_text="100",
        tolerance=None,
        id="width.length",
    )
    env = SimpleNamespace(dim=lambda role: extent if role == "width" else None, ref=object())
    plan = SimpleNamespace(of_kind=lambda kind: (env,) if kind == "envelope" else ())
    drawing = SimpleNamespace(
        views={"plan": object(), "front": object()},
        at=lambda _view, x, y, _z: (x, y, 0.0),
        draft=Draft(),
    )

    assert (
        from_model.render_envelope(
            drawing, plan, SimpleNamespace(arrangement="columns"), ctx=object()
        )
        == 1
    )
    assert registered == [((expected_view, "below"), expected_view, "m_env_width")]
