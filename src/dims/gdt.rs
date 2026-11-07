//! Feature control frames, datum feature symbols and labels: the part's AP242 PMI as
//! [`crate::pmi_lowering`] lowers it, placed beside its view and drawn (draftwright's
//! `annotations/_gdt.py` `render_gdt`, and `build123d_drafting`'s `FeatureControlFrame`,
//! `DatumFeature`, `TextBlock` and `Leader`).
//!
//! - **A feature control frame** is draftwright's glyph at the dimensions' text height (3 mm):
//!   cells two text heights tall, the characteristic's symbol drawn in strokes in the first, the
//!   zone's `⌀` (a ring and a stroke) or `S⌀`, the magnitude and the material condition's
//!   ringed letter in the second, one cell per datum letter after. Its leader runs straight from
//!   its site into its strip, with an arrowhead at the site, a shelf of 2 mm, and the frame hung
//!   from the shelf's end (draftwright's `Leader` with a callout), the all-around circle or the
//!   all-over double circle at the bend.
//! - **A datum feature symbol** is drawn as ISO 5459 and ASME Y14.5 draw it: a filled triangle
//!   whose base sits on the datum's surface at its site, a line along the outward normal to a
//!   square frame two text heights wide holding the letter. draftwright hangs its `DatumFeature`
//!   glyph (triangle, line and frame together) off an arrowed leader's shelf instead, so its
//!   triangle sits beside the frame in mid-air, not on the surface.
//! - **A surface texture symbol** (a face's AP242 surface texture) is ISO 1302's graphical
//!   symbol, as the default finish beside the title block draws it, standing on its leader's
//!   shelf, its parameters (`Ra 0.8`) under the bar: its leader runs from a proven site on its
//!   face's edge line along the face's outward normal, as a datum's does, with an arrowhead at
//!   the site (draftwright's `Finish`, a `SurfaceFinish` glyph on a `Leader`, which only
//!   draftwright's face-finish lowering for one external cylinder makes). A cylinder with no
//!   proven silhouette is reached head-on instead, its leader ending in a dot inside the
//!   outline (ISO 128-22).
//! - **A label** (a surface or common label: `A`, `F1.1`, `2X R`) is its text at the
//!   dimensions' text height, unframed, hung from a leader as a frame is (draftwright's `Note`,
//!   a `TextBlock` callout on a `Leader`).
//!
//! **Placement** (additive to the dimensions' and leaders'): the layout reserves each item's
//! lowering strip ([`demand`], [`crate::layout::Strips::reserve_gdt`]). Automatic datums first
//! try compact tiers between their face and the first dimension lane, retaining the triangle,
//! shaft, frame and normal clearances. Only after these choices are exhausted do they try the
//! existing tiers beyond the dimensions. Cylinder sites are proved on either trimmed-face
//! silhouette; planar symbols may stand on a drawn face extension beyond either end of the
//! view outline. Constrained planar datums precede cylinders. At equal clearance and crossing
//! counts automatic datums prefer shorter leaders; original site order breaks remaining ties.
//! Filled triangles reserve clearance alongside the letter boxes, and final-plan lint rejects
//! touching or overlapping datum triangles. A plane's finish keeps its outward side; a
//! cylinder's tries compact tiers in its own strip, then the other proven silhouette only when
//! its own has no place clear of every label and line; its leader keeps a text height from
//! any line it runs alongside, and the finish with fewest sites in a strip is placed first. Frames and labels can retry the other
//! strips (`_gdt_retry_sides`).
//!
//! All glyphs share the same obstacle grade: clearance from labels, views, sheet furniture and
//! reserved-area boundaries, followed by the count of leader crossings with dimension lines,
//! extension lines, arrow shafts and other leaders (the collision reporter's `lines_meet`,
//! including collinear travel). Only the all-zero grade ends the search immediately. Within
//! equal clearances, fewer crossings win. When the original arrangement separates datum
//! triangles, a new arrangement must not introduce any annotation contact (unique unordered
//! pairs, independent of reporting direction). An original arrangement with touching triangles
//! is already illegal: repair may trade soft contacts for fewer symbol overlaps, retaining
//! all findings. Joint attachment reservations are used only when ordinary placement cannot
//! separate the triangles. Later frames must not be displaced merely to clear
//! an earlier leader in an already legal arrangement. Unavoidable collisions are
//! retained and reported (Policy B). Explicit placement intents retain their strip/tier rules.
//! If the full rearrangement introduces contact, the datum changes are also tried alone.
//! Pins and canonical hand placements take precedence over automatic compact placement.
//!
//! Each item's text layer is its compartments as the plan states them (`⌖`, `⌀0.05Ⓜ`, `A`, …),
//! one invisible run over each, so the PDF and SVG can be searched and lint reads it. The ink of
//! the symbols is strokes, so the font need hold only the digits, letters and `ø` (checked by
//! `tests/pmi_lowering.rs`); a label's text is drawn as the file states it, a character the font
//! lacks as its missing-glyph box (as draftwright's FreeType draws it).

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use super::placement::{self, Frame, Page, boxes_overlap, crosses};
use super::{ARROW, ARROW_HALF, FONT, P2, Placed, PlannedDimension, TEXT, font};
use crate::items::{Op, State};
use crate::model::{Analysis, PlannedSheet, PlannedView, Problem, Spec};
use crate::output::{Annotation, Curve, HAlign, Item, TextRun, VAlign};
use crate::pmi_lowering::{Glyph, Lowered};
use crate::text::Segment;

/// A feature control frame or datum feature symbol the sheet will carry.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlannedGdt {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub from_spec: Vec<String>,
    /// `pmi:<identity>`, the record's semantic identity ([`crate::pmi::identity`]), never its
    /// Part 21 instance (that is `source`).
    pub id: String,
    /// `control_frame`, `datum_feature` or `label`.
    pub kind: String,
    pub view: String,
    /// Its strip: `above`, `below`, `left` or `right` of the view.
    pub side: String,
    /// The PMI record it draws (`Record::source_id`).
    pub source: String,
    /// The drawing's faces it applies to.
    pub faces: Vec<usize>,
    /// What it states, compartment by compartment, as its text runs: the characteristic's
    /// symbol, the zone, magnitude and material condition, then each datum letter; or a datum's
    /// letter.
    pub cells: Vec<String>,
    /// `all_around` or `all_over`, drawn at the leader's bend.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub modifiers: Vec<String>,
    /// What it states as one line ([`crate::items::frame_label`]).
    pub label: String,
    pub default: bool,
    pub basis: String,
    /// What became of it: shown, or why not ([`crate::items::State`]).
    pub state: crate::items::State,
    /// Its ink on the page; absent when it cannot be drawn, and a problem says why.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub placed: Option<GdtPlaced>,
}

/// A frame's or symbol's ink, mm on the page.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GdtPlaced {
    /// Where it attaches: the leader's arrowhead or the triangle's base.
    pub tip: P2,
    /// Its leader's lines: a frame's shaft and shelf, a symbol's line to its frame.
    pub lines: Vec<[P2; 2]>,
    /// Arrowheads, `[tip, tail]`.
    pub arrows: Vec<[P2; 2]>,
    /// The frame's box.
    pub label_box: [f64; 4],
    /// The glyph's straight strokes: the frame's rules and dividers, the symbols' lines.
    pub strokes: Vec<[P2; 2]>,
    /// Its circles and arcs, `[cx, cy, r, from, to]` (degrees anticlockwise).
    pub arcs: Vec<[f64; 5]>,
    /// Filled outlines: a datum's triangle.
    pub fills: Vec<Vec<P2>>,
    /// Letters and numbers drawn from the font, each centred on its point.
    pub letters: Vec<GdtText>,
    /// The text layer: each compartment's run, centred on its cell.
    pub runs: Vec<GdtText>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GdtText {
    pub text: String,
    pub at: P2,
    pub size: f64,
}

/// draftwright's draft: the leader's shelf (`pad_around_text`), the frame's metrics
/// (`_gdt_metrics`: cell padding, the zone sign's and the material ring's radii), and the
/// datum symbol's triangle and frame (`DatumFeature`).
const H: f64 = 2.0 * TEXT;
pub(super) const SHELF: f64 = 2.0;
const PAD: f64 = 0.6 * TEXT;
pub(super) const TRIANGLE: f64 = 1.4 * TEXT;
const RING: f64 = 0.7 * ARROW;
/// The clearance beyond the dimensions, and between tiers.
const CLEAR: f64 = crate::layout::GDT_CLEAR;
const TIERS: usize = 16;

/// A glyph laid out at its own origin, before it is placed.
#[derive(Default)]
struct Laid {
    strokes: Vec<[P2; 2]>,
    arcs: Vec<[f64; 5]>,
    letters: Vec<GdtText>,
    runs: Vec<GdtText>,
    size: P2,
}

fn text(s: &str, at: P2, size: f64) -> GdtText {
    GdtText {
        text: s.into(),
        at,
        size,
    }
}

fn circle(c: P2, r: f64) -> [f64; 5] {
    [c[0], c[1], r, 0.0, 360.0]
}

/// The width of *s*'s ink at *size* in the sheet's font (IBM Plex Sans Condensed): the width
/// haecceity's shared notation measures cells with, so frames, labels and surface texture
/// symbols are sized for the ink the sheet draws.
fn width(s: &str, size: f64) -> f64 {
    let l = font().text(s, size);
    l.max[0] - l.min[0]
}

/// Frame *p* with the count of the *n* features it applies to (`4×`) over its box at its left end
/// (ISO 1101: the number of features above the tolerance frame), its box grown to hold it.
pub(super) fn with_count(p: &mut GdtPlaced, n: usize) {
    let count = format!("{n}×");
    // Final witness repairs can revisit a frame already led to the same group.
    // Its translated glyph already carries the count and reserves its paper.
    if p.runs.iter().any(|run| run.text == count) {
        return;
    }
    let b = p.label_box;
    let w = width(&count, TEXT);
    let at = [b[0] + 0.5 * w, b[3] + PAD + 0.5 * TEXT];
    // Drawn from the font, as the frame's own text is, and first on its text layer.
    p.letters.push(text(&count, at, TEXT));
    p.runs.insert(0, text(&count, at, TEXT));
    p.label_box[3] = b[3] + 2.0 * PAD + TEXT;
}

/// A feature control frame laid out from its lower left corner (`FeatureControlFrame`):
/// haecceity's shared ISO 1101 frame (#258), its cells measured in the sheet's font.
fn control_frame(glyph: &Glyph) -> (Laid, Vec<String>) {
    let Glyph::ControlFrame {
        characteristic,
        tolerance,
        zone,
        material,
        datums,
        ..
    } = glyph
    else {
        // Invariant: This branch is reached only for a control-frame annotation.
        unreachable!("a control frame")
    };
    let c = haecceity::presentation::gdt::control_frame(
        characteristic,
        tolerance,
        *zone,
        *material,
        datums,
        &width,
    );
    let cells = c.cells.clone();
    (laid(c), cells)
}

/// A callout of haecceity's shared notation as this module lays it out.
fn laid(c: haecceity::presentation::Callout) -> Laid {
    let text = |t: haecceity::presentation::Text| GdtText {
        text: t.text,
        at: t.at,
        size: t.size,
    };
    Laid {
        strokes: c.strokes,
        arcs: c.arcs,
        letters: c.letters.into_iter().map(text).collect(),
        runs: c.runs.into_iter().map(text).collect(),
        size: c.size,
    }
}

/// A datum's frame laid out about its centre: haecceity's shared datum feature symbol frame
/// (#258), which states the letter without measuring it, so its geometry does not depend on the
/// sheet's font.
fn datum_frame(letter: &str) -> Laid {
    laid(haecceity::presentation::datum(letter))
}

/// A label's text laid out from its lower left corner, a text height tall per line: each line is
/// haecceity's shared label (#258) measured in the sheet's font, its run set unpadded in the
/// label's lines (the lanes and leaders that place a label are this module's).
fn label(s: &str) -> Laid {
    let lines = crate::callouts::prose(s);
    let h = TEXT + (lines.len() - 1) as f64 * crate::callouts::line_spacing();
    let mut laid = Laid::default();
    let mut w: f64 = 0.5 * TEXT;
    for (i, line) in lines.iter().enumerate() {
        let shared = haecceity::presentation::label_with_width(line, &width);
        let line_width = width(line, TEXT);
        w = w.max(line_width);
        let at = [
            line_width / 2.0,
            h - TEXT / 2.0 - i as f64 * crate::callouts::line_spacing(),
        ];
        for run in shared.runs {
            let t = text(&run.text, at, run.size);
            laid.letters.push(t.clone());
            laid.runs.push(t);
        }
    }
    laid.size = [w, h];
    laid
}

/// A numbered triangular flag refers to the text in the existing notes block.
/// Its triangle participates in the ordinary callout collision and routing machinery.
fn flag_note(number: usize) -> Laid {
    let h = 2.5 * TEXT;
    let y = h / 2.0;
    let corners = [[0.0, y], [h, 0.0], [h, h]];
    let number = text(&number.to_string(), [0.68 * h, y], TEXT);
    Laid {
        size: [h, h],
        strokes: (0..3).map(|i| [corners[i], corners[(i + 1) % 3]]).collect(),
        letters: vec![number.clone()],
        runs: vec![number],
        arcs: Vec::new(),
    }
}

/// The least gap between a finish's leader or shelf and a dimension or extension line it runs
/// alongside, mm: one text height.
const FINISH_PARALLEL_GAP: f64 = TEXT;

/// Whether segments *a* and *b* are parallel, overlap along their direction by more than
/// 0.5 mm, and lie closer than *gap* across it.
fn runs_alongside(a: [P2; 2], b: [P2; 2], gap: f64) -> bool {
    let d = [a[1][0] - a[0][0], a[1][1] - a[0][1]];
    let e = [b[1][0] - b[0][0], b[1][1] - b[0][1]];
    let (la, lb) = (d[0].hypot(d[1]), e[0].hypot(e[1]));
    if la < 1e-9 || lb < 1e-9 {
        return false;
    }
    let u = [d[0] / la, d[1] / la];
    if (u[0] * e[1] - u[1] * e[0]).abs() / lb > 1e-6 {
        return false;
    }
    let across = ((b[0][0] - a[0][0]) * u[1] - (b[0][1] - a[0][1]) * u[0]).abs();
    let t0 = dot([b[0][0] - a[0][0], b[0][1] - a[0][1]], u);
    let t1 = dot([b[1][0] - a[0][0], b[1][1] - a[0][1]], u);
    let overlap = la.min(t0.max(t1)) - 0f64.max(t0.min(t1));
    across < gap && overlap > 0.5
}

/// The empty margin left of a surface texture symbol's ink, from its origin.
const FINISH_MARGIN: f64 = 0.3 * TEXT;
/// The radius of the dot a leader ends in on a face inside its outline (ISO 128-22).
const DOT: f64 = 0.5;

/// A face's ISO 1302 surface texture symbol laid out from its lower left corner, which is the
/// left end of the reference line it stands on (its leader's shelf), and its parameters, one per
/// line: haecceity's shared symbol (#258), measured in the sheet's font. The short and long legs
/// stand at 60° to a tip on that line (1.4 and 3 text heights tall, as the default finish beside
/// the title block draws them, [`crate::title_block::default_finish`]), with the material removal
/// condition's mark, the bar from the long leg's top, and the parameters under it (ISO 1302's
/// positions a and b). The reference line itself is the leader's ([`candidate`]), as long as the
/// glyph is wide.
fn finish_symbol(texture: &haecceity::pmi::SurfaceTexture) -> (Laid, Vec<String>) {
    let c = haecceity::presentation::notes::surface_texture_with_width(texture, &width);
    let cells = c.cells.clone();
    (laid(c), cells)
}

/// How an item hangs from its site: a feature control frame or label from its leader's shelf,
/// a datum feature symbol's triangle on its surface, or a surface texture symbol standing on its
/// leader's shelf.
#[derive(Clone, Copy, PartialEq)]
enum Shape {
    Frame,
    Datum,
    Finish,
}

impl Shape {
    fn of(glyph: &Glyph) -> Shape {
        match glyph {
            Glyph::Datum { .. } => Shape::Datum,
            Glyph::Finish { .. } => Shape::Finish,
            Glyph::ControlFrame { .. } | Glyph::Label { .. } | Glyph::FlagNote { .. } => {
                Shape::Frame
            }
        }
    }
}

/// The outward direction of strip *side*.
fn outward(side: &str) -> P2 {
    match side {
        "below" => [0.0, -1.0],
        "above" => [0.0, 1.0],
        "left" => [-1.0, 0.0],
        _ => [1.0, 0.0],
    }
}

fn dot(a: P2, b: P2) -> f64 {
    a[0] * b[0] + a[1] * b[1]
}

/// An item as placed at strip coordinate *pos* (along its strip's outward axis): its leader,
/// frame box and the laid glyph's offset.
struct Candidate {
    lines: Vec<[P2; 2]>,
    arrows: Vec<[P2; 2]>,
    fills: Vec<Vec<P2>>,
    label_box: [f64; 4],
    /// Where the glyph's origin lands.
    origin: P2,
    elbow: P2,
}

impl Candidate {
    fn leader_length(&self) -> f64 {
        self.lines
            .iter()
            .map(|[a, b]| (b[0] - a[0]).hypot(b[1] - a[1]))
            .sum()
    }
}

fn candidate(shape: Shape, tip: P2, side: &str, pos: f64, size: P2) -> Candidate {
    let d = outward(side);
    let horizontal = d[0] == 0.0;
    if shape == Shape::Finish {
        // The symbol stands on its shelf, which runs from the leader's end under it: above or
        // below the view the shelf is the box's bottom edge (*pos* the box's middle), the
        // leader rising or falling to its left end; beside the view the leader runs straight
        // out to *pos* and on under the symbol, which stands on it. The glyph's ink starts
        // FINISH_MARGIN in from its origin: its box starts there too, so the leader's bend
        // stays that far outside it on every side (lint's leader through text), the box
        // turned to keep the margin toward the bend left of the view.
        let (elbow, x0) = if horizontal {
            ([tip[0], pos - size[1] / 2.0], tip[0])
        } else if d[0] > 0.0 {
            ([pos, tip[1]], pos)
        } else {
            ([pos, tip[1]], pos - size[0] - FINISH_MARGIN)
        };
        let label_box = [
            x0 + FINISH_MARGIN,
            elbow[1],
            x0 + size[0],
            elbow[1] + size[1],
        ];
        let shelf = if elbow[0] > x0 {
            [label_box[0], elbow[1]]
        } else {
            [label_box[2], elbow[1]]
        };
        return Candidate {
            lines: vec![[tip, elbow], [elbow, shelf]],
            arrows: vec![placement::arrow(tip, elbow)],
            fills: Vec::new(),
            label_box,
            origin: [x0, elbow[1]],
            elbow,
        };
    }
    if shape == Shape::Datum {
        let centre = if horizontal {
            [tip[0], pos]
        } else {
            [pos, tip[1]]
        };
        let n = [-d[1], d[0]];
        let half = 0.5 * TRIANGLE;
        let apex = [
            tip[0] + d[0] * 0.9 * TRIANGLE,
            tip[1] + d[1] * 0.9 * TRIANGLE,
        ];
        let near = [centre[0] - d[0] * TEXT, centre[1] - d[1] * TEXT];
        return Candidate {
            lines: vec![[apex, near]],
            arrows: Vec::new(),
            fills: vec![vec![
                [tip[0] + n[0] * half, tip[1] + n[1] * half],
                apex,
                [tip[0] - n[0] * half, tip[1] - n[1] * half],
            ]],
            label_box: [
                centre[0] - TEXT,
                centre[1] - TEXT,
                centre[0] + TEXT,
                centre[1] + TEXT,
            ],
            origin: centre,
            elbow: near,
        };
    }
    let elbow = if horizontal {
        [tip[0], pos]
    } else {
        [pos, tip[1]]
    };
    // The shelf runs on in the leader's direction, to the right of a vertical one.
    let dir = if elbow[0] >= tip[0] { 1.0 } else { -1.0 };
    let end = [elbow[0] + dir * SHELF, elbow[1]];
    let x0 = if dir > 0.0 { end[0] } else { end[0] - size[0] };
    let y0 = elbow[1] - size[1] / 2.0;
    Candidate {
        lines: vec![[tip, elbow], [elbow, end]],
        arrows: vec![placement::arrow(tip, elbow)],
        fills: Vec::new(),
        label_box: [x0, y0, x0 + size[0], y0 + size[1]],
        origin: [x0, y0],
        elbow,
    }
}

/// An item's extent across strip *side* from its site, page mm: a frame's or label's leader,
/// shelf and box above or below its view, its box centred on the leader left or right; a datum's
/// frame centred on its line.
fn span(shape: Shape, side: &str, size: P2) -> [f64; 2] {
    if shape == Shape::Datum {
        [-TEXT, TEXT]
    } else if shape == Shape::Finish {
        // From its leader: rightwards above or below the view, upwards beside it.
        if outward(side)[0] == 0.0 {
            [0.0, size[0]]
        } else {
            [0.0, size[1]]
        }
    } else if outward(side)[0] == 0.0 {
        [0.0, SHELF + size[0]]
    } else {
        [-size[1] / 2.0, size[1] / 2.0]
    }
}

/// The strip coordinate of an item's first tier in *side* of the view whose outline is *o*:
/// beyond the outline and every piece of that view's dimensions (*ink*: lines, arrows and label
/// boxes as their corners) on that side that lies across from it, with room for the leader's
/// arrowhead or the datum's triangle. A dimension elsewhere along the strip does not push it
/// out (draftwright's lateral tier reuse, `_adjust_strip_candidate_labels`): its tier is free
/// there.
fn first_tier(shape: Shape, side: &str, tip: P2, o: [f64; 4], ink: &[[P2; 2]], size: P2) -> f64 {
    let d = outward(side);
    let horizontal = d[0] == 0.0;
    let edge = match side {
        "below" => -o[1],
        "above" => o[3],
        "left" => -o[0],
        _ => o[2],
    };
    let across = |p: P2| if horizontal { p[0] } else { p[1] };
    let [a, b] = span(shape, side, size).map(|v| across(tip) + v);
    let base = ink
        .iter()
        .filter(|[p, q]| {
            let (lo, hi) = (across(*p).min(across(*q)), across(*p).max(across(*q)));
            lo < b + CLEAR && hi > a - CLEAR
        })
        .flat_map(|s| s.iter().map(|q| dot(*q, d)))
        .filter(|v| *v > edge)
        .fold(edge, f64::max);
    let along = dot(tip, d);
    // A surface texture symbol's box starts at its leader's end beside the view, as a frame's
    // would without its shelf, so the same clearances hold.
    let (from_base, from_tip) = if shape == Shape::Datum {
        (CLEAR + TEXT, 0.9 * TRIANGLE + 0.8 * TEXT + TEXT)
    } else if horizontal {
        (CLEAR + size[1] / 2.0, ARROW + 1.0 + size[1] / 2.0)
    } else {
        (CLEAR, ARROW + 1.0)
    };
    let s = (base + from_base).max(along + from_tip);
    // Back from the outward axis to the page coordinate.
    if d[0] + d[1] < 0.0 { -s } else { s }
}

/// The step between tiers along the strip.
fn step(shape: Shape, side: &str, size: P2) -> f64 {
    let horizontal = outward(side)[0] == 0.0;
    if shape == Shape::Datum || horizontal {
        size[1] + CLEAR
    } else if shape == Shape::Finish {
        size[0] + CLEAR
    } else {
        size[0] + SHELF + CLEAR
    }
}

/// An attachment site, its outward side, and an optional planar face extension.
type Site<'a> = (P2, &'a str, Option<[P2; 2]>);
/// Clearance class, followed by line crossings; winning candidates have exact counts.
type Grade = (u8, usize);
type Choice<'a> = (Grade, &'a str, Candidate, P2, usize);

/// Compact datum tiers, starting with the normal triangle, shaft and frame clearances.
/// Exact obstacle grading rejects any intervening dimension, extension or text. The outer
/// edge must precede the first stroke across the frame's strip, with the usual clearance.
fn inside_tiers(
    side: &str,
    tip: P2,
    outline: [f64; 4],
    size: P2,
    ink: &[[P2; 2]],
    local: bool,
) -> Vec<f64> {
    let d = outward(side);
    let sign = d[0] + d[1];
    // Start at the attachment, not at the far end of the entire view. A shoulder
    // or face extension can have clear paper beside it inside the outline's span.
    // The obstacle grade requires proof of exterior paper within the view's box.
    let first = dot(tip, d) + 0.9 * TRIANGLE + 0.8 * TEXT + TEXT;
    let across = usize::from(d[0] != 0.0);
    let nearest = ink
        .iter()
        .filter(|[a, b]| {
            a[across].min(b[across]) < tip[across] + TEXT + CLEAR
                && a[across].max(b[across]) > tip[across] - TEXT - CLEAR
        })
        .flat_map(|l| l.iter().map(|p| dot(*p, d)))
        .filter(|v| *v > dot(tip, d) + 1e-6)
        .fold(f64::INFINITY, f64::min);
    // Keep the original view-boundary grid too. Starting the new grid at a
    // shoulder changes its phase; replacing the old grid would lose clear
    // placements between crowded dimension lanes on unrelated parts.
    let boundary = first_tier(Shape::Datum, side, tip, outline, &[], size) * sign;
    let starts = if local {
        vec![first, boundary]
    } else {
        vec![boundary]
    };
    let mut positions: Vec<_> = starts
        .into_iter()
        .flat_map(|start| {
            (0..TIERS)
                .map(move |k| start + k as f64 * step(Shape::Datum, side, size))
                .take_while(move |p| *p + TEXT + CLEAR <= nearest)
                .map(move |p| p * sign)
        })
        .collect();
    positions.sort_by(|a, b| (a * sign).total_cmp(&(b * sign)));
    positions.dedup_by(|a, b| (*a - *b).abs() < 1e-6);
    positions
}

/// Both silhouettes are usable only where the reader proved points on the trimmed face.
/// Keep the original site first, then try the nearest sites in page space.
fn cylinder_sites<'a>(
    analysis: &Analysis,
    item: &'a Lowered,
    frame: &Frame,
    requested: Option<&str>,
) -> Vec<Site<'a>> {
    cylinder_sites_of(analysis, item, frame, requested, false)
}

/// [`cylinder_sites`], from each face's head-on sites when *face_on*
/// ([`crate::pmi::FaceAttachment::face_on`]), else from its silhouette sites.
fn cylinder_sites_of<'a>(
    analysis: &Analysis,
    item: &'a Lowered,
    frame: &Frame,
    requested: Option<&str>,
    face_on: bool,
) -> Vec<Site<'a>> {
    let original = frame.page(item.site);
    let mut sites = Vec::new();
    for face in &item.faces {
        let Some(a) = analysis.face_attachments.get(*face) else {
            continue;
        };
        let Some(axis) = a.exact_axis else {
            continue;
        };
        if a.support.len() != 2 {
            continue;
        }
        let mut centre = item.site;
        for (i, value) in (0..3).filter(|i| *i != axis).zip(&a.support) {
            centre[i] = *value;
        }
        let centre = frame.page(centre);
        let proven = if face_on { &a.face_on } else { &a.sites };
        for p in std::iter::once(item.site)
            .chain(item.alternatives.iter().copied())
            .chain(proven.iter().copied())
        {
            let tip = frame.page(p);
            let side = if outward(item.side)[0] != 0.0 {
                if tip[0] > centre[0] { "right" } else { "left" }
            } else if tip[1] > centre[1] {
                "above"
            } else {
                "below"
            };
            if requested.is_none_or(|s| s == side) {
                sites.push((tip, side, None));
            }
        }
    }
    if sites.is_empty() {
        return vec![(original, item.side, None)];
    }
    sites.sort_by(|a, b| {
        let distance = |p: P2| (p[0] - original[0]).hypot(p[1] - original[1]);
        distance(a.0).total_cmp(&distance(b.0))
    });
    sites.dedup_by(|a, b| a.0 == b.0 && a.1 == b.1);
    sites
}

/// A planar face seen edge-on continues as a straight extension beyond either end of
/// the view. Leave a 1 mm outline gap and carry the line beyond the triangle's full base.
fn plane_extensions<'a>(analysis: &Analysis, item: &'a Lowered, frame: &Frame) -> Vec<Site<'a>> {
    let tip = frame.page(item.site);
    let k = usize::from(outward(item.side)[0] != 0.0);
    let outline = frame.outline();
    let mut bounds = [f64::INFINITY, f64::NEG_INFINITY];
    for f in &item.faces {
        let Some(b) = analysis.face_attachments.get(*f).and_then(|a| a.bounds) else {
            continue;
        };
        for x in [b[0], b[3]] {
            for y in [b[1], b[4]] {
                for z in [b[2], b[5]] {
                    let p = frame.page([x, y, z]);
                    bounds[0] = bounds[0].min(p[k]);
                    bounds[1] = bounds[1].max(p[k]);
                }
            }
        }
    }
    if !bounds.iter().all(|v| v.is_finite()) {
        return Vec::new();
    }
    let mut sites = Vec::new();
    for (end, sign) in [(0, -1.0), (1, 1.0)] {
        // Starting 1 mm past the face must also clear the part's projected envelope.
        if (bounds[end] - outline[k + 2 * end]).abs() > 1.0 {
            continue;
        }
        for n in 0..TIERS {
            let mut at = tip;
            at[k] = outline[k + 2 * end] + sign * (CLEAR + TRIANGLE / 2.0 + n as f64 * (H + CLEAR));
            let mut start = tip;
            start[k] = bounds[end] + sign;
            let mut stop = at;
            stop[k] += sign * (TRIANGLE / 2.0 + CLEAR);
            sites.push((at, item.side, Some([start, stop])));
        }
    }
    sites.sort_by(|a, b| (a.0[k] - tip[k]).abs().total_cmp(&(b.0[k] - tip[k]).abs()));
    sites
}

fn authored(place: Option<&crate::placement_intent::Place>) -> bool {
    place.is_some_and(|p| {
        p.pinned || p.side.is_some() || p.lane.is_some() || p.along.is_some() || p.offset.is_some()
    })
}

fn datum_sites<'a>(
    analysis: &Analysis,
    item: &'a Lowered,
    frame: &Frame,
    constrained: bool,
) -> Vec<Site<'a>> {
    if matches!(&item.glyph, Glyph::Datum { surface, .. } if surface == "cylinder") {
        return cylinder_sites(analysis, item, frame, constrained.then_some(item.side));
    }
    let mut sites: Vec<_> = std::iter::once(item.site)
        .chain(item.alternatives.iter().copied())
        .map(|p| (frame.page(p), item.side, None))
        .collect();
    if !constrained {
        sites.extend(plane_extensions(analysis, item, frame));
    }
    sites
}

/// Reserve distinct proven attachment sites together before greedily placing
/// letter boxes. Three coincident projections may need an earlier triangle to
/// move away from its middle station to leave room for the third. The bounded
/// search concerns filled triangles alone, not combinations of leader tiers.
fn separated_sites(
    analysis: &Analysis,
    spec: &Spec,
    items: &[Lowered],
    frames: &BTreeMap<&str, Frame>,
    page: &Page<'_>,
) -> BTreeMap<String, Vec<P2>> {
    let mut choices = Vec::new();
    for item in items
        .iter()
        .filter(|i| matches!(i.glyph, Glyph::Datum { .. }))
    {
        let entry = spec.annotations.get(&item.id);
        if entry.is_some_and(|p| p.omit) || datum_omission(analysis, spec, item).is_some() {
            continue;
        }
        let intent = entry.and_then(|p| p.place.as_ref());
        // Explicit pins retain their authored positions and are linted as drawn.
        if intent.is_some_and(|p| p.pinned) {
            return BTreeMap::new();
        }
        let Some(frame) = frames.get(item.view) else {
            continue;
        };
        if page.area(item.view).is_none() {
            continue;
        }
        let mut fills = Vec::new();
        for (tip, side, _) in datum_sites(analysis, item, frame, authored(intent)) {
            let d = outward(side);
            let pos = dot(tip, d) + 0.9 * TRIANGLE + 0.8 * TEXT + TEXT;
            let c = candidate(Shape::Datum, tip, side, pos * (d[0] + d[1]), [H, H]);
            // Invariant: The datum-symbol builder always emits its filled triangle.
            let fill = c.fills.into_iter().next().expect("datum triangle");
            let b = fill_box(&fill);
            let paper = page.drawable();
            if b[0] >= paper[0]
                && b[1] >= paper[1]
                && b[2] <= paper[2]
                && b[3] <= paper[3]
                && !fills.contains(&fill)
            {
                fills.push(fill);
            }
        }
        if fills.is_empty() {
            return BTreeMap::new();
        }
        choices.push((item.id.clone(), fills));
    }
    fn choose(
        choices: &[(String, Vec<Vec<P2>>)],
        selected: &mut Vec<Vec<P2>>,
        remaining: &mut usize,
    ) -> bool {
        if selected.len() == choices.len() {
            return true;
        }
        for fill in &choices[selected.len()].1 {
            if *remaining == 0 {
                return false;
            }
            *remaining -= 1;
            if selected
                .iter()
                .any(|previous| polygons_touch(fill, previous))
            {
                continue;
            }
            selected.push(fill.clone());
            if choose(choices, selected, remaining) {
                return true;
            }
            selected.pop();
        }
        false
    }
    let mut selected = Vec::new();
    let mut remaining = 4096;
    if choose(&choices, &mut selected, &mut remaining) {
        choices
            .into_iter()
            .zip(selected)
            .map(|((id, _), fill)| (id, fill))
            .collect()
    } else {
        BTreeMap::new()
    }
}

/// Conservative bounds of a filled datum triangle, including its surface base.
/// Reserve these alongside frames so triangles cannot overlap or touch, even when
/// their leaders and letter boxes are far apart.
fn fill_box(fill: &[P2]) -> [f64; 4] {
    fill.iter().fold(
        [
            f64::INFINITY,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NEG_INFINITY,
        ],
        |b, p| {
            [
                b[0].min(p[0]),
                b[1].min(p[1]),
                b[2].max(p[0]),
                b[3].max(p[1]),
            ]
        },
    )
}

/// What a candidate must stay clear of.
struct Obstacles<'a> {
    page: &'a Page<'a>,
    own: String,
    /// The area reserved for its view and bands.
    area: Option<[f64; 4]>,
    /// Proven exterior paper beside a shoulder inside the whole-view box.
    exterior: Option<(&'a PlannedView, &'a super::interior::Proof)>,
    protect_triangles: bool,
    /// A finish's leader and shelf keep this clear of any line they run alongside.
    parallel_gap: Option<f64>,
    triangles: Vec<Vec<P2>>,
    labels: Vec<[f64; 4]>,
    lines: Vec<[P2; 2]>,
}

impl Obstacles<'_> {
    /// How well *c* fits, best first: 0 clear of everything and inside its view's reserved area;
    /// 1 clear of everything, past the area; 2 on the sheet and off every label and frame, a line
    /// of it through a label or another view; 3 off every label and frame only; 4 neither;
    /// 5 a filled datum triangle overlaps or touches an earlier triangle.
    /// Add the crossing count as the second component: (0, 0) alone is clear, and fewer
    /// crossings win among candidates with otherwise equal clearances.
    fn grade(&self, c: &Candidate, best: Option<Grade>) -> Grade {
        let clearance = self.clearance_grade(c);
        // Clearance precedes crossings. A worse clearance, or a crossing count that
        // already ties the best at the same clearance, cannot replace that candidate.
        // Only losing candidates stop early; every saved best has its exact count.
        if best.is_some_and(|b| clearance > b.0) {
            return (clearance, 0);
        }
        let limit = best.filter(|b| b.0 == clearance).map(|b| b.1);
        if limit == Some(0) {
            return (clearance, 0);
        }
        let triangle: Vec<_> = c
            .fills
            .iter()
            .flat_map(|f| (0..f.len()).map(|i| [f[i], f[(i + 1) % f.len()]]))
            .collect();
        let mut crossings = 0;
        for s in c.lines.iter().chain(&triangle) {
            // Every obstacle uses the same candidate segment. Reuse the router's exact
            // crossing/collinearity primitive with its direction computed once.
            let line = super::leaders::Line::new(*s);
            for t in &self.lines {
                if line.meets(*t) {
                    crossings += 1;
                    if limit.is_some_and(|n| crossings >= n) {
                        return (clearance, crossings);
                    }
                }
            }
        }
        (clearance, crossings)
    }

    fn clearance_grade(&self, c: &Candidate) -> u8 {
        // Filled symbols take precedence over soft label/leader contacts. In a
        // dense view every label tier may be obstructed, but another proven site
        // can still separate the triangles rather than keeping coincident ink.
        if c.fills
            .iter()
            .any(|f| self.triangles.iter().any(|t| polygons_touch(f, t)))
        {
            return 5;
        }
        let b = c.label_box;
        let d = self.page.drawable();
        let on_sheet = b[0] >= d[0] && b[1] >= d[1] && b[2] <= d[2] && b[3] <= d[3];
        let grown = [b[0] - 0.5, b[1] - 0.5, b[2] + 0.5, b[3] + 0.5];
        let off_labels = self.labels.iter().all(|l| !boxes_overlap(grown, *l))
            && (!self.protect_triangles
                || c.fills.iter().all(|f| {
                    let b = fill_box(f);
                    let grown = [b[0] - 0.5, b[1] - 0.5, b[2] + 0.5, b[3] + 0.5];
                    self.labels.iter().all(|l| !boxes_overlap(grown, *l))
                }));
        if !off_labels {
            return 4;
        }
        let furniture = on_sheet
            && !boxes_overlap(b, self.page.title_block())
            && self.page.views().iter().all(|(id, v)| {
                if boxes_overlap(b, *v)
                    && *id == self.own
                    && let Some((view, proof)) = self.exterior
                {
                    proof.exterior_clear(view, b, &c.lines, &c.arrows)
                } else {
                    !boxes_overlap(b, *v)
                }
            });
        if !furniture {
            return 3;
        }
        let lines_clear =
            self.page.views().iter().all(|(id, v)| {
                *id == self.own.as_str() || !c.lines.iter().any(|l| crosses(*l, *v))
            }) && !c.lines.iter().any(|l| crosses(*l, self.page.title_block()))
                && !self.lines.iter().any(|l| crosses(*l, b))
                && !c
                    .lines
                    .iter()
                    .any(|l| self.labels.iter().any(|x| crosses(*l, *x)));
        // A finish's leader run close alongside a dimension or extension line reads as a
        // doubled line: as bad as a line through a label.
        let alongside = self.parallel_gap.is_some_and(|gap| {
            c.lines
                .iter()
                .any(|l| self.lines.iter().any(|t| runs_alongside(*l, *t, gap)))
        });
        if !lines_clear || alongside {
            return 2;
        }
        let inside = |q: &P2, a: [f64; 4]| {
            q[0] >= a[0] - 1e-6 && q[0] <= a[2] + 1e-6 && q[1] >= a[1] - 1e-6 && q[1] <= a[3] + 1e-6
        };
        let within = self.area.is_none_or(|a| {
            c.lines.iter().flatten().all(|q| inside(q, a))
                && [[b[0], b[1]], [b[2], b[3]]].iter().all(|q| inside(q, a))
        });
        if within { 0 } else { 1 }
    }
}

/// The strips a feature control frame tries after its own (`_gdt_retry_sides`).
fn retries(side: &str) -> [&'static str; 3] {
    match side {
        "above" => ["below", "right", "left"],
        "below" => ["above", "right", "left"],
        "left" => ["right", "above", "below"],
        _ => ["left", "above", "below"],
    }
}

/// The principal views' towards-the-viewer and up directions, third angle (`model::VIEWS`).
const PRINCIPALS: [(&str, [f64; 3], [f64; 3]); 3] = [
    ("front", [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]),
    ("plan", [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]),
    ("side", [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]),
];

/// The part's lowered PMI as the layout reserves room for it ([`crate::layout::Gdt`],
/// [`crate::layout::Strips::reserve_gdt`]), *shown* saying which principal views the sheet
/// carries: each item's strip as lowering chose it, its site across the strip, and its glyph's
/// extents as [`plan`] places it (the leader and shelf of a frame or label, a datum's frame
/// centred on its line), with the room [`first_tier`] leaves for the arrowhead or triangle.
pub fn demand(analysis: &Analysis, shown: &dyn Fn(&str) -> bool) -> Vec<crate::layout::Gdt> {
    demand_with_spec(analysis, &Spec::default(), shown)
}

pub(crate) fn demand_with_spec(
    analysis: &Analysis,
    spec: &Spec,
    shown: &dyn Fn(&str) -> bool,
) -> Vec<crate::layout::Gdt> {
    let (mut items, _) = crate::pmi_lowering::lower(analysis, shown);
    if items
        .iter()
        .any(|i| matches!(i.glyph, Glyph::FlagNote { .. }))
    {
        // The exact same reconciled list reserves and numbers the notes block. A feature
        // whose leader cannot be lowered still has its text there, without a dangling flag.
        let (requirements, _) = crate::pmi_lowering::sheet_requirements(analysis);
        let (notes, inactive, _) = crate::notes::reconcile(&requirements, analysis, spec);
        for item in &mut items {
            if let Glyph::FlagNote { number, .. } = &mut item.glyph {
                let id = inactive
                    .iter()
                    .find(|n| n.id == item.id)
                    .and_then(|n| {
                        if let crate::items::State::Covered { by } = &n.state {
                            Some(by.as_str())
                        } else {
                            None
                        }
                    })
                    .unwrap_or(&item.id);
                *number = notes.iter().position(|n| n.id == id).map_or(0, |i| i + 1);
            }
        }
        items.retain(|i| !matches!(i.glyph, Glyph::FlagNote { number: 0, .. }));
    }
    // Invariant: plan_with validates these same annotation texts before demand and placement.
    crate::label_text::annotations(&mut items, spec).expect("validated annotation wording");
    if spec.sheet.uncited_datums != Some(true) || spec.annotations.values().any(|a| a.omit) {
        items.retain(|item| datum_omission(analysis, spec, item).is_none());
    }
    let frames: BTreeMap<&str, Frame> = PRINCIPALS
        .iter()
        .map(|(name, toward, up)| (*name, Frame::looking(analysis, *toward, *up, [0.0; 2], 1.0)))
        .collect();
    items
        .iter()
        .map(|item| {
            let shape = Shape::of(&item.glyph);
            let datum = shape == Shape::Datum;
            let size = match &item.glyph {
                Glyph::Datum { .. } => [H, H],
                Glyph::Label { text } => label(text).size,
                Glyph::FlagNote { number, .. } => flag_note(*number).size,
                Glyph::Finish { texture, .. } => finish_symbol(texture).0.size,
                g => control_frame(g).0.size,
            };
            let horizontal = outward(item.side)[0] == 0.0;
            let f = &frames[item.view];
            let p = f.page(item.site);
            let o = f.outline();
            let depth = if datum {
                H
            } else if horizontal {
                size[1]
            } else if shape == Shape::Finish {
                size[0]
            } else {
                SHELF + size[0]
            };
            crate::layout::Gdt {
                view: item.view,
                side: item.side,
                at: if horizontal { p[0] } else { p[1] },
                span: span(shape, item.side, size),
                ends: if horizontal {
                    [o[0], o[2]]
                } else {
                    [o[1], o[3]]
                },
                depth,
                lead: if datum {
                    0.9 * TRIANGLE + 0.8 * TEXT
                } else {
                    ARROW + 1.0
                },
            }
        })
        .collect()
}

/// The part's PMI lowered and placed around *views* on *sheet*, after the dimensions *dims*:
/// the items, and the problems met (lowering's, and what the placed items collide with). An
/// item whose view *spec* omits is fixed by restoring it.
pub fn plan(
    analysis: &Analysis,
    spec: &Spec,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    dims: &[PlannedDimension],
) -> (Vec<PlannedGdt>, Vec<Problem>) {
    plan_anchored(
        analysis,
        spec,
        sheet,
        views,
        dims,
        None,
        &super::footprints::Cache::default(),
    )
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn plan_anchored(
    analysis: &Analysis,
    spec: &Spec,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    dims: &[PlannedDimension],
    canonical: Option<&crate::model::Plan>,
    cache: &super::footprints::Cache,
) -> (Vec<PlannedGdt>, Vec<Problem>) {
    // A complete existing layout is also a candidate. Greedy improvements must not buy
    // fewer crossings by displacing later frames into text, other views or unreserved space.
    // Compare unique unordered pairs, since leader crossings can be reported both ways.
    let seed = plan_search(
        analysis,
        spec,
        sheet,
        views,
        dims,
        canonical,
        cache,
        Search::Seed,
        None,
    )
    // Invariant: The seed call supplies no incumbent bound, so search always returns its candidate.
    .expect("the seed search is never pruned");
    let contacts = |problems: &[Problem]| {
        problems
            .iter()
            .filter(|p| p.code.starts_with("annotation_") || p.code == "datum_symbol_overlap")
            .map(|p| {
                let mut pair = p.subjects.clone();
                pair.sort_unstable();
                (p.code.clone(), pair)
            })
            .collect::<std::collections::BTreeSet<_>>()
    };
    let before = contacts(&seed.1);
    let overlap_count = |problems: &[Problem]| {
        problems
            .iter()
            .filter(|p| p.code == "datum_symbol_overlap")
            .count()
    };
    let seed_overlaps = overlap_count(&seed.1);
    // Without datums, only the added dimension arrow shafts can change the datum-only
    // pass's clearance grading. If every seed label clears them, the same first-best
    // choice survives at each step; the pass would reproduce the seed.
    let datum_search_can_change = seed.0.iter().any(|g| g.kind == "datum_feature")
        || dims.iter().filter_map(|d| d.placed.as_ref()).any(|d| {
            d.arrows.iter().any(|a| {
                seed.0
                    .iter()
                    .filter_map(|g| g.placed.as_ref())
                    .any(|g| crosses(*a, g.label_box))
            })
        });
    if seed.0.iter().all(|g| g.placed.is_none()) {
        return seed;
    }
    // Preserve the actual old arrangement as the contact reference. Triangle
    // reservations belong to the corrective searches, never to that seed.
    // An already illegal seed needs symbol separation before soft contacts:
    // try ordinary placement first, reserving sites jointly only if it cannot
    // separate the triangles. Keep every resulting warning visible.
    // Keep the previous complete compact arrangement as a candidate too.
    // Its triangles are checked independently: an already legal, clearer
    // drawing must not be lost when the new attachment choices are expanded.
    let searches = [
        Search::Baseline,
        Search::All,
        Search::Datums,
        Search::Separate,
    ];
    let score = |candidate: &(Vec<PlannedGdt>, Vec<Problem>)| {
        let datums: BTreeSet<_> = candidate
            .0
            .iter()
            .filter(|g| g.kind == "datum_feature")
            .map(|g| &g.id)
            .collect();
        (
            overlap_count(&candidate.1),
            candidate
                .1
                .iter()
                .filter(|p| {
                    p.code == "annotation_leader_crossing"
                        && p.subjects.iter().any(|id| datums.contains(id))
                })
                .count(),
            contacts(&candidate.1).len(),
        )
    };
    let datum_length = |candidate: &(Vec<PlannedGdt>, Vec<Problem>)| -> f64 {
        candidate
            .0
            .iter()
            .filter(|g| g.kind == "datum_feature")
            .filter_map(|g| g.placed.as_ref())
            .flat_map(|p| &p.lines)
            .map(|[a, b]| (b[0] - a[0]).hypot(b[1] - a[1]))
            .sum()
    };
    let mut best_score = score(&seed);
    let mut best_length = datum_length(&seed);
    let mut best = seed;
    let mut original = true;
    for search in searches {
        if search == Search::Separate && (seed_overlaps == 0 || best_score.0 == 0) {
            continue;
        }
        if search == Search::Datums && !datum_search_can_change {
            continue;
        }
        if let Some(compact) = plan_search(
            analysis,
            spec,
            sheet,
            views,
            dims,
            canonical,
            cache,
            search,
            (seed_overlaps == 0).then_some(&before),
        ) && (contacts(&compact.1).is_subset(&before)
            || overlap_count(&compact.1) < seed_overlaps)
        {
            let grade = score(&compact);
            let length = datum_length(&compact);
            if grade < best_score
                || (grade == best_score && (original || length < best_length - 1e-6))
            {
                best = compact;
                best_score = grade;
                best_length = length;
                original = false;
            }
            // Try the datum-only arrangement too if a legal full arrangement
            // still crosses dimensions. Keep the shortest equally clear arrangement, and
            // stop once the datum symbols and their leaders are both clear. The
            // boundary-only baseline must still compete with the local search:
            // equal contacts do not justify retaining a much longer leader.
            if (search != Search::Baseline || best_length <= 1e-6)
                && best_score.0 == 0
                && best_score.1 == 0
            {
                return best;
            }
        }
    }
    // Keep the existing search loop unchanged for cited-only drawings. Only new,
    // uncited output with remaining contacts needs exact narrow-gap trials.
    if (best_score.0 != 0 || best_score.1 != 0)
        && matches!(&analysis.pmi, Some(crate::pmi::Reading::Read(report)) if report.records.iter().any(|r| r.category == crate::pmi::Category::Datum && !report.records.iter().any(|t| t.category == crate::pmi::Category::GeometricTolerance && t.datum_refs.iter().any(|d| d.trim() == r.label.trim()))))
        && let Some(candidate) = plan_search(
            analysis,
            spec,
            sheet,
            views,
            dims,
            canonical,
            cache,
            Search::Uncited,
            (seed_overlaps == 0).then_some(&before),
        )
        && (contacts(&candidate.1).is_subset(&before)
            || overlap_count(&candidate.1) < seed_overlaps)
    {
        let grade = score(&candidate);
        if grade < best_score
            || (grade == best_score && datum_length(&candidate) < best_length - 1e-6)
        {
            return candidate;
        }
    }
    best
}

#[derive(Clone, Copy, PartialEq)]
enum Search {
    Seed,
    Datums,
    All,
    Separate,
    Baseline,
    Uncited,
}

#[allow(clippy::too_many_arguments)]
fn plan_search(
    analysis: &Analysis,
    spec: &Spec,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    dims: &[PlannedDimension],
    canonical: Option<&crate::model::Plan>,
    cache: &super::footprints::Cache,
    search: Search,
    allowed: Option<&BTreeSet<(String, Vec<String>)>>,
) -> Option<(Vec<PlannedGdt>, Vec<Problem>)> {
    let compact = search != Search::Seed;
    let protect = compact && search != Search::Baseline;
    let recess_frames = dims.iter().any(|d| {
        d.measurements
            .iter()
            .any(|m| m.feature.starts_with("f:section_recess:"))
    });
    let shown = |name: &str| views.iter().any(|v| v.id == format!("v:{name}"));
    let (mut items, mut problems) = crate::pmi_lowering::lower(analysis, &shown);
    // The exact same reconciled list reserves and numbers the notes block. A feature
    // whose leader cannot be lowered still has its text there, without a dangling flag.
    let (requirements, _) = crate::pmi_lowering::sheet_requirements(analysis);
    let (notes, inactive, _) = crate::notes::reconcile(&requirements, analysis, spec);
    for item in &mut items {
        if let Glyph::FlagNote { number, .. } = &mut item.glyph {
            let id = inactive
                .iter()
                .find(|n| n.id == item.id)
                .and_then(|n| {
                    if let crate::items::State::Covered { by } = &n.state {
                        Some(by.as_str())
                    } else {
                        None
                    }
                })
                .unwrap_or(&item.id);
            *number = notes.iter().position(|n| n.id == id).map_or(0, |i| i + 1);
        }
    }
    items.retain(|i| !matches!(i.glyph, Glyph::FlagNote { number: 0, .. }));
    // Invariant: plan_with validates these same annotation texts before demand and placement.
    crate::label_text::annotations(&mut items, spec).expect("validated annotation wording");
    if items.is_empty() {
        return Some((Vec::new(), problems));
    }
    let frames: BTreeMap<&str, Frame> = views
        .iter()
        .filter_map(|v| {
            let name = v.id.strip_prefix("v:")?;
            ["front", "plan", "side"]
                .contains(&name)
                .then(|| (name, Frame::new(analysis, v)))
        })
        .collect();
    let page = Page::new(analysis, sheet, views);
    let on_sheet = |view: &str| {
        views
            .iter()
            .find(|v| v.id == view)
            .map_or(sheet.number == 1, |v| v.sheet == sheet.number)
    };
    for item in &mut items {
        if let Some(p) = spec
            .annotations
            .get(&item.id)
            .and_then(|a| a.place.as_ref())
            .filter(|p| !super::hole_marks::pinned_on_mark(analysis, item, p))
        {
            if let Some(v) = p.view.as_deref() {
                item.view = match v {
                    "plan" => "plan",
                    "side" => "side",
                    _ => "front",
                };
            }
            if let Some(side) = p.side {
                item.side = side.name();
            } else if matches!(item.glyph, Glyph::Datum { .. } | Glyph::Finish { .. })
                && let Some(crate::pmi::Reading::Read(report)) = &analysis.pmi
                && let Some(record) = report.records.iter().find(|r| r.source_id == item.source)
                && let Some((_, sides)) = placement_choices(item, record, views)
                    .into_iter()
                    .find(|(view, _)| *view == item.view)
            {
                item.side = sides[0];
            }
        }
    }
    // Each site's position across its strip.
    let across = |i: &Lowered| -> f64 {
        frames.get(i.view).map_or(0.0, |f| {
            let p = f.page(i.site);
            if outward(i.side)[0] == 0.0 {
                p[0]
            } else {
                p[1]
            }
        })
    };
    // A frame above or below its view runs right of its leader, so the rightmost leader takes
    // the nearest tier and no shaft runs through a frame placed before it. Leaders that stand
    // together keep the reading's order (its sources, as text), as before items were named by
    // their identity.
    // A plane has one outward side; a cylinder can try both silhouettes. Place
    // the constrained planar datum first in the compact search so a movable
    // cylinder does not consume its only clear shoulder band.
    items.sort_by(|a, b| {
        let order = if outward(a.side)[0] == 0.0 {
            across(b).total_cmp(&across(a))
        } else {
            across(a).total_cmp(&across(b))
        };
        (
            !spec
                .annotations
                .get(&a.id)
                .and_then(|a| a.place.as_ref())
                .is_some_and(|p| p.pinned),
            a.view,
            protect && !matches!(&a.glyph, Glyph::Datum { surface, .. } if surface == "plane"),
            a.side,
            finish_freedom(a),
        )
            .cmp(&(
                !spec
                    .annotations
                    .get(&b.id)
                    .and_then(|a| a.place.as_ref())
                    .is_some_and(|p| p.pinned),
                b.view,
                protect && !matches!(&b.glyph, Glyph::Datum { surface, .. } if surface == "plane"),
                b.side,
                finish_freedom(b),
            ))
            .then(order)
            .then(a.source.cmp(&b.source))
    });
    let mut labels: Vec<[f64; 4]> = dims
        .iter()
        .filter_map(|d| d.placed.as_ref().map(|p| p.label_box))
        .collect();
    let mut lines: Vec<[P2; 2]> = dims
        .iter()
        .filter_map(|d| d.placed.as_ref())
        .flat_map(|p| {
            p.lines
                .iter()
                .chain(p.arrows.iter().filter(|_| compact))
                .copied()
        })
        .collect();
    let exterior = if protect && items.iter().any(|i| matches!(i.glyph, Glyph::Datum { .. })) {
        super::interior::exterior_proofs(analysis, views)
    } else {
        BTreeMap::new()
    };
    let mut reservations = if search == Search::Separate {
        separated_sites(analysis, spec, &items, &frames, &page)
    } else {
        BTreeMap::new()
    };
    let mut out = Vec::new();
    let mut triangles = Vec::new();
    for item in items {
        reservations.remove(&item.id);
        let shape = Shape::of(&item.glyph);
        let datum = shape == Shape::Datum;
        let (laid, cells) = match &item.glyph {
            Glyph::Datum { letter, .. } => (datum_frame(letter), vec![letter.clone()]),
            Glyph::Label { text } => (label(text), vec![text.clone()]),
            Glyph::FlagNote { number, .. } => (flag_note(*number), vec![number.to_string()]),
            Glyph::Finish { texture, .. } => finish_symbol(texture),
            g => control_frame(g),
        };
        let modifiers: Vec<String> = match &item.glyph {
            Glyph::ControlFrame {
                all_around,
                all_over,
                ..
            } => [("all_around", *all_around), ("all_over", *all_over)]
                .into_iter()
                .filter(|(_, on)| *on)
                .map(|(m, _)| m.to_string())
                .collect(),
            Glyph::Datum { .. }
            | Glyph::Label { .. }
            | Glyph::FlagNote { .. }
            | Glyph::Finish { .. } => Vec::new(),
        };
        let mut planned = PlannedGdt {
            from_spec: if spec
                .annotations
                .get(&item.id)
                .is_some_and(|a| a.text.is_some())
            {
                vec!["text".into()]
            } else {
                Vec::new()
            },
            id: item.id.clone(),
            kind: match item.glyph {
                Glyph::Datum { .. } => "datum_feature",
                Glyph::Label { .. } | Glyph::FlagNote { .. } => "label",
                Glyph::ControlFrame { .. } => "control_frame",
                Glyph::Finish { .. } => "surface_finish",
            }
            .into(),
            view: format!("v:{}", item.view),
            side: item.side.into(),
            source: item.source.clone(),
            faces: item.faces.clone(),
            label: crate::items::frame_label(&cells),
            cells,
            modifiers,
            // A datum or control the Spec authors is never a default ([`crate::spec_gdt`]).
            default: !spec.annotations.contains_key(&item.id)
                && !item.source.starts_with(crate::spec_gdt::SOURCE_PREFIX),
            basis: item.basis.clone(),
            // Shown once placed; else its view is not drawn.
            state: State::not_drawable("pmi_not_drawn"),
            placed: None,
        };
        if let Some(reason) = datum_omission(analysis, spec, &item) {
            planned.basis += &format!("; {reason}");
            if reason == crate::spec_gdt::UNCITED_DATUM {
                planned.state = State::not_drawable("datum_not_drawn");
                problems.push(Problem {
                    id: format!("p:datum_not_drawn:{}", item.id),
                    code: "datum_not_drawn".into(),
                    severity: "info".into(),
                    message: format!(
                        "Datum {} is declared but not drawn: no control frame cites it and no dimension measures from it. Set sheet/uncited_datums to true to draw every declared datum.",
                        planned.label
                    ),
                    subjects: vec![item.id.clone()],
                    ..Default::default()
                });
            } else {
                planned.state = State::OmittedByUser;
            }
            out.push(planned);
            continue;
        }
        if spec.annotations.get(&item.id).is_some_and(|a| a.omit) {
            planned.state = State::OmittedByUser;
            planned.basis += "; omitted by the spec";
            out.push(planned);
            continue;
        }
        let intent = spec
            .annotations
            .get(&item.id)
            .and_then(|a| a.place.as_ref());
        let authored = authored(intent);
        if intent.is_some() {
            planned.basis += "; placed relative to its anchor by the spec";
        }
        let Some(frame) = frames.get(item.view) else {
            problems.push(Problem {
                id: format!("p:pmi_not_drawn:{}", item.id),
                code: "pmi_not_drawn".into(),
                severity: "warning".into(),
                message: format!(
                    "{} is drawn in the {} view, which is not on the sheet; it stays in the plan undrawn. Restore the view (remove views/{}/omit).",
                    crate::wording::capital(&crate::wording::item(
                        &item.id,
                        "",
                        &planned.cells.join(" ")
                    )),
                    item.view,
                    item.view
                ),
                subjects: vec![item.id.clone()],
                fix: if spec.views.get(item.view).is_some_and(|v| v.omit) {
                    vec![Op::remove(format!("views/{}/omit", item.view))]
                } else {
                    Vec::new()
                },
                ..Default::default()
            });
            out.push(planned);
            continue;
        };
        if intent.is_some_and(crate::placement_intent::automatic_pin)
            && let Some(base) = canonical
            && let Some(g) = base.gdt.iter().find(|g| {
                g.id == item.id && (g.view == planned.view || super::hole_marks::attached(g))
            })
            && let Some(delta) = crate::placement_intent::anchor_delta(base, views, &g.view)
            && let Some(mut p) = g.placed.clone()
        {
            use crate::placement_intent::{move_box, move_point};
            move_point(&mut p.tip, delta);
            move_box(&mut p.label_box, delta);
            for line in p
                .lines
                .iter_mut()
                .chain(&mut p.arrows)
                .chain(&mut p.strokes)
            {
                for q in line {
                    move_point(q, delta);
                }
            }
            for arc in &mut p.arcs {
                if delta != [0.0, 0.0] {
                    arc[0] += delta[0];
                    arc[1] += delta[1];
                }
            }
            for fill in &mut p.fills {
                for q in fill {
                    move_point(q, delta);
                }
            }
            for t in p.letters.iter_mut().chain(&mut p.runs) {
                move_point(&mut t.at, delta);
            }
            labels.push(p.label_box);
            if protect {
                labels.extend(p.fills.iter().map(|f| fill_box(f)));
                triangles.extend(p.fills.iter().cloned());
            }
            lines.extend(p.lines.iter().copied());
            planned.side = g.side.clone();
            planned.view.clone_from(&g.view);
            if super::hole_marks::attached(g) {
                planned.basis.clone_from(&g.basis);
            }
            planned.state = State::Shown;
            planned.placed = Some(p);
            out.push(planned);
            continue;
        }
        let own = format!("v:{}", item.view);
        let ink: Vec<[P2; 2]> = dims
            .iter()
            .filter(|d| d.view == own)
            .filter_map(|d| d.placed.as_ref())
            .flat_map(|p| {
                let b = p.label_box;
                p.lines
                    .iter()
                    .chain(&p.arrows)
                    .copied()
                    .chain([[[b[0], b[1]], [b[2], b[3]]]])
                    .collect::<Vec<_>>()
            })
            .collect();
        let obstacles = Obstacles {
            page: &page,
            own: own.clone(),
            area: page.area(item.view),
            exterior: datum
                .then(|| {
                    views
                        .iter()
                        .find(|v| v.id == own)
                        .and_then(|v| exterior.get(&v.id).map(|proof| (v, proof.as_ref())))
                })
                .flatten(),
            protect_triangles: protect,
            parallel_gap: (shape == Shape::Finish).then_some(FINISH_PARALLEL_GAP),
            triangles: triangles
                .iter()
                .chain(reservations.values())
                .cloned()
                .collect(),
            labels: labels.clone(),
            lines: lines.clone(),
        };
        let sides: Vec<&str> = if shape != Shape::Frame || intent.is_some_and(|p| p.side.is_some())
        {
            vec![item.side]
        } else {
            std::iter::once(item.side)
                .chain(retries(item.side))
                .collect()
        };
        let mut sites: Vec<Site<'_>> = std::iter::once(item.site)
            .chain(item.alternatives.iter().copied())
            .map(|s| (frame.page(s), item.side, None))
            .collect();
        if compact && datum {
            sites = datum_sites(analysis, &item, frame, authored);
        }
        // A cylinder's finish may stand at any proven site on either silhouette (or, head-on,
        // either half of the face), each in the strip it faces: its own strip's first, the
        // other's only when they find no clear place; only the authored strip's when the Spec
        // names one. A dot reads on blank face: sites clear of the view's projected edges and
        // silhouettes (hidden ones too) by a millimetre past the dot come first in each strip.
        let face_on = matches!(item.glyph, Glyph::Finish { face_on: true, .. });
        let cylinder_finish =
            matches!(&item.glyph, Glyph::Finish { surface, .. } if surface == "cylinder");
        if cylinder_finish {
            let requested = intent.and_then(|p| p.side).map(|s| s.name());
            sites = cylinder_sites_of(analysis, &item, frame, requested, face_on);
            let proofs = if face_on {
                super::interior::exterior_proofs(analysis, views)
            } else {
                BTreeMap::new()
            };
            let view = views.iter().find(|v| v.id == own);
            let blank = |p: P2| match (view, proofs.get(&own)) {
                (Some(v), Some(proof)) => proof.point_clear(v, p, DOT + 1.0),
                _ => true,
            };
            sites.sort_by_key(|s| (s.1 != item.side, !blank(s.0)));
        }
        let tiers = |side: &str, tip: P2| {
            let pinned = intent.is_some_and(|p| p.pinned);
            let first = first_tier(
                shape,
                side,
                tip,
                frame.outline(),
                if pinned { &[] } else { &ink },
                laid.size,
            );
            let d = outward(side);
            let sign = if d[0] + d[1] < 0.0 { -1.0 } else { 1.0 };
            let step = step(shape, side, laid.size);
            let lane = intent.and_then(|p| p.lane).unwrap_or(0);
            let count = if intent.is_some_and(|p| p.pinned) {
                1
            } else {
                TIERS
            };
            (0..count)
                .map(move |k| first + sign * (k + lane) as f64 * step)
                .collect::<Vec<_>>()
        };
        // The best grade, then the shortest automatic datum leader; remaining
        // ties keep the original site, strip and tier order.
        let mut best: Option<Choice<'_>> = None;
        let tier_ink: &[[P2; 2]] = if search == Search::Uncited { &[] } else { &ink };
        // Exhaust the compact, face-to-first-lane choices before trying any outer tier.
        // A cylinder's finish exhausts its own strip (compact tiers, then outer ones) before the
        // other silhouette's, which it tries only when its own has no place clear of every label
        // and line: a place past the reserved area, uncrossed, still keeps its strip.
        let passes: &[(bool, Option<bool>)] = if cylinder_finish {
            &[
                (true, Some(true)),
                (false, Some(true)),
                (true, Some(false)),
                (false, Some(false)),
            ]
        } else {
            &[(true, None), (false, None)]
        };
        'search: for &(inside, own_strip) in passes {
            if own_strip == Some(false)
                && best
                    .as_ref()
                    .is_some_and(|b: &Choice<'_>| b.0.0 <= 1 && b.0.1 == 0)
            {
                break;
            }
            if inside
                && (!compact
                    || !(datum || shape == Shape::Finish || recess_frames && shape == Shape::Frame)
                    || authored)
            {
                continue;
            }
            for (n, (tip, site_side, extension)) in sites.iter().enumerate() {
                if own_strip.is_some_and(|own| (*site_side == item.side) != own) {
                    continue;
                }
                let choices = if datum || cylinder_finish {
                    vec![*site_side]
                } else {
                    sides.clone()
                };
                for side in choices {
                    let positions = if inside && datum {
                        // The uncited fallback lets exact obstacle grading test a narrow
                        // shoulder gap that the conservative first-stroke band rejects.
                        // All original sites and tiers are reused; crossings still lose.
                        inside_tiers(side, *tip, frame.outline(), laid.size, tier_ink, protect)
                    } else if inside {
                        // Frame ink can fit between the face and the first
                        // dimension lane too. Exact obstacle grading, including
                        // every label and witness, still decides whether it fits.
                        let d = outward(side);
                        let sign = d[0] + d[1];
                        let first = first_tier(shape, side, *tip, frame.outline(), &[], laid.size);
                        let outer =
                            first_tier(shape, side, *tip, frame.outline(), &ink, laid.size) * sign;
                        (0..TIERS)
                            .map(|k| first + sign * k as f64 * step(shape, side, laid.size))
                            .take_while(|p| p * sign < outer - 1e-6)
                            .collect()
                    } else {
                        tiers(side, *tip)
                    };
                    for pos in positions {
                        let mut anchor = *tip;
                        if let Some(along) = intent.and_then(|p| p.along) {
                            let o = frame.outline();
                            let k = usize::from(outward(side)[0] != 0.0);
                            anchor[k] = o[k] + along.fraction() * (o[k + 2] - o[k]);
                        }
                        let mut c = candidate(shape, anchor, side, pos, laid.size);
                        if datum && intent.and_then(|p| p.along).is_some() {
                            for fill in &mut c.fills {
                                for q in fill {
                                    q[0] += tip[0] - anchor[0];
                                    q[1] += tip[1] - anchor[1];
                                }
                            }
                        }
                        if intent.and_then(|p| p.along).is_some() {
                            if let Some(line) = c.lines.first_mut() {
                                line[0] = if datum {
                                    [
                                        line[0][0] + tip[0] - anchor[0],
                                        line[0][1] + tip[1] - anchor[1],
                                    ]
                                } else {
                                    *tip
                                };
                            }
                            for a in &mut c.arrows {
                                *a = placement::arrow(*tip, c.elbow);
                            }
                        }
                        if datum && intent.and_then(|p| p.along).is_some() {
                            let [apex, near] = c.lines[0];
                            let u = outward(side);
                            let lateral = (near[0] - apex[0]) * u[1] - (near[1] - apex[1]) * u[0];
                            if lateral.abs() > 1e-6 {
                                let reach = dot([near[0] - apex[0], near[1] - apex[1]], u);
                                let stub = [
                                    apex[0] + u[0] * (0.8 * TEXT).min(reach / 2.),
                                    apex[1] + u[1] * (0.8 * TEXT).min(reach / 2.),
                                ];
                                c.lines = vec![[apex, stub], [stub, near]];
                            }
                        }
                        if let Some(line) = extension {
                            c.lines.push(*line);
                        }
                        if face_on {
                            // Inside the outline the leader ends in a dot on the face.
                            c.arrows.clear();
                            c.fills = vec![dot_at(c.lines[0][0])];
                        }
                        let compact_datum = protect && datum && !authored;
                        let shorter = compact_datum
                            && best
                                .as_ref()
                                .is_some_and(|b| c.leader_length() < b.2.leader_length() - 1e-6);
                        let g = if intent.is_some_and(|p| p.pinned) {
                            (0, 0)
                        } else if matches!(search, Search::All | Search::Baseline)
                            || (compact && datum)
                        {
                            // A shorter equal-grade datum can now win the tie, so
                            // its crossings must be exact rather than pruned at a tie.
                            obstacles.grade(
                                &c,
                                if shorter {
                                    None
                                } else {
                                    best.as_ref().map(|b| b.0)
                                },
                            )
                        } else {
                            (obstacles.clearance_grade(&c), 0)
                        };
                        if best
                            .as_ref()
                            .is_none_or(|b| g < b.0 || (shorter && g == b.0))
                        {
                            // The normal shaft clearance is the shortest possible
                            // datum leader. A longer clear route must still try other
                            // proven face sites before it can win on site order alone.
                            let shortest = c.leader_length() <= 0.8 * TEXT + 1e-6;
                            best = Some((g, side, c, *tip, n));
                            if g == (0, 0) && (!compact_datum || shortest) {
                                break 'search;
                            }
                        }
                    }
                }
            }
        }
        // Invariant: The finite tier loop always evaluates its first candidate without pruning.
        let (_, side, chosen, tip, alternative) = best.expect("a tier");
        if sites[alternative].2.is_some() {
            planned.basis += "; on an extension of its planar face beyond the view outline";
        } else if alternative > 0 {
            planned.basis +=
                "; at another proven site on its face, as the first has no clear place";
        }
        if side != item.side {
            planned.basis += &format!(
                "; its {} strip has no clear place, so it stands {side} the view",
                item.side
            );
            planned.side = side.into();
        }
        let shift = |p: P2| [p[0] + chosen.origin[0], p[1] + chosen.origin[1]];
        let moved = |t: &GdtText| GdtText {
            text: t.text.clone(),
            at: shift(t.at),
            size: t.size,
        };
        let mut arcs: Vec<[f64; 5]> = laid
            .arcs
            .iter()
            .map(|a| {
                let c = shift([a[0], a[1]]);
                [c[0], c[1], a[2], a[3], a[4]]
            })
            .collect();
        for m in &planned.modifiers {
            arcs.push(circle(chosen.elbow, RING));
            if m == "all_over" {
                arcs.push(circle(chosen.elbow, 1.7 * RING));
            }
        }
        labels.push(chosen.label_box);
        if protect {
            labels.extend(chosen.fills.iter().map(|f| fill_box(f)));
            triangles.extend(chosen.fills.iter().cloned());
        }
        lines.extend(chosen.lines.iter().copied());
        planned.state = State::Shown;
        planned.placed = Some(GdtPlaced {
            tip,
            lines: chosen.lines,
            arrows: chosen.arrows,
            label_box: chosen.label_box,
            strokes: laid
                .strokes
                .iter()
                .map(|[a, b]| [shift(*a), shift(*b)])
                .collect(),
            arcs,
            fills: chosen.fills,
            letters: laid.letters.iter().map(moved).collect(),
            runs: laid.runs.iter().map(moved).collect(),
        });
        // This contact cannot be undone by placing later items. Stop a search that the
        // complete-arrangement guard would reject, before searching the remaining items.
        if let Some(allowed) = allowed.filter(|_| on_sheet(&planned.view)) {
            // Invariant: The preceding placement assignment set planned.placed to Some.
            let p = planned.placed.as_ref().expect("just placed");
            for (id, lines, arrows) in dims
                .iter()
                .filter(|d| on_sheet(&d.view))
                .filter_map(|d| d.placed.as_ref().map(|p| (&d.id, &p.lines, &p.arrows)))
                .chain(
                    out.iter()
                        .filter(|g: &&PlannedGdt| on_sheet(&g.view))
                        .filter_map(|g| g.placed.as_ref().map(|p| (&g.id, &p.lines, &p.arrows))),
                )
            {
                if *id == planned.id {
                    continue;
                }
                let mut pair = vec![planned.id.clone(), id.clone()];
                pair.sort_unstable();
                if !allowed.contains(&("annotation_leader_crossing".into(), pair))
                    && p.lines.iter().any(|s| {
                        lines
                            .iter()
                            .chain(arrows)
                            .any(|t| super::leaders::lines_meet(*s, *t))
                    })
                {
                    return None;
                }
            }
        }
        out.push(planned);
    }
    out.sort_by(|a, b| a.id.cmp(&b.id));
    // What the placed items collide with, as the dimensions' collisions are judged; the
    // dimensions' own are reported by them.
    let mut all: Vec<PlannedDimension> = dims.to_vec();
    all.extend(out.iter().filter_map(proxy));
    problems.extend(
        placement::collisions_cached(analysis, &all, sheet, views, cache)
            .into_iter()
            .filter(|p| p.subjects.iter().any(|s| s.starts_with("pmi:"))),
    );
    problems.extend(symbol_collisions(out.iter().filter(|g| on_sheet(&g.view))));
    Some((out, problems))
}

/// Datum *letter*'s symbol with its triangle's base centred at *tip* and its line running out
/// toward *side*, its frame in tier *tier* (0 nearest, as [`first_tier`] spaces it from a tip).
pub(super) fn datum_at(letter: &str, tip: P2, side: &str, tier: usize) -> GdtPlaced {
    let d = outward(side);
    let size = [H, H];
    let reach = 0.9 * TRIANGLE + 0.8 * TEXT + TEXT + tier as f64 * step(Shape::Datum, side, size);
    let pos = dot(tip, d) + reach;
    let c = candidate(
        Shape::Datum,
        tip,
        side,
        if d[0] + d[1] < 0.0 { -pos } else { pos },
        size,
    );
    let laid = datum_frame(letter);
    let shift = |p: P2| [p[0] + c.origin[0], p[1] + c.origin[1]];
    let moved = |t: &GdtText| GdtText {
        text: t.text.clone(),
        at: shift(t.at),
        size: t.size,
    };
    GdtPlaced {
        tip,
        lines: c.lines,
        arrows: c.arrows,
        label_box: c.label_box,
        strokes: laid
            .strokes
            .iter()
            .map(|[a, b]| [shift(*a), shift(*b)])
            .collect(),
        arcs: Vec::new(),
        fills: c.fills,
        letters: laid.letters.iter().map(moved).collect(),
        runs: laid.runs.iter().map(moved).collect(),
    }
}

/// How free a surface finish is to move along its strip: its count of other proven sites there
/// (none for anything else). In one strip the finish with fewest places is placed first, so a
/// narrow face (a flange) keeps its only site before a long one takes the paper beside it.
fn finish_freedom(item: &Lowered) -> (bool, usize) {
    match item.glyph {
        Glyph::Finish { .. } => (true, item.alternatives.len()),
        _ => (false, 0),
    }
}

/// The filled dot a leader ends in at *c*, as a polygon.
fn dot_at(c: P2) -> Vec<P2> {
    (0..12)
        .map(|i| {
            let a = f64::from(i) * std::f64::consts::PI / 6.0;
            [c[0] + DOT * a.cos(), c[1] + DOT * a.sin()]
        })
        .collect()
}

/// Datum triangles must not overlap or even touch. Separating-axis testing also
/// detects containment; leader intersections and letter boxes cannot see it.
fn polygons_touch(a: &[P2], b: &[P2]) -> bool {
    if a.len() < 3 || b.len() < 3 {
        return false;
    }
    ![a, b].iter().any(|polygon| {
        (0..polygon.len()).any(|i| {
            let (p, q) = (polygon[i], polygon[(i + 1) % polygon.len()]);
            let normal = [p[1] - q[1], q[0] - p[0]];
            let interval = |points: &[P2]| {
                points
                    .iter()
                    .map(|p| dot(*p, normal))
                    .fold([f64::INFINITY, f64::NEG_INFINITY], |r, x| {
                        [r[0].min(x), r[1].max(x)]
                    })
            };
            let (a, b) = (interval(a), interval(b));
            a[1] < b[0] - 1e-9 || b[1] < a[0] - 1e-9
        })
    })
}

/// A drawn frame citing a datum whose symbol the drawing does not show (#224): the frame cannot
/// be read without it, so each such frame is said once, naming the datums it lacks
/// (`frame_datum_not_drawn`, a warning). Why each datum is missing is its own problem
/// (`pmi_not_lowered`, `pmi_not_drawn`, an omission). Replaces any earlier statement.
pub(crate) fn cited_undrawn(plan: &mut crate::model::Plan) {
    plan.problems.retain(|p| p.code != "frame_datum_not_drawn");
    let drawn: BTreeSet<&str> = plan
        .gdt
        .iter()
        .filter(|g| g.kind == "datum_feature" && g.state == State::Shown)
        .filter_map(|g| g.cells.first().map(String::as_str))
        .collect();
    let mut found = Vec::new();
    for g in plan
        .gdt
        .iter()
        .filter(|g| g.kind == "control_frame" && g.state == State::Shown)
    {
        let mut missing: Vec<&str> = g
            .cells
            .iter()
            .skip(2)
            .map(String::as_str)
            .filter(|d| !drawn.contains(d))
            .collect();
        missing.dedup();
        if missing.is_empty() {
            continue;
        }
        found.push(Problem {
            id: format!("p:frame_datum_not_drawn:{}", g.id),
            code: "frame_datum_not_drawn".into(),
            severity: "warning".into(),
            message: format!(
                "The feature control frame {} cites datum{} {} that the drawing does not show, so it cannot be read in full. Draw the datum{} (see why {} not drawn) or review the frame manually.",
                g.label,
                if missing.len() > 1 { "s" } else { "" },
                missing.join(", "),
                if missing.len() > 1 { "s" } else { "" },
                if missing.len() > 1 { "they are" } else { "it is" },
            ),
            subjects: vec![g.id.clone()],
            refs: missing.iter().map(|d| format!("datum {d}")).collect(),
            ..Default::default()
        });
    }
    plan.problems.extend(found);
    plan.status = crate::completeness::status(&plan.problems);
}

/// Independent final-plan lint, including pinned and unavoidable placements.
pub fn symbol_collisions<'a>(items: impl IntoIterator<Item = &'a PlannedGdt>) -> Vec<Problem> {
    let datums: Vec<_> = items
        .into_iter()
        .filter(|g| g.kind == "datum_feature")
        .filter_map(|g| g.placed.as_ref().map(|p| (g, p)))
        .collect();
    let mut problems = Vec::new();
    for (i, (a, pa)) in datums.iter().enumerate() {
        for (b, pb) in &datums[i + 1..] {
            if pa
                .fills
                .iter()
                .any(|fa| pb.fills.iter().any(|fb| polygons_touch(fa, fb)))
            {
                problems.push(Problem {
                    id: format!("p:datum_symbol_overlap:{}|{}", a.id, b.id),
                    code: "datum_symbol_overlap".into(),
                    severity: "error".into(),
                    message: format!("The filled triangles of datum feature symbols {} and {} overlap or touch; move their attachments apart.", a.label, b.label),
                    subjects: vec![a.id.clone(), b.id.clone()],
                    ..Default::default()
                });
            }
        }
    }
    problems
}

/// A placed item as the dimensions' collision checks see it: a callout whose leader is its
/// lines and whose label is its frame.
pub fn proxy(g: &PlannedGdt) -> Option<PlannedDimension> {
    let p = g.placed.as_ref()?;
    Some(PlannedDimension {
        callout_offset: None,
        reference: false,
        determined_by: Vec::new(),
        region: None,
        id: g.id.clone(),
        kind: "callout".into(),
        view: g.view.clone(),
        value: 0.0,
        label: g.cells.join(" "),
        terms: g.cells.clone(),
        measurements: Vec::new(),
        default: true,
        from_spec: Vec::new(),
        basis: g.basis.clone(),
        state: State::Shown,
        placed: Some(Placed {
            lines: p.lines.clone(),
            arrows: p.arrows.clone(),
            label_at: [p.label_box[0], p.label_box[1]],
            rotation: 0.0,
            label_box: p.label_box,
        }),
    })
}

/// The default surface finish's symbol beside the title block (*r*) as the placed items'
/// collision checks and the blocks placed after it see it: its strokes as lines, its whole box
/// as its label.
pub fn finish_proxy(r: &crate::pmi_lowering::PlannedRequirement) -> Option<PlannedDimension> {
    let p = r.placed.as_ref()?;
    Some(PlannedDimension {
        callout_offset: None,
        reference: false,
        determined_by: Vec::new(),
        region: None,
        id: r.id.clone(),
        kind: "symbol".into(),
        view: String::new(),
        value: 0.0,
        label: r.text.clone(),
        terms: vec![r.text.clone()],
        measurements: Vec::new(),
        default: true,
        from_spec: Vec::new(),
        basis: r.basis.clone(),
        state: State::Shown,
        placed: Some(Placed {
            lines: p.strokes.clone(),
            arrows: Vec::new(),
            label_at: [p.glyph[0], p.glyph[1]],
            rotation: 0.0,
            label_box: p.glyph,
        }),
    })
}

/// What the default surface finish's symbol (*r*) collides with among the placed *items* (the
/// dimensions, frames and labels), judged as their collisions are.
pub fn finish_collisions(
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    items: &[PlannedDimension],
    r: &crate::pmi_lowering::PlannedRequirement,
) -> Vec<Problem> {
    finish_collisions_cached(
        analysis,
        sheet,
        views,
        items,
        r,
        &super::footprints::Cache::default(),
    )
}

pub(crate) fn finish_collisions_cached(
    analysis: &Analysis,
    sheet: &PlannedSheet,
    views: &[PlannedView],
    items: &[PlannedDimension],
    r: &crate::pmi_lowering::PlannedRequirement,
    cache: &super::footprints::Cache,
) -> Vec<Problem> {
    if sheet.number != 1 {
        return Vec::new();
    }
    let Some(proxy) = finish_proxy(r) else {
        return Vec::new();
    };
    let mut all = items.to_vec();
    all.push(proxy);
    // It stands in the band the title block's reservation keeps above the grid, as the scale
    // note does, so the reservation is not an obstacle to it.
    placement::collisions_cached(analysis, &all, sheet, views, cache)
        .into_iter()
        .filter(|q| q.subjects.contains(&r.id) && q.code != "annotation_over_title_block")
        .collect()
}

/// Draws a planned item into *items* (the dims layer) and its runs into *text*, from the plan
/// alone; returns its annotation for lint (no measurement claims: a frame states the source's
/// requirement, not a measurement of the part).
pub fn render(
    g: &PlannedGdt,
    items: &mut Vec<Item>,
    text: &mut Vec<TextRun>,
) -> Option<Annotation> {
    // An omitted datum, or one not drawn by default because nothing cites or measures from it,
    // is suppressed by policy: recorded, not missing ink.
    if g.kind == "datum_feature"
        && (g.state == State::OmittedByUser || g.state == State::not_drawable("datum_not_drawn"))
    {
        return Some(Annotation {
            name: format!("suppressed:{}", g.id),
            view: Some(g.view.clone()),
            ..Default::default()
        });
    }
    let p = g.placed.as_ref()?;
    let first = text.len();
    for [a, b] in p.lines.iter().chain(&p.strokes) {
        items.push(Item::Edge(Curve::Line(*a, *b)));
    }
    for [tip, tail] in &p.arrows {
        let n = [tail[1] - tip[1], tip[0] - tail[0]];
        let k = ARROW_HALF / n[0].hypot(n[1]).max(1e-12);
        let b1 = [tail[0] + k * n[0], tail[1] + k * n[1]];
        let b2 = [tail[0] - k * n[0], tail[1] - k * n[1]];
        items.push(Item::Face(vec![vec![
            Curve::Line(*tip, b1),
            Curve::Line(b1, b2),
            Curve::Line(b2, *tip),
        ]]));
    }
    for f in &p.fills {
        let wire: Vec<Curve> = (0..f.len())
            .map(|i| Curve::Line(f[i], f[(i + 1) % f.len()]))
            .collect();
        items.push(Item::Face(vec![wire]));
    }
    for a in &p.arcs {
        items.push(Item::Edge(Curve::Conic {
            centre: [a[0], a[1]],
            radii: [a[2], a[2]],
            x_axis: [1.0, 0.0],
            y_axis: [0.0, 1.0],
            t0: a[3].to_radians(),
            t1: a[4].to_radians(),
        }));
    }
    let f = font();
    for l in &p.letters {
        let laid = f.text(&l.text, l.size);
        let shift = |q: P2| [q[0] + l.at[0], q[1] + l.at[1]];
        let wires: Vec<Vec<Curve>> = laid
            .contours
            .iter()
            .filter(|c| !c.is_empty())
            .map(|c| {
                c.iter()
                    .map(|s| match *s {
                        Segment::Line(a, b) => Curve::Line(shift(a), shift(b)),
                        Segment::Quad(a, b, c) => {
                            Curve::Beziers(vec![vec![shift(a), shift(b), shift(c)]])
                        }
                        Segment::Cubic(a, b, c, d) => {
                            Curve::Beziers(vec![vec![shift(a), shift(b), shift(c), shift(d)]])
                        }
                    })
                    .collect()
            })
            .collect();
        if !wires.is_empty() {
            items.push(Item::Face(wires));
        }
    }
    for r in &p.runs {
        text.push(TextRun {
            text: r.text.clone(),
            at: r.at,
            size: r.size,
            rotation: 0.0,
            font: FONT.into(),
            h_align: HAlign::Center,
            v_align: VAlign::Middle,
        });
    }
    Some(Annotation {
        approved_claims: Vec::new(),
        approved_text: None,
        coordinate_origin: None,
        satisfies: Vec::new(),
        name: g.id.clone(),
        view: Some(g.view.clone()),
        runs: (first..text.len()).collect(),
        claims: Vec::new(),
    })
}

fn placement_choices(
    item: &Lowered,
    record: &crate::pmi::Record,
    views: &[PlannedView],
) -> Vec<(&'static str, Vec<&'static str>)> {
    views
        .iter()
        .filter_map(|view| {
            let name = match view.id.as_str() {
                "v:front" => "front",
                "v:plan" => "plan",
                "v:side" => "side",
                _ => return None,
            };
            match &item.glyph {
                Glyph::Label { .. }
                    if record
                        .geometry
                        .as_ref()
                        .is_some_and(|g| !g.sites.is_empty()) =>
                {
                    Some((name, vec!["above", "below", "left", "right"]))
                }
                Glyph::ControlFrame { .. } | Glyph::Label { .. } | Glyph::FlagNote { .. } => {
                    (name == item.view).then(|| (name, vec!["above", "below", "left", "right"]))
                }
                Glyph::Datum { surface, .. } | Glyph::Finish { surface, .. }
                    if surface == "plane" =>
                {
                    let normal = record.geometry.as_ref()?.normal?;
                    let dot: f64 = normal.iter().zip(view.direction).map(|(a, b)| a * b).sum();
                    if dot.abs() > 1e-6 {
                        return None;
                    }
                    let projected = haecceity::hlr::View::new(view.direction, view.up)?.map(normal);
                    let side = if projected[0].abs() > 0.9 {
                        if projected[0] > 0.0 { "right" } else { "left" }
                    } else if projected[1].abs() > 0.9 {
                        if projected[1] > 0.0 { "above" } else { "below" }
                    } else {
                        return None;
                    };
                    Some((name, vec![side]))
                }
                // A cylinder's finish may stand on either side its record proves a site on.
                Glyph::Finish { .. } if name == item.view => {
                    let mut sides = vec![item.side];
                    if let Some(other) = other_finish_side(item, record) {
                        sides.push(other);
                    }
                    Some((name, sides))
                }
                Glyph::Datum { .. } | Glyph::Finish { .. } => {
                    (name == item.view).then(|| (name, vec![item.side]))
                }
            }
        })
        .collect()
}

/// The strip opposite a cylinder finish's own (*item*) when its record proves a site there: one
/// whose coordinate across the strip differs from its lowered site's (the silhouettes, or the
/// head-on sites either side of the axis, each share theirs).
fn other_finish_side(item: &Lowered, record: &crate::pmi::Record) -> Option<&'static str> {
    let Glyph::Finish { surface, .. } = &item.glyph else {
        return None;
    };
    if surface != "cylinder" {
        return None;
    }
    let beside = outward(item.side)[0] != 0.0;
    // The model axis across the strip in the view: front x | z, plan x | y, side y | z.
    let across = match (item.view, beside) {
        ("side", true) => 1,
        (_, true) => 0,
        ("plan", false) => 1,
        (_, false) => 2,
    };
    let g = record.geometry.as_ref()?;
    g.sites
        .iter()
        .chain(&g.alternatives)
        .any(|p| (p[across] - item.site[across]).abs() > 1e-6)
        .then_some(match item.side {
            "left" => "right",
            "right" => "left",
            "above" => "below",
            _ => "above",
        })
}

/// Authored carriers must prove the attachment before placement.
pub(crate) fn validate_placements(
    analysis: &Analysis,
    spec: &Spec,
    views: &[PlannedView],
) -> Result<(), crate::model::Invalid> {
    if spec.annotations.is_empty() {
        return Ok(());
    }
    let shown = |name: &str| views.iter().any(|v| v.id == format!("v:{name}"));
    let (items, _) = crate::pmi_lowering::lower(analysis, &shown);
    let all: Vec<_> = PRINCIPALS
        .iter()
        .map(|(name, direction, up)| PlannedView {
            detail: None,
            sheet: 1,
            scale_note: None,
            xref: None,
            id: format!("v:{name}"),
            direction: *direction,
            up: *up,
            at: [0.0; 2],
            scale: 1.0,
            area: [0.0; 4],
            default: true,
            basis: String::new(),
            state: State::Shown,
            cut: None,
            half_section: None,
            broken: None,
            line_work: Vec::new(),
            line_style: Default::default(),
            from_spec: Vec::new(),
        })
        .collect();
    let Some(crate::pmi::Reading::Read(report)) = &analysis.pmi else {
        return Ok(());
    };
    for item in items {
        let Some(entry) = spec.annotations.get(&item.id).filter(|e| !e.omit) else {
            continue;
        };
        let Some(place) = entry
            .place
            .as_ref()
            .filter(|p| !super::hole_marks::pinned_on_mark(analysis, &item, p))
        else {
            continue;
        };
        let Some(record) = report.records.iter().find(|r| r.source_id == item.source) else {
            continue;
        };
        let choices = placement_choices(&item, record, &all);
        let view = place.view.as_deref().unwrap_or(item.view);
        let Some((_, sides)) = choices.iter().find(|(name, _)| *name == view) else {
            return Err(crate::model::Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "{} cannot attach in the {view} view; choose a compatible view.",
                    record.label
                ),
                choices: choices.iter().map(|(name, _)| name.to_string()).collect(),
            });
        };
        // A finish stands only beside a silhouette (or half face) it proves a site on.
        if let (Glyph::Finish { .. }, Some(side)) = (&item.glyph, place.side)
            && view == item.view
            && !sides.contains(&side.name())
        {
            return Err(crate::model::Invalid {
                path: None,
                also: Vec::new(),
                message: format!(
                    "{} has no proven site {} the {view} view; choose a side it attaches on.",
                    record.label,
                    side.name()
                ),
                choices: sides.iter().map(|s| s.to_string()).collect(),
            });
        }
    }
    Ok(())
}

pub fn placement_options(
    analysis: &Analysis,
    views: &[PlannedView],
) -> BTreeMap<String, serde_json::Value> {
    let (items, _) = crate::pmi_lowering::lower(analysis, &|name| {
        views.iter().any(|view| view.id == format!("v:{name}"))
    });
    let identities = match analysis.pmi.as_ref() {
        Some(crate::pmi::Reading::Read(report)) => report.identities(),
        _ => return BTreeMap::new(),
    };
    items
        .into_iter()
        .filter_map(|item| {
            let Some(crate::pmi::Reading::Read(report)) = &analysis.pmi else {
                return None;
            };
            let record = report.records.iter().find(|r| r.source_id == item.source)?;
            let id = identities.id(&record.source_id).ok()?;
            let choices = placement_choices(&item, record, views)
                .into_iter()
                .map(|(view, sides)| serde_json::json!({"view": view, "sides": sides}))
                .collect::<Vec<_>>();
            Some((
                id.to_string(),
                serde_json::json!({"label": record.label, "views": choices}),
            ))
        })
        .collect()
}

/// Straight, short normal routes precede one-bend routes with a square stub.
/// The triangle and any reader-proven face extension stay fixed. No automatic
/// oblique departure is generated, even when every candidate retains a contact.
pub(super) fn normal_routes(g: &PlannedGdt, bends: bool) -> Vec<Placed> {
    let Some(p) = &g.placed else {
        return Vec::new();
    };
    let Some(&[apex, near]) = p.lines.first() else {
        return Vec::new();
    };
    let Some(triangle) = p.fills.first().filter(|f| f.len() == 3) else {
        return Vec::new();
    };
    let base = [
        (triangle[0][0] + triangle[2][0]) / 2.,
        (triangle[0][1] + triangle[2][1]) / 2.,
    ];
    let v = [triangle[1][0] - base[0], triangle[1][1] - base[1]];
    let n = v[0].hypot(v[1]);
    if n <= 1e-9 {
        return Vec::new();
    }
    let u = v.map(|x| x / n);
    let old = (near[0] - apex[0]).hypot(near[1] - apex[1]);
    let mut routes = Vec::new();
    for i in 0..64 {
        let length = 0.8 * TEXT + f64::from(i);
        if length > old + 1e-6 {
            break;
        }
        let end = [apex[0] + u[0] * length, apex[1] + u[1] * length];
        let delta = [end[0] - near[0], end[1] - near[1]];
        let Some(mut q) = proxy(g).and_then(|q| q.placed) else {
            break;
        };
        q.lines[0][1] = end;
        for k in 0..4 {
            q.label_box[k] += delta[k % 2];
        }
        q.label_at = [q.label_box[0], q.label_box[1]];
        routes.push(q);
    }
    // A lateral frame can escape an occupied normal corridor after a square stub.
    // Try the shorter normal tiers first; retain all face-extension strokes.
    if !bends {
        return routes;
    }
    let straight = routes.clone();
    for q in straight.iter().take(8) {
        for offset in [-8., 8., -16., 16.] {
            let delta = [-u[1] * offset, u[0] * offset];
            let mut bent = q.clone();
            let end = [q.lines[0][1][0] + delta[0], q.lines[0][1][1] + delta[1]];
            let stub = [apex[0] + u[0] * 0.8 * TEXT, apex[1] + u[1] * 0.8 * TEXT];
            if (end[0] - stub[0]) * u[0] + (end[1] - stub[1]) * u[1] <= 1e-6 {
                continue;
            }
            bent.lines[0] = [apex, stub];
            bent.lines.insert(1, [stub, end]);
            for k in 0..4 {
                bent.label_box[k] += delta[k % 2];
            }
            bent.label_at = [bent.label_box[0], bent.label_box[1]];
            routes.push(bent);
        }
    }
    routes
}

/// Explicit datum visibility intent, shared by strip demand and symbol placement.
fn datum_omission(analysis: &Analysis, spec: &Spec, item: &Lowered) -> Option<&'static str> {
    if !matches!(item.glyph, Glyph::Datum { .. })
        || (spec.sheet.uncited_datums == Some(true)
            && !spec.annotations.get(&item.id).is_some_and(|a| a.omit))
    {
        return None;
    }
    let Some(crate::pmi::Reading::Read(report)) = &analysis.pmi else {
        return None;
    };
    report
        .records
        .iter()
        .find(|r| r.source_id == item.source)
        .and_then(|r| {
            let origins = super::pmi_policy::origin_datums(analysis, spec);
            crate::spec_gdt::datum_omission(spec, report, r, &origins)
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn datum_route_inventory_tries_straight_before_square_stub_bends() {
        let a = crate::model::analyse_native(
            &std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("tests/fixtures/samples/cnc-review-spindle.step.gz"),
        )
        .unwrap()
        .1;
        let p = crate::model::plan(&a, &Spec::default()).unwrap();
        let g = p
            .gdt
            .iter()
            .find(|g| {
                g.kind == "datum_feature" && g.placed.as_ref().is_some_and(|p| p.lines.len() == 1)
            })
            .unwrap();
        // Make a deliberately long normal route so the inventory has both tiers
        // and bends to exercise (the real spindle is already at minimum reach).
        let mut g = g.clone();
        let u = outward(&g.side);
        let placed = g.placed.as_mut().unwrap();
        for (k, value) in u.into_iter().enumerate() {
            placed.lines[0][1][k] += 20. * value;
            placed.label_box[k] += 20. * value;
            placed.label_box[k + 2] += 20. * value;
        }
        let placed = g.placed.as_ref().unwrap();
        let triangle = &placed.fills[0];
        let base = [
            triangle[2][0] - triangle[0][0],
            triangle[2][1] - triangle[0][1],
        ];
        let routes = normal_routes(&g, true);
        assert!(!routes.is_empty());
        let mut bent = false;
        for route in routes {
            let [a, b] = route.lines[0];
            assert_eq!(a, placed.lines[0][0], "the triangle stays fixed");
            assert!(dot(base, [b[0] - a[0], b[1] - a[1]]).abs() < 1e-8);
            if route.lines.len() == 2 {
                bent = true;
                assert!(((b[0] - a[0]).hypot(b[1] - a[1]) - 0.8 * TEXT).abs() < 1e-8);
                assert_eq!(b, route.lines[1][0]);
            } else {
                assert!(!bent, "straight routes precede every bend");
            }
        }
        assert!(bent, "a blocked normal corridor has one-bend alternatives");
    }

    #[test]
    fn a_turned_shoulder_has_clear_exterior_paper_at_its_upper_site() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/pmi/step/GRM-03_thumbwheel_drive_screw_AP242_PMI.step.gz");
        let (_, mut analysis) = crate::model::analyse_native(&path).unwrap();
        // Keep a frame citation here to exercise existing cited-datum placement
        // alongside the separate regressions for uncited author declarations.
        if let Some(crate::pmi::Reading::Read(report)) = &mut analysis.pmi {
            let mut control = report
                .records
                .iter()
                .find(|r| r.category == crate::pmi::Category::Datum && r.label == "B")
                .unwrap()
                .clone();
            control.category = crate::pmi::Category::GeometricTolerance;
            control.source_id = "geometric_tolerance:placement-test".into();
            control.kind = "perpendicularity".into();
            control.value = Some(0.1);
            control.label = "⟂ 0.1 B".into();
            control.datum_refs = vec!["B".into()];
            report.records.push(control);
        }
        let view: PlannedView = serde_json::from_value(serde_json::json!({
            "id": "v:front", "direction": [0,-1,0], "up": [0,0,1],
            "at": [0,0], "scale": 5, "area": [-500,-500,500,500],
            "state": {"kind":"shown"}, "default": true, "basis": "test"
        }))
        .unwrap();
        let (items, _) = crate::pmi_lowering::lower(&analysis, &|_| true);
        let item = items
            .iter()
            .find(|i| matches!(&i.glyph, Glyph::Datum { letter, .. } if letter == "B"))
            .unwrap();
        let frame = Frame::new(&analysis, &view);
        let tip = std::iter::once(item.site)
            .chain(item.alternatives.iter().copied())
            .map(|p| frame.page(p))
            .max_by(|a, b| a[1].total_cmp(&b[1]))
            .unwrap();
        let c = candidate(
            Shape::Datum,
            tip,
            "right",
            tip[0] + 0.9 * TRIANGLE + 0.8 * TEXT + TEXT,
            [H, H],
        );
        assert!(
            !crate::dims::interior::proofs(&analysis, std::slice::from_ref(&view))
                .contains_key(&view.id),
            "ordinary dimension routing retains its planar-face requirement"
        );
        let proofs = crate::dims::interior::exterior_proofs(&analysis, std::slice::from_ref(&view));
        let proof = proofs
            .get(&view.id)
            .expect("turned side view has exterior proof");
        assert!(
            proof.exterior_clear(&view, c.label_box, &c.lines, &c.arrows),
            "tip {tip:?}, box {:?}, lines {:?}",
            c.label_box,
            c.lines
        );
    }

    #[test]
    fn datum_triangle_contact_detects_containment_and_touch_but_not_empty_corners() {
        let a = [[0.0, 0.0], [4.0, 0.0], [0.0, 4.0]];
        for b in [
            a,
            [[0.5, 0.5], [1.0, 0.5], [0.5, 1.0]],
            [[4.0, 0.0], [5.0, 0.0], [4.0, 1.0]],
        ] {
            assert!(polygons_touch(&a, &b));
            assert!(polygons_touch(&b, &a));
        }
        let clear = [[3.0, 3.0], [4.0, 3.0], [3.0, 4.0]];
        assert!(boxes_overlap(fill_box(&a), fill_box(&clear)));
        assert!(!polygons_touch(&a, &clear));
    }

    #[test]
    fn filled_datum_triangles_reserve_clearance_even_with_separate_boxes() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/samples/cnc-review-spindle.step.gz");
        let (_, analysis) = crate::model::analyse_native(&path).unwrap();
        let plan = crate::model::plan_recovered(&analysis, &Spec::default()).unwrap();
        let page = Page::new(&analysis, &plan.sheet, &plan.views);
        let first = candidate(Shape::Datum, [30.0, 180.0], "right", 50.0, [H, H]);
        let obstacles = Obstacles {
            page: &page,
            own: "v:front".into(),
            area: None,
            exterior: None,
            protect_triangles: true,
            parallel_gap: None,
            triangles: first.fills.clone(),
            labels: std::iter::once(first.label_box)
                .chain(first.fills.iter().map(|f| fill_box(f)))
                .collect(),
            lines: first.lines.clone(),
        };
        // The letter boxes and shafts are disjoint in every case. Identical,
        // overlapping and just-touching triangles must still be rejected.
        for offset in [0.0, TRIANGLE / 2.0, TRIANGLE] {
            let next = candidate(Shape::Datum, [30.0, 180.0 + offset], "right", 75.0, [H, H]);
            assert!(!boxes_overlap(first.label_box, next.label_box));
            assert_eq!(obstacles.clearance_grade(&next), 5, "offset {offset}");
        }
        let clear = candidate(
            Shape::Datum,
            [30.0, 180.0 + TRIANGLE + 1.0],
            "right",
            75.0,
            [H, H],
        );
        assert!(obstacles.clearance_grade(&clear) < 4);
    }

    #[test]
    fn planar_extensions_require_the_face_to_reach_the_outline() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/samples/cnc-review-spindle.step.gz");
        let (_, mut analysis) = crate::model::analyse_native(&path).unwrap();
        let (items, _) = crate::pmi_lowering::lower(&analysis, &|_| true);
        let item = items
            .iter()
            .find(|i| matches!(&i.glyph, Glyph::Datum { letter, .. } if letter == "A"))
            .unwrap();
        let frame = Frame::looking(&analysis, [0.0, -1.0, 0.0], [0.0, 0.0, 1.0], [0.0; 2], 1.0);
        assert!(!plane_extensions(&analysis, item, &frame).is_empty());
        // Inset both ends of this edge-on face while keeping the view outline fixed.
        for face in &item.faces {
            let bounds = analysis.face_attachments[*face].bounds.as_mut().unwrap();
            bounds[0] = analysis.envelope[0][0] + 2.0;
            bounds[3] = analysis.envelope[1][0] - 2.0;
        }
        assert!(plane_extensions(&analysis, item, &frame).is_empty());
        // The guard is directional: reaching only the right outline offers only that end.
        for face in &item.faces {
            analysis.face_attachments[*face].bounds.as_mut().unwrap()[3] = analysis.envelope[1][0];
        }
        let sites = plane_extensions(&analysis, item, &frame);
        assert!(!sites.is_empty());
        assert!(sites.iter().all(|s| s.0[0] > frame.outline()[2]));
    }
}
