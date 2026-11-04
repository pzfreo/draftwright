//! The title block's cells (`src/title_block.rs`) against draftwright's: the grid, the text
//! measured as draftwright measures it, and the clauses of draftwright's
//! `tests/test_issue_1585_title_block_date.py` (ISSUE-1585-a) and
//! `tests/test_issue_1536_title_overflow.py` (ISSUE-1536-a), on the plate fixture where
//! draftwright's tests use a 30 × 20 × 10 box. (The PDF text layer is read back in
//! `tests/output.rs`, which has the PDF reader.)

#[path = "common/local_fix.rs"]
mod local_fix;

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::OnceLock;

use draftwright::haecceity::Part;
use draftwright::model::{self, Analysis, Plan, Problem, Spec};
use draftwright::output::Drawing;
use draftwright::text::Font;
use draftwright::title_block::{self, TitleCell};
use serde_json::json;

fn fixture(path: &str) -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures")
        .join(path)
}

/// The plate fixture with its recognition, read once.
fn plate() -> &'static (Part, Analysis) {
    static PLATE: OnceLock<(Part, Analysis)> = OnceLock::new();
    PLATE.get_or_init(|| {
        let (part, mut a) = model::analyse(&fixture("parts/plate.step")).unwrap();
        let json = std::fs::read_to_string(fixture("recognition/plate.json")).unwrap();
        model::recognise(&part, &mut a, &json).unwrap();
        (part, a)
    })
}

fn font() -> Font {
    Font::open(&Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"))
        .unwrap()
}

/// The plate planned on sheet *size* with title fields *title*, and drawn.
fn sheet(size: &str, title: serde_json::Value) -> (Plan, Drawing) {
    let (part, analysis) = plate();
    let spec: Spec =
        serde_json::from_value(json!({ "sheet": { "size": size, "title": title } })).unwrap();
    let plan = model::plan(analysis, &spec).unwrap();
    let drawing = model::render(part, analysis, &plan, &font()).unwrap();
    (plan, drawing)
}

fn cell<'a>(plan: &'a Plan, field: &str) -> &'a TitleCell {
    plan.sheet
        .title_cells
        .iter()
        .find(|c| c.field == field)
        .unwrap()
}

/// The width and height of *value*'s ink as drawn in *cell*: at its fitted height.
fn drawn(value: &str, cell: [f64; 4]) -> [f64; 2] {
    let laid = title_block::lay(value, title_block::value_size(value, cell));
    [laid.max[0] - laid.min[0], laid.max[1] - laid.min[1]]
}

fn overflows(plan: &Plan) -> Vec<&Problem> {
    plan.problems
        .iter()
        .filter(|p| p.code == "title_field_overflow")
        .collect()
}

#[test]
fn projection_symbol_states_third_angle_and_clears_the_reserved_band() {
    use draftwright::output::{Curve, Item};
    for size in ["A4", "A3"] {
        let (plan, drawing) = sheet(size, json!({}));
        let view = |id: &str| plan.views.iter().find(|v| v.id == id).unwrap();
        assert!(view("v:plan").at[1] > view("v:front").at[1]);
        assert!(view("v:side").at[0] > view("v:front").at[0]);
        let symbol = drawing
            .sheet
            .items
            .iter()
            .find(|i| i.name == "projection_symbol")
            .expect("a finished sheet states its projection convention");
        let block = plan.sheet.title_block;
        let band = [
            block[2] - 3.0 - 16.0,
            title_block::top(block),
            block[2] - 3.0,
            block[3],
        ];
        assert!(symbol.ink[0] > band[0] && symbol.ink[1] > band[1]);
        assert!(symbol.ink[2] <= band[2] && symbol.ink[3] < band[3]);
        let scale = drawing
            .sheet
            .items
            .iter()
            .find(|i| i.name == "scale_note")
            .unwrap();
        assert!(scale.ink[2] < symbol.ink[0]);
        let curves: Vec<_> = drawing
            .layers
            .iter()
            .flat_map(|l| &l.items)
            .filter_map(|i| {
                let Item::Edge(c) = i else { return None };
                let b = c.bbox();
                (b[0] >= symbol.ink[0]
                    && b[1] >= symbol.ink[1]
                    && b[2] <= symbol.ink[2]
                    && b[3] <= symbol.ink[3])
                    .then_some(c)
            })
            .collect();
        let circles: Vec<_> = curves
            .iter()
            .filter_map(|c| match c {
                Curve::Conic {
                    centre,
                    radii,
                    t0,
                    t1,
                    ..
                } => {
                    assert_eq!(radii[0], radii[1]);
                    assert!((t1 - t0 - std::f64::consts::TAU).abs() < 1e-9);
                    Some((*centre, radii[0]))
                }
                _ => None,
            })
            .collect();
        assert_eq!(circles.len(), 2);
        assert_eq!(circles[0].0, circles[1].0);
        assert!((circles[1].1 / circles[0].1 - 0.6).abs() < 1e-9);
        let mut ends: Vec<_> = symbol.lines.iter().filter(|[a, b]| a[0] == b[0]).collect();
        ends.sort_by(|a, b| a[0][0].total_cmp(&b[0][0]));
        assert_eq!(symbol.lines.len(), 5);
        assert_eq!(ends.len(), 2);
        // ISO 5456-2's third-angle figure (#31): the circles left, the cone right of them, its
        // narrow end (the left one) towards them.
        let near = (ends[0][1][1] - ends[0][0][1]).abs();
        let far = (ends[1][1][1] - ends[1][0][1]).abs();
        assert!(
            (near / far - 0.6).abs() < 1e-9,
            "third-angle: narrow end faces circles"
        );
        assert!((far - 2.0 * circles[0].1).abs() < 1e-9);
        assert!((ends[1][0][0] - ends[0][0][0] - 1.3 * far).abs() < 1e-9);
        assert!((ends[0][0][0] - (circles[0].0[0] + circles[0].1) - 0.5 * far).abs() < 1e-9);
        let round_trip: Plan =
            serde_json::from_value(serde_json::to_value(&plan).unwrap()).unwrap();
        let (part, a) = plate();
        assert_eq!(
            drawing.svg(),
            model::render(part, a, &round_trip, &font()).unwrap().svg()
        );
    }
}

#[test]
fn a_blank_general_tolerance_is_flagged_with_an_explicit_acknowledgement_fix() {
    for title in [
        json!({}),
        json!({"tolerance":""}),
        json!({"tolerance":"  "}),
    ] {
        let (plan, _) = sheet("A4", title);
        assert_eq!(cell(&plan, "general_tolerance").value, None);
        let p = plan
            .problems
            .iter()
            .find(|p| p.code == "general_tolerance_missing")
            .expect("a blank GEN. TOL. cell needs the author's attention");
        assert_eq!(p.severity, "warning");
        assert_eq!(p.subjects, ["sheet"]);
        assert!(p.refs.iter().any(|r| r == "sheet/title/tolerance"));
        assert_eq!(local_fix::edits(p, &plan).len(), 1);
        assert_eq!(p.fix[0].op, "set");
        assert_eq!(p.fix[0].path, "sheet/title/tolerance");
        assert_eq!(p.fix[0].value, Some(json!("UNSPECIFIED")));
        let edited = model::edit(&plan.spec, &serde_json::to_value(&p.fix).unwrap()).unwrap();
        let after = model::plan(&plate().1, &edited).unwrap();
        assert!(!after.problems.iter().any(|q| q.code == p.code));
        assert_eq!(
            cell(&after, "general_tolerance").value.as_deref(),
            Some("UNSPECIFIED")
        );
    }
    let (plan, _) = sheet("A4", json!({"tolerance":"ISO 2768-m"}));
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "general_tolerance_missing")
    );
}

/// draftwright's cells (`TitleBlock(layout=draftwright_title_block_layout(), width=…)`'s
/// `cell_bbox`, build frame) on A4's 120 mm block and A3's and larger's 150 mm one.
const CELLS: [(&str, [f64; 4], [f64; 4]); 13] = [
    (
        "legal_owner",
        [0.0, 24.0, 84.0, 32.0],
        [0.0, 24.0, 105.0, 32.0],
    ),
    (
        "document_type",
        [84.0, 24.0, 120.0, 32.0],
        [105.0, 24.0, 150.0, 32.0],
    ),
    ("title", [0.0, 16.0, 84.0, 24.0], [0.0, 16.0, 105.0, 24.0]),
    (
        "drawing_number",
        [84.0, 16.0, 120.0, 24.0],
        [105.0, 16.0, 150.0, 24.0],
    ),
    ("material", [0.0, 8.0, 42.0, 16.0], [0.0, 8.0, 52.5, 16.0]),
    (
        "general_tolerance",
        [42.0, 8.0, 84.0, 16.0],
        [52.5, 8.0, 105.0, 16.0],
    ),
    ("units", [84.0, 8.0, 105.6, 16.0], [105.0, 8.0, 132.0, 16.0]),
    (
        "format",
        [105.6, 8.0, 120.0, 16.0],
        [132.0, 8.0, 150.0, 16.0],
    ),
    ("designed_by", [0.0, 0.0, 42.0, 8.0], [0.0, 0.0, 52.5, 8.0]),
    (
        "approved_by",
        [42.0, 0.0, 84.0, 8.0],
        [52.5, 0.0, 105.0, 8.0],
    ),
    ("date", [84.0, 0.0, 105.6, 8.0], [105.0, 0.0, 132.0, 8.0]),
    (
        "revision",
        [105.6, 0.0, 112.8, 8.0],
        [132.0, 0.0, 141.0, 8.0],
    ),
    ("sheet", [112.8, 0.0, 120.0, 8.0], [141.0, 0.0, 150.0, 8.0]),
];

/// The grid is draftwright's, cell for cell, in the block's frame, on both block widths; and the
/// plan's cells are it, placed at the bottom of the layout's reservation.
#[test]
fn the_grid_is_draftwrights() {
    for (k, size) in [(0, "A4"), (1, "A3")] {
        let (plan, _) = sheet(size, json!({}));
        let block = plan.sheet.title_block;
        assert_eq!(block[2] - block[0], [120.0, 150.0][k], "{size}");
        let cells = &plan.sheet.title_cells;
        assert_eq!(
            cells.iter().map(|c| c.field.as_str()).collect::<Vec<_>>(),
            CELLS.map(|c| c.0),
        );
        for (field, a4, a3) in CELLS {
            let want = [a4, a3][k];
            let got = cell(&plan, field).cell;
            for i in 0..4 {
                let origin = block[i % 2];
                assert!(
                    (got[i] - origin - want[i]).abs() < 1e-9,
                    "{size} {field}: {got:?}, draftwright's {want:?}"
                );
            }
        }
        assert_eq!(title_block::top(block), block[1] + 32.0);
    }
}

/// The text is measured as draftwright measures it (`_text_size`: the ink box of build123d's
/// `Text` in IBM Plex Sans Condensed at 3 mm), within a thousandth of a millimetre, multi-line
/// values included.
#[test]
fn values_are_measured_as_draftwright_measures_them() {
    let draftwright = [
        ("2026-09-11", 14.7900001, 2.16599629375),
        ("11/09/2026", 14.721005959375, 2.6729980468750005),
        ("11 SEP 2026", 15.45599619375, 2.16599629375),
        (
            "12 SEPTEMBER 2026 (ISSUE)",
            35.9009766625,
            2.6940041062500004,
        ),
        (
            "FRAME\nMACHINING\nREVIEW\nPRELIMINARY",
            17.37900400625,
            13.794004106250002,
        ),
        (
            "ENGINEERING REVIEW GROUP / draftwright",
            53.9609766625,
            2.85599629375,
        ),
        (
            "DOCUMENT NUMBER WITH A LONG SUFFIX",
            53.31595713125,
            2.16599629375,
        ),
        ("BRACKET", 11.457002053125, 2.16599629375),
        ("SCALE 1:2", 12.294004106250002, 2.16599629375),
    ];
    for (value, w, h) in draftwright {
        let [mw, mh] = title_block::measure(value);
        assert!(
            (mw - w).abs() < 1e-3 && (mh - h).abs() < 1e-3,
            "{value:?}: {mw} × {mh}, draftwright's {w} × {h}"
        );
    }
}

/// A `sheet.title` key the block has no cell for is refused by `plan` and by `edit`, naming the
/// keys it has, never accepted and left off the sheet (build123d_drafting's `TitleBlock` refuses
/// a value its layout has no cell for; draftwright's `Sheet` takes no such option).
#[test]
fn a_title_key_with_no_cell_is_refused() {
    let (_, analysis) = plate();
    for key in ["units", "format", "scale", "part_name", "drawing_number"] {
        let spec: Spec =
            serde_json::from_value(json!({ "sheet": { "title": { key: "X" } } })).unwrap();
        let refused = model::plan(analysis, &spec).unwrap_err();
        assert!(refused.message.contains(&format!("sheet/title/{key}")));
        assert!(refused.choices.iter().any(|c| c == "date"), "{refused:?}");
        let ops = json!([{ "op": "set", "path": format!("sheet/title/{key}"), "value": "X" }]);
        assert!(model::edit(&Spec::default(), &ops).is_err(), "{key}");
    }
    for (key, field) in title_block::KEYS {
        let (plan, _) = sheet("A4", json!({ key: "X" }));
        let expected = if key == "drawn_by" {
            "X / draftwright"
        } else {
            "X"
        };
        assert_eq!(cell(&plan, field).value.as_deref(), Some(expected), "{key}");
    }
}

const DATE: &str = "2026-09-11";

/// The reporter's configuration in draftwright's test: a title, a drawing number and a date.
fn dated(size: &str) -> (Plan, Drawing) {
    sheet(
        size,
        json!({ "title": "BRACKET", "number": "DRW-0042", "date": DATE }),
    )
}

/// ISSUE-1585-a `test_the_date_is_a_field_of_its_own`: the date is recorded as drawn in a cell
/// of its own, not another field's.
#[test]
fn the_date_is_a_field_of_its_own() {
    let (plan, _) = dated("A4");
    let date = cell(&plan, "date");
    assert_eq!(date.value.as_deref(), Some(DATE));
    assert_ne!(date.cell, cell(&plan, "revision").cell);
    let holding: Vec<&str> = plan
        .sheet
        .title_cells
        .iter()
        .filter(|c| c.value.as_deref() == Some(DATE))
        .map(|c| c.field.as_str())
        .collect();
    assert_eq!(holding, ["date"]);
}

/// ISSUE-1585-a `test_the_date_reaches_the_pdf_text_layer`, the drawing's half: the date is one
/// invisible text run, in the face it is drawn with, at its cell's centre; the SVG carries it.
/// (`tests/output.rs::title_block_values_reach_the_pdf_text_layer` reads it back from the PDF.)
#[test]
fn the_date_is_a_text_run_at_its_cells_centre() {
    let (plan, drawing) = dated("A4");
    let runs: Vec<_> = drawing.text.iter().filter(|t| t.text == DATE).collect();
    assert_eq!(runs.len(), 1);
    let [x0, y0, x1, y1] = cell(&plan, "date").cell;
    assert_eq!(runs[0].at, [0.5 * (x0 + x1), 0.5 * (y0 + y1)]);
    assert_eq!(runs[0].font, title_block::FONT);
    assert_eq!(runs[0].size, 3.0);
    assert!(drawing.svg().contains(&format!(">{DATE}</text>")));
}

/// ISSUE-1585-a `test_the_date_is_drawn_inside_its_cell`: the date's ink fits its cell, and no
/// cell of the sheet overflows.
#[test]
fn the_date_is_drawn_inside_its_cell() {
    let (plan, _) = dated("A4");
    let [x0, y0, x1, y1] = cell(&plan, "date").cell;
    let [w, h] = title_block::measure(DATE);
    assert!(w < x1 - x0 && h < y1 - y0);
    assert!(overflows(&plan).is_empty(), "{:?}", overflows(&plan));
}

/// ISSUE-1585-a `test_common_date_formats_fit_on_the_pages_draftwright_uses`: on A4's 120 mm
/// block and A3's 150 mm one, the date cell holds each common format.
#[test]
fn common_date_formats_fit_on_the_pages_draftwright_uses() {
    for size in ["A4", "A3"] {
        let (plan, _) = dated(size);
        let [x0, _, x1, _] = cell(&plan, "date").cell;
        for value in ["2026-09-11", "11/09/2026", "11 SEP 2026"] {
            let [w, _] = title_block::measure(value);
            assert!(
                w < x1 - x0,
                "{size}: {value:?} needs {w:.2} mm of {:.2}",
                x1 - x0
            );
        }
    }
}

/// ISSUE-1585-a `test_no_date_field_when_no_date_was_supplied`, with draftwright's whitespace
/// and padding cases: no date is drawn when none is supplied, or only whitespace; a padded date is
/// drawn as the date. Every field not supplied leaves its cell blank but for its label.
#[test]
fn no_date_field_when_no_date_was_supplied() {
    for title in [
        json!({ "title": "BRACKET", "number": "DRW-0042" }),
        json!({ "title": "BRACKET", "number": "DRW-0042", "date": "   " }),
    ] {
        let (plan, drawing) = sheet("A4", title);
        assert_eq!(cell(&plan, "date").value, None);
        let [x0, y0, x1, y1] = cell(&plan, "date").cell;
        assert!(
            !drawing
                .text
                .iter()
                .any(|t| (x0..x1).contains(&t.at[0]) && (y0..y1).contains(&t.at[1])),
            "a run in the empty date cell"
        );
        let filled: BTreeMap<&str, &str> = plan
            .sheet
            .title_cells
            .iter()
            .filter_map(|c| Some((c.field.as_str(), c.value.as_deref()?)))
            .collect();
        assert_eq!(
            filled,
            BTreeMap::from([
                ("title", "BRACKET"),
                ("drawing_number", "DRW-0042"),
                ("units", "mm"),
                ("format", "A4"),
                ("designed_by", "draftwright"),
            ])
        );
    }
    let (plan, drawing) = sheet("A4", json!({ "date": format!("  {DATE}  ") }));
    assert_eq!(cell(&plan, "date").value.as_deref(), Some(DATE));
    assert!(drawing.text.iter().any(|t| t.text == DATE));
}

/// draftwright's overflow fixture: a drawn-by value wider than its cell on A2, whose excess runs
/// into the block, not off the page. The spec gives the author; the cell credits draftwright
/// after it, as draftwright's does. (Lengthened for #221: draftwright's own value now fits its
/// cell drawn smaller.)
const AUTHOR: &str = "ENGINEERING DESIGN AND MANUFACTURING REVIEW GROUP";
const OVERFLOWING: &str = "ENGINEERING DESIGN AND MANUFACTURING REVIEW GROUP / draftwright";

/// ISSUE-1536-a `test_a_field_crossing_cells_inside_page_is_reported`: the overflow is one
/// warning naming the field, with the text's and the cell's widths, though the block stays
/// inside the page and nothing is off the sheet.
#[test]
fn a_field_crossing_cells_inside_the_page_is_reported() {
    let (plan, _) = sheet("A2", json!({ "title": "FRAME", "drawn_by": AUTHOR }));
    let c = cell(&plan, "designed_by").cell;
    let [x0, _, x1, _] = c;
    let [w, _] = drawn(OVERFLOWING, c);
    assert_eq!(
        title_block::value_size(OVERFLOWING, c),
        title_block::MIN_TEXT
    );
    assert!(w > x1 - x0);
    assert!(plan.sheet.title_block[2] <= plan.sheet.page[0] - 10.0 + 1e-9);
    // Nothing of the block is off the sheet. (The plate's own dimensions are placement's: on A2
    // its plan's y location of the hole pattern is reported off the sheet.)
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "annotation_off_sheet"
                && p.refs.iter().any(|s| s.starts_with("title_block/"))),
        "{:?}",
        plan.problems
    );
    let found = overflows(&plan);
    assert_eq!(found.len(), 1, "{found:?}");
    let p = found[0];
    assert_eq!(p.severity, "warning");
    assert_eq!(p.subjects, ["sheet"]);
    assert_eq!(p.refs, ["title_block/designed_by"]);
    assert!(p.message.contains("'designed_by'"));
    assert!(p.message.contains(&format!("{w:.2}")));
    assert!(p.message.contains(&format!("{:.2}", x1 - x0)));
    assert!(p.message.contains(OVERFLOWING));
    assert!(p.message.contains("sheet/title/drawn_by"));
}

/// ISSUE-1536-a `test_all_rendered_fields_and_multiline_height_are_checked`: each field the
/// sheet draws is checked against its cell, a multi-line value by its height; a value that runs
/// past the page edge is also an error. Each value is too long for its cell even at the smallest
/// height a value is drawn at (#221).
#[test]
fn all_rendered_fields_and_multiline_heights_are_checked() {
    let cases = [
        (
            "title",
            "FRAME\nMACHINING\nREVIEW\nPRELIMINARY".to_string(),
            "title",
        ),
        (
            "number",
            "DOCUMENT NUMBER WITH A LONG SUFFIX "
                .repeat(2)
                .trim()
                .to_string(),
            "drawing_number",
        ),
        ("revision", "PRELIMINARY REVISION A".into(), "revision"),
        (
            "date",
            "12 SEPTEMBER 2026 (PRELIMINARY ISSUE)".into(),
            "date",
        ),
        (
            "tolerance",
            "TOLERANCE REQUIREMENTS ARE IN THE PROCESS SPECIFICATION "
                .repeat(2)
                .trim()
                .to_string(),
            "general_tolerance",
        ),
        (
            "drawn_by",
            "ENGINEERING REVIEW TEAM ".repeat(8),
            "designed_by",
        ),
        (
            "company",
            "MANUFACTURING REVIEW ORGANISATION ".repeat(8),
            "legal_owner",
        ),
    ];
    for (key, value, field) in cases {
        let (plan, drawing) = sheet("A2", json!({ key: value }));
        let c = cell(&plan, field);
        let [x0, y0, x1, y1] = c.cell;
        let [w, h] = drawn(c.value.as_ref().unwrap(), c.cell);
        assert!(w > x1 - x0 || h > y1 - y0, "{field}");
        let subject = format!("title_block/{field}");
        assert!(
            overflows(&plan).iter().any(|p| p.subjects == ["sheet"]
                && p.refs == [subject.as_str()]
                && p.message.contains(&format!("'{field}'"))),
            "{field}: {:?}",
            plan.problems
        );
        let drawn = if key == "drawn_by" {
            title_block::attribution(&value)
        } else {
            value.trim().to_string()
        };
        assert!(drawing.text.iter().any(|t| t.text == drawn), "{field}");
        let off: Vec<_> = plan
            .problems
            .iter()
            .filter(|p| p.code == "annotation_off_sheet" && p.refs == [subject.as_str()])
            .collect();
        let cx = 0.5 * (x0 + x1);
        let past = cx - w / 2.0 < 10.0 || cx + w / 2.0 > plan.sheet.page[0] - 10.0;
        assert_eq!(past, !off.is_empty(), "{field}: {off:?}");
        assert!(off.iter().all(|p| p.severity == "error"));
        if field == "legal_owner" {
            assert!(past);
        }
    }
}

/// ISSUE-1536-a `test_export_preserves_the_full_overflowing_value`, the drawing's half: the
/// overflowing value is drawn and carried whole (the SVG's text here; the PDF's in
/// `tests/output.rs`), and drawing it changes nothing in the plan.
#[test]
fn export_keeps_the_full_overflowing_value() {
    let (plan, drawing) = sheet("A2", json!({ "title": "FRAME", "drawn_by": AUTHOR }));
    assert_eq!(
        drawing
            .text
            .iter()
            .filter(|t| t.text == OVERFLOWING)
            .count(),
        1
    );
    assert!(drawing.svg().contains(&format!(">{OVERFLOWING}</text>")));
    let (again, _) = sheet("A2", json!({ "title": "FRAME", "drawn_by": AUTHOR }));
    assert_eq!(
        serde_json::to_string(&plan).unwrap(),
        serde_json::to_string(&again).unwrap()
    );
    assert_eq!(overflows(&plan).len(), 1);
}

/// #221: a value too wide for its cell at the block's 3 mm is drawn smaller to fit, not reported
/// (and so never a reason for larger paper); a value that fits keeps 3 mm.
#[test]
fn a_long_value_is_drawn_smaller_to_fit_its_cell() {
    const NUMBER: &str = "RETAINING-RING-DIN471-M10-A";
    let (plan, drawing) = sheet("A4", json!({ "number": NUMBER, "date": DATE }));
    let c = cell(&plan, "drawing_number").cell;
    let [x0, y0, x1, y1] = c;
    assert!(
        title_block::measure(NUMBER)[0] > x1 - x0,
        "the value overflows at 3 mm"
    );
    let size = title_block::value_size(NUMBER, c);
    assert!(
        (title_block::MIN_TEXT..title_block::TEXT).contains(&size),
        "{size}"
    );
    let [w, h] = drawn(NUMBER, c);
    let pad = 2.0 * title_block::LABEL_PAD;
    assert!(
        w <= x1 - x0 - pad + 1e-9 && h <= y1 - y0 + 1e-9,
        "clear of the cell's lines"
    );
    assert!(overflows(&plan).is_empty(), "{:?}", overflows(&plan));
    assert!(
        !plan
            .problems
            .iter()
            .any(|p| p.code == "annotation_off_sheet"
                && p.refs.iter().any(|r| r.starts_with("title_block/"))),
        "{:?}",
        plan.problems
    );
    let run = drawing.text.iter().find(|t| t.text == NUMBER).unwrap();
    assert!((run.size - size).abs() < 1e-9);
    let date = drawing.text.iter().find(|t| t.text == DATE).unwrap();
    assert_eq!(date.size, title_block::TEXT);
}

/// The scale is stated outside the block, above its right end, as draftwright's scale note
/// (`_add_scale_note`), and the block holds every value with no other text.
#[test]
fn the_scale_is_stated_above_the_block() {
    let (plan, drawing) = dated("A4");
    let note = format!("SCALE {}", plan.sheet.scale);
    let runs: Vec<_> = drawing.text.iter().filter(|t| t.text == note).collect();
    assert_eq!(runs.len(), 1);
    let block = plan.sheet.title_block;
    let at = runs[0].at;
    assert!(at[1] > title_block::top(block) && at[1] < block[3]);
    assert!(at[0] > block[0] && at[0] < block[2]);
}

/// ARCH0006-1 for the block: a value is drawn with the face it is measured with. The ink drawn
/// in the date cell above its label is the measured box, centred in the cell, to 0.001 mm.
#[test]
fn a_value_is_drawn_as_it_is_measured() {
    use draftwright::output::{Curve, Item};
    let (plan, drawing) = dated("A4");
    let [x0, y0, x1, y1] = cell(&plan, "date").cell;
    let mut b = [
        f64::INFINITY,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NEG_INFINITY,
    ];
    let mut add = |p: [f64; 2]| {
        b = [
            b[0].min(p[0]),
            b[1].min(p[1]),
            b[2].max(p[0]),
            b[3].max(p[1]),
        ];
    };
    let first = |c: &Curve| match c {
        Curve::Line(a, _) => Some(*a),
        Curve::Beziers(list) => Some(list[0][0]),
        _ => None,
    };
    for item in &drawing.layers[2].items {
        let Item::Face(wires) = item else { continue };
        let Some(p) = first(&wires[0][0]) else {
            continue;
        };
        // The label's glyphs sit in the cell's lowest 2.5 mm, the value's above them.
        if !((x0..x1).contains(&p[0]) && (y0 + 2.5..y1).contains(&p[1])) {
            continue;
        }
        for c in wires.iter().flatten() {
            match c {
                Curve::Line(a, z) => {
                    add(*a);
                    add(*z);
                }
                Curve::Beziers(list) => {
                    for poles in list {
                        for i in 0..=256 {
                            let t = i as f64 / 256.0;
                            let mut q = poles.clone();
                            while q.len() > 1 {
                                q = q
                                    .windows(2)
                                    .map(|w| [0, 1].map(|k| w[0][k] + (w[1][k] - w[0][k]) * t))
                                    .collect();
                            }
                            add(q[0]);
                        }
                    }
                }
                _ => panic!("glyphs are lines and Béziers"),
            }
        }
    }
    let [w, h] = title_block::measure(DATE);
    let (cx, cy) = (0.5 * (x0 + x1), 0.5 * (y0 + y1));
    let want = [cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0];
    for k in 0..4 {
        assert!(
            (b[k] - want[k]).abs() < 1e-3,
            "drawn {b:?}, measured {want:?}"
        );
    }
}

/// The "drawn by" cell always credits draftwright, alone when the spec names no author, and the
/// drawing's attribution link covers that cell (draftwright's `_attribution_author` and
/// `add_svg_hyperlink`), linking to draftwright.io in the SVG and the PDF.
#[test]
fn the_drawn_by_cell_credits_draftwright_and_carries_the_link() {
    let (plan, drawing) = sheet("A4", json!({ "title": "FRAME" }));
    let c = cell(&plan, "designed_by");
    assert_eq!(c.value.as_deref(), Some("draftwright"));
    assert_eq!(drawing.link, Some(c.cell));
    assert!(drawing.text.iter().any(|t| t.text == "draftwright"));
    let svg = drawing.svg();
    assert!(svg.contains("<a xlink:href=\"https://draftwright.io\""));
    let pdf = drawing.pdf(&|_| None);
    assert!(
        pdf.windows(b"https://draftwright.io".len())
            .any(|w| w == b"https://draftwright.io")
    );
    let (plan, _) = sheet("A4", json!({ "drawn_by": "  A. Fitter  " }));
    assert_eq!(
        cell(&plan, "designed_by").value.as_deref(),
        Some("A. Fitter / draftwright")
    );
    // A whitespace-only author counts as none (draftwright's tests/test_export_formats.py).
    let (plan, _) = sheet("A4", json!({ "drawn_by": "   " }));
    assert_eq!(
        cell(&plan, "designed_by").value.as_deref(),
        Some("draftwright")
    );
    assert_eq!(title_block::attribution("   "), "draftwright");
}

#[test]
fn default_titles_prefer_meaningful_products_and_preserve_explicit_titles() {
    let bytes = std::fs::read_to_string(fixture("parts/plate.step")).unwrap();
    // A real STEP read, with no PMI required for the product name.
    let bytes = bytes.replace(
        "PRODUCT('COMPOUND','COMPOUND'",
        "PRODUCT('Clamp plate','Clamp plate'",
    );
    let (_, a) =
        model::analyse_native_bytes("string_post-tapped.step.gz", bytes.as_bytes()).unwrap();
    assert_eq!(a.product_name.as_deref(), Some("Clamp plate"));
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(cell(&p, "title").value.as_deref(), Some("CLAMP PLATE"));
    assert_eq!(a.binding.part, "string_post-tapped");

    for product in [
        None,
        Some(""),
        Some("  "),
        Some("Part1"),
        Some("Part 12"),
        Some("Body"),
        Some("Body.001"),
        Some("Solid"),
        Some("COMPOUND"),
    ] {
        let mut a = a.clone();
        a.product_name = product.map(String::from);
        let p = model::plan(&a, &Spec::default()).unwrap();
        assert_eq!(
            cell(&p, "title").value.as_deref(),
            Some("STRING POST TAPPED"),
            "{product:?}"
        );
        for title in ["Explicit drawing title", ""] {
            let mut s = Spec::default();
            s.sheet.title.insert("title".into(), title.into());
            let p = model::plan(&a, &s).unwrap();
            assert_eq!(p.sheet.title["title"], title);
        }
    }
    let mut s = Spec::default();
    s.sheet
        .title
        .insert("title".into(), "Override product".into());
    assert_eq!(
        model::plan(&a, &s).unwrap().sheet.title["title"],
        "Override product"
    );
    // A selected assembly member's binding is its product name, so preserve the file stem
    // separately when a generic CAD name needs the same fallback.
    let assembly = std::fs::read_to_string(fixture("assembly/assembly.step"))
        .unwrap()
        .replace("PRODUCT('pin','pin'", "PRODUCT('Body1','Body1'");
    let (_, selected) = model::analyse_step_part(
        Path::new("string_post-tapped.step"),
        assembly.as_bytes(),
        Some(1),
    )
    .unwrap();
    assert_eq!(selected.binding.part, "Body1");
    assert_eq!(selected.source_stem.as_deref(), Some("string_post-tapped"));
    assert_eq!(
        model::plan(&selected, &Spec::default())
            .unwrap()
            .sheet
            .title["title"],
        "STRING POST TAPPED"
    );
    let mut legacy = serde_json::to_value(&a).unwrap();
    legacy.as_object_mut().unwrap().remove("product_name");
    let legacy: Analysis = serde_json::from_value(legacy).unwrap();
    assert_eq!(
        model::plan(&legacy, &Spec::default()).unwrap().sheet.title["title"],
        "STRING POST TAPPED"
    );
}

#[test]
fn drawing_number_prefers_the_products_leading_part_number() {
    let bytes = std::fs::read_to_string(fixture("parts/plate.step"))
        .unwrap()
        .replace(
            "PRODUCT('COMPOUND','COMPOUND'",
            "PRODUCT('GRM-02 Shaft','GRM-02 Shaft'",
        );
    let (_, a) = model::analyse_native_bytes(
        "GRM-03_thumbwheel_drive_screw_ap242_pmi.step.gz",
        bytes.as_bytes(),
    )
    .unwrap();
    assert_eq!(a.product_name.as_deref(), Some("GRM-02 Shaft"));
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(cell(&p, "drawing_number").value.as_deref(), Some("GRM-02"));
    // Keep #108's complete readable title, even when its number is also in DWG NO.
    assert_eq!(cell(&p, "title").value.as_deref(), Some("GRM 02 SHAFT"));
}

#[test]
fn drawing_number_uses_the_stem_when_the_product_has_no_part_number() {
    for product in [None, Some("Thumbwheel drive screw"), Some("Bracket 42")] {
        let mut a = plate().1.clone();
        a.product_name = product.map(String::from);
        a.binding.part = "GRM-03_thumbwheel_drive_screw_ap242_pmi".into();
        let p = model::plan(&a, &Spec::default()).unwrap();
        assert_eq!(cell(&p, "drawing_number").value.as_deref(), Some("GRM-03"));
    }
    // A selected assembly member keeps the file's stem separately from its binding.
    let mut a = plate().1.clone();
    a.product_name = Some("Thumbwheel drive screw".into());
    a.binding.part = "Thumbwheel drive screw".into();
    a.source_stem = Some("GRM-03_thumbwheel_drive_screw_ap242_pmi".into());
    assert_eq!(
        model::plan(&a, &Spec::default()).unwrap().sheet.title["number"],
        "GRM-03"
    );
}

#[test]
fn a_numeric_stem_is_a_drawing_number() {
    let mut a = plate().1.clone();
    a.product_name = None;
    a.binding.part = "10000".into();
    let p = model::plan(&a, &Spec::default()).unwrap();
    assert_eq!(cell(&p, "drawing_number").value.as_deref(), Some("10000"));
}

#[test]
fn a_plain_word_stem_keeps_the_fallback_drawing_number() {
    let p = model::plan(&plate().1, &Spec::default()).unwrap();
    assert_eq!(cell(&p, "drawing_number").value.as_deref(), Some("DWG-001"));
}

#[test]
fn an_explicit_spec_drawing_number_wins() {
    let mut a = plate().1.clone();
    a.product_name = Some("GRM-03 Thumbwheel".into());
    let mut s = Spec::default();
    s.sheet.title.insert("number".into(), "AUTHOR-42".into());
    let p = model::plan(&a, &s).unwrap();
    assert_eq!(
        cell(&p, "drawing_number").value.as_deref(),
        Some("AUTHOR-42")
    );
}

#[test]
fn an_explicit_blank_spec_drawing_number_wins() {
    let mut a = plate().1.clone();
    a.product_name = Some("GRM-03 Thumbwheel".into());
    for blank in ["", "   "] {
        let mut s = Spec::default();
        s.sheet.title.insert("number".into(), blank.into());
        let p = model::plan(&a, &s).unwrap();
        assert_eq!(p.sheet.title["number"], blank);
        assert_eq!(cell(&p, "drawing_number").value, None);
    }
}

#[test]
fn drawing_numbers_require_a_leading_token_with_only_internal_hyphens() {
    for (product, expected) in [
        ("  grm-03-2 Thumbwheel", "grm-03-2"),
        ("10000 Bracket", "10000"),
        ("A42_thumbwheel", "A42"),
        ("GRM--03 Thumbwheel", "DWG-001"),
        ("GRM-03- Thumbwheel", "DWG-001"),
        ("-GRM-03 Thumbwheel", "DWG-001"),
        ("Bracket GRM-03", "DWG-001"),
    ] {
        let mut a = plate().1.clone();
        a.product_name = Some(product.into());
        let p = model::plan(&a, &Spec::default()).unwrap();
        assert_eq!(p.sheet.title["number"], expected, "{product}");
    }
}
