//! Lint's structural checks of the drawn sheet (`lint::structural`, draftwright's `lint_drawing`):
//!
//! - **Each rule at its threshold**, on sheets built through the public types: firing just past
//!   it and not at it.
//! - **The port's own drawings** of the captured corpus parts (as `tests/lint.rs` draws them:
//!   the corpus's, with their features from `tests/fixtures/recognition/corpus/`, under the
//!   default spec): every finding, counted by part and code, is listed in
//!   `tests/fixtures/known_structural.json` with a verdict and the evidence. A new, changed or
//!   vanished entry fails. The placement problems of `tests/fixtures/known_placement.json` judge
//!   the same collisions from the plan (`annotation_overlap` there is any overlap of two
//!   dimensions' label boxes, `annotation_off_sheet` any of a dimension's label corners or line
//!   ends off the drawable area, `annotation_ink_overlap` a dimension's lines through another's
//!   label, `annotation_over_view` a label over a view's line-work); the reasons here
//!   cross-reference them, and a finding between two dimensions must be among the plan's. Set
//!   `LINT_REPORT=1` to print each finding, and `LINT_OWN_ALL=1` to draw connector109 too.
//! - **What draftwright's lint read** (`tests/fixtures/sheet_lint/`, written by
//!   `tools/capture_sheet_lint.py` from draftwright's own sheets of the same parts): the
//!   arguments of its one `lint_drawing` call, each item's boxes, line-work, elbow and riders,
//!   each view's visible edges, the title block's fields, and what the call found. The checks run
//!   on that record and must give exactly its findings in the ported codes, by the names they
//!   are about (by message where draftwright names none) and severity, and with
//!   `annotation_overlap`'s line-work remedy where draftwright gives one.
//! - **Python's captured sheets** (`tests/fixtures/sheets/`): the checks run on what the facts
//!   record and are compared with draftwright's `lint` list, code by code. The facts carry each
//!   view's box (visible and hidden lines, where draftwright's lint boxes the visible lines
//!   alone, so the facts' box holds lint's), each label's text box, and the page; they carry no
//!   annotation's whole ink and no title block box. So `view_overlap`, `view_out_of_bounds` and
//!   `derived_view_identifier_reused` are compared whole (a finding on the facts' larger boxes
//!   that draftwright did not report would fail; none does, so lint's boxes give none either);
//!   `annotation_overlap` is compared over the pairs of labelled annotations, which is every pair
//!   draftwright compares but those with a title block, projection symbol, section wing or
//!   arrowhead (compared by whole ink); `annotation_out_of_bounds` over the labels alone (a label
//!   off the page is ink off the page, so the port's findings must be among draftwright's); and
//!   `scale_not_stated` is not run on the facts (no title block box to give the sheet one), but
//!   every captured sheet records draftwright's `scale_note` and no finding, as the rule says.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use draftwright::lint::{self, LintIssue, Severity, structural};
use draftwright::model;
use draftwright::output::{Curve, Drawing, Drawn, DrawnField, DrawnView, Kind, Sheet};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Known {
    part: String,
    code: String,
    count: usize,
    /// `rust-correct`, `rust-wrong`, `equivalent`, `undetermined` or `not-applicable`.
    verdict: String,
    reason: String,
}

const VERDICTS: [&str; 5] = [
    "rust-correct",
    "rust-wrong",
    "equivalent",
    "undetermined",
    "not-applicable",
];

/// The codes this module ports.
const CODES: [&str; 12] = [
    "derived_view_identifier_reused",
    "scale_not_stated",
    "title_field_overflow",
    "leader_line_through_text",
    "annotation_overlap",
    "annotation_ink_overlap",
    "label_centerline_overlap",
    "annotation_out_of_bounds",
    "view_annotation_overlap",
    "view_annotation_inside_extents",
    "view_overlap",
    "view_out_of_bounds",
];

/// Stream K7's codes (`lint::fidelity`, `tests/lint_fidelity.rs`): judged on the port's own
/// drawings here, never against draftwright's captured reads, which record no view transform
/// for the checks to measure with.
const FIDELITY: [&str; 6] = [
    "label_vs_measured",
    "nominal_rounded",
    "angular_label_vs_geometry",
    "angular_geometry_mismatch",
    "angular_support_mismatch",
    "datum_leader_remote",
];

fn fixture(path: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

/// An A4 sheet (drawable 10..287 × 10..200) with *views* and *items* and nothing else drawn.
fn sheet(views: &[(&str, [f64; 4])], items: Vec<Drawn>) -> Drawing {
    Drawing {
        page: [297.0, 210.0],
        layers: Vec::new(),
        link: None,
        text: Vec::new(),
        annotations: Vec::new(),
        sheet: Sheet {
            dxf: Default::default(),
            units: Default::default(),
            characteristics: Vec::new(),
            inspection_enabled: false,
            label_text: Default::default(),
            precision: Default::default(),
            references: Vec::new(),
            centre_marks: BTreeMap::new(),
            frames: BTreeMap::new(),
            drawable: Some([10.0, 10.0, 287.0, 200.0]),
            views: views
                .iter()
                .map(|(name, bbox)| DrawnView {
                    name: name.to_string(),
                    bbox: *bbox,
                    edges: Vec::new(),
                })
                .collect(),
            items,
            fields: Vec::new(),
        },
        problems: Vec::new(),
        spec: None,
    }
}

fn item(name: &str, kind: Kind, ink: [f64; 4], label: Option<[f64; 4]>) -> Drawn {
    Drawn {
        datum_triangle: None,
        heads: Vec::new(),
        name: name.into(),
        kind,
        ink,
        label,
        lines: Vec::new(),
        elbow: None,
        interior: false,
    }
}

/// A label whose ink is its text.
fn label(name: &str, b: [f64; 4]) -> Drawn {
    item(name, Kind::Dimension, b, Some(b))
}

fn codes(issues: &[LintIssue]) -> Vec<&str> {
    issues.iter().map(|i| i.code.as_str()).collect()
}

/// A title block and a scale note, well inside the sheet and apart.
fn furniture() -> Vec<Drawn> {
    vec![
        item(
            "title_block",
            Kind::TitleBlock,
            [107.0, 11.0, 286.0, 40.0],
            None,
        ),
        item(
            "scale_note",
            Kind::ScaleNote,
            [260.0, 46.0, 272.0, 48.2],
            Some([260.0, 46.0, 272.0, 48.2]),
        ),
    ]
}

#[test]
fn a_finished_sheet_states_its_scale() {
    let mut items = furniture();
    assert!(structural::check(&sheet(&[], items.clone())).is_empty());
    // The title block without the scale note: an error, also when other text says SCALE (only
    // the scale note states it).
    items[1].kind = Kind::Dimension;
    let found = structural::check(&sheet(&[], items.clone()));
    assert_eq!(codes(&found), ["scale_not_stated"]);
    let found = structural::check(&sheet(&[], items[..1].to_vec()));
    assert_eq!(codes(&found), ["scale_not_stated"]);
    assert_eq!(found[0].severity, Severity::Error);
    // Neither: a fragment, with nothing to state a scale about.
    assert!(structural::check(&sheet(&[], Vec::new())).is_empty());
}

#[test]
fn views_overlap_when_their_boxes_do() {
    let front = [20.0, 20.0, 100.0, 80.0];
    let touching = [100.0, 20.0, 150.0, 80.0];
    assert!(
        structural::check(&sheet(
            &[("v:front", front), ("v:side", touching)],
            Vec::new()
        ))
        .is_empty()
    );
    let into = [99.999, 20.0, 150.0, 80.0];
    let found = structural::check(&sheet(&[("v:front", front), ("v:side", into)], Vec::new()));
    assert_eq!(codes(&found), ["view_overlap"]);
    assert_eq!(found[0].severity, Severity::Warning);
    assert_eq!(found[0].view.as_deref(), Some("v:front"));
    assert_eq!(found[0].related, ["v:side"]);
    assert!(
        found[0]
            .message
            .starts_with("view 'front' bbox [x=20.0–100.0"),
        "{}",
        found[0].message
    );
}

#[test]
fn a_view_stays_inside_the_drawable_area() {
    let at_edge = [10.0 - 0.5e-9, 20.0, 100.0, 200.0];
    assert!(structural::check(&sheet(&[("v:front", at_edge)], Vec::new())).is_empty());
    let past = [10.0 - 2e-9, 20.0, 100.0, 200.5];
    let found = structural::check(&sheet(&[("v:front", past)], Vec::new()));
    assert_eq!(codes(&found), ["view_out_of_bounds", "view_out_of_bounds"]);
    assert!(found.iter().all(|i| i.severity == Severity::Error));
    assert!(
        found[0].message.contains("(left by 0.0 mm)"),
        "{}",
        found[0].message
    );
    assert!(
        found[1].message.contains("(above by 0.5 mm)"),
        "{}",
        found[1].message
    );
}

#[test]
fn every_annotation_stays_inside_the_drawable_area() {
    // A dimension's whole ink counts, not only its label.
    let inside = item(
        "d:a",
        Kind::Dimension,
        [10.0, 50.0, 60.0, 60.0],
        Some([30.0, 55.0, 40.0, 58.0]),
    );
    assert!(structural::check(&sheet(&[], vec![inside.clone()])).is_empty());
    let mut out = inside;
    out.ink[1] = 9.0;
    let found = structural::check(&sheet(&[], vec![out]));
    assert_eq!(codes(&found), ["annotation_out_of_bounds"]);
    assert_eq!(found[0].severity, Severity::Error);
    assert_eq!(found[0].annotation.as_deref(), Some("d:a"));
    assert!(
        found[0].message.contains("(below by 1.0 mm)"),
        "{}",
        found[0].message
    );
    // The title block may straddle the edge by half its border's stroke (0.075 mm), no more.
    let mut items = furniture();
    items[0].ink[2] = 287.075;
    assert!(structural::check(&sheet(&[], items.clone())).is_empty());
    items[0].ink[2] = 287.076;
    assert_eq!(
        codes(&structural::check(&sheet(&[], items))),
        ["annotation_out_of_bounds"]
    );
}

#[test]
fn even_a_small_overlap_of_dimension_text_is_reported() {
    let drawing = sheet(
        &[],
        vec![
            label("d:a", [50.0, 50.0, 60.0, 53.0]),
            label("d:b", [59.8, 50.0, 70.0, 53.0]),
        ],
    );
    let found = structural::check(&drawing);
    assert_eq!(codes(&found), ["annotation_overlap"]);
    assert_eq!(found[0].annotation.as_deref(), Some("d:a"));
    assert_eq!(found[0].related, ["d:b"]);
}

#[test]
fn labels_overlap_beyond_roundoff_both_ways() {
    let a = [50.0, 50.0, 60.0, 53.0];
    let check =
        |b: [f64; 4]| structural::check(&sheet(&[], vec![label("d:a", a), label("d:b", b)]));
    // Even half a millimetre obscures text. Only roundoff at a touching edge is ignored.
    assert!(check([60.0 - 0.5e-9, 50.0, 70.0, 56.0]).is_empty());
    for b in [
        [59.5, 52.5, 70.0, 56.0],
        [59.49, 52.5, 70.0, 56.0],
        [59.5, 52.49, 70.0, 56.0],
    ] {
        assert_eq!(codes(&check(b)), ["annotation_overlap"]);
    }
    let found = check([59.49, 52.49, 70.0, 56.0]);
    assert_eq!(codes(&found), ["annotation_overlap"]);
    assert_eq!(found[0].severity, Severity::Warning);
    assert_eq!(found[0].annotation.as_deref(), Some("d:a"));
    assert_eq!(found[0].related, ["d:b"]);
    assert!(
        found[0].message.contains("overlap by 0.5×0.5 mm"),
        "{}",
        found[0].message
    );
    // The labels' text boxes are compared, not the dimensions' whole ink (witness lines of
    // stacked dimensions overlap by design).
    let stacked = [
        item(
            "d:a",
            Kind::Dimension,
            [20.0, 50.0, 90.0, 70.0],
            Some([50.0, 66.0, 60.0, 69.0]),
        ),
        item(
            "d:b",
            Kind::Dimension,
            [20.0, 50.0, 90.0, 80.0],
            Some([50.0, 76.0, 60.0, 79.0]),
        ),
    ];
    assert!(structural::check(&sheet(&[], stacked.to_vec())).is_empty());
    // A thing with no label takes part by its whole ink, a section's hatch too; a centreline
    // takes none (it is judged by label_centerline_overlap).
    let block = item(
        "title_block",
        Kind::TitleBlock,
        [55.0, 40.0, 120.0, 52.0],
        None,
    );
    let hatch = item("section_hatch", Kind::Hatch, [62.0, 40.0, 80.0, 60.0], None);
    let mut line = item(
        "section_line",
        Kind::Centreline,
        [40.0, 51.4, 61.0, 51.6],
        None,
    );
    line.lines = vec![[[40.0, 51.5], [61.0, 51.5]]];
    let found = structural::check(&sheet(&[], vec![label("d:a", a), block, hatch, line]));
    assert_eq!(
        codes(&found),
        [
            "scale_not_stated",
            "annotation_overlap",
            "label_centerline_overlap",
            "annotation_overlap"
        ]
    );
    assert_eq!(found[1].related, ["title_block"]);
    assert_eq!(found[3].annotation.as_deref(), Some("title_block"));
    assert_eq!(found[3].related, ["section_hatch"]);
}

/// A dimension *name* with its label box *b* and line-work *lines*.
fn drawn(name: &str, b: [f64; 4], lines: &[[[f64; 2]; 2]]) -> Drawn {
    let mut d = label(name, b);
    d.lines = lines.to_vec();
    d.ink = lines.iter().flatten().fold(b, |k, p| {
        [
            k[0].min(p[0]),
            k[1].min(p[1]),
            k[2].max(p[0]),
            k[3].max(p[1]),
        ]
    });
    d
}

#[test]
fn line_work_through_a_label_is_reported_from_half_a_millimetre() {
    let b = [50.0, 50.0, 60.0, 53.0];
    let check = |lines: &[[[f64; 2]; 2]]| {
        structural::check(&sheet(
            &[],
            vec![
                label("d:b", b),
                drawn("d:a", [20.0, 20.0, 30.0, 23.0], lines),
            ],
        ))
    };
    // A line ending 0.49 mm into the label is a graze; 0.5 mm is reported.
    assert!(check(&[[[55.0, 30.0], [55.0, 50.49]]]).is_empty());
    let found = check(&[[[55.0, 30.0], [55.0, 50.5]]]);
    assert_eq!(codes(&found), ["annotation_ink_overlap"]);
    assert_eq!(found[0].severity, Severity::Warning);
    // The crossed label is the subject, the thing whose line-work crosses it related.
    assert_eq!(found[0].annotation.as_deref(), Some("d:b"));
    assert_eq!(found[0].related, ["d:a"]);
    assert_eq!(found[0].at, Some([55.0, 51.5]));
    assert!(
        found[0]
            .message
            .starts_with("'d:a' draws 0.5 mm of line-work through the label 'd:b'"),
        "{}",
        found[0].message
    );
    // Pieces meeting at an end inside the label are one stroke, 0.3 + 0.3 mm; apart, two
    // grazes of 0.3 mm.
    let joined = [[[55.0, 30.0], [55.0, 50.3]], [[55.0, 50.3], [55.3, 50.3]]];
    assert_eq!(codes(&check(&joined)), ["annotation_ink_overlap"]);
    let apart = [[[55.0, 30.0], [55.0, 50.3]], [[57.0, 50.3], [57.3, 50.3]]];
    assert!(check(&apart).is_empty());
    // A stroke clean through the label: its height, both ways round when each crosses the other.
    let mut c = drawn(
        "d:c",
        [56.0, 40.0, 62.0, 43.0],
        &[[[51.0, 45.0], [51.0, 51.0]]],
    );
    let mut d = label("d:b", b);
    d.lines = vec![[[58.0, 30.0], [58.0, 46.0]]];
    d.ink = [50.0, 30.0, 60.0, 53.0];
    c.ink = [51.0, 40.0, 62.0, 51.0];
    let found = structural::check(&sheet(&[], vec![d, c]));
    assert_eq!(
        found.iter().map(|i| i.id.as_str()).collect::<Vec<_>>(),
        [
            "l:annotation_ink_overlap:d:c+d:b",
            "l:annotation_ink_overlap:d:b+d:c"
        ]
    );
    assert!(
        found[0].message.contains("draws 3.0 mm"),
        "{}",
        found[0].message
    );
    assert!(
        found[1].message.contains("draws 1.0 mm"),
        "{}",
        found[1].message
    );
    // A thing with no label cannot be crossed (its own ink is no text).
    let mut block = item("title_block", Kind::TitleBlock, b, None);
    block.lines = vec![[[50.0, 50.0], [60.0, 50.0]]];
    let found = structural::check(&sheet(
        &[],
        vec![
            block,
            drawn(
                "d:a",
                [20.0, 20.0, 30.0, 23.0],
                &[[[55.0, 30.0], [55.0, 60.0]]],
            ),
        ],
    ));
    assert_eq!(codes(&found), ["scale_not_stated"]);
}

#[test]
fn overlapping_labels_name_the_line_work_that_also_crosses() {
    let a = [50.0, 50.0, 60.0, 53.0];
    let b = [58.0, 51.0, 70.0, 54.0];
    let found = structural::check(&sheet(&[], vec![label("d:a", a), label("d:b", b)]));
    assert_eq!(codes(&found), ["annotation_overlap"]);
    assert_eq!(
        found[0].message,
        "labels 'd:a' and 'd:b' overlap by 2.0×2.0 mm"
    );
    // d:b's leader also runs 2 mm through d:a's text: one finding for the pair, which says so.
    let crossing = drawn("d:b", b, &[[[52.0, 40.0], [52.0, 52.0]]]);
    let found = structural::check(&sheet(&[], vec![label("d:a", a), crossing]));
    assert_eq!(codes(&found), ["annotation_overlap"]);
    assert_eq!(
        found[0].message,
        "labels 'd:a' and 'd:b' overlap by 2.0×2.0 mm — 'd:b' also draws 2.0 mm of line-work \
         through the label 'd:a', so separating the text is not enough"
    );
    assert_eq!(
        found[0].fix.as_deref(),
        Some("move what is drawn, not just the text")
    );
    // Under half a millimetre of line-work is no crossing.
    let grazing = drawn("d:b", b, &[[[52.0, 40.0], [52.0, 50.49]]]);
    let found = structural::check(&sheet(&[], vec![label("d:a", a), grazing]));
    assert_eq!(
        found[0].message,
        "labels 'd:a' and 'd:b' overlap by 2.0×2.0 mm"
    );
}

#[test]
fn a_leader_does_not_turn_inside_its_own_label() {
    let b = [50.0, 50.0, 60.0, 53.0];
    let callout = |elbow: [f64; 2]| {
        let mut d = drawn(
            "d:h",
            b,
            &[[[40.0, 40.0], elbow], [elbow, [elbow[0] + 3.0, elbow[1]]]],
        );
        d.elbow = Some(elbow);
        structural::check(&sheet(&[], vec![d]))
    };
    assert!(callout([49.999, 51.5]).is_empty());
    let found = callout([50.0, 51.5]);
    assert_eq!(codes(&found), ["leader_line_through_text"]);
    assert_eq!(found[0].severity, Severity::Error);
    assert_eq!(found[0].annotation.as_deref(), Some("d:h"));
    assert_eq!(
        found[0].message,
        "Leader 'd:h': elbow point (50.00, 51.50) is inside the label bbox — leader line passes \
         through the text"
    );
    assert_eq!(codes(&callout([55.0, 53.0])), ["leader_line_through_text"]);
    assert!(callout([55.0, 53.001]).is_empty());
}

#[test]
fn a_datum_shaft_meets_its_frame_but_does_not_cross_its_letter() {
    use draftwright::dims::gdt::{GdtPlaced, GdtText, PlannedGdt};

    let letter = GdtText {
        text: "A".into(),
        at: [53.0, 53.0],
        size: 3.0,
    };
    let mut datum = PlannedGdt {
        from_spec: Vec::new(),
        id: "pmi:datum.datum:f0:t=A".into(),
        kind: "datum_feature".into(),
        view: "v:front".into(),
        side: "left".into(),
        source: "#10".into(),
        faces: vec![0],
        cells: vec!["A".into()],
        modifiers: Vec::new(),
        label: "A".into(),
        default: false,
        basis: "unit test attachment".into(),
        state: draftwright::items::State::Shown,
        placed: Some(GdtPlaced {
            tip: [40.0, 53.0],
            lines: vec![[[42.0, 53.0], [50.0, 53.0]]],
            arrows: Vec::new(),
            label_box: [50.0, 50.0, 56.0, 56.0],
            strokes: vec![
                [[50.0, 50.0], [56.0, 50.0]],
                [[56.0, 50.0], [56.0, 56.0]],
                [[56.0, 56.0], [50.0, 56.0]],
                [[50.0, 56.0], [50.0, 50.0]],
            ],
            frame_strokes: usize::MAX,
            arcs: Vec::new(),
            fills: vec![vec![[40.0, 51.0], [42.0, 53.0], [40.0, 55.0]]],
            letters: vec![letter.clone()],
            runs: vec![letter],
        }),
    };
    let check = |datum: &PlannedGdt| {
        let rendered = model::render_item(model::AnnotationItem::Gdt(datum));
        assert_eq!(rendered.drawn.len(), 1);
        assert_eq!(rendered.drawn[0].elbow, None, "a datum has no callout bend");
        let mut drawing = sheet(&[], rendered.drawn);
        drawing.text = rendered.text;
        drawing.annotations = rendered.annotations;
        structural::check(&drawing)
    };
    assert!(
        check(&datum).is_empty(),
        "the shaft ends on the frame border"
    );
    // A frame-edge stroke is legal too; its midpoint is not inside the frame.
    datum
        .placed
        .as_mut()
        .unwrap()
        .lines
        .push([[50.0, 50.0], [50.0, 56.0]]);
    assert!(check(&datum).is_empty());
    for end in [[53.0, 53.0], [60.0, 53.0]] {
        // Catch both a shaft ending in the letter and one passing all the way through,
        // even though neither has a callout elbow in its drawing record.
        datum.placed.as_mut().unwrap().lines = vec![[[42.0, 53.0], end]];
        let found = check(&datum);
        assert_eq!(codes(&found), ["leader_line_through_text"]);
        assert_eq!(found[0].severity, Severity::Error);
        assert_eq!(found[0].annotation.as_deref(), Some(datum.id.as_str()));
    }
}

#[test]
fn a_label_clears_a_centreline_by_half_a_millimetre() {
    let b = [50.0, 50.0, 60.0, 53.0];
    let check = |line: [[f64; 2]; 2]| {
        let mut cl = item("section_line", Kind::Centreline, [0.0; 4], None);
        cl.ink = [
            line[0][0].min(line[1][0]),
            line[0][1].min(line[1][1]),
            line[0][0].max(line[1][0]),
            line[0][1].max(line[1][1]),
        ];
        cl.lines = vec![line];
        structural::check(&sheet(&[], vec![label("d:a", b), cl]))
    };
    // A line along the page is measured across by how deep it runs in: 0.5 mm is clear.
    assert!(check([[40.0, 50.5], [70.0, 50.5]]).is_empty());
    let found = check([[40.0, 50.51], [70.0, 50.51]]);
    assert_eq!(codes(&found), ["label_centerline_overlap"]);
    assert_eq!(found[0].severity, Severity::Warning);
    assert_eq!(found[0].annotation.as_deref(), Some("d:a"));
    assert_eq!(found[0].related, ["section_line"]);
    assert_eq!(
        found[0].message,
        "label 'd:a' overlaps centerline by 10.0×0.5 mm"
    );
    // And along it by how far it runs inside: 0.5 mm is clear.
    assert!(check([[49.5, 51.5], [50.5, 51.5]]).is_empty());
    assert_eq!(
        codes(&check([[49.5, 51.5], [50.51, 51.5]])),
        ["label_centerline_overlap"]
    );
    // Across the page, the same.
    assert!(check([[59.5, 40.0], [59.5, 60.0]]).is_empty());
    assert_eq!(
        codes(&check([[59.49, 40.0], [59.49, 60.0]])),
        ["label_centerline_overlap"]
    );
}

#[test]
fn a_label_over_a_view_is_judged_by_its_edges() {
    let front = [20.0, 20.0, 100.0, 80.0];
    let view = |edges: Vec<Curve>| DrawnView {
        name: "v:front".into(),
        bbox: front,
        edges,
    };
    let rim = Curve::Conic {
        centre: [60.0, 50.0],
        radii: [20.0, 20.0],
        x_axis: [1.0, 0.0],
        y_axis: [0.0, 1.0],
        t0: 0.0,
        t1: std::f64::consts::TAU,
    };
    let check = |b: [f64; 4], edges: Vec<Curve>, interior: bool| {
        let mut drawing = sheet(&[], Vec::new());
        drawing.sheet.views = vec![view(edges)];
        let mut d = label("d:a", b);
        d.interior = interior;
        drawing.sheet.items = vec![d];
        structural::check(&drawing)
    };
    let outline = vec![Curve::Line([20.0, 20.0], [100.0, 20.0])];
    // Touching the view's box is not over it.
    assert!(check([100.0, 50.0, 110.0, 53.0], outline.clone(), false).is_empty());
    // Over its box but clear of its edges: blank face, noted.
    let found = check([55.0, 48.0, 65.0, 51.0], outline.clone(), false);
    assert_eq!(codes(&found), ["view_annotation_inside_extents"]);
    assert_eq!(found[0].severity, Severity::Info);
    assert_eq!(
        found[0].message,
        "label of annotation 'd:a' lies inside view 'front' extents [x=20.0–100.0, y=20.0–80.0] \
         over a blank region — legitimate for callouts on large faces"
    );
    // Unless it was placed inside on purpose.
    assert!(check([55.0, 48.0, 65.0, 51.0], outline.clone(), true).is_empty());
    // A straight edge through the label crosses it, one along its edge does not (draftwright
    // first compares the edge's box with the label's, a touch not counting).
    assert!(check([55.0, 20.0, 65.0, 23.0], outline.clone(), true).is_empty());
    let found = check([55.0, 19.0, 65.0, 22.0], outline.clone(), true);
    assert_eq!(codes(&found), ["view_annotation_overlap"]);
    assert_eq!(found[0].severity, Severity::Warning);
    assert_eq!(found[0].view.as_deref(), Some("v:front"));
    assert_eq!(
        found[0].message,
        "view 'front' line-work overlaps label of annotation 'd:a' — increase view spacing or \
         move the annotation"
    );
    // A curved edge by its points about a millimetre apart: the rim's top passes through a
    // label straddling y = 70, not one just under it.
    assert_eq!(
        codes(&check([55.0, 69.0, 65.0, 72.0], vec![rim.clone()], false)),
        ["view_annotation_overlap"]
    );
    assert_eq!(
        codes(&check([55.0, 66.0, 65.0, 69.0], vec![rim], false)),
        ["view_annotation_inside_extents"]
    );
}

#[test]
fn a_title_field_fits_its_cell() {
    let check = |w: f64| {
        let mut drawing = sheet(&[], furniture());
        drawing.sheet.fields = vec![DrawnField {
            field: "title".into(),
            cell: [107.0, 32.0, 212.0, 40.0],
            ink: [110.0, 35.0, 110.0 + w, 37.2],
        }];
        structural::check(&drawing)
    };
    assert!(check(105.0).is_empty());
    let found = check(105.0 + 2e-9);
    assert_eq!(codes(&found), ["title_field_overflow"]);
    assert_eq!(found[0].severity, Severity::Warning);
    assert_eq!(found[0].annotation.as_deref(), Some("title_block"));
    assert_eq!(
        found[0].message,
        "Title-block field 'title' overflows its cell: text 105.00 × 2.20 mm, cell 105.00 × 8.00 \
         mm. Use a concise field value and retain the full information in a notes block; text has \
         not been shortened."
    );
}

#[test]
fn one_identifier_names_one_derived_view() {
    let b = |x: f64| [x, 20.0, x + 10.0, 30.0];
    let views = [
        ("v:front", b(20.0)),
        ("v:section_aa", b(40.0)),
        ("v:detail_b", b(60.0)),
    ];
    assert!(structural::check(&sheet(&views, Vec::new())).is_empty());
    let views = [
        ("v:section_aa", b(40.0)),
        ("v:detail_a", b(60.0)),
        ("v:section_ab", b(80.0)),
    ];
    let found = structural::check(&sheet(&views, Vec::new()));
    assert_eq!(codes(&found), ["derived_view_identifier_reused"]);
    assert_eq!(found[0].severity, Severity::Error);
    assert_eq!(
        found[0].message,
        "derived-view identifier 'A' names multiple views: 'section_aa', 'detail_a'"
    );
}

/// The boxes the sheet records are the curves' own: a conic arc and a Bézier bound at their
/// turning points, not their poles.
#[test]
fn a_curves_box_is_tight() {
    let close = |a: [f64; 4], b: [f64; 4]| (0..4).all(|k| (a[k] - b[k]).abs() < 1e-9);
    let arc = |t0: f64, t1: f64| Curve::Conic {
        centre: [1.0, 1.0],
        radii: [2.0, 2.0],
        x_axis: [1.0, 0.0],
        y_axis: [0.0, 1.0],
        t0,
        t1,
    };
    let pi = std::f64::consts::PI;
    assert!(close(arc(0.0, 2.0 * pi).bbox(), [-1.0, -1.0, 3.0, 3.0]));
    assert!(close(
        arc(0.25 * pi, 0.75 * pi).bbox(),
        [1.0 - 2f64.sqrt(), 1.0 + 2f64.sqrt(), 1.0 + 2f64.sqrt(), 3.0]
    ));
    // y = 6t(1-t)(1-2t) turns at ±1/√3; its poles reach ±2.
    let cubic = Curve::Beziers(vec![vec![[0.0, 0.0], [1.0, 2.0], [2.0, -2.0], [3.0, 0.0]]]);
    let r = 1.0 / 3f64.sqrt();
    assert!(close(cubic.bbox(), [0.0, -r, 3.0, r]), "{:?}", cubic.bbox());
    // A rational quarter circle (weights 1, 1/√2, 1).
    let w = std::f64::consts::FRAC_1_SQRT_2;
    let quarter = Curve::Rational(vec![vec![[1.0, 0.0, 1.0], [w, w, w], [0.0, 1.0, 1.0]]]);
    assert!(close(quarter.bbox(), [0.0, 0.0, 1.0, 1.0]));
}

/// The port's own drawings of the captured parts: every structural finding is known.
#[test]
fn structural_findings_on_the_ports_own_drawings_are_known() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let known: Vec<Known> =
        serde_json::from_str(&std::fs::read_to_string(fixture("known_structural.json")).unwrap())
            .unwrap();
    let font = draftwright::text::Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    // As tests/lint.rs: ctc04 has no features, and connector109 takes minutes to draw, so it is
    // drawn only with LINT_OWN_ALL=1 (its entries stay listed, and are checked then).
    let all = std::env::var_os("LINT_OWN_ALL").is_some();
    let names: Vec<String> = facts::names()
        .into_iter()
        .filter(|n| corpus::recognition(n).is_some())
        .filter(|n| all || n != "connector109")
        .collect();
    let sections = std::sync::atomic::AtomicUsize::new(0);
    let linted: Vec<Vec<LintIssue>> = std::thread::scope(|s| {
        let handles: Vec<_> = names
            .iter()
            .map(|name| {
                let (dir, font, sections) = (&dir, &font, &sections);
                s.spawn(move || {
                    let f = facts::load(name);
                    let (part, analysis) = corpus::analyse(dir, &f.binding.file, name);
                    let plan = model::plan(&analysis, &model::Spec::default()).unwrap();
                    let drawing = model::render(&part, &analysis, &plan, font).unwrap();
                    // The furniture the checks rely on is recorded on every finished sheet.
                    let kinds: Vec<Kind> = drawing.sheet.items.iter().map(|d| d.kind).collect();
                    assert!(
                        kinds.contains(&Kind::TitleBlock) && kinds.contains(&Kind::ScaleNote),
                        "{name}"
                    );
                    assert_eq!(
                        kinds.iter().filter(|k| **k == Kind::Dimension).count(),
                        drawing
                            .annotations
                            .iter()
                            .filter(|a| a.satisfies.is_empty()
                                && a.name != "n:f:blend:relief/profiles"
                                && !a.name.starts_with("t:radius-centres/"))
                            .count(),
                        "{name}: every dimension annotation drawn is measured"
                    );
                    for a in drawing
                        .annotations
                        .iter()
                        .filter(|a| a.name == "n:f:blend:relief/profiles")
                    {
                        assert!(
                            !analysis.radius_reliefs.is_empty(),
                            "{name}: profile note needs geometric evidence"
                        );
                        assert!(a.claims.is_empty() && a.satisfies.is_empty());
                        let text = a
                            .runs
                            .iter()
                            .map(|&i| drawing.text[i].text.as_str())
                            .collect::<Vec<_>>()
                            .join(" ");
                        assert_eq!(text, "SHALLOW RELIEF PROFILES PER MODEL");
                        assert!(
                            !text.chars().any(|c| c.is_ascii_digit()),
                            "profile note fabricates no measured number"
                        );
                    }
                    for a in drawing
                        .annotations
                        .iter()
                        .filter(|a| a.name.starts_with("t:radius-centres/"))
                    {
                        assert_eq!(a.claims.len(), 1);
                        assert_eq!(a.runs.len(), 4);
                        assert_eq!(a.claims[0].parameter, "blend.radius");
                        assert!(plan.radius_table.as_ref().is_some_and(|t| {
                            t.rows.iter().any(|r| r.feature == a.claims[0].feature)
                        }));
                        for &run in &a.runs {
                            let [x, y] = drawing.text[run].at;
                            assert!(drawing.sheet.items.iter().any(|d| d.kind == Kind::Table
                                && d.ink[0] <= x
                                && x <= d.ink[2]
                                && d.ink[1] <= y
                                && y <= d.ink[3]));
                        }
                    }
                    for a in drawing.annotations.iter().filter(|a| {
                        !a.satisfies.is_empty() || a.name == "n:f:blend:relief/profiles"
                    }) {
                        assert!(!a.runs.is_empty(), "{name}: {} has no drawn note", a.name);
                        for &run in &a.runs {
                            let [x, y] = drawing.text[run].at;
                            assert!(
                                drawing.sheet.items.iter().any(|d| {
                                    d.kind == Kind::Notes
                                        && d.ink[0] <= x
                                        && x <= d.ink[2]
                                        && d.ink[1] <= y
                                        && y <= d.ink[3]
                                }),
                                "{name}: {} is outside measured note ink",
                                a.name
                            );
                        }
                    }
                    assert_eq!(drawing.sheet.views.len(), plan.views.len(), "{name}");
                    for v in &drawing.sheet.views {
                        assert!(!v.edges.is_empty(), "{name}: {} records its edges", v.name);
                    }
                    // A section's pieces, as draftwright places them: the chain line by its one
                    // line, and at each end a solid arrowhead (2.7 mm long, a third of that
                    // either side of the line) and a wing stub (1.5 arrow lengths).
                    // A half section's hatch has neither (ISO 128-50: its cut is the axis).
                    let piece = |n: &str| drawing.sheet.items.iter().find(|d| d.name == n);
                    if piece("section_hatch").is_some() {
                        let line = piece("section_line").expect("a cutting plane");
                        assert_eq!(line.kind, Kind::Centreline);
                        let [[[x0, y0], [x1, y1]]] = line.lines[..] else {
                            panic!("{name}: {line:?}")
                        };
                        assert!(y0 == y1 && x0 < x1, "{name}: {line:?}");
                        let close = |a: f64, b: f64| (a - b).abs() < 1e-6;
                        for (side, x) in [("left", x0), ("right", x1)] {
                            let head = piece(&format!("section_arrow_{side}")).unwrap().ink;
                            assert!(
                                close(head[3] - head[1], 2.7)
                                    && close(head[2] - head[0], 1.8)
                                    && close(head[3], y0)
                                    && close(head[0] + 0.9, x),
                                "{name}: {head:?}"
                            );
                            let wing = piece(&format!("section_wing_{side}")).unwrap().ink;
                            assert!(
                                close(wing[3] - wing[1], 1.5 * 2.7) && close(wing[3], y0 - 2.7),
                                "{name}: {wing:?}"
                            );
                        }
                        sections.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    }
                    let issues = structural::check(&drawing);
                    // The placement problems judge the same collisions from the plan: a
                    // dimension the plan finds off the sheet (a label corner or a line end) has
                    // ink off it, and two labels whose boxes the plan finds apart cannot overlap.
                    let flagged = |code: &str| -> BTreeSet<String> {
                        issues
                            .iter()
                            .filter(|i| i.code == code)
                            .flat_map(|i| i.annotation.iter().chain(&i.related).cloned())
                            .collect()
                    };
                    let off = flagged("annotation_out_of_bounds");
                    for p in plan
                        .problems
                        .iter()
                        .filter(|p| p.code == "annotation_off_sheet")
                    {
                        assert!(
                            off.contains(&p.subjects[0]),
                            "{name}: {} has no ink off the sheet",
                            p.id
                        );
                    }
                    let placed = |code: &str| -> BTreeSet<String> {
                        plan.problems
                            .iter()
                            .filter(|p| p.code == code)
                            .flat_map(|p| p.subjects.iter().cloned())
                            .collect()
                    };
                    // Likewise line-work through a label, and a label over a view's line-work,
                    // between dimensions: the plan reports each (annotation_ink_overlap,
                    // annotation_over_view).
                    for (code, plan_code) in [
                        ("annotation_overlap", "annotation_overlap"),
                        ("annotation_ink_overlap", "annotation_ink_overlap"),
                    ] {
                        let reported = placed(plan_code);
                        for i in issues.iter().filter(|i| i.code == code) {
                            let pair = i.annotation.iter().chain(&i.related);
                            let dims = pair.clone().filter(|n| n.starts_with("d:")).count();
                            assert!(
                                dims < 2 || pair.into_iter().all(|n| reported.contains(n)),
                                "{name}: {} the plan does not report",
                                i.id
                            );
                        }
                    }
                    let over_view = placed("annotation_over_view");
                    for i in issues
                        .iter()
                        .filter(|i| i.code == "view_annotation_overlap")
                    {
                        let a = i.annotation.as_deref().unwrap();
                        assert!(
                            !a.starts_with("d:") || over_view.contains(a),
                            "{name}: {} the plan does not report",
                            i.id
                        );
                    }
                    issues
                })
            })
            .collect();
        handles.into_iter().map(|h| h.join().unwrap()).collect()
    });
    assert!(sections.into_inner() > 0, "some drawing has a section");
    let mut found: BTreeMap<(String, String), usize> = BTreeMap::new();
    for (name, issues) in names.iter().zip(linted) {
        let ids: BTreeSet<&str> = issues.iter().map(|i| i.id.as_str()).collect();
        assert_eq!(ids.len(), issues.len(), "{name}: finding IDs are unique");
        for issue in &issues {
            assert!(
                CODES.contains(&issue.code.as_str())
                    || FIDELITY.contains(&issue.code.as_str())
                    || matches!(
                        issue.code.as_str(),
                        "leader_centerline_overlap" | "label_section_furniture_overlap"
                    ),
                "{name}: {} {} [{}]",
                issue.code,
                issue.message,
                issue.id
            );
            if std::env::var_os("LINT_REPORT").is_some() {
                eprintln!("{name}: {} {} [{}]", issue.code, issue.message, issue.id);
            }
            *found.entry((name.clone(), issue.code.clone())).or_default() += 1;
        }
    }
    let mut listed: BTreeMap<(String, String), usize> = BTreeMap::new();
    for k in &known {
        assert!(
            VERDICTS.contains(&k.verdict.as_str()) && !k.reason.is_empty(),
            "{k:?}"
        );
        assert!(
            CODES.contains(&k.code.as_str()) || FIDELITY.contains(&k.code.as_str()),
            "{k:?}"
        );
        let old = listed.insert((k.part.clone(), k.code.clone()), k.count);
        assert!(old.is_none(), "{k:?} is listed twice");
    }
    let listed: BTreeMap<(String, String), usize> = listed
        .into_iter()
        .filter(|((part, _), _)| names.contains(part))
        .collect();
    assert_eq!(
        found, listed,
        "structural findings on the port's own drawings (left) differ from known_structural.json (right)"
    );
}

/// What draftwright's lint reports in *codes* on a captured sheet, by code: each finding's
/// annotation and related annotations, or its message when it names none.
fn python(f: &facts::Facts, code: &str) -> Vec<String> {
    let mut out: Vec<String> = f
        .lint
        .iter()
        .filter(|i| i.code == code)
        .map(|i| match &i.annotation {
            Some(a) => std::iter::once(a.clone())
                .chain(i.related.iter().cloned())
                .collect::<Vec<_>>()
                .join("+"),
            None => i.message.clone(),
        })
        .collect();
    out.sort();
    out
}

/// The same for the port's findings, by draftwright's names.
fn port(issues: &[LintIssue], code: &str) -> Vec<String> {
    let mut out: Vec<String> = issues
        .iter()
        .filter(|i| i.code == code)
        .map(|i| match &i.annotation {
            Some(a) => std::iter::once(a.clone())
                .chain(i.related.iter().cloned())
                .collect::<Vec<_>>()
                .join("+"),
            None => i.message.clone(),
        })
        .collect();
    out.sort();
    out
}

/// The checks on what Python's captured sheets record agree with draftwright's lint (see the
/// module's note for what the facts allow).
#[test]
fn structural_checks_on_pythons_sheets_agree_with_pythons_lint() {
    let mut compared = 0;
    for name in facts::names() {
        let f = facts::load(&name);
        let [w, h] = f.sheet.page;
        let labelled = f.annotations.iter().filter_map(|a| {
            let b = a.label_bbox?;
            let kind = if a.name == "scale_note" {
                Kind::ScaleNote
            } else {
                Kind::Dimension
            };
            Some(item(&a.name, kind, b, Some(b)))
        });
        let drawing = Drawing {
            page: f.sheet.page,
            layers: Vec::new(),
            link: None,
            text: Vec::new(),
            annotations: Vec::new(),
            sheet: Sheet {
                dxf: Default::default(),
                units: Default::default(),
                characteristics: Vec::new(),
                inspection_enabled: false,
                label_text: Default::default(),
                precision: Default::default(),
                references: Vec::new(),
                centre_marks: BTreeMap::new(),
                frames: BTreeMap::new(),
                drawable: Some([10.0, 10.0, w - 10.0, h - 10.0]),
                views: f
                    .views
                    .iter()
                    .filter_map(|v| {
                        Some(DrawnView {
                            name: format!("v:{}", v.name),
                            bbox: v.bbox?,
                            edges: Vec::new(),
                        })
                    })
                    .collect(),
                items: labelled.collect(),
                fields: Vec::new(),
            },
            problems: Vec::new(),
            spec: None,
        };
        assert!(
            f.annotations.iter().any(|a| a.name == "scale_note")
                && f.annotations.iter().any(|a| a.kind == "TitleBlock")
                && python(&f, "scale_not_stated").is_empty(),
            "{name}: draftwright states the scale on a finished sheet"
        );
        let issues = structural::check(&drawing);
        for code in [
            "derived_view_identifier_reused",
            "annotation_overlap",
            "view_overlap",
            "view_out_of_bounds",
        ] {
            assert_eq!(port(&issues, code), python(&f, code), "{name}: {code}");
            compared += python(&f, code).len();
        }
        let theirs = python(&f, "annotation_out_of_bounds");
        for p in port(&issues, "annotation_out_of_bounds") {
            assert!(
                theirs.contains(&p),
                "{name}: {p} off the page, which draftwright did not report"
            );
        }
    }
    // ctc04's two labels: '8× ⌀14 THRU' (hc_plan6) and '150.0°' (m_angle_1), 4.7 × 2.7 mm.
    assert_eq!(
        compared, 1,
        "draftwright's findings in these codes on the captured sheets"
    );
}

/// The review reports the structural findings beside coverage (`Review::structural`; draftwright
/// runs them first in one list). `Review::lint` stays coverage alone, as `tests/lint.rs` pins it:
/// the plate's own drawing has nothing off the sheet and coverage finds nothing (its y location
/// ran 0.1 mm past the left border until placement's fall-through, stream d-placement-fit). On
/// A4 at 5:1 (the crowded plate of `tests/issue_drafting.rs`) every annotation the structural
/// check finds past the border the plan reports too (`annotation_off_sheet`). On A4 at 10:1 the
/// plate runs off the sheet.
#[test]
fn the_review_reports_structural_findings_beside_coverage() {
    let step = fixture("parts/plate.step");
    let features = fixture("recognition/plate.json");
    let review = draftwright::api::review(&step, Some(&features), &model::Spec::default()).unwrap();
    assert!(review.lint.is_empty());
    assert!(review.structural.is_empty(), "{:#?}", review.structural);
    assert!(
        review
            .problems
            .iter()
            .all(|p| p.code != "annotation_off_sheet")
    );
    let mut spec = model::Spec::default();
    spec.sheet.size = Some("A4".into());
    spec.sheet.scale = Some("5:1".into());
    let review = draftwright::api::review(&step, Some(&features), &spec).unwrap();
    let off: Vec<&str> = review
        .structural
        .iter()
        .filter(|i| i.code == "annotation_out_of_bounds")
        .map(|i| i.id.as_str())
        .collect();
    assert!(!off.is_empty(), "{:#?}", review.structural);
    for id in off {
        let subject = id
            .strip_prefix("l:annotation_out_of_bounds:")
            .and_then(|s| s.rsplit_once(':'))
            .unwrap()
            .0;
        assert!(
            review
                .problems
                .iter()
                .any(|p| p.id == format!("p:annotation_off_sheet:{subject}")),
            "{id}: the plan reports no annotation_off_sheet for {subject}"
        );
    }
    let mut spec = model::Spec::default();
    spec.sheet.size = Some("A4".into());
    spec.sheet.scale = Some("10:1".into());
    let review = draftwright::api::review(&step, Some(&features), &spec).unwrap();
    let (part, mut analysis) = model::analyse(&step).unwrap();
    model::recognise(
        &part,
        &mut analysis,
        &std::fs::read_to_string(&features).unwrap(),
    )
    .unwrap();
    // The plan the review draws: the named scale is never re-planned, but a hole item placed off
    // the sheet is dropped (`model::plan_escalated`).
    let plan = model::plan_escalated(&analysis, &spec).unwrap();
    let font = draftwright::text::Font::new(
        std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
            .unwrap(),
    )
    .unwrap();
    let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
    let structural = structural::check(&drawing);
    assert!(
        codes(&structural).contains(&"view_out_of_bounds"),
        "{structural:#?}"
    );
    assert_eq!(review.structural, structural);
    assert_eq!(review.lint, lint::lint(&analysis, &drawing));
    assert!(
        lint::lint(&analysis, &drawing)
            .iter()
            .all(|i| !CODES.contains(&i.code.as_str()))
    );
}

/// What draftwright's structural lint read on one sheet, and what it found
/// (`tools/capture_sheet_lint.py`, `tests/fixtures/sheet_lint/`).
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct Captured {
    schema: String,
    file: String,
    page_bbox: Option<[f64; 4]>,
    scale: f64,
    items: Vec<CapturedItem>,
    views: Vec<CapturedView>,
    issues: Vec<CapturedIssue>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CapturedItem {
    name: Option<String>,
    #[serde(rename = "type")]
    kind: String,
    label: String,
    label_bbox: Option<[f64; 4]>,
    label_polygon: Option<Vec<[f64; 2]>>,
    segments: Vec<[[f64; 2]; 2]>,
    ink: Option<[f64; 4]>,
    region: Option<String>,
    elbow: Option<[f64; 2]>,
    tip: Option<[f64; 2]>,
    #[serde(default)]
    dimension_like: bool,
    #[serde(default)]
    is_centerline: bool,
    #[serde(default)]
    is_sheet_frame: bool,
    #[serde(default)]
    is_zone_grid: bool,
    #[serde(default)]
    is_zone_label: bool,
    #[serde(default)]
    is_section_hatch: bool,
    #[serde(default)]
    is_datum_target: bool,
    #[serde(default)]
    is_scale_note: bool,
    centerline_segments: Option<Vec<[[f64; 2]; 2]>>,
    centerline_boxes: Option<Vec<[f64; 4]>>,
    title_fields: Option<Vec<CapturedField>>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CapturedField {
    field: String,
    value: String,
    /// Width and height, mm.
    cell: [f64; 2],
    text: [f64; 2],
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CapturedView {
    name: String,
    bbox: [f64; 4],
    edges: Vec<CapturedEdge>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CapturedEdge {
    line: Option<[[f64; 2]; 2]>,
    points: Option<Vec<[f64; 2]>>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CapturedIssue {
    code: String,
    severity: String,
    message: String,
    annotation: Option<String>,
    related: Vec<String>,
    view: Option<String>,
}

fn captured() -> Vec<(String, Captured)> {
    use std::io::Read as _;
    let dir = fixture("sheet_lint");
    let mut names: Vec<String> = std::fs::read_dir(&dir)
        .unwrap()
        .filter_map(|e| {
            let name = e.unwrap().file_name().to_string_lossy().into_owned();
            name.strip_suffix(".json.gz").map(str::to_string)
        })
        .collect();
    names.sort();
    names
        .into_iter()
        .map(|name| {
            let mut text = String::new();
            flate2::read::GzDecoder::new(
                std::fs::File::open(dir.join(format!("{name}.json.gz"))).unwrap(),
            )
            .read_to_string(&mut text)
            .unwrap();
            let c: Captured = serde_json::from_str(&text).unwrap_or_else(|e| panic!("{name}: {e}"));
            assert_eq!(c.schema, "draftwright.sheet-lint/1", "{name}");
            (name, c)
        })
        .collect()
}

/// A captured sheet as the port's record of what was drawn: each item by its registry name (an
/// unnamed one as `#<index>`), its kind from the riders lint dispatches on, its boxes,
/// line-work and elbow; its label's text as a text run, so messages call it as draftwright does;
/// each view by its box and visible edges, a sampled curve by its sample points (each a line of
/// no length: a point is in a box as a line of no length is); the title block's fields by their
/// measured sizes.
fn captured_drawing(name: &str, c: &Captured) -> Drawing {
    let mut text = Vec::new();
    let mut annotations = Vec::new();
    let mut items = Vec::new();
    let mut fields = Vec::new();
    for (k, i) in c.items.iter().enumerate() {
        // Furniture the port does not draw, and so cannot judge as draftwright does.
        assert!(
            !(i.is_sheet_frame || i.is_zone_grid || i.is_zone_label || i.is_datum_target),
            "{name}: {i:?}"
        );
        let item_name = i.name.clone().unwrap_or_else(|| format!("#{k}"));
        let kind = if i.is_centerline {
            Kind::Centreline
        } else if i.is_section_hatch {
            Kind::Hatch
        } else if i.is_scale_note {
            Kind::ScaleNote
        } else if i.title_fields.is_some() {
            Kind::TitleBlock
        } else {
            Kind::Dimension
        };
        if !i.label.is_empty() {
            annotations.push(draftwright::output::Annotation {
                approved_claims: Vec::new(),
                approved_text: None,
                coordinate_origin: None,
                satisfies: Vec::new(),
                name: item_name.clone(),
                view: None,
                runs: vec![text.len()],
                claims: Vec::new(),
            });
            text.push(draftwright::output::TextRun {
                text: i.label.clone(),
                at: [0.0, 0.0],
                size: 3.0,
                rotation: 0.0,
                font: String::new(),
                h_align: draftwright::output::HAlign::Center,
                v_align: draftwright::output::VAlign::Middle,
            });
        }
        // A centreline's own segments are its line; draftwright's component boxes are not
        // recorded by the port, and none is captured.
        assert!(
            i.centerline_boxes.as_ref().is_none_or(Vec::is_empty),
            "{name}: {item_name}"
        );
        let lines = i
            .centerline_segments
            .clone()
            .unwrap_or_else(|| i.segments.clone());
        let _ = (&i.kind, i.tip, i.dimension_like, &i.label_polygon);
        for f in i.title_fields.iter().flatten() {
            let _ = &f.value;
            fields.push(DrawnField {
                field: f.field.clone(),
                cell: [0.0, 0.0, f.cell[0], f.cell[1]],
                ink: [0.0, 0.0, f.text[0], f.text[1]],
            });
        }
        items.push(Drawn {
            datum_triangle: None,
            heads: Vec::new(),
            name: item_name,
            kind,
            ink: i
                .ink
                .unwrap_or_else(|| panic!("{name}: {i:?} has no ink box")),
            label: i.label_bbox,
            lines,
            elbow: i.elbow,
            interior: i.region.as_deref() == Some("interior"),
        });
    }
    let views = c
        .views
        .iter()
        .map(|v| DrawnView {
            name: format!("v:{}", v.name),
            bbox: v.bbox,
            edges: v
                .edges
                .iter()
                .flat_map(|e| match (e.line, &e.points) {
                    (Some([p, q]), None) => vec![Curve::Line(p, q)],
                    (None, Some(points)) => points.iter().map(|p| Curve::Line(*p, *p)).collect(),
                    _ => panic!("{name}: {e:?}"),
                })
                .collect(),
        })
        .collect();
    Drawing {
        page: [0.0, 0.0],
        layers: Vec::new(),
        link: None,
        text,
        annotations,
        sheet: Sheet {
            dxf: Default::default(),
            units: Default::default(),
            characteristics: Vec::new(),
            inspection_enabled: false,
            label_text: Default::default(),
            precision: Default::default(),
            references: Vec::new(),
            centre_marks: BTreeMap::new(),
            frames: BTreeMap::new(),
            drawable: c.page_bbox,
            views,
            items,
            fields,
        },
        problems: Vec::new(),
        spec: None,
    }
}

/// A finding as both sides can name it: its code, the names of what it is about (an unnamed
/// item left out), and its message where it names nothing, up to any advice the port leaves out.
fn key(code: &str, names: &[&str], message: &str) -> String {
    let named: Vec<&str> = names
        .iter()
        .copied()
        .filter(|n| !n.starts_with('#'))
        .collect();
    if named.is_empty() {
        let kept = if code == "label_centerline_overlap" {
            message.split(" — ").next().unwrap()
        } else {
            message
        };
        format!("{code} {kept}")
    } else {
        format!("{code} {}", named.join("+"))
    }
}

/// The checks on what draftwright's structural lint read on its own sheets give what it found:
/// every finding in the ported codes, with the line-work crossing named in `annotation_overlap`
/// where draftwright names one, and the same severity.
#[test]
fn structural_checks_on_what_draftwright_read_agree_with_what_it_found() {
    let mut compared = BTreeMap::new();
    let mut additional = BTreeMap::new();
    for (name, c) in captured() {
        assert!(c.scale > 0.0 && !c.file.is_empty(), "{name}");
        let drawing = captured_drawing(&name, &c);
        let issues = structural::check(&drawing);
        let mut theirs: Vec<String> = Vec::new();
        let mut ours: Vec<String> = Vec::new();
        for i in c.issues.iter().filter(|i| CODES.contains(&i.code.as_str())) {
            let names: Vec<&str> = i
                .annotation
                .iter()
                .chain(&i.related)
                .map(String::as_str)
                .collect();
            let mut k = key(&i.code, &names, &i.message);
            if i.code == "annotation_overlap"
                && let Some((_, remedy)) = i.message.split_once(" — ")
                && remedy.contains("also draws")
            {
                k += &format!(" ({})", remedy.split(", so").next().unwrap());
            }
            let _ = &i.view;
            theirs.push(format!("{k} [{}]", i.severity));
            *compared.entry(i.code.clone()).or_insert(0) += 1;
        }
        for i in issues.iter().filter(|i| !CODES.contains(&i.code.as_str())) {
            additional
                .entry(name.clone())
                .or_insert_with(Vec::new)
                .push((i.code.clone(), i.annotation.clone(), i.related.clone()));
        }
        for i in issues.iter().filter(|i| CODES.contains(&i.code.as_str())) {
            let names: Vec<&str> = i
                .annotation
                .iter()
                .chain(&i.related)
                .map(String::as_str)
                .collect();
            // draftwright names neither side of a centreline pair, nor what a view's line-work
            // runs through, nor the title block of a field.
            let names = if matches!(
                i.code.as_str(),
                "label_centerline_overlap"
                    | "view_annotation_overlap"
                    | "view_annotation_inside_extents"
                    | "title_field_overflow"
            ) {
                Vec::new()
            } else {
                names
            };
            let mut k = key(&i.code, &names, &i.message);
            if i.code == "annotation_overlap"
                && let Some((_, remedy)) = i.message.split_once(" — ")
            {
                k += &format!(" ({})", remedy.split(", so").next().unwrap());
            }
            let severity = match i.severity {
                Severity::Error => "error",
                Severity::Warning => "warning",
                Severity::Info => "info",
            };
            ours.push(format!("{k} [{severity}]"));
        }
        theirs.sort();
        ours.sort();
        assert_eq!(
            ours, theirs,
            "{name}: the port's findings (left), draftwright's (right)"
        );
    }
    let expected = serde_json::from_str(include_str!("fixtures/g6_python_fixed_ink.json")).unwrap();
    assert_eq!(
        additional, expected,
        "additional fixed-ink findings on captured Python sheets"
    );
    // ftc06's ten crossings, cgb245's and ctc04's one each; ctc04's overlapping pair (its
    // line-work remedy included); hanger's and m10046's labels over blank face.
    assert_eq!(
        compared,
        BTreeMap::from([
            ("annotation_ink_overlap".to_string(), 12),
            ("annotation_overlap".to_string(), 1),
            ("view_annotation_inside_extents".to_string(), 5),
        ]),
        "draftwright's findings in the ported codes on its sheets"
    );
}

#[test]
fn fixed_ink_review_distinguishes_shared_strokes_from_transverse_crossings() {
    let mut fixed = item("axis", Kind::Centreline, [0., 0., 10., 0.], None);
    fixed.lines = vec![[[0., 0.], [10., 0.]]];
    for (line, expected) in [
        ([[2., 0.], [8., 0.]], true),
        ([[5., -3.], [5., 3.]], false),
        ([[11., 0.], [20., 0.]], false),
        ([[9.5, 0.], [12., 0.]], false),
        ([[2., 0.1], [8., 0.1]], false),
    ] {
        let mut leader = item("callout", Kind::Dimension, [0.; 4], None);
        leader.lines = vec![line];
        leader.elbow = Some(line[1]);
        let found = structural::check(&sheet(&[], vec![leader, fixed.clone()]));
        assert_eq!(
            found.iter().any(|i| i.code == "leader_centerline_overlap"),
            expected
        );
    }
}

/// A vertical linear dimension from y *lo* to *hi*, its extension lines from the view's left edge
/// (x 40) out to its dimension line at x *at*, its label *label*.
fn vertical(name: &str, at: f64, lo: f64, hi: f64, label: [f64; 4]) -> Drawn {
    Drawn {
        datum_triangle: None,
        heads: Vec::new(),
        lines: vec![
            [[40.0, lo], [at - 2.0, lo]],
            [[40.0, hi], [at - 2.0, hi]],
            [[at, lo], [at, hi]],
        ],
        ..item(name, Kind::Dimension, [at - 3.0, lo, 40.0, hi], Some(label))
    }
}

/// #32: two linear dimensions on one dimension line over overlapping spans are an error, at
/// half a millimetre apart or less; chained dimensions meeting at an extension line, or one a
/// lane further out, are not. A label more than a text height (3 mm) from its own line is an
/// error, one beside it (1 mm off, as the layout sets it) is not.
#[test]
fn shared_dimension_lines_and_stray_text_are_errors() {
    let beside = |lo: f64, hi: f64, at: f64| {
        [
            at - 3.2,
            0.5 * (lo + hi) - 2.0,
            at - 1.0,
            0.5 * (lo + hi) + 2.0,
        ]
    };
    let found = |items: Vec<Drawn>| {
        structural::check(&sheet(&[], items))
            .into_iter()
            .filter(|i| i.code.starts_with("dimension_"))
            .map(|i| (i.code, i.severity))
            .collect::<Vec<_>>()
    };
    let shared = vec![
        vertical("d:22", 20.0, 90.0, 112.0, beside(90.0, 112.0, 20.0)),
        vertical("d:52", 20.4, 78.0, 130.0, beside(78.0, 130.0, 20.4)),
    ];
    assert_eq!(
        found(shared),
        vec![("dimension_line_shared".to_string(), Severity::Error)]
    );
    for (b_at, b_lo, b_hi) in [
        (20.0, 112.0, 130.0),
        (13.0, 78.0, 130.0),
        (20.6, 78.0, 130.0),
    ] {
        let apart = vec![
            vertical("d:22", 20.0, 90.0, 112.0, beside(90.0, 112.0, 20.0)),
            vertical("d:b", b_at, b_lo, b_hi, beside(b_lo, b_hi, b_at)),
        ];
        assert_eq!(found(apart), vec![], "{b_at} {b_lo}..{b_hi}");
    }
    // The label 3.5 mm past the line's far end (and 1 mm across); then 2.5 mm past it, 2.7 mm off.
    let stray = vertical("d:52", 20.0, 78.0, 130.0, [16.8, 133.5, 19.0, 137.5]);
    assert_eq!(
        found(vec![stray]),
        vec![("dimension_text_stray".to_string(), Severity::Error)]
    );
    let near = vertical("d:52", 20.0, 78.0, 130.0, [16.8, 132.5, 19.0, 136.5]);
    assert_eq!(found(vec![near]), vec![]);
}

#[test]
fn nts_caption_contacts_with_labels_lines_and_views_are_reported() {
    let name = model::NTS_CAPTION_ID;
    let b = [40.0, 40.0, 70.0, 42.0];
    let caption = item(name, Kind::Caption, b, Some(b));
    // Even a shallow retained clash must be visible to the caption's backstop.
    let other = label("callout", [69.9, 41.9, 80.0, 45.0]);
    let mut leader = item(
        "leader",
        Kind::Dimension,
        [69.8, 39.9, 90.0, 42.0],
        Some([80.0, 40.0, 90.0, 42.0]),
    );
    leader.lines.push([[69.8, 39.9], [70.1, 40.2]]);
    for (other, code) in [
        (other, "annotation_overlap"),
        (leader, "annotation_ink_overlap"),
    ] {
        let found = structural::check(&sheet(&[], vec![caption.clone(), other]));
        assert!(
            found.iter().any(|i| i.code == code
                && (i.annotation.as_deref() == Some(name) || i.related.iter().any(|r| r == name))),
            "{code}: {found:?}"
        );
    }
    let mut drawing = sheet(&[("v:side", [30.0, 30.0, 80.0, 50.0])], vec![caption]);
    drawing.sheet.views[0]
        .edges
        .push(Curve::Line([50.0, 30.0], [50.0, 50.0]));
    let found = structural::check(&drawing);
    assert!(
        found
            .iter()
            .any(|i| i.code == "view_annotation_overlap" && i.annotation.as_deref() == Some(name)),
        "{found:?}"
    );
}

#[test]
fn detail_captions_keep_half_a_text_height_clear_across_view_groups() {
    let b = [40.0, 40.0, 70.0, 42.0];
    let caption = item("detail_caption_B", Kind::Caption, b, Some(b));
    for (gap, expected) in [(0.99, true), (1.0, false)] {
        let other = label(
            "other_scale_dimension",
            [70.0 + gap, 40.0, 80.0 + gap, 42.0],
        );
        let found = structural::check(&sheet(&[], vec![caption.clone(), other]));
        assert_eq!(
            found.iter().any(|i| i.code == "detail_caption_clearance"),
            expected,
            "gap {gap}: {found:?}"
        );
    }
}
