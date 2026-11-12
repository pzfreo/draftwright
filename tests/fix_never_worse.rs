//! A fix the engine offers as local must not make the drawing worse: it applies, and leaves
//! strictly fewer findings than before (a trade that adds as many findings as it removes is
//! withheld).
//! A fix that re-lays the whole sheet carries no such promise: the engine cannot tell its outcome
//! without planning it, so a caller previews it (`Session::apply` without committing).
//! Structural counts only, over the quality parts and the editor's samples.

use std::io::Read as _;

use draftwright::{fixes::Scope, model, session::Session};

#[path = "common/corpus.rs"]
mod corpus;

/// Applies the offered local fixes (first fix of each finding first, at most `CAP` distinct ones
/// per part) and returns (distinct fixes offered, distinct fixes applied, the worse-or-equal
/// ones). An audit also applies the wider fixes and prints every outcome.
fn check(
    name: &str,
    part: draftwright::haecceity::Part,
    analysis: model::Analysis,
) -> (usize, usize, Vec<String>) {
    const CAP: usize = 3;
    let spec = serde_json::from_value(serde_json::json!({})).unwrap();
    let Ok(mut session) = Session::new(part, analysis, spec) else {
        return (0, 0, Vec::new());
    };
    let before = session.plan().problems.len();
    let problems = session.plan().problems.clone();
    let mut seen: Vec<Vec<draftwright::items::Op>> = Vec::new();
    let mut applied = 0;
    let mut worse = Vec::new();
    let offers = problems
        .iter()
        .map(|p| p.offered_fixes())
        .collect::<Vec<_>>();
    let rank = offers.iter().map(Vec::len).max().unwrap_or(0);
    for k in 0..rank {
        for (p, fixes) in problems.iter().zip(&offers) {
            let Some(fix) = fixes.get(k) else { continue };
            if seen.contains(&fix.ops) {
                continue;
            }
            seen.push(fix.ops.clone());
            let local = fix.scope() == Scope::Local;
            if !audit() && (!local || applied == CAP) {
                continue;
            }
            // A local fix the engine then refuses to apply is no fix either.
            let Ok(delta) = session.apply(&fix.ops, false) else {
                if audit() {
                    eprintln!("REFUSED\t{name}\t{}\t{:?}", p.id, fix.scope());
                }
                if local {
                    worse.push(format!(
                        "{name}: fix for {} is refused: {}",
                        p.id,
                        fix.description()
                    ));
                }
                continue;
            };
            applied += 1;
            let after = before + delta.problems_added.len() - delta.problems_resolved.len();
            if audit() {
                eprintln!(
                    "FIX\t{name}\t{}\t{before}\t{after}\t{:?}\t{}",
                    p.id,
                    fix.scope(),
                    fix.description()
                );
            }
            if local && after >= before {
                worse.push(format!(
                    "{name}: fix for {} leaves {after} findings, was {before}: {}",
                    p.id,
                    fix.description()
                ));
            }
        }
    }
    eprintln!(
        "{name}: {before} findings, {} fixes offered, {applied} applied, {} not better",
        seen.len(),
        worse.len()
    );
    (seen.len(), applied, worse)
}

/// `DW_FIX_AUDIT=1` applies every offered fix and reports instead of failing (a measurement run).
fn audit() -> bool {
    std::env::var_os("DW_FIX_AUDIT").is_some_and(|v| v == "1")
}

fn verdict(worse: Vec<String>) {
    if audit() {
        for w in &worse {
            eprintln!("NOT BETTER {w}");
        }
        return;
    }
    assert!(worse.is_empty(), "{}", worse.join("\n"));
}

#[test]
fn local_fixes_leave_fewer_findings_on_the_editor_samples() {
    let mut worse = Vec::new();
    for file in [
        "mounting-plate.step.gz",
        "draftwright-demo-bracket.step.gz",
        "specify-sample-spindle.step.gz",
        "turned_spindle_fits.step.gz",
        "specify-0.1.4-plate.step.gz",
    ] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/samples")
            .join(file);
        let mut bytes = Vec::new();
        flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
            .read_to_end(&mut bytes)
            .unwrap();
        let (part, analysis) = model::analyse_native_step(std::path::Path::new(file), &bytes)
            .unwrap_or_else(|e| panic!("{file}: {e:?}"));
        worse.extend(check(file, part, analysis).2);
    }
    verdict(worse);
}

#[test]
fn local_fixes_leave_fewer_findings_on_the_quality_parts() {
    let Some(root) = corpus::dir() else { return };
    let list = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tools/quality_parts.txt"
    ))
    .unwrap();
    let mut worse = Vec::new();
    let mut offered = 0;
    for file in list.lines().map(str::trim).filter(|l| !l.is_empty()) {
        let Ok((part, analysis)) = model::analyse_native(&root.join(file)) else {
            continue;
        };
        let (n, _, w) = check(file, part, analysis);
        offered += n;
        worse.extend(w);
    }
    eprintln!("quality parts: {offered} distinct fixes offered");
    verdict(worse);
}
