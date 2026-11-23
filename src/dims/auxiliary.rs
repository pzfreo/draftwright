//! Measurements whose true projection is carried by a requested auxiliary view.
use super::{Measurement, Shape, Want, add, angular::Angle, fmt_in, scale3, want_id};
use crate::auxiliary_geometry::{
    EdgeStation, Point, coplanar_patches, cross, dot, edge_stations, face, seat_normal, sub,
    support, unit,
};
use crate::bridge::{Feature, holes};
use crate::model::dimension_geometry::Reference;
use crate::model::{Analysis, PlannedView, Spec};
use std::collections::{BTreeMap, BTreeSet};

const EPS: f64 = 1e-6;

fn independent(a: &EdgeStation, b: &EdgeStation, axis: Point) -> bool {
    dot(cross(a.direction, b.direction), axis).abs() > EPS
}

/// Count the distinct straight supports of a closed, convex entrance. Every
/// exposed edge must be one of those supports or a piece of this bore's rim.
fn inscribed(
    a: &Analysis,
    f: &Feature,
    direction: Point,
    stations: &[EdgeStation],
) -> Option<usize> {
    let h = holes::Hole::read(f, &holes::frame(a)).ok()?;
    if stations.len() < 3 || h.opening_diameter() != h.diameter || !h.mouth_chamfers.is_empty() {
        return None;
    }
    let (seed, _) = support(a, f, direction)?;
    let patches = coplanar_patches(a, &seed, &f.faces);
    let mut boundary = BTreeMap::<usize, (usize, Option<&str>)>::new();
    for edge in patches
        .iter()
        .flat_map(|r| r.membership().edges.iter().flatten())
    {
        boundary
            .entry(edge.index)
            .and_modify(|e| e.0 += 1)
            .or_insert((1, edge.reference.as_deref()));
    }
    let bore: BTreeSet<_> = a
        .dimension_references()
        .values()
        .flatten()
        .filter(|r| {
            matches!(r, Reference::Cylinder { radius, membership, .. }
            if (radius - h.diameter / 2.).abs() <= 0.0001
                && membership.faces.iter().any(|i| f.faces.contains(i)))
        })
        .flat_map(|r| r.membership().edges.iter().flatten().map(|e| e.index))
        .collect();
    let tolerance = 0.001;
    if stations
        .iter()
        .any(|e| (e.length - h.diameter / 2.).abs() > tolerance)
    {
        return None;
    }
    let mut rim = false;
    for (index, (incidence, reference)) in boundary {
        if incidence == 2 {
            continue;
        }
        if incidence != 1 {
            return None;
        }
        let Some(key) = reference else {
            if !bore.contains(&index) {
                return None;
            }
            rim = true;
            continue;
        };
        let Reference::Edge { ends, .. } = a.dimension_reference(key).ok()? else {
            return None;
        };
        let contained = stations.iter().all(|e| {
            ends.iter()
                .all(|&p| dot(sub(p, e.points[0]), e.direction) >= -tolerance)
        });
        let supported = stations.iter().any(|e| {
            ends.iter()
                .all(|&p| dot(sub(p, e.points[0]), e.direction).abs() <= tolerance)
        });
        if !contained || !supported {
            return None;
        }
    }
    rim.then_some(stations.len())
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
            || aux.members.contains(&f.id)
            || support(a, f, v.direction).is_some_and(|(seed, _)| {
                face(a, &aux.anchor).is_some_and(|anchor| {
                    let patches = coplanar_patches(a, &seed, &f.faces);
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
            let tangent = (f.kind == "hole")
                .then(|| inscribed(a, f, v.direction, &stations))
                .flatten();
            circles.insert(f.id.as_str(), (v, site, tangent));
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
        let end_view = views.iter().find(|v| {
            v.auxiliary.as_ref().is_some_and(|aux| {
                (aux.anchor == f.id || aux.members.contains(&f.id))
                    && dot(v.direction, axis).abs() <= EPS
            })
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
            if let Some(normal) = seat_normal(a, f, base, axis)
                && dot(v.direction, normal).abs() <= EPS
            {
                // In a view along the normal of the inclination plane, the
                // cylindrical generator is parallel to the axis at true length.
                // Use that drawn support and the seat normal through its upper end.
                let radius = f
                    .parameters
                    .get("diameter")
                    .and_then(serde_json::Value::as_f64)
                    .unwrap_or(0.)
                    / 2.;
                let Some(across) = unit(cross(axis, v.direction)) else {
                    continue;
                };
                let radial = scale3(across, radius);
                let vertex = add(at, radial);
                let angle = Angle {
                    vertex,
                    first: add(vertex, scale3(normal, -height)),
                    second: add(base, radial),
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
                            "inclination of the boss axis from the seat face normal, shown true",
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
                    .map(|(v, at, count)| (*v, *at, m.value, *count))
            });
        let Some((v, at, diameter, tangent)) = targets.next() else {
            continue;
        };
        if spec
            .dimensions
            .get(&w.id)
            .and_then(|d| {
                d.place
                    .as_ref()
                    .and_then(|p| p.view.as_deref())
                    .or(d.view.as_deref())
            })
            .is_some_and(|name| w.views.contains(&name) && v.id.strip_prefix("v:") != Some(name))
        {
            continue;
        }
        let peers: Vec<_> = targets
            .take_while(|(other, ..)| other.id == v.id)
            .map(|(_, at, d, count)| ((at, d / 2.), count))
            .collect();
        let members = w
            .measurements
            .iter()
            .filter(|m| matches!(m.parameter.as_str(), "bore.diameter" | "boss.diameter"))
            .count();
        if peers.len() + 1 != members {
            continue;
        }
        if let Some(n) = tangent
            && peers.iter().all(|(_, count)| *count == Some(n))
        {
            w.terms.extend([
                crate::callouts::BREAK.into(),
                format!("TANGENT TO {n} EDGES"),
            ]);
        }
        *sites = std::iter::once((at, diameter / 2.))
            .chain(peers.into_iter().map(|(site, _)| site))
            .collect();
        arcs.clear();
        w.basis = "size called out on the true circle in the auxiliary view".into();
        routes.insert(w.id.clone(), v.id.trim_start_matches("v:"));
    }
    routes
}
