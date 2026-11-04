//! Authored quantities use exact B-rep evidence and the ordinary editing and ink pipeline.
#[path = "common/mcp_schema.rs"]
mod mcp_schema;

use draftwright::{
    api,
    items::Op,
    model::{self, Analysis, Spec, dimension_geometry::Reference},
    session::Session,
};
use serde_json::json;
use std::path::Path;

fn plate() -> (draftwright::haecceity::Part, Analysis) {
    model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step"),
    )
    .unwrap()
}
fn parse(v: serde_json::Value) -> Spec {
    serde_json::from_value(v).unwrap()
}
fn planes(a: &Analysis, k: usize) -> Vec<(String, f64)> {
    let mut refs: Vec<_> = a
        .dimension_references()
        .iter()
        .filter_map(|(id, rs)| match rs.as_slice() {
            [Reference::Plane { origin, normal, .. }] if normal[k].abs() > 1.0 - 1e-8 => {
                Some((id.clone(), origin[k]))
            }
            _ => None,
        })
        .collect();
    refs.sort_by(|p, q| p.1.total_cmp(&q.1));
    refs
}
fn width(a: &Analysis) -> serde_json::Value {
    let refs = planes(a, 0);
    json!({"between":[refs.first().unwrap().0,refs.last().unwrap().0],"along":"x","view":"front","tolerance":"±0.1","place":{"side":"below","lane":1}})
}
fn authored(a: &Analysis) -> Spec {
    parse(json!({"dimension_source":"authored","dimensions":{"d:authored:width":width(a)}}))
}

#[test]
fn authored_face_distance_is_measured_toleranced_placed_and_round_trips() {
    let (part, a) = plate();
    let s = authored(&a);
    let p = model::plan_recovered(&a, &s).unwrap();
    let d = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:authored:width")
        .unwrap();
    assert_eq!(d.value, a.envelope[1][0] - a.envelope[0][0]);
    assert!(d.label.contains("±0.1"));
    assert!(d.placed.is_some());
    assert!(!d.default);
    assert!(d.from_spec.contains(&"between".into()));
    assert!(d.from_spec.contains(&"along".into()));
    assert!(d.from_spec.contains(&"tolerance".into()));
    assert!(
        d.measurements
            .iter()
            .any(|m| m.feature == "envelope" && m.parameter == "width.length")
    );
    assert!(p.omitted.iter().any(|o| o.id == "d:envelope/width.length"
        && matches!(&o.state,draftwright::items::State::Covered{by} if by=="d:authored:width")));
    let saved: Spec = serde_json::from_slice(&serde_json::to_vec(&p.spec).unwrap()).unwrap();
    let reloaded: Analysis = serde_json::from_slice(&serde_json::to_vec(&a).unwrap()).unwrap();
    assert_eq!(
        serde_json::to_value(&p).unwrap(),
        serde_json::to_value(model::plan_recovered(&reloaded, &saved).unwrap()).unwrap()
    );
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    let mut marks = draftwright::lint::marks(&drawing);
    assert!(
        !draftwright::lint::check(&a, &marks)
            .iter()
            .any(|i| i.code == "authored_dimension_invalid")
    );
    marks
        .iter_mut()
        .find(|m| m.name == "d:authored:width")
        .unwrap()
        .text = "999 ±0.1".into();
    assert!(
        draftwright::lint::check(&a, &marks)
            .iter()
            .any(|i| i.code == "authored_dimension_invalid")
    );
}

#[test]
fn absent_override_keeps_the_default_and_legacy_analysis_works() {
    let (_, a) = plate();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let mut legacy = a.clone();
    legacy.edge_ink.as_mut().unwrap().references.clear();
    let mut old = model::plan_recovered(&legacy, &Spec::default()).unwrap();
    // Provenance binds the entire Analysis. The new inventory must change its hash,
    // while every actual drawing decision stays exactly the same.
    assert_ne!(p.analysis_sha256, old.analysis_sha256);
    old.analysis_sha256 = p.analysis_sha256.clone();
    assert_eq!(
        serde_json::to_value(&p).unwrap(),
        serde_json::to_value(&old).unwrap()
    );
    assert!(
        model::plan_recovered(&legacy, &authored(&a))
            .unwrap_err()
            .message
            .contains("analyse the STEP again")
    );
}

#[test]
fn duplicates_are_refused_with_the_existing_owner_and_bad_references_are_actionable() {
    let (_, a) = plate();
    let mut s = authored(&a);
    s.dimension_source = model::DimensionSource::Auto;
    let e = model::plan_recovered(&a, &s).unwrap_err();
    assert!(e.message.contains("duplicates"), "{e:?}");
    assert!(!e.choices.is_empty());
    let mut s = authored(&a);
    s.dimensions
        .get_mut("d:authored:width")
        .unwrap()
        .between
        .as_mut()
        .unwrap()[0] = "face:v1:missing".into();
    let e = model::plan_recovered(&a, &s).unwrap_err();
    assert!(e.message.contains("Unknown reference"));
    assert!(e.message.contains("Analysis.edge_ink.references"));
    let mut s = authored(&a);
    s.dimensions.get_mut("d:authored:width").unwrap().along = Some("y".into());
    assert!(
        model::plan_recovered(&a, &s)
            .unwrap_err()
            .message
            .contains("no unique")
    );
}

#[test]
fn vertex_true_distance_edge_angle_and_face_angle_are_authored() {
    let (_, a) = plate();
    let points: Vec<_> = a
        .dimension_references()
        .iter()
        .filter_map(|(id, rs)| match rs.as_slice() {
            [Reference::Vertex { point }] => Some((id, point)),
            _ => None,
        })
        .collect();
    let (p, q) = points
        .iter()
        .flat_map(|p| points.iter().map(move |q| (p, q)))
        .find(|(p, q)| {
            (p.1[1] - q.1[1]).abs() < 1e-6
                && (p.1[0] - q.1[0]).abs() > 1e-3
                && (p.1[2] - q.1[2]).abs() > 1e-3
        })
        .unwrap();
    let value = ((p.1[0] - q.1[0]).powi(2) + (p.1[2] - q.1[2]).powi(2)).sqrt();
    let s = parse(
        json!({"dimensions":{"d:authored:diagonal":{"between":[p.0,q.0],"along":"true","view":"front"}}}),
    );
    let plan = model::plan_recovered(&a, &s).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:authored:diagonal")
        .unwrap();
    assert!((d.value - value).abs() < 1e-8);
    assert!(d.placed.is_some());
    let x = planes(&a, 0);
    let z = planes(&a, 2);
    let s = parse(
        json!({"dimensions":{"d:authored:angle":{"kind":"angle","between":[x[0].0,z[0].0],"view":"front"}}}),
    );
    let plan = model::plan_recovered(&a, &s).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:authored:angle")
        .unwrap();
    assert_eq!(d.kind, "angular");
    assert!((d.value - 90.0).abs() < 1e-8);
    assert!(d.placed.is_some());
    let edges: Vec<_> = a
        .dimension_references()
        .iter()
        .filter_map(|(id, rs)| match rs.as_slice() {
            [Reference::Edge { ends }] => Some((id, ends)),
            _ => None,
        })
        .collect();
    let (e, f) = edges
        .iter()
        .flat_map(|e| edges.iter().map(move |f| (e, f)))
        .find(|(e, f)| {
            e.0 != f.0 && e.1.iter().any(|p| f.1.contains(p)) && {
                let u = [0, 1, 2].map(|k| e.1[1][k] - e.1[0][k]);
                let v = [0, 1, 2].map(|k| f.1[1][k] - f.1[0][k]);
                (0..3).map(|k| u[k] * v[k]).sum::<f64>().abs() < 1e-8
            }
        })
        .unwrap();
    let s = parse(json!({"dimensions":{"d:authored:edges":{"kind":"angle","between":[e.0,f.0]}}}));
    assert!(
        model::plan_recovered(&a, &s)
            .unwrap()
            .dimensions
            .iter()
            .any(|d| d.id == "d:authored:edges" && d.placed.is_some())
    );
}

#[test]
fn session_preview_commit_undo_and_redo_create_and_remove_dimensions() {
    let (part, a) = plate();
    let value = width(&a);
    let mut session = Session::new(part, a, Spec::default()).unwrap();
    let ops = vec![
        Op::set("dimension_source", json!("authored")),
        Op::set("dimensions/d:authored:width", value),
    ];
    let before = serde_json::to_value(session.spec()).unwrap();
    session.apply(&ops, false).unwrap();
    assert_eq!(serde_json::to_value(session.spec()).unwrap(), before);
    session.apply(&ops, true).unwrap();
    assert!(
        session
            .item("d:authored:width")
            .unwrap()
            .anchors
            .iter()
            .any(|s| s.starts_with("face:v1:"))
    );
    let options = session.options("d:authored:width").unwrap();
    assert!(!options.placements.is_empty());
    assert!(
        options
            .settable
            .contains_key("dimensions/d:authored:width/between")
    );
    session.undo().unwrap();
    assert!(session.spec().dimensions.is_empty());
    session.redo().unwrap();
    assert!(session.spec().dimensions.contains_key("d:authored:width"));
    let last = serde_json::to_value(session.spec()).unwrap();
    assert!(
        session
            .apply(
                &[Op::set(
                    "dimensions/d:authored:width/between",
                    json!(["face:bad", "face:worse"])
                )],
                true
            )
            .is_err()
    );
    assert_eq!(serde_json::to_value(session.spec()).unwrap(), last);
}

#[test]
fn edit_rejects_incomplete_or_geometric_intent_and_refuses_ambiguity() {
    for entry in [
        json!({"kind":"radius","between":["a","b"]}),
        json!({"kind":"bogus","between":["a"]}),
        json!({"between":["a","b"],"along":"u"}),
        json!({"between":["a","b"],"value":10}),
    ] {
        assert!(
            model::edit(
                &Spec::default(),
                &json!([{"op":"set","path":"dimensions/d:authored:test","value":entry}])
            )
            .is_err()
        );
    }
    let (_, mut a) = plate();
    let s = authored(&a);
    let key = s.dimensions["d:authored:width"].between.as_ref().unwrap()[0].clone();
    let rs = a
        .edge_ink
        .as_mut()
        .unwrap()
        .references
        .get_mut(&key)
        .unwrap();
    rs.push(rs[0].clone());
    assert!(
        model::plan_recovered(&a, &s)
            .unwrap_err()
            .message
            .contains("ambiguous")
    );
}

#[path = "common/corpus.rs"]
mod corpus;

#[test]
fn cylinders_spheres_and_axis_offsets_are_measured_and_linted() {
    let fixture =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step");
    let (part, a) = model::analyse_native(&fixture).unwrap();
    let (cylinder, radius) = a
        .dimension_references()
        .iter()
        .find_map(|(id, rs)| match rs.as_slice() {
            [Reference::Cylinder { radius, .. }] => Some((id.clone(), *radius)),
            _ => None,
        })
        .unwrap();
    for (kind, value) in [("diameter", 2.0 * radius), ("radius", radius)] {
        let s = parse(
            json!({"dimension_source":"authored","dimensions":{"d:authored:size":{"kind":kind,"between":[cylinder],"tolerance":"±0.05"}}}),
        );
        let p = model::plan_recovered(&a, &s).unwrap();
        let d = p
            .dimensions
            .iter()
            .find(|d| d.id == "d:authored:size")
            .unwrap();
        assert!((d.value - value).abs() < 1e-8);
        assert!(d.placed.is_some());
        assert!(d.label.contains("±0.05"));
        let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
        let drawing = model::render(&part, &a, &p, &font).unwrap();
        assert!(
            !draftwright::lint::lint(&a, &drawing)
                .iter()
                .any(|i| i.code == "authored_dimension_invalid")
        );
        let mut bad = draftwright::lint::marks(&drawing);
        bad.iter_mut()
            .find(|m| m.name == "d:authored:size")
            .unwrap()
            .text = "R999 ±0.05".into();
        assert!(
            draftwright::lint::check(&a, &bad)
                .iter()
                .any(|i| i.code == "authored_dimension_invalid")
        );
        let mut duplicate = s.clone();
        duplicate.dimension_source = model::DimensionSource::Auto;
        assert!(
            model::plan_recovered(&a, &duplicate)
                .unwrap_err()
                .message
                .contains("duplicates")
        );
    }
    let x = planes(&a, 0);
    let s = parse(
        json!({"dimension_source":"authored","dimensions":{"d:authored:offset":{"between":[x[0].0,format!("axis:{cylinder}")],"along":"x"}}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.id == "d:authored:offset" && d.placed.is_some())
    );
    let Some(corpus) = corpus::dir() else { return };
    let (_, sphere) = model::analyse_native(&corpus.join("nist/nist_ctc_04_asme1_rd.stp")).unwrap();
    let (id, radius) = sphere
        .dimension_references()
        .iter()
        .find_map(|(id, rs)| match rs.as_slice() {
            [Reference::Sphere { radius, .. }] => Some((id, *radius)),
            _ => None,
        })
        .expect("ctc04 spherical face");
    for (kind, expected) in [("radius", radius), ("diameter", radius * 2.0)] {
        let s = parse(
            json!({"dimension_source":"authored","dimensions":{"d:authored:sphere":{"kind":kind,"between":[id]}}}),
        );
        let p = model::plan_recovered(&sphere, &s).unwrap();
        let d = p
            .dimensions
            .iter()
            .find(|d| d.id == "d:authored:sphere")
            .unwrap();
        assert_eq!(d.value, expected);
        assert!(d.placed.is_some());
    }
}

#[test]
fn authored_precision_pins_and_duplicate_authored_quantities() {
    let (_, a) = plate();
    let mut s = authored(&a);
    s.precision.insert("d:authored:width".into(), 3);
    s.dimensions
        .get_mut("d:authored:width")
        .unwrap()
        .place
        .as_mut()
        .unwrap()
        .pinned = true;
    let p = model::plan_recovered(&a, &s).unwrap();
    let d = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:authored:width")
        .unwrap();
    assert!(
        d.label.starts_with(&format!("{:.3}", d.value)),
        "{}",
        d.label
    );
    assert!(d.placed.is_some());
    s.dimensions.insert(
        "d:authored:second".into(),
        s.dimensions["d:authored:width"].clone(),
    );
    assert!(
        model::plan_recovered(&a, &s)
            .unwrap_err()
            .message
            .contains("duplicates")
    );
}

#[test]
fn authored_linear_fit_is_class_only_and_requires_one_tolerance_owner() {
    let (_, a) = plate();
    let mut s = authored(&a);
    let d = s.dimensions.get_mut("d:authored:width").unwrap();
    d.tolerance = None;
    d.fit = Some("H7".into());
    let p = model::plan_recovered(&a, &s).unwrap();
    let d = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:authored:width")
        .unwrap();
    assert!(d.label.ends_with(" H7"), "{}", d.label);
    assert!(d.from_spec.contains(&"fit".into()));
    s.dimensions.get_mut("d:authored:width").unwrap().tolerance = Some("±0.1".into());
    assert!(model::plan_recovered(&a, &s).is_err());
}

#[test]
fn saved_v1_reference_keys_keep_resolving() {
    let (_, a) = plate();
    let saved: Spec =
        serde_json::from_str(include_str!("fixtures/identity/authored.spec2.json")).unwrap();
    let p = model::plan_recovered(&a, &saved).unwrap();
    let d = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:authored:width")
        .unwrap();
    assert_eq!(d.value, 60.0);
    assert_eq!(d.label, "60 ±0.1");
    assert!(d.placed.is_some());
}

#[test]
fn obtuse_face_and_edge_angles_keep_the_physical_included_sector() {
    let (_, mut a) = plate();
    let height = 5.0 * 3.0f64.sqrt();
    let records = a.edge_ink.as_mut().unwrap();
    records.references.insert(
        "face:test-horizontal".into(),
        vec![Reference::Plane {
            origin: [0.0; 3],
            normal: [0.0, 0.0, 1.0],
            bounds: [[0.0, -1.0, 0.0], [10.0, 1.0, 0.0]],
        }],
    );
    records.references.insert(
        "face:test-inclined".into(),
        vec![Reference::Plane {
            origin: [0.0; 3],
            normal: [3.0f64.sqrt() / 2.0, 0.0, 0.5],
            bounds: [[-5.0, -1.0, 0.0], [0.0, 1.0, height]],
        }],
    );
    records.references.insert(
        "edge:test-horizontal".into(),
        vec![Reference::Edge {
            ends: [[0.0; 3], [10.0, 0.0, 0.0]],
        }],
    );
    records.references.insert(
        "edge:test-inclined".into(),
        vec![Reference::Edge {
            ends: [[-5.0, 0.0, height], [0.0; 3]],
        }],
    );
    for prefix in ["face", "edge"] {
        let refs = vec![
            format!("{prefix}:test-horizontal"),
            format!("{prefix}:test-inclined"),
        ];
        assert!((a.measure_references(&refs, "angle", None).unwrap().value - 120.0).abs() < 1e-9);
        let s = parse(
            json!({"dimensions":{"d:authored:obtuse":{"kind":"angle","between":refs,"view":"front"}}}),
        );
        let p = model::plan_recovered(&a, &s).unwrap();
        let d = p
            .dimensions
            .iter()
            .find(|d| d.id == "d:authored:obtuse")
            .unwrap();
        assert!((d.value - 120.0).abs() < 1e-9);
        assert_eq!(d.label, "120°");
        assert!(d.placed.is_some());
    }
}

#[test]
fn angular_editor_options_offer_radial_placement_without_inapplicable_sides() {
    let (part, a) = plate();
    let x = planes(&a, 0);
    let z = planes(&a, 2);
    let s = parse(
        json!({"dimensions":{"d:authored:angle":{"kind":"angle","between":[x[0].0,z.last().unwrap().0],"view":"front"}}}),
    );
    let session = Session::new(part, a, s).unwrap();
    let options = session.options("d:authored:angle").unwrap();
    assert!(!options.placements.is_empty());
    assert!(
        options
            .placements
            .iter()
            .all(|p| p.side.is_none() && p.view.as_deref() == Some("front"))
    );
    assert_eq!(
        options.settable["dimensions/d:authored:angle/place/side"]["choices"],
        json!([])
    );
    assert_eq!(
        options.settable["dimensions/d:authored:angle/place/view"]["choices"],
        json!(["front"])
    );
}

#[test]
fn authored_corner_angle_keeps_the_plate_sheet_and_scale() {
    let (_, a) = plate();
    let before = model::plan_recovered(&a, &Spec::default()).unwrap();
    let x = planes(&a, 0);
    let z = planes(&a, 2);
    let s = parse(json!({"dimensions":{"d:authored:corner":{
        "kind":"angle", "between":[x[0].0,z.last().unwrap().0],
        "view":"front", "tolerance":"±0.5"
    }}}));
    let after = model::plan_recovered(&a, &s).unwrap();
    assert_eq!(before.sheet.size, "A4");
    assert_eq!(after.sheet.size, before.sheet.size);
    assert_eq!(
        after.sheet.scale, before.sheet.scale,
        "{:?}",
        after.problems
    );
    let d = after
        .dimensions
        .iter()
        .find(|d| d.id == "d:authored:corner")
        .unwrap();
    let p = d.placed.as_ref().unwrap();
    // A corner arc must remain local, regardless of how far away the face centres are.
    let extent = |k: usize| {
        let coords: Vec<_> = p.lines.iter().flatten().map(|q| q[k]).collect();
        coords.iter().copied().fold(f64::NEG_INFINITY, f64::max)
            - coords.iter().copied().fold(f64::INFINITY, f64::min)
    };
    assert!(extent(0) < 30.0 && extent(1) < 30.0, "{p:?}");
    let front = after.views.iter().find(|v| v.id == "v:front").unwrap();
    let half = [
        0.5 * front.scale * (a.envelope[1][0] - a.envelope[0][0]),
        0.5 * front.scale * (a.envelope[1][2] - a.envelope[0][2]),
    ];
    for q in p.lines.iter().skip(2).flatten() {
        assert!(
            q[0] <= front.at[0] - half[0]
                || q[0] >= front.at[0] + half[0]
                || q[1] <= front.at[1] - half[1]
                || q[1] >= front.at[1] + half[1],
            "the corner arc enters the outline: {q:?}"
        );
    }

    assert!(
        !after.problems.iter().any(|p| p.severity == "error"),
        "{:?}",
        after.problems
    );
}

#[test]
fn authored_corner_on_fixed_paper_spends_only_remaining_gutter_slack() {
    let (_, a) = plate();
    let x = planes(&a, 0);
    let z = planes(&a, 2);
    for gutter in [0, 25, 1000] {
        let spec = parse(json!({
            "sheet":{"size":"A4","scale":"2:1","view_gutter":gutter},
            "dimensions":{"d:authored:corner":{
                "kind":"angle", "between":[x[0].0,z.last().unwrap().0],
                "view":"front", "tolerance":"±0.5"
            }}
        }));
        let p = model::plan_recovered(&a, &spec).unwrap();
        assert_eq!((&*p.sheet.size, &*p.sheet.scale), ("A4", "2:1"));
        assert!(
            !p.problems
                .iter()
                .any(|p| p.code == "page_fit_uncertain" || p.code == "annotation_off_sheet"),
            "gutter {gutter}: {:?}",
            p.problems
        );
    }
}

// Independent of the placer's collision helper: include the rendered arrow flares.
fn dimension_strokes(p: &draftwright::dims::Placed) -> Vec<[[f64; 2]; 2]> {
    let mut lines = p.lines.clone();
    for [tip, tail] in &p.arrows {
        let d = [tail[0] - tip[0], tail[1] - tip[1]];
        let k = 0.45 / d[0].hypot(d[1]);
        let left = [tail[0] - k * d[1], tail[1] + k * d[0]];
        let right = [tail[0] + k * d[1], tail[1] - k * d[0]];
        lines.extend([[*tip, left], [left, right], [right, *tip]]);
    }
    lines
}

fn strokes_cross(a: [[f64; 2]; 2], b: [[f64; 2]; 2]) -> bool {
    let orient = |p: [f64; 2], q: [f64; 2], r: [f64; 2]| {
        (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    };
    orient(a[0], a[1], b[0]) * orient(a[0], a[1], b[1]) < -1e-12
        && orient(b[0], b[1], a[0]) * orient(b[0], b[1], a[1]) < -1e-12
}

#[test]
fn authored_aligned_span_clears_dimension_lines_extensions_and_arrowheads() {
    let (_, a) = plate();
    let vertices: Vec<_> = a
        .dimension_references()
        .iter()
        .filter_map(|(id, rs)| match rs.as_slice() {
            [Reference::Vertex { point }] => Some((id, point)),
            _ => None,
        })
        .collect();
    let (p, q) = vertices
        .iter()
        .flat_map(|p| vertices.iter().map(move |q| (p, q)))
        .find(|(p, q)| {
            (p.1[1] - q.1[1]).abs() < 1e-7
                && (p.1[0] - q.1[0]).abs() > 1e-3
                && (p.1[2] - q.1[2]).abs() > 1e-3
        })
        .unwrap();
    for place in [json!(null), json!({"side":"below","lane":1})] {
        let s = parse(
            json!({"sheet":{"size":"A4"},"dimensions":{"d:authored:diagonal":{
                "between":[p.0,q.0], "along":"true", "view":"front",
                "tolerance":"±0.1", "place":place
            }}}),
        );
        let plan = model::plan_recovered(&a, &s).unwrap();
        let diagonal = plan
            .dimensions
            .iter()
            .find(|d| d.id == "d:authored:diagonal")
            .unwrap();
        let placed = diagonal.placed.as_ref().unwrap();
        assert_eq!(plan.sheet.size, "A4");
        let strokes = dimension_strokes(placed);
        for other in plan.dimensions.iter().filter(|d| d.id != diagonal.id) {
            let Some(other_placed) = &other.placed else {
                continue;
            };
            for line in &strokes {
                for other_line in dimension_strokes(other_placed) {
                    assert!(
                        !strokes_cross(*line, other_line),
                        "diagonal crosses {}: {line:?}, {other_line:?}",
                        other.id
                    );
                }
            }
        }
        // The parallel offset should use short perpendicular witnesses. The unpinned
        // below/lane-1 hint found that side obstructed until the 3 mm frame gutter (2026-10-25)
        // re-spaced the plate; it is now honoured, one lane (7 mm) further out.
        let limit = if place.is_null() { 20.0 } else { 20.0 + 7.0 };
        for [a, b] in placed.lines.iter().take(2) {
            assert!((b[0] - a[0]).hypot(b[1] - a[1]) < limit, "{placed:?}");
        }
    }
}

#[test]
fn authored_dimensions_keep_label_overrides_geometry_schema_and_live_lint() {
    let (part, a) = plate();
    let base = authored(&a);
    let original = model::plan_recovered(&a, &base).unwrap();
    let old = original
        .dimensions
        .iter()
        .find(|d| d.id == "d:authored:width")
        .unwrap();
    let schema = mcp_schema::with_full_spec("get_spec", true);
    for style in ["basic", "reference"] {
        let mut value = serde_json::to_value(&base).unwrap();
        let entry = &mut value["dimensions"]["d:authored:width"];
        entry["kind"] = json!("linear");
        entry["text"] = json!({"prefix":"TYP", "suffix":"AFTER PLATING"});
        entry["style"] = json!(style);
        draftwright::mcp::validate(&schema, &json!({"spec_version":"v", "spec":value})).unwrap();
        for (field, bad) in [
            ("kind", json!("bad")),
            ("between", json!([])),
            ("along", json!("bad")),
            ("text", json!({"suffix":42})),
            ("style", json!("bad")),
        ] {
            let mut invalid = value.clone();
            invalid["dimensions"]["d:authored:width"][field] = bad;
            assert!(
                draftwright::mcp::validate(&schema, &json!({"spec_version":"v", "spec":invalid}))
                    .is_err(),
                "{field}"
            );
        }
        let spec: Spec = serde_json::from_value(value).unwrap();
        let saved: Spec = serde_json::from_slice(&serde_json::to_vec(&spec).unwrap()).unwrap();
        assert_eq!(spec, saved);
        let plan = model::plan_recovered(&a, &saved).unwrap();
        let d = plan.dimensions.iter().find(|d| d.id == old.id).unwrap();
        let label = format!("TYP {} AFTER PLATING", old.label);
        assert_eq!(
            d.label,
            if style == "reference" {
                format!("({label})")
            } else {
                label
            }
        );
        assert_eq!(d.value, old.value);
        assert_eq!(d.measurements, old.measurements);
        assert!(d.placed.is_some());
        for field in [
            "kind",
            "between",
            "along",
            "text",
            &format!("style.{style}"),
        ] {
            assert!(d.from_spec.iter().any(|f| f == field), "{field}");
        }
        let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        let mut marks = draftwright::lint::marks(&drawing);
        assert!(
            !draftwright::lint::check(&a, &marks)
                .iter()
                .any(|i| i.code == "authored_dimension_invalid")
        );
        assert!(
            !draftwright::lint::structural::check(&drawing)
                .iter()
                .any(|i| i.code == "label_vs_measured")
        );
        marks.iter_mut().find(|m| m.name == old.id).unwrap().text = "999 ±0.1".into();
        assert!(
            draftwright::lint::check(&a, &marks)
                .iter()
                .any(|i| i.code == "authored_dimension_invalid")
        );
    }
}

/// The independent fixture has a 40-long pocket starting 40 from the left.
/// Its centre at 60 is deliberately absent from the authored drawing.
fn pocket_fixture() -> (draftwright::haecceity::Part, Analysis, String) {
    let (part, a) = model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/location/pocket.step"),
    )
    .unwrap();
    assert!(!draftwright::lint::location::coverage(&a, &[]).is_empty());
    let pocket = draftwright::lint::pockets::placed(&a);
    assert_eq!(pocket.len(), 1);
    let (f, p) = &pocket[0];
    let p = p.as_ref().unwrap();
    assert_eq!((p.lo, p.hi, p.width), (40., 80., 6.));
    (part, a.clone(), f.id.clone())
}

fn pocket_spec(a: &Analysis, id: &str, compound: bool) -> Spec {
    let x = planes(a, 0);
    let y = planes(a, 1);
    let face = |refs: &[(String, f64)], at: f64| {
        refs.iter()
            .find(|(_, v)| (v - at).abs() < 1e-6)
            .unwrap()
            .0
            .clone()
    };
    let mut dims = json!({
        "d:authored:start": {"between": [face(&x, 0.), face(&x, 40.)], "along": "x", "view": "plan"},
        format!("d:{id}/location_pocket.location.y"): {}
    });
    if compound {
        dims[format!("d:{id}/pocket_width.length")] = json!({});
    } else {
        dims["d:authored:length"] =
            json!({"between": [face(&x, 40.), face(&x, 80.)], "along": "x", "view": "plan"});
        dims["d:authored:width"] =
            json!({"between": [face(&y, 17.), face(&y, 23.)], "along": "y", "view": "plan"});
    }
    parse(json!({"dimension_source": "authored", "dimensions": dims, "views": {"plan": {}}}))
}

fn pocket_requirements(a: &Analysis, drawing: &draftwright::output::Drawing) -> Vec<String> {
    draftwright::lint::lint(a, drawing)
        .into_iter()
        .filter(|i| i.code.starts_with("pocket_requirement_"))
        .flat_map(|i| i.requirements.into_iter().map(|r| r.parameter))
        .collect()
}

#[test]
fn printed_start_and_compound_or_authored_length_fix_the_pocket_centre_without_pmi() {
    let (part, a, id) = pocket_fixture();
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    for compound in [true, false] {
        let spec = pocket_spec(&a, &id, compound);
        let plan = model::plan_recovered(&a, &spec).unwrap();
        assert!(
            !plan
                .dimensions
                .iter()
                .any(|d| d.id == format!("d:{id}/location_pocket.location.x"))
        );
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        assert_eq!(
            pocket_requirements(&a, &drawing),
            if compound {
                vec![]
            } else {
                vec!["pocket_depth.length"]
            }
        );
        let findings = draftwright::lint::lint(&a, &drawing);
        assert!(
            !findings
                .iter()
                .any(|i| i.code == "authored_dimension_invalid")
        );
        assert!(
            !findings
                .iter()
                .flat_map(|i| &i.requirements)
                .any(|r| r.feature.starts_with("station_")),
            "local graph bookkeeping is not a repeated physical measurement"
        );
    }
}

#[test]
fn a_pocket_end_alone_or_length_alone_does_not_fix_its_centre() {
    let (part, a, id) = pocket_fixture();
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    for compound in [true, false] {
        let spec = pocket_spec(&a, &id, compound);
        for remove in [
            "d:authored:start".to_string(),
            if compound {
                format!("d:{id}/pocket_width.length")
            } else {
                "d:authored:length".into()
            },
        ] {
            let mut missing = spec.clone();
            missing.dimensions.remove(&remove);
            let plan = model::plan_recovered(&a, &missing).unwrap();
            let drawing = model::render(&part, &a, &plan, &font).unwrap();
            assert!(
                pocket_requirements(&a, &drawing).contains(&"location_pocket.location.x".into()),
                "{compound}: removed {remove}"
            );
        }
    }
}

#[test]
fn reference_wrong_nominal_or_wrong_witnesses_do_not_locate_a_pocket() {
    let (part, a, id) = pocket_fixture();
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    let spec = pocket_spec(&a, &id, false);
    for reference in ["d:authored:start", "d:authored:length"] {
        let mut s = spec.clone();
        s.dimensions.get_mut(reference).unwrap().style =
            Some(serde_json::from_value(json!("reference")).unwrap());
        let plan = model::plan_recovered(&a, &s).unwrap();
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        assert!(pocket_requirements(&a, &drawing).contains(&"location_pocket.location.x".into()));
    }
    let plan = model::plan_recovered(&a, &spec).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let mut wrong = drawing.clone();
    let runs = wrong
        .annotations
        .iter()
        .find(|d| d.name == "d:authored:start")
        .unwrap()
        .runs
        .clone();
    for run in runs {
        wrong.text[run].text = "99 ±40".into();
    }
    assert!(pocket_requirements(&a, &wrong).contains(&"location_pocket.location.x".into()));
    let mut wrong = drawing;
    let item = wrong
        .sheet
        .items
        .iter_mut()
        .find(|d| d.name == "d:authored:start")
        .unwrap();
    for line in &mut item.lines {
        for p in line {
            p[0] += 2.;
        }
    }
    assert!(pocket_requirements(&a, &wrong).contains(&"location_pocket.location.x".into()));
}

#[test]
fn authored_axis_chain_locates_an_unpatterned_hole_without_pmi() {
    let (part, a) = model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/scattered_holes.step"),
    )
    .unwrap();
    let x = planes(&a, 0);
    let spec = parse(json!({"dimension_source":"authored", "dimensions": {
        "d:authored:start": {"between":[x[0].0, "axis:f:hole:1"], "along":"x", "view":"plan"},
        "d:authored:link": {"between":["axis:f:hole:1", "axis:f:hole:2"], "along":"x", "view":"plan"}
    }}));
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    for remove in [None, Some("d:authored:start"), Some("d:authored:link")] {
        let mut s = spec.clone();
        if let Some(id) = remove {
            s.dimensions.remove(id);
        }
        let plan = model::plan_recovered(&a, &s).unwrap();
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        let issues = draftwright::lint::lint(&a, &drawing);
        assert_eq!(
            issues
                .iter()
                .filter(|i| i.code == "feature_not_located")
                .flat_map(|i| &i.requirements)
                .any(|r| r.feature == "f:hole:2" && r.parameter == "location.length.x"),
            remove.is_some(),
            "{remove:?}"
        );
        assert!(
            issues
                .iter()
                .filter(|i| i.code == "feature_not_located")
                .flat_map(|i| &i.requirements)
                .any(|r| r.feature == "f:hole:2" && r.parameter == "location.length.y")
        );
    }
}

#[test]
fn an_engine_coordinate_uses_the_axis_instead_of_a_trimmed_face_fingerprint() {
    let (_, mut a) = model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/scattered_holes.step"),
    )
    .unwrap();
    // The STEP's axis is x=-71, 49 from x=-120. A trimmed bore's
    // fingerprint centre need not lie on that analytic axis (CTC-02/FTC-08/FTC-09).
    let f = a.features.iter_mut().find(|f| f.id == "f:hole:1").unwrap();
    f.fingerprint.position[0] += 0.03;
    for (text, missing) in [("49", false), ("999 ±49", true)] {
        let mark = draftwright::lint::Mark {
            name: "hole_schedule_test/x".into(),
            view: Some("v:plan".into()),
            text: text.into(),
            claims: vec![draftwright::output::Claim {
                feature: "f:hole:1".into(),
                parameter: "location.length.x".into(),
            }],
        };
        let issues = draftwright::lint::check(&a, &[mark]);
        assert_eq!(
            issues
                .iter()
                .filter(|i| i.code == "feature_not_located")
                .flat_map(|i| &i.requirements)
                .any(|r| r.feature == "f:hole:1" && r.parameter == "location.length.x"),
            missing
        );
        assert!(
            issues
                .iter()
                .filter(|i| i.code == "feature_not_located")
                .flat_map(|i| &i.requirements)
                .any(|r| r.feature == "f:hole:1" && r.parameter == "location.length.y")
        );
    }
}

#[test]
fn a_pocket_midplane_never_locates_another_pocket_or_a_coincident_step_face() {
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    for fixture in ["two_pockets.step", "two_pockets_step.step"] {
        let (part, a) = model::analyse_native(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts/location")
                .join(fixture),
        )
        .unwrap();
        let x = planes(&a, 0);
        let y = planes(&a, 1);
        let face = |refs: &[(String, f64)], at: f64| {
            refs.iter()
                .find(|(_, v)| (v - at).abs() < 1e-6)
                .unwrap()
                .0
                .clone()
        };
        let dims = json!({
            "d:authored:start": {"between": [face(&x, 0.), face(&x, 40.)], "along": "x", "view": "plan"},
            "d:authored:length": {"between": [face(&x, 40.), face(&x, 80.)], "along": "x", "view": "plan"},
            "d:authored:width": {"between": [face(&y, 17.), face(&y, 23.)], "along": "y", "view": "plan"},
            "d:authored:y1": {"between": [face(&y, 0.), face(&y, 17.)], "along": "y", "view": "plan"},
            "d:authored:len2": {"between": [face(&x, 50.), face(&x, 70.)], "along": "x", "view": "plan"},
            "d:authored:width2": {"between": [face(&y, 27.), face(&y, 33.)], "along": "y", "view": "plan"},
            "d:authored:y2": {"between": [face(&y, 0.), face(&y, 27.)], "along": "y", "view": "plan"}
        });
        let pockets = draftwright::lint::pockets::placed(&a);
        let p1 = &pockets
            .iter()
            .find(|(_, p)| p.as_ref().is_ok_and(|p| (p.lo - 40.).abs() < 1e-6))
            .unwrap()
            .0
            .id;
        let p2 = &pockets
            .iter()
            .find(|(_, p)| p.as_ref().is_ok_and(|p| (p.lo - 50.).abs() < 1e-6))
            .unwrap()
            .0
            .id;
        for removed in [None, Some("d:authored:length"), Some("d:authored:start")] {
            let mut spec = parse(
                json!({"dimension_source":"authored", "dimensions":dims, "views":{"plan":{}}}),
            );
            if let Some(id) = removed {
                spec.dimensions.remove(id);
            }
            let plan = model::plan_recovered(&a, &spec).unwrap();
            let drawing = model::render(&part, &a, &plan, &font).unwrap();
            let issues = draftwright::lint::lint(&a, &drawing);
            let missing = |id: &str| {
                issues.iter().any(|i| {
                    i.code == "pocket_requirement_missing"
                        && i.requirements
                            .iter()
                            .any(|r| r.feature == id && r.parameter == "location_pocket.location.x")
                })
            };
            assert!(
                missing(p2),
                "{fixture}, removed {removed:?}: P2 has no start"
            );
            assert_eq!(
                missing(p1),
                removed.is_some(),
                "{fixture}, removed {removed:?}"
            );
            if fixture == "two_pockets_step.step" {
                let step_faces: Vec<_> = a
                    .face_attachments
                    .iter()
                    .enumerate()
                    .filter(|(_, f)| {
                        f.axis == Some(0)
                            && f.bounds.is_some_and(|b| {
                                (b[0] - 60.).abs() < 1e-6 && (b[3] - 60.).abs() < 1e-6
                            })
                    })
                    .map(|(i, _)| format!("face:{i}"))
                    .collect();
                assert!(!step_faces.is_empty());
                assert!(
                    issues.iter().any(|i| i.code == "feature_not_located"
                        && i.requirements.iter().any(|r| r.feature == "step_level"
                            && r.parameter == "step_position.length.x60")),
                    "the actual step at 60 must remain undimensioned: {issues:?}"
                );
            }
        }
    }
}

#[test]
fn removing_engine_ink_without_replanning_exposes_missing_geometry() {
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    let (pocket, a, _) = pocket_fixture();
    for (part, a) in [plate(), (pocket, a)] {
        let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
        for removed in plan
            .dimensions
            .iter()
            .filter(|d| !d.reference && d.placed.is_some())
        {
            let mut mutation = plan.clone();
            mutation.dimensions.retain(|d| d.id != removed.id);
            let drawing = model::render(&part, &a, &mutation, &font).unwrap();
            let issues = draftwright::lint::lint(&a, &drawing);
            assert!(
                issues.iter().any(|i| (i.code.contains("missing")
                    || i.code == "feature_not_located")
                    && i.requirements.iter().any(|r| removed
                        .measurements
                        .iter()
                        .any(|m| m.feature == r.feature && m.parameter == r.parameter))),
                "removed {} must expose its own geometric role: {issues:?}",
                removed.id
            );
        }
    }
}

#[test]
fn another_pockets_half_length_is_not_a_dimension_between_this_pockets_faces() {
    let (part, a) = model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/location/two_pockets_sizes.step"),
    )
    .unwrap();
    let pockets = draftwright::lint::pockets::placed(&a);
    let first = pockets
        .iter()
        .find(|(_, p)| p.as_ref().is_ok_and(|p| (p.lo - 40.).abs() < 1e-6))
        .unwrap()
        .0;
    let second = pockets
        .iter()
        .find(|(_, p)| p.as_ref().is_ok_and(|p| (p.lo - 60.).abs() < 1e-6))
        .unwrap()
        .0;
    let refs = planes(&a, 0);
    let face = |owner: Option<&draftwright::bridge::Feature>, at: f64| {
        refs.iter()
            .find(|(key, v)| {
                (v - at).abs() < 1e-6
                    && owner.is_none_or(|f| {
                        a.dimension_reference_face(key, 0)
                            .is_some_and(|i| f.faces.contains(&i))
                    })
            })
            .unwrap()
            .0
            .clone()
    };
    let spec = parse(
        json!({"dimension_source":"authored", "views":{"plan":{}}, "dimensions":{
            "d:authored:start":{"between":[face(None,0.),face(Some(first),40.)],"along":"x","view":"plan"},
            "d:authored:length":{"between":[face(Some(first),40.),face(Some(first),80.)],"along":"x","view":"plan"},
            format!("d:{}/location_pocket.location.x",second.id):{}
        }}),
    );
    let plan = model::plan_recovered(&a, &spec).unwrap();
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let issues = draftwright::lint::lint(&a, &drawing);
    let missing = |id: &str| {
        issues.iter().any(|i| {
            i.code == "pocket_requirement_missing"
                && i.requirements
                    .iter()
                    .any(|r| r.feature == id && r.parameter == "pocket_length.length")
        })
    };
    assert!(
        !missing(&first.id),
        "the first pocket has its printed 40 length"
    );
    assert!(
        missing(&second.id),
        "the second pocket's 20 is not the first pocket's half interval"
    );
}
