//! Small named sheet controls and repeatable edits to the existing Spec.
use clap::Args;
use serde_json::{Value, json};

use crate::{
    api::{self, Error},
    model::Spec,
    pack::PackSpec,
};

#[derive(Args, Default)]
pub(super) struct Overrides {
    /// Force sheet size: A4..A0, ANSI A..E or ARCH A..E (pack: GA only)
    #[arg(long, value_name = "NAME")]
    size: Option<String>,
    /// Preferred largest paper: A4..A0, ANSI A..E or ARCH A..E
    #[arg(long, value_name = "NAME")]
    pub(super) max_size: Option<String>,
    /// Set a Spec path to JSON or text; repeatable; conflicts with named flags
    #[arg(long = "set", value_name = "PATH=VALUE", action = clap::ArgAction::Append)]
    sets: Vec<String>,
}

fn at_path(mut error: Error, path: &str) -> Error {
    error.message = format!("{path}: {}", error.message);
    error.path = Some(path.into());
    error
}

fn components(path: &str) -> Vec<String> {
    path.split('/')
        .filter(|s| !s.is_empty())
        .map(|s| s.replace("~1", "/").replace("~0", "~"))
        .collect()
}

fn size(value: &Value, path: &str) -> Result<(), Error> {
    if value.is_null() {
        return Ok(());
    }
    if !value.as_str().is_some_and(|s| {
        crate::units::Paper::for_size(s)
            .pages()
            .iter()
            .any(|p| p.0 == s)
    }) {
        return Err(at_path(
            Error::invalid("sheet size must be A4..A0, ANSI A..E or ARCH A..E"),
            path,
        ));
    }
    Ok(())
}

impl Overrides {
    pub(super) fn requested(&self) -> bool {
        self.size.is_some() || self.max_size.is_some() || !self.sets.is_empty()
    }

    pub(super) fn paths(&self, prefix: &str) -> Vec<String> {
        [("size", &self.size), ("preferred_max_size", &self.max_size)]
            .into_iter()
            .filter(|(_, v)| v.is_some())
            .map(|(key, _)| format!("{prefix}sheet/{key}"))
            .collect()
    }

    fn edits(&self, named: &[String], pack: bool) -> Result<Vec<Value>, Error> {
        let mut edits = Vec::new();
        for assignment in &self.sets {
            let (path, text) = assignment
                .split_once('=')
                .ok_or_else(|| at_path(Error::invalid("--set expects PATH=VALUE"), assignment))?;
            let keys = components(path);
            if keys.is_empty() || keys.len() > 64 {
                return Err(at_path(
                    Error::invalid("--set needs a path of 1 to 64 components"),
                    path,
                ));
            }
            if named.iter().any(|n| {
                let n = components(n);
                keys.starts_with(&n) || n.starts_with(&keys)
            }) || (pack && self.max_size.is_some() && pack_max_conflict(&keys))
            {
                return Err(at_path(
                    Error::invalid("--set conflicts with a named flag"),
                    path,
                ));
            }
            let value = serde_json::from_str(text).unwrap_or_else(|_| Value::String(text.into()));
            // Size is otherwise checked during planning; packs can report per-part planning
            // errors as rows, so refuse an invalid CLI size before starting the pack.
            if keys.len() >= 2
                && keys[keys.len() - 2] == "sheet"
                && matches!(
                    keys.last().map(String::as_str),
                    Some("size" | "preferred_max_size")
                )
            {
                size(&value, path)?;
            }
            edits.push(json!({"op":"set", "path":path, "value":value}));
        }
        Ok(edits)
    }

    pub(super) fn drawing(&self, spec: &Spec, named: &[String]) -> Result<Spec, Error> {
        let edits = self.edits(named, false)?;
        let mut spec = spec.clone();
        for edit in edits {
            let path = edit["path"].as_str().expect("edit path");
            spec = api::edit(&spec, &json!([edit.clone()])).map_err(|e| at_path(e, path))?;
        }
        let mut named = Vec::new();
        for (key, value) in [("size", &self.size), ("preferred_max_size", &self.max_size)] {
            if let Some(value) = value {
                let path = format!("sheet/{key}");
                size(&json!(value), &path)?;
                named.push(json!({"op":"set", "path":path, "value":value}));
            }
        }
        if !named.is_empty() {
            spec = api::edit(&spec, &json!(named))?;
        }
        Ok(spec)
    }

    pub(super) fn pack(&self, spec: &PackSpec, scale: Option<&str>) -> Result<PackSpec, Error> {
        let mut named = self.paths("ga/drawing/");
        if scale.is_some() {
            named.push("ga/drawing/sheet/scale".into());
        }
        let mut doc = serde_json::to_value(spec).expect("pack Spec serialises");
        for edit in self.edits(&named, true)? {
            let path = edit["path"].as_str().expect("edit path");
            set(&mut doc, &components(path), edit["value"].clone())
                .map_err(|e| at_path(e, path))?;
            let _: PackSpec = serde_json::from_value(doc.clone())
                .map_err(|e| at_path(Error::invalid(format!("invalid pack Spec: {e}")), path))?;
        }
        let mut spec: PackSpec = serde_json::from_value(doc)
            .map_err(|e| Error::invalid(format!("invalid pack Spec: {e}")))?;
        // Named flags override the file, with the same drawing edit validation.
        let sheet = Overrides {
            size: self.size.clone(),
            max_size: self.max_size.clone(),
            sets: Vec::new(),
        };
        spec.ga.drawing = sheet.drawing(&spec.ga.drawing, &[])?;
        if let Some(scale) = scale {
            spec.ga.drawing.sheet.scale = Some(super::compat::parse_scale(scale)?);
        }
        Ok(spec)
    }
}

// --max-size covers every part, including keys not present in the input Spec.
fn pack_max_conflict(keys: &[String]) -> bool {
    let suffix = match keys.first().map(String::as_str) {
        Some("ga") => &keys[1..],
        Some("parts") if keys.len() > 1 => &keys[2..],
        Some("parts") => return true,
        _ => return false,
    };
    let target = ["drawing", "sheet", "preferred_max_size"];
    suffix.iter().zip(target).all(|(a, b)| a == b)
}

/// Pack paths use the same object creation and ~1/~0 decoding as drawing edits.
fn set(doc: &mut Value, keys: &[String], value: Value) -> Result<(), Error> {
    let (key, rest) = keys.split_first().expect("nonempty edit path");
    if doc.is_null() {
        *doc = json!({});
    }
    let child = match doc {
        Value::Object(object) => object.entry(key.clone()).or_insert(Value::Null),
        Value::Array(array) => key
            .parse::<usize>()
            .ok()
            .and_then(|i| array.get_mut(i))
            .ok_or_else(|| Error::invalid("no such list index"))?,
        _ => return Err(Error::invalid("path is not inside an object or list")),
    };
    if rest.is_empty() {
        *child = value;
        Ok(())
    } else {
        set(child, rest, value)
    }
}
