//! Regression cases from the review of the single-sheet drawings.
#[path = "common/corpus.rs"]
mod corpus;

use draftwright::model::{self, Plan, Spec};

/// The saved sm-hanger specs predate quiddity-rust v0.2.2 (#120), which makes the R7 blend
/// `f:blend:10` a fillet, counted with the others on one `14× R7` callout, `d:f:fillet:f30` (the
/// `16× R7` callout was `d:f:fillet:f14`). In v0.2.3 (#127), concave fillets move this
/// same convex presenter to `d:f:fillet:f53`. Their two radius entries become that one.
fn hanger_renumbered(mut spec: Spec) -> Spec {
    let fillet = spec.dimensions.remove("d:f:fillet:f14/fillet.radius");
    let blend = spec.dimensions.remove("d:f:blend:10/blend.radius");
    if let Some(entry) = fillet.or(blend) {
        spec.dimensions
            .insert("d:f:fillet:f53/fillet.radius".into(), entry);
    }
    spec
}

fn plan(file: &str, spec: &Spec) -> Option<Plan> {
    let dir = corpus::dir()?;
    let (_, analysis) = model::analyse_native(&dir.join(file)).unwrap();
    Some(model::plan_recovered(&analysis, spec).unwrap())
}

#[test]
fn adjacent_labels_have_readable_separation() {
    let spec: Spec =
        serde_json::from_str(include_str!("fixtures/layout/review-adjacent.spec.json")).unwrap();
    let Some(plan) = plan("mfcadpp/10127.step", &spec) else {
        return;
    };
    // Follow the same two spans with their faithful nominal labels; keep the gap assertion.
    let labels: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.view == "v:side" && ["23.890", "32.268"].contains(&d.label.as_str()))
        .collect();
    assert_eq!(labels.len(), 2);
    let a = labels[0].placed.as_ref().unwrap().label_box;
    let b = labels[1].placed.as_ref().unwrap().label_box;
    let gap = (a[0] - b[2])
        .max(b[0] - a[2])
        .max(a[1] - b[3])
        .max(b[1] - a[3]);
    assert!(
        gap >= 1.0 - 1e-6,
        "labels run together: {a:?}, {b:?}, gap {gap}"
    );
}

#[test]
fn hanger_keeps_its_hole_callout_or_reports_it_by_name() {
    let Some(dir) = corpus::dir() else { return };
    let (_, analysis) = model::analyse_native(&dir.join("ttt_inputs/sm-hanger.step")).unwrap();
    let spec = hanger_renumbered(
        serde_json::from_str(include_str!("fixtures/layout/review-hanger.spec.json")).unwrap(),
    );
    let plan = model::plan_recovered(&analysis, &spec).unwrap();
    let hole = plan
        .omitted
        .iter()
        .find(|d| d.label == "2× ⌀10 THRU")
        .expect("the authored set keeps its omitted callout in the ledger");
    assert_eq!(hole.state, draftwright::items::State::NotAuthored);
    let problem = plan
        .problems
        .iter()
        .find(|p| p.subjects == [hole.id.clone()] && p.code == "dimension_not_authored")
        .expect("a located hole must still report its missing size callout");
    assert!(problem.message.contains(&hole.label), "{}", problem.message);
    assert!(problem.message.contains("authored"));
    let restored = model::edit(&spec, &serde_json::to_value(&problem.fix).unwrap()).unwrap();
    let after = model::plan_recovered(&analysis, &restored).unwrap();
    assert!(
        after
            .dimensions
            .iter()
            .any(|d| d.id == hole.id && d.label == hole.label && d.placed.is_some())
    );
    assert!(
        !after
            .problems
            .iter()
            .any(|p| p.code == "dimension_not_authored" && p.subjects.contains(&hole.id))
    );
}

#[test]
fn edited_dimensions_do_not_add_extension_crossings_through_the_part() {
    let Some(dir) = corpus::dir() else { return };
    let (part, analysis) =
        model::analyse_native(&dir.join("nist/nist_ftc_06_asme1_rd.stp")).unwrap();
    let base = model::plan(&analysis, &Spec::default()).unwrap();
    let mut spec = Spec::default();
    for (i, d) in base
        .dimensions
        .iter()
        .enumerate()
        .filter(|(_, d)| d.kind == "linear")
    {
        let tolerance = match d.label.as_str() {
            "25.4" => "±0.007".to_string(),
            "3× 72.39" => "±0.006".to_string(),
            _ => format!("±{:.3}", (i + 1) as f64 / 1000.0),
        };
        spec.dimensions.entry(d.id.clone()).or_default().tolerance = Some(tolerance);
    }
    let edited = model::plan(&analysis, &spec).unwrap();
    // Inspect visible rendered geometry, independently of the planner's feature boxes.
    let font = draftwright::text::Font::open(
        &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(draftwright::dims::FONT),
    )
    .unwrap();
    let before = model::render(&part, &analysis, &base, &font).unwrap();
    let after = model::render(&part, &analysis, &edited, &font).unwrap();
    let crosses = |[a, b]: [[f64; 2]; 2], [c, d]: [[f64; 2]; 2]| {
        let side = |p: [f64; 2], q: [f64; 2], r: [f64; 2]| {
            (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        };
        side(a, b, c) * side(a, b, d) < -1e-9 && side(c, d, a) * side(c, d, b) < -1e-9
    };
    let crossings = |drawing: &draftwright::output::Drawing,
                     d: &draftwright::dims::PlannedDimension| {
        let view = drawing
            .sheet
            .views
            .iter()
            .find(|v| v.name == d.view)
            .unwrap();
        let item = drawing.sheet.items.iter().find(|i| i.name == d.id).unwrap();
        item.lines
            .iter()
            .take(2)
            .filter(|line| {
                view.edges.iter().any(|edge| match edge {
                    draftwright::output::Curve::Line(a, b) => crosses(**line, [*a, *b]),
                    _ => false,
                })
            })
            .count()
    };
    let targets: Vec<_> = base
        .dimensions
        .iter()
        .filter(|d| ["25.4", "3× 72.39"].contains(&d.label.as_str()))
        .collect();
    assert_eq!(targets.len(), 2);
    for d in targets {
        let moved = edited.dimensions.iter().find(|q| q.id == d.id).unwrap();
        assert!(
            crossings(&after, moved) <= crossings(&before, d),
            "{} gained extension lines crossing visible body edges",
            moved.label
        );
        assert!(!edited.problems.iter().any(|p| p.code == "extension_through_feature"
            && p.subjects.first() == Some(&d.id)), "{} crosses features: {:?}", moved.label, edited.problems);
    }
}
