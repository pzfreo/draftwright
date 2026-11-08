//! The spotface symbol (maintainer decision, 2026-10-08): a hole's spotface is stated with ASME
//! Y14.5-2018's dedicated symbol, the counterbore symbol with SF inside, where draftwright prints
//! the counterbore's `⌴`. The callout's term is `⌴SF` (Unicode has no code point for the
//! symbol); it is drawn as one symbol, the box's strokes around the drawing font's own `SF`, and
//! the PDF's and SVG's text layers carry it as `⌴SF`, so it is searched and copied.

#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/facts.rs"]
mod facts;
#[path = "common/pdf.rs"]
mod pdf;

use std::io::Read as _;
use std::path::{Path, PathBuf};

use draftwright::dims::label_ink;
use draftwright::model::{self, Spec};
use draftwright::text::Font;
use pdf::{pdf_objects, pdf_text, reference, svg_text, to_unicode};

const TEXT: f64 = draftwright::dims::TEXT;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn mono() -> Vec<u8> {
    std::fs::read(root().join("fonts/IBMPlexMono-Regular.ttf")).unwrap()
}

fn terms(label: &str) -> Vec<String> {
    label.split(' ').map(String::from).collect()
}

/// The symbol is drawn as the counterbore's open box, widened, with the font's `SF` inside it:
/// the letters' outlines are the font's own, lie wholly within the box, and stand clear of its
/// floor and below its sides. The label is measured with it, so a spotfaced callout's box is
/// wider than the counterbored one's by the wider cell.
#[test]
fn the_spotface_symbol_is_the_box_with_the_fonts_sf_inside() {
    let font = Font::new(mono()).unwrap();
    let ink = label_ink(&terms("⌴SF"), &font);
    assert_eq!(ink.strokes.len(), 3, "{:?}", ink.strokes);
    let (l, r, cap) = (
        ink.strokes[0][0][0],
        ink.strokes[2][0][0],
        ink.strokes[0][0][1],
    );
    assert!(cap > 0.5 * TEXT, "the box's sides rise {cap}");
    assert!(r - l > TEXT, "the box is {} wide", r - l);
    assert_eq!(
        ink.strokes,
        [
            [[l, cap], [l, 0.0]],
            [[l, 0.0], [r, 0.0]],
            [[r, 0.0], [r, cap]]
        ]
    );
    assert_eq!(ink.glyphs.len(), 1, "one text token, the letters");
    let letters = &ink.glyphs[0];
    let sf = font.baseline("SF", TEXT);
    assert_eq!(letters.len(), sf.contours.len(), "S and F's contours");
    let points: Vec<[f64; 2]> = letters
        .iter()
        .flatten()
        .flat_map(|s| match *s {
            draftwright::text::Segment::Line(a, b) => vec![a, b],
            draftwright::text::Segment::Quad(a, b, c) => vec![a, b, c],
            draftwright::text::Segment::Cubic(a, b, c, d) => vec![a, b, c, d],
        })
        .collect();
    assert!(!points.is_empty());
    for p in &points {
        assert!(
            p[0] > l && p[0] < r && p[1] > 0.0 && p[1] < cap,
            "{p:?} outside the box {l}..{r} x 0..{cap}"
        );
    }

    let width = |label: &str| {
        let ink = label_ink(&terms(label), &font);
        ink.max[0] - ink.min[0]
    };
    let (cbore, spotface) = (width("⌀62 THRU ⌴ ⌀70 ↧ 10"), width("⌀62 THRU ⌴SF ⌀70 ↧ 10"));
    assert!(
        spotface > cbore + 0.3 * TEXT,
        "spotfaced {spotface}, counterbored {cbore}"
    );
}

/// spool132's bore (⌀62 through, spotfaced ⌀70 10 deep, quiddity's record) is called out with
/// the spotface symbol (draftwright's sheet prints `⌴`), drawn with the font's `SF` in the box,
/// and the callout reads back from the PDF's and the SVG's text layers as written. The letters
/// shown in the PDF's text are real glyphs of the embedded subset of the drawing's font (not
/// `.notdef`); the box, like every callout symbol the bundled fonts lack (`⌀ ↧ ⌴ ⌵`), is ink, its
/// `⌴` searchable through the ToUnicode map.
#[test]
fn spool132s_spotface_is_drawn_and_searchable() {
    let Some(corpus) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let f = facts::load("spool132");
    let mut json = String::new();
    flate2::read::GzDecoder::new(
        std::fs::File::open(root().join("tests/fixtures/recognition/sheets/spool132.json.gz"))
            .unwrap(),
    )
    .read_to_string(&mut json)
    .unwrap();
    let (part, mut a) = model::analyse(&Path::new(&corpus).join(&f.binding.file)).unwrap();
    let (r, features) = draftwright::bridge::read(&part, &a.binding.source_sha256, &json).unwrap();
    a.recognition = Some(r);
    a.features = features;
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let callout = "⌀62 THRU ⌴SF ⌀70 ↧ 10";
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.label == callout)
        .unwrap_or_else(|| {
            panic!(
                "no {callout}: {:?}",
                plan.dimensions.iter().map(|d| &d.label).collect::<Vec<_>>()
            )
        });
    assert!(d.placed.is_some(), "{} is drawn", d.id);
    for (parameter, value) in [("spotface.diameter", 70.0), ("spotface.depth", 10.0)] {
        assert!(
            d.measurements
                .iter()
                .any(|m| m.parameter == parameter && m.value == value),
            "{parameter}"
        );
    }

    let font = Font::new(mono()).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let written: Vec<String> = drawing.text.iter().map(|r| r.text.clone()).collect();
    let run = drawing
        .text
        .iter()
        .find(|r| r.text == callout)
        .expect("the callout's text run");
    assert_eq!(run.font, "IBMPlexMono-Regular.ttf");
    let svg = drawing.svg();
    assert_eq!(svg_text(&svg), written, "the SVG's text");
    let pdf = drawing.pdf(&|name: &str| std::fs::read(root().join("fonts").join(name)).ok());
    assert_eq!(pdf_text(&pdf), written, "the PDF's text");

    // The letters' CIDs in the embedded font: each maps to a glyph of the subset program with
    // an outline and the whole font's advance for that letter.
    let objects = pdf_objects(&pdf);
    let font_dict = objects
        .values()
        .map(|(d, _)| d)
        .find(|d| d.contains("/Subtype /Type0") && d.contains("IBMPlexMono-Regular"))
        .expect("the mono font");
    let unicode = to_unicode(&objects[&reference(font_dict, "/ToUnicode")].1);
    let descendant = &font_dict[font_dict.find("/DescendantFonts [").unwrap() + 18..];
    let descendant: u32 = descendant
        .split_whitespace()
        .next()
        .unwrap()
        .parse()
        .unwrap();
    let cid_font = &objects[&descendant].0;
    let descriptor = &objects[&reference(cid_font, "/FontDescriptor")].0;
    let program = &objects[&reference(descriptor, "/FontFile2")].1;
    let subset = ttf_parser::Face::parse(program, 0).expect("the subset parses");
    let whole_data = mono();
    let whole = ttf_parser::Face::parse(&whole_data, 0).unwrap();
    let map: Vec<u16> = objects[&reference(cid_font, "/CIDToGIDMap")]
        .1
        .chunks(2)
        .map(|c| u16::from_be_bytes([c[0], c[1]]))
        .collect();
    for ch in ['S', 'F'] {
        let cid = unicode
            .iter()
            .find(|(_, t)| t.as_str() == ch.to_string())
            .map(|(c, _)| *c)
            .unwrap_or_else(|| panic!("{ch} in the ToUnicode map"));
        let g = ttf_parser::GlyphId(map[cid as usize]);
        assert_ne!(g.0, 0, "{ch} is .notdef");
        assert!(
            subset.glyph_bounding_box(g).is_some(),
            "{ch} has an outline"
        );
        let w = whole.glyph_index(ch).unwrap();
        assert_eq!(
            subset.glyph_hor_advance(g),
            whole.glyph_hor_advance(w),
            "{ch}"
        );
    }
    assert!(
        unicode.values().any(|t| t == "⌴"),
        "⌴ is searchable through the ToUnicode map"
    );
}

/// `bearing-lm10uu.step.gz` (dw-examples): the ø16.4 ↧ 1.4 counterbore is cut alike at both ends of
/// the ⌀10 bore. quiddity records it once, on the end it presents, so the callout says
/// `BOTH SIDES` after the recess, and lint reads the callout as before (#104). A recess at one
/// mouth (spool132's) keeps its plain callout, as above.
#[test]
fn a_recess_at_both_mouths_says_both_sides() {
    let file = root().join("tests/fixtures/parts/bearing-lm10uu.step.gz");
    let (part, a) = model::analyse_native(&file).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:hole:1/bore.diameter")
        .unwrap();
    // Line breaks are layout, which may split a term's words.
    let text = d
        .terms
        .iter()
        .map(String::as_str)
        .filter(|t| !t.trim().is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    // quiddity-rust v0.2.2 (#118): a 1.4 mm deep step is a counterbore, not a spotface.
    assert_eq!(text, "⌀10 THRU ⌴ ⌀16.4 ↧ 1.4 BOTH SIDES", "{}", d.label);
    // A single recess stays on the bore's line.
    assert_eq!(d.label, "⌀10 THRU ⌴ ⌀16.4 ↧ 1.4 BOTH SIDES");
    assert!(d.basis.contains("both mouths"), "{}", d.basis);
    let drawing = model::render(&part, &a, &plan, &Font::new(mono()).unwrap()).unwrap();
    let found: Vec<_> = draftwright::lint::lint(&a, &drawing)
        .into_iter()
        .filter(|i| i.code.contains("hole") || i.message.contains("hole:1"))
        .map(|i| i.message)
        .collect();
    assert!(found.is_empty(), "{found:?}");
}

/// `spotface_outer_steps.step` (quiddity-rust's #118 fixture): a ⌀6 through bore under a ⌀12 × 3
/// counterbore, a ⌀20 × 2 outer counterbore step and a ⌀30 × 0.5 spotface. quiddity records the
/// ⌀20 step in `outer_steps`; the callout states every step once, from the bore outward, each
/// with its own depth on its own line, claims each size, and lint finds every requirement stated.
#[test]
fn outer_counterbore_steps_are_each_stated_once() {
    let file = root().join("tests/fixtures/parts/spotface_outer_steps.step");
    let (part, a) = model::analyse_native(&file).unwrap();
    let plan = model::plan_recovered(&a, &Spec::default()).unwrap();
    let d = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:f:hole:f2/bore.diameter")
        .unwrap();
    let text = d
        .terms
        .iter()
        .map(String::as_str)
        .filter(|t| !t.trim().is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    assert_eq!(
        text, "⌀6 THRU ⌴ ⌀12 ↧ 3 ⌴ ⌀20 ↧ 2 ⌴SF ⌀30 ↧ 0.5",
        "{}",
        d.label
    );
    // More than one step: the bore on the first line, each step on its own, innermost first
    // (maintainer decision, 2026-10-29).
    assert_eq!(
        d.label.lines().collect::<Vec<_>>(),
        ["⌀6 THRU", "⌴ ⌀12 ↧ 3", "⌴ ⌀20 ↧ 2", "⌴SF ⌀30 ↧ 0.5"],
        "{:?}",
        d.terms
    );
    assert!(d.placed.is_some(), "{} is drawn", d.id);
    for (parameter, value) in [
        ("counterbore.diameter", 12.0),
        ("counterbore.depth", 3.0),
        ("outer_steps.0.diameter", 20.0),
        ("outer_steps.0.depth", 2.0),
        ("spotface.diameter", 30.0),
        ("spotface.depth", 0.5),
    ] {
        assert!(
            d.measurements
                .iter()
                .any(|m| m.parameter == parameter && m.value == value),
            "{parameter}: {:?}",
            d.measurements
        );
    }
    let drawing = model::render(&part, &a, &plan, &Font::new(mono()).unwrap()).unwrap();
    let found: Vec<_> = draftwright::lint::lint(&a, &drawing)
        .into_iter()
        .filter(|i| i.code.contains("hole") || i.message.contains("hole:1"))
        .map(|i| i.message)
        .collect();
    assert!(found.is_empty(), "{found:?}");
}
