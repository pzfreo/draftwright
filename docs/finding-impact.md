# Finding impact and repeated causes

Every problem and lint finding, including structural findings, carries additive `impact` and
`group` fields. Severity, codes, finding order and drawing output keep their existing meanings.

- `incomplete`: required information is absent or cannot be verified on the drawing.
- `decision`: an authored setting or unresolved source conflict needs a user choice.
- `readability`: the drawing or annotation is harder to read or associate with its geometry.
- `advisory`: normal practice, provenance, redundancy, or an informational finding.

The table is the classification for warning/error instances. **Every `info` instance is advisory**,
including assembly coverage and a reported dimension grammar gap that remains informational.
This does not change severity: some existing PMI gap codes already use error severity.

The sole spatial instance rule uses `annotation_leader_crossing.crossing_kinds`: exactly
`extension_line` is advisory; any `leader`, `dimension_line` or `arrow` is readability.
Absent/unknown kinds conservatively retain readability. `annotation_ink_overlap` is readability
for both ordinary ink and `ink_is_leader: true` (leader through text). No message is parsed.

`group` is an opaque deterministic key: code plus a cause where needed. Broad feature-gap codes
use the structural feature kind from `f:<kind>:<identity>` subjects, requirement codes use sorted
requirement roles, PMI codes use typed source kinds, leader crossings use sorted stroke kinds, and
`shape_not_recognised` uses its `shape:<kind>` ref (protrusion, cut, chamfer, step or teeth).
Other codes already name their cause and group by code. Occurrence IDs, prose, positions and list order never enter a key.
Thus four section-recess grammar gaps share a group, while a different feature kind does not;
extension-through-feature repeats share one group. Unknown externally supplied codes serialize
as advisory and group by their code; all codes emitted by this crate must appear in the table.

`review.summary.impacts` contains `findings` and distinct `groups` counts for all four impacts;
`summary.groups` maps group keys to finding counts. Counts cover `problems`, `lint`, and
`structural` once each, retaining all original findings. MCP review forwards this summary.
MCP inspection and problem pages carry the same finding labels within their existing response
budget; document-wide counts are emitted by `review`.

The single source table is `src/finding.rs`; the source-inventory test also enumerates dynamic
requirement-family codes and refuses an unclassified or undocumented emitted code.

| Code | Impact | Reason |
| --- | --- | --- |
| `angled_step_requirement_unsupported` | incomplete | No supported drawing grammar states this feature requirement. |
| `angular_dimension_dropped` | advisory | An automatic profile-corner angle found no clear place and is not drawn; lint still reports the corner. |
| `angular_geometry_mismatch` | incomplete | The drawn evidence does not correctly state the required source information. |
| `angular_label_vs_geometry` | incomplete | The printed angle does not state the measured angular sector. |
| `angular_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `angular_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `angular_support_mismatch` | incomplete | The drawn evidence does not correctly state the required source information. |
| `annotation_edit_unapplied` | decision | A saved annotation edit has no current item; its intent is retained. |
| `annotation_ink_overlap` | readability | Annotation ink impairs the legibility of another annotation; leader-through-text is included. |
| `annotation_leader_crossing` | readability | Leader, dimension-line or arrow crossings impair reading; extension-only crossings are advisory. |
| `annotation_leader_long` | readability | The leader is longer than the preferred reading distance. |
| `annotation_leader_search_capped` | advisory | The bounded leader search stopped; this reports search provenance. |
| `annotation_line_over_view` | readability | Annotation linework overlaps a view. |
| `annotation_off_sheet` | readability | Annotation ink extends outside the sheet. |
| `annotation_out_of_bounds` | readability | Rendered annotation ink extends outside the drawing bounds. |
| `annotation_outside_area` | readability | The annotation leaves its reserved view area. |
| `annotation_over_title_block` | readability | Annotation ink overlaps the title block. |
| `annotation_over_view` | readability | An annotation overlaps view geometry. |
| `annotation_overlap` | readability | Annotation labels overlap each other. |
| `annular_groove_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `annular_groove_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `assembly_drawn_whole` | advisory | The complete assembly was selected for drawing. |
| `assembly_principal_drawn` | advisory | The assembly's largest part, holding the others, was drawn alone; the others are not drawn. |
| `authored_dimension_invalid` | decision | The authored dimension needs a valid user definition. |
| `axial_length_missing` | incomplete | The drawing does not state the axial length. |
| `balloon_dropped` | incomplete | A required drawing item was dropped before it could be stated. |
| `blend_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `blend_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `boss_height_missing` | incomplete | The drawing does not state the boss height. |
| `boss_height_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `boss_height_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `break_anchor_removed` | incomplete | An annotation anchors in the removed span; its attachment needs an unbroken region. |
| `break_not_applied` | decision | The requested break has no proven uniform span or carrying view. |
| `callout_dropped` | incomplete | A required drawing item was dropped before it could be stated. |
| `chamfer_leader_target_mismatch` | readability | The chamfer leader points at the wrong geometry. |
| `chamfer_leader_target_unverifiable` | readability | The chamfer leader target cannot be verified. |
| `chamfer_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `chamfer_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `channel_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `channel_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `characteristic_balloon_missing` | incomplete | A numbered characteristic has no inspection balloon while inspection is enabled. |
| `characteristic_id_ambiguous` | incomplete | Duplicate requirement ids leave its inspection number unknown. |
| `characteristic_not_drawn` | incomplete | A numbered characteristic has no drawn carrier. |
| `characteristic_number_conflict` | decision | The requirement id and legacy UDA disagree; the id supplies the inspection number. |
| `circular_blind_step_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `circular_blind_step_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `circular_channel_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `circular_channel_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `claimed_value_absent` | incomplete | The drawn evidence does not correctly state the required source information. |
| `closed_dimension_chain` | advisory | A closed chain is redundant dimension information. |
| `coincident_bodies` | advisory | The file holds bodies that coincide with another; one of each is drawn when every body does. |
| `correlated_set_conflict` | decision | The authored choice splits a set that must be kept together. |
| `correlated_set_whole` | advisory | A correlated dimension set was kept together. |
| `cross_reference_missing` | incomplete | A required cross-sheet or derived-view reference is absent. |
| `datum_leader_not_normal` | readability | The datum leader does not leave its triangle square to the face. |
| `datum_leader_remote` | readability | The datum symbol is too far from its referenced surface. |
| `datum_not_drawn` | advisory | A declared datum no frame cites and no dimension measures from is not drawn. |
| `datum_not_identified` | incomplete | A referenced datum lacks its identifying symbol. |
| `datum_origin_unusable` | advisory | The datum cannot define this origin; the documented fallback is used. |
| `datum_symbol_overlap` | readability | Datum symbols overlap other annotations. |
| `datum_undeclared` | decision | A control references a datum the user must declare. |
| `derived_view_identifier_reused` | readability | A reused view identifier makes references ambiguous. |
| `detail_caption_clearance` | readability | The detail caption is too close to other ink. |
| `detail_unplaceable` | readability | A detail has no room; its dimensions remain on the parent view. |
| `diameter_leader_target_mismatch` | readability | The diameter leader points at the wrong geometry. |
| `diameter_leader_target_unverifiable` | readability | The diameter leader target cannot be verified. |
| `dimension_edit_unapplied` | decision | A saved dimension or precision edit has no current printed item; its intent is retained. |
| `dimension_line_crossing` | readability | Dimension lines cross each other. |
| `dimension_line_shared` | readability | Dimension strokes share a line and are harder to distinguish. |
| `dimension_not_authored` | incomplete | Authored-only dimensioning leaves this required dimension absent. |
| `dimension_scheme_invalid` | decision | The authored dimension scheme needs a valid user definition. |
| `dimension_text_stray` | readability | Dimension text is displaced from its own span. |
| `dimension_view_not_shown` | incomplete | The dimension requires a view that is not drawn. |
| `dimension_witness_detached` | incomplete | A detached witness or depth leader misstates where the dimension applies; the required measurement cannot be verified on the drawing. |
| `dimensions_not_ported` | incomplete | No implemented dimension grammar states this feature requirement. |
| `duplicate_measurement` | advisory | The same measurement is repeated on the drawing. |
| `edges_not_drawn` | incomplete | Failed projection left model edges absent from the drawing. |
| `extension_off_centreline` | readability | An extension line lies just beside a centre line, so it reads as locating that centre. |
| `extension_through_feature` | advisory | Extension lines crossing geometry are normal drafting practice. |
| `feature_count_mismatch` | incomplete | The drawn evidence does not correctly state the required source information. |
| `feature_located_twice` | advisory | Repeated location information is reported for review. |
| `feature_no_centermark` | incomplete | The required hole centre mark is absent from the drawing. |
| `feature_not_dimensioned` | incomplete | The feature has no drawn dimension that states its requirement. |
| `feature_not_located` | incomplete | The drawing does not state the feature location. |
| `feature_not_sized` | incomplete | The drawing does not state the feature size. |
| `features_not_recognised` | incomplete | Unsupported recognition leaves required feature information unstated. |
| `fillet_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `fillet_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `fit_deviations_unavailable` | advisory | The valid fit class states the requirement; optional numerical deviations are unavailable. |
| `flat_leader_target_mismatch` | readability | The flat leader points at the wrong geometry. |
| `flat_leader_target_unverifiable` | readability | The flat leader target cannot be verified. |
| `flat_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `flat_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `frame_datum_not_drawn` | incomplete | A drawn feature control frame cites a datum the drawing does not show. |
| `gear_axis_mismatch` | incomplete | The drawn evidence does not correctly state the required source information. |
| `gear_correspondence_unverifiable` | incomplete | The source gear requirement cannot be matched to the depicted gear. |
| `gear_repeat_count_mismatch` | incomplete | The drawn evidence does not correctly state the required source information. |
| `gear_requirement_ambiguous` | decision | Several authored gear definitions compete for the same feature. |
| `gear_requirement_mismatch` | incomplete | The drawn evidence does not correctly state the required source information. |
| `gear_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `gear_semantics_missing` | decision | Gear manufacturing semantics must be supplied by the user. |
| `general_tolerance_missing` | decision | Only the user can choose the general tolerance. |
| `geometry_below_resolution` | readability | The requested scale projects geometry below normal drawing resolution. |
| `groove_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `groove_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `gusset_rib_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `gusset_rib_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `hex_pocket_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `hex_pocket_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `hole_bottom_unknown` | incomplete | The hole termination needed for a complete callout is unknown. |
| `hole_exit_obstructed` | advisory | A through hole has a partly obstructed exit; covering geometry and fraction are reported. |
| `hole_pattern_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `hole_pattern_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `hole_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `hole_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `hole_table_entry_invalid` | incomplete | The drawn evidence does not correctly state the required source information. |
| `hole_table_escalated` | advisory | A hole table replaced crowded individual callouts. |
| `hole_table_unavailable` | advisory | Individual dimensions remain when the requested table cannot be used. |
| `interior_void` | advisory | A factual inventory of an internal cavity; its faces do not establish a hole opening. |
| `iso_layout_overflow` | readability | The requested isometric scale exceeds its reserved layout area. |
| `iso_projection_empty` | incomplete | The requested isometric view has no projected geometry. |
| `label_centerline_overlap` | readability | A centreline crosses label ink. |
| `label_crowded` | readability | Text lies within 0.5 mm of another label or of foreign line-work; it stays legible but reads as touching. |
| `label_section_furniture_overlap` | readability | Section furniture touches label ink, or annotation ink runs through a section mark. |
| `label_vs_measured` | incomplete | The printed label does not state the measured requirement. |
| `layout_recovery_exhausted` | readability | No recovery removed all reported layout defects. |
| `layout_replanned` | advisory | A successful sheet or scale recovery is reported. |
| `leader_across_view` | readability | A long leader crosses more of its own view's lines than a leader leaving the other way would. |
| `leader_along_centreline` | readability | A leader runs along a centre line, so the two read as one line. |
| `leader_centerline_overlap` | readability | A leader overlaps a centreline. |
| `leader_crosses_silhouette` | advisory | The informational audit reports leader re-entry into the silhouette. |
| `leader_line_through_text` | readability | A leader runs through text. |
| `leader_through_feature` | readability | A leader crosses feature material on its way to its target. |
| `leader_to_hidden_edge` | readability | A leader ends on an edge its view shows hidden. |
| `legibility_floor_breached` | readability | The selected geometry scale is below the requested legibility floor. |
| `location_on_axis` | advisory | The turning axis already fixes this centred location; no tangent dimension is needed. |
| `location_on_datum` | advisory | The datum already states this zero location. |
| `location_ref_dropped` | incomplete | A required drawing item was dropped before it could be stated. |
| `manufacturing_reference_unresolved` | incomplete | A manufacturing requirement reference has no resolved drawn requirement. |
| `manufacturing_table_fallback` | advisory | The callouts state the manufacturing requirements in full because their table has no place. |
| `missing_principal_dimension` | incomplete | A principal overall size is absent from the drawing. |
| `modelled_thread_not_depicted` | incomplete | The thread depiction or requirement could not be drawn. |
| `modelled_thread_refused` | incomplete | A refused thread recognition leaves the thread size and designation unstated. |
| `nominal_rounded` | advisory | The printed nominal uses the documented display precision. |
| `notch_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `notch_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `note_duplicated_in_spec` | advisory | The same note is already supplied by the authored settings. |
| `notes_not_placed` | incomplete | Required notes have no drawn placement. |
| `oblique_witness_crossing` | readability | An oblique witness crosses another dimension stroke. |
| `off_axis_location_dropped` | incomplete | A required drawing item was dropped before it could be stated. |
| `oriented_slot_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `oriented_slot_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `over_dimensioned_pattern` | advisory | Repeated pattern dimensions are redundant information. |
| `pack_bom_suggestion` | advisory | Stored BOM suggestions retain their provenance and remain reviewable guesses. |
| `pack_buy_guess` | advisory | A purchased-part candidate is listed with its detection reason and can be overridden. |
| `pack_compound` | advisory | One product contains multiple solids; the pack reports the upstream part-definition limit. |
| `pack_duplicate_item` | advisory | Conflicting file find numbers are reported and replaced by automatic allocation. |
| `pack_ga_balloon_unplaced` | incomplete | A visible assembly item has no clear item-reference route. |
| `pack_ga_failed` | incomplete | The requested general assembly front sheet could not be drawn. |
| `pack_ga_not_visible` | advisory | No face of this listed assembly item has proven visibility; no leader is fabricated. |
| `pack_geometry_merge` | advisory | Products with identical resolved geometry share one drawing and a summed quantity. |
| `pack_make_guess` | advisory | The default manufacturing decision and its evidence are reported and can be overridden. |
| `pack_single_product` | advisory | A single product has multiple placements, counted in its drawing quantity. |
| `pad_footprint_not_defined` | incomplete | The pad footprint is not fully defined by dimensions. |
| `pad_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `pad_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `page_fit_uncertain` | readability | The composed view and annotation zones may not fit. |
| `paired_ramp_step_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `paired_ramp_step_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `part_squared_to_axes` | advisory | The part lay a fraction of a degree off the file's axes and is drawn square, in its own frame. |
| `passage_requirement_unsupported` | incomplete | No supported drawing grammar states this feature requirement. |
| `pinned_item_not_drawn` | decision | The requested pin targets an item that is not drawn. |
| `pinned_reference_chain_changed` | decision | The authored pin conflicts with the recovered reference chain. |
| `pinned_view_off_sheet` | readability | The explicitly placed view extends outside the drawing area; an error offers removal of its placement. |
| `pinned_view_overlap` | readability | The pinned view overlaps another view. |
| `placement_anchor_not_shown` | decision | The requested anchor is absent from the drawing. |
| `placement_side_not_applicable` | decision | The authored side does not apply to this dimension. |
| `placement_unapplied` | decision | The requested placement cannot be applied; an available drawing placement is retained. |
| `plate_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `plate_requirement_suppressed` | advisory | Other geometry already supplies the suppressed plate requirement. |
| `plate_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `pmi_datum_suppressed` | advisory | The datum was deliberately suppressed by policy. |
| `pmi_dimensions_not_ported` | incomplete | The source dimension has no implemented drawing grammar. |
| `pmi_graphical_not_drawn` | incomplete | A graphical source annotation has no resolved semantic PMI coverage. |
| `pmi_ignored` | advisory | The selected dimension-source policy ignores this PMI. |
| `pmi_not_drawn` | incomplete | The planned PMI annotation is absent from the sheet. |
| `pmi_not_extracted` | incomplete | A source PMI record was not extracted into a drawable requirement. |
| `pmi_not_lowered` | incomplete | A read PMI requirement has no supported drawing representation. |
| `pmi_not_read` | incomplete | The PMI reading cannot supply the required annotation. |
| `pmi_not_rendered` | incomplete | The rendered drawing does not state the source PMI requirement. |
| `pmi_note_unattached` | incomplete | The note is stated in the notes block but its feature attachment has no supported flag leader. |
| `pmi_presentation_not_read` | incomplete | The source presentation PMI is not available as drawing information. |
| `pmi_read_fact` | advisory | A source PMI statement is preserved or measured drawing information is identified. |
| `pmi_read_nonconformance` | advisory | A source PMI nonconformance was read through an explicit interpretation. |
| `pmi_source_site_mismatch` | incomplete | The drawn evidence does not correctly state the required source information. |
| `pmi_source_text_mismatch` | incomplete | The drawn evidence does not correctly state the required source information. |
| `pmi_source_unknown` | incomplete | The claimed PMI provenance cannot be found in the source document. |
| `pmi_unreconciled` | incomplete | The source PMI and drawing evidence cannot be reconciled. |
| `pmi_value_mismatch` | incomplete | The drawn evidence does not correctly state the required source information. |
| `pocket_corner_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `pocket_corner_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `pocket_not_located` | incomplete | The drawing does not state the pocket location. |
| `pocket_pattern_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `pocket_pattern_requirement_suppressed` | advisory | The pattern already supplies this individual requirement. |
| `pocket_pattern_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `pocket_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `pocket_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `polygonal_boss_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `polygonal_boss_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `polygonal_stock_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `polygonal_stock_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `possible_thread` | incomplete | A smooth cylinder at a named metric size may be an unstated thread. |
| `prismatic_pocket_requirement_unsupported` | incomplete | No supported drawing grammar states this feature requirement. |
| `profile_corner_unlocated` | incomplete | An outer-profile corner's angle is drawn but no linear dimension locates its edge. |
| `profiled_bore_not_dimensioned` | incomplete | The profiled bore requirements have no complete drawn dimension. |
| `projection_relation_infeasible` | decision | The authored projected-view relationship cannot be satisfied. |
| `radius_leader_not_radial` | readability | The radius leader does not meet its arc radially from either side. |
| `radius_leader_target_mismatch` | readability | The radius leader points at the wrong geometry. |
| `radius_leader_target_unverifiable` | readability | The radius leader target cannot be verified. |
| `recess_leader_target_mismatch` | readability | The recess leader points at the wrong geometry. |
| `recess_leader_target_unverifiable` | readability | The recess leader target cannot be verified. |
| `rectangular_blind_slot_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `rectangular_blind_slot_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `relief_profile_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `relief_profiles_per_model` | advisory | The drawing deliberately refers to model-defined relief profiles. |
| `round_bottom_blind_slot_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `round_bottom_blind_slot_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `scale_fallback_applied` | advisory | The documented feasible scale fallback was applied. |
| `scale_not_stated` | incomplete | The drawing does not state its scale. |
| `section_dropped` | incomplete | A required drawing item was dropped before it could be stated. |
| `section_not_shown` | incomplete | The requested section is not shown. |
| `section_recess_recognition_refused` | incomplete | Refused recess recognition leaves the recess requirements unstated. |
| `section_recess_requirement_unsupported` | incomplete | No supported drawing grammar states this feature requirement. |
| `semicircular_blind_slot_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `semicircular_blind_slot_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `shape_not_recognised` | incomplete | A recognisable shape among unexplained faces is neither sized nor located. |
| `sheet_assignment_unused` | advisory | A sheet assignment has no corresponding drawing item. |
| `sheet_below_smallest_scale` | readability | The sheet uses views below the requested legibility scale. |
| `sheet_label_replaced` | advisory | Automatic document numbering replaces the authored sheet label. |
| `sheet_preference_exceeded` | advisory | A usable drawing needs larger paper or more sheets than preferred. |
| `sheet_renumbered` | advisory | The document sheets were renumbered consecutively. |
| `sheet_zones_kept` | advisory | Section cross-references cite the grid zones, so they stay on the sheet. |
| `slot_leader_target_mismatch` | readability | The slot leader points at the wrong geometry. |
| `slot_leader_target_unverifiable` | readability | The slot leader target cannot be verified. |
| `slot_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `slot_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `spec_entries_inactive` | decision | Authored settings do not apply to any current item. |
| `spec_from_other_revision` | decision | The user must decide whether to rebind settings from another revision. |
| `spec_overrides_pmi` | decision | The user-authored control replaces a different PMI control. |
| `spec_refines_pmi` | advisory | The authored designation refines a Specify-written thread. |
| `step_diameter_leader_target_mismatch` | readability | The turned diameter leader points at the wrong geometry. |
| `step_diameter_leader_target_unverifiable` | readability | The turned diameter leader target cannot be verified. |
| `step_general_tolerance_disagreement` | decision | The authored and PMI general tolerances disagree. |
| `step_length_witness_target_mismatch` | readability | The turned length's extension lines are off the band's shoulders. |
| `step_length_witness_target_unverifiable` | readability | The turned length's extension lines cannot be verified. |
| `step_level_support_missing` | incomplete | Missing shoulder support prevents the step level being stated. |
| `step_material_ambiguous` | decision | The STEP records do not identify one material to use. |
| `step_material_disagreement` | decision | The authored and STEP materials disagree. |
| `step_material_unavailable` | decision | The user must supply a usable material statement. |
| `step_position_coincident_with_datum` | advisory | The datum already states this shoulder location. |
| `table_dropped` | incomplete | A required drawing item was dropped before it could be stated. |
| `table_not_placed` | incomplete | The required item has no drawn placement. |
| `thread_designation_measured` | advisory | Measured diameter and pitch are stated without an inferred designation. |
| `through_step_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `through_step_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `title_field_overflow` | readability | The title value overflows its reserved cell. |
| `tolerance_not_shown` | incomplete | The authored tolerance is absent from the drawing. |
| `tolerance_withheld` | incomplete | The authored tolerance is absent from the drawing. |
| `toleranced_reference` | decision | A reference-only dimension carries a controlling tolerance; choose its intended role. |
| `turned_chain_no_clear_lane` | readability | The turned dimension chain has no clear annotation lane. |
| `turned_profile_superimposed` | readability | Superimposed turned profiles make dimensions harder to read. |
| `turned_step_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `turned_step_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `unclaimed_geometry` | advisory | Unclaimed faces can be ordinary stock faces, not missing requirements. |
| `unrecognised_defining_geometry` | incomplete | The drawing does not define the unrecognised principal geometry. |
| `v_channel_requirement_missing` | incomplete | The drawing does not state this required feature parameter. |
| `v_channel_requirement_unverifiable` | incomplete | The drawing does not prove this required feature parameter. |
| `view_annotation_inside_extents` | advisory | An annotation inside a view extent is informational. |
| `view_annotation_overlap` | readability | An annotation overlaps a view boundary or view geometry. |
| `view_on_title_block` | readability | A view overlaps the title block. |
| `view_out_of_bounds` | readability | A rendered view extends outside the drawing bounds. |
| `view_overlap` | readability | Rendered view extents overlap. |
| `view_plan_incomplete` | incomplete | Dimensions require views that are absent, so they remain undrawn. |
| `views_do_not_fit` | readability | The selected views do not fit the drawing area. |
| `views_overlap` | readability | Planned view zones overlap. |
