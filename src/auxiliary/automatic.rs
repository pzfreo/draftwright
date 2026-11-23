//! Choose automatic cameras and leave projection and placement to the request pipeline.
use super::*;
use crate::bridge::{Feature, holes};

const PARENTS: [&str; 3] = ["plan", "front", "side"];

/// An omission remains meaningful when another control resolves the feature's gap.
pub(super) fn known_feature_view(a: &Analysis, name: &str) -> bool {
    let Some(id) = name.strip_prefix("aux:") else {
        return false;
    };
    let anchor = id.strip_suffix("/edge").unwrap_or(id);
    a.features.iter().any(|f| {
        f.id == anchor
            && (f.kind == "boss"
                && anchor_direction(a, anchor).is_some_and(|d| holes::principal(d).is_none())
                || f.kind == "hole"
                    && id == anchor
                    && a.edge_ink
                        .as_ref()
                        .is_some_and(|ink| ink.bore_ends.contains_key(anchor)))
    })
}

fn parallel(a: Point, b: Point) -> bool {
    let perpendicular = cross(a, b);
    dot(perpendicular, perpendicular) <= TOLERANCE.powi(2)
}

fn already_end_on(feature: &Feature, axis: Point, toward: Point) -> bool {
    let cosine = dot(axis, toward).abs();
    parallel(axis, toward)
        // A near-end-on rim can already show the diameter to model resolution.
        // This gates view creation only; its location warning stays in the plan.
        || (cosine >= 0.999
            && feature.parameters.get("diameter").and_then(serde_json::Value::as_f64)
                .is_some_and(|diameter| diameter * (1. - cosine) <= 0.001))
}

fn perpendicular(a: Point, b: Point) -> bool {
    dot(a, b).abs() <= TOLERANCE
}

fn request(
    a: &Analysis,
    spec: &Spec,
    anchor: &str,
    parent: String,
    direction: Point,
    edge: bool,
) -> Result<Option<Request>, Invalid> {
    let id = format!("v:aux:{anchor}{}", if edge { "/edge" } else { "" });
    let name = spec_name(spec, &id);
    if spec
        .views
        .get(name)
        .is_some_and(|v| v.omit || v.auxiliary.is_some())
    {
        return Ok(None);
    }
    make_request(
        a,
        name.into(),
        id,
        anchor.into(),
        parent,
        direction,
        edge,
        None,
    )
    .map(Some)
}

fn include(request: &mut Request, bounds: [f64; 4]) {
    for k in 0..2 {
        request.meta.bounds[k] = request.meta.bounds[k].min(bounds[k]);
        request.meta.bounds[k + 2] = request.meta.bounds[k + 2].max(bounds[k + 2]);
    }
}

fn include_bore(a: &Analysis, spec: &Spec, request: &mut Request, f: &Feature) {
    if request.meta.full || request.meta.edge_on {
        return;
    }
    let Ok(hole) = holes::Hole::read(f, &holes::frame(a)) else {
        return;
    };
    let at = request.camera.map(hole.at);
    let radius = hole.opening_diameter().max(hole.diameter) / 2.;
    let mut bounds = [
        at[0] - radius,
        at[1] - radius,
        at[0] + radius,
        at[1] + radius,
    ];
    if !crate::auxiliary_geometry::has_face_pair(a, f, request.camera.toward) {
        let toward = request.camera.toward;
        let witnesses = crate::dims::auxiliary_stock_pair(a, spec, f, toward)
            .map(|pair| pair.map(|s| s.points[0]))
            .or_else(|| {
                let end = bore_end(a, &f.id, toward)?;
                Some(
                    crate::auxiliary_geometry::profile_stations(a, f, toward, &end.profile_ends)?
                        .map(|s| s.points[0]),
                )
            });
        for witness in witnesses.into_iter().flatten() {
            let at = request.camera.map(witness);
            for k in 0..2 {
                bounds[k] = bounds[k].min(at[k]);
                bounds[k + 2] = bounds[k + 2].max(at[k]);
            }
        }
    }
    include(request, local_crop(a, &request.camera, bounds, 0.12, 2.));
}

fn share(a: &Analysis, spec: &Spec, request: &mut Request, feature: &Feature) {
    let id = feature.id.as_str();
    if !request.automatic || request.meta.anchor == id {
        return;
    }
    if !request.meta.full {
        // Include adjacent stock around shared openings even when their individual
        // end faces barely extend beyond the rims.
        let bounds = |id| {
            let refs = anchor_references(a, id, request.camera.toward, request.meta.edge_on);
            let limits = fit_points(a, &refs, &request.camera)?;
            Some(if request.meta.edge_on {
                local_crop(a, &request.camera, limits, 0.12, 2.)
            } else {
                local_crop(a, &request.camera, limits, 0.25, 6.)
            })
        };
        for bounds in [bounds(&request.meta.anchor), bounds(id)]
            .into_iter()
            .flatten()
        {
            include(request, bounds);
        }
        include_bore(a, spec, request, feature);
    }
    request.meta.members.push(id.into());
}

fn profile_parent(
    a: &Analysis,
    feature: &Feature,
    direction: Point,
    principal: &impl Fn(&str) -> Option<View>,
) -> Option<(String, Point)> {
    let frame = holes::frame(a);
    let seat = holes::point(feature.parameters.get("location")).and_then(|point| {
        let height = feature.parameters.get("height")?.as_f64()?;
        let top = holes::to_part(&frame, point);
        let base = std::array::from_fn(|i| top[i] - height * direction[i]);
        crate::auxiliary_geometry::seat_normal(a, feature, base, direction)
    });
    PARENTS
        .into_iter()
        .filter_map(|name| {
            let d = unit(cross(direction, principal(name)?.toward))?;
            Some((name, d))
        })
        .min_by_key(|(_, d)| !seat.is_some_and(|normal| perpendicular(normal, *d)))
        .map(|(name, direction)| (format!("v:{name}"), direction))
}

fn opening_face(a: &Analysis, feature: &Feature, axis: Point) -> Option<usize> {
    support(a, feature, axis)?
        .0
        .membership()
        .faces
        .first()
        .copied()
}

fn measurable(a: &Analysis, spec: &Spec, f: &Feature, end: &BoreEnd) -> bool {
    let toward = end.toward;
    crate::auxiliary_geometry::has_face_pair(a, f, toward)
        || crate::auxiliary_geometry::profile_stations(a, f, toward, &end.profile_ends).is_some()
        || crate::dims::auxiliary_stock_pair(a, spec, f, toward).is_some()
}

/// Stage 1 can measure from edges on a grid's support, but cannot establish
/// a grid's own datum on an unlocated face. Keep its existing authored remedy.
fn ungrounded_grid_faces(a: &Analysis, needs: &BTreeSet<String>) -> BTreeSet<usize> {
    let Some(recognition) = &a.recognition else {
        return BTreeSet::new();
    };
    recognition
        .patterns
        .iter()
        .filter(|p| p.arrangement == "grid")
        .flat_map(|p| &p.members)
        .filter(|id| needs.contains(*id))
        .filter_map(|id| {
            let feature = a.features.iter().find(|f| &f.id == id)?;
            let axis = anchor_direction(a, id)?;
            let face = opening_face(a, feature, axis)?;
            let has_stock_edge = crate::auxiliary_geometry::edge_stations(a, feature, axis)
                .iter()
                .any(|station| station.has_stock_edge);
            (!has_stock_edge).then_some(face)
        })
        .collect()
}

pub(super) fn inspection_holes(a: &Analysis) -> Vec<(&Feature, Point)> {
    let candidates: Vec<_> = a
        .features
        .iter()
        .filter_map(|f| {
            if f.kind != "hole" {
                return None;
            }
            let axis = anchor_direction(a, &f.id)?;
            (holes::principal(axis).is_none()
                && !VIEWS
                    .iter()
                    .any(|(_, d, _)| unit(*d).is_some_and(|d| already_end_on(f, axis, d))))
            .then_some((f, axis))
        })
        .collect();
    let grid_faces =
        ungrounded_grid_faces(a, &candidates.iter().map(|(f, _)| f.id.clone()).collect());
    candidates
        .into_iter()
        .filter(|(f, axis)| {
            !opening_face(a, f, *axis).is_some_and(|face| grid_faces.contains(&face))
        })
        .collect()
}

pub(super) fn append(
    a: &Analysis,
    spec: &Spec,
    shown: &impl Fn(&str) -> bool,
    principal: &impl Fn(&str) -> Option<View>,
    out: &mut Vec<Request>,
    problems: &mut Vec<Problem>,
) -> BTreeSet<String> {
    let mut recognized = BTreeSet::new();
    if !crate::planning::automatic_auxiliaries() {
        return recognized;
    }
    let needs = crate::dims::auxiliary_needs(a, spec);
    if needs.is_empty() {
        return recognized;
    }
    let pictorial = VIEWS
        .iter()
        .skip(3)
        .map(|(n, d, _)| (*n, *d))
        .chain([("iso_below", [1., -1., -1.])])
        .filter(|(name, _)| shown(name) && a.drawing_iso_bounds() != [0.; 4])
        .filter_map(|(n, d)| unit(spec.views.get(n).and_then(|v| v.direction).unwrap_or(d)))
        .collect::<Vec<_>>();
    let principals: Vec<_> = PARENTS
        .iter()
        .filter_map(|&n| Some((format!("v:{n}"), principal(n)?.toward)))
        .collect();
    let grid_faces = ungrounded_grid_faces(a, &needs);
    let mut omitted = Vec::new();
    let start = out.len();

    // Profile cameras come first so a coaxial bore can use one as its parent.
    for edge in [true, false] {
        let kind = if edge { "boss" } else { "hole" };
        for id in &needs {
            let Some(feature) = a.features.iter().find(|f| f.id == *id && f.kind == kind) else {
                continue;
            };
            let Some(axis) = anchor_direction(a, id) else {
                continue;
            };
            if !edge
                && !grid_faces.is_empty()
                && opening_face(a, feature, axis).is_some_and(|face| grid_faces.contains(&face))
            {
                continue;
            }
            let already_shown = !edge
                && principals
                    .iter()
                    .map(|(_, d)| d)
                    .chain(&pictorial)
                    .any(|&d| already_end_on(feature, axis, d));
            if already_shown {
                continue;
            }
            let name = format!("aux:{id}{}", if edge { "/edge" } else { "" });
            let key = spec_name(spec, &name);
            recognized.insert(key.to_string());
            let (parent, camera) = if edge {
                let Some((parent, direction)) = profile_parent(a, feature, axis, principal) else {
                    problems.push(unplaceable(spec, &format!("v:aux:{id}/edge"), true));
                    continue;
                };
                (Some(parent), direction)
            } else {
                let ends = a.edge_ink.as_ref().and_then(|ink| ink.bore_ends.get(id));
                let Some(ends) = ends.filter(|ends| !ends.is_empty()) else {
                    if spec
                        .views
                        .get(key)
                        .is_none_or(|v| !v.omit && v.auxiliary.is_none())
                    {
                        problems.push(unproved(
                            spec,
                            &format!("v:{name}"),
                            id,
                            "no end-on projection has a verified visible bore rim",
                        ));
                    }
                    continue;
                };
                let Some(end) = ends.iter().find(|end| measurable(a, spec, feature, end)) else {
                    if spec
                        .views
                        .get(key)
                        .is_none_or(|v| !v.omit && v.auxiliary.is_none())
                    {
                        problems.push(unproved(
                            spec,
                            &format!("v:{name}"),
                            id,
                            "no visible end-on projection has two independent location anchors",
                        ));
                    }
                    continue;
                };
                (None, end.toward)
            };
            let can_share = |direction: Point| {
                parallel(direction, camera)
                    && (edge
                        || dot(direction, camera) > 0.
                        || feature
                            .parameters
                            .get("bottom")
                            .and_then(serde_json::Value::as_str)
                            == Some("through"))
            };
            if omitted
                .iter()
                .any(|&(profile, direction)| profile == edge && can_share(direction))
            {
                continue;
            }
            if spec.views.get(key).is_some_and(|v| v.omit) {
                omitted.push((edge, camera));
                continue;
            }
            if let Some(view) = out.iter_mut().find(|v| {
                v.automatic
                    && v.meta.edge_on == edge
                    && can_share(v.camera.toward)
                    && (edge
                        || bore_end(a, id, v.camera.toward)
                            .is_some_and(|end| measurable(a, spec, feature, end)))
            }) {
                share(a, spec, view, feature);
                continue;
            }
            if spec.views.get(key).is_some_and(|v| v.auxiliary.is_some()) {
                continue;
            }
            let mut intermediate = None;
            let parent = parent
                .or_else(|| {
                    principals
                        .iter()
                        .find(|(_, d)| perpendicular(*d, camera))
                        .map(|(n, _)| n.clone())
                })
                .or_else(|| {
                    out.iter()
                        .find(|v| {
                            !v.meta.parent.starts_with("v:aux:")
                                && perpendicular(v.camera.toward, camera)
                        })
                        .map(|v| v.id.clone())
                })
                .or_else(|| {
                    let (parent, direction) = principals.first()?;
                    let mut direction = unit(cross(camera, *direction))?;
                    let parent_camera = principal(parent.strip_prefix("v:")?)?;
                    // Either side shows the face edge-on. Face toward the available
                    // right-hand side of the principal-view row.
                    if parent_camera.map(direction)[0] < 0. {
                        direction = direction.map(|x| -x);
                    }
                    let support = support(a, feature, camera)
                        .and_then(|(face, _)| face.membership().faces.first().copied())
                        .map_or_else(|| id.clone(), |face| format!("face:{face}"));
                    let name = format!("v:aux:{support}/edge");
                    recognized.insert(spec_name(spec, &name).to_string());
                    if spec
                        .views
                        .get(spec_name(spec, &name))
                        .is_some_and(|v| v.omit)
                    {
                        return None;
                    }
                    let mut r =
                        request(a, spec, &support, parent.clone(), direction, true).ok()??;
                    let refs = anchor_references(a, &support, direction, true);
                    if let Some(limits) = fit_points(a, &refs, &r.camera) {
                        // Show the adjoining ends of the stock edges as well as the
                        // anchor edge. Keep those edges local to the cropped face.
                        r.meta.bounds = local_crop(a, &r.camera, limits, 0.24, 4.);
                    }
                    if spec
                        .views
                        .get(&r.name)
                        .is_none_or(|v| v.place.is_none() && v.sheet.is_none())
                    {
                        r.projection = Some(parent_camera.map(direction));
                    }
                    let id = r.id.clone();
                    intermediate = Some(r);
                    Some(id)
                });
            let Some(parent) = parent else {
                problems.push(unplaceable(spec, &format!("v:{name}"), true));
                continue;
            };
            match request(a, spec, id, parent, camera, edge) {
                Ok(Some(mut r)) => {
                    include_bore(a, spec, &mut r, feature);
                    out.extend(intermediate);
                    out.push(r);
                }
                Ok(None) => {}
                Err(_) => problems.push(unplaceable(spec, &format!("v:{name}"), true)),
            }
        }
    }
    // Leave the first profile and its true-shape projections together. Other
    // independently aimed profiles must still precede any dependent view.
    let parents: BTreeSet<_> = out[start..].iter().map(|r| r.meta.parent.clone()).collect();
    if let Some(first_profile) = out[start..]
        .iter()
        .find(|r| r.meta.edge_on)
        .map(|r| r.id.clone())
    {
        out[start..]
            .sort_by_key(|r| r.meta.edge_on && r.id != first_profile && !parents.contains(&r.id));
    }
    recognized
}
