//! View planning from the part's features: a turned part's reduced view set, and section A–A
//! planned, placed and drawn (its cut face hatched, its cutting plane marked on the plan).

#[path = "common/corpus.rs"]
mod corpus;

use std::path::Path;

use draftwright::model::{self, Spec};
use draftwright::output::{Curve, Item};
use draftwright::text::Font;

fn fixture(name: &str) -> (draftwright::haecceity::Part, model::Analysis) {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
    model::analyse_with(
        &dir.join("parts").join(format!("{name}.step")),
        Some(&dir.join("recognition").join(format!("{name}.json"))),
    )
    .unwrap()
}

fn ids(plan: &model::Plan) -> Vec<&str> {
    plan.views.iter().map(|v| v.id.as_str()).collect()
}

/// ISSUE-1262-a, ADR2-13: a plain part turned about z is drawn as its profile (front) and end
/// (plan) views and the isometric; the side view, which repeats the front, is left out and the
/// basis says why. A spec that names the side view keeps it.
#[test]
fn a_turned_part_leaves_out_the_view_that_repeats_its_profile() {
    let (_, analysis) = fixture("turned");
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    assert_eq!(ids(&plan), ["v:front", "v:plan", "v:iso"]);
    for v in &plan.views[..2] {
        assert!(
            v.basis.contains("turned about z") && v.basis.contains("side view"),
            "{}",
            v.basis
        );
    }
    let spec: Spec =
        serde_json::from_value(serde_json::json!({ "views": { "side": {} } })).unwrap();
    let plan = model::plan(&analysis, &spec).unwrap();
    assert_eq!(ids(&plan), ["v:front", "v:plan", "v:side", "v:iso"]);
    // Without its features, the part is not known to be turned.
    let (_, bare) = model::analyse(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/turned.step"),
    )
    .unwrap();
    let plan = model::plan(&bare, &Spec::default()).unwrap();
    assert_eq!(ids(&plan).len(), 4);
}

/// A prismatic part is not reduced.
#[test]
fn a_prismatic_part_keeps_its_three_views() {
    for name in ["prismatic", "plate"] {
        let (_, analysis) = fixture(name);
        let plan = model::plan(&analysis, &Spec::default()).unwrap();
        assert_eq!(
            ids(&plan),
            ["v:front", "v:plan", "v:side", "v:iso"],
            "{name}"
        );
    }
}

/// #222: no dimension of the angled blind step is routed to its plan view, a strip of the part's
/// outline: the plan is left out, every dimension stays in the view it had, the basis says why,
/// and a spec that names the plan keeps it.
#[test]
fn a_view_no_dimension_needs_is_left_out() {
    let step = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/issue_1247_angled_blind_step.step");
    let (_, analysis) = model::analyse_native(&step).unwrap();
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    assert_eq!(ids(&plan), ["v:front", "v:side", "v:iso"]);
    for v in &plan.views[..2] {
        assert!(
            v.basis.contains("no dimension needs the plan view")
                && v.basis.contains("so the plan view is left out"),
            "{}",
            v.basis
        );
    }
    let spec: Spec =
        serde_json::from_value(serde_json::json!({ "views": { "plan": {} } })).unwrap();
    let kept = model::plan(&analysis, &spec).unwrap();
    assert_eq!(ids(&kept), ["v:front", "v:plan", "v:side", "v:iso"]);
    let carriers = |plan: &model::Plan| {
        let mut c: Vec<(String, String)> = plan
            .dimensions
            .iter()
            .map(|d| (d.id.clone(), d.view.clone()))
            .collect();
        c.sort();
        c
    };
    assert_eq!(carriers(&plan), carriers(&kept));
    assert!(kept.dimensions.iter().all(|d| d.view != "v:plan"));
}

#[test]
fn an_unknown_view_names_the_section_among_the_choices() {
    let (_, analysis) = fixture("plate");
    let spec: Spec =
        serde_json::from_value(serde_json::json!({ "views": { "rear": {} } })).unwrap();
    let e = model::plan(&analysis, &spec).unwrap_err();
    assert!(e.choices.contains(&"section_aa".to_string()), "{e}");
}

/// nist_ftc_06: its counterbored z-axis holes warrant section A–A (draftwright places it too,
/// tests/fixtures/sheets/ftc06), cut through the row of four at y = 50.8, right of the side view
/// in the front's row; the cut face is hatched at 45° inside the section's area, the plan
/// carries the cutting-plane line with its letters, and the view is captioned. A spec can omit
/// it, and then no room is reserved for it.
#[test]
fn section_a_a_is_planned_placed_and_drawn() {
    let Some(dir) = corpus::dir() else {
        eprintln!(
            "SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS \
             (DRAFTWRIGHT_CORPUS_REQUIRED=1 makes this a failure)"
        );
        return;
    };
    let (part, analysis) = corpus::analyse(&dir, "nist/nist_ftc_06_asme1_rd.stp", "ftc06");
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    assert_eq!(
        ids(&plan),
        ["v:front", "v:plan", "v:side", "v:iso", "v:section_aa"]
    );
    let view = |id: &str| plan.views.iter().find(|v| v.id == id).unwrap();
    let (front, side, section) = (view("v:front"), view("v:side"), view("v:section_aa"));
    let cut = section.cut.as_ref().unwrap();
    assert!((cut.origin[1] - 50.8).abs() < 1e-9, "{cut:?}");
    assert_eq!(cut.normal, [0.0, 1.0, 0.0]);
    assert_eq!(
        section.at[1], front.at[1],
        "the section shares the front's row"
    );
    // draftwright starts the section's box 10 mm past the side view's band; that gap is all the
    // band the section has on its left, so its area abuts the side's (a 20 mm band there lay
    // 10 mm over the side's).
    let gap = section.area[0] - side.area[2];
    assert!(
        (0.0..1e-9).contains(&gap),
        "right of the side view's area: {gap}"
    );
    for v in plan
        .views
        .iter()
        .filter(|v| v.id != "v:side" && v.id != "v:section_aa")
    {
        let (a, b) = (v.area, section.area);
        assert!(
            a[2] <= b[0] || b[2] <= a[0] || a[3] <= b[1] || b[3] <= a[1],
            "{}",
            v.id
        );
    }

    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
    let a = section.area;
    let hatch = drawing.layers[2]
        .items
        .iter()
        .filter(|i| match i {
            Item::Edge(Curve::Line(p, q)) => {
                ((q[1] - p[1]) - (q[0] - p[0])).abs() < 1e-9
                    && [p, q]
                        .iter()
                        .all(|r| a[0] <= r[0] && r[0] <= a[2] && a[1] <= r[1] && r[1] <= a[3])
            }
            _ => false,
        })
        .count();
    assert!(hatch > 20, "{hatch} hatch lines");
    // Zone letters in the margin are not section letters.
    let [w, h] = plan.sheet.page;
    let texts: Vec<&str> = drawing
        .text
        .iter()
        .filter(|t| t.at[0] > 10. && t.at[1] > 10. && t.at[0] < w - 10. && t.at[1] < h - 10.)
        .map(|t| t.text.as_str())
        .collect();
    assert!(texts.contains(&"SECTION A\u{2013}A"), "{texts:?}");
    assert_eq!(texts.iter().filter(|t| **t == "A").count(), 2);

    let spec: Spec =
        serde_json::from_value(serde_json::json!({ "views": { "section_aa": { "omit": true } } }))
            .unwrap();
    let omitted = model::plan(&analysis, &spec).unwrap();
    assert_eq!(ids(&omitted), ["v:front", "v:plan", "v:side", "v:iso"]);
}

/// `views/sections`: several requested cuts, drawn at the paper and scale the drawing has without
/// them, on the next larger paper when they need it, and left out (last first) with a finding when
/// they fit nowhere the pair allows.
fn native(name: &str) -> (draftwright::haecceity::Part, model::Analysis) {
    model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts")
            .join(format!("{name}.step")),
    )
    .unwrap()
}

fn requested_plan(
    analysis: &model::Analysis,
    sheet: serde_json::Value,
    through: &[&str],
) -> model::Plan {
    let sections: Vec<serde_json::Value> = through
        .iter()
        .zip("ABCDEFGH".chars())
        .map(|(f, id)| serde_json::json!({"id": id.to_string(), "through": f}))
        .collect();
    let spec: Spec = serde_json::from_value(
        serde_json::json!({"sheet": sheet, "views": {"sections": sections}}),
    )
    .unwrap();
    model::plan_recovered(analysis, &spec).unwrap()
}

fn cuts(plan: &model::Plan) -> Vec<&model::PlannedView> {
    plan.views.iter().filter(|v| v.cut.is_some()).collect()
}

fn not_shown(plan: &model::Plan) -> Vec<&str> {
    plan.problems
        .iter()
        .filter(|p| p.code == "section_not_shown")
        .map(|p| p.message.as_str())
        .collect()
}

const SCATTERED: [&str; 4] = ["f:hole:f6", "f:hole:f9", "f:hole:f10", "f:hole:f8"];

#[test]
fn two_three_and_four_requests_are_each_drawn_at_the_scale_without_them() {
    let (part, analysis) = native("scattered_holes");
    let bare = model::plan_recovered(&analysis, &Spec::default()).unwrap();
    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    for n in 2..=4 {
        let p = requested_plan(&analysis, serde_json::json!({}), &SCATTERED[..n]);
        assert_eq!(
            (&p.sheet.size, p.sheet.scale_factor),
            (&bare.sheet.size, bare.sheet.scale_factor),
            "{n} requests keep the drawing's paper and scale"
        );
        let drawn = cuts(&p);
        assert_eq!(drawn.len(), n);
        for (v, letter) in drawn.iter().zip("ABCD".chars()) {
            let cut = v.cut.as_ref().unwrap();
            assert_eq!(cut.label, letter.to_string());
            assert_eq!(cut.marked_in, "v:plan");
            assert!(v.from_spec.iter().any(|f| f.starts_with("views/sections/")));
            assert!(
                p.views
                    .iter()
                    .filter(|w| w.id != v.id)
                    .all(|w| !(w.area[0] < v.area[2]
                        && v.area[0] < w.area[2]
                        && w.area[1] < v.area[3]
                        && v.area[1] < w.area[3])),
                "{} overlaps another view",
                v.id
            );
        }
        // The plan carries one cutting-plane line per cut.
        let plan_view = p.views.iter().find(|v| v.id == "v:plan").unwrap();
        let lines = plan_view
            .line_work
            .iter()
            .filter(|w| w.kind == "cutting_plane")
            .count();
        assert_eq!(lines, n);
        assert!(not_shown(&p).is_empty());
        // Each cut is captioned and both ends of its plane are lettered on the plan.
        let drawing = model::render(&part, &analysis, &p, &font).unwrap();
        let [w, h] = p.sheet.page;
        let texts: Vec<&str> = drawing
            .text
            .iter()
            .filter(|t| t.at[0] > 10. && t.at[1] > 10. && t.at[0] < w - 10. && t.at[1] < h - 10.)
            .map(|t| t.text.as_str())
            .collect();
        for letter in "ABCD".chars().take(n) {
            let caption = format!("SECTION {letter}\u{2013}{letter}");
            assert!(texts.contains(&caption.as_str()), "{caption}: {texts:?}");
            assert_eq!(
                texts.iter().filter(|t| **t == letter.to_string()).count(),
                2,
                "{letter}"
            );
        }
    }
}

#[test]
fn cuts_that_need_it_take_the_next_paper_at_the_same_scale() {
    let (_, analysis) = native("plate");
    let bare = model::plan_recovered(&analysis, &Spec::default()).unwrap();
    let p = requested_plan(
        &analysis,
        serde_json::json!({}),
        &["f:hole:f6", "f:hole:f8"],
    );
    assert_eq!(bare.sheet.size, "A4");
    assert_eq!(p.sheet.size, "A3");
    assert_eq!(p.sheet.scale_factor, bare.sheet.scale_factor);
    assert_eq!(cuts(&p).len(), 2);
    assert!(not_shown(&p).is_empty());
}

#[test]
fn a_cut_that_fits_no_paper_is_left_out_last_first_with_a_finding() {
    let (_, analysis) = native("plate");
    // At 10:1 the largest paper holds two of the three cuts.
    let p = requested_plan(
        &analysis,
        serde_json::json!({"scale": "10:1"}),
        &["f:hole:f6", "f:hole:f8", "f:hole:f7"],
    );
    assert_eq!(p.sheet.size, "A0");
    let labels: Vec<_> = cuts(&p)
        .iter()
        .map(|v| v.cut.as_ref().unwrap().label.clone())
        .collect();
    assert_eq!(labels, ["A", "B"]);
    let said = not_shown(&p);
    assert_eq!(said.len(), 1, "{said:?}");
    assert!(
        said[0].contains("section C through f:hole:f7")
            && said[0].contains("does not fit A0 at 10:1, the largest paper"),
        "{}",
        said[0]
    );
    // The Spec keeps every request.
    assert_eq!(p.spec.views.sections.len(), 3);
}

#[test]
fn fixed_paper_stays_and_drops_the_last_cut() {
    let (_, analysis) = native("scattered_holes");
    let p = requested_plan(
        &analysis,
        serde_json::json!({"size": "A3", "scale": "1:2"}),
        &SCATTERED,
    );
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        ("A3", "1:2")
    );
    assert_eq!(cuts(&p).len(), 3);
    let said = not_shown(&p);
    assert_eq!(said.len(), 1, "{said:?}");
    assert!(
        said[0].contains("section D through f:hole:f8")
            && said[0].contains("the paper the spec fixes"),
        "{}",
        said[0]
    );
}
