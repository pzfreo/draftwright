#[path = "common/reference_geometry.rs"]
mod reference_geometry;

#[path = "common/corpus.rs"]
mod corpus;

use draftwright::lint::{self, Mark};
use draftwright::model::{self, Analysis, Plan, Spec};
use draftwright::output::Claim;
use serde_json::json;

fn native(file: &str) -> Analysis {
    model::analyse_native(&corpus::dir().unwrap().join(file))
        .unwrap()
        .1
}
// Independent authored profiles retain chain semantics; a connected whole profile now
// defaults to a baseline. Keep the original reference/omission/pin invariants on this case.
fn chain_profile(file: &str) -> Analysis {
    let mut a = native(file);
    a.features.retain(|f| f.kind != "plate");
    for f in a.features.iter_mut().filter(|f| f.kind == "turned_step") {
        f.parameters["profile"]["body_key"] = json!([f.id]);
    }
    a
}
fn references() -> Spec {
    serde_json::from_value(json!({"sheet":{"references":true}})).unwrap()
}
fn marks(p: &Plan) -> Vec<Mark> {
    p.dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .map(|d| Mark {
            units: Default::default(),
            name: d.id.clone(),
            view: Some(d.view.clone()),
            text: d.label.clone(),
            claims: d
                .measurements
                .iter()
                .map(|m| Claim {
                    feature: m.feature.clone(),
                    parameter: m.parameter.clone(),
                })
                .collect(),
        })
        .collect()
}
#[test]
fn grm03_has_an_overall_and_baseline_without_plate_thickness() {
    let a = native("gramel/GRM-03_thumbwheel_drive_screw.step");
    // The small estimate withholds the full head; its 2 mm interval is determined
    // by the printed 0.5 and 2.5 stations. A roomy constrained sheet retains both
    // local head lengths in a complete detail. Check both representations and use
    // the independent geometric linter to prove every physical band quantity.
    for (spec, local_lengths) in [
        (Spec::default(), 1),
        (
            serde_json::from_value(json!({"sheet":{"size":"A3","scale":"2:1"}})).unwrap(),
            2,
        ),
    ] {
        let p = model::plan(&a, &spec).unwrap();
        assert!(
            p.dimensions
                .iter()
                .any(|d| d.id == "d:envelope/width.length" && d.label == "23.5")
        );
        let links: Vec<_> = p
            .dimensions
            .iter()
            .filter(|d| d.id.ends_with("/step.length"))
            .collect();
        assert_eq!(links.len(), local_lengths);
        assert_eq!(
            p.dimensions
                .iter()
                .filter(|d| d.id.ends_with("/step_position.length"))
                .count(),
            2
        );
        assert_eq!(
            links
                .iter()
                .filter(|d| d.label.starts_with('(') && d.label.ends_with(')'))
                .count(),
            0
        );
        assert!(!p.dimensions.iter().any(|d| d.id.contains("f:plate:")));
        let issues = lint::check(&a, &marks(&p));
        assert!(
            !issues.iter().any(
                |i| i.code == "closed_dimension_chain" || i.code == "plate_requirement_missing"
            ),
            "{spec:?}: {issues:?} / {:?}",
            p.dimensions
                .iter()
                .map(|d| (&d.id, &d.label, &d.measurements))
                .collect::<Vec<_>>()
        );
        let outcomes = lint::turned::outcomes(&a, &marks(&p));
        assert_eq!(outcomes.len(), 8);
        assert!(
            outcomes
                .iter()
                .all(|o| o.state == lint::turned::State::Placed),
            "{outcomes:?}"
        );
    }
}
#[test]
fn ctc05_states_its_od_and_across_flats_once() {
    let a = native("nist/nist_ctc_05_asme1_rd.stp");
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert!(
        !p.dimensions
            .iter()
            .any(|d| d.id == "d:envelope/depth.length" || d.id == "d:envelope/width.length")
    );
    assert!(p.dimensions.iter().any(|d| d.label == "ø558.8"));
    assert!(p.dimensions.iter().any(|d| d.label == "527.05 A/F"));
}
#[test]
fn slot_chain_keeps_overall_and_functional_length_without_closure() {
    let a = native("mfcadpp/10047.step");
    let p = model::plan(&a, &references()).unwrap();
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.id == "d:envelope/height.length")
    );
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.id == "d:f:slot:f5/slot_length.length" && d.label == "19.08")
    );
    assert!(
        !p.dimensions
            .iter()
            .any(|d| d.id == "d:f:slot:f5/location_slot.length" && !d.label.starts_with('('))
    );
    let floor = p
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == "step_level" && m.parameter == "step_height.length.f6")
        })
        .unwrap();
    assert_eq!(floor.id, "d:f:slot:f5/location_slot.length");
    assert_eq!(floor.label, "(5.134)");
    assert!(p.omitted.iter().any(|d| d.id == "d:step_level/step_height.length.f6"
        && matches!(&d.state, draftwright::items::State::Covered { by } if by == &floor.id)));
    let spec = model::edit(&references(), &json!([{"op":"set", "path":"dimensions/d:f:slot:f5~1location_slot.length/omit", "value":true}])).unwrap();
    let p = model::plan(&a, &spec).unwrap();
    let floor = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:step_level/step_height.length.f6")
        .unwrap();
    assert_eq!(
        // The restored geometric floor is 5.133; the slot-centre reference above is 5.1335.
        // Preserve both distinct modelled values at the new default precision.
        floor.label,
        "(5.133)",
        "restoring the floor must not close the chain again"
    );
}
#[test]
fn concentric_bolt_circles_have_axis_location_credit() {
    let (part, a) = model::analyse_native(
        &corpus::dir()
            .unwrap()
            .join("cadgenbench/flanged_spool_132.step"),
    )
    .unwrap();
    let p = model::plan(&a, &Spec::default()).unwrap();
    // Numeric claims alone do not convey the shared centre. Require the actual
    // outline, pitch circle and centre mark, including on recognised turned stock.
    assert!(
        lint::check(&a, &marks(&p))
            .iter()
            .any(|i| { i.code == "hole_pattern_requirement_missing" })
    );
    let mut drawing = model::render(
        &part,
        &a,
        &p,
        &draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    let issues = lint::lint(&a, &drawing);
    assert!(
        !issues
            .iter()
            .any(|i| i.code == "hole_pattern_requirement_missing"),
        "{issues:?}"
    );
    drawing.sheet.centre_marks.clear();
    assert!(
        lint::lint(&a, &drawing)
            .iter()
            .any(|i| { i.code == "hole_pattern_requirement_missing" })
    );
}
#[test]
fn fully_dimensioned_box_is_clear() {
    let a: Analysis = serde_json::from_value(json!({"schema":model::ANALYSIS_SCHEMA,
        "binding":{"source_sha256":"","part":"box","face_count":6,"loader":"test"},
        "envelope":[[0,0,0],[40,30,10]],"solid_count":1,"iso_bounds":[-20,-20,20,20],
        "principal_profiles":[{"bounds":[[0,0,0],[40,30,10]],"faces":(0..3).flat_map(|axis| [0,1].map(move |end| json!({"axis":axis,"at":if end==0 {0} else {[40,30,10][axis]},"inner":[],"outer_edges":4,"outer_arcs":[],"outer_lines":[]}))).collect::<Vec<_>>()}]})).unwrap();
    let spec: Spec =
        serde_json::from_value(json!({"sheet":{"title":{"tolerance":"ISO 2768-m"}}})).unwrap();
    let p = model::plan(&a, &spec).unwrap();
    assert_eq!(serde_json::to_value(&p).unwrap()["status"], "clear");
    assert!(!p.problems.iter().any(|p| p.code == "unclaimed_geometry"));
    let less = model::edit(
        &spec,
        &json!([{"op":"set", "path":"dimensions/d:envelope~1width.length/omit", "value":true}]),
    )
    .unwrap();
    let p = model::plan(&a, &less).unwrap();
    // Unclaimed faces are advisory; independent lint checks omitted overall dimensions.
    assert_eq!(serde_json::to_value(&p).unwrap()["status"], "clear");
    assert!(p.problems.iter().all(|p| p.severity == "info"));
    assert_eq!(p.completeness.claimed_faces, 0);
    assert!(p.problems.iter().any(|p| p.code == "unclaimed_geometry"));
}
#[test]
fn lint_detects_duplicate_claims_and_closed_turned_chains() {
    let a = chain_profile("gramel/GRM-03_thumbwheel_drive_screw.step");
    let p = model::plan(&a, &references()).unwrap();
    let mut ink = marks(&p);
    for m in &mut ink {
        m.text = m.text.trim_matches(['(', ')']).to_string();
    }
    ink.push(Mark {
        units: Default::default(),
        name: "overall".into(),
        view: Some("v:front".into()),
        text: "23.5".into(),
        claims: vec![Claim {
            feature: "envelope".into(),
            parameter: "width.length".into(),
        }],
    });
    let mut duplicate = ink
        .iter()
        .find(|m| m.claims.iter().any(|c| c.parameter == "step.length"))
        .unwrap()
        .clone();
    duplicate.name = "duplicate".into();
    ink.push(duplicate);
    let codes: Vec<_> = lint::check(&a, &ink)
        .iter()
        .map(|i| i.code.clone())
        .collect();
    assert!(codes.contains(&"duplicate_measurement".into()));
    assert!(codes.contains(&"closed_dimension_chain".into()));
}
#[test]
fn wall_witness_avoids_the_hole_centreline() {
    let a = native("nist/nist_ftc_07_asme1_rd.stp");
    let p = model::plan(&a, &Spec::default()).unwrap();
    let d = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:plate:f83/thickness.length")
        .unwrap();
    assert_eq!(d.label, "2× 9.297");
    assert_eq!(d.view, "v:plan");
}
#[test]
fn consolidated_flush_boss_height_is_not_reported_missing() {
    let a: Analysis=serde_json::from_value(json!({"schema":model::ANALYSIS_SCHEMA,
        "binding":{"source_sha256":"","part":"flush_hub","face_count":8,"loader":"test"},
        "envelope":[[0,0,0],[4.5,10,24]],"iso_bounds":[-20,-20,20,20],"solid_count":1,
        "features":[{"id":"f:boss:1","kind":"boss","faces":[6,7],
        "parameters":{"axis":[1,0,0],"location":[4.5,5,8],"diameter":8,"height":4.5},
        "fingerprint":{"version":"test","family":"boss","subtype":"BossRecord","sizes":{},"position":[2.25,5,8],"axis":[1,0,0],"neighbourhood":[]}}]})).unwrap();
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert!(
        p.omitted
            .iter()
            .any(|d| d.id == "d:f:boss:1/boss_height.length")
    );
    assert!(lint::bosses::coverage(&a, &marks(&p)).is_empty());
}

#[test]
fn cross_view_locations_keep_single_claim_owners_and_controlling_labels() {
    for file in [
        "nist/nist_ctc_02_asme1_rc.stp",
        "nist/nist_ftc_10_asme1_rb.stp",
        "cadgenbench_inputs/cgb245.step",
    ] {
        let a = native(file);
        let p = model::plan(&a, &Spec::default()).unwrap();
        let mut owners = std::collections::BTreeMap::new();
        for d in p.dimensions.iter().filter(|d| d.placed.is_some()) {
            for m in &d.measurements {
                assert!(
                    owners.insert((&m.feature, &m.parameter), &d.id).is_none(),
                    "{file}: duplicate claim {m:?} by {}",
                    d.id
                );
                if m.parameter.starts_with("location.length.") {
                    assert!(!d.label.starts_with('('), "{file}: {}", d.id);
                }
            }
        }
    }
}

#[test]
fn references_become_controlling_without_moving_other_ink() {
    for (file, total) in [
        ("mfcadpp/10047.step", "d:envelope/height.length"),
        (
            "gramel/GRM-03_thumbwheel_drive_screw.step",
            "d:envelope/width.length",
        ),
        (
            "gramel/GRM-05_depth_lock_bolt.step",
            "d:envelope/height.length",
        ),
        ("gramel/string_post.step", "d:envelope/height.length"),
    ] {
        let a = chain_profile(file);
        let full = model::plan(&a, &references()).unwrap();
        let reference = full
            .dimensions
            .iter()
            .find(|d| d.label.starts_with('('))
            .unwrap();
        let less = model::edit(&references(), &json!([{"op":"set", "path":format!("dimensions/{}/omit", draftwright::items::key(total)), "value":true}])).unwrap();
        let p = model::plan(&a, &less).unwrap();
        let controlling = p.dimensions.iter().find(|d| d.id == reference.id).unwrap();
        assert_eq!(
            reference.label,
            format!("({})", controlling.label),
            "{file}: {:?}",
            p.dimensions
                .iter()
                .map(|d| (&d.id, &d.label, &d.basis))
                .collect::<Vec<_>>()
        );
        for now in &p.dimensions {
            let was = full.dimensions.iter().find(|d| d.id == now.id).unwrap();
            reference_geometry::assert_local(was, now);
        }
    }
}

#[test]
fn a_pinned_reference_keeps_its_ink_and_reports_a_broken_controlling_chain() {
    let a = chain_profile("gramel/GRM-03_thumbwheel_drive_screw.step");
    // A pin holds the ink of the drawing it was taken from: the recovered one, as the pinned
    // plan below is recovered too. One sheet keeps the established frame: with free paper,
    // the omission can select a different small-sheet scale before the pin is read.
    let base: Spec =
        serde_json::from_value(json!({"sheet":{"references":true},"sheets":{"max":1}})).unwrap();
    let full = model::plan_recovered(&a, &base).unwrap();
    // ISSUE-1505-b: the chain leaves its longest band, not the 0.5 shoulder, to the total.
    let reference = full.dimensions.iter().find(|d| d.reference).unwrap();
    assert_ne!(reference.label, "(0.5)");
    let spec = model::edit(&base, &json!([
        {"op":"set", "path":"sheet/references", "value":false},
        {"op":"set", "path":format!("dimensions/{}/place", draftwright::items::key(&reference.id)), "value":{"pinned":true}},
        {"op":"set", "path":"dimensions/d:envelope~1width.length/omit", "value":true}
    ])).unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    let pinned = p.dimensions.iter().find(|d| d.id == reference.id).unwrap();
    assert_eq!(pinned.label, reference.label);
    assert_eq!(pinned.placed, reference.placed);
    let finding = p
        .problems
        .iter()
        .find(|p| p.code == "pinned_reference_chain_changed")
        .unwrap_or_else(|| {
            panic!(
                "{:?}",
                p.dimensions
                    .iter()
                    .map(|d| (&d.id, &d.label, &d.basis))
                    .collect::<Vec<_>>()
            )
        });
    assert_eq!(finding.severity, "warning");
    assert_eq!(
        finding.subjects.as_slice(),
        std::slice::from_ref(&reference.id)
    );
    let released = model::edit(&spec, &serde_json::to_value(&finding.fix).unwrap()).unwrap();
    let p = model::plan_recovered(&a, &released).unwrap();
    assert!(
        !p.problems
            .iter()
            .any(|p| p.code == "pinned_reference_chain_changed")
    );
    assert_eq!(
        p.dimensions
            .iter()
            .find(|d| d.id == reference.id)
            .unwrap()
            .label,
        reference.label.trim_matches(['(', ')'])
    );
}

fn label(p: &Plan, id: &str) -> String {
    p.dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap_or_else(|| panic!("{id} not planned"))
        .label
        .clone()
}

#[test]
fn shoulders_on_slot_walls_are_references_when_the_slot_is_stated() {
    // 10047: 18.6 + 4.5 = 23.1, and the slot's centre 20.9 fixes both walls.
    let a = native("mfcadpp/10047.step");
    let p = model::plan(&a, &references()).unwrap();
    assert_eq!(label(&p, "d:f:slot:f5/slot_width.length"), "4.53");
    assert_eq!(
        label(&p, "d:f:slot:f5/location_slot_across.length"),
        "20.877"
    );
    assert_eq!(label(&p, "d:step_level/step_position.length.f5"), "(18.61)");
    assert_eq!(
        label(&p, "d:step_level/step_position.length.f7"),
        "(23.144)"
    );
    let issues = lint::check(&a, &marks(&p));
    assert!(
        !issues
            .iter()
            .any(|i| i.code == "closed_dimension_chain" || i.code == "duplicate_measurement"),
        "{issues:?}"
    );
    // Without the centre line only the farther shoulder closes the chain.
    let spec = model::edit(&references(), &json!([{"op":"set", "path":"dimensions/d:f:slot:f5~1location_slot_across.length/omit", "value":true}])).unwrap();
    let p = model::plan(&a, &spec).unwrap();
    assert_eq!(label(&p, "d:step_level/step_position.length.f5"), "18.61");
    assert_eq!(
        label(&p, "d:step_level/step_position.length.f7"),
        "(23.144)"
    );
    // The same rule on z ladders (FTC-09's slots, FTC-10's 12.5 + 50 = 62.5).
    let p = model::plan(&native("nist/nist_ftc_09_asme1_rd.stp"), &references()).unwrap();
    assert_eq!(label(&p, "d:step_level/step_height.length.f81"), "(41.275)");
    let p = model::plan(&native("nist/nist_ftc_10_asme1_rb.stp"), &references()).unwrap();
    assert_eq!(label(&p, "d:step_level/step_height.length.f163"), "(62.5)");
}

#[test]
fn lint_reports_the_10047_slot_wall_closure_when_every_member_is_stated() {
    let a = native("mfcadpp/10047.step");
    let p = model::plan(&a, &references()).unwrap();
    let mut ink = marks(&p);
    for m in &mut ink {
        m.text = m.text.trim_matches(['(', ')']).to_string();
    }
    let closed: Vec<_> = lint::check(&a, &ink)
        .into_iter()
        .filter(|i| i.code == "closed_dimension_chain")
        .filter_map(|i| i.annotation)
        .collect();
    assert!(
        closed
            .iter()
            .any(|n| n.starts_with("d:step_level/step_position.length.f7")),
        "{closed:?}"
    );
}

#[test]
fn a_plate_between_a_shoulder_and_the_box_is_not_stated_again() {
    // 10060: 33.6 - 19.3 = 14.3, the plate's thickness.
    let a = native("mfcadpp/10060.step");
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert!(
        !p.dimensions
            .iter()
            .any(|d| d.id == "d:f:plate:f5/thickness.length")
    );
    assert!(
        p.omitted
            .iter()
            .any(|d| d.id == "d:f:plate:f5/thickness.length")
    );
    let issues = lint::check(&a, &marks(&p));
    assert!(
        !issues
            .iter()
            .any(|i| i.code == "closed_dimension_chain" || i.code == "plate_requirement_missing"),
        "{issues:?}"
    );
}

#[test]
fn lint_reports_an_overall_that_repeats_an_outside_diameter_or_across_flats() {
    // CTC-05 before this stream: linear 558.8 beside ø558.8, 527.0 beside 527.05 A/F.
    let a = native("nist/nist_ctc_05_asme1_rd.stp");
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert!(
        !lint::check(&a, &marks(&p))
            .iter()
            .any(|i| i.code == "duplicate_measurement" || i.code == "closed_dimension_chain")
    );
    let mut ink = marks(&p);
    for (name, parameter, text) in [
        ("overall x", "width.length", "527.0"),
        ("overall y", "depth.length", "558.8"),
    ] {
        ink.push(Mark {
            units: Default::default(),
            name: name.into(),
            view: Some("v:front".into()),
            text: text.into(),
            claims: vec![Claim {
                feature: "envelope".into(),
                parameter: parameter.into(),
            }],
        });
    }
    let duplicates: Vec<_> = lint::check(&a, &ink)
        .into_iter()
        .filter(|i| i.code == "duplicate_measurement")
        .filter_map(|i| i.annotation)
        .collect();
    assert!(
        duplicates.contains(&"overall x".to_string()),
        "{duplicates:?}"
    );
    assert!(
        duplicates.contains(&"overall y".to_string()),
        "{duplicates:?}"
    );
}

#[test]
fn demo_bracket_references_are_optional_and_accounted_for() {
    let (part, a) = model::analyse_native(
        &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/samples/draftwright-demo-bracket.step.gz"),
    )
    .unwrap();
    let default = model::plan_recovered(&a, &Spec::default()).unwrap();
    let opt_in = model::plan_recovered(&a, &references()).unwrap();
    let ladder = |p: &Plan| {
        let mut labels: Vec<_> = p
            .dimensions
            .iter()
            .filter(|d| {
                d.view == "v:front"
                    && d.placed.is_some()
                    && (d.id.starts_with("d:step_level/step_height")
                        || matches!(
                            d.id.as_str(),
                            "d:envelope/height.length"
                                | "d:f:slot:f12/slot_width.length"
                                | "d:f:slot:f12/location_slot_across.length"
                                | "d:f:boss:f10/boss_height.length"
                        ))
            })
            .map(|d| (d.value, d.label.clone()))
            .collect();
        labels.sort_by(|a, b| a.0.total_cmp(&b.0));
        labels
            .into_iter()
            .map(|(_, label)| label)
            .collect::<Vec<_>>()
    };
    // ADR1-8: the boss stands on the base plate, whose counted `2× 8` thickness and the 20 rung
    // determine its 12 height (the boss-height chain of `a_boss_height_on_a_stated_level_is_a_reference`).
    assert_eq!(ladder(&default), ["8", "20", "32", "48"]);
    assert_eq!(
        ladder(&opt_in),
        ["8", "(12)", "20", "(28)", "32", "(36)", "48"]
    );
    assert!(default.dimensions.iter().all(|d| !d.reference));
    for reference in opt_in.dimensions.iter().filter(|d| d.reference) {
        let o = default
            .omitted
            .iter()
            .find(|o| o.id == reference.id)
            .unwrap();
        assert!(matches!(&o.state, draftwright::items::State::Covered { by }
            if reference.determined_by.contains(by)));
        for by in &reference.determined_by {
            assert!(o.basis.contains(by), "{}", o.basis);
            assert!(
                default
                    .dimensions
                    .iter()
                    .any(|d| d.id == *by && d.placed.is_some())
            );
        }
        assert!(
            !default
                .problems
                .iter()
                .any(|p| p.subjects.contains(&reference.id) && p.code == "view_plan_incomplete")
        );
    }
    assert_eq!(default.completeness, opt_in.completeness);
    for p in [&default, &opt_in] {
        let issues = lint::check(&a, &marks(p));
        assert!(
            !issues.iter().any(|i| matches!(
                i.code.as_str(),
                "closed_dimension_chain" | "duplicate_measurement"
            )),
            "{issues:?}"
        );
        let drawing = model::render(
            &part,
            &a,
            p,
            &draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap(),
        )
        .unwrap();
        for omitted in &default.omitted {
            if p.spec.sheet.references != Some(true) {
                assert!(!drawing.annotations.iter().any(|d| d.name == omitted.id));
            }
        }
    }
    let reference = opt_in
        .dimensions
        .iter()
        .find(|d| d.label == "(28)")
        .unwrap();
    for entry in [
        json!({"style":"reference"}),
        json!({"place":{"pinned":true}}),
    ] {
        let spec: Spec =
            serde_json::from_value(json!({"dimensions":{&reference.id:entry}})).unwrap();
        let p = model::plan_recovered(&a, &spec).unwrap();
        let kept = p.dimensions.iter().find(|d| d.id == reference.id).unwrap();
        assert_eq!(kept.label, "(28)");
        assert!(kept.reference && kept.placed.is_some());
        assert!(!p.dimensions.iter().any(|d| d.label == "(36)"));
    }
}

#[test]
fn references_sheet_option_validates_and_round_trips() {
    for value in [json!(true), json!(false)] {
        let spec = model::edit(
            &Spec::default(),
            &json!([{"op":"set","path":"sheet/references","value":value}]),
        )
        .unwrap();
        assert_eq!(
            serde_json::to_value(&spec).unwrap()["sheet"]["references"],
            value
        );
        let removed =
            model::edit(&spec, &json!([{"op":"remove","path":"sheet/references"}])).unwrap();
        assert_eq!(removed.sheet.references, None);
    }
    for value in [json!("true"), json!(1), json!([]), json!({})] {
        assert!(
            model::edit(
                &Spec::default(),
                &json!([{"op":"set","path":"sheet/references","value":value}])
            )
            .is_err()
        );
    }
}

#[test]
fn automatic_references_are_covered_without_losing_controlling_dimensions() {
    let boss = model::analyse_native(
        &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/boss_circle.step"),
    )
    .unwrap()
    .1;
    for a in [
        boss,
        native("mfcadpp/10047.step"),
        native("mfcadpp/10190.step"),
        native("nist/nist_ftc_09_asme1_rd.stp"),
        chain_profile("gramel/GRM-03_thumbwheel_drive_screw.step"),
    ] {
        let shown = model::plan(&a, &references()).unwrap();
        let hidden = model::plan(&a, &Spec::default()).unwrap();
        assert!(shown.dimensions.iter().any(|d| d.reference));
        assert!(hidden.dimensions.iter().all(|d| !d.reference));
        for d in &shown.dimensions {
            if d.reference {
                let o = hidden.omitted.iter().find(|o| o.id == d.id).unwrap();
                assert!(matches!(&o.state, draftwright::items::State::Covered { by }
                    if hidden.dimensions.iter().any(|d| d.id == *by && d.placed.is_some())));
                assert!(
                    d.determined_by.iter().all(|by| o.basis.contains(by)),
                    "{}",
                    o.basis
                );
            } else {
                let same = hidden.dimensions.iter().find(|q| q.id == d.id).unwrap();
                assert_eq!(same.label, d.label);
                assert_eq!(same.placed.is_some(), d.placed.is_some());
            }
        }
        let issues = lint::check(&a, &marks(&hidden));
        assert!(
            !issues.iter().any(|i| matches!(
                i.code.as_str(),
                "closed_dimension_chain" | "duplicate_measurement"
            )),
            "{issues:?}"
        );
    }
}

/// A corpus part's native analysis trimmed to the features a case needs (`draftwright analyse`,
/// with its ink, profiles and other features emptied): planning the whole part is too slow for a
/// debug test, and these cases depend only on the kept features and the part's box and levels.
fn trimmed(part: &str) -> Analysis {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("tests/fixtures/one_owner/{part}.analysis.json"));
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// The drawn dimensions claiming *feature*'s *parameter*, each by its ID and label.
fn owners(p: &Plan, feature: &str, parameter: &str) -> Vec<(String, String)> {
    p.dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == feature && m.parameter == parameter)
        })
        .map(|d| (d.id.clone(), d.label.clone()))
        .collect()
}

fn covered_by(p: &Plan, id: &str) -> String {
    let o = p
        .omitted
        .iter()
        .find(|o| o.id == id)
        .unwrap_or_else(|| panic!("{id} is not omitted"));
    match &o.state {
        draftwright::items::State::Covered { by } => {
            assert!(
                p.dimensions
                    .iter()
                    .any(|d| &d.id == by && d.placed.is_some())
            );
            by.clone()
        }
        other => panic!("{id}: {other:?}"),
    }
}

/// ADR1-8: ftc09's top seat lies on the 128.1 face level; its location and the level's rung are
/// one station from the base, stated once (the rung prints its modelled value as `128.100`).
#[test]
fn ftc09_states_a_seat_on_a_face_level_once() {
    let a = trimmed("ftc09");
    let p = model::plan(&a, &Spec::default()).unwrap();
    let seat = owners(&p, "f:section_recess:f107", "seat_location.location.z");
    assert_eq!(seat.len(), 1);
    assert_eq!(seat[0].0, "d:step_level/step_height.length.f106");
    assert_eq!(
        covered_by(&p, "d:f:section_recess:f107/seat_location.location.z"),
        seat[0].0
    );
}

/// ADR1-8: cgb202's four ø10 rods run between the same two planes (recognition reads one end at
/// 152.500000015, the others at 152.500002497): one counted `4× 255`.
#[test]
fn cgb202_counts_its_equal_rods_once() {
    let a = trimmed("cgb202");
    let p = model::plan(&a, &Spec::default()).unwrap();
    let rods: Vec<_> = (3..=6)
        .flat_map(|n| {
            owners(
                &p,
                [
                    "f:boss:f1815",
                    "f:boss:f1824",
                    "f:boss:f1833",
                    "f:boss:f1842",
                ][n - 3],
                "boss_height.length",
            )
        })
        .collect();
    let one = (
        "d:f:boss:f1815/boss_height.length".to_string(),
        "4× 255".to_string(),
    );
    assert_eq!(rods, vec![one; 4]);
}

/// ADR4-U1: ctc01's hexagonal boss stands on the 100 level, and the 150 overall closes it; its 50
/// height is conveyed by the rung and the overall, and lint, reading the same chain, agrees.
#[test]
fn ctc01_polygonal_boss_height_is_conveyed_by_its_level_and_the_overall() {
    let a = trimmed("ctc01");
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert!(owners(&p, "f:polygonal_boss:f61", "boss_height.length").is_empty());
    covered_by(&p, "d:f:polygonal_boss:f61/boss_height.length");
    for id in [
        "d:envelope/height.length",
        "d:step_level/step_height.length.f3",
    ] {
        assert!(
            p.dimensions.iter().any(|d| d.id == id && !d.reference),
            "{id}"
        );
    }
    let shown = model::plan(&a, &references()).unwrap();
    let boss = shown
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:polygonal_boss:f61/boss_height.length")
        .unwrap();
    assert_eq!((boss.label.as_str(), boss.reference), ("(50)", true));
    let issues = lint::check(&a, &marks(&p));
    assert!(
        !issues.iter().any(|i| matches!(
            i.code.as_str(),
            "polygonal_boss_requirement_missing" | "closed_dimension_chain"
        )),
        "{issues:?}"
    );
}

/// ISSUE-1505-b: cgb217's turned chain closes on its 60 overall. The 1 mm end shoulder keeps its
/// own length; the longest band (45) is the member the overall and the others determine, and
/// lint agrees that every band length is stated.
#[test]
fn cgb217_keeps_its_short_shoulder_and_leaves_the_longest_band_to_the_total() {
    let a = trimmed("cgb217");
    let p = model::plan(&a, &Spec::default()).unwrap();
    let shoulder = owners(&p, "f:turned_step:f321", "step.length");
    assert_eq!(shoulder.len(), 1);
    assert_eq!(shoulder[0].1, "1");
    assert!(owners(&p, "f:turned_step:f358", "step.length").is_empty());
    assert_eq!(
        covered_by(&p, "d:f:turned_step:f358/step.length"),
        "d:envelope/height.length"
    );
    let issues = lint::check(&a, &marks(&p));
    assert!(
        !issues.iter().any(|i| matches!(
            i.code.as_str(),
            "axial_length_missing" | "closed_dimension_chain"
        )),
        "{issues:?}"
    );
    // Without the overall, the 45 is no longer determined: lint reports it missing.
    let partial: Vec<_> = marks(&p)
        .into_iter()
        .filter(|m| m.name != "d:envelope/height.length")
        .collect();
    assert!(
        lint::check(&a, &partial)
            .iter()
            .any(|i| i.id == "l:axial_length_missing:f:turned_step:f358/step.length")
    );
}

/// A plain mark of *text* claiming one *feature*'s *parameter*, drawn in the front view.
fn mark(name: &str, text: &str, claims: &[(&str, &str)]) -> Mark {
    Mark {
        units: Default::default(),
        name: name.into(),
        view: Some("v:front".into()),
        text: text.into(),
        claims: claims
            .iter()
            .map(|(feature, parameter)| Claim {
                feature: (*feature).into(),
                parameter: (*parameter).into(),
            })
            .collect(),
    }
}

fn redundancy_codes(a: &Analysis, marks: &[Mark]) -> Vec<String> {
    lint::check(a, marks)
        .into_iter()
        .filter(|i| {
            matches!(
                i.code.as_str(),
                "duplicate_measurement" | "closed_dimension_chain"
            )
        })
        .map(|i| i.id)
        .collect()
}

/// ADR1-8 (compound callouts, unsupported spans): mfcadpp 10063's 3.304 × 6.278 pocket opens on
/// the base and is 24.518 deep, so its floor is the 24.518 face level. The pocket callout states
/// that interval; the rung yields to it, carrying its claim, and lint (reading the callout's
/// depth as the same support planes) finds nothing stated twice. Drawn again, the rung is.
#[test]
fn m10063_a_rung_on_its_pockets_floor_is_conveyed_by_the_pocket_callout() {
    let a = trimmed("m10063");
    let p = model::plan(&a, &Spec::default()).unwrap();
    let rung = "d:step_level/step_height.length.f39";
    let callout = "d:f:section_recess:f25/pocket_width.length";
    assert!(!p.dimensions.iter().any(|d| d.id == rung));
    assert_eq!(covered_by(&p, rung), callout);
    assert_eq!(
        owners(&p, "step_level", "step_height.length.f39"),
        vec![(
            callout.to_string(),
            "3.304 × 6.278 × 24.518 DEEP".to_string()
        )]
    );
    let own = marks(&p);
    assert!(redundancy_codes(&a, &own).is_empty());
    let mut twice = own.clone();
    twice.push(mark(
        rung,
        "24.518",
        &[("step_level", "step_height.length.f39")],
    ));
    // Reported by its repeated claim and by its repeated support planes.
    let mut found = redundancy_codes(&a, &twice);
    found.dedup();
    assert_eq!(
        found,
        ["l:duplicate_measurement:step_level/step_height.length.f39"]
    );
}

/// ADR1-8 (point ordinates): mfcadpp 10020's 2 × 5.68 pocket is located to its centre (11.898)
/// with its width stated, which fixes both walls; the 10.898 and 12.898 rungs on those walls are
/// determined. They are left to the location and the callout (shown as reference with
/// `references`), and a toleranced rung stays controlling. ADR4-U1 (compound counts): a counted
/// `2× 2` stating both pockets' widths closes the far wall's chain just as the callout does.
#[test]
fn m10020_rungs_on_a_located_pockets_walls_yield_to_its_location_and_size() {
    let a = trimmed("m10020");
    let location = "d:f:section_recess:f35/location_pocket.z";
    let callout = "d:f:section_recess:f35/pocket_width.length";
    let walls = [
        "d:step_level/step_height.length.f36",
        "d:step_level/step_height.length.f38",
    ];
    let p = model::plan(&a, &Spec::default()).unwrap();
    for id in walls {
        assert!(!p.dimensions.iter().any(|d| d.id == id), "{id}");
        assert_eq!(covered_by(&p, id), location);
    }
    assert!(redundancy_codes(&a, &marks(&p)).is_empty());
    let shown = model::plan(&a, &references()).unwrap();
    for (id, label) in walls.iter().zip(["(10.898)", "(12.898)"]) {
        let d = shown.dimensions.iter().find(|d| &d.id == id).unwrap();
        assert_eq!((d.label.as_str(), d.reference), (label, true));
        assert_eq!(d.determined_by, [location, callout]);
    }
    let toleranced: Spec =
        serde_json::from_value(json!({"dimensions": {walls[1]: {"tolerance": "±0.1"}}})).unwrap();
    let kept = model::plan(&a, &toleranced).unwrap();
    let far = kept.dimensions.iter().find(|d| d.id == walls[1]).unwrap();
    assert_eq!((far.label.as_str(), far.reference), ("12.898 ±0.1", false));
    // Lint, independently: the far wall's rung drawn beside the location and a counted size.
    let counted = vec![
        mark(
            location,
            "11.898",
            &[("f:section_recess:f35", "location_pocket.z")],
        ),
        mark(
            "authored:widths",
            "2× 2",
            &[
                ("f:section_recess:f8", "pocket_width.length"),
                ("f:section_recess:f35", "pocket_width.length"),
            ],
        ),
        mark(
            walls[1],
            "12.898",
            &[("step_level", "step_height.length.f38")],
        ),
    ];
    assert_eq!(
        redundancy_codes(&a, &counted),
        ["l:closed_dimension_chain:step_level/step_height.length.f38"]
    );
}

/// ADR1-8: only a counted or compound label lets a rung yield in that pass. On mfcadpp 10020
/// (pocket callouts on the sheet) with a plate from the 17.961 level to the 26.685 level, the
/// 26.685 rung closes with the 17.961 rung and the plate's thickness, and no label is on that
/// chain: both rungs stay controlling, with or without `references`.
#[test]
fn a_rung_closed_by_a_rung_and_a_plate_without_a_label_stays_controlling() {
    let mut a = trimmed("m10020");
    let mut plate = a.features[0].clone();
    plate.id = "f:plate:1".into();
    plate.kind = "plate".into();
    plate.parameters = json!({
        "axis": "z", "lo": 17.960969758681, "hi": 26.68510842796, "u": 4.0, "v": 2.5
    })
    .as_object()
    .unwrap()
    .clone();
    a.features.push(plate);
    for spec in [Spec::default(), references()] {
        let p = model::plan(&a, &spec).unwrap();
        assert!(
            p.dimensions
                .iter()
                .any(|d| d.id == "d:f:section_recess:f35/pocket_width.length")
        );
        for (id, label) in [
            ("d:step_level/step_height.length.f28", "17.961"),
            ("d:step_level/step_height.length.f55", "26.685"),
            ("d:f:plate:1/thickness.length", "8.724"),
        ] {
            let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
            assert_eq!((d.label.as_str(), d.reference), (label, false), "{id}");
        }
    }
}

/// The dimension *id* in *p*, by its label and whether it is drawn as reference.
fn shown(p: &Plan, id: &str) -> (String, bool) {
    let d = p
        .dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap_or_else(|| panic!("{id} is not drawn"));
    (d.label.clone(), d.reference)
}

/// Lint's findings on *marks* among *codes*, by ID.
fn findings(a: &Analysis, marks: &[Mark], codes: &[&str]) -> Vec<String> {
    lint::check(a, marks)
        .into_iter()
        .filter(|i| codes.contains(&i.code.as_str()))
        .map(|i| i.id)
        .collect()
}

/// ADR1-8 (through-step legs): mfcadpp_holdout 275's through step has a 9.353 leg along x and a
/// 22.715 leg along y, each the rest of the box beyond a plate (4.045 and 11.345 thick). The legs
/// are the members that yield, to the plate and the overall: the plates and overalls stay
/// controlling, and lint (which accepts a plate's thickness with the extent as a leg's statement)
/// finds every leg and plate stated and nothing stated twice. With `references` the legs are
/// shown bracketed, determined by the plate and the overall; a toleranced leg stays controlling.
#[test]
fn h275_through_step_legs_yield_to_the_plates_and_overalls_that_state_them() {
    let a = trimmed("h275");
    let x = "d:f:through_step:f17/through_step_leg.length.x";
    let y = "d:f:through_step:f17/through_step_leg.length.y";
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(covered_by(&p, x), "d:f:plate:f0/thickness.length");
    assert_eq!(covered_by(&p, y), "d:f:plate:f1/thickness.length");
    for (id, label) in [
        ("d:f:plate:f0/thickness.length", "4.045"),
        ("d:f:plate:f1/thickness.length", "11.345"),
        ("d:envelope/width.length", "13.398"),
        ("d:envelope/depth.length", "34.060"),
    ] {
        assert_eq!(shown(&p, id), (label.to_string(), false), "{id}");
    }
    let codes = [
        "through_step_requirement_missing",
        "plate_requirement_missing",
        "duplicate_measurement",
        "closed_dimension_chain",
    ];
    assert!(findings(&a, &marks(&p), &codes).is_empty());
    let with = model::plan(&a, &references()).unwrap();
    for (id, label, by) in [
        (
            x,
            "(9.353)",
            ["d:f:plate:f0/thickness.length", "d:envelope/width.length"],
        ),
        (
            y,
            "(22.715)",
            ["d:f:plate:f1/thickness.length", "d:envelope/depth.length"],
        ),
    ] {
        assert_eq!(shown(&with, id), (label.to_string(), true));
        let d = with.dimensions.iter().find(|d| d.id == id).unwrap();
        assert_eq!(d.determined_by, by);
    }
    let toleranced: Spec =
        serde_json::from_value(json!({"dimensions": {x: {"tolerance": "±0.1"}}})).unwrap();
    let kept = model::plan(&a, &toleranced).unwrap();
    assert_eq!(shown(&kept, x), ("9.353 ±0.1".to_string(), false));
    assert_eq!(
        shown(&kept, "d:f:plate:f0/thickness.length"),
        ("4.045".to_string(), false)
    );
    // Lint, independently: the leg drawn beside the plate and the overall closes a chain.
    let mut drawn = marks(&p);
    drawn.push(mark(
        x,
        "9.353",
        &[("f:through_step:f17", "through_step_leg.length.x")],
    ));
    assert_eq!(
        findings(&a, &drawn, &codes),
        ["l:closed_dimension_chain:f:through_step:f17/through_step_leg.length.x"]
    );
}

/// ADR1-8 (through-step legs): mfcadpp_holdout 274's step runs along x with its 33.488 leg from
/// the 8.395 face level to the top. The rung and the overall height state it; the leg yields to
/// them, and lint accepts the rung and the extent as the leg's statement.
#[test]
fn h274_a_leg_from_a_face_level_to_the_top_yields_to_the_rung_and_the_overall() {
    let a = trimmed("h274");
    let leg = "d:f:through_step:f7/through_step_leg.length.z";
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(covered_by(&p, leg), "d:step_level/step_height.length.f7");
    assert_eq!(
        shown(&p, "d:step_level/step_height.length.f7"),
        ("8.395".to_string(), false)
    );
    let codes = [
        "through_step_requirement_missing",
        "duplicate_measurement",
        "closed_dimension_chain",
    ];
    assert!(findings(&a, &marks(&p), &codes).is_empty());
    let with = model::plan(&a, &references()).unwrap();
    assert_eq!(shown(&with, leg), ("(33.488)".to_string(), true));
}

/// ADR1-8 (circular-step depths): mfcadpp_holdout 483's `R2 × 18.243 DEEP` step runs down from the
/// top to the 6.495 face level, so that rung is the overall less the step's depth. The rung
/// yields (bracketed with `references`, determined by the overall and the callout); the callout
/// never does. Drawn controlling beside them, lint reports the closed chain.
#[test]
fn h483_a_rung_a_circular_step_depth_closes_is_a_reference() {
    let a = trimmed("h483");
    let rung = "d:step_level/step_height.length.f10";
    let callout = "d:f:circular_blind_step:f10/circular_step_radius.radius";
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(covered_by(&p, rung), "d:envelope/height.length");
    assert_eq!(shown(&p, callout), ("R2 × 18.243 DEEP".to_string(), false));
    let codes = ["duplicate_measurement", "closed_dimension_chain"];
    assert!(findings(&a, &marks(&p), &codes).is_empty());
    let with = model::plan(&a, &references()).unwrap();
    assert_eq!(shown(&with, rung), ("(6.495)".to_string(), true));
    let d = with.dimensions.iter().find(|d| d.id == rung).unwrap();
    assert_eq!(d.determined_by, ["d:envelope/height.length", callout]);
    let mut drawn = marks(&p);
    drawn.push(mark(
        rung,
        "6.495",
        &[("step_level", "step_height.length.f10")],
    ));
    assert_eq!(
        findings(&a, &drawn, &codes),
        ["l:closed_dimension_chain:step_level/step_height.length.f10"]
    );
}

/// ADR1-8 (hex-pocket depths): mfcadpp_holdout 265's `HEX 4.818 A/F × 5.35 DEEP` pocket opens on
/// the base, so its floor is the 5.35 face level: the rung is conveyed by the callout, which
/// takes its claim, and lint finds it stated once.
#[test]
fn h265_a_rung_on_a_hex_pockets_floor_is_conveyed_by_its_callout() {
    let a = trimmed("h265");
    let rung = "d:step_level/step_height.length.f35";
    let callout = "d:f:section_recess:f20/polygon_across_flats.length";
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(covered_by(&p, rung), callout);
    assert_eq!(
        owners(&p, "step_level", "step_height.length.f35"),
        [(callout.to_string(), "HEX 4.818 A/F × 5.35 DEEP".to_string())]
    );
    let codes = ["duplicate_measurement", "closed_dimension_chain"];
    assert!(findings(&a, &marks(&p), &codes).is_empty());
    let mut twice = marks(&p);
    twice.push(mark(
        rung,
        "5.35",
        &[("step_level", "step_height.length.f35")],
    ));
    assert!(
        findings(&a, &twice, &codes)
            .contains(&"l:duplicate_measurement:step_level/step_height.length.f35".to_string())
    );
}

/// ADR1-8 (blind-slot depths): mfcadpp_regressions 11512's `OPEN SLOT 2.458 × 11.672 × 6.674
/// DEEP` opens on the top and its floor is the 8.189 face level, which the overall height less
/// the slot's depth determines: the rung is a reference, and lint agrees.
#[test]
fn r11512_a_rung_a_blind_slot_depth_closes_is_a_reference() {
    let a = trimmed("r11512");
    let rung = "d:step_level/step_height.length.f29";
    let callout = "d:f:section_recess:f18/rectangular_blind_slot_width.length";
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(covered_by(&p, rung), "d:envelope/height.length");
    let codes = ["duplicate_measurement", "closed_dimension_chain"];
    assert!(findings(&a, &marks(&p), &codes).is_empty());
    let with = model::plan(&a, &references()).unwrap();
    let d = with.dimensions.iter().find(|d| d.id == rung).unwrap();
    assert_eq!((d.label.as_str(), d.reference), ("(8.189)", true));
    assert_eq!(d.determined_by, ["d:envelope/height.length", callout]);
}

/// ADR1-8 (hole depths): mfcadpp 1008's ø2.123 boss stands on the flat floor of a ⌀2.895 hole
/// 9.67 deep, up to the face the hole opens on: the boss height and the hole's depth run between
/// the same two planes. The callout states the depth and never yields, so the boss height is
/// conveyed by it (its claim moves to the callout); a toleranced boss height stays controlling.
/// Lint: two different holes' callouts stating equal depths are not a duplicate (hole schedules
/// list each hole's own), but the boss height drawn again beside the callout is.
#[test]
fn m1008_a_boss_height_a_flat_hole_depth_states_is_conveyed_by_the_hole_callout() {
    let a = trimmed("m1008");
    let boss = "d:f:boss:f15/boss_height.length";
    let callout = "d:f:hole:f13/bore.diameter";
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(covered_by(&p, boss), callout);
    assert_eq!(
        owners(&p, "f:boss:f15", "boss_height.length"),
        [(callout.to_string(), "⌀2.895 ↧ 9.67".to_string())]
    );
    let codes = [
        "boss_height_missing",
        "duplicate_measurement",
        "closed_dimension_chain",
    ];
    assert!(findings(&a, &marks(&p), &codes).is_empty());
    let toleranced: Spec =
        serde_json::from_value(json!({"dimensions": {boss: {"tolerance": "±0.1"}}})).unwrap();
    let kept = model::plan(&a, &toleranced).unwrap();
    assert_eq!(shown(&kept, boss), ("9.67 ±0.1".to_string(), false));
    // A second, identical hole beside the first: each callout states its own hole's depth.
    let mut two = a.clone();
    let mut other = two
        .features
        .iter()
        .find(|f| f.id == "f:hole:f13")
        .unwrap()
        .clone();
    other.id = "f:hole:2".into();
    other.parameters["location"][0] = json!(5.0);
    two.features.push(other);
    let callouts = [
        mark(
            "h1",
            "⌀2.895 ↧ 9.67",
            &[
                ("f:hole:f13", "bore.diameter"),
                ("f:hole:f13", "bore.depth"),
            ],
        ),
        mark(
            "h2",
            "⌀2.895 ↧ 9.67",
            &[("f:hole:2", "bore.diameter"), ("f:hole:2", "bore.depth")],
        ),
    ];
    let codes = ["duplicate_measurement", "closed_dimension_chain"];
    assert!(findings(&two, &callouts, &codes).is_empty());
    let mut again = callouts.to_vec();
    again.push(mark(boss, "9.67", &[("f:boss:f15", "boss_height.length")]));
    assert_eq!(
        findings(&two, &again, &codes),
        ["l:duplicate_measurement:f:boss:f15/boss_height.length"]
    );
}
