//! Physical plate thicknesses are editable items and independently checked on the ink.
#[path = "common/corpus.rs"]
mod corpus;

use draftwright::{
    items::{Op, State, key},
    lint,
    model::{self, Spec},
    text::Font,
};
use serde_json::json;

/// A draftsman runs a thickness's extension lines out from where both faces meet the view's
/// silhouette, never back across the part: each witness starts at the outline edge facing its
/// dimension line, less the usual gap.
fn witnesses_leave_through_the_silhouette(
    name: &str,
    a: &model::Analysis,
    plan: &model::Plan,
    d: &draftwright::dims::PlannedDimension,
) {
    let v = plan.views.iter().find(|v| v.id == d.view).unwrap();
    let up = v.up;
    let dir = v.direction;
    let right = [
        up[1] * dir[2] - up[2] * dir[1],
        up[2] * dir[0] - up[0] * dir[2],
        up[0] * dir[1] - up[1] * dir[0],
    ];
    let size: Vec<f64> = (0..3)
        .map(|k| a.envelope[1][k] - a.envelope[0][k])
        .collect();
    let half = |e: [f64; 3]| 0.5 * v.scale * (0..3).map(|k| (e[k] * size[k]).abs()).sum::<f64>();
    let outline = [
        v.at[0] - half(right),
        v.at[1] - half(up),
        v.at[0] + half(right),
        v.at[1] + half(up),
    ];
    let ink = d.placed.as_ref().unwrap();
    for [start, end] in &ink.lines[..2] {
        let k = usize::from((end[1] - start[1]).abs() > (end[0] - start[0]).abs());
        let (edge, out) = if end[k] > start[k] {
            (outline[k + 2], 1.0)
        } else {
            (outline[k], -1.0)
        };
        // Witnesses start a small gap clear of the object line.
        let gap = out * (start[k] - edge);
        assert!(
            (-0.05..=1.05).contains(&gap),
            "{name}: {} witness {start:?}->{end:?} starts inside the view (edge {edge:.2})",
            d.id
        );
    }
}

fn thicknesses(name: &str) {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = model::analyse_native(&dir.join(format!("mfcadpp/{name}.step"))).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let plates: Vec<_> = a.features.iter().filter(|f| f.kind == "plate").collect();
    assert!(!plates.is_empty(), "{name}: recognised plates");
    let drawn: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "thickness.length")
        })
        .collect();
    assert!(
        !drawn.is_empty(),
        "{name}: no plate thickness items; plates: {plates:#?}"
    );
    for d in &drawn {
        assert_eq!(d.state, State::Shown, "{name}: {}", d.id);
        assert!(d.placed.is_some(), "{name}: {}", d.id);
        let ink = d.placed.as_ref().unwrap();
        let along = usize::from(ink.rotation % 180.0 != 0.0);
        let scale = plan.views.iter().find(|v| v.id == d.view).unwrap().scale;
        assert!(
            ((ink.arrows[0][0][along] - ink.arrows[1][0][along]).abs() - d.value * scale).abs()
                < 1e-6,
            "{} has its actual thickness span",
            d.id
        );
        witnesses_leave_through_the_silhouette(name, &a, &plan, d);
        for m in d
            .measurements
            .iter()
            .filter(|m| m.parameter == "thickness.length")
        {
            assert_eq!(
                drawn
                    .iter()
                    .filter(|other| other.measurements.contains(m))
                    .count(),
                1
            );
        }
    }
    let drawing = model::render(
        &part,
        &a,
        &plan,
        &Font::new(draftwright::api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    let findings = lint::lint(&a, &drawing);
    assert!(
        !findings
            .iter()
            .any(|i| i.code.starts_with("plate_requirement_")),
        "{name}: {findings:#?}"
    );
    if name == "1000" {
        assert_eq!(
            drawn[0].view, "v:plan",
            "the wall beside the U-slot has solid edges in plan"
        );
    }
    if name == "10038" {
        assert_eq!(
            drawn
                .iter()
                .find(|d| (d.value - 4.448).abs() < 0.001)
                .unwrap()
                .view,
            "v:side",
            "the flange is exposed in the L profile"
        );
        let underside = "d:step_level/step_height.length.f4";
        assert!(
            !plan.dimensions.iter().any(|d| d.id == underside),
            "no 7.2 + 4.4 = 11.6 chain"
        );
        assert!(
            plan.omitted
                .iter()
                .any(|o| o.id == underside && matches!(o.state, State::Covered { .. }))
        );
        assert!(
            drawn.iter().any(|d| (d.value - 10.09).abs() < 0.01),
            "the 10.09 wall is stated"
        );
    }
    let d = drawn[0];
    let hidden = model::edit(
        &Spec::default(),
        &json!([Op::set(
            format!("dimensions/{}/omit", key(&d.id)),
            json!(true)
        )]),
    )
    .unwrap();
    let hidden_plan = model::plan_recovered(&a, &hidden).unwrap();
    assert!(
        hidden_plan
            .omitted
            .iter()
            .any(|o| o.id == d.id && o.state == State::OmittedByUser)
    );
    if name == "10038" && (d.value - 4.448).abs() < 0.001 {
        assert!(
            hidden_plan
                .dimensions
                .iter()
                .any(|d| d.id == "d:step_level/step_height.length.f4")
        );
    }
    let drawing = model::render(
        &part,
        &a,
        &hidden_plan,
        &Font::new(draftwright::api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    assert!(
        lint::lint(&a, &drawing)
            .iter()
            .any(|i| i.code == "plate_requirement_suppressed")
    );
    let pinned = model::edit(
        &Spec::default(),
        &json!([Op::set(
            format!("dimensions/{}/place", key(&d.id)),
            json!({"pinned":true})
        )]),
    )
    .unwrap();
    let pinned_plan = model::plan_recovered(&a, &pinned).unwrap();
    assert_eq!(
        pinned_plan
            .dimensions
            .iter()
            .find(|p| p.id == d.id)
            .unwrap()
            .placed,
        d.placed
    );
    let view = if d.measurements.iter().any(|m| {
        plates
            .iter()
            .any(|f| f.id == m.feature && f.parameters["axis"] != "z")
    }) {
        "plan"
    } else {
        "side"
    };
    let moved = model::edit(
        &Spec::default(),
        &json!([Op::set(
            format!("dimensions/{}/place", key(&d.id)),
            json!({"view":view,"pinned":true})
        )]),
    )
    .unwrap();
    let moved_plan = model::plan_recovered(&a, &moved).unwrap();
    let moved_item = moved_plan.dimensions.iter().find(|p| p.id == d.id).unwrap();
    assert_eq!(moved_item.view, format!("v:{view}"));
    assert_eq!(moved_item.value, d.value);
    assert!(moved_item.placed.is_some());
    let ink = moved_item.placed.as_ref().unwrap();
    let along = usize::from(ink.rotation % 180.0 != 0.0);
    let scale = moved_plan
        .views
        .iter()
        .find(|v| v.id == moved_item.view)
        .unwrap()
        .scale;
    assert!(
        ((ink.arrows[0][0][along] - ink.arrows[1][0][along]).abs() - d.value * scale).abs() < 1e-6
    );

    assert_eq!(
        serde_json::to_vec(&plan).unwrap(),
        serde_json::to_vec(&model::plan_recovered(&a, &Spec::default()).unwrap()).unwrap()
    );
}

#[test]
fn m10007_states_its_plate_thicknesses_once() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = model::analyse_native(&dir.join("mfcadpp/10007.step")).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let drawing = model::render(
        &part,
        &a,
        &plan,
        &Font::new(draftwright::api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    assert!(
        !lint::lint(&a, &drawing)
            .iter()
            .any(|i| i.code.starts_with("plate_requirement_"))
    );
    for f in a.features.iter().filter(|f| f.kind == "plate") {
        let id = format!("d:{}/thickness.length", f.id);
        let covered = plan.omitted.iter().find(|o| o.id == id).unwrap();
        assert!(matches!(covered.state, State::Covered { .. }));
        assert!(!plan.dimensions.iter().any(|d| d.id == id));
        let owners = covered.basis.split("; dependencies: ").nth(1).unwrap();
        for owner in owners.split(", ") {
            assert!(
                plan.dimensions
                    .iter()
                    .any(|d| d.id == owner && d.placed.is_some())
            );
            let spec = model::edit(
                &Spec::default(),
                &json!([Op::set(
                    format!("dimensions/{}/omit", key(owner)),
                    json!(true)
                )]),
            )
            .unwrap();
            let restored = model::plan_recovered(&a, &spec).unwrap();
            assert!(
                restored
                    .dimensions
                    .iter()
                    .any(|d| d.id == id && d.placed.is_some()),
                "omitting {owner} restores {id}"
            );
        }
    }
}

#[test]
fn m10038_states_its_ten_point_zero_nine_wall_once() {
    thicknesses("10038");
}

#[test]
fn m10033_wall_witnesses_do_not_cross_the_view() {
    thicknesses("10033");
}

#[test]
fn m10060_states_its_plate_thickness_once() {
    // The plate runs from the y shoulder to the box: 33.6 overall less the 19.3 shoulder states
    // its thickness, so it is conveyed by that chain and not drawn again (Stage H, h-chains).
    let Some(dir) = corpus::dir() else { return };
    let (_, a) = model::analyse_native(&dir.join("mfcadpp/10060.step")).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert!(!plan.dimensions.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| m.parameter == "thickness.length")
    }));
    let shoulder = "d:step_level/step_position.length.f3_1";
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.id == shoulder && d.placed.is_some())
    );
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.id == "d:envelope/depth.length")
    );
    assert!(
        plan.omitted
            .iter()
            .any(|o| o.id == "d:f:plate:f5/thickness.length"
                && matches!(&o.state, State::Covered { by } if by == shoulder))
    );
}

#[test]
fn m10049_states_its_plate_thicknesses_once() {
    thicknesses("10049");
}

#[test]
fn m1000_wall_uses_the_visible_plan_profile() {
    thicknesses("1000");
}

#[test]
fn captured_m1000_seat_callout_stays_inside_its_front_band() {
    let Some(dir) = corpus::dir() else { return };
    let (_, a) = corpus::analyse(&dir, "mfcadpp/1000.step", "m1000");
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let overruns: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| p.code == "annotation_outside_area")
        .collect();
    assert!(overruns.is_empty(), "{overruns:#?}");
    assert!(
        plan.dimensions
            .iter()
            .any(|d| d.id.ends_with("/seat_diameter.diameter") && d.placed.is_some()),
        "the seat remains drawn, with every band overrun removed"
    );
    assert!(
        overruns
            .iter()
            .all(|p| !p.message.contains("seat_diameter"))
    );
}
