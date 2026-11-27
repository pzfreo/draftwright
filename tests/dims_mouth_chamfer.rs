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

/// A counterbore cut from the underside and seen in plan says `FAR SIDE` directly after its
/// recess terms and before its own mouth chamfer; on a top-side counterbore the far bore's
/// chamfer stays `C0.25 FAR SIDE`, distinct from the counterbore's own `C0.25`.
#[test]
fn a_counterbores_chamfers_are_judged_from_its_own_mouth() {
    assert_eq!(fixture("cbore_under").1, "⌀6 THRU ⌴ ⌀10 ↧ 2 FAR SIDE C0.25");
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

fn words_of(d: &draftwright::dims::PlannedDimension) -> String {
    d.terms
        .iter()
        .map(String::as_str)
        .filter(|t| !t.trim().is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

fn bundled_font() -> Font {
    Font::new(
        std::fs::read(
            PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"),
        )
        .unwrap(),
    )
    .unwrap()
}

fn far_countersink() -> (haecceity::Part, Analysis) {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/far_countersink.step.gz");
    model::analyse_native(&path).unwrap()
}

fn rotate_analysis(a: &mut Analysis, rot: impl Fn([f64; 3]) -> [f64; 3]) {
    let rotate_box = |lo: [f64; 3], hi: [f64; 3]| {
        let mut rlo = [f64::INFINITY; 3];
        let mut rhi = [f64::NEG_INFINITY; 3];
        for mask in 0..8 {
            let p = rot([
                if mask & 1 == 0 { lo[0] } else { hi[0] },
                if mask & 2 == 0 { lo[1] } else { hi[1] },
                if mask & 4 == 0 { lo[2] } else { hi[2] },
            ]);
            for k in 0..3 {
                rlo[k] = rlo[k].min(p[k]);
                rhi[k] = rhi[k].max(p[k]);
            }
        }
        [rlo, rhi]
    };
    let [lo, hi] = a.envelope;
    a.envelope = rotate_box(lo, hi);
    if let Some(rec) = &mut a.recognition {
        for v in &mut rec.frame {
            *v = rot(*v);
        }
    }
    if let Some(ink) = &mut a.edge_ink {
        for poly in &mut ink.edges {
            for p in poly {
                *p = rot(*p);
            }
        }
        for plane in &mut ink.planes {
            plane.normal = rot(plane.normal);
        }
        for curved in &mut ink.curved {
            let [clo, chi] = curved.bounds;
            curved.bounds = rotate_box(clo, chi);
            if let Some(cyl) = &mut curved.cylinder {
                cyl.origin = rot(cyl.origin);
                cyl.axis = rot(cyl.axis);
            }
        }
        ink.resolved.clear();
    }
    for fa in &mut a.face_attachments {
        if let Some(b) = fa.bounds {
            let [nlo, nhi] = rotate_box([b[0], b[1], b[2]], [b[3], b[4], b[5]]);
            fa.bounds = Some([nlo[0], nlo[1], nlo[2], nhi[0], nhi[1], nhi[2]]);
        }
        if let Some(n) = &mut fa.normal {
            *n = rot(*n);
        }
        for s in &mut fa.sites {
            *s = rot(*s);
        }
        for s in &mut fa.face_on {
            *s = rot(*s);
        }
    }
    a.principal_profiles.clear();
}

fn rotate_x(a: &mut Analysis, degrees: f64) {
    let (s, c) = degrees.to_radians().sin_cos();
    rotate_analysis(a, |p| [p[0], c * p[1] - s * p[2], s * p[1] + c * p[2]]);
}

/// Countersinks cut from below say `FAR SIDE` in the plan view; countersinks on the top face stay
/// plain, and lint accepts the drawing without hole findings.
#[test]
fn a_countersink_cut_from_below_says_far_side_in_plan_view() {
    let (part, a) = far_countersink();
    let plan = plan_of(&a);
    let callout_for = |id: &str| {
        words_of(
            plan.dimensions
                .iter()
                .find(|d| d.id == id)
                .unwrap_or_else(|| panic!("missing {id}")),
        )
    };
    assert_eq!(
        callout_for("d:f:hole:f17/bore.diameter"),
        "2× ⌀6 THRU ⌵ ⌀12 × 90° FAR SIDE"
    );
    assert_eq!(
        callout_for("d:f:hole:f12/bore.diameter"),
        "2× ⌀5 THRU ⌵ ⌀10 × 90°"
    );
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "hole_recess_side_undecided")
    );
    let drawing = model::render(&part, &a, &plan, &bundled_font()).unwrap();
    let hole_issues: Vec<_> = draftwright::lint::lint(&a, &drawing)
        .into_iter()
        .filter(|i| i.code.contains("hole") || i.message.contains("hole:"))
        .map(|i| i.message)
        .collect();
    assert!(hole_issues.is_empty(), "{hole_issues:?}");
}

/// A counterbore cut from below says `FAR SIDE` directly after its recess terms (both plain and
/// before its own mouth chamfer), and lint accepts the rendered drawing.
#[test]
fn a_counterbore_cut_from_below_says_far_side() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mouth_cbore_under.step");
    let (part, a) = model::analyse_native(&path).unwrap();
    let plan = plan_of(&a);
    assert_eq!(callout(&plan).1, "⌀6 THRU ⌴ ⌀10 ↧ 2 FAR SIDE C0.25");
    let drawing = model::render(&part, &a, &plan, &bundled_font()).unwrap();
    let hole_issues: Vec<_> = draftwright::lint::lint(&a, &drawing)
        .into_iter()
        .filter(|i| i.code.contains("hole") || i.message.contains("hole:"))
        .map(|i| i.message)
        .collect();
    assert!(hole_issues.is_empty(), "{hole_issues:?}");

    let mut plain_cbore = a.clone();
    let params = hole_mut(&mut plain_cbore);
    params.remove("mouth_chamfers");
    params["cbore"]["depth"] = json!(2.0);
    assert_eq!(
        callout(&plan_of(&plain_cbore)).1,
        "⌀6 THRU ⌴ ⌀10 ↧ 2 FAR SIDE"
    );
}

/// The same bottom-countersunk hole seen from the opposite view (looking at the bottom face) has
/// no `FAR SIDE`, while the top-countersunk hole gains `FAR SIDE`; a side-on view stays plain
/// without raising `hole_recess_side_undecided`.
#[test]
fn a_far_recess_in_the_opposite_view_has_no_far_side_and_side_on_is_plain() {
    let (_, a) = far_countersink();
    let spec = Spec::default();
    let plan = plan_of(&a);

    let mut opposite_views = plan.views.clone();
    opposite_views
        .iter_mut()
        .find(|v| v.id == "v:plan")
        .unwrap()
        .direction = [0.0, 0.0, -1.0];
    let (dims, _, problems) =
        draftwright::dims::plan(&a, &spec, &plan.sheet, &opposite_views).unwrap();
    let callout_in = |dims: &[draftwright::dims::PlannedDimension], id: &str| {
        words_of(
            dims.iter()
                .find(|d| d.id == id)
                .unwrap_or_else(|| panic!("missing {id}")),
        )
    };
    assert_eq!(
        callout_in(&dims, "d:f:hole:f17/bore.diameter"),
        "2× ⌀6 THRU ⌵ ⌀12 × 90°"
    );
    assert_eq!(
        callout_in(&dims, "d:f:hole:f12/bore.diameter"),
        "2× ⌀5 THRU ⌵ ⌀10 × 90° FAR SIDE"
    );
    assert!(
        !problems
            .iter()
            .any(|p| p.code == "hole_recess_side_undecided")
    );

    let mut side_on_views = plan.views.clone();
    let plan_view = side_on_views.iter_mut().find(|v| v.id == "v:plan").unwrap();
    plan_view.direction = [0.0, -1.0, 0.0];
    plan_view.up = [0.0, 0.0, 1.0];
    let (dims, _, problems) =
        draftwright::dims::plan(&a, &spec, &plan.sheet, &side_on_views).unwrap();
    assert_eq!(
        callout_in(&dims, "d:f:hole:f17/bore.diameter"),
        "2× ⌀6 THRU ⌵ ⌀12 × 90°"
    );
    assert!(
        !problems
            .iter()
            .any(|p| p.code == "hole_recess_side_undecided"),
        "side-on view must not warn: {problems:?}"
    );
}

/// Hole-table size cells omit recess `FAR SIDE`, including with an authored suffix or a thread.
#[test]
fn hole_table_omits_far_side_for_far_recesses() {
    let (_, a) = far_countersink();
    let mut spec = Spec {
        hole_table: draftwright::hole_table::Setting::Always,
        ..Spec::default()
    };
    spec.sheet.size = Some("A3".into());
    let plan = model::plan_recovered(&a, &spec).unwrap();
    let rows: Vec<_> = plan.hole_tables.iter().flat_map(|t| &t.rows).collect();
    assert_eq!(rows.len(), 4);
    for feature in ["f:hole:f17", "f:hole:f18"] {
        let row = rows.iter().find(|r| r.feature == feature).unwrap();
        assert_eq!(row.cells[1], "⌀6 THRU ⌵ ⌀12 × 90°");
    }
    for feature in ["f:hole:f12", "f:hole:f14"] {
        let row = rows.iter().find(|r| r.feature == feature).unwrap();
        assert_eq!(row.cells[1], "⌀5 THRU ⌵ ⌀10 × 90°");
    }

    let suffix_spec: Spec = serde_json::from_value(json!({
        "hole_table": "always",
        "sheet": {"size": "A3"},
        "dimensions": { "d:f:hole:f17/bore.diameter": { "text": {"suffix": "AFTER PLATING"} } }
    }))
    .unwrap();
    let plan = model::plan_recovered(&a, &suffix_spec).unwrap();
    let row = plan
        .hole_tables
        .iter()
        .flat_map(|t| &t.rows)
        .find(|r| r.feature == "f:hole:f17")
        .unwrap();
    assert_eq!(row.cells[1], "⌀6 THRU ⌵ ⌀12 × 90° AFTER PLATING");

    let thread_spec: Spec = serde_json::from_value(json!({
        "hole_table": "always",
        "sheet": {"size": "A3"},
        "features": { "f:hole:f17": {"thread": {"designation": "M7"}} }
    }))
    .unwrap();
    let plan = model::plan_recovered(&a, &thread_spec).unwrap();
    let row = plan
        .hole_tables
        .iter()
        .flat_map(|t| &t.rows)
        .find(|r| r.feature == "f:hole:f17")
        .unwrap();
    assert!(!row.cells[1].contains("FAR SIDE"), "{:?}", row.cells);
}

/// Four identical bores with two countersink seats on the top face and two on the bottom face must
/// not be grouped into one `4× … FAR SIDE` callout (loose or as a recognised pattern).
#[test]
fn mixed_countersink_seats_are_not_grouped() {
    let (_, mut a) = far_countersink();
    let mut ids = Vec::new();
    for f in a.features.iter_mut().filter(|f| f.kind == "hole") {
        ids.push(f.id.clone());
        f.parameters["diameter"] = json!(6.0);
        f.parameters["axis"] = json!([0.0, 0.0, -1.0]);
        let csink = f.parameters["csink"].as_object_mut().unwrap();
        csink.insert("major_diameter".into(), json!(12.0));
        csink.insert("drill_diameter".into(), json!(6.0));
        csink.insert("included_angle".into(), json!(90.0));
    }
    assert_eq!(ids.len(), 4);
    if let Some(rec) = &mut a.recognition {
        rec.patterns.push(draftwright::bridge::HolePattern {
            presentation_order: None,
            legacy_id: None,
            id: "f:hole_pattern:mixed".into(),
            arrangement: "grid".into(),
            members: ids,
            faces: vec![],
            parameters: serde_json::Map::new(),
        });
    }
    let plan = plan_of(&a);
    let mut callouts: Vec<String> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout")
        .map(words_of)
        .collect();
    callouts.sort();
    assert_eq!(
        callouts,
        vec!["2× ⌀6 THRU ⌵ ⌀12 × 90°", "2× ⌀6 THRU ⌵ ⌀12 × 90° FAR SIDE"]
    );
}

/// `mouth_cbore_under` and `mouth_cbore_far_only` must print distinct callouts (`FAR SIDE C0.25`
/// vs `C0.25 FAR SIDE`) and lint must reject swapping them.
#[test]
fn opposite_cbore_and_far_chamfer_operations_are_distinct_in_callout_and_lint() {
    let under_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mouth_cbore_under.step");
    let far_only_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mouth_cbore_far_only.step");
    let (under_part, under_a) = model::analyse_native(&under_path).unwrap();
    let (far_part, far_a) = model::analyse_native(&far_only_path).unwrap();
    let under_plan = plan_of(&under_a);
    let far_plan = plan_of(&far_a);

    assert_eq!(callout(&under_plan).1, "⌀6 THRU ⌴ ⌀10 ↧ 2 FAR SIDE C0.25");
    assert_eq!(callout(&far_plan).1, "⌀6 THRU ⌴ ⌀10 ↧ 2 C0.25 FAR SIDE");

    let font = bundled_font();
    let under_drawing = model::render(&under_part, &under_a, &under_plan, &font).unwrap();
    let far_drawing = model::render(&far_part, &far_a, &far_plan, &font).unwrap();
    let hole_issues = |a: &Analysis, d: &draftwright::output::Drawing| -> Vec<String> {
        draftwright::lint::lint(a, d)
            .into_iter()
            .filter(|i| i.code.contains("hole") || i.message.contains("hole:"))
            .map(|i| format!("{}: {}", i.code, i.message))
            .collect()
    };
    assert!(hole_issues(&under_a, &under_drawing).is_empty());
    assert!(hole_issues(&far_a, &far_drawing).is_empty());

    let replace_callout = |drawing: &draftwright::output::Drawing, text: &str| {
        let mut d = drawing.clone();
        let ann = d
            .annotations
            .iter()
            .find(|a| a.name.starts_with("d:f:hole:") && a.name.ends_with("/bore.diameter"))
            .unwrap()
            .clone();
        for (idx, &run) in ann.runs.iter().enumerate() {
            d.text[run].text = if idx == 0 { text.into() } else { String::new() };
        }
        d
    };
    assert!(
        !hole_issues(
            &under_a,
            &replace_callout(&under_drawing, "⌀6 THRU ⌴ ⌀10 ↧ 2 C0.25 FAR SIDE")
        )
        .is_empty()
    );
    assert!(
        !hole_issues(
            &far_a,
            &replace_callout(&far_drawing, "⌀6 THRU ⌴ ⌀10 ↧ 2 FAR SIDE C0.25")
        )
        .is_empty()
    );
}

/// `FAR SIDE` placement in mm and inch units precedes the recess's own mouth chamfer.
#[test]
fn far_side_placement_in_inch_units_precedes_mouth_chamfer() {
    let a = analysis();
    for (units, expected_end) in [
        (draftwright::units::Units::Mm, "FAR SIDE C0.25"),
        (draftwright::units::Units::In, "FAR SIDE C.010"),
    ] {
        let spec = Spec {
            units,
            ..Spec::default()
        };
        let plan = model::plan_recovered(&a, &spec).unwrap();
        let mut opposite_views = plan.views.clone();
        opposite_views
            .iter_mut()
            .find(|v| v.id == "v:plan")
            .unwrap()
            .direction = [0.0, 0.0, -1.0];
        let (dims, _, _) =
            draftwright::dims::plan(&a, &spec, &plan.sheet, &opposite_views).unwrap();
        let d = dims
            .iter()
            .find(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
            .unwrap();
        let words = words_of(d);
        assert!(
            words.ends_with(expected_end),
            "units={units:?}: expected suffix {expected_end:?}, got {words:?}"
        );
    }
}

/// Missing `csink.axis` leaves the seat direction unrecorded: both groups stay plain and each
/// callout raises one `hole_recess_side_undecided` warning.
#[test]
fn missing_countersink_axis_stays_plain_with_warning() {
    let (_, mut a) = far_countersink();
    for f in a.features.iter_mut().filter(|f| f.kind == "hole") {
        if let Some(csink) = f
            .parameters
            .get_mut("csink")
            .and_then(|v| v.as_object_mut())
        {
            csink.remove("axis");
        }
    }
    let plan = plan_of(&a);
    let mut callouts: Vec<String> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .map(words_of)
        .collect();
    callouts.sort();
    assert_eq!(
        callouts,
        vec!["2× ⌀5 THRU ⌵ ⌀10 × 90°", "2× ⌀6 THRU ⌵ ⌀12 × 90°"]
    );
    let warnings: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "hole_recess_side_undecided")
        .collect();
    assert_eq!(warnings.len(), 2, "{warnings:?}");
    for p in warnings {
        assert_eq!(p.severity, "warning");
        assert!(p.message.contains("hole"), "{}", p.message);
        assert!(p.message.contains("countersink"), "{}", p.message);
        assert!(p.message.contains("without FAR SIDE"), "{}", p.message);
    }
}

/// A tilted recess axis (neither parallel nor perpendicular to the view) stays plain and raises
/// one `hole_recess_side_undecided` warning per callout; CTC-04's tilted counterbores stay plain
/// with that warning on two A2 sheets.
#[test]
fn tilted_recess_stays_plain_with_warning() {
    let cbore_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mouth_cbore_under.step");
    let (_, mut inclined) = model::analyse_native(&cbore_path).unwrap();
    hole_mut(&mut inclined).remove("mouth_chamfers");
    hole_mut(&mut inclined)["cbore"]["depth"] = json!(2.0);
    rotate_x(&mut inclined, 22.0);
    let plan = plan_of(&inclined);
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .unwrap();
    assert_eq!(d.view, "v:plan");
    assert_eq!(words_of(d), "⌀6 THRU ⌴ ⌀10 ↧ 2");
    let warnings: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "hole_recess_side_undecided")
        .collect();
    assert_eq!(warnings.len(), 1, "{warnings:?}");
    assert_eq!(warnings[0].severity, "warning");
    assert!(warnings[0].message.contains("counterbore"));
    assert!(warnings[0].message.contains("without FAR SIDE"));

    // Symmetric counterbore rotated 135° about X (bore is along Y): tilted -> plain + warning.
    let lm10_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/bearing-lm10uu.step.gz");
    let (_, lm10) = model::analyse_native(&lm10_path).unwrap();
    let mut tilted = lm10.clone();
    rotate_x(&mut tilted, 135.0);
    let tilted_plan = plan_of(&tilted);
    let rotated: Vec<String> = tilted_plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .map(words_of)
        .collect();
    assert!(!rotated.is_empty() && rotated.iter().all(|w| !w.contains("FAR SIDE")));
    assert!(
        tilted_plan
            .problems
            .iter()
            .any(|p| p.code == "hole_recess_side_undecided" && p.severity == "warning")
    );

    if let Some(corpus) = corpus::dir() {
        let (_, ctc04) =
            model::analyse_native(&corpus.join("nist/nist_ctc_04_asme1_rd.stp")).unwrap();
        let plan = plan_of(&ctc04);
        let cbore = plan
            .dimensions
            .iter()
            .find(|d| d.kind == "callout" && d.label.contains("⌴") && d.label.contains("20"))
            .expect("CTC-04 ⌀20 counterbore callout");
        assert_eq!(cbore.view, "v:plan");
        assert!(
            !words_of(cbore).contains("FAR SIDE"),
            "CTC-04 tilted counterbores must stay plain: {:?}",
            words_of(cbore)
        );
        assert_eq!(plan.sheet.size, "A2");
        assert_eq!(plan.sheets.len(), 1, "CTC-04 must stay on two A2 sheets");
        assert!(
            plan.problems
                .iter()
                .any(|p| p.code == "hole_recess_side_undecided" && p.severity == "warning")
        );
    }
}

/// A through hole countersunk at both mouths (`countersink-two-face.step`, from its own
/// `csink.faces`, not cross-feature matching) stays plain and raises `hole_recess_side_undecided`.
#[test]
fn both_end_countersink_stays_plain_with_warning() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/coverage-holes/countersink-two-face.step");
    let (_, a) = model::analyse_native(&path).unwrap();
    let plan = plan_of(&a);
    assert_eq!(callout(&plan).1, "⌀6 THRU ⌵ ⌀14 × 90°");
    let warnings: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "hole_recess_side_undecided")
        .collect();
    assert_eq!(warnings.len(), 1, "{warnings:?}");
    assert_eq!(warnings[0].severity, "warning");
}

/// Rotating a counterbored hole with chamfers 180° about X flips only which end faces the viewer:
/// the recess qualifier comes from the recess's own direction only, chamfers stay classified
/// against the recess, and no false warning fires.
#[test]
fn flipped_recess_with_chamfers_keeps_chamfers_relative_to_recess() {
    for (name, expected) in [
        (
            "mouth_cbore_far_only.step",
            "⌀6 THRU ⌴ ⌀10 ↧ 2 FAR SIDE C0.25 FAR SIDE",
        ),
        (
            "mouth_cbore_far.step",
            "⌀6 THRU ⌴ ⌀10 ↧ 2 FAR SIDE C0.25 C0.25 FAR SIDE",
        ),
        ("mouth_cbore_under.step", "⌀6 THRU ⌴ ⌀10 ↧ 2 C0.25"),
    ] {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts")
            .join(name);
        let (_, mut a) = model::analyse_native(&path).unwrap();
        rotate_x(&mut a, 180.0);
        let plan = plan_of(&a);
        assert_eq!(callout(&plan).1, expected, "{name}");
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "hole_recess_side_undecided"),
            "{name}: unexpected warning {:?}",
            plan.problems
        );
    }
    // Hole table strips the recess's `FAR SIDE` while keeping the recess's own chamfer.
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mouth_cbore_under.step");
    let (_, a) = model::analyse_native(&path).unwrap();
    let table_plan = model::plan_recovered(
        &a,
        &Spec {
            hole_table: draftwright::hole_table::Setting::Always,
            ..Spec::default()
        },
    )
    .unwrap();
    let rows: Vec<_> = table_plan
        .hole_tables
        .iter()
        .flat_map(|t| &t.rows)
        .collect();
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].cells[1], "⌀6 THRU ⌴ ⌀10 ↧ 2 C0.25");
}

/// Two ⌀6 bores on one plate — one countersunk underneath only and one countersunk at both mouths —
/// must never be grouped into `2× … FAR SIDE`: the single-ended hole says `FAR SIDE` without a
/// warning, and the both-ends hole stays plain with `hole_recess_side_undecided`.
#[test]
fn mixed_single_and_both_end_countersinks_are_not_grouped() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mixed_csink_plate.step");
    let (_, a) = model::analyse_native(&path).unwrap();
    let plan = plan_of(&a);
    let mut callouts: Vec<String> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .map(words_of)
        .collect();
    callouts.sort();
    assert_eq!(
        callouts,
        vec!["⌀6 THRU ⌵ ⌀12 × 90°", "⌀6 THRU ⌵ ⌀12 × 90° FAR SIDE"]
    );
    let warnings: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "hole_recess_side_undecided")
        .collect();
    assert_eq!(warnings.len(), 1, "{warnings:?}");
    assert_eq!(warnings[0].severity, "warning");
}

/// A counterbore recorded at both ends of a hole feature (`cbore.faces` spanning both mouths) that
/// is not established as `BOTH SIDES` stays plain and raises `hole_recess_side_undecided`.
#[test]
fn unestablished_both_end_cbore_stays_plain_with_warning() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/bearing-lm10uu.step.gz");
    let (_, mut a) = model::analyse_native(&path).unwrap();
    // Without `edge_ink`, `both_mouths` cannot establish `BOTH SIDES`, while `cbore.faces` still
    // records cylinders at both ends of the bore.
    a.edge_ink = None;
    let plan = plan_of(&a);
    let words = callout(&plan).1;
    assert_eq!(words, "⌀10 THRU ⌴ ⌀16.4 ↧ 1.4");
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "hole_recess_side_undecided" && p.severity == "warning"),
        "{:?}",
        plan.problems
    );
}

/// On a clevis (two arms with a gap), a single-ended countersunk hole on one arm keeps `FAR SIDE`
/// without a warning even when a coaxial hole on the other arm carries an opposing seat across
/// the gap.
#[test]
fn clevis_coaxial_seat_across_gap_does_not_warn_on_single_ended_hole() {
    let opposing = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/clevis_opposing_seat.step");
    let (_, a) = model::analyse_native(&opposing).unwrap();
    let plan = plan_of(&a);
    let d6 = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.label.contains("⌀6"))
        .expect("⌀6 clevis hole callout");
    assert_eq!(words_of(d6), "⌀6 THRU ⌵ ⌀12 × 90° FAR SIDE");
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "hole_recess_side_undecided"),
        "coaxial seat across gap must not warn: {:?}",
        plan.problems
    );

    let one_seat =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/clevis_one_seat.step");
    let (_, a) = model::analyse_native(&one_seat).unwrap();
    let plan = plan_of(&a);
    let mut callouts: Vec<String> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .map(words_of)
        .collect();
    callouts.sort();
    assert_eq!(callouts, vec!["⌀6 THRU", "⌀6 THRU ⌵ ⌀12 × 90° FAR SIDE"]);
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "hole_recess_side_undecided")
    );
}

/// Different opposite counterbores (`diff_end_recesses.step` and NIST CTC-02) still say `FAR SIDE`.
#[test]
fn different_opposite_recess_does_not_hide_far_side() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/diff_end_recesses.step");
    let (part, a) = model::analyse_native(&path).unwrap();
    let plan = plan_of(&a);
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .expect("hole callout");
    assert_eq!(d.view, "v:plan");
    assert_eq!(words_of(d), "⌀6 THRU ⌴ ⌀14 ↧ 4 FAR SIDE");

    let mut opposite_views = plan.views.clone();
    opposite_views
        .iter_mut()
        .find(|v| v.id == "v:plan")
        .unwrap()
        .direction = [0.0, 0.0, -1.0];
    let (dims, _, _) =
        draftwright::dims::plan(&a, &Spec::default(), &plan.sheet, &opposite_views).unwrap();
    let opp = dims
        .iter()
        .find(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .unwrap();
    assert_eq!(words_of(opp), "⌀6 THRU ⌴ ⌀14 ↧ 4");

    let drawing = model::render(&part, &a, &plan, &bundled_font()).unwrap();
    let hole_issues: Vec<_> = draftwright::lint::lint(&a, &drawing)
        .into_iter()
        .filter(|i| i.code.contains("hole") || i.message.contains("hole:"))
        .map(|i| i.message)
        .collect();
    assert!(hole_issues.is_empty(), "{hole_issues:?}");

    if let Some(corpus) = corpus::dir() {
        let (_, ctc02) =
            model::analyse_native(&corpus.join("nist/nist_ctc_02_asme1_rc.stp")).unwrap();
        let plan = plan_of(&ctc02);
        let callout_for = |feature: &str| {
            let d = plan
                .dimensions
                .iter()
                .find(|d| {
                    d.kind == "callout"
                        && d.measurements
                            .iter()
                            .any(|m| m.feature == feature && m.parameter == "bore.diameter")
                })
                .unwrap_or_else(|| panic!("missing callout for {feature}"));
            words_of(d)
        };
        assert_eq!(
            callout_for("f:hole:f150"),
            "⌀52 THRU ⌴ ⌀115 ↧ 23.49 FAR SIDE"
        );
        assert_eq!(callout_for("f:hole:f153"), "⌀52 THRU ⌴ ⌀115 ↧ 23.49");
        assert_eq!(callout_for("f:hole:f337"), "⌀40 THRU ⌴ ⌀120 ↧ 8 FAR SIDE");
        assert_eq!(callout_for("f:hole:f130"), "⌀40 THRU ⌴ ⌀120 ↧ 8");
    }
}

/// The entry cell of every scheduled hole of *a*, by feature, and lint's verdict on the rendered
/// schedule as drawn and with *wrong* written into *feature*'s entry cell.
fn entry_cells_and_lint(
    part: &haecceity::Part,
    a: &Analysis,
    feature: &str,
    wrong: &str,
) -> (Vec<(String, String)>, bool, bool) {
    let mut spec = Spec {
        hole_table: draftwright::hole_table::Setting::Always,
        ..Spec::default()
    };
    spec.sheet.size = Some("A3".into());
    let plan = model::plan_recovered(a, &spec).unwrap();
    let cells = plan
        .hole_tables
        .iter()
        .flat_map(|t| &t.rows)
        .map(|r| (r.feature.clone(), r.cells[4].clone()))
        .collect();
    let invalid = |d: &draftwright::output::Drawing| {
        draftwright::lint::lint(a, d).iter().any(|i| {
            i.code == "hole_table_entry_invalid" && i.subjects.iter().any(|s| s == feature)
        })
    };
    let mut drawing = model::render(part, a, &plan, &bundled_font()).unwrap();
    let accepted = !invalid(&drawing);
    let entry = drawing
        .annotations
        .iter()
        .find(|m| m.name.ends_with("/entry") && m.claims[0].feature == feature)
        .unwrap()
        .runs
        .clone();
    for run in entry {
        drawing.text[run].text = wrong.into();
    }
    (cells, accepted, invalid(&drawing))
}

/// A countersink cut from the end opposite quiddity's recorded opening is entered from its own
/// face: the schedule says where that face is and which way it is drilled, and lint agrees and
/// rejects the bore's recorded opening. Seats at the recorded opening keep their cell.
#[test]
fn a_far_countersink_row_names_the_seat_face() {
    let (part, a) = far_countersink();
    let (cells, accepted, wrong_rejected) =
        entry_cells_and_lint(&part, &a, "f:hole:f17", "10 / −Z");
    let cell = |f: &str| cells.iter().find(|c| c.0 == f).unwrap().1.clone();
    assert_eq!(cell("f:hole:f17"), "0 / +Z");
    assert_eq!(cell("f:hole:f18"), "0 / +Z");
    assert_eq!(cell("f:hole:f12"), "30 / −Z");
    assert!(accepted);
    assert!(wrong_rejected);
}

/// A counterbore cut from below: quiddity records its opening at the recess, so the schedule
/// already names the recess face; it is unchanged, and lint still rejects the far end.
#[test]
fn a_far_counterbore_row_keeps_the_recess_face() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mouth_cbore_under.step");
    let (part, a) = model::analyse_native(&path).unwrap();
    let (cells, accepted, wrong_rejected) = entry_cells_and_lint(&part, &a, "f:hole:f6", "5 / −Z");
    assert_eq!(cells[0].1, "0 / +Z");
    assert!(accepted);
    assert!(wrong_rejected);
}
