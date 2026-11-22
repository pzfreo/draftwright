//! Direct authored constraints across an inclined cylindrical axis.
use std::collections::{BTreeMap, BTreeSet};

use super::{Mark, location::AXES};
use crate::model::Analysis;
use crate::model::dimension_geometry::Reference;

fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    std::array::from_fn(|k| a[k] - b[k])
}
fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    (0..3).map(|k| a[k] * b[k]).sum()
}
fn unit(a: [f64; 3]) -> Option<[f64; 3]> {
    let n = dot(a, a).sqrt();
    (n > 1e-10).then(|| a.map(|v| v / n))
}

fn cross(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}

fn axis(a: &Analysis, id: &str) -> Option<([f64; 3], [f64; 3])> {
    let f = a.features.iter().find(|f| f.id == id && f.kind == "hole")?;
    if crate::bridge::holes::principal(f.fingerprint.axis?).is_some() {
        return None;
    }
    if crate::bridge::holes::Hole::read(f, &crate::bridge::holes::frame(a))
        .ok()?
        .principal
        .is_some()
    {
        return None;
    }
    let Reference::Edge { ends, .. } = a.dimension_reference(&format!("axis:{}", f.id)).ok()?
    else {
        return None;
    };
    let d = unit(sub(ends[1], ends[0]))?;
    crate::bridge::holes::principal(d)
        .is_none()
        .then_some((ends[0], d))
}

fn auxiliary_axis(a: &Analysis, id: &str) -> Option<[f64; 3]> {
    if let Some((_, direction)) = axis(a, id) {
        return Some(direction);
    }
    a.features.iter().find(|f| f.id == id && f.kind == "boss")?;
    let [start, end] = crate::auxiliary::feature_axis(a, id)?;
    let direction = unit(sub(end, start))?;
    crate::bridge::holes::principal(direction)
        .is_none()
        .then_some(direction)
}

pub(crate) fn is_inclined(a: &Analysis, id: &str) -> bool {
    axis(a, id).is_some()
}

pub(super) fn has_inclined(a: &Analysis) -> bool {
    a.features.iter().any(|f| {
        f.kind == "hole"
            && f.fingerprint
                .axis
                .is_some_and(|d| crate::bridge::holes::principal(d).is_none())
    })
}

#[derive(Clone)]
struct Constraint<'a> {
    key: &'a str,
    anchor: Reference,
    anchor_index: usize,
    normal: [f64; 3],
    witnesses: [[f64; 3]; 2],
    value: f64,
    auxiliary: bool,
}

impl Constraint<'_> {
    fn anchor_at(&self) -> [f64; 3] {
        self.witnesses[self.anchor_index]
    }
}

// A physical edge/plane is an anchor. Another feature axis is only a relative
// separation, and a true point-to-axis distance is a radius, not a linear station.
fn constraint<'a>(
    a: &Analysis,
    id: &str,
    refs: &[&'a str; 2],
    along: &str,
) -> Option<Constraint<'a>> {
    let (origin, d) = axis(a, id)?;
    let i = refs.iter().position(|r| r.starts_with("axis:"))?;
    if refs[1 - i].starts_with("axis:") {
        return None;
    }
    let Reference::Edge { ends, .. } = a.dimension_reference(refs[i]).ok()? else {
        return None;
    };
    let other_axis = unit(sub(ends[1], ends[0]))?;
    let off = sub(ends[0], origin);
    if dot(d, other_axis).abs() < 1. - 1e-8 || dot(cross(off, d), cross(off, d)) > 1e-10 {
        return None;
    }
    // An explicit face alias must belong to this feature; coincident peers cannot
    // lend their identity just because their axes happen to share a line.
    if refs[i] != format!("axis:{id}") {
        let face = a.dimension_reference_axis_face(refs[i])?;
        if !a
            .features
            .iter()
            .find(|f| f.id == id)?
            .faces
            .contains(&face)
        {
            return None;
        }
    }
    let key = refs[1 - i];
    let anchor = a.dimension_reference(key).ok()?;
    let membership = match &anchor {
        Reference::Plane { membership, .. }
        | Reference::Edge { membership, .. }
        | Reference::Vertex { membership, .. } => membership,
        _ => return None,
    };
    if membership.faces.iter().any(|i| {
        a.features
            .iter()
            .find(|f| f.id == id)
            .is_some_and(|f| f.faces.contains(i))
    }) {
        return None;
    }
    if along == "true"
        && match &anchor {
            Reference::Vertex { .. } => true,
            Reference::Edge { ends, .. } => dot(d, unit(sub(ends[1], ends[0]))?).abs() > 1. - 1e-8,
            _ => false,
        }
    {
        return None;
    }
    let m = a
        .measure_references(&refs.map(str::to_string), "linear", Some(along))
        .ok()?;
    let witnesses = m.witnesses?;
    let normal = if along == "true" {
        unit(sub(witnesses[1], witnesses[0]))?
    } else {
        let k = AXES.iter().position(|v| *v == along)?;
        std::array::from_fn(|j| if j == k { 1. } else { 0. })
    };
    (dot(normal, d).abs() < 1e-6).then_some(Constraint {
        key,
        anchor,
        anchor_index: 1 - i,
        normal,
        witnesses,
        value: m.value,
        auxiliary: false,
    })
}

fn view_normal(view: &str) -> Option<[f64; 3]> {
    match view.trim_start_matches("v:") {
        "front" | "rear" => Some([0., 1., 0.]),
        "side" => Some([1., 0., 0.]),
        "plan" => Some([0., 0., 1.]),
        _ => None,
    }
}

type Witness = (crate::output::DrawnFrame, Vec<[[f64; 2]; 2]>);

fn witness(mark: &Mark) -> Option<Witness> {
    let word = mark
        .claims
        .iter()
        .find_map(|c| c.feature.strip_prefix("station_witness:"))?;
    serde_json::from_str(word).ok()
}

fn constraints<'a>(a: &'a Analysis, marks: &'a [Mark], id: &str) -> Vec<(&'a str, Constraint<'a>)> {
    let mut constraints = Vec::new();
    for mark in marks {
        if mark.text.trim_start().starts_with('(') {
            continue;
        }
        if let Some(c) = auxiliary_constraint(a, mark, id) {
            constraints.push((mark.name.as_str(), c));
        }
        for claim in &mark.claims {
            let Some((refs, along)) = claim
                .feature
                .strip_prefix("geometry:")
                .zip(claim.parameter.strip_prefix("linear."))
            else {
                continue;
            };
            let Some((from, to)) = refs.split_once('|') else {
                continue;
            };
            let Some(c) = constraint(a, id, &[from, to], along) else {
                continue;
            };
            if super::location::nominal_in(&mark.text, c.value, mark.units, mark.precision)
                && mark
                    .view
                    .as_deref()
                    .and_then(view_normal)
                    .is_some_and(|toward| dot(c.normal, toward).abs() < 1e-6)
                && ink_matches(a, mark, &c)
            {
                constraints.push((mark.name.as_str(), c));
            }
        }
    }
    constraints
}

/// Rebuild the actual piercing point and edge normal, and require drawn ink
/// in an end-on camera before a claimed in-face role can enter the chain.
fn auxiliary_constraint<'a>(a: &'a Analysis, mark: &'a Mark, id: &str) -> Option<Constraint<'a>> {
    let view = mark.view.as_deref()?;
    if !view.starts_with("v:aux:") {
        return None;
    }
    let claim = mark
        .claims
        .iter()
        .find(|c| c.feature == id && c.parameter.starts_with("location.length.f"))?;
    let axis = auxiliary_axis(a, id)?;
    let (frame, _) = witness(mark)?;
    if dot(frame.direction, axis).abs() < 1. - 1e-6 {
        return None;
    }
    let f = a.features.iter().find(|f| f.id == id)?;
    let station = crate::dims::auxiliary::edge_stations(a, f, frame.direction)
        .into_iter()
        .find(|s| s.role() == claim.parameter)?;
    let (key, _) = a.dimension_references().get_key_value(&station.key)?;
    let c = Constraint {
        key,
        anchor: a.dimension_reference(key).ok()?,
        anchor_index: 0,
        normal: station.direction,
        witnesses: station.points,
        value: station.length,
        auxiliary: true,
    };
    (super::location::nominal_in(&mark.text, c.value, mark.units, mark.precision)
        && ink_matches(a, mark, &c))
    .then_some(c)
}

/// A printed start-and-angle can establish a physical support edge. The
/// principal face's station and the corner's trace must both be grounded.
fn trace_rooted(
    a: &Analysis,
    marks: &[Mark],
    edges: &[super::location::Edge],
    roots: &[Vec<f64>; 3],
    excluded: &BTreeSet<usize>,
    c: &Constraint<'_>,
) -> bool {
    let Reference::Edge { ends, membership } = &c.anchor else {
        return false;
    };
    let Some(line) = unit(sub(ends[1], ends[0])) else {
        return false;
    };
    let on_line = |p| {
        let d = cross(sub(p, ends[0]), line);
        dot(d, d) < 1e-8
    };
    let spans: Vec<_> = edges
        .iter()
        .enumerate()
        .filter(|(i, _)| !excluded.contains(i))
        .map(|(_, e)| (e.axis, e.a, e.b))
        .collect();
    let station = |k: usize, value| super::profile_angles::rooted_at(&roots[k], &spans, k, value);
    let support_face = membership.faces.iter().find_map(|&face| {
        let r = crate::auxiliary::face_at(a, face)?;
        matches!(&r, Reference::Plane { normal, .. } if crate::bridge::holes::principal(*normal).is_none()).then_some(r)
    });
    let on_face = |p| match &support_face {
        Some(Reference::Plane { normal, origin, .. }) => dot(*normal, sub(p, *origin)).abs() < 1e-6,
        _ => false,
    };
    let adjoining_stock_located = membership.faces.iter().any(|&face| {
        let Some(Reference::Plane { normal, origin, .. }) = crate::auxiliary::face_at(a, face)
        else {
            return false;
        };
        crate::bridge::holes::principal(normal).is_some_and(|axis| station(axis, origin[axis]))
    });
    let mut directions = Vec::new();
    for corner in super::profile_angles::requirements(a) {
        let mut id = corner.feature.split(':');
        if id.next() != Some("outer_profile") {
            continue;
        }
        let Some(face) = id
            .next()
            .and_then(|word| word.strip_prefix('f'))
            .and_then(|n| n.parse().ok())
        else {
            continue;
        };
        let Some(end) = corner.oblique_end() else {
            continue;
        };
        let fixed = station(corner.axis, corner.vertex[corner.axis])
            && (super::profile_angles::undefined_stations(&corner, &station).is_empty()
                || (super::profile_angles::located_with_angle(&corner, &station)
                    && marks.iter().any(|mark| {
                        mark.view
                            .as_deref()
                            .and_then(view_normal)
                            .is_some_and(|n| n[corner.axis].abs() > 1. - 1e-6)
                            && mark.claims.iter().any(|cl| {
                                cl.feature == corner.feature
                                    && cl.parameter == "profile_angle.angle"
                            })
                            && corner.states(&mark.text)
                    })));
        if !fixed {
            continue;
        }
        if membership.faces.contains(&face) && on_line(corner.vertex) && on_line(end) {
            return true;
        }
        // Two nonparallel grounded traces establish this inclined plane. Its
        // intersection with a grounded stock plane then establishes the edge.
        if adjoining_stock_located
            && on_face(corner.vertex)
            && on_face(end)
            && let Some(direction) = unit(sub(end, corner.vertex))
        {
            if directions
                .iter()
                .any(|&d| dot(direction, d).abs() < 1. - 1e-6)
            {
                return true;
            }
            directions.push(direction);
        }
    }
    false
}

struct Stations<'a> {
    a: &'a Analysis,
    edges: &'a [super::location::Edge],
    roots: &'a [Vec<f64>; 3],
}

impl Stations<'_> {
    fn excluding(&self, constraints: &[(&str, Constraint<'_>)]) -> BTreeSet<usize> {
        self.edges
            .iter()
            .enumerate()
            .filter(|(_, e)| constraints.iter().any(|(owner, _)| *owner == e.owner))
            .map(|(i, _)| i)
            .collect()
    }

    fn rooted(&self, c: &Constraint<'_>, excluded: &BTreeSet<usize>) -> bool {
        super::location::anchor_rooted(
            self.a,
            self.edges,
            self.roots,
            (c.key, &c.anchor),
            c.normal,
            c.anchor_at(),
            excluded,
        )
    }
}

fn independent(a: [f64; 3], b: [f64; 3], axis: [f64; 3]) -> bool {
    dot(cross(a, b), axis).abs() > 1e-6
}

/// Two independent, printed linear constraints from rooted anchors remove both
/// transverse freedoms. The axis's own dimensions cannot establish their anchors.
pub(crate) fn located(
    a: &Analysis,
    marks: &[Mark],
    edges: &[super::location::Edge],
    roots: &[Vec<f64>; 3],
    id: &str,
) -> bool {
    let d = axis(a, id).map(|(_, d)| d).or_else(|| {
        marks
            .iter()
            .any(|m| {
                m.view.as_deref().is_some_and(|v| v.starts_with("v:aux:"))
                    && m.claims
                        .iter()
                        .any(|c| c.feature == id && c.parameter.starts_with("location.length.f"))
            })
            .then(|| auxiliary_axis(a, id))
            .flatten()
    });
    let Some(d) = d else { return false };
    let constraints = constraints(a, marks, id);
    let stations = Stations { a, edges, roots };
    let excluded = stations.excluding(&constraints);
    let mut normals = Vec::new();
    for (_, c) in constraints {
        if stations.rooted(&c, &excluded)
            || (c.auxiliary && trace_rooted(a, marks, edges, roots, &excluded, &c))
        {
            if normals.iter().any(|&n| independent(n, c.normal, d)) {
                return true;
            }
            normals.push(c.normal);
        }
    }
    false
}

// When ink is available, its extension-line stations must match the references.
// Mark-only captured evidence still remeasures the references and leading nominal.
fn ink_matches(a: &Analysis, mark: &Mark, c: &Constraint<'_>) -> bool {
    let Some((frame, lines)) = witness(mark) else {
        return !mark
            .claims
            .iter()
            .any(|c| c.feature.starts_with("station_witness:"));
    };
    let Some(view) = haecceity::hlr::View::new(frame.direction, frame.up) else {
        return false;
    };
    if lines.len() < 3
        || !frame.scale.is_finite()
        || frame.scale <= 0.
        || dot(c.normal, view.toward).abs() > 1e-6
    {
        return false;
    }
    let u = view.map(c.normal);
    let project = |p: [f64; 2]| p[0] * u[0] + p[1] * u[1];
    let centre = view.map(std::array::from_fn(|k| {
        (a.envelope[0][k] + a.envelope[1][k]) / 2.
    }));
    let station = |p| {
        let p = view.map(p);
        project([
            frame.at[0] + frame.scale * (p[0] - centre[0]),
            frame.at[1] + frame.scale * (p[1] - centre[1]),
        ])
    };
    let ink: Vec<_> = lines[..2].iter().map(|e| project(e[0])).collect();
    let want = c.witnesses.map(station);
    let near = |x: f64, y: f64| (x - y).abs() < 1e-5;
    let along = sub2(lines[2][1], lines[2][0]);
    (along[0] * u[1] - along[1] * u[0]).abs() < 1e-5
        && lines[..2]
            .iter()
            .all(|e| near(project(e[0]), project(e[1])))
        && ((near(ink[0], want[0]) && near(ink[1], want[1]))
            || (near(ink[0], want[1]) && near(ink[1], want[0])))
}
fn sub2(a: [f64; 2], b: [f64; 2]) -> [f64; 2] {
    [a[0] - b[0], a[1] - b[1]]
}

#[derive(Default)]
struct ViewInk {
    frame: Option<crate::output::DrawnFrame>,
    strokes: Vec<[[f64; 2]; 2]>,
}

impl ViewInk {
    /// Compare to existing dimension strokes using the rendered view frame.
    /// The candidate's line offsets from the projected envelope are estimates.
    fn crossings(&self, a: &Analysis, constraint: &Constraint<'_>) -> Option<usize> {
        if self.strokes.is_empty() {
            return Some(0);
        }
        let frame = self.frame.as_ref()?;
        if frame.half_axis.is_some()
            || frame.broken.is_some()
            || !frame.scale.is_finite()
            || frame.scale <= 0.
        {
            return None;
        }
        let view = haecceity::hlr::View::new(frame.direction, frame.up)?;
        let direction = view.map(constraint.normal);
        let length = direction[0].hypot(direction[1]);
        if length < 1e-6 || dot(constraint.normal, view.toward).abs() > 1e-6 {
            return None;
        }
        let normal = [-direction[1] / length, direction[0] / length];
        let project = |p: [f64; 2]| p[0] * normal[0] + p[1] * normal[1];
        let [lo, hi] = a.envelope;
        let centre = view.map(std::array::from_fn(|k| (lo[k] + hi[k]) / 2.));
        let page = |p| {
            let projected = view.map(p);
            std::array::from_fn(|k| frame.at[k] + frame.scale * (projected[k] - centre[k]))
        };
        let witnesses = constraint.witnesses.map(page);
        let (mut min, mut max) = (f64::INFINITY, f64::NEG_INFINITY);
        for i in 0..8 {
            let corner = std::array::from_fn(|k| if i >> k & 1 == 0 { lo[k] } else { hi[k] });
            let station = project(page(corner));
            min = min.min(station);
            max = max.max(station);
        }
        let mut best = usize::MAX;
        for offset in [10., 17., 24.] {
            for station in [min - offset, max + offset] {
                let stroke = witnesses
                    .map(|p| std::array::from_fn(|k| p[k] + (station - project(p)) * normal[k]));
                let count = self
                    .strokes
                    .iter()
                    .filter(|&&peer| crate::output::segments_cross(stroke, peer))
                    .count();
                if count == 0 {
                    return Some(0);
                }
                best = best.min(count);
            }
        }
        Some(best)
    }
}

fn view_ink(marks: &[Mark]) -> BTreeMap<&str, ViewInk> {
    let mut ink = BTreeMap::<_, ViewInk>::new();
    for mark in marks {
        let (Some(view), Some((frame, lines))) = (mark.view.as_deref(), witness(mark)) else {
            continue;
        };
        let view = ink.entry(view.trim_start_matches("v:")).or_default();
        view.frame = Some(frame);
        if let Some(line) = lines.get(2) {
            view.strokes.push(*line);
        }
    }
    ink
}

struct Suggestion<'a> {
    c: Constraint<'a>,
    along: &'static str,
    view: &'static str,
    missing: bool,
    crossing: usize,
    preference: u8,
}

impl Suggestion<'_> {
    fn score(&self) -> (bool, usize, u8, u64) {
        (
            self.missing,
            self.crossing,
            self.preference,
            self.c.value.to_bits(),
        )
    }

    fn text(&self, a: &Analysis, id: &str, units: crate::output::Units, handles: bool) -> String {
        let axis = handle("the hole's axis".into(), &format!("axis:{id}"), handles);
        let anchor = handle(anchor_name(a, id, &self.c), self.c.key, handles);
        format!(
            "{} from {axis} to {anchor} with along: {} in the {} view",
            units.format(self.c.value),
            self.along,
            self.view
        )
    }
}

fn handle(name: String, key: &str, handles: bool) -> String {
    if handles {
        format!("{name} ({key})")
    } else {
        name
    }
}

fn flange_foot(a: &Analysis, id: &str, c: &Constraint<'_>) -> bool {
    let Reference::Edge { membership, .. } = &c.anchor else {
        return false;
    };
    membership.stock_sides.iter().any(|side| side == "bottom")
        && axis(a, id).is_some_and(|(_, d)| {
            membership.faces.iter().any(|&i| {
                a.face_attachments
                    .get(i)
                    .filter(|f| f.surface.as_deref() == Some("plane"))
                    .and_then(|f| f.normal)
                    .is_some_and(|n| dot(n, d).abs() > 1. - 1e-6)
            })
        })
}

fn anchor_name(a: &Analysis, id: &str, c: &Constraint<'_>) -> String {
    let (kind, membership) = match &c.anchor {
        Reference::Edge { membership, .. } => ("edge", membership),
        Reference::Plane { membership, .. } => ("face", membership),
        Reference::Vertex { membership, .. } => ("corner", membership),
        _ => unreachable!("axis constraints only have physical station anchors"),
    };
    if flange_foot(a, id, c) {
        return "the flange's foot edge".into();
    }
    if !membership.stock_sides.is_empty() {
        return format!("the {} stock {kind}", membership.stock_sides.join(" "));
    }
    let station = c.anchor_at();
    let stations = AXES
        .iter()
        .enumerate()
        .filter(|(k, _)| c.normal[*k].abs() > 1e-6)
        .map(|(k, axis)| {
            format!(
                "{axis} {}",
                super::coverage::fmt(station[k] - a.envelope[0][k])
            )
        })
        .collect::<Vec<_>>()
        .join(", ");
    let owner = membership
        .features
        .first()
        .and_then(|id| a.features.iter().find(|f| &f.id == id))
        .map(|f| format!(" of {}", super::parameters::feature_name(f)))
        .unwrap_or_default();
    format!("the {kind}{owner} at {stations} mm from the stock minimum")
}

/// Suggest edges that are part of the drawn B-rep and have a proven change of
/// tangent plane. A single incident face cannot distinguish a seam from a free
/// edge, and projection silhouettes have no physical reference.
fn drawable_anchors(a: &Analysis) -> Vec<&str> {
    let Some(ink) = &a.edge_ink else {
        return Vec::new();
    };
    let faces: BTreeMap<_, _> = ink
        .references
        .values()
        .flatten()
        .filter(|r| {
            matches!(
                r,
                Reference::Plane { .. } | Reference::Cylinder { .. } | Reference::Sphere { .. }
            )
        })
        .filter_map(|r| Some((*r.membership().faces.first()?, r)))
        .collect();
    let normal = |r: &Reference, at: [f64; 3]| match r {
        Reference::Plane { normal, .. } => Some(*normal),
        Reference::Cylinder { origin, axis, .. } => {
            let radial = sub(at, *origin);
            let height = dot(radial, *axis);
            unit(std::array::from_fn(|k| radial[k] - height * axis[k]))
        }
        Reference::Sphere { centre, .. } => unit(sub(at, *centre)),
        _ => None,
    };
    ink.references
        .iter()
        .filter_map(|(key, values)| match values.as_slice() {
            [Reference::Plane { .. }] => Some(key.as_str()),
            [Reference::Edge { ends, membership }] => {
                let [first, second] = membership.faces.as_slice() else {
                    return None;
                };
                let [first, second] = [*faces.get(first)?, *faces.get(second)?];
                let boundary = first.membership().edges.as_ref()?.iter().find(|e| {
                    e.reference.as_deref() == Some(key)
                        && ink
                            .edges
                            .get(e.index)
                            .is_some_and(|samples| !samples.is_empty())
                })?;
                if !second
                    .membership()
                    .edges
                    .as_ref()?
                    .iter()
                    .any(|e| e.index == boundary.index)
                {
                    return None;
                }
                let at = std::array::from_fn(|k| (ends[0][k] + ends[1][k]) / 2.0);
                let cosine = dot(normal(first, at)?, normal(second, at)?).abs();
                (1.0 - cosine > haecceity::geom::SMOOTH_ARC_GAP).then_some(key.as_str())
            }
            _ => None,
        })
        .collect()
}

/// Select the same guidance for lint and the planner warning. Use current ink
/// to preview dimension-line crossings without recursively planning another sheet.
pub(crate) fn fix(
    a: &Analysis,
    marks: &[Mark],
    edges: &[super::location::Edge],
    roots: &[Vec<f64>; 3],
    id: &str,
    handles: bool,
) -> Option<String> {
    let (_, d) = axis(a, id)?;
    let stations = Stations { a, edges, roots };
    let authored = constraints(a, marks, id);
    let excluded = stations.excluding(&authored);
    let unlocated = authored
        .iter()
        .filter(|(_, c)| !stations.rooted(c, &excluded))
        .map(|(_, c)| (c.key, handle(anchor_name(a, id, c), c.key, handles)))
        .collect::<BTreeMap<_, _>>();
    let ink = view_ink(marks);
    let empty = ViewInk::default();
    let units = marks
        .first()
        .map_or(crate::output::Units::default(), |m| m.units);
    let axis_key = format!("axis:{id}");
    let anchors = drawable_anchors(a);
    let mut candidates = BTreeMap::<&str, Suggestion>::new();
    for along in ["true", "y", "x", "z"] {
        for &key in &anchors {
            let Some(c) = constraint(a, id, &[&axis_key, key], along) else {
                continue;
            };
            let missing = !stations.rooted(&c, &excluded);
            let foot = flange_foot(a, id, &c);
            for view in ["front", "side", "plan"] {
                if dot(c.normal, view_normal(view).unwrap()).abs() > 1e-6 {
                    continue;
                }
                let crossing = ink
                    .get(view)
                    .unwrap_or(&empty)
                    .crossings(a, &c)
                    .unwrap_or(1);
                let back_face = matches!(&c.anchor, Reference::Plane { membership, .. }
                    if membership.stock_sides.iter().any(|side| side == "back"));
                let preference = match (along, view, foot, back_face) {
                    ("true", "front", true, _) | ("y", "side", _, true) => 0,
                    ("true", "front", _, _) | ("y", "side", _, _) => 1,
                    _ => 2,
                };
                let candidate = Suggestion {
                    c: c.clone(),
                    along,
                    view,
                    missing,
                    crossing,
                    preference,
                };
                // With the axis inclined to every principal view direction,
                // all transverse spans in one view have the same direction.
                // Keep its best candidate before comparing directions.
                if candidates
                    .get(view)
                    .is_none_or(|old| candidate.score() < old.score())
                {
                    candidates.insert(view, candidate);
                }
            }
        }
    }
    let mut best = None;
    let candidates: Vec<_> = candidates.values().collect();
    for (i, &first) in candidates.iter().enumerate() {
        for &other in &candidates[i + 1..] {
            if independent(first.c.normal, other.c.normal, d) {
                let score = (
                    usize::from(first.missing) + usize::from(other.missing),
                    first.crossing + other.crossing,
                    first.preference + other.preference,
                    (first.c.value + other.c.value).to_bits(),
                );
                if best.as_ref().is_none_or(|(old, _, _)| score < *old) {
                    best = Some((score, first, other));
                }
            }
        }
    }
    let mut prerequisites: BTreeSet<_> = unlocated.values().cloned().collect();
    if let Some((_, first, second)) = best {
        let suggested: BTreeSet<_> = [first, second]
            .into_iter()
            .filter(|r| r.missing)
            .map(|r| handle(anchor_name(a, id, &r.c), r.c.key, handles))
            .collect();
        let distances = format!(
            "{} and {}",
            first.text(a, id, units, handles),
            second.text(a, id, units, handles)
        );
        if !unlocated.is_empty()
            && unlocated
                .keys()
                .any(|key| *key != first.c.key && *key != second.c.key)
            && authored.iter().enumerate().any(|(i, (_, c))| {
                authored[i + 1..]
                    .iter()
                    .any(|(_, other)| independent(c.normal, other.normal, d))
            })
        {
            let replacement = if suggested.is_empty() {
                format!("replace the current authored dimensions with {distances}")
            } else {
                let anchors = suggested.into_iter().collect::<Vec<_>>().join(" and ");
                format!(
                    "locate {anchors} with printed dimensions to drawing datums and replace the current authored dimensions with {distances}"
                )
            };
            let anchors = prerequisites.into_iter().collect::<Vec<_>>().join(" and ");
            return Some(format!(
                "Either {replacement}. Or keep the current authored dimensions and locate {anchors} with printed dimensions to drawing datums."
            ));
        }
        prerequisites.extend(suggested);
        let required = anchor_prerequisites(prerequisites);
        Some(format!(
            "{required}Locate the inclined hole with two authored dimensions. State {distances}. These constrain both directions across its axis."
        ))
    } else {
        let required = anchor_prerequisites(prerequisites);
        Some(format!(
            "{required}Locate the inclined hole with two independent authored distances from its axis to located edges or datum faces, in views that show the distances at true length."
        ))
    }
}

fn anchor_prerequisites(anchors: BTreeSet<String>) -> String {
    anchors
        .into_iter()
        .map(|anchor| format!("Locate {anchor} with printed dimensions to drawing datums. "))
        .collect()
}

/// Keep the ordinary holes' finding and its fix intact when the legacy check
/// grouped both kinds of hole. Inclined holes have different requirements.
pub(super) fn explain(a: &Analysis, marks: &[Mark], issues: &mut Vec<super::LintIssue>) {
    if !issues.iter().any(|i| {
        i.code == "feature_not_located" && i.subjects.iter().any(|id| axis(a, id).is_some())
    }) {
        return;
    }
    let roots = super::location::drawing_roots(a, marks);
    let edges = super::location::drawn_edges(a, marks);
    let mut separated = Vec::new();
    for issue in issues
        .iter_mut()
        .filter(|i| i.code == "feature_not_located")
    {
        let (inclined, ordinary): (Vec<_>, Vec<_>) = issue
            .subjects
            .iter()
            .cloned()
            .partition(|id| axis(a, id).is_some());
        if inclined.is_empty() {
            continue;
        }
        if ordinary.is_empty() && inclined.len() == 1 {
            describe(a, marks, &edges, &roots, &inclined[0], issue);
            continue;
        }
        for id in inclined {
            let mut one = issue.clone();
            one.id = format!("l:feature_not_located:{id}/location.piercing_point");
            describe(a, marks, &edges, &roots, &id, &mut one);
            separated.push(one);
        }
        issue.subjects = ordinary;
        issue.message = format!(
            "{} hole(s) have no locating dimension",
            issue.subjects.len()
        );
        issue
            .requirements
            .retain(|r| issue.subjects.contains(&r.feature));
    }
    issues.retain(|i| i.code != "feature_not_located" || !i.subjects.is_empty());
    issues.extend(separated);
}

fn describe(
    a: &Analysis,
    marks: &[Mark],
    edges: &[super::location::Edge],
    roots: &[Vec<f64>; 3],
    id: &str,
    issue: &mut super::LintIssue,
) {
    issue.subjects = vec![id.into()];
    let hole = a
        .features
        .iter()
        .find(|f| f.id == id)
        .and_then(|f| f.parameters.get("diameter"))
        .and_then(serde_json::Value::as_f64)
        .map_or("The inclined hole".into(), |d| {
            format!("The ⌀{} hole", super::coverage::fmt(d))
        });
    issue.message = format!("{hole} has no complete location to drawing datums.");
    issue.requirements = vec![super::Requirement {
        feature: id.into(),
        parameter: "location.piercing_point".into(),
    }];
    if !issue.measurements.is_empty() {
        issue.measurements = vec![format!("{id}/location.piercing_point")];
    }
    issue.fix = fix(a, marks, edges, roots, id, true);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inclined_views_without_dimension_ink_score_zero() {
        let a: Analysis = serde_json::from_value(serde_json::json!({
            "schema":"draftwright.analysis/8",
            "binding":{"source_sha256":"","part":"","face_count":0,"loader":""},
            "envelope":[[-10.,-10.,-10.],[10.,10.,10.]],
            "iso_bounds":[0.,0.,0.,0.]
        }))
        .unwrap();
        let c = Constraint {
            key: "face",
            anchor: Reference::Plane {
                membership: Default::default(),
                origin: [0.; 3],
                normal: [0., 1., 0.],
                bounds: [[-10., 0., -10.], [10., 0., 10.]],
            },
            normal: [0., 1., 0.],
            anchor_index: 1,
            witnesses: [[0., -5., 0.], [0., 0., 0.]],
            value: 5.,
            auxiliary: false,
        };
        assert_eq!(ViewInk::default().crossings(&a, &c), Some(0));
        let ink = ViewInk {
            frame: Some(crate::output::DrawnFrame {
                direction: [1., 0., 0.],
                up: [0., 0., 1.],
                at: [100., 100.],
                scale: 2.,
                broken: None,
                half_axis: None,
            }),
            strokes: vec![[[95., 0.], [95., 200.]]],
        };
        assert_eq!(ink.crossings(&a, &c), Some(1));
    }
}
