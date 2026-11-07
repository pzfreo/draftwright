//! Plan-time schedules replace crowded hole ladders, while the rescue remains independent.
#[path = "common/support.rs"]
#[macro_use]
mod support;

#[path = "common/cache.rs"]
mod cache;

use draftwright::{
    model::{self, Analysis, Plan, Spec},
    text::Font,
};
#[path = "common/corpus.rs"]
mod corpus;
use std::path::Path;
use std::sync::OnceLock;

fn plate() -> &'static (haecceity::Part, Analysis) {
    static PART: OnceLock<(haecceity::Part, Analysis)> = OnceLock::new();
    PART.get_or_init(|| {
        model::analyse_native(
            &Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/parts/scattered_holes.step"),
        )
        .unwrap()
    })
}
fn setting(value: &str) -> Spec {
    model::edit(
        &Spec::default(),
        &serde_json::json!([{"op":"set","path":"hole_table","value":value}]),
    )
    .unwrap()
}
fn tables(p: &Plan) -> serde_json::Value {
    serde_json::to_value(p)
        .unwrap()
        .get("hole_tables")
        .cloned()
        .unwrap_or_else(|| serde_json::json!([]))
}
#[test]
fn crowding_selects_a_schedule_before_any_item_is_dropped() {
    let p = model::plan_escalated(&plate().1, &Spec::default()).unwrap();
    assert!(tables(&p).as_array().is_some_and(|t| !t.is_empty()));
    assert!(!p.problems.iter().any(|p| p.code.ends_with("_dropped")));
}
#[test]
fn the_spec_selects_always_never_and_default_auto() {
    let a = model::plan_escalated(&plate().1, &setting("always")).unwrap();
    let n = model::plan_escalated(&plate().1, &setting("never")).unwrap();
    assert!(tables(&a).as_array().is_some_and(|t| !t.is_empty()));
    assert!(tables(&n).as_array().is_some_and(|t| t.is_empty()));
    assert!(
        model::edit(
            &Spec::default(),
            &serde_json::json!([{"op":"set","path":"hole_table","value":"sometimes"}])
        )
        .is_err()
    );
    assert_eq!(setting("auto"), Spec::default());
}
#[test]
fn a_pinned_callout_keeps_its_ink_when_others_are_tabulated() {
    let mut spec = setting("never");
    let before = model::plan_escalated(&plate().1, &spec).unwrap();
    let held = before
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .unwrap();
    spec = model::edit(&spec, &serde_json::json!([
        {"op":"set","path":"hole_table","value":"always"},
        {"op":"set","path":format!("dimensions/{}/place",draftwright::items::key(&held.id)),"value":{"pinned":true}}
    ])).unwrap();
    let p = model::plan_escalated(&plate().1, &spec).unwrap();
    let pin = p.dimensions.iter().find(|d| d.id == held.id).unwrap();
    assert!(pin.placed.is_some());
    assert!(!matches!(
        pin.state,
        draftwright::items::State::Covered { .. }
    ));
    assert!(tables(&p).as_array().is_some_and(|t| !t.is_empty()));
}
#[test]
fn rows_and_tags_credit_hole_size_count_and_both_locations() {
    let (part, analysis) = plate();
    let p = model::plan_escalated(analysis, &setting("always")).unwrap();
    let t = tables(&p);
    assert_eq!(t[0]["rows"].as_array().unwrap().len(), 18);
    assert!(
        t[0]["rows"]
            .as_array()
            .unwrap()
            .iter()
            .all(|r| r["cells"].as_array().unwrap().len() == 5
                && r["measurements"].as_array().unwrap().len() >= 3)
    );
    let font = Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let drawing = model::render(part, analysis, &p, &font).unwrap();
    for finding in draftwright::lint::lint(analysis, &drawing) {
        assert!(
            !matches!(
                finding.code.as_str(),
                "hole_requirement_missing" | "feature_not_located"
            ),
            "{}",
            finding.message
        );
    }
}
part_cases!(the_dense_acceptance_parts_have_no_hole_location_tower;
    nist_ctc_02_asme1_rc => "nist_ctc_02_asme1_rc",
    nist_ctc_04_asme1_rd => "nist_ctc_04_asme1_rd",
    nist_ftc_06_asme1_rd => "nist_ftc_06_asme1_rd",
);
fn the_dense_acceptance_parts_have_no_hole_location_tower(name: &str) {
    let Some(corpus) = corpus::dir() else { return };

    let (_, analysis) = cache::native(&Path::new(&corpus).join(format!("nist/{name}.stp")));
    let p = model::plan_escalated(&analysis, &Spec::default()).unwrap();
    assert!(
        tables(&p).as_array().is_some_and(|t| !t.is_empty()),
        "{name}"
    );
    let read = draftwright::bridge::holes::read(&analysis);
    for tag in p.balloons.iter().filter(|b| b.certifying) {
        let h = read
            .iter()
            .find(|(f, _)| tag.features.contains(&f.id))
            .unwrap()
            .1
            .as_ref()
            .unwrap();
        let v = p.views.iter().find(|v| v.id == tag.view).unwrap();
        let frame = haecceity::hlr::View::new(v.direction, v.up).unwrap();
        let centre = frame.map(std::array::from_fn(|k| {
            (analysis.envelope[0][k] + analysis.envelope[1][k]) * 0.5
        }));
        let at = frame.map(h.at);
        let hole = [
            v.at[0] + v.scale * (at[0] - centre[0]),
            v.at[1] + v.scale * (at[1] - centre[1]),
        ];
        let width = tag.tag.len() as f64 * draftwright::dims::TEXT * 0.6;
        let clearance = ((tag.at[0] - hole[0]).abs() - width * 0.5)
            .max(0.0)
            .hypot(((tag.at[1] - hole[1]).abs() - draftwright::dims::TEXT * 0.5).max(0.0));
        let opening = [
            h.diameter,
            h.counterbore.map_or(0.0, |r| r.diameter),
            h.spotface.map_or(0.0, |r| r.diameter),
            h.countersink.map_or(0.0, |r| r.diameter),
        ]
        .into_iter()
        .fold(0.0, f64::max);
        assert!(
            clearance > opening * v.scale * 0.5,
            "{name} {} crosses its hole's outer seat",
            tag.tag
        );
    }
    for v in ["v:front", "v:plan", "v:side"] {
        let n = p
            .dimensions
            .iter()
            .filter(|d| {
                d.view == v
                    && d.placed.is_some()
                    && d.measurements.iter().any(|m| {
                        m.feature.starts_with("f:hole")
                            && m.parameter.starts_with("location.length.")
                    })
            })
            .count();
        assert!(n <= 8, "{name} {v}: {n} location tiers");
    }
}

#[test]
fn an_omitted_hole_location_is_not_silently_reintroduced_by_a_table() {
    let analysis = &plate().1;
    let first = cache::default_plan(analysis, false);
    let id = first
        .dimensions
        .iter()
        .find(|d| d.id.starts_with("d:f:hole:") && d.id.ends_with("/location.length.x"))
        .unwrap()
        .id
        .clone();
    let spec = model::edit(&setting("always"), &serde_json::json!([{"op":"set", "path":format!("dimensions/{}/omit", draftwright::items::key(&id)),"value":true}])).unwrap();
    let plan = model::plan_escalated(analysis, &spec).unwrap();
    let feature = id.strip_prefix("d:").unwrap().split('/').next().unwrap();
    assert!(tables(&plan).as_array().unwrap().iter().all(|t| {
        t["rows"]
            .as_array()
            .unwrap()
            .iter()
            .all(|r| r["feature"] != feature)
    }));
    assert!(
        plan.omitted
            .iter()
            .any(|o| o.id == id && o.state == draftwright::items::State::OmittedByUser)
    );
}

#[test]
fn a_changed_coordinate_cannot_borrow_a_number_from_another_cell() {
    let (part, analysis) = plate();
    let plan = model::plan_escalated(analysis, &setting("always")).unwrap();
    let font = Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let mut drawing = model::render(part, analysis, &plan, &font).unwrap();
    let a = drawing
        .annotations
        .iter()
        .find(|a| a.name.starts_with("hole_schedule_") && a.name.ends_with("/x"))
        .unwrap();
    let feature = a.claims[0].feature.clone();
    for &run in &a.runs {
        drawing.text[run].text = "9999".into();
    }
    let findings = draftwright::lint::lint(analysis, &drawing);
    assert!(
        findings
            .iter()
            .any(|f| f.code == "feature_not_located" && f.subjects.contains(&feature)),
        "{findings:?}"
    );
    assert!(
        !findings
            .iter()
            .any(|f| f.code == "hole_requirement_missing")
    );
}

#[test]
fn a_schedule_without_a_proven_datum_corner_is_refused() {
    let mut analysis = plate().1.clone();
    analysis.principal_profiles.clear();
    let plan = model::plan_escalated(&analysis, &setting("always")).unwrap();
    assert!(tables(&plan).as_array().unwrap().is_empty());
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "hole_table_unavailable")
    );
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.kind == "callout" && d.placed.is_some())
    );
}

#[test]
fn approved_fit_limits_survive_in_each_size_cell() {
    let analysis = &plate().1;
    let ops: Vec<_> = analysis.features.iter().filter(|f| f.kind == "hole").map(|f| serde_json::json!({"op":"set","path":format!("features/{}/fit",draftwright::items::key(&f.id)),"value":"H7"})).collect();
    let mut spec = model::edit(&setting("always"), &serde_json::json!(ops)).unwrap();
    spec.sheet.fit_deviations = Some(true);
    let plan = model::plan_escalated(analysis, &spec).unwrap();
    assert!(
        tables(&plan)[0]["rows"]
            .as_array()
            .unwrap()
            .iter()
            .all(|r| {
                let cell = r["cells"][1].as_str().unwrap();
                cell.contains("H7 +") && cell.contains("\n0")
            })
    );
}

/// The maintainer's decision (2026-10-28): where a schedule exists, its row replaces a hole's
/// size callout even when the size comes from PMI. The row prints the callout's text (band, fit
/// or thread designation) exactly, and the PMI claim moves to the rows once, so nothing is lost
/// and nothing is stated twice.
#[test]
fn semantic_pmi_callouts_are_replaced_by_rows_that_carry_them() {
    let (_, analysis) = model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/editor_pmi.step"),
    )
    .unwrap();
    // H7's interior callouts clear the leader crossing that made auto select a schedule on
    // this part; request one, as the semantic-PMI rule is about callouts beside a schedule.
    let always = Spec {
        hole_table: draftwright::hole_table::Setting::Always,
        ..Spec::default()
    };
    let plan = model::plan_escalated(&analysis, &always).unwrap();
    assert!(tables(&plan).as_array().is_some_and(|t| !t.is_empty()));
    let mut expected = always.clone();
    expected.binding = Some(analysis.binding.clone());
    assert_eq!(plan.spec, expected);
    assert!(plan.sheet.default);
    assert!(!plan.problems.iter().any(|p| {
        p.code == "annotation_off_sheet" && plan.gdt.iter().any(|g| p.subjects.contains(&g.id))
    }));
    let semantic: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements.iter().any(|m| {
                m.feature.starts_with("pmi:")
                    && matches!(m.parameter.as_str(), "diameter" | "thread")
            })
        })
        .collect();
    assert_eq!(semantic.len(), 3);
    let rows: Vec<_> = plan.hole_tables.iter().flat_map(|t| &t.rows).collect();
    for d in &semantic {
        assert!(d.placed.is_none(), "{} is drawn beside its rows", d.id);
        let text = d
            .terms
            .iter()
            .filter(|t| !matches!(t.as_str(), "\n" | "\u{2028}"))
            .skip(usize::from(d.terms[0].ends_with('×')))
            .cloned()
            .collect::<Vec<_>>()
            .join(" ");
        let holes: Vec<_> = d
            .measurements
            .iter()
            .filter(|m| m.parameter == "bore.diameter")
            .map(|m| m.feature.as_str())
            .collect();
        for hole in &holes {
            let row = rows.iter().find(|r| r.feature == *hole).unwrap();
            assert_eq!(row.cells[1], text, "{hole}");
        }
        for claim in d
            .measurements
            .iter()
            .filter(|m| m.feature.starts_with("pmi:"))
        {
            let carried = rows
                .iter()
                .flat_map(|r| &r.measurements)
                .filter(|m| *m == claim)
                .count();
            assert_eq!(carried, 1, "{} is carried once", claim.feature);
        }
    }
    assert!(!plan.problems.iter().any(|p| p.code == "pmi_not_lowered"));
}

#[test]
fn a_bare_pin_on_a_table_covered_location_does_not_restore_the_ladder() {
    let analysis = &plate().1;
    let base = cache::default_plan(analysis, true);
    let covered = base.omitted.iter().find(|o| o.id.starts_with("d:f:hole:") && o.id.ends_with("/location.length.x") && matches!(&o.state,draftwright::items::State::Covered {by} if by.starts_with("hole_schedule_"))).unwrap();
    let spec = model::edit(&Spec::default(), &serde_json::json!([{"op":"set","path":format!("dimensions/{}/place",draftwright::items::key(&covered.id)),"value":{"pinned":true}}])).unwrap();
    let after = model::plan_recovered(analysis, &spec).unwrap();
    assert!(after.omitted.iter().any(
        |o| o.id == covered.id && matches!(o.state, draftwright::items::State::Covered { .. })
    ));
    assert!(
        after
            .problems
            .iter()
            .any(|p| p.code == "pinned_item_not_drawn")
    );
    for d in base.dimensions.iter().filter(|d| d.kind == "linear") {
        assert_eq!(
            d.placed,
            after
                .dimensions
                .iter()
                .find(|a| a.id == d.id)
                .unwrap()
                .placed,
            "{}",
            d.id
        );
    }
}

#[test]
fn non_hole_pins_keep_the_settled_schedule_and_isometric_frame() {
    let Some(corpus) = corpus::dir() else { return };
    let (_, analysis) = cache::native(&Path::new(&corpus).join("nist/nist_ftc_08_asme1_rc.stp"));
    let base = cache::default_plan(&analysis, true);
    assert!(!tables(&base).as_array().unwrap().is_empty());
    // v0.2.3 inserts inside rounds in face order. Keep the same complete
    // non-hole pin contract using the current radius presenter's actual ID.
    let radius = base
        .dimensions
        .iter()
        .find(|d| {
            d.placed.is_some()
                && d.measurements
                    .iter()
                    .any(|m| m.parameter == "fillet.radius")
        })
        .unwrap();
    let ops: Vec<_> = [
        "d:f:slot:3/slot_width.length",
        radius.id.as_str(),
        "d:f:slot:4/location_slot.length",
    ]
    .iter()
    .map(|id| serde_json::json!({"op":"set","path":format!("dimensions/{}/place",draftwright::items::key(id)),"value":{"pinned":true}}))
    .collect();
    let spec = model::edit(&Spec::default(), &serde_json::json!(ops)).unwrap();
    let after = model::plan_recovered(&analysis, &spec).unwrap();
    let before = tables(&base);
    let after_tables = tables(&after);
    assert_eq!(
        before.as_array().unwrap().len(),
        after_tables.as_array().unwrap().len()
    );
    for (before, after) in before
        .as_array()
        .unwrap()
        .iter()
        .zip(after_tables.as_array().unwrap())
    {
        // The basis reports pre-table leader conflicts, which held labels can change.
        // The printed statements, datum and actual table geometry must stay settled.
        for field in ["id", "origin", "rows", "covered", "state"] {
            assert_eq!(before[field], after[field], "{field}");
        }
        assert_eq!(before["blocks"][0]["placed"], after["blocks"][0]["placed"]);
    }
    for view in &base.views {
        let held = after.views.iter().find(|v| v.id == view.id).unwrap();
        assert_eq!((view.at, view.scale), (held.at, held.scale), "{}", view.id);
    }
    for (id, edit) in &spec.dimensions {
        assert!(edit.place.as_ref().unwrap().pinned);
        assert_eq!(
            base.dimensions.iter().find(|d| d.id == *id).unwrap().placed,
            after
                .dimensions
                .iter()
                .find(|d| d.id == *id)
                .unwrap()
                .placed,
            "{id}"
        );
    }
}

#[test]
fn table_recess_and_depth_symbols_are_drawn_inside_the_size_cell() {
    let Some(corpus) = corpus::dir() else { return };
    let (part, analysis) = cache::native(&Path::new(&corpus).join("nist/nist_ftc_06_asme1_rd.stp"));
    let plan = cache::default_plan(&analysis, true);
    let table = tables(&plan);
    assert!(
        table[0]["rows"][0]["cells"][1]
            .as_str()
            .unwrap()
            .contains('⌴')
    );
    let block = &table[0]["blocks"][0]["placed"];
    let at = &block["cells_at"][2][1];
    let cell = [
        block["columns"][1].as_f64().unwrap(),
        at[1].as_f64().unwrap() - draftwright::tables::ROW * 0.5,
        block["columns"][2].as_f64().unwrap(),
        at[1].as_f64().unwrap() + draftwright::tables::ROW * 0.5,
    ];
    let font = Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
    let strokes = drawing
        .layers
        .iter()
        .flat_map(|l| &l.items)
        .filter(|i| {
            let draftwright::output::Item::Edge(draftwright::output::Curve::Line(a, b)) = i else {
                return false;
            };
            [a, b].iter().all(|p| {
                p[0] > cell[0] + 1e-6
                    && p[0] < cell[2] - 1e-6
                    && p[1] > cell[1] + 1e-6
                    && p[1] < cell[3] - 1e-6
            })
        })
        .count();
    assert!(
        strokes >= 3,
        "size cell has no drafting-symbol strokes: {strokes}"
    );
}

/// The CI pin grid's seed-2 reproducer: held labels removed the canonical plan's leader
/// crossings, so the re-measured trigger dropped FTC-07's front schedule and its patterns
/// returned as ladders that ran past the view's area. Pins keep the canonical schedule.
#[test]
fn held_labels_do_not_remove_the_canonical_schedule() {
    let Some(corpus) = corpus::dir() else { return };
    let (_, analysis) = cache::native(&Path::new(&corpus).join("nist/nist_ftc_07_asme1_rd.stp"));
    let base = cache::default_plan(&analysis, true);
    assert_eq!(tables(&base)[0]["id"], "hole_schedule_front");
    let ops: Vec<_> = [
        "d:step_level/step_height.length.h218_024",
        "d:f:polygonal_boss:1/polygon_across_flats.length",
        "d:f:hole:8/location.length.y",
    ]
    .iter()
    .map(|id| serde_json::json!({"op":"set","path":format!("dimensions/{}/place",draftwright::items::key(id)),"value":{"pinned":true}}))
    .collect();
    let spec = model::edit(&Spec::default(), &serde_json::json!(ops)).unwrap();
    let after = model::plan_recovered(&analysis, &spec).unwrap();
    let (before, after_tables) = (tables(&base), tables(&after));
    assert_eq!(
        before.as_array().unwrap().len(),
        after_tables.as_array().unwrap().len()
    );
    for field in ["id", "origin", "rows", "covered", "state"] {
        assert_eq!(before[0][field], after_tables[0][field], "{field}");
    }
    let drawn = |p: &Plan| {
        p.dimensions
            .iter()
            .filter(|d| d.placed.is_some())
            .map(|d| d.id.clone())
            .collect::<std::collections::BTreeSet<_>>()
    };
    assert_eq!(drawn(&base), drawn(&after));
    for id in spec.dimensions.keys() {
        assert_eq!(
            base.dimensions.iter().find(|d| &d.id == id).unwrap().placed,
            after
                .dimensions
                .iter()
                .find(|d| &d.id == id)
                .unwrap()
                .placed,
            "{id}"
        );
    }
}

/// NIST CTC-03: its two obround slots' `2× SLOT` callout, laid out with the plan view's schedule,
/// once took the datum corner the schedule's X/Y origin mark needs, so the schedule failed at A3
/// 1:5 and the part went to A1. The presentation is planned again with the mark's place reserved:
/// the drawing keeps A3 1:5 with its schedule, and no annotation label stands on the origin mark.
#[test]
fn a_slot_callout_leaves_the_schedule_origin_its_corner() {
    let Some(corpus) = corpus::dir() else { return };
    let (_, analysis) = cache::native(&corpus.join("nist/nist_ctc_03_asme1_rc.stp"));
    let plan = cache::default_plan(&analysis, true);
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A3", "1:5")
    );
    assert!(!tables(&plan).as_array().unwrap().is_empty());
    let slot = plan
        .dimensions
        .iter()
        .find(|d| d.label == "2× SLOT 19.05 × 107.95")
        .expect("the slot callout");
    assert!(slot.placed.is_some(), "{slot:?}");
    let origin = plan
        .balloons
        .iter()
        .find(|b| b.id.ends_with("/origin"))
        .expect("the schedule's origin mark");
    let [x, y] = origin.at;
    let mark = [x - 2.0, y - 2.0, x + 12.0, y + 12.0];
    for d in &plan.dimensions {
        if let Some(p) = &d.placed {
            let b = p.label_box;
            assert!(
                b[2] <= mark[0] || b[0] >= mark[2] || b[3] <= mark[1] || b[1] >= mark[3],
                "{} stands on the origin mark",
                d.label
            );
        }
    }
}

#[test]
fn the_cgb245_view_pin_reproducer_keeps_its_schedule() {
    let Some(corpus) = corpus::dir() else { return };
    let (_, mut analysis) = cache::native(&corpus.join("cadgenbench_inputs/cgb245.step"));
    // The reproducer pins slot 1's own length: its congruent twin is split from the slot
    // callout, so the two are not called out together and slot 1 keeps that dimension.
    corpus::split_slot(&mut analysis, "f:slot:2");
    let base = cache::default_plan(&analysis, true);
    assert!(!tables(&base).as_array().unwrap().is_empty());
    let ordinary = model::plan_recovered(&analysis, &setting("never")).unwrap();
    let iso = base.views.iter().find(|v| v.id == "v:iso").unwrap();
    let before = ordinary.views.iter().find(|v| v.id == "v:iso").unwrap();
    // The printer policy can choose different paper with and without the schedule.
    // Orientation retention compares frames only when their paper and scale agree.
    if (&base.sheet.size, &base.sheet.scale) == (&ordinary.sheet.size, &ordinary.sheet.scale) {
        assert_eq!(
            (iso.at, iso.area, iso.scale),
            (before.at, before.area, before.scale)
        );
    }
    for k in 0..2 {
        assert!(iso.at[k] + iso.scale * analysis.iso_bounds[k] >= draftwright::layout::margin());
        assert!(
            iso.at[k] + iso.scale * analysis.iso_bounds[k + 2]
                <= base.sheet.page[k] - draftwright::layout::margin()
        );
    }
    let mut ops: Vec<_> = ["d:envelope/height.length", "d:f:slot:1/location_slot.length", "d:f:slot:1/slot_length.length", "d:f:slot:1/slot_width.length", "d:step_level/step_height.length.h13_226", "d:step_level/step_height.length.h9_226"].iter().map(|id| serde_json::json!({"op":"set", "path":format!("dimensions/{}/place",draftwright::items::key(id)), "value":{"pinned":true}})).collect();
    // Pin each view at the gap the default layout reports (as the editor does), so the
    // default gutter is replayed rather than replaced by an explicit zero.
    let area = |id: &str| base.views.iter().find(|v| v.id == id).unwrap().area;
    let front = area("v:front");
    let gaps = [area("v:plan")[1] - front[3], area("v:iso")[0] - front[2]];
    for ((view, side, align), gap) in [("plan", "above", "middle"), ("iso", "right", "start")]
        .into_iter()
        .zip(gaps)
    {
        ops.push(serde_json::json!({"op":"set", "path":format!("views/{view}/place"), "value":{"relative_to":"front", "side":side, "align":align, "gap":gap, "pinned":true}}));
    }
    let spec = model::edit(&Spec::default(), &serde_json::json!(ops)).unwrap();
    let after = model::plan_recovered(&analysis, &spec).unwrap();
    assert_eq!(tables(&base)[0]["rows"], tables(&after)[0]["rows"]);
    for id in spec.dimensions.keys() {
        assert_eq!(
            base.dimensions.iter().find(|d| &d.id == id).unwrap().placed,
            after
                .dimensions
                .iter()
                .find(|d| &d.id == id)
                .unwrap()
                .placed,
            "{id}"
        );
    }
}

part_cases!(rounded_datums_and_stacked_openings_are_unambiguous;
    nist_ctc_02_asme1_rc => "nist_ctc_02_asme1_rc",
    nist_ctc_04_asme1_rd => "nist_ctc_04_asme1_rd",
    nist_ctc_05_asme1_rd => "nist_ctc_05_asme1_rd",
);
fn rounded_datums_and_stacked_openings_are_unambiguous(name: &str) {
    let Some(corpus) = corpus::dir() else { return };

    let (_, analysis) = cache::native(&corpus.join(format!("nist/{name}.stp")));
    let plan = cache::default_plan(&analysis, true);
    assert!(!plan.hole_tables.is_empty(), "{name}");
    for table in &plan.hole_tables {
        assert_eq!(table.origin, Some(analysis.envelope[0]));
        for row in &table.rows {
            assert!(
                row.cells[1]
                    .split_whitespace()
                    .all(|t| !(t.parse::<f64>().is_ok() && t.ends_with(".0"))),
                "{name}: redundant native size precision"
            );
            assert!(
                row.cells[2..4].iter().all(|c| !c.ends_with(".0")),
                "{name}: redundant coordinate precision"
            );
            assert!(
                row.measurements
                    .iter()
                    .filter(|m| m.parameter.starts_with("location.length."))
                    .all(|m| m.value >= -1e-6)
            );
            for other in &table.rows {
                if row.feature != other.feature && row.cells[2..4] == other.cells[2..4] {
                    assert_ne!(
                        row.cells[4], other.cells[4],
                        "{name}: indistinguishable entry faces"
                    );
                    assert!(
                        plan.balloons
                            .iter()
                            .any(|b| b.features.contains(&row.feature)
                                && b.features.contains(&other.feature))
                    );
                }
            }
        }
    }
}

#[test]
fn an_incomplete_bolt_circle_states_pcd_and_each_angle() {
    let Some(corpus) = corpus::dir() else { return };
    let (_, analysis) = cache::native(&corpus.join("nist/nist_ctc_05_asme1_rd.stp"));
    let plan = cache::default_plan(&analysis, true);
    let table = plan
        .hole_tables
        .iter()
        .find(|t| t.view.as_deref() == Some("v:plan"))
        .unwrap();
    let circle = table.pcd.as_ref().unwrap();
    assert!((circle.diameter - 444.5).abs() < 1e-4);
    assert_eq!(circle.members.len(), 10);
    let mut angles: Vec<_> = table
        .rows
        .iter()
        .filter(|r| circle.members.contains(&r.feature))
        .map(|r| r.cells[5].trim_end_matches('°').parse::<f64>().unwrap())
        .collect();
    angles.sort_by(f64::total_cmp);
    assert_eq!(
        angles,
        [
            30., 60.004, 90., 119.996, 150., 210., 240.004, 270., 299.996, 330.
        ]
    );
    assert!(table.blocks[0].rows[0][1].contains("PCD"));
    assert!(
        !table.blocks[0]
            .rows
            .iter()
            .flatten()
            .any(|c| c.contains("EQ SP"))
    );
}

part_cases!(schedule_labels_clear_the_rendered_part_and_other_annotations;
    nist_ctc_02_asme1_rc => "nist/nist_ctc_02_asme1_rc.stp",
    nist_ctc_05_asme1_rd => "nist/nist_ctc_05_asme1_rd.stp",
    cgb245 => "cadgenbench_inputs/cgb245.step",
);
fn schedule_labels_clear_the_rendered_part_and_other_annotations(file: &str) {
    let Some(corpus) = corpus::dir() else { return };
    let font = Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();

    let (part, analysis) = cache::native(&corpus.join(file));
    let plan = cache::default_plan(&analysis, true);
    let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
    for issue in draftwright::lint::structural::check(&drawing) {
        if issue
            .annotation
            .as_ref()
            .is_some_and(|a| a.starts_with("hole_schedule_"))
            || issue
                .related
                .iter()
                .any(|a| a.starts_with("hole_schedule_"))
        {
            assert!(
                !matches!(
                    issue.code.as_str(),
                    "view_annotation_overlap" | "annotation_overlap" | "annotation_ink_overlap"
                ),
                "{file}: {issue:?}"
            );
        }
    }
}

#[test]
fn a_changed_entry_cell_is_reported_even_when_xy_is_correct() {
    let (part, analysis) = plate();
    let plan = model::plan_recovered(analysis, &setting("always")).unwrap();
    let font = Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let mut drawing = model::render(part, analysis, &plan, &font).unwrap();
    assert!(
        !draftwright::lint::lint(analysis, &drawing)
            .iter()
            .any(|i| i.code == "hole_table_entry_invalid")
    );
    let entry = drawing
        .annotations
        .iter()
        .find(|a| a.name.ends_with("/entry"))
        .unwrap();
    let feature = entry.claims[0].feature.clone();
    for &run in &entry.runs {
        drawing.text[run].text = "9999 / +X".into();
    }
    assert!(
        draftwright::lint::lint(analysis, &drawing)
            .iter()
            .any(|i| i.code == "hole_table_entry_invalid" && i.subjects.contains(&feature))
    );
}

// Every tag names one hole a reader can find: page positions come from the table's own X/Y
// cells and its drawn origin, as a reader would find them. A tag without a leader is nearer
// its own rim (and centre) than any other scheduled hole's; a leadered tag's arrow touches its
// own rim and no other.
part_cases!(each_tag_is_beside_or_leadered_to_its_own_hole;
    nist_ctc_02_asme1_rc => "nist_ctc_02_asme1_rc",
    nist_ctc_04_asme1_rd => "nist_ctc_04_asme1_rd",
    nist_ctc_05_asme1_rd => "nist_ctc_05_asme1_rd",
    nist_ftc_06_asme1_rd => "nist_ftc_06_asme1_rd",
);
fn each_tag_is_beside_or_leadered_to_its_own_hole(name: &str) {
    let Some(corpus) = corpus::dir() else { return };
    let number = |cell: &str| -> f64 {
        cell.split_whitespace()
            .find_map(|t| t.parse::<f64>().ok())
            .unwrap()
    };
    let opening = |cell: &str| -> f64 {
        cell.split('⌀')
            .skip(1)
            .filter_map(|t| t.split_whitespace().next()?.parse::<f64>().ok())
            .fold(0.0, f64::max)
    };

    let (_, analysis) = cache::native(&Path::new(&corpus).join(format!("nist/{name}.stp")));
    let p = cache::default_plan(&analysis, true);
    assert!(!p.hole_tables.is_empty(), "{name}");
    for t in &p.hole_tables {
        let view = p
            .views
            .iter()
            .find(|v| Some(&v.id) == t.view.as_ref())
            .unwrap();
        let origin = p
            .balloons
            .iter()
            .find(|b| b.id == format!("{}/origin", t.id))
            .and_then(|b| b.leader)
            .unwrap()[0];
        let holes: Vec<(&str, [f64; 2], f64)> = t
            .rows
            .iter()
            .map(|r| {
                (
                    r.feature.as_str(),
                    [
                        origin[0] + view.scale * number(&r.cells[2]),
                        origin[1] + view.scale * number(&r.cells[3]),
                    ],
                    opening(&r.cells[1]) * view.scale * 0.5,
                )
            })
            .collect();
        let dist = |a: [f64; 2], b: [f64; 2]| (a[0] - b[0]).hypot(a[1] - b[1]);
        for tag in p
            .balloons
            .iter()
            .filter(|b| b.id.starts_with(&format!("{}/tag/", t.id)))
        {
            let own: Vec<_> = holes
                .iter()
                .filter(|h| tag.features.iter().any(|f| f == h.0))
                .collect();
            let others: Vec<_> = holes
                .iter()
                .filter(|h| own.iter().all(|o| dist(o.1, h.1) > 1e-3))
                .collect();
            let (_, centre, radius) = *own[0];
            if let Some([tip, _]) = tag.leader {
                assert!(
                    (dist(tip, centre) - radius).abs() < 0.3,
                    "{name} {}: its leader does not end on its own rim",
                    tag.tag
                );
                for (_, c, r) in &others {
                    assert!(
                        dist(tip, *c) - r > 1.0,
                        "{name} {}: its leader ends at another hole",
                        tag.tag
                    );
                }
            } else {
                let gap = dist(tag.at, centre) - radius;
                assert!(gap < 8.0, "{name} {}: {gap:.1} mm from its hole", tag.tag);
                for (_, c, r) in &others {
                    assert!(
                        dist(tag.at, *c) - r > gap * 1.15 + 0.5,
                        "{name} {}: as near another hole as its own",
                        tag.tag
                    );
                }
            }
        }
    }
}

// The entry column is in the view's frame (X right, Y up, Z out of the page), as X and Y
// are: a side or front view never states a part-frame axis there, and lint rejects one.
part_cases!(entry_cells_use_the_view_frame_and_lint_checks_it;
    nist_ctc_02_asme1_rc => "nist_ctc_02_asme1_rc",
    nist_ftc_06_asme1_rd => "nist_ftc_06_asme1_rd",
);
fn entry_cells_use_the_view_frame_and_lint_checks_it(name: &str) {
    let Some(corpus) = corpus::dir() else { return };
    let font = Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();

    let (part, analysis) = cache::native(&Path::new(&corpus).join(format!("nist/{name}.stp")));
    let p = cache::default_plan(&analysis, true);
    let off_plan: Vec<_> = p
        .hole_tables
        .iter()
        .filter(|t| t.view.as_deref() != Some("v:plan"))
        .collect();
    assert!(!off_plan.is_empty(), "{name}");
    for t in &p.hole_tables {
        assert_eq!(t.blocks[0].rows[1][4], "Z OUT / DIR");
        for r in &t.rows {
            assert!(
                r.cells[4].ends_with("/ +Z") || r.cells[4].ends_with("/ −Z"),
                "{name} {} {}",
                r.tag,
                r.cells[4]
            );
        }
    }
    let mut drawing = model::render(&part, &analysis, &p, &font).unwrap();
    assert!(
        !draftwright::lint::lint(&analysis, &drawing)
            .iter()
            .any(|i| i.code == "hole_table_entry_invalid"),
        "{name}"
    );
    // The same number with the part-frame axis letter is wrong in this view's frame.
    let entry = drawing
        .annotations
        .iter()
        .find(|a| a.name.starts_with(&off_plan[0].id) && a.name.ends_with("/entry"))
        .unwrap()
        .clone();
    let row = off_plan[0]
        .rows
        .iter()
        .find(|r| entry.claims[0].feature == r.feature)
        .unwrap();
    let part_frame = row.cells[4].replace("Z", if name.contains("ctc_02") { "X" } else { "Y" });
    for &run in &entry.runs {
        drawing.text[run].text = part_frame.clone();
    }
    assert!(
        draftwright::lint::lint(&analysis, &drawing)
            .iter()
            .any(|i| i.code == "hole_table_entry_invalid"),
        "{name}: {part_frame}"
    );
}

#[test]
fn an_omitted_hole_size_has_no_schedule_row_or_claim() {
    let (part, analysis) = plate();
    let ordinary = model::plan(analysis, &setting("never")).unwrap();
    let callout = ordinary
        .dimensions
        .iter()
        .find(|d| d.id.starts_with("d:f:hole:") && d.kind == "callout")
        .unwrap();
    let features: Vec<_> = callout
        .measurements
        .iter()
        .filter(|m| m.parameter == "bore.diameter")
        .map(|m| m.feature.clone())
        .collect();
    assert!(!features.is_empty());
    let spec = model::edit(&setting("always"), &serde_json::json!([{"op":"set", "path":format!("dimensions/{}/omit", draftwright::items::key(&callout.id)), "value":true}])).unwrap();
    let plan = model::plan_escalated(analysis, &spec).unwrap();
    assert!(!plan.hole_tables.is_empty());
    assert!(
        plan.hole_tables
            .iter()
            .flat_map(|t| &t.rows)
            .all(|r| !features.contains(&r.feature))
    );
    assert!(
        plan.omitted
            .iter()
            .any(|o| o.id == callout.id && o.state == draftwright::items::State::OmittedByUser)
    );
    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    let drawing = model::render(part, analysis, &plan, &font).unwrap();
    assert!(
        !drawing
            .annotations
            .iter()
            .flat_map(|a| &a.claims)
            .any(|c| features.contains(&c.feature) && c.parameter == "bore.diameter")
    );
    assert!(
        draftwright::lint::lint(analysis, &drawing)
            .iter()
            .any(|i| i.code == "hole_requirement_missing"
                && i.subjects.iter().any(|f| features.contains(f)))
    );
}

#[test]
fn damaged_size_cells_lose_size_through_and_quantity_evidence() {
    let (part, analysis) = plate();
    let plan = model::plan_escalated(analysis, &setting("always")).unwrap();
    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    let original = model::render(part, analysis, &plan, &font).unwrap();
    let size = original
        .annotations
        .iter()
        .find(|a| a.name.starts_with("hole_schedule_") && a.name.ends_with("/size"))
        .unwrap();
    let feature = &size.claims[0].feature;
    for damage in ["wrong", "empty", "missing", "role", "approval", "binding"] {
        let mut drawing = original.clone();
        let index = drawing
            .annotations
            .iter()
            .position(|a| a.name == size.name)
            .unwrap();
        match damage {
            "wrong" => drawing.text[size.runs[0]].text = "⌀9999 THRU".into(),
            "empty" => drawing.annotations[index].runs.clear(),
            "missing" => {
                drawing.annotations.remove(index);
            }
            "role" => {
                drawing.annotations[index].claims[0].parameter = "bolt_circle.diameter".into()
            }
            "approval" => drawing.annotations[index].approved_text = None,
            "binding" => drawing.annotations[index].approved_claims.clear(),
            _ => unreachable!(),
        }
        let findings = draftwright::lint::lint(analysis, &drawing);
        for parameter in ["bore.diameter", "bore.through", "grouping.count"] {
            assert!(
                findings.iter().any(|i| i
                    .requirements
                    .iter()
                    .any(|r| &r.feature == feature && r.parameter == parameter)),
                "{damage} {parameter}: {findings:?}"
            );
        }
    }
}

#[test]
fn approved_diameter_tolerances_reach_every_scheduled_size_cell_verbatim() {
    let (part, analysis) = plate();
    let ordinary = model::plan(analysis, &setting("never")).unwrap();
    let ops: Vec<_> = ordinary.dimensions.iter().filter(|d| d.kind == "callout" && d.measurements.iter().any(|m| m.parameter == "bore.diameter"))
        .enumerate().map(|(i,d)| serde_json::json!({"op":"set", "path":format!("dimensions/{}/tolerance",draftwright::items::key(&d.id)), "value":format!("±0.{:02}", i+11)})).collect();
    assert!(!ops.is_empty());
    let spec = model::edit(&setting("always"), &serde_json::json!(ops)).unwrap();
    let plan = model::plan_escalated(analysis, &spec).unwrap();
    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    let drawing = model::render(part, analysis, &plan, &font).unwrap();
    let rows: Vec<_> = plan.hole_tables.iter().flat_map(|t| &t.rows).collect();
    assert_eq!(rows.len(), 18);
    for row in rows {
        let source = plan
            .dimensions
            .iter()
            .find(|d| {
                d.kind == "callout"
                    && d.measurements
                        .iter()
                        .any(|m| m.feature == row.feature && m.parameter == "bore.diameter")
            })
            .unwrap();
        let band = source.terms.iter().find(|t| t.starts_with('±')).unwrap();
        assert!(
            row.cells[1].contains(band),
            "{}: {}",
            row.feature,
            row.cells[1]
        );
        let cell = drawing
            .annotations
            .iter()
            .find(|a| a.name.ends_with(&format!("/{}/size", row.tag)))
            .unwrap();
        let ink = cell
            .runs
            .iter()
            .map(|&i| drawing.text[i].text.as_str())
            .collect::<Vec<_>>()
            .join(" ");
        assert_eq!(ink, row.cells[1]);
    }
}

#[test]
fn drawing_consumer_observes_real_ink_and_reports_failed_builds_unknown() {
    use draftwright::evaluation::{State, drawing_consumer};
    let (part, analysis) = plate();
    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    for setting_name in ["always", "never"] {
        let plan = model::plan_escalated(analysis, &setting(setting_name)).unwrap();
        let original = model::render(part, analysis, &plan, &font).unwrap();
        let observed = drawing_consumer(analysis, Ok(std::slice::from_ref(&original)));
        assert_eq!(
            (observed.supported, observed.unsupported, observed.unknown),
            (18, 0, 0)
        );
        assert!(observed.holes.iter().all(|h| !h.carriers.is_empty()));
        for damage in ["empty", "wrong", "missing", "quantity"] {
            let mut drawing = original.clone();
            if damage == "missing" {
                drawing
                    .annotations
                    .retain(|a| !a.claims.iter().any(|c| c.parameter == "bore.diameter"));
            } else {
                let runs: Vec<_> = drawing
                    .annotations
                    .iter()
                    .filter(|a| a.claims.iter().any(|c| c.parameter == "bore.diameter"))
                    .flat_map(|a| &a.runs)
                    .copied()
                    .collect();
                for run in runs {
                    drawing.text[run].text = match damage {
                        "empty" => "",
                        "quantity" => "5×",
                        _ => "18× ⌀9999 THRU",
                    }
                    .into();
                }
            }
            let observed = drawing_consumer(analysis, Ok(&[drawing]));
            assert_eq!(
                (observed.supported, observed.unsupported, observed.unknown),
                (0, 18, 0),
                "{setting_name}/{damage}"
            );
            assert!(
                observed
                    .holes
                    .iter()
                    .all(|h| h.carriers.is_empty() && h.reason.is_some())
            );
        }
    }
    let failed = drawing_consumer(analysis, Err("renderer failed"));
    assert_eq!(
        (failed.supported, failed.unsupported, failed.unknown),
        (0, 0, 18)
    );
    assert_eq!(failed.build_error.as_deref(), Some("renderer failed"));
    assert!(
        failed
            .holes
            .iter()
            .all(|h| h.state == State::Unknown && h.reason.as_deref() == Some("renderer failed"))
    );
    let review = draftwright::api::review_analysed(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/scattered_holes.step"),
        analysis,
        &setting("always"),
    )
    .unwrap();
    assert_eq!(review.drawing_consumer.supported, 18);
}

#[test]
fn schedule_cell_evidence_checks_approved_tolerance_precision_and_through_wording() {
    let (part, analysis) = plate();
    let ordinary = model::plan(analysis, &setting("never")).unwrap();
    let callout = ordinary
        .dimensions
        .iter()
        .find(|d| d.kind == "callout" && d.id.starts_with("d:f:hole:"))
        .unwrap();
    let spec = model::edit(&setting("always"), &serde_json::json!([{"op":"set", "path":format!("dimensions/{}/tolerance", draftwright::items::key(&callout.id)), "value":"±0.02"}])).unwrap();
    let plan = model::plan_escalated(analysis, &spec).unwrap();
    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    let original = model::render(part, analysis, &plan, &font).unwrap();
    let cell = original
        .annotations
        .iter()
        .find(|a| {
            a.approved_text
                .as_ref()
                .is_some_and(|t| t.contains("±0.02"))
        })
        .unwrap();
    assert!(
        !draftwright::lint::lint(analysis, &original)
            .iter()
            .any(|i| i.code == "claimed_value_absent")
    );
    let approved = cell.approved_text.as_ref().unwrap();
    for actual in [
        approved.replace("±0.02", "±0.2"),
        approved.replace(" ±0.02", ""),
        approved.replace("THRU", ""),
        approved.replace("±0.02", "±0.020"),
    ] {
        let mut drawing = original.clone();
        drawing.text[cell.runs[0]].text = actual.clone();
        let issues = draftwright::lint::lint(analysis, &drawing);
        assert!(
            issues
                .iter()
                .any(|i| i.code == "claimed_value_absent"
                    && i.annotation.as_deref() == Some(&cell.name)),
            "{actual}: {issues:?}"
        );
        // The authored callout is additive evidence and may still document this hole;
        // the damaged table cell itself must lose every claim.
        assert!(
            draftwright::lint::marks(&drawing)
                .iter()
                .find(|m| m.name == cell.name)
                .unwrap()
                .claims
                .is_empty()
        );
    }
}

#[test]
fn a_schedule_coordinate_cannot_change_its_approved_axis_or_owner() {
    let (part, analysis) = plate();
    let plan = model::plan_escalated(analysis, &setting("always")).unwrap();
    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    let original = model::render(part, analysis, &plan, &font).unwrap();
    let index = original
        .annotations
        .iter()
        .position(|a| a.name.starts_with("hole_schedule_") && a.name.ends_with("/x"))
        .unwrap();
    for damage in ["axis", "owner", "binding"] {
        let mut drawing = original.clone();
        let cell = &mut drawing.annotations[index];
        let name = cell.name.clone();
        match damage {
            "axis" => cell.claims[0].parameter = "location.length.y".into(),
            "owner" => cell.claims[0].feature = "f:hole:foreign".into(),
            "binding" => cell.approved_claims.clear(),
            _ => unreachable!(),
        }
        let issues = draftwright::lint::lint(analysis, &drawing);
        assert!(issues.iter().any(|i| i.code == "claimed_value_absent" && i.annotation.as_deref() == Some(&name)), "{damage}: {issues:?}");
        assert!(
            draftwright::lint::marks(&drawing)
                .iter()
                .find(|m| m.name == name)
                .unwrap()
                .claims
                .is_empty()
        );
    }
}

/// #127's expanded radii make the recovered FTC-08 drawing tabular while its
/// primitive paper trial can still have a hole ladder. A pin on a new radius
/// and an existing view must retain the published size carrier, not that trial.
#[test]
fn a_profile_radius_and_view_pin_keep_the_recovered_hole_schedule() {
    let Some(corpus) = corpus::dir() else { return };
    let (_, analysis) = cache::native(&corpus.join("nist/nist_ftc_08_asme1_rc.stp"));
    let base = cache::default_plan(&analysis, true);
    let mut spec = Spec::default();
    for id in [
        "d:f:slot:3/location_slot_across.length",
        "d:f:slot:2/slot_end_radius.radius",
        "d:f:cylindrical_profile:11/cylindrical_profile.radius",
        "d:f:slot:1/location_slot.length",
        "d:f:section_recess:1/seat_diameter.diameter",
        "d:envelope/width.length",
        "d:f:plate:1/thickness.length",
    ] {
        assert!(
            base.dimensions
                .iter()
                .any(|d| d.id == id && d.placed.is_some())
        );
        spec.dimensions.entry(id.into()).or_default().place =
            Some(serde_json::from_value(serde_json::json!({"pinned":true})).unwrap());
    }
    let front = base.views.iter().find(|v| v.id == "v:front").unwrap();
    let side = base.views.iter().find(|v| v.id == "v:side").unwrap();
    spec.views.entry("side".into()).or_default().place = Some(
        serde_json::from_value(serde_json::json!({
            "relative_to":"front", "side":"right", "align":"projection",
            "gap":side.area[0]-front.area[2], "pinned":true
        }))
        .unwrap(),
    );
    let after = model::plan_recovered(&analysis, &spec).unwrap();
    let before_tables = tables(&base);
    let after_tables = tables(&after);
    assert!(!before_tables.as_array().unwrap().is_empty());
    assert_eq!(
        before_tables.as_array().unwrap().len(),
        after_tables.as_array().unwrap().len()
    );
    for (before, after) in before_tables
        .as_array()
        .unwrap()
        .iter()
        .zip(after_tables.as_array().unwrap())
    {
        for field in ["id", "origin", "rows", "covered", "state"] {
            assert_eq!(before[field], after[field], "{field}");
        }
    }
    for id in spec.dimensions.keys() {
        assert_eq!(
            base.dimensions.iter().find(|d| &d.id == id).unwrap().placed,
            after
                .dimensions
                .iter()
                .find(|d| &d.id == id)
                .unwrap()
                .placed,
            "{id}"
        );
    }
    assert!(!after.problems.iter().any(|p| matches!(
        p.code.as_str(),
        "annotation_overlap" | "annotation_ink_overlap" | "annotation_leader_crossing"
    )));
}

fn columns(n: serde_json::Value) -> Result<Spec, draftwright::model::Invalid> {
    model::edit(
        &setting("always"),
        &serde_json::json!([{"op":"set","path":"sheet/table_columns","value":{"hole_schedule_plan":n}}]),
    )
}

#[test]
fn a_schedule_that_fits_is_one_block_and_the_spec_can_split_it_in_order() {
    let (part, analysis) = plate();
    let whole = model::plan_escalated(analysis, &setting("always")).unwrap();
    let schedule = |p: &Plan| {
        p.hole_tables
            .iter()
            .find(|t| t.id == "hole_schedule_plan")
            .cloned()
            .unwrap()
    };
    // It fits the sheet whole: no split, the block is the table itself.
    let one = schedule(&whole);
    assert_eq!(one.blocks.len(), 1);
    assert_eq!(one.blocks[0].id, "hole_schedule_plan");
    assert_eq!(one.blocks[0].rows.len(), one.rows.len() + 2);
    // `1` never splits: the same single block.
    let fixed = model::plan_escalated(analysis, &columns(1.into()).unwrap()).unwrap();
    assert_eq!(schedule(&fixed).blocks.len(), 1);

    let p = model::plan_escalated(analysis, &columns(2.into()).unwrap()).unwrap();
    let t = schedule(&p);
    assert_eq!(t.blocks.len(), 2);
    let headers = &one.blocks[0].rows[..2];
    let per = t.rows.len().div_ceil(2);
    let mut read = Vec::new();
    for (k, block) in t.blocks.iter().enumerate() {
        assert_eq!(block.id, format!("hole_schedule_plan/{}", k + 1));
        // Each block repeats the title and header rows.
        assert_eq!(&block.rows[..2], headers, "block {k}");
        assert_eq!(block.rows.len(), per + 2);
        read.extend(
            block.rows[2..]
                .iter()
                .filter(|r| r.iter().any(|c| !c.is_empty()))
                .cloned(),
        );
    }
    // Rows keep their order, reading down the first block and then the second.
    let rows: Vec<_> = t.rows.iter().map(|r| r.cells.clone()).collect();
    assert_eq!(read, rows);
    // Side by side, left to right, tops aligned, clear of each other.
    let [a, b] = [0, 1].map(|k| t.blocks[k].placed.as_ref().unwrap().cells);
    assert!(a[2] < b[0]);
    assert!((a[3] - b[3]).abs() < 1e-9 && (a[1] - b[1]).abs() < 1e-9);
    // Every row's hole is still credited from its own block's cells.
    let font = Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let drawing = model::render(part, analysis, &p, &font).unwrap();
    for finding in draftwright::lint::lint(analysis, &drawing) {
        assert!(
            !matches!(
                finding.code.as_str(),
                "hole_requirement_missing" | "feature_not_located" | "hole_table_entry_invalid"
            ),
            "{}",
            finding.message
        );
    }
}

#[test]
fn table_columns_names_a_hole_table_and_a_count_from_one_to_four() {
    assert!(columns(0.into()).is_err());
    assert!(columns(5.into()).is_err());
    assert!(columns(4.into()).is_ok());
    assert!(
        model::edit(
            &Spec::default(),
            &serde_json::json!([{"op":"set","path":"sheet/table_columns","value":{"t:1":2}}]),
        )
        .is_err()
    );
}
