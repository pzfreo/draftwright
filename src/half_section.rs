//! Half sections (ISO 128-50 half view, half section): a part turned whole whose inside a
//! section shows more cleanly than hidden lines (a coaxial hole with a counterbore, spotface or
//! countersink, a bore that changes diameter along the axis, an internal groove, or a through
//! bore under an outer groove, as on a bush or linear bearing) has its profile view drawn half as
//! seen and half cut, split on the turning axis, so the internal steps and walls read as section
//! edges rather than overlapping hidden lines. A plain bore under a plain or merely stepped
//! outline stays a plain view: its hidden lines already read cleanly.
//!
//! The cut half reuses the full section's projection ([`crate::model::Cut`] through
//! `hlr::project_section`) and hatching; the boundary between the halves is the view's own axis
//! centre line, and neither half draws hidden edges. The dimensions of the view follow the cut:
//! a witness that lands on an internal edge of the half drawn as seen moves to its mirror in the
//! cut half, and an internal diameter becomes a half dimension (ISO 129-1: one arrow on the cut
//! half's edge, the dimension line running a little past the axis, no witness on the other half).
//!
//! The choice is automatic and steerable: `views/<name>/half_section` true forces it on a
//! profile view of a turned part, false forbids it.

use serde::{Deserialize, Serialize};

use crate::bridge::holes;
use crate::lint::turned::{Band, band};
use crate::model::{Analysis, Cut, Plan, PlannedView, Problem};

/// Page coordinates beyond any sheet, bounding a half plane as a rectangle.
const FAR: f64 = 1.0e6;
/// How far a half dimension's line runs past the axis, page mm: one arrowhead.
const PAST_AXIS: f64 = crate::dims::ARROW;
/// Clearance kept between a half dimension's open end and its label, page mm.
const LABEL_CLEAR: f64 = 0.5;

/// A half section's cut: the plane through the turning axis square to the view, keeping the
/// material behind it; the half across the axis on the side `keep` points to is drawn cut.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct HalfSection {
    /// A point on the turning axis.
    pub origin: [f64; 3],
    /// The world axis (0, 1, 2) the part turns about.
    pub axis: usize,
    /// The unit world direction, square to the axis in the view's plane, toward the cut half.
    pub keep: [f64; 3],
}

impl HalfSection {
    /// Its cutting plane as a full section's: through the axis, the viewer's side removed.
    pub fn cut(&self, v: &PlannedView) -> Cut {
        Cut {
            xref: None,
            label: String::new(),
            origin: self.origin,
            normal: v.direction.map(|d| -d),
            marked_in: String::new(),
        }
    }

    /// The axis on the page: the page coordinate (0 for x, 1 for y) that crosses it, and its
    /// value there.
    pub fn page_axis(&self, analysis: &Analysis, v: &PlannedView) -> (usize, f64) {
        let split = Split::new(analysis, v, self);
        (1 - split.along, split.at)
    }

    /// The page rectangle (beyond the sheet) of the cut half, or of the half drawn as seen.
    pub fn rect(&self, analysis: &Analysis, v: &PlannedView, cut: bool) -> [f64; 4] {
        Split::new(analysis, v, self).rect(cut)
    }
}

/// The split on the page: which page coordinate runs along the axis, where the axis lies across,
/// and which way across the cut half lies.
struct Split {
    along: usize,
    at: f64,
    sign: f64,
    /// The page coordinate along the axis of the origin, and per world mm along the axis.
    o: f64,
    per_mm: f64,
    origin: f64,
    scale: f64,
    /// The world axis the part turns about.
    axis: usize,
}

impl Split {
    fn new(analysis: &Analysis, v: &PlannedView, h: &HalfSection) -> Split {
        let page = page(analysis, v);
        let o = page(h.origin);
        let mut step = h.origin;
        step[h.axis] += 1.0;
        let a = page(step);
        let along = usize::from((a[1] - o[1]).abs() > (a[0] - o[0]).abs());
        let across = 1 - along;
        let k = page([0, 1, 2].map(|j| h.origin[j] + h.keep[j]));
        Split {
            along,
            at: o[across],
            sign: if k[across] >= o[across] { 1.0 } else { -1.0 },
            o: o[along],
            per_mm: a[along] - o[along],
            origin: h.origin[h.axis],
            scale: v.scale,
            axis: h.axis,
        }
    }

    fn rect(&self, cut: bool) -> [f64; 4] {
        let high = (self.sign > 0.0) == cut;
        let mut r = [-FAR, -FAR, FAR, FAR];
        let across = 1 - self.along;
        if high {
            r[across] = self.at;
        } else {
            r[across + 2] = self.at;
        }
        r
    }

    fn in_cut(&self, p: [f64; 2]) -> bool {
        (p[1 - self.along] - self.at) * self.sign > 0.0
    }

    /// A page point as the station along the axis and the radius from it, world mm.
    fn polar(&self, p: [f64; 2]) -> (f64, f64) {
        (
            self.origin + (p[self.along] - self.o) / self.per_mm,
            (p[1 - self.along] - self.at).abs() / self.scale,
        )
    }

    fn mirror(&self, mut p: [f64; 2]) -> [f64; 2] {
        let across = 1 - self.along;
        p[across] = 2.0 * self.at - p[across];
        p
    }
}

/// World to page in view *v*, as its geometry is placed.
fn page(analysis: &Analysis, v: &PlannedView) -> impl Fn([f64; 3]) -> [f64; 2] {
    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
    let view = haecceity::hlr::View::new(v.direction, v.up).expect("a planned view");
    let centre = crate::detail::crop_extent(analysis, v).1;
    let (at, s) = (v.at, v.scale);
    move |p| {
        let q = view.map(p);
        [
            at[0] + s * (q[0] - centre[0]),
            at[1] + s * (q[1] - centre[1]),
        ]
    }
}

/// A part turned whole: its axis, a point on it, and its turned bands (every turned step shares
/// one axis line and the largest spans the part's box across it, as the turned dimensions read
/// it).
struct Turned {
    axis: usize,
    on: [f64; 3],
    bands: Vec<Band>,
}

fn turned(analysis: &Analysis) -> Option<Turned> {
    let frame = holes::frame(analysis);
    let mut bands = Vec::new();
    for f in analysis.features.iter().filter(|f| f.kind == "turned_step") {
        let b = band(&frame, f).ok()?;
        if let Some(first) = bands.first() {
            let first: &Band = first;
            if first.axis != b.axis
                || (0..3).any(|j| j != b.axis && (first.on[j] - b.on[j]).abs() > 1e-3)
            {
                return None;
            }
        }
        bands.push(b);
    }
    if let Some((axis, on, _)) = crate::bridge::turned_body(analysis) {
        return Some(Turned { axis, on, bands });
    }
    let first = bands.first()?;
    let (axis, on) = (first.axis, first.on);
    let diameter = bands.iter().map(|b| b.diameter).fold(0.0, f64::max);
    let [lo, hi] = analysis.envelope;
    (0..3)
        .filter(|&j| j != axis)
        .all(|j| ((hi[j] - lo[j]) - diameter).abs() < 1e-3 * diameter.max(1.0))
        .then_some(Turned { axis, on, bands })
}

impl Turned {
    /// Why a half section shows what hidden lines cannot show cleanly, if it does: a hole along
    /// the axis steps inside (a counterbore, spotface or countersink), the bore changes diameter
    /// along the axis, a groove is turned inside the material (an internal groove), or a through
    /// bore runs under an outer groove (a bush or bearing, whose thin wall at the groove only a
    /// section shows). A plain bore under a plain or merely stepped outline is left to hidden
    /// lines.
    fn inside(&self, analysis: &Analysis) -> Option<&'static str> {
        let bores: Vec<holes::Hole> = holes::read(analysis)
            .into_iter()
            .filter_map(|(_, h)| h.ok())
            .filter(|h| {
                h.principal.map(|(k, _)| k) == Some(self.axis)
                    && (0..3).all(|j| j == self.axis || (h.at[j] - self.on[j]).abs() < 1e-3)
            })
            .collect();
        if bores
            .iter()
            .any(|h| h.counterbore.is_some() || h.spotface.is_some() || h.countersink.is_some())
        {
            return Some("a hole along the axis steps inside");
        }
        if bores
            .iter()
            .any(|h| (h.diameter - bores[0].diameter).abs() > 1e-3)
        {
            return Some("its bore changes diameter along the axis");
        }
        let frame = holes::frame(analysis);
        let grooves: Vec<(f64, f64)> = analysis
            .features
            .iter()
            .filter(|f| f.kind == "groove")
            .filter_map(|f| {
                let p = &f.parameters;
                let at = holes::to_part(&frame, holes::point(p.get("at"))?);
                let letter = p.get("axis")?.as_str()?;
                let mut d = [0.0; 3];
                d[["x", "y", "z"].iter().position(|x| *x == letter)?] = 1.0;
                let k = holes::principal(holes::dir_to_part(&frame, d))?;
                let diameter = p.get("diameter")?.as_f64()?;
                (k == self.axis
                    && (0..3).all(|j| j == self.axis || (at[j] - self.on[j]).abs() < 1e-3))
                .then_some((at[k], diameter))
            })
            .collect();
        // An internal groove opens off a bore: without a narrower coaxial bore, a groove floor
        // under the profile is an outer groove the profile does not record as a step.
        if grooves.iter().any(|&(t, diameter)| {
            self.outer(t) > 0.5 * diameter + 1e-3
                && bores.iter().any(|h| h.diameter < diameter - 1e-3)
        }) {
            return Some("a groove is turned inside it");
        }
        (!grooves.is_empty() && bores.iter().any(|h| h.through))
            .then_some("its through bore runs under a groove, whose wall only a section shows")
    }

    /// The outer radius of the turned profile at station *t* along the axis (0 off the part).
    fn outer(&self, t: f64) -> f64 {
        self.bands
            .iter()
            .filter(|b| b.lo - 1e-6 <= t && t <= b.hi + 1e-6)
            .map(|b| 0.5 * b.diameter)
            .fold(0.0, f64::max)
    }

    /// Whether an edge point at station *t*, radius *r* is inside the profile on both sides of
    /// its station, so the half drawn as seen hides it.
    fn internal(&self, t: f64, r: f64) -> bool {
        [t - 1e-3, t + 1e-3]
            .into_iter()
            .all(|u| self.outer(u) > r + 1e-3)
    }
}

fn spec_name(v: &PlannedView) -> &str {
    v.id.strip_prefix("v:").unwrap_or(&v.id)
}

/// Draws the plan's half section, when its part has one: on the profile view the Spec asks for
/// (`views/<name>/half_section: true`), or automatically on the profile view carrying most
/// dimensions of a part turned whole whose inside it shows ([`Turned::inside`]), when the plan has no full section
/// and the Spec does not forbid it. Idempotent: a plan that has one is left as it is.
pub(crate) fn apply(analysis: &Analysis, plan: &mut Plan) {
    if plan.views.iter().any(|v| v.half_section.is_some()) {
        return;
    }
    let asked = |v: &PlannedView| {
        plan.spec
            .views
            .get(spec_name(v))
            .and_then(|s| s.half_section)
    };
    let turned = turned(analysis);
    let profile = |v: &PlannedView| {
        v.detail.is_none()
            && v.cut.is_none()
            && v.id != "v:iso"
            && turned
                .as_ref()
                .is_some_and(|t| v.direction[t.axis].abs() < 1e-9)
    };
    let mut problems = Vec::new();
    let mut chosen: Vec<usize> = Vec::new();
    for (i, v) in plan.views.iter().enumerate() {
        if asked(v) != Some(true) {
            continue;
        }
        if profile(v) {
            chosen.push(i);
        } else {
            problems.push(Problem {
                id: format!("p:section_not_shown:{}", v.id),
                code: "section_not_shown".into(),
                severity: "warning".into(),
                message: format!(
                    "The half section of {} is not drawn: the spec asks for it, but a half section needs a part turned whole about one axis (a turned profile recognition gives) seen side on, and {} shows none; remove views/{}/half_section.",
                    crate::wording::view(&v.id),
                    crate::wording::view(&v.id),
                    spec_name(v)
                ),
                refs: vec![v.id.clone()],
                fix: vec![crate::items::Op::remove(format!(
                    "views/{}/half_section",
                    spec_name(v)
                ))],
                ..Default::default()
            });
        }
    }
    let automatic = (chosen.is_empty() && !plan.views.iter().any(|v| v.cut.is_some()))
        .then(|| turned.as_ref().and_then(|t| t.inside(analysis)))
        .flatten();
    if automatic.is_some() {
        let carried = |v: &PlannedView| plan.dimensions.iter().filter(|d| d.view == v.id).count();
        let rank = |(i, v): &(usize, &PlannedView)| (carried(v), std::cmp::Reverse(*i));
        if plan.views.iter().any(|v| v.broken.is_some())
            && let Some((_, v)) = plan
                .views
                .iter()
                .enumerate()
                .filter(|(_, v)| profile(v) && asked(v) != Some(false))
                .max_by_key(rank)
                .filter(|(_, v)| v.broken.is_some())
        {
            let b = v.broken.as_ref().unwrap();
            problems.push(Problem { id: format!("p:section_not_shown:break:{}", v.id), code: "section_not_shown".into(), severity: "warning".into(), subjects: vec![v.id.clone()], message: format!("Displaced automatic half section of {}: {} needs an uncut profile. Set views/{}/half_section to true to displace the break, or false to suppress this automatic choice.", v.id, b.source, spec_name(v)), ..Default::default() });
        }
        chosen.extend(
            plan.views
                .iter()
                .enumerate()
                .filter(|(_, v)| profile(v) && v.broken.is_none() && asked(v) != Some(false))
                .max_by_key(rank)
                .map(|(i, _)| i),
        );
    }
    for problem in problems {
        if !plan.problems.iter().any(|q| q.id == problem.id) {
            plan.problems.push(problem);
        }
    }
    let Some(t) = turned else { return };
    for i in chosen {
        let half = choose(analysis, plan, &plan.views[i], &t);
        let v = &mut plan.views[i];
        v.basis.push_str(&match automatic {
            Some(why) => format!(
                "; drawn as a half section (ISO 128-50), half as seen and half cut on the turning axis, because {why}"
            ),
            None => "; drawn as a half section (ISO 128-50), half as seen and half cut on the turning axis, as the spec asks".into(),
        });
        v.half_section = Some(half);
        drop_cut_away_axes(analysis, &mut plan.views[i]);
        let v = plan.views[i].clone();
        follow(analysis, plan, &v, &t);
    }
}

/// The centre lines of holes the half section cuts away (wholly in front of its plane, on its
/// cut half) go with them, as a full section's do.
fn drop_cut_away_axes(analysis: &Analysis, v: &mut PlannedView) {
    let Some(h) = v.half_section.clone() else {
        return;
    };
    let split = Split::new(analysis, v, &h);
    let n = h.cut(v).normal;
    let dot = |a: [f64; 3], b: [f64; 3]| (0..3).map(|k| a[k] * b[k]).sum::<f64>();
    let gone: Vec<String> = holes::read(analysis)
        .into_iter()
        .filter_map(|(_, r)| r.ok())
        .filter(|r| {
            let radius = 0.5 * r.opening_diameter();
            dot(r.direction, n).abs() < 1e-6
                && dot([0, 1, 2].map(|k| r.at[k] - h.origin[k]), n) < -radius - 1e-6
        })
        .map(|r| format!("ink:{}:{}", v.id, r.feature.id))
        .collect();
    v.line_work.retain(|w| {
        !(gone.contains(&w.id)
            && crate::centrelines::segments(w)
                .iter()
                .flatten()
                .all(|&p| split.in_cut(p)))
    });
}

/// The half section of view *v*: cut on the side of the axis its lengths are dimensioned on
/// (their witnesses then reach the cut edges shortest), the upper or right half when none are.
fn choose(analysis: &Analysis, plan: &Plan, v: &PlannedView, t: &Turned) -> HalfSection {
    // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
    let view = haecceity::hlr::View::new(v.direction, v.up).expect("a planned view");
    let mut half = HalfSection {
        origin: t.on,
        axis: t.axis,
        keep: [0.0; 3],
    };
    // A provisional keep square to the axis; only the page geometry along the axis is read.
    let across_world = |u: [f64; 2]| [0, 1, 2].map(|j| view.x[j] * u[0] + view.y[j] * u[1]);
    half.keep = across_world([0.0, 1.0]);
    if half.keep[t.axis].abs() > 0.5 {
        half.keep = across_world([1.0, 0.0]);
    }
    let split = Split::new(analysis, v, &half);
    let across = 1 - split.along;
    let side: f64 = plan
        .dimensions
        .iter()
        .filter(|d| d.view == v.id && d.kind == "linear")
        .filter_map(|d| crate::output::linear_ink(&d.placed.as_ref()?.lines))
        .filter(|l| l.k == split.along)
        .map(|l| (l.offset - split.at).signum())
        .sum();
    let mut u = [0.0; 2];
    u[across] = if side < 0.0 { -1.0 } else { 1.0 };
    half.keep = across_world(u);
    half
}

/// The dimensions of view *v* follow its half section: witnesses on internal edges of the half
/// drawn as seen move to their mirror in the cut half, and internal diameters become half
/// dimensions.
fn follow(analysis: &Analysis, plan: &mut Plan, v: &PlannedView, t: &Turned) {
    // Invariant: the view was given its half section just before.
    let split = Split::new(
        analysis,
        v,
        v.half_section.as_ref().expect("a half section"),
    );
    let across = 1 - split.along;
    let hidden = |p: [f64; 2]| {
        let (s, r) = split.polar(p);
        !split.in_cut(p) && t.internal(s, r)
    };
    for d in plan
        .dimensions
        .iter_mut()
        .filter(|d| d.view == v.id && d.kind == "linear")
    {
        let Some(p) = d.placed.as_mut() else { continue };
        if p.lines.len() != 3 || p.arrows.len() != 2 {
            continue;
        }
        let Some(l) = crate::output::linear_ink(&p.lines) else {
            continue;
        };
        // Each witness's end on the part: the one farther from the dimension line.
        let foot = |e: &[[f64; 2]; 2]| {
            usize::from((e[1][1 - l.k] - l.offset).abs() > (e[0][1 - l.k] - l.offset).abs())
        };
        if l.k == split.along {
            for e in 0..2 {
                let f = foot(&p.lines[e]);
                if hidden(p.lines[e][f]) {
                    p.lines[e][f] = split.mirror(p.lines[e][f]);
                }
            }
            continue;
        }
        let feet = [0, 1].map(|e| p.lines[e][foot(&p.lines[e])]);
        let (Some(seen), Some(cut)) = (
            (0..2).find(|&e| !split.in_cut(feet[e])),
            (0..2).find(|&e| split.in_cut(feet[e])),
        ) else {
            continue;
        };
        if !hidden(feet[seen]) {
            continue;
        }
        let station = feet[cut][across];
        let toward = -split.sign;
        // The line runs past the axis, and on past the arrow far enough to carry its label,
        // which moves along it to its middle beyond the arrow.
        let label = p.label_box[across + 2] - p.label_box[across];
        let run = ((split.at - station) * toward + PAST_AXIS)
            .max(crate::dims::ARROW + label + 2.0 * LABEL_CLEAR);
        let end = station + toward * run;
        let middle = station + toward * 0.5 * (run + crate::dims::ARROW);
        let shift = middle - 0.5 * (p.label_box[across] + p.label_box[across + 2]);
        p.label_box[across] += shift;
        p.label_box[across + 2] += shift;
        p.label_at[across] += shift;
        // The line keeps its run beyond the cut witness (an arrow outside), and ends past the axis.
        let line = p.lines[2];
        let back = [line[0][across], line[1][across]]
            .into_iter()
            .map(|x| (x - station) * toward)
            .fold(0.0, f64::min);
        let mut a = line[0];
        let mut b = line[0];
        a[across] = station + toward * back;
        b[across] = end;
        let arrow = (0..2)
            .min_by(|&i, &j| {
                (p.arrows[i][0][across] - station)
                    .abs()
                    .total_cmp(&(p.arrows[j][0][across] - station).abs())
            })
            .unwrap_or(0);
        // A repeated edge keeps the witness from the instance nearest the dimension line, so it
        // does not cross the section to the far one.
        let mut witness = p.lines[cut];
        let f = foot(&witness);
        if let Some(t) = nearest_instance(analysis, &d.measurements, &split, feet[cut], l.offset) {
            witness[f][split.along] = t;
        }
        p.lines = vec![witness, [a, b]];
        p.arrows = vec![p.arrows[arrow]];
    }
}

/// The page coordinate along the axis of a repeated edge's instance nearest *toward* (the
/// dimension line): the edge at *foot* sits at a fixed offset along its feature's own axis from
/// the nearest measured feature's location, and every measured instance has its own edge there.
/// None when the dimension measures one feature or its records give no location and axis.
fn nearest_instance(
    analysis: &Analysis,
    measurements: &[crate::dims::Measurement],
    split: &Split,
    foot: [f64; 2],
    toward: f64,
) -> Option<f64> {
    if measurements.len() < 2 {
        return None;
    }
    let frame = holes::frame(analysis);
    let placed: Vec<(f64, f64)> = measurements
        .iter()
        .map(|m| {
            let f = analysis.features.iter().find(|f| f.id == m.feature)?;
            let at = holes::to_part(&frame, holes::point(f.parameters.get("location"))?);
            let axis = holes::dir_to_part(&frame, holes::point(f.parameters.get("axis"))?);
            Some((at[split.axis], axis[split.axis]))
        })
        .collect::<Option<_>>()?;
    let (station, _) = split.polar(foot);
    let (at, dir) = *placed
        .iter()
        .min_by(|a, b| (station - a.0).abs().total_cmp(&(station - b.0).abs()))?;
    if dir.abs() < 0.5 {
        return None;
    }
    let u = (station - at) * dir;
    placed
        .iter()
        .map(|&(at, dir)| split.o + (at + u * dir - split.origin) * split.per_mm)
        .min_by(|a, b| (a - toward).abs().total_cmp(&(b - toward).abs()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Value, json};

    /// A part turned about z, ⌀15 × 24 with the given *steps* (diameter, lo, hi) over its
    /// whole length, and *extra* hole or groove records.
    fn part(steps: &[(f64, f64, f64)], extra: &[(&str, Value)]) -> Analysis {
        let profile = json!({ "axis": "z", "axis_origin": [0.0, 0.0, 0.0] });
        let features: Vec<Value> = steps
            .iter()
            .map(|&(d, lo, hi)| {
                (
                    "turned_step",
                    json!({ "axis": "z", "diameter": d, "lo": lo, "hi": hi,
                                         "profile": profile }),
                )
            })
            .chain(extra.iter().cloned())
            .enumerate()
            .map(|(i, (kind, p))| {
                json!({
                    "id": format!("f:{kind}:{}", i + 1), "kind": kind, "faces": [i],
                    "parameters": p,
                    "fingerprint": { "version": "test", "family": kind, "subtype": kind,
                                     "sizes": {}, "position": [0.0, 0.0, 0.0],
                                     "neighbourhood": [] },
                })
            })
            .collect();
        serde_json::from_value(json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding": { "source_sha256": "", "part": "test", "face_count": features.len(),
                         "loader": "test" },
            "envelope": [[-7.5, -7.5, 0.0], [7.5, 7.5, 24.0]],
            "features": features,
            "iso_bounds": [0.0, 0.0, 0.0, 0.0],
        }))
        .unwrap()
    }

    fn bore(d: f64, z: f64, depth: f64, bottom: &str) -> (&'static str, Value) {
        (
            "hole",
            json!({ "axis": [0.0, 0.0, -1.0], "location": [0.0, 0.0, z], "diameter": d,
                         "depth": depth, "bottom": bottom }),
        )
    }

    fn groove(d: f64, z: f64) -> (&'static str, Value) {
        (
            "groove",
            json!({ "axis": "z", "at": [0.0, 0.0, z], "diameter": d, "width": 1.2 }),
        )
    }

    fn why(a: &Analysis) -> Option<&'static str> {
        turned(a).unwrap().inside(a)
    }

    /// quiddity-rust#123: a body without a boss or bands still qualifies for an
    /// authored half section. Visible shallow face recesses alone do not trigger one:
    /// the end view and rim/depth/angle callout describe them cleanly.
    #[test]
    fn automatic_half_section_does_not_overlay_a_broken_profile() {
        let mut cbore = bore(5., 24., 24., "through");
        cbore.1["cbore"] = json!({"diameter":8.,"depth":3.});
        let a = part(&[(15., 0., 24.)], &[cbore]);
        assert!(
            why(&a).is_some(),
            "this fixture must normally earn a half section"
        );
        let mut p = crate::model::plan(&a, &crate::model::Spec::default()).unwrap();
        p.views.retain(|v| v.cut.is_none());
        for v in &mut p.views {
            v.half_section = None;
            v.broken = Some(crate::broken::Break {
                geometry: crate::output::BreakMap {
                    centre: [0., 12.],
                    axis: 1,
                    remove: [5., 15.],
                    gap: 2.,
                },
                source: "views/front/break".into(),
                paper_gap: 2.,
            });
        }
        apply(&a, &mut p);
        assert!(p.views.iter().all(|v| v.half_section.is_none()));
        assert!(p.problems.iter().any(|p| p.code == "section_not_shown"
            && p.message.contains("Displaced automatic half section")));
        // With equal inventories, the normal policy chooses the first profile.
        // Breaking that view displaces its half section even when another can carry it.
        let broken = p.views.iter().find_map(|v| v.broken.clone()).unwrap();
        let front = p.views.iter().find(|v| v.id == "v:front").unwrap().clone();
        let mut side = front.clone();
        side.id = "v:side".into();
        side.direction = [1., 0., 0.];
        side.up = [0., 0., 1.];
        p.views = vec![front, side];
        p.dimensions.clear();
        p.problems.clear();
        for v in &mut p.views {
            v.half_section = None;
            v.broken = (v.id == "v:front").then(|| broken.clone());
        }
        apply(&a, &mut p);
        assert!(p.problems.iter().any(|q| q.code == "section_not_shown"
            && q.subjects.contains(&"v:front".into())
            && q.message.contains("Displaced automatic half section")));
        assert!(
            p.views
                .iter()
                .any(|v| v.id == "v:side" && v.half_section.is_some())
        );
    }

    #[test]
    fn a_single_diameter_body_is_eligible_without_forcing_a_section() {
        let a = part(
            &[],
            &[
                (
                    "turned_body",
                    json!({"axis":"z", "axis_point":[0.0,0.0,12.0], "od":15.0, "length":24.0}),
                ),
                (
                    "face_groove",
                    json!({"axis":[0.0,0.0,1.0], "axis_point":[0.0,0.0,24.0], "outer_diameter":13.0, "inner_diameter":10.0, "depth":0.25, "outer_wall_angle":45.0, "inner_wall_angle":45.0}),
                ),
                bore(6.0, 24.0, 24.0, "through"),
            ],
        );
        let t = turned(&a).expect("a complete turned-body record, with no steps or boss");
        assert_eq!(t.axis, 2);
        assert_eq!(t.inside(&a), None);
    }

    /// The automatic half section's trigger: a section must show what hidden lines cannot show
    /// cleanly. A plain sleeve, or a plain bore under a stepped outline, stays a plain view; a
    /// stepped bore, a bore that changes diameter, an internal groove, or a through bore under an
    /// outer groove (a bush or linear bearing) is drawn as a half section.
    #[test]
    fn a_half_section_is_drawn_only_where_it_shows_the_inside() {
        let sleeve = [(15.0, 0.0, 24.0)];
        assert_eq!(why(&part(&sleeve, &[])), None);
        assert_eq!(
            why(&part(&sleeve, &[bore(12.4, 24.0, 24.0, "through")])),
            None
        );
        let stepped = [(18.0, 0.0, 8.0), (15.0, 8.0, 24.0)];
        let envelope = |mut a: Analysis| {
            a.envelope = [[-9.0, -9.0, 0.0], [9.0, 9.0, 24.0]];
            a
        };
        assert_eq!(
            why(&envelope(part(
                &stepped,
                &[bore(5.0, 24.0, 24.0, "through")]
            ))),
            None
        );
        let grooved = [(15.0, 0.0, 7.6), (13.4, 7.6, 8.8), (15.0, 8.8, 24.0)];
        assert_eq!(why(&part(&grooved, &[groove(13.4, 8.2)])), None);
        assert!(
            why(&part(
                &grooved,
                &[groove(13.4, 8.2), bore(12.4, 24.0, 24.0, "through")]
            ))
            .unwrap()
            .contains("groove, whose wall")
        );
        assert!(
            why(&part(
                &sleeve,
                &[groove(13.0, 12.0), bore(10.0, 24.0, 24.0, "through")]
            ))
            .unwrap()
            .contains("turned inside")
        );
        assert_eq!(why(&part(&sleeve, &[groove(13.0, 12.0)])), None);
        assert!(
            why(&part(
                &sleeve,
                &[
                    bore(10.0, 24.0, 8.0, "flat"),
                    bore(6.0, 16.0, 16.0, "through")
                ]
            ))
            .unwrap()
            .contains("changes diameter")
        );
        let mut cbore = bore(6.0, 24.0, 24.0, "through");
        cbore.1["cbore"] = json!({ "diameter": 10.0, "depth": 3.0 });
        assert!(
            why(&part(&sleeve, &[cbore]))
                .unwrap()
                .contains("steps inside")
        );
    }
}
