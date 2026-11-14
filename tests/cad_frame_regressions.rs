//! Exact reviewed CAD-frame layout regressions; information and sheet bounds remain guarded.
#[path = "common/support.rs"]
#[macro_use]
mod support;
#[path = "common/cache.rs"]
mod cache;
use std::path::Path;
part_cases!(accepted_cad_frame_layout;
    stc09 => "nist_stc_09_asme1_ap242-e4.stp",
    ftc07 => "nist_ftc_07_asme1_ap242-e2.stp",
    ftc10 => "nist_ftc_10_asme1_ap242-e2.stp",
    stc06 => "nist_stc_06_asme1_ap242-e3.stp",
    stc10 => "nist_stc_10_asme1_ap242-e2.stp",
    assembly => "assembly_plate_pin.step",
    hw_assembly => "hw_assembly_plate_pin_intent.step",
    angle => "specify-main-angle-bracket.step"
);
fn accepted_cad_frame_layout(file: &str) {
    let (size, scale, counts, crossings) = match file {
        "nist_stc_09_asme1_ap242-e4.stp" => ("A1", "1:2", [212, 66, 0, 0, 8, 0], [15, 67, 115]),
        "nist_ftc_07_asme1_ap242-e2.stp" => ("A1", "1:1", [13, 6, 3, 0, 8, 0], [4, 4, 5]),
        "nist_ftc_10_asme1_ap242-e2.stp" => ("A1", "1:1", [66, 25, 7, 0, 7, 0], [15, 17, 33]),
        "nist_stc_06_asme1_ap242-e3.stp" => ("A0", "1:1", [24, 0, 0, 0, 4, 1], [0, 8, 10]),
        // The attached cylindricity/position rows intentionally share an edge;
        // that pair no longer contributes a false label_crowded notice.
        "nist_stc_10_asme1_ap242-e2.stp" => ("A1", "1:1", [54, 19, 6, 1, 1, 0], [6, 14, 33]),
        "assembly_plate_pin.step" => ("A3", "1:1", [2, 2, 0, 1, 2, 0], [0, 0, 2]),
        "hw_assembly_plate_pin_intent.step" => ("A4", "1:2", [4, 1, 0, 0, 3, 0], [2, 0, 2]),
        "specify-main-angle-bracket.step" => ("A4", "1:1", [2, 0, 0, 0, 1, 0], [0, 2, 0]),
        _ => unreachable!(),
    };
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/pmi");
    let path = if file == "specify-main-angle-bracket.step" {
        fixture.join(format!("{file}.gz"))
    } else {
        fixture.join("step").join(format!("{file}.gz"))
    };
    let (_, a) = cache::native(&path);
    let p = cache::default_plan(&a, true);
    // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        (size, scale)
    );
    let codes = [
        "annotation_leader_crossing",
        "leader_through_feature",
        "leader_to_hidden_edge",
        "leader_across_view",
        "label_crowded",
        "annotation_leader_long",
    ];
    assert_eq!(
        codes.map(|code| p.problems.iter().filter(|q| q.code == code).count()),
        counts
    );
    assert_eq!(
        ["arrow", "dimension_line", "extension_line"].map(|kind| {
            p.problems
                .iter()
                .filter(|q| {
                    q.code == "annotation_leader_crossing"
                        && q.crossing_kinds.iter().any(|k| k == kind)
                })
                .count()
        }),
        crossings
    );
    assert!(!p.problems.iter().any(|q| matches!(
        q.code.as_str(),
        "annotation_off_sheet" | "pinned_view_off_sheet"
    )));
}
