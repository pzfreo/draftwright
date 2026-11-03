//! Section recess dimensions (`src/dims/recesses.rs`): pockets, hexagonal pockets, circular
//! seats and open channels on the sampled sheets' parts, with the features draftwright's own
//! recogniser found for them (`tests/fixtures/recognition/sheets/`), checked against the labels
//! draftwright prints for them (`tests/fixtures/sheets/`): what each states, in which view, and
//! that it is drawn. The item-by-item comparison with draftwright's sheets is
//! `tests/dimensions.rs`; blind slots, and the pockets beside them on other corpus parts, are in
//! `tests/dims_slots.rs`; a cylindrical mouth, an edge-open pocket and a pocket on the box are the
//! module's unit tests.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::io::Read as _;
use std::path::Path;

use draftwright::model::{self, Analysis, Plan, Spec};

fn analysed(corpus: &Path, name: &str) -> Analysis {
    let f = facts::load(name);
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("tests/fixtures/recognition/sheets/{name}.json.gz"));
    let mut json = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_string(&mut json)
        .unwrap();
    let (part, mut a) = model::analyse(&corpus.join(&f.binding.file)).unwrap();
    let (r, features) = draftwright::bridge::read(&part, &a.binding.source_sha256, &json).unwrap();
    a.recognition = Some(r);
    a.features = features;
    a
}

fn recesses(a: &Analysis) -> Vec<&str> {
    a.features
        .iter()
        .filter(|f| f.kind == "section_recess")
        .map(|f| f.id.as_str())
        .collect()
}

/// The plan's items that state a section recess's measurement, as (parameter, view, label),
/// sorted.
fn stating(plan: &Plan, a: &Analysis) -> Vec<(String, String, String)> {
    let ids = recesses(a);
    let mut out: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| ids.contains(&m.feature.as_str()))
        })
        .flat_map(|d| {
            let parameters = if d.id.starts_with("d:f:section_recess:") {
                vec![d.id.split_once('/').unwrap().1.to_string()]
            } else {
                // A coincident overall span can carry a recess location. Check the same
                // physical statement even when its drawing-owned carrier has another ID.
                d.measurements
                    .iter()
                    .filter(|m| ids.contains(&m.feature.as_str()))
                    .map(|m| m.parameter.clone())
                    .collect()
            };
            parameters
                .into_iter()
                .map(|parameter| {
                    (
                        parameter,
                        d.view.trim_start_matches("v:").to_string(),
                        d.label.clone(),
                    )
                })
                .collect::<Vec<_>>()
        })
        .collect();
    out.sort();
    out
}

fn strs(v: &[(&str, &str, &str)]) -> Vec<(String, String, String)> {
    v.iter()
        .map(|(a, b, c)| (a.to_string(), b.to_string(), c.to_string()))
        .collect()
}

/// The recesses reported `dimensions_not_ported`.
fn not_ported(plan: &Plan, a: &Analysis) -> Vec<String> {
    let ids = recesses(a);
    let mut out: Vec<String> = plan
        .problems
        .iter()
        .filter(|p| p.code == "dimensions_not_ported")
        .flat_map(|p| p.subjects.iter())
        .filter(|s| ids.contains(&s.as_str()))
        .cloned()
        .collect();
    out.sort();
    out
}

/// Every recess item is drawn and says why it is there; and the recesses it states are not
/// also reported undimensioned.
fn drawn_and_claimed(plan: &Plan, a: &Analysis) {
    let reported = not_ported(plan, a);
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:f:section_recess:"))
    {
        assert!(d.placed.is_some(), "{} is not drawn", d.id);
        assert!(d.default && !d.basis.is_empty(), "{d:?}");
        for m in &d.measurements {
            assert!(!reported.contains(&m.feature), "{}: {}", d.id, m.feature);
        }
    }
}

#[test]
fn m1000_pocket_hex_pocket_and_seat_are_stated_as_draftwright_states_them() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "m1000");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    // draftwright's sheet (tests/fixtures/sheets/m1000.json.gz) prints each of these, in the
    // same view, but for the pocket's y location, which it sends to the side view (ADR1-6: the
    // port keeps a pocket's position in the view that looks into it; known_dimensions.json).
    assert_eq!(
        stating(&plan, &a),
        strs(&[
            ("location_pocket.location.x", "plan", "39.335"),
            ("location_pocket.location.y", "plan", "10.187"),
            ("pocket_width.length", "plan", "2 × 2.81 × 3.446 DEEP"),
            (
                "polygon_across_flats.length",
                "side",
                "HEX 1.935 A/F × 2.104 DEEP"
            ),
            (
                "seat_diameter.diameter",
                "front",
                "ø2 × 12.593 LONG × 180° ARC"
            ),
            ("seat_location.location.x", "front", "48.414"),
            ("seat_location.location.y", "side", "6.297"),
            ("seat_location.location.z", "front", "23.139"),
        ])
    );
    let pocket = plan
        .dimensions
        .iter()
        .find(|d| d.label.ends_with("DEEP") && !d.label.starts_with("HEX"))
        .unwrap();
    let parameters: Vec<&str> = pocket
        .measurements
        .iter()
        .map(|m| m.parameter.as_str())
        .collect();
    assert_eq!(
        parameters,
        [
            "pocket_width.length",
            "pocket_length.length",
            "pocket_depth.length"
        ]
    );
    drawn_and_claimed(&plan, &a);
    // The triangular pocket and the four passages have no drafting grammar in draftwright (its
    // lint reports them unsupported): still reported here, one each.
    assert_eq!(not_ported(&plan, &a).len(), recesses(&a).len() - 3);
}

#[test]
fn m10046_pocket_is_called_out_and_located_in_the_view_that_looks_into_it() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "m10046");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    // draftwright prints the callout; it drops both locations (its lint: pocket_dim_dropped, "no
    // room beside the front", and pocket_not_located), whose values it gives as 22.963 and 7.691.
    assert_eq!(
        stating(&plan, &a),
        strs(&[
            ("location_pocket.x", "front", "22.963"),
            ("location_pocket.z", "front", "7.691"),
            (
                "pocket_width.length",
                "front",
                "5.042 × 11.698 × 10.559 DEEP"
            ),
        ])
    );
    // A sloping witness still passes through its arrow tip, including the
    // two-millimetre overshoot beyond the dimension line.
    for d in &plan.dimensions {
        if let Some(p) = &d.placed
            && p.arrows.len() == 2
            && p.lines.len() >= 3
        {
            for (witness, arrow) in p.lines[..2].iter().zip(&p.arrows) {
                let [a, b] = *witness;
                let t = arrow[0];
                let cross = (t[0] - a[0]) * (b[1] - a[1]) - (t[1] - a[1]) * (b[0] - a[0]);
                assert!(cross.abs() < 1e-6, "{}: arrow misses witness", d.id);
            }
        }
    }
    drawn_and_claimed(&plan, &a);
    assert_eq!(not_ported(&plan, &a).len(), recesses(&a).len() - 1);
}

#[test]
fn m10127_hex_pocket_states_its_size_across_flats_and_depth() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "m10127");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        stating(&plan, &a),
        strs(&[(
            "polygon_across_flats.length",
            "front",
            "HEX 4.236 A/F × 9.96 DEEP"
        )])
    );
    let hex = &plan
        .dimensions
        .iter()
        .find(|d| d.label.starts_with("HEX"))
        .unwrap();
    // draftwright's values: 4.236456 across flats, 9.959 deep. Its quiddity call publishes the
    // record in the part's coordinates, the fixture in quiddity's own frame, each rounded to
    // 0.001 mm there, so the depth (the run interval's length) reads 9.960 here: the same label.
    let parameters: Vec<&str> = hex
        .measurements
        .iter()
        .map(|m| m.parameter.as_str())
        .collect();
    assert_eq!(
        parameters,
        ["polygon_across_flats.length", "pocket_depth.length"]
    );
    for (m, python) in hex.measurements.iter().zip([4.236456, 9.959]) {
        assert!((m.value - python).abs() <= 0.001 + 1e-9, "{m:?}");
    }
    drawn_and_claimed(&plan, &a);
    assert!(not_ported(&plan, &a).is_empty());
}

#[test]
fn ftc06_channels_state_their_widths_in_the_view_along_them() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "ftc06");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    // A base and walls (quiddity's plates along two axes): each open channel's width, as
    // draftwright's seven dim_channel_* measurements, explicitly counted per projection.
    assert_eq!(
        stating(&plan, &a),
        strs(&[
            // Distinct modelled widths must no longer merge through one-decimal text.
            ("channel_width.length", "front", "3.102"),
            ("channel_width.length", "front", "3.103"),
            ("channel_width.length", "front", "3.132"),
            ("channel_width.length", "plan", "2× 12.7"),
            ("channel_width.length", "side", "2× 3.103"),
        ])
    );
    let claims: Vec<_> = plan
        .dimensions
        .iter()
        .flat_map(|d| &d.measurements)
        .filter(|m| m.parameter == "channel_width.length")
        .collect();
    assert_eq!(claims.len(), 7, "counting must retain every physical width");
    let owners: std::collections::BTreeSet<_> = claims.iter().map(|m| &m.feature).collect();
    assert_eq!(owners.len(), 7, "one independent width claim per channel");
    for d in plan.dimensions.iter().filter(|d| d.id.contains("channel")) {
        assert_eq!(d.kind, "linear", "{d:?}");
    }
    drawn_and_claimed(&plan, &a);
    // Its two 12- and 20-sided pockets have no drafting grammar (draftwright: "general pocket
    // requires a rounded rectangular profile").
    assert_eq!(not_ported(&plan, &a).len(), 2);
}

#[test]
fn ctc01_recesses_draftwright_does_not_dimension_stay_reported() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "ctc01");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    // Its plates lie along one axis, so its five channels are rebates the step ladder owns, as in
    // draftwright (which prints no channel width there); its two pockets are 12- and 20-sided.
    assert!(stating(&plan, &a).is_empty(), "{:?}", stating(&plan, &a));
    assert_eq!(not_ported(&plan, &a).len(), 7);
}

/// m1000 with a twin of its hexagonal pocket beside it, 5 mm away across its axis, and a twin of
/// its circular seat further along the same axis.
fn twinned(corpus: &Path) -> (Analysis, String, String) {
    let mut a = analysed(corpus, "m1000");
    let of = |a: &Analysis, kind: &str, shape: &str| {
        a.features
            .iter()
            .find(|f| {
                f.parameters.get("classification")
                    == Some(&serde_json::json!({ "feature_kind": kind, "section_shape": shape }))
            })
            .unwrap()
            .clone()
    };
    let mut hex = of(&a, "pocket", "hexagonal");
    let mut seat = of(&a, "channel", "circular");
    let (hex_id, seat_id) = (hex.id.clone(), seat.id.clone());
    hex.id = "f:section_recess:98".into();
    let g = &mut hex.parameters["geometry"]["frame"]["origin"][0];
    *g = serde_json::json!(g.as_f64().unwrap() + 5.0);
    seat.id = "f:section_recess:99".into();
    let run = &mut seat.parameters["geometry"]["run_interval"];
    let [lo, hi] = [run[0].as_f64().unwrap(), run[1].as_f64().unwrap()];
    *run = serde_json::json!([lo + 20.0, hi + 20.0]);
    a.features.extend([hex, seat]);
    (a, hex_id, seat_id)
}

#[test]
fn identical_hex_pockets_and_coaxial_seats_are_counted_once() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (a, hex, seat) = twinned(&corpus);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let by = |id: &str| {
        plan.dimensions
            .iter()
            .find(|d| d.id.starts_with(&format!("d:{id}/")) && d.kind == "callout")
            .unwrap()
    };
    // draftwright batches hexagonal pockets by axis, opening and label, and coaxial seats by
    // axis, centre and label (`polygonal_prism_jobs`, `circular_recess_jobs`).
    let h = by(&hex);
    assert_eq!(h.label, "2× HEX 1.935 A/F × 2.104\nDEEP");
    let features: Vec<&str> = h.measurements.iter().map(|m| m.feature.as_str()).collect();
    assert_eq!(
        features,
        [
            hex.as_str(),
            hex.as_str(),
            "f:section_recess:98",
            "f:section_recess:98"
        ]
    );
    let s = by(&seat);
    assert_eq!(s.label, "2× ø2 × 12.593 LONG × 180°\nARC");
    assert_eq!(s.measurements.len(), 6);
    // Across the axis the two seats share their locations; along it each has its own.
    let located: Vec<(String, usize)> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter.starts_with("seat_location."))
        })
        .map(|d| {
            (
                d.label.clone(),
                d.measurements
                    .iter()
                    .filter(|m| m.parameter.starts_with("seat_location."))
                    .count(),
            )
        })
        .collect();
    assert_eq!(
        located,
        [
            ("48.414".to_string(), 2),
            ("23.139".to_string(), 2),
            ("6.297".to_string(), 1),
            ("26.297".to_string(), 1),
        ]
    );
}

#[test]
fn recess_items_can_be_omitted_by_id_and_planning_is_deterministic() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "m1000");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let ids: Vec<String> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:f:section_recess:"))
        .map(|d| d.id.clone())
        .chain(
            plan.omitted
                .iter()
                .filter(|o| {
                    o.id.starts_with("d:f:section_recess:")
                        && matches!(o.state, draftwright::items::State::Covered { .. })
                })
                .map(|o| o.id.clone()),
        )
        .collect();
    assert_eq!(
        ids.len(),
        8,
        "all eight recess items remain addressable, including the location conveyed by the overall span: {ids:?}"
    );
    for id in &ids {
        let mut spec = Spec::default();
        spec.dimensions.insert(
            id.clone(),
            serde_json::from_str(r#"{"omit": true}"#).unwrap(),
        );
        let again = model::plan(&a, &spec).unwrap();
        assert!(again.dimensions.iter().all(|d| d.id != *id));
        assert!(again.omitted.iter().any(|o| o.id == *id));
        // Omitting one moves no other (ARCH0003-2).
        for d in &again.dimensions {
            let before = plan.dimensions.iter().find(|b| b.id == d.id).unwrap();
            assert_eq!(
                d.placed, before.placed,
                "{} moved when {id} was omitted",
                d.id
            );
        }
    }
    assert_eq!(
        serde_json::to_string(&plan).unwrap(),
        serde_json::to_string(&model::plan(&a, &Spec::default()).unwrap()).unwrap()
    );
}

/// A counted form must retain separately located seats, including when its
/// carrier is omitted and a remaining seat has to carry its own full form.
#[test]
fn identical_located_seats_keep_all_claims_and_promote_a_survivor() {
    let Some(corpus) = corpus::dir() else { return };
    let (mut a, _, original) = twinned(&corpus);
    let twin = a
        .features
        .iter_mut()
        .find(|f| f.id == "f:section_recess:99")
        .unwrap();
    let run = twin.parameters["geometry"]["frame"]["run"]
        .as_array()
        .unwrap();
    let across = run
        .iter()
        .position(|c| c.as_f64().unwrap().abs() < 0.5)
        .unwrap();
    let x = &mut twin.parameters["geometry"]["frame"]["origin"][across];
    *x = serde_json::json!(x.as_f64().unwrap() + 5.0);
    let full = model::plan(&a, &Spec::default()).unwrap();
    let carrier = full
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == original && m.parameter == "seat_diameter.diameter")
        })
        .unwrap();
    assert_eq!(carrier.label, "2× ø2 × 12.593 LONG × 180°\nARC");
    for feature in [&original[..], "f:section_recess:99"] {
        for parameter in [
            "seat_diameter.diameter",
            "seat_run.length",
            "seat_sweep.angle",
        ] {
            assert_eq!(
                carrier
                    .measurements
                    .iter()
                    .filter(|m| m.feature == feature && m.parameter == parameter)
                    .count(),
                1
            );
        }
        assert!(full.dimensions.iter().any(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == feature && m.parameter.starts_with("seat_location."))
        }));
    }
    assert!(full.omitted.iter().any(|o| o.id == "d:f:section_recess:99/seat_diameter.diameter"
        && matches!(&o.state, draftwright::items::State::Covered { by } if by == &carrier.id)));
    let mut spec = Spec::default();
    spec.dimensions.entry(carrier.id.clone()).or_default().omit = true;
    let edited = model::plan(&a, &spec).unwrap();
    let promoted = edited
        .dimensions
        .iter()
        .find(|d| {
            d.measurements.iter().any(|m| {
                m.feature == "f:section_recess:99" && m.parameter == "seat_diameter.diameter"
            })
        })
        .unwrap();
    assert_eq!(promoted.label, "ø2 × 12.593 LONG × 180° ARC");
    assert!(promoted.placed.is_some());
}

#[test]
fn coincident_pattern_and_pocket_centres_share_location_ink_and_claims() {
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(
        include_bytes!("fixtures/parts/electronics-enclosure-base.step.gz").as_slice(),
    )
    .read_to_end(&mut bytes)
    .unwrap();
    let (_, a) = model::analyse_native_step(Path::new("enclosure.step"), &bytes).unwrap();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(p.sheet.size, "A4");
    for (axis, value) in [("x", 50.), ("y", 35.)] {
        let pattern = format!("location.length.{axis}");
        let pocket = format!("location_pocket.location.{axis}");
        let stated: Vec<_> = p
            .dimensions
            .iter()
            .filter(|d| {
                d.measurements
                    .iter()
                    .any(|m| m.parameter == pattern || m.parameter == pocket)
            })
            .collect();
        assert_eq!(
            stated.len(),
            1,
            "{axis}: {:?}",
            stated.iter().map(|d| &d.id).collect::<Vec<_>>()
        );
        assert!(stated[0].placed.is_some());
        assert!(
            stated[0]
                .measurements
                .iter()
                .any(|m| m.parameter == pattern && (m.value - value).abs() < 1e-6)
        );
        assert!(
            stated[0]
                .measurements
                .iter()
                .any(|m| m.parameter == pocket && (m.value - value).abs() < 1e-6)
        );
    }
    assert!(p.dimensions.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| m.parameter == "grid_pitch.length.row" && (m.value - 50.).abs() < 1e-6)
    }));
}
