use draftwright::{
    dims,
    model::{self, Spec},
};
use serde_json::json;
use std::path::PathBuf;
fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}
fn analysed(name: &str) -> (draftwright::haecceity::Part, model::Analysis) {
    model::analyse_native(&root().join(format!("tests/fixtures/parts/{name}.step"))).unwrap()
}
#[test]
fn requested_precision_reaches_extents_chains_ladders_and_locations() {
    for name in ["plate", "turned", "prismatic", "two_grids", "boss_circle"] {
        let (part, a) = analysed(name);
        let base = model::plan(&a, &Spec::default()).unwrap();
        let mut spec = Spec::default();
        for o in &base.omitted {
            spec.precision.insert(o.id.clone(), 3);
        }
        for d in &base.dimensions {
            spec.precision.insert(d.id.clone(), 3);
            for m in &d.measurements {
                spec.precision
                    .insert(format!("d:{}/{}", m.feature, m.parameter), 3);
            }
        }
        let plan = model::plan(&a, &spec).unwrap();
        let font = draftwright::text::Font::open(&root().join("fonts").join(dims::FONT)).unwrap();
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        for d in &plan.dimensions {
            let original = base
                .dimensions
                .iter()
                .find(|b| b.id == d.id)
                .unwrap_or_else(|| panic!("{name}: new dimension {} {}", d.id, d.label));
            assert_eq!(d.value, original.value);
            assert_eq!(d.measurements, original.measurements);
            if d.placed.is_some() {
                let annotation = drawing.annotations.iter().find(|a| a.name == d.id).unwrap();
                // Each wrapped line has its own searchable run; all lines must match.
                assert_eq!(
                    annotation
                        .runs
                        .iter()
                        .map(|&i| drawing.text[i].text.as_str())
                        .collect::<Vec<_>>()
                        .join("\n"),
                    d.label
                );
                if d.kind == "linear" {
                    assert!(
                        d.label.contains(&dims::precision::format(d.value, 3)),
                        "{name}: {} {}",
                        d.id,
                        d.label
                    );
                }
            }
        }
        assert!(
            !draftwright::lint::structural::check(&drawing)
                .iter()
                .any(|i| i.code == "label_vs_measured"),
            "{name}"
        );
    }
}
#[test]
fn compound_terms_keep_their_own_precision_and_tolerances() {
    let (part, mut a) = analysed("plate");
    let hole = a.features.iter_mut().find(|f| f.kind == "hole").unwrap();
    let id = hole.id.clone();
    // Exercise the full semantic callout grammar independently of recognition.
    hole.parameters.insert("diameter".into(), json!(6.25));
    hole.parameters.insert("bottom".into(), json!("flat"));
    hole.parameters.insert("depth".into(), json!(4.75));
    hole.parameters
        .insert("cbore".into(), json!({"diameter":8.75,"depth":1.875}));
    hole.parameters.insert(
        "csink".into(),
        json!({"major_diameter":10.25,"included_angle":82.5}),
    );
    let mut spec = Spec::default();
    for (role, places) in [
        ("bore.diameter", 2),
        ("bore.depth", 2),
        ("counterbore.diameter", 2),
        ("counterbore.depth", 3),
        ("countersink.diameter", 2),
        ("countersink.angle", 2),
    ] {
        spec.precision.insert(format!("d:{id}/{role}"), places);
    }
    spec.dimensions.insert(
        format!("d:{id}/bore.diameter"),
        serde_json::from_value(json!({"tolerance":"±0.0005"})).unwrap(),
    );
    let plan = model::plan(&a, &spec).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == id && m.parameter == "bore.diameter")
        })
        .unwrap();
    assert!(d.label.contains("⌀6.25 ±0.0005 ↧ 4.75"), "{}", d.label);
    // A wrapped symbol stays on the line of its value (the maintainer's wrap, 2026-10-28).
    assert!(d.label.contains("\n⌴ ⌀8.75 ↧ 1.875"), "{}", d.label);
    assert!(d.label.contains("\n⌵ ⌀10.25 × 82.50°"), "{}", d.label);
    let ink = dims::label_ink(
        &d.terms,
        &draftwright::text::Font::open(&root().join("fonts").join(dims::FONT)).unwrap(),
    );
    let p = d.placed.as_ref().unwrap();
    assert!(p.label_box[2] - p.label_box[0] >= ink.max[0] - ink.min[0] - 1e-6);
    let _ = part;
}
#[test]
fn invalid_and_conflicting_precision_fail_closed_at_both_inputs() {
    for bad in [json!(-1), json!(16), json!(1.5), json!(true)] {
        assert!(
            serde_json::from_value::<Spec>(json!({"precision":{"d:envelope/width.length":bad}}))
                .is_err()
        );
        assert!(
            model::edit(
                &Spec::default(),
                &json!([{"op":"set","path":"precision/d:envelope~1width.length","value":bad}])
            )
            .is_err()
        );
    }
    let conflict = r#"{"precision":{"d:envelope/width.length":2,"d:envelope/width.length":3}}"#;
    assert!(
        serde_json::from_str::<Spec>(conflict)
            .unwrap_err()
            .to_string()
            .contains("conflicting precision")
    );
    for places in [0, 15] {
        let spec: Spec =
            serde_json::from_value(json!({"precision":{"d:envelope/width.length":places}}))
                .unwrap();
        assert_eq!(
            serde_json::from_value::<Spec>(serde_json::to_value(&spec).unwrap()).unwrap(),
            spec
        );
    }
}

#[test]
fn zero_place_precision_is_checked_against_the_drawn_span_and_never_licenses_false_ink() {
    let (part, mut a) = analysed("prismatic");
    a.envelope[1][0] = a.envelope[0][0] + 4.45;
    a.features.clear();
    a.recognition = None;
    let spec: Spec =
        serde_json::from_value(json!({"precision":{"d:envelope/width.length":0}})).unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:envelope/width.length")
        .unwrap();
    assert_eq!(d.label, "4");
    assert_eq!(d.value, a.envelope[1][0] - a.envelope[0][0]);
    let font = draftwright::text::Font::open(&root().join("fonts").join(dims::FONT)).unwrap();
    let mut drawing = model::render(&part, &a, &plan, &font).unwrap();
    assert!(
        !draftwright::lint::structural::check(&drawing)
            .iter()
            .any(
                |i| i.code == "label_vs_measured" && i.annotation.as_deref() == Some(d.id.as_str())
            )
    );
    let index = drawing
        .annotations
        .iter()
        .find(|a| a.name == d.id)
        .unwrap()
        .runs[0];
    drawing.text[index].text = "6".into();
    assert!(
        draftwright::lint::structural::check(&drawing)
            .iter()
            .any(
                |i| i.code == "label_vs_measured" && i.annotation.as_deref() == Some(d.id.as_str())
            )
    );
}

#[test]
fn close_diameters_keep_distinct_authoritative_text() {
    let (_, mut a) = analysed("turned");
    let mut spec = Spec::default();
    let steps: Vec<_> = a
        .features
        .iter_mut()
        .filter(|f| f.kind == "turned_step")
        .collect();
    assert_eq!(steps.len(), 2);
    for (step, value) in steps.into_iter().zip([6.241, 6.244]) {
        step.parameters.insert("diameter".into(), json!(value));
        spec.precision
            .insert(format!("d:{}/step.diameter", step.id), 3);
    }
    let plan = model::plan(&a, &spec).unwrap();
    for text in ["ø6.241", "ø6.244"] {
        assert!(
            plan.dimensions
                .iter()
                .any(|d| d.label == text && d.placed.is_some()),
            "{text}: {:?}",
            plan.dimensions.iter().map(|d| &d.label).collect::<Vec<_>>()
        );
    }
}

#[test]
fn coincident_measurements_require_matching_explicit_precision() {
    let (_, a) = analysed("two_grids");
    let base = model::plan(&a, &Spec::default()).unwrap();
    let alias = base
        .omitted
        .iter()
        .find(|o| {
            o.id.contains("pitch.length")
                && matches!(o.state, draftwright::items::State::Covered { .. })
        })
        .expect("coincident pitch measurements");
    let mut spec = Spec::default();
    spec.precision.insert(alias.id.clone(), 3);
    let err = model::plan(&a, &spec).unwrap_err();
    assert!(err.message.contains("conflicting precision"), "{err:?}");
}

#[test]
fn requested_precision_survives_hole_schedule_cells_and_their_ink() {
    for file in [
        "parts/scattered_holes.step",
        "recognition/issues/counterbored_block.step",
    ] {
        let (part, analysis) =
            model::analyse_native(&root().join("tests/fixtures").join(file)).unwrap();
        let seed = model::plan_escalated(
            &analysis,
            &Spec {
                hole_table: draftwright::hole_table::Setting::Never,
                ..Spec::default()
            },
        )
        .unwrap();
        let base_spec = Spec {
            hole_table: draftwright::hole_table::Setting::Always,
            ..Spec::default()
        };
        let before = model::plan_escalated(&analysis, &base_spec).unwrap();
        for places in [0, 3] {
            let mut spec = base_spec.clone();
            for d in &seed.dimensions {
                spec.precision.insert(d.id.clone(), places);
                for m in &d.measurements {
                    spec.precision
                        .insert(format!("d:{}/{}", m.feature, m.parameter), places);
                }
            }
            for d in &seed.omitted {
                spec.precision.insert(d.id.clone(), places);
            }
            let plan = model::plan_escalated(&analysis, &spec).unwrap();
            assert!(!plan.hole_tables.is_empty(), "{file}");
            let font =
                draftwright::text::Font::open(&root().join("fonts").join(dims::FONT)).unwrap();
            let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
            let mut located = 0;
            for table in &plan.hole_tables {
                for row in &table.rows {
                    let old = before
                        .hole_tables
                        .iter()
                        .flat_map(|t| &t.rows)
                        .find(|r| r.feature == row.feature)
                        .unwrap();
                    assert_eq!(row.measurements, old.measurements, "{file}");
                    let prefix = format!("{}/{}", table.id, row.key());
                    let text = drawing
                        .annotations
                        .iter()
                        .filter(|a| {
                            a.name == prefix
                                || a.name
                                    .strip_prefix(&prefix)
                                    .is_some_and(|s| s.starts_with('/'))
                        })
                        .flat_map(|a| a.runs.iter())
                        .map(|&i| drawing.text[i].text.as_str())
                        .collect::<Vec<_>>()
                        .join(" ");
                    assert!(!text.is_empty(), "{file}: row must be rendered");
                    for (role, cell) in [("location.length.x", 2), ("location.length.y", 3)] {
                        let Some(m) = row.measurements.iter().find(|m| m.parameter == role) else {
                            continue;
                        };
                        if !spec
                            .precision
                            .contains_key(&format!("d:{}/{}", m.feature, role))
                        {
                            continue;
                        }
                        let expected = dims::precision::format(m.value, places);
                        assert_eq!(row.cells[cell], expected, "{file}: {role}");
                        assert!(text.contains(&expected), "{file}: {text}");
                        located += 1;
                    }
                    if let Some(m) = row
                        .measurements
                        .iter()
                        .find(|m| m.parameter == "counterbore.depth")
                    {
                        let expected = format!("↧ {}", dims::precision::format(m.value, places));
                        assert!(row.cells[1].contains(&expected), "{file}: {:?}", row.cells);
                    }
                }
            }
            assert!(located >= 2, "{file}: native coordinate policies exercised");
            assert!(
                !draftwright::lint::lint(&analysis, &drawing)
                    .iter()
                    .any(|i| matches!(
                        i.code.as_str(),
                        "hole_requirement_missing" | "feature_not_located" | "label_vs_measured"
                    )),
                "{file}: precision may not create false coverage or fidelity findings"
            );
        }
    }
}

/// Precision formats only the extent nominal; fit classes follow the sheet's shared recipe.
/// Each display mode must reserve the full label and preserve searchable rendered text.
#[test]
fn extent_precision_composes_with_default_and_stacked_fit_labels() {
    let (part, a) = analysed("plate");
    let font = draftwright::text::Font::open(&root().join("fonts").join(dims::FONT)).unwrap();
    let ids = [
        "d:envelope/width.length",
        "d:envelope/depth.length",
        "d:envelope/height.length",
    ];
    for id in ids {
        for sheet in [
            json!({}),
            json!({"fit_deviations":true}),
            json!({"fit_deviations":false,"fit_limits":true}),
        ] {
            let stacked = sheet.get("fit_deviations").and_then(|v| v.as_bool()) == Some(true);
            let spec: Spec = serde_json::from_value(json!({
                "precision": {id:3},
                "envelope_tolerance":"±0.05",
                "dimensions": {id:{"fit":"h7"}},
                "sheet":sheet,
            }))
            .unwrap();
            let plan = model::plan(&a, &spec).unwrap();
            let d = plan.dimensions.iter().find(|d| d.id == id).unwrap();
            let nominal = format!("{} h7", dims::precision::format(d.value, 3));
            if stacked {
                assert!(
                    d.label.starts_with(&format!("{nominal} 0\n-")),
                    "{}",
                    d.label
                );
                assert!(!d.label.contains('/'), "{}", d.label);
            } else {
                assert_eq!(d.label, nominal);
            }
            assert!(!d.label.contains("±0.05"));
            for other in plan
                .dimensions
                .iter()
                .filter(|d| ids.contains(&d.id.as_str()) && d.id != id)
            {
                assert!(other.label.ends_with(" ±0.05"), "{}", other.label);
            }
            let drawing = model::render(&part, &a, &plan, &font).unwrap();
            let anno = drawing.annotations.iter().find(|a| a.name == id).unwrap();
            assert!(anno.runs.iter().any(|&i| drawing.text[i].text == d.label));
            let ink = dims::label_ink(&d.terms, &font);
            let placed = d.placed.as_ref().unwrap();
            let (width, height) = (ink.max[0] - ink.min[0], ink.max[1] - ink.min[1]);
            let (width, height) = if placed.rotation.abs() % 180.0 == 90.0 {
                (height, width)
            } else {
                (width, height)
            };
            assert!(placed.label_box[2] - placed.label_box[0] >= width - 1e-6);
            assert!(placed.label_box[3] - placed.label_box[1] >= height - 1e-6);
            assert!(
                !draftwright::lint::structural::check(&drawing)
                    .iter()
                    .any(|i| i.code == "label_vs_measured")
            );
        }
    }
}

#[test]
fn default_nominals_preserve_hundredths_and_report_the_three_place_limit() {
    for (value, expected) in [
        (4.75, "4.75"),
        (1.875, "1.875"),
        (247.65000001, "247.65"),
        (12., "12"),
    ] {
        assert_eq!(dims::fmt(value), expected);
        // Lint and PMI have independent implementations; compare their live-text policy.
        assert_eq!(draftwright::lint::coverage::fmt(value), expected);
        assert_eq!(draftwright::lint::parameters::fmt(value), expected);
        assert_eq!(draftwright::pmi::fmt(value, None), expected);
    }
    let (_, mut a) = analysed("plate");
    let hole = a.features.iter_mut().find(|f| f.kind == "hole").unwrap();
    let id = format!("d:{}/bore.diameter", hole.id);
    hole.parameters.insert("diameter".into(), json!(6.23456));
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let d = plan.dimensions.iter().find(|d| d.id == id).unwrap();
    assert!(d.label.contains("6.235"), "{}", d.label);
    let warning = plan
        .problems
        .iter()
        .find(|p| p.code == "nominal_rounded" && p.subjects.contains(&id))
        .unwrap();
    assert_eq!(warning.severity, "info");
    assert!(warning.message.contains("6.23456") && warning.message.contains("6.235"));
    let mut spec = Spec::default();
    spec.precision.insert(id.clone(), 5);
    let exact = model::plan(&a, &spec).unwrap();
    assert!(
        exact
            .dimensions
            .iter()
            .find(|d| d.id == id)
            .unwrap()
            .label
            .contains("6.23456")
    );
    assert!(
        !exact
            .problems
            .iter()
            .any(|p| p.code == "nominal_rounded" && p.subjects.contains(&id))
    );
}

#[test]
fn rescue_hole_table_reports_its_printed_three_place_nominal_limit() {
    let (part, mut a) = analysed("scattered_holes");
    let mut changed = 0;
    for f in &mut a.features {
        if f.id == "f:hole:10" {
            f.parameters.insert("diameter".into(), json!(5.000003));
            changed += 1;
        }
    }
    assert!(changed > 0);
    let spec = Spec {
        hole_table: draftwright::hole_table::Setting::Always,
        ..Default::default()
    };
    let mut plan = model::plan_recovered(&a, &spec).unwrap();
    let table = plan
        .hole_tables
        .pop()
        .expect("the scattered holes produce a real placed schedule");
    assert!(!table.blocks.is_empty());
    assert!(
        table
            .rows
            .iter()
            .flat_map(|r| &r.cells)
            .any(|c| c == "⌀5.000 THRU")
    );
    let id = table.id.clone();
    // The rescue table has the same real cells/blocks, through the legacy carrier.
    plan.hole_table = Some(table);
    draftwright::completeness::report(&a, &mut plan);
    let problem = plan
        .problems
        .iter()
        .find(|p| p.code == "nominal_rounded" && p.subjects.contains(&id))
        .unwrap();
    assert_eq!(problem.severity, "info");
    assert!(problem.message.contains("5.000003") && problem.message.contains("prints 5.000 at"));
    let font = draftwright::text::Font::open(&root().join("fonts").join(dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    assert!(drawing.text.iter().any(|t| t.text.contains("5.000")));
}
