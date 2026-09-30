"""Resolution of a caller-requested drawing scale."""

from __future__ import annotations

import os
import warnings
from collections.abc import Callable, Sequence
from typing import Literal

from draftwright._warnings import ScaleCompletenessWarning
from draftwright.annotation_layout_profile import current_layout_profile
from draftwright.build_policy import (
    ScaleIncompatibilityError,
    _automatic_candidate_rejection,
    _scale_attempt,
    _scale_decision,
)
from draftwright.drawing import Drawing
from draftwright.drawing_diagnostics import discard_finished_build_lint
from draftwright.linting import LintIssue
from draftwright.view_plan import ARRANGEMENTS, third_angle_view_names


def resolve_explicit_scale(
    scale: float,
    scale_policy: Literal["strict", "fallback", "permissive"],
    *,
    views_are_automatic: bool,
    _views: tuple[str, ...] | None,
    _SCALES: Sequence[float],
    _build: Callable[..., Drawing],
    _principal_names: Callable[[Drawing], tuple[str, ...]],
    _automatic_assessment,
    _absent_view_owners,
    scale_blockers_for,
    finish_annotation_layout: Callable[[Drawing], Drawing],
) -> Drawing:
    """Resolve the requested rung, view veto, and bounded fallback ladder."""
    requested_scale = float(scale)
    automatic_view_policy = views_are_automatic and _views is None
    layout_profile = current_layout_profile()
    experimental_arrangement = (
        layout_profile.arrangement
        if layout_profile is not None
        else os.environ.get("DRAFTWRIGHT_EXPERIMENTAL_ARRANGEMENT")
    )
    arrangements = None
    if experimental_arrangement is not None:
        if experimental_arrangement not in ARRANGEMENTS:
            raise ValueError(
                f"experimental arrangement must be one of {ARRANGEMENTS}, "
                f"got {experimental_arrangement!r}"
            )
        arrangements = (experimental_arrangement,)
    drawing = _build(
        requested_scale,
        arrangements=arrangements,
        views=_views,
        select_automatic_views=automatic_view_policy,
    )
    initial_view_decision = getattr(drawing, "view_decision", {})
    view_attempts = list(initial_view_decision.get("attempts", ()))
    view_status = initial_view_decision.get("status", "selected")
    settled_principal_views = _principal_names(drawing)
    assessed_blockers = None

    # An authored scale constrains the joint plan; it does not turn automatic view planning
    # off. Apply the same finished-drawing veto as the fully automatic scale path before the
    # explicit-scale policy decides whether the requested scale itself is acceptable.
    if view_status == "candidate":
        candidate_issues, candidate_blockers = _automatic_assessment(drawing)
        absent_owners = _absent_view_owners(drawing)
        unrecoverable_blockers = tuple(
            blocker for blocker in candidate_blockers if not blocker["source_ids"]
        )
        rejection = _automatic_candidate_rejection(candidate_issues, unrecoverable_blockers)
        if rejection is not None or absent_owners:
            proposed = settled_principal_views
            reason = "annotation_owned_by_absent_view" if absent_owners else rejection
            assert reason is not None
            view_attempts[-1] = {
                "views": proposed,
                "status": "rejected",
                "reason": reason,
                "blockers": candidate_blockers,
                **({"annotations": absent_owners} if absent_owners else {}),
            }
            drawing = _build(requested_scale, views=third_angle_view_names(), retry_reason=reason)
            settled_principal_views = _principal_names(drawing)
            view_status = "retained_after_rejection"
        else:
            view_attempts[-1] = {
                "views": settled_principal_views,
                "status": "chosen",
                "reason": "redundant_radial_view_removed",
                "blockers": candidate_blockers,
            }
            view_status = "reduced"
            assessed_blockers = candidate_blockers
    elif view_status == "selected" and automatic_view_policy:
        view_status = "default"

    settled_view_decision = {
        "policy": "automatic" if automatic_view_policy else "selected",
        "status": view_status,
        "chosen": settled_principal_views,
        "attempts": tuple(view_attempts),
    }
    drawing.view_decision = settled_view_decision

    def _retain_explicit_view_decision(candidate: Drawing) -> Drawing:
        candidate.view_decision = settled_view_decision
        return candidate

    if assessed_blockers is None:
        blockers, short_off_axis_span = scale_blockers_for(drawing, requested_scale)
    else:
        blockers, short_off_axis_span = assessed_blockers, False
    if not blockers:
        drawing.scale_decision = _scale_decision(
            policy=scale_policy,
            requested=requested_scale,
            effective=drawing.scale,
            status="honored",
            attempted=(requested_scale,),
            attempts=(_scale_attempt(requested_scale, "complete"),),
        )
        return finish_annotation_layout(drawing)

    if scale_policy == "permissive":
        drawing.scale_decision = _scale_decision(
            policy=scale_policy,
            requested=requested_scale,
            effective=drawing.scale,
            status="degraded",
            blockers=blockers,
            attempted=(requested_scale,),
            attempts=(_scale_attempt(requested_scale, "incomplete", blockers),),
        )
        codes = ", ".join(sorted({item["code"] for item in blockers}))
        warnings.warn(
            f"requested scale {requested_scale:g} dropped required annotation outcomes "
            f"({codes}); returning the incomplete drawing because scale_policy='permissive'",
            ScaleCompletenessWarning,
            stacklevel=5,  # Skip this stage, policy, entry point, and operation wrapper.
        )
        return finish_annotation_layout(drawing)

    if scale_policy == "strict":
        raise ScaleIncompatibilityError(
            _scale_decision(
                policy=scale_policy,
                requested=requested_scale,
                effective=drawing.scale,
                status="rejected",
                blockers=blockers,
                attempted=(requested_scale,),
                attempts=(_scale_attempt(requested_scale, "incomplete", blockers),),
            )
        )

    attempted = [requested_scale]
    attempts = [
        _scale_attempt(
            requested_scale,
            "incomplete",
            blockers,
            reason="off_axis_span_below_1_mm" if short_off_axis_span else None,
        )
    ]
    last_effective_scale = drawing.scale
    last_blockers = blockers
    # ``_SCALES`` is descending and contains the preferred ISO 5455 reductions. The
    # requested non-standard scale is evaluated first above; fallback candidates must be
    # standard and no greater than it.
    for candidate in (item for item in _SCALES if item < requested_scale):
        if short_off_axis_span:
            break
        attempted.append(candidate)
        try:
            fallback = _build(
                candidate, views=settled_principal_views, retry_reason="scale_completeness"
            )
        except ValueError as exc:
            # Once a smaller scale hits the hard rendering floor, every following candidate
            # is smaller still. Do not hide any unrelated build error.
            if "drawing geometry degenerates" in str(exc):
                attempts.append(_scale_attempt(candidate, "render_floor", error=str(exc)))
                break
            raise
        fallback = _retain_explicit_view_decision(fallback)
        candidate_blockers, short_off_axis_span = scale_blockers_for(fallback, candidate)
        if candidate_blockers:
            attempts.append(
                _scale_attempt(
                    candidate,
                    "incomplete",
                    candidate_blockers,
                    reason="off_axis_span_below_1_mm" if short_off_axis_span else None,
                )
            )
            last_effective_scale = fallback.scale
            last_blockers = candidate_blockers
            continue
        attempts.append(_scale_attempt(candidate, "complete"))
        fallback.scale_decision = _scale_decision(
            policy=scale_policy,
            requested=requested_scale,
            effective=fallback.scale,
            status="fallback",
            blockers=blockers,
            attempted=attempted,
            attempts=attempts,
        )
        fallback.registry.record_issue(
            LintIssue(
                severity="warning",
                code="scale_fallback_applied",
                message=f"Requested scale {requested_scale:g} dropped required annotations; "
                f"using complete fallback scale {fallback.scale:g}",
            )
        )
        discard_finished_build_lint(fallback)
        warnings.warn(
            f"requested scale {requested_scale:g} dropped required annotation outcomes; "
            f"using complete fallback scale {fallback.scale:g}",
            ScaleCompletenessWarning,
            stacklevel=5,  # Skip this stage, policy, entry point, and operation wrapper.
        )
        return finish_annotation_layout(fallback)

    raise ScaleIncompatibilityError(
        _scale_decision(
            policy=scale_policy,
            requested=requested_scale,
            effective=last_effective_scale,
            status="no_complete_scale",
            blockers=last_blockers,
            attempted=attempted,
            attempts=attempts,
        )
    )
