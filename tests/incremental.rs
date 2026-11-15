//! Incremental GD&T on the editor bracket and real quality drawings.
use draftwright::{
    items::Op,
    model::{self, Spec},
    session::Session,
};
use serde_json::{Value, json};
use std::{io::Read, path::Path};

fn open(name: &str) -> Session {
    open_spec(name, Spec::default())
}
fn open_spec(name: &str, spec: Spec) -> Session {
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
    let (part, analysis) = model::analyse_native_bytes(name, &bytes).unwrap();
    Session::new(part, analysis, spec).unwrap()
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
    save(name, "a-before", &s);
    let edited = model::edit(s.spec(), &value(&ops)).unwrap();
    let library = model::plan_incremental(s.analysis(), s.plan(), &edited).unwrap();
    let preview = s.apply_incremental(&ops, false).unwrap();
    assert_eq!(value(s.state()), value(&original));
    let delta = s.apply_incremental(&ops, true).unwrap();
    assert_eq!(value(&preview), value(&delta));
    assert_eq!(value(&library), value(s.plan()));
    save(name, "a-after", &s);
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
    let before = s.state().clone();
    save(name, "b-before", &s);
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
    save(name, "b-after", &s);
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
    save(name, "c-before", &s);
    let delta = s.apply_incremental(&ops, true).unwrap();
    fixed(&before.plan, s.plan());
    assert!(delta.moved.is_empty(), "{}", value(&delta.moved));
    for id in &targets {
        assert!(s.plan().gdt.iter().any(|g| g.source == format!("spec:features/{id}/controls/0") && g.placed.is_some()));
    }
    save(name, "c-after", &s);
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

fn save(name: &str, stage: &str, s: &Session) {
    if let Some(dir) = std::env::var_os("DRAFTWRIGHT_INCREMENTAL_OUT") {
        let dir = std::path::PathBuf::from(dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(format!("{name}-{stage}.svg")), s.drawing().svg()).unwrap();
    }
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
    assert!(delta.moved.iter().any(|m| m.id.starts_with("v:")));
    assert!(delta.moved.iter().all(|m| !m.reason.is_empty()));
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
