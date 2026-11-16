//! Every repeated dimension label on the quality parts has a verdict (alpha 1 gate, "repeated
//! dimension labels": each one remaining justified, none unexplained; `docs/alpha1.md`).
//!
//! A repeated label is what `tools/quality.py` counts: two or more linear items of one plan in
//! the same view, printing the same label, at the same rotation. The plan is the one the command
//! line gives (`api::plan_from` with `Features::Native`: Rust quiddity's recognition, the default
//! spec, layout recovery) for each part of `tools/quality_parts.txt`. Each such group is listed in
//! `tests/fixtures/known_repeated_labels.json` with its items, a verdict and the evidence from
//! draftwright's sheet of the same part; a listed group that goes away (or changes its items)
//! must come off. The verdicts:
//!
//! - `identical-features`: distinct features of the same size, each stated for itself as
//!   draftwright states them (not on one span, so not one dimension counted `n×`);
//! - `distinct-quantities`: different measured quantities whose values print alike;
//! - `draftwright-repeats`: the same span stated by a feature dimension and a ladder rung, which
//!   draftwright also states twice (its rungs take no corridor dedup against hole locations).
//!
//! Set `REPEATS_REPORT=1` to print every group the plans have.

#[path = "common/corpus.rs"]
mod corpus;

use std::collections::BTreeMap;
use std::path::Path;

use draftwright::api::{self, Features};
use draftwright::model::{Plan, Spec};
use serde::Deserialize;

const VERDICTS: [&str; 3] = [
    "identical-features",
    "distinct-quantities",
    "draftwright-repeats",
];

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    /// The part, as `tools/quality_parts.txt` names it.
    part: String,
    view: String,
    label: String,
    rotation: f64,
    /// The repeating items' IDs, sorted.
    items: Vec<String>,
    verdict: String,
    /// What draftwright's sheet of the part states for these quantities.
    draftwright: String,
    reason: String,
}

type Key = (String, String, String, i64);

/// The plan's repeated-label groups, as `tools/quality.py` counts them, with their items' IDs.
fn repeats(part: &str, plan: &Plan) -> BTreeMap<Key, Vec<String>> {
    let mut groups: BTreeMap<Key, Vec<String>> = BTreeMap::new();
    for d in plan.dimensions.iter().filter(|d| d.kind == "linear") {
        let rotation = d.placed.as_ref().map_or(0.0, |p| p.rotation);
        groups
            .entry((
                part.to_string(),
                d.view.trim_start_matches("v:").to_string(),
                d.label.clone(),
                rotation.round() as i64,
            ))
            .or_default()
            .push(d.id.clone());
    }
    groups.retain(|_, ids| ids.len() > 1);
    for ids in groups.values_mut() {
        ids.sort();
    }
    groups
}

#[test]
fn every_repeated_label_on_the_quality_parts_has_a_verdict() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED (corpus parts): the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let parts: Vec<String> = std::fs::read_to_string(root.join("tools/quality_parts.txt"))
        .unwrap()
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty())
        .map(String::from)
        .collect();
    let known: Vec<Known> = serde_json::from_str(
        &std::fs::read_to_string(root.join("tests/fixtures/known_repeated_labels.json")).unwrap(),
    )
    .unwrap();

    // The exact known-label ledger was captured in mm.
    // Four parts at a time: each plan is independent.
    let next = std::sync::atomic::AtomicUsize::new(0);
    let found: BTreeMap<Key, Vec<String>> = std::thread::scope(|s| {
        let workers: Vec<_> = (0..4)
            .map(|_| {
                s.spawn(|| {
                    let mut found = BTreeMap::new();
                    loop {
                        let i = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        let Some(part) = parts.get(i) else {
                            return found;
                        };
                        let plan = api::plan_from(
                            &dir.join(part),
                            Features::Native,
                            &Spec {
                                units: draftwright::units::Units::Mm,
                                ..Spec::default()
                            },
                        )
                        .unwrap_or_else(|e| panic!("{part}: {e:?}"));
                        found.extend(repeats(part, &plan));
                    }
                })
            })
            .collect();
        workers
            .into_iter()
            .flat_map(|w| w.join().unwrap())
            .collect()
    });
    if std::env::var_os("REPEATS_REPORT").is_some() {
        for (k, ids) in &found {
            eprintln!("{k:?} {ids:?}");
        }
    }

    let mut listed: BTreeMap<Key, &Known> = BTreeMap::new();
    for k in &known {
        assert!(
            parts.contains(&k.part),
            "{}: not a quality part (tools/quality_parts.txt)",
            k.part
        );
        assert!(
            VERDICTS.contains(&k.verdict.as_str()),
            "{k:?}: the verdict is one of {VERDICTS:?}"
        );
        assert!(
            !k.reason.is_empty() && !k.draftwright.is_empty(),
            "{k:?}: a verdict needs its reason and draftwright's evidence"
        );
        let key = (
            k.part.clone(),
            k.view.clone(),
            k.label.clone(),
            k.rotation.round() as i64,
        );
        assert!(listed.insert(key, k).is_none(), "{k:?}: listed twice");
    }
    let mut problems = Vec::new();
    for (key, ids) in &found {
        match listed.get(key) {
            None => problems.push(format!("unexplained repeat {key:?}: {ids:?}")),
            Some(k) if &k.items != ids => problems.push(format!(
                "{key:?}: the items are now {ids:?}, listed {:?}",
                k.items
            )),
            Some(_) => {}
        }
    }
    for key in listed.keys().filter(|k| !found.contains_key(*k)) {
        problems.push(format!(
            "stale entry {key:?}: the plan no longer repeats it"
        ));
    }
    assert!(problems.is_empty(), "{}", problems.join("\n"));
}
