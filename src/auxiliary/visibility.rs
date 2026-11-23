//! Certify actual visible rims and straight witnesses before choosing an automatic crop.
use super::*;
use crate::bridge::{Feature, holes};
use crate::output::Curve;

type BoreEnds = BTreeMap<String, Vec<BoreEnd>>;
type BossProfiles = BTreeMap<String, Vec<BossProfile>>;

struct Projection {
    camera: View,
    curves: Vec<Curve>,
    paths: Vec<Vec<[f64; 2]>>,
}

fn projection<'a>(
    part: &haecceity::Part,
    cameras: &'a mut Vec<Projection>,
    toward: Point,
) -> &'a Projection {
    if let Some(index) = cameras
        .iter()
        .position(|p| dot(p.camera.toward, toward) > 1. - TOLERANCE.powi(2))
    {
        return &cameras[index];
    }
    let up = if toward[2].abs() > 0.99 {
        [0., 1., 0.]
    } else {
        [0., 0., 1.]
    };
    let camera = View::new(toward, up).expect("nondegenerate auxiliary camera");
    let pieces = haecceity::hlr::project(part, &camera).unwrap_or_default();
    let mut curves = Vec::new();
    let mut paths = Vec::new();
    for piece in pieces.into_iter().filter(|p| p.visible) {
        if let Some(curve) = crate::output::projected(&piece) {
            curves.push(curve);
            paths.push(piece.points);
        }
    }
    cameras.push(Projection {
        camera,
        curves,
        paths,
    });
    cameras.last().unwrap()
}

pub(crate) fn evidence(part: &haecceity::Part, a: &Analysis) -> (BoreEnds, BossProfiles) {
    let mut cameras = Vec::new();
    let bores = bore_evidence(part, a, &mut cameras);
    let bosses = boss_evidence(part, a, &mut cameras);
    (bores, bosses)
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BossProfile {
    toward: Point,
    witnesses: [Point; 2],
}

pub(crate) fn boss_witnesses(a: &Analysis, id: &str, toward: Point) -> Option<[Point; 2]> {
    a.edge_ink
        .as_ref()?
        .boss_profiles
        .get(id)?
        .iter()
        .find(|profile| dot(profile.toward, toward) > 1. - TOLERANCE)
        .map(|profile| profile.witnesses)
}

fn near_path(path: &[[f64; 2]], at: [f64; 2], tolerance: f64) -> bool {
    path.windows(2).any(|p| {
        let [start, end] = [p[0], p[1]];
        if (0..2).any(|k| {
            at[k] < start[k].min(end[k]) - tolerance || at[k] > start[k].max(end[k]) + tolerance
        }) {
            return false;
        }
        let d = [end[0] - start[0], end[1] - start[1]];
        let offset = [at[0] - start[0], at[1] - start[1]];
        let length = d[0] * d[0] + d[1] * d[1];
        let t = if length > 0. {
            ((d[0] * offset[0] + d[1] * offset[1]) / length).clamp(0., 1.)
        } else {
            0.
        };
        (offset[0] - t * d[0]).hypot(offset[1] - t * d[1]) <= tolerance
    })
}

fn profile_witnesses(
    a: &Analysis,
    f: &Feature,
    camera: &View,
    visible: &[Vec<[f64; 2]>],
) -> Option<[Point; 2]> {
    let ink = a.edge_ink.as_ref()?;
    let frame = holes::frame(a);
    let axis = unit(holes::dir_to_part(
        &frame,
        holes::point(f.parameters.get("axis"))?,
    ))?;
    let top = holes::to_part(&frame, holes::point(f.parameters.get("location"))?);
    let height = f.parameters.get("height")?.as_f64()?;
    let base = std::array::from_fn(|k| top[k] - height * axis[k]);
    let across = unit(cross(axis, camera.toward))?;
    let ends = [base, top];
    let near = |at| visible.iter().any(|line| near_path(line, at, 0.02));
    let edges: BTreeSet<_> = anchor_references(a, &f.id, camera.toward, true)
        .iter()
        .flat_map(|r| r.membership().edges.iter().flatten())
        .map(|edge| edge.index)
        .collect();
    let paths: Vec<_> = edges.iter().filter_map(|i| ink.edges.get(*i)).collect();
    let own: Vec<Vec<_>> = paths
        .iter()
        .map(|path| path.iter().map(|&point| camera.map(point)).collect())
        .collect();
    let proved = |point| {
        let at = camera.map(point);
        near(at) && own.iter().any(|line| near_path(line, at, 0.06))
    };
    // Try the axial witnesses first, then move both by the same transverse
    // amount to a visible point on the trimmed seat. The height stays axial.
    if ends.into_iter().all(proved) {
        return Some(ends);
    }
    paths
        .iter()
        .flat_map(|path| path.windows(2))
        .filter_map(|p| {
            let distances = [p[0], p[1]].map(|point| dot(sub(point, base), axis));
            if distances[0].min(distances[1]) > 0.005 || distances[0].max(distances[1]) < -0.005 {
                return None;
            }
            let delta = distances[1] - distances[0];
            let t = if delta.abs() > 1e-9 {
                (-distances[0] / delta).clamp(0., 1.)
            } else {
                0.
            };
            let point = std::array::from_fn(|k| p[0][k] + t * (p[1][k] - p[0][k]));
            Some(dot(sub(point, base), across))
        })
        .map(|offset| ends.map(|p| std::array::from_fn(|k| p[k] + offset * across[k])))
        .find(|&points| points.into_iter().all(proved))
}

fn boss_evidence(
    part: &haecceity::Part,
    a: &Analysis,
    cameras: &mut Vec<Projection>,
) -> BossProfiles {
    let bosses: Vec<_> = a
        .features
        .iter()
        .filter(|f| f.kind == "boss")
        .filter_map(|f| {
            let axis = anchor_direction(a, &f.id)?;
            holes::principal(axis).is_none().then_some((f, axis))
        })
        .collect();
    let principal = |name: &str| {
        let (_, toward, up) = VIEWS[..3].iter().find(|(n, ..)| *n == name)?;
        View::new(*toward, *up)
    };
    let mut evidence = BTreeMap::new();
    for &(f, axis) in &bosses {
        if !bosses
            .iter()
            .any(|&(_, other)| dot(axis, other) < -1. + TOLERANCE)
        {
            continue;
        }
        let Some((_, direction)) = automatic::profile_parent(a, f, axis, &principal) else {
            continue;
        };
        let mut profiles = Vec::new();
        for toward in [direction, direction.map(|x| -x)] {
            let p = projection(part, cameras, toward);
            if let Some(witnesses) = profile_witnesses(a, f, &p.camera, &p.paths) {
                profiles.push(BossProfile { toward, witnesses });
            }
        }
        evidence.insert(f.id.clone(), profiles);
    }
    evidence
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
    crate::lint::auxiliary_geometry::projected_stations(a, f, view.toward)
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

fn bore_evidence(part: &haecceity::Part, a: &Analysis, cameras: &mut Vec<Projection>) -> BoreEnds {
    let candidates = automatic::inspection_holes(a);
    let frame = holes::frame(a);
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
            let p = projection(part, cameras, toward);
            let (view, curves) = (&p.camera, &p.curves);
            if contains_rim(curves, view.map(hole.at), hole.diameter * 0.5) {
                ends.push(BoreEnd {
                    toward,
                    edges: witnesses(a, feature, view, curves),
                    profile_ends: profile_ends(a, &hole, view, curves),
                });
                // Keep the preferred side if it has the ordinary in-face pair.
                if crate::lint::auxiliary_geometry::has_face_pair(a, feature, toward) {
                    break;
                }
            }
        }
        evidence.insert(feature.id.clone(), ends);
    }
    evidence
}
