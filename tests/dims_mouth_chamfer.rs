//! A chamfer on a hole's mouth (quiddity 0.5.0, quiddity-rust#244) is stated in the hole's callout:
//! after the recess it breaks, `C0.25` for equal legs at 45° and `0.25 × 30°` otherwise, as every
//! other chamfer is, and the recess's depth reads from the surface the mouth opens on (the
//! cylinder's 2.0 plus the 0.25 entry is `↧ 2.25`). `cbore_chamfer.step` is quiddity's own
//! fixture: a 30 × 30 × 5 plate, a ⌀8 × 2 boss, a ⌀3.3 through hole with a ⌀5 counterbore 2.25
//! deep from the boss top, and a 0.25 chamfer on the counterbore's mouth.
use std::path::PathBuf;

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
    let mut a = analysis();
    {
        let hole = hole_mut(&mut a);
        hole["mouth_chamfers"][0]["angle"] = json!(30.0);
        hole["mouth_chamfers"][0]["size"] = json!(0.4);
        hole["mouth_chamfers"][0]["depth"] = json!(0.231);
    }
    let (_, words, measured) = callout(&plan_of(&a));
    assert_eq!(words, "⌀3.3 THRU ⌴ ⌀5 ↧ 2.231 0.4 × 30°");
    for (parameter, value) in [("mouth_chamfer.size", 0.4), ("mouth_chamfer.angle", 30.0)] {
        assert!(
            measured
                .iter()
                .any(|(p, v)| p == parameter && (v - value).abs() < 1e-9),
            "{parameter}: {measured:?}"
        );
    }
}

/// A plain hole's mouth chamfer follows its bore; there is no recess whose depth changes.
#[test]
fn a_plain_holes_mouth_chamfer_follows_its_bore() {
    let words = says(|h| {
        h["cbore"] = json!(null);
        h["mouth_chamfers"][0]["diameter"] = json!(3.3);
    });
    assert_eq!(words, "⌀3.3 THRU C0.25");
}

/// Only the chamfer on the counterbore's own mouth deepens it: a chamfer on another diameter
/// does not move the counterbore's depth.
#[test]
fn only_the_chamfer_on_a_recess_mouth_adds_to_its_depth() {
    let words = says(|h| h["mouth_chamfers"][0]["diameter"] = json!(3.3));
    assert_eq!(words, "⌀3.3 THRU ⌴ ⌀5 ↧ 2 C0.25");
}

/// A through hole chamfered at both ends says so once; one chamfered only at the far end says
/// which end, and a different far chamfer is stated as well.
#[test]
fn the_far_end_of_a_through_hole_is_said() {
    let both = says(|h| {
        h["cbore"] = json!(null);
        h["mouth_chamfers"][0]["diameter"] = json!(3.3);
        let mut far = h["mouth_chamfers"][0].clone();
        far["axis"] = json!([0.0, 0.0, 1.0]);
        h["mouth_chamfers"].as_array_mut().unwrap().push(far);
    });
    assert_eq!(both, "⌀3.3 THRU C0.25 BOTH SIDES");
    let far_only = says(|h| {
        h["cbore"] = json!(null);
        h["mouth_chamfers"][0]["diameter"] = json!(3.3);
        h["mouth_chamfers"][0]["axis"] = json!([0.0, 0.0, 1.0]);
    });
    assert_eq!(far_only, "⌀3.3 THRU C0.25 FAR SIDE");
    let two = says(|h| {
        h["cbore"] = json!(null);
        h["mouth_chamfers"][0]["diameter"] = json!(3.3);
        let mut far = h["mouth_chamfers"][0].clone();
        far["axis"] = json!([0.0, 0.0, 1.0]);
        far["angle"] = json!(30.0);
        far["size"] = json!(0.5);
        h["mouth_chamfers"].as_array_mut().unwrap().push(far);
    });
    assert_eq!(two, "⌀3.3 THRU C0.25 0.5 × 30° FAR SIDE");
}

/// A countersink is not a mouth chamfer: a 0.5 × 45° edge on a ⌀2 bore is, to quiddity, a ⌀3 × 90°
/// countersink (geometry cannot tell intent), and is stated as one.
#[test]
fn a_countersink_stays_a_countersink() {
    let words = says(|h| {
        h["cbore"] = json!(null);
        h["mouth_chamfers"] = json!([]);
        h["diameter"] = json!(2.0);
        h["csink"] = json!({ "major_diameter": 3.0, "included_angle": 90.0 });
    });
    assert_eq!(words, "⌀2 THRU ⌵ ⌀3 × 90°");
}

/// A hole without a mouth chamfer keeps the callout it had.
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
