//! Fillet and chamfer callouts (`dims::edges`), as scenarios: a part and its recognition in, a
//! property of the plan, its problems or the drawing's lint out. Each test names the catalogue
//! rules it checks (`docs/rules/`).

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::path::{Path, PathBuf};

use draftwright::dims::PlannedDimension;
use draftwright::lint;
use draftwright::model::{self, DimensionSpec, Plan, Problem, Spec};
use serde_json::{Value, json};

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

fn recognition(name: &str) -> Value {
    serde_json::from_str(
        &std::fs::read_to_string(fixture(&format!("recognition/{name}.json"))).unwrap(),
    )
    .unwrap()
}

/// The prismatic block's recognition (four R2 fillets along quiddity's x, the part's z), with each
/// record (by its index) rewritten by *edit*.
fn prismatic_with(edit: impl Fn(usize, &mut Value)) -> Value {
    let mut json = recognition("prismatic");
    for (i, f) in json["quiddity"]["features"]
        .as_array_mut()
        .unwrap()
        .iter_mut()
        .enumerate()
    {
        edit(i, f);
    }
    json
}

/// The record of a chamfer along quiddity's x at the fillet's point *f*.
fn chamfer(f: &mut Value, leg1: f64, leg2: f64, angle: f64) {
    let at = f["record"]["at"].clone();
    f["family"] = "chamfers".into();
    f["record_type"] = "Chamfer".into();
    f["record"] = json!({
        "angle": angle, "at": at, "axis": "x", "corner": null, "dependents": [],
        "leg1": leg1, "leg2": leg2, "named_dimensions": {}, "turned": false
    });
}

fn planned(name: &str, json: &Value, spec: &Spec) -> Plan {
    let (part, mut a) = model::analyse(&fixture(&format!("parts/{name}.step"))).unwrap();
    model::recognise(&part, &mut a, &json.to_string()).unwrap();
    model::plan(&a, spec).unwrap()
}

fn callouts(plan: &Plan) -> Vec<&PlannedDimension> {
    plan.dimensions
        .iter()
        .filter(|d| d.measurements.iter().any(|m| m.parameter.contains('.')))
        .filter(|d| {
            d.measurements
                .iter()
                .all(|m| m.parameter.starts_with("fillet.") || m.parameter.starts_with("chamfer."))
        })
        .collect()
}

fn labels(plan: &Plan) -> Vec<(String, String)> {
    let mut l: Vec<(String, String)> = callouts(plan)
        .iter()
        .map(|d| (d.label.clone(), d.view.clone()))
        .collect();
    l.sort();
    l
}

fn problems<'a>(plan: &'a Plan, code: &str) -> Vec<&'a Problem> {
    plan.problems.iter().filter(|p| p.code == code).collect()
}

/// ISSUE-1374-b and ARCH0015-1 (the fillet's `n× R` grammar): equal fillets share one callout,
/// in the view that sees their rounded edges as arcs, which keeps every fillet's measurement; it
/// is a default, drawn, and no fillet is reported unplanned.
#[test]
fn equal_fillets_share_one_counted_callout() {
    let plan = planned("prismatic", &recognition("prismatic"), &Spec::default());
    let c = callouts(&plan);
    assert_eq!(c.len(), 1, "{:?}", labels(&plan));
    let c = c[0];
    assert_eq!(c.id, "d:f:fillet:1/fillet.radius");
    assert_eq!((c.kind.as_str(), c.label.as_str()), ("callout", "4× R2"));
    assert_eq!(c.terms, ["4×", "R2"]);
    assert_eq!(c.view, "v:plan", "the fillets run along the part's z");
    assert!(c.default && c.placed.is_some());
    let mut features: Vec<&str> = c.measurements.iter().map(|m| m.feature.as_str()).collect();
    features.sort();
    assert_eq!(
        features,
        ["f:fillet:1", "f:fillet:2", "f:fillet:3", "f:fillet:4"]
    );
    assert!(
        c.measurements
            .iter()
            .all(|m| m.parameter == "fillet.radius" && m.value == 2.0)
    );
    assert!(c.basis.contains("seen as an arc"), "{}", c.basis);
    assert!(problems(&plan, "dimensions_not_ported").is_empty());
    assert!(problems(&plan, "feature_not_dimensioned").is_empty());
}

/// ISSUE-1281-a: turned (toroidal) fillets read in the shaft's profile, one callout with every
/// measurement; the same records not marked turned are not guessed to be turned, and read
/// end-on.
#[test]
fn a_turned_fillet_reads_in_the_profile_and_is_never_guessed_turned() {
    let json = recognition("turned");
    let plan = planned("turned", &json, &Spec::default());
    let c = callouts(&plan);
    assert_eq!(c.len(), 1, "{:?}", labels(&plan));
    assert_eq!(c[0].id, "d:f:fillet:f1/fillet.radius");
    assert_eq!(c[0].label, "4× R0.8");
    assert_eq!(c[0].measurements.len(), 4);
    assert!(c[0].basis.contains("profile"), "{}", c[0].basis);
    assert!(c[0].placed.is_some());

    let mut parallel = json.clone();
    for f in parallel["quiddity"]["features"].as_array_mut().unwrap() {
        if f["family"] == "fillets" {
            f["record"]["turned"] = false.into();
        }
    }
    let plan = planned("turned", &parallel, &Spec::default());
    assert_eq!(
        labels(&plan),
        [("4× R0.8".to_string(), "v:plan".to_string())],
        "the turning axis is the part's z, which the plan looks along"
    );
}

/// ARCH0015-1 (the chamfer's grammar) and ISSUE-1254-a: `C<leg>` for equal legs at 45°, otherwise
/// the first leg and the angle; equal chamfers share one counted callout, but chamfers that only
/// share their first leg do not.
#[test]
fn chamfers_are_called_out_c_or_leg_and_angle() {
    let json = prismatic_with(|i, f| match i {
        0 | 1 => chamfer(f, 1.0, 1.0, 45.0),
        2 => chamfer(f, 1.0, 2.0, 26.57),
        _ => chamfer(f, 4.47, 4.0, 42.1),
    });
    let plan = planned("prismatic", &json, &Spec::default());
    assert_eq!(
        labels(&plan),
        [
            ("1 × 26.57°".to_string(), "v:plan".to_string()),
            ("2× C1".into(), "v:plan".into()),
            ("4.47 × 42.1°".into(), "v:plan".into()),
        ]
    );
    for c in callouts(&plan) {
        let lengths = c
            .measurements
            .iter()
            .filter(|m| m.parameter == "chamfer.length")
            .count();
        let angles = c
            .measurements
            .iter()
            .filter(|m| m.parameter == "chamfer.angle")
            .count();
        assert_eq!(lengths, c.label.strip_prefix("2× ").map_or(1, |_| 2));
        // The angle is a measurement only when the label prints it.
        assert_eq!(angles, usize::from(c.label.contains('°')), "{}", c.label);
        assert!(c.id.ends_with("/chamfer.length"), "{}", c.id);
        assert_eq!(c.view, "v:plan", "{}", c.id);
        assert!(c.placed.is_some());
    }
    assert!(problems(&plan, "dimensions_not_ported").is_empty());
}

/// An oblique record with non-perpendicular run and support legs is refused visibly.
#[test]
fn an_oriented_chamfer_with_inconsistent_directions_is_reported() {
    let json = prismatic_with(|i, f| {
        chamfer(f, 1.0, 1.0, 45.0);
        if i < 2 {
            let at = f["record"]["at"].clone();
            f["family"] = "oriented_chamfers".into();
            f["record_type"] = "OrientedChamfer".into();
            f["record"] = json!({
                "angle": 45.0, "at": at, "corner": at, "dependents": [], "leg1": 1.0,
                "leg2": 1.0, "leg1_direction": [0.0, 1.0, 0.0],
                "leg2_direction": [0.0, 0.0, 1.0], "length": 10.0, "named_dimensions": {},
                "run": [0.0, 0.5, 0.866025]
            });
        }
    });
    let plan = planned("prismatic", &json, &Spec::default());
    let c = callouts(&plan);
    assert_eq!(c.len(), 1, "{:?}", labels(&plan));
    assert_eq!(c[0].label, "2× C1");
    assert!(
        c[0].measurements
            .iter()
            .all(|m| !m.feature.starts_with("f:oriented_chamfer:"))
    );
    assert_eq!(problems(&plan, "feature_not_dimensioned").len(), 2);
    assert!(problems(&plan, "dimensions_not_ported").is_empty());
}

/// draftwright's grouping: a callout counts every equal member, and its leader points at those of
/// its most populous axis and view; the basis says how many are counted from elsewhere.
#[test]
fn a_callout_counts_members_along_other_axes() {
    let json = prismatic_with(|i, f| {
        if i == 3 {
            f["record"]["axis"] = "y".into();
        }
    });
    let plan = planned("prismatic", &json, &Spec::default());
    let c = callouts(&plan);
    assert_eq!(c.len(), 1, "{:?}", labels(&plan));
    assert_eq!(
        (c[0].label.as_str(), c[0].view.as_str()),
        ("4× R2", "v:plan")
    );
    assert_eq!(c[0].measurements.len(), 4);
    assert!(c[0].basis.contains("1 of them"), "{}", c[0].basis);
}

/// quiddity-rust#120: a fillet between oblique tangent walls carries a unit-vector axis. Its radius
/// is a true size: counted on the callout of equal principal fillets, or, with none, called out
/// where its edge is seen most nearly end-on. Never refused, so a radius note is not withdrawn
/// for an undrawn exception.
#[test]
fn an_oblique_fillet_is_counted_and_called_out() {
    let s = std::f64::consts::FRAC_1_SQRT_2;
    let json = prismatic_with(|i, f| {
        if i == 3 {
            f["record"]["axis"] = json!([s, s, 0.0]);
        }
    });
    let plan = planned("prismatic", &json, &Spec::default());
    let c = callouts(&plan);
    assert_eq!(c.len(), 1, "{:?}", labels(&plan));
    assert_eq!(c[0].label, "4× R2");
    assert_eq!(c[0].measurements.len(), 4);
    assert!(c[0].basis.contains("oblique fillet"), "{}", c[0].basis);
    assert!(problems(&plan, "feature_not_dimensioned").is_empty());

    let json = prismatic_with(|_, f| f["record"]["axis"] = json!([s, s, 0.0]));
    let plan = planned("prismatic", &json, &Spec::default());
    let c = callouts(&plan);
    assert_eq!(c.len(), 1, "{:?}", labels(&plan));
    assert_eq!(c[0].label, "4× R2");
    assert!(c[0].placed.is_some(), "{}", c[0].id);
    assert!(problems(&plan, "feature_not_dimensioned").is_empty());
}

/// Honest failure: a fillet whose record gives no radius is reported, and the others are still
/// called out.
#[test]
fn an_unreadable_fillet_is_reported() {
    let json = prismatic_with(|i, f| {
        if i == 0 {
            f["record"].as_object_mut().unwrap().remove("radius");
        }
    });
    let plan = planned("prismatic", &json, &Spec::default());
    assert_eq!(labels(&plan), [("3× R2".to_string(), "v:plan".to_string())]);
    let reported = problems(&plan, "feature_not_dimensioned");
    assert_eq!(reported.len(), 1);
    assert!(
        reported[0].message.ends_with("its record gives no radius."),
        "{}",
        reported[0].message
    );
}

/// R1 invariant 3: omitting the only view that sees the fillets as arcs keeps their callout in
/// the plan, undrawn, and says so.
#[test]
fn a_callout_whose_view_is_omitted_is_kept_and_reported() {
    let mut spec = Spec::default();
    spec.views.insert("plan".into(), Default::default());
    spec.views.get_mut("plan").unwrap().omit = true;
    let plan = planned("prismatic", &recognition("prismatic"), &spec);
    let c = callouts(&plan);
    assert_eq!(c.len(), 1);
    assert!(c[0].placed.is_none());
    assert!(
        problems(&plan, "view_plan_incomplete")
            .iter()
            .any(|p| p.subjects.contains(&c[0].id))
    );
}

/// LINT-fillet_coverage-radius on the port's own drawing: the drawn callout states every fillet's
/// radius, so lint finds none missing; omitting it in the spec leaves all four missing.
#[test]
fn lint_finds_the_fillets_stated() {
    let json = recognition("prismatic");
    let (part, mut a) = model::analyse(&fixture("parts/prismatic.step")).unwrap();
    model::recognise(&part, &mut a, &json.to_string()).unwrap();
    let font = draftwright::text::Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let missing = |spec: &Spec| -> usize {
        let plan = model::plan(&a, spec).unwrap();
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        lint::lint(&a, &drawing)
            .iter()
            .filter(|i| i.code.starts_with("fillet_requirement_"))
            .count()
    };
    assert_eq!(missing(&Spec::default()), 0);
    let mut spec = Spec::default();
    spec.dimensions.insert(
        "d:f:fillet:1/fillet.radius".into(),
        DimensionSpec {
            omit: true,
            ..Default::default()
        },
    );
    assert_eq!(missing(&spec), 4);
}

/// ISSUE-1254-a's scenario: GRM-03's three turned chamfers are two callouts in the profile view
/// (`2× C0.3` and `C0.5`) covering all three measurements, as draftwright draws them.
#[test]
fn grm03_states_three_chamfers_in_two_callouts() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let f = facts::load("grm03");
    let (_, a) = corpus::analyse(&dir, &f.binding.file, "grm03");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        labels(&plan),
        [
            ("2× C0.3".to_string(), "v:front".to_string()),
            ("C0.5".into(), "v:front".into()),
        ]
    );
    let n: usize = callouts(&plan).iter().map(|c| c.measurements.len()).sum();
    assert_eq!(n, 3);
}

/// ISSUE-1276-a's turned site in a fallback view: with GRM-03's front omitted, its chamfer
/// callouts move to the other profile view (plan, for its x-axis shaft), and their leaders still
/// end on the shaft's profile, as far off its centreline as in the front, not on the centreline.
#[test]
fn turned_callouts_in_the_fallback_profile_end_on_the_profile() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let f = facts::load("grm03");
    let (_, a) = corpus::analyse(&dir, &f.binding.file, "grm03");
    // Each callout's label, view and its leader tip's offset across the view's centre, paper mm.
    let offsets = |spec: &Spec| -> Vec<(String, String, f64)> {
        let plan = model::plan(&a, spec).unwrap();
        callouts(&plan)
            .iter()
            .map(|c| {
                let v = plan.views.iter().find(|v| v.id == c.view).unwrap();
                let tip = c.placed.as_ref().unwrap().arrows[0][0];
                (c.label.clone(), c.view.clone(), tip[1] - v.at[1])
            })
            .collect()
    };
    let front = offsets(&Spec::default());
    let mut spec = Spec::default();
    spec.views.insert("front".into(), Default::default());
    spec.views.get_mut("front").unwrap().omit = true;
    let fallback = offsets(&spec);
    assert_eq!(front.len(), 2);
    assert_eq!(fallback.len(), 2);
    for (f, b) in front.iter().zip(&fallback) {
        assert_eq!((f.0.as_str(), f.1.as_str()), (b.0.as_str(), "v:front"));
        assert_eq!(b.1, "v:plan");
        assert!(b.2.abs() > 1.0, "{fallback:?}");
        assert!(
            (f.2.abs() - b.2.abs()).abs() < 1e-6,
            "{front:?} {fallback:?}"
        );
    }
}

/// For quiddity-rust#134: the record includes the end treatment. State the complete
/// height once, retaining the chamfer/round's own size and the supporting plate.
#[test]
fn treated_boss_heights_reach_the_free_end_once_and_keep_the_edge_callout() {
    for (file, height, parameter, label) in [
        ("grm02.step", 37.6, "chamfer.length", "C1.5"),
        ("boss-end-round.step.gz", 20., "fillet.radius", "R1.5"),
    ] {
        let (part, a) = model::analyse_native(&fixture(&format!("parts/{file}"))).unwrap();
        let boss = a.features.iter().find(|f| f.kind == "boss").unwrap();
        assert_eq!(boss.parameters["height"], height);
        let plan = model::plan(&a, &Spec::default()).unwrap();
        let omitted = plan
            .omitted
            .iter()
            .find(|d| d.id.ends_with("/boss_height.length"))
            .unwrap();
        assert_eq!(omitted.label, height.to_string());
        let draftwright::items::State::Covered { by } = &omitted.state else {
            panic!("{file}: {omitted:#?}");
        };
        let owner = plan.dimensions.iter().find(|d| &d.id == by).unwrap();
        assert!(owner.placed.is_some());
        if file == "grm02.step" {
            assert_eq!(owner.id, "d:envelope/width.length");
            assert_eq!(owner.value, height);
        } else {
            // The default states the height by 24 overall minus the 4 mm plate.
            assert_eq!(owner.value, 4.);
            assert!(omitted.basis.contains("d:envelope/height.length"));
            assert!(
                plan.dimensions
                    .iter()
                    .any(|d| d.id == "d:envelope/height.length"
                        && d.value == 24.
                        && d.placed.is_some())
            );
        }
        assert!(!plan.dimensions.iter().any(|d| d.id == omitted.id));
        // An explicit tolerance keeps the boss's own full-height dimension.
        let spec: Spec = serde_json::from_value(json!({
            "dimensions": {&omitted.id: {"tolerance": "±0.1"}}
        }))
        .unwrap();
        let explicit = model::plan(&a, &spec).unwrap();
        let heights: Vec<_> = explicit
            .dimensions
            .iter()
            .filter(|d| {
                d.measurements
                    .iter()
                    .any(|m| m.feature == boss.id && m.parameter == "boss_height.length")
            })
            .collect();
        assert_eq!(heights.len(), 1);
        assert_eq!(heights[0].value, height);
        assert_eq!(heights[0].label, format!("{height} ±0.1"));
        assert!(heights[0].placed.is_some());
        let edge = plan.dimensions.iter().find(|d| d.label == label).unwrap();
        assert!(edge.placed.is_some());
        assert!(edge.measurements.iter().any(|m| m.parameter == parameter));
        let plate = a.features.iter().find(|f| f.kind == "plate").unwrap();
        let plate_id = format!("d:{}/thickness.length", plate.id);
        if file == "grm02.step" {
            assert!(
                plan.dimensions
                    .iter()
                    .any(|d| d.id == plate_id && d.value == 4. && d.placed.is_some())
            );
        } else {
            let thickness = plan.omitted.iter().find(|d| d.id == plate_id).unwrap();
            assert_eq!(thickness.label, "4");
            assert_eq!(thickness.state, omitted.state);
        }
        let font = draftwright::text::Font::new(
            include_bytes!("../fonts/IBMPlexMono-Regular.ttf").to_vec(),
        )
        .unwrap();
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        assert!(lint::lint(&a, &drawing).iter().all(|i| !matches!(
            i.code.as_str(),
            "boss_height_missing" | "chamfer_requirement_missing" | "fillet_requirement_missing"
        )));
    }
}

/// For quiddity-rust#134: a turned inside corner is concave, and remains counted
/// with the three equal convex end rounds in the searchable radius callout.
#[test]
fn concave_turned_fillets_keep_their_count_and_measurement() {
    let (part, a) = model::analyse_native(&fixture("parts/turned.step")).unwrap();
    let concave = a
        .features
        .iter()
        .find(|f| f.kind == "fillet" && f.parameters["side"] == "concave")
        .unwrap();
    assert_eq!(concave.parameters["turned"], true);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let owners: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.measurements.iter().any(|m| m.feature == concave.id))
        .collect();
    assert_eq!(owners.len(), 1);
    assert_eq!(owners[0].label, "4× R0.8");
    assert_eq!(owners[0].measurements.len(), 4);
    assert!(owners[0].placed.is_some());
    let font =
        draftwright::text::Font::new(include_bytes!("../fonts/IBMPlexMono-Regular.ttf").to_vec())
            .unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    assert!(
        lint::lint(&a, &drawing)
            .iter()
            .all(|i| !i.code.starts_with("fillet_requirement_"))
    );
}

/// For quiddity-rust#134: changing a turned fillet's side preserves the physical
/// presenter, count and route. A valid inside-round leader must not force larger paper.
#[test]
fn turning_side_correction_keeps_existing_radius_presenters() {
    let Some(dir) = corpus::dir() else { return };
    let (_, a) = model::analyse_native(&dir.join("cadgenbench_inputs/cgb207.step")).unwrap();
    let mut old = a.clone();
    let mut corrected = 0;
    for f in &mut old.features {
        if f.kind == "fillet" && f.parameters["turned"] == true && f.parameters["side"] == "concave"
        {
            f.parameters.insert("side".into(), json!("convex"));
            corrected += 1;
        }
    }
    assert!(corrected > 0);
    let before = model::plan(&old, &Spec::default()).unwrap();
    let after = model::plan(&a, &Spec::default()).unwrap();
    let presenters = |p: &Plan| {
        p.dimensions
            .iter()
            .filter(|d| {
                d.measurements
                    .iter()
                    .any(|m| m.parameter == "fillet.radius")
            })
            .map(|d| {
                (
                    d.id.clone(),
                    d.view.clone(),
                    d.label.clone(),
                    serde_json::to_value(&d.placed).unwrap(),
                )
            })
            .collect::<Vec<_>>()
    };
    assert_eq!(presenters(&before), presenters(&after));
}
