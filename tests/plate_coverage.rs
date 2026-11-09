//! The plate denominator comes from recognition, and credit comes from printed ink.
use draftwright::{
    bridge::Feature,
    lint::{self, Mark},
    model::Analysis,
    output::Claim,
};
use serde_json::{Value, json};

fn feature(id: &str, kind: &str, parameters: Value) -> Feature {
    serde_json::from_value(json!({"id":id,"kind":kind,"faces":[0,1],"parameters":parameters,
        "fingerprint":{"version":"test","family":kind,"subtype":kind,"sizes":{},"position":[0,0,0],"neighbourhood":[]}})).unwrap()
}
fn plate(axis: &str, lo: f64, hi: f64) -> Feature {
    feature(
        "f:plate:1",
        "plate",
        json!({"axis":axis,"lo":lo,"hi":hi,"u":5,"v":5,"body_key":[0,0,0,20,20,20,8000,2400]}),
    )
}
fn part(features: Vec<Feature>) -> Analysis {
    serde_json::from_value(json!({"schema":"draftwright.analysis/7","binding":{"source_sha256":"","part":"test","face_count":2,"loader":"test"},"envelope":[[0,0,0],[20,20,20]],"iso_bounds":[0,0,0,0],"features":features})).unwrap()
}
fn mark(feature: &str, parameter: &str, text: &str) -> Mark {
    Mark {
        name: format!("d:{feature}/{parameter}"),
        view: Some("v:front".into()),
        text: text.into(),
        claims: vec![Claim {
            feature: feature.into(),
            parameter: parameter.into(),
        }],
    }
}
fn codes(a: &Analysis, marks: &[Mark]) -> Vec<String> {
    lint::check(a, marks)
        .into_iter()
        .filter(|i| i.code.starts_with("plate_requirement_"))
        .map(|i| i.code)
        .collect()
}

fn one_body(a: &mut Analysis) {
    a.principal_profiles = serde_json::from_value(json!([
        {"bounds":a.envelope,"faces":[]}
    ]))
    .unwrap();
}

#[test]
fn a_same_body_band_chain_can_be_proved_by_printed_baseline_stations() {
    let slab = plate("x", 0.0, 3.0);
    let key = slab.parameters["body_key"].clone();
    let mut features = vec![slab];
    for (i, (lo, hi)) in [(0.0, 1.0), (1.0, 3.0), (3.0, 6.0), (6.0, 10.0)]
        .into_iter()
        .enumerate()
    {
        features.push(feature(
            &format!("f:turned_step:{}", i + 1),
            "turned_step",
            json!({"axis":"x","lo":lo,"hi":hi,"diameter":10,
                "profile":{"axis":"x","axis_origin":[0,5,5],"body_key":key}}),
        ));
    }
    let a = part(features);
    let forward = [
        mark("f:turned_step:1", "step.length", "1"),
        mark("f:turned_step:2", "step_position.length", "3"),
    ];
    assert!(codes(&a, &forward).is_empty());
    for kept in [&forward[..1], &forward[1..]] {
        assert_eq!(codes(&a, kept), ["plate_requirement_missing"]);
    }
    let reverse = [
        mark("f:turned_step:1", "step_position.length", "10"),
        mark("f:turned_step:2", "step_position.length", "9"),
        mark("f:turned_step:3", "step_position.length", "7"),
    ];
    assert!(codes(&a, &reverse).is_empty());
    for missing in 0..reverse.len() {
        let kept: Vec<_> = reverse
            .iter()
            .enumerate()
            .filter(|(i, _)| *i != missing)
            .map(|(_, m)| m.clone())
            .collect();
        assert_eq!(codes(&a, &kept), ["plate_requirement_missing"]);
    }
    let mut wrong = forward.clone();
    wrong[1].text = "99".into();
    assert_eq!(codes(&a, &wrong), ["plate_requirement_missing"]);
    wrong[1] = mark("unrelated", "step_position.length", "3");
    assert_eq!(codes(&a, &wrong), ["plate_requirement_missing"]);
    let mut suppressed = forward.clone();
    suppressed[1].name = "suppressed:baseline".into();
    assert_eq!(codes(&a, &suppressed), ["plate_requirement_missing"]);
    let mut other_body = a.clone();
    other_body.features[2].parameters["profile"]["body_key"] = json!(["other"]);
    assert_eq!(codes(&other_body, &forward), ["plate_requirement_missing"]);
    let mut other_axis_line = a.clone();
    other_axis_line.features[2].parameters["profile"]["axis_origin"] = json!([0, 5, 8]);
    assert_eq!(
        codes(&other_axis_line, &forward),
        ["plate_requirement_missing"]
    );
}

#[test]
fn plate_evidence_is_claimed_printed_and_typed() {
    let a = part(vec![plate("z", 0.0, 3.0)]);
    assert_eq!(codes(&a, &[]), ["plate_requirement_missing"]);
    assert_eq!(
        codes(&a, &[mark("f:plate:1", "thickness.length", "999")]),
        ["plate_requirement_unverifiable"]
    );
    assert_eq!(
        codes(&a, &[mark("f:plate:1", "height.length", "3")]),
        ["plate_requirement_missing"]
    );
    assert!(codes(&a, &[mark("f:plate:1", "thickness.length", "3")]).is_empty());
    let mut suppressed = mark("f:plate:1", "thickness.length", "");
    suppressed.name = format!("suppressed:{}", suppressed.name);
    assert_eq!(codes(&a, &[suppressed]), ["plate_requirement_suppressed"]);
    assert_eq!(
        codes(&a, &[mark("f:plate:1", "thickness.length", "3× 999")]),
        ["plate_requirement_unverifiable"]
    );
    assert_eq!(
        codes(&a, &[mark("f:plate:1", "thickness.length", "999 ±3")]),
        ["plate_requirement_unverifiable"]
    );
    let mut bad = a.clone();
    bad.features[0].parameters["hi"] = json!(0);
    assert_eq!(codes(&bad, &[]), ["plate_requirement_unverifiable"]);

    let mut turned = part(vec![
        plate("x", 0.0, 3.0),
        feature(
            "f:turned_step:f0",
            "turned_step",
            json!({"axis":"x","lo":0,"hi":3,"diameter":10,"profile":{"axis":"x","axis_origin":[0,5,5],"body_key":[30,0,0,50,20,20,8000,2400]}}),
        ),
    ]);
    let length = [mark("f:turned_step:f0", "step.length", "3")];
    assert_eq!(codes(&turned, &length), ["plate_requirement_missing"]);
    turned.features[1].parameters["profile"]["body_key"] =
        turned.features[0].parameters["body_key"].clone();
    assert!(codes(&turned, &length).is_empty());

    let mut duplicate = a.features[0].clone();
    duplicate.id = "f:plate:2".into();
    let mut equal = part(vec![a.features[0].clone(), duplicate]);
    let statements = |a: &Analysis| {
        draftwright::model::plan(a, &draftwright::model::Spec::default())
            .unwrap()
            .dimensions
            .into_iter()
            .filter(|d| {
                d.measurements
                    .iter()
                    .any(|m| m.parameter == "thickness.length")
            })
            .collect::<Vec<_>>()
    };
    let shared = statements(&equal);
    assert_eq!(shared.len(), 1);
    assert_eq!(shared[0].label, "2× 3");
    equal.features[1].parameters["body_key"] = json!([30, 0, 0, 50, 20, 20, 8000, 2400]);
    assert_eq!(statements(&equal).len(), 2);
    for f in &mut equal.features {
        f.parameters["body_key"] = Value::Null;
    }
    assert_eq!(statements(&equal).len(), 2);
}

#[test]
fn overall_size_counts_only_for_the_same_interval_and_printed_claim() {
    let a = part(vec![plate("z", 0.0, 20.0)]);
    assert_eq!(codes(&a, &[]), ["plate_requirement_missing"]);
    assert_eq!(
        codes(&a, &[mark("envelope", "height.length", "99")]),
        ["plate_requirement_missing"]
    );
    assert!(codes(&a, &[mark("envelope", "height.length", "20")]).is_empty());
    let b = part(vec![plate("z", 2.0, 5.0)]);
    assert_eq!(
        codes(&b, &[mark("envelope", "height.length", "3")]),
        ["plate_requirement_missing"]
    );
}

#[test]
fn ladder_credit_requires_both_proven_endpoints_and_both_printed_heights() {
    let mut a = part(vec![plate("z", 2.0, 5.0)]);
    one_body(&mut a);
    a.levels = vec![2.0, 5.0];
    a.level_supports = serde_json::from_value(
        json!([{"z":2,"x_span":[0,20],"y_span":[0,20]},{"z":5,"x_span":[0,20],"y_span":[0,20]}]),
    )
    .unwrap();
    let ink = [
        mark("step_level", "step_height.length.h2", "2"),
        mark("step_level", "step_height.length.h5", "5"),
    ];
    assert!(codes(&a, &ink).is_empty());
    assert_eq!(codes(&a, &ink[..1]), ["plate_requirement_missing"]);
    let mut assembly = a.clone();
    assembly
        .principal_profiles
        .push(serde_json::from_value(json!({"bounds":[[30,0,0],[50,20,20]],"faces":[]})).unwrap());
    assembly.envelope[1][0] = 50.0;
    assert_eq!(codes(&assembly, &ink), ["plate_requirement_missing"]);
    assembly.principal_profiles.clear();
    assert_eq!(codes(&assembly, &ink), ["plate_requirement_missing"]);
    a.features[0].parameters["u"] = json!(30);
    assert_eq!(codes(&a, &ink), ["plate_requirement_missing"]);
}

#[test]
fn polygonal_support_exclusion_needs_body_and_witness_proof() {
    let body = json!([0, 0, 0, 20, 20, 20, 8000, 2400]);
    let boss = feature(
        "f:polygonal_boss:1",
        "polygonal_boss",
        json!({"axis":"z","base":3,"top":20,"center":[5,5,11.5],"side_count":4,"across_flats":10,"flat_centres":[[0,5,10],[10,5,10],[5,0,10],[5,10,10]],"flat_directions":[[-1,0,0],[1,0,0],[0,-1,0],[0,1,0]],"body_key":body}),
    );
    let mut a = part(vec![plate("z", 0.0, 3.0), boss]);
    assert!(codes(&a, &[]).is_empty());
    a.features[1].parameters["body_key"] = json!([30, 0, 0, 50, 20, 20, 8000, 2400]);
    assert_eq!(codes(&a, &[]), ["plate_requirement_missing"]);
    one_body(&mut a);
    assert_eq!(codes(&a, &[]), ["plate_requirement_missing"]);
    a.features[1].parameters["body_key"] = body;
    a.features[0].parameters["u"] = json!(15);
    assert_eq!(codes(&a, &[]), ["plate_requirement_missing"]);
}

#[test]
fn slot_pattern_web_exclusion_needs_the_exact_material_interval_and_body() {
    let body = json!([0, 0, 0, 20, 20, 20, 8000, 2400]);
    let slot = |id: &str, centre: f64| {
        feature(
            id,
            "slot",
            json!({"width_axis":"x","long_axis":"y","lo":0,"hi":20,"d_lo":0,"d_hi":20,"w_center":centre,"width":2,"body_key":body}),
        )
    };
    let mut a = part(vec![
        plate("x", 5.0, 9.0),
        slot("f:slot:1", 4.0),
        slot("f:slot:2", 10.0),
        feature(
            "f:slot_pattern:1",
            "slot_pattern",
            json!({"slots":["f:slot:1","f:slot:2"]}),
        ),
    ]);
    assert!(codes(&a, &[]).is_empty());
    let mut duplicate = a.clone();
    duplicate.features[0].parameters["lo"] = json!(5);
    duplicate.features[0].parameters["hi"] = json!(20);
    duplicate.features[3].parameters["slots"] = json!(["f:slot:1", "f:slot:1"]);
    assert_eq!(codes(&duplicate, &[]), ["plate_requirement_missing"]);
    a.features[0].parameters["hi"] = json!(8);
    assert_eq!(codes(&a, &[]), ["plate_requirement_missing"]);
    a.features[0].parameters["hi"] = json!(9);
    a.features[1].parameters["body_key"] = json!([30, 0, 0, 50, 20, 20, 8000, 2400]);
    assert_eq!(codes(&a, &[]), ["plate_requirement_missing"]);
}

#[test]
fn opposite_channel_wall_needs_every_printed_dependency_and_a_full_span() {
    let end = json!({"condition":"open","surface":{"type":"plane","gradient":[0,0]}});
    let channel = feature(
        "f:section_recess:1",
        "section_recess",
        json!({
            "index":0,"body":0,"classification":{"feature_kind":"channel","section_shape":"rectangular"},
            "evidence":{"defining_faces":[0,1],"constituent_faces":[0,1]},
            "geometry":{"type":"section_recess","frame":{"origin":[0,0,0],"run":[1,0,0],"u":[0,1,0],"v":[0,0,1]},"run_interval":[0,20],"ends":{"low":end,"high":end},
                "profile":{"closure":"open","boundary":[{"point":[3,10],"bulge":0},{"point":[3,6],"bulge":0},{"point":[17,6],"bulge":0},{"point":[17,10],"bulge":0}],"opening":[[17,10],[3,10]]}}
        }),
    );
    let mut lower = plate("y", 0.0, 3.0);
    lower.parameters["u"] = json!(10);
    lower.parameters["v"] = json!(8);
    let mut upper = lower.clone();
    upper.id = "f:plate:2".into();
    upper.parameters["lo"] = json!(17);
    upper.parameters["hi"] = json!(20);
    let mut a = part(vec![lower, upper, channel]);
    one_body(&mut a);
    let ink = [
        mark("f:plate:1", "thickness.length", "3"),
        mark("f:section_recess:1", "channel_width.length", "14"),
        mark("envelope", "depth.length", "20"),
    ];
    assert!(codes(&a, &ink).is_empty());
    let mut assembly = a.clone();
    assembly
        .principal_profiles
        .push(serde_json::from_value(json!({"bounds":[[0,0,30],[20,20,50]],"faces":[]})).unwrap());
    assembly.envelope[1][2] = 50.0;
    assert_eq!(codes(&assembly, &ink), ["plate_requirement_missing"]);
    assert_eq!(codes(&a, &ink[..2]), ["plate_requirement_missing"]);
    let mut wrong = ink.clone();
    wrong[1].text = "999".into();
    assert_eq!(codes(&a, &wrong), ["plate_requirement_missing"]);
    a.features[2].parameters["geometry"]["run_interval"] = json!([1, 20]);
    assert_eq!(codes(&a, &ink), ["plate_requirement_missing"]);
}

#[test]
fn a_through_step_can_replace_a_baseline_rung_without_closing_the_chain() {
    let mut a = part(vec![
        plate("z", 2.0, 5.0),
        feature(
            "f:through_step:1",
            "through_step",
            json!({"axis":"y", "at":[5,5,1], "section":[[0,0],[0,2],[10,2]],
            "body_key":[0,0,0,20,20,20,8000,2400]}),
        ),
    ]);
    one_body(&mut a);
    a.levels = vec![2.0, 5.0];
    a.level_supports = serde_json::from_value(json!([
        {"z":5,"x_span":[0,20],"y_span":[0,20]}
    ]))
    .unwrap();
    let ink = [
        mark("f:through_step:1", "through_step_leg.length.z", "2"),
        mark("step_level", "step_height.length.h5", "5"),
    ];
    assert!(codes(&a, &ink).is_empty());
    assert_eq!(codes(&a, &ink[..1]), ["plate_requirement_missing"]);
    assert_eq!(codes(&a, &ink[1..]), ["plate_requirement_missing"]);
    // Recognition of a replacement does not displace a rung that is actually printed.
    assert!(
        codes(
            &a,
            &[
                mark("step_level", "step_height.length.h2", "2"),
                ink[1].clone()
            ]
        )
        .is_empty()
    );
    a.features[1].parameters["body_key"] = json!([30, 0, 0, 50, 20, 20, 8000, 2400]);
    assert_eq!(codes(&a, &ink), ["plate_requirement_missing"]);
}

#[test]
fn a_top_slabs_thickness_and_overall_cover_its_redundant_underside_station() {
    use draftwright::{
        items::{Op, State, key},
        model::{self, Spec},
    };
    let mut a = part(vec![plate("z", 17.0, 20.0)]);
    one_body(&mut a);
    a.levels = vec![17.0];
    a.level_supports = serde_json::from_value(json!([
        {"z":17,"x_span":[0,20],"y_span":[0,20]}
    ]))
    .unwrap();
    let rung = "d:step_level/step_height.length.h17";
    let thickness = "d:f:plate:1/thickness.length";
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert!(plan.dimensions.iter().any(|d| d.id == thickness));
    assert!(!plan.dimensions.iter().any(|d| d.id == rung));
    assert!(plan.omitted.iter().any(|o| o.id == rung
        && o.state
            == State::Covered {
                by: thickness.into()
            }));
    for owner in [thickness, "d:envelope/height.length"] {
        let spec = model::edit(
            &Spec::default(),
            &json!([Op::set(
                format!("dimensions/{}/omit", key(owner)),
                json!(true)
            )]),
        )
        .unwrap();
        let restored = model::plan(&a, &spec).unwrap();
        assert!(
            restored
                .dimensions
                .iter()
                .any(|d| d.id == rung && d.placed.is_some())
        );
    }
}
