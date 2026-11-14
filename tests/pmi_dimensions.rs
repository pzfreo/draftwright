//! The part's AP242 dimensions on the sheet (`draftwright::dims`' PMI dimensions, stage 2b)
//! against draftwright's, record by record, and their fail-closed paths, spec interplay, ink and
//! determinism.
//!
//! draftwright's outcome is in `tests/fixtures/pmi/dimensions/<stem>.json.gz`
//! (`tools/capture_pmi_dimensions.py`): for each dimension record of each AP242 file of
//! `tests/fixtures/pmi/step/`, what its `pmi="annotate"` drawing made of it: a placed term that
//! cites it (`Drawing.dimension_sources`), its own (`standalone`) or a recognised feature's
//! (`absorbed`, with the parameter it states), or the issues naming it (`refused`, or `dropped`
//! when placement found it no room). A draftwright record is paired with the port's as
//! `tests/pmi.rs` pairs them (by the Part 21 instance draftwright names, else by the faces), from
//! draftwright's reading in `tests/fixtures/pmi/<stem>.json.gz`. The outcome, the view and the
//! label (draftwright's `ø` read as the port's `⌀`) are compared; a record either side has no
//! pair for is a difference of field `record`. A common label, read as a dimension, is drawn as a
//! label and compared by `tests/pmi_drawings.rs`.
//!
//! Every difference must be listed, with a verdict and evidence, in
//! `tests/fixtures/known_pmi_dimensions.json`; a listed difference that has gone away must be
//! taken off.

#[path = "common/pdf.rs"]
mod pdf;

#[path = "common/support.rs"]
#[macro_use]
mod support;

#[path = "common/cache.rs"]
mod cache;

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::dims::PlannedDimension;
use draftwright::model::{self, Analysis, Plan, Spec};
use draftwright::pmi::{Category, Reading, Record};
use serde::Deserialize;
use serde_json::{Value, json};

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn fixtures() -> PathBuf {
    root().join("tests/fixtures/pmi")
}

fn gunzip(raw: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    flate2::read::GzDecoder::new(raw)
        .read_to_end(&mut out)
        .unwrap();
    out
}

fn sha256(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// The AP242 fixtures: `(file name as captured, stem, bytes)`.
fn inputs(only: &str) -> Vec<(String, String, Vec<u8>)> {
    let mut paths: Vec<PathBuf> = std::fs::read_dir(fixtures().join("step"))
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|x| x == "gz"))
        .collect();
    paths.retain(|path| {
        let name = path.file_name().unwrap().to_string_lossy();
        name.strip_suffix(".gz") == Some(only) || name.split('.').next() == Some(only)
    });
    paths.sort();
    paths
        .into_iter()
        .map(|p| {
            let name = p.file_name().unwrap().to_string_lossy().into_owned();
            let file = name.strip_suffix(".gz").unwrap().to_string();
            let stem = file.split('.').next().unwrap().to_string();
            (file, stem, gunzip(&std::fs::read(&p).unwrap()))
        })
        .collect()
}

fn json_gz(path: &Path) -> Value {
    serde_json::from_slice(&gunzip(&std::fs::read(path).unwrap())).unwrap()
}

/// The part and its Analysis, with Rust quiddity's features (the CLI's default, and what
/// draftwright's drawing correlates its PMI with), from *bytes* (written to a scratch file of its
/// own, as `analyse` reads a path and the tests run in parallel).
fn analysed_uncached(file: &str, bytes: &[u8]) -> (draftwright::haecceity::Part, Analysis) {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("pmi_dimensions")
        // nextest processes each start their local counter at zero.
        .join(format!("{}-{n}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(file);
    std::fs::write(&path, bytes).unwrap();
    let out = model::analyse_native(&path).unwrap();
    std::fs::remove_file(&path).unwrap();
    out
}

fn analysed(file: &str, bytes: &[u8]) -> (std::sync::Arc<draftwright::haecceity::Part>, Analysis) {
    cache::analysed(file, bytes, true, || analysed_uncached(file, bytes))
}

fn records(a: &Analysis) -> &[Record] {
    match &a.pmi {
        Some(Reading::Read(r)) => &r.records,
        other => panic!("not read: {other:?}"),
    }
}

/// The instance number of `#N`.
fn number(s: &str) -> Option<u64> {
    s.strip_prefix('#')?.parse().ok()
}

fn close(a: Option<f64>, b: Option<f64>) -> bool {
    match (a, b) {
        (None, None) => true,
        (Some(a), Some(b)) => (a - b).abs() <= 1e-9 * a.abs().max(b.abs()).max(1.0),
        _ => false,
    }
}

/// draftwright's dimension records of *reading* paired with the port's *rust* records, as
/// `tests/pmi.rs` pairs them: draftwright's `source_id` to the port's record index.
fn pairs(reading: &Value, rust: &[Record]) -> BTreeMap<String, usize> {
    let records: Vec<&Value> = reading["records"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|r| r["source_category"] == "dimension")
        .collect();
    let mut taken = vec![false; rust.len()];
    let mut out = BTreeMap::new();
    let id = |r: &Value| r["source_id"].as_str().unwrap().to_string();
    let dimension = |j: usize| rust[j].category == Category::Dimension;
    for rec in &records {
        if let Some(key) = number(rec["key"].as_str().unwrap_or(""))
            && let Some(j) =
                (0..rust.len()).find(|&j| !taken[j] && dimension(j) && rust[j].part21_id == key)
        {
            taken[j] = true;
            out.insert(id(rec), j);
        }
    }
    for rec in &records {
        if out.contains_key(&id(rec)) {
            continue;
        }
        let faces: BTreeSet<u64> = rec["faces"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_u64().unwrap())
            .collect();
        if faces.is_empty() {
            continue;
        }
        let same = |j: usize| rust[j].face_ids.iter().copied().collect::<BTreeSet<_>>() == faces;
        let candidates: Vec<usize> = (0..rust.len())
            .filter(|&j| !taken[j] && dimension(j) && same(j))
            .collect();
        let best = candidates
            .iter()
            .copied()
            .find(|&j| {
                rust[j].kind == rec["kind"].as_str().unwrap()
                    && close(rust[j].value, rec["value"].as_f64())
            })
            .or_else(|| candidates.first().copied());
        if let Some(j) = best {
            taken[j] = true;
            out.insert(id(rec), j);
        }
    }
    out
}

/// draftwright's label as the port writes its symbols.
fn norm(label: &str) -> String {
    label.replace(['ø', 'Ø'], "⌀")
}

/// draftwright's outcome for a captured record: `standalone`, `absorbed`, `dropped` (placement
/// found it no room) or `refused`, with the view and label it was drawn with.
fn python_outcome(rec: &Value) -> (String, Value, Value) {
    let drawn = rec["drawn"].as_array().unwrap();
    if let Some(d) = drawn.first() {
        let outcome = if d["parameter"].is_null() {
            "standalone"
        } else {
            "absorbed"
        };
        let label = d["label"].as_str().map_or(Value::Null, |l| json!(norm(l)));
        return (outcome.into(), d["view"].clone(), label);
    }
    let codes: BTreeSet<&str> = rec["issues"]
        .as_array()
        .unwrap()
        .iter()
        .map(|i| i["code"].as_str().unwrap())
        .collect();
    if codes.contains("pmi_dropped") || codes.contains("callout_dropped") {
        return ("dropped".into(), Value::Null, Value::Null);
    }
    (
        "refused".into(),
        Value::Null,
        json!(codes.into_iter().collect::<Vec<_>>()),
    )
}

/// The item ID of the record *r* of *a* (`pmi:<identity>`), if it has one.
fn item_id(a: &Analysis, r: &Record) -> Option<String> {
    match &a.pmi {
        Some(Reading::Read(report)) => report.identities().ids.get(&r.source_id).cloned(),
        _ => None,
    }
}

/// The planned item that states the port's record, if any: the one claiming its item ID.
fn stating<'a>(plan: &'a Plan, a: &Analysis, r: &Record) -> Option<&'a PlannedDimension> {
    let feature = item_id(a, r)?;
    plan.dimensions
        .iter()
        .find(|d| d.measurements.iter().any(|m| m.feature == feature))
}

/// The port's outcome for its record, in draftwright's terms.
fn rust_outcome(plan: &Plan, a: &Analysis, r: &Record) -> (String, Value, Value) {
    if let Some(d) = stating(plan, a, r) {
        let own = item_id(a, r).is_some_and(|id| d.id.starts_with(&format!("d:{id}/")));
        if d.placed.is_none() {
            return ("dropped".into(), Value::Null, Value::Null);
        }
        let outcome = if own { "standalone" } else { "absorbed" };
        let view = d.view.strip_prefix("v:").unwrap_or(&d.view);
        return (outcome.into(), json!(view), json!(norm(&d.label)));
    }
    let codes: BTreeSet<&str> = plan
        .problems
        .iter()
        .filter(|p| p.refs.contains(&r.source_id))
        .map(|p| p.code.as_str())
        .collect();
    (
        "refused".into(),
        Value::Null,
        json!(codes.into_iter().collect::<Vec<_>>()),
    )
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    file: String,
    /// draftwright's `source_id`, or the port's when draftwright has no such record.
    record: String,
    /// The port's record paired with draftwright's, or null.
    port: Value,
    field: String,
    python: Value,
    rust: Value,
    /// `rust-correct`, `rust-wrong`, `equivalent`, `undetermined` or `not-applicable`.
    verdict: String,
    evidence: String,
}

#[derive(Debug, Clone, PartialEq)]
struct Diff {
    file: String,
    record: String,
    port: Value,
    field: String,
    python: Value,
    rust: Value,
}

fn compare(file: &str, py: &Value, reading: &Value, a: &Analysis, plan: &Plan) -> Vec<Diff> {
    let rust = records(a);
    let paired = pairs(reading, rust);
    let mut diffs = Vec::new();
    let failed = py["error"].as_str();
    if let Some(e) = failed {
        let drawn = rust
            .iter()
            .filter(|r| r.category == Category::Dimension)
            .filter(|r| stating(plan, a, r).is_some_and(|d| d.placed.is_some()))
            .count();
        diffs.push(Diff {
            file: file.into(),
            record: "file".into(),
            port: Value::Null,
            field: "drawing".into(),
            python: json!(e),
            rust: json!(format!("{drawn} dimensions drawn")),
        });
    }
    let mut seen = BTreeSet::new();
    // A common label (read as a dimension) is drawn as a label, compared with draftwright's by
    // `tests/pmi_drawings.rs`.
    for rec in py["records"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|rec| rec["kind"] != "common_label")
    {
        let id = rec["source_id"].as_str().unwrap().to_string();
        let port = paired.get(&id).map(|&j| &rust[j]);
        let port_id = port.map_or(Value::Null, |r| json!(r.source_id));
        let mut diff = |field: &str, python: Value, rust: Value| {
            diffs.push(Diff {
                file: file.into(),
                record: id.clone(),
                port: port_id.clone(),
                field: field.into(),
                python,
                rust,
            })
        };
        let Some(r) = port else {
            diff(
                "record",
                json!({"kind": rec["kind"], "label": rec["label"]}),
                Value::Null,
            );
            continue;
        };
        seen.insert(r.source_id.clone());
        if failed.is_some() {
            continue;
        }
        let (po, pv, pl) = python_outcome(rec);
        let (ro, rv, rl) = rust_outcome(plan, a, r);
        if po != ro {
            diff(
                "outcome",
                json!({"outcome": po, "detail": pl}),
                json!({"outcome": ro, "detail": rl}),
            );
            continue;
        }
        if pv != rv {
            diff("view", pv, rv);
        }
        if po != "refused" && pl != rl {
            diff("label", pl, rl);
        }
    }
    for r in rust
        .iter()
        .filter(|r| r.category == Category::Dimension && r.kind != "common_label")
    {
        if !seen.contains(&r.source_id) {
            let (ro, _, rl) = rust_outcome(plan, a, r);
            diffs.push(Diff {
                file: file.into(),
                record: r.source_id.clone(),
                port: json!(r.source_id),
                field: "record".into(),
                python: Value::Null,
                rust: json!({"outcome": ro, "detail": rl}),
            });
        }
    }
    diffs
}

pmi_cases!(pmi_dimensions_match_draftwright_or_are_known);
fn pmi_dimensions_match_draftwright_or_are_known(only: &str) {
    let known: Vec<Known> = serde_json::from_str(
        &std::fs::read_to_string(root().join("tests/fixtures/known_pmi_dimensions.json")).unwrap(),
    )
    .unwrap();
    let verdicts = [
        "rust-correct",
        "rust-wrong",
        "equivalent",
        "undetermined",
        "not-applicable",
    ];
    for k in &known {
        assert!(
            support::PMI_FILES.contains(&k.file.as_str()),
            "unknown fixture: {}",
            k.file
        );
        assert!(
            verdicts.contains(&k.verdict.as_str()) && !k.evidence.trim().is_empty(),
            "{k:?}"
        );
    }
    let mut diffs = Vec::new();
    let mut records = 0;
    for (file, stem, bytes) in inputs(only).into_iter().filter(|(file, _, _)| file == only) {
        let py = json_gz(&fixtures().join(format!("dimensions/{stem}.json.gz")));
        let reading = json_gz(&fixtures().join(format!("{stem}.json.gz")));
        assert_eq!(py["file"], file.as_str());
        assert_eq!(
            py["sha256"],
            sha256(&bytes).as_str(),
            "{file}: not the captured file"
        );
        let (_, a) = analysed(&file, &bytes);
        let plan = cache::default_plan(&a, false);
        let d = compare(&file, &py, &reading, &a, &plan);
        records += py["records"].as_array().unwrap().len();
        eprintln!(
            "{file}: {} draftwright records, {} differences",
            py["records"].as_array().unwrap().len(),
            d.len()
        );
        diffs.extend(d);
    }
    let listed: Vec<Diff> = known
        .iter()
        .filter(|k| k.file == only)
        .map(|k| Diff {
            file: k.file.clone(),
            record: k.record.clone(),
            port: k.port.clone(),
            field: k.field.clone(),
            python: k.python.clone(),
            rust: k.rust.clone(),
        })
        .collect();
    let keys: BTreeSet<(String, String, String)> = listed
        .iter()
        .map(|d| (d.file.clone(), d.record.clone(), d.field.clone()))
        .collect();
    assert_eq!(keys.len(), listed.len(), "a difference is listed twice");
    let unlisted: Vec<&Diff> = diffs.iter().filter(|d| !listed.contains(d)).collect();
    let stale: Vec<&Diff> = listed.iter().filter(|d| !diffs.contains(d)).collect();
    eprintln!(
        "{records} draftwright records; {} differences ({} known)",
        diffs.len(),
        listed.len()
    );
    let as_json = |v: &[&Diff]| {
        v.iter()
            .map(|d| {
                json!({"file": d.file, "record": d.record, "port": d.port, "field": d.field, "python": d.python, "rust": d.rust})
                    .to_string()
            })
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert!(
        unlisted.is_empty() && stale.is_empty(),
        "differences not in known_pmi_dimensions.json:\n{}\nlisted but no longer differing:\n{}",
        as_json(&unlisted),
        as_json(&stale)
    );
}

/// A fixture by stem: its file name and bytes.
fn fixture(stem: &str) -> (String, Vec<u8>) {
    inputs(stem)
        .into_iter()
        .find(|(_, s, _)| s == stem)
        .map(|(f, _, b)| (f, b))
        .unwrap()
}

fn planned(stem: &str) -> (std::sync::Arc<draftwright::haecceity::Part>, Analysis, Plan) {
    let (file, bytes) = fixture(stem);
    let (part, a) = analysed(&file, &bytes);
    let plan = cache::default_plan(&a, false);
    (part, a, plan)
}

fn planned_uncached(stem: &str) -> (draftwright::haecceity::Part, Analysis, Plan) {
    let (file, bytes) = fixture(stem);
    let (part, a) = analysed_uncached(&file, &bytes);
    let plan = model::plan(&a, &Spec::default()).unwrap();
    (part, a, plan)
}

/// The codes that say a PMI dimension is not drawn, and why.
const REFUSALS: [&str; 3] = [
    "pmi_not_lowered",
    "pmi_dimensions_not_ported",
    "pmi_not_drawn",
];

pmi_cases!(every_dimension_is_drawn_or_reported_never_both);
fn every_dimension_is_drawn_or_reported_never_both(only: &str) {
    for (file, _, bytes) in inputs(only).into_iter().filter(|(file, _, _)| file == only) {
        let (_, a) = analysed(&file, &bytes);
        let plan = cache::default_plan(&a, false);
        for r in records(&a)
            .iter()
            .filter(|r| r.category == Category::Dimension && r.kind != "common_label")
        {
            let stated = stating(&plan, &a, r);
            // #33: what states the part's PMI is controlling, never a reference dimension.
            if let Some(d) = stated {
                assert!(!d.reference, "{file}: {} is drawn as reference", d.id);
            }
            let reported = plan
                .problems
                .iter()
                .any(|p| REFUSALS.contains(&p.code.as_str()) && p.refs.contains(&r.source_id));
            assert!(
                stated.is_some() != reported,
                "{file}: {} is stated ({}) or reported ({reported}), exactly one",
                r.source_id,
                stated.is_some()
            );
            // A record whose reading left blockers, or whose faces prove no witness, is never
            // drawn.
            let unproven = r.geometry.as_ref().is_some_and(|g| !g.reasons.is_empty());
            if !r.blockers.is_empty() || unproven {
                assert!(reported, "{file}: {} has blockers", r.source_id);
            }
            // An item stated in an undrawn place is said (view_plan_incomplete), not lost.
            if let Some(d) = stated
                && d.placed.is_none()
            {
                assert!(
                    plan.problems.iter().any(|p| p.subjects.contains(&d.id)),
                    "{file}: {} unplaced and unreported",
                    d.id
                );
            }
        }
        // A standalone item's basis says where it came from.
        for d in plan
            .dimensions
            .iter()
            .filter(|d| d.id.starts_with("d:pmi:"))
        {
            assert!(d.basis.starts_with("source-authored"), "{}", d.basis);
        }
    }
}

part_cases!(the_open_questions_stay_refused;
    nist_stc_10_asme1_ap242_e2 => "nist_stc_10_asme1_ap242-e2",
    nist_ftc_10_asme1_ap242_e2 => "nist_ftc_10_asme1_ap242-e2",
);
fn the_open_questions_stay_refused(stem: &str) {
    // Unsupported supplemental geometry and fits without limits stay refused with a remedy.
    let needle = match stem {
        "nist_stc_10_asme1_ap242-e2" => "supplemental geometry",
        "nist_ftc_10_asme1_ap242-e2" => "stated without limits",
        _ => unreachable!(),
    };

    let (_, a, plan) = planned(stem);
    let open: Vec<&Record> = records(&a)
        .iter()
        .filter(|r| r.category == Category::Dimension)
        .filter(|r| r.blockers.iter().any(|b| b.contains(needle)))
        .collect();
    assert!(!open.is_empty(), "{stem}: {needle}");
    for r in open {
        assert!(stating(&plan, &a, r).is_none(), "{}", r.source_id);
        let p = plan
            .problems
            .iter()
            .find(|p| p.code == "pmi_not_lowered" && p.refs.first() == Some(&r.source_id))
            .unwrap();
        assert!(
            p.message
                .contains("add the required dimension in the Spec and review it manually")
                && p.refs.iter().any(|reason| reason.contains(needle))
                && !p
                    .refs
                    .iter()
                    .any(|reason| reason.contains("open question for the maintainer")),
            "{}",
            p.message
        );
    }
}

#[test]
fn a_hole_size_naming_its_rim_edge_reaches_its_callout_and_schedule() {
    // NIST STC-10 names each hole of its ⌀2.5 ±0.15, ⌀3.5 ±0.2, ⌀7 ±0.25 and ⌀3.5 G6 by its
    // cylindrical face and a rim edge of that face: the edge names the same hole again.
    let (_, a, plan) = planned("nist_stc_10_asme1_ap242-e2");
    for source in [
        "dimension:#296",
        "dimension:#305",
        "dimension:#306",
        "dimension:#309",
        "dimension:#310",
        "dimension:#319",
    ] {
        let r = records(&a).iter().find(|r| r.source_id == source).unwrap();
        assert!(!r.edges.is_empty(), "{source} names an edge");
        let g = r.geometry.as_ref().unwrap();
        assert!(g.reasons.is_empty(), "{source}: {:?}", g.reasons);
        assert!(r.blockers.is_empty(), "{source}: {:?}", r.blockers);
        assert!(stating(&plan, &a, r).is_some(), "{source}");
        assert!(
            !plan.problems.iter().any(|p| {
                REFUSALS.contains(&p.code.as_str())
                    && p.refs.first().map(String::as_str) == Some(source)
            }),
            "{source}"
        );
    }
    // The drawing's schedule repeats each size as its callout states it.
    let drawn = cache::default_plan(&a, true);
    let rows: Vec<&str> = drawn
        .hole_tables
        .iter()
        .flat_map(|t| &t.rows)
        .map(|r| r.cells[1].as_str())
        .collect();
    assert_eq!(
        rows.iter().filter(|c| **c == "⌀2.5 ±0.15 THRU").count(),
        8,
        "{rows:?}"
    );
    assert_eq!(
        rows.iter().filter(|c| **c == "⌀3.5 G6 THRU").count(),
        3,
        "{rows:?}"
    );
    assert_eq!(
        rows.iter().filter(|c| **c == "⌀3.5 ±0.2 THRU").count(),
        3,
        "{rows:?}"
    );
    // The ⌀10 spotfaces name a plane, and the supplemental-geometry ⌀3 stays an open question.
    for source in ["dimension:#299", "dimension:#313"] {
        let r = records(&a).iter().find(|r| r.source_id == source).unwrap();
        assert!(stating(&plan, &a, r).is_none(), "{source}");
    }
    // An edge off the referenced cylinders is no size reference.
    let (file, bytes) = fixture("nist_stc_10_asme1_ap242-e2");
    let (part, _) = analysed(&file, &bytes);
    let mut r = records(&a)
        .iter()
        .find(|r| r.source_id == "dimension:#296")
        .unwrap()
        .clone();
    let rim = r.edges.clone();
    r.edges = vec![
        (0..part.edges.len())
            .find(|e| !rim.contains(e) && part.edges[*e].start[2] > 20.0)
            .unwrap(),
    ];
    let g = draftwright::pmi_lowering::dimension_geometry(&part, &r);
    assert_eq!(g.reasons, ["one diameter reference is not a face"]);
}

#[test]
fn a_toleranced_bore_annotates_its_callout_and_a_spec_tolerance_wins() {
    let (_, a, plan) = planned("spool_fits");
    let callout = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:hole_pattern:f0/bore.diameter")
        .unwrap();
    // quiddity 0.5.0 (#244) recognises the holes' 1 × 45° mouth chamfer on the top face; the callout states it.
    assert_eq!(callout.label, "6× ⌀10 +0.09/0 THRU C1 EQ\nSP ON ø90 BC");
    assert!(callout.basis.contains("lower_ap242_hole_tolerances"));
    let feature = callout
        .measurements
        .iter()
        .find(|m| m.feature.starts_with("pmi:"))
        .unwrap()
        .feature
        .clone();
    let Some(Reading::Read(report)) = &a.pmi else {
        panic!("its PMI is read")
    };
    let source = report.identities().source(&feature).unwrap().to_string();
    // Stated once: no item of its own.
    assert!(
        !plan
            .dimensions
            .iter()
            .any(|d| d.id.starts_with(&format!("d:{feature}/")))
    );
    // The spec's own tolerance on that size wins, and the PMI one is said not drawn.
    let mut spec = Spec::default();
    spec.dimensions.insert(
        callout.id.clone(),
        serde_json::from_value(json!({"tolerance": "±0.05"})).unwrap(),
    );
    let edited = model::plan(&a, &spec).unwrap();
    let c = edited
        .dimensions
        .iter()
        .find(|d| d.id == callout.id)
        .unwrap();
    // The holes' 1 × 45° mouth chamfer (quiddity 0.5.0, #244) is stated: `C1`.
    assert_eq!(c.label, "6× ⌀10 ±0.05 THRU C1 EQ SP\nON ø90 BC");
    assert!(
        c.measurements
            .iter()
            .all(|m| !m.feature.starts_with("pmi:"))
    );
    assert!(
        !c.basis.contains("lower_ap242_hole_tolerances"),
        "{}",
        c.basis
    );
    assert!(edited.problems.iter().any(|p| {
        p.code == "pmi_not_lowered"
            && p.refs.contains(&source)
            && p.message
                .contains("Remove the Spec override to use the STEP tolerance")
            && p.refs
                .iter()
                .any(|r| r.contains("lower_ap242_hole_tolerances"))
    }));

    // A fit on every hole wins the same way, and only the fit is printed.
    let holes: Vec<String> = callout
        .measurements
        .iter()
        .filter(|m| m.parameter == "bore.diameter")
        .map(|m| m.feature.clone())
        .collect();
    assert_eq!(holes.len(), 6, "{holes:?}");
    let fitted = |on: &[String]| {
        let mut spec = Spec::default();
        for h in on {
            spec.features.insert(
                h.clone(),
                serde_json::from_value(json!({"fit": "H7"})).unwrap(),
            );
        }
        let edited = model::plan(&a, &spec).unwrap();
        let c = edited
            .dimensions
            .iter()
            .find(|d| d.id == callout.id)
            .unwrap()
            .clone();
        (c, edited.problems)
    };
    let (c, problems) = fitted(&holes);
    assert_eq!(c.label, "6× ⌀10 H7 THRU C1 EQ SP ON\nø90 BC");
    assert!(problems.iter().any(|p| p.code == "pmi_not_lowered"
        && p.refs.contains(&source)
        && p.message.contains("its own fit")
        && !p.message.contains("cannot be printed")));
    // A fit on some of them is withheld: the label states neither, and the report says so.
    let (c, problems) = fitted(&holes[..1]);
    assert_eq!(c.label, "6× ⌀10 THRU C1 EQ SP ON ø90\nBC");
    assert!(problems.iter().any(|p| p.code == "tolerance_withheld"));
    assert!(problems.iter().any(|p| p.code == "pmi_not_lowered"
        && p.refs.contains(&source)
        && p.message.contains("cannot be printed")
        && p.refs.contains(&"tolerance_withheld".into())));
}

#[test]
fn holes_of_different_requirements_are_called_out_apart() {
    let (_, _, plan) = planned("nist_ctc_01_asme1_ap242-e1");
    let labels: Vec<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/bore.diameter") && d.view == "v:front")
        .map(|d| d.label.as_str())
        .collect();
    assert!(labels.contains(&"⌀20 +0.1/-0.05 ↧ 45"), "{labels:?}");
    assert!(labels.contains(&"⌀20 +0.05/-0.1 ↧ 45"), "{labels:?}");
    // The 2×2 grid of ⌀35 whose holes carry three requirements is split too (its pitches still
    // locate the holes).
    let plan_labels: Vec<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/bore.diameter") && d.view == "v:plan")
        .map(|d| d.label.as_str())
        .collect();
    for want in ["⌀35 0/-0.2 THRU", "⌀35 +0.2/0 THRU", "2× ⌀35 ±0.2 THRU"] {
        assert!(plan_labels.contains(&want), "{want}: {plan_labels:?}");
    }
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.id.contains("hole_pattern:f71/grid_pitch"))
    );
}

#[test]
fn an_omitted_standalone_dimension_is_said_not_drawn() {
    let (_, a, plan) = planned("string_post_tapped");
    let own = plan
        .dimensions
        .iter()
        .find(|d| d.id.starts_with("d:pmi:"))
        .unwrap()
        .clone();
    let mut spec = Spec::default();
    spec.dimensions.insert(
        own.id.clone(),
        serde_json::from_value(json!({"omit": true})).unwrap(),
    );
    let edited = model::plan(&a, &spec).unwrap();
    assert!(edited.dimensions.iter().all(|d| d.id != own.id));
    let Some(Reading::Read(report)) = &a.pmi else {
        panic!("its PMI is read")
    };
    let ids = report.identities();
    let source = ids.source(&own.measurements[0].feature).unwrap();
    assert!(
        edited
            .problems
            .iter()
            .any(|p| p.code == "pmi_not_drawn" && p.refs.iter().any(|s| s == source))
    );
}

#[test]
fn a_basic_dimension_is_boxed_in_every_format_and_searchable() {
    let (part, a, plan) = planned("string_post_tapped");
    let basic = plan
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter.ends_with(".basic"))
        })
        .unwrap();
    assert_eq!(basic.label, "1.2");
    let p = basic.placed.as_ref().unwrap();
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    // The box: the four sides of its label box, on the dims layer.
    let [x0, y0, x1, y1] = p.label_box;
    let svg = drawing.svg();
    let mut bare = plan.clone();
    for d in &mut bare.dimensions {
        d.measurements.iter_mut().for_each(|m| {
            if let Some(kind) = m.parameter.strip_suffix(".basic") {
                m.parameter = kind.to_string();
            }
        });
    }
    let unboxed = model::render(&part, &a, &bare, &font).unwrap();
    assert!(svg.len() > unboxed.svg().len(), "the box adds ink");
    assert!(x1 - x0 > 0.0 && y1 - y0 > 0.0);
    let written: Vec<String> = drawing.text.iter().map(|r| r.text.clone()).collect();
    assert!(written.iter().any(|t| t == "1.2"));
    assert_eq!(pdf::svg_text(&svg), written, "the SVG's text");
    let pdf_bytes = drawing.pdf(&|name: &str| std::fs::read(root().join("fonts").join(name)).ok());
    assert_eq!(pdf::pdf_text(&pdf_bytes), written, "the PDF's text");
    assert!(
        drawing.dxf().len() > unboxed.dxf().len(),
        "the DXF draws the box"
    );
    assert!(!drawing.png(100.0).unwrap().is_empty());
}

/// #34: the editor's spindle with a reviewer's PMI (`samples/cnc-review-spindle.step.gz`): the
/// ⌀4 cross hole's 23 from datum A (the end face) is basic. A z plane to an x bore's axis was not
/// proven (draftwright proves an x or y plane to a z bore only), so the default location stood in,
/// drawn plain. The PMI dimension now draws it, boxed, in the side view that sees the hole end on,
/// and no unboxed 23 is drawn.
#[test]
fn a_basic_location_of_a_turned_parts_cross_hole_is_boxed() {
    let raw =
        std::fs::read(root().join("tests/fixtures/samples/cnc-review-spindle.step.gz")).unwrap();
    let (part, a) = analysed("cnc-review-spindle.step", &gunzip(&raw));
    let plan = cache::default_plan(&a, false);
    let basic = |d: &PlannedDimension| {
        d.measurements
            .iter()
            .any(|m| m.feature.starts_with("pmi:") && m.parameter.ends_with(".basic"))
    };
    let drawn: Vec<&PlannedDimension> = plan
        .dimensions
        .iter()
        .filter(|d| d.label == "23" && d.placed.is_some())
        .collect();
    assert_eq!(drawn.len(), 1, "{drawn:?}");
    let d = drawn[0];
    assert!(basic(d), "{d:?}");
    assert_eq!(d.view, "v:side");
    // It claims the default location it conveys.
    assert!(
        d.measurements
            .iter()
            .any(|m| m.feature == "f:hole:f7" && m.parameter == "location.length.z"),
        "{d:?}"
    );
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| REFUSALS.contains(&p.code.as_str()) && p.message.contains("(23)")),
        "{:?}",
        plan.problems
    );
    // The box is drawn: the plan's ink is more than the same plan with the basic status taken off.
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let mut bare = plan.clone();
    for d in &mut bare.dimensions {
        d.measurements.iter_mut().for_each(|m| {
            if let Some(kind) = m.parameter.strip_suffix(".basic") {
                m.parameter = kind.to_string();
            }
        });
    }
    let boxed = model::render(&part, &a, &plan, &font).unwrap();
    let unboxed = model::render(&part, &a, &bare, &font).unwrap();
    assert!(boxed.svg().len() > unboxed.svg().len(), "the box adds ink");
}

/// The drawn marks of *plan*, as lint reads them back from the sheet.
fn marks(plan: &Plan) -> Vec<draftwright::lint::Mark> {
    plan.dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .map(|d| draftwright::lint::Mark {
            precision: None,
            units: Default::default(),
            name: d.id.clone(),
            view: Some(d.view.clone()),
            text: d.label.clone(),
            claims: d
                .measurements
                .iter()
                .map(|m| draftwright::output::Claim {
                    feature: m.feature.clone(),
                    parameter: m.parameter.clone(),
                })
                .collect(),
        })
        .collect()
}

/// The drawn labels of *plan*'s dimensions along z in the front view (the spindle's axis).
fn front_z_labels(plan: &Plan) -> Vec<String> {
    plan.dimensions
        .iter()
        .filter(|d| d.placed.is_some() && d.view == "v:front" && d.kind == "linear")
        .map(|d| d.label.clone())
        .collect()
}

/// #33: the editor's spindle names its bottom end face datum A, and its inspection list measures
/// the shoulders from A. Its step lengths were a chain (22, 18) with the 12 as reference `(12)`;
/// they are now a baseline from A: 12, 34 and the overall 52, none in brackets, and lint finds
/// every band's length determined.
#[test]
fn a_turned_profile_is_dimensioned_from_its_datum_end() {
    let raw =
        std::fs::read(root().join("tests/fixtures/samples/cnc-review-spindle.step.gz")).unwrap();
    let (_, a) = analysed("cnc-review-spindle.step", &gunzip(&raw));
    let plan = cache::default_plan(&a, false);
    let labels = front_z_labels(&plan);
    for want in ["12", "34", "52"] {
        let d = plan
            .dimensions
            .iter()
            .find(|d| d.placed.is_some() && d.view == "v:front" && d.label == want)
            .unwrap_or_else(|| panic!("{want} is not drawn: {labels:?}"));
        assert!(d.basis.contains("datum A"), "{}: {}", d.id, d.basis);
        assert!(!d.reference, "{}", d.id);
    }
    for gone in ["22", "18", "(12)"] {
        assert!(!labels.iter().any(|l| l == gone), "{gone}: {labels:?}");
    }
    assert!(
        !plan
            .dimensions
            .iter()
            .any(|d| d.reference || d.label.starts_with('(')),
        "{labels:?}"
    );
    let issues = draftwright::lint::check(&a, &marks(&plan));
    assert!(
        !issues.iter().any(|i| matches!(
            i.code.as_str(),
            "axial_length_missing"
                | "turned_step_requirement_missing"
                | "turned_step_requirement_unverifiable"
                | "closed_dimension_chain"
                | "duplicate_measurement"
        )),
        "{issues:?}"
    );
    // The band lengths the baseline conveys keep their IDs: the editor's saved spec, which places
    // the ⌀15 band's length, still plans, and the plan says what conveys it.
    let spec: Spec = serde_json::from_value(json!({
        "schema": "draftwright.spec/2",
        "dimensions": {"d:f:turned_step:f6/step.length": {"place": {
            "along": "outside", "lane": 0, "side": "left", "view": "front"}}}
    }))
    .unwrap();
    let placed = model::plan(&a, &spec).unwrap();
    assert_eq!(front_z_labels(&placed), labels);
    let covered = plan
        .omitted
        .iter()
        .find(|o| o.id == "d:f:turned_step:f6/step.length")
        .unwrap();
    assert!(
        covered
            .basis
            .contains("d:f:turned_step:f6/step_position.length"),
        "{}",
        covered.basis
    );
}

/// #33, the case the issue describes: the PMI states the spindle's heights from datum A (12 ±0.2,
/// 34 ±0.3, 52 ±0.3 to its shoulders and end, 42 ±0.3 to the tapped hole's floor). They are drawn
/// as they are stated, from A, every one controlling, and no chain restates the steps between
/// them. With datum A taken away the lengths alone establish the baseline (draftwright's
/// `_authored_step_baseline`).
#[test]
fn pmi_heights_from_a_datum_are_drawn_as_a_baseline_not_a_chain() {
    let raw =
        std::fs::read(root().join("tests/fixtures/samples/cnc-review-spindle.step.gz")).unwrap();
    let (_, base) = analysed("cnc-review-spindle.step", &gunzip(&raw));
    let template = records(&base)
        .iter()
        .find(|r| r.category == Category::Dimension && r.kind == "linear")
        .unwrap()
        .clone();
    for keep_datum in [true, false] {
        let mut a = base.clone();
        let Some(Reading::Read(report)) = &mut a.pmi else {
            unreachable!()
        };
        if !keep_datum {
            report.records.retain(|r| r.category != Category::Datum);
        }
        for (n, (to, tol)) in [(12.0, 0.2), (34.0, 0.3), (42.0, 0.3), (52.0, 0.3)]
            .into_iter()
            .enumerate()
        {
            let mut r = template.clone();
            let id = 9001 + n as u64;
            r.source_id = format!("dimension:#{id}");
            r.part21_id = id;
            r.instances = vec![id];
            r.value = Some(to);
            r.upper_tol = Some(tol);
            r.lower_tol = Some(-tol);
            r.basic = false;
            r.label = format!("{to} ±{tol}");
            // Faces of their own, so each record has an identity of its own.
            r.faces = vec![10, 100 + n];
            r.face_groups = vec![vec![10], vec![100 + n]];
            r.ref_bbox = Some([-2.0, -2.0, 0.0, 2.0, 2.0, to]);
            let g = r.geometry.as_mut().unwrap();
            g.reasons.clear();
            g.view = None;
            g.axis = Some("Z".into());
            g.stations = vec![[0.0, 0.0, 0.0], [0.0, 0.0, to]];
            report.records.push(r);
        }
        let plan = cache::default_plan(&a, false);
        let labels = front_z_labels(&plan);
        for want in ["12 ±0.2", "34 ±0.3", "42 ±0.3", "52 ±0.3"] {
            let d = plan
                .dimensions
                .iter()
                .find(|d| d.placed.is_some() && d.label == want)
                .unwrap_or_else(|| panic!("datum {keep_datum}: {want} not drawn: {labels:?}"));
            assert!(!d.reference, "{}", d.id);
        }
        for gone in ["22", "18", "(12)", "12", "34", "52"] {
            assert!(
                !labels.iter().any(|l| l == gone),
                "datum {keep_datum}: {gone}: {labels:?}"
            );
        }
        assert!(
            !plan
                .dimensions
                .iter()
                .any(|d| d.reference || d.label.starts_with('(')),
            "datum {keep_datum}: {labels:?}"
        );
    }
}

#[test]
fn what_the_faces_do_not_prove_is_reported_not_guessed() {
    let (_, a, _) = planned("nist_stc_10_asme1_ap242-e2");
    let source = records(&a)
        .iter()
        .find(|r| r.category == Category::Dimension && r.kind == "linear" && r.blockers.is_empty())
        .unwrap()
        .source_id
        .clone();
    let edit = |f: &dyn Fn(&mut Record)| {
        let mut b = a.clone();
        let Some(Reading::Read(r)) = &mut b.pmi else {
            unreachable!()
        };
        r.records
            .iter_mut()
            .filter(|r| r.source_id == source)
            .for_each(f);
        cache::default_plan(&b, false)
    };
    let reason = |plan: &Plan| {
        plan.problems
            .iter()
            .find(|p| p.refs.first() == Some(&source))
            .map(|p| (p.code.clone(), p.message.clone()))
            .unwrap()
    };
    let plan = edit(&|r| {
        r.geometry
            .as_mut()
            .unwrap()
            .reasons
            .push("linear reference groups occupy the same station".into())
    });
    assert_eq!(reason(&plan).0, "pmi_not_lowered");
    let plan = edit(&|r| {
        let g = r.geometry.as_mut().unwrap();
        g.axis = None;
        g.stations = vec![[0.0, 0.0, 0.0], [12.0, 13.0, 14.0]];
    });
    assert_eq!(reason(&plan).0, "pmi_not_lowered");
    assert!(reason(&plan).1.contains("principal projection plane"));
    let plan = edit(&|r| r.geometry = None);
    assert!(reason(&plan).1.contains("analyse the part again"));
    let plan = edit(&|r| r.kind = "angular".into());
    assert!(reason(&plan).1.contains("angle"));
}

#[test]
fn coplanar_composites_measure_the_plane_and_attach_to_its_patches() {
    let raw = std::fs::read(fixtures().join("spindle-coplanar-groups.step.gz")).unwrap();
    let (part, a) = analysed("spindle-coplanar-groups.step", &gunzip(&raw));
    let plan = cache::default_plan(&a, false);
    for (id, value, axis, view, groups) in [
        (1397, 4.9, "Z", "v:front", vec![vec![1049], vec![734, 914]]),
        (1413, 5.0, "X", "v:plan", vec![vec![939, 1074], vec![1198]]),
    ] {
        let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
        assert_eq!(r.value, Some(value));
        // The datum's non-face member must not enter either reference group.
        assert_eq!(
            r.face_groups.iter().map(Vec::len).collect::<Vec<_>>(),
            groups.iter().map(Vec::len).collect::<Vec<_>>()
        );
        assert_eq!(
            r.face_ids.iter().copied().collect::<BTreeSet<_>>(),
            groups.into_iter().flatten().collect()
        );
        let g = r.geometry.as_ref().unwrap();
        assert!(g.reasons.is_empty(), "{}: {:?}", r.source_id, g.reasons);
        assert_eq!(g.axis.as_deref(), Some(axis));
        for (faces, &p) in r.face_groups.iter().zip(&g.stations) {
            assert!(
                faces.iter().any(|&f| {
                    let s = &part.faces[f].surface;
                    let Some((u, v)) = s.parameters(p, None) else {
                        return false;
                    };
                    let q = s.value(u, v);
                    (0..3).all(|k| (p[k] - q[k]).abs() < 1e-6)
                        && part.domain(f).is_some_and(|d| d.contains(u, v))
                }),
                "{}: {p:?} is not on its reference patches",
                r.source_id
            );
        }
        let d = stating(&plan, &a, r).expect("PMI must be claimed by a dimension");
        assert!(d.placed.is_some(), "{d:?}");
        assert_eq!(d.value, value);
        assert_eq!(d.label, value.to_string());
        assert_eq!(d.view, view);
        let camera = plan.views.iter().find(|v| v.id == d.view).unwrap();
        let projection =
            draftwright::haecceity::hlr::View::new(camera.direction, camera.up).unwrap();
        let centre = projection.map([0, 1, 2].map(|k| 0.5 * (a.envelope[0][k] + a.envelope[1][k])));
        let ink = d.placed.as_ref().unwrap();
        for &station in &g.stations {
            let projected = projection.map(station);
            let page = [0, 1].map(|k| camera.at[k] + camera.scale * (projected[k] - centre[k]));
            // Each extension starts one paper mm away from its proven attachment.
            assert!(
                ink.lines[..2].iter().any(|line| ((line[0][0] - page[0])
                    .hypot(line[0][1] - page[1])
                    - 1.0)
                    .abs()
                    < 1e-6),
                "{}: extensions miss the face witness at {page:?}: {:?}",
                d.id,
                ink.lines
            );
        }
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| REFUSALS.contains(&p.code.as_str()) && p.refs.contains(&r.source_id)),
            "{:?}",
            plan.problems
        );
        // The engine's same span must yield to PMI, even if its label is identical.
        assert_eq!(
            plan.dimensions
                .iter()
                .filter(|other| other.placed.is_some() && other.label == d.label)
                .count(),
            1
        );
    }
}

#[test]
fn coplanar_composites_still_refuse_an_inconsistent_nominal() {
    let raw = std::fs::read(fixtures().join("spindle-coplanar-groups.step.gz")).unwrap();
    let (part, mut a) = analysed_uncached("spindle-coplanar-groups.step", &gunzip(&raw));
    let Some(Reading::Read(report)) = &mut a.pmi else {
        panic!("PMI")
    };
    for r in report
        .records
        .iter_mut()
        .filter(|r| [1397, 1413].contains(&r.part21_id))
    {
        r.value = r.value.map(|v| v + 1.0);
        r.geometry = Some(draftwright::pmi_lowering::dimension_geometry(&part, r));
        assert!(
            r.geometry
                .as_ref()
                .unwrap()
                .reasons
                .iter()
                .any(|s| s.contains("differs from nominal"))
        );
    }
    let plan = cache::default_plan(&a, false);
    for id in [1397, 1413] {
        let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
        assert!(stating(&plan, &a, r).is_none());
        assert!(plan.problems.iter().any(|p| p.code == "pmi_not_lowered"
            && p.refs.contains(&r.source_id)
            && p.message.contains("differs from nominal")));
    }
}

#[test]
fn an_external_diameter_annotates_the_turned_size_with_its_signs() {
    // spool_fits' two ø130 flanges (a shaft band wholly below its nominal) and its ø70 step.
    let (_, _, plan) = planned("spool_fits");
    let label = |id: &str| {
        plan.dimensions
            .iter()
            .find(|d| d.id == id)
            .map(|d| d.label.clone())
            .unwrap()
    };
    assert_eq!(label("d:envelope/od.diameter"), "2× ø130 -0.014/-0.039");
    assert_eq!(
        label("d:f:turned_step:f89/step.diameter"),
        "ø70 -0.03/-0.06 OD"
    );
    // bolt_thread_knurl's shank: the turned step's ø6 takes its band, and nothing restates it.
    let (_, _, plan) = planned("bolt_thread_knurl");
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:turned_step:f6/step.diameter")
        .unwrap();
    assert_eq!(d.label, "ø6 0/-0.008");
    assert!(d.basis.contains("lower_ap242_external_diameter_tolerances"));
    assert!(!plan.dimensions.iter().any(|d| d.id.starts_with("d:pmi:")));
}

#[test]
fn a_shaft_fit_prints_its_class_by_default_and_stacked_deviations_on_request() {
    // #30: the editor's spindle states its ø15 journal as LIMITS_AND_FITS('g','shaft','6')
    // with no limits; both the turned step and hole print their classes by default, and
    // their computed ISO 286-2 deviations when explicitly enabled.
    let bytes = gunzip(
        &std::fs::read(root().join("tests/fixtures/samples/spindle_shaft_fit.step.gz")).unwrap(),
    );
    let (_, a) = analysed("spindle_shaft_fit.step", &bytes);
    let fit = records(&a)
        .iter()
        .find(|r| r.fit.as_deref() == Some("g6"))
        .unwrap();
    assert_eq!(fit.blockers, Vec::<String>::new());
    let plan = cache::default_plan(&a, false);
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.label.starts_with("ø15"))
        .unwrap();
    assert_eq!(d.label, "ø15 g6");
    assert!(d.placed.is_some());
    assert!(d.basis.contains("lower_ap242_external_diameter_tolerances"));
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| REFUSALS.contains(&p.code.as_str()) && p.refs.contains(&fit.source_id)),
        "the g6 fit is drawn, not refused"
    );
    assert!(
        plan.dimensions.iter().any(|d| d.label.contains("H7 THRU")),
        "the hole fit still prints"
    );
    // The opt-in retains the computed upper/lower deviations on both shaft and hole fits.
    let mut quiet = Spec::default();
    quiet.sheet.fit_deviations = Some(true);
    let plan = model::plan(&a, &quiet).unwrap();
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.label == "ø15 g6 -0.006\n-0.017")
    );
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.label.contains("H7 +0.012\n0"))
    );
}

part_cases!(the_same_part_gives_the_same_plan_and_bytes;
    assembly_plate_pin => "assembly_plate_pin",
    nist_ftc_10_asme1_ap242_e2 => "nist_ftc_10_asme1_ap242-e2",
);
fn the_same_part_gives_the_same_plan_and_bytes(stem: &str) {
    let (part, a, plan) = planned_uncached(stem);
    let again = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        serde_json::to_string(&plan).unwrap(),
        serde_json::to_string(&again).unwrap(),
        "{stem}"
    );
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let one = model::render(&part, &a, &plan, &font).unwrap();
    let two = model::render(&part, &a, &again, &font).unwrap();
    assert_eq!(one.svg(), two.svg(), "{stem}");
    assert_eq!(one.dxf(), two.dxf(), "{stem}");
    // The Analysis round-trips through JSON with its dimensions' geometry, and plans the
    // same.
    let text = serde_json::to_string(&a).unwrap();
    let back: Analysis = serde_json::from_str(&text).unwrap();
    assert_eq!(serde_json::to_string(&back).unwrap(), text, "{stem}");
    let replanned = model::plan(&back, &Spec::default()).unwrap();
    assert_eq!(
        serde_json::to_string(&replanned).unwrap(),
        serde_json::to_string(&plan).unwrap(),
        "{stem}"
    );
}

/// One span, one dimension for PMI (`share_pmi`): the plate states each grid hole's basic
/// location, so a row or column states the same span once per hole, and the default hole
/// locations measure spans the PMI states. The plan view draws each span once: no two of its
/// placed PMI or location dimensions share a label and value (every one of them here runs from
/// the part's left or front face), and every basic record is claimed by a planned dimension.
#[test]
fn a_span_the_pmi_states_is_drawn_once() {
    let (_, a, plan) = planned("hw_assembly_plate_pin_intent");
    let mut seen = BTreeSet::new();
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some() && d.view == "v:plan")
    {
        // The PMI dimensions and the default locations; a pitch of the same value spans
        // something else.
        if !(d.id.contains("/linear") || d.id.contains("/location.")) {
            continue;
        }
        assert!(
            seen.insert((d.label.clone(), d.value.to_bits())),
            "{} repeats {:?} in the plan view",
            d.id,
            d.label
        );
    }
    let conveyed: Vec<_> = plan
        .omitted
        .iter()
        .filter(|o| o.basis.contains("the part's PMI"))
        .collect();
    assert!(conveyed.len() >= 8, "{conveyed:?}");
    for r in records(&a).iter().filter(|r| r.kind == "linear" && r.basic) {
        assert!(
            stating(&plan, &a, r).is_some(),
            "{} is claimed by a planned dimension",
            r.source_id
        );
    }
}

/// Geometry and AP242 values can differ below a micrometre. The PMI states the requirement;
/// one line carries it and the default measurement, even when one is basic or in inches.
#[test]
fn a_basic_pmi_span_covers_defaults_despite_geometric_roundoff() {
    let (_, a, plan) = planned("bracket_positions");
    for feature in ["f:hole:f234", "f:hole:f236"] {
        let d = plan
            .dimensions
            .iter()
            .find(|d| {
                d.measurements
                    .iter()
                    .any(|m| m.feature == feature && m.parameter == "location.length.x")
            })
            .unwrap();
        assert!(d.id.starts_with("d:pmi:"), "{d:?}");
        assert!(d.measurements.iter().any(|m| m.parameter == "linear.basic"));
        assert!(
            !plan
                .dimensions
                .iter()
                .any(|other| other.id == format!("d:{feature}/location.length.x"))
        );
    }
    for r in records(&a).iter().filter(|r| {
        r.kind == "linear"
            && r.basic
            && [[234, 262], [235, 262], [236, 262]]
                .iter()
                .any(|faces| r.face_ids == *faces)
    }) {
        assert!(stating(&plan, &a, r).is_some(), "{}", r.source_id);
    }
}

#[test]
fn an_inch_pmi_span_covers_the_default_extent_despite_geometric_roundoff() {
    let (_, a, plan) = planned("nist_stc_06_asme1_ap242-e3");
    let d = plan
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == "envelope" && m.parameter == "depth.length")
        })
        .unwrap();
    assert!(d.id.starts_with("d:pmi:"), "{d:?}");
    assert_eq!(d.label, "304.8 ±0.254");
    let mut spec = Spec::default();
    spec.dimensions.insert(
        "d:envelope/depth.length".into(),
        serde_json::from_value(json!({"tolerance": "±0.1"})).unwrap(),
    );
    let separate = model::plan(&a, &spec).unwrap();
    assert!(
        separate
            .dimensions
            .iter()
            .any(|d| d.id == "d:envelope/depth.length" && d.label == "304.8 ±0.1")
    );
    assert!(
        separate
            .dimensions
            .iter()
            .any(|d| d.label == "304.8 ±0.254")
    );
}

#[test]
fn oblique_pmi_draws_its_exact_projected_station_span() {
    let (part, a, _) = planned("nist_stc_10_asme1_ap242-e2");
    for (stations, view) in [
        ([[0.0, 0.0, 0.0], [12.0, 0.0, 16.0]], "v:front"),
        ([[0.0, 0.0, 0.0], [12.0, 16.0, 0.0]], "v:plan"),
        ([[0.0, 10.0, 5.0], [0.001, -2.0, 21.0]], "v:side"),
    ] {
        let mut a = a.clone();
        let Some(Reading::Read(report)) = &mut a.pmi else {
            unreachable!()
        };
        let record = report
            .records
            .iter_mut()
            .find(|r| {
                r.category == Category::Dimension && r.kind == "linear" && r.blockers.is_empty()
            })
            .unwrap();
        let source = record.source_id.clone();
        record.value = Some(20.0);
        record.label = "20 ±0.2".into();
        let g = record.geometry.as_mut().unwrap();
        g.axis = None;
        g.view = None;
        g.stations = stations.to_vec();
        g.reasons.clear();
        let id = match &a.pmi {
            Some(Reading::Read(r)) => r.identities().ids[&source].clone(),
            _ => unreachable!(),
        };
        for places in [None, Some(3)] {
            let mut spec = Spec::default();
            if let Some(places) = places {
                spec.precision.insert(format!("d:{id}/linear"), places);
            }
            let plan = model::plan(&a, &spec).unwrap();
            let d = plan
                .dimensions
                .iter()
                .find(|d| d.measurements.iter().any(|m| m.feature == id))
                .unwrap();
            assert_eq!(d.view, view);
            assert_eq!(
                d.label,
                if places.is_some() {
                    "20.000 ±0.2"
                } else {
                    "20 ±0.2"
                }
            );
            let p = d.placed.as_ref().unwrap();
            // The drawing border is the frame.
            let margin = draftwright::layout::FRAME;
            assert!(
                p.label_box[0] >= margin
                    && p.label_box[1] >= margin
                    && p.label_box[2] <= plan.sheet.page[0] - margin
                    && p.label_box[3] <= plan.sheet.page[1] - margin,
                "{view}: aligned label must remain inside the drawing border: {:?}",
                p.label_box
            );
            let footprint_issues: Vec<_> = plan
                .problems
                .iter()
                .filter(|p| {
                    matches!(
                        p.code.as_str(),
                        "annotation_off_sheet" | "annotation_outside_area"
                    ) && p.subjects.contains(&d.id)
                })
                .collect();
            assert!(
                footprint_issues.is_empty(),
                "{view}: aligned ink must fit its reserved area: {:?}",
                footprint_issues
            );
            let scale = plan.views.iter().find(|v| v.id == view).unwrap().scale;
            let tips = [p.arrows[0][0], p.arrows[1][0]];
            let delta = [tips[1][0] - tips[0][0], tips[1][1] - tips[0][1]];
            for q in p.lines[2] {
                assert!(
                    ((q[0] - tips[0][0]) * delta[1] - (q[1] - tips[0][1]) * delta[0]).abs() < 1e-6,
                    "{view}: the dimension line must remain collinear with its arrow tips"
                );
            }
            assert!(
                ((tips[1][0] - tips[0][0]).hypot(tips[1][1] - tips[0][1]) / scale - 20.0).abs()
                    < 1e-6
            );
            assert!(
                (tips[1][0] - tips[0][0]).abs() > 1e-6 && (tips[1][1] - tips[0][1]).abs() > 1e-6
            );
            let font =
                draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT))
                    .unwrap();
            let ink = draftwright::dims::label_ink(&d.terms, &font);
            let span = delta[0].hypot(delta[1]);
            let along = ((p.label_at[0] - tips[0][0]) * delta[0]
                + (p.label_at[1] - tips[0][1]) * delta[1])
                / span;
            assert!(
                (along + 0.5 * (ink.min[0] + ink.max[0]) - 0.5 * span).abs() < 1e-6,
                "{view}: Python's aligned dimension centres text even with outside arrows"
            );
            let drawing = model::render(&part, &a, &plan, &font).unwrap();
            assert!(
                !draftwright::lint::structural::check(&drawing)
                    .iter()
                    .any(|i| i.code == "label_vs_measured"
                        && i.annotation.as_deref() == Some(d.id.as_str()))
            );
        }
    }
}

#[test]
fn grm03_draws_all_ten_derived_dimensions_with_their_requirements_once() {
    let (part, a, plan) = planned(support::DERIVED_PMI_FILE.trim_end_matches(".step"));
    let expected = [
        (841, 4.0),
        (877, 6.0),
        (914, 10.0),
        (958, 5.0),
        (994, 1.6),
        (1040, 3.2),
        (1086, 0.5),
        (1132, 2.0),
        (1174, 3.0),
        (1216, 20.0),
    ];
    let dimensions: Vec<_> = records(&a)
        .iter()
        .filter(|r| r.category == Category::Dimension)
        .collect();
    assert_eq!(dimensions.len(), 10);
    for (id, value) in expected {
        let r = dimensions.iter().find(|r| r.part21_id == id).unwrap();
        assert_eq!(r.value, Some(value));
        assert!(r.blockers.is_empty(), "{r:?}");
        let g = r.geometry.as_ref().unwrap();
        assert!(g.reasons.is_empty(), "{r:?}");
        assert!(g.targets.iter().all(Option::is_some), "{r:?}");
        let d = stating(&plan, &a, r).unwrap_or_else(|| panic!("{r:?}\n{:#?}", plan.problems));
        assert!(d.placed.is_some(), "{d:?}");
        assert_eq!(d.value, value);
        let identity = item_id(&a, r).unwrap();
        assert_eq!(
            plan.dimensions
                .iter()
                .filter(|d| d.measurements.iter().any(|m| m.feature == identity))
                .count(),
            1
        );
        if id == 914 {
            assert_eq!(d.label, "ø10 MAX SEE MFG 2");
        }
        if r.upper_tol.is_some() || r.lower_tol.is_some() {
            assert!(
                d.label.contains('±') || d.label.contains('+') || d.label.contains('-'),
                "{}",
                d.label
            );
        }
        // A geometrical fallback for a claimed size would repeat the source's information.
        assert!(
            !plan
                .dimensions
                .iter()
                .any(|other| other.id != d.id && other.value == value && other.kind == d.kind),
            "duplicate {d:?}"
        );
    }
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let issues = draftwright::lint::pmi_coverage::coverage(&a, &draftwright::lint::marks(&drawing));
    // The original file also has three unsupported manufacturing notes. Keep those
    // exact refusals while requiring every dimension and datum to reach actual ink.
    let refused: Vec<_> = issues
        .iter()
        .filter(|i| i.code == "pmi_not_lowered")
        .flat_map(|i| i.sources.iter().map(String::as_str))
        .collect();
    assert_eq!(
        refused,
        [
            "manufacturing_requirement:#2020",
            "manufacturing_requirement:#2024",
            "manufacturing_requirement:#2028"
        ]
    );
    assert!(
        !issues
            .iter()
            .any(|i| ["pmi_not_rendered", "pmi_value_mismatch"].contains(&i.code.as_str())),
        "{issues:?}"
    );
    assert_eq!(plan.sheet.size, "A4");
    assert!(
        !plan
            .dimensions
            .iter()
            .any(|d| d.id.ends_with("/step_position.length")),
        "the complete source chain owns the profile lengths"
    );
}

#[test]
fn derived_dimension_lint_checks_geometry_and_maximum_independently_of_claims() {
    let (_, mut a, plan) = planned("GRM-03_thumbwheel_drive_screw_AP242_PMI");
    let mut ink = marks(&plan);
    let Some(Reading::Read(report)) = &mut a.pmi else {
        panic!("PMI");
    };
    let r = report
        .records
        .iter_mut()
        .find(|r| r.part21_id == 914)
        .unwrap();
    let source = r.source_id.clone();
    r.geometry.as_mut().unwrap().targets[0]
        .as_mut()
        .unwrap()
        .radius = Some(6.0);
    let issues = draftwright::lint::pmi_coverage::coverage(&a, &ink);
    assert!(
        issues
            .iter()
            .any(|i| i.code == "pmi_value_mismatch" && i.sources.contains(&source)),
        "{issues:?}"
    );
    let Some(Reading::Read(report)) = &mut a.pmi else {
        unreachable!()
    };
    report
        .records
        .iter_mut()
        .find(|r| r.part21_id == 914)
        .unwrap()
        .geometry
        .as_mut()
        .unwrap()
        .targets[0]
        .as_mut()
        .unwrap()
        .radius = Some(5.0);
    for m in &mut ink {
        m.text = m.text.replace(" MAX", "");
    }
    let issues = draftwright::lint::pmi_coverage::coverage(&a, &ink);
    assert!(
        issues
            .iter()
            .any(|i| i.code == "pmi_value_mismatch" && i.message.contains("MAX")),
        "{issues:?}"
    );
}

#[test]
fn derived_requirements_preserve_tolerance_bands_on_owned_and_standalone_dimensions() {
    let (_, mut a, _) = planned("GRM-03_thumbwheel_drive_screw_AP242_PMI");
    let Some(Reading::Read(report)) = &mut a.pmi else {
        panic!("PMI");
    };
    // The supplied GRM-03 has nominal dimensions and one Maximum qualifier, without
    // per-dimension semantic tolerance bands. Plant bands to exercise both ownership paths.
    for (id, label) in [
        (841, "ø4 +0.02/-0.01"),
        (1040, "3.2 +0.02/-0.01"),
        (914, "ø10 +0.02/-0.01 MAX"),
    ] {
        let r = report
            .records
            .iter_mut()
            .find(|r| r.part21_id == id)
            .unwrap();
        r.upper_tol = Some(0.02);
        r.lower_tol = Some(-0.01);
        r.label = label.into();
    }
    // A lone knurl does not use the legacy manufacturing schedule. Its raw
    // MAX AFTER KNURL must not stand in for the size's independent MAX qualifier.
    for include_threads in [true, false] {
        let mut a = a.clone();
        if !include_threads {
            let Some(Reading::Read(report)) = &mut a.pmi else {
                unreachable!()
            };
            report
                .records
                .retain(|r| r.category != Category::ManufacturingRequirement || r.kind == "knurl");
        }
        for style in ["auto", "note", "compact", "full"] {
            let spec = serde_json::from_value(serde_json::json!({
                "sheet": {"callouts": {"style": style}}
            }))
            .unwrap();
            let plan = model::plan(&a, &spec).unwrap();
            for id in [841, 1040, 914] {
                let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
                let d = stating(&plan, &a, r).unwrap();
                assert!(d.placed.is_some(), "{style}: {d:?}");
                assert!(
                    d.label.contains("+0.02") && d.label.contains("-0.01"),
                    "{style}: {d:?}"
                );
                if id == 914 {
                    assert!(d.label.contains(" MAX"), "{style}: {d:?}");
                    if style != "full" {
                        let see = d.label.find("SEE MFG").expect("knurl note reference");
                        assert!(d.label.find(" MAX").unwrap() < see, "{style}: {d:?}");
                    }
                }
            }
        }
    }
    // An incomplete chain must not hand its remaining source lengths to the hidden
    // local dimensions of the automatic cumulative baseline.
    let Some(Reading::Read(report)) = &mut a.pmi else {
        unreachable!()
    };
    report
        .records
        .iter_mut()
        .find(|r| r.part21_id == 1216)
        .unwrap()
        .blockers
        .push("planted unresolved endpoint".into());
    let partial = model::plan(&a, &Spec::default()).unwrap();
    for id in [1040, 1086, 1132, 1174] {
        let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
        assert!(
            stating(&partial, &a, r).is_some_and(|d| d.placed.is_some()),
            "source {id} must survive a partial chain"
        );
    }
}

/// #265: Specify 0.1.4 writes every size the general tolerance covers as its own nominal-only
/// dimension. They are drawn as the planners draw that geometry, not on top of it: the drawing
/// is the one the same file makes without them, and one no planned item states is planned as
/// draftwright's own. Strict mode still draws them as the file's PMI,
/// and Specify's characteristic-number history is bookkeeping.
#[test]
fn no_datum_specify_general_dimensions_are_drawn_as_planned() {
    let bytes = gunzip(
        &std::fs::read(root().join("tests/fixtures/samples/specify-0.1.4-plate.step.gz")).unwrap(),
    );
    let (part, mut a) = analysed("specify-0.1.4-plate.step", &bytes);
    // Exercise #265 independently of the newly retained datum-reference policy.
    // Only the intent metadata changes; the native geometry and other PMI stay.
    // The actual datum-owned plate is asserted separately below, and the new
    // writer's truly no-datum dimension is checked without modifying its reading.
    if let Some(Reading::Read(report)) = &mut a.pmi {
        for r in &mut report.records {
            r.fields.retain(|(key, _)| key != "reference datum");
        }
    }
    let Some(Reading::Read(report)) = &a.pmi else {
        panic!("PMI read")
    };
    let general: BTreeSet<&str> = report
        .records
        .iter()
        .filter(|r| {
            r.category == Category::Dimension
                && !r.basic
                && r.upper_tol.is_none()
                && r.lower_bound.is_none()
                && r.fit.is_none()
        })
        .map(|r| r.source_id.as_str())
        .collect();
    assert_eq!(general.len(), 10, "{general:?}");
    for s in report
        .sources
        .iter()
        .filter(|s| general.contains(s.id.as_str()))
    {
        assert!(s.reason.starts_with("Specify-written PMI"), "{s:?}");
    }
    let history = report
        .sources
        .iter()
        .find(|s| {
            s.id.starts_with("attribute_set:")
                && s.reason.contains("specify characteristic numbers")
        })
        .expect("the history set is bookkeeping");
    assert_eq!(history.outcome, draftwright::pmi::Outcome::Extracted);

    let plan = cache::default_plan(&a, true);
    let mut without = a.clone();
    if let Some(Reading::Read(r)) = &mut without.pmi {
        r.records
            .retain(|r| !general.contains(r.source_id.as_str()));
        r.sources.retain(|s| !general.contains(s.id.as_str()));
    }
    let planned = cache::default_plan(&without, true);
    let ink = |p: &Plan| {
        (
            p.sheet.size.clone(),
            p.sheet.scale.clone(),
            p.dimensions
                .iter()
                .map(|d| {
                    (
                        d.id.clone(),
                        d.label.clone(),
                        d.view.clone(),
                        d.placed.is_some(),
                    )
                })
                .collect::<Vec<_>>(),
        )
    };
    assert_eq!(ink(&plan), ink(&planned));
    for id in ["width", "depth", "height"] {
        let d = plan
            .dimensions
            .iter()
            .find(|d| d.id == format!("d:envelope/{id}.length"))
            .unwrap();
        assert!(
            d.basis.contains("Specify's general-tolerance dimension"),
            "{d:?}"
        );
    }
    assert!(
        !plan.problems.iter().any(|p| p.code == "pmi_not_extracted"),
        "{:#?}",
        plan.problems
    );
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let issues = draftwright::lint::pmi_coverage::coverage(&a, &draftwright::lint::marks(&drawing));
    assert!(
        !issues.iter().any(|i| i.code == "pmi_not_rendered"
            || (i.code == "pmi_not_extracted"
                && i.sources.iter().any(|s| s.starts_with("attribute_set:")))),
        "{issues:?}"
    );

    // A general dimension no planned item states (here the slot side's level, its faces made
    // to include one no recognised feature owns) is one more planned size: drawn as
    // draftwright's own dimension, not as PMI, with the plan otherwise as without it.
    let side = "dimension:#2273";
    let mut unmatched = a.clone();
    if let Some(Reading::Read(r)) = &mut unmatched.pmi {
        let r = r.records.iter_mut().find(|r| r.source_id == side).unwrap();
        assert!(!r.faces.contains(&26));
        r.faces.push(26);
    }
    let mut gone = a.clone();
    if let Some(Reading::Read(r)) = &mut gone.pmi {
        r.records.retain(|r| r.source_id != side);
    }
    let (drawn, rest) = (
        cache::default_plan(&unmatched, true),
        cache::default_plan(&gone, true),
    );
    let extra: Vec<_> = drawn
        .dimensions
        .iter()
        .filter(|d| !rest.dimensions.iter().any(|e| e.id == d.id))
        .collect();
    let [d] = extra.as_slice() else {
        panic!("{extra:#?}")
    };
    assert!(d.id.starts_with("d:specify_general:"), "{d:?}");
    assert_eq!(d.label, "17");
    assert!(d.placed.is_some(), "{d:?}");
    assert!(
        d.measurements
            .iter()
            .all(|m| !m.feature.starts_with("pmi:")),
        "{d:?}"
    );
    assert!(
        d.basis.contains(side) && d.basis.contains("Specify-written PMI"),
        "{d:?}"
    );
    let others = |p: &Plan| {
        (
            p.sheet.size.clone(),
            p.sheet.scale.clone(),
            p.dimensions
                .iter()
                .filter(|e| !e.id.starts_with("d:specify_general:"))
                .map(|e| {
                    (
                        e.id.clone(),
                        e.label.clone(),
                        e.view.clone(),
                        e.placed.is_some(),
                    )
                })
                .collect::<Vec<_>>(),
        )
    };
    assert_eq!(others(&drawn), others(&rest));

    let strict = Spec {
        pmi: model::PmiMode::Strict,
        ..Spec::default()
    };
    let plan = model::plan(&a, &strict).unwrap();
    let ids = report.identities();
    for s in &general {
        let id = ids.id(s).unwrap();
        assert!(
            plan.dimensions
                .iter()
                .any(|d| d.measurements.iter().any(|m| m.feature == id)),
            "strict mode draws {s}"
        );
    }
}

/// These ten old-writer general locations all reference A/B/C. The datum rule
/// intentionally supersedes the former all-general #265 expectation for this part.
#[test]
fn specify_plate_general_locations_keep_their_datums() {
    let bytes = gunzip(
        &std::fs::read(root().join("tests/fixtures/samples/specify-0.1.4-plate.step.gz")).unwrap(),
    );
    let (_, a) = analysed("specify-0.1.4-plate.step", &bytes);
    let general: Vec<_> = records(&a)
        .iter()
        .filter(|r| {
            r.category == Category::Dimension
                && !r.basic
                && r.upper_tol.is_none()
                && r.lower_bound.is_none()
                && r.fit.is_none()
        })
        .collect();
    assert_eq!(general.len(), 10);
    let plan = cache::default_plan(&a, true);
    assert_eq!(
        plan.sheet.size, "A2",
        "the authored datum baseline flood is retained"
    );
    assert_eq!(plan.sheet.scale, "1:1");
    assert_eq!(
        plan.dimensions
            .iter()
            .filter(|d| d.placed.is_some())
            .count(),
        24
    );
    for r in general {
        assert!(r.fields.iter().any(|(key, _)| key == "reference datum"));
        let d = stating(&plan, &a, r).unwrap();
        assert!(d.placed.is_some(), "{d:?}");
        assert_eq!(Some(d.value), r.value);
    }
    assert!(!plan.problems.iter().any(|p| p.code == "pmi_not_extracted"));
}

#[test]
fn datum_general_dimensions_keep_their_authored_baselines_and_characteristics() {
    let bytes = gunzip(&std::fs::read(fixtures().join("spindle-coplanar-groups.step.gz")).unwrap());
    let (_, a) = analysed("spindle-coplanar-groups.step", &bytes);
    let plan = cache::default_plan(&a, true);
    for (id, value) in [(1405, 33.6), (1409, 27.2)] {
        let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
        assert!(r.fields.contains(&(
            "reference datum".into(),
            draftwright::pmi::Field::Text("B".into())
        )));
        assert_eq!(r.face_groups[0], vec![7, 11]);
        let g = r.geometry.as_ref().unwrap();
        assert_eq!(g.axis.as_deref(), Some("X"));
        assert!((g.stations[0][0] - 0.).abs() < 1e-6);
        assert!((g.stations[1][0] - value).abs() < 1e-6);
        let d = stating(&plan, &a, r).unwrap();
        assert_eq!(d.value, value);
        assert!(d.placed.is_some(), "{d:?}");
        assert!(d.id.starts_with("d:pmi:"));
        let c = plan
            .characteristics
            .iter()
            .find(|c| c.source == r.source_id)
            .unwrap();
        assert_eq!(c.drawn_item.as_deref(), Some(d.id.as_str()));
        if id == 1409 {
            assert_eq!(c.number, "8");
        }
    }
    assert!(
        !plan
            .dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.id == "d:f:plate:f3/thickness.length")
    );
    assert!(
        !plan
            .dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.id == "d:f:slot:f4/location_slot.length")
    );
    assert!(
        !plan
            .dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.id == "d:f:slot:f4/slot_length.length")
    );
    assert!(
        !plan.problems.iter().any(|p| p.code == "feature_not_located"
            && p.subjects.iter().any(|s| s == "f:slot:f4")
            && p.message.contains(" on x:")),
        "the authored baseline locates the slot end: {:?}",
        plan.problems
    );
    // Every numbered source is carried by ink stating its own quantity, never an
    // unrelated engine envelope reached through a coverage chain.
    for c in &plan.characteristics {
        let r = records(&a)
            .iter()
            .find(|r| r.source_id == c.source)
            .unwrap();
        let item = c
            .drawn_item
            .as_ref()
            .expect("every spindle characteristic draws");
        if r.category == Category::Dimension {
            let d = plan.dimensions.iter().find(|d| &d.id == item).unwrap();
            assert!((d.value - r.value.unwrap()).abs() < 1e-6, "{c:?}: {d:?}");
        } else if r.category == Category::Datum || r.category == Category::GeometricTolerance {
            assert!(
                plan.gdt
                    .iter()
                    .any(|g| &g.id == item && g.source == r.source_id),
                "{c:?}"
            );
        } else {
            assert!(
                plan.requirements
                    .iter()
                    .any(|g| &g.id == item && g.source == r.source_id)
                    || plan
                        .dimensions
                        .iter()
                        .any(|d| &d.id == item && d.basis.contains(&r.source_id)),
                "{c:?}"
            );
        }
    }
}

#[test]
fn specify_origin_is_dimension_owned_and_controls_general_replanning() {
    let bytes =
        gunzip(&std::fs::read(fixtures().join("specify-main-GRM-02_shaft.step.gz")).unwrap());
    let (_, a) = analysed("specify-main-GRM-02_shaft.step", &bytes);
    let Some(Reading::Read(report)) = &a.pmi else {
        panic!("PMI")
    };
    assert!(
        !report
            .sources
            .iter()
            .any(|s| s.reason.contains("specify pmi origin")
                && s.outcome == draftwright::pmi::Outcome::NotExtracted)
    );
    let plan = cache::default_plan(&a, true);
    for (id, value) in [(1274, 33.6), (1281, 27.2)] {
        let r = report.records.iter().find(|r| r.part21_id == id).unwrap();
        assert!(r.fields.contains(&(
            "specify origin".into(),
            draftwright::pmi::Field::Text("general_tolerance".into())
        )));
        assert!(r.fields.contains(&(
            "reference datum".into(),
            draftwright::pmi::Field::Text("B".into())
        )));
        let d = stating(&plan, &a, r).unwrap();
        assert_eq!(d.value, value);
        assert!(d.placed.is_some());
    }
    // Same no-datum nominal, only its explicitly stored origin differs.
    let r = report.records.iter().find(|r| r.part21_id == 1256).unwrap();
    assert!(!r.fields.iter().any(|(k, _)| k == "reference datum"));
    let mut requirement = a.clone();
    if let Some(Reading::Read(report)) = &mut requirement.pmi {
        let r = report
            .records
            .iter_mut()
            .find(|r| r.part21_id == 1256)
            .unwrap();
        r.fields.retain(|(k, _)| k != "specify origin");
        r.fields.push((
            "specify origin".into(),
            draftwright::pmi::Field::Text("requirement".into()),
        ));
    }
    let authored = cache::default_plan(&requirement, true);
    let rr = records(&requirement)
        .iter()
        .find(|r| r.part21_id == 1256)
        .unwrap();
    assert!(stating(&authored, &requirement, rr).is_some_and(|d| d.placed.is_some()));
    assert!(
        stating(&plan, &a, r).is_none(),
        "a no-datum general dimension keeps #265"
    );
}

#[test]
fn compound_bore_basics_keep_their_physical_axis_datums_and_boxes() {
    let bytes =
        gunzip(&std::fs::read(fixtures().join("specify-017-mounting-plate.step.gz")).unwrap());
    let (part, a) = analysed("specify-017-mounting-plate.step", &bytes);
    let plan = cache::default_plan(&a, true);
    for (id, value, datum, axis, start) in [
        (2830, 50., "C", "X", [-50., 0., 4.]),
        (2837, 35., "B", "Y", [0., -35., 4.]),
    ] {
        let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
        assert!(r.basic);
        assert!(r.fields.iter().any(|(key, field)| {
            key == "reference datum" && *field == draftwright::pmi::Field::Text(datum.into())
        }));
        assert_eq!(
            r.face_groups.iter().map(Vec::len).collect::<Vec<_>>(),
            [1, 5]
        );
        let g = r.geometry.as_ref().unwrap();
        assert!(g.reasons.is_empty(), "{g:?}");
        assert_eq!(g.axis.as_deref(), Some(axis));
        assert_eq!(g.view.as_deref(), Some("plan"));
        assert_eq!(g.stations, [start, [0., 0., 4.]]);
        // Independent native support: the group centre is the physical Ø28
        // bore's axis, not an invented centroid between four empty holes.
        let bore = records(&a)
            .iter()
            .find(|r| r.kind == "diameter" && r.value == Some(28.))
            .unwrap();
        let cy = &bore.geometry.as_ref().unwrap().cylinders[0];
        assert_eq!(cy.radius, 14.);
        let q = g.stations[1];
        let t: f64 = (0..3)
            .map(|k| (q[k] - cy.origin[k]) * cy.direction[k])
            .sum();
        assert!(cy.interval[0] <= t && t <= cy.interval[1]);
        assert!((0..3).all(|k| (q[k] - cy.origin[k] - t * cy.direction[k]).abs() < 1e-6));
        assert!(bore.faces.iter().all(|f| r.face_groups[1].contains(f)));
        let plane = &part.faces[r.face_groups[0][0]].surface;
        let (u, v) = plane.parameters(start, None).unwrap();
        assert!(part.domain(r.face_groups[0][0]).unwrap().contains(u, v));
        assert!((0..3).all(|k| (plane.value(u, v)[k] - start[k]).abs() < 1e-6));
        let d = stating(&plan, &a, r).expect("the authored basic dimension draws");
        assert!(d.placed.is_some());
        assert_eq!(d.value, value);
        assert!(
            d.measurements
                .iter()
                .any(|m| m.parameter.ends_with(".basic"))
        );
        assert_eq!(
            plan.dimensions
                .iter()
                .filter(|d| d.placed.is_some()
                    && d.kind == "linear"
                    && (d.value - value).abs() < 1e-6)
                .count(),
            1,
            "engine centre locations must yield to the authored boxed dimension"
        );
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "pmi_not_lowered" && p.refs.contains(&r.source_id))
        );
    }
    let position = records(&a)
        .iter()
        .find(|r| r.kind == "position" && r.value == Some(4.))
        .unwrap();
    assert!(
        plan.gdt
            .iter()
            .any(|g| g.source == position.source_id && g.placed.is_some())
    );
}

#[test]
fn compound_bore_locations_still_require_symmetry_and_matching_nominal() {
    let bytes =
        gunzip(&std::fs::read(fixtures().join("specify-017-mounting-plate.step.gz")).unwrap());
    let (part, a) = analysed("specify-017-mounting-plate.step", &bytes);
    for id in [2830, 2837] {
        let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
        let centre = records(&a)
            .iter()
            .find(|r| r.kind == "diameter" && r.value == Some(28.))
            .unwrap()
            .faces[0];
        // Reflection proves only the measured coordinate. An empty centre is
        // acceptable along that axis; it does not assert a physical central bore.
        let mut symmetric = r.clone();
        symmetric.face_groups[1].retain(|f| *f != centre);
        let g = draftwright::pmi_lowering::dimension_geometry(&part, &symmetric);
        let k = if id == 2830 { 0 } else { 1 };
        assert!(g.reasons.is_empty(), "{g:?}");
        assert_eq!(g.axis.as_deref(), Some(if k == 0 { "X" } else { "Y" }));
        assert_eq!(g.stations[1][k], 0.);
        assert_eq!(
            (g.stations[1][k] - g.stations[0][k]).abs(),
            r.value.unwrap()
        );

        let removed = r.face_groups[1]
            .iter()
            .copied()
            .find(|f| *f != centre)
            .unwrap();
        let mut unproven = r.clone();
        unproven.face_groups[1].retain(|f| *f != removed);
        let g = draftwright::pmi_lowering::dimension_geometry(&part, &unproven);
        assert!(
            g.reasons
                .iter()
                .any(|s| s.contains("no proven face-supported axis witness")),
            "an asymmetric group is still unproven: {g:?}"
        );
        let mut inconsistent = a.clone();
        if let Some(Reading::Read(report)) = &mut inconsistent.pmi {
            let wrong = report
                .records
                .iter_mut()
                .find(|r| r.part21_id == id)
                .unwrap();
            wrong.value = wrong.value.map(|v| v + 1.);
            wrong.geometry = Some(draftwright::pmi_lowering::dimension_geometry(&part, wrong));
            assert!(
                wrong
                    .geometry
                    .as_ref()
                    .unwrap()
                    .reasons
                    .iter()
                    .any(|s| s.contains("differs from nominal"))
            );
        }
        let refused = cache::default_plan(&inconsistent, true);
        assert!(
            refused
                .problems
                .iter()
                .any(|p| p.code == "pmi_not_lowered" && p.refs.contains(&r.source_id))
        );
    }
}

#[test]
fn symmetric_empty_centre_enclosure_locations_lower_once_along_the_datum_normal() {
    let bytes =
        gunzip(&std::fs::read(fixtures().join("electronics-enclosure-base-pmi.step.gz")).unwrap());
    let (part, a) = analysed("electronics-enclosure-base-pmi.step", &bytes);
    let plan = cache::default_plan(&a, true);
    for (id, k, axis, value) in [(1830, 0, "X", 50.), (1835, 1, "Y", 35.)] {
        let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
        assert_eq!(
            r.face_groups.iter().map(Vec::len).collect::<Vec<_>>(),
            [1, 4]
        );
        let g = r.geometry.as_ref().unwrap();
        assert!(g.reasons.is_empty(), "{g:?}");
        assert_eq!(g.axis.as_deref(), Some(axis));
        assert_eq!(g.stations.len(), 2);
        assert_eq!(g.stations[0][k], -value);
        assert_eq!(g.stations[1][k], 0.);
        let axes: Vec<_> = r.face_groups[1]
            .iter()
            .map(|&f| {
                let draftwright::haecceity::geom::Surface::Cylinder { frame, .. } =
                    &part.faces[f].surface
                else {
                    panic!("the pattern member must be cylindrical");
                };
                [frame.origin[0], frame.origin[1]]
            })
            .collect();
        for expected in [[-35., -25.], [-35., 25.], [35., -25.], [35., 25.]] {
            assert!(axes.contains(&expected), "{axes:?}");
        }
        let d = stating(&plan, &a, r).expect("the pattern centre location lowers");
        assert!(d.placed.is_some(), "{d:?}");
        assert_eq!(d.value, value);
        let pattern = a
            .recognition
            .as_ref()
            .unwrap()
            .patterns
            .iter()
            .find(|p| {
                let bores: Vec<_> = p
                    .faces
                    .iter()
                    .filter(|&&f| {
                        matches!(
                            part.faces[f].surface,
                            draftwright::haecceity::geom::Surface::Cylinder { .. }
                        )
                    })
                    .collect();
                bores.len() == r.face_groups[1].len()
                    && bores.iter().all(|f| r.face_groups[1].contains(f))
            })
            .expect("the four bores form the planner's pattern");
        assert!(
            !plan.problems.iter().any(|p| p.code == "feature_not_located"
                && p.subjects.iter().any(|s| pattern.members.contains(s))),
            "the authored centre locations and native pitches must locate every pattern member"
        );
        let parameter = format!("location.length.{}", axis.to_lowercase());
        let pmi = item_id(&a, r).unwrap();
        assert_eq!(
            plan.dimensions
                .iter()
                .filter(|d| d.placed.is_some()
                    && d.measurements.iter().any(|m| m.feature == pmi
                        || (m.feature == pattern.id && m.parameter == parameter)))
                .count(),
            1,
            "the authored centre location must merge with or replace the planner's location"
        );
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "pmi_not_lowered" && p.refs.contains(&r.source_id))
        );

        let mut wrong_nominal = r.clone();
        wrong_nominal.value = Some(value + 1.);
        let wrong = draftwright::pmi_lowering::dimension_geometry(&part, &wrong_nominal);
        assert_eq!(
            wrong.refusal,
            Some(draftwright::pmi::GeometryRefusal::NominalMismatch {
                measured: value,
                nominal: value + 1.,
            })
        );
        assert!(
            wrong
                .reasons
                .iter()
                .any(|s| s.contains("differs from nominal")),
            "{wrong:?}"
        );

        // Removing a corner must still refuse the location. Its diagnostic
        // measures along the datum normal, even though the boxes differ in Z.
        let mut asymmetric = r.clone();
        asymmetric.face_groups[1].pop();
        asymmetric.value = Some(value + 1.);
        let refused = draftwright::pmi_lowering::dimension_geometry(&part, &asymmetric);
        assert!(
            refused
                .reasons
                .iter()
                .any(|s| s.contains("no proven face-supported axis witness")),
            "{refused:?}"
        );
        assert_eq!(
            refused.refusal,
            Some(draftwright::pmi::GeometryRefusal::NominalMismatch {
                measured: value,
                nominal: value + 1.,
            })
        );
    }
}

#[test]
fn specify_main_plane_and_axis_locations_use_geometric_directions() {
    for (name, ids) in [
        ("angle-bracket", vec![753, 774]),
        (
            "nema-17-bracket",
            vec![
                3431, 3445, 3459, 3473, 3533, 3539, 3545, 3551, 3563, 3573, 3579, 3583,
            ],
        ),
        ("sk08", vec![1691, 1705, 1804]),
    ] {
        let bytes = gunzip(
            &std::fs::read(fixtures().join(format!("specify-main-{name}.step.gz"))).unwrap(),
        );
        let (part, a) = analysed(&format!("specify-main-{name}.step"), &bytes);
        for id in ids {
            let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
            let g = r.geometry.as_ref().unwrap();
            assert!(
                g.reasons.is_empty(),
                "{name} {}: {:?}",
                r.source_id,
                g.reasons
            );
            let mut wrong = r.clone();
            wrong.value = r.value.map(|v| v + 1.);
            let bad = draftwright::pmi_lowering::dimension_geometry(&part, &wrong);
            assert!(
                bad.reasons
                    .iter()
                    .any(|s| s.contains("differs from nominal")),
                "{name}: {bad:?}"
            );
        }
    }
}

#[test]
fn an_inclined_bore_keeps_its_h11_fit_on_its_proven_callout() {
    let bytes =
        gunzip(&std::fs::read(fixtures().join("specify-main-angle-bracket.step.gz")).unwrap());
    let (_, a) = analysed("specify-main-angle-bracket.step", &bytes);
    let r = records(&a).iter().find(|r| r.part21_id == 745).unwrap();
    assert_eq!(r.fit.as_deref(), Some("H11"));
    assert!(r.blockers.is_empty(), "{:?}", r.blockers);
    assert!(
        r.geometry
            .as_ref()
            .unwrap()
            .cylinders
            .iter()
            .all(|c| c.principal().is_none())
    );
    let plan = cache::default_plan(&a, true);
    let d = stating(&plan, &a, r).unwrap();
    assert_eq!(d.id, "d:f:hole:f9/bore.diameter");
    assert!(d.placed.is_some());
    assert_eq!(d.value, 10.);
    assert!(d.label.contains("H11"), "{d:?}");
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "pmi_not_lowered" && p.refs.contains(&r.source_id))
    );
    assert_eq!(
        plan.dimensions
            .iter()
            .filter(|d| d.placed.is_some()
                && d.measurements
                    .iter()
                    .any(|m| m.feature == "f:hole:f9" && m.parameter == "bore.diameter"))
            .count(),
        1
    );
}

#[test]
fn recovered_thicknesses_leave_the_bend_angle_defined() {
    let bytes =
        gunzip(&std::fs::read(fixtures().join("specify-main-angle-bracket.step.gz")).unwrap());
    let (_, a) = analysed("specify-main-angle-bracket.step", &bytes);
    let plan = cache::default_plan(&a, true);
    // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A4", "1:1")
    );
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.kind == "angular" && (d.value - 120.).abs() < 1e-6 && d.placed.is_some())
    );
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "angular_dimension_dropped")
    );
    for id in [770, 774] {
        let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
        let c = plan
            .characteristics
            .iter()
            .find(|c| c.source == r.source_id)
            .unwrap();
        let d = plan
            .dimensions
            .iter()
            .find(|d| Some(&d.id) == c.drawn_item.as_ref())
            .unwrap();
        assert_eq!(d.value, 8.);
        let p = d.placed.as_ref().expect("both authored thicknesses draw");
        // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
        for [a, b] in p.lines[..2].iter().filter(|_| id == 774) {
            assert_eq!(
                ((a[0] - b[0]).hypot(a[1] - b[1]) * 1_000_000.).round() as i64,
                31_464_102,
                "{d:?}"
            );
        }
        assert!(!plan.problems.iter().any(|p| {
            ["annotation_ink_overlap", "dimension_line_crossing"].contains(&p.code.as_str())
                && p.subjects.contains(&d.id)
        }));
    }
}

#[test]
fn authored_slot_ends_preserve_existing_engine_location_proofs() {
    for (file, lost) in [
        (
            "nist_ctc_01_asme1_ap242-e1.stp",
            vec!["f:slot:1", "f:slot:2"],
        ),
        ("nist_ctc_01_merge.step", vec!["f:slot:1", "f:slot:2"]),
        ("nist_stc_09_asme1_ap242-e4.stp", vec!["f:slot:f102"]),
    ] {
        let bytes =
            gunzip(&std::fs::read(fixtures().join("step").join(format!("{file}.gz"))).unwrap());
        let (_, a) = analysed(file, &bytes);
        let plan = cache::default_plan(&a, true);
        for id in lost {
            assert!(
                !plan.problems.iter().any(|p| p.code == "feature_not_located"
                    && p.subjects.iter().any(|s| s == id)
                    && p.message.contains(" on x:")),
                "{file}: {id} lost its longitudinal locator: {:?}",
                plan.problems
            );
        }
    }
}

#[test]
fn valueless_ctc01_dimensions_use_proven_geometry_and_keep_their_carriers() {
    let file = "nist_ctc_01_asme1_ap242-e1.stp";
    let bytes = gunzip(&std::fs::read(fixtures().join("step").join(format!("{file}.gz"))).unwrap());
    let (part, a) = analysed(file, &bytes);
    let plan = cache::default_plan(&a, true);
    let report = match a.pmi.as_ref().unwrap() {
        Reading::Read(report) => report,
        _ => panic!("PMI is read"),
    };
    for (id, value) in [(24, 85.), (25, 50.), (126, 35.), (127, 35.)] {
        let r = records(&a).iter().find(|r| r.part21_id == id).unwrap();
        assert_eq!(
            r.value,
            Some(value),
            "measured source values discard numerical noise: {r:?}"
        );
        assert!(r.fields.iter().any(|(key, field)| key == "nominal source"
            && *field == draftwright::pmi::Field::Text("geometry (no stated value)".into())));
        assert!(r.blockers.is_empty());
        assert!(r.geometry.as_ref().unwrap().reasons.is_empty());
        assert!(
            r.lower_tol.is_none()
                && r.upper_tol.is_none()
                && r.lower_bound.is_none()
                && r.upper_bound.is_none()
        );
        let d = stating(&plan, &a, r).expect("measured PMI retains a drawn carrier");
        assert!(d.placed.is_some());
        let facts: Vec<_> = plan
            .problems
            .iter()
            .filter(|p| p.code == "pmi_read_fact" && p.refs.contains(&r.source_id))
            .collect();
        assert_eq!(facts.len(), 1);
        assert_eq!(facts[0].severity, "info");
        assert!(facts[0].message.contains("was measured"));
        assert_eq!(json!(facts[0])["impact"], "advisory");
        assert_eq!(json!(facts[0])["group"], "pmi_read_fact:dimension");
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "pmi_not_lowered" && p.refs.contains(&r.source_id))
        );
    }
    // Each measured diameter adds provenance to the already toleranced size,
    // preserving the original requirement and its single callout.
    for (measured, stated) in [(126, 120), (127, 121)] {
        let by_id = |id| records(&a).iter().find(|r| r.part21_id == id).unwrap();
        assert_eq!(
            stating(&plan, &a, by_id(measured)).unwrap().id,
            stating(&plan, &a, by_id(stated)).unwrap().id
        );
        assert!(
            report
                .identities()
                .id(&by_id(measured).source_id)
                .unwrap()
                .ends_with(":t="),
            "the measured fallback keeps the source's original empty-label identity"
        );
    }
    let wall = records(&a).iter().find(|r| r.part21_id == 25).unwrap();
    let d = stating(&plan, &a, wall).unwrap();
    assert!(
        d.measurements
            .iter()
            .any(|m| m.feature == "f:slot:f82" && m.parameter == "slot_width.length")
    );
    let mut numbered = a.clone();
    if let Some(Reading::Read(report)) = &mut numbered.pmi {
        for r in report
            .records
            .iter_mut()
            .filter(|r| [24, 25, 126, 127].contains(&r.part21_id))
        {
            r.fields.push((
                "id".into(),
                draftwright::pmi::Field::Text(format!("{}", r.part21_id)),
            ));
        }
    }
    let numbered_plan = cache::default_plan(&numbered, true);
    for id in [24, 25, 126, 127] {
        let r = records(&numbered)
            .iter()
            .find(|r| r.part21_id == id)
            .unwrap();
        let carrier = stating(&numbered_plan, &numbered, r).unwrap();
        let characteristic = numbered_plan
            .characteristics
            .iter()
            .find(|c| c.source == r.source_id)
            .unwrap();
        assert_eq!(characteristic.number, format!("{id}"));
        assert_eq!(
            characteristic.drawn_item.as_deref(),
            Some(carrier.id.as_str())
        );
    }
    let notches: Vec<_> = a
        .features
        .iter()
        .filter(|f| f.kind == "rectangular_through_notch")
        .collect();
    assert_eq!(notches.len(), 2);
    let carriers: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.placed.is_some()
                && d.measurements.iter().any(|m| {
                    m.feature.starts_with("f:rectangular_through_notch:")
                        && m.parameter == "notch_width.length"
                })
        })
        .collect();
    assert_eq!(carriers.len(), 2);
    assert!(
        carriers
            .iter()
            .all(|d| d.kind == "linear" && d.label == "85")
    );
    let authored = records(&a).iter().find(|r| r.part21_id == 24).unwrap();
    let width = stating(&plan, &a, authored).unwrap();
    assert_eq!(width.kind, "linear");
    assert_eq!(width.label, "85");
    let placed = width.placed.as_ref().unwrap();
    let view = plan.views.iter().find(|v| v.id == width.view).unwrap();
    assert!(
        ((placed.lines[0][0][0] - placed.lines[1][0][0]).abs() / view.scale - 85.).abs() < 1e-6
    );
    assert!(
        plan.notes
            .iter()
            .all(|n| !n.id.contains("rectangular_through_notch"))
    );
    // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
    assert_eq!(plan.sheet.size, "A1");
    assert_eq!(plan.sheet.scale, "1:2");
    let locators: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.placed.is_some()
                && d.measurements.iter().any(|m| {
                    m.feature.starts_with("f:rectangular_through_notch:")
                        && m.parameter.starts_with("location.")
                })
        })
        .collect();
    assert_eq!(locators.len(), 3);
    // Baselines with a common origin must remain separate readable rows,
    // including when their labels occupy different X spans.
    let x_locations: Vec<_> = locators
        .iter()
        .filter(|d| d.placed.as_ref().unwrap().rotation == 0.)
        .collect();
    assert_eq!(x_locations.len(), 2);
    let row = |d: &&draftwright::dims::PlannedDimension| d.placed.as_ref().unwrap().lines[2][0][1];
    assert!((row(x_locations[0]) - row(x_locations[1])).abs() >= 3.);
    for value in [207.5, 507.5, 350.] {
        assert!(
            locators
                .iter()
                .any(|d| d.kind == "linear" && (d.value - value).abs() < 1e-6)
        );
    }
    // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
    let notch_contacts: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| {
            p.code == "dimension_line_crossing"
                || (matches!(
                    p.code.as_str(),
                    "extension_through_feature"
                        | "label_crowded"
                        | "annotation_overlap"
                        | "annotation_ink_overlap"
                ) && p
                    .subjects
                    .iter()
                    .any(|s| s.contains("rectangular_through_notch")))
        })
        .map(|p| {
            (
                p.code.as_str(),
                p.subjects.iter().map(String::as_str).collect::<Vec<_>>(),
            )
        })
        .collect();
    assert_eq!(
        notch_contacts,
        vec![(
            "label_crowded",
            vec![
                "d:f:rectangular_through_notch:f37/location.length.x",
                "d:f:hole_pattern:f0/grid_pitch.length.row"
            ]
        )]
    );
    for (code, count) in [
        ("extension_through_feature", 8),
        ("label_crowded", 1),
        ("annotation_leader_crossing", 1),
        ("leader_across_view", 0),
    ] {
        assert_eq!(
            plan.problems.iter().filter(|p| p.code == code).count(),
            count,
            "{code}: {:?}",
            plan.problems
        );
    }

    for f in notches {
        for (role, value) in [("notch_width.length", 85.), ("notch_depth.length", 25.)] {
            assert!(plan.dimensions.iter().any(|d| d.placed.is_some()
                && d.measurements.iter().any(|m| m.feature == f.id
                    && m.parameter == role
                    && (m.value - value).abs() < 1e-6)));
        }
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "feature_not_located" && p.subjects.contains(&f.id))
        );
    }
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let lint = draftwright::lint::lint(&a, &drawing);
    assert!(!lint.iter().any(|p| {
        p.code == "duplicate_measurement"
            && p.subjects
                .iter()
                .any(|id| id.starts_with("f:rectangular_through_notch:"))
    }));
    // Ordinary datum-based dimensions are verified from their actual witness
    // ink and drawing frame, including the printed coordinate origin.
    for id in [
        "f:slot:f77",
        "f:slot:f82",
        "f:rectangular_through_notch:f31",
        "f:rectangular_through_notch:f37",
    ] {
        assert!(
            !lint
                .iter()
                .any(|p| p.code == "feature_not_located" && p.subjects.iter().any(|s| s == id)),
            "{id}: {lint:?}"
        );
    }
    // This is a real shared location, not an exemption for this feature family.
    // Removing the drawn 225 ordinate must leave both slot centres unlocated.
    let mut missing = drawing.clone();
    for annotation in &missing.annotations {
        if annotation.name == "d:f:hole_pattern:f0/location.length.y" {
            for &run in &annotation.runs {
                missing.text[run].text.clear();
            }
        }
    }
    let missing_lint = draftwright::lint::lint(&a, &missing);
    for id in ["f:slot:f77", "f:slot:f82"] {
        assert!(
            missing_lint
                .iter()
                .any(|p| p.code == "feature_not_located" && p.subjects.iter().any(|s| s == id)),
            "{id}: removing the actual ordinate must remove its evidence"
        );
    }
    // The ordinary dimension ink, rather than note text or planned metadata,
    // carries each notch's physical location.
    let mut unlocated = drawing.clone();
    for annotation in &unlocated.annotations {
        if annotation.name.contains("rectangular_through_notch")
            && annotation.name.contains("/location.length.")
        {
            for &run in &annotation.runs {
                unlocated.text[run].text = format!("1{}", unlocated.text[run].text);
            }
        }
    }
    let unlocated_lint = draftwright::lint::lint(&a, &unlocated);
    for id in [
        "f:rectangular_through_notch:f31",
        "f:rectangular_through_notch:f37",
    ] {
        assert!(
            unlocated_lint
                .iter()
                .any(|p| p.code == "feature_not_located" && p.subjects.iter().any(|s| s == id)),
            "{id}: a wrong printed linear location must lose its evidence"
        );
    }
}

#[test]
fn missing_nominals_require_unique_geometry_and_do_not_relax_stated_values() {
    let file = "nist_ctc_01_asme1_ap242-e1.stp";
    let bytes = gunzip(&std::fs::read(fixtures().join("step").join(format!("{file}.gz"))).unwrap());
    let (part, a) = analysed(file, &bytes);
    let r = records(&a).iter().find(|r| r.part21_id == 24).unwrap();
    let mut wrong = r.clone();
    wrong.value = Some(86.);
    assert!(
        draftwright::pmi_lowering::dimension_geometry(&part, &wrong)
            .reasons
            .iter()
            .any(|r| r.contains("differs from nominal"))
    );
    let mut missing = r.clone();
    missing.value = None;
    // A nonparallel companion has no constant plane separation: a box diagonal
    // is not a recovered PMI length.
    missing.face_groups[1] = vec![part.faces.iter().position(|f|
        matches!(&f.surface, draftwright::haecceity::geom::Surface::Plane { frame } if frame.z[1].abs() > 0.99)).unwrap()];
    assert!(
        !draftwright::pmi_lowering::dimension_geometry(&part, &missing)
            .reasons
            .is_empty()
    );
    let bore = records(&a).iter().find(|r| r.part21_id == 126).unwrap();
    let other = records(&a)
        .iter()
        .find(|r| r.kind == "diameter" && r.value == Some(25.))
        .unwrap();
    let mut inconsistent = bore.clone();
    inconsistent.value = None;
    inconsistent.faces.extend(&other.faces);
    assert!(
        !draftwright::pmi_lowering::dimension_geometry(&part, &inconsistent)
            .reasons
            .is_empty(),
        "different cylinder radii cannot supply one measured diameter"
    );
}
