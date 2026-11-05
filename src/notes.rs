//! General notes: the part's AP242 notes (`pmi:<identity>`,
//! [`crate::pmi_lowering::sheet_requirements`]), then the Spec's drawing notes (`n:<k>`), drawn as
//! draftwright draws its document notes (`render_document_notes`): one `GENERAL NOTES` table whose rows are
//! `document_note_rows`' (each note numbered by its place in the list, `1  BREAK ALL SHARP
//! EDGES`, wrapped at 48 characters with continuation rows indented three spaces), in IBM Plex
//! Mono at 3 mm, each row 3 mm plus 2 mm of padding above and below, ruled as `_build_table`
//! rules it: draftwright's single-column table, sharing the table's pieces (`src/tables.rs`).
//!
//! The block is reserved when the sheet is chosen, top right (draftwright's
//! `layout_required_tables`), and placed after the dimensions, as draftwright places it after
//! everything else, by `fit_box` towards the top right, first with 6 mm of breathing room and
//! then at the text padding: clear of the views' areas (their bands, where the dimension lanes
//! run), the isometric's drawn box, every dimension's ink and the title block. A block that
//! fits nowhere is a `notes_not_placed` error naming every note, which stays in the plan
//! undrawn; one that no sheet can hold at a standard scale is not reserved, so it never shrinks
//! the views (draftwright would take a computed scale for it).
//!
//! A Spec note matching a part note is covered by it, with an offered fix removing the Spec
//! copy. Omission uses `annotations/<id>/omit`, keeping an undrawn item in the plan.
//! Differences from draftwright: a continuation row keeps its three-space indent, where
//! draftwright's left alignment on each row's ink drops it; the views' areas are kept clear,
//! where draftwright's late placement sees the views' drawn boxes and the annotations' ink.

use serde::{Deserialize, Serialize};

use crate::dims::PlannedDimension;
use crate::items::State;
use crate::layout::Corner;
use crate::model::{Analysis, Invalid, PlannedSheet, PlannedView, Problem};
use crate::tables;

/// The block's heading, as draftwright's `document_note_rows` writes it.
pub const TITLE: &str = "GENERAL NOTES";
/// The characters a row holds before it wraps (draftwright's `textwrap.wrap(width=48)`).
pub const WRAP: usize = 48;
/// The text height, the padding around it and one row's height, mm: a table's.
pub use crate::tables::{PAD, ROW, TEXT, ink};

/// A general note as the spec states it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NoteSpec {
    /// `n:<k>`, `k` a whole number from 1.
    pub id: String,
    pub text: String,
}

/// A general note the sheet will carry.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlannedNote {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub from_spec: Vec<String>,
    /// Exact requirements carried by an automatic note, never inferred from its prose.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub measurements: Vec<crate::dims::Measurement>,
    pub id: String,
    /// The note as the spec gives it, without surrounding whitespace.
    pub text: String,
    /// Its number in the block, from 1; zero for an undrawn or covered note.
    pub number: usize,
    /// The rows it is printed as, numbered and wrapped.
    pub rows: Vec<String>,
    pub default: bool,
    pub basis: String,
    /// What became of it: shown, or why not ([`crate::items::State`]).
    pub state: crate::items::State,
    /// Where it is drawn; absent when the block has no place, and a problem says so.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub placed: Option<PlacedNote>,
}

/// Combine model notes and drawing notes without changing either source. Omitted and
/// covered items stay in the inventory but reserve no space and take no printed number.
pub(crate) fn reconcile(
    requirements: &[crate::pmi_lowering::SheetRequirement],
    analysis: &Analysis,
    spec: &crate::model::Spec,
) -> (Vec<NoteSpec>, Vec<PlannedNote>, Vec<Problem>) {
    let mut active = Vec::new();
    let mut inactive = Vec::new();
    let mut problems = Vec::new();
    let omitted = |id: &str| spec.annotations.get(id).is_some_and(|a| a.omit);
    let undrawn = |id: &str, text: &str, basis: String, state: State, default| PlannedNote {
        id: id.into(),
        text: text.trim().into(),
        number: 0,
        rows: vec![text.trim().into()],
        default,
        basis,
        state,
        placed: None,
        from_spec: Vec::new(),
        measurements: Vec::new(),
    };
    let model_notes: Vec<_> = requirements
        .iter()
        .filter(|r| r.kind == "document_note")
        .collect();
    let normalized = |text: &str| {
        text.split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase()
    };
    // An assembly drawn whole carries each part's notes. A note the parts state alike is stated
    // once; one that only some of them state names them, so the reader knows which part it is for.
    let part_of = |r: &crate::pmi_lowering::SheetRequirement| match &analysis.pmi {
        Some(crate::pmi::Reading::Read(report)) => report
            .records
            .iter()
            .find(|record| record.source_id == r.source)
            .map(|record| record.part),
        _ => None,
    };
    let drawn: Vec<_> = model_notes.iter().filter(|r| !omitted(&r.id)).collect();
    let noting: std::collections::BTreeSet<usize> =
        drawn.iter().filter_map(|r| part_of(r)).collect();
    let part_name = |k: usize| {
        analysis
            .parts
            .iter()
            .find(|p| p.part == k)
            .map(|p| p.name.trim().to_uppercase())
            .filter(|n| !n.is_empty())
            .unwrap_or_else(|| k.to_string())
    };
    for r in &model_notes {
        if omitted(&r.id) {
            inactive.push(undrawn(
                &r.id,
                &r.value,
                format!("{}; omitted by the spec", r.basis),
                State::OmittedByUser,
                false,
            ));
            continue;
        }
        let alike: Vec<_> = drawn
            .iter()
            .filter(|d| normalized(&d.value) == normalized(&r.value))
            .collect();
        // Invariant: r is drawn, so it is among the notes alike to it.
        let first = alike[0];
        if first.id != r.id {
            inactive.push(undrawn(
                &r.id,
                &r.value,
                format!(
                    "{}; stated once: general note {} states the same text",
                    r.basis, first.id
                ),
                State::Covered {
                    by: first.id.clone(),
                },
                true,
            ));
            continue;
        }
        let parts: std::collections::BTreeSet<usize> =
            alike.iter().filter_map(|d| part_of(d)).collect();
        let text = if noting.len() > 1 && !parts.is_empty() && parts.len() < noting.len() {
            let names: Vec<_> = parts.iter().map(|&k| part_name(k)).collect();
            format!(
                "{} ({} {})",
                r.value,
                if names.len() == 1 { "PART" } else { "PARTS" },
                names.join(", ")
            )
        } else {
            r.value.clone()
        };
        active.push(NoteSpec {
            id: r.id.clone(),
            text,
        });
    }
    if let Some(text) = assembly_note(analysis) {
        if omitted(ASSEMBLY_NOTE) {
            inactive.push(undrawn(
                ASSEMBLY_NOTE,
                &text,
                format!("{ASSEMBLY_BASIS}; omitted by the spec"),
                State::OmittedByUser,
                false,
            ));
        } else {
            active.push(NoteSpec {
                id: ASSEMBLY_NOTE.into(),
                text,
            });
        }
    }
    for (i, n) in spec.notes.iter().enumerate() {
        let matches: Vec<_> = model_notes
            .iter()
            .filter(|r| {
                normalized(&r.value) == normalized(&n.text)
                    || matches!(&analysis.pmi, Some(crate::pmi::Reading::Read(report)) if report.records.iter().any(|record| record.source_id == r.source && normalized(&record.label) == normalized(&n.text)))
            })
            .collect();
        for r in &matches {
            let mut remaining = spec.notes.clone();
            remaining.remove(i);
            problems.push(Problem {
                crossing_kinds: Vec::new(),
                ink_is_leader: None,
                id: format!("p:note_duplicated_in_spec:{}:{}", r.source, n.id),
                code: "note_duplicated_in_spec".into(), severity: "info".into(),
                message: format!("The Spec note {:?} repeats the part's PMI note {:?}. Keep the requirement in the model; remove the Spec copy.", n.text, r.value),
                subjects: vec![r.id.clone(), n.id.clone()],
                refs: vec![r.source.clone(), format!("notes/{i}")],
                // Invariant: Document fields and map keys have JSON representations; serialization performs no external I/O.
                fix: vec![crate::items::Op::set("notes", serde_json::to_value(remaining).expect("notes serialise"))],
                fixes: Vec::new(),
            });
        }
        if let Some(r) = matches.iter().find(|r| !omitted(&r.id)) {
            inactive.push(undrawn(
                &n.id,
                &n.text,
                format!(
                    "authored: the spec's note {} (notes/{i}), stated by PMI {}",
                    n.id, r.source
                ),
                State::Covered { by: r.id.clone() },
                false,
            ));
        } else {
            active.push(n.clone());
        }
    }
    (active, inactive, problems)
}

/// The automatic note saying that only an assembly's principal part is drawn (#219's
/// `assembly_principal_drawn`): the sheet's title names the assembly, so without it a reader
/// takes the one part for the whole. `annotations/n:assembly/omit` leaves it out, and
/// `annotations/n:assembly/text` words it.
pub(crate) const ASSEMBLY_NOTE: &str = "n:assembly";
const ASSEMBLY_BASIS: &str = "automatic: the file is an assembly whose principal part, holding the others within its box, is drawn alone, so the sheet says which part it shows and that the others are not shown";

/// The text of [`ASSEMBLY_NOTE`], when only an assembly's principal part is drawn: the part by
/// name, and the others by name (up to three) or by count.
pub(crate) fn assembly_note(analysis: &Analysis) -> Option<String> {
    let k = analysis.binding.part_index.filter(|_| analysis.principal)?;
    // "PART" before the names, so a part named after its material ("Metal") is not read as
    // a statement about material.
    let name = |p: &crate::model::FilePart| {
        let n = p.name.trim();
        if n.is_empty() {
            p.part.to_string()
        } else {
            n.to_uppercase()
        }
    };
    let drawn = analysis.parts.iter().find(|p| p.part == k)?;
    let others: Vec<String> = analysis
        .parts
        .iter()
        .filter(|p| p.part != k)
        .map(name)
        .collect();
    let rest = match others.len() {
        0 => return None,
        1 => format!("PART {}", others[0]),
        2 | 3 => format!("PARTS {}", others.join(", ")),
        n => format!("{n} OTHER PARTS"),
    };
    Some(format!(
        "ASSEMBLY OF {} PARTS: ONLY PART {} SHOWN; {rest} NOT SHOWN",
        analysis.parts.len(),
        name(drawn)
    ))
}

/// Restore the original Spec list paths after PMI insertion and duplicate suppression.
pub(crate) fn provenance(
    notes: &mut [PlannedNote],
    requirements: &[crate::pmi_lowering::PlannedRequirement],
    spec: &crate::model::Spec,
) {
    for note in notes {
        if let Some(r) = requirements.iter().find(|r| r.id == note.id) {
            note.basis = format!(
                "{}; general note {} in the {TITLE} block before the spec's notes",
                r.basis, note.number
            );
        } else if let Some(i) = spec.notes.iter().position(|n| n.id == note.id) {
            note.basis = spec_basis(&note.id, i, note.number);
        } else if note.id == ASSEMBLY_NOTE {
            note.default = true;
            note.basis = format!(
                "{ASSEMBLY_BASIS}; general note {} in the {TITLE} block before the spec's notes",
                note.number
            );
        }
    }
}

fn spec_basis(id: &str, index: usize, number: usize) -> String {
    format!(
        "authored: the spec's note {id} (notes/{index}), general note {number} in the {TITLE} block, which goes towards the sheet's top right as draftwright places its document notes"
    )
}

/// A note's place in the block, mm on the page.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlacedNote {
    /// Each row's ink centre, in the order of `rows`.
    pub rows_at: Vec<[f64; 2]>,
    /// Its rows' cells, `[x0, y0, x1, y1]`.
    #[serde(rename = "box")]
    pub cells: [f64; 4],
}

/// The block the notes are drawn in: its heading and box, mm on the page.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NotesBlock {
    /// Authored notes use a ruled table; automatic radius notes form a compact plain block.
    #[serde(default = "ruled_default", skip_serializing_if = "is_ruled")]
    pub ruled: bool,
    pub title: String,
    /// The heading's ink centre.
    pub title_at: [f64; 2],
    /// The whole table, `[x0, y0, x1, y1]`, a row of [`ROW`] per heading and note row.
    #[serde(rename = "box")]
    pub cells: [f64; 4],
}

fn ruled_default() -> bool {
    true
}
fn is_ruled(ruled: &bool) -> bool {
    *ruled
}

/// A note's ID is `n:` and a whole number from 1, written without leading zeros.
fn well_formed(id: &str) -> bool {
    id.strip_prefix("n:").is_some_and(|k| {
        !k.is_empty() && !k.starts_with('0') && k.bytes().all(|b| b.is_ascii_digit())
    })
}

/// The first `n:<k>` after every well-formed ID in *ids*.
pub fn next_id<'a>(ids: impl IntoIterator<Item = &'a str>) -> String {
    let k = ids
        .into_iter()
        .filter(|id| well_formed(id))
        .filter_map(|id| id[2..].parse::<u64>().ok())
        .max()
        .unwrap_or(0);
    format!("n:{}", k + 1)
}

/// The notes are refused, with what to write instead, when an ID is not `n:<k>`, two notes
/// share an ID, or a note has no text.
pub fn check(notes: &[NoteSpec]) -> Result<(), Invalid> {
    let free = || vec![next_id(notes.iter().map(|n| n.id.as_str()))];
    for (i, n) in notes.iter().enumerate() {
        if !well_formed(&n.id) {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "notes/{i} has the ID {:?}; a note's ID is n:<k>, k a whole number from 1 (n:1, n:2, …).",
                    n.id
                ),
                choices: free(),
            });
        }
        if notes[..i].iter().any(|m| m.id == n.id) {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "Two notes have the ID {:?} (notes/{i} and an earlier one); give each note its own.",
                    n.id
                ),
                choices: free(),
            });
        }
        if n.text.trim().is_empty() {
            return Err(Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "Note {} (notes/{i}) has no text; give it its text, or remove it.",
                    n.id
                ),
                choices: vec![],
            });
        }
    }
    Ok(())
}

/// *text* broken into rows of at most [`WRAP`] characters as Python's `textwrap.wrap` breaks it
/// (`break_long_words=False`, `break_on_hyphens=False`): tabs expanded to 8 columns, ASCII
/// whitespace read as spaces, words never split (a longer one has a row of its own), runs of
/// spaces inside a row kept, and spaces at a row's ends dropped.
pub fn wrap(text: &str) -> Vec<String> {
    const SPACE: [char; 6] = ['\t', '\n', '\x0b', '\x0c', '\r', ' '];
    let mut expanded = String::new();
    let mut column = 0;
    for c in text.chars() {
        match c {
            '\t' => {
                let n = 8 - column % 8;
                expanded.extend(std::iter::repeat_n(' ', n));
                column += n;
            }
            '\n' | '\r' => {
                expanded.push(' ');
                column = 0;
            }
            c if SPACE.contains(&c) => {
                expanded.push(' ');
                column += 1;
            }
            c => {
                expanded.push(c);
                column += 1;
            }
        }
    }
    // Runs of spaces and runs of anything else, last first.
    let mut chunks: Vec<String> = Vec::new();
    for c in expanded.chars() {
        match chunks.last_mut() {
            Some(last) if (last.starts_with(' ')) == (c == ' ') => last.push(c),
            _ => chunks.push(c.to_string()),
        }
    }
    chunks.reverse();
    let blank = |s: &str| s.trim().is_empty();
    let len = |s: &str| s.chars().count();
    let mut lines = Vec::new();
    while !chunks.is_empty() {
        if !lines.is_empty() && chunks.last().is_some_and(|c| blank(c)) {
            chunks.pop();
        }
        let mut line: Vec<String> = Vec::new();
        let mut width = 0;
        while let Some(c) = chunks.last() {
            if width + len(c) > WRAP {
                break;
            }
            width += len(c);
            // Invariant: The while-let condition just observed chunks.last() as Some.
            line.push(chunks.pop().expect("a chunk"));
        }
        if line.is_empty() && chunks.last().is_some_and(|c| len(c) > WRAP) {
            // Invariant: The is_some_and condition just observed chunks.last() as Some.
            line.push(chunks.pop().expect("a chunk"));
        }
        if line.last().is_some_and(|c| blank(c)) {
            line.pop();
        }
        if !line.is_empty() {
            lines.push(line.concat());
        }
    }
    lines
}

/// Note *number*'s rows: the first numbered, the rest indented to its text.
pub fn rows(number: usize, text: &str) -> Vec<String> {
    let lines = wrap(text.trim());
    let lines = if lines.is_empty() {
        vec![String::new()]
    } else {
        lines
    };
    lines
        .iter()
        .enumerate()
        .map(|(i, l)| {
            if i == 0 {
                format!("{number}  {l}")
            } else {
                format!("   {l}")
            }
        })
        .collect()
}

/// The table's rows, heading first, and each note's first row among them.
fn table(notes: &[NoteSpec]) -> (Vec<String>, Vec<Vec<String>>) {
    let per: Vec<Vec<String>> = notes
        .iter()
        .enumerate()
        .map(|(i, n)| rows(i + 1, &n.text))
        .collect();
    let mut all = vec![TITLE.to_string()];
    all.extend(per.iter().flatten().cloned());
    (all, per)
}

/// The block's width (its widest row's ink, the rows aligned on their pens, and the padding
/// each side; at least 2.5 text heights, as `_table_metrics`) and the pen's offset from its
/// left edge.
fn width(rows: &[String]) -> (f64, f64) {
    // Each row's ink laid from a pen at 0, leading spaces advancing the pen.
    let spans: Vec<[f64; 2]> = rows.iter().filter_map(|r| tables::span(r)).collect();
    let lo = spans.iter().map(|s| s[0]).fold(f64::INFINITY, f64::min);
    let hi = spans.iter().map(|s| s[1]).fold(f64::NEG_INFINITY, f64::max);
    ((hi - lo + 2.0 * PAD).max(2.5 * TEXT), PAD - lo)
}

/// The block's size, mm, or none when there are no notes: what the sheet reserves for it.
pub fn size(notes: &[NoteSpec]) -> Option<[f64; 2]> {
    if notes.is_empty() {
        return None;
    }
    let (rows, _) = table(notes);
    Some([width(&rows).0, ROW * rows.len() as f64])
}

/// What [`plan`] returns: the notes, the block when it has a place, and the problems.
pub type Planned = (Vec<PlannedNote>, Option<NotesBlock>, Vec<Problem>);

/// The spec's notes as Plan items, placed in one block clear of the views, the dimensions and
/// the title block, or reported when the block has no place.
pub fn plan(
    notes: &[NoteSpec],
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    dims: &[PlannedDimension],
) -> Planned {
    plan_with_rows(notes, analysis, sheet, views, dims, ROW, true)
}

fn plan_with_rows(
    notes: &[NoteSpec],
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    dims: &[PlannedDimension],
    row_height: f64,
    ruled: bool,
) -> Planned {
    if notes.is_empty() {
        return (Vec::new(), None, Vec::new());
    }
    let ids = match &analysis.pmi {
        Some(crate::pmi::Reading::Read(r)) => r.identities(),
        _ => crate::pmi::Identities::default(),
    };
    let (all, per) = table(notes);
    let (w, pen) = width(&all);
    let h = row_height * all.len() as f64;
    let obstacles = tables::obstacles(analysis, sheet, views, dims);
    let at = if ruled {
        tables::place([w, h], sheet, &obstacles, Corner::TopRight)
    } else {
        let [pw, ph] = sheet.page;
        crate::layout::fit_auxiliary_box(
            [w, h],
            [16.0, 16.0, pw - 16.0, ph - 16.0],
            &obstacles,
            Corner::TopRight,
            PAD,
        )
    };
    // Row *i*'s ink centre, counted from the heading, and its cell's bottom.
    let font = crate::dims::font();
    let centre = |x0: f64, top: f64, i: usize, row: &str| {
        let laid = tables::text_ink(row, font);
        let mid = top - (i as f64 + 0.5) * row_height;
        [x0 + pen + 0.5 * (laid.min[0] + laid.max[0]), mid]
    };
    let mut problems = Vec::new();
    let block = at.map(|[x0, y0]| NotesBlock {
        ruled,
        title: TITLE.into(),
        title_at: centre(x0, y0 + h, 0, TITLE),
        cells: [x0, y0, x0 + w, y0 + h],
    });
    if block.is_none() {
        problems.push(Problem {
            id: "p:notes_not_placed:notes".into(),
            code: "notes_not_placed".into(),
            severity: "error".into(),
            message: format!(
                "The general notes block ({w:.1} × {h:.1} mm) has no place on the sheet clear of the views, their dimensions and the title block; its notes stay in the plan undrawn. Shorten or remove notes, or choose a larger sheet (sheet/size) or a smaller scale (sheet/scale)."
            ),
            subjects: notes.iter().map(|n| n.id.clone()).collect(),
            ..Default::default()
        });
    }
    let mut first = 1;
    let items = notes
        .iter()
        .zip(per)
        .enumerate()
        .map(|(i, (n, rows))| {
            let placed = at.map(|[x0, y0]| {
                let top = y0 + h;
                PlacedNote {
                    rows_at: rows
                        .iter()
                        .enumerate()
                        .map(|(j, r)| centre(x0, top, first + j, r))
                        .collect(),
                    cells: [
                        x0,
                        top - (first + rows.len()) as f64 * row_height,
                        x0 + w,
                        top - first as f64 * row_height,
                    ],
                }
            });
            first += rows.len();
            // A PMI part note is named by its record's item ID; its basis names the
            // record's source.
            let source = n.id.starts_with("pmi:").then(|| {
                ids.source(&n.id)
                    .map_or_else(|| n.id.clone(), str::to_string)
            });
            PlannedNote {
                from_spec: Vec::new(),
                measurements: Vec::new(),
                id: n.id.clone(),
                text: n.text.trim().into(),
                number: if placed.is_some() { i + 1 } else { 0 },
                rows: if placed.is_some() { rows } else { wrap(&n.text) },
                default: source.is_some(),
                basis: match source {
                    Some(s) => format!(
                        "source-authored: the part's AP242 note {s}, general note {} in the {TITLE} block before the spec's notes (draftwright's DocumentNote)",
                        i + 1
                    ),
                    None => spec_basis(&n.id, i, i + 1),
                },
                state: State::placed_or(placed.is_some(), "notes_not_placed"),
                placed,
            }
        })
        .collect();
    (items, block, problems)
}

/// The block's rules: its outline and a line under each row, as `_build_table` rules a table.
pub fn rules(block: &NotesBlock) -> Vec<[[f64; 2]; 2]> {
    if !block.ruled {
        return Vec::new();
    }
    let [x0, _, x1, _] = block.cells;
    tables::ruling(block.cells, &[x0, x1])
}

/// Every row the block draws, heading first, with its ink centre.
pub fn texts<'a>(block: &'a NotesBlock, notes: &'a [PlannedNote]) -> Vec<(&'a str, [f64; 2])> {
    let mut out = vec![(block.title.as_str(), block.title_at)];
    for n in notes {
        if let Some(p) = &n.placed {
            out.extend(
                n.rows
                    .iter()
                    .map(String::as_str)
                    .zip(p.rows_at.iter().copied()),
            );
        }
    }
    out
}

/// Attach the automatic radius note's exact identities, retaining omitted notes as editable
/// items. Explicit placement detaches its rows from the block and anchors them to a view.
pub(crate) fn radius_notes(
    notes: &mut Vec<PlannedNote>,
    generated: &[(NoteSpec, Vec<crate::dims::Measurement>)],
    spec: &crate::model::Spec,
    sheet: &PlannedSheet,
    views: &[PlannedView],
) -> Vec<Problem> {
    use crate::placement_intent::Side;
    let mut problems = Vec::new();
    for (given, measurements) in generated {
        let index = notes
            .iter()
            .position(|n| n.id == given.id)
            .unwrap_or_else(|| {
                notes.push(PlannedNote {
                    from_spec: Vec::new(),
                    id: given.id.clone(),
                    text: given.text.clone(),
                    number: notes.len() + 1,
                    rows: rows(notes.len() + 1, &given.text),
                    default: true,
                    basis: String::new(),
                    state: State::OmittedByUser,
                    placed: None,
                    measurements: Vec::new(),
                });
                notes.len() - 1
            });
        let n = &mut notes[index];
        n.measurements = measurements.clone();
        if n.id == crate::dims::blends::RELIEF_NOTE {
            // This profile requirement prints no measured number, including a note index.
            n.rows = vec![n.text.clone()];
        }
        n.default = !spec.annotations.contains_key(&n.id);
        n.basis = if n.id == crate::dims::blends::RELIEF_NOTE {
            "grouped tiny, complex relief floors and their short walls share a shallow layer on one broad carrier; their profiles are governed by the model, with no radius credit".into()
        } else {
            "a true default states the most frequent equal radius once; other radii need explicit callouts, and exact identities retain coverage authority".into()
        };
        let entry = spec.annotations.get(&n.id);
        if entry.is_some_and(|a| a.text.is_some()) {
            n.from_spec = vec!["text".into()];
            n.basis += "; from_spec: text; original measured terms retained";
        }
        if spec.dimension_source == crate::model::DimensionSource::Authored && entry.is_none() {
            n.placed = None;
            n.state = State::NotAuthored;
            continue;
        }
        if entry.is_some_and(|e| e.omit) {
            n.placed = None;
            n.state = State::OmittedByUser;
            continue;
        }
        let Some(place) = entry
            .and_then(|e| e.place.as_ref())
            .filter(|p| p.side.is_some() || p.lane.is_some() || p.along.is_some())
        else {
            continue;
        };
        let view_name = place.view.as_deref().unwrap_or("front");
        let Some(v) = views.iter().find(|v| v.id == format!("v:{view_name}")) else {
            n.placed = None;
            n.state = State::NotDrawable {
                reason_code: "notes_not_placed".into(),
            };
            problems.push(Problem {
                id: format!("p:notes_not_placed:{}", n.id),
                code: "notes_not_placed".into(),
                severity: "error".into(),
                message: format!(
                    "{} cannot be placed relative to the omitted {view_name} view.",
                    crate::wording::capital(&crate::wording::note(&n.text))
                ),
                subjects: vec![n.id.clone()],
                ..Default::default()
            });
            continue;
        };
        let (w, pen) = width(&n.rows);
        let h = ROW * n.rows.len() as f64;
        let [x0, y0, x1, y1] = v.area;
        let t = place.along.map_or(0.5, |a| a.fraction());
        let gap = 6.0 + ROW * place.lane.unwrap_or(0) as f64;
        let at = match place.side.unwrap_or(Side::Above) {
            Side::Above => [x0 + t * (x1 - x0) - w / 2.0, y1 + gap],
            Side::Below => [x0 + t * (x1 - x0) - w / 2.0, y0 - gap - h],
            Side::Left => [x0 - gap - w, y0 + t * (y1 - y0) - h / 2.0],
            Side::Right => [x1 + gap, y0 + t * (y1 - y0) - h / 2.0],
        };
        let [x, y] = at;
        n.placed = Some(PlacedNote {
            rows_at: n
                .rows
                .iter()
                .enumerate()
                .map(|(i, row)| {
                    let laid = tables::text_ink(row, crate::dims::font());
                    [
                        x + pen + 0.5 * (laid.min[0] + laid.max[0]),
                        y + h - (i as f64 + 0.5) * ROW,
                    ]
                })
                .collect(),
            cells: [x, y, x + w, y + h],
        });
        n.state = State::Shown;
        // Explicit placements remain visible, with impossible requests reported on their ID.
        // Invariant: The placement was assigned Some immediately above.
        let cells = n.placed.as_ref().unwrap().cells;
        if cells[0] < 0.0 || cells[1] < 0.0 || cells[2] > sheet.page[0] || cells[3] > sheet.page[1]
        {
            problems.push(Problem {
                id: format!("p:annotation_off_sheet:{}", n.id),
                code: "annotation_off_sheet".into(),
                severity: "warning".into(),
                message: format!(
                    "{} lies outside the sheet at its requested placement.",
                    crate::wording::capital(&crate::wording::note(&n.text))
                ),
                subjects: vec![n.id.clone()],
                ..Default::default()
            });
        }
    }
    problems
}

/// Automatic radius notes use the actual view outlines and placed annotation ink, so a free
/// area of a reserved band remains usable without moving the established drawing.
pub(crate) fn plan_radii(
    notes: &[NoteSpec],
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    dims: &[PlannedDimension],
    authored: Option<&NotesBlock>,
) -> Planned {
    let mut actual = views.to_vec();
    for v in actual
        .iter_mut()
        .filter(|v| ["v:front", "v:plan", "v:side"].contains(&v.id.as_str()))
    {
        let view =
            // Invariant: Planned cameras come from the fixed views or checked section/detail cameras, with nonparallel axes.
            crate::haecceity::hlr::View::new(v.direction, v.up).expect("planned principal view");
        let (size, _) = crate::model::extent(analysis, &view);
        v.area = [
            v.at[0] - 0.5 * v.scale * size[0],
            v.at[1] - 0.5 * v.scale * size[1],
            v.at[0] + 0.5 * v.scale * size[0],
            v.at[1] + 0.5 * v.scale * size[1],
        ];
    }
    let mut ink = dims.to_vec();
    if let Some(block) = authored {
        ink.push(proxy(block));
    }
    plan_with_rows(notes, analysis, sheet, &actual, &ink, TEXT + 2.0, false)
}

/// A notes block is an obstacle to blocks placed after it, just as a table is.
pub(crate) fn proxy(block: &NotesBlock) -> PlannedDimension {
    PlannedDimension {
        callout_offset: None,
        reference: false,
        determined_by: Vec::new(),
        region: None,
        id: "notes".into(),
        kind: "notes".into(),
        view: String::new(),
        value: 0.0,
        label: String::new(),
        terms: Vec::new(),
        measurements: Vec::new(),
        default: true,
        from_spec: Vec::new(),
        basis: String::new(),
        state: State::Shown,
        placed: Some(crate::dims::Placed {
            lines: Vec::new(),
            arrows: Vec::new(),
            label_at: [block.cells[0], block.cells[1]],
            rotation: 0.0,
            label_box: block.cells,
        }),
    }
}
