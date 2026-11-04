//! Text/feature catalogue rules, checked through the same public representations as planning.
use std::path::{Path, PathBuf};

use draftwright::{dims, model, text::Font};

fn fixture(name: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/parts/rules-text")
        .join(format!("{name}.step"))
}

/// ARCH0008-1: symbols unsupported by the bundled font remain real ink with finite bounds;
/// a callout's display recipe does not become a measurement parameter.
#[test]
fn callout_symbols_are_geometry_and_claims_are_semantic_roles() {
    let font = Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(dims::FONT),
    )
    .unwrap();
    for (symbol, strokes) in [("↧", 4), ("⌴", 3), ("⌵", 2)] {
        let ink = dims::label_ink(&[symbol.into()], &font);
        assert_eq!(ink.strokes.len(), strokes, "{symbol}");
        assert!(
            ink.glyphs.is_empty(),
            "{symbol} must not depend on a missing glyph"
        );
        assert!(ink.min.iter().chain(&ink.max).all(|x| x.is_finite()));
        assert!(ink.min[0] < ink.max[0] && ink.min[1] < ink.max[1]);
    }
    let (_, a) = model::analyse_native(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/plate.step"),
    )
    .unwrap();
    let plan = model::plan(&a, &model::Spec::default()).unwrap();
    let callouts: Vec<_> = plan
        .dimensions
        .iter()
        .filter(|d| d.kind == "callout")
        .collect();
    assert!(!callouts.is_empty());
    for d in callouts {
        assert!(d.label.contains('⌀'));
        assert!(!d.measurements.is_empty());
        for m in &d.measurements {
            assert!(a.features.iter().any(|f| f.id == m.feature));
            assert!(!m.parameter.contains('⌀') && !m.parameter.contains('×'));
            assert!(m.parameter.contains('.'), "a semantic role: {m:?}");
        }
    }
}

fn document(name: &str) -> quiddity::recognition::RecognitionDocument {
    let (part, _) = model::analyse(&fixture(name)).unwrap();
    let recognition = quiddity::correspondence::recognise(&part);
    quiddity::recognition::document(&recognition, part.faces.len())
}

/// ISSUE-958-a: the exact Python solids. Exercise quiddity's document, including recess families
/// the native reader does not yet take, so an unread family cannot make a negative test vacuous.
#[test]
fn separate_solids_do_not_invent_features_and_preserve_real_slots_pads_and_pockets() {
    for name in ["detached", "three-body-pocket", "three-body-channel"] {
        let doc = document(name);
        for family in ["slots", "pads", "pockets", "channels", "section_recesses"] {
            assert!(
                !doc.records.iter().any(|r| r.family == family),
                "{name}: false {family}: {:?}",
                doc.records
            );
        }
        let (part, a) = model::analyse(&fixture(name)).unwrap();
        let native = draftwright::bridge::native::read(&part, &doc).unwrap();
        assert!(
            !native
                .features
                .iter()
                .any(|f| matches!(f.kind.as_str(), "slot" | "pad"))
        );
        let mut a = a;
        model::recognise_native(&part, &mut a).unwrap();
        let plan = model::plan(&a, &model::Spec::default()).unwrap();
        assert!(
            !plan
                .dimensions
                .iter()
                .flat_map(|d| &d.measurements)
                .any(|m| m.parameter.starts_with("slot_") || m.parameter.starts_with("pad_"))
        );
    }
    for (name, family) in [
        ("two-slots", "slots"),
        ("two-pads", "pads"),
        ("two-pockets", "pockets"),
    ] {
        let doc = document(name);
        assert_eq!(
            doc.records.iter().filter(|r| r.family == family).count(),
            2,
            "{name}: {family}"
        );
    }
    // The same two channelled blocks in either compound child order give one channel each.
    for name in ["two-channels", "two-channels-reversed"] {
        let doc = document(name);
        assert_eq!(
            doc.records
                .iter()
                .filter(|r| r.family == "channels")
                .count(),
            2,
            "{name}: channels"
        );
    }
}

/// ARCH0009-3: Python's central through-hole block and two coaxial-bore scope locks from
/// test_layout_generalisation.py. Shape variants keep the row rule independent of one part.
#[test]
fn prismatic_central_bores_keep_their_row_and_turned_bores_clear_axis_ink() {
    let font = Font::open(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("fonts")
            .join(dims::FONT),
    )
    .unwrap();
    for (name, turned) in [
        ("central-prismatic", false),
        ("central-prismatic-wide", false),
        ("central-prismatic-square", false),
        ("synthetic-coaxial-cylinder-x", true),
        ("coaxial-step-x", true),
    ] {
        let (part, a) = model::analyse_native(&fixture(name)).unwrap();
        if name == "synthetic-coaxial-cylinder-x" {
            assert!(part.solid_is_geometrically_valid(0));
        }
        let mut spec = model::Spec::default();
        if turned {
            spec.sheet.scale = Some("2:1".into());
        }
        let plan = model::plan_recovered(&a, &spec).unwrap();
        let callouts: Vec<_> = plan
            .dimensions
            .iter()
            .filter(|d| {
                d.kind == "callout"
                    && d.measurements
                        .iter()
                        .any(|m| m.parameter == "bore.diameter")
            })
            .collect();
        assert_eq!(callouts.len(), 1, "{name}");
        let callout = callouts[0];
        let placed = callout.placed.as_ref().expect("the bore is drawn");
        let view = plan.views.iter().find(|v| v.id == callout.view).unwrap();
        assert_eq!(view.id, if turned { "v:side" } else { "v:plan" });
        let offset = 0.5 * (placed.label_box[1] + placed.label_box[3]) - view.at[1];
        if !turned {
            assert!(
                offset.abs() < dims::TEXT,
                "{name}: centre-row offset {offset} mm"
            );
        }
        if turned {
            assert!(offset.abs() >= dims::TEXT, "{name}: coaxial text on axis");
        }
        let drawing = model::render(&part, &a, &plan, &font).unwrap();
        let [x0, y0, x1, y1] = placed.label_box;
        let crossings: Vec<_> = drawing
            .sheet
            .items
            .iter()
            .filter(|d| d.name != callout.id)
            .flat_map(|d| d.lines.iter().map(move |line| (&d.name, line)))
            .filter(|(_, [a, b])| {
                let row = 0.5 * (a[1] + b[1]);
                (a[1] - b[1]).abs() < 0.05
                    && (a[0] - b[0]).abs() > 1.0
                    && y0 + 0.3 < row
                    && row < y1 - 0.3
                    && a[0].max(b[0]) > x0 + 0.3
                    && a[0].min(b[0]) < x1 - 0.3
            })
            .collect();
        assert!(
            crossings.is_empty(),
            "{name}: horizontal ink through bore text: {crossings:?}"
        );
    }
}

#[test]
fn the_old_coaxial_cylinder_is_a_sealed_cavity_not_a_bore_callout() {
    // build123d's Hole was started on the centred cylinder's middle workplane,
    // so its entire eight-millimetre cut is enclosed. Keep it as a negative pin;
    // the axis-ink rule above now uses a genuine opening, not a false hole.
    let (_, a) = model::analyse_native(&fixture("coaxial-cylinder-x")).unwrap();
    assert!(draftwright::bridge::holes::read(&a).is_empty());
    let voids: Vec<_> = a
        .features
        .iter()
        .filter(|f| f.kind == "interior_void")
        .collect();
    assert_eq!(voids.len(), 1);
    assert!(
        voids[0].parameters["openings"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    let p = model::plan_recovered(&a, &model::Spec::default()).unwrap();
    assert!(p.dimensions.iter().all(|d| {
        d.kind != "callout"
            || !d
                .measurements
                .iter()
                .any(|m| m.parameter == "bore.diameter")
    }));
    assert_eq!(
        draftwright::lint::check(&a, &[])
            .iter()
            .filter(|f| f.code == "interior_void")
            .count(),
        1
    );
}
