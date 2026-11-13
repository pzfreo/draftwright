// Each original module keeps its helper instances, including private cache statics.
#![allow(clippy::duplicate_mod)]

#[path = "../bands.rs"]
mod bands;
#[path = "../cad_frame_regressions.rs"]
mod cad_frame_regressions;
#[path = "../centrelines.rs"]
mod centrelines;
#[path = "../detail_views.rs"]
mod detail_views;
#[path = "../dxf_native_gdt.rs"]
mod dxf_native_gdt;
#[path = "../fix_relayout.rs"]
mod fix_relayout;
#[path = "../gdt_band.rs"]
mod gdt_band;
#[path = "../h5_chains.rs"]
mod h5_chains;
#[path = "../interior_placement.rs"]
mod interior_placement;
#[path = "../iso_nts.rs"]
mod iso_nts;
#[path = "../label_lanes.rs"]
mod label_lanes;
#[path = "../label_rows.rs"]
mod label_rows;
#[path = "../label_text.rs"]
mod label_text;
#[path = "../layout.rs"]
mod layout;
#[path = "../leader_rules.rs"]
mod leader_rules;
#[path = "../leaders.rs"]
mod leaders;
#[path = "../line_through_feature.rs"]
mod line_through_feature;
#[path = "../linework.rs"]
mod linework;
#[path = "../linework_spec.rs"]
mod linework_spec;
#[path = "../modelled_thread_layout.rs"]
mod modelled_thread_layout;
#[path = "../notes.rs"]
mod notes;
#[path = "../oriented_chamfers.rs"]
mod oriented_chamfers;
#[path = "../output.rs"]
mod output;
#[path = "../page_escalation.rs"]
mod page_escalation;
#[path = "../pinned_callouts.rs"]
mod pinned_callouts;
#[path = "../pinned_offset.rs"]
mod pinned_offset;
#[path = "../placement.rs"]
mod placement;
#[path = "../placement_intent.rs"]
mod placement_intent;
#[path = "../pmi_drawings.rs"]
mod pmi_drawings;
#[path = "../pmi_labels.rs"]
mod pmi_labels;
#[path = "../png.rs"]
mod png;
#[path = "../projection_alignment.rs"]
mod projection_alignment;
#[path = "../render_caches.rs"]
mod render_caches;
#[path = "../render_panics.rs"]
mod render_panics;
#[path = "../repeated_labels.rs"]
mod repeated_labels;
#[path = "../sections.rs"]
mod sections;
#[path = "../sheet_fill.rs"]
mod sheet_fill;
#[path = "../sheet_projection.rs"]
mod sheet_projection;
#[path = "../sheets.rs"]
mod sheets;
#[path = "../sheets_lint.rs"]
mod sheets_lint;
#[path = "../sheets_render.rs"]
mod sheets_render;
#[path = "../sheets_xref.rs"]
mod sheets_xref;
#[path = "../spec_gdt.rs"]
mod spec_gdt;
#[path = "../spec_notes.rs"]
mod spec_notes;
#[path = "../surface_texture.rs"]
mod surface_texture;
#[path = "../table_symbols.rs"]
mod table_symbols;
#[path = "../tables.rs"]
mod tables;
#[path = "../text.rs"]
mod text;
#[path = "../timezone.rs"]
mod timezone;
#[path = "../title_block.rs"]
mod title_block;
#[path = "../title_block_material.rs"]
mod title_block_material;
#[path = "../view_gutters.rs"]
mod view_gutters;
#[path = "../views.rs"]
mod views;
#[path = "../views_layout_rules.rs"]
mod views_layout_rules;

#[path = "../characteristics.rs"]
mod characteristics;
#[path = "../shaded.rs"]
mod shaded;
