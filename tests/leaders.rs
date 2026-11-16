//! Callout leaders stay clear (maintainer priority, 2026-10-08: "Something is wrong with the
//! leaders, especially diagonals"; `src/dims/leaders.rs`).
//!
//! On the captured corpus parts (their features from `tests/fixtures/recognition/corpus/`), and
//! on cgb245 and nist_ctc_04 as the command line reads them (Rust quiddity's recognition), every
//! callout of the default plan is checked as drawn (`dims::render` draws the plan's ink and
//! nothing else, so the plan's lines and label boxes are the drawing's):
//! - its leader crosses no view but its own, and does not run back into its own view after
//!   leaving it; its shoulder and label lie over no view (a view is its drawn part box, the
//!   envelope projected, as `tests/placement.rs` has it, the isometric by its projected bounds);
//! - none of its lines crosses or runs along another annotation's line (a dimension, extension
//!   or leader line, or an arrowhead's shaft), none runs through another annotation's label, no other
//!   annotation's line runs through its label, and its label overlaps no other label;
//! - its ink stays out of the title block.
//!
//! Every crossing found is listed in `tests/fixtures/known_leaders.json` with a verdict and the
//! evidence; a new one, or a listed one gone, fails, printing the entries to paste. Set
//! `LEADERS_REPORT=1` to print each callout's leader (length, angle, shoulder), the distribution
//! compared with draftwright's in `docs/next-session.md`.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use draftwright::model::{self, Analysis, Plan};
use serde::{Deserialize, Serialize};

type P2 = [f64; 2];

#[derive(Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Known {
    part: String,
    callout: String,
    /// What it crosses: `view:<id>`, `line:<id>`, `label:<id>` or `title_block`.
    crossing: String,
    /// `rust-correct`, `rust-wrong`, `equivalent`, `undetermined` or `not-applicable`.
    verdict: String,
    reason: String,
}

const VERDICTS: [&str; 5] = [
    "rust-correct",
    "rust-wrong",
    "equivalent",
    "undetermined",
    "not-applicable",
];

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

/// The parts checked, analysed: none when the corpus is missing.
fn parts() -> Vec<(String, Analysis)> {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return Vec::new();
    };
    let mut jobs: Vec<(String, String, bool)> = facts::names()
        .into_iter()
        .filter(|n| corpus::recognition(n).is_some())
        .map(|n| (n.clone(), facts::load(&n).binding.file, false))
        .collect();
    for n in ["cgb245", "ctc04"] {
        jobs.push((format!("{n} (native)"), facts::load(n).binding.file, true));
    }
    std::thread::scope(|s| {
        let handles: Vec<_> = jobs
            .iter()
            .map(|(name, file, native)| {
                let dir = &dir;
                s.spawn(move || {
                    let analysis = if *native {
                        model::analyse_native(&dir.join(file)).unwrap().1
                    } else {
                        corpus::analyse(dir, file, name).1
                    };
                    (name.clone(), analysis)
                })
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    })
}

/// Each view's drawn box on the page (as `tests/placement.rs`).
fn view_boxes(plan: &Plan, analysis: &Analysis) -> Vec<(String, [f64; 4])> {
    plan.views
        .iter()
        .map(|v| {
            let b = if v.id == "v:iso" {
                let ib = analysis.iso_bounds;
                [
                    v.at[0] + v.scale * ib[0],
                    v.at[1] + v.scale * ib[1],
                    v.at[0] + v.scale * ib[2],
                    v.at[1] + v.scale * ib[3],
                ]
            } else {
                let view = draftwright::haecceity::hlr::View::new(v.direction, v.up).unwrap();
                let [lo, hi] = v.detail.as_ref().map_or(analysis.envelope, |d| d.bounds);
                let (mut min, mut max) = ([f64::INFINITY; 2], [f64::NEG_INFINITY; 2]);
                for i in 0..8 {
                    let corner = [0, 1, 2].map(|k| if i >> k & 1 == 0 { lo[k] } else { hi[k] });
                    let p = view.map(corner);
                    for k in 0..2 {
                        min[k] = min[k].min(p[k]);
                        max[k] = max[k].max(p[k]);
                    }
                }
                let half = [0.5 * (max[0] - min[0]), 0.5 * (max[1] - min[1])];
                [
                    v.at[0] - v.scale * half[0],
                    v.at[1] - v.scale * half[1],
                    v.at[0] + v.scale * half[0],
                    v.at[1] + v.scale * half[1],
                ]
            };
            (v.id.clone(), b)
        })
        .collect()
}

/// The segment's span inside the open box *b*, as fractions of its length (Liang–Barsky); none
/// when it does not pass through.
fn clip(s: [P2; 2], b: [f64; 4]) -> Option<(f64, f64)> {
    let (p, d) = (s[0], [s[1][0] - s[0][0], s[1][1] - s[0][1]]);
    let (mut t0, mut t1) = (0.0f64, 1.0f64);
    for (num, den) in [
        (p[0] - b[0], -d[0]),
        (b[2] - p[0], d[0]),
        (p[1] - b[1], -d[1]),
        (b[3] - p[1], d[1]),
    ] {
        if den == 0.0 {
            if num <= 0.0 {
                return None;
            }
        } else if den < 0.0 {
            t0 = t0.max(num / den);
        } else {
            t1 = t1.min(num / den);
        }
    }
    (t1 - t0 > 1e-9).then_some((t0, t1))
}

fn crosses(s: [P2; 2], b: [f64; 4]) -> bool {
    clip(s, b).is_some()
}

fn overlap(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]
}

/// Whether two segments cross at a point inside both.
fn segments_cross(a: [P2; 2], b: [P2; 2]) -> bool {
    let o = |p: P2, q: P2, r: P2| (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    let e = 1e-9;
    let (d1, d2) = (o(b[0], b[1], a[0]), o(b[0], b[1], a[1]));
    let (d3, d4) = (o(a[0], a[1], b[0]), o(a[0], a[1], b[1]));
    ((d1 > e && d2 < -e) || (d1 < -e && d2 > e)) && ((d3 > e && d4 < -e) || (d3 < -e && d4 > e))
}

/// Whether two segments run along each other (parallel within 1 mm, together for 0.5 mm or
/// more): a leader laid on an extension line reads as crossing it all the way.
fn along(a: [P2; 2], b: [P2; 2]) -> bool {
    let d = [a[1][0] - a[0][0], a[1][1] - a[0][1]];
    let len = d[0].hypot(d[1]);
    if len < 1e-9 {
        return false;
    }
    let u = [d[0] / len, d[1] / len];
    let off = |p: P2| (p[0] - a[0][0]) * u[1] - (p[1] - a[0][1]) * u[0];
    let at = |p: P2| (p[0] - a[0][0]) * u[0] + (p[1] - a[0][1]) * u[1];
    if off(b[0]).abs() > 1.0 || off(b[1]).abs() > 1.0 {
        return false;
    }
    let (t0, t1) = (at(b[0]).min(at(b[1])), at(b[0]).max(at(b[1])));
    t1.min(len) - t0.max(0.0) >= 0.5
}

/// What each callout of *plan* crosses, by callout ID.
fn crossings(plan: &Plan, analysis: &Analysis) -> BTreeMap<String, BTreeSet<String>> {
    let boxes = view_boxes(plan, analysis);
    let placed: Vec<_> = plan
        .dimensions
        .iter()
        .filter_map(|d| d.placed.as_ref().map(|p| (d, p)))
        .collect();
    let mut out: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for (d, p) in placed.iter().filter(|(d, _)| d.kind == "callout") {
        let mut found = BTreeSet::new();
        let (shaft, shoulder) = (p.lines[0], p.lines[1]);
        for (view, b) in &boxes {
            let interior =
                *view == d.view && d.region == Some(draftwright::dims::CandidateRegion::Interior);
            if interior {
                // An own-view candidate lies wholly inside its view; tests/interior_placement.rs
                // checks it against the rendered line-work.
                assert!(
                    p.label_box[0] >= b[0]
                        && p.label_box[1] >= b[1]
                        && p.label_box[2] <= b[2]
                        && p.label_box[3] <= b[3]
                );
            }
            if !interior && (overlap(p.label_box, *b) || crosses(shoulder, *b)) {
                found.insert(format!("view:{view}"));
            }
            if *view != d.view {
                if crosses(shaft, *b) {
                    found.insert(format!("view:{view}"));
                }
            } else if let Some((t0, _)) = clip(shaft, *b) {
                // Back into its own view after leaving it (past a hair at the tip).
                let len = (shaft[1][0] - shaft[0][0]).hypot(shaft[1][1] - shaft[0][1]);
                if t0 * len > 0.05 {
                    found.insert(format!("view:{view}"));
                }
            }
        }
        let tb = plan.sheet.title_block;
        if overlap(p.label_box, tb) || p.lines.iter().any(|l| crosses(*l, tb)) {
            found.insert("title_block".into());
        }
        for (e, q) in &placed {
            if e.id == d.id {
                continue;
            }
            let theirs: Vec<[P2; 2]> = q.lines.iter().chain(&q.arrows).copied().collect();
            if p.lines.iter().any(|l| {
                theirs
                    .iter()
                    .any(|m| segments_cross(*l, *m) || along(*l, *m))
            }) {
                found.insert(format!("line:{}", e.id));
            }
            if overlap(p.label_box, q.label_box)
                || p.lines.iter().any(|l| crosses(*l, q.label_box))
                || theirs.iter().any(|m| crosses(*m, p.label_box))
            {
                found.insert(format!("label:{}", e.id));
            }
        }
        out.insert(d.id.clone(), found);
    }
    out
}

/// Every callout is placed or explicitly refused, and every drawn leader has a
/// horizontal shoulder with its label beyond it, on the side the leader runs to (draftwright's
/// `text_side`).
///
/// The maintainer's acceptance: no callout leader crosses a view, another annotation or the
/// title block, except the listed, each with a verdict.
#[test]
fn callout_leaders_cross_nothing_unlisted_and_have_outward_horizontal_shoulders() {
    let parts = parts();
    if parts.is_empty() {
        return;
    }
    let known: Vec<Known> =
        serde_json::from_str(&std::fs::read_to_string(fixture("known_leaders.json")).unwrap())
            .unwrap();
    let mut listed: BTreeMap<(String, String, String), &Known> = BTreeMap::new();
    for k in &known {
        assert!(
            VERDICTS.contains(&k.verdict.as_str()) && !k.reason.is_empty(),
            "{k:?}"
        );
        assert!(
            listed
                .insert((k.part.clone(), k.callout.clone(), k.crossing.clone()), k)
                .is_none(),
            "{k:?} is listed twice"
        );
    }
    let report = std::env::var_os("LEADERS_REPORT").is_some();
    let mut found: BTreeSet<(String, String, String)> = BTreeSet::new();
    let mut callouts = 0;
    // The complete known-leader ledger was captured on metric ISO drawings.
    for (name, analysis) in &parts {
        let plan = model::plan(
            analysis,
            &serde_json::from_value(
                serde_json::json!({"units":"mm","sheet":{"preferred_max_size":"A0"}}),
            )
            .unwrap(),
        )
        .unwrap();
        for (id, set) in crossings(&plan, analysis) {
            callouts += 1;
            for c in set {
                // Policy B keeps a crossing leader and logs it (ADR2-D6, ADR2-9): a line crossing
                // is the plan's `annotation_leader_crossing` naming the callout and what it
                // crosses.
                if let Some(other) = c.strip_prefix("line:") {
                    assert!(
                        plan.problems
                            .iter()
                            .any(|p| p.code == "annotation_leader_crossing"
                                && p.subjects == [id.clone(), other.to_string()]),
                        "{name}: {id} crosses {other} unreported"
                    );
                }
                found.insert((name.clone(), id.clone(), c));
            }
        }
        // Honest failure: a leader back across its own view, or over the 75 mm a leader may run,
        // is taken only when every other place was blocked, and is then reported.
        let boxes = view_boxes(&plan, analysis);
        for d in plan.dimensions.iter().filter(|d| d.kind == "callout") {
            let Some(p) = d.placed.as_ref() else { continue };
            let shaft = p.lines[0];
            let len = (shaft[1][0] - shaft[0][0]).hypot(shaft[1][1] - shaft[0][1]);
            let has = |code: &str| {
                plan.problems
                    .iter()
                    .any(|q| q.code == code && q.subjects.first() == Some(&d.id))
            };
            if len > 75.0 {
                assert!(has("annotation_leader_long"), "{name}: {} unreported", d.id);
            }
            let own = boxes.iter().find(|(v, _)| *v == d.view).map(|(_, b)| *b);
            if let Some((t0, _)) = own.and_then(|b| clip(shaft, b))
                && t0 * len > 0.05
            {
                assert!(
                    has("annotation_line_over_view"),
                    "{name}: {} unreported",
                    d.id
                );
            }
        }
        for d in plan.dimensions.iter().filter(|d| d.kind == "callout") {
            // ISSUE-798-a: only an item no shown view carries is unplaced.
            let Some(p) = d.placed.as_ref() else {
                let draftwright::items::State::NotDrawable { reason_code } = &d.state else {
                    panic!("{name}: {} has no explicit refusal state", d.id);
                };
                assert!(reason_code == "view_plan_incomplete");
                assert!(
                    plan.problems
                        .iter()
                        .any(|q| q.code == *reason_code && q.subjects.contains(&d.id)),
                    "{name}: {} is neither placed nor reported",
                    d.id
                );
                continue;
            };
            assert_eq!(p.lines.len(), 2, "{name}: {}", d.id);
            let [tip, elbow] = p.lines[0];
            let [from, end] = p.lines[1];
            assert_eq!(from, elbow, "{name}: {}", d.id);
            assert!(
                (end[1] - elbow[1]).abs() < 1e-9,
                "{name}: {} shoulder",
                d.id
            );
            let out = end[0] - elbow[0];
            assert!(
                (elbow[0] - tip[0]) * out >= -1e-9,
                "{name}: {} shoulder doubles back",
                d.id
            );
            let b = p.label_box;
            assert!(
                if out > 0.0 {
                    b[0] >= end[0] - 1e-9
                } else {
                    b[2] <= end[0] + 1e-9
                },
                "{name}: {} label beyond the shoulder",
                d.id
            );
        }
        if report {
            for d in plan.dimensions.iter().filter(|d| d.kind == "callout") {
                let Some(p) = d.placed.as_ref() else { continue };
                let [tip, elbow] = p.lines[0];
                let [_, end] = p.lines[1];
                let (dx, dy) = (elbow[0] - tip[0], elbow[1] - tip[1]);
                eprintln!(
                    "LEADER\t{name}\t{}\t{}\t{:.2}\t{:.1}\t{:.2}",
                    d.id,
                    d.label,
                    dx.hypot(dy),
                    dy.atan2(dx.abs()).to_degrees().abs(),
                    (end[0] - elbow[0]).abs()
                );
            }
        }
    }
    let mut wrong = Vec::new();
    for key in &found {
        if !listed.contains_key(key) {
            wrong.push(
                serde_json::to_string_pretty(&Known {
                    part: key.0.clone(),
                    callout: key.1.clone(),
                    crossing: key.2.clone(),
                    verdict: "VERDICT".into(),
                    reason: "EVIDENCE".into(),
                })
                .unwrap(),
            );
        }
    }
    for (key, k) in &listed {
        if !found.contains(key) {
            wrong.push(format!(
                "{} {} {} is listed but no longer found: take it off",
                k.part, k.callout, k.crossing
            ));
        }
    }
    eprintln!(
        "{callouts} callouts on {} parts, {} crossings, each listed",
        parts.len(),
        found.len()
    );
    assert!(
        wrong.is_empty(),
        "callout crossings differ from known_leaders.json; give each entry below a verdict (one \
         of {VERDICTS:?}) and the evidence:\n{}",
        wrong.join("\n")
    );
}
