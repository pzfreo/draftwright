use crate::geometry_ids::*;
use crate::model::Analysis;
fn block() -> Analysis {
    crate::model::analyse_native(
        &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/recognition/steps/stepped_block.step"),
    )
    .unwrap()
    .1
}
#[test]
fn a_level_without_witness_support_uses_its_own_plane_faces() {
    let mut a = block();
    let original = height_parameter(&a, 0.);
    assert_eq!(original, "step_height.length.f6");
    a.level_supports.clear();
    assert_eq!(height_parameter(&a, 0.), original);
    a.face_attachments.clear();
    assert_eq!(
        height_parameter(&a, 0.),
        original,
        "immutable face references are evidence too"
    );
    a.edge_ink = None;
    assert_eq!(height_parameter(&a, 0.), "step_height.length.h10");
    assert_eq!(
        parameter_span(&a, "step_height.length.h10"),
        Some((2, -10., 0.))
    );
}
#[test]
fn shoulder_spans_and_axes_use_geometry_without_saved_aliases() {
    let mut a = block();
    for f in &mut a.features {
        f.legacy_id = None;
    }
    assert_eq!(position_parameter(&a, 0, 0.), "step_position.length.f7");
    assert_eq!(a.ladder_axis("step_position.length.f7"), Some(0));
    assert_eq!(
        parameter_span(&a, "step_position.length.f7"),
        Some((0, -20., 0.))
    );
    a.risers.clear();
    assert_eq!(position_parameter(&a, 0, 0.), "step_position.length.f7");
    assert_eq!(a.ladder_axis("step_position.length.f7"), Some(0));
}
#[test]
fn presentation_keeps_geometric_enumeration_after_aliases_and_records_are_removed() {
    let mut a = block();
    let template = a.features[0].clone();
    a.features = (0..12)
        .map(|n| {
            let mut f = template.clone();
            f.kind = "hole".into();
            f.id = format!("f:hole:f{}", 100 + n);
            f.faces = vec![100 + n];
            f.legacy_id = None;
            f.presentation_order = None;
            f
        })
        .collect();
    assign_presentation_order(&mut a.features, &mut []);
    a.features.remove(0);
    for f in &mut a.features {
        f.legacy_id = Some("incorrect migration alias".into());
    }
    let mut ids: Vec<_> = a.features.iter().map(|f| f.id.clone()).collect();
    ids.sort_by_key(|id| presentation_key(&a, id).into_owned());
    assert_eq!(
        &ids[..4],
        ["f:hole:f109", "f:hole:f110", "f:hole:f111", "f:hole:f101"]
    );
}
#[test]
fn clearing_migration_aliases_keeps_the_drawing_decisions() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/mounting_plate.step");
    let (_, mut a) = crate::model::analyse_native(&path).unwrap();
    let before = crate::model::plan(&a, &crate::model::Spec::default()).unwrap();
    for f in &mut a.features {
        f.legacy_id = None;
    }
    if let Some(r) = &mut a.recognition {
        for p in &mut r.patterns {
            p.legacy_id = None;
        }
    }
    let after = crate::model::plan(&a, &crate::model::Spec::default()).unwrap();
    assert_eq!(
        serde_json::to_value(&before.dimensions).unwrap(),
        serde_json::to_value(&after.dimensions).unwrap()
    );
    assert_eq!(
        serde_json::to_value(&before.hole_table).unwrap(),
        serde_json::to_value(&after.hole_table).unwrap()
    );
}
