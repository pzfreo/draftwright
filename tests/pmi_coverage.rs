//! The port's PMI coverage lint ([`draftwright::lint::pmi_coverage`]) against draftwright's
//! (`linting/pmi_coverage.py`), file by file, and its determinism and value check.
//!
//! draftwright's findings are in `tests/fixtures/pmi_coverage/<stem>.json.gz`
//! (`tools/capture_pmi_coverage.py`): every finding of a code `pmi_coverage.py` emits on its
//! `pmi="annotate"` drawing of each AP242 file of `tests/fixtures/pmi/step/`. The port's are
//! [`lint::lint`]'s findings of the same codes on its own drawing of the file (its features from
//! Rust quiddity, `model::analyse_native`, and the default spec, `model::plan_recovered`, as the
//! command line draws it: draftwright draws with its features too, and a PMI dimension a hole
//! callout or turned size states is stated there). A finding is keyed by its code and its source: a
//! draftwright source that is a record of draftwright's reading
//! (`tests/fixtures/pmi/<stem>.json.gz`) is named by the port's record it pairs with, as
//! `tests/pmi.rs` pairs them (by the Part 21 instance draftwright names, else by the faces, else a
//! datum by its letter); any other keeps draftwright's ID. A key found on one side only, or with
//! another severity, is a difference.
//!
//! Every difference must be listed, with a verdict and evidence, in
//! `tests/fixtures/known_pmi_coverage.json`; a listed difference that has gone away must be taken
//! off.

#[path = "common/support.rs"]
#[macro_use]
mod support;

use crate::cache;

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::haecceity::Part;
use draftwright::lint::{self, LintIssue};
use draftwright::model::{self, Analysis, Spec};
use draftwright::output::Drawing;
use draftwright::pmi::{Category, Outcome, Reading, Record};
use serde::Deserialize;
use serde_json::{Value, json};

/// The codes `pmi_coverage.py` emits (`tools/capture_pmi_coverage.py`'s `CODES`).
const CODES: [&str; 16] = [
    "pmi_present_but_ignored",
    "pmi_not_extracted",
    "pmi_not_lowered",
    "pmi_not_rendered",
    "pmi_value_mismatch",
    "pmi_source_text_mismatch",
    "pmi_source_site_mismatch",
    "pmi_unreconciled",
    "pmi_source_unknown",
    "manufacturing_reference_unresolved",
    "step_material_mismatch",
    "step_material_disagreement",
    "step_material_unavailable",
    "step_material_ambiguous",
    "step_general_tolerance_mismatch",
    "step_general_tolerance_disagreement",
];

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn fixtures() -> PathBuf {
    root().join("tests/fixtures")
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

fn json_gz(path: &Path) -> Value {
    serde_json::from_slice(&gunzip(&std::fs::read(path).unwrap())).unwrap()
}

/// The AP242 fixtures: `(file name as captured, stem, bytes)`.
fn inputs(only: &str) -> Vec<(String, String, Vec<u8>)> {
    let mut paths: Vec<PathBuf> = std::fs::read_dir(fixtures().join("pmi/step"))
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

/// The part's Analysis and the port's drawing of it, from *bytes* (written to a scratch file of
/// its own, as `analyse` reads a path and the tests run in parallel).
fn drawn(file: &str, bytes: &[u8]) -> (std::sync::Arc<Part>, Analysis, Drawing) {
    let (part, a) = cache::analysed(file, bytes, true, || {
        static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
            .join("pmi_coverage")
            .join(format!("{}-{n}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(file);
        std::fs::write(&path, bytes).unwrap();
        let result = model::analyse_native(&path).unwrap();
        std::fs::remove_file(&path).unwrap();
        result
    });
    let plan = cache::default_plan(&a, true);
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    (part, a, drawing)
}

fn drawn_uncached(file: &str, bytes: &[u8]) -> (Part, Analysis, Drawing) {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("pmi_coverage_uncached")
        // nextest processes each start their local counter at zero.
        .join(format!("{}-{n}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(file);
    std::fs::write(&path, bytes).unwrap();
    let (part, a) = model::analyse_native(&path).unwrap();
    std::fs::remove_file(&path).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    (part, a, drawing)
}

fn records(a: &Analysis) -> &[Record] {
    match &a.pmi {
        Some(Reading::Read(r)) => &r.records,
        other => panic!("not read: {other:?}"),
    }
}

/// The port's findings of `pmi_coverage.py`'s codes on *drawing*.
fn coverage(a: &Analysis, drawing: &Drawing) -> Vec<LintIssue> {
    let found: Vec<LintIssue> = lint::lint(a, drawing)
        .into_iter()
        .filter(|i| CODES.contains(&i.code.as_str()))
        .collect();
    for issue in &found {
        for internal in ["haecceity", "quiddity", "tools/", "lower_ap242_"] {
            assert!(
                !issue.message.contains(internal),
                "{}: {}",
                issue.code,
                issue.message
            );
        }
        if issue.code == "pmi_not_extracted" {
            assert!(
                issue.evidence.is_some(),
                "the original reader diagnostic is retained"
            );
        }
    }
    found
}

fn category(c: &str) -> Option<Category> {
    Some(match c {
        "dimension" => Category::Dimension,
        "geometric_tolerance" => Category::GeometricTolerance,
        "datum" => Category::Datum,
        "manufacturing_requirement" => Category::ManufacturingRequirement,
        "surface_label" => Category::SurfaceLabel,
        _ => return None,
    })
}

/// Whether *rec* is read from Part 21 alone, with no XCAF shapes: a manufacturing requirement,
/// a surface label or a common label.
fn part21_only(rec: &Value) -> bool {
    rec["kind"] == "common_label"
        || ["manufacturing_requirement", "surface_label"]
            .contains(&rec["source_category"].as_str().unwrap())
}

/// The instance number of `#N`.
fn number(s: &str) -> Option<u64> {
    s.strip_prefix('#')?.parse().ok()
}

/// draftwright's faces for *rec*: XCAF's, and for a datum or a Part 21 record its Part 21
/// support too.
fn python_faces(rec: &Value) -> BTreeSet<u64> {
    let mut out: BTreeSet<u64> = rec["faces"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap())
        .collect();
    if rec["source_category"] == "datum" || part21_only(rec) {
        out.extend(
            rec["reference_item_ids"]
                .as_array()
                .unwrap()
                .iter()
                .filter_map(|v| number(v.as_str().unwrap())),
        );
    }
    out
}

fn close(a: Option<f64>, b: Option<f64>) -> bool {
    match (a, b) {
        (None, None) => true,
        (Some(a), Some(b)) => (a - b).abs() <= 1e-9 * a.abs().max(b.abs()).max(1.0),
        _ => false,
    }
}

/// The port's faces for *r*, as `python_faces` takes draftwright's: its `ADVANCED_FACE`
/// instances, and for a Part 21 record (*part21*) its edges' instances too.
fn rust_faces(part: &Part, r: &Record, part21: bool) -> BTreeSet<u64> {
    let mut out: BTreeSet<u64> = r.face_ids.iter().copied().collect();
    if part21 {
        out.extend(
            r.edges
                .iter()
                .filter_map(|&e| part.edge_source(e).map(|s| s.entity)),
        );
    }
    out
}

/// draftwright's records of *py* (its reading) paired with the port's *rust* records on the
/// drawing's *part*, as `tests/pmi.rs` pairs them: draftwright's `source_id` to the port's.
fn pairs(py: &Value, part: &Part, rust: &[Record]) -> BTreeMap<String, String> {
    let records: Vec<&Value> = py["records"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|r| category(r["source_category"].as_str().unwrap()).is_some())
        .collect();
    let mut taken = vec![false; rust.len()];
    let mut out = BTreeMap::new();
    let id = |r: &Value| r["source_id"].as_str().unwrap().to_string();
    let cat = |r: &Value| category(r["source_category"].as_str().unwrap()).unwrap();
    for rec in &records {
        if let Some(key) = number(rec["key"].as_str().unwrap())
            && let Some(j) = (0..rust.len())
                .find(|&j| !taken[j] && rust[j].category == cat(rec) && rust[j].part21_id == key)
        {
            taken[j] = true;
            out.insert(id(rec), j);
        }
    }
    for rec in &records {
        let faces = python_faces(rec);
        if out.contains_key(&id(rec)) || faces.is_empty() {
            continue;
        }
        let candidates: Vec<usize> = (0..rust.len())
            .filter(|&j| {
                !taken[j]
                    && rust[j].category == cat(rec)
                    && rust_faces(part, &rust[j], part21_only(rec)) == faces
            })
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
    for rec in &records {
        let faces = python_faces(rec);
        if out.contains_key(&id(rec)) || faces.is_empty() {
            continue;
        }
        if let Some(j) = (0..rust.len()).find(|&j| {
            !taken[j]
                && rust[j].category == cat(rec)
                && rust[j].kind == rec["kind"].as_str().unwrap()
                && close(rust[j].value, rec["value"].as_f64())
                && faces.is_subset(&rust_faces(part, &rust[j], part21_only(rec)))
        }) {
            taken[j] = true;
            out.insert(id(rec), j);
        }
    }
    for rec in &records {
        if out.contains_key(&id(rec)) || rec["source_category"] != "datum" {
            continue;
        }
        if let Some(j) = (0..rust.len()).find(|&j| {
            !taken[j]
                && rust[j].category == Category::Datum
                && rust[j].label == rec["label"].as_str().unwrap()
        }) {
            taken[j] = true;
            out.insert(id(rec), j);
        }
    }
    out.into_iter()
        .map(|(k, j)| (k, rust[j].source_id.clone()))
        .collect()
}

#[derive(Debug, Deserialize)]
struct Known {
    file: String,
    code: String,
    source: String,
    python: Value,
    rust: Value,
    verdict: String,
    evidence: String,
}

#[derive(Clone, Debug, PartialEq)]
struct Diff {
    file: String,
    code: String,
    source: String,
    python: Value,
    rust: Value,
}

/// Findings by key, their code and source (`part` for a finding with none): the severity, and
/// the source's own ID where draftwright's was renamed by its pairing.
type Keyed = BTreeMap<(String, String), (String, Option<String>)>;

fn keyed(findings: impl Iterator<Item = (String, Vec<(String, String)>, String)>) -> Keyed {
    let mut out = Keyed::new();
    for (code, sources, severity) in findings {
        let sources = if sources.is_empty() {
            vec![("part".to_string(), "part".to_string())]
        } else {
            sources
        };
        for (key, own) in sources {
            let own = (own != key).then_some(own);
            out.insert((code.clone(), key), (severity.clone(), own));
        }
    }
    out
}

fn severity(i: &LintIssue) -> String {
    serde_json::to_value(i.severity)
        .unwrap()
        .as_str()
        .unwrap()
        .to_string()
}

/// The alpha 1 gate on *file*: every record of the reading is drawn (an annotation names or
/// claims its item ID, `pmi:<identity>`) or reported by lint as not lowered or not rendered,
/// never both; explicit no-requirement values instead stay read as info without either. Every
/// source not read, or read in part, is reported as not extracted, unless it is drawn (a material
/// the title block states), never both.
fn gate(file: &str, a: &Analysis, drawing: &Drawing, found: &[LintIssue]) {
    let Some(Reading::Read(report)) = &a.pmi else {
        panic!("{file}: its PMI is read");
    };
    let reported = |code: &str, id: &str| {
        found
            .iter()
            .any(|i| i.code == code && i.sources.iter().any(|s| s == id))
    };
    let ids = report.identities();
    for r in &report.records {
        // An item is named by its record's identity (none when another record shares it).
        let name = ids.ids.get(&r.source_id).cloned().unwrap_or_default();
        let on_sheet = drawing
            .annotations
            .iter()
            .any(|x| x.name == name || x.claims.iter().any(|c| c.feature == name));
        let said =
            reported("pmi_not_lowered", &r.source_id) || reported("pmi_not_rendered", &r.source_id);
        // Explicit absence is read information, not a missing drawing requirement. Check
        // the value independently of lowering and require its extracted informational source.
        if r.category == Category::ManufacturingRequirement
            && r.blockers.is_empty()
            && matches!(
                r.kind.as_str(),
                "general_note" | "surface_treatment" | "heat_treatment" | "edge_condition"
            )
            && matches!(
                r.label.trim().to_ascii_lowercase().as_str(),
                "" | "none" | "n/a" | "na" | "-"
            )
        {
            let source = report.sources.iter().find(|s| s.id == r.source_id).unwrap();
            assert_eq!(source.outcome, Outcome::Extracted);
            assert!(source.reason.starts_with("info: no requirement stated"));
            assert!(
                !on_sheet && !said,
                "{file}: {} needs neither ink nor a failure finding",
                r.source_id
            );
            continue;
        }
        assert!(
            on_sheet != said,
            "{file}: {} is drawn ({on_sheet}) or reported ({said}), not both",
            r.source_id
        );
    }
    for s in &report.sources {
        if matches!(
            s.outcome,
            Outcome::NotExtracted | Outcome::PartiallyExtracted
        ) {
            let name = format!("pmi:{}", s.id);
            let on_sheet = drawing.annotations.iter().any(|x| x.name == name);
            assert!(
                on_sheet != reported("pmi_not_extracted", &s.id),
                "{file}: {} is drawn ({on_sheet}) or reported, not both",
                s.id
            );
        }
    }
}

pmi_cases!(pmi_coverage_matches_draftwright_or_is_known);
fn pmi_coverage_matches_draftwright_or_is_known(only: &str) {
    let known: Vec<Known> = serde_json::from_str(
        &std::fs::read_to_string(fixtures().join("known_pmi_coverage.json")).unwrap(),
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
    for (file, stem, bytes) in inputs(only).into_iter().filter(|(file, _, _)| file == only) {
        let py = json_gz(&fixtures().join(format!("pmi_coverage/{stem}.json.gz")));
        let reading = json_gz(&fixtures().join(format!("pmi/{stem}.json.gz")));
        assert_eq!(py["file"], file.as_str());
        assert_eq!(
            py["sha256"],
            sha256(&bytes).as_str(),
            "{file}: not the captured file"
        );
        // A part the port cannot draw is one difference, of code `drawing`: lint has no drawing
        // to judge.
        let Ok((part, a, drawing)) = std::panic::catch_unwind(|| drawn(&file, &bytes)) else {
            diffs.push(Diff {
                file: file.clone(),
                code: "drawing".into(),
                source: "part".into(),
                python: json!(py.get("error").map_or("drawn", |_| "not drawn")),
                rust: json!("not drawn: the render panicked"),
            });
            eprintln!("{file}: the port could not draw it");
            continue;
        };
        let ours = coverage(&a, &drawing);
        gate(&file, &a, &drawing, &ours);
        let rust = keyed(ours.iter().map(|i| {
            let sources = i.sources.iter().map(|s| (s.clone(), s.clone())).collect();
            (i.code.clone(), sources, severity(i))
        }));
        // draftwright could not draw the part: every port finding is a difference.
        let python = match py.get("issues") {
            None => Keyed::new(),
            Some(issues) => {
                let paired = pairs(&reading, &part, records(&a));
                let named = |s: &str| paired.get(s).cloned().unwrap_or_else(|| s.to_string());
                keyed(issues.as_array().unwrap().iter().map(|i| {
                    (
                        i["code"].as_str().unwrap().to_string(),
                        i["source_ids"]
                            .as_array()
                            .unwrap()
                            .iter()
                            .map(|s| {
                                let s = s.as_str().unwrap();
                                (named(s), s.to_string())
                            })
                            .collect(),
                        i["severity"].as_str().unwrap().to_string(),
                    )
                }))
            }
        };
        let before = diffs.len();
        let keys: BTreeSet<&(String, String)> = python.keys().chain(rust.keys()).collect();
        for k in keys {
            let (p, r) = (python.get(k), rust.get(k));
            if p.map(|p| &p.0) != r.map(|r| &r.0) {
                diffs.push(Diff {
                    file: file.clone(),
                    code: k.0.clone(),
                    source: k.1.clone(),
                    python: p.map_or(Value::Null, |(s, own)| match own {
                        Some(own) => json!({"severity": s, "id": own}),
                        None => json!(s),
                    }),
                    rust: r.map_or(Value::Null, |r| json!(r.0)),
                });
            }
        }
        eprintln!(
            "{file}: {} draftwright findings, {} port findings, {} differences",
            python.len(),
            rust.len(),
            diffs.len() - before
        );
    }
    let listed: Vec<Diff> = known
        .iter()
        .filter(|k| k.file == only)
        .map(|k| Diff {
            file: k.file.clone(),
            code: k.code.clone(),
            source: k.source.clone(),
            python: k.python.clone(),
            rust: k.rust.clone(),
        })
        .collect();
    let keys: BTreeSet<(&str, &str, &str)> = listed
        .iter()
        .map(|d| (d.file.as_str(), d.code.as_str(), d.source.as_str()))
        .collect();
    assert_eq!(keys.len(), listed.len(), "a difference is listed twice");
    let unlisted: Vec<&Diff> = diffs.iter().filter(|d| !listed.contains(d)).collect();
    let stale: Vec<&Diff> = listed.iter().filter(|d| !diffs.contains(d)).collect();
    eprintln!("{} differences ({} known)", diffs.len(), listed.len());
    let as_json = |v: &[&Diff]| {
        v.iter()
            .map(|d| {
                json!({"file": d.file, "code": d.code, "source": d.source, "python": d.python, "rust": d.rust})
                    .to_string()
            })
            .collect::<Vec<_>>()
            .join("\n")
    };
    assert!(
        unlisted.is_empty() && stale.is_empty(),
        "differences not in known_pmi_coverage.json:\n{}\nlisted but no longer differing:\n{}",
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

#[test]
fn the_same_drawing_gives_the_same_findings() {
    let (file, bytes) = fixture("nist_ctc_01_asme1_ap242-e1");
    let (_, a, one) = drawn_uncached(&file, &bytes);
    let (_, b, two) = drawn_uncached(&file, &bytes);
    let first = coverage(&a, &one);
    assert!(!first.is_empty());
    assert_eq!(
        serde_json::to_string(&first).unwrap(),
        serde_json::to_string(&coverage(&b, &two)).unwrap()
    );
    let ids: BTreeSet<&str> = first.iter().map(|i| i.id.as_str()).collect();
    assert_eq!(ids.len(), first.len(), "each finding's ID is unique");
}

#[test]
fn a_frame_that_misstates_its_source_is_reported() {
    let (file, bytes) = fixture("nist_ftc_07_asme1_ap242-e2");
    let (_, a, drawing) = drawn(&file, &bytes);
    let codes = |found: &[LintIssue], code: &str| -> Vec<Vec<String>> {
        found
            .iter()
            .filter(|i| i.code == code)
            .map(|i| i.sources.clone())
            .collect()
    };
    let clean = coverage(&a, &drawing);
    assert!(codes(&clean, "pmi_value_mismatch").is_empty());
    // Every frame and symbol lowered is drawn; what the drawing leaves unstated is dimensions
    // (a standalone diameter the plan refuses, `known_pmi_coverage.json`).
    let unstated = codes(&clean, "pmi_not_rendered");
    assert!(
        unstated
            .iter()
            .flatten()
            .all(|s| s.starts_with("dimension:")),
        "{unstated:?}"
    );
    let with = |source: &str| {
        let mut out = unstated.clone();
        out.push(vec![source.to_string()]);
        out.sort();
        out
    };
    let sorted = |mut v: Vec<Vec<String>>| {
        v.sort();
        v
    };
    let k = drawing
        .annotations
        .iter()
        .position(|x| x.name.starts_with("pmi:geometric_tolerance."))
        .expect("a drawn frame");
    let name = drawing.annotations[k].name.clone();
    // Named by its record's identity; lint reports by the record's source.
    let Some(Reading::Read(report)) = &a.pmi else {
        panic!("its PMI is read")
    };
    let source = report.identities().source(&name).unwrap().to_string();
    // The magnitude's last digit altered in the frame's text run.
    let mut bad = drawing.clone();
    let run = bad.annotations[k].runs[1];
    let text = &mut bad.text[run].text;
    let at = text.rfind(|c: char| c.is_ascii_digit()).unwrap();
    let digit = text[at..=at].parse::<u32>().unwrap();
    text.replace_range(at..=at, &((digit + 1) % 10).to_string());
    let found = coverage(&a, &bad);
    let mismatch: Vec<&LintIssue> = found
        .iter()
        .filter(|i| i.code == "pmi_value_mismatch")
        .collect();
    assert_eq!(mismatch.len(), 1, "{found:#?}");
    assert_eq!(mismatch[0].sources, std::slice::from_ref(&source));
    assert_eq!(mismatch[0].annotation.as_deref(), Some(name.as_str()));
    assert_eq!(mismatch[0].severity, lint::Severity::Error);
    // A datum letter dropped from the frame is a mismatch too.
    let mut fewer = drawing.clone();
    if fewer.annotations[k].runs.len() > 2 {
        fewer.annotations[k].runs.pop();
        assert_eq!(
            codes(&coverage(&a, &fewer), "pmi_value_mismatch"),
            [[source.clone()]]
        );
    }
    // The frame removed: lowered, not drawn.
    let mut gone = drawing.clone();
    gone.annotations.remove(k);
    assert_eq!(
        sorted(codes(&coverage(&a, &gone), "pmi_not_rendered")),
        with(&source)
    );
    // The frame claiming a source the reading does not hold: fabricated, and its own source
    // undrawn.
    let mut forged = drawing.clone();
    forged.annotations[k].name = "pmi:geometric_tolerance:#999999999".into();
    let found = coverage(&a, &forged);
    assert_eq!(
        codes(&found, "pmi_source_unknown"),
        [["geometric_tolerance:#999999999".to_string()]]
    );
    assert_eq!(sorted(codes(&found, "pmi_not_rendered")), with(&source));
    // No PMI reading to check the drawing's claims against: unverified, not satisfied.
    let mut none = a.clone();
    none.pmi = None;
    let found = coverage(&none, &drawing);
    assert_eq!(found.len(), 1, "{found:#?}");
    assert_eq!(
        (found[0].code.as_str(), found[0].severity),
        ("pmi_unreconciled", lint::Severity::Warning)
    );
    // A reading refused: the PMI could not be inventoried.
    none.pmi = Some(Reading::Refused("cannot read Part 21: test".into()));
    let found = coverage(&none, &bare(&drawing));
    assert_eq!(found.len(), 1, "{found:#?}");
    assert_eq!(
        (found[0].code.as_str(), found[0].severity),
        ("pmi_not_extracted", lint::Severity::Error)
    );
    assert!(
        found[0]
            .message
            .contains("STEP annotations could not be read")
    );
    assert!(found[0].message.contains("review them manually"));
    assert_eq!(
        found[0].evidence.as_deref(),
        Some("cannot read Part 21: test")
    );
}

/// *drawing* without its PMI annotations.
fn bare(drawing: &Drawing) -> Drawing {
    let mut out = drawing.clone();
    out.annotations.retain(|x| {
        !x.name.starts_with("pmi:") && x.claims.iter().all(|c| !c.feature.starts_with("pmi:"))
    });
    out
}

/// LINT-code-pmi_source_text_mismatch, text-run half: a wrapped source note is checked from
/// the finished drawing, and changing just its printed text keeps its provenance but loses truth.
#[test]
fn a_source_edge_note_must_keep_every_word_when_wrapped() {
    let (file, bytes) = fixture("spool_fits");
    let (part, mut a, _) = drawn(&file, &bytes);
    let Some(Reading::Read(report)) = &mut a.pmi else {
        panic!("PMI is read")
    };
    let edge = report
        .records
        .iter_mut()
        .find(|r| r.kind == "edge_condition")
        .unwrap();
    edge.label = "Break sharp edges and remove all burrs around external corners 0.2 max".into();
    let source = edge.source_id.clone();
    let id = report.identities().id(&source).unwrap().to_string();
    let plan = cache::default_plan(&a, true);
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let mut drawing = model::render(&part, &a, &plan, &font).unwrap();
    let annotation = drawing.annotations.iter().find(|n| n.name == id).unwrap();
    assert!(annotation.runs.len() >= 2, "exercise a wrapped note");
    let runs = annotation.runs.clone();
    let mismatch = |drawing: &Drawing| {
        coverage(&a, drawing)
            .into_iter()
            .filter(|i| i.code == "pmi_source_text_mismatch" && i.sources.contains(&source))
            .collect::<Vec<_>>()
    };
    assert!(mismatch(&drawing).is_empty());
    let last = *runs.last().unwrap();
    assert!(drawing.text[last].text.contains("0.2 max"));
    drawing.text[last].text = drawing.text[last].text.replace("0.2 max", "");
    assert_eq!(mismatch(&drawing).len(), 1);
    drawing.annotations.retain(|n| n.name != id);
    assert!(
        coverage(&a, &drawing)
            .iter()
            .any(|i| i.code == "pmi_not_rendered" && i.sources.contains(&source))
    );
}
