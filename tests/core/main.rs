// Each original module keeps its helper instances, including private cache statics.
#![allow(clippy::duplicate_mod)]

#[path = "../api_analysis.rs"]
mod api_analysis;
#[path = "../assembly_parts.rs"]
mod assembly_parts;
#[path = "../bridge.rs"]
mod bridge;
#[path = "../cli_native.rs"]
mod cli_native;
#[path = "../editor_session.rs"]
mod editor_session;
#[path = "../engine_version.rs"]
mod engine_version;
// Preserve both the public test prefix and its existing private facts helper.
#[allow(clippy::module_inception)]
#[path = "../facts.rs"]
mod facts;
#[path = "../gears.rs"]
mod gears;
#[path = "../gzip.rs"]
mod gzip;
#[path = "../identity.rs"]
mod identity;
#[path = "../mcp.rs"]
mod mcp;
#[path = "../mcp_efficiency.rs"]
mod mcp_efficiency;
#[path = "../mcp_spec_schema.rs"]
mod mcp_spec_schema;
#[path = "../model.rs"]
mod model;
#[path = "../native.rs"]
mod native;
#[path = "../native_recesses.rs"]
mod native_recesses;
#[path = "../off_axis.rs"]
mod off_axis;
#[path = "../pmi.rs"]
mod pmi;
#[path = "../pmi_lowering.rs"]
mod pmi_lowering;
#[path = "../pmi_thread_knurl.rs"]
mod pmi_thread_knurl;
#[path = "../recognition_corrections.rs"]
mod recognition_corrections;
#[path = "../revision.rs"]
mod revision;
#[path = "../saved_spec.rs"]
mod saved_spec;
#[path = "../serve.rs"]
mod serve;
#[path = "../sheets_mcp.rs"]
mod sheets_mcp;
#[path = "../stateless.rs"]
mod stateless;
#[path = "../test_support.rs"]
mod test_support;

#[path = "../bom.rs"]
mod bom;
#[path = "../pack.rs"]
mod pack;

#[path = "../sealed_voids.rs"]
mod sealed_voids;
