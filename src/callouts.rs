//! Shared manufacturing presentation and font-measured, word-preserving callout wrapping.
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Style {
    #[default]
    Auto,
    Compact,
    Full,
    Note,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub style: Style,
}

pub(crate) fn style(spec: &crate::model::Spec, owner: &str) -> Style {
    spec.features
        .get(owner)
        .and_then(|f| f.callouts)
        .or(spec.sheet.callouts)
        .unwrap_or_default()
        .style
}

/// A standalone newline is a callout line break, distinct from a numeric deviation stack.
pub const BREAK: &str = "\n";
/// Discretionary wraps may be repacked after precision, tolerance or authored wording changes.
const SOFT_BREAK: &str = "\u{2028}";
pub(crate) fn is_break(term: &str) -> bool {
    term == BREAK || term == SOFT_BREAK
}

pub const WIDTH: f64 = 48.0;
pub use crate::bridge::holes::{BOTH_SIDES, FAR_SIDE};
/// The same font-derived baseline advance used by ordinary multiline font text.
pub fn line_spacing() -> f64 {
    crate::dims::font().line_spacing(crate::dims::TEXT)
}

pub fn lines(terms: &[String]) -> Vec<&[String]> {
    terms.split(|t| is_break(t)).collect()
}

pub fn label(terms: &[String]) -> String {
    lines(terms)
        .iter()
        .map(|line| line.join(" "))
        .collect::<Vec<_>>()
        .join("\n")
}

fn width(terms: &[String]) -> f64 {
    let ink = crate::dims::label_ink(terms, crate::dims::font());
    ink.max[0] - ink.min[0]
}

/// Python's 48 mm policy: intact words; a semicolon closes a half-used line.
/// Explicit breaks are retained. Numeric upper/lower deviation tokens stay intact.
pub fn wrap(terms: &[String]) -> Vec<String> {
    // The private placement comparison predates multiline callouts. Keep its
    // compact corridors intact; every live label still uses measured wrapping.
    if crate::nominal::comparison() {
        return terms.to_vec();
    }
    use sha2::{Digest, Sha256};
    static MEMO: crate::planning::BoundedMemo<Vec<String>> = crate::planning::BoundedMemo::new(512);
    let terms: Vec<_> = terms
        .iter()
        .filter(|t| t.as_str() != SOFT_BREAK)
        .cloned()
        .collect();
    let key: [u8; 32] =
        // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
        Sha256::digest(serde_json::to_vec(&terms).expect("label terms serialize")).into();
    if let Some(wrapped) = MEMO.get(&key) {
        return wrapped;
    }
    let wrapped = wrap_measured(&terms, &width);
    MEMO.insert(key, wrapped.clone());
    wrapped
}

/// Plain PMI labels use font spaces rather than dimension-symbol token cells.
pub(crate) fn prose(text: &str) -> Vec<String> {
    let terms = text
        .split_whitespace()
        .map(str::to_string)
        .collect::<Vec<_>>();
    let wrapped = wrap_measured(&terms, &|terms| {
        let ink = crate::dims::font().baseline(&terms.join(" "), crate::dims::TEXT);
        ink.max[0] - ink.min[0]
    });
    lines(&wrapped).iter().map(|line| line.join(" ")).collect()
}

fn wrap_measured(terms: &[String], width: &dyn Fn(&[String]) -> f64) -> Vec<String> {
    let mut out = Vec::new();
    for (i, forced) in lines(terms).iter().enumerate() {
        if i > 0 {
            out.push(BREAK.into());
        }
        let (forced, tail) = match forced.split_last() {
            Some((last, body)) if last == BOTH_SIDES && !body.is_empty() => (body, Some(last)),
            _ => (*forced, None),
        };
        // As in Python, short labels keep their original token cells and spacing.
        if width(forced) <= WIDTH {
            out.extend_from_slice(forced);
            out.extend(tail.cloned());
            continue;
        }
        let mut line: Vec<String> = Vec::new();
        let flush = |out: &mut Vec<String>, line: &mut Vec<String>| {
            out.append(line);
            out.push(SOFT_BREAK.into());
        };
        for token in forced {
            let words: Vec<_> = if token.contains('\n') {
                vec![token.as_str()]
            } else {
                token.split_whitespace().collect()
            };
            for word in words {
                let mut next = line.clone();
                next.push(word.into());
                if !line.is_empty() && width(&next) > WIDTH {
                    // A depth, counterbore or countersink symbol stays with its value.
                    let symbol = (line.len() > 1
                        && line
                            .last()
                            .is_some_and(|w| matches!(w.as_str(), "↧" | "⌴" | "⌵")))
                    .then(|| line.pop())
                    .flatten();
                    flush(&mut out, &mut line);
                    line.extend(symbol);
                }
                line.push(word.into());
                if word.ends_with(';') && width(&line) >= WIDTH / 2.0 {
                    flush(&mut out, &mut line);
                }
            }
        }
        out.append(&mut line);
        if out.last().is_some_and(|s| s == SOFT_BREAK) {
            out.pop();
        }
        out.extend(tail.cloned());
    }
    out
}

#[cfg(test)]
mod tests {
    #[test]
    fn a_wrapped_depth_symbol_stays_with_its_value() {
        // STC-10's counterbore callout used to leave `↧` at a line end and `10` alone below it.
        let terms: Vec<String> = ["⌀10", "↧", "10"].map(String::from).to_vec();
        // Two words fit a line, three do not: the break falls after `↧` unless it is carried.
        let wrapped = super::wrap_measured(&terms, &|t| 20.0 * t.len() as f64);
        let lines = super::lines(&wrapped);
        assert!(lines.len() > 1, "{wrapped:?}");
        assert!(
            lines
                .iter()
                .all(|l| l.last().map(String::as_str) != Some("↧")),
            "{wrapped:?}"
        );
        assert!(
            lines
                .iter()
                .any(|l| l.ends_with(&["↧".into(), "10".into()]))
        );
    }
}
