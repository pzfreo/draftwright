//! Plate coverage (ISSUE-1373-a, LINT-plate_coverage-*): every recognised slab has
//! one thickness requirement. Alternative owners count only when their ink states
//! their measurements; a scalar elsewhere on the sheet is not evidence.

use serde_json::Value;

use super::envelope::PARAMETERS;
use super::parameters::{self, Wanted};
use super::{LintIssue, Mark, holes};
use crate::bridge::Feature;
use crate::bridge::holes::{dir_to_part, frame, point, principal, to_part};
use crate::model::Analysis;

const AXES: [&str; 3] = ["x", "y", "z"];
// Plate intervals are published to three decimals, while the envelope is unrounded.
const NEAR: f64 = 1e-3;

#[derive(Clone, Debug)]
pub struct Plate {
    pub axis: usize,
    pub span: [[f64; 3]; 2],
}

impl Plate {
    pub fn thickness(&self) -> f64 {
        self.span[1][self.axis] - self.span[0][self.axis]
    }
}

pub fn read(a: &Analysis, f: &Feature) -> Result<Plate, String> {
    let p = &f.parameters;
    let local = AXES
        .iter()
        .position(|axis| p.get("axis").and_then(Value::as_str) == Some(axis))
        .ok_or("no thin axis")?;
    let n = |name: &str| {
        p.get(name)
            .and_then(Value::as_f64)
            .filter(|v| v.is_finite())
            .ok_or_else(|| format!("no finite {name}"))
    };
    let (lo, hi, u, v) = (n("lo")?, n("hi")?, n("u")?, n("v")?);
    if hi <= lo {
        return Err("the thickness interval is not positive".into());
    }
    let across: Vec<_> = (0..3).filter(|k| *k != local).collect();
    let at = |t| {
        let mut q = [0.0; 3];
        q[local] = t;
        q[across[0]] = u;
        q[across[1]] = v;
        to_part(&frame(a), q)
    };
    let mut unit = [0.0; 3];
    unit[local] = 1.0;
    let axis =
        principal(dir_to_part(&frame(a), unit)).ok_or("the thin axis lies along no part axis")?;
    let mut span = [at(lo), at(hi)];
    if span[0][axis] > span[1][axis] {
        span.swap(0, 1);
    }
    Ok(Plate { axis, span })
}

fn near(a: f64, b: f64) -> bool {
    (a - b).abs() <= NEAR
}
fn between(t: f64, lo: f64, hi: f64) -> bool {
    t >= lo - NEAR && t <= hi + NEAR
}

fn body(f: &Feature) -> Option<&Value> {
    f.parameters
        .get("body_key")
        .filter(|key| !key.is_null())
        .or_else(|| {
            f.parameters
                .get("profile")?
                .get("body_key")
                .filter(|key| !key.is_null())
        })
}

fn same_body(a: &Analysis, f: &Feature, other: &Feature) -> bool {
    match (body(f), body(other)) {
        (Some(left), Some(right)) => left == right,
        _ => a.principal_profiles.len() == 1,
    }
}

/// Intrinsic exclusions need physical support, never only equal scalar dimensions.
/// A single solid proves membership for a polygonal support. With several solids,
/// matching provider body keys are required; absent proof keeps the requirement.
pub struct Exclusion {
    pub by: String,
    pub reason: &'static str,
}

pub fn exclusion(a: &Analysis, f: &Feature) -> Option<Exclusion> {
    let p = read(a, f).ok()?;
    let k = p.axis;
    let (lo, hi) = (p.span[0][k], p.span[1][k]);
    let same_body = |other: &Feature| {
        let key = f.parameters.get("body_key");
        key.is_some() && key == other.parameters.get("body_key")
    };
    for boss in a.features.iter().filter(|b| b.kind == "polygonal_boss") {
        if !self::same_body(a, f, boss) {
            continue;
        }
        let q = &boss.parameters;
        let axis = parameters::direction(&frame(a), q.get("axis")).and_then(principal);
        if axis != Some(k) {
            continue;
        }
        let centre = point(q.get("center"))?;
        let ends = [q.get("base")?.as_f64()?, q.get("top")?.as_f64()?];
        let local = AXES
            .iter()
            .position(|s| q.get("axis").and_then(Value::as_str) == Some(s))?;
        let at = |t| {
            let mut c = centre;
            c[local] = t;
            to_part(&frame(a), c)[k]
        };
        let (b0, b1) = (at(ends[0]).min(at(ends[1])), at(ends[0]).max(at(ends[1])));
        if !((near(lo, a.envelope[0][k]) && near(hi, b0) && near(b1, a.envelope[1][k]))
            || (near(b0, a.envelope[0][k]) && near(b1, lo) && near(hi, a.envelope[1][k])))
        {
            continue;
        }
        let sides = q.get("side_count").and_then(Value::as_u64)? as usize;
        let centres = q.get("flat_centres")?.as_array()?;
        let directions = q.get("flat_directions")?.as_array()?;
        if sides < 4
            || !sides.is_multiple_of(2)
            || centres.len() != sides
            || directions.len() != sides
        {
            continue;
        }
        if directions.iter().enumerate().any(|(i, d)| {
            let Some(d) = point(Some(d)) else {
                return true;
            };
            (d.iter().map(|v| v * v).sum::<f64>() - 1.0).abs() > 1e-6
                || d[local].abs() > 1e-6
                || directions[..i]
                    .iter()
                    .any(|other| point(Some(other)) == Some(d))
        }) {
            continue;
        }
        if centres.iter().zip(directions).all(|(c, d)| {
            let Some(c) = point(Some(c)).map(|c| to_part(&frame(a), c)) else {
                return false;
            };
            let Some(d) = point(Some(d)).map(|d| dir_to_part(&frame(a), d)) else {
                return false;
            };
            (0..3).map(|j| (p.span[0][j] - c[j]) * d[j]).sum::<f64>() <= NEAR
        }) {
            return Some(Exclusion {
                by: format!("d:{}/boss_height.length", boss.id),
                reason: "polygonal_boss_owns_supporting_slab",
            });
        }
    }
    for pattern in a.features.iter().filter(|p| p.kind == "slot_pattern") {
        let ids = pattern.parameters.get("slots")?.as_array()?;
        if ids
            .iter()
            .filter_map(Value::as_str)
            .collect::<std::collections::BTreeSet<_>>()
            .len()
            != ids.len()
        {
            continue;
        }
        let members: Vec<_> = ids
            .iter()
            .filter_map(|id| {
                a.features
                    .iter()
                    .find(|s| Some(s.id.as_str()) == id.as_str() && s.kind == "slot")
            })
            .collect();
        if members.len() != ids.len() || members.len() < 2 {
            continue;
        }
        let mut cuts = Vec::new();
        let mut bounds = None;
        for s in members {
            if !same_body(s) {
                cuts.clear();
                break;
            }
            let q = &s.parameters;
            let local = AXES
                .iter()
                .position(|axis| q.get("width_axis").and_then(Value::as_str) == Some(axis))?;
            let run = AXES
                .iter()
                .position(|axis| q.get("long_axis").and_then(Value::as_str) == Some(axis))?;
            if local == run {
                cuts.clear();
                break;
            }
            let depth = 3 - local - run;
            let mut unit = [0.0; 3];
            unit[local] = 1.0;
            if principal(dir_to_part(&frame(a), unit)) != Some(k) {
                cuts.clear();
                break;
            }
            // Compare in provider coordinates, including the body's scope and cut support.
            let fp = &f.parameters;
            let plocal = AXES
                .iter()
                .position(|axis| fp.get("axis").and_then(Value::as_str) == Some(axis))?;
            let other: Vec<_> = (0..3).filter(|j| *j != plocal).collect();
            let mut witness = [0.0; 3];
            witness[other[0]] = fp.get("u")?.as_f64()?;
            witness[other[1]] = fp.get("v")?.as_f64()?;
            let n = |name: &str| q.get(name).and_then(Value::as_f64);
            if !between(witness[run], n("lo")?, n("hi")?)
                || !between(witness[depth], n("d_lo")?, n("d_hi")?)
            {
                continue;
            }
            let body = q.get("body_key")?.as_array()?;
            bounds = Some((body.get(local)?.as_f64()?, body.get(local + 3)?.as_f64()?));
            let centre = n("w_center")?;
            let half = n("width")? / 2.0;
            cuts.push((centre - half, centre + half));
        }
        if cuts.is_empty() {
            continue;
        }
        let Some((mut cursor, end)) = bounds else {
            continue;
        };
        cuts.sort_by(|a, b| a.0.total_cmp(&b.0));
        let fp = &f.parameters;
        let (slo, shi) = (fp.get("lo")?.as_f64()?, fp.get("hi")?.as_f64()?);
        for (start, stop) in cuts {
            if near(slo, cursor) && near(shi, start) {
                return Some(Exclusion {
                    by: format!("d:{}/slot_width.length", pattern.id),
                    reason: "slot_pattern_owns_material_web",
                });
            }
            cursor = cursor.max(stop);
        }
        if near(slo, cursor) && near(shi, end) {
            return Some(Exclusion {
                by: format!("d:{}/slot_width.length", pattern.id),
                reason: "slot_pattern_owns_material_web",
            });
        }
    }
    None
}

#[derive(Clone, Debug)]
pub struct Term {
    pub feature: String,
    pub parameter: String,
    pub value: f64,
}
impl Term {
    fn new(feature: &str, parameter: &str, value: f64) -> Self {
        Self {
            feature: feature.into(),
            parameter: parameter.into(),
            value,
        }
    }
    pub fn stated(&self, marks: &[Mark]) -> bool {
        marks.iter().any(|m| {
            !m.name.starts_with("suppressed:")
                && m.claims
                    .iter()
                    .any(|c| c.feature == self.feature && c.parameter == self.parameter)
                && if self.parameter == "thickness.length" {
                    holes::states(nominal(&m.text), self.value)
                } else {
                    holes::states(&m.text, self.value)
                }
        })
    }
}

/// Proven alternatives: an overall interval, a through-step leg, a ladder interval,
/// or the opposite wall of a full-span U-channel (lower wall + opening + overall).
pub fn alternatives(a: &Analysis, f: &Feature) -> Vec<Vec<Term>> {
    let Ok(p) = read(a, f) else {
        return Vec::new();
    };
    let k = p.axis;
    let (lo, hi) = (p.span[0][k], p.span[1][k]);
    let mut out = Vec::new();
    let overall = || {
        Term::new(
            "envelope",
            PARAMETERS[k],
            a.envelope[1][k] - a.envelope[0][k],
        )
    };
    if near(lo, a.envelope[0][k]) && near(hi, a.envelope[1][k]) {
        out.push(vec![overall()]);
    }
    for s in a.features.iter().filter(|s| s.kind == "turned_step") {
        if !same_body(a, f, s) {
            continue;
        }
        if let Ok(b) = super::turned::band(&frame(a), s)
            && b.axis == k
            && near(lo, b.lo)
            && near(hi, b.hi)
            && (0..3).all(|j| j == k || near(p.span[0][j], b.on[j]))
        {
            out.push(vec![Term::new(&s.id, "step.length", b.hi - b.lo)]);
        }
    }
    // A turned plate-like slab may span several bands (GRM-03's 0.5 + 2).
    // Same-body and axis-line evidence are required; equal sums alone prove nothing.
    let bands: Vec<_> = a
        .features
        .iter()
        .filter(|s| s.kind == "turned_step" && same_body(a, f, s))
        .filter_map(|s| super::turned::band(&frame(a), s).ok().map(|b| (s, b)))
        .filter(|(_, b)| b.axis == k && (0..3).all(|j| j == k || near(p.span[0][j], b.on[j])))
        .collect();
    let links: Vec<_> = bands
        .iter()
        .enumerate()
        .map(|(i, (_, b))| (super::redundancy::Span::new(k, b.lo, b.hi), i))
        .collect();
    if let Some(route) = super::redundancy::path(&links, super::redundancy::Span::new(k, lo, hi)) {
        out.push(
            route
                .iter()
                .map(|i| {
                    let (s, b) = &bands[*i];
                    Term::new(&s.id, "step.length", b.hi - b.lo)
                })
                .collect(),
        );
    }
    let mut step_intervals = Vec::new();
    for s in a.features.iter().filter(|s| s.kind == "through_step") {
        if !same_body(a, f, s) {
            continue;
        }
        let q = &s.parameters;
        let Some(run) = q
            .get("axis")
            .and_then(Value::as_str)
            .and_then(|r| AXES.iter().position(|x| *x == r))
        else {
            continue;
        };
        let Some(at) = point(q.get("at")) else {
            continue;
        };
        let across: Vec<_> = (0..3).filter(|j| *j != run).collect();
        let Some(section) = q.get("section").and_then(Value::as_array) else {
            continue;
        };
        let ends: Option<Vec<_>> = section
            .iter()
            .map(|q| {
                let q = q.as_array()?;
                let mut c = at;
                c[across[0]] = q.first()?.as_f64()?;
                c[across[1]] = q.get(1)?.as_f64()?;
                Some(to_part(&frame(a), c))
            })
            .collect();
        let Some(ends) = ends else {
            continue;
        };
        for ends in ends.windows(2) {
            if (0..3).all(|j| j == k || near(ends[0][j], ends[1][j])) {
                let (start, stop) = (ends[0][k].min(ends[1][k]), ends[0][k].max(ends[1][k]));
                let term = Term::new(
                    &s.id,
                    &format!("through_step_leg.length.{}", AXES[k]),
                    stop - start,
                );
                if near(lo, start)
                    && near(hi, stop)
                    && s.faces.iter().any(|face| f.faces.contains(face))
                {
                    out.push(vec![term.clone()]);
                }
                step_intervals.push((start, stop, term));
            }
        }
    }

    // The aggregate ladder has no body key. Its intervals cannot be borrowed
    // across disconnected solids, or when an older Analysis lacks that proof.
    let support = a.principal_profiles.len() == 1
        && a.level_supports.iter().any(|s| {
            (k == 0 || between(p.span[0][0], s.x_span[0], s.x_span[1]))
                && (k == 1 || between(p.span[0][1], s.y_span[0], s.y_span[1]))
        });
    if k == 2 && support {
        let rung = |z: f64| {
            if near(z, a.envelope[0][2]) {
                return vec![Vec::new()];
            }
            let mut terms: Vec<_> = step_intervals
                .iter()
                .filter(|(start, stop, _)| near(*start, a.envelope[0][2]) && near(*stop, z))
                .map(|(_, _, term)| vec![term.clone()])
                .collect();
            if a.levels.iter().any(|level| near(*level, z)) {
                let h = z - a.envelope[0][2];
                terms.push(vec![Term::new(
                    "step_level",
                    &crate::dims::levels::height_parameter(a, z),
                    h,
                )]);
            }
            terms
        };
        for left in rung(lo) {
            for right in rung(hi) {
                let mut terms = left.clone();
                terms.extend(right);
                if !terms.is_empty() {
                    out.push(terms);
                }
            }
        }
    }
    // A shoulder is a riser's own position on the plate's face, so the riser is the support
    // evidence across x or y; one connected profile keeps it on the plate's body.
    if k < 2 && a.principal_profiles.len() == 1 {
        let positions: Vec<_> = a
            .risers
            .iter()
            .flat_map(|r| {
                let first = if r.axis == AXES[k] {
                    r.positions.clone()
                } else {
                    Vec::new()
                };
                let second = if r.other_axis == AXES[k] {
                    r.other_positions.clone()
                } else {
                    Vec::new()
                };
                first.into_iter().chain(second)
            })
            .collect();
        let position = |t: f64| {
            if near(t, a.envelope[0][k]) {
                Some(Vec::new())
            } else if near(t, a.envelope[1][k]) {
                Some(vec![overall()])
            } else if positions.iter().any(|v| near(*v, t)) {
                let value = t - a.envelope[0][k];
                Some(vec![Term::new(
                    "step_level",
                    &crate::dims::levels::position_parameter(a, k, t),
                    value,
                )])
            } else {
                None
            }
        };
        if let (Some(mut left), Some(right)) = (position(lo), position(hi)) {
            left.extend(right);
            if !left.is_empty() {
                out.push(left);
            }
        }
    }
    let channels = super::channels::placed(a);
    if let [(channel, Ok(c))] = channels.as_slice()
        && a.principal_profiles.len() == 1
    {
        let full_span = match super::section_recesses::grammar(channel) {
            Ok(super::section_recesses::Grammar::Channel(raw)) => {
                let at = |t| {
                    let mut q = raw.origin;
                    q[raw.axis] = t;
                    to_part(&frame(a), q)[c.run_axis]
                };
                near(at(raw.lo).min(at(raw.hi)), a.envelope[0][c.run_axis])
                    && near(at(raw.lo).max(at(raw.hi)), a.envelope[1][c.run_axis])
            }
            _ => false,
        };
        if full_span && c.width_axis == k && near(lo, c.walls[1]) && near(hi, a.envelope[1][k]) {
            let upper_at = p.span[0];
            let lower: Vec<_> = a
                .features
                .iter()
                .filter(|s| s.kind == "plate" && s.id != f.id)
                .filter_map(|s| {
                    let p = read(a, s).ok()?;
                    (p.axis == k
                        && near(p.span[0][k], a.envelope[0][k])
                        && near(p.span[1][k], c.walls[0])
                        && (0..3).all(|j| j == k || near(p.span[0][j], upper_at[j])))
                    .then_some((s, p))
                })
                .collect();
            if let [(s, p)] = lower.as_slice() {
                out.push(vec![
                    Term::new(&s.id, "thickness.length", p.thickness()),
                    Term::new(&channel.id, "channel_width.length", c.width),
                    overall(),
                ]);
            }
        }
    }
    out
}

fn nominal(text: &str) -> &str {
    let text = text.trim();
    let text = text
        .split_once('×')
        .filter(|(count, _)| count.trim().parse::<usize>().is_ok())
        .map_or(text, |(_, value)| value.trim());
    let end = text
        .find(|c: char| !c.is_ascii_digit() && c != '.')
        .unwrap_or(text.len());
    &text[..end]
}

pub fn coverage(a: &Analysis, marks: &[Mark]) -> Vec<LintIssue> {
    let mut out = Vec::new();
    // A proven same-body band chain also works when its lengths are independently
    // determined by printed baseline stations. A missing local label is not a
    // missing physical interval; the turned linter verifies both actual endpoints.
    let turned = std::cell::LazyCell::new(|| {
        let printed = if marks.iter().any(|m| m.name.starts_with("suppressed:")) {
            std::borrow::Cow::Owned(
                marks
                    .iter()
                    .filter(|m| !m.name.starts_with("suppressed:"))
                    .cloned()
                    .collect::<Vec<_>>(),
            )
        } else {
            std::borrow::Cow::Borrowed(marks)
        };
        super::turned::outcomes(a, &printed)
    });
    for f in a.features.iter().filter(|f| f.kind == "plate") {
        if exclusion(a, f).is_some()
            || alternatives(a, f).iter().any(|terms| {
                terms.iter().all(|t| {
                    t.stated(marks)
                        || (t.parameter == "step.length"
                            && turned.iter().any(|o| {
                                o.feature == t.feature
                                    && o.parameter == t.parameter
                                    && o.state == super::turned::State::Placed
                            }))
                })
            })
        {
            continue;
        }
        let value = read(a, f).map(|p| vec![p.thickness()]);
        let w = Wanted {
            feature: f,
            parameter: "thickness.length".into(),
            callout: value
                .as_ref()
                .map_or_else(|_| "thickness".into(), |v| parameters::fmt(v[0])),
            values: value,
        };
        let shown: Vec<_> = marks
            .iter()
            .filter(|m| !m.name.starts_with("suppressed:"))
            .map(|m| {
                let mut m = m.clone();
                m.text = nominal(&m.text).into();
                m
            })
            .collect();
        let mut issues = parameters::coverage(
            "plate",
            &[w],
            &shown,
            |f| format!("plate {}", f.id),
            "a thickness dimension in an edge-on view",
        );
        let suppressed = marks.iter().any(|m| {
            m.name.starts_with("suppressed:")
                && m.claims
                    .iter()
                    .any(|c| c.feature == f.id && c.parameter == "thickness.length")
        });
        if suppressed {
            for i in &mut issues {
                i.code = "plate_requirement_suppressed".into();
                i.id = format!("l:{}:{}/thickness.length", i.code, f.id);
                i.message = format!(
                    "plate {}: thickness.length was deliberately omitted by the authored dimension set",
                    f.id
                );
            }
        }
        out.extend(issues);
    }
    out
}
