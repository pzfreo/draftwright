//! Identity token parsing and collision-safe whole-document renaming, shared with revision carrying.
use serde_json::{Map, Value};
use std::collections::BTreeMap;

/// The same whole-document rename used by revision carrying, with an exact identity map.
/// Unknown IDs are identity mappings; two object keys reaching one key are refused.
pub(crate) fn rename_spec(v: &Value, ids: &BTreeMap<String, String>) -> Result<Value, String> {
    rename_value(v, &|s| Ok::<_, String>(rename_ids(s, ids)), &|s| s)
}

pub(crate) fn rename_ids(s: &str, ids: &BTreeMap<String, String>) -> String {
    let word = |c: char| c.is_ascii_alphanumeric() || c == '_';
    let mut out = String::new();
    let mut rest = s;
    while !rest.is_empty() {
        let next = ids
            .iter()
            .filter_map(|(old, new)| {
                rest.match_indices(old.as_str())
                    .find(|(i, _)| {
                        (*i == 0 || !rest[..*i].ends_with(word))
                            && !rest[i + old.len()..].starts_with(word)
                    })
                    .map(|(i, _)| (i, old, new))
            })
            .min_by(|a, b| a.0.cmp(&b.0).then(b.1.len().cmp(&a.1.len())));
        let Some((i, old, new)) = next else {
            out.push_str(rest);
            break;
        };
        out.push_str(&rest[..i]);
        out.push_str(new);
        rest = &rest[i + old.len()..];
    }
    out
}

pub(crate) fn rename_value<E>(
    v: &Value,
    rename: &impl Fn(&str) -> Result<String, E>,
    collision: &impl Fn(String) -> E,
) -> Result<Value, E> {
    Ok(match v {
        Value::String(s) => Value::String(rename(s)?),
        Value::Array(a) => Value::Array(
            a.iter()
                .map(|x| rename_value(x, rename, collision))
                .collect::<Result<_, _>>()?,
        ),
        Value::Object(o) => {
            let mut out = Map::new();
            for (k, x) in o {
                let key = rename(k)?;
                if out.contains_key(&key) {
                    return Err(collision(format!(
                        "More than one Spec entry reads as {key:?}; keep one of them."
                    )));
                }
                out.insert(key, rename_value(x, rename, collision)?);
            }
            Value::Object(out)
        }
        other => other.clone(),
    })
}

/// The span of the next `f:<kind>:f<face>` or `face:<n>` in *s*, or of a face or edge list embedded
/// in an ID after `.` or `:` (`profile_angle.angle.f12`, `outer_profile:f12:e3:e4`, `:f3.4`).
pub(crate) fn next_id(s: &str) -> Option<(usize, usize)> {
    let b = s.as_bytes();
    let word = |c: u8| c.is_ascii_alphanumeric() || c == b'_';
    let digit = |k: usize| k < b.len() && b[k].is_ascii_digit();
    let mut i = 0;
    while i < b.len() {
        // An edge also follows a face with `_` (`profile_angle.angle.f97_e92_e96`).
        let after = |c: u8| matches!(c, b'.' | b':') || (c == b'_' && b[i] == b'e');
        if i > 0 && after(b[i - 1]) && matches!(b[i], b'f' | b'e') && digit(i + 1) {
            let mut j = i + 1;
            while digit(j) || (j < b.len() && b[j] == b'.' && digit(j + 1)) {
                j += 1;
            }
            if j == b.len() || !b[j].is_ascii_alphanumeric() {
                return Some((i, j));
            }
        }
        let (face, feature) = (b[i..].starts_with(b"face:"), b[i..].starts_with(b"f:"));
        if (i == 0 || !word(b[i - 1])) && (face || feature) {
            let mut j = i + if face { 5 } else { 2 };
            if feature {
                let kind = j;
                while j < b.len() && (b[j].is_ascii_lowercase() || b[j] == b'_') {
                    j += 1;
                }
                if j == kind || j >= b.len() || b[j] != b':' {
                    i += 1;
                    continue;
                }
                j += 1;
            }
            let anchored = feature && j < b.len() && b[j] == b'f';
            if anchored {
                j += 1;
            }
            let digits = j;
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
            if j > digits && anchored && j < b.len() && b[j] == b'_' && digit(j + 1) {
                j += 1;
                while digit(j) {
                    j += 1;
                }
            }
            if j > digits && (j == b.len() || !word(b[j])) {
                return Some((i, j));
            }
        }
        i += 1;
    }
    None
}
