use std::path::Path;

use draftwright::haecceity::{Part, hlr::View};
use draftwright::model::{self, Analysis, Plan, Spec, dimension_geometry::Reference};
use draftwright::output::{Curve, Drawing};
use serde_json::{Value, json};

const BORE: &str = "f:hole:f10";
const EDGE: &str = "v:aux:face:4/edge";
const TRUE: &str = "v:aux:f:hole:f10";
const PIN: &str = "f:boss:f20";
const PIN_VIEW: &str = "v:aux:f:boss:f20/edge";

fn part(path: &str) -> (Part, Analysis) {
    model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures")
            .join(path),
    )
    .expect("fixture analyses")
}

fn jern() -> Value {
    json!({"views":{
        "aux:face:4":{"auxiliary":{"edge_on":"face:4","from":"plan"}},
        "aux:f:hole:f10":{"auxiliary":{"square_to":BORE,"from":EDGE}}
    }})
}

fn plan(a: &Analysis, spec: Value) -> Plan {
    let spec: Spec = serde_json::from_value(spec).expect("auxiliary Spec parses");
    model::plan_recovered(a, &spec).expect("auxiliary Spec plans")
}

fn render(part: &Part, a: &Analysis, p: &Plan) -> Drawing {
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    model::render(part, a, p, &font).expect("auxiliary projection renders")
}

fn sub(a: [f64; 3], b: [f64; 3]) -> [f64; 3] {
    std::array::from_fn(|k| a[k] - b[k])
}

fn dot(a: [f64; 3], b: [f64; 3]) -> f64 {
    (0..3).map(|k| a[k] * b[k]).sum()
}

fn assert_true(span: [f64; 3], direction: [f64; 3]) {
    assert!(
        dot(span, direction).abs() < 1e-6,
        "{span:?} in {direction:?}"
    );
}

fn lift(view: &View, span: [f64; 2]) -> [f64; 3] {
    std::array::from_fn(|k| span[0] * view.x[k] + span[1] * view.y[k])
}

fn page_span([a, b]: [[f64; 2]; 2]) -> [f64; 2] {
    [b[0] - a[0], b[1] - a[1]]
}

fn arrow(drawing: &Drawing, p: &Plan, child: &str, parent: &str) {
    let parent_view = p.views.iter().find(|v| v.id == parent).unwrap();
    let child_view = p.views.iter().find(|v| v.id == child).unwrap();
    let view = View::new(parent_view.direction, parent_view.up).unwrap();
    let d = view.map(child_view.direction);
    let mark = drawing
        .sheet
        .items
        .iter()
        .find(|ink| ink.name == format!("{parent}/arrow/{child}"))
        .expect("arrow owned by parent");
    let [tail, tip] = mark.lines[0];
    assert!((0..2).all(|i| (tip[i] - tail[i] + 9. * d[i]).abs() < 1e-6));
}

fn assert_fidelity(drawing: &Drawing, view: &str) {
    let items: Vec<_> = drawing
        .annotations
        .iter()
        .filter(|a| a.view.as_deref() == Some(view))
        .map(|a| a.name.as_str())
        .collect();
    let findings: Vec<_> = draftwright::lint::structural::check(drawing)
        .into_iter()
        .filter(|i| {
            matches!(
                i.code.as_str(),
                "label_vs_measured"
                    | "angular_label_vs_geometry"
                    | "angular_geometry_mismatch"
                    | "angular_support_mismatch"
            ) && i.annotation.as_deref().is_some_and(|a| items.contains(&a))
        })
        .collect();
    assert!(findings.is_empty(), "{findings:?}");
}

#[test]
fn jern_has_an_edge_parent_and_a_true_shape_with_grounded_location() {
    let (part, a) = part("parts/corner-cuts.step.gz");
    let p = plan(&a, jern());
    let doc = serde_json::to_value(&p).unwrap();
    let meta = |id| {
        doc["views"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| v["id"] == id)
            .and_then(|v| v.get("auxiliary"))
            .expect("auxiliary camera metadata")
    };
    let edge = p
        .views
        .iter()
        .find(|v| v.id == EDGE)
        .expect("primary edge view");
    let square = p
        .views
        .iter()
        .find(|v| v.id == TRUE)
        .expect("secondary true shape view");
    assert_eq!(meta(EDGE)["anchor"], "face:4");
    assert_eq!(meta(EDGE)["parent"], "v:plan");
    assert_eq!(meta(TRUE)["anchor"], BORE);
    assert_eq!(meta(TRUE)["parent"], EDGE);
    assert_true(square.direction, edge.direction);
    assert_true(edge.direction, [0., 0., 1.]);
    assert!(sub(edge.up, [0., 0., 1.]).iter().all(|d| d.abs() < 1e-6));
    assert!(meta(TRUE)["rotation"].is_number());
    assert_ne!(meta(EDGE)["label"], meta(TRUE)["label"]);

    let d = render(&part, &a, &p);
    arrow(&d, &p, EDGE, "v:plan");
    arrow(&d, &p, TRUE, EDGE);
    for id in [EDGE, TRUE] {
        assert!(
            d.text
                .iter()
                .any(|t| t.text == format!("VIEW {}", meta(id)["label"].as_str().unwrap()))
        );
    }
    let shape = d.sheet.views.iter().find(|v| v.name == TRUE).unwrap();
    assert!(
        shape
            .edges
            .iter()
            .any(|c| matches!(c, Curve::Conic { radii, .. }
        if (radii[0] - radii[1]).abs() < 1e-6
        && (radii[0] / square.scale - 11.9928 / 2.).abs() < 1e-4))
    );
    let dimension = p
        .dimensions
        .iter()
        .find(|d| d.id == format!("d:{BORE}/bore.diameter"))
        .unwrap();
    assert_eq!(dimension.view, TRUE);
    assert!(dimension.placed.is_some());
    let camera = View::new(square.direction, square.up).unwrap();
    let tip = dimension.placed.as_ref().unwrap().arrows[0][0];
    let centre = shape
        .edges
        .iter()
        .find_map(|edge| match edge {
            Curve::Conic { centre, radii, .. }
                if (radii[0] - 11.9928 * square.scale / 2.).abs() < 1e-4
                    && ((tip[0] - centre[0]).hypot(tip[1] - centre[1]) - radii[0]).abs() < 1e-4 =>
            {
                Some(*centre)
            }
            _ => None,
        })
        .expect("the leader tip lies on the true bore rim");
    let radius = lift(&camera, page_span([centre, tip]));
    assert_true(radius, square.direction);
    assert!((dot(radius, radius).sqrt() / square.scale - 11.9928 / 2.).abs() < 1e-4);
    assert!(d.text.iter().any(|t| t.text == "TANGENT TO 3 EDGES"));

    let located: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with(&format!("d:{BORE}/location.length.")))
        .collect();
    assert_eq!(located.len(), 2);
    let station = located[0].id.clone();
    for d in located {
        assert_eq!(d.view, TRUE);
        assert!(d.placed.is_some());
        assert!((d.value - 5.9964).abs() < 1e-4);
        let (_, edge) = d.id.rsplit_once(".e").unwrap();
        let edge = edge.parse::<usize>().unwrap();
        let reference = a
            .dimension_references()
            .values()
            .flatten()
            .find_map(|r| match r {
                Reference::Plane { membership, .. } => membership
                    .edges
                    .iter()
                    .flatten()
                    .find(|e| e.index == edge)?
                    .reference
                    .as_deref(),
                _ => None,
            })
            .unwrap();
        let Reference::Edge { ends, .. } = a.dimension_reference(reference).unwrap() else {
            panic!("straight witness edge")
        };
        let span = sub(ends[1], ends[0]);
        let Reference::Edge {
            ends: [axis_at, _], ..
        } = a.dimension_reference(&format!("axis:{BORE}")).unwrap()
        else {
            panic!("bore axis")
        };
        let towards = square.direction;
        let in_plane: [f64; 3] =
            std::array::from_fn(|k| axis_at[k] - dot(sub(axis_at, ends[0]), towards) * towards[k]);
        let fraction = dot(sub(in_plane, ends[0]), span) / dot(span, span);
        let foot = std::array::from_fn(|k| ends[0][k] + fraction * span[k]);
        let distance = sub(in_plane, foot);
        assert_true(distance, square.direction);
        assert!((dot(distance, distance).sqrt() - d.value).abs() < 1e-6);
    }
    let mut cache = model::Projections::default();
    let g = model::render_geometry(&part, &a, &p, &mut cache).unwrap();
    for id in [EDGE, TRUE] {
        let view = &g.views.iter().find(|(name, _)| name == id).unwrap().1;
        assert!(!view.breaks.is_empty(), "{id} has no break line");
        assert!(view.hidden.is_empty());
        assert_fidelity(&d, id);
    }
    let issues = draftwright::lint::lint(&a, &d);
    assert!(
        issues.iter().all(|f| !f.subjects.iter().any(|s| s == BORE)),
        "{issues:?}"
    );

    let mut false_ink = d.clone();
    let item = false_ink
        .sheet
        .items
        .iter_mut()
        .find(|item| item.name == station)
        .unwrap();
    // Move the extension off its witness along the same infinite line. Its
    // distance and direction still read correctly, but it no longer reaches the part.
    let shift = page_span(item.lines[0]);
    for point in &mut item.lines[0] {
        for k in 0..2 {
            point[k] += 100. * shift[k];
        }
    }
    assert!(
        draftwright::lint::lint(&a, &false_ink)
            .iter()
            .any(|issue| issue.code == "feature_not_located"
                && issue.subjects.iter().any(|id| id == BORE)),
        "a detached auxiliary witness must lose location credit"
    );
}

#[test]
fn inscribed_bore_note_checks_both_tangency_and_edge_count() {
    let (part, a) = part("parts/corner-cuts.step.gz");
    let d = render(&part, &a, &plan(&a, jern()));
    let has_finding = |a: &Analysis, d: &Drawing| {
        draftwright::lint::lint(a, d).iter().any(|issue| {
            issue.code == "hole_tangent_note_unverifiable"
                && issue.subjects.iter().any(|id| id == BORE)
        })
    };
    assert!(!has_finding(&a, &d), "the genuine tangency is supported");
    let mut wrong_count = d.clone();
    let note = wrong_count
        .text
        .iter_mut()
        .find(|r| r.text == "TANGENT TO 3 EDGES")
        .unwrap();
    note.text = "TANGENT TO 4 EDGES".into();
    assert!(
        has_finding(&a, &wrong_count),
        "the printed edge count must be verified"
    );

    let mut non_tangent = a.clone();
    let bore = non_tangent
        .features
        .iter_mut()
        .find(|f| f.id == BORE)
        .unwrap();
    bore.parameters["diameter"] = json!(11.9828);
    assert!(
        has_finding(&non_tangent, &d),
        "tangency must agree with the bore geometry"
    );
}

#[test]
fn omitting_the_true_shape_restores_the_bores_location_finding() {
    let (part, a) = part("parts/corner-cuts.step.gz");
    let p = plan(&a, jern());
    assert!(p.views.iter().any(|v| v.id == TRUE));
    let mut omit = jern();
    omit["views"]["aux:f:hole:f10"]["omit"] = json!(true);
    let p = plan(&a, omit);
    assert!(p.views.iter().any(|v| v.id == EDGE));
    assert!(!p.views.iter().any(|v| v.id == TRUE));
    let d = render(&part, &a, &p);
    assert!(
        draftwright::lint::lint(&a, &d)
            .iter()
            .any(|f| f.code == "feature_not_located" && f.subjects.iter().any(|s| s == BORE))
    );
}

#[test]
fn unavailable_parents_report_why_the_request_cannot_draw_and_offer_a_fix() {
    let (_, a) = part("parts/corner-cuts.step.gz");
    for (edit, child, reason) in [
        (json!({"omit":true}), "plan", "hidden"),
        (json!({"omit":true}), "aux:face:4", "omitted"),
        (
            json!("aux:face:999/edge"),
            "aux:f:hole:f10",
            "does not exist",
        ),
    ] {
        let mut spec = jern();
        if edit.is_object() {
            spec["views"][child]["omit"] = edit["omit"].clone();
        } else {
            spec["views"][child]["auxiliary"]["from"] = edit;
        }
        let p = plan(&a, spec);
        let request = if child == "plan" {
            "aux:face:4"
        } else {
            "aux:f:hole:f10"
        };
        let problem = p
            .problems
            .iter()
            .find(|problem| {
                problem.code == "auxiliary_parent_unapplied"
                    && problem.refs.first().is_some_and(|name| name == request)
            })
            .expect("unavailable parent must be reported");
        assert!(problem.message.contains(reason), "{}", problem.message);
        assert_eq!(problem.fix[0].path, format!("views/{request}/omit"));
    }
}

#[test]
fn plate_pin_primary_shows_true_height_and_inclination() {
    let (part, a) = part("pmi/step/assembly_plate_pin.step.gz");
    let request = json!({"views":{"aux:f:boss:f20":{"auxiliary":{"edge_on":PIN,"from":"plan"}}}});
    let p = plan(&a, request.clone());
    let view = p
        .views
        .iter()
        .find(|v| v.id == PIN_VIEW)
        .expect("primary edge view");
    let spec_json = serde_json::to_value(view).unwrap();
    assert_eq!(spec_json["auxiliary"]["anchor"], PIN);
    assert_eq!(spec_json["auxiliary"]["parent"], "v:plan");
    assert_true(view.direction, [0., 0., 1.]);
    let Reference::Edge { ends, .. } = a.dimension_reference(&format!("axis:{PIN}")).unwrap()
    else {
        panic!("pin axis")
    };
    assert_true(sub(ends[1], ends[0]), view.direction);
    assert!(
        p.views
            .iter()
            .any(|v| v.sheet == view.sheet && v.id == "v:section_aa")
    );
    assert_eq!(spec_json["auxiliary"]["label"], "B");
    let mut detail_request = request.clone();
    detail_request["sheet"]["size"] = json!("A2");
    detail_request["views"]["detail_b"] = json!({"detail":{"around":"f:hole:f6","from":"plan"}});
    let detailed = plan(&a, detail_request);
    let detail = detailed
        .views
        .iter()
        .find(|v| v.id == "v:detail_b")
        .expect("detail B");
    let aux = detailed
        .views
        .iter()
        .find(|v| v.id == PIN_VIEW)
        .expect("auxiliary with a detail");
    assert_eq!(detail.sheet, aux.sheet);
    assert_ne!(
        serde_json::to_value(detail).unwrap()["detail"]["label"],
        serde_json::to_value(aux).unwrap()["auxiliary"]["label"]
    );

    let dimension = |suffix: &str| {
        p.dimensions
            .iter()
            .find(|d| d.view == PIN_VIEW && d.id.ends_with(suffix) && d.placed.is_some())
            .unwrap()
    };
    let height = dimension("/boss_height.length");
    let angle = dimension("/boss.inclination.angle");
    assert!((height.value - 20.).abs() < 1e-6);
    assert_eq!(angle.label, "21.9°");

    let camera = View::new(view.direction, view.up).unwrap();
    let height_ink = height.placed.as_ref().unwrap();
    let measured = lift(
        &camera,
        page_span([height_ink.arrows[0][0], height_ink.arrows[1][0]]),
    );
    let axis = sub(ends[1], ends[0]);
    assert_true(measured, view.direction);
    assert!((dot(measured, axis).abs() / dot(axis, axis).sqrt() / view.scale - 20.).abs() < 1e-6);
    let rays = [0, 1].map(|i| lift(&camera, page_span(angle.placed.as_ref().unwrap().lines[i])));
    for ray in rays {
        assert_true(ray, view.direction);
    }
    let parallel = |ray, model| {
        let cosine = dot(ray, model).abs() / (dot(ray, ray) * dot(model, model)).sqrt();
        (cosine - 1.).abs() < 1e-6
    };
    let seat = [0., 0., 1.];
    assert!(
        parallel(rays[0], seat) && parallel(rays[1], axis)
            || parallel(rays[1], seat) && parallel(rays[0], axis)
    );

    // Reorient just the seat evidence about its contact on the pin axis.
    let mut tilted = a.clone();
    let angle_from_z = 10_f64.to_radians();
    let horizontal = axis[0].hypot(axis[1]);
    for face in tilted
        .edge_ink
        .as_mut()
        .unwrap()
        .references
        .values_mut()
        .flatten()
    {
        if let Reference::Plane {
            membership,
            origin,
            normal,
            ..
        } = face
            && membership.faces == [2]
        {
            *origin = [30., 0., 5.];
            *normal = [
                axis[0] / horizontal * angle_from_z.sin(),
                axis[1] / horizontal * angle_from_z.sin(),
                angle_from_z.cos(),
            ];
        }
    }
    let tilted = plan(&tilted, request);
    let angle_to_seat = tilted
        .dimensions
        .iter()
        .find(|d| d.view == PIN_VIEW && d.id.ends_with("/boss.inclination.angle"))
        .unwrap();
    assert_eq!(
        angle_to_seat.label, "11.9°",
        "the inclination is measured from the changed seat"
    );
    assert!(angle.basis.contains("seat face normal"));
    let d = render(&part, &a, &p);
    arrow(&d, &p, PIN_VIEW, "v:plan");
    assert_fidelity(&d, PIN_VIEW);
}

#[test]
fn automatic_corner_cut_locates_and_sizes_the_bore() {
    let (part, a) = part("parts/corner-cuts.step.gz");
    let p = plan(&a, json!({}));
    let ordinary = plan(&a, json!({"purpose":{"kind":"outline"}}));
    assert_eq!(
        (ordinary.sheet.size.as_str(), ordinary.sheet.scale.as_str()),
        ("A4", "1:1")
    );
    assert_eq!(p.sheet.size, ordinary.sheet.size);
    assert_eq!(p.sheet.scale, ordinary.sheet.scale);
    assert_eq!(p.sheet.dimension_spacing, model::DimensionSpacing::Compact);
    let square = p
        .views
        .iter()
        .find(|v| v.id == TRUE)
        .expect("automatic true shape");
    let parent = &square.auxiliary.as_ref().unwrap().parent;
    let edge = p
        .views
        .iter()
        .find(|v| &v.id == parent)
        .expect("automatic primary");
    assert_eq!(edge.auxiliary.as_ref().unwrap().parent, "v:plan");
    assert_true(square.direction, edge.direction);
    assert!(p.spec.views.is_empty());
    let principal = p.views.iter().find(|v| v.id == "v:plan").unwrap();
    let projection = View::new(principal.direction, principal.up)
        .unwrap()
        .map(edge.direction);
    let shift = page_span([principal.at, edge.at]);
    let beside = (0..2).any(|axis| {
        let cross = 1 - axis;
        projection[axis] * shift[axis] > 0.
            && principal.area[cross] <= edge.at[cross]
            && edge.at[cross] <= principal.area[cross + 2]
            && if shift[axis] > 0. {
                (0.0..42.).contains(&(edge.area[axis] - principal.area[axis + 2]))
            } else {
                (0.0..42.).contains(&(principal.area[axis] - edge.area[axis + 2]))
            }
    });
    assert!(beside, "{:?} beside {:?}", edge.area, principal.area);

    let d = render(&part, &a, &p);
    arrow(&d, &p, TRUE, parent);
    assert!(
        d.sheet
            .views
            .iter()
            .find(|v| &v.name == parent)
            .unwrap()
            .edges
            .len()
            > 1
    );
    assert_fidelity(&d, TRUE);
    let stations: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| {
            d.id.starts_with(&format!("d:{BORE}/location.length."))
                && d.view == TRUE
                && d.placed.is_some()
        })
        .collect();
    assert_eq!(stations.len(), 2);
    assert!(stations.iter().all(|d| (d.value - 5.9964).abs() < 1e-4));
    let bore = p
        .dimensions
        .iter()
        .find(|d| d.id == format!("d:{BORE}/bore.diameter"))
        .unwrap();
    assert_eq!(bore.view, TRUE);
    assert_eq!(bore.label, "⌀11.993 THRU\nTANGENT TO 3 EDGES");
    let tip = bore.placed.as_ref().unwrap().arrows[0][0];
    let shape = d.sheet.views.iter().find(|v| v.name == TRUE).unwrap();
    assert!(
        shape
            .edges
            .iter()
            .any(|edge| matches!(edge, Curve::Conic { centre, radii, .. }
        if (radii[0] - radii[1]).abs() < 1e-6
        && (radii[0] / square.scale - 5.9964).abs() < 1e-4
        && ((tip[0] - centre[0]).hypot(tip[1] - centre[1]) - radii[0]).abs() < 1e-4))
    );
    assert!(
        draftwright::lint::lint(&a, &d)
            .iter()
            .all(|f| !f.subjects.iter().any(|id| id == BORE))
    );
}

#[test]
fn automatic_pin_view_shows_inclination_and_axial_height() {
    let (part, a) = part("pmi/step/assembly_plate_pin.step.gz");
    let p = plan(&a, json!({}));
    let view = p
        .views
        .iter()
        .find(|v| v.id == PIN_VIEW)
        .expect("automatic primary");
    assert_eq!(view.auxiliary.as_ref().unwrap().parent, "v:plan");
    let Reference::Edge { ends, .. } = a.dimension_reference(&format!("axis:{PIN}")).unwrap()
    else {
        panic!("pin axis");
    };
    let span = sub(ends[1], ends[0]);
    assert_true(span, view.direction);
    let on_view = |role| {
        p.dimensions
            .iter()
            .find(|d| d.id == format!("d:{PIN}/{role}") && d.view == PIN_VIEW)
            .expect("automatic auxiliary dimension")
    };
    assert_eq!(on_view("boss.inclination.angle").label, "21.9°");
    assert!(on_view("boss.inclination.angle").placed.is_some());
    let height = on_view("boss_height.length")
        .placed
        .as_ref()
        .expect("placed height");
    let camera = View::new(view.direction, view.up).unwrap();
    let measured = lift(
        &camera,
        page_span([height.arrows[0][0], height.arrows[1][0]]),
    );
    assert!((dot(measured, span).abs() / dot(span, span).sqrt() / view.scale - 20.).abs() < 1e-6);
    let d = render(&part, &a, &p);
    assert_fidelity(&d, PIN_VIEW);

    // The plate's six toleranced holes retain their complete size statement.
    let plate_holes = [
        "f:hole:f6",
        "f:hole:f7",
        "f:hole:f8",
        "f:hole:f9",
        "f:hole:f10",
        "f:hole:f12",
    ];
    let callout = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:hole_pattern:f6/bore.diameter")
        .expect("plate hole size statement");
    assert_eq!(callout.view, "v:plan");
    assert_eq!(callout.label, "6× ⌀6.6 +0.09/0 THRU");
    assert!(callout.placed.is_some());
    for id in plate_holes {
        assert!(callout.measurements.iter().any(|m| m.feature == id
            && m.parameter == "bore.diameter"
            && (m.value - 6.6).abs() < 1e-9));
    }
}

#[test]
fn automatic_coaxial_bosses_share_a_profile_view() {
    let (_, a) = part("pmi/step/assembly_plate_pin.step.gz");
    let p = plan(&a, json!({}));
    for id in [PIN, "f:boss:f24"] {
        let height = p
            .dimensions
            .iter()
            .find(|d| d.id == format!("d:{id}/boss_height.length"))
            .expect("height on each coaxial boss");
        assert_eq!(height.view, PIN_VIEW);
    }
    let pin = p.views.iter().find(|v| v.id == PIN_VIEW).unwrap();
    assert_eq!(
        p.views
            .iter()
            .filter(|v| {
                v.auxiliary.is_some() && dot(v.direction, pin.direction).abs() >= 1. - 1e-12
            })
            .count(),
        1
    );
}

#[test]
fn automatic_auxiliary_omit_restores_the_bore_finding() {
    let (part, a) = part("parts/corner-cuts.step.gz");
    assert!(plan(&a, json!({})).views.iter().any(|v| v.id == TRUE));
    let p = plan(&a, json!({"views":{"aux:f:hole:f10":{"omit":true}}}));
    assert!(p.views.iter().all(|v| v.auxiliary.is_none()));
    assert!(
        draftwright::lint::lint(&a, &render(&part, &a, &p))
            .iter()
            .any(|f| f.code == "feature_not_located" && f.subjects.iter().any(|id| id == BORE))
    );
}

#[test]
fn automatic_auxiliary_reports_when_a_fixed_sheet_cannot_hold_it() {
    let (_, a) = part("parts/corner-cuts.step.gz");
    let p = plan(&a, json!({"sheet":{"size":"A4","scale":"2:1"}}));
    assert_eq!(p.sheet.size, "A4");
    assert!(p.sheets.is_empty());
    assert!(p.views.iter().all(|v| v.id != TRUE));
    let problem = p
        .problems
        .iter()
        .find(|f| f.code == "auxiliary_unplaceable" && f.refs.iter().any(|id| id == TRUE))
        .expect("automatic auxiliary could not fit");
    assert!(!problem.fix.is_empty());
    assert!(
        p.problems
            .iter()
            .any(|f| f.code == "feature_not_dimensioned" && f.subjects.iter().any(|id| id == BORE))
    );
}

#[test]
fn automatic_auxiliaries_respect_outline_and_leave_negative_controls_unchanged() {
    let ordinary = |a: &Analysis| model::plan(a, &Spec::default()).unwrap();
    let (_, a) = part("parts/corner-cuts.step.gz");
    let outline = plan(&a, json!({"purpose":{"kind":"outline"}}));
    assert!(outline.views.iter().all(|v| v.auxiliary.is_none()));
    for path in [
        "pmi/issue505-wedge.step.gz",
        "parts/mouth_angled.step",
        "parts/issue_1247_angled_blind_step.step",
        "samples/draftwright-demo-bracket.step.gz",
    ] {
        let (_, a) = part(path);
        assert!(
            ordinary(&a).views.iter().all(|v| v.auxiliary.is_none()),
            "{path}"
        );
    }
    let corpus = std::env::var_os("DRAFTWRIGHT_CORPUS");
    if let Some(corpus) = corpus {
        for path in include_str!("../tools/quality_parts.txt").lines() {
            let (_, a) = model::analyse_native(&Path::new(&corpus).join(path)).unwrap();
            assert!(
                ordinary(&a).views.iter().all(|v| v.auxiliary.is_none()),
                "{path}"
            );
        }
    } else {
        assert_ne!(
            std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").as_deref(),
            Ok("1")
        );
    }
}
