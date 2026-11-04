//! Hole coverage (Python's `linting/hole_coverage.py`): every recognised hole, or group of
//! identical holes, states its sizes on the sheet.
//!
//! The requirements come from the Analysis's hole features alone, read as the dimensions read
//! them ([`crate::bridge::holes`]: one reader, one principal-axis rule, one size resolution):
//!
//! - **The groups** ([`groups`]): the members of a recognised hole pattern
//!   (`Analysis.recognition.patterns`) are one group when the pattern is established
//!   ([`holes::established_with`]: every member read, all identical, drilled along an axis of
//!   the part and on distinct axis lines, and a bolt circle of five or more, or of fewer with a
//!   hole or boss on its centre). A grid or linear pattern is one group however it is spaced,
//!   as in Python, whose lint takes every pattern its drafting did not refuse, and whose
//!   drafting refuses only those (`_append_hole_features`); the port's dimensions also decline a
//!   grid that is not a lattice, or a linear pattern not evenly spaced, in the view (and call its
//!   holes out loose), which Python's do not;
//!   every other hole joins the loose holes with the same machining requirement, signed drilling
//!   direction included (LINT-hole_coverage-opposed_bores_distinct). An Analysis without a
//!   recognition record falls back to the fingerprints' pattern count and pitch.
//! - **Each group's requirements** ([`HoleGroup::requirements`]): its bore diameter
//!   (LINT-hole_coverage-bore_diameter); THRU for a through hole, or else its depth
//!   (-through_indicator, -blind_depth); the counterbore's, spotface's or countersink's two sizes
//!   (-cbore_diameter_depth, -spotface_diameter_depth, -csink_diameter_angle); and, for more than
//!   one hole, the quantity (-grouping_count).
//!
//! A size, or THRU, is **placed** when an annotation claims that measurement of one of the
//! group's holes *and* its printed text states the part's value at the precision printed (THRU
//! for `bore.through`, carried by the diameter's callout): one truthful statement serves a common
//! machining specification, as in draftwright (`_state`). The size must be stated by the term
//! that states it ([`Callout`]: the bore's diameter is the number after its `⌀`, the
//! counterbore's depth the one after its `↧`, …), so neither a quantity nor another size of the
//! same value confirms it (ISSUE-1217-a). The quantity is placed only when every member is
//! counted: claimed by a diameter's callout whose printed quantity is the number of holes it
//! claims, all of them the group's ([`uncounted`]; ISSUE-1143-b). A size is **unverifiable**
//! when an annotation claims it but its text does not state it, or when the Analysis does not
//! give it (a blind hole without a depth); otherwise a requirement is **missing**, THRU and the
//! quantity included when the diameter's callouts leave them off
//! (LINT-code-hole_requirement_state). Only those two are reported
//! (LINT-_parameter_coverage-report_uncovered_only). Lint cannot see the author's omissions or
//! placement drops, so the `suppressed` and `dropped` outcomes are not reached here.
//!
//! Pattern locations and spacing are checked by [`super::hole_patterns`]. Not yet ported here:
//! loose-hole location requirements (the location backstop runs separately), threads and
//! profiled bores, the assembly severity for these size findings, and the `suppressed` outcome.

use std::collections::{BTreeMap, BTreeSet};

use super::{LintIssue, Mark, Requirement, Severity};
use crate::bridge::holes::{self, Countersink, Hole, Recess};
use crate::model::Analysis;

/// Holes that share one machining specification, and so one callout.
#[derive(Clone, Debug, PartialEq)]
pub struct HoleGroup {
    /// The holes' Analysis IDs, in Analysis order.
    pub members: Vec<String>,
    /// Whether quiddity recognised them as a pattern (rather than as loose identical holes).
    pub pattern: bool,
    pub sizes: HoleSizes,
    /// The members' centres in the part's frame (the centres of their faces' boxes).
    pub centres: Vec<[f64; 3]>,
    /// The axis in the part's frame, as the fingerprint gives it (its sign is arbitrary).
    pub axis: Option<[f64; 3]>,
    /// How many holes the members are as counted: the coaxial segments of one through hole
    /// split by a slit are one ([`holes::drillings`]).
    pub drillings: usize,
}

/// A hole's machining sizes, as recognised.
#[derive(Clone, Debug, PartialEq)]
pub struct HoleSizes {
    pub diameter: f64,
    pub through: bool,
    /// The bore's depth, when the Analysis gives it; for a through hole, the thickness it passes
    /// through.
    pub depth: Option<f64>,
    pub counterbore: Option<Recess>,
    /// The counterbore-class steps outside the counterbore, outermost first (quiddity-rust#118).
    pub outer_steps: Vec<Recess>,
    pub spotface: Option<Recess>,
    pub countersink: Option<Countersink>,
}

impl HoleSizes {
    fn of(h: &Hole) -> HoleSizes {
        HoleSizes {
            diameter: h.diameter,
            through: h.through,
            depth: h.depth,
            counterbore: h.counterbore,
            outer_steps: h.outer_steps.clone(),
            spotface: h.spotface,
            countersink: h.countersink,
        }
    }
}

impl HoleGroup {
    /// The group's physical requirements, each with the value its text must state: none for
    /// `bore.through`, the group's size for `grouping.count`, and none for a size the Analysis
    /// does not give. Each outer step's are `outer_steps.{i}.diameter` and `.depth`, *i*
    /// quiddity's index (outermost first).
    pub fn requirements(&self) -> Vec<(String, Option<f64>)> {
        let s = &self.sizes;
        let mut out = vec![("bore.diameter".to_string(), Some(s.diameter))];
        if s.through {
            out.push(("bore.through".into(), None));
        } else {
            out.push(("bore.depth".into(), s.depth));
        }
        let recess = |r: Option<Recess>| r.map(|r| (r.diameter, r.depth));
        let mut sizes = vec![("counterbore".to_string(), recess(s.counterbore))];
        sizes.extend(
            s.outer_steps
                .iter()
                .enumerate()
                .map(|(i, r)| (format!("outer_steps.{i}"), recess(Some(*r)))),
        );
        sizes.push(("spotface".into(), recess(s.spotface)));
        for (role, sizes) in sizes {
            if let Some((a, b)) = sizes {
                out.push((format!("{role}.diameter"), Some(a)));
                out.push((format!("{role}.depth"), b));
            }
        }
        if let Some(c) = s.countersink {
            out.push(("countersink.diameter".into(), Some(c.diameter)));
            out.push(("countersink.angle".into(), c.angle));
        }
        if self.drillings > 1 {
            out.push(("grouping.count".into(), Some(self.drillings as f64)));
        }
        out
    }

    /// The callout that would state every size, as a fix suggests it (`?` for a size the
    /// Analysis does not give).
    pub fn callout(&self) -> String {
        let s = &self.sizes;
        let f = |v: Option<f64>| v.map_or("?".into(), fmt);
        let mut out = String::new();
        if self.drillings > 1 {
            out += &format!("{}× ", self.drillings);
        }
        out += &format!("⌀{}", fmt(s.diameter));
        out += &if s.through {
            " THRU".to_string()
        } else {
            format!(" ↧ {}", f(s.depth))
        };
        for r in s.counterbore.iter().chain(s.outer_steps.iter().rev()) {
            out += &format!(" ⌴ ⌀{} ↧ {}", fmt(r.diameter), f(r.depth));
        }
        if let Some(r) = s.spotface {
            out += &format!(" ⌴SF ⌀{} ↧ {}", fmt(r.diameter), f(r.depth));
        }
        if let Some(c) = s.countersink {
            out += &format!(" ⌵ ⌀{} × {}°", fmt(c.diameter), f(c.angle));
        }
        out
    }

    /// The centre of the members' centres.
    pub fn centre(&self) -> [f64; 3] {
        let n = self.centres.len().max(1) as f64;
        [0, 1, 2].map(|k| self.centres.iter().map(|c| c[k]).sum::<f64>() / n)
    }

    fn noun(&self) -> String {
        let s = &self.sizes;
        let operation = match s.depth {
            _ if s.through => "THRU".to_string(),
            Some(d) => format!("blind depth {}", fmt(d)),
            None => "blind, depth not given".to_string(),
        };
        let axis = self
            .axis
            .map_or("unknown".into(), |a| format!("({})", a.map(fmt).join(", ")));
        let c = self.centre();
        format!(
            "{}× ø{} {operation} {}, axis {axis}, centred at ({})",
            self.drillings,
            fmt(s.diameter),
            if self.pattern {
                "hole pattern"
            } else {
                "holes"
            },
            c.map(fmt).join(", ")
        )
    }
}

/// A number as messages print it: at most three decimals, trailing zeros dropped.
fn fmt(x: f64) -> String {
    let s = format!("{x:.3}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    if s == "-0" { "0".into() } else { s.into() }
}

/// The Analysis's holes in groups that share a callout. A hole whose record cannot be read is
/// in no group ([`coverage`] reports it).
pub fn groups(analysis: &Analysis) -> Vec<HoleGroup> {
    let holes: Vec<Hole> = holes::read(analysis)
        .into_iter()
        .filter_map(|(_, h)| h.ok())
        .collect();
    // `holes` is in Analysis order, so sorted indices give the members in Analysis order.
    let group = |members: &[usize], pattern: bool| {
        let mut members = members.to_vec();
        members.sort_unstable();
        HoleGroup {
            members: members
                .iter()
                .map(|&i| holes[i].feature.id.clone())
                .collect(),
            pattern,
            sizes: HoleSizes::of(&holes[members[0]]),
            centres: members
                .iter()
                .map(|&i| holes[i].feature.fingerprint.position)
                .collect(),
            axis: holes[members[0]].feature.fingerprint.axis,
            drillings: holes::drillings(members.iter().map(|&i| &holes[i])),
        }
    };
    let mut out = Vec::new();
    let mut taken = vec![false; holes.len()];
    match &analysis.recognition {
        // Patterns as quiddity recognised them: each established one is a group.
        Some(r) => {
            let refs: Vec<&Hole> = holes.iter().collect();
            let bosses = holes::bosses(analysis);
            for p in &r.patterns {
                if let Ok(members) = holes::established_with(p, &refs, &bosses)
                    && members.iter().all(|&m| !taken[m])
                {
                    out.push(group(&members, true));
                    members.iter().for_each(|&m| taken[m] = true);
                }
            }
        }
        // No recognition record (an Analysis assembled by hand): a pattern is read from the
        // fingerprints, accepted only when exactly its count of holes share its count and pitch,
        // the requirement and the opening's level along the drilling direction (to 1e-3 mm).
        None => {
            let mut patterns: BTreeMap<(usize, i64, Vec<i64>, i64), Vec<usize>> = BTreeMap::new();
            for (i, h) in holes.iter().enumerate() {
                if let Some(p) = &h.feature.fingerprint.pattern {
                    let level = (0..3).map(|k| h.direction[k] * h.at[k]).sum::<f64>();
                    let key = (
                        p.count,
                        holes::size_key(p.pitch),
                        h.requirement(),
                        (level * 1e3).round() as i64,
                    );
                    patterns.entry(key).or_default().push(i);
                }
            }
            for ((count, ..), members) in &patterns {
                if members.len() == *count && *count > 1 {
                    out.push(group(members, true));
                    members.iter().for_each(|&i| taken[i] = true);
                }
            }
        }
    }
    let mut loose: BTreeMap<Vec<i64>, Vec<usize>> = BTreeMap::new();
    for (i, h) in holes.iter().enumerate() {
        if !taken[i] {
            loose.entry(h.requirement()).or_default().push(i);
        }
    }
    for members in loose.values() {
        out.push(group(members, false));
    }
    // In Analysis order of their first member.
    let order: BTreeMap<&str, usize> = analysis
        .features
        .iter()
        .enumerate()
        .map(|(i, f)| (f.id.as_str(), i))
        .collect();
    out.sort_by_key(|g| order[g.members[0].as_str()]);
    out
}

/// The numbers a text prints, each with its count of decimals.
fn numbers(text: &str) -> Vec<(f64, usize)> {
    let chars: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if chars[i].is_ascii_digit() {
            let start = i;
            while i < chars.len() && chars[i].is_ascii_digit() {
                i += 1;
            }
            let mut decimals = 0;
            if i + 1 < chars.len() && chars[i] == '.' && chars[i + 1].is_ascii_digit() {
                i += 1;
                while i < chars.len() && chars[i].is_ascii_digit() {
                    i += 1;
                    decimals += 1;
                }
            }
            let s: String = chars[start..i].iter().collect();
            if let Ok(v) = s.parse() {
                out.push((v, decimals));
            }
        } else {
            i += 1;
        }
    }
    out
}

/// Whether *text* states *value* at the precision it prints: some number in it rounds from it.
/// The families sharing [`super::parameters`] read their labels this way; hole coverage reads a
/// callout term by term ([`Callout`]).
pub fn states(text: &str, value: f64) -> bool {
    numbers(text)
        .iter()
        .any(|&(n, decimals)| rounds(n, decimals, value))
}

/// Whether the number *n*, printed with *decimals* decimals, rounds from *value*.
fn rounds(n: f64, decimals: usize, value: f64) -> bool {
    (n - value).abs() <= 0.5 * 10f64.powi(-(decimals as i32)) + 1e-9 * value.abs().max(1.0)
}

/// Whether *text* states THRU.
fn says_thru(text: &str) -> bool {
    text.split(|c: char| !c.is_alphanumeric())
        .any(|w| w == "THRU")
}

/// What opened a segment of a hole callout: the bore, `⌴` (a counterbore, or draftwright's
/// spotface), the spotface symbol `⌴SF` (ASME Y14.5-2018's, as the port prints it) or a bare
/// `SF`, `⌵`, or a `⌀` printed after the bore's with no symbol (a captured sheet's text runs lose
/// the symbols draftwright draws as strokes: `4× ø 12 20 ø 20 20`).
#[derive(Clone, Copy, Debug, PartialEq)]
enum Opened {
    Bore,
    Recess,
    Spotface,
    Countersink,
    Bare,
}

/// One segment of a hole callout: the sizes its terms state, each a printed number and its count
/// of decimals.
#[derive(Clone, Debug, PartialEq)]
struct Segment {
    opened: Opened,
    diameter: Option<(f64, usize)>,
    depth: Option<(f64, usize)>,
    angle: Option<(f64, usize)>,
}

impl Segment {
    fn new(opened: Opened) -> Segment {
        Segment {
            opened,
            diameter: None,
            depth: None,
            angle: None,
        }
    }
}

/// A hole callout read term by term, in draftwright's grammar
/// (`[n×] ⌀d [fit] (THRU | ↧ depth) [⌴ ⌀d [↧ depth]]… [⌵ ⌀d [× a°]] [(r×c) | EQ SP ON ⌀D BC]`):
/// the leading quantity, the segments, and the numbers it prints outside them.
///
/// A size is a term's: the number after a `⌀` is its segment's diameter, the number after `↧`
/// (or, with the symbol lost, the bare number after a diameter) its depth, a number before `°`
/// its angle. A segment with an angle is the countersink. Not sizes: the quantity (`n×` at the
/// start, before a diameter, as draftwright's `_REPEAT_RE` reads it), a tolerance (a number
/// after `±`, `+` or `-`), a fit class (`H7`), and anything from a `(` (a grid's `(2×2)`) or
/// from `EQ`, `ON` or `BC` on (a bolt circle's diameter).
#[derive(Clone, Debug, PartialEq)]
struct Callout {
    count: Option<usize>,
    segments: Vec<Segment>,
    /// The numbers no term owns.
    loose: Vec<(f64, usize)>,
}

impl Callout {
    fn read(text: &str) -> Callout {
        let chars: Vec<char> = text.chars().collect();
        let mut out = Callout {
            count: None,
            segments: vec![Segment::new(Opened::Bore)],
            loose: Vec::new(),
        };
        let (mut diameter, mut depth, mut thru, mut first) = (false, false, false, true);
        let mut i = 0;
        while i < chars.len() {
            let c = chars[i];
            if c.is_ascii_digit() {
                let start = i;
                while i < chars.len() && chars[i].is_ascii_digit() {
                    i += 1;
                }
                let mut decimals = 0;
                if i + 1 < chars.len() && chars[i] == '.' && chars[i + 1].is_ascii_digit() {
                    i += 1;
                    while i < chars.len() && chars[i].is_ascii_digit() {
                        i += 1;
                        decimals += 1;
                    }
                }
                let v: f64 = chars[start..i]
                    .iter()
                    .collect::<String>()
                    .parse()
                    .unwrap_or(f64::NAN);
                let signed = start > 0 && matches!(chars[start - 1], '±' | '+' | '-');
                let mut j = i;
                while j < chars.len() && chars[j] == ' ' {
                    j += 1;
                }
                let times = matches!(chars.get(j), Some('×' | 'x' | 'X'));
                let mut k = j + 1;
                while times && k < chars.len() && chars[k] == ' ' {
                    k += 1;
                }
                // Invariant: The parser pushed a segment before processing its suffixes.
                let segment = out.segments.last_mut().unwrap();
                if first
                    && times
                    && decimals == 0
                    && (matches!(chars.get(k), Some('⌀' | 'ø' | 'Ø'))
                        || (chars.get(k) == Some(&'M')
                            && chars.get(k + 1).is_some_and(char::is_ascii_digit)))
                {
                    out.count = Some(v as usize);
                    i = k;
                } else if signed {
                    // A tolerance's deviation.
                } else if chars.get(i) == Some(&'°') {
                    segment.angle = Some((v, decimals));
                    if segment.opened == Opened::Bare {
                        segment.opened = Opened::Countersink;
                    }
                } else if diameter {
                    if segment.diameter.is_some() {
                        out.segments.push(Segment::new(Opened::Bare));
                    }
                    // Invariant: The parser pushed a segment before processing its suffixes.
                    out.segments.last_mut().unwrap().diameter = Some((v, decimals));
                } else if depth
                    || (segment.diameter.is_some()
                        && segment.depth.is_none()
                        && segment.angle.is_none()
                        && !(segment.opened == Opened::Bore && thru))
                {
                    segment.depth = Some((v, decimals));
                } else {
                    out.loose.push((v, decimals));
                }
                (diameter, depth, first) = (false, false, false);
                continue;
            }
            if c.is_alphabetic() && c != 'ø' && c != 'Ø' {
                let start = i;
                while i < chars.len() && chars[i].is_alphanumeric() {
                    i += 1;
                }
                let word: String = chars[start..i].iter().collect();
                match word.as_str() {
                    "THRU" => thru = true,
                    // `⌴SF` (or `⌴ SF`): the `⌴` just opened is the spotface symbol's.
                    "SF" => match out.segments.last_mut() {
                        Some(s) if *s == Segment::new(Opened::Recess) => {
                            s.opened = Opened::Spotface
                        }
                        _ => out.segments.push(Segment::new(Opened::Spotface)),
                    },
                    "EQ" | "ON" | "BC" => break,
                    _ => {}
                }
                first = false;
                continue;
            }
            match c {
                '(' => break,
                '⌀' | 'ø' | 'Ø' => diameter = true,
                '↧' => depth = true,
                '⌴' => out.segments.push(Segment::new(Opened::Recess)),
                '⌵' => out.segments.push(Segment::new(Opened::Countersink)),
                _ => {}
            }
            if !c.is_whitespace() {
                first = false;
            }
            i += 1;
        }
        out
    }

    /// The term stating *parameter*, a hole's size; *counterbore* says whether the hole has a
    /// counterbore, which the port prints, as its first `⌴` segment, before its spotface's, and
    /// *outer* how many outer steps it has, which the port prints after the counterbore,
    /// innermost first, each its own `⌴` segment (`outer_steps.{i}`, outermost first).
    fn term(&self, parameter: &str, counterbore: bool, outer: usize) -> Option<(f64, usize)> {
        let recesses: Vec<&Segment> = self.segments[1..]
            .iter()
            .filter(|s| s.opened != Opened::Countersink)
            .collect();
        let unmarked = || recesses.iter().filter(|s| s.opened != Opened::Spotface);
        let countersink = || {
            self.segments[1..]
                .iter()
                .find(|s| s.opened == Opened::Countersink)
        };
        let spotface = || {
            recesses
                .iter()
                .find(|s| s.opened == Opened::Spotface)
                .or_else(|| unmarked().nth(usize::from(counterbore) + outer))
        };
        // `outer_steps.{i}.diameter` or `.depth`: the unmarked segment after the counterbore
        // and the outer steps inside step *i*.
        if let Some(rest) = parameter.strip_prefix("outer_steps.") {
            let (i, size) = rest.split_once('.')?;
            let i: usize = i.parse().ok().filter(|&i| i < outer)?;
            let s = unmarked().nth(usize::from(counterbore) + outer - 1 - i)?;
            return match size {
                "diameter" => s.diameter,
                "depth" => s.depth,
                _ => None,
            };
        }
        match parameter {
            "bore.diameter" => self.segments[0].diameter,
            "bore.depth" => self.segments[0].depth,
            "counterbore.diameter" => unmarked().next()?.diameter,
            "counterbore.depth" => unmarked().next()?.depth,
            "spotface.diameter" => spotface()?.diameter,
            "spotface.depth" => spotface()?.depth,
            "countersink.diameter" => countersink()?.diameter,
            "countersink.angle" => countersink()?.angle,
            _ => None,
        }
    }

    /// Whether the callout states *value* for *parameter*: its term prints a number that rounds
    /// from it. Text with no diameter at all (a plain dimension) states the one number it prints.
    fn states(&self, parameter: &str, value: f64, counterbore: bool, outer: usize) -> bool {
        let term = if self.segments.iter().any(|s| s.diameter.is_some()) {
            self.term(parameter, counterbore, outer)
        } else {
            let numbers: Vec<(f64, usize)> = self
                .segments
                .iter()
                .flat_map(|s| [s.depth, s.angle])
                .flatten()
                .chain(self.loose.iter().copied())
                .collect();
            (numbers.len() == 1).then(|| numbers[0])
        };
        term.is_some_and(|(n, decimals)| rounds(n, decimals, value))
    }
}

/// Whether the rendered bore term, excluding quantities, tolerances and pitch circles,
/// states this diameter at the precision actually printed.
pub fn states_bore(text: &str, diameter: f64) -> bool {
    // A lone loose number may be a quantity, location or depth. Bore observations require
    // the explicit diameter term; the scalar fallback is for independent depth dimensions.
    Callout::read(text)
        .term("bore.diameter", false, 0)
        .is_some_and(|(number, decimals)| rounds(number, decimals, diameter))
}

/// A scheduled size cell is one physical hole's complete callout. Its physical riders
/// (THRU and quantity) are usable only while every size it claims is borne out by that
/// cell's own ink. A pitch-circle diameter or a number in another row proves nothing.
pub fn verified_schedule_size(analysis: &Analysis, mark: &Mark) -> bool {
    if !mark.name.starts_with("hole_schedule_") || !mark.name.ends_with("/size") {
        return true;
    }
    let bores: Vec<_> = mark
        .claims
        .iter()
        .filter(|c| c.parameter == "bore.diameter")
        .collect();
    if bores.len() != 1 {
        return false;
    }
    let Some(group) = groups(analysis)
        .into_iter()
        .find(|g| g.members.contains(&bores[0].feature))
    else {
        return false;
    };
    let callout = Callout::read(&mark.text);
    group
        .requirements()
        .iter()
        .filter(|(p, _)| !matches!(p.as_str(), "grouping.count" | "bore.through"))
        .all(|(parameter, value)| {
            mark.claims
                .iter()
                .any(|c| c.feature == bores[0].feature && c.parameter == *parameter)
                && value.is_some_and(|v| {
                    callout.states(
                        parameter,
                        v,
                        group.sizes.counterbore.is_some(),
                        group.sizes.outer_steps.len(),
                    )
                })
        })
        && (!group.sizes.through || says_thru(&mark.text))
}

/// The holes whose bore diameters *m* claims.
fn diameters(m: &Mark) -> BTreeSet<&str> {
    m.claims
        .iter()
        .filter(|c| c.parameter == "bore.diameter")
        .map(|c| c.feature.as_str())
        .collect()
}

/// The members of *g* whose quantity no mark states (draftwright's `_synthetic_placed`: every
/// owner of the group carries its count, the counts summing to the group's). A mark counts the
/// holes whose diameters it claims when they are all the group's and its quantity (`n×`, or one
/// when it prints none) is how many it claims, as a grouped callout's printed quantity must
/// agree with its member allocations (`_index_hole_evidence`). So one hole's callout counts that
/// hole only, whatever quantity it prints. A mark claiming the whole group may count its
/// drillings instead (a split through hole's segments once, [`HoleGroup::drillings`]).
fn uncounted<'a>(g: &'a HoleGroup, marks: &[Mark]) -> Vec<&'a str> {
    let counted: BTreeSet<&str> = marks
        .iter()
        .filter_map(|m| {
            let claimed = diameters(m);
            let n = Callout::read(&m.text).count.unwrap_or(1);
            let whole = claimed.len() == g.members.len() && n == g.drillings;
            ((claimed.len() == n || whole)
                && claimed.iter().all(|f| g.members.iter().any(|x| x == f)))
            .then_some(claimed)
        })
        .flatten()
        .collect();
    g.members
        .iter()
        .map(String::as_str)
        .filter(|f| !counted.contains(f))
        .collect()
}

/// The hole coverage findings: each requirement of each group that the marks do not state.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    // A thread's source-bound manufacturing row can carry its tapping-drill values.
    // This evidence augments only that owner's hole check, never measurement ownership.
    let manufacturing = crate::pmi_lowering::manufacturing(analysis);
    let normal = |s: &str| s.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut evidence = marks.to_vec();
    for mark in &mut evidence {
        for o in manufacturing.owned.iter().filter(|o| {
            mark.claims
                .iter()
                .any(|c| c.feature == o.id && c.parameter == "thread")
        }) {
            let words: Vec<_> = mark.text.split_whitespace().collect();
            let reference = words
                .windows(3)
                .find(|w| w[0] == "SEE" && w[1] == "MFG")
                .map(|w| format!("MFG {}", w[2].trim_end_matches([';', ','])));
            if let Some(tag) = reference {
                if let Some(row) = marks.iter().find(|m| {
                    m.name == format!("manufacturing_requirements/{tag}")
                        && normal(&m.text) == normal(&format!("{tag} {}", o.text))
                }) {
                    mark.text += " ";
                    mark.text += &row.text;
                }
            } else if let Some(t) = o.thread()
                && t.internal
                && t.through
                && t.conventional_drill()
                && super::pmi_coverage::thread_text_states(t, &mark.text)
            {
                // Invariant: The tapped-hole branch requires a known drill diameter.
                mark.text += &format!(" ⌀{} THRU", t.drill_diameter.unwrap());
            }
        }
    }
    let marks = evidence.as_slice();
    let mut issues = Vec::new();
    for g in groups(analysis) {
        let uncounted = uncounted(&g, marks);
        for (parameter, value) in g.requirements() {
            let parameter = parameter.as_str();
            // THRU and the quantity are stated by the diameter's callout.
            let carrier = match parameter {
                "bore.through" | "grouping.count" => "bore.diameter",
                p => p,
            };
            let synthetic = carrier != parameter;
            let claiming: Vec<&Mark> = marks
                .iter()
                .filter(|m| {
                    m.claims
                        .iter()
                        .any(|c| c.parameter == carrier && g.members.contains(&c.feature))
                })
                .collect();
            let requirements: Vec<Requirement> = g
                .members
                .iter()
                .map(|f| Requirement {
                    feature: f.clone(),
                    parameter: parameter.into(),
                })
                .collect();
            let measurements: Vec<String> =
                g.members.iter().map(|f| format!("{f}/{carrier}")).collect();
            // A size the Analysis does not give cannot be checked against any text (THRU has
            // no value to give).
            let value = match value {
                Some(v) => v,
                None if parameter == "bore.through" => f64::NAN,
                None => {
                    issues.push(LintIssue {
                    sheet: None,
                    id: format!("l:hole_requirement_unverifiable:{}/{parameter}", g.members[0]),
                    code: "hole_requirement_unverifiable".into(),
                    severity: Severity::Warning,
                    message: format!(
                        "{}: requirement {parameter} cannot be checked: the analysis does not give its value",
                        g.noun()
                    ),
                    at: None,
                    view: claiming.first().and_then(|m| m.view.clone()),
                    subjects: g.members.clone(),
                    requirements,
                    measurements,
                    sources: Vec::new(),
                    stage: None,
                    annotation: claiming.first().map(|m| m.name.clone()),
                    related: claiming.iter().skip(1).map(|m| m.name.clone()).collect(),
                    evidence: Some(format!("the hole record gives no {parameter}")),
                    fix: None,
                });
                    continue;
                }
            };
            let counterbore = g.sizes.counterbore.is_some();
            let outer = g.sizes.outer_steps.len();
            let stated = |m: &&Mark| match parameter {
                "bore.through" => says_thru(&m.text),
                _ => Callout::read(&m.text).states(parameter, value, counterbore, outer),
            };
            if match parameter {
                "grouping.count" => uncounted.is_empty(),
                // A native grouped callout states the group's shared nominal; quantity is
                // audited separately. A schedule replaces that statement with physical rows,
                // so a valid sibling row must not hide one missing or damaged member cell.
                _ if claiming.iter().any(|m| {
                    m.name.starts_with("hole_schedule_") || m.name.starts_with("hole_table_plan/")
                }) =>
                {
                    g.members.iter().all(|feature| {
                        claiming.iter().any(|m| {
                            m.claims
                                .iter()
                                .any(|c| &c.feature == feature && c.parameter == carrier)
                                && stated(m)
                        })
                    })
                }
                _ => claiming.iter().any(stated),
            } {
                continue;
            }
            let what = match parameter {
                "bore.through" => "THRU".to_string(),
                "grouping.count" => format!("{}×", g.drillings),
                _ => fmt(value),
            };
            // A size claimed but misprinted cannot be credited; THRU or the quantity left off
            // the diameter's callout is simply not stated (the callout claims the diameter).
            let (code, message, evidence, annotation, view) = match claiming.first() {
                Some(m) if !synthetic => (
                    "hole_requirement_unverifiable",
                    format!(
                        "{}: requirement {parameter} is claimed by {} but its text does not state {what}",
                        g.noun(),
                        m.name
                    ),
                    Some(format!("{:?} does not state {what}", m.text)),
                    Some(m.name.clone()),
                    m.view.clone(),
                ),
                first => (
                    "hole_requirement_missing",
                    format!(
                        "{}: requirement {parameter} ({what}) is not stated on the sheet",
                        g.noun()
                    ),
                    first.map(|m| match parameter {
                        "grouping.count" => {
                            let k = diameters(m).len();
                            format!(
                                "the diameter's callout {:?} prints {} for the {k} hole{} it claims; {} {} counted by no callout",
                                m.text,
                                Callout::read(&m.text)
                                    .count
                                    .map_or("no quantity".into(), |n| format!("{n}×")),
                                if k == 1 { "" } else { "s" },
                                uncounted.join(", "),
                                if uncounted.len() == 1 { "is" } else { "are" }
                            )
                        }
                        _ => format!("the diameter's callout {:?} does not state {what}", m.text),
                    }),
                    first.map(|m| m.name.clone()),
                    first.and_then(|m| m.view.clone()),
                ),
            };
            issues.push(LintIssue {
                sheet: None,
                id: format!("l:{code}:{}/{parameter}", g.members[0]),
                code: code.into(),
                severity: Severity::Warning,
                message,
                at: None,
                view,
                subjects: g.members.clone(),
                measurements,
                requirements,
                sources: Vec::new(),
                stage: None,
                annotation,
                related: claiming.iter().skip(1).map(|m| m.name.clone()).collect(),
                evidence,
                fix: Some(format!(
                    "State {what} for the holes with a callout reading \"{}\", in a view that sees the holes end-on.",
                    g.callout()
                )),
            });
        }
    }
    issues.extend(unattached_countersinks(analysis));
    // A hole whose record cannot be read is reported, never left out of the count.
    for (f, read) in holes::read(analysis) {
        if let Err(why) = read {
            issues.push(LintIssue {
                sheet: None,
                id: format!("l:hole_requirement_unverifiable:{}/?", f.id),
                code: "hole_requirement_unverifiable".into(),
                severity: Severity::Warning,
                message: format!(
                    "hole {}: its sizes cannot be read from the analysis, so its requirements cannot be checked",
                    f.id
                ),
                at: None,
                view: None,
                subjects: vec![f.id.clone()],
                requirements: vec![Requirement {
                    feature: f.id.clone(),
                    parameter: "?".into(),
                }],
                measurements: Vec::new(),
                sources: Vec::new(),
                stage: None,
                annotation: None,
                related: Vec::new(),
                evidence: Some(why),
                fix: None,
            });
        }
    }
    issues
}

/// Consume each attached seat once. A standalone recognised seat cannot disappear merely
/// because the hole record has only one countersink slot (Python issue 1229/1370).
fn unattached_countersinks(analysis: &Analysis) -> Vec<LintIssue> {
    let mut attached: Vec<&serde_json::Value> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "hole")
        .filter_map(|f| f.parameters.get("csink").filter(|v| !v.is_null()))
        .collect();
    let same = |a: &serde_json::Value, b: &serde_json::Map<String, serde_json::Value>| {
        [
            "axis",
            "location",
            "major_diameter",
            "drill_diameter",
            "included_angle",
            "depth",
        ]
        .iter()
        .all(|k| a.get(*k) == b.get(*k))
    };
    let mut issues = Vec::new();
    for f in analysis
        .features
        .iter()
        .filter(|f| f.kind == "countersink" || f.fingerprint.subtype == "CounterSink")
    {
        if let Some(i) = attached.iter().position(|a| same(a, &f.parameters)) {
            attached.remove(i);
            continue;
        }
        for (parameter, field) in [
            ("countersink.diameter", "major_diameter"),
            ("countersink.angle", "included_angle"),
        ] {
            let mut finding = super::uncovered(
                analysis,
                "hole_requirement_unverifiable",
                &f.id,
                parameter,
                format!(
                    "Recognised countersink {} has no attributable hole callout: {parameter} remains unverifiable; the hole record carries only one seat.",
                    f.id
                ),
            );
            finding.evidence = f
                .parameters
                .get(field)
                .map(|v| format!("Recognised {field}: {v}"));
            finding.fix = Some("State this seat on its opening face, or supply recognition evidence attributing every seat to its bore.".into());
            issues.push(finding);
        }
    }
    issues
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::Claim;
    use serde_json::{Value, json};

    /// cgb243's f:modelled_thread:1 (M14×1.5 over its *start*..*end* along z) and a ⌀12 hole
    /// down z from its opening at *top*, *depth* deep.
    fn threaded(internal: bool, start: f64, end: f64, x: f64, top: f64, depth: f64) -> Analysis {
        serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": 2, "loader": "test" },
            "envelope": [[-20.0, -20.0, -20.0], [20.0, 20.0, 20.0]],
            "iso_bounds": [-20.0, -20.0, 20.0, 20.0],
            "features": [
                {"id": "f:modelled_thread:1", "kind": "modelled_thread", "faces": [0],
                 "fingerprint": {"version": "test", "family": "modelled_threads",
                    "subtype": "ModelledThread", "sizes": {}, "axis": [0, 0, 1],
                    "position": [0, 0, 0], "neighbourhood": []},
                 "parameters": {"axis": {"point": [0, 0, 0], "direction": [0, 0, 1]},
                    "side": if internal { "internal" } else { "external" },
                    "major_diameter": 13.6, "minor_diameter": 12.0, "pitch": 1.5,
                    "hand": "right", "starts": 1, "start": start, "end": end}},
                {"id": "f:hole:1", "kind": "hole", "faces": [1],
                 "fingerprint": {"version": "test", "family": "holes", "subtype": "HoleRecord",
                    "sizes": {}, "axis": [0, 0, 1], "position": [x, 0, top], "neighbourhood": []},
                 "parameters": {"axis": [0, 0, -1], "bottom": "through", "cbore": null,
                    "csink": null, "depth": depth, "diameter": 12.0, "location": [x, 0, top],
                    "spotface": null}}
            ]
        }))
        .unwrap()
    }

    /// The modelled-thread half of LINT-hole_coverage-thread_and_profile is retired
    /// (2026-10-23): a thread recognised from modelled geometry carries no `thread.depth`
    /// requirement, since its trimmed span includes partial end turns and runouts and does not
    /// prove a depth. Its extent is left to the ISO 6410 depiction; a depth is required only
    /// when a Spec thread length or AP242 minimum full thread states one
    /// (tests/spec_notes.rs::a_spec_threads_depth_on_its_holes_is_required_by_lint).
    #[test]
    fn a_modelled_thread_shorter_than_its_hole_requires_no_depth() {
        let mark = Mark {
            name: "d:f:modelled_thread:1/thread".into(),
            view: Some("v:front".into()),
            text: "M14×1.5".into(),
            claims: vec![Claim {
                feature: "f:modelled_thread:1".into(),
                parameter: "thread".into(),
            }],
        };
        // 9.13 of thread at the top of a 19.1 hole, as in cgb243.
        let a = threaded(true, -1.13, 8.0, 0.0, 8.0, 19.1);
        let issues = coverage(&a, &[mark]);
        assert!(
            issues
                .iter()
                .all(|i| i.requirements.iter().all(|r| r.parameter != "thread.depth")),
            "{issues:#?}"
        );
    }

    /// An Analysis of the holes *records* (quiddity's record fields, each with its `pattern`
    /// count if any), each on its own face, along the part's z.
    fn part(records: &[Value]) -> Analysis {
        let features: Vec<Value> = records
            .iter()
            .enumerate()
            .map(|(i, r)| {
                let mut parameters = r.clone();
                let pattern = parameters.as_object_mut().unwrap().remove("pattern");
                let location = r["location"].clone();
                json!({
                    "id": format!("f:hole:{}", i + 1), "kind": "hole", "faces": [i],
                    "parameters": parameters,
                    "fingerprint": {
                        "version": "test", "family": "holes", "subtype": "HoleRecord",
                        "sizes": {}, "axis": [0.0, 0.0, 1.0], "position": location,
                        "pattern": pattern.map(|n| json!({"count": n, "pitch": 10.0})),
                        "neighbourhood": [],
                    },
                })
            })
            .collect();
        serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": records.len(),
                         "loader": "test" },
            "envelope": [[0.0, 0.0, 0.0], [100.0, 100.0, 10.0]],
            "features": features,
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
        }))
        .unwrap()
    }

    fn hole(at: [f64; 3], down: bool) -> Value {
        json!({ "axis": [0.0, 0.0, if down { -1.0 } else { 1.0 }], "location": at,
                "diameter": 6.6, "depth": 10.0, "bottom": "through",
                "cbore": null, "spotface": null, "csink": null })
    }

    fn mark(text: &str, claims: &[(&str, &str)]) -> Mark {
        Mark {
            name: format!("m:{text}"),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: claims
                .iter()
                .map(|(f, p)| Claim {
                    feature: f.to_string(),
                    parameter: p.to_string(),
                })
                .collect(),
        }
    }

    fn found(analysis: &Analysis, marks: &[Mark]) -> Vec<String> {
        coverage(analysis, marks)
            .into_iter()
            .map(|i| {
                format!(
                    "{} {} {}",
                    i.code,
                    i.subjects.join("+"),
                    i.requirements[0].parameter
                )
            })
            .collect()
    }

    /// #225: a through hole split by a slit into two coaxial segments (shf08's clamp hole) is one
    /// hole: its group wants no quantity, `⌀6.6 THRU` claiming both segments states it, and the
    /// count audit sees one ø6.6. A third hole off the line counts again.
    #[test]
    fn a_through_hole_split_by_a_slit_counts_once() {
        let segment = |top: f64| {
            let mut h = hole([50.0, 50.0, top], true);
            h["depth"] = json!(4.4);
            h
        };
        let a = part(&[segment(10.0), segment(4.4)]);
        let g = groups(&a);
        assert_eq!((g.len(), g[0].drillings), (1, 1));
        assert!(g[0].requirements().iter().all(|r| r.0 != "grouping.count"));
        assert_eq!(g[0].callout(), "⌀6.6 THRU");
        let both = [("f:hole:1", "bore.diameter"), ("f:hole:2", "bore.diameter")];
        assert_eq!(found(&a, &[mark("⌀6.6 THRU", &both)]), Vec::<String>::new());
        assert!(super::super::coverage::counts(&a, &[mark("⌀6.6 THRU", &both)]).is_empty());

        let b = part(&[segment(10.0), segment(4.4), hole([20.0, 50.0, 10.0], true)]);
        let g = groups(&b);
        assert_eq!(g[0].drillings, 2);
        let all = [both[0], both[1], ("f:hole:3", "bore.diameter")];
        assert_eq!(
            found(&b, &[mark("2× ⌀6.6 THRU", &all)]),
            Vec::<String>::new()
        );
        assert_eq!(
            found(&b, &[mark("⌀6.6 THRU", &all)]),
            ["hole_requirement_missing f:hole:1+f:hole:2+f:hole:3 grouping.count"]
        );
    }

    /// LINT-hole_coverage-opposed_bores_distinct: holes drilled from opposite faces are two
    /// requirements even when every size matches; identical holes drilled one way are one.
    #[test]
    fn holes_drilled_from_opposite_faces_are_separate_groups() {
        let a = part(&[
            hole([10.0, 10.0, 10.0], true),
            hole([20.0, 10.0, 10.0], true),
            hole([30.0, 10.0, 0.0], false),
        ]);
        let g = groups(&a);
        assert_eq!(g.len(), 2);
        assert_eq!(g[0].members, ["f:hole:1", "f:hole:2"]);
        assert_eq!(g[1].members, ["f:hole:3"]);
        // One callout for the pair leaves the opposed hole unstated.
        let pair = mark(
            "2× ⌀6.6 THRU",
            &[("f:hole:1", "bore.diameter"), ("f:hole:2", "bore.diameter")],
        );
        assert_eq!(
            found(&a, &[pair]),
            [
                "hole_requirement_missing f:hole:3 bore.diameter",
                "hole_requirement_missing f:hole:3 bore.through"
            ]
        );
        // Claiming one of the pair, it counts that one only (ISSUE-1143-b).
        let one = mark("2× ⌀6.6 THRU", &[("f:hole:1", "bore.diameter")]);
        assert_eq!(
            found(&a, &[one]),
            [
                "hole_requirement_missing f:hole:1+f:hole:2 grouping.count",
                "hole_requirement_missing f:hole:3 bore.diameter",
                "hole_requirement_missing f:hole:3 bore.through"
            ]
        );
        // A callout claiming all three but printing "3×" states no group's quantity.
        let all = mark(
            "3× ⌀6.6 THRU",
            &[("f:hole:1", "bore.diameter"), ("f:hole:3", "bore.diameter")],
        );
        assert_eq!(
            found(&a, &[all]),
            ["hole_requirement_missing f:hole:1+f:hole:2 grouping.count"]
        );
    }

    #[test]
    fn a_hole_without_sizes_is_reported_not_dropped() {
        let mut odd = hole([10.0, 10.0, 10.0], true);
        odd["diameter"] = Value::Null;
        let a = part(&[odd]);
        assert!(groups(&a).is_empty());
        assert_eq!(found(&a, &[]), ["hole_requirement_unverifiable f:hole:1 ?"]);
    }

    /// A blind hole whose record gives no depth has a depth requirement that cannot be checked:
    /// it is reported unverifiable, whatever the sheet prints, never compared with a made-up
    /// value. A through hole needs no depth.
    #[test]
    fn a_blind_hole_without_a_depth_is_unverifiable() {
        let mut blind = hole([10.0, 10.0, 10.0], true);
        blind["bottom"] = json!("flat");
        blind.as_object_mut().unwrap().remove("depth");
        let mut through = hole([40.0, 10.0, 10.0], true);
        through.as_object_mut().unwrap().remove("depth");
        through["diameter"] = json!(8.0);
        let a = part(&[blind, through]);
        let marks = [
            mark(
                "⌀6.6 ↧ 10",
                &[("f:hole:1", "bore.diameter"), ("f:hole:1", "bore.depth")],
            ),
            mark("⌀8 THRU", &[("f:hole:2", "bore.diameter")]),
        ];
        let issues = coverage(&a, &marks);
        assert_eq!(
            found(&a, &marks),
            ["hole_requirement_unverifiable f:hole:1 bore.depth"]
        );
        assert!(!issues[0].message.contains("NaN"), "{}", issues[0].message);
        assert_eq!(issues[0].annotation.as_deref(), Some("m:⌀6.6 ↧ 10"));
        assert_eq!(groups(&a)[0].callout(), "⌀6.6 ↧ ?");
    }

    /// A recognised pattern is one group; one whose members are not all present falls back to
    /// loose holes (LINT-hole_coverage-bolt_circle_corroboration's fallback).
    #[test]
    fn a_pattern_is_a_group_only_when_all_its_members_are_found() {
        let mut members: Vec<Value> = (0..4)
            .map(|i| {
                let mut h = hole([10.0 * f64::from(i), 0.0, 10.0], true);
                h["pattern"] = json!(4);
                h
            })
            .collect();
        members.push(hole([50.0, 50.0, 10.0], true));
        let g = groups(&part(&members));
        assert_eq!(g.len(), 2);
        assert!(g[0].pattern && g[0].members.len() == 4);
        assert!(!g[1].pattern && g[1].members == ["f:hole:5"]);
        members.truncate(3);
        let g = groups(&part(&members));
        assert_eq!(g.len(), 1);
        assert!(!g[0].pattern && g[0].members.len() == 3);
    }

    /// With a recognition record, the recognised patterns are the groups: a grid of identical
    /// holes is one, even these four in a row, which the port's dimensions decline as no lattice
    /// (Python's lint and drafting keep it); a bolt circle of fewer than five holes with nothing
    /// on its centre is not established (ISSUE-1596-a), so its holes join the loose holes of the
    /// same requirement, as the dimensions call them out; with a bore on its centre it is a
    /// pattern (LINT-hole_coverage-bolt_circle_corroboration).
    #[test]
    fn recognised_patterns_are_the_groups() {
        let mut a = part(
            &(0..9)
                .map(|i| hole([10.0 * f64::from(i), 0.0, 10.0], true))
                .collect::<Vec<_>>(),
        );
        let ids = |r: std::ops::Range<usize>| -> Vec<String> {
            r.map(|i| format!("f:hole:{}", i + 1)).collect()
        };
        let pattern = |n: usize, arrangement: &str, members: Vec<String>| {
            json!({ "id": format!("f:hole_pattern:{n}"), "arrangement": arrangement,
                    "members": members, "faces": [], "parameters": {} })
        };
        a.recognition = Some(
            serde_json::from_value(json!({
                "recogniser": "test",
                "frame": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "patterns": [pattern(1, "grid", ids(0..4)), pattern(2, "bolt_circle", ids(4..8))],
            }))
            .unwrap(),
        );
        let g = groups(&a);
        assert_eq!(
            g.iter()
                .map(|g| (g.members.clone(), g.pattern))
                .collect::<Vec<_>>(),
            [(ids(0..4), true), (ids(4..9), false)]
        );
        // Four holes on a ø40 circle about (50, 50) with a ø20 bore on its centre: the circle
        // is corroborated, and its four holes are one group, the bore another.
        let mut circle: Vec<Value> = [[70.0, 50.0], [50.0, 70.0], [30.0, 50.0], [50.0, 30.0]]
            .iter()
            .map(|c| hole([c[0], c[1], 10.0], true))
            .collect();
        let mut bore = hole([50.0, 50.0, 10.0], true);
        bore["diameter"] = json!(20.0);
        circle.push(bore);
        let mut a = part(&circle);
        let bolt_circle = |members: Vec<String>| {
            json!({ "id": "f:hole_pattern:1", "arrangement": "bolt_circle", "members": members,
                    "faces": [], "parameters": { "center": [50.0, 50.0, 10.0], "diameter": 40.0 } })
        };
        let recognition = |members: Vec<String>| {
            serde_json::from_value(json!({
                "recogniser": "test",
                "frame": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                "patterns": [bolt_circle(members)],
            }))
            .unwrap()
        };
        a.recognition = Some(recognition(ids(0..4)));
        let g = groups(&a);
        assert_eq!(
            g.iter()
                .map(|g| (g.members.clone(), g.pattern))
                .collect::<Vec<_>>(),
            [(ids(0..4), true), (ids(4..5), false)]
        );
        // Without the bore, the four are loose holes.
        let mut a = part(&circle[..4]);
        a.recognition = Some(recognition(ids(0..4)));
        assert!(!groups(&a)[0].pattern);
    }

    /// LINT-hole_coverage-blind_depth, -cbore_diameter_depth, -spotface_diameter_depth and
    /// -csink_diameter_angle: each recess's two sizes are requirements, stated by the callout.
    #[test]
    fn a_blind_counterbored_or_countersunk_hole_states_its_recess() {
        let mut blind = hole([10.0, 10.0, 10.0], true);
        blind["bottom"] = json!("flat");
        blind["cbore"] = json!({ "diameter": 11.0, "depth": 6.4 });
        let mut sunk = hole([40.0, 10.0, 10.0], true);
        sunk["csink"] = json!({ "major_diameter": 12.4, "included_angle": 90.0,
                                "drill_diameter": 6.6, "depth": 2.9 });
        let mut faced = hole([70.0, 10.0, 0.0], false);
        faced["spotface"] = json!({ "diameter": 14.0, "depth": 0.5 });
        let a = part(&[blind, sunk, faced]);
        let g = groups(&a);
        let names = |i: usize| {
            g[i].requirements()
                .into_iter()
                .map(|r| r.0)
                .collect::<Vec<_>>()
        };
        assert_eq!(
            names(0),
            [
                "bore.diameter",
                "bore.depth",
                "counterbore.diameter",
                "counterbore.depth"
            ]
        );
        assert_eq!(
            names(1),
            [
                "bore.diameter",
                "bore.through",
                "countersink.diameter",
                "countersink.angle"
            ]
        );
        assert_eq!(
            names(2),
            [
                "bore.diameter",
                "bore.through",
                "spotface.diameter",
                "spotface.depth"
            ]
        );
        let claims = |f: &'static str, ps: &[&'static str]| -> Vec<(&'static str, &'static str)> {
            ps.iter().map(|p| (f, *p)).collect()
        };
        let stated = [
            mark(
                "⌀6.6 ↧ 10 ⌴ ⌀11 ↧ 6.4",
                &claims(
                    "f:hole:1",
                    &[
                        "bore.diameter",
                        "bore.depth",
                        "counterbore.diameter",
                        "counterbore.depth",
                    ],
                ),
            ),
            mark(
                "⌀6.6 THRU ⌵ ⌀12.4 × 90°",
                &claims(
                    "f:hole:2",
                    &["bore.diameter", "countersink.diameter", "countersink.angle"],
                ),
            ),
            mark(
                "⌀6.6 THRU SF ⌀14 ↧ 0.5",
                &claims(
                    "f:hole:3",
                    &["bore.diameter", "spotface.diameter", "spotface.depth"],
                ),
            ),
        ];
        assert_eq!(found(&a, &stated), Vec::<String>::new());
        // The fix states every size.
        assert_eq!(g[0].callout(), "⌀6.6 ↧ 10 ⌴ ⌀11 ↧ 6.4");
        assert_eq!(g[1].callout(), "⌀6.6 THRU ⌵ ⌀12.4 × 90°");
        assert_eq!(g[2].callout(), "⌀6.6 THRU ⌴SF ⌀14 ↧ 0.5");
        let mut symbol = stated.clone();
        symbol[2].text = g[2].callout();
        assert_eq!(found(&a, &symbol), Vec::<String>::new());
        // Leaving the countersink's angle off, and misprinting the counterbore's depth.
        let mut short = stated.clone();
        short[0].text = "⌀6.6 ↧ 10 ⌴ ⌀11 ↧ 6.9".into();
        short[1].text = "⌀6.6 THRU ⌵ ⌀12.4".into();
        short[1].claims.pop();
        assert_eq!(
            found(&a, &short),
            [
                "hole_requirement_unverifiable f:hole:1 counterbore.depth",
                "hole_requirement_missing f:hole:2 countersink.angle"
            ]
        );
    }

    #[test]
    fn a_number_is_stated_at_the_precision_printed() {
        assert!(states("⌀13.0 ↧ 5.7", 12.9898));
        assert!(states("⌀13.0 ↧ 5.7", 5.73));
        assert!(!states("⌀13.0 ↧ 5.7", 12.94));
        assert!(states("12× ⌀8 THRU", 8.0));
        assert!(states("12× ⌀8 THRU", 8.4));
        assert!(!states("12× ⌀8 THRU", 8.6));
        assert!(states("⌵ ⌀10 × 90°", 90.0));
    }

    #[test]
    fn thru_and_the_quantity_are_read_as_words() {
        let count = |text: &str| Callout::read(text).count;
        assert!(says_thru("12× ⌀8 THRU EQ SP"));
        assert!(!says_thru("THRUST"));
        assert_eq!(count("12× ⌀8 THRU"), Some(12));
        assert_eq!(count("4X ⌀5"), Some(4));
        assert_eq!(count("4× ø 10 THRU"), Some(4));
        assert_eq!(count("1.5× ⌀8"), None);
        // Only a leading quantity before a diameter: not a grid's shape, not a pitch.
        assert_eq!(count("⌀8 THRU (6×2)"), None);
        assert_eq!(count("2× 25"), None);
    }

    /// ISSUE-1217-a: each size is read from the term that states it, in the port's callouts and
    /// in a captured sheet's text runs, which lose the symbols drawn as strokes.
    #[test]
    fn a_size_is_read_from_its_own_term() {
        let term = |text: &str, parameter: &str, counterbore: bool| {
            Callout::read(text)
                .term(parameter, counterbore, 0)
                .map(|(v, _)| v)
        };
        // The port's spotface symbol (`⌴SF`), and the earlier form with two `⌴` segments.
        for port in [
            "4× ⌀10 +0.1 -0.02 ↧ 12 ⌴ ⌀16 ↧ 5 ⌴SF ⌀20 ↧ 0.5 (2×2)",
            "4× ⌀10 +0.1 -0.02 ↧ 12 ⌴ ⌀16 ↧ 5 ⌴ ⌀20 ↧ 0.5 (2×2)",
        ] {
            assert_eq!(term(port, "bore.diameter", true), Some(10.0));
            assert_eq!(term(port, "bore.depth", true), Some(12.0));
            assert_eq!(term(port, "counterbore.diameter", true), Some(16.0));
            assert_eq!(term(port, "counterbore.depth", true), Some(5.0));
            assert_eq!(term(port, "spotface.diameter", true), Some(20.0));
            assert_eq!(term(port, "spotface.depth", true), Some(0.5));
        }
        assert_eq!(
            Callout::read("⌀6 THRU ⌴ ⌀11 ↧ 6.4 ⌴SF ⌀20 ↧ 1")
                .segments
                .len(),
            3
        );
        // A lone spotface: the port's symbol, draftwright's `⌴`, and a hand-written `SF`.
        for text in [
            "⌀6.6 THRU ⌴SF ⌀14 ↧ 0.5",
            "⌀6.6 THRU ⌴ SF ⌀14 ↧ 0.5",
            "⌀6.6 THRU ⌴ ⌀14 ↧ 0.5",
            "⌀6.6 THRU SF ⌀14 ↧ 0.5",
        ] {
            assert_eq!(term(text, "spotface.diameter", false), Some(14.0), "{text}");
            assert_eq!(term(text, "spotface.depth", false), Some(0.5), "{text}");
        }
        // The spotface symbol is not a counterbore.
        assert_eq!(
            term("⌀6.6 THRU ⌴SF ⌀14 ↧ 0.5", "counterbore.diameter", false),
            None
        );
        let sunk = "⌀8.1 H7 THRU ⌵ ⌀12.4 × 90°";
        assert_eq!(term(sunk, "bore.diameter", false), Some(8.1));
        assert_eq!(term(sunk, "bore.depth", false), None);
        assert_eq!(term(sunk, "countersink.diameter", false), Some(12.4));
        assert_eq!(term(sunk, "countersink.angle", false), Some(90.0));
        // Captured text runs: `ø 12 20 ø 20 20` is ⌀12 ↧ 20 ⌴ ⌀20 ↧ 20.
        let captured = "4× ø 12 20 ø 20 20";
        assert_eq!(term(captured, "bore.depth", true), Some(20.0));
        assert_eq!(term(captured, "counterbore.diameter", true), Some(20.0));
        assert_eq!(term(captured, "counterbore.depth", true), Some(20.0));
        let thru = "4× ø 10.5 THRU ø 15.9 12.7 (2×2)";
        assert_eq!(term(thru, "bore.depth", true), None);
        assert_eq!(term(thru, "counterbore.depth", true), Some(12.7));
        // A bolt circle's diameter is not the bore's, nor a second segment.
        let circle = Callout::read("12× ø 8 THRU EQ SP ON ø110 BC UPPER FACE");
        assert_eq!(circle.segments.len(), 1);
        assert!(circle.states("bore.diameter", 8.0, false, 0));
        assert!(!circle.states("bore.diameter", 110.0, false, 0));
        // A repeat count, a grid's shape or another size of the same value states no diameter.
        assert!(!Callout::read("6× ⌀8 THRU").states("bore.diameter", 6.0, false, 0));
        assert!(!Callout::read("4× ⌀8 THRU (6×2)").states("bore.diameter", 6.0, false, 0));
        assert!(!Callout::read("⌀8 ↧ 6").states("bore.diameter", 6.0, false, 0));
        // A plain dimension states its one number.
        assert!(Callout::read("10.0").states("bore.depth", 10.0, false, 0));
        assert!(!Callout::read("10 12").states("bore.depth", 10.0, false, 0));
    }

    /// quiddity-rust#118: outer counterbore steps are printed after the counterbore, innermost
    /// first, each its own `⌴` segment; `outer_steps.{i}` counts outermost first.
    #[test]
    fn outer_steps_are_read_from_their_own_segments() {
        let text = "⌀6 THRU ⌴ ⌀12 ↧ 3 ⌴ ⌀20 ↧ 2 ⌴ ⌀25 ↧ 1.5 ⌴SF ⌀30 ↧ 0.5";
        let term = |parameter: &str| Callout::read(text).term(parameter, true, 2).map(|(v, _)| v);
        assert_eq!(term("counterbore.diameter"), Some(12.0));
        assert_eq!(term("counterbore.depth"), Some(3.0));
        assert_eq!(term("outer_steps.0.diameter"), Some(25.0));
        assert_eq!(term("outer_steps.0.depth"), Some(1.5));
        assert_eq!(term("outer_steps.1.diameter"), Some(20.0));
        assert_eq!(term("outer_steps.1.depth"), Some(2.0));
        assert_eq!(term("outer_steps.2.diameter"), None);
        assert_eq!(term("spotface.diameter"), Some(30.0));
        // draftwright's form, the spotface with the counterbore's `⌴`: after the outer steps.
        let plain = Callout::read("⌀6 THRU ⌴ ⌀12 ↧ 3 ⌴ ⌀20 ↧ 2 ⌴ ⌀30 ↧ 0.5");
        assert_eq!(
            plain.term("spotface.diameter", true, 1).map(|t| t.0),
            Some(30.0)
        );
        assert_eq!(
            plain.term("outer_steps.0.diameter", true, 1).map(|t| t.0),
            Some(20.0)
        );
    }
}
