//! Issue 122: the editor's actual plate, with semantic PMI written as Specify writes it.
use draftwright::{
    Session,
    haecceity::{
        p21::Document,
        pmi::{self as hp, *},
        step::read_part_definitions,
    },
    model::{self, Analysis, DimensionSource, PmiMode, Spec},
};
use std::path::Path;
const PLATE: &[u8] = include_bytes!("fixtures/parts/mounting_plate.step");

fn mm(v: f64) -> Value {
    Value::length(
        Decimal::parse(&if v.fract() == 0. {
            format!("{v}.")
        } else {
            v.to_string()
        })
        .unwrap(),
        LengthUnit::Millimetre,
    )
}
fn face(p: &mut PartPmi, f: usize) -> FeatureId {
    p.features
        .push(Feature::Items(vec![Anchor::Face(FaceIndex(f))]));
    FeatureId(p.features.len() - 1)
}
fn distance(p: &mut PartPmi, from: usize, to: usize, value: f64, basic: bool) {
    let from = face(p, from);
    let to = face(p, to);
    p.dimensions.push(Dimension {
        kind: DimensionKind::Location {
            from,
            to,
            kind: LocationKind::LinearDistance,
            path: None,
            directed: false,
            angle: None,
        },
        nominal: Some(mm(value)),
        tolerance: if basic {
            DimTolerance::Basic
        } else {
            DimTolerance::Deviations(Bounds::new(mm(0.05), mm(-0.05)).unwrap())
        },
        qualifier: None,
        modifiers: vec![],
        principle: None,
    });
}
fn bytes(p: PartPmi) -> Vec<u8> {
    let parts = read_part_definitions(PLATE).unwrap();
    let doc = Document::parse(PLATE.to_vec()).unwrap();
    let (edit, _) = hp::write(
        &doc,
        &parts,
        &[(PartId(0), p)],
        hp::write::Mode::Replace,
        hp::write::PresentationPolicy::RemovePresentation,
    )
    .unwrap();
    doc.apply(&edit).unwrap().bytes
}
fn analysed(b: &[u8]) -> Analysis {
    model::analyse_native_step(Path::new("plate.step"), b)
        .unwrap()
        .1
}
fn plan(a: &Analysis, s: &Spec) -> model::Plan {
    model::plan_recovered(a, s).unwrap()
}
#[test]
fn specified_distance_from_either_end_owns_the_hole_location() {
    let a = analysed(PLATE);
    assert_eq!([a.envelope[0][0], a.envelope[1][0]], [-50., 50.]);
    for x in [50., 95.] {
        assert!(
            a.features.iter().any(|f| f.kind == "hole"
                && (f.fingerprint.position[0] - a.envelope[0][0] - x).abs() < 1e-5),
            "missing x={x}"
        );
    }
    let plain = plan(&a, &Spec::default());
    assert!(plain.dimensions.iter().any(|d| d.label == "2× 30"));
    for (from, v) in [(0, 95.), (5, 5.)] {
        let mut p = PartPmi::default();
        distance(&mut p, from, 12, v, false);
        let written = bytes(p);
        let a = analysed(&written);
        let result = plan(&a, &Spec::default());
        assert!(
            result
                .dimensions
                .iter()
                .any(|d| d.label == format!("{v} ±0.05")),
            "{:?}",
            result
                .dimensions
                .iter()
                .map(|d| &d.label)
                .collect::<Vec<_>>()
        );
        let hole = a
            .features
            .iter()
            .find(|f| f.kind == "hole" && f.faces.contains(&12))
            .unwrap();
        let id = format!("d:{}/location.length.x", hole.id);
        assert!(
            !result.dimensions.iter().any(|d| d.id == id),
            "engine location {id} duplicates PMI"
        );
        assert!(result.omitted.iter().any(|d|d.id==id&&matches!(d.state,draftwright::items::State::Covered{..})),"covered ledger missing {id}");
    }
}
#[test]
fn modes_keep_plain_drawings_and_strict_exposes_gaps() {
    let a = analysed(PLATE);
    let plain = plan(&a, &Spec::default());
    let (part, _) = model::analyse_native_step(Path::new("plate.step"), PLATE).unwrap();
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &plain, &font).unwrap();
    let svg = drawing.svg();
    for pmi in [PmiMode::Strict, PmiMode::Ignore] {
        let s = Spec {
            pmi,
            ..Default::default()
        };
        let p = plan(&a, &s);
        let drawing = model::render(&part, &a, &p, &font).unwrap();
        assert_eq!(svg, drawing.svg());
    }
    let mut p = PartPmi::default();
    distance(&mut p, 5, 12, 5., false);
    let written = bytes(p);
    let a = analysed(&written);
    let strict = plan(
        &a,
        &Spec {
            pmi: PmiMode::Strict,
            ..Default::default()
        },
    );
    assert_eq!(
        strict.dimensions.len(),
        1,
        "{:?}",
        strict.dimensions.iter().map(|d| &d.id).collect::<Vec<_>>()
    );
    assert!(
        strict
            .problems
            .iter()
            .any(|p| p.code == "feature_not_located")
    );
    assert!(
        strict
            .problems
            .iter()
            .any(|p| p.code == "feature_not_sized")
    );
    let mut authored = Spec {
        pmi: PmiMode::Strict,
        dimension_source: DimensionSource::Authored,
        ..Default::default()
    };
    authored
        .dimensions
        .insert("d:envelope/width.length".into(), Default::default());
    assert!(
        plan(&a, &authored)
            .dimensions
            .iter()
            .any(|d| d.id == "d:envelope/width.length")
    );
    let ignored = plan(
        &a,
        &Spec {
            pmi: PmiMode::Ignore,
            ..Default::default()
        },
    );
    assert_eq!(ignored.dimensions, plain.dimensions);
    assert_eq!(
        ignored
            .problems
            .iter()
            .filter(|p| p.code == "pmi_ignored")
            .count(),
        1
    );
    assert!(a.pmi.is_some());
    // The live replacement path observes the same policy and exposes extraction unchanged.
    let mut session = Session::new(
        part,
        analysed(PLATE),
        Spec {
            pmi: PmiMode::Strict,
            ..Default::default()
        },
    )
    .unwrap();
    session.set_pmi(&written).unwrap();
    assert_eq!(session.plan().dimensions.len(), 1);
}
#[test]
fn datum_origins_and_individual_basics_replace_pattern_pitches() {
    for b in [0, 5] {
        let mut p = PartPmi::default();
        for (letter, f) in [("A", 2), ("B", b), ("C", 1)] {
            let f = face(&mut p, f);
            p.datums
                .push(Datum::new(DatumLabel::new(letter).unwrap(), Some(f), vec![]).unwrap());
        }
        let a = analysed(PLATE);
        for f in a.features.iter().filter(|f| f.kind == "hole") {
            let x = f.fingerprint.position[0] - a.envelope[0][0];
            let y = f.fingerprint.position[1] - a.envelope[0][1];
            distance(
                &mut p,
                b,
                f.faces[0],
                if b == 5 { 100. - x } else { x },
                true,
            );
            distance(&mut p, 1, f.faces[0], y, true);
        }
        let a = analysed(&bytes(p));
        let result = plan(&a, &Spec::default());
        assert!(
            !result.dimensions.iter().any(|d| !d.id.starts_with("d:pmi:")
                && d.measurements
                    .iter()
                    .any(|m| m.parameter.contains("pitch.length"))),
            "{:?}",
            result
                .dimensions
                .iter()
                .filter(|d| d
                    .measurements
                    .iter()
                    .any(|m| m.parameter.contains("pitch.length")))
                .collect::<Vec<_>>()
        );
        assert!(!result.dimensions.iter().any(|d| {
            !d.id.starts_with("d:pmi:")
                && d.measurements
                    .iter()
                    .any(|m| m.parameter == "location.length.x")
        }));
        for (letter, face) in [("A", 2), ("B", b), ("C", 1)] {
            let datum = result
                .gdt
                .iter()
                .find(|g| g.kind == "datum_feature" && g.label == letter)
                .expect("declared origin datum is drawn without a frame");
            assert_eq!(datum.faces, [face]);
            assert!(datum.placed.is_some());
        }
        assert!(
            !result
                .problems
                .iter()
                .any(|p| p.code == "feature_located_twice"),
            "{:?}",
            result
                .problems
                .iter()
                .filter(|p| p.code == "feature_located_twice")
                .collect::<Vec<_>>()
        );
    }
    let mut p = PartPmi::default();
    let f = face(&mut p, 5);
    p.datums
        .push(Datum::new(DatumLabel::new("B").unwrap(), Some(f), vec![]).unwrap());
    let a = analysed(&bytes(p));
    let result = plan(&a, &Spec::default());
    assert!(
        result
            .dimensions
            .iter()
            .filter(|d| d.id.ends_with("location.length.x"))
            .all(|d| d.basis.contains("datum B"))
    );
    assert!(
        result
            .dimensions
            .iter()
            .any(|d| d.id.ends_with("location.length.x") && d.value == 5.)
    );
    let s = Spec {
        origin: std::collections::BTreeMap::from([("x".into(), "min".into())]),
        ..Default::default()
    };
    assert!(
        plan(&a, &s)
            .dimensions
            .iter()
            .any(|d| d.id.ends_with("location.length.x")
                && d.value == 95.
                && d.basis.contains("origin/x min"))
    );
    let key = a
        .dimension_references()
        .iter()
        .find_map(|(key, values)| match values.as_slice() {
            [model::dimension_geometry::Reference::Plane { origin, normal, .. }]
                if normal[0].abs() > 0.999 && (origin[0] - a.envelope[0][0]).abs() < 1e-6 =>
            {
                Some(key.clone())
            }
            _ => None,
        })
        .unwrap();
    assert!(key.starts_with("face:v1:"));
    let s = Spec {
        origin: std::collections::BTreeMap::from([("x".into(), key.clone())]),
        ..Default::default()
    };
    assert!(
        plan(&a, &s)
            .dimensions
            .iter()
            .any(|d| d.id.ends_with("location.length.x")
                && d.value == 95.
                && d.basis.contains(&key))
    );
}
#[test]
fn invalid_modes_origins_and_transactional_edits_are_refused() {
    assert!(serde_json::from_str::<Spec>(r#"{"pmi":"annotate"}"#).is_err());
    let a = analysed(PLATE);
    for (axis, value) in [("w", "min"), ("x", "face:12"), ("x", "missing"), ("x", "")] {
        let s = Spec {
            origin: std::collections::BTreeMap::from([(axis.into(), value.into())]),
            ..Default::default()
        };
        assert!(model::plan(&a, &s).is_err(), "{axis} {value}");
    }
    assert!(
        model::edit(
            &Spec::default(),
            &serde_json::json!([{"op":"set","path":"origin/w","value":"min"}])
        )
        .is_err()
    );
}
#[test]
fn competing_authored_locations_are_kept_and_reported_by_both_checks() {
    let mut p = PartPmi::default();
    distance(&mut p, 0, 12, 95., false);
    distance(&mut p, 5, 12, 5., false);
    let b = bytes(p);
    let (part, a) = model::analyse_native_step(Path::new("plate.step"), &b).unwrap();
    let result = plan(&a, &Spec::default());
    for label in ["95 ±0.05", "5 ±0.05"] {
        assert!(result.dimensions.iter().any(|d| d.label == label));
    }
    let problem = result
        .problems
        .iter()
        .find(|p| p.code == "feature_located_twice")
        .expect("competing origins are reported");
    assert!(
        problem
            .subjects
            .iter()
            .filter(|s| s.starts_with("d:pmi:"))
            .count()
            >= 2
    );
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &result, &font).unwrap();
    let found = draftwright::lint::location::coverage(&a, &draftwright::lint::marks(&drawing));
    assert!(found.iter().any(
        |p| p.code == "feature_located_twice" && p.subjects.first() == problem.subjects.first()
    ));
}
#[test]
fn position_tolerances_cite_and_draw_datums_at_either_end() {
    for b in [0, 5] {
        let mut p = PartPmi::default();
        for (letter, f) in [("A", 2), ("B", b), ("C", 1)] {
            let f = face(&mut p, f);
            p.datums
                .push(Datum::new(DatumLabel::new(letter).unwrap(), Some(f), vec![]).unwrap());
        }
        let a = analysed(PLATE);
        for f in a.features.iter().filter(|f| f.kind == "hole") {
            let x = f.fingerprint.position[0] - a.envelope[0][0];
            let y = f.fingerprint.position[1] - a.envelope[0][1];
            distance(
                &mut p,
                b,
                f.faces[0],
                if b == 5 { 100. - x } else { x },
                true,
            );
            distance(&mut p, 1, f.faces[0], y, true);
            let target = face(&mut p, f.faces[0]);
            p.tolerances.push(GeometricTolerance {
                kind: ToleranceKind::Position,
                target: ToleranceTarget::Feature(target),
                magnitude: Some(mm(0.1)),
                zone: None,
                modifiers: vec![],
                unit_basis: None,
                maximum: None,
                unequal: None,
                datums: Some(
                    DatumSystem::new(
                        (0..3)
                            .map(|d| Compartment {
                                references: vec![DatumReference {
                                    datum: DatumId(d),
                                    modifiers: vec![],
                                }],
                                modifiers: vec![],
                            })
                            .collect(),
                    )
                    .unwrap(),
                ),
                auxiliary: vec![],
                description: None,
            });
        }
        let a = analysed(&bytes(p));
        let result = plan(&a, &Spec::default());
        assert_eq!(
            result
                .gdt
                .iter()
                .filter(|g| g.kind == "datum_feature" && g.placed.is_some())
                .count(),
            3
        );
        assert!(!result.dimensions.iter().any(|d| {
            !d.id.starts_with("d:pmi:")
                && d.measurements
                    .iter()
                    .any(|m| m.parameter.contains("pitch.length"))
        }));
        assert!(
            !result
                .dimensions
                .iter()
                .any(|d| d.id.ends_with("location.length.x"))
        );
    }
}
#[test]
fn cylindrical_datum_draws_its_symbol_and_reports_the_measurement_origin_fallback() {
    let mut p = PartPmi::default();
    let f = face(&mut p, 12);
    p.datums
        .push(Datum::new(DatumLabel::new("B").unwrap(), Some(f), vec![]).unwrap());
    let a = analysed(&bytes(p));
    let result = plan(&a, &Spec::default());
    assert!(
        result
            .problems
            .iter()
            .any(|p| p.code == "datum_origin_unusable" && p.severity == "info")
    );
    assert!(
        result
            .dimensions
            .iter()
            .any(|d| d.id.ends_with("location.length.x") && d.value == 95.)
    );
    // No frame cites it and no dimension measures from it: not drawn by default, and reported
    // (maintainer 2026-10-25, revising #141); `sheet/uncited_datums: true` draws it.
    let datum = result
        .gdt
        .iter()
        .find(|g| g.kind == "datum_feature" && g.label == "B")
        .expect(
            "a cylindrical datum stays in the plan when it cannot be a planar measurement origin",
        );
    assert_eq!(datum.faces, [12]);
    assert!(datum.placed.is_none());
    assert!(result.problems.iter().any(|p| p.code == "datum_not_drawn"));
    let mut every = Spec::default();
    every.sheet.uncited_datums = Some(true);
    let drawn = plan(&a, &every);
    let datum = drawn
        .gdt
        .iter()
        .find(|g| g.kind == "datum_feature" && g.label == "B")
        .unwrap();
    assert!(datum.placed.is_some());
}
#[test]
fn strict_semantic_thread_callouts_do_not_add_engine_drill_sizes() {
    let a = analysed(include_bytes!("fixtures/parts/editor_pmi.step"));
    let p = plan(
        &a,
        &Spec {
            pmi: PmiMode::Strict,
            ..Default::default()
        },
    );
    let callouts: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.measurements.iter().any(|m| m.parameter == "thread"))
        .collect();
    assert!(!callouts.is_empty());
    for d in callouts {
        assert!(d.label.contains("M5"), "{}", d.label);
        assert!(!d.label.contains("⌀4.2"), "{}", d.label);
    }
}
#[test]
fn one_pattern_member_plus_pitches_locates_the_array_without_a_second_origin() {
    let mut p = PartPmi::default();
    distance(&mut p, 0, 6, 20., true);
    distance(&mut p, 1, 6, 15., true);
    let a = analysed(&bytes(p));
    let result = plan(&a, &Spec::default());
    let failures: Vec<_> = result
        .problems
        .iter()
        .filter(|p| p.code == "feature_located_twice" || p.code == "feature_not_located")
        .collect();
    assert!(failures.is_empty(), "{failures:?}");
    assert!(
        result
            .dimensions
            .iter()
            .any(|d| d.id.contains("pitch.length"))
    );
}
#[test]
fn one_pmi_axis_does_not_cover_an_unrelated_coincident_hole_axis() {
    let mut p = PartPmi::default();
    distance(&mut p, 1, 12, 30., true);
    let a = analysed(&bytes(p));
    let result = plan(&a, &Spec::default());
    let failures: Vec<_> = result
        .problems
        .iter()
        .filter(|p| p.code == "feature_located_twice" || p.code == "feature_not_located")
        .collect();
    assert!(failures.is_empty(), "{failures:?}");
    let engine = result
        .dimensions
        .iter()
        .filter(|d| !d.id.starts_with("d:pmi:"))
        .flat_map(|d| &d.measurements)
        .any(|m| m.feature == "f:hole:4" && m.parameter == "location.length.y");
    assert!(engine, "the unrelated ⌀8 hole still needs its y location");
}
#[test]
fn a_shared_pmi_span_locates_every_record_it_claims() {
    let mut authored = PartPmi::default();
    distance(&mut authored, 1, 12, 30., true);
    distance(&mut authored, 1, 9, 30., true);
    let written = bytes(authored);
    let (part, a) = model::analyse_native_step(Path::new("plate.step"), &written).unwrap();
    let p = plan(
        &a,
        &Spec {
            hole_table: draftwright::hole_table::Setting::Never,
            ..Default::default()
        },
    );
    let shared: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| {
            d.placed.is_some()
                && d.label == "30"
                && d.measurements.iter().any(|m| m.feature.starts_with("pmi:"))
        })
        .collect();
    assert_eq!(shared.len(), 1, "{shared:?}");
    assert_eq!(
        shared[0]
            .measurements
            .iter()
            .filter(|m| m.feature.starts_with("pmi:"))
            .count(),
        2
    );
    assert!(
        !p.problems
            .iter()
            .any(|q| q.code == "feature_not_located" || q.code == "feature_located_twice"),
        "{:?}",
        p.problems
    );
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    let issues = draftwright::lint::lint(&a, &drawing);
    assert!(
        !issues
            .iter()
            .any(|q| q.code == "feature_not_located" || q.code == "feature_located_twice"),
        "{issues:?}"
    );
}

#[test]
fn ignore_renders_the_plain_drawing_including_cosmetic_ink() {
    let (part, a) = model::analyse_native_step(
        Path::new("plate.step"),
        include_bytes!("fixtures/parts/editor_pmi.step"),
    )
    .unwrap();
    let mut plain = a.clone();
    plain.pmi = None;
    let expected = plan(&plain, &Spec::default());
    let ignored = plan(
        &a,
        &Spec {
            pmi: PmiMode::Ignore,
            ..Default::default()
        },
    );
    let locating = |p: &model::Plan| {
        p.problems
            .iter()
            .filter(|p| p.code == "feature_not_located" || p.code == "feature_located_twice")
            .map(|p| &p.id)
            .cloned()
            .collect::<std::collections::BTreeSet<_>>()
    };
    assert_eq!(locating(&expected), locating(&ignored));
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let mut first = model::render(&part, &plain, &expected, &font).unwrap();
    let mut second = model::render(&part, &a, &ignored, &font).unwrap();
    first.spec = None;
    second.spec = None;
    assert_eq!(first.svg(), second.svg());
}

#[test]
fn located_once_checks_native_ink_and_rejects_a_broken_witness() {
    let (part, a) = model::analyse_native_step(Path::new("plate.step"), PLATE).unwrap();
    let p = plan(
        &a,
        &Spec {
            hole_table: draftwright::hole_table::Setting::Never,
            origin: std::collections::BTreeMap::from([("x".into(), "min".into())]),
            ..Default::default()
        },
    );
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let mut drawing = model::render(&part, &a, &p, &font).unwrap();
    let d = p
        .dimensions
        .iter()
        .find(|d| {
            d.placed.is_some()
                && d.measurements
                    .first()
                    .is_some_and(|m| m.feature == "f:hole:7" && m.parameter == "location.length.x")
        })
        .unwrap();
    let has_gap = |drawing: &draftwright::output::Drawing| {
        draftwright::lint::lint(&a, drawing).iter().any(|i| {
            i.code == "feature_not_located"
                && i.subjects.contains(&"f:hole:7".into())
                && i.id.ends_with("location.length.x")
        })
    };
    assert!(!has_gap(&drawing));
    let item = drawing
        .sheet
        .items
        .iter_mut()
        .find(|i| i.name == d.id)
        .unwrap();
    item.lines.clear();
    assert!(
        has_gap(&drawing),
        "source claims cannot repair missing witness ink"
    );
}

#[test]
fn datum_rebased_plane_location_also_states_its_coincident_plate_thickness() {
    use std::io::Read as _;
    for compressed in [
        include_bytes!("fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz").as_slice(),
        include_bytes!("fixtures/pmi/step/nist_ctc_01_merge.step.gz").as_slice(),
    ] {
        let mut step = Vec::new();
        flate2::read::GzDecoder::new(compressed)
            .read_to_end(&mut step)
            .unwrap();
        let a = analysed(&step);
        let p = plan(&a, &Spec::default());
        let owner = p
            .dimensions
            .iter()
            .find(|d| d.id == "d:step_level/step_height.length.h50")
            .unwrap();
        assert_eq!(owner.label, "50");
        assert!(owner.basis.contains("measured from datum A"));
        for (feature, parameter) in [
            ("step_level", "step_height.length.h50"),
            ("f:plate:1", "thickness.length"),
        ] {
            let claims: Vec<_> = p
                .dimensions
                .iter()
                .filter(|d| {
                    d.measurements
                        .iter()
                        .any(|m| m.feature == feature && m.parameter == parameter)
                })
                .collect();
            assert_eq!(claims.len(), 1, "{feature}/{parameter}");
            assert_eq!(claims[0].id, owner.id);
        }
        assert!(p.omitted.iter().any(|d| {
            d.id == "d:f:plate:1/thickness.length"
                && matches!(&d.state, draftwright::items::State::Covered { by } if by == &owner.id)
        }));
        let placed = owner.placed.as_ref().unwrap();
        let k = usize::from(placed.rotation == 90.);
        let span = [placed.lines[0][0][k], placed.lines[1][0][k]];
        let view = p.views.iter().find(|v| v.id == owner.view).unwrap();
        assert!(((span[1] - span[0]).abs() / view.scale - 50.).abs() < 1e-6);
        assert!(!p.dimensions.iter().any(|d| {
            d.id != owner.id
                && d.view == owner.view
                && d.kind == "linear"
                && d.label == owner.label
                && d.placed.as_ref().is_some_and(|q| {
                    q.rotation == placed.rotation
                        && q.lines.len() >= 2
                        && ((q.lines[0][0][k] - span[0]).abs() < 1e-6
                            && (q.lines[1][0][k] - span[1]).abs() < 1e-6
                            || (q.lines[1][0][k] - span[0]).abs() < 1e-6
                                && (q.lines[0][0][k] - span[1]).abs() < 1e-6)
                })
        }));
        assert_eq!(
            p.problems
                .iter()
                .filter(|q| q.code == "feature_not_located")
                .count(),
            // cx/ctc01: the already printed 225 ordinate also proves both
            // slot centres in y; retaining the datum chain must keep zero gaps.
            0,
            "the retained datum chain must not introduce another gap"
        );
        assert!(!p.problems.iter().any(|q| q.code == "feature_located_twice"));
    }
}

#[test]
fn generated_hole_schedules_state_each_size_and_coordinate_without_extra_locators() {
    use std::io::Read as _;
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(
        include_bytes!("fixtures/pmi/step/nist_ftc_10_asme1_ap242-e2.stp.gz").as_slice(),
    )
    .read_to_end(&mut step)
    .unwrap();
    let a = analysed(&step);
    let p = plan(&a, &Spec::default());
    let tables: std::collections::BTreeSet<_> = p
        .hole_table
        .iter()
        .chain(&p.hole_tables)
        .map(|t| t.id.as_str())
        .collect();
    assert!(
        !tables.is_empty(),
        "the regression needs a generated coordinate schedule"
    );
    assert!(
        !p.problems.iter().any(|q| q.code == "feature_located_twice"
            && q.subjects.iter().any(|s| tables.contains(s.as_str()))),
        "{:?}",
        p.problems
            .iter()
            .filter(|q| q.code == "feature_located_twice")
            .collect::<Vec<_>>()
    );
    // A PMI size callout is replaced by its rows (the maintainer's decision, 2026-10-28): none
    // is drawn beside a row of its holes, the frames on those holes attaching to the holes
    // themselves instead (2026-10-29).
    let tabled: std::collections::BTreeSet<_> = p
        .hole_table
        .iter()
        .chain(&p.hole_tables)
        .flat_map(|t| &t.rows)
        .map(|r| r.feature.as_str())
        .collect();
    assert!(
        !p.dimensions.iter().any(|d| {
            d.kind == "callout"
                && d.placed.is_some()
                && d.measurements.iter().any(|m| m.feature.starts_with("pmi:"))
                && d.measurements
                    .iter()
                    .any(|m| tabled.contains(m.feature.as_str()))
        }),
        "a PMI size callout is drawn beside its schedule rows"
    );
    assert!(!serde_json::to_string(&p).unwrap().contains("station_"));
    assert!(
        !p.dimensions
            .iter()
            .any(|d| d.placed.is_some() && d.label.contains("(62.5)")),
        "reference PMI is omitted by default"
    );
    let mut checked = 0;
    let mut pmi_claims = std::collections::BTreeSet::new();
    let mut sizes = std::collections::BTreeMap::new();
    for row in p
        .hole_table
        .iter()
        .chain(&p.hole_tables)
        .flat_map(|t| &t.rows)
    {
        assert!(
            row.cells[1..4].iter().all(|cell| !cell.is_empty()),
            "{row:?}"
        );
        let letter = row.tag.chars().next().unwrap();
        if let Some(size) = sizes.insert(letter, row.cells[1].clone()) {
            assert_eq!(size, row.cells[1]);
        }
        let claims: Vec<_> = row
            .measurements
            .iter()
            .filter(|m| m.feature.starts_with("pmi:"))
            .collect();
        if !claims.is_empty() {
            assert!(
                !row.cells[1].is_empty(),
                "schedule must state the source size for {}",
                row.feature
            );
            // The row's own hole (its sizes and coordinates) and the PMI sizes it now states; no
            // other feature's locator.
            assert!(
                row.measurements
                    .iter()
                    .all(|m| m.feature.starts_with("pmi:") || m.feature == row.feature),
                "{row:?}"
            );
            for claim in claims {
                assert!(
                    pmi_claims.insert((claim.feature.clone(), claim.parameter.clone())),
                    "{} twice",
                    claim.feature
                );
            }
            checked += 1;
        }
    }
    assert!(
        checked > 0,
        "the fixture must exercise PMI size callouts replaced by schedule rows"
    );
}

#[test]
fn schedule_aliases_keep_existing_source_covered_ids_unique() {
    use std::io::Read as _;
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(
        include_bytes!("fixtures/pmi/step/bracket_positions.step.gz").as_slice(),
    )
    .read_to_end(&mut step)
    .unwrap();
    // Exercise installation independently of whether a source-only table frees a corridor.
    let p = plan(
        &analysed(&step),
        &Spec {
            hole_table: draftwright::hole_table::Setting::Always,
            ..Default::default()
        },
    );
    assert!(!p.hole_tables.is_empty());
    let mut ids = std::collections::BTreeSet::new();
    for item in draftwright::items::items(&p) {
        assert!(ids.insert(item.id.clone()), "duplicate item {}", item.id);
    }
}

fn editor_fixture(bytes: &[u8]) -> (draftwright::haecceity::Part, Analysis) {
    use std::io::Read as _;
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(bytes)
        .read_to_end(&mut step)
        .unwrap();
    model::analyse_native_step(Path::new("editor.step"), &step).unwrap()
}

#[test]
fn specify_composite_location_uses_the_datum_normal_not_a_box_diagonal() {
    let (part, a) = editor_fixture(include_bytes!("fixtures/pmi/plate.specify018.step.gz"));
    let draftwright::pmi::Reading::Read(report) = a.pmi.as_ref().unwrap() else {
        panic!("PMI read")
    };
    let r = report
        .records
        .iter()
        .find(|r| r.source_id == "dimension:#1100")
        .unwrap();
    assert_eq!(r.face_groups, [vec![1], vec![6, 7, 8, 10, 11, 12, 13]]);
    assert_eq!(r.value, Some(30.));
    assert!(r.basic);
    let g = r.geometry.as_ref().unwrap();
    assert!(g.reasons.is_empty(), "{:?}", g.reasons);
    assert_eq!(g.axis.as_deref(), Some("Y"));
    assert!((g.stations[1][1] - g.stations[0][1] - 30.).abs() < 1e-8);
    // An actual source member carries the centre station. Choosing it must not
    // depend on a nominal that happens to match a different bore.
    assert_eq!(g.stations[1][0], 45.);
    let mut wrong = r.clone();
    wrong.value = Some(31.);
    let refused = draftwright::pmi_lowering::dimension_geometry(&part, &wrong);
    assert!(
        refused
            .reasons
            .iter()
            .any(|s| s.contains("30 mm differs from nominal 31 mm"))
    );
    let p = plan(&a, &Spec::default());
    assert!(
        !p.problems.iter().any(|p| p.code == "feature_not_located"),
        "composite normal locations retain their included pattern's centre station"
    );
    assert!(p.dimensions.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| m.feature.contains("f1,6,7,8,10,11,12,13"))
            && d.placed.is_some()
    }));
}

#[test]
fn strict_pmi_references_show_only_unstated_engine_quantities() {
    let (_, a) = editor_fixture(include_bytes!(
        "fixtures/pmi/mounting-plate.specify018.step.gz"
    ));
    let mut s = Spec {
        pmi: PmiMode::Strict,
        ..Default::default()
    };
    let strict = plan(&a, &s);
    s.sheet.references = Some(true);
    let refs = plan(&a, &s);
    assert!(
        refs.dimensions
            .iter()
            .any(|d| d.reference && d.placed.is_some())
    );
    for d in &refs.dimensions {
        if !d.measurements.iter().any(|m| m.feature.starts_with("pmi:")) {
            assert!(d.reference, "{} {}", d.id, d.label);
        }
    }
    for pmi in [PmiMode::Strict, PmiMode::First] {
        s.pmi = pmi;
        let p = plan(&a, &s);
        assert!(
            !p.dimensions
                .iter()
                .any(|d| d.reference && ["(76)", "(48)"].contains(&d.label.as_str())),
            "PMI pitches repeated as references"
        );
    }
    for d in &strict.dimensions {
        let same = refs.dimensions.iter().find(|r| r.id == d.id).unwrap();
        assert_eq!(same.label, d.label);
        let source: Vec<_> = same
            .measurements
            .iter()
            .filter(|m| m.feature.starts_with("pmi:"))
            .collect();
        let original: Vec<_> = d
            .measurements
            .iter()
            .filter(|m| m.feature.starts_with("pmi:"))
            .collect();
        assert_eq!(source, original);
        assert!(!same.reference);
    }
}

#[test]
fn pmi_width_can_use_the_other_edge_on_view() {
    let (_, a) = editor_fixture(include_bytes!("fixtures/pmi/plate.specify018.step.gz"));
    let p = plan(&a, &Spec::default());
    let width = p.dimensions.iter().find(|d| d.label == "100").unwrap();
    let mut s = Spec::default();
    s.dimensions.entry(width.id.clone()).or_default().view = Some("plan".into());
    let other = plan(&a, &s);
    let d = other.dimensions.iter().find(|d| d.id == width.id).unwrap();
    assert_eq!(d.view, "v:plan");
    assert!(d.placed.is_some());
    assert_eq!(d.measurements, width.measurements);
    assert_eq!(d.label, width.label);
}

#[test]
fn strict_references_share_a_source_diameter_with_its_through_callout() {
    let (_, a) = editor_fixture(include_bytes!("fixtures/pmi/plate.specify018.step.gz"));
    let p = plan(
        &a,
        &Spec {
            pmi: PmiMode::Strict,
            sheet: model::SheetSpec {
                references: Some(true),
                ..Default::default()
            },
            ..Default::default()
        },
    );
    let bore: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter == "bore.diameter" && m.value == 8.)
        })
        .collect();
    assert_eq!(bore.len(), 1);
    assert!(!bore[0].reference);
    assert!(
        bore[0].label.contains("THRU"),
        "through condition remains stated"
    );
    assert!(
        !p.dimensions
            .iter()
            .any(|d| d.reference && d.label.contains("⌀8"))
    );
    let draftwright::pmi::Reading::Read(report) = a.pmi.as_ref().unwrap() else {
        panic!("PMI read")
    };
    let record = report
        .records
        .iter()
        .find(|r| r.kind == "diameter" && r.value == Some(8.) && r.faces == [9])
        .unwrap();
    let ids = report.identities();
    let id = ids.id(&record.source_id).unwrap();
    assert!(
        bore[0]
            .measurements
            .iter()
            .any(|m| m.feature == id && m.value == 8.)
    );
}

#[test]
fn semantic_text_draws_part_note_and_feature_flag_without_presentation_matching() {
    let (part, a) = editor_fixture(include_bytes!("fixtures/pmi/pmi_plate.xcaf-pmi.step.gz"));
    let p = plan(&a, &Spec::default());
    assert_eq!(p.sheet.size, "A4");
    assert!(
        p.notes
            .iter()
            .any(|n| n.text == "Remove all burrs and break sharp edges" && n.placed.is_some())
    );
    let note = p
        .gdt
        .iter()
        .find(|n| {
            n.cells
                .iter()
                .any(|s| s == "Mask these features before coating with primer")
        })
        .expect("feature flag");
    let placed = note.placed.as_ref().expect("flag placed");
    assert!(!placed.lines.is_empty());
    assert!(placed.strokes.len() >= 3, "triangle flag is visible");
    assert!(p.problems.iter().all(|p| {
        !p.refs
            .iter()
            .any(|r| r == "manufacturing_requirement:#559" || r == "surface_label:#564")
    }));
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let svg = model::render(&part, &a, &p, &font).unwrap().svg();
    assert!(svg.contains("Remove all burrs"));
    assert!(svg.contains("Mask these features"));
}

#[test]
fn pmi_slot_layout_retries_physical_witnesses_without_changing_authored_intent() {
    let (_, a) = editor_fixture(include_bytes!(
        "fixtures/pmi/mounting-plate.specify018.step.gz"
    ));
    let p = plan(&a, &Spec::default());
    assert_eq!(p.sheet.size, "A4");
    assert!(
        !p.problems.iter().any(|p| p.code == "feature_not_located"),
        "shared pitches retain every pattern location constraint"
    );
    assert!(
        !p.problems
            .iter()
            .any(|p| p.code == "extension_through_feature"),
        "{:?}",
        p.problems
            .iter()
            .filter(|p| p.code == "extension_through_feature")
            .map(|p| &p.message)
            .collect::<Vec<_>>()
    );
    let draftwright::pmi::Reading::Read(report) = a.pmi.as_ref().unwrap() else {
        panic!("PMI read")
    };
    let ids = report.identities();
    for r in report
        .records
        .iter()
        .filter(|r| r.category == draftwright::pmi::Category::Dimension)
    {
        if let Some(value) = r.value {
            let identity = ids.id(&r.source_id).unwrap();
            let claims: Vec<_> = p
                .dimensions
                .iter()
                .flat_map(|d| &d.measurements)
                .filter(|m| m.feature == identity)
                .collect();
            assert_eq!(claims.len(), 1, "{}", r.source_id);
            assert_eq!(claims[0].value, value);
        }
    }
}
