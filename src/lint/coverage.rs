//! The coverage backstop (Python's `lint_feature_coverage` and `lint_location_coverage` in
//! `linting/coverage.py`): coarse checks that every recognised cylindrical diameter is called
//! out, and that every hole is centre-marked and located, kept beside the semantic ledgers.
//!
//! - **Diameters** ([`feature_coverage`], LINT-code-feature_not_dimensioned): the inventory
//!   ([`inventory`], quiddity's `feature_diameters`) is every hole's bore, counterbore and spotface
//!   diameter and every boss's, as distinct values. A diameter is covered when an annotation
//!   mentions one within 0.15 mm ([`not_dimensioned`]). What an annotation mentions
//!   ([`mentions`], Python's `_annotation_diameter_sources`): a hole callout (a mark claiming a
//!   hole's bore, counterbore, spotface or countersink diameter) mentions the diameters it prints
//!   for the hole, not its bolt circle's, and is not read as free text; any other mark mentions
//!   each number after a `ø` or `⌀` in its text, so a radius (`R5 TYP`) covers nothing. A
//!   misprinted callout (`⌀16` for a ø6 hole) leaves the diameter uncovered, as in draftwright,
//!   besides hole coverage's `hole_requirement_unverifiable`. Python skips the title block; the
//!   port's marks are the drawing's annotations, which never include it. Python excludes the
//!   diameters its per-view callout cap dropped; the port drops no callout, so nothing is
//!   excluded.
//! - **Counts** ([`count_mismatch`], LINT-code-feature_count_mismatch): each bore, counterbore and
//!   spotface is one occurrence of its diameter, and the hole callouts' quantities (`n×`, else
//!   one) must account for them; several callouts of one owner (the same claimed holes) count
//!   once, at their largest. A diameter some free text mentions is exempt. Draftwright's drawing
//!   lint turns this off (`check_hole_counts=False`: counts are hole coverage's `grouping.count`),
//!   and so does the port: [`crate::lint::check`] does not run it.
//! - **Locations** ([`located`], LINT-code-feature_not_located): every hole that no recognised
//!   pattern holds (a pattern is located by its pitch or bolt circle) and that is more than
//!   1.0 mm from the part's centre across its axis must be located along each such axis
//!   ([`location`]): by structured evidence (here, an annotation claiming the hole's
//!   `location.length.<axis>`), or by a dimension's witness point within 0.6 mm of the hole's
//!   projected centre on that axis. The drawing does not record its dimensions' witness points
//!   nor the views' projections, so the port reads the structured evidence only.
//! - **Centre marks** ([`centre_marks`], LINT-code-feature_no_centermark): every hole, pattern
//!   members included, has a centre mark within 0.6 mm of its centre projected into the view that
//!   looks along it (the front or rear for a hole along y). The sheet's drawn centre marks and its
//!   views' frames supply the marks and projections ([`drawn_centre_marks`]), and
//!   [`crate::lint::check`] runs it on every sheet.
//!
//! Python reports one location finding per code for the sheet, with the number of holes; the
//! port does too, with Python's message, naming the holes as its subjects (and, for a location,
//! each axis not located as a requirement). The drawing-wide collector makes missing-feature
//! coverage informational on a part of several solids. Double-D bores are located and
//! centre-marked as holes are (Python's `profiled_bores`, [`sites`]). General authored
//! diameter-note authority is not ported. A boss whose defining face and circle belong to a
//! uniquely proven blend is excluded from the diameter inventory ([`super::bosses::boss_owner`]);
//! its height remains independently required.

use std::collections::{BTreeMap, BTreeSet};

use super::holes::groups;
use super::{LintIssue, Mark, Requirement, Severity, profiled_bores};
use crate::bridge::holes::{self, Hole};
use crate::model::Analysis;

/// How far a mentioned diameter may be from the part's (Python's `tol`).
pub const DIAMETER_TOL: f64 = 0.15;
/// How far a centre mark or a witness may be from a hole's projected centre, page mm.
pub const PAGE_TOL: f64 = 0.6;
/// How far from the part's centre a hole must be, across its axis, to need locating.
pub const AXIS_TOL: f64 = 1.0;

const AXES: [&str; 3] = ["x", "y", "z"];

/// A nominal as live labels print it: shortest within 1e-6, up to three places.
pub fn fmt(v: f64) -> String {
    let v = (v * 1e6).round() / 1e6;
    let places = (0..3)
        .find(|&places| {
            let factor = 10_f64.powi(places as i32);
            ((v * factor).round() / factor - v).abs() <= 1e-6 + 1e-12
        })
        .unwrap_or(3);
    let text = format!("{v:.places$}");
    if places == 3
        && text
            .parse::<f64>()
            .is_ok_and(|n| (n - v).abs() > 1e-6 + 1e-12)
    {
        return if text.parse::<f64>() == Ok(0.) {
            "0.000".into()
        } else {
            text
        };
    }
    let text = if places == 0 {
        text.as_str()
    } else {
        text.trim_end_matches('0').trim_end_matches('.')
    };
    if text == "-0" {
        "0".into()
    } else {
        text.into()
    }
}

/// The diameters a text prints: each number after `ø`, `Ø` or `⌀` (Python's `_DIAM_RE`).
pub fn text_diameters(text: &str) -> Vec<f64> {
    let chars: Vec<char> = text.chars().collect();
    let digits = |mut i: usize| {
        while i < chars.len() && chars[i].is_ascii_digit() {
            i += 1;
        }
        i
    };
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if !matches!(chars[i], 'ø' | 'Ø' | '⌀') {
            i += 1;
            continue;
        }
        let mut j = i + 1;
        while j < chars.len() && chars[j].is_whitespace() {
            j += 1;
        }
        let mut end = digits(j);
        if end == j {
            i += 1;
            continue;
        }
        if end + 1 < chars.len() && chars[end] == '.' && chars[end + 1].is_ascii_digit() {
            end = digits(end + 1);
        }
        out.extend(chars[j..end].iter().collect::<String>().parse::<f64>().ok());
        i = end;
    }
    out
}

/// The quantity a callout's text leads with (`4× ⌀6`), else one (Python's `covers_count`).
pub fn quantity(text: &str) -> usize {
    let t = text.trim_start();
    let n: String = t.chars().take_while(char::is_ascii_digit).collect();
    match t[n.len()..].trim_start().starts_with('×') {
        true => n.parse().unwrap_or(1).max(1),
        false => 1,
    }
}

/// The diameters one annotation mentions: structured (a hole callout's), or read from its text.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Mentions {
    pub structured: Vec<f64>,
    pub text: Vec<f64>,
}

/// The hole diameters a callout states, by parameter.
const HOLE_DIAMETERS: [&str; 4] = [
    "bore.diameter",
    "counterbore.diameter",
    "spotface.diameter",
    "countersink.diameter",
];

/// Hole *h*'s value of *parameter*, one of [`HOLE_DIAMETERS`].
fn hole_diameter(h: &Hole, parameter: &str) -> Option<f64> {
    match parameter {
        "bore.diameter" => Some(h.diameter),
        "counterbore.diameter" => h.counterbore.as_ref().map(|r| r.diameter),
        "spotface.diameter" => h.spotface.as_ref().map(|r| r.diameter),
        "countersink.diameter" => h.countersink.as_ref().map(|c| c.diameter),
        _ => None,
    }
}

/// Hole *h*'s bore, counterbore, outer-step and spotface diameters, each with its parameter.
fn sized_diameters(h: &Hole) -> Vec<(String, f64)> {
    let mut out: Vec<(String, f64)> = HOLE_DIAMETERS[..3]
        .iter()
        .filter_map(|p| Some((p.to_string(), hole_diameter(h, p)?)))
        .collect();
    out.extend(
        h.outer_steps
            .iter()
            .enumerate()
            .map(|(i, r)| (format!("outer_steps.{i}.diameter"), r.diameter)),
    );
    out
}

/// The Analysis's readable holes by ID.
fn holes_by_id(analysis: &Analysis) -> BTreeMap<&str, Hole<'_>> {
    holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| Some((f.id.as_str(), h.ok()?)))
        .collect()
}

/// What *mark* mentions. A mark claiming a hole's diameter is a hole callout: it mentions the
/// diameters its text prints before any bolt circle clause (`EQ SP ON ø60 BC`), as draftwright's
/// callout records the bore, recess and countersink diameters it draws (`covers_diameters`), and
/// is not read as free text. Any other mark mentions every diameter its text prints.
pub fn mentions(holes: &BTreeMap<&str, Hole>, mark: &Mark) -> Mentions {
    let callout = mark.claims.iter().any(|c| {
        HOLE_DIAMETERS.contains(&c.parameter.as_str()) && holes.contains_key(c.feature.as_str())
    });
    if !callout {
        return Mentions {
            structured: Vec::new(),
            text: text_diameters(&mark.text),
        };
    }
    let own = [" EQ SP", " ON ", " BC"]
        .iter()
        .filter_map(|clause| mark.text.find(clause))
        .min()
        .map_or(mark.text.as_str(), |end| &mark.text[..end]);
    Mentions {
        structured: text_diameters(own),
        text: Vec::new(),
    }
}

/// The part's distinct recognised diameters, ascending, each with the requirements that have it:
/// every readable hole's bore, counterbore, outer steps and spotface, and every boss's diameter.
pub fn inventory(analysis: &Analysis) -> Vec<(f64, Vec<Requirement>)> {
    let mut found: Vec<(f64, Requirement)> = Vec::new();
    for (id, h) in holes_by_id(analysis) {
        for (parameter, d) in sized_diameters(&h) {
            found.push((
                d,
                Requirement {
                    feature: id.into(),
                    parameter,
                },
            ));
        }
    }
    for f in analysis
        .features
        .iter()
        .filter(|f| f.kind == "boss" && super::bosses::boss_owner(analysis, f).is_none())
    {
        if let Some(d) = f.parameters.get("diameter").and_then(|v| v.as_f64()) {
            found.push((
                d,
                Requirement {
                    feature: f.id.clone(),
                    parameter: "boss.diameter".into(),
                },
            ));
        }
    }
    // quiddity-rust#123 suppresses a complete body's full-length OD boss. Its
    // diameter remains an independent coverage requirement, carried by the OD dimension.
    for f in analysis.features.iter().filter(|f| f.kind == "turned_body") {
        if let Some(d) = f.parameters.get("od").and_then(|v| v.as_f64()) {
            found.push((
                d,
                Requirement {
                    feature: f.id.clone(),
                    parameter: "turned_body.diameter".into(),
                },
            ));
        }
    }
    found.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
    let mut out: Vec<(f64, Vec<Requirement>)> = Vec::new();
    for (d, r) in found {
        match out.last_mut() {
            Some((v, rs)) if *v == d => rs.push(r),
            _ => out.push((d, vec![r])),
        }
    }
    out
}

/// One `feature_not_dimensioned` finding for each inventory diameter that no mentioned diameter
/// and no excluded one is within [`DIAMETER_TOL`] of.
pub fn not_dimensioned(
    inventory: &[(f64, Vec<Requirement>)],
    mentioned: &[f64],
    exclude: &[f64],
    severity: Severity,
) -> Vec<LintIssue> {
    let near = |d: f64, vs: &[f64]| vs.iter().any(|v| (d - v).abs() <= DIAMETER_TOL);
    inventory
        .iter()
        .filter(|(d, _)| !near(*d, mentioned) && !near(*d, exclude))
        .map(|(d, requirements)| {
            let mut subjects: Vec<String> =
                requirements.iter().map(|r| r.feature.clone()).collect();
            subjects.dedup();
            LintIssue {
                sheet: None,
                id: format!("l:feature_not_dimensioned:ø{}", fmt(*d)),
                code: "feature_not_dimensioned".into(),
                severity,
                message: format!(
                    "cylindrical feature ø{} has no diameter callout on the sheet",
                    fmt(*d)
                ),
                at: None,
                view: None,
                subjects,
                requirements: requirements.clone(),
                measurements: Vec::new(),
                sources: Vec::new(),
                stage: None,
                annotation: None,
                related: Vec::new(),
                evidence: None,
                fix: Some(format!(
                    "Call out ø{} with a diameter callout, in a view that sees the feature's circle.",
                    fmt(*d)
                )),
            }
        })
        .collect()
}

/// The diameter findings on a drawing whose annotations are *marks*. A mark claiming a boss's
/// diameter mentions it, as a hole callout's claims do, whether or not its text prints `ø`: an
/// external thread's designation stands in place of the diameter it is cut on (`M90 x 2`).
pub fn feature_coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let holes = holes_by_id(analysis);
    let boss = |id: &str| {
        analysis
            .features
            .iter()
            .find(|f| f.id == id && f.kind == "boss")
            .and_then(|f| f.parameters.get("diameter").and_then(|v| v.as_f64()))
    };
    let mentioned: Vec<f64> = marks
        .iter()
        .flat_map(|m| {
            let claimed = m
                .claims
                .iter()
                .filter(|c| c.parameter == "boss.diameter")
                .filter_map(|c| boss(&c.feature));
            let m = mentions(&holes, m);
            m.structured.into_iter().chain(m.text).chain(claimed)
        })
        .collect();
    not_dimensioned(&inventory(analysis), &mentioned, &[], Severity::Warning)
}

/// A quantity a hole callout states for one diameter, and whose it is: callouts of one owner
/// count once, at their largest; those of none add up.
#[derive(Clone, Debug, PartialEq)]
pub struct Provided {
    pub value: f64,
    pub count: usize,
    pub owner: Option<String>,
}

/// The `feature_count_mismatch` findings: *occurrences* lists each bore, counterbore and spotface
/// diameter on the part once per hole; a diameter within [`DIAMETER_TOL`] of a text mention is
/// exempt; otherwise one is reported when the provided quantities within tolerance add to more
/// than none but fewer than its occurrences, largest diameter first.
pub fn count_mismatch(
    occurrences: &[f64],
    text: &[f64],
    provided: &[Provided],
    severity: Severity,
) -> Vec<LintIssue> {
    let mut required: Vec<(f64, usize)> = Vec::new();
    for &d in occurrences {
        match required
            .iter_mut()
            .find(|(k, _)| (k - d).abs() <= DIAMETER_TOL)
        {
            Some((_, n)) => *n += 1,
            None => required.push((d, 1)),
        }
    }
    required.sort_by(|a, b| b.0.total_cmp(&a.0));
    // Python sums by value, uniting one owner's statements of a value first.
    let mut owned: BTreeMap<(String, u64), usize> = BTreeMap::new();
    let mut by_value: Vec<(f64, usize)> = Vec::new();
    let mut add = |value: f64, count: usize| match by_value.iter_mut().find(|(v, _)| *v == value) {
        Some((_, c)) => *c += count,
        None => by_value.push((value, count)),
    };
    for p in provided {
        match &p.owner {
            None => add(p.value, p.count),
            Some(o) => {
                let c = owned.entry((o.clone(), p.value.to_bits())).or_default();
                *c = (*c).max(p.count);
            }
        }
    }
    for ((_, bits), c) in owned {
        add(f64::from_bits(bits), c);
    }
    let mut issues = Vec::new();
    for (d, need) in required {
        if text.iter().any(|v| (d - v).abs() <= DIAMETER_TOL) {
            continue;
        }
        let have: usize = by_value
            .iter()
            .filter(|(v, _)| (d - v).abs() <= DIAMETER_TOL)
            .map(|(_, c)| c)
            .sum();
        if have > 0 && have < need {
            issues.push(LintIssue {
                sheet: None,
                id: format!("l:feature_count_mismatch:ø{}", fmt(d)),
                code: "feature_count_mismatch".into(),
                severity,
                message: format!(
                    "{need} ø{} features on the part but callouts account for {have}",
                    fmt(d)
                ),
                at: None,
                view: None,
                subjects: Vec::new(),
                requirements: Vec::new(),
                measurements: Vec::new(),
                sources: Vec::new(),
                stage: None,
                annotation: None,
                related: Vec::new(),
                evidence: None,
                fix: Some(format!(
                    "State the quantity of the ø{} holes (n×) on their callouts.",
                    fmt(d)
                )),
            });
        }
    }
    issues
}

/// The count findings on a drawing whose annotations are *marks*: each readable hole's bore,
/// counterbore, outer steps and spotface is an occurrence (a split through hole's segments once); a hole callout provides its stated diameters at its
/// quantity, its owner the holes it claims.
pub fn counts(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let holes = holes_by_id(analysis);
    // The coaxial segments of one through hole split by a slit are one occurrence.
    let continuations = holes::continuations(holes.values());
    let occurrences: Vec<f64> = holes
        .values()
        .filter(|h| !continuations.contains(h.feature.id.as_str()))
        .flat_map(|h| sized_diameters(h).into_iter().map(|(_, d)| d))
        .collect();
    let (mut text, mut provided) = (Vec::new(), Vec::new());
    for m in marks {
        let found = mentions(&holes, m);
        text.extend(found.text);
        let owner: BTreeSet<&str> = m
            .claims
            .iter()
            .filter(|c| holes.contains_key(c.feature.as_str()))
            .map(|c| c.feature.as_str())
            .collect();
        let owner = owner.into_iter().collect::<Vec<_>>().join(",");
        for value in found.structured {
            provided.push(Provided {
                value,
                count: quantity(&m.text),
                owner: Some(owner.clone()),
            });
        }
    }
    count_mismatch(&occurrences, &text, &provided, Severity::Warning)
}

/// A hole as location coverage reads it.
#[derive(Clone, Debug, PartialEq)]
pub struct Site {
    /// Its Analysis ID (or any name for it).
    pub subject: String,
    /// The axis it is drilled along (0, 1, 2: its direction's largest component).
    pub axis: usize,
    /// Its centre, in the frame of the part's centre.
    pub at: [f64; 3],
    /// Held by a recognised pattern, so located by its pitch or bolt circle.
    pub patterned: bool,
    /// Its centre projected into each view it may be centre-marked in (`front`, `plan`, `side`,
    /// `rear`), page mm.
    pub projections: Vec<(String, [f64; 2])>,
    /// The axes structured evidence locates it along.
    pub claimed: [bool; 3],
}

/// What the sheet shows, by view: its centre marks' centres and its dimensions' witness points
/// (those of a dimension carrying no structured location), page mm.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Page {
    pub marks: BTreeMap<String, Vec<[f64; 2]>>,
    pub witnesses: BTreeMap<String, Vec<[f64; 2]>>,
}

/// The world axes a principal view shows across and up the page (draftwright's `VIEW_AXES`).
fn page_axes(view: &str) -> Option<[usize; 2]> {
    match view {
        "front" | "rear" => Some([0, 2]),
        "plan" => Some([0, 1]),
        "side" => Some([1, 2]),
        _ => None,
    }
}

/// The holes without a centre mark, and those not located along every axis that needs it, with
/// those axes: [`centre_marks`]'s and [`located`]'s judgements.
pub fn judge<'a>(
    sites: &'a [Site],
    centre: [f64; 3],
    page: &Page,
) -> (Vec<&'a Site>, Vec<(&'a Site, Vec<usize>)>) {
    let (mut unmarked, mut unlocated) = (Vec::new(), Vec::new());
    for s in sites {
        let near = |a: [f64; 2], b: [f64; 2]| {
            (a[0] - b[0]).abs() <= PAGE_TOL && (a[1] - b[1]).abs() <= PAGE_TOL
        };
        if !s.projections.iter().any(|(view, p)| {
            page.marks
                .get(view)
                .is_some_and(|ms| ms.iter().any(|m| near(*m, *p)))
        }) {
            unmarked.push(s);
        }
        if s.patterned {
            continue;
        }
        let wanted: Vec<usize> = (0..3)
            .filter(|&k| k != s.axis && (s.at[k] - centre[k]).abs() > AXIS_TOL)
            .collect();
        let missing: Vec<usize> = wanted
            .into_iter()
            .filter(|&k| {
                let geometric = s.projections.iter().any(|(view, p)| {
                    let Some(i) = page_axes(view).and_then(|a| a.iter().position(|&j| j == k))
                    else {
                        return false;
                    };
                    page.witnesses
                        .get(view)
                        .is_some_and(|ws| ws.iter().any(|w| (w[i] - p[i]).abs() <= PAGE_TOL))
                });
                !(s.claimed[k] || geometric)
            })
            .collect();
        if !missing.is_empty() {
            unlocated.push((s, missing));
        }
    }
    (unmarked, unlocated)
}

fn sheet_issue(code: &str, message: String, subjects: Vec<String>, fix: &str) -> LintIssue {
    LintIssue {
        sheet: None,
        id: format!("l:{code}:holes"),
        code: code.into(),
        severity: Severity::Warning,
        message,
        at: None,
        view: None,
        subjects,
        requirements: Vec::new(),
        measurements: Vec::new(),
        sources: Vec::new(),
        stage: None,
        annotation: None,
        related: Vec::new(),
        evidence: None,
        fix: Some(fix.into()),
    }
}

/// Python's two findings on *sites* (`feature_no_centermark`, then `feature_not_located`), each
/// counting its holes, at *severity*.
pub fn location(
    sites: &[Site],
    centre: [f64; 3],
    page: &Page,
    severity: Severity,
) -> Vec<LintIssue> {
    let (unmarked, unlocated) = judge(sites, centre, page);
    let mut issues = Vec::new();
    if !unmarked.is_empty() {
        issues.push(sheet_issue(
            "feature_no_centermark",
            format!("{} hole(s) have no centre mark", unmarked.len()),
            unmarked.iter().map(|s| s.subject.clone()).collect(),
            "Mark each hole's centre in the view that looks along it.",
        ));
    }
    if !unlocated.is_empty() {
        let mut issue = sheet_issue(
            "feature_not_located",
            format!("{} hole(s) have no locating dimension", unlocated.len()),
            unlocated.iter().map(|(s, _)| s.subject.clone()).collect(),
            "Locate each hole from a datum along each axis named, in the view that looks along it.",
        );
        issue.requirements = unlocated
            .iter()
            .flat_map(|(s, axes)| {
                axes.iter().map(|&k| Requirement {
                    feature: s.subject.clone(),
                    parameter: format!("location.length.{}", AXES[k]),
                })
            })
            .collect();
        issues.push(issue);
    }
    for i in &mut issues {
        i.severity = severity;
    }
    issues
}

/// The Analysis's readable holes, then its readable double-D bores (Python's `profiled_bores`,
/// located and centre-marked as holes are), as sites in the part's frame: a member of an
/// established hole pattern ([`groups`]) is patterned, a double-D bore never; an annotation
/// claiming the feature's `location.length.<axis>` locates it along that axis. A double-D bore
/// is at its record's `location`, along the part's axis its record's runs nearest; one whose
/// record cannot be read is left to [`super::profiled_bores`], which reports it. The drawing
/// records no centre marks, witness points or view projections, so the sites have no
/// projections.
pub fn sites(analysis: &Analysis, marks: &[Mark]) -> Vec<Site> {
    let patterned: BTreeSet<String> = groups(analysis)
        .into_iter()
        .filter(|g| g.pattern)
        .flat_map(|g| g.members)
        .collect();
    let claimed = |id: &str, at: [f64; 3]| {
        [0, 1, 2].map(|k| {
            let parameter = format!("location.length.{}", AXES[k]);
            marks.iter().any(|m| {
                // Reference ordinates are information only. The controlling
                // chain graph already excludes them; the backstop must agree.
                !m.text.trim_start().starts_with('(')
                    && m.claims
                        .iter()
                        .any(|c| c.feature == id && c.parameter == parameter)
                    && (!m.name.starts_with("hole_schedule_")
                        || super::holes::states(&m.text, at[k] - analysis.envelope[0][k]))
            })
        })
    };
    let frame = holes::frame(analysis);
    let bores = analysis
        .features
        .iter()
        .filter(|f| f.kind == "double_d_bore")
        .filter_map(|f| {
            let (key, ..) = profiled_bores::key(analysis, f).ok()?;
            let at = holes::to_part(&frame, holes::point(f.parameters.get("location"))?);
            Some(Site {
                subject: f.id.clone(),
                axis: key.axis,
                at,
                patterned: false,
                projections: Vec::new(),
                claimed: claimed(&f.id, at),
            })
        });
    holes::read(analysis)
        .into_iter()
        .filter_map(|(f, h)| Some((f, h.ok()?)))
        .map(|(f, h)| {
            let d = h.direction;
            let axis = (0..3)
                .max_by(|&a, &b| d[a].abs().total_cmp(&d[b].abs()).then(b.cmp(&a)))
                .unwrap_or(2);
            Site {
                subject: f.id.clone(),
                axis,
                at: h.at,
                patterned: patterned.contains(&f.id),
                projections: Vec::new(),
                claimed: claimed(&f.id, h.at),
            }
        })
        .chain(bores)
        .collect()
}

/// The part's centre: its box's.
fn centre(analysis: &Analysis) -> [f64; 3] {
    let [lo, hi] = analysis.envelope;
    [0, 1, 2].map(|k| 0.5 * (lo[k] + hi[k]))
}

/// The `feature_not_located` finding on a drawing whose annotations are *marks*.
pub fn located(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let edges = super::location::drawn_edges(analysis, marks);
    let roots = super::location::drawing_roots(analysis, marks);
    let mut sites = sites(analysis, marks);
    for site in &mut sites {
        for (k, root) in roots.iter().enumerate() {
            // Unsupported relations retain main's verdict. Only a positive printed
            // chain may change it; absence of a graph path is not a new finding.
            if let Some(node) = super::location::node_feature(analysis, &site.subject, k) {
                let proved = super::location::drawn_proof(
                    analysis,
                    &edges,
                    root,
                    k,
                    site.at[k],
                    Some(&node),
                )
                .is_some();
                site.claimed[k] = if super::location::fully_modelled(analysis, marks, &site.subject)
                {
                    proved
                } else {
                    site.claimed[k] || proved
                };
            }
        }
    }
    location(
        &sites,
        centre(analysis),
        &Page::default(),
        Severity::Warning,
    )
    .into_iter()
    .filter(|i| i.code == "feature_not_located")
    .collect()
}

/// The `feature_no_centermark` finding on a drawing whose annotations are *marks*: every hole,
/// without geometric drawing evidence.
pub fn centre_marks(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    location(
        &sites(analysis, marks),
        centre(analysis),
        &Page::default(),
        Severity::Warning,
    )
    .into_iter()
    .filter(|i| i.code == "feature_no_centermark")
    .collect()
}

/// A scheduled opening must identify its axial entry plane and drilling direction.
pub fn schedule_entries(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    for size in marks
        .iter()
        .filter(|m| m.name.starts_with("hole_schedule_") && m.name.ends_with("/size"))
    {
        for claim in size
            .claims
            .iter()
            .filter(|c| c.parameter == "bore.diameter")
        {
            let Some(h) = holes::read(analysis)
                .into_iter()
                .find_map(|(f, h)| (f.id == claim.feature).then_some(h.ok()).flatten())
            else {
                continue;
            };
            let name = size.name.trim_end_matches("/size").to_owned() + "/entry";
            let entry = marks.iter().find(|m| m.name == name);
            // The entry cell is in its view's frame; the claim carries that view's out-of-page
            // direction (`entry.view.x,y,z`).
            let correct = entry.is_some_and(|m| {
                let Some(out) = m.claims.iter().find_map(|c| {
                    let v: Vec<f64> = c
                        .parameter
                        .strip_prefix("entry.view.")?
                        .split(',')
                        .map(|x| x.parse().ok())
                        .collect::<Option<_>>()?;
                    <[f64; 3]>::try_from(v).ok()
                }) else {
                    return false;
                };
                // Recomputed from the geometry, independently of the planner: the distance
                // from the rearmost face along view Z, and the drilling sense along it.
                let dot = |p: [f64; 3]| p.iter().zip(out).map(|(a, b)| a * b).sum::<f64>();
                let [lo, hi] = analysis.envelope;
                let back = (0..8)
                    .map(|i| {
                        dot(std::array::from_fn(|k| {
                            if (i >> k) & 1 == 0 { lo[k] } else { hi[k] }
                        }))
                    })
                    .fold(f64::INFINITY, f64::min);
                let want = dot(h.at) - back;
                let want_dir = if dot(h.direction) > 0.0 { "+Z" } else { "−Z" };
                let Some((position, direction)) = m.text.split_once('/') else {
                    return false;
                };
                super::holes::states(position.trim(), want) && direction.trim() == want_dir
            });
            if !correct {
                let mut issue = sheet_issue(
                    "hole_table_entry_invalid",
                    "Hole-table entry coordinate or drilling direction does not match the opening."
                        .into(),
                    vec![claim.feature.clone()],
                    "Restore the entry coordinate and drilling direction from the hole's proven opening.",
                );
                issue.annotation = Some(name);
                issues.push(issue);
            }
        }
    }
    issues
}

/// Centre-mark coverage from the drawn centres and view transforms, never the Plan.
pub fn drawn_centre_marks(analysis: &Analysis, drawing: &crate::output::Drawing) -> Vec<LintIssue> {
    let mut sites = sites(analysis, &[]);
    for s in &mut sites {
        for (name, f) in &drawing.sheet.frames {
            let Some(view) = haecceity::hlr::View::new(f.direction, f.up) else {
                continue;
            };
            if view.toward[s.axis].abs() < 1.0 - 1e-6 {
                continue;
            }
            let c = view.map(centre(analysis));
            let p = view.map(s.at);
            let p = f.broken.as_ref().map_or(p, |b| b.map(p));
            s.projections.push((
                name.clone(),
                [0, 1].map(|k| f.at[k] + f.scale * (p[k] - c[k])),
            ));
        }
    }
    location(
        &sites,
        centre(analysis),
        &Page {
            marks: drawing.sheet.centre_marks.clone(),
            witnesses: BTreeMap::new(),
        },
        if analysis.solid_count.is_some_and(|n| n > 1) {
            Severity::Info
        } else {
            Severity::Warning
        },
    )
    .into_iter()
    .filter(|i| i.code == "feature_no_centermark")
    .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn site(at: [f64; 3], axis: usize) -> Site {
        Site {
            subject: "f:hole:1".into(),
            axis,
            at,
            patterned: false,
            projections: vec![("plan".into(), [100.0, 50.0])],
            claimed: [false; 3],
        }
    }

    fn codes(issues: &[LintIssue]) -> Vec<&str> {
        issues.iter().map(|i| i.code.as_str()).collect()
    }

    /// Only a number after `ø`, `Ø` or `⌀` is a diameter (draftwright's `_DIAM_RE`): a radius,
    /// a quantity or a depth is not, and the glyph may be spaced from its number, as a captured
    /// sheet's text runs are.
    #[test]
    fn text_mentions_only_diameters() {
        assert_eq!(text_diameters("4× ø8 THRU"), [8.0]);
        assert_eq!(text_diameters("R4 TYP"), Vec::<f64>::new());
        assert_eq!(text_diameters("4× ø 12 ↧ 20 Ø20.5 ⌀6."), [12.0, 20.5, 6.0]);
        assert_eq!(text_diameters("ø ø3"), [3.0]);
        assert_eq!(
            (quantity("4× ⌀6 THRU"), quantity("⌀6"), quantity("12 ×2")),
            (4, 1, 12)
        );
    }

    /// A hole callout (a mark claiming a hole's diameter) mentions the diameters it prints for
    /// the hole, not its bolt circle's, and is not free text; any other mark is read as text.
    #[test]
    fn a_hole_callout_mentions_its_own_diameters() {
        use crate::bridge::Feature;
        use crate::output::Claim;
        let f: Feature = serde_json::from_value(serde_json::json!({
            "id": "f:hole:1", "kind": "hole", "faces": [0],
            "parameters": { "axis": [0.0, 0.0, 1.0], "location": [0.0, 0.0, 0.0],
                            "diameter": 6.0, "bottom": "through" },
            "fingerprint": { "version": "test", "family": "holes", "subtype": "Hole",
                             "sizes": {}, "position": [0.0, 0.0, 0.0], "neighbourhood": [] },
        }))
        .unwrap();
        let identity = [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ];
        let holes: BTreeMap<&str, Hole> = [("f:hole:1", Hole::read(&f, &identity).unwrap())].into();
        let mark = |text: &str, parameter: &str| Mark {
            name: "m".into(),
            view: None,
            text: text.into(),
            claims: vec![Claim {
                feature: "f:hole:1".into(),
                parameter: parameter.into(),
            }],
        };
        let callout = mentions(&holes, &mark("6× ⌀6 THRU EQ SP ON ø60 BC", "bore.diameter"));
        assert_eq!((callout.structured, callout.text), (vec![6.0], vec![]));
        let other = mentions(&holes, &mark("ø60 BC", "bolt_circle.diameter"));
        assert_eq!((other.structured, other.text), (vec![], vec![60.0]));
    }

    /// A diameter is covered by a mention within 0.15 mm, or by an excluded diameter; each
    /// uncovered one is one finding, ascending.
    #[test]
    fn an_uncovered_diameter_is_reported_once() {
        let inv = |ds: &[f64]| ds.iter().map(|&d| (d, Vec::new())).collect::<Vec<_>>();
        let found = |mentioned: &[f64], exclude: &[f64]| {
            not_dimensioned(&inv(&[8.0, 12.99]), mentioned, exclude, Severity::Warning)
                .into_iter()
                .map(|i| i.message)
                .collect::<Vec<_>>()
        };
        assert_eq!(
            found(&[], &[]),
            [
                "cylindrical feature ø8 has no diameter callout on the sheet",
                "cylindrical feature ø12.99 has no diameter callout on the sheet"
            ]
        );
        assert_eq!(found(&[8.14, 13.0], &[]), Vec::<String>::new());
        assert_eq!(found(&[8.16], &[13.0]).len(), 1);
    }

    /// Counts: a shortfall is reported, largest diameter first, but not none at all and not a
    /// diameter free text mentions; one owner's callouts of a value count once, at their largest.
    #[test]
    fn a_count_shortfall_is_reported() {
        let p = |value: f64, count: usize, owner: Option<&str>| Provided {
            value,
            count,
            owner: owner.map(Into::into),
        };
        let occ = [6.0, 6.0, 6.0, 6.0, 10.0, 10.0, 10.05];
        let found = |text: &[f64], provided: &[Provided]| {
            count_mismatch(&occ, text, provided, Severity::Warning)
                .into_iter()
                .map(|i| i.message)
                .collect::<Vec<_>>()
        };
        assert_eq!(
            found(&[], &[p(6.0, 2, None), p(10.0, 2, None)]),
            [
                "3 ø10 features on the part but callouts account for 2",
                "4 ø6 features on the part but callouts account for 2"
            ]
        );
        assert_eq!(found(&[], &[p(6.0, 2, None), p(6.0, 2, None)]).len(), 0);
        assert_eq!(
            found(&[], &[p(6.0, 2, Some("a")), p(6.0, 2, Some("a"))]),
            ["4 ø6 features on the part but callouts account for 2"]
        );
        assert_eq!(found(&[6.0], &[p(6.0, 1, None)]), Vec::<String>::new());
    }

    /// A hole is marked by a centre mark within 0.6 mm of its projection; located along each axis
    /// across it where it is more than 1 mm off the part's centre, by a claim or a witness within
    /// 0.6 mm on that axis; a pattern member needs no location.
    /// A double-D bore is located as a hole is (Python's `profiled_bores` among the location
    /// checks' holes): along x at y 30 on a part centred at y 50, it needs locating along y (not
    /// z, where it is centred); an annotation claiming its `location.length.y` locates it.
    #[test]
    fn a_double_d_bore_is_located_as_a_hole_is() {
        use crate::output::Claim;
        let a = super::super::paired_ramp_steps::tests::part(
            "double_d_bore",
            serde_json::json!({ "axis": [1.0, 0.0, 0.0], "location": [100.0, 30.0, 5.0],
                                "depth": 100.0, "major_diameter": 10.0, "across_flats": 7.2,
                                "through": true, "flat_direction": [0.0, 1.0, 0.0] }),
        );
        let found = located(&a, &[]);
        assert_eq!(codes(&found), ["feature_not_located"]);
        assert_eq!(found[0].subjects, ["f:double_d_bore:1"]);
        assert_eq!(found[0].requirements[0].parameter, "location.length.y");
        let mark = Mark {
            name: "m".into(),
            view: Some("v:side".into()),
            text: "30".into(),
            claims: vec![Claim {
                feature: "f:double_d_bore:1".into(),
                parameter: "location.length.y".into(),
            }],
        };
        assert!(located(&a, std::slice::from_ref(&mark)).is_empty());
        let reference = Mark {
            text: " (30)".into(),
            ..mark
        };
        assert_eq!(codes(&located(&a, &[reference])), ["feature_not_located"]);
    }

    #[test]
    fn holes_need_a_mark_and_a_location() {
        let centre = [0.0, 0.0, 0.0];
        let page = |marks: &[[f64; 2]], witnesses: &[[f64; 2]]| Page {
            marks: [("plan".to_string(), marks.to_vec())].into(),
            witnesses: [("plan".to_string(), witnesses.to_vec())].into(),
        };
        let bare = location(
            &[site([30.0, 0.5, 0.0], 2)],
            centre,
            &page(&[], &[]),
            Severity::Warning,
        );
        assert_eq!(
            codes(&bare),
            ["feature_no_centermark", "feature_not_located"]
        );
        assert_eq!(bare[1].requirements[0].parameter, "location.length.x");
        assert_eq!(
            bare[1].requirements.len(),
            1,
            "y is within 1 mm of the centre"
        );
        // Marked, and a witness at the hole's page x.
        let done = page(&[[100.5, 49.5]], &[[100.4, 10.0]]);
        assert!(
            location(
                &[site([30.0, 0.5, 0.0], 2)],
                centre,
                &done,
                Severity::Warning
            )
            .is_empty()
        );
        // A witness off by more than 0.6 mm, or on the other page axis, does not locate it.
        let off = page(&[[100.0, 50.0]], &[[100.7, 10.0], [10.0, 50.0]]);
        let found = location(
            &[site([30.0, 0.5, 0.0], 2)],
            centre,
            &off,
            Severity::Warning,
        );
        assert_eq!(codes(&found), ["feature_not_located"]);
        // A claim locates it; so does membership of a pattern; on the centre it needs none.
        let mut claimed = site([30.0, 0.5, 0.0], 2);
        claimed.claimed[0] = true;
        let mut patterned = site([30.0, 20.0, 0.0], 2);
        patterned.patterned = true;
        let marked = page(&[[100.0, 50.0]], &[]);
        for s in [claimed, patterned, site([0.4, -0.9, 7.0], 2)] {
            assert!(location(&[s], centre, &marked, Severity::Warning).is_empty());
        }
    }
}
