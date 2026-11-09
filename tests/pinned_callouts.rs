//! E1: prescribed callouts keep their station and point their text along the leader.
use draftwright::model::{self, Analysis, Plan, Spec};
use serde_json::json;
use std::path::Path;

const ID: &str = "d:f:hole_pattern:f6/bore.diameter";

fn plate() -> Analysis {
    model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step"),
    )
    .unwrap()
    .1
}

fn pinned(a: &Analysis, id: &str, side: &str, along: f64) -> Plan {
    let spec: Spec = serde_json::from_value(json!({"dimensions":{id:{"place":{
        "view":"plan", "side":side, "lane":0, "along":along, "pinned":true
    }}}}))
    .unwrap();
    model::plan_recovered(a, &spec).unwrap()
}

#[test]
fn above_and_below_left_drag_puts_text_left_of_bend() {
    let a = plate();
    for side in ["above", "below"] {
        let p = pinned(&a, ID, side, 0.18);
        let d = p.dimensions.iter().find(|d| d.id == ID).unwrap();
        let ink = d.placed.as_ref().unwrap();
        let [tip, bend] = ink.lines[0];
        assert!(bend[0] < tip[0], "{side}: {ink:?}");
        assert!(ink.label_box[2] < bend[0], "{side}: {ink:?}");
        assert!(ink.lines[1][1][0] < bend[0], "{side}: {ink:?}");
    }
}

#[test]
fn crossing_pin_keeps_bend_and_label_and_reports_features() {
    let a = plate();
    // The central bore's vertical leader crosses the pattern's upper middle hole.
    let id = "d:f:hole:f9/bore.diameter";
    let p = pinned(&a, id, "above", 0.5);
    let view = p.views.iter().find(|v| v.id == "v:plan").unwrap();
    let ink = p
        .dimensions
        .iter()
        .find(|d| d.id == id)
        .unwrap()
        .placed
        .as_ref()
        .unwrap();
    let bounds = outline(&a, view);
    let expected = [view.at[0], bounds[3] + draftwright::dims::FIRST_NORMAL];
    assert!(
        (ink.lines[0][1][0] - expected[0]).abs() < 1e-6,
        "{ink:?}, {expected:?}"
    );
    assert!(
        (ink.lines[0][1][1] - expected[1]).abs() < 1e-6,
        "{ink:?}, {expected:?}"
    );
    assert!(
        (ink.label_box[0] - expected[0] - 3.0).abs() < 1e-6,
        "{ink:?}"
    );
    assert!(
        (ink.label_at[1] - expected[1] - 1.0).abs() < 1e-6,
        "{ink:?}"
    );
    let findings: Vec<_> = p
        .problems
        .iter()
        .filter(|p| p.code == "leader_through_feature" && p.subjects.contains(&id.into()))
        .collect();
    assert!(!findings.is_empty(), "{:#?}", p.problems);
    for problem in findings {
        assert_eq!(problem.severity, "warning");
        assert!(problem.refs.contains(&id.into()), "{problem:?}");
        assert!(
            problem
                .message
                .contains("pinned label's leader crosses part features"),
            "{problem:?}"
        );
    }
}

fn outline(a: &Analysis, view: &model::PlannedView) -> [f64; 4] {
    let projection = draftwright::haecceity::hlr::View::new(view.direction, view.up).unwrap();
    let [lo, hi] = a.envelope;
    let mut bounds = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    for i in 0..8 {
        let point = projection.map([0, 1, 2].map(|k| if i >> k & 1 == 0 { lo[k] } else { hi[k] }));
        for k in 0..2 {
            bounds[k] = bounds[k].min(point[k]);
            bounds[k + 2] = bounds[k + 2].max(point[k]);
        }
    }
    [
        view.at[0] - view.scale * 0.5 * (bounds[2] - bounds[0]),
        view.at[1] - view.scale * 0.5 * (bounds[3] - bounds[1]),
        view.at[0] + view.scale * 0.5 * (bounds[2] - bounds[0]),
        view.at[1] + view.scale * 0.5 * (bounds[3] - bounds[1]),
    ]
}

#[test]
fn above_left_pin_keeps_parallel_location_dimensions_readable() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (_, recognised) = model::analyse_with(
        &root.join("tests/fixtures/parts/mounting_plate.step"),
        Some(&root.join("tools/mcp-eval/features/plate.features.json")),
    )
    .unwrap();
    for a in [plate(), recognised] {
        for along in [0.05, 0.1, 0.18] {
            let p = pinned(&a, ID, "above", along);
            let ink = |id: &str| {
                p.dimensions
                    .iter()
                    .find(|d| d.id == id)
                    .unwrap()
                    .placed
                    .as_ref()
                    .unwrap()
            };
            let callout = ink(ID);
            assert!(callout.label_box[2] < callout.lines[0][1][0]);
            let short = ink("d:f:hole:f9/location.length.x");
            let long = ink("d:f:hole:f12/location.length.x");
            let short_line = short.lines[2];
            let long_line = long.lines[2];
            assert!(
                (short_line[0][1] - long_line[0][1]).abs() >= draftwright::dims::LANE_NORMAL - 1e-6,
                "along {along}: 50 and 95 share a lane: {short:?}, {long:?}"
            );
            // When both lie below the view, the larger span must be outside the smaller.
            let view = p.views.iter().find(|v| v.id == "v:plan").unwrap();
            if short_line[0][1] < view.at[1] && long_line[0][1] < view.at[1] {
                assert!(long_line[0][1] < short_line[0][1]);
            }
        }
    }
}
