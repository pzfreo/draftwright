//! Structural tier checks: no count snapshots or drawing-output hashes.
use std::collections::BTreeMap;
use std::path::Path;

use draftwright::api::{self, Features};
use draftwright::finding::Tier;
use draftwright::model::{Problem, Spec};

#[path = "common/corpus.rs"]
mod corpus;

#[test]
fn severity_only_changes_explicit_context_dependent_codes() {
    let tier = |code: &str, severity: &str| {
        Problem {
            code: code.into(),
            severity: severity.into(),
            ..Default::default()
        }
        .labels()
        .tier
    };
    for severity in ["info", "warning", "error"] {
        assert_eq!(
            tier("annotation_ink_overlap", severity),
            Tier::FixBeforePrinting
        );
        assert_eq!(
            tier("annotation_off_sheet", severity),
            Tier::FixBeforePrinting
        );
        assert_eq!(tier("label_crowded", severity), Tier::Tidy);
        assert_eq!(tier("annotation_leader_crossing", severity), Tier::Tidy);
        assert_eq!(tier("unclaimed_geometry", severity), Tier::Information);
        assert_eq!(tier("possible_thread", severity), Tier::Information);
    }
    assert_eq!(tier("pmi_not_extracted", "info"), Tier::Information);
    assert_eq!(tier("pmi_not_extracted", "error"), Tier::FixBeforePrinting);
    assert_eq!(tier("feature_not_located", "info"), Tier::Information);
    assert_eq!(
        tier("feature_not_located", "warning"),
        Tier::FixBeforePrinting
    );
}

#[test]
fn quality_parts_report_tier_counts_without_discarding_any_finding() {
    let Some(dir) = corpus::dir() else { return };
    let mut total = BTreeMap::from(Tier::ALL.map(|t| (t, 0usize)));
    for file in include_str!("../tools/quality_parts.txt").lines() {
        let review = api::review_from(&dir.join(file), Features::Native, &Spec::default()).unwrap();
        let labels: Vec<_> = review
            .problems
            .iter()
            .map(Problem::labels)
            .chain(review.lint.iter().map(|p| p.labels()))
            .chain(review.structural.iter().map(|p| p.labels()))
            .collect();
        let mut counts = BTreeMap::from(Tier::ALL.map(|t| (t, 0usize)));
        for label in &labels {
            *counts.get_mut(&label.tier).unwrap() += 1;
        }
        assert_eq!(
            counts.values().sum::<usize>(),
            review.problems.len() + review.lint.len() + review.structural.len()
        );
        for (tier, count) in &counts {
            *total.get_mut(tier).unwrap() += count;
        }
        eprintln!("{file}: {}", serde_json::to_string(&counts).unwrap());
        let wire = serde_json::to_value(&review).unwrap();
        for field in ["problems", "lint", "structural"] {
            for problem in wire[field].as_array().unwrap() {
                assert!(Tier::ALL.iter().any(|t| problem["tier"] == t.as_str()));
            }
        }
    }
    assert!(
        total.values().sum::<usize>() > 0,
        "quality set must exercise findings"
    );
    eprintln!("quality totals: {}", serde_json::to_string(&total).unwrap());
}

#[test]
fn a_fully_drawn_plate_has_no_fix_before_printing_and_a_removed_size_does() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let step = root.join("tests/fixtures/parts/plate.step");
    let features = root.join("tests/fixtures/recognition/plate.json");
    let mut spec = Spec::default();
    spec.sheet
        .title
        .insert("tolerance".into(), "ISO 2768-m".into());
    spec.sheet.title.insert("material".into(), "Steel".into());
    let review = api::review(&step, Some(&features), &spec).unwrap();
    let urgent = |r: &api::Review| {
        r.problems
            .iter()
            .map(Problem::labels)
            .chain(r.lint.iter().map(|p| p.labels()))
            .chain(r.structural.iter().map(|p| p.labels()))
            .filter(|p| p.tier == Tier::FixBeforePrinting)
            .count()
    };
    assert_eq!(urgent(&review), 0, "{review:#?}");
    spec.dimensions
        .entry("d:f:hole_pattern:f6/bore.diameter".into())
        .or_default()
        .omit = true;
    let incomplete = api::review(&step, Some(&features), &spec).unwrap();
    assert!(
        urgent(&incomplete) > 0,
        "a removed hole size must stop printing"
    );
}

#[test]
fn every_documented_code_serializes_tier_and_old_values_recompute_it() {
    for row in include_str!("../docs/finding-impact.md")
        .lines()
        .filter(|row| row.starts_with("| `"))
    {
        let code = row.split('`').nth(1).unwrap();
        for info in [false, true] {
            let problem = Problem {
                code: code.into(),
                severity: if info { "info" } else { "warning" }.into(),
                ..Default::default()
            };
            let labels = problem.labels();
            let mut wire = serde_json::to_value(&problem).unwrap();
            assert_eq!(wire["tier"], labels.tier.as_str());
            // Old saved values and client-supplied labels cannot override engine policy.
            wire.as_object_mut().unwrap().remove("tier");
            let decoded: Problem = serde_json::from_value(wire.clone()).unwrap();
            assert_eq!(decoded.labels().tier, labels.tier);
            wire["tier"] = serde_json::json!("client_label");
            let decoded: Problem = serde_json::from_value(wire).unwrap();
            assert_eq!(decoded.labels().tier, labels.tier);
        }
    }
}
