"""Finished-drawing scale and arrangement decisions for the builder."""

from __future__ import annotations

import collections
import json
import warnings
from collections.abc import Iterable

from OCP.Standard import Standard_Failure

from draftwright._warnings import ScaleCompletenessWarning
from draftwright.annotations.orchestrator import _WITHHOLDING_CODES
from draftwright.drawing import Drawing, feature_key
from draftwright.drawing_diagnostics import finished_build_lint_issues
from draftwright.linting import LintIssue
from draftwright.linting.quality import is_hard_layout_issue, is_unreadable_layout_issue
from draftwright.view_plan import ARRANGEMENTS


class ScaleIncompatibilityError(ValueError):
    """An explicit scale could not preserve required annotation outcomes.

    ``decision`` is the same JSON-friendly record exposed on a successfully returned
    :class:`Drawing` as :attr:`Drawing.scale_decision`.
    """

    def __init__(self, decision: dict):
        self.decision = decision
        codes = ", ".join(sorted({item["code"] for item in decision["blockers"]}))
        attempted = decision.get("attempted_scales", ())
        suffix = f"; tried {list(attempted)}" if attempted else ""
        super().__init__(
            f"requested scale {decision['requested_scale']:g} cannot preserve required "
            f"annotations ({codes or 'no complete standard fallback'}){suffix}"
        )


def _scale_requirement(mid) -> dict:
    """Plain-data identity for one compiler measurement in a scale decision."""
    return {
        "feature": feature_key(getattr(mid, "feature", None)),
        "parameter": str(getattr(mid, "parameter", "")),
    }


def _hole_scale_requirement(requirement) -> dict:
    """Plain-data identity for one recognition-owned hole requirement."""
    feature, parameter = requirement
    return {"feature": feature_key(feature), "parameter": str(parameter)}


def _is_required_scale_drop(issue) -> bool:
    """Whether *issue* is an unresolved required placement outcome.

    Transactional table/balloon attempts may fail while restoring the original complete
    representation; their unowned diagnostics are not evidence that a manufacturing
    requirement was lost.  Semantic measurement/source provenance, an explicit placement
    stage, and all other established ``*_dropped`` codes fail closed.
    """
    stage = getattr(issue, "outcome_stage", None)
    if stage == "validation":
        return False
    if stage == "placement" or issue.code == "placement_unsatisfiable":
        return True
    if not issue.code.endswith("_dropped"):
        return False
    if issue.code in {"table_dropped", "balloon_dropped"}:
        return bool(
            getattr(issue, "measurement_ids", ())
            or getattr(issue, "hole_requirement_ids", ())
            or getattr(issue, "source_ids", ())
        )
    return True


def _short_off_axis_span_blocks_smaller_scales(blockers) -> bool:
    """A placed-model location already below 1 mm cannot recover by shrinking.

    The off-axis pass emits this typed evidence only after an approved, nonzero
    location span fails its paper-space gate. Explicit retries reuse the same model
    and principal views. The off-axis loss has no table replacement or retraction,
    so every smaller scale would report the same required loss. Other drop reasons
    have no such implication.
    """
    return any(
        blocker["code"] == "off_axis_location_dropped"
        and blocker.get("evidence_reason") == "off_axis_span_below_1_mm"
        and blocker["measurements"]
        for blocker in blockers
    )


#: Placement failures a different sheet or scale can repair, which must NOT refuse an
#: explicitly requested one.
#:
#: :func:`_is_required_scale_drop` answers one question — may this drawing be returned at the
#: scale the CALLER asked for? — and these codes deliberately answer no to it. Reporting a
#: withheld overall extent through `placement_unsatisfiable` made `build_drawing(part,
#: scale=...)` raise `ScaleIncompatibilityError` on parts that had built for as long as the
#: defect had existed, because that predicate matches by code name (#1216); the
#: comment in `annotations/from_model.py` records the measurement.
#:
#: Choosing a sheet AUTOMATICALLY is a different question. There is no caller request to
#: honour and nothing to refuse — a required dimension with nowhere to go is precisely what
#: the recovery ladder exists to repair. One predicate served both questions, so the ladder
#: could not see this symptom at all (#1590).
#:
#: The `*_dropped` member of the withholding vocabulary is excluded: it already ends in the
#: suffix `_is_required_scale_drop` matches, so it is a blocker and needs no second route in.
_REPLANNABLE_LOSS_CODES = tuple(
    code for code in _WITHHOLDING_CODES if not code.endswith("_dropped")
)


def _replannable_losses(issues) -> tuple:
    """Required dimensions that found no room, from one already-materialised lint pass.

    The automatic path's third recovery trigger, beside an axial-coverage gap and an
    authored-intent blocker. Severity is checked rather than assumed: the same codes are
    recorded at `info` when the measurement is merely too small to letter at this scale,
    which a larger sheet does fix — but by re-selecting the scale, not by replanning the
    arrangement, and `step_dim_withheld` at `info` fires on ordinary complete drawings.
    """
    return tuple(
        issue
        for issue in issues
        if issue.code in _REPLANNABLE_LOSS_CODES and issue.severity == "error"
    )


def _axial_dimension_losses(issues) -> tuple:
    """Missing explicit turned-axis lengths that a larger layout may recover.

    ``lint_axial_coverage`` proves only that annotation spans cover the overall profile.  A
    synthetic block dimension can satisfy that geometric span while omitting every individual
    step length.  The coverage lint reports that distinct manufacturing failure as
    ``axial_length_missing``; page/scale qualification must read it too.
    """
    return tuple(issue for issue in issues if issue.code == "axial_length_missing")


def _scale_blockers_from_issues(issues) -> tuple[dict, ...]:
    """Required placement failures from one already-materialised lint pass."""
    blockers = []
    for issue in issues:
        if not _is_required_scale_drop(issue):
            continue
        blocker = {
            "severity": issue.severity,
            "code": issue.code,
            "message": issue.message,
            "measurements": tuple(
                _scale_requirement(mid) for mid in getattr(issue, "measurement_ids", ())
            ),
            "hole_requirements": tuple(
                _hole_scale_requirement(req) for req in getattr(issue, "hole_requirement_ids", ())
            ),
            "source_ids": tuple(getattr(issue, "source_ids", ())),
        }
        if issue.code == "off_axis_location_dropped" and (
            issue.evidence_reason == "off_axis_span_below_1_mm"
        ):
            blocker["evidence_reason"] = issue.evidence_reason
        blockers.append(blocker)
    return tuple(blockers)


def _scale_blockers(drawing: Drawing, *, physical: bool = True) -> tuple[dict, ...]:
    """Required placement failures on a finished drawing, as stable plain data.

    ``physical=False`` restricts the critique to the recognition-free components, so a caller
    that must not materialise the ADR 3 (was 0017) aggregate can still read what failed to place.
    """
    issues = finished_build_lint_issues(drawing) if physical else drawing.lint(physical=False)
    return _scale_blockers_from_issues(issues)


def _hard_layout_issues(issues) -> tuple:
    """Settled-drawing defects that no page/scale proposal may outrank.

    Required placement drops are deliberately separate: they are the next
    verdict tier.  This first tier covers off-sheet, overlapping, or otherwise
    structurally unreadable ink using the same classification already applied
    to corrective candidates.
    """
    return tuple(issue for issue in issues if is_hard_layout_issue(issue))


def _structural_layout_issues(issues) -> tuple:
    """All established structural vetoes, including lower-tier crossings."""
    return tuple(
        issue for issue in issues if issue.severity == "error" or is_unreadable_layout_issue(issue)
    )


def _automatic_candidate_rejection(issues, blockers) -> str | None:
    """Apply the automatic-view veto tiers in their authoritative order.

    This gate chooses a principal-view topology, not a page/scale proposal.  Preserve
    its established tolerance for advisory crossing findings: a later page/scale
    candidate still applies the wider ``_structural_layout_issues`` veto, while a
    reduced topology is rejected here only for hard layout invalidity, a required
    loss, or an error-severity finding.
    """
    if _hard_layout_issues(issues):
        return "structural_error"
    if blockers:
        return "required_outcome_dropped"
    if any(issue.severity == "error" for issue in issues):
        return "structural_error"
    return None


def _layout_issue_records(issues) -> tuple[dict, ...]:
    """Stable plain-data hard-layout evidence for ``scale_decision``."""
    return tuple(
        {
            "severity": issue.severity,
            "code": issue.code,
            "message": issue.message,
        }
        for issue in issues
    )


def _blocker_identity(blocker) -> str:
    """A stable identity for one required placement failure, for comparing two builds.

    Keyed on WHAT was lost — the code and the measurement/requirement/source ids — never the
    message, which carries positions and sheet sizes that differ between two layouts of the
    same defect and would make every blocker look unique.
    """
    return json.dumps(
        {
            "code": blocker.get("code"),
            "measurements": blocker.get("measurements", ()),
            "hole_requirements": blocker.get("hole_requirements", ()),
            "source_ids": blocker.get("source_ids", ()),
        },
        sort_keys=True,
        default=str,
    )


def _arrangement_quality(issues, blockers, *, page, scale, interior_dimensions=0) -> dict:
    """Return a stable, JSON-friendly key for one finished arrangement.

    The tuple puts correctness and readability ahead of compactness. The layout
    selector uses it only after separately checking semantic and sheet parity.
    """
    issues = tuple(issues)
    blockers = tuple(blockers)
    hard_layout = _hard_layout_issues(issues)
    overlap_codes = {
        "annotation_ink_overlap",
        "annotation_overlap",
        "view_annotation_overlap",
    }
    overlaps = tuple(issue for issue in issues if issue.code in overlap_codes)
    crossings = tuple(issue for issue in issues if issue.code.endswith("crossing"))
    width, height = (float(value) for value in page)
    scale = float(scale)
    if width <= 0 or height <= 0 or scale <= 0:
        raise ValueError("arrangement quality needs positive page dimensions and scale")
    if interior_dimensions < 0:
        raise ValueError("arrangement quality needs a non-negative interior dimension count")
    key = (
        len(hard_layout),
        len(blockers),
        len(overlaps),
        int(interior_dimensions),
        len(crossings),
        -scale,
        width * height,
    )
    return {
        "selection_key": key,
        "hard_layout_violations": len(hard_layout),
        "required_outcomes_dropped": len(blockers),
        "overlaps": len(overlaps),
        "interior_dimensions": int(interior_dimensions),
        "crossings": len(crossings),
        "scale": scale,
        "page": (width, height),
    }


def _complete_automatic_plan(drawing: Drawing, *, issues=None) -> Drawing:
    """Fail closed when the settled automatic plan is incomplete or unreadable.

    `_scale_blockers` ran on the explicit-scale policy loop and on nothing else, so the two
    paths disagreed about the same part: asked for the sheet and scale it had just chosen
    itself, the engine would refuse or quietly reduce the scale, while the automatic path
    returned the incomplete drawing reporting `passed: True`.

    Corrective candidates have already exhausted the bounded recovery ladder. This final
    audit must not let a hard overlap/bounds defect disappear merely because every required
    annotation survived, nor let an incomplete drawing report itself as an ordinary automatic
    result. Required losses still receive the established ``plan_incomplete`` summary; hard
    layout evidence is already first-class lint and is copied into ``scale_decision``.
    """
    # One materialised recognition-free lint feeds blockers, provenance, and the
    # final decision. Corrective candidates pass their acceptance lint through so
    # the selected winner is not immediately re-linted from scratch.
    issues = tuple(drawing.lint(physical=False)) if issues is None else tuple(issues)
    blockers = _scale_blockers_from_issues(issues)
    hard_layout = _hard_layout_issues(issues)
    if not blockers and not hard_layout:
        return drawing

    if blockers:
        codes = ", ".join(sorted({item["code"] for item in blockers}))
        dropped = [i for i in issues if _is_required_scale_drop(i)]
        measurements = tuple(
            dict.fromkeys(
                mid for issue in dropped for mid in getattr(issue, "measurement_ids", ())
            )
        )
        hole_requirements = tuple(
            dict.fromkeys(
                req for issue in dropped for req in getattr(issue, "hole_requirement_ids", ())
            )
        )
        source_ids = tuple(
            dict.fromkeys(sid for issue in dropped for sid in getattr(issue, "source_ids", ()))
        )
        drawing.registry.record_issue(
            LintIssue(
                severity="error",
                code="plan_incomplete",
                message=(
                    f"the automatically planned sheet drops {len(blockers)} required "
                    f"annotation outcome(s) ({codes})"
                ),
                measurement_ids=measurements,
                source_ids=source_ids,
                hole_requirement_ids=hole_requirements,
            )
        )

    violations = _layout_issue_records(hard_layout)
    final_status = "invalid" if violations else "incomplete"
    final_reason = "hard_layout_invalid" if violations else "required_outcome_dropped"
    previous = getattr(drawing, "scale_decision", {})
    previous_attempts = tuple(previous.get("attempts", ()))
    previous_scales = tuple(previous.get("attempted_scales", ()))
    incomplete_attempt = _scale_attempt(
        drawing.scale,
        final_status,
        blockers,
        reason=final_reason,
        violations=violations,
        views=drawing.views,
        page=(drawing.page_w, drawing.page_h),
    )
    drawing.scale_decision = _scale_decision(
        policy="automatic",
        requested=None,
        effective=drawing.scale,
        status=final_status,
        blockers=blockers,
        violations=violations,
        attempted=previous_scales + (drawing.scale,),
        attempts=previous_attempts + (incomplete_attempt,),
    )
    if violations:
        violation_codes = ", ".join(sorted({item["code"] for item in violations}))
        message = (
            f"the automatically planned sheet remains structurally unreadable "
            f"({violation_codes}); returning the invalid drawing — see Drawing.scale_decision"
        )
    else:
        message = (
            f"the automatically planned sheet drops required annotation outcomes ({codes}); "
            f"returning the incomplete drawing — see Drawing.scale_decision"
        )
    warnings.warn(message, ScaleCompletenessWarning, stacklevel=2)
    return drawing


def _preserve_requirements_under_arrangement(drawing, chosen, build, blockers_for):
    """ADR 2 (was 0018 §5)'s first hard gate, applied to the arrangement: preserve every supported
    requirement or reject the candidate.

    The candidate loop can only ask whether the view blocks *fit*. Fitting is necessary and
    not sufficient, which #1130 measured three separate ways: an alternative arrangement can
    reach a larger scale whose enlarged views leave the location dims nowhere to go; it can
    reach a smaller sheet whose reduced free area does the same; and re-deriving it per stage
    can compose a sheet under an arrangement whose feasibility was never established. In each
    case the geometry was feasible and the drawing lost a dimension anyway.

    So the gate is not predicted, it is measured on the finished drawing (ADR 2 (was 0014 Amdt 3)),
    reusing the engine's own definition of a lost requirement — the same `_scale_blockers`
    the explicit-scale policy rejects a scale on. Applying it here is what closes the gap
    that made the automatic path emit sheets it would have refused if asked for them
    explicitly (#1250).

    Costs a second compile only for a drawing that both departed from the preferred
    arrangement AND lost something: an alternative that lost nothing cannot be improved on,
    and the preferred arrangement is never second-guessed at all (the caller checks that
    before calling, since only it knows what the attempt was built under). Every attempt is
    recorded on the returned drawing, so the cost and the reason are both visible.
    """

    def _record(winner, attempts):
        winner.arrangement_decision = {
            "chosen": next(name for name, status, _found in attempts if status == "chosen"),
            "attempts": tuple(
                {"arrangement": name, "status": status, "blockers": found}
                for name, status, found in attempts
            ),
        }
        return winner

    blockers = blockers_for(drawing)
    if not blockers:
        return _record(drawing, [(chosen, "chosen", ())])

    # Rebuild confined to the arrangement every drawing used before this choice existed. It
    # is kept only if it genuinely preserves more — an alternative is not rejected for
    # blockers that the preferred arrangement would have produced too.
    preferred = build(None, (ARRANGEMENTS[0],))
    preferred_blockers = blockers_for(preferred)
    # Compared by IDENTITY as a multiset, not by count. Cardinality alone accepts a
    # DIFFERENT loss: if the preferred layout drops requirement B and the alternative drops
    # requirement A, both have one blocker, and a `<` test keeps the alternative even though
    # the default preserved A. That is the opposite of "preserve every supported requirement
    # or reject the candidate" (#1130).
    #
    # The rule is therefore one-sided, and deliberately so: the alternative may not introduce
    # any blocker the preferred result did not already have. It is free to preserve MORE, and
    # it does not have to beat the historical arrangement on volume — that arrangement
    # was the comparison floor before this local choice existed, so an alternative
    # earns its place by costing nothing, not by costing less.
    introduced = collections.Counter(map(_blocker_identity, blockers)) - collections.Counter(
        map(_blocker_identity, preferred_blockers)
    )
    if introduced:
        return _record(
            preferred,
            [
                (chosen, "rejected", blockers),
                (ARRANGEMENTS[0], "chosen", preferred_blockers),
            ],
        )
    return _record(
        drawing,
        [
            (chosen, "chosen", blockers),
            (ARRANGEMENTS[0], "rejected", preferred_blockers),
        ],
    )


def _scale_decision(
    *,
    policy: str,
    requested: float | None,
    effective: float,
    status: str,
    blockers=(),
    violations=(),
    attempted=(),
    attempts=(),
) -> dict:
    decision = {
        "policy": policy,
        "requested_scale": requested,
        "effective_scale": effective,
        "status": status,
        "blockers": tuple(blockers),
        "attempted_scales": tuple(attempted),
        "attempts": tuple(attempts),
    }
    if violations:
        decision["violations"] = tuple(violations)
    return decision


def _scale_attempt(
    scale: float | None,
    status: str,
    blockers=(),
    *,
    error: str | None = None,
    reason: str | None = None,
    rejection: str | None = None,
    violations=(),
    views: Iterable[str] | None = None,
    page: tuple[float, float] | None = None,
) -> dict:
    """One plain-data scale trial, or a prebuild skip with no candidate evidence."""
    attempt: dict[str, object] = {"scale": scale, "status": status}
    if status != "skipped":
        attempt["blockers"] = tuple(blockers)
    if error is not None:
        attempt["error"] = error
    if reason is not None:
        attempt["reason"] = reason
    if rejection is not None:
        attempt["rejection"] = rejection
    if violations:
        attempt["violations"] = tuple(violations)
    if views is not None:
        attempt["views"] = tuple(views)
    if page is not None:
        attempt["page"] = tuple(page)
    return attempt


def _principal_view_exceeds_page(scale, page, bounds, views) -> bool:
    """Rule out a fixed-scale build whose unannotated principal cannot fit.

    This uses only the part's world-space bounding box and the full page, leaving
    margins and annotation footprints out of the bound. A rejected candidate
    therefore cannot be rescued by a different arrangement or ink placement.
    """
    extents = {
        "x": bounds.max.X - bounds.min.X,
        "y": bounds.max.Y - bounds.min.Y,
        "z": bounds.max.Z - bounds.min.Z,
    }
    axes = {"front": ("x", "z"), "plan": ("x", "y"), "side": ("y", "z")}
    page_w, page_h = page
    return any(
        extents[horizontal] * scale > page_w + 1e-9 or extents[vertical] * scale > page_h + 1e-9
        for view in views
        if (pair := axes.get(view)) is not None
        for horizontal, vertical in (pair,)
    )


def _has_detail_view(views) -> bool:
    """Whether a resolved drawing/view mapping contains an automatic detail."""
    return any(name.startswith("detail_") for name in views)


#: The ValueError messages a speculative build may raise WITHOUT it meaning a bug. Matched on
#: message because the engine raises bare `ValueError` here; a purpose-built type would be
#: better and is not this change's to introduce. Everything else — `ScaleIncompatibilityError`,
#: `ViewPlanIncomplete`, `MultipleTurnedProfilesError`, an unknown page size — is a deliberate
#: refusal under ADR 5 and must reach the caller rather than be logged and stepped over.
_EXPECTED_CANDIDATE_FAILURES = (
    # The speculative-page path: a scale at which the part's own geometry collapses.
    "drawing geometry degenerates",
    # A source with no solid body — a STEP file holding a bare curve projects nothing. The
    # settled-layout reference build in `sheet_emit` sees this and must still write a script.
    "project_to_viewport returned empty geometry",
)


def _is_expected_candidate_build_failure(exc: Exception) -> bool:
    """Whether a speculative build may reject without hiding an invariant bug."""
    return isinstance(exc, Standard_Failure) or (
        isinstance(exc, ValueError)
        and any(known in str(exc) for known in _EXPECTED_CANDIDATE_FAILURES)
    )
