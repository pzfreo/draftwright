//! Detail ownership and the default turned inspection baseline, including saved spec IDs.
#[path = "common/corpus.rs"]
mod corpus;
use draftwright::model::{self, Analysis, Spec};
use serde_json::json;
use std::path::Path;

fn string_post() -> Option<Analysis> {
    let dir = corpus::dir()?;
    Some(
        model::analyse_native(&dir.join("gramel/string_post.step"))
            .unwrap()
            .1,
    )
}

#[test]
fn string_post_prefers_complete_larger_principals_without_a_detail() {
    let Some(a) = string_post() else { return };
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A4", "5:1")
    );
    assert!(plan.views.iter().all(|v| v.detail.is_none()));
    assert!(plan.dimensions.iter().any(|d| d.view == "v:front"
        && d.id == "d:f:turned_step:3/step.length"
        && d.placed.is_some()));
    let dir = corpus::dir().unwrap();
    let (part, _) = model::analyse_native(&dir.join("gramel/string_post.step")).unwrap();
    let font = draftwright::text::Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(draftwright::dims::FONT),
    )
    .unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    assert!(drawing.text.iter().all(|t| !t.text.starts_with("DETAIL ")));
    assert!(
        drawing
            .sheet
            .items
            .iter()
            .all(|i| !i.name.starts_with("detail_marker_"))
    );
    assert!(
        draftwright::lint::structural::check(&drawing)
            .iter()
            .all(|i| !matches!(
                i.code.as_str(),
                "derived_view_identifier_reused" | "detail_caption_clearance"
            ))
    );
}

#[test]
fn a_constrained_main_scale_keeps_the_complete_head_crop_and_sheet_scale_iso() {
    let Some(a) = string_post() else { return };
    let fixed: Spec = serde_json::from_value(json!({"sheet":{"size":"A4","scale":"2:1"}})).unwrap();
    let plan = model::plan_recovered(&a, &fixed).unwrap();
    let v = plan
        .views
        .iter()
        .find(|v| v.detail.is_some())
        .expect("head detail");
    let d = v.detail.as_ref().unwrap();
    assert_eq!(d.kind, "turned-head");
    assert_eq!(v.scale, 10.0);
    assert!(d.bounds[0][2] <= 13.8 && d.bounds[1][2] >= 14.8);
    assert!(
        plan.dimensions.iter().any(|d| d.view == v.id
            && d.id == "d:f:turned_step:3/step.length"
            && d.placed.is_some())
    );
    assert_eq!(
        plan.views.iter().find(|v| v.id == "v:iso").unwrap().scale,
        plan.sheet.scale_factor
    );
    let dir = corpus::dir().unwrap();
    let (part, _) = model::analyse_native(&dir.join("gramel/string_post.step")).unwrap();
    let font = draftwright::text::Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(draftwright::dims::FONT),
    )
    .unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    assert!(drawing.text.iter().any(|t| t.text == "DETAIL B (10:1)"));
    assert!(
        drawing
            .sheet
            .items
            .iter()
            .any(|i| i.name == "detail_marker_B")
    );
    assert!(
        draftwright::lint::structural::check(&drawing)
            .iter()
            .all(|i| !matches!(
                i.code.as_str(),
                "derived_view_identifier_reused" | "detail_caption_clearance"
            ))
    );
}

#[test]
fn x_heads_keep_complete_profiles_or_withhold_the_crop() {
    let Some(dir) = corpus::dir() else { return };
    let (part, a) = corpus::analyse(&dir, "gramel/GRM-03_thumbwheel_drive_screw.step", "grm03");
    let fixed = |size: &str| {
        serde_json::from_value::<Spec>(json!({"sheet":{"size":size,"scale":"2:1"}})).unwrap()
    };
    let small = model::plan_recovered(&a, &fixed("A4")).unwrap();
    assert!(small.views.iter().all(|v| v.detail.is_none()));
    for value in [0.5, 6.0] {
        assert!(small.dimensions.iter().any(|d| {
            d.view == "v:front" && (d.value - value).abs() < 1e-6 && d.placed.is_some()
        }));
    }
    let p = model::plan_recovered(&a, &fixed("A3")).unwrap();
    let v = p
        .views
        .iter()
        .find(|v| v.detail.is_some())
        .expect("full head fits A3");
    let d = v.detail.as_ref().unwrap();
    assert_eq!(d.axis, 0);
    assert!(!d.partial_profile);
    assert!(d.bounds[0][2] <= a.envelope[0][2] && d.bounds[1][2] >= a.envelope[1][2]);
    for value in [0.5, 2.0, 6.0] {
        assert!(p.dimensions.iter().any(|dim| {
            dim.view == v.id && (dim.value - value).abs() < 1e-6 && dim.placed.is_some()
        }));
    }
    let geometry =
        model::render_geometry(&part, &a, &p, &mut model::Projections::default()).unwrap();
    let g = &geometry.views.iter().find(|(id, _)| id == &v.id).unwrap().1;
    let ink = draftwright::output::items_bbox(&g.visible).unwrap();
    assert!(ink[3] - ink[1] >= (a.envelope[1][2] - a.envelope[0][2]) * v.scale - 0.1);
}

#[test]
fn an_omitted_detail_seed_cannot_leak_into_an_ordinary_candidate() {
    let Some(a) = string_post() else { return };
    let seed = model::plan_escalated(&a, &Spec::default()).unwrap();
    assert!(seed.views.iter().any(|v| v.detail.is_some()));
    let spec: Spec = serde_json::from_value(json!({"views":{"detail_b":{"omit":true}}})).unwrap();
    let p = model::recover(&a, &spec, seed).unwrap();
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        ("A4", "5:1")
    );
    assert!(p.views.iter().all(|v| v.detail.is_none()));
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.id == "d:f:turned_step:3/step.length"
                && d.view == "v:front"
                && d.placed.is_some())
    );
}

#[test]
fn bare_dimension_pins_keep_recoverys_detail_selection_and_ink() {
    let Some(dir) = corpus::dir() else { return };
    let (_, a) = model::analyse_native(&dir.join("mfcadpp/10046.step")).unwrap();
    let base = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert!(base.views.iter().all(|v| v.detail.is_none()));
    assert!(base.problems.iter().all(|p| !matches!(
        p.code.as_str(),
        "annotation_over_view" | "annotation_line_over_view"
    )));
    let dimensions = |plan: &model::Plan| {
        let mut dims = plan.dimensions.clone();
        for d in &mut dims {
            // The pin changes provenance; all measurements and drawn ink must stay equal.
            d.default = true;
            d.from_spec.clear();
            d.basis.clear();
        }
        dims
    };
    for pinned in &base.dimensions {
        let spec: Spec = serde_json::from_value(json!({
            "dimensions": {&pinned.id: {"place": {"pinned": true}}}
        }))
        .unwrap();
        let plan = model::plan_recovered(&a, &spec).unwrap();
        assert_eq!(dimensions(&plan), dimensions(&base), "pin {}", pinned.id);
        assert_eq!(
            plan.views
                .iter()
                .map(|v| (&v.id, v.at, v.area, v.scale))
                .collect::<Vec<_>>(),
            base.views
                .iter()
                .map(|v| (&v.id, v.at, v.area, v.scale))
                .collect::<Vec<_>>(),
            "pin {}",
            pinned.id
        );
        assert!(
            plan.problems.iter().all(|p| !matches!(
                p.code.as_str(),
                "annotation_over_view" | "annotation_line_over_view"
            )),
            "pin {}: {:?}",
            pinned.id,
            plan.problems
        );
    }
}

fn crosses(line: [[f64; 2]; 2], b: [f64; 4]) -> bool {
    let (mut lo, mut hi) = (0.0_f64, 1.0_f64);
    for k in 0..2 {
        let d = line[1][k] - line[0][k];
        if d.abs() < 1e-9 {
            if line[0][k] <= b[k] + 1e-6 || line[0][k] >= b[k + 2] - 1e-6 {
                return false;
            }
        } else {
            let p = (b[k] - line[0][k]) / d;
            let q = (b[k + 2] - line[0][k]) / d;
            lo = lo.max(p.min(q));
            hi = hi.min(p.max(q));
        }
    }
    hi > lo + 1e-6
}

#[test]
fn string_post_heights_share_one_origin_and_extension_lines_clear_text() {
    let Some(a) = string_post() else { return };
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let heights: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.view == "v:front" && d.kind == "linear")
        .filter_map(|d| Some((d, d.placed.as_ref()?)))
        .filter(|(_, p)| p.rotation.abs() == 90.0)
        .collect();
    assert!(heights.len() >= 3);
    let base = heights[0]
        .1
        .arrows
        .iter()
        .map(|a| a[0][1])
        .find(|y| {
            heights
                .iter()
                .all(|(_, q)| q.arrows.iter().any(|a| (a[0][1] - y).abs() < 1e-6))
        })
        .expect("one common origin");
    for (d, q) in &heights {
        assert!(
            q.arrows.iter().any(|a| (a[0][1] - base).abs() < 1e-6),
            "{} has another origin",
            d.id
        );
        assert!(
            !d.label.starts_with('('),
            "{} is spuriously reference",
            d.id
        );
        for (other, r) in &heights {
            if d.id != other.id {
                for line in q.lines.iter().take(2) {
                    assert!(
                        !crosses(*line, r.label_box),
                        "{} crosses {}",
                        d.id,
                        other.id
                    );
                }
            }
        }
    }
    assert!(
        heights
            .iter()
            .any(|(d, _)| d.id.contains("location.length.z") && (d.value - 3.75).abs() < 1e-6)
    );
}

#[test]
fn an_authored_detail_is_honoured_and_keeps_its_exact_scale() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (_, a) = model::analyse_native(&root.join("tests/fixtures/parts/turned.step")).unwrap();
    let feature = a.features.iter().find(|f| f.kind == "turned_step").unwrap();
    let spec:Spec=serde_json::from_value(json!({"views":{"detail_c":{"detail":{"around":feature.id,"scale_factor":3.0}}},"sheet":{"size":"A3","scale":"1:2"}})).unwrap();
    let p = model::plan(&a, &spec).unwrap();
    let v = p.views.iter().find(|v| v.id == "v:detail_c").unwrap();
    assert_eq!(v.scale, 1.5);
    assert!(v.detail.as_ref().unwrap().authored);
}

#[test]
fn a_plain_part_has_no_stray_detail() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (_, a) =
        model::analyse_native(&root.join("tests/fixtures/parts/mounting_plate.step")).unwrap();
    assert!(
        model::plan_recovered(&a, &Spec::default())
            .unwrap()
            .views
            .iter()
            .all(|v| v.detail.is_none())
    );
    let Some(a) = string_post() else { return };
    assert!(
        model::plan(
            &a,
            &serde_json::from_value(json!({"sheet":{"size":"A4","scale":"2:1"}})).unwrap()
        )
        .unwrap()
        .views
        .iter()
        .any(|v| v.detail.is_some())
    );
}

#[test]
fn saved_chain_ids_and_tolerances_still_resolve() {
    let Some(a) = string_post() else { return };
    let spec:Spec=serde_json::from_value(json!({"dimensions":{"d:f:turned_step:1/step.length":{"tolerance":"±0.05"},"d:f:turned_step:3/step.length":{"tolerance":"±0.02"}}})).unwrap();
    let p = model::plan_recovered(&a, &spec).unwrap();
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.id == "d:f:turned_step:3/step.length" && d.view == "v:front")
    );
    for (id, tol) in [
        ("d:f:turned_step:1/step.length", "±0.05"),
        ("d:f:turned_step:3/step.length", "±0.02"),
    ] {
        assert!(
            p.dimensions
                .iter()
                .any(|d| d.id == id && d.label.contains(tol) && d.placed.is_some())
        );
    }
}

#[test]
fn the_saved_string_post_spec_keeps_every_tolerance_without_crossed_text() {
    let Some(a) = string_post() else { return };
    let saved: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/known_single_sheet_specs.json")).unwrap();
    // string_post.step:spec:1c29f39db68e8b009a719d83ec1389d96a1e350a2d6b4bc142069453c99e3569
    let spec: Spec = serde_json::from_value(saved[&a.binding.source_sha256][1].clone()).unwrap();
    assert_eq!(spec.dimensions.len(), 14);
    let p = model::plan_recovered(&a, &spec).unwrap();
    assert_eq!(
        (p.sheet.size.as_str(), p.sheet.scale.as_str()),
        ("A4", "5:1")
    );
    assert!(p.views.iter().all(|v| v.detail.is_none()));
    for (id, spec) in &spec.dimensions {
        let tol = spec.tolerance.as_ref().unwrap();
        assert!(
            p.dimensions
                .iter()
                .any(|d| d.id == *id && d.placed.is_some() && d.label.contains(tol)),
            "{id}: {tol}"
        );
    }
    assert!(!p.problems.iter().any(|p| matches!(
        p.code.as_str(),
        "annotation_overlap" | "annotation_ink_overlap" | "annotation_off_sheet"
    )));
}

#[test]
fn crowded_prismatic_rungs_and_y_chains_use_their_own_camera_and_scale() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    for (name, kind, parent) in [
        ("crowded_prism", "prismatic-steps", "v:front"),
        ("unequal_prism", "prismatic-steps", "v:front"),
        ("y_chain", "y-turned-chain", "v:side"),
    ] {
        let (_, a) =
            model::analyse_native(&root.join(format!("tests/fixtures/details/{name}.step.gz")))
                .unwrap();
        let spec: Spec =
            serde_json::from_value(json!({"sheet":{"size":"A3","scale":"1:1"}})).unwrap();
        let p = model::plan(&a, &spec).unwrap();
        let v = p
            .views
            .iter()
            .find(|v| v.detail.as_ref().is_some_and(|d| d.kind == kind))
            .unwrap_or_else(|| panic!("{name}: no {kind}: {:?}", p.problems));
        let d = v.detail.as_ref().unwrap();
        assert_eq!(d.source_view, parent);
        assert!(v.scale > p.sheet.scale_factor);
        assert!(!d.dimensions.is_empty());
        for id in &d.dimensions {
            assert!(
                p.dimensions
                    .iter()
                    .any(|dim| dim.id == *id && dim.view == v.id)
            );
        }
    }
}

#[test]
fn an_authored_a_reserves_its_letter_before_the_automatic_section() {
    let Some(a) = string_post() else { return };
    let s:Spec=serde_json::from_value(json!({"sheet":{"size":"A3","scale":"2:1"},"views":{"detail_a":{"detail":{"around":"f:turned_step:3","scale_factor":5}}}})).unwrap();
    let p = model::plan(&a, &s).unwrap();
    assert!(p.views.iter().any(|v| v.id == "v:detail_a"));
    assert!(
        p.views
            .iter()
            .any(|v| v.id == "v:section_bb" && v.cut.as_ref().is_some_and(|c| c.label == "B"))
    );
}

#[test]
fn omitting_an_automatic_detail_does_not_rename_it() {
    let Some(a) = string_post() else { return };
    let s: Spec = serde_json::from_value(json!({"views":{"detail_b":{"omit":true}}})).unwrap();
    let p = model::plan(&a, &s).unwrap();
    assert!(p.views.iter().all(|v| v.detail.is_none()));
    assert!(
        p.dimensions
            .iter()
            .any(|d| d.id == "d:f:turned_step:3/step.length" && d.view == "v:front")
    );
}

/// K6 ownership survives moving a requirement to a crop, including authored sets and
/// tolerances on saved IDs. Rendering must not credit the parent a second time.
#[test]
fn detail_measurements_keep_one_owner_in_automatic_and_authored_sets() {
    use draftwright::model::{DimensionSource, DimensionSpec};
    use std::collections::BTreeMap;
    let Some(a) = string_post() else { return };
    let fixed: Spec = serde_json::from_value(json!({"sheet":{"size":"A4","scale":"2:1"}})).unwrap();
    let automatic = model::plan_recovered(&a, &fixed).unwrap();
    let authored = Spec {
        dimension_source: DimensionSource::Authored,
        dimensions: automatic
            .dimensions
            .iter()
            .map(|d| (d.id.clone(), DimensionSpec::default()))
            .collect(),
        sheet: fixed.sheet.clone(),
        ..Spec::default()
    };
    for spec in [fixed, authored] {
        let plan = model::plan_recovered(&a, &spec).unwrap();
        assert!(plan.views.iter().any(|v| v.detail.is_some()));
        let mut owners = BTreeMap::new();
        for d in plan
            .dimensions
            .iter()
            .filter(|d| d.placed.is_some() && !d.reference)
        {
            for m in &d.measurements {
                assert!(
                    owners
                        .insert((m.feature.clone(), m.parameter.clone()), d.id.clone())
                        .is_none(),
                    "measurement {m:?} repeated by {}",
                    d.id
                );
            }
        }
        for d in automatic
            .dimensions
            .iter()
            .filter(|d| d.view.starts_with("v:detail_"))
        {
            let moved = plan
                .dimensions
                .iter()
                .find(|p| p.id == d.id)
                .expect("same owner ID");
            assert_eq!(moved.view, d.view);
            assert_eq!(moved.measurements, d.measurements);
        }
        for omitted in &plan.omitted {
            if let draftwright::items::State::Covered { by } = &omitted.state {
                assert!(
                    plan.dimensions
                        .iter()
                        .any(|d| d.id == *by && d.placed.is_some()),
                    "{}: {by}",
                    omitted.id
                );
            }
        }
    }
}

fn fixture(name: &str) -> (haecceity::Part, Analysis) {
    model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("tests/fixtures/parts/{name}.step")),
    )
    .unwrap()
}

fn authored(around: serde_json::Value, from: Option<&str>) -> Spec {
    let mut detail = json!({"around":around,"scale_factor":2});
    if let Some(source) = from {
        detail["from"] = json!(source);
    }
    serde_json::from_value(json!({"sheet":{"size":"A2","scale":"1:2"},"views":{"front":{},"plan":{},"side":{},"detail_c":{"detail":detail}}})).unwrap()
}

fn detail_view(p: &model::Plan) -> &model::PlannedView {
    p.views.iter().find(|v| v.id == "v:detail_c").unwrap()
}

#[test]
fn authored_from_uses_the_parent_camera_projection_and_marker() {
    let (part, a) = fixture("mounting_plate");
    let font = draftwright::text::Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(draftwright::dims::FONT),
    )
    .unwrap();
    for source in ["plan", "side"] {
        let p = model::plan(&a, &authored(json!("f:hole:1"), Some(source))).unwrap();
        let v = detail_view(&p);
        let parent = p
            .views
            .iter()
            .find(|v| v.id == format!("v:{source}"))
            .unwrap();
        assert_eq!(v.direction, parent.direction);
        assert_eq!(v.up, parent.up);
        let d = v.detail.as_ref().unwrap();
        assert_eq!(d.source_view, parent.id);
        let drawing = model::render(&part, &a, &p, &font).unwrap();
        let marker = drawing
            .sheet
            .items
            .iter()
            .find(|i| i.name == "detail_marker_C")
            .unwrap();
        let camera = haecceity::hlr::View::new(parent.direction, parent.up).unwrap();
        let centre = camera.map(std::array::from_fn(|k| {
            (d.marked_bounds[0][k] + d.marked_bounds[1][k]) / 2.0
        }));
        let whole = camera.map(std::array::from_fn(|k| {
            (a.envelope[0][k] + a.envelope[1][k]) / 2.0
        }));
        let expected: [f64; 2] =
            std::array::from_fn(|k| parent.at[k] + parent.scale * (centre[k] - whole[k]));
        // Every surviving arc lies on the circle about the selected parent's crop.
        let radii: Vec<_> = marker
            .lines
            .iter()
            .flatten()
            .map(|p| (p[0] - expected[0]).hypot(p[1] - expected[1]))
            .collect();
        assert!(!radii.is_empty());
        assert!(radii.iter().all(|r| (r - radii[0]).abs() < 1e-5));
    }
}

#[test]
fn edge_on_members_and_missing_source_views_are_refused_by_name() {
    let (_, a) = fixture("mounting_plate");
    for around in [json!("top"), json!(["f:hole:1", "top"])] {
        let error = model::plan(&a, &authored(around, Some("front"))).unwrap_err();
        assert!(
            error.message.contains("top")
                && error.message.contains("front")
                && error.message.contains("degenerate"),
            "{}",
            error.message
        );
    }
    for source in ["iso", "section_aa", "missing"] {
        let e = model::plan(&a, &authored(json!("f:hole:1"), Some(source))).unwrap_err();
        assert!(
            e.message.contains(source) && e.message.contains("f:hole:1"),
            "{}",
            e.message
        );
    }
    let mut s = authored(json!("f:hole:1"), Some("plan"));
    s.views.get_mut("plan").unwrap().omit = true;
    let e = model::plan(&a, &s).unwrap_err();
    assert!(e.message.contains("v:plan") && e.message.contains("f:hole:1"));
}

#[test]
fn list_crop_is_the_union_of_the_existing_member_crops() {
    let (_, a) = fixture("turned");
    let ids: Vec<_> = a
        .features
        .iter()
        .filter(|f| f.kind == "turned_step")
        .map(|f| f.id.clone())
        .collect();
    assert!(ids.len() >= 2);
    let first = model::plan(&a, &authored(json!(ids[0]), None)).unwrap();
    let second = model::plan(&a, &authored(json!(ids[1]), None)).unwrap();
    let both = model::plan(&a, &authored(json!(&ids[..2]), None)).unwrap();
    let bounds = |p: &model::Plan| detail_view(p).detail.as_ref().unwrap().bounds;
    let (x, y, u) = (bounds(&first), bounds(&second), bounds(&both));
    for k in 0..3 {
        assert_eq!(u[0][k], x[0][k].min(y[0][k]));
        assert_eq!(u[1][k], x[1][k].max(y[1][k]));
    }
    assert_ne!(u, x);
    assert_ne!(u, y);
}

#[test]
fn mixed_face_feature_lists_choose_the_most_visible_view_and_record_it() {
    let (_, a) = fixture("mounting_plate");
    let s = authored(json!(["f:hole:1", "top", "bottom", "front"]), None);
    let p = model::plan(&a, &s).unwrap();
    let v = detail_view(&p);
    assert_eq!(v.detail.as_ref().unwrap().source_view, "v:plan");
    assert!(
        v.basis.contains("natural source views differ") && v.basis.contains("3/4"),
        "{}",
        v.basis
    );
    // Member order cannot change the winner.
    let p = model::plan(
        &a,
        &authored(json!(["front", "bottom", "top", "f:hole:1"]), None),
    )
    .unwrap();
    assert_eq!(
        detail_view(&p).detail.as_ref().unwrap().source_view,
        "v:plan"
    );
}

#[test]
fn every_list_member_must_exist_and_empty_lists_are_refused() {
    let (_, a) = fixture("turned");
    for id in ["f:turned_step:999", "face:99999", "unknown"] {
        let e = model::plan(&a, &authored(json!(["f:turned_step:1", id]), None)).unwrap_err();
        assert!(e.message.contains(id), "{}", e.message);
    }
    assert!(
        model::plan(&a, &authored(json!([]), None))
            .unwrap_err()
            .message
            .contains("at least one")
    );
}

#[test]
fn saved_string_and_list_specs_round_trip_through_json_and_a_drawing() {
    let (part, a) = fixture("mounting_plate");
    let font = draftwright::text::Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(draftwright::dims::FONT),
    )
    .unwrap();
    let top = a
        .face_attachments
        .iter()
        .position(|f| f.surface.as_deref() == Some("plane") && f.axis == Some(2))
        .unwrap();
    for around in [
        json!("f:hole:1"),
        json!(["f:hole:1", format!("face:{top}")]),
    ] {
        let mut s = authored(around.clone(), Some("plan"));
        s.binding = Some(a.binding.clone());
        let saved = serde_json::to_value(&s).unwrap();
        assert_eq!(saved["views"]["detail_c"]["detail"]["around"], around);
        let read: Spec = serde_json::from_value(saved).unwrap();
        assert_eq!(s, read);
        let p = model::plan(&a, &read).unwrap();
        let drawing = model::render(&part, &a, &p, &font).unwrap();
        let svg = drawing.svg();
        assert_eq!(model::embedded_spec(svg.as_bytes()).unwrap().unwrap(), s);
    }
}

#[test]
fn a_groove_and_its_neighboring_shoulder_keep_both_model_crops() {
    let (_, a) = fixture("k5/groove-compound");
    let groove = "f:groove:1";
    let shoulder = "f:turned_step:3";
    let p = model::plan(&a, &authored(json!([groove, shoulder]), Some("front"))).unwrap();
    let union = detail_view(&p).detail.as_ref().unwrap();
    for id in [groove, shoulder] {
        let member = model::plan(&a, &authored(json!(id), Some("front"))).unwrap();
        let b = detail_view(&member).detail.as_ref().unwrap().bounds;
        assert!((0..3).all(|k| union.bounds[0][k] <= b[0][k] && union.bounds[1][k] >= b[1][k]));
    }
}

#[test]
fn section_parent_reuses_the_section_projection_without_duplicating_furniture() {
    let Some(a) = string_post() else { return };
    let dir = corpus::dir().unwrap();
    let (part, _) = model::analyse_native(&dir.join("gramel/string_post.step")).unwrap();
    let p = model::plan(&a, &authored(json!("f:turned_step:3"), Some("section_aa"))).unwrap();
    let v = detail_view(&p);
    let parent = p.views.iter().find(|v| v.id == "v:section_aa").unwrap();
    assert!(parent.cut.is_some());
    assert!(v.cut.is_none());
    assert_eq!(v.detail.as_ref().unwrap().source_cut, parent.cut);
    let mut projections = model::Projections::default();
    let geometry = model::render_geometry(&part, &a, &p, &mut projections).unwrap();
    for (_, g) in &geometry.views {
        for c in &g.breaks {
            let b = c.bbox();
            let ink = g.bbox.expect("a break is visible owned ink");
            assert!(
                b[0] >= ink[0] && b[1] >= ink[1] && b[2] <= ink[2] && b[3] <= ink[3],
                "break outside its owned visible ink bounds: {b:?} / {ink:?}"
            );
        }
    }
    assert!(
        geometry
            .views
            .iter()
            .find(|(id, _)| id == &v.id)
            .unwrap()
            .1
            .bbox
            .is_some()
    );
    // The detail must share the parent's cut HLR, rather than introducing an uncut camera.
    assert_eq!(
        projections.stats().projections,
        p.views.iter().filter(|v| v.detail.is_none()).count()
    );
    // Page-local rendering may no longer carry the parent section in its view list.
    let mut isolated = p.clone();
    isolated.views.retain(|view| view.id == v.id);
    let isolated_geometry =
        model::render_geometry(&part, &a, &isolated, &mut model::Projections::default()).unwrap();
    let original = geometry.views.iter().find(|(id, _)| id == &v.id).unwrap();
    assert_eq!(
        serde_json::to_value(original).unwrap(),
        serde_json::to_value(&isolated_geometry.views[0]).unwrap()
    );
    // An explicitly cut projection is the independent rendering oracle.
    isolated.views[0].cut = parent.cut.clone();
    isolated.views[0].detail.as_mut().unwrap().source_cut = None;
    let explicit =
        model::render_geometry(&part, &a, &isolated, &mut model::Projections::default()).unwrap();
    assert_eq!(
        serde_json::to_value(&isolated_geometry.views[0]).unwrap(),
        serde_json::to_value(&explicit.views[0]).unwrap()
    );
}

#[test]
fn sheet_centre_lines_in_a_head_detail_are_aligned_and_clipped() {
    let Some(a) = string_post() else { return };
    for settings in [
        json!({"sheet":{"size":"A3","scale":"2:1","centre_lines":true},
               "views":{"detail_a":{"detail":{"around":"f:turned_step:3","scale_factor":5}}}}),
        json!({"sheet":{"size":"A3","scale":"2:1"},
               "views":{"detail_a":{"centre_lines":true,"detail":{"around":"f:turned_step:3","scale_factor":5}}}}),
    ] {
        let p = model::plan(&a, &serde_json::from_value(settings).unwrap()).unwrap();
        let v = p.views.iter().find(|v| v.id == "v:detail_a").unwrap();
        let d = v.detail.as_ref().unwrap();
        let camera = haecceity::hlr::View::new(v.direction, v.up).unwrap();
        let corners: Vec<_> = (0..8)
            .map(|i| camera.map([0, 1, 2].map(|k| d.bounds[(i >> k) & 1][k])))
            .collect();
        let lo = [0, 1].map(|k| corners.iter().map(|p| p[k]).fold(f64::INFINITY, f64::min));
        let hi = [0, 1].map(|k| {
            corners
                .iter()
                .map(|p| p[k])
                .fold(f64::NEG_INFINITY, f64::max)
        });
        let crop = [
            v.at[0] - v.scale * (hi[0] - lo[0]) / 2.,
            v.at[1] - v.scale * (hi[1] - lo[1]) / 2.,
            v.at[0] + v.scale * (hi[0] - lo[0]) / 2.,
            v.at[1] + v.scale * (hi[1] - lo[1]) / 2.,
        ];
        let axes: Vec<_> = v.line_work.iter().filter(|w| w.kind == "axis").collect();
        assert!(
            !axes.is_empty(),
            "sheet/view centre_lines reaches the detail"
        );
        for c in axes.iter().flat_map(|w| &w.curves) {
            let b = c.bbox();
            assert!(
                (0..2).all(|k| b[k] >= crop[k] - 1e-6 && b[k + 2] <= crop[k + 2] + 1e-6),
                "axis {b:?} outside crop {crop:?}"
            );
            // string_post turns about z; its projected vertical axis is through x = 0.
            let axis_x = v.at[0] - v.scale * (lo[0] + hi[0]) / 2.;
            assert!((b[0] - axis_x).abs() < 1e-6 && (b[2] - axis_x).abs() < 1e-6);
        }
    }
}

#[test]
fn a_detail_caption_is_drawn_in_its_reserved_crop_slot() {
    let (part, a) = fixture("mounting_plate");
    let mut p = model::plan(&a, &authored(json!("f:hole:1"), Some("plan"))).unwrap();
    let v = p.views.iter_mut().find(|v| v.id == "v:detail_c").unwrap();
    let d = v.detail.as_mut().unwrap();
    // Include blank paper below the silhouette, so the ink and crop anchors differ.
    d.bounds = a.envelope;
    for k in 0..3 {
        d.bounds[0][k] -= 10.;
        d.bounds[1][k] += 10.;
    }
    let expected = [
        v.at[0],
        v.at[1] - (d.bounds[1][1] - d.bounds[0][1]) * d.scale / 2. - 8.,
    ];
    let font =
        draftwright::text::Font::new(include_bytes!("../fonts/IBMPlexMono-Regular.ttf").to_vec())
            .unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    let caption = drawing
        .text
        .iter()
        .find(|t| t.text.starts_with("DETAIL C ("))
        .unwrap();
    assert_eq!(
        caption.at, expected,
        "drawing must use the row protected during placement"
    );
}

#[test]
fn unavailable_detail_warning_uses_the_drawings_scale_notation() {
    let (part, a) = fixture("mounting_plate");
    let mut s = authored(json!("f:hole:1"), Some("plan"));
    s.sheet.size = Some("A4".into());
    s.sheet.scale = Some("1:5".into());
    s.views
        .get_mut("detail_c")
        .unwrap()
        .detail
        .as_mut()
        .unwrap()
        .scale_factor = 1000.;
    let p = model::plan_recovered(&a, &s).unwrap();
    let warning = p
        .problems
        .iter()
        .find(|p| p.code == "detail_unplaceable")
        .unwrap();
    assert!(
        warning.message.contains("scale 200:1"),
        "{}",
        warning.message
    );
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    assert!(
        model::render(&part, &a, &p, &font)
            .unwrap()
            .svg()
            .contains("<svg")
    );
}
