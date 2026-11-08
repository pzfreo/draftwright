//! Numbered AP242 PMI exercises the real lossless writer, reader and drawing pipeline.
use draftwright::{
    api,
    characteristics::{self, Mode},
    haecceity::{
        self,
        p21::{self, Attribute as A, Document, RawEntity},
    },
    items,
    model::{self, Spec},
    pmi::{Category, Field, Reading},
    text::Font,
};
use std::{collections::BTreeMap, io::Read as _, path::Path};
fn bytes(name: &str) -> Vec<u8> {
    let b = std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join(name)).unwrap();
    if name.ends_with(".gz") {
        let mut out = Vec::new();
        flate2::read::GzDecoder::new(&b[..])
            .read_to_end(&mut out)
            .unwrap();
        out
    } else {
        b
    }
}
fn analysed(b: &[u8]) -> (haecceity::Part, model::Analysis) {
    model::analyse_native_bytes("numbered.step", b).unwrap()
}
fn on() -> Spec {
    let mut s = Spec::default();
    s.sheet.balloons = Mode::All;
    s
}
fn report(a: &model::Analysis) -> &draftwright::pmi::Report {
    let Some(Reading::Read(r)) = &a.pmi else {
        panic!("PMI")
    };
    r
}
/// A small set of existing drawn requirements on a real part. Original instances and geometry
/// are untouched; the writer appends only UDA properties and representation items.
fn fixture(name: &str) -> (Vec<u8>, usize) {
    let b = bytes(name);
    let (_, mut a) = analysed(&b);
    if let Some(Reading::Read(r)) = &mut a.pmi {
        for (i, r) in r.records.iter_mut().enumerate() {
            if matches!(
                r.category,
                Category::Dimension | Category::GeometricTolerance | Category::Datum
            ) {
                r.fields.push((
                    "characteristic number".into(),
                    Field::Text((i + 101).to_string()),
                ));
            }
        }
    }
    let p = model::plan_recovered(&a, &Spec::default()).unwrap();
    let mut candidates: Vec<_> = p
        .characteristics
        .iter()
        .filter(|c| c.drawn_item.is_some())
        .collect();
    candidates.sort_by_key(|c| {
        let r = report(&a)
            .records
            .iter()
            .find(|r| r.source_id == c.source)
            .unwrap();
        (
            r.category,
            std::cmp::Reverse(r.geometry.as_ref().map_or(0, |g| g.cylinders.len())),
        )
    });
    let mut counts = BTreeMap::new();
    let selected: Vec<_> = candidates
        .into_iter()
        .filter(|c| {
            let r = report(&a)
                .records
                .iter()
                .find(|r| r.source_id == c.source)
                .unwrap();
            let n = counts.entry(r.category).or_insert(0);
            *n += 1;
            *n <= 2
        })
        .collect();
    assert!(!selected.is_empty());
    let doc = Document::parse(b).unwrap();
    let ctx = doc
        .ids()
        .find_map(|id| match doc.get(id).unwrap() {
            RawEntity::Simple {
                name, attributes, ..
            } if name == "REPRESENTATION_CONTEXT" => Some(A::EntityRef(id)),
            RawEntity::Complex { parts, .. }
                if parts.iter().any(|p| p.name == "REPRESENTATION_CONTEXT") =>
            {
                Some(A::EntityRef(id))
            }
            _ => None,
        })
        .unwrap();
    let mut e = p21::Edit::new();
    // Edition 4 extends characterized_definition to size/tolerance items (UDA owners).
    // The lossless edit keeps all source instances, upgrading only the FILE_SCHEMA header.
    e.set_file_schema(vec![haecceity::express::TARGET_SCHEMA.into()]);
    for (i, c) in selected.iter().enumerate() {
        let r = report(&a)
            .records
            .iter()
            .find(|r| r.source_id == c.source)
            .unwrap();
        let n = 101 + i as i64 * 7;
        let val = if i % 2 == 0 {
            A::String(n.to_string())
        } else {
            A::Integer(n)
        };
        let v = e.add(p21::simple(
            if i % 2 == 0 {
                "DESCRIPTIVE_REPRESENTATION_ITEM"
            } else {
                "INTEGER_REPRESENTATION_ITEM"
            },
            vec![A::String("characteristic number".into()), val],
        ));
        let rep = e.add(p21::simple(
            "REPRESENTATION",
            vec![A::String("".into()), A::List(vec![v.to_ref()]), ctx.clone()],
        ));
        let prop = e.add(p21::simple(
            "PROPERTY_DEFINITION",
            vec![
                A::String("characteristic number".into()),
                A::String("user defined attribute".into()),
                A::EntityRef(r.part21_id),
            ],
        ));
        e.add(p21::simple(
            "PROPERTY_DEFINITION_REPRESENTATION",
            vec![prop.to_ref(), rep.to_ref()],
        ));
        let gp = e.add(p21::simple(
            "GENERAL_PROPERTY",
            vec![
                A::String("".into()),
                A::String("characteristic number".into()),
                A::Unset,
            ],
        ));
        e.add(p21::simple(
            "GENERAL_PROPERTY_ASSOCIATION",
            vec![A::String("".into()), A::Unset, gp.to_ref(), prop.to_ref()],
        ));
    }
    let written = doc.apply(&e).unwrap();
    let d = Document::parse(written.bytes.clone()).unwrap();
    let edition = haecceity::express::edition(&d.file_schema().unwrap()[0]).unwrap();
    let errors: Vec<_> = edition
        .validate_document(d.graph())
        .into_iter()
        .filter(|v| v.id > doc.max_id())
        .collect();
    assert!(errors.is_empty(), "{name}: {errors:?}");
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("target/balloons-fixtures");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(
        dir.join(
            Path::new(name)
                .file_name()
                .unwrap()
                .to_string_lossy()
                .trim_end_matches(".gz"),
        ),
        &written.bytes,
    )
    .unwrap();
    (written.bytes, selected.len())
}
fn draw(
    part: &haecceity::Part,
    a: &model::Analysis,
    p: &model::Plan,
) -> draftwright::output::Drawing {
    model::render(part, a, p, &Font::new(api::MONO.to_vec()).unwrap()).unwrap()
}
#[test]
fn numbered_pmi_round_trips_to_items_and_clear_rings() {
    for name in [
        "tests/fixtures/parts/editor_pmi.step",
        "tests/fixtures/pmi/step/GRM-03_thumbwheel_drive_screw_AP242_PMI.step.gz",
        "tests/fixtures/pmi/step/nist_ctc_02_asme1_ap242-e2.stp.gz",
    ] {
        let (b, n) = fixture(name);
        let (part, a) = analysed(&b);
        let off = model::plan_recovered(&a, &Spec::default()).unwrap();
        let p = model::plan_recovered(&a, &on()).unwrap();
        // Main's #254 fixtures, with the same UDA edits and balloons enabled.
        let (paper, scale, crossings, extensions) = match name {
            // Drawn at 2:1 now that covered PMI spans leave no empty rungs (main: 1:1, one
            // leader crossing and one extension crossing). The two numbers whose carriers stand
            // deep inside the plan view take rings just outside it.
            "tests/fixtures/parts/editor_pmi.step" => ("A3", "2:1", 0, 0),
            "tests/fixtures/pmi/step/GRM-03_thumbwheel_drive_screw_AP242_PMI.step.gz" => {
                ("A4", "2:1", 0, 0)
            }
            _ => ("A2", "1:5", 11, 19),
        };
        assert_eq!((&*p.sheet.size, &*p.sheet.scale), (paper, scale), "{name}");
        for (code, maximum) in [
            ("annotation_leader_crossing", crossings),
            ("extension_through_feature", extensions),
        ] {
            assert!(
                p.problems.iter().filter(|p| p.code == code).count() <= maximum,
                "{name}: {code}: {:?}",
                p.problems
            );
        }
        let stem = Path::new(name).file_stem().unwrap().to_string_lossy();
        std::fs::write(
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .join(format!("target/balloons-fixtures/{stem}.plan.json")),
            serde_json::to_vec(&p).unwrap(),
        )
        .unwrap();
        eprintln!("{name}: {n} numbers, {} rings", p.inspection_balloons.len());
        assert_eq!(p.characteristics.len(), n, "{name}");
        assert_eq!(
            p.inspection_balloons.len(),
            n,
            "{name}: {:?}",
            p.problems
                .iter()
                .filter(|p| p.code.starts_with("characteristic"))
                .collect::<Vec<_>>()
        );
        assert!(off.inspection_balloons.is_empty());
        assert!(
            p.characteristics.iter().all(|c| items::items(&p)
                .iter()
                .any(|i| Some(&i.id) == c.item.as_ref()
                    && i.characteristic_numbers.contains(&c.number)))
        );
        let again = model::plan_recovered(&a, &on()).unwrap();
        assert_eq!(p.inspection_balloons, again.inspection_balloons);
        let d = draw(&part, &a, &p);
        let old = draw(&part, &a, &off);
        let counted = |d: &draftwright::output::Drawing| {
            let mut counts = BTreeMap::new();
            for i in draftwright::lint::structural::check(d) {
                if [
                    "annotation_overlap",
                    "annotation_ink_overlap",
                    "annotation_leader_crossing",
                ]
                .contains(&i.code.as_str())
                {
                    *counts.entry(i.code).or_insert(0usize) += 1;
                }
            }
            counts
        };
        assert_eq!(counted(&old), counted(&d), "{name}");
        assert!(
            !draftwright::lint::lint(&a, &d)
                .iter()
                .any(|i| i.code.starts_with("characteristic")),
            "{name}"
        );
        let mut broken = d.clone();
        let id = &p.inspection_balloons[0].id;
        broken.sheet.items.retain(|i| &i.name != id);
        assert!(
            draftwright::lint::lint(&a, &broken)
                .iter()
                .any(|i| i.code == "characteristic_balloon_missing")
        );
    }
}
#[test]
fn suppression_and_unavailable_characteristics_are_reported() {
    let (b, _) = fixture("tests/fixtures/parts/editor_pmi.step");
    let (_, a) = analysed(&b);
    let mut s = on();
    let p = model::plan_recovered(&a, &s).unwrap();
    let c = &p.characteristics[0];
    s.balloons.items.insert(
        c.source.clone(),
        characteristics::Override {
            suppress: true,
            position: None,
        },
    );
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "characteristic_balloon_missing")
    );
    let mut s = on();
    for c in &p.characteristics {
        if let Some(i) = &c.item {
            if p.dimensions.iter().any(|d| &d.id == i) {
                s.dimensions.entry(i.clone()).or_default().omit = true;
            } else {
                s.annotations.entry(i.clone()).or_default().omit = true;
            }
        }
    }
    let p = model::plan_recovered(&a, &s).unwrap();
    assert!(
        p.problems
            .iter()
            .any(|p| p.code == "characteristic_not_drawn")
    );
}
#[test]
fn numbers_survive_relayout_and_each_ring_stays_on_its_carriers_sheet() {
    let (b, _) = fixture("tests/fixtures/parts/editor_pmi.step");
    let (part, a) = analysed(&b);
    let p = model::plan_recovered(&a, &on()).unwrap();
    // The current allocator keeps annotation-bearing views on sheet 1. An unnumbered
    // authored table on sheet 2 verifies that rendering does not repeat their rings there.
    let s:Spec=serde_json::from_value(serde_json::json!({"sheet":{"balloons":"all"},"sheets":{"max":2},"tables":[{"id":"t:1","rows":[["GENERAL"],["Unnumbered row"]],"sheet":2}]})).unwrap();
    let q = model::plan_recovered(&a, &s).unwrap();
    assert!(!q.sheets.is_empty());
    assert_eq!(
        p.characteristics
            .iter()
            .map(|c| (&c.source, &c.number))
            .collect::<Vec<_>>(),
        q.characteristics
            .iter()
            .map(|c| (&c.source, &c.number))
            .collect::<Vec<_>>()
    );
    let pages =
        model::render_sheets(&part, &a, &q, &Font::new(api::MONO.to_vec()).unwrap()).unwrap();
    for (index, page) in pages.iter().enumerate() {
        for b in &q.inspection_balloons {
            assert_eq!(
                page.sheet.items.iter().any(|i| i.name == b.id),
                b.sheet as usize == index + 1
            );
        }
    }
    for b in &q.inspection_balloons {
        let c = q
            .characteristics
            .iter()
            .find(|c| c.source == b.source)
            .unwrap();
        assert_eq!(c.sheet, Some(b.sheet));
    }
}

#[test]
fn grouped_occurrences_and_relative_pins_keep_the_files_numbers() {
    let (b, _) = fixture("tests/fixtures/parts/editor_pmi.step");
    let (_, a) = analysed(&b);
    let mut s = on();
    let p = model::plan_recovered(&a, &s).unwrap();
    let grouped = p
        .characteristics
        .iter()
        .find(|c| {
            report(&a)
                .records
                .iter()
                .find(|r| r.source_id == c.source)
                .unwrap()
                .geometry
                .as_ref()
                .is_some_and(|g| g.cylinders.len() > 1)
        })
        .unwrap();
    assert_eq!(grouped.balloon_ids.len(), 1);
    s.balloons.occurrences = true;
    let many = model::plan_recovered(&a, &s).unwrap();
    let c = many
        .characteristics
        .iter()
        .find(|c| c.source == grouped.source)
        .unwrap();
    assert!(c.balloon_ids.len() > 2);
    assert!(
        many.inspection_balloons
            .iter()
            .filter(|b| b.source == c.source)
            .count()
            > 1,
        "{:?}",
        many.problems
            .iter()
            .filter(|p| p.code.starts_with("characteristic"))
            .collect::<Vec<_>>()
    );
    assert!(
        many.inspection_balloons
            .iter()
            .filter(|b| b.source == c.source)
            .all(|b| b.number == c.number)
    );
    let b = &p.inspection_balloons[0];
    let carrier = p
        .characteristics
        .iter()
        .find(|c| c.source == b.source)
        .unwrap()
        .drawn_item
        .as_deref()
        .unwrap();
    let rect = p
        .dimensions
        .iter()
        .find(|d| d.id == carrier)
        .and_then(|d| d.placed.as_ref())
        .map(|p| p.label_box)
        .or_else(|| {
            p.gdt
                .iter()
                .find(|g| g.id == carrier)
                .and_then(|g| g.placed.as_ref())
                .map(|p| p.label_box)
        })
        .unwrap();
    let offset = [
        b.at[0] - (rect[0] + rect[2]) / 2.,
        b.at[1] - (rect[1] + rect[3]) / 2.,
    ];
    s.balloons.occurrences = false;
    s.balloons.items.insert(
        b.source.clone(),
        characteristics::Override {
            suppress: false,
            position: Some(offset),
        },
    );
    let q = model::plan_recovered(&a, &s).unwrap();
    // The ring is placed again from its offset to the carrier's centre: a ring far from its
    // carrier (one outside the view its carrier stands deep in) round-trips to within rounding.
    let again = q
        .inspection_balloons
        .iter()
        .find(|c| c.source == b.source)
        .unwrap()
        .at;
    assert!(
        (again[0] - b.at[0]).abs() < 1e-9 && (again[1] - b.at[1]).abs() < 1e-9,
        "{again:?} {:?}",
        b.at
    );
    s.balloons.items.get_mut(&b.source).unwrap().position = Some([0., 0.]);
    let q = model::plan_recovered(&a, &s).unwrap();
    assert!(!q.inspection_balloons.iter().any(|c| c.source == b.source));
    assert!(
        q.problems
            .iter()
            .any(|p| p.code == "characteristic_balloon_missing")
    );
}

#[test]
fn hole_rows_use_number_rings_separate_from_their_table_tags() {
    let (b, _) = fixture("tests/fixtures/parts/editor_pmi.step");
    let (part, a) = analysed(&b);
    let mut s = on();
    s.hole_table = draftwright::hole_table::Setting::Always;
    let p = model::plan_recovered(&a, &s).unwrap();
    std::fs::write(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("target/balloons-fixtures/table.plan.json"),
        serde_json::to_vec(&p).unwrap(),
    )
    .unwrap();
    let rows: Vec<_> = p
        .characteristics
        .iter()
        .filter(|c| {
            c.drawn_item
                .as_ref()
                .is_some_and(|id| id.starts_with("hole_schedule_"))
        })
        .collect();
    assert!(!rows.is_empty(), "{:?}", p.characteristics);
    for c in rows {
        assert_eq!(
            p.inspection_balloons
                .iter()
                .filter(|b| b.source == c.source && Some(&b.item) == c.drawn_item.as_ref())
                .count(),
            1,
            "{:?}",
            p.problems
        );
    }
    assert!(!p.balloons.is_empty());
    assert!(
        p.inspection_balloons
            .iter()
            .all(|b| b.number.bytes().all(|b| b.is_ascii_digit()))
    );
    let d = draw(&part, &a, &p);
    assert!(
        !draftwright::lint::lint(&a, &d)
            .iter()
            .any(|i| i.code.starts_with("characteristic"))
    );
}

/// Specify 0.1.4 owns 34 numbers, including the ten general-tolerance dimensions
/// credited by #265. Every requirement must reach its own settled drawing carrier.
#[test]
fn specify_plate_draws_every_number_on_its_requirement() {
    let b = bytes("tests/fixtures/samples/specify-0.1.4-plate.step.gz");
    let (part, a) = analysed(&b);
    let manufacturing = draftwright::pmi_lowering::manufacturing(&a);
    let shared: Vec<_> = manufacturing
        .owned
        .iter()
        .filter(|o| o.source == "manufacturing_requirement:#2309")
        .collect();
    assert_eq!(shared.len(), 2, "{:?}", manufacturing.refused);
    assert_ne!(shared[0].owner, shared[1].owner);
    let off = model::plan_recovered(&a, &Spec::default()).unwrap();
    let p = model::plan_recovered(&a, &on()).unwrap();
    let taps: Vec<_> = p
        .dimensions
        .iter()
        .filter(|d| d.label.contains("M6"))
        .collect();
    assert_eq!(taps.len(), 1);
    assert_eq!(taps[0].label, "2× M6-6H THRU");
    assert_eq!(
        taps[0]
            .measurements
            .iter()
            .filter(|m| m.parameter == "bore.diameter")
            .count(),
        2
    );
    // Retaining the ten authored datum baselines intentionally changes the
    // former #265 paper/layout baseline. Inspection rings must still cost no
    // extra paper or layout findings compared with the same authored drawing.
    assert_eq!(p.sheet.size, off.sheet.size);
    assert_eq!(p.sheet.scale, off.sheet.scale);
    for code in [
        "sheet_preference_exceeded",
        "annotation_leader_crossing",
        "extension_through_feature",
    ] {
        assert!(
            p.problems.iter().filter(|p| p.code == code).count()
                <= off.problems.iter().filter(|p| p.code == code).count(),
            "inspection rings added {code}: {:?}",
            p.problems
        );
    }
    let table = p
        .hole_tables
        .iter()
        .find(|t| t.id == "hole_schedule_plan")
        .unwrap();
    assert_eq!(table.rows.len(), 7);
    for row in &table.rows {
        let inventory = items::items(&p);
        let item = inventory
            .iter()
            .find(|i| i.id == format!("{}/{}", table.id, row.key()))
            .unwrap();
        assert!(!item.characteristic_numbers.is_empty(), "{}", row.tag);
        assert!(
            p.inspection_balloons
                .iter()
                .any(|b| b.item == format!("{}/{}", table.id, row.key())),
            "{} has no number ring",
            row.tag
        );
    }
    assert_eq!(
        table
            .rows
            .iter()
            .filter(|r| r.cells[1] == "⌀5 THRU M6-6H THRU")
            .count(),
        2
    );
    // A connected group must really touch the earlier ring's circle, rather
    // than a corner of its rectangular obstacle; every group roots on its own
    // callout. This also catches leaders borrowed from neighbouring items.
    for (i, balloon) in p.inspection_balloons.iter().enumerate() {
        let Some(placed) = p
            .dimensions
            .iter()
            .find(|d| d.id == balloon.item)
            .and_then(|d| d.placed.as_ref())
        else {
            continue;
        };
        let tip = balloon.lines.first().expect("a connection to the carrier")[0];
        let b = placed.label_box;
        let at_label = ((tip[0] - b[0]).abs().min((tip[0] - b[2]).abs()) < 1e-6
            && tip[1] >= b[1] - 1e-6
            && tip[1] <= b[3] + 1e-6)
            || ((tip[1] - b[1]).abs().min((tip[1] - b[3]).abs()) < 1e-6
                && tip[0] >= b[0] - 1e-6
                && tip[0] <= b[2] + 1e-6);
        let at_ring = p.inspection_balloons[..i].iter().any(|previous| {
            previous.item == balloon.item
                && ((tip[0] - previous.at[0]).hypot(tip[1] - previous.at[1]) - previous.radius)
                    .abs()
                    < 1e-6
        });
        assert!(
            at_label || at_ring,
            "{} is disconnected from {}",
            balloon.number,
            balloon.item
        );
    }
    assert_eq!(p.characteristics.len(), 34);
    let mut numbers: Vec<u32> = p
        .characteristics
        .iter()
        .map(|c| c.number.parse().unwrap())
        .collect();
    numbers.sort_unstable();
    assert_eq!(numbers, (1..=34).collect::<Vec<_>>());
    // The four-hole size and two-hole thread repeat their existing numbers in
    // each row: 34 requirements, 38 rings, with no newly allocated numbers.
    assert_eq!(p.inspection_balloons.len(), 38, "{:?}", p.problems);
    let mut kinds = BTreeMap::new();
    for c in &p.characteristics {
        let r = report(&a)
            .records
            .iter()
            .find(|r| r.source_id == c.source)
            .unwrap();
        *kinds.entry((r.category, r.kind.as_str())).or_insert(0) += 1;
        let balloon = p
            .inspection_balloons
            .iter()
            .find(|b| b.source == c.source)
            .unwrap();
        assert_eq!(Some(&balloon.item), c.drawn_item.as_ref());
        assert_eq!(balloon.number, c.number);
        let expected = match c.number.as_str() {
            "16" => 4,
            "22" => 2,
            _ => 1,
        };
        assert_eq!(c.balloon_ids.len(), expected, "{}", c.number);
        assert!(c.balloon_ids.iter().all(|id| {
            p.inspection_balloons
                .iter()
                .any(|b| &b.id == id && b.number == c.number && b.source == c.source)
        }));
        assert!(
            items::items(&p)
                .iter()
                .any(|i| Some(&i.id) == c.drawn_item.as_ref()
                    && i.characteristic_numbers.contains(&c.number))
        );
        if r.category == Category::Datum {
            assert_eq!(
                r.label,
                match c.number.as_str() {
                    "1" => "A",
                    "2" => "B",
                    "3" => "C",
                    _ => panic!("datum number"),
                }
            );
        }
        if r.kind == "surface_texture" {
            assert_eq!(c.number, "34");
            assert!(
                p.requirements
                    .iter()
                    .any(|r| Some(&r.id) == c.drawn_item.as_ref()
                        && r.kind == "default_surface_finish"
                        && r.placed.is_some())
            );
        }
        if r.kind == "internal_thread" {
            assert_eq!(c.number, "22");
            assert!(
                table
                    .rows
                    .iter()
                    .any(|row| Some(format!("{}/{}", table.id, row.key())).as_ref()
                        == c.drawn_item.as_ref()
                        && row.cells[1] == "⌀5 THRU M6-6H THRU")
            );
        }
        if r.category == Category::Dimension && r.upper_tol.is_none() && !r.basic {
            assert!(
                p.dimensions
                    .iter()
                    .any(|d| Some(&d.id) == c.item.as_ref() && d.basis.contains(&r.source_id))
                    || p.omitted
                        .iter()
                        .any(|o| Some(&o.id) == c.item.as_ref() && o.basis.contains(&r.source_id))
            );
        }
    }
    let count = |cat| {
        kinds
            .iter()
            .filter(|((c, _), _)| *c == cat)
            .map(|(_, n)| n)
            .sum::<usize>()
    };
    assert_eq!(count(Category::Dimension), 26);
    assert_eq!(count(Category::Datum), 3);
    assert_eq!(count(Category::GeometricTolerance), 3);
    assert_eq!(
        kinds[&(Category::ManufacturingRequirement, "surface_texture")],
        1
    );
    assert_eq!(
        kinds[&(Category::ManufacturingRequirement, "internal_thread")],
        1
    );
    assert_eq!(
        off.problems, p.problems,
        "balloons must add no layout warnings"
    );
    let d = draw(&part, &a, &p);
    let lint = draftwright::lint::lint(&a, &d);
    assert!(
        !lint.iter().any(|i| i.code.starts_with("characteristic")
            || matches!(
                i.code.as_str(),
                "pmi_not_rendered" | "hole_requirement_missing" | "feature_not_dimensioned"
            )),
        "{lint:?}"
    );
    let layout = |drawing: &draftwright::output::Drawing| {
        let mut counts = BTreeMap::new();
        for finding in draftwright::lint::structural::check(drawing) {
            *counts.entry(finding.code).or_insert(0usize) += 1;
        }
        counts
    };
    assert_eq!(layout(&draw(&part, &a, &off)), layout(&d));
}

fn with_id(b: Vec<u8>, owner: u64, values: &[&str]) -> Vec<u8> {
    let doc = Document::parse(b).unwrap();
    let mut edit = p21::Edit::new();
    for value in values {
        edit.add(p21::simple(
            "ID_ATTRIBUTE",
            vec![A::String((*value).into()), A::EntityRef(owner)],
        ));
    }
    doc.apply(&edit).unwrap().bytes
}

#[test]
fn requirement_id_wins_over_uda_and_ambiguity_never_chooses_a_number() {
    let (b, _) = fixture("tests/fixtures/parts/editor_pmi.step");
    let (_, a) = analysed(&b);
    let r = report(&a)
        .records
        .iter()
        .find(|r| r.category == Category::Dimension && characteristics::number(r).is_some())
        .unwrap();
    let source = r.source_id.clone();
    let owner = r.part21_id;
    let uda = characteristics::number(r).unwrap().to_string();
    let (_, a) = analysed(&with_id(b.clone(), owner, &["9001"]));
    let p = model::plan_recovered(&a, &on()).unwrap();
    let c = p
        .characteristics
        .iter()
        .find(|c| c.source == source)
        .unwrap();
    assert_eq!(c.number, "9001");
    assert!(
        p.inspection_balloons
            .iter()
            .any(|b| b.source == source && b.number == "9001")
    );
    let conflict = p
        .problems
        .iter()
        .find(|p| p.code == "characteristic_number_conflict")
        .unwrap();
    assert_eq!(conflict.subjects, [c.item.clone().unwrap()]);
    assert!(conflict.message.contains(&uda));
    for values in [["9001", "9001"], ["9001", "9002"]] {
        let (_, a) = analysed(&with_id(b.clone(), owner, &values));
        let p = model::plan_recovered(&a, &on()).unwrap();
        assert!(!p.characteristics.iter().any(|c| c.source == source));
        assert!(!p.inspection_balloons.iter().any(|c| c.source == source));
        let issue = p
            .problems
            .iter()
            .find(|p| p.code == "characteristic_id_ambiguous")
            .unwrap();
        assert!(
            p.dimensions
                .iter()
                .any(|d| issue.subjects.contains(&d.id) && d.placed.is_some())
        );
        assert!(
            !p.problems
                .iter()
                .any(|p| p.code == "pmi_not_lowered" && p.refs.contains(&source))
        );
    }
    let (_, a) = analysed(&with_id(b.clone(), owner, &["thread-01"]));
    let p = model::plan_recovered(&a, &on()).unwrap();
    assert_eq!(
        p.characteristics
            .iter()
            .find(|c| c.source == source)
            .unwrap()
            .number,
        uda
    );
    assert!(
        !p.problems
            .iter()
            .any(|p| p.code == "characteristic_number_conflict")
    );
}

#[test]
fn nonnumeric_ids_and_shared_supports_do_not_number_notes_or_requirements() {
    let b = bytes("tests/fixtures/samples/specify-0.1.4-plate.step.gz");
    let text = String::from_utf8(b).unwrap();
    let (_, a) = analysed(
        text.replace(
            "ID_ATTRIBUTE('15',#2167)",
            "ID_ATTRIBUTE('thread-01',#2167)",
        )
        .as_bytes(),
    );
    let p = model::plan_recovered(&a, &on()).unwrap();
    assert_eq!(p.characteristics.len(), 33);
    assert!(!p.characteristics.iter().any(|c| c.number == "15"));
    assert!(
        !p.problems
            .iter()
            .any(|p| p.code.starts_with("characteristic"))
    );
    let note = report(&a)
        .records
        .iter()
        .find(|r| r.kind == "edge_condition")
        .unwrap();
    let (_, a) = analysed(&with_id(text.into_bytes(), note.part21_id, &["88"]));
    let p = model::plan_recovered(&a, &on()).unwrap();
    let c = p.characteristics.iter().find(|c| c.number == "88").unwrap();
    assert!(p.notes.iter().any(|n| Some(&n.id) == c.drawn_item.as_ref()));
    assert!(p.inspection_balloons.iter().any(|b| b.number == "88"));
}

#[test]
fn face_textures_and_knurls_use_their_own_id_carriers() {
    for (name, kind) in [
        (
            "tests/fixtures/surface_texture/plate_face_ra0_8.step.gz",
            "surface_finish",
        ),
        (
            "tests/fixtures/turned_knurl/spindle_turned_knurl.step.gz",
            "knurl",
        ),
    ] {
        let b = bytes(name);
        let doc = Document::parse(b.clone()).unwrap();
        let (_, a) = analysed(&b);
        let r = report(&a).records.iter().find(|r| r.kind == kind).unwrap();
        // Pick the exact definition shape / texture property, never its parameter or face.
        let owner = r.instances.iter().copied().find(|&id| {
            matches!(doc.get(id), Some(RawEntity::Simple { name, attributes, .. })
                if (name == "PRODUCT_DEFINITION_SHAPE" && kind == "knurl")
                    || (name == "PROPERTY_DEFINITION" && matches!(attributes.first(), Some(A::String(n)) if n == "surface texture")))
        }).unwrap();
        let (part, a) = analysed(&with_id(b, owner, &["71"]));
        let p = model::plan_recovered(&a, &on()).unwrap();
        let c = p.characteristics.iter().find(|c| c.number == "71").unwrap();
        assert!(
            p.inspection_balloons
                .iter()
                .any(|b| b.source == c.source && b.number == "71"),
            "{name}: {:?}",
            p.problems
        );
        if kind == "surface_finish" {
            assert!(
                p.gdt
                    .iter()
                    .any(|g| Some(&g.id) == c.drawn_item.as_ref() && g.kind == kind)
            );
        } else {
            assert!(
                p.dimensions
                    .iter()
                    .any(|d| Some(&d.id) == c.drawn_item.as_ref() && d.label.contains("KNURL"))
            );
        }
        assert!(
            !draftwright::lint::lint(&a, &draw(&part, &a, &p))
                .iter()
                .any(|i| i.code.starts_with("characteristic"))
        );
    }
}

#[test]
fn datum_ids_stay_distinct_when_two_labels_share_one_feature() {
    let b = bytes("tests/fixtures/samples/specify-0.1.4-plate.step.gz");
    let text = String::from_utf8(b).unwrap().replace(
        "#2119=SHAPE_ASPECT_RELATIONSHIP('','',#2065,#2118);",
        "#2119=SHAPE_ASPECT_RELATIONSHIP('','',#2063,#2118);",
    );
    let (_, a) = analysed(text.as_bytes());
    let datums: Vec<_> = report(&a)
        .records
        .iter()
        .filter(|r| r.category == Category::Datum)
        .collect();
    let a = datums.iter().find(|r| r.label == "A").unwrap();
    let b = datums.iter().find(|r| r.label == "B").unwrap();
    assert_eq!(a.faces, b.faces);
    assert_ne!(a.source_id, b.source_id);
    assert_eq!(characteristics::number(a), Some("1"));
    assert_eq!(characteristics::number(b), Some("2"));
}
