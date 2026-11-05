//! Structural checks of the drawn sheet (draftwright's `lint_drawing`, `linting/structural.py`
//! and `linting/ink_overlap.py`): what the sheet looks like, not what it says about the part.
//! They read the rendered drawing alone — its page, its text and what it records of the views
//! and things drawn ([`Drawing::sheet`]: each view's visible edges and their box, each
//! annotation's and each piece of furniture's ink and label boxes, line-work and leader elbow,
//! and the title block's fields) — and never the part, the Plan or the Spec.
//!
//! Ported, in Python's order and with its thresholds and severities:
//! - `derived_view_identifier_reused` (`_lint_derived_view_identifiers`): one section or detail
//!   letter naming two views, read from the views' names, never their captions. Error.
//! - `scale_not_stated` (`_lint_scale_stated`): a finished sheet (one with a title block) with no
//!   scale note. Error.
//! - `title_field_overflow` (`_lint_title_fields`): a title-block value whose ink is wider or
//!   taller than its cell. Warning; the message points to the notes block, as draftwright's does.
//! - `leader_line_through_text` (`_lint_leader`): a callout's leader elbow inside its own label's
//!   box, or a datum's shaft entering its frame's interior. Meeting the frame border is legal.
//!   Error.
//! - `annotation_overlap` (`_lint_annotation_pairs`): two labels whose text boxes overlap beyond
//!   roundoff in both directions. A pair involving label-less furniture keeps the 0.5 mm
//!   allowance for conservative ink bounds; a thing with no label (the title block, the notes block, a
//!   section's hatch, arrowheads and wings) takes part by its whole ink, a centreline none, as in
//!   Python. Where the pair's line-work also runs through one of the labels, the message says
//!   which, by how much, and that separating the text is not enough. Warning.
//! - `annotation_ink_overlap` (`_lint_annotation_pairs` with `ink_overlap.label_crossings`): one
//!   thing's straight line-work running at least 0.5 mm through another's label box, measured as
//!   the longest connected stroke inside it, for each pair whose labels clear each other; both
//!   directions. Warning.
//! - `label_centerline_overlap` (`_lint_centerline_dim_overlap`): an actual label
//!   overlapping a centreline's line by more than 0.5 mm both ways, a line measured
//!   across its stroke by how deep it runs into the box. Warning.
//! - `leader_centerline_overlap`: a callout shares over 0.5 mm of a fixed chain stroke.
//!   Transverse crossings and contact outside the finite stroke are excluded. Warning.
//! - `label_section_furniture_overlap`: actual text touches a section letter, arrow or
//!   wing box beyond numerical roundoff, including shallow contacts. Warning.
//! - `annotation_out_of_bounds` (`_lint_annotation_bounds`): any ink past the drawable area,
//!   the title block allowed its border's half stroke. Error, once per side overshot.
//! - `view_annotation_overlap` and `view_annotation_inside_extents`
//!   (`_lint_view_annotation_overlap`): a label (or a label-less thing's ink; not a centreline
//!   or hatching) over a view's box: a warning when one of the view's visible edges passes
//!   through it, otherwise an info notice that it lies over blank face, unless it was placed
//!   inside on purpose.
//! - `view_overlap` and `view_out_of_bounds` (`_lint_view_shapes`, its box part): two views'
//!   boxes overlapping (warning), a view's box past the drawable area (error, once per side).

//!
//! The port's own (#32): `dimension_line_shared` (two linear dimensions on one dimension line
//! over overlapping spans) and `dimension_text_stray` (a linear dimension's text more than a text
//! height from its own line). Errors.
//!
//! Run last, from [`super::fidelity`] (stream K7): `label_vs_measured`, `nominal_rounded`,
//! `angular_label_vs_geometry`, `angular_geometry_mismatch`, `angular_support_mismatch` and
//! `datum_leader_remote`, what each label states against what its ink draws.
//!
//! Not ported here: `interior_label_on_narrow_material` and
//! `leader_crosses_silhouette` (they read the view's filled material, which the drawing does not
//! record), and `missing_principal_dimension` (draftwright's sheet lint never runs it: its one
//! `lint_drawing` call passes no part box).
//!
//! Differences from Python, each judged in `tests/lint_structural.rs`:
//! - Required `note_iso_nts` furniture reports label and line contacts beyond numerical
//!   roundoff, including those below the generic 0.5 mm overlap thresholds.
//! - The port draws every label along or across the page, so a label's box is its text's own:
//!   no label polygon, and no diagonal dimension to leave uncrossable.
//! - A centreline without line-work (draftwright's centre circle) is judged by its whole ink's
//!   box; draftwright then checks its drawn edges. Planned pitch circles carry sampled strokes.
//! - A curved view edge is sampled at draftwright's spacing by its own parameter (a conic's
//!   angle, a Bézier's), where build123d samples its edge's parameter.
//! - The messages name the port's annotations and leave out draftwright's API advice
//!   (`label_offset_x`, `margin`, `dim_width`) and issue numbers, which the port's spec does not
//!   have.

use std::collections::BTreeMap;

use super::{LintIssue, Severity};
use crate::output::{Curve, Drawing, Drawn, DrawnView, Kind, NTS_CAPTION_ID, P};

/// Below this a box past a bound is rounding (draftwright's `BOUNDS_ROUNDOFF`).
const BOUNDS_ROUNDOFF: f64 = 1e-9;
/// Half the title block's border stroke (draftwright's `_TB_LINE_WIDTH / 2`): the border may
/// straddle the drawable area's edge.
const TITLE_HALF_STROKE: f64 = 0.075;
/// Conservative label-less furniture bounds, or a label and centreline, overlap when they
/// share more than this, mm, in both directions. Actual text pairs use BOUNDS_ROUNDOFF.
const OVERLAP: f64 = 0.5;
/// Line-work running this far through a label is reported, mm (draftwright's `MIN_CROSSING_MM`).
const MIN_CROSSING: f64 = 0.5;

fn issue(id: String, code: &str, severity: Severity, message: String) -> LintIssue {
    LintIssue {
        sheet: None,
        id,
        code: code.into(),
        severity,
        message,
        at: None,
        view: None,
        subjects: Vec::new(),
        requirements: Vec::new(),
        measurements: Vec::new(),
        sources: Vec::new(),
        stage: None,
        annotation: None,
        related: Vec::new(),
        evidence: None,
        fix: None,
    }
}

/// Every structural finding on *drawing*, in draftwright's order.
pub fn check(drawing: &Drawing) -> Vec<LintIssue> {
    let mut issues = derived_view_identifiers(drawing);
    issues.extend(scale_stated(drawing));
    issues.extend(detail_caption_clearance(drawing));
    issues.extend(cross_references(drawing));
    issues.extend(annotation_items(drawing));
    issues.extend(annotation_pairs(drawing));
    issues.extend(fixed_ink_contacts(drawing));
    issues.extend(dimension_lines(drawing));
    issues.extend(oblique_witnesses(drawing));
    issues.extend(annotation_bounds(drawing));
    issues.extend(view_annotations(drawing));
    issues.extend(view_boxes(drawing));
    // Stream K7: what each label states against what its ink draws (`super::fidelity`).
    issues.extend(super::fidelity::check(drawing));
    issues
}

/// Detail captions take part in the same text comparison as every scale group.
fn detail_caption_clearance(drawing: &Drawing) -> Vec<LintIssue> {
    let mut out = Vec::new();
    for caption in drawing
        .sheet
        .items
        .iter()
        .filter(|i| i.name.starts_with("detail_caption_"))
    {
        let Some(b) = caption.label else { continue };
        for other in drawing
            .sheet
            .items
            .iter()
            .filter(|i| i.name != caption.name)
        {
            let Some(c) = other.label else { continue };
            let dx = (b[0] - c[2]).max(c[0] - b[2]).max(0.0);
            let dy = (b[1] - c[3]).max(c[1] - b[3]).max(0.0);
            let gap = drawing
                .text
                .iter()
                .find(|t| t.text.starts_with("DETAIL "))
                .map_or((b[3] - b[1]) / 2.0, |t| t.size / 2.0);
            if dx < gap && dy < gap {
                let mut finding = issue(
                    format!("l:detail_caption_clearance:{}:{}", caption.name, other.name),
                    "detail_caption_clearance",
                    Severity::Warning,
                    format!(
                        "{} needs readable clearance from {}.",
                        caption.name, other.name
                    ),
                );
                finding.subjects = vec![caption.name.clone(), other.name.clone()];
                out.push(finding);
            }
        }
    }
    out
}

/// Compare positions within each page, retaining page identity on later-sheet findings.
pub fn check_sheets(drawings: &[Drawing]) -> Vec<LintIssue> {
    drawings
        .iter()
        .enumerate()
        .flat_map(|(index, drawing)| {
            let mut found = check(drawing);
            // A missing furniture record cannot exempt a section: derive the reciprocal pair
            // from the section view and the cutting-plane letter actually drawn on another page.
            for (page, target) in drawings.iter().enumerate() {
                for view in target
                    .sheet
                    .views
                    .iter()
                    .filter(|v| v.name.starts_with("v:section_"))
                {
                    let Some(letter) = derived_view_identifier(&view.name) else {
                        continue;
                    };
                    let marker = format!("section_{}_left", letter.to_lowercase());
                    let source = drawings
                        .iter()
                        .position(|d| d.sheet.items.iter().any(|i| i.name == marker));
                    let Some(source) = source.filter(|source| *source != page) else {
                        continue;
                    };
                    let name = if index == page {
                        format!("{}/xref", view.name)
                    } else if index == source {
                        format!("{}/cut/xref", view.name)
                    } else {
                        continue;
                    };
                    if !drawing.sheet.references.iter().any(|r| r.name == name) {
                        let mut i = issue(
                            format!("l:cross_reference_missing:{name}"),
                            "cross_reference_missing",
                            Severity::Error,
                            format!(
                                "{name} is missing from the drawn section's reciprocal addresses."
                            ),
                        );
                        i.annotation = Some(name);
                        found.push(i);
                    }
                }
            }
            for r in &drawing.sheet.references {
                let target = r
                    .sheet
                    .checked_sub(1)
                    .and_then(|n| drawings.get(n as usize));
                let zone = target.and_then(|d| {
                    let at = d
                        .sheet
                        .views
                        .iter()
                        .find(|v| v.name == r.target)
                        .map(|v| v.bbox)
                        .or_else(|| d.sheet.items.iter().find(|i| i.name == r.target)?.label)?;
                    drawn_zone(d, [(at[0] + at[2]) / 2., (at[1] + at[3]) / 2.])
                });
                if zone.as_ref() != Some(&r.zone)
                    && !found.iter().any(|i| {
                        i.code == "cross_reference_missing"
                            && i.annotation.as_deref() == Some(&r.name)
                    })
                {
                    let mut i = issue(
                        format!("l:cross_reference_missing:{}", r.name),
                        "cross_reference_missing",
                        Severity::Error,
                        format!(
                            "{} does not address its target on sheet {} in the drawn grid.",
                            r.name, r.sheet
                        ),
                    );
                    i.annotation = Some(r.name.clone());
                    found.push(i);
                }
            }
            found.into_iter().map(move |mut i| {
                if index > 0 {
                    i.sheet = Some(index as u32 + 1);
                    i.id = format!("sheet:{}/{}", index + 1, i.id);
                }
                i
            })
        })
        .collect()
}

/// Read the grid from its printed margin labels, independently of the planner's zone table.
fn drawn_zone(drawing: &Drawing, at: P) -> Option<String> {
    let b = drawing.sheet.drawable?;
    let columns = drawing
        .text
        .iter()
        .filter(|t| t.at[1] > b[3] && t.text.parse::<usize>().is_ok())
        .count();
    let letters = b"ABCDEFGHJKLMNPQRSTUVWXYZ";
    let rows = drawing
        .text
        .iter()
        .filter(|t| t.at[0] < b[0] && t.text.len() == 1 && letters.contains(&t.text.as_bytes()[0]))
        .count();
    if columns == 0 || rows == 0 || rows > letters.len() {
        return None;
    }
    let col = ((at[0] / drawing.page[0] * columns as f64).floor() as usize).min(columns - 1);
    let row = (((drawing.page[1] - at[1]) / drawing.page[1] * rows as f64).floor() as usize)
        .min(rows - 1);
    Some(format!("{}{}", letters[row] as char, col + 1))
}

fn cross_references(drawing: &Drawing) -> Vec<LintIssue> {
    drawing
        .sheet
        .references
        .iter()
        .filter_map(|r| {
            let expected = format!("{} SHEET {}, ZONE {}", r.prefix, r.sheet, r.zone);
            if drawing.text.get(r.run).is_some_and(|t| t.text == expected)
                && drawing
                    .sheet
                    .items
                    .iter()
                    .any(|i| i.name == r.name && i.label.is_some())
            {
                return None;
            }
            let mut i = issue(
                format!("l:cross_reference_missing:{}", r.name),
                "cross_reference_missing",
                Severity::Error,
                format!("{} must print {expected}.", r.name),
            );
            i.annotation = Some(r.name.clone());
            i.at = drawing.text.get(r.run).map(|t| t.at);
            i.fix = Some("Restore the section's reciprocal sheet and zone address.".into());
            Some(i)
        })
        .collect()
}

/// The semantic identifier a derived view's name encodes (draftwright's
/// `derived_view_identifier`): `section_aa` is section A (the slug doubled), `detail_b` detail B;
/// none for any other name, or a slug that is not alphanumeric.
pub fn derived_view_identifier(name: &str) -> Option<String> {
    let name = name.strip_prefix("v:").unwrap_or(name);
    let identifier = if let Some(slug) = name.strip_prefix("detail_") {
        slug
    } else {
        let slug = name.strip_prefix("section_")?;
        let n = slug.chars().count();
        let half: String = slug.chars().take(n / 2).collect();
        if n % 2 == 1 || slug.chars().skip(n / 2).ne(half.chars()) {
            return None;
        }
        &slug[..half.len()]
    };
    (!identifier.is_empty() && identifier.chars().all(char::is_alphanumeric))
        .then(|| identifier.to_uppercase())
}

/// `derived_view_identifier_reused`: one identifier naming more than one drawn view.
fn derived_view_identifiers(drawing: &Drawing) -> Vec<LintIssue> {
    let mut by_identifier: BTreeMap<String, Vec<&str>> = BTreeMap::new();
    for v in &drawing.sheet.views {
        if let Some(id) = derived_view_identifier(&v.name) {
            by_identifier.entry(id).or_default().push(&v.name);
        }
    }
    by_identifier
        .into_iter()
        .filter(|(_, names)| names.len() > 1)
        .map(|(id, names)| {
            let mut i = issue(
                format!("l:derived_view_identifier_reused:{id}"),
                "derived_view_identifier_reused",
                Severity::Error,
                format!(
                    "derived-view identifier '{id}' names multiple views: {}",
                    names
                        .iter()
                        .map(|n| format!("'{}'", bare(n)))
                        .collect::<Vec<_>>()
                        .join(", ")
                ),
            );
            i.related = names.iter().map(|n| n.to_string()).collect();
            i
        })
        .collect()
}

/// `scale_not_stated`: a finished sheet (it has a title block) states its scale. A drawing with
/// neither is a fragment, with nothing to state a scale about.
fn scale_stated(drawing: &Drawing) -> Option<LintIssue> {
    let has = |k: Kind| drawing.sheet.items.iter().any(|d| d.kind == k);
    if has(Kind::ScaleNote) || !has(Kind::TitleBlock) {
        return None;
    }
    let mut i = issue(
        "l:scale_not_stated:sheet".into(),
        "scale_not_stated",
        Severity::Error,
        "The sheet does not state its scale. ISO 7200 presents the scale outside the title \
         block; draftwright draws it beside the projection symbol. A drawing whose scale is not \
         stated cannot be measured off."
            .into(),
    );
    i.fix = Some("draw the SCALE note beside the title block".into());
    Some(i)
}

/// What a message calls *d*: by its label's text where the drawing records it (a dimension's),
/// anything else by its name.
fn called(drawing: &Drawing, d: &Drawn) -> String {
    if let Some(a) = drawing.annotations.iter().find(|a| a.name == d.name) {
        let text: Vec<&str> = a
            .runs
            .iter()
            .filter_map(|&i| drawing.text.get(i))
            .map(|r| r.text.as_str())
            .collect();
        if !text.is_empty() {
            return text.join(" ");
        }
    }
    d.name.clone()
}

/// `title_field_overflow` and `leader_line_through_text`, thing by thing in drawing order: each
/// title-block value's ink against its cell, and each leader against its own label. A datum
/// shaft attaches to its frame border without a callout elbow; only entering the interior
/// crosses its letter. Its frame strokes are not leader lines.
fn annotation_items(drawing: &Drawing) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    for d in &drawing.sheet.items {
        if d.kind == Kind::TitleBlock {
            for f in &drawing.sheet.fields {
                let (w, h) = (f.ink[2] - f.ink[0], f.ink[3] - f.ink[1]);
                let (cw, ch) = (f.cell[2] - f.cell[0], f.cell[3] - f.cell[1]);
                if w > cw + BOUNDS_ROUNDOFF || h > ch + BOUNDS_ROUNDOFF {
                    let mut i = issue(
                        format!("l:title_field_overflow:{}/{}", d.name, f.field),
                        "title_field_overflow",
                        Severity::Warning,
                        format!(
                            "Title-block field '{}' overflows its cell: text {w:.2} × {h:.2} mm, \
                             cell {cw:.2} × {ch:.2} mm. Use a concise field value and retain the \
                             full information in a notes block; text has not been shortened.",
                            f.field
                        ),
                    );
                    i.annotation = Some(d.name.clone());
                    i.fix = Some(
                        "give the field a concise value and put the full text in the general \
                         notes"
                            .into(),
                    );
                    issues.push(i);
                }
            }
        }
        let [x0, y0, x1, y1] = d.label.unwrap_or(d.ink);
        let datum = d.kind == Kind::Gdt && d.name.starts_with("pmi:datum.");
        let point = if datum {
            d.lines
                .iter()
                .filter_map(|[a, b]| clip(*a, *b, [x0, y0, x1, y1]))
                .map(|[a, b]| [(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0])
                .find(|[x, y]| {
                    x0 + BOUNDS_ROUNDOFF < *x
                        && *x < x1 - BOUNDS_ROUNDOFF
                        && y0 + BOUNDS_ROUNDOFF < *y
                        && *y < y1 - BOUNDS_ROUNDOFF
                })
        } else {
            d.elbow
                .filter(|[x, y]| x0 <= *x && *x <= x1 && y0 <= *y && *y <= y1)
        };
        if let Some([ex, ey]) = point {
            let point_kind = if datum { "shaft point" } else { "elbow point" };
            let mut i = issue(
                format!("l:leader_line_through_text:{}", d.name),
                "leader_line_through_text",
                Severity::Error,
                format!(
                    "Leader '{}': {point_kind} ({ex:.2}, {ey:.2}) is inside the label bbox — \
                         leader line passes through the text",
                    called(drawing, d)
                ),
            );
            i.at = Some([ex, ey]);
            i.annotation = Some(d.name.clone());
            i.fix = Some("turn the leader so it meets the label at its end".into());
            issues.push(i);
        }
    }
    issues
}

/// The part of the segment *p*–*q* inside *b*, by its two ends (draftwright's
/// `_segment_clip_extent`, Liang–Barsky, a touch counting); none when it misses.
fn clip(p: P, q: P, b: [f64; 4]) -> Option<[P; 2]> {
    let (dx, dy) = (q[0] - p[0], q[1] - p[1]);
    let (mut t0, mut t1) = (0.0f64, 1.0f64);
    for (pp, qq) in [
        (-dx, p[0] - b[0]),
        (dx, b[2] - p[0]),
        (-dy, p[1] - b[1]),
        (dy, b[3] - p[1]),
    ] {
        if pp.abs() < 1e-12 {
            if qq < 0.0 {
                return None;
            }
        } else {
            let r = qq / pp;
            if pp < 0.0 {
                if r > t1 {
                    return None;
                }
                t0 = t0.max(r);
            } else {
                if r < t0 {
                    return None;
                }
                t1 = t1.min(r);
            }
        }
    }
    (t0 <= t1).then(|| {
        [
            [p[0] + t0 * dx, p[1] + t0 * dy],
            [p[0] + t1 * dx, p[1] + t1 * dy],
        ]
    })
}

/// The longest connected stroke of *lines* inside the label box *b* (draftwright's
/// `crossing_length`): pieces meeting at an end inside the box add, separate pieces do not.
fn crossing(lines: &[[P; 2]], b: [f64; 4]) -> f64 {
    let lengths: Vec<f64> = lines
        .iter()
        .map(|[p, q]| clip(*p, *q, b).map_or(0.0, |[a, c]| (c[0] - a[0]).hypot(c[1] - a[1])))
        .collect();
    let active: Vec<usize> = (0..lines.len()).filter(|&k| lengths[k] > 0.0).collect();
    let mut parent: Vec<usize> = (0..lines.len()).collect();
    fn find(parent: &mut [usize], mut k: usize) -> usize {
        while parent[k] != k {
            parent[k] = parent[parent[k]];
            k = parent[k];
        }
        k
    }
    let inside = |p: P| {
        b[0] - 1e-9 <= p[0] && p[0] <= b[2] + 1e-9 && b[1] - 1e-9 <= p[1] && p[1] <= b[3] + 1e-9
    };
    for (n, &l) in active.iter().enumerate() {
        for &r in &active[n + 1..] {
            let joint = lines[l].iter().find_map(|a| {
                lines[r].iter().find_map(|c| {
                    ((a[0] - c[0]).hypot(a[1] - c[1]) <= 1e-9)
                        .then(|| [(a[0] + c[0]) / 2.0, (a[1] + c[1]) / 2.0])
                })
            });
            if joint.is_some_and(inside) {
                let (lr, rr) = (find(&mut parent, l), find(&mut parent, r));
                if lr != rr {
                    parent[rr] = lr;
                }
            }
        }
    }
    let mut totals: BTreeMap<usize, f64> = BTreeMap::new();
    for &k in &active {
        let root = find(&mut parent, k);
        *totals.entry(root).or_default() += lengths[k];
    }
    totals.into_values().fold(0.0, f64::max)
}

/// A label box line-work can cross: one with area (draftwright's `crossable_region`).
fn crossable(d: &Drawn) -> Option<[f64; 4]> {
    d.label.filter(|b| (b[2] - b[0]).min(b[3] - b[1]) > 0.0)
}

/// Each reportable crossing of the pair, worst first: (crosser, crossed, length).
fn crossings<'a>(a: &'a Drawn, b: &'a Drawn) -> Vec<(&'a Drawn, &'a Drawn, f64)> {
    let mut found: Vec<(&Drawn, &Drawn, f64)> = [(a, b), (b, a)]
        .into_iter()
        .filter_map(|(x, y)| {
            let b = crossable(y)?;
            let length = crossing(&x.lines, b);
            let head = crossing(&x.heads, b);
            Some((x, y, length.max(head)))
        })
        // Required NTS furniture must not hide even a shallow retained ink clash.
        .filter(|c| {
            c.2 >= if c.1.name == NTS_CAPTION_ID {
                BOUNDS_ROUNDOFF
            } else {
                MIN_CROSSING
            }
            // A flare is filled ink: report even a shallow contact, retaining its real size.
            || crossable(c.1).is_some_and(|b| crossing(&c.0.heads, b) > BOUNDS_ROUNDOFF)
        })
        .collect();
    found.sort_by(|p, q| q.2.total_cmp(&p.2));
    found
}

/// `label_centerline_overlap` for the actual label of *d* and the centreline *cl*
/// (draftwright's `_label_centerline_overlap`): the greatest overlap of the label box with any
/// of the centreline's lines, each measured across its stroke by how deep it runs in and along
/// it by its clipped length; with no lines, its whole ink. None unless both exceed 0.5 mm.
fn centreline_overlap(d: &Drawn, cl: &Drawn) -> Option<(f64, f64)> {
    let [lx0, ly0, lx1, ly1] = d.label?;
    let overlap = |[x0, y0, x1, y1]: [f64; 4], segment: bool| {
        let (w, h) = (x1 - x0, y1 - y0);
        let across = |lo: f64, hi: f64, c: f64| {
            if lo < c && c < hi {
                (c - lo).min(hi - c)
            } else {
                0.0
            }
        };
        let ox = if (segment && w < h) || (!segment && w < 0.1) {
            across(lx0, lx1, (x0 + x1) / 2.0)
        } else {
            (lx1.min(x1) - lx0.max(x0)).max(0.0)
        };
        let oy = if (segment && h < w) || (!segment && h < 0.1) {
            across(ly0, ly1, (y0 + y1) / 2.0)
        } else {
            (ly1.min(y1) - ly0.max(y0)).max(0.0)
        };
        let threshold = if d.name == NTS_CAPTION_ID {
            BOUNDS_ROUNDOFF
        } else {
            OVERLAP
        };
        (ox > threshold && oy > threshold).then_some((ox, oy))
    };
    if cl.lines.is_empty() {
        return overlap(cl.ink, false);
    }
    cl.lines
        .iter()
        .filter_map(|[p, q]| clip(*p, *q, [lx0, ly0, lx1, ly1]))
        .filter_map(|[a, c]| {
            overlap(
                [
                    a[0].min(c[0]),
                    a[1].min(c[1]),
                    a[0].max(c[0]),
                    a[1].max(c[1]),
                ],
                true,
            )
        })
        .max_by(|p, q| {
            (p.0 * p.1)
                .total_cmp(&(q.0 * q.1))
                .then(p.0.total_cmp(&q.0))
        })
}

/// The finite collinear stroke check shared by candidate repair and drawn lint.
pub(crate) fn leader_follows_stroke(lines: &[[P; 2]], strokes: &[[P; 2]]) -> bool {
    lines.iter().any(|[a, b]| {
        let u = [b[0] - a[0], b[1] - a[1]];
        let length = u[0].hypot(u[1]);
        length > MIN_CROSSING
            && strokes.iter().any(|[p, q]| {
                let distance = |p: P| ((p[0] - a[0]) * u[1] - (p[1] - a[1]) * u[0]).abs() / length;
                if distance(*p) > 1e-6 || distance(*q) > 1e-6 {
                    return false;
                }
                let t = |p: P| ((p[0] - a[0]) * u[0] + (p[1] - a[1]) * u[1]) / length;
                t(*p).max(t(*q)).min(length) - t(*p).min(t(*q)).max(0.) > MIN_CROSSING
            })
    })
}

/// Independently judge fixed furniture from the rendered inventory. A collinear leader
/// cannot borrow a cutting stroke; section letters and arrows are ink even at a shallow touch.
fn fixed_ink_contacts(drawing: &Drawing) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    for d in drawing
        .sheet
        .items
        .iter()
        .filter(|d| d.kind == Kind::Dimension)
    {
        for fixed in &drawing.sheet.items {
            let code = if fixed.kind == Kind::Centreline && d.elbow.is_some() {
                let along = leader_follows_stroke(&d.lines, &fixed.lines);
                if !along {
                    continue;
                }
                "leader_centerline_overlap"
            } else if fixed.kind == Kind::CuttingPlane
                || (fixed.kind == Kind::Caption
                    && fixed.name.starts_with("section_")
                    && (fixed.name.ends_with("_left") || fixed.name.ends_with("_right")))
            {
                let Some(b) = d.label else {
                    continue;
                };
                let f = fixed.label.unwrap_or(fixed.ink);
                if b[2].min(f[2]) - b[0].max(f[0]) <= 1e-6
                    || b[3].min(f[3]) - b[1].max(f[1]) <= 1e-6
                {
                    continue;
                }
                "label_section_furniture_overlap"
            } else {
                continue;
            };
            let mut i = issue(
                format!("l:{code}:{}+{}", d.name, fixed.name),
                code,
                Severity::Warning,
                format!(
                    "'{}' touches fixed ink '{}'",
                    called(drawing, d),
                    fixed.name
                ),
            );
            i.annotation = Some(d.name.clone());
            i.related = vec![fixed.name.clone()];
            i.fix = Some("move the annotation clear of the fixed ink".into());
            issues.push(i);
        }
    }
    issues
}

/// #32, the port's own (draftwright has neither): `dimension_line_shared`, two linear dimensions
/// drawn on one dimension line over overlapping spans, and `dimension_text_stray`, a linear
/// dimension's text further than a text height from its own line. Both errors: the sheet reads
/// wrongly whatever else a move cleared. A linear dimension is read from its ink alone (no elbow;
/// two extension lines, then the line across them).
fn dimension_lines(drawing: &Drawing) -> Vec<LintIssue> {
    let linear: Vec<&Drawn> = drawing
        .sheet
        .items
        .iter()
        .filter(|d| d.kind == Kind::Dimension && d.elbow.is_none())
        .collect();
    let mut issues = Vec::new();
    for (k, a) in linear.iter().enumerate() {
        for b in &linear[k + 1..] {
            if crate::output::shared_dimension_line(&a.lines, &b.lines) {
                let mut i = issue(
                    format!("l:dimension_line_shared:{}+{}", a.name, b.name),
                    "dimension_line_shared",
                    Severity::Error,
                    format!(
                        "'{}' and '{}' are drawn on one dimension line over overlapping spans",
                        called(drawing, a),
                        called(drawing, b)
                    ),
                );
                i.annotation = Some(a.name.clone());
                i.related = vec![b.name.clone()];
                i.fix = Some("move one of them onto a separate dimension line".into());
                issues.push(i);
            }
        }
        if let Some(gap) = a
            .label
            .and_then(|l| crate::output::label_gap(&a.lines, l))
            .filter(|g| *g > crate::output::STRAY_TEXT)
        {
            let mut i = issue(
                format!("l:dimension_text_stray:{}", a.name),
                "dimension_text_stray",
                Severity::Error,
                format!(
                    "the text of '{}' lies {gap:.1} mm from its dimension line",
                    called(drawing, a)
                ),
            );
            i.annotation = Some(a.name.clone());
            i.fix = Some("set the text beside its own dimension line".into());
            issues.push(i);
        }
    }
    issues
}

/// Oblique witnesses must not cut the visible part or another dimension's ink.
/// Read the line grammar and visible curves of the finished sheet, independently
/// of the placement search and its conservative material evidence.
fn oblique_witnesses(drawing: &Drawing) -> Vec<LintIssue> {
    let side = |a: P, b: P, c: P| (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    let intersects = |a: [P; 2], b: [P; 2]| {
        side(a[0], a[1], b[0]) * side(a[0], a[1], b[1]) < -1e-10
            && side(b[0], b[1], a[0]) * side(b[0], b[1], a[1]) < -1e-10
    };
    let mut issues = Vec::new();
    for d in drawing
        .sheet
        .items
        .iter()
        .filter(|d| d.kind == Kind::Dimension && d.elbow.is_none())
    {
        let Some(linear) = crate::output::linear_ink(&d.lines) else {
            continue;
        };
        if !d.lines[..2]
            .iter()
            .any(|l| (l[1][linear.k] - l[0][linear.k]).abs() > 1e-6)
        {
            continue;
        }
        let view = drawing
            .annotations
            .iter()
            .find(|a| a.name == d.name)
            .and_then(|a| a.view.as_deref());
        let material = drawing
            .sheet
            .views
            .iter()
            .filter(|v| Some(v.name.as_str()) == view)
            .any(|v| {
                edges(v).iter().any(|edge| match edge {
                    Edge::Line(a, b, _) => d.lines[..2].iter().any(|l| intersects(*l, [*a, *b])),
                    Edge::Points(points, _) => points
                        .windows(2)
                        .any(|p| d.lines[..2].iter().any(|l| intersects(*l, [p[0], p[1]]))),
                })
            });
        let peers: Vec<_> = drawing
            .sheet
            .items
            .iter()
            .filter(|q| q.name != d.name && q.kind == Kind::Dimension)
            .filter(|q| {
                d.lines[..2].iter().any(|l| {
                    q.label.is_some_and(|b| clip(l[0], l[1], b).is_some())
                        || q.lines.iter().chain(&q.heads).any(|t| intersects(*l, *t))
                })
            })
            .collect();
        if material || !peers.is_empty() {
            let mut i = issue(
                format!("l:oblique_witness_crossing:{}", d.name),
                "oblique_witness_crossing",
                Severity::Warning,
                format!(
                    "'{}' has an oblique witness crossing {}",
                    called(drawing, d),
                    if material {
                        "the visible part outline"
                    } else {
                        "another dimension"
                    }
                ),
            );
            i.annotation = Some(d.name.clone());
            i.view = view.map(str::to_owned);
            i.related = peers.iter().map(|q| q.name.clone()).collect();
            i.evidence = Some("the drawn oblique witness intersects visible boundary ink, a dimension line, witness, arrowhead or label".into());
            i.fix = Some("draw perpendicular extension lines in clear space where possible".into());
            issues.push(i);
        }
    }
    issues
}

/// The pairwise checks, each pair once in drawing order (draftwright's `_lint_annotation_pairs`):
/// a centreline against a label by `label_centerline_overlap`; two other things by their label
/// boxes (a label-less thing by its whole ink), `annotation_overlap` beyond roundoff for text
/// pairs or past 0.5 mm for conservative furniture bounds (its
/// message naming any line-work also crossing), and otherwise `annotation_ink_overlap` for each
/// label the other's line-work runs through.
fn annotation_pairs(drawing: &Drawing) -> Vec<LintIssue> {
    let items = &drawing.sheet.items;
    let mut issues = Vec::new();
    for (k, a) in items.iter().enumerate() {
        for b in &items[k + 1..] {
            let (cl_a, cl_b) = (a.kind == Kind::Centreline, b.kind == Kind::Centreline);
            if cl_a && cl_b {
                continue;
            }
            if cl_a || cl_b {
                let (d, cl) = if cl_a { (b, a) } else { (a, b) };
                if let Some((ox, oy)) = centreline_overlap(d, cl) {
                    let mut i = issue(
                        format!("l:label_centerline_overlap:{}+{}", d.name, cl.name),
                        "label_centerline_overlap",
                        Severity::Warning,
                        format!(
                            "label '{}' overlaps centerline by {ox:.1}×{oy:.1} mm",
                            called(drawing, d)
                        ),
                    );
                    let [x0, y0, x1, y1] = cl.ink;
                    i.at = Some([(x0 + x1) / 2.0, (y0 + y1) / 2.0]);
                    i.annotation = Some(d.name.clone());
                    i.related = vec![cl.name.clone()];
                    i.fix = Some("move the label clear of the centreline".into());
                    issues.push(i);
                }
                continue;
            }
            let (la, lb) = (a.label.unwrap_or(a.ink), b.label.unwrap_or(b.ink));
            let ox = (la[2].min(lb[2]) - la[0].max(lb[0])).max(0.0);
            let oy = (la[3].min(lb[3]) - la[1].max(lb[1])).max(0.0);
            let crossed = crossings(a, b);
            // Actual text has no acceptable overlap. Keep the half-millimetre
            // allowance only for label-less furniture's conservative ink bounds.
            let threshold = if (a.label.is_some() && b.label.is_some())
                || a.name == NTS_CAPTION_ID
                || b.name == NTS_CAPTION_ID
            {
                BOUNDS_ROUNDOFF
            } else {
                OVERLAP
            };
            if ox > threshold && oy > threshold {
                let mut message = format!(
                    "labels '{}' and '{}' overlap by {ox:.1}×{oy:.1} mm",
                    called(drawing, a),
                    called(drawing, b)
                );
                let mut fix = "move one of the labels clear of the other";
                if let Some((crosser, over, length)) = crossed.first() {
                    message += &format!(
                        " — '{}' also draws {length:.1} mm of line-work through the label '{}', \
                         so separating the text is not enough",
                        called(drawing, crosser),
                        called(drawing, over)
                    );
                    fix = "move what is drawn, not just the text";
                }
                let mut i = issue(
                    format!("l:annotation_overlap:{}+{}", a.name, b.name),
                    "annotation_overlap",
                    Severity::Warning,
                    message,
                );
                i.annotation = Some(a.name.clone());
                i.related = vec![b.name.clone()];
                i.fix = Some(fix.into());
                issues.push(i);
                continue;
            }
            for (crosser, over, length) in crossed {
                let mut i = issue(
                    format!("l:annotation_ink_overlap:{}+{}", over.name, crosser.name),
                    "annotation_ink_overlap",
                    Severity::Warning,
                    format!(
                        "'{}' draws {length:.1} mm of line-work through the label '{}' — the \
                         labels clear each other, so this is a dimension line, an extension line \
                         or a leader over text; move what is drawn, not just the text",
                        called(drawing, crosser),
                        called(drawing, over)
                    ),
                );
                let [x0, y0, x1, y1] = over.label.unwrap_or(over.ink);
                i.at = Some([(x0 + x1) / 2.0, (y0 + y1) / 2.0]);
                i.annotation = Some(over.name.clone());
                i.related = vec![crosser.name.clone()];
                i.fix = Some("move what is drawn, not just the text".into());
                issues.push(i);
            }
        }
    }
    issues
}

/// Whether two boxes overlap, a touch not counting (draftwright's `_boxes_overlap`).
fn boxes_overlap(a: [f64; 4], b: [f64; 4]) -> bool {
    a[0] < b[2] && a[2] > b[0] && a[1] < b[3] && a[3] > b[1]
}

/// A view edge as draftwright's `_edges_intersect_rect` tests it: a line by its ends, any other
/// curve by points about 1 mm apart (8 to 200 steps), with the box of either.
enum Edge {
    Line(P, P, [f64; 4]),
    Points(Vec<P>, [f64; 4]),
}

fn edges(view: &DrawnView) -> Vec<Edge> {
    let bound = |pts: &[P]| {
        pts.iter().fold(
            [f64::INFINITY, f64::INFINITY, -f64::INFINITY, -f64::INFINITY],
            |b, p| {
                [
                    b[0].min(p[0]),
                    b[1].min(p[1]),
                    b[2].max(p[0]),
                    b[3].max(p[1]),
                ]
            },
        )
    };
    view.edges
        .iter()
        .map(|c| match c {
            Curve::Line(p, q) => Edge::Line(*p, *q, bound(&[*p, *q])),
            _ => {
                let fine: Vec<P> = (0..=64).map(|i| c.at(i as f64 / 64.0)).collect();
                let length: f64 = fine
                    .windows(2)
                    .map(|w| (w[1][0] - w[0][0]).hypot(w[1][1] - w[0][1]))
                    .sum();
                let n = (length as usize + 1).clamp(8, 200);
                let pts: Vec<P> = (0..=n).map(|i| c.at(i as f64 / n as f64)).collect();
                let b = bound(&pts);
                Edge::Points(pts, b)
            }
        })
        .collect()
}

/// Whether any of *edges* passes through the box *r*.
fn edges_cross(edges: &[Edge], r: [f64; 4]) -> bool {
    edges.iter().any(|e| match e {
        Edge::Line(p, q, b) => boxes_overlap(*b, r) && clip(*p, *q, r).is_some(),
        Edge::Points(pts, b) => {
            boxes_overlap(*b, r)
                && pts
                    .iter()
                    .any(|p| r[0] <= p[0] && p[0] <= r[2] && r[1] <= p[1] && p[1] <= r[3])
        }
    })
}

/// `view_annotation_overlap` and `view_annotation_inside_extents`, view by view: each thing's
/// label box (a label-less thing's whole ink; never a centreline's or hatching's) that overlaps
/// the view's box, a warning when a visible edge passes through it and otherwise an info notice,
/// unless it was placed inside on purpose.
fn view_annotations(drawing: &Drawing) -> Vec<LintIssue> {
    let mut issues = Vec::new();
    for v in &drawing.sheet.views {
        let mut sampled: Option<Vec<Edge>> = None;
        let [vx0, vy0, vx1, vy1] = v.bbox;
        for d in &drawing.sheet.items {
            if matches!(d.kind, Kind::Centreline | Kind::Hatch) {
                continue;
            }
            let ab = d.label.unwrap_or(d.ink);
            if !boxes_overlap(v.bbox, ab) {
                continue;
            }
            let what = if d.label.is_some() {
                "label of annotation"
            } else {
                "annotation"
            };
            let edges = sampled.get_or_insert_with(|| edges(v));
            let (name, called) = (bare(&v.name), called(drawing, d));
            if edges_cross(edges, ab) {
                let mut i = issue(
                    format!("l:view_annotation_overlap:{}:{}", v.name, d.name),
                    "view_annotation_overlap",
                    Severity::Warning,
                    format!(
                        "view '{name}' line-work overlaps {what} '{called}' — increase view \
                         spacing or move the annotation"
                    ),
                );
                i.view = Some(v.name.clone());
                i.annotation = Some(d.name.clone());
                issues.push(i);
            } else if !d.interior {
                let mut i = issue(
                    format!("l:view_annotation_inside_extents:{}:{}", v.name, d.name),
                    "view_annotation_inside_extents",
                    Severity::Info,
                    format!(
                        "{what} '{called}' lies inside view '{name}' extents [x={vx0:.1}–{vx1:.1}, \
                         y={vy0:.1}–{vy1:.1}] over a blank region — legitimate for callouts on \
                         large faces"
                    ),
                );
                i.view = Some(v.name.clone());
                i.annotation = Some(d.name.clone());
                issues.push(i);
            }
        }
    }
    issues
}

/// The sides on which *b* runs past *bounds*, and by how much, as draftwright words them.
fn overshoots(b: [f64; 4], bounds: [f64; 4]) -> Vec<(&'static str, String)> {
    let mut out = Vec::new();
    if b[0] < bounds[0] - BOUNDS_ROUNDOFF {
        out.push(("left", format!("left by {:.1} mm", bounds[0] - b[0])));
    }
    if b[2] > bounds[2] + BOUNDS_ROUNDOFF {
        out.push(("right", format!("right by {:.1} mm", b[2] - bounds[2])));
    }
    if b[1] < bounds[1] - BOUNDS_ROUNDOFF {
        out.push(("below", format!("below by {:.1} mm", bounds[1] - b[1])));
    }
    if b[3] > bounds[3] + BOUNDS_ROUNDOFF {
        out.push(("above", format!("above by {:.1} mm", b[3] - bounds[3])));
    }
    out
}

/// `annotation_out_of_bounds`: every thing's whole ink inside the drawable area; the title block
/// may reach half its border's stroke past it.
fn annotation_bounds(drawing: &Drawing) -> Vec<LintIssue> {
    let Some(area) = drawing.sheet.drawable else {
        return Vec::new();
    };
    let mut issues = Vec::new();
    for d in &drawing.sheet.items {
        let bounds = if d.kind == Kind::TitleBlock {
            let h = TITLE_HALF_STROKE;
            [area[0] - h, area[1] - h, area[2] + h, area[3] + h]
        } else {
            area
        };
        for (side, detail) in overshoots(d.ink, bounds) {
            let mut i = issue(
                format!("l:annotation_out_of_bounds:{}:{side}", d.name),
                "annotation_out_of_bounds",
                Severity::Error,
                format!(
                    "annotation '{}' extends past drawable area ({detail})",
                    called(drawing, d)
                ),
            );
            i.annotation = Some(d.name.clone());
            i.fix = Some(
                "move it inside the border, or choose a larger sheet or a smaller scale".into(),
            );
            issues.push(i);
        }
    }
    issues
}

/// A view's name as draftwright writes it: `front`, not `v:front`.
fn bare(name: &str) -> &str {
    name.strip_prefix("v:").unwrap_or(name)
}

/// `view_overlap` (two views' boxes overlapping, a touch not counted) and `view_out_of_bounds`
/// (a view's box past the drawable area, once per side).
fn view_boxes(drawing: &Drawing) -> Vec<LintIssue> {
    let views = &drawing.sheet.views;
    let mut issues = Vec::new();
    for (k, a) in views.iter().enumerate() {
        let [ax0, ay0, ax1, ay1] = a.bbox;
        for b in &views[k + 1..] {
            let [bx0, by0, bx1, by1] = b.bbox;
            if ax0 < bx1 && ax1 > bx0 && ay0 < by1 && ay1 > by0 {
                let mut i = issue(
                    format!("l:view_overlap:{}+{}", a.name, b.name),
                    "view_overlap",
                    Severity::Warning,
                    format!(
                        "view '{}' bbox [x={ax0:.1}–{ax1:.1}, y={ay0:.1}–{ay1:.1}] overlaps view \
                         '{}' [x={bx0:.1}–{bx1:.1}, y={by0:.1}–{by1:.1}] — increase spacing \
                         between views",
                        bare(&a.name),
                        bare(&b.name)
                    ),
                );
                i.view = Some(a.name.clone());
                i.related = vec![b.name.clone()];
                issues.push(i);
            }
        }
    }
    if let Some(area) = drawing.sheet.drawable {
        for v in views {
            for (side, detail) in overshoots(v.bbox, area) {
                let mut i = issue(
                    format!("l:view_out_of_bounds:{}:{side}", v.name),
                    "view_out_of_bounds",
                    Severity::Error,
                    format!(
                        "view '{}' extends past drawable area ({detail}) — reduce the view scale \
                         or move the view",
                        bare(&v.name)
                    ),
                );
                i.view = Some(v.name.clone());
                issues.push(i);
            }
        }
    }
    issues
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn derived_view_identifiers_are_read_as_draftwright_reads_them() {
        assert_eq!(
            derived_view_identifier("v:section_aa").as_deref(),
            Some("A")
        );
        assert_eq!(
            derived_view_identifier("section_b1b1").as_deref(),
            Some("B1")
        );
        assert_eq!(derived_view_identifier("detail_c").as_deref(), Some("C"));
        assert_eq!(derived_view_identifier("section_ab"), None);
        assert_eq!(derived_view_identifier("section_a"), None);
        assert_eq!(derived_view_identifier("detail_"), None);
        assert_eq!(derived_view_identifier("detail_a-b"), None);
        assert_eq!(derived_view_identifier("front"), None);
    }
}
