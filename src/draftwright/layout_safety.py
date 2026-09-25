"""Independent, versioned safety evidence for candidate annotation layouts."""

from __future__ import annotations

import math
from collections import Counter

from draftwright.linting.structural import annotation_bounds
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
_SATISFIED_RAW_REQUIREMENT_STATES = frozenset(
    {"placed", "satisfied_by_structured_note", "inapplicable"}
)
_SATISFIED_OCCURRENCE_DISPOSITIONS = frozenset({"represented", "absorbed"})
_SATISFIED_OCCURRENCE_COVERAGE = frozenset({"ledger", "not-applicable"})
_DECLARED_CARRIER_KINDS = frozenset(
    {
        "control_frame",
        "datum_ref",
        "finish",
        "note",
        "general_tolerance",
        "default_surface_finish",
        "document_note",
    }
)
_MIN_VIEW_AREA_MM2 = 100.0
_PAGE_EDGE_TOLERANCE_MM = 1e-6


def _raw_requirement_state(requirement: object) -> str:
    if not isinstance(requirement, dict):
        return "<invalid>"
    state = requirement.get("state")
    return state if isinstance(state, str) else "<invalid>"


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
    """Match authored aspects and document carriers to live ink by identity."""

    aspects = [
        (index, feature)
        for index, feature in enumerate(model.features)
        if getattr(feature, "kind", None) in _DECLARED_CARRIER_KINDS
    ]
    if not aspects:
        return []
    registry = getattr(drawing, "registry", None)
    names = getattr(registry, "names", None)
    declaration_of = getattr(registry, "declaration_of", None)
    feature_of = getattr(registry, "feature_of", None)
    named = getattr(registry, "named", None)
    if (
        not callable(names)
        or not callable(declaration_of)
        or not callable(feature_of)
        or not callable(named)
    ):
        return [{"reason": "declaration_provenance_unavailable"}]
    represented: set[int] = set()
    for name in names():
        represented.update((id(declaration_of(name)), id(feature_of(name))))
        annotation = named(name)
        represented.update(id(feature) for feature in getattr(annotation, "source_features", ()))
    return [
        {"feature_index": index, "kind": feature.kind, "reason": "representation_missing"}
        for index, feature in aspects
        if id(feature) not in represented
    ]


def _annotation_page_gaps(drawing) -> list[dict[str, object]]:
    """Check every non-rider annotation against the settled physical page.

    The report call has already run lint, which fills the drawing's shared
    bounding-box cache. Reusing it avoids tessellating the same ink a second
    time; unlike a lint-code search, an unavailable box also fails closed.
    """

    items = getattr(drawing, "items", None)
    cache = getattr(drawing, "box_cache", None)
    if items is None or not isinstance(cache, dict):
        return [{"reason": "annotation_bounds_unavailable"}]
    iter_annotations = getattr(drawing, "iter_annotations", None)
    names = (
        {id(annotation): name for name, annotation in iter_annotations()}
        if callable(iter_annotations)
        else {}
    )
    gaps = []
    for index, item in enumerate(items):
        if any(
            getattr(item, rider, False)
            for rider in ("is_sheet_frame", "is_zone_grid", "is_zone_label")
        ):
            continue
        name = names.get(id(item), f"anonymous[{index}]")
        bounds = annotation_bounds(item, cache)
        if bounds is None:
            gaps.append({"annotation": name, "reason": "ink_bounds_unavailable"})
            continue
        finite = all(math.isfinite(value) for value in bounds)
        if not finite or (
            bounds[0] < -_PAGE_EDGE_TOLERANCE_MM
            or bounds[1] < -_PAGE_EDGE_TOLERANCE_MM
            or bounds[2] > drawing.page_w + _PAGE_EDGE_TOLERANCE_MM
            or bounds[3] > drawing.page_h + _PAGE_EDGE_TOLERANCE_MM
        ):
            gaps.append(
                {
                    "annotation": name,
                    "reason": "off_page_ink" if finite else "invalid_ink_bounds",
                    "bounds": [value if math.isfinite(value) else None for value in bounds],
                }
            )
    return gaps


def _recognition_occurrence_gaps(recognition: dict, expected: int) -> list[dict[str, object]]:
    """Check accepted source occurrences, not just their aggregate summary."""

    occurrences = recognition.get("occurrences")
    if not isinstance(occurrences, list):
        return [{"reason": "occurrence_inventory_unavailable"}]
    gaps: list[dict[str, object]] = []
    requirements = recognition.get("requirements")
    if not isinstance(requirements, list):
        return [{"reason": "requirement_ledger_unavailable"}]
    ledger: dict[str, dict] = {}
    for index, requirement in enumerate(requirements):
        requirement_id = requirement.get("id") if isinstance(requirement, dict) else None
        if not isinstance(requirement_id, str) or not requirement_id:
            gaps.append({"index": index, "reason": "invalid_requirement_id"})
        elif requirement_id in ledger:
            gaps.append({"requirement": requirement_id, "reason": "duplicate_requirement_id"})
        else:
            ledger[requirement_id] = requirement
    if len(occurrences) != expected:
        gaps.append(
            {
                "reason": "occurrence_count_mismatch",
                "expected": expected,
                "observed": len(occurrences),
            }
        )
    for index, occurrence in enumerate(occurrences):
        if not isinstance(occurrence, dict):
            gaps.append({"index": index, "reason": "invalid_occurrence"})
            continue
        occurrence_id = occurrence.get("id", f"occurrence[{index}]")
        disposition = occurrence.get("disposition")
        if (
            not isinstance(disposition, str)
            or disposition not in _SATISFIED_OCCURRENCE_DISPOSITIONS
        ):
            gaps.append(
                {
                    "occurrence": occurrence_id,
                    "reason": "adverse_disposition",
                    "disposition": disposition,
                }
            )
        requirements = occurrence.get("requirements")
        coverage = requirements.get("coverage") if isinstance(requirements, dict) else None
        ids = requirements.get("ids") if isinstance(requirements, dict) else None
        if (
            not isinstance(coverage, str)
            or coverage not in _SATISFIED_OCCURRENCE_COVERAGE
            or (coverage == "ledger" and (not isinstance(ids, list) or not ids))
            or (coverage == "not-applicable" and ids != [])
        ):
            gaps.append(
                {
                    "occurrence": occurrence_id,
                    "reason": "unresolved_requirement_coverage",
                    "coverage": coverage,
                }
            )
        if coverage == "ledger" and isinstance(ids, list):
            for requirement_id in ids:
                requirement = (
                    ledger.get(requirement_id) if isinstance(requirement_id, str) else None
                )
                sources = requirement.get("occurrence_ids") if requirement is not None else None
                if (
                    not isinstance(requirement_id, str)
                    or not requirement_id
                    or not isinstance(sources, list)
                    or occurrence_id not in sources
                ):
                    gaps.append(
                        {
                            "occurrence": occurrence_id,
                            "requirement": requirement_id,
                            "reason": "requirement_link_missing",
                        }
                    )
    return gaps


def candidate_safety_evidence(drawing) -> dict[str, object]:
    """Observe finished candidate risks without comparing it to a baseline.

    The report supplies recognition/declared obligations and independent lint;
    the live drawing supplies settled page, scale, and view geometry. Missing
    evidence is a failed check, never an empty successful inventory. Declared
    GD&T and document-wide carriers are checked, but other non-dimensional and
    generated-script parity, plus leader legibility, remain incomplete. It must
    not admit a production candidate.
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
            requirements = recognition.get("requirements")
            requirement_rows = requirements if isinstance(requirements, list) else []
            # Fail closed on suppressed or unfamiliar states as well as the
            # known unresolved outcomes. The report schema may grow without
            # this safety gate silently admitting a new requirement state.
            states = (
                map(_raw_requirement_state, requirement_rows)
                if isinstance(requirements, list)
                else ("<invalid>",)
            )
            unresolved = Counter(
                state for state in states if state not in _SATISFIED_RAW_REQUIREMENT_STATES
            )
            check("required_outcomes", not unresolved, dict(sorted(unresolved.items())))
            missing = int(summary.get("unexpectedly_missing", 0))
            check("recognized_ownership", missing == 0, missing)
            total = int(summary.get("total", 0))
            occurrence_gaps = _recognition_occurrence_gaps(recognition, total)
            check("recognized_occurrences", not occurrence_gaps, occurrence_gaps)
            check(
                "recognized_inventory",
                total > 0
                and feature_count is not None
                and feature_count > 0
                and bool(requirement_rows),
                {
                    "accepted": total,
                    "features": feature_count,
                    "requirements": len(requirement_rows),
                },
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
    annotation_gaps = _annotation_page_gaps(drawing)
    check("annotation_page_containment", not annotation_gaps, annotation_gaps)

    failed = [item["name"] for item in checks if not item["passed"]]
    return {
        "version": 8,
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
