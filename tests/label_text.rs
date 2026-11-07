#[path = "common/mcp_schema.rs"]
mod mcp_schema;

#[path = "common/cache.rs"]
mod cache;

use draftwright::{
    Session, dims,
    items::{Op, key},
    model::{self, Spec},
};
use serde_json::json;
use std::path::PathBuf;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}
fn analysed(
    name: &str,
) -> (
    std::sync::Arc<draftwright::haecceity::Part>,
    model::Analysis,
) {
    cache::native(&root().join(format!("tests/fixtures/parts/{name}.step")))
}
fn font() -> draftwright::text::Font {
    draftwright::text::Font::open(&root().join("fonts").join(dims::FONT)).unwrap()
}

#[test]
fn full_wording_preserves_measurements_searchable_text_and_ink_reservation() {
    for name in ["plate", "turned", "prismatic"] {
        let (part, a) = analysed(name);
        let base = cache::default_plan(&a, false);
        for old in base
            .dimensions
            .iter()
            .filter(|d| d.placed.is_some() && !d.reference)
            .take(3)
        {
            for text in [
                json!({"prefix":"TYP", "suffix":"(NOM)"}),
                json!({"template":"{value} AFTER PLATING"}),
            ] {
                let spec: Spec =
                    serde_json::from_value(json!({"dimensions":{&old.id:{"text":text}}})).unwrap();
                let saved = serde_json::to_string(&spec).unwrap();
                let read: Spec = serde_json::from_str(&saved).unwrap();
                assert_eq!(spec, read);
                let plan = model::plan(&a, &read).unwrap();
                let d = plan.dimensions.iter().find(|d| d.id == old.id).unwrap();
                let expected = if text.get("template").is_some() {
                    format!("{} AFTER PLATING", old.label)
                } else {
                    format!("TYP {} (NOM)", old.label)
                };
                assert_eq!(d.label, expected);
                assert_eq!(d.value, old.value);
                assert_eq!(d.measurements, old.measurements);
                assert!(!d.default);
                assert!(d.from_spec.iter().any(|s| s == "text"));
                assert!(d.basis.contains("from_spec"));
                let drawing = model::render(&part, &a, &plan, &font()).unwrap();
                let annotation = drawing
                    .annotations
                    .iter()
                    .find(|an| an.name == d.id)
                    .unwrap();
                assert!(
                    annotation
                        .runs
                        .iter()
                        .any(|&i| drawing.text[i].text == expected)
                );
                let ink = dims::label_ink(&d.terms, &font());
                let p = d.placed.as_ref().unwrap();
                let (w, h) = (ink.max[0] - ink.min[0], ink.max[1] - ink.min[1]);
                let (w, h) = if p.rotation.abs() % 180.0 == 90.0 {
                    (h, w)
                } else {
                    (w, h)
                };
                assert!(p.label_box[2] - p.label_box[0] >= w - 1e-6);
                assert!(p.label_box[3] - p.label_box[1] >= h - 1e-6);
                assert!(
                    !draftwright::lint::structural::check(&drawing)
                        .iter()
                        .any(|i| i.code == "label_vs_measured"
                            && i.annotation.as_deref() == Some(&d.id))
                );
            }
        }
        let absent: Spec = serde_json::from_value(json!({})).unwrap();
        assert_eq!(
            serde_json::to_value(model::plan(&a, &absent).unwrap()).unwrap(),
            serde_json::to_value(&base).unwrap()
        );
        assert!(!serde_json::to_string(&absent).unwrap().contains("\"text\""));
    }
}

#[test]
fn reference_and_basic_are_explicit_and_keep_the_measured_claims() {
    let (part, a) = analysed("plate");
    let base = cache::default_plan(&a, false);
    let old = base
        .dimensions
        .iter()
        .find(|d| d.kind == "linear" && !d.reference && d.placed.is_some())
        .unwrap();
    for style in ["reference", "basic"] {
        let spec: Spec = serde_json::from_value(
            json!({"dimensions":{&old.id:{"style":style,"text":{"suffix":"REF"}}}}),
        )
        .unwrap();
        let p = model::plan(&a, &spec).unwrap();
        let d = p.dimensions.iter().find(|d| d.id == old.id).unwrap();
        assert_eq!(d.reference, style == "reference");
        assert_eq!(
            d.label,
            if style == "reference" {
                format!("({} REF)", old.label)
            } else {
                format!("{} REF", old.label)
            }
        );
        assert_eq!(d.measurements, old.measurements);
        assert_eq!(d.value, old.value);
        assert!(d.from_spec.contains(&format!("style.{style}")));
        let drawing = model::render(&part, &a, &p, &font()).unwrap();
        assert!(
            !draftwright::lint::structural::check(&drawing)
                .iter()
                .any(|i| i.code == "label_vs_measured")
        );
        if style == "basic" {
            let ink = dims::label_ink(&d.terms, &font());
            let placed = d.placed.as_ref().unwrap();
            let width = if placed.rotation == 90.0 {
                ink.max[1] - ink.min[1]
            } else {
                ink.max[0] - ink.min[0]
            };
            assert!(placed.label_box[2] - placed.label_box[0] >= width + 1.5 - 1e-6);
        }
    }
}

#[test]
fn invalid_wording_is_refused_and_unapplied_edits_warn() {
    let (_, a) = analysed("plate");
    let id = "d:envelope/width.length";
    for text in [
        json!({"template":"AFTER PLATING"}),
        json!({"template":"99 AFTER PLATING"}),
        json!({"template":"{value} = 99"}),
        json!({"template":"{value} {value}"}),
        json!({"template":"{value} {other}"}),
        json!({"template":"{value}","prefix":"TYP"}),
        json!({"suffix":"99"}),
        json!({"prefix":"\nTYP"}),
        json!({"suffix":"±"}),
    ] {
        let spec: Spec = serde_json::from_value(json!({"dimensions":{id:{"text":text}}})).unwrap();
        let error = model::plan(&a, &spec).unwrap_err();
        assert!(error.message.contains(id));
        assert!(error.message.contains("{value}"));
        assert!(
            model::edit(
                &Spec::default(),
                &json!([{"op":"set","path":format!("dimensions/{}/text",key(id)),"value":text}])
            )
            .is_err()
        );
    }
    for spec in [
        json!({"dimensions":{"d:missing/value":{"text":{"suffix":"REF"}}}}),
        json!({"annotations":{"pmi:missing":{"text":{"template":"{value} REF"}}}}),
    ] {
        let plan = model::plan(&a, &serde_json::from_value(spec).unwrap()).unwrap();
        assert!(
            plan.problems
                .iter()
                .any(|p| p.code.ends_with("edit_unapplied"))
        );
        assert!(plan.dimensions.iter().any(|d| d.placed.is_some()));
    }
    let base = cache::default_plan(&a, false);
    if let Some(o) = base
        .omitted
        .iter()
        .find(|o| matches!(o.state, draftwright::items::State::Covered { .. }))
    {
        let spec: Spec =
            serde_json::from_value(json!({"dimensions":{&o.id:{"text":{"suffix":"REF"}}}}))
                .unwrap();
        let plan = model::plan(&a, &spec).unwrap();
        assert!(
            plan.problems
                .iter()
                .any(|p| p.code == "dimension_edit_unapplied")
        );
        assert!(plan.dimensions.iter().any(|d| d.placed.is_some()));
    }
}

#[test]
fn lint_reads_live_measured_text_even_with_unit_substrings_in_prose_and_refuses_damaged_text() {
    let (part, a) = analysed("plate");
    let id = "d:envelope/width.length";
    let base = cache::default_plan(&a, false);
    let old = base.dimensions.iter().find(|d| d.id == id).unwrap();
    for style in [json!(null), json!("reference"), json!("basic")] {
        let spec: Spec = serde_json::from_value(json!({"dimensions":{id:{"text":{"prefix":"DEGREASE CLEAN", "suffix":"REF"}, "style":style}}})).unwrap();
        let plan = model::plan(&a, &spec).unwrap();
        let drawing = model::render(&part, &a, &plan, &font()).unwrap();
        assert!(
            !draftwright::lint::structural::check(&drawing)
                .iter()
                .any(|i| i.code == "label_vs_measured")
        );
        let index = drawing
            .annotations
            .iter()
            .find(|a| a.name == id)
            .unwrap()
            .runs[0];
        for bad in [
            drawing.text[index].text.replace(&old.label, "999"),
            "REF".into(),
        ] {
            let mut damaged = drawing.clone();
            damaged.text[index].text = bad;
            assert!(
                draftwright::lint::structural::check(&damaged)
                    .iter()
                    .any(|i| i.code == "label_vs_measured" && i.annotation.as_deref() == Some(id))
            );
        }
    }
}

#[test]
fn session_edits_preview_history_replay_and_options_use_existing_ops() {
    let (part, a) = model::analyse_native(&root().join("tests/fixtures/parts/plate.step")).unwrap();
    let mut session = Session::new(part, a, Spec::default()).unwrap();
    let id = "d:envelope/width.length";
    let old = session.plan().clone();
    let op = Op::set(
        format!("dimensions/{}/text", key(id)),
        json!({"template":"{value} AFTER PLATING"}),
    );
    let mut state = session.state().clone();
    session.apply(std::slice::from_ref(&op), false).unwrap();
    assert_eq!(
        serde_json::to_value(session.plan()).unwrap(),
        serde_json::to_value(&old).unwrap()
    );
    let delta = session.apply(&[op], true).unwrap();
    delta.apply_to(&mut state).unwrap();
    assert_eq!(
        serde_json::to_value(&state.plan).unwrap(),
        serde_json::to_value(session.plan()).unwrap()
    );
    assert!(
        session
            .options(id)
            .unwrap()
            .settable
            .contains_key(&format!("dimensions/{}/text", key(id)))
    );
    assert!(
        session
            .apply(
                &[Op::set(
                    format!("dimensions/{}/text", key(id)),
                    json!({"template":"wrong"})
                )],
                true
            )
            .is_err()
    );
    let saved: Spec =
        serde_json::from_str(&serde_json::to_string(session.spec()).unwrap()).unwrap();
    assert_eq!(&saved, session.spec());
    session.undo().unwrap();
    assert_eq!(
        serde_json::to_value(session.plan()).unwrap(),
        serde_json::to_value(&old).unwrap()
    );
    session.redo().unwrap();
    assert_eq!(session.spec(), &saved);
}

#[test]
fn pmi_callout_wording_is_reserved_drawn_searchable_and_structured_symbols_refuse_it() {
    let file = root().join("tests/fixtures/pmi/step/nist_ftc_10_asme1_ap242-e2.stp.gz");
    let file = if file.exists() {
        file
    } else {
        std::fs::read_dir(root().join("tests/fixtures/pmi/step"))
            .unwrap()
            .map(|e| e.unwrap().path())
            .find(|p| {
                p.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .starts_with("nist_ftc_10_asme1_ap242-e2.")
            })
            .unwrap()
    };
    let (part, a) = cache::native(&file);
    let base = cache::default_plan(&a, false);
    let label = base
        .gdt
        .iter()
        .find(|g| g.kind == "label" && g.placed.is_some())
        .unwrap();
    let spec: Spec = serde_json::from_value(
        json!({"annotations":{&label.id:{"text":{"template":"{value} AFTER PLATING"}}}}),
    )
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let edited = plan.gdt.iter().find(|g| g.id == label.id).unwrap();
    assert_eq!(edited.label, format!("{} AFTER PLATING", label.label));
    assert_eq!(edited.from_spec, ["text"]);
    assert!(
        edited.placed.as_ref().unwrap().label_box[2] - edited.placed.as_ref().unwrap().label_box[0]
            > label.placed.as_ref().unwrap().label_box[2]
                - label.placed.as_ref().unwrap().label_box[0]
    );
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    let annotation = drawing
        .annotations
        .iter()
        .find(|a| a.name == label.id)
        .unwrap();
    assert!(
        annotation
            .runs
            .iter()
            .any(|&i| drawing.text[i].text == edited.label)
    );
    if let Some(g) = base.gdt.iter().find(|g| g.kind != "label") {
        let spec: Spec =
            serde_json::from_value(json!({"annotations":{&g.id:{"text":{"suffix":"REF"}}}}))
                .unwrap();
        assert!(
            model::plan(&a, &spec)
                .unwrap_err()
                .message
                .contains("structured meanings")
        );
    }
}

#[test]
fn compound_callouts_keep_every_value_tolerance_and_fit_and_lint_rejects_false_live_values() {
    let (part, mut a) = analysed("plate");
    let hole = a.features.iter_mut().find(|f| f.kind == "hole").unwrap();
    let feature = hole.id.clone();
    hole.parameters.insert("diameter".into(), json!(6.25));
    hole.parameters.insert("bottom".into(), json!("flat"));
    hole.parameters.insert("depth".into(), json!(4.75));
    hole.parameters
        .insert("cbore".into(), json!({"diameter":8.75,"depth":1.875}));
    hole.parameters.insert(
        "csink".into(),
        json!({"major_diameter":10.25,"included_angle":82.5}),
    );
    let mut spec: Spec = serde_json::from_value(json!({
        "hole_table":"never", "features":{&feature:{"fit":"H7"}},
        "precision": {format!("d:{feature}/bore.diameter"):2,format!("d:{feature}/bore.depth"):2,format!("d:{feature}/counterbore.diameter"):2,format!("d:{feature}/counterbore.depth"):3,format!("d:{feature}/countersink.diameter"):2}
    })).unwrap();
    let base = model::plan(&a, &spec).unwrap();
    let old = base
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == feature && m.parameter == "bore.diameter")
        })
        .unwrap();
    spec.dimensions.insert(
        old.id.clone(),
        serde_json::from_value(json!({"text":{"template":"DRILL {value} AFTER PLATING"}})).unwrap(),
    );
    let p = model::plan(&a, &spec).unwrap();
    let d = p.dimensions.iter().find(|d| d.id == old.id).unwrap();
    // Each callout symbol stays on the line of its value (the maintainer's wrap, 2026-10-28;
    // draftwright's 48 mm wrap gave "DRILL ⌀6.25 H7 ↧ 4.75 ⌴\n⌀8.75 ↧ 1.875 ⌵ ⌀10.25 ×\n82.5°
    // AFTER PLATING").
    assert_eq!(
        d.label,
        "DRILL ⌀6.25 H7 ↧ 4.75\n⌴ ⌀8.75 ↧ 1.875\n⌵ ⌀10.25 × 82.5° AFTER\nPLATING"
    );
    assert_eq!(d.value, old.value);
    assert_eq!(d.measurements, old.measurements);
    assert!(d.label.contains("H7"));
    for term in ["6.25", "4.75", "8.75", "1.875", "10.25", "82.5"] {
        assert!(d.label.contains(term), "{}", d.label);
    }
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    let mark = draftwright::lint::marks(&drawing)
        .into_iter()
        .find(|m| m.name == d.id)
        .unwrap();
    assert_eq!(
        mark.text,
        old.label.split_whitespace().collect::<Vec<_>>().join(" ")
    );
    assert!(!mark.claims.is_empty());
    let run = drawing
        .annotations
        .iter()
        .find(|a| a.name == d.id)
        .unwrap()
        .runs[0];
    let mut damaged = drawing.clone();
    damaged.text[run].text = damaged.text[run].text.replace("6.25", "99");
    let original = draftwright::lint::lint(&a, &drawing);
    let changed = draftwright::lint::lint(&a, &damaged);
    assert!(
        changed.iter().any(|i| !original.contains(i)
            && matches!(
                i.code.as_str(),
                "hole_requirement_unverifiable"
                    | "feature_not_dimensioned"
                    | "hole_requirement_missing"
            )),
        "{changed:?}"
    );
}

#[test]
fn quantity_prefix_stays_visible_to_lint_and_cannot_double_an_existing_count() {
    let (part, a) = analysed("plate");
    let id = "d:envelope/width.length";
    let spec: Spec = serde_json::from_value(
        json!({"dimensions":{id:{"text":{"prefix":"2×","suffix":"TYP"},"style":"reference"}}}),
    )
    .unwrap();
    let p = model::plan(&a, &spec).unwrap();
    let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
    assert!(d.label.starts_with("(2× "));
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    let m = draftwright::lint::marks(&drawing)
        .into_iter()
        .find(|m| m.name == id)
        .unwrap();
    assert!(m.text.starts_with("(2× "));
    assert!(
        !draftwright::lint::structural::check(&drawing)
            .iter()
            .any(|i| i.code == "label_vs_measured")
    );
    let base = cache::default_plan(&a, false);
    let counted = base
        .dimensions
        .iter()
        .find(|d| d.label.contains('×'))
        .unwrap();
    let spec: Spec =
        serde_json::from_value(json!({"dimensions":{&counted.id:{"text":{"prefix":"2×"}}}}))
            .unwrap();
    assert!(
        model::plan(&a, &spec)
            .unwrap_err()
            .message
            .contains("already has a quantity")
    );
}

#[test]
fn mcp_saved_spec_shape_and_closed_object_cardinality_remain_strict() {
    let schema = mcp_schema::with_full_spec("get_spec", true);
    for value in [
        json!({"prefix":"2×","suffix":"TYP"}),
        json!({"template":"{value} AFTER PLATING"}),
    ] {
        let spec: Spec = serde_json::from_value(
            json!({"dimensions":{"d:envelope/width.length":{"text":value,"style":"basic"}}}),
        )
        .unwrap();
        draftwright::mcp::validate(&schema, &json!({"spec_version":"v1","spec":spec})).unwrap();
    }
    assert!(draftwright::mcp::validate(&schema, &json!({"spec_version":"v1"})).is_err());
    assert!(draftwright::mcp::validate(&schema, &json!({"error":{"code":"invalid"}})).is_err());
    assert!(
        draftwright::mcp::validate(
            &schema,
            &json!({"spec_version":"v1","spec":{"dimensions":{"d:x":{"text":{"prefix":99}}}}})
        )
        .is_err()
    );
}

#[path = "common/corpus.rs"]
mod corpus;

#[test]
fn automatic_radius_note_wording_keeps_coverage_and_live_value_checks() {
    let Some(dir) = corpus::dir() else {
        return;
    };
    let (part, a) = cache::native(&dir.join("nist/nist_ctc_03_asme1_rc.stp"));
    let base = cache::default_plan(&a, false);
    let note = base
        .notes
        .iter()
        .find(|n| !n.measurements.is_empty() && n.placed.is_some())
        .unwrap();
    let spec: Spec = serde_json::from_value(
        json!({"annotations":{&note.id:{"text":{"prefix":"TYP","suffix":"AFTER PLATING"}}}}),
    )
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let edited = plan.notes.iter().find(|n| n.id == note.id).unwrap();
    assert_eq!(edited.text, format!("TYP {} AFTER PLATING", note.text));
    assert_eq!(edited.from_spec, ["text"]);
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    let findings = draftwright::lint::lint(&a, &drawing);
    assert!(
        !findings
            .iter()
            .any(|i| i.code == "blend_requirement_missing"),
        "note: {edited:?}; wrapper: {:?}; marks: {:?}; missing: {:?}",
        drawing.sheet.label_text.get(&note.id),
        draftwright::lint::marks(&drawing)
            .iter()
            .filter(|m| m.name == note.id)
            .collect::<Vec<_>>(),
        findings
            .iter()
            .filter(|i| i.code == "blend_requirement_missing")
            .collect::<Vec<_>>()
    );
    let radius = format!("R{}", dims::fmt(note.measurements[0].value));
    let anno = drawing
        .annotations
        .iter()
        .find(|a| a.name == note.id)
        .unwrap();
    let run = *anno
        .runs
        .iter()
        .find(|&&i| drawing.text[i].text.contains(&radius))
        .unwrap();
    let mut damaged = drawing.clone();
    damaged.text[run].text = damaged.text[run].text.replace(&radius, "R999");
    assert!(
        draftwright::lint::lint(&a, &damaged)
            .iter()
            .any(|i| i.code == "blend_requirement_missing")
    );
}

#[test]
fn label_overrides_survive_schedule_selection_on_their_original_items() {
    let (part, a) = analysed("scattered_holes");
    let base_spec: Spec = serde_json::from_value(json!({"hole_table":"never"})).unwrap();
    let base = model::plan_recovered(&a, &base_spec).unwrap();
    for old in base
        .dimensions
        .iter()
        .filter(|d| {
            d.placed.is_some()
                && (d.kind == "callout"
                    || d.measurements
                        .iter()
                        .any(|m| m.parameter.starts_with("location.length.")))
        })
        .take(3)
    {
        let spec: Spec = serde_json::from_value(
            json!({"hole_table":"always","dimensions":{&old.id:{"text":{"suffix":"REF"}}}}),
        )
        .unwrap();
        let plan = model::plan_recovered(&a, &spec).unwrap();
        let d = plan
            .dimensions
            .iter()
            .find(|d| d.id == old.id)
            .expect("edited label remains an item");
        assert!(d.label.contains("REF"));
        assert!(!matches!(
            d.state,
            draftwright::items::State::Covered { .. }
        ));
        let drawing = model::render(&part, &a, &plan, &font()).unwrap();
        if d.placed.is_some() {
            assert!(drawing.annotations.iter().any(|a| a.name == d.id));
        }
    }
}

#[test]
fn old_spec_id_migration_and_embedded_spec_retain_wording() {
    let (part, a) = analysed("plate");
    let old: Spec = serde_json::from_value(json!({"schema":"draftwright.spec/1", "dimensions":{"d:envelope/width.length@front":{"text":{"suffix":"REF"},"style":"reference"}}})).unwrap();
    let mut spec = model::migrate(&old, &a).unwrap();
    spec.binding = Some(a.binding.clone());
    assert!(spec.dimensions.contains_key("d:envelope/width.length"));
    let p = model::plan(&a, &spec).unwrap();
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    assert_eq!(
        model::embedded_spec(drawing.svg().as_bytes()).unwrap(),
        Some(spec.clone())
    );
    assert_eq!(
        model::embedded_spec(&drawing.png(40.0).unwrap()).unwrap(),
        Some(spec)
    );
}

#[test]
fn compact_mcp_required_fields_reject_missing_and_extra_keys() {
    for tool in ["apply_ops", "preview_ops"] {
        let schema = draftwright::mcp::schema(tool, true).unwrap();
        draftwright::mcp::validate(
            &schema,
            &json!({"error":{"code":"invalid","message":"bad label"}}),
        )
        .unwrap();
        for error in [
            json!({"code":"invalid"}),
            json!({"message":"bad label"}),
            json!({"code":"invalid","unknown":true}),
            json!({"code":"invalid","message":"bad label","unknown":true}),
        ] {
            assert!(draftwright::mcp::validate(&schema, &json!({"error":error})).is_err());
        }
    }
}

#[test]
fn saved_template_reexpands_when_the_authoritative_measurement_changes() {
    let (_, mut a) = analysed("prismatic");
    a.features.clear();
    a.recognition = None;
    let id = "d:envelope/width.length";
    let spec: Spec = serde_json::from_value(
        json!({"dimensions":{id:{"text":{"template":"{value} AFTER PLATING"}}}}),
    )
    .unwrap();
    let saved = serde_json::to_string(&spec).unwrap();
    let before = model::plan(&a, &spec).unwrap();
    let old = before.dimensions.iter().find(|d| d.id == id).unwrap();
    a.envelope[1][0] += 3.25;
    let read: Spec = serde_json::from_str(&saved).unwrap();
    let automatic = cache::default_plan(&a, false);
    let expected = automatic.dimensions.iter().find(|d| d.id == id).unwrap();
    let after = model::plan(&a, &read).unwrap();
    let d = after.dimensions.iter().find(|d| d.id == id).unwrap();
    assert!((d.value - old.value - 3.25).abs() < 1e-9);
    assert_eq!(d.value, expected.value);
    assert_eq!(d.measurements, expected.measurements);
    assert_eq!(d.label, format!("{} AFTER PLATING", expected.label));
    assert_ne!(d.label, old.label);
}

#[test]
fn large_mcp_options_keep_all_structured_and_resource_details_within_the_wire_budget() {
    let directory = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("label-text-options-wire");
    let _ = std::fs::remove_dir_all(&directory);
    let cache = draftwright::stateless::Cache::new(directory);
    let summary = draftwright::mcp::call(
        &cache,
        "inspect_part",
        &json!({
            "step":root().join("tests/fixtures/parts/mounting_plate.step"),
            "features":root().join("tools/mcp-eval/features/plate.features.json")
        }),
    )
    .unwrap();
    let mut resources = 0;
    for item in summary["key_items"].as_array().unwrap() {
        let response = draftwright::mcp::reply(&cache,&json!({"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
            "name":"item_options", "arguments":{"part_ref":summary["part_ref"],"spec_version":summary["spec_version"],"id":item["id"]}
        }})).unwrap();
        assert_eq!(response["result"]["isError"], false, "{response}");
        assert!(serde_json::to_vec(&response).unwrap().len() < 4096);
        let full = &response["result"]["structuredContent"];
        assert!(
            full["settable"]
                .as_object()
                .unwrap()
                .keys()
                .any(|p| p.ends_with("/text"))
        );
        assert!(
            full["settable"]
                .as_object()
                .unwrap()
                .keys()
                .any(|p| p.ends_with("/style"))
        );
        let text: serde_json::Value =
            serde_json::from_str(response["result"]["content"][0]["text"].as_str().unwrap())
                .unwrap();
        if let Some(reference) = text["detail_ref"].as_str() {
            let resource = draftwright::mcp::reply(&cache,&json!({"jsonrpc":"2.0","id":2,"method":"resources/read","params":{"uri":format!("draftwright:{reference}")}})).unwrap();
            let restored: serde_json::Value =
                serde_json::from_str(resource["result"]["contents"][0]["text"].as_str().unwrap())
                    .unwrap();
            assert_eq!(&restored, full);
            resources += 1;
        } else {
            assert_eq!(&text, full);
        }
    }
    assert!(
        resources > 0,
        "the complete large option map exercised resource-backed text"
    );
}

#[test]
fn inactive_saved_wording_never_becomes_live_lint_evidence_on_a_reused_id() {
    let (part, a) = analysed("plate");
    let base = cache::default_plan(&a, false);
    let old = base
        .dimensions
        .iter()
        .find(|d| d.id.starts_with("d:f:") && d.kind == "callout" && d.placed.is_some())
        .unwrap();
    let mut spec: Spec =
        serde_json::from_value(json!({"dimensions":{&old.id:{"text":{"suffix":"AFTER PLATING"}}}}))
            .unwrap();
    let mut binding = a.binding.clone();
    binding.source_sha256 = "0".repeat(64);
    spec.binding = Some(binding);
    let saved: Spec = serde_json::from_str(&serde_json::to_string(&spec).unwrap()).unwrap();
    let plan = model::plan(&a, &saved).unwrap();
    let d = plan.dimensions.iter().find(|d| d.id == old.id).unwrap();
    assert_eq!(d.label, old.label);
    assert!(!d.from_spec.iter().any(|field| field == "text"));
    assert!(
        plan.spec.dimensions[&old.id].text.is_some(),
        "quarantine preserves saved intent"
    );
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "spec_from_other_revision")
    );
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    assert!(!drawing.sheet.label_text.contains_key(&old.id));
    let original = model::render(&part, &a, &base, &font()).unwrap();
    assert_eq!(
        draftwright::lint::marks(&drawing),
        draftwright::lint::marks(&original)
    );
}

#[test]
fn style_only_counted_references_keep_live_per_unit_value_checks() {
    let (part, a) = analysed("two_grids");
    let base = cache::default_plan(&a, false);
    let old = base
        .dimensions
        .iter()
        .find(|d| d.kind == "linear" && d.label.starts_with("2× ") && d.placed.is_some())
        .unwrap();
    let spec: Spec =
        serde_json::from_value(json!({"dimensions":{&old.id:{"style":"reference"}}})).unwrap();
    let p = model::plan(&a, &spec).unwrap();
    let d = p.dimensions.iter().find(|d| d.id == old.id).unwrap();
    assert_eq!(d.label, format!("({})", old.label));
    assert_eq!(d.value, old.value);
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    assert!(
        !draftwright::lint::structural::check(&drawing)
            .iter()
            .any(|i| i.code == "label_vs_measured" && i.annotation.as_deref() == Some(&d.id))
    );
    let mut damaged = drawing.clone();
    let run = damaged
        .annotations
        .iter()
        .find(|a| a.name == d.id)
        .unwrap()
        .runs[0];
    damaged.text[run].text = "REF".into();
    assert!(
        draftwright::lint::structural::check(&damaged)
            .iter()
            .any(|i| i.code == "label_vs_measured" && i.annotation.as_deref() == Some(&d.id))
    );
}
