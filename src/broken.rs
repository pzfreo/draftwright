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
    /// Retained separation in model mm (default 6).
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
}
impl std::ops::Deref for Break {
    type Target = crate::output::BreakMap;
    fn deref(&self) -> &Self::Target {
        &self.geometry
    }
}
impl Break {
    pub(crate) fn items(&self, items: Vec<Item>, centre: [f64; 2], v: &PlannedView) -> Vec<Item> {
        items
            .into_iter()
            .flat_map(|item| {
                let Item::Edge(c) = item else {
                    return vec![item];
                };
                let mut out = Vec::new();
                for side in 0..2 {
                    let mut rect = [-1e12, -1e12, 1e12, 1e12];
                    rect[self.axis + if side == 0 { 2 } else { 0 }] =
                        v.at[self.axis] + v.scale * (self.remove[side] - centre[self.axis]);
                    let shift = if side == 0 { 1. } else { -1. } * v.scale * self.reduction() / 2.;
                    out.extend(
                        crate::detail::clip(c.clone(), rect)
                            .into_iter()
                            .map(|c| Item::Edge(shift_curve(c, self.axis, shift))),
                    );
                }
                out
            })
            .collect()
    }
    pub(crate) fn ink(&self, items: &[Item], centre: [f64; 2], v: &PlannedView) -> Vec<Curve> {
        let Some(bounds) = crate::output::items_bbox(items) else {
            return Vec::new();
        };
        let mut out = Vec::new();
        for side in 0..2 {
            let mut rect = bounds;
            rect[self.axis + if side == 0 { 2 } else { 0 }] =
                v.at[self.axis] + v.scale * (self.remove[side] - centre[self.axis]);
            let mut boundary = crate::detail::Boundary::default();
            for item in items {
                if let Item::Edge(c) = item {
                    boundary.add(c, &crate::detail::clip(c.clone(), rect), rect);
                }
            }
            let shift = if side == 0 { 1. } else { -1. } * v.scale * self.reduction() / 2.;
            out.extend(
                boundary
                    .ink(rect)
                    .into_iter()
                    .map(|c| shift_curve(c, self.axis, shift)),
            );
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
                || remove[1] - remove[0] <= gap
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
            problems.push(Problem {id:format!("p:break_not_applied:v:{name}"),code:"break_not_applied".into(),severity:"warning".into(),subjects:vec![format!("v:{name}")], message:format!("Rejected {source}: {why}; the request is retained and any shown view stays unbroken. Choose axis x/y/z in the view plane and an interval strictly inside a constant section, or remove the request."),..Default::default()});
            continue;
        };
        for (peer, direction, up) in VIEWS.iter().filter(|(n, ..)| *n != "iso") {
            if *peer != name
                && (r.aligned == Some(false)
                    || spec.views.get(*peer).is_some_and(|v| {
                        v.broken.is_some() || v.detail.is_some() || v.half_section == Some(true)
                    }))
            {
                continue;
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
                },
            );
        }
    }
    out
}
