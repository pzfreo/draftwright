//! Auxiliary orthographic views whose viewing direction is true in their parent.
use haecceity::hlr::View;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

use crate::auxiliary_geometry::{
    Point, coplanar_patches, cross, dot, face, feature_axis, sub, support, unit,
};
use crate::layout::{Rect, Scaled};
use crate::model::dimension_geometry::Reference;
use crate::model::{Analysis, Invalid, Plan, PlannedView, Problem, Spec, VIEWS};

mod automatic;

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
    /// Additional features sharing this automatic projection.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub members: Vec<String>,
    pub parent: String,
    pub edge_on: bool,
    pub full: bool,
    /// Full or cropped rectangle in the camera plane, in model millimetres.
    pub bounds: Rect,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub label: String,
    /// Signed rotation of the image from the projected vertical.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rotation: Option<f64>,
}

pub(crate) fn is_name(name: &str) -> bool {
    name.strip_prefix("aux:")
        .is_some_and(|anchor| !anchor.is_empty())
}

pub(crate) fn spec_name<'a>(spec: &'a Spec, id: &'a str) -> &'a str {
    let name = id.trim_start_matches("v:");
    if !spec.views.contains_key(name)
        && let Some(short) = name.strip_suffix("/edge")
        && spec.views.get(short).is_some_and(|v| {
            v.auxiliary.as_ref().map_or_else(
                || short.starts_with("aux:f:boss:") || short.starts_with("aux:face:"),
                |a| a.edge_on.is_some(),
            )
        })
    {
        return short;
    }
    name
}

pub(crate) fn keeps_parent(spec: &Spec, name: &str) -> bool {
    spec.views.values().any(|v| {
        !v.omit
            && v.auxiliary.as_ref().is_some_and(|aux| {
                aux.from
                    .as_deref()
                    .map(|s| s.trim_start_matches("v:"))
                    .is_none_or(|from| from == name)
            })
    })
}

pub(crate) fn check(spec: &Spec) -> Result<(), Invalid> {
    for (name, entry) in &spec.views {
        let Some(aux) = &entry.auxiliary else {
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

fn unavailable_parent(name: &str, parent: &str, why: &str) -> Problem {
    Problem {
        id: format!("p:auxiliary_parent_unapplied:{name}"),
        code: "auxiliary_parent_unapplied".into(),
        severity: "warning".into(),
        message: format!(
            "Cannot draw {name}: parent view {parent} {why}. Restore or change the parent to draw this view, or omit the auxiliary request."
        ),
        refs: vec![name.into(), parent.into()],
        fix: vec![crate::items::Op::set(
            format!("views/{}/omit", crate::items::key(name)),
            serde_json::Value::Bool(true),
        )],
        ..Default::default()
    }
}

fn anchor_references(a: &Analysis, anchor: &str, camera: Point, edge_on: bool) -> Vec<Reference> {
    if let Some(face) = face(a, anchor) {
        return coplanar_patches(a, &face, &[]);
    }
    let Some(feature) = a.features.iter().find(|f| f.id == anchor) else {
        return Vec::new();
    };
    if !edge_on && let Some((seed, _)) = support(a, feature, camera) {
        return coplanar_patches(a, &seed, &feature.faces);
    }
    a.dimension_references()
        .values()
        .flatten()
        .filter(|r| {
            r.membership().faces.len() == 1
                && r.membership().edges.is_some()
                && feature.faces.contains(&r.membership().faces[0])
        })
        .cloned()
        .collect()
}

fn longest_edge(a: &Analysis, references: &[Reference]) -> Option<Point> {
    let mut lines: Vec<(Point, Point, f64)> = Vec::new();
    for edge in references
        .iter()
        .flat_map(|r| r.membership().edges.iter().flatten())
    {
        let Some(key) = &edge.reference else { continue };
        let Ok(Reference::Edge {
            ends: [from, to], ..
        }) = a.dimension_reference(key)
        else {
            continue;
        };
        let span = sub(to, from);
        let Some(direction) = unit(span) else {
            continue;
        };
        let length = dot(direction, span);
        // The bore can split a straight side into two edges at its tangency.
        if let Some((_, _, total)) = lines.iter_mut().find(|(p, d, _)| {
            dot(*d, direction).abs() > 1. - TOLERANCE
                && dot(cross(sub(from, *p), *d), cross(sub(from, *p), *d)) < TOLERANCE.powi(2)
        }) {
            *total += length;
        } else {
            lines.push((from, direction, length));
        }
    }
    lines
        .into_iter()
        .max_by(|a, b| a.2.total_cmp(&b.2))
        .map(|(_, d, _)| d)
}

fn fit_points(a: &Analysis, references: &[Reference], view: &View) -> Option<Rect> {
    let ink = a.edge_ink.as_ref()?;
    let mut points = references
        .iter()
        .flat_map(|r| r.membership().edges.iter().flatten())
        .filter_map(|e| ink.edges.get(e.index))
        .flatten()
        .map(|p| view.map(*p));
    let first = points.next()?;
    Some(
        points.fold([first[0], first[1], first[0], first[1]], |b, p| {
            [
                b[0].min(p[0]),
                b[1].min(p[1]),
                b[2].max(p[0]),
                b[3].max(p[1]),
            ]
        }),
    )
}

fn crop(a: &Analysis, references: &[Reference], view: &View) -> Option<Rect> {
    let limits = fit_points(a, references, view)?;
    let pad = (0.12 * (limits[2] - limits[0]).max(limits[3] - limits[1])).max(2.0);
    let (size, centre) = crate::model::extent(a, view);
    Some(std::array::from_fn(|k| {
        let axis = k % 2;
        if k < 2 {
            (limits[k] - pad).max(centre[axis] - size[axis] / 2.)
        } else {
            (limits[k] + pad).min(centre[axis] + size[axis] / 2.)
        }
    }))
}

pub(crate) fn extent(rect: Rect) -> ([f64; 2], [f64; 2]) {
    (
        [rect[2] - rect[0], rect[3] - rect[1]],
        std::array::from_fn(|k| (rect[k] + rect[k + 2]) / 2.),
    )
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
    let opening = crate::bridge::holes::point(f.parameters.get("axis"))
        .map(|d| crate::bridge::holes::dir_to_part(&crate::bridge::holes::frame(a), d))
        .map(|d| if f.kind == "hole" { d.map(|v| -v) } else { d });
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
    automatic: bool,
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
                let reason = match spec.views.get(spec_name(spec, p)) {
                    Some(v) if v.omit => Some("is omitted"),
                    _ if unavailable.contains(spec_name(spec, p)) => Some("could not be drawn"),
                    Some(v) if v.auxiliary.is_some() => None,
                    _ => Some("does not exist"),
                };
                if let Some(reason) = reason {
                    problems.push(unavailable_parent(name, p, reason));
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
                        problems.push(unavailable_parent(
                            name,
                            p,
                            "is not a principal or a requested primary auxiliary view",
                        ));
                        unavailable.insert(name.as_str());
                        continue;
                    }
                    if !shown(p) {
                        problems.push(unavailable_parent(name, p, "is hidden from the drawing"));
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
            out.push(make_request(
                a,
                name.clone(),
                id,
                anchor.clone(),
                parent,
                direction,
                edge,
                Some(aux),
            )?);
        }
        if next.len() == count {
            return Err(invalid(
                next[0].0,
                "needs an existing primary parent and cannot form a cycle",
            ));
        }
        pending = next;
    }
    automatic::append(a, spec, &shown, &principal, &mut out, problems);
    Ok(out)
}

#[allow(clippy::too_many_arguments)]
fn make_request(
    a: &Analysis,
    name: String,
    id: String,
    anchor: String,
    parent: String,
    direction: Point,
    edge: bool,
    recipe: Option<&AuxiliarySpec>,
) -> Result<Request, Invalid> {
    let axes = [[0., 0., 1.], [0., 1., 0.], [1., 0., 0.]];
    let upright = axes
        .into_iter()
        .find(|axis| dot(*axis, direction).abs() <= TOLERANCE);
    let base = View::new(direction, upright.unwrap_or(axes[0]))
        .ok_or_else(|| invalid(&name, "has a degenerate camera"))?;
    let references = anchor_references(a, &anchor, base.toward, edge);
    let (camera, rotation) = if upright.is_none()
        && let Some(horizontal) = longest_edge(a, &references)
        && let Some(up) = unit(cross(base.toward, horizontal))
    {
        let up = if dot(up, base.y) < 0. {
            up.map(|x| -x)
        } else {
            up
        };
        let angle = dot(up, base.x).atan2(dot(up, base.y)).to_degrees();
        (View::new(direction, up).unwrap_or(base), Some(angle))
    } else {
        (base, None)
    };
    let full = recipe.is_some_and(|aux| aux.full);
    let crop = (!full).then(|| crop(a, &references, &camera)).flatten();
    if !full && crop.is_none() && recipe.is_some() {
        return Err(invalid(
            &name,
            "needs boundary geometry for a partial view; analyse the STEP again or request full",
        ));
    }
    let bounds = crop.unwrap_or_else(|| {
        let (size, centre) = crate::model::extent(a, &camera);
        std::array::from_fn(|k| centre[k % 2] + if k < 2 { -0.5 } else { 0.5 } * size[k % 2])
    });
    Ok(Request {
        automatic: recipe.is_none(),
        name,
        id,
        camera,
        meta: Auxiliary {
            anchor,
            members: Vec::new(),
            parent,
            edge_on: edge,
            full: crop.is_none(),
            bounds,
            label: String::new(),
            rotation,
        },
        factor: recipe.and_then(|aux| aux.scale_factor).unwrap_or(1.),
    })
}

const PAD: [f64; 2] = [22., 22.];

pub(crate) fn reserve(mut scaled: Scaled, requests: &[Request], scale: f64) -> Scaled {
    scaled.derived.extend(requests.iter().map(|r| {
        let [w, h] = extent(r.meta.bounds).0.map(|d| d * scale * r.factor);
        (r.name.clone(), (w + 2. * PAD[0]).max(65.), h + 2. * PAD[1])
    }));
    scaled
}

pub(crate) fn place(
    spec: &Spec,
    views: &mut Vec<PlannedView>,
    requests: Vec<Request>,
    boxes: &[(String, Rect)],
    scale: f64,
    problems: &mut Vec<Problem>,
) {
    for r in requests {
        if !views.iter().any(|v| v.id == r.meta.parent) {
            problems.push(if r.automatic {
                unplaceable(spec, &r.id, true)
            } else {
                unavailable_parent(&r.name, &r.meta.parent, "could not be placed")
            });
            continue;
        }
        let area = boxes
            .iter()
            .find(|(name, _)| *name == r.name)
            .map(|(_, area)| *area);
        if let Some(area) = area {
            views.push(PlannedView {
                auxiliary: Some(r.meta),
                detail: None,
                id: r.id,
                direction: r.camera.toward,
                up: r.camera.y,
                at: std::array::from_fn(|k| (area[k] + area[k + 2]) / 2.),
                area,
                scale: scale * r.factor,
                default: r.automatic && !spec.views.contains_key(&r.name),
                basis: if r.automatic {
                    "automatic auxiliary projection for an inclined feature, placed by reference arrow"
                } else {
                    "requested auxiliary projection, placed by reference arrow"
                }.into(),
                state: crate::items::State::Shown,
                sheet: 1,
                scale_note: (r.factor != 1.).then(|| crate::model::scale_name(scale * r.factor)),
                xref: None,
                line_pose: None,
                line_work: Vec::new(),
                line_style: crate::linework::Settings {
                    hidden: Some(false),
                    ..Default::default()
                },
                from_spec: if r.automatic {
                    Vec::new()
                } else {
                    vec![format!("views/{}/auxiliary", r.name)]
                },
                cut: None,
                half_section: None,
                broken: None,
            });
        } else {
            problems.push(unplaceable(spec, &r.id, r.automatic));
        }
    }
}

pub(crate) fn is_automatic_view(view: &PlannedView) -> bool {
    view.auxiliary.is_some() && view.basis.starts_with("automatic auxiliary projection")
}

pub(crate) fn automatic_in(plan: &Plan) -> bool {
    plan.views.iter().any(is_automatic_view) || plan.problems.iter().any(automatic_unplaceable)
}

pub(crate) fn automatic_unplaceable(problem: &Problem) -> bool {
    problem.code == "auxiliary_unplaceable" && problem.message.contains("automatic auxiliary view")
}

pub(crate) fn unplaceable(spec: &Spec, id: &str, automatic: bool) -> Problem {
    use crate::items::{Op, key};
    let name = spec_name(spec, id);
    let fix = if spec.sheet.size.is_some() {
        vec![Op::remove("sheet/size")]
    } else if spec.sheet.scale.is_some() {
        vec![Op::remove("sheet/scale")]
    } else {
        vec![Op::set(
            format!("views/{}/omit", key(name)),
            serde_json::json!(true),
        )]
    };
    Problem {
        id: format!("p:auxiliary_unplaceable:{id}"),
        code: "auxiliary_unplaceable".into(),
        severity: "warning".into(),
        message: format!(
            "The layout has insufficient room for the {}auxiliary view. Allow more paper or omit the view.",
            if automatic { "automatic " } else { "" },
        ),
        refs: vec![id.into()],
        fix,
        ..Default::default()
    }
}

/// Assign each letter on the sheet where its reference arrow is read. Also
/// reserve it on the destination sheet if the view is carried there.
pub(crate) fn letters(views: &mut [PlannedView]) {
    if !views.iter().any(|v| v.auxiliary.is_some()) {
        return;
    }
    let mut used = std::collections::BTreeMap::<u32, BTreeSet<String>>::new();
    let sheets: std::collections::BTreeMap<_, _> =
        views.iter().map(|v| (v.id.clone(), v.sheet)).collect();
    for v in views.iter() {
        if let Some(d) = &v.detail {
            used.entry(v.sheet).or_default().insert(d.label.clone());
        }
        if let Some(c) = &v.cut {
            used.entry(v.sheet).or_default().insert(c.label.clone());
            if let Some(sheet) = sheets.get(&c.marked_in) {
                used.entry(*sheet).or_default().insert(c.label.clone());
            }
        }
    }
    for v in views {
        let Some(aux) = &mut v.auxiliary else {
            continue;
        };
        let parent = sheets.get(&aux.parent).copied().unwrap_or(v.sheet);
        let label = (0..)
            .map(|i| {
                let letter = char::from(b'A' + (i % 26) as u8);
                std::iter::repeat_n(letter, 1 + i / 26).collect::<String>()
            })
            .find(|s| {
                !used.get(&parent).is_some_and(|u| u.contains(s))
                    && !used.get(&v.sheet).is_some_and(|u| u.contains(s))
            })
            .expect("finite view inventory");
        used.entry(parent).or_default().insert(label.clone());
        used.entry(v.sheet).or_default().insert(label.clone());
        aux.label = label;
    }
}

pub(crate) fn caption(v: &PlannedView, aux: &Auxiliary) -> String {
    let mut title = format!("VIEW {}", aux.label);
    if let Some(scale) = &v.scale_note {
        title.push_str(&format!(" ({scale})"));
    }
    title
}

pub(crate) fn caption_at(v: &PlannedView, aux: &Auxiliary) -> [f64; 2] {
    crate::detail::below(v, extent(aux.bounds).0[1] * v.scale)
}

pub(crate) fn viewing_arrow(
    a: &Analysis,
    child: &PlannedView,
    parent: &PlannedView,
    views: &[PlannedView],
    page: [f64; 2],
    avoid: &[Rect],
) -> Option<(Vec<crate::output::Curve>, [f64; 2])> {
    use crate::output::Curve;
    let aux = child.auxiliary.as_ref()?;
    let view = View::new(parent.direction, parent.up)?;
    let references = anchor_references(a, &aux.anchor, child.direction, aux.edge_on);
    let (size, centre) = crate::detail::crop_extent(a, parent);
    let target = fit_points(a, &references, &view).map_or(centre, |r| extent(r).1);
    let target: [f64; 2] =
        std::array::from_fn(|k| parent.at[k] + parent.scale * (target[k] - centre[k]));
    // The arrow points along the line of sight, from the viewer to the part.
    let towards = view.map(child.direction);
    let tangent = [-towards[1], towards[0]];
    let base: [f64; 2] = std::array::from_fn(|k| {
        target[k].clamp(
            parent.at[k] - size[k] * parent.scale / 2.,
            parent.at[k] + size[k] * parent.scale / 2.,
        )
    });
    let tip: [f64; 2] = std::array::from_fn(|k| base[k] + 3. * towards[k]);
    let tail: [f64; 2] = std::array::from_fn(|k| tip[k] + 9. * towards[k]);
    let label_positions = [1., -1.].into_iter().flat_map(|side| {
        [4., 7., 10., 15.]
            .into_iter()
            .map(move |gap| std::array::from_fn(|k| tail[k] + side * gap * tangent[k]))
    });
    let obstacles = crate::detail::marker_obstacles(a, views, avoid);
    let label_at = crate::detail::marker_label(&aux.label, label_positions, page, &obstacles)
        .unwrap_or_else(|| std::array::from_fn(|k| tail[k] + 4. * tangent[k]));
    Some((
        vec![
            Curve::Line(tail, tip),
            Curve::Line(
                tip,
                std::array::from_fn(|k| tip[k] + 3. * towards[k] + tangent[k]),
            ),
            Curve::Line(
                tip,
                std::array::from_fn(|k| tip[k] + 3. * towards[k] - tangent[k]),
            ),
        ],
        label_at,
    ))
}

/// ASME Y14.3-2012 §6.4.1 and Figs. 6-4/6-5 specify an arc with an arrow
/// for a rotated reference-arrow view, with its angle in degrees beside it.
pub(crate) fn rotation(angle: f64, at: [f64; 2]) -> (Vec<crate::output::Curve>, String, [f64; 2]) {
    use crate::output::Curve;
    let sense = angle.signum();
    let centre = [at[0] - 5., at[1] - 7.];
    let arc = Curve::Conic {
        centre,
        radii: [2.; 2],
        x_axis: [1., 0.],
        y_axis: [0., sense],
        t0: 0.,
        t1: 4.5,
    };
    let tip = arc.at(1.);
    let before = arc.at(0.84);
    let tangent = [tip[0] - before[0], tip[1] - before[1]];
    let normal = [-0.35 * tangent[1], 0.35 * tangent[0]];
    let mut curves = vec![arc];
    curves.extend(
        [-1., 1.]
            .map(|side| Curve::Line(tip, std::array::from_fn(|k| before[k] + side * normal[k]))),
    );
    (
        curves,
        format!("{:.1}°", angle.abs()),
        [at[0] + 3., centre[1]],
    )
}
