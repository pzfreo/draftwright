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
        ("src/draftwright/drawing.py", "src/draftwright/intent_routing.py"),
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
            "test_pattern_contract.py",
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
    "annotation_layout_selection": ContractGroup(
        (
            "src/draftwright/annotation_layout_profile.py",
            "src/draftwright/layout_selection.py",
        ),
        ("test_annotation_layout_product.py",),
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
            "src/draftwright/annotations/_axial_render.py",
        ),
        (
            "test_through_step_semantics.py",
            "test_turned_lengths.py",
            "test_issue_1505_short_axial_chains.py",
            "test_issue_1357_plural_turned_profiles.py",
        ),
    ),
    "height_ladder": ContractGroup(
        (
            "src/draftwright/annotations/_height_ladder.py",
            "src/draftwright/annotations/_axial_render.py",
        ),
        (
            "test_compiled_plan_boundary.py",
            "test_location_dimensions.py",
            "test_issue_1466_semantic_sides.py",
            "test_strip_layout.py",
            "test_deferred_edits.py",
            "test_refactor_golden.py",
            "test_solve_trace.py",
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
            "test_flat_callout_precision.py",
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
        (
            "src/draftwright/annotations/leaders.py",
            "src/draftwright/annotations/_leader_candidates.py",
        ),
        (
            "test_feature_leader_candidate_regions.py",
            "test_issue_740_leader_assignment.py",
            "test_issue_798_floor_cardinality.py",
            "test_issue_1166_cross_pass_feature_leaders.py",
        ),
    ),
    "hole_leader_placement": ContractGroup(
        (
            "src/draftwright/annotations/holes.py",
            "src/draftwright/annotations/hole_leader_candidates.py",
            "src/draftwright/annotations/_hole_leader_placement.py",
        ),
        (
            "test_feature_leader_candidate_regions.py",
            "test_hole_annotations.py",
            "test_hole_pattern_callouts.py",
            "test_issue_1142_hole_leader_labels.py",
        ),
    ),
    "machined_leader_lowering": ContractGroup(
        ("src/draftwright/annotations/_machined_leaders.py",),
        (
            "test_feature_leader_candidate_regions.py",
            "test_issue_740_leader_assignment.py",
            "test_issue_1308_machined_leader_analytics.py",
            "test_issue_1166_cross_pass_feature_leaders.py",
            "test_refactor_golden.py",
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
            "test_requirement_dimension_sharing.py",
            "test_layout_override_lane_issue_1757.py",
            "test_pad_rendering.py",
            "test_pattern_contract.py",
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
            "test_pattern_contract.py",
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
    "pmi_dimensions": ContractGroup(
        ("src/draftwright/annotations/_pmi_dimensions.py",),
        (
            "test_pmi.py",
            "test_issue_1209_linear_pmi_witnesses.py",
            "test_issue_1296_cylindrical_diameter_pmi.py",
            "test_issue_1177_angular_dimensions.py",
            "test_sheet_fallback_issue_1797.py",
            "test_refactor_golden.py",
            "test_compiled_plan_boundary.py",
            "test_audit_differential.py",
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
            "src/draftwright/model/detect_inventory.py",
            "src/draftwright/model/detect_ownership.py",
            "src/draftwright/*_contract.py",
        ),
        (
            "test_boss_ownership.py",
            "test_declared_recognition_gate.py",
            "test_external_recognition_boundary.py",
            "test_gusset_rib_semantics.py",
            "test_issue_1357_plural_turned_profiles.py",
            "test_issue_1433_blend_semantics.py",
            "test_issue_1596_bolt_circle_corroboration.py",
            "test_issue_1612_frame_sergio_coverage.py",
            "test_nested_recognition_ownership.py",
            "test_occurrence_recognition_ownership.py",
            "test_part_classification.py",
            "test_part_model.py",
            "test_recogniser_capabilities.py",
            "test_recogniser_contract.py",
            "test_recogniser_policy.py",
            "test_recognition_boundary_failures.py",
            "test_recognition_evidence_schema.py",
            "test_recognition_result.py",
            "test_slot_recognition.py",
            "test_turned_step_ownership.py",
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
            "test_pattern_contract.py",
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
            "test_sheet_section.py",
            "test_strip_layout.py",
            "test_strip_zones.py",
            "test_two_pass_layout.py",
            "test_view_plan.py",
        ),
    ),
    "section_rendering": ContractGroup(
        ("src/draftwright/annotations/sections.py",),
        (
            "test_detail_views.py",
            "test_script_detail_parity.py",
            "test_section_hatching.py",
            "test_sheet_section.py",
            "test_issue_1190_section_decision.py",
            "test_issue_1530_section_provenance.py",
            "test_issue_1604_internal_section.py",
            "test_through_step_semantics.py",
            "test_issue_1166_cross_pass_feature_leaders.py",
            "test_issue_1215_envelope_tolerance.py",
            "test_audit_differential.py",
        ),
    ),
    "hole_pattern_rendering": ContractGroup(
        ("src/draftwright/annotations/holes.py",),
        ("test_pattern_contract.py",),
    ),
    "intent_drain": ContractGroup(
        ("src/draftwright/intent_drain.py",),
        (
            "test_detail_views.py",
            "test_pattern_contract.py",
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
            "test_issue_1215_no_approved_tolerance_is_dropped.py",
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

# Narrow source owners for fast modules outside the shared change-area contracts above.
# These do not expand the broad-source fallback on every Linux compatibility leg.
ADDITIONAL_SOURCE_CONTRACTS = {
    "src/draftwright/_build_profile.py": ("test_build_profile_harness.py",),
    "src/draftwright/_core.py": ("test_formatting.py",),
    "src/draftwright/_warnings.py": ("test_scale_policy.py",),
    "src/draftwright/analysis.py": (
        "test_analysis_geometry.py",
        "test_auxiliary_layout.py",
        "test_detect_once.py",
        "test_diameter_deduplication.py",
        "test_issue_1396_layout_advisories.py",
        "test_prismatic_classification.py",
        "test_repack_geometry_seam.py",
        "test_shared_box_memo.py",
    ),
    "src/draftwright/annotation_layout_profile.py": (
        "test_layout_scheme.py",
        "test_view_blocks.py",
    ),
    "src/draftwright/annotations/_axial_render.py": ("test_issue_443_grm03_axial_coverage.py",),
    "src/draftwright/annotations/_envelope.py": (
        "test_envelope_view_route_issue_1813.py",
        "test_issue_1000_envelope_reuse.py",
    ),
    "src/draftwright/annotations/_pocket_pad.py": ("test_issue_916_pocket_leader.py",),
    "src/draftwright/annotations/balloons.py": ("test_balloon_ring_standoff.py",),
    "src/draftwright/annotations/from_model.py": (
        "test_fits.py",
        "test_issue_1058_wheel_profile.py",
        "test_issue_1349_precision_display.py",
        "test_issue_1360_thread_depth.py",
        "test_issue_1392_recognisers_048.py",
        "test_issue_1524_tolerance_magnitude.py",
        "test_render_seam.py",
        "test_suppression_marks.py",
    ),
    "src/draftwright/annotations/gears.py": ("test_issue_1086_declared_gears.py",),
    "src/draftwright/annotations/holes.py": ("test_issue_367_leader_ink.py",),
    "src/draftwright/annotations/leaders.py": (
        "test_issue_1187_unroutable_leaders.py",
        "test_issue_1188_per_view_assignment.py",
    ),
    "src/draftwright/annotations/orchestrator.py": (
        "test_iso_nts_caption_placement.py",
        "test_issue_1593_title_block_keepout.py",
        "test_layout_constants.py",
        "test_typ_dimensions.py",
    ),
    "src/draftwright/builder.py": (
        "test_issue_1155_grm04_sheet_use.py",
        "test_repack_geometry_seam.py",
        "test_scale_policy.py",
    ),
    "src/draftwright/cli.py": (
        "test_cli_report_sidecar.py",
        "test_format_selector.py",
        "test_issue_1514_projection_safeguard.py",
    ),
    "src/draftwright/compose.py": (
        "test_adr0018_view_selection.py",
        "test_annotation_box_composition.py",
        "test_annotation_box_corpus.py",
        "test_depth_estimators.py",
        "test_iso_layout.py",
        "test_issue_1082_polygonal_stock.py",
        "test_issue_1216_callout_reservation_measures_ink.py",
        "test_issue_1395_degenerate_iso_region.py",
        "test_issue_1612_sheet_margins.py",
        "test_pre_render_profile_choice.py",
    ),
    "src/draftwright/document.py": ("test_issue_1353_cnc_authoritative_workflow.py",),
    "src/draftwright/document_evidence.py": (
        "test_document_claim_authority.py",
        "test_requirement_carriers.py",
    ),
    "src/draftwright/drawing.py": (
        "test_drawing_operations.py",
        "test_feature_queries.py",
        "test_issue_1154_one_owner_per_measurement.py",
        "test_issue_909_sloped_profile.py",
        "test_note_verb.py",
        "test_title_block_units_format.py",
    ),
    "src/draftwright/explicit_scale.py": ("test_issue_1338_scale_before_page_escalation.py",),
    "src/draftwright/fits.py": ("test_fits.py",),
    "src/draftwright/layout.py": ("test_layout_property.py",),
    "src/draftwright/layout_safety.py": ("test_layout_safety.py",),
    "src/draftwright/layout_scheme.py": (
        "test_layout_scheme.py",
        "test_pre_render_profile_choice.py",
    ),
    "src/draftwright/leader_policy.py": ("test_leader_region_policy.py",),
    "src/draftwright/layout_selection.py": (
        "test_annotation_scheme_compare_script.py",
        "test_candidate_preview_sheet_holdout.py",
    ),
    "src/draftwright/linting/_registry.py": ("test_issue_1229_ledger_member_keying.py",),
    "src/draftwright/linting/angled_step_coverage.py": (
        "test_issue_1247_angled_step_disposition.py",
    ),
    "src/draftwright/linting/angular.py": ("test_angle_patterns.py",),
    "src/draftwright/linting/blend_coverage.py": ("test_issue_1479_radius_leader_targets.py",),
    "src/draftwright/linting/circular_blind_step_coverage.py": (
        "test_circular_blind_step_semantics.py",
        "test_framed_step_family_evidence.py",
    ),
    "src/draftwright/linting/coverage.py": (
        "test_issue_1132_turned_profile_span_is_reported.py",
        "test_issue_1176_quality_fidelity.py",
        "test_issue_1245_passage_disposition.py",
        "test_issue_1246_prismatic_pocket_disposition.py",
        "test_issue_1450_boss_blend_ownership.py",
    ),
    "src/draftwright/linting/evidence.py": (
        "test_equivalent_dimension_presentations_issue_1759.py",
        "test_issue_1217_claimed_representations.py",
        "test_issue_1217_the_facility_is_shared.py",
        "test_issue_1219_location_claims_its_own_axis.py",
        "test_issue_1543_feature_schedules.py",
    ),
    "src/draftwright/linting/flat_coverage.py": ("test_flat_completeness.py",),
    "src/draftwright/linting/hole_coverage.py": (
        "test_issue_1143_hole_completeness.py",
        "test_issue_1351_structured_note_coverage.py",
        "test_issue_1378_diameter_leader_targets.py",
        "test_issue_1531_semantic_hole_counts.py",
    ),
    "src/draftwright/linting/ink_overlap.py": ("test_repair.py",),
    "src/draftwright/linting/issues.py": (
        "test_blind_slot_completeness.py",
        "test_issue_1397_unverifiable_requirement_message.py",
        "test_issue_883_location_identity.py",
        "test_paired_ramp_semantics.py",
    ),
    "src/draftwright/linting/oriented_slot_coverage.py": (
        "test_issue_1432_adoption.py",
        "test_issue_1432_boundary_failures.py",
    ),
    "src/draftwright/linting/paired_ramp_step_coverage.py": ("test_paired_ramp_semantics.py",),
    "src/draftwright/linting/pmi_coverage.py": ("test_manufacturing_schedule.py",),
    "src/draftwright/linting/pocket_coverage.py": (
        "test_issue_1485_curved_pockets.py",
        "test_issue_1485_rounded_pockets.py",
    ),
    "src/draftwright/linting/quality.py": (
        "test_e2e_standards.py",
        "test_issue_1126_requesting_less_never_scores_better.py",
        "test_typed_remediation_projection_issue_1759.py",
    ),
    "src/draftwright/linting/requirements.py": (
        "test_requirement_applicability.py",
        "test_requirement_catalog_families.py",
    ),
    "src/draftwright/linting/schedule_evidence.py": ("test_issue_1543_schedule_requirements.py",),
    "src/draftwright/linting/structural.py": (
        "test_issue_1153_contradictory_dimensions.py",
        "test_issue_1196_deterministic_view_names.py",
        "test_issue_1204_multiscale_view_issues.py",
        "test_issue_1216_pairwise_across_scale_groups.py",
        "test_layout_evidence_issue_1711.py",
        "test_lint_box_cache.py",
        "test_lint_reconciliation.py",
        "test_lint_structural.py",
    ),
    "src/draftwright/linting/suggest.py": ("test_lint_suggestions.py",),
    "src/draftwright/model/compiled.py": (
        "test_blind_slot_semantics.py",
        "test_issue_1511_turned_boss_heights.py",
        "test_issue_1517_through_indicator.py",
        "test_issue_1560_envelope_axes.py",
        "test_issue_915_dense_case.py",
        "test_issue_924_zero_length_shoulders.py",
        "test_issue_955_height_contingency.py",
        "test_overall_height_coverage.py",
        "test_suppression_ledger.py",
    ),
    "src/draftwright/model/declare.py": (
        "test_declaration_identity_issue_1710.py",
        "test_flat_stock_identity.py",
        "test_geometry_graph_spike.py",
        "test_grid_lattice_convention.py",
        "test_issue_676_polygonal_boss_declare.py",
        "test_object_aspects.py",
        "test_slanted_blind_step.py",
    ),
    "src/draftwright/model/detect.py": (
        "test_candidate_evidence_issue_1726.py",
        "test_channel_ownership.py",
        "test_detect_registry.py",
        "test_feature_provenance.py",
        "test_groove_profile.py",
        "test_issue_1073_cross_body_patterns.py",
        "test_pattern_ownership.py",
        "test_pmi_common_labels.py",
        "test_pocket_pattern_recognition.py",
        "test_section_recess_pockets.py",
        "test_slot_pattern_recognition.py",
    ),
    "src/draftwright/model/detect_inventory.py": ("test_recogniser_step_inventory.py",),
    "src/draftwright/model/dimension_intent.py": ("test_dimension_intent_contract.py",),
    "src/draftwright/model/ir.py": (
        "test_dimension_role_vocabulary.py",
        "test_issue_1006_measurement_comparison.py",
        "test_issue_1116_ap242_hole_tolerance_lowering.py",
        "test_issue_1325_nominal_agreement.py",
        "test_issue_1357_pmi_frame.py",
        "test_issue_676_polygonal_boss.py",
        "test_parameter_id.py",
        "test_turned_diameters.py",
    ),
    "src/draftwright/model/manufacturing_schedule.py": ("test_manufacturing_schedule.py",),
    "src/draftwright/model/pmi_lowering.py": ("test_nominal_step_pmi.py",),
    "src/draftwright/pmi.py": (
        "test_issue_1336_grm03_ap242_pmi_fixture.py",
        "test_issue_1563_sheet_pmi_reconciliation.py",
        "test_pmi_datum_lowering.py",
    ),
    "src/draftwright/profile_angles.py": ("test_profile_angles.py",),
    "src/draftwright/projection.py": (
        "test_drawing_state.py",
        "test_iso_detail_growth.py",
        "test_isometric_orientation.py",
        "test_isometric_placement.py",
        "test_issue_1240_iso_above_strip_visibility.py",
        "test_issue_1260_view_constraints.py",
        "test_view_coordinates.py",
    ),
    "src/draftwright/recogniser_contract.py": ("test_recognition_policy_dispositions.py",),
    "src/draftwright/recognition_frame.py": (
        "test_issue_1357_framed_activation.py",
        "test_issue_1357_framed_boundary.py",
    ),
    "src/draftwright/recognition_ownership.py": (
        "test_plate_ownership.py",
        "test_through_step_ownership.py",
    ),
    "src/draftwright/repair.py": (
        "test_issue_1153_contradictory_dimensions.py",
        "test_repair.py",
        "test_witness_label_reconciliation.py",
    ),
    "src/draftwright/reporting.py": (
        "test_declaration_report_issue_1710.py",
        "test_declared_report_issue_1688.py",
        "test_document_report_authority.py",
        "test_issue_1460_step_inspection.py",
        "test_report_status_reasons_issue_1618.py",
    ),
    "src/draftwright/score.py": ("test_score.py",),
    "src/draftwright/section_recess_contract.py": (
        "test_issue_1485_quiddity_024_boundary.py",
        "test_issue_1598_hex_pockets.py",
        "test_issue_958_cross_solid_recognition.py",
        "test_section_recess_contract.py",
    ),
    "src/draftwright/sheet.py": (
        "test_sheet_of.py",
        "test_soft_deprecation.py",
    ),
    "src/draftwright/sheet_emit.py": (
        "test_issue_1157_no_implicit_general_tolerance.py",
        "test_issue_1536_title_overflow.py",
        "test_issue_1585_title_block_date.py",
        "test_projection_symbol_default.py",
        "test_sheet_furniture.py",
        "test_sheet_generation_snapshot.py",
    ),
    "src/draftwright/view_plan.py": (
        "test_adr0018_view_routing.py",
        "test_derived_view_integration.py",
        "test_issue_1130_view_planning_evidence.py",
        "test_issue_1262_automatic_turned_views.py",
        "test_issue_1350_detected_takeover.py",
        "test_issue_1515_first_angle.py",
        "test_issue_1518_explicit_rear.py",
    ),
}

# These coordinators cross the area boundaries above. A change to one must run every group.
BROAD_SOURCE_PATTERNS = (
    "src/draftwright/_core.py",
    "src/draftwright/analysis.py",
    "src/draftwright/annotations/orchestrator.py",
    "src/draftwright/builder.py",
    "src/draftwright/drawing.py",
    "src/draftwright/sheet.py",
)

# These run on every code-bearing pull request before any change-area expansion.
PR_CORE_MODULES = frozenset(
    {
        "test_agents_guide.py",
        "test_build_drawing_entrypoints.py",
        "test_compiled_plan_boundary.py",
        "test_declared_recognition_gate.py",
        "test_dense_sheet_canary.py",
        "test_e2e_slice.py",
        "test_export_formats.py",
        "test_fillets_adjacency.py",
        "test_flat_stock_opposition.py",
        "test_issue_1532_authored_section_examples.py",
    }
)

# Repository-policy guards scan source, tests, history, or CI configuration rather than one
# production unit. They remain mandatory on every PR without lengthening the inner unit loop.
PR_POLICY_MODULES = frozenset(
    {
        "test_adr_corpus.py",
        "test_annotation_scheme_corpus_script.py",
        "test_annotation_scheme_shadow_script.py",
        "test_api_docs.py",
        "test_architecture_docs.py",
        "test_carve_free_position_callers.py",
        "test_clone_budget.py",
        "test_coverage_mode.py",
        "test_deprecation_dates.py",
        "test_import_boundaries.py",
        "test_private_test_attr_reads.py",
        "test_private_test_imports.py",
        "test_quiddity_lifecycle_boundary.py",
        "test_real_part_fixtures.py",
        "test_shared_drawing_cache.py",
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

# These modules have no fast tests. Keep each exception explicit so a new fast module
# cannot silently escape the PR selector.
NONFAST_ONLY_MODULES = {
    "test_agent_improvement_canary_issue_1716.py": "slow real-part canary",
    "test_dimension_lane_canary_issue_1757.py": "slow real-part canary",
    "test_issue_1544_frame_document_canary.py": "slow real-part canary",
    "test_issue_1555_turned_recognition_format_invariance.py": "slow STEP fixture builds",
    "test_issue_827_real_part_canary.py": "slow real-part canary",
    "test_layout_hypothesis.py": "slow generated geometry property test",
    "test_pareto_loop_canary_issue_1753.py": "slow real-part canary",
}

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
    for path in changed_paths:
        modules.update(ADDITIONAL_SOURCE_CONTRACTS.get(path, ()))
    missing = modules - available
    if missing:
        raise ValueError(f"tier manifest names missing test modules: {sorted(missing)}")
    return sorted(modules)
