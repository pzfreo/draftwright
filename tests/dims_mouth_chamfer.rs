//! A chamfer on a hole's mouth (quiddity 0.5.0, quiddity-rust#244) is stated in the hole's callout:
//! after the recess it breaks, `C0.25` for equal legs at 45° and `0.25 × 30°` otherwise, as every
//! other chamfer is, and the recess's depth reads from the surface the mouth opens on (the
//! cylinder's 2.0 plus the 0.25 entry is `↧ 2.25`). `cbore_chamfer.step` is quiddity's own
//! fixture: a 30 × 30 × 5 plate, a ⌀8 × 2 boss, a ⌀3.3 through hole with a ⌀5 counterbore 2.25
//! deep from the boss top, and a 0.25 chamfer on the counterbore's mouth.
use std::path::PathBuf;

#[path = "common/corpus.rs"]
mod corpus;

use draftwright::model::{self, Analysis, Plan, Spec};
use draftwright::text::Font;
use serde_json::json;

fn part() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/cbore_chamfer.step")
}

fn analysis() -> Analysis {
    model::analyse_native(&part()).unwrap().1
}

fn hole_mut(a: &mut Analysis) -> &mut serde_json::Map<String, serde_json::Value> {
    &mut a
        .features
        .iter_mut()
        .find(|f| f.kind == "hole")
        .unwrap()
        .parameters
}

/// The hole's callout: its label, its words joined, and its measurements.
fn callout(plan: &Plan) -> (String, String, Vec<(String, f64)>) {
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .expect("the hole's callout");
    let words = d
        .terms
        .iter()
        .map(String::as_str)
        .filter(|t| !t.trim().is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    let measured = d
        .measurements
        .iter()
        .map(|m| (m.parameter.clone(), m.value))
        .collect();
    (d.label.clone(), words, measured)
}

/// The words, and measurements, of the one hole callout of fixture `parts/mouth_<case>.step`, its
/// mouth chamfers read from real geometry (generated: `tests/fixtures/parts/mouth_chamfers.md`).
fn fixture(case: &str) -> (Analysis, String, Vec<(String, f64)>) {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join(format!("tests/fixtures/parts/mouth_{case}.step"));
    let a = model::analyse_native(&path).unwrap().1;
    let (_, words, measured) = callout(&plan_of(&a));
    (a, words, measured)
}

fn plan_of(a: &Analysis) -> Plan {
    model::plan_recovered(a, &Spec::default()).unwrap()
}

/// What the callout says for *a* with its hole's parameters edited by *edit*.
fn says(edit: impl FnOnce(&mut serde_json::Map<String, serde_json::Value>)) -> String {
    let mut a = analysis();
    edit(hole_mut(&mut a));
    callout(&plan_of(&a)).1
}

/// The repro end to end: quiddity's record, the callout, the drawing's text, and lint.
#[test]
fn the_counterbore_mouth_chamfer_is_stated_from_the_record_to_the_svg() {
    let a = analysis();
    // The record: the counterbore stays the cylinder's 2.0; the chamfer is a nested claim.
    let hole = a.features.iter().find(|f| f.kind == "hole").unwrap();
    assert_eq!(hole.parameters["cbore"]["depth"], 2.0);
    let entry = &hole.parameters["mouth_chamfers"][0];
    assert_eq!(
        (entry["size"].as_f64(), entry["angle"].as_f64()),
        (Some(0.25), Some(45.0))
    );
    // Its cone is not one of the hole's own faces.
    assert!(!hole.faces.contains(&1), "{:?}", hole.faces);

    let plan = plan_of(&a);
    let (label, words, measured) = callout(&plan);
    assert_eq!(words, "⌀3.3 THRU ⌴ ⌀5 ↧ 2.25 C0.25");
    assert_eq!(
        label.lines().collect::<Vec<_>>(),
        ["⌀3.3 THRU ⌴ ⌀5 ↧ 2.25", "C0.25"]
    );
    for (parameter, value) in [
        ("bore.diameter", 3.3),
        ("counterbore.diameter", 5.0),
        ("counterbore.depth", 2.25),
        ("mouth_chamfer.size", 0.25),
    ] {
        assert!(
            measured
                .iter()
                .any(|(p, v)| p == parameter && (v - value).abs() < 1e-9),
            "{parameter}: {measured:?}"
        );
    }
    // 45° with equal legs is the C form: no angle is claimed.
    assert!(
        !measured.iter().any(|(p, _)| p == "mouth_chamfer.angle"),
        "{measured:?}"
    );

    // The cone is explained: no face of the part is unclaimed but the two the plate's and boss's
    // tops already left unclaimed, and the cone (face 1) is not one of them.
    let unclaimed: Vec<&str> = plan
        .problems
        .iter()
        .filter(|p| p.code == "unclaimed_geometry")
        .flat_map(|p| p.subjects.iter().map(String::as_str))
        .collect();
    assert!(!unclaimed.contains(&"face:1"), "{unclaimed:?}");
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "shape_not_recognised"),
        "{:?}",
        plan.problems.iter().map(|p| &p.code).collect::<Vec<_>>()
    );

    // The drawing's text carries it, and lint finds every requirement stated.
    let (part, a) = model::analyse_native(&part()).unwrap();
    let font = Font::new(
        std::fs::read(
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"),
        )
        .unwrap(),
    )
    .unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let written: Vec<String> = drawing.text.iter().map(|r| r.text.clone()).collect();
    assert!(written.iter().any(|t| t == "C0.25"), "{written:?}");
    assert!(written.iter().any(|t| t.contains("2.25")), "{written:?}");
    let svg = drawing.svg();
    assert!(svg.contains("C0.25") && svg.contains("2.25"));
    let found: Vec<_> = draftwright::lint::lint(&a, &drawing)
        .into_iter()
        .filter(|i| i.code.contains("hole") || i.message.contains("hole:"))
        .map(|i| i.message)
        .collect();
    assert!(found.is_empty(), "{found:?}");
}

/// A chamfer that is not equal legs at 45° says its size and angle, and claims both.
#[test]
fn a_chamfer_off_45_degrees_states_its_size_and_angle() {
    let (_, words, measured) = fixture("angled");
    assert_eq!(words, "⌀6 THRU 0.4 × 30°");
    for (parameter, value) in [("mouth_chamfer.size", 0.4), ("mouth_chamfer.angle", 30.0)] {
        assert!(
            measured
                .iter()
                .any(|(p, v)| p == parameter && (v - value).abs() < 1e-3),
            "{parameter}: {measured:?}"
        );
    }
}

/// A plain hole's mouth chamfer follows its bore; there is no recess whose depth changes.
#[test]
fn a_plain_holes_mouth_chamfer_follows_its_bore() {
    let (a, words, measured) = fixture("plain");
    assert_eq!(words, "⌀6 THRU C0.5");
    assert!(
        measured
            .iter()
            .any(|(p, v)| p == "mouth_chamfer.size" && *v == 0.5)
    );
    // Its cone is explained by the hole.
    assert!(
        !plan_of(&a)
            .problems
            .iter()
            .any(|p| p.code == "shape_not_recognised")
    );
}

/// Only the chamfer on a recess mouth deepens it: a chamfer on the bore's far mouth does not move
/// the counterbore's depth, and is the far side.
#[test]
fn only_the_chamfer_on_a_recess_mouth_adds_to_its_depth() {
    assert_eq!(
        fixture("cbore_far_only").1,
        "⌀6 THRU ⌴ ⌀10 ↧ 2 C0.25 FAR SIDE"
    );
}

/// The chamfer is judged from the end the rest of the callout is stated from: a counterbore cut
/// from the underside and seen in plan is stated at its own mouth, plain, whatever the view; the
/// far bore's chamfer is the far side, and is not the counterbore's chamfer's twin (`BOTH SIDES`)
/// though both are `C0.25`.
#[test]
fn a_counterbores_chamfers_are_judged_from_its_own_mouth() {
    assert_eq!(fixture("cbore_under").1, "⌀6 THRU ⌴ ⌀10 ↧ 2 C0.25");
    assert_eq!(
        fixture("cbore_far").1,
        "⌀6 THRU ⌴ ⌀10 ↧ 2 C0.25 C0.25 FAR SIDE"
    );
}

/// A blind bore's depth, like a counterbore's, is stated from the surface it opens on (ISO 129-1,
/// ASME Y14.5): the 2.5 mm cylinder under a 0.5 chamfer is `↧ 3`, and its claim says so.
#[test]
fn a_blind_bores_depth_includes_its_mouth_chamfer() {
    let (a, words, measured) = fixture("blind");
    assert_eq!(words, "⌀4 ↧ 3 C0.5");
    assert!(
        measured
            .iter()
            .any(|(p, v)| p == "bore.depth" && (v - 3.0).abs() < 1e-9)
    );
    let hole = a.features.iter().find(|f| f.kind == "hole").unwrap();
    assert_eq!(hole.parameters["depth"], 2.5);
}

/// A hole table's row states the chamfer in its size cell, and its entry plane is the surface the
/// hole opens on (the chamfer's 0.25 lies above the counterbore's cylinder).
#[test]
fn a_hole_table_row_states_the_chamfer() {
    let path =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mouth_cbore_far.step");
    let a = model::analyse_native(&path).unwrap().1;
    let spec = Spec {
        hole_table: draftwright::hole_table::Setting::Always,
        ..Spec::default()
    };
    let plan = model::plan_recovered(&a, &spec).unwrap();
    let rows: Vec<_> = plan.hole_tables.iter().flat_map(|t| &t.rows).collect();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].cells[1], "⌀6 THRU ⌴ ⌀10 ↧ 2 C0.25 C0.25 FAR SIDE");
    assert_eq!(rows[0].cells[4], "5 / −Z");
}

/// A through hole chamfered at both ends says so once; one chamfered only at the end its view
/// does not look at says which end. The plan view looks down on the top face, whichever end
/// quiddity calls the hole's opening.
#[test]
fn the_far_end_of_a_through_hole_is_said() {
    assert_eq!(fixture("both").1, "⌀6 THRU C0.5 BOTH SIDES");
    assert_eq!(fixture("far_side").1, "⌀6 THRU C0.5 FAR SIDE");
}

/// Two different chamfers are stated each, the far end's with its side.
#[test]
fn a_different_far_chamfer_is_stated_as_well() {
    assert_eq!(fixture("mixed").1, "⌀6 THRU C0.5 0.4 × 30° FAR SIDE");
}

/// A countersink is not a mouth chamfer: a 0.5 × 45° edge on a ⌀2 bore is, to quiddity, a ⌀3 × 90°
/// countersink (geometry cannot tell intent), and is stated as one.
#[test]
fn a_countersink_stays_a_countersink() {
    let (a, words, _) = fixture("countersink");
    assert_eq!(words, "⌀2 THRU ⌵ ⌀3 × 90°");
    let hole = a.features.iter().find(|f| f.kind == "hole").unwrap();
    assert!(hole.parameters.get("mouth_chamfers").is_none());
}

/// A hole without a mouth chamfer keeps the callout it had; an older record lacks the field (a
/// record edit).
#[test]
fn a_hole_without_a_mouth_chamfer_is_unchanged() {
    let words = says(|h| {
        h["mouth_chamfers"] = json!([]);
    });
    assert_eq!(words, "⌀3.3 THRU ⌴ ⌀5 ↧ 2");
    let words = says(|h| {
        h.remove("mouth_chamfers");
    });
    assert_eq!(words, "⌀3.3 THRU ⌴ ⌀5 ↧ 2");
}

/// `flanged_spool_132` (the corpus's cadgenbench part): six ⌀10 through holes on a ⌀90 bolt circle,
/// each chamfered 1 × 45° on the top face. quiddity records each hole drilled up from the flange's
/// far face (axis +z) with the chamfer at the other end, but the plan view looks down on the
/// chamfered face, so the callout states the chamfer without a side: the end seen, not the one
/// quiddity calls the opening. The ⌀70 counterbore has no chamfer and is unchanged.
#[test]
fn a_chamfer_on_the_face_the_view_looks_at_has_no_side() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let (_, a) = model::analyse_native(&corpus.join("cadgenbench/flanged_spool_132.step")).unwrap();
    let plan = plan_of(&a);
    let words: Vec<String> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout")
        .map(|d| {
            d.terms
                .iter()
                .map(String::as_str)
                .filter(|t| !t.trim().is_empty())
                .collect::<Vec<_>>()
                .join(" ")
        })
        .collect();
    assert!(
        words.contains(&"6× ⌀10 THRU C1 EQ SP ON ø90 BC".to_string()),
        "{words:?}"
    );
    assert!(
        words.contains(&"⌀62 THRU ⌴ ⌀70 ↧ 10".to_string()),
        "{words:?}"
    );
}
