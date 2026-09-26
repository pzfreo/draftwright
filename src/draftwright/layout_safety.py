"""Independent, versioned safety evidence for candidate annotation layouts."""

from __future__ import annotations

import math
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
_DECLARED_ASPECT_KINDS = frozenset({"control_frame", "datum_ref", "finish", "note"})
_MIN_VIEW_AREA_MM2 = 100.0
_PAGE_EDGE_TOLERANCE_MM = 1e-6


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


def _declared_aspect_gaps(drawing, model) -> list[dict[str, object]]:
    """Match each authored aspect to live ink by exact declaration provenance."""

    aspects = [
        (index, feature)
        for index, feature in enumerate(model.features)
        if getattr(feature, "kind", None) in _DECLARED_ASPECT_KINDS
    ]
    if not aspects:
        return []
    registry = getattr(drawing, "registry", None)
    names = getattr(registry, "names", None)
    declaration_of = getattr(registry, "declaration_of", None)
    if not callable(names) or not callable(declaration_of):
        return [{"reason": "declaration_provenance_unavailable"}]
    represented = {id(declaration_of(name)) for name in names()}
    return [
        {"feature_index": index, "kind": feature.kind, "reason": "representation_missing"}
        for index, feature in aspects
        if id(feature) not in represented
    ]


def candidate_safety_evidence(drawing) -> dict[str, object]:
    """Observe finished candidate risks without comparing it to a baseline.

    The report supplies recognition/declared obligations and independent lint;
    the live drawing supplies settled page, scale, and view geometry. Missing
    evidence is a failed check, never an empty successful inventory. This first
    slice is observational: declared GD&T provenance is checked, but other
    non-dimensional and generated-script parity, plus leader legibility, remain
    incomplete. It must not admit a production candidate.
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
                total > 0
                and feature_count is not None
                and feature_count > 0
                and bool(requirements),
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
            aspect_gaps = _declared_aspect_gaps(drawing, model) if model is not None else []
            check("declared_aspects", model is not None and not aspect_gaps, aspect_gaps)
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
    outside_page = {}
    for name in drawing.views:
        bounds = tuple(float(value) for value in drawing.view_bounds(name))
        finite = all(math.isfinite(value) for value in bounds)
        if not finite or (
            bounds[0] < -_PAGE_EDGE_TOLERANCE_MM
            or bounds[1] < -_PAGE_EDGE_TOLERANCE_MM
            or bounds[2] > drawing.page_w + _PAGE_EDGE_TOLERANCE_MM
            or bounds[3] > drawing.page_h + _PAGE_EDGE_TOLERANCE_MM
        ):
            outside_page[name] = [value if math.isfinite(value) else None for value in bounds]
        area = max(0.0, bounds[2] - bounds[0]) * max(0.0, bounds[3] - bounds[1]) if finite else 0.0
        view_sizes[name] = round(area, 3)
    too_small = {name: area for name, area in view_sizes.items() if area < _MIN_VIEW_AREA_MM2}
    check("views_present", bool(view_sizes), sorted(view_sizes))
    check("minimum_view_area", not too_small, too_small)
    # The report's lint is still required, but a missing or misclassified lint
    # issue must not make a wholly off-sheet view look safe. Check settled view
    # geometry directly against the caller's resolved page, without changing it.
    check("view_page_containment", not outside_page, outside_page)

    failed = [item["name"] for item in checks if not item["passed"]]
    return {
        "version": 3,
        "checks_passed": not failed,
        "admission_ready": False,
        "limitations": [
            "authored_non_dimension_parity_incomplete",
            "automatic_pmi_aspect_parity_not_checked",
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
