use draftwright::lint::{self, Mark, Severity};
#[path = "common/support.rs"]
#[macro_use]
mod support;

use draftwright::model::{self, Analysis, Spec};
use draftwright::output::Claim;
use serde_json::{Value, json};

fn feature(kind: &str, i: usize, parameters: Value) -> Value {
    json!({"id":format!("f:{kind}:{i}"),"kind":kind,"faces":[i],"parameters":parameters,
        "fingerprint":{"version":"test","family":kind,"subtype":"test","sizes":{},
            "position":[0,0,0],"axis":[0,0,1],"neighbourhood":[]}})
}

fn analysis(features: Vec<Value>) -> Analysis {
    serde_json::from_value(json!({"schema":model::ANALYSIS_SCHEMA,
        "binding":{"source_sha256":"","part":"test","face_count":12,"loader":"test"},
        "envelope":[[0,0,0],[40,30,10]],"iso_bounds":[-20,-20,20,20],"features":features}))
    .unwrap()
}

fn mark(feature: &str, parameter: &str, text: &str) -> Mark {
    Mark {
        precision: None,
        units: Default::default(),
        name: format!("{feature}/{parameter}"),
        view: Some("v:plan".into()),
        text: text.into(),
        claims: vec![Claim {
            feature: feature.into(),
            parameter: parameter.into(),
        }],
    }
}

#[test]
fn unclaimed_faces_and_unplanned_features_need_attention() {
    let a = analysis(vec![
        feature("unported", 1, json!({})),
        feature("unported", 2, json!({})),
    ]);
    let p = model::plan(&a, &Spec::default()).unwrap();
    let doc = serde_json::to_value(&p).unwrap();
    assert_eq!(doc["status"], "needs_attention");
    assert_eq!(doc["completeness"]["claimed_faces"], 2);
    assert_eq!(doc["completeness"]["faces"], 12);
    assert_eq!(doc["completeness"]["unplanned_features"], 2);
    assert!(p.problems.iter().any(|p| p.code == "unclaimed_geometry"
        && p.severity == "info"
        && p.message.contains("2 of 12")));
    assert!(
        p.problems
            .iter()
            .filter(|p| p.code == "dimensions_not_ported")
            .all(|p| p.severity == "warning")
    );
}

#[test]
fn multi_solid_completeness_findings_are_info() {
    let mut doc = serde_json::to_value(analysis(vec![feature("unported", 1, json!({}))])).unwrap();
    doc["solid_count"] = json!(2);
    let a = serde_json::from_value(doc).unwrap();
    // A stated general tolerance keeps the title block's own warning out of the status.
    let spec: Spec =
        serde_json::from_value(json!({ "sheet": { "title": { "tolerance": "ISO 2768-m" } } }))
            .unwrap();
    let p = model::plan(&a, &spec).unwrap();
    assert_eq!(serde_json::to_value(&p).unwrap()["status"], "clear");
    let issues = lint::check(&a, &[]);
    let extents: Vec<_> = issues
        .iter()
        .filter(|i| i.code == "missing_principal_dimension")
        .collect();
    assert_eq!(extents.len(), 3);
    assert!(extents.iter().all(|i| i.severity == Severity::Info));
    for code in ["unclaimed_geometry", "dimensions_not_ported"] {
        let found: Vec<_> = p.problems.iter().filter(|p| p.code == code).collect();
        assert!(!found.is_empty());
        assert!(found.iter().all(|p| p.severity == "info"));
    }
}

#[test]
fn omitted_overall_extents_are_lint_findings() {
    let a = analysis(vec![]);
    let issues = lint::check(&a, &[mark("envelope", "depth.length", "30")]);
    let missing: Vec<_> = issues
        .iter()
        .filter(|i| i.code == "missing_principal_dimension")
        .collect();
    assert_eq!(missing.len(), 2);
    assert!(missing.iter().all(|i| i.severity == Severity::Warning));
    assert_eq!(missing[0].requirements[0].parameter, "width.length");
    assert_eq!(missing[1].requirements[0].parameter, "height.length");
}

#[test]
fn unstated_turned_band_length_and_diameter_are_lint_findings() {
    let a = analysis(vec![feature(
        "turned_step",
        1,
        json!({"axis":"z","lo":0,"hi":10,
        "diameter":20,"profile":{"axis":"z","axis_origin":[0,0,0]}}),
    )]);
    let issues = lint::check(&a, &[]);
    assert!(
        issues
            .iter()
            .any(|i| i.code == "axial_length_missing" && i.subjects == ["f:turned_step:1"])
    );
    assert!(
        issues
            .iter()
            .any(|i| i.code == "turned_step_requirement_missing"
                && i.requirements[0].parameter == "step.diameter")
    );
}

fn grid() -> Analysis {
    let mut a = analysis(
        [(10., 10.), (20., 10.), (10., 20.), (20., 20.)]
            .iter()
            .enumerate()
            .map(|(i, (x, y))| {
                feature(
                    "hole",
                    i + 1,
                    json!({"axis":[0,0,1],"location":[x,y,10],
            "diameter":4,"depth":10,"bottom":"through"}),
                )
            })
            .collect(),
    );
    a.recognition = Some(
        serde_json::from_value(json!({"recogniser":"test",
        "frame":[[0,0,0],[1,0,0],[0,1,0],[0,0,1]],"patterns":[{"id":"f:hole_pattern:1",
        "arrangement":"grid","members":["f:hole:1","f:hole:2","f:hole:3","f:hole:4"],
        "faces":[1,2,3,4],"parameters":{}}]}))
        .unwrap(),
    );
    a
}

#[test]
fn hole_pattern_missing_location_is_reported_per_axis() {
    let a = grid();
    let issues = lint::check(&a, &[mark("f:hole_pattern:1", "location.length.x", "15")]);
    assert!(
        issues
            .iter()
            .any(|i| i.code == "hole_pattern_requirement_missing"
                && i.requirements[0].parameter == "location.length.y")
    );
    assert!(
        !issues
            .iter()
            .any(|i| i.code == "hole_pattern_requirement_missing"
                && i.requirements[0].parameter == "location.length.x")
    );
}

#[test]
fn hole_pattern_pitch_needs_truthful_printed_evidence() {
    let a = grid();
    for text in ["", "9"] {
        let issues = lint::check(
            &a,
            &[
                mark("f:hole_pattern:1", "grid_pitch.length.col", text),
                mark("f:hole_pattern:1", "grid_pitch.length.row", "10"),
            ],
        );
        assert!(
            issues
                .iter()
                .any(|i| i.code.starts_with("hole_pattern_requirement_")
                    && i.requirements[0].parameter == "grid_pitch.length.col")
        );
        assert!(
            !issues
                .iter()
                .any(|i| i.code.starts_with("hole_pattern_requirement_")
                    && i.requirements[0].parameter == "grid_pitch.length.row")
        );
    }
}

#[test]
fn formatting_rounds_negative_zero_without_a_sign() {
    // Faithful default nominals retain small negative values; only rounded zero loses its sign.
    for value in [-0.0001, -0.0] {
        assert!(!draftwright::dims::fmt(value).starts_with('-'));
    }
    assert_eq!(draftwright::dims::fmt(-0.04), "-0.04");
    assert_eq!(draftwright::dims::fmt(-0.06), "-0.06");
}

#[test]
fn inspect_summary_carries_the_plans_status_and_completeness() {
    let root = std::env::temp_dir().join(format!("draftwright-g1-summary-{}", std::process::id()));
    let cache = draftwright::stateless::Cache::new(root.clone());
    let step = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mounting_plate.step");
    let part_ref = cache.register(&step, None).unwrap();
    let summary = cache.summary(&part_ref, &Spec::default()).unwrap();
    assert!(matches!(
        summary["status"].as_str(),
        Some("clear" | "needs_attention")
    ));
    assert_eq!(summary["completeness"]["faces"], summary["face_count"]);
    assert!(summary["completeness"]["claimed_faces"].is_u64());
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn wholly_omitted_supported_feature_needs_attention() {
    let a = analysis(vec![feature(
        "turned_step",
        1,
        json!({"axis":"z","lo":0,"hi":10,
            "diameter":20,"profile":{"axis":"z","axis_origin":[0,0,0]}}),
    )]);
    let spec = serde_json::from_value(json!({"dimensions": {
        "d:f:turned_step:1/step.length": {"omit":true},
        "d:f:turned_step:1/step.diameter": {"omit":true}
    }}))
    .unwrap();
    let p = model::plan(&a, &spec).unwrap();
    assert_eq!(
        serde_json::to_value(&p).unwrap()["completeness"]["unplanned_features"],
        1
    );
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "feature_not_dimensioned"
                && p.severity == "warning"
                && p.subjects == ["f:turned_step:1"])
    );
}

fn member_locations(a: &Analysis) -> Vec<Mark> {
    a.features
        .iter()
        .flat_map(|f| {
            ["x", "y"].into_iter().enumerate().map(move |(k, axis)| {
                mark(
                    &f.id,
                    &format!("location.length.{axis}"),
                    &draftwright::dims::fmt(f.parameters["location"][k].as_f64().unwrap()),
                )
            })
        })
        .collect()
}

#[test]
fn member_ordinates_prove_pattern_location_and_spacing() {
    let a = grid();
    let marks = member_locations(&a);
    assert!(draftwright::lint::hole_patterns::coverage(&a, &marks).is_empty());
    for missing in 0..marks.len() {
        let mut incomplete = marks.clone();
        incomplete[missing].text = "999".into();
        let issues = draftwright::lint::hole_patterns::coverage(&a, &incomplete);
        assert!(
            issues
                .iter()
                .any(|i| i.requirements[0].parameter.starts_with("grid_pitch."))
        );
        assert!(
            issues
                .iter()
                .any(|i| i.requirements[0].parameter == marks[missing].claims[0].parameter)
        );
    }
}

#[test]
fn one_member_anchor_and_printed_pitches_locate_a_grid() {
    let a = grid();
    let mut marks = member_locations(&a)[..2].to_vec();
    marks.extend([
        mark("f:hole_pattern:1", "grid_pitch.length.col", "10"),
        mark("f:hole_pattern:1", "grid_pitch.length.row", "10"),
    ]);
    assert!(draftwright::lint::hole_patterns::coverage(&a, &marks).is_empty());
    marks[1].claims[0].feature = "f:hole:2".into();
    assert!(!draftwright::lint::hole_patterns::coverage(&a, &marks).is_empty());
}

#[path = "common/corpus.rs"]
mod corpus;

part_cases!(nist_member_ordinates_leave_no_false_pattern_findings;
    nist_ctc_02_asme1_rc => "nist_ctc_02_asme1_rc",
    nist_ctc_05_asme1_rd => "nist_ctc_05_asme1_rd",
);
fn nist_member_ordinates_leave_no_false_pattern_findings(name: &str) {
    let Some(dir) = corpus::dir() else { return };
    let font = draftwright::text::Font::new(
        std::fs::read(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"),
        )
        .unwrap(),
    )
    .unwrap();
    let (part, a) = model::analyse_native(&dir.join(format!("nist/{name}.stp"))).unwrap();
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let issues = lint::hole_patterns::coverage(&a, &lint::marks(&drawing));
    assert!(issues.is_empty(), "{name}: {issues:?}");
}

#[test]
fn member_ordinates_also_prove_linear_pattern_spacing() {
    let mut a = grid();
    a.features.truncate(2);
    let p = &mut a.recognition.as_mut().unwrap().patterns[0];
    p.arrangement = "linear".into();
    p.members.truncate(2);
    p.faces.truncate(2);
    p.parameters.insert("pitch".into(), json!(10));
    let mut marks = member_locations(&a);
    assert!(lint::hole_patterns::coverage(&a, &marks).is_empty());
    marks.truncate(2);
    marks.push(mark("f:hole_pattern:1", "pitch.length", "10"));
    assert!(lint::hole_patterns::coverage(&a, &marks).is_empty());
    marks.last_mut().unwrap().text = "9".into();
    assert!(
        lint::hole_patterns::coverage(&a, &marks)
            .iter()
            .any(|i| i.requirements[0].parameter == "pitch.length")
    );
}

#[test]
fn member_ordinates_prove_a_grid_without_an_axis_aligned_lattice() {
    let mut a = grid();
    for (f, (x, y)) in a
        .features
        .iter_mut()
        .zip([(10., 10.), (20., 12.), (8., 20.), (18., 22.)])
    {
        f.parameters.insert("location".into(), json!([x, y, 10]));
    }
    let marks = member_locations(&a);
    assert!(lint::hole_patterns::coverage(&a, &marks).is_empty());
    let incomplete = &marks[..marks.len() - 1];
    assert!(
        lint::hole_patterns::coverage(&a, incomplete)
            .iter()
            .any(|i| i.code == "hole_pattern_requirement_unverifiable")
    );
}

/// A GA keeps exactly the same coverage evidence, but every feature family's missing or
/// unverifiable requirement is informational. This exercises both the generic backstop and
/// families that historically built their own Warning issues.
#[test]
fn assembly_severity_reaches_all_feature_coverage_families() {
    let a = analysis(vec![
        feature(
            "hole",
            1,
            json!({"axis":[0,0,1],"location":[12,12,10],
            "diameter":4,"depth":10,"bottom":"through"}),
        ),
        feature(
            "boss",
            2,
            json!({"axis":[0,0,1],"location":[15,15,10],
            "diameter":6,"height":4}),
        ),
        feature("fillet", 3, json!({"radius":3})),
        feature("chamfer", 4, json!({"distance":1})),
        feature("oriented_slot", 5, json!({})),
        feature("pad", 6, json!({})),
        feature("double_d_bore", 7, json!({})),
    ]);
    let strict = lint::check(&a, &[]);
    for code in [
        "feature_not_dimensioned",
        "hole_requirement_missing",
        "boss_height_missing",
        "fillet_requirement_missing",
        "oriented_slot_requirement_unverifiable",
        "pad_requirement_unverifiable",
        "profiled_bore_not_dimensioned",
    ] {
        assert!(
            strict.iter().any(|i| i.code == code),
            "missing exercised family {code}: {strict:?}"
        );
    }
    for solids in [None, Some(1), Some(2), Some(4)] {
        let mut a = a.clone();
        a.solid_count = solids;
        let ga = lint::check(&a, &[]);
        assert_eq!(ga.len(), strict.len());
        for (mut expected, got) in strict.iter().cloned().zip(ga) {
            if solids.is_some_and(|n| n > 1) && expected.severity == Severity::Warning {
                expected.severity = Severity::Info;
            }
            assert_eq!(got, expected, "{solids:?}");
        }
    }
}

/// ADR3-8: exact occurrences, including repeated occurrences in a deferred family, survive
/// as explicit, typed policy. Adding a provider subtype must not silently invent a reason.
#[test]
fn ownerless_occurrences_have_closed_policy_reasons() {
    use draftwright::dims::{Disposition, DispositionReason, FeatureDisposition};
    let subtypes = [
        "CircularFacePattern",
        "CounterSink",
        "FreeformSurface",
        "InteriorVoid",
        "ObliqueThroughStep",
        "OrientedSlotArray",
        "OrientedSlotGrid",
        "SheetMetalBody",
        "ThinWallBody",
        "InteriorVoid",
    ];
    let mut fs: Vec<_> = subtypes
        .iter()
        .enumerate()
        .map(|(i, subtype)| {
            let mut f = feature("undrafted", i, json!({}));
            f["fingerprint"]["subtype"] = json!(subtype);
            f
        })
        .collect();
    fs.push(feature("angled_step", 20, json!({})));
    fs.push(feature("new_family", 21, json!({})));
    fs.push(feature(
        "hole",
        22,
        json!({"axis":[0,0,1],"location":[12,12,10],
        "diameter":4,"depth":10,"bottom":"through"}),
    ));
    let a = analysis(fs);
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(p.feature_dispositions.len(), subtypes.len() + 2);
    for (i, subtype) in subtypes.iter().enumerate() {
        let entries: Vec<_> = p
            .feature_dispositions
            .iter()
            .filter(|d| d.subject == format!("f:undrafted:{i}"))
            .collect();
        assert_eq!(entries.len(), 1);
        let d = entries[0];
        assert_eq!(d.severity, "info");
        if *subtype == "CounterSink" {
            assert_eq!(d.disposition, Disposition::EvidenceOnly);
            assert_eq!(d.reason_code, DispositionReason::HoleCalloutEvidence);
        } else {
            assert_eq!(d.disposition, Disposition::Deferred);
            assert_eq!(d.reason_code, DispositionReason::ConsumerSemanticsDeferred);
        }
    }
    assert!(
        p.feature_dispositions
            .iter()
            .any(|d| d.subject == "f:angled_step:20"
                && d.disposition == Disposition::Unsupported
                && d.reason_code == DispositionReason::ConsumerSemanticsUnsupported)
    );
    assert!(
        p.feature_dispositions
            .iter()
            .any(|d| d.subject == "f:new_family:21"
                && d.disposition == Disposition::Deferred
                && d.reason_code == DispositionReason::DimensionsNotPorted)
    );
    assert!(
        !p.feature_dispositions
            .iter()
            .any(|d| d.subject == "f:hole:f75")
    );
    // A policy record does not erase the existing warning for an unsupported step.
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "angled_step_requirement_unsupported" && p.severity == "warning")
    );
    let mut bad = serde_json::to_value(&p.feature_dispositions[0]).unwrap();
    bad["reason_code"] = json!("unreviewed_excuse");
    assert!(serde_json::from_value::<FeatureDisposition>(bad).is_err());
}

#[test]
fn hole_pattern_interval_and_group_counts_must_match_physical_members() {
    let mut a = grid();
    // Two columns, two rows: each pitch has exactly one interval.
    for bad in ["2× 10", "9× 10"] {
        let issues = lint::check(
            &a,
            &[mark("f:hole_pattern:1", "grid_pitch.length.col", bad)],
        );
        assert!(
            issues
                .iter()
                .any(|i| i.code.starts_with("hole_pattern_requirement_")
                    && i.requirements
                        .iter()
                        .any(|r| r.parameter == "grid_pitch.length.col")),
            "{bad}"
        );
    }
    // Add a third column. A bare pitch cannot certify two intervals.
    for (n, y) in [(5, 10), (6, 20)] {
        a.features.push(serde_json::from_value(feature("hole", n,
            json!({"axis":[0,0,1],"location":[30,y,10],"diameter":4,"depth":10,"bottom":"through"}))).unwrap());
        a.recognition.as_mut().unwrap().patterns[0]
            .members
            .push(format!("f:hole:{n}"));
    }
    for text in ["10", "3× 10", "2× 11", "2× 10"] {
        let issues = lint::check(
            &a,
            &[mark("f:hole_pattern:1", "grid_pitch.length.col", text)],
        );
        let gap = issues.iter().any(|i| {
            i.code.starts_with("hole_pattern_requirement_")
                && i.requirements
                    .iter()
                    .any(|r| r.parameter == "grid_pitch.length.col")
        });
        assert_eq!(gap, text != "2× 10", "{text}");
    }
    let mut counted = mark("f:hole:1", "bore.diameter", "6× ⌀4 THRU (2×3)");
    counted.claims = (1..=6)
        .map(|n| Claim {
            feature: format!("f:hole:{n}"),
            parameter: "bore.diameter".into(),
        })
        .collect();
    assert!(!lint::check(&a, &[counted.clone()]).iter().any(|i| {
        i.requirements
            .iter()
            .any(|r| r.parameter == "grouping.count")
    }));
    counted.text = "5× ⌀4 THRU (2×3)".into();
    assert!(lint::check(&a, &[counted]).iter().any(|i| {
        i.requirements
            .iter()
            .any(|r| r.parameter == "grouping.count")
    }));
}

/// #207: claims follow quiddity's face accounting. On NIST CTC-01 its `step_levels` evidence
/// accounts the end blocks' inner walls, the notch side walls and slot 1's end walls as feature
/// faces, and its body evidence (quiddity-rust#100) the envelope planes once the overall sizes
/// are stated. Only what it leaves unexplained is reported: the notch walls and the hex boss's
/// top, which is not stock.
#[test]
fn quiddity_face_accounting_decides_which_faces_are_claimed() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let (_, a) = model::analyse_native(
        &root.join("tests/fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz"),
    )
    .unwrap();
    assert!(a.face_accounting.is_some());
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let unclaimed: Vec<String> = p
        .problems
        .iter()
        .filter(|q| q.code == "unclaimed_geometry")
        .flat_map(|q| q.subjects.clone())
        .collect();
    for f in [8, 20, 28, 31, 35, 37, 41, 44, 82, 86] {
        assert!(
            !unclaimed.contains(&format!("face:{f}")),
            "face {f} is a step_levels face: {unclaimed:?}"
        );
    }
    for f in [4, 6, 22, 24, 26, 46, 47, 49, 54, 55] {
        assert!(
            !unclaimed.contains(&format!("face:{f}")),
            "face {f} is quiddity's body evidence: {unclaimed:?}"
        );
    }
    // quiddity-rust 0.2.1 explains CTC-01's last faces: notch walls 33 and 39
    // (rectangular_through_notches) and boss cap 104 (polygonal_bosses).
    for f in [33, 39, 104] {
        assert!(
            !unclaimed.contains(&format!("face:{f}")),
            "face {f} is explained by quiddity: {unclaimed:?}"
        );
    }
}

/// #213: a recognisable shape among unexplained faces warns, naming its faces, kind and place,
/// with impact `incomplete` grouped by its kind; `unclaimed_geometry` stays the info count, and a
/// feature claiming one of its faces (a declaration) withdraws the warning.
#[test]
fn unexplained_shapes_warn_beside_the_unclaimed_count() {
    let shape =
        |kind: &str, faces: Value| json!({"kind":kind,"faces":faces,"bounds":[[0,0,0],[4,1,2]]});
    let mut doc = serde_json::to_value(analysis(vec![feature("unported", 1, json!({}))])).unwrap();
    doc["unexplained_shapes"] = json!([
        shape("protrusion", json!([5, 7, 9])),
        shape("cut", json!([1, 2])),
        {"kind":"chamfer","faces":[3],"bounds":[[0,0,0],[1,1,1]],
            "chamfer":{"diameter":5.5,"length":1,"angle":45}},
    ]);
    let a: Analysis = serde_json::from_value(doc).unwrap();
    let p = model::plan(&a, &Spec::default()).unwrap();
    let shapes: Vec<_> = p
        .problems
        .iter()
        .filter(|q| q.code == "shape_not_recognised")
        .collect();
    // Face 1 is f:unported:1's, so the cut is withdrawn.
    assert_eq!(shapes.len(), 2, "{shapes:#?}");
    for q in &shapes {
        assert_eq!(q.severity, "warning");
        let labels = q.labels();
        assert_eq!(labels.impact, draftwright::finding::Impact::Incomplete);
    }
    let protrusion = shapes
        .iter()
        .find(|q| q.labels().group == "shape_not_recognised:protrusion")
        .unwrap();
    assert_eq!(protrusion.subjects, ["face:5", "face:7", "face:9"]);
    assert!(protrusion.message.contains("at (2, 0.5, 1)"));
    let chamfer = shapes
        .iter()
        .find(|q| q.labels().group == "shape_not_recognised:chamfer")
        .unwrap();
    assert!(chamfer.message.contains("1 × 45° chamfer") && chamfer.message.contains("ø5.5"));
    assert!(
        p.problems
            .iter()
            .any(|q| q.code == "unclaimed_geometry" && q.severity == "info")
    );
    assert!(!p.problems.iter().any(|q| q.severity == "error"));
}

/// #213: shapes read from real topology. The spool's hole-edge cones are chamfers on their ø10
/// bores and its six flange scallops a periodic pattern; the string post's shallow concentric
/// recesses are cuts. Every shape face is one quiddity leaves unexplained.
#[test]
fn unexplained_shapes_are_read_from_the_part() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/pmi/step");
    let (part, mut spool) = model::analyse_native(&root.join("spool_fits.step.gz")).unwrap();
    // v0.2.3 (#127) now states all six scallops as cylindrical profiles, counted with the two existing R10 rounds,
    // rather than leaving their periodic shape unexplained. Prove that stronger
    // native contract before exercising the legacy unexplained-topology detector.
    let profiles: Vec<_> = spool
        .features
        .iter()
        .filter(|f| f.kind == "cylindrical_profile")
        .collect();
    assert_eq!(profiles.len(), 6);
    assert!(profiles.iter().all(|f| f.parameters["radius"] == 10.0));
    let native = model::plan(&spool, &Spec::default()).unwrap();
    for f in &profiles {
        assert!(native.dimensions.iter().any(|d| {
            d.placed.is_some()
                && d.label == "8× R10"
                && d.measurements
                    .iter()
                    .any(|m| m.feature == f.id && m.parameter == "cylindrical_profile.radius")
        }));
    }
    let mut scallops: Vec<_> = profiles
        .iter()
        .flat_map(|f| f.faces.iter().copied())
        .collect();
    // quiddity 0.5.0 (#244) claims each ⌀10 hole's 1 × 45° mouth chamfer as part of the hole, and
    // the callout states it (`C1`), so the six cones are explained: the detector reports none of
    // them. Prove that, then withdraw the claim as the scallops' is withdrawn, so the detector's
    // own reading of the part's topology is still exercised below.
    let cones: Vec<usize> = spool
        .features
        .iter()
        .filter(|f| f.kind == "hole")
        .flat_map(|f| {
            f.parameters
                .get("mouth_chamfers")
                .and_then(|c| c.as_array())
                .into_iter()
                .flatten()
                .flat_map(|c| c["faces"].as_array().into_iter().flatten())
                .filter_map(|face| face.as_u64().map(|face| face as usize))
        })
        .collect();
    assert_eq!(cones.len(), 6, "{cones:?}");
    assert!(
        !spool.unexplained_shapes.iter().any(|s| s
            .chamfer
            .as_ref()
            .is_some_and(|c| (c.diameter - 10.0).abs() < 1e-6)),
        "{:?}",
        spool.unexplained_shapes
    );
    assert!(
        cones
            .iter()
            .all(|&f| spool.face_accounting.as_ref().unwrap()[f] == model::FaceGroup::Feature)
    );
    scallops.extend(cones);
    let accounting = spool.face_accounting.as_mut().unwrap();
    for face in scallops {
        accounting[face] = model::FaceGroup::Unexplained;
    }
    spool.features.retain(|f| f.kind != "cylindrical_profile");
    spool.unexplained_shapes = draftwright::unexplained::shapes(&part, accounting);
    let kinds = |a: &Analysis, kind: Kind| -> Vec<draftwright::unexplained::Shape> {
        a.unexplained_shapes
            .iter()
            .filter(|s| s.kind == kind)
            .cloned()
            .collect()
    };
    use draftwright::unexplained::Kind;
    let bores: Vec<_> = kinds(&spool, Kind::Chamfer)
        .into_iter()
        .filter_map(|s| s.chamfer)
        .filter(|c| (c.diameter - 10.0).abs() < 1e-6)
        .collect();
    assert_eq!(bores.len(), 6, "{:?}", spool.unexplained_shapes);
    assert!(
        bores
            .iter()
            .all(|c| (c.length - 1.0).abs() < 1e-6 && (c.angle - 45.0).abs() < 1e-6)
    );
    let teeth = kinds(&spool, Kind::Teeth);
    assert_eq!(teeth.len(), 1);
    assert_eq!(teeth[0].teeth.as_ref().unwrap().count, 6);
    let (_, post) = model::analyse_native(&root.join("string_post_tapped.step.gz")).unwrap();
    let cuts = kinds(&post, Kind::Cut);
    assert!(!cuts.is_empty() && cuts.iter().all(|s| s.faces.len() >= 2));
    for a in [&spool, &post] {
        let accounting = a.face_accounting.as_ref().unwrap();
        for s in &a.unexplained_shapes {
            assert!(
                s.faces
                    .iter()
                    .all(|&f| accounting[f] == model::FaceGroup::Unexplained),
                "{s:?}"
            );
        }
    }
    // Congruent shapes share one warning, and every shape face is in exactly one.
    let warnings = |spec: &Spec| -> Vec<draftwright::model::Problem> {
        model::plan(&spool, spec)
            .unwrap()
            .problems
            .into_iter()
            .filter(|q| q.code == "shape_not_recognised")
            .collect()
    };
    let plain = warnings(&Spec::default());
    let mut cited: Vec<String> = plain.iter().flat_map(|q| q.subjects.clone()).collect();
    cited.sort();
    let mut faces: Vec<String> = spool
        .unexplained_shapes
        .iter()
        .flat_map(|s| s.faces.iter().map(|f| format!("face:{f}")))
        .collect();
    faces.sort();
    assert_eq!(cited, faces);
    let bore = |qs: &[draftwright::model::Problem]| {
        qs.iter()
            .find(|q| {
                q.message
                    .contains("1 × 45° chamfer on the edge of a ø10 cylinder")
            })
            .cloned()
    };
    assert!(bore(&plain).unwrap().message.contains("6 congruent shapes"));
    // #213 review: the Spec states a shape with a note on one of its faces, or a tooth pattern
    // with an authored gear on its axis, and that withdraws its warning.
    let chamfer = &kinds(&spool, Kind::Chamfer)
        .into_iter()
        .find(|s| {
            s.chamfer
                .as_ref()
                .is_some_and(|c| (c.diameter - 10.0).abs() < 1e-6)
        })
        .unwrap();
    let t = teeth[0].teeth.as_ref().unwrap();
    let axis = ["x", "y", "z"][(0..3).find(|&k| t.axis[k].abs() > 0.99).unwrap()];
    let spec: Spec = serde_json::from_value(json!({
        "faces": {format!("face:{}", chamfer.faces[0]): {"note": "C1"}},
        "gears": [{"on": format!("face:{}", teeth[0].faces[0]),
            "frame": {"origin": t.centre, "axis": axis}, "tooth_count": 6,
            "module": 1.25, "pressure_angle": 20, "profile_shift": 0, "face_width": 10,
            "tooth_thickness": 1.9634954084936207, "tooth_thickness_tolerance": [-0.03, 0.01],
            "flank_tolerance_class": 7}],
    }))
    .unwrap();
    let stated = warnings(&spec);
    assert!(
        bore(&stated)
            .unwrap()
            .message
            .contains("5 congruent shapes")
    );
    assert!(
        !stated
            .iter()
            .any(|q| q.labels().group == "shape_not_recognised:teeth"),
        "{stated:#?}"
    );
    assert_eq!(stated.len(), plain.len() - 1);
}

/// #213 review: congruent shapes are one warning naming their count; a turned step names its
/// shoulder; each kind's remedy is one the Spec can take.
#[test]
fn unexplained_shape_warnings_merge_congruent_shapes() {
    let mut doc = serde_json::to_value(analysis(vec![])).unwrap();
    let box_at = |x: f64| json!([[x, 0, 0], [x + 4.0, 1, 2]]);
    doc["unexplained_shapes"] = json!([
        {"kind":"protrusion","faces":[1, 2],"bounds":box_at(0.0)},
        {"kind":"protrusion","faces":[3, 4],"bounds":box_at(10.0)},
        {"kind":"protrusion","faces":[5, 6],"bounds":[[0,0,0],[4,2,1]]},
        {"kind":"step","faces":[7, 8],"bounds":[[-31.5,-31.5,-1.5],[31.5,31.5,1.5]],
            "shoulders":[57]},
        {"kind":"teeth","faces":[9, 10],"bounds":[[0,0,0],[1,1,1]],
            "teeth":{"count":20,"centre":[0,0,0],"axis":[0,0,1]}},
    ]);
    let a: Analysis = serde_json::from_value(doc).unwrap();
    let p = model::plan(&a, &Spec::default()).unwrap();
    let shapes: Vec<_> = p
        .problems
        .iter()
        .filter(|q| q.code == "shape_not_recognised")
        .collect();
    assert_eq!(shapes.len(), 4, "{shapes:#?}");
    let pair = shapes
        .iter()
        .find(|q| q.subjects == ["face:1", "face:2", "face:3", "face:4"])
        .unwrap();
    assert!(
        pair.message
            .contains("2 congruent shapes, each a protrusion of 4 × 1 × 2 mm")
    );
    assert!(pair.message.contains("feature_declarations") && pair.message.contains("Spec's faces"));
    let step = shapes
        .iter()
        .find(|q| q.labels().group == "shape_not_recognised:step")
        .unwrap();
    assert!(
        step.message
            .contains("turned step or land, its shoulder ø57")
    );
    let teeth = shapes
        .iter()
        .find(|q| q.labels().group == "shape_not_recognised:teeth")
        .unwrap();
    assert!(
        teeth.message.contains("Spec's gears") && !teeth.message.contains("feature_declarations")
    );
}
