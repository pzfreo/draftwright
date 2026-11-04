//! Regression scenarios from the Python drafting-rule catalogue. Fixture facts are hand-pinned,
//! rather than measured from whatever the current recogniser happens to return.
use draftwright::model::{self, Analysis, Plan, Spec};
use draftwright::text::Font;
use serde_json::json;
use std::io::Read as _;
use std::path::Path;

fn bytes(name: &str) -> Vec<u8> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/rule_pins")
        .join(format!("{name}.gz"));
    let mut out = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_end(&mut out)
        .unwrap();
    out
}
fn source(name: &str, recognition: Option<&str>) -> (draftwright::haecceity::Part, Analysis) {
    let data = bytes(name);
    if let Some(recognition) = recognition {
        let (part, mut a) = model::analyse_step(Path::new(name), &data).unwrap();
        model::recognise(
            &part,
            &mut a,
            std::str::from_utf8(&bytes(recognition)).unwrap(),
        )
        .unwrap();
        (part, a)
    } else {
        model::analyse_native_step(Path::new(name), &data).unwrap()
    }
}
fn font() -> Font {
    Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(draftwright::dims::FONT),
    )
    .unwrap()
}
fn approx(got: f64, expected: f64) {
    assert!((got - expected).abs() < 1e-3, "{got} != {expected}");
}
fn stated(p: &Plan, feature: &str, parameter: &str, expected: f64) {
    let ds: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .flat_map(|d| &d.measurements)
        .filter(|m| m.feature == feature && m.parameter == parameter)
        .collect();
    assert_eq!(ds.len(), 1, "{feature}/{parameter}: {ds:?}");
    approx(ds[0].value, expected);
}

#[test]
fn tuner_five_pockets_keep_their_sizes_and_locations_through_export() {
    let (part, a) = source(
        "tuner_jig_blind_obround_pockets.step",
        Some("tuner.recognition.json"),
    );
    let pockets = draftwright::lint::pockets::placed(&a);
    assert_eq!(pockets.len(), 5);
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let mut centres = Vec::new();
    for (f, pocket) in pockets {
        let pocket = pocket.unwrap();
        approx(pocket.width, 7.9);
        approx(pocket.length, 13.6);
        approx(pocket.depth, 19.0);
        approx(pocket.centre[0], -5.0);
        approx(pocket.centre[2], 8.5);
        centres.push(pocket.centre[1]);
        for (parameter, value) in [
            ("pocket_width.length", 7.9),
            ("pocket_length.length", 13.6),
            ("pocket_depth.length", 19.0),
            ("location_pocket.location.x", 10.0),
            ("location_pocket.location.y", pocket.centre[1] + 8.0),
        ] {
            stated(&p, &f.id, parameter, value);
        }
    }
    centres.sort_by(f64::total_cmp);
    for (got, expected) in centres.into_iter().zip([21.2, 48.4, 75.6, 102.8, 130.0]) {
        approx(got, expected);
    }
    let blends: Vec<_> = a.features.iter().filter(|f| f.kind == "blend").collect();
    assert_eq!(
        blends.len(),
        20,
        "the twenty rounded corners are fixture facts too"
    );
    for blend in blends {
        approx(blend.parameters["radius"].as_f64().unwrap(), 3.94);
        let claims: Vec<_> = p
            .notes
            .iter()
            .filter(|n| n.placed.is_some())
            .flat_map(|n| &n.measurements)
            .chain(
                p.dimensions
                    .iter()
                    .filter(|d| d.placed.is_some())
                    .flat_map(|d| &d.measurements),
            )
            .filter(|m| m.feature == blend.id && m.parameter == "blend.radius")
            .collect();
        assert_eq!(
            claims.len(),
            1,
            "{} must have one stated corner radius",
            blend.id
        );
        approx(claims[0].value, 3.94);
    }
    let d = model::render_with(&part, &a, &p, &font(), &mut model::Projections::default()).unwrap();
    assert_eq!(
        d.text
            .iter()
            .filter(|r| r.text == "7.9 × 13.6 × 19 DEEP")
            .count(),
        5
    );
    assert!(
        d.text
            .iter()
            .any(|r| r.text.contains("ALL UNSPECIFIED RADII R3.9"))
    );
    assert!(d.svg().contains("<svg"));
    assert!(d.dxf().len() > 100);
}

#[test]
fn grm04_small_hole_retains_diameter_and_location_on_a_clean_sheet() {
    let (part, a) = source("grm04_drive_plate.step", Some("grm04.recognition.json"));
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let (f, _) = draftwright::bridge::holes::read(&a)
        .into_iter()
        .find(|(_, h)| h.as_ref().is_ok_and(|h| (h.diameter - 2.4).abs() < 1e-6))
        .unwrap();
    stated(&p, &f.id, "bore.diameter", 2.4);
    let locations: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .flat_map(|d| &d.measurements)
        .filter(|m| m.feature == f.id && m.parameter.starts_with("location.length."))
        .collect();
    assert!(
        !locations.is_empty(),
        "hole has no location: {:?}",
        p.dimensions
    );
    let d = model::render_with(&part, &a, &p, &font(), &mut model::Projections::default()).unwrap();
    let issues = draftwright::lint::structural::check(&d);
    assert!(issues.is_empty(), "{issues:?}");
    assert!(
        !p.problems.iter().any(|i| i.code.contains("overlap")
            || i.code.contains("outside")
            || i.code.ends_with("_dropped")),
        "{:?}",
        p.problems
    );
}

/// GRM-04's hub face meets the plate's taper, the common tangent of R3 and R4 about the two
/// located holes. Tangency plus those stated circles fix the 96° corners; without the located
/// centres the corners are required again.
#[test]
fn grm04_taper_corners_are_determined_by_tangency_to_located_circles() {
    let (_, mut a) = source("grm04_drive_plate.step", None);
    assert!(draftwright::lint::profile_angles::requirements(&a).is_empty());
    a.features
        .retain(|f| !matches!(f.kind.as_str(), "hole" | "boss"));
    let left = draftwright::lint::profile_angles::requirements(&a);
    assert_eq!(left.len(), 2);
    assert!(left.iter().all(|c| (c.degrees() - 96.0).abs() < 0.1));
}

#[test]
fn opposed_coaxial_taps_keep_authored_left_and_right_callouts() {
    // The archived opposed.step has a sealed M2-sized cavity, not two taps.
    // Preserve this two-opening placement pin on independently authored geometry.
    let (part, a) = model::analyse_native_step(
        Path::new("synthetic_opposed_taps.step"),
        include_bytes!("fixtures/parts/synthetic_opposed_taps.step"),
    )
    .unwrap();
    assert!(part.solid_is_geometrically_valid(0));
    assert!(a.features.iter().all(|f| f.kind != "interior_void"));
    let mut holes: Vec<_> = draftwright::bridge::holes::read(&a)
        .into_iter()
        .map(|(f, h)| (f, h.unwrap()))
        .collect();
    holes.sort_by(|(_, a), (_, b)| a.diameter.total_cmp(&b.diameter));
    assert_eq!(holes.len(), 2);
    assert_eq!([holes[0].1.at[2], holes[1].1.at[2]], [20., 0.]);
    assert!(holes[0].1.direction[2] * holes[1].1.direction[2] < 0.);
    let mut s: Spec = serde_json::from_value(
        json!({"sheet":{"size":"A3","scale":"2:1","title":{"tolerance":"ISO 2768-m"}}}),
    )
    .unwrap();
    for ((f, h), (designation, side, diameter, depth)) in holes
        .iter()
        .zip([("M2x0.4", "left", 2., 6.), ("M4x0.7", "right", 4., 3.)])
    {
        approx(h.diameter, diameter);
        approx(h.depth.expect("blind-hole depth"), depth);
        s.features.insert(
            f.id.clone(),
            serde_json::from_value(json!({"thread":{"designation":designation}})).unwrap(),
        );
        s.dimensions.insert(
            format!("d:{}/bore.diameter", f.id),
            serde_json::from_value(json!({"place":{"view":"plan","side":side}})).unwrap(),
        );
    }
    let p = model::plan_recovered(&a, &s).unwrap();
    for ((f, _), (side, label)) in holes
        .iter()
        .zip([("left", "M2\n⌀2 ↧ 6"), ("right", "M4\n⌀4 ↧ 3")])
    {
        let d = p
            .dimensions
            .iter()
            .find(|d| d.id == format!("d:{}/bore.diameter", f.id))
            .unwrap();
        assert_eq!(d.label, label);
        let v = p.views.iter().find(|v| v.id == d.view).unwrap();
        let elbow = d.placed.as_ref().unwrap().lines[0][1][0];
        let half = 20.0 * v.scale;
        assert!(
            if side == "left" {
                elbow < v.at[0] - half
            } else {
                elbow > v.at[0] + half
            },
            "{side}: {d:?}"
        );
    }
    let d = model::render_with(&part, &a, &p, &font(), &mut model::Projections::default()).unwrap();
    let effective = draftwright::spec_gdt::effective(&a, &s).unwrap();
    let issues: Vec<_> = draftwright::lint::lint(&effective, &d)
        .into_iter()
        .chain(draftwright::lint::structural::check(&d))
        .collect::<Vec<_>>()
        .into_iter()
        .filter(|i| i.severity != draftwright::lint::Severity::Info)
        .collect();
    assert!(issues.is_empty(), "{issues:?}");
    // The M2 opens on top and is visible in plan; only the M4 opens below.
    // Keep the hidden-ink warning for that real underside opening, not for the top tap.
    let mut problems: Vec<_> = p
        .problems
        .iter()
        .filter(|i| i.severity != "info")
        .map(|i| (i.code.as_str(), i.subjects.clone()))
        .collect();
    problems.sort();
    assert_eq!(
        problems,
        holes
            .iter()
            .filter(|(_, h)| h.at[2] == 0.)
            .map(|(f, _)| (
                "leader_to_hidden_edge",
                vec![format!("d:{}/bore.diameter", f.id), "v:plan".to_string()]
            ))
            .collect::<std::collections::BTreeSet<_>>()
            .into_iter()
            .collect::<Vec<_>>(),
    );
}

#[test]
fn ctc05_step_encodings_have_the_same_true_turned_profile() {
    let profile = |encoding| {
        let (_, a) = source(&format!("nist_ctc_05_asme1_{encoding}.stp"), None);
        let mut steps: Vec<_> = a
            .features
            .iter()
            .filter(|f| f.kind == "turned_step")
            .map(|f| {
                let p = &f.parameters;
                (
                    p["axis"].as_str().unwrap().to_owned(),
                    p["diameter"].as_f64().unwrap(),
                    p["lo"].as_f64().unwrap(),
                    p["hi"].as_f64().unwrap(),
                )
            })
            .collect();
        steps.sort_by(|a, b| a.1.total_cmp(&b.1));
        steps
    };
    let a = profile("ap203");
    let b = profile("ap242");
    assert_eq!(a.len(), 3, "{a:?}");
    assert_eq!(b.len(), 3, "{b:?}");
    for ((a, b), expected) in a.iter().zip(&b).zip([
        (63.5, 279.4, 482.6),
        (304.8, 25.4, 127.0),
        (558.8, 0.0, 25.4),
    ]) {
        assert_eq!(a.0, "z");
        assert_eq!(b.0, "z");
        for (got, other, expected) in [
            (a.1, b.1, expected.0),
            (a.2, b.2, expected.1),
            (a.3, b.3, expected.2),
        ] {
            approx(got, expected);
            approx(other, expected);
        }
    }
}

#[test]
fn fixed_paper_typography_survives_scale_and_sheet_fit() {
    let mut escalated = false;
    let mut fitted_iso = false;
    for (file, recognition) in [
        ("grm04_drive_plate.step", Some("grm04.recognition.json")),
        (
            "tuner_jig_blind_obround_pockets.step",
            Some("tuner.recognition.json"),
        ),
        ("opposed.step", None),
    ] {
        let (part, a) = source(file, recognition);
        let mut policies = std::collections::BTreeMap::new();
        for scale in [None, Some("1:1"), Some("2:1"), Some("10:1")] {
            let s: Spec = match scale {
                None => Spec::default(),
                Some(scale) => {
                    serde_json::from_value(json!({"sheet":{"size":"A4","scale":scale}})).unwrap()
                }
            };
            let p = model::plan_recovered(&a, &s).unwrap();
            // An automatic choice records its recovery from the first estimate in the
            // sheet basis; layout_replanned belongs to kept layouts and local fixes.
            escalated |= p.problems.iter().any(|p| p.code == "layout_replanned")
                || p.sheet
                    .basis
                    .split_once("first estimate ")
                    .is_some_and(|(_, from)| {
                        let words: Vec<_> = from.split_whitespace().collect();
                        (words[0], words[2]) != (p.sheet.size.as_str(), p.sheet.scale.as_str())
                    });
            fitted_iso |= p
                .views
                .iter()
                .any(|v| v.id == "v:iso" && (v.scale - p.sheet.scale_factor).abs() > 1e-6);
            let d = model::render_with(&part, &a, &p, &font(), &mut model::Projections::default())
                .unwrap();
            assert!(!d.text.is_empty());
            for run in &d.text {
                // Title-field labels are a deliberately fixed smaller style, never a fit result.
                assert!([3.0, 1.5].contains(&run.size), "{file}/{scale:?}: {run:?}");
                if let Some(size) = policies.insert(run.text.clone(), run.size) {
                    assert_eq!(size, run.size, "{}", run.text);
                }
            }
            for annotation in &d.annotations {
                for &i in &annotation.runs {
                    assert_eq!(d.text[i].size, 3.0);
                }
            }
        }
    }
    assert!(escalated, "guard must exercise sheet/scale recovery");
    assert!(fitted_iso, "guard must exercise fitted views");
}

/// ARCH0020-1, partial: native recognition keeps these intrinsic sizes under the Python
/// canary's general rigid motion. View-frame invariance remains pending: raw planning still
/// uses the caller-axis envelope. Do not mistake this pin for a framed-drawing guarantee.
#[test]
fn rigid_motion_preserves_intrinsic_native_feature_sizes() {
    let (_, original) = source("framed.step", None);
    let (_, moved) = source("framed-moved.step", None);
    let sizes = |a: &Analysis| {
        a.features
            .iter()
            .map(|f| (f.kind.clone(), f.fingerprint.sizes.clone()))
            .collect::<Vec<_>>()
    };
    // This fixture also has a closed rectangular cavity, formerly unconsumed.
    // Its newly retained record must not invalidate the real through-hole pin.
    assert_eq!(original.features.len(), 2);
    assert_eq!(
        original
            .features
            .iter()
            .map(|f| f.kind.as_str())
            .collect::<Vec<_>>(),
        vec!["hole", "interior_void"]
    );
    assert!(
        original.features[1].parameters["openings"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(original.features[0].kind, "hole");
    approx(original.features[0].fingerprint.sizes["diameter"], 8.0);
    approx(original.features[0].fingerprint.sizes["depth"], 20.0);
    assert_eq!(sizes(&original), sizes(&moved));
    for key in ["estimated_volume", "grid_pitch"] {
        approx(
            original.features[1].parameters[key].as_f64().unwrap(),
            moved.features[1].parameters[key].as_f64().unwrap(),
        );
    }
}

#[test]
fn the_archived_opposed_fixture_has_one_open_hole_and_one_closed_cavity() {
    for recognition in [None, Some("opposed.recognition.json")] {
        let (_, a) = source("opposed.step", recognition);
        let holes = draftwright::bridge::holes::read(&a);
        assert_eq!(holes.len(), 1);
        let h = holes[0].1.as_ref().unwrap();
        approx(h.diameter, 4.);
        approx(h.depth.unwrap(), 3.);
        let voids: Vec<_> = a
            .features
            .iter()
            .filter(|f| f.kind == "interior_void")
            .collect();
        assert_eq!(voids.len(), 1);
        assert!(
            voids[0].parameters["openings"]
                .as_array()
                .unwrap()
                .is_empty()
        );
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert!(p.dimensions.iter().all(|d| {
            !d.measurements
                .iter()
                .any(|m| m.parameter == "bore.diameter" && m.value == 2.)
        }));
        let findings = draftwright::lint::check(&a, &[]);
        assert_eq!(
            findings
                .iter()
                .filter(|f| f.code == "interior_void")
                .count(),
            1
        );
    }
}
