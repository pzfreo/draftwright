//! The port's SVG against draftwright's, written from the same drawings
//! (`tools/capture_output.py`): every layer must carry the same style and the same lines, each
//! side's within `common::TOL` of the other's. (The PDF, rendered and searched, is compared by
//! `tools/compare_pdf.py`, which needs a PDF renderer.) The text a PDF and an SVG carry is read
//! back here and must be the labels' text, callout symbols included.

#[path = "common/output.rs"]
mod common;
#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/pdf.rs"]
mod pdf;

use std::collections::{BTreeMap, BTreeSet};
use std::io::Read as _;
use std::path::Path;

use draftwright::model::{self, Spec};
use draftwright::output::Drawing;
use pdf::{pdf_objects, pdf_text, reference, svg_text, to_unicode};
use serde_json::{Value, json};

#[test]
fn svg_matches_draftwright() {
    let mut problems = Vec::new();
    for name in ["m10127", "string_post", "ctc01"] {
        let drawing = common::drawing(&common::load(name));
        for (kind, theirs, mine) in [(
            "svg",
            common::read_svg(
                &String::from_utf8(common::gunzip(&format!("{name}.svg.gz"))).unwrap(),
            ),
            common::read_svg(&drawing.svg()),
        )] {
            let layers: std::collections::BTreeSet<&String> =
                theirs.keys().chain(mine.keys()).collect();
            for layer in layers {
                let (Some(t), Some(m)) = (theirs.get(layer), mine.get(layer)) else {
                    problems.push(format!("{name} {kind} {layer}: on one side only"));
                    continue;
                };
                let (a, b) = (
                    common::covered(&t.lines, &m.lines),
                    common::covered(&m.lines, &t.lines),
                );
                if a < 0.9999 || b < 0.9999 || t.style != m.style {
                    problems.push(format!(
                        "{name} {kind} {layer}: {a:.4} {b:.4}, style {:?} vs {:?}",
                        t.style, m.style
                    ));
                }
            }
        }
    }
    assert!(problems.is_empty(), "{}", problems.join("\n"));
}

/// cgb242's front view had a hidden stretch of no span (an exact cubic from t = 0 to t = 0, at a
/// vertex several edges share), which the port left out and reported (`edges_not_drawn`, still
/// tested on its own in `output`). haecceity now passes over such stretches at source, so every
/// stretch is drawn, nothing is reported, and the sheet is drawn and reviewed.
#[test]
fn cgb242_is_drawn_whole() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found; set DRAFTWRIGHT_CORPUS");
        return;
    };
    let mut step = Vec::new();
    flate2::read::GzDecoder::new(
        std::fs::File::open(dir.join("cadgenbench_inputs/cgb242.step.gz")).unwrap(),
    )
    .read_to_end(&mut step)
    .unwrap();
    let path = std::path::Path::new(env!("CARGO_TARGET_TMPDIR")).join("cgb242.step");
    std::fs::write(&path, step).unwrap();

    let (part, analysis) = model::analyse(&path).unwrap();
    let plan = model::plan(&analysis, &Spec::default()).unwrap();
    let font = draftwright::text::Font::new(
        std::fs::read(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("fonts/IBMPlexMono-Regular.ttf"),
        )
        .unwrap(),
    )
    .unwrap();
    let drawing = model::render(&part, &analysis, &plan, &font).unwrap();
    let ids: Vec<&str> = drawing.problems.iter().map(|p| p.id.as_str()).collect();
    assert!(ids.is_empty(), "{ids:?}");
    assert!(drawing.svg().contains("<path d="));
    assert!(!drawing.dxf().is_empty());
    assert!(!drawing.pdf(&common::fonts).is_empty());

    let review = draftwright::api::review(&path, None, &Spec::default()).unwrap();
    assert!(review.svg.contains("id=\"hidden\""));
}

/// A change to each hole's recognition record.
type Edit = dyn Fn(&mut Value);

/// The plate (four ø6 holes in a grid) drawn with its recognition changed by *edit*, its spec
/// embedded.
fn plate(edit: &Edit) -> Drawing {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut json: Value = serde_json::from_str(
        &std::fs::read_to_string(root.join("tests/fixtures/recognition/plate.json")).unwrap(),
    )
    .unwrap();
    for f in json["quiddity"]["features"].as_array_mut().unwrap() {
        edit(&mut f["record"]);
    }
    let (part, mut a) = model::analyse(&root.join("tests/fixtures/parts/plate.step")).unwrap();
    let (r, features) =
        draftwright::bridge::read(&part, &a.binding.source_sha256, &json.to_string()).unwrap();
    a.recognition = Some(r);
    a.features = features;
    let plan = model::plan(&a, &Spec::default()).unwrap();
    let font =
        draftwright::text::Font::new(common::fonts("IBMPlexMono-Regular.ttf").unwrap()).unwrap();
    model::render(&part, &a, &plan, &font).unwrap()
}

/// Text drawn on the sheet is searched and copied as it is written, callout symbols included:
/// read back from the PDF through the fonts' ToUnicode maps, and from the SVG's invisible text,
/// every label is its text run, so `⌀6` and the depth, counterbore and countersink symbols are
/// found. (draftwright's PDF text has `ø6` and spaces where the symbols are drawn as paths.)
#[test]
fn callout_symbols_are_searchable_in_the_pdf_and_the_svg() {
    let cases: [(&Edit, &str); 4] = [
        (&|_| {}, "4× ⌀6 THRU"),
        (&|r| r["bottom"] = "flat".into(), "4× ⌀6 ↧ 5"),
        (
            &|r| r["cbore"] = json!({ "diameter": 11.0, "depth": 6.4 }),
            "4× ⌀6 THRU ⌴ ⌀11 ↧ 6.4",
        ),
        (
            &|r| {
                r["csink"] = json!({
                    "axis": r["axis"].clone(), "location": r["location"].clone(),
                    "major_diameter": 12.0, "drill_diameter": 6.0, "included_angle": 90.0,
                    "depth": 3.0,
                })
            },
            "4× ⌀6 THRU ⌵ ⌀12 × 90°",
        ),
    ];
    for (edit, callout) in cases {
        let drawing = plate(edit);
        let written: Vec<String> = drawing.text.iter().map(|r| r.text.clone()).collect();
        assert!(
            drawing.annotations.iter().any(|a| a
                .runs
                .iter()
                .map(|&i| drawing.text[i].text.as_str())
                .collect::<Vec<_>>()
                .join("\n")
                == callout),
            "{callout}: {written:?}"
        );
        assert!(written.iter().any(|t| t == "PLATE"), "{written:?}");
        let pdf = drawing.pdf(&common::fonts);
        assert_eq!(pdf_text(&pdf), written, "{callout}: the PDF's text");
        let svg = drawing.svg();
        assert_eq!(svg_text(&svg), written, "{callout}: the SVG's text");
        // The spec still travels in both.
        for file in [pdf, svg.into_bytes()] {
            assert!(model::embedded_spec(&file).unwrap().is_some());
        }
    }
}

/// The text layers are deterministic: the same part drawn twice gives the same PDF and SVG
/// bytes, symbols and all (the time zone is `tests/timezone.rs`'s).
#[test]
fn the_text_layers_are_deterministic() {
    let edit = |r: &mut Value| r["cbore"] = json!({ "diameter": 11.0, "depth": 6.4 });
    let (a, b) = (plate(&edit), plate(&edit));
    assert!(
        a.pdf(&common::fonts) == b.pdf(&common::fonts),
        "pdf differs"
    );
    assert!(a.svg() == b.svg(), "svg differs");
    // And a sheet in both fonts, each subset.
    let ctc01 = common::load("ctc01");
    let (a, b) = (common::drawing(&ctc01), common::drawing(&ctc01));
    assert!(
        a.pdf(&common::fonts) == b.pdf(&common::fonts),
        "ctc01's pdf differs"
    );
}

/// *glyphs* and the glyphs their composite glyphs are built from, in *face*'s `glyf` table.
fn with_components(face: &ttf_parser::Face, mut glyphs: BTreeSet<u16>) -> BTreeSet<u16> {
    let glyf = face
        .raw_face()
        .table(ttf_parser::Tag::from_bytes(b"glyf"))
        .unwrap();
    let loca = face
        .raw_face()
        .table(ttf_parser::Tag::from_bytes(b"loca"))
        .unwrap();
    let be = |d: &[u8], k: usize| u16::from_be_bytes([d[k], d[k + 1]]);
    let offset = |g: usize| match face.tables().head.index_to_location_format {
        ttf_parser::head::IndexToLocationFormat::Short => be(loca, 2 * g) as usize * 2,
        ttf_parser::head::IndexToLocationFormat::Long => {
            u32::from_be_bytes(loca[4 * g..4 * g + 4].try_into().unwrap()) as usize
        }
    };
    let mut todo: Vec<u16> = glyphs.iter().copied().collect();
    while let Some(g) = todo.pop() {
        let d = &glyf[offset(g as usize)..offset(g as usize + 1)];
        if d.len() < 10 || (be(d, 0) as i16) >= 0 {
            continue;
        }
        // Each component: flags, glyph, its offsets (words or bytes), then any scale.
        let mut k = 10;
        loop {
            let (flags, component) = (be(d, k), be(d, k + 2));
            if glyphs.insert(component) {
                todo.push(component);
            }
            k += 4 + if flags & 0x1 != 0 { 4 } else { 2 };
            k += match flags {
                f if f & 0x8 != 0 => 2,
                f if f & 0x40 != 0 => 4,
                f if f & 0x80 != 0 => 8,
                _ => 0,
            };
            if flags & 0x20 == 0 {
                break;
            }
        }
    }
    glyphs
}

/// Each font a PDF embeds holds only the glyphs its text shows, `.notdef`, and the glyphs
/// composite ones are built from (Plex Mono's `:` is two `.`): the font program (`FontFile2`) has
/// exactly that many glyphs, tagged as a subset (`ABCDEF+name`, the tag from the
/// glyphs kept). Each code shown still reaches, through `CIDToGIDMap`, the glyph the whole font
/// has for its character (`.notdef` for the callout symbols the font lacks), with the whole
/// font's advance, outline box and `/W` width, so the text's selection boxes do not move; and
/// the text still reads back as written. Each program is under a tenth of the whole font: ctc01's
/// PDF went from 244,241 bytes (fonts embedded whole) to 146,131, the plate's (counterbored)
/// from 113,025 to 58,883.
#[test]
fn pdf_fonts_hold_only_the_glyphs_shown() {
    let cbore = |r: &mut Value| r["cbore"] = json!({ "diameter": 11.0, "depth": 6.4 });
    let csink = |r: &mut Value| {
        r["csink"] = json!({
            "axis": r["axis"].clone(), "location": r["location"].clone(),
            "major_diameter": 12.0, "drill_diameter": 6.0, "included_angle": 90.0, "depth": 3.0,
        })
    };
    let drawings = [
        ("plate, counterbored", plate(&cbore)),
        ("plate, countersunk", plate(&csink)),
        ("ctc01", common::drawing(&common::load("ctc01"))),
    ];
    for (name, drawing) in &drawings {
        let pdf = drawing.pdf(&common::fonts);
        let objects = pdf_objects(&pdf);
        let written: Vec<String> = drawing.text.iter().map(|r| r.text.clone()).collect();
        assert_eq!(pdf_text(&pdf), written, "{name}: the PDF's text");
        let type0: Vec<&String> = objects
            .values()
            .map(|(d, _)| d)
            .filter(|d| d.contains("/Subtype /Type0"))
            .collect();
        let files: BTreeSet<&str> = drawing.text.iter().map(|r| r.font.as_str()).collect();
        assert_eq!(type0.len(), files.len(), "{name}: one font per file");
        for font in type0 {
            let base = font[font.find("/BaseFont /").unwrap() + 11..]
                .split_whitespace()
                .next()
                .unwrap();
            let (tag, file) = base.split_once('+').expect("a subset tag");
            assert!(
                tag.len() == 6 && tag.bytes().all(|b| b.is_ascii_uppercase()),
                "{name}: {base}"
            );
            let file = format!("{file}.ttf");
            let whole_data = common::fonts(&file).unwrap();
            let whole = ttf_parser::Face::parse(&whole_data, 0).unwrap();
            let shown: BTreeSet<u16> = drawing
                .text
                .iter()
                .filter(|r| r.font == file)
                .flat_map(|r| r.text.chars())
                // U+2300 keeps its Unicode text but uses the bundled slashed-o glyph.
                .map(|ch| if ch == '⌀' { 'ø' } else { ch })
                .map(|ch| whole.glyph_index(ch).map_or(0, |g| g.0))
                .chain([0])
                .collect();
            let shown = with_components(&whole, shown);

            let descendant = &font[font.find("/DescendantFonts [").unwrap() + 18..];
            let descendant: u32 = descendant
                .split_whitespace()
                .next()
                .unwrap()
                .parse()
                .unwrap();
            let cid_font = &objects[&descendant].0;
            assert!(
                cid_font.contains(&format!("/BaseFont /{base}")),
                "{cid_font}"
            );
            let descriptor = &objects[&reference(cid_font, "/FontDescriptor")].0;
            assert!(
                descriptor.contains(&format!("/FontName /{base}")),
                "{descriptor}"
            );
            let (program_dict, program) = &objects[&reference(descriptor, "/FontFile2")];
            assert!(
                program_dict.contains(&format!("/Length1 {}", program.len())),
                "{program_dict}"
            );
            let subset = ttf_parser::Face::parse(program, 0).expect("the subset parses");
            assert_eq!(
                subset.number_of_glyphs() as usize,
                shown.len(),
                "{name} {file}: the glyphs embedded"
            );
            assert!(program.len() * 10 < whole_data.len(), "{name} {file}");

            let map: Vec<u16> = objects[&reference(cid_font, "/CIDToGIDMap")]
                .1
                .chunks(2)
                .map(|c| u16::from_be_bytes([c[0], c[1]]))
                .collect();
            let w = &cid_font[cid_font.find("/W [").unwrap() + 4..];
            let w: Vec<f64> = w[..w.rfind(']').unwrap()]
                .split(['[', ']', ' '])
                .filter(|t| !t.is_empty())
                .map(|t| t.parse().unwrap())
                .collect();
            let widths: BTreeMap<u16, f64> = w.chunks(2).map(|p| (p[0] as u16, p[1])).collect();
            let unicode = to_unicode(&objects[&reference(font, "/ToUnicode")].1);
            // Each CID shown has a width (the ToUnicode map also holds its code space range).
            for (cid, width) in &widths {
                let ch = unicode[cid].chars().next().unwrap();
                let printed = if ch == '⌀' { 'ø' } else { ch };
                let g = ttf_parser::GlyphId(whole.glyph_index(printed).map_or(0, |g| g.0));
                let s = ttf_parser::GlyphId(map[*cid as usize]);
                let advance = whole.glyph_hor_advance(g).unwrap();
                assert_eq!(subset.glyph_hor_advance(s), Some(advance), "{name} {ch:?}");
                assert_eq!(
                    subset.glyph_bounding_box(s),
                    whole.glyph_bounding_box(g),
                    "{name} {ch:?}"
                );
                let em = whole.units_per_em() as f64;
                assert!(
                    (width - advance as f64 * 1000.0 / em).abs() < 1e-3,
                    "{name} {ch:?}: /W"
                );
            }
        }
    }
}

/// ISSUE-1585-a and ISSUE-1536-a, the PDF's text layer: a supplied date and an overflowing value
/// are read back from the PDF, each whole and once, in the title block's own face.
#[test]
fn title_block_values_reach_the_pdf_text_layer() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, mut a) = model::analyse(&root.join("tests/fixtures/parts/plate.step")).unwrap();
    let json = std::fs::read_to_string(root.join("tests/fixtures/recognition/plate.json")).unwrap();
    model::recognise(&part, &mut a, &json).unwrap();
    // Too long for its cell even drawn at the smallest height (#221).
    let author = "ENGINEERING DESIGN AND MANUFACTURING REVIEW GROUP";
    let (date, long) = ("2026-09-11", format!("{author} / draftwright"));
    let long = long.as_str();
    let spec: Spec = serde_json::from_value(json!({ "sheet": { "size": "A2", "title": {
        "title": "FRAME", "date": date, "drawn_by": author } } }))
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    assert!(
        plan.problems
            .iter()
            .any(|p| p.code == "title_field_overflow")
    );
    let font =
        draftwright::text::Font::new(common::fonts("IBMPlexMono-Regular.ttf").unwrap()).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let text = pdf_text(&drawing.pdf(&common::fonts));
    for value in [date, long] {
        assert_eq!(text.iter().filter(|t| *t == value).count(), 1, "{value}");
        let run = drawing.text.iter().find(|t| t.text == value).unwrap();
        assert_eq!(run.font, draftwright::title_block::FONT);
    }
}

#[test]
fn general_notes_reach_the_pdf_text_layer() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, mut a) = model::analyse(&root.join("tests/fixtures/parts/plate.step")).unwrap();
    let json = std::fs::read_to_string(root.join("tests/fixtures/recognition/plate.json")).unwrap();
    model::recognise(&part, &mut a, &json).unwrap();
    let spec: Spec = serde_json::from_value(json!({ "notes": [
        { "id": "n:1", "text": "BREAK ALL SHARP EDGES" },
        { "id": "n:2", "text": "⌀6 H7 REAMED HOLES ARE INSPECTED WITH A GO / NO-GO GAUGE AFTER PLATING" } ] }))
    .unwrap();
    let plan = model::plan(&a, &spec).unwrap();
    let font =
        draftwright::text::Font::new(common::fonts("IBMPlexMono-Regular.ttf").unwrap()).unwrap();
    let drawing = model::render(&part, &a, &plan, &font).unwrap();
    let text = pdf_text(&drawing.pdf(&common::fonts));
    for row in [
        "GENERAL NOTES",
        "1  BREAK ALL SHARP EDGES",
        "2  ⌀6 H7 REAMED HOLES ARE INSPECTED WITH A GO /",
        "NO-GO GAUGE AFTER PLATING",
    ] {
        assert_eq!(
            text.iter().filter(|t| *t == row).count(),
            1,
            "{row}: {text:?}"
        );
    }
    let svg = svg_text(&drawing.svg());
    assert!(
        svg.iter().any(|t| t == "1  BREAK ALL SHARP EDGES"),
        "{svg:?}"
    );
}

/// Native entities replace the old glyph-curve DXF contract. Read with the Rust
/// crate alone so the normal test suite does not depend on Python or a CAD install.
#[test]
fn editable_dxf_preserves_text_measurements_blocks_and_groups() {
    use draftwright::output::{Curve, Item};
    use dxf::entities::EntityType as E;
    let drawing = plate(&|_| {});
    let bytes = drawing.dxf();
    assert_eq!(bytes, drawing.dxf(), "deterministic handles and metadata");
    let d = dxf::Drawing::load(&mut std::io::Cursor::new(&bytes)).unwrap();
    let all: Vec<_> = d
        .entities()
        .chain(d.blocks().flat_map(|b| b.entities.iter()))
        .collect();
    let mut strings = Vec::new();
    for e in &all {
        match &e.specific {
            E::Text(t) => strings.push(t.value.clone()),
            E::MText(t) => strings.push(t.text.clone()),
            E::Insert(i) => strings.extend(
                i.attributes()
                    .filter(|a| !a.value.is_empty())
                    .map(|a| a.value.clone()),
            ),
            _ => {}
        }
    }
    let escape = |s: &str| {
        s.replace('⌀', "%%c")
            .replace('°', "%%d")
            .replace('±', "%%p")
    };
    let mut expected: Vec<_> = drawing
        .text
        .iter()
        .chain(&drawing.sheet.dxf.labels)
        .map(|r| escape(&r.text))
        .collect();
    strings.sort();
    expected.sort();
    assert_eq!(
        strings, expected,
        "one editable native text entity per run, including attribute values"
    );
    let title = d
        .entities()
        .find_map(|e| match &e.specific {
            E::Insert(i) if i.name == "TITLE_BLOCK" => Some(i),
            _ => None,
        })
        .unwrap();
    let tags: BTreeSet<_> = title
        .attributes()
        .map(|a| a.attribute_tag.as_str())
        .collect();
    for tag in [
        "TITLE",
        "DRAWING_NUMBER",
        "MATERIAL",
        "SCALE",
        "DATE",
        "REVISION",
        "DESIGNED_BY",
    ] {
        assert!(tags.contains(tag), "{tag}");
    }
    assert!(
        d.blocks()
            .find(|b| b.name == "TITLE_BLOCK")
            .unwrap()
            .entities
            .iter()
            .any(|e| matches!(e.specific, E::AttributeDefinition(_)))
    );
    for view in &drawing.sheet.views {
        assert!(d.blocks().any(|b| b.name == view.name.replace(':', "_")));
    }
    // Native blocks retain the original view geometry, before centres/hatch are appended.
    for view in &drawing.sheet.dxf.views {
        let block = d
            .blocks()
            .find(|b| b.name == view.name.replace(':', "_"))
            .unwrap();
        for (item, entity) in drawing.layers[0].items[view.visible.clone()]
            .iter()
            .zip(&block.entities)
        {
            match (item, &entity.specific) {
                (Item::Edge(Curve::Line(a, b)), E::Line(line)) => {
                    assert!((line.p1.x - a[0]).abs() < 1e-10 && (line.p1.y - a[1]).abs() < 1e-10);
                    assert!((line.p2.x - b[0]).abs() < 1e-10 && (line.p2.y - b[1]).abs() < 1e-10);
                }
                (
                    Item::Edge(Curve::Conic {
                        centre,
                        radii,
                        t0,
                        t1,
                        ..
                    }),
                    E::Circle(circle),
                ) => {
                    assert!((t1 - t0).abs() > std::f64::consts::TAU - 1e-10);
                    assert!(
                        (circle.center.x - centre[0]).abs() < 1e-10
                            && (circle.center.y - centre[1]).abs() < 1e-10
                    );
                    assert!((circle.radius - radii[0]).abs() < 1e-10);
                }
                (Item::Edge(Curve::Conic { .. }), E::Arc(_) | E::Ellipse(_)) => {}
                (Item::Edge(Curve::Beziers(_) | Curve::Rational(_)), E::Spline(_)) => {}
                _ => panic!("view curve changed type: {item:?} -> {:?}", entity.specific),
            }
        }
        assert!(block.entities.len() >= view.visible.len());
    }
    let title_geometry = d.blocks().find(|b| b.name == "TITLE_BLOCK").unwrap();
    for e in title_geometry
        .entities
        .iter()
        .skip(4)
        .filter(|e| matches!(e.specific, E::Line(_)))
    {
        assert_eq!(
            e.common.lineweight_enum_value, 5,
            "title rules retain their original 0.05 mm weight"
        );
    }
    let mut dimensions = 0;
    for e in d.entities() {
        if let E::RotatedDimension(dim) = &e.specific {
            dimensions += 1;
            let base = &dim.dimension_base;
            let style = d
                .dim_styles()
                .find(|s| s.name == base.dimension_style_name)
                .unwrap();
            let block = d.blocks().find(|b| b.name == base.block_name).unwrap();
            assert!(block.name.starts_with("*D") && !block.entities.is_empty());
            let label = block
                .entities
                .iter()
                .find_map(|e| match &e.specific {
                    E::Text(t) => t.value.parse::<f64>().ok(),
                    _ => None,
                })
                .unwrap();
            let a = &dim.definition_point_2;
            let b = &dim.definition_point_3;
            let angle = dim.rotation_angle.to_radians();
            let measured = ((b.x - a.x) * angle.cos() + (b.y - a.y) * angle.sin()).abs();
            let text = block
                .entities
                .iter()
                .find_map(|e| {
                    if let E::Text(t) = &e.specific {
                        Some(t)
                    } else {
                        None
                    }
                })
                .unwrap();
            let delta = [
                base.text_mid_point.x - text.location.x,
                base.text_mid_point.y - text.location.y,
            ];
            let rise = -delta[0] * angle.sin() + delta[1] * angle.cos();
            assert!(
                (rise - text.text_height / 2.).abs() < 0.15,
                "DIMENSION text point is the ink centre, not its baseline"
            );

            assert!(
                (measured * style.dimension_linear_measurement_scale_factor - label).abs() < 1e-5
            );
            assert_eq!(base.text, "<>", "plain values regenerate from geometry");
        }
    }
    assert!(dimensions >= 7);
    assert!(
        d.objects()
            .any(|o| matches!(o.specific, dxf::objects::ObjectType::Group(_)))
    );
    for name in [
        "A-OUTL",
        "A-HIDN",
        "A-CNTR",
        "A-DIMS",
        "A-TEXT",
        "A-PMI",
        "A-HATCH",
        "A-CUTP",
        "A-BORD",
        "Defpoints",
    ] {
        assert!(d.layers().any(|l| l.name == name));
    }
    // CAD installations commonly lack Plex; the named Plex styles explicitly
    // request these installed alternatives instead (round 2 interoperability).
    for font in ["Courier New.ttf", "Arial.ttf"] {
        assert!(d.styles().any(|s| s.primary_font_file_name == font));
    }
    // A clockwise ellipse with the longer radius on its second axis must retain
    // the same arc after DXF's mandatory major-axis normalization.
    let mut elliptical = drawing.clone();
    let curve = Curve::Conic {
        centre: [12., 34.],
        radii: [2., 4.],
        x_axis: [1., 0.],
        y_axis: [0., -1.],
        t0: 0.4,
        t1: 1.2,
    };
    elliptical.layers[0].items.push(Item::Edge(curve.clone()));
    let parsed = dxf::Drawing::load(&mut std::io::Cursor::new(elliptical.dxf())).unwrap();
    let e = parsed
        .entities()
        .find_map(|e| {
            if let E::Ellipse(e) = &e.specific {
                Some(e)
            } else {
                None
            }
        })
        .unwrap();
    let at = |t: f64| {
        [
            e.center.x + e.major_axis.x * t.cos() - e.major_axis.y * e.minor_axis_ratio * t.sin(),
            e.center.y + e.major_axis.y * t.cos() + e.major_axis.x * e.minor_axis_ratio * t.sin(),
        ]
    };
    for (a, b) in [
        (at(e.start_parameter), curve.end()),
        (at(e.end_parameter), curve.start()),
    ] {
        assert!((a[0] - b[0]).hypot(a[1] - b[1]) < 1e-10);
    }
    // A leader's sampled rim may be slightly off the analytic circle. Native
    // dimension definitions still measure the exact bore, rather than that sample.
    let mut sampled = drawing.clone();
    let callout = sampled
        .sheet
        .dxf
        .dimensions
        .values_mut()
        .find(|d| d.label.contains("THRU"))
        .unwrap();
    callout.placed.as_mut().unwrap().arrows[0][0][0] += 0.0001;
    let parsed = dxf::Drawing::load(&mut std::io::Cursor::new(sampled.dxf())).unwrap();
    let dim = parsed
        .entities()
        .find_map(|e| {
            if let E::DiameterDimension(d) = &e.specific {
                Some(d)
            } else {
                None
            }
        })
        .unwrap();
    let a = &dim.dimension_base.definition_point_1;
    let b = &dim.definition_point_2;
    let factor = parsed
        .dim_styles()
        .find(|s| s.name == dim.dimension_base.dimension_style_name)
        .unwrap()
        .dimension_linear_measurement_scale_factor;
    assert!(((b.x - a.x).hypot(b.y - a.y) * factor - 6.).abs() < 1e-8);
    // Isolated projected poles are not valid degree-zero DXF splines.
    let mut with_points = drawing.clone();
    with_points.layers[0].items.extend([
        Item::Edge(Curve::Beziers(vec![vec![[12., 34.]]])),
        Item::Edge(Curve::Rational(vec![vec![[12., 34., 1.]]])),
        Item::Edge(Curve::Rational(vec![
            (0..15)
                .map(|i| [12. + i as f64, 34. + (i as f64 / 14.).powi(2), 1.])
                .collect(),
        ])),
    ]);
    let parsed = dxf::Drawing::load(&mut std::io::Cursor::new(with_points.dxf())).unwrap();
    for e in parsed
        .entities()
        .chain(parsed.blocks().flat_map(|b| b.entities.iter()))
    {
        if let E::Spline(s) = &e.specific {
            assert!((1..=10).contains(&s.degree_of_curve));
            assert!(s.control_points.len() > s.degree_of_curve as usize);
        }
    }
    // Poison all glyph outlines. No such geometry may appear in the native export.
    let mut poisoned = drawing.clone();
    let mut count = 0;
    for layer in &mut poisoned.layers {
        for i in &mut layer.items {
            match i {
                Item::Glyph(w, _) => {
                    *w = vec![vec![Curve::Line([9000., 9000.], [9999., 9999.])]];
                    count += 1;
                }
                Item::TextStroke(c, _) => *c = Curve::Line([9000., 9000.], [9999., 9999.]),
                _ => {}
            }
        }
    }
    assert!(count > 0);
    let check = dxf::Drawing::load(&mut std::io::Cursor::new(poisoned.dxf())).unwrap();
    assert_eq!(
        all.len(),
        check.entities().count() + check.blocks().map(|b| b.entities.len()).sum::<usize>()
    );
    for e in check
        .entities()
        .chain(check.blocks().flat_map(|b| b.entities.iter()))
    {
        match &e.specific {
            E::Line(l) => assert!(
                [&l.p1, &l.p2]
                    .iter()
                    .all(|p| p.x.abs() < 1000. && p.y.abs() < 1000.)
            ),
            E::Spline(s) => assert!(
                s.control_points
                    .iter()
                    .all(|p| p.x.abs() < 1000. && p.y.abs() < 1000.)
            ),
            _ => {}
        }
    }
}

/// Turned-profile labels use the historical slashed-o spelling. It carries the
/// same diameter semantics and control code as the explicit diameter sign.
#[test]
fn turned_profile_diameters_preserve_native_witness_dimensions() {
    use dxf::entities::EntityType as E;
    let (plan, files) = draftwright::api::render_from(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/parts/turned.step"),
        draftwright::api::Features::Native,
        &Spec::default(),
        &["dxf"],
        150.,
    )
    .unwrap();
    let d = dxf::Drawing::load(&mut std::io::Cursor::new(&files[0].1)).unwrap();
    let dim = d
        .entities()
        .find_map(|e| {
            if let E::RotatedDimension(dim) = &e.specific
                && dim.dimension_base.text == "%%c<>"
            {
                Some(dim)
            } else {
                None
            }
        })
        .expect("the overall turned diameter");
    let a = &dim.definition_point_2;
    let b = &dim.definition_point_3;
    let factor = d
        .dim_styles()
        .find(|s| s.name == dim.dimension_base.dimension_style_name)
        .unwrap()
        .dimension_linear_measurement_scale_factor;
    assert!(((a.x - b.x).hypot(a.y - b.y) * factor - 30.).abs() < 1e-6);
    let block = d
        .blocks()
        .find(|b| b.name == dim.dimension_base.block_name)
        .unwrap();
    assert!(
        block
            .entities
            .iter()
            .any(|e| matches!(&e.specific,E::Text(t) if t.value=="%%c30"))
    );
    // A live field is required: editing the witnesses must update the displayed diameter.
    assert_eq!(dim.dimension_base.text, "%%c<>");
    let wanted = plan
        .dimensions
        .iter()
        .find(|d| d.id == "d:envelope/od.diameter")
        .unwrap()
        .placed
        .as_ref()
        .unwrap()
        .arrows[0][0];
    let at = &dim.dimension_base.definition_point_1;
    assert!(
        (at.x - wanted[0]).hypot(at.y - wanted[1]) < 1e-10,
        "the profile dimension line stays where it was drawn"
    );
}

fn dxf_pairs(bytes: &[u8]) -> Vec<(i32, String)> {
    let text = std::str::from_utf8(bytes).unwrap();
    text.lines()
        .collect::<Vec<_>>()
        .as_chunks::<2>()
        .0
        .iter()
        .map(|p| (p[0].trim().parse().unwrap(), p[1].to_owned()))
        .collect()
}
fn dxf_header(bytes: &[u8], name: &str) -> String {
    let pairs = dxf_pairs(bytes);
    pairs
        .windows(2)
        .find(|p| p[0] == (9, name.to_owned()))
        .unwrap()[1]
        .1
        .clone()
}
#[test]
fn dxf_has_r2000_document_structure_and_sheet_viewport() {
    let drawing = plate(&|_| {});
    let bytes = drawing.dxf();
    let pairs = dxf_pairs(&bytes);
    let tables: BTreeSet<_> = pairs
        .windows(2)
        .filter(|p| p[0] == (0, "TABLE".into()))
        .map(|p| p[1].1.as_str())
        .collect();
    for name in [
        "VPORT",
        "LTYPE",
        "LAYER",
        "STYLE",
        "VIEW",
        "UCS",
        "APPID",
        "DIMSTYLE",
        "BLOCK_RECORD",
    ] {
        assert!(tables.contains(name), "{name}");
    }
    assert!(pairs.contains(&(2, "CLASSES".into())));
    assert_eq!(
        pairs.iter().filter(|p| **p == (278, "46".into())).count(),
        pairs
            .iter()
            .filter(|p| **p == (0, "DIMSTYLE".into()))
            .count(),
        "every dimstyle preserves the drawing's decimal point"
    );
    assert_eq!(
        pairs.iter().filter(|p| **p == (0, "LAYOUT".into())).count(),
        2
    );
    let seed = u64::from_str_radix(&dxf_header(&bytes, "$HANDSEED"), 16).unwrap();
    let max = pairs
        .windows(2)
        .filter(|p| [5, 105].contains(&p[1].0) && p[0] != (9, "$HANDSEED".into()))
        .map(|p| u64::from_str_radix(&p[1].1, 16).unwrap())
        .max()
        .unwrap();
    assert!(seed > max);
    let d = dxf::Drawing::load(&mut std::io::Cursor::new(&bytes)).unwrap();
    assert!(d.blocks().any(|b| b.name == "*Model_Space"));
    assert!(d.blocks().any(|b| b.name == "*Paper_Space"));
    assert!(
        d.view_ports()
            .any(|v| v.name.eq_ignore_ascii_case("*ACTIVE"))
    );
    assert_eq!(
        d.objects()
            .filter(|o| matches!(o.specific, dxf::objects::ObjectType::Layout(_)))
            .count(),
        2
    );
}
/// QCAD regenerates dimensions and uses literal white for ACI 7 in its CLI.
/// These contracts must hold without recomputing empty dimension blocks.
#[test]
fn dxf_cad_readers_get_visible_blocks_and_full_dimension_settings() {
    let drawing = plate(&|_| {});
    let bytes = drawing.dxf();
    let parsed = dxf::Drawing::load(&mut std::io::Cursor::new(&bytes)).unwrap();
    let pairs = dxf_pairs(&bytes);
    let starts: Vec<_> = pairs
        .iter()
        .enumerate()
        .filter_map(|(i, p)| (p.0 == 0).then_some(i))
        .chain(std::iter::once(pairs.len()))
        .collect();
    let records: Vec<_> = starts.windows(2).map(|w| &pairs[w[0]..w[1]]).collect();
    let value = |r: &[(i32, String)], code| r.iter().find(|p| p.0 == code).unwrap().1.clone();
    let records_of = |kind: &str| {
        records
            .iter()
            .copied()
            .filter(|r| r[0].1 == kind)
            .collect::<Vec<_>>()
    };
    for layer in ["0", "A-OUTL", "A-CNTR", "A-BORD"] {
        let r = records_of("LAYER")
            .into_iter()
            .find(|r| value(r, 2) == layer)
            .unwrap();
        assert_eq!(value(r, 420), "0", "{layer} is explicit black on white");
    }
    let mono = records_of("STYLE")
        .into_iter()
        .find(|r| value(r, 2) == "IBMPlexMono-Regular")
        .unwrap();
    let width = value(mono, 41).parse::<f64>().unwrap();
    assert!(
        (0.81..0.83).contains(&width),
        "Courier cap/advance compensation"
    );
    for text in records_of("TEXT") {
        if value(text, 7) == "IBMPlexMono-Regular" {
            assert_eq!(value(text, 41).parse::<f64>().unwrap(), width);
        }
    }
    let block_records = records_of("BLOCK_RECORD");
    for r in &block_records {
        let name = value(r, 2);
        let layout = matches!(name.as_str(), "*Model_Space" | "*Paper_Space");
        assert_eq!(r.iter().any(|p| p.0 == 340), layout, "{name}");
        let block = records_of("BLOCK")
            .into_iter()
            .find(|b| value(b, 2) == name)
            .unwrap();
        assert_eq!(value(block, 330), value(r, 5), "{name}: BLOCK owner");
        let begin = records.iter().position(|b| *b == block).unwrap();
        let end = records[begin + 1..]
            .iter()
            .find(|b| b[0].1 == "ENDBLK")
            .unwrap();
        assert_eq!(value(end, 330), value(r, 5), "{name}: ENDBLK owner");
    }
    for v in &drawing.sheet.dxf.views {
        // View IDs contain ':', which DXF symbol-table names cannot contain.
        let b = parsed
            .blocks()
            .find(|b| b.name == v.name.replace(':', "_"))
            .unwrap();
        assert!(!b.entities.is_empty(), "{} has geometry", v.name);
    }
    for (name, expected) in [
        ("$DIMTXT", 3. * 0.698),
        ("$DIMASZ", 2.7),
        ("$DIMLFAC", 1.),
        ("$DIMSCALE", 1.),
        ("$DIMLUNIT", 2.),
        ("$DIMDEC", 3.),
        ("$DIMDSEP", 46.),
    ] {
        assert_eq!(dxf_header(&bytes, name).parse::<f64>().unwrap(), expected);
    }
    let dimensions = records_of("DIMENSION");
    assert!(dimensions.len() >= 7);
    for r in dimensions {
        assert!(value(r, 1).contains("<>"));
        let name = value(r, 3);
        assert!(name.starts_with("DW-") && name.contains("to"), "{name}");
        assert!(!name.contains(['[', ']', '_', ' ']), "{name}");
        let style = parsed.dim_styles().find(|s| s.name == name).unwrap();
        let acad = r.iter().position(|p| *p == (1001, "ACAD".into())).unwrap();
        let overrides = &r[acad..];
        assert!(overrides.contains(&(1000, "DSTYLE".into())));
        for (code, expected) in [
            (144, style.dimension_linear_measurement_scale_factor),
            (140, 3. * 0.698),
            (41, 2.7),
            (40, 1.),
        ] {
            let at = overrides
                .iter()
                .position(|p| *p == (1070, code.to_string()))
                .unwrap();
            assert_eq!(overrides[at + 1], (1040, expected.to_string()));
        }
        let block = parsed.blocks().find(|b| b.name == value(r, 2)).unwrap();
        assert!(
            block.entities.iter().any(|e| matches!(
                &e.specific, dxf::entities::EntityType::Text(t)
                    if t.value.chars().any(|c| c.is_ascii_digit())
            )),
            "cached dimension contains its readable value"
        );
    }
}
#[test]
fn dxf_multiline_rotation_is_degrees_and_single_line_symbols_are_text() {
    use draftwright::output::{HAlign, TextRun, VAlign};
    use dxf::entities::EntityType as E;
    let mut drawing = plate(&|_| {});
    for (text, rotation) in [("FIRST ↧6\nSECOND ⌴8", 37.), ("−3 × ⌀6 ±0.1 ° ⌵", 0.)] {
        drawing.text.push(TextRun {
            text: text.into(),
            at: [25., 100.],
            size: 2.5,
            rotation,
            font: "IBMPlexMono-Regular.ttf".into(),
            h_align: HAlign::Left,
            v_align: VAlign::Baseline,
        });
    }
    let d = dxf::Drawing::load(&mut std::io::Cursor::new(drawing.dxf())).unwrap();
    let m = d
        .entities()
        .find_map(|e| {
            if let E::MText(t) = &e.specific {
                t.text.contains("FIRST").then_some(t)
            } else {
                None
            }
        })
        .unwrap();
    assert_eq!(m.rotation_angle, 37.);
    assert_eq!(m.text, "FIRST ↧6\\PSECOND ⌴8");
    let t = d
        .entities()
        .find_map(|e| {
            if let E::Text(t) = &e.specific {
                t.value.starts_with("-3").then_some(t)
            } else {
                None
            }
        })
        .unwrap();
    assert_eq!(t.value, "-3 × %%c6 %%p0.1 %%d ⌵");
    assert!(
        !String::from_utf8(drawing.dxf())
            .unwrap()
            .contains("\\fArial")
    );
}
#[test]
fn dxf_pmi_hatch_and_multisheet_attributes_are_native() {
    use dxf::entities::EntityType as E;
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (_, files) = draftwright::api::render_from(
        &root.join("tests/fixtures/parts/editor_pmi.step"),
        draftwright::api::Features::Native,
        &Spec::default(),
        &["dxf"],
        150.,
    )
    .unwrap();
    let d = dxf::Drawing::load(&mut std::io::Cursor::new(&files[0].1)).unwrap();
    assert!(
        d.entities().any(|e| e.common.layer == "A-PMI"
            && matches!(&e.specific,E::Text(t) if !t.value.is_empty()))
    );
    let pairs = dxf_pairs(&files[0].1);
    assert!(
        pairs
            .iter()
            .any(|p| p.0 == 3 && p.1.starts_with("VIEW_") && p.1.ends_with("_ANNOT"))
    );
    let section: Spec =
        serde_json::from_value(json!({"views":{"sections":[{"id":"A","through":"f:hole:f6"}]}}))
            .unwrap();
    let (_, files) = draftwright::api::render_from(
        &root.join("tests/fixtures/parts/plate.step"),
        draftwright::api::Features::Native,
        &section,
        &["dxf"],
        150.,
    )
    .unwrap();
    let pairs = dxf_pairs(&files[0].1);
    assert!(
        pairs.contains(&(0, "HATCH".into())),
        "the closed section region stays a HATCH"
    );
    assert!(pairs.contains(&(2, "ANSI31".into())));
    assert!(
        pairs
            .iter()
            .any(|(code, text)| *code == 3 && text.ends_with("/cutting_plane"))
    );
    // The crate can still read the document around the unsupported HATCH entity.
    let section = dxf::Drawing::load(&mut std::io::Cursor::new(&files[0].1)).unwrap();
    let groups: Vec<_> = section
        .objects()
        .filter_map(|o| {
            if let dxf::objects::ObjectType::Group(g) = &o.specific {
                Some(g)
            } else {
                None
            }
        })
        .collect();
    let cutting = groups
        .iter()
        .find(|g| g.description.ends_with("/cutting_plane"))
        .unwrap();
    assert!(!cutting.__entities_handle.is_empty());
    assert!(
        groups.iter().any(|g| g.description.starts_with("VIEW_")
            && cutting
                .__entities_handle
                .iter()
                .all(|h| g.__entities_handle.contains(h))),
        "moving the source view's annotation group must move its cutting plane"
    );
    let multi:Spec=serde_json::from_value(json!({"sheet":{"size":"A4","scale":"1:1","title":{"title":"MULTI"}},"sheets":{"max":2},"views":{"iso":{"sheet":2}}})).unwrap();
    let (_, files) = draftwright::api::render_from(
        &root.join("tests/fixtures/parts/plate.step"),
        draftwright::api::Features::Native,
        &multi,
        &["dxf"],
        150.,
    )
    .unwrap();
    assert_eq!(files.len(), 2);
    let mut fingerprints = BTreeSet::new();
    for (_, bytes) in &files {
        let d = dxf::Drawing::load(&mut std::io::Cursor::new(bytes)).unwrap();
        let title = d
            .entities()
            .find_map(|e| {
                if let E::Insert(i) = &e.specific {
                    (i.name == "TITLE_BLOCK").then_some(i)
                } else {
                    None
                }
            })
            .unwrap();
        assert!(
            title
                .attributes()
                .any(|a| a.attribute_tag == "TITLE" && a.value == "MULTI")
        );
        assert!(title.attributes().any(|a| a.attribute_tag == "SCALE"));
        fingerprints.insert(dxf_header(bytes, "$FINGERPRINTGUID"));
    }
    assert_eq!(fingerprints.len(), 2);
}
#[test]
fn dxf_shaded_pictorial_is_visible_edges_without_mesh_wedges() {
    use dxf::entities::EntityType as E;
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let (part, a) = model::analyse_native(&root.join("tests/fixtures/parts/plate.step")).unwrap();
    let spec: Spec = serde_json::from_value(json!({"views":{"iso":{"style":"shaded"}}})).unwrap();
    let p = model::plan(&a, &spec).unwrap();
    let font = draftwright::text::Font::new(draftwright::api::MONO.to_vec()).unwrap();
    let drawing = model::render(&part, &a, &p, &font).unwrap();
    assert!(
        drawing
            .layers
            .iter()
            .any(|l| l.name.starts_with("shaded-") && !l.items.is_empty())
    );
    let d = dxf::Drawing::load(&mut std::io::Cursor::new(drawing.dxf())).unwrap();
    let iso = d.blocks().find(|b| b.name == "v_iso").unwrap();
    assert!(!iso.entities.is_empty());
    assert!(
        iso.entities.iter().all(
            |e| e.common.layer == "A-OUTL" && !matches!(e.specific, E::Solid(_) | E::Face3D(_))
        )
    );
    assert!(
        !d.entities()
            .chain(d.blocks().flat_map(|b| &b.entities))
            .any(|e| e.common.layer == "A-OUTL" && matches!(e.specific, E::Solid(_)))
    );
}

#[test]
fn dxf_tolerances_and_compound_nominals_stay_live() {
    use dxf::entities::EntityType as E;
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    for (band, up, lo) in [("±0.05", 0.05, 0.05), ("+0.1 -0.02", 0.1, 0.02)] {
        let spec: Spec = serde_json::from_value(
            json!({"dimensions":{"d:envelope/width.length":{"tolerance":band}}}),
        )
        .unwrap();
        let (_, files) = draftwright::api::render_from(
            &root.join("tests/fixtures/parts/plate.step"),
            draftwright::api::Features::Native,
            &spec,
            &["dxf"],
            150.,
        )
        .unwrap();
        let d = dxf::Drawing::load(&mut std::io::Cursor::new(&files[0].1)).unwrap();
        let mut found = false;
        for e in d.entities() {
            if let E::RotatedDimension(dim) = &e.specific {
                let st = d
                    .dim_styles()
                    .find(|s| s.name == dim.dimension_base.dimension_style_name)
                    .unwrap();
                if st.generate_dimension_tolerances {
                    assert_eq!(dim.dimension_base.text, "<>");
                    assert!((st.dimension_plus_tolerance - up).abs() < 1e-12);
                    assert!((st.dimension_minus_tolerance - lo).abs() < 1e-12);
                    found = true;
                }
            }
            if let E::DiameterDimension(dim) = &e.specific {
                assert_eq!(dim.dimension_base.text, "4× <> THRU");
            }
        }
        assert!(found, "{band} must be dimension tolerance data");
    }
}

#[test]
fn dxf_dimension_axis_is_independent_of_horizontal_text() {
    use draftwright::output::{Curve, HAlign, Item, VAlign};
    use dxf::entities::EntityType as E;
    for angle in [30_f64, 90.] {
        let mut drawing = plate(&|_| {});
        let (id, mut dimension) = drawing
            .sheet
            .dxf
            .dimensions
            .iter()
            .find(|(_, d)| d.placed.as_ref().is_some_and(|p| p.arrows.len() == 2))
            .map(|(id, d)| (id.clone(), d.clone()))
            .unwrap();
        let (sin, cos) = angle.to_radians().sin_cos();
        let a = [60., 60.];
        let b = [60. + 10. * cos, 60. + 10. * sin];
        let offset = |p: [f64; 2], distance: f64| [p[0] - distance * sin, p[1] + distance * cos];
        let arrows = [
            [offset(a, 5.), offset(a, 4.)],
            [offset(b, 5.), offset(b, 4.)],
        ];
        let centre = [
            (arrows[0][0][0] + arrows[1][0][0]) / 2.,
            (arrows[0][0][1] + arrows[1][0][1]) / 2.,
        ];
        let p = dimension.placed.as_mut().unwrap();
        p.lines = vec![
            [offset(a, 1.), offset(a, 7.)],
            [offset(b, 1.), offset(b, 7.)],
            [arrows[0][0], arrows[1][0]],
        ];
        p.arrows = arrows.to_vec();
        p.rotation = 0.;
        p.label_at = centre;
        p.label_box = [
            centre[0] - 3.,
            centre[1] - 1.,
            centre[0] + 3.,
            centre[1] + 1.,
        ];
        dimension.value = 10.;
        dimension.label = "10".into();
        dimension.primary = Some(0..2);
        dimension.diameter = false;
        dimension.radial = false;
        drawing
            .sheet
            .frames
            .get_mut(dimension.view.trim_start_matches("v:"))
            .unwrap()
            .scale = 1.;
        for layer in &mut drawing.layers {
            layer.items.clear();
        }
        drawing.layers[0].items.push(Item::Edge(Curve::Line(a, b)));
        drawing.layers[2].items = p
            .lines
            .iter()
            .map(|[a, b]| Item::Edge(Curve::Line(*a, *b)))
            .collect();
        drawing.sheet.dxf.views.clear();
        drawing.sheet.dxf.annotations = BTreeMap::from([(id.clone(), 0..p.lines.len())]);
        drawing.sheet.dxf.furniture.clear();
        drawing.sheet.dxf.title_fields.clear();
        drawing.sheet.dxf.title = 0..0;
        drawing.sheet.dxf.labels.clear();
        drawing.sheet.dxf.dimensions = BTreeMap::from([(id.clone(), dimension)]);
        drawing.annotations.retain(|a| a.name == id);
        drawing.annotations[0].runs = vec![0];
        let mut run = drawing.text[0].clone();
        run.text = "10".into();
        run.at = centre;
        run.rotation = 0.;
        run.h_align = HAlign::Center;
        run.v_align = VAlign::Middle;
        drawing.text = vec![run];
        let bytes = drawing.dxf();
        let d = dxf::Drawing::load(&mut std::io::Cursor::new(&bytes)).unwrap();
        let dim = d
            .entities()
            .find_map(|e| {
                if let E::RotatedDimension(dim) = &e.specific {
                    Some(dim)
                } else {
                    None
                }
            })
            .unwrap();
        let base = &dim.dimension_base;
        assert_eq!(base.dimension_type as i32, if angle == 30. { 1 } else { 0 });
        assert!((dim.rotation_angle - angle).abs() < 1e-10);
        let a = &dim.definition_point_2;
        let b = &dim.definition_point_3;
        assert_eq!(base.text_rotation_angle, 0.);
        assert!(((b.x - a.x).hypot(b.y - a.y) - 10.).abs() < 1e-10);
        if let Some(dir) = std::env::var_os("DRAFTWRIGHT_DXF_PROBE_DIR") {
            let path = Path::new(&dir).join(format!("synthetic_axis_{angle}"));
            std::fs::write(path.with_extension("dxf"), bytes).unwrap();
            std::fs::write(path.with_extension("svg"), drawing.svg()).unwrap();
        }
    }
}
