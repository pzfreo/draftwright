//! The rules catalogue (`docs/rules/`) kept honest, and scenarios proving rules the port already
//! met when stream K8 re-assessed the catalogue (2026-10-16).
//!
//! The catalogue checks read the YAML line by line (each entry starts `- id:` and keeps its
//! fields on single lines, as the extraction wrote them), so they need no YAML parser: every
//! status is one of four, a pending rule says what remains (`rust_note`), a ported rule names its
//! test, a translated one its rule, a retired one why, every named test exists, and the README's
//! tables agree with the files.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::api;
use draftwright::model::{self, Analysis, Plan, Spec};
use draftwright::text::Font;

#[path = "common/corpus.rs"]
mod corpus;

const FILES: [&str; 3] = ["adr.yaml", "lint.yaml", "issues.yaml"];
const STATUSES: [&str; 4] = ["pending", "ported", "translated", "retired"];
const CLASSES: [&str; 4] = ["drafting", "trust", "spec", "machinery"];

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// One catalogue entry: its ID and its top-level fields (`status`, `rust_note`, …), values as
/// written, quotes taken off.
struct Entry {
    id: String,
    fields: BTreeMap<String, String>,
}

fn entries(file: &str) -> Vec<Entry> {
    let text = std::fs::read_to_string(root().join("docs/rules").join(file)).unwrap();
    let mut out: Vec<Entry> = Vec::new();
    for line in text.lines() {
        if let Some(id) = line.strip_prefix("- id: ") {
            out.push(Entry {
                id: id.trim().into(),
                fields: BTreeMap::new(),
            });
        } else if let (Some(e), Some(rest)) = (out.last_mut(), line.strip_prefix("  "))
            && !rest.starts_with(' ')
            && let Some((key, value)) = rest.split_once(':')
        {
            let value = value.trim();
            let value = value
                .strip_prefix('"')
                .and_then(|v| v.strip_suffix('"'))
                .unwrap_or(value);
            e.fields.insert(key.into(), value.into());
        }
    }
    out
}

fn field<'a>(e: &'a Entry, key: &str) -> &'a str {
    e.fields.get(key).map_or("", |v| v.as_str())
}

/// Function names, source-qualified names, and integration target/module-qualified names.
/// Qualified citations must resolve through an explicit Cargo target and its root module.
fn functions() -> BTreeSet<String> {
    fn in_file(path: &Path) -> BTreeSet<String> {
        let text = std::fs::read_to_string(path).unwrap();
        let mut out = BTreeSet::new();
        for (i, _) in text.match_indices("fn ") {
            let before = text[..i].chars().next_back();
            if before.is_some_and(|c| c.is_alphanumeric() || c == '_') {
                continue;
            }
            let name: String = text[i + 3..]
                .chars()
                .take_while(|c| c.is_ascii_alphanumeric() || *c == '_')
                .collect();
            if !name.is_empty() {
                out.insert(name);
            }
        }
        out
    }
    fn walk(dir: &Path, out: &mut BTreeSet<String>) {
        for entry in std::fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                walk(&path, out);
            } else if path.extension().is_some_and(|x| x == "rs") {
                for name in in_file(&path) {
                    out.insert(format!(
                        "{}::{name}",
                        path.strip_prefix(root()).unwrap().display()
                    ));
                    out.insert(name);
                }
            }
        }
    }
    let mut out = BTreeSet::new();
    walk(&root().join("src"), &mut out);
    walk(&root().join("tests"), &mut out);
    let manifest = std::fs::read_to_string(root().join("Cargo.toml")).unwrap();
    for target in manifest.split("[[test]]").skip(1) {
        let value = |key: &str| {
            target
                .lines()
                .find_map(|line| {
                    line.strip_prefix(key)
                        .and_then(|s| s.strip_prefix(" = \""))
                        .and_then(|s| s.strip_suffix('"'))
                })
                .unwrap()
        };
        let binary = value("name");
        let path = root().join(value("path"));
        for name in in_file(&path) {
            out.insert(format!("{binary}::{name}"));
        }
        let mut module_file = None;
        for line in std::fs::read_to_string(&path).unwrap().lines() {
            if let Some(relative) = line
                .strip_prefix("#[path = \"")
                .and_then(|s| s.strip_suffix("\"]"))
            {
                module_file = Some(path.parent().unwrap().join(relative));
            } else if let Some(module) = line.strip_prefix("mod ").and_then(|s| s.strip_suffix(';'))
                && let Some(file) = module_file.take()
            {
                for name in in_file(&file) {
                    out.insert(format!("{binary}::{module}::{name}"));
                }
            }
        }
    }
    out
}

/// The names a `rust_test` value cites: a qualified path, a bare name, or
/// one in a list separated by `;`, `,` or spaces; file paths alone (`tests/x.rs`) name no test.
fn cited(value: &str) -> Vec<String> {
    value
        .split([';', ',', ' '])
        .map(|p| {
            p.trim()
                .trim_matches(|c| matches!(c, '`' | '(' | ')' | '.'))
        })
        .filter(|p| !p.is_empty() && !p.ends_with(".rs") && *p != "and")
        .map(|p| {
            // Source citations may include an inline unit-test module. Validate
            // their file and function; integration citations also bind the target
            // and original source module to the explicit Cargo module graph.
            if p.starts_with("src/")
                && let Some((file, name)) = p.split_once("::")
            {
                format!("{file}::{}", name.rsplit("::").next().unwrap())
            } else {
                p.to_string()
            }
        })
        .filter(|n| {
            n.chars()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || "_:/ .".contains(c))
        })
        .collect()
}

/// #43 item 2 (stream K8): every rule has a known status and says what backs it; no pending rule
/// is left without a note saying what remains.
#[test]
fn every_rule_says_what_backs_its_status() {
    let mut problems = Vec::new();
    let mut ids = BTreeSet::new();
    for file in FILES {
        for e in entries(file) {
            if !ids.insert(e.id.clone()) {
                problems.push(format!("{file}: {} appears twice", e.id));
            }
            let status = field(&e, "status");
            let needs = match status {
                "pending" => "rust_note",
                "ported" => "rust_test",
                "translated" => "rust_rule",
                "retired" => "why",
                other => {
                    problems.push(format!("{file}: {} has status {other:?}", e.id));
                    continue;
                }
            };
            if field(&e, needs).is_empty() {
                problems.push(format!("{file}: {} is {status} with no {needs}", e.id));
            }
            if !CLASSES.contains(&field(&e, "class")) {
                problems.push(format!(
                    "{file}: {} has class {:?}",
                    e.id,
                    field(&e, "class")
                ));
            }
        }
    }
    assert!(problems.is_empty(), "{}", problems.join("\n"));
}

/// Every test a rule cites exists, so a renamed test cannot leave a rule pointing at nothing.
#[test]
fn every_cited_test_exists() {
    let known = functions();
    let mut missing = Vec::new();
    for file in FILES {
        for e in entries(file) {
            for name in cited(field(&e, "rust_test")) {
                if !known.contains(&name) {
                    missing.push(format!("{file}: {} cites {name}", e.id));
                }
            }
        }
    }
    assert!(missing.is_empty(), "{}", missing.join("\n"));
}

/// The README's two tables (entries by class, and statuses) match the files, totals included.
#[test]
fn the_readme_tables_match_the_files() {
    let readme = std::fs::read_to_string(root().join("docs/rules/README.md")).unwrap();
    // A row of numbers per file, keyed by the table's header (its column names).
    let mut rows: BTreeMap<(String, String), Vec<usize>> = BTreeMap::new();
    let mut header = String::new();
    for line in readme.lines() {
        let cells: Vec<&str> = line
            .trim()
            .trim_matches('|')
            .split('|')
            .map(str::trim)
            .collect();
        if !line.starts_with('|') || cells.len() < 2 {
            continue;
        }
        if cells[0] == "File" {
            header = cells[1..].join(",");
            continue;
        }
        let name = cells[0].trim_matches(|c| c == '`' || c == '*').to_string();
        let numbers: Vec<usize> = cells[1..]
            .iter()
            .filter_map(|c| c.trim_matches('*').parse().ok())
            .collect();
        if !numbers.is_empty() {
            rows.insert((header.clone(), name), numbers);
        }
    }
    let by_status = STATUSES.map(|s| s[..1].to_uppercase() + &s[1..]).join(",");
    let by_class = format!(
        "Source,Entries,{}",
        CLASSES.map(|s| s[..1].to_uppercase() + &s[1..]).join(",")
    );
    let mut totals = (vec![0; 4], vec![0; 5]);
    for file in FILES {
        let e = entries(file);
        let statuses: Vec<usize> = STATUSES
            .iter()
            .map(|s| e.iter().filter(|e| field(e, "status") == *s).count())
            .collect();
        let mut classes = vec![e.len()];
        classes.extend(
            CLASSES
                .iter()
                .map(|c| e.iter().filter(|e| field(e, "class") == *c).count()),
        );
        assert_eq!(
            rows.get(&(by_status.clone(), file.to_string())),
            Some(&statuses),
            "status row of {file}"
        );
        assert_eq!(
            rows.get(&(by_class.clone(), file.to_string())),
            Some(&classes),
            "class row of {file}"
        );
        for (t, n) in totals.0.iter_mut().zip(&statuses) {
            *t += n;
        }
        for (t, n) in totals.1.iter_mut().zip(&classes) {
            *t += n;
        }
    }
    assert_eq!(rows.get(&(by_status, "All".into())), Some(&totals.0));
    assert_eq!(rows.get(&(by_class, "All".into())), Some(&totals.1));
}

fn font() -> Font {
    Font::new(api::MONO.to_vec()).unwrap()
}

fn native(path: &Path) -> Analysis {
    model::analyse_native(path).unwrap().1
}

/// Where a placed linear dimension's line lies: its view, the strip side (`above`, `below`,
/// `left`, `right` of the part's centre on the page) and its distance from that centre; none for
/// an aligned line.
fn strip(
    plan: &Plan,
    d: &draftwright::dims::PlannedDimension,
) -> Option<(String, &'static str, f64)> {
    let placed = d.placed.as_ref()?;
    let [a, b] = [placed.arrows.first()?[0], placed.arrows.get(1)?[0]];
    let c = plan.views.iter().find(|v| v.id == d.view)?.at;
    if (a[1] - b[1]).abs() < 1e-6 {
        let side = if a[1] > c[1] { "above" } else { "below" };
        Some((d.view.clone(), side, (a[1] - c[1]).abs()))
    } else if (a[0] - b[0]).abs() < 1e-6 {
        let side = if a[0] > c[0] { "right" } else { "left" };
        Some((d.view.clone(), side, (a[0] - c[0]).abs()))
    } else {
        None
    }
}

/// A drawn linear dimension in its strip: distance from the view's centre, rank (0 a size, 1 a
/// location, 2 the overall extent), value and ID.
type Lined<'a> = (f64, u8, f64, &'a str);

/// ARCH0009-5: within each strip beside a view, feature sizes sit nearest the view, datum
/// locations (hole locations and the height or position ladder's rungs) nest outward in
/// ascending distance from the datum, and the overall extent is outermost. Checked on the
/// drawn lines of the plate fixture and, with the corpus, on parts whose strips mix all three
/// (NIST FTC-06's ladder and plan strip, mfcadpp 10000's side strip).
#[test]
fn sizes_sit_nearest_then_locations_by_distance_then_the_overall() {
    let mut parts = vec![(
        "plate".to_string(),
        native(&root().join("tests/fixtures/parts/plate.step")),
    )];
    if let Some(dir) = corpus::dir() {
        for f in ["nist/nist_ftc_06_asme1_rd.stp", "mfcadpp/10000.step"] {
            parts.push((f.into(), native(&dir.join(f))));
        }
    }
    let mut mixed = 0;
    for (name, analysis) in &parts {
        // Keep the complete FTC-06 corridor fixture on its historical paper.
        let mut spec = Spec::default();
        if name.contains("ftc_06") {
            spec.sheet.size = Some("A1".into());
        }
        let plan = model::plan_recovered(analysis, &spec).unwrap();
        let mut strips: BTreeMap<(String, &str), Vec<Lined>> = BTreeMap::new();
        for d in plan.dimensions.iter().filter(|d| d.kind == "linear") {
            let Some((view, side, distance)) = strip(&plan, d) else {
                continue;
            };
            // What the item measures, from its ID (`d:<feature>/<parameter>`).
            let parameter = d.id.rsplit_once('/').map_or("", |(_, p)| p);
            let rank = if d.id.starts_with("d:envelope/") {
                2
            } else if parameter.starts_with("location")
                || parameter.starts_with("step_height")
                || parameter.starts_with("step_position")
            {
                1
            } else {
                0
            };
            strips
                .entry((view, side))
                .or_default()
                .push((distance, rank, d.value, &d.id));
        }
        for (key, mut items) in strips {
            items.sort_by(|a, b| a.0.total_cmp(&b.0));
            let ranks: Vec<u8> = items.iter().map(|i| i.1).collect();
            let mut sorted = ranks.clone();
            sorted.sort();
            assert_eq!(ranks, sorted, "{name} {key:?}: {items:?}");
            let locations: Vec<f64> = items.iter().filter(|i| i.1 == 1).map(|i| i.2).collect();
            assert!(
                locations.windows(2).all(|w| w[0] <= w[1] + 1e-9),
                "{name} {key:?}: {items:?}"
            );
            if sorted.first() != sorted.last() {
                mixed += 1;
            }
        }
    }
    if parts.len() > 1 {
        assert!(
            mixed >= 3,
            "too few strips mix sizes, locations and extents: {mixed}"
        );
    }
}

/// ADR2-U1: text is fixed paper-space input. Every annotation's text is drawn at the one 3 mm
/// size, on the automatic sheet (A1 1:1) and when the sheet is pinned one size smaller with no
/// hole table (scattered_holes at A2 1:1, where locations too close to read are left out and
/// reported, `location_ref_dropped`): nothing is shrunk to fit.
#[test]
fn annotation_text_keeps_its_size_however_crowded_the_sheet() {
    let path = root().join("tests/fixtures/parts/scattered_holes.step");
    let (part, analysis) = model::analyse_native(&path).unwrap();
    let crowded: Spec = serde_json::from_value(
        serde_json::json!({"hole_table": "never", "sheet": {"size": "A2", "scale": "1:1"}}),
    )
    .unwrap();
    for spec in [Spec::default(), crowded] {
        let plan = model::plan_recovered(&analysis, &spec).unwrap();
        let drawing = model::render(&part, &analysis, &plan, &font()).unwrap();
        let mut runs = 0;
        for a in &drawing.annotations {
            for &r in &a.runs {
                runs += 1;
                assert_eq!(
                    drawing.text[r].size,
                    draftwright::dims::TEXT,
                    "{} at {} {}: {:?}",
                    a.name,
                    plan.sheet.size,
                    plan.sheet.scale,
                    drawing.text[r].text
                );
            }
        }
        assert!(
            runs > 30,
            "{runs} runs at {} {}",
            plan.sheet.size,
            plan.sheet.scale
        );
    }
}

/// The issue part *name* (`tests/fixtures/recognition/issues/`) with its recorded recognition.
fn issue_part(name: &str) -> Analysis {
    let dir = root().join("tests/fixtures/recognition/issues");
    let (part, mut a) = model::analyse(&dir.join(format!("{name}.step"))).unwrap();
    let mut json = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(dir.join(format!("{name}.json.gz"))).unwrap())
        .read_to_string(&mut json)
        .unwrap();
    model::recognise(&part, &mut a, &json).unwrap();
    a
}

/// ISSUE-917-a: an open U-channel's defining chain is not redundant. Of its two walls exactly one
/// thickness is drawn (the lower one, 10 rather than 15, when they differ) and the other is
/// derived from the overall width and the channel width, recorded as covered by the drawn one;
/// the base's thickness is drawn once; the channel has one width.
#[test]
fn an_open_channel_draws_one_wall_and_derives_the_other() {
    for (name, wall) in [("u_channel", "12.5"), ("u_channel_asymmetric", "10")] {
        let plan = model::plan(&issue_part(name), &Spec::default()).unwrap();
        let thicknesses: Vec<(&str, &str)> = plan
            .dimensions
            .iter()
            .filter(|d| d.id.ends_with("/thickness.length"))
            .map(|d| (d.id.as_str(), d.label.as_str()))
            .collect();
        assert_eq!(
            thicknesses,
            [
                ("d:f:plate:f1/thickness.length", wall),
                ("d:f:plate:f2/thickness.length", "12")
            ],
            "{name}"
        );
        assert!(
            plan.omitted
                .iter()
                .any(|o| o.id == "d:f:plate:f3/thickness.length"
                    && o.state
                        == draftwright::items::State::Covered {
                            by: "d:f:plate:f1/thickness.length".into()
                        }),
            "{name}: {:?}",
            plan.omitted
        );
        let widths = plan
            .dimensions
            .iter()
            .filter(|d| d.id.ends_with("/channel_width.length"))
            .count();
        assert_eq!(widths, 1, "{name}");
    }
}
