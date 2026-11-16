//! Exact reviewed CAD-frame layout regressions; information and sheet bounds remain guarded.
#[path = "common/support.rs"]
#[macro_use]
mod support;
use crate::cache;
use std::path::Path;
part_cases!(accepted_cad_frame_layout;
    stc09 => "nist_stc_09_asme1_ap242-e4.stp",
    stc09_mm => "nist_stc_09_asme1_ap242-e4.stp:mm",
    ftc07 => "nist_ftc_07_asme1_ap242-e2.stp",
    ftc07_mm => "nist_ftc_07_asme1_ap242-e2.stp:mm",
    ftc10 => "nist_ftc_10_asme1_ap242-e2.stp",
    stc06 => "nist_stc_06_asme1_ap242-e3.stp",
    stc10 => "nist_stc_10_asme1_ap242-e2.stp",
    assembly => "assembly_plate_pin.step",
    hw_assembly => "hw_assembly_plate_pin_intent.step",
    angle => "specify-main-angle-bracket.step"
);
fn accepted_cad_frame_layout(file: &str) {
    let (size, scale, counts, crossings) = match file {
        // Retain main's exact metric regression alongside the declared-inch default.
        "nist_stc_09_asme1_ap242-e4.stp:mm" => ("A1", "1:2", [212, 66, 0, 0, 8, 0], [15, 67, 115]),
        // 2026-11-15: declared inches select ANSI. The exhausted seed at C 1:12
        // was below the readable floor; recovery now keeps the measured fitting
        // E 1:1 candidate, preserving every carried requirement. These exact
        // lower counts retain its remaining source-PMI conflicts for review.
        // 2026-11-16: the checked interior slot-size retry removes one
        // dimension-line and one witness crossing, keeping all views/requirements.
        // Pending maintainer acceptance (not accepted): the f59 seat-diameter
        // callout's leader is 94.7 paper mm (75 mm limit); f94's slot-width
        // callout runs 42.5 mm across front, crossing 5 lines versus 1 on exit.
        // Sixteen bounded offset trials shortened/flipped these routes, but
        // increased extension-line crossings and displaced source-PMI frames.
        // Keep the exact remaining counts until a no-regression route exists.
        "nist_stc_09_asme1_ap242-e4.stp" => ("ANSI E", "1:1", [80, 30, 0, 1, 5, 1], [5, 26, 46]),
        // 2026-11-14: below-band preference restores the approved A2 1:2.
        // 2026-11-14: repair clears 3 crossings and 4 feature contacts.
        // 2026-11-15, Paul accepted: on A2 1:2 the 4x 7.137 counterbore callout's leader crosses
        // one more of its own front view's inner wall lines than the shortest route
        // (leader_across_view 0 -> 1). Three bounded attempts (placement, view re-spacing,
        // ownership) found no clean route, and the drawing reads cleanly (#478).
        "nist_ftc_07_asme1_ap242-e2.stp:mm" => ("A2", "1:2", [10, 2, 3, 1, 8, 0], [0, 4, 2]),
        // 2026-11-15: declared inches select ANSI C at 1:2. Normal datum
        // bends and a final pattern-column retry clear the witness band without
        // tilting scoped rings. The final retry also clears three crossings and
        // eight extension-through-feature findings from the first inch repair.
        // All remaining dimension-stroke contacts stay exact and within main's limits.
        "nist_ftc_07_asme1_ap242-e2.stp" => ("ANSI C", "1:2", [4, 0, 3, 0, 0, 0], [0, 4, 0]),
        // 2026-11-14: repair preserving shared rows clears 11 crossings and 13 feature contacts.
        "nist_ftc_10_asme1_ap242-e2.stp" => ("A1", "1:1", [55, 12, 7, 0, 3, 0], [15, 16, 31]),
        // 2026-11-14: bounded frame repair clears 7 crossings at the unchanged A0 1:1.
        "nist_stc_06_asme1_ap242-e3.stp" => ("A0", "1:1", [17, 0, 0, 0, 4, 1], [0, 6, 9]),
        // The attached cylindricity/position rows intentionally share an edge;
        // that pair no longer contributes a false label_crowded notice.
        // 2026-11-14: repair preserving shared rows clears 21 crossings and 19 feature contacts.
        "nist_stc_10_asme1_ap242-e2.stp" => ("A1", "1:1", [33, 0, 6, 1, 1, 0], [1, 5, 26]),
        "assembly_plate_pin.step" => ("A3", "1:1", [2, 2, 0, 1, 2, 0], [0, 0, 2]),
        "hw_assembly_plate_pin_intent.step" => ("A4", "1:2", [4, 1, 0, 0, 3, 0], [2, 0, 2]),
        "specify-main-angle-bracket.step" => ("A4", "1:1", [2, 0, 0, 0, 1, 0], [0, 2, 0]),
        _ => unreachable!(),
    };
    let metric = file.ends_with(":mm");
    let file = file.strip_suffix(":mm").unwrap_or(file);
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/pmi");
    let path = if file == "specify-main-angle-bracket.step" {
        fixture.join(format!("{file}.gz"))
    } else {
        fixture.join("step").join(format!("{file}.gz"))
    };
    let (_, a) = cache::native(&path);
    let p = if metric {
        draftwright::model::plan_recovered(
            &a,
            &draftwright::model::Spec {
                units: draftwright::units::Units::Mm,
                ..Default::default()
            },
        )
        .unwrap()
    } else {
        cache::default_plan(&a, true)
    };
    // Exact layout pins; cases still awaiting maintainer acceptance are marked above.
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
