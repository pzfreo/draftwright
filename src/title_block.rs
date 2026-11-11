//! The title block's cells: draftwright's ISO 7200 grid (`draftwright_title_block_layout` in
//! `annotations/_sheet_furniture.py`, drawn by build123d_drafting's `TitleBlock`) as data, each
//! supplied field placed in its own cell and checked against it.
//!
//! The grid is four rows of 8 mm from the bottom of the box the layout reserves for the block
//! (`TB_H`, 40 mm, as draftwright's `_TB_H`); draftwright draws the same 32 mm block in the same
//! 40 mm reservation and puts the scale note in the band above it, as here. Each value is drawn
//! centred in its cell in IBM Plex Sans Condensed at 3 mm (ARCH0006-1: the condensed face for the
//! title block), each cell's label at its lower left at 1.5 mm. A value too wide or tall for its
//! cell at 3 mm is drawn smaller, down to 1.8 mm, so a long field fits rather than asking for
//! larger paper (#221); one that still does not fit is reported (`title_field_overflow`,
//! ISSUE-1536-a) and drawn whole.

use std::collections::BTreeMap;
use std::sync::OnceLock;

use serde::{Deserialize, Serialize};

use crate::layout::FRAME;
use crate::model::{Invalid, Problem};
use crate::output::Curve;
use crate::text::{Font, Laid, P, Segment};

/// Prefer a leading part number in the product name, then the source file's stem.
/// Keep its spelling: alphanumeric segments joined by single hyphens, with at least
/// one digit. Underscores, spaces and other punctuation separate it from a description.
pub(crate) fn default_number(analysis: &crate::model::Analysis) -> String {
    let leading_number = |name: &str| {
        let token = name
            .trim_start()
            .split(|c: char| !c.is_ascii_alphanumeric() && c != '-')
            .next()
            .unwrap_or_default();
        (token.bytes().any(|c| c.is_ascii_digit())
            && token.split('-').all(|segment| !segment.is_empty()))
        .then(|| token.to_string())
    };
    analysis
        .product_name
        .as_deref()
        .and_then(leading_number)
        .or_else(|| {
            leading_number(
                analysis
                    .source_stem
                    .as_deref()
                    .unwrap_or(&analysis.binding.part),
            )
        })
        .unwrap_or_else(|| "DWG-001".into())
}

/// A drawing title from the STEP product, falling back to the source file's stem.
/// Bindings retain their original identity; only the displayed title is normalised.
pub(crate) fn default_title(analysis: &crate::model::Analysis) -> String {
    let meaningful = |name: &str| {
        let compact: String = name
            .chars()
            .filter(|c| c.is_alphanumeric())
            .flat_map(char::to_lowercase)
            .collect();
        !compact.is_empty()
            && ![
                "part", "body", "solid", "compound", "shape", "object", "model", "unnamed",
            ]
            .iter()
            .any(|prefix| {
                compact
                    .strip_prefix(prefix)
                    .is_some_and(|suffix| suffix.chars().all(|c| c.is_ascii_digit()))
            })
    };
    let name = analysis
        .product_name
        .as_deref()
        .filter(|name| meaningful(name))
        .unwrap_or(
            analysis
                .source_stem
                .as_deref()
                .unwrap_or(&analysis.binding.part),
        );
    name.replace(['_', '-'], " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_uppercase()
}

/// The face the title block is measured and drawn with, by file name and data.
pub const FONT: &str = "IBMPlexSansCondensed-Regular.ttf";
pub const FONT_DATA: &[u8] = include_bytes!("../fonts/IBMPlexSansCondensed-Regular.ttf");

/// Page mm: a row's height (build123d_drafting's `cell_height`), the values' and the labels'
/// text heights (draftwright's `_FONT_SIZE` and half of it), the labels' inset (0.4 of the
/// draft's 2 mm `pad_around_text`), and the scale note's gap above the block and the projection
/// symbol's band right of it (`_TB_FURNITURE_GAP`, `_PROJECTION_BAND_W`).
pub const ROW_H: f64 = 8.0;
pub const TEXT: f64 = 3.0;
pub const LABEL_TEXT: f64 = 1.5;
pub const LABEL_PAD: f64 = 0.8;
/// The smallest height a value is drawn at to fit its cell: ISO 3098-0's smallest nominal
/// height (#221).
pub const MIN_TEXT: f64 = 1.8;
const FURNITURE_GAP: f64 = 3.0;
const PROJECTION_BAND_W: f64 = 16.0;
pub const PROJECTION_LINE: f64 = 0.15;

/// One cell: the field it holds, its share of the block's width, its label.
pub struct Cell {
    pub field: &'static str,
    pub width: f64,
    pub label: &'static str,
}

const fn cell(field: &'static str, width: f64, label: &'static str) -> Cell {
    Cell {
        field,
        width,
        label,
    }
}

/// draftwright's grid, top row first. Each row's widths sum to 1; the internal lines fall at
/// 35 %, 70 %, 88 % and 94 % of the width, shared by the rows that use them.
pub const ROWS: [&[Cell]; 4] = [
    &[
        cell("legal_owner", 0.70, "LEGAL OWNER"),
        cell("document_type", 0.30, "DOC. TYPE"),
    ],
    &[
        cell("title", 0.70, "TITLE"),
        cell("drawing_number", 0.30, "DWG NO."),
    ],
    &[
        cell("material", 0.35, "MATERIAL"),
        cell("general_tolerance", 0.35, "GEN. TOL."),
        cell("units", 0.18, "UNITS"),
        cell("format", 0.12, "FORMAT"),
    ],
    &[
        cell("designed_by", 0.35, "DRAWN BY"),
        cell("approved_by", 0.35, "APPROVED BY"),
        cell("date", 0.18, "DATE"),
        cell("revision", 0.06, "REV"),
        cell("sheet", 0.06, "SHEET"),
    ],
];

/// The spec's `sheet.title` keys (draftwright's `Sheet` options of the same names), each with
/// the cell it fills. `units` and `format` are not keys: the sheet states them (millimetres, and
/// the sheet's size), as draftwright does.
pub const KEYS: [(&str, &str); 11] = [
    ("title", "title"),
    ("number", "drawing_number"),
    ("material", "material"),
    ("tolerance", "general_tolerance"),
    ("drawn_by", "designed_by"),
    ("approved_by", "approved_by"),
    ("date", "date"),
    ("revision", "revision"),
    ("sheet", "sheet"),
    ("company", "legal_owner"),
    ("document_type", "document_type"),
];

/// A cell as the plan places it: the field, its label, its box `[x0, y0, x1, y1]` (page mm), and
/// the value drawn in it (none: the cell is left blank).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TitleCell {
    pub field: String,
    pub label: String,
    #[serde(rename = "box")]
    pub cell: [f64; 4],
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<String>,
}

pub fn font() -> &'static Font {
    static FONT: OnceLock<Font> = OnceLock::new();
    // Invariant: Font bytes are bundled at build time and parsed unchanged, independently of user input.
    FONT.get_or_init(|| Font::new(FONT_DATA.to_vec()).expect("the bundled font parses"))
}

/// A `sheet.title` key the block has no cell for is refused, naming the keys it has: a value
/// with nowhere to go would be dropped in silence (build123d_drafting's `TitleBlock` refuses a
/// value its layout has no cell for, and draftwright's `Sheet` an option it does not take).
pub fn check(title: &BTreeMap<String, String>) -> Result<(), Invalid> {
    match title.keys().find(|k| !KEYS.iter().any(|(key, _)| key == k)) {
        None => Ok(()),
        Some(k) => Err(Invalid {
            path: None,
            also: Vec::new(),
            message: format!(
                "The title block has no cell for sheet/title/{k}, so it would not be drawn."
            ),
            choices: KEYS.iter().map(|(key, _)| key.to_string()).collect(),
        }),
    }
}

/// The ink drawn for *value*: `⌀`, which the face lacks, as `ø` (draftwright's
/// `_font_safe_text`); the text layer keeps the value as given.
pub fn ink(value: &str) -> String {
    value.replace('⌀', "ø")
}

/// *value*'s ink at *size*, centred on its box, as build123d's centred `Text` lays it: each line
/// centred on its pen's advance, the lines a line spacing apart (OpenCascade's
/// `Font_TextFormatter`; one line is `Font::text`'s). `⌀` is drawn as `ø`.
pub fn lay(value: &str, size: f64) -> Laid {
    let value = ink(value);
    if !value.contains('\n') {
        return font().text(&value, size);
    }
    // Invariant: Font bytes are bundled at build time and parsed unchanged, independently of user input.
    let face = ttf_parser::Face::parse(FONT_DATA, 0).expect("the bundled font parses");
    let pitch = (face.ascender() as f64 - face.descender() as f64 + face.line_gap() as f64) * size
        / face.units_per_em() as f64;
    let moved = |s: &Segment, d: P| {
        let m = |p: P| [p[0] + d[0], p[1] + d[1]];
        match *s {
            Segment::Line(a, b) => Segment::Line(m(a), m(b)),
            Segment::Quad(a, b, c) => Segment::Quad(m(a), m(b), m(c)),
            Segment::Cubic(a, b, c, d) => Segment::Cubic(m(a), m(b), m(c), m(d)),
        }
    };
    let mut lines = Vec::new();
    for (row, line) in value.split('\n').enumerate() {
        let laid = font().baseline(line, size);
        let d = [-0.5 * font().advance(line, size), -(row as f64) * pitch];
        lines.push((laid, d));
    }
    let (mut min, mut max) = ([f64::INFINITY; 2], [f64::NEG_INFINITY; 2]);
    for (laid, d) in lines.iter().filter(|(l, _)| !l.contours.is_empty()) {
        for k in 0..2 {
            min[k] = min[k].min(laid.min[k] + d[k]);
            max[k] = max[k].max(laid.max[k] + d[k]);
        }
    }
    if min[0] > max[0] {
        return font().text("", size);
    }
    let centre = [0.5 * (min[0] + max[0]), 0.5 * (min[1] + max[1])];
    let contours = lines
        .iter()
        .flat_map(|(laid, d)| {
            let d = [d[0] - centre[0], d[1] - centre[1]];
            laid.contours
                .iter()
                .map(move |c| c.iter().map(|s| moved(s, d)).collect())
        })
        .collect();
    let half = [0.5 * (max[0] - min[0]), 0.5 * (max[1] - min[1])];
    Laid {
        contours,
        min: [-half[0], -half[1]],
        max: half,
    }
}

/// The ink's width and height of *value* at the block's text height, mm: the box of its glyphs
/// as [`lay`] lays them.
pub fn measure(value: &str) -> [f64; 2] {
    let laid = lay(value, TEXT);
    [laid.max[0] - laid.min[0], laid.max[1] - laid.min[1]]
}

/// The height *value* is drawn at in *cell* (`[x0, y0, x1, y1]`): [`TEXT`] when it fits, or
/// smaller so its ink fits the cell clear of the cell's lines by the labels' inset, but never
/// below [`MIN_TEXT`]. Ink scales with its height.
pub fn value_size(value: &str, cell: [f64; 4]) -> f64 {
    let [w, h] = measure(value);
    let (cw, ch) = (cell[2] - cell[0], cell[3] - cell[1]);
    if w <= cw + 1e-9 && h <= ch + 1e-9 {
        return TEXT;
    }
    let room = ((cw - 2.0 * LABEL_PAD) / w).min((ch - 2.0 * LABEL_PAD) / h);
    (TEXT * room).clamp(MIN_TEXT, TEXT)
}

/// The cells in *block* (the layout's reservation, `[x0, y0, x1, y1]`), with the values from
/// *title* (the plan's `sheet.title`, by spec key) and the sheet's *size*. A value that is empty
/// or only whitespace is no value, and is drawn without its surrounding whitespace.
pub fn cells(block: [f64; 4], title: &BTreeMap<String, String>, size: &str) -> Vec<TitleCell> {
    cells_in(block, title, size, crate::units::Units::Mm)
}

pub fn cells_in(
    block: [f64; 4],
    title: &BTreeMap<String, String>,
    size: &str,
    units: crate::units::Units,
) -> Vec<TitleCell> {
    let [x0, y0, x1, _] = block;
    let width = x1 - x0;
    let value = |field: &str| -> Option<String> {
        if field == "designed_by" {
            let author = title.get("drawn_by").map_or("", String::as_str);
            return Some(attribution(author));
        }
        let v = match field {
            "units" => units.symbol(),
            "format" => size,
            _ => {
                let key = KEYS.iter().find(|(_, f)| *f == field)?.0;
                title.get(key)?.as_str()
            }
        };
        let v = v.trim();
        (!v.is_empty()).then(|| v.to_string())
    };
    let mut out = Vec::new();
    for (i, row) in ROWS.iter().enumerate() {
        let bottom = y0 + (ROWS.len() - 1 - i) as f64 * ROW_H;
        let mut left = 0.0;
        for (j, c) in row.iter().enumerate() {
            let right = if j + 1 == row.len() {
                width
            } else {
                left + c.width * width
            };
            out.push(TitleCell {
                field: c.field.into(),
                label: c.label.into(),
                cell: [x0 + left, bottom, x0 + right, bottom + ROW_H],
                value: value(c.field),
            });
            left = right;
        }
    }
    out
}

/// The ISO 7200 "drawn by" value: the author and draftwright, or draftwright alone when no
/// author is given (draftwright's `_attribution_author`). The cell always credits draftwright,
/// and the drawing links it to [`crate::output::LINK`].
pub fn attribution(author: &str) -> String {
    let author = author.trim();
    if author.is_empty() {
        "draftwright".into()
    } else {
        format!("{author} / draftwright")
    }
}

/// The top of the drawn grid, page mm.
pub fn top(block: [f64; 4]) -> f64 {
    block[1] + ROWS.len() as f64 * ROW_H
}

/// Where the scale note is centred: in the band above the grid, its right end clear of the
/// projection symbol's band (draftwright's `_add_scale_note`, with the block's right edge as the
/// title margin's).
pub fn scale_note(block: [f64; 4], text: &str) -> [f64; 2] {
    let laid = lay(text, TEXT);
    let (w, h) = (laid.max[0] - laid.min[0], laid.max[1] - laid.min[1]);
    let right = block[2] - 3.0 - PROJECTION_BAND_W;
    [right - w / 2.0, top(block) + FURNITURE_GAP + h / 2.0]
}

/// The projection-method symbol's resolved paper geometry: `third-angle` (plan above front and
/// right side to the right, the default) or `first-angle` (plan below, right side to the left),
/// as the Spec's `sheet/projection` says. This is sheet furniture, not a view.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProjectionPlaced {
    pub method: String,
    pub curves: Vec<Curve>,
}

/// ISO 5456-2's cone elevation and concentric end view, as build123d_drafting's
/// `ProjectionSymbol`: large diameter h, small diameter 0.6h, length 1.3h and gap 0.5h. Both
/// are ISO 5456-2's figures (and ISO 128-3's), the cone's small end on its left: first angle
/// (*first_angle*, the Spec's `sheet/projection`) puts the cone on the left and the circles on
/// the right, its large end towards them; third angle puts the circles on the left and the cone
/// on the right, its small end towards them (#31: draftwright, and this port before, drew third
/// angle with the circles on the right, the cone turned end for end, which is geometrically
/// third angle but reads as first angle's layout). The 3 mm symbol and its 0.15 mm stroke fit
/// inside the reserved band, right of the scale note, as Python places them.
pub fn projection(block: [f64; 4], first_angle: bool) -> ProjectionPlaced {
    let h = TEXT;
    let (big, small, len) = (0.5 * h, 0.3 * h, 1.3 * h);
    // The cone's diameters at its left and right ends (the small end left in either method),
    // where it starts, and the circles' centre: the same overall extent either way.
    let (left, right) = (small, big);
    let far = len + 0.5 * h + big;
    let end = far + big;
    let (x0, cx) = if first_angle {
        (0.0, far)
    } else {
        (end - len, big)
    };
    let x1 = x0 + len;
    let at = [
        block[2] - 3.0 - PROJECTION_LINE / 2.0 - (end + 0.25 * h),
        top(block) + FURNITURE_GAP + PROJECTION_LINE / 2.0 + big,
    ];
    let m = |p: P| [p[0] + at[0], p[1] + at[1]];
    let mut curves: Vec<Curve> = [
        [[x0, -left], [x0, left]],
        [[x1, -right], [x1, right]],
        [[x0, left], [x1, right]],
        [[x0, -left], [x1, -right]],
        [[-0.25 * h, 0.0], [end + 0.25 * h, 0.0]],
    ]
    .into_iter()
    .map(|[a, b]| Curve::Line(m(a), m(b)))
    .collect();
    curves.extend([big, small].map(|r| Curve::Conic {
        centre: m([cx, 0.0]),
        radii: [r, r],
        x_axis: [1.0, 0.0],
        y_axis: [0.0, 1.0],
        t0: 0.0,
        t1: std::f64::consts::TAU,
    }));
    ProjectionPlaced {
        method: if first_angle {
            "first-angle"
        } else {
            "third-angle"
        }
        .into(),
        curves,
    }
}

/// A part's default surface finish as drawn beside the title block: ISO 1302's complete
/// graphical symbol, its short and long legs at 60° to a tip (1.4 and 3 text heights tall, the
/// proportions ISO 1302 gives a text height), the bar from the long leg's top, and the
/// requirement (`Ra 3.2`) under the bar; mm on the page.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FinishPlaced {
    pub strokes: Vec<[P; 2]>,
    /// The requirement's text, centred here.
    pub text_at: P,
    /// The text's box.
    pub label_box: [f64; 4],
    /// The whole symbol's box.
    #[serde(rename = "box")]
    pub glyph: [f64; 4],
    /// Circles, `[cx, cy, r, from, to]` (degrees): the circle of `no material removal`.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub arcs: Vec<[f64; 5]>,
}

/// The default surface finish *text* (`Ra 3.2`) as its symbol, at the left end of the band
/// above the grid of *block*, as draftwright places it (`_add_default_surface_finish`: the
/// scale note and the projection symbol take the band's right end). draftwright's glyph
/// (build123d_drafting's `SurfaceFinish`) draws one leg at 60° and a vertical stub, not
/// ISO 1302's two legs; the port draws ISO 1302's.
pub fn default_finish(block: [f64; 4], text: &str, removal: &str) -> FinishPlaced {
    let laid = lay(text, TEXT);
    let w = laid.max[0] - laid.min[0];
    let cot = 1.0 / 3f64.sqrt();
    let (short, long, gap) = (1.4 * TEXT, 3.0 * TEXT, 0.3 * TEXT);
    // About the tip at the origin.
    let short_top = [-short * cot, short];
    let long_top = [long * cot, long];
    let left = long_top[0] + gap;
    let end = left + w + gap;
    let at = [block[0] - short_top[0], top(block) + FURNITURE_GAP];
    let m = |p: P| [p[0] + at[0], p[1] + at[1]];
    let centre = m([left + w / 2.0, long - gap - TEXT / 2.0]);
    // ISO 1302's mark of the material removal condition (ISO 10303-1110's term), about the tip:
    // haecceity's shared notation (#258); a term it does not know draws the basic symbol.
    let (marks, circles) = haecceity::presentation::notes::removal_marks(
        haecceity::pmi::MaterialRemoval::from_term(removal)
            .unwrap_or(haecceity::pmi::MaterialRemoval::AnyProcessAllowed),
        short,
    );
    let mut strokes = vec![
        [m(short_top), m([0.0, 0.0])],
        [m([0.0, 0.0]), m(long_top)],
        [m(long_top), m([end, long])],
    ];
    strokes.extend(marks.iter().map(|[a, b]| [m(*a), m(*b)]));
    FinishPlaced {
        strokes,
        arcs: circles
            .iter()
            .map(|c| {
                let [x, y] = m([c[0], c[1]]);
                [x, y, c[2], c[3], c[4]]
            })
            .collect(),
        text_at: centre,
        label_box: [
            centre[0] - w / 2.0,
            centre[1] - TEXT / 2.0,
            centre[0] + w / 2.0,
            centre[1] + TEXT / 2.0,
        ],
        glyph: [block[0], at[1], m([end, long])[0], at[1] + long],
    }
}

/// The spec path a cell's value comes from, or what states it.
fn source(field: &str) -> String {
    match KEYS.iter().find(|(_, f)| *f == field) {
        Some((key, _)) => format!("sheet/title/{key}"),
        None if field == "format" => "sheet/size".into(),
        None => format!("the title block's {field}"),
    }
}

/// One `title_field_overflow` warning for each cell whose value's ink is wider or taller than
/// the cell (draftwright's `_lint_title_fields`, there a lint warning, here a plan problem), and
/// an `annotation_off_sheet` error for each value whose ink leaves the sheet's drawing area,
/// each measured at the value's [`value_size`]. Each carries the full value, which is drawn
/// whole: nothing is shortened.
pub fn problems(cells: &[TitleCell], page: [f64; 2]) -> Vec<Problem> {
    let mut out = Vec::new();
    for c in cells {
        if c.field == "general_tolerance" && c.value.is_none() {
            out.push(Problem {
                crossing_kinds: Vec::new(),
                ink_is_leader: None,
                id: "p:general_tolerance_missing:sheet".into(),
                code: "general_tolerance_missing".into(),
                severity: "warning".into(),
                message: "GEN. TOL. is blank: no general tolerance is drawn from the Spec or usable PMI. Set sheet/title/tolerance to the intended requirement. The offered fix explicitly states UNSPECIFIED; it acknowledges the missing requirement without inventing a manufacturing tolerance.".into(),
                subjects: vec!["sheet".into()],
                refs: vec!["title_block/general_tolerance".into(), "sheet/title/tolerance".into()],
                fix: vec![crate::items::Op::set("sheet/title/tolerance", serde_json::json!("UNSPECIFIED"))],
                fixes: Vec::new(),
            });
        }
        let Some(v) = &c.value else { continue };
        let laid = lay(v, value_size(v, c.cell));
        let [w, h] = [laid.max[0] - laid.min[0], laid.max[1] - laid.min[1]];
        let [x0, y0, x1, y1] = c.cell;
        let (cw, ch) = (x1 - x0, y1 - y0);
        let subject = format!("title_block/{}", c.field);
        if w > cw + 1e-9 || h > ch + 1e-9 {
            out.push(Problem {
                id: format!("p:title_field_overflow:{subject}"),
                code: "title_field_overflow".into(),
                severity: "warning".into(),
                message: format!(
                    "Title-block field '{}' overflows its cell: text {w:.2} × {h:.2} mm, cell {cw:.2} × {ch:.2} mm. The value, {v:?}, is drawn whole at {MIN_TEXT} mm, the smallest text height, not shortened; give {} a concise value.",
                    c.field,
                    source(&c.field)
                ),
                subjects: vec!["sheet".into()],
                refs: vec![subject.clone()],
                ..Default::default()
            });
        }
        let (cx, cy) = (0.5 * (x0 + x1), 0.5 * (y0 + y1));
        let (lo, hi) = ([cx - w / 2.0, cy - h / 2.0], [cx + w / 2.0, cy + h / 2.0]);
        if lo[0] < FRAME || lo[1] < FRAME || hi[0] > page[0] - FRAME || hi[1] > page[1] - FRAME {
            out.push(Problem {
                id: format!("p:annotation_off_sheet:{subject}"),
                code: "annotation_off_sheet".into(),
                severity: "error".into(),
                message: format!(
                    "Title-block field '{}' ({v:?}) runs off the sheet's drawing area; give {} a concise value.",
                    c.field,
                    source(&c.field)
                ),
                subjects: vec!["sheet".into()],
                refs: vec![subject],
                ..Default::default()
            });
        }
    }
    out
}

/// Resolve ISO 7200 sheet numbers in the Plan, keeping the grid and authored Spec intact.
pub(crate) fn number_sheets(plan: &mut crate::model::Plan) {
    if plan.sheets.is_empty() {
        return;
    }
    let count = plan.sheets.len() + 1;
    // The authored label no longer reaches the sheet; audit its replacement instead.
    plan.problems.retain(|p| {
        !(["title_field_overflow", "annotation_off_sheet"].contains(&p.code.as_str())
            && p.refs.iter().any(|r| r == "title_block/sheet"))
    });
    for sheet in std::iter::once(&mut plan.sheet).chain(&mut plan.sheets) {
        let value = format!("{}/{count}", sheet.number);
        sheet.title.insert("sheet".into(), value.clone());
        for cell in sheet.title_cells.iter_mut().filter(|c| c.field == "sheet") {
            cell.value = Some(value.clone());
            for mut problem in problems(std::slice::from_ref(cell), sheet.page) {
                if sheet.number > 1 {
                    problem.id += &format!(":sheet:{}", sheet.number);
                    problem.subjects = vec![format!("sheet:{}", sheet.number)];
                }
                plan.problems.push(problem);
            }
        }
    }
}

/// Pack-only top row: keep owner and document type, adding independent item and quantity
/// cells. Ordinary drawing Specs and their title blocks are byte-for-byte unchanged.
pub(crate) fn pack_cells(sheet: &mut crate::model::PlannedSheet, item: u32, qty: usize) {
    sheet.title_cells = cells_in(sheet.title_block, &sheet.title, &sheet.size, sheet.units);
    let owner = &mut sheet.title_cells[0];
    let [x0, y0, x1, y1] = owner.cell;
    let width = x1 - x0;
    owner.cell[2] = x0 + width * 0.60;
    for (field, label, value, left, right) in [
        ("pack_item", "ITEM", item.to_string(), 0.60, 0.80),
        ("pack_qty", "QTY", qty.to_string(), 0.80, 1.00),
    ] {
        sheet.title_cells.push(TitleCell {
            field: field.into(),
            label: label.into(),
            cell: [x0 + width * left, y0, x0 + width * right, y1],
            value: Some(value),
        });
    }
}
