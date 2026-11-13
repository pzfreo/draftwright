//! Datum placement regressions: inside dimension lanes and valid alternative attachments.
use std::path::Path;

use draftwright::dims::{PlannedDimension, gdt};
use draftwright::model::{self, Analysis, Plan, Spec};

fn spindle() -> (Analysis, Plan) {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/samples/cnc-review-spindle.step.gz");
    let (_, a) = model::analyse_native(&path).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    (a, plan)
}

fn only_datum(a: &mut Analysis, letter: &str) {
    let Some(draftwright::pmi::Reading::Read(report)) = &mut a.pmi else {
        panic!("spindle has PMI")
    };
    report.records.retain(|r| {
        r.label == letter || r.category == draftwright::pmi::Category::GeometricTolerance
    });
}

fn isolated_spec(a: &Analysis) -> Spec {
    let mut s = Spec::default();
    if let Some(draftwright::pmi::Reading::Read(report)) = &a.pmi {
        let ids = report.identities();
        for r in report
            .records
            .iter()
            .filter(|r| r.category == draftwright::pmi::Category::GeometricTolerance)
        {
            if let Ok(id) = ids.id(&r.source_id) {
                s.annotations.insert(
                    id.into(),
                    serde_json::from_value(serde_json::json!({"omit":true})).unwrap(),
                );
            }
        }
    }
    s
}

fn obstacle(plan: &Plan, line: [[f64; 2]; 2]) -> PlannedDimension {
    let mut d = plan
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .clone();
    d.id = "test:dimension".into();
    d.view = "v:front".into();
    let p = d.placed.as_mut().unwrap();
    p.lines = vec![line];
    p.arrows.clear();
    p.label_box = [350.0, 250.0, 360.0, 253.0];
    d
}

fn place(a: &Analysis, p: &Plan, dims: &[PlannedDimension]) -> gdt::PlannedGdt {
    let (items, _) = gdt::plan(a, &isolated_spec(a), &p.sheet, &p.views, dims);
    let datums: Vec<_> = items
        .into_iter()
        .filter(|g| g.kind == "datum_feature")
        .collect();
    assert_eq!(datums.len(), 1);
    datums.into_iter().next().unwrap()
}

/// Resetting an editor placement leaves an empty entry and restores every automatic choice.
fn reset_matches_automatic(a: &Analysis, p: &Plan, dims: &[PlannedDimension], g: &gdt::PlannedGdt) {
    let mut reset = isolated_spec(a);
    reset.annotations.insert(
        g.id.clone(),
        serde_json::from_value(serde_json::json!({"place":{}})).unwrap(),
    );
    let (items, _) = gdt::plan(a, &reset, &p.sheet, &p.views, dims);
    let item = items.iter().find(|i| i.id == g.id).unwrap();
    assert_eq!(item.placed, g.placed);
    assert_eq!(item.side, g.side);
}

#[test]
fn spindle_datums_have_no_crossings_or_overlaps() {
    let (a, plan) = spindle();
    assert_eq!(
        (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
        ("A4", "1:1")
    );
    let ids: Vec<_> = plan
        .gdt
        .iter()
        .filter(|g| g.kind == "datum_feature")
        .map(|g| &g.id)
        .collect();
    assert_eq!(ids.len(), 2);
    let findings: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| {
            p.subjects.iter().any(|id| ids.contains(&id))
                && (p.code.contains("crossing")
                    || p.code.contains("overlap")
                    || p.code == "annotation_outside_area")
        })
        .collect();
    assert!(findings.is_empty(), "{findings:#?}");
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, _) =
        model::analyse_native(&root.join("tests/fixtures/samples/cnc-review-spindle.step.gz"))
            .unwrap();
    let font =
        draftwright::text::Font::open(&root.join("fonts").join(draftwright::dims::FONT)).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let findings: Vec<_> = draftwright::lint::structural::check(&drawing)
        .into_iter()
        .filter(|p| p.subjects.iter().any(|id| ids.contains(&id)))
        .collect();
    assert!(findings.is_empty(), "rendered datum lint: {findings:#?}");
}

#[test]
fn datum_prefers_inside_the_first_dimension_lane() {
    let (mut a, p) = spindle();
    only_datum(&mut a, "B");
    let original = place(&a, &p, &[]);
    let tip = original.placed.unwrap().tip;
    let lane = tip[0] + 35.0;
    let d = obstacle(&p, [[lane, tip[1] - 10.0], [lane, tip[1] + 10.0]]);
    let g = place(&a, &p, std::slice::from_ref(&d));
    assert_eq!(g.side, "right");
    reset_matches_automatic(&a, &p, &[d], &g);
    let b = g.placed.unwrap().label_box;
    assert!(
        b[0] > tip[0] && b[2] < lane - 1.0,
        "inside gap: {b:?}, lane {lane}"
    );
}

#[test]
fn cylinder_datum_uses_the_other_proven_silhouette() {
    let (mut a, p) = spindle();
    only_datum(&mut a, "B");
    let original = place(&a, &p, &[]).placed.unwrap().tip;
    let d = obstacle(
        &p,
        [
            [original[0] + 5.0, 10.0],
            [original[0] + 5.0, p.sheet.page[1] - 10.0],
        ],
    );
    let g = place(&a, &p, std::slice::from_ref(&d));
    reset_matches_automatic(&a, &p, &[d], &g);
    assert_eq!(g.side, "left");
    let placed = g.placed.unwrap();
    assert!(placed.tip[0] < original[0]);
    // Both attachment points are on the same cylinder, at opposite radial extremes.
    let Some(draftwright::pmi::Reading::Read(report)) = &a.pmi else {
        unreachable!()
    };
    let datum = report
        .records
        .iter()
        .find(|r| r.category == draftwright::pmi::Category::Datum && r.label == "B")
        .unwrap();
    let geometry = datum.geometry.as_ref().unwrap();
    let face = &a.face_attachments[datum.faces[0]];
    assert_eq!(face.surface.as_deref(), Some("cylinder"));
    assert!(!geometry.sites.is_empty());
    let view = p.views.iter().find(|v| v.id == "v:front").unwrap();
    let centre = [0, 1, 2].map(|k| (a.envelope[0][k] + a.envelope[1][k]) / 2.0);
    assert!(
        face.sites.iter().any(|s| {
            let page = [
                view.at[0] + view.scale * (s[0] - centre[0]),
                view.at[1] + view.scale * (s[2] - centre[2]),
            ];
            (page[0] - placed.tip[0]).hypot(page[1] - placed.tip[1]) < 1e-6
        }),
        "opposite silhouette must be a reader-proven trimmed-face point"
    );
}

#[test]
fn planar_datum_stands_on_a_face_extension_line() {
    let (mut a, p) = spindle();
    only_datum(&mut a, "A");
    let original = place(&a, &p, &[]).placed.unwrap().tip;
    let view = p.views.iter().find(|v| v.id == "v:front").unwrap();
    let half_width = (a.envelope[1][0] - a.envelope[0][0]) * view.scale / 2.0;
    let ends = [view.at[0] - half_width, view.at[0] + half_width];
    let d = obstacle(
        &p,
        [
            [ends[0] - 1.0, original[1] - 6.0],
            [ends[1] + 1.0, original[1] - 6.0],
        ],
    );
    let g = place(&a, &p, std::slice::from_ref(&d));
    reset_matches_automatic(&a, &p, &[d], &g);
    assert_eq!(g.side, "below");
    let placed = g.placed.unwrap();
    assert!(
        placed.tip[0] < ends[0] || placed.tip[0] > ends[1],
        "extension tip {:?}, outline {ends:?}",
        placed.tip
    );
    assert!((placed.tip[1] - original[1]).abs() < 1e-6);
    assert!(
        placed
            .lines
            .iter()
            .any(|[u, v]| (u[1] - original[1]).abs() < 1e-6
                && (v[1] - original[1]).abs() < 1e-6
                && u[0].min(v[0]) <= placed.tip[0]
                && u[0].max(v[0]) >= placed.tip[0]),
        "triangle must stand on the drawn extension"
    );
}

#[test]
fn cited_spindle_datums_have_separate_attachments_and_short_leaders() {
    let (_, plan) = spindle();
    let datum = |letter: &str| {
        plan.gdt
            .iter()
            .find(|g| g.kind == "datum_feature" && g.label == letter)
            .unwrap()
    };
    let (a, b) = (datum("A"), datum("B"));
    let (pa, pb) = (a.placed.as_ref().unwrap(), b.placed.as_ref().unwrap());
    let height = pa.label_box[3] - pa.label_box[1];
    assert!(
        (pa.tip[0] - pb.tip[0]).hypot(pa.tip[1] - pb.tip[1]) > height,
        "{pa:?} / {pb:?}"
    );
    let length: f64 = pb
        .lines
        .iter()
        .map(|[a, b]| (b[0] - a[0]).hypot(b[1] - a[1]))
        .sum();
    assert!(length < 20., "B leader: {length}");
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "annotation_leader_crossing" && p.subjects.contains(&b.id)),
        "{:?}",
        plan.problems
            .iter()
            .filter(|p| p.subjects.contains(&b.id))
            .collect::<Vec<_>>()
    );
    for fa in &pa.fills {
        for fb in &pb.fills {
            let bounds = |f: &Vec<[f64; 2]>| {
                f.iter().fold(
                    [
                        f64::INFINITY,
                        f64::INFINITY,
                        f64::NEG_INFINITY,
                        f64::NEG_INFINITY,
                    ],
                    |r, p| {
                        [
                            r[0].min(p[0]),
                            r[1].min(p[1]),
                            r[2].max(p[0]),
                            r[3].max(p[1]),
                        ]
                    },
                )
            };
            let (a, b) = (bounds(fa), bounds(fb));
            assert!(
                a[2] < b[0] || b[2] < a[0] || a[3] < b[1] || b[3] < a[1],
                "triangles overlap: {a:?} / {b:?}"
            );
        }
    }
}

/// GRM-03's two datums: B is drawn; A, a cylindrical datum no frame cites and no dimension measures
/// from, is not drawn by default and is reported (maintainer 2026-10-25, revising #141).
/// `sheet/uncited_datums: true` draws both, clear of other annotation.
#[test]
fn grm03_draws_its_used_datum_and_reports_the_uncited_one() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/pmi/step/GRM-03_thumbwheel_drive_screw_AP242_PMI.step.gz");
    let (_, a) = model::analyse_native(&path).unwrap();
    let automatic = model::plan_recovered(&a, &Spec::default()).unwrap();
    for g in automatic.gdt.iter().filter(|g| g.kind == "datum_feature") {
        assert_eq!(g.placed.is_some(), g.label == "B", "{}", g.label);
    }
    assert!(
        automatic
            .problems
            .iter()
            .any(|p| p.code == "datum_not_drawn"
                && p.severity == "info"
                && p.message.starts_with("Datum A "))
    );
    let mut every = Spec::default();
    every.sheet.uncited_datums = Some(true);
    let plan = model::plan_recovered(&a, &every).unwrap();
    let Some(draftwright::pmi::Reading::Read(report)) = &a.pmi else {
        panic!("GRM has PMI")
    };
    assert_eq!(
        report
            .records
            .iter()
            .filter(|r| r.category == draftwright::pmi::Category::Datum)
            .count(),
        2
    );
    let datums: Vec<_> = plan
        .gdt
        .iter()
        .filter(|g| g.kind == "datum_feature")
        .collect();
    assert_eq!(datums.len(), 2, "every declared datum is author intent");
    assert!(datums.iter().all(|g| g.placed.is_some()));
    assert!(
        plan.problems.iter().all(|p| !matches!(
            p.code.as_str(),
            "annotation_leader_crossing" | "annotation_overlap" | "annotation_ink_overlap"
        )),
        "{:#?}",
        plan.problems
    );
}

#[test]
fn overlapping_datum_triangles_are_errors_even_with_separate_letter_boxes() {
    let (_, plan) = spindle();
    assert!(gdt::symbol_collisions(&plan.gdt).is_empty());
    let mut datums: Vec<_> = plan
        .gdt
        .iter()
        .filter(|g| g.kind == "datum_feature")
        .cloned()
        .collect();
    assert_eq!(datums.len(), 2);
    // Corrupt only the filled ink: keeping the letters and leaders apart must
    // not conceal a symbol overlap (including a placement pinned by an editor).
    datums[1].placed.as_mut().unwrap().fills = datums[0].placed.as_ref().unwrap().fills.clone();
    let issues = gdt::symbol_collisions(&datums);
    assert_eq!(issues.len(), 1, "{issues:?}");
    assert_eq!(issues[0].code, "datum_symbol_overlap");
    assert_eq!(issues[0].severity, "error");
    assert!(datums.iter().all(|g| issues[0].subjects.contains(&g.id)));
}

#[test]
fn an_already_separated_seed_does_not_gain_datum_crossings() {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/pmi/step/hw_every_kind.step.gz");
    let (_, a) = model::analyse_native(&path).unwrap();
    let plan = model::plan_recovered(
        &a,
        &serde_json::from_value(serde_json::json!({"sheet":{"size":"A0"}})).unwrap(),
    )
    .unwrap();
    assert!(gdt::symbol_collisions(&plan.gdt).is_empty());
    let ids: Vec<_> = plan
        .gdt
        .iter()
        .filter(|g| g.kind == "datum_feature")
        .map(|g| &g.id)
        .collect();
    let crossings: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| {
            p.code == "annotation_leader_crossing" && p.subjects.iter().any(|s| ids.contains(&s))
        })
        .collect();
    assert!(
        crossings.is_empty(),
        "the legal seed has no datum-leader crossings: {crossings:#?}"
    );
}

fn dense_datums_are_separate(name: &str, expected: usize) -> Plan {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/pmi/step")
        .join(name);
    let (_, a) = model::analyse_native(&path).unwrap();
    let plan = model::plan_recovered(
        &a,
        &serde_json::from_value(serde_json::json!({"sheet":{"size":"A1"}})).unwrap(),
    )
    .unwrap();
    let shown: Vec<_> = plan
        .gdt
        .iter()
        .filter(|g| g.kind == "datum_feature" && g.placed.is_some())
        .collect();
    assert_eq!(
        shown.len(),
        expected,
        "separate the symbols without dropping datums"
    );
    let collisions = gdt::symbol_collisions(&plan.gdt);
    assert!(
        collisions.is_empty(),
        "coincident projections still need distinct symbols: {collisions:#?}"
    );
    plan
}

#[test]
fn ftc07_bent_datum_shafts_keep_clear_of_other_datum_letters() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/pmi/step/nist_ftc_07_asme1_ap242-e2.stp.gz");
    let (_, a) = model::analyse_native(&path).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    for letter in ["C", "D"] {
        let datums: Vec<_> = plan
            .gdt
            .iter()
            .filter(|g| g.kind == "datum_feature" && g.cells == [letter])
            .collect();
        assert_eq!(datums.len(), 1);
        assert_eq!(datums[0].state, draftwright::items::State::Shown);
        assert!(datums[0].placed.is_some(), "retain datum {letter}");
    }
    // The final bent C shaft used to pass within 0.5 mm of D's letter box,
    // even though the initial placement had kept that same text clearance.
    let touching: Vec<_> = plan
        .problems
        .iter()
        .filter(|p| {
            p.code == "label_crowded" && p.subjects.iter().any(|id| id.starts_with("pmi:datum."))
        })
        .collect();
    assert!(touching.is_empty(), "{touching:#?}");
}

#[test]
fn ftc10_coincident_projected_datums_do_not_touch() {
    // frame laid out as CAD's TOLERANCE (Paul 2026-11-12): the smaller boxes also let J fit.
    dense_datums_are_separate("nist_ftc_10_asme1_ap242-e2.stp.gz", 9);
}

#[test]
fn stc10_coincident_projected_datums_do_not_touch() {
    // accepted regression, pzfreo/draftwright#452 (Paul 2026-11-13); restore when fixed
    let plan = dense_datums_are_separate("nist_stc_10_asme1_ap242-e2.stp.gz", 8);
    assert_eq!(
        plan.gdt.iter().filter(|g| g.source == "datum:#117").count(),
        0
    );
    let frame = "pmi:geometric_tolerance.profile_surface:f65:ref=A,B,C";
    assert!(plan.gdt.iter().any(|g| g.id == frame && g.placed.is_some()));
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "leader_through_feature"
                && p.subjects.first().is_some_and(|id| id == frame)),
        "a datum bend must not consume the established frame's clear escape route"
    );
}

#[test]
fn ftc07_coincident_projected_datums_do_not_touch() {
    dense_datums_are_separate("nist_ftc_07_asme1_ap242-e2.stp.gz", 5);
}

#[test]
fn stc09_nearby_projected_datums_do_not_touch() {
    dense_datums_are_separate("nist_stc_09_asme1_ap242-e4.stp.gz", 5);
}

/// #146: both native CTC-01 variants retain A on the base top with a normal departure.
#[test]
fn ctc01_datum_a_is_square_to_the_base_top() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let corpus = std::env::var("DRAFTWRIGHT_CORPUS")
        .unwrap_or_else(|_| "/Users/paul/repos/quiddity/tests/corpus".into());
    let rd = Path::new(&corpus).join("nist/nist_ctc_01_asme1_rd.stp");
    for path in [
        rd,
        root.join("tests/fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz"),
    ] {
        if !path.is_file() {
            assert!(std::env::var("DRAFTWRIGHT_CORPUS_REQUIRED").is_err());
            continue;
        }
        let (part, a) = model::analyse_native(&path).unwrap();
        // The legacy RD file has presentation-only PMI. Author A on its proven
        // base-top face; the AP242 file supplies A semantically.
        let mut spec = Spec::default();
        if path.extension().is_some_and(|e| e == "stp") {
            spec.faces.insert(
                "face:3".into(),
                serde_json::from_value(serde_json::json!({"datum":"A"})).unwrap(),
            );
        }
        let p = model::plan(&a, &spec).unwrap();
        let g = p
            .gdt
            .iter()
            .find(|g| g.kind == "datum_feature" && g.label == "A")
            .unwrap();
        let placed = g.placed.as_ref().unwrap();
        let [apex, end] = placed.lines[0];
        assert!(
            (end[0] - apex[0]).abs() < 1e-8,
            "{}: {:?}",
            path.display(),
            placed.lines
        );
        assert!(end[1] > apex[1], "A leaves the top face upwards");
        let drawing = model::render(
            &part,
            &a,
            &p,
            &draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap(),
        )
        .unwrap();
        assert!(
            !draftwright::lint::structural::check(&drawing)
                .iter()
                .any(|i| i.annotation.as_deref() == Some(g.id.as_str())
                    && matches!(
                        i.code.as_str(),
                        "datum_leader_not_normal" | "leader_line_through_text"
                    ))
        );
    }
}

#[test]
fn an_authored_lateral_datum_frame_has_a_square_stub() {
    let (mut a, p) = spindle();
    only_datum(&mut a, "A");
    let g = place(&a, &p, &[]);
    let mut spec = isolated_spec(&a);
    spec.annotations.insert(
        g.id.clone(),
        serde_json::from_value(serde_json::json!({
            "place":{"along":0.8,"pinned":true}
        }))
        .unwrap(),
    );
    let (items, _) = gdt::plan(&a, &spec, &p.sheet, &p.views, &[]);
    let g = items.iter().find(|i| i.id == g.id).unwrap();
    let placed = g.placed.as_ref().unwrap();
    let [a, b] = placed.lines[0];
    let triangle = &placed.fills[0];
    let base = [
        triangle[2][0] - triangle[0][0],
        triangle[2][1] - triangle[0][1],
    ];
    assert!((base[0] * (b[0] - a[0]) + base[1] * (b[1] - a[1])).abs() < 1e-8);
    assert_eq!(placed.lines.len(), 2);
    assert_eq!(b, placed.lines[1][0]);
    assert!((b[0] - a[0]).hypot(b[1] - a[1]) > 1.);
}
