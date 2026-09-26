"""Finished-drawing annotation layout evidence and safe comparison."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING

from draftwright.annotation_layout_profile import (
    AnnotationLayoutProfile,
    candidate_profile,
)

if TYPE_CHECKING:
    from draftwright.compose import AnnotationSchemeShadowReport, StripDepths
    from draftwright.drawing import Drawing


def choose_pre_render_profile(
    strips: StripDepths,
    report: AnnotationSchemeShadowReport,
    *,
    page: tuple[float, float],
    views: tuple[str, ...],
    auto_dims: bool,
) -> dict[str, object]:
    """Recommend a layout from typed demand and settled pre-render constraints.

    This observational policy uses demand composition and corridor pressure, not
    the quality of a finished baseline drawing. It does not safety-admit a result.
    """

    scheme = strips.scheme
    demand_count = len(scheme.demands) if scheme is not None else 0
    under_reserved_count = len(report.under_reserved)
    missing_views = (
        sorted({demand.view for demand in scheme.demands} - set(views))
        if scheme is not None
        else []
    )
    if not auto_dims:
        profile, reason = None, "automatic_annotations_disabled"
    elif scheme is None:
        profile, reason = None, "typed_scheme_unavailable"
    elif missing_views:
        profile, reason = None, "demand_view_absent"
    elif not scheme.demands and not scheme.unplanned:
        profile, reason = "iso-growth", "no_annotation_demand"
    elif (
        demand_count >= 40
        and under_reserved_count >= 3
        and 3 * report.unplanned_count >= demand_count
    ):
        # A dense, partly unplanned drawing should retain uncapped reservations;
        # columns also avoid the exterior-dimension treatment of the side profile.
        profile, reason = "columns", "dense_unplanned_corridors"
    elif demand_count >= 30 and report.unplanned_count <= 5 and under_reserved_count >= 4:
        # Mostly typed demand can use planned corridors when the legacy strips
        # are already under pressure; the independent rendered gate still judges it.
        profile, reason = "planned", "typed_corridor_pressure"
    elif demand_count <= 25 and report.unplanned_count <= 5 and 0 < under_reserved_count <= 3:
        # Sparse uncertain routes keep baseline annotation placement while
        # granting the isometric view any sheet slack it can safely consume.
        profile, reason = "iso-growth", "bounded_sparse_demand"
    elif scheme.unplanned or report.under_reserved:
        profile, reason = "legacy-depth", "unplanned_or_under_reserved_demand"
    else:
        profile, reason = "planned", "all_typed_corridors_fit"
    return {
        "version": 3,
        "profile": profile,
        "reason": reason,
        "page": [float(page[0]), float(page[1])],
        "scale": report.scale,
        "views": list(views),
        "demand_count": demand_count,
        "unplanned_count": report.unplanned_count,
        "under_reserved": under_reserved_count,
        "missing_views": missing_views,
    }


def drawing_record(drawing) -> dict:
    """Capture the finished drawing facts used by the layout selector."""

    return {
        "page": [drawing.page_w, drawing.page_h],
        "scale": drawing.scale,
        "views": list(drawing.views),
        "manifest": _manifest(drawing),
    }


def _lint_witnesses(issues) -> list[dict]:
    """Retain located/named lint evidence for offline layout diagnosis.

    A score or blocker count cannot identify the annotation pair that collided.
    Keep only structured witnesses, not mutable prose or process-local objects;
    comparison still uses its existing quality and semantic fields.
    """

    witnesses = []
    for issue in issues:
        if not (
            issue.annotation_name
            or issue.related_annotation_names
            or issue.view
            or issue.location is not None
        ):
            continue
        witnesses.append(
            {
                "code": issue.code,
                "severity": issue.severity,
                "view": issue.view,
                "annotation_name": issue.annotation_name,
                "related_annotation_names": sorted(issue.related_annotation_names),
                "location": list(issue.location) if issue.location is not None else None,
            }
        )
    return sorted(witnesses, key=lambda item: json.dumps(item, sort_keys=True))


def _manifest(drawing) -> dict:
    from draftwright.builder import (
        _arrangement_quality,
        _blocker_identity,
        _scale_blockers_from_issues,
    )

    model = drawing.model()
    feature_indices = {id(feature): index for index, feature in enumerate(model.features)}

    def feature_identity(feature) -> dict:
        index = feature_indices.get(id(feature))
        if index is None:
            matches = [
                candidate_index
                for candidate_index, candidate in enumerate(model.features)
                if candidate == feature
            ]
            index = matches[0] if len(matches) == 1 else None
        return {
            "feature_index": index,
            "kind": str(getattr(feature, "kind", type(feature).__name__)),
            "source_id": str(
                getattr(feature, "source_id", "")
                or next(iter(sorted(map(str, getattr(feature, "source_ids", ())))), "")
            ),
        }

    def requirement_identities(identities) -> list[dict]:
        values = [
            {
                "feature": feature_identity(identity.feature),
                "parameter": str(identity.parameter),
            }
            for identity in identities
        ]
        return sorted(values, key=lambda value: json.dumps(value, sort_keys=True))

    annotations = {}
    for name, annotation in drawing.iter_annotations():
        annotations[name] = {
            "type": type(annotation).__name__,
            "label": str(getattr(annotation, "label", "")),
            "view": drawing.view_of(name),
            "region": getattr(annotation, "_dw_candidate_region", None),
            "measurements": requirement_identities(drawing.registry.measurement_of(name)),
            "satisfactions": requirement_identities(drawing.registry.satisfaction_of(name)),
            "owners": sorted(
                (feature_identity(feature) for feature in drawing.registry.features_of(name)),
                key=lambda value: json.dumps(value, sort_keys=True),
            ),
        }
    issues = tuple(drawing.lint())
    blockers = _scale_blockers_from_issues(issues)
    lint = drawing.lint_summary()
    completeness = lint["quality"]["completeness"]
    drops = {
        code: count
        for code, count in lint["by_code"].items()
        if "drop" in code or "withheld" in code or code == "placement_unsatisfiable"
    }
    interior_dimensions = sorted(
        name
        for name, annotation in drawing.iter_annotations()
        if "Dimension" in type(annotation).__name__
        and getattr(annotation, "_dw_candidate_region", None) == "interior"
    )
    return {
        "annotations": annotations,
        "iso_bounds": drawing.view_bounds("iso"),
        "iso_to_sheet_scale": (
            drawing.iso_projection_scale is not None
            and abs(drawing.iso_projection_scale / drawing.scale - 1.0) < 0.05
        ),
        "orthographic_bounds": {
            view: drawing.view_bounds(view) for view in ("front", "plan", "side")
        },
        "interior_dimensions": interior_dimensions,
        "arrangement_quality": _arrangement_quality(
            issues,
            blockers,
            page=(drawing.page_w, drawing.page_h),
            scale=drawing.scale,
            interior_dimensions=len(interior_dimensions),
        ),
        "blocker_identities": sorted(_blocker_identity(blocker) for blocker in blockers),
        "lint_witnesses": _lint_witnesses(issues),
        "coverage": {
            key: completeness.get(key)
            for key in ("requirements", "placed", "missing", "unverifiable", "unsupported")
        },
        "drops": drops,
        "lint": {"errors": lint["errors"], "warnings": lint["warnings"]},
    }


def _compare(baseline: dict, candidate: dict) -> dict:
    before = baseline["manifest"]
    after = candidate["manifest"]
    before_annotations = before["annotations"]
    after_annotations = after["annotations"]

    def semantic_annotation(annotation):
        annotation_type = annotation["type"]
        # Leader routing is layout, not manufacturing meaning. The candidate is allowed
        # to replace a straight Leader with a RoutedLeader while preserving the same
        # label, ownership, and requirement identities.
        semantic_type = "Leader" if annotation_type.endswith("Leader") else annotation_type
        semantic = {"type": semantic_type, "label": annotation["label"]}
        for field in ("measurements", "satisfactions", "owners"):
            if field in annotation:
                # FeatureFacts is a compiler-only structural projection, never a
                # model feature that drop()/annotations_of() can target. Candidate
                # route choice may attach or omit it without changing ownership.
                semantic[field] = (
                    [owner for owner in annotation[field] if owner.get("kind") != "FeatureFacts"]
                    if field == "owners"
                    else annotation[field]
                )
        return semantic

    def semantic_counts(annotations, *, omit_nts=False):
        return Counter(
            json.dumps(
                semantic_annotation(annotation),
                sort_keys=True,
                separators=(",", ":"),
            )
            for annotation in annotations.values()
            if not (
                omit_nts
                and annotation["type"] == "Note"
                and annotation["label"] == "ISO VIEW (NTS)"
            )
        )

    # A to-scale orientation view must lose its former NTS caption. The final
    # projected scale is measured in the worker, so a missing note is excused
    # only when the candidate still has the iso and it truly matches the sheet.
    iso_note_obsolete = after.get("iso_to_sheet_scale", False) and "iso" in candidate.get(
        "views", ()
    )
    before_semantics = semantic_counts(before_annotations, omit_nts=iso_note_obsolete)
    after_semantics = semantic_counts(after_annotations)
    before_interiors = semantic_counts(
        {name: before_annotations[name] for name in before["interior_dimensions"]}
    )
    after_interiors = semantic_counts(
        {name: after_annotations[name] for name in after["interior_dimensions"]}
    )

    def expand(delta):
        return [json.loads(item) for item, count in sorted(delta.items()) for _ in range(count)]

    before_blockers = Counter(before["blocker_identities"])
    after_blockers = Counter(after["blocker_identities"])
    introduced_blockers = sorted((after_blockers - before_blockers).elements())
    before_coverage = before["coverage"]
    after_coverage = after["coverage"]
    same_sheet = baseline.get("page") == candidate.get("page") and baseline.get(
        "scale"
    ) == candidate.get("scale")
    coverage_no_worse = (
        before_coverage["requirements"] == after_coverage["requirements"]
        and after_coverage["placed"] >= before_coverage["placed"]
        and after_coverage["missing"] <= before_coverage["missing"]
        and after_coverage["unsupported"] <= before_coverage["unsupported"]
        and after_coverage["unverifiable"] <= before_coverage["unverifiable"]
    )
    parity = {
        "missing": expand(before_semantics - after_semantics),
        "added": expand(after_semantics - before_semantics),
        "renamed_or_reordered": sorted(
            name
            for name in set(before_annotations) & set(after_annotations)
            if before_annotations[name] != after_annotations[name]
        ),
        "coverage_equal": before["coverage"] == after["coverage"],
        "drops_equal": before["drops"] == after["drops"],
        "coverage_no_worse": coverage_no_worse,
        "same_sheet": same_sheet,
        "introduced_blockers": introduced_blockers,
        "candidate_interior_dimensions": after["interior_dimensions"],
        "introduced_interior_dimensions": expand(after_interiors - before_interiors),
        "obsolete_nts_caption_removed": bool(
            iso_note_obsolete
            and any(
                annotation["type"] == "Note" and annotation["label"] == "ISO VIEW (NTS)"
                for annotation in before_annotations.values()
            )
            and not any(
                annotation["type"] == "Note" and annotation["label"] == "ISO VIEW (NTS)"
                for annotation in after_annotations.values()
            )
        ),
    }
    parity["passed"] = (
        not any(
            (
                parity["missing"],
                parity["introduced_interior_dimensions"],
            )
        )
        and parity["coverage_no_worse"]
        and parity["same_sheet"]
        and not parity["introduced_blockers"]
        and not (
            iso_note_obsolete
            and any(
                annotation["type"] == "Note" and annotation["label"] == "ISO VIEW (NTS)"
                for annotation in after_annotations.values()
            )
        )
    )
    baseline_key = tuple(before["arrangement_quality"]["selection_key"])
    candidate_key = tuple(after["arrangement_quality"]["selection_key"])

    def area(bounds):
        if bounds is None:
            return 0.0
        return max(0.0, bounds[2] - bounds[0]) * max(0.0, bounds[3] - bounds[1])

    baseline_iso_area = area(before.get("iso_bounds"))
    iso_area_ratio = (
        area(after.get("iso_bounds")) / baseline_iso_area if baseline_iso_area else 0.0
    )
    orthographic_bounds_equal = before.get("orthographic_bounds") == after.get(
        "orthographic_bounds"
    )
    iso_legibility_gain = (
        not any(baseline_key[:5])
        and candidate_key == baseline_key
        and orthographic_bounds_equal
        and iso_area_ratio >= 1.10
    )
    if not parity["passed"]:
        verdict = "ineligible"
    elif candidate_key < baseline_key:
        verdict = "candidate"
    elif baseline_key < candidate_key:
        verdict = "baseline"
    elif iso_legibility_gain:
        verdict = "candidate"
    else:
        verdict = "tie"
    return {
        "baseline": baseline,
        "candidate": candidate,
        "parity": parity,
        "quality_comparison": {
            "verdict": verdict,
            "baseline_key": baseline_key,
            "candidate_key": candidate_key,
            "reason": "larger_iso" if verdict == "candidate" and iso_legibility_gain else None,
            "iso_area_ratio": iso_area_ratio,
        },
    }


def select_best_annotation_layout(
    baseline: Drawing,
    build_candidate: Callable[[AnnotationLayoutProfile], Drawing],
) -> Drawing:
    """Keep the baseline unless a same-sheet candidate proves a strict gain."""

    baseline_record = drawing_record(baseline)
    baseline_key = baseline_record["manifest"]["arrangement_quality"]["selection_key"]
    if not any(baseline_key[:5]):
        names = ["iso-growth"]
    else:
        interiors = baseline_record["manifest"]["interior_dimensions"]
        # A required-content loss deserves the planned trial first: CTC01's
        # columns layout only removes crossings, while planned restores a blocker
        # and two interior dimensions. When hard layout defects already lead,
        # existing interior dimensions make columns the cheaper semantic match.
        names = ["planned", "legacy-depth"]
        if interiors:
            if baseline_key[0] > 0:
                names.insert(0, "columns")
            else:
                names.append("columns")
    selected = baseline
    selected_trial = None
    selected_key = baseline_key
    trials: list[dict[str, object]] = []
    for name in names:
        try:
            candidate = build_candidate(candidate_profile(name, baseline.scale))
        except Exception as error:
            trials.append({"name": name, "verdict": "build_failed", "error": str(error)})
            continue
        result = _compare(baseline_record, drawing_record(candidate))
        quality = result["quality_comparison"]
        trials.append(
            {
                "name": name,
                "verdict": quality["verdict"],
                "semantic_parity": result["parity"]["passed"],
                "missing_annotations": len(result["parity"]["missing"]),
                "quality_key": quality["candidate_key"],
                "iso_area_ratio": quality["iso_area_ratio"],
            }
        )
        if quality["verdict"] == "candidate":
            selected = candidate
            selected_trial = name
            selected_key = quality["candidate_key"]
            break
    selected.annotation_scheme_decision = {
        **selected.annotation_scheme_decision,
        "status": "candidate" if selected_trial else "retained_baseline",
        "influenced_layout": selected_trial is not None,
        "policy": "best",
        "selected_trial": selected_trial,
        "baseline_quality_key": baseline_key,
        "selected_quality_key": selected_key,
        "trials": trials,
    }
    return selected
