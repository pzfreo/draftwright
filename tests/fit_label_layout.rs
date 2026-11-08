//! Full fit/tolerance ink must keep readable paper around the dimension stack.
use std::io::Read as _;
use std::path::Path;

use draftwright::dims::{Placed, label_ink};
use draftwright::model::{self, Plan, Spec};
use draftwright::text::Font;

fn gz(name: &str, compressed: &[u8]) -> model::Analysis {
    let mut bytes = Vec::new();
    flate2::read::GzDecoder::new(compressed)
        .read_to_end(&mut bytes)
        .unwrap();
    model::analyse_native_step(Path::new(name), &bytes)
        .unwrap()
        .1
}

fn spindle() -> model::Analysis {
    gz(
        "turned_spindle_fits.step",
        include_bytes!("fixtures/samples/turned_spindle_fits.step.gz"),
    )
}

fn crosses([a, z]: [[f64; 2]; 2], b: [f64; 4]) -> bool {
    let (mut lo, mut hi) = (0.0_f64, 1.0_f64);
    for k in 0..2 {
        let d = z[k] - a[k];
        if d.abs() < 1e-12 {
            if a[k] <= b[k] || a[k] >= b[k + 2] {
                return false;
            }
        } else {
            let (x, y) = ((b[k] - a[k]) / d, (b[k + 2] - a[k]) / d);
            lo = lo.max(x.min(y));
            hi = hi.min(x.max(y));
        }
    }
    hi - lo > 1e-9
}

fn strokes(p: &Placed) -> Vec<[[f64; 2]; 2]> {
    let mut out = p.lines.clone();
    for &[tip, tail] in &p.arrows {
        let n = [tail[1] - tip[1], tip[0] - tail[0]];
        let k = (2.7 / 6.0) / n[0].hypot(n[1]);
        let a = [tail[0] + k * n[0], tail[1] + k * n[1]];
        let b = [tail[0] - k * n[0], tail[1] - k * n[1]];
        out.extend([[tip, a], [a, b], [b, tip]]);
    }
    out
}

fn clear_label(plan: &Plan, id: &str, text: &str) {
    let d = plan.dimensions.iter().find(|d| d.id == id).unwrap();
    assert_eq!(d.label, text);
    let p = d.placed.as_ref().unwrap();
    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    let ink = label_ink(&d.terms, &font);
    assert!(
        ((p.label_box[2] - p.label_box[0]) - (ink.max[0] - ink.min[0])).abs() < 1e-9,
        "the reservation covers the full drawn label: {text}"
    );
    let b = p.label_box;
    let padded = [b[0] - 2.0, b[1] - 2.0, b[2] + 2.0, b[3] + 2.0];
    for q in plan
        .dimensions
        .iter()
        .filter(|q| q.id != d.id && q.kind == "linear")
    {
        if let Some(p) = &q.placed {
            assert!(
                !strokes(p).into_iter().any(|s| crosses(s, padded)),
                "{text} crowds the lines/arrows of {}",
                q.label
            );
        }
    }
}

#[test]
fn editor_spindle_g6_label_clears_dimension_lines_and_arrows() {
    let a = spindle();
    let default = model::plan_recovered(&a, &Spec::default()).unwrap();
    clear_label(&default, "d:f:turned_step:f2/step.diameter", "ø10 g6");
    let mut spec = Spec::default();
    spec.sheet.fit_deviations = Some(true);
    let plan = model::plan_recovered(&a, &spec).unwrap();
    // Compact manufacturing text now retains a clean, legible A4 before enlargement.
    assert_eq!(plan.sheet.size, "A4");
    assert_eq!(plan.sheet.scale, "1:1");
    clear_label(
        &plan,
        "d:f:turned_step:f2/step.diameter",
        "ø10 g6 -0.005\n-0.014",
    );
    let h6 = plan
        .dimensions
        .iter()
        .find(|d| d.label == "ø15 h6 0\n-0.011")
        .unwrap();
    let p = h6.placed.as_ref().unwrap();
    let [tip, elbow] = p.lines[0];
    assert!(
        (elbow[0] - tip[0]).hypot(elbow[1] - tip[1]) < 25.0,
        "h6 should leave the journal nearby, rather than follow the knurled base"
    );
}

#[path = "common/corpus.rs"]
mod corpus;

#[test]
fn crowded_h7_hole_label_clears_the_dimension_stack() {
    let Some(dir) = corpus::dir() else { return };
    let a = model::analyse_native(&dir.join("nist/nist_ctc_04_asme1_rd.stp"))
        .unwrap()
        .1;
    let spec: Spec =
        serde_json::from_str(include_str!("fixtures/layout/crowded_h7.spec.json")).unwrap();
    let plan = model::plan_recovered(&a, &spec).unwrap();
    clear_label(
        &plan,
        "d:f:hole_pattern:f5/bore.diameter",
        "3× ⌀14 H7 +0.018\n0 THRU",
    );
}

#[test]
fn asymmetric_band_label_clears_the_dimension_stack() {
    let Some(dir) = corpus::dir() else { return };
    let a = model::analyse_native(&dir.join("nist/nist_ctc_01_asme1_rd.stp"))
        .unwrap()
        .1;
    let spec: Spec = serde_json::from_value(serde_json::json!({
        "dimensions": { "d:f:hole_pattern:f77/bore.diameter": { "tolerance": "+0.03 -0.0" } }
    }))
    .unwrap();
    let plan = model::plan_recovered(&a, &spec).unwrap();
    clear_label(
        &plan,
        "d:f:hole_pattern:f77/bore.diameter",
        "4× ⌀35 +0.03 0 THRU",
    );
    let mut on = spec.clone();
    on.sheet.fit_deviations = Some(true);
    let with_deviations = model::plan_recovered(&a, &on).unwrap();
    let label = |p: &Plan| {
        p.dimensions
            .iter()
            .find(|d| d.id == "d:f:hole_pattern:f77/bore.diameter")
            .unwrap()
            .label
            .clone()
    };
    assert_eq!(
        label(&plan),
        label(&with_deviations),
        "fit display leaves explicit bands unchanged"
    );
}

#[test]
fn intent_spool_fit_callouts_clear_late_gdt_without_losing_scale() {
    let a = gz(
        "hw_spool_fits_intent.step",
        include_bytes!("fixtures/pmi/step/hw_spool_fits_intent.step.gz"),
    );
    for enabled in [false, true] {
        let mut spec = Spec::default();
        spec.sheet.fit_deviations = Some(enabled);
        let plan = model::plan_recovered(&a, &spec).unwrap();
        assert_eq!(
            (plan.sheet.size.as_str(), plan.sheet.scale.as_str()),
            ("A3", "1:2"),
            "{}",
            plan.sheet.basis
        );
        assert!(
            !plan.problems.iter().any(|p| matches!(
                p.code.as_str(),
                "annotation_ink_overlap" | "annotation_overlap"
            )),
            "{:?}",
            plan.problems
        );
        let od = plan
            .dimensions
            .iter()
            .find(|d| d.label.starts_with("ø70 f7"))
            .unwrap();
        assert!(od.placed.is_some());
        assert!(
            od.placed.as_ref().unwrap().label_box[0] >= 12.0 - 1e-9,
            "the fit label needs paper between its ink and the sheet border"
        );
        // v0.2.3 (#127): six residual cylindrical scallops share the two
        // existing R10 rounds; all eight must remain stated through fit layout.
        assert!(
            plan.dimensions
                .iter()
                .any(|d| d.label == "8× R10" && d.placed.is_some())
        );
    }
}

#[test]
fn stacked_fit_deviations_reserve_the_full_smaller_glyphs_and_searchable_text() {
    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    let stack = label_ink(&["ø10".into(), "g6 -0.005\n-0.014".into()], &font);
    let bare = label_ink(&["ø10".into(), "g6".into()], &font);
    let flat = label_ink(&["ø10".into(), "g6 -0.005 -0.014".into()], &font);
    assert!(stack.max[0] > bare.max[0]);
    assert!(stack.max[0] < flat.max[0]);
    assert!(stack.max[1] - stack.min[1] > bare.max[1] - bare.min[1]);
    let a = spindle();
    let spec: Spec =
        serde_json::from_value(serde_json::json!({"sheet":{"fit_deviations":true}})).unwrap();
    let plan = model::plan_recovered(&a, &spec).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.label.starts_with("ø10 g6"))
        .unwrap();
    let rendered = model::render_item(model::AnnotationItem::Dimension(d));
    assert_eq!(rendered.text[0].text, d.label);
    assert!(rendered.text[0].text.contains("-0.005\n-0.014"));
    // A bare editor/MCP pin must use the same display recipe when rebuilding its canonical
    // view: otherwise the shorter default shelf shifts the enabled stack off the sheet.
    let mut pinned = spec.clone();
    pinned
        .dimensions
        .entry(d.id.clone())
        .or_default()
        .place
        .get_or_insert_with(Default::default)
        .pinned = true;
    let kept = model::plan_recovered(&a, &pinned).unwrap();
    let same = kept.dimensions.iter().find(|e| e.id == d.id).unwrap();
    assert_eq!(same.label, d.label);
    assert_eq!(same.placed, d.placed);
    assert!(
        !kept
            .problems
            .iter()
            .any(|p| p.code == "annotation_off_sheet" && p.subjects.contains(&d.id))
    );

    // Both explicit false and the default keep only the class; the new setting wins the alias.
    let off: Spec = serde_json::from_value(
        serde_json::json!({"sheet":{"fit_deviations":false,"fit_limits":true}}),
    )
    .unwrap();
    assert!(
        model::plan_recovered(&a, &off)
            .unwrap()
            .dimensions
            .iter()
            .any(|d| d.label == "ø10 g6")
    );
}

#[test]
fn rendered_dimension_arrowheads_cannot_hide_a_label_overlap() {
    let a = spindle();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let mut arrow = plan
        .dimensions
        .iter()
        .find(|d| d.kind == "linear")
        .unwrap()
        .clone();
    let mut label = plan
        .dimensions
        .iter()
        .find(|d| d.label == "ø10 g6")
        .unwrap()
        .clone();
    let p = arrow.placed.as_mut().unwrap();
    p.lines = vec![[[50.0, 50.0], [50.0, 80.0]]];
    p.arrows = vec![[[50.0, 50.0], [50.0, 52.7]]];
    p.label_box = [20.0, 20.0, 25.0, 23.0];
    let font =
        Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap();
    let ink = label_ink(&label.terms, &font);
    let lp = label.placed.as_mut().unwrap();
    lp.label_at = [50.3 - ink.min[0], 51.0 - ink.min[1]];
    lp.rotation = 0.0;
    lp.label_box = [
        50.3,
        51.0,
        50.3 + ink.max[0] - ink.min[0],
        51.0 + ink.max[1] - ink.min[1],
    ];
    let drawn_arrow = model::render_item(model::AnnotationItem::Dimension(&arrow));
    let drawn_label = model::render_item(model::AnnotationItem::Dimension(&label));
    let mut drawing = draftwright::output::Drawing {
        page: [297.0, 210.0],
        layers: Vec::new(),
        link: None,
        text: Vec::new(),
        annotations: Vec::new(),
        sheet: Default::default(),
        problems: Vec::new(),
        spec: None,
    };
    drawing.sheet.items.extend(drawn_arrow.drawn);
    drawing.sheet.items.extend(drawn_label.drawn);
    let found = draftwright::lint::structural::check(&drawing);
    assert!(
        found.iter().any(|i| i.code == "annotation_ink_overlap"
            && i.annotation.as_deref() == Some(label.id.as_str())
            && i.related.contains(&arrow.id)),
        "{found:#?}"
    );
}
