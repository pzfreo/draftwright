//! Turned-step completeness (Python's `linting/turned_step_coverage.py`): each outside-diameter
//! band of a turned profile states its axial length and its diameter
//! (LINT-turned_step_coverage-length_and_od).
//!
//! Python uses this ledger in its quality report. The port also reports each uncovered band
//! requirement, retaining `axial_length_missing` for missing lengths.
//!
//! Each of the Analysis's `turned_step` features (quiddity's `TurnedStep`: its `axis`, the band's
//! `lo` and `hi` along it, its `diameter`, and its `profile`: the turned body's `axis_origin` and
//! bounds) that no groove owns has two requirements, `step.length` and `step.diameter`:
//!
//! - **placed**: an annotation claims the step's parameter and its text states the value (the
//!   length is `hi − lo`); the diameter is placed too when the part's overall diameter
//!   (`envelope`/`od.diameter`, which Python's `rotational` OD stands for) is claimed and stated,
//!   and the band is the unique largest of its profile and the only band of its axis line and
//!   diameter; a length is placed too when a baseline from an end of its profile locates both
//!   its shoulders (`step_position.length` claims, #33);
//! - **unverifiable**: an annotation claims it but its text does not state the value, the record
//!   cannot be read, or groove ownership is ambiguous (then every band is unverifiable, as in
//!   Python, since ownership changes the count);
//! - **missing**: nothing claims it.
//!
//! A groove owns a band (draftwright's `groove_owns_turned_step_band`) when it lies on the
//! band's axis, its station is within the band (0.1 mm), and the band is no longer than the
//! groove's width plus 1 mm; the groove must belong to exactly one turned profile: the profile
//! its record names, whose axis line it lies on (within 0.0005 mm) and whose shoulders contain
//! its width. `suppressed`, `dropped` and `satisfied_by_structured_note` are not reached: lint
//! cannot see the author's omissions or the placement drops.

use serde::Serialize;
use serde_json::Value;

use super::parameters::{self, fmt};
use super::{Mark, holes};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, frame, point, principal, to_part};
use crate::model::Analysis;

const AXES: [&str; 3] = ["x", "y", "z"];

/// A requirement's outcome in a completeness ledger.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum State {
    Placed,
    Missing,
    Unverifiable,
}

/// One requirement of one turned band, and how the marks meet it.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Outcome {
    pub feature: String,
    /// `step.length` or `step.diameter`.
    pub parameter: String,
    pub state: State,
    /// The annotation that states it, or that claims it without stating it.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub annotation: Option<String>,
    /// Why it is unverifiable.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub evidence: Option<String>,
}

/// A turned step's band in the part's frame.
#[derive(Clone, Debug, PartialEq)]
pub struct Band {
    /// The part's axis the band turns about.
    pub axis: usize,
    /// A point on its axis line.
    pub on: [f64; 3],
    /// Its ends along the axis, `lo < hi`.
    pub lo: f64,
    pub hi: f64,
    pub diameter: f64,
}

/// A turned step's record axis (its index in quiddity's frame), `lo`, `hi`, diameter and
/// `profile.axis_origin`, or why they cannot be read.
fn record(f: &Feature) -> Result<(usize, f64, f64, f64, [f64; 3]), String> {
    let p = &f.parameters;
    let axis = p
        .get("axis")
        .and_then(Value::as_str)
        .and_then(|a| AXES.iter().position(|x| *x == a))
        .ok_or("its record gives no principal axis")?;
    let (lo, hi) = (parameters::size(f, "lo")?, parameters::size(f, "hi")?);
    if hi <= lo {
        return Err("its band has no length".into());
    }
    let diameter = parameters::size(f, "diameter")?;
    if diameter <= 0.0 {
        return Err("its diameter is not positive".into());
    }
    let origin = point(p.get("profile").and_then(|v| v.get("axis_origin")))
        .ok_or("its record gives no profile axis origin")?;
    Ok((axis, lo, hi, diameter, origin))
}

/// A turned step's band in the part's frame, or why it cannot be read.
pub fn band(frame: &[[f64; 3]; 4], f: &Feature) -> Result<Band, String> {
    let (a, lo, hi, diameter, origin) = record(f)?;
    let mut d = [0.0; 3];
    d[a] = 1.0;
    let k = principal(dir_to_part(frame, d)).ok_or("its axis lies along no axis of the part")?;
    let at = |t: f64| {
        let mut q = origin;
        q[a] = t;
        to_part(frame, q)
    };
    let (p, q) = (at(lo), at(hi));
    Ok(Band {
        axis: k,
        on: p,
        lo: p[k].min(q[k]),
        hi: p[k].max(q[k]),
        diameter,
    })
}

/// A flat that cuts a band's cylinder: its outward unit direction square to the band's axis and
/// its distance from the axis, in the part's frame, and its span along the axis.
#[derive(Clone, Debug, PartialEq)]
pub struct Cut {
    pub normal: [f64; 3],
    pub distance: f64,
    pub lo: f64,
    pub hi: f64,
}

/// The Analysis's `flat` features that cut band *b*: about its own axis line (the flat's foot
/// lies its `axis_distance` from the band's axis), inside its radius, over part of its span.
pub fn cuts(analysis: &Analysis, b: &Band) -> Vec<Cut> {
    flat_cuts(analysis, b).into_iter().map(|(_, c)| c).collect()
}

/// [`cuts`], each with the flat that makes it.
pub fn flat_cuts<'a>(analysis: &'a Analysis, b: &Band) -> Vec<(&'a Feature, Cut)> {
    let q = frame(analysis);
    let k = b.axis;
    let r = 0.5 * b.diameter;
    let mut out = Vec::new();
    for f in analysis.features.iter().filter(|f| f.kind == "flat") {
        let p = &f.parameters;
        let (Some(at), Some(dir), Some(distance), Some(span)) = (
            point(p.get("at")),
            point(p.get("axis_direction")),
            p.get("axis_distance").and_then(Value::as_f64),
            p.get("stock_span").and_then(Value::as_array),
        ) else {
            continue;
        };
        let [Some(s0), Some(s1)] = [0, 1].map(|i| span.get(i).and_then(Value::as_f64)) else {
            continue;
        };
        if principal(dir_to_part(&q, dir)) != Some(k)
            || !distance.is_finite()
            || distance >= r - 1e-6
        {
            continue;
        }
        let foot = to_part(&q, at);
        let mut v = [0.0; 3];
        for j in (0..3).filter(|&j| j != k) {
            v[j] = foot[j] - b.on[j];
        }
        let n = (v[0] * v[0] + v[1] * v[1] + v[2] * v[2]).sqrt();
        if n < 1e-9 || (n - distance).abs() > 1e-3 * distance.max(1.0) {
            continue;
        }
        // The span's ends: the foot moved along the flat's axis to each recorded station.
        let along = at[0] * dir[0] + at[1] * dir[1] + at[2] * dir[2];
        let [e0, e1] =
            [s0, s1].map(|s| to_part(&q, [0, 1, 2].map(|i| at[i] + (s - along) * dir[i]))[k]);
        let (lo, hi) = (e0.min(e1).max(b.lo), e0.max(e1).min(b.hi));
        if hi - lo > 1e-6 {
            out.push((
                f,
                Cut {
                    normal: v.map(|x| x / n),
                    distance: n,
                    lo,
                    hi,
                },
            ));
        }
    }
    out
}

fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn dot3(a: [f64; 3], b: [f64; 3]) -> f64 {
    a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
}

/// The point of band *b*'s cylinder at station *t*, trimmed by those of *cuts* over *t*, that lies
/// farthest along *d* (a unit direction square to its axis): a proven point of the cylinder face
/// whose projection is the band's outline in any view whose page shows *d* across the axis. None
/// when no material is left at *t*.
pub fn extreme(b: &Band, cuts: &[Cut], t: f64, d: [f64; 3]) -> Option<[f64; 3]> {
    let mut a = [0.0; 3];
    a[b.axis] = 1.0;
    let e = cross(a, d);
    let r = 0.5 * b.diameter;
    let live: Vec<(f64, f64)> = cuts
        .iter()
        .filter(|c| c.lo - 1e-9 <= t && t <= c.hi + 1e-9)
        .map(|c| (dot3(c.normal, e).atan2(dot3(c.normal, d)), c.distance))
        .collect();
    let feasible = |th: f64| {
        live.iter()
            .all(|&(phi, h)| r * (th - phi).cos() <= h + 1e-9 * r.max(1.0))
    };
    let mut best: Option<f64> = None;
    let candidates = std::iter::once(0.0).chain(live.iter().flat_map(|&(phi, h)| {
        let alpha = (h / r).clamp(-1.0, 1.0).acos();
        [phi - alpha, phi + alpha]
    }));
    for th in candidates {
        if feasible(th) && best.is_none_or(|b| th.cos() > b.cos() + 1e-12) {
            best = Some(th);
        }
    }
    let th = best?;
    let mut p = b.on;
    p[b.axis] = t;
    Some([0, 1, 2].map(|i| p[i] + r * (th.cos() * d[i] + th.sin() * e[i])))
}

/// Band *b*'s outline as a view looking along *toward* shows it, across its axis on the side of
/// *d* (`toward × axis`, or its opposite): a segment along the axis for each run of its span over
/// which the same cuts apply. Empty when the view looks along the axis.
pub fn outline(b: &Band, cuts: &[Cut], d: [f64; 3]) -> Vec<[[f64; 3]; 2]> {
    let mut stations = vec![b.lo, b.hi];
    for c in cuts {
        stations.extend([c.lo, c.hi].into_iter().filter(|s| b.lo < *s && *s < b.hi));
    }
    stations.sort_by(f64::total_cmp);
    stations.dedup_by(|x, y| (*x - *y).abs() < 1e-9);
    let mut out = Vec::new();
    for w in stations.windows(2) {
        let mid = 0.5 * (w[0] + w[1]);
        if let Some(p) = extreme(b, cuts, mid, d) {
            let (mut p0, mut p1) = (p, p);
            p0[b.axis] = w[0];
            p1[b.axis] = w[1];
            out.push([p0, p1]);
        }
    }
    out
}

/// Band *b*'s circle at station *t*, trimmed by the cuts over it, as arcs about its axis (as a
/// view looking along the axis shows its cylinder).
pub fn arcs(b: &Band, cuts: &[Cut], t: f64) -> Vec<crate::arc::ProfileArc> {
    let k = b.axis;
    let mut u = [0.0; 3];
    u[(k + 1) % 3] = 1.0;
    let mut a = [0.0; 3];
    a[k] = 1.0;
    let v = cross(a, u);
    let r = 0.5 * b.diameter;
    let live: Vec<(f64, f64)> = cuts
        .iter()
        .filter(|c| c.lo - 1e-9 <= t && t <= c.hi + 1e-9)
        .map(|c| (dot3(c.normal, v).atan2(dot3(c.normal, u)), c.distance))
        .collect();
    let mut centre = b.on;
    centre[k] = t;
    let arc = |start: f64, sweep: f64| crate::arc::ProfileArc {
        centre,
        radius: r,
        u,
        v,
        start,
        sweep,
    };
    if live.is_empty() {
        return vec![arc(0.0, 2.0 * std::f64::consts::PI)];
    }
    let tau = 2.0 * std::f64::consts::PI;
    let mut ends: Vec<f64> = live
        .iter()
        .flat_map(|&(phi, h)| {
            let alpha = (h / r).clamp(-1.0, 1.0).acos();
            [phi - alpha, phi + alpha]
        })
        .map(|x| x.rem_euclid(tau))
        .collect();
    ends.sort_by(f64::total_cmp);
    ends.dedup_by(|x, y| (*x - *y).abs() < 1e-12);
    let inside = |th: f64| live.iter().all(|&(phi, h)| r * (th - phi).cos() <= h);
    (0..ends.len())
        .filter_map(|i| {
            let (s, e) = (ends[i], ends[(i + 1) % ends.len()]);
            let sweep = if i + 1 == ends.len() {
                e + tau - s
            } else {
                e - s
            };
            (sweep > 1e-9 && inside(s + 0.5 * sweep)).then(|| arc(s, sweep))
        })
        .collect()
}

/// The band a treatment (a chamfer or fillet) turned about world axis *k*, through *at*, of size
/// *size*, turns about: the coaxial band whose span (widened by the treatment's size) contains its
/// station and whose radius reaches it, the nearest such.
pub fn coaxial(bands: &[Band], k: usize, at: [f64; 3], size: f64) -> Option<&Band> {
    let (j, m) = ((k + 1) % 3, (k + 2) % 3);
    let size = size + 1e-6;
    bands
        .iter()
        .filter(|b| b.axis == k)
        .filter_map(|b| {
            let rho = (at[j] - b.on[j]).hypot(at[m] - b.on[m]);
            let outside = (b.lo - at[k]).max(at[k] - b.hi).max(0.0);
            (outside <= size && rho <= 0.5 * b.diameter + size).then_some((
                outside,
                (0.5 * b.diameter - rho).abs(),
                b,
            ))
        })
        .min_by(|a, b| a.0.total_cmp(&b.0).then(a.1.total_cmp(&b.1)))
        .map(|(_, _, b)| b)
}

/// Every readable turned step's band, in the part's frame, groove-owned or not.
pub fn all_bands(analysis: &Analysis) -> Vec<Band> {
    let q = frame(analysis);
    analysis
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .filter_map(|f| band(&q, f).ok())
        .collect()
}

/// The turned steps that are bands of their own (no groove owns them), or why groove ownership
/// cannot be decided.
pub fn bands(analysis: &Analysis) -> Result<Vec<&Feature>, String> {
    let steps: Vec<&Feature> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .collect();
    // The turned profiles, by their records' `profile` (Python's `TurnedProfileKey`): each its
    // key and the shoulders of its readable bands.
    let mut profiles: Vec<(&Value, Vec<f64>)> = Vec::new();
    for s in &steps {
        let (Some(key), Ok((_, lo, hi, _, _))) = (s.parameters.get("profile"), record(s)) else {
            continue;
        };
        match profiles.iter_mut().find(|(k, _)| *k == key) {
            Some((_, shoulders)) => shoulders.extend([lo, hi]),
            None => profiles.push((key, vec![lo, hi])),
        }
    }
    let mut owned = vec![false; steps.len()];
    for g in analysis.features.iter().filter(|f| f.kind == "groove") {
        let p = &g.parameters;
        let axis = p
            .get("axis")
            .and_then(Value::as_str)
            .and_then(|a| AXES.iter().position(|x| *x == a));
        let (Some(a), Some(at), Ok(width)) =
            (axis, point(p.get("at")), parameters::size(g, "width"))
        else {
            return Err(format!(
                "groove {} has no readable axis, place and width",
                g.id
            ));
        };
        let named = p.get("profile");
        let candidates: Vec<&(&Value, Vec<f64>)> = profiles
            .iter()
            .filter(|(k, _)| named.is_none_or(|n| n == *k))
            .collect();
        let owners: Vec<&&(&Value, Vec<f64>)> = candidates
            .iter()
            .filter(|(k, shoulders)| {
                let same_axis = k.get("axis").and_then(Value::as_str) == Some(AXES[a]);
                let on_line = point(k.get("axis_origin")).is_some_and(|o| {
                    (0..3).all(|j| j == a || (at[j] - o[j]).abs() <= 0.0005 + 5e-9)
                });
                let lo = shoulders.iter().copied().fold(f64::INFINITY, f64::min);
                let hi = shoulders.iter().copied().fold(f64::NEG_INFINITY, f64::max);
                let tol = 1e-3 + 1e-9;
                same_axis
                    && on_line
                    && lo - tol <= at[a] - width / 2.0
                    && at[a] + width / 2.0 <= hi + tol
            })
            .collect();
        if owners.len() > 1 || (named.is_some() && !candidates.is_empty() && owners.is_empty()) {
            return Err(format!(
                "groove {} does not belong to exactly one turned profile",
                g.id
            ));
        }
        let Some((key, _)) = owners.first() else {
            continue;
        };
        for (i, s) in steps.iter().enumerate() {
            if s.parameters.get("profile") != Some(*key) {
                continue;
            }
            let Ok((sa, lo, hi, _, _)) = record(s) else {
                continue;
            };
            if sa == a && lo - 0.1 <= at[a] && at[a] <= hi + 0.1 && hi - lo <= width + 1.0 {
                owned[i] = true;
            }
        }
    }
    Ok(steps
        .into_iter()
        .zip(owned)
        .filter(|(_, o)| !o)
        .map(|(s, _)| s)
        .collect())
}

/// How the marks meet the requirement *parameter* of *id*, whose value is *value*: the first
/// claiming mark that states it, else the first that claims it.
fn judged<'a>(
    marks: &'a [Mark],
    id: &str,
    parameter: &str,
    value: f64,
) -> (State, Option<&'a Mark>) {
    let claiming: Vec<&Mark> = marks
        .iter()
        .filter(|m| {
            m.claims
                .iter()
                .any(|c| c.feature == id && c.parameter == parameter)
        })
        .collect();
    let states = |m: &&&Mark| {
        if parameter.ends_with("diameter")
            && let Some(nominal) = m
                .text
                .split_whitespace()
                .filter_map(|w| w.strip_prefix('M'))
                .find(|w| w.starts_with(|c: char| c.is_ascii_digit()))
        {
            let end = nominal
                .find(|c: char| !(c.is_ascii_digit() || c == '.'))
                .unwrap_or(nominal.len());
            return holes::states(&nominal[..end], value);
        }
        holes::states(&m.text, value)
    };
    match claiming.iter().find(states) {
        Some(m) => (State::Placed, Some(m)),
        None => match claiming.first() {
            Some(m) => (State::Unverifiable, Some(m)),
            None => (State::Missing, None),
        },
    }
}

/// The turned-step ledger: each band's length and diameter, in Analysis order.
pub fn outcomes(analysis: &Analysis, marks: &[Mark]) -> Vec<Outcome> {
    let frame = frame(analysis);
    let unverifiable = |f: &Feature, parameter: &str, why: String| Outcome {
        feature: f.id.clone(),
        parameter: parameter.into(),
        state: State::Unverifiable,
        annotation: None,
        evidence: Some(why),
    };
    let steps = match bands(analysis) {
        Ok(s) => s,
        Err(why) => {
            return analysis
                .features
                .iter()
                .filter(|f| f.kind == "turned_step")
                .flat_map(|f| {
                    ["step.length", "step.diameter"].map(|p| unverifiable(f, p, why.clone()))
                })
                .collect();
        }
    };
    let read: Vec<(&Feature, Result<Band, String>)> =
        steps.iter().map(|f| (*f, band(&frame, f))).collect();
    let mut out = Vec::new();
    for (f, b) in &read {
        let b = match b {
            Ok(b) => b,
            Err(why) => {
                for p in ["step.length", "step.diameter"] {
                    out.push(unverifiable(f, p, why.clone()));
                }
                continue;
            }
        };
        for (parameter, value) in [("step.length", b.hi - b.lo), ("step.diameter", b.diameter)] {
            let (mut state, mut mark) = judged(marks, &f.id, parameter, value);
            if parameter == "step.length"
                && state == State::Missing
                && let Some(m) = from_baseline(&read, marks, f, b)
            {
                (state, mark) = (State::Placed, Some(m));
            }
            let mut evidence = None;
            if parameter == "step.length"
                && state == State::Placed
                && let Some(m) = mark
                && let Err(why) = grouped(&read, m)
            {
                (state, evidence) = (State::Unverifiable, Some(why));
            }
            if parameter == "step.diameter" && state == State::Missing && overall(&read, f, b) {
                let (s, m) = judged(marks, "envelope", "od.diameter", b.diameter);
                if s == State::Placed {
                    (state, mark) = (s, m);
                }
            }
            out.push(Outcome {
                feature: f.id.clone(),
                parameter: parameter.into(),
                state,
                annotation: mark.map(|m| m.name.clone()),
                evidence: evidence.or_else(|| {
                    (state == State::Unverifiable)
                        // Invariant: The preceding condition selects an existing mark with differing text.
                        .then(|| format!("{:?} does not state {}", mark.unwrap().text, fmt(value)))
                }),
            });
        }
    }
    out
}

/// Whether mark *m*, when it states several bands' lengths at once (`3× 10`), states exactly its
/// members (ISSUE-1374-c, Python's `test_grouped_length_label_requires_exact_count_and_value` and
/// `test_grouped_length_cannot_skip_an_equal_adjacent_member`): its label is the count of the
/// bands it claims and one length, nothing else (`3× 999 NOTE 10` and `2× 10` for three bands
/// state none of them), and its bands lie on one axis line with no other band of theirs between
/// them, so a grouped label never skips an equal neighbour. A mark of one band passes.
fn grouped(read: &[(&Feature, Result<Band, String>)], m: &Mark) -> Result<(), String> {
    let claimed: Vec<&str> = m
        .claims
        .iter()
        .filter(|c| c.parameter == "step.length")
        .map(|c| c.feature.as_str())
        .collect();
    if claimed.len() < 2 {
        return Ok(());
    }
    let n = claimed.len();
    let exact = m
        .text
        .trim()
        .strip_prefix(&format!("{n}×"))
        .is_some_and(|rest| !rest.trim().is_empty() && !rest.chars().any(char::is_alphabetic));
    if !exact {
        return Err(format!(
            "{:?} states the lengths of {n} bands, but not as exactly {n}× one length",
            m.text
        ));
    }
    let members: Vec<(&Feature, &Band)> = read
        .iter()
        .filter(|(f, _)| claimed.contains(&f.id.as_str()))
        .filter_map(|(f, b)| Some((*f, b.as_ref().ok()?)))
        .collect();
    let (f0, b0) = members[0];
    let line = |f: &Feature, b: &Band| {
        b.axis == b0.axis
            && f.parameters.get("profile") == f0.parameters.get("profile")
            && (0..3).all(|j| j == b0.axis || (b.on[j] - b0.on[j]).abs() <= 1e-3)
    };
    if members.len() != n || !members.iter().all(|(f, b)| line(f, b)) {
        return Err(format!(
            "{:?} claims bands that are not of one turned profile",
            m.text
        ));
    }
    let lo = members
        .iter()
        .map(|(_, b)| b.lo)
        .fold(f64::INFINITY, f64::min);
    let hi = members
        .iter()
        .map(|(_, b)| b.hi)
        .fold(f64::NEG_INFINITY, f64::max);
    if let Some((skipped, _)) = read.iter().find(|(f, b)| {
        !claimed.contains(&f.id.as_str())
            && b.as_ref()
                .is_ok_and(|b| line(f, b) && b.lo >= lo - 1e-3 && b.hi <= hi + 1e-3)
    }) {
        return Err(format!(
            "{:?} skips {}, a band between the ones it claims",
            m.text, skipped.id
        ));
    }
    Ok(())
}

/// The mark that locates band *b*'s far shoulder from an end of its profile, when a baseline from
/// that end determines the band's length (#33): a mark claims the band's `step_position.length`
/// and states the far shoulder's distance from the end, and the near shoulder is the end itself
/// or is located from it the same way, or by the length of the band at the end. Lint reads only
/// the printed claims and the Analysis's bands.
fn from_baseline<'a>(
    read: &[(&Feature, Result<Band, String>)],
    marks: &'a [Mark],
    f: &Feature,
    b: &Band,
) -> Option<&'a Mark> {
    let profile = f.parameters.get("profile");
    let near = |x: f64, y: f64| (x - y).abs() <= 1e-3;
    let same: Vec<(&Feature, &Band)> = read
        .iter()
        .filter(|(g, _)| g.parameters.get("profile") == profile)
        .filter_map(|(g, c)| c.as_ref().ok().map(|c| (*g, c)))
        .filter(|(_, c)| c.axis == b.axis && (0..3).all(|j| j == c.axis || near(c.on[j], b.on[j])))
        .collect();
    let lo = same.iter().map(|(_, c)| c.lo).fold(f64::INFINITY, f64::min);
    let hi = same
        .iter()
        .map(|(_, c)| c.hi)
        .fold(f64::NEG_INFINITY, f64::max);
    // The mark stating band *c*'s far shoulder from *end*.
    let position = |c: &Feature, distance: f64| {
        marks.iter().find(|m| {
            m.claims
                .iter()
                .any(|k| k.feature == c.id && k.parameter == "step_position.length")
                && holes::states(&m.text, distance)
        })
    };
    [lo, hi].into_iter().find_map(|end| {
        let (near_end, far_end) = if near(end, lo) {
            (b.lo, b.hi)
        } else {
            (b.hi, b.lo)
        };
        let m = position(f, (far_end - end).abs())?;
        let located = near(near_end, end)
            || same.iter().any(|(g, c)| {
                let (cn, cf) = if near(end, lo) {
                    (c.lo, c.hi)
                } else {
                    (c.hi, c.lo)
                };
                near(cf, near_end)
                    && if near(cn, end) {
                        judged(marks, &g.id, "step.length", c.hi - c.lo).0 == State::Placed
                    } else {
                        position(g, (cf - end).abs()).is_some()
                    }
            });
        located.then_some(m)
    })
}

/// Whether band *b* of step *f* is the one the part's overall diameter can stand for: the unique
/// largest band of its profile, and the only band on its axis line with its diameter.
fn overall(read: &[(&Feature, Result<Band, String>)], f: &Feature, b: &Band) -> bool {
    let profile = f.parameters.get("profile");
    let same_line = |c: &Band| {
        c.axis == b.axis && (0..3).all(|j| j == b.axis || (c.on[j] - b.on[j]).abs() <= 1e-6)
    };
    let mut in_profile = read
        .iter()
        .filter(|(g, _)| g.parameters.get("profile") == profile)
        .filter_map(|(_, c)| c.as_ref().ok());
    let unique_max = in_profile.clone().all(|c| c.diameter <= b.diameter)
        && in_profile
            .clone()
            .filter(|c| c.diameter == b.diameter)
            .count()
            == 1
        && in_profile.next().is_some();
    let alone = read
        .iter()
        .filter_map(|(_, c)| c.as_ref().ok())
        .filter(|c| same_line(c) && c.diameter == b.diameter)
        .count()
        == 1;
    unique_max && alone
}

/// Band requirements the printed drawing does not prove.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<super::LintIssue> {
    outcomes(analysis, marks)
        .into_iter()
        .filter(|o| o.state != State::Placed)
        .map(|o| {
            let code = if o.state == State::Unverifiable {
                "turned_step_requirement_unverifiable"
            } else if o.parameter == "step.length" {
                "axial_length_missing"
            } else {
                "turned_step_requirement_missing"
            };
            let mut issue = super::uncovered(
                analysis,
                code,
                &o.feature,
                &o.parameter,
                format!(
                    "{}: {} is {} on the drawing.",
                    o.feature,
                    o.parameter,
                    if o.state == State::Missing {
                        "missing"
                    } else {
                        "unverifiable"
                    }
                ),
            );
            issue.annotation = o.annotation;
            issue.evidence = o.evidence;
            issue
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::Claim;
    use serde_json::json;

    /// string_post's turned profile (quiddity 0.3.9), with connector109's first groove moved
    /// onto its ø6 band: three bands of their own, each length and diameter judged.
    fn part(groove: Option<Value>) -> Analysis {
        let profile = json!({ "axis": "x", "axis_origin": [0.0, 9.46e-06, 2.81e-06],
                              "body_bounds": [-4.79568699, 10.00431301, -3.74999064, 3.75000956,
                                              -3.74999729, 3.75000291] });
        let step = |lo: f64, hi: f64, d: f64| json!({ "axis": "x", "lo": lo, "hi": hi, "diameter": d, "profile": profile.clone() });
        let mut features = vec![
            ("turned_step", step(-4.796, -3.796, 7.5)),
            ("turned_step", step(-3.796, 1.704, 6.0)),
            ("turned_step", step(1.704, 2.804, 5.0)),
        ];
        if let Some(mut g) = groove {
            g["profile"] = profile.clone();
            features.push(("groove", g));
        }
        let features: Vec<Value> = features
            .into_iter()
            .enumerate()
            .map(|(i, (kind, r))| {
                json!({
                    "id": format!("f:{kind}:{}", i + 1), "kind": kind, "faces": [i],
                    "parameters": r,
                    "fingerprint": { "version": "test", "family": "test", "subtype": "test",
                                     "sizes": {}, "position": [0.0, 0.0, 0.0],
                                     "neighbourhood": [] },
                })
            })
            .collect();
        // quiddity's frame for string_post: its x is the part's -z.
        serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": 4, "loader": "test" },
            "envelope": [[-3.75, -3.75, 0.0], [3.75, 3.75, 14.8]],
            "features": features,
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
            "recognition": { "recogniser": "test",
                             "frame": [[0.0, 0.0, 10.004], [0.0, 0.0, -1.0], [-1.0, 0.0, 0.0],
                                       [0.0, 1.0, 0.0]] },
        }))
        .unwrap()
    }

    fn mark(name: &str, text: &str, feature: &str, parameter: &str) -> Mark {
        Mark {
            name: name.into(),
            view: Some("v:front".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: feature.into(),
                parameter: parameter.into(),
            }],
        }
    }

    fn states(a: &Analysis, marks: &[Mark]) -> Vec<String> {
        outcomes(a, marks)
            .into_iter()
            .map(|o| format!("{} {} {:?}", o.feature, o.parameter, o.state))
            .collect()
    }

    #[test]
    fn a_thread_designation_states_its_nominal_not_its_tolerance_class() {
        let correct = mark("thread", "M6-6g", "f:turned_step:2", "step.diameter");
        assert_eq!(
            judged(&[correct], "f:turned_step:2", "step.diameter", 6.0).0,
            State::Placed
        );
        let wrong = mark("thread", "M5-6g", "f:turned_step:2", "step.diameter");
        assert_eq!(
            judged(&[wrong], "f:turned_step:2", "step.diameter", 6.0).0,
            State::Unverifiable
        );
    }

    /// Each band's length and diameter are placed when a claiming annotation prints them,
    /// unverifiable when it misprints them, and missing when nothing claims them. The part's
    /// overall diameter stands for its largest band's.
    #[test]
    fn a_turned_band_states_its_length_and_diameter() {
        let a = part(None);
        let b = band(&frame(&a), &a.features[0]).unwrap();
        assert_eq!(b.axis, 2);
        assert!((b.lo - 13.8).abs() < 1e-9 && (b.hi - 14.8).abs() < 1e-9);
        let marks = [
            mark("m_steplen0", "1", "f:turned_step:1", "step.length"),
            mark("m_steplen1", "5.5", "f:turned_step:2", "step.length"),
            mark("m_steplen2", "1.2", "f:turned_step:3", "step.length"),
            mark("m_dia_z1", "ø6", "f:turned_step:2", "step.diameter"),
            mark("dim_od", "ø7.5", "envelope", "od.diameter"),
        ];
        assert_eq!(
            states(&a, &marks),
            [
                "f:turned_step:1 step.length Placed",
                "f:turned_step:1 step.diameter Placed",
                "f:turned_step:2 step.length Placed",
                "f:turned_step:2 step.diameter Placed",
                "f:turned_step:3 step.length Unverifiable",
                "f:turned_step:3 step.diameter Missing",
            ]
        );
        assert_eq!(
            outcomes(&a, &marks)[4].evidence.as_deref(),
            Some("\"1.2\" does not state 1.1")
        );
    }

    /// A groove on a band owns it: the band is the groove's (its width and floor diameter), not
    /// a turned step's. A groove no profile can own leaves every band unverifiable.
    #[test]
    fn a_band_a_groove_owns_is_the_grooves() {
        let groove = json!({ "axis": "x", "at": [2.254, 0.0, 0.0], "width": 1.1,
                             "diameter": 5.0 });
        let a = part(Some(groove.clone()));
        let ids: Vec<&str> = bands(&a).unwrap().iter().map(|f| f.id.as_str()).collect();
        assert_eq!(ids, ["f:turned_step:1", "f:turned_step:2"]);
        let mut astray = groove;
        astray["at"] = json!([2.254, 1.0, 0.0]);
        let a = part(Some(astray));
        assert!(states(&a, &[]).iter().all(|s| s.ends_with("Unverifiable")));
    }
}
