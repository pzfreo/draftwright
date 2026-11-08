//! Issue #76 item 2: author intent changes the inventory, never the measuring/rendering path.
#[path = "common/mcp_schema.rs"]
mod mcp_schema;

use std::borrow::Cow;
use std::path::Path;

use draftwright::items::Op;
use draftwright::model::{self, Analysis, Spec};
use draftwright::recognition_corrections::{self as corrections, face_fingerprint};
use draftwright::{Session, api, haecceity::Part, lint};
use serde_json::{Value, json};

fn load(name: &str) -> (Part, Analysis) {
    model::analyse_native(&Path::new(env!("CARGO_MANIFEST_DIR")).join(name)).unwrap()
}
fn plate() -> (Part, Analysis) {
    load("tests/fixtures/parts/mounting_plate.step")
}
fn spec(value: Value) -> Spec {
    serde_json::from_value(value).unwrap()
}
fn font() -> draftwright::text::Font {
    draftwright::text::Font::new(api::MONO.to_vec()).unwrap()
}
fn saved(s: &Spec) -> Spec {
    serde_json::from_slice(&serde_json::to_vec(s).unwrap()).unwrap()
}

#[test]
fn absent_corrections_borrow_the_inventory_and_keep_default_bytes() {
    let (_, a) = plate();
    let default = Spec::default();
    assert!(matches!(
        corrections::effective(&a, &default).unwrap(),
        Cow::Borrowed(_)
    ));
    let p = model::plan_recovered(&a, &default).unwrap();
    let json = serde_json::to_value(&p).unwrap();
    assert!(json.get("recognition_corrections").is_none());
    assert!(
        !serde_json::to_string(&default)
            .unwrap()
            .contains("feature_declarations")
    );
    let explicit = spec(
        json!({"feature_declarations":[],"features":{"f:hole:f6":{"suppress":false,"split":false}}}),
    );
    let mut explicit = model::plan_recovered(&a, &explicit).unwrap();
    explicit.spec = p.spec.clone();
    assert_eq!(serde_json::to_value(explicit).unwrap(), json);
}

#[test]
fn suppression_removes_dimensions_and_lint_requirements_but_does_not_hide_other_findings() {
    let (part, a) = plate();
    let hole = a.features.iter().find(|f| f.kind == "hole").unwrap();
    let s = spec(json!({"features":{&hole.id:{"suppress":true}}}));
    let p = model::plan_recovered(&a, &s).unwrap();
    assert_eq!(
        p.recognition_corrections[0].from_spec,
        [format!("features/{}/suppress", hole.id)]
    );
    assert!(p.spec.features[&hole.id].anchor.is_some());
    assert!(
        p.dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .all(|m| m.feature != hole.id)
    );
    let effective = corrections::effective(&a, &p.spec).unwrap();
    assert!(effective.features.iter().all(|f| f.id != hole.id));
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    assert!(
        lint::lint(&effective, &drawing)
            .iter()
            .all(|i| !i.subjects.contains(&hole.id))
    );
    assert!(
        !lint::check(&effective, &[]).is_empty(),
        "unsuppressed requirements still judged"
    );
    let replay = model::plan_recovered(&a, &saved(&p.spec)).unwrap();
    assert_eq!(
        serde_json::to_value(p).unwrap(),
        serde_json::to_value(replay).unwrap()
    );
}

#[test]
fn recognition_binding_and_authored_dimension_validation_share_the_planned_spec() {
    use model::dimension_geometry::Reference;
    let (_, a) = plate();
    let hole = a.features.iter().find(|f| f.kind == "hole").unwrap();
    let mut planes: Vec<_> = a
        .dimension_references()
        .iter()
        .filter_map(|(id, refs)| match refs.as_slice() {
            [Reference::Plane { origin, normal, .. }] if normal[0].abs() > 1.0 - 1e-8 => {
                Some((id.clone(), origin[0]))
            }
            _ => None,
        })
        .collect();
    planes.sort_by(|p, q| p.1.total_cmp(&q.1));
    let mut s = spec(json!({
        "features":{&hole.id:{"suppress":true}},
        "dimension_source":"authored",
        "dimensions":{"d:authored:width":{
            "between":[planes.first().unwrap().0,planes.last().unwrap().0],"along":"x"
        }}
    }));
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(p.spec.features[&hole.id].anchor.is_some());
    assert_eq!(p.recognition_corrections.len(), 1);
    let width = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:authored:width")
        .unwrap();
    assert_eq!(width.value, a.envelope[1][0] - a.envelope[0][0]);
    assert!(width.from_spec.contains(&"between".into()));
    assert_eq!(
        serde_json::to_value(&p).unwrap(),
        serde_json::to_value(model::plan_recovered(&a, &saved(&p.spec)).unwrap()).unwrap()
    );
    s.dimensions.get_mut("d:authored:width").unwrap().between = Some(vec!["one-reference".into()]);
    assert!(
        model::plan_recovered(&a, &s)
            .unwrap_err()
            .message
            .contains("needs 2 reference(s)")
    );
}

#[test]
fn declare_a_missed_bore_from_faces_then_lint_its_actual_printed_value() {
    let (part, mut a) = plate();
    let hole = a
        .features
        .iter()
        .find(|f| f.kind == "hole")
        .unwrap()
        .clone();
    let faces: Vec<_> = hole
        .faces
        .iter()
        .map(|&i| face_fingerprint(&a, i).unwrap())
        .collect();
    a.features.retain(|f| f.id != hole.id);
    a.recognition.as_mut().unwrap().patterns.clear();
    let s = spec(
        json!({"feature_declarations":[{"id":"f:declared:pilot","kind":"hole","faces":faces}]}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    let d = p
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == "f:declared:pilot" && m.parameter == "bore.diameter")
        })
        .unwrap();
    assert!(!d.default);
    assert!(
        d.from_spec
            .contains(&"feature_declarations/f:declared:pilot".into())
    );
    let effective = corrections::effective(&a, &s).unwrap();
    let declared = effective
        .features
        .iter()
        .find(|f| f.id == "f:declared:pilot")
        .unwrap();
    assert!(
        (declared.parameters["diameter"].as_f64().unwrap()
            - hole.parameters["diameter"].as_f64().unwrap())
        .abs()
            < 1e-5
    );
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    let mut marks = lint::marks(&drawing);
    assert!(!lint::check(&effective, &[]).is_empty());
    let mark = marks
        .iter_mut()
        .find(|m| {
            m.claims
                .iter()
                .any(|c| c.feature == "f:declared:pilot" && c.parameter == "bore.diameter")
        })
        .unwrap();
    mark.text = "ø999 THRU".into();
    assert!(
        lint::check(&effective, &marks)
            .iter()
            .any(|i| i.subjects.contains(&"f:declared:pilot".into())),
        "changed text is still checked"
    );
    assert_eq!(
        serde_json::to_value(&p).unwrap(),
        serde_json::to_value(model::plan_recovered(&a, &saved(&p.spec)).unwrap()).unwrap()
    );
    a.face_attachments[hole.faces[0]].bounds = None;
    let e = model::plan_recovered(&a, &s).unwrap_err();
    assert!(
        e.message.contains("fingerprint")
            && e.message.contains("reattach")
            && e.message.contains("remove"),
        "{}",
        e.message
    );
}

#[test]
fn split_and_regroup_use_the_ordinary_grid_planner_and_saved_ids() {
    let (part, a) = plate();
    let pattern = a.recognition.as_ref().unwrap().patterns.first().unwrap();
    let split = spec(json!({"features":{&pattern.id:{"split":true}}}));
    let p = model::plan_recovered(&a, &split).unwrap();
    assert!(
        corrections::effective(&a, &p.spec)
            .unwrap()
            .recognition
            .as_ref()
            .unwrap()
            .patterns
            .is_empty()
    );
    assert!(p.dimensions.iter().all(|d| !d.id.contains("grid_pitch")));
    let grouped = spec(
        json!({"features":{"f:hole_pattern:mounting":{"pattern":{"arrangement":"grid","members":pattern.members}}}}),
    );
    let p = model::plan_recovered(&a, &grouped).unwrap();
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.id.starts_with("d:f:hole_pattern:mounting/") && d.id.contains("grid_pitch"))
    );
    assert!(
        pattern
            .members
            .iter()
            .all(|id| p.spec.features[id].anchor.is_some())
    );
    let effective = corrections::effective(&a, &p.spec).unwrap();
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    assert!(!lint::check(&effective, &[]).is_empty());
    let findings = lint::lint(&effective, &drawing);
    assert!(
        !findings
            .iter()
            .any(|i| i.code == "hole_pattern_requirement_missing"),
        "{findings:?}"
    );
    let mut again = a.clone();
    again.features.reverse();
    let replay = model::plan_recovered(&again, &saved(&p.spec)).unwrap();
    assert!(
        replay
            .dimensions
            .iter()
            .any(|d| d.id.starts_with("d:f:hole_pattern:mounting/"))
    );
}

#[test]
fn split_grid_prefers_complete_a4_over_fill_enlargement() {
    let (part, a) = plate();
    let pattern = &a.recognition.as_ref().unwrap().patterns[0];
    let split = spec(json!({"features":{&pattern.id:{"split":true}}}));
    let automatic = model::plan_recovered(&a, &split).unwrap();
    assert_eq!(automatic.dimensions.len(), 13);
    assert!(
        automatic.sheet.basis.contains("A4: usable at 1:1"),
        "{}",
        automatic.sheet.basis
    );
    assert_eq!(
        (&*automatic.sheet.size, &*automatic.sheet.scale),
        ("A4", "1:1")
    );

    let mut pinned = split;
    pinned.sheet.size = Some("A4".into());
    pinned.sheet.scale = Some("1:1".into());
    let p = model::plan_recovered(&a, &pinned).unwrap();
    assert_eq!(p.dimensions.len(), 13);
    assert!(p.dimensions.iter().all(|d| d.placed.is_some()));
    assert!(
        p.problems.iter().all(|problem| problem.severity == "info"
            || matches!(
                problem.code.as_str(),
                "features_not_recognised" | "general_tolerance_missing"
            )),
        "{:?}",
        p.problems
    );
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    let effective = corrections::effective(&a, &p.spec).unwrap();
    assert!(lint::lint(&effective, &drawing).is_empty());
}

#[test]
fn bad_references_conflicting_actions_and_false_patterns_are_refused() {
    let (_, a) = plate();
    for value in [
        json!({"features":{"f:hole:missing":{"suppress":true}}}),
        json!({"features":{"f:hole_pattern:f6":{"pattern":{"arrangement":"linear","members":["f:hole:f6","f:hole:f7"]}}}}),
        json!({"features":{"f:hole:f6":{"suppress":true,"reclassify":"boss"}}}),
        json!({"features":{"f:hole:f6":{"split":true}}}),
        json!({"features":{"f:hole_pattern:bad":{"pattern":{"arrangement":"linear","members":["f:hole:f6","f:hole:f6"]}}}}),
        json!({"features":{"f:hole_pattern:bad":{"pattern":{"arrangement":"linear","members":["f:hole:f6","f:hole:999"]}}}}),
    ] {
        let e = model::plan_recovered(&a, &spec(value)).unwrap_err();
        assert!(
            e.message.contains("remove") && e.message.contains("reattach"),
            "{}",
            e.message
        );
    }
    let pattern = &a.recognition.as_ref().unwrap().patterns[0];
    let bad = spec(
        json!({"features":{"f:hole_pattern:bad":{"pattern":{"arrangement":"linear","members":pattern.members}}}}),
    );
    assert!(
        model::plan_recovered(&a, &bad)
            .unwrap_err()
            .message
            .contains("principal axis")
    );
    assert!(
        serde_json::from_value::<Spec>(json!({"features":{"f:hole:f6":{"reclassify":"dragon"}}}))
            .is_err()
    );
    assert!(model::edit(&Spec::default(),&json!([{"op":"add","path":"feature_declarations","value":{"id":"bad","kind":"hole","faces":[]}}])).is_err());
}

#[test]
fn boss_to_turned_step_reads_the_faces_and_preserves_the_feature_identity() {
    let (part, mut a) = load("tests/fixtures/parts/boss_circle.step");
    let boss = a
        .features
        .iter()
        .find(|f| f.kind == "boss")
        .unwrap()
        .clone();
    a.features
        .retain(|f| f.id == boss.id || f.faces != boss.faces);
    let s = spec(json!({"features":{&boss.id:{"reclassify":"turned_step"}}}));
    let p = model::plan_recovered(&a, &s).unwrap();
    let effective = corrections::effective(&a, &p.spec).unwrap();
    assert_eq!(
        effective
            .features
            .iter()
            .find(|f| f.id == boss.id)
            .unwrap()
            .kind,
        "turned_step"
    );
    let d = p
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == boss.id && m.parameter == "step.length")
        })
        .unwrap();
    assert!(!d.default);
    assert!(
        d.from_spec
            .contains(&format!("features/{}/reclassify", boss.id))
    );
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    assert!(!lint::check(&effective, &[]).is_empty());
    let _ = lint::lint(&effective, &drawing);
    let wrong = spec(json!({"features":{&boss.id:{"reclassify":"pocket"}}}));
    assert!(
        model::plan_recovered(&a, &wrong)
            .unwrap_err()
            .message
            .contains("reinterpretations")
    );
}

#[test]
fn declarations_use_rectangular_wall_geometry_and_refuse_unproved_kind_changes() {
    let (_, a) = load("tests/fixtures/recognition/issues/pocket_z.step");
    let faces: Vec<_> = a
        .face_attachments
        .iter()
        .enumerate()
        .filter(|(i, f)| f.surface.as_deref() == Some("plane") && !a.stock_faces.contains(i))
        .map(|(i, _)| face_fingerprint(&a, i).unwrap())
        .collect();
    let s = spec(
        json!({"feature_declarations":[{"id":"f:declared:recess","kind":"pocket","faces":faces}]}),
    );
    let effective = corrections::effective(&a, &s).unwrap();
    let f = effective
        .features
        .iter()
        .find(|f| f.id == "f:declared:recess")
        .unwrap();
    let draftwright::lint::section_recesses::Grammar::Pocket(pocket) =
        draftwright::lint::section_recesses::grammar(f).unwrap()
    else {
        panic!("declared pocket grammar");
    };
    assert_eq!((pocket.width, pocket.length, pocket.depth), (14., 22., 10.));
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(p.dimensions.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| m.feature == "f:declared:recess")
            && !d.default
    }));
    assert!(
        lint::check(&effective, &[])
            .iter()
            .any(|i| i.subjects.contains(&"f:declared:recess".into()))
    );
    let mut wrong = s.clone();
    wrong.feature_declarations[0].kind = corrections::Kind::Slot;
    assert!(
        model::plan_recovered(&a, &wrong)
            .unwrap_err()
            .message
            .contains("floor")
    );
}

#[test]
fn session_preview_apply_history_and_declaration_ops_share_validation() {
    let (part, a) = plate();
    let id = a
        .features
        .iter()
        .find(|f| f.kind == "hole")
        .unwrap()
        .id
        .clone();
    let mut session = Session::new(part, a, Spec::default()).unwrap();
    let options = session.options("face:0").unwrap();
    assert_eq!(
        options.settable["feature_declarations"]["fields"][0],
        json!(face_fingerprint(session.analysis(), 0).unwrap())
    );
    assert!(serde_json::to_vec(&options).unwrap().len() < 1024);
    assert!(session.options("face:99999").is_err());
    let feature_options = session.options(&id).unwrap();
    assert_eq!(
        feature_options.settable[&format!("features/{id}/suppress")]["choices"],
        json!([false, true])
    );
    assert!(serde_json::to_vec(&feature_options).unwrap().len() < 1024);
    let before = serde_json::to_value(session.state()).unwrap();
    let ops = [Op::set(format!("features/{id}/suppress"), json!(true))];
    session.apply(&ops, false).unwrap();
    assert_eq!(serde_json::to_value(session.state()).unwrap(), before);
    session.apply(&ops, true).unwrap();
    assert!(session.spec().features[&id].anchor.is_some());
    let after = serde_json::to_value(session.state()).unwrap();
    session.undo().unwrap();
    assert_eq!(serde_json::to_value(session.state()).unwrap(), before);
    session.redo().unwrap();
    assert_eq!(serde_json::to_value(session.state()).unwrap(), after);
    assert!(
        session
            .apply(&[Op::set("features/f:missing/suppress", json!(true))], true)
            .is_err()
    );
    assert_eq!(serde_json::to_value(session.state()).unwrap(), after);
    let faces = session
        .analysis()
        .features
        .iter()
        .find(|f| f.id == id)
        .unwrap()
        .faces
        .iter()
        .map(|&i| face_fingerprint(session.analysis(), i).unwrap())
        .collect::<Vec<_>>();
    session
        .apply(
            &[Op::add(
                "feature_declarations",
                json!({"id":"f:declared:replacement","kind":"hole","faces":faces}),
            )],
            true,
        )
        .unwrap();
    assert!(
        session
            .plan()
            .dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .any(|m| m.feature == "f:declared:replacement")
    );
}

#[test]
fn exact_saved_face_anchors_resolve_after_reanalysis_and_refuse_vanished_faces() {
    let (_, a) = plate();
    let id = a
        .features
        .iter()
        .find(|f| f.kind == "hole")
        .unwrap()
        .id
        .clone();
    let p = model::plan_recovered(&a, &spec(json!({"features":{&id:{"suppress":true}}}))).unwrap();
    let (_, mut again) = plate();
    again.binding.source_sha256 = "a new source containing the same faces".into();
    let replay = model::plan_recovered(&again, &saved(&p.spec)).unwrap();
    assert_eq!(replay.recognition_corrections[0].id, id);
    assert_eq!(
        replay.spec.binding.as_ref().unwrap().source_sha256,
        again.binding.source_sha256
    );
    assert!(
        replay
            .dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .all(|m| m.feature != id)
    );
    let face = again.features.iter().find(|f| f.id == id).unwrap().faces[0];
    again.face_attachments[face].bounds = None;
    let e = model::plan_recovered(&again, &p.spec).unwrap_err();
    assert!(
        e.message.contains("fingerprint") && e.message.contains("reattach"),
        "{}",
        e.message
    );
}

#[test]
fn declaration_faces_can_be_reindexed_without_changing_the_authored_id() {
    let (_, mut a) = plate();
    let hole = a
        .features
        .iter()
        .find(|f| f.kind == "hole")
        .unwrap()
        .clone();
    let faces = hole
        .faces
        .iter()
        .map(|&i| face_fingerprint(&a, i).unwrap())
        .collect::<Vec<_>>();
    a.features.clear();
    a.recognition.as_mut().unwrap().patterns.clear();
    let s = spec(
        json!({"feature_declarations":[{"id":"f:declared:mount","kind":"hole","faces":faces}]}),
    );
    // A bridge can use an oblique working frame even when the selected bore is principal
    // in the part. Vector-axis readers must keep that case, just as ordinary holes do.
    let c = std::f64::consts::FRAC_1_SQRT_2;
    a.recognition.as_mut().unwrap().frame = [[0.; 3], [1., 0., 0.], [0., c, c], [0., -c, c]];
    let first = corrections::effective(&a, &s).unwrap();
    assert_eq!(
        first
            .features
            .iter()
            .find(|f| f.id == "f:declared:mount")
            .unwrap()
            .fingerprint
            .axis,
        hole.fingerprint.axis
    );
    let expected = first
        .features
        .iter()
        .find(|f| f.id == "f:declared:mount")
        .unwrap()
        .parameters
        .clone();
    a.face_attachments.reverse();
    let again = corrections::effective(&a, &saved(&s)).unwrap();
    let f = again
        .features
        .iter()
        .find(|f| f.id == "f:declared:mount")
        .unwrap();
    assert_eq!(f.parameters, expected);
    assert_ne!(f.faces, hole.faces);
}

#[test]
fn an_authored_linear_pair_replaces_its_automatic_grid_membership() {
    let (_, a) = plate();
    let holes: Vec<_> = draftwright::bridge::holes::read(&a)
        .into_iter()
        .map(|(_, h)| h.unwrap())
        .collect();
    let first = &holes[0];
    let k = first.principal.unwrap().0;
    let second = holes
        .iter()
        .skip(1)
        .find(|h| {
            (0..3)
                .filter(|&i| i != k)
                .any(|i| (h.at[i] - first.at[i]).abs() < 1e-5)
        })
        .unwrap();
    let s = spec(
        json!({"features":{"f:hole_pattern:pair":{"pattern":{"arrangement":"linear","members":[first.feature.id,second.feature.id]}}}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.id == "d:f:hole_pattern:pair/pitch.length" && !d.default)
    );
    let effective = corrections::effective(&a, &p.spec).unwrap();
    let patterns = &effective.recognition.as_ref().unwrap().patterns;
    assert_eq!(patterns.len(), 1);
    assert_eq!(patterns[0].id, "f:hole_pattern:pair");
}

#[test]
fn a_bolt_circle_derives_its_pcd_and_lint_still_requires_its_position() {
    let (part, a) = model::analyse_native_bytes(
        "bolt-circle.step",
        include_bytes!("fixtures/recognition_corrections/bolt_circle.step.gz"),
    )
    .unwrap();
    let members: Vec<_> = a
        .features
        .iter()
        .filter(|f| {
            f.kind == "hole"
                && f.parameters["diameter"]
                    .as_f64()
                    .is_some_and(|d| (d - 4.).abs() < 1e-5)
        })
        .map(|f| f.id.clone())
        .collect();
    assert_eq!(members.len(), 4);
    let s = spec(
        json!({"features":{"f:hole_pattern:bolts":{"pattern":{"arrangement":"bolt_circle","members":members}}}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    let effective = corrections::effective(&a, &p.spec).unwrap();
    let circle = effective
        .recognition
        .as_ref()
        .unwrap()
        .patterns
        .iter()
        .find(|p| p.id == "f:hole_pattern:bolts")
        .unwrap();
    assert!((circle.parameters["diameter"].as_f64().unwrap() - 40.).abs() < 1e-5);
    assert!(p.dimensions.iter().any(|d| {
        d.label.contains("BC")
            && d.from_spec
                .contains(&"features/f:hole_pattern:bolts/pattern".into())
    }));
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    assert!(
        !lint::lint(&effective, &drawing)
            .iter()
            .any(|i| i.code == "hole_pattern_requirement_missing")
    );
    assert!(
        lint::check(&effective, &[])
            .iter()
            .any(|i| i.code == "hole_pattern_requirement_missing")
    );
    assert_eq!(
        serde_json::to_value(&p).unwrap(),
        serde_json::to_value(model::plan_recovered(&a, &saved(&p.spec)).unwrap()).unwrap()
    );
}

#[test]
fn published_mcp_schema_and_common_model_accept_corrections_and_refuse_bad_shapes() {
    let input = mcp_schema::with_full_spec("inspect_part", false);
    assert!(
        draftwright::mcp::validate(&input, &json!({"part_ref":"part","part_ref\n":"bad"})).is_err()
    );
    let s = json!({"feature_declarations":[{"id":"f:declared:bore","kind":"hole","faces":["face:sha256:0000000000000000000000000000000000000000000000000000000000000000"]}]});
    assert!(draftwright::mcp::validate(&input, &json!({"part_ref":"part","spec":s})).is_ok());
    for bad in [
        json!({"id":"f:declared:x","kind":"hole","faces":"face:bad"}),
        json!({"id":"f:declared:x","kind":"hole","faces":[],"diameter":20}),
        json!({"kind":"hole","faces":[],"replacement":"x"}),
        json!({"id":"f:declared:x","faces":[]}),
    ] {
        assert!(
            draftwright::mcp::validate(
                &input,
                &json!({"part_ref":"part","spec":{"feature_declarations":[bad]}})
            )
            .is_err()
        );
    }
}

#[test]
fn a_missed_boss_uses_its_named_negative_end_and_the_same_height_planner() {
    let (part, mut a) = model::analyse_native_bytes(
        "below-boss.step",
        include_bytes!("fixtures/recognition_corrections/below_boss.step.gz"),
    )
    .unwrap();
    let faces: Vec<_> = a
        .face_attachments
        .iter()
        .enumerate()
        .filter(|(_, f)| {
            f.surface.as_deref() == Some("cylinder")
                || f.bounds
                    .is_some_and(|b| (b[2] + 7.).abs() < 1e-5 && (b[5] + 7.).abs() < 1e-5)
        })
        .map(|(i, _)| face_fingerprint(&a, i).unwrap())
        .collect();
    assert_eq!(faces.len(), 2);
    let recognised = a.clone();
    a.features.clear();
    a.recognition.as_mut().unwrap().patterns.clear();
    let s = spec(
        json!({"feature_declarations":[{"id":"f:declared:lower_boss","kind":"boss","faces":faces}]}),
    );
    let owned = corrections::effective(&recognised, &s).unwrap();
    assert_eq!(
        owned.features.iter().filter(|f| f.kind == "boss").count(),
        1
    );
    assert!(
        owned
            .features
            .iter()
            .any(|f| f.id == "f:declared:lower_boss")
    );
    let mut conflict = s.clone();
    conflict.features.insert(
        "f:boss:f6".into(),
        serde_json::from_value(json!({"fit":"g6"})).unwrap(),
    );
    assert!(
        corrections::effective(&recognised, &conflict)
            .unwrap_err()
            .message
            .contains("already have a feature")
    );
    let mut duplicate = s.clone();
    let mut second = duplicate.feature_declarations[0].clone();
    second.id = "f:declared:duplicate".into();
    duplicate.feature_declarations.push(second);
    assert!(corrections::effective(&a, &duplicate).is_err());
    let effective = corrections::effective(&a, &s).unwrap();
    let f = effective
        .features
        .iter()
        .find(|f| f.id == "f:declared:lower_boss")
        .unwrap();
    assert_eq!(f.parameters["axis"], json!([0., 0., -1.]));
    assert_eq!(f.parameters["location"], json!([0., 0., -7.]));
    assert_eq!(f.parameters["height"], json!(4.));
    assert_eq!(f.fingerprint.axis, Some([0., 0., 1.]));
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .any(|m| m.feature == f.id && (m.value - 4.).abs() < 1e-5)
    );
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    assert!(
        !lint::lint(&effective, &drawing)
            .iter()
            .any(|i| i.code == "boss_height_missing")
    );
}

#[test]
fn saved_corrections_rebind_members_fits_and_measurement_policies_when_ranks_change() {
    let (_, a) = plate();
    let members = a.recognition.as_ref().unwrap().patterns[0].members.clone();
    let outside = a
        .features
        .iter()
        .find(|f| f.kind == "hole" && !members.contains(&f.id))
        .unwrap()
        .id
        .clone();
    let mut value = json!({"features":{"f:hole_pattern:mounting":{"pattern":{"arrangement":"grid","members":members}}}});
    for id in &members {
        value["features"][id] = json!({"fit":"H7"});
    }
    value["features"][&outside] = json!({"reclassify":"hole"});
    let measurement = format!("d:{outside}/bore.diameter");
    value["dimensions"] = json!({&measurement:{"tolerance":"±0.05"}});
    value["precision"] = json!({&measurement:2});
    let original = model::plan_recovered(&a, &spec(value)).unwrap();
    let mut again = a.clone();
    let mut renamed = std::collections::BTreeMap::new();
    for f in &mut again.features {
        let old = f.id.clone();
        f.id = format!(
            "{}:{}",
            old.rsplit_once(':').unwrap().0,
            old.rsplit_once(':').unwrap().1.parse::<usize>().unwrap() + 100
        );
        renamed.insert(old, f.id.clone());
    }
    for pattern in &mut again.recognition.as_mut().unwrap().patterns {
        for member in &mut pattern.members {
            *member = renamed[member].clone();
        }
    }
    let replay = model::plan_recovered(&again, &saved(&original.spec)).unwrap();
    assert!(replay.spec.features.contains_key("f:hole_pattern:mounting"));
    for old in &members {
        assert_eq!(
            replay.spec.features[&renamed[old]].fit.as_deref(),
            Some("H7")
        );
        assert!(!replay.spec.features.contains_key(old));
    }
    let current = format!("d:{}/bore.diameter", renamed[&outside]);
    assert_eq!(
        replay.spec.dimensions[&current].tolerance.as_deref(),
        Some("±0.05")
    );
    assert_eq!(replay.spec.precision[&current], 2);
    assert!(!replay.spec.dimensions.contains_key(&measurement));
    assert!(replay.dimensions.iter().any(|d| {
        d.id == current
            && d.from_spec
                .contains(&format!("features/{}/reclassify", renamed[&outside]))
    }));
}

#[test]
fn saved_declared_identity_survives_the_recogniser_learning_the_feature() {
    let (part, native) = plate();
    let hole = native
        .features
        .iter()
        .find(|f| f.kind == "hole")
        .unwrap()
        .clone();
    let faces: Vec<_> = hole
        .faces
        .iter()
        .map(|&i| face_fingerprint(&native, i).unwrap())
        .collect();
    let mut missed = native.clone();
    missed.features.retain(|f| f.id != hole.id);
    missed.recognition.as_mut().unwrap().patterns.clear();
    let s = spec(
        json!({"feature_declarations":[{"id":"f:declared:pilot","kind":"hole","faces":faces}]}),
    );
    let first = model::plan_recovered(&missed, &s).unwrap();
    let p = model::plan_recovered(&native, &saved(&first.spec)).unwrap();
    let effective = corrections::effective(&native, &p.spec).unwrap();
    assert_eq!(
        effective
            .features
            .iter()
            .filter(|f| f.kind == "hole")
            .count(),
        native.features.iter().filter(|f| f.kind == "hole").count()
    );
    assert!(
        effective
            .features
            .iter()
            .any(|f| f.id == "f:declared:pilot")
    );
    assert!(effective.features.iter().all(|f| f.id != hole.id));
    assert!(
        effective
            .recognition
            .as_ref()
            .unwrap()
            .patterns
            .iter()
            .any(|g| g.members.contains(&"f:declared:pilot".into()))
    );
    assert!(
        p.recognition_corrections
            .iter()
            .any(|c| c.id == "f:declared:pilot" && c.basis.contains(&hole.id))
    );
    assert!(
        p.dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .all(|m| m.feature != hole.id)
    );
    let drawing = model::render(&part, &native, &p, &font()).unwrap();
    assert!(
        !lint::lint(&effective, &drawing)
            .iter()
            .any(|i| i.code == "hole_pattern_requirement_missing")
    );
    assert_eq!(
        serde_json::to_value(&p).unwrap(),
        serde_json::to_value(model::plan_recovered(&native, &saved(&p.spec)).unwrap()).unwrap()
    );
}

#[test]
fn omitting_a_void_or_pocket_cap_cannot_turn_it_into_an_open_recess() {
    let (_, a) = model::analyse_native_bytes(
        "closed-void.step",
        include_bytes!("fixtures/recognition_corrections/closed_void.step.gz"),
    )
    .unwrap();
    for kind in ["pocket", "slot"] {
        let faces: Vec<_> = a
            .face_attachments
            .iter()
            .enumerate()
            .filter(|(_, f)| {
                f.surface.as_deref() == Some("plane")
                    && f.bounds.is_some_and(|b| {
                        b[0] >= -5. - 1e-5
                            && b[3] <= 5. + 1e-5
                            && b[1] >= -4. - 1e-5
                            && b[4] <= 4. + 1e-5
                            && b[2] >= -3. - 1e-5
                            && b[5] <= 3. + 1e-5
                    })
                    && (f.axis != Some(2)
                        || (kind == "pocket"
                            && f.support.first().is_some_and(|z| (*z + 3.).abs() < 1e-5)))
            })
            .map(|(i, _)| face_fingerprint(&a, i).unwrap())
            .collect();
        assert_eq!(faces.len(), if kind == "pocket" { 5 } else { 4 });
        let s = spec(
            json!({"feature_declarations":[{"id":"f:declared:false_opening","kind":kind,"faces":faces}]}),
        );
        let e = model::plan_recovered(&a, &s).unwrap_err();
        assert!(
            e.message.contains("open") && e.message.contains("remove"),
            "{}",
            e.message
        );
    }
}

#[test]
fn a_local_planar_pocket_mouth_below_a_higher_boss_is_proved_by_the_edge_graph() {
    let (_, a) = model::analyse_native_bytes(
        "local-pocket.step",
        include_bytes!("fixtures/recognition_corrections/local_pocket.step.gz"),
    )
    .unwrap();
    let faces: Vec<_> = a
        .face_attachments
        .iter()
        .enumerate()
        .filter(|(_, f)| {
            f.surface.as_deref() == Some("plane")
                && f.bounds.is_some_and(|b| {
                    b[0] >= 4. - 1e-5
                        && b[3] <= 16. + 1e-5
                        && b[1] >= -5. - 1e-5
                        && b[4] <= 5. + 1e-5
                        && b[2] >= -1e-5
                        && b[5] <= 6. + 1e-5
                })
        })
        .map(|(i, _)| face_fingerprint(&a, i).unwrap())
        .collect();
    assert_eq!(faces.len(), 5);
    let s = spec(
        json!({"feature_declarations":[{"id":"f:declared:local","kind":"pocket","faces":faces}]}),
    );
    let effective = corrections::effective(&a, &s).unwrap();
    let f = effective
        .features
        .iter()
        .find(|f| f.id == "f:declared:local")
        .unwrap();
    let draftwright::lint::section_recesses::Grammar::Pocket(p) =
        draftwright::lint::section_recesses::grammar(f).unwrap()
    else {
        panic!("pocket");
    };
    assert_eq!((p.width, p.length, p.depth), (10., 12., 6.));
}

#[test]
fn removing_a_group_field_restores_defaults_while_another_correction_remains() {
    let (_, a) = plate();
    let s = spec(
        json!({"features":{"f:hole:f9":{"suppress":true},"f:hole_pattern:pair":{"pattern":{"arrangement":"linear","members":["f:hole:f6","f:hole:f7"]}}}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    let removed = model::edit(
        &p.spec,
        &json!([{"op":"remove","path":"features/f:hole_pattern:pair/pattern"}]),
    )
    .unwrap();
    let restored = model::plan_recovered(&a, &removed).unwrap();
    let effective = corrections::effective(&a, &restored.spec).unwrap();
    assert!(
        effective
            .recognition
            .as_ref()
            .unwrap()
            .patterns
            .iter()
            .any(|p| p.id == a.recognition.as_ref().unwrap().patterns[0].id)
    );
    assert!(
        restored
            .recognition_corrections
            .iter()
            .all(|c| c.id != "f:hole_pattern:pair")
    );
    assert!(restored.spec.features["f:hole:f9"].suppress);
}
