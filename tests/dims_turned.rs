//! Turned steps' own dimensions (`src/dims/turned.rs`): each band's diameter and length, the
//! overall diameter that also states the band it is, and the overall length the step chain
//! conveys. On the captured turned corpus parts (`tests/fixtures/recognition/sheets/`) they are
//! checked against what draftwright's sheets print; on the turned fixture (`parts/turned.step`,
//! its recognition edited) the rules one at a time. The item-by-item comparison over every
//! captured sheet, with the differences' verdicts, is `tests/dimensions.rs`.

#[path = "common/reference_geometry.rs"]
mod reference_geometry;

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::lint::{self, Mark};
use draftwright::model::{self, Analysis, DimensionSpec, Plan, Spec};
use draftwright::output::Claim;
use serde_json::{Value, json};

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

/// A sampled corpus part read with the recognition draftwright drew its sheet from.
fn sheet_part(corpus: &Path, name: &str) -> Analysis {
    let f = facts::load(name);
    let mut json = String::new();
    flate2::read::GzDecoder::new(
        std::fs::File::open(fixture(&format!("recognition/sheets/{name}.json.gz"))).unwrap(),
    )
    .read_to_string(&mut json)
    .unwrap();
    let (part, mut a) = model::analyse(&corpus.join(&f.binding.file)).unwrap();
    let (r, features) = draftwright::bridge::read(&part, &a.binding.source_sha256, &json).unwrap();
    a.recognition = Some(r);
    a.features = features;
    a
}

/// The turned fixture (a ø30 band 40 long and a ø16 band 30 long about the part's z, quiddity's
/// x) with its recognition.
fn turned() -> Analysis {
    let json = std::fs::read_to_string(fixture("recognition/turned.json")).unwrap();
    let (part, mut a) = model::analyse(&fixture("parts/turned.step")).unwrap();
    let (r, features) = draftwright::bridge::read(&part, &a.binding.source_sha256, &json).unwrap();
    a.recognition = Some(r);
    a.features = features;
    a
}

/// The turned fixture with its bands replaced by *bands*, each `(lo, hi, diameter)` along
/// quiddity's x (the part's z from 26.153 below quiddity's origin), on the fixture's profile.
fn with_bands(bands: &[(f64, f64, f64)]) -> Analysis {
    let mut a = turned();
    let template = a
        .features
        .iter()
        .find(|f| f.kind == "turned_step")
        .unwrap()
        .clone();
    a.features.retain(|f| f.kind != "turned_step");
    for (i, (lo, hi, d)) in bands.iter().enumerate() {
        let mut f = template.clone();
        f.id = format!("f:turned_step:{}", i + 1);
        f.legacy_id = None;
        f.parameters.insert("lo".into(), json!(lo));
        f.parameters.insert("hi".into(), json!(hi));
        f.parameters.insert("diameter".into(), json!(d));
        a.features.push(f);
    }
    a
}

/// quiddity's x of the part's z = *z* on the turned fixture.
fn q(z: f64) -> f64 {
    z - 26.152_728_066_648_48
}

/// The plan's items that state a turned step or the overall extents, as (ID, label), in plan
/// order; each must be drawn.
fn turned_items(plan: &Plan) -> Vec<(String, String)> {
    plan.dimensions
        .iter()
        .filter(|d| {
            d.id.starts_with("d:envelope/")
                || d.measurements
                    .iter()
                    .any(|m| m.feature.starts_with("f:turned_step:"))
        })
        .map(|d| {
            assert!(d.placed.is_some(), "{} is not drawn", d.id);
            (d.id.clone(), d.label.clone())
        })
        .collect()
}

fn strs(items: &[(&str, &str)]) -> Vec<(String, String)> {
    items
        .iter()
        .map(|(a, b)| (a.to_string(), b.to_string()))
        .collect()
}

/// Lint's turned-step ledger of *a* against the plan's items as marks (their labels and claims),
/// as `feature parameter state`.
fn marks(plan: &Plan) -> Vec<Mark> {
    plan.dimensions
        .iter()
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

fn ledger(a: &Analysis, plan: &Plan) -> Vec<String> {
    lint::turned::outcomes(a, &marks(plan))
        .into_iter()
        .map(|o| format!("{} {} {:?}", o.feature, o.parameter, o.state))
        .collect()
}

fn reported(plan: &Plan, code: &str) -> Vec<String> {
    plan.problems
        .iter()
        .filter(|p| p.code == code)
        .flat_map(|p| p.subjects.clone())
        .collect()
}

/// GRM-05 (a bolt turned about z: ø10 × 14, ø6.25 × 5, ø6 × 13): draftwright's sheet prints the
/// overall ø10 (which also states the ø10 band), a ø leader for each smaller band, and the chain
/// 14, 5, 13, which conveys the overall 32. The plan states the same, every step claimed, none
/// reported.
#[test]
fn grm05_states_its_step_diameters_and_lengths_as_draftwright_does() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = sheet_part(&corpus, "grm05");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        turned_items(&plan),
        strs(&[
            ("d:envelope/od.diameter", "ø10"),
            ("d:envelope/height.length", "32"),
            ("d:f:turned_step:f4/step.diameter", "ø6.25"),
            ("d:f:turned_step:f6/step.diameter", "ø6"),
            ("d:f:turned_step:f0/step.length", "14"),
            ("d:f:turned_step:f4/step_position.length", "19"),
        ])
    );
    let od = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:envelope/od.diameter")
        .unwrap();
    let claims: Vec<String> = od
        .measurements
        .iter()
        .map(|m| format!("{}/{}", m.feature, m.parameter))
        .collect();
    assert_eq!(
        claims,
        ["envelope/od.diameter", "f:turned_step:f0/step.diameter"]
    );
    assert!(
        reported(&plan, "dimensions_not_ported").is_empty()
            && reported(&plan, "feature_not_dimensioned").is_empty(),
        "{:?}",
        plan.problems
    );
}

/// The other captured turned parts, against draftwright's sheets: each turned step's diameter
/// and length items (view and label) are the sheet's (`step.diameter` and `step.length`
/// annotations), the overall diameter prints as the sheet's `dim_od` (`2× ø130` where it stands
/// for spool132's two flanges), and the overall length is stated exactly where the chain does
/// not run end to end (spool132's hub and connector109's grooves and thread leave gaps; there
/// draftwright prints it too). connector109's three ø130.94 lands are the overall diameter's
/// (#220), where draftwright repeats each. grm03 differs once, listed in
/// `known_dimensions.json`: draftwright has no overall diameter for it and leads its ø10 band
/// on its own.
#[test]
fn turned_corpus_parts_state_their_steps_as_draftwright_does() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    for (name, od, length) in [
        ("grm03", "ø10", Some("23.5")),
        ("string_post", "ø7.5", Some("14.8")),
        ("spool132", "2× ø130", Some("140")),
        ("connector109", "ø130.94", Some("420")),
    ] {
        let a = sheet_part(&corpus, name);
        let plan = model::plan(&a, &Spec::default()).unwrap();
        let mut rust: Vec<(String, String)> = plan
            .dimensions
            .iter()
            .filter(|d| !d.id.starts_with("d:envelope/"))
            .filter(|d| {
                d.measurements
                    .iter()
                    .any(|m| m.parameter == "step.diameter")
            })
            .map(|d| {
                let source = plan
                    .views
                    .iter()
                    .find(|v| v.id == d.view)
                    .and_then(|v| v.detail.as_ref())
                    .map_or(d.view.as_str(), |r| r.source_view.as_str());
                (source.trim_start_matches("v:").to_string(), d.label.clone())
            })
            .collect();
        rust.sort();
        let f = facts::load(name);
        let mut python: Vec<(String, String)> = f
            .annotations
            .iter()
            .filter(|n| n.name != "dim_od")
            .filter(|n| {
                n.measurements
                    .iter()
                    .any(|m| m.parameter == "step.diameter")
            })
            .map(|n| {
                (
                    n.view.clone().unwrap_or_default(),
                    n.label.clone().unwrap_or_default(),
                )
            })
            .collect();
        // Captured Python labels stay archived; only their expected nominal text changes.
        for (_, label) in &mut python {
            *label = match label.as_str() {
                "ø103.1" => "ø103.12".into(),
                "ø130.9" => "ø130.94".into(),
                "ø6.2" => "ø6.25".into(),
                _ => label.clone(),
            };
        }
        if name == "grm03" {
            python.retain(|(_, l)| l != "ø10");
        }
        // #220: separate lands of the largest diameter are stated by the overall diameter, which
        // claims each (connector109's three ø130.94); draftwright repeats the size per land.
        if name == "connector109" {
            python.retain(|(_, l)| l != "ø130.94");
            let od = plan
                .dimensions
                .iter()
                .find(|d| d.id == "d:envelope/od.diameter")
                .unwrap();
            let lands = od
                .measurements
                .iter()
                .filter(|m| m.parameter == "step.diameter")
                .count();
            assert_eq!(lands, 3, "{name}");
        }
        // Axial positions now follow the inspection datum; compare physical diameter
        // statements with Python and prove every band length through lint's baseline ledger.
        assert!(
            ledger(&a, &plan).iter().all(|o| o.ends_with("Placed")),
            "{name}: {:?}",
            ledger(&a, &plan)
        );
        python.sort();
        assert_eq!(rust, python, "{name}");
        let label = |id: &str| {
            plan.dimensions
                .iter()
                .find(|d| d.id == id)
                .map(|d| d.label.clone())
        };
        assert_eq!(
            label("d:envelope/od.diameter").as_deref(),
            Some(od),
            "{name}"
        );
        let along = plan
            .dimensions
            .iter()
            .find(|d| d.id.starts_with("d:envelope/") && d.id.ends_with(".length"))
            .map(|d| d.label.as_str());
        assert_eq!(along, length, "{name}");
        assert!(
            reported(&plan, "dimensions_not_ported")
                .iter()
                .all(|s| !s.starts_with("f:turned_step:")),
            "{name}"
        );
    }
}

/// Each band states its own diameter and length; the overall diameter states the band of its
/// size, and the chain (40 + 30) the overall 70, which is not stated again. With a band taken
/// away the chain no longer runs end to end, so the overall length is stated.
#[test]
fn the_overall_finishes_the_default_baseline_and_a_partial_profile_keeps_its_length() {
    let plan = model::plan(&turned(), &Spec::default()).unwrap();
    assert_eq!(
        turned_items(&plan),
        strs(&[
            ("d:envelope/od.diameter", "ø30"),
            ("d:envelope/height.length", "70"),
            ("d:f:turned_step:f6/step.diameter", "ø16"),
            ("d:f:turned_step:f0/step.length", "40"),
        ])
    );
    let a = with_bands(&[(q(0.0), q(40.0), 30.0)]);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        turned_items(&plan),
        strs(&[
            ("d:envelope/od.diameter", "ø30"),
            ("d:envelope/height.length", "70"),
            ("d:f:turned_step:1/step.length", "40"),
        ])
    );
    let height = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:envelope/height.length")
        .unwrap();
    assert!(
        height.basis.contains("does not run end to end"),
        "{}",
        height.basis
    );
}

/// Three or more bands of one printed length are one `N× L` claiming each (draftwright also
/// collapses lengths that print apart, within 10 % of their mean: the port keeps those apart, as
/// `3× 23.3` would misstate a 23 and a 23.7); and two
/// bands of the largest diameter at separate stations are both stated by the overall
/// diameter (`2× ø30`), while two that adjoin are not.
#[test]
fn repeated_lengths_and_twin_flanges_are_stated_once() {
    let a = with_bands(&[
        (q(0.0), q(23.0), 30.0),
        (q(23.0), q(46.3), 20.0),
        (q(46.3), q(70.0), 30.0),
    ]);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let lengths: Vec<String> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/step.length"))
        .map(|d| d.label.clone())
        .collect();
    assert_eq!(lengths, ["23"]);
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.id.ends_with("/step_position.length") && (d.value - 46.3).abs() < 1e-6)
    );
    let a = with_bands(&[
        (q(0.0), q(23.34), 30.0),
        (q(23.34), q(46.66), 20.0),
        (q(46.66), q(70.0), 30.0),
    ]);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        turned_items(&plan),
        strs(&[
            ("d:envelope/od.diameter", "2× ø30"),
            ("d:envelope/height.length", "70"),
            ("d:f:turned_step:2/step.diameter", "ø20"),
            ("d:f:turned_step:1/step.length", "23.34"),
            ("d:f:turned_step:2/step_position.length", "46.66"),
        ])
    );
    let chain = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:turned_step:1/step.length")
        .unwrap();
    let members: Vec<&str> = chain
        .measurements
        .iter()
        .map(|m| m.feature.as_str())
        .collect();
    assert_eq!(members, ["f:turned_step:1"]);
    let od = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:envelope/od.diameter")
        .unwrap();
    assert_eq!(od.measurements.len(), 3);
    // Lint credits every band's length and diameter on these labels.
    assert!(
        ledger(&a, &plan).iter().all(|o| o.ends_with("Placed")),
        "{:?}",
        ledger(&a, &plan)
    );

    // Adjoining twins: the overall diameter states neither, so they have their own callout,
    // one for the two, as two equal bands along z share one.
    let a = with_bands(&[(q(0.0), q(40.0), 30.0), (q(40.0), q(70.0), 30.0)]);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        turned_items(&plan),
        strs(&[
            ("d:envelope/od.diameter", "ø30"),
            ("d:envelope/height.length", "70"),
            ("d:f:turned_step:1/step.diameter", "2× ø30"),
            ("d:f:turned_step:1/step.length", "40"),
        ])
    );
}

/// Along z, exactly two bands of one diameter at different stations share one `2× ø16`
/// callout; a band whose diameter prints as a coaxial bore's reads `ø… OD`.
#[test]
fn equal_z_bands_share_a_callout_and_a_bore_sized_band_reads_od() {
    let mut a = with_bands(&[
        (q(0.0), q(10.0), 16.0),
        (q(10.0), q(60.0), 30.0),
        (q(60.0), q(70.0), 16.0),
    ]);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        turned_items(&plan)[..3],
        strs(&[
            ("d:envelope/od.diameter", "ø30"),
            ("d:envelope/height.length", "70"),
            ("d:f:turned_step:1/step.diameter", "2× ø16"),
        ])
    );
    let pair = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:turned_step:1/step.diameter")
        .unwrap();
    assert_eq!(pair.measurements.len(), 2);
    assert_eq!(pair.measurements[1].feature, "f:turned_step:3");

    // A blind ø16 bore down the axis from the top face (quiddity's x is the part's z).
    let mut hole = a.features[0].clone();
    hole.id = "f:hole:1".into();
    hole.kind = "hole".into();
    hole.parameters = serde_json::from_value(json!({
        "axis": [-1.0, 0.0, 0.0], "location": [q(70.0), 0.0, 0.0], "diameter": 16.0,
        "depth": 5.0, "bottom": "flat", "cbore": null, "csink": null, "spotface": null,
        "named_dimensions": {}
    }))
    .unwrap();
    a.features.push(hole);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:turned_step:1/step.diameter")
        .unwrap();
    assert_eq!(d.label, "2× ø16 OD");
}

/// A part turned about y: its bands' diameters are led to their circles in the front view, which
/// looks along y, and their lengths are in the side view, where each contiguous run of three or
/// more equal lengths is one `N× L`.
#[test]
fn a_part_turned_about_y_reads_its_diameters_end_on() {
    let mut a = with_bands(&[
        (q(0.0), q(10.0), 20.0),
        (q(10.0), q(20.0), 24.0),
        (q(20.0), q(30.0), 20.0),
        (q(30.0), q(70.0), 30.0),
    ]);
    // quiddity's x is now the part's y.
    let r = a.recognition.as_mut().unwrap();
    r.frame = [
        [0.0, 26.152_728_066_648_48, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
        [1.0, 0.0, 0.0],
    ];
    a.envelope = [[-15.0, 0.0, -15.0], [15.0, 70.0, 15.0]];
    a.features.retain(|f| f.kind == "turned_step");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        turned_items(&plan),
        strs(&[
            ("d:envelope/od.diameter", "ø30"),
            ("d:envelope/depth.length", "70"),
            ("d:f:turned_step:1/step.diameter", "ø20"),
            ("d:f:turned_step:2/step.diameter", "ø24"),
            ("d:f:turned_step:3/step.diameter", "ø20"),
            ("d:f:turned_step:4/step.length", "40"),
            ("d:f:turned_step:3/step_position.length", "50"),
            ("d:f:turned_step:2/step_position.length", "60"),
        ])
    );
}

/// A band whose record cannot be read is reported (`feature_not_dimensioned`), the others still
/// stated; and with a groove whose width is missing no band's ownership can be decided, so every
/// band is reported, none guessed. No turned step is ever `dimensions_not_ported`.
#[test]
fn a_band_that_cannot_be_stated_is_reported() {
    let mut a = turned();
    let f = a
        .features
        .iter_mut()
        .find(|f| f.id == "f:turned_step:f6")
        .unwrap();
    f.parameters["profile"]
        .as_object_mut()
        .unwrap()
        .remove("axis_origin");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        reported(&plan, "feature_not_dimensioned"),
        ["f:turned_step:f6"]
    );
    assert!(reported(&plan, "dimensions_not_ported").is_empty());
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.id == "d:f:turned_step:f0/step.length")
    );

    let mut a = turned();
    let mut groove = a.features[0].clone();
    groove.id = "f:groove:1".into();
    groove.kind = "groove".into();
    let profile: Value = a
        .features
        .iter()
        .find(|f| f.kind == "turned_step")
        .unwrap()
        .parameters["profile"]
        .clone();
    groove.parameters = serde_json::from_value(json!({
        "axis": "x", "at": [q(50.0), 0.0, 0.0], "diameter": 12.0, "profile": profile,
        "named_dimensions": {}
    }))
    .unwrap();
    a.features.push(groove);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        reported(&plan, "feature_not_dimensioned"),
        ["f:turned_step:f0", "f:turned_step:f6", "f:groove:1"]
    );
    assert!(
        !plan
            .dimensions
            .iter()
            .any(|d| d.id.starts_with("d:f:turned_step:"))
    );
}

/// A spec's tolerance reaches a step's length and diameter by their IDs, as for every planner,
/// and an omitted step item leaves the others (and the withheld overall length) as they were.
#[test]
fn spec_tolerances_and_omissions_reach_the_step_items() {
    let a = turned();
    let mut spec = Spec::default();
    for (id, band) in [
        ("d:f:turned_step:f6/step.length", "±0.05"),
        ("d:f:turned_step:f6/step.diameter", "+0 -0.1"),
    ] {
        spec.dimensions.insert(
            id.into(),
            DimensionSpec {
                tolerance: Some(band.into()),
                ..Default::default()
            },
        );
    }
    let plan = model::plan(&a, &spec).unwrap();
    for (id, label) in [
        ("d:f:turned_step:f6/step.length", "30 ±0.05"),
        ("d:f:turned_step:f6/step.diameter", "ø16 0 -0.1"),
    ] {
        let d = plan.dimensions.iter().find(|d| d.id == id).unwrap();
        assert_eq!(d.label, label);
        assert_eq!(d.from_spec, ["tolerance"]);
        assert!(!d.default);
    }
    let mut spec = Spec::default();
    spec.dimensions.insert(
        "d:f:turned_step:f0/step.length".into(),
        DimensionSpec {
            omit: true,
            ..Default::default()
        },
    );
    let less = model::plan(&a, &spec).unwrap();
    let full = model::plan(&a, &Spec::default()).unwrap();
    assert!(
        less.omitted
            .iter()
            .any(|o| o.id == "d:f:turned_step:f0/step.length")
    );
    for d in &less.dimensions {
        let same = full.dimensions.iter().find(|e| e.id == d.id).unwrap();
        reference_geometry::assert_local(same, d);
    }
    assert!(
        less.dimensions
            .iter()
            .any(|d| d.id == "d:envelope/height.length" && d.label == "70")
    );
}

/// ISSUE-1505-b and K6 with the inspection baseline: a 1 mm shoulder never erases its
/// neighbours' measurements. Defaults convey every band through live baseline owners;
/// explicitly toleranced lengths keep their own IDs, claims and values. Compare a 10 mm
/// shoulder too: both neighbouring lengths remain 30 and 39 in either representation.
#[test]
fn a_short_shoulder_never_erases_its_neighbours_lengths() {
    let lengths = |plan: &Plan| -> Vec<(String, String, Vec<String>)> {
        plan.dimensions
            .iter()
            .filter(|d| d.id.ends_with("/step.length"))
            .map(|d| {
                assert!(d.placed.is_some(), "{} is not drawn", d.id);
                let claims = d
                    .measurements
                    .iter()
                    .map(|m| format!("{} {}", m.feature, m.parameter))
                    .collect();
                assert!(d.label.contains("±0.1") && !d.reference, "{}", d.label);
                (
                    d.id.clone(),
                    d.label.strip_suffix(" ±0.1").unwrap().into(),
                    claims,
                )
            })
            .collect()
    };
    let short = with_bands(&[
        (q(0.0), q(30.0), 30.0),
        (q(30.0), q(31.0), 24.0),
        (q(31.0), q(70.0), 16.0),
    ]);
    let mut long = with_bands(&[
        (q(0.0), q(30.0), 30.0),
        (q(30.0), q(40.0), 24.0),
        (q(40.0), q(79.0), 16.0),
    ]);
    // The longer synthetic profile is a whole part too, with its far end at 79.
    long.envelope[1][2] += 9.0;
    for (a, expected) in [(&short, [30., 31., 70.]), (&long, [30., 40., 79.])] {
        let default = model::plan(a, &Spec::default()).unwrap();
        assert!(
            ledger(a, &default).iter().all(|o| o.ends_with("Placed")),
            "{:?}",
            ledger(a, &default)
        );
        for (i, value) in expected.iter().enumerate() {
            let feature = format!("f:turned_step:{}", i + 1);
            let parameter = if i == 0 {
                "step.length"
            } else {
                "step_position.length"
            };
            let owners: Vec<_> = default
                .dimensions
                .iter()
                .filter(|d| {
                    d.measurements
                        .iter()
                        .any(|m| m.feature == feature && m.parameter == parameter)
                })
                .collect();
            assert_eq!(owners.len(), 1, "{feature}: {owners:?}");
            let owner = owners[0];
            assert!(owner.placed.is_some());
            assert_eq!(owner.label, draftwright::dims::fmt(*value));
            if i > 0 {
                let covered = default
                    .omitted
                    .iter()
                    .find(|o| o.id == format!("d:{feature}/step.length"))
                    .unwrap();
                assert_eq!(
                    covered.state,
                    draftwright::items::State::Covered {
                        by: owner.id.clone()
                    }
                );
            }
        }
        assert_eq!([expected[0], expected[2] - expected[1]], [30., 39.]);
    }
    let mut spec = Spec::default();
    for i in 1..=3 {
        spec.dimensions.insert(
            format!("d:f:turned_step:{i}/step.length"),
            DimensionSpec {
                tolerance: Some("±0.1".into()),
                ..Default::default()
            },
        );
    }
    let plan = model::plan(&short, &spec).unwrap();
    let got = lengths(&plan);
    let ids: Vec<&str> = got.iter().map(|(id, _, _)| id.as_str()).collect();
    assert_eq!(
        ids,
        [
            "d:f:turned_step:1/step.length",
            "d:f:turned_step:2/step.length",
            "d:f:turned_step:3/step.length",
        ],
        "{got:?}"
    );
    for (i, (id, label, claims)) in got.iter().enumerate() {
        assert_eq!(
            claims,
            &[format!("f:turned_step:{} step.length", i + 1)],
            "{id}"
        );
        let value = ["30", "1", "39"][i];
        assert!(
            label.trim_matches(['(', ')']) == value,
            "{id} prints {label}, not {value}"
        );
    }
    assert!(
        ledger(&short, &plan).iter().all(|o| o.ends_with("Placed")),
        "{:?}",
        ledger(&short, &plan)
    );

    let wide = lengths(&model::plan(&long, &spec).unwrap());
    for i in [0, 2] {
        assert_eq!(
            (&got[i].0, &got[i].1),
            (&wide[i].0, &wide[i].1),
            "a neighbour's length changes with the shoulder's"
        );
    }
}

/// ISSUE-1505-b: a thin shoulder in the middle of a vertical turned profile (0.2 long between a
/// ø30 and a ø16 band, 200 overall) keeps its own length on the main view, beside its neighbour's
/// 99.8; the longest band is the one the overall conveys. Nothing is off the sheet, so no
/// shoulder detail is made (a detail only when no clean main-view statement exists), and lint
/// finds every band length stated.
#[test]
fn a_nonterminal_thin_z_shoulder_keeps_its_length_on_the_main_view() {
    let mut a = with_bands(&[
        (q(0.0), q(100.0), 30.0),
        (q(100.0), q(100.2), 40.0),
        (q(100.2), q(200.0), 16.0),
    ]);
    a.envelope[1][2] = a.envelope[0][2] + 200.0;
    let p = model::plan(&a, &Spec::default()).unwrap();
    let length = |id: &str| {
        let d = p.dimensions.iter().find(|d| d.id == id).unwrap();
        assert!(d.placed.is_some() && !d.reference, "{id}");
        d.label.clone()
    };
    assert_eq!(length("d:f:turned_step:2/step.length"), "0.2");
    assert_eq!(length("d:f:turned_step:3/step.length"), "99.8");
    assert!(
        p.omitted
            .iter()
            .any(|o| o.id == "d:f:turned_step:1/step.length"
                && o.state
                    == draftwright::items::State::Covered {
                        by: "d:envelope/height.length".into()
                    })
    );
    assert!(!p.views.iter().any(|v| v.id.contains("detail")));
    let marks: Vec<Mark> = p
        .dimensions
        .iter()
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
        .collect();
    let issues = lint::check(&a, &marks);
    assert!(
        !issues.iter().any(|i| i.code == "axial_length_missing"),
        "{issues:?}"
    );
}

/// Three ø30 lands between two equal ø26 grooves, 2 wide, on x: 10, 46 and 10 long, 70 overall.
fn lands_between_grooves() -> Analysis {
    let mut a = with_bands(&[
        (q(0.0), q(10.0), 30.0),
        (q(12.0), q(58.0), 30.0),
        (q(60.0), q(70.0), 30.0),
    ]);
    let profile = a
        .features
        .iter()
        .find(|f| f.kind == "turned_step")
        .unwrap()
        .parameters["profile"]
        .clone();
    for (i, z) in [11.0, 59.0].into_iter().enumerate() {
        let mut groove = a.features[0].clone();
        groove.id = format!("f:groove:{}", i + 1);
        groove.kind = "groove".into();
        groove.parameters = serde_json::from_value(json!({
            "axis": "x", "at": [q(z), 0.0, 0.0], "width": 2.0, "diameter": 26.0,
            "profile": profile, "named_dimensions": {}
        }))
        .unwrap();
        a.features.push(groove);
    }
    a
}

/// #220: three ø30 lands between two equal grooves (10, 46 and 10 long, the grooves 2 wide and
/// ø26 at 11 and 59, 70 overall). The overall diameter states every land, so no land repeats
/// it; the two grooves are one counted callout claiming both; and the chain of lands and grooves
/// closes with the overall length, so its longest member is reference: omitted by default and
/// drawn in parentheses when the spec asks for references (`sheet/references`).
#[test]
fn lands_between_equal_grooves_state_the_diameter_once_and_close_their_chain() {
    let a = lands_between_grooves();
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let od = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:envelope/od.diameter")
        .unwrap();
    assert_eq!(od.label, "ø30");
    let lands: Vec<&str> = od
        .measurements
        .iter()
        .filter(|m| m.parameter == "step.diameter")
        .map(|m| m.feature.as_str())
        .collect();
    assert_eq!(
        lands,
        ["f:turned_step:1", "f:turned_step:2", "f:turned_step:3"]
    );
    assert!(
        !plan
            .dimensions
            .iter()
            .any(|d| d.id.ends_with("/step.diameter")),
        "no land repeats the overall diameter"
    );
    let grooves: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.contains("groove"))
        .collect();
    assert_eq!(grooves.len(), 1);
    assert_eq!(grooves[0].label, "2× 2 WIDE × ø26");
    let claimed: Vec<&str> = grooves[0]
        .measurements
        .iter()
        .filter(|m| m.parameter == "groove.length")
        .map(|m| m.feature.as_str())
        .collect();
    assert_eq!(claimed, ["f:groove:1", "f:groove:2"]);
    // Every land's diameter is credited by lint, through the overall diameter's claims.
    let ledger = ledger(&a, &plan);
    assert!(
        ledger
            .iter()
            .filter(|o| o.contains("step.diameter"))
            .all(|o| o.ends_with("Placed")),
        "{ledger:?}"
    );
    // The 46 land closes the chain 10 + 2 + 46 + 2 + 10 with the 70 overall.
    let middle = "d:f:turned_step:2/step.length";
    assert!(plan.dimensions.iter().all(|d| d.id != middle));
    let covered = plan.omitted.iter().find(|o| o.id == middle).unwrap();
    assert!(
        matches!(&covered.state, draftwright::items::State::Covered { .. }),
        "{covered:?}"
    );
    let spec: Spec = serde_json::from_value(json!({"sheet": {"references": true}})).unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let reference = plan.dimensions.iter().find(|d| d.id == middle).unwrap();
    assert_eq!(reference.label, "(46)");
    for id in [
        "d:f:turned_step:1/step.length",
        "d:f:turned_step:3/step.length",
        "d:envelope/height.length",
    ] {
        let d = plan.dimensions.iter().find(|d| d.id == id).unwrap();
        assert!(!d.label.starts_with('('), "{id}: {}", d.label);
    }
}

/// #220: lm8uu's two solids (its ball cage inside the sleeve) are drawn as one. The chain of
/// lands and grooves lies wholly on the solid that fills the box, so it still closes with the
/// overall length; with a member on another solid, or no solid filling the box, none is walked.
#[test]
fn a_chain_on_the_solid_that_fills_the_box_closes_among_several() {
    let middle = "d:f:turned_step:2/step.length";
    let mut a = lands_between_grooves();
    a.solid_count = Some(2);
    let body: Vec<usize> = a.features.iter().flat_map(|f| f.faces.clone()).collect();
    a.body_faces = Some(body.clone());
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert!(plan.dimensions.iter().all(|d| d.id != middle));
    a.body_faces = None;
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert!(plan.dimensions.iter().any(|d| d.id == middle));
    let elsewhere = body.iter().max().unwrap() + 1;
    let groove = a
        .features
        .iter_mut()
        .find(|f| f.id == "f:groove:1")
        .unwrap();
    groove.faces = vec![elsewhere];
    a.body_faces = Some(body);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert!(plan.dimensions.iter().any(|d| d.id == middle));
}

/// #220: a short disc on z (gear_blank: one ø16 boss 10 high, as wide as the part) reads as a
/// turned part. Its diameter states the overall width and depth, which are not dimensioned
/// again; the side view, which would repeat the front, is left out; and the spec keeps it by
/// naming it.
#[test]
fn a_short_disc_on_z_reads_as_turned() {
    let (_, a) = model::analyse_native(&fixture("parts/gear_blank.step")).unwrap();
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let views: Vec<&str> = plan.views.iter().map(|v| v.id.as_str()).collect();
    assert!(!views.contains(&"v:side"), "{views:?}");
    assert!(views.contains(&"v:front") && views.contains(&"v:plan"));
    for id in ["d:envelope/width.length", "d:envelope/depth.length"] {
        assert!(plan.dimensions.iter().all(|d| d.id != id), "{id}");
    }
    // v0.2.3 (#123): a whole-body OD replaces the suppressed full-length boss.
    let diameter = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:envelope/od.diameter")
        .unwrap();
    assert_eq!(diameter.label, "ø16");
    assert!(
        diameter
            .measurements
            .iter()
            .any(|m| m.feature == "f:turned_body:f0"
                && m.parameter == "turned_body.diameter"
                && m.value == 16.0)
    );
    // Independent geometry-based coverage still proves all three physical extents,
    // without inventing box-width/depth requirements on the turned body.
    let found = lint::envelope::outcomes(&a, &marks(&plan));
    assert_eq!(found.len(), 3);
    assert!(
        found.iter().all(|o| o.state == lint::turned::State::Placed),
        "{found:?}"
    );
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.id == "d:envelope/height.length")
    );
    let spec: Spec = serde_json::from_value(json!({"views": {"side": {}}})).unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    assert!(plan.views.iter().any(|v| v.id == "v:side"));
}
