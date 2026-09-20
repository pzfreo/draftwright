"""Executable manifests for pull-request, full, and scheduled test tiers."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from _unit_manifest import UNIT_MODULES


@dataclass(frozen=True)
class ContractGroup:
    """Production paths and the complete fast contract modules that protect them."""

    source_patterns: tuple[str, ...]
    test_patterns: tuple[str, ...]


CONTRACT_GROUPS = {
    "recognition": ContractGroup(
        (
            "src/draftwright/recogn*",
            "src/draftwright/model/detect.py",
            "src/draftwright/*_contract.py",
        ),
        (
            "test_declared_recognition_gate.py",
            "test_external_recognition_boundary.py",
            "test_nested_recognition_ownership.py",
            "test_occurrence_recognition_ownership.py",
            "test_part_classification.py",
            "test_recogniser_capabilities.py",
            "test_recogniser_contract.py",
            "test_recogniser_policy.py",
            "test_recognition_boundary_failures.py",
            "test_recognition_evidence_schema.py",
            "test_recognition_result.py",
            "test_slot_recognition.py",
        ),
    ),
    "compilation": ContractGroup(
        (
            "src/draftwright/model/*",
            "src/draftwright/intents.py",
            "src/draftwright/measurement_support.py",
            "src/draftwright/sheet.py",
            "src/draftwright/sheet_emit.py",
        ),
        (
            "test_add_dimension.py",
            "test_axial_dimensions.py",
            "test_compiled_plan_boundary.py",
            "test_declare.py",
            "test_dimension_placement_api.py",
            "test_feature_edit_replay.py",
            "test_feature_edit_verbs.py",
            "test_issue_1466_dimension_options.py",
            "test_measurement_support.py",
            "test_model_ir.py",
            "test_sheet_emit.py",
        ),
    ),
    "placement": ContractGroup(
        (
            "src/draftwright/annotations/*",
            "src/draftwright/annotate.py",
            "src/draftwright/compose.py",
            "src/draftwright/layout.py",
            "src/draftwright/projection.py",
            "src/draftwright/view_plan.py",
        ),
        (
            "test_compose_then_pack.py",
            "test_deferred_edits.py",
            "test_dynamic_corridors.py",
            "test_gdt_placement.py",
            "test_layout_cleanliness.py",
            "test_layout_generalisation.py",
            "test_place_dimension.py",
            "test_strip_layout.py",
            "test_strip_zones.py",
            "test_two_pass_layout.py",
            "test_view_plan.py",
        ),
    ),
    "reporting": ContractGroup(
        (
            "src/draftwright/audit.py",
            "src/draftwright/document*.py",
            "src/draftwright/inspection*.py",
            "src/draftwright/linting/*",
            "src/draftwright/replay_assessment.py",
            "src/draftwright/reporting.py",
        ),
        (
            "test_assessment_comparison_issue_1712.py",
            "test_audit_differential.py",
            "test_document_claims.py",
            "test_document_coverage.py",
            "test_document_report.py",
            "test_inspection.py",
            "test_lint_coverage.py",
            "test_lint_summary.py",
            "test_requirement_catalog.py",
            "test_report_projection.py",
            "test_report_writer.py",
            "test_replay_assessment_issue_1715.py",
        ),
    ),
    "export": ContractGroup(
        (
            "src/draftwright/cli.py",
            "src/draftwright/drawing.py",
            "src/draftwright/export.py",
            "src/draftwright/make_drawing.py",
        ),
        (
            "test_build_drawing_entrypoints.py",
            "test_cli_behavior.py",
            "test_export_dxf_curves.py",
            "test_export_dxf_zoom.py",
            "test_export_formats.py",
            "test_export_reproducible.py",
            "test_export_shape.py",
            "test_issue_1352_searchable_pdf_text.py",
            "test_make_drawing_entrypoints.py",
        ),
    ),
}

# These coordinators cross the area boundaries above. A change to one must run every group.
BROAD_SOURCE_PATTERNS = (
    "src/draftwright/_core.py",
    "src/draftwright/analysis.py",
    "src/draftwright/builder.py",
    "src/draftwright/drawing.py",
    "src/draftwright/sheet.py",
)

# These run on every code-bearing pull request before any change-area expansion.
PR_CORE_MODULES = frozenset(
    {
        "test_build_drawing_entrypoints.py",
        "test_compiled_plan_boundary.py",
        "test_declared_recognition_gate.py",
        "test_e2e_slice.py",
        "test_export_formats.py",
    }
)

# Repository-policy guards scan source, tests, history, or CI configuration rather than one
# production unit. They remain mandatory on every PR without lengthening the inner unit loop.
PR_POLICY_MODULES = frozenset(
    {
        "test_api_docs.py",
        "test_architecture_docs.py",
        "test_carve_free_position_callers.py",
        "test_clone_budget.py",
        "test_coverage_mode.py",
        "test_deprecation_dates.py",
        "test_import_boundaries.py",
        "test_private_test_attr_reads.py",
        "test_private_test_imports.py",
        "test_suite_shape.py",
        "test_tier_manifest.py",
        "test_version_bump_ci.py",
        "test_workflows.py",
    }
)

# A critical public contract may run in PR/full or in all tiers, but never scheduled only.
CRITICAL_CONTRACT_MODULES = frozenset(
    {
        "test_declared_recognition_gate.py",
        "test_export_formats.py",
        "test_recogniser_contract.py",
        "test_report_writer.py",
        "test_scale_selection.py",
    }
)

FULL_EXPRESSION = "not slow and not scheduled"
SCHEDULED_EXPRESSION = "slow or scheduled"


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def selected_groups(changed_paths: list[str]) -> frozenset[str]:
    """Return every contract group selected by changed production paths.

    Unknown production modules select all groups. That conservative fallback makes adding a new
    production area safe before its narrower ownership is added here.
    """
    selected: set[str] = set()
    for path in changed_paths:
        if not path.startswith("src/draftwright/") or not path.endswith(".py"):
            continue
        if _matches(path, BROAD_SOURCE_PATTERNS):
            selected.update(CONTRACT_GROUPS)
            continue
        matched = {
            name
            for name, group in CONTRACT_GROUPS.items()
            if _matches(path, group.source_patterns)
        }
        selected.update(matched or CONTRACT_GROUPS)
    return frozenset(selected)


def pr_modules(tests_dir: Path, changed_paths: list[str]) -> list[str]:
    """Return stable module names for the PR core, changed tests, and selected groups."""
    available = {path.name for path in tests_dir.glob("test_*.py")}
    modules = set(UNIT_MODULES) | set(PR_CORE_MODULES) | set(PR_POLICY_MODULES)
    modules.update(
        Path(path).name
        for path in changed_paths
        if path.startswith("tests/test_") and path.endswith(".py") and Path(path).name in available
    )
    for name in selected_groups(changed_paths):
        patterns = CONTRACT_GROUPS[name].test_patterns
        modules.update(module for module in available if _matches(module, patterns))
    missing = modules - available
    if missing:
        raise ValueError(f"tier manifest names missing test modules: {sorted(missing)}")
    return sorted(modules)
