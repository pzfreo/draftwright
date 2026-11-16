//! The port's view planning against draftwright's sheets (`tests/fixtures/sheets/`), fact by
//! fact: the sheet, the scale, the view set, and for each view both draw, its scale and where it
//! puts the centre of the part's box. Every difference must be listed, with a verdict and the
//! reason, in `tests/fixtures/known_views.json`; a listed difference that has gone away must be
//! taken off the list.
//!
//! The parts are read from the corpus (`DRAFTWRIGHT_CORPUS`, by default
//! `~/repos/quiddity/tests/corpus`), with their features from Python quiddity's recognition
//! (`tests/fixtures/recognition/corpus/`), as draftwright reads them.
//!
//! Tolerances: a view's centre within 0.01 mm on the page, its scale within 1e-6 relative. Both
//! sides centre each view on the part's tight box, and the boxes agree to well under that (the
//! test checks it); the facts are rounded to 1e-6.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::BTreeSet;

use draftwright::model::{self, Spec};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    part: String,
    /// `sheet`, `scale`, `views`, `centre:<view>` or `scale:<view>`.
    fact: String,
    /// `rust-correct`, `equivalent` or `undetermined`.
    verdict: String,
    reason: String,
}

#[test]
fn view_planning_matches_draftwright_or_is_known() {
    let Some(dir) = corpus::dir() else {
        eprintln!(
            "SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS \
             (DRAFTWRIGHT_CORPUS_REQUIRED=1 makes this a failure)"
        );
        return;
    };
    let known: Vec<Known> = serde_json::from_str(
        &std::fs::read_to_string(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/known_views.json"),
        )
        .unwrap(),
    )
    .unwrap();
    for k in &known {
        assert!(
            ["rust-correct", "equivalent", "undetermined"].contains(&k.verdict.as_str())
                && !k.reason.is_empty(),
            "{k:?}"
        );
    }
    let listed: BTreeSet<(String, String)> = known
        .iter()
        .map(|k| (k.part.clone(), k.fact.clone()))
        .collect();
    assert_eq!(listed.len(), known.len(), "a difference is listed twice");

    let mut differ: BTreeSet<(String, String)> = BTreeSet::new();
    let mut agree = 0;
    // Compare the captured metric ISO paper/scale/view ledger on its original units.
    for name in facts::names() {
        let f = facts::load(&name);
        let (_, analysis) = corpus::analyse(&dir, &f.binding.file, &name);
        assert_eq!(analysis.binding.source_sha256, f.binding.sha256, "{name}");
        let plan = model::plan(
            &analysis,
            &serde_json::from_value(
                serde_json::json!({"units":"mm","sheet":{"preferred_max_size":"A0"}}),
            )
            .unwrap(),
        )
        .unwrap();
        let mut check = |fact: String, same: bool| {
            if same {
                agree += 1;
            } else {
                differ.insert((name.clone(), fact));
            }
        };
        check("sheet".into(), f.sheet.format == plan.sheet.size);
        check("scale".into(), f.sheet.scale == plan.sheet.scale);
        let theirs: Vec<&str> = f.views.iter().map(|v| v.name.as_str()).collect();
        let ours: Vec<&str> = plan
            .views
            .iter()
            .map(|v| v.id.trim_start_matches("v:"))
            .collect();
        check("views".into(), theirs == ours);

        let [lo, hi] = analysis.envelope;
        let centre = [0, 1, 2].map(|k| 0.5 * (lo[k] + hi[k]));
        for v in &plan.views {
            let id = v.id.trim_start_matches("v:");
            let Some(t) = f.views.iter().find(|t| t.name == id) else {
                continue;
            };
            // Both centre the view on the part's box.
            let look_at = t.look_at.unwrap();
            let off = (0..3)
                .map(|k| (look_at[k] - centre[k]).abs())
                .fold(0.0, f64::max);
            assert!(
                v.detail.is_some() || off < 1e-3,
                "{name} {id}: the boxes' centres differ by {off} mm"
            );
            let at = t.page(centre).unwrap();
            // Compare one physical point for both transforms. Detail cameras centre on
            // their own crops, whose extents can differ from Python's supported strip.
            let mut ours = v.at;
            if let Some(d) = &v.detail {
                let view = haecceity::hlr::View::new(v.direction, v.up).unwrap();
                let crop_centre = [0, 1, 2].map(|k| (d.bounds[0][k] + d.bounds[1][k]) / 2.0);
                let c = view.map(crop_centre);
                let p = view.map(centre);
                ours = [0, 1].map(|k| v.at[k] + v.scale * (p[k] - c[k]));
            }
            check(
                format!("centre:{id}"),
                (ours[0] - at[0]).hypot(ours[1] - at[1]) < 0.01,
            );
            let scale = t.scale.unwrap();
            check(
                format!("scale:{id}"),
                (v.scale - scale).abs() <= 1e-6 * scale,
            );
        }
    }
    let unlisted: Vec<_> = differ.difference(&listed).collect();
    let stale: Vec<_> = listed.difference(&differ).collect();
    eprintln!(
        "{agree} facts agree, {} differ ({} known)",
        differ.len(),
        known.len()
    );
    assert!(
        unlisted.is_empty() && stale.is_empty(),
        "differences not in known_views.json: {unlisted:#?}\nlisted but no longer differing: {stale:#?}"
    );
}

/// Third-angle packing keeps the projection alignment: the plan above the front sharing its x,
/// the side to its right sharing its y, and the isometric in free space; the reserved areas are
/// disjoint and inside the border, clear of the title block.
#[test]
fn views_keep_projection_alignment_and_their_areas_apart() {
    for name in ["turned.step", "prismatic.step"] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts")
            .join(name);
        let (_, analysis) = model::analyse(&path).unwrap();
        let plan = model::plan(&analysis, &Spec::default()).unwrap();
        let view = |id: &str| plan.views.iter().find(|v| v.id == id).unwrap();
        let (front, top, side, iso) = (
            view("v:front"),
            view("v:plan"),
            view("v:side"),
            view("v:iso"),
        );
        assert_eq!(top.at[0], front.at[0], "{name}");
        assert!(top.at[1] > front.at[1], "{name}");
        assert_eq!(side.at[1], front.at[1], "{name}");
        assert!(side.at[0] > front.at[0], "{name}");
        let [w, h] = plan.sheet.page;
        let mut areas: Vec<[f64; 4]> = plan.views.iter().map(|v| v.area).collect();
        areas.push(plan.sheet.title_block);
        for (i, a) in areas.iter().enumerate() {
            assert!(a[0] >= 10.0 - 0.5 && a[1] >= 10.0 - 0.5, "{name}: {a:?}");
            assert!(
                a[2] <= w - 10.0 + 0.5 && a[3] <= h - 10.0 + 0.5,
                "{name}: {a:?}"
            );
            for b in &areas[i + 1..] {
                let apart = a[2] <= b[0] || b[2] <= a[0] || a[3] <= b[1] || b[3] <= a[1];
                assert!(apart, "{name}: {a:?} meets {b:?}");
            }
        }
        assert!(iso.scale > 0.0 && plan.sheet.scale_factor > 0.0);
    }
}

/// Over the sampled corpus parts, no view's reserved area (its box and annotation bands, the
/// isometric's rectangle, a section's block) overlaps another's or the title block (drawing
/// quality 3, docs/quality-2026-10-09.md); areas may touch.
#[test]
fn corpus_views_and_their_bands_keep_apart_and_off_the_title_block() {
    let Some(dir) = corpus::dir() else {
        eprintln!(
            "SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS \
             (DRAFTWRIGHT_CORPUS_REQUIRED=1 makes this a failure)"
        );
        return;
    };
    let meets =
        |a: &[f64; 4], b: &[f64; 4]| a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
    let mut clashes = Vec::new();
    for name in facts::names() {
        let f = facts::load(&name);
        let (_, analysis) = corpus::analyse(&dir, &f.binding.file, &name);
        let plan = model::plan(&analysis, &Spec::default()).unwrap();
        let mut areas: Vec<(&str, [f64; 4])> =
            plan.views.iter().map(|v| (v.id.as_str(), v.area)).collect();
        areas.push(("title block", plan.sheet.title_block));
        for (i, (a, ra)) in areas.iter().enumerate() {
            for (b, rb) in &areas[i + 1..] {
                if meets(ra, rb) {
                    clashes.push(format!("{name}: {a} {ra:?} meets {b} {rb:?}"));
                }
            }
        }
    }
    assert!(clashes.is_empty(), "{clashes:#?}");
}

/// The sheet's basis says what a fallback is, not the rule that found nothing: a part too big
/// for every standard scale takes a computed one, and one too big for every sheet at a pinned
/// scale overflows; each also has its problem.
#[test]
fn a_fallback_scale_or_sheet_says_so_in_its_basis() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/prismatic.step");
    let (_, mut analysis) = model::analyse(&path).unwrap();
    // 200 km on a side: no sheet holds it even at 1:10000.
    analysis.envelope = [[0.0; 3], [2e8; 3]];
    let planned = |sheet: serde_json::Value| {
        let spec: Spec = serde_json::from_value(serde_json::json!({ "sheet": sheet })).unwrap();
        model::plan(&analysis, &spec).unwrap()
    };
    let codes = |plan: &model::Plan| -> Vec<String> {
        plan.problems.iter().map(|p| p.code.clone()).collect()
    };
    let plan = planned(serde_json::json!({}));
    assert!(codes(&plan).contains(&"scale_fallback_applied".into()));
    assert!(
        plan.sheet.basis.contains("not a standard scale")
            && !plan.sheet.basis.contains("first sheet and scale"),
        "{}",
        plan.sheet.basis
    );
    let plan = planned(serde_json::json!({ "size": "A3" }));
    assert!(
        plan.sheet.basis.contains("no standard scale fits") && plan.sheet.basis.contains("A3"),
        "{}",
        plan.sheet.basis
    );
    let plan = planned(serde_json::json!({ "scale": "1:1" }));
    assert!(codes(&plan).contains(&"views_do_not_fit".into()));
    assert!(
        plan.sheet.basis.contains("no sheet holds the views at 1:1")
            && plan.sheet.basis.contains("do not fit"),
        "{}",
        plan.sheet.basis
    );
    let plan = planned(serde_json::json!({ "scale": "1:1", "size": "A4" }));
    assert!(codes(&plan).contains(&"page_fit_uncertain".into()));
    assert!(
        plan.sheet.basis.contains("may not fit"),
        "{}",
        plan.sheet.basis
    );
}

/// An isometric projection with no edges leaves nothing to fit the isometric by: reported, not
/// left as a box of zero size.
#[test]
fn an_empty_isometric_projection_is_reported() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/prismatic.step");
    let (_, mut analysis) = model::analyse(&path).unwrap();
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    assert!(
        plan.problems
            .iter()
            .all(|p| p.code != "iso_projection_empty")
    );
    analysis.iso_bounds = [0.0; 4];
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    let p = plan
        .problems
        .iter()
        .find(|p| p.code == "iso_projection_empty")
        .expect("reported");
    assert_eq!(p.subjects, ["v:iso"]);
    let ops = serde_json::to_value(&p.fix).unwrap();
    assert_eq!(
        ops,
        serde_json::json!([{ "op": "set", "path": "views/iso/omit", "value": true }])
    );
    let fixed = model::edit(&Spec::default(), &ops).unwrap();
    let after = model::plan(&analysis, &fixed).unwrap();
    assert!(after.problems.iter().all(|q| q.id != p.id));
    assert!(after.views.iter().all(|v| v.id != "v:iso"));
    let spec: Spec =
        serde_json::from_value(serde_json::json!({ "views": { "iso": { "omit": true } } }))
            .unwrap();
    let plan = model::plan(&analysis, &spec).unwrap();
    assert!(
        plan.problems
            .iter()
            .all(|p| p.code != "iso_projection_empty")
    );
}
