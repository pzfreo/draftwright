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
    assert!(d.text.iter().any(|t| t.text == "⌀ TANGENT TO 3 EDGES"));

    let located: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with(&format!("d:{BORE}/location.length.")))
        .collect();
    assert_eq!(located.len(), 2);
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
    let p = plan(
        &a,
        json!({"views":{"aux:f:boss:f20":{"auxiliary":{"edge_on":PIN,"from":"plan"}}}}),
    );
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
    let dimensions: Vec<_> = p.dimensions.iter().filter(|d| d.view == PIN_VIEW).collect();
    assert!(
        dimensions
            .iter()
            .any(|d| d.id.ends_with("/boss_height.length")
                && (d.value - 20.).abs() < 1e-6
                && d.placed.is_some())
    );
    assert!(
        dimensions
            .iter()
            .any(|d| d.id.ends_with("/boss.inclination.angle")
                && d.label == "21.9°"
                && d.placed.is_some())
    );
    let d = render(&part, &a, &p);
    arrow(&d, &p, PIN_VIEW, "v:plan");
    assert_fidelity(&d, PIN_VIEW);
}
