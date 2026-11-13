//! A feature control frame is one native TOLERANCE in the DXF, and no text ink is geometry:
//! over the quality parts and the NIST PMI fixtures, on the drawing model and the DXF text.
//! Whether ink belongs to a frame is how it was built (`gdt::frame`), never where it lies.

#[path = "common/cache.rs"]
mod cache;
#[path = "common/corpus.rs"]
mod corpus;
#[path = "common/support.rs"]
mod support;

use std::collections::BTreeMap;
use std::io::Read as _;
use std::path::Path;

use draftwright::model::{self, Plan};
use draftwright::output::{Drawing, Item, Kind};

/// gdt.shx's letters, written out here from AutoCAD's font, independently of the writer.
const GDT: &[(char, char)] = &[
    ('a', '∠'),
    ('b', '⟂'),
    ('c', '⏥'),
    ('d', '⌓'),
    ('e', '○'),
    ('f', '∥'),
    ('g', '⌭'),
    ('h', '↗'),
    ('i', '⌯'),
    ('j', '⌖'),
    ('k', '⌒'),
    ('l', 'Ⓛ'),
    ('m', 'Ⓜ'),
    ('n', '⌀'),
    ('p', 'Ⓟ'),
    ('r', '◎'),
    ('s', 'Ⓢ'),
    ('t', '⌰'),
    ('u', '⏤'),
];

/// A TOLERANCE's text back to its cells. AutoCAD's string is positional (six slots: symbol,
/// tolerance 1, tolerance 2, datums 1 to 3), so the empty slots are dropped to compare with
/// the plan's cells.
fn decode(s: &str) -> Vec<String> {
    s.split("%%v")
        .filter(|cell| !cell.is_empty())
        .map(|cell| {
            let mut out = String::new();
            let mut rest = cell;
            while let Some(i) = rest.find("{\\Fgdt;") {
                out.push_str(&rest[..i]);
                let g = rest[i + 7..].chars().next().unwrap();
                out.push(
                    GDT.iter()
                        .find(|(l, _)| *l == g)
                        .expect("a mapped letter")
                        .1,
                );
                rest = &rest[i + 7 + g.len_utf8() + 1..];
            }
            out.push_str(rest);
            out.replace("%%c", "⌀").replace('X', "×")
        })
        .collect()
}

/// What a cell reads as once the symbols with no gdt glyph are spelled out.
fn spelled(cell: &str) -> String {
    cell.replace('Ⓕ', "(F)").replace('×', "X")
}

struct Entity {
    kind: String,
    tags: Vec<(i32, String)>,
}

fn entities(bytes: &[u8]) -> Vec<Entity> {
    let text = std::str::from_utf8(bytes).unwrap();
    let mut out: Vec<Entity> = Vec::new();
    for p in text.lines().collect::<Vec<_>>().as_chunks::<2>().0 {
        let code: i32 = p[0].trim().parse().unwrap();
        if code == 0 {
            out.push(Entity {
                kind: p[1].to_owned(),
                tags: Vec::new(),
            });
        } else if let Some(e) = out.last_mut() {
            e.tags.push((code, p[1].to_owned()));
        }
    }
    out
}

fn tag(e: &Entity, code: i32) -> Option<&str> {
    e.tags.iter().find(|t| t.0 == code).map(|t| t.1.as_str())
}

fn text_of(e: &Entity) -> String {
    // A TOLERANCE's group 3 is its dimension style; an MTEXT's are its leading chunks.
    e.tags
        .iter()
        .filter(|t| t.0 == 1 || (t.0 == 3 && e.kind != "TOLERANCE"))
        .map(|t| t.1.as_str())
        .collect()
}

/// What one sheet held: its TOLERANCE strings as written, and how many of its frames carry a
/// count over the box (`lead > 0`) or fills on the face (`fills > 0`).
#[derive(Default)]
struct Found {
    written: Vec<String>,
    counted: usize,
    filled: usize,
}

fn check(name: &str, drawing: &Drawing, plan: &Plan) -> Found {
    let bytes = drawing.dxf();
    // (a) Text ink never reaches the DXF as geometry: emptying it leaves every geometry entity.
    let mut bare = drawing.clone();
    for item in bare.layers.iter_mut().flat_map(|l| &mut l.items) {
        if let Item::Glyph(_, r) | Item::TextStroke(_, r) | Item::FrameText(_, r) = item {
            *item = Item::Glyph(Vec::new(), *r);
        }
    }
    let all = entities(&bytes);
    let geometry = |all: &[Entity]| {
        let mut n = BTreeMap::new();
        for e in all.iter().filter(|e| {
            matches!(
                e.kind.as_str(),
                "LINE" | "CIRCLE" | "ARC" | "ELLIPSE" | "SPLINE" | "SOLID" | "HATCH" | "LWPOLYLINE"
            )
        }) {
            *n.entry(e.kind.clone()).or_insert(0) += 1;
        }
        n
    };
    assert_eq!(
        geometry(&entities(&bare.dxf())),
        geometry(&all),
        "{name}: text ink reached the DXF"
    );
    // The frames on the sheet, as the plan states them.
    let mut frames: Vec<Vec<String>> = drawing
        .sheet
        .items
        .iter()
        .filter(|i| i.kind == Kind::Gdt)
        .filter_map(|i| plan.gdt.iter().find(|g| g.id == i.name))
        .filter(|g| g.kind == "control_frame")
        .map(|g| g.cells.iter().map(|c| spelled(c)).collect())
        .collect();
    // (c) One TOLERANCE each, its cells the plan's.
    let raw: Vec<String> = all
        .iter()
        .filter(|e| e.kind == "TOLERANCE")
        .map(text_of)
        .collect();
    for t in &raw {
        // Six slots, always, up to three datums.
        let n = t.matches("%%v").count();
        assert!(n == 5 || decode(t).len() > 5, "{name}: not positional: {t}");
    }
    let mut written: Vec<Vec<String>> = raw.iter().map(|t| decode(t)).collect();
    frames.sort();
    written.sort();
    assert_eq!(written, frames, "{name}: TOLERANCE cells");
    // None of a frame's own ink is loose linework.
    let lines: Vec<[f64; 4]> = all
        .iter()
        .filter(|e| e.kind == "LINE")
        .map(|e| {
            [10, 20, 11, 21].map(|c| tag(e, c).and_then(|v| v.parse().ok()).unwrap_or(f64::NAN))
        })
        .collect();
    for g in plan
        .gdt
        .iter()
        .filter(|g| g.kind == "control_frame")
        .filter(|g| drawing.sheet.items.iter().any(|i| i.name == g.id))
    {
        for [a, b] in &g.placed.as_ref().unwrap().strokes {
            let near = |l: &[f64; 4], p: [f64; 2], q: [f64; 2]| {
                (l[0] - p[0]).abs()
                    + (l[1] - p[1]).abs()
                    + (l[2] - q[0]).abs()
                    + (l[3] - q[1]).abs()
                    < 1e-6
            };
            assert!(
                !lines.iter().any(|l| near(l, *a, *b) || near(l, *b, *a)),
                "{name}: {} has a loose stroke",
                g.id
            );
        }
    }
    // (b) Every run is written once: as text, or as a compartment of its TOLERANCE. The
    // title block's own fields and labels are written beside the runs (an ATTRIB per field,
    // which stands for the run at its place, when there is one).
    let cells: usize = written.iter().map(Vec::len).sum();
    let texts = all
        .iter()
        .filter(|e| matches!(e.kind.as_str(), "TEXT" | "MTEXT" | "ATTRIB"))
        .count();
    let dxf = &drawing.sheet.dxf;
    let field_runs = drawing
        .text
        .iter()
        .filter(|r| {
            dxf.title_fields
                .iter()
                .any(|(_, f)| (f.at[0] - r.at[0]).abs() < 1e-6 && (f.at[1] - r.at[1]).abs() < 1e-6)
        })
        .count();
    assert_eq!(
        texts + cells + field_runs,
        drawing.text.len() + dxf.labels.len() + dxf.title_fields.len(),
        "{name}: text entities {texts} + cells {cells} vs runs {}",
        drawing.text.len(),
    );
    for c in written.iter().flatten().filter(|c| c.contains('(')) {
        eprintln!("{name}: a symbol with no gdt glyph, written as text: {c}");
    }
    // (d) Nothing written is a character a CAD font would show as `?`.
    for e in &all {
        if matches!(
            e.kind.as_str(),
            "TEXT" | "MTEXT" | "ATTRIB" | "ATTDEF" | "TOLERANCE" | "DIMENSION"
        ) {
            let t = text_of(e);
            assert!(t.is_ascii(), "{name}: {} has {t:?}", e.kind);
        }
    }
    // The arcs a frame owns (zone, material, characteristic) are the entity's; the rings at
    // its bend (all-around, all-over) are drawn as circles.
    let circles: Vec<[f64; 3]> = all
        .iter()
        .filter(|e| e.kind == "CIRCLE")
        .map(|e| [10, 20, 40].map(|c| tag(e, c).and_then(|v| v.parse().ok()).unwrap_or(f64::NAN)))
        .collect();
    let mut found = Found::default();
    for g in plan
        .gdt
        .iter()
        .filter(|g| g.kind == "control_frame")
        .filter(|g| drawing.sheet.items.iter().any(|i| i.name == g.id))
    {
        let p = g.placed.as_ref().unwrap();
        let rings: usize = g
            .modifiers
            .iter()
            .map(|m| if m == "all_over" { 2 } else { 1 })
            .sum();
        let has = |a: &[f64; 5]| {
            circles.iter().any(|c| {
                (c[0] - a[0]).abs() < 1e-6
                    && (c[1] - a[1]).abs() < 1e-6
                    && (c[2] - a[2]).abs() < 1e-6
            })
        };
        let n = p.arcs.len();
        for a in &p.arcs[..n - rings] {
            assert!(!has(a), "{name}: {} has a loose frame arc", g.id);
        }
        for a in &p.arcs[n - rings..] {
            assert!(has(a), "{name}: {} lost a bend ring", g.id);
        }
        found.counted += usize::from(p.runs.len() > g.cells.len());
        found.filled += usize::from(!p.fills.is_empty());
    }
    found.written = raw;
    found
}

fn drawings(a: &draftwright::model::Analysis, part: &draftwright::haecceity::Part) -> Vec<Drawing> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    let font =
        draftwright::text::Font::open(&root.join("fonts").join(draftwright::dims::FONT)).unwrap();
    let plan = cache::default_plan(a, true);
    model::render_sheets(part, a, &plan, &font).unwrap()
}

fn on(name: &str, part: &draftwright::haecceity::Part, a: &draftwright::model::Analysis) -> Found {
    let plan = cache::default_plan(a, true);
    let mut all = Found::default();
    for d in drawings(a, part) {
        let f = check(name, &d, &plan);
        all.written.extend(f.written);
        all.counted += f.counted;
        all.filled += f.filled;
    }
    all
}

#[test]
fn nist_and_pmi_fixtures_write_each_frame_as_one_positional_tolerance() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
    let mut files: Vec<_> = std::fs::read_dir(root.join("pmi/step"))
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect();
    files.push(root.join("parts/manufacturing_pmi.step.gz"));
    files.retain(|p| {
        let n = p.file_name().unwrap().to_string_lossy();
        n.starts_with("nist_") || n.starts_with("manufacturing_pmi")
    });
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join("dxf_native_gdt");
    std::fs::create_dir_all(&dir).unwrap();
    let results = support::parallel(&files, |f| {
        let mut step = Vec::new();
        flate2::read::GzDecoder::new(std::fs::File::open(f).unwrap())
            .read_to_end(&mut step)
            .unwrap();
        let name = f.file_name().unwrap().to_string_lossy().replace(".gz", "");
        let path = dir.join(format!("{}-{name}", std::process::id()));
        std::fs::write(&path, step).unwrap();
        let (part, a) = model::analyse_native(&path).unwrap();
        std::fs::remove_file(&path).unwrap();
        on(&name, &part, &a)
    });
    let written: Vec<&String> = results.iter().flat_map(|r| &r.written).collect();
    // Counted when this was written: 9 files, 122 frames, 18 of them with a count.
    assert!(written.len() >= 120, "frames found: {}", written.len());
    // Frames as typed by hand from AutoCAD's positional string (symbol, tolerance 1, tolerance
    // 2, datums 1 to 3), each read off the NIST fixtures: a position frame with a diameter
    // zone, Ⓜ and three datums; flatness with no datums; profile of a surface; perpendicularity.
    for expected in [
        "{\\Fgdt;j}%%v{\\Fgdt;n}1.016{\\Fgdt;m}%%v%%vA%%vB%%vC",
        "{\\Fgdt;c}%%v0.2%%v%%v%%v%%v",
        "{\\Fgdt;d}%%v1.5%%v%%vA%%vB%%vC",
        "{\\Fgdt;b}%%v{\\Fgdt;n}0.254%%v%%vA%%v%%v",
    ] {
        assert!(
            written.iter().any(|w| w.as_str() == expected),
            "no frame written as {expected}"
        );
    }
    // The count over the box ("2X") is no compartment: frames that carry one are exercised.
    let counted: usize = results.iter().map(|r| r.counted).sum();
    assert!(counted >= 15, "frames with a count: {counted}");
    // No fixture has a frame with fills on its face (`fills > 0`), so that case is not covered.
    let filled: usize = results.iter().map(|r| r.filled).sum();
    eprintln!("frames with fills: {filled}");
}

#[test]
fn quality_parts_write_no_text_as_geometry_and_only_ascii() {
    let Some(dir) = corpus::dir() else {
        eprintln!("SKIPPED: the corpus is not found");
        return;
    };
    let files: Vec<&str> = support::QUALITY_FILES.to_vec();
    let frames: BTreeMap<_, _> = files
        .iter()
        .zip(support::parallel(&files, |f| {
            let (part, a) = cache::native(&dir.join(f));
            on(f, &part, &a).written.len()
        }))
        .collect();
    // The quality parts carry no PMI, so they hold no frame: what is checked here is that no
    // text ink is geometry and all text is ASCII (`check`); the frame checks run on the fixtures.
    eprintln!("frames per part: {frames:?}");
}
