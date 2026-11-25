use std::path::Path;

use draftwright::{
    bridge::{Feature, holes},
    lint,
    model::{self, Analysis, Plan, Spec},
    text::Font,
};
use serde_json::json;

fn fixture_path(name: &str) -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/blind_recess_depths")
        .join(format!("{name}.step.gz"))
}

fn fixture(name: &str) -> (draftwright::haecceity::Part, Analysis) {
    model::analyse_native(&fixture_path(name)).unwrap()
}

fn font() -> Font {
    Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(draftwright::dims::FONT),
    )
    .unwrap()
}

fn callout(plan: &Plan) -> &draftwright::dims::PlannedDimension {
    plan.dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .unwrap()
}

fn words(label: &str) -> String {
    label.split_whitespace().collect::<Vec<_>>().join(" ")
}

#[test]
fn blind_callouts_sum_the_recognised_entry_depths() {
    for (name, cylinder, label) in [
        ("plain", 20., "⌀6 ↧ 20"),
        ("chamfer", 19.2, "⌀6 ↧ 20 C0.8"),
        ("counterbore", 15., "⌀6 ↧ 20 ⌴ ⌀12 ↧ 5"),
        ("countersink", 17., "⌀6 ↧ 20 ⌵ ⌀12 × 90°"),
        ("stepped", 12., "⌀6 ↧ 20 ⌴ ⌀12 ↧ 5 ⌴ ⌀18 ↧ 3"),
        ("spotface", 15., "⌀6 ↧ 20 ⌴ ⌀12 ↧ 4.25 ⌴SF ⌀18 ↧ 0.75"),
        ("chamfered_counterbore", 15., "⌀6 ↧ 20 ⌴ ⌀12 ↧ 5 C0.5"),
        ("edge_chamfer_cb", 15., "⌀6 ↧ 20 ⌴ ⌀12 ↧ 5"),
    ] {
        let (_, analysis) = fixture(name);
        let records = holes::read(&analysis);
        assert_eq!(records.len(), 1, "{name}");
        assert_eq!(
            records[0].1.as_ref().unwrap().depth,
            Some(cylinder),
            "{name}"
        );
        let plan = model::plan_recovered(&analysis, &Spec::default()).unwrap();
        assert_eq!(words(&callout(&plan).label), label, "{name}");
        assert!(
            callout(&plan)
                .measurements
                .iter()
                .any(|m| { m.parameter == "bore.depth" && m.value == 20. })
        );
    }
}

#[test]
fn a_countersink_counts_only_at_the_recorded_opening() {
    let (_, mut analysis) = fixture("countersink");
    let feature = analysis
        .features
        .iter_mut()
        .find(|f| f.kind == "hole")
        .unwrap();
    let mut combined = feature.clone();
    // Quiddity describes a sink on its own, but does not attach one to a larger
    // counterbore wall. Exercise the combination at the record boundary.
    combined.parameters["depth"] = json!(12.0);
    combined
        .parameters
        .insert("cbore".into(), json!({"diameter": 9., "depth": 5.}));
    let frame = holes::frame(&analysis);
    let depth = |f: &Feature| holes::Hole::read(f, &frame).unwrap().depth_from_mouth();
    assert_eq!(depth(&combined), Some(20.));
    let location = combined.parameters["csink"]["location"].clone();
    combined.parameters["csink"]["axis"] = json!([0., 0., 1.]);
    combined.parameters["csink"]["location"][2] = json!(-6.);
    assert_eq!(depth(&combined), Some(17.));
    combined.parameters["csink"]["axis"] = json!([0., 0., -1.]);
    combined.parameters["csink"]["location"][2] = json!(-12.);
    assert_eq!(depth(&combined), Some(17.));
    combined.parameters["csink"]["location"] = location;
    combined.parameters["csink"]
        .as_object_mut()
        .unwrap()
        .remove("depth");
    combined.parameters["cbore"]
        .as_object_mut()
        .unwrap()
        .remove("depth");
    assert_eq!(depth(&combined), Some(12.));
}

#[test]
fn a_recognised_eighty_two_degree_sink_uses_bore_depth_precision() {
    let (_, analysis) = fixture("countersink82");
    let records = holes::read(&analysis);
    assert_eq!(records.len(), 6);
    for (record, hole) in records {
        let hole = hole.unwrap();
        assert_eq!(hole.depth, Some(9.58));
        assert_eq!(hole.countersink.unwrap().angle, Some(82.));
        assert_eq!(record.parameters["csink"]["depth"], json!(2.4158));
    }
    let plan = model::plan_recovered(&analysis, &Spec::default()).unwrap();
    let label = words(
        &plan
            .dimensions
            .iter()
            .find(|d| d.label.contains("⌀4.1 ↧"))
            .unwrap()
            .label,
    );
    assert!(label.contains("⌀4.1 ↧ 12 ⌵ ⌀8.3 × 82°"), "{label}");
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "nominal_rounded" && p.message.contains("11.99"))
    );
}

#[test]
fn countersunk_blind_table_rows_use_the_same_opening_offset() {
    let spec = Spec {
        hole_table: draftwright::hole_table::Setting::Always,
        ..Spec::default()
    };
    let mut failures = Vec::new();
    for (name, size, old_entry) in [
        ("countersink", "⌀6 ↧ 20 ⌵ ⌀12 × 90°", "27 / −Z"),
        ("chamfer", "⌀6 ↧ 20 C0.8", "29.2 / −Z"),
    ] {
        let (part, analysis) = fixture(name);
        let plan = model::plan_recovered(&analysis, &spec).unwrap();
        let row = &plan.hole_tables[0].rows[0];
        assert_eq!(row.cells[1], size);
        assert_eq!(row.cells[4], "30 / −Z");
        let review = draftwright::api::review_from(
            &fixture_path(name),
            draftwright::api::Features::Native,
            &spec,
        )
        .unwrap();
        let invalid = |i: &lint::LintIssue| i.code == "hole_table_entry_invalid";
        if review.lint.iter().any(invalid) {
            failures.push((name, "correct entry rejected"));
        }
        let mut drawing = model::render(&part, &analysis, &plan, &font()).unwrap();
        let entry = drawing
            .annotations
            .iter()
            .find(|a| a.name.ends_with("/entry"))
            .unwrap();
        for &run in &entry.runs {
            drawing.text[run].text = old_entry.into();
        }
        if !lint::lint(&analysis, &drawing).iter().any(invalid) {
            failures.push((name, "cylinder entry accepted"));
        }
    }
    assert!(failures.is_empty(), "{failures:?}");
}

#[test]
fn lint_rejects_the_old_bore_length_and_a_recess_cannot_define_a_zero_bore() {
    let (part, mut analysis) = fixture("counterbore");
    let font = font();
    let plan = model::plan_recovered(&analysis, &Spec::default()).unwrap();
    let mut drawing = model::render(&part, &analysis, &plan, &font).unwrap();
    let issues = |a: &Analysis, d: &draftwright::output::Drawing| {
        lint::lint(a, d)
            .into_iter()
            .filter(|i| i.requirements.iter().any(|r| r.parameter == "bore.depth"))
            .collect::<Vec<_>>()
    };
    assert!(issues(&analysis, &drawing).is_empty());
    let annotation = drawing
        .annotations
        .iter()
        .find(|a| a.name == callout(&plan).id)
        .unwrap();
    let old_depth = annotation
        .runs
        .iter()
        .copied()
        .find(|&r| drawing.text[r].text.contains("20"))
        .unwrap();
    drawing.text[old_depth].text = drawing.text[old_depth].text.replacen("20", "15", 1);
    assert_eq!(
        issues(&analysis, &drawing)[0].code,
        "hole_requirement_unverifiable"
    );

    let hole = analysis
        .features
        .iter_mut()
        .find(|f| f.kind == "hole")
        .unwrap();
    hole.parameters["depth"] = json!(0.0);
    let plan = model::plan_recovered(&analysis, &Spec::default()).unwrap();
    let d = callout(&plan);
    assert_eq!(words(&d.label), "⌀6 DEPTH UNDEFINED ⌴ ⌀12 ↧ 5");
    assert!(d.measurements.iter().all(|m| m.parameter != "bore.depth"));
}

#[test]
fn a_zero_length_bore_refuses_spec_threads() {
    let (_, mut analysis) = fixture("counterbore");
    let hole = analysis
        .features
        .iter_mut()
        .find(|f| f.kind == "hole")
        .unwrap();
    hole.parameters["depth"] = json!(0.0);
    let id = &hole.id.clone();
    let mut labels = Vec::new();
    for length in [Some(3), None] {
        let mut thread = json!({"designation":"M7x1-6H"});
        if let Some(length) = length {
            thread["length"] = json!(length);
        }
        let spec = serde_json::from_value(json!({"features": {id: {"thread":thread}}})).unwrap();
        let plan = model::plan_recovered(&analysis, &spec).unwrap();
        labels.push(words(&callout(&plan).label));
    }
    assert_eq!(labels, ["⌀6 DEPTH UNDEFINED ⌴ ⌀12 ↧ 5"; 2]);
}

#[test]
fn an_unknown_bottom_finding_states_the_callout_depth() {
    let (_, mut analysis) = fixture("counterbore");
    let hole = analysis
        .features
        .iter_mut()
        .find(|f| f.kind == "hole")
        .unwrap();
    hole.parameters["bottom"] = json!("unknown");
    let plan = model::plan_recovered(&analysis, &Spec::default()).unwrap();
    assert!(words(&callout(&plan).label).contains("↧ 20"));
    let warning = plan
        .problems
        .iter()
        .find(|p| p.code == "hole_bottom_unknown")
        .unwrap();
    assert!(
        warning.message.contains("depth (20)"),
        "{}",
        warning.message
    );
}

#[test]
fn a_spec_thread_preserves_the_bore_length() {
    let (part, analysis) = fixture("counterbore");
    let id = &holes::read(&analysis)[0].0.id;
    for length in [12, 19, 21] {
        let spec: Spec = serde_json::from_value(json!({
            "features": { id: {"thread": {"designation": "M7x1-6H", "length": length}} }
        }))
        .unwrap();
        let plan = model::plan_recovered(&analysis, &spec).unwrap();
        if length == 12 {
            let label = words(&callout(&plan).label);
            assert!(
                label.contains("M7x1-6H") && label.contains("↧ 15"),
                "{label}"
            );
            assert!(
                callout(&plan)
                    .measurements
                    .iter()
                    .any(|m| { m.parameter == "bore.depth" && m.value == 15. })
            );
            let effective = draftwright::spec_gdt::effective(&analysis, &spec).unwrap();
            let drawing = model::render(&part, &analysis, &plan, &font()).unwrap();
            let findings = lint::lint(&effective, &drawing);
            assert!(findings.is_empty(), "{findings:?}");
        } else {
            assert!(plan.problems.iter().any(|p| {
                p.message.contains(&format!(
                    "thread length {length} exceeds its hole's depth 15"
                ))
            }));
        }
    }
}
