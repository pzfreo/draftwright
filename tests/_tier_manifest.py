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
    "drawing_diagnostics": ContractGroup(
        ("src/draftwright/drawing_diagnostics.py",),
        (
            "test_drawing_encapsulation.py",
            "test_issue_1147_legibility_pair_aggregation.py",
            "test_quality_components.py",
            "test_report_projection.py",
            "test_report_writer.py",
        ),
    ),
    "drawing_edits": ContractGroup(
        ("src/draftwright/drawing_edits.py", "src/draftwright/intent_routing.py"),
        (
            "test_add_dimension.py",
            "test_canonical_angles.py",
            "test_compiled_plan_boundary.py",
            "test_deferred_edits.py",
            "test_feature_edit_corridors.py",
            "test_feature_edit_replay.py",
            "test_feature_edit_verbs.py",
            "test_issue_563_placement_intent.py",
            "test_issue_1613_circular_channel.py",
            "test_refactor_golden.py",
            "test_script_detail_parity.py",
        ),
    ),
    "scale_policy": ContractGroup(
        (
            "src/draftwright/build_policy.py",
            "src/draftwright/explicit_scale.py",
        ),
        (
            "test_issue_1146_scale_completeness.py",
            "test_issue_1299_page_escalation.py",
            "test_arrangement_gate.py",
            "test_scale_selection.py",
        ),
    ),
    "pmi_support": ContractGroup(
        (
            "src/draftwright/pmi.py",
            "src/draftwright/_pmi_support_blockers.py",
        ),
        (
            "test_issue_1209_linear_pmi_witnesses.py",
            "test_pmi.py",
            "test_pmi_gtol_lowering.py",
            "test_pmi_records.py",
        ),
    ),
    "double_d_evidence": ContractGroup(
        ("src/draftwright/evaluation/_double_d_evidence.py",),
        ("test_issue_1370_double_d_completeness_evidence.py",),
    ),
    "turned_step_evidence": ContractGroup(
        ("src/draftwright/evaluation/_turned_step_evidence.py",),
        ("test_issue_1374_turned_step_completeness_evidence.py",),
    ),
    "pocket_evidence": ContractGroup(
        ("src/draftwright/evaluation/_pocket_evidence.py",),
        (
            "test_pocket_completeness_evidence.py",
            "test_pocket_pattern_completeness_evidence.py",
        ),
    ),
    "prismatic_evidence": ContractGroup(
        ("src/draftwright/evaluation/_prismatic_evidence.py",),
        (
            "test_pad_completeness_evidence.py",
            "test_issue_1373_plate_completeness_evidence.py",
        ),
    ),
    "groove_evidence": ContractGroup(
        ("src/draftwright/evaluation/_groove_evidence.py",),
        ("test_groove_completeness_evidence.py",),
    ),
    "edge_profile_evidence": ContractGroup(
        ("src/draftwright/evaluation/_edge_profile_evidence.py",),
        (
            "test_issue_1374_chamfer_completeness_evidence.py",
            "test_issue_1374_fillet_completeness_evidence.py",
        ),
    ),
    "flat_polygonal_evidence": ContractGroup(
        ("src/draftwright/evaluation/_flat_polygonal_evidence.py",),
        (
            "test_issue_1371_flat_completeness_evidence.py",
            "test_issue_1371_polygonal_stock_completeness_evidence.py",
            "test_polygonal_boss_completeness_evidence.py",
        ),
    ),
    "hole_family_evidence": ContractGroup(
        ("src/draftwright/evaluation/_hole_family_evidence.py",),
        (
            "test_issue_1202_observed_drawing_consumer.py",
            "test_issue_1369_hole_completeness_evidence.py",
            "test_issue_1370_countersink_completeness_evidence.py",
            "test_issue_1370_hole_pattern_completeness_evidence.py",
        ),
    ),
    "step_observers": ContractGroup(
        ("src/draftwright/evaluation/step_analysis.py",),
        (
            "test_step_analysis_evaluation.py",
            "test_issue_1369_hole_completeness_evidence.py",
            "test_issue_1370_countersink_completeness_evidence.py",
            "test_issue_1370_double_d_completeness_evidence.py",
            "test_issue_1370_hole_pattern_completeness_evidence.py",
            "test_issue_1371_flat_completeness_evidence.py",
            "test_issue_1371_polygonal_stock_completeness_evidence.py",
            "test_polygonal_boss_completeness_evidence.py",
            "test_groove_completeness_evidence.py",
            "test_issue_1374_chamfer_completeness_evidence.py",
            "test_issue_1374_fillet_completeness_evidence.py",
            "test_issue_1374_turned_step_completeness_evidence.py",
            "test_pad_completeness_evidence.py",
            "test_issue_1373_plate_completeness_evidence.py",
            "test_pocket_completeness_evidence.py",
            "test_pocket_pattern_completeness_evidence.py",
        ),
    ),
    "through_step_placement": ContractGroup(
        (
            "src/draftwright/annotations/from_model.py",
            "src/draftwright/annotations/_step_lengths.py",
        ),
        (
            "test_through_step_semantics.py",
            "test_turned_lengths.py",
            "test_issue_1505_short_axial_chains.py",
            "test_issue_1357_plural_turned_profiles.py",
        ),
    ),
    "solve_trace": ContractGroup(
        ("src/draftwright/annotations/solve_trace.py",),
        ("test_solve_trace.py", "test_compiled_plan_boundary.py"),
    ),
    "angular_ink": ContractGroup(
        ("src/draftwright/annotations/angular.py", "src/draftwright/linting/angular.py"),
        (
            "test_angular_references.py",
            "test_angular_rendering.py",
            "test_issue_1177_angular_dimensions.py",
        ),
    ),
    "dimension_ink": ContractGroup(
        ("src/draftwright/annotations/_dimension_ink.py",),
        (
            "test_pitch_dim_footprint.py",
            "test_issue_1334_prevent_ink.py",
            "test_issue_1516_dimension_text.py",
            "test_issue_1601_flat_precision.py",
        ),
    ),
    "dimension_ink_repair": ContractGroup(
        ("src/draftwright/annotations/_dimension_ink_repair.py",),
        (
            "test_issue_1334_prevent_ink.py",
            "test_issue_1333_bounded_ink_repair.py",
            "test_remaining_ink_overlaps.py",
        ),
    ),
    "placement_geometry": ContractGroup(
        ("src/draftwright/annotations/_placement_geometry.py",),
        (
            "test_leader_footprint.py",
            "test_occupancy_boxes.py",
            "test_strip_layout.py",
            "test_interior_label_placement.py",
            "test_issue_740_leader_assignment.py",
        ),
    ),
    "strip_postsolve": ContractGroup(
        ("src/draftwright/annotations/_strip_postsolve.py",),
        ("test_gdt_ink_shared.py", "test_strip_layout.py", "test_solve_trace.py"),
    ),
    "feature_leader_assignment": ContractGroup(
        ("src/draftwright/annotations/leaders.py",),
        (
            "test_feature_leader_candidate_regions.py",
            "test_issue_740_leader_assignment.py",
            "test_issue_798_floor_cardinality.py",
            "test_issue_1166_cross_pass_feature_leaders.py",
        ),
    ),
    "leader_fixed_ink": ContractGroup(
        ("src/draftwright/annotations/_leader_fixed_ink.py",),
        (
            "test_issue_1166_cross_pass_feature_leaders.py",
            "test_issue_1534_build_progress.py",
            "test_issue_798_silhouette_lint.py",
        ),
    ),
    "hole_locations": ContractGroup(
        ("src/draftwright/annotations/hole_locations.py",),
        (
            "test_lint_summary.py",
            "test_location_dimensions.py",
            "test_location_vocabulary.py",
        ),
    ),
    "oriented_slot_geometry": ContractGroup(
        ("src/draftwright/model/oriented_slot_geometry.py",),
        ("test_issue_1432_oriented_slot_semantics.py",),
    ),
    "ir_foundation": ContractGroup(
        ("src/draftwright/model/ir_foundation.py",),
        ("test_issue_1298_manufacturing_requirements.py",),
    ),
    "slot_rendering": ContractGroup(
        ("src/draftwright/annotations/_slots.py",),
        (
            "test_feature_edit_corridors.py",
            "test_issue_885_prismatic_coverage.py",
            "test_issue_1599_duplicate_slot_widths.py",
            "test_layout_override_lane_issue_1757.py",
            "test_pad_rendering.py",
            "test_slot_completeness.py",
            "test_slot_pattern.py",
            "test_slot_recognition.py",
            "test_tolerances.py",
        ),
    ),
    "pocket_pad_leaders": ContractGroup(
        ("src/draftwright/annotations/_pocket_pad.py",),
        (
            "test_pad_rendering.py",
            "test_tolerances.py",
            "test_refactor_golden.py",
            "test_issue_740_leader_assignment.py",
            "test_issue_1166_cross_pass_feature_leaders.py",
            "test_interior_label_placement.py",
        ),
    ),
    "edge_callouts": ContractGroup(
        ("src/draftwright/annotations/_edge_callouts.py",),
        (
            "test_machined_feature_callouts.py",
            "test_issue_1433_blend_semantics.py",
            "test_issue_1254_turned_chamfers.py",
            "test_issue_1281_turned_fillets.py",
            "test_issue_1374_chamfer_completeness_evidence.py",
            "test_issue_1374_fillet_completeness_evidence.py",
            "test_tolerances.py",
            "test_refactor_golden.py",
            "test_script_detail_parity.py",
            "test_issue_1308_machined_leader_analytics.py",
            "test_interior_label_placement.py",
        ),
    ),
    "thin_profiles": ContractGroup(
        ("src/draftwright/annotations/_thin_profiles.py",),
        (
            "test_prismatic_dimensions.py",
            "test_issue_917_open_channel.py",
            "test_tolerances.py",
            "test_refactor_golden.py",
            "test_script_detail_parity.py",
        ),
    ),
    "diameter_family": ContractGroup(
        ("src/draftwright/annotations/_diameters.py",),
        (
            "test_diameter_leaders.py",
            "test_prismatic_boss_diameter.py",
            "test_issue_1505_diameter_coverage.py",
            "test_turned_steps.py",
            "test_issue_798_material_field.py",
            "test_tolerances.py",
            "test_refactor_golden.py",
            "test_compiled_plan_boundary.py",
        ),
    ),
    "location_family": ContractGroup(
        ("src/draftwright/annotations/_locations.py",),
        (
            "test_location_dimensions.py",
            "test_issue_1613_circular_channel.py",
            "test_dynamic_corridors.py",
            "test_feature_edit_corridors.py",
            "test_tolerances.py",
            "test_compiled_plan_boundary.py",
            "test_refactor_golden.py",
        ),
    ),
    "gdt_family": ContractGroup(
        ("src/draftwright/annotations/_gdt.py",),
        (
            "test_gdt_placement.py",
            "test_gdt_ink_shared.py",
            "test_document_build.py",
            "test_sheet_gdt.py",
            "test_sheet_notes.py",
            "test_issue_1276_turned_leader_targets.py",
            "test_issue_1352_searchable_pdf_text.py",
            "test_declare.py",
            "test_compiled_plan_boundary.py",
            "test_refactor_golden.py",
            "test_solve_trace.py",
        ),
    ),
    "sheet_layout_controls": ContractGroup(
        ("src/draftwright/sheet_layout_controls.py",),
        (
            "test_layout_override_lane_issue_1757.py",
            "test_layout_override_side_issue_1757.py",
            "test_sheet_identity_invariant.py",
            "test_declare.py",
        ),
    ),
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
            "src/draftwright/intent_routing.py",
            "src/draftwright/measurement_support.py",
            "src/draftwright/sheet.py",
            "src/draftwright/sheet_features.py",
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
            "test_sheet_identity_invariant.py",
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
    "intent_drain": ContractGroup(
        ("src/draftwright/intent_drain.py",),
        (
            "test_detail_views.py",
            "test_pocket_pattern.py",
            "test_slot_pattern.py",
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
            "test_pmi_records.py",
            "test_requirement_catalog.py",
            "test_report_projection.py",
            "test_report_writer.py",
            "test_replay_assessment_issue_1715.py",
        ),
    ),
    "drawing_tables": ContractGroup(
        ("src/draftwright/drawing_tables.py",),
        (
            "test_hole_table.py",
            "test_sheet_tables.py",
            "test_issue_1144_transactional_hole_table.py",
        ),
    ),
    "export": ContractGroup(
        (
            "src/draftwright/cli.py",
            "src/draftwright/drawing.py",
            "src/draftwright/drawing_export.py",
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
            "test_issue_1533_review_diagnostics.py",
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
