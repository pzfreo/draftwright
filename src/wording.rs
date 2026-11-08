//! How a problem's message names what it is about, in the words a draftsman reads on the sheet
//! (draftwright-rust #24): a dimension by the text it prints (`2× 30`), a feature by what is
//! seen of it (`the ⌀6.6 hole`), a view by its name (`the plan view`). The IDs stay in the
//! problem's `subjects` and `refs`; its message never carries one (`tests/problem_wording.rs`
//! scans every message of the test parts for them).

use crate::bridge::Feature;
use crate::dims::fmt;
use crate::model::Analysis;

/// *s* to start a sentence with: its first letter a capital, or, for a label that starts with a
/// number or symbol (`30.1`, `⌀8 THRU`), `The dimension` before it.
pub fn capital(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) if f.is_alphabetic() => f.to_uppercase().chain(c).collect(),
        Some(_) => format!("The dimension {s}"),
        None => String::new(),
    }
}

/// The feature as a draftsman sees it: its kind, with the size that tells it apart where its
/// record gives one (`the ⌀6.6 hole`, `the R3 blend`, `the 6 wide slot`).
pub fn feature(f: &Feature) -> String {
    let p = |name: &str| f.parameters.get(name).and_then(|v| v.as_f64());
    let kind = words(&f.kind);
    let sized = match f.kind.as_str() {
        "hole" | "boss" | "turned_step" => p("diameter").map(|d| format!("⌀{} {kind}", fmt(d))),
        "countersink" => p("major_diameter").map(|d| format!("⌀{} {kind}", fmt(d))),
        "blend" | "fillet" | "circular_blind_step" => {
            p("radius").map(|r| format!("R{} {kind}", fmt(r)))
        }
        "chamfer" => p("leg1").map(|l| format!("{} {kind}", fmt(l))),
        "slot" => p("width").map(|w| format!("{} wide {kind}", fmt(w))),
        "flat" => p("across").map(|a| format!("{} {kind}", fmt(a))),
        "polygonal_boss" => p("across_flats").map(|a| format!("{} A/F {kind}", fmt(a))),
        "angled_step" | "paired_ramp_step" => p("angle").map(|a| format!("{}° {kind}", fmt(a))),
        "plate" => p("hi")
            .zip(p("lo"))
            .map(|(hi, lo)| format!("{} thick {kind}", fmt(hi - lo))),
        _ => None,
    };
    format!("the {}", sized.unwrap_or(kind))
}

/// The feature *id* of *a* as [`feature`] names it; a hole pattern by its members
/// (`the 6× ⌀6.6 hole pattern`). One the Analysis does not have is named by its kind alone.
pub fn feature_id(a: &Analysis, id: &str) -> String {
    if let Some(f) = a.features.iter().find(|f| f.id == id) {
        return match f.kind.as_str() {
            "slot_pattern" => {
                let n = f.parameters.get("slots").and_then(|v| v.as_array());
                match n {
                    Some(n) => format!("the {}× slot pattern", n.len()),
                    None => "the slot pattern".into(),
                }
            }
            _ => feature(f),
        };
    }
    if let Some(p) = a
        .recognition
        .iter()
        .flat_map(|r| &r.patterns)
        .find(|p| p.id == id)
    {
        let d = p
            .members
            .first()
            .and_then(|m| a.features.iter().find(|f| f.id == *m))
            .and_then(|f| f.parameters.get("diameter"))
            .and_then(|v| v.as_f64());
        return match d {
            Some(d) => format!("the {}× ⌀{} hole pattern", p.members.len(), fmt(d)),
            None => format!("the {}× hole pattern", p.members.len()),
        };
    }
    kind_of(id)
}

/// A feature named from its ID's kind alone (`f:hole_pattern:f6` is `the hole pattern`), where
/// no Analysis is at hand.
pub fn kind_of(id: &str) -> String {
    match id.strip_prefix("f:").and_then(|s| s.split(':').next()) {
        Some(kind) => format!("the {}", words(kind)),
        None if id == "envelope" => "the part's overall size".into(),
        None if id == "step_level" => "the step heights".into(),
        None => "a feature".into(),
    }
}

/// A view by its name: `the plan view`, `section A–A`.
pub fn view(id: &str) -> String {
    let name = id.strip_prefix("v:").unwrap_or(id);
    match name {
        "iso" => "the isometric view".into(),
        _ => match name.strip_prefix("section_") {
            Some(letters) => {
                let l: Vec<String> = letters
                    .chars()
                    .map(|c| c.to_uppercase().collect())
                    .collect();
                format!("section {}", l.join("\u{2013}"))
            }
            None => format!("the {} view", words(name)),
        },
    }
}

/// A block of the sheet by its ID: a view, a table, the hole table, the manufacturing
/// requirements table.
pub fn block(id: &str) -> String {
    if id.starts_with("v:") {
        view(id)
    } else if id == crate::lint::blends::RADIUS_TABLE {
        "the radius table".into()
    } else if let Some(k) = id.strip_prefix("t:") {
        format!("table {k}")
    } else if id.starts_with("hole_table") || id.starts_with("hole_schedule") {
        "the hole table".into()
    } else if id.starts_with("spring_data_") {
        "the spring data table".into()
    } else if id == crate::pmi_lowering::SCHEDULE_ID {
        "the manufacturing requirements table".into()
    } else {
        "a block".into()
    }
}

/// A drawn item by its ID, its kind and the text it prints: a dimension or callout by its label
/// (`2× 30`), a frame or datum symbol by what it says, a note block, table or line by what it
/// is.
pub fn item(id: &str, kind: &str, label: &str) -> String {
    let label = label.trim();
    if id.starts_with("pmi:datum") {
        return format!("datum feature symbol {label}");
    }
    if id.starts_with("pmi:geometric_tolerance") {
        return format!("the feature control frame {label}");
    }
    if id.starts_with("v:") {
        return view(id);
    }
    match kind {
        "notes" => "the general notes".into(),
        "table" => block(id),
        "ink" => "a centre line".into(),
        _ if id.starts_with("n:") => note(label),
        _ if id.starts_with("t:") => block(id),
        _ if !label.is_empty() => label.into(),
        _ => "a dimension".into(),
    }
}

/// A view's line work by its kind (`crate::linework`): `a centre line`, `a pitch circle`.
pub fn line_work(kind: &str) -> String {
    match kind {
        "axis" => "a centre line".into(),
        "cross" => "a centre mark".into(),
        "pitch_circle" => "a pitch circle".into(),
        "cutting_plane" => "the cutting-plane line".into(),
        k => format!("a {} line", words(k)),
    }
}

/// A general note by its text, its first 40 characters where it is longer.
pub fn note(text: &str) -> String {
    let text = text.trim();
    if text.is_empty() {
        return "a general note".into();
    }
    let short: String = text.chars().take(40).collect();
    let more = if short.len() < text.len() { "…" } else { "" };
    format!("the note \"{short}{more}\"")
}

/// A dimension the message knows only by its ID (one left out, or a spec entry): what it
/// measures, of which feature (`the bore diameter of the ⌀8 hole`, `the overall width`).
pub fn dimension_id(a: &Analysis, id: &str) -> String {
    let Some((feature, parameter)) = id.strip_prefix("d:").and_then(|s| s.rsplit_once('/')) else {
        return "a dimension".into();
    };
    let role = parameter.split('.').next().unwrap_or(parameter);
    match feature {
        "envelope" if role.starts_with("notch") => format!("the {}", words(role)),
        "envelope" => format!("the overall {}", words(role)),
        "step_level" => format!("the {}", words(role)),
        f if f.starts_with("pmi:") => "the PMI dimension".into(),
        f => format!("the {} of {}", words(role), feature_id(a, f)),
    }
}

/// Why a dimension is left out, from its state, without naming the item that states it (its
/// omitted entry's basis and state carry that).
pub fn left_out(state: &crate::items::State) -> &'static str {
    use crate::items::State;
    match state {
        State::OmittedByUser => "the spec omits it",
        State::NotAuthored => "the authored set does not list it",
        State::Covered { .. } => "another dimension states what it would",
        _ => "it is not drawn",
    }
}

/// `snake_case` as words.
fn words(s: &str) -> String {
    s.replace('_', " ")
}

/// IDs as a message may not carry them: the forms of `docs/model.md`'s ID table that name an
/// item or feature (`d:`, `f:`, `v:`, `pmi:`, `n:`, `t:`) and a face (`face:`).
pub fn carries_id(message: &str) -> Option<&str> {
    const PREFIXES: [&str; 7] = ["d:", "f:", "v:", "pmi:", "n:", "t:", "face:"];
    let mut start = 0;
    for (i, c) in message.char_indices() {
        if !(c.is_alphanumeric() || c == '_' || c == ':') {
            start = i + c.len_utf8();
            continue;
        }
        let token = &message[start..];
        if i == start
            && let Some(p) = PREFIXES.iter().find(|p| token.starts_with(**p))
            && token[p.len()..]
                .chars()
                .next()
                .is_some_and(|c| c.is_ascii_alphanumeric())
        {
            let end = token
                .find(|c: char| c.is_whitespace() || c == ',' || c == ')' || c == ';')
                .unwrap_or(token.len());
            return Some(token[..end].trim_end_matches(['.', '\'']));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_found_and_words_are_not() {
        assert_eq!(
            carries_id("The extension of d:f:hole_pattern:f6/grid_pitch.length.col (2× 30)."),
            Some("d:f:hole_pattern:f6/grid_pitch.length.col")
        );
        assert_eq!(carries_id("It crosses f:hole:f9."), Some("f:hole:f9"));
        assert_eq!(carries_id("over the v:plan view"), Some("v:plan"));
        assert_eq!(carries_id("set sheet/title/tolerance, 2× 30 at 2:1"), None);
        assert_eq!(carries_id("Section A–A, the ⌀6.6 hole: R3 at d: 5"), None);
    }

    #[test]
    fn views_read_as_their_names() {
        assert_eq!(view("v:plan"), "the plan view");
        assert_eq!(view("v:section_aa"), "section A\u{2013}A");
        assert_eq!(view("v:iso"), "the isometric view");
    }
}
