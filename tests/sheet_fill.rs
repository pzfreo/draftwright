//! Maintainer sheet policy, 2026-10-12: measured content, bounded paper, valid ink.
use draftwright::model::{self, Analysis, Spec};
use serde_json::json;
use std::path::Path;

fn part() -> Analysis {
    model::analyse(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/prismatic.step"),
    )
    .unwrap()
    .1
}
fn named(size: &str, scale: &str) -> Spec {
    serde_json::from_value(json!({"sheet": {"size": size, "scale": scale}})).unwrap()
}

#[test]
fn smallest_fit_can_only_grow_one_sheet_with_enlargement() {
    let a = part();
    let fit = model::plan_escalated(&a, &named("A4", "1:1")).unwrap();
    let first = model::plan_escalated(&a, &named("A2", "1:1")).unwrap();
    let p = model::recover(&a, &Spec::default(), first).unwrap();
    assert!(
        matches!(p.sheet.size.as_str(), "A4" | "A3"),
        "{}",
        p.sheet.basis
    );
    if p.sheet.size == "A3" {
        assert!(p.sheet.scale_factor > fit.sheet.scale_factor);
    }
    assert!(p.sheet.basis.contains("smallest fit"), "{}", p.sheet.basis);
}

#[test]
fn complete_underfilled_sheet_is_enlarged_and_fill_candidates_are_explained() {
    let a = part();
    let first = model::plan_escalated(&a, &named("A4", "1:10")).unwrap();
    let p = model::recover(&a, &Spec::default(), first).unwrap();
    assert!(p.sheet.scale_factor > 0.1);
    assert!(p.sheet.basis.contains("fill"), "{}", p.sheet.basis);
    assert!(p.sheet.basis.contains("60–85%"), "{}", p.sheet.basis);
}

#[test]
fn named_sheet_overrides_smallest_fit_but_still_chooses_scale_by_fill() {
    let a = part();
    let mut s = Spec::default();
    s.sheet.size = Some("A2".into());
    let first = model::plan_escalated(&a, &named("A2", "1:10")).unwrap();
    let p = model::recover(&a, &s, first).unwrap();
    assert_eq!(p.sheet.size, "A2");
    assert!(p.sheet.scale_factor > 0.1);
    assert!(p.sheet.basis.contains("fill"), "{}", p.sheet.basis);
    let s = named("A2", "1:10");
    let p = model::plan_recovered(&a, &s).unwrap();
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        ("A2", "1:10")
    );
}

#[test]
fn invalid_high_fill_never_beats_a_valid_complete_sheet() {
    let a = part();
    let first = model::plan_escalated(&a, &named("A4", "10:1")).unwrap();
    assert!(
        first
            .problems
            .iter()
            .any(|p| p.code == "page_fit_uncertain")
    );
    let p = model::recover(&a, &Spec::default(), first).unwrap();
    assert!(!p.problems.iter().any(|p| matches!(
        p.code.as_str(),
        "annotation_off_sheet"
            | "annotation_overlap"
            | "annotation_ink_overlap"
            | "page_fit_uncertain"
            | "views_do_not_fit"
    )));
    assert!(p.sheet.basis.contains("fill"), "{}", p.sheet.basis);
}

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/sheet_rule.rs"]
mod sheet_rule;

#[test]
fn cosmetic_contacts_keep_the_thickened_plate_on_a4_at_full_scale() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/revisions/plate_thickened.new.step.gz");
    let a = model::analyse_native(&path).unwrap().1;
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A4", "1:1"),
        "{}",
        plan.sheet.basis
    );
    assert!(plan.sheets.is_empty());
    assert!(!plan.problems.iter().any(|p| matches!(
        p.code.as_str(),
        "annotation_off_sheet"
            | "annotation_overlap"
            | "annotation_ink_overlap"
            | "page_fit_uncertain"
            | "views_do_not_fit"
    )));
    let explicit = model::plan_recovered(&a, &named("A4", "1:1")).unwrap();
    let statements = |p: &model::Plan| {
        p.dimensions
            .iter()
            .filter(|d| d.placed.is_some())
            .flat_map(|d| &d.measurements)
            .map(|m| (m.feature.clone(), m.parameter.clone()))
            .collect::<std::collections::BTreeSet<_>>()
    };
    assert_eq!(statements(&plan), statements(&explicit));
}

#[test]
fn grm04_uses_the_largest_clean_scale_on_a4_before_spending_more_paper() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/rule_pins/grm04_drive_plate.step.gz");
    let a = model::analyse_native(&path).unwrap().1;
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A4", "5:1")
    );
    assert!(plan.sheets.is_empty());
    assert_eq!(
        plan.sheet.dimension_spacing,
        model::DimensionSpacing::Compact
    );
    assert!(!plan.problems.iter().any(|p| matches!(
        p.code.as_str(),
        "page_fit_uncertain"
            | "annotation_off_sheet"
            | "views_do_not_fit"
            | "callout_dropped"
            | "location_ref_dropped"
            | "leader_along_centreline"
    )));
    let conflicts = plan
        .problems
        .iter()
        .filter(|p| {
            matches!(
                p.code.as_str(),
                "annotation_ink_overlap"
                    | "annotation_over_view"
                    | "annotation_line_over_view"
                    | "annotation_leader_crossing"
                    | "annotation_outside_area"
            )
        })
        .count();
    assert!(plan.sheet.basis.contains(&format!("A4 at 5:1 (Compact): valid and complete; usable; {conflicts} final annotation conflicts")),"{}",plan.sheet.basis);
    for outcome in [
        "A4 at 10:1",
        "A4 at 5:1 (Normal)",
        "A4 at 5:1 (Compact)",
        "base choice before scale maximisation A3 at 5:1",
    ] {
        assert!(
            plan.sheet.basis.contains(outcome),
            "{outcome}: {}",
            plan.sheet.basis
        );
    }
    assert!(
        plan.views
            .iter()
            .filter(|v| v.id != "v:iso")
            .all(|v| v.scale == 5.0)
    );
}

#[test]
fn grm03_keeps_a4_once_its_extension_contact_is_finished_away() {
    let Some(dir) = corpus::dir() else {
        return;
    };
    let a = model::analyse_native(&dir.join("gramel/GRM-03_thumbwheel_drive_screw.step"))
        .unwrap()
        .1;
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    // #221: the A4 trial's first routing put a leader on an extension line, which the trial's
    // finishing pass now clears, so A4 is clean and wins over A3 at 10:1.
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        ("A4", "5:1")
    );
    let a4 = &p.sheet_candidates[0];
    assert_eq!((a4.size.as_str(), a4.max), ("A4", 1));
    assert!(a4.outcome.contains("usable"), "{}", a4.outcome);
    assert!(
        !p.sheet_candidates
            .iter()
            .any(|c| c.outcome.contains("leader_on_extension"))
    );
    // Centre lines cropped on the frame line clip nothing: one A4 sheet.
    assert!(p.sheets.is_empty());
    // A4 first outranks fill: 5:1 fills below the band, and A4 at the next scale, 10:1,
    // still loses ink off the sheet.
    let fill = draftwright::sheet_fit::fill(&a, &p);
    assert!(
        fill < draftwright::sheet_fit::FILL_BAND[0],
        "GRM-03 fill {fill:.4}"
    );
    let larger = model::plan_recovered(&a, &named("A4", "10:1")).unwrap();
    assert!(
        larger
            .problems
            .iter()
            .any(|q| q.severity == "error" && q.code == "annotation_off_sheet"),
        "{:?}",
        larger.problems
    );
    assert!(p.views.iter().all(|v| v.detail.is_none()));
    assert!(p.dimensions.iter().all(|d| d.placed.is_some()));
    sheet_rule::assert_rule("grm03", &p);
    assert!(!p.problems.iter().any(|p| matches!(
        p.code.as_str(),
        "annotation_off_sheet"
            | "annotation_outside_area"
            | "annotation_overlap"
            | "annotation_ink_overlap"
            | "page_fit_uncertain"
    )));
}

fn manufacturing_plate() -> Analysis {
    use std::io::Read as _;
    let bytes = std::fs::read(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/parts/manufacturing_pmi.step.gz"),
    )
    .unwrap();
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(bytes.as_slice())
        .read_to_end(&mut step)
        .unwrap();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("h6-pmi-plate")
        .join(format!("{:?}", std::thread::current().id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("manufacturing-pmi.step");
    std::fs::write(&path, step).unwrap();
    model::analyse_native(&path).unwrap().1
}

/// The supplied plate chooses A3 at 2:1 with every frame clear (1:1 until covered PMI spans
/// stopped leaving empty rungs in its ladders). Smaller
/// measured candidates can exist; the independent sheet-rule oracle must
/// still select the fuller allowed drawing rather than compacting the plate.
#[test]
fn specify_plate_pmi_fits_a3_at_full_size_with_every_frame_clear() {
    let a = manufacturing_plate();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    sheet_rule::assert_rule("manufacturing-pmi", &p);
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        ("A3", "2:1")
    );
    // #127's fixed-size source note must remain drawn, without rewarding a
    // compact 1:5 diagram after PMI covers the generated dimensions.
    assert!(
        p.notes.iter().any(|n| {
            n.id == "pmi:manufacturing_requirement.edge_condition" && n.placed.is_some()
        })
    );
    assert_eq!(p.gdt.len(), 6);
    assert!(p.gdt.iter().all(|g| g.placed.is_some()));
    assert!(!p.problems.iter().any(|p| matches!(
        p.code.as_str(),
        "annotation_off_sheet"
            | "annotation_overlap"
            | "annotation_ink_overlap"
            | "page_fit_uncertain"
    )));
    for v in &p.views {
        let b = v.area;
        let t = p.sheet.title_block;
        assert!(!(b[0] < t[2] && t[0] < b[2] && b[1] < t[3] && t[1] < b[3]));
    }
}

#[test]
fn automatic_overflow_never_uses_an_illegible_scale() {
    let mut a = part();
    a.envelope = [[0.0; 3], [100_000.0; 3]];
    let error = model::plan_recovered(&a, &Spec::default()).unwrap_err();
    assert!(
        error.message.contains("No unclipped automatic drawing"),
        "{error}"
    );
}

#[test]
fn ctc03_valid_complete_sheet_beats_invalid_seed_despite_material_crossings() {
    let Some(dir) = corpus::dir() else { return };
    let mut a = model::analyse_native(&dir.join("nist/nist_ctc_03_asme1_rc.stp"))
        .unwrap()
        .1;
    // The seed was captured when the two congruent slots were dimensioned singly: one is split
    // from their slot callout, so the plan recovered from it has the first's items. The split
    // one is now dimensioned by its end centres, so its length, end radius and low-end location
    // are left out of the seed below.
    corpus::split_slot(&mut a, "f:slot:f66");
    use std::io::Read as _;
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(
        include_bytes!("fixtures/sheet-policy/ctc03-invalid.plan.json.gz").as_slice(),
    )
    .read_to_end(&mut bytes)
    .unwrap();
    let mut first: model::Plan = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(first.binding.source_sha256, a.binding.source_sha256);
    assert!(
        first
            .problems
            .iter()
            .any(|p| p.code == "annotation_ink_overlap")
    );
    // The seed was captured before G3 planned radii by note and leader; its radius callouts
    // are not outcomes the current planner owes on any sheet, so they are left out of it.
    first.dimensions.retain(|d| {
        !d.measurements
            .iter()
            .all(|m| matches!(m.parameter.as_str(), "blend.radius" | "fillet.radius"))
    });
    first.dimensions.retain(|d| {
        !d.measurements.iter().all(|m| {
            m.feature == "f:slot:f66"
                && matches!(
                    m.parameter.as_str(),
                    "slot_length.length" | "slot_end_radius.radius" | "location_slot.length"
                )
        })
    });
    let mut spec = Spec::default();
    spec.sheet.size = Some("A2".into());
    first.spec.sheet.size = spec.sheet.size.clone();
    let p = model::recover(&a, &spec, first).unwrap();
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        ("A2", "1:5")
    );
    assert!(p.dimensions.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| m.feature == "f:slot:f66" && m.parameter == "slot_centres.length")
    }));
    assert!(!p.problems.iter().any(|p| matches!(
        p.code.as_str(),
        "annotation_ink_overlap" | "layout_recovery_exhausted" | "annotation_off_sheet"
    )));
    // A profile-corner angle with no clear place is dropped by design and still reported.
    assert!(
        p.dimensions.iter().all(|d| !matches!(&d.state,
            draftwright::items::State::NotDrawable { reason_code } if reason_code != "angular_dimension_dropped"))
    );
}

#[test]
fn holes_set_legibility_floor_from_visible_opening_size() {
    let mut a = model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step"),
    )
    .unwrap()
    .1;
    a.features[0].kind = "hole".into();
    a.features[0].parameters = json!({"diameter": 6.6}).as_object().unwrap().clone();
    assert!(
        draftwright::sheet_fit::min_legible_scale(&a) >= draftwright::sheet_fit::MIN_OPENING / 6.6
    );
    a.features[0].parameters = json!({"diameter": 0.6}).as_object().unwrap().clone();
    assert!(
        draftwright::sheet_fit::min_legible_scale(&a) >= draftwright::sheet_fit::MIN_OPENING / 0.6
    );
}

#[test]
fn packed_gdt_keeps_title_overflow_on_the_recipient_sheet() {
    let a = manufacturing_plate();
    let mut spec = Spec::default();
    spec.sheet.size = Some("A3".into());
    spec.sheet.title.insert("title".into(), "M".repeat(100));
    let p = model::plan_recovered(&a, &spec).unwrap();
    assert_eq!(p.sheet.size, "A3");
    assert!(p.problems.iter().any(|q| q.code == "title_field_overflow"));
    assert_eq!(p.status, draftwright::completeness::Status::NeedsAttention);
}

#[test]
fn unavailable_legible_scale_is_a_warning() {
    let mut a = manufacturing_plate();
    a.features
        .iter_mut()
        .find(|f| f.kind == "hole")
        .unwrap()
        .parameters
        .insert("diameter".into(), json!(0.02));
    assert!(draftwright::sheet_fit::min_legible_scale(&a) > 10.0);
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert!(
        p.problems
            .iter()
            .any(|q| q.code == "layout_recovery_exhausted" && q.severity == "warning")
    );
    assert_eq!(p.status, draftwright::completeness::Status::NeedsAttention);
}

fn gz_part(name: &str) -> Analysis {
    use std::io::Read as _;
    let bytes = std::fs::read(
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/pmi/step")
            .join(format!("{name}.gz")),
    )
    .unwrap();
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(bytes.as_slice())
        .read_to_end(&mut step)
        .unwrap();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("large-sheets")
        .join(format!(
            "{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(name);
    std::fs::write(&path, step).unwrap();
    model::analyse_native(&path).unwrap().1
}

/// Maintainer rule, 2026-10-15: A2 and larger are for parts that need them at 1:1. A part about
/// 100 mm across, whose views fit A3 at 1:1, never takes a large sheet by default, even when its
/// first estimate is A2 and A2 would score fewer findings (assembly_plate_pin: its ladder
/// estimate is A2 at 1:1). A named large sheet is still the user's to choose.
#[test]
fn a_part_about_100_mm_never_gets_a2_or_larger_by_default() {
    for name in [
        "assembly_plate_pin.step",
        "hw_assembly_plate_pin_intent.step",
    ] {
        let a = gz_part(name);
        let p = model::plan_recovered(&a, &Spec::default()).unwrap();
        assert!(
            matches!(p.sheet.size.as_str(), "A4" | "A3"),
            "{name}: {} at {}: {}",
            p.sheet.size,
            p.sheet.scale,
            p.sheet.basis
        );
        let first = model::plan_escalated(&a, &named("A2", "1:1")).unwrap();
        let p = model::recover(&a, &Spec::default(), first).unwrap();
        assert!(
            matches!(p.sheet.size.as_str(), "A4" | "A3"),
            "{name} from an A2 estimate: {}",
            p.sheet.basis
        );
    }
    let a = part();
    let first = model::plan_escalated(&a, &named("A2", "1:1")).unwrap();
    let mut s = Spec::default();
    s.sheet.size = Some("A2".into());
    assert_eq!(model::recover(&a, &s, first).unwrap().sheet.size, "A2");
}

/// #163: the sheet metal hanger's plan band, not its ink, overran A4 at 1:2, so its views
/// went to A3 at the same scale. Judged by its drawn items, A4 holds the same drawing.
#[test]
fn sm_hanger_keeps_its_scale_on_a4_when_only_an_empty_band_overruns() {
    let Some(dir) = corpus::dir() else {
        return;
    };
    let a = model::analyse_native(&dir.join("ttt_inputs/sm-hanger.step"))
        .unwrap()
        .1;
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(plan.sheet.size, "A4", "{}", plan.sheet.basis);
    assert!(plan.sheet.scale_factor >= 0.5, "{}", plan.sheet.basis);
    assert!(plan.sheets.is_empty());
    for v in &plan.views {
        let [x0, y0, x1, y1] = v.area;
        assert!(
            x0 >= 10.
                && y0 >= 10.
                && x1 <= plan.sheet.page[0] - 10.
                && y1 <= plan.sheet.page[1] - 10.,
            "{} {:?}",
            v.id,
            v.area
        );
    }
    assert!(
        !plan.problems.iter().any(|p| matches!(
            p.code.as_str(),
            "annotation_off_sheet" | "page_fit_uncertain" | "views_do_not_fit" | "views_overlap"
        )),
        "{:?}",
        plan.problems
    );
}

/// Different tolerances keep each of the small turned part's statements distinct. A
/// section over the title block must not push the end view off A4, and a transverse
/// hole's witness must not run through a step-length label.
#[test]
fn toleranced_string_post_is_complete_on_a4_without_hard_layout_findings() {
    let Some(dir) = corpus::dir() else {
        return;
    };
    let a = model::analyse_native(&dir.join("gramel/string_post.step"))
        .unwrap()
        .1;
    let spec: Spec = serde_json::from_str(include_str!(
        "fixtures/layout/string_post_tolerances.spec.json"
    ))
    .unwrap();
    let plan = model::plan_recovered(&a, &spec).unwrap();
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A4", "5:1"),
        "{}",
        plan.sheet.basis
    );
    assert!(plan.views.iter().all(|v| v.detail.is_none()));
    let hard = [
        "annotation_off_sheet",
        "annotation_over_title_block",
        "annotation_overlap",
        "annotation_ink_overlap",
        "title_field_overflow",
        "page_fit_uncertain",
        "views_do_not_fit",
        "notes_not_placed",
        "table_not_placed",
    ];
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| hard.contains(&p.code.as_str())),
        "{:?}",
        plan.problems
    );
    for id in spec.dimensions.keys() {
        assert!(
            plan.dimensions
                .iter()
                .any(|d| &d.id == id && d.placed.is_some()),
            "missing toleranced statement {id}"
        );
    }
    sheet_rule::assert_rule("toleranced string post", &plan);
}

/// The same small geometry with GD&T keeps the established readable scale. Its
/// reserved frame corridors stay with the aligned row during section correction.
#[test]
fn section_correction_preserves_the_gdt_string_posts_readable_scale() {
    use std::io::Read as _;
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/pmi/step/hw_string_post_tapped_intent.step.gz");
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(path).unwrap())
        .read_to_end(&mut step)
        .unwrap();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sheet-fill-gdt-string-post");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("string-post.step");
    std::fs::write(&path, step).unwrap();
    let a = model::analyse_native(&path).unwrap().1;
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        ("A3", "5:1"),
        "{}",
        p.sheet.basis
    );
    assert!(!p.problems.iter().any(|p| matches!(
        p.code.as_str(),
        "annotation_off_sheet"
            | "annotation_overlap"
            | "annotation_ink_overlap"
            | "page_fit_uncertain"
    )));
}

/// Conservative section bands must not force a second sheet when the settled ink fits.
fn assert_small_single_sheet(name: &str, expected: (&str, &str)) {
    let Some(dir) = corpus::dir() else { return };
    let a = model::analyse_native(&dir.join(format!("mfcadpp/{name}.step")))
        .unwrap()
        .1;
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert!(
        matches!(p.sheet.size.as_str(), "A4" | "A3"),
        "{name}: {}",
        p.sheet.basis
    );
    assert!(p.sheets.is_empty(), "{name}: {}", p.sheet.basis);
    assert_eq!((p.sheet.size.as_str(), p.sheet.scale.as_str()), expected);
    assert!(
        p.views
            .iter()
            .filter(|v| ["v:front", "v:plan", "v:side"].contains(&v.id.as_str()))
            .all(|v| v.sheet == 1)
    );
    assert!(p.views.iter().all(|v| v.sheet == 1));
    for (i, v) in p.views.iter().enumerate() {
        let [x0, y0, x1, y1] = v.area;
        assert!(
            x0 >= 10. && y0 >= 10. && x1 <= p.sheet.page[0] - 10. && y1 <= p.sheet.page[1] - 10.,
            "{name}: {} {:?}",
            v.id,
            v.area
        );
        assert!(p.views[i + 1..].iter().all(|w| !(v.area[0] < w.area[2]
            && v.area[2] > w.area[0]
            && v.area[1] < w.area[3]
            && v.area[3] > w.area[1])));
    }
    assert!(
        !p.problems.iter().any(|q| [
            "annotation_off_sheet",
            "views_overlap",
            "view_on_title_block",
            "cross_reference_missing",
            "layout_recovery_exhausted"
        ]
        .contains(&q.code.as_str())),
        "{name}: {:?}",
        p.problems
    );
    assert!(
        p.spec.sheets.is_none(),
        "allocation must not rewrite intent"
    );
}

/// Readability is required even when a smaller scale physically fits A4.
#[test]
fn small_10224_keeps_readable_features_at_two_to_one() {
    // Repacking the section lets A4 1:1 fit, but its 1.296 mm diameter
    // is below the 1.5 paper-mm reduction floor. A4 2:1 does not fit
    // the complete layout, so retain main's readable A3 2:1.
    assert_small_single_sheet("10224", ("A3", "2:1"));
}

#[test]
fn small_10255_fits_one_a3_sheet() {
    assert_small_single_sheet("10255", ("A3", "2:1"));
}

/// This 279 mm principal projection already needs large paper at full size.
#[test]
fn a_part_needing_large_paper_at_full_size_keeps_large_paper() {
    let Some(dir) = corpus::dir() else { return };
    let mut a = model::analyse_native(&dir.join("nist/nist_ftc_09_asme1_rd.stp"))
        .unwrap()
        .1;
    // Its congruent slots 1 and 2, called out together, drop the slot radius leader that kept
    // A2 at 1:2 from being clean, and the part fits A2 at 1:2 (the default drawing now): slot 2
    // is split from their callout so this guard keeps reading the full-size large-paper case.
    corpus::split_slot(&mut a, "f:slot:f82");
    let p = model::plan_recovered(
        &a,
        &serde_json::from_value(serde_json::json!({"sheet":{"preferred_max_size":"A0"}})).unwrap(),
    )
    .unwrap();
    assert!(
        matches!(p.sheet.size.as_str(), "A2" | "A1" | "A0"),
        "{}",
        p.sheet.basis
    );
    assert_eq!(p.sheet.scale, "1:1");
    assert!(p.sheets.is_empty());
    assert!(!p.sheet.basis.contains("R5 allocation before"));
}

#[test]
fn overflow_honours_named_paper_and_sheet_count() {
    let Some(dir) = corpus::dir() else { return };
    let a = model::analyse_native(&dir.join("mfcadpp/10224.step"))
        .unwrap()
        .1;
    for (intent, size, count) in [
        (json!({"sheet":{"size":"A2"}}), "A2", 1),
        // A one-sheet limit must not trade the 1.5 mm feature minimum for A4.
        (json!({"sheets":{"max":1}}), "A3", 1),
    ] {
        let spec: Spec = serde_json::from_value(intent).unwrap();
        let p = model::plan_recovered(&a, &spec).unwrap();
        assert_eq!(p.sheet.size, size);
        assert_eq!(1 + p.sheets.len(), count);
        assert_eq!(p.spec.sheets, spec.sheets);
        if spec.sheet.size.is_none() {
            assert_eq!(p.sheet.scale, "2:1");
        }
    }
    let spec: Spec = serde_json::from_value(json!({"sheets":{"max":2}})).unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    assert!(
        matches!(p.sheet.size.as_str(), "A4" | "A3"),
        "{}",
        p.sheet.basis
    );
    assert!(p.sheets.is_empty());
    assert_eq!(p.spec.sheets, spec.sheets);
}

#[test]
fn small_overflow_respects_explicit_auxiliary_assignment() {
    let Some(dir) = corpus::dir() else { return };
    let a = model::analyse_native(&dir.join("mfcadpp/10224.step"))
        .unwrap()
        .1;
    let spec: Spec =
        serde_json::from_value(json!({"sheets":{"max":2},"views":{"iso":{"sheet":2}}})).unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    assert!(
        matches!(p.sheet.size.as_str(), "A4" | "A3"),
        "{}",
        p.sheet.basis
    );
    assert_eq!(p.sheets.len(), 1);
    assert_eq!(p.views.iter().find(|v| v.id == "v:iso").unwrap().sheet, 2);
    assert_eq!(p.spec.views["iso"].sheet, Some(2));
    assert_eq!(p.spec.sheets, spec.sheets);
}

#[test]
fn a_hard_sheet_limit_preserves_soft_preference_accountability() {
    let Some(dir) = corpus::dir() else { return };
    let a = model::analyse_native(&dir.join("mfcadpp/10190.step"))
        .unwrap()
        .1;
    let spec = serde_json::from_value(json!({"sheets":{"max":9}})).unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    sheet_rule::assert_preferences(&p);
    assert!(
        p.sheet_candidates
            .iter()
            .all(|c| c.max <= 9 && c.preferred_max_size == "A3" && c.preferred_max_sheets == 2)
    );
}

#[test]
fn fixed_scale_can_use_a_valid_smaller_sheet_without_changing_scale() {
    let Some(dir) = corpus::dir() else { return };
    let a = model::analyse_native(&dir.join("gramel/GRM-03_thumbwheel_drive_screw.step"))
        .unwrap()
        .1;
    let spec: Spec = serde_json::from_value(json!({"sheet":{"scale":"10:1"}})).unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    assert_eq!(p.sheet.size, "A3", "{}", p.sheet.basis);
    assert_eq!(p.sheet.scale, "10:1");
    assert!(p.sheets.is_empty());
    assert_eq!(p.spec.sheet.scale, spec.sheet.scale);
}

/// A fit is readable only when the dimensioned profile intervals survive its scale.
/// This formerly selected A4 1:10: the 420 mm profile was 42 mm tall and its 20 mm
/// shoulder bands were shorter than the 2.1 mm printed capitals, despite no hard collision.
#[test]
fn connector109_uses_a3_at_one_to_five_for_readable_steps() {
    let Some(dir) = corpus::dir() else { return };
    let a = model::analyse_native(&dir.join("cadgenbench/threaded_connector_109.step"))
        .unwrap()
        .1;
    let tiny = model::plan_recovered(&a, &named("A4", "1:10")).unwrap();
    assert!(draftwright::sheet_fit::illegible_steps(&a, &tiny) > 0);
    assert!(
        tiny.problems
            .iter()
            .any(|p| p.code == "legibility_floor_breached")
    );
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        ("A3", "1:5"),
        "{}",
        p.sheet.basis
    );
    assert_eq!(draftwright::sheet_fit::illegible_steps(&a, &p), 0);
    assert!(p.views.iter().all(|v| v.detail.is_none()));
    assert!(p.dimensions.iter().all(|d| d.placed.is_some()));
    sheet_rule::assert_rule("connector109", &p);
}

/// A sheet and scale whose drawing lacks room, and which the planner itself doubts, gets a
/// second attempt with the height ladder reserved at lane pitch. FTC-06 takes A2 at 1:2 by
/// it (A2 at 1:5 before, fill 13% to 40%), and its basis says so. A kept layout at that sheet
/// and scale gets the same attempt, so a local edit keeps the drawing's ink on its sheet.
#[test]
fn ftc06_holds_one_to_two_on_a_second_attempt_and_a_kept_layout_holds_it_too() {
    let Some(dir) = corpus::dir() else { return };
    let a = model::analyse_native(&dir.join("nist/nist_ftc_06_asme1_rd.stp"))
        .unwrap()
        .1;
    let kept: Spec = serde_json::from_value(json!({
        "sheet": {
            "size": "A2", "scale": "1:2", "dimension_spacing": "compact", "keep_layout": true
        },
        "sheets": { "max": 1 }
    }))
    .unwrap();
    for spec in [Spec::default(), kept] {
        let p = model::plan_recovered(&a, &spec).unwrap();
        assert_eq!(
            (p.sheet.size.as_str(), p.sheet.scale.as_str()),
            ("A2", "1:2"),
            "{}",
            p.sheet.basis
        );
        assert!(
            p.sheet
                .basis
                .contains("height ladder reserved at lane pitch"),
            "{}",
            p.sheet.basis
        );
        let lost: Vec<_> = p
            .problems
            .iter()
            .filter(|q| {
                matches!(
                    q.code.as_str(),
                    "page_fit_uncertain" | "annotation_off_sheet" | "annotation_outside_area"
                )
            })
            .collect();
        assert!(lost.is_empty(), "{lost:?}");
    }
}

#[test]
fn step_legibility_uses_the_owning_view_and_keeps_baseline_intervals() {
    let mut a = model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/turned.step"),
    )
    .unwrap()
    .1;
    let mut p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let mut d = p
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .clone();
    let f = &mut a.features[0];
    f.kind = "turned_step".into();
    f.parameters = json!({"lo": 0.0, "hi": 20.0}).as_object().unwrap().clone();
    d.measurements = vec![draftwright::dims::Measurement {
        feature: f.id.clone(),
        parameter: "step.length".into(),
        value: 20.0,
    }];
    p.dimensions = vec![d];
    let mut detail = p
        .views
        .iter()
        .find(|v| v.id == p.dimensions[0].view)
        .unwrap()
        .clone();
    for v in &mut p.views {
        v.scale = 0.1;
    }
    assert_eq!(draftwright::sheet_fit::illegible_steps(&a, &p), 1);
    detail.id = "v:detail_a".into();
    detail.scale = 0.2;
    p.dimensions[0].view = detail.id.clone();
    p.views.push(detail);
    assert_eq!(draftwright::sheet_fit::illegible_steps(&a, &p), 0);
    p.dimensions[0].measurements[0].parameter = "step_position.length".into();
    p.views.last_mut().unwrap().scale = 0.1;
    assert_eq!(draftwright::sheet_fit::illegible_steps(&a, &p), 1);
}

/// A source note cannot induce compaction when the geometry floor already rules
/// out reductions. Keep the established enlargement and reuse its clean A4 plan.
#[test]
fn pmi_bolt_reuses_its_readable_a4_baseline() {
    let (_, a) = model::analyse_native(
        &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/pmi/step/bolt_thread_knurl.step.gz"),
    )
    .unwrap();
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        ("A4", "2:1")
    );
    assert_eq!(p.sheet_candidates.len(), 1);
    let candidate = &p.sheet_candidates[0];
    assert_eq!((candidate.size.as_str(), candidate.max), ("A4", 1));
    assert!(candidate.outcome.starts_with("A4: usable at 2:1"));
    assert!(p.gdt.iter().all(|g| g.placed.is_some()));
}
