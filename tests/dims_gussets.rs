//! Native #256 fixtures: group ownership, true views, lint credit and author/PMI intent.
use draftwright::{
    lint,
    model::{self, Analysis, PmiMode, Spec},
    text::Font,
};
use std::path::Path;

fn fixture(name: &str) -> (draftwright::haecceity::Part, Analysis) {
    model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(format!("tests/fixtures/parts/gusset-{name}.step.gz")),
    )
    .unwrap()
}

fn check(name: &str, expected: &[(&str, &str, f64, &str)]) {
    let (part, a) = fixture(name);
    let group = lint::gussets::groups(&a);
    assert_eq!(group.len(), 1);
    assert!(group[0].read.is_ok(), "{:?}", group[0].read);
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    for &(parameter, view, value, label) in expected {
        let found: Vec<_> = plan
            .dimensions
            .iter()
            .filter(|d| d.measurements.iter().any(|m| m.parameter == parameter))
            .collect();
        assert_eq!(found.len(), 1, "{parameter}: {found:?}");
        let d = found[0];
        assert_eq!(d.view, view);
        assert_eq!(d.label, label);
        assert!((d.value - value).abs() < 1e-6);
        assert!(d.placed.is_some(), "{d:?}");
        assert!(
            d.measurements
                .iter()
                .any(|m| m.feature == group[0].owner.id)
        );
    }
    assert_eq!(plan.sheet.size, "A4");
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "feature_not_dimensioned"
                && p.subjects.iter().any(|s| s.contains("gusset"))),
        "{:?}",
        plan.problems
    );

    assert!(
        !plan.problems.iter().any(|p| matches!(
            p.code.as_str(),
            "annotation_overlap"
                | "annotation_ink_overlap"
                | "annotation_leader_crossing"
                | "label_crowded"
        )),
        "{:?}",
        plan.problems
    );

    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "dimensions_not_ported"
                && p.subjects.iter().any(|s| s.contains("gusset")))
    );
    let drawing = model::render(
        &part,
        &a,
        &plan,
        &Font::new(draftwright::api::MONO.to_vec()).unwrap(),
    )
    .unwrap();
    let findings: Vec<_> = lint::lint(&a, &drawing)
        .into_iter()
        .filter(|i| i.code.starts_with("gusset_rib_requirement_"))
        .collect();
    assert!(findings.is_empty(), "{findings:?}");
}

#[test]
fn mirror_pair_states_each_size_once_and_lint_credits_both_members() {
    check(
        "mirror",
        &[
            ("gusset_thickness.length", "v:front", 8., "2× 8"),
            ("gusset_leg.length.y", "v:side", 30., "2× 30"),
            ("gusset_leg.length.z", "v:side", 30., "2× 30"),
            ("gusset_spacing.length", "v:front", 40., "40"),
            ("gusset_location.length", "v:front", 40., "40"),
        ],
    );
}

#[test]
fn linear_array_states_pitch_and_first_centre() {
    check(
        "array",
        &[
            ("gusset_thickness.length", "v:front", 6., "3× 6"),
            ("gusset_leg.length.y", "v:side", 22., "3× 22"),
            ("gusset_leg.length.z", "v:side", 26., "3× 26"),
            ("gusset_pitch.length", "v:front", 25., "25"),
            ("gusset_location.length", "v:front", 20., "20"),
        ],
    );
}

#[test]
fn transverse_view_is_group_intent_and_must_show_thickness_true() {
    let (_, a) = fixture("mirror");
    let mut spec = Spec::default();
    spec.features
        .entry("f:gusset_rib_pattern:f7".into())
        .or_default()
        .gusset_view = Some("plan".into());
    let p = model::plan_recovered(&a, &spec).unwrap();
    for d in p.dimensions.iter().filter(|d| {
        d.measurements
            .iter()
            .any(|m| m.parameter.starts_with("gusset_"))
    }) {
        assert_eq!(
            d.view,
            if d.measurements[0].parameter.starts_with("gusset_leg.") {
                "v:side"
            } else {
                "v:plan"
            }
        );
    }
    spec.features
        .get_mut("f:gusset_rib_pattern:f7")
        .unwrap()
        .gusset_view = Some("side".into());
    assert!(model::plan_recovered(&a, &spec).is_err());
}

#[test]
fn unreadable_array_keeps_port_gap_and_unverifiable_lint() {
    let (_, mut a) = fixture("array");
    let f = a
        .features
        .iter_mut()
        .find(|f| f.kind == "gusset_rib_pattern")
        .unwrap();
    f.parameters.insert("pitch".into(), serde_json::json!(99.0));
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    assert!(!p.dimensions.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| m.parameter.starts_with("gusset_"))
    }));
    assert_eq!(
        p.problems
            .iter()
            .filter(|p| p.code == "dimensions_not_ported"
                && p.subjects.iter().any(|s| s.contains("gusset")))
            .count(),
        4
    );
    assert!(
        lint::gussets::coverage(&a, &[])
            .iter()
            .all(|i| i.code == "gusset_rib_requirement_unverifiable")
    );
}

#[test]
fn pmi_thickness_wins_and_first_fills_only_missing_gusset_sizes() {
    use draftwright::haecceity::{
        p21::Document,
        pmi::{self as hp, *},
        step::read_part_definitions,
    };
    use std::io::Read;
    let mut raw = Vec::new();
    flate2::read::GzDecoder::new(include_bytes!("fixtures/parts/gusset-mirror.step.gz").as_slice())
        .read_to_end(&mut raw)
        .unwrap();
    // The two triangular faces of the left rib, proved by the fixture's native attachments.
    let (_, a) = fixture("mirror");
    let faces: Vec<_> = a
        .face_attachments
        .iter()
        .enumerate()
        .filter(|(_, f)| {
            f.axis == Some(0) && f.support.first().is_some_and(|x| *x == -24. || *x == -16.)
        })
        .map(|(i, _)| i)
        .collect();
    assert_eq!(faces.len(), 2);
    let mut pmi = PartPmi::default();
    for i in faces {
        pmi.features
            .push(Feature::Items(vec![Anchor::Face(FaceIndex(i))]));
    }
    let mm = |s| Value::length(Decimal::parse(s).unwrap(), LengthUnit::Millimetre);
    pmi.dimensions.push(Dimension {
        kind: DimensionKind::Location {
            from: FeatureId(0),
            to: FeatureId(1),
            kind: LocationKind::LinearDistance,
            path: None,
            directed: false,
            angle: None,
        },
        nominal: Some(mm("8.")),
        tolerance: DimTolerance::Deviations(Bounds::new(mm("0.1"), mm("-0.1")).unwrap()),
        qualifier: None,
        modifiers: vec![],
        principle: None,
    });
    let doc = Document::parse(raw.clone()).unwrap();
    let (edit, _) = hp::write(
        &doc,
        &read_part_definitions(&raw).unwrap(),
        &[(PartId(0), pmi)],
        hp::write::Mode::Replace,
        hp::write::PresentationPolicy::RemovePresentation,
    )
    .unwrap();
    let written = doc.apply(&edit).unwrap().bytes;
    let (_, a) =
        draftwright::model::analyse_native_step(Path::new("gusset-pmi.step"), &written).unwrap();
    for mode in [PmiMode::First, PmiMode::Strict, PmiMode::Ignore] {
        let p = draftwright::model::plan_recovered(
            &a,
            &Spec {
                pmi: mode,
                ..Spec::default()
            },
        )
        .unwrap();
        let sizes: Vec<_> = p
            .dimensions
            .iter()
            .filter(|d| {
                d.placed.is_some()
                    && d.measurements
                        .iter()
                        .any(|m| m.parameter == "gusset_thickness.length")
            })
            .collect();
        match mode {
            PmiMode::First => {
                assert_eq!(sizes.len(), 1);
                assert!(
                    sizes[0]
                        .measurements
                        .iter()
                        .any(|m| m.feature.starts_with("pmi:")),
                    "{sizes:?}"
                );
                assert!(sizes[0].label.contains("0.1"));
                assert!(p.dimensions.iter().any(|d| {
                    d.placed.is_some()
                        && d.measurements
                            .iter()
                            .any(|m| m.parameter == "gusset_leg.length.y")
                }));
            }
            PmiMode::Strict => assert!(!p.dimensions.iter().any(|d| {
                d.placed.is_some()
                    && d.measurements
                        .iter()
                        .any(|m| m.parameter == "gusset_leg.length.y")
            })),
            PmiMode::Ignore => {
                assert_eq!(sizes.len(), 1);
                assert_eq!(sizes[0].label, "2× 8");
            }
        }
    }
}

#[test]
fn a_lone_rib_owns_four_uncounted_requirements() {
    let (_, mut a) = fixture("mirror");
    a.features
        .retain(|f| !f.kind.starts_with("gusset") || f.id == "f:gusset_rib:f7");
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let dimensions: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| {
            d.measurements
                .iter()
                .any(|m| m.parameter.starts_with("gusset_"))
        })
        .collect();
    assert_eq!(dimensions.len(), 4);
    assert!(dimensions.iter().all(|d| d.placed.is_some()
        && !d.label.contains('×')
        && d.measurements.iter().all(|m| m.feature == "f:gusset_rib:f7")));
}
