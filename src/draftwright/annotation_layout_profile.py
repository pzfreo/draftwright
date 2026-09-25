"""Build-scoped options for the alternative annotation layout.

The comparison runner used to mutate environment variables and monkeypatch analysis
to construct a candidate.  A build-scoped profile lets the same engine construct
that layout for library callers without changing another build in the process.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from draftwright.compose import StripDepths

Route = tuple[str, str]


def annotation_layout_policy(value: str) -> Literal["baseline", "best"]:
    if value == "baseline":
        return "baseline"
    if value == "best":
        return "best"
    raise ValueError(f"annotation_layout must be 'baseline' or 'best', got {value!r}")


DEFAULT_CAPPED_ROUTES: frozenset[Route] = frozenset(
    {
        ("front", "right"),
        ("plan", "right"),
        ("front", "left"),
        ("plan", "left"),
        ("plan", "above"),
        ("front", "above"),
        ("front", "below"),
        ("plan", "below"),
        ("side", "above"),
        ("side", "below"),
        ("side", "right"),
        ("rear", "right"),
    }
)


@dataclass(frozen=True)
class AnnotationLayoutProfile:
    """One candidate's layout choices, scoped to one synchronous build."""

    arrangement: str | None = None
    corridor_scale: float | None = None
    capped_routes: frozenset[Route] = frozenset()
    exterior_dimensions: bool = False
    crossing_recovery: bool = False
    plan_x_below: bool = False
    iso_growth: bool = False
    scheme_lanes: bool = False
    lateral_tier_reuse: bool = False


_CURRENT: ContextVar[AnnotationLayoutProfile | None] = ContextVar(
    "draftwright_annotation_layout_profile", default=None
)


def current_layout_profile() -> AnnotationLayoutProfile | None:
    return _CURRENT.get()


def layout_flag(name: str, experimental_env: str) -> bool:
    """Use an explicit build profile, retaining old experimental test switches."""

    profile = current_layout_profile()
    return (
        bool(getattr(profile, name))
        if profile is not None
        else os.environ.get(experimental_env) == "1"
    )


@contextmanager
def use_layout_profile(profile: AnnotationLayoutProfile) -> Iterator[None]:
    token = _CURRENT.set(profile)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def cap_planned_strips(strips: StripDepths) -> StripDepths:
    """Use planned lanes to reduce selected legacy reservations at a fixed scale."""

    profile = current_layout_profile()
    if profile is None or profile.corridor_scale is None or not profile.capped_routes:
        return strips
    planned = strips.planned_corridor_depths(profile.corridor_scale)

    def cap(current: float, *routes: Route) -> float:
        if not any(route in profile.capped_routes for route in routes):
            return current
        depths = [planned[route] for route in routes if route in planned]
        return min(current, max(depths)) if current > 0 and depths else current

    return replace(
        strips,
        right=cap(strips.right, ("front", "right"), ("plan", "right")),
        left=cap(strips.left, ("front", "left"), ("plan", "left")),
        top=cap(strips.top, ("plan", "above")),
        fv_top=cap(strips.fv_top, ("front", "above")),
        fv_bottom=cap(strips.fv_bottom, ("front", "below")),
        pv_authored_top=cap(strips.pv_authored_top, ("plan", "above")),
        pv_bottom=cap(strips.pv_bottom, ("plan", "below")),
        sv_top=cap(strips.sv_top, ("side", "above")),
        sv_bottom=cap(strips.sv_bottom, ("side", "below")),
        sv_right=cap(strips.sv_right, ("side", "right")),
        rv_right=cap(strips.rv_right, ("rear", "right")),
    )
