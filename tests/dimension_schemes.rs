use draftwright::{
    api,
    items::{Op, State},
    model::{self, Analysis, Spec},
    session::Session,
};
use serde_json::json;
use std::path::Path;
fn part(name: &str) -> (draftwright::haecceity::Part, Analysis) {
    model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("tests/fixtures/parts/{name}.step")),
    )
    .unwrap()
}
fn spec(v: serde_json::Value) -> Spec {
    serde_json::from_value(v).unwrap()
}
fn whole(style: &str, axis: &str, view: &str, from: &str) -> Spec {
    spec(
        json!({"dimension_schemes":{"locations":{"scheme":style,"axis":axis,"view":view,"from":from}}}),
    )
}
#[test]
fn chain_and_high_end_baseline_replace_locations_once_and_round_trip() {
    let (part, a) = part("scattered_holes");
    for style in ["chain", "baseline", "ordinate"] {
        let s = whole(style, "x", "plan", "max");
        let p = model::plan_recovered(&a, &s).unwrap();
        let generated: Vec<_> = p
            .dimensions
            .iter()
            .filter(|d| d.id.starts_with("d:scheme:"))
            .collect();
        assert!(!generated.is_empty());
        assert!(generated.iter().all(|d| d.placed.is_some()
            && !d.default
            && d.from_spec.contains(&"dimension_schemes/locations".into())));
        assert!(p.omitted.iter().any(
            |d| d.id.contains("location.length.x") && matches!(d.state, State::Covered { .. })
        ));
        if style == "ordinate" {
            assert!(
                generated
                    .iter()
                    .all(|d| d.value < 0. && d.placed.as_ref().unwrap().arrows.len() == 1)
            );
        }
        let saved: Spec = serde_json::from_slice(&serde_json::to_vec(&p.spec).unwrap()).unwrap();
        let reload: Analysis = serde_json::from_slice(&serde_json::to_vec(&a).unwrap()).unwrap();
        assert_eq!(
            serde_json::to_value(&p).unwrap(),
            serde_json::to_value(model::plan_recovered(&reload, &saved).unwrap()).unwrap()
        );
        let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
        let drawings = model::render_sheets(&part, &a, &p, &font).unwrap();
        for drawing in &drawings {
            assert_eq!(
                model::embedded_spec(drawing.svg().as_bytes())
                    .unwrap()
                    .unwrap(),
                p.spec
            );
        }
        // The principal view and its scheme may live on a later physical page.
        let sheet = p
            .views
            .iter()
            .find(|v| v.id == generated[0].view)
            .unwrap()
            .sheet;
        let drawing = &drawings[(sheet - 1) as usize];
        let mut marks = draftwright::lint::marks(drawing);
        let issues = draftwright::lint::lint(&a, drawing);
        assert!(
            !issues.iter().any(|i| [
                "dimension_scheme_invalid",
                "closed_dimension_chain",
                "duplicate_measurement",
                "angular_geometry_mismatch",
                "label_vs_measured"
            ]
            .contains(&i.code.as_str())),
            "{style}: {issues:#?}"
        );
        assert!(
            !issues
                .iter()
                .any(|i| i.code == "feature_not_located" && i.id.ends_with("location.length.x")),
            "{style}: authored scheme is a rooted locating network: {issues:#?}"
        );
        let name = generated[0].id.clone();
        marks.iter_mut().find(|m| m.name == name).unwrap().text = "999 ±0.1".into();
        assert!(
            draftwright::lint::check(&a, &marks)
                .iter()
                .any(|i| i.code == "dimension_scheme_invalid")
        );
    }
}
#[test]
fn absent_or_removed_scheme_is_exactly_the_automatic_plan() {
    let (_, a) = part("turned");
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let s = whole("baseline", "z", "front", "max");
    let cleared = model::edit(
        &s,
        &json!([{"op":"remove","path":"dimension_schemes/locations"}]),
    )
    .unwrap();
    assert_eq!(
        serde_json::to_value(p).unwrap(),
        serde_json::to_value(model::plan_recovered(&a, &cleared).unwrap()).unwrap()
    );
    assert!(
        !serde_json::to_string(&Spec::default())
            .unwrap()
            .contains("dimension_schemes")
    );
}
#[test]
fn bad_references_axes_overlaps_and_conflicting_edits_are_refused() {
    let (_, a) = part("scattered_holes");
    for s in [
        whole("baseline", "x", "plan", "face:12"),
        whole("baseline", "x", "side", "min"),
        spec(
            json!({"dimension_schemes":{"bad":{"scheme":"chain","axis":"x","targets":["f:hole:missing"]}}}),
        ),
        spec(json!({"dimension_schemes":{"bad":{"scheme":"chain","axis":"x","view":"unknown"}}})),
    ] {
        let e = model::plan_recovered(&a, &s).unwrap_err();
        assert!(!e.message.is_empty());
    }
    assert!(
        serde_json::from_value::<Spec>(
            json!({"dimension_schemes":{"bad":{"scheme":"chain","axis":"x","view":"plan","x":10}}})
        )
        .is_err()
    );
    let p = model::plan(&a, &Spec::default()).unwrap();
    let id = p
        .dimensions
        .iter()
        .find(|d| d.id.contains("location.length.x"))
        .unwrap()
        .id
        .clone();
    let mut s = whole("chain", "x", "plan", "min");
    s.dimensions.insert(
        id,
        serde_json::from_value(json!({"tolerance":"±0.1"})).unwrap(),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.label.contains("±0.1"))
    );
    let p = model::plan_recovered(&a, &spec(json!({"dimension_schemes":{"a":{"scheme":"chain","axis":"x","view":"plan"},"b":{"scheme":"baseline","axis":"x","view":"plan"}}}))).unwrap();
    assert!(p.problems.iter().any(|p| p.code == "duplicate_measurement"));
    assert!(p.dimensions.iter().any(|d| d.placed.is_some()));
}
#[test]
fn session_preview_undo_redo_and_refusal_keep_transaction_semantics() {
    let (part, a) = part("scattered_holes");
    let mut session = Session::new(part, a, Spec::default()).unwrap();
    let before = serde_json::to_value(session.plan()).unwrap();
    let op = Op::set(
        "dimension_schemes/locations",
        json!({"scheme":"chain","axis":"x","view":"plan"}),
    );
    session.apply(std::slice::from_ref(&op), false).unwrap();
    assert_eq!(serde_json::to_value(session.plan()).unwrap(), before);
    session.apply(&[op], true).unwrap();
    let after = serde_json::to_value(session.plan()).unwrap();
    session.undo().unwrap();
    assert_eq!(serde_json::to_value(session.plan()).unwrap(), before);
    session.redo().unwrap();
    assert_eq!(serde_json::to_value(session.plan()).unwrap(), after);
    assert!(
        session
            .apply(
                &[Op::set(
                    "dimension_schemes/locations/from",
                    json!("face:missing")
                )],
                true
            )
            .is_err()
    );
    assert_eq!(serde_json::to_value(session.plan()).unwrap(), after);
    assert!(
        session
            .options("sheet")
            .unwrap()
            .settable
            .contains_key("dimension_schemes/<name>")
    );
}

#[test]
fn pattern_members_and_turned_shoulders_are_owned_without_redundant_pitch() {
    for name in ["two_grids", "turned"] {
        let (part, a) = part(name);
        let (axis, view, target) = if name == "turned" {
            let f = a.features.iter().find(|f| f.kind == "turned_step").unwrap();
            let band =
                draftwright::lint::turned::band(&draftwright::bridge::holes::frame(&a), f).unwrap();
            (
                ["x", "y", "z"][band.axis],
                if band.axis == 1 { "side" } else { "front" },
                None,
            )
        } else {
            (
                "x",
                "plan",
                Some(a.recognition.as_ref().unwrap().patterns[0].id.clone()),
            )
        };
        let mut intent = json!({"scheme":"baseline","axis":axis,"view":view,"from":"max"});
        if let Some(id) = target {
            intent["targets"] = json!([id]);
        }
        let s = spec(json!({"dimension_schemes":{"profile":intent}}));
        let p = model::plan_recovered(&a, &s).unwrap();
        assert!(p.dimensions.iter().any(|d| d.id.starts_with("d:scheme:")));
        let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
        let d = model::render(&part, &a, &p, &font).unwrap();
        let issues = draftwright::lint::lint(&a, &d);
        assert!(
            !issues.iter().any(|i| [
                "dimension_scheme_invalid",
                "closed_dimension_chain",
                "over_dimensioned_pattern",
                "axial_length_missing"
            ]
            .contains(&i.code.as_str())),
            "{name}: {issues:#?}"
        );
    }
}

#[test]
fn fingerprints_survive_reload_and_generated_owners_accept_tolerance_precision_and_authored_source()
{
    use model::dimension_geometry::Reference;
    let (part, a) = part("plate");
    let faces: Vec<_> = a
        .dimension_references()
        .iter()
        .filter_map(|(id, rs)| match rs.as_slice() {
            [Reference::Plane { normal, origin, .. }] if normal[0].abs() > 0.999 => {
                Some((id.clone(), origin[0]))
            }
            _ => None,
        })
        .collect();
    let low = faces
        .iter()
        .min_by(|a, b| a.1.total_cmp(&b.1))
        .unwrap()
        .0
        .clone();
    let high = faces
        .iter()
        .max_by(|a, b| a.1.total_cmp(&b.1))
        .unwrap()
        .0
        .clone();
    let mut s = spec(
        json!({"dimension_schemes":{"width":{"scheme":"baseline","axis":"x","view":"front","from":low,"targets":[high]}}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    let id = p
        .dimensions
        .iter()
        .find(|d| d.id.starts_with("d:scheme:"))
        .unwrap()
        .id
        .clone();
    s.dimensions.insert(
        id.clone(),
        serde_json::from_value(
            json!({"tolerance":"±0.1","place":{"side":"below","lane":1,"pinned":true}}),
        )
        .unwrap(),
    );
    s.precision.insert(id.clone(), 2);
    s.dimension_source = model::DimensionSource::Authored;
    let p = model::plan_recovered(&a, &s).unwrap();
    let owner = p.dimensions.iter().find(|d| d.id == id).unwrap();
    assert!(
        owner.label.contains(".00") && owner.label.contains("±0.1"),
        "{}",
        owner.label
    );
    assert!(owner.placed.is_some());
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    let mut marks = draftwright::lint::marks(&drawing);
    let repeated = marks.iter().find(|m| m.name == id).unwrap().clone();
    marks.push(draftwright::lint::Mark {
        name: "bad-extra-link".into(),
        ..repeated
    });
    assert!(
        draftwright::lint::check(&a, &marks)
            .iter()
            .any(|i| i.code == "closed_dimension_chain")
    );
    marks.retain(|m| m.name != id && m.name != "bad-extra-link");
    assert!(draftwright::lint::check(&a, &marks).iter().any(|i| {
        i.code == "missing_principal_dimension"
            && i.requirements
                .iter()
                .any(|r| r.feature == "envelope" && r.parameter == "width.length")
    }));
    // Fingerprints are safe across a reloaded Analysis and a stale binding; feature ranks are not.
    s.binding = Some(a.binding.clone());
    s.binding.as_mut().unwrap().source_sha256 = "old-revision".into();
    assert!(model::plan_recovered(&a, &s).is_ok());
    s.dimension_schemes.get_mut("width").unwrap().targets = vec!["f:hole:1".into()];
    assert!(
        model::plan_recovered(&a, &s)
            .unwrap_err()
            .message
            .contains("rebind")
    );
}

#[test]
fn slot_pattern_expands_its_members_and_retires_its_pitch() {
    use std::io::Read;
    let dir =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/recognition/slot_patterns");
    let (part, mut a) = model::analyse(&dir.join("slot_array.step")).unwrap();
    let mut source = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(dir.join("slot_array.json.gz")).unwrap())
        .read_to_string(&mut source)
        .unwrap();
    model::recognise(&part, &mut a, &source).unwrap();
    let id = a
        .features
        .iter()
        .find(|f| f.kind == "slot_pattern")
        .unwrap()
        .id
        .clone();
    let s = spec(
        json!({"dimension_schemes":{"slots":{"scheme":"chain","axis":"x","view":"plan","targets":[id]}}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.omitted
            .iter()
            .any(|o| o.id.ends_with("/pitch.length") && matches!(o.state, State::Covered { .. }))
    );
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    let issues = draftwright::lint::lint(&a, &drawing);
    assert!(
        !issues
            .iter()
            .any(|i| i.code.starts_with("slot_pattern_requirement")
                || i.code == "dimension_scheme_invalid"),
        "{issues:#?}"
    );
}

#[test]
fn whole_view_expands_all_intervals_of_a_counted_grid_pitch() {
    let (part, a) = part("mounting_plate");
    let p = model::plan_recovered(&a, &whole("chain", "x", "plan", "min")).unwrap();
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    let issues = draftwright::lint::lint(&a, &drawing);
    assert!(
        !issues
            .iter()
            .any(|i| i.code.starts_with("hole_pattern_requirement")
                || i.code == "dimension_scheme_invalid"),
        "{issues:#?}"
    );
}

#[test]
fn generated_feature_edits_cannot_rebind_silently_or_duplicate_a_target_across_views() {
    let (_, a) = part("scattered_holes");
    let mut s = whole("baseline", "x", "plan", "min");
    let p = model::plan_recovered(&a, &s).unwrap();
    let id = p
        .dimensions
        .iter()
        .find(|d| d.id.starts_with("d:scheme:"))
        .unwrap()
        .id
        .clone();
    s.dimensions.insert(id, Default::default());
    s.binding = Some(a.binding.clone());
    s.binding.as_mut().unwrap().source_sha256 = "another-revision".into();
    assert!(
        model::plan_recovered(&a, &s)
            .unwrap_err()
            .message
            .contains("rebind")
    );
    let id = a
        .features
        .iter()
        .find(|f| f.kind == "hole")
        .unwrap()
        .id
        .clone();
    let s = spec(json!({"dimension_schemes":{
        "first":{"scheme":"baseline","axis":"x","view":"front","targets":[id]},
        "second":{"scheme":"baseline","axis":"x","view":"plan","targets":[id]}
    }}));
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(p.problems.iter().any(|p| p.code == "duplicate_measurement"));
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.view == "v:front")
    );
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.view == "v:plan")
    );
}

#[test]
fn separately_named_schemes_do_not_close_the_automatic_overall() {
    use model::dimension_geometry::Reference;
    let (part, a) = part("turned");
    let band = draftwright::lint::turned::band(
        &draftwright::bridge::holes::frame(&a),
        a.features.iter().find(|f| f.kind == "turned_step").unwrap(),
    )
    .unwrap();
    let k = band.axis;
    let shoulder = a
        .dimension_references()
        .iter()
        .find_map(|(id, rs)| match rs.as_slice() {
            [Reference::Plane { normal, origin, .. }]
                if normal[k].abs() > 0.999999
                    && origin[k] > a.envelope[0][k] + 1e-6
                    && origin[k] < a.envelope[1][k] - 1e-6 =>
            {
                Some(id.clone())
            }
            _ => None,
        })
        .unwrap();
    let view = if k == 1 { "side" } else { "front" };
    let s = spec(json!({"dimension_schemes":{
        "near":{"scheme":"baseline","axis":(["x","y","z"][k]),"view":view,"targets":[shoulder]},
        "far":{"scheme":"baseline","axis":(["x","y","z"][k]),"view":view,"from":shoulder,"targets":["max"]}
    }}));
    let p = model::plan_recovered(&a, &s).unwrap();
    let overall = format!("d:envelope/{}", draftwright::lint::envelope::PARAMETERS[k]);
    assert!(
        p.omitted
            .iter()
            .any(|o| o.id == overall && matches!(o.state, State::Covered { .. }))
    );
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    assert!(!draftwright::lint::lint(&a, &drawing).iter().any(|i| {
        [
            "closed_dimension_chain",
            "dimension_scheme_invalid",
            "missing_principal_dimension",
            "axial_length_missing",
        ]
        .contains(&i.code.as_str())
    }));
}

#[test]
fn partial_counted_pitch_is_refused_instead_of_losing_its_far_interval() {
    let (_, a) = part("mounting_plate");
    let pattern = &a.recognition.as_ref().unwrap().patterns[0];
    let mut members: Vec<_> = pattern
        .members
        .iter()
        .map(|id| a.features.iter().find(|f| f.id == *id).unwrap())
        .collect();
    members.sort_by(|p, q| p.fingerprint.position[0].total_cmp(&q.fingerprint.position[0]));
    members.dedup_by(|p, q| (p.fingerprint.position[0] - q.fingerprint.position[0]).abs() < 1e-6);
    assert!(members.len() > 2);
    let s = spec(
        json!({"dimension_schemes":{"partial":{"scheme":"baseline","axis":"x","view":"plan","from":members[0].id,"targets":[members[1].id]}}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.id.starts_with("d:scheme:"))
    );
    assert!(p.dimensions.iter().any(
        |d| d.placed.is_some() && d.measurements.iter().any(|m| m.parameter.contains("pitch"))
    ));
}

#[test]
fn whole_axis_keeps_every_shoulder_of_counted_turned_lengths() {
    let (part, a) = part("k5/turned-step-repeated-lengths");
    let f = a.features.iter().find(|f| f.kind == "turned_step").unwrap();
    let b = draftwright::lint::turned::band(&draftwright::bridge::holes::frame(&a), f).unwrap();
    let s = whole(
        "chain",
        ["x", "y", "z"][b.axis],
        if b.axis == 1 { "side" } else { "front" },
        "min",
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    let issues = draftwright::lint::lint(&a, &drawing);
    assert!(
        !issues.iter().any(|i| [
            "axial_length_missing",
            "closed_dimension_chain",
            "dimension_scheme_invalid"
        ]
        .contains(&i.code.as_str())),
        "{issues:#?}"
    );
}

#[test]
fn fitting_authored_chain_links_share_one_dimension_line() {
    let (_, a) = part("mounting_plate");
    for scale in ["1:1", "2:1"] {
        let mut s = whole("chain", "x", "plan", "min");
        s.sheet.scale = Some(scale.into());
        let p = model::plan_recovered(&a, &s).unwrap();
        let mut links: Vec<_> = p
            .dimensions
            .iter()
            .filter(|d| d.id.starts_with("d:scheme:"))
            .collect();
        links.sort_by(|a, b| a.value.total_cmp(&b.value));
        assert_eq!(
            links.iter().map(|d| d.value).collect::<Vec<_>>(),
            [15., 20., 30., 30.]
        );
        let line = links[0].placed.as_ref().unwrap().arrows[0][0][1];
        for link in links {
            let placed = link.placed.as_ref().unwrap();
            assert_eq!(placed.arrows.len(), 2);
            for arrow in &placed.arrows {
                assert!((arrow[0][1] - line).abs() < 1e-6, "{}: {placed:?}", link.id);
            }
            let (lo, hi) = (placed.arrows[0][0][0], placed.arrows[1][0][0]);
            assert!(placed.label_box[0] > lo.min(hi));
            assert!(placed.label_box[2] < lo.max(hi));
        }
    }
}

#[test]
fn recognition_split_and_scheme_label_edits_share_the_same_inventory() {
    let (part, a) = part("mounting_plate");
    let pattern = &a.recognition.as_ref().unwrap().patterns[0].id;
    let mut s = whole("chain", "x", "plan", "min");
    s.sheet.scale = Some("2:1".into());
    s.features.insert(
        pattern.clone(),
        serde_json::from_value(json!({"split":true})).unwrap(),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    let owner = p
        .dimensions
        .iter()
        .find(|d| d.id.starts_with("d:scheme:"))
        .unwrap();
    let id = owner.id.clone();
    s = p.spec.clone();
    s.dimensions.insert(
        id.clone(),
        serde_json::from_value(json!({"text":{"prefix":"TYP"},"style":"basic"})).unwrap(),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    let edited = p.dimensions.iter().find(|d| d.id == id).unwrap();
    assert!(edited.label.starts_with("TYP "));
    assert_eq!(edited.measurements, owner.measurements);
    assert!(edited.from_spec.contains(&"text".into()));
    assert!(edited.from_spec.contains(&"style.basic".into()));
    assert!(
        edited
            .from_spec
            .contains(&"dimension_schemes/locations".into())
    );
    assert_eq!(p.recognition_corrections.len(), 1);
    let saved: Spec = serde_json::from_slice(&serde_json::to_vec(&p.spec).unwrap()).unwrap();
    assert_eq!(
        serde_json::to_value(&p).unwrap(),
        serde_json::to_value(model::plan_recovered(&a, &saved).unwrap()).unwrap()
    );
    let effective = draftwright::spec_gdt::effective(&a, &p.spec).unwrap();
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    assert!(
        !draftwright::lint::lint(&effective, &drawing)
            .iter()
            .any(|i| [
                "dimension_scheme_invalid",
                "closed_dimension_chain",
                "label_vs_measured"
            ]
            .contains(&i.code.as_str()))
    );
    let mut marks = draftwright::lint::marks(&drawing);
    marks.iter_mut().find(|m| m.name == id).unwrap().text = "TYP 999".into();
    assert!(
        draftwright::lint::check(&effective, &marks)
            .iter()
            .any(|i| i.code == "dimension_scheme_invalid")
    );
}

#[test]
fn recognition_rebinding_cannot_silently_refresh_rank_based_scheme_targets() {
    let (_, a) = part("scattered_holes");
    let holes: Vec<_> = a.features.iter().filter(|f| f.kind == "hole").collect();
    let s = spec(json!({
        "features":{&holes[0].id:{"suppress":true}},
        "dimension_schemes":{"locations":{"scheme":"chain","axis":"x","view":"plan","targets":[holes[1].id]}}
    }));
    let mut saved = model::plan_recovered(&a, &s).unwrap().spec;
    saved.binding.as_mut().unwrap().source_sha256 = "another-revision".into();
    assert!(
        model::plan_recovered(&a, &saved)
            .unwrap_err()
            .message
            .contains("rebind")
    );
}
