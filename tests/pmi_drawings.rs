//! The PMI on the port's drawing against draftwright's drawing, item by item (alpha 1's PMI
//! gate): for every AP242 record of every file of `tests/fixtures/pmi/step/`, whether the
//! drawing states it, and if so where (the view), what it shows (the label or the frame's
//! compartments, with tolerance, modifiers, datum references and a basic dimension's box) and
//! which model dimension it rides on; if not, whether placement dropped it or the planner
//! refused it, and with which codes. Positions are not compared.
//!
//! draftwright's drawing is in `tests/fixtures/pmi_drawings/<stem>.json.gz`
//! (`tools/capture_pmi_drawings.py`, `build_drawing(..., pmi="annotate")`). The port's is built
//! as the library builds it: `analyse_native` (Rust quiddity's features, the CLI's default),
//! `plan_recovered` (page escalation) and `render`, read back from the drawing's annotations
//! (their claims and text runs). A draftwright record is paired with the port's as
//! `tests/pmi.rs` pairs them (by the Part 21 instance draftwright names, else by the faces,
//! else a datum by its letter), from draftwright's reading in `tests/fixtures/pmi/<stem>.json.gz`.
//!
//! Every difference must be listed, with a verdict and evidence, in
//! `tests/fixtures/pmi_drawings/known_pmi_drawings.json`; a listed difference that has gone away
//! must be taken off.

#[path = "common/support.rs"]
#[macro_use]
mod support;

#[path = "common/cache.rs"]
mod cache;

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::model::{self, Analysis, Plan};
use draftwright::output::Drawing;
use draftwright::pmi::{Category, Reading, Record};
use serde::Deserialize;
use serde_json::{Value, json};

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

fn json_gz(path: &Path) -> Value {
    serde_json::from_slice(&gunzip(&std::fs::read(path).unwrap())).unwrap()
}

/// The part and its Analysis with Rust quiddity's features, from *bytes* (written to a scratch
/// file of its own, as `analyse` reads a path).
fn analysed_uncached(file: &str, bytes: &[u8]) -> (draftwright::haecceity::Part, Analysis) {
    static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
    let n = N.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("pmi_drawings")
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

fn category(c: &str) -> Option<Category> {
    match c {
        "dimension" => Some(Category::Dimension),
        "geometric_tolerance" => Some(Category::GeometricTolerance),
        "datum" => Some(Category::Datum),
        "manufacturing_requirement" => Some(Category::ManufacturingRequirement),
        "surface_label" => Some(Category::SurfaceLabel),
        _ => None,
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

/// draftwright's faces for *rec*: XCAF's, and for a datum its Part 21 support too.
fn python_faces(rec: &Value) -> BTreeSet<u64> {
    let mut out: BTreeSet<u64> = rec["faces"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap())
        .collect();
    if rec["source_category"] == "datum" {
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

/// draftwright's records of *reading* paired with the port's *rust* records, as `tests/pmi.rs`
/// pairs them: draftwright's `source_id` to the port's record index.
fn pairs(reading: &Value, rust: &[Record]) -> BTreeMap<String, usize> {
    let records: Vec<(&Value, Category)> = reading["records"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|r| Some((r, category(r["source_category"].as_str().unwrap())?)))
        .collect();
    let mut taken = vec![false; rust.len()];
    let mut out = BTreeMap::new();
    let id = |r: &Value| r["source_id"].as_str().unwrap().to_string();
    for (rec, c) in &records {
        if let Some(key) = number(rec["key"].as_str().unwrap_or(""))
            && let Some(j) = (0..rust.len())
                .find(|&j| !taken[j] && rust[j].category == *c && rust[j].part21_id == key)
        {
            taken[j] = true;
            out.insert(id(rec), j);
        }
    }
    // By the same faces (the same kind and value first), then by faces the port's include.
    for exact in [true, false] {
        for (rec, c) in &records {
            if out.contains_key(&id(rec)) {
                continue;
            }
            let faces = python_faces(rec);
            if faces.is_empty() {
                continue;
            }
            let same_value = |j: usize| {
                rust[j].kind == rec["kind"].as_str().unwrap()
                    && close(rust[j].value, rec["value"].as_f64())
            };
            let candidates: Vec<usize> = (0..rust.len())
                .filter(|&j| !taken[j] && rust[j].category == *c)
                .filter(|&j| {
                    let theirs: BTreeSet<u64> = rust[j].face_ids.iter().copied().collect();
                    if exact {
                        theirs == faces
                    } else {
                        same_value(j) && faces.is_subset(&theirs)
                    }
                })
                .collect();
            let best = candidates
                .iter()
                .copied()
                .find(|&j| same_value(j))
                .or_else(|| candidates.first().copied());
            if let Some(j) = best {
                taken[j] = true;
                out.insert(id(rec), j);
            }
        }
    }
    for (rec, c) in &records {
        if out.contains_key(&id(rec)) || *c != Category::Datum {
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
    out
}

/// draftwright's symbols as the port writes them.
fn norm(text: &str) -> String {
    text.replace(['ø', 'Ø'], "⌀")
}

/// A characteristic's symbol, as the port draws it in a frame's first compartment
/// (`dims::gdt`, ASME Y14.5 / ISO 1101).
fn symbol(characteristic: &str) -> &'static str {
    match characteristic {
        "straightness" => "⏤",
        "flatness" => "⏥",
        "circularity" => "○",
        "cylindricity" => "⌭",
        "profile_line" => "⌒",
        "profile_surface" => "⌓",
        "angularity" => "∠",
        "perpendicularity" => "⟂",
        "parallelism" => "∥",
        "position" => "⌖",
        "concentricity" | "coaxiality" => "◎",
        "symmetry" => "⌯",
        "circular_runout" => "↗",
        "total_runout" => "⌰",
        other => panic!("characteristic {other}"),
    }
}

/// One thing the drawing shows of a record: what kind of thing, its view (none for the sheet's
/// furniture), the model dimension's parameter it rides on (none when it stands alone), what it
/// shows, and whether it is boxed (a basic dimension).
fn entry(kind: &str, view: Option<&str>, rides: Option<&str>, text: String, boxed: bool) -> Value {
    json!({"kind": kind, "view": view, "rides_on": rides, "text": text, "boxed": boxed})
}

/// draftwright's parameter less a split callout's member index (`bore.diameter.member_2`, the
/// third hole of the callout it split): the port splits a callout into items of their own, each
/// stating `bore.diameter`, so the index says nothing the label does not.
fn member_free(parameter: &str) -> &str {
    match parameter.rsplit_once('.') {
        Some((stem, member))
            if member
                .strip_prefix("member_")
                .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit())) =>
        {
            stem
        }
        _ => parameter,
    }
}

/// draftwright's frame text as one line of compartments: `⌖ ⌀0.2Ⓜ A B`.
fn python_frame(d: &Value) -> String {
    let tokens: Vec<String> = d["text"]
        .as_array()
        .unwrap()
        .iter()
        .map(|t| norm(t.as_str().unwrap()))
        .collect();
    let datums = d["datums"].as_u64().unwrap() as usize;
    let (head, letters) = tokens.split_at(tokens.len() - datums);
    let mut run = String::new();
    let mut seen_value = false;
    for t in head {
        match t.as_str() {
            "⌀" | "S⌀" if !seen_value => run += t,
            "M" if seen_value => run += "Ⓜ",
            "L" if seen_value => run += "Ⓛ",
            _ => {
                run += t;
                seen_value = true;
            }
        }
    }
    let mut cells = vec![
        symbol(d["characteristic"].as_str().unwrap()).to_string(),
        run,
    ];
    cells.extend(letters.iter().cloned());
    cells.join(" ")
}

/// draftwright's projection of one captured item: its drawn entries (sorted), or its outcome
/// when nothing states it.
fn python_projection(item: &Value) -> Value {
    let mut drawn: Vec<Value> = item["drawn"]
        .as_array()
        .unwrap()
        .iter()
        .map(|d| {
            let kind = d["kind"].as_str().unwrap();
            let view = d["view"].as_str();
            match kind {
                "dimension" => entry(
                    kind,
                    view,
                    d["parameter"].as_str().map(member_free),
                    norm(d["text"].as_str().unwrap_or("")),
                    d["boxed"].as_bool().unwrap(),
                ),
                "control_frame" => entry(kind, view, None, python_frame(d), false),
                _ => {
                    let text: Vec<String> = d["text"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|t| norm(t.as_str().unwrap()))
                        .collect();
                    entry(kind, view, None, text.join(" "), false)
                }
            }
        })
        .collect();
    drawn.sort_by_key(|v| v.to_string());
    drawn.dedup();
    if !drawn.is_empty() {
        return json!({"drawn": drawn});
    }
    let codes: Vec<&str> = item["issues"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c.as_str().unwrap())
        .collect();
    let dropped = codes
        .iter()
        .any(|c| *c == "pmi_dropped" || *c == "callout_dropped");
    json!({"outcome": if dropped { "dropped" } else { "refused" }, "codes": codes})
}

/// A planned GD&T item's kind in draftwright's words: a frame, a datum symbol, a label (a
/// `note`).
fn gdt_kind(kind: &str) -> &'static str {
    match kind {
        "control_frame" => "control_frame",
        "label" => "note",
        _ => "datum_ref",
    }
}

/// The parameter a model item's ID names: `d:f:hole:1/bore.diameter` → `bore.diameter`.
fn parameter_of(id: &str) -> Option<&str> {
    id.rsplit_once('/').map(|(_, p)| p)
}

/// The port's projection of its record *r*: what the rendered drawing's annotations state of it,
/// or, when none does, whether placement dropped it (planned, not placed) or the planner
/// refused it (its problems' codes).
fn rust_projection(
    drawing: Option<&Drawing>,
    plan: &Plan,
    analysis: &Analysis,
    r: &Record,
) -> Value {
    // Its items are named by its item ID, `pmi:<identity>` (none when another record shares it).
    let feature = match &analysis.pmi {
        Some(Reading::Read(report)) => report.identities().ids.get(&r.source_id).cloned(),
        _ => None,
    }
    .unwrap_or_default();
    let own = format!("d:{feature}/");
    let mut drawn = Vec::new();
    if let Some(drawing) = drawing {
        for a in &drawing.annotations {
            let view = a.view.as_deref().map(|v| v.strip_prefix("v:").unwrap_or(v));
            let text: Vec<&str> = a
                .runs
                .iter()
                .map(|&i| drawing.text[i].text.as_str())
                .collect();
            if a.name == feature
                && let Some(q) = plan.requirements.iter().find(|q| q.id == a.name)
            {
                // A drawing-wide requirement: what it states, read back from its runs (a general
                // note's rows less the note's number).
                let read: Vec<&str> = text
                    .iter()
                    .enumerate()
                    .map(|(i, t)| match (i, q.carrier.as_str()) {
                        (0, "notes") => t.split_once("  ").map_or(*t, |(_, rest)| rest),
                        _ => t,
                    })
                    .collect();
                let words = |s: &str| s.split_whitespace().collect::<Vec<_>>().join(" ");
                assert_eq!(words(&read.join(" ")), words(&q.text), "{}", a.name);
                drawn.push(entry(&q.kind, None, None, norm(&q.text), false));
            } else if a.name == feature {
                // A frame, datum symbol or label: its compartments as planned (the
                // characteristic's symbol is drawn in strokes, so has no text run), checked
                // against its runs.
                let g = plan.gdt.iter().find(|g| g.id == a.name).unwrap();
                let shown: Vec<&str> = g.cells.iter().map(String::as_str).collect();
                let mut runs: Vec<&str> = text.iter().copied().filter(|t| !t.is_empty()).collect();
                // Multiplicity (`4×`) stands above the frame or the top row of its
                // shared-leader stack; it is not a tolerance compartment.
                if g.kind == "control_frame"
                    && runs
                        .first()
                        .and_then(|t| t.strip_suffix('×'))
                        .is_some_and(|n| n.parse::<usize>().is_ok_and(|n| n > 1))
                {
                    let p = g.placed.as_ref().unwrap();
                    let top = p.strokes[..p.frame_strokes]
                        .iter()
                        .flatten()
                        .map(|q| q[1])
                        .fold(f64::NEG_INFINITY, f64::max);
                    assert_eq!(p.runs.len(), g.cells.len() + 1, "{}: one count", g.id);
                    assert!(p.runs[0].at[1] > top, "{}: count above its box", g.id);
                    if g.side != "leader" || p.lines.is_empty() {
                        let carrier = plan
                            .gdt
                            .iter()
                            .find(|other| {
                                other.id != g.id
                                    && other.kind == "control_frame"
                                    && other.view == g.view
                                    && !g.faces.is_empty()
                                    && other.faces == g.faces
                                    && other.placed.as_ref().is_some_and(|q| {
                                        !q.lines.is_empty()
                                            && (p.label_box[1] - q.label_box[3]).abs() < 1e-9
                                            && p.label_box[0].max(q.label_box[0])
                                                < p.label_box[2].min(q.label_box[2])
                                    })
                            })
                            .expect("a counted top row must touch its shared-leader frame");
                        assert_eq!(
                            carrier.placed.as_ref().unwrap().runs.len(),
                            carrier.cells.len(),
                            "{}: shared count must not be repeated below",
                            g.id
                        );
                    }
                    runs.remove(0);
                }
                assert!(
                    runs.iter().all(|t| shown.contains(t)),
                    "{}: runs {runs:?} not among its cells {shown:?}",
                    a.name
                );
                drawn.push(entry(
                    gdt_kind(&g.kind),
                    view,
                    None,
                    norm(&g.cells.join(" ")),
                    false,
                ));
            } else if a.claims.iter().any(|c| c.feature == feature) {
                let standalone = a.name.starts_with(&own);
                let boxed = a
                    .claims
                    .iter()
                    .any(|c| c.feature == feature && c.parameter.ends_with(".basic"));
                drawn.push(entry(
                    "dimension",
                    view,
                    if standalone {
                        None
                    } else {
                        parameter_of(&a.name)
                    },
                    norm(&text.join(" ")),
                    boxed,
                ));
            }
        }
    } else {
        // No drawing (render failed): the plan's placed items, as render would draw them.
        for g in plan
            .gdt
            .iter()
            .filter(|g| g.id == feature && g.placed.is_some())
        {
            let view = g.view.strip_prefix("v:").unwrap_or(&g.view);
            drawn.push(entry(
                gdt_kind(&g.kind),
                Some(view),
                None,
                norm(&g.cells.join(" ")),
                false,
            ));
        }
        // The drawing-wide requirements whose carrier has a place.
        for q in plan.requirements.iter().filter(|q| q.id == feature) {
            let placed = match q.carrier.as_str() {
                "notes" => plan
                    .notes
                    .iter()
                    .any(|n| n.id == q.id && n.placed.is_some()),
                "sheet" => q.placed.is_some(),
                "none" => false,
                _ => true,
            };
            if placed {
                drawn.push(entry(&q.kind, None, None, norm(&q.text), false));
            }
        }
        for d in plan.dimensions.iter().filter(|d| d.placed.is_some()) {
            if d.measurements.iter().any(|m| m.feature == feature) {
                let view = d.view.strip_prefix("v:").unwrap_or(&d.view);
                let boxed = d
                    .measurements
                    .iter()
                    .any(|m| m.feature == feature && m.parameter.ends_with(".basic"));
                let standalone = d.id.starts_with(&own);
                drawn.push(entry(
                    "dimension",
                    Some(view),
                    if standalone {
                        None
                    } else {
                        parameter_of(&d.id)
                    },
                    norm(&d.terms.join(" ")),
                    boxed,
                ));
            }
        }
    }
    drawn.sort_by_key(|v| v.to_string());
    drawn.dedup();
    if plan
        .requirements
        .iter()
        .any(|q| q.id == feature && q.kind == "no_requirement")
    {
        assert!(
            drawn.is_empty(),
            "a no-requirement value must never be drawn"
        );
        return json!({"outcome": "no_requirement", "codes": []});
    }
    if !drawn.is_empty() {
        return json!({"drawn": drawn});
    }
    // The items that state it: its frame, symbol, label or drawing-wide requirement
    // (`pmi:<source>`) and the dimensions claiming it; their problems say why they are not
    // drawn.
    let mut items: Vec<&str> = plan
        .gdt
        .iter()
        .map(|g| g.id.as_str())
        .chain(plan.requirements.iter().map(|q| q.id.as_str()))
        .filter(|id| *id == feature)
        .collect();
    items.extend(
        plan.dimensions
            .iter()
            .filter(|d| d.measurements.iter().any(|m| m.feature == feature))
            .map(|d| d.id.as_str()),
    );
    let planned = !items.is_empty();
    let codes: BTreeSet<&str> = plan
        .problems
        .iter()
        .filter(|p| {
            p.subjects
                .iter()
                .chain(&p.refs)
                .any(|s| *s == r.source_id || items.contains(&s.as_str()))
        })
        .map(|p| p.code.as_str())
        .collect();
    json!({
        "outcome": if planned { "dropped" } else { "refused" },
        "codes": codes.into_iter().collect::<Vec<_>>(),
    })
}

/// Two projections differ when what is drawn differs, or, nothing being drawn, the outcome
/// (dropped or refused) does; the codes are each side's own vocabulary and are carried as
/// evidence, not compared.
fn same(python: &Value, rust: &Value) -> bool {
    match (python.get("drawn"), rust.get("drawn")) {
        (Some(p), Some(r)) => p == r,
        (None, None) => python["outcome"] == rust["outcome"],
        _ => false,
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    file: String,
    /// draftwright's `source_id`, or the port's when draftwright has no such record, or `file`.
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

/// The port's drawing of *plan*, or why there is none: render's refusal, or its panic's message
/// (a panic is caught so the other items are still compared; it is a difference of its own).
fn drawn(
    part: &draftwright::haecceity::Part,
    a: &Analysis,
    plan: &Plan,
) -> Result<Drawing, String> {
    let font =
        draftwright::text::Font::open(&root().join("fonts").join(draftwright::dims::FONT)).unwrap();
    let out = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        model::render(part, a, plan, &font)
    }));
    match out {
        Ok(Ok(d)) => Ok(d),
        Ok(Err(e)) => Err(format!("refused: {e:?}")),
        Err(p) => {
            let msg = p
                .downcast_ref::<String>()
                .cloned()
                .or_else(|| p.downcast_ref::<&str>().map(|s| s.to_string()))
                .unwrap_or_default();
            Err(format!("panicked: {msg}"))
        }
    }
}

fn compare(
    file: &str,
    py: &Value,
    reading: &Value,
    a: &Analysis,
    plan: &Plan,
    drawing: &Result<Drawing, String>,
) -> Vec<Diff> {
    let rust = records(a);
    let paired = pairs(reading, rust);
    let mut diffs = Vec::new();
    let mut file_diff = |field: &str, python: Value, rust: Value| {
        diffs.push(Diff {
            file: file.into(),
            record: "file".into(),
            port: Value::Null,
            field: field.into(),
            python,
            rust,
        })
    };
    let drawing = match drawing {
        Ok(d) => Some(d),
        Err(e) => {
            file_diff("render", json!("drawn"), json!(e));
            None
        }
    };
    let failed = py["error"].as_str();
    if let Some(e) = failed {
        let n = rust
            .iter()
            .filter(|r| rust_projection(drawing, plan, a, r).get("drawn").is_some())
            .count();
        file_diff("drawing", json!(e), json!(format!("{n} records drawn")));
    }
    let mut seen = BTreeSet::new();
    for item in py["items"].as_array().unwrap() {
        let id = item["source_id"].as_str().unwrap().to_string();
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
            let what =
                json!({"category": item["category"], "kind": item["kind"], "label": item["label"]});
            let python = if failed.is_some() {
                what
            } else {
                json!({"record": what, "drawing": python_projection(item)})
            };
            diff("record", python, Value::Null);
            continue;
        };
        seen.insert(r.source_id.clone());
        if failed.is_some() {
            continue;
        }
        let (p, q) = (
            python_projection(item),
            rust_projection(drawing, plan, a, r),
        );
        if !same(&p, &q) {
            diff("drawing", p, q);
        }
    }
    for r in rust {
        if !seen.contains(&r.source_id) {
            diffs.push(Diff {
                file: file.into(),
                record: r.source_id.clone(),
                port: json!(r.source_id),
                field: "record".into(),
                python: Value::Null,
                rust: json!({
                    "record": {"kind": r.kind, "label": r.label},
                    "drawing": rust_projection(drawing, plan, a, r),
                }),
            });
        }
    }
    diffs
}

/// One file compared: its differences, draftwright's record count, and a summary line.
fn one(file: &str, stem: &str, bytes: &[u8]) -> (Vec<Diff>, usize, String) {
    let py = json_gz(&fixtures().join(format!("pmi_drawings/{stem}.json.gz")));
    let reading = json_gz(&fixtures().join(format!("pmi/{stem}.json.gz")));
    assert_eq!(py["file"], file);
    assert_eq!(
        py["sha256"],
        sha256(bytes).as_str(),
        "{file}: not the captured file"
    );
    // The capture and the reading are the same extraction: the same records.
    let ids = |v: &Value, key: &str| -> Vec<String> {
        v[key]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| r["source_id"].as_str().unwrap().to_string())
            .collect()
    };
    assert_eq!(ids(&py, "items"), ids(&reading, "records"), "{file}");
    let (part, a) = analysed(file, bytes);
    let plan = cache::default_plan(&a, true);
    let drawing = drawn(&part, &a, &plan);
    let inventory = serde_json::from_str::<Value>(
        &std::fs::read_to_string(fixtures().join("pmi_drawings/cad_frame_information.json"))
            .unwrap(),
    )
    .unwrap();
    if let Some(expected) = inventory["parts"].get(file) {
        let sources = |key: &str| -> BTreeSet<String> {
            expected[key]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_str().unwrap().to_owned())
                .collect()
        };
        // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
        // J alone may leave the main inventory; FTC10 additionally draws its J.
        let expected: BTreeSet<_> = sources("main_drawn_sources")
            .difference(&sources("accepted_missing_sources"))
            .cloned()
            .chain(sources("additional_sources"))
            .collect();
        let actual: BTreeSet<_> = records(&a)
            .iter()
            .filter(|r| {
                rust_projection(
                    Some(drawing.as_ref().expect("PMI must render")),
                    &plan,
                    &a,
                    r,
                )
                .get("drawn")
                .is_some()
            })
            .map(|r| r.source_id.clone())
            .collect();
        assert_eq!(actual, expected, "{file}: main PMI information changed");
    }
    if file == "assembly_plate_pin.step" {
        // The former 6× callout is now six placed schedule rows, not six lost sizes.
        let table = plan
            .hole_tables
            .iter()
            .find(|t| t.id == "hole_schedule_plan")
            .unwrap();
        let holes: Vec<_> = table
            .rows
            .iter()
            .filter(|r| r.cells[1] == "⌀6.6 +0.09/0 THRU")
            .collect();
        assert_eq!(
            holes
                .iter()
                .map(|r| (r.tag.as_str(), r.feature.as_str()))
                .collect::<Vec<_>>(),
            [
                ("C1", "f:hole:f6"),
                ("C2", "f:hole:f7"),
                ("C3", "f:hole:f8"),
                ("C4", "f:hole:f9"),
                ("C5", "f:hole:f10"),
                ("C6", "f:hole:f12")
            ]
        );
        assert!(!table.blocks.is_empty() && table.blocks.iter().all(|b| b.placed.is_some()));
        for row in holes {
            assert!(row.measurements.iter().any(|m| m.feature == row.feature
                && m.parameter == "bore.diameter"
                && m.value == 6.6));
        }
    }
    let d = compare(file, &py, &reading, &a, &plan, &drawing);
    let n = py["items"].as_array().unwrap().len();
    let python_drawn = py["items"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|i| i["drawn"].as_array().is_some_and(|d| !d.is_empty()))
        .count();
    let rust_drawn = records(&a)
        .iter()
        .filter(|r| {
            rust_projection(drawing.as_ref().ok(), &plan, &a, r)
                .get("drawn")
                .is_some()
        })
        .count();
    let summary = format!(
        "{file}: {n} draftwright records, {python_drawn} drawn by draftwright; {} port records, {rust_drawn} drawn by the port; {} differences",
        records(&a).len(),
        d.len()
    );
    (d, n, summary)
}

pmi_cases!(pmi_on_the_drawing_matches_draftwright_or_is_known);
fn pmi_on_the_drawing_matches_draftwright_or_is_known(only: &str) {
    let known: Vec<Known> = serde_json::from_str(
        &std::fs::read_to_string(fixtures().join("pmi_drawings/known_pmi_drawings.json")).unwrap(),
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
    let (file, stem, bytes) = inputs(only)
        .into_iter()
        .find(|(file, _, _)| file == only)
        .unwrap();
    let (diffs, items, summary) = one(&file, &stem, &bytes);
    eprintln!("{summary}");
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
    let mut by_verdict: BTreeMap<&str, usize> = BTreeMap::new();
    for k in &known {
        *by_verdict.entry(k.verdict.as_str()).or_default() += 1;
    }
    eprintln!(
        "{items} draftwright records; {} differences ({} known: {by_verdict:?})",
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
        "differences not in known_pmi_drawings.json:\n{}\nlisted but no longer differing:\n{}",
        as_json(&unlisted),
        as_json(&stale)
    );
}
