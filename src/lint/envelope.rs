//! Envelope completeness (Python's `linting/envelope_coverage.py`): each non-zero overall extent
//! of the part, its width (x), depth (y) and height (z), is stated on the drawing
//! (LINT-envelope_coverage-overall_extents).
//!
//! Python uses this ledger only in its quality report. The port also reports missing axes as
//! `missing_principal_dimension`, so an omitted extent cannot buy silence.
//!
//! The denominator is the part's box (the Analysis's `envelope`), not any recognised feature. An
//! axis is **placed** when the marks prove its whole interval (within 1e-3 mm), by:
//!
//! - one measurement reaching from end to end: an `envelope` extent (`width.length`, …), or any
//!   other claim whose physical span, as lint reads it (`redundancy::span`: a face level, a
//!   step position, a plate thickness, a slot length, a boss height), is the axis's whole
//!   interval (stream K5);
//! - a turned profile's diameter across the axis, centred on it: a turned step's or a boss's
//!   diameter, or the part's overall diameter (`envelope`/`od.diameter`, the largest turned
//!   band's, which Python's `rotational` OD stands for);
//! - or a chain of turned step lengths along the axis, on one axis line, that partitions it
//!   exactly (an overlap or a gap breaks the chain).
//!
//! Each counts only when its mark's text states its value ([`holes::states`]). Otherwise the axis
//! is **missing**. Not ported: measurements whose span lint does not read (a hole location, a
//! through step's leg) and step lengths chained by a declared profile group; such an axis is
//! reported missing here. `suppressed`, `dropped` and
//! `satisfied_by_structured_note` are not reached: lint cannot see the author's omissions or the
//! placement drops.

use serde::Serialize;

use super::turned::{self, State};
use super::{Mark, holes};
use crate::bridge::Feature;
use crate::bridge::holes::{frame, point, principal, to_part};
use crate::model::Analysis;

/// The overall extents' requirements, by the part's axis.
pub const PARAMETERS: [&str; 3] = ["width.length", "depth.length", "height.length"];

const TOL: f64 = 1e-3;

/// One overall extent's outcome.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Outcome {
    /// `x`, `y` or `z`.
    pub axis: &'static str,
    pub parameter: &'static str,
    pub extent: f64,
    /// Placed or missing.
    pub state: State,
    /// The annotations whose measurements prove it.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub annotations: Vec<String>,
}

/// What one stated measurement proves along one of the part's axes: an interval, and the axis
/// line of the turned step whose length it is, for chaining.
#[derive(Clone, Debug, PartialEq)]
struct Carrier {
    axis: usize,
    lo: f64,
    hi: f64,
    chain: Option<[i64; 3]>,
    mark: String,
}

/// The part's overall diameter: the turning axis, a point on it and the largest diameter, when
/// every readable turned step shares one axis line.
pub(super) fn turning(
    analysis: &Analysis,
    frame: &[[f64; 3]; 4],
) -> Option<(usize, [f64; 3], f64)> {
    if let Some(body) = crate::bridge::turned_body(analysis) {
        return Some(body);
    }
    let mut found: Option<(usize, [f64; 3], f64)> = None;
    for f in analysis.features.iter().filter(|f| f.kind == "turned_step") {
        let b = turned::band(frame, f).ok()?;
        match &mut found {
            None => found = Some((b.axis, b.on, b.diameter)),
            Some((k, on, d)) => {
                if *k != b.axis || (0..3).any(|j| j != *k && (on[j] - b.on[j]).abs() > TOL) {
                    return None;
                }
                *d = d.max(b.diameter);
            }
        }
    }
    found
}

/// The intervals across the axis *k* of a circle of diameter *d* centred on the line through
/// *on*: one on each other axis.
fn across(k: usize, on: [f64; 3], d: f64, mark: &str) -> Vec<Carrier> {
    (0..3)
        .filter(|&j| j != k)
        .map(|j| Carrier {
            axis: j,
            lo: on[j] - d / 2.0,
            hi: on[j] + d / 2.0,
            chain: None,
            mark: mark.into(),
        })
        .collect()
}

/// A boss's axis (the part's) and a point on it, with its diameter.
fn boss(frame: &[[f64; 3]; 4], f: &Feature) -> Option<(usize, [f64; 3], f64)> {
    let p = &f.parameters;
    let axis = super::parameters::direction(frame, p.get("axis"))?;
    let at = point(p.get("location")).map(|l| to_part(frame, l))?;
    Some((
        principal(axis)?,
        at,
        super::parameters::size(f, "diameter").ok()?,
    ))
}

/// What a whole-part polygonal stock's stated length or size across flats proves (ARCH0015-4:
/// the stock's statements stand in place of the overall extents). Its length runs along its
/// axis from base to top. Its size across flats fixes its regular cross-section, which the end
/// view draws in its true orientation, so it proves each extent across the axis that the
/// section's corners reach, whatever the bar's roll about its axis; it counts only when the
/// record's ring reaches the part's box there, within the record's three-decimal rounding.
/// Nothing is proved for a record that breaks quiddity's stock contract.
fn stock(
    analysis: &Analysis,
    frame: &[[f64; 3]; 4],
    f: &Feature,
    parameter: &str,
    mark: &str,
    states: impl Fn(f64) -> bool,
) -> Vec<Carrier> {
    let Some(values) = super::polygonal::stock_wanted(analysis)
        .into_iter()
        .find(|w| w.feature.id == f.id && w.parameter == parameter)
        .and_then(|w| w.values.ok())
    else {
        return Vec::new();
    };
    let p = &f.parameters;
    let number = |name: &str| p.get(name).and_then(|v| v.as_f64());
    let (Some(q), Some(centre), Some(base), Some(top)) = (
        p.get("axis")
            .and_then(|v| v.as_str())
            .and_then(|a| ["x", "y", "z"].iter().position(|x| *x == a)),
        point(p.get("center")),
        number("base"),
        number("top"),
    ) else {
        return Vec::new();
    };
    let mut unit = [0.0; 3];
    unit[q] = 1.0;
    let Some(k) = principal(crate::bridge::holes::dir_to_part(frame, unit)) else {
        return Vec::new();
    };
    let at = |l: [f64; 3]| to_part(frame, l);
    if parameter == "stock_length.length" {
        let (mut b, mut t) = (centre, centre);
        (b[q], t[q]) = (base, top);
        let (lo, hi) = (at(b)[k], at(t)[k]);
        return if values.iter().any(|&v| states(v)) {
            vec![Carrier {
                axis: k,
                lo: lo.min(hi),
                hi: lo.max(hi),
                chain: None,
                mark: mark.into(),
            }]
        } else {
            Vec::new()
        };
    }
    let Some(&af) = values.first() else {
        return Vec::new();
    };
    if !states(af) {
        return Vec::new();
    }
    // The section's corners: adjacent flats' lines, at half the size across flats from the
    // centre, meet there (the contract has checked that they do, in ring order).
    let (i, j) = match q {
        0 => (1, 2),
        1 => (0, 2),
        _ => (0, 1),
    };
    let Some(normals) = p
        .get("flat_directions")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| point(Some(v)))
                .map(|d| {
                    let l = d[i].hypot(d[j]);
                    [d[i] / l, d[j] / l]
                })
                .collect::<Vec<_>>()
        })
    else {
        return Vec::new();
    };
    let n = normals.len();
    let half = af / 2.0;
    let corners: Vec<[f64; 3]> = (0..n)
        .map(|m| {
            let (a, o) = (normals[m], normals[(m + 1) % n]);
            let det = a[0] * o[1] - a[1] * o[0];
            let mut c = centre;
            c[i] += half * (o[1] - a[1]) / det;
            c[j] += half * (a[0] - o[0]) / det;
            at(c)
        })
        .collect();
    let [lo, hi] = analysis.envelope;
    let tol = 1e-3 * af;
    (0..3)
        .filter(|&a| a != k)
        .filter_map(|a| {
            let low = corners.iter().map(|c| c[a]).fold(f64::INFINITY, f64::min);
            let high = corners
                .iter()
                .map(|c| c[a])
                .fold(f64::NEG_INFINITY, f64::max);
            ((low - lo[a]).abs() <= tol && (high - hi[a]).abs() <= tol).then(|| Carrier {
                axis: a,
                lo: lo[a],
                hi: hi[a],
                chain: None,
                mark: mark.into(),
            })
        })
        .collect()
}

/// What each mark's stated claims prove along the part's axes.
fn carriers(analysis: &Analysis, marks: &[Mark]) -> Vec<Carrier> {
    let frame = frame(analysis);
    let [lo, hi] = analysis.envelope;
    let mut out = Vec::new();
    for m in marks {
        for c in &m.claims {
            let states = |v: f64| holes::states(&m.text, v);
            if c.feature == "envelope" {
                if let Some(k) = PARAMETERS.iter().position(|p| *p == c.parameter) {
                    if states(hi[k] - lo[k]) {
                        out.push(Carrier {
                            axis: k,
                            lo: lo[k],
                            hi: hi[k],
                            chain: None,
                            mark: m.name.clone(),
                        });
                    }
                } else if c.parameter == "od.diameter"
                    && let Some((k, on, d)) = turning(analysis, &frame)
                    && states(d)
                {
                    out.extend(across(k, on, d, &m.name));
                }
                continue;
            }
            // Any other measurement whose physical span (lint's own reading of the claim,
            // `redundancy::span`: a face level, a plate thickness, a slot length, a boss height,
            // …) runs along a principal axis carries that interval (ISSUE-1560-a; Python's
            // "one measurement spanning the full bbox interval").
            if let Some(s) = super::redundancy::span(analysis, &c.feature, &c.parameter)
                && !(c.feature.starts_with("f:turned_step:") && c.parameter == "step.length")
                && states(s.hi - s.lo)
            {
                out.push(Carrier {
                    axis: s.axis,
                    lo: s.lo.min(s.hi),
                    hi: s.lo.max(s.hi),
                    chain: None,
                    mark: m.name.clone(),
                });
            }
            let Some(f) = analysis.features.iter().find(|f| f.id == c.feature) else {
                continue;
            };
            // Independent physical evidence: lint does not call the spring drawing planner.
            if f.kind == "helical_spring"
                && let Some(axis) = point(f.parameters.get("axis").and_then(|a| a.get("direction")))
                    .and_then(haecceity::geom::unit)
                && let Some(k) = principal(axis)
                && let Some(on) = point(f.parameters.get("axis").and_then(|a| a.get("point")))
            {
                let number = |name: &str| f.parameters.get(name).and_then(|v| v.as_f64());
                if c.parameter == "outer_diameter"
                    && let Some(outer) = number("outer_diameter")
                    && states(outer)
                {
                    out.extend(across(k, on, outer, &m.name));
                } else if c.parameter == "free_length"
                    && let (Some(start), Some(end)) = (number("start"), number("end"))
                    && states(end - start)
                {
                    let ends = [on[k] + axis[k] * start, on[k] + axis[k] * end];
                    out.push(Carrier {
                        axis: k,
                        lo: ends[0].min(ends[1]),
                        hi: ends[0].max(ends[1]),
                        chain: None,
                        mark: m.name.clone(),
                    });
                }
            }
            match (f.kind.as_str(), c.parameter.as_str()) {
                ("turned_step", "step.length") => {
                    if let Ok(b) = turned::band(&frame, f)
                        && states(b.hi - b.lo)
                    {
                        let mut line = b.on.map(|x| (x / TOL).round() as i64);
                        line[b.axis] = 0;
                        out.push(Carrier {
                            axis: b.axis,
                            lo: b.lo,
                            hi: b.hi,
                            chain: Some(line),
                            mark: m.name.clone(),
                        });
                    }
                }
                ("turned_step", "step.diameter") => {
                    if let Ok(b) = turned::band(&frame, f)
                        && states(b.diameter)
                    {
                        out.extend(across(b.axis, b.on, b.diameter, &m.name));
                    }
                }
                ("polygonal_stock", "stock_length.length" | "polygon_across_flats.length") => {
                    out.extend(stock(analysis, &frame, f, &c.parameter, &m.name, states));
                }
                ("boss", "boss.diameter") => {
                    if let Some((k, on, d)) = boss(&frame, f)
                        && states(d)
                    {
                        out.extend(across(k, on, d, &m.name));
                    }
                }
                _ => {}
            }
        }
    }
    out
}

/// The marks that prove axis *k*'s interval `lo..hi`, if any do: one carrier end to end, else a
/// chain of turned step lengths on one axis line that partitions it.
fn proof(carriers: &[Carrier], k: usize, lo: f64, hi: f64) -> Option<Vec<String>> {
    let on: Vec<&Carrier> = carriers.iter().filter(|c| c.axis == k).collect();
    if let Some(c) = on
        .iter()
        .find(|c| (c.lo - lo).abs() <= TOL && (c.hi - hi).abs() <= TOL)
    {
        return Some(vec![c.mark.clone()]);
    }
    let mut chains: Vec<[i64; 3]> = on.iter().filter_map(|c| c.chain).collect();
    chains.sort();
    chains.dedup();
    for chain in chains {
        // One link per band: the same step stated twice is one link.
        let mut links: Vec<&Carrier> = on
            .iter()
            .copied()
            .filter(|c| c.chain == Some(chain))
            .collect();
        // Invariant: Carriers are built from finite parsed dimensions and geometric intervals.
        links.sort_by(|a, b| (a.lo, a.hi).partial_cmp(&(b.lo, b.hi)).unwrap());
        links.dedup_by(|a, b| a.lo == b.lo && a.hi == b.hi);
        if (links[0].lo - lo).abs() > TOL {
            continue;
        }
        let mut cursor = links[0].hi;
        let mut whole = true;
        for l in &links[1..] {
            if (l.lo - cursor).abs() > TOL {
                whole = false;
                break;
            }
            cursor = l.hi;
        }
        if whole && (cursor - hi).abs() <= TOL {
            return Some(links.iter().map(|l| l.mark.clone()).collect());
        }
    }
    None
}

/// The envelope ledger: each non-zero axis of the part's box, placed or missing.
pub fn outcomes(analysis: &Analysis, marks: &[Mark]) -> Vec<Outcome> {
    let carriers = carriers(analysis, marks);
    let [lo, hi] = analysis.envelope;
    (0..3)
        .filter(|&k| hi[k] - lo[k] > TOL)
        .map(|k| {
            let found = proof(&carriers, k, lo[k], hi[k]);
            Outcome {
                axis: ["x", "y", "z"][k],
                parameter: PARAMETERS[k],
                extent: hi[k] - lo[k],
                state: if found.is_some() {
                    State::Placed
                } else {
                    State::Missing
                },
                annotations: found.unwrap_or_default(),
            }
        })
        .collect()
}

/// Missing overall extents, independently judged from the drawing's marks.
pub fn coverage(analysis: &Analysis, marks: &[Mark]) -> Vec<super::LintIssue> {
    outcomes(analysis, marks).into_iter().filter(|o| o.state == State::Missing).map(|o| {
        super::uncovered(analysis, "missing_principal_dimension", "envelope", o.parameter,
            format!("The overall {} extent ({}) is not stated by a full span, centred diameter or contiguous turned-length chain.", o.axis, super::parameters::fmt(o.extent)))
    }).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::output::Claim;
    use serde_json::{Value, json};

    /// A plate 28.8 × 32.3 × 44.7 (m10127's box) with a boss ø8.4 along y, or string_post's
    /// turned profile (its x the part's −z).
    fn part(envelope: [[f64; 3]; 2], frame: Value, features: &[(&str, Value)]) -> Analysis {
        let features: Vec<Value> = features
            .iter()
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
        serde_json::from_value(json!({
            "schema": "draftwright.analysis/2",
            "binding": { "source_sha256": "", "part": "test", "face_count": 4, "loader": "test" },
            "envelope": envelope,
            "features": features,
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
            "recognition": { "recogniser": "test", "frame": frame },
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
            .map(|o| format!("{} {:?} {:?}", o.axis, o.state, o.annotations))
            .collect()
    }

    /// Each extent is placed by a stated `envelope` extent; misprinted or absent, it is missing.
    #[test]
    fn each_overall_extent_is_stated() {
        let identity = json!([
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0]
        ]);
        let a = part([[0.0, 0.0, 0.0], [28.794, 32.268, 44.729]], identity, &[]);
        let marks = [
            mark("m_env_width", "28.8", "envelope", "width.length"),
            mark("m_env_depth", "32.4", "envelope", "depth.length"),
        ];
        assert_eq!(
            states(&a, &marks),
            ["x Placed [\"m_env_width\"]", "y Missing []", "z Missing []"]
        );
    }

    /// A turned part's length is placed by its chain of step lengths (a gap breaks it), and its
    /// two transverse extents by a centred diameter: a step's, or the part's overall one.
    #[test]
    fn a_turned_parts_extents_are_its_chain_and_its_diameter() {
        let profile = json!({ "axis": "x", "axis_origin": [0.0, 0.0, 0.0] });
        let step = |lo: f64, hi: f64, d: f64| json!({ "axis": "x", "lo": lo, "hi": hi, "diameter": d, "profile": profile.clone() });
        let frame = json!([
            [0.0, 0.0, 10.004],
            [0.0, 0.0, -1.0],
            [-1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0]
        ]);
        let a = part(
            [[-3.75, -3.75, 0.0], [3.75, 3.75, 14.8]],
            frame,
            &[
                ("turned_step", step(-4.796, -3.796, 7.5)),
                ("turned_step", step(-3.796, 1.704, 6.0)),
                ("turned_step", step(1.704, 2.804, 5.0)),
                ("turned_step", step(2.804, 10.004, 3.4)),
            ],
        );
        let lengths = [
            mark("m_steplen0", "7.2", "f:turned_step:4", "step.length"),
            mark("m_steplen1", "1.1", "f:turned_step:3", "step.length"),
            mark("m_steplen2", "5.5", "f:turned_step:2", "step.length"),
            mark("m_steplen3", "1", "f:turned_step:1", "step.length"),
        ];
        let mut marks = lengths.to_vec();
        marks.push(mark("dim_od", "ø7.5", "envelope", "od.diameter"));
        assert_eq!(
            states(&a, &marks),
            [
                "x Placed [\"dim_od\"]",
                "y Placed [\"dim_od\"]",
                "z Placed [\"m_steplen0\", \"m_steplen1\", \"m_steplen2\", \"m_steplen3\"]"
            ]
        );
        let mut gap = lengths[..3].to_vec();
        gap.push(mark("m_dia_z3", "ø7.5", "f:turned_step:1", "step.diameter"));
        assert_eq!(
            states(&a, &gap),
            [
                "x Placed [\"m_dia_z3\"]",
                "y Placed [\"m_dia_z3\"]",
                "z Missing []"
            ]
        );
    }

    /// ISSUE-1560-a, stream K5: any other measurement whose span runs end to end carries the
    /// extent. On m10127's box a boss along +z (its location its free end) as tall as the part
    /// (its stated height spans 0..44.7) places z; one standing 2 mm higher, whose height spans
    /// 2..44.7, does not, and a misprinted height places nothing.
    #[test]
    fn any_measurement_spanning_the_box_states_the_extent() {
        let identity = json!([
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0]
        ]);
        let boss = |z: f64, h: f64| {
            json!({ "axis": [0.0, 0.0, 1.0], "location": [14.0, 16.0, z], "diameter": 8.4,
                    "height": h })
        };
        let envelope = [[0.0, 0.0, 0.0], [28.794, 32.268, 44.729]];
        let a = part(
            envelope,
            identity.clone(),
            &[("boss", boss(44.729, 44.729))],
        );
        let height = [mark("m_boss_h", "44.7", "f:boss:1", "boss_height.length")];
        assert_eq!(
            states(&a, &height),
            ["x Missing []", "y Missing []", "z Placed [\"m_boss_h\"]"]
        );
        let wrong = [mark("m_boss_h", "44", "f:boss:1", "boss_height.length")];
        assert_eq!(states(&a, &wrong)[2], "z Missing []");
        let short = part(envelope, identity, &[("boss", boss(44.729, 42.729))]);
        let height = [mark("m_boss_h", "42.7", "f:boss:1", "boss_height.length")];
        assert_eq!(states(&short, &height)[2], "z Missing []");
    }
}
