//! Oriented slot and through-step coverage (`lint::oriented_slots`, `lint::steps`) on the parts
//! that have them, against Python draftwright's lint (origin/main) on its own sheets of them.
//!
//! No captured corpus sheet has an oriented slot or a through step running along x or y, so the
//! parts are:
//!
//! - **h467** (`mfcadpp_holdout/467.step`, the one corpus part with an oriented slot) and
//!   quiddity's golden **oriented_slots** plate (`tests/fixtures/recognition/golden/`: three
//!   slots, one oriented slot array): their recognition by draftwright's quiddity 0.3.9 and
//!   draftwright's sheet facts are in `tests/fixtures/lint_family_rest/`, written by
//!   `tools/recognise.py` and `tests/fixtures/recognition/slot_patterns/capture.py` with
//!   `tools/capture_sheet_facts.py`, run with draftwright's venv on origin/main (2026-10-09);
//! - the step fixtures (`tests/fixtures/recognition/steps/`): a step along y whose legs
//!   draftwright leaves to its ladder and overall extents (`stepped_block`), and through steps
//!   along z.
//!
//! On each, lint judges Python's sheet (each captured annotation a mark: its printed text, and the
//! measurements it draws, joined to the Analysis exactly) and the port's own drawing; its
//! oriented-slot and through-step findings are compared with Python's lint on its sheet. Every
//! difference is listed, with a verdict and the reason, in `tests/fixtures/known_lint.json`
//! (`family_rest`), and a listed one that has gone away must be taken off.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::BTreeMap;
use std::io::Read as _;
use std::path::{Path, PathBuf};

use serde::Deserialize;
use serde_json::Value;

use draftwright::bridge::holes::{frame, point, to_part};
use draftwright::lint::{self, Mark};
use draftwright::model::{self, Analysis};
use draftwright::output::Claim;

fn fixture(rel: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(rel)
}

fn gunzip(path: &Path) -> String {
    let mut text = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_string(&mut text)
        .unwrap();
    text
}

/// The finding codes compared here.
const FAMILIES: [&str; 2] = ["oriented_slot_requirement_", "through_step_requirement_"];

/// A difference between the two lints, by part, sheet and subject.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    part: String,
    /// `python` (lint on Python's sheet) or `own` (lint on the port's own drawing).
    sheet: String,
    /// The Analysis feature.
    subject: String,
    /// Each side's finding code, or none, and how many requirements of the subject differ so.
    python: Option<String>,
    rust: Option<String>,
    count: usize,
    /// `rust-correct`, `rust-wrong`, `equivalent`, `undetermined` or `not-applicable`.
    verdict: String,
    reason: String,
}

/// A difference as the test counts it: the part, the sheet, the subject and each side's code.
type Difference = (String, String, String, Option<String>, Option<String>);

/// A size `name=` in a Python feature key (`oriented_slot@(…)/x[width=2.000,length=4.543]`).
fn key_size(key: &str, name: &str) -> Option<f64> {
    let sizes = key.split_once('[')?.1.strip_suffix(']')?;
    sizes
        .split(',')
        .find_map(|kv| kv.strip_prefix(name)?.strip_prefix('=')?.parse().ok())
}

/// The Analysis feature a Python feature names, when exactly one fits: an oriented slot by its
/// centre (Python's origin, within 2e-3) and its width and length (within 1.5e-3); a through step
/// by its `at` (within 2e-3). The ladder and the overall extents are named as the port names them
/// (`step_level`, `envelope`).
fn join(a: &Analysis, f: &facts::Feature) -> Option<String> {
    match f.kind.as_str() {
        "envelope" | "step_level" => return Some(f.kind.clone()),
        "oriented_slot" | "through_step" => {}
        _ => return None,
    }
    let frame = frame(a);
    let origin = f.origin?;
    let key = f.key.as_deref().unwrap_or("");
    let fits: Vec<&String> = a
        .features
        .iter()
        .filter(|g| g.kind == f.kind)
        .filter(|g| {
            let at = if f.kind == "oriented_slot" {
                "center"
            } else {
                "at"
            };
            let Some(p) = point(g.parameters.get(at)).map(|l| to_part(&frame, l)) else {
                return false;
            };
            let sized = ["width", "length"].iter().all(|n| match key_size(key, n) {
                Some(v) if f.kind == "oriented_slot" => g
                    .parameters
                    .get(*n)
                    .and_then(Value::as_f64)
                    .is_some_and(|w| (w - v).abs() <= 1.5e-3),
                _ => true,
            });
            sized && (0..3).all(|k| (p[k] - origin[k]).abs() <= 2e-3)
        })
        .map(|g| &g.id)
        .collect();
    match fits[..] {
        [id] => Some(id.clone()),
        _ => None,
    }
}

/// Python's sheet as marks, and how many claims on the families compared could not be joined.
fn marks(a: &Analysis, f: &facts::Facts) -> (Vec<Mark>, usize) {
    let mut unjoined = 0;
    let marks = f
        .annotations
        .iter()
        .map(|n| {
            let claims = n
                .measurements
                .iter()
                .filter_map(|m| {
                    let feature = m.feature.as_ref()?;
                    let joined = join(a, feature);
                    if joined.is_none()
                        && ["oriented_slot", "through_step"].contains(&&*feature.kind)
                    {
                        unjoined += 1;
                    }
                    Some(Claim {
                        feature: joined?,
                        parameter: m.parameter.clone(),
                    })
                })
                .collect();
            Mark {
                precision: None,
                units: Default::default(),
                name: n.name.clone(),
                view: n.view.clone(),
                text: n.text.join(" "),
                claims,
            }
        })
        .collect();
    (marks, unjoined)
}

/// The part *name*: its STEP file, its recognition and Python's sheet facts.
fn part(name: &str) -> Option<(PathBuf, String, facts::Facts)> {
    let steps = ["stepped_block", "rectangular_through_step"];
    let (file, recognition, facts) = if steps.contains(&name) {
        let dir = fixture("recognition/steps");
        (
            dir.join(format!("{name}.step")),
            dir.join(format!("{name}.json.gz")),
            dir.join(format!("{name}.facts.json.gz")),
        )
    } else {
        let file = match name {
            "h467" => corpus::dir()?.join("mfcadpp_holdout/467.step"),
            _ => fixture(&format!("recognition/golden/{name}.step")),
        };
        let dir = fixture("lint_family_rest");
        (
            file,
            dir.join(format!("{name}.json.gz")),
            dir.join(format!("{name}.facts.json.gz")),
        )
    };
    Some((file, gunzip(&recognition), facts::load_file(&facts)))
}

/// The findings of the families compared, by subject: its code, for each requirement.
fn by_subject<'a>(
    codes: impl Iterator<Item = (String, String)> + 'a,
) -> BTreeMap<String, Vec<String>> {
    let mut out: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (subject, code) in codes {
        if FAMILIES.iter().any(|f| code.starts_with(f)) {
            out.entry(subject).or_default().push(code);
        }
    }
    out
}

/// Lint on Python's sheets and on the port's own drawings of the parts with an oriented slot or a
/// through step, against Python's lint on its sheets.
#[test]
fn oriented_slots_and_through_steps_match_pythons_lint_or_are_known() {
    if corpus::dir().is_none() {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    }
    let known: Vec<Known> = serde_json::from_value(
        serde_json::from_str::<Value>(
            &std::fs::read_to_string(fixture("known_lint.json")).unwrap(),
        )
        .unwrap()["family_rest"]
            .clone(),
    )
    .unwrap();
    let font = draftwright::text::Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let names = [
        "h467",
        "oriented_slots",
        "stepped_block",
        "rectangular_through_step",
    ];
    let mut found: BTreeMap<Difference, usize> = BTreeMap::new();
    for name in names {
        let (file, json, f) = part(name).unwrap();
        let (part, mut a) = model::analyse(&file).unwrap();
        assert_eq!(a.binding.source_sha256, f.binding.sha256, "{name}");
        model::recognise(&part, &mut a, &json).unwrap();
        let pythons: Vec<String> = f
            .lint
            .iter()
            .filter(|i| FAMILIES.iter().any(|p| i.code.starts_with(p)))
            .map(|i| i.code.clone())
            .collect();
        // Python's findings carry no feature to join; none of these parts has one, so each of
        // the port's findings is a difference.
        assert!(pythons.is_empty(), "{name}: {pythons:?}");
        if name == "stepped_block" {
            // Its step runs along y: both legs are stated by the ladder and the extents alone.
            let step = a
                .features
                .iter()
                .find(|g| g.kind == "through_step")
                .unwrap();
            let alts = lint::steps::alternatives(&a, step);
            assert!(alts.len() == 2 && alts.iter().all(|(_, alts)| !alts.is_empty()));
        }
        let (marks, unjoined) = marks(&a, &f);
        assert_eq!(unjoined, 0, "{name}: a claim joins no feature, or several");
        let on_pythons = lint::check(&a, &marks);
        let plan = model::plan(&a, &model::Spec::default()).unwrap();
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        let on_own = lint::lint(&a, &drawing);
        for (sheet, issues) in [("python", on_pythons), ("own", on_own)] {
            let mine = by_subject(
                issues
                    .into_iter()
                    .map(|i| (i.subjects.first().cloned().unwrap_or_default(), i.code)),
            );
            for (subject, codes) in mine {
                for code in codes {
                    *found
                        .entry((name.into(), sheet.into(), subject.clone(), None, Some(code)))
                        .or_default() += 1;
                }
            }
        }
    }
    for k in &known {
        assert!(
            [
                "rust-correct",
                "rust-wrong",
                "equivalent",
                "undetermined",
                "not-applicable"
            ]
            .contains(&k.verdict.as_str())
                && ["python", "own"].contains(&k.sheet.as_str())
                && !k.reason.is_empty(),
            "{k:?}"
        );
    }
    let listed: BTreeMap<Difference, usize> = known
        .iter()
        .map(|k| {
            (
                (
                    k.part.clone(),
                    k.sheet.clone(),
                    k.subject.clone(),
                    k.python.clone(),
                    k.rust.clone(),
                ),
                k.count,
            )
        })
        .collect();
    assert_eq!(listed.len(), known.len(), "a difference is listed twice");
    assert_eq!(
        found, listed,
        "the differences (left) are not known_lint.json's family_rest (right)"
    );
}
