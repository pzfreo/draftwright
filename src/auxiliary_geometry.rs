//! Geometric evidence shared by auxiliary view planning and drawing lint.
use crate::bridge::{Feature, holes};
use crate::model::Analysis;
use crate::model::dimension_geometry::Reference;
use std::collections::BTreeSet;

const TOLERANCE: f64 = 1e-6;

pub(crate) type Point = [f64; 3];

pub(crate) fn sub(a: Point, b: Point) -> Point {
    std::array::from_fn(|k| a[k] - b[k])
}

pub(crate) fn dot(a: Point, b: Point) -> f64 {
    (0..3).map(|k| a[k] * b[k]).sum()
}

pub(crate) fn cross(a: Point, b: Point) -> Point {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

pub(crate) fn unit(v: Point) -> Option<Point> {
    let length = dot(v, v).sqrt();
    (length.is_finite() && length > 1e-10).then(|| v.map(|x| x / length))
}

fn add(a: Point, b: Point) -> Point {
    [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
}

fn scale(v: Point, factor: f64) -> Point {
    [v[0] * factor, v[1] * factor, v[2] * factor]
}

pub(crate) fn feature_axis(a: &Analysis, anchor: &str) -> Option<[Point; 2]> {
    let Reference::Edge { ends, .. } = a.dimension_reference(&format!("axis:{anchor}")).ok()?
    else {
        return None;
    };
    Some(ends)
}

pub(crate) fn face(a: &Analysis, anchor: &str) -> Option<Reference> {
    if let Ok(reference @ Reference::Plane { .. }) = a.dimension_reference(anchor) {
        return Some(reference);
    }
    let index = anchor.strip_prefix("face:")?.parse().ok()?;
    face_at(a, index)
}

pub(crate) fn face_at(a: &Analysis, index: usize) -> Option<Reference> {
    a.dimension_references()
        .values()
        .flatten()
        .find(|r| matches!(r, Reference::Plane { membership, .. } if membership.faces == [index]))
        .cloned()
}

/// Planar patches that share the boundary of a feature and lie on the same
/// plane. An inscribed bore can split its entrance into several such patches.
pub(crate) fn coplanar_patches(
    a: &Analysis,
    seed: &Reference,
    feature_faces: &[usize],
) -> Vec<Reference> {
    let Reference::Plane { origin, normal, .. } = seed else {
        return Vec::new();
    };
    let mut boundary = seed.membership().vertices.clone().unwrap_or_default();
    let mut patches = vec![seed.clone()];
    let mut candidates: Vec<_> = a
        .dimension_references()
        .values()
        .flatten()
        .filter(|r| {
            matches!(r, Reference::Plane { origin: p, normal: n, .. }
            if dot(*normal, *n).abs() > 1. - TOLERANCE
                && dot(sub(*p, *origin), *normal).abs() < TOLERANCE)
                && r.membership().faces != seed.membership().faces
        })
        .collect();
    loop {
        let count = candidates.len();
        candidates.retain(|r| {
            let m = r.membership();
            let touches = m
                .vertices
                .iter()
                .flatten()
                .any(|key| boundary.contains(key))
                || m.edges.iter().flatten().any(|edge| {
                    feature_faces.iter().any(|&index| {
                        a.dimension_references().values().flatten().any(|f| {
                            f.membership().faces == [index]
                                && f.membership()
                                    .edges
                                    .iter()
                                    .flatten()
                                    .any(|e| e.index == edge.index)
                        })
                    })
                });
            if touches {
                boundary.extend(m.vertices.iter().flatten().cloned());
                patches.push((*r).clone());
            }
            !touches
        });
        if candidates.len() == count {
            break;
        }
    }
    patches
}

/// A support at the opening of a hole or the base of a boss. Require shared
/// B-rep boundary and the same normal section as the axis.
pub(crate) fn support(
    a: &Analysis,
    feature: &Feature,
    camera: Point,
) -> Option<(Reference, Point)> {
    let q = holes::frame(a);
    let (point, side) = if feature.kind == "hole" {
        let hole = holes::Hole::read(feature, &q).ok()?;
        let far = dot(hole.direction, camera) > 0.;
        (
            add(
                hole.at,
                scale(hole.direction, if far { hole.depth? } else { -hole.entry }),
            ),
            camera,
        )
    } else {
        let top = holes::to_part(&q, holes::point(feature.parameters.get("location"))?);
        let direction = holes::dir_to_part(&q, holes::point(feature.parameters.get("axis"))?);
        (
            add(
                top,
                scale(direction, -feature.parameters.get("height")?.as_f64()?),
            ),
            direction,
        )
    };
    let edges: BTreeSet<_> = a
        .dimension_references()
        .values()
        .flatten()
        .filter(|r| {
            r.membership()
                .faces
                .as_slice()
                .first()
                .is_some_and(|i| feature.faces.contains(i))
        })
        .flat_map(|r| r.membership().edges.iter().flatten().map(|e| e.index))
        .collect();
    let seed = a
        .dimension_references()
        .values()
        .flatten()
        .filter(|r| {
            r.membership()
                .faces
                .iter()
                .all(|i| !feature.faces.contains(i))
        })
        .filter_map(|r| match r {
            Reference::Plane { origin, normal, .. }
                if dot(*normal, side).abs() > 1. - TOLERANCE
                    && r.membership()
                        .edges
                        .iter()
                        .flatten()
                        .any(|e| edges.contains(&e.index)) =>
            {
                Some((dot(sub(point, *origin), *normal).abs(), r))
            }
            _ => None,
        })
        .filter(|(distance, _)| *distance <= 0.002)
        .min_by(|(a, _), (b, _)| a.total_cmp(b))?
        .1;
    Some((seed.clone(), point))
}

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
    let Some((seed, at)) = support(a, feature, direction) else {
        return Vec::new();
    };
    let patches = coplanar_patches(a, &seed, &feature.faces);
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
            let anchor = add(ends[0], scale(along, foot));
            let delta = sub(at, anchor);
            let Some(normal) = unit(delta) else { continue };
            if dot(normal, direction).abs() > TOLERANCE
                || stations.iter().any(|s| {
                    dot(s.direction, normal) > 1. - TOLERANCE
                        && dot(sub(anchor, s.points[0]), normal).abs() < TOLERANCE
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
