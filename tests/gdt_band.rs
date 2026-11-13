//! Room for the GD&T in the layout ([`draftwright::layout::Strips::reserve_gdt`],
//! [`draftwright::dims::gdt::demand`]): the bands around the views hold the feature control
//! frames, datum feature symbols and labels lowering puts in them, and `dims::gdt` places each
//! inside its view's reserved area and on the sheet when a clear place there exists. Pinned on
//! AP242 fixtures, through `plan_recovered` as the library plans:
//!
//! - nist_ctc_02 e2, nist_stc_09 e4, spool_fits and thumbwheel_thread_knurl: no frame, datum
//!   symbol or label off the sheet or over another view (before, ctc_02's datum symbols E and J
//!   stood off the sheet's left edge, stc_09's frames off it and across the plan, spool_fits'
//!   datum symbol on the side view and thumbwheel's datum line across it);
//! - nist_ctc_02 e2: every item inside its area;
//! - nist_stc_09 e4: no datum symbol past its area, and every frame past it is one whose own
//!   strip has no clear place at all, so it stands in a strip the layout did not reserve for it
//!   (reported, never dropped); its room is reserved beyond every dimension lane, as within
//!   draftwright's estimate the dimensions below the front run on and leave 13 frames no place.

#[path = "common/support.rs"]
#[macro_use]
mod support;

#[path = "common/cache.rs"]
mod cache;

use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::layout::{Gdt, Lanes, Strips};
use draftwright::model::{self, Analysis, Plan, Spec};

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

/// An AP242 fixture's Analysis (`tests/fixtures/pmi/step/<stem>.*.gz`), from a scratch file of
/// its own.
fn analysed(stem: &str) -> Analysis {
    let dir = root().join("tests/fixtures/pmi/step");
    let gz = std::fs::read_dir(&dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| p.file_name().unwrap().to_string_lossy().split('.').next() == Some(stem))
        .unwrap();
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::read(&gz).unwrap().as_slice())
        .read_to_end(&mut bytes)
        .unwrap();
    let file = gz.file_name().unwrap().to_string_lossy();
    let out = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("gdt_band")
        .join(std::process::id().to_string())
        .join(stem);
    std::fs::create_dir_all(&out).unwrap();
    let path = out.join(file.strip_suffix(".gz").unwrap());
    // Written whole under a name of this thread's, then renamed: two tests read one fixture.
    let part = out.join(format!("{:?}.part", std::thread::current().id()));
    std::fs::write(&part, &bytes).unwrap();
    std::fs::rename(&part, &path).unwrap();
    cache::native(&path).1
}

/// The plan's GD&T items reported with *code*.
fn reported<'a>(plan: &'a Plan, code: &str) -> Vec<&'a draftwright::dims::gdt::PlannedGdt> {
    plan.gdt
        .iter()
        .filter(|g| {
            plan.problems
                .iter()
                .any(|p| p.code == code && p.subjects.first() == Some(&g.id))
        })
        .collect()
}

#[test]
fn ctc_02_frames_and_datum_symbols_are_inside_their_bands_and_on_the_sheet() {
    let a = analysed("nist_ctc_02_asme1_ap242-e2");
    let plan = cache::default_plan(&a, true);
    assert_eq!(plan.sheet.size, "A2", "a tall table must not force A0");
    assert_eq!(plan.sheet.scale, "1:5");
    assert!(plan.sheets.is_empty());
    for (id, count) in [("hole_schedule_plan", 61), ("hole_schedule_side", 22)] {
        let table = plan.hole_tables.iter().find(|t| t.id == id).unwrap();
        assert_eq!(table.rows.len(), count);
        if id == "hole_schedule_plan" {
            assert!(table.blocks.len() >= 2);
        }
        let headers = &table.blocks[0].rows[..2];
        let mut read = Vec::new();
        for block in &table.blocks {
            assert_eq!(&block.rows[..2], headers);
            read.extend(
                block.rows[2..]
                    .iter()
                    .filter(|r| r.iter().any(|c| !c.is_empty()))
                    .cloned(),
            );
        }
        assert_eq!(
            read,
            table
                .rows
                .iter()
                .map(|r| r.cells.clone())
                .collect::<Vec<_>>()
        );
    }
    // Unsupported datum targets remain source errors, as on A0; no symbol changes sheet.
    for datum in ["datum:#125", "datum:#126", "datum:#127"] {
        assert!(
            plan.problems
                .iter()
                .any(|p| p.code == "pmi_not_lowered" && p.refs.iter().any(|r| r == datum))
        );
    }
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "datum_not_identified")
    );
    let iso = plan.views.iter().find(|v| v.id == "v:iso").unwrap();
    let caption = plan.captions.iter().find(|c| c.view == iso.id).unwrap();
    let bottom = iso.at[1] + iso.scale * a.iso_bounds[1];
    assert!(
        caption.ink[3] < bottom && caption.ink[1] > bottom - 12.0,
        "the NTS caption must stay beneath the isometric, not under a table"
    );
    // Datum D among them since its feature, naming no face, is read as its usage's faces.
    assert_eq!(plan.gdt.len(), 11, "five frames and six datum symbols");
    assert!(plan.gdt.iter().all(|g| g.placed.is_some()));
    let off: Vec<&str> = reported(&plan, "annotation_off_sheet")
        .iter()
        .map(|g| g.id.as_str())
        .collect();
    assert!(off.is_empty(), "off the sheet: {off:?}");
    let outside: Vec<&str> = reported(&plan, "annotation_outside_area")
        .iter()
        .map(|g| g.id.as_str())
        .collect();
    assert!(outside.is_empty(), "past their areas: {outside:?}");
    // J and E stood left of the front view until #224 attached them to their holes' tags in the
    // plan, which sees the holes end-on: E above its B1 tag and J left of its D3 tag. F sat beneath
    // its B2 tag until quiddity-rust v0.2.2 (#120) made seven of the R25 rounds along z fillets:
    // their `15× R25` callout and the displaced section-recess callout now take the paper beneath
    // the plan, so F stays on its surface, right of the front view, as draftwright draws it (a
    // cosmetic cost on this dense part, recorded in the leader-crossings baseline). Each stays
    // inside the sheet's drawing area.
    let drawable = 10.0;
    for (id, side) in [
        ("datum:#75", ("v:plan", "above")),
        ("datum:#78", ("v:plan", "left")),
        ("datum:#76", ("v:front", "right")),
    ] {
        let g = plan.gdt.iter().find(|g| g.source == id).unwrap();
        assert_eq!((g.view.as_str(), g.side.as_str()), side);
        let b = g.placed.as_ref().unwrap().label_box;
        assert!(b[0] >= drawable, "{id} at {b:?}");
    }
}

part_cases!(frames_datum_symbols_and_labels_are_on_the_sheet_and_off_the_other_views;
    nist_ctc_02_asme1_ap242_e2 => "nist_ctc_02_asme1_ap242-e2",
    nist_stc_09_asme1_ap242_e4 => "nist_stc_09_asme1_ap242-e4",
    spool_fits => "spool_fits",
    thumbwheel_thread_knurl => "thumbwheel_thread_knurl",
);
fn frames_datum_symbols_and_labels_are_on_the_sheet_and_off_the_other_views(stem: &str) {
    let plan = cache::default_plan(&analysed(stem), true);
    assert!(!plan.gdt.is_empty(), "{stem} has GD&T");
    for code in [
        "annotation_off_sheet",
        "annotation_over_view",
        "annotation_line_over_view",
    ] {
        let ids: Vec<&str> = reported(&plan, code)
            .iter()
            .map(|g| g.id.as_str())
            .collect();
        assert!(ids.is_empty(), "{stem}: {code} {ids:?}");
    }
}

#[test]
fn stc_09_datum_symbols_are_inside_their_bands_and_frames_past_them_had_no_clear_strip() {
    let a = analysed("nist_stc_09_asme1_ap242-e4");
    let plan = cache::default_plan(&a, true);
    // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A1", "1:2")
    );
    let outside = reported(&plan, "annotation_outside_area");
    for g in &outside {
        assert_eq!(g.kind, "control_frame", "{} past its area", g.id);
        assert!(
            g.basis.contains("strip has no clear place, so it stands"),
            "{} is past its area in its own strip: {}",
            g.id,
            g.basis
        );
    }
    // 12 frames and 4 datum symbols stood past their areas before the reservation; 1 frame,
    // moved to a strip across or beside its view, remained (2 while the value-less `2X R` and
    // `4X R` labels were drawn, #214), until it stood beneath its holes' `4× ⌀6.35` callout
    // (#224).
    assert_eq!(
        outside.len(),
        0,
        "{:?}",
        outside.iter().map(|g| &g.id).collect::<Vec<_>>()
    );
}

/// The reservation is the strip's dimensions' room and its items' tiers packed first fit: two
/// frames side by side share a tier, two over one another take two; the dimensions' room is the
/// lesser of the band before GD&T and the strip's lanes, or the lanes beyond that; an item past
/// the view's end widens the band beyond that end; a part with no GD&T reserves nothing.
#[test]
fn the_reservation_stacks_overlapping_items_in_tiers_beyond_the_dimensions() {
    let frame = |at: f64| Gdt {
        view: "front",
        side: "below",
        at,
        span: [0.0, 32.0],
        ends: [-100.0, 100.0],
        depth: 6.0,
        lead: 3.7,
    };
    let lanes = Lanes {
        front_below: 2,
        ..Lanes::default()
    };
    let lane_band = draftwright::layout::lane_band(2);
    let base = Strips::without_features();
    let band = |items: &[Gdt], scale: f64, beyond: bool| {
        let mut s = base.clone();
        s.reserve_gdt(&base, &lanes, beyond, items, scale);
        s.fv_bottom
    };
    let one = lane_band + 2.0 + 6.0;
    assert_eq!(band(&[frame(-50.0)], 1.0, true), one);
    // 100 mm apart at 1:1, side by side; 20 mm apart at 1:5, over one another.
    let two = [frame(-50.0), frame(50.0)];
    assert_eq!(band(&two, 1.0, true), one);
    assert_eq!(band(&two, 0.2, true), one + 2.0 + 6.0);
    // Within a band before GD&T narrower than the lanes', the tiers start beyond that band.
    let mut narrow = base.clone();
    narrow.fv_bottom = 4.0;
    let mut s = narrow.clone();
    s.reserve_gdt(&narrow, &lanes, false, &[frame(-50.0)], 1.0);
    assert!(4.0 < lane_band);
    assert_eq!(s.fv_bottom, 4.0 + 2.0 + 6.0);
    let mut s = narrow.clone();
    s.reserve_gdt(&narrow, &lanes, true, &[frame(-50.0)], 1.0);
    assert_eq!(s.fv_bottom, one);
    // A frame hung right of a leader 10 mm short of the view's right end runs 22 mm past it.
    let mut s = base.clone();
    let right = s.right;
    s.reserve_gdt(&base, &lanes, false, &[frame(90.0)], 1.0);
    assert_eq!(s.right, right.max(22.0));
    let mut s = base.clone();
    s.reserve_gdt(&base, &lanes, false, &[], 1.0);
    assert_eq!(s, base);
}

/// STC-09 keeps its first CAD-frame drawing at its legibility floor and reports
/// the remaining layout findings; the complete frame/datum inventory survives.
#[test]
fn stc09_without_a_complete_fit_keeps_its_first_drawing_and_reports_the_floor() {
    let a = analysed("nist_stc_09_asme1_ap242-e4");
    let first = model::plan_escalated(&a, &Spec::default()).unwrap();
    assert_eq!(
        (first.sheet.size.as_str(), first.sheet.scale.as_str()),
        // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
        ("A1", "1:2")
    );
    let p = cache::default_plan(&a, true);
    // The value-less `2X R` and `4X R` common labels are refused (#214).
    assert_eq!(p.gdt.len(), 26, "complete frame/datum inventory");
    let notices: Vec<_> = p
        .problems
        .iter()
        .filter(|p| p.code == "sheet_preference_exceeded")
        .collect();
    assert_eq!(notices.len(), 1);
    assert_eq!(notices[0].severity, "info");
    assert_eq!(
        notices[0].labels().impact,
        draftwright::finding::Impact::Advisory
    );
    assert!(notices[0].message.contains(&p.sheet.size));
    assert!(p.sheet.basis.contains("sheet/preferred_max_size A3"));
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        (first.sheet.size.as_str(), first.sheet.scale.as_str())
    );
    // Recovery retains the first geometry; the final allocator may add slack-only gutters.
    let mut unspaced = Spec::default();
    unspaced.sheet.view_gutter = Some(0.0);
    let unspaced = model::plan_recovered(&a, &unspaced).unwrap();
    assert_eq!(
        serde_json::to_value(&unspaced.views).unwrap(),
        serde_json::to_value(&first.views).unwrap()
    );
    assert_eq!(
        p.views.iter().map(|v| (&v.id, v.scale)).collect::<Vec<_>>(),
        first
            .views
            .iter()
            .map(|v| (&v.id, v.scale))
            .collect::<Vec<_>>()
    );
    assert_eq!(
        p.sheet.scale_factor,
        draftwright::sheet_fit::min_legible_scale(&a)
    );
    let said = p
        .problems
        .iter()
        .find(|q| q.code == "layout_recovery_exhausted")
        .expect("the exhausted search is said");
    assert_eq!(said.severity, "warning");
    // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
    assert!(
        said.message.starts_with(
            "A1 at 1:2 has 39 hard layout findings, 6 hole locations too close to read;"
        ),
        "{}",
        said.message
    );
    assert_eq!(p.status, draftwright::completeness::Status::NeedsAttention);
    assert_eq!(
        p.sheet.basis.matches("drawn-item fill").count(),
        1,
        "{}",
        p.sheet.basis
    );
    assert!(
        !p.sheet.basis.contains("as the spec asks"),
        "{}",
        p.sheet.basis
    );
}
