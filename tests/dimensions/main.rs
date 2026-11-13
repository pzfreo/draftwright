// Each original module keeps its helper instances, including private cache statics.
#![allow(clippy::duplicate_mod)]

#[path = "../angled_steps.rs"]
mod angled_steps;
#[path = "../authored_dimensions.rs"]
mod authored_dimensions;
#[path = "../coverage_holes.rs"]
mod coverage_holes;
#[path = "../datum_placement.rs"]
mod datum_placement;
#[path = "../dimension_rules.rs"]
mod dimension_rules;
#[path = "../dimension_schemes.rs"]
mod dimension_schemes;
#[path = "../dimensions.rs"]
mod dimensions;
#[path = "../dims_blends.rs"]
mod dims_blends;
#[path = "../dims_edges.rs"]
mod dims_edges;
#[path = "../dims_grooves_flats.rs"]
mod dims_grooves_flats;
#[path = "../dims_gussets.rs"]
mod dims_gussets;
#[path = "../dims_mouth_chamfer.rs"]
mod dims_mouth_chamfer;
#[path = "../dims_plates.rs"]
mod dims_plates;
#[path = "../dims_recesses.rs"]
mod dims_recesses;
#[path = "../dims_slots.rs"]
mod dims_slots;
#[path = "../dims_spotface.rs"]
mod dims_spotface;
#[path = "../dims_steps.rs"]
mod dims_steps;
#[path = "../dims_stock.rs"]
mod dims_stock;
#[path = "../dims_turned.rs"]
mod dims_turned;
#[path = "../fit_label_layout.rs"]
mod fit_label_layout;
#[path = "../hole_marks.rs"]
mod hole_marks;
#[path = "../hole_tables.rs"]
mod hole_tables;
#[path = "../leader_ends.rs"]
mod leader_ends;
#[path = "../planned_hole_tables.rs"]
mod planned_hole_tables;
#[path = "../plate_coverage.rs"]
mod plate_coverage;
#[path = "../pmi_angular.rs"]
mod pmi_angular;
#[path = "../pmi_dimensions.rs"]
mod pmi_dimensions;
#[path = "../pmi_modes.rs"]
mod pmi_modes;
#[path = "../precision.rs"]
mod precision;
#[path = "../profile_angles.rs"]
mod profile_angles;
#[path = "../repeated_dimensions.rs"]
mod repeated_dimensions;
#[path = "../tolerances.rs"]
mod tolerances;
