//! Revisions (ADR R3): the Spec carried from one revision of a part to the next through
//! quiddity's correspondence, the new revision drawn with it, and what changed.

use std::path::{Path, PathBuf};
use std::process::Command;

use draftwright::revision::{Revised, diff};
use serde_json::{Value, json};

fn pair(case: &str) -> (PathBuf, PathBuf) {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/revisions");
    (
        dir.join(format!("{case}.old.step.gz")),
        dir.join(format!("{case}.new.step.gz")),
    )
}

fn revised(case: &str, spec: Value) -> Revised {
    let (old, new) = pair(case);
    diff(&old, &new, None, &spec).unwrap_or_else(|e| panic!("{case}: {e:?}"))
}

fn spec(dimensions: Value) -> Value {
    json!({ "schema": "draftwright.spec/3", "sheet": {}, "dimensions": dimensions })
}

fn label(r: &Revised, id: &str) -> String {
    r.new
        .plan()
        .dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap_or_else(|| panic!("no {id}"))
        .label
        .clone()
}

#[test]
fn removing_a_hole_carries_the_entries_of_the_holes_that_renumber() {
    // Old holes 3 (⌀8 at 85, 15) and 4 (⌀6 at 40, 45) are holes 2 and 3 of the new part.
    let r = revised(
        "hole_removed",
        spec(json!({
            "d:f:hole:f8/bore.diameter": { "tolerance": "+0.1 -0" },
            "d:f:hole:f9/location.length.x": { "tolerance": "±0.05" },
            "d:f:hole:f7/bore.diameter": { "tolerance": "±0.2" },
        })),
    );
    assert_eq!(
        r.report.carried_spec["dimensions"],
        json!({
            "d:f:hole:f7/bore.diameter": { "tolerance": "+0.1 -0" },
            "d:f:hole:f8/location.length.x": { "tolerance": "±0.05" },
        })
    );
    assert!(
        label(&r, "d:f:hole:f7/bore.diameter").starts_with("⌀8 +0.1"),
        "the ⌀8 hole keeps its tolerance"
    );
    assert_eq!(label(&r, "d:f:hole:f8/location.length.x"), "40 ±0.05");
    assert_eq!(
        label(&r, "d:f:hole:f8/bore.diameter"),
        "⌀6 THRU",
        "the ⌀6 hole gains nothing"
    );
    let orphaned: Vec<&str> = r
        .report
        .spec
        .iter()
        .filter(|e| e.class == "orphaned")
        .map(|e| e.path.as_str())
        .collect();
    assert_eq!(
        orphaned,
        ["dimensions/d:f:hole:f7/bore.diameter"],
        "the removed hole's entry is kept aside"
    );
    assert_eq!(r.report.verdict, "decide");
    let gone: Vec<_> = r
        .report
        .features
        .iter()
        .map(|f| (f.class.as_str(), f.old.as_deref()))
        .collect();
    assert_eq!(gone, [("orphaned", Some("f:hole:f7"))]);
    assert_eq!(r.report.edges.added, 0);
    assert!(
        r.report.edges.removed > 0,
        "the removed hole's edges are marked"
    );
}

#[test]
fn a_moved_hole_changes_its_location_and_carries_its_tolerance() {
    let r = revised(
        "hole_moved",
        spec(json!({ "d:f:hole:f8/location.length.x": { "tolerance": "±0.1" } })),
    );
    assert_eq!(r.report.verdict, "ok");
    let changed: Vec<_> = r
        .report
        .annotations
        .iter()
        .map(|a| {
            (
                a.class.as_str(),
                a.new.as_deref(),
                a.from.as_deref(),
                a.to.as_deref(),
            )
        })
        .collect();
    assert_eq!(
        changed,
        [(
            "changed",
            Some("d:f:hole:f8/location.length.x"),
            Some("40 ±0.1"),
            Some("25 ±0.1")
        )]
    );
    assert_eq!(r.report.features.len(), 1);
    assert_eq!(r.report.features[0].class, "adapted");
    assert!(r.report.features[0].changes.contains_key("position"));
    let marked = r.marked();
    let layer = |name: &str| {
        marked
            .layers
            .iter()
            .find(|l| l.name == name)
            .map_or(0, |l| l.items.len())
    };
    assert!(
        layer("revision_added") > 0
            && layer("revision_removed") > 0
            && layer("revision_changed") > 0
    );
    assert!(marked.png(50.0).is_ok());
}

#[test]
fn a_resized_boss_is_one_changed_callout() {
    let r = revised("boss_resized", spec(json!({})));
    let changed: Vec<_> = r
        .report
        .annotations
        .iter()
        .map(|a| (a.from.as_deref(), a.to.as_deref()))
        .collect();
    assert_eq!(changed, [(Some("ø20"), Some("ø26"))]);
    // #226: the larger boss's interior ø26 leader runs up its vertical centre line, which the
    // ø20 leader did not; that new warning is the revision's only problem.
    let added: Vec<_> = r
        .report
        .problems
        .added
        .iter()
        .map(|p| p.code.as_str())
        .collect();
    assert_eq!(added, ["leader_along_centreline"]);
    assert!(r.report.problems.resolved.is_empty());
    assert_eq!(r.report.verdict, "problems");
}

#[test]
fn symmetric_repeats_are_asked_not_guessed() {
    let r = revised("plate_thickened", spec(json!({})));
    assert!(
        r.report
            .features
            .iter()
            .any(|f| f.class == "ambiguous" && f.candidates.len() > 1)
    );
    assert_eq!(r.report.verdict, "decide");
    assert!(
        r.markdown("plate", None)
            .contains("can't tell which it is now")
    );
}

#[test]
fn a_turned_part_carries_everything_and_says_so() {
    let r = revised(
        "whole_part_moved",
        spec(json!({ "d:f:hole:f8/location.length.x": { "tolerance": "±0.1" } })),
    );
    assert!(r.report.alignment.found);
    assert!(r.report.features.is_empty(), "every feature carried");
    assert!(r.report.spec.is_empty(), "every entry carried unchanged");
    assert!(r.report.summary.contains("turned"));
    assert_eq!(
        r.report.edges.added + r.report.edges.removed,
        0,
        "a turned part's edges are not compared"
    );
}

#[test]
fn the_same_file_has_nothing_to_report() {
    let (old, _) = pair("hole_moved");
    let r = diff(
        &old,
        &old,
        None,
        &spec(json!({ "d:f:hole:f8/location.length.x": { "tolerance": "±0.1" } })),
    )
    .unwrap();
    assert_eq!(r.report.verdict, "ok");
    assert!(
        r.report.features.is_empty() && r.report.annotations.is_empty() && r.report.spec.is_empty()
    );
    assert_eq!(r.report.edges.added + r.report.edges.removed, 0);
}

#[test]
fn the_command_line_writes_the_report_and_the_marked_drawing() {
    let (old, new) = pair("hole_removed");
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("revision_cli");
    let spec_path = dir.with_extension("spec.json");
    std::fs::create_dir_all(dir.parent().unwrap()).unwrap();
    std::fs::write(
        &spec_path,
        spec(json!({ "d:f:hole:f7/bore.diameter": { "tolerance": "±0.2" } })).to_string(),
    )
    .unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .args([
            "diff",
            old.to_str().unwrap(),
            new.to_str().unwrap(),
            "--spec",
            spec_path.to_str().unwrap(),
            "-o",
            dir.to_str().unwrap(),
            "--title",
            "Plate",
        ])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stdout)
    );
    let printed: Value = serde_json::from_slice(&out.stdout).unwrap();
    assert_eq!(printed["verdict"], "decide");
    for file in [
        "report.json",
        "report.md",
        "spec.json",
        "changes.svg",
        "changes.png",
        "drawing.svg",
    ] {
        assert!(dir.join(file).is_file(), "{file}");
    }
    let md = std::fs::read_to_string(dir.join("report.md")).unwrap();
    assert!(md.starts_with("### Plate\n\n⚠️"), "{md}");
    assert!(
        md.contains("`dimensions/d:f:hole:f7/bore.diameter` (orphaned)"),
        "{md}"
    );
    assert!(md.contains("![What changed"), "{md}");
}

#[test]
fn an_unapplied_edit_on_both_revisions_does_not_ask_for_a_new_decision() {
    let (old, _) = pair("hole_moved");
    let r = diff(
        &old,
        &old,
        None,
        &spec(json!({ "d:f:hole:99/bore.diameter": { "tolerance": "±0.1" } })),
    )
    .unwrap();
    let classes: Vec<_> = r.report.spec.iter().map(|e| e.class.as_str()).collect();
    assert!(classes.is_empty());
    assert_eq!(r.report.verdict, "ok", "{}", r.report.summary);
    assert!(
        r.new
            .plan()
            .problems
            .iter()
            .any(|p| p.code == "dimension_edit_unapplied")
    );
    assert_eq!(
        r.report.carried_spec["dimensions"]["d:f:hole:99/bore.diameter"],
        json!({"tolerance":"±0.1"})
    );
    assert!(r.new.plan().dimensions.iter().any(|d| d.placed.is_some()));
}
