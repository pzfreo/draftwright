//! Finding labels only: independent of drawing, planning and recovery policy.

use std::collections::{BTreeMap, BTreeSet};

use serde::Serialize;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Impact {
    Incomplete,
    Decision,
    Readability,
    Advisory,
}

#[derive(Clone, Copy)]
enum Group {
    Code,
    Feature,
    Requirement,
    Crossing,
    Source,
    Shape,
}

struct Classification {
    code: &'static str,
    impact: Impact,
    group: Group,
    #[cfg_attr(not(test), allow(dead_code))]
    reason: &'static str,
}

// ONE code → impact table. Keep sorted; the source-inventory test requires coverage and
// the user-facing documentation to agree. Severity is never modified by these labels.
macro_rules! catalogue {
    ($($code:literal => $impact:ident, $group:ident, $reason:literal;)*) => {
        const CODES: &[Classification] = &[$(Classification {
            code: $code, impact: Impact::$impact, group: Group::$group, reason: $reason,
        }),*];
    };
}
catalogue! {
    "angled_step_requirement_unsupported" => Incomplete, Requirement, "No supported drawing grammar states this feature requirement.";
    "angular_dimension_dropped" => Advisory, Code, "An automatic profile-corner angle found no clear place and is not drawn; lint still reports the corner.";
    "angular_geometry_mismatch" => Incomplete, Code, "The drawn evidence does not correctly state the required source information.";
    "angular_label_vs_geometry" => Incomplete, Code, "The printed angle does not state the measured angular sector.";
    "angular_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "angular_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "angular_support_mismatch" => Incomplete, Code, "The drawn evidence does not correctly state the required source information.";
    "annotation_edit_unapplied" => Decision, Code, "A saved annotation edit has no current item; its intent is retained.";
    "annotation_ink_overlap" => Readability, Code, "Annotation ink impairs the legibility of another annotation; leader-through-text is included.";
    "annotation_leader_crossing" => Readability, Crossing, "Leader, dimension-line or arrow crossings impair reading; extension-only crossings are advisory.";
    "annotation_leader_long" => Readability, Code, "The leader is longer than the preferred reading distance.";
    "annotation_leader_search_capped" => Advisory, Code, "The bounded leader search stopped; this reports search provenance.";
    "annotation_line_over_view" => Readability, Code, "Annotation linework overlaps a view.";
    "annotation_off_sheet" => Readability, Code, "Annotation ink extends outside the sheet.";
    "annotation_out_of_bounds" => Readability, Code, "Rendered annotation ink extends outside the drawing bounds.";
    "annotation_outside_area" => Readability, Code, "The annotation leaves its reserved view area.";
    "annotation_over_title_block" => Readability, Code, "Annotation ink overlaps the title block.";
    "annotation_over_view" => Readability, Code, "An annotation overlaps view geometry.";
    "annotation_overlap" => Readability, Code, "Annotation labels overlap each other.";
    "annular_groove_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "annular_groove_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "assembly_drawn_whole" => Advisory, Code, "The complete assembly was selected for drawing.";
    "assembly_principal_drawn" => Advisory, Code, "The assembly's largest part, holding the others, was drawn alone; the others are not drawn.";
    "authored_dimension_invalid" => Decision, Code, "The authored dimension needs a valid user definition.";
    "axial_length_missing" => Incomplete, Code, "The drawing does not state the axial length.";
    "balloon_dropped" => Incomplete, Code, "A required drawing item was dropped before it could be stated.";
    "blend_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "blend_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "boss_height_missing" => Incomplete, Code, "The drawing does not state the boss height.";
    "boss_height_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "boss_height_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "break_anchor_removed" => Incomplete, Code, "An annotation anchors in the removed span; its attachment needs an unbroken region.";
    "break_not_applied" => Decision, Code, "The requested break has no proven uniform span or carrying view.";
    "callout_dropped" => Incomplete, Code, "A required drawing item was dropped before it could be stated.";
    "chamfer_leader_target_mismatch" => Readability, Code, "The chamfer leader points at the wrong geometry.";
    "chamfer_leader_target_unverifiable" => Readability, Code, "The chamfer leader target cannot be verified.";
    "chamfer_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "chamfer_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "channel_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "channel_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "characteristic_balloon_missing" => Incomplete, Source, "A numbered characteristic has no inspection balloon while inspection is enabled.";
    "characteristic_id_ambiguous" => Incomplete, Source, "Duplicate requirement ids leave its inspection number unknown.";
    "characteristic_not_drawn" => Incomplete, Source, "A numbered characteristic has no drawn carrier.";
    "characteristic_number_conflict" => Decision, Source, "The requirement id and legacy UDA disagree; the id supplies the inspection number.";
    "circular_blind_step_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "circular_blind_step_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "circular_channel_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "circular_channel_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "claimed_value_absent" => Incomplete, Code, "The drawn evidence does not correctly state the required source information.";
    "closed_dimension_chain" => Advisory, Code, "A closed chain is redundant dimension information.";
    "coincident_bodies" => Advisory, Code, "The file holds bodies that coincide with another; one of each is drawn when every body does.";
    "correlated_set_conflict" => Decision, Code, "The authored choice splits a set that must be kept together.";
    "correlated_set_whole" => Advisory, Code, "A correlated dimension set was kept together.";
    "cross_reference_missing" => Incomplete, Code, "A required cross-sheet or derived-view reference is absent.";
    "datum_leader_not_normal" => Readability, Code, "The datum leader does not leave its triangle square to the face.";
    "datum_leader_remote" => Readability, Code, "The datum symbol is too far from its referenced surface.";
    "datum_not_drawn" => Advisory, Code, "A declared datum no frame cites and no dimension measures from is not drawn.";
    "datum_not_identified" => Incomplete, Code, "A referenced datum lacks its identifying symbol.";
    "datum_origin_unusable" => Advisory, Code, "The datum cannot define this origin; the documented fallback is used.";
    "datum_symbol_overlap" => Readability, Code, "Datum symbols overlap other annotations.";
    "datum_undeclared" => Decision, Code, "A control references a datum the user must declare.";
    "derived_view_identifier_reused" => Readability, Code, "A reused view identifier makes references ambiguous.";
    "detail_caption_clearance" => Readability, Code, "The detail caption is too close to other ink.";
    "detail_unplaceable" => Readability, Code, "A detail has no room; its dimensions remain on the parent view.";
    "diameter_leader_target_mismatch" => Readability, Code, "The diameter leader points at the wrong geometry.";
    "diameter_leader_target_unverifiable" => Readability, Code, "The diameter leader target cannot be verified.";
    "dimension_edit_unapplied" => Decision, Code, "A saved dimension or precision edit has no current printed item; its intent is retained.";
    "dimension_line_crossing" => Readability, Code, "Dimension lines cross each other.";
    "dimension_line_shared" => Readability, Code, "Dimension strokes share a line and are harder to distinguish.";
    "dimension_not_authored" => Incomplete, Code, "Authored-only dimensioning leaves this required dimension absent.";
    "dimension_scheme_invalid" => Decision, Code, "The authored dimension scheme needs a valid user definition.";
    "dimension_text_stray" => Readability, Code, "Dimension text is displaced from its own span.";
    "dimension_view_not_shown" => Incomplete, Code, "The dimension requires a view that is not drawn.";
    "dimension_witness_detached" => Incomplete, Code, "A detached witness or depth leader misstates where the dimension applies; the required measurement cannot be verified on the drawing.";
    "dimensions_not_ported" => Incomplete, Feature, "No implemented dimension grammar states this feature requirement.";
    "duplicate_measurement" => Advisory, Code, "The same measurement is repeated on the drawing.";
    "edges_not_drawn" => Incomplete, Code, "Failed projection left model edges absent from the drawing.";
    "extension_off_centreline" => Readability, Code, "An extension line lies just beside a centre line, so it reads as locating that centre.";
    "extension_through_feature" => Advisory, Code, "Extension lines crossing geometry are normal drafting practice.";
    "feature_count_mismatch" => Incomplete, Code, "The drawn evidence does not correctly state the required source information.";
    "feature_located_twice" => Advisory, Code, "Repeated location information is reported for review.";
    "feature_no_centermark" => Incomplete, Code, "The required hole centre mark is absent from the drawing.";
    "feature_not_dimensioned" => Incomplete, Feature, "The feature has no drawn dimension that states its requirement.";
    "feature_not_located" => Incomplete, Feature, "The drawing does not state the feature location.";
    "feature_not_sized" => Incomplete, Feature, "The drawing does not state the feature size.";
    "features_not_recognised" => Incomplete, Code, "Unsupported recognition leaves required feature information unstated.";
    "fillet_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "fillet_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "fit_deviations_unavailable" => Advisory, Code, "The valid fit class states the requirement; optional numerical deviations are unavailable.";
    "flat_leader_target_mismatch" => Readability, Code, "The flat leader points at the wrong geometry.";
    "flat_leader_target_unverifiable" => Readability, Code, "The flat leader target cannot be verified.";
    "flat_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "flat_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "frame_datum_not_drawn" => Incomplete, Source, "A drawn feature control frame cites a datum the drawing does not show.";
    "gear_axis_mismatch" => Incomplete, Code, "The drawn evidence does not correctly state the required source information.";
    "gear_correspondence_unverifiable" => Incomplete, Code, "The source gear requirement cannot be matched to the depicted gear.";
    "gear_repeat_count_mismatch" => Incomplete, Code, "The drawn evidence does not correctly state the required source information.";
    "gear_requirement_ambiguous" => Decision, Requirement, "Several authored gear definitions compete for the same feature.";
    "gear_requirement_mismatch" => Incomplete, Requirement, "The drawn evidence does not correctly state the required source information.";
    "gear_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "gear_semantics_missing" => Decision, Code, "Gear manufacturing semantics must be supplied by the user.";
    "general_tolerance_missing" => Decision, Code, "Only the user can choose the general tolerance.";
    "geometry_below_resolution" => Readability, Code, "The requested scale projects geometry below normal drawing resolution.";
    "groove_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "groove_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "gusset_rib_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "gusset_rib_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "hex_pocket_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "hex_pocket_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "hole_bottom_unknown" => Incomplete, Code, "The hole termination needed for a complete callout is unknown.";
    "hole_exit_obstructed" => Advisory, Code, "A through hole has a partly obstructed exit; covering geometry and fraction are reported.";
    "hole_pattern_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "hole_pattern_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "hole_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "hole_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "hole_table_entry_invalid" => Incomplete, Code, "The drawn evidence does not correctly state the required source information.";
    "hole_table_escalated" => Advisory, Code, "A hole table replaced crowded individual callouts.";
    "hole_table_unavailable" => Advisory, Code, "Individual dimensions remain when the requested table cannot be used.";
    "interior_void" => Advisory, Feature, "A factual inventory of an internal cavity; its faces do not establish a hole opening.";
    "iso_layout_overflow" => Readability, Code, "The requested isometric scale exceeds its reserved layout area.";
    "iso_projection_empty" => Incomplete, Code, "The requested isometric view has no projected geometry.";
    "label_centerline_overlap" => Readability, Code, "A centreline crosses label ink.";
    "label_crowded" => Readability, Code, "Text lies within 0.5 mm of another label or of foreign line-work; it stays legible but reads as touching.";
    "label_section_furniture_overlap" => Readability, Code, "Section furniture touches label ink, or annotation ink runs through a section mark.";
    "label_vs_measured" => Incomplete, Code, "The printed label does not state the measured requirement.";
    "layout_recovery_exhausted" => Readability, Code, "No recovery removed all reported layout defects.";
    "layout_replanned" => Advisory, Code, "A successful sheet or scale recovery is reported.";
    "leader_across_view" => Readability, Code, "A long leader crosses more of its own view's lines than a leader leaving the other way would.";
    "leader_along_centreline" => Readability, Code, "A leader runs along a centre line, so the two read as one line.";
    "leader_centerline_overlap" => Readability, Code, "A leader overlaps a centreline.";
    "leader_crosses_silhouette" => Advisory, Code, "The informational audit reports leader re-entry into the silhouette.";
    "leader_line_through_text" => Readability, Code, "A leader runs through text.";
    "leader_through_feature" => Readability, Code, "A leader crosses feature material on its way to its target.";
    "leader_to_hidden_edge" => Readability, Code, "A leader ends on an edge its view shows hidden.";
    "legibility_floor_breached" => Readability, Code, "The selected geometry scale is below the requested legibility floor.";
    "location_on_axis" => Advisory, Code, "The turning axis already fixes this centred location; no tangent dimension is needed.";
    "location_on_datum" => Advisory, Code, "The datum already states this zero location.";
    "location_ref_dropped" => Incomplete, Code, "A required drawing item was dropped before it could be stated.";
    "manufacturing_reference_unresolved" => Incomplete, Code, "A manufacturing requirement reference has no resolved drawn requirement.";
    "manufacturing_table_fallback" => Advisory, Code, "The callouts state the manufacturing requirements in full because their table has no place.";
    "missing_principal_dimension" => Incomplete, Code, "A principal overall size is absent from the drawing.";
    "modelled_thread_not_depicted" => Incomplete, Feature, "The thread depiction or requirement could not be drawn.";
    "modelled_thread_refused" => Incomplete, Code, "A refused thread recognition leaves the thread size and designation unstated.";
    "nominal_rounded" => Advisory, Code, "The printed nominal uses the documented display precision.";
    "notch_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "notch_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "note_duplicated_in_spec" => Advisory, Code, "The same note is already supplied by the authored settings.";
    "notes_not_placed" => Incomplete, Code, "Required notes have no drawn placement.";
    "oblique_witness_crossing" => Readability, Code, "An oblique witness crosses another dimension stroke.";
    "off_axis_location_dropped" => Incomplete, Code, "A required drawing item was dropped before it could be stated.";
    "oriented_slot_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "oriented_slot_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "over_dimensioned_pattern" => Advisory, Code, "Repeated pattern dimensions are redundant information.";
    "pack_bom_suggestion" => Advisory, Code, "Stored BOM suggestions retain their provenance and remain reviewable guesses.";
    "pack_buy_guess" => Advisory, Code, "A purchased-part candidate is listed with its detection reason and can be overridden.";
    "pack_compound" => Advisory, Code, "One product contains multiple solids; the pack reports the upstream part-definition limit.";
    "pack_duplicate_item" => Advisory, Code, "Conflicting file find numbers are reported and replaced by automatic allocation.";
    "pack_ga_balloon_unplaced" => Incomplete, Code, "A visible assembly item has no clear item-reference route.";
    "pack_ga_failed" => Incomplete, Code, "The requested general assembly front sheet could not be drawn.";
    "pack_ga_not_visible" => Advisory, Code, "No face of this listed assembly item has proven visibility; no leader is fabricated.";
    "pack_geometry_merge" => Advisory, Code, "Products with identical resolved geometry share one drawing and a summed quantity.";
    "pack_make_guess" => Advisory, Code, "The default manufacturing decision and its evidence are reported and can be overridden.";
    "pack_single_product" => Advisory, Code, "A single product has multiple placements, counted in its drawing quantity.";
    "pad_footprint_not_defined" => Incomplete, Code, "The pad footprint is not fully defined by dimensions.";
    "pad_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "pad_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "page_fit_uncertain" => Readability, Code, "The composed view and annotation zones may not fit.";
    "paired_ramp_step_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "paired_ramp_step_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "part_squared_to_axes" => Advisory, Code, "The part lay a fraction of a degree off the file's axes and is drawn square, in its own frame.";
    "passage_requirement_unsupported" => Incomplete, Requirement, "No supported drawing grammar states this feature requirement.";
    "pinned_item_not_drawn" => Decision, Code, "The requested pin targets an item that is not drawn.";
    "pinned_reference_chain_changed" => Decision, Code, "The authored pin conflicts with the recovered reference chain.";
    "pinned_view_off_sheet" => Readability, Code, "The explicitly placed view extends outside the drawing area; an error offers removal of its placement.";
    "pinned_view_overlap" => Readability, Code, "The pinned view overlaps another view.";
    "placement_anchor_not_shown" => Decision, Code, "The requested anchor is absent from the drawing.";
    "placement_side_not_applicable" => Decision, Code, "The authored side does not apply to this dimension.";
    "placement_unapplied" => Decision, Code, "The requested placement cannot be applied; an available drawing placement is retained.";
    "plate_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "plate_requirement_suppressed" => Advisory, Requirement, "Other geometry already supplies the suppressed plate requirement.";
    "plate_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "pmi_datum_suppressed" => Advisory, Code, "The datum was deliberately suppressed by policy.";
    "pmi_dimensions_not_ported" => Incomplete, Code, "The source dimension has no implemented drawing grammar.";
    "pmi_graphical_not_drawn" => Incomplete, Source, "A graphical source annotation has no resolved semantic PMI coverage.";
    "pmi_ignored" => Advisory, Code, "The selected dimension-source policy ignores this PMI.";
    "pmi_not_drawn" => Incomplete, Source, "The planned PMI annotation is absent from the sheet.";
    "pmi_not_extracted" => Incomplete, Source, "A source PMI record was not extracted into a drawable requirement.";
    "pmi_not_lowered" => Incomplete, Source, "A read PMI requirement has no supported drawing representation.";
    "pmi_not_read" => Incomplete, Code, "The PMI reading cannot supply the required annotation.";
    "pmi_not_rendered" => Incomplete, Source, "The rendered drawing does not state the source PMI requirement.";
    "pmi_note_unattached" => Incomplete, Source, "The note is stated in the notes block but its feature attachment has no supported flag leader.";
    "pmi_presentation_not_read" => Incomplete, Code, "The source presentation PMI is not available as drawing information.";
    "pmi_read_fact" => Advisory, Source, "A source PMI statement is preserved or measured drawing information is identified.";
    "pmi_read_nonconformance" => Advisory, Source, "A source PMI nonconformance was read through an explicit interpretation.";
    "pmi_source_site_mismatch" => Incomplete, Source, "The drawn evidence does not correctly state the required source information.";
    "pmi_source_text_mismatch" => Incomplete, Source, "The drawn evidence does not correctly state the required source information.";
    "pmi_source_unknown" => Incomplete, Source, "The claimed PMI provenance cannot be found in the source document.";
    "pmi_unreconciled" => Incomplete, Code, "The source PMI and drawing evidence cannot be reconciled.";
    "pmi_value_mismatch" => Incomplete, Source, "The drawn evidence does not correctly state the required source information.";
    "pocket_corner_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "pocket_corner_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "pocket_not_located" => Incomplete, Code, "The drawing does not state the pocket location.";
    "pocket_pattern_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "pocket_pattern_requirement_suppressed" => Advisory, Requirement, "The pattern already supplies this individual requirement.";
    "pocket_pattern_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "pocket_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "pocket_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "polygonal_boss_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "polygonal_boss_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "polygonal_stock_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "polygonal_stock_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "possible_thread" => Incomplete, Feature, "A smooth cylinder at a named metric size may be an unstated thread.";
    "prismatic_pocket_requirement_unsupported" => Incomplete, Requirement, "No supported drawing grammar states this feature requirement.";
    "profile_corner_unlocated" => Incomplete, Requirement, "An outer-profile corner's angle is drawn but no linear dimension locates its edge.";
    "profiled_bore_not_dimensioned" => Incomplete, Code, "The profiled bore requirements have no complete drawn dimension.";
    "projection_relation_infeasible" => Decision, Code, "The authored projected-view relationship cannot be satisfied.";
    "radius_leader_not_radial" => Readability, Code, "The radius leader does not meet its arc radially from either side.";
    "radius_leader_target_mismatch" => Readability, Code, "The radius leader points at the wrong geometry.";
    "radius_leader_target_unverifiable" => Readability, Code, "The radius leader target cannot be verified.";
    "recess_leader_target_mismatch" => Readability, Code, "The recess leader points at the wrong geometry.";
    "recess_leader_target_unverifiable" => Readability, Code, "The recess leader target cannot be verified.";
    "rectangular_blind_slot_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "rectangular_blind_slot_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "relief_profile_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "relief_profiles_per_model" => Advisory, Code, "The drawing deliberately refers to model-defined relief profiles.";
    "round_bottom_blind_slot_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "round_bottom_blind_slot_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "scale_fallback_applied" => Advisory, Code, "The documented feasible scale fallback was applied.";
    "scale_not_stated" => Incomplete, Code, "The drawing does not state its scale.";
    "section_dropped" => Incomplete, Code, "A required drawing item was dropped before it could be stated.";
    "section_not_shown" => Incomplete, Code, "The requested section is not shown.";
    "section_recess_recognition_refused" => Incomplete, Code, "Refused recess recognition leaves the recess requirements unstated.";
    "section_recess_requirement_unsupported" => Incomplete, Requirement, "No supported drawing grammar states this feature requirement.";
    "semicircular_blind_slot_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "semicircular_blind_slot_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "shape_not_recognised" => Incomplete, Shape, "A recognisable shape among unexplained faces is neither sized nor located.";
    "sheet_assignment_unused" => Advisory, Code, "A sheet assignment has no corresponding drawing item.";
    "sheet_below_smallest_scale" => Readability, Code, "The sheet uses views below the requested legibility scale.";
    "sheet_label_replaced" => Advisory, Code, "Automatic document numbering replaces the authored sheet label.";
    "sheet_preference_exceeded" => Advisory, Code, "A usable drawing needs larger paper or more sheets than preferred.";
    "sheet_renumbered" => Advisory, Code, "The document sheets were renumbered consecutively.";
    "sheet_zones_kept" => Advisory, Code, "Section cross-references cite the grid zones, so they stay on the sheet.";
    "slot_leader_target_mismatch" => Readability, Code, "The slot leader points at the wrong geometry.";
    "slot_leader_target_unverifiable" => Readability, Code, "The slot leader target cannot be verified.";
    "slot_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "slot_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "spec_entries_inactive" => Decision, Code, "Authored settings do not apply to any current item.";
    "spec_from_other_revision" => Decision, Code, "The user must decide whether to rebind settings from another revision.";
    "spec_overrides_pmi" => Decision, Code, "The user-authored control replaces a different PMI control.";
    "spec_refines_pmi" => Advisory, Code, "The authored designation refines a Specify-written thread.";
    "step_diameter_leader_target_mismatch" => Readability, Code, "The turned diameter leader points at the wrong geometry.";
    "step_diameter_leader_target_unverifiable" => Readability, Code, "The turned diameter leader target cannot be verified.";
    "step_general_tolerance_disagreement" => Decision, Code, "The authored and PMI general tolerances disagree.";
    "step_length_witness_target_mismatch" => Readability, Code, "The turned length's extension lines are off the band's shoulders.";
    "step_length_witness_target_unverifiable" => Readability, Code, "The turned length's extension lines cannot be verified.";
    "step_level_support_missing" => Incomplete, Code, "Missing shoulder support prevents the step level being stated.";
    "step_material_ambiguous" => Decision, Code, "The STEP records do not identify one material to use.";
    "step_material_disagreement" => Decision, Code, "The authored and STEP materials disagree.";
    "step_material_unavailable" => Decision, Code, "The user must supply a usable material statement.";
    "step_position_coincident_with_datum" => Advisory, Code, "The datum already states this shoulder location.";
    "table_dropped" => Incomplete, Code, "A required drawing item was dropped before it could be stated.";
    "table_not_placed" => Incomplete, Code, "The required item has no drawn placement.";
    "thread_designation_measured" => Advisory, Code, "Measured diameter and pitch are stated without an inferred designation.";
    "through_step_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "through_step_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "title_field_overflow" => Readability, Code, "The title value overflows its reserved cell.";
    "tolerance_not_shown" => Incomplete, Code, "The authored tolerance is absent from the drawing.";
    "tolerance_withheld" => Incomplete, Code, "The authored tolerance is absent from the drawing.";
    "toleranced_reference" => Decision, Code, "A reference-only dimension carries a controlling tolerance; choose its intended role.";
    "turned_chain_no_clear_lane" => Readability, Code, "The turned dimension chain has no clear annotation lane.";
    "turned_profile_superimposed" => Readability, Code, "Superimposed turned profiles make dimensions harder to read.";
    "turned_step_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "turned_step_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "unclaimed_geometry" => Advisory, Code, "Unclaimed faces can be ordinary stock faces, not missing requirements.";
    "unrecognised_defining_geometry" => Incomplete, Code, "The drawing does not define the unrecognised principal geometry.";
    "v_channel_requirement_missing" => Incomplete, Requirement, "The drawing does not state this required feature parameter.";
    "v_channel_requirement_unverifiable" => Incomplete, Requirement, "The drawing does not prove this required feature parameter.";
    "view_annotation_inside_extents" => Advisory, Code, "An annotation inside a view extent is informational.";
    "view_annotation_overlap" => Readability, Code, "An annotation overlaps a view boundary or view geometry.";
    "view_on_title_block" => Readability, Code, "A view overlaps the title block.";
    "view_out_of_bounds" => Readability, Code, "A rendered view extends outside the drawing bounds.";
    "view_overlap" => Readability, Code, "Rendered view extents overlap.";
    "view_plan_incomplete" => Incomplete, Code, "Dimensions require views that are absent, so they remain undrawn.";
    "views_do_not_fit" => Readability, Code, "The selected views do not fit the drawing area.";
    "views_overlap" => Readability, Code, "Planned view zones overlap.";
}

fn classification(code: &str) -> Option<&'static Classification> {
    CODES
        .binary_search_by_key(&code, |entry| entry.code)
        .ok()
        .map(|i| &CODES[i])
}

#[derive(Clone, Debug, Serialize)]
pub struct Labels {
    pub impact: Impact,
    pub group: String,
}

// Source IDs have a typed prefix, e.g. geometric_tolerance:#123 or pmi:datum.a:….
// Refusal explanations without that grammar are deliberately ignored.
fn source_kind(id: &str) -> Option<String> {
    let (kind, tail) = id.strip_prefix("pmi:").unwrap_or(id).split_once(':')?;
    if !(tail.starts_with('#')
        || id.starts_with("pmi:")
        || id.starts_with("spec:")
        || (kind == "material" && tail.bytes().all(|b| b.is_ascii_digit() || b == b':')))
    {
        return None;
    }
    let kind = kind.split('.').next()?;
    (!kind.is_empty() && kind.bytes().all(|c| c.is_ascii_lowercase() || c == b'_'))
        .then(|| kind.to_owned())
}

/// No message text or occurrence IDs enter the grouping key. An unknown external code is
/// conservatively advisory; the source-inventory test forbids unclassified crate codes.
pub fn labels(
    code: &str,
    info: bool,
    subjects: &[String],
    parameters: impl IntoIterator<Item = impl AsRef<str>>,
    sources: &[String],
    crossing_kinds: &[String],
    ink_is_leader: Option<bool>,
) -> Labels {
    let entry = classification(code);
    let mut impact = entry.map_or(Impact::Advisory, |c| c.impact);
    let kinds: BTreeSet<_> = crossing_kinds.iter().map(String::as_str).collect();
    if code == "annotation_leader_crossing" && kinds == BTreeSet::from(["extension_line"]) {
        impact = Impact::Advisory;
    }
    if code == "annotation_ink_overlap" && ink_is_leader == Some(true) {
        impact = Impact::Readability;
    }
    // Info is informational even when the same code warns on a single solid.
    if info {
        impact = Impact::Advisory;
    }
    let cause: BTreeSet<String> = match entry.map(|c| c.group) {
        Some(Group::Feature) => subjects
            .iter()
            .filter_map(|id| {
                id.strip_prefix("f:")
                    .and_then(|s| s.split(':').next())
                    .map(String::from)
            })
            .collect(),
        Some(Group::Requirement) => parameters
            .into_iter()
            .map(|p| p.as_ref().to_owned())
            .collect(),
        Some(Group::Source) => sources
            .iter()
            .filter_map(|s| source_kind(s))
            .chain(subjects.iter().filter_map(|s| source_kind(s)))
            .collect(),
        Some(Group::Crossing) => kinds.into_iter().map(String::from).collect(),
        Some(Group::Shape) => sources
            .iter()
            .filter_map(|s| s.strip_prefix("shape:").map(String::from))
            .collect(),
        _ => BTreeSet::new(),
    };
    let group = if cause.is_empty() {
        code.to_owned()
    } else {
        format!("{code}:{}", cause.into_iter().collect::<Vec<_>>().join("+"))
    };
    Labels { impact, group }
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Count {
    pub findings: usize,
    pub groups: usize,
}

/// Counts cover the lists supplied by the caller, once each, without reordering them.
#[derive(Clone, Debug, Serialize)]
pub struct Summary {
    pub impacts: BTreeMap<Impact, Count>,
    pub groups: BTreeMap<String, usize>,
}

impl Summary {
    pub fn new(labels: impl IntoIterator<Item = Labels>) -> Self {
        let mut impacts: BTreeMap<_, _> = [
            Impact::Incomplete,
            Impact::Decision,
            Impact::Readability,
            Impact::Advisory,
        ]
        .into_iter()
        .map(|i| (i, Count::default()))
        .collect();
        let mut groups = BTreeMap::<String, usize>::new();
        let mut seen = BTreeSet::new();
        for label in labels {
            let count = impacts.get_mut(&label.impact).unwrap();
            count.findings += 1;
            if seen.insert((label.impact, label.group.clone())) {
                count.groups += 1;
            }
            *groups.entry(label.group).or_default() += 1;
        }
        Self { impacts, groups }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalogue_is_sorted_and_documented() {
        let docs = include_str!("../docs/finding-impact.md");
        for pair in CODES.windows(2) {
            assert!(pair[0].code < pair[1].code);
        }
        for c in CODES {
            let impact = serde_json::to_value(c.impact).unwrap();
            assert!(
                docs.contains(&format!(
                    "| `{}` | {} | {} |",
                    c.code,
                    impact.as_str().unwrap(),
                    c.reason
                )),
                "{}",
                c.code
            );
        }
        assert_eq!(
            docs.lines().filter(|l| l.starts_with("| `")).count(),
            CODES.len()
        );
    }
    #[test]
    fn every_info_instance_is_advisory_without_changing_the_code_mapping() {
        for code in CODES {
            assert_eq!(
                labels(
                    code.code,
                    true,
                    &[],
                    std::iter::empty::<&str>(),
                    &[],
                    &[],
                    None
                )
                .impact,
                Impact::Advisory
            );
        }
    }

    #[test]
    fn stroke_metadata_classifies_crossings_and_leader_text_contacts() {
        let crossing = |kinds: &[&str]| {
            labels(
                "annotation_leader_crossing",
                false,
                &[],
                std::iter::empty::<&str>(),
                &[],
                &kinds.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>(),
                None,
            )
        };
        assert_eq!(crossing(&["extension_line"]).impact, Impact::Advisory);
        for kinds in [
            vec!["leader"],
            vec!["dimension_line"],
            vec!["arrow"],
            vec!["extension_line", "leader"],
            vec![],
            vec!["future_kind"],
        ] {
            assert_eq!(crossing(&kinds).impact, Impact::Readability);
        }
        assert_eq!(
            crossing(&["arrow", "leader"]).group,
            crossing(&["leader", "arrow", "leader"]).group
        );
        for ink in [Some(true), Some(false), None] {
            assert_eq!(
                labels(
                    "annotation_ink_overlap",
                    false,
                    &[],
                    std::iter::empty::<&str>(),
                    &[],
                    &[],
                    ink
                )
                .impact,
                Impact::Readability
            );
        }
    }

    #[test]
    fn groups_ignore_occurrences_but_retain_causes() {
        let feature = |id: &str| {
            labels(
                "dimensions_not_ported",
                false,
                &[id.into()],
                std::iter::empty::<&str>(),
                &[],
                &[],
                None,
            )
            .group
        };
        assert_eq!(
            feature("f:section_recess:1"),
            feature("f:section_recess:999")
        );
        assert_ne!(feature("f:section_recess:1"), feature("f:through_step:1"));
        let requirements = |roles: &[&str]| {
            labels(
                "hole_requirement_missing",
                false,
                &[],
                roles,
                &[],
                &[],
                None,
            )
            .group
        };
        assert_ne!(
            requirements(&["bore.diameter"]),
            requirements(&["location.length.x"])
        );
        assert_eq!(
            requirements(&["bore.diameter", "bore.through"]),
            requirements(&["bore.through", "bore.diameter"])
        );
    }
    #[test]
    fn pmi_groups_use_source_kinds_and_never_refusal_prose() {
        let group = |source: &str, why: &str| {
            labels(
                "pmi_not_lowered",
                false,
                &[],
                std::iter::empty::<&str>(),
                &[source.into(), why.into()],
                &[],
                None,
            )
            .group
        };
        assert_eq!(
            group("geometric_tolerance:#123", "Why: first explanation"),
            group("geometric_tolerance:#999", "Different: second explanation")
        );
        assert_ne!(
            group("geometric_tolerance:#123", "why"),
            group("dimensional_size:#123", "why")
        );
        assert_eq!(source_kind("reason:unsupported"), None);
    }
}
