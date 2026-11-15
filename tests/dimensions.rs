//! The port's dimensions against draftwright's sheets (`tests/fixtures/sheets/`), item by item.
//!
//! In scope are the families the port plans: the overall extents (`width`, `depth`, `height`,
//! a turned part's `od.diameter`), hole callouts (bore, counterbore, spotface, countersink and
//! bolt-circle terms, on holes, patterns and turned bores), hole and pattern locations, pattern
//! pitches, slot sizes (`slot`: width, length, end radius) and locations (`location.slot`), boss
//! diameters, sizes across flats and heights (`boss`), fillet and chamfer callouts, through-step
//! legs (`through_step`), circular blind step and paired-ramp callouts, the height ladder's
//! heights and shoulder positions (`step_height`, `step_position`), turned steps' diameters and
//! lengths (`turned_step`), raised pads' footprints and heights (`pad`; their locations are
//! `location.<axis>`), and section recesses
//! (`recess`: pocket, hexagonal pocket and circular seat callouts and open channel widths; their
//! locations are `location.<axis>`, a z pocket's axis read from which of its two compiled values
//! draftwright's label prints). Each of draftwright's annotations that states one of them is
//! matched with a plan item of the same family, view and printed label whose measurement identity
//! agrees: for an extent, a pitch, a location (whose family names its axis) or a step, the same
//! value within half the printed unit; for a
//! callout (fillets' and chamfers' too), every feature draftwright names sits where one of the
//! item's features (or its pattern) does, within 0.01 mm across the view. Every difference is
//! listed, with a count, a verdict and the reason, in
//! `tests/fixtures/known_dimensions.json`; a listed difference that goes away must come off.
//!
//! The features come from Python quiddity 0.3.9 (draftwright's own recogniser for these sheets),
//! through `tools/recognise.py`, stored in `tests/fixtures/recognition/sheets/`. No corpus part
//! has a slot array, so two fixture plates with one (`tests/fixtures/recognition/slot_patterns/`:
//! a linear array and a grid, with their recognition and draftwright's sheet facts) are compared
//! the same way. ctc04 is not
//! compared: the bridge refused its recognition until its face check took OpenCascade's
//! default-precision sphere areas for what they are (`tests/fixtures/known_bridge_faces.json`),
//! and its differences are not listed yet. Set `DIMS_REPORT=1` to print every difference.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::BTreeMap;
use std::io::Read as _;
use std::path::Path;

use draftwright::dims::{self, PlannedDimension};
use draftwright::model::{self, Analysis};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    part: String,
    /// `python` (on draftwright's sheet, not the port's) or `rust` (the other way).
    side: String,
    /// `envelope`, `callout`, `location.<axis>`, `pitch`, `fillet` or `chamfer`.
    family: String,
    view: String,
    label: String,
    count: usize,
    /// `rust-correct`, `rust-wrong`, `equivalent`, `undetermined` or `not-applicable`.
    verdict: String,
    reason: String,
}

fn recognition(name: &str) -> Option<String> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("tests/fixtures/recognition/sheets/{name}.json.gz"));
    let mut text = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).ok()?)
        .read_to_string(&mut text)
        .unwrap();
    Some(text)
}

/// The part with its features, or None when the bridge refuses the recognition, which only
/// ctc04's may (see the module docs).
fn analysed(corpus: &Path, name: &str, file: &str, json: &str) -> Option<Analysis> {
    let (part, mut a) = model::analyse(&corpus.join(file)).unwrap();
    let (r, features) = match draftwright::bridge::read(&part, &a.binding.source_sha256, json) {
        Ok(read) => read,
        Err(e) => {
            assert_eq!(name, "ctc04", "the bridge refuses {name}: {e}");
            return None;
        }
    };
    a.recognition = Some(r);
    a.profile_arcs = draftwright::arc::evidence(&part, &features);
    a.features = features;
    Some(a)
}

/// The family a draftwright measurement belongs to, if the port plans it.
fn python_family(parameter: &str, kind: &str) -> Option<String> {
    match parameter {
        "width.length" | "depth.length" | "height.length" if kind == "envelope" => {
            Some("envelope".into())
        }
        "od.diameter" => Some("envelope".into()),
        "step.diameter" | "step.length" => Some("turned_step".into()),
        "thickness.length" if kind == "plate" => Some("plate".into()),
        "fillet.radius" => Some("fillet".into()),
        "chamfer.length" => Some("chamfer".into()),
        "step_height.length" => Some("step_height".into()),
        "step_position.length" => Some("step_position".into()),
        "circular_step_radius.radius" | "circular_step_depth.length" => {
            Some("circular_blind_step".into())
        }
        "ramp_angle.angle" | "ramp_run.length" => Some("paired_ramp_step".into()),
        p if p.starts_with("through_step_leg.") => Some("through_step".into()),
        "pitch.length" => Some("pitch".into()),
        "slot_width.length" | "slot_length.length" | "slot_end_radius.radius" => {
            Some("slot".into())
        }
        "location_slot.length" => Some("location.slot".into()),
        "boss.diameter" | "boss_height.length" => Some("boss".into()),
        "polygon_across_flats.length" if kind == "polygonal_boss" => Some("boss".into()),
        "groove.length" | "groove.diameter" => Some("groove".into()),
        "flat.length" => Some("flat".into()),
        "polygon_across_flats.length" if kind == "hex_pocket" => Some("recess".into()),
        "pocket_width.length"
        | "pocket_length.length"
        | "pocket_depth.length"
        | "pocket_max_depth.length"
        | "seat_diameter.diameter"
        | "seat_run.length"
        | "seat_sweep.angle"
        | "channel_width.length" => Some("recess".into()),
        "pad_width.length" | "pad_length.length" | "pad_height.length" => Some("pad".into()),
        p if p.starts_with("seat_location.")
            || p.starts_with("location_pocket.")
            || p.starts_with("location_pad.") =>
        {
            Some(format!("location.{}", p.rsplit('.').next().unwrap_or("")))
        }
        p if p.starts_with("grid_pitch.") => Some("pitch".into()),
        p if p.starts_with("location")
            && (kind == "hole" || kind == "pattern" || kind == "slot_pattern") =>
        {
            Some(format!("location.{}", p.rsplit('.').next().unwrap_or("")))
        }
        p if [
            "bore.",
            "counterbore.",
            "spotface.",
            "countersink.",
            "bolt_circle.",
        ]
        .iter()
        .any(|r| p.starts_with(r)) =>
        {
            Some("callout".into())
        }
        _ => None,
    }
}

fn rust_family(d: &PlannedDimension) -> String {
    let parameter = d.id.split('/').nth(1).unwrap_or("");
    if d.id.starts_with("d:envelope/") {
        "envelope".into()
    } else if parameter == "thickness.length" {
        "plate".into()
    } else if parameter == "step.diameter" || parameter == "step.length" {
        "turned_step".into()
    } else if let Some(family) = [
        ("step_height", "step_height"),
        ("step_position", "step_position"),
        ("through_step_leg", "through_step"),
        ("circular_step_", "circular_blind_step"),
        ("ramp_", "paired_ramp_step"),
    ]
    .iter()
    .find_map(|(stem, family)| parameter.starts_with(stem).then_some(family))
    {
        (*family).into()
    } else if parameter.starts_with("location_slot_pattern") {
        format!("location.{}", parameter.rsplit('.').next().unwrap_or(""))
    } else if d.id.starts_with("d:f:section_recess:") && !parameter.contains("location") {
        "recess".into()
    } else if parameter.starts_with("seat_location") {
        format!("location.{}", parameter.rsplit('.').next().unwrap_or(""))
    } else if parameter.starts_with("location_slot") {
        "location.slot".into()
    } else if parameter.starts_with("slot_") {
        "slot".into()
    } else if parameter.starts_with("pad_") {
        "pad".into()
    } else if parameter.starts_with("boss") || parameter.starts_with("polygon_") {
        "boss".into()
    } else if parameter.starts_with("groove.") || parameter == "flat.length" {
        parameter.split('.').next().unwrap_or("").into()
    } else if parameter.starts_with("location") {
        format!("location.{}", parameter.rsplit('.').next().unwrap_or(""))
    } else if parameter.contains("pitch") {
        "pitch".into()
    } else if parameter == "fillet.radius" || parameter == "chamfer.length" {
        parameter.split('.').next().unwrap_or("").into()
    } else {
        "callout".into()
    }
}

/// The world axis a view looks along: its coordinate is not compared.
fn depth_axis(view: &str) -> usize {
    match view {
        "side" => 0,
        "front" => 1,
        _ => 2,
    }
}

struct Item {
    family: String,
    view: String,
    label: String,
    value: Option<f64>,
    origins: Vec<[f64; 3]>,
}

type Diff = (String, String, String, String, String);

fn compare(name: &str, corpus: &Path) -> Option<(usize, BTreeMap<Diff, usize>, usize)> {
    if name == "ctc04" {
        return None; // not listed yet (see the module docs)
    }
    let f = facts::load(name);
    let json = recognition(name)?;
    let a = analysed(corpus, name, &f.binding.file, &json)?;
    Some(compare_sheet(name, &f, &a))
}

/// The fixture parts beside their recognition in `tests/fixtures/recognition/<dir>/`, with
/// draftwright's sheet facts for each (`<name>.facts.json.gz`, captured by
/// `slot_patterns/capture.py`): plates with slot arrays (no corpus part has one), and quiddity's
/// golden step parts with a block stepped along y (no captured corpus sheet has a circular blind
/// step, a paired-ramp step or a through step along y).
const FIXTURES: [(&str, &str); 7] = [
    ("slot_patterns", "slot_array"),
    ("slot_patterns", "slot_grid"),
    ("steps", "rectangular_through_step"),
    ("steps", "circular_blind_step"),
    ("steps", "paired_ramp_step"),
    ("steps", "plates_pads_levels_and_slanted_steps"),
    ("steps", "stepped_block"),
];

fn compare_fixture((dir, name): (&str, &str)) -> (usize, BTreeMap<Diff, usize>, usize) {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/recognition")
        .join(dir);
    let f = facts::load_file(&dir.join(format!("{name}.facts.json.gz")));
    let mut json = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(dir.join(format!("{name}.json.gz"))).unwrap())
        .read_to_string(&mut json)
        .unwrap();
    let a = analysed(&dir, name, &f.binding.file, &json).unwrap();
    compare_sheet(name, &f, &a)
}

fn compare_sheet(
    name: &str,
    f: &facts::Facts,
    a: &Analysis,
) -> (usize, BTreeMap<Diff, usize>, usize) {
    assert_eq!(a.binding.source_sha256, f.binding.sha256, "{name}");
    // Keep the captured dimension ledger on its original full ISO paper resources.
    let spec = serde_json::from_value(
        serde_json::json!({"units":"mm","sheet":{"preferred_max_size":"A0"}}),
    )
    .unwrap();
    let plan = model::plan(a, &spec).unwrap();

    let mut python: Vec<Item> = Vec::new();
    let mut outside = 0;
    for ann in &f.annotations {
        let Some(family) = ann.measurements.iter().find_map(|m| {
            python_family(
                &m.parameter,
                m.feature.as_ref().map_or("", |f| f.kind.as_str()),
            )
        }) else {
            if matches!(
                ann.kind.as_str(),
                "Dimension" | "Leader" | "AngularDimension"
            ) {
                outside += 1;
            }
            continue;
        };
        let origins = ann
            .measurements
            .iter()
            .filter_map(|m| m.feature.as_ref())
            .filter(|f| {
                ["hole", "pattern", "fillet", "chamfer", "groove", "flat"]
                    .contains(&f.kind.as_str())
            })
            .filter_map(|f| f.origin)
            .collect();
        let label = ann.label.clone().unwrap_or_default();
        let mut value = ann.measurements.iter().find_map(|m| m.value);
        // A z pocket's location is one measurement of both directions
        // (`location_pocket.location`, no value): the direction is the one whose compiled
        // value the label prints, x then y.
        let family = if family == "location.location" {
            let values: Vec<f64> = ann
                .measurements
                .iter()
                .filter_map(|m| m.compiled.as_ref())
                .flatten()
                .filter_map(|c| c.get("value").and_then(serde_json::Value::as_f64))
                .collect();
            match values.iter().position(|v| dims::fmt(*v) == label) {
                Some(i) if i < 2 => {
                    value = Some(values[i]);
                    format!("location.{}", ["x", "y"][i])
                }
                _ => family,
            }
        } else {
            family
        };
        python.push(Item {
            family,
            view: ann.view.clone().unwrap_or_default(),
            label,
            value,
            origins,
        });
    }
    let rust: Vec<Item> = plan
        .dimensions
        .iter()
        .map(|d| Item {
            family: rust_family(d),
            view: d.view.trim_start_matches("v:").to_string(),
            label: d.label.clone(),
            value: Some(d.value),
            origins: d
                .measurements
                .iter()
                .map(|m| m.feature.as_str())
                .chain(d.id.strip_prefix("d:").and_then(|s| s.split('/').next()))
                .filter_map(|f| dims::feature_origin(a, f))
                .collect(),
        })
        .collect();

    let same = |p: &Item, r: &Item| {
        if (&p.family, &p.view, &p.label) != (&r.family, &r.view, &r.label) {
            return false;
        }
        // Values agree within half the printed unit (the labels are already equal); a location's
        // identity is its view, axis and distance; a callout's, where its features are.
        match p.family.as_str() {
            "callout" | "fillet" | "chamfer" | "groove" | "flat" => {
                let skip = depth_axis(&p.view);
                p.origins.iter().all(|o| {
                    r.origins
                        .iter()
                        .any(|q| (0..3).all(|k| k == skip || (o[k] - q[k]).abs() < 0.01))
                })
            }
            _ => p.value.is_none_or(|v| (v - r.value.unwrap()).abs() < 0.05),
        }
    };
    let mut used = vec![false; rust.len()];
    let mut diffs: BTreeMap<Diff, usize> = BTreeMap::new();
    let mut agree = 0;
    for p in &python {
        match (0..rust.len()).find(|&i| !used[i] && same(p, &rust[i])) {
            Some(i) => {
                used[i] = true;
                agree += 1;
            }
            None => {
                *diffs
                    .entry((
                        name.into(),
                        "python".into(),
                        p.family.clone(),
                        p.view.clone(),
                        p.label.clone(),
                    ))
                    .or_default() += 1
            }
        }
    }
    for (i, r) in rust.iter().enumerate() {
        if !used[i] {
            *diffs
                .entry((
                    name.into(),
                    "rust".into(),
                    r.family.clone(),
                    r.view.clone(),
                    r.label.clone(),
                ))
                .or_default() += 1;
        }
    }
    (agree, diffs, outside)
}

#[test]
fn dimensions_match_draftwright_or_are_known() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let known: Vec<Known> = serde_json::from_str(
        &std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/known_dimensions.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let verdicts = [
        "rust-correct",
        "rust-wrong",
        "equivalent",
        "undetermined",
        "not-applicable",
    ];
    let mut listed: BTreeMap<Diff, usize> = BTreeMap::new();
    for k in &known {
        assert!(
            verdicts.contains(&k.verdict.as_str()) && !k.reason.is_empty() && k.count > 0,
            "{k:?}"
        );
        let key = (
            k.part.clone(),
            k.side.clone(),
            k.family.clone(),
            k.view.clone(),
            k.label.clone(),
        );
        assert!(listed.insert(key, k.count).is_none(), "listed twice: {k:?}");
    }
    let mut found: BTreeMap<Diff, usize> = BTreeMap::new();
    let (mut agree, mut compared, mut outside) = (0, 0, 0);
    for name in facts::names() {
        let Some((a, d, o)) = compare(&name, &corpus) else {
            continue;
        };
        agree += a;
        outside += o;
        compared += 1;
        found.extend(d);
    }
    for fixture in FIXTURES {
        let (a, d, o) = compare_fixture(fixture);
        agree += a;
        outside += o;
        found.extend(d);
    }
    if std::env::var_os("DIMS_REPORT").is_some() {
        for ((part, side, family, view, label), n) in &found {
            println!("{part}\t{side}\t{family}\t{view}\t{label}\t{n}");
        }
    }
    eprintln!(
        "{compared} sheets: {agree} of draftwright's in-scope items agree; {} differences listed; {outside} of its dimensions and callouts are outside this port's families",
        found.values().sum::<usize>()
    );
    let unlisted: Vec<_> = found
        .iter()
        .filter(|(k, n)| listed.get(*k) != Some(n))
        .collect();
    let gone: Vec<_> = listed
        .iter()
        .filter(|(k, n)| found.get(*k) != Some(n))
        .collect();
    assert!(
        unlisted.is_empty() && gone.is_empty(),
        "differences not listed (or with another count): {unlisted:#?}\nlisted but not found (or with another count): {gone:#?}"
    );
    assert_eq!(
        compared, 12,
        "every sheet with recognition JSON is compared"
    );
}
