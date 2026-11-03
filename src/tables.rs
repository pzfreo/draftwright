//! Corner-block tables: the spec's `tables` (`t:<k>`, set in the spec), drawn as draftwright's
//! `Sheet.table` draws a declared table (revision blocks, BOMs, schedules) through
//! `Drawing.add_table` and `_build_table`: row 0 the header, each column as wide as its widest
//! cell's ink with 2 mm of padding each side (at least 2.5 text heights, as `_table_metrics`),
//! each cell's ink centred in its cell, IBM Plex Mono at 3 mm, each row 3 mm plus 2 mm of
//! padding above and below, ruled at every column edge and every row edge.
//!
//! Each table is reserved when the sheet is chosen, towards its `prefer` corner (draftwright's
//! `layout_required_tables`, which reserves declared tables ahead of the general notes), and
//! placed after the dimensions and the general notes, in the spec's order, as draftwright places
//! them after the build: by `fit_box` towards that corner, first with 6 mm of breathing room and
//! then at the text padding, clear of the views' areas (the isometric's drawn box), every
//! dimension's ink, the title block, the notes block and the tables placed before it. A table
//! that fits nowhere is a `table_not_placed` error naming it (draftwright's `table_dropped`),
//! which stays in the plan undrawn.
//!
//! The pieces the general notes block shares with a table (its text height, padding and row,
//! the font-safe ink, what a block keeps clear of, its placement and its rules) live here; the
//! notes block is draftwright's single-column table (`src/notes.rs`).

use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::dims::PlannedDimension;
use crate::items::State;
use crate::layout::{self, Corner, FRAME, Rect};
use crate::model::{Analysis, Invalid, PlannedSheet, PlannedView, Problem};

/// The text height and the padding around it, mm (draftwright's `_FONT_SIZE` and the draft
/// preset's `pad_around_text`).
pub const TEXT: f64 = 3.0;
pub const PAD: f64 = 2.0;
/// One row's height.
pub const ROW: f64 = TEXT + 2.0 * PAD;
/// The corners a table may prefer, as draftwright names them.
pub const CORNERS: [&str; 4] = ["tr", "tl", "br", "bl"];

/// Legacy font-safe spelling for text-only consumers (draftwright's `_font_safe_text`).
/// Drawing and measurement use [`text_ink`] so all four callout symbols have ink.
pub fn ink(text: &str) -> String {
    text.replace('⌀', "ø")
}

/// A table cell or note on its baseline, preserving prose, punctuation and whitespace.
/// Each callout symbol uses the same one-em cell and ink as dimension labels, even when
/// embedded in a word. Ordinary text retains the font's advances and line spacing.
pub fn text_ink(text: &str, font: &crate::text::Font) -> crate::dims::LabelInk {
    use crate::dims::LabelInk;
    use crate::text::Segment;
    if !text.contains(['⌀', '↧', '⌴', '⌵']) {
        let laid = font.baseline(text, TEXT);
        return LabelInk {
            glyphs: vec![laid.contours],
            strokes: Vec::new(),
            min: laid.min,
            max: laid.max,
        };
    }
    let mut out = LabelInk {
        glyphs: Vec::new(),
        strokes: Vec::new(),
        min: [f64::INFINITY; 2],
        max: [f64::NEG_INFINITY; 2],
    };
    let mut append = |ink: LabelInk, x: f64, y: f64| {
        if ink.glyphs.iter().all(Vec::is_empty) && ink.strokes.is_empty() {
            return;
        }
        let shift = |p: [f64; 2]| [x + p[0], y + p[1]];
        for k in 0..2 {
            out.min[k] = out.min[k].min(shift(ink.min)[k]);
            out.max[k] = out.max[k].max(shift(ink.max)[k]);
        }
        out.strokes
            .extend(ink.strokes.into_iter().map(|[a, b]| [shift(a), shift(b)]));
        out.glyphs.extend(ink.glyphs.into_iter().map(|g| {
            g.into_iter()
                .map(|c| {
                    c.into_iter()
                        .map(|s| match s {
                            Segment::Line(a, b) => Segment::Line(shift(a), shift(b)),
                            Segment::Quad(a, b, c) => Segment::Quad(shift(a), shift(b), shift(c)),
                            Segment::Cubic(a, b, c, d) => {
                                Segment::Cubic(shift(a), shift(b), shift(c), shift(d))
                            }
                        })
                        .collect()
                })
                .collect()
        }));
    };
    for (row, line) in text.split('\n').enumerate() {
        let mut x = 0.0;
        let y = -(row as f64) * font.line_spacing(TEXT);
        for chunk in line.split_inclusive(['⌀', '↧', '⌴', '⌵']) {
            let symbol = chunk
                .chars()
                .next_back()
                .filter(|c| matches!(c, '⌀' | '↧' | '⌴' | '⌵'));
            let plain = symbol.map_or(chunk, |c| &chunk[..chunk.len() - c.len_utf8()]);
            if !plain.is_empty() {
                let laid = font.baseline(plain, TEXT);
                append(
                    LabelInk {
                        glyphs: vec![laid.contours],
                        strokes: Vec::new(),
                        min: laid.min,
                        max: laid.max,
                    },
                    x,
                    y,
                );
                x += font.advance(plain, TEXT);
            }
            if let Some(c) = symbol {
                append(crate::dims::label_ink(&[c.to_string()], font), x, y);
                x += TEXT;
            }
        }
    }
    if !out.min[0].is_finite() {
        (out.min, out.max) = ([0.0; 2], [0.0; 2]);
    }
    out
}

/// *text*'s ink `[x0, x1]` laid from a pen at 0, or none when it draws nothing.
pub(crate) fn span(text: &str) -> Option<[f64; 2]> {
    // The bundled table font and height are fixed. Layout trials repeatedly measure
    // the same schedule cells; retain their exact ink bounds, not their outlines.
    use std::collections::VecDeque;
    use std::sync::{Mutex, OnceLock};
    type Memo = VecDeque<(String, Option<[f64; 2]>)>;
    static MEMO: OnceLock<Mutex<Memo>> = OnceLock::new();
    let text = text.to_string();
    let memo = MEMO.get_or_init(|| Mutex::new(VecDeque::new()));
    if let Some(span) = memo
        .lock()
        // Invariant: The memo lock protects only string comparison and copying fixed-size bounds.
        .expect("table metrics memo lock")
        .iter()
        .find(|(t, _)| *t == text)
        .map(|(_, span)| *span)
    {
        return span;
    }
    let laid = text_ink(&text, crate::dims::font());
    let span = (laid.glyphs.iter().any(|g| !g.is_empty()) || !laid.strokes.is_empty())
        .then_some([laid.min[0], laid.max[0]]);
    if text.len() <= 4096 {
        // Invariant: Text measurement happens before locking; locked operations only update the bounded deque.
        let mut memo = memo.lock().expect("table metrics memo lock");
        memo.retain(|(t, _)| *t != text);
        if memo.len() == 128 {
            memo.pop_front();
        }
        memo.push_back((text, span));
    }
    span
}

/// What a block keeps clear of: the views' areas (the isometric's drawn box), every
/// dimension's ink and the title block.
pub(crate) fn obstacles(
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    dims: &[PlannedDimension],
) -> Vec<Rect> {
    let mut out: Vec<Rect> = views
        .iter()
        .filter(|v| v.sheet == sheet.number)
        .map(|v| {
            if v.id == "v:iso" {
                let ib = analysis.drawing_iso_bounds();
                [
                    v.at[0] + v.scale * ib[0],
                    v.at[1] + v.scale * ib[1],
                    v.at[0] + v.scale * ib[2],
                    v.at[1] + v.scale * ib[3],
                ]
            } else {
                v.area
            }
        })
        .collect();
    let bounds = |pts: &[[f64; 2]], grow: f64| {
        [
            pts.iter().map(|p| p[0]).fold(f64::INFINITY, f64::min) - grow,
            pts.iter().map(|p| p[1]).fold(f64::INFINITY, f64::min) - grow,
            pts.iter().map(|p| p[0]).fold(f64::NEG_INFINITY, f64::max) + grow,
            pts.iter().map(|p| p[1]).fold(f64::NEG_INFINITY, f64::max) + grow,
        ]
    };
    for p in dims
        .iter()
        .filter(|d| {
            views
                .iter()
                .find(|v| v.id == d.view)
                .map_or(sheet.number == 1, |v| v.sheet == sheet.number)
        })
        .filter_map(|d| d.placed.as_ref())
    {
        out.push(p.label_box);
        out.extend(p.lines.iter().map(|l| bounds(l, 0.0)));
        // An arrowhead is no wider than its length.
        out.extend(p.arrows.iter().map(|a| bounds(a, crate::dims::ARROW)));
    }
    out.push(sheet.title_block);
    out
}

/// Half the annotation-rule stroke, shared with measured frame checks.
pub fn frame_clearance() -> f64 {
    crate::output::layer_styles()[2].1.width / 2.0
}

/// A block of *size*'s lower-left corner on the sheet's drawing area, clear of *obstacles*,
/// towards *corner*, or none when it fits nowhere.
pub(crate) fn place(
    size: [f64; 2],
    sheet: &PlannedSheet,
    obstacles: &[Rect],
    corner: Corner,
) -> Option<[f64; 2]> {
    let [pw, ph] = sheet.page;
    // Ruled furniture must keep its visible stroke inside the drawable frame.
    let edge = FRAME + frame_clearance();
    let region = [edge, edge, pw - edge, ph - edge];
    layout::fit_auxiliary_box(size, region, obstacles, corner, PAD)
}

/// A block's rules, as `_build_table` rules a table: a vertical at each of *columns* (its
/// sides among them) and a horizontal at the top and bottom of each row.
pub fn ruling(cells: [f64; 4], columns: &[f64]) -> Vec<[[f64; 2]; 2]> {
    let [x0, y0, x1, y1] = cells;
    let n = ((y1 - y0) / ROW).round() as usize;
    let mut out: Vec<_> = columns.iter().map(|&x| [[x, y0], [x, y1]]).collect();
    out.extend((0..=n).map(|k| {
        let y = y1 - k as f64 * ROW;
        [[x0, y], [x1, y]]
    }));
    out
}

/// A table as the spec states it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TableSpec {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sheet: Option<u32>,
    /// `t:<k>`, `k` a whole number from 1.
    pub id: String,
    /// Its rows, row 0 the header: lists of one length, each cell a string or a number.
    pub rows: Vec<Value>,
    /// The corner it goes towards: `tr` (the default), `tl`, `br` or `bl`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub prefer: Option<String>,
}

impl TableSpec {
    /// The corner it goes towards, its name and the layout's.
    pub(crate) fn corner(&self) -> (&str, Corner) {
        match self.prefer.as_deref().unwrap_or("tr") {
            "tl" => ("tl", Corner::TopLeft),
            "br" => ("br", Corner::BottomRight),
            "bl" => ("bl", Corner::BottomLeft),
            _ => ("tr", Corner::TopRight),
        }
    }

    /// Its cells as text (a number as JSON writes it), once [`check`] has passed it.
    fn cells(&self) -> Vec<Vec<String>> {
        self.rows
            .iter()
            .map(|r| {
                r.as_array()
                    .into_iter()
                    .flatten()
                    .map(|c| match c {
                        Value::String(s) => s.clone(),
                        c => c.to_string(),
                    })
                    .collect()
            })
            .collect()
    }
}

/// A table the sheet will carry.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlannedTable {
    #[serde(
        default = "crate::sheets::one",
        skip_serializing_if = "crate::sheets::is_one"
    )]
    pub sheet: u32,
    pub id: String,
    /// Its cells as text, row 0 the header.
    pub rows: Vec<Vec<String>>,
    /// Compiler-owned provenance for tagged manufacturing rows, including wrapped continuations.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub row_claims: Vec<RowClaims>,
    pub default: bool,
    pub basis: String,
    /// What became of it: shown, or why not ([`crate::items::State`]).
    pub state: crate::items::State,
    /// Where it is drawn; absent when it has no place, and a problem says so.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub placed: Option<PlacedTable>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RowClaims {
    pub tag: String,
    pub claims: Vec<crate::output::Claim>,
}

/// A table's place, mm on the page.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlacedTable {
    /// Each cell's ink centre, by row and column; none for a cell that draws nothing.
    pub cells_at: Vec<Vec<Option<[f64; 2]>>>,
    /// Side-by-side ruled blocks; empty for an unsplit table.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub blocks: Vec<PlacedTable>,
    /// The x of each column's left edge, and of the table's right edge.
    pub columns: Vec<f64>,
    /// The whole table, `[x0, y0, x1, y1]`, a row of [`ROW`] per row.
    #[serde(rename = "box")]
    pub cells: [f64; 4],
}

/// A table's ID is `t:` and a whole number from 1, written without leading zeros.
fn well_formed(id: &str) -> bool {
    id.strip_prefix("t:").is_some_and(|k| {
        !k.is_empty() && !k.starts_with('0') && k.bytes().all(|b| b.is_ascii_digit())
    })
}

/// The first `t:<k>` after every well-formed ID in *ids*.
pub fn next_id<'a>(ids: impl IntoIterator<Item = &'a str>) -> String {
    let k = ids
        .into_iter()
        .filter(|id| well_formed(id))
        .filter_map(|id| id[2..].parse::<u64>().ok())
        .max()
        .unwrap_or(0);
    format!("t:{}", k + 1)
}

/// The tables are refused, with what to write instead, when an ID is not `t:<k>`, two tables
/// share an ID, a table has no rows, a row is a string or not a list, the rows are not all of
/// one non-zero length (draftwright's `Sheet.table` refusals), a cell is neither a string nor a
/// number, or a corner is not one of [`CORNERS`].
pub fn check(tables: &[TableSpec]) -> Result<(), Invalid> {
    let free = || vec![next_id(tables.iter().map(|t| t.id.as_str()))];
    let refuse = |message: String| Invalid {
        path: None,
        also: Vec::new(),
        message,
        choices: vec![],
    };
    for (i, t) in tables.iter().enumerate() {
        if !well_formed(&t.id) {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "tables/{i} has the ID {:?}; a table's ID is t:<k>, k a whole number from 1 (t:1, t:2, …).",
                    t.id
                ),
                choices: free(),
            });
        }
        if tables[..i].iter().any(|u| u.id == t.id) {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "Two tables have the ID {:?} (tables/{i} and an earlier one); give each table its own.",
                    t.id
                ),
                choices: free(),
            });
        }
        let id = &t.id;
        if t.rows.is_empty() {
            return Err(refuse(format!(
                "Table {id} (tables/{i}) has no rows; give it a header row and its rows, or remove it."
            )));
        }
        let mut width = None;
        for (j, row) in t.rows.iter().enumerate() {
            let cells = match row {
                Value::String(_) => {
                    return Err(refuse(format!(
                        "Table {id} (tables/{i}/rows/{j}) has a row that is a string; a row is a list of cells. For a list of notes, use notes."
                    )));
                }
                Value::Array(cells) => cells,
                _ => {
                    return Err(refuse(format!(
                        "Table {id} (tables/{i}/rows/{j}) has a row that is not a list; a row is a list of cells."
                    )));
                }
            };
            let n = *width.get_or_insert(cells.len());
            if n == 0 || cells.len() != n {
                return Err(refuse(format!(
                    "Table {id} (tables/{i}/rows/{j}): its rows must all have the same number of cells, at least one (row 0 has {n}, row {j} has {}).",
                    cells.len()
                )));
            }
            if let Some(k) = cells.iter().position(|c| !(c.is_string() || c.is_number())) {
                return Err(refuse(format!(
                    "Table {id} (tables/{i}/rows/{j}/{k}) has a cell that is neither text nor a number; write it as text."
                )));
            }
        }
        if let Some(p) = &t.prefer
            && !CORNERS.contains(&p.as_str())
        {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "Table {id} (tables/{i}) prefers the corner {p:?}; a table goes towards tr, tl, br or bl."
                ),
                choices: CORNERS.iter().map(|c| c.to_string()).collect(),
            });
        }
    }
    Ok(())
}

/// Each column's width, as `_table_metrics` measures it: its widest cell's ink and the padding
/// each side, at least 2.5 text heights.
pub(crate) fn widths(rows: &[Vec<String>]) -> Vec<f64> {
    // Repeated cells have identical ink at the table's fixed font and text height. Keep
    // the exact measurement locally; long repeated requirements otherwise lay out every
    // glyph again for each row on every sheet/scale candidate.
    let mut spans = std::collections::BTreeMap::new();
    (0..rows[0].len())
        .map(|c| {
            let ink = rows
                .iter()
                .filter_map(|r| *spans.entry(r[c].as_str()).or_insert_with(|| span(&r[c])))
                .map(|s| s[1] - s[0])
                .fold(0.0, f64::max);
            (ink + 2.0 * PAD).max(2.5 * TEXT)
        })
        .collect()
}

/// A table's size, mm, from its cells.
pub(crate) fn measure(rows: &[Vec<String>]) -> [f64; 2] {
    [widths(rows).iter().sum(), ROW * rows.len() as f64]
}

/// Whether an override names a shared table rather than a hole schedule.
pub(crate) fn split_id(id: &str, spec: &crate::model::Spec) -> bool {
    id.starts_with("spring_data_f:helical_spring:")
        || spec.tables.iter().any(|t| t.id == id)
        || id == crate::dims::blends::RADIUS_TABLE
        || id == crate::pmi_lowering::SCHEDULE_ID
        || id
            .strip_prefix("gear_data_")
            .is_some_and(|owner| !owner.is_empty())
}

/// Keep a fitting table whole. Otherwise try the fewest blocks whose height fits.
pub(crate) fn counts(fixed: Option<usize>, headers: usize, body: usize, page: [f64; 2]) -> usize {
    fixed.unwrap_or_else(|| {
        (1..=crate::hole_table::MAX_COLUMNS)
            .find(|&n| {
                ROW * (headers + body.div_ceil(n)) as f64
                    <= page[1] - 2.0 * (FRAME + frame_clearance())
            })
            .unwrap_or(1)
    })
}

pub(crate) fn forms(rows: &[Vec<String>], fixed: Option<usize>) -> Vec<[f64; 2]> {
    let w: f64 = widths(rows).iter().sum();
    fixed
        .map_or(1..=crate::hole_table::MAX_COLUMNS, |n| n..=n)
        .map(|n| {
            [
                w * n as f64 + crate::hole_table::BLOCK_GAP * (n - 1) as f64,
                ROW * (1 + (rows.len() - 1).div_ceil(n)) as f64,
            ]
        })
        .collect()
}

pub(crate) fn size_on(rows: &[Vec<String>], fixed: Option<usize>, page: [f64; 2]) -> [f64; 2] {
    let n = counts(fixed, 1, rows.len() - 1, page);
    forms(rows, Some(n))[0]
}

/// The shared table placement keeps source rows and their claims in their original order.
/// Repeated headers live in the ruled blocks, without duplicating source provenance.
pub(crate) fn place_split(
    rows: &[Vec<String>],
    sheet: &PlannedSheet,
    obstacles: &[Rect],
    corner: Corner,
    fixed: Option<usize>,
) -> Option<PlacedTable> {
    let first = counts(fixed, 1, rows.len() - 1, sheet.page);
    let (n, at) = (first..=fixed.unwrap_or(crate::hole_table::MAX_COLUMNS))
        .find_map(|n| place(forms(rows, Some(n))[0], sheet, obstacles, corner).map(|at| (n, at)))?;
    let widths = widths(rows);
    if n == 1 {
        return Some(lay(rows, &widths, at));
    }
    let per = (rows.len() - 1).div_ceil(n);
    let blocks: Vec<_> = crate::hole_table::split(&rows[..1], &rows[1..], n)
        .iter()
        .enumerate()
        .map(|(k, rows)| {
            lay(
                rows,
                &widths,
                [
                    at[0] + k as f64 * (widths.iter().sum::<f64>() + crate::hole_table::BLOCK_GAP),
                    at[1],
                ],
            )
        })
        .collect();
    let mut cells_at = vec![blocks[0].cells_at[0].clone()];
    cells_at.extend((0..rows.len() - 1).map(|i| blocks[i / per].cells_at[1 + i % per].clone()));
    let size = forms(rows, Some(n))[0];
    Some(PlacedTable {
        cells_at,
        columns: blocks[0].columns.clone(),
        cells: [at[0], at[1], at[0] + size[0], at[1] + size[1]],
        blocks,
    })
}

/// Rules for each block, without drawing lines across the gaps.
pub(crate) fn rules(p: &PlacedTable) -> Vec<[[f64; 2]; 2]> {
    if p.blocks.is_empty() {
        ruling(p.cells, &p.columns)
    } else {
        p.blocks.iter().flat_map(rules).collect()
    }
}

type CellRow<'a> = (usize, &'a [String], &'a [Option<[f64; 2]>]);

/// Text rows with their original source index, including repeated headers.
pub(crate) fn cell_rows(table: &PlannedTable) -> Vec<CellRow<'_>> {
    let Some(p) = &table.placed else {
        return Vec::new();
    };
    let mut rows: Vec<_> = table
        .rows
        .iter()
        .zip(&p.cells_at)
        .enumerate()
        .map(|(i, (row, at))| (i, row.as_slice(), at.as_slice()))
        .collect();
    rows.extend(
        p.blocks
            .iter()
            .skip(1)
            .map(|b| (0, table.rows[0].as_slice(), b.cells_at[0].as_slice())),
    );
    rows
}

pub(crate) fn sizes_on(
    tables: &[TableSpec],
    spec: &crate::model::Spec,
    page: [f64; 2],
) -> Vec<([f64; 2], Corner)> {
    tables
        .iter()
        .map(|t| {
            (
                size_on(&t.cells(), crate::hole_table::columns(spec, &t.id), page),
                t.corner().1,
            )
        })
        .collect()
}

/// Each table's size and corner, in the spec's order: what the sheet reserves for them.
pub fn sizes(tables: &[TableSpec]) -> Vec<([f64; 2], Corner)> {
    tables
        .iter()
        .map(|t| (measure(&t.cells()), t.corner().1))
        .collect()
}

/// The spec's tables as Plan items, each placed in the spec's order clear of the views, the
/// dimensions, the title block, *notes* (the general notes block's box) and the tables placed
/// before it, or reported when it has no place.
pub fn plan(
    tables: &[TableSpec],
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    dims: &[PlannedDimension],
    notes: Option<Rect>,
    spec: &crate::model::Spec,
) -> (Vec<PlannedTable>, Vec<Problem>) {
    if tables.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let mut obstacles = obstacles(analysis, sheet, views, dims);
    obstacles.extend(notes);
    let mut problems = Vec::new();
    let items = tables
        .iter()
        .enumerate()
        .map(|(i, t)| {
            let rows = t.cells();
            let [w, h] = size_on(&rows, crate::hole_table::columns(spec, &t.id), sheet.page);
            let (prefer, corner) = t.corner();
            let placed = place_split(&rows, sheet, &obstacles, corner, crate::hole_table::columns(spec, &t.id));
            if let Some(p) = &placed { obstacles.push(p.cells); }
            if placed.is_none() {
                problems.push(Problem {
                    id: format!("p:table_not_placed:{}", t.id),
                    code: "table_not_placed".into(),
                    severity: "error".into(),
                    message: format!(
                        "{} ({w:.1} × {h:.1} mm) has no place on the sheet clear of the views, their dimensions, the title block, the general notes and the tables before it; it stays in the plan undrawn. Shorten its cells, or choose a larger sheet (sheet/size) or a smaller scale (sheet/scale).",
                        crate::wording::capital(&crate::wording::block(&t.id))
                    ),
                    subjects: vec![t.id.clone()],
                    ..Default::default()
                });
            }
            PlannedTable {
                sheet: sheet.number,
                id: t.id.clone(),
                rows,
                row_claims: Vec::new(),
                default: false,
                basis: format!(
                    "authored: the spec's table {} (tables/{i}), towards the sheet's {prefer} corner as draftwright places a declared table",
                    t.id
                ),
                state: State::placed_or(placed.is_some(), "table_not_placed"),
                placed,
            }
        })
        .collect();
    (items, problems)
}

/// A table of *rows*, its columns *widths* wide, laid with its lower-left corner at *at*: each
/// column's edges and each cell's ink centre.
pub(crate) fn lay(rows: &[Vec<String>], widths: &[f64], at: [f64; 2]) -> PlacedTable {
    let [x0, y0] = at;
    let mut columns = vec![x0];
    for w in widths {
        // Invariant: columns starts with the left edge and grows by one per column.
        columns.push(columns.last().expect("a column") + w);
    }
    let top = y0 + ROW * rows.len() as f64;
    let cells_at = rows
        .iter()
        .enumerate()
        .map(|(r, row)| {
            let y = top - (r as f64 + 0.5) * ROW;
            row.iter()
                .enumerate()
                .map(|(c, cell)| span(cell).map(|_| [0.5 * (columns[c] + columns[c + 1]), y]))
                .collect()
        })
        .collect();
    // Invariant: columns starts with the left edge, even before any widths are appended.
    let x1 = *columns.last().expect("a column");
    PlacedTable {
        blocks: Vec::new(),
        cells_at,
        columns,
        cells: [x0, y0, x1, top],
    }
}

/// Every cell a placed table draws, with its ink centre.
pub fn texts(table: &PlannedTable) -> Vec<(&str, [f64; 2])> {
    cell_rows(table)
        .into_iter()
        .flat_map(|(_, row, at)| row.iter().zip(at))
        .filter_map(|(cell, at)| at.map(|at| (cell.as_str(), at)))
        .collect()
}

/// The size of the manufacturing requirements table of *rows* (as [`measure`] sizes a table),
/// or none when there is none: what the sheet reserves for it, top right.
pub fn manufacturing_size(rows: &[Vec<String>]) -> Option<[f64; 2]> {
    (!rows.is_empty()).then(|| measure(rows))
}

pub(crate) fn manufacturing_size_on(
    rows: &[Vec<String>],
    spec: &crate::model::Spec,
    page: [f64; 2],
) -> Option<[f64; 2]> {
    (!rows.is_empty()).then(|| {
        size_on(
            rows,
            crate::hole_table::columns(spec, crate::pmi_lowering::SCHEDULE_ID),
            page,
        )
    })
}

/// The manufacturing requirements table (draftwright's `_place_manufacturing_schedule`): the
/// part's thread and knurl requirements when their callouts print `SEE MFG n`
/// ([`crate::pmi_lowering::manufacturing`]), drawn as a declared table is, its requirement
/// column left-aligned at the padding (`_left_align_cols=(1,)`), and placed towards the top
/// right clear of the views, the dimensions, the PMI and the title block, before the general
/// notes and the spec's tables (draftwright commits it before its other furniture). A table
/// that fits nowhere is a `table_not_placed` error naming it and the requirements it states,
/// which stays in the plan undrawn; as draftwright then prints the requirements on their
/// callouts in full, the finished drawing is planned again with them in full wording and no
/// table (`model::manufacturing_fallback`).
pub fn manufacturing(
    analysis: &Analysis,
    spec: &crate::model::Spec,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    dims: &[PlannedDimension],
) -> (Option<PlannedTable>, Vec<Problem>) {
    let m = crate::pmi_lowering::manufacturing_for(analysis, spec);
    let rows = m.rows();
    if rows.is_empty() {
        return (None, Vec::new());
    }
    let id = crate::pmi_lowering::SCHEDULE_ID;
    let [w, h] = size_on(&rows, crate::hole_table::columns(spec, id), sheet.page);
    let placed = place_split(
        &rows,
        sheet,
        &obstacles(analysis, sheet, views, dims),
        Corner::TopRight,
        crate::hole_table::columns(spec, id),
    )
    .map(|mut p| {
        let per = p
            .blocks
            .first()
            .map_or(rows.len() - 1, |b| b.cells_at.len() - 1);
        for (i, (row, cells)) in rows.iter().zip(&mut p.cells_at).enumerate() {
            let left = if p.blocks.is_empty() {
                p.columns[1]
            } else {
                p.blocks[i.saturating_sub(1) / per].columns[1]
            };
            if let (Some(s), Some(c)) = (span(&row[1]), cells[1].as_mut()) {
                c[0] = left + PAD + 0.5 * (s[1] - s[0]);
            }
        }
        for block in &mut p.blocks {
            if let (Some(s), Some(c)) = (span(&rows[0][1]), block.cells_at[0][1].as_mut()) {
                c[0] = block.columns[1] + PAD + 0.5 * (s[1] - s[0]);
            }
        }
        p
    });
    let scheduled = m.scheduled();
    let mut problems = Vec::new();
    if placed.is_none() {
        problems.push(Problem {
            id: format!("p:table_not_placed:{id}"),
            code: "table_not_placed".into(),
            severity: "error".into(),
            message: format!(
                "The manufacturing requirements table ({w:.1} × {h:.1} mm) has no place on the sheet clear of the views, their dimensions and the title block; it stays in the plan undrawn, so the callouts' SEE MFG references point to no table. Choose a larger sheet (sheet/size) or a smaller scale (sheet/scale)."
            ),
            subjects: vec![id.to_string()],
            // The threads and knurls it states, which are no items of their own.
            refs: scheduled,
            ..Default::default()
        });
    }
    let table = PlannedTable {
        sheet: 1,
        id: id.into(),
        rows,
        row_claims: m
            .tags
            .iter()
            .filter_map(|(source, tag)| {
                let owned = m.owned.iter().find(|o| &o.source == source)?;
                Some(RowClaims {
                    tag: tag.clone(),
                    claims: vec![crate::output::Claim {
                        feature: owned.id.clone(),
                        parameter: owned.aspect.into(),
                    }],
                })
            })
            .collect(),
        default: true,
        basis: format!(
            "source-authored: the part's effective thread and knurl requirements ({}): complete source wording for explicit or automatic note form and secondary manufacturing terms; the corresponding horizontal leaders refer to these source-bound rows (SEE MFG n), towards the sheet's top right",
            m.tags
                .iter()
                .map(|(s, _)| s.as_str())
                .collect::<Vec<_>>()
                .join(", ")
        ),
        state: State::placed_or(placed.is_some(), "table_not_placed"),
        placed,
    };
    (Some(table), problems)
}

/// The placed table as the blocks placed after it see it: its box as a label.
pub fn proxy(t: &PlannedTable) -> Option<PlannedDimension> {
    let p = t.placed.as_ref()?;
    Some(PlannedDimension {
        reference: false,
        determined_by: Vec::new(),
        region: None,
        id: t.id.clone(),
        kind: "table".into(),
        view: String::new(),
        value: 0.0,
        label: String::new(),
        terms: Vec::new(),
        measurements: Vec::new(),
        default: t.default,
        from_spec: Vec::new(),
        basis: t.basis.clone(),
        state: State::Shown,
        placed: Some(crate::dims::Placed {
            lines: Vec::new(),
            arrows: Vec::new(),
            label_at: [p.cells[0], p.cells[1]],
            rotation: 0.0,
            label_box: p.cells,
        }),
    })
}

#[cfg(test)]
mod split_tests {
    use super::*;

    #[test]
    fn shared_tables_split_without_losing_source_rows_or_repeating_claims() {
        let sheet = crate::model::PlannedSheet {
            page: [594.0, 420.0],
            ..serde_json::from_value(serde_json::json!({
                "size":"A2", "page":[594.0,420.0], "scale":"1:1", "scale_factor":1.0,
                "default":true, "basis":"test", "arrangement":"columns", "state":{"kind":"shown"}, "title":{}, "title_block":[400.0,13.0,581.0,50.0]
            })).unwrap()
        };
        let mut rows = vec![vec!["HEADER".to_string()]];
        rows.extend((0..61).map(|i| vec![i.to_string()]));
        let p = place_split(&rows, &sheet, &[], Corner::TopRight, None).unwrap();
        assert_eq!(p.blocks.len(), 2);
        assert_eq!(p.cells_at.len(), rows.len());
        assert_eq!(p.blocks[0].cells_at[0].len(), 1);
        for i in 1..rows.len() {
            assert_eq!(
                p.cells_at[i],
                p.blocks[(i - 1) / 31].cells_at[1 + (i - 1) % 31]
            );
        }
        assert!(p.blocks[0].cells[2] < p.blocks[1].cells[0]);
        assert!(place_split(&rows, &sheet, &[], Corner::TopRight, Some(1)).is_none());
        assert_eq!(
            place_split(&rows, &sheet, &[], Corner::TopRight, Some(3))
                .unwrap()
                .blocks
                .len(),
            3
        );
        // A table that fits the height can still need a shorter shape above fixed ink.
        let crowded = &rows[..18];
        let obstacle = [[0.0, 0.0, 594.0, 325.0]];
        assert!(place_split(crowded, &sheet, &obstacle, Corner::TopRight, Some(1)).is_none());
        assert_eq!(
            place_split(crowded, &sheet, &obstacle, Corner::TopRight, None)
                .unwrap()
                .blocks
                .len(),
            2
        );
        let small = &rows[..5];
        let whole = place_split(small, &sheet, &[], Corner::TopRight, None).unwrap();
        assert!(whole.blocks.is_empty());
        assert_eq!(
            whole,
            lay(
                small,
                &widths(small),
                place(measure(small), &sheet, &[], Corner::TopRight).unwrap()
            )
        );
    }
}
