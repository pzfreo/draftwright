# Finding tiers, impact and repeated causes

Every problem and lint finding, including structural findings, carries additive `tier`, `impact`
and `group` fields. `tier` is owned by the engine in the same exhaustive code registry.

Tiers group issues; they never block export. They set which group the editor's Issues panel
opens first; printing and export remain available at every tier.

- `fix_before_printing`: wrong, missing, unreadable or ambiguously attached manufacturing information.
- `tidy`: drafting quality, placement intent or redundancy that leaves the requirements readable.
- `information`: source diagnostics, provenance and documented fallbacks.

Tier is independent of severity and impact. The table explicitly marks the context-dependent
coverage/PMI codes: an `info` instance is `information` (general-arrangement coverage or a source
attribute that states no requirement); warning/error instances are `fix_before_printing`.
Unapplied annotation edits need review because they can include authored requirement text.
Other codes retain their listed tier even at info severity: crowding is still `tidy` and text
obscured by ink still needs fixing. `pmi_note_unattached` needs fixing because the note loses
its feature association even though its text survives. Target mismatch/unverifiable warnings
need fixing because the measurement cannot be trusted to apply to the intended feature.
`possible_thread` is recognition advice: a smooth cylinder is not proof of an unstated thread.
Recognition and dimension-grammar hints remain `tidy` at every severity; unclaimed geometry
remains `information`. Deliberate Spec overrides of PMI are `information`, and geometry below
normal drawing resolution is `tidy`.
Unknown externally supplied codes use `information`; the inventory test forbids unknown engine codes.

The plan schema stays `draftwright.plan/1`; old plans remain readable and labels are computed
again on serialization. The source `analysis_sha256` keeps the pre-tier byte stream,
so derived labels do not change saved-plan provenance. Machine JSON retains its original list order.
Severity, codes and drawing output keep their existing meanings.

- `incomplete`: required information is absent or cannot be verified on the drawing.
- `decision`: an authored setting or unresolved source conflict needs a user choice.
- `readability`: the drawing or annotation is harder to read or associate with its geometry.
- `advisory`: normal practice, provenance, redundancy, or an informational finding.

The Impact column classifies warning/error instances. **Every `info` instance is advisory**,
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

| Code | Impact | Tier | Reason |
| --- | --- | --- | --- |
| `angled_step_requirement_unsupported` | incomplete | fix_before_printing (information when severity=info) | No supported drawing grammar states this feature requirement. |
| `angular_dimension_dropped` | advisory | tidy | An automatic profile-corner angle found no clear place and is not drawn; lint still reports the corner. |
| `angular_geometry_mismatch` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `angular_label_vs_geometry` | incomplete | fix_before_printing (information when severity=info) | The printed angle does not state the measured angular sector. |
| `angular_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `angular_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `angular_support_mismatch` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `annotation_edit_unapplied` | decision | fix_before_printing | A saved annotation edit has no current item; its intent is retained. |
| `annotation_ink_overlap` | readability | fix_before_printing | Annotation ink impairs the legibility of another annotation; leader-through-text is included. |
| `annotation_leader_crossing` | readability | tidy | Leader, dimension-line or arrow crossings impair reading; extension-only crossings are advisory. |
| `annotation_leader_long` | readability | tidy | The leader is longer than the preferred reading distance. |
| `annotation_leader_search_capped` | advisory | information | The bounded leader search stopped; this reports search provenance. |
| `annotation_line_over_view` | readability | tidy | Annotation linework overlaps a view. |
| `annotation_off_sheet` | readability | fix_before_printing | Annotation ink extends outside the sheet. |
| `annotation_out_of_bounds` | readability | fix_before_printing | Rendered annotation ink extends outside the drawing bounds. |
| `annotation_outside_area` | readability | tidy | The annotation leaves its reserved view area. |
| `annotation_over_title_block` | readability | fix_before_printing | Annotation ink overlaps the title block. |
| `annotation_over_view` | readability | fix_before_printing | An annotation overlaps view geometry. |
| `annotation_overlap` | readability | fix_before_printing | Annotation labels overlap each other. |
| `annular_groove_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `annular_groove_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `assembly_drawn_whole` | advisory | information | The complete assembly was selected for drawing. |
| `assembly_principal_drawn` | advisory | information | The assembly's largest part, holding the others, was drawn alone; the others are not drawn. |
| `authored_dimension_invalid` | decision | fix_before_printing | The authored dimension needs a valid user definition. |
| `axial_length_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state the axial length. |
| `balloon_dropped` | incomplete | fix_before_printing (information when severity=info) | A required drawing item was dropped before it could be stated. |
| `blend_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `blend_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `boss_height_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state the boss height. |
| `boss_height_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `boss_height_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `break_anchor_removed` | incomplete | fix_before_printing (information when severity=info) | An annotation anchors in the removed span; its attachment needs an unbroken region. |
| `break_not_applied` | decision | tidy | The requested break has no proven uniform span or carrying view. |
| `callout_dropped` | incomplete | fix_before_printing (information when severity=info) | A required drawing item was dropped before it could be stated. |
| `chamfer_leader_target_mismatch` | readability | fix_before_printing | The chamfer leader points at the wrong geometry. |
| `chamfer_leader_target_unverifiable` | readability | fix_before_printing | The chamfer leader target cannot be verified. |
| `chamfer_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `chamfer_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `channel_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `channel_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `characteristic_balloon_missing` | incomplete | fix_before_printing (information when severity=info) | A numbered characteristic has no inspection balloon while inspection is enabled. |
| `characteristic_id_ambiguous` | incomplete | fix_before_printing (information when severity=info) | Duplicate requirement ids leave its inspection number unknown. |
| `characteristic_not_drawn` | incomplete | fix_before_printing (information when severity=info) | A numbered characteristic has no drawn carrier. |
| `characteristic_number_conflict` | decision | information | The requirement id and legacy UDA disagree; the id supplies the inspection number. |
| `circular_blind_step_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `circular_blind_step_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `circular_channel_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `circular_channel_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `claimed_value_absent` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `closed_dimension_chain` | advisory | tidy | A closed chain is redundant dimension information. |
| `coincident_bodies` | advisory | information | The file holds bodies that coincide with another; one of each is drawn when every body does. |
| `correlated_set_conflict` | decision | fix_before_printing | The authored choice splits a set that must be kept together. |
| `correlated_set_whole` | advisory | information | A correlated dimension set was kept together. |
| `cross_reference_missing` | incomplete | fix_before_printing (information when severity=info) | A required cross-sheet or derived-view reference is absent. |
| `datum_leader_not_normal` | readability | tidy | The datum leader does not leave its triangle square to the face. |
| `datum_leader_remote` | readability | tidy | The datum symbol is too far from its referenced surface. |
| `datum_not_drawn` | advisory | information | A declared datum no frame cites and no dimension measures from is not drawn. |
| `datum_not_identified` | incomplete | fix_before_printing (information when severity=info) | A referenced datum lacks its identifying symbol. |
| `datum_origin_unusable` | advisory | information | The datum cannot define this origin; the documented fallback is used. |
| `datum_symbol_overlap` | readability | fix_before_printing | Datum symbols overlap other annotations. |
| `datum_undeclared` | decision | fix_before_printing | A control references a datum the user must declare. |
| `derived_view_identifier_reused` | readability | fix_before_printing | A reused view identifier makes references ambiguous. |
| `detail_caption_clearance` | readability | tidy | The detail caption is too close to other ink. |
| `detail_unplaceable` | readability | tidy | A detail has no room; its dimensions remain on the parent view. |
| `diameter_leader_target_mismatch` | readability | fix_before_printing | The diameter leader points at the wrong geometry. |
| `diameter_leader_target_unverifiable` | readability | fix_before_printing | The diameter leader target cannot be verified. |
| `dimension_edit_unapplied` | decision | fix_before_printing | A saved dimension or precision edit has no current printed item; its intent is retained. |
| `dimension_line_crossing` | readability | tidy | Dimension lines cross each other. |
| `dimension_line_shared` | readability | tidy | Dimension strokes share a line and are harder to distinguish. |
| `dimension_not_authored` | incomplete | fix_before_printing (information when severity=info) | Authored-only dimensioning leaves this required dimension absent. |
| `dimension_scheme_invalid` | decision | fix_before_printing | The authored dimension scheme needs a valid user definition. |
| `dimension_text_stray` | readability | fix_before_printing | Dimension text is displaced from its own span. |
| `dimension_view_not_shown` | incomplete | fix_before_printing (information when severity=info) | The dimension requires a view that is not drawn. |
| `dimension_witness_detached` | incomplete | fix_before_printing (information when severity=info) | A detached witness or depth leader misstates where the dimension applies; the required measurement cannot be verified on the drawing. |
| `dimensions_not_ported` | incomplete | tidy | An unimplemented dimension grammar is a drafting-quality hint for this feature. |
| `duplicate_measurement` | advisory | tidy | The same measurement is repeated on the drawing. |
| `edges_not_drawn` | incomplete | fix_before_printing (information when severity=info) | Failed projection left model edges absent from the drawing. |
| `extension_off_centreline` | readability | fix_before_printing | An extension line lies just beside a centre line, so it reads as locating that centre. |
| `extension_through_feature` | advisory | tidy | Extension lines crossing geometry are normal drafting practice. |
| `feature_count_mismatch` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `feature_located_twice` | advisory | tidy | Repeated location information is reported for review. |
| `feature_no_centermark` | incomplete | fix_before_printing (information when severity=info) | The required hole centre mark is absent from the drawing. |
| `feature_not_dimensioned` | incomplete | fix_before_printing (information when severity=info) | The feature has no drawn dimension that states its requirement. |
| `feature_not_located` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state the feature location. |
| `feature_not_sized` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state the feature size. |
| `features_not_recognised` | incomplete | tidy | Unsupported recognition is a drafting-quality hint; unowned faces need not be a problem. |
| `fillet_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `fillet_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `fit_deviations_unavailable` | advisory | information | The valid fit class states the requirement; optional numerical deviations are unavailable. |
| `flat_leader_target_mismatch` | readability | fix_before_printing | The flat leader points at the wrong geometry. |
| `flat_leader_target_unverifiable` | readability | fix_before_printing | The flat leader target cannot be verified. |
| `flat_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `flat_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `frame_datum_not_drawn` | incomplete | fix_before_printing (information when severity=info) | A drawn feature control frame cites a datum the drawing does not show. |
| `gdt_position_changed` | decision | tidy | Dimension placement moved an unrelated GD&T item; the warning names the item and cause. |
| `gear_axis_mismatch` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `gear_correspondence_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The source gear requirement cannot be matched to the depicted gear. |
| `gear_repeat_count_mismatch` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `gear_requirement_ambiguous` | decision | fix_before_printing | Several authored gear definitions compete for the same feature. |
| `gear_requirement_mismatch` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `gear_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `gear_semantics_missing` | decision | fix_before_printing | Gear manufacturing semantics must be supplied by the user. |
| `general_tolerance_missing` | decision | fix_before_printing | Only the user can choose the general tolerance. |
| `geometry_below_resolution` | readability | tidy | Geometry projected below normal drawing resolution is a drafting-quality concern. |
| `groove_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `groove_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `gusset_rib_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `gusset_rib_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `hex_pocket_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `hex_pocket_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `hole_bottom_unknown` | incomplete | fix_before_printing (information when severity=info) | The hole termination needed for a complete callout is unknown. |
| `hole_exit_obstructed` | advisory | information | A through hole has a partly obstructed exit; covering geometry and fraction are reported. |
| `hole_pattern_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `hole_pattern_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `hole_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `hole_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `hole_table_entry_invalid` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `hole_table_escalated` | advisory | information | A hole table replaced crowded individual callouts. |
| `hole_table_tag_order_retained` | advisory | information | Legacy tags retain the finished geometry when spatial relabelling would collide or detach a row leader. |
| `hole_table_unavailable` | decision | tidy | An explicit table request was not met; individual dimensions remain. |
| `id_not_migrated` | advisory | information | A saved legacy identity has no exact current match; its key and intent are retained. |
| `inch_design_detected` | advisory | information | Model sizes closely follow sixteenth-inch or decimal-inch grids; units remain unchanged. |
| `interior_void` | advisory | information | A factual inventory of an internal cavity; its faces do not establish a hole opening. |
| `iso_layout_overflow` | readability | tidy | The requested isometric scale exceeds its reserved layout area. |
| `iso_projection_empty` | incomplete | fix_before_printing (information when severity=info) | The requested isometric view has no projected geometry. |
| `label_centerline_overlap` | readability | fix_before_printing | A centreline crosses label ink. |
| `label_crowded` | readability | tidy | Text lies within 0.5 mm of another label or of foreign line-work; it stays legible but reads as touching. |
| `label_section_furniture_overlap` | readability | fix_before_printing | Section furniture touches label ink, or annotation ink runs through a section mark. |
| `label_vs_measured` | incomplete | fix_before_printing (information when severity=info) | The printed label does not state the measured requirement. |
| `layout_recovery_exhausted` | readability | tidy | No recovery removed all reported layout defects. |
| `layout_replanned` | advisory | information | A successful sheet or scale recovery is reported. |
| `leader_across_view` | readability | tidy | A long leader crosses more of its own view's lines than a leader leaving the other way would. |
| `leader_along_centreline` | readability | tidy | A leader runs along a centre line, so the two read as one line. |
| `leader_centerline_overlap` | readability | tidy | A leader overlaps a centreline. |
| `leader_crosses_silhouette` | advisory | tidy | The informational audit reports leader re-entry into the silhouette. |
| `leader_line_through_text` | readability | fix_before_printing | A leader runs through text. |
| `leader_through_feature` | readability | tidy | A leader crosses feature material on its way to its target. |
| `leader_to_hidden_edge` | readability | tidy | A leader ends on an edge its view shows hidden. |
| `legibility_floor_breached` | readability | tidy | The selected geometry scale is below the requested legibility floor. |
| `location_on_axis` | advisory | information | The turning axis already fixes this centred location; no tangent dimension is needed. |
| `location_on_datum` | advisory | information | The datum already states this zero location. |
| `location_ref_dropped` | incomplete | fix_before_printing (information when severity=info) | A required drawing item was dropped before it could be stated. |
| `manufacturing_reference_unresolved` | incomplete | fix_before_printing (information when severity=info) | A manufacturing requirement reference has no resolved drawn requirement. |
| `manufacturing_table_fallback` | advisory | information | The callouts state the manufacturing requirements in full because their table has no place. |
| `missing_principal_dimension` | incomplete | fix_before_printing (information when severity=info) | A principal overall size is absent from the drawing. |
| `modelled_thread_not_depicted` | incomplete | fix_before_printing (information when severity=info) | The thread depiction or requirement could not be drawn. |
| `modelled_thread_refused` | incomplete | fix_before_printing (information when severity=info) | A refused thread recognition leaves the thread size and designation unstated. |
| `nominal_rounded` | advisory | information | The printed nominal uses the documented display precision. |
| `notch_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `notch_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `note_duplicated_in_spec` | advisory | information | The same note is already supplied by the authored settings. |
| `notes_not_placed` | incomplete | fix_before_printing (information when severity=info) | Required notes have no drawn placement. |
| `oblique_witness_crossing` | readability | tidy | An oblique witness crosses another dimension stroke. |
| `off_axis_location_dropped` | incomplete | fix_before_printing (information when severity=info) | A required drawing item was dropped before it could be stated. |
| `oriented_slot_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `oriented_slot_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `over_dimensioned_pattern` | advisory | tidy | Repeated pattern dimensions are redundant information. |
| `pack_bom_suggestion` | advisory | information | Stored BOM suggestions retain their provenance and remain reviewable guesses. |
| `pack_buy_guess` | advisory | information | A purchased-part candidate is listed with its detection reason and can be overridden. |
| `pack_compound` | advisory | information | One product contains multiple solids; the pack reports the upstream part-definition limit. |
| `pack_duplicate_item` | advisory | information | Conflicting file find numbers are reported and replaced by automatic allocation. |
| `pack_ga_balloon_unplaced` | incomplete | fix_before_printing (information when severity=info) | A visible assembly item has no clear item-reference route. |
| `pack_ga_failed` | incomplete | fix_before_printing (information when severity=info) | The requested general assembly front sheet could not be drawn. |
| `pack_ga_not_visible` | advisory | information | No face of this listed assembly item has proven visibility; no leader is fabricated. |
| `pack_geometry_merge` | advisory | information | Products with identical resolved geometry share one drawing and a summed quantity. |
| `pack_make_guess` | advisory | information | The default manufacturing decision and its evidence are reported and can be overridden. |
| `pack_single_product` | advisory | information | A single product has multiple placements, counted in its drawing quantity. |
| `pad_footprint_not_defined` | incomplete | fix_before_printing (information when severity=info) | The pad footprint is not fully defined by dimensions. |
| `pad_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `pad_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `page_fit_uncertain` | readability | tidy | The composed view and annotation zones may not fit. |
| `paired_ramp_step_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `paired_ramp_step_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `part_squared_to_axes` | advisory | information | The part lay a fraction of a degree off the file's axes and is drawn square, in its own frame. |
| `passage_requirement_unsupported` | incomplete | fix_before_printing (information when severity=info) | No supported drawing grammar states this feature requirement. |
| `pictorial_placement_unresolved` | decision | tidy | The authored pictorial placement or scale cannot fit; its intent is retained for a user choice. |
| `pinned_item_not_drawn` | decision | tidy | The requested pin targets an item that is not drawn. |
| `pinned_reference_chain_changed` | decision | tidy | The authored pin conflicts with the recovered reference chain. |
| `pinned_view_off_sheet` | readability | fix_before_printing | The explicitly placed view extends outside the drawing area; an error offers removal of its placement. |
| `pinned_view_overlap` | readability | fix_before_printing | The pinned view overlaps another view. |
| `placement_anchor_not_shown` | decision | tidy | The requested anchor is absent from the drawing. |
| `placement_displaced` | decision | tidy | The final layout displaced an explicit placement; the warning names the requested fields and cause. |
| `placement_side_not_applicable` | decision | tidy | The authored side does not apply to this dimension. |
| `placement_unapplied` | decision | tidy | The requested placement cannot be applied; an available drawing placement is retained. |
| `plate_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `plate_requirement_suppressed` | advisory | information | Other geometry already supplies the suppressed plate requirement. |
| `plate_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `pmi_datum_suppressed` | advisory | information | The datum was deliberately suppressed by policy. |
| `pmi_dimensions_not_ported` | incomplete | fix_before_printing (information when severity=info) | The source dimension has no implemented drawing grammar. |
| `pmi_graphical_not_drawn` | incomplete | fix_before_printing (information when severity=info) | A graphical source annotation has no resolved semantic PMI coverage. |
| `pmi_hole_depth_mismatch` | decision | fix_before_printing (information when severity=info) | The authored PMI hole depth disagrees with the recognised geometry; the authored depth is drawn. |
| `pmi_ignored` | advisory | information | The selected dimension-source policy ignores this PMI. |
| `pmi_not_drawn` | incomplete | fix_before_printing (information when severity=info) | The planned PMI annotation is absent from the sheet. |
| `pmi_not_extracted` | incomplete | fix_before_printing (information when severity=info) | A source PMI record was not extracted into a drawable requirement. |
| `pmi_not_lowered` | incomplete | fix_before_printing (information when severity=info) | A read PMI requirement has no supported drawing representation. |
| `pmi_not_read` | incomplete | fix_before_printing (information when severity=info) | The PMI reading cannot supply the required annotation. |
| `pmi_not_rendered` | incomplete | fix_before_printing (information when severity=info) | The rendered drawing does not state the source PMI requirement. |
| `pmi_note_unattached` | incomplete | fix_before_printing | The note is stated in the notes block but its feature attachment has no supported flag leader. |
| `pmi_presentation_not_read` | incomplete | information | The source presentation PMI is not available as drawing information. |
| `pmi_read_fact` | advisory | information | A source PMI statement is preserved or measured drawing information is identified. |
| `pmi_read_nonconformance` | advisory | information | A source PMI nonconformance was read through an explicit interpretation. |
| `pmi_source_site_mismatch` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `pmi_source_text_mismatch` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `pmi_source_unknown` | incomplete | fix_before_printing (information when severity=info) | The claimed PMI provenance cannot be found in the source document. |
| `pmi_unreconciled` | incomplete | fix_before_printing (information when severity=info) | The source PMI and drawing evidence cannot be reconciled. |
| `pmi_value_mismatch` | incomplete | fix_before_printing (information when severity=info) | The drawn evidence does not correctly state the required source information. |
| `pocket_corner_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `pocket_corner_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `pocket_not_located` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state the pocket location. |
| `pocket_pattern_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `pocket_pattern_requirement_suppressed` | advisory | information | The pattern already supplies this individual requirement. |
| `pocket_pattern_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `pocket_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `pocket_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `polygonal_boss_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `polygonal_boss_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `polygonal_stock_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `polygonal_stock_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `possible_thread` | incomplete | information | A smooth cylinder at a named metric size may be an unstated thread. |
| `prismatic_pocket_requirement_unsupported` | incomplete | fix_before_printing (information when severity=info) | No supported drawing grammar states this feature requirement. |
| `profile_corner_unlocated` | incomplete | fix_before_printing (information when severity=info) | An outer-profile corner's angle is drawn but no linear dimension locates its edge. |
| `profiled_bore_not_dimensioned` | incomplete | fix_before_printing (information when severity=info) | The profiled bore requirements have no complete drawn dimension. |
| `projection_relation_infeasible` | decision | tidy | The authored projected-view relationship cannot be satisfied. |
| `radius_leader_not_radial` | readability | tidy | The radius leader does not meet its arc radially from either side. |
| `radius_leader_target_mismatch` | readability | fix_before_printing | The radius leader points at the wrong geometry. |
| `radius_leader_target_unverifiable` | readability | fix_before_printing | The radius leader target cannot be verified. |
| `recess_leader_target_mismatch` | readability | fix_before_printing | The recess leader points at the wrong geometry. |
| `recess_leader_target_unverifiable` | readability | fix_before_printing | The recess leader target cannot be verified. |
| `rectangular_blind_slot_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `rectangular_blind_slot_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `relief_profile_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `relief_profiles_per_model` | advisory | information | The drawing deliberately refers to model-defined relief profiles. |
| `round_bottom_blind_slot_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `round_bottom_blind_slot_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `scale_fallback_applied` | advisory | information | The documented feasible scale fallback was applied. |
| `scale_not_stated` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state its scale. |
| `section_dropped` | incomplete | fix_before_printing (information when severity=info) | A required drawing item was dropped before it could be stated. |
| `section_not_shown` | incomplete | fix_before_printing (information when severity=info) | The requested section is not shown. |
| `section_recess_recognition_refused` | incomplete | fix_before_printing (information when severity=info) | Refused recess recognition leaves the recess requirements unstated. |
| `section_recess_requirement_unsupported` | incomplete | fix_before_printing (information when severity=info) | No supported drawing grammar states this feature requirement. |
| `semicircular_blind_slot_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `semicircular_blind_slot_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `shaded_view_unresolved` | decision | tidy | The requested shaded style could not be resolved; ordinary pictorial lines preserve the drawing. |
| `shape_not_recognised` | incomplete | tidy | A recognisable shape among unexplained faces is a drafting-quality hint. |
| `sheet_assignment_unused` | advisory | information | A sheet assignment has no corresponding drawing item. |
| `sheet_below_smallest_scale` | readability | tidy | The sheet uses views below the requested legibility scale. |
| `sheet_label_replaced` | advisory | information | Automatic document numbering replaces the authored sheet label. |
| `sheet_preference_exceeded` | advisory | information | A usable drawing needs larger paper or more sheets than preferred. |
| `sheet_renumbered` | advisory | information | The document sheets were renumbered consecutively. |
| `sheet_zones_kept` | advisory | information | Section cross-references cite the grid zones, so they stay on the sheet. |
| `slot_leader_target_mismatch` | readability | fix_before_printing | The slot leader points at the wrong geometry. |
| `slot_leader_target_unverifiable` | readability | fix_before_printing | The slot leader target cannot be verified. |
| `slot_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `slot_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `spec_entries_inactive` | decision | information | Authored settings do not apply to any current item. |
| `spec_from_other_revision` | decision | fix_before_printing | The user must decide whether to rebind settings from another revision. |
| `spec_overrides_pmi` | decision | information | The deliberate user-authored override of PMI is informational. |
| `spec_refines_pmi` | advisory | information | The authored designation refines a Specify-written thread. |
| `step_diameter_leader_target_mismatch` | readability | fix_before_printing | The turned diameter leader points at the wrong geometry. |
| `step_diameter_leader_target_unverifiable` | readability | fix_before_printing | The turned diameter leader target cannot be verified. |
| `step_general_tolerance_disagreement` | decision | fix_before_printing | The authored and PMI general tolerances disagree. |
| `step_length_witness_target_mismatch` | readability | fix_before_printing | The turned length's extension lines are off the band's shoulders. |
| `step_length_witness_target_unverifiable` | readability | fix_before_printing | The turned length's extension lines cannot be verified. |
| `step_level_support_missing` | incomplete | fix_before_printing (information when severity=info) | Missing shoulder support prevents the step level being stated. |
| `step_material_ambiguous` | decision | fix_before_printing | The STEP records do not identify one material to use. |
| `step_material_disagreement` | decision | fix_before_printing | The authored and STEP materials disagree. |
| `step_material_unavailable` | decision | fix_before_printing | The user must supply a usable material statement. |
| `step_position_coincident_with_datum` | advisory | information | The datum already states this shoulder location. |
| `table_dropped` | incomplete | fix_before_printing (information when severity=info) | A required drawing item was dropped before it could be stated. |
| `table_not_placed` | incomplete | fix_before_printing (information when severity=info) | The required item has no drawn placement. |
| `text_glyph_missing` | readability | fix_before_printing | A character in a label is in none of the bundled fonts, so it is drawn as an empty box. |
| `thread_designation_measured` | advisory | information | Measured diameter and pitch are stated without an inferred designation. |
| `through_step_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `through_step_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `title_field_overflow` | readability | fix_before_printing | The title value overflows its reserved cell. |
| `tolerance_not_shown` | incomplete | fix_before_printing (information when severity=info) | The authored tolerance is absent from the drawing. |
| `tolerance_withheld` | incomplete | fix_before_printing (information when severity=info) | The authored tolerance is absent from the drawing. |
| `toleranced_reference` | decision | fix_before_printing | A reference-only dimension carries a controlling tolerance; choose its intended role. |
| `turned_chain_no_clear_lane` | readability | tidy | The turned dimension chain has no clear annotation lane. |
| `turned_profile_superimposed` | readability | tidy | Superimposed turned profiles make dimensions harder to read. |
| `turned_step_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `turned_step_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `unclaimed_geometry` | advisory | information | Unclaimed faces can be ordinary stock faces, not missing requirements. |
| `unrecognised_defining_geometry` | incomplete | tidy | Unrecognised principal geometry is a drafting-quality hint. |
| `v_channel_requirement_missing` | incomplete | fix_before_printing (information when severity=info) | The drawing does not state this required feature parameter. |
| `v_channel_requirement_unverifiable` | incomplete | fix_before_printing (information when severity=info) | The drawing does not prove this required feature parameter. |
| `view_annotation_inside_extents` | advisory | information | An annotation inside a view extent is informational. |
| `view_annotation_overlap` | readability | fix_before_printing | An annotation overlaps a view boundary or view geometry. |
| `view_on_title_block` | readability | fix_before_printing | A view overlaps the title block. |
| `view_out_of_bounds` | readability | fix_before_printing | A rendered view extends outside the drawing bounds. |
| `view_overlap` | readability | fix_before_printing | Rendered view extents overlap. |
| `view_plan_incomplete` | incomplete | fix_before_printing (information when severity=info) | Dimensions require views that are absent, so they remain undrawn. |
| `views_do_not_fit` | readability | fix_before_printing | The selected views do not fit the drawing area. |
| `views_overlap` | readability | tidy | Planned view zones overlap. |
