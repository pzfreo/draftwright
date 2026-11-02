//! Draftwright: automated technical drawings from STEP B-rep — Rust port.
//!
//! Drawing logic only: the geometry (projection with hidden lines, sections) comes from the
//! [`haecceity`] kernel, and feature recognition from quiddity.

pub use haecceity;

pub mod api;
pub mod arc;
pub mod blends;
pub mod bridge;
pub mod callouts;
pub mod centrelines;
pub mod characteristics;
pub mod completeness;
pub mod cosmetic;
pub mod demand;
pub mod dimension_scheme;
pub mod dims;
pub mod evaluation;
pub mod finding;
pub mod fixes;
pub mod gears;
pub mod graphical_pmi;
pub mod half_section;
pub mod hole_table;
pub mod items;
pub mod label_text;
pub mod layout;
pub mod linework;
pub mod lint;
pub mod material;
pub mod model;
pub mod modelled_threads;
pub mod notes;
pub mod output;
pub mod placement_intent;
pub mod pmi;
pub mod pmi_lowering;
pub mod png;
pub mod radius;
pub mod remedy_scope;
pub mod section;
pub mod session;
pub mod sheet_fit;
pub mod sheets;
pub mod spec_gdt;
pub mod spec_notes;
pub use session::Session;
pub mod tables;
pub mod text;
pub mod title_block;
pub mod unexplained;
pub mod wording;

#[cfg(not(target_arch = "wasm32"))]
pub mod mcp;
#[cfg(not(target_arch = "wasm32"))]
pub mod plan_cache;
#[cfg(not(target_arch = "wasm32"))]
pub mod revision;
#[cfg(not(target_arch = "wasm32"))]
pub mod stateless;

pub mod local_layout;
mod nominal;
mod notches;
mod oriented_chamfers;
mod planning;
mod quiddity_evidence;
pub mod sheet_policy;
mod view_gutters;

pub mod iso_caption;

pub mod detail;
/// Authored corrections resolved into the ordinary feature inventory.
pub mod recognition_corrections;

#[cfg(not(target_arch = "wasm32"))]
pub mod pack;
