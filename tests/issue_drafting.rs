//! The drafting rules of the issue regressions (`docs/rules/issues.yaml`, class `drafting`) that
//! the port can exercise: what the sheet must state, show or avoid. Each test names the rules it
//! checks; a part (and its recognition) and a spec go in, a property of the plan, its problems,
//! lint's findings on the drawing or the output comes out. A rule the port breaks is checked by an
//! ignored test that says why (`rust-wrong`), and stays pending in the catalogue.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;
#[path = "common/sheet_rule.rs"]
mod sheet_rule;

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use draftwright::bridge::holes;
use draftwright::dims::{PlannedDimension, fmt};
use draftwright::lint::{self, Mark};
use draftwright::model::{self, Analysis, DimensionSource, DimensionSpec, Plan, PlannedView, Spec};
use draftwright::output::{Claim, Drawing};
use draftwright::text::Font;
use draftwright::title_block;
use serde_json::json;

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

fn font() -> Font {
    Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
        .unwrap()
}

/// The fixture part *name* with its recognition.
fn analysed(name: &str) -> (draftwright::haecceity::Part, Analysis) {
    let (part, mut a) = model::analyse(&fixture(&format!("parts/{name}.step"))).unwrap();
    let json = std::fs::read_to_string(fixture(&format!("recognition/{name}.json"))).unwrap();
    model::recognise(&part, &mut a, &json).unwrap();
    (part, a)
}

const FIXTURES: [&str; 5] = ["plate", "turned", "prismatic", "two_grids", "boss_circle"];

/// The fixtures, read once for every test here.
fn fixtures() -> &'static [(String, Analysis)] {
    static FIXED: OnceLock<Vec<(String, Analysis)>> = OnceLock::new();
    FIXED.get_or_init(|| {
        FIXTURES
            .iter()
            .map(|n| (n.to_string(), analysed(n).1))
            .collect()
    })
}

/// The sampled sheets' corpus parts (`tests/fixtures/recognition/sheets/`), each with the
/// features draftwright's own recogniser found for it, checked to be the file the sheet was
/// captured from; read once for every test here, and none when the corpus is missing. All 13,
/// ctc04 among them since the bridge reads it (d-bridge-area-tolerance).
fn sampled() -> &'static [(String, Analysis)] {
    static SAMPLED: OnceLock<Vec<(String, Analysis)>> = OnceLock::new();
    SAMPLED.get_or_init(|| {
        let Some(dir) = corpus::dir() else {
            eprintln!("SKIPPED (corpus part): the corpus is not found; set DRAFTWRIGHT_CORPUS");
            return Vec::new();
        };
        let mut names: Vec<String> = std::fs::read_dir(fixture("recognition/sheets"))
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .filter_map(|f| f.strip_suffix(".json.gz").map(String::from))
            .collect();
        names.sort();
        names
            .into_iter()
            .map(|name| {
                let f = facts::load(&name);
                let (part, mut a) = model::analyse(&dir.join(&f.binding.file)).unwrap();
                assert_eq!(a.binding.source_sha256, f.binding.sha256, "{name}");
                let mut json = String::new();
                flate2::read::GzDecoder::new(
                    std::fs::File::open(fixture(&format!("recognition/sheets/{name}.json.gz")))
                        .unwrap(),
                )
                .read_to_string(&mut json)
                .unwrap();
                model::recognise(&part, &mut a, &json).unwrap_or_else(|e| panic!("{name}: {e}"));
                (name, a)
            })
            .collect()
    })
}

/// Every part here, with its default plan.
fn planned() -> &'static [(String, Analysis, Plan)] {
    static PLANNED: OnceLock<Vec<(String, Analysis, Plan)>> = OnceLock::new();
    PLANNED.get_or_init(|| {
        fixtures()
            .iter()
            .chain(sampled())
            .map(|(n, a)| {
                (
                    n.clone(),
                    a.clone(),
                    model::plan(a, &Spec::default()).unwrap(),
                )
            })
            .collect()
    })
}

fn spec(value: serde_json::Value) -> Spec {
    serde_json::from_value(value).unwrap()
}

fn omitting(omit: &[&str]) -> Spec {
    let mut spec = Spec::default();
    for id in omit {
        spec.dimensions.insert(
            id.to_string(),
            DimensionSpec {
                omit: true,
                ..Default::default()
            },
        );
    }
    spec
}

fn drawn(name: &str, plan: &Plan) -> (Analysis, Drawing) {
    let (part, analysis) = analysed(name);
    let drawing = model::render(&part, &analysis, plan, &font()).unwrap();
    (analysis, drawing)
}

fn codes(plan: &Plan) -> BTreeSet<&str> {
    plan.problems.iter().map(|p| p.code.as_str()).collect()
}

fn hole_callout(d: &PlannedDimension) -> bool {
    d.kind == "callout"
        && d.measurements
            .iter()
            .any(|m| m.parameter == "bore.diameter")
}

/// Where world point *p* lands on the page in view *v* (the centre of the part's box lands on
/// `at`; the page's x is `up × direction`).
fn page_of(v: &PlannedView, analysis: &Analysis, p: [f64; 3]) -> [f64; 2] {
    let [lo, hi] = v.detail.as_ref().map_or(analysis.envelope, |d| d.bounds);
    let (t, u) = (v.direction, v.up);
    let right = [
        u[1] * t[2] - u[2] * t[1],
        u[2] * t[0] - u[0] * t[2],
        u[0] * t[1] - u[1] * t[0],
    ];
    let q = [0, 1, 2].map(|k| p[k] - 0.5 * (lo[k] + hi[k]));
    let dot = |a: [f64; 3]| a[0] * q[0] + a[1] * q[1] + a[2] * q[2];
    [v.at[0] + v.scale * dot(right), v.at[1] + v.scale * dot(u)]
}

fn inside(b: [f64; 4], outer: [f64; 4]) -> bool {
    b[0] >= outer[0] - 1e-9
        && b[1] >= outer[1] - 1e-9
        && b[2] <= outer[2] + 1e-9
        && b[3] <= outer[3] + 1e-9
}

fn overlap(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] - 1e-9 && a[2] > b[0] + 1e-9 && a[1] < b[3] - 1e-9 && a[3] > b[1] + 1e-9
}

/// The numbers a label prints as measurements: each term's number, less a count (`4×`), a
/// grid's shape (`(2×2)`) and a face's ordinal (`FACE 1 OF 3`).
fn printed(label: &str) -> Vec<String> {
    let terms: Vec<&str> = label.split_whitespace().collect();
    let mut out = Vec::new();
    for (i, t) in terms.iter().enumerate() {
        let before = i.checked_sub(1).map(|j| terms[j]);
        if t.ends_with('×') || t.starts_with('(') || matches!(before, Some("FACE" | "OF")) {
            continue;
        }
        let n = t
            .trim_start_matches(['⌀', 'ø', 'R', 'C'])
            .trim_end_matches('°');
        if !n.is_empty() && n.chars().all(|c| c.is_ascii_digit() || c == '.') {
            out.push(n.to_string());
        }
    }
    out
}

/// ISSUE-1142-a: a hole callout's claims cover everything its compound text prints. Every number
/// it prints (the bore, its depth, the recesses, the countersink, a bolt circle) is the value of
/// a measurement it claims, and every measurement it claims is printed; the drawn annotation
/// carries the plan's claims and prints the complete label in ordered, wrapped text runs.
#[test]
fn a_hole_callout_claims_everything_it_prints() {
    let mut seen = 0;
    for (name, _, plan) in planned() {
        for d in plan.dimensions.iter().filter(|d| hole_callout(d)) {
            seen += 1;
            let numbers: BTreeSet<String> = printed(&d.label).into_iter().collect();
            let claimed: BTreeSet<String> = d.measurements.iter().map(|m| fmt(m.value)).collect();
            assert_eq!(numbers, claimed, "{name}: {} prints {:?}", d.id, d.label);
            assert_eq!(
                d.label,
                draftwright::callouts::label(&d.terms),
                "{name}: {}",
                d.id
            );
        }
    }
    assert!(seen > 10, "{seen} hole callouts");
    for name in ["plate", "boss_circle"] {
        let plan = &planned().iter().find(|p| p.0 == name).unwrap().2;
        let (_, drawing) = drawn(name, plan);
        for d in plan.dimensions.iter().filter(|d| hole_callout(d)) {
            let a = drawing.annotations.iter().find(|a| a.name == d.id).unwrap();
            let claims: Vec<Claim> = d
                .measurements
                .iter()
                .map(|m| Claim {
                    feature: m.feature.clone(),
                    parameter: m.parameter.clone(),
                })
                .collect();
            assert_eq!(a.claims, claims, "{name}: {}", d.id);
            assert_eq!(a.runs.len(), d.label.lines().count(), "{name}: {}", d.id);
            assert_eq!(
                a.runs
                    .iter()
                    .map(|&i| drawing.text[i].text.as_str())
                    .collect::<Vec<_>>()
                    .join("\n"),
                d.label,
                "{name}: {}",
                d.id
            );
        }
    }
}

/// ISSUE-883-a, in part: each measured axis of a location is its own measurement. Every location
/// dimension measures one axis; no measurement (feature and parameter) is stated by two
/// dimensions; a pattern's centre is measured on the pattern, never on a member; and omitting one
/// axis's location leaves the other axis, and the other members, as they were.
#[test]
fn each_axis_of_a_location_is_its_own_measurement() {
    for (name, analysis, plan) in planned() {
        let patterns: BTreeSet<&str> = analysis
            .recognition
            .iter()
            .flat_map(|r| r.patterns.iter().map(|p| p.id.as_str()))
            .collect();
        let mut stated = BTreeMap::new();
        for d in &plan.dimensions {
            for m in &d.measurements {
                let key = (m.feature.as_str(), m.parameter.as_str());
                if let Some(other) = stated.insert(key, d.id.as_str()) {
                    assert_eq!(other, d.id, "{name}: {key:?} is stated twice");
                }
            }
            let axes: BTreeSet<&str> = d
                .measurements
                .iter()
                .filter_map(|m| m.parameter.strip_prefix("location"))
                .collect();
            assert!(axes.len() <= 1, "{name}: {} measures {axes:?}", d.id);
        }
        for d in plan
            .dimensions
            .iter()
            .filter(|d| d.id.starts_with("d:f:hole_pattern") && d.id.contains("/location"))
        {
            assert!(
                d.measurements
                    .iter()
                    .all(|m| patterns.contains(m.feature.as_str())),
                "{name}: {} locates a member",
                d.id
            );
        }
    }

    // boss_circle's four lower holes squared onto their circle in quiddity's records: as made
    // they are a 24.3 × 24.38 rectangle, which since #223 is no bolt circle.
    let (part, mut squared) = model::analyse(&fixture("parts/boss_circle.step")).unwrap();
    let json = std::fs::read_to_string(fixture("recognition/boss_circle.json"))
        .unwrap()
        .replace("-32.103747339", "-32.063747339")
        .replace("-7.7237473394", "-7.7637473394");
    model::recognise(&part, &mut squared, &json).unwrap();
    let analysis = &squared;
    let ids = |plan: &Plan| -> BTreeSet<String> {
        plan.dimensions.iter().map(|d| d.id.clone()).collect()
    };
    let plan = model::plan(analysis, &Spec::default()).unwrap();
    let full = ids(&plan);
    for gone in [
        "d:f:hole:5/location.length.x",
        "d:f:hole_pattern:1/location.length.y",
    ] {
        let left = ids(&model::plan(analysis, &omitting(&[gone])).unwrap());
        let mut expected = full.clone();
        assert!(expected.remove(gone), "{gone}");
        // A coincident location it conveyed comes back in its place (ARCH0009-6): the boss at
        // the bolt circle's centre.
        expected.extend(
            plan.omitted
                .iter()
                .filter(|o| o.basis.starts_with(&format!("conveyed by {gone}: ")))
                .map(|o| o.id.clone()),
        );
        assert_eq!(left, expected, "omitting {gone}");
    }
}

/// ISSUE-1146-c: each principal projection fits inside the physical page, and no two views'
/// reserved areas overlap. On every part the views the plan places lie on the page unless the
/// plan says they do not fit; and a sheet and scale pinned so that they cannot hold the views
/// are reported, never silently overrun.
#[test]
fn every_view_lies_on_the_page_or_the_plan_says_it_does_not() {
    for (name, _, plan) in planned() {
        let [w, h] = plan.sheet.page;
        let unfit = codes(plan).contains("views_do_not_fit");
        for v in &plan.views {
            assert!(
                unfit || inside(v.area, [0.0, 0.0, w, h]),
                "{name}: {} at {:?} on {w}×{h}",
                v.id,
                v.area
            );
        }
        // The principal views, each with its annotation bands, never overlap. (The isometric's
        // area is only the space they leave, which it is fitted into; a section's area is not
        // checked here, see the rule's rust_note.)
        let ortho: Vec<&PlannedView> = plan
            .views
            .iter()
            .filter(|v| ["v:front", "v:plan", "v:side"].contains(&v.id.as_str()))
            .collect();
        for (i, a) in ortho.iter().enumerate() {
            for b in &ortho[i + 1..] {
                assert!(
                    unfit || !overlap(a.area, b.area),
                    "{name}: {} and {} overlap",
                    a.id,
                    b.id
                );
            }
        }
    }
    let plate = &fixtures()[0].1;
    let pinned = model::plan(
        plate,
        &spec(json!({ "sheet": { "size": "A4", "scale": "10:1" } })),
    )
    .unwrap();
    assert_eq!(
        (pinned.sheet.size.as_str(), pinned.sheet.scale.as_str()),
        ("A4", "10:1")
    );
    assert!(
        codes(&pinned).contains("page_fit_uncertain"),
        "{:?}",
        codes(&pinned)
    );
}

/// ISSUE-1146-a, in part: a scale never reduces the sheet silently. A pinned scale is kept as
/// asked and its sheet chosen to hold it; when none does, or the pinned sheet cannot, the plan
/// says so; and the automatic choice states its reason. Not ported: completeness is not weighed
/// in the choice (no outcome the scale loses is counted) and there is no scale policy.
#[test]
fn a_scale_that_does_not_fit_is_reported() {
    for (name, analysis, plan) in planned() {
        assert!(
            plan.sheet.basis.contains(&plan.sheet.scale),
            "{name}: {}",
            plan.sheet.basis
        );
        let fallback = ["scale_fallback_applied", "views_do_not_fit"]
            .iter()
            .any(|c| codes(plan).contains(c));
        let standard = draftwright::layout::SCALES
            .iter()
            .any(|s| (s - plan.sheet.scale_factor).abs() < 1e-12);
        assert!(
            standard || fallback,
            "{name}: {} unreported",
            plan.sheet.scale
        );
        for (size, scale, code) in [
            (None, "10:1", Some("views_do_not_fit")),
            (Some("A4"), "10:1", Some("page_fit_uncertain")),
            (Some("A0"), "1:10000", None),
        ] {
            let mut asked = json!({ "sheet": { "scale": scale } });
            if let Some(s) = size {
                asked["sheet"]["size"] = s.into();
            }
            let pinned = model::plan(analysis, &spec(asked)).unwrap();
            assert_eq!(pinned.sheet.scale, scale, "{name}");
            let fits = !["views_do_not_fit", "page_fit_uncertain"]
                .iter()
                .any(|c| codes(&pinned).contains(c));
            match code {
                // 10:1 fits some small parts; it never fits silently when it does not.
                Some(c) => assert!(fits || codes(&pinned).contains(c), "{name}: {scale}"),
                None => assert!(fits, "{name}: {scale} on {size:?}"),
            }
        }
    }
    let ctc01 = sampled().iter().find(|p| p.0 == "ctc01");
    if let Some((_, analysis)) = ctc01 {
        let big = model::plan(analysis, &spec(json!({ "sheet": { "scale": "10:1" } }))).unwrap();
        assert_eq!(big.sheet.size, "A0");
        assert!(codes(&big).contains("views_do_not_fit"));
    }
}

/// ISSUE-1260-a, in part: what the spec asks of the views is kept, or the plan says why not. A
/// pinned scale is never relaxed (`a_scale_that_does_not_fit_is_reported`); a section is drawn
/// only with the plan view its cutting plane is marked on, and is reported when that view is
/// omitted; and an omitted view is planned out before anything is placed in it, its dimensions
/// carried by the views left or kept unplaced and reported. Not ported:
/// per-view scales and pinned positions (the spec has neither).
#[test]
fn what_the_spec_asks_of_the_views_is_kept_or_reported() {
    for (name, analysis, plan) in planned() {
        for omit in ["front", "plan", "side", "iso"] {
            if !plan.views.iter().any(|v| v.id == format!("v:{omit}")) {
                continue;
            }
            let Ok(less) = model::plan(
                analysis,
                &spec(json!({ "views": { omit: { "omit": true } } })),
            ) else {
                continue;
            };
            let gone = format!("v:{omit}");
            assert!(less.views.iter().all(|v| v.id != gone), "{name}: {omit}");
            // Each dimension it carried moves to a view left, or stays unplaced and reported.
            for d in less.dimensions.iter().filter(|d| d.view == gone) {
                assert!(d.placed.is_none(), "{name}: {} is drawn in {gone}", d.id);
                assert!(
                    less.problems
                        .iter()
                        .any(|p| p.code == "view_plan_incomplete" && p.subjects.contains(&d.id)),
                    "{name}: {} is not reported",
                    d.id
                );
            }
            let section = less.views.iter().any(|v| v.cut.is_some());
            if omit == "plan" && plan.views.iter().any(|v| v.cut.is_some()) {
                assert!(!section, "{name}");
                assert!(codes(&less).contains("section_not_shown"), "{name}");
            }
            for v in less.views.iter().filter_map(|v| v.cut.as_ref()) {
                assert!(less.views.iter().any(|w| w.id == v.marked_in), "{name}");
            }
        }
    }
    let string_post = sampled().iter().find(|p| p.0 == "string_post");
    if let Some((_, analysis)) = string_post {
        let plan = model::plan(analysis, &Spec::default()).unwrap();
        assert!(
            plan.views.iter().any(|v| v.cut.is_some()),
            "string_post has a section"
        );
    }
}

/// ISSUE-1299-a, in part: a sheet the spec names is never escalated. Pinned alone it takes the
/// largest standard scale at which the views fit it, and draftwright's recovery
/// (`model::recover`, which `model::plan` does not apply yet) keeps it, re-planning at most its
/// scale on that sheet; pinned with a scale too large for it, both are kept, before and after
/// the recovery, and the misfit reported (with the nearest pair that fits as its fix), unless the
/// finished drawing fits after all (#13).
#[test]
fn a_sheet_the_spec_names_is_never_escalated() {
    for (name, analysis, _) in planned() {
        for size in ["A4", "A3"] {
            let named = spec(json!({ "sheet": { "size": size } }));
            let pinned = model::plan(analysis, &named).unwrap();
            assert_eq!(pinned.sheet.size, size, "{name}");
            let recovered = model::recover(analysis, &named, pinned).unwrap();
            assert_eq!(recovered.sheet.size, size, "{name}");
            let both = model::plan(
                analysis,
                &spec(json!({ "sheet": { "size": size, "scale": "10:1" } })),
            )
            .unwrap();
            assert_eq!(
                (both.sheet.size.as_str(), both.sheet.scale.as_str()),
                (size, "10:1")
            );
            let both_spec = spec(json!({ "sheet": { "size": size, "scale": "10:1" } }));
            let before = serde_json::to_value(&both).unwrap();
            let fit = |p: &model::Plan| {
                p.problems
                    .iter()
                    .position(|q| q.code == "page_fit_uncertain")
            };
            let reported = fit(&both).map(|i| both.problems[i].message.clone());
            let mut kept = model::recover(analysis, &both_spec, both).unwrap();
            assert_eq!(
                (kept.sheet.size.as_str(), kept.sheet.scale.as_str()),
                (size, "10:1"),
                "{name}"
            );
            // #13: a misfit the finished drawing disproves (after the measured correction every
            // automatic candidate gets) is withdrawn, and says so; a real one is kept, the
            // recovery adding only the nearest pair that fits as its fix.
            if reported.is_some() && fit(&kept).is_none() {
                assert!(
                    kept.sheet.basis.contains("the finished drawing fits"),
                    "{name}"
                );
                continue;
            }
            if let (Some(i), Some(message)) = (fit(&kept), reported) {
                let p = &mut kept.problems[i];
                assert!(
                    p.message.starts_with(message.trim_end_matches('.')),
                    "{name}"
                );
                (p.message, p.fix, p.fixes) = (message, Vec::new(), Vec::new());
            }
            assert_eq!(serde_json::to_value(&kept).unwrap(), before, "{name}");
        }
    }
}

/// ISSUE-1378-a, the planner's part: a hole callout's leader tip touches the rim of a hole it
/// names. On every part, each placed hole callout's arrow tip lies on the projected bore circle
/// of one of the members it claims, in its view (an inclined bore's, an end of the ellipse's
/// major axis). Lint's check of the drawn tip, double-D
/// profiles included, is `tests/leader_rules.rs` (stream K4).
#[test]
fn a_hole_callouts_tip_is_on_the_rim_of_a_hole_it_names() {
    let mut seen = 0;
    for (name, analysis, plan) in planned() {
        let read: BTreeMap<&str, holes::Hole> = holes::read(analysis)
            .into_iter()
            .filter_map(|(f, h)| Some((f.id.as_str(), h.ok()?)))
            .collect();
        for d in plan.dimensions.iter().filter(|d| hole_callout(d)) {
            let (Some(p), Some(v)) = (&d.placed, plan.views.iter().find(|v| v.id == d.view)) else {
                continue;
            };
            seen += 1;
            let tip = p.arrows[0][0];
            let on_rim = d
                .measurements
                .iter()
                .filter(|m| m.parameter == "bore.diameter")
                .filter_map(|m| read.get(m.feature.as_str()))
                .any(|h| {
                    let c = page_of(v, analysis, h.at);
                    if h.principal.is_some() {
                        let r = (tip[0] - c[0]).hypot(tip[1] - c[1]);
                        return (r - 0.5 * v.scale * h.diameter).abs() < 1e-3;
                    }
                    // An inclined bore shows its rim as an ellipse; the tip is an end of its major
                    // axis: the full radius square to the projected axis, at a rim along the bore.
                    let e = page_of(v, analysis, [0, 1, 2].map(|k| h.at[k] + h.direction[k]));
                    let a = [e[0] - c[0], e[1] - c[1]];
                    let la = a[0].hypot(a[1]);
                    let q = [tip[0] - c[0], tip[1] - c[1]];
                    let across = (q[0] * a[1] - q[1] * a[0]).abs() / la;
                    let along = (q[0] * a[0] + q[1] * a[1]) / (la * la);
                    (across - 0.5 * v.scale * h.diameter).abs() < 1e-3
                        && (-1e-6..=h.depth.unwrap_or(0.0) + 1e-6).contains(&along)
                });
            assert!(on_rim, "{name}: {} tip at {tip:?}", d.id);
        }
    }
    assert!(seen > 10, "{seen}");
}

/// The features the plan states *parameter* of.
fn marks(plan: &Plan) -> Vec<Mark> {
    plan.dimensions
        .iter()
        .map(|d| Mark {
            name: d.id.clone(),
            view: Some(d.view.clone()),
            text: d.label.clone(),
            claims: d
                .measurements
                .iter()
                .map(|m| draftwright::output::Claim {
                    feature: m.feature.clone(),
                    parameter: m.parameter.clone(),
                })
                .collect(),
        })
        .collect()
}

fn stated<'a>(plan: &'a Plan, parameter: &str) -> BTreeSet<&'a str> {
    plan.dimensions
        .iter()
        .flat_map(|d| d.measurements.iter())
        .filter(|m| m.parameter == parameter)
        .map(|m| m.feature.as_str())
        .collect()
}

/// ISSUE-1450-a, in part: no face is dimensioned both as a boss's diameter and as a fillet's
/// radius. On every part, the faces of the bosses whose diameter the plan states and of the
/// fillets whose radius it states are disjoint. Not ported: draftwright's absorption decision
/// (the port takes each recognised feature as quiddity gives it, and has no predicate to fail
/// closed); GRM-04, the reported part, has no recognition here.
#[test]
fn no_face_is_both_a_boss_diameter_and_a_fillet_radius() {
    for (name, analysis, plan) in planned() {
        let faces = |ids: BTreeSet<&str>| -> BTreeSet<usize> {
            analysis
                .features
                .iter()
                .filter(|f| ids.contains(f.id.as_str()))
                .flat_map(|f| f.faces.iter().copied())
                .collect()
        };
        let bosses = faces(stated(plan, "boss.diameter"));
        let fillets = faces(stated(plan, "fillet.radius"));
        assert!(bosses.is_disjoint(&fillets), "{name}");
    }
}

/// ISSUE-1505-a, in part: equal diameters on different features are stated and credited
/// separately. No dimension states both a hole's bore and an outside diameter (a boss's, or the
/// part's overall), and lint's turned-step ledger lets the overall diameter stand for a band's
/// only when that band is the unique largest of its profile and alone on its axis line with its
/// diameter. The plan's overall diameter claims the band it is (src/dims/turned.rs), and every
/// other band has its own diameter; with two adjoining bands of the largest diameter it stands
/// for neither, in the plan or in the ledger.
#[test]
fn equal_diameters_on_different_features_are_stated_apart() {
    for (name, _, plan) in planned() {
        for d in &plan.dimensions {
            let ps: BTreeSet<&str> = d
                .measurements
                .iter()
                .map(|m| m.parameter.as_str())
                .collect();
            let outside = ps.contains("boss.diameter") || ps.contains("od.diameter");
            assert!(
                !(outside && ps.contains("bore.diameter")),
                "{name}: {}",
                d.id
            );
        }
    }
    let plan = &planned().iter().find(|p| p.0 == "turned").unwrap().2;
    let (analysis, drawing) = drawn("turned", plan);
    let marks = lint::marks(&drawing);
    let ledger = |a: &Analysis, marks: &[Mark]| -> Vec<String> {
        lint::turned::outcomes(a, marks)
            .into_iter()
            .filter(|o| o.parameter == "step.diameter")
            .map(|o| format!("{} {:?}", o.feature, o.state))
            .collect()
    };
    assert_eq!(
        ledger(&analysis, &marks),
        ["f:turned_step:1 Placed", "f:turned_step:2 Placed"]
    );
    let od = |plan: &Plan| -> Vec<String> {
        let d = plan
            .dimensions
            .iter()
            .find(|d| d.id == "d:envelope/od.diameter")
            .unwrap();
        d.measurements
            .iter()
            .map(|m| format!("{}/{}", m.feature, m.parameter))
            .collect()
    };
    assert_eq!(
        od(plan),
        ["envelope/od.diameter", "f:turned_step:1/step.diameter"]
    );
    // Two bands of the largest diameter: the overall diameter stands for neither.
    let mut twin = analysis.clone();
    let d = twin
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .map(|f| f.parameters["diameter"].as_f64().unwrap())
        .fold(0.0, f64::max);
    let d = serde_json::Value::from(d);
    for f in twin.features.iter_mut().filter(|f| f.kind == "turned_step") {
        f.parameters.insert("diameter".into(), d.clone());
    }
    let twin_plan = model::plan(&twin, &Spec::default()).unwrap();
    assert_eq!(od(&twin_plan), ["envelope/od.diameter"]);
    let od_mark: Vec<Mark> = twin_plan
        .dimensions
        .iter()
        .filter(|d| d.id.starts_with("d:envelope/od.diameter"))
        .map(|d| Mark {
            name: d.id.clone(),
            view: Some(d.view.clone()),
            text: d.label.clone(),
            claims: vec![Claim {
                feature: "envelope".into(),
                parameter: "od.diameter".into(),
            }],
        })
        .collect();
    assert_eq!(
        ledger(&twin, &od_mark),
        ["f:turned_step:1 Missing", "f:turned_step:2 Missing"]
    );
    // The twins' diameters have their own callout instead, one for the two (equal bands along z).
    let d = twin_plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:turned_step:1/step.diameter")
        .unwrap();
    assert_eq!(d.label, "2× ø30");
    let members: Vec<&str> = d.measurements.iter().map(|m| m.feature.as_str()).collect();
    assert_eq!(members, ["f:turned_step:1", "f:turned_step:2"]);
}

/// ISSUE-1518-a, in part: a rear view is never added automatically, and one asked for is
/// refused with the views there are, before anything is planned. Not ported: the explicit rear
/// view itself (the port draws front, plan, side, the isometric and section A–A).
#[test]
fn a_rear_view_is_never_added_and_one_asked_for_is_refused() {
    for (name, analysis, plan) in planned() {
        for v in plan.views.iter().filter(|v| v.detail.is_some()) {
            assert!(
                draftwright::detail::NAMES
                    .iter()
                    .any(|n| v.id == format!("v:{n}"))
            );
        }
        let names: BTreeSet<&str> = plan
            .views
            .iter()
            .filter(|v| v.detail.is_none())
            .map(|v| v.id.as_str())
            .collect();
        assert!(
            names.is_subset(&BTreeSet::from([
                "v:front",
                "v:plan",
                "v:side",
                "v:iso",
                "v:section_aa"
            ])),
            "{name}: {names:?}"
        );
        let e = model::plan(analysis, &spec(json!({ "views": { "rear": {} } }))).unwrap_err();
        assert!(e.message.contains("rear"), "{e}");
        assert!(e.choices.iter().any(|c| c == "front"), "{e}");
    }
}

/// ISSUE-1530-a, in part: a section's ink describes its cut. The cutting plane lies inside the
/// part's extent along its normal and is marked on a view the plan draws; the drawing hatches
/// the cut, captions it `SECTION A–A` and letters both ends of the plane on the marking view.
/// Not ported: an authored cut (the spec cannot place one, so none outside the part can be
/// asked for), and the tangent, gap and coplanar-boundary cases have no fixture here.
#[test]
fn a_sections_ink_describes_its_cut() {
    let mut seen = 0;
    for (name, analysis, plan) in planned() {
        let Some(v) = plan.views.iter().find(|v| v.cut.is_some()) else {
            continue;
        };
        seen += 1;
        let cut = v.cut.as_ref().unwrap();
        let [lo, hi] = analysis.envelope;
        let along = |p: [f64; 3]| (0..3).map(|k| p[k] * cut.normal[k]).sum::<f64>();
        let (a, b) = (along(lo).min(along(hi)), along(lo).max(along(hi)));
        assert!(a < along(cut.origin) && along(cut.origin) < b, "{name}");
        assert!(plan.views.iter().any(|w| w.id == cut.marked_in), "{name}");
        if !FIXTURES.contains(&name.as_str()) {
            let dir = corpus::dir().unwrap();
            let (part, _) = model::analyse(&dir.join(facts::load(name).binding.file)).unwrap();
            let drawing = model::render(&part, analysis, plan, &font()).unwrap();
            // Zone letters in the margin are not section letters.
            let [w, h] = plan.sheet.page;
            let texts: Vec<&str> = drawing
                .text
                .iter()
                .filter(|t| {
                    t.at[0] > 10. && t.at[1] > 10. && t.at[0] < w - 10. && t.at[1] < h - 10.
                })
                .map(|t| t.text.as_str())
                .collect();
            assert!(texts.contains(&"SECTION A\u{2013}A"), "{name}: {texts:?}");
            assert_eq!(texts.iter().filter(|t| **t == "A").count(), 2, "{name}");
        }
    }
    assert!(seen > 0);
}

/// The plan of fixture *name* with sheet title fields *title*.
fn titled(name: &str, title: serde_json::Value) -> (Plan, Drawing) {
    let analysis = &fixtures().iter().find(|p| p.0 == name).unwrap().1;
    let plan = model::plan(analysis, &spec(json!({ "sheet": { "title": title } }))).unwrap();
    let (_, drawing) = drawn(name, &plan);
    (plan, drawing)
}

/// ISSUE-1536-a: a title-block value wider than its cell is reported, and kept whole. A
/// 143-character title on plate is wider than its cell even drawn at the smallest height
/// (#221): the plan carries one
/// `title_field_overflow` warning for the title's cell, with the full value, and the drawing
/// carries the value whole. (Every field and multi-line heights: `tests/title_block.rs`.)
#[test]
fn a_title_block_value_that_overflows_is_reported() {
    let long = "FRAME FOR THE ENGINEERING REVIEW GROUP, PRELIMINARY MACHINING ISSUE, SHEET ONE OF \
                ONE, FOR APPROVAL BY THE MANUFACTURING ENGINEERING DEPARTMENT";
    let (plan, drawing) = titled("plate", json!({ "title": long }));
    let cell = plan.sheet.title_cells.iter().find(|c| c.field == "title");
    let c = cell.unwrap().cell;
    let [x0, _, x1, _] = c;
    let laid = title_block::lay(long, title_block::value_size(long, c));
    let width = laid.max[0] - laid.min[0];
    assert!(
        width > x1 - x0,
        "the fixture's title must overflow its cell"
    );
    let overflow: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "title_field_overflow")
        .collect();
    assert_eq!(overflow.len(), 1, "{:?}", codes(&plan));
    assert_eq!(overflow[0].subjects, ["sheet"]);
    assert_eq!(overflow[0].refs, ["title_block/title"]);
    assert_eq!(overflow[0].severity, "warning");
    assert!(overflow[0].message.contains(long));
    assert_eq!(drawing.text.iter().filter(|t| t.text == long).count(), 1);
}

/// ISSUE-1585-a: a supplied date reaches the title block as its own field: recorded in the
/// plan's date cell (not the revision's), drawn as a searchable run at that cell's centre with
/// its ink inside the cell, and absent when not supplied. (Each of draftwright's clauses:
/// `tests/title_block.rs`; the PDF text layer: `tests/output.rs`.)
#[test]
fn a_supplied_date_reaches_the_title_block() {
    let (plan, drawing) = titled("plate", json!({ "title": "BRACKET", "date": "2026-09-11" }));
    let cell = |plan: &Plan, field: &str| {
        plan.sheet
            .title_cells
            .iter()
            .find(|c| c.field == field)
            .unwrap()
            .clone()
    };
    let date = cell(&plan, "date");
    assert_eq!(date.value.as_deref(), Some("2026-09-11"));
    assert_ne!(date.cell, cell(&plan, "revision").cell);
    let [x0, y0, x1, y1] = date.cell;
    let runs: Vec<_> = drawing
        .text
        .iter()
        .filter(|t| t.text == "2026-09-11")
        .collect();
    assert_eq!(runs.len(), 1, "the date is drawn once");
    assert_eq!(runs[0].at, [0.5 * (x0 + x1), 0.5 * (y0 + y1)]);
    let [w, h] = title_block::measure("2026-09-11");
    assert!(w < x1 - x0 && h < y1 - y0);
    assert!(!codes(&plan).contains("title_field_overflow"));
    let (undated, drawing) = titled("plate", json!({ "title": "BRACKET" }));
    assert_eq!(cell(&undated, "date").value, None);
    assert!(!drawing.text.iter().any(|t| t.text == "2026-09-11"));
}

/// ISSUE-1593-a, in part: nothing is drawn into the title block unreported. On every part, a
/// placed dimension whose label, lines or arrowheads reach into the title block is the subject
/// of an `annotation_over_title_block` problem. The keep-out on draftwright's own cases is
/// `tests/issue_regressions.rs::the_title_block_keeps_dimensions_out_without_dropping_them`. Not
/// ported: a dimension with nowhere to go keeps its place, reported as a warning, where
/// draftwright withholds it as an error and replans onto a sheet that fits.
#[test]
fn a_dimension_in_the_title_block_is_reported() {
    for (name, _, plan) in planned() {
        let tb = plan.sheet.title_block;
        let within = |p: [f64; 2]| tb[0] < p[0] && p[0] < tb[2] && tb[1] < p[1] && p[1] < tb[3];
        for d in &plan.dimensions {
            let Some(p) = &d.placed else { continue };
            let ink = overlap(p.label_box, tb)
                || p.lines
                    .iter()
                    .chain(&p.arrows)
                    .flatten()
                    .any(|q| within(*q));
            let reported = plan
                .problems
                .iter()
                .any(|q| q.code == "annotation_over_title_block" && q.subjects.contains(&d.id));
            assert!(!ink || reported, "{name}: {} is in the title block", d.id);
        }
    }
}

/// ISSUE-1177-a, in part: an angle is never drawn as a length. On every part no linear
/// dimension states an angle, and every angle the plan states (a countersink's, a chamfer's)
/// is printed in degrees in a callout. Not ported: angular dimensions (the port plans none, so
/// none can fall back to a linear one) and their refusal when they cannot be drawn.
#[test]
fn an_angle_is_never_drawn_as_a_length() {
    for (name, _, plan) in planned() {
        for d in &plan.dimensions {
            let angles: Vec<f64> = d
                .measurements
                .iter()
                .filter(|m| m.parameter.ends_with("angle"))
                .map(|m| m.value)
                .collect();
            if d.kind == "linear" {
                assert!(
                    angles.is_empty() && !d.label.contains('°'),
                    "{name}: {}",
                    d.id
                );
            }
            for a in angles {
                assert!(
                    d.label.contains(&format!("{}°", fmt(a))),
                    "{name}: {}",
                    d.id
                );
            }
        }
    }
}

/// ISSUE-1433-a, for fillets: fillets share a callout only when their radii are equal, each
/// occurrence is credited by it, and without it every occurrence is reported. Not ported: blends
/// (quiddity's `Blend`, the rule's subject) are not dimensioned yet: each is reported
/// `dimensions_not_ported` (src/dims/edges.rs plans fillets and chamfers only).
#[test]
fn fillets_share_a_callout_only_when_their_radii_are_equal() {
    for (name, analysis, plan) in planned() {
        let radius = |id: &str| {
            analysis
                .features
                .iter()
                .find(|f| f.id == id)
                .unwrap()
                .parameters["radius"]
                .as_f64()
                .unwrap()
        };
        for d in plan
            .dimensions
            .iter()
            .filter(|d| d.id.contains("/fillet.radius"))
        {
            let printed = d.label.rsplit('R').next().unwrap();
            for m in &d.measurements {
                assert_eq!(fmt(radius(&m.feature)), printed, "{name}: {}", d.id);
            }
            let count = d.measurements.len();
            assert!(
                (count == 1 && !d.label.contains('×'))
                    || d.label.starts_with(&format!("{count}× ")),
                "{name}: {}",
                d.id
            );
        }
    }
    let plan = &planned().iter().find(|p| p.0 == "prismatic").unwrap().2;
    let callout = plan
        .dimensions
        .iter()
        .find(|d| d.id.contains("/fillet.radius"))
        .unwrap();
    assert_eq!(callout.label, "4× R2");
    let (analysis, drawing) = drawn("prismatic", plan);
    let fillet_findings = |marks: &[Mark]| -> BTreeSet<String> {
        lint::check(&analysis, marks)
            .into_iter()
            .filter(|i| i.code.starts_with("fillet_"))
            .flat_map(|i| i.subjects)
            .collect()
    };
    let marks = lint::marks(&drawing);
    assert!(fillet_findings(&marks).is_empty());
    let without: Vec<Mark> = marks.into_iter().filter(|m| m.name != callout.id).collect();
    let every: BTreeSet<String> = callout
        .measurements
        .iter()
        .map(|m| m.feature.clone())
        .collect();
    assert_eq!(fillet_findings(&without), every);
}

/// ISSUE-1374-c, in part: each turned outside-diameter band carries two requirements, its
/// diameter and its length, and a band a groove owns is the groove's, not a second step. On every
/// part, lint's turned-step ledger has exactly those two outcomes for each band it reads, and
/// none for a groove's band; and the default plan states both for each such band (its own
/// `step.diameter` and `step.length`, or the overall diameter claiming the band it is,
/// src/dims/turned.rs), and states neither for a band a groove owns. Not ported: lint's check of
/// a grouped length label's exact count and that it skips no equal neighbour (lint credits each
/// band's claim by its printed value).
#[test]
fn each_turned_band_carries_its_diameter_and_length() {
    let mut grooved = 0;
    let mut stated_bands = 0;
    for (name, analysis, plan) in planned() {
        let Ok(bands) = lint::turned::bands(analysis) else {
            continue;
        };
        let steps = analysis
            .features
            .iter()
            .filter(|f| f.kind == "turned_step")
            .count();
        let ledger = lint::turned::outcomes(analysis, &[]);
        let mut by_band: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
        for o in &ledger {
            by_band.entry(&o.feature).or_default().push(&o.parameter);
        }
        assert_eq!(by_band.len(), bands.len(), "{name}");
        for b in &bands {
            assert_eq!(
                by_band[b.id.as_str()],
                ["step.length", "step.diameter"],
                "{name}"
            );
            if plan.dimensions.iter().any(|d| d.id.contains("/step.")) {
                for parameter in ["step.diameter", "step.length"] {
                    assert!(
                        if parameter == "step.length" {
                            lint::turned::outcomes(analysis, &marks(plan))
                                .iter()
                                .any(|o| {
                                    o.feature == b.id
                                        && o.parameter == parameter
                                        && o.state == lint::turned::State::Placed
                                })
                        } else {
                            stated(plan, parameter).contains(b.id.as_str())
                        },
                        "{name}: {} has no {parameter}",
                        b.id
                    );
                }
                stated_bands += 1;
            }
        }
        let owned: BTreeSet<&str> = bands.iter().map(|b| b.id.as_str()).collect();
        for parameter in ["step.diameter", "step.length"] {
            for f in stated(plan, parameter) {
                assert!(owned.contains(f), "{name}: {f}'s {parameter} is stated");
            }
        }
        if bands.len() < steps {
            grooved += 1;
        }
    }
    if !sampled().is_empty() {
        assert!(grooved > 0, "no sampled part has a band a groove owns");
        assert!(stated_bands > 0, "no sampled part's plan states a band");
    }
}

/// ISSUE-1560-a, in part: the part's extents never leave the drawing silently. On every part,
/// each non-zero axis of the box has an outcome in lint's envelope ledger (placed on the default
/// drawing); an extent the spec omits is listed in the plan's `omitted` with why, and stays in
/// the ledger, missing; an empty authored set leaves all three in the ledger. A turned part's
/// length may be stated by its step chain alone (src/dims/turned.rs). Not ported: a local
/// turned profile or overlapping steps standing for an axis (no sampled part has one), and
/// Python's other carriers of an end-to-end span (`lint::envelope`'s module note).
#[test]
fn the_parts_extents_never_leave_the_drawing_silently() {
    let (part, analysis) = analysed("plate");
    let ledger = |plan: &Plan| -> Vec<String> {
        let drawing = model::render(&part, &analysis, plan, &font()).unwrap();
        lint::envelope::outcomes(&analysis, &lint::marks(&drawing))
            .into_iter()
            .map(|o| format!("{} {:?}", o.parameter, o.state))
            .collect()
    };
    let full = model::plan(&analysis, &Spec::default()).unwrap();
    assert_eq!(
        ledger(&full),
        [
            "width.length Placed",
            "depth.length Placed",
            "height.length Placed"
        ]
    );
    let width = "d:envelope/width.length";
    let less = model::plan(&analysis, &omitting(&[width])).unwrap();
    assert!(
        less.omitted
            .iter()
            .any(|o| o.id == width && !o.basis.is_empty())
    );
    assert_eq!(
        ledger(&less),
        [
            "width.length Missing",
            "depth.length Placed",
            "height.length Placed"
        ]
    );
    let none = Spec {
        dimension_source: DimensionSource::Authored,
        ..Spec::default()
    };
    assert_eq!(
        ledger(&model::plan(&analysis, &none).unwrap()),
        [
            "width.length Missing",
            "depth.length Missing",
            "height.length Missing"
        ]
    );
    for (name, analysis, plan) in planned() {
        let [lo, hi] = analysis.envelope;
        let axes = (0..3).filter(|&k| hi[k] - lo[k] > 1e-9).count();
        let stated: Vec<Mark> = plan
            .dimensions
            .iter()
            .filter(|d| d.id.starts_with("d:envelope/") || d.id.ends_with("/step.length"))
            .map(|d| Mark {
                name: d.id.clone(),
                view: Some(d.view.clone()),
                text: d.label.clone(),
                claims: d
                    .measurements
                    .iter()
                    .map(|m| Claim {
                        feature: m.feature.clone(),
                        parameter: m.parameter.clone(),
                    })
                    .collect(),
            })
            .collect();
        let outcomes = lint::envelope::outcomes(analysis, &stated);
        assert_eq!(outcomes.len(), axes, "{name}");
        for o in &outcomes {
            assert_eq!(
                format!("{:?}", o.state),
                "Placed",
                "{name}: {}",
                o.parameter
            );
        }
    }
}

/// ISSUE-1357-b, in part: eccentric parallel cylinders are not one turned profile. The turned
/// fixture's two bands share one axis line, and the plan states the part's overall diameter (and
/// its length by the step chain); with its smaller band's axis moved 2 mm off that line, the plan
/// no longer reads the part as turned whole (it states the three extents instead) and states each
/// band's diameter and length on its own, none merged or silently chosen. Not ported: several
/// bodies in one compound (no fixture has one).
#[test]
fn eccentric_cylinders_are_not_one_turned_profile() {
    let analysis = &fixtures().iter().find(|p| p.0 == "turned").unwrap().1;
    let envelope = |plan: &Plan| -> Vec<String> {
        plan.dimensions
            .iter()
            .filter(|d| d.id.starts_with("d:envelope/"))
            .map(|d| d.measurements[0].parameter.clone())
            .collect()
    };
    let reported = |plan: &Plan| -> BTreeSet<String> {
        plan.problems
            .iter()
            .filter(|p| p.code == "dimensions_not_ported" || p.code == "feature_not_dimensioned")
            .flat_map(|p| p.subjects.clone())
            .collect()
    };
    // Each band's own items, as (feature, parameter, label).
    let bands = |plan: &Plan| -> Vec<(String, String, String)> {
        plan.dimensions
            .iter()
            .flat_map(|d| {
                d.measurements
                    .iter()
                    .filter(|m| m.feature.starts_with("f:turned_step:"))
                    .map(|m| (m.feature.clone(), m.parameter.clone(), d.label.clone()))
            })
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect()
    };
    let item = |f: &str, p: &str, l: &str| (f.to_string(), p.to_string(), l.to_string());
    let plan = model::plan(analysis, &Spec::default()).unwrap();
    assert_eq!(envelope(&plan), ["od.diameter", "height.length"]);
    assert!(reported(&plan).is_empty(), "{:?}", plan.problems);
    assert_eq!(
        bands(&plan),
        [
            item("f:turned_step:1", "step.diameter", "ø30"),
            item("f:turned_step:1", "step.length", "40"),
            item("f:turned_step:2", "step.diameter", "ø16"),
            item("f:turned_step:2", "step_position.length", "70"),
        ]
    );

    let mut eccentric = analysis.clone();
    let step = eccentric
        .features
        .iter_mut()
        .find(|f| f.id == "f:turned_step:2")
        .unwrap();
    step.parameters["profile"]["axis_origin"] = json!([0.0, 2.0, 0.0]);
    let plan = model::plan(&eccentric, &Spec::default()).unwrap();
    assert_eq!(
        envelope(&plan),
        ["width.length", "depth.length", "height.length"]
    );
    assert!(reported(&plan).is_empty(), "{:?}", plan.problems);
    assert_eq!(
        bands(&plan),
        [
            item("f:turned_step:1", "step.diameter", "ø30"),
            item("f:turned_step:1", "step.length", "40"),
            item("f:turned_step:2", "step.diameter", "ø16"),
            item("f:turned_step:2", "step.length", "30"),
        ]
    );
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.id == "d:f:turned_step:1/step.diameter")
    );
}

/// The plan of fixture *name* with *spec*.
fn fixture_plan(name: &str, s: &Spec) -> Plan {
    let analysis = &fixtures().iter().find(|p| p.0 == name).unwrap().1;
    model::plan(analysis, s).unwrap()
}

fn tolerances(bands: &[(&str, &str)]) -> Spec {
    let mut spec = Spec::default();
    for (id, band) in bands {
        spec.dimensions.insert(
            id.to_string(),
            DimensionSpec {
                tolerance: Some(band.to_string()),
                ..Default::default()
            },
        );
    }
    spec
}

fn dimension<'a>(plan: &'a Plan, id: &str) -> &'a PlannedDimension {
    plan.dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap_or_else(|| panic!("no {id}"))
}

const EXTENTS: [(&str, &str); 3] = [
    ("d:envelope/width.length", "60"),
    ("d:envelope/depth.length", "40"),
    ("d:envelope/height.length", "5"),
];

/// ISSUE-1215-a, in part: an authored envelope tolerance prints in the label of the extent it is
/// set on, and only there. On plate each extent's `±0.05` reaches its own label (and its drawn
/// text run), the other two stay bare, and a pair of deviations prints both limits. Not ported:
/// a bare tolerance folded onto every extent (the spec sets a tolerance on one dimension ID; there
/// is no envelope-wide entry) and a fit class on an extent (a fit is set on a hole or a boss, so
/// `features/<id>/fit` names no extent).
#[test]
fn an_envelope_tolerance_prints_on_its_own_extent() {
    for (id, bare) in EXTENTS {
        let plan = fixture_plan("plate", &tolerances(&[(id, "±0.05")]));
        for (other, other_bare) in EXTENTS {
            let label = &dimension(&plan, other).label;
            if other == id {
                assert_eq!(label, &format!("{bare} ±0.05"), "{id}");
            } else {
                assert_eq!(label, other_bare, "{id} decorates {other}");
            }
        }
        let (_, drawing) = drawn("plate", &plan);
        let a = drawing.annotations.iter().find(|a| a.name == id).unwrap();
        let runs: Vec<&str> = a
            .runs
            .iter()
            .map(|&r| drawing.text[r].text.as_str())
            .collect();
        assert_eq!(runs, [format!("{bare} ±0.05")], "{id}");
    }
    let plan = fixture_plan(
        "plate",
        &tolerances(&[("d:envelope/height.length", "+0.2 -0.1")]),
    );
    assert_eq!(
        dimension(&plan, "d:envelope/height.length").label,
        "5 +0.2 -0.1"
    );
}

/// The faces of the drawing's dims layer whose every point lies inside *b*.
fn faces_inside(drawing: &Drawing, b: [f64; 4]) -> usize {
    use draftwright::output::{Curve, Item};
    let within = |p: &[f64; 2]| {
        b[0] - 1e-6 <= p[0] && p[0] <= b[2] + 1e-6 && b[1] - 1e-6 <= p[1] && p[1] <= b[3] + 1e-6
    };
    let points = |c: &Curve| -> Vec<[f64; 2]> {
        match c {
            Curve::Line(a, b) => vec![*a, *b],
            Curve::Beziers(bs) => bs.iter().flatten().copied().collect(),
            other => vec![other.start(), other.end()],
        }
    };
    drawing
        .layers
        .iter()
        .filter(|l| l.name == "dims")
        .flat_map(|l| &l.items)
        .filter(|i| match i {
            Item::Face(wires) => wires.iter().flatten().all(|c| points(c).iter().all(within)),
            Item::Edge(_) => false,
        })
        .count()
}

/// ISSUE-1215-b: a toleranced label's box encloses the whole label and grows along its
/// dimension line. On plate the rotated height's box grows more than twice along the line and
/// not across it (within the tight ink of the `±` glyph, which reaches 0.04 mm below the digits:
/// the port's box is the glyphs' ink, draftwright's the text's), the horizontal width's grows the
/// other way, each box is the ink of its terms as drawn, and every glyph drawn for the label lies
/// inside its box.
#[test]
fn a_toleranced_label_grows_along_its_line_and_its_box_holds_it() {
    let font = font();
    let bare = fixture_plan("plate", &Spec::default());
    for (id, along) in [
        ("d:envelope/height.length", 1),
        ("d:envelope/width.length", 0),
    ] {
        let plan = fixture_plan("plate", &tolerances(&[(id, "±0.05")]));
        let (b0, b1) = (
            dimension(&bare, id).placed.as_ref().unwrap(),
            dimension(&plan, id).placed.as_ref().unwrap(),
        );
        assert_eq!(b1.rotation, if along == 1 { 90.0 } else { 0.0 });
        let size = |b: [f64; 4], k: usize| b[k + 2] - b[k];
        let across = 1 - along;
        assert!(
            size(b1.label_box, along) > 2.0 * size(b0.label_box, along),
            "{id}: {:?} from {:?}",
            b1.label_box,
            b0.label_box
        );
        assert!(
            (size(b1.label_box, across) - size(b0.label_box, across)).abs() < 0.05,
            "{id}: {:?} from {:?}",
            b1.label_box,
            b0.label_box
        );
        // The box is the ink of the label as drawn, tolerance included.
        let d = dimension(&plan, id);
        let ink = draftwright::dims::label_ink(&d.terms, &font);
        assert!((size(b1.label_box, along) - (ink.max[0] - ink.min[0])).abs() < 1e-9);
        let glyphs = ink.glyphs.iter().filter(|g| !g.is_empty()).count();
        let (_, drawing) = drawn("plate", &plan);
        assert!(
            faces_inside(&drawing, b1.label_box) >= glyphs,
            "{id}: a glyph of {:?} is drawn outside its box",
            d.label
        );
    }
}

/// ISSUE-1215-c, in part: every authored tolerance reaches the label of the dimension it is set
/// on, verbatim, once, and nowhere else; none is dropped. On every part each dimension (and each
/// the default plan leaves out) is given its own band: every planned label prints its own band
/// exactly once and no other, or the plan says it withheld it; one the plan does not draw is
/// reported (`view_plan_incomplete`) and one it leaves out is reported (`tolerance_not_shown`);
/// on plate each band is drawn in exactly one text run. Not ported: a callout's other terms
/// (counterbore and countersink sizes, bore depth) have no dimension ID, so they cannot be
/// toleranced (draftwright's kind-keyed sweep tolerances them too); hole tables (the port draws
/// none).
#[test]
fn every_authored_tolerance_reaches_the_label_that_claims_it() {
    let mut seen = 0;
    for (name, analysis, plan) in planned() {
        let ids: Vec<&str> = plan
            .dimensions
            .iter()
            .map(|d| d.id.as_str())
            .chain(plan.omitted.iter().map(|o| o.id.as_str()))
            .collect();
        let bands: BTreeMap<&str, String> = ids
            .iter()
            .enumerate()
            .map(|(i, id)| (*id, format!("±0.{:04}", i + 1)))
            .collect();
        let pairs: Vec<(&str, &str)> = bands.iter().map(|(id, b)| (*id, b.as_str())).collect();
        let toleranced = model::plan(analysis, &tolerances(&pairs)).unwrap();
        let reported = |code: &str, id: &str| {
            toleranced
                .problems
                .iter()
                .any(|p| p.code == code && p.subjects.iter().any(|s| s == id))
        };
        let mut accounted = BTreeSet::new();
        for d in &toleranced.dimensions {
            accounted.insert(d.id.as_str());
            let withheld = reported("tolerance_withheld", &d.id);
            for (id, band) in &bands {
                let want = usize::from(*id == d.id && !withheld);
                assert_eq!(
                    d.label.matches(band.as_str()).count(),
                    want,
                    "{name}: {} prints {:?} ({id}'s band {band})",
                    d.id,
                    d.label
                );
            }
            assert_eq!(
                d.label,
                draftwright::callouts::label(&d.terms),
                "{name}: {}",
                d.id
            );
            if d.placed.is_none() {
                // A profile-corner angle with no clear place is dropped by design and reported.
                assert!(
                    reported("view_plan_incomplete", &d.id)
                        || reported("angular_dimension_dropped", &d.id),
                    "{name}: {}",
                    d.id
                );
            }
            seen += 1;
        }
        for o in &toleranced.omitted {
            accounted.insert(o.id.as_str());
            assert!(reported("tolerance_not_shown", &o.id), "{name}: {}", o.id);
        }
        for id in bands.keys() {
            assert!(accounted.contains(id), "{name}: {id} vanished");
        }
        if name == "plate" {
            let (_, drawing) = drawn("plate", &toleranced);
            for band in bands.values() {
                let runs = drawing
                    .text
                    .iter()
                    .filter(|t| t.text.contains(band.as_str()))
                    .count();
                assert_eq!(runs, 1, "{band}");
            }
        }
    }
    assert!(seen > 100, "{seen}");
}

/// ISSUE-1215-d, in part: a pattern whose gaps are not equal claims no tolerance on a pitch it
/// does not have. two_grids' first linear pattern with its middle hole moved 0.3 mm (gaps 10.3
/// and 9.7): the port does not collapse it to `2× 10`, so it plans no pitch, and a tolerance set
/// on that absent pitch warns without inventing it; its holes are dimensioned singly, and their callout's
/// basis says why. The control: the even pattern prints `2× 10 ±0.05`. draftwright collapses
/// the jittered pattern (its recogniser admits 2 % jitter), prints the bare `2× 10` and records
/// the withheld tolerance; the port states no equal pitch over unequal gaps at all
/// (rust-correct). Not ported: the `N× rise` representative that stays deliberately bare (the
/// port dimensions no step levels on main).
#[test]
fn a_jittered_pattern_claims_no_pitch_tolerance() {
    let pitch = "d:f:hole_pattern:1/pitch.length";
    let even = fixture_plan("two_grids", &tolerances(&[(pitch, "±0.05")]));
    assert_eq!(dimension(&even, pitch).label, "2× 10 ±0.05");

    let mut jittered = fixtures()
        .iter()
        .find(|p| p.0 == "two_grids")
        .unwrap()
        .1
        .clone();
    let middle = jittered
        .features
        .iter_mut()
        .find(|f| f.id == "f:hole:2")
        .unwrap();
    let z = middle.parameters["location"][2].as_f64().unwrap();
    middle.parameters["location"][2] = json!(z + 0.3);
    let plan = model::plan(&jittered, &Spec::default()).unwrap();
    assert!(
        plan.dimensions
            .iter()
            .all(|d| !d.id.starts_with("d:f:hole_pattern:1/")),
        "the uneven pattern is still called out as one"
    );
    let single = plan
        .dimensions
        .iter()
        .find(|d| hole_callout(d) && d.measurements.iter().any(|m| m.feature == "f:hole:2"))
        .unwrap();
    assert!(
        single.basis.contains("not evenly spaced"),
        "{}",
        single.basis
    );
    let p = model::plan(&jittered, &tolerances(&[(pitch, "±0.05")])).unwrap();
    assert!(p.problems.iter().any(|p| {
        p.code == "dimension_edit_unapplied"
            && p.subjects
                .contains(&format!("dimensions/{}", draftwright::items::key(pitch)))
    }));
    assert!(p.dimensions.iter().all(|d| d.id != pitch));
    assert!(p.dimensions.iter().any(|d| d.placed.is_some()));
}

/// ISSUE-1215-e: an overall extent never leaves the sheet silently. On every part each
/// envelope extent the plan keeps is placed, and one whose ink runs off the drawing area is an
/// `annotation_off_sheet` error naming it. The fall-through to the opposite strip is checked in
/// `tests/placement.rs`; draftwright's withholding and its retraction are not-applicable (the
/// port withholds no extent).
#[test]
fn an_overall_extent_is_placed_or_reported() {
    let margin = draftwright::layout::margin();
    for (name, _, plan) in planned() {
        let [w, h] = plan.sheet.page;
        for d in plan
            .dimensions
            .iter()
            .filter(|d| d.id.starts_with("d:envelope/"))
            // A profile-corner angle shares the envelope prefix but is dropped, reported, when
            // it has no clear place (LINT-angular-profile_angle_requirement).
            .filter(|d| !d.id.starts_with("d:envelope/profile_angle."))
        {
            let p = d
                .placed
                .as_ref()
                .unwrap_or_else(|| panic!("{name}: {} is not placed", d.id));
            let b = p.label_box;
            let mut ends = p.lines.iter().chain(&p.arrows).flatten();
            let outside = |q: &[f64; 2]| {
                q[0] < margin - 1e-9
                    || q[1] < margin - 1e-9
                    || q[0] > w - margin + 1e-9
                    || q[1] > h - margin + 1e-9
            };
            if outside(&[b[0], b[1]]) || outside(&[b[2], b[3]]) || ends.any(outside) {
                assert!(
                    plan.problems
                        .iter()
                        .any(|q| q.code == "annotation_off_sheet"
                            && q.severity == "error"
                            && q.subjects == [d.id.clone()]),
                    "{name}: {} runs off the sheet unreported",
                    d.id
                );
            }
        }
    }
}

/// m10127's one boss, moved along its axis and resized in quiddity's frame so that it spans
/// *span* (world coordinates along its axis, low first), as `tests/dims_slots.rs` does.
fn boss_spanning(a: &mut Analysis, span: [f64; 2]) {
    let frame = a.recognition.as_ref().unwrap().frame;
    let b = a.features.iter_mut().find(|f| f.kind == "boss").unwrap();
    let local: Vec<f64> = serde_json::from_value(b.parameters["axis"].clone()).unwrap();
    let mut top: Vec<f64> = serde_json::from_value(b.parameters["location"].clone()).unwrap();
    let dir: Vec<f64> = (0..3)
        .map(|k| (0..3).map(|j| local[j] * frame[j + 1][k]).sum())
        .collect();
    let k = (0..3).find(|&k| dir[k].abs() > 0.5).unwrap();
    let world_top: f64 = frame[0][k] + (0..3).map(|j| top[j] * frame[j + 1][k]).sum::<f64>();
    let target = if dir[k] > 0.0 { span[1] } else { span[0] };
    let t = (target - world_top) / dir[k];
    for j in 0..3 {
        top[j] += t * local[j];
    }
    b.parameters.insert("location".into(), top.into());
    b.parameters
        .insert("height".into(), (span[1] - span[0]).into());
}

/// ISSUE-1154-a, its tolerance clause: a toleranced height is not handed to an untoleranced
/// extent. m10127's boss made flush with both faces of the part, so its height and the overall
/// depth are one measurement: untoleranced, the depth states it and the height is omitted as
/// conveyed by it (`tests/dims_slots.rs`); with the height toleranced both are stated, the
/// height with its band; with the depth toleranced the height is still conveyed by it; with
/// both toleranced both are stated (draftwright's `TestTheToleranceRuleIsAsymmetric`).
#[test]
fn a_toleranced_height_is_not_handed_to_an_untoleranced_extent() {
    let Some((_, analysis)) = sampled().iter().find(|p| p.0 == "m10127") else {
        return;
    };
    let mut a = analysis.clone();
    let [lo, hi] = a.envelope;
    boss_spanning(&mut a, [lo[1], hi[1]]);
    let flush = model::plan(&a, &Spec::default()).unwrap();
    let height = flush
        .omitted
        .iter()
        .find(|o| o.id.ends_with("/boss_height.length"))
        .expect("the flush boss height is conveyed")
        .id
        .clone();
    let depth = flush
        .dimensions
        .iter()
        .find(|d| d.id.starts_with("d:envelope/depth.length"))
        .unwrap();
    let (depth, bare) = (depth.id.clone(), depth.label.clone());
    let labels = |bands: &[(&str, &str)]| -> Vec<String> {
        let plan = model::plan(&a, &tolerances(bands)).unwrap();
        let mut out: Vec<String> = plan
            .dimensions
            .iter()
            .filter(|d| d.id == height || d.id == depth)
            .map(|d| d.label.clone())
            .collect();
        out.sort();
        let conveyed = plan.omitted.iter().any(|o| o.id == height);
        assert_eq!(conveyed, out.len() == 1, "{bands:?}");
        out
    };
    let banded = format!("{bare} ±0.05");
    assert_eq!(
        labels(&[(height.as_str(), "±0.05")]),
        [bare.clone(), banded.clone()]
    );
    assert_eq!(
        labels(&[(depth.as_str(), "±0.05")]),
        std::slice::from_ref(&banded)
    );
    assert_eq!(
        labels(&[(height.as_str(), "±0.05"), (depth.as_str(), "±0.05")]),
        [banded.clone(), banded]
    );
}

/// ISSUE-1216-a, in part: the room a callout takes is measured from the text it draws,
/// tolerance included. On plate the pattern callout's placed box is the ink of its terms with
/// the band (`4× ⌀6 ±0.05 THRU`), wider than the bare callout's by the band's ink. Not
/// ported: draftwright's pre-placement reservation (`demand.rs` does not reserve the hole
/// callout band; placement then reports what runs past the view's area) and a tolerance on a
/// recess term alone (recess terms have no dimension ID).
#[test]
fn a_callouts_room_is_measured_from_the_text_it_draws() {
    let font = font();
    let id = "d:f:hole_pattern:1/bore.diameter";
    let width = |plan: &Plan| {
        let d = dimension(plan, id);
        let b = d.placed.as_ref().unwrap().label_box;
        let ink = draftwright::dims::label_ink(&d.terms, &font);
        assert!(
            ((b[2] - b[0]) - (ink.max[0] - ink.min[0])).abs() < 1e-9,
            "{}",
            d.label
        );
        b[2] - b[0]
    };
    let bare = fixture_plan("plate", &Spec::default());
    let banded = fixture_plan("plate", &tolerances(&[(id, "±0.05")]));
    assert_eq!(dimension(&banded, id).label, "4× ⌀6 ±0.05 THRU");
    let band = draftwright::dims::label_ink(&["±0.05".to_string()], &font);
    assert!(width(&banded) >= width(&bare) + (band.max[0] - band.min[0]) - 1e-9);
}

/// ISSUE-1524-a, in part: an authored magnitude is never rounded to the value's precision. On
/// plate's pattern callout each of draftwright's cases prints as draftwright's `_tol_suffix`
/// does (`±1` as `±1.0`, `±0.000000123` and `+0.00001 -0` whole), in the label, the drawn text
/// run and the placed box (the ink of the whole label), and adds no problem. Not ported: hole
/// tables (the port draws none).
#[test]
fn an_authored_magnitude_is_never_rounded() {
    let font = font();
    let id = "d:f:hole_pattern:1/bore.diameter";
    let bare = fixture_plan("plate", &Spec::default());
    for (authored, printed) in [
        ("±0.02", "±0.02"),
        ("±0.05", "±0.05"),
        ("±0.2", "±0.2"),
        ("±1", "±1.0"),
        ("±1.234567", "±1.234567"),
        ("±0.000000123", "±0.000000123"),
        ("+0.015 -0.002", "+0.015 -0.002"),
        ("+0.00001 -0", "+0.00001 0"),
    ] {
        let plan = fixture_plan("plate", &tolerances(&[(id, authored)]));
        let d = dimension(&plan, id);
        // Without the grid's "(2×2)" (#225) even the longest bands fit within the 48 mm limit.
        assert_eq!(d.label, format!("4× ⌀6 {printed} THRU"), "{authored}");
        let b = d.placed.as_ref().unwrap().label_box;
        let ink = draftwright::dims::label_ink(&d.terms, &font);
        assert!(((b[2] - b[0]) - (ink.max[0] - ink.min[0])).abs() < 1e-9);
        assert_eq!(codes(&plan), codes(&bare), "{authored}");
        let (_, drawing) = drawn("plate", &plan);
        let annotation = drawing.annotations.iter().find(|a| a.name == d.id).unwrap();
        assert_eq!(
            annotation
                .runs
                .iter()
                .map(|&i| drawing.text[i].text.as_str())
                .collect::<Vec<_>>()
                .join("\n"),
            d.label,
            "{authored}"
        );
    }
}

/// Whether segment *s* passes through the open box *b* (Liang–Barsky, as placement clips).
fn crosses(s: [[f64; 2]; 2], b: [f64; 4]) -> bool {
    let (p, q) = (s[0], s[1]);
    let d = [q[0] - p[0], q[1] - p[1]];
    let (mut t0, mut t1) = (0.0f64, 1.0f64);
    for (num, den) in [
        (p[0] - b[0], -d[0]),
        (b[2] - p[0], d[0]),
        (p[1] - b[1], -d[1]),
        (b[3] - p[1], d[1]),
    ] {
        if den == 0.0 {
            if num <= 0.0 {
                return false;
            }
        } else {
            let t = num / den;
            if den < 0.0 {
                t0 = t0.max(t);
            } else {
                t1 = t1.min(t);
            }
        }
    }
    t1 - t0 > 1e-9
}

fn reported_pair(plan: &Plan, code: &str, a: &str, b: &str) -> bool {
    plan.problems.iter().any(|p| {
        // Overlapping labels deliberately suppress the duplicate ink warning.
        (p.code == code || (code == "annotation_ink_overlap" && p.code == "annotation_overlap"))
            && p.subjects.iter().any(|s| s == a)
            && p.subjects.iter().any(|s| s == b)
    })
}

/// ISSUE-1334-a, in part: dimension ink already placed is what peer labels are kept off, by the
/// measure the final check uses. On every part, after labels settle, any line of one item
/// through another's label is an `annotation_ink_overlap` naming both, and any two labels that
/// overlap an `annotation_overlap`; and the settled places are the same plan after plan. Not
/// ported: promoting a label to a farther lane when no shift along its line is clear (the port
/// leaves it and reports the collision).
#[test]
fn placed_ink_keeps_peer_labels_off_by_the_final_measure() {
    for (name, analysis, plan) in planned() {
        let placed: Vec<(&str, &draftwright::dims::Placed)> = plan
            .dimensions
            .iter()
            .filter_map(|d| Some((d.id.as_str(), d.placed.as_ref()?)))
            .collect();
        for (a, pa) in &placed {
            for (b, pb) in &placed {
                if a == b {
                    continue;
                }
                if pa.lines.iter().any(|l| crosses(*l, pb.label_box)) {
                    assert!(
                        reported_pair(plan, "annotation_ink_overlap", a, b),
                        "{name}: a line of {a} crosses {b}'s label unreported"
                    );
                }
                if overlap(pa.label_box, pb.label_box) {
                    assert!(
                        reported_pair(plan, "annotation_overlap", a, b),
                        "{name}: {a} and {b} overlap unreported"
                    );
                }
            }
        }
        let again = model::plan(analysis, &Spec::default()).unwrap();
        assert_eq!(
            serde_json::to_string(&again.dimensions).unwrap(),
            serde_json::to_string(&plan.dimensions).unwrap(),
            "{name}"
        );
    }
}

/// ISSUE-367-a, checked on the drawings only: an arrowhead's flare in another item's label is
/// reported. On every part each arrowhead, as `dims::render` draws it (a triangle 2.7 mm long
/// and 0.9 mm wide at its tail), that reaches into another item's label box belongs to an item
/// reported with that label (`annotation_ink_overlap`). It holds because every such arrowhead's
/// shaft crosses the label too (m10127's side depth); the collision checks themselves test
/// zero-width centrelines, without the flare or the stroke (see the rule's `rust_note`). Since
/// callout leaders are placed clear of labels (stream d-callout-leaders, `src/dims/leaders.rs`),
/// that collision is gone and no part here has an arrowhead in a label (default plans, and the
/// fixture parts pinned to A4 or A3 at 1:5 to 10:1); the planted case that keeps the check
/// exercised is `src/dims/placement.rs`'s unit test
/// `an_arrowhead_and_a_leader_through_other_ink_are_reported`, which the public API cannot make.
#[test]
fn an_arrowhead_in_another_label_is_reported() {
    let inside_triangle = |t: [[f64; 2]; 3], b: [f64; 4]| {
        (0..=20).any(|i| {
            (0..=20 - i).any(|j| {
                let (u, v) = (f64::from(i) / 20.0, f64::from(j) / 20.0);
                let w = 1.0 - u - v;
                let p = [
                    u * t[0][0] + v * t[1][0] + w * t[2][0],
                    u * t[0][1] + v * t[1][1] + w * t[2][1],
                ];
                b[0] < p[0] && p[0] < b[2] && b[1] < p[1] && p[1] < b[3]
            })
        })
    };
    let half = draftwright::dims::ARROW / 6.0;
    for (name, _, plan) in planned() {
        for a in &plan.dimensions {
            let Some(pa) = &a.placed else { continue };
            for b in &plan.dimensions {
                let Some(pb) = &b.placed else { continue };
                if a.id == b.id {
                    continue;
                }
                for [tip, tail] in &pa.arrows {
                    let n = [tail[1] - tip[1], tip[0] - tail[0]];
                    let k = half / n[0].hypot(n[1]).max(1e-12);
                    let head = [
                        *tip,
                        [tail[0] + k * n[0], tail[1] + k * n[1]],
                        [tail[0] - k * n[0], tail[1] - k * n[1]],
                    ];
                    if inside_triangle(head, pb.label_box) {
                        assert!(
                            reported_pair(plan, "annotation_ink_overlap", &a.id, &b.id),
                            "{name}: an arrowhead of {} lies in {}'s label unreported",
                            a.id,
                            b.id
                        );
                    }
                }
            }
        }
    }
}

/// ISSUE-1240-a, in part: annotations and the isometric view do not overlap unreported. On every
/// part with an isometric, each placed label over its drawn box (the projected bounds at its
/// scale) is an `annotation_over_view` naming `v:iso`. A default plan's label over the isometric
/// is itself a placement defect (ftc06's, recorded rust-wrong in known_placement.json), so the
/// check is also run on plate pinned to A4 at 5:1, a scale whose annotations the sheet has no
/// room for (some run off it, reported), where labels crowd onto the isometric and at least one
/// must: that keeps the check exercised once the defects are fixed. (It was A3 at 2:1 until
/// placement's fall-through to the opposite strip and callouts beside their view, stream
/// d-placement-fit, found room for every label there.) Not ported: clamping a strip
/// below an overlapping isometric, and capping the isometric's growth at placed annotations (the
/// port fits it into the space the views' areas leave, before anything is placed).
#[test]
fn a_label_over_the_isometric_is_reported() {
    let plate = &fixtures().iter().find(|p| p.0 == "plate").unwrap().1;
    let mut crowded = model::plan(
        plate,
        &spec(json!({ "sheet": { "size": "A4", "scale": "5:1" } })),
    )
    .unwrap();
    assert!(codes(&crowded).contains("annotation_off_sheet"));
    // Since H6 lifts every view clear of the title block, this case no longer crowds a label
    // onto the isometric, and no natural case is left: the five fixtures on A4 and A3 at 10:1 to
    // 1:1, and on A3 and A2 with the isometric pinned at zero gap beside each view, place none
    // (probed 2026-10-12). So the placement runs again, for real, around an isometric enlarged
    // to cover the plan's bands, which keeps the reporting check exercised on placed labels.
    crowded
        .views
        .iter_mut()
        .find(|v| v.id == "v:iso")
        .unwrap()
        .scale = 100.0;
    let (dims, omitted, problems) =
        draftwright::dims::plan(plate, &Spec::default(), &crowded.sheet, &crowded.views).unwrap();
    crowded.dimensions = dims;
    crowded.omitted = omitted;
    crowded.problems.extend(problems);
    let cases = planned()
        .iter()
        .map(|(n, a, p)| (n.as_str(), a, p))
        .chain([("plate on A4 at 5:1", plate, &crowded)]);
    let mut seen = 0;
    let mut seen_crowded = 0;
    for (name, analysis, plan) in cases {
        let Some(iso) = plan.views.iter().find(|v| v.id == "v:iso") else {
            continue;
        };
        let ib = analysis.iso_bounds;
        let drawn = [
            iso.at[0] + iso.scale * ib[0],
            iso.at[1] + iso.scale * ib[1],
            iso.at[0] + iso.scale * ib[2],
            iso.at[1] + iso.scale * ib[3],
        ];
        for d in &plan.dimensions {
            let Some(p) = &d.placed else { continue };
            if overlap(p.label_box, drawn) {
                seen += 1;
                if std::ptr::eq(plan, &crowded) {
                    seen_crowded += 1;
                }
                assert!(
                    reported_pair(plan, "annotation_over_view", &d.id, "v:iso"),
                    "{name}: {} lies over the isometric unreported",
                    d.id
                );
            }
        }
    }
    assert!(
        seen > 0,
        "no label lies over an isometric: the check guards nothing"
    );
    assert!(
        seen_crowded > 0,
        "no label lies over plate's isometric on A4 at 5:1: the check loses its fixed-defect guard"
    );
}

/// ISSUE-1276-a, for grooves: a groove's callout leads to its floor. On every part, each placed
/// groove callout's arrow tip lies on the groove's floor in its view: the floor radius off the
/// groove's own axis, at its own axial station, so neither the outer surface nor another
/// station's floor takes the tip (connector109's three grooves, two of them equal). Not ported:
/// rotation about a feature's own shaft on a part with several or partial shafts, knurl notes,
/// and draftwright's per-feature notes on a groove (`POLISH GROOVE`).
#[test]
fn a_groove_callout_leads_to_its_floor() {
    let mut seen = 0;
    for (name, analysis, plan) in planned() {
        let frame = holes::frame(analysis);
        for d in &plan.dimensions {
            // A counted callout (#220: connector109's "2× 6 WIDE × ø122") measures each equal
            // groove, so each of its tips must lie on one of their floors; every groove it
            // measures counts as seen.
            let floors: Vec<_> = d
                .measurements
                .iter()
                .filter(|m| m.parameter == "groove.diameter")
                .collect();
            if floors.is_empty() {
                continue;
            }
            let (Some(p), Some(v)) = (&d.placed, plan.views.iter().find(|v| v.id == d.view)) else {
                continue;
            };
            let on_floor_of = |feature: &str, value: f64, tip: [f64; 2]| {
                let f = analysis.features.iter().find(|f| f.id == feature).unwrap();
                let at = lint::parameters::place(&frame, f.parameters.get("at")).unwrap();
                let axis = lint::parameters::direction(&frame, f.parameters.get("axis")).unwrap();
                let k = holes::principal(axis).unwrap();
                let r = 0.5 * value;
                (0..3).filter(|&j| j != k).any(|j| {
                    [-r, r].into_iter().any(|o| {
                        let mut site = at;
                        site[j] += o;
                        let s = page_of(v, analysis, site);
                        (tip[0] - s[0]).hypot(tip[1] - s[1]) < 1e-3
                    })
                })
            };
            for arrow in &p.arrows {
                let tip = arrow[0];
                let on_floor = floors.iter().any(|m| on_floor_of(&m.feature, m.value, tip));
                assert!(on_floor, "{name}: {} tip at {tip:?}", d.id);
            }
            seen += floors.len();
        }
    }
    if !sampled().is_empty() {
        assert!(seen >= 3, "{seen}");
    }
}

/// ISSUE-915-a, its witness clause: each level's own face support defines its rung's witnesses.
/// On every part, both witness lines of each height in the ladder stand (one extension-line gap
/// off) at its level's support (its faces' largest x and smallest y), not at the part's box; a
/// level with no recorded support is reported, its witnesses on the box. Checked to differ from
/// the box on some part. Not ported: draftwright's single spacing solve for a dense plan view's
/// hole callouts, and detail views (none is drawn, so no crop or empty-region search).
#[test]
fn a_rungs_witnesses_stand_on_its_levels_support() {
    // dims::GAP: an extension line starts this far off the point it extends from.
    const GAP: f64 = 1.0;
    let (mut seen, mut off_box) = (0, 0);
    for (name, analysis, plan) in planned() {
        let [lo, hi] = analysis.envelope;
        for d in &plan.dimensions {
            if !d.id.starts_with("d:step_level/step_height.length.h") {
                continue;
            }
            let (Some(p), Some(v)) = (&d.placed, plan.views.iter().find(|v| v.id == d.view)) else {
                continue;
            };
            assert!(
                d.view == "v:front"
                    || v.detail
                        .as_ref()
                        .is_some_and(|d| d.source_view == "v:front"),
                "{name}: {}",
                d.id
            );
            seen += 1;
            let z = lo[2] + d.value;
            let key = |z: f64| (z * 1e3).round() as i64;
            let support = analysis
                .level_supports
                .iter()
                .rev()
                .find(|s| key(s.z) == key(z));
            let [x, y] = match support {
                Some(s) => [s.x_span[1], s.y_span[0]],
                None => {
                    assert!(codes(plan).contains("step_level_support_missing"), "{name}");
                    [hi[0], lo[1]]
                }
            };
            if (x - hi[0]).abs() > 1e-6 {
                off_box += 1;
            }
            for (line, foot) in p.lines.iter().zip([[x, y, lo[2]], [x, y, z]]) {
                let f = page_of(v, analysis, foot);
                let start = line[0];
                assert!(
                    (start[1] - f[1]).abs() < 1e-6 && ((start[0] - f[0]).abs() - GAP).abs() < 1e-6,
                    "{name}: {} witness from {start:?}, its level's support at {f:?}",
                    d.id
                );
            }
        }
    }
    assert!(seen > 0);
    if !sampled().is_empty() {
        assert!(off_box > 0, "no rung's support differs from the box");
    }
}

/// A part of `tests/fixtures/recognition/steps/` with its recognition.
fn steps_part(name: &str) -> (draftwright::haecceity::Part, Analysis) {
    let dir = fixture("recognition/steps");
    let mut json = String::new();
    flate2::read::GzDecoder::new(std::fs::File::open(dir.join(format!("{name}.json.gz"))).unwrap())
        .read_to_string(&mut json)
        .unwrap();
    let (part, mut a) = model::analyse(&dir.join(format!("{name}.step"))).unwrap();
    model::recognise(&part, &mut a, &json).unwrap();
    (part, a)
}

/// ISSUE-924-a, in part: a shoulder coincident with its datum is not drawn but reported, and only
/// it leaves its chain. The block stepped along x (from -20 to 20, its shoulder 20 from the box)
/// is given risers standing on its box's low x and low y faces, as draftwright's tests declare
/// them: the ladder keeps the 20 and draws no zero-length shoulder, each coincident one an info
/// `step_position_coincident_with_datum` naming its axis. With only a coincident shoulder and its
/// view omitted, the plan needs no view for it. Not ported: draftwright also lists the shoulder
/// among its suppressions (value 0, not authored); the plan's `omitted` lists only items it
/// holds, and a coincident shoulder is no item.
#[test]
fn a_shoulder_on_the_box_is_reported_not_drawn() {
    let (part, mut a) = steps_part("stepped_block");
    let [lo, _] = a.envelope;
    a.features.clear();
    let wall = a.risers[0].clone();
    assert_eq!(
        (wall.axis.as_str(), wall.positions.as_slice()),
        ("x", &[0.0][..])
    );
    let on_box = |axis: &str, at: f64| model::Riser {
        axis: axis.into(),
        positions: vec![at],
        ..wall.clone()
    };
    let shoulders = |plan: &Plan| -> Vec<(String, String)> {
        plan.dimensions
            .iter()
            .filter(|d| d.id.contains("/step_position."))
            .map(|d| (d.id.clone(), d.label.clone()))
            .collect()
    };
    let coincident = |plan: &Plan| -> Vec<String> {
        plan.problems
            .iter()
            .filter(|p| p.code == "step_position_coincident_with_datum")
            .inspect(|p| assert_eq!(p.severity, "info"))
            .map(|p| p.message.clone())
            .collect()
    };

    a.risers = vec![wall.clone(), on_box("x", lo[0]), on_box("y", lo[1])];
    let plan = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(
        shoulders(&plan),
        [(
            "d:step_level/step_position.length.x20".to_string(),
            "20".to_string()
        )]
    );
    assert!(plan.dimensions.iter().all(|d| d.value > 0.0));
    let reports = coincident(&plan);
    assert_eq!(reports.len(), 2, "{reports:?}");
    assert!(
        reports[0].starts_with("The X shoulder at -20 "),
        "{reports:?}"
    );
    assert!(
        reports[1].starts_with("The Y shoulder at -15 "),
        "{reports:?}"
    );
    let drawing = model::render(&part, &a, &plan, &font()).unwrap();
    let drawn: Vec<&str> = drawing
        .annotations
        .iter()
        .filter(|n| n.name.contains("/step_position."))
        .map(|n| n.name.as_str())
        .collect();
    assert_eq!(drawn, ["d:step_level/step_position.length.x20"]);

    for (axis, at, view) in [("x", lo[0], "plan"), ("y", lo[1], "side")] {
        a.risers = vec![on_box(axis, at)];
        let plan = model::plan(&a, &spec(json!({ "views": { view: { "omit": true } } }))).unwrap();
        assert!(shoulders(&plan).is_empty(), "{axis}");
        assert_eq!(coincident(&plan).len(), 1, "{axis}");
        assert!(
            plan.problems
                .iter()
                .all(|p| p.code == "step_position_coincident_with_datum"
                    || !p.refs.iter().any(|s| s == "step_level")),
            "{axis}: {:#?}",
            plan.problems
        );
    }
}

/// ISSUE-443-a: every GRM-03 shoulder is determined by actual printed ink and the
/// isometric remains. Python states four local lengths; the raw Rust estimate
/// uses the inspection-end baseline, with one local length and two shoulder
/// stations. Prove all eight band quantities independently, including after
/// recovery enlarges the complete main views instead of adding a partial head.
#[test]
fn grm03_locates_every_turned_shoulder_and_keeps_the_isometric() {
    let Some((_, analysis, plan)) = planned().iter().find(|p| p.0 == "grm03") else {
        return;
    };
    let facts = facts::load("grm03");
    let theirs: BTreeSet<&str> = facts
        .annotations
        .iter()
        .filter(|a| a.name.starts_with("m_steplen"))
        .filter_map(|a| a.label.as_deref())
        .collect();
    assert_eq!(theirs, BTreeSet::from(["0.5", "2", "3", "18"]));
    let ours: BTreeSet<&str> = plan
        .dimensions
        .iter()
        .filter(|d| d.id.ends_with("/step.length") && d.placed.is_some())
        .map(|d| d.label.as_str())
        .collect();
    assert_eq!(ours, BTreeSet::from(["0.5"]));
    assert_eq!(
        plan.dimensions
            .iter()
            .filter(|d| d.id.ends_with("/step_position.length") && d.placed.is_some())
            .map(|d| d.label.as_str())
            .collect::<BTreeSet<_>>(),
        BTreeSet::from(["2.5", "5.5"])
    );
    let printed: Vec<_> = marks(plan)
        .into_iter()
        .filter(|m| {
            plan.dimensions
                .iter()
                .any(|d| d.id == m.name && d.placed.is_some())
        })
        .collect();
    let outcomes = lint::turned::outcomes(analysis, &printed);
    assert_eq!(outcomes.len(), 8);
    assert!(
        outcomes
            .iter()
            .all(|o| o.state == lint::turned::State::Placed),
        "{outcomes:?}"
    );
    for station in printed
        .iter()
        .filter(|m| m.name.ends_with("/step_position.length"))
    {
        let kept: Vec<_> = printed
            .iter()
            .filter(|m| m.name != station.name)
            .cloned()
            .collect();
        assert!(
            lint::turned::outcomes(analysis, &kept)
                .iter()
                .any(|o| o.parameter == "step.length" && o.state != lint::turned::State::Placed),
            "{} did not control a physical interval",
            station.name
        );
    }
    let recovered = model::recover(analysis, &Spec::default(), plan.clone()).unwrap();
    assert_eq!(
        (
            recovered.sheet.size.as_str(),
            recovered.sheet.scale.as_str()
        ),
        ("A3", "10:1")
    );
    assert!(recovered.views.iter().all(|v| v.detail.is_none()));
    assert!(recovered.views.iter().any(|v| v.id == "v:iso"));
    assert_eq!(
        plan.dimensions
            .iter()
            .filter(|d| d.id.ends_with("/step_position.length"))
            .count(),
        2
    );
    assert!(facts.views.iter().any(|v| v.name == "iso"));
    assert!(plan.views.iter().any(|v| v.id == "v:iso"));
    assert_eq!(
        (facts.sheet.format.as_str(), facts.sheet.scale.as_str()),
        ("A4", "5:1")
    );
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A4", "2:1")
    );
    assert!(
        plan.problems
            .iter()
            .all(|p| !p.subjects.iter().any(|s| s.starts_with("f:turned_step:"))),
        "{:#?}",
        plan.problems
    );
}

/// draftwright's hard layout codes as the port reports them: what makes `model::recover`
/// re-plan (its private `HARD_LAYOUT`, at warning or error).
const HARD_LAYOUT: [&str; 6] = [
    "annotation_off_sheet",
    "annotation_overlap",
    "annotation_ink_overlap",
    "title_field_overflow",
    "page_fit_uncertain",
    "views_do_not_fit",
];

/// Every part's default plan with draftwright's recovery applied (`model::recover`, which
/// `model::plan` does not apply yet: a maintainer question in docs/next-session.md).
fn recovered() -> &'static [(String, Plan)] {
    static RECOVERED: OnceLock<Vec<(String, Plan)>> = OnceLock::new();
    RECOVERED.get_or_init(|| {
        planned()
            .iter()
            .map(|(n, a, p)| {
                (
                    n.clone(),
                    model::recover(a, &Spec::default(), p.clone()).unwrap(),
                )
            })
            .collect()
    })
}

/// Hole/shoulder quantities stated directly or independently proven by printed baselines.
/// The linter checks actual band geometry and both printed shoulder stations; a Covered
/// ledger entry alone is insufficient, and unplaced annotations supply no evidence.
fn hole_and_shoulder_outcomes(analysis: &Analysis, plan: &Plan) -> BTreeSet<(String, String)> {
    let mut outcomes: BTreeSet<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .flat_map(|d| &d.measurements)
        .chain(plan.hole_tables.iter().flat_map(|t| {
            t.rows
                .iter()
                .flat_map(|r| &r.measurements)
                .chain(&t.covered)
        }))
        .filter(|m| m.feature.starts_with("f:hole") || m.feature.starts_with("f:turned_step:"))
        .map(|m| (m.feature.clone(), m.parameter.clone()))
        .collect();
    let live: BTreeSet<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.placed.is_some())
        .map(|d| d.id.as_str())
        .collect();
    let printed: Vec<_> = marks(plan)
        .into_iter()
        .filter(|m| live.contains(m.name.as_str()))
        .collect();
    outcomes.extend(
        lint::turned::outcomes(analysis, &printed)
            .into_iter()
            .filter(|o| o.state == lint::turned::State::Placed)
            .map(|o| (o.feature, o.parameter)),
    );
    outcomes
}

/// H6 replaces the old recovery order (ISSUE-1338-a's same-page-first ladder) with the
/// maintainer's rule of 2026-10-12, checked by an independent oracle over the candidates each
/// basis records (`common/sheet_rule.rs`): smallest complete fit, at most one paper step and only
/// with an enlargement, then fill. Every hole and turned-shoulder outcome the first plan placed
/// survives the choice. A plan with no valid complete candidate keeps its first sheet and scale.
/// Sheets chosen by the A4-first paper policy say so in their basis ("sheet choice: …").
#[test]
fn sheet_selection_is_bounded_and_keeps_every_outcome() {
    // Keep the exhausted-ladder fixture explicit; auto tables now resolve these losses.
    let mut style = Spec {
        hole_table: draftwright::hole_table::Setting::Never,
        ..Spec::default()
    };
    // This conservation oracle has the full ISO paper ladder available. Capped
    // fallbacks may retain reported required drops; sheet_policy tests those.
    style.sheet.preferred_max_size = Some("A0".into());
    let initial: Vec<_> = planned()
        .iter()
        .map(|(n, a, _)| (n.clone(), a.clone(), model::plan(a, &style).unwrap()))
        .collect();
    let recovered: Vec<_> = initial
        .iter()
        .map(|(n, a, _)| (n.clone(), model::plan_recovered(a, &style).unwrap()))
        .collect();
    for ((name, analysis, first), (_, after)) in initial.iter().zip(&recovered) {
        let kept = hole_and_shoulder_outcomes(analysis, after);
        assert!(
            hole_and_shoulder_outcomes(analysis, first).is_subset(&kept),
            "{name}: {:?}",
            hole_and_shoulder_outcomes(analysis, first).difference(&kept)
        );
        if after.sheet.basis.starts_with("smallest fit ") {
            sheet_rule::assert_follows(
                name,
                after,
                (first.sheet.size.as_str(), first.sheet.scale.as_str()),
            );
        } else if after.sheet.basis.contains("; sheet choice: ") {
            // The A4-first paper policy (`sheet_policy`) chose the sheet; the outcome check
            // above is its invariant here, and its own tests check the candidate order.
        } else {
            assert!(codes(after).contains("layout_recovery_exhausted"), "{name}");
            assert_eq!(
                (&after.sheet.size, &after.sheet.scale),
                (&first.sheet.size, &first.sheet.scale),
                "{name}"
            );
        }
    }
}

/// Selection is honest: either a valid complete candidate is justified by measured fill,
/// or the first drawing and its hard findings survive with an exhausted-search explanation.
#[test]
fn fill_selection_preserves_hard_findings_when_no_complete_candidate_exists() {
    for ((name, analysis, first), (_, after)) in planned().iter().zip(recovered()) {
        let hard: BTreeSet<_> = after
            .problems
            .iter()
            .filter(|p| HARD_LAYOUT.contains(&p.code.as_str()) && p.severity != "info")
            .map(|p| p.code.as_str())
            .collect();
        if codes(after).contains("layout_recovery_exhausted") {
            assert_eq!(after.sheet.size, first.sheet.size, "{name}");
            assert_eq!(
                after.sheet.scale_factor,
                first.sheet.scale_factor.max(0.1),
                "{name}"
            );
            let said = after
                .problems
                .iter()
                .find(|p| p.code == "layout_recovery_exhausted")
                .unwrap();
            let illegible = after.sheet.scale_factor
                < draftwright::sheet_fit::min_legible_scale(analysis)
                || draftwright::sheet_fit::illegible_steps(analysis, after) > 0;
            assert_eq!(said.severity, if illegible { "warning" } else { "info" });
            assert!(said.message.contains("Tried "), "{name}");
            let before: BTreeSet<_> = first
                .problems
                .iter()
                .filter(|p| HARD_LAYOUT.contains(&p.code.as_str()) && p.severity != "info")
                .map(|p| p.code.as_str())
                .collect();
            if first.sheet.scale_factor >= 0.1 {
                assert_eq!(hard, before, "{name}");
            } // Below 1:10 the existing legibility clamp redraws the first estimate.
        } else {
            assert!(hard.is_empty(), "{name}: {hard:?}");
            assert!(after.sheet.basis.contains("fill"), "{name}");
        }
    }
}

/// Recovery must judge transported centre strokes before calling an automatic
/// enlargement clean. GRM-05's A3/5:1 cross formerly exceeded the drawable inset.
#[test]
fn grm05_automatic_recovery_keeps_rendered_centre_ink_inside_the_page() {
    let Some(dir) = corpus::dir() else { return };
    let (part, mut a) = model::analyse(&dir.join("gramel/GRM-05_depth_lock_bolt.step")).unwrap();
    let mut recognition = String::new();
    flate2::read::GzDecoder::new(
        std::fs::File::open(fixture("recognition/sheets/grm05.json.gz")).unwrap(),
    )
    .read_to_string(&mut recognition)
    .unwrap();
    model::recognise(&part, &mut a, &recognition).unwrap();
    let spec = Spec::default();
    let first = model::plan(&a, &spec).unwrap();
    let p = model::recover(&a, &spec, first).unwrap();
    assert!(
        !p.problems.iter().any(|q| q.code == "annotation_off_sheet"),
        "{:?}",
        p.problems
    );
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    let margin = draftwright::layout::margin();
    let drawable = [
        margin,
        margin,
        drawing.page[0] - margin,
        drawing.page[1] - margin,
    ];
    assert!(
        drawing
            .sheet
            .items
            .iter()
            .any(|i| i.name == "ink:v:plan:f:turned_step:1")
    );
    for item in drawing
        .sheet
        .items
        .iter()
        .filter(|i| i.kind == draftwright::output::Kind::Centreline)
    {
        assert!(
            inside(item.ink, drawable),
            "{}: {:?} outside {drawable:?}",
            item.name,
            item.ink
        );
    }
}

#[test]
fn native_recovery_judges_centre_ink_after_deferred_finishing() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = model::analyse_native(&dir.join("mfcadpp_holdout/388.step")).unwrap();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    // Its finished A2 drawing fits. An unfinished axis endpoint must not
    // force A1 before the enclosing paper trial finishes the view positions.
    assert_eq!(p.sheet.size, "A2");
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    let m = draftwright::layout::margin();
    let drawable = [m, m, drawing.page[0] - m, drawing.page[1] - m];
    for item in drawing
        .sheet
        .items
        .iter()
        .filter(|i| i.kind == draftwright::output::Kind::Centreline)
    {
        assert!(inside(item.ink, drawable), "{}: {:?}", item.name, item.ink);
    }
}

#[test]
fn envelope_wide_bands_and_extent_fits_reach_the_ink() {
    let (part, a) = analysed("plate");
    let font = font();
    let ids = [
        "d:envelope/width.length",
        "d:envelope/depth.length",
        "d:envelope/height.length",
    ];
    let wide = spec(json!({"envelope_tolerance": "±0.05"}));
    let plan = model::plan(&a, &wide).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    for id in ids {
        let dim = plan.dimensions.iter().find(|d| d.id == id).unwrap();
        assert!(dim.label.ends_with(" ±0.05"), "{}", dim.label);
        let anno = drawing.annotations.iter().find(|a| a.name == id).unwrap();
        assert!(
            anno.runs
                .iter()
                .any(|&i| drawing.text[i].text.ends_with(" ±0.05"))
        );
    }
    for id in ids {
        let fitted =
            spec(json!({"dimensions": {id: {"fit": "h7"}}, "sheet": {"fit_limits": false}}));
        let plan = model::plan(&a, &fitted).unwrap();
        for d in plan
            .dimensions
            .iter()
            .filter(|d| ids.contains(&d.id.as_str()))
        {
            assert_eq!(d.label.ends_with(" h7"), d.id == id, "{} {}", d.id, d.label);
        }
        assert!(
            model::render(&part, &a, &plan, &font)
                .unwrap()
                .svg()
                .contains("h7")
        );
    }
    let override_one = spec(
        json!({"envelope_tolerance": "±0.05", "dimensions": {ids[0]: {"tolerance": "+0.2 -0.1"}}}),
    );
    let plan = model::plan(&a, &override_one).unwrap();
    assert!(
        plan.dimensions
            .iter()
            .find(|d| d.id == ids[0])
            .unwrap()
            .label
            .ends_with(" +0.2 -0.1")
    );
}
