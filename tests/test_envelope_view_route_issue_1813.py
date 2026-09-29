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
        (None, None, "front"),
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
        "plan": SimpleNamespace(
            below=None if plan_depth is None else below(plan_depth), above=None
        ),
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


def test_deferred_envelope_retry_resolves_the_live_strip_binding(monkeypatch):
    """A binding replaced after registration still controls the post-drain retry."""
    zone = SimpleNamespace(below=object(), above=object())
    monkeypatch.setattr(
        from_model,
        "layout_frame",
        lambda _a: SimpleNamespace(zones=lambda _view: zone),
    )
    candidates = []
    monkeypatch.setattr(
        from_model,
        "register_corridor",
        lambda _ctx, _key, _strip, _view, _axis, _tier, candidate: candidates.append(candidate),
    )
    calls = []

    def retry_binding(which):
        def place(*args, **kwargs):
            assert kwargs["trace_label"] == "m_env_width_above_fallthrough"
            calls.append(which)
            return []

        return place

    monkeypatch.setattr(from_model, "place_strip_candidates", retry_binding("early"))
    extent = SimpleNamespace(
        view="plan",
        span=((0.0, 0.0, 0.0), (100.0, 0.0, 0.0)),
        value_text="100",
        tolerance=None,
        id="width.length",
    )
    env = SimpleNamespace(dim=lambda role: extent if role == "width" else None, ref=object())
    plan = SimpleNamespace(of_kind=lambda kind: (env,) if kind == "envelope" else ())
    drawing = SimpleNamespace(
        views={"plan": object()},
        at=lambda _view, x, y, _z: (x, y, 0.0),
        view_bounds=lambda _view: (0.0, 0.0, 100.0, 100.0),
        draft=Draft(),
    )
    issues = []
    ctx = SimpleNamespace(
        post_drain=[],
        interior_dimensions=None,
        exterior_dimensions_only=True,
        trace=None,
        record_issue=lambda *args, **kwargs: issues.append((args, kwargs)),
    )

    assert (
        from_model.render_envelope(drawing, plan, SimpleNamespace(arrangement="columns"), ctx=ctx)
        == 1
    )
    assert len(candidates) == 1
    assert candidates[0].name == "m_env_width"
    candidates[0].on_drop("m_env_width")
    assert len(ctx.post_drain) == 1
    monkeypatch.setattr(from_model, "place_strip_candidates", retry_binding("late"))
    ctx.post_drain[0]()

    assert calls == ["late"]
    assert issues == []
