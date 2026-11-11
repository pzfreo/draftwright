//! The trust rules of the issue regressions (`docs/rules/issues.yaml`, class `trust`) that the
//! port can check: who owns a requirement, what earns credit for it, honest and deterministic
//! diagnostics, and refusals before any work. Each test names the rules it checks; a part (and
//! its recognition) and a spec go in, a property of the plan, its problems, lint's findings on
//! the drawing or the output comes out.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use draftwright::api;
use draftwright::lint::{self, LintIssue, Mark};
use draftwright::model::{self, Analysis, DimensionSource, DimensionSpec, Plan, Spec};
use draftwright::output::{Claim, Drawing};
use draftwright::text::Font;
use serde_json::{Value, json};

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

fn font() -> Font {
    Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
        .unwrap()
}

fn recognition(name: &str) -> Value {
    serde_json::from_str(
        &std::fs::read_to_string(fixture(&format!("recognition/{name}.json"))).unwrap(),
    )
    .unwrap()
}

/// The fixture part *name* with its recognition.
fn analysed(name: &str) -> (draftwright::haecceity::Part, Analysis) {
    let (part, mut a) = model::analyse(&fixture(&format!("parts/{name}.step"))).unwrap();
    model::recognise(&part, &mut a, &recognition(name).to_string()).unwrap();
    (part, a)
}

const FIXTURES: [&str; 4] = ["plate", "turned", "prismatic", "two_grids"];

/// The sampled sheets' corpus parts (`tests/fixtures/recognition/sheets/`), each with the
/// features draftwright's own recogniser found for it and checked to be the very file the
/// sheet was captured from (ISSUE-1336-a); read once for every test here, and none when the
/// corpus is missing. All 13, ctc04 among them since the bridge reads it
/// (d-bridge-area-tolerance).
fn sampled() -> &'static [(String, Analysis)] {
    static SAMPLED: OnceLock<Vec<(String, Analysis)>> = OnceLock::new();
    SAMPLED.get_or_init(|| {
        let Some(dir) = corpus::dir() else {
            eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
            return Vec::new();
        };
        let mut names: Vec<String> = std::fs::read_dir(fixture("recognition/sheets"))
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .filter_map(|f| f.strip_suffix(".json.gz").map(String::from))
            .collect();
        names.sort();
        names
            .into_iter()
            .map(|name| {
                let f = facts::load(&name);
                let (part, mut a) = model::analyse(&dir.join(&f.binding.file)).unwrap();
                assert_eq!(
                    a.binding.source_sha256, f.binding.sha256,
                    "{name}: the corpus file is not the one the sheet was captured from"
                );
                model::recognise(&part, &mut a, &sheet_recognition(&name))
                    .unwrap_or_else(|e| panic!("{name}: {e}"));
                (name, a)
            })
            .collect()
    })
}

fn sheet_recognition(name: &str) -> String {
    let mut json = String::new();
    flate2::read::GzDecoder::new(
        std::fs::File::open(fixture(&format!("recognition/sheets/{name}.json.gz"))).unwrap(),
    )
    .read_to_string(&mut json)
    .unwrap();
    json
}

/// The fixtures and the sampled parts, analysed.
fn every_part() -> Vec<(String, Analysis)> {
    let mut parts: Vec<(String, Analysis)> = FIXTURES
        .iter()
        .map(|n| (n.to_string(), analysed(n).1))
        .collect();
    parts.extend(sampled().iter().cloned());
    parts
}

fn scratch(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("issue_trust")
        .join(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn listing(dir: &Path) -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(dir)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    names.sort();
    names
}

/// A spec that omits the dimensions *omit* (in `auto`).
fn omitting(omit: &[&str]) -> Spec {
    let mut spec = Spec::default();
    for id in omit {
        spec.dimensions.insert(
            id.to_string(),
            DimensionSpec {
                omit: true,
                ..Default::default()
            },
        );
    }
    spec
}

/// A spec of an empty authored set: no dimensions at all.
fn nothing() -> Spec {
    Spec {
        dimension_source: DimensionSource::Authored,
        ..Spec::default()
    }
}

fn drawn(part: &draftwright::haecceity::Part, analysis: &Analysis, plan: &Plan) -> Drawing {
    model::render(part, analysis, plan, &font()).unwrap()
}

/// Lint's findings as `code feature parameter`, one per requirement they name.
fn findings(issues: &[LintIssue]) -> BTreeSet<String> {
    issues
        .iter()
        .flat_map(|i| {
            i.requirements
                .iter()
                .map(move |r| format!("{} {} {}", i.code, r.feature, r.parameter))
        })
        .collect()
}

fn holes_only(issues: Vec<LintIssue>) -> BTreeSet<String> {
    findings(&issues)
        .into_iter()
        .filter(|f| f.starts_with("hole_requirement_"))
        .collect()
}

fn mark(text: &str, claims: &[(&str, &str)]) -> Mark {
    Mark {
        units: Default::default(),
        name: format!("m:{text}"),
        view: Some("v:plan".into()),
        text: text.into(),
        claims: claims
            .iter()
            .map(|(f, p)| Claim {
                feature: f.to_string(),
                parameter: p.to_string(),
            })
            .collect(),
    }
}

/// ISSUE-1143-a, ISSUE-1369-a and ISSUE-1202-a, the hole callouts: the part owns the hole
/// requirements and only the drawn ink earns them. Lint counts the requirements from the
/// Analysis alone, so no spec shrinks them: with no dimensions at all, every requirement of
/// every hole group is reported missing, and omitting the callout gives the same findings. A
/// callout the plan approved but that never reached the sheet earns nothing; nor does one whose
/// ink contradicts the part, or that draws no text. Without recognition there is no hole
/// requirement at all.
#[test]
fn the_part_owns_the_hole_requirements_and_only_the_drawn_ink_earns_them() {
    let (part, analysis) = analysed("plate");
    let callout = "d:f:hole_pattern:f6/bore.diameter";
    let lint_of = |plan: &Plan| holes_only(lint::lint(&analysis, &drawn(&part, &analysis, plan)));

    let full = model::plan(&analysis, &Spec::default()).unwrap();
    assert!(lint_of(&full).is_empty(), "{:?}", lint_of(&full));

    // The denominator: every requirement of every group, from the Analysis.
    let groups = lint::holes::groups(&analysis);
    assert_eq!(groups.len(), 1);
    let every: BTreeSet<String> = groups
        .iter()
        .flat_map(|g| {
            g.requirements().into_iter().flat_map(move |(p, _)| {
                g.members
                    .iter()
                    .map(move |m| format!("hole_requirement_missing {m} {p}"))
            })
        })
        .collect();
    assert_eq!(every.len(), 12, "{every:?}");
    let none = model::plan(&analysis, &nothing()).unwrap();
    assert!(none.dimensions.is_empty());
    assert_eq!(lint_of(&none), every);
    assert_eq!(
        lint_of(&model::plan(&analysis, &omitting(&[callout])).unwrap()),
        every
    );

    // Approved, but never drawn.
    let mut undrawn = full.clone();
    let i = undrawn
        .dimensions
        .iter()
        .position(|d| d.id == callout)
        .unwrap();
    undrawn.dimensions[i].placed = None;
    assert_eq!(lint_of(&undrawn), every);

    // Drawn, but its ink contradicts the part, or states nothing.
    let drawing = drawn(&part, &analysis, &full);
    let runs = drawing
        .annotations
        .iter()
        .find(|a| a.name == callout)
        .unwrap()
        .runs
        .clone();
    assert_eq!(drawing.text[runs[0]].text, "4× ⌀6 THRU");
    let mut wrong = drawing.clone();
    wrong.text[runs[0]].text = "4× ⌀8 THRU".into();
    let found = holes_only(lint::lint(&analysis, &wrong));
    let unverifiable: BTreeSet<&str> = found
        .iter()
        .filter_map(|f| f.strip_prefix("hole_requirement_unverifiable "))
        .collect();
    assert_eq!(unverifiable.len(), 4, "{found:?}");
    assert!(unverifiable.iter().all(|f| f.ends_with(" bore.diameter")));
    let mut blank = drawing.clone();
    for a in &mut blank.annotations {
        if a.name == callout {
            a.runs.clear();
        }
    }
    let found = holes_only(lint::lint(&analysis, &blank));
    assert_eq!(found.len(), 12, "{found:?}");
    assert!(
        found
            .iter()
            .all(|f| !f.ends_with(" bore.diameter")
                || f.starts_with("hole_requirement_unverifiable"))
    );

    // No recognition, no hole requirements.
    let (bare_part, bare) = model::analyse(&fixture("parts/plate.step")).unwrap();
    assert!(bare.features.is_empty());
    let plan = model::plan(&bare, &Spec::default()).unwrap();
    assert!(lint::lint(&bare, &drawn(&bare_part, &bare, &plan)).is_empty());
}

/// ISSUE-1126-a, for what lint checks: asking for less never buys silence. Omitting any one
/// dimension never takes a lint finding away, and omitting one that states a requirement lint
/// counts adds a finding for it; dropping every dimension adds findings, of codes the full
/// drawing did not have.
#[test]
fn asking_for_less_never_buys_silence() {
    for name in FIXTURES {
        let (part, analysis) = analysed(name);
        let lint_of = |spec: &Spec| {
            let plan = model::plan(&analysis, spec).unwrap();
            lint::lint(&analysis, &drawn(&part, &analysis, &plan))
        };
        let full = lint_of(&Spec::default());
        let before = findings(&full);
        let bare = lint_of(&nothing());
        // What lint counts: every requirement a drawing with no dimensions leaves unstated.
        let counted: BTreeSet<(String, String)> = bare
            .iter()
            .flat_map(|i| i.requirements.iter())
            .map(|r| (r.feature.clone(), r.parameter.clone()))
            .collect();
        assert!(!counted.is_empty(), "{name}");
        assert!(
            bare.len() > full.len(),
            "{name}: {} findings with nothing, {} with everything",
            bare.len(),
            full.len()
        );
        let codes = |issues: &[LintIssue]| -> BTreeSet<String> {
            issues.iter().map(|i| i.code.clone()).collect()
        };
        assert!(
            !codes(&bare).is_subset(&codes(&full)),
            "{name}: dropping everything raised no new code"
        );

        let plan = model::plan(&analysis, &Spec::default()).unwrap();
        for d in &plan.dimensions {
            let after = findings(&lint_of(&omitting(&[&d.id])));
            assert!(
                after.is_superset(&before),
                "{name}: omitting {} took {:?} away",
                d.id,
                before.difference(&after).collect::<Vec<_>>()
            );
            let states = d
                .measurements
                .iter()
                .any(|m| counted.contains(&(m.feature.clone(), m.parameter.clone())));
            if states && d.measurements.iter().any(|m| m.feature == "envelope") && after == before {
                // A newly explicit overall may still be proven by its complete
                // turned chain after omission; prove that alternate printed evidence.
                let less = model::plan(&analysis, &omitting(&[&d.id])).unwrap();
                let drawing = drawn(&part, &analysis, &less);
                let marks = lint::marks(&drawing);
                for m in d
                    .measurements
                    .iter()
                    .filter(|m| m.feature == "envelope" && m.parameter.ends_with(".length"))
                {
                    let o = lint::envelope::outcomes(&analysis, &marks)
                        .into_iter()
                        .find(|o| o.parameter == m.parameter)
                        .unwrap();
                    assert_eq!(o.state, lint::turned::State::Placed);
                    assert!(
                        o.annotations.len() > 1,
                        "{name}: {} has no independently drawn chain",
                        d.id
                    );
                }
                continue;
            }
            if states {
                assert!(
                    after.len() > before.len(),
                    "{name}: omitting {} cost no finding",
                    d.id
                );
            }
        }
    }
}

/// ISSUE-1143-b, in part: hole correspondence is by feature ID. One group's callout, claiming
/// every member, does not certify another group of the same size, and text that states the
/// right sizes but claims no hole certifies none (there is no fallback by value or position).
/// A callout claiming one member of a group certifies the group's common sizes (one truthful
/// statement, as draftwright's `_state`) but not its quantity, whatever quantity it prints: the
/// members it does not claim are reported (draftwright's
/// `test_one_singleton_cannot_claim_an_entire_multi_owner_group`).
#[test]
fn a_callout_certifies_only_the_holes_it_claims() {
    let (_, analysis) = analysed("two_grids");
    let groups = lint::holes::groups(&analysis);
    assert_eq!(groups.len(), 4);
    assert!(groups.iter().all(|g| g.sizes == groups[0].sizes));
    let missing = |marks: &[Mark]| -> BTreeSet<String> {
        lint::check(&analysis, marks)
            .iter()
            .filter(|i| i.code.starts_with("hole_requirement_"))
            .map(|i| i.subjects[0].clone())
            .collect()
    };
    let firsts: BTreeSet<String> = groups.iter().map(|g| g.members[0].clone()).collect();
    assert_eq!(missing(&[]), firsts);

    assert_eq!(groups[0].members.len(), 3);
    let all: Vec<(&str, &str)> = groups[0]
        .members
        .iter()
        .map(|m| (m.as_str(), "bore.diameter"))
        .collect();
    let claimed = missing(&[mark("3× ⌀6 THRU", &all)]);
    let mut others = firsts.clone();
    others.remove(&groups[0].members[0]);
    assert_eq!(claimed, others);

    // One member claimed, the group's quantity printed: only the quantity is missing, and its
    // finding names the two members nothing counts.
    let one = [mark(
        "3× ⌀6 THRU",
        &[(groups[0].members[0].as_str(), "bore.diameter")],
    )];
    let issues: Vec<LintIssue> = lint::check(&analysis, &one)
        .into_iter()
        .filter(|i| i.code.starts_with("hole_requirement_"))
        .filter(|i| i.subjects[0] == groups[0].members[0])
        .collect();
    assert_eq!(
        issues
            .iter()
            .map(|i| (i.code.as_str(), i.requirements[0].parameter.as_str()))
            .collect::<Vec<_>>(),
        [("hole_requirement_missing", "grouping.count")]
    );
    let evidence = issues[0].evidence.as_deref().unwrap();
    assert!(
        evidence.contains(&format!(
            "{}, {} are counted by no callout",
            groups[0].members[1], groups[0].members[2]
        )),
        "{evidence}"
    );
    assert_eq!(missing(&one), firsts);
    // Each member claimed by its own callout, counting itself: the quantity is stated.
    let each: Vec<Mark> = groups[0]
        .members
        .iter()
        .map(|m| mark("⌀6 THRU", &[(m.as_str(), "bore.diameter")]))
        .collect();
    assert_eq!(missing(&each), others);

    // Every size printed, no hole claimed.
    assert_eq!(missing(&[mark("12× ⌀6 THRU", &[])]), firsts);
    // A claim on a part that has no such hole certifies nothing.
    assert_eq!(
        missing(&[mark("3× ⌀6 THRU", &[("f:hole:99", "bore.diameter")])]),
        firsts
    );
}

/// ISSUE-1217-a, in part, and ISSUE-1142-b: a claim is verified against the ink. A callout that
/// claims a hole's diameter is credited only when the term that states the diameter (the number
/// after its `⌀`) rounds, at its printed precision, from the part's value: neither a number that
/// merely contains the value's digits, nor one at a finer precision that does not round from it,
/// nor another number of the callout (a repeat count, a grid's shape, a depth) of the same value
/// (draftwright's `test_a_repeat_count_cannot_confirm_a_diameter`).
#[test]
fn a_claim_is_credited_only_when_its_text_states_the_value() {
    let (_, analysis) = analysed("plate");
    let members = lint::holes::groups(&analysis)[0].members.clone();
    let diameter = |text: &str| -> Vec<String> {
        let claims: Vec<(&str, &str)> = members
            .iter()
            .map(|m| (m.as_str(), "bore.diameter"))
            .collect();
        // Hole coverage's findings: the diameter backstop (`feature_not_dimensioned`) also
        // reports a misprinted diameter, as draftwright's does.
        lint::check(&analysis, &[mark(text, &claims)])
            .into_iter()
            .filter(|i| {
                i.code.starts_with("hole_requirement_")
                    && i.requirements
                        .iter()
                        .any(|r| r.parameter == "bore.diameter")
            })
            .map(|i| i.code)
            .collect()
    };
    assert!(diameter("4× ⌀6 THRU").is_empty());
    assert!(diameter("4× ⌀6.0 THRU").is_empty());
    for wrong in [
        "4× ⌀16 THRU",
        "4× ⌀61 THRU",
        "4× ⌀5.6 THRU",
        "4× ⌀6.06 THRU",
        "6× ⌀8 THRU",
        "4× ⌀8 THRU (6×2)",
        "4× ⌀8 ↧ 6",
    ] {
        assert_eq!(
            diameter(wrong),
            ["hole_requirement_unverifiable"],
            "{wrong} is credited"
        );
    }
}

/// ISSUE-1217-b: every number the drawing prints about the part is claimed. Each annotation
/// whose label prints a digit claims the measurements it states (a structured radius note
/// carries exact satisfaction identities), and every other text run with
/// a digit is the title block's (the drawing number and the scale), not a fact about the part.
#[test]
fn every_number_on_the_sheet_carries_a_claim() {
    let mut parts: Vec<(String, draftwright::haecceity::Part, Analysis)> = FIXTURES
        .iter()
        .map(|n| {
            let (p, a) = analysed(n);
            (n.to_string(), p, a)
        })
        .collect();
    if let Some(dir) = corpus::dir() {
        for name in ["ftc06", "ctc01"] {
            let (p, a) = corpus::analyse(&dir, &facts::load(name).binding.file, name);
            parts.push((name.into(), p, a));
        }
    }
    for (name, part, analysis) in &parts {
        let plan = model::plan(analysis, &Spec::default()).unwrap();
        let drawing = drawn(part, analysis, &plan);
        assert!(!drawing.annotations.is_empty(), "{name}");
        let mut annotated = BTreeSet::new();
        for a in &drawing.annotations {
            annotated.extend(a.runs.iter().copied());
            let text: String = a
                .runs
                .iter()
                .map(|&i| drawing.text[i].text.as_str())
                .collect();
            if text.chars().any(|c| c.is_ascii_digit()) {
                assert!(
                    !a.claims.is_empty() || !a.satisfies.is_empty(),
                    "{name}: {} prints {text:?}",
                    a.name
                );
                for c in &a.satisfies {
                    let f = analysis
                        .features
                        .iter()
                        .find(|f| f.id == c.feature)
                        .expect("a note's radius identity belongs to the analysis");
                    assert!(matches!(f.kind.as_str(), "blend" | "fillet"));
                    assert_eq!(c.parameter, format!("{}.radius", f.kind));
                    let radius = f
                        .parameters
                        .get("radius")
                        .and_then(|r| r.as_f64())
                        .expect("a claimed radius has a source value");
                    assert!(radius.is_finite() && radius > 0.0);
                    assert!(
                        lint::holes::states(&text, radius),
                        "{name}: {} misprints its claimed radius",
                        a.name
                    );
                    assert!(
                        text.contains("ALL UNSPECIFIED RADII"),
                        "a note needs an identifiable default"
                    );
                }
            }
        }
        let [x0, y0, x1, y1] = plan.sheet.title_block;
        for (i, run) in drawing.text.iter().enumerate() {
            if annotated.contains(&i) || !run.text.chars().any(|c| c.is_ascii_digit()) {
                continue;
            }
            if run.text.starts_with("DETAIL ") {
                let v = plan
                    .views
                    .iter()
                    .find(|v| {
                        v.detail
                            .as_ref()
                            .is_some_and(|d| run.text.starts_with(&format!("DETAIL {} (", d.label)))
                    })
                    .expect("detail scale furniture has a planned view");
                let ratio = run
                    .text
                    .split_once('(')
                    .unwrap()
                    .1
                    .split_once(')')
                    .unwrap()
                    .0;
                let (n, d) = ratio.split_once(':').unwrap();
                let scale = n.parse::<f64>().unwrap() / d.parse::<f64>().unwrap();
                assert!(
                    (scale - v.scale).abs() <= 0.001 * v.scale,
                    "{name}: detail scale must state its actual view scale"
                );
                let ink = drawing
                    .sheet
                    .views
                    .iter()
                    .find(|drawn| drawn.name == v.id)
                    .expect("detail has real projected ink")
                    .bbox;
                assert_eq!(run.at, [(ink[0] + ink[2]) / 2.0, ink[1] - 8.0]);
                continue;
            }
            let [x, y] = run.at;
            // The planned ISO 5457 grid's column numbers, centred on their fields in the top
            // and bottom margins, state no measurement.
            if let Some(grid) = &plan.sheet.zones
                && let Ok(n) = run.text.parse::<u32>()
                && (1..=grid.columns).contains(&n)
            {
                let [w, h] = plan.sheet.page;
                // Zone numbers sit in the margin outside the frame.
                let margin = draftwright::layout::FRAME;
                let centre = (f64::from(n) - 0.5) * w / f64::from(grid.columns);
                if (x - centre).abs() < 1e-9
                    && ((y - margin / 2.).abs() < 1e-9 || (y - (h - margin / 2.)).abs() < 1e-9)
                {
                    continue;
                }
            }
            assert!(
                (x0..=x1).contains(&x) && (y0..=y1).contains(&y),
                "{name}: {:?} at {:?} is claimed by no annotation",
                run.text,
                run.at
            );
        }
    }
}

/// ISSUE-1397-a: an unverifiable finding never prints a placeholder where a requirement
/// belongs, and does not invent a count it cannot know. Every finding lint can raise on every
/// part (judged against a drawing that states nothing) names its requirement; a hole whose
/// record cannot be read is reported by its ID, with why, and no number of requirements.
#[test]
fn an_unverifiable_finding_names_its_requirement_or_says_it_cannot() {
    for (name, analysis) in every_part() {
        for issue in lint::check(&analysis, &[]) {
            for bad in ["requirement ?", "measurement ?", "??"] {
                assert!(!issue.message.contains(bad), "{name}: {}", issue.message);
            }
        }
    }

    let (_, mut analysis) = analysed("plate");
    let hole = analysis
        .features
        .iter_mut()
        .find(|f| f.kind == "hole")
        .unwrap();
    hole.parameters.remove("diameter");
    let id = hole.id.clone();
    let issues = lint::check(&analysis, &[]);
    let unread: Vec<&LintIssue> = issues
        .iter()
        .filter(|i| {
            i.subjects == [id.clone()] && i.evidence.as_deref() == Some("it has no diameter")
        })
        .collect();
    assert_eq!(unread.len(), 1, "{issues:#?}");
    let message = &unread[0].message;
    assert_eq!(unread[0].code, "hole_requirement_unverifiable");
    assert!(message.contains(&id), "{message}");
    assert!(!message.contains('?'), "{message}");
    assert!(
        !message.replace(&id, "").chars().any(|c| c.is_ascii_digit()),
        "{message} names a count"
    );
}

/// ISSUE-1196-a: diagnostic text is deterministic. Two plans of one part give the same problems
/// and lint the same findings, to the byte; no message carries a memory address or a debug
/// rendering; and every view a problem names is a view of the plan.
#[test]
fn diagnostics_are_the_same_every_run() {
    for (name, analysis) in every_part() {
        let once = model::plan(&analysis, &Spec::default()).unwrap();
        let twice = model::plan(&analysis, &Spec::default()).unwrap();
        assert_eq!(
            serde_json::to_string(&once.problems).unwrap(),
            serde_json::to_string(&twice.problems).unwrap(),
            "{name}"
        );
        assert_eq!(
            serde_json::to_string(&lint::check(&analysis, &[])).unwrap(),
            serde_json::to_string(&lint::check(&analysis, &[])).unwrap(),
            "{name}"
        );
        let views: BTreeSet<&str> = once.views.iter().map(|v| v.id.as_str()).collect();
        let mut ids = BTreeSet::new();
        for p in &once.problems {
            assert!(ids.insert(p.id.as_str()), "{name}: {} twice", p.id);
            // Debug renderings, by whole word, so that "information" or "infeasible" pass.
            for bad in ["Some(", "@0"] {
                assert!(!p.message.contains(bad), "{name}: {}", p.message);
            }
            for word in p.message.split(|c: char| !c.is_alphanumeric() && c != '_') {
                let address = word.len() > 2
                    && word.starts_with("0x")
                    && word[2..].chars().all(|c| c.is_ascii_hexdigit());
                assert!(
                    !address && !["None", "NaN", "inf", "Infinity"].contains(&word),
                    "{name}: {}",
                    p.message
                );
            }
            for s in &p.subjects {
                if s.starts_with("v:") {
                    assert!(
                        views.contains(s.as_str()) || p.code == "section_not_shown",
                        "{name}: {} names {s}",
                        p.id
                    );
                }
            }
        }
    }
}

/// ISSUE-1514-a: a projection the port does not draw is refused before any input is read, on
/// every entry point. The port draws first and third angle (`sheet/projection`), so any other
/// method is refused when the spec is read, naming the methods there are: by `plan`, `render`
/// and `review` (the service, the MCP server and the command line all call them), with the
/// STEP file never opened and nothing written, and by `edit`. The two it draws pass that check
/// and fail only on the missing part.
#[test]
fn a_projection_is_refused_before_the_part_is_read() {
    for projection in ["first", "third"] {
        let dir = scratch(&format!("projection-{projection}"));
        let missing = dir.join("missing.step");
        let spec = json!({ "sheet": { "projection": projection } });
        let e = api::call(
            "plan",
            &json!({ "step": missing, "spec": spec,
                "output": dir.join("out").display().to_string() }),
        )
        .unwrap_err();
        assert!(e.message.contains("missing.step"), "{e}");
        model::edit(
            &Spec::default(),
            &json!([{ "op": "set", "path": "sheet/projection", "value": projection }]),
        )
        .unwrap();
    }
    for projection in ["unknown", "second", ""] {
        let dir = scratch(&format!("projection-{projection}"));
        let missing = dir.join("missing.step");
        let spec = json!({ "sheet": { "projection": projection } });
        for op in ["plan", "render", "review"] {
            let e = api::call(
                op,
                &json!({ "step": missing, "spec": spec,
                    "output": dir.join("out").display().to_string() }),
            )
            .unwrap_err();
            assert_eq!(e.code, "invalid", "{op}: {e}");
            assert!(e.message.contains("projection"), "{op}: {e}");
            assert!(e.message.contains("first"), "{op}: {e}");
            assert!(e.message.contains("third"), "{op}: {e}");
            assert!(!e.message.contains("missing.step"), "{op}: {e}");
        }
        let e = model::edit(
            &Spec::default(),
            &json!([{ "op": "set", "path": "sheet/projection", "value": projection }]),
        )
        .unwrap_err();
        assert!(e.message.contains("projection"), "{e}");

        // The command line, the same way.
        let spec_file = dir.join("spec.json");
        std::fs::write(&spec_file, spec.to_string()).unwrap();
        let out = std::process::Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .arg("render")
            .arg(&missing)
            .arg("--spec")
            .arg(&spec_file)
            .arg("-o")
            .arg(dir.join("out"))
            .output()
            .unwrap();
        assert_eq!(out.status.code(), Some(2));
        let text = String::from_utf8_lossy(&out.stdout);
        assert!(text.contains("projection"), "{text}");
        assert_eq!(listing(&dir), ["spec.json"]);
    }
}

/// ISSUE-1260-b, in part: a view request the port cannot honour is refused when the plan is
/// made, saying which views there are, and nothing is planned.
#[test]
fn an_unknown_view_is_refused_with_the_views_there_are() {
    let (_, analysis) = analysed("plate");
    let spec: Spec = serde_json::from_value(json!({ "views": { "top": {} } })).unwrap();
    let e = model::plan(&analysis, &spec).unwrap_err();
    assert!(e.message.contains("top"), "{e}");
    for view in ["front", "plan", "side", "iso"] {
        assert!(e.choices.iter().any(|c| c == view), "{e}");
    }
}

/// ISSUE-1353-a (translated): a drawing taken over from its output round-trips. The spec read
/// back from each format (SVG, DXF, PDF, PNG) plans the very same plan, which draws the very
/// same drawing and lint finds the same on it, for a prismatic and a turned part with edits.
#[test]
fn the_spec_read_back_from_a_drawing_gives_the_same_drawing() {
    for (name, ops) in [
        (
            "plate",
            json!([
                { "op": "set", "path": "dimensions/d:envelope~1height.length/omit", "value": true },
                { "op": "set", "path": "sheet/title/material", "value": "6082-T6" },
                { "op": "set", "path": "views/iso/omit", "value": true },
            ]),
        ),
        (
            "turned",
            json!([{ "op": "set", "path": "sheet/size", "value": "A3" }]),
        ),
    ] {
        let (part, analysis) = analysed(name);
        let spec = model::edit(&Spec::default(), &ops).unwrap();
        let plan = model::plan(&analysis, &spec).unwrap();
        let drawing = drawn(&part, &analysis, &plan);
        let mono = std::fs::read(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"),
        )
        .unwrap();
        let fonts = |_: &str| Some(mono.clone());
        let outputs = [
            drawing.svg().into_bytes(),
            drawing.dxf(),
            drawing.pdf(&fonts),
            drawing.png(20.0).unwrap(),
        ];
        let lint = serde_json::to_string(&lint::lint(&analysis, &drawing)).unwrap();
        for bytes in &outputs {
            let back = model::embedded_spec(bytes).unwrap().unwrap();
            let again = model::plan(&analysis, &back).unwrap();
            assert_eq!(
                serde_json::to_string(&again).unwrap(),
                serde_json::to_string(&plan).unwrap(),
                "{name}"
            );
            let redrawn = drawn(&part, &analysis, &again);
            assert!(redrawn.svg() == drawing.svg(), "{name}: the SVG differs");
            assert!(redrawn.dxf() == drawing.dxf(), "{name}: the DXF differs");
            assert_eq!(
                serde_json::to_string(&lint::lint(&analysis, &redrawn)).unwrap(),
                lint,
                "{name}"
            );
        }
    }
}

/// ISSUE-1460-d (translated): rendering writes the drawings asked for beside the output stem
/// and nothing else, so it leaves no evidence file to go stale, and it removes nothing it did
/// not write: a sidecar-like file, and a drawing of a format not asked for this time, are left
/// as they were.
#[test]
fn rendering_writes_only_its_drawings_and_removes_nothing() {
    let dir = scratch("sidecar");
    std::fs::write(dir.join("plate.inspection.json"), "{}").unwrap();
    std::fs::write(dir.join("plate.pdf"), "earlier").unwrap();
    let reply = api::call(
        "render",
        &json!({ "step": fixture("parts/plate.step"), "features": fixture("recognition/plate.json"),
            "output": dir.join("plate").display().to_string(), "formats": ["svg"] }),
    )
    .unwrap();
    assert_eq!(reply["written"].as_array().unwrap().len(), 1);
    assert_eq!(
        listing(&dir),
        ["plate.inspection.json", "plate.pdf", "plate.svg"]
    );
    assert_eq!(
        std::fs::read_to_string(dir.join("plate.inspection.json")).unwrap(),
        "{}"
    );
    assert_eq!(
        std::fs::read_to_string(dir.join("plate.pdf")).unwrap(),
        "earlier"
    );
}

/// quiddity record types draftwright leaves undimensioned by design (`detect.py`'s
/// `_ORCHESTRATED_RECORDS` and `_UNCONSUMED_RECORDS`; the port's `dims::UNDRAFTED`). An angled
/// step is not one: the plan reports it unsupported (ISSUE-1247-a).
const UNDRAFTED: [&str; 9] = [
    "CircularFacePattern",
    "CounterSink",
    "FreeformSurface",
    "InteriorVoid",
    "ObliqueThroughStep",
    "OrientedSlotArray",
    "OrientedSlotGrid",
    "SheetMetalBody",
    "ThinWallBody",
];

/// ISSUE-1392-a, in part: no recognised feature goes missing from the drawing without a trace.
/// Every feature of the Analysis is stated by a planned dimension or drawn structured note (a pattern's for its member
/// holes, a groove's for the boss that is its floor and the turned band it owns) or is the
/// subject of a problem, unless it is of a kind draftwright itself leaves undimensioned.
#[test]
fn every_recognised_feature_is_dimensioned_or_reported() {
    for (name, analysis) in every_part() {
        let plan = model::plan(&analysis, &Spec::default()).unwrap();
        let mut traced: BTreeSet<&str> = plan
            .dimensions
            .iter()
            .flat_map(|d| d.measurements.iter().map(|m| m.feature.as_str()))
            .chain(
                plan.notes
                    .iter()
                    .filter(|n| n.placed.is_some())
                    .flat_map(|n| n.measurements.iter().map(|m| m.feature.as_str())),
            )
            .chain(
                plan.problems
                    .iter()
                    .flat_map(|p| p.subjects.iter().map(String::as_str)),
            )
            .collect();
        // Derived plate items remain traced only with a live owner and physical
        // exclusion or every dependency independently stated by the rendered ink.
        let mut ink = Vec::new();
        let mut text = Vec::new();
        let marks: Vec<_> = draftwright::dims::render(&plan.dimensions, &mut ink, &mut text)
            .into_iter()
            .map(|a| Mark {
                units: Default::default(),
                name: a.name,
                view: a.view,
                text: a
                    .runs
                    .iter()
                    .map(|i| text[*i].text.as_str())
                    .collect::<Vec<_>>()
                    .join(" "),
                claims: a.claims,
            })
            .collect();
        for o in &plan.omitted {
            let Some(id) =
                o.id.strip_prefix("d:")
                    .and_then(|id| id.strip_suffix("/thickness.length"))
            else {
                continue;
            };
            let draftwright::items::State::Covered { by } = &o.state else {
                continue;
            };
            let f = analysis.features.iter().find(|f| f.id == id).unwrap();
            assert!(
                marks.iter().any(|m| m.name == *by),
                "{name}: {} has no live owner",
                o.id
            );
            assert!(
                lint::plates::exclusion(&analysis, f).is_some()
                    || lint::plates::alternatives(&analysis, f)
                        .iter()
                        .any(|terms| terms.iter().all(|t| t.stated(&marks)))
                    || marks.iter().any(|m| m
                        .claims
                        .iter()
                        .any(|c| c.feature == id && c.parameter == "thickness.length")
                        && lint::holes::states(
                            &m.text,
                            lint::plates::read(&analysis, f).unwrap().thickness()
                        )),
                "{name}: {} has no proven printed thickness",
                o.id
            );
            traced.insert(f.id.as_str());
        }
        let patterns: BTreeMap<&str, &[String]> = analysis
            .recognition
            .iter()
            .flat_map(|r| r.patterns.iter())
            .map(|p| (p.id.as_str(), p.members.as_slice()))
            .collect();
        for (id, members) in &patterns {
            if traced.contains(id) {
                traced.extend(members.iter().map(String::as_str));
            }
        }
        // A boss that is a groove's floor is the groove's (as in draftwright and lint).
        let in_traced_groove = |b: &draftwright::bridge::Feature| {
            b.kind == "boss"
                && lint::bosses::groove_floor(&analysis, b)
                && analysis.features.iter().any(|g| {
                    g.kind == "groove"
                        && traced.contains(g.id.as_str())
                        && g.faces.iter().any(|i| b.faces.contains(i))
                })
        };
        // A turned step a groove owns is the groove's too (`lint::turned::bands` leaves it out).
        let own_bands: BTreeSet<&str> = lint::turned::bands(&analysis)
            .map(|b| b.iter().map(|f| f.id.as_str()).collect())
            .unwrap_or_default();
        let grooved = |f: &draftwright::bridge::Feature| {
            f.kind == "turned_step"
                && !own_bands.is_empty()
                && !own_bands.contains(f.id.as_str())
                && analysis
                    .features
                    .iter()
                    .any(|g| g.kind == "groove" && traced.contains(g.id.as_str()))
        };
        let untraced: Vec<String> = analysis
            .features
            .iter()
            .filter(|f| !traced.contains(f.id.as_str()) && !in_traced_groove(f) && !grooved(f))
            .filter(|f| !UNDRAFTED.contains(&f.fingerprint.subtype.as_str()))
            .map(|f| format!("{} ({})", f.id, f.fingerprint.subtype))
            .collect();
        assert!(untraced.is_empty(), "{name}: {untraced:?}");
    }
}

/// ISSUE-1336-a: a test that reads a corpus part pins the exact file. The sampled parts are
/// checked against the hash their sheet was captured from (in [`sampled`]), and recognition made
/// for one file is refused for another, so no test can pass on a look-alike.
#[test]
fn corpus_fixtures_are_bound_to_their_files() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let parts = sampled();
    assert_eq!(parts.len(), 13);
    let (part, mut analysis) =
        model::analyse(&dir.join(facts::load("ftc06").binding.file)).unwrap();
    let e = model::recognise(&part, &mut analysis, &sheet_recognition("ctc01")).unwrap_err();
    assert!(!e.is_empty());
    assert!(analysis.features.is_empty());
}
