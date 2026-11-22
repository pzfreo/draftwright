//! Measurements whose true projection is carried by a requested auxiliary view.
use super::{Measurement, Shape, Want, add, angular::Angle, fmt_in, scale3, want_id};
use crate::auxiliary::{self, cross, dot, sub, unit};
use crate::bridge::{Feature, holes};
use crate::model::dimension_geometry::Reference;
use crate::model::{Analysis, PlannedView, Problem, Spec};
use std::collections::BTreeMap;

type Point = [f64; 3];
const EPS: f64 = 1e-6;

#[derive(Debug)]
pub(crate) struct EdgeStation {
    pub face: usize,
    pub edge: usize,
    pub key: String,
    pub points: [Point; 2],
    pub direction: Point,
    pub length: f64,
}

pub(crate) fn edge_stations(a: &Analysis, feature: &Feature, direction: Point) -> Vec<EdgeStation> {
    let Some((seed, at)) = auxiliary::support(a, feature, direction) else {
        return Vec::new();
    };
    let patches = auxiliary::coplanar_patches(a, &seed, &feature.faces);
    let mut stations = Vec::<EdgeStation>::new();
    for patch in &patches {
        let m = patch.membership();
        let Some(&face) = m.faces.first() else {
            continue;
        };
        for edge in m.edges.iter().flatten() {
            let Some(key) = &edge.reference else { continue };
            let Ok(Reference::Edge { ends, .. }) = a.dimension_reference(key) else {
                continue;
            };
            let span = sub(ends[1], ends[0]);
            let Some(along) = unit(span) else { continue };
            let foot = dot(sub(at, ends[0]), along);
            if foot < -0.002 || foot > dot(along, span) + 0.002 {
                continue;
            }
            let anchor = add(ends[0], scale3(along, foot));
            let delta = sub(at, anchor);
            let Some(normal) = unit(delta) else { continue };
            if dot(normal, direction).abs() > EPS
                || stations.iter().any(|s| {
                    dot(s.direction, normal) > 1. - EPS
                        && dot(sub(anchor, s.points[0]), normal).abs() < EPS
                })
            {
                continue;
            }
            stations.push(EdgeStation {
                face,
                edge: edge.index,
                key: key.clone(),
                points: [anchor, at],
                direction: normal,
                length: dot(delta, normal),
            });
        }
    }
    stations.sort_by_key(|s| (s.face, s.edge));
    stations
}

impl EdgeStation {
    pub(crate) fn role(&self) -> String {
        format!("location.length.f{}.e{}", self.face, self.edge)
    }
}

pub(crate) fn independent(a: &EdgeStation, b: &EdgeStation, axis: Point) -> bool {
    dot(cross(a.direction, b.direction), axis).abs() > EPS
}

fn measurement(
    spec: &Spec,
    feature: &Feature,
    role: &str,
    value: f64,
    shape: Shape,
    basis: &str,
) -> Want {
    let text = if matches!(shape, Shape::Angular { .. }) {
        format!("{:.1}°", value)
    } else {
        fmt_in(value, spec.units)
    };
    Want {
        units: spec.units,
        id: want_id(&feature.id, role),
        views: Vec::new(),
        value,
        terms: vec![text],
        measurements: vec![Measurement {
            feature: feature.id.clone(),
            parameter: role.into(),
            value,
        }],
        basis: basis.into(),
        shape,
        turning: None,
    }
}

pub(super) type Routes<'a> = BTreeMap<String, &'a str>;

fn add_want<'a>(routes: &mut Routes<'a>, wants: &mut Vec<Want>, v: &'a PlannedView, want: Want) {
    routes.insert(want.id.clone(), v.id.trim_start_matches("v:"));
    wants.push(want);
}

fn requested(v: &PlannedView, a: &Analysis, f: &Feature, axis: Point) -> bool {
    let Some(aux) = &v.auxiliary else {
        return false;
    };
    !aux.edge_on
        && dot(v.direction, axis).abs() >= 1. - EPS
        && (aux.anchor == f.id
            || auxiliary::support(a, f, v.direction).is_some_and(|(seed, _)| {
                auxiliary::face(a, &aux.anchor).is_some_and(|anchor| {
                    let patches = auxiliary::coplanar_patches(a, &seed, &f.faces);
                    patches
                        .iter()
                        .any(|r| r.membership().faces == anchor.membership().faces)
                })
            }))
}

pub(super) fn apply<'a>(
    a: &Analysis,
    spec: &Spec,
    views: &'a [PlannedView],
    wants: &mut Vec<Want>,
    _problems: &mut Vec<Problem>,
) -> Routes<'a> {
    let mut routes = Routes::new();
    if views.iter().all(|v| v.auxiliary.is_none()) {
        return routes;
    }
    let frame = holes::frame(a);
    let mut circles = BTreeMap::new();
    for f in &a.features {
        if !matches!(f.kind.as_str(), "hole" | "boss") {
            continue;
        }
        let Some(raw) = holes::point(f.parameters.get("axis")) else {
            continue;
        };
        let Some(axis) = unit(holes::dir_to_part(&frame, raw)) else {
            continue;
        };
        if holes::principal(axis).is_some() {
            continue;
        }
        let Some(at) =
            holes::point(f.parameters.get("location")).map(|p| holes::to_part(&frame, p))
        else {
            continue;
        };
        let square = views.iter().find(|v| requested(v, a, f, axis));
        if let Some(v) = square {
            let stations = edge_stations(a, f, v.direction);
            let pair = stations
                .iter()
                .enumerate()
                .flat_map(|(i, first)| stations[i + 1..].iter().map(move |second| (first, second)))
                .filter(|(first, second)| independent(first, second, axis))
                .max_by(|(a, b), (c, d)| {
                    dot(cross(a.direction, b.direction), axis)
                        .abs()
                        .total_cmp(&dot(cross(c.direction, d.direction), axis).abs())
                });
            if let Some((first, second)) = pair {
                for station in [first, second] {
                    let [a, b] = station.points;
                    let w = measurement(
                        spec,
                        f,
                        &station.role(),
                        station.length,
                        Shape::Aligned { a, b },
                        "perpendicular distance from the face edge to the axis, shown true",
                    );
                    add_want(&mut routes, wants, v, w);
                }
            }
            let site = if f.kind == "hole" {
                let hole = holes::Hole::read(f, &frame).ok();
                hole.as_ref().map_or(at, |h| {
                    if dot(h.direction, v.direction) > 0. {
                        add(at, scale3(axis, h.depth.unwrap_or(0.)))
                    } else {
                        at
                    }
                })
            } else {
                at
            };
            circles.insert(f.id.as_str(), (v, site));
        }
        if f.kind != "boss" {
            continue;
        }
        let Some(height) = f
            .parameters
            .get("height")
            .and_then(serde_json::Value::as_f64)
            .filter(|h| *h > 0.)
        else {
            continue;
        };
        let base = add(at, scale3(axis, -height));
        let vertical = [0., 0., axis[2].signum()];
        let end_view = views.iter().find(|v| {
            v.auxiliary
                .as_ref()
                .is_some_and(|aux| aux.anchor == f.id && dot(v.direction, axis).abs() <= EPS)
        });
        if let Some(v) = end_view {
            add_want(
                &mut routes,
                wants,
                v,
                measurement(
                    spec,
                    f,
                    "boss_height.length",
                    height,
                    Shape::Aligned { a: base, b: at },
                    "height along the boss axis, shown true",
                ),
            );
            if dot(v.direction, vertical).abs() <= EPS {
                let angle = Angle {
                    vertex: base,
                    first: add(base, scale3(vertical, height)),
                    second: at,
                };
                if let Some(value) = angle.degrees() {
                    add_want(
                        &mut routes,
                        wants,
                        v,
                        measurement(
                            spec,
                            f,
                            "boss.inclination.angle",
                            value,
                            Shape::Angular {
                                angle,
                                alternates: Vec::new(),
                            },
                            "inclination of the boss axis from vertical, shown true",
                        ),
                    );
                }
            }
        }
    }
    for w in wants.iter_mut() {
        let Shape::Callout { sites, arcs } = &mut w.shape else {
            continue;
        };
        if w.measurements.is_empty() {
            continue;
        }
        let mut targets = w
            .measurements
            .iter()
            .filter(|m| matches!(m.parameter.as_str(), "bore.diameter" | "boss.diameter"))
            .filter_map(|m| {
                circles
                    .get(m.feature.as_str())
                    .map(|(v, at)| (*v, *at, m.value))
            });
        let Some((v, at, diameter)) = targets.next() else {
            continue;
        };
        let peers: Vec<_> = targets
            .take_while(|(other, ..)| other.id == v.id)
            .map(|(_, at, d)| (at, d / 2.))
            .collect();
        let members = w
            .measurements
            .iter()
            .filter(|m| matches!(m.parameter.as_str(), "bore.diameter" | "boss.diameter"))
            .count();
        if peers.len() + 1 != members {
            continue;
        }
        *sites = std::iter::once((at, diameter / 2.)).chain(peers).collect();
        arcs.clear();
        w.basis = "size called out on the true circle in the requested auxiliary view".into();
        routes.insert(w.id.clone(), v.id.trim_start_matches("v:"));
    }
    routes
}
