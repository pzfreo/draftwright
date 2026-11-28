//! Draftwright: automated technical drawings from STEP B-rep — Rust port.
//!
//! Drawing logic only: the geometry (projection with hidden lines, sections) comes from the
//! `haecceity` kernel, and feature recognition from quiddity.

pub mod api;
mod arc;
mod blends;
mod bom;
mod bridge;
mod callouts;
mod centrelines;
mod characteristics;
mod completeness;
mod cosmetic;
mod demand;
mod dimension_scheme;
mod dims;
mod dxf_output;
mod evaluation;
mod finding;
mod fixes;
mod gears;
mod geometry_ids;
#[cfg(test)]
mod geometry_ids_tests;
mod graphical_pmi;
mod half_section;
mod hole_exits;
mod hole_table;
mod items;
mod label_text;
mod layout;
mod linework;
mod lint;
mod material;
mod model;
mod modelled_threads;
mod notes;
mod output;
mod placement_intent;
mod pmi;
mod pmi_lowering;
mod png;
mod process;
mod radius;
mod remedy_scope;
mod revision_rename;
mod section;
mod sections;
mod session;
mod sheet_fit;
mod sheets;
mod spec_document;
mod spec_gdt;
mod spec_notes;
mod springs;
mod stable_ids;
mod tables;
mod text;
mod thread_designation;
mod title_block;
mod unexplained;
mod wording;

#[cfg(not(target_arch = "wasm32"))]
mod mcp;
#[cfg(not(target_arch = "wasm32"))]
mod plan_cache;
#[cfg(not(target_arch = "wasm32"))]
mod revision;
#[cfg(not(target_arch = "wasm32"))]
mod stateless;

mod broken;
mod decision_overrides;
mod engine_pins;
mod incremental;
mod local_layout;
mod nominal;
mod notches;
mod oriented_chamfers;
mod part_choice;
mod planning;
mod quiddity_evidence;
mod sheet_policy;
mod view_gutters;

mod iso_caption;
mod shaded;

mod auxiliary;
mod detail;
/// Authored corrections resolved into the ordinary feature inventory.
mod recognition_corrections;

mod pack;

#[cfg(test)]
mod lint_evidence_tests;

// Shared binary/extension adapters; this module is not public API.
#[doc(hidden)]
#[cfg(feature = "cli")]
pub mod cli;

mod units;

/// Test access to the internals. Not public API: nothing here is stable.
#[doc(hidden)]
pub mod __testing {
    pub use crate::{api, session::Session};
    pub use ::haecceity;
    macro_rules! expose {
        ($($m:ident),* $(,)?) => { $(pub mod $m { pub use crate::$m::*; })* };
    }
    expose!(
        ['arc, bom, bridge, callouts, centrelines, characteristics,', '        completeness, cosmetic, demand, detail, dims, evaluation,', '        finding, fixes, gears, graphical_pmi, hole_table, items,', '        label_text, layout, linework, lint, material, model,', '        notes, output, pack, placement_intent, pmi, pmi_lowering,', '        png, process, recognition_corrections, section, session, sheet_fit,', '        sheet_policy, sheets, spec_gdt, spec_notes, tables, text,', '        thread_designation, title_block, unexplained, units, wording']
    );
    #[cfg(not(target_arch = "wasm32"))]
    expose!(mcp, plan_cache, revision, stateless);
}
