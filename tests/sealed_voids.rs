//! Analytic specimen authored by tools/make_review_fixtures.py, without a CAD kernel.
use draftwright::{
    haecceity, hole_table, lint,
    model::{self, Spec},
};
use std::path::Path;
const INPUT: &[u8] = include_bytes!("fixtures/parts/synthetic_sealed_voids.step");

#[test]
fn sealed_cylinders_are_cavities_not_holes_or_pattern_requirements() {
    let part = haecceity::read_step(INPUT).unwrap();
    assert!(part.solid_is_geometrically_valid(0));
    let classifier = haecceity::classify::Classifier::new(&part);
    assert_eq!(
        classifier.classify([8., 8., 10.]),
        haecceity::classify::State::Out
    );
    assert_eq!(
        classifier.classify([16., 12., 10.]),
        haecceity::classify::State::In
    );
    let raw = quiddity::correspondence::recognise(&part);
    // Quiddity 0.4.7 reports exact closed shells, without contradictory holes.
    // Keep these provider preconditions separate from draftwright's drawing guard.
    let doc = quiddity::recognition::document(&raw, part.faces.len());
    let voids: Vec<_> = doc
        .records
        .iter()
        .filter(|r| r.family == "interior_voids" && r.record_type == "InteriorVoid")
        .collect();
    assert_eq!(voids.len(), 4);
    assert_eq!(
        doc.records.iter().filter(|r| r.family == "holes").count(),
        0
    );
    assert!(!doc.records.iter().any(|r| r.family == "hole_patterns"));
    assert!(
        voids
            .iter()
            .all(|v| v.parameters["openings"].as_array().unwrap().is_empty()
                && v.parameters["volume_method"] == "closed_shell")
    );
    let (part, a) = model::analyse_native_step(Path::new("sealed-cylinders.step"), INPUT).unwrap();
    assert_eq!(
        a.features
            .iter()
            .filter(|f| f.kind == "interior_void")
            .count(),
        4
    );
    assert!(a.features.iter().all(|f| f.kind != "hole"));
    assert!(a.recognition.as_ref().unwrap().patterns.is_empty());
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert!(!p.problems.iter().any(|p| p.code == "detail_unplaceable"));
    assert!(!p.views.iter().any(|v| v.id.starts_with("v:detail")));
    for height in [4., 16.] {
        assert!(
            p.dimensions.iter().any(|d| d.placed.is_some()
                && d.measurements
                    .iter()
                    .any(|m| m.feature == "step_level" && m.value == height)),
            "cavity cap height {height} stays on the parent"
        );
    }
    assert!(p.dimensions.iter().all(|d| {
        !d.label.contains("2.5")
            && !d
                .measurements
                .iter()
                .any(|m| m.feature.starts_with("f:hole"))
    }));
    assert!(hole_table::table_rows(&a, &p).is_empty());
    assert!(p.hole_table.is_none());
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    let findings = lint::lint(&a, &drawing);
    let voids: Vec<_> = findings
        .iter()
        .filter(|f| f.code == "interior_void")
        .collect();
    assert_eq!(voids.len(), 4);
    for f in voids {
        assert!(
            f.message
                .contains("closed internal cavity, no opening to the outside")
        );
        assert!(f.message.contains("2.5 × 2.5 × 12 mm"));
        assert!(f.message.contains("centre (") && f.message.contains(", 10) mm"));
        assert!(!f.message.contains("machinable"));
    }
    assert!(!drawing.svg().contains("⌀2.5"));
}

#[test]
fn cavity_face_ownership_drops_mixed_patterns_but_keeps_unclaimed_holes() {
    let part = haecceity::read_step(INPUT).unwrap();
    let raw = quiddity::correspondence::recognise(&part);
    let mut doc = quiddity::recognition::document(&raw, part.faces.len());
    // Explicitly inject the old contradictory claims: the current recognizer no
    // longer supplies them. Each cylinder's faces and bounds come from the specimen.
    let mut holes = Vec::new();
    for void in doc.records.iter().filter(|r| r.family == "interior_voids") {
        let face = *void
            .faces
            .iter()
            .find(|&&f| {
                matches!(
                    part.faces[f].surface,
                    haecceity::geom::Surface::Cylinder { .. }
                )
            })
            .unwrap();
        let bounds = part.face_bounds(face);
        let parameters = serde_json::json!({
            "axis": [0., 0., -1.],
            "location": [(bounds.min[0]+bounds.max[0])/2., (bounds.min[1]+bounds.max[1])/2., bounds.max[2]],
            "diameter": 2.5, "depth": 12., "bottom": "flat",
            "cbore": null, "spotface": null, "csink": null
        });
        let mut hole = void.clone();
        hole.id = format!("holes/{}", holes.len());
        hole.family = "holes".into();
        hole.record_type = "HoleRecord".into();
        hole.faces = vec![face];
        hole.parameters = parameters.as_object().unwrap().clone();
        holes.push(hole);
    }
    assert_eq!(holes.len(), 4);
    let mut pattern = holes[0].clone();
    pattern.id = "hole_patterns/0".into();
    pattern.family = "hole_patterns".into();
    pattern.record_type = "RectangularHoleSet".into();
    pattern.faces = holes.iter().flat_map(|h| h.faces.iter().copied()).collect();
    pattern.parameters = serde_json::json!({
        "holes": holes.iter().map(|h| &h.parameters).collect::<Vec<_>>(),
        "center": [16., 12., 16.], "width": 16., "height": 8., "angle": 0.
    })
    .as_object()
    .unwrap()
    .clone();
    doc.records.extend(holes);
    doc.records.push(pattern);
    let mut kept_void = false;
    // Contract-level negative control: only one cavity is claimed by this provider
    // document. The other three hole records must not disappear by equal diameter,
    // shared axis, position, or membership of the rejected pattern.
    doc.records.retain(|r| {
        if r.family != "interior_voids" {
            return true;
        }
        if kept_void {
            false
        } else {
            kept_void = true;
            true
        }
    });
    let read = draftwright::bridge::native::read(&part, &doc).unwrap();
    assert_eq!(read.holes.len(), 3);
    assert!(read.patterns.is_empty());
    assert!(read.holes.iter().all(|h| h.fingerprint.pattern.is_none()));
}
