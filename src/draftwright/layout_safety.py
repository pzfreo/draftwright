"""Independent, versioned safety evidence for candidate annotation layouts."""

from __future__ import annotations

from collections import Counter

from draftwright.model.planner import _authored_addresses, authored_dimension_requests
from draftwright.reporting import ReportUnavailableError

_LAYOUT_BLOCKERS = frozenset(
    {
        "annotation_out_of_bounds",
        "annotation_overlap",
        "annotation_ink_overlap",
        "dim_inside_part",
        "feature_leader_crossing",
        "feature_leader_fixed_ink_unverified",
        "label_centerline_overlap",
        "layout_repack_stalled",
        "legibility_floor_breached",
        "leader_crosses_silhouette",
        "leader_line_through_text",
        "page_fit_uncertain",
        "placement_unsatisfiable",
        "title_field_overflow",
        "view_annotation_inside_extents",
        "view_annotation_overlap",
        "view_out_of_bounds",
        "view_overlap",
    }
)
_UNRESOLVED_STATES = frozenset({"dropped", "missing", "unverifiable", "unsupported"})
_MIN_VIEW_AREA_MM2 = 100.0


def _authored_dimension_gaps(drawing, model) -> list[dict[str, object]]:
    requests = authored_dimension_requests(model)
    if requests is None:
        requests = model.requested_dimensions
    if not requests:
        return []

    claims = {
        (id(identity.feature), str(identity.parameter))
        for name in drawing.registry.names()
        for identity in (
            *drawing.registry.measurement_of(name),
            *drawing.registry.satisfaction_of(name),
        )
    }
    feature_indices = {id(feature): index for index, feature in enumerate(model.features)}
    gaps = []
    for request in requests:
        feature = request.feature
        parameters = [
            parameter
            for parameter in feature.parameters()
            if _authored_addresses(request, feature, parameter)
        ]
        if not parameters:
            gaps.append(
                {
                    "feature_index": feature_indices.get(id(feature)),
                    "role": request.role,
                    "reason": "request_has_no_matching_parameter",
                }
            )
        for parameter in parameters:
            if (id(feature), parameter.parameter_id) not in claims:
                gaps.append(
                    {
                        "feature_index": feature_indices.get(id(feature)),
                        "role": parameter.parameter_id,
                        "reason": "representation_missing",
                    }
                )
    return gaps


def candidate_safety_evidence(drawing) -> dict[str, object]:
    """Observe finished candidate risks without comparing it to a baseline.

    The report supplies recognition/declared obligations and independent lint;
    the live drawing supplies settled page, scale, and view geometry. Missing
    evidence is a failed check, never an empty successful inventory. This first
    slice is observational until authored and generated-script parity and leader
    legibility are checked; it must not be used to admit a production candidate.
    """

    checks: list[dict[str, object]] = []

    def check(name: str, passed: bool, detail: object) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    model = drawing.model()
    feature_count = len(model.features) if model is not None else None
    check("model_available", feature_count is not None, feature_count)
    authored_gaps = _authored_dimension_gaps(drawing, model) if model is not None else []
    check("authored_dimensions", model is not None and not authored_gaps, authored_gaps)

    try:
        report = drawing.report()
    except ReportUnavailableError as error:
        report = None
        check("report_available", False, str(error))
    else:
        check("report_available", True, report.get("schema_version"))

    if report is not None:
        if report.get("schema_version") == 3:
            recognition = report.get("recognition", {})
            summary = recognition.get("summary", {})
            requirements = recognition.get("requirements", ())
            unresolved = Counter(
                requirement.get("state")
                for requirement in requirements
                if requirement.get("state") in _UNRESOLVED_STATES
            )
            check("required_outcomes", not unresolved, dict(sorted(unresolved.items())))
            missing = int(summary.get("unexpectedly_missing", 0))
            check("recognized_ownership", missing == 0, missing)
            total = int(summary.get("total", 0))
            check(
                "recognized_inventory",
                not (
                    (total > 0 and (feature_count == 0 or not requirements))
                    or (total == 0 and feature_count == 0)
                ),
                {"accepted": total, "features": feature_count, "requirements": len(requirements)},
            )
        elif report.get("schema_version") == 8:
            declarations = report.get("declarations", {})
            declared_count = declarations.get("feature_count")
            check(
                "declared_inventory",
                feature_count is not None and declared_count == feature_count,
                {"expected": feature_count, "reported": declared_count},
            )
        else:
            check("report_schema", False, report.get("schema_version"))

        lint = report.get("lint")
        quality = lint.get("quality") if isinstance(lint, dict) else None
        completeness = quality.get("completeness") if isinstance(quality, dict) else None
        lint_available = (
            isinstance(lint, dict)
            and isinstance(lint.get("issues"), list)
            and isinstance(completeness, dict)
        )
        check(
            "lint_evidence",
            lint_available,
            "structured lint and completeness required",
        )
        lint = lint if isinstance(lint, dict) else {}
        issues = lint.get("issues")
        issues = issues if isinstance(issues, list) else ()
        blocking = sorted(
            {
                issue.get("code", "")
                for issue in issues
                if issue.get("severity") == "error"
                or issue.get("code") in _LAYOUT_BLOCKERS
                or str(issue.get("code", "")).endswith(
                    ("_dropped", "_withheld", "_unsupported", "_unverifiable")
                )
            }
        )
        check("lint_blockers", not blocking, blocking)
        completeness = completeness if isinstance(completeness, dict) else {}
        unresolved_counts = {
            state: completeness.get(state, 0)
            for state in _UNRESOLVED_STATES
            if completeness.get(state, 0)
        }
        check("audited_coverage", not unresolved_counts, unresolved_counts)

    view_sizes = {}
    for name in drawing.views:
        bounds = drawing.view_bounds(name)
        area = max(0.0, bounds[2] - bounds[0]) * max(0.0, bounds[3] - bounds[1])
        view_sizes[name] = round(area, 3)
    too_small = {name: area for name, area in view_sizes.items() if area < _MIN_VIEW_AREA_MM2}
    check("views_present", bool(view_sizes), sorted(view_sizes))
    check("minimum_view_area", not too_small, too_small)

    failed = [item["name"] for item in checks if not item["passed"]]
    return {
        "version": 1,
        "checks_passed": not failed,
        "admission_ready": False,
        "limitations": [
            "authored_non_dimension_parity_not_checked",
            "generated_script_parity_not_checked",
            "leader_legibility_not_checked",
            "minimum_view_area_threshold_not_calibrated",
            "recognition_gaps_beyond_accepted_occurrences_not_detectable",
        ],
        "failed_checks": failed,
        "checks": checks,
        "page": [float(drawing.page_w), float(drawing.page_h)],
        "scale": float(drawing.scale),
        "view_areas_mm2": view_sizes,
    }
