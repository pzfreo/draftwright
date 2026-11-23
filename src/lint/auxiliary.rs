//! Opening geometry reconstructed from Analysis for drawing checks.
use super::inclined::{dot, sub, unit};
use crate::bridge::{Feature, holes};
use crate::model::Analysis;
use crate::model::dimension_geometry::Reference;
use std::collections::{BTreeMap, BTreeSet};

type Point = [f64; 3];
const PLANE_TOLERANCE: f64 = 0.002;

#[derive(Clone, Copy)]
pub(super) struct Opening {
    pub at: Point,
    pub normal: Point,
}

pub(super) fn opening(a: &Analysis, f: &Feature, toward: Point) -> Option<Opening> {
    let frame = holes::frame(a);
    let (at, axis, distance) = match f.kind.as_str() {
        "hole" => {
            let h = holes::Hole::read(f, &frame).ok()?;
            let distance = if dot(h.direction, toward) > 0. {
                h.through.then_some(h.depth).flatten()?
            } else {
                -h.entry
            };
            (h.at, h.direction, distance)
        }
        "boss" => (
            holes::to_part(&frame, holes::point(f.parameters.get("location"))?),
            unit(holes::dir_to_part(
                &frame,
                holes::point(f.parameters.get("axis"))?,
            ))?,
            -f.parameters.get("height")?.as_f64()?,
        ),
        _ => return None,
    };
    Some(Opening {
        at: std::array::from_fn(|k| at[k] + axis[k] * distance),
        normal: axis,
    })
}

pub(super) fn face_at(a: &Analysis, index: usize) -> Option<&Reference> {
    a.dimension_references()
        .values()
        .flatten()
        .find(|r| matches!(r, Reference::Plane { membership, .. } if membership.faces == [index]))
}

pub(super) fn feature_edges(a: &Analysis, f: &Feature) -> BTreeSet<usize> {
    a.dimension_references()
        .values()
        .flatten()
        .filter(|r| matches!(r.membership().faces.as_slice(), [face] if f.faces.contains(face)))
        .flat_map(|r| r.membership().edges.iter().flatten().map(|e| e.index))
        .collect()
}

/// Grow the coplanar opening surface from its shared bore or boss boundary.
/// A tangent bore may split its enclosing face into otherwise disjoint patches.
pub(super) fn opening_faces<'a>(
    a: &'a Analysis,
    feature: &Feature,
    opening: Opening,
) -> Vec<&'a Reference> {
    let mut edges = feature_edges(a, feature);
    let mut vertices = BTreeSet::new();
    let mut faces = Vec::new();
    let mut candidates: Vec<_> = a
        .dimension_references()
        .values()
        .flatten()
        .filter(|r| {
            matches!(r, Reference::Plane { membership, origin, normal, .. }
            if matches!(membership.faces.as_slice(), [face] if !feature.faces.contains(face))
                && dot(*normal, opening.normal).abs() > 1. - 1e-6
                && dot(sub(opening.at, *origin), *normal).abs() <= PLANE_TOLERANCE)
        })
        .collect();
    loop {
        let count = candidates.len();
        candidates.retain(|r| {
            let m = r.membership();
            if m.edges.iter().flatten().any(|e| edges.contains(&e.index))
                || m.vertices.iter().flatten().any(|v| vertices.contains(v))
            {
                edges.extend(m.edges.iter().flatten().map(|e| e.index));
                vertices.extend(m.vertices.iter().flatten().cloned());
                faces.push(*r);
                false
            } else {
                true
            }
        });
        if candidates.len() == count {
            return faces;
        }
    }
}

/// Count supporting lines at the live bore opening. Internal patch seams and
/// the bore's own rim are excluded from the enclosing boundary.
pub(super) fn tangent_edges(a: &Analysis, h: &holes::Hole<'_>, toward: Point) -> Option<usize> {
    const TOLERANCE: f64 = 0.001;
    if dot(toward, h.direction).abs() < 1. - 1e-6
        || h.opening_diameter() != h.diameter
        || !h.mouth_chamfers.is_empty()
    {
        return None;
    }
    let mouth = opening(a, h.feature, toward)?;
    let faces = opening_faces(a, h.feature, mouth);
    let mut boundary = BTreeMap::new();
    for edge in faces
        .iter()
        .flat_map(|r| r.membership().edges.iter().flatten())
    {
        boundary
            .entry(edge.index)
            .and_modify(|(count, _)| *count += 1)
            .or_insert((1, edge));
    }
    let radius = h.diameter / 2.;
    let rim: BTreeSet<_> = a
        .dimension_references()
        .values()
        .flatten()
        .filter(|r| {
            matches!(r, Reference::Cylinder { membership, radius: r, .. }
            if (*r - radius).abs() <= TOLERANCE
                && membership.faces.iter().any(|face| h.feature.faces.contains(face)))
        })
        .flat_map(|r| r.membership().edges.iter().flatten().map(|e| e.index))
        .collect();
    let mut seen_rim = false;
    let mut lines = Vec::new();
    let mut points = Vec::new();
    for (count, edge) in boundary.into_values() {
        match count {
            2 => continue,
            1 if rim.contains(&edge.index) => {
                seen_rim = true;
                continue;
            }
            1 => {}
            _ => return None,
        }
        let Reference::Edge { ends, .. } = a.dimension_reference(edge.reference.as_ref()?).ok()?
        else {
            return None;
        };
        let along = unit(sub(ends[1], ends[0]))?;
        let delta = sub(mouth.at, ends[0]);
        let foot = dot(along, delta);
        let offset = std::array::from_fn(|k| delta[k] - foot * along[k]);
        if (dot(offset, offset).sqrt() - radius).abs() > TOLERANCE {
            return None;
        }
        let normal = unit(offset)?;
        if let Some((_, _, touches)) = lines
            .iter_mut()
            .find(|(_, n, _)| dot(*n, normal) > 1. - 1e-6)
        {
            *touches |=
                (-TOLERANCE..=dot(sub(ends[1], ends[0]), along) + TOLERANCE).contains(&foot);
        } else {
            let touches =
                (-TOLERANCE..=dot(sub(ends[1], ends[0]), along) + TOLERANCE).contains(&foot);
            lines.push((ends[0], normal, touches));
        }
        points.extend(ends);
    }
    (seen_rim
        && lines.len() >= 3
        && lines.iter().all(|&(origin, normal, touches)| {
            touches
                && points
                    .iter()
                    .all(|&p| dot(sub(p, origin), normal) >= -TOLERANCE)
        }))
    .then_some(lines.len())
}
