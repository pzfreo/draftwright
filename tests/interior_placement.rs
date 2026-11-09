//! Interior candidates require projection evidence and protect the complete sheet's ink.
#[path = "common/cache.rs"]
mod cache;

use draftwright::model::{self, Analysis, Plan, Spec};
use serde_json::json;
use std::path::Path;

fn plate() -> Analysis {
    model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step"),
    )
    .unwrap()
    .1
}
fn font() -> draftwright::text::Font {
    draftwright::text::Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"),
    )
    .unwrap()
}
fn interiors(p: &Plan) -> Vec<&draftwright::dims::PlannedDimension> {
    p.dimensions
        .iter()
        .filter(|d| d.basis.contains("proven blank interior"))
        .collect()
}
#[test]
fn blank_projected_face_prefers_interior_callouts() {
    let a = plate();
    let p = cache::default_plan(&a, false);
    assert!(
        !interiors(&p).is_empty(),
        "blank plate should carry an interior callout"
    );
    for d in interiors(&p) {
        assert!(
            !p.problems
                .iter()
                .any(|q| q.code == "annotation_over_view" && q.subjects.contains(&d.id))
        );
    }
}
#[test]
fn missing_projection_evidence_cannot_prove_blank_face() {
    let a = plate();
    assert!(!interiors(&cache::default_plan(&a, false)).is_empty());
    let mut value = serde_json::to_value(a).unwrap();
    value.as_object_mut().unwrap().remove("edge_ink");
    let old: Analysis = serde_json::from_value(value).unwrap();
    assert!(interiors(&cache::default_plan(&old, false)).is_empty());
}
#[test]
fn projected_linework_refuses_interior_labels() {
    let a = plate();
    assert!(!interiors(&cache::default_plan(&a, false)).is_empty());
    let mut value = serde_json::to_value(&a).unwrap();
    // A dense grid of edges in all three principal planes covers every interior candidate.
    let [lo, hi] = a.envelope;
    let mid: Vec<f64> = (0..3).map(|k| (lo[k] + hi[k]) / 2.0).collect();
    let edges = value["edge_ink"]["edges"].as_array_mut().unwrap();
    for i in -400..=400 {
        let t = i as f64 * 0.5;
        for (along, across, depth) in [
            (0, 1, 2),
            (1, 0, 2),
            (0, 2, 1),
            (2, 0, 1),
            (1, 2, 0),
            (2, 1, 0),
        ] {
            let mut a = [0.0; 3];
            let mut b = [0.0; 3];
            a[along] = -400.0;
            b[along] = 400.0;
            a[across] = t;
            b[across] = t;
            a[depth] = mid[depth];
            b[depth] = mid[depth];
            edges.push(json!([a, b]));
        }
    }
    let blocked: Analysis = serde_json::from_value(value).unwrap();
    assert!(interiors(&cache::default_plan(&blocked, false)).is_empty());
}
#[test]
fn interior_output_is_deterministic_and_replays_projection_evidence() {
    let a = plate();
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert!(!interiors(&p).is_empty());
    let copy: Analysis = serde_json::from_slice(&serde_json::to_vec(&a).unwrap()).unwrap();
    assert_eq!(
        serde_json::to_vec(&p).unwrap(),
        serde_json::to_vec(&model::plan(&copy, &Spec::default()).unwrap()).unwrap()
    );
}

#[test]
fn another_view_blocks_the_owning_views_interior() {
    let a = plate();
    let p = cache::default_plan(&a, false);
    assert!(!interiors(&p).is_empty());
    let own = p
        .views
        .iter()
        .find(|v| v.id == interiors(&p)[0].view)
        .unwrap();
    let mut foreign = own.clone();
    foreign.id = "v:iso".into();
    foreign.cut = None;
    let dims = draftwright::dims::plan(&a, &Spec::default(), &p.sheet, &[own.clone(), foreign])
        .unwrap()
        .0;
    assert!(
        !dims
            .iter()
            .any(|d| d.basis.contains("proven blank interior"))
    );
}

#[test]
fn labels_and_entire_interior_dimensions_clear_other_annotation_ink() {
    let a = plate();
    let p = cache::default_plan(&a, false);
    assert!(!interiors(&p).is_empty());
    for d in interiors(&p) {
        assert!(!p.problems.iter().any(|q| matches!(
            q.code.as_str(),
            "annotation_overlap" | "annotation_ink_overlap" | "annotation_leader_crossing"
        ) && q.subjects.contains(&d.id)));
    }
    let d = interiors(&p)[0];
    let spec: Spec =
        serde_json::from_value(json!({"dimensions": {d.id.clone(): {"place": {"pinned": true}}}}))
            .unwrap();
    let pinned = model::plan(&a, &spec).unwrap();
    assert_eq!(
        pinned
            .dimensions
            .iter()
            .find(|e| e.id == d.id)
            .unwrap()
            .placed,
        d.placed
    );
}

#[test]
fn failed_exterior_corridors_retry_the_whole_linear_dimension_inside() {
    let a = plate();
    let p = cache::default_plan(&a, false);
    let linear: Vec<_> = interiors(&p)
        .into_iter()
        .filter(|d| d.kind == "linear")
        .collect();
    assert!(
        !linear.is_empty(),
        "blocked exterior pitches/locations need an interior retry"
    );
    for d in linear {
        let placed = d.placed.as_ref().unwrap();
        assert_eq!(placed.arrows.len(), 2);
        assert_eq!(placed.lines.len(), 3);
        assert!(!p.problems.iter().any(|q| q.subjects.contains(&d.id)
            && matches!(
                q.code.as_str(),
                "annotation_overlap"
                    | "annotation_ink_overlap"
                    | "annotation_over_view"
                    | "extension_through_feature"
            )));
    }
}

#[test]
fn empty_space_without_projected_material_is_refused() {
    let a = plate();
    assert!(!interiors(&cache::default_plan(&a, false)).is_empty());
    let mut value = serde_json::to_value(a).unwrap();
    value["edge_ink"]["planes"] = json!([]);
    let empty: Analysis = serde_json::from_value(value).unwrap();
    assert!(interiors(&cache::default_plan(&empty, false)).is_empty());
}

#[test]
fn interior_provenance_does_not_hide_a_rendered_ink_overlap() {
    let file =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/mounting_plate.step");
    let (part, a) = cache::native(&file);
    let p = cache::default_plan(&a, false);
    assert!(!interiors(&p).is_empty());
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    let problems = draftwright::lint::structural::check(&drawing);
    for d in interiors(&p) {
        assert!(
            drawing
                .sheet
                .items
                .iter()
                .any(|item| item.name == d.id && item.interior)
        );
        assert!(
            !problems
                .iter()
                .any(|q| q.annotation.as_deref() == Some(&d.id)
                    && matches!(
                        q.code.as_str(),
                        "view_annotation_overlap" | "annotation_overlap" | "annotation_ink_overlap"
                    ))
        );
    }
}

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/interior_ink.rs"]
mod interior_ink;
#[path = "common/support.rs"]
mod support;

#[test]
fn all_quality_parts_interior_lines_arrows_and_tips_clear_rendered_ink() {
    let Some(root) = corpus::dir() else {
        return;
    };
    let files: Vec<_> = include_str!("../tools/quality_parts.txt").lines().collect();
    let count: usize = support::parallel(&files, |&file| {
        let (part, a) = cache::native(&root.join(file));
        let p = cache::default_plan(&a, false);
        let interior: Vec<_> = p
            .dimensions
            .iter()
            .filter(|d| d.region == Some(draftwright::dims::CandidateRegion::Interior))
            .collect();
        if interior.is_empty() {
            return 0;
        }
        let drawing = model::render(&part, &a, &p, &font()).unwrap();
        let mut count = 0;
        for d in interior {
            interior_ink::assert_clear(&drawing, d);
            count += 1;
        }
        count
    })
    .into_iter()
    .sum();
    assert!(count > 0);
}

#[test]
fn a_hidden_edge_in_an_arrow_wing_refuses_the_old_interior_placement() {
    let mut a = plate();
    let p = cache::default_plan(&a, false);
    let d = interiors(&p)
        .into_iter()
        .find(|d| d.kind == "callout")
        .unwrap();
    let placed = d.placed.as_ref().unwrap();
    let [tip, tail] = placed.arrows[0];
    let delta = [tail[0] - tip[0], tail[1] - tip[1]];
    let length = delta[0].hypot(delta[1]);
    let u = [delta[0] / length, delta[1] / length];
    let width = draftwright::dims::ARROW / 6.0;
    // Inside the filled head, well away from its centreline and the label.
    let at = [
        tip[0] + 0.95 * delta[0] - 0.9 * width * u[1],
        tip[1] + 0.95 * delta[1] + 0.9 * width * u[0],
    ];
    let b = placed.label_box;
    assert!(at[0] < b[0] - 0.5 || at[0] > b[2] + 0.5 || at[1] < b[1] - 0.5 || at[1] > b[3] + 0.5);
    let v = p.views.iter().find(|v| v.id == d.view).unwrap();
    let view = draftwright::haecceity::hlr::View::new(v.direction, v.up).unwrap();
    let centre = view.map(std::array::from_fn(|k| {
        (a.envelope[0][k] + a.envelope[1][k]) / 2.0
    }));
    let point = |sign: f64| -> [f64; 3] {
        let q: [f64; 2] =
            std::array::from_fn(|k| (at[k] + sign * 0.025 * u[k] - v.at[k]) / v.scale + centre[k]);
        std::array::from_fn(|k| q[0] * view.x[k] + q[1] * view.y[k])
    };
    a.edge_ink
        .as_mut()
        .unwrap()
        .edges
        .push(vec![point(-1.0), point(1.0)]);
    let after = cache::default_plan(&a, false);
    let got = after.dimensions.iter().find(|q| q.id == d.id).unwrap();
    assert!(
        got.region != Some(draftwright::dims::CandidateRegion::Interior) || got.placed != d.placed,
        "{} kept an interior arrowhead over a hidden edge",
        d.id
    );
}

#[test]
fn an_occluded_native_boss_diameter_uses_its_visible_profile() {
    let Some(root) = corpus::dir() else { return };
    let (part, a) = cache::native(&root.join("nist/nist_ftc_06_asme1_rd.stp"));
    let p = cache::default_plan(&a, false);
    let d = p
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:boss:f21/boss.diameter")
        .unwrap();
    assert_eq!(d.view, "v:front");
    assert_eq!(d.label, "4× ø44.45");
    assert_eq!(d.measurements.len(), 4);
    let drawing = model::render(&part, &a, &p, &font()).unwrap();
    interior_ink::assert_clear(&drawing, d);
}
