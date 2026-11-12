//! R5 allocation.

#[path = "common/overflow.rs"]
mod overflow;

use draftwright::model::{Analysis, Spec};
use draftwright::{api, model};
use serde_json::{Value, json};
use std::path::Path;

fn analysed(name: &str) -> Analysis {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let fixture = root.join(format!("tests/fixtures/parts/{name}.step"));
    if fixture.is_file() {
        let features = root.join(format!("tests/fixtures/recognition/{name}.json"));
        return if features.is_file() {
            model::analyse_with(&fixture, Some(&features)).unwrap().1
        } else {
            model::analyse_native(&fixture).unwrap().1
        };
    }
    let corpus = std::env::var("DRAFTWRIGHT_CORPUS").unwrap();
    model::analyse_native(&Path::new(&corpus).join(name))
        .unwrap()
        .1
}

fn spec(value: Value) -> Spec {
    serde_json::from_value(value).unwrap()
}
fn value(a: &Analysis, s: &Spec) -> Value {
    serde_json::to_value(model::plan_recovered(a, s).unwrap()).unwrap()
}

#[test]
fn r5_assigned_section_keeps_parent_scale_and_principals() {
    let a = analysed("nist/nist_ftc_06_asme1_rd.stp");
    let s =
        spec(json!({"sheet":{"size":"A1"},"sheets":{"max":2},"views":{"section_aa":{"sheet":2}}}));
    let p = value(&a, &s);
    assert_eq!(p["sheets"].as_array().unwrap().len(), 1);
    let views = p["views"].as_array().unwrap();
    let section = views.iter().find(|v| v["id"] == "v:section_aa").unwrap();
    assert_eq!(section["sheet"], 2);
    assert_eq!(section["scale"], p["sheet"]["scale_factor"]);
    assert!(section["xref"].is_object() && section["cut"]["xref"].is_object());
    for v in views
        .iter()
        .filter(|v| ["v:front", "v:plan", "v:side"].contains(&v["id"].as_str().unwrap()))
    {
        assert!(v.get("sheet").is_none());
    }
    assert_eq!(p["spec"]["views"]["section_aa"]["sheet"], 2);
    assert!(p["spec"]["views"]["section_aa"].get("omit").is_none());
    assert!(!p["problems"].as_array().unwrap().iter().any(|p| {
        [
            "annotation_off_sheet",
            "views_overlap",
            "view_on_title_block",
            "cross_reference_missing",
        ]
        .contains(&p["code"].as_str().unwrap())
    }));
}

#[test]
fn r5_keep_together_rejects_splitting_a_principal() {
    let e = api::edit(
        &Spec::default(),
        &json!([
            {"op":"set","path":"sheets","value":{"max":2,"keep_together":[["side","section_aa"]]}},
            {"op":"set","path":"views/section_aa/sheet","value":2}
        ]),
    )
    .unwrap_err();
    assert!(
        e.to_string().contains("keep-together") && e.to_string().contains("side"),
        "{e}"
    );
}

#[test]
fn r5_relative_to_rejects_different_sheets() {
    let e = api::edit(&Spec::default(), &json!([
        {"op":"set","path":"sheets/max","value":2},
        {"op":"set","path":"views/iso","value":{"sheet":2,"place":{"relative_to":"front","side":"right"}}}
    ])).unwrap_err();
    assert!(e.to_string().contains("relative_to"), "{e}");
    assert!(e.to_string().contains("remove views/iso/place"), "{e}");
}

#[test]
fn r5_gaps_close_and_moved_views_keep_ids() {
    let a = analysed("plate");
    let original = model::plan_recovered(&a, &Spec::default()).unwrap();
    let p = value(
        &a,
        &spec(json!({"sheets":{"max":3},"views":{"iso":{"sheet":3}}})),
    );
    assert_eq!(p["sheets"].as_array().unwrap().len(), 1);
    assert_eq!(p["sheets"][0]["number"], 2);
    assert_eq!(
        p["views"]
            .as_array()
            .unwrap()
            .iter()
            .find(|v| v["id"] == "v:iso")
            .unwrap()["sheet"],
        2
    );
    let q = p["problems"]
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["code"] == "sheet_renumbered")
        .unwrap();
    assert_eq!(q["fix"][0]["path"], "views/iso/sheet");
    assert_eq!(q["fix"][0]["value"], 2);
    for d in &original.dimensions {
        assert!(
            p["dimensions"]
                .as_array()
                .unwrap()
                .iter()
                .any(|n| n["id"] == d.id)
        );
    }
}

#[test]
fn r5_hole_table_assignment_does_not_force_a_table() {
    let a = analysed("scattered_holes");
    let p = value(&a, &spec(json!({"sheets":{"max":2,"hole_table":2}})));
    assert!(p.get("hole_table").is_none());
    assert!(p.get("sheets").is_none());
    assert!(
        p["problems"]
            .as_array()
            .unwrap()
            .iter()
            .any(|p| p["code"] == "sheet_assignment_unused")
    );
}

#[test]
fn r5_sheet_numbers_and_scale_intents_are_validated() {
    let s = api::edit(
        &Spec::default(),
        &json!([{"op":"set","path":"sheets/max","value":2}]),
    )
    .unwrap();
    let e = api::edit(
        &s,
        &json!([{"op":"set","path":"views/iso/sheet","value":3}]),
    )
    .unwrap_err();
    assert!(e.to_string().contains("set sheets/max 3"));
    let e = api::edit(
        &s,
        &json!([{"op":"set","path":"views/front/sheet","value":2}]),
    )
    .unwrap_err();
    assert!(e.to_string().contains("Principal"));
    let e = api::edit(
        &s,
        &json!([
            {"op":"set","path":"sheet/scale","value":"1:5"},
            {"op":"set","path":"sheets/smallest_scale","value":"1:2"}
        ]),
    )
    .unwrap_err();
    assert!(e.to_string().contains("remove sheet/scale"));
    assert!(e.to_string().contains("set sheets/smallest_scale 1:5"));
}

#[test]
fn r5_corner_table_on_sheet_two_has_a_place() {
    let a = analysed("plate");
    let s = spec(
        json!({"sheets":{"max":2},"views":{"iso":{"sheet":2}},"tables":[{"id":"t:1","sheet":2,"prefer":"tr","rows":[["REV","CHANGE"],["A","ISSUED"]]}]}),
    );
    let p = value(&a, &s);
    assert_eq!(p["tables"][0]["sheet"], 2);
    assert!(p["tables"][0]["placed"].is_object());
    assert!(!p["problems"].as_array().unwrap().iter().any(|p| {
        ["table_not_placed", "views_overlap", "view_on_title_block"]
            .contains(&p["code"].as_str().unwrap())
    }));
    let edited = api::edit(&s, &json!([{"op":"set","path":"tables/0/sheet","value":1}])).unwrap();
    assert_eq!(
        serde_json::to_value(edited).unwrap()["tables"][0]["sheet"],
        1
    );
}

#[test]
fn r5_floor_is_reported_with_an_actionable_paper_fix() {
    let a = analysed("plate");
    let s = spec(
        json!({"sheet":{"size":"A4"},"sheets":{"smallest_scale":"10:1"},"views":{"iso":{"omit":true},"section_aa":{"omit":true}}}),
    );
    let p = value(&a, &s);
    let q = p["problems"]
        .as_array()
        .unwrap()
        .iter()
        .find(|p| p["code"] == "sheet_below_smallest_scale")
        .unwrap();
    assert!(
        q["fix"]
            .as_array()
            .unwrap()
            .iter()
            .any(|op| op["path"] == "sheet/size" && op["value"] == "A3")
    );
}

fn ap242(stem: &str) -> Analysis {
    use std::io::Read as _;
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let file = std::fs::read_dir(root.join("tests/fixtures/pmi/step"))
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| p.file_name().unwrap().to_string_lossy().split('.').next() == Some(stem))
        .unwrap();
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(&file).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join("sheets")
        .join(stem);
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join(
        file.file_name()
            .unwrap()
            .to_string_lossy()
            .trim_end_matches(".gz"),
    );
    std::fs::write(&path, bytes).unwrap();
    model::analyse_native(&path).unwrap().1
}

#[test]
fn r5_gdt_and_dimension_carriers_are_on_sheet_one() {
    for stem in ["nist_ftc_07_asme1_ap242-e2", "nist_ctc_01_asme1_ap242-e1"] {
        let a = ap242(stem);
        let p = model::plan_recovered(&a, &spec(json!({"sheets":{"max":2,"gdt_on_sheet_1":true},"views":{"iso":{"sheet":2},"section_aa":{"sheet":2}}}))).unwrap();
        assert_eq!(p.sheets.len(), 1, "{stem}");
        assert!(!p.gdt.is_empty());
        for view in p
            .dimensions
            .iter()
            .map(|d| &d.view)
            .chain(p.gdt.iter().map(|g| &g.view))
        {
            assert!(
                p.views
                    .iter()
                    .find(|v| &v.id == view)
                    .is_none_or(|v| v.sheet == 1)
            );
        }
        assert!(
            !draftwright::sheets::check(&p)
                .iter()
                .any(|p| p.code == "datum_not_identified"),
            "{stem}"
        );
        let items = draftwright::items::items(&p);
        assert!(
            items
                .iter()
                .filter(
                    |i| ["dimension", "control_frame", "datum_feature"].contains(&i.kind.as_str())
                )
                .all(|i| i.sheet == 1)
        );
        assert!(items.iter().any(|i| i.id == "v:iso" && i.sheet == 2));
    }
}

#[test]
fn r5_datum_rule_uses_sheets_instead_of_view_names() {
    let a = ap242("assembly_plate_pin");
    let mut p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let mut frame = p
        .gdt
        .iter()
        .find(|g| g.kind == "control_frame" && g.placed.is_some())
        .unwrap()
        .clone();
    let mut datum = p
        .gdt
        .iter()
        .find(|g| g.kind == "datum_feature" && g.placed.is_some())
        .unwrap()
        .clone();
    frame.cells = vec!["⌖".into(), "0.1".into(), "A".into()];
    datum.cells = vec!["A".into()];
    let mut view = p.views[0].clone();
    view.id = "v:carrier".into();
    view.sheet = 2;
    view.area = [10., 10., 30., 30.];
    view.cut = None;
    frame.view = view.id.clone();
    p.views.push(view);
    let mut sheet = p.sheet.clone();
    sheet.number = 2;
    sheet.projection_symbol = None;
    p.sheets.push(sheet);
    p.gdt = vec![frame, datum];
    assert!(
        !draftwright::sheets::check(&p)
            .iter()
            .any(|p| p.code == "datum_not_identified")
    );
    p.gdt[1].view = "v:carrier".into();
    assert!(
        !draftwright::sheets::check(&p)
            .iter()
            .any(|p| p.code == "datum_not_identified")
    );
    let mut third = p.views.last().unwrap().clone();
    third.id = "v:third".into();
    third.sheet = 3;
    p.views.push(third);
    let mut sheet = p.sheet.clone();
    sheet.number = 3;
    p.sheets.push(sheet);
    p.gdt[0].view = "v:third".into();
    let problems = draftwright::sheets::check(&p);
    let missing = problems
        .iter()
        .find(|p| p.code == "datum_not_identified")
        .unwrap();
    assert_eq!(missing.subjects, [p.gdt[0].id.clone()]);
    assert_eq!(missing.refs, ["A"]);
}

#[test]
fn r5_cross_reference_check_catches_either_missing_direction() {
    let a = analysed("nist/nist_ftc_06_asme1_rd.stp");
    let mut p = model::plan_recovered(
        &a,
        &spec(json!({"sheets":{"max":2},"views":{"section_aa":{"sheet":2}}})),
    )
    .unwrap();
    let i = p.views.iter().position(|v| v.id == "v:section_aa").unwrap();
    assert!(
        !draftwright::sheets::check(&p)
            .iter()
            .any(|p| p.code == "cross_reference_missing")
    );
    let from = p.views[i].xref.take();
    assert!(
        draftwright::sheets::check(&p)
            .iter()
            .any(|p| p.code == "cross_reference_missing")
    );
    p.views[i].xref = from;
    p.views[i].cut.as_mut().unwrap().xref = None;
    assert!(
        draftwright::sheets::check(&p)
            .iter()
            .any(|p| p.code == "cross_reference_missing")
    );
}

#[test]
fn r5_zones_follow_the_iso_grid_and_skip_i_and_o() {
    use draftwright::sheets::{zone, zones};
    for (page, columns, rows) in [
        ([297., 210.], 6, 4),
        ([420., 297.], 8, 6),
        ([594., 420.], 12, 8),
        ([841., 594.], 16, 12),
        ([1189., 841.], 24, 16),
    ] {
        assert_eq!(zones(page).columns, columns);
        assert_eq!(zones(page).rows, rows);
    }
    assert_eq!(zone([297., 210.], [0., 210.]), "A1");
    assert_eq!(zone([297., 210.], [296., 1.]), "D6");
    assert_eq!(zone([297., 210.], [151., 104.]), "C4");
    assert_eq!(zone([1189., 841.], [1189., 0.]), "R24");
}

#[test]
fn r5_parent_relative_groups_move_whole_and_keep_their_gap() {
    let a = analysed("nist/nist_ftc_06_asme1_rd.stp");
    let s = spec(
        json!({"sheets":{"max":2},"views":{"section_aa":{"sheet":2},"iso":{"place":{"relative_to":"section_aa","side":"right","gap":12,"align":"middle"}}}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    let section = p.views.iter().find(|v| v.id == "v:section_aa").unwrap();
    let iso = p.views.iter().find(|v| v.id == "v:iso").unwrap();
    assert_eq!(iso.sheet, section.sheet);
    assert!((iso.area[0] - section.area[2] - 12.).abs() < 1e-9);
    assert!((iso.area[1] + iso.area[3] - section.area[1] - section.area[3]).abs() < 1e-9);
}

#[test]
fn r5_an_auxiliary_shrinks_only_on_its_own_paper_and_reports_the_floor() {
    let a = analysed("prismatic");
    let s = spec(
        json!({"sheet":{"size":"A4","scale":"5:1"},"sheets":{"max":2,"smallest_scale":"5:1"},"dimension_source":"authored","views":{"plan":{"omit":true},"side":{"omit":true},"iso":{"sheet":2}}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    let iso = p.views.iter().find(|v| v.id == "v:iso").unwrap();
    assert!(iso.scale < p.sheet.scale_factor);
    assert!(iso.scale_note.is_none());
    assert!(
        p.captions
            .iter()
            .any(|c| c.sheet == iso.sheet && c.text == model::NTS_CAPTION)
    );
    assert_eq!(p.sheets[0].size, "A4");
    let floor = p
        .problems
        .iter()
        .find(|p| p.code == "sheet_below_smallest_scale")
        .unwrap();
    assert_eq!(floor.subjects, ["sheet:2"]);
    assert_eq!(floor.refs, ["v:iso"]);
    assert_eq!(floor.fix[0].path, "sheet/size");
    assert_eq!(floor.fix[0].value, Some(json!("A3")));
    let fixed = api::edit(&s, &serde_json::to_value(&floor.fix).unwrap()).unwrap();
    assert!(
        !model::plan_recovered(&a, &fixed)
            .unwrap()
            .problems
            .iter()
            .any(|p| p.code == "sheet_below_smallest_scale")
    );
}

#[test]
fn r5_principals_alone_below_the_floor_offer_larger_paper() {
    let a = analysed("plate");
    let s = spec(
        json!({"sheet":{"size":"A4"},"sheets":{"smallest_scale":"10:1","keep_together":[["front","iso"]]}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    let floor = p
        .problems
        .iter()
        .find(|p| p.code == "sheet_below_smallest_scale")
        .unwrap();
    assert_eq!(floor.fix[0].path, "sheet/size");
    assert_eq!(floor.fix[0].value, Some(json!("A3")));
}

#[test]
fn r5_the_bounded_ladder_skips_scales_below_the_floor() {
    use draftwright::layout::{self, Findings, Retry};
    let attempts = layout::recover(
        2.,
        [297., 210.],
        false,
        Findings {
            hard: 1,
            dropped: 0,
        },
        Some(2.),
        &mut |_| Some(Findings::default()),
    );
    assert_eq!(attempts[0].retry, Retry::Scale(5.));
    assert_eq!(attempts.len(), 1);
}

#[test]
fn r5_off_sheet_hole_table_keeps_all_rows_and_balloons() {
    use draftwright::hole_table::{self, TableOutcome};
    let a = analysed("scattered_holes");
    let mut p = model::plan(
        &a,
        &serde_json::from_value(serde_json::json!({"sheet":{"preferred_max_size":"A0"}})).unwrap(),
    )
    .unwrap();
    let id = p
        .dimensions
        .iter()
        .find(|d| {
            d.view == "v:plan"
                && d.placed.is_some()
                && d.kind == "linear"
                && d.measurements.iter().any(|m| {
                    m.feature.starts_with("f:hole:") && m.parameter.starts_with("location.length")
                })
        })
        .unwrap()
        .id
        .clone();
    p.problems.push(model::Problem {
        id: format!("stand-in:{id}"),
        code: "annotation_off_sheet".into(),
        severity: "warning".into(),
        subjects: vec![id],
        ..Default::default()
    });
    assert_eq!(
        hole_table::escalate(&a, &mut p, Some(2)),
        TableOutcome::Kept
    );
    let table = p.hole_table.as_mut().unwrap();
    assert_eq!(table.sheet, 2);
    assert!(table.blocks.is_empty());
    assert_eq!(table.rows.len(), 18);
    assert_eq!(p.balloons.len(), 18);
    assert!(p.balloons.iter().all(|b| b.view == "v:plan"));
    let covered: Vec<_> = p.dimensions.iter().filter(|d| matches!(d.state,draftwright::items::State::Covered { ref by } if by == hole_table::ID)).collect();
    assert!(!covered.is_empty());
    assert!(covered.iter().all(|d| d.placed.is_none()));
    let mut sheet = p.sheet.clone();
    sheet.number = 2;
    sheet.projection_symbol = None;
    table.blocks = hole_table::place_on_sheet(table, &sheet, &Spec::default()).unwrap();
    assert!(
        table
            .blocks
            .iter()
            .all(|b| b.sheet == 2 && b.placed.is_some())
    );
    p.sheets.push(sheet);
    assert!(!draftwright::sheets::check(&p).iter().any(|p| {
        [
            "annotation_off_sheet",
            "views_overlap",
            "view_on_title_block",
        ]
        .contains(&p.code.as_str())
    }));
}

#[test]
fn r5_moving_the_table_does_not_create_balloon_room() {
    use draftwright::hole_table::{self, TableOutcome};
    let a = analysed("nist/nist_ctc_02_asme1_rc.stp");
    let mut p = model::plan(&a, &Spec::default()).unwrap();
    let id = p
        .dimensions
        .iter()
        .find(|d| {
            d.view == "v:plan"
                && d.placed.is_some()
                && d.measurements.iter().any(|m| {
                    m.feature.starts_with("f:hole:") && m.parameter.starts_with("location.length")
                })
        })
        .unwrap()
        .id
        .clone();
    p.problems.push(model::Problem {
        id: format!("stand-in:{id}"),
        code: "annotation_off_sheet".into(),
        severity: "warning".into(),
        subjects: vec![id],
        ..Default::default()
    });
    assert_eq!(
        hole_table::escalate(&a, &mut p, Some(2)),
        TableOutcome::Balloons
    );
    assert!(p.hole_table.is_none());
    assert!(p.problems.iter().any(|p| p.code == "table_dropped"));
}

#[test]
fn r5_a_further_sheet_before_a_smaller_scale_uses_cumulative_prefixes() {
    let a = analysed("gramel/string_post.step");
    let base =
        spec(json!({"sheet":{"size":"A4"},"hole_table":"never","tables":overflow::tables()}));
    let before = model::plan_recovered(&a, &base).unwrap();
    assert_eq!(before.sheet.scale, "2:1");
    let s = spec(
        json!({"sheet":{"size":"A4"},"sheets":{"max":2,"smallest_scale":"5:1"},"hole_table":"never","tables":overflow::tables()}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert_eq!(p.sheet.scale, "5:1");
    assert_eq!(p.sheets.len(), 1);
    assert!(
        p.views
            .iter()
            .filter(|v| ["v:iso", "v:section_aa"].contains(&v.id.as_str()))
            .all(|v| v.sheet == 2)
    );
    for step in ["step 0:", "step 1:", "step 2:"] {
        assert!(p.sheet.basis.contains(step));
    }
    assert!(p.sheet.basis.contains("moved iso, section_aa"));
    assert!(p.tables.iter().any(|t| t.id == "t:1" && t.placed.is_some()));
    assert!(!p.problems.iter().any(|p| {
        [
            "sheet_below_smallest_scale",
            "table_not_placed",
            "annotation_off_sheet",
            "views_overlap",
            "view_on_title_block",
        ]
        .contains(&p.code.as_str())
    }));
    let single = spec(
        json!({"sheet":{"size":"A4"},"sheets":{"smallest_scale":"5:1"},"hole_table":"never","tables":overflow::tables()}),
    );
    let p = model::plan_recovered(&a, &single).unwrap();
    let floor = p
        .problems
        .iter()
        .find(|p| p.code == "sheet_below_smallest_scale")
        .unwrap();
    assert_eq!(floor.fix[0].path, "sheets/max");
    assert_eq!(floor.fix[0].value, Some(json!(2)));
}

#[test]
fn r5_first_usable_single_sheet_precedes_further_paper_and_sheets() {
    let a = analysed("gramel/string_post.step");
    let s = spec(
        json!({"sheets":{"max":2,"smallest_scale":"5:1"},"hole_table":"never","tables":overflow::tables()}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(p.sheet.scale_factor >= 5.);
    assert_eq!(p.sheet.size, "A4");
    assert!(p.sheets.is_empty());
    assert_eq!(p.sheet_candidates.len(), 1);
    assert_eq!(p.sheet_candidates[0].max, 1);
    assert!(
        p.sheet_candidates[0]
            .outcome
            .starts_with("A4: usable at 5:1")
    );
    assert!(p.tables.iter().all(|t| t.placed.is_some()));
}

#[test]
fn r5_a_helpful_prefix_spends_the_second_sheet_to_reach_the_floor() {
    // Until #163 the 10:1 trial on the allocated sheets was refused for an empty band past the
    // border, so one sheet at 5:1 was kept, below the authored floor. Judged by its ink, moving
    // the isometric and the section reaches the floor, so the second sheet is spent.
    let a = analysed("gramel/string_post.step");
    let s = spec(
        json!({"sheet":{"size":"A3"},"sheets":{"max":2,"smallest_scale":"10:1"},"hole_table":"never"}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.sheet.basis.contains("step 2: 0 hard findings"),
        "{}",
        p.sheet.basis
    );
    assert_eq!(p.sheets.len(), 1);
    assert_eq!(p.sheet.scale, "10:1", "{}", p.sheet.basis);
    assert!(
        !p.problems.iter().any(
            |p| p.code == "sheet_below_smallest_scale" || p.code == "layout_recovery_exhausted"
        )
    );
    for v in &p.views {
        let second = ["v:iso", "v:section_aa"].contains(&v.id.as_str());
        assert_eq!(v.sheet, if second { 2 } else { 1 }, "{}", v.id);
    }
}

#[test]
fn r5_an_unhelpful_prefix_does_not_spend_an_extra_sheet() {
    // With the section kept on sheet 1, the only prefix moves the isometric, which leaves the
    // drawing at 5:1 with the same finding: the second sheet does not help and is not spent.
    let a = analysed("gramel/string_post.step");
    let s = spec(json!({
        "sheet":{"size":"A3"},"sheets":{"max":2,"smallest_scale":"10:1"},"hole_table":"never",
        "views":{"section_aa":{"sheet":1}}
    }));
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(p.sheet.basis.contains("step 1:"), "{}", p.sheet.basis);
    assert_eq!(p.sheet.scale, "5:1");
    assert!(p.sheets.is_empty());
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "sheet_below_smallest_scale")
    );
    assert!(p.views.iter().all(|v| v.sheet == 1));
}

#[test]
fn r5_an_authored_sheet_label_is_reported_on_a_multi_sheet_plan() {
    let a = analysed("plate");
    for label in ["", "ANNEX"] {
        let s = spec(json!({
            "sheet":{"title":{"sheet":label}},
            "sheets":{"max":2},"views":{"iso":{"sheet":2}}
        }));
        let p = value(&a, &s);
        let q = p["problems"]
            .as_array()
            .unwrap()
            .iter()
            .find(|p| p["code"] == "sheet_label_replaced")
            .unwrap();
        assert_eq!(q["severity"], "info");
        assert_eq!(q["subjects"], json!(["sheet"]));
        assert_eq!(q["refs"], json!(["title_block/sheet"]));
        assert_eq!(q["fix"][0]["op"], "remove");
        assert_eq!(q["fix"][0]["path"], "sheet/title/sheet");
        assert_eq!(p["spec"]["sheet"]["title"]["sheet"], label);
    }
}

#[test]
fn r5_narrow_section_blocks_reserve_caption_and_reference_width() {
    let a = analysed("nist/nist_ftc_06_asme1_rd.stp");
    let s = spec(json!({
        "sheet":{"size":"A4","scale":"1:500"},"sheets":{"max":2},
        "dimension_source":"authored",
        "views":{"iso":{"omit":true},"section_aa":{"sheet":2}}
    }));
    let p = model::plan_recovered(&a, &s).unwrap();
    let section = p.views.iter().find(|v| v.id == "v:section_aa").unwrap();
    let reference = section.xref.as_ref().unwrap();
    let font = draftwright::text::Font::new(api::MONO.to_vec()).unwrap();
    for text in [
        "SECTION A–A".into(),
        format!("FROM SHEET {}, ZONE {}", reference.sheet, reference.zone),
    ] {
        let half = font.advance(&text, draftwright::dims::TEXT) / 2.;
        assert!(section.area[0] + draftwright::tables::PAD <= section.at[0] - half);
        assert!(section.area[2] - draftwright::tables::PAD >= section.at[0] + half);
        assert!(section.at[0] - half >= draftwright::layout::margin());
    }
    assert!(
        !draftwright::sheets::check(&p)
            .iter()
            .any(|p| p.code == "annotation_off_sheet")
    );
}

#[test]
fn r5_allowing_extra_sheets_preserves_the_finished_sheet() {
    for name in [
        "mfcadpp/1000.step",
        "gramel/GRM-03_thumbwheel_drive_screw.step",
        "gramel/string_post.step",
        "ttt_inputs/sm-hanger.step",
        "cadgenbench_inputs/cgb203.step",
        "mfcadpp/10020.step",
        "mfcadpp/10063.step",
    ] {
        let a = analysed(name);
        // Hold paper fixed while varying only R5's overflow permission. A free-paper
        // Spec now applies the A4 preference, while an explicit max retains old policy.
        let paper = model::plan_recovered(&a, &Spec::default())
            .unwrap()
            .sheet
            .size;
        let mut ordinary = value(&a, &spec(json!({"sheet":{"size":paper}})));
        let mut allowed = value(
            &a,
            &spec(json!({"sheet":{"size":paper},"sheets":{"max":2}})),
        );
        assert!(allowed.get("sheets").is_none(), "{name}");
        for p in [&mut ordinary, &mut allowed] {
            p.as_object_mut().unwrap().remove("spec");
            p["sheet"].as_object_mut().unwrap().remove("basis");
        }
        assert_eq!(ordinary, allowed, "{name}");
    }
}

#[test]
fn r5_remote_section_retains_parent_cutting_plane_ink() {
    let root = std::env::var("DRAFTWRIGHT_CORPUS").unwrap();
    let (part, a) =
        model::analyse_native(&Path::new(&root).join("gramel/string_post.step")).unwrap();
    let p = model::plan_recovered(
        &a,
        &spec(json!({"sheet":{"size":"A4","scale":"5:1"},"sheets":{"max":2},"views":{"section_aa":{"sheet":2}}})),
    )
    .unwrap();
    let font = draftwright::text::Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(draftwright::dims::FONT),
    )
    .unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    for name in [
        "section_line",
        "section_arrow_left",
        "section_arrow_right",
        "section_a_left",
        "section_a_right",
    ] {
        assert!(drawing.sheet.items.iter().any(|i| i.name == name), "{name}");
    }
    let [x0, y0, x1, y1] = drawing.sheet.drawable.unwrap();
    assert_eq!(
        drawing
            .text
            .iter()
            .filter(|t| t.text == "A"
                && t.at[0] >= x0
                && t.at[0] <= x1
                && t.at[1] >= y0
                && t.at[1] <= y1)
            .count(),
        2
    );
    assert!(
        drawing
            .text
            .iter()
            .any(|t| t.text.starts_with("SECTION A–A, SHEET 2, ZONE "))
    );
    for letter in drawing
        .sheet
        .items
        .iter()
        .filter(|i| ["section_a_left", "section_a_right"].contains(&i.name.as_str()))
    {
        for d in p.dimensions.iter().filter_map(|d| d.placed.as_ref()) {
            let a = letter.ink;
            let b = d.label_box;
            assert!(
                !(a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]),
                "letter overlaps a dimension label"
            );
        }
    }
}

#[test]
fn r5_table_sheet_edits_are_narrow_and_transactional() {
    let s = spec(json!({"sheets":{"max":2},"tables":[{"id":"t:1","rows":[["A"]],"sheet":2}]}));
    let removed = api::edit(&s, &json!([{"op":"remove","path":"tables/0/sheet"}])).unwrap();
    assert_eq!(removed.tables[0].sheet, None);
    for (path, value) in [
        ("tables/1/sheet", json!(1)),
        ("tables/0/sheet", json!(3)),
        ("tables/0/rows/0/0", json!("B")),
        ("tables/0", json!({"id":"t:2","rows":[["B"]]})),
    ] {
        assert!(
            api::edit(&s, &json!([{"op":"set","path":path,"value":value}])).is_err(),
            "{path}"
        );
    }
    assert!(api::edit(&s, &json!([{"op":"remove","path":"tables/0"}])).is_err());
    assert_eq!(s.tables[0].sheet, Some(2));
}

#[test]
fn r5_impossible_assignment_keeps_ink_and_reports_the_floor() {
    let mut a = analysed("prismatic");
    a.iso_bounds = [-1e9, -1e9, 1e9, 1e9];
    let s = spec(
        json!({"sheet":{"size":"A4","scale":"1:1"},"sheets":{"max":2,"smallest_scale":"1:1"},"views":{"iso":{"sheet":2}}}),
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(p.views.iter().any(|v| v.id == "v:iso" && v.sheet == 2));
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "sheet_below_smallest_scale" && p.subjects == ["sheet:2"])
    );
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "annotation_off_sheet" && p.subjects == ["v:iso"])
    );
}
