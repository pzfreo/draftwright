//! Certify actual visible rims and straight witnesses before choosing an automatic crop.
use super::*;
use crate::bridge::{Feature, holes};
use crate::output::Curve;

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

fn contains_rim(curves: &[Curve], centre: [f64; 2], radius: f64) -> bool {
    use std::f64::consts::TAU;
    // Unwrap trimmed arcs onto the same circle before checking coverage.
    let mut arcs = Vec::new();
    for curve in curves {
        let Curve::Conic {
            centre: at,
            radii,
            x_axis,
            y_axis,
            t0,
            t1,
        } = curve
        else {
            continue;
        };
        if (centre[0] - at[0]).hypot(centre[1] - at[1]) > 0.002
            || radii.iter().any(|r| (r - radius).abs() > 0.002)
        {
            continue;
        }
        let span = (t1 - t0).abs();
        if span >= TAU - 1e-5 {
            return true;
        }
        let angle = x_axis[1].atan2(x_axis[0]);
        let start = if x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0] > 0. {
            angle + t0.min(*t1)
        } else {
            angle - t0.max(*t1)
        }
        .rem_euclid(TAU);
        let end = start + span;
        arcs.push((start, end.min(TAU)));
        if end > TAU {
            arcs.push((0., end - TAU));
        }
    }
    arcs.sort_by(|left, right| left.0.total_cmp(&right.0));
    let mut end: f64 = 0.;
    for (lo, hi) in arcs {
        if lo > end + 1e-5 {
            return false;
        }
        end = end.max(hi);
    }
    end >= TAU - 1e-5
}

fn visible_at(curves: &[Curve], point: [f64; 2]) -> bool {
    curves.iter().any(|c| {
        let line = match c {
            Curve::Line(a, b) => [*a, *b],
            Curve::Beziers(pieces) => match pieces.as_slice() {
                [p] => match p.as_slice() {
                    [a, b] => [*a, *b],
                    _ => return false,
                },
                _ => return false,
            },
            _ => return false,
        };
        let [a, b] = line;
        let span = [b[0] - a[0], b[1] - a[1]];
        let length = span[0].hypot(span[1]);
        let delta = [point[0] - a[0], point[1] - a[1]];
        length > 0.002
            && (delta[0] * span[1] - delta[1] * span[0]).abs() <= 0.002 * length
            && (0.0..=length * (length + 0.002))
                .contains(&(delta[0] * span[0] + delta[1] * span[1]))
    })
}

fn witnesses(a: &Analysis, f: &Feature, view: &View, curves: &[Curve]) -> BTreeSet<usize> {
    crate::auxiliary_geometry::projected_stations(a, f, view.toward)
        .into_iter()
        .filter(|s| visible_at(curves, view.map(s.points[0])))
        .map(|s| s.edge)
        .collect()
}

fn profile_ends(a: &Analysis, hole: &holes::Hole<'_>, view: &View, curves: &[Curve]) -> Vec<usize> {
    if view.toward[2].abs() > TOLERANCE
        || view.y[2] < 1. - TOLERANCE
        || !a.features.iter().any(|f| f.kind == "turned_step")
    {
        return Vec::new();
    }
    let x = view.map(hole.at)[0];
    (0..2)
        .filter(|&i| {
            let z = a.envelope[i][2];
            curves.iter().any(|curve| {
                if [curve.start(), curve.end(), curve.at(0.5)]
                    .iter()
                    .any(|p| (p[1] - z).abs() > 0.002)
                {
                    return false;
                }
                let [x0, y0, x1, y1] = curve.bbox();
                x >= x0 && x <= x1 && (y0 - z).abs().max((y1 - z).abs()) <= 0.002
            })
        })
        .collect()
}

pub(crate) fn bore_evidence(
    part: &haecceity::Part,
    a: &Analysis,
) -> std::collections::BTreeMap<String, Vec<BoreEnd>> {
    let candidates = automatic::inspection_holes(a);
    let frame = holes::frame(a);
    let mut cameras = Vec::<(View, Vec<Curve>)>::new();
    let mut evidence = std::collections::BTreeMap::new();
    for (feature, direction) in candidates {
        let Ok(hole) = holes::Hole::read(feature, &frame) else {
            continue;
        };
        let mut ends = Vec::new();
        for toward in [direction, direction.map(|x| -x)]
            .into_iter()
            .take(if hole.through { 2 } else { 1 })
        {
            let index = cameras
                .iter()
                .position(|(v, _)| dot(v.toward, toward) > 1. - TOLERANCE.powi(2));
            let index = index.unwrap_or_else(|| {
                let view = View::new(
                    toward,
                    if toward[2].abs() > 0.99 {
                        [0., 1., 0.]
                    } else {
                        [0., 0., 1.]
                    },
                )
                .expect("nondegenerate auxiliary camera");
                let curves = haecceity::hlr::project(part, &view)
                    .ok()
                    .into_iter()
                    .flatten()
                    .filter(|p| p.visible)
                    .filter_map(|p| crate::output::projected(&p))
                    .collect();
                cameras.push((view, curves));
                cameras.len() - 1
            });
            let (view, curves) = &cameras[index];
            if contains_rim(curves, view.map(hole.at), hole.diameter * 0.5) {
                ends.push(BoreEnd {
                    toward,
                    edges: witnesses(a, feature, view, curves),
                    profile_ends: profile_ends(a, &hole, view, curves),
                });
                // Keep the preferred side if it has the ordinary in-face pair.
                if crate::auxiliary_geometry::has_face_pair(a, feature, toward) {
                    break;
                }
            }
        }
        evidence.insert(feature.id.clone(), ends);
    }
    evidence
}
