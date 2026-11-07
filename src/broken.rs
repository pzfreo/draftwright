//! A single, geometry-proven longitudinal interruption. Measurements never use this map.
use std::collections::BTreeMap;

use haecceity::hlr::View;
use serde::{Deserialize, Serialize};

use crate::model::{Analysis, PlannedView, Problem, Spec, VIEWS};
use crate::output::{Curve, Item};

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Request {
    /// World axis; absent chooses the longest axis in the requested view's plane.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub axis: Option<String>,
    /// Removed interval, in true model coordinates along axis.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub remove: Option<[f64; 2]>,
    /// Retained separation in paper mm (default 6).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gap: Option<f64>,
    /// Carry the same break into aligned principal views (default true).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub aligned: Option<bool>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Break {
    #[serde(flatten)]
    pub geometry: crate::output::BreakMap,
    pub source: String,
    /// Author-selected paper separation, independent of drawing scale.
    pub paper_gap: f64,
}
impl std::ops::Deref for Break {
    type Target = crate::output::BreakMap;
    fn deref(&self) -> &Self::Target {
        &self.geometry
    }
}
impl Break {
    pub(crate) fn at_scale(&self, scale: f64) -> Self {
        let mut b = self.clone();
        b.geometry.gap = b.paper_gap / scale;
        b
    }
    fn pieces(&self, c: Curve, centre: [f64; 2], v: &PlannedView) -> Vec<(usize, Curve)> {
        let mut out = Vec::new();
        for side in 0..2 {
            let mut rect = [-1e12, -1e12, 1e12, 1e12];
            rect[self.axis + if side == 0 { 2 } else { 0 }] =
                v.at[self.axis] + v.scale * (self.remove[side] - centre[self.axis]);
            let shift = if side == 0 { 1. } else { -1. } * v.scale * self.reduction() / 2.;
            out.extend(
                crate::detail::clip(c.clone(), rect)
                    .into_iter()
                    .map(|c| (side, shift_curve(c, self.axis, shift))),
            );
        }
        out
    }
    pub(crate) fn items(&self, items: Vec<Item>, centre: [f64; 2], v: &PlannedView) -> Vec<Item> {
        items
            .into_iter()
            .flat_map(|item| match item {
                Item::Edge(c) => self
                    .pieces(c, centre, v)
                    .into_iter()
                    .map(|(_, c)| Item::Edge(c))
                    .collect(),
                other => vec![other],
            })
            .collect()
    }
    pub(crate) fn ink(&self, items: &[Item], centre: [f64; 2], v: &PlannedView) -> Vec<Curve> {
        let cut = [0, 1].map(|side| {
            let mut q = centre;
            q[self.axis] = self.remove[side];
            v.at[self.axis] + v.scale * (self.map(q)[self.axis] - centre[self.axis])
        });
        let mut contacts = [Vec::new(), Vec::new()];
        for item in items {
            if let Item::Edge(c) = item {
                for (side, c) in self.pieces(c.clone(), centre, v) {
                    for p in [c.start(), c.end()] {
                        if (p[self.axis] - cut[side]).abs() < 1e-6 {
                            contacts[side].push(p[1 - self.axis]);
                        }
                    }
                }
            }
        }
        let mut out = Vec::new();
        for (side, points) in contacts.iter().enumerate().filter(|(_, p)| !p.is_empty()) {
            let lo = points.iter().copied().fold(f64::INFINITY, f64::min) - 2.;
            let hi = points.iter().copied().fold(f64::NEG_INFINITY, f64::max) + 2.;
            // Continuous thin zig-zag: 4 mm pitch, 1.6 mm peak-to-peak, at every scale.
            let n = ((hi - lo) / 4.).ceil().max(1.) as usize * 4;
            let points: Vec<_> = (0..=n)
                .map(|i| {
                    let mut p = [0.; 2];
                    p[self.axis] = cut[side] + [0., 0.8, 0., -0.8][i % 4];
                    p[1 - self.axis] = lo + (hi - lo) * i as f64 / n as f64;
                    p
                })
                .collect();
            out.extend(points.windows(2).map(|w| Curve::Line(w[0], w[1])));
        }
        out
    }
}

fn shift_curve(mut c: Curve, axis: usize, delta: f64) -> Curve {
    match &mut c {
        Curve::Line(a, b) => {
            a[axis] += delta;
            b[axis] += delta;
        }
        Curve::Conic { centre, .. } => centre[axis] += delta,
        Curve::Beziers(p) => {
            for p in p.iter_mut().flatten() {
                p[axis] += delta;
            }
        }
        Curve::Rational(p) => {
            for p in p.iter_mut().flatten() {
                p[axis] += delta * p[2];
            }
        }
    }
    c
}

/// A conservative extrusion proof: no transverse edge or non-extruded curved face
/// may enter the interval. Every crossing edge is a straight axial generator.
fn spans(a: &Analysis, axis: usize) -> Vec<[f64; 2]> {
    let Some(ink) = &a.edge_ink else {
        return Vec::new();
    };
    let mut stops = vec![a.envelope[0][axis], a.envelope[1][axis]];
    let mut blocked = Vec::new();
    for edge in &ink.edges {
        let lo = edge.iter().map(|p| p[axis]).fold(f64::INFINITY, f64::min);
        let hi = edge
            .iter()
            .map(|p| p[axis])
            .fold(f64::NEG_INFINITY, f64::max);
        stops.extend([lo, hi]);
        let axial = edge.first().is_some_and(|p| {
            edge.iter()
                .all(|q| (0..3).all(|k| k == axis || (q[k] - p[k]).abs() < 1e-6))
        });
        if !axial {
            blocked.push([lo, hi]);
        }
    }
    for face in &ink.curved {
        if !face
            .cylinder
            .as_ref()
            .is_some_and(|c| c.axis[axis].abs() > 1. - 1e-8)
        {
            let range = [face.bounds[0][axis], face.bounds[1][axis]];
            stops.extend(range);
            blocked.push(range);
        }
    }
    stops.sort_by(f64::total_cmp);
    stops.dedup_by(|a, b| (*a - *b).abs() < 1e-6);
    stops
        .windows(2)
        .filter(|s| {
            s[1] - s[0] > 1e-6
                && !blocked
                    .iter()
                    .any(|b| b[0] < s[1] - 1e-6 && b[1] > s[0] + 1e-6)
        })
        .filter(|s| {
            ink.edges
                .iter()
                .filter(|e| e.iter().any(|p| p[axis] <= s[0]) && e.iter().any(|p| p[axis] >= s[1]))
                .count()
                >= 2
                || ink.curved.iter().any(|f| {
                    f.bounds[0][axis] <= s[0]
                        && f.bounds[1][axis] >= s[1]
                        && f.cylinder
                            .as_ref()
                            .is_some_and(|c| c.axis[axis].abs() > 1. - 1e-8)
                })
        })
        .map(|s| [s[0], s[1]])
        .collect()
}

pub(crate) fn resolve(
    a: &Analysis,
    spec: &Spec,
    problems: &mut Vec<Problem>,
) -> BTreeMap<String, Break> {
    let mut out = BTreeMap::new();
    for (name, entry) in &spec.views {
        let Some(r) = &entry.broken else { continue };
        let source = format!("views/{name}/break");
        let resolved = (|| {
            let (_, direction, up) = VIEWS.iter().find(|(n, ..)| *n == name && *n != "iso")?;
            if entry.omit || entry.detail.is_some() || entry.half_section == Some(true) {
                return None;
            }
            let camera = View::new(*direction, *up)?;
            let axis = if let Some(axis) = &r.axis {
                ["x", "y", "z"].iter().position(|k| k == axis)?
            } else {
                (0..3)
                    .filter(|k| camera.toward[*k].abs() < 1e-8)
                    .max_by(|i, j| {
                        (a.envelope[1][*i] - a.envelope[0][*i])
                            .total_cmp(&(a.envelope[1][*j] - a.envelope[0][*j]))
                    })?
            };
            if camera.toward[axis].abs() > 1e-8 {
                return None;
            }
            let gap = r.gap.unwrap_or(6.);
            if !gap.is_finite() || gap <= 0. {
                return None;
            }
            let span = spans(a, axis)
                .into_iter()
                .filter(|s| {
                    r.remove
                        .is_none_or(|r| r[0] > s[0] + 1e-3 && r[1] < s[1] - 1e-3)
                })
                .max_by(|a, b| (a[1] - a[0]).total_cmp(&(b[1] - b[0])))?;
            let width = (0..3)
                .filter(|k| *k != axis)
                .map(|k| a.envelope[1][k] - a.envelope[0][k])
                .fold(0., f64::max);
            let remove = r
                .remove
                .unwrap_or([span[0] + width.max(5.), span[1] - width.max(5.)]);
            if !remove.iter().all(|v| v.is_finite())
                || remove[1] <= remove[0]
                || remove[0] <= span[0]
                || remove[1] >= span[1]
            {
                return None;
            }
            Some((axis, remove, gap))
        })();
        let Some((axis, remove, gap)) = resolved else {
            let why = if entry.omit {
                "views/<id>/omit displaces the break: the carrying view is omitted"
            } else if entry.half_section == Some(true) {
                "views/<id>/half_section displaces the break: combined half sections are unsupported"
            } else if entry.detail.is_some() || !VIEWS[..3].iter().any(|(n, ..)| *n == name) {
                "breaks need an uncut principal view, rather than a detail, section or isometric"
            } else {
                "no proven uniform axial span contains the requested removal and positive retained gap"
            };
            not_applied(problems, name, &source, why);
            continue;
        };
        for (peer, direction, up) in VIEWS.iter().filter(|(n, ..)| *n != "iso") {
            if direction[axis].abs() > 1e-8 {
                continue;
            }
            if *peer != name {
                if r.aligned == Some(false) {
                    continue;
                }
                if let Some(v) = spec.views.get(*peer) {
                    if v.detail.is_some() || v.half_section == Some(true) {
                        not_applied(
                            problems,
                            peer,
                            &source,
                            "displaced aligned intent: this peer has its own detail or half section; projection alignment cannot be maintained",
                        );
                        continue;
                    }
                    if v.broken.is_some() {
                        continue;
                    }
                }
            }
            let camera = View::new(*direction, *up).expect("principal camera");
            let Some(k) = [0, 1].into_iter().find(|k| {
                if *k == 0 {
                    camera.x[axis].abs() > 1. - 1e-8
                } else {
                    camera.y[axis].abs() > 1. - 1e-8
                }
            }) else {
                continue;
            };
            let sign = if k == 0 {
                camera.x[axis]
            } else {
                camera.y[axis]
            };
            let mut interval = remove.map(|p| p * sign);
            interval.sort_by(f64::total_cmp);
            out.insert(
                (*peer).into(),
                Break {
                    geometry: crate::output::BreakMap {
                        centre: crate::model::extent(a, &camera).1,
                        axis: k,
                        remove: interval,
                        gap,
                    },
                    source: source.clone(),
                    paper_gap: gap,
                },
            );
        }
    }
    out
}

/// One warning path for rejected and partially displaced requests.
pub(crate) fn not_applied(problems: &mut Vec<Problem>, name: &str, source: &str, why: &str) {
    let id = format!("p:break_not_applied:v:{name}");
    if problems.iter().any(|p| p.id == id) {
        return;
    }
    problems.push(Problem { id, code: "break_not_applied".into(), severity: "warning".into(),
        subjects: vec![format!("v:{name}")], message: format!("Rejected {source} on {name}: {why}; the request is retained and this view stays unbroken. Choose axis x/y/z in the view plane and an interval strictly inside a constant section, change views/{name}/half_section or detail, or remove the request."), ..Default::default() });
}

/// Report true attachment sites removed by the depiction, rather than silently squeezing them.
pub(crate) fn check_anchors(plan: &mut crate::model::Plan) {
    for v in plan.views.iter().filter(|v| v.broken.is_some()) {
        let b = v.broken.as_ref().unwrap();
        let anchors = plan
            .dimensions
            .iter()
            .filter(|d| d.view == v.id && d.kind == "callout")
            .filter_map(|d| d.placed.as_ref().map(|p| (d.id.as_str(), p)))
            .flat_map(|(id, p)| p.arrows.iter().map(move |a| (id, a[0])))
            .chain(
                plan.gdt
                    .iter()
                    .filter(|g| g.view == v.id)
                    .filter_map(|g| g.placed.as_ref().map(|p| (g.id.as_str(), p.tip))),
            );
        for (id, p) in anchors {
            if !b.contains_page(p, v.at, v.scale) {
                continue;
            }
            let pid = format!("p:break_anchor_removed:{id}");
            if plan.problems.iter().any(|p| p.id == pid) {
                continue;
            }
            plan.problems.push(Problem { id: pid, code: "break_anchor_removed".into(), severity: "warning".into(), subjects: vec![id.into(), v.id.clone()], message: format!("{id} anchors inside the region removed by {}; the true site is compressed into the gap. Change {}/remove or remove the break to show its attachment.", b.source, b.source), ..Default::default() });
        }
    }
}

/// The same authored drawing with its interruption requests removed.
pub(crate) fn without(spec: &Spec) -> Option<Spec> {
    if !spec.views.values().any(|v| v.broken.is_some()) {
        return None;
    }
    let mut base = spec.clone();
    for v in base.views.values_mut() {
        v.broken = None;
    }
    Some(base)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn removed_leader_anchor_warns_but_a_retained_anchor_does_not() {
        let a: Analysis = serde_json::from_value(json!({
            "schema": crate::model::ANALYSIS_SCHEMA,
            "binding": {"source_sha256":"", "part":"test", "face_count":1,"loader":"test"},
            "envelope":[[-7.5,-7.5,0.],[7.5,7.5,24.]], "iso_bounds":[0.,0.,0.,0.],
            "features":[{"id":"f:turned_step:1","kind":"turned_step","faces":[0],
                "parameters":{"axis":"z","diameter":15.,"lo":0.,"hi":24.,
                    "profile":{"axis":"z","axis_origin":[0.,0.,0.]}},
                "fingerprint":{"version":"test","family":"turned_step","subtype":"turned_step",
                    "sizes":{},"position":[0.,0.,12.],"neighbourhood":[]}}]
        }))
        .unwrap();
        let mut p = crate::model::plan(&a, &Spec::default()).unwrap();
        let v = p.views.iter_mut().find(|v| v.id == "v:front").unwrap();
        v.at = [50., 50.];
        v.scale = 1.;
        v.broken = Some(Break {
            geometry: crate::output::BreakMap {
                centre: [0., 12.],
                axis: 1,
                remove: [5., 15.],
                gap: 6.,
            },
            source: "views/front/break".into(),
            paper_gap: 6.,
        });
        let mut d = p.dimensions[0].clone();
        d.kind = "callout".into();
        d.view = v.id.clone();
        d.placed = Some(crate::dims::Placed {
            lines: vec![],
            arrows: vec![[[50., 48.], [51., 49.]]],
            label_at: [0., 0.],
            rotation: 0.,
            label_box: [0.; 4],
        });
        p.dimensions = vec![d];
        check_anchors(&mut p);
        assert!(p.problems.iter().any(|q| q.code == "break_anchor_removed"
            && q.subjects.contains(&p.dimensions[0].id)
            && q.message.contains("views/front/break/remove")));
        p.problems.clear();
        p.dimensions[0].placed.as_mut().unwrap().arrows[0][0][1] = 60.;
        check_anchors(&mut p);
        assert!(!p.problems.iter().any(|q| q.code == "break_anchor_removed"));
    }
}
