//! Native default-path regressions for the recess-reader rules.
use draftwright::{
    lint::{
        self,
        section_recesses::{Grammar, grammar},
    },
    model::{self, Analysis, Spec},
    text::Font,
};
use serde_json::json;
use std::{
    io::Read,
    path::{Path, PathBuf},
};

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(name)
}
fn read(name: &str) -> (draftwright::haecceity::Part, Analysis) {
    let path = fixture(name);
    if path.extension().is_some_and(|e| e == "gz") {
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(std::fs::File::open(&path).unwrap())
            .read_to_end(&mut bytes)
            .unwrap();
        model::analyse_native_step(&path, &bytes).unwrap()
    } else {
        model::analyse_native(&path).unwrap()
    }
}
fn font() -> Font {
    Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
        .unwrap()
}

/// ISSUE-885-a: size, depth and both in-plane positions reach the native plan on all axes.
#[test]
fn native_side_pockets_state_size_depth_and_both_locations() {
    for (name, axis) in [("pocket_x", 0), ("pocket_y", 1), ("pocket_z", 2)] {
        let (part, a) = read(&format!("recognition/issues/{name}.step"));
        assert!(
            !a.unrecognised
                .iter()
                .any(|u| u.family == "section_recesses")
        );
        let f = a
            .features
            .iter()
            .find(|f| matches!(grammar(f), Ok(Grammar::Pocket(_))))
            .unwrap();
        let plan = model::plan(&a, &Spec::default()).unwrap();
        let own: Vec<_> = plan
            .dimensions
            .iter()
            .filter(|d| d.measurements.iter().any(|m| m.feature == f.id))
            .collect();
        let callout = own.iter().find(|d| d.kind == "callout").unwrap();
        assert!(callout.label.contains("DEEP"));
        assert!(callout.placed.is_some());
        let locations: Vec<_> = own
            .iter()
            .flat_map(|d| &d.measurements)
            .filter(|m| m.parameter.starts_with("location_pocket."))
            .collect();
        assert_eq!(locations.len(), 2, "{name}: {own:#?}");
        let expected = ["side", "front", "plan"][axis];
        assert!(
            own.iter().all(|d| d.view == format!("v:{expected}")),
            "{name}"
        );
        let drawing = model::render(&part, &a, &plan, &font()).unwrap();
        assert!(
            lint::lint(&a, &drawing)
                .iter()
                .all(|i| !i.code.starts_with("pocket_requirement_")),
            "{name}"
        );
    }
}

/// LINT-pocket_pattern_coverage-requirements, ISSUE-1485-a/c: retain one owner for
/// count, sizes, depth, pitches and absolute location, with no per-member size callouts.
#[test]
fn native_patterns_have_one_grouped_callout_pitches_and_absolute_location() {
    for (name, count, pitches) in [
        ("array.step.gz", 3, 1),
        ("grid.step.gz", 6, 2),
        ("curved-grid.step.gz", 6, 2),
        ("tuner.step", 5, 1),
    ] {
        let (part, a) = read(&format!("recesses/{name}"));
        let f = a
            .features
            .iter()
            .find(|f| f.kind == "pocket_pattern")
            .unwrap_or_else(|| panic!("{name}: no pattern"));
        let plan = model::plan(&a, &Spec::default()).unwrap();
        let dims: Vec<_> = plan
            .dimensions
            .iter()
            .filter(|d| d.measurements.iter().any(|m| m.feature == f.id))
            .collect();
        let callouts: Vec<_> = dims.iter().filter(|d| d.kind == "callout").collect();
        assert_eq!(callouts.len(), 1, "{name}");
        assert!(callouts[0].label.starts_with(&format!("{count}× ")));
        if name.starts_with("curved") {
            assert!(callouts[0].label.contains("DEEP") && !callouts[0].label.contains("MAX"));
        }
        assert_eq!(
            dims.iter()
                .filter(|d| d.id.contains("pitch.length"))
                .count(),
            pitches,
            "{name}"
        );
        assert_eq!(
            dims.iter()
                .filter(|d| d.id.contains("location_pocket_pattern"))
                .count(),
            2,
            "{name}"
        );
        assert!(dims.iter().all(|d| d.placed.is_some()), "{name}: {dims:#?}");
        assert!(
            !plan.dimensions.iter().any(|d| d.kind == "callout"
                && d.id.starts_with("d:f:section_recess:")
                && !d.id.contains("pocket_corner_radius")),
            "{name}"
        );
        let drawing = model::render(&part, &a, &plan, &font()).unwrap();
        let issues = lint::lint(&a, &drawing);
        assert!(
            !issues
                .iter()
                .any(|i| i.code.starts_with("pocket_pattern_requirement_")
                    || i.code.starts_with("pocket_corner_requirement_")),
            "{name}: {issues:#?}"
        );
    }
}

#[test]
fn rounded_pocket_corner_radius_reaches_native_ink_and_lint() {
    let (part, a) = read("recesses/tuner.step");
    let p = a
        .features
        .iter()
        .find(|f| f.kind == "pocket_pattern")
        .unwrap();
    let group = lint::pocket_patterns::read(&a, p).unwrap();
    let radius = group.pocket.corner_radius.unwrap();
    assert!((radius - 3.94).abs() < 1e-6);
    let placed = lint::pockets::placed(&a);
    let mut aliases = Vec::new();
    for (f, p) in &placed {
        let owners = lint::pockets::corner_blends(&a, f, p.as_ref().unwrap());
        assert_eq!(owners.len(), 4);
        aliases.extend(owners.into_iter().map(|f| f.id.clone()));
    }
    aliases.sort();
    aliases.dedup();
    assert_eq!(aliases.len(), 20);
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let radius = plan
        .dimensions
        .iter()
        .find(|d| d.id.contains("pocket_corner_radius"))
        .unwrap();
    assert_eq!(radius.label, "20× R3.94");
    assert_eq!(
        radius
            .measurements
            .iter()
            .filter(|m| m.parameter == "blend.radius")
            .count(),
        20
    );
    assert!(
        !plan.notes.iter().any(|n| n.text.contains("R3.9")),
        "pattern corners have one physical carrier"
    );
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    let mut marks = lint::marks(&drawing);
    assert!(
        lint::blends::coverage(&a, &marks)
            .iter()
            .all(|i| !i.requirements.iter().any(|r| aliases.contains(&r.feature))),
        "{:#?}",
        lint::blends::coverage(&a, &marks)
    );
    assert!(marks.iter().any(|m| {
        m.text.contains("R3.9")
            && m.claims
                .iter()
                .filter(|c| aliases.contains(&c.feature))
                .count()
                == 20
    }));
    marks.retain(|m| !m.claims.iter().any(|c| aliases.contains(&c.feature)));
    let missing: Vec<_> = lint::blends::coverage(&a, &marks)
        .into_iter()
        .filter(|i| i.requirements.iter().any(|r| aliases.contains(&r.feature)))
        .collect();
    assert_eq!(missing.len(), 20);
    assert!(
        missing
            .iter()
            .all(|i| i.code == "blend_requirement_missing")
    );
    // A provider without separate corner blends still retains four physical requirements
    // per published rounded profile, with one counted radius carrier.
    let mut fallback = a.clone();
    fallback.features.retain(|f| !aliases.contains(&f.id));
    let plan = model::plan_recovered(&fallback, &Spec::default()).unwrap();
    let radius = plan
        .dimensions
        .iter()
        .find(|d| d.id.contains("pocket_corner_radius"))
        .unwrap();
    assert_eq!(radius.measurements.len(), 20);
    assert_eq!(radius.label, "20× R3.94");
    let drawing = model::render(&part, &fallback, &plan, &font()).unwrap();
    let mut marks = lint::marks(&drawing);
    assert!(lint::pockets::corner_coverage(&fallback, &marks).is_empty());
    marks.retain(|m| {
        !m.claims
            .iter()
            .any(|c| c.parameter.starts_with("pocket_corner_radius."))
    });
    let missing = lint::pockets::corner_coverage(&fallback, &marks);
    assert_eq!(missing.len(), 20);
    assert!(
        missing
            .iter()
            .all(|i| i.code == "pocket_corner_requirement_missing")
    );
}

#[test]
fn a_size_numeral_cannot_supply_a_pocket_patterns_quantity() {
    let (_, a) = read("recesses/array.step.gz");
    let pattern = a
        .features
        .iter()
        .find(|f| f.kind == "pocket_pattern")
        .unwrap();
    assert_eq!(pattern.parameters["members"].as_array().unwrap().len(), 3);
    let mut mark = lint::Mark {
        name: "pattern-callout".into(),
        view: Some("v:plan".into()),
        text: String::new(),
        claims: vec![draftwright::output::Claim {
            feature: pattern.id.clone(),
            parameter: "grouping.count".into(),
        }],
    };
    for text in ["8 × 3 × 4 DEEP", "2× 3 × 8 × 4 DEEP", "POCKETS"] {
        mark.text = text.into();
        let issues = lint::pocket_patterns::coverage(&a, &[mark.clone()]);
        assert!(
            issues
                .iter()
                .any(|i| i.code == "pocket_pattern_requirement_unverifiable"
                    && i.requirements
                        .iter()
                        .any(|r| r.parameter == "grouping.count")),
            "{text}: {issues:#?}"
        );
    }
    mark.text = "3× 8 × 3 × 4 DEEP".into();
    assert!(
        lint::pocket_patterns::coverage(&a, &[mark])
            .iter()
            .all(|i| i
                .requirements
                .iter()
                .all(|r| r.parameter != "grouping.count"))
    );
}

/// LINT-code-pocket_pattern_requirement_state: missing, unverifiable and authored
/// suppression remain distinguishable and honest.
#[test]
fn pocket_pattern_requirement_states_are_checked_independently() {
    let (part, a) = read("recesses/array.step.gz");
    let issues = lint::pocket_patterns::coverage(&a, &[]);
    assert_eq!(issues.len(), 7);
    assert!(
        issues
            .iter()
            .all(|i| i.code == "pocket_pattern_requirement_missing")
    );
    let mut mark = lint::Mark {
        name: String::new(),
        view: None,
        text: String::new(),
        claims: Vec::new(),
    };
    mark.name = "bad".into();
    mark.text = "POCKETS".into();
    let id = a
        .features
        .iter()
        .find(|f| f.kind == "pocket_pattern")
        .unwrap()
        .id
        .clone();
    mark.claims.push(draftwright::output::Claim {
        feature: id.clone(),
        parameter: "pitch.length".into(),
    });
    let issues = lint::pocket_patterns::coverage(&a, &[mark.clone()]);
    assert!(
        issues
            .iter()
            .any(|i| i.code == "pocket_pattern_requirement_unverifiable")
    );
    mark.name = "suppressed:pitch".into();
    let issues = lint::pocket_patterns::coverage(&a, &[mark]);
    assert!(
        issues
            .iter()
            .any(|i| i.code == "pocket_pattern_requirement_suppressed")
    );
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    let mut marks = lint::marks(&drawing);
    assert!(lint::prismatic::unlocated_pockets(&a, &marks).is_empty());
    marks.retain(|m| {
        !m.claims
            .iter()
            .any(|c| c.feature == id && c.parameter == "pitch.length")
    });
    assert_eq!(lint::prismatic::unlocated_pockets(&a, &marks).len(), 3);
    // The omission survives the rendered drawing boundary too.
    let spec: Spec = serde_json::from_value(
        json!({"dimensions":{format!("d:{id}/pitch.length"): {"omit":true}}}),
    )
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    assert!(
        lint::lint(&a, &drawing)
            .iter()
            .any(|i| i.code == "pocket_pattern_requirement_suppressed")
    );
}

/// ISSUE-1245-a, ISSUE-1246-a, ARCH0015-1 and ISSUE-917-a now run on the
/// recogniser the CLI actually uses: unsupported passages/pockets are reported,
/// channels own their width, and every accepted grammar can supply dimensions.
#[test]
fn native_recess_grammars_and_unsupported_dispositions_are_preserved() {
    for name in [
        "bounded_pocket",
        "u_channel",
        "u_channel_asymmetric",
        "curved_centered",
    ] {
        let (_, a) = read(&format!("recognition/issues/{name}.step"));
        let plan = model::plan(&a, &Spec::default()).unwrap();
        assert!(
            a.features.iter().any(|f| f.kind == "section_recess"),
            "{name}"
        );
        assert!(
            plan.dimensions.iter().any(|d| d
                .measurements
                .iter()
                .any(|m| m.feature.starts_with("f:section_recess:"))),
            "{name}"
        );
        if name.starts_with("u_channel") {
            assert_eq!(
                plan.dimensions
                    .iter()
                    .flat_map(|d| &d.measurements)
                    .filter(|m| m.parameter == "channel_width.length")
                    .count(),
                1
            );
        }
    }
}

#[path = "common/corpus.rs"]
mod corpus;

#[test]
fn native_passages_pockets_and_source_refusals_reach_lint() {
    let Some(root) = corpus::dir() else { return };
    let (_, a) = model::analyse_native(&root.join("mfcadpp/1000.step")).unwrap();
    let recognition = a.recognition.as_ref().unwrap();
    for records in [&recognition.evidence, &recognition.refusals] {
        assert!(
            records
                .windows(2)
                .all(|p| (&p[0].family, &p[0].subtype, &p[0].faces)
                    <= (&p[1].family, &p[1].subtype, &p[1].faces))
        );
    }
    let found = lint::section_recesses::coverage(&a, &[]);
    assert!(
        found
            .iter()
            .any(|i| i.code == "passage_requirement_unsupported")
    );
    assert!(
        a.recognition
            .as_ref()
            .unwrap()
            .refusals
            .iter()
            .any(|r| r.subtype == "SectionRecessRefusal")
    );
    assert!(
        found
            .iter()
            .any(|i| i.code == "section_recess_recognition_refused")
    );
    assert!(
        a.recognition
            .as_ref()
            .unwrap()
            .evidence
            .iter()
            .any(|r| r.family == "step_levels")
    );
    let (_, a) = model::analyse_native(&root.join("inventory_refusal/14052.step.gz")).unwrap();
    assert!(
        lint::section_recesses::coverage(&a, &[])
            .iter()
            .any(|i| i.code == "prismatic_pocket_requirement_unsupported")
    );
}

#[test]
fn native_reader_refuses_bad_recess_fields_local_coordinates_and_pattern_links() {
    use draftwright::bridge::native;
    let (part, _) = read("recesses/array.step.gz");
    let rec = quiddity::correspondence::recognise(&part);
    let doc = quiddity::recognition::document(&rec, part.faces.len());
    for field in ["index", "body", "geometry", "classification", "evidence"] {
        let mut bad = doc.clone();
        bad.records
            .iter_mut()
            .find(|r| r.record_type == "SectionRecess")
            .unwrap()
            .parameters
            .remove(field);
        assert!(native::read(&part, &bad).is_err(), "{field}");
    }
    let mut bad = doc.clone();
    bad.records
        .iter_mut()
        .find(|r| r.record_type == "SectionRecess")
        .unwrap()
        .parameters
        .insert("origin".into(), json!([1.0, 2.0, 3.0]));
    assert!(native::read(&part, &bad).is_err());
    for members in [json!([0, 0, 1]), json!([0, 1, 9999])] {
        let mut bad = doc.clone();
        bad.records
            .iter_mut()
            .find(|r| r.family == "section_recess_patterns")
            .unwrap()
            .parameters
            .insert("members".into(), members);
        assert!(native::read(&part, &bad).is_err());
    }
}

#[test]
fn a_grid_with_a_repeated_position_cannot_claim_a_complete_lattice() {
    let (_, mut a) = read("recesses/grid.step.gz");
    let pattern = a
        .features
        .iter()
        .find(|f| f.kind == "pocket_pattern")
        .unwrap()
        .clone();
    assert!(lint::pocket_patterns::read(&a, &pattern).is_ok());
    let ids = pattern.parameters["members"].as_array().unwrap();
    let first = a
        .features
        .iter()
        .find(|f| Some(f.id.as_str()) == ids[0].as_str())
        .unwrap()
        .parameters["geometry"]
        .clone();
    let last = a
        .features
        .iter_mut()
        .find(|f| Some(f.id.as_str()) == ids.last().unwrap().as_str())
        .unwrap();
    last.parameters.insert("geometry".into(), first);
    assert!(lint::pocket_patterns::read(&a, &pattern).is_err());
    let issues = lint::pocket_patterns::coverage(&a, &[]);
    assert_eq!(issues.len(), 1);
    assert_eq!(issues[0].code, "pocket_pattern_requirement_unverifiable");
}

#[test]
fn python_and_native_tuner_patterns_keep_the_same_members_and_measurements() {
    let (part, native) = read("recesses/tuner.step");
    let mut text = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(fixture("recesses/tuner.json.gz")).unwrap())
        .read_to_string(&mut text)
        .unwrap();
    let mut bridge = native.clone();
    model::recognise(&part, &mut bridge, &text).unwrap();
    let pattern = |a: &Analysis| {
        a.features
            .iter()
            .find(|f| f.kind == "pocket_pattern")
            .unwrap()
            .clone()
    };
    let (p, q) = (pattern(&native), pattern(&bridge));
    let world_direction = |a: &Analysis, f: &draftwright::bridge::Feature| {
        draftwright::bridge::holes::dir_to_part(
            &draftwright::bridge::holes::frame(a),
            draftwright::bridge::holes::point(f.parameters.get("direction")).unwrap(),
        )
    };
    let (u, v) = (world_direction(&native, &p), world_direction(&bridge, &q));
    let mut expected = p.parameters["members"].as_array().unwrap().clone();
    if u.iter().zip(v).map(|(x, y)| x * y).sum::<f64>() < 0.0 {
        expected.reverse();
    }
    assert_eq!(json!(expected), q.parameters["members"]);
    assert_eq!(p.faces, q.faces);
    let (p, q) = (
        lint::pocket_patterns::read(&native, &p).unwrap(),
        lint::pocket_patterns::read(&bridge, &q).unwrap(),
    );
    assert_eq!(p.members.len(), 5);
    assert_eq!(q.members.len(), 5);
    for (x, y) in [
        (p.pocket.width, q.pocket.width),
        (p.pocket.length, q.pocket.length),
        (p.pocket.depth, q.pocket.depth),
    ] {
        assert!((x - y).abs() < 1e-3, "{x} != {y}");
    }
    for a in [&native, &bridge] {
        let plan = model::plan(a, &Spec::default()).unwrap();
        let group = plan
            .dimensions
            .iter()
            .find(|d| d.id.starts_with("d:f:pocket_pattern:") && d.kind == "callout")
            .unwrap();
        assert_eq!(group.label, "5× 7.9 × 13.6 × 19 DEEP");
    }
}

#[test]
fn a_recess_location_on_the_far_datum_uses_the_overall_span_once() {
    let Some(root) = corpus::dir() else { return };
    let (_, a) = model::analyse_native(&root.join("mfcadpp/1000.step")).unwrap();
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let owners: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "seat_location.location.x")
        })
        .collect();
    assert_eq!(owners.len(), 1);
    assert_eq!(owners[0].id, "d:envelope/width.length");
    let covered = plan
        .omitted
        .iter()
        .find(|o| o.id.ends_with("/seat_location.location.x"))
        .unwrap();
    assert!(
        matches!(&covered.state,draftwright::items::State::Covered {by} if by=="d:envelope/width.length")
    );
    let mut spec = Spec::default();
    spec.dimensions.insert(
        "d:envelope/width.length".into(),
        model::DimensionSpec {
            omit: true,
            ..Default::default()
        },
    );
    let promoted = model::plan(&a, &spec).unwrap();
    assert!(
        promoted
            .dimensions
            .iter()
            .any(|d| d.id == covered.id && d.placed.is_some())
    );
}

#[test]
fn native_blind_slots_use_the_existing_approved_recess_callout_grammars() {
    let Some(root) = corpus::dir() else { return };
    for (file, labels) in [
        (
            "mfcadpp/10047.step",
            vec![
                "ROUND-BOTTOM OPEN SLOT 1\nBOTTOM FLAT × R0.541 × 2.42\nLONG",
                "ROUND-BOTTOM OPEN SLOT 1\nBOTTOM FLAT × R4.396 × 4.662\nLONG",
            ],
        ),
        (
            "mfcadpp/10245.step",
            vec!["OPEN SLOT 3.344 × 9.15 ×\n2.049 DEEP"],
        ),
        (
            "mfcadpp_regressions/11512.step",
            vec!["OPEN SLOT 2.458 × 11.672 ×\n6.674 DEEP"],
        ),
    ] {
        let (_, a) = model::analyse_native(&root.join(file)).unwrap();
        let plan = model::plan(&a, &Spec::default()).unwrap();
        for label in labels {
            let d = plan
                .dimensions
                .iter()
                .find(|d| d.label == label)
                .unwrap_or_else(|| panic!("{file}: {label}"));
            assert!(d.placed.is_some());
            assert!(
                d.measurements
                    .iter()
                    .all(|m| m.feature.starts_with("f:section_recess:"))
            );
        }
    }
}

/// Equal channel sizes share counted ink while every recognised width stays editable.
#[test]
fn native_equal_channel_widths_keep_all_claims_and_promote_an_omitted_carrier() {
    let Some(root) = corpus::dir() else { return };
    let (_, a) = model::analyse_native(&root.join("nist/nist_ftc_06_asme1_rd.stp")).unwrap();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let widths = |p: &model::Plan| {
        p.dimensions
            .iter()
            .flat_map(|d| &d.measurements)
            .filter(|m| m.parameter == "channel_width.length")
            .map(|m| (m.feature.clone(), m.value))
            .collect::<Vec<_>>()
    };
    let mut claims = widths(&p);
    claims.sort_by(|a, b| a.0.cmp(&b.0));
    assert_eq!(claims.len(), 4);
    let ds: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "channel_width.length")
        })
        .collect();
    assert_eq!(ds.len(), 2);
    assert!(
        ds.iter()
            .all(|d| d.label.starts_with("2× ") && d.placed.is_some())
    );
    let carrier = ds[0];
    let other = carrier
        .measurements
        .iter()
        .find(|m| m.feature != carrier.measurements[0].feature)
        .unwrap();
    let mut spec = Spec::default();
    spec.dimensions.insert(
        carrier.id.clone(),
        model::DimensionSpec {
            omit: true,
            ..Default::default()
        },
    );
    let edited = model::plan_recovered(&a, &spec).unwrap();
    assert!(widths(&edited).iter().any(|m| m.0 == other.feature));
    assert!(
        edited.dimensions.iter().any(
            |d| d.measurements.iter().any(|m| m.feature == other.feature) && d.placed.is_some()
        )
    );
}

/// A width label and an existing ramp leader must be repaired together. Moving
/// the leader alone cleared the label but added two real hidden-edge crossings.
#[test]
fn recess_locations_do_not_send_an_existing_ramp_leader_through_extra_hidden_edges() {
    let Some(root) = corpus::dir() else { return };
    let (part, a) = model::analyse_native(&root.join("mfcadpp/10060.step")).unwrap();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let mut earlier = a.clone();
    earlier.features.retain(|f| f.kind != "section_recess");
    let before = model::plan_recovered(&earlier, &Spec::default()).unwrap();
    let id = "d:f:paired_ramp_step:f2/ramp_angle.angle";
    let crossings = |a: &Analysis, p: &model::Plan| {
        use draftwright::output::Item;
        let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
        let lines = &d.placed.as_ref().unwrap().lines;
        let drawing = model::render(&part, a, p, &font()).unwrap();
        let cross = |s: [[f64; 2]; 2], t: [[f64; 2]; 2]| {
            let side = |a: [f64; 2], b: [f64; 2], c: [f64; 2]| {
                (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            };
            side(s[0], s[1], t[0]) * side(s[0], s[1], t[1]) < -1e-12
                && side(t[0], t[1], s[0]) * side(t[0], t[1], s[1]) < -1e-12
        };
        drawing
            .layers
            .iter()
            .filter(|l| l.name == "hidden")
            .flat_map(|l| &l.items)
            .filter_map(|i| match i {
                Item::Edge(c) => Some(c),
                Item::Face(_) => None,
            })
            .filter(|c| {
                if let draftwright::output::Curve::Line(a, b) = c {
                    return lines.iter().any(|s| cross(*s, [*a, *b]));
                }
                let b = c.bbox();
                let n = ((b[2] - b[0]).hypot(b[3] - b[1]) / 0.002).ceil().max(1.0) as usize;
                (0..n).any(|i| {
                    let edge = [c.at(i as f64 / n as f64), c.at((i + 1) as f64 / n as f64)];
                    lines.iter().any(|s| cross(*s, edge))
                })
            })
            .count()
    };
    let old = crossings(&earlier, &before);
    assert!(
        old > 0,
        "the established route has a real hidden-edge crossing"
    );
    assert!(
        crossings(&a, &p) <= old,
        "new recess ink displaced the existing route"
    );
    let claims: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .flat_map(|d| &d.measurements)
        .filter(|m| m.feature == "f:section_recess:f18")
        .collect();
    for parameter in [
        "pocket_width.length",
        "pocket_length.length",
        "pocket_depth.length",
        "location_pocket.y",
        "location_pocket.z",
    ] {
        assert!(
            claims.iter().any(|m| m.parameter == parameter),
            "lost {parameter}"
        );
    }
}

/// A short new form leader must not strand its location witness across a hole.
/// Both statements participate in the joint corridor solve, with every seat claim retained.
#[test]
fn native_seat_locations_share_the_joint_solve_with_their_form_leader() {
    let Some(root) = corpus::dir() else { return };
    let (_, a) = model::analyse_native(&root.join("nist/nist_ftc_09_asme1_rd.stp")).unwrap();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let location = "d:f:section_recess:f103/seat_location.location.x";
    let d = p.dimensions.iter().find(|d| d.id == location).unwrap();
    assert!(d.placed.is_some());
    assert!((d.value - 39.6).abs() < 1e-9);
    assert!(
        !p.problems
            .iter()
            .any(|q| q.code == "extension_through_feature"
                && q.subjects.first().is_some_and(|id| id == location)),
        "{:?}",
        p.problems
    );
    let claims: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .flat_map(|d| &d.measurements)
        .filter(|m| m.feature == "f:section_recess:f103")
        .collect();
    for parameter in [
        "seat_diameter.diameter",
        "seat_run.length",
        "seat_sweep.angle",
        "seat_location.location.x",
        "seat_location.location.y",
        "seat_location.location.z",
    ] {
        assert!(
            claims.iter().any(|m| m.parameter == parameter),
            "lost {parameter}"
        );
    }
}

/// #233: quiddity's annular grooves, V channels and semicircular blind slots are read, no longer
/// unrecognised, and each is stated: by its own items, or by the hole and boss on a groove's
/// walls and the profile V-notch on a channel's flanks.
#[test]
fn quiddity_recess_families_are_read_and_stated() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let families = [
        ("annular_grooves", "annular_groove"),
        ("v_channels", "v_channel"),
        ("semicircular_blind_slots", "semicircular_blind_slot"),
    ];
    for (file, counts) in [
        ("nist/nist_ctc_01_asme1_rd.stp", [0, 2, 0]),
        ("mfcadpp/1008.step", [1, 0, 0]),
        ("mfcadpp/10092.step", [0, 0, 1]),
        ("mfcadpp/10098.step", [1, 1, 0]),
    ] {
        let (_, a) = model::analyse_native(&dir.join(file)).unwrap();
        assert!(
            a.unrecognised
                .iter()
                .all(|u| families.iter().all(|(family, _)| u.family != *family)),
            "{file}"
        );
        for ((_, kind), n) in families.iter().zip(counts) {
            let found = a.features.iter().filter(|f| f.kind == *kind).count();
            assert_eq!(found, n, "{file}: {kind}");
        }
        let plan = model::plan(&a, &Spec::default()).unwrap();
        let ours = |f: &draftwright::bridge::Feature| {
            plan.dimensions
                .iter()
                .filter(|d| d.placed.is_some() && d.measurements.iter().any(|m| m.feature == f.id))
                .collect::<Vec<_>>()
        };
        for f in a
            .features
            .iter()
            .filter(|f| families.iter().any(|(_, kind)| f.kind == *kind))
        {
            assert!(
                plan.problems.iter().all(|p| !p.subjects.contains(&f.id)),
                "{file}: {} is reported",
                f.id
            );
            match (file, f.kind.as_str()) {
                // Their walls' hole and boss, and the profile V-notches, state them.
                (_, "annular_groove") | ("nist/nist_ctc_01_asme1_rd.stp", "v_channel") => {
                    assert!(ours(f).is_empty(), "{file}: {}", f.id);
                }
                (_, "semicircular_blind_slot") => {
                    let own = ours(f);
                    let callout = own.iter().find(|d| d.kind == "callout").unwrap();
                    assert!(callout.label.starts_with("ROUND-BOTTOM OPEN SLOT"));
                    let width = &callout.measurements[0];
                    assert_eq!(width.parameter, "semicircular_blind_slot_width.length");
                    let radius = f.parameters["radius"].as_f64().unwrap();
                    assert!((width.value - 2.0 * radius).abs() < 1e-9);
                }
                (_, "v_channel") => {
                    let own = ours(f);
                    let callout = own.iter().find(|d| d.kind == "callout").unwrap();
                    assert!(callout.label.contains("° V ×"), "{}", callout.label);
                    assert_eq!(
                        own.iter()
                            .filter(|d| d.measurements[0].parameter.starts_with("location"))
                            .count(),
                        2,
                        "an oblique opening locates the trough on both of the view's axes"
                    );
                }
                _ => {}
            }
        }
    }
}

/// quiddity-rust#127: every residual cylinder retains its true radius. CTC-05's
/// four shallow outer profiles count once, with ordinary Spec text steering.
#[test]
fn ctc05_cylindrical_profiles_are_counted_radii_with_spec_overrides() {
    let (_, a) = read("rule_pins/nist_ctc_05_asme1_ap242.stp.gz");
    let profiles: Vec<_> = a
        .features
        .iter()
        .filter(|f| f.kind == "cylindrical_profile")
        .collect();
    assert_eq!(profiles.len(), 7);
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    for f in &profiles {
        let drawn: Vec<_> = p
            .dimensions
            .iter()
            .filter(|d| {
                d.placed.is_some()
                    && d.measurements
                        .iter()
                        .any(|m| m.feature == f.id && m.parameter == "cylindrical_profile.radius")
            })
            .collect();
        assert_eq!(
            drawn.len(),
            1,
            "{}: its radius must reach the drawing once",
            f.id
        );
        assert!(drawn[0].label.contains('R'));
    }
    let outer = p
        .dimensions
        .iter()
        .find(|d| d.label == "4× R203.2")
        .unwrap();
    assert_eq!(
        outer
            .measurements
            .iter()
            .filter(|m| m.parameter == "cylindrical_profile.radius")
            .count(),
        4
    );
    let spec =
        serde_json::from_value(json!({"dimensions":{&outer.id:{"text":{"suffix":" PROFILE"}}}}))
            .unwrap();
    let steered = model::plan_recovered(&a, &spec).unwrap();
    let edited = steered
        .dimensions
        .iter()
        .find(|d| d.id == outer.id)
        .unwrap();
    assert!(edited.label.ends_with(" PROFILE"), "{}", edited.label);
    assert_eq!(edited.measurements, outer.measurements);
}

#[test]
fn curved_pattern_depth_presentations_are_checked_against_every_member() {
    let (part, a) = read("recesses/curved-grid.step.gz");
    let f = a
        .features
        .iter()
        .find(|f| f.kind == "pocket_pattern")
        .unwrap();
    let id = format!("d:{}/pocket_width.length", f.id);
    for choice in ["tangent_depth", "far_extreme"] {
        let spec: Spec =
            serde_json::from_value(serde_json::json!({"dimensions":{&id:{"presentation":choice}}}))
                .unwrap();
        let p = model::plan(&a, &spec).unwrap();
        let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
        assert!(d.from_spec.iter().any(|s| s == "presentation"), "{d:?}");
        let drawing = model::render(&part, &a, &p, &font()).unwrap();
        let marks = lint::marks(&drawing);
        let found = lint::pocket_patterns::coverage(&a, &marks);
        assert!(
            !found.iter().any(|i| i
                .requirements
                .iter()
                .any(|r| r.parameter == "pocket_max_depth.length")),
            "{choice}: {found:?}"
        );
        let mut wrong = marks.clone();
        for mark in &mut wrong {
            if mark.claims.iter().any(|c| {
                c.feature == f.id
                    && matches!(
                        c.parameter.as_str(),
                        "pocket_depth_from_tangent.length" | "pocket_floor_from_far_extreme.length"
                    )
            }) {
                mark.text = "6× 999 × 999 × 999 DEEP".into();
            }
        }
        assert!(lint::pocket_patterns::coverage(&a, &wrong).iter().any(|i| {
            i.requirements
                .iter()
                .any(|r| r.parameter == "pocket_max_depth.length")
        }));
    }
}
