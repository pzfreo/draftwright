//! Incremental GD&T on the editor bracket and real quality drawings.
use draftwright::{
    items::Op,
    model::{self, Spec},
    session::Session,
};
use serde_json::{Value, json};
use std::{io::Read, path::Path};

fn open(name: &str) -> Session {
    open_spec(
        name,
        Spec {
            units: draftwright::units::Units::Mm,
            ..Default::default()
        },
    )
}
fn open_spec(name: &str, spec: Spec) -> Session {
    let bytes = source(name);
    let (part, analysis) = model::analyse_native_bytes(name, &bytes).unwrap();
    Session::new(part, analysis, spec).unwrap()
}
fn source(name: &str) -> Vec<u8> {
    let file = match name {
        "bracket" => "tests/fixtures/samples/draftwright-demo-bracket.step.gz",
        "scattered" => "tests/fixtures/parts/scattered_holes.step",
        "ctc" => "tests/fixtures/pmi/step/nist_ctc_01_asme1_ap242-e1.stp.gz",
        _ => panic!("unknown fixture"),
    };
    let mut bytes = std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join(file)).unwrap();
    if file.ends_with(".gz") {
        let mut decoded = Vec::new();
        flate2::read::GzDecoder::new(bytes.as_slice())
            .read_to_end(&mut decoded)
            .unwrap();
        bytes = decoded;
    }
    bytes
}
fn value(v: impl serde::Serialize) -> Value {
    serde_json::to_value(v).unwrap()
}
fn fixed(before: &model::Plan, after: &model::Plan) {
    assert_eq!(
        (&before.sheet.size, &before.sheet.scale, before.sheet.page),
        (&after.sheet.size, &after.sheet.scale, after.sheet.page)
    );
    assert_eq!(value(&before.views), value(&after.views));
    assert_eq!(value(&before.hole_tables), value(&after.hole_tables));
    assert_eq!(value(&before.hole_table), value(&after.hole_table));
    assert_eq!(value(&before.balloons), value(&after.balloons));
    assert_eq!(value(&before.tables), value(&after.tables));
    assert_eq!(value(&before.notes), value(&after.notes));
    for b in &before.views {
        let a = after.views.iter().find(|a| a.id == b.id).unwrap();
        assert_eq!(
            (b.at, b.scale, b.direction, b.up, b.sheet),
            (a.at, a.scale, a.direction, a.up, a.sheet),
            "{}",
            b.id
        );
    }
    for b in &before.dimensions {
        assert!(
            after.dimensions.iter().any(|a| a.id == b.id),
            "{} disappeared",
            b.id
        );
        if let Some(a) = after
            .dimensions
            .iter()
            .find(|a| a.id == b.id && a.terms == b.terms)
        {
            assert_eq!((&b.view, &b.placed), (&a.view, &a.placed), "{}", b.id);
            assert_eq!(
                value(model::render_item(model::AnnotationItem::Dimension(b))),
                value(model::render_item(model::AnnotationItem::Dimension(a))),
                "{} ink",
                b.id
            );
        }
    }
    for b in &before.gdt {
        if let Some(a) = after
            .gdt
            .iter()
            .find(|a| a.id == b.id && a.cells == b.cells)
        {
            assert_eq!(
                (&b.view, &b.side, &b.placed),
                (&a.view, &a.side, &a.placed),
                "{}",
                b.id
            );
            assert_eq!(
                value(model::render_item(model::AnnotationItem::Gdt(b))),
                value(model::render_item(model::AnnotationItem::Gdt(a))),
                "{} ink",
                b.id
            );
        }
    }
}
fn exercise(name: &str) {
    let mut s = open(name);
    let original = s.state().clone();
    let ops = [Op::set("faces/bottom/datum", json!("A"))];
    let edited = model::edit(s.spec(), &value(&ops)).unwrap();
    let library = model::plan_incremental(s.analysis(), s.plan(), &edited).unwrap();
    let preview = s.apply_incremental(&ops, false).unwrap();
    assert_eq!(value(s.state()), value(&original));
    let delta = s.apply_incremental(&ops, true).unwrap();
    assert_eq!(value(&preview), value(&delta));
    assert_eq!(value(&library), value(s.plan()));
    fixed(&original.plan, s.plan());
    assert!(delta.moved.is_empty(), "{}", value(&delta.moved));
    assert!(
        s.plan()
            .gdt
            .iter()
            .any(|g| g.cells == ["A"] && g.placed.is_some())
    );
    let mut replay = original.clone();
    delta.apply_to(&mut replay).unwrap();
    assert_eq!(value(&replay), value(s.state()));
    let added = s.state().clone();
    s.undo().unwrap();
    assert_eq!(value(s.state()), value(&original));
    s.redo().unwrap();
    assert_eq!(value(s.state()), value(&added));
    let delta = s
        .apply_incremental(&[Op::remove("faces/bottom")], true)
        .unwrap();
    fixed(&original.plan, s.plan());
    assert!(delta.moved.is_empty());
    assert_eq!(value(&s.state().annotations), value(&original.annotations));
    assert_eq!(s.state().problems, original.problems);
    let before = s.state().clone();
    let delta = s
        .apply_incremental(
            &[Op::set(
                "faces/top/controls",
                json!([
                    {"characteristic":"flatness","tolerance":0.05}
                ]),
            )],
            true,
        )
        .unwrap();
    fixed(&before.plan, s.plan());
    assert!(delta.moved.is_empty(), "{}", value(delta.moved));
    assert!(
        s.plan().gdt.iter().any(|g| g.kind == "control_frame"
            && g.source.starts_with("spec:")
            && g.placed.is_some())
    );
    s.apply_incremental(
        &[
            Op::set("faces/bottom/datum", json!("A")),
            Op::set("faces/left/datum", json!("B")),
            Op::set("faces/front/datum", json!("C")),
        ],
        true,
    )
    .unwrap();
    let targets: Vec<_> = s
        .analysis()
        .recognition
        .as_ref()
        .unwrap()
        .patterns
        .first()
        .map(|p| vec![p.id.clone()])
        .unwrap_or_else(|| {
            s.analysis()
                .features
                .iter()
                .filter(|f| f.kind == "hole")
                .map(|f| f.id.clone())
                .collect()
        });
    assert!(!targets.is_empty());
    let ops: Vec<_> = targets
        .iter()
        .map(|id| {
            Op::set(
                format!("features/{}/controls", draftwright::items::key(id)),
                json!([{"characteristic":"position","tolerance":0.1,"datums":["A","B","C"]}]),
            )
        })
        .collect();
    let before = s.state().clone();
    let delta = s.apply_incremental(&ops, true).unwrap();
    fixed(&before.plan, s.plan());
    assert!(delta.moved.is_empty(), "{}", value(&delta.moved));
    for id in &targets {
        assert!(s.plan().gdt.iter().any(|g| g.source == format!("spec:features/{id}/controls/0") && g.placed.is_some()));
    }
}
#[test]
fn bracket() {
    exercise("bracket");
}
#[test]
fn scattered() {
    exercise("scattered");
}
#[test]
fn ctc() {
    exercise("ctc");
}

#[test]
fn explicit_full_apply_is_required_for_layout_edits() {
    let mut s = open("bracket");
    let before = value(s.state());
    let stats = s.stats();
    let ops = [Op::set("sheet/scale", json!("1:5"))];
    let err = s.apply_incremental(&ops, true).unwrap_err();
    assert!(err.message.contains("full apply"));
    assert_eq!(value(s.state()), before);
    assert_eq!(s.stats(), stats);
    let delta = s.apply(&ops, true).unwrap();
    assert!(
        delta.moved.is_empty(),
        "ordinary full apply keeps main's Delta contract"
    );
}

#[test]
fn a_crowded_fixed_sheet_reports_the_new_frames_conflicts() {
    let spec: Spec = serde_json::from_value(json!({
        "sheet":{"size":"A4","scale":"1:1"}, "hole_table":"never"
    }))
    .unwrap();
    let mut s = open_spec("scattered", spec);
    let before = s.state().clone();
    let delta = s
        .apply_incremental(
            &[Op::set(
                "faces/top/controls",
                json!([
                    {"characteristic":"flatness","tolerance":0.05}
                ]),
            )],
            true,
        )
        .unwrap();
    fixed(&before.plan, s.plan());
    assert!(delta.moved.is_empty(), "{}", value(&delta.moved));
    assert!(
        s.plan()
            .gdt
            .iter()
            .any(|g| g.source == "spec:faces/top/controls/0")
    );
    for old in before.plan.problems.iter().filter(|p| {
        matches!(
            p.code.as_str(),
            "location_ref_dropped" | "extension_through_feature"
        )
    }) {
        assert!(
            s.plan().problems.iter().any(|p| p.id == old.id),
            "unchanged defect {} was silently resolved",
            old.id
        );
    }
    let finding = s
        .plan()
        .problems
        .iter()
        .find(|p| p.code == "incremental_placement_constrained")
        .expect("the frame cannot fit cleanly on the crowded fixed A4 sheet");
    assert_eq!(value(finding)["tier"], "tidy");
    assert!(!finding.subjects.is_empty());
}

#[test]
fn removing_a_datum_and_its_dependent_frame_restores_the_drawing() {
    let mut s = open("bracket");
    let original = s.state().clone();
    let hole = s
        .analysis()
        .features
        .iter()
        .find(|f| f.kind == "hole")
        .unwrap()
        .id
        .clone();
    let path = format!("features/{}/controls", draftwright::items::key(&hole));
    s.apply_incremental(
        &[
            Op::set("faces/bottom/datum", json!("A")),
            Op::set(
                &path,
                json!([{"characteristic":"position","tolerance":0.1,"datums":["A"]}]),
            ),
        ],
        true,
    )
    .unwrap();
    let delta = s
        .apply_incremental(&[Op::remove("faces/bottom"), Op::remove(&path)], true)
        .unwrap();
    assert!(delta.moved.is_empty(), "{}", value(delta.moved));
    fixed(&original.plan, s.plan());
    assert_eq!(value(&original.annotations), value(&s.state().annotations));
    assert_eq!(
        value(&original.plan.dimensions),
        value(&s.plan().dimensions)
    );
    assert_eq!(value(&original.plan.gdt), value(&s.plan().gdt));
    assert_eq!(s.state().problems, original.problems);
}

#[test]
fn changing_dimension_content_releases_only_its_ink_and_reports_it() {
    let mut s = open("bracket");
    let original = s.state().clone();
    let id = s
        .plan()
        .dimensions
        .iter()
        .find(|d| d.kind == "linear" && d.placed.is_some())
        .unwrap()
        .id
        .clone();
    let path = format!("dimensions/{}/tolerance", draftwright::items::key(&id));
    let delta = s
        .apply_incremental(&[Op::set(&path, json!("±0.001"))], true)
        .unwrap();
    fixed(&original.plan, s.plan());
    assert!(
        delta
            .moved
            .iter()
            .any(|m| m.id == id && !m.reason.is_empty())
    );
    assert!(delta.moved.iter().all(|m| m.id == id));
    assert_eq!(s.spec().dimensions.len(), 1);
    assert!(
        s.spec().dimensions[&id].place.is_none(),
        "derived pins are not authored intent"
    );
    s.apply_incremental(&[Op::remove(&path)], true).unwrap();
    fixed(&original.plan, s.plan());
    assert_eq!(value(&original.annotations), value(&s.state().annotations));
}

#[test]
fn removing_a_tolerance_restores_a_shared_counted_statement() {
    let mut s = open("bracket");
    let original = s.state().clone();
    let child = "d:f:plate:f4/thickness.length";
    assert!(original.plan.omitted.iter().any(|o| o.id == child));
    let path = format!("dimensions/{}/tolerance", draftwright::items::key(child));
    s.apply_incremental(&[Op::set(&path, json!("±0.01"))], true)
        .unwrap();
    assert!(
        s.plan()
            .dimensions
            .iter()
            .any(|d| d.id == child && d.placed.is_some())
    );
    let delta = s.apply_incremental(&[Op::remove(&path)], true).unwrap();
    fixed(&original.plan, s.plan());
    assert_eq!(value(&original.annotations), value(&s.state().annotations));
    assert_eq!(
        value(&original.plan.dimensions),
        value(&s.plan().dimensions)
    );
    let mut replay = original.clone();
    // This delta starts from the grown state; check the complete add/remove stream too.
    let mut other = open("bracket");
    other
        .apply_incremental(&[Op::set(&path, json!("±0.01"))], true)
        .unwrap()
        .apply_to(&mut replay)
        .unwrap();
    delta.apply_to(&mut replay).unwrap();
    assert_eq!(value(&replay), value(s.state()));
}

#[test]
fn removing_an_override_restores_an_imported_frames_content_and_exact_ink() {
    let mut s = open("ctc");
    let original = s.state().clone();
    let flatness = original
        .plan
        .gdt
        .iter()
        .find(|g| g.id.starts_with("pmi:geometric_tolerance.flatness:") && g.faces.len() == 1)
        .unwrap();
    let path = format!("faces/face:{}/controls", flatness.faces[0]);
    let delta = s
        .apply_incremental(
            &[Op::set(
                &path,
                json!([
                    {"characteristic":"flatness","tolerance":0.01}
                ]),
            )],
            true,
        )
        .unwrap();
    assert!(delta.moved.iter().any(|m| m.id == flatness.id));
    assert_eq!(
        s.plan()
            .gdt
            .iter()
            .find(|g| g.id == flatness.id)
            .unwrap()
            .cells[1],
        "0.01"
    );
    s.apply_incremental(&[Op::remove(&path)], true).unwrap();
    fixed(&original.plan, s.plan());
    assert_eq!(value(&original.annotations), value(&s.state().annotations));
    assert_eq!(original.plan.gdt.len(), s.plan().gdt.len());
    for old in &original.plan.gdt {
        assert_eq!(old, s.plan().gdt.iter().find(|g| g.id == old.id).unwrap());
    }
}

use draftwright::haecceity::{
    p21::Document,
    pmi::{self as hp, *},
    step::read_part_definitions,
};
fn pmi_edit(bytes: &[u8], edit: impl FnOnce(&mut PartPmi)) -> Vec<u8> {
    let doc = Document::parse(bytes.to_vec()).unwrap();
    let parts = read_part_definitions(bytes).unwrap();
    let mut pmi = hp::read(&doc, &parts).unwrap().parts.remove(0);
    edit(&mut pmi);
    let (edits, _) = hp::write(
        &doc,
        &parts,
        &[(PartId(0), pmi)],
        hp::write::Mode::Replace,
        hp::write::PresentationPolicy::RemovePresentation,
    )
    .unwrap();
    doc.apply(&edits).unwrap().bytes
}
fn face_target(pmi: &mut PartPmi, face: usize) -> FeatureId {
    pmi.features
        .push(Feature::Items(vec![Anchor::Face(FaceIndex(face))]));
    FeatureId(pmi.features.len() - 1)
}
fn datum_step(bytes: &[u8], face: usize) -> Vec<u8> {
    pmi_edit(bytes, |p| {
        let feature = face_target(p, face);
        if let Some(d) = p.datums.iter_mut().find(|d| d.label().as_str() == "A") {
            *d = Datum::new(DatumLabel::new("A").unwrap(), Some(feature), vec![]).unwrap();
        } else {
            p.datums
                .push(Datum::new(DatumLabel::new("A").unwrap(), Some(feature), vec![]).unwrap());
        }
    })
}
fn planar_face(s: &Session) -> usize {
    // Pick a principal plane at the maximum station, so a re-base changes a real value.
    s.part()
        .faces
        .iter()
        .enumerate()
        .find_map(|(i, f)| {
            if let draftwright::haecceity::geom::Surface::Plane { frame } = &f.surface {
                let z = s.analysis().envelope[1][2];
                (frame.z[2].abs() > 0.99 && (frame.origin[2] - z).abs() < 1e-5).then_some(i)
            } else {
                None
            }
        })
        .unwrap()
}
fn reopened(name: &str, bytes: &[u8], s: &Session) -> Session {
    let spec: Spec = serde_json::from_slice(&serde_json::to_vec(s.spec()).unwrap()).unwrap();
    let (part, a) = model::analyse_native_bytes(name, bytes).unwrap();
    Session::new(part, a, spec).unwrap()
}
fn pmi_round_trip(name: &str) {
    let bytes = source(name);
    let mut s = open(name);
    let original = s.state().clone();
    let updated = datum_step(&bytes, planar_face(&s));
    let preview = s.replace_step_incremental(name, &updated, false).unwrap();
    assert_eq!(value(s.state()), value(&original));
    assert_eq!(
        s.analysis().binding.source_sha256,
        original.plan.binding.source_sha256
    );
    let committed = s.replace_step_incremental(name, &updated, true).unwrap();
    assert_eq!(value(&preview), value(&committed));
    fixed(&original.plan, s.plan());
    assert!(committed.moved.is_empty(), "{}", value(&committed.moved));
    assert!(
        s.plan()
            .gdt
            .iter()
            .any(|g| g.cells == ["A"] && g.placed.is_some())
    );
    assert_eq!(value(&s.spec().engine_pins)["written_by"], "engine");
    let restored = reopened(name, &updated, &s);
    fixed(s.plan(), restored.plan());
    assert_eq!(
        value(&s.state().annotations),
        value(&restored.state().annotations)
    );
    assert_eq!(s.state().problems, restored.state().problems);
    let after = s.state().clone();
    s.undo().unwrap();
    assert_eq!(value(s.state()), value(&original));
    s.redo().unwrap();
    assert_eq!(value(s.state()), value(&after));
    let removed = s.replace_step_incremental(name, &bytes, true).unwrap();
    assert!(removed.moved.is_empty(), "{}", value(&removed.moved));
    fixed(&original.plan, s.plan());
    assert_eq!(value(&original.annotations), value(&s.state().annotations));
    assert_eq!(original.problems, s.state().problems);
}
#[test]
fn step_pmi_bracket_reload_and_remove() {
    pmi_round_trip("bracket");
}
#[test]
fn step_pmi_scattered_reload_and_remove() {
    pmi_round_trip("scattered");
}
#[test]
fn step_pmi_ctc_reload_and_remove() {
    pmi_round_trip("ctc");
}
#[test]
fn changed_brep_is_refused_without_mutating_source_or_history() {
    let mut s = open("bracket");
    let original = value(s.state());
    let error = s
        .replace_step_incremental("scattered", &source("scattered"), true)
        .unwrap_err();
    assert!(error.message.contains("geometry differs") && error.message.contains("full plan"));
    assert_eq!(value(s.state()), original);
    assert!(s.undo().is_err());
}
#[test]
fn changed_brep_is_refused_even_when_an_in_process_caller_reuses_the_binding() {
    let s = open("bracket");
    let original = value(s.state());
    let mut faces = s.part().faces.clone();
    let frame = faces
        .iter_mut()
        .find_map(|f| match &mut f.surface {
            draftwright::haecceity::geom::Surface::Plane { frame } => Some(frame),
            _ => None,
        })
        .unwrap();
    frame.origin[0] += 1e-8;
    let changed =
        draftwright::haecceity::Part::new(faces, s.part().edges.clone(), s.part().solids.clone());
    let error =
        model::plan_incremental_source(s.part(), s.plan(), &changed, s.analysis(), s.spec())
            .unwrap_err();
    assert!(error.message.contains("geometry differs") && error.message.contains("full plan"));
    assert_eq!(value(s.state()), original);
}
#[test]
fn relayout_clears_only_engine_pins() {
    let mut s = open("bracket");
    let id = s
        .plan()
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .id
        .clone();
    s.apply(
        &[Op::set(
            format!("dimensions/{}/place", draftwright::items::key(&id)),
            json!({"pinned":true}),
        )],
        true,
    )
    .unwrap();
    let authored = s.spec().clone();
    let updated = datum_step(&source("bracket"), planar_face(&s));
    s.replace_step_incremental("bracket", &updated, true)
        .unwrap();
    assert!(s.spec().engine_pins.is_some());
    s.relayout(true).unwrap();
    assert_eq!(s.spec(), &authored);
    let full = model::plan_recovered(s.analysis(), &authored).unwrap();
    assert_eq!(value(s.plan()), value(full));
}
#[test]
fn datum_rebase_offer_previews_and_applies_only_when_chosen() {
    let mut s = open("ctc");
    let updated = datum_step(&source("ctc"), planar_face(&s));
    s.replace_step_incremental("ctc", &updated, true).unwrap();
    let held = s.state().clone();
    let problem = s
        .state()
        .problems
        .iter()
        .find(|p| p.code == "incremental_basis_kept")
        .expect("kept basis must be explained");
    assert_eq!(
        problem.labels().tier,
        draftwright::finding::Tier::Information
    );
    let fix = problem
        .offered_fixes()
        .into_iter()
        .find(|f| f.description.contains("Dimension from datum A"))
        .unwrap();
    let id = problem.subjects[0].clone();
    let preview = s.apply(&fix.ops, false).unwrap();
    assert_eq!(value(s.state()), value(&held));
    let applied = s.apply(&fix.ops, true).unwrap();
    assert_eq!(value(&preview), value(&applied));
    let old = held.plan.dimensions.iter().find(|d| d.id == id).unwrap();
    let new = s.plan().dimensions.iter().find(|d| d.id == id).unwrap();
    assert_ne!(
        old.measurements, new.measurements,
        "re-base changes measured content"
    );
    assert_ne!(old.terms, new.terms);
    assert!(applied.moved.iter().any(|m| m.id == id));
    assert!(new.basis.contains("datum A"));
    let rebased = new.measurements.clone();
    s.apply_incremental(&[], true).unwrap();
    assert_eq!(
        s.plan()
            .dimensions
            .iter()
            .find(|d| d.id == id)
            .unwrap()
            .measurements,
        rebased
    );
}
#[test]
fn consecutive_crowded_adds_have_unique_findings() {
    let mut s = open_spec(
        "scattered",
        serde_json::from_value(json!({"sheet":{"size":"A4","scale":"1:1"},"hole_table":"never"}))
            .unwrap(),
    );
    for side in ["top", "bottom"] {
        s.apply_incremental(
            &[Op::set(
                format!("faces/{side}/controls"),
                json!([{"characteristic":"flatness","tolerance":0.05}]),
            )],
            true,
        )
        .unwrap();
    }
    let p = &s.state().problems;
    assert!(
        p.iter()
            .filter(|p| p.code == "incremental_placement_constrained")
            .count()
            >= 2
    );
    let ids: std::collections::BTreeSet<_> = p.iter().map(|p| &p.id).collect();
    assert_eq!(p.len(), ids.len());
}
#[test]
fn history_retains_at_most_thirty_two_edits() {
    let mut s = open("bracket");
    for _ in 0..100 {
        s.apply_incremental(&[], true).unwrap();
    }
    let mut n = 0;
    while s.undo().is_ok() {
        n += 1;
    }
    assert_eq!(n, 32);
}

fn pmi_tolerance(
    target: FeatureId,
    kind: ToleranceKind,
    datums: Option<DatumSystem>,
) -> GeometricTolerance {
    GeometricTolerance {
        kind,
        target: ToleranceTarget::Feature(target),
        magnitude: Some(hp::Value::length(
            Decimal::parse("0.05").unwrap(),
            LengthUnit::Millimetre,
        )),
        zone: None,
        modifiers: vec![],
        unit_basis: None,
        maximum: None,
        unequal: None,
        datums,
        auxiliary: vec![],
        description: None,
    }
}
#[test]
fn step_pmi_flatness_and_position_preserve_existing_ink() {
    for name in ["bracket", "scattered", "ctc"] {
        let mut s = open(name);
        let before = s.state().clone();
        let flat = pmi_edit(&source(name), |p| {
            let target = face_target(p, planar_face(&s));
            p.tolerances
                .push(pmi_tolerance(target, ToleranceKind::Flatness, None));
        });
        let delta = s.replace_step_incremental(name, &flat, true).unwrap();
        fixed(&before.plan, s.plan());
        assert!(delta.moved.is_empty());
        assert!(
            s.plan()
                .gdt
                .iter()
                .any(|g| g.cells.iter().any(|c| c == "⏥") && g.placed.is_some())
        );
        let flat_reload = reopened(name, &flat, &s);
        fixed(s.plan(), flat_reload.plan());
        assert_eq!(
            value(&s.state().annotations),
            value(&flat_reload.state().annotations)
        );
        assert_eq!(s.state().problems, flat_reload.state().problems);
        let positioned = pmi_edit(&flat, |p| {
            let mut ids = Vec::new();
            for (letter, axis) in [("A", 2), ("B", 0), ("C", 1)] {
                let face = s
                    .part()
                    .faces
                    .iter()
                    .enumerate()
                    .find_map(|(i, f)| {
                        if let draftwright::haecceity::geom::Surface::Plane { frame } = &f.surface {
                            (frame.z[axis].abs() > 0.99
                                && (frame.origin[axis] - s.analysis().envelope[0][axis]).abs()
                                    < 1e-5)
                                .then_some(i)
                        } else {
                            None
                        }
                    })
                    .unwrap();
                let feature = face_target(p, face);
                let d =
                    Datum::new(DatumLabel::new(letter).unwrap(), Some(feature), vec![]).unwrap();
                if let Some(index) = p.datums.iter().position(|d| d.label().as_str() == letter) {
                    p.datums[index] = d;
                    ids.push(index);
                } else {
                    ids.push(p.datums.len());
                    p.datums.push(d);
                }
            }
            let faces = s
                .analysis()
                .features
                .iter()
                .filter(|f| f.kind == "hole")
                .flat_map(|f| f.faces.clone())
                .collect::<Vec<_>>();
            assert!(!faces.is_empty());
            p.features.push(Feature::Items(
                faces
                    .into_iter()
                    .map(|f| Anchor::Face(FaceIndex(f)))
                    .collect(),
            ));
            let target = FeatureId(p.features.len() - 1);
            let datums = DatumSystem::new(
                ids.into_iter()
                    .map(|id| Compartment {
                        references: vec![DatumReference {
                            datum: DatumId(id),
                            modifiers: vec![],
                        }],
                        modifiers: vec![],
                    })
                    .collect(),
            )
            .unwrap();
            p.tolerances
                .push(pmi_tolerance(target, ToleranceKind::Position, Some(datums)));
        });
        let before = s.state().clone();
        let delta = s.replace_step_incremental(name, &positioned, true).unwrap();
        fixed(&before.plan, s.plan());
        assert!(delta.moved.is_empty(), "{name}: {}", value(&delta.moved));
        assert!(s.plan().gdt.iter().any(|g| g.cells.iter().any(|c| c == "⌖")
            && g.cells.iter().any(|c| c == "A")
            && g.placed.is_some()));
        let reload = reopened(name, &positioned, &s);
        assert_eq!(
            value(&reload.state().annotations),
            value(&s.state().annotations)
        );
    }
}

fn without_a(bytes: &[u8]) -> Vec<u8> {
    pmi_edit(bytes, |p| {
        let Some(a) = p.datums.iter().position(|d| d.label().as_str() == "A") else {
            return;
        };
        p.datums.remove(a);
        p.tolerances.retain(|t| {
            !t.datums.as_ref().is_some_and(|s| {
                s.compartments()
                    .iter()
                    .flat_map(|c| &c.references)
                    .any(|r| r.datum == DatumId(a))
            })
        });
        for t in &mut p.tolerances {
            if let Some(system) = &t.datums {
                let mut compartments = system.compartments().to_vec();
                for r in compartments.iter_mut().flat_map(|c| &mut c.references) {
                    if r.datum.0 > a {
                        r.datum.0 -= 1;
                    }
                }
                t.datums = Some(DatumSystem::new(compartments).unwrap());
            }
        }
    })
}
#[test]
fn ctc_step_adds_a_to_the_without_a_pmi_variant() {
    let bytes = without_a(&source("ctc"));
    let (part, a) = model::analyse_native_bytes("ctc", &bytes).unwrap();
    let mut s = Session::new(part, a, Spec::default()).unwrap();
    let before = s.state().clone();
    assert!(!s.plan().gdt.iter().any(|g| g.cells == ["A"]));
    let updated = datum_step(&bytes, planar_face(&s));
    let added = s.replace_step_incremental("ctc", &updated, true).unwrap();
    assert!(added.moved.is_empty());
    fixed(&before.plan, s.plan());
    assert!(
        s.plan()
            .gdt
            .iter()
            .any(|g| g.cells == ["A"] && g.placed.is_some())
    );
    let reloaded = reopened("ctc", &updated, &s);
    assert_eq!(
        value(&s.state().annotations),
        value(&reloaded.state().annotations)
    );
    s.replace_step_incremental("ctc", &bytes, true).unwrap();
    assert_eq!(value(&before.annotations), value(&s.state().annotations));
    assert_eq!(before.problems, s.state().problems);
}

#[test]
fn unchanged_ink_does_not_restore_findings_from_another_step_revision() {
    let bytes = source("bracket");
    let mut s = open("bracket");
    let original = s.state().clone();
    let added = datum_step(&bytes, planar_face(&s));
    s.replace_step_incremental("bracket", &added, true).unwrap();
    s.replace_step_incremental("bracket", &bytes, true).unwrap();
    let unsupported = pmi_edit(&bytes, |p| {
        p.attributes.push(AttributeSet {
            name: "foreign inspection requirement".into(),
            on: None,
            description: None,
            general_property: None,
            items: vec![(
                "inspection".into(),
                AttributeValue::Text("inspect independently".into()),
            )],
        })
    });
    let delta = s
        .replace_step_incremental("bracket", &unsupported, true)
        .unwrap();
    assert!(delta.moved.is_empty());
    assert_eq!(value(&original.annotations), value(&s.state().annotations));
    assert!(
        s.state()
            .problems
            .iter()
            .any(|p| p.code == "pmi_not_extracted")
    );
    assert_ne!(original.problems, s.state().problems);
    let reload = reopened("bracket", &unsupported, &s);
    assert_eq!(reload.state().problems, s.state().problems);
    s.replace_step_incremental("bracket", &bytes, true).unwrap();
    assert_eq!(original.problems, s.state().problems);
}

#[test]
fn consecutive_crowded_step_adds_retain_unique_findings() {
    let bytes = source("scattered");
    let mut s = open_spec(
        "scattered",
        serde_json::from_value(json!({
            "sheet":{"size":"A4","scale":"1:1"},"hole_table":"never"
        }))
        .unwrap(),
    );
    let bottom = s
        .part()
        .faces
        .iter()
        .enumerate()
        .find_map(|(i, f)| {
            if let draftwright::haecceity::geom::Surface::Plane { frame } = &f.surface {
                (frame.z[2].abs() > 0.99
                    && (frame.origin[2] - s.analysis().envelope[0][2]).abs() < 1e-5)
                    .then_some(i)
            } else {
                None
            }
        })
        .unwrap();
    let first = pmi_edit(&bytes, |p| {
        let target = face_target(p, planar_face(&s));
        p.tolerances
            .push(pmi_tolerance(target, ToleranceKind::Flatness, None));
    });
    let before = s.state().clone();
    let delta = s
        .replace_step_incremental("scattered", &first, true)
        .unwrap();
    assert!(delta.moved.is_empty());
    fixed(&before.plan, s.plan());
    let second = pmi_edit(&first, |p| {
        let target = face_target(p, bottom);
        p.tolerances
            .push(pmi_tolerance(target, ToleranceKind::Flatness, None));
    });
    let before = s.state().clone();
    let delta = s
        .replace_step_incremental("scattered", &second, true)
        .unwrap();
    assert!(delta.moved.is_empty());
    fixed(&before.plan, s.plan());
    let problems = &s.state().problems;
    assert_eq!(
        problems
            .iter()
            .filter(|p| p.code == "incremental_placement_constrained")
            .count(),
        2
    );
    let ids: std::collections::BTreeSet<_> = problems.iter().map(|p| &p.id).collect();
    assert_eq!(ids.len(), problems.len());
}

fn pinned_bracket() -> (Session, Vec<u8>) {
    let mut session = open("bracket");
    let bytes = datum_step(&source("bracket"), planar_face(&session));
    session
        .replace_step_incremental("bracket", &bytes, true)
        .unwrap();
    (session, bytes)
}

#[test]
fn full_apply_accepts_content_and_layout_edits_on_engine_pins() {
    let (mut session, bytes) = pinned_bracket();
    for op in [
        Op::set("sheet/title/drawn_by", json!("QA")),
        Op::set("sheet/title/material", json!("ALUMINIUM")),
        Op::set("notes", json!([{"id":"n:1","text":"REMOVE BURRS"}])),
    ] {
        let before = session.state().clone();
        let preview = session.apply(std::slice::from_ref(&op), false).unwrap();
        assert_eq!(value(session.state()), value(&before));
        let delta = session.apply(&[op], true).unwrap();
        assert_eq!(value(preview), value(&delta));
        assert_eq!(value(&before.plan.views), value(&session.plan().views));
        for dim in &before.plan.dimensions {
            let next = session
                .plan()
                .dimensions
                .iter()
                .find(|d| d.id == dim.id)
                .unwrap();
            assert_eq!(dim.placed, next.placed, "{}", dim.id);
        }
    }
    assert!(!session.plan().notes.is_empty());
    let before = session.state().clone();
    let mut replacement = session.spec().clone();
    replacement
        .sheet
        .title
        .insert("drawn_by".into(), "SET_SPEC".into());
    session.set_spec(&replacement).unwrap();
    assert!(session.spec().engine_pins.is_some());
    assert_eq!(value(&before.plan.views), value(&session.plan().views));

    let cold = reopened("bracket", &bytes, &session);
    assert_eq!(
        value(&session.state().annotations),
        value(&cold.state().annotations)
    );
    assert_eq!(value(&session.plan().views), value(&cold.plan().views));

    for op in [
        Op::set("sheet/scale", json!("1:1")),
        Op::set("sheet/size", json!("A3")),
        Op::set("hole_table", json!("never")),
        Op::set("views/side/omit", json!(true)),
    ] {
        let mut candidate = reopened("bracket", &bytes, &session);
        let delta = candidate.apply(&[op], true).unwrap();
        assert!(candidate.spec().engine_pins.is_none());
        let full = model::plan_recovered(candidate.analysis(), candidate.spec()).unwrap();
        assert_eq!(value(candidate.plan()), value(full));
        if value(&candidate.plan().views) != value(&session.plan().views) {
            assert!(!delta.moved.is_empty());
        }
    }
}

#[test]
fn stale_engine_pins_open_and_are_dropped_without_stale_ink() {
    let (session, _) = pinned_bracket();
    let mut spec = session.spec().clone();
    let pins = spec.engine_pins.as_mut().unwrap();
    let sample = pins.annotations.values().next().unwrap().clone();
    pins.annotations.insert("d:missing/item".into(), sample);
    let (part, analysis) = model::analyse_native_bytes(
        "bracket",
        &datum_step(&source("bracket"), planar_face(&session)),
    )
    .unwrap();
    let reopened = Session::new(part, analysis, spec).unwrap();
    assert!(
        reopened
            .state()
            .problems
            .iter()
            .any(|p| p.code == "engine_pins_partly_retained"
                && p.refs.contains(&"d:missing/item".into()))
    );
    assert!(
        !reopened
            .spec()
            .engine_pins
            .as_ref()
            .unwrap()
            .annotations
            .contains_key("d:missing/item")
    );
    assert!(!reopened.state().annotations.contains_key("d:missing/item"));
    let (part, analysis) = model::analyse_native_bytes("scattered", &source("scattered")).unwrap();
    let changed = Session::new(part, analysis, session.spec().clone()).unwrap();
    assert!(changed.spec().engine_pins.is_none());
    assert!(
        changed
            .state()
            .problems
            .iter()
            .any(|p| p.code == "engine_pins_replanned")
    );
    assert!(
        changed
            .state()
            .problems
            .iter()
            .any(|p| p.code == "engine_pins_replanned"
                && p.severity == "warning"
                && p.message.contains("geometry"))
    );
    assert!(changed.plan().gdt.iter().all(|g| g.cells != ["A"]));
}

#[test]
fn engine_pins_have_versioned_placement_only_members() {
    let (session, bytes) = pinned_bracket();
    let v = value(session.spec());
    assert_eq!(v["schema"], "draftwright.spec/4");
    fn check(v: &Value) {
        match v {
            Value::Object(fields) => {
                for (key, child) in fields {
                    assert!(
                        ![
                            "layout",
                            "intent",
                            "problems",
                            "measurements",
                            "terms",
                            "faces",
                            "characteristics",
                            "retired_findings",
                            "retired_gdt"
                        ]
                        .contains(&key.as_str()),
                        "{key}"
                    );
                    check(child);
                }
            }
            Value::Array(values) => values.iter().for_each(check),
            _ => (),
        }
    }
    check(&v["engine_pins"]);
    let (part, analysis) = model::analyse_native_bytes("bracket", &bytes).unwrap();
    let roundtrip: Spec = serde_json::from_value(v.clone()).unwrap();
    assert_eq!(
        value(
            reopened("bracket", &bytes, &session)
                .state()
                .annotations
                .clone()
        ),
        value(
            Session::new(part, analysis, roundtrip)
                .unwrap()
                .state()
                .annotations
                .clone()
        )
    );
    let mut unknown = v;
    unknown["schema"] = json!("draftwright.spec/999");
    unknown["new_future_field"] = json!({});
    let error = serde_json::from_value::<Spec>(unknown)
        .unwrap_err()
        .to_string();
    assert!(error.contains("Spec version"), "{error}");
}

#[test]
fn cli_and_mcp_open_pins_after_full_edits_and_source_changes() {
    use std::io::Write;
    use std::process::{Command, Stdio};
    let (session, bytes) = pinned_bracket();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("incremental-open");
    std::fs::create_dir_all(&dir).unwrap();
    let step = dir.join("bracket.step");
    let changed = dir.join("changed.step");
    std::fs::write(&step, &bytes).unwrap();
    std::fs::write(&changed, source("scattered")).unwrap();
    let revision_dir = dir.join("revision");
    std::fs::create_dir_all(&revision_dir).unwrap();
    let revision_path = revision_dir.join("bracket.step");
    let revision = pmi_edit(&bytes, |p| {
        let target = face_target(p, planar_face(&session));
        p.tolerances
            .push(pmi_tolerance(target, ToleranceKind::Flatness, None));
    });
    std::fs::write(&revision_path, revision).unwrap();
    let mut cases = Vec::new();
    for (name, path, val) in [
        ("title", "sheet/title/drawn_by", json!("QA")),
        ("scale", "sheet/scale", json!("1:1")),
        ("table", "hole_table", json!("never")),
        ("view", "views/side/omit", json!(true)),
    ] {
        let edited = model::edit(
            session.spec(),
            &json!([{"op":"set","path":path,"value":val}]),
        )
        .unwrap();
        cases.push((name, step.clone(), edited, false));
    }
    let changed_place_id = session
        .plan()
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .id
        .clone();
    let mut raw_place = session.spec().clone();
    raw_place
        .dimensions
        .entry(changed_place_id.clone())
        .or_default()
        .place =
        Some(serde_json::from_value(json!({"offset":[1000.,1000.],"pinned":true})).unwrap());
    cases.push(("raw_place", step.clone(), raw_place, false));
    // CLI files are edited directly, without model::edit to release engine pins.
    let mut raw_layout = session.spec().clone();
    raw_layout.sheet.scale = Some("1:1".into());
    cases.push(("raw_layout", step.clone(), raw_layout, true));
    let mut missing = session.spec().clone();
    let pins = missing.engine_pins.as_mut().unwrap();
    let pin = pins.annotations.values().next().unwrap().clone();
    pins.annotations.insert("d:missing/item".into(), pin);
    cases.push(("missing", step.clone(), missing, true));
    cases.push(("changed", changed, session.spec().clone(), true));
    cases.push(("revision", revision_path, session.spec().clone(), false));
    let mut messages = vec![
        json!({"jsonrpc":"2.0","id":0,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"incremental-test","version":"1"}}}),
        json!({"jsonrpc":"2.0","method":"notifications/initialized"}),
    ];
    for (index, (name, step, spec, stale)) in cases.iter().enumerate() {
        let file = dir.join(format!("{name}.spec.json"));
        std::fs::write(&file, serde_json::to_vec(spec).unwrap()).unwrap();
        let output = Command::new(env!("CARGO_BIN_EXE_draftwright"))
            .args([
                "plan",
                step.to_str().unwrap(),
                "--spec",
                file.to_str().unwrap(),
            ])
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{name}: {} {}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let plan: Value = serde_json::from_slice(&output.stdout).unwrap();
        if *name == "raw_place" {
            let actual: model::Plan = serde_json::from_value(plan.clone()).unwrap();
            let mut peers = session.plan().clone();
            peers.dimensions.retain(|d| d.id != changed_place_id);
            fixed(&peers, &actual);
            assert!(
                !actual
                    .problems
                    .iter()
                    .any(|p| p.code == "engine_pins_replanned")
            );
        }
        if *name == "revision" {
            assert_eq!(plan["views"], value(&session.plan().views));
            assert!(!plan["spec"]["engine_pins"].is_null());
        }

        assert_eq!(
            plan["problems"]
                .as_array()
                .unwrap()
                .iter()
                .any(|p| p["code"]
                    == if matches!(*name, "missing" | "raw_place") {
                        "engine_pins_partly_retained"
                    } else {
                        "engine_pins_replanned"
                    }),
            *stale,
            "{name}"
        );
        messages.push(json!({"jsonrpc":"2.0","id":index+1,"method":"tools/call","params":{"name":"plan","arguments":{"step":step,"spec":spec,"analysis":model::analyse_native(step).unwrap().1}}}));
    }
    let mut child = Command::new(env!("CARGO_BIN_EXE_draftwright-mcp"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let stdout = child.stdout.take().unwrap();
    let reader = std::thread::spawn(move || {
        let mut bytes = Vec::new();
        std::io::Read::read_to_end(&mut std::io::BufReader::new(stdout), &mut bytes).unwrap();
        bytes
    });
    let mut stdin = child.stdin.take().unwrap();
    for message in messages {
        writeln!(stdin, "{message}").unwrap();
    }
    drop(stdin);
    assert!(child.wait().unwrap().success());
    let text = String::from_utf8(reader.join().unwrap()).unwrap();
    let replies: Vec<Value> = text
        .lines()
        .map(|s| serde_json::from_str(s).unwrap())
        .collect();
    assert_eq!(replies.len(), cases.len() + 1);
    for ((name, _, _, stale), reply) in cases.iter().zip(&replies[1..]) {
        assert_eq!(reply["result"]["isError"], false, "{name}: {reply}");
        let plan: Value =
            serde_json::from_str(reply["result"]["content"][0]["text"].as_str().unwrap()).unwrap();
        if *name == "raw_place" {
            let actual: model::Plan = serde_json::from_value(plan.clone()).unwrap();
            let mut peers = session.plan().clone();
            peers.dimensions.retain(|d| d.id != changed_place_id);
            fixed(&peers, &actual);
            assert!(
                !actual
                    .problems
                    .iter()
                    .any(|p| p.code == "engine_pins_replanned")
            );
        }
        if *name == "revision" {
            assert_eq!(plan["views"], value(&session.plan().views));
            assert!(!plan["spec"]["engine_pins"].is_null());
        }
        assert_eq!(
            plan["problems"]
                .as_array()
                .unwrap()
                .iter()
                .any(|p| p["code"]
                    == if matches!(*name, "missing" | "raw_place") {
                        "engine_pins_partly_retained"
                    } else {
                        "engine_pins_replanned"
                    }),
            *stale,
            "{name}"
        );
    }
}

#[test]
fn multiple_rebase_selections_are_merged() {
    let (session, _) = pinned_bracket();
    let ids: Vec<_> = session
        .plan()
        .dimensions
        .iter()
        .take(2)
        .map(|d| d.id.clone())
        .collect();
    let edited = model::edit(
        session.spec(),
        &json!([
            {"op":"add","path":"engine_pins/rebase","value":ids[0]},
            {"op":"add","path":"engine_pins/rebase","value":ids[1]}
        ]),
    )
    .unwrap();
    assert_eq!(edited.engine_pins.as_ref().unwrap().rebase, ids);
}

#[test]
fn outside_pmi_revision_keeps_surviving_pins_and_reads_current_content() {
    let (session, bytes) = pinned_bracket();
    let revised = pmi_edit(&bytes, |p| {
        let target = face_target(p, planar_face(&session));
        p.tolerances
            .push(pmi_tolerance(target, ToleranceKind::Flatness, None));
    });
    let reopened = reopened("bracket", &revised, &session);
    fixed(session.plan(), reopened.plan());
    assert!(reopened.spec().engine_pins.is_some());
    assert!(
        reopened
            .state()
            .problems
            .iter()
            .all(|p| p.code != "engine_pins_replanned")
    );
    assert!(
        reopened
            .plan()
            .gdt
            .iter()
            .any(|g| g.cells.iter().any(|c| c == "⏥"))
    );
    assert_ne!(
        session.plan().binding.source_sha256,
        reopened.plan().binding.source_sha256
    );
}

#[test]
fn opening_a_saved_spec_in_an_existing_session_verifies_the_current_step() {
    let (session, bytes) = pinned_bracket();
    let revised = pmi_edit(&bytes, |p| {
        let target = face_target(p, planar_face(&session));
        p.tolerances
            .push(pmi_tolerance(target, ToleranceKind::Flatness, None));
    });
    let (part, analysis) = model::analyse_native_bytes("bracket", &revised).unwrap();
    let mut opened = Session::new(part, analysis, Spec::default()).unwrap();
    opened
        .open_spec(&serde_json::to_string(session.spec()).unwrap())
        .unwrap();
    fixed(session.plan(), opened.plan());
    assert!(opened.spec().engine_pins.is_some());
    assert!(
        opened
            .plan()
            .gdt
            .iter()
            .any(|g| g.cells.iter().any(|c| c == "⏥"))
    );
}

#[test]
fn crowded_ctc_retained_datums_reload_as_their_published_ink() {
    let bytes = source("ctc");
    let mut session = open_spec(
        "ctc",
        serde_json::from_value(json!({"sheet":{"size":"A4","scale":"1:1"},"hole_table":"never"}))
            .unwrap(),
    );
    let revised = pmi_edit(&without_a(&bytes), |p| {
        let target = face_target(p, planar_face(&session));
        p.tolerances
            .push(pmi_tolerance(target, ToleranceKind::Flatness, None));
    });
    let before = session.state().clone();
    let delta = session
        .replace_step_incremental("ctc", &revised, true)
        .unwrap();
    fixed(&before.plan, session.plan());
    assert!(delta.moved.is_empty());
    for d in session
        .plan()
        .dimensions
        .iter()
        .filter(|d| d.placed.is_none())
    {
        assert!(
            session
                .spec()
                .engine_pins
                .as_ref()
                .unwrap()
                .annotations
                .get(&d.id)
                .is_some_and(|p| p.omit),
            "{} must retain its absence of ink",
            d.id
        );
    }
    let restored = reopened("ctc", &revised, &session);
    fixed(session.plan(), restored.plan());
    assert_eq!(
        value(&session.state().annotations),
        value(&restored.state().annotations)
    );
    assert_eq!(session.state().problems, restored.state().problems);
}

#[test]
fn notes_and_declared_tables_keep_their_placement_after_pmi_and_content_edits() {
    let spec: Spec = serde_json::from_value(json!({
        "notes":[{"id":"n:1","text":"REMOVE BURRS"}],
        "tables":[{"id":"t:1","rows":[["OP","NOTE"],["1","DEBURR"]]}]
    }))
    .unwrap();
    let mut session = open_spec("bracket", spec);
    let original = session.state().clone();
    let revised = datum_step(&source("bracket"), planar_face(&session));
    session
        .replace_step_incremental("bracket", &revised, true)
        .unwrap();
    fixed(&original.plan, session.plan());
    let restored = reopened("bracket", &revised, &session);
    fixed(session.plan(), restored.plan());
    assert_eq!(
        value(&session.state().annotations),
        value(&restored.state().annotations)
    );
    session
        .apply(
            &[Op::set(
                "notes",
                json!([{"id":"n:1","text":"REMOVE ALL BURRS"}]),
            )],
            true,
        )
        .unwrap();
    let before = session.state().clone();
    session
        .apply(
            &[Op::set(
                "tables",
                json!([{"id":"t:1","rows":[["OP", "NOTE"], ["1", "DEBURR"], ["2", "CLEAN"]]}]),
            )],
            true,
        )
        .unwrap();
    assert_eq!(value(&before.plan.views), value(&session.plan().views));
    for note in &before.plan.notes {
        let current = session
            .plan()
            .notes
            .iter()
            .find(|n| n.id == note.id)
            .unwrap();
        assert_eq!(note.placed, current.placed);
    }
    for dim in &before.plan.dimensions {
        let current = session
            .plan()
            .dimensions
            .iter()
            .find(|d| d.id == dim.id)
            .unwrap();
        assert_eq!(dim.placed, current.placed);
    }
    let cold = reopened("bracket", &revised, &session);
    assert_eq!(
        value(&session.state().annotations),
        value(&cold.state().annotations)
    );
    assert_eq!(value(&session.plan().views), value(&cold.plan().views));
}

#[test]
fn removing_one_note_in_full_apply_keeps_another_notes_placement() {
    let (mut session, bytes) = pinned_bracket();
    session
        .apply(
            &[Op::set(
                "notes",
                json!([
                    {"id":"n:1","text":"REMOVE BURRS"},
                    {"id":"n:2","text":"CLEAN AFTER MACHINING"}
                ]),
            )],
            true,
        )
        .unwrap();
    let before = session
        .plan()
        .notes
        .iter()
        .find(|n| n.id == "n:2")
        .unwrap()
        .placed
        .clone();
    assert!(before.is_some());
    session
        .apply(
            &[Op::set(
                "notes",
                json!([
                    {"id":"n:2","text":"CLEAN AFTER MACHINING"}
                ]),
            )],
            true,
        )
        .unwrap();
    assert!(session.spec().engine_pins.is_some());
    assert_eq!(
        before,
        session
            .plan()
            .notes
            .iter()
            .find(|n| n.id == "n:2")
            .unwrap()
            .placed
    );
    let cold = reopened("bracket", &bytes, &session);
    assert_eq!(
        value(&session.state().annotations),
        value(&cold.state().annotations)
    );
}

#[test]
fn rebase_then_ctc_reload_share_one_process() {
    datum_rebase_offer_previews_and_applies_only_when_chosen();
    pmi_round_trip("ctc");
}

#[test]
fn ctc_reload_then_rebase_share_one_process() {
    pmi_round_trip("ctc");
    datum_rebase_offer_previews_and_applies_only_when_chosen();
}

#[test]
fn ordinary_plan_after_incremental_matches_fresh_process() {
    const CHILD: &str = "DRAFTWRIGHT_FRESH_INCREMENTAL_REGRESSION";
    if let Some(path) = std::env::var_os(CHILD) {
        std::fs::write(path, serde_json::to_vec(open("ctc").plan()).unwrap()).unwrap();
        return;
    }
    let path = Path::new(env!("CARGO_TARGET_TMPDIR"))
        .join(format!("fresh-ctc-{}.json", std::process::id()));
    let child = std::process::Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "incremental::ordinary_plan_after_incremental_matches_fresh_process",
        ])
        .env(CHILD, &path)
        .output()
        .unwrap();
    assert!(
        child.status.success(),
        "{}",
        String::from_utf8_lossy(&child.stderr)
    );
    let fresh: Value = serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
    std::fs::remove_file(path).unwrap();
    let mut session = open("ctc");
    let updated = datum_step(&source("ctc"), planar_face(&session));
    session
        .replace_step_incremental("ctc", &updated, true)
        .unwrap();
    datum_rebase_offer_previews_and_applies_only_when_chosen();
    assert_eq!(value(open("ctc").plan()), fresh);
}

#[test]
fn ordinary_spec_only_history_is_not_capped() {
    let mut s = open("bracket");
    let original = value(s.state());
    for i in 0..100 {
        s.apply(
            &[Op::set("sheet/title/drawn_by", json!(format!("USER {i}")))],
            true,
        )
        .unwrap();
    }
    for _ in 0..100 {
        s.undo().unwrap();
    }
    assert_eq!(value(s.state()), original);
    assert!(s.undo().is_err());
    for _ in 0..100 {
        s.redo().unwrap();
    }
    assert!(s.redo().is_err());
}

#[test]
fn unpinned_spec_four_has_current_spec_three_semantics() {
    let s = open("bracket");
    let mut three = Spec::default();
    three
        .dimensions
        .insert("d:envelope/width.length@front".into(), Default::default());
    let mut four = three.clone();
    four.schema = "draftwright.spec/4".into();
    let decoded: Spec = serde_json::from_value(value(&four)).unwrap();
    assert_eq!(decoded, three);
    assert_eq!(model::migrate(&four, s.analysis()).unwrap(), three);
    assert_eq!(model::migrate(&three, s.analysis()).unwrap(), three);
    let four: Spec = serde_json::from_value(json!({"schema":"draftwright.spec/4"})).unwrap();
    assert_eq!(value(&four), value(Spec::default()));
}

#[test]
fn compact_native_dimension_constraints_derive_ink_and_keep_exact_reload() {
    for name in ["bracket", "scattered", "ctc"] {
        let mut s = open(name);
        let updated = datum_step(&source(name), planar_face(&s));
        let before = s.state().clone();
        let delta = s.replace_step_incremental(name, &updated, true).unwrap();
        assert!(delta.moved.is_empty());
        fixed(&before.plan, s.plan());
        let pins = s.spec().engine_pins.as_ref().unwrap();
        let mut native = 0;
        for d in &s.plan().dimensions {
            let Some(pin) = pins.annotations.get(&d.id) else {
                continue;
            };
            if pin.route.is_none() && !pin.omit {
                native += 1;
                let record = value(pin);
                for field in ["route", "space", "bounds", "lines", "arrows"] {
                    assert!(record.get(field).is_none(), "{name} {} {field}", d.id);
                }
                if let Some(at) = pin.place.offset {
                    for n in at {
                        assert_eq!(n, (n * 10000.).round() / 10000.);
                    }
                }
            }
        }
        assert!(native > 0, "{name}");
        let cold = reopened(name, &updated, &s);
        fixed(s.plan(), cold.plan());
        assert_eq!(
            value(&s.state().annotations),
            value(&cold.state().annotations)
        );
        assert_eq!(s.state().problems, cold.state().problems);
    }
}

#[test]
fn crowded_full_content_edit_keeps_pins_and_warns_on_the_item() {
    let (mut s, _) = pinned_bracket();
    let before = s.plan().clone();
    let id = before
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .id
        .clone();
    let op = Op::set(
        format!("dimensions/{}/place", draftwright::items::key(&id)),
        json!({"offset":[1000., 1000.], "pinned":true}),
    );
    let preview = s.apply(std::slice::from_ref(&op), false).unwrap();
    let committed = s.apply(&[op], true).unwrap();
    assert_eq!(value(preview), value(committed));
    assert!(s.spec().engine_pins.is_some());
    let mut retained = before.clone();
    retained.dimensions.retain(|d| d.id != id);
    fixed(&retained, s.plan());
    for old in before.dimensions.iter().filter(|d| d.id != id) {
        let current = s.plan().dimensions.iter().find(|d| d.id == old.id).unwrap();
        assert_eq!(old.placed, current.placed, "{}", old.id);
        assert_eq!(old.measurements, current.measurements);
    }
    assert!(
        s.state()
            .problems
            .iter()
            .any(|p| p.code == "incremental_placement_constrained"
                && p.severity == "warning"
                && p.subjects.contains(&id))
    );
    assert!(
        !s.state()
            .problems
            .iter()
            .any(|p| p.code == "engine_pins_replanned")
    );
}

#[test]
fn trimmed_source_history_stops_at_a_consistent_revision_boundary() {
    let mut s = open("bracket");
    // These Spec-only states belong to the original source and must not remain
    // reachable once its first source transition is evicted.
    for n in 0..3 {
        s.apply(
            &[Op::set(
                "sheet/title/drawn_by",
                json!(format!("original-{n}")),
            )],
            true,
        )
        .unwrap();
    }
    let base = source("bracket");
    let updated = datum_step(&base, planar_face(&s));
    let mut expected = Vec::new();
    for n in 0..36 {
        expected.push((s.analysis().binding.clone(), value(s.state())));
        let bytes = if n % 2 == 0 { &updated } else { &base };
        s.replace_step_incremental("bracket", bytes, true).unwrap();
    }
    let last = value(s.state());
    let mut undone = 0;
    for (binding, state) in expected.iter().rev().take(32) {
        s.undo().unwrap();
        assert_eq!(&s.analysis().binding, binding);
        assert_eq!(value(s.state()), *state);
        undone += 1;
    }
    assert_eq!(undone, 32);
    assert!(
        s.undo().is_err(),
        "the trimmed source boundary must stop undo"
    );
    for _ in 0..32 {
        s.redo().unwrap();
    }
    assert_eq!(value(s.state()), last);
    assert!(s.redo().is_err());
}

#[test]
fn oversized_content_in_full_apply_warns_without_reshuffling() {
    let (mut s, _) = pinned_bracket();
    let before = s.plan().clone();
    let id = before
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .id
        .clone();
    let op = Op::set(
        format!("dimensions/{}/text", draftwright::items::key(&id)),
        json!({"prefix":"W".repeat(230)}),
    );
    let preview = s.apply(std::slice::from_ref(&op), false).unwrap();
    let committed = s.apply(&[op], true).unwrap();
    assert_eq!(value(preview), value(committed));
    assert!(s.spec().engine_pins.is_some());
    let mut retained = before.clone();
    retained.dimensions.retain(|d| d.id != id);
    fixed(&retained, s.plan());
    assert!(
        s.state()
            .problems
            .iter()
            .any(|p| p.code == "incremental_placement_constrained"
                && p.severity == "warning"
                && p.subjects.contains(&id))
    );
    assert!(
        !s.state()
            .problems
            .iter()
            .any(|p| p.code == "engine_pins_replanned")
    );
}

#[test]
fn whole_dimension_map_edits_release_only_the_changed_items_pin() {
    let (mut s, _) = pinned_bracket();
    let before = s.plan().clone();
    let id = before
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .id
        .clone();
    let mut spec = s.spec().clone();
    spec.dimensions.entry(id.clone()).or_default().place =
        Some(serde_json::from_value(json!({"offset":[1000.,1000.],"pinned":true})).unwrap());
    s.set_spec(&spec).unwrap();
    assert!(s.spec().engine_pins.is_some());
    let changed = s.plan().dimensions.iter().find(|d| d.id == id).unwrap();
    let previous = before.dimensions.iter().find(|d| d.id == id).unwrap();
    assert_ne!(
        previous.placed, changed.placed,
        "whole-map edits must honour authored placement"
    );
    let mut retained = before;
    retained.dimensions.retain(|d| d.id != id);
    fixed(&retained, s.plan());
    assert!(
        s.state()
            .problems
            .iter()
            .any(|p| p.code == "incremental_placement_constrained" && p.subjects.contains(&id))
    );
}

#[test]
fn hand_edited_place_keeps_peer_pins_in_api_session_and_cli() {
    use std::process::Command;
    let (mut s, bytes) = pinned_bracket();
    let before = s.plan().clone();
    let id = before
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .id
        .clone();
    let mut edited = s.spec().clone();
    edited.dimensions.entry(id.clone()).or_default().place =
        Some(serde_json::from_value(json!({"offset":[1000.,1000.],"pinned":true})).unwrap());
    // Bypass model::edit, as an agent editing a file or an openSpec caller does.
    let api = model::plan_recovered(s.analysis(), &edited).unwrap();
    let (part, analysis) = model::analyse_native_bytes("bracket", &bytes).unwrap();
    let opened = Session::new(part, analysis, edited.clone()).unwrap();
    s.set_spec(&edited).unwrap();
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("hand-edited-place");
    std::fs::create_dir_all(&dir).unwrap();
    let step = dir.join("bracket.step");
    let file = dir.join("edited.spec.json");
    std::fs::write(&step, &bytes).unwrap();
    std::fs::write(&file, serde_json::to_vec(&edited).unwrap()).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_draftwright"))
        .args([
            "plan",
            step.to_str().unwrap(),
            "--spec",
            file.to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let cli: model::Plan = serde_json::from_slice(&output.stdout).unwrap();
    let original = before.dimensions.iter().find(|d| d.id == id).unwrap();
    let mut peers = before.clone();
    peers.dimensions.retain(|d| d.id != id);
    for plan in [&api, opened.plan(), s.plan(), &cli] {
        fixed(&peers, plan);
        let changed = plan.dimensions.iter().find(|d| d.id == id).unwrap();
        assert_ne!(original.placed, changed.placed);
        assert_eq!(
            changed.placed,
            s.plan()
                .dimensions
                .iter()
                .find(|d| d.id == id)
                .unwrap()
                .placed
        );
        assert!(plan.spec.engine_pins.is_some());
        assert!(
            !plan
                .problems
                .iter()
                .any(|p| p.code == "engine_pins_replanned")
        );
    }
    let reloaded = reopened("bracket", &bytes, &s);
    fixed(s.plan(), reloaded.plan());
}

fn fixed_inch_geometry(before: &model::Plan, after: &model::Plan) {
    // #501 permits fresh diagnostic provenance when the same table ink is restored.
    // View basis also describes a pre-repair fit; geometry and engineering bases
    // are checked independently below.
    fn provenance(plan: &mut model::Plan) {
        for view in &mut plan.views {
            view.basis.clear();
        }
        fn clear(table: &mut draftwright::tables::PlannedTable) {
            table.basis.clear();
        }
        for table in plan
            .hole_tables
            .iter_mut()
            .chain(plan.hole_table.iter_mut())
        {
            table.basis.clear();
            for block in &mut table.blocks {
                clear(block);
            }
        }
    }
    let (mut a, mut b) = (before.clone(), after.clone());
    provenance(&mut a);
    provenance(&mut b);
    fixed(&a, &b);
    for d in &before.dimensions {
        let retained = after
            .dimensions
            .iter()
            .find(|kept| kept.id == d.id)
            .unwrap();
        assert_eq!(
            (&d.terms, &d.measurements),
            (&retained.terms, &retained.measurements),
            "{} engineering values",
            d.id
        );
        if let (Some(a), Some(b)) = (&before.spec.engine_pins, &after.spec.engine_pins) {
            assert_eq!(a.origins, b.origins);
            if let (Some(a), Some(b)) = (a.annotations.get(&d.id), b.annotations.get(&d.id)) {
                assert_eq!(a.basis, b.basis, "{} engineering basis", d.id);
            }
        }
    }
}

fn inch_source(name: &str) -> Vec<u8> {
    let corpus = std::env::var("DRAFTWRIGHT_CORPUS")
        .unwrap_or_else(|_| "/Users/paul/repos/quiddity/tests/corpus".into());
    let file = match name {
        "ftc07" => "nist/nist_ftc_07_asme1_rd.stp",
        "ctc05" => "nist/nist_ctc_05_asme1_rd.stp",
        "ftc08" => "nist/nist_ftc_08_asme1_rc.stp",
        _ => unreachable!(),
    };
    std::fs::read(Path::new(&corpus).join(file)).unwrap()
}
fn inch_session(name: &str, bytes: &[u8]) -> Session {
    let (part, analysis) = model::analyse_native_bytes(name, bytes).unwrap();
    assert_eq!(analysis.declared_length_unit, Some(LengthUnit::Inch));
    // CTC-05's automatic drawing is multi-sheet. Exercise the single-sheet
    // incremental API with explicit paper, principal views and box origins,
    // keeping units automatic. FTC-07 below exercises the automatic layout.
    let spec = if name == "ctc05" {
        serde_json::from_value(
            json!({"sheet":{"size":"ANSI E", "scale":"1:4"}, "views":{"section_aa":{"omit":true}}, "origin":{"x":"min","y":"min","z":"min"}}),
        )
        .unwrap()
    } else {
        Spec::default()
    };
    let s = Session::new(part, analysis, spec).unwrap();
    assert_eq!(s.plan().sheet.units, draftwright::units::Units::In);
    assert_eq!(
        draftwright::units::Paper::for_size(&s.plan().sheet.size),
        draftwright::units::Paper::Ansi
    );
    assert!(s.spec().units.is_auto());
    s
}

#[test]
fn declared_inches_incremental_pmi_keeps_units_basis_and_exact_reload() {
    for name in ["ftc07", "ctc05"] {
        let bytes = inch_source(name);
        let mut s = inch_session(name, &bytes);
        // The drawing explicitly asks to show this newly declared, uncited datum.
        s.apply(&[Op::set("sheet/uncited_datums", json!(true))], true)
            .unwrap();
        let before = s.state().clone();
        let source_pmi = value(&s.analysis().pmi);
        let updated = pmi_edit(&bytes, |p| {
            assert!(!p.datums.iter().any(|d| d.label().as_str() == "X"));
            let claimed: std::collections::BTreeSet<_> = s
                .plan()
                .gdt
                .iter()
                .filter(|g| g.kind == "datum_feature")
                .flat_map(|g| g.faces.iter().copied())
                .collect();
            let face = s
                .part()
                .faces
                .iter()
                .enumerate()
                .filter_map(|(i, f)| {
                    if let draftwright::haecceity::geom::Surface::Plane { frame } = &f.surface {
                        (!claimed.contains(&i)
                            && frame.z.iter().any(|v| (v.abs() - 1.0).abs() < 1e-10))
                        .then_some((i, frame.origin[2]))
                    } else {
                        None
                    }
                })
                .max_by(|a, b| a.1.total_cmp(&b.1))
                .unwrap()
                .0;
            let target = face_target(p, face);
            p.datums
                .push(Datum::new(DatumLabel::new("X").unwrap(), Some(target), vec![]).unwrap());
            let mut flatness = pmi_tolerance(target, ToleranceKind::Flatness, None);
            flatness.magnitude = Some(hp::Value::length(
                Decimal::parse("0.254").unwrap(),
                LengthUnit::Millimetre,
            ));
            p.tolerances.push(flatness);
        });
        let preview = s.replace_step_incremental(name, &updated, false).unwrap();
        assert_eq!(value(s.state()), value(&before));
        let applied = s.replace_step_incremental(name, &updated, true).unwrap();
        assert_eq!(value(&preview), value(&applied));
        assert!(applied.moved.is_empty(), "{name}: {:?}", applied.moved);
        fixed_inch_geometry(&before.plan, s.plan());
        for d in &before.plan.dimensions {
            let kept = s
                .plan()
                .dimensions
                .iter()
                .find(|kept| kept.id == d.id)
                .unwrap();
            assert_eq!(
                (&d.label, &d.terms, &d.measurements, &d.basis),
                (&kept.label, &kept.terms, &kept.measurements, &kept.basis),
                "{name}: {}",
                d.id
            );
        }
        let new: Vec<_> = s
            .plan()
            .gdt
            .iter()
            .filter(|g| !before.plan.gdt.iter().any(|old| old.id == g.id))
            .collect();
        assert!(
            new.iter().any(|g| g.cells == ["X"]),
            "{name}: datums {:?}; problems {:?}; sheet {:?}",
            s.plan()
                .gdt
                .iter()
                .map(|g| (&g.id, &g.cells, &g.state))
                .collect::<Vec<_>>(),
            s.state()
                .problems
                .iter()
                .filter(|p| p.code == "pmi_not_lowered")
                .collect::<Vec<_>>(),
            s.spec().sheet
        );
        assert!(
            new.iter().any(|g| g.cells.first().is_some_and(|c| c == "⏥")
                && g.cells.iter().any(|c| c == ".010"))
        );
        assert!(
            new.iter().all(|g| g.placed.is_some()),
            "{name}: new ink must be present"
        );
        assert_eq!(s.plan().sheet.units, draftwright::units::Units::In);
        let wire = value(s.spec());
        assert!(wire.get("units").is_none());
        assert_eq!(wire["engine_pins"]["sheet"]["units"], "in");
        let loaded = reopened(name, &updated, &s);
        fixed_inch_geometry(s.plan(), loaded.plan());
        assert_eq!(loaded.plan().sheet.units, draftwright::units::Units::In);
        assert!(loaded.spec().units.is_auto());
        s.replace_step_incremental(name, &bytes, true).unwrap();
        fixed_inch_geometry(&before.plan, s.plan());
        assert_eq!(
            source_pmi,
            value(&s.analysis().pmi),
            "{name}: source evidence"
        );
        // Main emits two source diagnostics twice on CTC-05. Compare every
        // complete finding by stable ID, and require the incremental result to
        // contain each only once; the ordinary full path stays byte-identical.
        let findings = |ps: &[model::Problem]| {
            let mut unique = std::collections::BTreeMap::new();
            for p in ps {
                if let Some(old) = unique.insert(p.id.clone(), value(p)) {
                    assert_eq!(old, value(p), "conflicting duplicate finding {}", p.id);
                }
            }
            unique
        };
        assert_eq!(findings(&before.problems), findings(&s.state().problems));
        assert_eq!(
            findings(&s.state().problems).len(),
            s.state().problems.len()
        );
    }
}

#[test]
fn hand_edited_inch_place_releases_only_that_pin() {
    let bytes = inch_source("ftc07");
    let mut s = inch_session("ftc07", &bytes);
    s.apply_incremental(&[], true).unwrap();
    let before = s.plan().clone();
    let id = before
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .id
        .clone();
    let mut edited = s.spec().clone();
    edited.dimensions.entry(id.clone()).or_default().place =
        Some(serde_json::from_value(json!({"offset":[1000.,1000.],"pinned":true})).unwrap());
    let raw = model::plan_recovered(s.analysis(), &edited).unwrap();
    let (part, a) = model::analyse_native_bytes("ftc07", &bytes).unwrap();
    let loaded = Session::new(part, a, edited.clone()).unwrap();
    s.set_spec(&edited).unwrap();
    let mut peers = before.clone();
    peers.dimensions.retain(|d| d.id != id);
    for p in [&raw, loaded.plan(), s.plan()] {
        fixed_inch_geometry(&peers, p);
        assert_eq!(p.sheet.units, draftwright::units::Units::In);
        assert!(p.spec.units.is_auto());
        assert!(p.spec.engine_pins.is_some());
        assert_ne!(
            before
                .dimensions
                .iter()
                .find(|d| d.id == id)
                .unwrap()
                .placed,
            p.dimensions.iter().find(|d| d.id == id).unwrap().placed
        );
        assert!(!p.problems.iter().any(|p| p.code == "engine_pins_replanned"));
    }
}

#[test]
fn ftc08_schedule_tags_compose_with_engine_pins_and_inch_repairs() {
    let bytes = inch_source("ftc08");
    let mut s = inch_session("ftc08", &bytes);
    assert!(!s.plan().hole_tables.is_empty());
    assert!(!s.plan().balloons.is_empty());
    let before = s.plan().clone();
    s.apply_incremental(&[], true).unwrap();
    fixed_inch_geometry(&before, s.plan());
    let id = before
        .dimensions
        .iter()
        .find(|d| d.placed.is_some())
        .unwrap()
        .id
        .clone();
    let op = Op::set(
        format!("dimensions/{}/place", draftwright::items::key(&id)),
        json!({"pinned":true}),
    );
    s.apply(&[op], true).unwrap();
    fixed_inch_geometry(&before, s.plan());
    let loaded = reopened("ftc08", &bytes, &s);
    fixed_inch_geometry(s.plan(), loaded.plan());
    assert_eq!(loaded.plan().sheet.units, draftwright::units::Units::In);
}
