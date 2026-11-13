//! Table and note symbols must be ink, not the bundled face's .notdef box.

#[path = "common/cache.rs"]
mod cache;
#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/output.rs"]
mod output;
#[path = "common/pdf.rs"]
mod pdf;
#[path = "common/support.rs"]
mod support;

use draftwright::dims;
use draftwright::model::{self, Plan, Spec};
use draftwright::output::{Curve, Drawing, Item};
use draftwright::tables;
use draftwright::text::Segment;
use serde_json::json;
use std::path::Path;

fn font() -> &'static draftwright::text::Font {
    static FONT: std::sync::OnceLock<draftwright::text::Font> = std::sync::OnceLock::new();
    FONT.get_or_init(|| draftwright::text::Font::new(output::fonts(dims::FONT).unwrap()).unwrap())
}

fn symbol(c: char) -> bool {
    matches!(c, '⌀' | '↧' | '⌴' | '⌵')
}

fn assert_text_ink(drawing: &Drawing, text: &str, at: [f64; 2]) -> usize {
    let bytes = output::fonts(dims::FONT).unwrap();
    let face = ttf_parser::Face::parse(&bytes, 0).unwrap();
    let missing: Vec<_> = text
        .chars()
        .filter(|c| !c.is_whitespace() && face.glyph_index(*c).is_none())
        .collect();
    for c in &missing {
        assert!(symbol(*c), "unsupported table/note character {c:?}: {text}");
    }
    if missing.is_empty() {
        return 0;
    }
    assert!(
        drawing
            .text
            .iter()
            .any(|run| { run.text.trim() == text.trim() && run.at == at }),
        "missing searchable text: {text}"
    );
    let ink = tables::text_ink(text, font());
    let centre = [0, 1].map(|k| 0.5 * (ink.min[k] + ink.max[k]));
    let shift = |p: [f64; 2]| [at[0] + (p[0] - centre[0]), at[1] + (p[1] - centre[1])];
    let expected = ink
        .strokes
        .iter()
        .map(|[a, b]| Curve::Line(shift(*a), shift(*b)))
        .chain(ink.glyphs.iter().flatten().flatten().map(|s| match *s {
            Segment::Line(a, b) => Curve::Line(shift(a), shift(b)),
            Segment::Quad(a, b, c) => Curve::Beziers(vec![vec![shift(a), shift(b), shift(c)]]),
            Segment::Cubic(a, b, c, d) => {
                Curve::Beziers(vec![vec![shift(a), shift(b), shift(c), shift(d)]])
            }
        }));
    let actual: Vec<_> = drawing
        .layers
        .iter()
        .flat_map(|l| &l.items)
        .flat_map(|item| match item {
            Item::Edge(c) | Item::TextStroke(c, _) | Item::FrameText(c, _) => vec![c],
            Item::Face(wires) | Item::Glyph(wires, _) => wires.iter().flatten().collect(),
        })
        .collect();
    for curve in expected {
        assert!(
            actual.contains(&&curve),
            "missing symbol/text ink in {text:?}: {curve:?}"
        );
    }
    missing.len()
}

fn assert_blocks(drawing: &Drawing, plan: &Plan) -> usize {
    let mut count = 0;
    for table in &plan.tables {
        for (text, at) in tables::texts(table) {
            count += assert_text_ink(drawing, text, at);
        }
    }
    for note in &plan.notes {
        if let Some(p) = &note.placed {
            for (row, at) in note.rows.iter().zip(&p.rows_at) {
                count += assert_text_ink(drawing, row, *at);
            }
        }
    }
    count
}

#[test]
fn inline_symbols_use_label_shapes_and_preserve_authored_spacing() {
    let font = font();
    for c in ['⌀', '↧', '⌴', '⌵'] {
        let cell = tables::text_ink(&c.to_string(), font);
        let label = dims::label_ink(&[c.to_string()], font);
        assert_eq!(cell.glyphs, label.glyphs);
        assert_eq!(cell.strokes, label.strokes);
        assert_eq!(cell.min, label.min);
        assert_eq!(cell.max, label.max);
    }
    // Symbols must also work after prose and punctuation, without changing its spaces.
    for c in ['⌀', '↧', '⌴', '⌵'] {
        let prefix = "  DRILL(";
        let ink = tables::text_ink(&format!("{prefix}{c}8"), font);
        let source = dims::label_ink(&[c.to_string()], font);
        let x = font.advance(prefix, tables::TEXT);
        let shift = |p: [f64; 2]| [x + p[0], p[1]];
        for glyph in source.glyphs {
            let expected: Vec<Vec<Segment>> = glyph
                .into_iter()
                .map(|contour| {
                    contour
                        .into_iter()
                        .map(|s| match s {
                            Segment::Line(a, b) => Segment::Line(shift(a), shift(b)),
                            Segment::Quad(a, b, c) => Segment::Quad(shift(a), shift(b), shift(c)),
                            Segment::Cubic(a, b, c, d) => {
                                Segment::Cubic(shift(a), shift(b), shift(c), shift(d))
                            }
                        })
                        .collect()
                })
                .collect();
            assert!(ink.glyphs.contains(&expected));
        }
        for [a, b] in source.strokes {
            assert!(ink.strokes.contains(&[shift(a), shift(b)]));
        }
        let end = x + tables::TEXT + font.baseline("8", tables::TEXT).max[0];
        assert!((ink.max[0] - end).abs() < 1e-9);
    }
    let ink = tables::text_ink("PROSE\n   ↧2", font);
    let x = font.advance("   ", tables::TEXT);
    let y = -font.line_spacing(tables::TEXT);
    for [a, b] in dims::label_ink(&["↧".into()], font).strokes {
        assert!(
            ink.strokes
                .contains(&[[x + a[0], y + a[1]], [x + b[0], y + b[1]]])
        );
    }
}

#[test]
fn declared_tables_and_numbered_notes_draw_and_extract_all_symbols() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, a) = model::analyse_native(&root.join("tests/fixtures/parts/plate.step")).unwrap();
    let wording = "DRILL(⌀1.6) ↧8;⌴3⌵90°";
    let spec: Spec = serde_json::from_value(json!({
        "notes": [{"id": "n:1", "text": format!("{wording} {}", "CONTINUED ".repeat(6))}],
        "tables": [{"id": "t:1", "rows": [[wording], ["⌀↧⌴⌵"]], "prefer": "bl"}]
    }))
    .unwrap();
    let plan = model::plan_recovered(&a, &spec).unwrap();
    let table = plan.tables.iter().find(|t| t.id == "t:1").unwrap();
    let p = table.placed.as_ref().expect("declared table fits");
    let ink = tables::text_ink(wording, font());
    assert!(
        (p.columns[1] - p.columns[0] - (ink.max[0] - ink.min[0] + 2.0 * tables::PAD)).abs() < 1e-9
    );
    let note = &plan.notes[0];
    assert!(
        note.rows.len() > 1,
        "exercise note continuation indentation"
    );
    assert!(note.placed.is_some());
    let drawing = model::render(&part, &a, &plan, font()).unwrap();
    assert!(assert_blocks(&drawing, &plan) >= 12);
    let extracted_pdf = pdf::pdf_text(&drawing.pdf(&output::fonts));
    let extracted_svg = pdf::svg_text(&drawing.svg());
    for run in drawing.text.iter().filter(|r| r.text.chars().any(symbol)) {
        assert!(extracted_pdf.contains(&run.text));
        assert!(extracted_svg.contains(&run.text));
    }
    assert!(!drawing.png(72.0).unwrap().is_empty());
}

#[test]
fn grm03_manufacturing_depth_is_drawn_as_symbol_ink() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, a) = cache::native(
        &root.join("tests/fixtures/material/GRM-03_thumbwheel_drive_screw_AP242_PMI.step.gz"),
    );
    let plan = cache::default_plan(&a, true);
    let drawing = model::render(&part, &a, &plan, font()).unwrap();
    assert!(
        drawing
            .text
            .iter()
            .any(|r| r.text.contains("CONVENTIONAL DRILL POINT") && r.text.contains('↧'))
    );
    assert!(assert_blocks(&drawing, &plan) > 0);
    let extracted_pdf = pdf::pdf_text(&drawing.pdf(&output::fonts));
    let extracted_svg = pdf::svg_text(&drawing.svg());
    let row = drawing
        .text
        .iter()
        .find(|r| r.text.contains("CONVENTIONAL DRILL POINT"))
        .unwrap();
    assert!(extracted_pdf.contains(&row.text));
    assert!(extracted_svg.contains(&row.text));
    assert!(!drawing.png(72.0).unwrap().is_empty());
}

#[test]
fn quality_parts_have_no_unsupported_table_or_note_glyphs() {
    let Some(root) = corpus::dir() else { return };
    let files: Vec<_> = include_str!("../tools/quality_parts.txt").lines().collect();
    assert_eq!(files.len(), 30);
    let counts = support::parallel(&files, |file| {
        let (part, a) = cache::native(&root.join(file));
        let plan = cache::default_plan(&a, true);
        let drawing = model::render(&part, &a, &plan, font()).unwrap();
        let found = assert_blocks(&drawing, &plan);
        if found > 0 {
            eprintln!("BLOCK_SYMBOLS {file}: {found}");
        }
        let texts = plan
            .tables
            .iter()
            .map(|t| tables::texts(t).len())
            .sum::<usize>()
            + plan
                .notes
                .iter()
                .filter(|n| n.placed.is_some())
                .map(|n| n.rows.len())
                .sum::<usize>();
        (texts, found)
    });
    eprintln!(
        "Checked {} quality parts: {} table/note rows, {} symbol characters",
        files.len(),
        counts.iter().map(|(texts, _)| texts).sum::<usize>(),
        counts.iter().map(|(_, symbols)| symbols).sum::<usize>()
    );
}
