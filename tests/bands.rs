//! The annotation bands the port reserves from a part's features (`src/demand.rs`), against the
//! bands draftwright reserved for the same part: its first placement's strip depths and step
//! count (the first `_layout_geometry` call recorded in `tests/fixtures/layout/<name>.json.gz`,
//! with all three principal views), and its step levels (the count its first scale choice is
//! seeded with). Every difference is listed, with a verdict and the reason,
//! in `tests/fixtures/known_bands.json`; a listed difference that has gone away must come off it.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::BTreeSet;
use std::io::Read as _;
use std::path::Path;

use draftwright::demand;
use draftwright::layout::{Principals, Request};
use serde::Deserialize;
use serde_json::Value;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    part: String,
    /// A strip field, `step_levels`, `n_steps` or `section`.
    fact: String,
    /// `rust-correct`, `rust-wrong`, `equivalent` or `undetermined`.
    verdict: String,
    reason: String,
}

const FIELDS: [&str; 11] = [
    "right",
    "left",
    "top",
    "pv_halo",
    "fv_top",
    "fv_bottom",
    "front_hole_below",
    "pv_bottom",
    "sv_top",
    "sv_bottom",
    "sv_right",
];

fn calls(name: &str) -> Vec<Value> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/layout")
        .join(format!("{name}.json.gz"));
    let mut text = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_string(&mut text)
        .unwrap();
    let v: Value = serde_json::from_str(&text).unwrap();
    v["calls"].as_array().unwrap().clone()
}

#[test]
fn bands_match_draftwright_or_are_known() {
    let Some(dir) = corpus::dir() else {
        eprintln!(
            "SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS \
             (DRAFTWRIGHT_CORPUS_REQUIRED=1 makes this a failure)"
        );
        return;
    };
    let known: Vec<Known> = serde_json::from_str(
        &std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/known_bands.json"),
        )
        .unwrap(),
    )
    .unwrap();
    for k in &known {
        assert!(
            ["rust-correct", "rust-wrong", "equivalent", "undetermined"]
                .contains(&k.verdict.as_str())
                && !k.reason.is_empty(),
            "{k:?}"
        );
    }
    let listed: BTreeSet<(String, String)> = known
        .iter()
        .map(|k| (k.part.clone(), k.fact.clone()))
        .collect();
    assert_eq!(listed.len(), known.len(), "a difference is listed twice");

    let mut differ = BTreeSet::new();
    let mut agree = 0;
    for name in facts::names() {
        let f = facts::load(&name);
        let (_, analysis) = corpus::analyse(&dir, &f.binding.file, &name);
        let d = demand::read(&analysis);
        let [lo, hi] = analysis.envelope;
        let mut request = Request::new(
            [hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]],
            Principals::ALL,
            true,
        );
        request.sections = usize::from(d.section.is_some());
        let (_, strips, n_steps) = demand::size(&d, &request, None, None, lo[2]);
        let calls = calls(&name);
        // Draftwright seeds its first scale choice with every step level, then places the
        // views with the count legible at the scale it settles on.
        let seed = calls[0]["n_steps"].as_u64().unwrap() as usize;
        let theirs = calls
            .iter()
            .find(|c| c["call"] == "layout_geometry" && c["views"].is_null())
            .unwrap_or_else(|| panic!("{name}: no first placement"));
        let ours = serde_json::json!({
            "right": strips.right, "left": strips.left, "top": strips.top,
            "pv_halo": strips.pv_halo, "fv_top": strips.fv_top, "fv_bottom": strips.fv_bottom,
            "front_hole_below": strips.front_hole_below, "pv_bottom": strips.pv_bottom,
            "sv_top": strips.sv_top, "sv_bottom": strips.sv_bottom, "sv_right": strips.sv_right,
        });
        let mut check = |fact: &str, same: bool, detail: String| {
            if same {
                agree += 1;
            } else {
                eprintln!("{name} {fact}: {detail}");
                differ.insert((name.clone(), fact.to_string()));
            }
        };
        for field in FIELDS {
            let (a, b) = (
                ours[field].as_f64().unwrap(),
                theirs["strips"][field].as_f64().unwrap(),
            );
            check(
                field,
                (a - b).abs() < 1e-6,
                format!("ours {a}, draftwright {b}"),
            );
        }
        check(
            "step_levels",
            d.step_zs.len() == seed,
            format!("ours {:?}, draftwright {seed} levels", d.step_zs),
        );
        let theirs_n = theirs["n_steps"].as_u64().unwrap() as usize;
        check(
            "n_steps",
            n_steps == theirs_n,
            format!("ours {n_steps} of {:?}, draftwright {theirs_n}", d.step_zs),
        );
        let theirs_s = theirs["section"].as_u64().unwrap() as usize;
        check(
            "section",
            request.sections == theirs_s,
            format!("ours {}, draftwright {theirs_s}", request.sections),
        );
    }
    let unlisted: Vec<_> = differ.difference(&listed).collect();
    let stale: Vec<_> = listed.difference(&differ).collect();
    eprintln!(
        "{agree} band facts agree, {} differ ({} known)",
        differ.len(),
        known.len()
    );
    assert!(
        unlisted.is_empty() && stale.is_empty(),
        "differences not in known_bands.json: {unlisted:#?}\nlisted but no longer differing: {stale:#?}"
    );
}

/// demand groups holes as the dimensions and lint do (`bridge::holes::established_with`): a recognised
/// pattern that is not established as one requirement, here a bolt circle of four holes
/// (ISSUE-1596-a), leaves its holes loose, so the bands are sized for the callouts and locations
/// the sheet will carry.
#[test]
fn demand_groups_holes_by_established_patterns() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
    let (part, mut a) = draftwright::model::analyse(&fixtures.join("parts/plate.step")).unwrap();
    let json = std::fs::read_to_string(fixtures.join("recognition/plate.json")).unwrap();
    let (r, features) = draftwright::bridge::read(&part, &a.binding.source_sha256, &json).unwrap();
    a.recognition = Some(r);
    a.features = features;
    let callouts = |a: &draftwright::model::Analysis| -> Vec<String> {
        let plan = draftwright::model::plan(a, &draftwright::model::Spec::default()).unwrap();
        plan.dimensions
            .iter()
            .filter(|d| d.kind == "callout")
            .map(|d| d.label.clone())
            .collect()
    };

    let grid = demand::read(&a);
    assert_eq!(grid.holes.len(), 4);
    assert!(
        grid.holes
            .iter()
            .all(|h| h.pattern.as_deref() == Some("f:hole_pattern:f6"))
    );
    assert_eq!(callouts(&a), ["4× ⌀6 THRU"]);

    a.recognition.as_mut().unwrap().patterns[0].arrangement = "bolt_circle".into();
    let circle = demand::read(&a);
    assert!(circle.holes.iter().all(|h| h.pattern.is_none()));
    assert_eq!(callouts(&a), ["4× ⌀6 THRU"]);
    // Loose, the holes are located singly: two x positions to tier above the plan, not one.
    let strips = |d: &demand::Demand| demand::strips(d, 0, true);
    assert!(strips(&circle).top > strips(&grid).top);
}

/// A bolt circle of fewer than five holes with a boss on its centre is one requirement in the
/// dimensions and demand, as it is in lint (`bridge::holes::established_with`, ISSUE-1596-a).
/// `boss_circle.step` is draftwright's `test_a_concentric_feature_alone_can_carry_it` part
/// (#1595's 60 × 90 × 6 plate with six ⌀2.4 holes at x ±12.15, y −32.3, −7.92 and 32.3, plus a
/// ⌀12 boss 3 high on the fitted circle's centre (0, −20.11)), made in build123d and recognised by
/// `tools/recognise.py` (quiddity 0.4.0.dev0): quiddity fits a ø34.42 circle through the four
/// lower holes, and draftwright's sheet for it prints `4× ⌀2.4 THRU EQ SP ON\nø34.4 BC` and
/// `2× ⌀2.4 THRU`. Without the boss, the circle is refused and the six are loose.
///
/// The four lower holes are a 24.3 × 24.38 rectangle, 0.19° off even spacing, which since #223
/// is no bolt circle: as recognised, all six are loose. The rule is checked on them squared to
/// 24.3 × 24.3 about the same centre in quiddity's records.
#[test]
fn a_boss_on_its_centre_establishes_a_small_bolt_circle() {
    let fixtures = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
    let (part, mut a) =
        draftwright::model::analyse(&fixtures.join("parts/boss_circle.step")).unwrap();
    let json = std::fs::read_to_string(fixtures.join("recognition/boss_circle.json")).unwrap();
    let mut as_recognised = a.clone();
    draftwright::model::recognise(&part, &mut as_recognised, &json).unwrap();
    assert!(
        as_recognised
            .recognition
            .as_ref()
            .unwrap()
            .patterns
            .is_empty()
    );
    assert!(
        demand::read(&as_recognised)
            .holes
            .iter()
            .all(|h| h.pattern.is_none())
    );
    let squared = json
        .replace("-32.103747339", "-32.063747339")
        .replace("-7.7237473394", "-7.7637473394");
    draftwright::model::recognise(&part, &mut a, &squared).unwrap();
    let callouts = |a: &draftwright::model::Analysis| -> Vec<(String, String)> {
        let plan = draftwright::model::plan(a, &draftwright::model::Spec::default()).unwrap();
        plan.dimensions
            .iter()
            .filter(|d| d.kind == "callout" && d.label.contains("⌀2.4"))
            .map(|d| (d.label.clone(), d.basis.clone()))
            .collect()
    };

    let pattern = a.recognition.as_ref().unwrap().patterns[0].id.clone();
    let d = demand::read(&a);
    assert_eq!(d.holes.len(), 6);
    assert_eq!(
        d.holes
            .iter()
            .filter(|h| h.pattern.as_deref() == Some(pattern.as_str()))
            .count(),
        4
    );
    let c = callouts(&a);
    let labels: Vec<&str> = c.iter().map(|(l, _)| l.as_str()).collect();
    assert_eq!(labels, ["4× ⌀2.4 THRU EQ SP ON\nø34.42 BC", "2× ⌀2.4 THRU"]);
    assert!(
        c[0].1
            .contains("corroborated by a hole or boss on its centre")
            && !c[0].1.contains("five or more"),
        "{}",
        c[0].1
    );

    // Without the boss nothing corroborates the circle: all six are loose, in both.
    a.features.retain(|f| f.kind != "boss");
    assert!(demand::read(&a).holes.iter().all(|h| h.pattern.is_none()));
    let c = callouts(&a);
    assert_eq!(
        c.iter().map(|(l, _)| l.as_str()).collect::<Vec<_>>(),
        ["6× ⌀2.4 THRU"]
    );
}
