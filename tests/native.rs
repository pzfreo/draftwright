//! Features from Rust quiddity (`bridge::native`: holes and hole patterns, fillets, chamfers,
//! countersinks, bosses, turned steps, grooves, flats, the steps, blends, slots and slot patterns,
//! polygonal bosses and stock, double-D bores, plates, gusset ribs and their patterns, oriented
//! slots and their patterns, raised pads) against the same parts' Python quiddity recognition
//! through the bridge, record by record.
//!
//! Every part with bridged Python recognition is compared: the fixtures in `tests/fixtures/parts/`
//! (set `parts`), the slot-pattern plates (`slot_patterns`) and quiddity-rust's golden parts with
//! a double-D bore, gusset ribs, hexagonal stock and a pattern of oriented slots (`golden`), which
//! no corpus part has, the two fixture parts with raised pads Python quiddity 0.3.9 finds
//! (`pads`), the sampled corpus parts with Python quiddity 0.4.0.dev0's recognition
//! (`recognition/corpus/`, set `corpus`) and 0.3.9's, the recogniser draftwright drew their sheets
//! with (`recognition/sheets/`, set `sheets`), and 0.3.9's on corpus parts with features no
//! sampled sheet has (`recognition/dims/`, set `dims`, and `recognition/steps/`, set `steps`), and
//! 0.4.0.dev0's on the one corpus part with an oriented slot (`recognition/oriented/`, set
//! `oriented`).
//! sheets' ctc04 is not compared: the bridge refused its recognition until its face check took
//! OpenCascade's default-precision sphere areas for what they are
//! (`tests/fixtures/known_bridge_faces.json`), and its differences are not listed yet.
//!
//! Python's records are in its working frame and are mapped into the part's (the native ones
//! already are): points, directions, axis letters, a turned step's `lo` and `hi` and its
//! profile's box, a flat's stock span and axis line, and a step's section, where the frame allows
//! ([`other_to_part`]), and a slot's, prism's, plate's and rib's coordinates along its axes
//! ([`kind_to_part`]). A slot pattern's members are named by their Python pairs. Holes are paired by axis, location and diameter; patterns by their
//! members; the other kinds by where they are ([`anchor`]). Oriented slot patterns, which neither
//! reader makes features, are compared as records: Python's derived ones against the native
//! document's, paired by their members.
//! For each pair the IDs, faces and parameters are compared, numbers to 1e-6 relative (absolute
//! below 1), or within the step quiddity rounds a field to when the two frames round it
//! differently ([`rounding`]); for each pattern also the arrangement and its members. A pair's
//! fingerprints are compared field by field and the agreement counted and printed, not listed:
//! who owns fingerprints is an open question. Every difference is listed
//! in `tests/fixtures/known_native.json` with the values on both sides, a verdict and the
//! evidence. The test fails on a difference not listed, a listed one that no longer occurs or
//! has changed, and a duplicate entry, and prints the entries it needs (`NATIVE_REPORT=1` prints
//! every difference).

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::bridge::{self, Feature, HolePattern, native};
use draftwright::haecceity::Part;
use draftwright::model;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    set: String,
    part: String,
    /// Python's ID of the feature or pattern, or `rust <ID>`, the native one, when Python has none.
    subject: String,
    /// `presence`, `id`, `faces`, `opening` (a through hole's location and axis), `free_end` (a
    /// boss's location and axis, read from its other end), `arrangement`, `members` or
    /// `parameters.<path>`.
    field: String,
    python: String,
    rust: String,
    /// `rust-correct`, `rust-wrong`, `equivalent`, `undetermined` or `not-applicable`.
    verdict: String,
    evidence: String,
}

type Key = (String, String, String, String);

#[derive(Debug, PartialEq)]
struct Difference {
    python: String,
    rust: String,
}

struct Case {
    set: &'static str,
    part: String,
    step: PathBuf,
    json: String,
}

fn gunzip(path: &Path) -> Option<String> {
    let mut text = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).ok()?)
        .read_to_string(&mut text)
        .unwrap();
    Some(text)
}

/// The STEP file of fixture *name*: `<name>.step`, or `<name>.step.gz` unpacked once into the
/// test's scratch directory.
fn plain_step(name: &str) -> PathBuf {
    let plain = fixture(&format!("{name}.step"));
    if plain.exists() {
        return plain;
    }
    let file = Path::new(name).file_name().unwrap().to_string_lossy();
    let out = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("native_{file}.step"));
    if !out.exists() {
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(
            std::fs::File::open(fixture(&format!("{name}.step.gz"))).unwrap(),
        )
        .read_to_end(&mut bytes)
        .unwrap();
        // Written whole then renamed, so another test binary never reads half a file.
        let partial = out.with_extension(format!("step.{}", std::process::id()));
        std::fs::write(&partial, bytes).unwrap();
        std::fs::rename(&partial, &out).unwrap();
    }
    out
}

/// Every part with bridged Python recognition; the corpus sets only when the corpus is found.
fn cases() -> Vec<Case> {
    let mut out = Vec::new();
    for part in ["boss_circle", "plate", "prismatic", "turned", "two_grids"] {
        out.push(Case {
            set: "parts",
            part: part.into(),
            step: fixture(&format!("parts/{part}.step")),
            json: std::fs::read_to_string(fixture(&format!("recognition/{part}.json"))).unwrap(),
        });
    }
    // Fixture parts with a family no corpus part has: the slot-pattern plates
    // (`recognition/slot_patterns/`, quiddity 0.3.9), and quiddity-rust's golden parts with a
    // double-D bore, gusset ribs, hexagonal stock and oriented slots in a pattern
    // (`recognition/golden/`, copied from its `tests/fixtures/golden_<name>.step` at the pinned
    // commit, recognised by `tools/recognise.py` with Python quiddity 0.4.0.dev0).
    for (set, part) in [
        ("slot_patterns", "slot_array"),
        ("slot_patterns", "slot_grid"),
        ("golden", "double_d_bore"),
        ("golden", "gusset_ribs"),
        ("golden", "gusset_rib_patterns"),
        ("golden", "polygonal_stock"),
        ("golden", "oriented_slots"),
    ] {
        out.push(Case {
            set,
            part: part.into(),
            step: fixture(&format!("recognition/{set}/{part}.step")),
            json: gunzip(&fixture(&format!("recognition/{set}/{part}.json.gz"))).unwrap(),
        });
    }
    // The fixture parts whose Python recognition (quiddity 0.3.9) has raised pads, which no
    // compared corpus recognition has: quiddity's golden plates, pads, levels and slanted steps
    // (`recognition/steps/`) and the whistle frame (`recognition/issues/`, its STEP file gzipped).
    for (dir, part) in [
        ("steps", "plates_pads_levels_and_slanted_steps"),
        ("issues", "whistle_frame_reference"),
    ] {
        out.push(Case {
            set: "pads",
            part: part.into(),
            step: plain_step(&format!("recognition/{dir}/{part}")),
            json: gunzip(&fixture(&format!("recognition/{dir}/{part}.json.gz"))).unwrap(),
        });
    }
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus parts): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return out;
    };
    for set in ["corpus", "sheets"] {
        let mut names: Vec<String> = std::fs::read_dir(fixture(&format!("recognition/{set}")))
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .filter_map(|f| f.strip_suffix(".json.gz").map(String::from))
            .collect();
        names.sort();
        for part in names {
            let json = gunzip(&fixture(&format!("recognition/{set}/{part}.json.gz"))).unwrap();
            out.push(Case {
                set,
                step: dir.join(&facts::load(&part).binding.file),
                part,
                json,
            });
        }
    }
    // Corpus parts with features no sampled sheet has: `dims` (`tests/dims_slots.rs`'s), and
    // `steps`, parts with paired-ramp, circular blind and oblique through steps, written for this
    // test by `tools/recognise.py` with draftwright's environment (quiddity 0.3.9); and
    // `oriented`, the one corpus part with an oriented slot (in either recogniser's reading),
    // written by `tools/recognise.py` with quiddity 0.4.0.dev0.
    for (set, part, file) in [
        ("dims", "ftc07", "nist/nist_ftc_07_asme1_rd.stp"),
        ("dims", "m10047", "mfcadpp/10047.step"),
        ("dims", "m10245", "mfcadpp/10245.step"),
        ("dims", "m11512", "mfcadpp_regressions/11512.step"),
        ("steps", "h363", "mfcadpp_holdout/363.step"),
        ("steps", "m10000", "mfcadpp/10000.step"),
        ("steps", "m10060", "mfcadpp/10060.step"),
        ("steps", "m1007", "mfcadpp/1007.step"),
        ("oriented", "h467", "mfcadpp_holdout/467.step"),
    ] {
        out.push(Case {
            set,
            part: part.into(),
            step: dir.join(file),
            json: gunzip(&fixture(&format!("recognition/{set}/{part}.json.gz"))).unwrap(),
        });
    }
    out
}

/// A record value as the list shows it: compact JSON, numbers to 1e-6.
fn show(v: &Value) -> String {
    fn rounded(v: &Value) -> Value {
        match v {
            Value::Number(n) => {
                let x = (n.as_f64().unwrap() * 1e6).round() / 1e6;
                Value::from(if x == 0.0 { 0.0 } else { x })
            }
            Value::Array(a) => Value::Array(a.iter().map(rounded).collect()),
            Value::Object(o) => Value::Object(
                o.iter()
                    .map(|(k, v)| (k.clone(), rounded(v)))
                    .collect::<Map<_, _>>(),
            ),
            v => v.clone(),
        }
    }
    rounded(v).to_string()
}

fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-6 * a.abs().max(b.abs()).max(1.0)
}

/// Python's record mapped into the part's frame: points (`location`, `center`) and directions
/// (`axis`, `direction`), here and in nested records; empty `named_dimensions` dropped (the
/// native document never carries them, and an empty one says nothing).
fn to_part(frame: &[[f64; 3]; 4], record: &Map<String, Value>) -> Map<String, Value> {
    let mut out = Map::new();
    for (k, v) in record {
        let mapped = match (k.as_str(), bridge::holes::point(Some(v))) {
            ("location" | "center", Some(p)) => {
                Value::from(bridge::holes::to_part(frame, p).to_vec())
            }
            ("axis" | "direction", Some(d)) => {
                Value::from(bridge::holes::dir_to_part(frame, d).to_vec())
            }
            ("named_dimensions", _) if v.as_object().is_some_and(Map::is_empty) => continue,
            (_, _) => match v {
                Value::Object(o) => Value::Object(to_part(frame, o)),
                v => v.clone(),
            },
        };
        out.insert(k.clone(), mapped);
    }
    out
}

/// The kinds read natively besides holes, as `bridge::native::READ` names their families.
const KINDS: [&str; 26] = [
    "fillet",
    "cylindrical_profile",
    "face_groove",
    "turned_body",
    "chamfer",
    "countersink",
    "boss",
    "turned_step",
    "groove",
    "flat",
    "angled_step",
    "paired_ramp_step",
    "circular_blind_step",
    "through_step",
    "oblique_through_step",
    "blend",
    // Before `slot_pattern`, whose members name slots.
    "slot",
    "slot_pattern",
    "polygonal_boss",
    "polygonal_stock",
    "double_d_bore",
    "plate",
    "gusset_rib",
    "gusset_rib_pattern",
    "oriented_slot",
    "pad",
];

/// Where a feature of *kind* is, as its record gives it: what pairs a Python feature with a
/// native one.
fn anchor(kind: &str) -> &'static [&'static str] {
    match kind {
        "countersink" | "boss" => &["location"],
        "turned_step" => &["axis", "lo", "hi"],
        "cylindrical_profile" | "face_groove" | "turned_body" => &["axis", "axis_point"],
        "circular_blind_step" => &["centreline"],
        // The rolling ball (its radius and side) and its path's place, not the path's
        // direction: a line's sense follows the frame. Coaxial blends share a path's place.
        "blend" => &["radius", "side", "path.at", "path.center", "path.radius"],
        "slot" => &["long_axis", "lo", "hi", "w_center"],
        // A grid by its centre and lattice, an array by its pitch (its direction's sense follows
        // the frame).
        "slot_pattern" => &["center", "rows", "cols", "pitch"],
        "polygonal_boss" | "polygonal_stock" => &["center"],
        "double_d_bore" => &["location"],
        "plate" => &["axis", "lo", "hi"],
        "gusset_rib" => &["thickness_axis", "thickness_bounds"],
        "gusset_rib_pattern" => &["pitch", "mirror_plane"],
        "oriented_slot" => &["center"],
        "pad" => &["axis", "x0", "x1", "y0", "y1", "z0", "z1"],
        _ => &["at"],
    }
}

/// Field *path* (`a` or `a.b`) of *record*.
fn field<'a>(record: &'a Map<String, Value>, path: &str) -> Option<&'a Value> {
    match path.split_once('.') {
        Some((a, b)) => record.get(a)?.get(b),
        None => record.get(path),
    }
}

/// The step quiddity rounds a field of *kind* to in its own frame (zero when it does not round
/// it): rounded in Python's working frame and mapped, or rounded in the part's, one value can
/// differ by up to that step.
fn rounding(kind: &str, path: &str) -> f64 {
    let field = path.rsplit('.').next().unwrap_or(path);
    match (kind, field) {
        ("fillet" | "chamfer", "at" | "corner") => 1e-3,
        ("turned_step", "lo" | "hi") => 1e-3,
        // Rounded in quiddity's frame and again on the file's grid (quiddity-rust 0.1.0).
        ("countersink", "location") => 1e-3,
        ("groove" | "paired_ramp_step", "at") => 1e-3,
        ("flat", "at" | "stock_span" | "axis_line") => 1e-3,
        ("angled_step", "at" | "corner") => 1e-3,
        ("through_step", "at" | "section") => 1e-3,
        ("oblique_through_step", "at") => 1e-3,
        // Six significant figures (`quantise6`), of coordinates below 100 in either frame.
        ("circular_blind_step", "centreline" | "section") => 1e-4,
        ("blend", "at" | "center") => 1e-3,
        ("slot", "lo" | "hi" | "w_center" | "d_lo" | "d_hi") => 1e-2,
        ("plate", "lo" | "hi") => 1e-3,
        ("polygonal_boss" | "polygonal_stock", "center" | "base" | "top") => 1e-4,
        ("polygonal_boss" | "polygonal_stock", "flat_centres" | "flat_directions") => 1e-3,
        ("gusset_rib" | "gusset_rib_pattern", "thickness_bounds" | "supports") => 1e-3,
        // Sizes and points to 3 decimals, directions to 6 (`OrientedSlot`).
        ("oriented_slot", "center" | "origin" | "width" | "length") => 1e-3,
        ("oriented_slot", "width_direction" | "long_direction" | "run" | "u" | "v") => 1e-6,
        // The box to 3 decimals (`RaisedPad`).
        ("pad", "x0" | "x1" | "y0" | "y1" | "z0" | "z1") => 1e-3,
        _ => 0.0,
    }
}

/// Working axis *j* of *frame* in the part's: the part's axis it lies along and its sense, if
/// it lies along one.
fn along(frame: &[[f64; 3]; 4], j: usize) -> Option<(usize, f64)> {
    let d = frame[j + 1];
    bridge::holes::principal(d).map(|k| (k, d[k].signum()))
}

/// Two numbers.
fn pair(v: Option<&Value>) -> Option<[f64; 2]> {
    let a = v?.as_array()?;
    match a.as_slice() {
        [x, y] => Some([x.as_f64()?, y.as_f64()?]),
        _ => None,
    }
}

fn letter(v: &Value) -> Option<usize> {
    ["x", "y", "z"].iter().position(|l| v.as_str() == Some(l))
}

/// A box given as working-frame minima and maxima, in the part's frame: only when every working
/// axis lies along one of the part's.
fn box_to_part(frame: &[[f64; 3]; 4], lo: [f64; 3], hi: [f64; 3]) -> Option<([f64; 3], [f64; 3])> {
    let (mut a, mut b) = ([0.0; 3], [0.0; 3]);
    for j in 0..3 {
        let (k, s) = along(frame, j)?;
        let (p, q) = (frame[0][k] + s * lo[j], frame[0][k] + s * hi[j]);
        (a[k], b[k]) = (p.min(q), p.max(q));
    }
    Some((a, b))
}

/// Python's record of a feature of one of [`KINDS`] in the part's frame: points (`at`, `corner`,
/// `location`, `center`, `axis_origin`, each of a `centreline`) and directions as [`to_part`]
/// maps them; an axis letter as the part's axis it lies along (a letter names a line, so no
/// sense), or the direction when it lies along none; a turned step's `lo` and `hi` and a flat's
/// `stock_span`, coordinates along its axis, a flat's `axis_line` and a step's `section`,
/// coordinates across it, and a profile's or step's body box and key, where its axis or every
/// axis lies along the part's.
fn other_to_part(frame: &[[f64; 3]; 4], record: &Map<String, Value>) -> Map<String, Value> {
    let mut out = Map::new();
    for (k, v) in record {
        let mapped = match (k.as_str(), bridge::holes::point(Some(v)), letter(v)) {
            ("at" | "corner" | "location" | "center" | "axis_origin", Some(p), _) => {
                Value::from(bridge::holes::to_part(frame, p).to_vec())
            }
            (
                "axis" | "direction" | "normal" | "axis_direction" | "opening_direction" | "run"
                | "depth_direction" | "across_direction",
                Some(d),
                _,
            ) => Value::from(bridge::holes::dir_to_part(frame, d).to_vec()),
            ("centreline", _, _) => Value::from(
                v.as_array()
                    .unwrap()
                    .iter()
                    .map(|p| {
                        let p = bridge::holes::point(Some(p)).unwrap();
                        Value::from(bridge::holes::to_part(frame, p).to_vec())
                    })
                    .collect::<Vec<_>>(),
            ),
            ("axis", None, Some(j)) => match along(frame, j) {
                Some((k, _)) => Value::from(["x", "y", "z"][k]),
                None => Value::from(frame[j + 1].to_vec()),
            },
            ("named_dimensions", _, _) if v.as_object().is_some_and(Map::is_empty) => continue,
            ("body_bounds", _, _) => {
                let b: Vec<f64> = v
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter_map(Value::as_f64)
                    .collect();
                match box_to_part(frame, [b[0], b[2], b[4]], [b[1], b[3], b[5]]) {
                    Some((a, b)) => Value::from(vec![a[0], b[0], a[1], b[1], a[2], b[2]]),
                    None => v.clone(),
                }
            }
            ("body_key", _, _) if v.is_array() => {
                let b: Vec<f64> = v
                    .as_array()
                    .unwrap()
                    .iter()
                    .filter_map(Value::as_f64)
                    .collect();
                match box_to_part(frame, [b[0], b[1], b[2]], [b[3], b[4], b[5]]) {
                    Some((a, c)) => Value::from([&a[..], &c[..], &b[6..]].concat()),
                    None => v.clone(),
                }
            }
            (_, _, _) => match v {
                Value::Object(o) => Value::Object(other_to_part(frame, o)),
                v => v.clone(),
            },
        };
        out.insert(k.clone(), mapped);
    }
    let axis = record
        .get("axis")
        .and_then(letter)
        .and_then(|j| along(frame, j));
    if let (Some((k, s)), Some(lo), Some(hi)) = (
        axis,
        record.get("lo").and_then(Value::as_f64),
        record.get("hi").and_then(Value::as_f64),
    ) {
        let (p, q) = (frame[0][k] + s * lo, frame[0][k] + s * hi);
        out.insert("lo".into(), Value::from(p.min(q)));
        out.insert("hi".into(), Value::from(p.max(q)));
    }
    // A flat's stock span, coordinates along its axis, as `lo` and `hi` are.
    if let (Some((k, s)), Some([lo, hi])) = (axis, pair(record.get("stock_span"))) {
        let (p, q) = (frame[0][k] + s * lo, frame[0][k] + s * hi);
        out.insert("stock_span".into(), Value::from(vec![p.min(q), p.max(q)]));
    }
    // Points across an axis (a flat's axis line, a step's section): their coordinates along the
    // two other axes, in `x`, `y`, `z` order, in the part's when every axis lies along one.
    if let Some(j) = record.get("axis").and_then(letter) {
        let across = |q: &[f64; 2]| -> Option<Value> {
            let mut local = [0.0; 3];
            for (i, l) in (0..3).filter(|&l| l != j).enumerate() {
                local[l] = q[i];
            }
            let (k, _) = along(frame, j)?;
            (0..3).all(|l| along(frame, l).is_some()).then_some(())?;
            let p = bridge::holes::to_part(frame, local);
            Some(Value::from(
                (0..3).filter(|&l| l != k).map(|l| p[l]).collect::<Vec<_>>(),
            ))
        };
        if let Some(q) = pair(record.get("axis_line"))
            && let Some(v) = across(&q)
        {
            out.insert("axis_line".into(), v);
        }
        let section: Option<Vec<[f64; 2]>> = record
            .get("section")
            .and_then(Value::as_array)
            .and_then(|a| a.iter().map(|q| pair(Some(q))).collect());
        if let Some(v) = section.and_then(|q| q.iter().map(across).collect::<Option<Vec<_>>>()) {
            out.insert("section".into(), Value::from(v));
        }
    }
    // A turned profile's `axis_origin` has its axial coordinate zeroed, in the frame it is
    // given in: zeroed again in the part's.
    if let Some(Value::Object(profile)) = out.get_mut("profile")
        && let Some(k) = profile.get("axis").and_then(letter)
        && let Some(mut o) = bridge::holes::point(profile.get("axis_origin"))
    {
        o[k] = 0.0;
        profile.insert("axis_origin".into(), Value::from(o.to_vec()));
    }
    out
}

/// [`other_to_part`], and what it does not map of the slot, polygonal prism, double-D bore, plate
/// and gusset rib records, in the part's frame where the frame's axes lie along the part's: a
/// slot's axis letters and its coordinates along them (`lo`, `hi`, `w_center`, `d_lo`, `d_hi`);
/// a prism's `base` and `top` along its axis, its flats' directions and centres; a bore's flat
/// direction; a plate's `u` and `v` (the centre on its other two axes, in axis order); a rib's
/// thickness axis and bounds, and its supports (axis letter and plane) with their legs and
/// directions, ordered by the part's axis as `lint/gussets.rs` orders them, here and in a
/// pattern's `ribs`; a mirrored pair's plane; a pad's box and the sense it rises in.
fn kind_to_part(
    kind: &str,
    frame: &[[f64; 3]; 4],
    record: &Map<String, Value>,
) -> Map<String, Value> {
    let mut out = other_to_part(frame, record);
    let axis = |v: Option<&Value>| v.and_then(letter).and_then(|j| along(frame, j));
    let at = |k: usize, s: f64, v: f64| frame[0][k] + s * v;
    let number = |k: &str| record.get(k).and_then(Value::as_f64);
    let sorted = |a: f64, b: f64| (a.min(b), a.max(b));
    match kind {
        "slot" => {
            let letters = (
                record.get("long_axis").and_then(letter),
                record.get("width_axis").and_then(letter),
            );
            if let (Some(jl), Some(jw)) = letters
                && jl != jw
                && let (Some((l, sl)), Some((w, sw)), Some((d, sd))) = (
                    along(frame, jl),
                    along(frame, jw),
                    along(frame, 3 - jl - jw),
                )
            {
                out.insert("long_axis".into(), ["x", "y", "z"][l].into());
                out.insert("width_axis".into(), ["x", "y", "z"][w].into());
                for (lo, hi, k, s) in [("lo", "hi", l, sl), ("d_lo", "d_hi", d, sd)] {
                    if let (Some(a), Some(b)) = (number(lo), number(hi)) {
                        let (a, b) = sorted(at(k, s, a), at(k, s, b));
                        out.insert(lo.into(), a.into());
                        out.insert(hi.into(), b.into());
                    }
                }
                if let Some(c) = number("w_center") {
                    out.insert("w_center".into(), at(w, sw, c).into());
                }
            }
        }
        "polygonal_boss" | "polygonal_stock" => {
            if let Some((k, s)) = axis(record.get("axis")) {
                for key in ["base", "top"] {
                    if let Some(v) = number(key) {
                        out.insert(key.into(), at(k, s, v).into());
                    }
                }
            }
            let each = |key: &str, f: &dyn Fn([f64; 3]) -> [f64; 3]| -> Option<Value> {
                let a = record.get(key)?.as_array()?;
                Some(Value::from(
                    a.iter()
                        .map(|p| Some(Value::from(f(bridge::holes::point(Some(p))?).to_vec())))
                        .collect::<Option<Vec<_>>>()?,
                ))
            };
            if let Some(v) = each("flat_directions", &|d| bridge::holes::dir_to_part(frame, d)) {
                out.insert("flat_directions".into(), v);
            }
            if let Some(v) = each("flat_centres", &|p| bridge::holes::to_part(frame, p)) {
                out.insert("flat_centres".into(), v);
            }
        }
        "double_d_bore" => {
            if let Some(d) = bridge::holes::point(record.get("flat_direction")) {
                out.insert(
                    "flat_direction".into(),
                    Value::from(bridge::holes::dir_to_part(frame, d).to_vec()),
                );
            }
        }
        "plate" => {
            if let Some(j) = record.get("axis").and_then(letter)
                && let Some((k, _)) = along(frame, j)
                && (0..3).all(|l| along(frame, l).is_some())
                && let (Some(u), Some(v)) = (number("u"), number("v"))
            {
                let mut local = [0.0; 3];
                let others: Vec<usize> = (0..3).filter(|&l| l != j).collect();
                (local[others[0]], local[others[1]]) = (u, v);
                let p = bridge::holes::to_part(frame, local);
                let across: Vec<usize> = (0..3).filter(|&l| l != k).collect();
                out.insert("u".into(), p[across[0]].into());
                out.insert("v".into(), p[across[1]].into());
            }
        }
        "oriented_slot" => {
            for key in ["width_direction", "long_direction"] {
                if let Some(d) = bridge::holes::point(record.get(key)) {
                    out.insert(
                        key.into(),
                        Value::from(bridge::holes::dir_to_part(frame, d).to_vec()),
                    );
                }
            }
            // The source passage's frame: its origin a point, its run, u and v directions.
            if let Some(f) = record.get("source").and_then(|s| s.get("frame"))
                && let Some(Value::Object(source)) = out.get_mut("source")
            {
                let mut mapped = Map::new();
                for (k, v) in f.as_object().into_iter().flatten() {
                    let p = bridge::holes::point(Some(v));
                    let m = match (k.as_str(), p) {
                        ("origin", Some(p)) => bridge::holes::to_part(frame, p).to_vec().into(),
                        (_, Some(d)) => bridge::holes::dir_to_part(frame, d).to_vec().into(),
                        _ => v.clone(),
                    };
                    mapped.insert(k.clone(), m);
                }
                source.insert("frame".into(), mapped.into());
            }
        }
        "pad" => {
            let corner = |end: usize| -> Option<[f64; 3]> {
                let mut c = [0.0; 3];
                for (j, a) in ["x", "y", "z"].iter().enumerate() {
                    c[j] = number(&format!("{a}{end}"))?;
                }
                Some(c)
            };
            if let (Some(lo), Some(hi)) = (corner(0), corner(1))
                && let Some((a, b)) = box_to_part(frame, lo, hi)
            {
                for (k, l) in ["x", "y", "z"].iter().enumerate() {
                    out.insert(format!("{l}0"), a[k].into());
                    out.insert(format!("{l}1"), b[k].into());
                }
            }
            if let (Some((_, s)), Some(d)) = (
                axis(record.get("axis")),
                record.get("direction").and_then(Value::as_f64),
            ) {
                out.insert("direction".into(), Value::from((s * d) as i64));
            }
        }
        "gusset_rib" => {
            if let Some(r) = rib_to_part(frame, record, out.clone()) {
                out = r;
            }
        }
        "gusset_rib_pattern" => {
            if let Some(Value::Array(ribs)) = record.get("ribs") {
                let mapped: Option<Vec<Value>> = ribs
                    .iter()
                    .map(|r| {
                        let r = r.as_object()?;
                        Some(Value::Object(rib_to_part(
                            frame,
                            r,
                            other_to_part(frame, r),
                        )?))
                    })
                    .collect();
                if let Some(m) = mapped {
                    out.insert("ribs".into(), m.into());
                }
            }
            if let Some(m) = plane_to_part(frame, record.get("mirror_plane")) {
                out.insert("mirror_plane".into(), m.0);
            }
        }
        _ => {}
    }
    out
}

/// An axis letter and a coordinate along it (a plane across that axis) in the part's frame, and
/// the sense the axis maps with.
fn plane_to_part(frame: &[[f64; 3]; 4], v: Option<&Value>) -> Option<(Value, usize, f64)> {
    let a = v?.as_array()?;
    let (k, s) = along(frame, letter(a.first()?)?)?;
    let at = frame[0][k] + s * a.get(1)?.as_f64()?;
    Some((
        Value::from(vec![Value::from(["x", "y", "z"][k]), at.into()]),
        k,
        s,
    ))
}

/// A gusset rib's *record* in the part's frame (see [`kind_to_part`]), its other fields as *out*
/// has them, when every field it maps is readable and its axes lie along the part's.
fn rib_to_part(
    frame: &[[f64; 3]; 4],
    record: &Map<String, Value>,
    mut out: Map<String, Value>,
) -> Option<Map<String, Value>> {
    let (k, s) = along(frame, letter(record.get("thickness_axis")?)?)?;
    let [a, b] = pair(record.get("thickness_bounds"))?;
    let (a, b) = (frame[0][k] + s * a, frame[0][k] + s * b);
    out.insert("thickness_axis".into(), ["x", "y", "z"][k].into());
    out.insert(
        "thickness_bounds".into(),
        Value::from(vec![a.min(b), a.max(b)]),
    );
    let legs = pair(record.get("legs"))?;
    let directions = pair(record.get("directions"))?;
    let supports = record.get("supports")?.as_array()?;
    let mut sides = Vec::new();
    for (i, support) in supports.iter().enumerate() {
        let (plane, j, sj) = plane_to_part(frame, Some(support))?;
        sides.push((j, plane, *legs.get(i)?, directions.get(i)? * sj));
    }
    sides.sort_by_key(|side| side.0);
    out.insert(
        "supports".into(),
        Value::from(sides.iter().map(|s| s.1.clone()).collect::<Vec<_>>()),
    );
    out.insert(
        "legs".into(),
        Value::from(sides.iter().map(|s| s.2).collect::<Vec<_>>()),
    );
    out.insert(
        "directions".into(),
        Value::from(sides.iter().map(|s| s.3).collect::<Vec<_>>()),
    );
    Some(out)
}

/// Whether two boss records are one boss read from its two ends: the same diameter and height,
/// axes reversed, each location the other's moved by the height (to its 0.01 rounding) along
/// its axis.
fn other_end(p: &Map<String, Value>, q: &Map<String, Value>) -> bool {
    let (Some(a), Some(b), Some(height)) = (
        bridge::holes::point(p.get("axis")),
        bridge::holes::point(q.get("axis")),
        p.get("height").and_then(Value::as_f64),
    ) else {
        return false;
    };
    compare_none(p.get("diameter"), q.get("diameter"))
        && compare_none(p.get("height"), q.get("height"))
        && (0..3).all(|k| close(a[k], -b[k]))
        && {
            let d: [f64; 3] = std::array::from_fn(|k| location(p)[k] - location(q)[k]);
            let along: f64 = (0..3).map(|k| d[k] * a[k]).sum();
            (along - height).abs() <= 0.005 + 1e-6 * height
                && (0..3).all(|k| close(location(q)[k] + along * a[k], location(p)[k]))
        }
}

/// The differences between two values under *path*, into *out*.
fn compare(
    path: &str,
    py: Option<&Value>,
    rs: Option<&Value>,
    out: &mut Vec<(String, Difference)>,
) {
    compare_within(path, py, rs, &|_| 0.0, out);
}

/// [`compare`], numbers under a path also agreeing within `rounding(path)`: the step quiddity
/// rounds that field to in its own frame, so the same value rounded in two frames.
fn compare_within(
    path: &str,
    py: Option<&Value>,
    rs: Option<&Value>,
    rounding: &dyn Fn(&str) -> f64,
    out: &mut Vec<(String, Difference)>,
) {
    let differ = match (py, rs) {
        (Some(Value::Object(a)), Some(Value::Object(b))) => {
            let keys: BTreeSet<&String> = a.keys().chain(b.keys()).collect();
            for k in keys {
                compare_within(&format!("{path}.{k}"), a.get(k), b.get(k), rounding, out);
            }
            false
        }
        (Some(Value::Array(a)), Some(Value::Array(b))) if a.len() == b.len() => {
            let mut inner = Vec::new();
            for (x, y) in a.iter().zip(b) {
                compare_within(path, Some(x), Some(y), rounding, &mut inner);
            }
            !inner.is_empty()
        }
        (Some(Value::Number(a)), Some(Value::Number(b))) => {
            let (a, b) = (a.as_f64().unwrap(), b.as_f64().unwrap());
            !close(a, b) && (a - b).abs() > rounding(path) + 1e-9
        }
        (a, b) => a != b,
    };
    if differ {
        let s = |v: Option<&Value>| v.map_or("-".to_string(), show);
        out.push((
            path.to_string(),
            Difference {
                python: s(py),
                rust: s(rs),
            },
        ));
    }
}

fn location(p: &Map<String, Value>) -> [f64; 3] {
    bridge::holes::point(p.get("location")).unwrap()
}

/// A hole as the list names it when it has no counterpart: its ID, faces and record.
fn summary(f: &Feature, record: &Map<String, Value>) -> String {
    format!(
        "{} {:?} {}",
        f.id,
        f.faces,
        show(&Value::Object(record.clone()))
    )
}

/// The differences on one part, read by the bridge as *recognition* and *features*, by
/// (subject, field).
fn differences(
    part: &Part,
    case: &Case,
    recognition: &bridge::Recognition,
    features: &[Feature],
    fingerprints: &mut Fingerprints,
) -> BTreeMap<(String, String), Difference> {
    let frame = recognition.frame;
    let py: Vec<(&Feature, Map<String, Value>)> = features
        .iter()
        .filter(|f| f.kind == "hole")
        .map(|f| (f, to_part(&frame, &f.parameters)))
        .collect();
    let doc = quiddity::recognition::document(
        &quiddity::correspondence::recognise(part),
        part.faces.len(),
    );
    let rs = native::read(part, &doc).unwrap_or_else(|e| panic!("{}: {e}", case.part));
    let mut out = BTreeMap::new();
    let mut put = |subject: &str, field: &str, d: Difference| {
        let old = out.insert((subject.to_string(), field.to_string()), d);
        assert!(old.is_none(), "{} {subject} {field} twice", case.part);
    };

    // Holes, paired by axis, location and diameter (each native hole at most once); a through
    // hole also with the one drilled from its other opening (its axis reversed, its location
    // the other end of its depth).
    let mut pair: BTreeMap<String, String> = BTreeMap::new();
    let mut taken = vec![false; rs.holes.len()];
    for (f, p) in &py {
        let found = rs.holes.iter().enumerate().position(|(i, h)| {
            let q = &h.parameters;
            !taken[i]
                && compare_none(p.get("diameter"), q.get("diameter"))
                && (same_opening(p, q) || other_opening(p, q))
        });
        let Some(i) = found else {
            put(
                &f.id,
                "presence",
                Difference {
                    python: summary(f, p),
                    rust: "-".into(),
                },
            );
            continue;
        };
        taken[i] = true;
        let h = &rs.holes[i];
        fingerprints.tally("hole", &f.fingerprint, &h.fingerprint);
        pair.insert(h.id.clone(), f.id.clone());
        if h.id != f.id {
            put(
                &f.id,
                "id",
                Difference {
                    python: f.id.clone(),
                    rust: h.id.clone(),
                },
            );
        }
        if h.faces != f.faces {
            put(
                &f.id,
                "faces",
                Difference {
                    python: format!("{:?}", f.faces),
                    rust: format!("{:?}", h.faces),
                },
            );
        }
        let (mut p, mut q) = (p.clone(), h.parameters.clone());
        if !same_opening(&p, &q) {
            let opening = |r: &mut Map<String, Value>| {
                let at = r.remove("location").unwrap();
                let axis = r.remove("axis").unwrap();
                format!("{} along {}", show(&at), show(&axis))
            };
            put(
                &f.id,
                "opening",
                Difference {
                    python: opening(&mut p),
                    rust: opening(&mut q),
                },
            );
        }
        let mut diffs = Vec::new();
        compare(
            "parameters",
            Some(&Value::Object(p)),
            Some(&Value::Object(q)),
            &mut diffs,
        );
        for (field, d) in diffs {
            put(&f.id, &field, d);
        }
    }
    for (i, h) in rs.holes.iter().enumerate() {
        if !taken[i] {
            put(
                &format!("rust {}", h.id),
                "presence",
                Difference {
                    python: "-".into(),
                    rust: summary(h, &h.parameters),
                },
            );
        }
    }

    // Patterns, paired by their members (native members named by their Python pair).
    let members = |p: &HolePattern, map: Option<&BTreeMap<String, String>>| -> BTreeSet<String> {
        p.members
            .iter()
            .map(|m| {
                map.map_or(m.clone(), |map| {
                    map.get(m).cloned().unwrap_or(format!("rust {m}"))
                })
            })
            .collect()
    };
    let mut taken = vec![false; rs.patterns.len()];
    for p in &recognition.patterns {
        let mine = members(p, None);
        let Some(i) = rs
            .patterns
            .iter()
            .enumerate()
            .position(|(i, q)| !taken[i] && members(q, Some(&pair)) == mine)
        else {
            put(
                &p.id,
                "presence",
                Difference {
                    python: format!("{} {:?}", p.arrangement, p.members),
                    rust: "-".into(),
                },
            );
            continue;
        };
        taken[i] = true;
        let q = &rs.patterns[i];
        let mut put_if = |field: &str, a: String, b: String| {
            if a != b {
                put(&p.id, field, Difference { python: a, rust: b });
            }
        };
        put_if("id", p.id.clone(), q.id.clone());
        put_if("arrangement", p.arrangement.clone(), q.arrangement.clone());
        put_if("faces", format!("{:?}", p.faces), format!("{:?}", q.faces));
        let order: Vec<String> = q.members.iter().map(|m| pair[m].clone()).collect();
        put_if("members", format!("{:?}", p.members), format!("{order:?}"));
        let mut diffs = Vec::new();
        compare(
            "parameters",
            Some(&Value::Object(to_part(&frame, &p.parameters))),
            Some(&Value::Object(q.parameters.clone())),
            &mut diffs,
        );
        for (field, d) in diffs {
            put(&p.id, &field, d);
        }
    }
    for (i, q) in rs.patterns.iter().enumerate() {
        if !taken[i] {
            put(
                &format!("rust {}", q.id),
                "presence",
                Difference {
                    python: "-".into(),
                    rust: format!("{} {:?}", q.arrangement, q.members),
                },
            );
        }
    }

    // Native IDs of the other kinds paired, and their Python pairs' IDs.
    let mut paired: BTreeMap<String, String> = BTreeMap::new();
    // The other kinds, kind by kind, paired by where they are ([`anchor`]; each native feature at most once); a boss also with the one read
    // from its other end ([`other_end`]).
    for kind in KINDS {
        let py: Vec<(&Feature, Map<String, Value>)> = features
            .iter()
            .filter(|f| f.kind == kind)
            .map(|f| (f, kind_to_part(kind, &frame, &f.parameters)))
            .collect();
        let ours: Vec<&Feature> = rs.features.iter().filter(|f| f.kind == kind).collect();
        let within = |path: &str| rounding(kind, path);
        let mut taken = vec![false; ours.len()];
        for (f, p) in &py {
            // For quiddity-rust#134 a boss's free end can move across its chamfer.
            // Pair its physical cylinder faces first, so the new end cannot accidentally
            // pair with the next coaxial boss. All parameters are still compared below.
            let same_faces = (kind == "boss" && !f.faces.is_empty())
                .then(|| {
                    ours.iter()
                        .enumerate()
                        .position(|(i, h)| !taken[i] && h.faces == f.faces)
                })
                .flatten();
            let found = same_faces.or_else(|| {
                ours.iter().enumerate().position(|(i, h)| {
                    !taken[i]
                        && (anchor(kind).iter().all(|k| {
                            let mut d = Vec::new();
                            compare_within(
                                k,
                                field(p, k),
                                field(&h.parameters, k),
                                &within,
                                &mut d,
                            );
                            d.is_empty()
                        }) || (kind == "boss" && other_end(p, &h.parameters)))
                })
            });
            let Some(i) = found else {
                put(
                    &f.id,
                    "presence",
                    Difference {
                        python: summary(f, p),
                        rust: "-".into(),
                    },
                );
                continue;
            };
            taken[i] = true;
            let h = ours[i];
            paired.insert(h.id.clone(), f.id.clone());
            fingerprints.tally(kind, &f.fingerprint, &h.fingerprint);
            if h.id != f.id {
                put(
                    &f.id,
                    "id",
                    Difference {
                        python: f.id.clone(),
                        rust: h.id.clone(),
                    },
                );
            }
            if h.faces != f.faces {
                put(
                    &f.id,
                    "faces",
                    Difference {
                        python: format!("{:?}", f.faces),
                        rust: format!("{:?}", h.faces),
                    },
                );
            }
            let (mut p, mut q) = (p.clone(), h.parameters.clone());
            // A slot pattern's members are named by their Python pairs.
            if let Some(Value::Array(slots)) = q.get_mut("slots") {
                for s in slots {
                    let id = s.as_str().unwrap_or_default().to_string();
                    *s = paired
                        .get(&id)
                        .cloned()
                        .unwrap_or(format!("rust {id}"))
                        .into();
                }
            }
            if kind == "boss" && other_end(&p, &q) {
                let end = |r: &mut Map<String, Value>| {
                    let at = r.remove("location").unwrap();
                    let axis = r.remove("axis").unwrap();
                    format!("{} along {}", show(&at), show(&axis))
                };
                put(
                    &f.id,
                    "free_end",
                    Difference {
                        python: end(&mut p),
                        rust: end(&mut q),
                    },
                );
            }
            let mut diffs = Vec::new();
            compare_within(
                "parameters",
                Some(&Value::Object(p)),
                Some(&Value::Object(q)),
                &within,
                &mut diffs,
            );
            for (field, d) in diffs {
                put(&f.id, &field, d);
            }
        }
        for (i, h) in ours.iter().enumerate() {
            if !taken[i] {
                put(
                    &format!("rust {}", h.id),
                    "presence",
                    Difference {
                        python: "-".into(),
                        rust: summary(h, &h.parameters),
                    },
                );
            }
        }
    }

    // Oriented slot patterns, which neither reader makes features: Python's derived ones against
    // the native document's records, paired by their members (each named by its Python feature
    // ID, a native member by its pair's).
    let python: Value = serde_json::from_str(&case.json).unwrap();
    let derived = python["quiddity"]["derived"]["oriented_slot_patterns"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let is_member = |m: &Value, f: &Feature| {
        f.kind == "oriented_slot"
            && m.as_object().is_some_and(|m| {
                m.iter()
                    .filter(|(k, _)| *k != "dependents")
                    .all(|(k, v)| f.parameters.get(k) == Some(v))
            })
    };
    let named = |p: &Map<String, Value>, ids: &dyn Fn(&Value) -> String| -> Vec<String> {
        p.get("slots")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .map(ids)
            .collect()
    };
    let python_id = |m: &Value| {
        features
            .iter()
            .find(|f| is_member(m, f))
            .map_or("-".to_string(), |f| f.id.clone())
    };
    let rust_id = |m: &Value| {
        rs.features
            .iter()
            .find(|f| is_member(m, f))
            .map_or("-".to_string(), |f| {
                paired
                    .get(&f.id)
                    .cloned()
                    .unwrap_or(format!("rust {}", f.id))
            })
    };
    let ours: Vec<&quiddity::recognition::Record> = doc
        .records
        .iter()
        .filter(|r| r.family == "oriented_slot_patterns")
        .collect();
    let mut taken = vec![false; ours.len()];
    for (n, p) in derived.iter().enumerate() {
        let p = p.as_object().unwrap();
        let subject = format!("oriented_slot_patterns[{n}]");
        let mine = named(p, &python_id);
        let sorted = |mut v: Vec<String>| {
            v.sort();
            v
        };
        let Some(i) = (0..ours.len()).find(|&i| {
            !taken[i] && sorted(named(&ours[i].parameters, &rust_id)) == sorted(mine.clone())
        }) else {
            put(
                &subject,
                "presence",
                Difference {
                    python: format!("{mine:?}"),
                    rust: "-".into(),
                },
            );
            continue;
        };
        taken[i] = true;
        let q = ours[i];
        let subtype = if p.contains_key("rows") {
            "OrientedSlotGrid"
        } else {
            "OrientedSlotArray"
        };
        if subtype != q.record_type {
            put(
                &subject,
                "record_type",
                Difference {
                    python: subtype.into(),
                    rust: q.record_type.clone(),
                },
            );
        }
        let order = named(&q.parameters, &rust_id);
        if order != mine {
            put(
                &subject,
                "members",
                Difference {
                    python: format!("{mine:?}"),
                    rust: format!("{order:?}"),
                },
            );
        }
        let (mut a, mut b) = (to_part(&frame, p), q.parameters.clone());
        a.remove("slots");
        b.remove("slots");
        let mut diffs = Vec::new();
        compare_within(
            "parameters",
            Some(&Value::Object(a)),
            Some(&Value::Object(b)),
            &|path| if path.ends_with("center") { 1e-3 } else { 0.0 },
            &mut diffs,
        );
        for (field, d) in diffs {
            put(&subject, &field, d);
        }
    }
    for (i, q) in ours.iter().enumerate() {
        if !taken[i] {
            put(
                &format!("rust {}", q.id),
                "presence",
                Difference {
                    python: "-".into(),
                    rust: format!("{} {:?}", q.record_type, named(&q.parameters, &rust_id)),
                },
            );
        }
    }
    out
}

/// How often a native feature's fingerprint agrees with its Python pair's, field by field, by
/// kind: `[agree, compared]`.
#[derive(Default)]
struct Fingerprints(BTreeMap<(String, &'static str), [usize; 2]>);

impl Fingerprints {
    fn tally(&mut self, kind: &str, py: &bridge::Fingerprint, rs: &bridge::Fingerprint) {
        let fields: [(&'static str, bool); 7] = [
            ("family", py.family == rs.family),
            ("subtype", py.subtype == rs.subtype),
            ("sizes", py.sizes == rs.sizes),
            ("axis", py.axis == rs.axis),
            ("position", py.position == rs.position),
            ("pattern", py.pattern == rs.pattern),
            ("neighbourhood", py.neighbourhood == rs.neighbourhood),
        ];
        for (field, same) in fields {
            let n = self.0.entry((kind.to_string(), field)).or_default();
            n[0] += usize::from(same);
            n[1] += 1;
        }
        let n = self.0.entry((kind.to_string(), "whole")).or_default();
        n[0] += usize::from(py == rs);
        n[1] += 1;
    }
}

/// Whether two hole records open at the same point along the same axis.
fn same_opening(p: &Map<String, Value>, q: &Map<String, Value>) -> bool {
    (0..3).all(|k| close(location(p)[k], location(q)[k]))
        && compare_none(p.get("axis"), q.get("axis"))
}

/// Whether two through holes are one bore drilled from its two openings: axes reversed, each
/// location the other's moved by the depth (to its 0.01 rounding) along its axis.
fn other_opening(p: &Map<String, Value>, q: &Map<String, Value>) -> bool {
    let through =
        |r: &Map<String, Value>| r.get("bottom").and_then(Value::as_str) == Some("through");
    let (Some(a), Some(b)) = (
        bridge::holes::point(p.get("axis")),
        bridge::holes::point(q.get("axis")),
    ) else {
        return false;
    };
    let Some(depth) = p.get("depth").and_then(Value::as_f64) else {
        return false;
    };
    through(p)
        && through(q)
        && compare_none(p.get("depth"), q.get("depth"))
        && (0..3).all(|k| close(a[k], -b[k]))
        && {
            // The other end lies on the axis line, the depth along it; quiddity rounds the
            // depth to 0.01, so the ends are that far apart within 0.005.
            let d: [f64; 3] = std::array::from_fn(|k| location(q)[k] - location(p)[k]);
            let along: f64 = (0..3).map(|k| d[k] * a[k]).sum();
            (along - depth).abs() <= 0.005 + 1e-6 * depth
                && (0..3).all(|k| close(location(p)[k] + along * a[k], location(q)[k]))
        }
}

/// Whether two values agree as [`compare`] compares them.
fn compare_none(a: Option<&Value>, b: Option<&Value>) -> bool {
    let mut d = Vec::new();
    compare("", a, b, &mut d);
    d.is_empty()
}

#[test]
fn native_features_match_the_bridge_or_are_listed() {
    let known: Vec<Known> =
        serde_json::from_str(&std::fs::read_to_string(fixture("known_native.json")).unwrap())
            .unwrap();
    let verdicts = [
        "rust-correct",
        "rust-wrong",
        "equivalent",
        "undetermined",
        "not-applicable",
    ];
    let mut listed: BTreeMap<Key, &Known> = BTreeMap::new();
    for k in &known {
        assert!(verdicts.contains(&k.verdict.as_str()), "{k:?}");
        assert!(!k.evidence.trim().is_empty(), "{k:?} has no evidence");
        let key = (
            k.set.clone(),
            k.part.clone(),
            k.subject.clone(),
            k.field.clone(),
        );
        assert!(listed.insert(key, k).is_none(), "listed twice: {k:?}");
    }
    let corpus_found = corpus::dir().is_some();
    let report = std::env::var_os("NATIVE_REPORT").is_some();
    let mut seen: BTreeSet<Key> = BTreeSet::new();
    let mut needed = Vec::new();
    let mut changed = Vec::new();
    let mut tally: BTreeMap<String, usize> = BTreeMap::new();
    let mut compared = 0;
    let mut fingerprints = Fingerprints::default();
    for case in cases() {
        if (case.set, case.part.as_str()) == ("sheets", "ctc04") {
            continue; // not listed yet (see the module docs)
        }
        let (part, a) = model::analyse(&case.step).unwrap();
        let (recognition, features) =
            match bridge::read(&part, &a.binding.source_sha256, &case.json) {
                Ok(read) => read,
                Err(e) => {
                    assert_eq!(
                        (case.set, case.part.as_str()),
                        ("sheets", "ctc04"),
                        "the bridge refuses {}/{}: {e}",
                        case.set,
                        case.part
                    );
                    continue;
                }
            };
        compared += 1;
        for ((subject, field), d) in
            differences(&part, &case, &recognition, &features, &mut fingerprints)
        {
            let key = (case.set.to_string(), case.part.clone(), subject, field);
            if report {
                eprintln!("{key:?}: python {} rust {}", d.python, d.rust);
            }
            match listed.get(&key) {
                Some(k) if k.python == d.python && k.rust == d.rust => {
                    *tally.entry(k.verdict.clone()).or_default() += 1;
                }
                Some(k) => changed.push(format!(
                    "{key:?}: listed python {} rust {}, now python {} rust {}",
                    k.python, k.rust, d.python, d.rust
                )),
                None => needed.push(Known {
                    set: key.0.clone(),
                    part: key.1.clone(),
                    subject: key.2.clone(),
                    field: key.3.clone(),
                    python: d.python,
                    rust: d.rust,
                    verdict: "?".into(),
                    evidence: "?".into(),
                }),
            }
            seen.insert(key);
        }
    }
    let stale: Vec<_> = listed
        .keys()
        .filter(|k| {
            !seen.contains(*k)
                && (corpus_found
                    || ["parts", "slot_patterns", "golden", "pads"].contains(&k.0.as_str()))
        })
        .collect();
    eprintln!("compared {compared} parts; differences by verdict: {tally:?}");
    // Fingerprints are compared, not listed: who owns them is an open question.
    for ((kind, field), [agree, all]) in &fingerprints.0 {
        eprintln!("fingerprints: {kind} {field} agree {agree} of {all}");
    }
    assert!(
        needed.is_empty() && changed.is_empty() && stale.is_empty(),
        "unlisted differences (add to tests/fixtures/known_native.json):\n{}\nchanged: {changed:#?}\nno longer occur: {stale:#?}",
        serde_json::to_string_pretty(&needed).unwrap()
    );
    if corpus_found {
        // 5 fixture parts, 2 slot-pattern, 5 golden and 2 pad fixture parts, 12 corpus parts,
        // the 12 sheets parts the bridge reads, 4 dims parts, 4 steps parts and 1 oriented part.
        assert_eq!(compared, 47);
    }
}

/// The native document of *name* (a fixture part), and the part.
fn document(name: &str) -> (Part, quiddity::recognition::RecognitionDocument) {
    document_at(&format!("parts/{name}.step"))
}

/// The native document of the fixture part at *path*, and the part.
fn document_at(path: &str) -> (Part, quiddity::recognition::RecognitionDocument) {
    let (part, _) = model::analyse(&fixture(path)).unwrap();
    let r = quiddity::correspondence::recognise(&part);
    let doc = quiddity::recognition::document(&r, part.faces.len());
    (part, doc)
}

#[test]
fn plate_holes_and_grid_are_read_in_the_parts_frame() {
    let (part, doc) = document("plate");
    let n = native::read(&part, &doc).unwrap();
    let ids: Vec<_> = n.holes.iter().map(|f| f.id.as_str()).collect();
    assert_eq!(ids, ["f:hole:f6", "f:hole:f7", "f:hole:f8", "f:hole:f9"]);
    for (f, face) in n.holes.iter().zip(6..) {
        assert_eq!((f.kind.as_str(), &f.faces), ("hole", &vec![face]));
        assert_eq!(f.fingerprint.version, bridge::FINGERPRINT_VERSION);
        assert_eq!(f.fingerprint.axis, Some([0.0, 0.0, 1.0]));
        let p = f.fingerprint.pattern.as_ref().unwrap();
        assert_eq!((p.count, p.pitch), (4, 20.0));
    }
    assert_eq!(n.patterns.len(), 1);
    assert_eq!(n.patterns[0].id, "f:hole_pattern:f6");
    assert_eq!(n.patterns[0].arrangement, "grid");
    assert_eq!(n.patterns[0].faces, [6, 7, 8, 9]);
    assert!(
        n.recogniser.starts_with("quiddity-rust "),
        "{}",
        n.recogniser
    );
    assert_eq!(native::recognise(&part).unwrap(), n);
}

#[test]
fn the_reader_refuses_what_it_does_not_know() {
    let (part, doc) = document("plate");
    let refused = |edit: &dyn Fn(&mut quiddity::recognition::RecognitionDocument)| {
        let mut d = doc.clone();
        edit(&mut d);
        native::read(&part, &d).unwrap_err()
    };
    let e = refused(&|d| d.complete = Some(false));
    assert!(e.contains("recognition is incomplete"), "{e}");
    let e = refused(&|d| {
        d.failures.push(quiddity::features::FamilyFailure {
            family: "holes".into(),
            stage: "recognition".into(),
            message: Some("injected failure".into()),
            blocked_by: Vec::new(),
        });
    });
    assert!(e.contains("holes") && e.contains("injected failure"), "{e}");
    let e = refused(&|d| d.schema_version = "quiddity-rust/recognition/1".into());
    assert!(e.contains("\"quiddity-rust/recognition/1\", not"), "{e}");
    // A record measured from its own origin is not in the file's coordinates.
    let e = refused(&|d| {
        d.records[0]
            .parameters
            .insert("origin".into(), serde_json::json!([1.0, 2.0, 3.0]));
    });
    assert!(e.contains("gives origin in the part's own frame"), "{e}");
    let e = refused(&|d| d.coordinate_space = "working".into());
    assert!(e.contains("\"working\" coordinates"), "{e}");
    let e = refused(&|d| d.face_count += 1);
    assert!(e.contains("11 faces and the part 10"), "{e}");
    let e = refused(&|d| d.records[0].family = "warp_drives".into());
    assert!(
        e.contains("family \"warp_drives\" this reader does not know"),
        "{e}"
    );
    let e = refused(&|d| d.records[0].record_type = "WarpDrive".into());
    assert!(e.contains("WarpDrive (holes), a record type"), "{e}");
    // A record type known to another family is not known to this one.
    let e = refused(&|d| d.records[0].record_type = "BoltCircle".into());
    assert!(e.contains("BoltCircle (holes)"), "{e}");
    let e = refused(&|d| d.records[0].faces = vec![10]);
    assert!(e.contains("names face 10"), "{e}");
    let e = refused(&|d| {
        let faces = d.records[1].faces.clone();
        d.records[0].faces = faces;
    });
    assert!(e.contains("cannot be told apart"), "{e}");
    let e = refused(&|d| {
        let last = d.records.len() - 1;
        d.records[last].parameters.remove("holes");
    });
    assert!(e.contains("has no member holes"), "{e}");
}

#[test]
fn turned_part_families_are_read_with_draftwrights_ids() {
    let (part, doc) = document("turned");
    let n = native::read(&part, &doc).unwrap();
    let got: Vec<(&str, &str, &[usize])> = n
        .features
        .iter()
        .map(|f| (f.id.as_str(), f.kind.as_str(), f.faces.as_slice()))
        .collect();
    assert_eq!(
        got,
        [
            ("f:fillet:f1", "fillet", &[1][..]),
            ("f:fillet:f2", "fillet", &[2]),
            ("f:fillet:f5", "fillet", &[5]),
            ("f:fillet:f7", "fillet", &[7]),
            // v0.2.3: whole-body classification co-owns all faces; step records remain.
            (
                "f:turned_body:f0",
                "turned_body",
                &[0, 1, 2, 3, 4, 5, 6, 7, 8]
            ),
            ("f:turned_step:f0", "turned_step", &[0]),
            ("f:turned_step:f6", "turned_step", &[6]),
        ]
    );
    for f in &n.features {
        assert_eq!(f.fingerprint.version, bridge::FINGERPRINT_VERSION);
        // An axis letter and a vector both read as the part's z axis.
        assert_eq!(f.fingerprint.axis, Some([0.0, 0.0, 1.0]), "{}", f.id);
        assert_eq!(f.fingerprint.pattern, None);
    }
    let step = n
        .features
        .iter()
        .find(|f| f.id == "f:turned_step:f6")
        .unwrap();
    assert_eq!(
        (
            step.parameters["lo"].as_f64(),
            step.parameters["hi"].as_f64()
        ),
        (Some(40.0), Some(70.0))
    );
    assert_eq!(
        step.fingerprint.sizes,
        BTreeMap::from([("diameter".into(), 16.0)])
    );
}

#[test]
fn the_reader_refuses_a_record_without_what_draftwright_reads() {
    let (part, doc) = document("turned");
    let fillet = doc
        .records
        .iter()
        .position(|r| r.family == "fillets")
        .unwrap();
    let refused = |edit: &dyn Fn(&mut quiddity::recognition::RecognitionDocument)| {
        let mut d = doc.clone();
        edit(&mut d);
        native::read(&part, &d).unwrap_err()
    };
    let e = refused(&|d| {
        d.records[fillet].parameters.remove("radius");
    });
    assert!(
        e.contains("(Fillet) has no radius, which draftwright reads; the fillets are not read"),
        "{e}"
    );
    let e = refused(&|d| {
        d.records[fillet]
            .parameters
            .insert("at".into(), "here".into());
    });
    assert!(e.contains("(Fillet)'s at is \"here\", not Point"), "{e}");
    let e = refused(&|d| {
        d.records[fillet]
            .parameters
            .insert("axis".into(), "w".into());
    });
    assert!(e.contains("'s axis is \"w\", not Axis"), "{e}");
    let e = refused(&|d| {
        let faces = d.records[fillet + 1].faces.clone();
        d.records[fillet].faces = faces;
    });
    assert!(e.contains("two fillets on faces [2]"), "{e}");
    let e = refused(&|d| d.records[fillet].faces.clear());
    assert!(e.contains("has no ascending faces"), "{e}");
}

/// Nested and shaped fields are checked as the readers use them: a turned profile's axis, origin
/// and body key (when there is a profile), a blend's path, an axis that must be a letter, a
/// record of a family read here as that family's; a field nothing reads is not required; a
/// record's faces must be strictly ascending.
#[test]
fn the_reader_checks_each_familys_fields() {
    let (part, doc) = document("turned");
    let of = |family: &str| doc.records.iter().position(|r| r.family == family).unwrap();
    let (fillet, step) = (of("fillets"), of("turned_steps"));
    let read = |edit: &dyn Fn(&mut quiddity::recognition::RecognitionDocument)| {
        let mut d = doc.clone();
        edit(&mut d);
        native::read(&part, &d)
    };
    let profile = |d: &mut quiddity::recognition::RecognitionDocument| match d.records[step]
        .parameters
        .get_mut("profile")
    {
        Some(Value::Object(p)) => p.clone(),
        p => panic!("{p:?}"),
    };
    let e = read(&|d| {
        let mut p = profile(d);
        p.remove("axis_origin");
        d.records[step]
            .parameters
            .insert("profile".into(), p.into());
    })
    .unwrap_err();
    assert!(
        e.contains("(TurnedStep) has no profile.axis_origin, which draftwright reads"),
        "{e}"
    );
    let e = read(&|d| {
        let mut p = profile(d);
        p.insert("body_key".into(), "b".into());
        d.records[step]
            .parameters
            .insert("profile".into(), p.into());
    })
    .unwrap_err();
    assert!(e.contains("'s profile.body_key is \"b\", not Key"), "{e}");
    // A direction where draftwright reads an axis letter is no file axis: the step is left out.
    let n = read(&|d| {
        let mut p = profile(d);
        p.insert("axis".into(), Value::from(vec![0.6, 0.8, 0.0]));
        d.records[step]
            .parameters
            .insert("profile".into(), p.into());
    })
    .unwrap();
    assert!(
        !n.features
            .iter()
            .any(|f| f.faces == doc.records[step].faces)
    );
    // No profile: nothing in it to check.
    read(&|d| {
        d.records[step]
            .parameters
            .insert("profile".into(), Value::Null);
    })
    .unwrap();
    // quiddity's reconciliation leaves this part no blend (each one is a fillet's), so a
    // fillet's record is made a blend's, with a circular path that lacks its radius.
    let e = read(&|d| {
        let r = &mut d.records[fillet];
        r.family = "blends".into();
        r.record_type = "Blend".into();
        let path = serde_json::json!({"center": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0]});
        r.parameters.insert("path".into(), path);
    })
    .unwrap_err();
    assert!(e.contains("(Blend)'s path is"), "{e}");
    assert!(e.contains("not BlendPath; the blends are not read"), "{e}");
    // A fillet's side is read nowhere, so it is not required.
    read(&|d| {
        d.records[fillet].parameters.remove("side");
    })
    .unwrap();
    // A record of a family read here is read as that family's: a fillet's record is no groove.
    let e = read(&|d| {
        d.records[fillet].family = "grooves".into();
        d.records[fillet].record_type = "Groove".into();
    })
    .unwrap_err();
    assert!(e.contains("(Groove) has no width"), "{e}");
    // A groove's axis must be a letter (lint/turned.rs reads it so): a direction is no file axis,
    // so the groove is left out.
    let n = read(&|d| {
        let r = &mut d.records[fillet];
        r.family = "grooves".into();
        r.record_type = "Groove".into();
        for (k, v) in [("width", 2.0), ("diameter", 9.0)] {
            r.parameters.insert(k.into(), v.into());
        }
        r.parameters.insert("profile".into(), Value::Null);
        r.parameters
            .insert("axis".into(), Value::from(vec![0.6, 0.8, 0.0]));
    })
    .unwrap();
    assert!(!n.features.iter().any(|f| f.kind == "groove"));
    // #233's families are checked as the others are: a fillet's record is no V channel.
    let e = read(&|d| {
        d.records[fillet].family = "v_channels".into();
        d.records[fillet].record_type = "VChannel".into();
    })
    .unwrap_err();
    assert!(e.contains("(VChannel)"), "{e}");
    assert!(e.contains("the v_channels are not read"), "{e}");
    // Faces must be strictly ascending: a face named twice is refused, for every family.
    let e = read(&|d| d.records[fillet].faces = vec![1, 1]).unwrap_err();
    assert!(e.contains("has no ascending faces"), "{e}");
    let (part, mut d) = document("plate");
    let h = d.records.iter().position(|r| r.family == "holes").unwrap();
    let f = d.records[h].faces[0];
    d.records[h].faces = vec![f, f];
    let e = native::read(&part, &d).unwrap_err();
    assert!(e.contains("has no ascending faces"), "{e}");
}

/// quiddity-rust's golden oriented slots: three `oriented_slot` features ranked by faces, and
/// their pattern checked but not made a feature (the bridge reads none of Python's); a pattern
/// whose member is no oriented slot, or one that names an oriented slot twice, or that lacks what
/// draftwright reads of its members, is refused.
#[test]
fn oriented_slots_are_read_and_their_pattern_checked() {
    let (part, doc) = document_at("recognition/golden/oriented_slots.step");
    let n = native::read(&part, &doc).unwrap();
    let ids: Vec<&str> = n
        .features
        .iter()
        .filter(|f| f.kind == "oriented_slot")
        .map(|f| f.id.as_str())
        .collect();
    assert_eq!(
        ids,
        [
            "f:oriented_slot:f6",
            "f:oriented_slot:f10",
            "f:oriented_slot:f14"
        ]
    );
    assert!(n.features.iter().all(|f| f.kind != "oriented_slot_pattern"));
    for f in n.features.iter().filter(|f| f.kind == "oriented_slot") {
        assert!(f.fingerprint.sizes.contains_key("width"), "{}", f.id);
        assert!(f.fingerprint.sizes.contains_key("length"), "{}", f.id);
        assert_eq!(f.fingerprint.pattern, None, "{}", f.id);
    }
    let pattern = doc
        .records
        .iter()
        .position(|r| r.family == "slot_patterns")
        .unwrap();
    let refused = |edit: &dyn Fn(&mut quiddity::recognition::RecognitionDocument)| {
        let mut d = doc.clone();
        edit(&mut d);
        native::read(&part, &d).unwrap_err()
    };
    let members = |d: &mut quiddity::recognition::RecognitionDocument| -> Vec<Value> {
        d.records[pattern].parameters["slots"]
            .as_array()
            .unwrap()
            .clone()
    };
    let e = refused(&|d| {
        let mut m = members(d);
        m[1] = m[0].clone();
        d.records[pattern]
            .parameters
            .insert("slots".into(), m.into());
    });
    assert!(
        e.contains("one oriented slot in an oriented slot pattern twice"),
        "{e}"
    );
    let e = refused(&|d| {
        let mut m = members(d);
        m[0]["width"] = Value::from(99.0);
        d.records[pattern]
            .parameters
            .insert("slots".into(), m.into());
    });
    assert!(e.contains("matches 0 oriented slots, not one"), "{e}");
    let e = refused(&|d| {
        let mut m = members(d);
        m[0].as_object_mut().unwrap().remove("w_center");
        d.records[pattern]
            .parameters
            .insert("slots".into(), m.into());
    });
    assert!(
        e.contains("(OrientedSlot) has no center, which draftwright reads"),
        "{e}"
    );
    let slot = doc
        .records
        .iter()
        .position(|r| r.family == "slots")
        .unwrap();
    // quiddity 0.2.0 gives the slots as `slots` measured from an origin along their own axes
    // (#92); from 0.2.1, in the plate's file axes (#91), each claims all four walls and carries
    // a `source`. One without its depth extent still cannot be placed.
    assert!(!doc.records[slot].parameters["source"].is_null());
    let e = refused(&|d| {
        d.records[slot].parameters.remove("d_lo");
    });
    assert!(
        e.contains("(OrientedSlot) has no center, which draftwright reads"),
        "{e}"
    );
}

/// The reader's schema is the provider's: its version and every family and record type.
#[test]
fn the_reader_knows_the_providers_schema() {
    assert_eq!(
        native::SCHEMA_VERSION,
        quiddity::recognition::SCHEMA_VERSION
    );
    // quiddity 0.2.0 merged the oriented families into their plain ones (#92); the reader
    // restores them from the records' own fields (`native::in_file_axes`).
    let derived = [
        "oriented_chamfers",
        "oriented_slots",
        "oriented_slot_patterns",
    ];
    let read: Vec<_> = native::FAMILIES
        .iter()
        .filter(|(f, _)| !derived.contains(f))
        .copied()
        .collect();
    assert_eq!(read, quiddity::recognition::RECORD_TYPES);
}

/// quiddity and haecceity come from one commit of quiddity-rust: their `source` lines in
/// Cargo.lock agree.
#[test]
fn quiddity_and_haecceity_are_pinned_together() {
    let lock =
        std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.lock")).unwrap();
    let field = |name: &str, key: &str| {
        let block = lock
            .split("[[package]]")
            .find(|b| b.contains(&format!("name = \"{name}\"\n")))
            .unwrap_or_else(|| panic!("{name} is not in Cargo.lock"));
        block
            .lines()
            .find_map(|l| l.strip_prefix(key))
            .unwrap()
            .to_string()
    };
    let pin = |name: &str| (field(name, "source = "), field(name, "version = "));
    // Either one git commit for both, or one crates.io release (published from one tag).
    let (source, _) = pin("haecceity");
    assert!(
        source.contains('#')
            || source == "\"registry+https://github.com/rust-lang/crates.io-index\"",
        "{source}"
    );
    assert_eq!(pin("quiddity"), pin("haecceity"));
}

/// Python quiddity 0.3.9 reports 12 countersinks on ctc04 (its recognition JSON, faces through
/// `caller_index`). They are the quarter-cone corner faces of the 5×45° pocket-rim chamfer, seated
/// only on the pockets' R5 corner walls, not countersinks (quiddity-rust #76): since 0.1.1 a
/// countersink's cone must run at least 270° round its axis. Native recognition reports none of
/// them, and no countersink at all on ctc04.
#[test]
fn ctc04_has_none_of_pythons_chamfer_corner_countersinks() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let json: Value =
        serde_json::from_str(&gunzip(&fixture("recognition/sheets/ctc04.json.gz")).unwrap())
            .unwrap();
    let python = json["quiddity"]["features"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|r| r["family"] == "countersinks")
        .count();
    assert_eq!(python, 12);
    let (part, _) = model::analyse(&dir.join(facts::load("ctc04").binding.file)).unwrap();
    let rs = native::recognise(&part).unwrap();
    let ours: Vec<&Vec<usize>> = rs
        .features
        .iter()
        .filter(|f| f.kind == "countersink")
        .map(|f| &f.faces)
        .collect();
    assert!(ours.is_empty(), "{ours:?}");
}

/// A slot pattern names its member slots by their IDs, in quiddity's order, and gives them and
/// itself their count and pitch, as the bridge does; a member that is not exactly one slot's
/// record, or a record without what draftwright reads of it, is refused.
#[test]
fn slot_patterns_name_their_member_slots() {
    let (part, doc) = document_at("recognition/slot_patterns/slot_array.step");
    let n = native::read(&part, &doc).unwrap();
    let slots: Vec<&Feature> = n.features.iter().filter(|f| f.kind == "slot").collect();
    let patterns: Vec<&Feature> = n
        .features
        .iter()
        .filter(|f| f.kind == "slot_pattern")
        .collect();
    assert_eq!((slots.len(), patterns.len()), (3, 1));
    let p = patterns[0];
    assert_eq!(p.id, "f:slot_pattern:f6");
    assert_eq!(p.fingerprint.subtype, "SlotArray");
    // quiddity 0.1.0 orders the members in the part's own frame, which runs the other way.
    assert_eq!(
        p.parameters["slots"],
        serde_json::json!(["f:slot:f14", "f:slot:f10", "f:slot:f6"])
    );
    for f in slots.iter().copied().chain([p]) {
        let pattern = f.fingerprint.pattern.as_ref().unwrap();
        assert_eq!((pattern.count, pattern.pitch), (3, 25.0), "{}", f.id);
    }
    let mut faces: Vec<usize> = slots.iter().flat_map(|f| f.faces.clone()).collect();
    faces.sort_unstable();
    assert_eq!(p.faces, faces);

    let of = |family: &str| doc.records.iter().position(|r| r.family == family).unwrap();
    let (slot, pattern) = (of("slots"), of("slot_patterns"));
    let refused = |edit: &dyn Fn(&mut quiddity::recognition::RecognitionDocument)| {
        let mut d = doc.clone();
        edit(&mut d);
        native::read(&part, &d).unwrap_err()
    };
    let e = refused(&|d| {
        d.records[slot].parameters.remove("w_center");
    });
    assert!(
        e.contains("(Slot) has no w_center, which draftwright reads; the slots are not read"),
        "{e}"
    );
    let member = |d: &mut quiddity::recognition::RecognitionDocument| match d.records[pattern]
        .parameters
        .get_mut("slots")
    {
        Some(Value::Array(a)) => a[0].as_object_mut().unwrap().clone(),
        s => panic!("{s:?}"),
    };
    // A member's record is checked as a slot's.
    let e = refused(&|d| {
        let mut m = member(d);
        m.remove("width");
        d.records[pattern].parameters["slots"][0] = m.into();
    });
    assert!(
        e.contains("(SlotArray)'s slots[0] (Slot) has no width, which draftwright reads"),
        "{e}"
    );
    // A member that is no slot's record, or two slots', is refused.
    let e = refused(&|d| {
        let mut m = member(d);
        m.insert("lo".into(), Value::from(-11.0));
        d.records[pattern].parameters["slots"][0] = m.into();
    });
    assert!(
        e.contains("f:slot_pattern:f6's member matches 0 slots"),
        "{e}"
    );
    let e = refused(&|d| {
        let m = member(d);
        d.records[pattern].parameters["slots"][1] = m.into();
    });
    assert!(e.contains("puts one slot in a slot pattern twice"), "{e}");
    let e = refused(&|d| {
        d.records[pattern].parameters["slots"] = Value::Array(Vec::new());
    });
    assert!(
        e.contains("(SlotArray)'s slots is [], not Records(\"Slot\")"),
        "{e}"
    );

    let (part, doc) = document_at("recognition/slot_patterns/slot_grid.step");
    let grid = doc
        .records
        .iter()
        .position(|r| r.record_type == "SlotGrid")
        .unwrap();
    let mut d = doc.clone();
    d.records[grid].parameters["rows"] = Value::from(3.0);
    let e = native::read(&part, &d).unwrap_err();
    assert!(e.contains("(SlotGrid)'s rows is 3.0, not Count"), "{e}");
    let n = native::read(&part, &doc).unwrap();
    let p = n
        .features
        .iter()
        .find(|f| f.kind == "slot_pattern")
        .unwrap();
    assert_eq!(p.parameters["slots"].as_array().map(Vec::len), Some(6));
}

/// The double-D bore, gusset rib, plate and polygonal stock records are checked against what
/// lint reads of them: a missing or mis-shaped field refuses the read, in a pattern's member ribs
/// too.
#[test]
fn bore_rib_plate_and_stock_fields_are_checked() {
    let refused = |path: &str, family: &str, edit: &dyn Fn(&mut Map<String, Value>)| {
        let (part, mut d) = document_at(path);
        let r = d
            .records
            .iter()
            .position(|r| r.family == family)
            .unwrap_or_else(|| panic!("{path} has no {family}"));
        native::read(&part, &d).unwrap();
        edit(&mut d.records[r].parameters);
        native::read(&part, &d).unwrap_err()
    };
    let e = refused(
        "recognition/golden/double_d_bore.step",
        "double_d_bores",
        &|p| {
            p.remove("through");
        },
    );
    assert!(e.contains("(DoubleDBore) has no through"), "{e}");
    let e = refused(
        "recognition/golden/polygonal_stock.step",
        "polygonal_stock",
        &|p| {
            p.insert("side_count".into(), Value::from(6.5));
        },
    );
    assert!(
        e.contains("(PolygonalStock)'s side_count is 6.5, not Count"),
        "{e}"
    );
    let e = refused("recognition/golden/gusset_ribs.step", "plates", &|p| {
        p.remove("u");
    });
    assert!(e.contains("(Plate) has no u"), "{e}");
    let e = refused("recognition/golden/gusset_ribs.step", "gusset_ribs", &|p| {
        p.insert("supports".into(), serde_json::json!([["y", 17.0]]));
    });
    assert!(
        e.contains("(GussetRib)'s supports is [[\"y\",17.0]], not Planes(2)"),
        "{e}"
    );
    let path = "recognition/golden/gusset_rib_patterns.step";
    let e = refused(path, "gusset_rib_patterns", &|p| {
        p.insert("mirror_plane".into(), serde_json::json!(["w", 0.0]));
    });
    assert!(e.contains("(GussetRibMirrorPair)'s mirror_plane is"), "{e}");
    let e = refused(path, "gusset_rib_patterns", &|p| {
        p["ribs"][1].as_object_mut().unwrap().remove("legs");
    });
    assert!(
        e.contains("(GussetRibMirrorPair)'s ribs[1] (GussetRib) has no legs"),
        "{e}"
    );
}

/// A raised pad's record is checked against what lint and dims read of it (its axis letter and
/// box): a missing or mis-shaped field refuses the read; its `direction`, which nothing reads, is
/// not required.
#[test]
fn pad_fields_are_checked() {
    let path = "recognition/steps/plates_pads_levels_and_slanted_steps.step";
    let (part, doc) = document_at(path);
    let r = doc.records.iter().position(|r| r.family == "pads").unwrap();
    let n = native::read(&part, &doc).unwrap();
    let pads: Vec<_> = n.features.iter().filter(|f| f.kind == "pad").collect();
    assert_eq!(pads.len(), 1);
    assert_eq!(pads[0].id, "f:pad:f16");
    assert_eq!(pads[0].faces, doc.records[r].faces);
    let edited = |edit: &dyn Fn(&mut Map<String, Value>)| {
        let mut d = doc.clone();
        edit(&mut d.records[r].parameters);
        native::read(&part, &d)
    };
    let e = edited(&|p| {
        p.remove("z1");
    })
    .unwrap_err();
    assert!(
        e.contains("(RaisedPad) has no z1, which draftwright reads"),
        "{e}"
    );
    // A direction where draftwright reads an axis letter is no file axis: the pad is left out.
    let n = edited(&|p| {
        p.insert("axis".into(), serde_json::json!([0.6, 0.8, 0.0]));
    })
    .unwrap();
    assert!(!n.features.iter().any(|f| f.kind == "pad"));
    let e = edited(&|p| {
        p.insert("axis".into(), serde_json::json!("w"));
    })
    .unwrap_err();
    assert!(e.contains("(RaisedPad)'s axis is \"w\", not Letter"), "{e}");
    let e = edited(&|p| {
        p.insert("x0".into(), Value::from("5.623"));
    })
    .unwrap_err();
    assert!(
        e.contains("(RaisedPad)'s x0 is \"5.623\", not Number"),
        "{e}"
    );
    edited(&|p| {
        p.remove("direction");
    })
    .unwrap();
}

/// The pad lint asks the same of a natively read pad as of the bridge's (Python quiddity 0.3.9's
/// record, in its working frame): the same parameters, and values within the 1e-3 quiddity rounds
/// a pad's box to (Python rounds it in its working frame, so the golden part's x location is
/// 90.000068 through the bridge and 90 natively), on the two fixture parts with pads.
#[test]
fn native_pads_lint_as_the_bridges_do() {
    let wanted = |a: &model::Analysis| -> Vec<(String, String, Vec<f64>)> {
        draftwright::lint::pads::wanted(a)
            .into_iter()
            .map(|w| (w.feature.id.clone(), w.parameter, w.values.unwrap()))
            .collect()
    };
    for (dir, name, pads) in [
        ("steps", "plates_pads_levels_and_slanted_steps", 1),
        ("issues", "whistle_frame_reference", 3),
    ] {
        let step = plain_step(&format!("recognition/{dir}/{name}"));
        let json = gunzip(&fixture(&format!("recognition/{dir}/{name}.json.gz"))).unwrap();
        let (part, mut native) = model::analyse(&step).unwrap();
        let mut bridged = native.clone();
        model::recognise_native(&part, &mut native).unwrap();
        model::recognise(&part, &mut bridged, &json).unwrap();
        assert!(
            native.unrecognised.iter().all(|u| u.family != "pads"),
            "{name}"
        );
        let (ours, theirs) = (wanted(&native), wanted(&bridged));
        assert_eq!(ours.len(), 5 * pads, "{name}: {ours:?}");
        assert_eq!(ours.len(), theirs.len(), "{name}: {ours:?} {theirs:?}");
        for (a, b) in ours.iter().zip(&theirs) {
            let near = a.2.len() == b.2.len()
                && a.2
                    .iter()
                    .zip(&b.2)
                    .all(|(x, y)| (x - y).abs() <= 1e-3 + 1e-9);
            assert!(a.0 == b.0 && a.1 == b.1 && near, "{name}: {a:?} {b:?}");
        }
    }
}

/// The corpus parts on which Rust quiddity finds raised pads (every corpus part read natively,
/// 2026-10-09: these four), each as Python quiddity 0.4.0.dev0's document gives it (its defining
/// faces' file indices, and its record mapped from the working frame into the file's): the same
/// faces, axis, sense and box. No compared recognition has these parts.
#[test]
fn corpus_pads_are_pythons() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    type Pad = (&'static [usize], &'static str, i64, [f64; 6]);
    let cases: [(&str, &[Pad]); 4] = [
        (
            "mfcadpp_holdout/205.step",
            &[(
                &[1, 3, 7, 8, 9],
                "y",
                1,
                [43.259, 48.938, 24.485, 41.262, 7.769, 16.518],
            )],
        ),
        (
            "mfcadpp_holdout/238.step",
            &[(
                &[3, 5, 7, 8, 9],
                "z",
                -1,
                [21.588, 24.449, 0.0, 3.633, 0.0, 13.808],
            )],
        ),
        (
            "mfcadpp/10092.step",
            &[
                (
                    &[0, 3, 7, 9, 12],
                    "y",
                    1,
                    [0.0, 4.274, 10.285, 16.681, 36.317, 39.721],
                ),
                (
                    &[3, 14, 15, 16, 25],
                    "y",
                    1,
                    [0.0, 2.828, 12.327, 16.681, 0.0, 20.258],
                ),
            ],
        ),
        (
            "mfcadpp/10255.step",
            &[(
                &[0, 1, 2, 3, 20],
                "y",
                -1,
                [0.0, 3.592, 0.0, 4.785, 7.951, 12.438],
            )],
        ),
    ];
    for (file, expected) in cases {
        let (part, _) = model::analyse(&dir.join(file)).unwrap();
        let n = native::recognise(&part).unwrap();
        let pads: Vec<&Feature> = n.features.iter().filter(|f| f.kind == "pad").collect();
        assert_eq!(pads.len(), expected.len(), "{file}");
        for (f, (faces, axis, direction, b)) in pads.iter().zip(expected) {
            let p = &f.parameters;
            assert_eq!(f.faces, *faces, "{file}");
            assert_eq!(p["axis"], *axis, "{file} {}", f.id);
            assert_eq!(p["direction"].as_i64(), Some(*direction), "{file} {}", f.id);
            for (k, v) in ["x0", "x1", "y0", "y1", "z0", "z1"].iter().zip(b) {
                // Python rounds the box in its working frame, Rust in the file's.
                let got = p[*k].as_f64().unwrap();
                assert!(
                    (got - v).abs() <= 1e-3 + 1e-9,
                    "{file} {} {k}: {got} {v}",
                    f.id
                );
            }
        }
    }
}

/// Both minimum-version documents and the new measured-thread contract are read losslessly.
#[test]
fn modelled_threads_and_refusals_survive_recognition_and_analysis_json() {
    let (part, mut doc) = document("turned");
    assert_eq!(doc.schema_version, native::SCHEMA_VERSION);
    let parameters = serde_json::json!({
        "axis": {"point": [0, 0, 0], "direction": [0, 0, 1]},
        "side": "external", "major_diameter": 20, "minor_diameter": 19,
        "pitch": 1, "hand": "left", "starts": 2, "start": 2, "end": 10,
        "length": 8, "tolerances": {"diameter": 0.04, "pitch": 0.005, "axial": 0.01},
        "flank_faces": [0], "crest_faces": [1], "root_faces": [], "runout_faces": []
    });
    let mut thread = doc.records[0].clone();
    thread.id = "modelled_threads/0".into();
    thread.family = "modelled_threads".into();
    thread.record_type = "ModelledThread".into();
    thread.faces = vec![0, 1];
    thread.parameters = parameters.as_object().unwrap().clone();
    doc.records.push(thread);
    let mut refusal = doc.records.last().unwrap().clone();
    refusal.id = "modelled_thread_refusals/0".into();
    refusal.family = "modelled_thread_refusals".into();
    refusal.record_type = "ModelledThreadRefusal".into();
    refusal.parameters = serde_json::json!({
        "axis": {"point": [0, 0, 0], "direction": [0, 0, 1]},
        "faces": [0, 1], "reason": "inconsistent_pitch"
    })
    .as_object()
    .unwrap()
    .clone();
    doc.records.push(refusal);
    let json = serde_json::to_string(&doc).unwrap();
    let doc = serde_json::from_str(&json).unwrap();
    let mut analysis = model::analyse(&fixture("parts/turned.step")).unwrap().1;
    model::recognise_document(&part, &mut analysis, &doc).unwrap();
    let analysis: model::Analysis =
        serde_json::from_str(&serde_json::to_string(&analysis).unwrap()).unwrap();
    let thread = analysis
        .features
        .iter()
        .find(|f| f.kind == "modelled_thread")
        .unwrap();
    assert_eq!(thread.parameters, parameters.as_object().unwrap().clone());
    assert_eq!(thread.faces, [0, 1]);
    assert_eq!(thread.fingerprint.axis, Some([0.0, 0.0, 1.0]));
    for (name, size) in [
        ("major_diameter", 20.0),
        ("minor_diameter", 19.0),
        ("pitch", 1.0),
        ("starts", 2.0),
    ] {
        assert_eq!(thread.fingerprint.sizes[name], size);
    }
    assert!(
        analysis
            .unrecognised
            .iter()
            .all(|u| !u.family.starts_with("modelled_thread"))
    );
    assert!(
        analysis
            .recognition
            .as_ref()
            .unwrap()
            .refusals
            .iter()
            .any(|r| r.subtype == "ModelledThreadRefusal"
                && r.parameters["reason"] == "inconsistent_pitch")
    );
    let plan = model::plan_recovered(&analysis, &model::Spec::default()).unwrap();
    // The occurrence now has ISO 6410 depiction and a dimensional owner.
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "modelled_thread_not_depicted" && p.subjects.contains(&thread.id))
    );
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.id == format!("d:{}/thread", thread.id) && d.label.contains("M20×Ph2P1-LH"))
    );
    let reply = draftwright::api::call("plan", &serde_json::json!({"analysis": analysis})).unwrap();
    assert_eq!(reply, serde_json::to_value(&plan).unwrap());
    assert!(
        !plan
            .feature_dispositions
            .iter()
            .any(|d| d.subject == thread.id)
    );

    let mut bad = doc.clone();
    bad.schema_version = "quiddity-rust/recognition/5".into();
    assert!(
        native::read(&part, &bad)
            .unwrap_err()
            .contains("recognition/5\", not")
    );
    let index = doc
        .records
        .iter()
        .position(|r| r.family == "modelled_threads")
        .unwrap();
    for field in [
        "axis",
        "side",
        "major_diameter",
        "minor_diameter",
        "pitch",
        "hand",
        "starts",
        "start",
        "end",
        "length",
        "tolerances",
        "flank_faces",
        "crest_faces",
        "root_faces",
        "runout_faces",
    ] {
        let mut bad = doc.clone();
        bad.records[index].parameters.remove(field);
        assert!(
            native::read(&part, &bad).unwrap_err().contains(field),
            "{field}"
        );
    }
    let mut bad = doc.clone();
    bad.records[index].parameters["root_faces"] = serde_json::json!([0]);
    assert!(
        native::read(&part, &bad)
            .unwrap_err()
            .contains("face claims")
    );
    // A thread quiddity measures from its own origin is left out, not misread.
    let mut bad = doc.clone();
    bad.records[index]
        .parameters
        .insert("origin".into(), serde_json::json!([1.0, 2.0, 3.0]));
    assert!(
        !native::read(&part, &bad)
            .unwrap()
            .features
            .iter()
            .any(|f| f.faces == doc.records[index].faces)
    );
    assert!(native::left_in_frame(&bad.records[index]));
}

/// A thread refusal's faces are no hole (#218): the hole on them, and its pattern, are left out,
/// the others stay, and the refusal is reported against those faces.
#[test]
fn no_hole_is_built_on_faces_a_thread_refusal_names() {
    let (part, mut doc) = document("plate");
    let mut refusal = doc.records[0].clone();
    refusal.id = "modelled_thread_refusals/0".into();
    refusal.family = "modelled_thread_refusals".into();
    refusal.record_type = "ModelledThreadRefusal".into();
    refusal.faces = vec![6];
    refusal.parameters = serde_json::json!({
        "axis": {"point": [0, 0, 0], "direction": [0, 0, 1]},
        "faces": [6], "reason": "inconsistent_diameters"
    })
    .as_object()
    .unwrap()
    .clone();
    doc.records.push(refusal);
    let n = native::read(&part, &doc).unwrap();
    let faces: Vec<_> = n.holes.iter().map(|f| f.faces.clone()).collect();
    assert_eq!(faces, [vec![7], vec![8], vec![9]]);
    assert!(n.patterns.is_empty());

    let mut analysis = model::analyse(&fixture("parts/plate.step")).unwrap().1;
    model::recognise_document(&part, &mut analysis, &doc).unwrap();
    assert!(
        analysis
            .features
            .iter()
            .all(|f| !f.faces.contains(&6) || f.kind != "hole")
    );
    let plan = model::plan_recovered(&analysis, &model::Spec::default()).unwrap();
    let refused: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "modelled_thread_refused")
        .collect();
    assert_eq!(refused.len(), 1);
    assert_eq!(refused[0].severity, "warning");
    assert!(
        refused[0]
            .refs
            .contains(&"recognition:refusal:0".to_string())
    );
    assert!(refused[0].message.contains("inconsistent diameters"));

    // Declaring the feature on those faces, as the warning asks, settles it.
    let face = draftwright::recognition_corrections::face_fingerprint(&analysis, 6).unwrap();
    let spec: model::Spec = serde_json::from_value(serde_json::json!({
        "schema": model::Spec::default().schema,
        "feature_declarations": [{"id": "f:declared:thread", "kind": "hole", "faces": [face]}],
    }))
    .unwrap();
    let plan = model::plan_recovered(&analysis, &spec).unwrap();
    assert!(
        plan.problems
            .iter()
            .all(|p| p.code != "modelled_thread_refused")
    );
}

/// A hole at the major diameter of the metric size a part is named for is reported as a possible
/// thread, and its callout stays measured; an authored thread resolves it (#218).
#[test]
fn a_smooth_hole_at_a_named_metric_size_is_a_possible_thread() {
    let (part, doc) = document("plate");
    let mut analysis = model::analyse(&fixture("parts/plate.step")).unwrap().1;
    model::recognise_document(&part, &mut analysis, &doc).unwrap();
    let possible = |spec: serde_json::Value| -> Vec<String> {
        let spec: model::Spec = serde_json::from_value(spec).unwrap();
        let plan = model::plan_recovered(&analysis, &spec).unwrap();
        plan.problems
            .iter()
            .filter(|p| p.code == "possible_thread")
            .flat_map(|p| p.subjects.clone())
            .collect()
    };
    let schema = model::Spec::default().schema;
    let titled =
        |title: &str| serde_json::json!({"schema": schema, "sheet": {"title": {"title": title}}});
    assert_eq!(
        possible(titled("M6x20 mounting plate")),
        ["f:hole:f6", "f:hole:f7", "f:hole:f8", "f:hole:f9"]
    );
    // No named size, another size, or a word that only starts with M.
    for title in ["Mounting plate", "M5 plate", "Model 6 plate", "6 mm plate"] {
        assert!(possible(titled(title)).is_empty(), "{title}");
    }
    // Info only: a plain bore at that size is as likely, and the pattern owns the thread.
    let plan = model::plan_recovered(
        &analysis,
        &serde_json::from_value(titled("M6 plate")).unwrap(),
    )
    .unwrap();
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "possible_thread")
        .unwrap();
    assert_eq!(p.severity, "info");
    assert!(p.message.contains("features/f:hole_pattern:f6/thread"));
    let mut spec = titled("M6 plate");
    spec["features"] = serde_json::json!({"f:hole:f6": {"thread": {"designation": "M6"}}});
    assert_eq!(possible(spec), ["f:hole:f7", "f:hole:f8", "f:hole:f9"]);
}

/// recognition/6 (quiddity #92) gives a record whose frame is not the file's an `origin` and its
/// coordinates as distances from it along named measuring axes. A plate or turned step so
/// given, along file axes, reads exactly as the same record in absolute file coordinates.
#[test]
fn a_record_measured_from_its_origin_reads_as_in_file_coordinates() {
    let origin = [3.25, -5.5, 7.125];
    let mut converted = 0;
    for name in ["plate", "turned"] {
        let (part, doc) = document(name);
        let mut moved = doc.clone();
        for r in &mut moved.records {
            if !matches!(r.family.as_str(), "plates" | "turned_steps") {
                continue;
            }
            let p = &mut r.parameters;
            let Some(w) = ["x", "y", "z"].iter().position(|l| p["axis"] == *l) else {
                continue;
            };
            for k in ["lo", "hi"] {
                p[k] = serde_json::json!(p[k].as_f64().unwrap() - origin[w]);
            }
            if r.family == "plates" {
                // Across the axis, descending, to check `u`/`v` go back by file axis.
                let [a, b] = match w {
                    0 => [2, 1],
                    1 => [2, 0],
                    _ => [1, 0],
                };
                let (u, v) = (p["u"].as_f64().unwrap(), p["v"].as_f64().unwrap());
                p.insert("u".into(), serde_json::json!(v - origin[a]));
                p.insert("v".into(), serde_json::json!(u - origin[b]));
                let letters = [["x", "y", "z"][a], ["x", "y", "z"][b]];
                p.insert("section_axes".into(), serde_json::json!(letters));
            }
            p.insert("origin".into(), serde_json::json!(origin));
            assert!(!native::left_in_frame(r), "{}", r.id);
            converted += 1;
        }
        assert_eq!(
            native::read(&part, &moved).unwrap(),
            native::read(&part, &doc).unwrap(),
            "{name}"
        );
    }
    assert!(converted >= 2, "{converted}");
}

/// threaded_connector_109 is turned about file z in a frame turned about it, so quiddity 0.2.0
/// gives its turned steps and flat from that frame's origin: all are read, the flat's stock axis
/// on z, and its turned profiles keyed by their own boxes.
#[test]
fn threaded_connector_109s_framed_records_are_read_in_file_coordinates() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (part, _) = model::analyse(&dir.join("cadgenbench/threaded_connector_109.step")).unwrap();
    let r = quiddity::correspondence::recognise(&part);
    let doc = quiddity::recognition::document(&r, part.faces.len());
    let framed: Vec<_> = doc
        .records
        .iter()
        .filter(|r| r.parameters.contains_key("origin"))
        .collect();
    assert!(
        framed.iter().any(|r| r.family == "turned_steps")
            && framed.iter().any(|r| r.family == "flats"),
        "{:?}",
        framed.iter().map(|r| &r.id).collect::<Vec<_>>()
    );
    assert!(framed.iter().all(|r| !native::left_in_frame(r)));
    let n = native::read(&part, &doc).unwrap();
    let flat = n.features.iter().find(|f| f.kind == "flat").unwrap();
    assert_eq!(flat.parameters["axis_line"], serde_json::json!([0.0, 0.0]));
    let turned: Vec<_> = n
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .collect();
    assert_eq!(
        turned.len(),
        framed.iter().filter(|r| r.family == "turned_steps").count()
    );
    for f in turned {
        let p = &f.parameters;
        assert!(!p.contains_key("origin"), "{}", f.id);
        let (lo, hi) = (p["lo"].as_f64().unwrap(), p["hi"].as_f64().unwrap());
        assert!(
            (0.0..=420.0).contains(&lo) && lo < hi && hi <= 420.0,
            "{} {lo} {hi}",
            f.id
        );
        assert_eq!(
            p["profile"]["axis_origin"],
            serde_json::json!([0.0, 0.0, 0.0])
        );
    }
}
