//! Authored tolerances and fits: what the spec says of a size reaches the label verbatim
//! (ARCH0011-1: a tolerance or fit belongs to a dimension), and nothing else does (ADR5-D2).
//!
//! - **A tolerance** is set on a dimension by its ID (`dimensions/<id>/tolerance`): `±T`, or
//!   `+U -L` (an upper and a lower deviation, both magnitudes), as draftwright's
//!   `.tolerance(t)` and `.tolerance(lo, hi)` print them (`_fmt_tolerance`). It is printed after
//!   the term that states the dimension's value, at the drawn precision (one decimal) or the
//!   authored digits if there are more: the drawn precision is a minimum, and an authored
//!   magnitude is never rounded into another requirement. A zero deviation is written `0`,
//!   unsigned (ISO 129-1): `+0 -0.1` prints `0 -0.1`.
//! - **A fit** is set on a hole or a cylindrical boss (`features/<id>/fit`): an ISO 286 class
//!   independent of the optional deviation table (holes H, G, F; shafts h, g, f, js, k, n, p;
//!   grades 5 to 11; nominal sizes up to 250 mm). It is printed after the diameter that states
//!   the feature's size, as the class alone (`⌀8.1 H7 THRU`) by default; `sheet/fit_deviations: true` adds stacked deviations.
//!
//! A band is withheld, and the plan says so, when the sheet cannot state it truthfully:
//! - **a band of nothing** (ARCH0019-2): with the authored digits kept, only a band whose every
//!   magnitude is zero rounds to zero, and `±0.0` states no tolerance;
//! - **a collapsed mark whose members disagree** (ARCH0019-3): an `n×` callout states one size
//!   for every member, so it states a fit only when every member has the same one. (A pattern's
//!   `n× p` pitch needs no such check here: the hole planner collapses only gaps equal within
//!   1e-4 mm, where draftwright's `_pitch_text` has to withhold a band over jittered ones.)
//!
//! An entry for a dimension the plan leaves out, or a fit on a feature no planned callout
//! states, is reported against its ID, never dropped in silence.

use std::collections::{BTreeMap, BTreeSet};

use super::{Omitted, Want, levenshtein};
use crate::items::{Op, State, key};
use crate::model::{Analysis, Invalid, Problem, Spec};

// The fit table's classes and grades, in order, which [`choices`] lists and [`apply`] sorts into
// hole and shaft classes, and the largest nominal size it covers, mm (ISO 286's band up to and
// including 250), are haecceity's, as the table is (#258).
use haecceity::presentation::tolerance::{GRADES, HOLE_CLASSES, SHAFT_CLASSES};

/// The requirement notation of fits and bands: ISO 286 classes and their limit deviations, and
/// a band or a class's stacked deviations as text. haecceity's shared presentation module owns
/// it, so the sheet and Specify's STEP callouts state a requirement the same way (#258).
pub(super) use haecceity::presentation::tolerance::{band, deviations, limits_term, with_limits};

/// Reading a class is independent of our optional numerical-deviation table.
pub(super) fn fit(code: &str) -> Result<(&str, u32), String> {
    let n = code
        .find(|c: char| c.is_ascii_digit())
        .unwrap_or(code.len());
    let (letters, grade) = code.split_at(n);
    let classes = [
        "A", "B", "C", "CD", "D", "E", "EF", "F", "FG", "G", "H", "JS", "J", "K", "M", "N", "P",
        "R", "S", "T", "U", "V", "X", "Y", "Z", "ZA", "ZB", "ZC",
    ];
    let valid_case = letters.bytes().all(|c| c.is_ascii_uppercase())
        || letters.bytes().all(|c| c.is_ascii_lowercase());
    if valid_case
        && classes
            .iter()
            .any(|class| class.eq_ignore_ascii_case(letters))
        && (grade == "01"
            || grade
                .parse::<u32>()
                .is_ok_and(|g| g <= 18 && grade == g.to_string()))
    {
        return Ok((letters, grade.parse().unwrap()));
    }
    Err(format!(
        "{code:?} is not an ISO 286 fit class; use a class such as H7, N9 or js9."
    ))
}

fn fit_text(
    code: &str,
    nominal: f64,
    id: &str,
    spec: &Spec,
    problems: &mut Vec<Problem>,
) -> String {
    if !limits_shown(spec) {
        return code.into();
    }
    if let Some(limits) = deviations(code, nominal) {
        return with_limits(code, limits);
    }
    problems.push(problem("fit_deviations_unavailable", id,
        format!("The fit {code} is printed, but requested numerical deviations at {nominal} mm are outside the supported ISO 286 table; no deviations were invented."), vec![id.into()]));
    code.into()
}

/// A kept item, as [`super::plan`] holds it: the want, whether it is a default, its view and the
/// views that look along it.
type Kept = (Want, bool, Option<&'static str>, Vec<&'static str>);

/// The feature kinds a fit can be set on, and the parameter whose size it qualifies.
const FITTED: [(&str, &str); 2] = [("hole", "bore.diameter"), ("boss", "boss.diameter")];
/// The accepted forms, as the refusal names them.
const FORMS: [&str; 2] = ["±0.05", "+0.1 -0.02"];

/// Supported classes at this feature's size, from the same table used by validation.
pub(crate) fn choices(kind: &str, nominal: f64) -> Vec<String> {
    let letters: &[&str] = match kind {
        "hole" => &HOLE_CLASSES,
        "boss" => &SHAFT_CLASSES,
        _ => return Vec::new(),
    };
    letters
        .iter()
        .flat_map(|letter| GRADES.iter().map(move |grade| format!("{letter}{grade}")))
        .filter(|code| deviations(code, nominal).is_some())
        .collect()
}

/// A magnitude as authored (`0.05`, `1`): digits with an optional fraction, no sign.
fn magnitude(s: &str) -> Option<&str> {
    let (whole, frac) = s.split_once('.').unwrap_or((s, "0"));
    let digits = |t: &str| !t.is_empty() && t.bytes().all(|b| b.is_ascii_digit());
    (digits(whole) && digits(frac)).then_some(s)
}

/// A magnitude printed as draftwright prints it: its authored digits, with at least the drawn
/// precision (one decimal), so `1` reads `1.0` and `0.05` stays `0.05`.
fn printed(m: &str) -> String {
    match m.split_once('.') {
        Some(_) => m.to_string(),
        None => format!("{m}.0"),
    }
}

fn zero(m: &str) -> bool {
    m.bytes().all(|b| b == b'0' || b == b'.')
}

/// An authored tolerance read: its printed text, and whether it is a band of nothing. The error
/// says what was wrong.
fn tolerance(text: &str) -> Result<(String, bool), String> {
    let t = text.trim();
    if let Some(m) = t.strip_prefix('±').and_then(magnitude) {
        return Ok((format!("±{}", printed(m)), zero(m)));
    }
    let parts: Vec<&str> = t.split_whitespace().collect();
    if let [upper, lower] = parts[..]
        && let (Some(u), Some(l)) = (
            upper.strip_prefix('+').and_then(magnitude),
            lower.strip_prefix('-').and_then(magnitude),
        )
    {
        // A zero deviation is written `0`, unsigned (ISO 129-1), as an imported band is ([`band`]).
        let deviation = |sign: char, m: &str| {
            if zero(m) {
                "0".to_string()
            } else {
                format!("{sign}{}", printed(m))
            }
        };
        return Ok((
            format!("{} {}", deviation('+', u), deviation('-', l)),
            zero(u) && zero(l),
        ));
    }
    // Close but wrong: the ASCII spellings of ± and a limit pair written with a slash.
    let close = t.replace("+/-", "±").replace("+-", "±").replace('/', " ");
    if close != t
        && let Ok((reads, _)) = tolerance(&close)
    {
        return Err(format!("{text:?} reads as {reads:?}"));
    }
    Err(format!(
        "{text:?} is not a tolerance draftwright reads: a symmetric band is \"±T\" and a pair of deviations \"+U -L\" (the upper above the nominal, the lower below it, both as magnitudes)"
    ))
}

/// Equal decimal bands can have different authored precision or symmetric notation.
pub(super) fn same_band(a: &str, b: &str) -> bool {
    type Decimal<'a> = (&'a str, &'a str);
    type Band<'a> = (Decimal<'a>, Decimal<'a>);
    fn number(s: &str) -> Option<Decimal<'_>> {
        let s = magnitude(s)?;
        let (whole, fraction) = s.split_once('.').unwrap_or((s, ""));
        Some((
            whole.trim_start_matches('0'),
            fraction.trim_end_matches('0'),
        ))
    }
    fn normalized(t: &str) -> Option<Band<'_>> {
        let t = t.trim();
        if let Some(n) = t.strip_prefix('±').and_then(number) {
            return Some((n, n));
        }
        let mut words = t.split_whitespace();
        let upper = number(words.next()?.strip_prefix('+')?)?;
        let lower = number(words.next()?.strip_prefix('-')?)?;
        words.next().is_none().then_some((upper, lower))
    }
    normalized(a)
        .zip(normalized(b))
        .is_some_and(|(a, b)| a == b)
}

/// ISO fits qualify a physical size between opposed surfaces, never a station or pitch.
fn size_parameter(p: &str) -> bool {
    let (quantity, unit) = p.split_once('.').unwrap_or((p, ""));
    p == "diameter"
        || (unit == "diameter" && quantity != "bolt_circle")
        || (unit == "length"
            && (matches!(
                quantity,
                "width"
                    | "depth"
                    | "height"
                    | "thickness"
                    | "step"
                    | "groove"
                    | "polygon_across_flats"
                    | "profile_across_flats"
                    | "general_size"
            ) || quantity.ends_with("_width")
                || quantity.ends_with("_length")
                || quantity.ends_with("_depth")
                || quantity.ends_with("_height")
                || quantity.ends_with("_thickness")))
}

fn feature_size(a: &Analysis, spec: &Spec, w: &Want) -> bool {
    // Coverage riders do not change the quantity the leading nominal states.
    if w.measurements
        .first()
        .is_some_and(|m| size_parameter(&m.parameter))
    {
        return true;
    }
    // Authored linear quantities carry geometry parameters until ownership is attached.
    // Only a pair of parallel physical planes proves a linear feature of size;
    // an axis/point-to-datum or axis-to-axis location does not.
    let Some(refs) = spec.dimensions.get(&w.id).and_then(|d| d.between.as_ref()) else {
        return false;
    };
    let [p, q] = refs.as_slice() else {
        return false;
    };
    use crate::model::dimension_geometry::Reference;
    match (a.dimension_reference(p), a.dimension_reference(q)) {
        (Ok(Reference::Plane { normal: p, .. }), Ok(Reference::Plane { normal: q, .. })) => {
            ((0..3).map(|k| p[k] * q[k]).sum::<f64>().abs() - 1.).abs() < 1e-6
        }
        _ => false,
    }
}

/// Compare effective requirements, including an unedited owner's general tolerance.
pub(super) fn same_requirement(
    a: &Analysis,
    spec: &Spec,
    p: &Want,
    q: &Want,
    subjects: &BTreeSet<String>,
) -> bool {
    let inherited = spec.sheet.title.get("tolerance").cloned().or_else(|| {
        crate::pmi_lowering::sheet_requirements(a)
            .0
            .into_iter()
            .find(|r| r.kind == "general_tolerance")
            .map(|r| r.value)
    });
    let requirement = |w: &Want| {
        let edit = spec.dimensions.get(&w.id);
        if let Some(t) = edit.and_then(|e| e.tolerance.as_deref()) {
            return (false, Some(t));
        }
        if let Some(f) = edit.and_then(|e| e.fit.as_deref()) {
            return (true, Some(f));
        }
        if let Some(f) = w
            .measurements
            .iter()
            .filter(|m| {
                m.parameter == parameter(&w.id)
                    && FITTED.iter().any(|(_, p)| *p == m.parameter)
                    && (subjects.is_empty() || subjects.contains(&m.feature))
            })
            .find_map(|m| spec.features.get(&m.feature).and_then(|f| f.fit.as_deref()))
        {
            return (true, Some(f));
        }
        (
            false,
            (crate::dims::is_envelope_extent(&w.id)
                .then_some(spec.envelope_tolerance.as_deref())
                .flatten())
            .or(inherited.as_deref()),
        )
    };
    let (fit_p, p) = requirement(p);
    let (fit_q, q) = requirement(q);
    fit_p == fit_q
        && match (p, q) {
            (Some(p), Some(q)) => p == q || (!fit_p && same_band(p, q)),
            (None, None) => true,
            _ => false,
        }
}

/// Whether the sheet requests stacked fit deviations (off by default).
pub(super) fn limits_shown(spec: &Spec) -> bool {
    spec.sheet.fit_deviations.or(spec.sheet.fit_limits) == Some(true)
}

/// Checks every tolerance and fit the spec authors reads as one: the form, never the part (that
/// is [`apply`]'s). A refusal names the entry.
pub(crate) fn check(spec: &Spec) -> Result<(), Invalid> {
    if let Some(t) = &spec.envelope_tolerance {
        tolerance(t).map_err(|why| Invalid {
            path: None,
            also: Vec::new(),
            message: format!("The envelope tolerance cannot be read: {why}."),
            choices: FORMS.map(String::from).to_vec(),
        })?;
    }
    for (id, entry) in &spec.dimensions {
        if let Some(code) = &entry.fit {
            fit(code).map_err(|why| Invalid {
                path: None,
                also: Vec::new(),
                message: why,
                choices: vec![],
            })?;
            if entry.tolerance.is_some() {
                return Err(Invalid {
                    path: None,
                    also: Vec::new(),
                    message: format!(
                        "The fit of {id} conflicts with its explicit tolerance; a size takes one or the other."
                    ),
                    choices: vec![],
                });
            }
        }
        if let Some(t) = &entry.tolerance {
            tolerance(t).map_err(|why| Invalid {
                path: None,
                also: Vec::new(),
                message: format!("The tolerance of {id:?} cannot be read: {why}."),
                choices: FORMS.map(String::from).to_vec(),
            })?;
        }
    }
    for (id, entry) in &spec.features {
        if let Some(code) = &entry.fit {
            fit(code).map_err(|why| Invalid {
                path: None,
                also: Vec::new(),
                message: format!("The fit of {id:?} cannot be read: {why}."),
                choices: vec![],
            })?;
        }
    }
    Ok(())
}

/// The parameter an ID names: `d:<feature>/<parameter>`.
fn parameter(id: &str) -> &str {
    id.rsplit_once('/').map_or(id, |(_, p)| p)
}

/// Whether the nominal term is a supported thread designation in place of a plain
/// diameter. A missing diameter glyph alone says nothing about a thread. A tapping
/// drill printed before the designation remains the value that takes a bore tolerance.
pub(super) fn thread_designated(w: &super::Want) -> bool {
    if w.measurements
        .iter()
        .any(|m| matches!(m.parameter.as_str(), "step.diameter" | "od.diameter"))
        && w.terms
            .iter()
            .any(|t| t.starts_with('M') && t.as_bytes().get(1).is_some_and(u8::is_ascii_digit))
    {
        return true;
    }
    parameter(&w.id).ends_with("diameter")
        && value_term(&w.terms).is_some_and(|i| {
            // Manufacturing suffixes append depth/length prose after a comma or semicolon.
            let designation = w.terms[i].split([',', ';']).next().unwrap_or_default();
            crate::spec_notes::metric(designation).is_ok()
        })
}

/// The term a band follows: the first that states a number and is not a count (`4×`).
fn value_term(terms: &[String]) -> Option<usize> {
    // An internal thread's identity leads its tapping-drill line. A bore fit or
    // band qualifies that explicit drill, not the thread nominal in its designation.
    if let Some(thread) = terms
        .iter()
        .position(|t| t.starts_with('M') && t.as_bytes().get(1).is_some_and(u8::is_ascii_digit))
        && let Some(drill) = terms
            .iter()
            .enumerate()
            .skip(thread + 1)
            .find(|(_, t)| t.starts_with(['⌀', 'ø', 'Ø']))
            .map(|(i, _)| i)
    {
        return Some(drill);
    }
    terms.iter().position(|t| {
        t.chars().any(|c| c.is_ascii_digit())
            && !(t.ends_with('×') && t.trim_end_matches('×').bytes().all(|b| b.is_ascii_digit()))
    })
}

/// The edit path of a dimension's tolerance: a `/` inside the ID is written `~1`.
fn path(id: &str) -> String {
    format!("dimensions/{}/tolerance", key(id))
}

use super::warning as problem;

/// Puts the spec's tolerances and fits on the kept items' labels, before they are placed, and
/// reports what cannot be stated. Returns, for each kept item, what of it the spec gave
/// (`tolerance`, `fit`). Refused: a fit on a feature that does not exist or cannot take one, or
/// a size given contradictory tolerance and fit requirements. Numerical table coverage
/// affects only requested deviations.
pub(super) fn apply(
    analysis: &Analysis,
    spec: &Spec,
    kept: &mut [Kept],
    omitted: &[Omitted],
    problems: &mut Vec<Problem>,
) -> Result<Vec<Vec<String>>, Invalid> {
    check(spec)?;
    let patterns = analysis
        .recognition
        .as_ref()
        .map_or(&[][..], |r| r.patterns.as_slice());
    // Each fit, on a feature that can take it, at its size.
    let mut fits: BTreeMap<&str, &str> = BTreeMap::new();
    let mut nominals: BTreeMap<&str, f64> = BTreeMap::new();
    for (id, entry) in &spec.features {
        let Some(code) = entry.fit.as_deref() else {
            continue;
        };
        let Some(f) = analysis.features.iter().find(|f| f.id == *id) else {
            if let Some(p) = patterns.iter().find(|p| p.id == *id) {
                return Err(Invalid {
                    path: None,
                    also: Vec::new(),
                    message: format!(
                        "{id:?} is a hole pattern; a fit belongs to a hole, so set it on each of its holes (features/<hole>/fit)."
                    ),
                    choices: p.members.clone(),
                });
            }
            let mut near: Vec<&str> = analysis.features.iter().map(|f| f.id.as_str()).collect();
            near.sort_by_key(|c| (levenshtein(id, c), *c));
            near.truncate(5);
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!("There is no feature {id:?} on this part."),
                choices: near.into_iter().map(String::from).collect(),
            }
            .at("features", id));
        };
        if !FITTED.iter().any(|(k, _)| *k == f.kind) {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "{id:?} is a {}; a fit is set on a hole or a cylindrical boss.",
                    f.kind
                ),
                choices: vec![],
            });
        }
        // Invariant: check validated this same immutable fit code before apply.
        let (letters, grade) = fit(code).expect("checked");
        let hole = f.kind == "hole";
        if hole != letters.bytes().all(|c| c.is_ascii_uppercase()) {
            let other = if hole {
                letters.to_ascii_uppercase()
            } else {
                letters.to_ascii_lowercase()
            };
            let (what, class) = if hole {
                ("hole", "an upper-case hole class")
            } else {
                ("boss", "a lower-case shaft class")
            };
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "{code:?} reads as \"{other}{grade}\": {id:?} is a {what}, and takes {class}."
                ),
                choices: vec![format!("{other}{grade}")],
            });
        }
        let d = f.parameters.get("diameter").and_then(|v| v.as_f64());
        if !d.is_some_and(|d| d.is_finite() && d > 0.0) {
            return Err(Invalid {
                message: format!("The fit {code} of {id} needs a unique positive nominal size."),
                choices: vec![],
                path: None,
                also: vec![],
            });
        }
        fits.insert(id, code);
        // Invariant: The preceding validation found this same nominal in the fit table.
        nominals.insert(id, d.expect("in the table"));
    }

    let mut from_spec = Vec::with_capacity(kept.len());
    let mut stated: BTreeSet<&str> = BTreeSet::new();
    for (w, ..) in kept.iter_mut() {
        let mut given = Vec::new();
        let lead = parameter(&w.id).to_string();
        // The members whose size the label states, and their fits.
        let mut members: Vec<String> = Vec::new();
        if FITTED.iter().any(|(_, p)| *p == lead) {
            for m in &w.measurements {
                if m.parameter == lead && !members.contains(&m.feature) {
                    members.push(m.feature.clone());
                }
            }
        }
        let member_fits: Vec<Option<&str>> = members
            .iter()
            .map(|m| fits.get(m.as_str()).copied())
            .collect();
        for m in &members {
            if let Some((k, _)) = fits.get_key_value(m.as_str()) {
                stated.insert(*k);
            }
        }
        let extent_fit = spec.dimensions.get(&w.id).and_then(|e| e.fit.as_deref());
        if let Some(code) = extent_fit
            && member_fits.iter().flatten().any(|fit| *fit != code)
        {
            return Err(Invalid {
                message: format!(
                    "{} has conflicting dimension and feature fit requirements; retain one compatible class.",
                    w.id
                ),
                path: None,
                also: vec![],
                choices: vec![],
            });
        }
        let authored = spec
            .dimensions
            .get(&w.id)
            .and_then(|e| e.tolerance.clone())
            .or_else(|| {
                (crate::dims::is_envelope_extent(&w.id) && extent_fit.is_none())
                    .then(|| spec.envelope_tolerance.clone())
                    .flatten()
            });
        if authored.is_some() && member_fits.iter().any(Option::is_some) {
            let fitted: Vec<&str> = members
                .iter()
                .zip(&member_fits)
                .filter(|(_, f)| f.is_some())
                .map(|(m, _)| m.as_str())
                .collect();
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "{:?} has a tolerance and {} a fit; a size takes one or the other. Remove {} or the fit.",
                    w.id,
                    fitted.join(", "),
                    path(&w.id)
                ),
                choices: vec![],
            });
        }
        let band = if let Some(code) = extent_fit {
            if !w.value.is_finite()
                || w.value <= 0.0
                || matches!(w.shape, super::Shape::Angular { .. })
                || !feature_size(analysis, spec, w)
            {
                return Err(Invalid {
                    message: format!(
                        "The fit of {} needs a positive feature of size, not a location, pitch, radius or angle.",
                        w.id
                    ),
                    path: None,
                    also: vec![],
                    choices: vec![],
                });
            }
            given.push("fit".into());
            Some(fit_text(code, w.value, &w.id, spec, problems))
        } else if let Some(t) = authored {
            // Invariant: check validated this same immutable tolerance text before apply.
            let (text, nothing) = tolerance(&t).expect("checked");
            if nothing {
                problems.push(Problem {
                    fix: vec![Op::remove(path(&w.id))],
                    ..problem(
                        "tolerance_withheld",
                        &w.id,
                        format!(
                            "{} is not printed on {}: a band of nothing ({text}) states no tolerance (ARCH0019-2). Give it a band, or remove the tolerance.",
                            t.trim(),
                            super::wording_of(w)
                        ),
                        vec![w.id.clone()],
                    )
                });
                None
            } else {
                given.push("tolerance".to_string());
                w.basis += "; its tolerance is the spec's";
                Some(text)
            }
        } else if member_fits.iter().all(Option::is_some) && !member_fits.is_empty() {
            // Invariant: The preceding any(is_none) check returned before this branch.
            let first = member_fits[0].expect("all are some");
            if member_fits.iter().all(|f| *f == Some(first)) {
                given.push("fit".to_string());
                w.basis += &format!("; its fit is the spec's ({})", members.join(", "));
                Some(fit_text(
                    first,
                    nominals[members[0].as_str()],
                    &w.id,
                    spec,
                    problems,
                ))
            } else {
                None
            }
        } else {
            None
        };
        // A collapsed mark whose members do not all have one fit states none (ARCH0019-3).
        if member_fits.iter().any(Option::is_some) && band.is_none() {
            // Each member's fit, in the order of the problem's subjects, which name the
            // members: the message carries no ID (#24).
            let told: Vec<&str> = member_fits.iter().map(|f| f.unwrap_or("none")).collect();
            problems.push(problem(
                "tolerance_withheld",
                &w.id,
                format!(
                    "No fit is printed on {}: its {} members do not all have the same fit ({}, in the order of the problem's subjects), and one label would claim it of every member (ARCH0019-3). Give them all the same fit, or none.",
                    super::wording_of(w),
                    members.len(),
                    told.join(", ")
                ),
                std::iter::once(w.id.clone())
                    .chain(members.iter().cloned())
                    .collect(),
            ));
        }
        if let Some(text) = band.as_ref().filter(|_| thread_designated(w)) {
            given.clear();
            problems.push(problem(
                "tolerance_withheld",
                &w.id,
                format!(
                    "{text} is not printed on {}: its thread designation states the nominal size and carries the thread's own tolerance class.",
                    super::wording_of(w)
                ),
                vec![w.id.clone()],
            ));
        } else if let Some(text) = band {
            match value_term(&w.terms) {
                Some(i) => w.terms.insert(i + 1, text),
                None => {
                    given.clear();
                    problems.push(problem(
                        "tolerance_withheld",
                        &w.id,
                        format!(
                            "{text} is not printed on {}: its label states no value for it to follow.",
                            super::wording_of(w)
                        ),
                        vec![w.id.clone()],
                    ));
                }
            }
        }
        from_spec.push(given);
    }

    // What the spec says of something the plan does not state.
    for (id, entry) in &spec.dimensions {
        if entry.tolerance.is_some()
            && let Some(o) = omitted.iter().find(|o| o.id == *id)
        {
            // Brought back when the spec left it out; else the tolerance goes.
            let fix = match o.state {
                State::OmittedByUser => Op::remove(format!("dimensions/{}/omit", key(id))),
                _ => Op::remove(path(id)),
            };
            problems.push(Problem {
                fix: vec![fix],
                ..problem(
                    "tolerance_not_shown",
                    id,
                    format!(
                        "The tolerance of {} is not printed: the plan leaves the dimension out ({}). Remove the tolerance or bring the dimension back.",
                        if o.label.is_empty() {
                            crate::wording::dimension_id(analysis, id)
                        } else {
                            o.label.clone()
                        },
                        crate::wording::left_out(&o.state)
                    ),
                    vec![id.clone()],
                )
            });
        }
    }
    for (id, code) in &fits {
        if !stated.contains(id) {
            problems.push(Problem {
                fix: vec![Op::remove(format!("features/{}/fit", key(id)))],
                ..problem(
                    "tolerance_not_shown",
                    id,
                    format!(
                        "The fit {code} of {} is not printed: no dimension in the plan states its size.",
                        crate::wording::feature_id(analysis, id)
                    ),
                    vec![id.to_string()],
                )
            });
        }
    }
    Ok(from_spec)
}

/// What the spec gives a kept item's size, as [`apply`] decides it: its `tolerance` or its
/// members' `fit`, and whether that is printed (a band of nothing, or a fit the members do not
/// all share, is withheld). None when the spec gives it neither.
pub(super) fn authored(spec: &Spec, w: &Want) -> Option<(&'static str, bool)> {
    if spec.dimensions.get(&w.id).is_some_and(|e| e.fit.is_some()) {
        return Some(("fit", true));
    }
    if crate::dims::is_envelope_extent(&w.id)
        && spec
            .dimensions
            .get(&w.id)
            .is_none_or(|e| e.tolerance.is_none())
        && let Some(t) = &spec.envelope_tolerance
    {
        return Some(("tolerance", tolerance(t).is_ok_and(|(_, nothing)| !nothing)));
    }
    if let Some(t) = spec
        .dimensions
        .get(&w.id)
        .and_then(|e| e.tolerance.as_deref())
    {
        return Some(("tolerance", tolerance(t).is_ok_and(|(_, nothing)| !nothing)));
    }
    let lead = parameter(&w.id);
    if !FITTED.iter().any(|(_, p)| *p == lead) {
        return None;
    }
    let mut members: Vec<&str> = Vec::new();
    for m in &w.measurements {
        if m.parameter == lead && !members.contains(&m.feature.as_str()) {
            members.push(&m.feature);
        }
    }
    let fits: Vec<Option<&str>> = members
        .iter()
        .map(|m| spec.features.get(*m).and_then(|e| e.fit.as_deref()))
        .collect();
    if fits.iter().all(Option::is_none) {
        return None;
    }
    Some(("fit", fits.iter().all(|f| *f == fits[0])))
}

/// Puts *text* after the term of *w*'s label that states its value, as a spec's tolerance is put
/// ([`apply`]); false when the label states no value for it to follow.
pub(super) fn insert(w: &mut super::Want, text: &str) -> bool {
    match value_term(&w.terms) {
        Some(i) => {
            w.terms.insert(i + 1, text.to_string());
            true
        }
        None => false,
    }
}

/// Puts a limit pair in place of the term of *w*'s label that states its value, keeping that
/// term's symbol (`⌀35` → `⌀34.8 - ⌀35.1`), as draftwright states an imported limit pair that is
/// not symmetric (`bore_callout_value`); false when the label states no value.
pub(super) fn limits(w: &mut super::Want, lower: f64, upper: f64) -> bool {
    let Some(i) = value_term(&w.terms) else {
        return false;
    };
    let prefix: String = w.terms[i]
        .chars()
        .take_while(|c| !c.is_ascii_digit())
        .collect();
    w.terms.splice(
        i..=i,
        [
            format!("{prefix}{lower}"),
            "-".into(),
            format!("{prefix}{upper}"),
        ],
    );
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use haecceity::presentation::tolerance::deviation;

    #[test]
    fn feature_fits_qualify_only_their_size_and_measured_member() {
        let a = crate::lint::paired_ramp_steps::tests::part(
            "boss",
            serde_json::json!({"diameter":8.,"height":3.}),
        );
        let mut spec: Spec = serde_json::from_value(serde_json::json!({
            "sheet":{"title":{"tolerance":"±0.1"}},
            "features":{"f:boss:1":{"fit":"h6"}},
            "dimensions":{"d:authored:copy":{"tolerance":"±0.1"}}
        }))
        .unwrap();
        let mut original = Want {
            id: "d:f:boss:1/boss_height.length".into(),
            views: vec!["front"],
            value: 3.,
            terms: vec!["3".into()],
            measurements: vec![super::super::Measurement {
                feature: "f:boss:1".into(),
                parameter: "boss_height.length".into(),
                value: 3.,
            }],
            basis: String::new(),
            shape: super::super::Shape::Callout {
                arcs: vec![],
                sites: vec![],
            },
            turning: None,
        };
        let mut copy = original.clone();
        copy.id = "d:authored:copy".into();
        // The boss's diameter fit does not change its height's general band.
        assert!(same_requirement(
            &a,
            &spec,
            &copy,
            &original,
            &BTreeSet::from(["f:boss:1".into()])
        ));
        original.id = "d:f:boss:1/boss.diameter".into();
        original.measurements[0].parameter = "boss.diameter".into();
        original.value = 8.;
        original.terms = vec!["⌀8".into()];
        original.measurements[0].value = 8.;
        copy.value = 8.;
        copy.terms = vec!["⌀8".into()];
        copy.measurements[0].parameter = "diameter".into();
        copy.measurements[0].value = 8.;
        // The diameter itself must not acquire an explicit band alongside its fit.
        assert!(!same_requirement(
            &a,
            &spec,
            &copy,
            &original,
            &BTreeSet::from(["f:boss:1".into()])
        ));
        original.measurements.push(super::super::Measurement {
            feature: "f:boss:2".into(),
            parameter: "boss.diameter".into(),
            value: 8.,
        });
        // A shared label's other member must not lend its fit to this member.
        assert!(same_requirement(
            &a,
            &spec,
            &copy,
            &original,
            &BTreeSet::from(["f:boss:2".into()])
        ));
        spec.dimensions
            .get_mut("d:authored:copy")
            .unwrap()
            .tolerance = None;
        assert!(same_requirement(
            &a,
            &spec,
            &copy,
            &original,
            &BTreeSet::from(["f:boss:2".into()])
        ));
    }

    #[test]
    fn tolerances_read_as_draftwright_prints_them() {
        let read = |t: &str| tolerance(t).map(|r| r.0);
        assert_eq!(read("±0.05").unwrap(), "±0.05");
        assert_eq!(read("±1").unwrap(), "±1.0");
        assert_eq!(read("±0.02").unwrap(), "±0.02");
        assert_eq!(read("+0.1 -0.02").unwrap(), "+0.1 -0.02");
        assert_eq!(read("+0.1 -0").unwrap(), "+0.1 0");
        assert_eq!(read("+0 -0.1").unwrap(), "0 -0.1");
        assert!(tolerance("±0").unwrap().1 && tolerance("+0.0 -0").unwrap().1);
        assert!(!tolerance("+0.1 -0").unwrap().1);
        assert_eq!(read("+-0.05").unwrap_err(), "\"+-0.05\" reads as \"±0.05\"");
        assert_eq!(
            read("+0.1/-0.02").unwrap_err(),
            "\"+0.1/-0.02\" reads as \"+0.1 -0.02\""
        );
        for bad in [
            "0.05",
            "±-0.05",
            "±.05",
            "-0.02 +0.1",
            "±0.05mm",
            "±1e-2",
            "",
        ] {
            assert!(read(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn imported_bands_print_as_haecceity_states_them() {
        // One line, upper/lower (ISO 129-1:2004 §6.2), as haecceity v0.2.7 writes them.
        assert_eq!(band(0.2, -0.2), "±0.2");
        assert_eq!(band(0.09, 0.0), "+0.09/0");
        assert_eq!(band(0.0, -0.2), "0/-0.2");
        assert_eq!(band(0.0762, -0.0254), "+0.0762/-0.0254");
        assert_eq!(band(1.0, -1.0), "±1.0");
        assert_eq!(band(-0.014, -0.039), "-0.014/-0.039");
        assert_eq!(band(0.039, 0.014), "+0.039/+0.014");
    }

    #[test]
    fn fit_classes_have_iso_286_limits() {
        // ISO 286-2: ⌀10 H7 is +0.015/0, ⌀10 G7 +0.020/+0.005, ⌀30 F8 +0.053/+0.020; a
        // band's upper bound belongs to it (⌀3 H11 is +0.060/0).
        let at = |c, d| deviations(c, d).map(|(l, u)| (deviation(u), deviation(l)));
        assert_eq!(at("H7", 10.0), Some(("+0.015".into(), "0".into())));
        assert_eq!(at("G7", 10.0), Some(("+0.020".into(), "+0.005".into())));
        assert_eq!(at("F8", 30.0), Some(("+0.053".into(), "+0.020".into())));
        assert_eq!(at("H11", 3.0), Some(("+0.060".into(), "0".into())));
        assert_eq!(at("H7", 250.5), None);
        // Shafts (`fits.fit_deviation("g6", 20) == (-0.020, -0.007)`), js symmetric, k8 from 0.
        assert_eq!(at("g6", 20.0), Some(("-0.007".into(), "-0.020".into())));
        assert_eq!(at("js6", 20.0), Some(("+0.0065".into(), "-0.0065".into())));
        assert_eq!(at("k8", 20.0), Some(("+0.033".into(), "0".into())));
        assert_eq!(at("H4", 10.0), None);
    }

    #[test]
    fn valid_fit_classes_print_independently_of_the_deviation_table() {
        assert_eq!(fit("H7"), Ok(("H", 7)));
        assert_eq!(fit("js6"), Ok(("js", 6)));
        for (code, letters, grade) in [
            ("H4", "H", 4),
            ("H12", "H", 12),
            ("K7", "K", 7),
            ("N7", "N", 7),
            ("x6", "x", 6),
        ] {
            assert_eq!(fit(code), Ok((letters, grade)));
            assert_eq!(deviations(code, 20.0), None);
            let mut problems = Vec::new();
            let mut spec = Spec::default();
            assert_eq!(fit_text(code, 20.0, "d:size", &spec, &mut problems), code);
            assert!(problems.is_empty());
            spec.sheet.fit_deviations = Some(true);
            assert_eq!(fit_text(code, 20.0, "d:size", &spec, &mut problems), code);
            assert!(
                problems
                    .iter()
                    .any(|p| p.code == "fit_deviations_unavailable")
            );
        }
        for bad in ["7", "H", "H 7", "Hh7", "I7", "H19"] {
            assert!(fit(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn what_the_spec_gives_a_size_includes_a_bosss_fit() {
        let want = |id: &str, parameter: &str, members: &[&str]| Want {
            id: id.into(),
            views: vec!["front"],
            value: 20.0,
            terms: vec!["ø20".into()],
            measurements: members
                .iter()
                .map(|f| super::super::Measurement {
                    feature: f.to_string(),
                    parameter: parameter.into(),
                    value: 20.0,
                })
                .collect(),
            basis: String::new(),
            shape: super::super::Shape::Callout {
                arcs: Vec::new(),
                sites: Vec::new(),
            },
            turning: None,
        };
        let mut spec = Spec::default();
        for f in ["f:boss:1", "f:hole:1"] {
            spec.features.insert(
                f.into(),
                serde_json::from_value(serde_json::json!({"fit": "H7"})).unwrap(),
            );
        }
        let boss = want("d:f:boss:1/boss.diameter", "boss.diameter", &["f:boss:1"]);
        assert_eq!(authored(&spec, &boss), Some(("fit", true)));
        let mixed = want(
            "d:f:hole:1/bore.diameter",
            "bore.diameter",
            &["f:hole:1", "f:hole:2"],
        );
        assert_eq!(authored(&spec, &mixed), Some(("fit", false)));
        let step = want(
            "d:f:turned_step:1/step.diameter",
            "step.diameter",
            &["f:boss:1"],
        );
        assert_eq!(authored(&spec, &step), None);
        spec.dimensions.insert(
            step.id.clone(),
            serde_json::from_value(serde_json::json!({"tolerance": "±0"})).unwrap(),
        );
        assert_eq!(authored(&spec, &step), Some(("tolerance", false)));
    }

    #[test]
    fn thread_designations_are_positive_and_a_tapped_hole_is_not_called_external() {
        let (_, analysis) = crate::model::analyse(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts/plate.step"),
        )
        .unwrap();
        let id = "d:f:hole:f6/bore.diameter";
        let want = |terms: &[&str]| Want {
            id: id.into(),
            views: vec!["plan"],
            value: 6.0,
            terms: terms.iter().map(|t| t.to_string()).collect(),
            measurements: vec![super::super::Measurement {
                feature: "f:hole:f6".into(),
                parameter: "bore.diameter".into(),
                value: 6.0,
            }],
            basis: String::new(),
            shape: super::super::Shape::Callout {
                arcs: Vec::new(),
                sites: Vec::new(),
            },
            turning: None,
        };
        let spec: Spec = serde_json::from_value(serde_json::json!({
            "dimensions": {id: {"tolerance": "±0.1"}}
        }))
        .unwrap();
        // No drill diameter: the designation carries a thread class, not an ISO 286 bore band.
        let tapped = want(&["2×", "M6x1-6H", "THRU"]);
        assert!(thread_designated(&tapped));
        let mut kept = vec![(tapped, true, Some("plan"), Vec::new())];
        let mut problems = Vec::new();
        apply(&analysis, &spec, &mut kept, &[], &mut problems).unwrap();
        assert_eq!(kept[0].0.terms, ["2×", "M6x1-6H", "THRU"]);
        let withheld = problems
            .iter()
            .find(|p| p.code == "tolerance_withheld")
            .unwrap();
        assert!(withheld.message.contains("thread designation"));
        assert!(
            !withheld.message.contains("external"),
            "{}",
            withheld.message
        );
        // A plain numeric nominal is not a thread, with or without a diameter glyph.
        // When a drill size precedes a thread, the band belongs to the drill size.
        for terms in [
            &["6", "THRU"][..],
            &["5", "THRU", "M6x1-6H"][..],
            &["⌀5", "THRU", "M6x1-6H"][..],
        ] {
            let plain = want(terms);
            assert!(!thread_designated(&plain));
            let mut kept = vec![(plain, true, Some("plan"), Vec::new())];
            let mut problems = Vec::new();
            apply(&analysis, &spec, &mut kept, &[], &mut problems).unwrap();
            assert_eq!(kept[0].0.terms[1], "±0.1");
            assert!(
                !problems.iter().any(|p| p.code == "tolerance_withheld"),
                "{problems:?}"
            );
        }
        assert!(thread_designated(&want(&[
            "M10 x 1.5-6g RH, FULL AVAILABLE LENGTH"
        ])));
    }

    #[test]
    fn a_band_follows_the_value_not_the_count() {
        let terms = |t: &[&str]| t.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(value_term(&terms(&["4×", "⌀10", "THRU", "(2×2)"])), Some(1));
        assert_eq!(value_term(&terms(&["3× 72.4"])), Some(0));
        assert_eq!(value_term(&terms(&["HEX", "100", "A/F"])), Some(1));
        assert_eq!(value_term(&terms(&["28.8"])), Some(0));
    }
}
