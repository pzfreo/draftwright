//! Slot and boss dimensions (`src/dims/slots.rs`, `src/dims/bosses.rs`) on the sampled sheets'
//! parts, with the features draftwright's own recogniser found for them
//! (`tests/fixtures/recognition/sheets/`), and on corpus parts with features no sampled sheet has
//! (`tests/fixtures/recognition/dims/`: nist_ftc_07's square bosses, mfcadpp blind slots), and on
//! two fixture plates with slot arrays (`tests/fixtures/recognition/slot_patterns/`), checked
//! against the labels draftwright prints for them: what each states, in which view, and that it
//! is drawn. The item-by-item comparison with draftwright's sheets is `tests/dimensions.rs`.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::io::Read as _;
use std::path::Path;

use draftwright::dims::PlannedDimension;
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

/// The plan's items whose parameter starts with *prefix*, as (parameter, view, label), sorted.
fn items(plan: &Plan, prefix: &str) -> Vec<(String, String, String)> {
    let mut out: Vec<_> = plan
        .dimensions
        .iter()
        .filter_map(|d| {
            let (_, parameter) = d.id.split_once('/')?;
            let view = d.view.trim_start_matches("v:");
            parameter
                .starts_with(prefix)
                .then(|| (parameter.to_string(), view.to_string(), d.label.clone()))
        })
        .collect();
    out.sort();
    out
}

fn of_kind<'a>(a: &'a Analysis, kind: &str) -> Vec<&'a str> {
    a.features
        .iter()
        .filter(|f| f.kind == kind)
        .map(|f| f.id.as_str())
        .collect()
}

fn not_ported(plan: &Plan, ids: &[&str]) -> Vec<String> {
    plan.problems
        .iter()
        .filter(|p| p.code == "dimensions_not_ported")
        .filter(|p| p.subjects.iter().any(|s| ids.contains(&s.as_str())))
        .map(|p| p.id.clone())
        .collect()
}

fn drawn(plan: &Plan, d: &PlannedDimension) {
    assert!(
        d.placed.is_some(),
        "{} is not drawn: {:#?}",
        d.id,
        plan.problems
            .iter()
            .filter(|p| p.subjects.contains(&d.id))
            .collect::<Vec<_>>()
    );
}

fn strs(v: &[(&str, &str, &str)]) -> Vec<(String, String, String)> {
    v.iter()
        .map(|(a, b, c)| (a.to_string(), b.to_string(), c.to_string()))
        .collect()
}

#[test]
fn cgb245_slots_state_width_length_end_radius_and_location() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "cgb245");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    // Three through slots in the plan: two obrounds (6 × 15.0, ends R3) and one straight slot
    // (7.0 × 16.9, corner radius 2: no end radius), each located from the box along x, and across
    // in y by its centre line: one at 38.9; two share 50.4, which is a hole's y location in the
    // same view, so that states it (ARCH0009-6). The two obrounds are congruent, so they are
    // dimensioned as slots: one `2× SLOT 6 × 14.97` claims both widths and lengths, their
    // full-round ends (R3, half the width) need no radius, and each is located along x by its
    // centre. The straight slot keeps its own width and length, and its low end's location.
    assert_eq!(
        items(&plan, "slot_"),
        strs(&[
            ("slot_length.length", "plan", "16.86"),
            ("slot_width.length", "plan", "2× SLOT 6 × 14.97"),
            ("slot_width.length", "plan", "6.98"),
        ])
    );
    let callout = plan
        .dimensions
        .iter()
        .find(|d| d.label == "2× SLOT 6 × 14.97")
        .unwrap();
    assert_eq!(callout.kind, "callout", "{callout:?}");
    let mut stated: Vec<_> = callout
        .measurements
        .iter()
        .map(|m| (m.feature.as_str(), m.parameter.as_str()))
        .collect();
    stated.sort();
    assert_eq!(
        stated,
        [
            ("f:slot:f13", "slot_length.length"),
            ("f:slot:f13", "slot_width.length"),
            ("f:slot:f21", "slot_length.length"),
            ("f:slot:f21", "slot_width.length"),
        ]
    );
    assert_eq!(
        items(&plan, "location_slot"),
        strs(&[
            ("location_slot.length", "plan", "181.57"),
            ("location_slot.length", "plan", "293.445"),
            ("location_slot.length", "plan", "86.555"),
            ("location_slot_across.length", "plan", "38.866"),
        ])
    );
    // The obrounds' locations measure to their centres, half a length (7.485) beyond their low
    // ends (79.070 and 285.960 before they were grouped).
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/location_slot.length") && d.label != "181.57")
    {
        assert!(d.basis.contains("to its centre"), "{}", d.basis);
    }
    let conveyed: Vec<_> = plan
        .omitted
        .iter()
        .filter(|o| o.id.ends_with("/location_slot_across.length"))
        .collect();
    assert_eq!(conveyed.len(), 1, "{:#?}", plan.omitted);
    assert!(conveyed[0].basis.contains("ARCH0009-6"));
    let basis = &conveyed[0].basis;
    let owner = &basis["conveyed by ".len()..basis.find(": ").unwrap()];
    let hole = plan.dimensions.iter().find(|d| d.id == owner).unwrap();
    assert!(
        hole.id.ends_with("/location.length.y") && hole.view == "v:plan" && hole.label == "50.356",
        "{hole:?}"
    );
    for d in plan.dimensions.iter().filter(|d| d.id.contains("slot")) {
        drawn(&plan, d);
        assert!(d.default && !d.basis.is_empty(), "{d:?}");
    }
    assert_eq!(
        not_ported(&plan, &of_kind(&a, "slot")),
        Vec::<String>::new()
    );
}

#[test]
fn ctc01_slot_locations_are_exact() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "ctc01");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    // quiddity's ends are rounded to 0.01 in its own frame; the faces' box gives 55, not 54.998.
    let mut values: Vec<f64> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/location_slot.length"))
        .map(|d| d.value)
        .collect();
    values.sort_by(f64::total_cmp);
    assert_eq!(values.len(), 2);
    assert!(
        (values[0] - 55.0).abs() < 1e-6 && (values[1] - 635.0).abs() < 1e-6,
        "{values:?}"
    );
    assert_eq!(
        items(&plan, "slot_end_radius"),
        strs(&[("slot_end_radius.radius", "plan", "2× R20")])
    );
}

#[test]
fn ftc06_bosses_are_counted_called_out_and_their_heights_stated() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "ftc06");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        items(&plan, "boss"),
        strs(&[
            ("boss.diameter", "front", "2× ø22.86"),
            ("boss.diameter", "front", "2× ø31.75"),
            ("boss.diameter", "plan", "4× ø44.45"),
            // Each set of bosses stands to one height across one span: one counted dimension
            // claiming every boss (src/dims/mod.rs::share), where draftwright's sheet repeats
            // the same span four (and twice two) times.
            ("boss_height.length", "front", "4× 38.1"),
            ("boss_height.length", "side", "2× 12.07"),
            ("boss_height.length", "side", "2× 16.34"),
        ])
    );
    let heights: usize = plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/boss_height.length"))
        .map(|d| {
            d.measurements
                .iter()
                .filter(|m| m.parameter == "boss_height.length")
                .count()
        })
        .sum();
    assert_eq!(heights, 8);
    let shared = plan
        .dimensions
        .iter()
        .find(|d| d.label == "4× 38.1")
        .unwrap();
    assert_eq!(shared.measurements.len(), 5);
    assert!(
        shared
            .measurements
            .iter()
            .any(|m| m.feature == "step_level" && m.parameter == "step_height.length.f11")
    );
    let bosses = of_kind(&a, "boss");
    for d in plan.dimensions.iter().filter(|d| {
        d.measurements
            .iter()
            .any(|m| bosses.contains(&m.feature.as_str()))
    }) {
        drawn(&plan, d);
    }
    assert_eq!(not_ported(&plan, &bosses), Vec::<String>::new());
}

#[test]
fn a_turned_parts_coaxial_bosses_are_profile_bands_and_a_groove_floor_is_the_grooves() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "connector109");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    // Five boss records: the ø122 × 6 one is groove 217's floor, so four bosses are stated, each
    // as a band of the profile in the front view, and none is located (the turning axis does).
    assert_eq!(
        items(&plan, "boss"),
        strs(&[
            ("boss.diameter", "front", "ø110"),
            ("boss.diameter", "front", "ø118"),
            ("boss.diameter", "front", "ø122"),
            ("boss_height.length", "front", "199.67"),
            ("boss_height.length", "front", "20"),
            ("boss_height.length", "front", "4.84"),
            ("boss_height.length", "front", "6.38"),
        ])
    );
    let overall = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:envelope/od.diameter")
        .unwrap();
    assert_eq!(overall.label, "ø130.94");
    assert_eq!(
        overall
            .measurements
            .iter()
            .filter(|m| m.parameter == "boss.diameter" && (m.value - 130.94).abs() < 1e-6)
            .count(),
        1
    );
    drawn(&plan, overall);
    let bosses = of_kind(&a, "boss");
    assert!(plan.dimensions.iter().all(|d| {
        !d.id.contains("/location")
            || !d
                .measurements
                .iter()
                .any(|m| bosses.contains(&m.feature.as_str()))
    }));
    for d in plan.dimensions.iter().filter(|d| d.id.contains("/boss")) {
        assert_eq!(d.kind, "linear", "{}", d.id);
        drawn(&plan, d);
    }
}

#[test]
fn slot_and_boss_items_can_be_omitted_by_id() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "m10127");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let ids: Vec<String> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.contains("/boss"))
        .map(|d| d.id.clone())
        .collect();
    // Its diameter; its height is conveyed by the hole callout on whose floor it stands (ADR1-8).
    assert_eq!(ids, ["d:f:boss:f6/boss.diameter"]);
    let mut spec = Spec::default();
    spec.dimensions.insert(
        ids[0].clone(),
        serde_json::from_str(r#"{"omit": true}"#).unwrap(),
    );
    let again = model::plan(&a, &spec).unwrap();
    assert!(again.dimensions.iter().all(|d| d.id != ids[0]));
    assert!(again.omitted.iter().any(|o| o.id == ids[0]));
    // Planning is deterministic.
    assert_eq!(
        serde_json::to_string(&plan).unwrap(),
        serde_json::to_string(&model::plan(&a, &Spec::default()).unwrap()).unwrap()
    );
}

#[test]
fn equal_widths_across_a_view_share_one_counted_dimension() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let mut a = analysed(&corpus, "cgb245");
    // Turn cgb245's slots a quarter turn in the plan (quiddity's letters swapped): their widths
    // now run across the view, and the two 6 mm obrounds span the same stretch of x. One is split
    // from the congruent slots' callout (`features/<id>/split`), so their widths are shared.
    for f in a.features.iter_mut().filter(|f| f.kind == "slot") {
        f.parameters.insert("long_axis".into(), "y".into());
        f.parameters.insert("width_axis".into(), "z".into());
    }
    corpus::split_slot(&mut a, "f:slot:f21");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        items(&plan, "slot_width"),
        strs(&[
            ("slot_width.length", "plan", "2× 6"),
            ("slot_width.length", "plan", "6.98"),
        ])
    );
    let shared = plan.dimensions.iter().find(|d| d.label == "2× 6").unwrap();
    assert_eq!(shared.measurements.len(), 2);
}

/// A corpus part (*file*, under the corpus) with Python quiddity 0.3.9's recognition for it
/// (`tests/fixtures/recognition/dims/<name>.json.gz`, written by `tools/recognise.py` with
/// draftwright's own environment): parts whose features no sampled sheet has.
fn analysed_file(corpus: &Path, name: &str, file: &str) -> Analysis {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("tests/fixtures/recognition/dims/{name}.json.gz"));
    let mut json = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_string(&mut json)
        .unwrap();
    let (part, mut a) = model::analyse(&corpus.join(file)).unwrap();
    let (r, features) = draftwright::bridge::read(&part, &a.binding.source_sha256, &json).unwrap();
    a.recognition = Some(r);
    a.features = features;
    a
}

/// The plan's items that state a measurement of a feature of *kind*, as (parameter, view,
/// label), sorted.
fn stating(plan: &Plan, a: &Analysis, kind: &str) -> Vec<(String, String, String)> {
    let ids = of_kind(a, kind);
    let mut out: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| ids.contains(&m.feature.as_str()))
        })
        .map(|d| {
            let (_, parameter) = d.id.split_once('/').unwrap();
            let view = d.view.trim_start_matches("v:");
            (parameter.to_string(), view.to_string(), d.label.clone())
        })
        .collect();
    out.sort();
    out
}

#[test]
fn ctc01_hex_boss_states_its_size_across_flats_and_its_height() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "ctc01");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    // HEX 100 A/F in the plan, as on draftwright's sheet. Its height 50 runs from the 100 face
    // level to the 150 top, so the stated rung and overall convey it (ADR4-U1; draftwright
    // prints it, a listed parity difference). The port also locates a boss, but this one stands
    // at the centre of the 2×2 hole grid, whose own location (400, 225) conveys it (ARCH0009-6).
    assert_eq!(
        stating(&plan, &a, "polygonal_boss"),
        strs(&[("polygon_across_flats.length", "plan", "HEX 100 A/F")])
    );
    let across = plan
        .dimensions
        .iter()
        .find(|d| d.id.ends_with("/polygon_across_flats.length"))
        .unwrap();
    assert_eq!(across.kind, "callout");
    assert_eq!(across.terms, ["HEX", "100", "A/F"]);
    assert!((across.value - 100.0).abs() < 1e-6);
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.id.contains("polygonal_boss"))
    {
        assert!(d.placed.is_some(), "{} is not drawn", d.id);
    }
    assert_eq!(
        not_ported(&plan, &of_kind(&a, "polygonal_boss")),
        Vec::<String>::new()
    );
    // Each is editable by its ID.
    let mut spec = Spec::default();
    spec.dimensions.insert(
        across.id.clone(),
        serde_json::from_str(r#"{"omit": true}"#).unwrap(),
    );
    let again = model::plan(&a, &spec).unwrap();
    assert!(again.dimensions.iter().all(|d| d.id != across.id));
    assert!(again.omitted.iter().any(|o| o.id == across.id));
    // The covered height keeps its ID, so it stays addressable.
    assert!(
        again
            .omitted
            .iter()
            .any(|o| o.id.contains("polygonal_boss:1/boss_height"))
    );
}

#[test]
fn ftc07_square_bosses_are_counted_across_flats_with_their_heights() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed_file(&corpus, "ftc07", "nist/nist_ftc_07_asme1_rd.stp");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    // draftwright's sheet (captured 2026-10-08 at origin/main 586628b) prints `4-SIDED 25.4 A/F`
    // four times in the front and `2.5` four times in the side, on one span; the port counts the
    // four identical bosses once, as it does cylindrical ones, and states their one height once,
    // counted (src/dims/mod.rs::share).
    let want = vec![
        (
            "polygon_across_flats.length",
            "front",
            "4× 4-SIDED 25.352 A/F",
        ),
        ("boss_height.length", "side", "4× 2.54"),
    ];
    let got: Vec<_> = stating(&plan, &a, "polygonal_boss")
        .into_iter()
        .filter(|(p, ..)| !p.starts_with("location"))
        .collect();
    let mut want = strs(&want);
    want.sort();
    assert_eq!(got, want);
    let across = plan
        .dimensions
        .iter()
        .find(|d| d.id.ends_with("/polygon_across_flats.length"))
        .unwrap();
    assert_eq!(across.measurements.len(), 4);
    assert!(across.placed.is_some());
    let height = plan
        .dimensions
        .iter()
        .find(|d| d.id.ends_with("/boss_height.length"))
        .unwrap();
    assert_eq!(height.measurements.len(), 4);
}

#[test]
fn blind_slots_are_called_out_as_draftwright_reads_their_sections() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    // draftwright's labels and views for the same parts (captured 2026-10-08 at origin/main
    // 586628b with tools/capture_sheet_facts.py): the section recesses its
    // `section_recess_fields` reads as rectangular or round-bottom blind slots; and, beyond
    // draftwright, each one's location across its width (the maintainer's decision of 2026-10-08).
    // The pockets and hexagonal pockets beside them are `src/dims/recesses.rs`'s; their labels,
    // views and locations are draftwright's too (its sheets, built at origin/main 586628b).
    type Case<'a> = (&'a str, &'a str, &'a [(&'a str, &'a str, &'a str)]);
    let cases: [Case; 3] = [
        (
            "m10047",
            "mfcadpp/10047.step",
            &[
                ("location_pocket.x", "front", "7.789"),
                ("location_pocket.z", "front", "3.526"),
                ("location_slot_across.length", "plan", "16.119"),
                ("location_slot_across.length", "side", "11.002"),
                ("pocket_width.length", "front", "2 × 4.124 × 26.996 DEEP"),
                (
                    "polygon_across_flats.length",
                    "front",
                    "HEX 1.732 A/F × 3.112 DEEP",
                ),
                (
                    "round_bottom_blind_slot_flat_width.length",
                    "plan",
                    "ROUND-BOTTOM OPEN SLOT 1\nBOTTOM FLAT × R0.541 × 2.42\nLONG",
                ),
                (
                    "round_bottom_blind_slot_flat_width.length",
                    "side",
                    "ROUND-BOTTOM OPEN SLOT 1\nBOTTOM FLAT × R4.396 × 4.662\nLONG",
                ),
            ],
        ),
        (
            "m10245",
            "mfcadpp/10245.step",
            &[
                ("location_pocket.x", "front", "5.565"),
                ("location_pocket.x", "front", "8.392"),
                ("location_pocket.z", "front", "24.090"),
                ("location_pocket.z", "front", "40.570"),
                ("location_slot_across.length", "front", "33.902"),
                ("pocket_width.length", "front", "2 × 2 × 16.575 DEEP"),
                ("pocket_width.length", "front", "3.124 × 8.326 × 7.44 DEEP"),
                (
                    "rectangular_blind_slot_width.length",
                    "front",
                    "OPEN SLOT 3.344 × 9.15 ×\n2.049 DEEP",
                ),
            ],
        ),
        (
            "m11512",
            "mfcadpp_regressions/11512.step",
            // Its walls stand at x = 12.837 and 15.295 (OpenCascade's face boxes), so its centre
            // line is 14.066 from the part's left face.
            &[
                ("location_slot_across.length", "plan", "14.067"),
                (
                    "rectangular_blind_slot_width.length",
                    "plan",
                    "OPEN SLOT 2.458 × 11.672 ×\n6.674 DEEP",
                ),
            ],
        ),
    ];
    for (name, file, want) in cases {
        let a = analysed_file(&corpus, name, file);
        let plan = model::plan(&a, &Spec::default()).unwrap();
        assert_eq!(stating(&plan, &a, "section_recess"), strs(want), "{name}");
        // Each blind slot is located across its width by its centre line, in the view that looks
        // into it (beyond draftwright); the locations are in *want* beside the callouts.
        for d in plan
            .dimensions
            .iter()
            .filter(|d| d.id.ends_with("/location_slot_across.length"))
        {
            assert!(d.placed.is_some(), "{name}: {} is not drawn", d.id);
        }
        // The other section recesses (channels, passages, sections no grammar reads) stay
        // reported, one each.
        let recesses = of_kind(&a, "section_recess");
        let reported = not_ported(&plan, &recesses);
        let callouts = want
            .iter()
            .filter(|w| {
                ["blind_slot", "pocket_width", "polygon_"]
                    .iter()
                    .any(|k| w.0.contains(k))
            })
            .count();
        assert_eq!(reported.len(), recesses.len() - callouts, "{name}");
        for d in plan
            .dimensions
            .iter()
            .filter(|d| d.id.contains("blind_slot"))
        {
            assert!(d.placed.is_some(), "{name}: {} is not drawn", d.id);
            assert_eq!(d.measurements.len(), 3, "{name}: {}", d.id);
            assert!(
                !reported
                    .iter()
                    .any(|p| p.ends_with(&d.measurements[0].feature))
            );
        }
    }
}

/// m10127's one boss, moved along its axis and resized in quiddity's frame so that it spans
/// *span* (world coordinates along its axis, low first).
fn boss_spanning(a: &mut Analysis, span: [f64; 2]) {
    let frame = a.recognition.as_ref().unwrap().frame;
    let b = a.features.iter_mut().find(|f| f.kind == "boss").unwrap();
    let local: Vec<f64> = serde_json::from_value(b.parameters["axis"].clone()).unwrap();
    let mut top: Vec<f64> = serde_json::from_value(b.parameters["location"].clone()).unwrap();
    let dir: Vec<f64> = (0..3)
        .map(|k| (0..3).map(|j| local[j] * frame[j + 1][k]).sum())
        .collect();
    let k = (0..3).find(|&k| dir[k].abs() > 0.5).unwrap();
    let world_top: f64 = frame[0][k] + (0..3).map(|j| top[j] * frame[j + 1][k]).sum::<f64>();
    let target = if dir[k] > 0.0 { span[1] } else { span[0] };
    let t = (target - world_top) / dir[k];
    for j in 0..3 {
        top[j] += t * local[j];
    }
    b.parameters.insert("location".into(), top.into());
    b.parameters
        .insert("height".into(), (span[1] - span[0]).into());
}

#[test]
fn a_boss_height_between_the_parts_own_faces_is_the_overall_extents() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let mut a = analysed(&corpus, "m10127");
    // m10127's boss stands proud (5.7 along y on a part 32.3 deep), on the flat floor of the
    // ⌀12.99 hole 5.73 deep: its height is not the overall's (ISSUE-1154-a's control) but the
    // hole's depth, which the hole's callout states (ADR1-8), so the callout carries its claim.
    let proud = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(items(&proud, "boss_height"), vec![]);
    let callout = proud
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "boss_height.length")
        })
        .unwrap();
    assert_eq!(
        (callout.id.as_str(), callout.label.as_str()),
        ("d:f:hole:f10/bore.diameter", "⌀12.990 ↧ 5.73")
    );
    // Flush with both faces of the part, its height is the overall depth's: one fact, one owner.
    let [lo, hi] = a.envelope;
    boss_spanning(&mut a, [lo[1], hi[1]]);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(items(&plan, "boss_height"), vec![]);
    let depth = plan
        .dimensions
        .iter()
        .find(|d| d.id.starts_with("d:envelope/depth.length"))
        .unwrap();
    let gone = plan
        .omitted
        .iter()
        .find(|o| o.id.ends_with("/boss_height.length"))
        .expect("the boss height is omitted");
    assert!(
        gone.basis.starts_with(&format!("conveyed by {}", depth.id)),
        "{}",
        gone.basis
    );
    // An owner the spec omits takes nothing: the boss height is stated again.
    let mut spec = Spec::default();
    spec.dimensions.insert(
        depth.id.clone(),
        serde_json::from_str(r#"{"omit": true}"#).unwrap(),
    );
    let kept = model::plan(&a, &spec).unwrap();
    assert_eq!(items(&kept, "boss_height").len(), 1);
    assert!(kept.omitted.iter().all(|o| !o.id.contains("boss_height")));
    // 0.5 mm short of a face is another plane: both are stated.
    boss_spanning(&mut a, [lo[1] + 0.5, hi[1]]);
    let short = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(items(&short, "boss_height").len(), 1);
}

#[test]
fn a_boss_with_no_height_is_reported_not_silently_left_short() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let mut a = analysed(&corpus, "m10127");
    let b = a.features.iter_mut().find(|f| f.kind == "boss").unwrap();
    b.parameters
        .insert("height".into(), serde_json::Value::Null);
    let id = b.id.clone();
    let plan = model::plan(&a, &Spec::default()).unwrap();
    // Its diameter is still called out; its height is not, and a warning says so.
    assert_eq!(
        items(&plan, "boss"),
        strs(&[("boss.diameter", "front", "ø8.385")])
    );
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "feature_not_dimensioned" && p.subjects == [id.clone()])
        .expect("a problem names the boss");
    assert_eq!(p.severity, "warning");
    assert!(p.message.contains("height"), "{}", p.message);
}

/// A slot-pattern fixture part (`tests/fixtures/recognition/slot_patterns/`, built by `make.py`
/// there; no corpus part has a slot array) with its features, its recognition JSON edited by
/// *edit*.
fn slot_fixture(name: &str, edit: impl Fn(&mut serde_json::Value)) -> Analysis {
    let dir =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/recognition/slot_patterns");
    let mut text = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(dir.join(format!("{name}.json.gz"))).unwrap())
        .read_to_string(&mut text)
        .unwrap();
    let mut json: serde_json::Value = serde_json::from_str(&text).unwrap();
    edit(&mut json);
    let (part, mut a) = model::analyse(&dir.join(format!("{name}.step"))).unwrap();
    model::recognise(&part, &mut a, &json.to_string()).unwrap();
    a
}

/// draftwright's `render_slot_patterns`: one `N× SLOT W × L` callout, the pitches, and the
/// pattern's centre located, in the view that looks through the slots; its members are not
/// dimensioned again. The labels are those on draftwright's sheets of these parts
/// (`<name>.facts.json.gz`; `tests/dimensions.rs` compares them item by item), less the obround's
/// `2× R`: the ends are full-round (the radius half the width), which the slot callout implies.
#[test]
fn a_slot_pattern_is_called_out_once_with_its_pitches_and_centre() {
    let a = slot_fixture("slot_array", |_| {});
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let of_pattern: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:f:slot"))
        .map(|d| (d.id.clone(), d.label.clone()))
        .collect();
    assert_eq!(
        of_pattern,
        [
            ("d:f:slot_pattern:f6/slot_width.length", "3× SLOT 6 × 20"),
            ("d:f:slot_pattern:f6/pitch.length", "2× 25"),
            ("d:f:slot_pattern:f6/location_slot_pattern.length.x", "60"),
            ("d:f:slot_pattern:f6/location_slot_pattern.length.y", "30"),
        ]
        .map(|(a, b)| (a.to_string(), b.to_string()))
    );
    let callout = &plan
        .dimensions
        .iter()
        .find(|d| d.label.contains("SLOT"))
        .unwrap();
    assert_eq!(callout.kind, "callout");
    let stated: Vec<_> = callout
        .measurements
        .iter()
        .map(|m| (m.feature.as_str(), m.parameter.as_str(), m.value))
        .collect();
    assert_eq!(
        stated,
        [
            ("f:slot_pattern:f6", "slot_width.length", 6.0),
            ("f:slot_pattern:f6", "slot_length.length", 20.0),
        ]
    );
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:f:slot"))
    {
        drawn(&plan, d);
        assert!(d.default && !d.basis.is_empty(), "{d:?}");
    }
    let ids = of_kind(&a, "slot")
        .into_iter()
        .chain(of_kind(&a, "slot_pattern"))
        .collect::<Vec<_>>();
    assert!(
        plan.problems
            .iter()
            .all(|p| !p.subjects.iter().any(|s| ids.contains(&s.as_str()))),
        "{:#?}",
        plan.problems
    );

    // A grid: its column and row pitches as quiddity names them (2 columns 30 apart in y, 3 rows
    // 40 apart in x), and its centre.
    let a = slot_fixture("slot_grid", |_| {});
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let of_pattern: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:f:slot"))
        .map(|d| (d.id.clone(), d.label.clone()))
        .collect();
    assert_eq!(
        of_pattern,
        [
            ("d:f:slot_pattern:f6/slot_width.length", "6× SLOT 8 × 24"),
            ("d:f:slot_pattern:f6/grid_pitch.length.col", "30"),
            ("d:f:slot_pattern:f6/grid_pitch.length.row", "2× 40"),
            ("d:f:slot_pattern:f6/location_slot_pattern.length.x", "80"),
            ("d:f:slot_pattern:f6/location_slot_pattern.length.y", "50"),
        ]
        .map(|(a, b)| (a.to_string(), b.to_string()))
    );
}

/// A pattern whose slots do not stand where quiddity says (here, at another pitch) is reported,
/// and its slots are dimensioned singly rather than under a pitch they do not have.
#[test]
fn a_slot_pattern_that_does_not_hold_together_is_reported_and_its_slots_dimensioned_singly() {
    let a = slot_fixture("slot_array", |j| {
        j["quiddity"]["derived"]["slot_patterns"][0]["pitch"] = 26.0.into();
    });
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let p = plan
        .problems
        .iter()
        .find(|p| p.subjects == ["f:slot_pattern:f6"])
        .unwrap();
    assert_eq!(p.code, "feature_not_dimensioned");
    assert!(
        p.message
            .contains("not in one line along x at its pitch; its slots are dimensioned singly"),
        "{}",
        p.message
    );
    assert!(
        plan.dimensions
            .iter()
            .all(|d| !d.id.contains("slot_pattern"))
    );
    // Each slot's own length is stated: the three are congruent, so one slot callout claims all
    // three (`3× SLOT 6 × 20`), never the pattern, and states no pitch.
    let lengths: Vec<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.view == "v:plan")
        .flat_map(|d| &d.measurements)
        .filter(|m| m.parameter == "slot_length.length")
        .map(|m| m.feature.as_str())
        .collect();
    assert!(plan.dimensions.iter().any(|d| d.label == "3× SLOT 6 × 20"));
    assert!(plan.dimensions.iter().all(|d| !d.id.contains("pitch")));
    assert_eq!(lengths.len(), 3, "{lengths:?}");
    assert!(
        lengths.iter().all(|f| f.starts_with("f:slot:")),
        "{lengths:?}"
    );
}

/// The across-width locations a plan states, as (label, the slots it serves), sorted.
fn across(plan: &Plan) -> Vec<(String, usize)> {
    let mut out: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/location_slot_across.length"))
        .map(|d| (d.label.clone(), d.measurements.len()))
        .collect();
    out.sort();
    out
}

/// The maintainer's decision of 2026-10-08: a slot is also located across its width, from the
/// part's box to its centre line, in the view that looks through it (beyond draftwright). On the
/// array plate with its pattern broken (so its three slots are dimensioned singly), each slot,
/// running along y, is located in x, the middle one too although it stands at the plate's
/// centre (60 of 120): a centred slot is located, not left to symmetry.
#[test]
fn a_lone_slot_is_located_across_its_width_from_the_box_to_its_centre_line() {
    let a = slot_fixture("slot_array", |j| {
        j["quiddity"]["derived"]["slot_patterns"][0]["pitch"] = 26.0.into();
    });
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        across(&plan),
        [("35", 1), ("60", 1), ("85", 1)].map(|(l, n)| (l.to_string(), n))
    );
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/location_slot_across.length"))
    {
        assert_eq!(d.view, "v:plan", "{d:?}");
        assert_eq!(d.kind, "linear", "{d:?}");
        assert_eq!(d.measurements[0].parameter, "location_slot_across.length");
        assert!(d.basis.contains("across its width (x)"), "{}", d.basis);
        drawn(&plan, d);
    }
    // Only the middle slot is the plate's centre, and it is still located.
    let middle = plan.dimensions.iter().find(|d| d.label == "60").unwrap();
    assert!((middle.value - 60.0).abs() < 1e-6);
    assert!(plan.problems.iter().all(|p| p.code != "location_on_datum"));
}

/// Slots whose centre lines stand at the same distance across, in one view, share one location,
/// as hole locations do: the grid plate with its pattern broken has two rows of three slots
/// (running along x) at y = 35 and 65.
#[test]
fn slots_at_the_same_distance_across_share_one_location() {
    let a = slot_fixture("slot_grid", |j| {
        j["quiddity"]["derived"]["slot_patterns"][0]["col_pitch"] = 31.0.into();
    });
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "feature_not_dimensioned" && p.subjects == ["f:slot_pattern:f6"])
    );
    assert_eq!(
        across(&plan),
        [("35", 3), ("65", 3)].map(|(l, n)| (l.to_string(), n))
    );
    let d = plan.dimensions.iter().find(|d| d.label == "35").unwrap();
    assert!(
        d.basis.contains("one dimension serves every slot"),
        "{}",
        d.basis
    );
}

/// A slot pattern is located by its centre on both of its view's axes, across its slots' width
/// too; its members get no location of their own.
#[test]
fn a_slot_pattern_is_located_across_its_slots_by_its_centre() {
    let a = slot_fixture("slot_array", |_| {});
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(across(&plan), []);
    let x = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:slot_pattern:f6/location_slot_pattern.length.x")
        .unwrap();
    assert_eq!(x.label, "60");
    // Its slots run along y and are 6 wide in x: x is across them.
    assert!(plan.dimensions.iter().any(|d| d.label == "3× SLOT 6 × 20"));
}

/// ARCH0009-6 on ctc01: the hex boss stands at the centre of the 2×2 hole grid, so its location
/// and the grid's measure the same spans in the plan view. Each is drawn once, by the grid (a hole
/// location outranks a boss's); the boss's own location is omitted as conveyed by it, and is not reported as a drop. Omitting the grid's promotes the
/// boss's.
#[test]
fn ctc01_hex_boss_location_is_conveyed_by_its_hole_grid() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let a = analysed(&corpus, "ctc01");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let in_plan = |plan: &Plan, label: &str| {
        plan.dimensions
            .iter()
            .filter(|d| d.view == "v:plan" && d.kind == "linear" && d.label == label)
            .map(|d| d.id.clone())
            .collect::<Vec<_>>()
    };
    for (axis, label) in [("x", "400"), ("y", "225")] {
        let stated = in_plan(&plan, label);
        let grid = format!("d:f:hole_pattern:1/location.length.{axis}");
        let boss = format!("d:f:polygonal_boss:1/location.length.{axis}");
        assert_eq!(
            stated,
            std::slice::from_ref(&grid),
            "{label} is stated once, by the grid"
        );
        let gone = plan.omitted.iter().find(|o| o.id == boss).unwrap();
        assert!(
            gone.basis.starts_with(&format!("conveyed by {grid}: "))
                && gone.basis.contains("ARCH0009-6"),
            "{gone:?}"
        );
        assert!(
            plan.problems.iter().all(|p| !p.subjects.contains(&boss)),
            "a conveyed location is no drop"
        );

        // Omitting the grid's location promotes the boss's in its place.
        let mut spec = Spec::default();
        spec.dimensions.insert(
            grid.clone(),
            serde_json::from_str(r#"{"omit": true}"#).unwrap(),
        );
        let again = model::plan(&a, &spec).unwrap();
        assert_eq!(in_plan(&again, label), std::slice::from_ref(&boss));
        assert!(again.omitted.iter().all(|o| o.id != boss));
        let promoted = again.dimensions.iter().find(|d| d.id == boss).unwrap();
        assert!(promoted.placed.is_some(), "{promoted:?}");
    }
}

/// dw-examples nema17-bracket and gantry-plate: congruent lone slots (here the array plate's
/// three, its pattern broken) are dimensioned as slots: one `3× SLOT 6 × 20` callout claims each
/// slot's width and length, no `2× R3` repeats the full-round ends the callout implies, no
/// separate width or length is drawn, and each slot is located by its centre on both axes, so one
/// line serves the three centres at the same distance along their length.
#[test]
fn congruent_lone_slots_are_dimensioned_as_slots_located_by_their_centres() {
    let a = slot_fixture("slot_array", |j| {
        j["quiddity"]["derived"]["slot_patterns"][0]["pitch"] = 26.0.into();
    });
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let slots: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:f:slot:"))
        .collect();
    let callouts: Vec<_> = slots.iter().filter(|d| d.kind == "callout").collect();
    assert_eq!(callouts.len(), 1, "{slots:#?}");
    assert_eq!(callouts[0].label, "3× SLOT 6 × 20");
    for parameter in ["slot_width.length", "slot_length.length"] {
        let mut claimed: Vec<_> = callouts[0]
            .measurements
            .iter()
            .filter(|m| m.parameter == parameter)
            .map(|m| m.feature.as_str())
            .collect();
        claimed.sort();
        assert_eq!(claimed, of_kind(&a, "slot"), "{parameter}");
    }
    assert!(
        slots
            .iter()
            .all(|d| !d.id.ends_with("/slot_end_radius.radius")
                && !d.id.ends_with("/slot_length.length")
                && !(d.kind == "linear" && d.id.ends_with("/slot_width.length"))),
        "{slots:#?}"
    );
    // Along y (their length) every centre stands at 30: one location line, the rest conveyed.
    let along: Vec<_> = slots
        .iter()
        .filter(|d| d.id.ends_with("/location_slot.length"))
        .collect();
    assert_eq!(along.len(), 1, "{along:#?}");
    assert_eq!(along[0].label, "30");
    assert!(
        along[0].basis.contains("to its centre"),
        "{}",
        along[0].basis
    );
    // Its witness starts at whichever centre is nearest its dimension line, so it crosses no
    // other slot, on either side of the view.
    for side in ["left", "right"] {
        let spec: Spec = serde_json::from_value(serde_json::json!({
            "dimensions": {along[0].id.as_str(): {"place": {"side": side}}}
        }))
        .unwrap();
        let sided = model::plan(&a, &spec).unwrap();
        assert!(
            !sided
                .problems
                .iter()
                .any(|p| p.code == "extension_through_feature"
                    && p.subjects.first() == Some(&along[0].id)),
            "{side}: {:#?}",
            sided.problems
        );
    }
    assert_eq!(
        across(&plan),
        [("35", 1), ("60", 1), ("85", 1)].map(|(l, n)| (l.to_string(), n))
    );
    for d in &slots {
        drawn(&plan, d);
    }
}

/// The spec's override (`features/<id>/split` on a slot): a full-round slot split from its
/// congruent slots' callout is dimensioned by its two end centres, as two arcs located: its width,
/// its low end's centre located from the part's box and the centres' distance apart, and no
/// length or end radius (the width with the located centres fixes the full-round ends). The
/// callout counts the rest; without the override the callout states all three. The review finds
/// every requirement of the split slot stated.
#[test]
fn a_slot_split_by_the_spec_is_dimensioned_by_its_end_centres() {
    // Three lone congruent slots: the fixture's array without its pattern.
    let mut a = slot_fixture("slot_array", |_| {});
    a.features.retain(|f| f.kind != "slot_pattern");
    let claims = |plan: &Plan, parameter: &str| -> Vec<String> {
        plan.dimensions
            .iter()
            .filter(|d| d.placed.is_some())
            .filter(|d| {
                d.measurements
                    .iter()
                    .any(|m| m.feature == "f:slot:f10" && m.parameter == parameter)
            })
            .map(|d| d.label.clone())
            .collect()
    };

    // Centre marks at its end centres, where its centre dimensions end.
    let end_marks = |plan: &Plan| -> usize {
        plan.views
            .iter()
            .flat_map(|v| &v.line_work)
            .filter(|w| w.kind == "cross" && w.id.contains(":f:slot:f10:end"))
            .count()
    };

    let unsplit = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(claims(&unsplit, "slot_width.length"), ["3× SLOT 6 × 20"]);
    assert!(claims(&unsplit, "slot_centres.length").is_empty());
    assert_eq!(end_marks(&unsplit), 0);

    let spec: Spec =
        serde_json::from_value(serde_json::json!({"features": {"f:slot:f10": {"split": true}}}))
            .unwrap();
    let plan = model::plan_recovered(&a, &spec).unwrap();
    let callout = plan
        .dimensions
        .iter()
        .find(|d| d.label.contains("SLOT"))
        .unwrap();
    assert_eq!(callout.label, "2× SLOT 6 × 20");
    assert!(callout.measurements.iter().all(|m| m.feature != "f:slot:f10"));
    assert_eq!(claims(&plan, "slot_width.length"), ["6"]);
    // 20 long with R3 ends: centres 14 apart.
    assert_eq!(claims(&plan, "slot_centres.length"), ["14"]);
    assert!(claims(&plan, "slot_length.length").is_empty());
    assert!(claims(&plan, "slot_end_radius.radius").is_empty());
    assert_eq!(end_marks(&plan), 2);
    // Its low end's centre is located from the part's box, R3 in from the low end.
    let f = a.features.iter().find(|f| f.id == "f:slot:f10").unwrap();
    let o = draftwright::lint::slots::outline(&a, f).unwrap();
    let located = plan
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == "f:slot:f10" && m.parameter == "location_slot.length")
        })
        .expect("the low end's centre located");
    assert!((located.value - (o.ends[0] + 3.0 - a.envelope[0][o.long])).abs() < 1e-6);

    // The review reads the slot as the plan dimensions it: nothing of it is missing.
    let dir =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/recognition/slot_patterns");
    let (part, _) = model::analyse(&dir.join("slot_array.step")).unwrap();
    let mut split = a.clone();
    corpus::split_slot(&mut split, "f:slot:f10");
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &split, &plan, &font).unwrap();
    let missing: Vec<_> = draftwright::lint::lint(&split, &drawing)
        .into_iter()
        .filter(|i| i.code.starts_with("slot_requirement_"))
        .collect();
    assert!(missing.is_empty(), "{missing:#?}");
}

/// nist_ftc_08's two keyhole slots (15.24 × 31.75, full-round, each opened at its middle by a
/// circular seat) are congruent but not called out together: the seat's axis and callout carry
/// the compound, and each slot keeps its own length, end radius and low-end location. That holds
/// in the recess-free Analysis the established route templates plan (nist_stc_08's hole table
/// came from it).
#[test]
fn keyhole_slots_are_not_called_out_together() {
    let Some(dir) = corpus::dir() else { return };
    let a = model::analyse_native(&dir.join("nist/nist_ftc_08_asme1_rc.stp"))
        .unwrap()
        .1;
    let keyholes = ["f:slot:f152", "f:slot:f220"];
    let bare = draftwright::lint::slots::without_recesses(&a);
    assert!(bare.features.iter().all(|f| f.kind != "section_recess"));
    for analysis in [&a, &bare] {
        for id in keyholes {
            let f = analysis.features.iter().find(|f| f.id == id).unwrap();
            assert!(draftwright::lint::slots::keyed(analysis, f), "{id}");
            assert!(!draftwright::lint::slots::grouped(analysis, f), "{id}");
        }
    }
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert!(plan.dimensions.iter().all(|d| !d.label.contains("SLOT")));
    for id in keyholes {
        for parameter in ["slot_length.length", "slot_end_radius.radius"] {
            assert!(
                plan.dimensions.iter().any(|d| d
                    .measurements
                    .iter()
                    .any(|m| m.feature == id && m.parameter == parameter)),
                "{id} {parameter}"
            );
        }
    }
}

/// NIST FTC-09: its two congruent slots' callout reaches one of them clear of the wide slot
/// beside them. The part's semantic PMI keeps the other leaders' established routing, which does
/// not count a crossed feature; the slot callout is new and still does (`dims::leaders`).
#[test]
fn ftc09_slot_callout_leader_crosses_no_other_feature() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (part, a) = model::analyse_native(&corpus.join("nist/nist_ftc_09_asme1_rd.stp")).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let callout = plan
        .dimensions
        .iter()
        .find(|d| d.label == "2× SLOT 6.35 × 25.4")
        .expect("the slot callout");
    drawn(&plan, callout);
    let crossings: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "leader_through_feature" && p.subjects.first() == Some(&callout.id))
        .collect();
    assert!(crossings.is_empty(), "{crossings:#?}");
    // Its tip lands on a slot's full-round end, which the lint reads as round although
    // quiddity records the end radius rounded (R3.18 on a 6.35 wide slot).
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let mismatched: Vec<_> = draftwright::lint::lint(&a, &drawing)
        .into_iter()
        .filter(|i| i.code == "slot_leader_target_mismatch")
        .collect();
    assert!(mismatched.is_empty(), "{mismatched:#?}");
}
