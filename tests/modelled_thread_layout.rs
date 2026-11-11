//! Default native thread drawings retain their designation and clear printed text.
#[path = "common/corpus.rs"]
mod corpus;

use std::io::Read;

use draftwright::{api, model, text::Font};

#[test]
fn modelled_thread_callouts_clear_labels_and_dimension_ink() {
    let Some(corpus) = corpus::dir() else {
        return;
    };
    let font = Font::new(api::MONO.to_vec()).unwrap();
    for (file, expected) in [
        (
            "cadgenbench_inputs/cgb243.step.gz",
            vec!["M14×1.5", "M62×1.5", "M62×1.5", "M75×1.5"],
        ),
        (
            "cadgenbench/threaded_connector_109.step",
            vec!["⌀110 × P6.35"],
        ),
    ] {
        let path = corpus.join(file);
        let mut bytes = std::fs::read(&path).unwrap();
        if file.ends_with(".gz") {
            let mut plain = Vec::new();
            flate2::read::GzDecoder::new(bytes.as_slice())
                .read_to_end(&mut plain)
                .unwrap();
            bytes = plain;
        }
        let (part, analysis) = model::analyse_native_step(&path, &bytes).unwrap();
        let plan = model::plan_recovered(&analysis, &model::Spec::default()).unwrap();
        let labels: Vec<_> = plan
            .dimensions
            .iter()
            .filter(|d| d.measurements.iter().any(|m| m.parameter == "thread"))
            .map(|d| d.label.as_str())
            .collect();
        assert_eq!(labels, expected, "{file}");
        let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
        // cgb243's M14×1.5 and two M62×1.5 are shorter than the holes they are cut in, but a
        // modelled thread's span does not prove a depth: lint asks it for none (the modelled
        // half of LINT-hole_coverage-thread_and_profile is retired).
        let depths: Vec<_> = draftwright::lint::lint(&analysis, &drawing)
            .into_iter()
            .filter(|i| i.requirements.iter().any(|r| r.parameter == "thread.depth"))
            .map(|i| i.subjects.join(","))
            .collect();
        assert!(depths.is_empty(), "{file}: {depths:?}");
        for code in ["annotation_overlap", "annotation_ink_overlap"] {
            let problems: Vec<_> = plan
                .problems
                .iter()
                .chain(&drawing.problems)
                .filter(|p| p.code == code)
                .collect();
            assert!(problems.is_empty(), "{file}: {code}: {problems:?}");
        }
        // The fallback remains measured rather than being guessed as metric.
        assert_eq!(
            plan.problems
                .iter()
                .filter(|p| p.code == "thread_designation_measured")
                .count(),
            usize::from(file.contains("threaded_connector"))
        );
        assert!(
            plan.dimensions
                .iter()
                .filter(|d| d.measurements.iter().any(|m| m.parameter == "thread"))
                .all(|d| {
                    !d.label.contains("LONG")
                        && !d.label.contains('\n')
                        && d.measurements
                            .iter()
                            .any(|m| m.parameter == "thread.length")
                })
        );
    }
}

/// A thread on cgb243's lone ø90 boss prints its designation in place of the diameter, and
/// lint counts that callout as stating the boss's diameter (it claims `boss.diameter`), so
/// there is neither a `pmi_not_lowered` refusal nor a false `feature_not_dimensioned`.
#[test]
fn a_thread_on_a_lone_boss_states_its_diameter() {
    let Some(corpus) = corpus::dir() else {
        return;
    };
    let path = corpus.join("cadgenbench_inputs/cgb243.step.gz");
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::read(&path).unwrap().as_slice())
        .read_to_end(&mut bytes)
        .unwrap();
    let (_, analysis) = model::analyse_native_step(&path, &bytes).unwrap();
    let spec: model::Spec = serde_json::from_value(serde_json::json!({
        "features": {"f:boss:f129": {"thread": {"designation": "M90x2"}}}
    }))
    .unwrap();
    let plan = model::plan_recovered(&analysis, &spec).unwrap();
    let boss = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:boss:f129/boss.diameter")
        .expect("the boss's callout is planned");
    assert_eq!(boss.label, "M90 x 2");
    assert!(
        plan.problems.iter().all(|p| p.code != "pmi_not_lowered"),
        "{:?}",
        plan.problems
    );
    let marks: Vec<_> = plan
        .dimensions
        .iter()
        .map(|d| draftwright::lint::Mark {
            units: Default::default(),
            name: d.id.clone(),
            view: Some(d.view.clone()),
            text: d.label.replace('\n', " "),
            claims: d
                .measurements
                .iter()
                .map(|m| draftwright::output::Claim {
                    feature: m.feature.clone(),
                    parameter: m.parameter.clone(),
                })
                .collect(),
        })
        .collect();
    let unstated: Vec<_> = draftwright::lint::coverage::feature_coverage(&analysis, &marks)
        .into_iter()
        .filter(|i| i.subjects.iter().any(|s| s == "f:boss:f129"))
        .collect();
    assert!(unstated.is_empty(), "{unstated:?}");
}

/// dw-examples' T8 flange nut (`t8-flange-nut.step.gz`): quiddity refuses its modelled 4-start
/// Tr8 bore because a chamfer splits the flanks. draftwright measures it from the refused faces,
/// states the measured thread, and draws it conventionally instead of its raw helix.
#[test]
fn a_refused_thread_with_chamfer_split_flanks_is_measured_and_depicted() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/t8-flange-nut.step.gz");
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::read(&path).unwrap().as_slice())
        .read_to_end(&mut bytes)
        .unwrap();
    let (part, analysis) =
        model::analyse_native_step(std::path::Path::new("t8-flange-nut.step"), &bytes).unwrap();
    let threads: Vec<_> = analysis
        .features
        .iter()
        .filter(|f| f.kind == "modelled_thread")
        .collect();
    assert_eq!(threads.len(), 1);
    let t = threads[0];
    let number = |name: &str| t.parameters[name].as_f64().unwrap();
    assert_eq!(t.parameters["side"], "internal");
    assert_eq!(t.parameters["hand"], "right");
    assert_eq!(t.parameters["starts"], 4);
    assert_eq!(t.parameters["refused"], "inconsistent_diameters");
    assert!((number("pitch") - 2.).abs() < 1e-3, "{}", number("pitch"));
    assert!((number("major_diameter") - 8.3).abs() < 1e-3);
    assert!((number("minor_diameter") - 6.5).abs() < 1e-3);
    // The completed refusal is gone, and no hole is read on the thread's flanks.
    let r = analysis.recognition.as_ref().unwrap();
    assert!(
        r.refusals
            .iter()
            .all(|r| r.subtype != "ModelledThreadRefusal")
    );
    assert!(
        analysis
            .features
            .iter()
            .all(|f| f.kind != "hole" || !f.faces.iter().any(|i| t.faces.contains(i)))
    );

    let spec = model::Spec::default();
    let plan = model::plan_recovered(&analysis, &spec).unwrap();
    let labels: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.measurements.iter().any(|m| m.parameter == "thread"))
        .map(|d| d.label.as_str())
        .collect();
    assert_eq!(labels, ["⌀8.3 × P2 4 STARTS"]);
    let codes: Vec<_> = plan.problems.iter().map(|p| p.code.as_str()).collect();
    assert!(!codes.contains(&"modelled_thread_refused"), "{codes:?}");
    assert!(codes.contains(&"thread_designation_measured"), "{codes:?}");

    // The raw helix projects as curved Bézier pieces; ISO 6410 ink and the nut's other edges are
    // lines and conics (a straight Bézier piece is a dash). Turning the depiction off restores
    // the raw helix.
    let font = Font::new(api::MONO.to_vec()).unwrap();
    let curved = |spec: &model::Spec| {
        let plan = model::plan_recovered(&analysis, spec).unwrap();
        let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
        // Part and hidden ink inside the orthographic views (the iso view draws its own edges).
        let boxes: Vec<_> = drawing
            .sheet
            .views
            .iter()
            .filter(|v| v.name != "v:iso")
            .map(|v| v.bbox)
            .collect();
        let bent = |poles: &[[f64; 2]]| {
            let (a, b) = (poles[0], poles[poles.len() - 1]);
            let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
            let chord = dx.hypot(dy).max(1e-9);
            poles[1..poles.len() - 1]
                .iter()
                .any(|p| ((p[0] - a[0]) * dy - (p[1] - a[1]) * dx).abs() / chord > 0.01)
        };
        drawing
            .layers
            .iter()
            .filter(|l| l.name != "dims")
            .flat_map(|l| &l.items)
            .filter_map(|i| match i {
                draftwright::output::Item::Edge(draftwright::output::Curve::Beziers(b)) => {
                    Some(b.clone())
                }
                draftwright::output::Item::Edge(draftwright::output::Curve::Rational(r)) => Some(
                    r.iter()
                        .map(|piece| piece.iter().map(|p| [p[0] / p[2], p[1] / p[2]]).collect())
                        .collect(),
                ),
                _ => None,
            })
            .filter(|pieces| {
                let p = pieces[0][0];
                pieces.iter().any(|poles| bent(poles))
                    && boxes
                        .iter()
                        .any(|b| (b[0]..=b[2]).contains(&p[0]) && (b[1]..=b[3]).contains(&p[1]))
            })
            .count()
    };
    let off: model::Spec = serde_json::from_value(serde_json::json!({
        "features": {(t.id.as_str()): {"modelled_thread": {"depiction": false}}}
    }))
    .unwrap();
    let (on, raw) = (curved(&spec), curved(&off));
    assert_eq!(
        on, 0,
        "raw helix ink remains ({raw} with the depiction off)"
    );
    assert!(raw > 0);
}
