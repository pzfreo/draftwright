//! Geometric evidence shared by auxiliary view planning and drawing lint.
use crate::bridge::{Feature, holes};
use crate::model::Analysis;
use crate::model::dimension_geometry::Reference;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

const TOLERANCE: f64 = 1e-6;

pub(crate) type Point = [f64; 3];

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BoreEnd {
    pub toward: Point,
    pub edges: BTreeSet<usize>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub profile_ends: Vec<usize>,
}

pub(crate) fn bore_end<'a>(a: &'a Analysis, id: &str, toward: Point) -> Option<&'a BoreEnd> {
    a.edge_ink
        .as_ref()?
        .bore_ends
        .get(id)?
        .iter()
        .find(|e| dot(e.toward, toward) > 1. - TOLERANCE)
}

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

fn face_at(a: &Analysis, index: usize) -> Option<Reference> {
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

#[derive(Debug, Clone)]
pub(crate) struct EdgeStation {
    pub face: usize,
    pub edge: usize,
    pub points: [Point; 2],
    pub direction: Point,
    pub length: f64,
    pub has_stock_edge: bool,
    pub key: String,
}

pub(crate) fn has_face_pair(a: &Analysis, f: &Feature, toward: Point) -> bool {
    let stations = edge_stations(a, f, toward);
    stations.first().is_some_and(|first| {
        stations
            .iter()
            .skip(1)
            .any(|s| dot(cross(first.direction, s.direction), toward).abs() > TOLERANCE)
    })
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
            let Ok(Reference::Edge { ends, membership }) = a.dimension_reference(key) else {
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
                points: [anchor, at],
                direction: normal,
                length: dot(delta, normal),
                has_stock_edge: !membership.stock_sides.is_empty(),
                key: key.clone(),
            });
        }
    }
    stations.sort_by_key(|s| (s.face, s.edge));
    stations
}

/// Distance to a visible straight edge in the true-shape projection. The stock
/// edge may be at another depth, such as the seat below a chamfered boss rim.
pub(crate) fn projected_stations(a: &Analysis, f: &Feature, toward: Point) -> Vec<EdgeStation> {
    let Ok(hole) = holes::Hole::read(f, &holes::frame(a)) else {
        return Vec::new();
    };
    let mut stations = Vec::new();
    let mut seen = BTreeSet::new();
    for plane in a.dimension_references().values().flatten() {
        let Reference::Plane { membership, .. } = plane else {
            continue;
        };
        let [face] = membership.faces.as_slice() else {
            continue;
        };
        for edge in membership.edges.iter().flatten() {
            let Some(key) = &edge.reference else { continue };
            if !seen.insert(edge.index) {
                continue;
            }
            let Ok(Reference::Edge {
                ends: [from, to],
                membership,
            }) = a.dimension_reference(key)
            else {
                continue;
            };
            let project = |v| sub(v, scale(toward, dot(v, toward)));
            let span = sub(to, from);
            let projected = project(span);
            let len = dot(projected, projected);
            if len <= 0.01 {
                continue;
            }
            let t = dot(project(sub(hole.at, from)), projected) / len;
            if !(0. ..=1.).contains(&t) {
                continue;
            }
            let anchor = add(from, scale(span, t));
            let separation = project(sub(hole.at, anchor));
            let Some(direction) = unit(separation) else {
                continue;
            };
            stations.push(EdgeStation {
                face: *face,
                edge: edge.index,
                points: [anchor, hole.at],
                direction,
                length: dot(separation, direction),
                has_stock_edge: !membership.stock_sides.is_empty(),
                key: key.clone(),
            });
        }
    }
    stations
}

pub(crate) struct ProfileStation {
    pub reference: String,
    pub points: [Point; 2],
}

impl ProfileStation {
    pub(crate) fn role(&self) -> String {
        format!("location.length.profile.{}", self.reference)
    }

    pub(crate) fn length(&self, toward: Point) -> f64 {
        let d = sub(self.points[1], self.points[0]);
        let d = sub(d, scale(toward, dot(d, toward)));
        dot(d, d).sqrt()
    }
}

/// A turned stock axis fixes the lateral coordinate in an orthographic profile.
/// The visible end of that profile supplies the independent axial station.
pub(crate) fn profile_stations(
    a: &Analysis,
    f: &Feature,
    toward: Point,
    visible_ends: &[usize],
) -> Option<[ProfileStation; 2]> {
    let frame = holes::frame(a);
    let h = holes::Hole::read(f, &frame).ok()?;
    let mut bands = a
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .filter_map(|f| Some((&f.id, super::turned::band(&frame, f).ok()?)));
    let (first, band) = bands.next()?;
    let mut owner = first;
    let mut diameter = band.diameter;
    if toward[band.axis].abs() > TOLERANCE {
        return None;
    }
    let mut covers = (band.lo..=band.hi).contains(&h.at[band.axis]);
    for (id, other) in bands {
        if other.axis != band.axis
            || (0..3).any(|k| k != band.axis && (other.on[k] - band.on[k]).abs() > 0.001)
        {
            return None;
        }
        covers |= (other.lo..=other.hi).contains(&h.at[band.axis]);
        if other.diameter > diameter {
            owner = id;
            diameter = other.diameter;
        }
    }
    if !covers {
        return None;
    }
    let mut axis_point = band.on;
    axis_point[band.axis] = h.at[band.axis];
    let side = ProfileStation {
        reference: owner.clone(),
        points: [axis_point, h.at],
    };
    let index = visible_ends
        .iter()
        .copied()
        .filter(|&i| i < 2)
        .min_by(|&i, &j| {
            (a.envelope[i][band.axis] - h.at[band.axis])
                .abs()
                .total_cmp(&(a.envelope[j][band.axis] - h.at[band.axis]).abs())
        })?;
    let mut end_point = h.at;
    end_point[band.axis] = a.envelope[index][band.axis];
    let end = ProfileStation {
        reference: format!("end:{index}"),
        points: [end_point, h.at],
    };
    (side.length(toward) > 0.001 && end.length(toward) > 0.001).then_some([side, end])
}

impl EdgeStation {
    pub(crate) fn role(&self) -> String {
        format!("location.length.f{}.e{}", self.face, self.edge)
    }
}

/// Find the surface that seats the boss or the common-axis turned component
/// carrying it. End caps belonging to the component are not seating faces.
pub(crate) fn seat_normal(a: &Analysis, f: &Feature, base: Point, axis: Point) -> Option<Point> {
    let component = a.features.iter().find(|peer| {
        peer.kind == "turned_body" && f.faces.iter().all(|face| peer.faces.contains(face))
    });
    let excluded = component.map_or(f.faces.as_slice(), |peer| peer.faces.as_slice());
    let mut contacts = vec![base];
    if let (Some(_), Some(ink)) = (component, &a.edge_ink) {
        let retreat = a
            .dimension_references()
            .values()
            .flatten()
            .filter(
                |r| matches!(r.membership().faces.as_slice(), [face] if excluded.contains(face)),
            )
            .flat_map(|r| r.membership().edges.iter().flatten())
            .filter_map(|e| ink.edges.get(e.index))
            .flatten()
            .map(|&p| dot(sub(p, base), axis))
            .fold(0., f64::min);
        if retreat < -0.002 {
            contacts.push(add(base, scale(axis, retreat)));
        }
    }
    for point in contacts {
        let mut normals = a.dimension_references().values().flatten().filter_map(|r| {
            let Reference::Plane {
                membership,
                origin,
                normal,
                bounds,
            } = r
            else {
                return None;
            };
            if !matches!(membership.faces.as_slice(), [face] if !excluded.contains(face))
                || dot(sub(point, *origin), *normal).abs() > 0.002
                || !(0..3)
                    .all(|k| (bounds[0][k] - 0.002..=bounds[1][k] + 0.002).contains(&point[k]))
            {
                return None;
            }
            let normal = unit(*normal)?;
            let cosine = dot(axis, normal);
            (cosine.abs() > TOLERANCE && cosine.abs() < 1. - TOLERANCE).then(|| {
                if cosine < 0. {
                    scale(normal, -1.)
                } else {
                    normal
                }
            })
        });
        if let Some(normal) = normals.next()
            && normals.all(|other| dot(normal, other) > 1. - TOLERANCE)
        {
            return Some(normal);
        }
    }
    None
}
