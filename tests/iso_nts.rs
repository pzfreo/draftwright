//! The "ISO VIEW (NTS)" caption (draftwright's `place_iso_nts_note`, ADR2-14): drawn exactly
//! when the isometric is drawn off the sheet's scale, carried as sheet furniture
//! (`note_iso_nts`), and placed clear of the views, the annotations, the tables and the title
//! block.

use std::path::Path;

#[path = "common/corpus.rs"]
mod corpus;

use draftwright::model::{self, NTS_CAPTION, NTS_CAPTION_ID, Plan, nts_caption_at};
use draftwright::output::{Drawing, Drawn, Kind};

fn draw(name: &str) -> (Plan, Drawing) {
    draw_spec(name, &model::Spec::default())
}

fn draw_spec(name: &str, spec: &model::Spec) -> (Plan, Drawing) {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, analysis) =
        model::analyse_native(&root.join("tests/fixtures/parts").join(name)).unwrap();
    let plan = model::plan_recovered(&analysis, spec).unwrap();
    let font =
        draftwright::text::Font::open(&root.join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render_with(
        &part,
        &analysis,
        &plan,
        &font,
        &mut model::Projections::default(),
    )
    .unwrap();
    (plan, drawing)
}

fn iso_ratio(plan: &Plan) -> f64 {
    let iso = plan.views.iter().find(|v| v.id == "v:iso").unwrap();
    iso.scale / plan.sheet.scale_factor
}

fn caption(drawing: &Drawing) -> Option<&Drawn> {
    drawing
        .sheet
        .items
        .iter()
        .find(|i| i.name == NTS_CAPTION_ID)
}

fn overlaps(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]
}

/// Whether segment *p*–*q* passes through box *b* (Liang–Barsky clipping).
fn crosses(b: [f64; 4], [p, q]: [[f64; 2]; 2]) -> bool {
    let d = [q[0] - p[0], q[1] - p[1]];
    let (mut t0, mut t1) = (0.0f64, 1.0f64);
    for (dp, lo, hi) in [
        (d[0], b[0] - p[0], b[2] - p[0]),
        (d[1], b[1] - p[1], b[3] - p[1]),
    ] {
        if dp.abs() < 1e-12 {
            if lo > 0.0 || hi < 0.0 {
                return false;
            }
            continue;
        }
        let (a, c) = ((lo / dp).min(hi / dp), (lo / dp).max(hi / dp));
        t0 = t0.max(a);
        t1 = t1.min(c);
    }
    t0 < t1
}

#[test]
fn an_isometric_drawn_off_the_sheet_scale_is_captioned() {
    // prismatic (reduced): turned.step's isometric is drawn at the sheet scale since the 3 mm
    // frame gutter (2026-10-25).
    for name in ["plate.step", "prismatic.step", "boss_circle.step"] {
        let (plan, drawing) = draw(name);
        assert!(
            (iso_ratio(&plan) - 1.0).abs() > 1e-6,
            "{name}: fixture no longer draws its isometric off the sheet scale"
        );
        let c = caption(&drawing).unwrap_or_else(|| panic!("{name}: no NTS caption"));
        assert_eq!(c.kind, Kind::Caption);
        let planned = plan
            .captions
            .iter()
            .find(|c| c.id == NTS_CAPTION_ID)
            .unwrap();
        assert_eq!(planned.text, NTS_CAPTION);
        for (a, b) in planned.ink.iter().zip(c.ink) {
            assert!(
                (a - b).abs() < 1e-8,
                "{name}: planned caption differs from rendered ink"
            );
        }
        let item = draftwright::items::items(&plan)
            .into_iter()
            .find(|i| i.id == NTS_CAPTION_ID)
            .unwrap();
        assert_eq!(item.kind, "caption");
        assert_eq!(item.label.as_deref(), Some(NTS_CAPTION));
        assert_eq!(
            drawing
                .text
                .iter()
                .filter(|t| t.text == NTS_CAPTION)
                .count(),
            1,
            "{name}: the caption is one searchable run"
        );
        let run = drawing.text.iter().find(|t| t.text == NTS_CAPTION).unwrap();
        assert_eq!(run.size, 3.0, "{name}: draftwright's font size");
    }
}

#[test]
fn an_isometric_at_the_sheet_scale_has_no_caption() {
    // An isometric fitted at the sheet scale is uncaptioned. mounting_plate on its automatic
    // sheet: issue_1247's A3 normalization moved to 1:1 with the 3 mm frame gutter
    // (2026-10-25), where its isometric is enlarged and captioned.
    let spec = model::Spec::default();
    let (plan, drawing) = draw_spec("mounting_plate.step", &spec);
    assert_eq!(iso_ratio(&plan), 1.0);
    assert!(caption(&drawing).is_none());
    assert!(!drawing.text.iter().any(|t| t.text == NTS_CAPTION));
}

#[test]
fn the_caption_overlaps_nothing() {
    for name in [
        "plate.step",
        "turned.step",
        "boss_circle.step",
        "prismatic.step",
        "two_grids.step",
        "mounting_plate.step",
        "scattered_holes.step",
        "editor_pmi.step",
    ] {
        let (plan, drawing) = draw(name);
        if (iso_ratio(&plan) - 1.0).abs() < 1e-6 {
            assert!(caption(&drawing).is_none());
            continue;
        }
        let c = caption(&drawing).unwrap_or_else(|| panic!("{name}: no NTS caption to check"));
        let b = c.ink;
        let drawable = drawing.sheet.drawable.unwrap();
        assert!(
            b[0] >= drawable[0]
                && b[1] >= drawable[1]
                && b[2] <= drawable[2]
                && b[3] <= drawable[3],
            "{name}: caption off the drawable area"
        );
        for v in &drawing.sheet.views {
            assert!(!overlaps(b, v.bbox), "{name}: caption over {}", v.name);
        }
        for d in drawing
            .sheet
            .items
            .iter()
            .filter(|d| d.name != NTS_CAPTION_ID)
        {
            if let Some(l) = d.label {
                assert!(
                    !overlaps(b, l),
                    "{name}: caption over the label of {}",
                    d.name
                );
            }
            for line in &d.lines {
                assert!(
                    !crosses(b, *line),
                    "{name}: a line of {} crosses the caption",
                    d.name
                );
            }
            if !matches!(d.kind, Kind::Dimension | Kind::Centreline) {
                assert!(!overlaps(b, d.ink), "{name}: caption over {}", d.name);
            }
        }
    }
}

const PAGE: [f64; 2] = [297.0, 210.0];
const ISO: [f64; 4] = [150.0, 80.0, 250.0, 160.0];
const SIZE: [f64; 2] = [30.0, 2.5];

#[test]
fn a_clear_natural_position_is_kept() {
    // Two font heights below the iso, on its projection centre.
    assert_eq!(nts_caption_at(PAGE, ISO, 200.0, SIZE, &[]), [200.0, 74.0]);
}

#[test]
fn a_blocked_natural_position_moves_further_below() {
    let blocker = [195.0, 73.0, 205.0, 75.0];
    let at = nts_caption_at(PAGE, ISO, 200.0, SIZE, &[blocker]);
    assert_eq!(at, [200.0, 80.0 - 9.0 - 2.5]);
}

#[test]
fn a_flush_neighbour_within_the_text_padding_displaces_it() {
    // 1 mm under the natural box: clear of it, but inside the 2 mm padding.
    let flush = [190.0, 70.0, 210.0, 71.75];
    assert_ne!(
        nts_caption_at(PAGE, ISO, 200.0, SIZE, &[flush]),
        [200.0, 74.0]
    );
}

#[test]
fn below_beats_beside_and_beside_beats_above() {
    let under = [152.0, 20.0, 248.0, 79.0];
    let at = nts_caption_at(PAGE, ISO, 200.0, SIZE, &[under]);
    assert_eq!(at, [150.0 - 3.0 - 15.0, 74.0], "left of the iso first");
    let left = [20.0, 20.0, 149.0, 79.0];
    let at = nts_caption_at(PAGE, ISO, 200.0, SIZE, &[under, left]);
    assert_eq!(at, [250.0 + 3.0 + 15.0, 74.0], "then right of it");
}

#[test]
fn a_sideways_position_off_the_page_is_refused_and_above_is_last() {
    let iso = [100.0, 80.0, 280.0, 160.0];
    let under = [102.0, 20.0, 278.0, 79.0];
    let at = nts_caption_at(PAGE, iso, 190.0, SIZE, &[under]);
    // Left (x 82) is free and on the page; block it, and right (x 298) is off the page.
    assert_eq!(at, [100.0 - 18.0, 74.0]);
    let left = [20.0, 20.0, 99.0, 79.0];
    assert_eq!(
        nts_caption_at(PAGE, iso, 190.0, SIZE, &[under, left]),
        [190.0, 166.0]
    );
}

#[test]
fn the_caption_is_kept_where_nothing_is_clear() {
    let everything = [0.0, 0.0, PAGE[0], PAGE[1]];
    assert_eq!(
        nts_caption_at(PAGE, ISO, 200.0, SIZE, &[everything]),
        [200.0, 74.0]
    );
}

#[test]
fn an_authored_iso_scale_is_exact_and_its_actual_scale_controls_the_caption() {
    for factor in [0.7, 1.0, 1.03, 1.3] {
        let spec = model::Spec {
            iso_scale_factor: Some(factor),
            ..Default::default()
        };
        let (plan, drawing) = draw_spec("plate.step", &spec);
        assert!((iso_ratio(&plan) - factor).abs() < 1e-12);
        assert_eq!(caption(&drawing).is_some(), factor != 1.0);
        assert_eq!(!plan.captions.is_empty(), factor != 1.0);
        let replay: Plan = serde_json::from_slice(&serde_json::to_vec(&plan).unwrap()).unwrap();
        assert_eq!(replay.captions, plan.captions);
    }
}

#[test]
fn an_omitted_iso_has_no_caption_even_with_an_authored_scale() {
    let spec = serde_json::from_value(serde_json::json!({
        "iso_scale_factor": 0.7, "views": {"iso": {"omit": true}}
    }))
    .unwrap();
    let (plan, drawing) = draw_spec("plate.step", &spec);
    assert!(plan.captions.is_empty());
    assert!(caption(&drawing).is_none());
}

#[test]
fn an_iso_scale_override_must_be_finite_and_positive() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (_, analysis) =
        model::analyse_native(&root.join("tests/fixtures/parts/plate.step")).unwrap();
    for factor in [0.0, -1.0, f64::NAN, f64::INFINITY] {
        let spec = model::Spec {
            iso_scale_factor: Some(factor),
            ..Default::default()
        };
        assert!(
            model::plan(&analysis, &spec)
                .unwrap_err()
                .message
                .contains("iso_scale_factor")
        );
    }
}

#[test]
fn an_enlarged_detail_prevents_iso_growth_but_cannot_prevent_a_required_shrink() {
    use draftwright::layout::iso_factor_with_detail;
    let bounds = [-10.0, -8.0, 10.0, 8.0];
    let region = [0.0, 0.0, 100.0, 100.0];
    assert_eq!(
        iso_factor_with_detail(region, [50.0, 50.0], bounds, false),
        1.3
    );
    assert_eq!(
        iso_factor_with_detail(region, [50.0, 50.0], bounds, true),
        1.0
    );
    let small = [0.0, 0.0, 10.0, 10.0];
    assert!(iso_factor_with_detail(small, [5.0, 5.0], bounds, true) < 1.0);
}

#[test]
fn late_tables_and_notes_avoid_the_reserved_caption() {
    let spec = serde_json::from_value(serde_json::json!({
        "tables": [{"id": "t:1", "rows": [["REV", "DESCRIPTION"], ["A", "FIRST ISSUE"]]}],
        "notes": [{"id": "n:1", "text": "REMOVE ALL BURRS"}]
    }))
    .unwrap();
    let (plan, drawing) = draw_spec("plate.step", &spec);
    let c = caption(&drawing).expect("fixture must draw an NTS iso");
    assert_eq!(plan.captions.len(), 1);
    assert!(plan.tables.iter().all(|t| t.placed.is_some()));
    assert!(plan.notes.iter().all(|n| n.placed.is_some()));
    for d in drawing
        .sheet
        .items
        .iter()
        .filter(|d| matches!(d.kind, Kind::Table | Kind::Notes))
    {
        assert!(!overlaps(c.ink, d.ink), "caption over {}", d.name);
    }
}

#[test]
fn measured_refits_preserve_the_same_strict_five_percent_no_op_band() {
    use draftwright::layout::keep_sheet_scale;
    assert_eq!(keep_sheet_scale(1.049, 1.0), 1.0);
    assert_eq!(keep_sheet_scale(0.951, 1.0), 1.0);
    assert_eq!(keep_sheet_scale(1.051, 1.0), 1.051);
    assert_eq!(keep_sheet_scale(0.949, 1.0), 0.949);
}

#[test]
fn an_infeasible_authored_iso_scale_is_refused_instead_of_relaxed() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (_, analysis) =
        model::analyse_native(&root.join("tests/fixtures/parts/plate.step")).unwrap();
    let spec = serde_json::from_value(serde_json::json!({
        "iso_scale_factor": 100.0, "sheet": {"size": "A4", "scale": "1:1"}
    }))
    .unwrap();
    assert!(
        model::plan_recovered(&analysis, &spec)
            .unwrap_err()
            .message
            .contains("authored iso scale")
    );
}

#[test]
fn a_caption_follows_its_iso_to_another_sheet_and_clears_that_sheets_tables() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, analysis) =
        model::analyse_native(&root.join("tests/fixtures/parts/plate.step")).unwrap();
    let spec = serde_json::from_value(serde_json::json!({
        "iso_scale_factor": 0.7, "sheets": {"max": 2}, "views": {"iso": {"sheet": 2}},
        "tables": [{"id": "t:1", "sheet": 2, "rows": [["REV"], ["A"]]}]
    }))
    .unwrap();
    let plan = model::plan_recovered(&analysis, &spec).unwrap();
    let iso = plan.views.iter().find(|v| v.id == "v:iso").unwrap();
    assert_eq!(iso.sheet, 2);
    assert!((iso.scale / plan.sheet.scale_factor - 0.7).abs() < 1e-12);
    assert_eq!(plan.captions.len(), 1);
    assert_eq!(plan.captions[0].sheet, 2);
    let font =
        draftwright::text::Font::open(&root.join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawings = model::render_sheets(&part, &analysis, &plan, &font).unwrap();
    assert!(caption(&drawings[0]).is_none());
    let c = caption(&drawings[1]).expect("sheet 2 must carry the disclaimer");
    let t = drawings[1]
        .sheet
        .items
        .iter()
        .find(|d| d.name == "t:1")
        .unwrap();
    assert!(!overlaps(c.ink, t.ink));
    for d in drawings[1]
        .sheet
        .items
        .iter()
        .filter(|d| d.name != NTS_CAPTION_ID)
    {
        assert!(!overlaps(c.ink, d.ink), "sheet 2 caption over {}", d.name);
    }
}

#[test]
fn session_edits_remove_and_restore_the_caption_in_items_and_furniture() {
    use draftwright::items::Op;
    use draftwright::session::Session;
    use serde_json::json;
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, analysis) =
        model::analyse_native(&root.join("tests/fixtures/parts/plate.step")).unwrap();
    let spec = serde_json::from_value(json!({
        "sheet": {"size": "A4", "scale": "1:1"}, "iso_scale_factor": 0.7
    }))
    .unwrap();
    let mut session = Session::new(part, analysis, spec).unwrap();
    assert!(session.item(NTS_CAPTION_ID).is_ok());
    let delta = session
        .apply(&[Op::set("iso_scale_factor", json!(1.0))], true)
        .unwrap();
    assert!(delta.removed.contains(&NTS_CAPTION_ID.to_string()));
    assert!(session.item(NTS_CAPTION_ID).is_err());
    assert!(caption(session.drawing()).is_none());
    let fragments = session.svg_fragments(Some(&delta));
    assert!(fragments.contains_key("furniture"));
    assert!(!fragments["furniture"].contains(NTS_CAPTION));
    let delta = session
        .apply(&[Op::set("iso_scale_factor", json!(0.7))], true)
        .unwrap();
    assert!(delta.added.iter().any(|i| i.item.id == NTS_CAPTION_ID));
    assert!(caption(session.drawing()).is_some());
    assert!(session.svg_fragments(Some(&delta))["furniture"].contains(NTS_CAPTION));
}

/// The saved sm-hanger specs predate quiddity-rust v0.2.2 (#120), which makes the R7 blend
/// `f:blend:10` a fillet, counted with the others on one `14× R7` callout, `d:f:fillet:9` (the
/// `16× R7` callout was `d:f:fillet:3`). In v0.2.3 (#127), concave fillets move this
/// same convex presenter to `d:f:fillet:16`. Their two radius entries become that one.
fn hanger_renumbered(mut spec: model::Spec) -> model::Spec {
    let fillet = spec.dimensions.remove("d:f:fillet:f14/fillet.radius");
    if let Some(entry) = fillet {
        spec.dimensions
            .insert("d:f:fillet:f53/fillet.radius".into(), entry);
    }
    spec
}

fn saved_spec(name: &str) -> model::Spec {
    let input = match name {
        "hanger-first" => include_str!("fixtures/layout/nts-hanger.spec.json"),
        "hanger-review" => include_str!("fixtures/layout/review-hanger.spec.json"),
        "mounting-plate" => include_str!("fixtures/layout/nts-mounting-plate.spec.json"),
        _ => panic!("unknown saved regression input {name}"),
    };
    serde_json::from_str(input).unwrap()
}

#[test]
fn hanger_authored_dimensions_leave_the_nts_caption_clear_of_ink() {
    let Some(corpus) = corpus::dir() else { return };
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, analysis) =
        model::analyse_native(&corpus.join("ttt_inputs/sm-hanger.step")).unwrap();
    let font =
        draftwright::text::Font::open(&root.join("fonts").join(draftwright::dims::FONT)).unwrap();
    for prefix in ["hanger-first", "hanger-review"] {
        let spec = hanger_renumbered(saved_spec(prefix));
        let plan = model::plan_recovered(&analysis, &spec).unwrap();
        assert_eq!(plan.sheet.size, "A4");
        assert_eq!(plan.sheet.scale, "1:2");
        let drawing = model::render_with(
            &part,
            &analysis,
            &plan,
            &font,
            &mut model::Projections::default(),
        )
        .unwrap();
        let b = caption(&drawing).expect("hanger needs its NTS caption").ink;
        for v in &drawing.sheet.views {
            assert!(!overlaps(b, v.bbox), "{prefix}: caption over {}", v.name);
        }
        for d in drawing
            .sheet
            .items
            .iter()
            .filter(|d| d.name != NTS_CAPTION_ID)
        {
            if let Some(label) = d.label {
                assert!(
                    !overlaps(b, label),
                    "{prefix}: caption over label {}",
                    d.name
                );
            }
            for line in &d.lines {
                assert!(
                    !crosses(b, *line),
                    "{prefix}: caption crosses line {}",
                    d.name
                );
            }
            if !matches!(d.kind, Kind::Dimension | Kind::Centreline) {
                assert!(!overlaps(b, d.ink), "{prefix}: caption over {}", d.name);
            }
        }
    }
}

#[test]
fn pinned_refits_snap_the_isometric_to_sheet_scale() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (_, analysis) =
        model::analyse_native(&root.join("tests/fixtures/parts/mounting_plate.step")).unwrap();
    let plan = model::plan_recovered(&analysis, &saved_spec("mounting-plate")).unwrap();
    assert_eq!(iso_ratio(&plan), 1.0);
    assert!(plan.captions.is_empty());
}

#[test]
fn recovery_with_an_iso_factor_propagates_other_planning_errors() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (_, analysis) =
        model::analyse_native(&root.join("tests/fixtures/parts/plate.step")).unwrap();
    let first = model::plan(&analysis, &model::Spec::default()).unwrap();
    let invalid = model::Spec {
        iso_scale_factor: Some(-1.0),
        ..Default::default()
    };
    let expected = model::plan(&analysis, &invalid).unwrap_err();
    assert_eq!(
        model::recover(&analysis, &invalid, first).unwrap_err(),
        expected
    );
}
