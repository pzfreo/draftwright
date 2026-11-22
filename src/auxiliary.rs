//! Requested orthographic views whose viewing direction is true in their parent.
use haecceity::hlr::View;
use serde::{Deserialize, Serialize};

use crate::layout::{Rect, Scaled};
use crate::model::dimension_geometry::Reference;
use crate::model::{Analysis, Invalid, PlannedView, Problem, Spec, VIEWS};

const TOLERANCE: f64 = 1e-6;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuxiliarySpec {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub square_to: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub edge_on: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub from: Option<String>,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub full: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scale_factor: Option<f64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Auxiliary {
    pub anchor: String,
    pub parent: String,
    pub edge_on: bool,
    pub full: bool,
}

type Point = [f64; 3];

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

pub(crate) fn is_name(name: &str) -> bool {
    name.strip_prefix("aux:")
        .is_some_and(|anchor| !anchor.is_empty())
}

pub(crate) fn keeps_parent(spec: &Spec, name: &str) -> bool {
    spec.views.values().any(|v| {
        !v.omit
            && v.auxiliary.as_ref().is_some_and(|aux| {
                aux.from.as_deref().map(|s| s.trim_start_matches("v:")) == Some(name)
            })
    })
}

pub(crate) fn check(spec: &Spec) -> Result<(), Invalid> {
    for (name, entry) in &spec.views {
        let Some(aux) = &entry.auxiliary else {
            if is_name(name) && !entry.omit {
                return Err(invalid(name, "needs an auxiliary request"));
            }
            continue;
        };
        let (anchor, edge) = match (&aux.square_to, &aux.edge_on) {
            (Some(anchor), None) => (anchor, false),
            (None, Some(anchor)) => (anchor, true),
            _ => return Err(invalid(name, "needs exactly one of square_to and edge_on")),
        };
        if anchor.is_empty()
            || (name != &format!("aux:{anchor}")
                && !(edge && name == &format!("aux:{anchor}/edge")))
        {
            return Err(invalid(
                name,
                "must be keyed by its anchor as aux:<anchor> (optionally /edge for an edge view)",
            ));
        }
        if edge
            && name.ends_with("/edge")
            && spec.views.contains_key(&format!("aux:{anchor}"))
            && spec.views[&format!("aux:{anchor}")]
                .auxiliary
                .as_ref()
                .is_some_and(|v| v.edge_on.is_some())
        {
            return Err(invalid(name, "duplicates the edge view for this anchor"));
        }
        if aux.scale_factor.is_some_and(|s| !s.is_finite() || s <= 0.) {
            return Err(invalid(name, "needs a positive finite scale_factor"));
        }
    }
    Ok(())
}

fn invalid(name: &str, why: &str) -> Invalid {
    crate::model::invalid(format!("Auxiliary view {name} {why}."), &[]).at("views", name)
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

fn anchor_direction(a: &Analysis, anchor: &str) -> Option<Point> {
    if let Some(Reference::Plane { normal, .. }) = face(a, anchor) {
        return unit(normal);
    }
    let f = a.features.iter().find(|f| f.id == anchor)?;
    let axis = feature_axis(a, anchor)
        .and_then(|[start, end]| unit(sub(end, start)))
        .or_else(|| unit(f.fingerprint.axis?))?;
    // The fingerprint axis is an unoriented line. The recognizer's opening or
    // top direction gives the side from which the cylinder is actually seen.
    let opening = crate::bridge::holes::point(f.parameters.get("direction"))
        .or_else(|| crate::bridge::holes::point(f.parameters.get("axis")))
        .map(|d| crate::bridge::holes::dir_to_part(&crate::bridge::holes::frame(a), d));
    Some(if opening.is_some_and(|d| dot(d, axis) < 0.) {
        axis.map(|x| -x)
    } else {
        axis
    })
}

pub(crate) struct Request {
    name: String,
    id: String,
    camera: View,
    meta: Auxiliary,
    factor: f64,
    extent: [f64; 2],
}

pub(crate) fn requests(
    a: &Analysis,
    spec: &Spec,
    shown: impl Fn(&str) -> bool,
    problems: &mut Vec<Problem>,
) -> Result<Vec<Request>, Invalid> {
    let principal = |name: &str| {
        VIEWS[..3]
            .iter()
            .find(|(n, ..)| *n == name && shown(n))
            .and_then(|(_, direction, up)| View::new(*direction, *up))
    };
    let mut pending: Vec<_> = spec
        .views
        .iter()
        .filter_map(|(name, entry)| (!entry.omit).then_some((name, entry.auxiliary.as_ref()?)))
        .collect();
    let mut out: Vec<Request> = Vec::new();
    let mut unavailable = std::collections::BTreeSet::new();
    while !pending.is_empty() {
        let count = pending.len();
        let mut next = Vec::new();
        for (name, aux) in pending {
            let parent = aux.from.as_deref().map(|p| p.trim_start_matches("v:"));
            let from_aux = parent.and_then(|p| {
                out.iter()
                    .find(|r| r.name == p || r.id.strip_prefix("v:") == Some(p))
            });
            if let Some(p) = parent.filter(|p| is_name(p)) {
                if unavailable.contains(p) || spec.views.get(p).is_some_and(|v| v.omit) {
                    unavailable.insert(name.as_str());
                    continue;
                }
                if from_aux.is_none() {
                    next.push((name, aux));
                    continue;
                }
                if from_aux.is_some_and(|p| p.meta.parent.starts_with("v:aux:")) {
                    return Err(invalid(
                        name,
                        "cannot descend from a secondary auxiliary view",
                    ));
                }
            }

            let (anchor, edge) = if let Some(anchor) = &aux.square_to {
                (anchor, false)
            } else if let Some(anchor) = &aux.edge_on {
                (anchor, true)
            } else {
                return Err(invalid(name, "needs square_to or edge_on"));
            };
            let id = format!("v:aux:{anchor}{}", if edge { "/edge" } else { "" });
            let Some(axis) = anchor_direction(a, anchor) else {
                problems.push(Problem {
                    id: format!("p:auxiliary_anchor_unapplied:{id}"),
                    code: "auxiliary_anchor_unapplied".into(),
                    severity: "warning".into(),
                    message: format!("The auxiliary view needs a current planar face or feature axis for {anchor}. Its Spec entry is retained."),
                    refs: vec![id.clone()],
                    ..Default::default()
                });
                unavailable.insert(name.as_str());
                continue;
            };
            let direction = |parent: &View| {
                if edge {
                    unit(cross(axis, parent.toward))
                } else {
                    (dot(axis, parent.toward).abs() <= TOLERANCE).then_some(axis)
                }
            };
            let parents = if edge {
                ["plan", "front", "side"]
            } else {
                ["front", "plan", "side"]
            };
            let chosen = match (parent, from_aux) {
                (_, Some(r)) => direction(&r.camera).map(|d| (r.id.clone(), d)),
                (Some(p), None) => {
                    if !VIEWS[..3].iter().any(|(n, ..)| *n == p) {
                        return Err(invalid(
                            name,
                            "needs a principal or primary auxiliary parent",
                        ));
                    }
                    if !shown(p) {
                        unavailable.insert(name.as_str());
                        continue;
                    }
                    principal(p)
                        .and_then(|v| direction(&v))
                        .map(|d| (format!("v:{p}"), d))
                }
                (None, None) => parents
                    .into_iter()
                    .find_map(|p| direction(&principal(p)?).map(|d| (format!("v:{p}"), d))),
            };
            let Some((parent, direction)) = chosen else {
                return Err(invalid(
                    name,
                    "needs a parent whose plane contains its viewing direction",
                ));
            };
            let axes = [[0., 0., 1.], [0., 1., 0.], [1., 0., 0.]];
            let up = axes
                .into_iter()
                .find(|axis| dot(*axis, direction).abs() <= TOLERANCE)
                .unwrap_or(axes[0]);
            let camera =
                View::new(direction, up).ok_or_else(|| invalid(name, "has a degenerate camera"))?;
            out.push(Request {
                name: name.clone(),
                id,
                extent: crate::model::extent(a, &camera).0,
                camera,
                meta: Auxiliary {
                    anchor: anchor.clone(),
                    parent,
                    edge_on: edge,
                    full: aux.full,
                },
                factor: aux.scale_factor.unwrap_or(1.),
            });
        }
        if next.len() == count {
            return Err(invalid(
                next[0].0,
                "needs an existing primary parent and cannot form a cycle",
            ));
        }
        pending = next;
    }
    Ok(out)
}

const PAD: [f64; 2] = [22., 22.];

pub(crate) fn reserve(mut scaled: Scaled, requests: &[Request], scale: f64) -> Scaled {
    scaled.derived.extend(requests.iter().map(|r| {
        let [w, h] = r.extent.map(|d| d * scale * r.factor);
        (r.name.clone(), (w + 2. * PAD[0]).max(65.), h + 2. * PAD[1])
    }));
    scaled
}

pub(crate) fn place(
    views: &mut Vec<PlannedView>,
    requests: Vec<Request>,
    boxes: &[(String, Rect)],
    scale: f64,
    problems: &mut Vec<Problem>,
) {
    for r in requests {
        let area = boxes
            .iter()
            .find(|(name, _)| *name == r.name)
            .map(|(_, area)| *area);
        if let Some(area) = area
            && views.iter().any(|v| v.id == r.meta.parent)
        {
            views.push(PlannedView {
                auxiliary: Some(r.meta),
                detail: None,
                id: r.id,
                direction: r.camera.toward,
                up: r.camera.y,
                at: std::array::from_fn(|k| (area[k] + area[k + 2]) / 2.),
                area,
                scale: scale * r.factor,
                default: false,
                basis: "requested auxiliary projection, placed by reference arrow".into(),
                state: crate::items::State::Shown,
                sheet: 1,
                scale_note: None,
                xref: None,
                line_pose: None,
                line_work: Vec::new(),
                line_style: crate::linework::Settings {
                    hidden: Some(false),
                    ..Default::default()
                },
                from_spec: vec![format!("views/{}/auxiliary", r.name)],
                cut: None,
                half_section: None,
                broken: None,
            });
        } else {
            problems.push(Problem {
                id: format!("p:auxiliary_unplaceable:{}", r.id),
                code: "auxiliary_unplaceable".into(),
                severity: "warning".into(),
                message: "The layout has insufficient room for the auxiliary view or its parent."
                    .into(),
                refs: vec![r.id],
                ..Default::default()
            });
        }
    }
}
