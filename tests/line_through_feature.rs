//! Shaft routing regressions from the Stage F draftsman review. Test the drawn geometry as
//! well as its problems: a missing warning must not make a bad route look clear.
#[path = "common/corpus.rs"]
mod corpus;

#[path = "common/local_fix.rs"]
mod local_fix;

use draftwright::model::{self, Plan, Spec};
use serde_json::json;
use std::path::Path;

type P2 = [f64; 2];

fn shaft(plan: &Plan, id: &str) -> [P2; 2] {
    plan.dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap()
        .placed
        .as_ref()
        .unwrap()
        .lines[0]
}

fn circle_crossing([a, b]: [P2; 2], centre: P2, radius: f64) -> bool {
    let d = [b[0] - a[0], b[1] - a[1]];
    let t = (((centre[0] - a[0]) * d[0] + (centre[1] - a[1]) * d[1]) / (d[0] * d[0] + d[1] * d[1]))
        .clamp(0.0, 1.0);
    (a[0] + t * d[0] - centre[0]).hypot(a[1] + t * d[1] - centre[1]) < radius - 1e-6
}

fn project(plan: &Plan, a: &model::Analysis, view: &str, point: [f64; 3]) -> P2 {
    let v = plan.views.iter().find(|v| v.id == view).unwrap();
    let frame = draftwright::haecceity::hlr::View::new(v.direction, v.up).unwrap();
    let [lo, hi] = a.envelope;
    let corners: Vec<_> = (0..8)
        .map(|i| frame.map([0, 1, 2].map(|k| if i >> k & 1 == 0 { lo[k] } else { hi[k] })))
        .collect();
    let p = frame.map(point);
    [0, 1].map(|k| {
        let low = corners.iter().map(|p| p[k]).fold(f64::INFINITY, f64::min);
        let high = corners
            .iter()
            .map(|p| p[k])
            .fold(f64::NEG_INFINITY, f64::max);
        v.at[k] + v.scale * (p[k] - 0.5 * (low + high))
    })
}

#[test]
fn right_pinned_plate_callouts_clear_other_holes_and_report_no_callout_problem() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, mut a) =
        model::analyse(&root.join("tests/fixtures/parts/mounting_plate.step")).unwrap();
    model::recognise(
        &part,
        &mut a,
        &std::fs::read_to_string(root.join("tools/mcp-eval/features/plate.features.json")).unwrap(),
    )
    .unwrap();
    let ids = [
        "d:f:hole_pattern:f6/bore.diameter",
        "d:f:hole:f9/bore.diameter",
        "d:f:hole:f12/bore.diameter",
    ];
    // A pin is never moved: the centre bore takes a clear station (0.4) rather than "middle",
    // which crosses the small right hole and is reported (see placement_intent).
    let mut s = Spec::default();
    for (lane, id) in ids.iter().enumerate() {
        s=model::edit(&s,&json!([{"op":"set","path":format!("dimensions/{}/place",draftwright::items::key(id)),
            "value":{"view":"plan","side":"right","lane":lane,"along":([json!("start"),json!(0.4),json!("end")][lane]),"pinned":true}}])).unwrap();
    }
    let plan = model::plan_recovered(&a, &s).unwrap();
    let small = a.features.iter().find(|f| f.id == "f:hole:f12").unwrap();
    let c = project(&plan, &a, "v:plan", small.fingerprint.position);
    let scale = plan.views.iter().find(|v| v.id == "v:plan").unwrap().scale;
    assert!(!circle_crossing(shaft(&plan, ids[1]), c, 2.1 * scale));
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.subjects.iter().any(|s| ids.contains(&s.as_str()))),
        "{:?}",
        plan.problems
    );
    let changed = model::edit(
        &s,
        &json!([{"op":"set","path":"dimensions/d:envelope~1width.length/omit","value":true}]),
    )
    .unwrap();
    assert_eq!(
        shaft(&plan, ids[1]),
        shaft(&model::plan_recovered(&a, &changed).unwrap(), ids[1])
    );
}

#[test]
fn notch_leader_exits_by_a_near_free_edge_instead_of_traversing_the_height() {
    let Some(c) = corpus::dir() else { return };
    let (part, a) = model::analyse_native(&c.join("mfcadpp/10020.step")).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let [tip, elbow] = shaft(&plan, "d:f:chamfer:f3/chamfer.length");
    let v = plan.views.iter().find(|v| v.id == "v:front").unwrap();
    let width = (a.envelope[1][0] - a.envelope[0][0]) * v.scale;
    // Independently count the actual projected straight boundaries, including hidden features.
    // The former vertical route went to the station 3 paper mm above the view's envelope.
    let old = [
        tip,
        [
            tip[0],
            project(&plan, &a, "v:front", a.envelope[1])[1] + 3.0,
        ],
    ];
    let geometry =
        model::render_geometry(&part, &a, &plan, &mut model::Projections::default()).unwrap();
    let front = &geometry
        .views
        .iter()
        .find(|(id, _)| id == "v:front")
        .unwrap()
        .1;
    let crosses = |shaft: [P2; 2], edge: [P2; 2]| {
        let cross =
            |p: P2, q: P2, r: P2| (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
        cross(shaft[0], shaft[1], edge[0]) * cross(shaft[0], shaft[1], edge[1]) < -1e-9
            && cross(edge[0], edge[1], shaft[0]) * cross(edge[0], edge[1], shaft[1]) < -1e-9
    };
    let count = |shaft| {
        front.visible.iter().chain(&front.hidden).filter(|item| {
        matches!(item,draftwright::output::Item::Edge(draftwright::output::Curve::Line(a,b)) if crosses(shaft,[*a,*b]))
    }).count()
    };
    eprintln!(
        "notch boundary crossings {} -> {}",
        count(old),
        count([tip, elbow])
    );
    assert!(count([tip, elbow]) < count(old));

    assert!(
        (elbow[0] - tip[0]).hypot(elbow[1] - tip[1]) < width,
        "{tip:?} -> {elbow:?}, width {width}"
    );
}

#[test]
fn hex_leader_avoids_the_blind_hole_band_it_previously_crossed() {
    let Some(c) = corpus::dir() else { return };
    let (_, a) = model::analyse_native(&c.join("nist/nist_ctc_01_asme1_rd.stp")).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let id = "d:f:polygonal_boss:f61/polygon_across_flats.length";
    let [tip, elbow] = shaft(&plan, id);
    // The HEX is on the upper face. Its old downward shaft crossed the bottom outline and
    // the projected band of f:hole:f73; leaving upwards removes that crossing.
    assert!(elbow[1] > tip[1], "{tip:?} -> {elbow:?}");
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "leader_through_feature" && p.subjects[0] == id),
        "{:?}",
        plan.problems
    );
}

#[test]
fn underside_cavity_containment_is_not_a_crossing() {
    let Some(c) = corpus::dir() else { return };
    let (_, a) = model::analyse_native(&c.join("nist/nist_ftc_08_asme1_rc.stp")).unwrap();
    let plan = model::plan_recovered(
        &a,
        &Spec {
            hole_table: draftwright::hole_table::Setting::Never,
            sheet: serde_json::from_value(serde_json::json!({"size":"A1"})).unwrap(),
            ..Spec::default()
        },
    )
    .unwrap();
    let callouts = [
        "d:f:hole_pattern:f157/bore.diameter",
        "d:f:hole_pattern:f163/bore.diameter",
        "d:f:hole:f165/bore.diameter",
        "d:f:hole:f177/bore.diameter",
        "d:f:hole:f181/bore.diameter",
        "d:f:hole:f185/bore.diameter",
    ];
    for id in callouts {
        assert!(
            plan.dimensions
                .iter()
                .any(|d| d.id == id && d.placed.is_some())
        );
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "leader_through_feature" && p.subjects == [id, "f:slot:f117"]),
            "{id}: {:?}",
            plan.problems
        );
    }
}

/// An extension line through a feature is reported, never repaired by moving ink: its offered
/// fixes are local to the dimension, and `hole_table: never` is not overridden by a table remedy.
/// Asserted on FTC-10 (A1, 65 findings, 24 with fixes). FTC-08 carried these six findings until
/// quiddity 0.5.0 (#228) recognised its long slot `f:slot:f213` as a pocket, which draftwright
/// does not draw: all six were extension lines of other slots' dimensions crossing that slot, and
/// none remains.
#[test]
fn extensions_are_report_only_and_hole_table_never_keeps_its_scope() {
    let Some(c) = corpus::dir() else { return };
    let (_, a) = model::analyse_native(&c.join("nist/nist_ftc_10_asme1_rb.stp")).unwrap();
    let plan = model::plan_recovered(
        &a,
        &Spec {
            hole_table: draftwright::hole_table::Setting::Never,
            sheet: serde_json::from_value(serde_json::json!({"size":"A1"})).unwrap(),
            ..Spec::default()
        },
    )
    .unwrap();
    let extensions: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "extension_through_feature")
        .collect();
    assert!(!extensions.is_empty());
    // Reporting keeps the existing ink. #126 adds opt-in local repairs, while
    // this Spec's hole_table: never must not acquire a sheet-wide table remedy.
    assert!(extensions.iter().any(|p| !p.fix.is_empty()));
    for problem in extensions {
        let path = format!(
            "dimensions/{}/place",
            draftwright::items::key(&problem.subjects[0])
        );
        let fixes = problem.offered_fixes();
        if fixes.is_empty() {
            continue;
        }
        let owner = plan
            .dimensions
            .iter()
            .find(|d| d.id == problem.subjects[0])
            .unwrap();
        for fix in fixes {
            let new_view = fix
                .ops
                .iter()
                .find(|op| op.path == path)
                .and_then(|op| op.value.as_ref())
                .and_then(|v| v["view"].as_str())
                .map(|v| format!("v:{v}"));
            let table_carrier = plan.radius_table.as_ref().is_some_and(|table| {
                table
                    .rows
                    .iter()
                    .any(|r| r.view == owner.view || new_view.as_ref() == Some(&r.view))
            });
            assert_eq!(
                fix.scope(),
                if table_carrier {
                    draftwright::fixes::Scope::Sheet
                } else {
                    draftwright::fixes::Scope::Local
                }
            );
            if table_carrier {
                assert!(
                    fix.description()
                        .contains("The whole sheet is laid out again")
                );
                assert!(
                    fix.ops.iter().all(|op| op.path == path),
                    "no table remedy may override hole_table: never"
                );
            } else {
                assert!(
                    local_fix::edits(problem, &plan)
                        .iter()
                        .all(|op| op.path == path)
                );
                for (field, value) in [
                    ("sheet/keep_layout", serde_json::json!(true)),
                    ("sheet/size", serde_json::json!(plan.sheet.size)),
                    ("sheet/scale", serde_json::json!(plan.sheet.scale)),
                    ("sheets/max", serde_json::json!(1)),
                ] {
                    let ops: Vec<_> = fix.ops.iter().filter(|op| op.path == field).collect();
                    assert_eq!(ops.len(), 1);
                    assert_eq!(ops[0].value, Some(value));
                }
                assert!(
                    fix.ops
                        .iter()
                        .all(|op| op.path == path || local_fix::constraint(op))
                );
            }
        }
    }
}

#[test]
fn mounting_plate_slot_dimensions_use_clear_local_witnesses_without_losing_locations() {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/samples/mounting-plate.step.gz");
    let (_, a) = model::analyse_native(&path).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(plan.sheet.size, "A4");
    assert_eq!(plan.sheet.scale, "1:1");
    let slot = a.features.iter().find(|f| f.kind == "slot").unwrap();
    for (parameter, value) in [
        ("slot_width.length", 8.),
        ("slot_length.length", 30.),
        ("location_slot.length", 20.),
        ("location_slot_across.length", 82.),
    ] {
        assert!(
            plan.dimensions.iter().any(|d| d.placed.is_some()
                && d.measurements.iter().any(|m| m.feature == slot.id
                    && m.parameter == parameter
                    && (m.value - value).abs() < 1e-6)),
            "lost {parameter}"
        );
    }
    assert!(
        plan.dimensions.iter().any(|d| d.placed.is_some()
            && d.measurements
                .iter()
                .any(|m| m.parameter == "location.length.y" && (m.value - 35.).abs() < 1e-6)),
        "the bore still has its 35 mm ordinate"
    );
    let length = plan
        .dimensions
        .iter()
        .find(|d| {
            d.measurements
                .iter()
                .any(|m| m.feature == slot.id && m.parameter == "slot_length.length")
        })
        .unwrap();
    let view = plan.views.iter().find(|v| v.id == length.view).unwrap();
    let centre = project(&plan, &a, &length.view, slot.fingerprint.position);
    assert!(
        length
            .placed
            .as_ref()
            .unwrap()
            .arrows
            .iter()
            .all(|tip| tip[0][0] > centre[0] + 4. * view.scale),
        "length belongs beside the slot on the right"
    );
    let frame = draftwright::bridge::holes::frame(&a);
    let holes: Vec<_> = a
        .features
        .iter()
        .filter(|f| f.kind == "hole")
        .map(|f| draftwright::bridge::holes::Hole::read(f, &frame).unwrap())
        .collect();
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "linear" && d.measurements.iter().any(|m| m.feature == slot.id))
    {
        let p = d.placed.as_ref().unwrap();
        for line in p.lines.iter().take(2) {
            for hole in &holes {
                let c = project(&plan, &a, &d.view, hole.at);
                assert!(
                    !circle_crossing(*line, c, hole.diameter * view.scale / 2.),
                    "{} crosses a hole with {line:?}",
                    d.id
                );
            }
        }
    }
    // Independently catch the old bore ordinate through the slot's straight
    // interior, rather than trusting a missing collision warning.
    let slot_box = [
        centre[0] - 4. * view.scale,
        centre[1] - 11. * view.scale,
        centre[0] + 4. * view.scale,
        centre[1] + 11. * view.scale,
    ];
    for d in plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "linear" && d.measurements.iter().all(|m| m.feature != slot.id))
    {
        for [p, q] in d.placed.as_ref().unwrap().lines.iter().take(2) {
            let through = if (p[1] - q[1]).abs() < 1e-6 {
                p[1] > slot_box[1]
                    && p[1] < slot_box[3]
                    && p[0].max(q[0]) > slot_box[0]
                    && p[0].min(q[0]) < slot_box[2]
            } else {
                false
            };
            assert!(!through, "{} crosses the slot with {p:?} -> {q:?}", d.id);
        }
    }
    assert!(
        plan.problems.iter().all(|p| !matches!(
            p.code.as_str(),
            "extension_through_feature"
                | "label_crowded"
                | "annotation_overlap"
                | "annotation_ink_overlap"
                | "annotation_leader_crossing"
        )),
        "{:?}",
        plan.problems
    );
}
