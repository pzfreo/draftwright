//! Identity token parsing and collision-safe whole-document renaming, shared with revision carrying.
use serde_json::{Map, Value};
use std::collections::BTreeMap;

/// The same whole-document rename used by revision carrying, with an exact identity map.
/// Unknown IDs are identity mappings; two object keys reaching one key are refused.
pub(crate) fn rename_spec(v: &Value, ids: &BTreeMap<String, String>) -> Result<Value, String> {
    let names = Renamer::new(ids);
    rename_value(v, &|s| Ok::<_, String>(names.rename(s)), &|s| s)
}

#[derive(Default)]
struct Node<'a> {
    edges: BTreeMap<u8, usize>,
    replacement: Option<&'a str>,
}

/// Compile exact aliases once. Each string scans its own token prefixes, never
/// every alias in the document. Leftmost/longest and ASCII word boundaries are unchanged.
pub(crate) struct Renamer<'a> {
    nodes: Vec<Node<'a>>,
}
impl<'a> Renamer<'a> {
    pub(crate) fn new(ids: &'a BTreeMap<String, String>) -> Self {
        let mut out = Self {
            nodes: vec![Node::default()],
        };
        for (old, new) in ids {
            if old.is_empty() {
                continue;
            }
            let mut at = 0;
            for byte in old.bytes() {
                at = if let Some(&next) = out.nodes[at].edges.get(&byte) {
                    next
                } else {
                    let next = out.nodes.len();
                    out.nodes.push(Node::default());
                    out.nodes[at].edges.insert(byte, next);
                    next
                };
            }
            out.nodes[at].replacement = Some(new);
        }
        out
    }
    pub(crate) fn rename(&self, s: &str) -> String {
        let word = |c: u8| c.is_ascii_alphanumeric() || c == b'_';
        let bytes = s.as_bytes();
        let mut out = String::new();
        let mut cursor = 0;
        let mut copied = 0;
        while cursor < bytes.len() {
            if cursor > copied && word(bytes[cursor - 1]) {
                cursor += s[cursor..]
                    .chars()
                    .next()
                    .expect("character boundary")
                    .len_utf8();
                continue;
            }
            let mut at = 0;
            let mut end = cursor;
            let mut best = None;
            while end < bytes.len() {
                let Some(&next) = self.nodes[at].edges.get(&bytes[end]) else {
                    break;
                };
                at = next;
                end += 1;
                if let Some(replacement) = self.nodes[at].replacement
                    && (end == bytes.len() || !word(bytes[end]))
                {
                    best = Some((end, replacement));
                }
            }
            if let Some((end, replacement)) = best {
                out.push_str(&s[copied..cursor]);
                out.push_str(replacement);
                cursor = end;
                copied = end;
            } else {
                cursor += s[cursor..]
                    .chars()
                    .next()
                    .expect("character boundary")
                    .len_utf8();
            }
        }
        out.push_str(&s[copied..]);
        out
    }
}

pub(crate) fn rename_ids(s: &str, ids: &BTreeMap<String, String>) -> String {
    Renamer::new(ids).rename(s)
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

#[cfg(test)]
mod indexed_tests {
    use super::*;
    #[test]
    fn indexed_aliases_match_the_reference_token_algorithm() {
        let ids = BTreeMap::from([
            ("f:hole:1".into(), "f:hole:f8".into()),
            ("f:hole:10".into(), "f:hole:f9".into()),
            ("d:f:hole:1/pitch.length".into(), "d:replacement".into()),
        ]);
        let names = Renamer::new(&ids);
        for left in [
            "",
            "α",
            "x",
            "f:hole:1",
            "f:hole:10",
            "d:f:hole:1/pitch.length",
        ] {
            for gap in ["", " ", "/", ":", "_", "a", "α"] {
                for right in [
                    "",
                    "α",
                    "x",
                    "f:hole:1",
                    "f:hole:10",
                    "d:f:hole:1/pitch.length",
                ] {
                    let text = format!("{left}{gap}{right}");
                    assert_eq!(
                        names.rename(&text),
                        reference_rename(&text, &ids),
                        "{text:?}"
                    );
                }
            }
        }
    }
    fn reference_rename(s: &str, ids: &BTreeMap<String, String>) -> String {
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

    #[test]
    fn compiled_aliases_keep_longest_tokens_boundaries_and_unicode() {
        let ids = BTreeMap::from([
            ("f:hole:1".into(), "f:hole:f8".into()),
            ("f:hole:10".into(), "f:hole:f9".into()),
            ("d:f:hole:1/pitch.length".into(), "d:replacement".into()),
        ]);
        let names = Renamer::new(&ids);
        assert_eq!(
            names.rename("α f:hole:1;f:hole:10 / d:f:hole:1/pitch.length"),
            "α f:hole:f8;f:hole:f9 / d:replacement"
        );
        assert_eq!(
            names.rename("xf:hole:1 f:hole:11 f:hole:1_suffix"),
            "xf:hole:1 f:hole:11 f:hole:1_suffix"
        );
        assert_eq!(names.rename("f:hole:1/f:hole:10"), "f:hole:f8/f:hole:f9");
        assert_eq!(names.rename("f:hole:f8"), "f:hole:f8");
    }
}
