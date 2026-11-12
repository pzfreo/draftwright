//! The operations every adapter calls (ADR R1): the command line, the JSON-lines service
//! (`draftwright serve`) and the MCP server (`draftwright-mcp`) are thin layers over these.
//!
//! - [`analyse`], [`plan`], [`edit`], [`render`] (with [`write`]), [`spec`] and [`review`],
//!   typed, over the documents of [`crate::model`]; [`plan_analysed`], [`render_analysed`] and
//!   [`review_analysed`] take an Analysis that [`analyse`] made, so the caller need not re-analyse;
//!   [`analyse_from`], [`plan_from`], [`render_from`] and [`review_from`] take where the features
//!   come from ([`Features`]), Rust quiddity's recognition among them (the command line's
//!   default); the others, and [`call`], take Python quiddity's JSON or none;
//! - [`spec_ops`]: the edit ops that turn one Spec into another;
//! - [`call`]: the same operations by name with JSON arguments, as the service and the MCP server
//!   receive them. Requests carry their documents and name the STEP file by path, so nothing is
//!   kept between calls. [`Service`] additionally owns the live JSON-lines session.
//!
//! An [`Error`] is `invalid` (the request cannot be met as asked; it says what would be accepted)
//! or `failed` (something went wrong; each file is published only when complete).

use serde::Serialize;

use crate::model;

/// The drawing's font (IBM Plex Mono), bundled.
pub const MONO: &[u8] = include_bytes!("../fonts/IBMPlexMono-Regular.ttf");

/// The formats [`render`] writes, including editable DXF drawings.
pub const FORMATS: [&str; 4] = ["svg", "pdf", "png", "dxf"];

/// The formats written when none are named: the drawing's vector files.
pub const DEFAULT_FORMATS: [&str; 2] = ["svg", "pdf"];

/// The PNG's resolution when none is given, pixels per inch (draftwright's).
pub use crate::png::DPI;

/// Rendered files: each suffix (`svg`, `sheet2.svg`, `pdf`, etc.) and its bytes.
pub type Files = Vec<(String, Vec<u8>)>;

/// The operations [`call`] answers, in the order they are usually used.
pub const OPERATIONS: [&str; 10] = [
    "analyse",
    "plan",
    "edit",
    "render",
    "review",
    "spec",
    "parts",
    "pack",
    "spec_schema",
    "item_options",
];

/// Why an operation did not complete.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Error {
    /// `invalid` or `failed`.
    pub code: &'static str,
    pub message: String,
    /// What would be accepted, where the request chose from a list.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub choices: Vec<String>,
    /// The Spec entry refused, as an escaped [`crate::items::Op`] path
    /// (`dimensions/d:f:hole:1~1bore.diameter`), where the refusal is that the part has no such
    /// feature, face, dimension, annotation, view or measurement.
    /// [`crate::session::Session::open_spec`] sets such entries aside.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
    /// The other entries the same check found the part does not have (`model::Invalid::also`).
    #[serde(skip)]
    pub(crate) also: Vec<String>,
}

impl Error {
    pub fn invalid(message: impl Into<String>) -> Error {
        Error {
            code: "invalid",
            message: message.into(),
            choices: Vec::new(),
            path: None,
            also: Vec::new(),
        }
    }

    pub fn failed(message: impl Into<String>) -> Error {
        Error {
            code: "failed",
            message: message.into(),
            choices: Vec::new(),
            path: None,
            also: Vec::new(),
        }
    }
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)?;
        if !self.choices.is_empty() {
            write!(f, " Choose one of: {}.", self.choices.join(", "))?;
        }
        Ok(())
    }
}

impl From<model::Unanalysed> for Error {
    fn from(e: model::Unanalysed) -> Error {
        if e.failed {
            Error::failed(e.message)
        } else {
            Error::invalid(e.message)
        }
    }
}

impl From<model::Invalid> for Error {
    fn from(e: model::Invalid) -> Error {
        Error {
            code: "invalid",
            message: e.message,
            choices: e.choices,
            path: e.path,
            also: e.also,
        }
    }
}

/// The `set` and `remove` ops on escaped paths ([`crate::items::key`]) that turn Spec *before*
/// into *after*, as [`crate::items::Op`]s for [`crate::session::Session::apply`]: object keys in
/// sorted order, `schema` at the root skipped, arrays and scalars replaced whole, an object new
/// in *after* set key by key (an empty one set whole, `{}`), and a key missing in *after*
/// removed. An explicit `null` in *after* also removes the key, as the MCP's `spec_diff` does;
/// the editor's `specOperations` sets `null` there instead. A serialised Spec never holds
/// `null`, so for Specs the three agree.
pub fn spec_ops(before: &serde_json::Value, after: &serde_json::Value) -> Vec<crate::items::Op> {
    let mut ops = Vec::new();
    spec_diff(before, after, "", &mut ops);
    ops
}

fn spec_diff(
    before: &serde_json::Value,
    after: &serde_json::Value,
    path: &str,
    out: &mut Vec<crate::items::Op>,
) {
    use serde_json::Value;
    if before == after {
        return;
    }
    match (before, after) {
        (_, Value::Null) => out.push(crate::items::Op::remove(path)),
        (b, Value::Object(a)) if !b.is_object() && (a.is_empty() || !b.is_null()) => {
            // An empty object has no keys to set, and a scalar or array no keys to set them in.
            out.push(crate::items::Op::set(path, after.clone()))
        }
        (b, Value::Object(a)) => {
            let empty = serde_json::Map::new();
            let b = b.as_object().unwrap_or(&empty);
            let mut keys: Vec<&String> = b.keys().chain(a.keys()).collect();
            keys.sort();
            keys.dedup();
            for k in keys {
                if k == "schema" && path.is_empty() {
                    continue;
                }
                let child = match path {
                    "" => crate::items::key(k),
                    _ => format!("{path}/{}", crate::items::key(k)),
                };
                spec_diff(
                    b.get(k).unwrap_or(&Value::Null),
                    a.get(k).unwrap_or(&Value::Null),
                    &child,
                    out,
                );
            }
        }
        _ => out.push(crate::items::Op::set(path, after.clone())),
    }
}

// File and service adapters have no browser implementation.
#[cfg(not(target_arch = "wasm32"))]
mod native;
#[cfg(not(target_arch = "wasm32"))]
pub(crate) use native::selected_part;
#[cfg(not(target_arch = "wasm32"))]
pub use native::*;

/// Plan in-memory analysis with the same recovery as the native API, without
/// the optional filesystem plan cache in a browser.
#[cfg(target_arch = "wasm32")]
pub fn plan_analysed(analysis: &model::Analysis, spec: &model::Spec) -> Result<model::Plan, Error> {
    Ok(model::plan_recovered(analysis, spec)?)
}
