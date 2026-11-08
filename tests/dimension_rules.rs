//! The drafting rules the dimension planner ports, as scenarios: a part and its recognition in,
//! a property of the plan (or its problems, or the drawing) out. Each test names the catalogue
//! rules it checks (`docs/rules/`).

use std::path::{Path, PathBuf};

use draftwright::dims::{self, PlannedDimension};
use draftwright::model::{self, Analysis, DimensionSource, DimensionSpec, Plan, Spec};
use draftwright::text::Font;
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

/// The part *name* with *json* as its recognition.
fn analysed_with(name: &str, json: &Value) -> (haecceity::Part, Analysis) {
    let (part, mut a) = model::analyse(&fixture(&format!("parts/{name}.step"))).unwrap();
    let (r, features) =
        draftwright::bridge::read(&part, &a.binding.source_sha256, &json.to_string()).unwrap();
    a.recognition = Some(r);
    a.features = features;
    (part, a)
}

fn planned(name: &str, json: &Value, spec: &Spec) -> Plan {
    model::plan(&analysed_with(name, json).1, spec).unwrap()
}

fn find<'a>(plan: &'a Plan, id: &str) -> &'a PlannedDimension {
    plan.dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap_or_else(|| {
            panic!(
                "no {id}; the plan has {:?}",
                plan.dimensions.iter().map(|d| &d.id).collect::<Vec<_>>()
            )
        })
}

fn labels(plan: &Plan, kind: &str) -> Vec<String> {
    let mut l: Vec<String> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == kind)
        .map(|d| d.label.clone())
        .collect();
    l.sort();
    l
}

/// Faithful nominals intentionally depart from Python's one-decimal `_fmt`.
#[test]
fn numbers_print_as_draftwright_prints_them() {
    for (v, s) in [
        (12.9898, "12.990"),
        (5.0, "5"),
        (0.25, "0.25"),
        (4.45, "4.45"),
        (72.39, "72.39"),
        (-0.0, "0"),
        (-0.04, "-0.04"),
        (276.25, "276.25"),
        (247.649_999_999_9, "247.65"),
        (5.000_000_1, "5"),
    ] {
        assert_eq!(dims::fmt(v), s, "{v}");
    }
}

/// ADR1-6: a hole's view follows from its axis by one rule.
#[test]
fn a_holes_view_follows_from_its_axis() {
    assert_eq!(
        [dims::end_on(0), dims::end_on(1), dims::end_on(2)],
        ["side", "front", "plan"]
    );
    let plan = planned("plate", &recognition("plate"), &Spec::default());
    for d in plan
        .dimensions
        .iter()
        .filter(|d| !d.id.starts_with("d:envelope"))
    {
        assert_eq!(d.view, "v:plan", "{}: the plate's holes run along z", d.id);
    }
}

/// ARCH0008-2 (identical features are counted), ISSUE-1143-c (a grid has both pitches and is
/// located), ADR4-7 (THRU is the feature's fact; a through hole states no depth),
/// LINT-envelope_coverage-overall_extents and ARCH0016-5 (each dimension in exactly one view).
#[test]
fn a_grid_of_holes_is_called_out_once_located_and_spaced() {
    let plan = planned("plate", &recognition("plate"), &Spec::default());
    let c = find(&plan, "d:f:hole_pattern:f6/bore.diameter");
    assert_eq!(c.label, "4× ⌀6 THRU");
    assert_eq!(c.measurements.len(), 4, "one bore diameter per hole");
    assert!(
        c.measurements
            .iter()
            .all(|m| m.parameter == "bore.diameter")
    );
    assert_eq!(
        find(&plan, "d:f:hole_pattern:f6/grid_pitch.length.col").label,
        "40"
    );
    assert_eq!(
        find(&plan, "d:f:hole_pattern:f6/grid_pitch.length.row").label,
        "20"
    );
    // The grid's centre is the part's centre: 30 and 20 from the left and front faces.
    assert_eq!(
        find(&plan, "d:f:hole_pattern:f6/location.length.x").label,
        "30"
    );
    assert_eq!(
        find(&plan, "d:f:hole_pattern:f6/location.length.y").label,
        "20"
    );
    for (id, label) in [
        ("d:envelope/width.length", "60"),
        ("d:envelope/depth.length", "40"),
        ("d:envelope/height.length", "5"),
    ] {
        assert_eq!(find(&plan, id).label, label);
    }
    let mut ids: Vec<&str> = plan.dimensions.iter().map(|d| d.id.as_str()).collect();
    ids.sort();
    ids.dedup();
    assert_eq!(ids.len(), plan.dimensions.len(), "IDs are unique");
    for d in &plan.dimensions {
        assert!(!d.id.contains('@'), "an ID never names its view: {}", d.id);
        assert!(d.default && !d.basis.is_empty(), "{}", d.id);
        assert!(d.placed.is_some(), "{}", d.id);
    }
}

/// ADR4-7, ISSUE-1229-a and ISSUE-1517-a: a blind hole states its depth and never reads THRU;
/// holes group only when every machining term matches, so a through hole is not counted with
/// blind ones.
#[test]
fn blind_and_through_holes_are_separate_requirements() {
    let mut json = recognition("plate");
    for f in json["quiddity"]["features"].as_array_mut().unwrap() {
        f["record"]["bottom"] = "flat".into();
    }
    let plan = planned("plate", &json, &Spec::default());
    assert_eq!(labels(&plan, "callout"), ["4× ⌀6 ↧ 5"]);
    let c = &plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout")
        .unwrap();
    assert_eq!(
        c.measurements
            .iter()
            .filter(|m| m.parameter == "bore.depth")
            .count(),
        4
    );

    // An unknown bottom is not a through hole either (string_post's cross hole is one).
    for f in json["quiddity"]["features"].as_array_mut().unwrap() {
        f["record"]["bottom"] = "unknown".into();
    }
    let plan = planned("plate", &json, &Spec::default());
    assert_eq!(labels(&plan, "callout"), ["4× ⌀6 ↧ 5"]);

    json["quiddity"]["features"][0]["record"]["bottom"] = "through".into();
    let plan = planned("plate", &json, &Spec::default());
    assert_eq!(labels(&plan, "callout"), ["3× ⌀6 ↧ 5", "⌀6 THRU"]);
    // Dimensioned singly, every hole is located in both directions (LINT-hole_coverage-location).
    let located: Vec<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.contains("/location."))
        .flat_map(|d| d.measurements.iter().map(|m| m.feature.as_str()))
        .collect();
    for h in 1..=4 {
        assert_eq!(
            located
                .iter()
                .filter(|f| **f == ["f:hole:f6", "f:hole:f7", "f:hole:f8", "f:hole:f9"][h - 1])
                .count(),
            2,
            "f:hole:{h}"
        );
    }
}

/// ISSUE-1596-a: a bolt circle of fewer than five holes is no evidence of a circle; its holes are
/// still drawn, counted and located. The plate's four holes, a 40 × 20 rectangle, are squared to
/// 20 × 20 about the same centre wherever quiddity records them: since #223 the bridge refuses an
/// uneven circle before this rule is reached (as the plate's own rectangle is, below).
#[test]
fn a_four_hole_bolt_circle_is_not_trusted() {
    fn square(v: &mut Value) {
        match v {
            Value::Object(m) => {
                for (k, x) in m.iter_mut() {
                    let z = x.get(2).and_then(Value::as_f64);
                    if k == "location" && z.is_some_and(|z| z.abs() == 20.0) {
                        x[2] = json!(z.unwrap() / 2.0);
                    } else {
                        square(x);
                    }
                }
            }
            Value::Array(a) => a.iter_mut().for_each(square),
            _ => {}
        }
    }
    let mut json = recognition("plate");
    let p = &mut json["quiddity"]["derived"]["hole_patterns"][0];
    let o = p.as_object_mut().unwrap();
    o.remove("width");
    o.remove("height");
    o.insert("diameter".into(), json!(44.721));
    let (_, uneven) = analysed_with("plate", &json);
    assert!(uneven.recognition.as_ref().unwrap().patterns.is_empty());
    json["quiddity"]["derived"]["hole_patterns"][0]["diameter"] = json!(28.284);
    square(&mut json);
    let (_, a) = analysed_with("plate", &json);
    assert_eq!(
        a.recognition.as_ref().unwrap().patterns[0].arrangement,
        "bolt_circle"
    );
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(labels(&plan, "callout"), ["4× ⌀6 THRU"]);
    assert!(!plan.dimensions.iter().any(|d| d.label.contains("BC")));
    let c = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout")
        .unwrap();
    assert!(c.basis.contains("fewer than five"), "{}", c.basis);
    assert_eq!(
        plan.dimensions
            .iter()
            .filter(|d| d.id.contains("/location."))
            .count(),
        4,
        "two x and two y positions"
    );
}

/// R1 invariant 3 (ADR4-9, ADR5-7, ADR2-12): an approved item that cannot be drawn stays in the
/// plan and is reported against its ID, naming the view that would carry it.
#[test]
fn nothing_approved_vanishes_when_its_view_is_omitted() {
    let mut spec = Spec::default();
    spec.views.insert("plan".into(), Default::default());
    spec.views.get_mut("plan").unwrap().omit = true;
    let plan = planned("plate", &recognition("plate"), &spec);
    let unplaced: Vec<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_none())
        .map(|d| d.id.as_str())
        .collect();
    assert!(unplaced.contains(&"d:f:hole_pattern:f6/bore.diameter"));
    for id in &unplaced {
        assert!(
            plan.problems
                .iter()
                .any(|p| p.code == "view_plan_incomplete" && p.subjects.iter().any(|s| s == id)),
            "{id} is reported"
        );
    }
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "view_plan_incomplete")
        .unwrap();
    assert_eq!(p.severity, "error");
    assert!(p.message.contains("plan view"), "{}", p.message);
}

/// ADR4-5: both sources of dimension intent. `auto` draws the defaults less what the spec omits;
/// `authored` draws exactly what it lists; each omission says it was the author's (ADR4-8); a
/// dimension the part does not have is refused with the nearest IDs (R1 invariant 9).
#[test]
fn both_dimension_sources_and_their_validation() {
    let json = recognition("plate");
    let callout = "d:f:hole_pattern:f6/bore.diameter";
    let width = "d:envelope/width.length";

    // auto, through an edit: the ID's `/` is written `~1`.
    let spec = model::edit(
        &Spec::default(),
        &json!([{"op": "set", "path": format!("dimensions/{}/omit", callout.replace('/', "~1")), "value": true}]),
    )
    .unwrap();
    assert!(spec.dimensions[callout].omit);
    let plan = planned("plate", &json, &spec);
    assert!(!plan.dimensions.iter().any(|d| d.id == callout));
    assert_eq!(plan.omitted.len(), 1);
    assert!(plan.omitted[0].basis.starts_with("authored"));

    // authored: exactly the listed ones.
    let mut spec = Spec {
        dimension_source: DimensionSource::Authored,
        ..Spec::default()
    };
    spec.dimensions
        .insert(width.into(), DimensionSpec::default());
    let plan = planned("plate", &json, &spec);
    assert_eq!(plan.dimensions.len(), 1);
    assert_eq!(plan.dimensions[0].id, width);
    assert!(!plan.dimensions[0].default);
    assert!(plan.omitted.iter().any(|o| o.id == callout));
    let round: Spec = serde_json::from_value(serde_json::to_value(&spec).unwrap()).unwrap();
    assert_eq!(round, spec);
    assert!(
        serde_json::to_string(&spec)
            .unwrap()
            .contains("\"dimension_source\":\"authored\"")
    );

    // An explicit omission inside an authored set is honoured.
    spec.dimensions.get_mut(width).unwrap().omit = true;
    let (_, a) = analysed_with("plate", &json);
    let plan = model::plan(&a, &spec).unwrap();
    assert!(plan.dimensions.iter().all(|d| d.id != width));
    assert!(plan.omitted.iter().any(|d| d.id == width));
    assert!(!plan.views.is_empty());

    // A saved version-1 ID is retained without guessing which item it edits.
    let mut spec = Spec::default();
    spec.dimensions.insert(
        "d:envelope/width.length@plan".into(),
        DimensionSpec::default(),
    );
    let plan = model::plan(&a, &spec).unwrap();
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "dimension_edit_unapplied")
    );
    assert_eq!(plan.spec.dimensions, spec.dimensions);
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.id == width && d.placed.is_some())
    );

    // A source that does not exist is refused by the schema, naming the two.
    let e = serde_json::from_value::<Spec>(json!({"dimension_source": "both"})).unwrap_err();
    assert!(
        e.to_string().contains("auto") && e.to_string().contains("authored"),
        "{e}"
    );
}

/// A part turned whole states its diameter (with ø) and its length, not a width and depth that
/// are both its diameter. Its connected profile uses one shoulder ordinate (40) and the
/// overall (70) from the largest flat end, with no redundant reference link.
#[test]
fn a_turned_part_states_its_diameter_and_length() {
    let plan = planned("turned", &recognition("turned"), &Spec::default());
    let env: Vec<(&str, &str)> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:envelope") || d.id.ends_with("/step.length"))
        .map(|d| (d.id.as_str(), d.label.as_str()))
        .collect();
    assert_eq!(
        env,
        [
            ("d:envelope/od.diameter", "ø30"),
            ("d:envelope/height.length", "70"),
            ("d:f:turned_step:f0/step.length", "40")
        ]
    );
    assert!(plan.dimensions.iter().all(|d| !d.reference));
    let shoulder = find(&plan, "d:f:turned_step:f0/step.length");
    assert_eq!(shoulder.measurements[0].value, 40.0);
}

/// When the preferred view is omitted, a turned part's diameter moves to the other profile view
/// and is drawn across the radial axis that view shows; the plan never says it is drawable and
/// undrawable at once (R1 invariant 3).
#[test]
fn a_turned_parts_diameter_follows_its_view() {
    let spec: Spec = serde_json::from_value(json!({"views": {"front": {"omit": true}}})).unwrap();
    let plan = planned("turned", &recognition("turned"), &spec);
    for id in ["d:envelope/od.diameter", "d:f:turned_step:f0/step.length"] {
        let d = find(&plan, id);
        assert!(d.placed.is_some(), "{id} is drawn");
        assert_eq!(d.view, "v:side");
        assert!(d.basis.contains("the front view is omitted"), "{}", d.basis);
    }
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "view_plan_incomplete"),
        "{:?}",
        plan.problems
    );
    // With both profile views omitted, the message names both and why.
    let spec: Spec =
        serde_json::from_value(json!({"views": {"front": {"omit": true}, "side": {"omit": true}}}))
            .unwrap();
    let plan = planned("turned", &recognition("turned"), &spec);
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "view_plan_incomplete")
        .expect("reported");
    assert!(
        p.subjects.iter().any(|s| s == "d:envelope/od.diameter"),
        "{:?}",
        p.subjects
    );
    assert!(
        p.message.contains("front or side view") && p.message.contains("omitted"),
        "{}",
        p.message
    );
}

/// A view left out as a turned part's repeated profile has no spec omission to remove.
#[test]
fn a_dimensions_chosen_view_names_why_it_is_not_shown() {
    let (_, a) = analysed_with("turned", &recognition("turned"));
    let id = "d:f:turned_step:f0/step.length";
    for omitted in [false, true] {
        let mut spec: Spec =
            serde_json::from_value(json!({ "dimensions": { id: { "view": "side" } } })).unwrap();
        if omitted {
            spec.views.insert(
                "side".into(),
                model::ViewSpec {
                    sheet: None,
                    omit: true,
                    ..Default::default()
                },
            );
        }
        let plan = model::plan(&a, &spec).unwrap();
        let p = plan
            .problems
            .iter()
            .find(|p| p.code == "dimension_view_not_shown" && p.subjects == [id])
            .expect("the chosen side view is not shown");
        assert!(find(&plan, id).placed.is_some());
        if omitted {
            assert!(
                p.message.contains("remove views/side/omit"),
                "{}",
                p.message
            );
            assert_eq!(p.fix, [draftwright::items::Op::remove("views/side/omit")]);
            let fixed = model::edit(&spec, &serde_json::to_value(&p.fix).unwrap()).unwrap();
            let after = model::plan(&a, &fixed).unwrap();
            assert!(after.problems.iter().all(|q| q.id != p.id));
            assert_eq!(find(&after, id).view, "v:side");
        } else {
            assert!(
                p.message.contains("turned part's repeated profile"),
                "{}",
                p.message
            );
            assert!(
                !p.message.contains("remove views/side/omit"),
                "{}",
                p.message
            );
            assert!(p.fix.is_empty());
            let kept = model::edit(
                &spec,
                &json!([{ "op": "set", "path": "views/side", "value": {} }]),
            )
            .unwrap();
            let after = model::plan(&a, &kept).unwrap();
            assert!(after.problems.iter().all(|q| q.id != p.id));
            assert_eq!(find(&after, id).view, "v:side");
        }
    }
}

/// An edge treatment that needs the dropped view stays undrawn, with the actual reason.
#[test]
fn an_incomplete_view_plan_names_why_the_view_is_not_shown() {
    let mut json = recognition("turned");
    for f in json["quiddity"]["features"].as_array_mut().unwrap() {
        if f["family"] == "fillets" {
            f["record"]["turned"] = json!(false);
            f["record"]["axis"] = json!("y");
        }
    }
    let (_, a) = analysed_with("turned", &json);
    for omitted in [false, true] {
        let spec: Spec = if omitted {
            serde_json::from_value(json!({ "views": { "side": { "omit": true } } })).unwrap()
        } else {
            Spec::default()
        };
        let plan = model::plan(&a, &spec).unwrap();
        let p = plan
            .problems
            .iter()
            .find(|p| p.code == "view_plan_incomplete" && p.id.ends_with("v:side"))
            .expect("the fillets need the side view");
        assert!(!p.subjects.is_empty());
        for id in &p.subjects {
            assert!(find(&plan, id).placed.is_none());
        }
        if omitted {
            assert!(
                p.message.contains("remove views/side/omit"),
                "{}",
                p.message
            );
            assert_eq!(p.fix, [draftwright::items::Op::remove("views/side/omit")]);
            let fixed = model::edit(&spec, &serde_json::to_value(&p.fix).unwrap()).unwrap();
            let after = model::plan(&a, &fixed).unwrap();
            assert!(after.problems.iter().all(|q| q.id != p.id));
            for id in &p.subjects {
                assert!(find(&after, id).placed.is_some());
            }
        } else {
            assert!(
                p.message.contains("turned part's repeated profile"),
                "{}",
                p.message
            );
            assert!(
                !p.message.contains("remove views/side/omit"),
                "{}",
                p.message
            );
            assert!(p.fix.is_empty());
            let kept = model::edit(
                &spec,
                &json!([{ "op": "set", "path": "views/side", "value": {} }]),
            )
            .unwrap();
            let after = model::plan(&a, &kept).unwrap();
            assert!(after.problems.iter().all(|q| q.id != p.id));
            for id in &p.subjects {
                assert!(find(&after, id).placed.is_some());
            }
        }
    }
}

/// Review M8, and ARCH0017-1 in part: a recognised feature of a kind no planner dimensions yet is
/// reported once, as info, in the Analysis's feature order; a record type draftwright itself
/// leaves undimensioned (here a freeform surface) is no gap in the port and is not reported, and
/// the holes and fillets the port plans are not reported either.
#[test]
fn features_with_no_ported_dimensions_are_reported() {
    let not_ported = |plan: &Plan| -> Vec<String> {
        plan.problems
            .iter()
            .filter(|p| p.code == "dimensions_not_ported")
            .map(|p| {
                assert_eq!(p.severity, "warning");
                assert_eq!(p.id, format!("p:dimensions_not_ported:{}", p.subjects[0]));
                p.subjects.concat()
            })
            .collect()
    };
    let (_, mut a) = analysed_with("turned", &recognition("turned"));
    let kinds: Vec<&str> = a.features.iter().map(|f| f.kind.as_str()).collect();
    assert!(kinds.contains(&"fillet") && kinds.contains(&"turned_step"));
    assert!(!kinds.contains(&"hole"));
    // The fillets and turned steps are planned (`dims::edges`, `dims::turned`): with nothing
    // else, nothing is reported.
    assert!(not_ported(&model::plan(&a, &Spec::default()).unwrap()).is_empty());
    // An unknown feature kind is reported even as the set of ported planners grows.
    let ids: Vec<String> = a
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .map(|f| f.id.clone())
        .collect();
    assert_eq!(ids.len(), 2);
    for f in a.features.iter_mut().filter(|f| f.kind == "turned_step") {
        f.kind = "unported_test_feature".into();
    }
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(not_ported(&plan), ids);
    let step = plan
        .problems
        .iter()
        .find(|p| p.subjects[0].starts_with("f:turned_step:"))
        .unwrap();
    assert!(
        step.message
            .ends_with("no dimensions for unported test feature are ported yet."),
        "{}",
        step.message
    );

    let first = a.features.iter().position(|f| f.id == ids[0]).unwrap();
    a.features[first].fingerprint.subtype = "FreeformSurface".into();
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(not_ported(&plan), ids[1..]);

    let plate = planned("plate", &recognition("plate"), &Spec::default());
    assert!(not_ported(&plate).is_empty(), "{:?}", plate.problems);
}

/// An ID never names the view that carries its dimension (`draftwright.spec/2`), so omitting that
/// view moves the dimension and keeps its ID; a version 2 spec keyed by a version 1 ID (with its
/// `@<view>`) warns without silently applying the stale edit to the current dimension.
#[test]
fn an_omitted_view_keeps_a_dimensions_id_and_an_old_key_reads_as_the_new_one() {
    let (_, a) = analysed_with("plate", &recognition("plate"));
    let shown = model::plan(&a, &Spec::default()).unwrap();
    let width = find(&shown, "d:envelope/width.length");
    assert_eq!(width.view, "v:front");
    let spec: Spec = serde_json::from_value(json!({ "views": {"front": {"omit": true}} })).unwrap();
    let moved = model::plan(&a, &spec).unwrap();
    let width = find(&moved, "d:envelope/width.length");
    assert_eq!(width.view, "v:plan");
    assert!(
        width.basis.contains("front view is omitted"),
        "{}",
        width.basis
    );

    let spec: Spec = serde_json::from_value(json!({
        "views": {"front": {"omit": true}},
        "dimensions": {"d:envelope/width.length@front": {"omit": true}}
    }))
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "dimension_edit_unapplied")
    );
    assert_eq!(plan.spec.dimensions, spec.dimensions);
    let width = find(&plan, "d:envelope/width.length");
    assert_eq!(width.view, "v:plan");
    assert!(width.placed.is_some());
}

/// A countersunk hole's callout states its countersink as quiddity records it (`major_diameter`,
/// `included_angle`: `countersinks.py`), in draftwright's grammar `⌵ ⌀D × A°`, and claims both
/// sizes for every hole.
#[test]
fn a_countersunk_hole_states_its_countersink() {
    let mut json = recognition("plate");
    for f in json["quiddity"]["features"].as_array_mut().unwrap() {
        let r = &mut f["record"];
        r["csink"] = json!({
            "axis": r["axis"].clone(), "location": r["location"].clone(),
            "major_diameter": 12.0, "drill_diameter": 6.0, "included_angle": 90.0, "depth": 3.0,
        });
    }
    let plan = planned("plate", &json, &Spec::default());
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "feature_not_dimensioned"),
        "{:?}",
        plan.problems
    );
    let c = find(&plan, "d:f:hole_pattern:f6/bore.diameter");
    assert_eq!(c.label, "4× ⌀6 THRU ⌵ ⌀12 × 90°");
    for (parameter, value) in [("countersink.diameter", 12.0), ("countersink.angle", 90.0)] {
        let m: Vec<f64> = c
            .measurements
            .iter()
            .filter(|m| m.parameter == parameter)
            .map(|m| m.value)
            .collect();
        assert_eq!(m, [value; 4], "{parameter}");
    }
}

/// A hole with both a counterbore and a spotface (quiddity finds both when a shallow facing cut
/// and a deeper step lie between the opening and the bore) states both, the counterbore first as
/// in draftwright's segment order, and claims all four sizes; lint then finds nothing missing.
/// draftwright itself prints only the counterbore (its one recess slot), which its own lint
/// would report: the port departs from it here (accuracy over parity). No captured part has
/// such a hole, so the plate's holes are given both.
#[test]
fn a_hole_with_a_counterbore_and_a_spotface_states_both() {
    let mut json = recognition("plate");
    for f in json["quiddity"]["features"].as_array_mut().unwrap() {
        f["record"]["spotface"] = json!({ "diameter": 20.0, "depth": 1.0 });
        f["record"]["cbore"] = json!({ "diameter": 11.0, "depth": 6.4 });
    }
    let (part, analysis) = analysed_with("plate", &json);
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    let c = find(&plan, "d:f:hole_pattern:f6/bore.diameter");
    // Two steps: the bore, then each step on its own line (maintainer decision, 2026-10-29).
    assert_eq!(c.label, "4× ⌀6 THRU\n⌴ ⌀11 ↧ 6.4\n⌴SF ⌀20 ↧ 1");
    for (parameter, value) in [
        ("counterbore.diameter", 11.0),
        ("counterbore.depth", 6.4),
        ("spotface.diameter", 20.0),
        ("spotface.depth", 1.0),
    ] {
        let m: Vec<f64> = c
            .measurements
            .iter()
            .filter(|m| m.parameter == parameter)
            .map(|m| m.value)
            .collect();
        assert_eq!(m, [value; 4], "{parameter}");
    }
    let font =
        Font::new(std::fs::read(fixture("../../fonts/IBMPlexMono-Regular.ttf")).unwrap()).unwrap();
    let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
    let found: Vec<String> = draftwright::lint::lint(&analysis, &drawing)
        .iter()
        .map(|i| format!("{} {:?}", i.code, i.requirements))
        .collect();
    assert!(found.is_empty(), "{found:#?}");
    // A hole-table row states the stacked steps on one line, in callout order.
    let spec: Spec = serde_json::from_value(json!({"hole_table": "always"})).unwrap();
    let tabled = serde_json::to_value(model::plan_escalated(&analysis, &spec).unwrap()).unwrap();
    let cells: Vec<&str> = tabled["hole_tables"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|t| t["rows"].as_array().unwrap())
        .flat_map(|r| r["cells"].as_array().unwrap())
        .filter_map(Value::as_str)
        .filter(|c| c.contains('⌴'))
        .collect();
    assert!(!cells.is_empty(), "{tabled}");
    for cell in cells {
        assert_eq!(cell, "⌀6 THRU ⌴ ⌀11 ↧ 6.4 ⌴SF ⌀20 ↧ 1");
    }
}

/// ADR4-7: a through hole's depth is not part of its callout, so a through hole whose record
/// gives none is still called out; a blind hole without a depth cannot be, and says why.
#[test]
fn only_a_blind_hole_needs_its_depth() {
    let mut json = recognition("plate");
    for f in json["quiddity"]["features"].as_array_mut().unwrap() {
        f["record"].as_object_mut().unwrap().remove("depth");
    }
    let plan = planned("plate", &json, &Spec::default());
    assert_eq!(labels(&plan, "callout"), ["4× ⌀6 THRU"]);
    json["quiddity"]["features"][0]["record"]["bottom"] = "flat".into();
    let plan = planned("plate", &json, &Spec::default());
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "feature_not_dimensioned")
        .unwrap_or_else(|| panic!("reported: {:?}", plan.problems));
    assert_eq!(p.subjects, ["f:hole:f6"]);
    assert!(
        p.message.contains("blind and has no depth"),
        "{}",
        p.message
    );
    assert_eq!(labels(&plan, "callout"), ["3× ⌀6 THRU"]);
}

/// A hole whose bottom recognition did not establish is called out blind to its depth, as
/// draftwright does, and says so (accuracy over parity: no silent claim).
#[test]
fn an_unestablished_hole_bottom_is_reported() {
    let mut json = recognition("plate");
    json["quiddity"]["features"][0]["record"]["bottom"] = "unknown".into();
    let plan = planned("plate", &json, &Spec::default());
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "hole_bottom_unknown")
        .unwrap_or_else(|| panic!("reported: {:?}", plan.problems));
    assert_eq!(p.severity, "info");
    assert!(
        !planned("plate", &recognition("plate"), &Spec::default())
            .problems
            .iter()
            .any(|p| p.code == "hole_bottom_unknown")
    );
}

/// R1 invariant 2 (ADR4-4) and ARCH0019-1: the renderer draws the plan's own label text and
/// composes none; dimension runs use planned terms, and furniture uses planned caption text.
#[test]
fn renderers_draw_only_from_the_plan() {
    let (part, a) = analysed_with("plate", &recognition("plate"));
    let font = Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let mut plan = model::plan(&a, &Spec::default()).unwrap();
    let runs = |plan: &Plan| -> Vec<String> {
        model::render(&part, &a, plan, &font)
            .unwrap()
            .text
            .iter()
            .filter(|r| r.font == dims::FONT && r.size == dims::TEXT)
            .map(|r| r.text.clone())
            .collect()
    };
    let labels: Vec<String> = plan
        .dimensions
        .iter()
        .map(|d| d.terms.join(" "))
        .chain(plan.captions.iter().map(|c| c.text.clone()))
        // The planned ISO 5457 grid's margin labels: its column numbers and row letters.
        .chain(plan.sheet.zones.iter().flat_map(|z| {
            (1..=z.columns).map(|n| n.to_string()).chain(
                b"ABCDEFGHJKLMNPQRSTUVWXYZ"[..z.rows as usize]
                    .iter()
                    .map(|c| (*c as char).to_string()),
            )
        }))
        .collect();
    let before = runs(&plan);
    assert!(!before.is_empty());
    assert_eq!(
        plan.captions.len(),
        1,
        "the fixture must exercise planned furniture"
    );
    for caption in &plan.captions {
        assert_eq!(before.iter().filter(|r| **r == caption.text).count(), 1);
    }
    for r in &before {
        assert!(labels.contains(r), "{r:?} is not in the plan");
    }
    // Change both kinds of plan text: the drawing follows them, with no text of its own.
    let old_caption = plan.captions[0].text.clone();
    plan.captions[0].text = "CAPTION-FROM-PLAN".into();
    let d = plan
        .dimensions
        .iter_mut()
        .find(|d| d.id == "d:envelope/width.length")
        .unwrap();
    d.terms = vec!["WIDTH-FROM-PLAN".into()];
    let after = runs(&plan);
    assert!(after.contains(&"WIDTH-FROM-PLAN".to_string()));
    assert!(!after.contains(&"60".to_string()));
    assert!(after.contains(&"CAPTION-FROM-PLAN".to_string()));
    assert!(!after.contains(&old_caption));
}

/// R1 invariant 7: the same documents give the same plan.
#[test]
fn dimension_planning_is_deterministic() {
    let json = recognition("plate");
    let a = serde_json::to_string(&planned("plate", &json, &Spec::default())).unwrap();
    let b = serde_json::to_string(&planned("plate", &json, &Spec::default())).unwrap();
    assert_eq!(a, b);
}
